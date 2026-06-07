"""Ordo Xenos Target Packages subsystem.

Strike packages issued by Ordo Xenos for Watch Fortress Jericho to complete.
Commands: /request_target_packages, /view_target_packages, /assign_package,
          /log_strike_report, /target_package_status
"""

import os
import json
import random
import string
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys as _sys
import re

import discord
from discord import app_commands
from discord.ext import tasks as _tasks

from .constants import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from . import _bot_globals as _g

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


TARGET_PACKAGES_PATH = os.path.join(DATA_DIR, "target_packages.json")
_REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference")

_TP_LOCK = asyncio.Lock()


def _get_guild_from_bot() -> "discord.Guild | None":
    """Resolve the configured guild from the bot. Used when interaction.guild is None (DM context)."""
    bot = getattr(_g, "bot", None) or _b("bot")
    if not bot:
        return None
    guild_id = (_b("CONFIG") or {}).get("guild_id")
    if guild_id:
        return bot.get_guild(int(guild_id))
    return next(iter(bot.guilds), None)


async def _resolve_channel(guild: "discord.Guild | None", channel_id: int):
    """Resolve a channel from cache, then API as fallback."""
    if not channel_id:
        return None

    ch = guild.get_channel(int(channel_id)) if guild else None
    if ch:
        return ch

    bot = getattr(_g, "bot", None) or _b("bot")
    if not bot:
        return None

    ch = bot.get_channel(int(channel_id))
    if ch:
        return ch

    try:
        return await bot.fetch_channel(int(channel_id))
    except Exception:
        return None


async def _track_package_message(package_id: str, msg: "discord.Message | None") -> None:
    """Persist message references so package cleanup can remove all related embeds."""
    if not msg or not package_id:
        return

    channel_id = getattr(getattr(msg, "channel", None), "id", None)
    message_id = getattr(msg, "id", None)
    if not channel_id or not message_id:
        return

    async with _TP_LOCK:
        data = _load_tp()
        pkg = data.get("packages", {}).get(package_id)
        if not pkg:
            return
        pkg.setdefault("message_refs", [])
        exists = any(
            int(ref.get("channel_id", 0) or 0) == int(channel_id)
            and int(ref.get("message_id", 0) or 0) == int(message_id)
            for ref in pkg["message_refs"]
            if isinstance(ref, dict)
        )
        if not exists:
            pkg["message_refs"].append({"channel_id": int(channel_id), "message_id": int(message_id)})
            _save_tp(data)


async def _delete_package_messages(package_id: str, guild: discord.Guild) -> int:
    """Delete all tracked package messages across channels."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data.get("packages", {}).get(package_id)
        if not pkg:
            return 0

        refs: set[tuple[int, int]] = set()

        def _add_ref(ch_id, msg_id):
            if ch_id and msg_id:
                try:
                    refs.add((int(ch_id), int(msg_id)))
                except Exception:
                    pass

        _add_ref(pkg.get("sgt_accept_channel_id"), pkg.get("sgt_accept_message_id"))
        _add_ref(pkg.get("signup_channel_id"), pkg.get("signup_message_id"))

        for ref in pkg.get("specialist_notification_msgs", []):
            if isinstance(ref, dict):
                _add_ref(ref.get("channel_id"), ref.get("message_id"))

        for ref in pkg.get("message_refs", []):
            if isinstance(ref, dict):
                _add_ref(ref.get("channel_id"), ref.get("message_id"))

    deleted = 0
    for ch_id, msg_id in refs:
        try:
            ch = await _resolve_channel(guild, ch_id)
            if not ch:
                continue
            msg = await ch.fetch_message(msg_id)
            await msg.delete()
            deleted += 1
        except (discord.NotFound, discord.Forbidden):
            continue
        except Exception as e:
            _g.logger.debug(f"[TP] Failed deleting message {msg_id} in {ch_id} for {package_id}: {e}")

    async with _TP_LOCK:
        data = _load_tp()
        pkg = data.get("packages", {}).get(package_id)
        if pkg:
            pkg["sgt_accept_message_id"] = None
            pkg["sgt_accept_channel_id"] = None
            pkg["signup_message_id"] = None
            pkg["signup_channel_id"] = None
            pkg["specialist_notification_msgs"] = []
            pkg["message_refs"] = []
            _save_tp(data)

    return deleted


_DISCORD_MSG_URL_RE = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)$",
    re.IGNORECASE,
)


def _resolve_aar_record_for_link(aar_link: str) -> tuple[Optional[str], Optional[dict]]:
    """Resolve datastore AAR record from a Discord message URL.

    Returns (aar_record_key, record) if found, else (None, None).
    """
    if not aar_link:
        return None, None

    ds = getattr(_g, "DATASTORE", None)
    if ds is None:
        return None, None

    message_id: Optional[str] = None
    m = _DISCORD_MSG_URL_RE.match(aar_link.strip())
    if m:
        message_id = m.group(2)

    record = ds.get_record(message_id) if message_id else None
    key = str((record or {}).get("aar_id") or message_id or "")

    if not record:
        # Fallback by direct URL match in case format differs.
        for rec in ds.iter_records():
            if (rec.get("message_url") or "").strip() == aar_link.strip():
                record = rec
                key = str(rec.get("aar_id") or message_id or "")
                break

    if not record:
        return None, None

    if not key:
        key = str(record.get("aar_id") or "")
    return (key or None), record


async def _parse_live_aar_for_link(aar_link: str, guild: discord.Guild | None) -> Optional[dict]:
    """Fetch and parse a live AAR message when it has not yet been ingested."""
    if not aar_link or guild is None:
        return None

    m = _DISCORD_MSG_URL_RE.match(aar_link.strip())
    if not m:
        return None

    channel_id = int(m.group(1))
    message_id = int(m.group(2))
    channel = await _resolve_channel(guild, channel_id)
    if channel is None:
        return None

    try:
        msg = await channel.fetch_message(message_id)
    except Exception:
        return None

    try:
        from .aar_ops import parse_aar

        return parse_aar(msg)
    except Exception as exc:
        _g.logger.debug(f"[TP] Live AAR parse failed for {aar_link}: {exc}")
        return None


def _canonical_mission_name(name: str) -> str:
    """Normalize mission name for safe equality checks."""
    val = (name or "").lower()
    val = re.sub(r"<@&\d+>", "", val)
    val = re.sub(r"\s+", " ", val).strip()
    return val


def _expected_difficulty_for_mode(mode: str) -> str:
    return "omega_ops" if "Omega" in (mode or "") else "hard_stratagem"


async def _attach_package_to_aar_record(package_id: str, aar_link: str) -> tuple[Optional[str], Optional[str]]:
    """Attach target package metadata to the submitted AAR record, if present.

    Returns (aar_record_id, canonical_message_url) when linked, else (None, None).
    """
    if not package_id or not aar_link:
        return None, None

    ds = getattr(_g, "DATASTORE", None)
    if ds is None:
        return None, None

    key, record = _resolve_aar_record_for_link(aar_link)

    if not record:
        _g.logger.debug(f"[TP] AAR link not found in datastore for package {package_id}: {aar_link}")
        return None, None

    updated = dict(record)
    updated["target_package_id"] = package_id
    pkg_ids = list(updated.get("target_package_ids", []))
    if package_id not in pkg_ids:
        pkg_ids.append(package_id)
    updated["target_package_ids"] = pkg_ids

    # Persist under canonical key if possible.
    key = str(updated.get("aar_id") or key or "")
    if not key:
        _g.logger.debug(f"[TP] Could not resolve datastore key for package {package_id} attachment")
        return None, None
    await ds.set_record(key, updated)
    # Ensure the link appears in aar_records.json immediately after submission.
    try:
        await ds.flush()
    except Exception:
        pass
    return key, (updated.get("message_url") or aar_link)

GREEK_LETTERS = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho",
    "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega",
]

# Requirement tier keys used in briefing_templates.json
_REQ_TIER_VETERAN_OATHSWORN = "veteran_oathsworn"
_REQ_TIER_KT_COMMAND = "kt_command"
_REQ_TIER_COMPANY_COMMAND = "company_command"
_REQ_TIER_HC = "hc"
_REQ_TIER_NO_REQ = "no_req"

# Role name sets per requirement tier
_TIER_ROLES = {
    _REQ_TIER_VETERAN_OATHSWORN: ["Watch Veteran", "Oathsworn"],
    _REQ_TIER_KT_COMMAND: ["Watch Sergeant", "Kill Team Champion"],
    _REQ_TIER_COMPANY_COMMAND: [
        "Watch Captain", "Watch Lieutenant", "Company Champion",
        "Watch Techmarine", "Watch Apothecary", "Watch Chaplain",
        "Watch Librarian", "Watch Keeper", "Honored Dreadnought",
    ],
    _REQ_TIER_HC: [
        "Watch Master", "Lord Executioner", "Forgemaster", "Chief Apothecary",
        "High Chaplain", "Huntmaster", "Void Warden", "Castellan",
        "Venerable Dreadnought",
    ],
}

# Requirement tier draw weights (must sum to 100)
_TIER_WEIGHTS = [
    (_REQ_TIER_NO_REQ, 50),
    (_REQ_TIER_VETERAN_OATHSWORN, 15),
    (_REQ_TIER_KT_COMMAND, 20),
    (_REQ_TIER_COMPANY_COMMAND, 10),
    (_REQ_TIER_HC, 5),
]

# Strat table: rep range -> (pos_count, neg_count). Positive count scales 1-4, negative 2-5.
_STRAT_TABLE = {
    -3: (1, 5),
    -2: (1, 4),
    -1: (1, 3),
     0: (1, 2),
     1: (2, 2),
     2: (3, 2),
     3: (4, 2),
}

# Generator switch: disable Omega packages temporarily when needed.
ENABLE_OMEGA_PACKAGES = False

# Chaos-only mission IDs that force Intel Lapse — sourced from operations.json intel_lapse_forced field.
# Do not hardcode here; the generation code reads directly from the ops data.

# Package classification by mission objective type
_OBJECTIVE_CLASSIFICATION: dict[str, str] = {
    "demolition_trap":      "TARGET STRIKE",
    "destroy_beacon":       "TARGET STRIKE",
    "destroy_artifact":     "TARGET STRIKE",
    "assassination":        "BREACH",
    "cleanse_comms":        "PURIFICATION",
    "cleanse_corruption":   "PURIFICATION",
    "rescue_extraction":    "EXTRACTION",
    "recover_launch_asset": "EXTRACTION",
    "sabotage_reactor":     "SABOTAGE",
    "reclaim_secure":       "SABOTAGE",
    "reactivate_defences":  "SABOTAGE",
    "weapons_delivery":     "AREA DENIAL",
    "defend_secure":        "AREA DENIAL",
    "defend_waves":         "AREA DENIAL",
}

# Package statuses
STATUS_UNASSIGNED = "unassigned"          # Generated, WM hasn't distributed
STATUS_DISTRIBUTED = "distributed"        # Sent to Captain, not yet assigned to KT
STATUS_PENDING_SGT = "pending_sgt"        # Captain assigned KT, awaiting Sgt acceptance
STATUS_RECRUITING = "recruiting"          # Sgt accepted, sign-up open in KT channel
STATUS_DEPLOYED = "deployed"              # Min brothers signed up + all reqs filled
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"                  # Assigned, deadline passed without submission
STATUS_LAPSED = "lapsed"                  # Distributed, never fully assigned, deadline passed


async def _notify_send(
    channel,
    guild: discord.Guild,
    *args,
    **kwargs,
):
    """Send a notification. In debug mode, redirects all channel posts to admin DM."""
    if _is_debug_mode():
        admin_ids = list(str(x) for x in ((_b("CONFIG") or {}).get("admin_user_ids") or []))
        bot_obj = getattr(_g, "bot", None) or _b("bot")
        ch_name = getattr(channel, "name", str(getattr(channel, "id", "?")))
        _g.logger.info(f"[TP DEBUG] _notify_send to #{ch_name}, admin_ids={admin_ids}, bot={bot_obj is not None}")
        for aid in admin_ids:
            try:
                user = await bot_obj.fetch_user(int(aid)) if bot_obj else None
                _g.logger.info(f"[TP DEBUG] fetched user {aid}: {user}")
                if user:
                    if "content" in kwargs:
                        kwargs["content"] = f"[DEBUG → #{ch_name}]\n{kwargs['content']}"
                    elif args and isinstance(args[0], str):
                        args = (f"[DEBUG → #{ch_name}]\n{args[0]}",) + args[1:]
                    else:
                        kwargs.setdefault("content", f"[DEBUG → #{ch_name}]")
                    result = await user.send(*args, **kwargs)
                    _g.logger.info(f"[TP DEBUG] DM sent to {aid} successfully")
                    return result
            except Exception as e:
                _g.logger.warning(f"[TP] Debug DM failed for {aid}: {e}")
        _g.logger.warning(f"[TP DEBUG] No admin DM sent — admin_ids={admin_ids}")
        return None
    return await channel.send(*args, **kwargs)


# ---------------------------------------------------------------------------
# Data I/O
# ---------------------------------------------------------------------------

def _load_tp() -> dict:
    try:
        if not os.path.exists(TARGET_PACKAGES_PATH):
            return _empty_tp_store()
        with open(TARGET_PACKAGES_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or _empty_tp_store()
    except Exception:
        return _empty_tp_store()


def _save_tp(data: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TARGET_PACKAGES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _g.logger.error(f"[TP] Failed to save target_packages.json: {e}")


def _empty_tp_store() -> dict:
    return {
        "rep": 0.0,
        "cycle": {
            "generated_at": None,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "lapsed": 0,
        },
        "entity_stats": {
            "companies": {},
            "kill_teams": {},
            "cadres": {},
        },
        "packages": {},
        "rep_embed_message_id": None,
    }


def _load_graph() -> dict:
    path = os.path.join(_REFERENCE_DIR, "jericho_reach_graph.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_stratagems() -> list:
    path = os.path.join(_REFERENCE_DIR, "stratagems.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [s for s in data["stratagems"] if not s.get("excluded", False)]


def _load_operations() -> list:
    path = os.path.join(_REFERENCE_DIR, "operations.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["operations"] if isinstance(data, dict) else data


def _load_briefing_templates() -> dict:
    path = os.path.join(_REFERENCE_DIR, "briefing_templates.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _generate_package_id(existing_ids: set) -> str:
    """Generate a unique OX-XXXXX ID (5 alphanumeric chars, uppercase)."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(1000):
        suffix = "".join(random.choices(chars, k=5))
        pid = f"OX-{suffix}"
        if pid not in existing_ids:
            return pid
    raise RuntimeError("Failed to generate unique package ID after 1000 attempts")


# ---------------------------------------------------------------------------
# Active roster helpers
# ---------------------------------------------------------------------------

def _is_active(member: discord.Member) -> bool:
    """Return True if member is not in Reserves."""
    return not any(getattr(r, "id", 0) == RESERVES_ROLE_ID for r in getattr(member, "roles", []))


def _member_role_names(member: discord.Member) -> set:
    return {(getattr(r, "name", "") or "").strip() for r in getattr(member, "roles", [])}


def _active_members(guild: discord.Guild) -> list:
    return [m for m in guild.members if not m.bot and _is_active(m)]


def _count_active_kts(guild: discord.Guild) -> int:
    """Count distinct Kill Teams with at least one active non-reserves member holding a KT role (excl. champion)."""
    kt_role_names = {
        "Watch Sergeant", "Oathsworn", "Watch Veteran", "Watch Brother",
    }
    occupied: set = set()
    kill_teams = _b("KILL_TEAMS") or []
    kt_lower = {kt.lower(): kt for kt in kill_teams}
    for member in _active_members(guild):
        roles = _member_role_names(member)
        if not roles.intersection(kt_role_names):
            continue
        for r in getattr(member, "roles", []):
            rn = (getattr(r, "name", "") or "").strip().lower()
            if rn in kt_lower:
                occupied.add(kt_lower[rn])
                break
    return max(len(occupied), 1)


def _get_active_roles_in_guild(guild: discord.Guild) -> set:
    """Return set of role names held by at least one active non-LOA member.

    Roles are excluded when:
    - A cadre leader (Forgemaster, Chief Apothecary, etc.) is on LOA — all roles
      they administer are removed.
    - A specialist role has no remaining non-LOA, non-Reserves holder.
    """
    _CADRE_ADMIN_ROLES = {
        "Forgemaster": {"Watch Techmarine", "Honored Dreadnought", "Venerable Dreadnought", "Forgemaster"},
        "Chief Apothecary": {"Watch Apothecary", "Chief Apothecary"},
        "High Chaplain": {"Watch Chaplain", "High Chaplain"},
        "Void Warden": {"Watch Librarian", "Void Warden"},
        "Castellan": {"Watch Keeper", "Castellan"},
        "Lord Executioner": {"Kill Team Champion", "Company Champion", "Lord Executioner"},
        "Huntmaster": {"Huntmaster"},
    }

    def _is_loa(m: discord.Member) -> bool:
        return any(getattr(r, "id", 0) == LOA_ROLE_ID for r in getattr(m, "roles", []))

    active = _active_members(guild)  # already excludes Reserves
    present: set = set()
    excluded: set = set()

    # Check cadre leaders on LOA — remove their entire cadre from available roles
    for m in active:
        if not _is_loa(m):
            continue
        roles = _member_role_names(m)
        for leader_role, admin_set in _CADRE_ADMIN_ROLES.items():
            if leader_role in roles:
                excluded.update(admin_set)

    # Build available roles from non-LOA active members
    for m in active:
        if _is_loa(m):
            continue
        present.update(_member_role_names(m))

    # For specialist roles not already excluded via cadre leaders: remove if no non-LOA holder
    # (present already only contains non-LOA roles, so this is implicit)

    return present - excluded


def _get_active_role_counts(guild: discord.Guild) -> dict:
    """Return {role_name: count} for non-LOA, non-Reserves active members.

    Counts how many eligible members hold each role. Used by _draw_requirements
    to allow duplicate role requirements up to the number of available holders.
    Roles excluded by LOA cadre-leader logic are omitted entirely.
    """
    excluded = _get_active_roles_in_guild.__wrapped__(guild)[1] if hasattr(_get_active_roles_in_guild, '__wrapped__') else set()
    # Recompute excluded set inline (same logic as _get_active_roles_in_guild)
    _CADRE_ADMIN_ROLES = {
        "Forgemaster": {"Watch Techmarine", "Honored Dreadnought", "Venerable Dreadnought", "Forgemaster"},
        "Chief Apothecary": {"Watch Apothecary", "Chief Apothecary"},
        "High Chaplain": {"Watch Chaplain", "High Chaplain"},
        "Void Warden": {"Watch Librarian", "Void Warden"},
        "Castellan": {"Watch Keeper", "Castellan"},
        "Lord Executioner": {"Kill Team Champion", "Company Champion", "Lord Executioner"},
        "Huntmaster": {"Huntmaster"},
    }
    def _is_loa(m: discord.Member) -> bool:
        return any(getattr(r, "id", 0) == LOA_ROLE_ID for r in getattr(m, "roles", []))
    active = _active_members(guild)
    excluded: set = set()
    for m in active:
        if not _is_loa(m):
            continue
        roles = _member_role_names(m)
        for leader_role, admin_set in _CADRE_ADMIN_ROLES.items():
            if leader_role in roles:
                excluded.update(admin_set)

    counts: dict = {}
    for m in active:
        if _is_loa(m):
            continue
        for rn in _member_role_names(m):
            if rn not in excluded:
                counts[rn] = counts.get(rn, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Stratagem draw (conflict-aware)
# ---------------------------------------------------------------------------

def _draw_strats(rep: float, active_strats: list, mode: str = "Hard-Strat") -> dict:
    """Draw stratagem pool for a package given current rep.

    Returns {"core": [...], "wildcards": [...]}
    (intel_lapse is injected later based on mission).
    """
    rep_tier = max(-3, min(3, round(rep)))
    pos_count, neg_count = _STRAT_TABLE[rep_tier]

    # Omega-Strat: YOLO is redundant (Omega already has 1-life rules)
    omega_excluded = {"You Only Live Once"} if "Omega" in mode else set()
    # Globally blacklisted stratagems across all modes.
    mode_excluded = {
        "Great Responsibility",
        "Fatality",
        "No Delays",
        "Corrosion",
    } | omega_excluded

    buffs = [s for s in active_strats if s["type"] == "buff" and s["name"] not in mode_excluded and s["name"] != "Intelligence Lapse"]
    debuffs = [s for s in active_strats if s["type"] == "debuff" and s["name"] not in mode_excluded]

    def conflicts_with_pool(candidate: dict, pool: list) -> bool:
        # Category conflict
        for cat in candidate.get("restriction_categories", []):
            for drawn in pool:
                if cat in drawn.get("restriction_categories", []):
                    return True
        # Specific conflict (bidirectional)
        for drawn in pool:
            if candidate["name"] in drawn.get("specific_conflicts", []):
                return True
            if drawn["name"] in candidate.get("specific_conflicts", []):
                return True
        return False

    def draw_from(pool: list, count: int, existing: list) -> list:
        available = [s for s in pool if not conflicts_with_pool(s, existing)]
        random.shuffle(available)
        chosen = []
        for s in available:
            if len(chosen) >= count:
                break
            if not conflicts_with_pool(s, existing + chosen):
                chosen.append(s)
        return chosen

    core: list = []
    core += draw_from(buffs, pos_count, core)
    core += draw_from(debuffs, neg_count, core)

    return {
        "core": [{"name": s["name"], "type": s["type"]} for s in core],
        "wildcards": [],
    }


# ---------------------------------------------------------------------------
# Requirement generation
# ---------------------------------------------------------------------------

# Roles that require formal cadre assignment (not naturally present in a KT)
# Kill Team Champion is KT-command tier but still requires Lord Executioner assignment
_CADRE_SPECIALIST_ROLES = set(
    _TIER_ROLES[_REQ_TIER_COMPANY_COMMAND] + _TIER_ROLES[_REQ_TIER_HC]
) | {"Kill Team Champion"}

# Per-tier draw probability for a single requirement slot
# Weights used when independently drawing each slot
_SLOT_TIER_WEIGHTS = [
    (_REQ_TIER_VETERAN_OATHSWORN, 30),
    (_REQ_TIER_KT_COMMAND,        40),
    (_REQ_TIER_COMPANY_COMMAND,   20),
    (_REQ_TIER_HC,                10),
]


def _draw_requirements(available_roles: "set | dict", mode: str = "Hard-Strat") -> tuple:
    """Draw cross-tier role requirements for a package.

    available_roles may be a set (presence only) or dict {role: count}.
    When a dict is provided, a role may appear multiple times up to its count
    (e.g. 2x Watch Veteran if 2 eligible Vets exist in the guild).
    HC roles are still capped at max 1.
    """
    # Normalise to count dict
    if isinstance(available_roles, dict):
        role_counts = available_roles
        available_set = set(available_roles.keys())
    else:
        role_counts = {r: 1 for r in available_roles}
        available_set = set(available_roles)

    max_reqs = 3 if "Hard" in mode else 5
    max_hc = 1

    weights = [2 ** (max_reqs - i) for i in range(1, max_reqs + 1)]
    if random.random() < 0.50:
        return (_REQ_TIER_NO_REQ, [])

    target_count = random.choices(range(1, max_reqs + 1), weights=weights)[0]

    tiers, tier_weights = zip(*_SLOT_TIER_WEIGHTS)
    chosen: list = []
    chosen_counts: dict = {}  # role -> times already drawn
    hc_count = 0
    # Per-role hard caps that override holder count.
    # Default: no duplicate rank requirements. Explicit exceptions can repeat.
    role_caps = {
        "Watch Veteran": 2,
        "Kill Team Champion": 2,
    }

    for _ in range(target_count * 5):
        if len(chosen) >= target_count:
            break
        tier = random.choices(tiers, weights=tier_weights)[0]
        if tier == _REQ_TIER_HC and hc_count >= max_hc:
            continue
        # A role is available if we haven't drawn it more times than there are holders
        pool = [
            r for r in _TIER_ROLES[tier]
            if r in available_set
            and chosen_counts.get(r, 0) < min(role_counts.get(r, 1), role_caps.get(r, 1))
        ]
        if not pool:
            continue
        role = random.choice(pool)
        chosen.append(role)
        chosen_counts[role] = chosen_counts.get(role, 0) + 1
        if tier == _REQ_TIER_HC:
            hc_count += 1

    if not chosen:
        return (_REQ_TIER_NO_REQ, [])

    # Determine highest tier for briefing template selection
    tier_order = [_REQ_TIER_VETERAN_OATHSWORN, _REQ_TIER_KT_COMMAND, _REQ_TIER_COMPANY_COMMAND, _REQ_TIER_HC]
    role_to_tier = {}
    for t in tier_order:
        for r in _TIER_ROLES[t]:
            role_to_tier[r] = t
    highest = max(chosen, key=lambda r: tier_order.index(role_to_tier.get(r, _REQ_TIER_VETERAN_OATHSWORN)))
    highest_tier = role_to_tier.get(highest, _REQ_TIER_KT_COMMAND)

    return (highest_tier, chosen)


# Keep old name as alias for call sites that pass mode
def _draw_requirement_tier(available_roles: set, mode: str = "Hard-Strat") -> tuple:
    return _draw_requirements(available_roles, mode=mode)


# ---------------------------------------------------------------------------
# Briefing text assembly
# ---------------------------------------------------------------------------

def _build_briefing(node_name: str, world_type: str, mission_id: int,
                    tier_key: str, req_roles: list, rep: float,
                    templates: dict) -> str:
    # World type hook
    hooks = templates["world_type_hooks"].get(world_type, templates["world_type_hooks"]["dead_world"])
    hook = random.choice(hooks).replace("{node}", node_name)

    # Mission hook
    mission_hooks = templates["mission_hooks"].get(str(mission_id), ["eliminate the xenos threat in the area"])
    mission_hook = random.choice(mission_hooks)

    # Requirement clause — use list format for 3+ roles, inline for 1-2
    if not req_roles:
        tier_templates_raw = templates["req_tier_templates"].get(_REQ_TIER_NO_REQ, templates["req_tier_templates"]["no_req"])
        req_clause = random.choice(tier_templates_raw).replace("{rank}", "").replace("{mission}", "")
    elif len(req_roles) <= 2:
        rank_str = " and ".join(req_roles)
        tier_templates_raw = templates["req_tier_templates"].get(tier_key, templates["req_tier_templates"]["no_req"])
        req_clause = random.choice(tier_templates_raw).replace("{rank}", rank_str).replace("{mission}", "")
        if len(req_roles) == 2:
            req_clause = req_clause.replace(" is required", " are required").replace(" is required.", " are required.")
    else:
        # 3+ roles: use a generic multi-specialist line, list roles separately
        req_clause = f"Multi-specialist deployment required. ({', '.join(req_roles)})"

    # Find operation name
    try:
        ops = _load_operations()
        op_name = next((o["name"] for o in ops if o["id"] == mission_id), str(mission_id))
    except Exception:
        op_name = str(mission_id)

    sentence1 = f"{hook} — {mission_hook}. {req_clause} ({op_name})"

    sentence2 = ""
    rep_tier = max(-3, min(3, round(rep)))
    if rep_tier <= -2:
        tone_options = templates["strat_tone"]["rep_neg2"]
        sentence2 = random.choice(tone_options)
    elif rep_tier == -1:
        tone_options = templates["strat_tone"]["rep_neg1"]
        sentence2 = random.choice(tone_options)

    return f"{sentence1}\n{sentence2}".strip()


# ---------------------------------------------------------------------------
# Package generation
# ---------------------------------------------------------------------------

def _generate_single_package(
    existing_ids: set,
    rep: float,
    graph: dict,
    active_strats: list,
    templates: dict,
    available_roles: set,
    ops_list: list,
) -> dict:
    # Pick random node with eligible missions
    world_type_missions: dict = graph["world_type_missions"]
    eligible_nodes = [
        n for n in graph["nodes"]
        if world_type_missions.get(n["type"], [])
    ]
    node = random.choice(eligible_nodes)
    world_type = node["type"]
    mission_id = random.choice(world_type_missions[world_type])

    op_data = next((o for o in ops_list if o["id"] == mission_id), {})
    intel_lapse_forced = bool(op_data.get("intel_lapse_forced", False))
    classification = _OBJECTIVE_CLASSIFICATION.get(op_data.get("objective_type", ""), "STRIKE")

    if ENABLE_OMEGA_PACKAGES:
        mode = random.choices(["Hard-Strat", "Omega-Strat"], weights=[70, 30])[0]
    else:
        mode = "Hard-Strat"

    tier_key, req_roles = _draw_requirement_tier(available_roles, mode=mode)
    strats = _draw_strats(rep, active_strats, mode=mode)
    briefing = _build_briefing(
        node["id"], world_type, mission_id, tier_key, req_roles, rep, templates
    )

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(days=7)
    pid = _generate_package_id(existing_ids)

    return {
        "id": pid,
        "node": node["id"],
        "world_type": world_type,
        "mission_id": mission_id,
        "classification": classification,
        "mode": mode,
        "requirement_tier": tier_key,
        "required_roles": req_roles,
        "stratagems": strats,
        "intel_lapse": intel_lapse_forced,
        "briefing": briefing,
        "status": STATUS_UNASSIGNED,
        "assigned_captain_id": None,
        "assigned_kt": None,
        "assigned_specialist_ids": [],   # user IDs of cadre-assigned specialists
        "signed_up": [],                 # user IDs of brothers who signed up
        "assigned_company": None,
        "sgt_accept_message_id": None,   # message ID of the Sgt accept embed
        "signup_message_id": None,       # message ID of the KT sign-up embed
        "message_refs": [],              # tracked channel/message refs for cleanup
        "generated_at": now.isoformat(),
        "deadline": deadline.isoformat(),
        "completed_at": None,
        "submitted_by": None,
        "aar_link": None,
        "aar_record_id": None,
        "aar_message_id": None,
    }


async def generate_packages(guild: discord.Guild, actor: discord.Member = None) -> list:
    """Generate a batch of target packages. Returns list of package dicts."""
    async with _TP_LOCK:
        data = _load_tp()
        rep = data.get("rep", 0.0)

        graph = _load_graph()
        active_strats = _load_stratagems()
        templates = _load_briefing_templates()
        ops_list = _load_operations()
        available_roles = _get_active_role_counts(guild)

        kt_count = _count_active_kts(guild)
        multiplier = random.randint(1, 3)
        count = kt_count * multiplier

        existing_ids = set(data["packages"].keys())
        new_packages = []
        for _ in range(count):
            pkg = _generate_single_package(
                existing_ids, rep, graph, active_strats, templates, available_roles, ops_list
            )
            existing_ids.add(pkg["id"])
            data["packages"][pkg["id"]] = pkg
            data["cycle"]["total"] += 1
            new_packages.append(pkg)

        data["cycle"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        _save_tp(data)

    # Gap 1 — Notify general fortress channel when WM generates packages
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    general_channel_id = config_tp.get("general_channel_id")
    if general_channel_id:
        general_channel = guild.get_channel(int(general_channel_id)) if guild else None
        if general_channel or _is_debug_mode():
            count = len(new_packages)
            wm_flavor = [
                "The Watch Master has received intelligence packets from Ordo Xenos. Await your orders \u2014 prepare for deployment.",
                "Astropathic relay inbound. Ordo Xenos has transmitted new target packages to Watch Fortress Jericho. Stand ready, brothers.",
                "Orders inbound from Ordo Xenos. The Watch Master is reviewing strike packages. Deployment briefings to follow.",
            ]
            wm_embed = discord.Embed(
                title=f"{_DW_EMOJI} ᴏʀᴅᴏ xᴇɴᴏs ᴛʀᴀɴsᴍɪssɪᴏɴ {_DW_EMOJI}",
                description=random.choice(wm_flavor),
                color=0xC4A030,
            )
            if actor:
                wm_embed.set_author(
                    name=actor.display_name,
                    icon_url=actor.display_avatar.url if actor.display_avatar else None,
                )
            wm_embed.set_image(url="https://cdn.discordapp.com/attachments/1512944307840090304/1512952612079669268/content.png?ex=6a25f66c&is=6a24a4ec&hm=79449fbdf92892c418cbe5f66118581905755cdba1845b8cf91a8bf32545aead&")
            wm_embed.set_footer(
                text=f"{count} ᴘᴋɢ{'s' if count != 1 else ''} ʀᴇᴄᴇɪᴠᴇᴅ · ᴄʟᴇᴀʀᴀɴᴄᴇ: sᴀɴᴄᴛɪᴏɴᴇᴅ",
            )
            await _notify_send(general_channel, guild, content=f"<@&{WATCH_BROTHER_ROLE_ID}>", embed=wm_embed)

    return new_packages


async def distribute_packages(package_ids: list, guild: discord.Guild, actor: discord.Member = None) -> None:
    """Mark packages as distributed and notify Captains in highcom channel."""
    async with _TP_LOCK:
        data = _load_tp()
        config_tp = (_b("CONFIG") or {}).get("target_packages", {})
        highcom_channel_id = config_tp.get("highcom_strategium_channel_id")

        for pid in package_ids:
            if pid in data["packages"]:
                data["packages"][pid]["status"] = STATUS_DISTRIBUTED

        _save_tp(data)

    # Notify highcom channel
    if highcom_channel_id:
        channel = guild.get_channel(int(highcom_channel_id)) if guild else None
        if channel or _is_debug_mode():
            mention_str = f"<@&{WATCH_CAPTAIN_ROLE_ID}> <@&{WATCH_LIEUTENANT_ROLE_ID}>"
            count = len(package_ids)
            s = "s" if count != 1 else ""
            flavor = random.choice(_DISTRIBUTE_FLAVOR).format(count=count, s=s)
            dist_embed = discord.Embed(
                title=f"{_DW_EMOJI} ᴛᴀʀɢᴇᴛ ᴘᴋɢs ᴅɪsᴛʀɪʙᴜᴛᴇᴅ {_DW_EMOJI}",
                description=flavor,
                color=0xC4A030,
            )
            if actor:
                dist_embed.set_author(
                    name=f"Distributed by {actor.display_name}",
                    icon_url=actor.display_avatar.url if actor.display_avatar else discord.Embed.Empty,
                )
            dist_embed.set_footer(
                text=f"{count} ᴘᴋɢ{s} ᴀᴡᴀɪᴛɪɴɢ ᴀssɪɢɴᴍᴇɴᴛ · ᴄʟᴇᴀʀᴀɴᴄᴇ: sᴀɴᴄᴛɪᴏɴᴇᴅ",
            )
            _dist_img_path = os.path.join(_ASSETS_DIR, "priority operation alert assign kill teams.jpg")
            if os.path.exists(_dist_img_path):
                _dist_file = discord.File(_dist_img_path, filename="priority_op_alert.jpg")
                dist_embed.set_image(url="attachment://priority_op_alert.jpg")
                dist_msg = await _notify_send(channel, guild, content=mention_str, embed=dist_embed, file=_dist_file)
            else:
                dist_msg = await _notify_send(channel, guild, content=mention_str, embed=dist_embed)

            if dist_msg:
                for pid in package_ids:
                    await _track_package_message(pid, dist_msg)


async def assign_package_to_kt(
    package_id: str,
    kt_name: str,
    company_name: str,
    captain_member: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Assign a package to a KT. Returns (success: bool, message: str)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Package `{package_id}` not found."
        if pkg["status"] not in (STATUS_DISTRIBUTED,):
            return False, f"Package `{package_id}` is not available for assignment (status: {pkg['status']})."

        # Check KT package cap (max 3)
        kt_active = [
            p for p in data["packages"].values()
            if p.get("assigned_kt") == kt_name
            and p["status"] in (STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED)
        ]
        if len(kt_active) >= 3:
            return False, f"{kt_name} already has 3 active packages. Cannot assign more until one is completed."

        # Determine if a cadre specialist needs formal attachment
        # Line ranks (Veteran, Oathsworn, Sgt, KT Champion) are validated
        # at submission from KT membership — no formal attach needed.

        new_status = STATUS_PENDING_SGT

        pkg["assigned_kt"] = kt_name
        pkg["assigned_company"] = company_name
        pkg["assigned_captain_id"] = captain_member.id
        pkg["status"] = new_status

        # Init entity stats
        stats = data["entity_stats"]
        if kt_name not in stats["kill_teams"]:
            stats["kill_teams"][kt_name] = {"completed": 0, "failed": 0}
        if company_name and company_name not in stats["companies"]:
            stats["companies"][company_name] = {"completed": 0, "failed": 0}

        _save_tp(data)

    # Notify KT channel
    await _notify_kt_assigned(package_id, kt_name, pkg, guild, fully_active=False, captain=captain_member)

    # Cadre leader pings fire after Sgt complies (in SgtAcceptView), not here

    return True, f"Package `{package_id}` assigned to {kt_name}."


async def assign_specialist(
    package_id: str,
    specialist_member: discord.Member,
    cadre_leader: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Attach a specialist to a package. Returns (success, message)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Package `{package_id}` not found."
        if pkg["status"] not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            return False, f"Package `{package_id}` cannot accept a specialist attachment (status: {pkg['status']})."

        # Check specialist not already locked on another package
        active_statuses = {STATUS_RECRUITING, STATUS_DEPLOYED}
        for p in data["packages"].values():
            if (specialist_member.id in p.get("assigned_specialist_ids", [])
                    and p["id"] != package_id
                    and p["status"] in active_statuses):
                return False, f"{specialist_member.display_name} is already attached to package `{p['id']}`."

        pkg.setdefault("assigned_specialist_ids", [])
        if specialist_member.id not in pkg["assigned_specialist_ids"]:
            pkg["assigned_specialist_ids"].append(specialist_member.id)
        # Track who assigned each specialist
        pkg.setdefault("specialist_assigners", {})
        pkg["specialist_assigners"][str(specialist_member.id)] = cadre_leader.id

        # Check if all required roles are now covered and min sign-ups met
        now_active = _check_deployed(pkg, guild)
        if now_active:
            pkg["status"] = STATUS_DEPLOYED

        _save_tp(data)

    # Gap 2 — Ping specialist in their cadre channel
    await _notify_specialist_assigned(specialist_member, package_id, pkg, guild, cadre_leader=cadre_leader)

    return True, (
        f"{specialist_member.display_name} attached to package `{package_id}`. "
        f"Status: `{pkg['status']}`."
    )


def _requirements_satisfied(pkg: dict, guild: discord.Guild) -> bool:
    """Check if all required roles for a package are satisfied by assigned members."""
    req_roles = pkg.get("required_roles", [])
    if not req_roles:
        return True

    kt_name = pkg.get("assigned_kt")
    company_name = pkg.get("assigned_company")
    specialist_ids = set(pkg.get("assigned_specialist_ids", []))

    # Build set of roles held by: KT members + company members + HC members + attached specialists
    covered_roles: set = set()
    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        roles = _member_role_names(m)
        # Is this member part of the assigned KT or company, or HC?
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        member_kt = _resolve_killteam_for_member(m)
        member_company = _get_member_company_name(m)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in roles)
        if (member_kt == kt_name or member_company == company_name or is_hc or m.id in specialist_ids):
            covered_roles.update(roles)

    return all(role in covered_roles for role in req_roles)


async def submit_package(
    package_id: str,
    aar_link: str,
    submitter: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Submit a completed package. Returns (success, message)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Package `{package_id}` not found."

        if pkg["status"] not in (STATUS_DEPLOYED, STATUS_RECRUITING):
            return False, f"Package `{package_id}` cannot be submitted (status: `{pkg['status']}`)."

        # Check deadline
        deadline = datetime.fromisoformat(pkg["deadline"])
        if datetime.now(timezone.utc) > deadline:
            return False, f"Package `{package_id}` has expired (deadline passed)."

        # Submitter must be signed up OR be command of the assigned KT/company
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        submitter_kt = _resolve_killteam_for_member(submitter)
        submitter_company = _get_member_company_name(submitter)
        submitter_roles = _member_role_names(submitter)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in submitter_roles)
        is_command = (
            submitter_kt == pkg.get("assigned_kt")
            and (_has_role(submitter, "Watch Sergeant") or _has_role(submitter, "Kill Team Champion"))
        )
        is_signed_up = submitter.id in pkg.get("signed_up", [])

        assigned_kt = pkg.get("assigned_kt")
        assigned_company = pkg.get("assigned_company")

        if not (is_signed_up or is_command or submitter_company == assigned_company or is_hc):
            return False, (
                f"You do not have permission to submit package `{package_id}`. "
                f"Submission requires: being signed up, KT command (Sergeant/Champion), "
                f"same-company membership, or High Command."
            )

        # Package must be DEPLOYED (all reqs met)
        if pkg["status"] != STATUS_DEPLOYED:
            mode = pkg.get("mode", "")
            min_p = 2 if "Hard" in mode else 3
            signed = len(pkg.get("signed_up", []))
            return False, (
                f"Package `{package_id}` is not yet deployed. "
                f"Signed up: {signed}/{min_p} minimum brothers."
            )

        # Validate linked AAR content against package contract.
        aar_key, aar_record = _resolve_aar_record_for_link(aar_link)
        if not aar_record:
            aar_record = await _parse_live_aar_for_link(aar_link, guild)
        if not aar_record:
            return False, "AAR link could not be resolved or parsed."

        expected_brothers = {
            int(uid)
            for uid in (pkg.get("signed_up", []) + pkg.get("assigned_specialist_ids", []))
            if str(uid).strip()
        }
        aar_brothers: set[int] = set()
        for uid in aar_record.get("brother_ids", []) or []:
            try:
                aar_brothers.add(int(uid))
            except Exception:
                continue
        if aar_brothers != expected_brothers:
            return False, (
                "AAR team roster does not match package roster "
                f"(expected {len(expected_brothers)}, got {len(aar_brothers)})."
            )

        expected_mission = str(pkg.get("mission_id") or "")
        for op in (_load_operations() or []):
            if op.get("id") == pkg.get("mission_id"):
                expected_mission = str(op.get("name") or expected_mission)
                break
        aar_mission = str(aar_record.get("mission") or aar_record.get("mission_name") or "")
        if _canonical_mission_name(aar_mission) != _canonical_mission_name(expected_mission):
            return False, "AAR mission does not match target package mission."

        expected_diff = _expected_difficulty_for_mode(pkg.get("mode", ""))
        aar_diff = str(aar_record.get("difficulty_class") or "").strip().lower()
        if aar_diff != expected_diff:
            return False, "AAR difficulty does not match target package mode."

        pkg["status"] = STATUS_COMPLETED
        pkg["completed_at"] = datetime.now(timezone.utc).isoformat()
        pkg["submitted_by"] = submitter.id
        pkg["aar_link"] = aar_link
        _m = _DISCORD_MSG_URL_RE.match((aar_link or "").strip())
        if _m:
            pkg["aar_message_id"] = _m.group(2)
        if aar_key:
            pkg["aar_record_id"] = str(aar_key)

        # Update entity stats
        stats = data["entity_stats"]
        kt = pkg.get("assigned_kt")
        company = pkg.get("assigned_company")
        if kt:
            stats["kill_teams"].setdefault(kt, {"completed": 0, "failed": 0})
            stats["kill_teams"][kt]["completed"] += 1
        if company:
            stats["companies"].setdefault(company, {"completed": 0, "failed": 0})
            stats["companies"][company]["completed"] += 1

        data["cycle"]["completed"] += 1
        rep_before = float(data.get("rep", 0.0) or 0.0)
        _update_rep(data)
        rep_after = float(data.get("rep", 0.0) or 0.0)
        pkg["rep_before"] = rep_before
        pkg["rep_after"] = rep_after
        _save_tp(data)

    await _update_ox_rep_embed(guild)
    linked_aar_id, canonical_aar_link = await _attach_package_to_aar_record(package_id, aar_link)
    if linked_aar_id or canonical_aar_link:
        async with _TP_LOCK:
            data2 = _load_tp()
            pkg2 = data2.get("packages", {}).get(package_id)
            if pkg2:
                if linked_aar_id:
                    pkg2["aar_record_id"] = str(linked_aar_id)
                    # Keep aar_message_id in sync with canonical record key.
                    pkg2["aar_message_id"] = str(linked_aar_id)
                if canonical_aar_link:
                    pkg2["aar_link"] = canonical_aar_link
                _save_tp(data2)
    await _delete_package_messages(package_id, guild)
    return True, f"Package `{package_id}` marked completed. Ordo Xenos standing updated."


def _role_satisfied_by_unit(role: str, pkg: dict, guild: discord.Guild) -> bool:
    """Check if a single required role is satisfied by the assigned unit."""
    kt_name = pkg.get("assigned_kt")
    company_name = pkg.get("assigned_company")
    specialist_ids = set(pkg.get("assigned_specialist_ids", []))

    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        roles = _member_role_names(m)
        if role not in roles:
            continue
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        member_kt = _resolve_killteam_for_member(m)
        member_company = _get_member_company_name(m)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in roles)
        if (member_kt == kt_name or member_company == company_name or is_hc or m.id in specialist_ids):
            return True
    return False


def _update_rep(data: dict) -> None:
    """Recalculate and clamp rep after a completed/failed package."""
    cycle = data["cycle"]
    total_assigned = cycle.get("completed", 0) + cycle.get("failed", 0)
    if total_assigned == 0:
        return
    delta = (cycle["completed"] - cycle["failed"]) / total_assigned
    data["rep"] = max(-3.0, min(3.0, data.get("rep", 0.0) + delta))


async def _update_ox_rep_embed(guild: discord.Guild) -> None:
    """Post or update the persistent Ordo Xenos standing embed in the configured channel."""
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    channel_id = config_tp.get("ox_rep_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return

    data = _load_tp()
    rep = data.get("rep", 0.0)
    cycle = data.get("cycle", {})

    embed = discord.Embed(
        title=f"{_DW_EMOJI} ᴏʀᴅᴏ xᴇɴᴏs sᴛᴀɴᴅɪɴɢ {_DW_EMOJI}",
        description=(
            f"{_rep_display(rep)}\n\n"
            f"**Completed:** {cycle.get('completed', 0)}  ·  "
            f"**Failed:** {cycle.get('failed', 0)}  ·  "
            f"**Lapsed:** {cycle.get('lapsed', 0)}"
        ),
        color=0xC4A030,
    )
    embed.set_footer(
        text="ᴏʀᴅᴏ xᴇɴᴏs · ᴊᴇʀɪᴄʜᴏ ᴅᴀᴛᴀɴᴇᴛ",
    )

    existing_msg_id = data.get("rep_embed_message_id")
    if existing_msg_id:
        try:
            msg = await channel.fetch_message(int(existing_msg_id))
            await msg.edit(embed=embed)
            return
        except Exception:
            pass

    msg = await channel.send(embed=embed)
    async with _TP_LOCK:
        data2 = _load_tp()
        data2["rep_embed_message_id"] = msg.id
        _save_tp(data2)


# ---------------------------------------------------------------------------
# Deadline expiry checker
# ---------------------------------------------------------------------------

async def expire_packages(guild: discord.Guild) -> None:
    """Check for expired packages and mark them failed or lapsed."""
    async with _TP_LOCK:
        data = _load_tp()
        now = datetime.now(timezone.utc)
        changed = False

        for pkg in data["packages"].values():
            if pkg["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED):
                continue
            deadline = datetime.fromisoformat(pkg["deadline"])
            if now <= deadline:
                continue

            if pkg["status"] in (STATUS_DEPLOYED, STATUS_RECRUITING, STATUS_PENDING_SGT):
                pkg["status"] = STATUS_FAILED
                data["cycle"]["failed"] += 1
                # Update entity stats
                kt = pkg.get("assigned_kt")
                company = pkg.get("assigned_company")
                if kt:
                    data["entity_stats"]["kill_teams"].setdefault(kt, {"completed": 0, "failed": 0})
                    data["entity_stats"]["kill_teams"][kt]["failed"] += 1
                if company:
                    data["entity_stats"]["companies"].setdefault(company, {"completed": 0, "failed": 0})
                    data["entity_stats"]["companies"][company]["failed"] += 1
                changed = True

            elif pkg["status"] == STATUS_DISTRIBUTED:
                pkg["status"] = STATUS_LAPSED
                data["cycle"]["lapsed"] += 1
                changed = True

        if changed:
            _update_rep(data)
            _save_tp(data)

    if changed:
        # Fire rep embed update
        try:
            await _update_ox_rep_embed(guild)
        except Exception as exc:
            _g.logger.debug(f"[TP] Rep embed update failed: {exc}")


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

_DISTRIBUTE_FLAVOR = [
    "Astropathic relay inbound. Watch Captains to the strategium — {count} target package{s} transmitted from Ordo Xenos to Watch Fortress Jericho. Await your assignments.\nUse `/view_target_packages` to review and assign to your Kill Teams.",
    "Ordo Xenos datalink established. {count} target package{s} received and logged to the strategium. Watch Captains, move to review.\nUse `/view_target_packages` to assign packages to your Kill Teams.",
    "Intelligence packet cleared Vermillion. {count} target package{s} routed to Watch Fortress Jericho command. Captains — your orders await.\nUse `/view_target_packages` to review and assign.",
]

_KT_ASSIGN_FLAVOR = [
    "Data-inload received, brother. Target Package `{pid}` has been assigned to {kt}. Blackstar is prepped — await final clearance before departure.",
    "Strategic orders received. {kt} has been tasked with Target Package `{pid}`. All brothers, stand ready.",
    "Orders transmitted. {kt}, you have your mission — Target Package `{pid}` is yours. Await specialist attachment if flagged.",
]

_KT_READY_FLAVOR = [
    "All conditions met. {kt} is cleared for immediate deployment on Target Package `{pid}`. Emperor guide your blades.",
    "Deployment authorised. {kt} — Target Package `{pid}` is fully active. Blackstar is green.",
    "Final clearance granted. {kt}, Target Package `{pid}` is live. Move out.",
]

_CADRE_FLAVOR = {
    "Forgemaster": [
        "{kt} of {company} requires Techmarine or Dreadnought attachment on Target Package `{pid}`. Forgemaster — designate your specialist.\nUse `/view_target_packages` to assign.",
        "Forge-lord, {kt} needs a specialist from your cadre for Target Package `{pid}`. Forge-bond required before deployment.\nUse `/view_target_packages` to assign.",
    ],
    "Chief Apothecary": [
        "{kt} of {company} requires an Apothecary on Target Package `{pid}`. Chief Apothecary — designate your brother.\nUse `/view_target_packages` to assign.",
        "Chief Apothecary, {kt} needs your cadre's hand. Target Package `{pid}` cannot deploy without an Apothecary.\nUse `/view_target_packages` to assign.",
    ],
    "High Chaplain": [
        "Reclusiam requisition raised. {kt} of {company} requires a Chaplain on Target Package `{pid}`. High Chaplain — assign from your cadre.\nUse `/view_target_packages` to assign.",
        "High Chaplain, {kt} needs spiritual authority in the field. Target Package `{pid}` awaits your designation.\nUse `/view_target_packages` to assign.",
    ],
    "Void Warden": [
        "Librarius requisition transmitted. {kt} of {company} requires a Librarian on Target Package `{pid}`. Void Warden — assign as required.\nUse `/view_target_packages` to assign.",
        "Void Warden, the psyker's gift is needed by {kt}. Target Package `{pid}` awaits Librarian attachment.\nUse `/view_target_packages` to assign.",
    ],
    "Castellan": [
        "Watch Keeper requisition flagged. {kt} of {company} requires a Keeper on Target Package `{pid}`. Castellan — designate your operative.\nUse `/view_target_packages` to assign.",
        "Castellan, your intelligence cadre is needed by {kt}. Target Package `{pid}` awaits Watch Keeper attachment.\nUse `/view_target_packages` to assign.",
    ],
    "Lord Executioner": [
        "Champion requisition raised. {kt} of {company} requires a Champion on Target Package `{pid}`. Lord Executioner — designate as required.\nUse `/view_target_packages` to assign.",
        "Lord Executioner, {kt} needs martial authority on Target Package `{pid}`. Champion assignment required before deployment.\nUse `/view_target_packages` to assign.",
    ],
    "Huntmaster": [
        "Huntmaster, {kt} of {company} requires your personal engagement on Target Package `{pid}`. Your direct participation is demanded.\nUse `/view_target_packages` to assign yourself.",
        "Huntmaster — {kt} is called to the field on Target Package `{pid}` and requires you. Await no further orders.\nUse `/view_target_packages` to assign yourself.",
    ],
}

_CADRE_DEFAULT_FLAVOR = [
    "{kt} of {company} requires specialists on Target Package `{pid}`: {roles}. Cadre leaders — assign as required.\nUse `/view_target_packages` to assign.",
]


async def _notify_kt_assigned(
    package_id: str, kt_name: str, pkg: dict, guild: discord.Guild, fully_active: bool = False, captain: discord.Member = None
) -> None:
    """Post persistent Sgt accept embed in the watch command strategium channel."""
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    strategium_channel_id = config_tp.get("watch_command_deployment_channel_id")
    if not strategium_channel_id:
        return

    channel = guild.get_channel(int(strategium_channel_id)) if guild else None
    if not channel and not _is_debug_mode():
        return

    # Find and ping the Sgt
    sgt_mention = ""
    for m in guild.members if guild else []:
        if m.bot or not _is_active(m):
            continue
        from .forge_ops import _resolve_killteam_for_member
        if _resolve_killteam_for_member(m) == kt_name and _has_role(m, "Watch Sergeant"):
            sgt_mention = m.mention
            break

    data = _load_tp()
    rep = data.get("rep", 0.0)
    embed = _build_package_embed(pkg, rep)
    embed.add_field(
        name="▸ Orders",
        value=(
            (f"Assigned by {captain.mention}\n" if captain else "")
            + "Watch Sergeant — press **⚔ Comply** to accept these orders."
        ),
        inline=False,
    )

    view = SgtAcceptView(package_id=package_id, kt_name=kt_name)
    _cls_file = _classification_file(pkg)
    msg = await _notify_send(channel, guild, content=sgt_mention, embed=embed, view=view, **_file_kwarg(_cls_file))

    # Store message ID for later editing
    if msg:
        async with _TP_LOCK:
            data = _load_tp()
            if package_id in data["packages"]:
                data["packages"][package_id]["sgt_accept_message_id"] = msg.id
                data["packages"][package_id]["sgt_accept_channel_id"] = getattr(msg.channel, "id", None)
                _save_tp(data)
        await _track_package_message(package_id, msg)


# Cadre role → config key mapping
_ROLE_TO_CADRE_KEY: dict[str, str] = {
    "Watch Techmarine": "techmarine",
    "Forgemaster": "techmarine",
    "Honored Dreadnought": "dreadnought",
    "Venerable Dreadnought": "dreadnought",
    "Watch Librarian": "librarian",
    "Void Warden": "librarian",
    "Watch Apothecary": "apothecary",
    "Chief Apothecary": "apothecary",
    "Watch Chaplain": "chaplain",
    "High Chaplain": "chaplain",
}

# Fallback constants if not set in config
_CADRE_CHANNEL_FALLBACKS: dict[str, int] = {
    "techmarine": TECHMARINE_STAFF_CHANNEL_ID,
    "librarian": LIBRARIUS_STAFF_CHANNEL_ID,
    "apothecary": APOTHECARY_STAFF_CHANNEL_ID,
    "chaplain": CHAPLAIN_STAFF_CHANNEL_ID,
    "dreadnought": TECHMARINE_STAFF_CHANNEL_ID,
}


def _get_cadre_channel_id(role: str) -> int | None:
    """Return cadre staff channel ID for a role. Config takes precedence over constants."""
    cadre_key = _ROLE_TO_CADRE_KEY.get(role)
    if not cadre_key:
        return None
    config_ch = (((_b("CONFIG") or {}).get("target_packages") or {}).get("cadre_channels") or {}).get(cadre_key)
    if config_ch:
        return int(config_ch)
    return _CADRE_CHANNEL_FALLBACKS.get(cadre_key)


async def _notify_specialist_assigned(
    specialist_member: discord.Member, package_id: str, pkg: dict, guild: discord.Guild, cadre_leader: discord.Member = None
) -> None:
    """Ping the specialist in their cadre channel about their assignment with a full package embed."""
    specialist_roles = _member_role_names(specialist_member)

    # Determine the right channel for this specialist
    cadre_channel_id = None
    for role in specialist_roles:
        ch_id = _get_cadre_channel_id(role)
        if ch_id:
            cadre_channel_id = ch_id
            break

    # KTC fallback: ping in KT signup channel
    if not cadre_channel_id and "Kill Team Champion" in specialist_roles:
        cadre_channel_id = pkg.get("signup_channel_id")

    data = _load_tp()
    rep = data.get("rep", 0.0)
    embed = _build_package_embed(pkg, rep)
    embed.add_field(
        name="▸ Assignment",
        value=(
            (f"Assigned by {cadre_leader.mention}\n" if cadre_leader else "")
            + f"{specialist_member.mention} — you have been attached to this package. You are locked until completion or expiry."
        ),
        inline=False,
    )

    if cadre_channel_id:
        cadre_channel = guild.get_channel(int(cadre_channel_id)) if guild else None
        if cadre_channel or _is_debug_mode():
            _cls_file = _classification_file(pkg)
            sent_msg = await _notify_send(cadre_channel, guild, content=specialist_member.mention, embed=embed, **_file_kwarg(_cls_file))
            # Store specialist notification message for later roster updates
            if sent_msg:
                await _track_package_message(package_id, sent_msg)
                async with _TP_LOCK:
                    _sp_data = _load_tp()
                    if package_id in _sp_data["packages"]:
                        _sp_data["packages"][package_id].setdefault("specialist_notification_msgs", [])
                        _sp_data["packages"][package_id]["specialist_notification_msgs"].append({
                            "channel_id": getattr(sent_msg.channel, "id", None),
                            "message_id": sent_msg.id,
                        })
                        _save_tp(_sp_data)

    # Gap 3 — Update KT sign-up embed to show attached specialists
    signup_channel_id = pkg.get("signup_channel_id")
    signup_message_id = pkg.get("signup_message_id")
    if signup_channel_id and signup_message_id:
        try:
            ch = await _resolve_channel(guild, int(signup_channel_id))
            if ch:
                msg = await ch.fetch_message(int(signup_message_id))
                # Rebuild specialist list
                specialist_ids = pkg.get("assigned_specialist_ids", [])
                specialist_assigners = pkg.get("specialist_assigners", {})
                specialist_names = []
                for sid in specialist_ids:
                    m = guild.get_member(sid)
                    name = m.display_name if m else str(sid)
                    assigner_id = specialist_assigners.get(str(sid))
                    if assigner_id:
                        a = guild.get_member(assigner_id)
                        assigner_name = a.display_name if a else str(assigner_id)
                        name += f" (via {assigner_name})"
                    specialist_names.append(name)
                # Edit embed to add specialist field
                if msg.embeds:
                    embed = msg.embeds[0]
                    # Remove old specialist field if present
                    new_fields = [f for f in embed.fields if f.name != "▸ Attached Specialists"]
                    embed.clear_fields()
                    for f in new_fields:
                        embed.add_field(name=f.name, value=f.value, inline=f.inline)
                    if specialist_names:
                        embed.add_field(
                            name="▸ Attached Specialists",
                            value="\n".join(f"• {n}" for n in specialist_names),
                            inline=False,
                        )
                    await msg.edit(embed=embed)
        except Exception as e:
            _g.logger.debug(f"[TP] Failed to update KT signup embed for {package_id}: {e}")


async def _notify_cadre_leaders_needed(
    package_id: str, req_roles: list, guild: discord.Guild
) -> None:
    """Ping relevant cadre leaders in highcom that their specialist is needed."""
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    highcom_channel_id = config_tp.get("highcom_strategium_channel_id")
    if not highcom_channel_id:
        return

    channel = guild.get_channel(int(highcom_channel_id)) if guild else None
    if not channel and not _is_debug_mode():
        return

    cadre_map = {
        "Watch Techmarine": "Forgemaster",
        "Honored Dreadnought": "Forgemaster",
        "Venerable Dreadnought": "Forgemaster",
        "Watch Apothecary": "Chief Apothecary",
        "Watch Chaplain": "High Chaplain",
        "Watch Librarian": "Void Warden",
        "Watch Keeper": "Castellan",
        "Kill Team Champion": "Lord Executioner",
        "Company Champion": "Lord Executioner",
        "Huntmaster": "Huntmaster",
    }

    needed: dict[str, list] = {}  # cadre_leader_role -> [required roles it owns]
    for role in req_roles:
        cl = cadre_map.get(role)
        if cl:
            needed.setdefault(cl, []).append(role)

    if not needed:
        return

    for cl_role, owned_roles in needed.items():
        # Find members with this cadre leader role
        mentions = []
        for m in (guild.members if guild else []):
            if m.bot or not _is_active(m):
                continue
            if cl_role in _member_role_names(m):
                mentions.append(m.mention)

        flavor_pool = _CADRE_FLAVOR.get(cl_role, _CADRE_DEFAULT_FLAVOR)
        # Load KT and company context for this package
        _pkg_data = _load_tp()["packages"].get(package_id, {})
        _kt = _pkg_data.get("assigned_kt", "the assigned Kill Team")
        _company = _pkg_data.get("assigned_company", "Watch Fortress Jericho")
        flavor = random.choice(flavor_pool).format(
            pid=package_id, roles=", ".join(owned_roles), kt=_kt, company=_company
        )

        cadre_embed = discord.Embed(
            title=f"{_DW_EMOJI} sᴘᴇᴄɪᴀʟɪsᴛ ʀᴇQᴜɪsɪᴛɪᴏɴ {_DW_EMOJI}",
            description=flavor,
            color=0xE67E22,
        )
        cadre_embed.set_footer(
            text=f"ᴘᴋɢ `{package_id}` · ᴄʟᴇᴀʀᴀɴᴄᴇ: ʀᴏsᴇᴛᴛᴇ",
        )
        _spec_img = os.path.join(_ASSETS_DIR, "priority operation orders special assignment.jpg")
        _spec_file = discord.File(_spec_img, filename="specialist_requisition.jpg") if os.path.exists(_spec_img) else None
        if _spec_file:
            cadre_embed.set_image(url="attachment://specialist_requisition.jpg")

        if mentions:
            sent_msg = await _notify_send(channel, guild, content=" ".join(mentions), embed=cadre_embed, **_file_kwarg(_spec_file))
            await _track_package_message(package_id, sent_msg)
        elif _is_debug_mode():
            sent_msg = await _notify_send(channel, guild, content=f"[{cl_role} — no members found]", embed=cadre_embed, **_file_kwarg(_spec_file))
            await _track_package_message(package_id, sent_msg)


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

_DW_EMOJI = "<:Deathwatch:1501748904880767147>"
_OX_STANDING_EMOJI = ":OrdoXenosStanding:"

_REP_TIER_LABELS = {
    -3: "ANATHEMA",
    -2: "CENSURED",
    -1: "WATCHED",
     0: "SCRUTINY",
     1: "SANCTIONED",
     2: "FAVOURED",
     3: "MANDATED",
}

_COMMAND_ROLES = {"Watch Captain", "Watch Lieutenant"}


def _clearance_for_member(member: Optional[discord.Member]) -> str:
    if member is None or _is_admin(member):
        return "VERMILLION"
    roles = _member_role_names(member)
    if roles & _HC_ROLES:
        return "VERMILLION"
    if roles & _COMMAND_ROLES:
        return "ROSETTE"
    if roles & _KT_COMMAND_ROLES:
        return "HERETICUS"
    return "SANCTIONED"


def _rep_display(rep: float) -> str:
    tier = max(-3, min(3, round(rep)))
    label = _REP_TIER_LABELS[tier]
    # 7-emoji bar: 1 at -3, scaling to 7 at +3
    emoji_count = tier + 4  # -3->1, 0->4, +3->7
    return f"{_DW_EMOJI * emoji_count} {label} · {rep:+.2f}"


def _standing_skull_bar(rep: float) -> str:
    """Render standing as 1..7 Ordo Xenos skulls from tier -3..+3."""
    tier = max(-3, min(3, round(rep)))
    emoji_count = tier + 4  # -3->1, 0->4, +3->7
    return " ".join([_OX_STANDING_EMOJI] * emoji_count)


def _standing_state_name(rep: float) -> str:
    """Resolve named standing state from the rounded rep tier."""
    tier = max(-3, min(3, round(rep)))
    return _REP_TIER_LABELS[tier]


def _strat_line(strat: dict) -> str:
    t = strat["type"]
    if t == "buff":
        prefix = "+"
    elif t == "debuff":
        prefix = "-"
    else:
        prefix = "~"
    return f"{prefix} {strat['name']}"


def _build_package_embed(
    pkg: dict,
    rep: float,
    index: int = 0,
    total: int = 0,
    viewer: Optional[discord.Member] = None,
) -> discord.Embed:
    pid = pkg["id"]
    node = pkg.get("node", "Unknown")
    mission_id = pkg.get("mission_id")
    mode = pkg.get("mode", "")
    status = pkg.get("status", "")
    req_roles = pkg.get("required_roles", [])
    briefing = pkg.get("briefing", "")
    stratagems = pkg.get("stratagems", {})
    intel_lapse = pkg.get("intel_lapse", False)

    try:
        ops = _load_operations()
        op_name = next((o["name"] for o in ops if o["id"] == mission_id), str(mission_id))
    except Exception:
        op_name = str(mission_id)

    deadline_str = ""
    if pkg.get("deadline"):
        deadline = datetime.fromisoformat(pkg["deadline"])
        remaining = deadline - datetime.now(timezone.utc)
        if remaining.total_seconds() > 0:
            days = remaining.days
            hours = remaining.seconds // 3600
            deadline_str = f"{days}d {hours}h"
        else:
            deadline_str = "EXPIRED"

    page_label = f"  [{index}/{total}]" if index and total else ""
    mode_short = "HARD-STRAT" if "Hard" in mode else "OMEGA-STRAT"
    kt = pkg.get("assigned_kt")
    company = pkg.get("assigned_company")
    status_str = status.upper()
    if kt:
        status_str += f" · {kt}"
    elif company:
        status_str += f" · {company}"

    clearance = _clearance_for_member(viewer)

    _STATUS_COLORS = {
        STATUS_UNASSIGNED: 0xC4A030,     # gold
        STATUS_DISTRIBUTED: 0xC4A030,    # gold
        STATUS_PENDING_SGT: 0xE67E22,    # amber
        STATUS_RECRUITING: 0xF39C12,     # orange
        STATUS_DEPLOYED: 0x2ECC71,       # green
        STATUS_COMPLETED: 0x1ABC9C,      # teal
        STATUS_FAILED: 0x8B0000,         # dark red
        STATUS_LAPSED: 0x555555,         # grey
    }
    embed_color = _STATUS_COLORS.get(status, 0xC4A030)

    embed = discord.Embed(
        title=f"`ᴛᴀʀɢᴇᴛ ᴘᴋɢ {pid}{page_label}`",
        color=embed_color,
    )
    embed.set_author(
        name="ᴏʀᴅᴏ xᴇɴᴏs · ᴊᴇʀɪᴄʜᴏ ᴅᴀᴛᴀɴᴇᴛ",
    )

    # ▸ Intel section
    intel_lines = [
        f"**ᴛʜᴇᴀᴛʀᴇ:** {node}",
        f"**ᴏᴘᴇʀᴀᴛɪᴏɴ:** {op_name}",
        f"**ᴄʟᴀssɪғɪᴄᴀᴛɪᴏɴ:** {pkg.get('classification', 'STRIKE')}",
        f"**ᴍᴏᴅᴇ:** {mode_short}",
        f"**ᴅᴇᴀᴅʟɪɴᴇ:** {deadline_str or '—'}",
        f"**sᴛᴀᴛᴜs:** {status_str}",
    ]
    if req_roles:
        intel_lines.append(f"**ʀᴇQᴜɪʀᴇᴅ:** {', '.join(req_roles)}")
    intel_value = "\n".join(intel_lines)
    if len(intel_value) > 1024:
        intel_value = intel_value[:1020] + "\n…"
    embed.add_field(name="▸ Intel Dossier", value=intel_value, inline=False)

    # ▸ Briefing
    if briefing:
        embed.add_field(name="▸ Field Briefing", value=f"> {briefing[:380]}", inline=False)

    # ▸ Stratagems as diff code block
    core_strats = stratagems.get("core", [])
    wildcards = stratagems.get("wildcards", [])
    all_strats = core_strats + wildcards
    if intel_lapse:
        all_strats.append({"name": "Intelligence Lapse (forced)", "type": "special"})
    if all_strats:
        strat_lines = [_strat_line(s) for s in all_strats]
        strat_block = "```diff\n" + "\n".join(strat_lines) + "\n```"
        if len(strat_block) > 1024:
            strat_block = "```diff\n" + "\n".join(strat_lines)[:990] + "\n…\n```"
        embed.add_field(name="▸ Operational Stratagems", value=strat_block, inline=False)

    embed.set_footer(
        text=f"ᴄʟᴇᴀʀᴀɴᴄᴇ: {clearance}  ·  {_REP_TIER_LABELS[max(-3, min(3, round(rep)))]} {rep:+.2f}",
        icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
    )

    _CLASSIFICATION_IMAGES = {
        "TARGET STRIKE": "attachment://Target_Strike.png",
        "BREACH":        "attachment://Breach.png",
        "PURIFICATION":  "attachment://Purification.png",
        "EXTRACTION":    "attachment://Extraction.png",
        "SABOTAGE":      "attachment://Sabotage.png",
        "AREA DENIAL":   "attachment://Area_Denial.png",
    }
    cls_img = _CLASSIFICATION_IMAGES.get(pkg.get("classification", ""))
    if cls_img:
        embed.set_image(url=cls_img)

    return embed


_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")

_CLASSIFICATION_IMAGE_FILES = {
    "TARGET STRIKE": "Target_Strike.png",
    "BREACH":        "Breach.png",
    "PURIFICATION":  "Purification.png",
    "EXTRACTION":    "Extraction.png",
    "SABOTAGE":      "Sabotage.png",
    "AREA DENIAL":   "Area_Denial.png",
}


def _classification_file(pkg: dict) -> "discord.File | None":
    """Return a discord.File for the package classification image, or None."""
    filename = _CLASSIFICATION_IMAGE_FILES.get(pkg.get("classification", ""))
    if not filename:
        return None
    path = os.path.join(_ASSETS_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        return discord.File(path, filename=filename)
    except Exception:
        return None


def _file_kwarg(f: "discord.File | None") -> dict:
    """Return {'file': f} if f is not None, else empty dict. Prevents passing file=None."""
    return {"file": f} if f is not None else {}

# ---------------------------------------------------------------------------
# Persistent views (Sgt accept, KT sign-up, specialist assignment)
# ---------------------------------------------------------------------------

# Rank seniority for sign-up eligibility (higher index = more senior)
_RANK_SENIORITY: list[str] = [
    "Watch Brother",
    "Watch Veteran",
    "Oathsworn",
    "Watch Sergeant",
    "Kill Team Champion",
    "Watch Lieutenant",
    "Watch Captain",
    "Company Champion",
    "Watch Techmarine", "Watch Apothecary", "Watch Chaplain", "Watch Librarian", "Watch Keeper",
    "Honored Dreadnought",
    "Lord Executioner",
    "Forgemaster", "Chief Apothecary", "High Chaplain", "Void Warden", "Castellan", "Huntmaster",
    "Venerable Dreadnought",
    "Watch Master",
]
_RANK_SENIORITY_MAP = {r: i for i, r in enumerate(_RANK_SENIORITY)}


def _meets_rank_requirement(member: discord.Member, required_role: str, pkg: dict, guild: discord.Guild) -> bool:
    """Return True if member meets or exceeds the required rank AND is in the right unit."""
    req_idx = _RANK_SENIORITY_MAP.get(required_role, -1)
    if req_idx < 0:
        return False

    member_roles = _member_role_names(member)
    # Check rank — member must hold required role or higher
    member_max_rank = max(
        (_RANK_SENIORITY_MAP.get(r, -1) for r in member_roles),
        default=-1,
    )
    if member_max_rank < req_idx:
        return False

    # Check unit scope — same KT, same company (for company command / HC only), or HC
    from .forge_ops import _resolve_killteam_for_member
    from .roster_ops import _get_member_company_name
    member_kt = _resolve_killteam_for_member(member)
    member_company = _get_member_company_name(member)
    assigned_kt = pkg.get("assigned_kt")
    assigned_company = pkg.get("assigned_company")
    is_hc = any(r in HIGH_COMMAND_RANKS for r in member_roles)

    # Line ranks and KT command: same KT only
    if required_role in ("Watch Veteran", "Oathsworn", "Watch Sergeant", "Kill Team Champion"):
        return member_kt == assigned_kt or is_hc

    # Company command / specialists: same company or HC
    return member_company == assigned_company or is_hc


def _remaining_line_requirements(line_reqs: list[str], member_ids: list[int], guild: discord.Guild) -> list[str]:
    """Return unsatisfied line requirements after greedily assigning each signer to one requirement.

    Supports duplicate requirements by consuming counts from a multiset.
    """
    remaining = Counter(line_reqs)
    if not remaining:
        return []

    for uid in member_ids:
        m = guild.get_member(uid) if guild else None
        if not m:
            continue
        m_roles = _member_role_names(m)
        m_max = max((_RANK_SENIORITY_MAP.get(r, -1) for r in m_roles), default=-1)
        if m_max < 0:
            continue

        satisfiable = [
            req for req, cnt in remaining.items()
            if cnt > 0 and m_max >= _RANK_SENIORITY_MAP.get(req, -1)
        ]
        if not satisfiable:
            continue

        # Consume the hardest requirement this member can fill.
        chosen = max(satisfiable, key=lambda req: _RANK_SENIORITY_MAP.get(req, -1))
        remaining[chosen] -= 1

    return list(remaining.elements())


def _remaining_cadre_requirements(
    cadre_reqs: list[str],
    signed_up_ids: list[int],
    specialist_ids: list[int],
    guild: discord.Guild,
) -> list[str]:
    """Return unsatisfied cadre requirements after consuming specialists/signed-up members.

    Cadre requirements are exact-role matches (no rank-seniority substitution).
    """
    remaining = Counter(cadre_reqs)
    if not remaining:
        return []

    # Attached specialists should satisfy first, then signed-up roster members.
    ordered_ids = list(dict.fromkeys(list(specialist_ids) + list(signed_up_ids)))
    for uid in ordered_ids:
        m = guild.get_member(uid) if guild else None
        if not m:
            continue
        m_roles = _member_role_names(m)
        satisfiable = [req for req, cnt in remaining.items() if cnt > 0 and req in m_roles]
        if not satisfiable:
            continue
        chosen = satisfiable[0]
        remaining[chosen] -= 1

    return list(remaining.elements())


def _is_eligible_to_sign_up(member: discord.Member, pkg: dict, guild: discord.Guild) -> tuple[bool, str]:
    """Return (eligible, reason). Watch Brother+ check, unit scope, not already signed up."""
    if member.bot or not _is_active(member):
        return False, "Not an active member."

    # In debug mode, skip rank/unit/company checks
    if _is_debug_mode() and _is_admin(member):
        if member.id in pkg.get("signed_up", []):
            return False, "You are already signed up for this package."
        return True, ""

    # Already signed up on this package
    if member.id in pkg.get("signed_up", []):
        return False, "You are already signed up for this package."

    # Check not signed up on another active package
    data = _load_tp()
    active_statuses = {STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED}
    for p in data["packages"].values():
        if p["id"] == pkg["id"]:
            continue
        if member.id in p.get("signed_up", []) and p["status"] in active_statuses:
            return False, f"You are already signed up for package `{p['id']}`."

    # Must be Watch Brother+
    member_roles = _member_role_names(member)
    min_idx = _RANK_SENIORITY_MAP.get("Watch Brother", 0)
    member_max = max((_RANK_SENIORITY_MAP.get(r, -1) for r in member_roles), default=-1)
    if member_max < min_idx:
        return False, "You must be at least Watch Brother to sign up."

    # Must be in the assigned KT, company, or HC
    from .forge_ops import _resolve_killteam_for_member
    from .roster_ops import _get_member_company_name
    member_kt = _resolve_killteam_for_member(member)
    member_company = _get_member_company_name(member)
    is_hc = any(r in HIGH_COMMAND_RANKS for r in member_roles)
    assigned_kt = pkg.get("assigned_kt")
    assigned_company = pkg.get("assigned_company")

    if not (member_kt == assigned_kt or member_company == assigned_company or is_hc):
        return False, f"You are not part of {assigned_kt or assigned_company}."

    # Must not already be signed up on another active package
    data = _load_tp()
    for p in data.get("packages", {}).values():
        if p["id"] == pkg.get("id"):
            continue
        if (member.id in p.get("signed_up", [])
                and p["status"] in (STATUS_RECRUITING, STATUS_DEPLOYED)):
            return False, f"You are already committed to package `{p['id']}`. Complete that operation first."

    # Enforce slot availability and rank requirements
    # Hard-Strat = 3 total slots, Omega-Strat = 5 total slots
    mode = pkg.get("mode", "")
    total_capacity = 3 if "Hard" in mode else 5
    req_roles = pkg.get("required_roles", [])
    line_reqs = [r for r in req_roles if r not in _CADRE_SPECIALIST_ROLES]
    cadre_reqs = [r for r in req_roles if r in _CADRE_SPECIALIST_ROLES]
    signed_up = pkg.get("signed_up", [])
    specialist_ids = pkg.get("assigned_specialist_ids", [])

    if len(signed_up) >= total_capacity:
        return False, "This package is already at full capacity."

    # Feasibility gate: simulate this member signing up and ensure the remaining
    # slots are still sufficient to satisfy all unresolved requirements.
    projected_signed = list(signed_up) + [member.id]
    projected_uncovered_line = (
        _remaining_line_requirements(line_reqs, projected_signed, guild) if line_reqs else []
    )
    projected_uncovered_cadre = (
        _remaining_cadre_requirements(cadre_reqs, projected_signed, specialist_ids, guild) if cadre_reqs else []
    )
    slots_after_signup = total_capacity - len(projected_signed)
    remaining_required_slots = len(projected_uncovered_line) + len(projected_uncovered_cadre)
    if remaining_required_slots > slots_after_signup:
        unfilled = sorted(set(projected_uncovered_line + projected_uncovered_cadre))
        unfilled_str = ", ".join(unfilled)
        return False, f"The remaining slot(s) require: **{unfilled_str}**. Your current rank/roles do not qualify."

    return True, ""


async def _post_signup_embed(package_id: str, guild: discord.Guild, complier: discord.Member = None) -> None:
    """Post the KT sign-up embed in the KT's forum thread."""
    from .forge_ops import _get_award_announcement_channel, _resolve_killteam_for_member

    data = _load_tp()
    pkg = data["packages"].get(package_id)
    if not pkg:
        return

    kt_name = pkg.get("assigned_kt", "")
    mode = pkg.get("mode", "")
    req_roles = pkg.get("required_roles", [])

    data_rep = data.get("rep", 0.0)
    embed = _build_package_embed(pkg, data_rep)
    total_capacity = 3 if "Hard" in mode else 5
    embed.add_field(
        name="▸ Deployment Requirements",
        value=(
            (f"{complier.mention} has accepted these orders.\n" if complier else "")
            + f"**Strike Team Size:** {total_capacity}\n"
            + (f"**Required Ranks:** {', '.join(req_roles)}\n" if req_roles else "")
            + "\nPress **⚔ Comply** to register for this operation."
        ),
        inline=False,
    )

    view = SignUpView(package_id=package_id)

    # Find KT channel via any KT member
    sent = False
    for m in guild.members if guild else []:
        if m.bot or not _is_active(m):
            continue
        if _resolve_killteam_for_member(m) == kt_name:
            channel = await _get_award_announcement_channel(m, guild)
            if channel:
                _cls_file = _classification_file(pkg)
                msg = await _notify_send(channel, guild, embed=embed, view=view, **_file_kwarg(_cls_file))
                async with _TP_LOCK:
                    data2 = _load_tp()
                    if package_id in data2["packages"]:
                        data2["packages"][package_id]["signup_message_id"] = msg.id
                        data2["packages"][package_id]["signup_channel_id"] = getattr(msg.channel, "id", channel.id)
                        _save_tp(data2)
                await _track_package_message(package_id, msg)
                sent = True
            return

    # Debug fallback — DM admin if no KT channel found
    if not sent and _is_debug_mode():
        _cls_file = _classification_file(pkg)
        await _notify_send(None, guild, embed=embed, view=view, **_file_kwarg(_cls_file))


def _check_deployed(pkg: dict, guild: discord.Guild) -> bool:
    """Return True if package is full (3/Hard or 5/Omega) and all rank/specialist reqs covered."""
    mode = pkg.get("mode", "")
    total_capacity = 3 if "Hard" in mode else 5
    signed_up = pkg.get("signed_up", [])
    if len(signed_up) < total_capacity:
        return False

    req_roles = pkg.get("required_roles", [])

    # Check line role requirements covered by signed-up members
    line_reqs = [r for r in req_roles if r not in _CADRE_SPECIALIST_ROLES]
    if line_reqs:
        uncovered = _remaining_line_requirements(line_reqs, signed_up, guild)
        if uncovered:
            return False

    # Check cadre specialist requirements.
    # A cadre requirement may be satisfied by an attached specialist OR by a
    # signed-up member who explicitly holds that cadre role.
    cadre_reqs = [r for r in req_roles if r in _CADRE_SPECIALIST_ROLES]
    if not cadre_reqs:
        return True
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    uncovered_cadre = _remaining_cadre_requirements(cadre_reqs, signed_up, specialist_ids, guild)
    if uncovered_cadre:
        return False
    return True


def _role_satisfied_by_unit_ids(role: str, specialist_ids: set, pkg: dict, guild: discord.Guild) -> bool:
    """Check if a cadre role is satisfied by an attached specialist."""
    for sid in specialist_ids:
        m = guild.get_member(sid)
        if m and role in _member_role_names(m):
            return True
    return False


class SgtAcceptView(discord.ui.View):
    """Persistent view with Accept Orders button posted in watch command strategium."""

    def __init__(self, package_id: str, kt_name: str):
        super().__init__(timeout=None)
        self.package_id = package_id
        self.kt_name = kt_name

    @discord.ui.button(label="⚔ Comply", style=discord.ButtonStyle.success, custom_id="tp_sgt_accept")
    async def accept_orders(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        # Must be Sgt of the assigned KT.
        # Debug admin may bypass to help with staging flows.
        from .forge_ops import _resolve_killteam_for_member
        if not (_is_debug_mode() and _is_admin(member)):
            if not _has_role(member, "Watch Sergeant"):
                await interaction.response.send_message(
                    "Only the assigned Kill Team's Watch Sergeant may accept these orders.",
                    ephemeral=True,
                )
                return
            if _resolve_killteam_for_member(member) != self.kt_name:
                await interaction.response.send_message(f"These orders are addressed to {self.kt_name}.", ephemeral=True)
                return

        async with _TP_LOCK:
            data = _load_tp()
            pkg = data["packages"].get(self.package_id)
            if not pkg:
                await interaction.response.send_message("Package not found.", ephemeral=True)
                return
            if pkg["status"] != STATUS_PENDING_SGT:
                await interaction.response.send_message(f"Package is already `{pkg['status']}`.", ephemeral=True)
                return
            pkg["status"] = STATUS_RECRUITING
            _save_tp(data)

        # Disable the button
        button.disabled = True
        button.label = "✓ Orders Accepted"
        button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

        # Post sign-up embed in KT channel
        guild = interaction.guild or _get_guild_from_bot()
        await _post_signup_embed(self.package_id, guild, complier=member)

        # Notify cadre leaders if specialists needed
        req_roles = pkg.get("required_roles", [])
        cadre_reqs = [r for r in req_roles if r in _CADRE_SPECIALIST_ROLES]
        if cadre_reqs:
            await _notify_cadre_leaders_needed(self.package_id, cadre_reqs, guild)


class SignUpView(discord.ui.View):
    """Persistent view posted in KT channel for brothers to sign up."""

    def __init__(self, package_id: str):
        super().__init__(timeout=None)
        self.package_id = package_id

    @discord.ui.button(label="⚔ Comply", style=discord.ButtonStyle.success, custom_id="tp_signup")
    async def sign_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild

        data = _load_tp()
        pkg = data["packages"].get(self.package_id)
        if not pkg:
            await interaction.response.send_message("Package not found.", ephemeral=True)
            return
        if pkg["status"] not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            await interaction.response.send_message("This package is no longer accepting sign-ups.", ephemeral=True)
            return

        eligible, reason = _is_eligible_to_sign_up(member, pkg, guild)
        if not eligible:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        # Acknowledge promptly so long-running embed updates do not trigger
        # "Interaction Failed" while still allowing follow-up messaging.
        await interaction.response.defer(ephemeral=True)

        async with _TP_LOCK:
            data2 = _load_tp()
            pkg2 = data2["packages"].get(self.package_id)
            if not pkg2:
                await interaction.followup.send("Package not found.", ephemeral=True)
                return
            if pkg2.get("status") not in (STATUS_RECRUITING, STATUS_DEPLOYED):
                await interaction.followup.send("This package is no longer accepting sign-ups.", ephemeral=True)
                return
            eligible2, reason2 = _is_eligible_to_sign_up(member, pkg2, guild)
            if not eligible2:
                await interaction.followup.send(reason2, ephemeral=True)
                return
            pkg2.setdefault("signed_up", [])
            if member.id in pkg2["signed_up"]:
                await interaction.followup.send("Already signed up.", ephemeral=True)
                return
            pkg2["signed_up"].append(member.id)

            # Check if now deployed
            if _check_deployed(pkg2, guild):
                pkg2["status"] = STATUS_DEPLOYED

            _save_tp(data2)

        signed_up = pkg2.get("signed_up", [])
        mode = pkg2.get("mode", "")
        total_capacity = 3 if "Hard" in mode else 5
        count = len(signed_up)

        # Update the sign-up embed to show current roster
        try:
            resolved_guild = guild or _get_guild_from_bot()
            signed_names = []
            for uid in pkg2.get("signed_up", []):
                m2 = resolved_guild.get_member(uid) if resolved_guild else None
                signed_names.append(m2.display_name if m2 else str(uid))
            roster_field_name = f"▸ Signed Up ({count}/{total_capacity})"
            roster_field_value = "\n".join(f"• {n}" for n in signed_names) or "—"

            # Update KT sign-up embed
            signup_channel_id = pkg2.get("signup_channel_id")
            signup_message_id = pkg2.get("signup_message_id")
            if signup_channel_id and signup_message_id and resolved_guild:
                ch = await _resolve_channel(resolved_guild, int(signup_channel_id))
                if ch:
                    msg = await ch.fetch_message(int(signup_message_id))
                    if msg.embeds:
                        upd_embed = msg.embeds[0]
                        new_fields = [f for f in upd_embed.fields if not f.name.startswith("▸ Signed Up")]
                        upd_embed.clear_fields()
                        for f in new_fields:
                            upd_embed.add_field(name=f.name, value=f.value, inline=f.inline)
                        upd_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
                        await msg.edit(embed=upd_embed)

            # Update specialist notification embeds
            for sp_msg_ref in pkg2.get("specialist_notification_msgs", []):
                try:
                    sp_ch_id = sp_msg_ref.get("channel_id")
                    sp_msg_id = sp_msg_ref.get("message_id")
                    if not sp_ch_id or not sp_msg_id or not resolved_guild:
                        continue
                    sp_ch = await _resolve_channel(resolved_guild, int(sp_ch_id))
                    if not sp_ch:
                        continue
                    sp_msg = await sp_ch.fetch_message(int(sp_msg_id))
                    if sp_msg.embeds:
                        sp_embed = sp_msg.embeds[0]
                        sp_fields = [f for f in sp_embed.fields if not f.name.startswith("▸ Signed Up")]
                        sp_embed.clear_fields()
                        for f in sp_fields:
                            sp_embed.add_field(name=f.name, value=f.value, inline=f.inline)
                        sp_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
                        await sp_msg.edit(embed=sp_embed)
                except Exception as e:
                    _g.logger.debug(f"[TP] Failed specialist roster update for {self.package_id}: {e}")
        except Exception as e:
            _g.logger.debug(f"[TP] Failed signup roster update for {self.package_id}: {e}")


    @discord.ui.button(label="Stand Down", style=discord.ButtonStyle.secondary, custom_id="tp_stand_down")
    async def stand_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        async with _TP_LOCK:
            data = _load_tp()
            pkg = data["packages"].get(self.package_id)
            if not pkg:
                await interaction.response.send_message("Package not found.", ephemeral=True)
                return
            if member.id not in pkg.get("signed_up", []):
                await interaction.response.send_message("You are not signed up for this package.", ephemeral=True)
                return
            if pkg["status"] == STATUS_DEPLOYED:
                await interaction.response.send_message(
                    "Package is already deployed — you cannot stand down at this stage.", ephemeral=True
                )
                return
            pkg["signed_up"].remove(member.id)
            _save_tp(data)

        # Update the signup embed roster
        try:
            resolved_guild = interaction.guild or _get_guild_from_bot()
            data3 = _load_tp()
            pkg3 = data3["packages"].get(self.package_id, {})
            signed_up3 = pkg3.get("signed_up", [])
            mode3 = pkg3.get("mode", "")
            total_capacity3 = 3 if "Hard" in mode3 else 5
            count3 = len(signed_up3)
            signed_names = []
            for uid in signed_up3:
                m2 = resolved_guild.get_member(uid) if resolved_guild else None
                signed_names.append(m2.display_name if m2 else str(uid))
            roster_field_name = f"▸ Signed Up ({count3}/{total_capacity3})"
            roster_field_value = "\n".join(f"• {n}" for n in signed_names) or "—"

            signup_channel_id = pkg3.get("signup_channel_id")
            signup_message_id = pkg3.get("signup_message_id")
            if signup_channel_id and signup_message_id and resolved_guild:
                ch = await _resolve_channel(resolved_guild, int(signup_channel_id))
                if ch:
                    msg = await ch.fetch_message(int(signup_message_id))
                    if msg.embeds:
                        upd_embed = msg.embeds[0]
                        new_fields = [f for f in upd_embed.fields if not f.name.startswith("▸ Signed Up")]
                        upd_embed.clear_fields()
                        for f in new_fields:
                            upd_embed.add_field(name=f.name, value=f.value, inline=f.inline)
                        upd_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
                        await msg.edit(embed=upd_embed)
        except Exception as e:
            _g.logger.debug(f"[TP] Stand Down embed update failed for {self.package_id}: {e}")


class SpecialistAssignView(discord.ui.View):
    """View for cadre leaders to assign a specialist to a package."""

    def __init__(self, package_id: str, required_roles: list, guild: discord.Guild):
        super().__init__(timeout=600)
        self.package_id = package_id
        self.required_roles = required_roles

        # Build filtered member list: only members who hold a CADRE SPECIALIST role
        # (line roles like Watch Veteran / Oathsworn sign up via Comply, not here)
        # Also excludes specialists already locked on another active package.
        cadre_roles_needed = [r for r in required_roles if r in _CADRE_SPECIALIST_ROLES]

        # Collect IDs already locked on an active package (excluding this one)
        _tp_data = _load_tp()
        _active_statuses = {STATUS_RECRUITING, STATUS_DEPLOYED}
        already_assigned: set = set()
        for _p in _tp_data.get("packages", {}).values():
            if _p["id"] == package_id:
                continue
            if _p["status"] in _active_statuses:
                already_assigned.update(_p.get("assigned_specialist_ids", []))

        options = []
        seen = set()
        for role_name in cadre_roles_needed:
            for m in (guild.members if guild else []):
                if m.bot or m.id in seen:
                    continue
                if m.id in already_assigned:
                    continue  # already on another package
                if any((getattr(r, "name", "") or "").strip() == role_name for r in getattr(m, "roles", [])):
                    options.append(discord.SelectOption(
                        label=m.display_name[:100],
                        value=str(m.id),
                        description=role_name[:100],
                    ))
                    seen.add(m.id)

        if options:
            select = discord.ui.Select(
                placeholder="Select specialist to attach…",
                options=options[:25],
                custom_id="tp_specialist_select",
            )
            select.callback = self.on_select
            self.add_item(select)
        else:
            # Fallback if no members found with the required role
            select = discord.ui.UserSelect(
                placeholder="Select specialist to attach…",
                custom_id="tp_specialist_select",
            )
            select.callback = self.on_select_user
            self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        cadre_leader = interaction.user
        member_id = int(interaction.data["values"][0])
        specialist_member = interaction.guild.get_member(member_id)
        if not specialist_member:
            await interaction.response.send_message("Could not resolve member.", ephemeral=True)
            return
        success, msg = await assign_specialist(self.package_id, specialist_member, cadre_leader, interaction.guild)
        await interaction.response.send_message(msg, ephemeral=True)

    async def on_select_user(self, interaction: discord.Interaction):
        cadre_leader = interaction.user
        specialist = interaction.data.get("resolved", {}).get("members", {})
        if not specialist:
            await interaction.response.send_message("No member selected.", ephemeral=True)
            return
        member_id = next(iter(specialist))
        specialist_member = interaction.guild.get_member(int(member_id))
        if not specialist_member:
            await interaction.response.send_message("Could not resolve member.", ephemeral=True)
            return
        specialist_roles = _member_role_names(specialist_member)
        owned = any(_cadre_leader_owns(cadre_leader, r) for r in specialist_roles)
        if not owned and not _is_admin(cadre_leader):
            await interaction.response.send_message(
                f"{specialist_member.display_name} is not in your cadre.", ephemeral=True
            )
            return
        success, msg = await assign_specialist(self.package_id, specialist_member, cadre_leader, interaction.guild)
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Captain KT assignment button (inside PackagePaginatorView)
# ---------------------------------------------------------------------------

class AssignToKTView(discord.ui.View):
    """View shown after captain clicks Assign to KT — filtered to their company's KTs."""

    def __init__(self, package_id: str, member: discord.Member, guild: discord.Guild):
        super().__init__(timeout=300)
        self.package_id = package_id

        from .roster_ops import _get_member_company_name
        from .roster_embeds import _get_kill_teams_for_company
        company = _get_member_company_name(member)

        options = []
        if _is_debug_mode() or not company:
            # Debug: show full KT list; no company role: fall through to placeholder
            pass
        elif guild:
            kt_list = _get_kill_teams_for_company(guild, company)
            options = [
                discord.SelectOption(label=kt_name, value=kt_name)
                for kt_name, _, __ in kt_list
            ]

        # Debug or company KT lookup failed: fall back to full KILL_TEAMS list
        if not options and (_is_debug_mode() or company):
            kill_teams = list(_b("KILL_TEAMS") or [])
            options = [discord.SelectOption(label=kt, value=kt) for kt in kill_teams[:25]]

        if options:
            select = discord.ui.Select(
                placeholder="Select Kill Team…",
                options=options[:25],
                custom_id="tp_kt_role_select",
            )
            select.callback = self.on_select
            self.add_item(select)
        elif not company:
            # No company role — add a disabled placeholder
            select = discord.ui.Select(
                placeholder="No company role assigned",
                options=[discord.SelectOption(label="—", value="none")],
                custom_id="tp_kt_role_select",
                disabled=True,
            )
            self.add_item(select)
        else:
            # Has company but no KTs found — fall back to full list
            kill_teams = list(_b("KILL_TEAMS") or [])
            if kill_teams:
                select = discord.ui.Select(
                    placeholder="Select Kill Team…",
                    options=[discord.SelectOption(label=kt, value=kt) for kt in kill_teams[:25]],
                    custom_id="tp_kt_role_select",
                )
                select.callback = self.on_select
                self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        kt_name = interaction.data["values"][0]
        if kt_name == "none":
            await interaction.response.send_message("You do not have a company role and cannot assign packages.", ephemeral=True)
            return
        member = interaction.user
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member) or ("Debug" if _is_debug_mode() else None)
        success, msg = await assign_package_to_kt(
            self.package_id, kt_name, company, member, interaction.guild
        )
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Status board view (with select menu drill-down)
# ---------------------------------------------------------------------------

class StatusBoardView(discord.ui.View):
    """Status board with a select menu to drill into any package's full detail."""

    def __init__(self, packages: list, rep: float):
        super().__init__(timeout=600)
        self.packages = packages
        self.rep = rep

        # Build select menu options (max 25)
        options = []
        for p in packages[:25]:
            mode_short = "HS" if "Hard" in p.get("mode", "") else "Ω"
            status_short = p["status"].upper()[:12]
            kt = p.get("assigned_kt", "")
            label = p["id"]
            desc = f"{mode_short} · {status_short}" + (f" → {kt}" if kt else "")
            options.append(discord.SelectOption(label=label, description=desc[:100], value=p["id"]))

        if options:
            select = discord.ui.Select(
                placeholder="Select a package to view details…",
                options=options,
                custom_id="tp_board_select",
            )
            select.callback = self.on_select
            self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        package_id = interaction.data["values"][0]
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            await interaction.response.send_message(f"Package `{package_id}` not found.", ephemeral=True)
            return
        embed = _build_package_embed(pkg, data.get("rep", 0.0), viewer=interaction.user)
        specialist_ids = pkg.get("assigned_specialist_ids", [])
        if specialist_ids and interaction.guild:
            names = []
            for sid in specialist_ids:
                m = interaction.guild.get_member(sid)
                names.append(m.display_name if m else str(sid))
            embed.add_field(name="Attached Specialists", value=", ".join(names), inline=False)
        await interaction.response.send_message(embed=embed, **_file_kwarg(_classification_file(pkg)), ephemeral=True)


# ---------------------------------------------------------------------------
# Pagination view
# ---------------------------------------------------------------------------

class PackagePaginatorView(discord.ui.View):
    def __init__(self, packages: list, rep: float, show_distribute: bool = False, viewer: Optional[discord.Member] = None):
        super().__init__(timeout=600)
        self.packages = packages
        self.rep = rep
        self.index = 0
        self.show_distribute = show_distribute
        self.viewer = viewer
        self.selected_kt: str | None = None

        if show_distribute:
            distribute_btn = discord.ui.Button(
                label="Distribute All",
                style=discord.ButtonStyle.danger,
                custom_id="tp_distribute_all",
            )
            distribute_btn.callback = self.distribute_all
            self.add_item(distribute_btn)

        # Captain: inline KT select + green Assign button
        if not show_distribute and viewer and (_has_role(viewer, "Watch Captain") or _has_role(viewer, "Watch Lieutenant") or _is_admin(viewer)):
            kt_options = self._build_kt_options(viewer)
            if kt_options:
                kt_select = discord.ui.Select(
                    placeholder="Select Kill Team…",
                    options=kt_options,
                    custom_id="tp_kt_select_inline",
                )
                kt_select.callback = self.on_kt_select
                self.add_item(kt_select)
            assign_btn = discord.ui.Button(
                label="Assign",
                style=discord.ButtonStyle.success,
                custom_id="tp_assign_kt",
                disabled=True,  # enabled once a KT is selected
            )
            assign_btn.callback = self.assign_to_kt
            self.add_item(assign_btn)

        # Cadre leader: "Assign Specialist" button — only on cadre views, not the WM request board
        if not show_distribute and viewer and (_member_role_names(viewer) & _CADRE_LEADER_ROLES):
            spec_btn = discord.ui.Button(
                label="Assign Specialist",
                style=discord.ButtonStyle.secondary,
                custom_id="tp_assign_specialist",
            )
            spec_btn.callback = self.assign_specialist_btn
            self.add_item(spec_btn)

        # Select menu for quick navigation (max 25)
        if len(packages) > 1:
            options = []
            for p in packages[:25]:
                mode_short = "HS" if "Hard" in p.get("mode", "") else "Ω"
                status_short = p["status"].upper()[:12]
                kt = p.get("assigned_kt", "")
                desc = f"{mode_short} · {status_short}" + (f" → {kt}" if kt else "")
                options.append(discord.SelectOption(label=p["id"], description=desc[:100], value=p["id"]))
            select = discord.ui.Select(
                placeholder="Jump to package…",
                options=options,
                custom_id="tp_paginator_select",
            )
            select.callback = self.on_select
            self.add_item(select)

        # Set initial disabled state for specialist button
        if not show_distribute:
            self._refresh_specialist_btn()
            self._refresh_assign_btn()

    def _refresh_current_package_snapshot(self) -> dict:
        """Refresh current package from datastore so status changes show live."""
        if not self.packages:
            return {}
        idx = max(0, min(self.index, len(self.packages) - 1))
        pkg = self.packages[idx]
        pid = pkg.get("id")
        if not pid:
            return pkg
        latest = (_load_tp().get("packages", {}) or {}).get(pid)
        if latest:
            self.packages[idx] = latest
            return latest
        return pkg

    async def on_select(self, interaction: discord.Interaction):
        pid = interaction.data["values"][0]
        self.index = next((i for i, p in enumerate(self.packages) if p["id"] == pid), self.index)
        self._refresh_specialist_btn()
        self._refresh_assign_btn()
        await interaction.response.defer()
        f = self.current_file()
        await interaction.edit_original_response(
            embed=self.current_embed(), view=self,
            attachments=[f] if f else [],
        )

    def _build_kt_options(self, viewer: discord.Member) -> list:
        """Build KT select options filtered to the captain's company, excluding KTs at capacity."""
        from .roster_ops import _get_member_company_name
        try:
            from .roster_embeds import _get_kill_teams_for_company
        except Exception:
            _get_kill_teams_for_company = None
        company = _get_member_company_name(viewer)

        # Determine which KTs already have 3 active packages (capped)
        tp_data = _load_tp()
        active_statuses = {STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED}
        kt_active_counts: dict[str, int] = {}
        for p in tp_data.get("packages", {}).values():
            kt = p.get("assigned_kt")
            if kt and p["status"] in active_statuses:
                kt_active_counts[kt] = kt_active_counts.get(kt, 0) + 1
        at_capacity = {kt for kt, cnt in kt_active_counts.items() if cnt >= 3}

        options = []
        if _is_debug_mode() or not company or not _get_kill_teams_for_company:
            kill_teams = list(_b("KILL_TEAMS") or [])
            options = [
                discord.SelectOption(label=kt, value=kt)
                for kt in kill_teams
                if kt not in at_capacity
            ]
        else:
            guild = _get_guild_from_bot()
            if guild:
                kt_list = _get_kill_teams_for_company(guild, company)
                options = [
                    discord.SelectOption(label=kt_name, value=kt_name)
                    for kt_name, _, __ in kt_list
                    if kt_name not in at_capacity
                ]
            if not options:
                kill_teams = list(_b("KILL_TEAMS") or [])
                options = [
                    discord.SelectOption(label=kt, value=kt)
                    for kt in kill_teams
                    if kt not in at_capacity
                ]
        return options[:25]

    def _sync_kt_select_state(self) -> None:
        """Keep KT select UI aligned with current selection."""
        for item in self.children:
            if getattr(item, "custom_id", None) != "tp_kt_select_inline":
                continue
            options = getattr(item, "options", None)
            if not options:
                continue

            has_selected = False
            for opt in options:
                opt.default = bool(self.selected_kt and opt.value == self.selected_kt)
                if opt.default:
                    has_selected = True

            if self.selected_kt and not has_selected:
                self.selected_kt = None

            item.placeholder = f"Kill Team: {self.selected_kt}" if self.selected_kt else "Select Kill Team…"
            break

    async def on_kt_select(self, interaction: discord.Interaction):
        self.selected_kt = interaction.data["values"][0]
        self._refresh_assign_btn()
        await interaction.response.edit_message(view=self)

    async def assign_to_kt(self, interaction: discord.Interaction):
        pkg = self._refresh_current_package_snapshot()
        if pkg["status"] != STATUS_DISTRIBUTED:
            await interaction.response.send_message(
                f"Package `{pkg['id']}` is `{pkg['status']}` — cannot assign.", ephemeral=True
            )
            return
        if not self.selected_kt:
            await interaction.response.send_message("Select a Kill Team first.", ephemeral=True)
            return
        member = interaction.user
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member) or ("Debug" if _is_debug_mode() else None)
        success, msg = await assign_package_to_kt(
            pkg["id"], self.selected_kt, company, member, interaction.guild or _get_guild_from_bot()
        )
        if not success:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await interaction.response.defer()
        assigned_pid = pkg["id"]
        self.selected_kt = None

        # Live-update the current captain/LT paginator view so assigned package disappears.
        # For captain/LT views, once assigned it moves to pending_sgt and should no longer appear.
        if self.viewer and _is_captain_or_lt(self.viewer):
            self.packages = [p for p in self.packages if p.get("id") != assigned_pid]
            if not self.packages:
                await interaction.edit_original_response(
                    content="No active target packages for your role.",
                    embed=None,
                    view=None,
                    attachments=[],
                )
                return
            self.index = min(self.index, len(self.packages) - 1)

        self._refresh_assign_btn()
        self._refresh_specialist_btn()
        f = self.current_file()
        await interaction.edit_original_response(
            embed=self.current_embed(),
            view=self,
            attachments=[f] if f else [],
        )

    async def assign_specialist_btn(self, interaction: discord.Interaction):
        pkg = self._refresh_current_package_snapshot()
        if pkg["status"] not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            await interaction.response.send_message(
                f"Package `{pkg['id']}` is `{pkg['status']}` — cannot assign specialist.", ephemeral=True
            )
            return
        req_roles = pkg.get("required_roles", [])
        cadre_roles = [r for r in req_roles if r in _CADRE_SPECIALIST_ROLES]
        if not cadre_roles:
            await interaction.response.send_message("This package has no cadre specialist requirements.", ephemeral=True)
            return
        view = SpecialistAssignView(package_id=pkg["id"], required_roles=cadre_roles, guild=interaction.guild)
        await interaction.response.send_message(
            f"Select the specialist to attach to `{pkg['id']}`:", view=view, ephemeral=True
        )

    def current_embed(self) -> discord.Embed:
        self._refresh_current_package_snapshot()
        return _build_package_embed(
            self.packages[self.index],
            self.rep,
            index=self.index + 1,
            total=len(self.packages),
            viewer=self.viewer,
        )

    def current_file(self) -> "discord.File | None":
        self._refresh_current_package_snapshot()
        return _classification_file(self.packages[self.index])

    def _refresh_specialist_btn(self) -> None:
        """Disable Assign Specialist button if current package has no required specialist roles or wrong status."""
        pkg = self._refresh_current_package_snapshot()
        needs = (
            bool(set(pkg.get("required_roles", [])) & _CADRE_SPECIALIST_ROLES)
            and pkg.get("status") in (STATUS_RECRUITING, STATUS_DEPLOYED)
        )
        for item in self.children:
            if getattr(item, "custom_id", None) == "tp_assign_specialist":
                item.disabled = not needs
                break

    def _refresh_assign_btn(self) -> None:
        """Disable Assign button when package not DISTRIBUTED or no KT selected."""
        pkg = self._refresh_current_package_snapshot()
        can_assign = pkg.get("status") == STATUS_DISTRIBUTED and bool(self.selected_kt)
        for item in self.children:
            if getattr(item, "custom_id", None) == "tp_assign_kt":
                item.disabled = not can_assign
                break
        # Also reset selection when navigating away from a DISTRIBUTED package
        if pkg.get("status") != STATUS_DISTRIBUTED:
            self.selected_kt = None
        self._sync_kt_select_state()

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.packages)
        self._refresh_specialist_btn()
        self._refresh_assign_btn()
        await interaction.response.defer()
        f = self.current_file()
        await interaction.edit_original_response(
            embed=self.current_embed(), view=self,
            attachments=[f] if f else [],
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.packages)
        self._refresh_specialist_btn()
        self._refresh_assign_btn()
        await interaction.response.defer()
        f = self.current_file()
        await interaction.edit_original_response(
            embed=self.current_embed(), view=self,
            attachments=[f] if f else [],
        )

    async def distribute_all(self, interaction: discord.Interaction):
        guild = interaction.guild
        ids = [p["id"] for p in self.packages]
        await interaction.response.defer(ephemeral=True)
        await distribute_packages(ids, guild, actor=interaction.user)

        # Best-effort close of the ephemeral panel after successful distribution.
        # Depending on Discord interaction type/client, deletion may not be allowed.
        try:
            if interaction.message is not None:
                await interaction.message.delete()
                return
        except Exception:
            pass
        try:
            await interaction.delete_original_response()
            return
        except Exception:
            pass

        # Disable the distribute button after use
        for item in self.children:
            if getattr(item, "custom_id", None) == "tp_distribute_all":
                item.disabled = True
                item.label = "Distributed ✓"
        await interaction.edit_original_response(
            content=f"**{len(ids)} package{'s' if len(ids) != 1 else ''} distributed to Watch Captains.**",
            embed=self.current_embed(),
            view=self,
        )


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

def _is_admin(member: discord.Member) -> bool:
    """Return True if member is in admin_user_ids config."""
    admin_ids = set(str(x) for x in ((_b("CONFIG") or {}).get("admin_user_ids") or []))
    return str(getattr(member, "id", None)) in admin_ids


def _is_debug_mode() -> bool:
    return bool(_b("DEBUG_MODE"))


def _has_role(member: discord.Member, role_name: str) -> bool:
    return any((getattr(r, "name", "") or "").strip() == role_name for r in getattr(member, "roles", []))


def _is_watch_master(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    return _has_role(member, "Watch Master")


def _is_captain_or_lt(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    return _has_role(member, "Watch Captain") or _has_role(member, "Watch Lieutenant")


_CADRE_LEADER_ROLES = {
    "Lord Executioner", "Forgemaster", "Chief Apothecary",
    "High Chaplain", "Void Warden", "Castellan",
}

_HC_ROLES = {
    "Watch Master", "Lord Executioner", "Forgemaster", "Chief Apothecary",
    "High Chaplain", "Huntmaster", "Void Warden", "Castellan",
    "Venerable Dreadnought",
}
_COMMAND_ROLES = {"Watch Captain", "Watch Lieutenant"} | _CADRE_LEADER_ROLES
_KT_COMMAND_ROLES = {"Watch Sergeant", "Kill Team Champion"}


def _is_cadre_leader(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    roles = _member_role_names(member)
    return bool(roles.intersection(_CADRE_LEADER_ROLES))


def _cadre_leader_owns(cadre_leader: discord.Member, specialist_role: str) -> bool:
    """Return True if the cadre leader has authority over the given specialist role.
    
    Cadre leaders can assign themselves only if they personally hold the required role.
    Forgemaster manages Dreadnoughts administratively but cannot self-assign as one.
    """
    _CADRE_OWNERSHIP = {
        "Lord Executioner": {"Kill Team Champion", "Company Champion"},
        "Huntmaster": {"Huntmaster"},
        "Forgemaster": {"Watch Techmarine", "Venerable Dreadnought", "Honored Dreadnought"},
        "Chief Apothecary": {"Watch Apothecary"},
        "High Chaplain": {"Watch Chaplain"},
        "Void Warden": {"Watch Librarian"},
        "Castellan": {"Watch Keeper"},
        "Venerable Dreadnought": {"Honored Dreadnought", "Venerable Dreadnought"},
    }
    cl_roles = _member_role_names(cadre_leader)
    for cl_role, owned in _CADRE_OWNERSHIP.items():
        if cl_role in cl_roles and specialist_role in owned:
            return True
    return False


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

_bot_tree = None


def _get_tree():
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, "tree", None) if m else None


# /request_target_packages — WM only
@app_commands.command(
    name="request_target_packages",
    description="[Watch Master] Request a new batch of Ordo Xenos target packages.",
)
async def request_target_packages(interaction: discord.Interaction):
    if not _b("check_command_permission")(interaction.user, "request_target_packages"):
        await interaction.response.send_message(
            "Only the Watch Master may request target packages.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    data = _load_tp()
    rep = data.get("rep", 0.0)

    # If there are already unassigned packages, re-show them instead of generating new ones
    pending = [
        p for p in data["packages"].values()
        if p["status"] == STATUS_UNASSIGNED
    ]
    if pending:
        view = PackagePaginatorView(pending, rep, show_distribute=True, viewer=interaction.user)
        _pf = view.current_file()
        await interaction.followup.send(
            content=f"**{len(pending)} unassigned package{'s' if len(pending) != 1 else ''} already pending distribution.** "
                    f"Review and press **Distribute All** when ready. "
                    f"To generate a fresh batch instead, distribute or let these lapse first.",
            embed=view.current_embed(),
            view=view,
            ephemeral=True,
            **_file_kwarg(_pf),
        )
        return

    packages = await generate_packages(guild, actor=interaction.user)
    data = _load_tp()
    rep = data.get("rep", 0.0)

    if not packages:
        await interaction.followup.send("No active Kill Teams found — cannot generate packages.", ephemeral=True)
        return

    view = PackagePaginatorView(packages, rep, show_distribute=True, viewer=interaction.user)
    _pf = view.current_file()
    await interaction.followup.send(
        content=f"**{len(packages)} target package{'s' if len(packages) != 1 else ''} received from Ordo Xenos.** "
                f"Review below and press **Distribute All** when ready.",
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
        **_file_kwarg(_pf),
    )


# /view_target_packages — role-overloaded view
@app_commands.command(
    name="view_target_packages",
    description="View Ordo Xenos target packages relevant to your role.",
)
async def view_target_packages(interaction: discord.Interaction):
    member = interaction.user
    await interaction.response.defer(ephemeral=True)
    data = _load_tp()
    rep = data.get("rep", 0.0)
    packages = data.get("packages", {})

    def _active(statuses=None):
        return [
            p for p in packages.values()
            if p["status"] not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
            and (statuses is None or p["status"] in statuses)
        ]

    # Watch Master / admin — all active packages
    if _is_watch_master(member):
        pkgs = _active()

    # Captain / Lieutenant — distributed (awaiting assignment) + company packages
    # already in-flight (recruiting/deployed for tracking); exclude pending_sgt since
    # the captain already acted and the Sgt needs to accept.
    elif _is_captain_or_lt(member):
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member)
        pkgs = [
            p for p in _active()
            if p["status"] == STATUS_DISTRIBUTED
            or (
                p.get("assigned_company") == company
                and p["status"] in (STATUS_RECRUITING, STATUS_DEPLOYED)
            )
        ]

    # Cadre leader — packages needing their cadre's specialists
    elif _is_cadre_leader(member):
        pkgs = [
            p for p in _active([STATUS_RECRUITING, STATUS_DEPLOYED])
            if any(_cadre_leader_owns(member, r) for r in p.get("required_roles", []))
        ]

    # Everyone else — packages assigned to their KT
    else:
        from .forge_ops import _resolve_killteam_for_member
        kt = _resolve_killteam_for_member(member)
        pkgs = [
            p for p in _active()
            if p.get("assigned_kt") == kt
        ] if kt else []

    if not pkgs:
        await interaction.followup.send("No active target packages for your role.", ephemeral=True)
        return

    view = PackagePaginatorView(pkgs, rep, show_distribute=False, viewer=member)
    _pf = view.current_file()
    await interaction.followup.send(
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
        **_file_kwarg(_pf),
    )


# /log_strike_report
async def _submit_target_package_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list:
    """Autocomplete package IDs for packages the current user is attached to."""
    data = _load_tp()
    packages = data.get("packages", {})
    member = interaction.user
    current_norm = (current or "").strip().upper()

    choices: list = []
    for pkg in packages.values():
        if pkg.get("status") not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            continue

        signed_up = member.id in pkg.get("signed_up", [])
        specialist = member.id in pkg.get("assigned_specialist_ids", [])
        if not (signed_up or specialist):
            continue

        pid = str(pkg.get("id", "")).upper()
        if current_norm and current_norm not in pid:
            continue

        label_bits = [pid]
        kt = pkg.get("assigned_kt")
        if kt:
            label_bits.append(kt)
        if specialist:
            label_bits.append("specialist")
        elif signed_up:
            label_bits.append("signed up")

        choices.append(app_commands.Choice(name=" · ".join(label_bits)[:100], value=pid))
        if len(choices) >= 25:
            break

    return choices


_submit_target_package_autocomplete_decorator = (
    app_commands.autocomplete(package_id=_submit_target_package_autocomplete)
    if hasattr(app_commands, "autocomplete")
    else (lambda func: func)
)


@app_commands.command(
    name="log_strike_report",
    description="Log a completed Ordo Xenos strike report.",
)
@app_commands.describe(
    package_id="The target package ID (e.g. OX-A4B2C)",
    aar_link="Link to the After Action Report",
)
@_submit_target_package_autocomplete_decorator
async def log_strike_report(
    interaction: discord.Interaction,
    package_id: str,
    aar_link: str,
):
    package_id = package_id.strip().upper()
    success, msg = await submit_package(package_id, aar_link, interaction.user, interaction.guild)
    if success:
        data = _load_tp()
        pkg = data.get("packages", {}).get(package_id, {})
        classification = str(pkg.get("classification") or "STRIKE").strip().title()
        completed_kt = str(pkg.get("assigned_kt") or "Unassigned")
        rep_before = float(pkg.get("rep_before", data.get("rep", 0.0)) or 0.0)
        rep_after = float(pkg.get("rep_after", data.get("rep", 0.0)) or 0.0)
        standing_before = _standing_skull_bar(rep_before)
        standing_after = _standing_skull_bar(rep_after)
        state_before = _standing_state_name(rep_before)
        state_after = _standing_state_name(rep_after)

        embed = discord.Embed(
            title=f"{_DW_EMOJI} sᴛʀɪᴋᴇ ʀᴇᴘᴏʀᴛ ʟᴏɢɢᴇᴅ {_DW_EMOJI}",
            description=msg,
            color=0x2ECC71,
        )
        embed.add_field(name=f"▸ {classification} Package", value=f"`{package_id}`", inline=True)
        embed.add_field(name="▸ Kill Team Completed", value=completed_kt, inline=True)
        embed.add_field(
            name="▸ Ordo Xenos Standing",
            value=(
                f"{standing_before} **{state_before}** `{rep_before:+.2f}`\n"
                f"-> {standing_after} **{state_after}** `{rep_after:+.2f}`"
            ),
            inline=False,
        )
        embed.add_field(name="▸ AAR", value=aar_link, inline=False)

        report_header = "**```++ 𝐒𝐓𝐑𝐈𝐊𝐄 𝐑𝐄𝐏𝐎𝐑𝐓 ++```**"
        report_footer = "**```++ 𝐄𝐍𝐃 𝐎𝐅 𝐑𝐄𝐏𝐎𝐑𝐓 ++```**"
        completion_img = os.path.join(_ASSETS_DIR, "Mission_Complete.png")
        if os.path.exists(completion_img):
            comp_file = discord.File(completion_img, filename="mission_complet.png")
            embed.set_image(url="attachment://mission_complet.png")
            await interaction.response.send_message(content=report_header, embed=embed, file=comp_file, ephemeral=False)
        else:
            await interaction.response.send_message(content=report_header, embed=embed, ephemeral=False)
        await interaction.followup.send(report_footer, ephemeral=False)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# Backward-compatible Python alias for older imports/call sites.
submit_target_package = log_strike_report


# /target_package_status
@app_commands.command(
    name="target_package_status",
    description="View the full status of a specific target package.",
)
@app_commands.describe(package_id="The target package ID (e.g. OX-A4B2C)")
async def target_package_status(
    interaction: discord.Interaction,
    package_id: str,
):
    member = interaction.user
    package_id = package_id.strip().upper()

    can_view = (
        _is_admin(member)
        or _is_watch_master(member)
        or _is_captain_or_lt(member)
        or _is_cadre_leader(member)
    )

    data = _load_tp()
    pkg = data["packages"].get(package_id)
    if not pkg:
        await interaction.response.send_message(f"Package `{package_id}` not found.", ephemeral=True)
        return

    # Non-command members can only view their own KT's packages
    if not can_view:
        from .forge_ops import _resolve_killteam_for_member
        kt = _resolve_killteam_for_member(member)
        if pkg.get("assigned_kt") != kt:
            await interaction.response.send_message(
                f"Package `{package_id}` is not assigned to your Kill Team.", ephemeral=True
            )
            return

    embed = _build_package_embed(pkg, data.get("rep", 0.0), viewer=interaction.user)
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    if specialist_ids:
        names = []
        for sid in specialist_ids:
            m = interaction.guild.get_member(sid)
            names.append(m.display_name if m else str(sid))
        embed.add_field(name="Attached Specialists", value=", ".join(names), inline=False)

    await interaction.response.send_message(embed=embed, **_file_kwarg(_classification_file(pkg)), ephemeral=True)


# ---------------------------------------------------------------------------
# Register commands + expiry loop
# ---------------------------------------------------------------------------

def _register_commands(tree: app_commands.CommandTree) -> None:
    for cmd in (
        request_target_packages,
        view_target_packages,
        log_strike_report,
        target_package_status,
    ):
        if tree.get_command(cmd.name) is None:
            tree.add_command(cmd)



@_tasks.loop(minutes=30)
async def _tp_expiry_loop():
    """Periodically expire overdue packages."""
    try:
        m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
        bot = getattr(m, "bot", None) if m else None
        if not bot:
            return
        guild_id = _b("CONFIG") and (_b("CONFIG") or {}).get("guild_id")
        if not guild_id:
            for guild in bot.guilds:
                await expire_packages(guild)
        else:
            guild = bot.get_guild(int(guild_id))
            if guild:
                await expire_packages(guild)
    except Exception as e:
        _g.logger.error(f"[TP] Expiry loop error: {e}")



async def register_persistent_views() -> None:
    """Call from on_ready to restore TP persistent views after a bot restart.

    Each active package's SgtAcceptView and SignUpView are re-registered scoped
    to their original message IDs so instance state (package_id / kt_name) is
    correctly restored.
    """
    try:
        data = _load_tp()
        sgt_count = 0
        signup_count = 0
        for package_id, pkg in data.get("packages", {}).items():
            status = pkg.get("status")
            kt_name = pkg.get("assigned_kt", "")

            if status == STATUS_PENDING_SGT:
                msg_id = pkg.get("sgt_accept_message_id")
                if msg_id:
                    _g.bot.add_view(
                        SgtAcceptView(package_id=package_id, kt_name=kt_name),
                        message_id=msg_id,
                    )
                    sgt_count += 1

            if status in (STATUS_RECRUITING, STATUS_DEPLOYED):
                msg_id = pkg.get("signup_message_id")
                if msg_id:
                    _g.bot.add_view(
                        SignUpView(package_id=package_id),
                        message_id=msg_id,
                    )
                    signup_count += 1

        if _g.logger:
            _g.logger.info(
                f"target_packages_ops: registered {sgt_count} SgtAccept + "
                f"{signup_count} SignUp persistent views"
            )
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"target_packages_ops: register_persistent_views failed: {exc}")


# Public exports
__all__ = [
    "request_target_packages",
    "view_target_packages",
    "log_strike_report",
    "submit_target_package",
    "target_package_status",
    "_register_commands",
    "_tp_expiry_loop",
    "generate_packages",
    "distribute_packages",
    "expire_packages",
    "register_persistent_views",
]
