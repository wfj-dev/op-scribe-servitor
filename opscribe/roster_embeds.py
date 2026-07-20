"""Auto-roster embed subsystem.

Maintains persistent embed messages in each configured Watch Company's roster channel.
Embeds are posted once via /roster_post (Forgemaster only) and then edited
in-place by a daily task and by /roster_refresh (Watch Command+).

Embed layout per company channel:
    1. HIGH COMMAND               — Cadre leaders only
    2. DEATHWATCH SPECIALIST      — one field per specialist cadre
    3. COMPANY ROSTER             — company captain/lieutenant plus one field per Kill Team

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

# Maps campaign company_id → Watch Company name prefix (matches configured roster company names)
def _co_id_to_roster_name(company_id: str) -> str:
    """Convert 'primus' → 'Watch Company Primus'."""
    return f"Watch Company {company_id.capitalize()}"


def _co_roster_name_to_id(roster_name: str) -> str:
    """Convert 'Watch Company Primus' → 'primus'."""
    return roster_name.replace("Watch Company", "").strip().lower()


def _configured_roster_company_channels() -> dict[str, int]:
    """Return roster channel mapping from config, falling back to constants."""
    cfg = (_b("CONFIG") or {}).get("companies") or {}
    configured: dict[str, int] = {}
    if isinstance(cfg, dict):
        for key, entry in cfg.items():
            if not isinstance(entry, dict):
                continue
            company_short_name = str(entry.get("name") or key or "").strip()
            if not company_short_name:
                continue
            try:
                channel_id = int(entry.get("rosterChannelId") or 0)
            except Exception:
                channel_id = 0
            if not channel_id:
                continue
            configured[f"Watch Company {company_short_name}"] = channel_id
    return configured or dict(ROSTER_COMPANY_CHANNELS)


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

_ROSTER_FIELD_CHAR_LIMIT = 1024
_CONTAINER_TEXT_LIMIT = 4000
_EMPTY_FIELD_NAME = "\u200b"
_DEATHWATCH_SPECIALIST_ROLE_ID = 1509921744712896724

_SPECIALIST_SECTION_ROLE_GROUPS = (
    # Blade Hall supports both legacy blade-track names and champion-track naming.
    ("Blade Hall", {"First Blade", "Bladeguard", "Blade Master", "Company Champion", "Kill Team Champion", "Lord Executioner"}),
    ("Librarius", {"Watch Librarian"}),
    ("Watch Armory", {"Watch Techmarine", "Venerable Dreadnought", "Honored Dreadnought"}),
    ("Reclusiam", {"Watch Chaplain"}),
    ("Apothecarion", {"Watch Apothecary"}),
)

_SPECIALIST_IMAGE_BY_SECTION = {
    "Blade Hall": "Hall of Champions.png",
    "Librarius": "Librarius.png",
    "Watch Armory": "Armory.png",
    "Reclusiam": "Reclusiam.png",
    "Apothecarion": "Apothecarion.png",
}


def _configured_cadre_section_image_assets() -> dict[str, str]:
    """Return optional mapping of specialist section name -> asset filename."""
    cfg = ((_b("CONFIG") or {}).get("target_packages") or {}).get("cadre_section_image_assets") or {}
    configured: dict[str, str] = {}
    if not isinstance(cfg, dict):
        return configured

    canonical_sections = {name.lower(): name for name in _SPECIALIST_IMAGE_BY_SECTION}
    for section_name, filename in cfg.items():
        section_key = canonical_sections.get(str(section_name or "").strip().lower())
        if not section_key:
            continue
        asset_name = str(filename or "").strip()
        if not asset_name:
            continue
        configured[section_key] = asset_name
    return configured


def _configured_cadre_role_image_assets() -> dict[int, str]:
    """Return optional mapping of cadre role ID -> asset filename."""
    cfg = ((_b("CONFIG") or {}).get("target_packages") or {}).get("cadre_role_image_assets") or {}
    configured: dict[int, str] = {}
    if not isinstance(cfg, dict):
        return configured
    for role_id, filename in cfg.items():
        try:
            rid = int(role_id)
        except (TypeError, ValueError):
            continue
        asset_name = str(filename or "").strip()
        if not asset_name:
            continue
        configured[rid] = asset_name
    return configured


def _specialist_image_filename(
    section_name: str,
    role_names: set[str],
    guild: Optional[discord.Guild],
) -> str:
    """Resolve specialist image filename with config override precedence.

    Precedence:
    1) target_packages.cadre_section_image_assets[section_name]
    2) target_packages.cadre_role_image_assets[role_id] (first configured role
       that belongs to this section in the current guild)
    3) module defaults in _SPECIALIST_IMAGE_BY_SECTION
    """
    section_override = _configured_cadre_section_image_assets().get(section_name)
    if section_override:
        return section_override

    role_overrides = _configured_cadre_role_image_assets()
    if role_overrides and guild is not None:
        section_role_ids = {
            int(getattr(role, "id", 0) or 0)
            for role in getattr(guild, "roles", []) or []
            if (getattr(role, "name", "") or "") in role_names
        }
        for role_id, filename in role_overrides.items():
            if role_id and role_id in section_role_ids:
                return filename

    return _SPECIALIST_IMAGE_BY_SECTION.get(section_name, "")

def _configured_kill_team_image_assets() -> dict[int, str]:
    """Return optional mapping of KT role ID -> asset filename from config."""
    cfg = ((_b("CONFIG") or {}).get("target_packages") or {}).get("kt_role_image_assets") or {}
    configured: dict[int, str] = {}
    if not isinstance(cfg, dict):
        return configured
    for role_id, filename in cfg.items():
        try:
            rid = int(role_id)
        except (TypeError, ValueError):
            continue
        asset_name = str(filename or "").strip()
        if not asset_name:
            continue
        configured[rid] = asset_name
    return configured

def _configured_company_entry(company_name: str) -> dict:
    """Return the configured company entry matching a roster company name."""
    cfg = (_b("CONFIG") or {}).get("companies") or {}
    if not isinstance(cfg, dict):
        return {}
    for key, entry in cfg.items():
        if not isinstance(entry, dict):
            continue
        raw_name = str(entry.get("name") or key or "").strip()
        if not raw_name:
            continue
        full_name = raw_name if raw_name.lower().startswith("watch company ") else f"Watch Company {raw_name}"
        if full_name == company_name:
            return entry
    return {}


def _company_command_image_filename(company_name: str) -> str:
    """Resolve company command container asset from config or naming convention."""
    entry = _configured_company_entry(company_name)
    configured_filename = str(entry.get("commandImageAsset") or "").strip()
    if configured_filename:
        return configured_filename

    short_name = company_name.replace("Watch Company", "").strip()
    short_slug = short_name.lower()
    candidate = f"{short_slug} command.png"
    if short_name and os.path.exists(_asset_path(candidate)):
        return candidate
    return "Command.png"


def _company_banner_image_filenames(company_name: str) -> list[str]:
    """Resolve the first roster container images for a company.

    Order is the company crest/banner first, followed by company art.
    """
    entry = _configured_company_entry(company_name)
    short_name = company_name.replace("Watch Company", "").strip()
    short_slug = short_name.lower()
    configured_company = str(entry.get("companyImageAsset") or "").strip()
    configured_art = str(entry.get("companyArtImageAsset") or "").strip()

    filenames: list[str] = []
    for candidate in (
        configured_company or f"{short_slug} company.png",
        configured_art or f"{short_slug} art.png",
    ):
        if candidate:
            filenames.append(candidate)
    return filenames


def _asset_roots() -> list[str]:
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))
    return [os.path.join(base, "roster images"), base]


def _asset_candidate_paths(filename: str) -> list[str]:
    name = str(filename or "")
    if "/" in name or "\\" in name or ".." in name:
        _log().warning(f"Roster: invalid asset filename (path traversal blocked): {name!r}")
        return []
    return [os.path.join(root, name) for root in _asset_roots()]


def _asset_path(filename: str) -> str:
    candidates = _asset_candidate_paths(filename)
    if not candidates:
        return os.path.join(_asset_roots()[-1], "__invalid__")
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _resolve_asset_image(filename: str | None) -> tuple[Optional[str], Optional[discord.File]]:
    """Resolve a local asset image to an attachment URL + file payload."""
    if not filename:
        return None, None
    path = _asset_path(filename)
    if not os.path.exists(path):
        candidates = ", ".join(_asset_candidate_paths(filename))
        _log().warning(f"Roster: image asset missing: {filename} (checked: {candidates})")
        return None, None
    return f"attachment://{filename}", discord.File(path, filename=filename)


def _resolve_asset_images(filenames: List[str] | None) -> tuple[list[str], list[discord.File]]:
    """Resolve multiple local asset images, preserving input order."""
    urls: list[str] = []
    files: list[discord.File] = []
    for filename in filenames or []:
        image_url, image_file = _resolve_asset_image(filename)
        if image_url and image_file:
            urls.append(image_url)
            files.append(image_file)
    return urls, files


def _kill_team_image_filename(kt_name: str, kt_role_id: Optional[int] = None) -> str:
    # Prefer role-id mapping from config so image swaps do not require code
    # changes; keep legacy name-based fallback behavior intact.
    if kt_role_id is not None:
        configured_assets = _configured_kill_team_image_assets()
        configured_asset = configured_assets.get(int(kt_role_id))
        if configured_asset:
            return configured_asset

    return f"{kt_name}.png"


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
                "company_message_id": null,
                "company_art_message_id": null,
                "hc_message_id": null,
                "specialist_message_id": null,
                "command_message_id": null,
                "killteam_message_ids": {"Kill Team Alpha": 123456}
            },
            ...
        }
    """
    default_state = {
        company: {
            "channel_id": channel_id,
            "company_message_id": None,
            "company_art_message_id": None,
            "hc_message_id": None,
            "specialist_message_id": None,
            "specialist_message_ids": {},
            "command_message_id": None,
            "killteam_message_ids": {},
        }
        for company, channel_id in _configured_roster_company_channels().items()
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
                        "company_message_id": existing_company_state.get("company_message_id"),
                        "company_art_message_id": existing_company_state.get("company_art_message_id"),
                        "hc_message_id": existing_company_state.get("hc_message_id"),
                        "specialist_message_id": existing_company_state.get("specialist_message_id"),
                        "specialist_message_ids": (
                            existing_company_state.get("specialist_message_ids")
                            if isinstance(existing_company_state.get("specialist_message_ids"), dict)
                            else {}
                        ),
                        "command_message_id": existing_company_state.get("command_message_id"),
                        "killteam_message_ids": (
                            existing_company_state.get("killteam_message_ids")
                            if isinstance(existing_company_state.get("killteam_message_ids"), dict)
                            else {}
                        ),
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


_BLADE_HALL_ROLE_BANDS: tuple[set[str], ...] = (
    {"Lord Executioner", "Blade Master"},
    {"Company Champion", "First Blade"},
    {"Kill Team Champion", "Bladeguard"},
)

_BLADE_HALL_ROLE_ORDER: tuple[str, ...] = (
    "Lord Executioner",
    "Blade Master",
    "Company Champion",
    "First Blade",
    "Kill Team Champion",
    "Bladeguard",
)


def _blade_hall_sort_key(member: discord.Member) -> tuple[int, int, int, str]:
    """Sort Blade Hall by champion band, then normal roster rank/name ordering.

    Desired band order:
    1) Lord Executioner / Blade Master
    2) Company Champion / First Blade
    3) Kill Team Champion / Bladeguard
    """
    role_names = _member_role_names(member)
    role_rank = len(_BLADE_HALL_ROLE_ORDER)
    for idx, role_name in enumerate(_BLADE_HALL_ROLE_ORDER):
        if role_name in role_names:
            role_rank = idx
            break
    band = len(_BLADE_HALL_ROLE_BANDS)
    for idx, band_roles in enumerate(_BLADE_HALL_ROLE_BANDS):
        if role_names & band_roles:
            band = idx
            break
    rank_idx, name_key = _sort_key_for_member(member)
    return (band, role_rank, rank_idx, name_key)


def _company_order_index_for_member(member: discord.Member) -> int:
    """Return configured company order index for a member, or fallback at end."""
    role_names = _member_role_names(member)
    company_names = list(_configured_roster_company_channels().keys())
    for idx, company_name in enumerate(company_names):
        if company_name in role_names:
            return idx
    return len(company_names)


def _high_command_sort_key(member: discord.Member) -> tuple[int, int, int, str]:
    """Sort HC as: Watch Master, company captains, then remaining HC members."""
    role_names = _member_role_names(member)
    rank_idx, name_key = _sort_key_for_member(member)

    if "Watch Master" in role_names:
        return (0, 0, 0, name_key)
    if "Watch Captain" in role_names:
        return (1, _company_order_index_for_member(member), rank_idx, name_key)
    return (2, rank_idx, 0, name_key)


def _collect_members_with_roles(
    guild: discord.Guild,
    role_names: set[str],
    *,
    exclude_roles: set[str] | None = None,
) -> List[discord.Member]:
    """Return sorted members who hold any requested roles and none of the excluded roles."""
    exclude_roles = exclude_roles or set()
    result: list[discord.Member] = []
    seen_ids: set[int] = set()
    for member in guild.members:
        if member.bot or _is_in_reserves(member):
            continue
        member_role_names = _member_role_names(member)
        if not (member_role_names & role_names):
            continue
        if member_role_names & exclude_roles:
            continue
        if member.id in seen_ids:
            continue
        seen_ids.add(member.id)
        result.append(member)
    return sorted(result, key=_sort_key_for_member)


def _render_member_block(
    guild: discord.Guild,
    members: List[discord.Member],
    *,
    max_chars: int,
    lead_lines: Optional[List[str]] = None,
) -> str:
    """Render a compact member block for an embed field."""
    count = len(members)
    noun = "Brother" if count == 1 else "Brothers"
    header = f"**{count} {noun} Assigned**"
    prefix_lines = [line for line in (lead_lines or []) if line]
    block_header = "\n".join(prefix_lines + [header]) if prefix_lines else header

    if not members:
        return f"{block_header}\n*No members currently assigned.*"

    lines: List[str] = []
    truncated_count = 0
    running_len = len(block_header) + 1

    for member in members:
        try:
            line = _render_member_line(guild, member)
        except Exception as exc:
            _log().warning(
                f"Roster: failed to render line for {getattr(member, 'id', '?')}: {exc}"
            )
            line = f"*[render error: {getattr(member, 'id', '?')}]*"

        if running_len + len(line) + 1 > max_chars:
            truncated_count = len(members) - len(lines)
            break
        lines.append(line)
        running_len += len(line) + 1

    block = block_header + "\n" + "\n".join(lines)
    if truncated_count:
        block += f"\n*…and {truncated_count} more not shown (embed limit reached)*"
        _log().warning(
            f"Roster: field truncated — {truncated_count} member(s) omitted to stay within embed field limit"
        )
    return block


def _build_container_view(
    title: Optional[str],
    *,
    body_blocks: List[str],
    image_url: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
    description_lines: Optional[List[str]] = None,
    last_updated: Optional[datetime] = None,
) -> discord.ui.LayoutView:
    """Build a compact roster container using one TextDisplay component."""
    lines: List[str] = []
    if title:
        lines.append(title)
    lines.extend([line for line in (description_lines or []) if line])
    lines.extend([block for block in body_blocks if block])

    full_text = "\n".join([line for line in lines if line])
    if len(full_text) > _CONTAINER_TEXT_LIMIT:
        reserve = len("\n*...truncated for container character limit.*")
        full_text = full_text[: max(0, _CONTAINER_TEXT_LIMIT - reserve)]
        full_text += "\n*...truncated for container character limit.*"
        _log().warning("Roster container text truncated to stay within 4000-char LayoutView limit")

    view = discord.ui.LayoutView(timeout=None)
    children: list[discord.ui.Item] = []
    gallery_urls = [url for url in (image_urls or ([] if image_url is None else [image_url])) if url]
    if gallery_urls:
        gallery = discord.ui.MediaGallery()
        for media_url in gallery_urls:
            gallery.add_item(media=media_url)
        children.append(gallery)
    children.append(discord.ui.TextDisplay(full_text))
    container = discord.ui.Container(*children, accent_color=None)
    view.add_item(container)
    return view


def _get_hc_members(guild: discord.Guild) -> List[discord.Member]:
    """Return high-command members (including Watch Captains), excluding Reserves."""
    result = []
    for m in guild.members:
        if m.bot:
            continue
        if _is_in_reserves(m):
            continue
        role_names = _member_role_names(m)
        role_ids = _member_role_ids(m)
        # Watch Master must always appear in High Command, even if HC role ID
        # was not applied or is temporarily missing.
        is_high_command = (
            HIGH_COMMAND_ROLE_ID in role_ids
            or "Watch Master" in role_names
            or "Huntmaster" in role_names
        )
        if not is_high_command:
            continue
        result.append(m)
    return sorted(result, key=_high_command_sort_key)


def _get_company_command_members(
    guild: discord.Guild, company_name: str
) -> List[discord.Member]:
    """Return Company Command members for a given company, sorted.

    In the Phase 3 roster layout this is intentionally limited to Watch Captain
    and Watch Lieutenant; specialists are rendered in the dedicated HC +
    Specialists embed.
    """
    result = []
    for m in guild.members:
        if m.bot:
            continue
        if _is_in_reserves(m):
            continue
        role_names = _member_role_names(m)
        # Watch Master must always be displayed in High Command.
        if "Watch Master" in role_names:
            continue
        # High Command members should not appear in Company Command,
        # except Watch Captains who belong to their company command embed.
        is_watch_captain = "Watch Captain" in role_names
        is_high_command = HIGH_COMMAND_ROLE_ID in _member_role_ids(m) or "Watch Master" in role_names
        if is_high_command and not is_watch_captain:
            continue
        if company_name not in role_names:
            continue
        if not (role_names & ROSTER_COMPANY_COMMAND_RANKS):
            continue
        result.append(m)
    return sorted(result, key=_sort_key_for_member)


def _get_company_champion_members(
    guild: discord.Guild, company_name: str
) -> List[discord.Member]:
    """Deprecated compatibility helper for legacy tests/callers.

    First Blade is no longer structurally company-bound in policy; this helper is
    retained only to avoid breaking imports and legacy test fixtures, and
    intentionally preserves the legacy company-scoped filtering behavior.
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
        if "First Blade" not in role_names:
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
            # Blade track is rendered exclusively in the Blade Hall specialist
            # container and should not duplicate into KT roster containers.
            if "First Blade" in role_names or "Bladeguard" in role_names:
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

_EMBED_COLOR = None  # No left accent color for roster embeds


def _fmt_title(text: str, emoji_str: str = "") -> str:
    """Build a roster embed title with emoji on both sides.

    ``text`` is the uppercase label (e.g. 'HIGH COMMAND').
    ``emoji_str`` is an already-resolved Discord emoji string such as
    ``'<:Deathwatch:123>'``, or empty string to fall back to plain ᛭⋅…⋅᛭.
    """
    if emoji_str:
        return f"{emoji_str} {text} {emoji_str}"
    return f"\u16ed\u22c5 {text} \u22c5\u16ed"  # ᛭⋅ … ⋅᛭ fallback


def _normalize_member_casing(text: str) -> str:
    """Normalize mixed-case tokens while preserving punctuation and symbols.

    Each run of ASCII letters is title-cased; single-letter tokens are forced to
    uppercase (preserving initials such as ``"D."``); hyphens, apostrophes, and
    other non-letter characters are left untouched.

    Examples::

        >>> _normalize_member_casing("WATCH MAsTER VAN")
        'Watch Master Van'
        >>> _normalize_member_casing("d'Amore")
        "D'Amore"
        >>> _normalize_member_casing("B. Grimm-Knight")
        'B. Grimm-Knight'
    """

    def _fix_word(match: re.Match[str]) -> str:
        word = match.group(0)
        # Preserve one-letter tokens exactly as uppercase initials (e.g., "D.").
        if len(word) == 1:
            return word.upper()
        return word[:1].upper() + word[1:].lower()

    return re.sub(r"[A-Za-z]+", _fix_word, text)


def _render_member_line(guild: discord.Guild, member: discord.Member) -> str:
    """Render a single roster line: ``:chapteremoji: | display_name`` (plain text)."""
    home_chapters: List[str] = _b("HOME_CHAPTERS") or []
    role_names = _member_role_names(member)
    chapter_emoji_str = ""
    for chapter in home_chapters:
        if chapter in role_names:
            chapter_emoji_str = _get_emoji_by_name(guild, chapter) or ""
            break

    # Use server display name (nickname-aware), normalized to plain readable text.
    display_name = _normalize_display_name(getattr(member, "display_name", "") or getattr(member, "name", ""))
    display_name = re.sub(r"\s+", " ", display_name).strip() or str(getattr(member, "id", "?"))
    # Normalize casing so mixed-case Discord nicknames render consistently
    # (e.g. "WATCH MAsTER VAN" -> "Watch Master Van").
    display_name = _normalize_member_casing(display_name)
    left = chapter_emoji_str or "·"
    return f"{left} | {display_name}"


def _tp_status_for_kt(
    kt_name: str,
    packages: dict | None = None,
    *,
    ready_icon: str = "🟢",
    deployed_icon: str = "🔴",
    assigned_icon: str = "🟡",
) -> str:
    """Return a TP deployment status line for a KT. Empty string if no data.

    ``packages`` may be a pre-loaded dict from ``target_packages.json`` to avoid
    repeated disk reads when calling this in a loop.
    """
    try:
        if packages is None:
            path = os.path.join("data", "target_packages.json")
            if not os.path.exists(path):
                return ""
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            packages = data.get("packages", {})
        active_statuses = {"pending_sgt", "recruiting", "deployed"}
        kt_pkgs = [
            p for p in packages.values()
            if p.get("assigned_kt") == kt_name and p["status"] in active_statuses
        ]
        if not kt_pkgs:
            return f"-# {ready_icon} Ready for Deployment"
        if any(p["status"] == "deployed" for p in kt_pkgs):
            return f"-# {deployed_icon} Deployed ({len(kt_pkgs)} directive{'s' if len(kt_pkgs) > 1 else ''})"
        return f"-# {assigned_icon} Assigned ({len(kt_pkgs)} directive{'s' if len(kt_pkgs) > 1 else ''})"
    except Exception:
        return ""


def _package_member_ids(pkg: dict) -> set[int]:
    """Return all member IDs actively attached to a package."""
    ids: set[int] = set()
    for uid in (pkg.get("signed_up", []) or []):
        try:
            ids.add(int(uid))
        except Exception:
            continue
    for uid in (pkg.get("assigned_specialist_ids", []) or []):
        try:
            ids.add(int(uid))
        except Exception:
            continue
    return ids


def _tp_status_for_company(
    guild: discord.Guild,
    company_name: str,
    packages: dict | None = None,
    *,
    ready_icon: str = "🟢",
    deployed_icon: str = "🔴",
    assigned_icon: str = "🟡",
) -> str:
    """Return company-command status based on member participation in active packages."""
    try:
        if packages is None:
            path = os.path.join("data", "target_packages.json")
            if not os.path.exists(path):
                return "🟢 Ready for Deployment"
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            packages = data.get("packages", {})

        active_statuses = {"pending_sgt", "recruiting", "deployed"}
        company_member_ids = {m.id for m in _get_company_command_members(guild, company_name)}
        company_pkgs = [
            p for p in packages.values()
            if p.get("status") in active_statuses
            and bool(_package_member_ids(p) & company_member_ids)
        ]

        if not company_pkgs:
            return f"-# {ready_icon} Ready for Deployment"
        if any(p.get("status") == "deployed" for p in company_pkgs):
            return f"-# {deployed_icon} Deployed ({len(company_pkgs)} directive{'s' if len(company_pkgs) > 1 else ''})"
        return f"-# {assigned_icon} Assigned ({len(company_pkgs)} directive{'s' if len(company_pkgs) > 1 else ''})"
    except Exception:
        return f"-# {ready_icon} Ready for Deployment"


def _tp_status_for_members(
    member_ids: set[int],
    packages: dict | None = None,
    *,
    ready_icon: str = "🟢",
    deployed_icon: str = "🔴",
    assigned_icon: str = "🟡",
) -> str:
    """Return active directive status for an arbitrary member set."""
    try:
        if packages is None:
            path = os.path.join("data", "target_packages.json")
            if not os.path.exists(path):
                return "-# 🟢 Ready for Deployment"
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            packages = data.get("packages", {})

        active_statuses = {"pending_sgt", "recruiting", "deployed"}
        relevant_pkgs = [
            p for p in packages.values()
            if p.get("status") in active_statuses and bool(_package_member_ids(p) & member_ids)
        ]

        if not relevant_pkgs:
            return f"-# {ready_icon} Ready for Deployment"
        if any(p.get("status") == "deployed" for p in relevant_pkgs):
            return f"-# {deployed_icon} Deployed ({len(relevant_pkgs)} directive{'s' if len(relevant_pkgs) > 1 else ''})"
        return f"-# {assigned_icon} Assigned ({len(relevant_pkgs)} directive{'s' if len(relevant_pkgs) > 1 else ''})"
    except Exception:
        return f"-# {ready_icon} Ready for Deployment"


def _tp_status_for_high_command(
    guild: discord.Guild,
    packages: dict | None = None,
    *,
    ready_icon: str = "🟢",
    deployed_icon: str = "🔴",
    assigned_icon: str = "🟡",
) -> str:
    """Return high-command status based on member participation in active packages."""
    try:
        if packages is None:
            path = os.path.join("data", "target_packages.json")
            if not os.path.exists(path):
                return "🟢 Ready for Deployment"
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            packages = data.get("packages", {})

        active_statuses = {"pending_sgt", "recruiting", "deployed"}
        hc_member_ids = {m.id for m in _get_hc_members(guild)}
        hc_pkgs = [
            p for p in packages.values()
            if p.get("status") in active_statuses and bool(_package_member_ids(p) & hc_member_ids)
        ]

        if not hc_pkgs:
            return f"-# {ready_icon} Ready for Deployment"
        if any(p.get("status") == "deployed" for p in hc_pkgs):
            return f"-# {deployed_icon} Deployed ({len(hc_pkgs)} directive{'s' if len(hc_pkgs) > 1 else ''})"
        return f"-# {assigned_icon} Assigned ({len(hc_pkgs)} directive{'s' if len(hc_pkgs) > 1 else ''})"
    except Exception:
        return f"-# {ready_icon} Ready for Deployment"


def _fortress_rep_state_name(rep: float) -> str:
    """Resolve fortress standing label from the 0..100 rep bands."""
    rep_clamped = max(0.0, min(100.0, float(rep if rep is not None and rep != "" else 50.0)))
    if rep_clamped < 17.0:
        return "CENSURED"
    if rep_clamped < 34.0:
        return "SUSPECT"
    if rep_clamped < 50.0:
        return "TOLERATED"
    if rep_clamped < 67.0:
        return "NEUTRAL"
    if rep_clamped < 84.0:
        return "FAVOURED"
    if rep_clamped < 97.0:
        return "ENDORSED"
    return "MANDATED"


def _fortress_rep_title(tp_data: dict | None = None, *, standing_prefix: str = "⚖️") -> str:
    """Return fortress standing progress-bar line (0..100); caller controls the label via standing_prefix."""
    rep_value = 50.0
    try:
        if isinstance(tp_data, dict):
            raw_rep = tp_data.get("rep")
            rep_value = float(raw_rep if raw_rep is not None and raw_rep != "" else 50.0)
    except Exception:
        rep_value = 50.0

    rep_clamped = max(0.0, min(100.0, rep_value))
    bar_width = 12
    filled = int(round((rep_clamped / 100.0) * bar_width))
    filled = max(0, min(bar_width, filled))
    bar = "=" * filled + "-" * (bar_width - filled)
    state = _fortress_rep_state_name(rep_clamped)
    return f"-# {standing_prefix} [{bar}] `{rep_clamped:.1f}/100` **{state}**"


def _load_honors() -> dict:
    """Load and return parsed honors.json, or an empty dict on any error."""
    try:
        path = os.path.join("data", "honors.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_KT_TITLE_TIERS    = ["Unproven", "Initiated", "Vigilant", "Sworn", "Hallowed", "Eternal"]
_CO_LEGACY_TITLE_TIERS = ["Unrecorded", "Marked", "Recognized", "Honored", "Exalted", "Storied"]
_CO_TITLE_TIERS    = list(_KT_TITLE_TIERS)
_CADRE_TITLE_TIERS = {
    "Blades": list(_KT_TITLE_TIERS),
    "Armory": list(_KT_TITLE_TIERS),
    "Apothecarion": list(_KT_TITLE_TIERS),
    "Librarius": list(_KT_TITLE_TIERS),
    "Reclusiam": list(_KT_TITLE_TIERS),
}
_CADRE_LEGACY_TITLE_TIERS = {
    "Blades": ["Unblooded", "Duel-Sworn", "Edge Consecrated", "Execution Masters", "Relic Edge Conclave", "Headsman's Ascendant"],
    "Armory": ["Uncalibrated", "Tempered", "Machine-Blessed", "Artificer Proven", "Relic-Smiths", "Omnissian Exemplars"],
    "Apothecarion": ["Unsworn Chirurgeons", "Field Medicae", "Gene-Locked Stewards", "Sanguine Custodians", "Vitae Keepers", "Apothecarion Ascendant"],
    "Librarius": ["Unattuned", "Warded Minds", "Empyric Disciplined", "Veil Wardens", "Hexagrammic Savants", "Oracular Ascendant"],
    "Reclusiam": ["Unanointed", "Catechized", "Zeal-Bound", "Crozius Proven", "Litany Exemplars", "Voice of the Emperor"],
}
_CADRE_BY_SPECIALIST_SECTION = {
    "Blade Hall": "Blades",
    "Watch Armory": "Armory",
    "Apothecarion": "Apothecarion",
    "Librarius": "Librarius",
    "Reclusiam": "Reclusiam",
}
_HONORS_WINDOW     = 4  # number of tiers to show in the sliding window


def _normalize_honors_tier(
    tier_name: str | None,
    current_tiers: list[str],
    legacy_tiers: list[str] | None = None,
) -> str:
    """Map persisted legacy tier names onto the active shared tier labels."""
    if tier_name in current_tiers:
        return str(tier_name)
    if legacy_tiers and tier_name in legacy_tiers:
        return current_tiers[legacy_tiers.index(str(tier_name))]
    return current_tiers[0]


def _tier_window(tiers: list, current: str, *, standing_prefix: str = "⚖️") -> str:
    """Return a sliding window of tiers centered on current.

    Current tier is **bold**, others are *italic*.
    Window shifts at edges to maintain width.
    """
    if current not in tiers:
        current = tiers[0]
    idx = tiers.index(current)

    # Keep current as centered as possible for the configured window size.
    left = (_HONORS_WINDOW - 1) // 2
    right = _HONORS_WINDOW - 1 - left
    start = max(0, idx - left)
    end = min(len(tiers), idx + right + 1)

    if end - start < _HONORS_WINDOW:
        start = max(0, end - _HONORS_WINDOW)
        end = min(len(tiers), start + _HONORS_WINDOW)

    window = tiers[start:end]
    parts = [f"**{t}**" if t == current else f"*{t}*" for t in window]
    return f"-# {standing_prefix} " + " · ".join(parts)


def _honors_title_for_kt(
    kt_name: str,
    honors: dict | None = None,
    *,
    standing_prefix: str = "⚖️",
) -> str:
    """Return formatted sliding-window honor title line for a KT.

    Args:
        kt_name: The kill team name to look up.
        honors: Pre-loaded honors dict (from ``_load_honors()``). When ``None``,
            the dict is loaded from disk on demand. Pass a pre-loaded dict when
            calling this multiple times in a single roster update to avoid
            repeated disk I/O.
    """
    if honors is None:
        honors = _load_honors()
    tier = honors.get("kill_teams", {}).get(kt_name, {}).get("tier", "Unproven")
    if not tier:
        tier = "Unproven"
    return _tier_window(_KT_TITLE_TIERS, tier, standing_prefix=standing_prefix)


def _honors_title_for_company(
    company_name: str,
    honors: dict | None = None,
    *,
    standing_prefix: str = "⚖️",
) -> str:
    """Return formatted sliding-window honor title line for a company.

    Args:
        company_name: The company name to look up.
        honors: Pre-loaded honors dict (from ``_load_honors()``). When ``None``,
            the dict is loaded from disk on demand. Pass a pre-loaded dict when
            calling this multiple times in a single roster update to avoid
            repeated disk I/O.
    """
    if honors is None:
        honors = _load_honors()
    tier = _normalize_honors_tier(
        honors.get("companies", {}).get(company_name, {}).get("tier"),
        _CO_TITLE_TIERS,
        _CO_LEGACY_TITLE_TIERS,
    )
    return _tier_window(_CO_TITLE_TIERS, tier, standing_prefix=standing_prefix)


def _honors_title_for_cadre(
    section_name: str,
    honors: dict | None = None,
    *,
    standing_prefix: str = "⚖️",
) -> str | None:
    """Return formatted sliding-window honor title line for a specialist cadre section."""
    cadre_name = _CADRE_BY_SPECIALIST_SECTION.get(section_name)
    if not cadre_name:
        return None
    tiers = _CADRE_TITLE_TIERS.get(cadre_name)
    if not tiers:
        return None
    if honors is None:
        honors = _load_honors()
    tier = _normalize_honors_tier(
        honors.get("cadres", {}).get(cadre_name, {}).get("tier"),
        tiers,
        _CADRE_LEGACY_TITLE_TIERS.get(cadre_name),
    )
    return _tier_window(tiers, tier, standing_prefix=standing_prefix)


def _build_embed(
    title: str,
    members: List[discord.Member],
    guild: discord.Guild,
    last_updated: Optional[datetime] = None,
    image_url: Optional[str] = None,
    tp_status: Optional[str] = None,
    honors_title: Optional[str] = None,
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
    header_parts = [title, f"**{count} {noun} Assigned**"]
    if tp_status:
        header_parts.append(tp_status)
    if honors_title:
        header_parts.append(honors_title)
    header_parts.append(SEPARATOR)
    header = "\n".join(header_parts)

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
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.LayoutView] = None,
    files: Optional[list[discord.File]] = None,
) -> int:
    """Edit an existing message or post a new one.

    Returns the message ID (existing or newly created).
    Raises on failure so callers can decide how to handle.
    """
    if embed is None and view is None:
        raise ValueError("_upsert_message requires either an embed or a container view")

    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            if view is not None:
                # Required when transitioning a message to LayoutView/v2 components.
                await msg.edit(
                    content=None,
                    embed=None,
                    embeds=None,
                    attachments=files or [],
                    view=view,
                )
            else:
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
            raise RuntimeError(
                f"Roster edit failed for message {message_id} in channel {channel.id}: {exc}"
            )

    # Post fresh
    if view is not None:
        if files:
            msg = await channel.send(view=view, files=files)
        else:
            msg = await channel.send(view=view)
    else:
        msg = await channel.send(embed=embed)
    return msg.id


async def _delete_message_if_exists(
    channel: discord.TextChannel,
    message_id: Optional[int],
) -> None:
    """Delete a roster message if it still exists."""
    if not message_id:
        return
    try:
        msg = await channel.fetch_message(message_id)
        await msg.delete()
    except discord.NotFound:
        _log().info(f"Roster: message {message_id} not found in {channel.id} — already gone")
    except discord.Forbidden:
        _log().warning(f"Roster: missing permissions to delete message {message_id} in {channel.id}")
    except Exception as exc:
        _log().warning(f"Roster: delete failed for message {message_id} ({exc})")


# ---------------------------------------------------------------------------
# Core update logic
# ---------------------------------------------------------------------------

async def _update_company_roster(
    guild: discord.Guild,
    company_name: str,
    state: dict,
    *,
    now: Optional[datetime] = None,
    force_repost: bool = False,
) -> None:
    """Refresh all roster embeds for one company.

    Mutates *state* in place with updated message IDs.
    Raises on unrecoverable errors; callers should catch and log.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    company_state = state.get(company_name, {})
    channel_id = company_state.get("channel_id") or _configured_roster_company_channels().get(company_name)
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

    if force_repost:
        tracked_ids: set[int] = set()
        for key in ("company_message_id", "company_art_message_id", "hc_message_id", "specialist_message_id", "command_message_id"):
            mid = company_state.get(key)
            if mid:
                tracked_ids.add(int(mid))
        for mid in (company_state.get("specialist_message_ids") or {}).values():
            if mid:
                tracked_ids.add(int(mid))
        for mid in (company_state.get("killteam_message_ids") or {}).values():
            if mid:
                tracked_ids.add(int(mid))

        for tracked_id in tracked_ids:
            await _delete_message_if_exists(channel, tracked_id)

        company_state["company_message_id"] = None
        company_state["company_art_message_id"] = None
        company_state["hc_message_id"] = None
        company_state["specialist_message_id"] = None
        company_state["specialist_message_ids"] = {}
        company_state["command_message_id"] = None
        company_state["killteam_message_ids"] = {}
        _log().info(f"Roster: force re-post reset completed for '{company_name}' ({len(tracked_ids)} tracked message(s))")

    # One-time migration: existing rosters need a full repost so the new company
    # banner container can be inserted ahead of High Command.
    if not force_repost and (
        not company_state.get("company_message_id")
        or not company_state.get("company_art_message_id")
    ):
        existing_ids = [
            company_state.get("company_message_id"),
            company_state.get("hc_message_id"),
            company_state.get("command_message_id"),
            *list((company_state.get("specialist_message_ids") or {}).values()),
            *list((company_state.get("killteam_message_ids") or {}).values()),
        ]
        if any(existing_ids):
            force_repost = True
            tracked_ids: set[int] = set()
            for tracked_id in existing_ids:
                if tracked_id:
                    tracked_ids.add(int(tracked_id))
            if company_state.get("specialist_message_id"):
                tracked_ids.add(int(company_state["specialist_message_id"]))
            for tracked_id in tracked_ids:
                await _delete_message_if_exists(channel, tracked_id)
            company_state["company_message_id"] = None
            company_state["company_art_message_id"] = None
            company_state["hc_message_id"] = None
            company_state["specialist_message_id"] = None
            company_state["specialist_message_ids"] = {}
            company_state["command_message_id"] = None
            company_state["killteam_message_ids"] = {}
            _log().info(f"Roster: migrated '{company_name}' to split-banner layout ({len(tracked_ids)} message(s) reset)")

    # Load strike directive data once so status lines can be rendered on all embeds.
    _tp_data: dict = {}
    _tp_packages: dict | None = None
    try:
        _tp_path = os.path.join("data", "target_packages.json")
        if os.path.exists(_tp_path):
            with open(_tp_path, "r", encoding="utf-8") as _f:
                _tp_data = json.load(_f) or {}
                _tp_packages = _tp_data.get("packages", {})
    except Exception:
        pass

    # Load honors data once for all embeds in this company update.
    _honors_data = _load_honors()
    banner_filenames = _company_banner_image_filenames(company_name)
    company_image_url, company_image_file = _resolve_asset_image(banner_filenames[0] if banner_filenames else None)
    art_image_url, art_image_file = _resolve_asset_image(banner_filenames[1] if len(banner_filenames) > 1 else None)
    cmd_image_url, cmd_image_file = _resolve_asset_image(
        _company_command_image_filename(company_name)
    )
    hc_image_url, hc_file = _resolve_asset_image("high command.png")

    ready_icon = _get_emoji_by_name(guild, "Ready") or "🟢"
    deployed_icon = _get_emoji_by_name(guild, "Deployed") or "🔴"
    assigned_icon = deployed_icon
    renown_prefix = "`ʀᴇɴᴏᴡɴ`"
    fortress_prefix = "`1. ᴏʀᴅᴏ sᴛᴀɴᴅɪɴɢ`"

    # ── Container 1: Company banner ─────────────────────────────────────────
    company_view = _build_container_view(
        None,
        body_blocks=[_EMPTY_FIELD_NAME],
        image_url=company_image_url,
        last_updated=now,
        description_lines=[],
    )
    company_msg_id = await _upsert_message(
        channel,
        company_state.get("company_message_id"),
        view=company_view,
        files=[company_image_file] if company_image_file else None,
    )
    company_state["company_message_id"] = company_msg_id

    # ── Container 2: Company art ────────────────────────────────────────────
    if art_image_url and art_image_file:
        company_art_view = _build_container_view(
            None,
            body_blocks=[_EMPTY_FIELD_NAME],
            image_url=art_image_url,
            last_updated=now,
            description_lines=[],
        )
        company_art_msg_id = await _upsert_message(
            channel,
            company_state.get("company_art_message_id"),
            view=company_art_view,
            files=[art_image_file],
        )
        company_state["company_art_message_id"] = company_art_msg_id
    else:
        await _delete_message_if_exists(channel, company_state.get("company_art_message_id"))
        company_state["company_art_message_id"] = None

    # ── Container 3: High Command ───────────────────────────────────────────
    hc_members = _get_hc_members(guild)
    hc_block = _render_member_block(
        guild,
        hc_members,
        max_chars=_ROSTER_FIELD_CHAR_LIMIT,
    )
    hc_view = _build_container_view(
        None,
        body_blocks=[hc_block],
        image_url=hc_image_url,
        last_updated=now,
        description_lines=[
            _tp_status_for_high_command(
                guild,
                packages=_tp_packages,
                ready_icon=ready_icon,
                deployed_icon=deployed_icon,
                assigned_icon=assigned_icon,
            ),
            _fortress_rep_title(tp_data=_tp_data, standing_prefix=fortress_prefix),
        ],
    )
    hc_msg_id = await _upsert_message(
        channel,
        company_state.get("hc_message_id"),
        view=hc_view,
        files=[hc_file] if hc_file else None,
    )
    company_state["hc_message_id"] = hc_msg_id

    # ── Specialist cadres: one container message per cadre ───────────────────
    specialist_message_ids: dict[str, int] = dict(company_state.get("specialist_message_ids") or {})
    legacy_specialist_message_id = company_state.get("specialist_message_id")
    new_specialist_message_ids: dict[str, int] = {}

    for idx, (section_name, role_names) in enumerate(_SPECIALIST_SECTION_ROLE_GROUPS):
        specialist_image_url, specialist_file = _resolve_asset_image(
            _specialist_image_filename(section_name, set(role_names), guild)
        )
        specialist_members = _collect_members_with_roles(
            guild,
            set(role_names),
            exclude_roles={"Watch Master"},
        )
        if section_name == "Blade Hall":
            specialist_members = sorted(specialist_members, key=_blade_hall_sort_key)
        specialist_block = _render_member_block(
            guild,
            specialist_members,
            max_chars=_ROSTER_FIELD_CHAR_LIMIT,
            lead_lines=[
                _tp_status_for_members(
                    {m.id for m in specialist_members},
                    packages=_tp_packages,
                    ready_icon=ready_icon,
                    deployed_icon=deployed_icon,
                    assigned_icon=assigned_icon,
                ),
                _honors_title_for_cadre(
                    section_name,
                    honors=_honors_data,
                    standing_prefix=renown_prefix,
                ) or "",
            ],
        )
        specialist_view = _build_container_view(
            None,
            body_blocks=[specialist_block],
            image_url=specialist_image_url,
            last_updated=now,
            description_lines=[],
        )

        seed_message_id = specialist_message_ids.get(section_name)
        if seed_message_id is None and idx == 0 and legacy_specialist_message_id:
            seed_message_id = legacy_specialist_message_id
        specialist_msg_id = await _upsert_message(
            channel,
            seed_message_id,
            view=specialist_view,
            files=[specialist_file] if specialist_file else None,
        )
        new_specialist_message_ids[section_name] = specialist_msg_id

    stale_specialist_ids = set(specialist_message_ids.values())
    if legacy_specialist_message_id:
        stale_specialist_ids.add(legacy_specialist_message_id)
    stale_specialist_ids -= set(new_specialist_message_ids.values())
    for stale_message_id in stale_specialist_ids:
        await _delete_message_if_exists(channel, stale_message_id)

    company_state["specialist_message_ids"] = new_specialist_message_ids
    company_state["specialist_message_id"] = None

    # ── Company command container ────────────────────────────────────────────
    cmd_members = _get_company_command_members(guild, company_name)
    kill_teams = _get_kill_teams_for_company(guild, company_name)
    combined_cmd_members = sorted(cmd_members, key=_sort_key_for_member)
    combined_cmd_block = _render_member_block(
        guild,
        combined_cmd_members,
        max_chars=_ROSTER_FIELD_CHAR_LIMIT,
        lead_lines=[
            _tp_status_for_members(
                {m.id for m in combined_cmd_members},
                packages=_tp_packages,
                ready_icon=ready_icon,
                deployed_icon=deployed_icon,
                assigned_icon=assigned_icon,
            ),
        ],
    )
    cmd_view = _build_container_view(
        None,
        body_blocks=[combined_cmd_block],
        image_url=cmd_image_url,
        last_updated=now,
        description_lines=[
            _honors_title_for_company(
                company_name,
                honors=_honors_data,
                standing_prefix=renown_prefix,
            ),
        ],
    )
    cmd_msg_id = await _upsert_message(
        channel,
        company_state.get("command_message_id"),
        view=cmd_view,
        files=[cmd_image_file] if cmd_image_file else None,
    )
    company_state["command_message_id"] = cmd_msg_id

    kt_message_ids: dict[str, int] = dict(company_state.get("killteam_message_ids") or {})
    new_kt_message_ids: dict[str, int] = {}

    for kt_name, kt_role_id, kt_members in kill_teams:
        kt_image_url, kt_image_file = _resolve_asset_image(_kill_team_image_filename(kt_name, kt_role_id))
        kt_block = _render_member_block(
            guild,
            kt_members,
            max_chars=_ROSTER_FIELD_CHAR_LIMIT,
            lead_lines=[
                _tp_status_for_kt(
                    kt_name,
                    packages=_tp_packages,
                    ready_icon=ready_icon,
                    deployed_icon=deployed_icon,
                    assigned_icon=assigned_icon,
                ),
                _honors_title_for_kt(
                    kt_name,
                    honors=_honors_data,
                    standing_prefix=renown_prefix,
                ),
            ],
        )
        kt_view = _build_container_view(
            None,
            body_blocks=[kt_block],
            image_url=kt_image_url,
            last_updated=now,
        )
        kt_message_id = await _upsert_message(
            channel,
            kt_message_ids.get(kt_name),
            view=kt_view,
            files=[kt_image_file] if kt_image_file else None,
        )
        new_kt_message_ids[kt_name] = kt_message_id

    stale_kt_ids = set(kt_message_ids.values()) - set(new_kt_message_ids.values())
    for stale_message_id in stale_kt_ids:
        await _delete_message_if_exists(channel, stale_message_id)
    kt_message_ids = new_kt_message_ids

    company_state["killteam_message_ids"] = kt_message_ids
    state[company_name] = company_state


async def _update_all_rosters(guild: discord.Guild, *, force_repost: bool = False) -> dict[str, str]:
    """Refresh roster embeds for every configured company.

    Returns a dict of ``{company_name: "ok" | error_message}``.
    State is persisted after all companies have been attempted.
    """
    async with (_g.ROSTER_STATE_LOCK or asyncio.Lock()):
        state = _load_roster_state()
        results: dict[str, str] = {}
        now = datetime.now(timezone.utc)

        for company_name in _configured_roster_company_channels():
            try:
                await _update_company_roster(
                    guild,
                    company_name,
                    state,
                    now=now,
                    force_repost=force_repost,
                )
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

    results = await _update_all_rosters(guild, force_repost=True)

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

    results = await _update_all_rosters(guild, force_repost=False)

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
