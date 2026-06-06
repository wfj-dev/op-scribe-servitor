"""Auto-AAR-ingest: pressure-driven ingestion loop.

Design summary
--------------
Every ``check_interval_minutes`` the loop:

1. Computes aggregate cadre pressure (see ``pressure_registry``).
2. Counts the AAR backlog (messages newer than the latest processed id).
3. Decides one of four outcomes:
   * **READY**  — mean cadre score < READY_THRESHOLD and no hard blocker.
                  Runs ``_run_ingest_new``, posts a public report.
   * **FORCED** — backlog or staleness exceed their thresholds, ingest
                  anyway with a "forced" flavor.
   * **COOLDOWN** — last ingest was too recent; do nothing this tick.
   * **BLOCKED** — pressure too high. Post a Tier 1 channel notice
                   identifying blocker cadres; after ``escalation_hours``
                   of continued blocking, DM the Forgemaster (Tier 2).

State is persisted in ``data/auto_ingest_state.json`` so escalation
timestamps and cooldown survive restarts.

All Forgemaster-only commands (status, set, force) live in this module.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import tasks

from . import _bot_globals as _g
from .constants import AAR_CHANNEL_ID, DATA_DIR


# ---------------------------------------------------------------------------
# Config accessors
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    try:
        return (_g.CONFIG or {}).get("auto_ingest", {}) or {}
    except Exception:
        return {}


def _enabled_in_config() -> bool:
    return bool(_cfg().get("enabled", True))


def _check_interval_seconds() -> int:
    return max(60, int(_cfg().get("check_interval_minutes", 360)) * 60)


def _cooldown_hours() -> float:
    return float(_cfg().get("cooldown_hours", 12))


def _span_days() -> int:
    return int(_cfg().get("ingest_span_days", 45))


def _forced_max_backlog() -> int:
    return int(_cfg().get("forced_max_backlog", 50))


def _forced_max_stale_days() -> int:
    return int(_cfg().get("forced_max_stale_days", 10))


def _forgemaster_user_id() -> Optional[int]:
    raw = _cfg().get("forgemaster_user_id")
    try:
        return int(raw) if raw else None
    except Exception:
        return None


def _escalation_hours() -> float:
    return float(_cfg().get("escalation_hours", 48))


def _notification_channel_id() -> int:
    raw = _cfg().get("notification_channel_id")
    try:
        if raw:
            return int(raw)
    except Exception:
        pass
    return AAR_CHANNEL_ID


# ---------------------------------------------------------------------------
# State persistence — data/auto_ingest_state.json
# ---------------------------------------------------------------------------

STATE_PATH = os.path.join(DATA_DIR, "auto_ingest_state.json")


@dataclass
class AutoIngestState:
    runtime_enabled: bool = True          # Forgemaster /auto_ingest_set toggle
    last_ingest_at: Optional[str] = None  # ISO UTC of last successful ingest
    last_ingest_mode: Optional[str] = None  # "ready" | "forced" | "manual"
    last_ingest_summary: Optional[str] = None
    last_check_at: Optional[str] = None
    last_check_mean: Optional[float] = None
    last_check_max: Optional[float] = None
    last_check_backlog: Optional[int] = None
    last_check_outcome: Optional[str] = None  # READY/FORCED/COOLDOWN/BLOCKED
    blocked_since: Optional[str] = None        # ISO UTC of first BLOCKED tick
    last_blocker_notice_at: Optional[str] = None
    last_blocker_set: List[str] = field(default_factory=list)  # cadre_ids
    forgemaster_dm_at: Optional[str] = None

    @classmethod
    def load(cls) -> "AutoIngestState":
        try:
            if not os.path.exists(STATE_PATH):
                return cls()
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.__dict__, f, indent=2)
        except Exception:
            _g.logger.exception("Failed to save auto_ingest_state.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Tolerate both naive and tz-aware strings.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _hours_since(s: Optional[str]) -> Optional[float]:
    dt = _parse_iso(s)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _fmt_ts(s: Optional[str]) -> str:
    """Render an ISO timestamp as Discord dynamic markdown (short + relative).

    Falls back to an em-dash when unparseable, or the raw string when parsing
    fails on something that's still vaguely time-shaped.
    """
    if not s:
        return "—"
    dt = _parse_iso(s)
    if dt is None:
        return s
    unix = int(dt.timestamp())
    return f"<t:{unix}:f> (<t:{unix}:R>)"


async def _count_backlog(aar_channel: discord.TextChannel) -> int:
    """Cheap count of AAR-shaped messages newer than the latest processed AAR id.

    Only messages that pass ``aar_ops.is_aar_message`` (i.e. contain the
    ``++ MISSION REPORT ++`` marker) are counted, so chatter, bot reports,
    edits, and other noise in the AAR channel don't inflate the backlog.
    """
    try:
        from .aar_ops import load_processed_ids, is_aar_message
        processed = load_processed_ids()
        if not processed:
            # Backlog is unbounded if we've never processed anything; treat as
            # "definitely should ingest" by reporting the forced-max threshold.
            return _forced_max_backlog()
        latest_id = max(int(x) for x in processed if str(x).isdigit())
        count = 0
        # `after=` accepts a discord.Object with id.
        async for msg in aar_channel.history(
            limit=200, after=discord.Object(id=latest_id), oldest_first=True
        ):
            if is_aar_message(msg):
                count += 1
        return count
    except Exception:
        _g.logger.exception("auto_ingest: backlog count failed")
        return 0



# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------

def _is_forced(backlog: int, last_ingest_iso: Optional[str]) -> bool:
    """Backlog/staleness override that ignores cadre scores."""
    max_b = _forced_max_backlog()
    max_d = _forced_max_stale_days()
    if backlog >= max_b:
        return True
    hours = _hours_since(last_ingest_iso)
    if hours is None:
        # Never ingested → treat as maximally stale.
        return True
    return (hours / 24.0) >= max_d


def _in_cooldown(state: AutoIngestState) -> bool:
    hours = _hours_since(state.last_ingest_at)
    if hours is None:
        return False
    return hours < _cooldown_hours()


# ---------------------------------------------------------------------------
# Outcome handlers
# ---------------------------------------------------------------------------

async def _run_ingest(
    aar_channel: discord.TextChannel, mode: str, backlog: int
) -> str:
    """Invoke the existing ingest pipeline and return a one-line summary."""
    from .aar_ops import _run_ingest_new

    span = _span_days()
    if _g.RECONCILE_LOCK.locked():
        _g.logger.info("auto_ingest: reconcile lock held; deferring this tick")
        return "deferred (lock held)"
    async with _g.RECONCILE_LOCK:
        ingested, rejected = await _run_ingest_new(aar_channel, span)
    summary = (
        f"mode={mode} ingested={ingested} rejected={rejected} "
        f"backlog≈{backlog}"
    )
    _g.logger.info(f"auto_ingest: {summary}")

    # Private report — DM to Forgemaster.
    flavor = "Ready" if mode == "ready" else "Forced (backlog/staleness override)"
    color = 0x2ECC71 if mode == "ready" else 0xE67E22
    embed = discord.Embed(
        title="ᛙ⋅ AUTOMATED INGESTION RITE ⋅ᛙ",
        color=color,
        description=f"_Trigger: **{flavor}**_",
    )
    embed.add_field(name="▸ Chronicled", value=str(ingested), inline=True)
    embed.add_field(name="▸ Rejected", value=str(rejected), inline=True)
    embed.add_field(name="▸ Scan Window", value=f"Last {span} day(s)", inline=True)
    embed.add_field(name="▸ Backlog", value=f"backlog ≈ **{backlog}**", inline=False)
    embed.set_footer(text="Operation-Scribe Servitor · automated rite")
    user_id = _forgemaster_user_id()
    if user_id:
        try:
            user = _g.bot.get_user(user_id) or await _g.bot.fetch_user(user_id)
            await user.send(embed=embed)
        except discord.Forbidden:
            _g.logger.warning(
                "auto_ingest: Forgemaster has DMs disabled; falling back to AAR channel"
            )
            try:
                await aar_channel.send(embed=embed)
            except Exception:
                _g.logger.exception("auto_ingest: fallback channel post failed")
        except Exception:
            _g.logger.exception("auto_ingest: failed to DM Forgemaster ingest report")
    else:
        _g.logger.debug(
            "auto_ingest: forgemaster_user_id not configured; skipping ingest report"
        )
    return summary



# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

async def _tick() -> None:
    """One iteration of the auto-ingest decision loop."""
    if not _enabled_in_config():
        return

    # Skip in debug mode — avoid unintended production writes during local testing.
    if getattr(_g, 'DEBUG_MODE', False):
        return

    state = AutoIngestState.load()
    if not state.runtime_enabled:
        return

    # Resolve guild + channel.
    from .bot import _resolve_notification_guild  # late import (cycle)
    guild = _resolve_notification_guild()
    if guild is None:
        return
    aar_channel = guild.get_channel(AAR_CHANNEL_ID)
    if aar_channel is None:
        _g.logger.debug("auto_ingest: AAR channel not found")
        return

    backlog = await _count_backlog(aar_channel)

    state.last_check_at = _now_iso()
    state.last_check_mean = None
    state.last_check_max = None
    state.last_check_backlog = backlog

    if _in_cooldown(state):
        state.last_check_outcome = "COOLDOWN"
        state.save()
        return

    forced = _is_forced(backlog, state.last_ingest_at)

    if forced:
        state.last_check_outcome = "FORCED"
        summary = await _run_ingest(aar_channel, "forced", backlog)
        state.last_ingest_at = _now_iso()
        state.last_ingest_mode = "forced"
        state.last_ingest_summary = summary
        state.blocked_since = None
        state.last_blocker_notice_at = None
        state.last_blocker_set = []
        state.forgemaster_dm_at = None
        state.save()
        return

    # READY — no cadre pressure system; ingest whenever not in cooldown and backlog exists.
    state.last_check_outcome = "READY"
    if backlog == 0:
        state.save()
        return
    summary = await _run_ingest(aar_channel, "ready", backlog)
    state.last_ingest_at = _now_iso()
    state.last_ingest_mode = "ready"
    state.last_ingest_summary = summary
    state.blocked_since = None
    state.last_blocker_notice_at = None
    state.last_blocker_set = []
    state.forgemaster_dm_at = None
    state.save()


# ---------------------------------------------------------------------------
# Task loop
# ---------------------------------------------------------------------------

@tasks.loop(seconds=60)  # interval-checked inside; real cadence in _tick gate
async def _auto_ingest_loop():
    try:
        # Run on the configured cadence using a coarse gate (the loop fires
        # every minute but only acts when last_check_at is older than the
        # configured interval). This keeps the cadence reconfigurable at
        # runtime without restarting the loop.
        state = AutoIngestState.load()
        last = _parse_iso(state.last_check_at)
        if last is not None:
            delta = (datetime.now(timezone.utc) - last).total_seconds()
            if delta < _check_interval_seconds():
                return
        await _tick()
    except Exception:
        _g.logger.exception("auto_ingest: loop iteration failed")


# ---------------------------------------------------------------------------
# Forgemaster-only commands (perm enforced via permissions.py against
# config.permissions). All three commands are intended as debug/control
# surfaces, not routine ops.
# ---------------------------------------------------------------------------

def _is_forgemaster_check(user: discord.abc.User, command_name: str) -> bool:
    fn = None
    try:
        import sys as _sys
        m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
        fn = getattr(m, "check_command_permission", None) if m else None
    except Exception:
        fn = None
    if fn is None:
        return False
    return fn(user, command_name)


@_g.bot.tree.command(
    name="auto_ingest_status",
    description="Show auto-AAR-ingest state and current pressure (Forgemaster).",
)
async def auto_ingest_status(interaction: discord.Interaction):
    if not _is_forgemaster_check(interaction.user, "auto_ingest_status"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    state = AutoIngestState.load()
    guild = interaction.guild
    backlog: Optional[int] = None
    if guild is not None:
        try:
            ch = guild.get_channel(AAR_CHANNEL_ID)
            if ch is not None:
                backlog = await _count_backlog(ch)
        except Exception:
            _g.logger.exception("auto_ingest_status: backlog count failed")

    # Fetch forge subsystem enabled state.
    try:
        from .forge_ops import _is_forge_enabled
        forge_enabled: Optional[bool] = await _is_forge_enabled()
    except Exception:
        forge_enabled = None

    # Pick an embed color from current state: blocked=red, cooldown=blue,
    # ready/forced=green, otherwise neutral.
    outcome = (state.last_check_outcome or "").upper()
    if outcome == "BLOCKED":
        color = 0xE74C3C
    elif outcome == "COOLDOWN":
        color = 0x3498DB
    elif outcome in ("READY", "FORCED"):
        color = 0x2ECC71
    else:
        color = 0x9B59B6

    # ─── Verdict line + cooldown / next-check ETAs ─────────────────────────
    cd_h = _cooldown_hours()
    iv_min = _check_interval_seconds() // 60
    last_ingest_age = _hours_since(state.last_ingest_at)
    cooldown_remaining = None
    if last_ingest_age is not None and last_ingest_age < cd_h:
        cooldown_remaining = cd_h - last_ingest_age
    last_check_age = _hours_since(state.last_check_at)
    next_check_in_min = None
    if last_check_age is not None:
        elapsed_min = last_check_age * 60.0
        next_check_in_min = max(0.0, iv_min - elapsed_min)

    if cooldown_remaining is not None:
        verdict = f"⏳ **COOLDOWN** — {cooldown_remaining:.1f}h until next eligible ingest"
    elif (backlog or 0) > 0:
        verdict = "✅ **READY** — will ingest on next tick"
    elif backlog == 0:
        verdict = "✅ **READY** — no backlog to chronicle"
    else:
        verdict = f"_{outcome or '—'}_"

    embed = discord.Embed(
        title="᛭⋅ AUTO-AAR-INGEST STATUS ⋅᛭",
        description=verdict,
        color=color,
    )

    # ─── Config field ─────────────────────────────────────────────────────
    def _on_off(v: Optional[bool]) -> str:
        if v is None:
            return "?"
        return "on" if v else "off"

    enabled_str = (
        f"**Runtime:** {'on' if state.runtime_enabled else 'off'}\n"
        f"**Config:** {'on' if _enabled_in_config() else 'off'}\n"
        f"**Interval:** {iv_min} min \u00b7 **Cooldown:** {cd_h:.1f}h \u00b7 "
        f"**Span:** {_span_days()}d\n"
        f"**Forced if:** backlog \u2265 {_forced_max_backlog()} OR "
        f"stale \u2265 {_forced_max_stale_days()}d\n"
        f"**Forge system:** {_on_off(forge_enabled)}"
    )
    embed.add_field(name="\u25b8 Configuration", value=enabled_str, inline=False)

    # \u2500\u2500\u2500 Backlog field \u2500\u2500\u2500
    embed.add_field(
        name="\u25b8 Backlog",
        value=f"**{backlog if backlog is not None else '?'}** unprocessed AARs",
        inline=False,
    )

    # ─── History field ────────────────────────────────────────────────────
    next_check_str = (
        f"in ~{next_check_in_min:.0f} min" if next_check_in_min is not None else "pending"
    )
    history = (
        f"**Last check:** {_fmt_ts(state.last_check_at)} ({outcome or '—'})\n"
        f"**Next check:** {next_check_str}\n"
        f"**Last ingest:** {_fmt_ts(state.last_ingest_at)}"
        f" ({state.last_ingest_mode or '—'})\n"
        f"**Blocked since:** {_fmt_ts(state.blocked_since)}\n"
        f"**Last FM DM:** {_fmt_ts(state.forgemaster_dm_at)}"
    )
    embed.add_field(name="▸ History", value=history, inline=False)

    embed.set_footer(text="Operation-Scribe Servitor · /auto_ingest_force to override")
    await interaction.followup.send(embed=embed, ephemeral=True)


@_g.bot.tree.command(
    name="auto_ingest_set",
    description="Enable or disable auto-AAR-ingest (Forgemaster).",
)
@app_commands.describe(enabled="True to enable, False to disable")
async def auto_ingest_set(interaction: discord.Interaction, enabled: bool):
    if not _is_forgemaster_check(interaction.user, "auto_ingest_set"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    state = AutoIngestState.load()
    state.runtime_enabled = bool(enabled)
    state.save()
    word = "ENABLED" if enabled else "DISABLED"
    await interaction.response.send_message(
        f"Auto-AAR-ingest **{word}**.", ephemeral=True
    )


@_g.bot.tree.command(
    name="auto_ingest_force",
    description="Force an auto-ingest tick now, bypassing cooldown (Forgemaster).",
)
async def auto_ingest_force(interaction: discord.Interaction):
    if not _is_forgemaster_check(interaction.user, "auto_ingest_force"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    # Temporarily clear cooldown by zeroing last_ingest_at, then tick.
    state = AutoIngestState.load()
    state.last_ingest_at = None
    state.save()
    try:
        await _tick()
        await interaction.followup.send(
            "Auto-ingest tick executed. See `/auto_ingest_status` for results.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"Tick failed: {e}", ephemeral=True)


__all__ = [
    "AutoIngestState",
    "_auto_ingest_loop",
    "_tick",
    "auto_ingest_status",
    "auto_ingest_set",
    "auto_ingest_force",
]
