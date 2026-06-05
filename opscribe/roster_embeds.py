"""Auto-roster embed subsystem.

Maintains persistent embed messages in each configured Watch Company's roster channel.
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

from .constants import (
    HIGH_COMMAND_ROLE_ID,
    RESERVES_ROLE_ID,
    ROSTER_COMPANY_CHANNELS,
    ROSTER_COMPANY_COMMAND_RANKS,
    ROSTER_EMBED_DESC_LIMIT,
    ROSTER_IMAGE_COMPANY_COMMAND,
    ROSTER_IMAGE_HIGH_COMMAND,
    ROSTER_IMAGE_KILLTEAM,
    ROSTER_IMAGE_KILLTEAM_BY_COMPANY,
    ROSTER_STATE_PATH,
    _normalize_display_name,
)
from .forge_ops import _get_emoji_by_name
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

# ---------------------------------------------------------------------------
# Campaign accolades helpers
# ---------------------------------------------------------------------------

# Maps campaign company_id → Watch Company name prefix (matches ROSTER_COMPANY_CHANNELS keys)
def _co_id_to_roster_name(company_id: str) -> str:
    """Convert 'primus' → 'Watch Company Primus'."""
    return f"Watch Company {company_id.capitalize()}"


def _co_roster_name_to_id(roster_name: str) -> str:
    """Convert 'Watch Company Primus' → 'primus'."""
    return roster_name.replace("Watch Company", "").strip().lower()


_RIBBON_LABELS = {
    "kt_ribbon_active": "Active Ribbon",
    "kt_ribbon_vanguard": "Vanguard Ribbon",
    "co_ribbon_active": "Active Ribbon",
    "co_ribbon_vanguard": "Vanguard Ribbon",
}
_HONOUR_LABELS = {
    "kt_honour_stalwart": "Stalwart",
    "co_honour_stalwart": "Stalwart",
}


def _load_campaign_state_for_roster() -> Optional[dict]:
    """Load campaign state without crashing the roster update if absent."""
    try:
        from .campaign_ops import _load_campaign_state
        state = _load_campaign_state()
        phase = state.get("campaign", {}).get("phase", "inactive")
        return state if phase != "inactive" else None
    except Exception as exc:
        _log().debug(f"Roster: could not load campaign state: {exc}")
        return None


def _resolve_kt_role_name(sgt_id: str, kt_member_ids: list[str], guild: Optional[discord.Guild]) -> Optional[str]:
    """Look up the Kill Team X Discord role name from any enlisted member of this KT."""
    if not guild:
        return None
    for uid in kt_member_ids:
        try:
            member = guild.get_member(int(uid))
        except (ValueError, TypeError):
            continue
        if not member:
            continue
        for r in member.roles:
            rl = r.name.lower()
            if "kill" in rl and "team" in rl and "champion" not in rl:
                return r.name
    return None


def _build_campaign_accolades_embed(
    company_name: str,
    campaign_state: dict,
    now: Optional[datetime] = None,
    guild: Optional[discord.Guild] = None,
) -> Optional[discord.Embed]:
    """Build the Campaign Accolades embed for a company roster channel.

    Returns None if the campaign is inactive or has no accolades data yet.
    Shows company-level honours/ribbons/prestige, then each KT in the company.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    company_id = _co_roster_name_to_id(company_name)
    campaign = campaign_state.get("campaign", {})
    phase = campaign.get("phase", "inactive")

    co_data = campaign_state.get("companies", {}).get(company_id)
    if not co_data:
        return None

    camp_name = campaign.get("name") or campaign.get("id") or "Active Campaign"
    beat = campaign.get("beat")
    beat_name = campaign.get("beat_name")
    beat_label = f"{beat_name}" if beat_name else (f"Cycle {beat}" if beat else "")

    # Build active enlistment lookup: sgt_id → [uid, ...]
    enlistment = campaign_state.get("enlistment", {})
    kt_members_by_sgt: dict[str, list[str]] = {}
    for uid, rec in enlistment.items():
        if not rec.get("active"):
            continue
        sgt_id = rec.get("kt_sgt_id")
        if sgt_id:
            kt_members_by_sgt.setdefault(sgt_id, []).append(uid)

    embed = discord.Embed(
        title="Campaign Accolades",
        description=(
            f"**{camp_name}**"
            + (f"  ·  {beat_label}" if beat_label else "")
            + f"\nPhase: {phase}"
        ),
        color=0xC4A030,
    )

    # ── Company section ──────────────────────────────────────────────────────
    co_prestige = co_data.get("prestige_window_total", 0)
    co_ribbon = co_data.get("ribbon")
    co_honour = co_data.get("honour")
    co_title = co_data.get("title")
    co_lore = co_data.get("lore_priority", False)

    co_lines: list[str] = [f"Prestige: **{co_prestige}**"]
    if co_title:
        co_lines.append(f"Title: *{co_title}*")
    if co_ribbon and co_ribbon in _RIBBON_LABELS:
        co_lines.append(f"Ribbon: {_RIBBON_LABELS[co_ribbon]}")
    if co_honour and co_honour in _HONOUR_LABELS:
        co_lines.append(f"Honour: {_HONOUR_LABELS[co_honour]}")
    if co_lore:
        co_lines.append("Lore Priority: Yes")

    embed.add_field(
        name=f"▸ {co_data.get('display_name', company_name)}",
        value="\n".join(co_lines),
        inline=False,
    )

    # ── Kill Teams in this company ───────────────────────────────────────────
    kill_teams = campaign_state.get("kill_teams", {})
    co_kts = [
        (sgt_id, kt) for sgt_id, kt in kill_teams.items()
        if kt.get("company_id") == company_id
    ]
    co_kts.sort(key=lambda x: x[1].get("prestige_window_total", 0), reverse=True)

    for sgt_id, kt in co_kts[:8]:
        kt_prestige = kt.get("prestige_window_total", 0)
        kt_ribbon = kt.get("ribbon")
        kt_honour_list = kt.get("honour") or []
        if isinstance(kt_honour_list, str):
            kt_honour_list = [kt_honour_list]
        kt_title = kt.get("title")
        kt_lore = kt.get("lore_priority", False)

        # Resolve name from Discord role; fallback to stored display_name
        member_ids = kt_members_by_sgt.get(sgt_id, [sgt_id])
        kt_name = (
            _resolve_kt_role_name(sgt_id, member_ids, guild)
            or kt.get("display_name")
            or f"KT {sgt_id}"
        )

        kt_lines: list[str] = [f"Prestige: **{kt_prestige}**"]
        if kt_title:
            kt_lines.append(f"Title: *{kt_title}*")
        if kt_ribbon and kt_ribbon in _RIBBON_LABELS:
            kt_lines.append(f"Ribbon: {_RIBBON_LABELS[kt_ribbon]}")
        for hon in kt_honour_list:
            if hon in _HONOUR_LABELS:
                kt_lines.append(f"Honour: {_HONOUR_LABELS[hon]}")
        if kt_lore:
            kt_lines.append("Lore Priority: Yes")

        embed.add_field(name=f"▸ {kt_name}", value="\n".join(kt_lines), inline=True)

    embed.set_image(url="https://cdn.discordapp.com/attachments/1499152772225040544/1511884332908941432/Honor_of_the_Watch.png?ex=6a221382&is=6a20c202&hm=827b132ad5ae8a794d09aa6503eab5a9ba0c592105609f2efe09284b955d781e&")
    camp_name = campaign.get("name") or "Jericho Watch Campaign"
    embed.set_footer(
        text=f"{camp_name}  ·  {now.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    return embed


def _clean_roster_name(member: discord.Member) -> str:
    """Return a clean display name suitable for the roster embed.

    Processing pipeline (order matters):
    1. Strip Discord custom-emoji notation ``<:name:id>`` / ``<a:name:id>``
    2. Normalize decorative unicode (small-caps, math-bold, etc.) via
       ``_normalize_display_name`` — keeps text readable without losing intent
    3. Strip stud-pip glyphs  ●⚬▬
    4. Collapse runs of whitespace to a single space and trim edges
    5. Strip rank name prefix (case-insensitive) so only the personal name remains
    6. Cap length at 40 chars to keep roster lines tidy
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
    # 5. Strip rank prefix (longest match first to handle multi-word ranks)
    rank_list: list[str] = _b("RANK_ROLES_PRIORITY") or []
    out_lower = out.lower()
    for rank in sorted(rank_list, key=len, reverse=True):
        if out_lower.startswith(rank.lower()):
            out = out[len(rank):].strip()
            break
    # 6. Length cap
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
    default_state = {
        company: {
            "channel_id": channel_id,
            "hc_message_id": None,
            "command_message_id": None,
            "killteam_message_ids": {},
            "campaign_accolades_message_id": None,
        }
        for company, channel_id in ROSTER_COMPANY_CHANNELS.items()
    }
    try:
        if os.path.exists(ROSTER_STATE_PATH):
            with open(ROSTER_STATE_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged_state = {}
                for company, default_company_state in default_state.items():
                    existing_company_state = data.get(company)
                    if not isinstance(existing_company_state, dict):
                        merged_state[company] = dict(default_company_state)
                        continue
                    merged_state[company] = {
                        "channel_id": existing_company_state.get("channel_id", default_company_state["channel_id"]),
                        "hc_message_id": existing_company_state.get("hc_message_id"),
                        "command_message_id": existing_company_state.get("command_message_id"),
                        "killteam_message_ids": (
                            existing_company_state.get("killteam_message_ids")
                            if isinstance(existing_company_state.get("killteam_message_ids"), dict)
                            else {}
                        ),
                        "campaign_accolades_message_id": existing_company_state.get("campaign_accolades_message_id"),
                    }
                return merged_state
    except Exception as exc:
        _log().warning(f"Roster: failed to load state from {ROSTER_STATE_PATH}: {exc}")
    # Return default structure when no file exists yet
    return default_state


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
) -> List[Tuple[str, int, List[discord.Member]]]:
    """Return list of (kt_role_name, kt_role_id, sorted_members) for Kill Teams in a company.

    A Kill Team is a guild role whose name contains "Kill Team" (case-insensitive)
    and that has at least one non-Reserve member who also holds the company role.
    Returns up to 4 Kill Teams, sorted by role name.
    """
    kt_roles: List[discord.Role] = []
    for role in guild.roles:
        rn_lower = (role.name or "").lower()
        if "kill team" in rn_lower and "champion" not in rn_lower:
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
            results.append((role.name, role.id, sorted(members, key=_sort_key_for_member)))
        if len(results) >= 4:
            break
    return results


# ---------------------------------------------------------------------------
# Embed rendering
# ---------------------------------------------------------------------------

_EMBED_COLOR = discord.Color.from_rgb(96, 125, 139)  # Deathwatch steel blue-grey


def _fmt_title(text: str, emoji_str: str = "") -> str:
    """Build a roster embed title with emoji on both sides.

    ``text`` is the uppercase label (e.g. 'HIGH COMMAND').
    ``emoji_str`` is an already-resolved Discord emoji string such as
    ``'<:Deathwatch:123>'``, or empty string to fall back to plain ᛭⋅…⋅᛭.
    """
    if emoji_str:
        return f"{emoji_str} {text} {emoji_str}"
    return f"\u16ed\u22c5 {text} \u22c5\u16ed"  # ᛭⋅ … ⋅᛭ fallback


def _render_member_line(guild: discord.Guild, member: discord.Member) -> str:
    """Render a single roster line: ``:chapteremoji: | @mention``."""
    home_chapters: List[str] = _b("HOME_CHAPTERS") or []
    role_names = _member_role_names(member)
    chapter_emoji_str = ""
    for chapter in home_chapters:
        if chapter in role_names:
            chapter_emoji_str = _get_emoji_by_name(guild, chapter) or ""
            break

    mention = member.mention
    left = chapter_emoji_str or "·"
    return f"{left} | {mention}"


def _build_embed(
    title: str,
    members: List[discord.Member],
    guild: discord.Guild,
    last_updated: Optional[datetime] = None,
    image_url: Optional[str] = None,
) -> discord.Embed:
    """Build a roster discord.Embed for a list of members.

    ``title`` is placed at the top of the description (so role mentions render)
    rather than in the embed title field.
    Gracefully truncates the description if the member list would exceed
    ROSTER_EMBED_DESC_LIMIT characters.
    """
    ts = last_updated or datetime.now(timezone.utc)
    count = len(members)
    noun = "Brother" if count == 1 else "Brothers"

    embed = discord.Embed(color=_EMBED_COLOR)
    if image_url:
        embed.set_image(url=image_url)

    # Title goes into description so role mentions are rendered by Discord
    SEPARATOR = "\u2500" * 24  # ────────────────────────
    header = f"{title}\n**{count} {noun} Assigned**\n{SEPARATOR}"

    if not members:
        embed.description = f"{header}\n*No members currently assigned.*"
    else:
        lines: List[str] = []
        truncated_count = 0
        running_len = len(header) + 1  # account for header in limit

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

        description = header + "\n" + "\n".join(lines)

        if truncated_count:
            note = f"\n*…and {truncated_count} more not shown (embed limit reached)*"
            description += note
            _log().warning(
                f"Roster: '{title}' truncated — {truncated_count} member(s) omitted to stay within embed limit"
            )

        embed.description = description

    embed.set_footer(
        text=f"Recorded by decree of Watch Command  ·  {ts.strftime('%Y-%m-%d %H:%M UTC')}"
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

    # Fetch guild emojis fresh from the API so we don't depend on the cache.
    try:
        _fetched_emojis: list[discord.Emoji] = await guild.fetch_emojis()
        _log().debug(f"Roster: fetched {len(_fetched_emojis)} emojis from guild {guild.id}")
    except Exception as _fe:
        _fetched_emojis = list(guild.emojis)
        _log().warning(f"Roster: fetch_emojis() failed ({_fe}), falling back to cache ({len(_fetched_emojis)} emojis)")

    def _te(name: str) -> str:
        normalized = name.replace(" ", "").replace("-", "").replace("'", "").lower()
        for emoji in _fetched_emojis:
            if emoji.name.lower() == normalized:
                return str(emoji)
        all_names = sorted(e.name for e in _fetched_emojis)
        _log().warning(
            f"Roster: emoji '{name}' not found in guild {guild.id}. "
            f"All {len(all_names)} emoji names: {all_names}"
        )
        return ""

    hc_emoji = _te("Deathwatch")        # :Deathwatch:
    cmd_emoji = _te("WatchCommand")     # :WatchCommand:
    company_emoji = _te(short_name)     # :Primus: / :Secundus: / etc.

    # Resolve role IDs for mentions (fallback to plain text if role not found)
    # Company Command embed uses "@Primus Command" / "@Secundus Command" role
    cmd_role = discord.utils.get(guild.roles, name=f"{short_name} Command")
    company_role_mention = f"<@&{cmd_role.id}>" if cmd_role else f"{short_name} Command"

    # ── Embed 1: High Command ────────────────────────────────────────────────
    hc_members = _get_hc_members(guild)
    hc_embed = _build_embed(
        _fmt_title(f"<@&{HIGH_COMMAND_ROLE_ID}>", hc_emoji),
        hc_members,
        guild,
        last_updated=now,
        image_url=ROSTER_IMAGE_HIGH_COMMAND,
    )
    hc_msg_id = await _upsert_message(
        channel, company_state.get("hc_message_id"), hc_embed
    )
    company_state["hc_message_id"] = hc_msg_id

    # ── Embed 1b: Campaign Accolades (only when campaign active) ─────────────
    campaign_state = _load_campaign_state_for_roster()
    accolades_embed = _build_campaign_accolades_embed(company_name, campaign_state, now=now, guild=guild) if campaign_state else None
    if accolades_embed:
        accolades_msg_id = await _upsert_message(
            channel, company_state.get("campaign_accolades_message_id"), accolades_embed
        )
        company_state["campaign_accolades_message_id"] = accolades_msg_id
    else:
        # Campaign inactive: if we have a stale message ID, clear it (message won't be deleted,
        # but we stop tracking it so a future init gets a fresh post in the right position).
        company_state["campaign_accolades_message_id"] = None

    # ── Embed 2: Company Command ─────────────────────────────────────────────
    cmd_members = _get_company_command_members(guild, company_name)
    cmd_embed = _build_embed(
        _fmt_title(company_role_mention, cmd_emoji),
        cmd_members,
        guild,
        last_updated=now,
        image_url=ROSTER_IMAGE_COMPANY_COMMAND,
    )
    cmd_msg_id = await _upsert_message(
        channel, company_state.get("command_message_id"), cmd_embed
    )
    company_state["command_message_id"] = cmd_msg_id

    # ── Embeds 3–6: Kill Teams ───────────────────────────────────────────────
    kill_teams = _get_kill_teams_for_company(guild, company_name)
    kt_message_ids: dict = dict(company_state.get("killteam_message_ids") or {})

    # Track which KT names are still active so we can clean up stale IDs
    active_kt_names = {kt_name for kt_name, _, __ in kill_teams}
    # Remove stale entries (KT disbanded / no longer has members in this company)
    for stale_kt in list(kt_message_ids.keys()):
        if stale_kt not in active_kt_names:
            _log().info(
                f"Roster: KT '{stale_kt}' no longer active for {company_name} — removing tracked message ID"
            )
            del kt_message_ids[stale_kt]

    kt_image = ROSTER_IMAGE_KILLTEAM_BY_COMPANY.get(company_name, ROSTER_IMAGE_KILLTEAM)
    for kt_name, kt_role_id, kt_members in kill_teams:
        kt_embed = _build_embed(
            _fmt_title(f"<@&{kt_role_id}>", company_emoji),
            kt_members,
            guild,
            last_updated=now,
            image_url=kt_image,
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
# Slash commands — registered at import time (same as roster_ops.py pattern)
# so they are in the tree before tree.sync() fires in on_ready.
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
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


@_g.bot.tree.command(
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
