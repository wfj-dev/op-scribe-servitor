"""Auto-roster embed subsystem.

Maintains persistent embed messages in each Watch Company's roster channel.
Embeds are posted once via /roster_post (Forgemaster only) and then edited
in-place by a daily task and by /roster_refresh (Watch Command+).

Embed layout per company channel:
  1. HIGH COMMAND          — members with HC role, excluding Watch Captains
  2. COMPANY COMMAND       — captain, lieutenant, specialists, honored dreads
  3–6. KILL TEAM <name>    — one per KT role linked to that company (up to 4)

Members in Reserves are excluded from all embeds.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord
from discord.ext import tasks

from .constants import (  # noqa: F401
    HIGH_COMMAND_ROLE_ID,
    INTERRED_BROTHER_ROLE_ID,
    RESERVES_ROLE_ID,
    ROSTER_COMPANY_CHANNELS,
    ROSTER_COMPANY_COMMAND_RANKS,
    ROSTER_EMBED_DESC_LIMIT,
    ROSTER_STATE_PATH,
    _normalize_display_name,
    _strip_display_name,
)
from . import _bot_globals as _g

# ---------------------------------------------------------------------------
# Module-level logger (falls back to root if _g.logger not yet set)
# ---------------------------------------------------------------------------
def _log() -> logging.Logger:
    return _g.logger or logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers: bot module resolution (same pattern as roster_ops._b)
# ---------------------------------------------------------------------------
def _b(name):
    m = sys.modules.get("opscribe.bot") or sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


# ---------------------------------------------------------------------------
# Discord custom-emoji pattern (used in name cleaning below)
# ---------------------------------------------------------------------------
_CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_]+:\d+>")


def _clean_roster_name(member: discord.Member) -> str:
    """Return a clean display name suitable for the roster embed.

    Processing pipeline (order matters):
    1. Strip Discord custom-emoji notation ``<:name:id>`` / ``<a:name:id>``
    2. Normalize decorative unicode (small-caps, math-bold, etc.) via
       ``_normalize_display_name`` — keeps text readable without losing intent
    3. Strip stud-pip glyphs  ●⚬▬
    4. Collapse runs of whitespace to a single space and trim edges
    5. Cap length at 40 chars to keep roster lines tidy

    Intentionally does NOT attempt to strip Oathsworn titles or rank prefixes
    because those form part of the member's chosen identity.
    """
    raw = (
        getattr(member, "nick", None)
        or getattr(member, "display_name", None)
        or getattr(member, "name", None)
        or str(getattr(member, "id", "?"))
    )
    # 1. Custom Discord emoji notations
    out = _CUSTOM_EMOJI_RE.sub("", raw)
    # 2. Unicode normalisation (small-caps → ASCII, math-bold → plain, etc.)
    out = _normalize_display_name(out)
    # 3. Stud pips and leftover NFKD combining marks already handled by
    #    _normalize_display_name; just strip the pip glyphs explicitly
    out = out.replace("●", "").replace("⚬", "").replace("▬", "")
    # 4. Collapse whitespace
    out = re.sub(r"\s+", " ", out).strip()
    # 5. Length cap
    if len(out) > 40:
        out = out[:37] + "…"
    return out or str(getattr(member, "id", "?"))


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_roster_state() -> dict:
    """Load roster embed state from disk.

    State shape::

        {
            "Watch Company Primus": {
                "channel_id": 1433351509722267658,
                "hc_message_id": null,
                "command_message_id": null,
                "killteam_message_ids": {"Kill Team Alpha": 123456}
            },
            ...
        }
    """
    try:
        if os.path.exists(ROSTER_STATE_PATH):
            with open(ROSTER_STATE_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        _log().warning(f"Roster: failed to load state from {ROSTER_STATE_PATH}: {exc}")
    # Return default structure when no file exists yet
    return {
        company: {
            "channel_id": channel_id,
            "hc_message_id": None,
            "command_message_id": None,
            "killteam_message_ids": {},
        }
        for company, channel_id in ROSTER_COMPANY_CHANNELS.items()
    }


def _save_roster_state(state: dict) -> None:
    tmp = ROSTER_STATE_PATH + ".tmp"
    bak = ROSTER_STATE_PATH + ".bak"
    try:
        os.makedirs(os.path.dirname(ROSTER_STATE_PATH) or ".", exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(ROSTER_STATE_PATH):
            try:
                os.replace(ROSTER_STATE_PATH, bak)
            except Exception:
                pass
        os.replace(tmp, ROSTER_STATE_PATH)
    except Exception as exc:
        _log().error(f"Roster: failed to save state: {exc}")


# ---------------------------------------------------------------------------
# Member classification helpers
# ---------------------------------------------------------------------------

def _is_in_reserves(member: discord.Member) -> bool:
    """Return True if this member is in Reserves (any role containing 'reserve')."""
    for r in getattr(member, "roles", []) or []:
        name_lower = (getattr(r, "name", "") or "").lower()
        rid = getattr(r, "id", 0)
        if rid == RESERVES_ROLE_ID or "reserve" in name_lower:
            return True
    return False


def _member_role_names(member: discord.Member) -> set[str]:
    return {(getattr(r, "name", "") or "").strip() for r in (getattr(member, "roles", []) or [])}


def _member_role_ids(member: discord.Member) -> set[int]:
    return {getattr(r, "id", 0) for r in (getattr(member, "roles", []) or [])}


def _get_highest_rank(member: discord.Member) -> Optional[str]:
    """Return the member's highest rank according to RANK_ROLES_PRIORITY, or None."""
    priority: List[str] = _b("RANK_ROLES_PRIORITY") or []
    role_names = _member_role_names(member)
    for rank in priority:
        if rank in role_names:
            return rank
    return None


def _sort_key_for_member(member: discord.Member) -> Tuple[int, str]:
    """Sort key: (rank_priority_index, display_name). Lower index = higher rank."""
    priority: List[str] = _b("RANK_ROLES_PRIORITY") or []
    role_names = _member_role_names(member)
    best = len(priority)  # fallback: lowest priority
    for i, rank in enumerate(priority):
        if rank in role_names:
            best = i
            break
    return (best, _clean_roster_name(member).lower())


def _get_hc_members(guild: discord.Guild) -> List[discord.Member]:
    """Return HC-role members excluding Watch Captains and Reserves, sorted."""
    result = []
    for m in guild.members:
        if m.bot:
            continue
        if _is_in_reserves(m):
            continue
        role_ids = _member_role_ids(m)
        if HIGH_COMMAND_ROLE_ID not in role_ids:
            continue
        # Watch Captains belong in Company Command, not HC
        if "Watch Captain" in _member_role_names(m):
            continue
        result.append(m)
    return sorted(result, key=_sort_key_for_member)


def _get_company_command_members(
    guild: discord.Guild, company_name: str
) -> List[discord.Member]:
    """Return Company Command members for a given company, sorted.

    Includes anyone who has the company role AND at least one
    ROSTER_COMPANY_COMMAND_RANKS rank (Watch Captain, Watch Lieutenant,
    Company Champion, Specialists, Honored Dreadnought).
    Excludes Reserves.
    """
    result = []
    for m in guild.members:
        if m.bot:
            continue
        if _is_in_reserves(m):
            continue
        role_names = _member_role_names(m)
        if company_name not in role_names:
            continue
        if not (role_names & ROSTER_COMPANY_COMMAND_RANKS):
            continue
        result.append(m)
    return sorted(result, key=_sort_key_for_member)


def _get_kill_teams_for_company(
    guild: discord.Guild, company_name: str
) -> List[Tuple[str, List[discord.Member]]]:
    """Return list of (kt_role_name, sorted_members) for Kill Teams in a company.

    A Kill Team is a guild role whose name contains "Kill Team" (case-insensitive)
    and that has at least one non-Reserve member who also holds the company role.
    Returns up to 4 Kill Teams, sorted by role name.
    """
    kt_roles: List[discord.Role] = []
    for role in guild.roles:
        if "kill team" in (role.name or "").lower():
            kt_roles.append(role)
    kt_roles.sort(key=lambda r: r.name)

    results = []
    for role in kt_roles:
        members = []
        for m in role.members:
            if m.bot:
                continue
            if _is_in_reserves(m):
                continue
            role_names = _member_role_names(m)
            if company_name not in role_names:
                continue
            members.append(m)
        if members:
            results.append((role.name, sorted(members, key=_sort_key_for_member)))
        if len(results) >= 4:
            break
    return results


# ---------------------------------------------------------------------------
# Embed rendering
# ---------------------------------------------------------------------------

_EMBED_COLOR = discord.Color.from_str("#2B2B2B")  # dark grey / Deathwatch aesthetic


def _render_member_line(guild: discord.Guild, member: discord.Member) -> str:
    """Render a single roster line: ``:rankemoji: Name :chapteremoji:``.

    Falls back gracefully if emojis or chapter cannot be resolved.
    """
    rank = _get_highest_rank(member)
    rank_emoji_str = ""
    if rank:
        rank_emoji_str = _b("_get_rank_emoji")(guild, rank) or ""

    name = _clean_roster_name(member)

    # Home chapter emoji
    home_chapters: List[str] = _b("HOME_CHAPTERS") or []
    role_names = _member_role_names(member)
    chapter_emoji_str = ""
    for chapter in home_chapters:
        if chapter in role_names:
            chapter_emoji_str = _b("_get_emoji_by_name")(guild, chapter) or ""
            break

    parts = []
    if rank_emoji_str:
        parts.append(rank_emoji_str)
    parts.append(name)
    if chapter_emoji_str:
        parts.append(chapter_emoji_str)
    return " ".join(parts)


def _build_embed(
    title: str,
    members: List[discord.Member],
    guild: discord.Guild,
    last_updated: Optional[datetime] = None,
) -> discord.Embed:
    """Build a roster discord.Embed for a list of members.

    Gracefully truncates the description if the member list would exceed
    ROSTER_EMBED_DESC_LIMIT characters. Reports the truncation count in a
    footer note so admins are aware.
    """
    embed = discord.Embed(title=title, color=_EMBED_COLOR)

    if not members:
        embed.description = "*No members currently assigned.*"
    else:
        lines: List[str] = []
        truncated_count = 0
        running_len = 0

        for m in members:
            try:
                line = _render_member_line(guild, m)
            except Exception as exc:
                _log().warning(
                    f"Roster: failed to render line for {getattr(m, 'id', '?')}: {exc}"
                )
                line = f"*[render error: {getattr(m, 'id', '?')}]*"

            # +1 for the newline joining them
            if running_len + len(line) + 1 > ROSTER_EMBED_DESC_LIMIT:
                truncated_count = len(members) - len(lines)
                break
            lines.append(line)
            running_len += len(line) + 1

        description = "\n".join(lines)

        if truncated_count:
            note = f"\n*…and {truncated_count} more not shown (embed limit reached)*"
            description += note
            _log().warning(
                f"Roster: '{title}' truncated — {truncated_count} member(s) omitted to stay within embed limit"
            )

        embed.description = description

    ts = last_updated or datetime.now(timezone.utc)
    embed.set_footer(
        text=f"Last updated · {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    return embed


# ---------------------------------------------------------------------------
# Per-message upsert helper
# ---------------------------------------------------------------------------

async def _upsert_message(
    channel: discord.TextChannel,
    message_id: Optional[int],
    embed: discord.Embed,
) -> int:
    """Edit an existing message or post a new one.

    Returns the message ID (existing or newly created).
    Raises on failure so callers can decide how to handle.
    """
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
            return msg.id
        except discord.NotFound:
            _log().info(
                f"Roster: message {message_id} not found in {channel.id} — will repost"
            )
        except discord.Forbidden:
            raise RuntimeError(
                f"Missing permissions to edit message {message_id} in channel {channel.id}"
            )
        except Exception as exc:
            _log().warning(
                f"Roster: edit failed for message {message_id} ({exc}) — will repost"
            )

    # Post fresh
    msg = await channel.send(embed=embed)
    return msg.id


# ---------------------------------------------------------------------------
# Core update logic
# ---------------------------------------------------------------------------

async def _update_company_roster(
    guild: discord.Guild,
    company_name: str,
    state: dict,
    *,
    now: Optional[datetime] = None,
) -> None:
    """Refresh all roster embeds for one company.

    Mutates *state* in place with updated message IDs.
    Raises on unrecoverable errors; callers should catch and log.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    company_state = state.get(company_name, {})
    channel_id = company_state.get("channel_id") or ROSTER_COMPANY_CHANNELS.get(company_name)
    if not channel_id:
        raise ValueError(f"No channel ID configured for '{company_name}'")

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await _g.bot.fetch_channel(int(channel_id))
        except Exception as exc:
            raise RuntimeError(
                f"Cannot access roster channel {channel_id} for '{company_name}': {exc}"
            ) from exc

    short_name = company_name.replace("Watch Company", "").strip()  # "Primus" etc.

    # ── Embed 1: High Command ────────────────────────────────────────────────
    hc_members = _get_hc_members(guild)
    hc_embed = _build_embed(
        "⸸ HIGH COMMAND",
        hc_members,
        guild,
        last_updated=now,
    )
    hc_msg_id = await _upsert_message(
        channel, company_state.get("hc_message_id"), hc_embed
    )
    company_state["hc_message_id"] = hc_msg_id

    # ── Embed 2: Company Command ─────────────────────────────────────────────
    cmd_members = _get_company_command_members(guild, company_name)
    cmd_embed = _build_embed(
        f"⸸ WATCH COMPANY {short_name.upper()} — COMMAND",
        cmd_members,
        guild,
        last_updated=now,
    )
    cmd_msg_id = await _upsert_message(
        channel, company_state.get("command_message_id"), cmd_embed
    )
    company_state["command_message_id"] = cmd_msg_id

    # ── Embeds 3–6: Kill Teams ───────────────────────────────────────────────
    kill_teams = _get_kill_teams_for_company(guild, company_name)
    kt_message_ids: dict = dict(company_state.get("killteam_message_ids") or {})

    # Track which KT names are still active so we can clean up stale IDs
    active_kt_names = {kt_name for kt_name, _ in kill_teams}
    # Remove stale entries (KT disbanded / no longer has members in this company)
    for stale_kt in list(kt_message_ids.keys()):
        if stale_kt not in active_kt_names:
            _log().info(
                f"Roster: KT '{stale_kt}' no longer active for {company_name} — removing tracked message ID"
            )
            del kt_message_ids[stale_kt]

    for kt_name, kt_members in kill_teams:
        kt_embed = _build_embed(
            f"⸸ {kt_name.upper()}",
            kt_members,
            guild,
            last_updated=now,
        )
        kt_msg_id = await _upsert_message(
            channel, kt_message_ids.get(kt_name), kt_embed
        )
        kt_message_ids[kt_name] = kt_msg_id

    company_state["killteam_message_ids"] = kt_message_ids
    state[company_name] = company_state


async def _update_all_rosters(guild: discord.Guild) -> dict[str, str]:
    """Refresh roster embeds for every configured company.

    Returns a dict of ``{company_name: "ok" | error_message}``.
    State is persisted after all companies have been attempted.
    """
    async with (_g.ROSTER_STATE_LOCK or asyncio.Lock()):
        state = _load_roster_state()
        results: dict[str, str] = {}
        now = datetime.now(timezone.utc)

        for company_name in ROSTER_COMPANY_CHANNELS:
            try:
                await _update_company_roster(guild, company_name, state, now=now)
                results[company_name] = "ok"
                _log().info(f"Roster: updated '{company_name}' successfully")
            except Exception as exc:
                results[company_name] = str(exc)
                _log().error(
                    f"Roster: failed to update '{company_name}': {exc}", exc_info=True
                )

        _save_roster_state(state)
        return results


# ---------------------------------------------------------------------------
# Scheduled daily task
# ---------------------------------------------------------------------------

@tasks.loop(hours=24)
async def _roster_update_loop() -> None:
    """Refresh all roster embeds once per day."""
    try:
        guild = _b("_resolve_notification_guild")()
        if not guild:
            _log().debug("Roster daily update: no guild available, skipping")
            return
        results = await _update_all_rosters(guild)
        errors = {k: v for k, v in results.items() if v != "ok"}
        if errors:
            _log().error(f"Roster daily update: errors in {list(errors.keys())}: {errors}")
        else:
            _log().info("Roster daily update: all companies refreshed")
    except Exception:
        _log().exception("Roster daily update loop encountered an unexpected error")


@_roster_update_loop.before_loop
async def _roster_update_before_loop() -> None:
    """Wait for the bot to be ready before the first run."""
    bot = _g.bot
    if bot:
        await bot.wait_until_ready()
    # Delay first run by 2 hours so startup flurry settles
    await asyncio.sleep(7200)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def _register_commands() -> None:
    """Register /roster_post and /roster_refresh with the bot's command tree.

    Called once from bot.py after the bot object is available via _g.bot.
    """
    bot = _g.bot

    @bot.tree.command(
        name="roster_post",
        description="Post (or re-anchor) all roster embeds in company channels. Forgemaster only.",
    )
    async def roster_post(interaction: discord.Interaction) -> None:
        if not _b("check_command_permission")(interaction.user, "roster_post"):
            await interaction.response.send_message(
                "Access denied. This command is restricted to Forgemaster.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Must be used in a server.", ephemeral=True)
            return

        results = await _update_all_rosters(guild)

        lines = ["**Roster embed status:**"]
        for company, result in results.items():
            short = company.replace("Watch Company", "").strip()
            icon = "✅" if result == "ok" else "❌"
            msg = "posted / updated" if result == "ok" else result
            lines.append(f"{icon} **{short}**: {msg}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @bot.tree.command(
        name="roster_refresh",
        description="Manually refresh all roster embeds now. Watch Command+.",
    )
    async def roster_refresh(interaction: discord.Interaction) -> None:
        if not _b("check_command_permission")(interaction.user, "roster_refresh"):
            await interaction.response.send_message(
                "Access denied. Requires Watch Command or higher.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Must be used in a server.", ephemeral=True)
            return

        results = await _update_all_rosters(guild)

        lines = ["**Roster refresh complete:**"]
        any_error = False
        for company, result in results.items():
            short = company.replace("Watch Company", "").strip()
            icon = "✅" if result == "ok" else "❌"
            msg = "updated" if result == "ok" else result
            if result != "ok":
                any_error = True
            lines.append(f"{icon} **{short}**: {msg}")

        if any_error:
            lines.append("\n*Check bot logs for full error details.*")

        await interaction.followup.send("\n".join(lines), ephemeral=True)
