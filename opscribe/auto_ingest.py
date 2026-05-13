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

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import tasks

from . import _bot_globals as _g
from .constants import AAR_CHANNEL_ID, FORGEMASTER_ROLE_NAME, DATA_DIR
from .pressure_registry import (
    CadrePressure,
    PressureSnapshot,
    READY_THRESHOLD,
    HARD_BLOCK_THRESHOLD,
    evaluate_all,
)


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
    return max(60, int(_cfg().get("check_interval_minutes", 90)) * 60)


def _cooldown_hours() -> float:
    return float(_cfg().get("cooldown_hours", 12))


def _span_days() -> int:
    return int(_cfg().get("ingest_span_days", 45))


def _forced_max_backlog() -> int:
    return int(_cfg().get("forced_max_backlog", 30))


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


async def _count_backlog(aar_channel: discord.TextChannel) -> int:
    """Cheap count of messages newer than the latest processed AAR id."""
    try:
        from .aar_ops import load_processed_ids
        processed = load_processed_ids()
        if not processed:
            # Backlog is unbounded if we've never processed anything; treat as
            # "definitely should ingest" by reporting the forced-max threshold.
            return _forced_max_backlog()
        latest_id = max(int(x) for x in processed if str(x).isdigit())
        count = 0
        # `after=` accepts a discord.Object with id.
        async for _msg in aar_channel.history(
            limit=200, after=discord.Object(id=latest_id), oldest_first=True
        ):
            count += 1
        return count
    except Exception:
        _g.logger.exception("auto_ingest: backlog count failed")
        return 0


def _score_str(s: float) -> str:
    return "∞" if s == float("inf") else f"{s:.2f}"


def _format_cadre_line(c: CadrePressure) -> str:
    role_mention = f"<@&{c.notify_role_id}>" if c.notify_role_id else c.display_name
    return (
        f"• {role_mention} — score **{_score_str(c.score)}** "
        f"({c.demand} demand / {c.supply} supply)"
    )


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
    aar_channel: discord.TextChannel, mode: str, snapshot: PressureSnapshot, backlog: int
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
        f"backlog≈{backlog} mean={_score_str(snapshot.mean_score)} "
        f"max={_score_str(snapshot.max_score)}"
    )
    _g.logger.info(f"auto_ingest: {summary}")

    # Public report
    flavor = "ready" if mode == "ready" else "forced (specialists overwhelmed)"
    try:
        await aar_channel.send(
            "```ansi\n"
            "\u001b[32m==============================================================================\n"
            "  OPERATION-SCRIBE SERVITOR — AUTOMATED INGESTION RITE\n"
            "==============================================================================\n"
            f"  Trigger: {flavor}\n"
            f"  Scan Window: Last {span} day(s)\n"
            f"  Chronicled: {ingested}\n"
            f"  Rejected: {rejected}\n"
            f"  Pressure: mean {_score_str(snapshot.mean_score)} / "
            f"max {_score_str(snapshot.max_score)}\n"
            "==============================================================================\n"
            "\u001b[0m```"
        )
    except Exception:
        _g.logger.exception("auto_ingest: failed to post ingest report")
    return summary


async def _post_tier1_blocker_notice(
    snapshot: PressureSnapshot, state: AutoIngestState, guild: discord.Guild
) -> None:
    """Post a tier-1 notice in each blocking cadre's own channel.

    Each blocker is routed to its own ``notify_channel_id`` (e.g. Techmarines
    post to the arming-chamber, Librarians post to the librarium watch). When
    a blocker has no channel configured we fall back to the global
    ``notification_channel_id`` (or AAR_CHANNEL_ID). Multiple cadres sharing
    one channel are coalesced into a single message there.
    """
    blockers = snapshot.blockers() or snapshot.cadres
    fallback_channel_id = _notification_channel_id()

    # Group blockers by destination channel id so each channel receives a
    # single message naming only the cadres that belong there.
    groups: Dict[int, List[CadrePressure]] = {}
    for c in blockers:
        cid = c.notify_channel_id or fallback_channel_id
        groups.setdefault(cid, []).append(c)

    posted_any = False
    for channel_id, group in groups.items():
        channel = guild.get_channel(channel_id) or _g.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await _g.bot.fetch_channel(channel_id)
            except Exception:
                _g.logger.warning("auto_ingest: tier-1 channel %s not accessible", channel_id)
                continue

        body = "\n".join(_format_cadre_line(c) for c in group)
        pings = " ".join(
            f"<@&{c.notify_role_id}>" for c in group if c.notify_role_id
        )
        msg = (
            f"⚠️ **The Servitor stalls.** The pressure of un-chronicled records mounts, "
            f"yet the specialists are not ready to bear it.\n"
            f"{pings}\n"
            f"{body}\n"
            f"_Auto-ingest will resume when mean pressure falls below "
            f"{READY_THRESHOLD:.1f} and no cadre exceeds {HARD_BLOCK_THRESHOLD:.1f}._"
        )
        try:
            await channel.send(msg)
            posted_any = True
        except Exception:
            _g.logger.exception("auto_ingest: failed to post tier-1 notice to %s", channel_id)

    if posted_any:
        state.last_blocker_notice_at = _now_iso()
        state.last_blocker_set = sorted(c.cadre_id for c in blockers)


async def _dm_forgemaster_tier2(
    snapshot: PressureSnapshot, state: AutoIngestState, backlog: int
) -> None:
    user_id = _forgemaster_user_id()
    if not user_id:
        _g.logger.warning("auto_ingest: forgemaster_user_id not configured; skipping DM")
        return
    try:
        user = _g.bot.get_user(user_id) or await _g.bot.fetch_user(user_id)
    except Exception:
        _g.logger.exception("auto_ingest: failed to fetch Forgemaster user")
        return

    hours_blocked = _hours_since(state.blocked_since) or 0.0
    lines = [_format_cadre_line(c) for c in snapshot.cadres]
    body = (
        f"**Auto-ingest has been blocked for {hours_blocked:.1f}h.**\n"
        f"Backlog ≈ {backlog} unchronicled record(s).\n"
        f"Mean cadre pressure: **{_score_str(snapshot.mean_score)}** "
        f"(needs < {READY_THRESHOLD:.1f})\n"
        f"Max cadre pressure: **{_score_str(snapshot.max_score)}** "
        f"(needs < {HARD_BLOCK_THRESHOLD:.1f})\n\n"
        + "\n".join(lines)
        + "\n\nRecommendation: nudge the specialists in the listed cadres to "
        "perform their rites, or use `/auto_ingest_set state:off` to pause "
        "auto-ingest entirely."
    )
    try:
        await user.send(body)
        state.forgemaster_dm_at = _now_iso()
        _g.logger.info("auto_ingest: DM'd Forgemaster (tier 2)")
    except Exception:
        _g.logger.exception("auto_ingest: failed to DM Forgemaster")


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

async def _tick() -> None:
    """One iteration of the auto-ingest decision loop."""
    if not _enabled_in_config():
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

    snapshot = await evaluate_all(guild)
    backlog = await _count_backlog(aar_channel)

    state.last_check_at = _now_iso()
    state.last_check_mean = (
        None if snapshot.mean_score == float("inf") else snapshot.mean_score
    )
    state.last_check_max = (
        None if snapshot.max_score == float("inf") else snapshot.max_score
    )
    state.last_check_backlog = backlog

    if _in_cooldown(state):
        state.last_check_outcome = "COOLDOWN"
        state.save()
        return

    forced = _is_forced(backlog, state.last_ingest_at)

    if forced:
        state.last_check_outcome = "FORCED"
        summary = await _run_ingest(aar_channel, "forced", snapshot, backlog)
        state.last_ingest_at = _now_iso()
        state.last_ingest_mode = "forced"
        state.last_ingest_summary = summary
        state.blocked_since = None
        state.last_blocker_notice_at = None
        state.last_blocker_set = []
        state.forgemaster_dm_at = None
        state.save()
        return

    if snapshot.is_ready:
        state.last_check_outcome = "READY"
        # Skip ingest if there's literally nothing to do.
        if backlog == 0:
            state.save()
            return
        summary = await _run_ingest(aar_channel, "ready", snapshot, backlog)
        state.last_ingest_at = _now_iso()
        state.last_ingest_mode = "ready"
        state.last_ingest_summary = summary
        state.blocked_since = None
        state.last_blocker_notice_at = None
        state.last_blocker_set = []
        state.forgemaster_dm_at = None
        state.save()
        return

    # BLOCKED
    state.last_check_outcome = "BLOCKED"
    if state.blocked_since is None:
        state.blocked_since = _now_iso()

    blockers = snapshot.blockers()
    blocker_set = sorted(c.cadre_id for c in blockers)
    # Post Tier 1 if it's the first block OR the blocker set has changed.
    if state.last_blocker_notice_at is None or blocker_set != state.last_blocker_set:
        await _post_tier1_blocker_notice(snapshot, state, guild)

    # Tier 2: DM Forgemaster after escalation_hours of continuous block.
    hours_blocked = _hours_since(state.blocked_since) or 0.0
    esc = _escalation_hours()
    should_dm = (
        hours_blocked >= esc
        and (
            state.forgemaster_dm_at is None
            or (_hours_since(state.forgemaster_dm_at) or 0.0) >= esc
        )
    )
    if should_dm:
        await _dm_forgemaster_tier2(snapshot, state, backlog)

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
    snapshot: Optional[PressureSnapshot] = None
    backlog: Optional[int] = None
    if guild is not None:
        try:
            snapshot = await evaluate_all(guild)
            ch = guild.get_channel(AAR_CHANNEL_ID)
            if ch is not None:
                backlog = await _count_backlog(ch)
        except Exception:
            _g.logger.exception("auto_ingest_status: live evaluation failed")

    lines: List[str] = []
    lines.append("```")
    lines.append("AUTO-AAR-INGEST STATUS")
    lines.append("=" * 56)
    lines.append(
        f"runtime_enabled : {state.runtime_enabled} "
        f"(config: {_enabled_in_config()})"
    )
    lines.append(f"check_interval  : {_check_interval_seconds() // 60} min")
    lines.append(f"cooldown        : {_cooldown_hours():.1f}h")
    lines.append(f"ingest_span     : {_span_days()} days")
    lines.append(
        f"forced if       : backlog ≥ {_forced_max_backlog()} OR "
        f"stale ≥ {_forced_max_stale_days()}d"
    )
    lines.append("-" * 56)
    lines.append(f"last_check_at   : {state.last_check_at or '—'}")
    lines.append(f"last_outcome    : {state.last_check_outcome or '—'}")
    lines.append(f"last_ingest_at  : {state.last_ingest_at or '—'}")
    lines.append(f"last_mode       : {state.last_ingest_mode or '—'}")
    lines.append(f"blocked_since   : {state.blocked_since or '—'}")
    lines.append(f"fm_dm_at        : {state.forgemaster_dm_at or '—'}")
    lines.append("-" * 56)
    if snapshot is not None:
        lines.append(
            f"LIVE  mean={_score_str(snapshot.mean_score)}  "
            f"max={_score_str(snapshot.max_score)}  "
            f"backlog={backlog if backlog is not None else '?'}"
        )
        for c in snapshot.cadres:
            lines.append(
                f"  {c.display_name:<14} score={_score_str(c.score)}  "
                f"demand={c.demand:<4} supply={c.supply}"
            )
    else:
        lines.append("LIVE: unavailable (no guild context)")
    lines.append("```")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


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
