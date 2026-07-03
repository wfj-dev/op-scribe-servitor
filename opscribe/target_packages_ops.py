"""Ordo Xenos Strike Directives subsystem.

Strike directives issued by Ordo Xenos for Watch Fortress Jericho to complete.
Commands: /request_strike_directives, /view_strike_directives, /assign_directive,
          /log_strike_report, /strike_directive_status
"""

import os
import json
import random
import string
import asyncio
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import combinations
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
STRIKE_QUEUE_PATH = os.path.join(DATA_DIR, "strike_directive_queue.json")
_REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference")

_TP_LOCK = asyncio.Lock()
_STRIKE_QUEUE_LOCK = asyncio.Lock()
_STRIKE_QUEUE_MATCH_LOCK = asyncio.Lock()
_STRIKE_QUEUE_COMBINATION_CANDIDATE_LIMIT = 12


def _get_guild_from_bot() -> "discord.Guild | None":
    """Resolve the configured guild from the bot. Used when interaction.guild is None (DM context)."""
    bot = getattr(_g, "bot", None) or _b("bot")
    if not bot:
        return None
    guild_id = (_b("CONFIG") or {}).get("guild_id")
    if guild_id:
        return bot.get_guild(int(guild_id))
    return next(iter(bot.guilds), None)


def _tp_get_player_platform(member: discord.Member) -> Optional[str]:
    """Resolve member platform as "pc" or "console" using LFG role semantics."""
    platform_fn = _b("_get_player_platform")
    if callable(platform_fn):
        try:
            platform = platform_fn(member)
            if platform in ("pc", "console"):
                return platform
        except Exception:
            pass

    cfg = (_b("CONFIG") or {}).get("lfg", {})
    pc_role_id = int(cfg.get("pc_player_role_id") or LFG_PC_PLAYER_ROLE_ID_DEFAULT)
    console_role_id = int(cfg.get("console_player_role_id") or LFG_CONSOLE_PLAYER_ROLE_ID_DEFAULT)
    role_ids = {int(getattr(r, "id", 0) or 0) for r in getattr(member, "roles", [])}

    if pc_role_id in role_ids:
        return "pc"
    if console_role_id in role_ids:
        return "console"
    return None


def _tp_console_count(pkg: dict, guild: discord.Guild) -> int:
    """Count console players currently committed to a directive (signed + specialists)."""
    count = 0
    for uid in (pkg.get("signed_up", []) + pkg.get("assigned_specialist_ids", [])):
        m = guild.get_member(int(uid)) if guild else None
        if m and _tp_get_player_platform(m) == "console":
            count += 1
    return count


async def _resolve_channel(guild: "discord.Guild | None", channel_id: int):
    """Resolve a channel from cache, then API as fallback. Handles forum threads."""
    if not channel_id:
        return None

    ch = guild.get_channel(int(channel_id)) if guild else None
    if ch:
        return ch

    # Try thread cache (forum posts / active threads are not in get_channel)
    ch = guild.get_thread(int(channel_id)) if guild else None
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


def _normalize_company_key(company_name: str | None) -> str:
    return str(company_name or "").strip().lower()


def _normalize_company_label(company_name: str | None) -> str:
    """Normalize company labels for role/config matching.

    Examples:
      "Watch Company Primus" -> "primus"
      "primus" -> "primus"
    """
    raw = _normalize_company_key(company_name)
    if raw.startswith("watch company "):
        return raw.replace("watch company ", "", 1).strip()
    return raw


def _resolve_company_role_mention(guild: "discord.Guild | None", company_name: str | None) -> str:
    """Resolve assigned company role mention for directive notifications."""
    if guild is None:
        return ""

    company_label = _normalize_company_label(company_name)
    if not company_label:
        return ""

    companies_cfg = ((_b("CONFIG") or {}).get("companies") or {})
    if isinstance(companies_cfg, dict):
        for entry in companies_cfg.values():
            if not isinstance(entry, dict):
                continue
            entry_name = _normalize_company_label(entry.get("name"))
            if entry_name != company_label:
                continue
            try:
                role_id = int(entry.get("companyRoleId") or 0)
            except Exception:
                role_id = 0
            if role_id:
                role_obj = guild.get_role(role_id)
                if role_obj:
                    return role_obj.mention

    candidates = [
        str(company_name or "").strip(),
        f"Watch Company {company_label.capitalize()}",
        company_label.capitalize(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        role_obj = discord.utils.get(getattr(guild, "roles", []), name=candidate)
        if role_obj:
            return role_obj.mention

    return ""


def _directive_forum_parent_map() -> dict[str, int]:
    """Load directive forum parent routing from config.

    Expected config shape:
      CONFIG["target_packages"]["directive_forum_parent_by_company"] = {
        "Watch Company Primus": 123,
        "Watch Company Secundus": 456,
      }
    """
    raw = (((_b("CONFIG") or {}).get("target_packages") or {}).get("directive_forum_parent_by_company") or {})
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for company_name, parent_id in raw.items():
        key = _normalize_company_key(str(company_name))
        if not key:
            continue
        try:
            out[key] = int(parent_id)
        except Exception:
            _g.logger.warning(f"[TP] Invalid directive forum parent id for company '{company_name}': {parent_id}")
    return out


async def _resolve_directive_forum_parent(
    guild: "discord.Guild | None",
    company_name: str | None,
) -> "discord.ForumChannel | None":
    """Resolve directive forum parent by explicit company routing with fallback."""
    if guild is None:
        return None

    company_key = _normalize_company_key(company_name)
    parent_id = _directive_forum_parent_map().get(company_key)
    allowed_parent_ids = {int(pid) for pid in ((_b("ALLOWED_KT_FORUM_PARENT_IDS") or set())) if pid}

    if parent_id:
        channel = guild.get_channel(int(parent_id))
        if channel is None:
            channel = await _resolve_channel(guild, int(parent_id))
        if isinstance(channel, discord.ForumChannel):
            return channel
        _g.logger.warning(f"[TP] Routed forum parent {parent_id} for {company_name} is unavailable or not a forum channel")

    if company_key:
        _g.logger.warning(f"[TP] No explicit directive forum mapping for company '{company_name}', using fallback forum scan")

    for ch in getattr(guild, "channels", []):
        if isinstance(ch, discord.ForumChannel):
            if not allowed_parent_ids or int(ch.id) in allowed_parent_ids:
                return ch
    return None


async def _ensure_directive_forum_thread(
    package_id: str,
    guild: "discord.Guild | None",
    pkg: dict | None = None,
) -> "discord.Thread | None":
    """Create or resolve the directive forum thread and persist linkage on the package."""
    if guild is None:
        return None

    data = _load_tp()
    pkg_obj = pkg or data.get("packages", {}).get(package_id)
    if not pkg_obj:
        return None

    existing_thread_id = int(pkg_obj.get("forum_thread_id") or 0)
    if existing_thread_id:
        existing = await _resolve_channel(guild, existing_thread_id)
        if isinstance(existing, discord.Thread):
            return existing

    parent = await _resolve_directive_forum_parent(guild, pkg_obj.get("assigned_company"))
    if not isinstance(parent, discord.ForumChannel):
        _g.logger.warning(f"[TP] Could not resolve forum parent for directive {package_id}")
        return None

    thread_title = (pkg_obj.get("directive_name") or pkg_obj.get("directive_code") or package_id or "Strike Directive").strip()
    if len(thread_title) > 100:
        thread_title = thread_title[:100]

    code = pkg_obj.get("directive_code") or package_id
    opener = f"Strike Directive `{code}` thread initialized."

    try:
        created = await parent.create_thread(name=thread_title, content=opener)
    except Exception as exc:
        _g.logger.warning(f"[TP] Failed creating forum thread for directive {package_id}: {exc}")
        return None

    thread = getattr(created, "thread", None) or created
    if not isinstance(thread, discord.Thread):
        _g.logger.warning(f"[TP] Unexpected forum thread create result for directive {package_id}: {type(thread)}")
        return None

    async with _TP_LOCK:
        live = _load_tp()
        live_pkg = live.get("packages", {}).get(package_id)
        if live_pkg is None:
            return thread
        already_set = int(live_pkg.get("forum_thread_id") or 0)
        if already_set and already_set != int(thread.id):
            _g.logger.warning(
                f"[TP] Concurrent forum thread creation detected for directive {package_id}; "
                f"preferring existing thread {already_set} over newly created {thread.id}"
            )
            existing = await _resolve_channel(guild, already_set)
            if isinstance(existing, discord.Thread):
                return existing
        live_pkg["forum_thread_id"] = int(thread.id)
        live_pkg["forum_parent_id"] = int(getattr(parent, "id", 0) or 0)
        live_pkg["forum_created_at"] = datetime.now(timezone.utc).isoformat()
        _save_tp(live)

    return thread


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
        forum_thread_id = int(pkg.get("forum_thread_id") or 0)

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

    if forum_thread_id and guild:
        try:
            thread = await _resolve_channel(guild, forum_thread_id)
            if isinstance(thread, discord.Thread):
                await thread.delete()
                deleted += 1
        except (discord.NotFound, discord.Forbidden):
            pass
        except Exception as exc:
            _g.logger.debug(f"[TP] Failed deleting forum thread {forum_thread_id} for {package_id}: {exc}")

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
            pkg["forum_thread_id"] = None
            pkg["forum_parent_id"] = None
            pkg["forum_created_at"] = None
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
    """Attach strike directive metadata to the submitted AAR record, if present.

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

    # Apply +1 AAR point to everyone in this op for completing a strike directive.
    # Guarded by a flag so reconcile / re-ingest never double-counts.
    if not updated.get("strike_directive_bonus_applied"):
        updated["points_for_op"] = int(updated.get("points_for_op") or 0) + 1
        updated["strike_directive_bonus_applied"] = True

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
_REQ_TIER_VETERAN = "veteran"
_REQ_TIER_OATHSWORN = "oathsworn"
_REQ_TIER_KT_COMMAND = "kt_command"
_REQ_TIER_COMPANY_COMMAND = "company_command"
_REQ_TIER_HC = "hc"
_REQ_TIER_NO_REQ = "no_req"

# Role name sets per requirement tier
_TIER_ROLES = {
    _REQ_TIER_VETERAN: ["Watch Veteran"],
    _REQ_TIER_OATHSWORN: ["Oathsworn"],
    _REQ_TIER_KT_COMMAND: ["Watch Sergeant", "Bladeguard"],
    _REQ_TIER_COMPANY_COMMAND: [
        "Watch Captain", "Watch Lieutenant", "First Blade",
        "Watch Techmarine", "Watch Apothecary", "Watch Chaplain",
        "Watch Librarian", "Watch Keeper", "Honored Dreadnought",
    ],
    _REQ_TIER_HC: [
        "Watch Master", "Blademaster", "Forgemaster", "Chief Apothecary",
        "High Chaplain", "Huntmaster", "Void Warden", "Castellan",
        "Venerable Dreadnought",
    ],
}

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


def _strat_counts_for_rep_tier(rep_tier: int) -> tuple[int, int]:
    """Return (positive_count, negative_count) for a rep tier.

    Config supports either fixed counts per tier, or weighted distributions:

      CONFIG["target_packages"]["strat_modifier_counts_by_rep_tier"] = {
        "-3": {"positive": 1, "negative": 5},
        "0": {
          "distribution": [
            {"positive": 1, "negative": 2, "weight": 70},
            {"positive": 2, "negative": 2, "weight": 30}
          ]
        },
      }
    """
    default = _STRAT_TABLE.get(rep_tier, _STRAT_TABLE[0])

    cfg_tp = (_b("CONFIG") or {}).get("target_packages") or {}
    table_cfg = cfg_tp.get("strat_modifier_counts_by_rep_tier") or {}
    if not isinstance(table_cfg, dict):
        return default

    tier_cfg = table_cfg.get(str(rep_tier), table_cfg.get(rep_tier))
    if not isinstance(tier_cfg, dict):
        return default

    dist = tier_cfg.get("distribution")
    if isinstance(dist, list) and dist:
        parsed: list[tuple[int, int, int]] = []
        for idx, row in enumerate(dist):
            if not isinstance(row, dict):
                _g.logger.warning(
                    "[TP] Invalid distribution row for rep tier %s at index %s; using default %s",
                    rep_tier,
                    idx,
                    default,
                )
                return default
            try:
                pos = int(row.get("positive", row.get("pos")))
                neg = int(row.get("negative", row.get("neg")))
                weight = int(row.get("weight", 0))
            except Exception:
                _g.logger.warning(
                    "[TP] Invalid distribution values for rep tier %s at index %s; using default %s",
                    rep_tier,
                    idx,
                    default,
                )
                return default
            if pos < 0 or neg < 0 or weight <= 0:
                _g.logger.warning(
                    "[TP] Non-positive distribution values for rep tier %s at index %s; using default %s",
                    rep_tier,
                    idx,
                    default,
                )
                return default
            parsed.append((pos, neg, weight))

        choice = random.choices(parsed, weights=[w for _, _, w in parsed], k=1)[0]
        return choice[0], choice[1]

    try:
        pos = int(tier_cfg.get("positive", tier_cfg.get("pos")))
        neg = int(tier_cfg.get("negative", tier_cfg.get("neg")))
    except Exception:
        _g.logger.warning(
            "[TP] Invalid target_packages.strat_modifier_counts_by_rep_tier[%s]=%s; using default %s",
            rep_tier,
            tier_cfg,
            default,
        )
        return default

    if pos < 0 or neg < 0:
        _g.logger.warning(
            "[TP] Negative strat counts in target_packages.strat_modifier_counts_by_rep_tier[%s]=%s; using default %s",
            rep_tier,
            tier_cfg,
            default,
        )
        return default

    return pos, neg

_REP_MIN = 0.0
_REP_MAX = 60.0
_REP_NEUTRAL = 30.0
_REP_SCALE_VERSION = 2

# Strike directive volume by rep band.
# The multiplier stays integer-only; the band controls the draw weights.
_PACKAGE_MULTIPLIER_WEIGHTS = [
    (16.0, [65, 25, 10, 0]),
    (31.0, [40, 35, 20, 5]),
    (46.0, [20, 35, 30, 15]),
    (float("inf"), [10, 20, 35, 35]),
]

_GENERAL_WARNING_WINDOW = timedelta(hours=24)

# Generator switch: disable Omega packages temporarily when needed.
ENABLE_OMEGA_PACKAGES = True
_MODE_WEIGHTS_DEFAULT = {"Hard-Strat": 90, "Omega-Strat": 10}
_REQUIREMENT_NO_REQ_CHANCE_DEFAULT = 0.50
_REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT = {
    _REQ_TIER_VETERAN: 30,
    _REQ_TIER_OATHSWORN: 10,
    _REQ_TIER_KT_COMMAND: 40,
    _REQ_TIER_COMPANY_COMMAND: 20,
    _REQ_TIER_HC: 10,
}


def _requirement_no_req_chance() -> float:
    """Return no-requirement draw chance from config with safe default."""
    cfg_tp = (_b("CONFIG") or {}).get("target_packages") or {}
    req_cfg = cfg_tp.get("requirement_weights") or {}
    raw = req_cfg.get("no_requirement_chance", _REQUIREMENT_NO_REQ_CHANCE_DEFAULT)
    try:
        chance = float(raw)
    except Exception:
        _g.logger.warning(
            "[TP] Invalid target_packages.requirement_weights.no_requirement_chance (%s); using default %.2f",
            raw,
            _REQUIREMENT_NO_REQ_CHANCE_DEFAULT,
        )
        return _REQUIREMENT_NO_REQ_CHANCE_DEFAULT
    if chance < 0.0 or chance > 1.0:
        _g.logger.warning(
            "[TP] Out-of-range target_packages.requirement_weights.no_requirement_chance (%s); using default %.2f",
            raw,
            _REQUIREMENT_NO_REQ_CHANCE_DEFAULT,
        )
        return _REQUIREMENT_NO_REQ_CHANCE_DEFAULT
    return chance


def _requirement_slot_tier_weights() -> list[tuple[str, int]]:
    """Return per-slot requirement tier draw weights from config with defaults.

    Expected config shape:
      CONFIG["target_packages"]["requirement_weights"]["slot_tier"] = {
        "veteran": 30,
                "oathsworn": 10,
        "kt_command": 40,
        "company_command": 20,
        "hc": 10,
      }
    """
    cfg_tp = (_b("CONFIG") or {}).get("target_packages") or {}
    req_cfg = cfg_tp.get("requirement_weights") or {}
    raw = req_cfg.get("slot_tier") or {}

    if not isinstance(raw, dict):
        return list(_REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items())

    parsed: dict[str, int] = {}
    for tier_key, default_weight in _REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items():
        v = raw.get(tier_key, default_weight)
        try:
            parsed[tier_key] = int(v)
        except Exception:
            _g.logger.warning(
                "[TP] Invalid target_packages.requirement_weights.slot_tier[%s]=%s; using defaults %s",
                tier_key,
                v,
                _REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT,
            )
            return list(_REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items())

    if any(w < 0 for w in parsed.values()) or sum(parsed.values()) <= 0:
        _g.logger.warning(
            "[TP] Non-positive target_packages.requirement_weights.slot_tier (%s); using defaults %s",
            parsed,
            _REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT,
        )
        return list(_REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items())

    return [(tier, parsed[tier]) for tier in _REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.keys()]


def _mode_draw_weights() -> tuple[int, int]:
    """Return (hard_weight, omega_weight) from config with safe defaults.

    Expected config shape:
      CONFIG["target_packages"]["mode_weights"] = {
        "hard_strat": 90,
        "omega_strat": 10,
      }
    """
    cfg_tp = (_b("CONFIG") or {}).get("target_packages") or {}
    raw = cfg_tp.get("mode_weights") or {}
    if not isinstance(raw, dict):
        return _MODE_WEIGHTS_DEFAULT["Hard-Strat"], _MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    hard_raw = raw.get("hard_strat", raw.get("Hard-Strat", _MODE_WEIGHTS_DEFAULT["Hard-Strat"]))
    omega_raw = raw.get("omega_strat", raw.get("Omega-Strat", _MODE_WEIGHTS_DEFAULT["Omega-Strat"]))

    try:
        hard = int(hard_raw)
        omega = int(omega_raw)
    except Exception:
        _g.logger.warning(
            "[TP] Invalid target_packages.mode_weights config (%s); using defaults %s",
            raw,
            _MODE_WEIGHTS_DEFAULT,
        )
        return _MODE_WEIGHTS_DEFAULT["Hard-Strat"], _MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    if hard < 0 or omega < 0 or (hard + omega) <= 0:
        _g.logger.warning(
            "[TP] Non-positive target_packages.mode_weights config (%s); using defaults %s",
            raw,
            _MODE_WEIGHTS_DEFAULT,
        )
        return _MODE_WEIGHTS_DEFAULT["Hard-Strat"], _MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    return hard, omega

def _format_deadline_dual_region(deadline_iso: str) -> str:
    """Render a directive deadline using Discord local-time tags for each viewer."""
    if not deadline_iso:
        return "Deadline unavailable"
    try:
        deadline = datetime.fromisoformat(deadline_iso)
    except Exception:
        return deadline_iso

    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    unix_ts = int(deadline.timestamp())
    return f"<t:{unix_ts}:F> (<t:{unix_ts}:R>)"


def _batch_id_for_package(pkg: dict) -> str:
    explicit = pkg.get("batch_id")
    if explicit:
        return explicit
    gen_str = pkg.get("generated_at")
    if gen_str:
        try:
            gen = datetime.fromisoformat(gen_str)
            return f"BATCH-{gen.strftime('%Y%m%d')}"
        except Exception:
            pass
    return "BATCH-UNKNOWN"


_BATCH_ID_RE = re.compile(r"^BATCH-(\d{8})(?:-(\d+))?$")


def _batch_recency_key(batch_id: str) -> tuple[int, int]:
    """Return a sortable recency key for batch IDs.

    Higher values are newer batches. Unknown/malformed IDs sort oldest.
    """
    m = _BATCH_ID_RE.match(batch_id or "")
    if not m:
        return (0, 0)
    date_part = int(m.group(1))
    seq = int(m.group(2)) if m.group(2) else 0
    return (date_part, seq)


def _resolve_summary_batch_id(data: dict, requested_batch_id: str | None = None) -> str | None:
    """Resolve the target batch ID for cycle summary posting.

    Priority:
    1) Explicit requested batch.
    2) cycle.batch_id.
    3) Newest known batch from package records.
    4) BATCH-UNKNOWN if that is all that exists.
    """
    if requested_batch_id:
        return requested_batch_id

    cycle_batch_id = (data.get("cycle") or {}).get("batch_id")
    if cycle_batch_id:
        return cycle_batch_id

    packages = data.get("packages", {})
    if not packages:
        return None
    all_batch_ids = {_batch_id_for_package(p) for p in packages.values()}
    known_batch_ids = [bid for bid in all_batch_ids if bid != "BATCH-UNKNOWN"]
    if known_batch_ids:
        return max(known_batch_ids, key=_batch_recency_key)
    if all_batch_ids:
        return "BATCH-UNKNOWN"
    return None


def _should_post_batch_summary(data: dict, batch_id: str | None) -> bool:
    if not batch_id or batch_id == "BATCH-UNKNOWN":
        return False
    cycle = data.setdefault("cycle", {})
    if _batch_summary_posted_at(cycle, batch_id):
        return False
    return _is_batch_terminal(data, batch_id)


def _generate_unique_batch_id(data: dict, now: datetime) -> str:
    """Generate a unique same-day batch id as BATCH-YYYYMMDD-NN."""
    date_key = now.strftime("%Y%m%d")
    prefix = f"BATCH-{date_key}"
    seq = 1
    pattern = re.compile(rf"^{re.escape(prefix)}(?:-(\d+))?$")
    for pkg in data.get("packages", {}).values():
        bid = _batch_id_for_package(pkg)
        m = pattern.match(bid)
        if not m:
            continue
        n = int(m.group(1)) if m.group(1) else 1
        if n >= seq:
            seq = n + 1
    return f"{prefix}-{seq:02d}"


def _batch_warning_sent_at(cycle: dict, batch_id: str) -> str | None:
    sent_map = cycle.get("general_warning_sent_at", {})
    if isinstance(sent_map, dict):
        sent_at = sent_map.get(batch_id)
        if sent_at:
            return sent_at
    if cycle.get("last_general_warning_batch_id") == batch_id:
        return cycle.get("last_general_warning_at")
    return None


def _mark_batch_warning_sent(cycle: dict, batch_id: str, now: datetime) -> None:
    sent_map = cycle.setdefault("general_warning_sent_at", {})
    if not isinstance(sent_map, dict):
        sent_map = {}
        cycle["general_warning_sent_at"] = sent_map
    ts = now.isoformat()
    sent_map[batch_id] = ts
    # Legacy mirrors for compatibility.
    cycle["last_general_warning_batch_id"] = batch_id
    cycle["last_general_warning_at"] = ts


def _is_batch_terminal(data: dict, batch_id: str) -> bool:
    batch_pkgs = [p for p in data.get("packages", {}).values() if _batch_id_for_package(p) == batch_id]
    if not batch_pkgs:
        return False
    return all(
        p.get("status") in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
        for p in batch_pkgs
    )


def _batch_summary_posted_at(cycle: dict, batch_id: str) -> str | None:
    posted_map = cycle.get("batch_summary_posted_at", {})
    if not isinstance(posted_map, dict):
        return None
    return posted_map.get(batch_id)


def _mark_batch_summary_posted(cycle: dict, batch_id: str, now: datetime) -> None:
    posted_map = cycle.setdefault("batch_summary_posted_at", {})
    if not isinstance(posted_map, dict):
        posted_map = {}
        cycle["batch_summary_posted_at"] = posted_map
    posted_map[batch_id] = now.isoformat()


def _can_request_strike_directives(cycle: dict, now: datetime, max_per_week: int = 2) -> tuple[bool, str]:
    """Check if the WM can request strike directives based on weekly quota.
    
    Returns (can_request: bool, message: str) tuple.
    """
    if max_per_week <= 0:
        return True, ""

    timestamps = cycle.get("batch_generation_timestamps", [])
    if not isinstance(timestamps, list):
        timestamps = []

    # Determine whether `now` is timezone-aware so we can normalise comparisons.
    now_aware = now.tzinfo is not None

    # Filter to batches generated strictly within the past 7 days, parsing
    # timestamps defensively and normalising naive datetimes to UTC.
    week_ago = now - timedelta(days=7)
    recent_parsed: list[datetime] = []
    for ts in timestamps:
        if not ts or not isinstance(ts, str):
            continue
        try:
            parsed = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        # Normalise tzinfo so comparison with `now` is always valid.
        if now_aware and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        elif not now_aware and parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        # Use an exclusive boundary so a timestamp exactly 7 days old is pruned.
        if parsed > week_ago:
            recent_parsed.append(parsed)

    if len(recent_parsed) < max_per_week:
        return True, ""

    # Calculate when the oldest recent batch exits the 7-day window.
    oldest_recent = min(recent_parsed)
    available_at = oldest_recent + timedelta(days=7)

    # If the quota window has already elapsed, allow the request.
    if available_at <= now:
        return True, ""

    time_remaining = available_at - now
    hours_remaining = max(1, int(time_remaining.total_seconds() / 3600))

    message = (
        f"Strike directive request quota reached: {max_per_week} per week. "
        f"Next request available in {hours_remaining} hours."
    )
    return False, message


def _record_batch_generation_time(cycle: dict, now: datetime) -> None:
    """Record the timestamp of a newly generated batch."""
    timestamps = cycle.get("batch_generation_timestamps", [])
    if not isinstance(timestamps, list):
        timestamps = []
        cycle["batch_generation_timestamps"] = timestamps
    timestamps.append(now.isoformat())


def _warning_flavor_for_completion_rate(rate: float) -> str:
    if rate >= 0.75:
        return "Ordo Xenos reports strong compliance this cycle. Keep pressure on the remaining directives."
    if rate >= 0.4:
        return "Progress is mixed. Push assignments and completions before the window closes."
    return "Compliance remains poor. Ordo Xenos scrutiny is rising; all brothers are expected to respond."


def _batch_warning_channel(guild: discord.Guild | None) -> object | None:
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    channel_id = config_tp.get("general_channel_id")
    channel = guild.get_channel(int(channel_id)) if guild and channel_id else None
    if not channel and not _is_debug_mode():
        return None
    return channel


async def _send_single_batch_warning(
    guild: discord.Guild,
    data: dict,
    reminder_batch_id: str,
    now: datetime,
) -> bool:
    """Send one sparse warning embed in general chat per batch."""
    channel = _batch_warning_channel(guild)
    if not channel:
        return False

    packages = data.get("packages", {})
    batch_pkgs = [p for p in packages.values() if _batch_id_for_package(p) == reminder_batch_id]
    if not batch_pkgs:
        return False

    completed = [p for p in batch_pkgs if p.get("status") == STATUS_COMPLETED]
    actionable = [
        p for p in batch_pkgs
        if p.get("status") in (STATUS_UNASSIGNED, STATUS_DISTRIBUTED, STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED)
    ]
    if not actionable:
        return False

    completion_rate = len(completed) / len(batch_pkgs) if batch_pkgs else 0.0
    nearest_deadline = min(
        (datetime.fromisoformat(p["deadline"]) for p in actionable if p.get("deadline")),
        default=None,
    )
    if nearest_deadline is None:
        return False

    remaining = nearest_deadline - now
    hours_left = max(1, int(remaining.total_seconds() // 3600))

    embed = discord.Embed(
        title=f"{_DW_EMOJI} Strike Directive Reminder {_DW_EMOJI}",
        color=0xC4A030,
        description=(
            f"You have roughly **{hours_left} hour(s)** to assign and complete current Ordo Xenos strike directives.\n"
            f"{_warning_flavor_for_completion_rate(completion_rate)}"
        ),
    )
    embed.add_field(
        name="▸ Current Batch",
        value=(
            f"Batch: `{reminder_batch_id}`\n"
            f"Completed: {len(completed)}/{len(batch_pkgs)} ({completion_rate * 100:.0f}%)\n"
            f"Still Active: {len(actionable)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="▸ Earliest Deadline",
        value=_format_deadline_dual_region(nearest_deadline.isoformat()),
        inline=False,
    )

    msg = await _notify_send(channel, guild, content=f"<@&{WATCH_BROTHER_ROLE_ID}>", embed=embed)
    if msg is None:
        return False

    logger = getattr(_g, "logger", None)
    if logger:
        logger.info(f"[TP] General batch warning sent for {reminder_batch_id}")
    return True


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
STATUS_PENDING_SGT = "pending_sgt"        # Legacy status kept for migration compatibility
STATUS_RECRUITING = "recruiting"          # Sgt accepted, sign-up open in KT channel
STATUS_DEPLOYED = "deployed"              # Min brothers signed up + all reqs filled
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"                  # Assigned, deadline passed without submission
STATUS_LAPSED = "lapsed"                  # Distributed, never fully assigned, deadline passed

# Feature flags (Phase 0: Cadre-Based Specialist Migration)
MIGRATION_CADRE_SPECIALIST_ENABLED = False  # Disabled by default; controls specialist scope (company vs cadre)

# Telemetry counters for migration rollout safety (Phase 0)
_TELEMETRY_COUNTERS = {
    "specialist_assign_success": 0,         # Successful specialist assignments
    "specialist_assign_fail_authority": 0,  # Failed: insufficient authority
    "specialist_assign_fail_scope": 0,      # Failed: company scope violation
    "cross_company_specialist_assign": 0,   # Cross-company specialist assignments
    "specialist_removal_reconcile": 0,      # Removals during reconciliation
}


# ---------------------------------------------------------------------------
# Phase 1: Cadre-Based Specialist Migration - Config Loading
# ---------------------------------------------------------------------------

def _load_cadre_ownership_config() -> dict:
    """Load cadre ownership mappings from config/config.json.

    Returns dict mapping cadre role names to ownership info:
    {
        "Forgemaster": {
            "id": 1436505765073653860,
            "members": [
                {"name": "Watch Techmarine", "id": 1429342203251265576},
                ...
            ],
            "scope": "fortress"
        },
        ...
    }

    Falls back gracefully to empty dict if config absent (Phase 1 safe mode).
    """
    try:
        config = _b("CONFIG") or {}
        cadres_config = (config.get("target_packages") or {}).get("cadres") or {}
        
        result = {}
        for cadre_key, cadre_data in cadres_config.items():
            if not cadre_data or not isinstance(cadre_data, dict):
                _g.logger.warning(f"[TP Phase1] Invalid cadre config for '{cadre_key}': missing or malformed")
                continue
            
            leader_role_name = cadre_data.get("leader_role_name")
            leader_role_id = cadre_data.get("leader_role_id")
            member_roles = cadre_data.get("member_roles", [])
            scope = cadre_data.get("scope", "fortress")
            
            if not leader_role_name or not leader_role_id:
                _g.logger.warning(f"[TP Phase1] Cadre '{cadre_key}' missing leader role name or ID")
                continue
            
            # Normalize member_roles to list of dicts
            normalized_members = []
            for member in member_roles:
                if isinstance(member, dict) and member.get("name") and member.get("id"):
                    normalized_members.append(member)
                elif isinstance(member, str):
                    # Legacy: convert string role name to dict
                    normalized_members.append({"name": member, "id": None})
            
            result[leader_role_name] = {
                "id": leader_role_id,
                "members": normalized_members,
                "scope": scope,
                "cadre_key": cadre_key,  # For logging/debugging
            }
        
        if result:
            _g.logger.info(f"[TP Phase1] Loaded cadre ownership config: {len(result)} cadres")
        
        return result
    except Exception as e:
        _g.logger.warning(f"[TP Phase1] Failed to load cadre ownership config: {e}")
        return {}


def _get_cadre_ownership_mapping() -> dict:
    """Get cadre ownership mapping with fallback to hardcoded values.

    This function implements Phase 1 dual-read pattern:
    1. Try config-backed cadre ownership (new)
    2. Fall back to hardcoded _CADRE_OWNERSHIP (existing, Phase 1-2 compatibility)

    Returns dict mapping leader role ID → set of owned specialist role IDs.
    """
    config_mapping = _load_cadre_ownership_config()
    
    if config_mapping:
        # Convert config format to internal format
        result = {}
        for leader_role_name, cadre_info in config_mapping.items():
            leader_id = cadre_info["id"]
            member_ids = {m["id"] for m in cadre_info["members"] if m.get("id")}
            if member_ids:
                result[leader_id] = member_ids
        return result
    
    # Fallback to hardcoded mapping (existing code)
    # Will be removed in Phase 2 when config is required
    return _get_cadre_ownership_mapping_hardcoded()


def _get_cadre_ownership_mapping_hardcoded() -> dict:
    """Hardcoded cadre ownership mapping (Phase 1 fallback, will be removed Phase 2+).

    Maps leader role name → set of owned specialist role names.
    This is the fallback when config is not available.
    """
    return {
        "Blademaster": {"Bladeguard", "First Blade"},
        "Huntmaster": {"Huntmaster"},
        "Forgemaster": {"Watch Techmarine", "Venerable Dreadnought", "Honored Dreadnought"},
        "Chief Apothecary": {"Watch Apothecary"},
        "High Chaplain": {"Watch Chaplain"},
        "Void Warden": {"Watch Librarian"},
        "Castellan": {"Watch Keeper"},
    }


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
            data = json.load(f) or _empty_tp_store()
        migrated = _migrate_rep_scale_if_needed(data)
        migrated = _migrate_pending_sgt_flow_if_needed(data) or migrated
        migrated = _ensure_entity_stats_schema(data) or migrated
        if migrated:
            _save_tp(data)
        return data
    except Exception:
        return _empty_tp_store()


def _empty_strike_queue_store() -> dict:
    return {"entries": {}, "announced_matches": {}}


def _load_strike_queue() -> dict:
    try:
        if not os.path.exists(STRIKE_QUEUE_PATH):
            return _empty_strike_queue_store()
        with open(STRIKE_QUEUE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or _empty_strike_queue_store()
        if not isinstance(data, dict):
            return _empty_strike_queue_store()
        entries = data.get("entries")
        if not isinstance(entries, dict):
            data["entries"] = {}
        announced = data.get("announced_matches")
        if not isinstance(announced, dict):
            data["announced_matches"] = {}
        return data
    except Exception:
        return _empty_strike_queue_store()


def _save_strike_queue(data: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STRIKE_QUEUE_PATH + ".tmp"
        bak = STRIKE_QUEUE_PATH + ".bak"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        if os.path.exists(STRIKE_QUEUE_PATH):
            try:
                os.replace(STRIKE_QUEUE_PATH, bak)
            except Exception:
                pass
        os.replace(tmp, STRIKE_QUEUE_PATH)
    except Exception as e:
        _g.logger.error(f"[TP] Failed to save strike_directive_queue.json: {e}")


def _normalize_strike_queue_mode(mode: str | None) -> str:
    raw = str(mode or "any").strip().lower()
    if raw in {"hard", "hard-strat", "hard_strat"}:
        return "hard"
    if raw in {"omega", "omega-strat", "omega_strat"}:
        return "omega"
    return "any"


def _strike_queue_entry_expired(entry: dict, now: datetime | None = None) -> bool:
    expires_at = str((entry or {}).get("expires_at") or "").strip()
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except Exception:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return expiry <= ref


def _prune_strike_queue(data: dict, now: datetime | None = None) -> tuple[dict, int]:
    ref = now or datetime.now(timezone.utc)
    entries = data.setdefault("entries", {})
    removed = 0
    for user_id in list(entries.keys()):
        entry = entries.get(user_id)
        if not isinstance(entry, dict) or _strike_queue_entry_expired(entry, ref):
            entries.pop(user_id, None)
            removed += 1
    return data, removed


def _strike_queue_match_sweep_minutes() -> int:
    cfg = (_b("CONFIG") or {}).get("target_packages", {})
    try:
        minutes = int(cfg.get("strike_queue_match_sweep_minutes", 15))
    except Exception:
        return 15
    if 5 <= minutes <= 120:
        return minutes
    return 15


def _strike_queue_backfill_partials_enabled() -> bool:
    cfg = (_b("CONFIG") or {}).get("target_packages", {})
    raw = cfg.get("strike_queue_backfill_partials_enabled", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _strike_queue_partial_backfill_wait_percent() -> float:
    """Minimum relative wait percent before allowing tier-1 partial backfill."""
    cfg = (_b("CONFIG") or {}).get("target_packages", {})
    try:
        n = float(cfg.get("strike_queue_partial_backfill_wait_percent", 35))
    except Exception:
        return 35.0
    return max(0.0, min(100.0, n))


def _strike_queue_single_fill_wait_percent() -> float:
    """Minimum relative wait percent before allowing tier-2 single-slot backfill."""
    cfg = (_b("CONFIG") or {}).get("target_packages", {})
    try:
        n = float(cfg.get("strike_queue_single_fill_wait_percent", 70))
    except Exception:
        return 70.0
    return max(0.0, min(100.0, n))


def _strike_queue_announced_ttl_minutes() -> int:
    # Keep tentative groups short-lived and tied to sweep cadence.
    return max(15, min(60, _strike_queue_match_sweep_minutes() * 2))


def _prune_announced_strike_queue_matches(data: dict, packages: dict, active_entry_ids: set[str]) -> tuple[dict, int]:
    announced = data.setdefault("announced_matches", {})
    removed = 0
    now = datetime.now(timezone.utc)
    ttl_minutes = _strike_queue_announced_ttl_minutes()
    for package_id in list(announced.keys()):
        record = announced.get(package_id)
        pkg = packages.get(package_id)
        if not isinstance(record, dict) or not isinstance(pkg, dict):
            announced.pop(package_id, None)
            removed += 1
            continue
        if pkg.get("status") in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED, STATUS_DEPLOYED):
            announced.pop(package_id, None)
            removed += 1
            continue

        raw_queued_member_ids = record.get("queued_member_ids") or []
        if not isinstance(raw_queued_member_ids, list):
            announced.pop(package_id, None)
            removed += 1
            continue
        try:
            queued_member_ids = [int(uid) for uid in raw_queued_member_ids]
        except (TypeError, ValueError):
            announced.pop(package_id, None)
            removed += 1
            continue
        if not queued_member_ids or any(str(uid) not in active_entry_ids for uid in queued_member_ids):
            announced.pop(package_id, None)
            removed += 1
            continue

        signature = str(record.get("signature") or "")
        if signature != _queue_match_signature(pkg, queued_member_ids):
            announced.pop(package_id, None)
            removed += 1
            continue

        announced_at = str(record.get("announced_at") or "").strip()
        if announced_at:
            try:
                announced_dt = datetime.fromisoformat(announced_at)
                if announced_dt.tzinfo is None:
                    announced_dt = announced_dt.replace(tzinfo=timezone.utc)
                if now - announced_dt > timedelta(minutes=ttl_minutes):
                    announced.pop(package_id, None)
                    removed += 1
                    continue
            except Exception:
                announced.pop(package_id, None)
                removed += 1
    return data, removed


def _member_active_directive_commitment(member_id: int, data: dict) -> tuple[str, bool] | None:
    active_statuses = {STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED}
    for pkg in (data.get("packages", {}) or {}).values():
        if pkg.get("status") not in active_statuses:
            continue
        code = str(pkg.get("directive_code") or pkg.get("id") or "UNKNOWN")
        if int(member_id) in {int(uid) for uid in (pkg.get("signed_up", []) or [])}:
            return code, False
        if int(member_id) in {int(uid) for uid in (pkg.get("assigned_specialist_ids", []) or [])}:
            return code, True
    return None


def _queue_entry_sort_key(item: tuple[str, dict]) -> tuple[datetime, int]:
    user_id_str, entry = item
    queued_at = str((entry or {}).get("queued_at") or "").strip()
    try:
        queued_dt = datetime.fromisoformat(queued_at)
        if queued_dt.tzinfo is None:
            queued_dt = queued_dt.replace(tzinfo=timezone.utc)
    except Exception:
        queued_dt = datetime.max.replace(tzinfo=timezone.utc)
    try:
        uid = int(user_id_str)
    except Exception:
        uid = 0
    return queued_dt, uid


def _ordered_queue_entries(entries: dict) -> list[tuple[str, dict]]:
    return sorted(((uid, e) for uid, e in (entries or {}).items() if isinstance(e, dict)), key=_queue_entry_sort_key)


def _queue_member_display(guild: "discord.Guild | None", user_id: int) -> str:
    member = guild.get_member(int(user_id)) if guild else None
    if member is not None:
        return member.display_name
    return f"Brother {user_id}"


def _queue_eta_window_text(position: int, seats_per_sweep: int, sweep_minutes: int) -> str:
    return _queue_eta_window_text_with_context(position, seats_per_sweep, sweep_minutes, [], [])


def _queue_eta_window_text_with_context(
    position: int,
    seats_per_sweep: int,
    sweep_minutes: int,
    eligible_packages: list[dict],
    tentative_codes: list[str],
) -> str:
    if tentative_codes:
        shown = ", ".join(f"`{c}`" for c in tentative_codes[:2])
        if len(tentative_codes) > 2:
            shown += f", +{len(tentative_codes) - 2} more"
        low = sweep_minutes
        high = sweep_minutes * 2
        return (
            f"You are in a tentative strike group ({shown}). "
            f"Likely window: **{low}-{high} min**."
        )

    if position <= 0 or seats_per_sweep <= 0:
        return "No ETA yet - waiting for an eligible full strike composition."

    pkg_count = len(eligible_packages)
    constrained_count = sum(1 for p in eligible_packages if (p.get("required_roles") or []))
    omega_count = sum(1 for p in eligible_packages if "Omega" in str(p.get("mode") or ""))
    if pkg_count > 0:
        penalty = min(0.55, (0.35 * (constrained_count / pkg_count)) + (0.15 * (omega_count / pkg_count)))
    else:
        penalty = 0.0

    effective_seats = max(1, int(math.floor(seats_per_sweep * (1.0 - penalty))))
    waves = max(1, math.ceil(position / effective_seats))
    low = max(1, waves) * sweep_minutes
    high = (waves + 1) * sweep_minutes
    suffix = ""
    if constrained_count > 0:
        suffix = " Role-gated directives may increase wait."
    return f"Approximately **{low}-{high} min** (heuristic).{suffix}"


def _tentative_groups_for_status(
    queue_data: dict,
    packages: dict,
    guild: "discord.Guild | None",
) -> list[str]:
    groups: list[str] = []
    announced = queue_data.get("announced_matches") or {}
    if not isinstance(announced, dict):
        return groups
    for package_id, record in announced.items():
        if not isinstance(record, dict):
            continue
        pkg = packages.get(package_id) or {}
        code = str((pkg or {}).get("directive_code") or package_id)
        queued_member_ids = record.get("queued_member_ids") or []
        try:
            queued_ids = [int(uid) for uid in queued_member_ids]
        except Exception:
            continue
        names = [_queue_member_display(guild, uid) for uid in queued_ids]
        if len(names) > 5:
            names_text = ", ".join(names[:5]) + f", +{len(names) - 5} more"
        else:
            names_text = ", ".join(names) if names else "None"
        groups.append(f"`{code}`: {names_text}")
    return groups


def _member_tentative_codes(queue_data: dict, packages: dict, member_id: int) -> list[str]:
    codes: list[str] = []
    announced = queue_data.get("announced_matches") or {}
    if not isinstance(announced, dict):
        return codes
    for package_id, record in announced.items():
        if not isinstance(record, dict):
            continue
        queued_member_ids = record.get("queued_member_ids") or []
        try:
            queued_ids = {int(uid) for uid in queued_member_ids}
        except Exception:
            continue
        if int(member_id) not in queued_ids:
            continue
        pkg = packages.get(package_id) or {}
        codes.append(str((pkg or {}).get("directive_code") or package_id))
    return codes


def _member_meets_strike_queue_baseline(member: discord.Member) -> bool:
    if member.bot or not _is_active(member):
        return False
    member_roles = _member_role_names(member)
    min_idx = _RANK_SENIORITY_MAP.get("Watch Brother", 0)
    member_max = max((_RANK_SENIORITY_MAP.get(r, -1) for r in member_roles), default=-1)
    return member_max >= min_idx


async def _remove_member_from_strike_queue(user_id: int) -> bool:
    async with _STRIKE_QUEUE_LOCK:
        queue_data = _load_strike_queue()
        queue_data, _ = _prune_strike_queue(queue_data)
        removed = queue_data.setdefault("entries", {}).pop(str(int(user_id)), None)
        if removed is not None:
            _save_strike_queue(queue_data)
            return True
        _save_strike_queue(queue_data)
        return False


async def _reconcile_member_strike_queue_entry(member: discord.Member) -> bool:
    """Reconcile one member's queue entry after a role/status change.

    Returns True if the member remains queued after reconciliation.
    """
    if member is None:
        return False

    async with _STRIKE_QUEUE_LOCK:
        queue_data = _load_strike_queue()
        queue_data, _ = _prune_strike_queue(queue_data)
        entries = queue_data.setdefault("entries", {})
        entry = entries.get(str(member.id))
        if not isinstance(entry, dict):
            _save_strike_queue(queue_data)
            return False

        if not _member_meets_strike_queue_baseline(member):
            entries.pop(str(member.id), None)
            _save_strike_queue(queue_data)
            return False

        normalized_mode = _normalize_strike_queue_mode(entry.get("mode_preference"))
        current_platform = _tp_get_player_platform(member)
        if normalized_mode == "omega" and not current_platform:
            entries.pop(str(member.id), None)
            _save_strike_queue(queue_data)
            return False

        entry["platform"] = current_platform
        entry["mode_preference"] = normalized_mode
        _save_strike_queue(queue_data)
        return True


def _member_can_remain_attached_to_directive(
    member: discord.Member,
    pkg: dict,
    guild: "discord.Guild | None",
    attachment_kind: str,
) -> tuple[bool, str]:
    """Validate that a member still meets baseline/scope requirements for a directive attachment."""
    if member is None:
        return False, "Member not found."

    if _is_debug_mode() and _is_admin(member):
        return True, ""

    if not _member_meets_strike_queue_baseline(member):
        return False, "Member is no longer active or eligible."

    member_roles = _member_role_names(member)
    from .forge_ops import _resolve_killteam_for_member
    from .roster_ops import _get_member_company_name

    member_kt = _resolve_killteam_for_member(member)
    member_company = _get_member_company_name(member)
    assigned_kt = pkg.get("assigned_kt")
    assigned_company = pkg.get("assigned_company")

    mode = str(pkg.get("mode") or "")
    if "Omega" in mode and not _tp_get_player_platform(member):
        return False, "Omega directives require a PC/Console role."

    if attachment_kind == "specialist":
        required_specialist_roles = [
            role_name
            for role_name in (pkg.get("required_roles", []) or [])
            if role_name in _CADRE_SPECIALIST_ROLES
        ]
        if required_specialist_roles and any(role_name in member_roles for role_name in required_specialist_roles):
            return True, ""
        if required_specialist_roles:
            return False, "Member no longer holds a required specialist role for this directive."
        return False, "This directive does not require a specialist attachment."

    if _member_has_structural_scope(member_kt, member_company) and not (
        member_kt == assigned_kt or member_company == assigned_company
    ):
        return False, f"Member is no longer part of {assigned_kt or assigned_company}."

    return True, ""


async def _reconcile_member_directive_attachments(
    member: discord.Member,
    guild: "discord.Guild | None",
) -> list[str]:
    """Remove a member from directives they no longer qualify to remain attached to.

    Returns the package IDs that were changed.
    """
    if member is None:
        return []

    resolved_guild = guild or getattr(member, "guild", None) or _get_guild_from_bot()
    refreshed_package_ids: list[str] = []

    async with _TP_LOCK:
        data = _load_tp()
        changed = False
        for package_id, pkg in (data.get("packages") or {}).items():
            if pkg.get("status") not in (STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED):
                continue

            removable_kinds: set[str] = set()
            if int(member.id) in {int(uid) for uid in pkg.get("signed_up", [])}:
                allowed, _reason = _member_can_remain_attached_to_directive(member, pkg, resolved_guild, "signed")
                if not allowed:
                    removable_kinds.add("signed")

            if int(member.id) in {int(uid) for uid in pkg.get("assigned_specialist_ids", [])}:
                allowed, _reason = _member_can_remain_attached_to_directive(member, pkg, resolved_guild, "specialist")
                if not allowed:
                    removable_kinds.add("specialist")

            if not removable_kinds:
                continue

            removed, _message = _remove_target_from_package(pkg, int(member.id), removable_kinds, resolved_guild)
            if removed:
                changed = True
                refreshed_package_ids.append(package_id)

        if changed:
            _save_tp(data)

    for package_id in refreshed_package_ids:
        await _refresh_signup_embed_for_package(package_id, resolved_guild)
    return refreshed_package_ids


def _strike_mode_matches_preference(pkg: dict, preference: str) -> bool:
    normalized = _normalize_strike_queue_mode(preference)
    mode = str(pkg.get("mode") or "")
    if normalized == "hard":
        return "Hard" in mode
    if normalized == "omega":
        return "Omega" in mode
    return True


def _queue_eligible_packages_for_member(
    member: discord.Member,
    packages: dict,
    mode_preference: str,
    guild: "discord.Guild | None",
) -> list[dict]:
    """Return recruiting directives this member can actually be queue-matched into."""
    visible_non_deployed = _visible_non_deployed_packages_for_member(member, packages)
    visible_ids = {str(p.get("id")) for p in visible_non_deployed}
    backfill_partials = _strike_queue_backfill_partials_enabled()

    eligible_packages: list[dict] = []
    for pkg in packages.values():
        if pkg.get("status") != STATUS_RECRUITING:
            continue
        if str(pkg.get("id")) not in visible_ids:
            continue
        # Default behavior: only seed fully open directives.
        # Optional behavior: allow queue backfill for partially-filled directives.
        if (not backfill_partials) and (pkg.get("signed_up", []) or pkg.get("assigned_specialist_ids", [])):
            continue
        if not _strike_mode_matches_preference(pkg, mode_preference):
            continue
        eligible, _reason = _is_eligible_to_sign_up(member, pkg, guild)
        if not eligible:
            continue
        eligible_packages.append(pkg)

    return eligible_packages


def _queue_member_exact_requirement_score(member: discord.Member, pkg: dict) -> int:
    roles = _member_role_names(member)
    req_roles = pkg.get("required_roles", []) or []
    return sum(1 for req in req_roles if req in roles)


def _queue_existing_roster_names(pkg: dict, guild: discord.Guild) -> list[str]:
    names: list[str] = []
    for uid in list(dict.fromkeys((pkg.get("signed_up", []) or []) + (pkg.get("assigned_specialist_ids", []) or []))):
        m = guild.get_member(int(uid)) if guild else None
        names.append(m.display_name if m else str(uid))
    return names


def _queue_match_signature(pkg: dict, queued_member_ids: list[int]) -> str:
    existing_ids = sorted(int(uid) for uid in dict.fromkeys((pkg.get("signed_up", []) or []) + (pkg.get("assigned_specialist_ids", []) or [])))
    queued_ids = sorted(int(uid) for uid in queued_member_ids)
    return f"{pkg.get('id')}|existing:{','.join(map(str, existing_ids))}|queued:{','.join(map(str, queued_ids))}"


def _queue_match_oldest_timestamp(entry_map: dict[str, dict], members: list[discord.Member]) -> datetime:
    oldest = datetime.max.replace(tzinfo=timezone.utc)
    for member in members:
        queued_at = str((entry_map.get(str(member.id)) or {}).get("queued_at") or "").strip()
        try:
            parsed = datetime.fromisoformat(queued_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except Exception:
            parsed = datetime.max.replace(tzinfo=timezone.utc)
        if parsed < oldest:
            oldest = parsed
    return oldest


def _queue_match_sort_key(item: tuple[dict, list[discord.Member], list[str], datetime]) -> tuple:
    pkg, members, _existing_names, oldest_queue = item[:4]
    quality_tier = _queue_match_quality_tier(pkg, len(members))
    current_count = len(pkg.get("signed_up", []) or []) + len(pkg.get("assigned_specialist_ids", []) or [])
    mode = str(pkg.get("mode") or "")
    capacity = 3 if "Hard" in mode else 5
    requirement_score = sum(_queue_member_exact_requirement_score(m, pkg) for m in members)
    return (quality_tier, current_count, capacity, -requirement_score, oldest_queue)


def _queue_match_quality_tier(pkg: dict, matched_count: int) -> int:
    """Rank candidate match quality (lower is better).

    0: Fully-open directive filled by a full queued team (best)
    1: Partially-filled directive completed by 2+ queued brothers
    2: Single queued brother fills the last slot on a directive (last resort)
    3: Any other catch-all shape (should be rare)
    """
    current_count = len(pkg.get("signed_up", []) or []) + len(pkg.get("assigned_specialist_ids", []) or [])
    mode = str(pkg.get("mode") or "")
    capacity = 3 if "Hard" in mode else 5
    remaining_slots = max(0, capacity - current_count)

    if current_count == 0 and matched_count == capacity and remaining_slots == capacity:
        return 0
    if current_count > 0 and matched_count >= 2 and remaining_slots >= 2:
        return 1
    if current_count > 0 and matched_count == 1 and remaining_slots == 1:
        return 2
    return 3


def _select_queue_members_for_package(
    pkg: dict,
    candidate_members: list[discord.Member],
    guild: discord.Guild,
) -> list[discord.Member]:
    mode = str(pkg.get("mode") or "")
    capacity = 3 if "Hard" in mode else 5
    existing_signed = list(pkg.get("signed_up", []) or [])
    existing_specialists = list(pkg.get("assigned_specialist_ids", []) or [])
    current_count = len(existing_signed) + len(existing_specialists)
    remaining_slots = capacity - current_count
    if remaining_slots <= 0:
        return []

    if len(candidate_members) < remaining_slots:
        return []

    scored_candidates = sorted(
        candidate_members,
        key=lambda m: (-_queue_member_exact_requirement_score(m, pkg), m.id),
    )
    scored_candidates = scored_candidates[:_STRIKE_QUEUE_COMBINATION_CANDIDATE_LIMIT]

    best_combo: list[discord.Member] = []
    best_score: tuple | None = None
    for combo in combinations(scored_candidates, remaining_slots):
        projected_pkg = dict(pkg)
        projected_pkg["signed_up"] = existing_signed + [m.id for m in combo]
        projected_pkg["assigned_specialist_ids"] = existing_specialists

        if "Omega" in mode:
            console_count = 0
            for uid in projected_pkg["signed_up"] + projected_pkg["assigned_specialist_ids"]:
                m = guild.get_member(int(uid)) if guild else None
                if m and _tp_get_player_platform(m) == "console":
                    console_count += 1
            if console_count > 2:
                continue

        if not _check_deployed(projected_pkg, guild):
            continue

        exact_score = sum(_queue_member_exact_requirement_score(m, pkg) for m in combo)
        combo_score = (-exact_score, sorted(m.id for m in combo))
        if best_score is None or combo_score < best_score:
            best_score = combo_score
            best_combo = list(combo)

    return best_combo


async def _post_queue_match_ping(
    pkg: dict,
    matched_members: list[discord.Member],
    guild: discord.Guild,
    existing_roster_names: list[str],
) -> bool:
    if not matched_members or guild is None:
        return False

    thread = await _ensure_directive_forum_thread(pkg.get("id"), guild, pkg=pkg)
    if not isinstance(thread, discord.Thread):
        return False

    code = pkg.get("directive_code") or pkg.get("id") or "UNKNOWN"
    name = str(pkg.get("directive_name") or "").strip()
    mode = str(pkg.get("mode") or "")
    capacity = 3 if "Hard" in mode else 5
    classification = str(pkg.get("classification") or "STRIKE").title()
    roster_mentions = " ".join(m.mention for m in matched_members)
    matched_names = ", ".join(m.display_name for m in matched_members)
    existing_line = f"Existing roster: {', '.join(existing_roster_names)}" if existing_roster_names else "None"
    directive_line = f"`{code}` — {name}" if name else f"`{code}`"
    embed = discord.Embed(
        title="Strike Team Readied",
        description=(
            f"**Astropathic concurrence achieved.** {classification} directive {directive_line} has a ready strike element now.\n\n"
            f"Queued brothers available now: {matched_names}\n"
            f"Required strike strength: **{capacity}**\n"
            f"Queue cleared for matched brothers."
        ),
        color=0xA31919,
    )
    embed.add_field(name="Existing Roster", value=existing_line, inline=False)
    await thread.send(content=roster_mentions, embed=embed)
    return True


async def _apply_strike_queue_match(
    pkg: dict,
    matched_members: list[discord.Member],
    guild: discord.Guild,
) -> dict | None:
    if not matched_members or guild is None:
        return None

    package_id = str(pkg.get("id") or "").strip()
    if not package_id:
        return None

    matched_ids = [int(member.id) for member in matched_members]

    async with _TP_LOCK:
        data = _load_tp()
        live_pkg = (data.get("packages", {}) or {}).get(package_id)
        if not isinstance(live_pkg, dict):
            return None
        if live_pkg.get("status") != STATUS_RECRUITING:
            return None

        for member in matched_members:
            eligible, _reason = _is_eligible_to_sign_up(member, live_pkg, guild)
            if not eligible:
                return None

        live_pkg.setdefault("signed_up", [])
        for member_id in matched_ids:
            if member_id not in live_pkg["signed_up"]:
                live_pkg["signed_up"].append(member_id)

        if _check_deployed(live_pkg, guild):
            live_pkg["status"] = STATUS_DEPLOYED

        _save_tp(data)
        committed_pkg = dict(live_pkg)

    return committed_pkg


async def _finalize_strike_queue_match_directive(package_id: str, committed_pkg: dict, guild: discord.Guild) -> None:
    await _ensure_directive_forum_thread(package_id, guild, pkg=committed_pkg)
    if committed_pkg.get("signup_message_id") and committed_pkg.get("signup_channel_id"):
        await _refresh_signup_embed_for_package(package_id, guild)
    else:
        await _post_signup_embed(package_id, guild)


def _member_queue_wait_time_minutes(member: discord.Member, entry: dict) -> float:
    """Calculate how many minutes a member has been waiting in queue."""
    queued_at_iso = str((entry or {}).get("queued_at") or "").strip()
    if not queued_at_iso:
        return 0.0
    try:
        queued_at_iso = queued_at_iso.replace("Z", "+00:00")
        queued_at = datetime.fromisoformat(queued_at_iso)
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - queued_at
        return max(0.0, delta.total_seconds() / 60.0)
    except (ValueError, TypeError):
        return 0.0


def _queue_wait_percent_of_oldest(wait_minutes: float, max_wait_minutes: float) -> float:
    """Convert a member wait into a queue-relative percentage of current oldest wait."""
    if max_wait_minutes <= 0:
        return 0.0
    return max(0.0, min(100.0, (wait_minutes / max_wait_minutes) * 100.0))


def _queue_match_wait_percent(
    entry_map: dict[str, dict],
    match_members: list[discord.Member],
    max_wait_minutes: float,
) -> float:
    """Return the shortest relative wait percent among matched members."""
    if not match_members:
        return 0.0
    return min(
        _queue_wait_percent_of_oldest(
            _member_queue_wait_time_minutes(member, entry_map.get(str(member.id), {})),
            max_wait_minutes,
        )
        for member in match_members
    )


async def _evaluate_strike_queue_matches(guild: discord.Guild) -> int:
    if guild is None:
        return 0

    async with _STRIKE_QUEUE_MATCH_LOCK:
        follow_up_actions: list[tuple[str, dict, list[discord.Member], list[str]]] = []
        async with _STRIKE_QUEUE_LOCK:
            queue_data = _load_strike_queue()
            queue_data, _ = _prune_strike_queue(queue_data)
            entries = queue_data.setdefault("entries", {})
            if not entries:
                _save_strike_queue(queue_data)
                return 0

            data = _load_tp()
            packages = data.get("packages", {})
            active_entries: list[tuple[discord.Member, dict]] = []
            for user_id, entry in entries.items():
                try:
                    member_id = int(user_id)
                except (TypeError, ValueError):
                    continue
                member = guild.get_member(member_id) if guild else None
                if not member or not _member_meets_strike_queue_baseline(member):
                    continue
                active_entries.append((member, entry))
            active_entry_ids = {str(member.id) for member, _entry in active_entries}
            queue_data, _ = _prune_announced_strike_queue_matches(queue_data, packages, active_entry_ids)

            if not active_entries:
                _save_strike_queue(queue_data)
                return 0

            candidate_matches: list[tuple[dict, list[discord.Member], list[str], datetime]] = []
            backfill_partials = _strike_queue_backfill_partials_enabled()
            partial_wait_threshold = _strike_queue_partial_backfill_wait_percent()
            single_wait_threshold = _strike_queue_single_fill_wait_percent()
            max_wait_minutes = max(
                (_member_queue_wait_time_minutes(member, entry) for member, entry in active_entries),
                default=0.0,
            )

            for pkg in packages.values():
                if pkg.get("status") != STATUS_RECRUITING:
                    continue
                if (not backfill_partials) and (pkg.get("signed_up", []) or pkg.get("assigned_specialist_ids", [])):
                    continue

                visible_candidates: list[discord.Member] = []
                for member, entry in active_entries:
                    if not _strike_mode_matches_preference(pkg, entry.get("mode_preference")):
                        continue
                    visible = _visible_non_deployed_packages_for_member(member, packages)
                    if not any(p.get("id") == pkg.get("id") for p in visible):
                        continue
                    eligible, _reason = _is_eligible_to_sign_up(member, pkg, guild)
                    if not eligible:
                        continue
                    visible_candidates.append(member)

                match_members = _select_queue_members_for_package(pkg, visible_candidates, guild)
                if not match_members:
                    continue

                quality_tier = _queue_match_quality_tier(pkg, len(match_members))

                # Gate backfill tiers by relative queue age, not fixed minutes.
                if quality_tier == 1:
                    match_wait_percent = _queue_match_wait_percent(entries, match_members, max_wait_minutes)
                    if match_wait_percent < partial_wait_threshold:
                        continue

                if quality_tier == 2:
                    match_wait_percent = _queue_match_wait_percent(entries, match_members, max_wait_minutes)
                    if match_wait_percent < single_wait_threshold:
                        continue

                candidate_matches.append((
                    pkg,
                    match_members,
                    _queue_existing_roster_names(pkg, guild),
                    _queue_match_oldest_timestamp(entries, match_members),
                ))

            if not candidate_matches:
                _save_strike_queue(queue_data)
                return 0

            candidate_matches.sort(key=_queue_match_sort_key)

            used_member_ids: set[int] = set()
            committed = 0
            for pkg, match_members, existing_names, _oldest_queue in candidate_matches:
                if any(m.id in used_member_ids for m in match_members):
                    continue

                package_id = str(pkg.get("id") or "").strip()
                if not package_id:
                    continue

                matched_ids = [int(m.id) for m in match_members]
                queue_data.setdefault("announced_matches", {})[package_id] = {
                    "signature": _queue_match_signature(pkg, matched_ids),
                    "queued_member_ids": matched_ids,
                    "announced_at": datetime.now(timezone.utc).isoformat(),
                }
                _save_strike_queue(queue_data)

                try:
                    committed_pkg = await _apply_strike_queue_match(pkg, match_members, guild)
                except Exception as exc:
                    _g.logger.error(f"[TP] Queue match commit failed for {pkg.get('id')}: {exc}")
                    continue
                if not committed_pkg:
                    continue

                package_id = str(committed_pkg.get("id") or package_id or "").strip()
                for member_id in [m.id for m in match_members]:
                    entries.pop(str(member_id), None)
                queue_data.setdefault("announced_matches", {}).pop(package_id, None)
                _save_strike_queue(queue_data)

                used_member_ids.update(m.id for m in match_members)
                committed += 1
                follow_up_actions.append((package_id, committed_pkg, match_members, existing_names))

        for package_id, committed_pkg, match_members, existing_names in follow_up_actions:
            try:
                await _finalize_strike_queue_match_directive(package_id, committed_pkg, guild)
                ok = await _post_queue_match_ping(committed_pkg, match_members, guild, existing_names)
                if not ok:
                    _g.logger.debug(f"[TP] Queue match committed without forum ping for {package_id}")
            except Exception as exc:
                _g.logger.error(f"[TP] Queue match follow-up failed for {package_id}: {exc}")

        return committed


def _visible_active_packages_for_member(member: discord.Member, packages: dict) -> list[dict]:
    """Return the active directive pool naturally visible to a member today."""

    def _active(statuses=None):
        return [
            p for p in packages.values()
            if p["status"] not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
            and (statuses is None or p["status"] in statuses)
        ]

    def _is_personally_attached(p: dict) -> bool:
        return (
            member.id in p.get("signed_up", [])
            or member.id in p.get("assigned_specialist_ids", [])
        )

    _mroles = _member_role_names(member)
    if "Watch Master" in _mroles:
        return _active()

    if "Watch Captain" in _mroles or "Watch Lieutenant" in _mroles:
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member)
        return [
            p for p in _active()
            if p["status"] == STATUS_DISTRIBUTED
            or (
                p.get("assigned_company") == company
                and p["status"] in (STATUS_RECRUITING, STATUS_DEPLOYED)
            )
            or _is_personally_attached(p)
        ]

    # High Command visibility mirrors signup eligibility: global active pool.
    # Captain/Lieutenant company scope is intentionally handled above.
    if any(role_name in HIGH_COMMAND_RANKS for role_name in _mroles):
        return _active()

    if _mroles & _CADRE_LEADER_ROLES:
        cadre_pkgs = [
            p for p in _active([STATUS_RECRUITING, STATUS_DEPLOYED])
            if any(_cadre_leader_owns(member, r) for r in p.get("required_roles", []))
        ]
        attached_pkgs = [p for p in _active() if _is_personally_attached(p)]
        merged_by_id = {p.get("id"): p for p in cadre_pkgs}
        for p in attached_pkgs:
            merged_by_id[p.get("id")] = p
        return list(merged_by_id.values())

    if _is_debug_mode() and _is_admin(member):
        return _active()

    from .forge_ops import _resolve_killteam_for_member
    kt = _resolve_killteam_for_member(member)
    if kt:
        return [
            p for p in _active()
            if p.get("assigned_kt") == kt or _is_personally_attached(p)
        ]
    return [p for p in _active() if _is_personally_attached(p)]


def _visible_non_deployed_packages_for_member(member: discord.Member, packages: dict) -> list[dict]:
    return [p for p in _visible_active_packages_for_member(member, packages) if p.get("status") != STATUS_DEPLOYED]

def _save_tp(data: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TARGET_PACKAGES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _g.logger.error(f"[TP] Failed to save target_packages.json: {e}")


def _empty_tp_store() -> dict:
    return {
        "rep": _REP_NEUTRAL,
        "rep_scale_version": _REP_SCALE_VERSION,
        "cycle": {
            "generated_at": None,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "lapsed": 0,
            "general_warning_sent_at": {},
            "batch_summary_posted_at": {},
            "batch_generation_timestamps": [],
        },
        "entity_stats": {
            "companies": {},
            "kill_teams": {},
            "cadres": {},
        },
        "packages": {},
        "rep_embed_message_id": None,
    }


def _migrate_pending_sgt_flow_if_needed(data: dict) -> bool:
    """Migrate legacy pending_sgt directives to recruiting sign-up flow."""
    changed = False
    for pkg in (data.get("packages", {}) or {}).values():
        if pkg.get("status") == STATUS_PENDING_SGT:
            pkg["status"] = STATUS_RECRUITING
            pkg["sgt_accept_message_id"] = None
            pkg["sgt_accept_channel_id"] = None
            changed = True
    return changed


def _ensure_entity_stats_schema(data: dict) -> bool:
    """Ensure entity stats buckets exist and carry rep_earned counters."""
    changed = False
    stats = data.setdefault("entity_stats", {})
    for bucket in ("companies", "kill_teams", "cadres"):
        if bucket not in stats or not isinstance(stats.get(bucket), dict):
            stats[bucket] = {}
            changed = True
        for _, row in stats[bucket].items():
            if "rep_earned" not in row:
                row["rep_earned"] = 0.0
                changed = True
    return changed


def _legacy_rep_to_new(rep_value: float) -> float:
    """Convert legacy -3..3 rep to new 0..60 scale."""
    legacy = max(-3.0, min(3.0, float(rep_value or 0.0)))
    return float(round((legacy + 3.0) * 10.0, 4))


def _migrate_rep_scale_if_needed(data: dict) -> bool:
    """One-time migration for target_packages.json rep values to 0..60 scale."""
    if int(data.get("rep_scale_version", 1) or 1) >= _REP_SCALE_VERSION:
        return False

    data["rep"] = _legacy_rep_to_new(data.get("rep", 0.0))
    for pkg in (data.get("packages", {}) or {}).values():
        if "rep_before" in pkg:
            pkg["rep_before"] = _legacy_rep_to_new(pkg.get("rep_before", 0.0))
        if "rep_after" in pkg:
            pkg["rep_after"] = _legacy_rep_to_new(pkg.get("rep_after", 0.0))

    data["rep_scale_version"] = _REP_SCALE_VERSION
    return True


def _rep_tier_for_strat(rep: float) -> int:
    """Map 0..60 reputation to legacy strat tiers -3..+3."""
    rep_clamped = max(_REP_MIN, min(_REP_MAX, float(rep or _REP_NEUTRAL)))
    if rep_clamped < 10:
        return -3
    if rep_clamped < 20:
        return -2
    if rep_clamped < 30:
        return -1
    if rep_clamped < 40:
        return 0
    if rep_clamped < 50:
        return 1
    if rep_clamped < 58:
        return 2
    return 3


def _rep_delta_for_package(pkg: dict, outcome: str) -> float:
    """Return rep delta for a directive outcome under the 0..60 model."""
    mode = str(pkg.get("mode", "") or "")
    is_omega = "Omega" in mode
    if outcome == STATUS_COMPLETED:
        req_roles = pkg.get("required_roles", []) or []
        delta = 5.0 if is_omega else 3.0
        if req_roles:
            delta += 1.0
        return delta
    if outcome == STATUS_FAILED:
        return -3.0 if is_omega else -2.0
    if outcome == STATUS_LAPSED:
        return -2.0 if is_omega else -1.0
    return 0.0


def _apply_rep_delta(data: dict, delta: float) -> None:
    cur = float(data.get("rep", _REP_NEUTRAL) or _REP_NEUTRAL)
    data["rep"] = max(_REP_MIN, min(_REP_MAX, cur + float(delta or 0.0)))


def _specialist_rep_bucket(member: "discord.Member") -> str | None:
    roles = _member_role_names(member)
    if roles & {"Watch Apothecary", "Chief Apothecary"}:
        return "Apothecarion"
    if roles & {"Watch Techmarine", "Forgemaster", "Honored Dreadnought", "Venerable Dreadnought"}:
        return "Armory"
    if roles & {"Watch Chaplain", "High Chaplain"}:
        return "Reclusiam"
    if roles & {"Watch Librarian", "Void Warden"}:
        return "Librarius"
    if roles & {"Bladeguard", "First Blade", "Blademaster"}:
        return "Blades"
    return None


def _compute_participation_rep_allocations(pkg: dict, guild: "discord.Guild | None", total_rep: float) -> dict:
    """Split directive completion rep across participating KTs and specialist cadres.

    Captain/Lieutenant participants are intentionally excluded from split attribution;
    their contribution is represented in full via company rep.
    """
    result = {"kill_teams": {}, "cadres": {}}
    total = float(total_rep or 0.0)
    if total <= 0:
        return result

    signed_ids = [int(uid) for uid in (pkg.get("signed_up", []) or []) if str(uid).strip()]
    specialist_ids = [int(uid) for uid in (pkg.get("assigned_specialist_ids", []) or []) if str(uid).strip()]
    specialist_id_set = set(specialist_ids)
    participant_ids = list(dict.fromkeys(signed_ids + specialist_ids))
    if not participant_ids:
        return result

    from .forge_ops import _resolve_killteam_for_member

    contributor_keys: set[tuple[str, str]] = set()
    for uid in participant_ids:
        member = guild.get_member(uid) if guild else None
        if not member:
            continue

        roles = _member_role_names(member)
        if "Watch Captain" in roles or "Watch Lieutenant" in roles:
            continue

        if uid in specialist_id_set:
            cadre_bucket = _specialist_rep_bucket(member)
            if cadre_bucket:
                contributor_keys.add(("cadres", cadre_bucket))
                continue

        kt_name = _resolve_killteam_for_member(member)
        if kt_name:
            contributor_keys.add(("kill_teams", kt_name))

    if not contributor_keys:
        return result

    ordered = sorted(contributor_keys, key=lambda item: (item[0], item[1]))
    cents_total = int(round(total * 100.0))
    base = cents_total // len(ordered)
    remainder = cents_total % len(ordered)
    for idx, (bucket, name) in enumerate(ordered):
        share_cents = base + (1 if idx < remainder else 0)
        result[bucket][name] = round(share_cents / 100.0, 2)

    return result


def _compute_company_command_bonus(pkg: dict, guild: "discord.Guild | None") -> float:
    """Return company-only bonus rep from participating command members.

    Each participating Watch Captain or Watch Lieutenant contributes +1.0 company
    rep on completion. This bonus does not flow to KT/cadre split allocations.
    """
    assigned_company = pkg.get("assigned_company")
    if not assigned_company:
        return 0.0

    participant_ids = [
        int(uid)
        for uid in dict.fromkeys((pkg.get("signed_up", []) or []) + (pkg.get("assigned_specialist_ids", []) or []))
        if str(uid).strip()
    ]
    if not participant_ids or guild is None:
        return 0.0

    bonus = 0.0
    for uid in participant_ids:
        member = guild.get_member(uid) if guild else None
        if not member:
            continue
        roles = _member_role_names(member)
        if "Watch Captain" in roles or "Watch Lieutenant" in roles:
            bonus += 1.0
    return bonus


def _compute_fortress_command_bonus(pkg: dict, guild: "discord.Guild | None") -> float:
    """Return fortress-only bonus rep from participating high command members.

    Watch Master and Huntmaster contribute +1.0 fortress rep each when they
    participate in a completed directive.
    """
    participant_ids = [
        int(uid)
        for uid in dict.fromkeys((pkg.get("signed_up", []) or []) + (pkg.get("assigned_specialist_ids", []) or []))
        if str(uid).strip()
    ]
    if not participant_ids or guild is None:
        return 0.0

    bonus = 0.0
    for uid in participant_ids:
        member = guild.get_member(uid) if guild else None
        if not member:
            continue
        roles = _member_role_names(member)
        if "Watch Master" in roles or "Huntmaster" in roles:
            bonus += 1.0
    return bonus


_CADRE_LEADER_BONUS_BY_ROLE = {
    "Blademaster": "Blades",
    "Forgemaster": "Armory",
    "Chief Apothecary": "Apothecarion",
    "High Chaplain": "Reclusiam",
    "Void Warden": "Librarius",
}


def _compute_cadre_leader_bonus_allocations(pkg: dict, guild: "discord.Guild | None") -> dict[str, float]:
    """Return cadre-only bonus rep from participating cadre leaders.

    Each participating cadre leader contributes +0.5 rep to their cadre bucket.
    Huntmaster is intentionally excluded and contributes to fortress bonus only.
    """
    participant_ids = [
        int(uid)
        for uid in dict.fromkeys((pkg.get("signed_up", []) or []) + (pkg.get("assigned_specialist_ids", []) or []))
        if str(uid).strip()
    ]
    if not participant_ids or guild is None:
        return {}

    bonus: dict[str, float] = {}
    for uid in participant_ids:
        member = guild.get_member(uid) if guild else None
        if not member:
            continue
        roles = _member_role_names(member)
        for role_name, cadre_bucket in _CADRE_LEADER_BONUS_BY_ROLE.items():
            if role_name in roles:
                bonus[cadre_bucket] = round(float(bonus.get(cadre_bucket, 0.0)) + 0.5, 2)
                break
    return bonus


def _apply_entity_rep_allocations(
    data: dict,
    pkg: dict,
    allocations: dict,
    total_rep: float,
    company_bonus: float = 0.0,
    cadre_bonus_allocations: dict | None = None,
) -> None:
    """Apply split participation rep to KT/cadre stats and company rep credit.

    Company credit = full directive rep + command participation bonus.
    """
    stats = data.setdefault("entity_stats", {})
    kt_stats = stats.setdefault("kill_teams", {})
    company_stats = stats.setdefault("companies", {})
    cadre_stats = stats.setdefault("cadres", {})

    for kt_name, delta in (allocations.get("kill_teams", {}) or {}).items():
        row = kt_stats.setdefault(kt_name, {"completed": 0, "failed": 0, "rep_earned": 0.0})
        row["rep_earned"] = round(float(row.get("rep_earned", 0.0) or 0.0) + float(delta or 0.0), 2)

    merged_cadre_allocations: dict[str, float] = {}
    for cadre_name, delta in (allocations.get("cadres", {}) or {}).items():
        merged_cadre_allocations[cadre_name] = float(merged_cadre_allocations.get(cadre_name, 0.0)) + float(delta or 0.0)
    for cadre_name, delta in (cadre_bonus_allocations or {}).items():
        merged_cadre_allocations[cadre_name] = float(merged_cadre_allocations.get(cadre_name, 0.0)) + float(delta or 0.0)

    for cadre_name, delta in merged_cadre_allocations.items():
        row = cadre_stats.setdefault(cadre_name, {"completed": 0, "failed": 0, "rep_earned": 0.0})
        row["rep_earned"] = round(float(row.get("rep_earned", 0.0) or 0.0) + float(delta or 0.0), 2)

    assigned_company = pkg.get("assigned_company")
    if assigned_company:
        row = company_stats.setdefault(assigned_company, {"completed": 0, "failed": 0, "rep_earned": 0.0})
        company_delta = float(total_rep or 0.0) + float(company_bonus or 0.0)
        row["rep_earned"] = round(float(row.get("rep_earned", 0.0) or 0.0) + company_delta, 2)


def _select_package_multiplier(rep: float) -> int:
    """Pick an integer directive-volume multiplier from the current rep band."""
    rep_clamped = max(_REP_MIN, min(_REP_MAX, float(rep or _REP_NEUTRAL)))
    for upper_bound, weights in _PACKAGE_MULTIPLIER_WEIGHTS:
        if rep_clamped < upper_bound:
            return random.choices([1, 2, 3, 4], weights=weights, k=1)[0]
    return 1


def _batch_company_stats(batch_pkgs: list[dict]) -> dict[str, dict[str, int]]:
    """Compute company stats from the provided batch only."""
    stats: dict[str, dict[str, int]] = {}
    for pkg in batch_pkgs:
        company = pkg.get("assigned_company")
        if not company:
            continue
        company_stats = stats.setdefault(company, {"completed": 0, "failed": 0})
        if pkg.get("status") == STATUS_COMPLETED:
            company_stats["completed"] += 1
        elif pkg.get("status") in (STATUS_FAILED, STATUS_LAPSED):
            company_stats["failed"] += 1
    return stats


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
# Directive code / name generation
# ---------------------------------------------------------------------------

_DIRECTIVE_GREEK = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi",
    "Rho", "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega",
]

_DIRECTIVE_ADJECTIVES = [
    "Silent", "Iron", "Void", "Crimson", "Ashen", "Hollow", "Pale", "Dark",
    "Steel", "Broken", "Fallen", "Cold", "Burning", "Black", "White", "Grey",
    "Eternal", "Lost", "Bitter", "Sable", "Veiled", "Sacred", "Grim",
    "Severed", "Sundered", "Blighted", "Forsaken", "Shrouded", "Ravaged",
    "Wrathful", "Undying", "Buried", "Starless", "Hollow", "Scarred",
]

_DIRECTIVE_NOUNS = [
    "Spear", "Blade", "Storm", "Vigil", "Gate", "Throne", "Shroud", "Lance",
    "Pyre", "Forge", "Warden", "Oath", "Seal", "Relic", "Abyss", "Dirge",
    "Veil", "Chain", "Brand", "Coil", "Tide", "Hammer", "Fang", "Crest",
    "Hunger", "Wake", "Talon", "Shard", "Pact", "Mantle", "Sigil", "Wound",
    "Bastion", "Requiem", "Purgatory", "Terminus", "Omen", "Cipher",
]

_DIRECTIVE_VERBS = [
    "Break", "Sever", "Purge", "Strike", "Hunt", "Burn", "Seal",
    "Claim", "Raze", "Pierce", "Silence", "Condemn", "Expunge",
    "Shatter", "Crush", "Reclaim", "Sanctify", "Annul", "Bleed",
    "Unmake", "Scour", "Erase", "Consume", "Drive", "Slay",
]

_DIRECTIVE_MODIFIERS = [
    "Protocol", "Mandate", "Sanction", "Verdict", "Rite", "Accord",
    "Measure", "Decree", "Inquisition", "Edict", "Warrant", "Judgment",
]

# Total unique codenames:
#   Noun only:           38
#   Adj + Noun:          35 × 38 = 1,330
#   Verb + Noun:         25 × 38 = 950
#   Noun + Modifier:     38 × 12 = 456
#   Adj + Noun + Mod:    35 × 38 × 12 = 15,960
#   Verb + Adj + Noun:   25 × 35 × 38 = 33,250
#   Total ≈ 51,984 unique codenames


def _smallcaps(text: str) -> str:
    """Convert ASCII letters to Unicode small-cap equivalents."""
    _MAP = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
    )
    return text.translate(_MAP)


def _generate_directive_code(existing_codes: set) -> str:
    """Generate a unique xxx-GREEK display code (e.g. 734-THETA)."""
    for _ in range(1000):
        number = random.randint(100, 999)
        greek = random.choice(_DIRECTIVE_GREEK).upper()
        code = f"{number}-{greek}"
        if code not in existing_codes:
            return code
    raise RuntimeError("Failed to generate unique directive code after 1000 attempts")


def _generate_directive_name(existing_names: set) -> str:
    """Generate a unique 1-3 word directive codename (e.g. 'Silent Spear')."""
    for _ in range(1000):
        style = random.randint(1, 6)
        if style == 1:
            name = random.choice(_DIRECTIVE_NOUNS)
        elif style == 2:
            name = f"{random.choice(_DIRECTIVE_ADJECTIVES)} {random.choice(_DIRECTIVE_NOUNS)}"
        elif style == 3:
            name = f"{random.choice(_DIRECTIVE_VERBS)} {random.choice(_DIRECTIVE_NOUNS)}"
        elif style == 4:
            name = f"{random.choice(_DIRECTIVE_NOUNS)} {random.choice(_DIRECTIVE_MODIFIERS)}"
        elif style == 5:
            name = (
                f"{random.choice(_DIRECTIVE_ADJECTIVES)} "
                f"{random.choice(_DIRECTIVE_NOUNS)} "
                f"{random.choice(_DIRECTIVE_MODIFIERS)}"
            )
        else:
            name = (
                f"{random.choice(_DIRECTIVE_VERBS)} "
                f"{random.choice(_DIRECTIVE_ADJECTIVES)} "
                f"{random.choice(_DIRECTIVE_NOUNS)}"
            )
        if name not in existing_names:
            return name
    return f"Directive {random.randint(1000, 9999)}"


# ---------------------------------------------------------------------------
# Active roster helpers
# ---------------------------------------------------------------------------

def _is_active(member: discord.Member) -> bool:
    """Return True if member is not in Reserves."""
    return not any(getattr(r, "id", 0) == RESERVES_ROLE_ID for r in getattr(member, "roles", []))


def _member_role_names(member: discord.Member) -> set:
    return {(getattr(r, "name", "") or "").strip() for r in getattr(member, "roles", [])}


_KT_LEADER_PRIORITY = (
    "Watch Master",
    "Watch Captain",
    "Watch Lieutenant",
    "Watch Sergeant",
)


def _resolve_kt_leader_for_package(pkg: dict, guild: "discord.Guild | None") -> tuple["discord.Member | None", str | None]:
    """Resolve KT leader by role precedence among active members of assigned KT.

    Preference order: Watch Master > Watch Captain > Watch Lieutenant > Watch Sergeant.
    Ties are deterministic by display name then member id, with assigned captain preferred
    when role precedence is equal.
    """
    kt_name = pkg.get("assigned_kt")
    if not guild or not kt_name:
        return None, None

    from .forge_ops import _resolve_killteam_for_member

    assigned_captain_id = pkg.get("assigned_captain_id")
    candidates: list[tuple[int, int, str, int, discord.Member, str]] = []

    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        if _resolve_killteam_for_member(m) != kt_name:
            continue

        roles = _member_role_names(m)
        for rank_idx, leader_role in enumerate(_KT_LEADER_PRIORITY):
            if leader_role in roles:
                captain_bias = -1 if assigned_captain_id and int(assigned_captain_id) == int(m.id) else 0
                candidates.append(
                    (rank_idx, captain_bias, (m.display_name or "").lower(), int(m.id), m, leader_role)
                )
                break

    if not candidates:
        return None, None

    candidates.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    best = candidates[0]
    return best[4], best[5]


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

    Availability is driven only by active non-LOA members. Specialist roles no
    longer cascade-exclude when a cadre leader is on LOA.
    """
    def _is_loa(m: discord.Member) -> bool:
        return any(getattr(r, "id", 0) == LOA_ROLE_ID for r in getattr(m, "roles", []))

    active = _active_members(guild)  # already excludes Reserves
    present: set = set()

    # Build available roles from non-LOA active members
    for m in active:
        if _is_loa(m):
            continue
        present.update(_member_role_names(m))

    return present


def _get_active_role_counts(guild: discord.Guild) -> dict:
    """Return {role_name: count} for non-LOA, non-Reserves active members.

    Counts how many eligible members hold each role. Used by _draw_requirements
    to allow duplicate role requirements up to the number of available holders.
    Only roles held by active non-LOA members are counted.
    """
    def _is_loa(m: discord.Member) -> bool:
        return any(getattr(r, "id", 0) == LOA_ROLE_ID for r in getattr(m, "roles", []))
    active = _active_members(guild)
    counts: dict = {}
    for m in active:
        if _is_loa(m):
            continue
        for rn in _member_role_names(m):
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
    rep_tier = _rep_tier_for_strat(rep)
    pos_count, neg_count = _strat_counts_for_rep_tier(rep_tier)

    # Omega-Strat: YOLO is redundant (Omega already has 1-life rules)
    omega_excluded = {"You Only Live Once", "Fatality"} if "Omega" in mode else set()
    # Globally blacklisted stratagems across all modes.
    mode_excluded = {
        "Great Responsibility",
        "Fatality",
        "No Delays",
        "Corrosion",
        "Beset",
        "Personal Quarry",
        "Posthumous Proliferation",
        "Mine Field",
        "Hunted",
        "Armour Malfunction",
        "Split Up",
        "Squad Unity",
        "Press the Attack",
        "No Apothecaries",
        "Clever Foe",
        "Aggravated Assault",
        "Bleary Sniper",
        "Broken Bulwark",
        "Fallen Vanguard",
        "Heavy Burden",
        "Tactical Weakness",
        "Booby Trap",
        "Depleted Armour",
        "Empathy",
        "Equipment Malfunction",
        "Fatal Contamination",
        "Hazardous Environment",
        "Shadow of the Warp"
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
# Bladeguard is KT-command tier but still requires Blademaster assignment
_CADRE_SPECIALIST_ROLES = set(
    _TIER_ROLES[_REQ_TIER_COMPANY_COMMAND] + _TIER_ROLES[_REQ_TIER_HC]
) | {"Bladeguard"}

_OMEGA_REQ_TIERS = {
    _REQ_TIER_COMPANY_COMMAND,
    _REQ_TIER_HC,
}


def _omega_ranked_requirement_limit(mode: str) -> int | None:
    if "Omega" not in mode:
        return None
    return 2


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

    # Configured directive caps: Hard-Strat max 3 required roles, Omega-Strat hard max 2.
    max_reqs = 2 if "Omega" in mode else 3
    max_hc = 1

    weights = [2 ** (max_reqs - i) for i in range(1, max_reqs + 1)]
    if random.random() < _requirement_no_req_chance():
        return (_REQ_TIER_NO_REQ, [])

    target_count = random.choices(range(1, max_reqs + 1), weights=weights)[0]

    tiers, tier_weights = zip(*_requirement_slot_tier_weights())
    chosen: list = []
    chosen_counts: dict = {}  # role -> times already drawn
    hc_count = 0
    omega_req_count = 0
    omega_req_limit = _omega_ranked_requirement_limit(mode)
    # Per-role hard caps that override holder count.
    # Default: no duplicate rank requirements. Explicit exceptions can repeat.
    role_caps = {
        "Watch Veteran": 2,
        "Bladeguard": 2,
    }

    for _ in range(target_count * 5):
        if len(chosen) >= target_count:
            break
        tier = random.choices(tiers, weights=tier_weights)[0]
        if tier == _REQ_TIER_HC and hc_count >= max_hc:
            continue
        if omega_req_limit is not None and tier in _OMEGA_REQ_TIERS and omega_req_count >= omega_req_limit:
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
        if tier in _OMEGA_REQ_TIERS:
            omega_req_count += 1

    if not chosen:
        return (_REQ_TIER_NO_REQ, [])

    # Determine highest tier for briefing template selection
    tier_order = [
        _REQ_TIER_VETERAN,
        _REQ_TIER_OATHSWORN,
        _REQ_TIER_KT_COMMAND,
        _REQ_TIER_COMPANY_COMMAND,
        _REQ_TIER_HC,
    ]
    role_to_tier = {}
    for t in tier_order:
        for r in _TIER_ROLES[t]:
            role_to_tier[r] = t
    highest = max(chosen, key=lambda r: tier_order.index(role_to_tier.get(r, _REQ_TIER_VETERAN)))
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
    rep_tier = _rep_tier_for_strat(rep)
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
    existing_codes: set,
    existing_names: set,
    rep: float,
    graph: dict,
    active_strats: list,
    templates: dict,
    available_roles: set,
    ops_list: list,
) -> dict:
    # Pick random node for narrative context.
    world_type_missions: dict = graph["world_type_missions"]
    eligible_nodes = [
        n for n in graph["nodes"]
        if world_type_missions.get(n["type"], [])
    ]
    node = random.choice(eligible_nodes)
    world_type = node["type"]
    mission_pool = list(dict.fromkeys(op["id"] for op in ops_list if op.get("id") is not None))
    if not mission_pool:
        raise ValueError("No operations with valid IDs are available for package generation.")
    mission_id = random.choice(mission_pool)

    op_data = next((o for o in ops_list if o["id"] == mission_id), {})
    intel_lapse_forced = bool(op_data.get("intel_lapse_forced", False))
    classification = _OBJECTIVE_CLASSIFICATION.get(op_data.get("objective_type", ""), "STRIKE")

    if ENABLE_OMEGA_PACKAGES:
        hard_weight, omega_weight = _mode_draw_weights()
        mode = random.choices(["Hard-Strat", "Omega-Strat"], weights=[hard_weight, omega_weight])[0]
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
    directive_code = _generate_directive_code(existing_codes)
    directive_name = _generate_directive_name(existing_names)

    return {
        "id": pid,
        "directive_code": directive_code,
        "directive_name": directive_name,
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
        "forum_thread_id": None,         # thread ID of directive post inside forum channel
        "forum_parent_id": None,         # parent forum channel ID used for this directive
        "forum_created_at": None,        # timestamp when directive forum thread was created
        "generated_at": now.isoformat(),
        "deadline": deadline.isoformat(),
        "completed_at": None,
        "submitted_by": None,
        "aar_link": None,
        "aar_record_id": None,
        "aar_message_id": None,
    }


async def generate_packages(guild: discord.Guild, actor: discord.Member = None) -> list:
    """Generate a batch of strike directives. Returns list of directive dicts."""
    async with _TP_LOCK:
        data = _load_tp()
        rep = data.get("rep", 0.0)

        graph = _load_graph()
        active_strats = _load_stratagems()
        templates = _load_briefing_templates()
        ops_list = _load_operations()
        available_roles = _get_active_role_counts(guild)

        kt_count = _count_active_kts(guild)
        multiplier = _select_package_multiplier(rep)
        count = kt_count * multiplier

        now_utc = datetime.now(timezone.utc)
        batch_id = _generate_unique_batch_id(data, now_utc)

        existing_ids = set(data["packages"].keys())
        existing_codes = {p.get("directive_code", "") for p in data["packages"].values() if p.get("directive_code")}
        existing_names = {p.get("directive_name", "") for p in data["packages"].values() if p.get("directive_name")}
        new_packages = []
        for _ in range(count):
            pkg = _generate_single_package(
                existing_ids, existing_codes, existing_names,
                rep, graph, active_strats, templates, available_roles, ops_list
            )
            existing_ids.add(pkg["id"])
            if pkg.get("directive_code"):
                existing_codes.add(pkg["directive_code"])
            if pkg.get("directive_name"):
                existing_names.add(pkg["directive_name"])
            data["packages"][pkg["id"]] = pkg
            data["cycle"]["total"] += 1
            new_packages.append(pkg)
        data["cycle"]["generated_at"] = now_utc.isoformat()
        data["cycle"]["batch_id"] = batch_id
        # Stamp each package with the batch ID for reliable cycle scoping
        for pkg in new_packages:
            pkg["batch_id"] = batch_id
        # Record generation timestamp for weekly quota tracking
        _record_batch_generation_time(data["cycle"], now_utc)
        _save_tp(data)

    # Gap 1 — Notify general fortress channel when WM generates packages
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    general_channel_id = config_tp.get("general_channel_id")
    if general_channel_id:
        general_channel = guild.get_channel(int(general_channel_id)) if guild else None
        if general_channel or _is_debug_mode():
            count = len(new_packages)
            wm_flavor = [
                "The Watch Master has received intelligence packets from Ordo Xenos. Await your orders \u2014 prepare for deployment.\n -# **Faster way:** Use `/queue_strike` to skip the planning and get auto-assigned to a directive ready for immediate deployment.",
                "Astropathic relay inbound. Ordo Xenos has transmitted new strike directives to Watch Fortress Jericho. Stand ready, brothers.\n -# **Faster way:** Use `/queue_strike` to skip the planning and get auto-assigned to a directive ready for immediate deployment.",
                "Orders inbound from Ordo Xenos. The Watch Master is reviewing strike directives. Deployment briefings to follow.\n -# **Faster way:** Use `/queue_strike` to skip the planning and get auto-assigned to a directive ready for immediate deployment.",
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
                text="ᴄʟᴇᴀʀᴀɴᴄᴇ: sᴄᴀʀʟᴇᴛ",
                icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
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
                title=f"{_DW_EMOJI} sᴛʀɪᴋᴇ ᴅɪʀᴇᴄᴛɪᴠᴇs ᴅɪsᴛʀɪʙᴜᴛᴇᴅ {_DW_EMOJI}",
                description=flavor,
                color=0xC4A030,
            )
            if actor:
                dist_embed.set_author(
                    name=f"Distributed by {actor.display_name}",
                    icon_url=actor.display_avatar.url if actor.display_avatar else None,
                )
            dist_embed.set_footer(
                text="ᴄʟᴇᴀʀᴀɴᴄᴇ: ᴏʙsɪᴅɪᴀɴ",
                icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
            )
            _dist_img_path = os.path.join(_ASSETS_DIR, "distributed to captains.jpg")
            if os.path.exists(_dist_img_path):
                _dist_file = discord.File(_dist_img_path, filename="distributed_to_captains.jpg")
                dist_embed.set_image(url="attachment://distributed_to_captains.jpg")
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
    """Accept a distributed directive for a captain/lieutenant's company.

    Legacy call sites may pass ``kt_name``; it is ignored in the company-scoped
    acceptance flow.
    """
    from .roster_ops import _get_member_company_name

    actor_company = _get_member_company_name(captain_member) or company_name
    if not actor_company:
        return False, "You must have a company role to accept strike directives."
    if not _is_captain_or_lt(captain_member):
        return False, "Only Watch Captains or Watch Lieutenants may accept strike directives for a company."

    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Directive `{package_id}` not found."
        if pkg["status"] not in (STATUS_DISTRIBUTED,):
            return False, f"Directive `{package_id}` is not available for assignment (status: {pkg['status']})."

        new_status = STATUS_RECRUITING

        pkg["assigned_kt"] = None
        pkg["assigned_company"] = actor_company
        pkg["assigned_captain_id"] = captain_member.id
        pkg["status"] = new_status
        pkg["sgt_accept_message_id"] = None
        pkg["sgt_accept_channel_id"] = None

        # Init entity stats
        stats = data["entity_stats"]
        if actor_company not in stats["companies"]:
            stats["companies"][actor_company] = {"completed": 0, "failed": 0, "rep_earned": 0.0}

        _save_tp(data)

    # Notify command channel with assignment order mention.
    await _notify_kt_assigned(package_id, "", pkg, guild, fully_active=False, captain=captain_member)

    # New lifecycle: assignment directly opens recruitment in the KT thread.
    await _ensure_directive_forum_thread(package_id, guild, pkg=pkg)
    await _post_signup_embed(package_id, guild, complier=captain_member)

    req_roles = pkg.get("required_roles", [])
    cadre_reqs = [r for r in req_roles if r in _CADRE_SPECIALIST_ROLES]
    if cadre_reqs:
        await _notify_cadre_leaders_needed(package_id, cadre_reqs, guild)

    return True, f"Directive `{package_id}` accepted for {actor_company}; recruitment is now active."


async def assign_specialist(
    package_id: str,
    specialist_member: discord.Member,
    cadre_leader: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Attach a specialist to a directive. Returns (success, message)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Directive `{package_id}` not found."
        if pkg["status"] not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            return False, f"Directive `{package_id}` cannot accept a specialist attachment (status: {pkg['status']})."

        # Omega directives must keep console players at <= 2 total (signed + specialists).
        mode = pkg.get("mode", "")
        if "Omega" in mode:
            resolved_guild = guild or _get_guild_from_bot()
            if not resolved_guild:
                return False, "Guild context unavailable to validate Omega platform limits."
            sp_platform = _tp_get_player_platform(specialist_member)
            if not sp_platform:
                return False, "Omega directives require a PC/Console role before specialist attachment."
            if sp_platform == "console" and _tp_console_count(pkg, resolved_guild) >= 2:
                return False, "This Omega directive already has the maximum 2 console players."

        eligible, reason = _member_can_remain_attached_to_directive(
            specialist_member,
            pkg,
            guild,
            "specialist",
        )
        if not eligible:
            return False, reason

        # Check specialist not already locked on another package
        active_statuses = {STATUS_RECRUITING, STATUS_DEPLOYED}
        for p in data["packages"].values():
            if (
                specialist_member.id in p.get("signed_up", [])
                and p["id"] != package_id
                and p["status"] in active_statuses
            ):
                return False, f"{specialist_member.display_name} is already signed up for directive `{p['id']}`."
            if (specialist_member.id in p.get("assigned_specialist_ids", [])
                    and p["id"] != package_id
                    and p["status"] in active_statuses):
                return False, f"{specialist_member.display_name} is already attached to directive `{p['id']}`."

        pkg.setdefault("assigned_specialist_ids", [])
        if specialist_member.id in pkg.get("signed_up", []):
            return False, (
                f"{specialist_member.display_name} is already signed up on this directive. "
                "No specialist attachment is needed."
            )
        if specialist_member.id in pkg["assigned_specialist_ids"]:
            return False, f"{specialist_member.display_name} is already attached to directive `{package_id}`."

        specialist_slots = _specialist_slots_allowed(pkg)
        current_specialists = len(pkg.get("assigned_specialist_ids", []))
        if specialist_slots <= 0:
            return False, f"Directive `{package_id}` does not require specialist attachment."
        if current_specialists >= specialist_slots:
            return False, (
                f"Directive `{package_id}` already has all required specialists "
                f"({current_specialists}/{specialist_slots})."
            )

        cadre_roles_for_leader = [
            r for r in (pkg.get("required_roles", []) or [])
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(cadre_leader, r)
        ]
        if not cadre_roles_for_leader:
            return False, "This directive has no specialist requirements for your cadre."

        cadre_assigned = 0
        for uid in pkg.get("assigned_specialist_ids", []):
            m = guild.get_member(uid) if guild else None
            if not m:
                continue
            m_roles = _member_role_names(m)
            if any(r in m_roles for r in cadre_roles_for_leader):
                cadre_assigned += 1
        if cadre_assigned >= len(cadre_roles_for_leader):
            return False, (
                f"Your cadre assignment slots for directive `{package_id}` are already filled "
                f"({cadre_assigned}/{len(cadre_roles_for_leader)})."
            )

        # Hard cap: specialists count toward strike team size and cannot exceed capacity.
        mode = pkg.get("mode", "")
        total_capacity = 3 if "Hard" in mode else 5
        current_total = len(pkg.get("signed_up", [])) + len(pkg.get("assigned_specialist_ids", []))
        if current_total >= total_capacity:
            return False, (
                f"Directive `{package_id}` is already at full capacity "
                f"({current_total}/{total_capacity})."
            )

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

    await _remove_member_from_strike_queue(specialist_member.id)
    await _refresh_signup_embed_for_package(package_id, guild)

    # Gap 2 — Ping specialist in their cadre channel
    await _notify_specialist_assigned(specialist_member, package_id, pkg, guild, cadre_leader=cadre_leader)

    return True, (
        f"{specialist_member.display_name} attached to directive `{package_id}`. "
        f"Status: `{pkg['status']}`."
    )


async def unassign_specialist(
    package_id: str,
    specialist_member: discord.Member,
    cadre_leader: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Detach a specialist from a directive. Returns (success, message)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Directive `{package_id}` not found."
        if pkg["status"] not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            return False, f"Directive `{package_id}` cannot remove specialists (status: {pkg['status']})."

        assigned_ids = pkg.get("assigned_specialist_ids", [])
        if specialist_member.id not in assigned_ids:
            return False, f"{specialist_member.display_name} is not attached to directive `{package_id}`."

        cadre_roles_for_leader = [
            r for r in (pkg.get("required_roles", []) or [])
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(cadre_leader, r)
        ]
        if not cadre_roles_for_leader:
            return False, "This directive has no specialist requirements for your cadre."

        specialist_roles = _member_role_names(specialist_member)
        if not any(r in specialist_roles for r in cadre_roles_for_leader):
            return False, "You can only unassign specialists that belong to your cadre requirements."

        pkg["assigned_specialist_ids"] = [
            uid for uid in assigned_ids if int(uid) != int(specialist_member.id)
        ]
        pkg.setdefault("specialist_assigners", {})
        pkg["specialist_assigners"].pop(str(specialist_member.id), None)

        if pkg.get("status") == STATUS_DEPLOYED and not _check_deployed(pkg, guild):
            pkg["status"] = STATUS_RECRUITING

        _save_tp(data)

    return True, (
        f"{specialist_member.display_name} detached from directive `{package_id}`. "
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
    """Submit a completed directive. Returns (success, message)."""

    def _directive_display(_pkg: dict | None, fallback_id: str) -> str:
        if not isinstance(_pkg, dict):
            return f"`{fallback_id}`"
        code = _pkg.get("directive_code") or fallback_id
        name = str(_pkg.get("directive_name") or "").strip()
        if name:
            return f"`{code}` — {name}"
        return f"`{code}`"

    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            # Try directive_code lookup
            _upper = package_id.upper()
            for _pid_key, _p in data["packages"].items():
                if (_p.get("directive_code") or "").upper() == _upper:
                    package_id = _pid_key
                    pkg = _p
                    break
        if not pkg:
            return False, f"Directive `{package_id}` not found."

        directive_display = _directive_display(pkg, package_id)

        if pkg["status"] not in (STATUS_DEPLOYED, STATUS_RECRUITING):
            return False, f"Directive {directive_display} cannot be submitted (status: `{pkg['status']}`)."

        # Check deadline
        deadline = datetime.fromisoformat(pkg["deadline"])
        if datetime.now(timezone.utc) > deadline:
            return False, f"Directive {directive_display} has expired (deadline passed)."

        # Submitter must be signed up OR be command of the assigned KT/company
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        submitter_kt = _resolve_killteam_for_member(submitter)
        submitter_company = _get_member_company_name(submitter)
        submitter_roles = _member_role_names(submitter)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in submitter_roles)
        is_command = (
            submitter_kt == pkg.get("assigned_kt")
            and (_has_role(submitter, "Watch Sergeant") or _has_role(submitter, "Bladeguard"))
        )
        is_signed_up = submitter.id in pkg.get("signed_up", [])
        is_specialist_attached = submitter.id in pkg.get("assigned_specialist_ids", [])
        is_rostered_participant = is_signed_up or is_specialist_attached

        assigned_company = pkg.get("assigned_company")

        if not (is_rostered_participant or is_command or submitter_company == assigned_company or is_hc):
            return False, (
                f"You do not have permission to submit directive {directive_display}. "
                f"Submission requires: being signed up or attached as a specialist, KT command (Sergeant/Bladeguard), "
                f"same-company membership, or High Command."
            )

        # Package must be DEPLOYED (all reqs met)
        if pkg["status"] != STATUS_DEPLOYED:
            mode = pkg.get("mode", "")
            min_p = 2 if "Hard" in mode else 3
            signed = len(pkg.get("signed_up", []))
            return False, (
                f"Directive {directive_display} is not yet deployed. "
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
                "AAR team roster does not match directive roster "
                f"(expected {len(expected_brothers)}, got {len(aar_brothers)})."
            )

        expected_mission = str(pkg.get("mission_id") or "")
        for op in (_load_operations() or []):
            if op.get("id") == pkg.get("mission_id"):
                expected_mission = str(op.get("name") or expected_mission)
                break
        aar_mission = str(aar_record.get("mission") or aar_record.get("mission_name") or "")
        if _canonical_mission_name(aar_mission) != _canonical_mission_name(expected_mission):
            return False, "AAR mission does not match strike directive mission."

        expected_diff = _expected_difficulty_for_mode(pkg.get("mode", ""))
        aar_diff = str(aar_record.get("difficulty_class") or "").strip().lower()
        if aar_diff != expected_diff:
            return False, "AAR difficulty does not match strike directive mode."

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
            stats["kill_teams"].setdefault(kt, {"completed": 0, "failed": 0, "rep_earned": 0.0})
            stats["kill_teams"][kt]["completed"] += 1
        if company:
            stats["companies"].setdefault(company, {"completed": 0, "failed": 0, "rep_earned": 0.0})
            stats["companies"][company]["completed"] += 1

        data["cycle"]["completed"] += 1
        rep_before = float(data.get("rep", _REP_NEUTRAL) or _REP_NEUTRAL)
        completion_rep_delta = _rep_delta_for_package(pkg, STATUS_COMPLETED)
        fortress_command_bonus = _compute_fortress_command_bonus(pkg, guild)
        _apply_rep_delta(data, completion_rep_delta + fortress_command_bonus)
        rep_after = float(data.get("rep", _REP_NEUTRAL) or _REP_NEUTRAL)
        pkg["rep_before"] = rep_before
        pkg["rep_after"] = rep_after

        split_allocations = _compute_participation_rep_allocations(pkg, guild, completion_rep_delta)
        company_bonus = _compute_company_command_bonus(pkg, guild)
        cadre_bonus_allocations = _compute_cadre_leader_bonus_allocations(pkg, guild)
        _apply_entity_rep_allocations(
            data,
            pkg,
            split_allocations,
            completion_rep_delta,
            company_bonus=company_bonus,
            cadre_bonus_allocations=cadre_bonus_allocations,
        )
        merged_cadre_allocations = dict(split_allocations.get("cadres", {}) or {})
        for cadre_name, delta in (cadre_bonus_allocations or {}).items():
            merged_cadre_allocations[cadre_name] = round(
                float(merged_cadre_allocations.get(cadre_name, 0.0)) + float(delta or 0.0),
                2,
            )
        pkg["rep_allocations"] = {
            "kill_teams": split_allocations.get("kill_teams", {}),
            "cadres": merged_cadre_allocations,
            "companies": (
                {company: round(float(completion_rep_delta) + float(company_bonus), 2)}
                if company else {}
            ),
            "fortress_bonus": round(float(fortress_command_bonus), 2),
        }

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

    # If this completion made this batch terminal, post summary for that batch.
    try:
        _final_data = _load_tp()
        cycle = _final_data.setdefault("cycle", {})
        pkg_batch_id = _batch_id_for_package(pkg)
        if _should_post_batch_summary(_final_data, pkg_batch_id):
            await _post_batch_summary(guild, _final_data, batch_id=pkg_batch_id)
            _mark_batch_summary_posted(cycle, pkg_batch_id, datetime.now(timezone.utc))
            _save_tp(_final_data)
    except Exception as exc:
        _g.logger.debug(f"[TP] Batch summary check failed after submission: {exc}")

    return True, f"Directive {directive_display} marked completed. Ordo Xenos standing updated."


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


_BATCH_SUMMARY_CHANNEL_ID = 1512929774970998945  # legacy fallback only


def _random_strike_image_file(filename_hint: str = "report") -> "tuple[discord.File | None, str | None]":
    """Pick a random image from assets/strike directive images/.

    Returns (discord.File, attachment_filename) or (None, None) if unavailable.
    """
    try:
        if not os.path.isdir(_STRIKE_DIRECTIVE_IMAGES_DIR):
            return None, None
        images = [
            f for f in os.listdir(_STRIKE_DIRECTIVE_IMAGES_DIR)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ]
        if not images:
            return None, None
        chosen = random.choice(images)
        path = os.path.join(_STRIKE_DIRECTIVE_IMAGES_DIR, chosen)
        safe_name = f"{filename_hint}_{chosen.replace(' ', '_')}"
        return discord.File(path, filename=safe_name), safe_name
    except Exception:
        return None, None

_HONORS_PATH = os.path.join(DATA_DIR, "honors.json")

# ---------------------------------------------------------------------------
# KT title tiers (ordered lowest → highest; index = tier level)
# ---------------------------------------------------------------------------
_KT_TITLE_TIERS = ["Unproven", "Initiated", "Vigilant", "Sworn", "Hallowed", "Eternal"]
_COMPANY_TITLE_TIERS = ["Unrecorded", "Marked", "Recognized", "Honored", "Exalted", "Storied"]
_CADRE_TITLE_TIERS = {
    "Blades": ["Unblooded", "Keen-Edged", "Honed Arsenal", "Master of Blades", "Relic Weapon Adepts", "Living Arsenal"],
    "Armory": ["Uncalibrated", "Tempered", "Machine-Blessed", "Artificer Proven", "Relic-Smiths", "Omnissian Exemplars"],
    "Apothecarion": ["Unsworn Chirurgeons", "Field Medicae", "Gene-Guarded", "Sanguine Stewards", "Vitae Keepers", "Apothecarion Ascendant"],
    "Librarius": ["Unattuned", "Warded Minds", "Empyric Disciplined", "Veil Wardens", "Lexicanum Exemplars", "Oracular Ascendant"],
    "Reclusiam": ["Unanointed", "Catechized", "Zeal-Bound", "Crozius Proven", "Litany Exemplars", "Voice of the Emperor"],
}

# Cadre sections for highcom report: (section_name, [role_names_in_cadre])
# Castellan omitted by design. Huntmaster not a cadre.
_CADRE_SECTIONS = [
    ("Armory Deployments",       ["Watch Techmarine", "Honored Dreadnought", "Venerable Dreadnought"]),
    ("Apothecarion Interventions", ["Watch Apothecary"]),
    ("Reclusiam Attachments",    ["Watch Chaplain"]),
    ("Librarius Operations",     ["Watch Librarian"]),
    ("Blade Detachments",        ["Bladeguard", "First Blade"]),
]


def _load_honors() -> dict:
    try:
        if not os.path.exists(_HONORS_PATH):
            return {"kill_teams": {}, "companies": {}, "cadres": {}}
        with open(_HONORS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
            payload.setdefault("kill_teams", {})
            payload.setdefault("companies", {})
            payload.setdefault("cadres", {})
            return payload
    except Exception:
        return {"kill_teams": {}, "companies": {}, "cadres": {}}


def _save_honors(data: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_HONORS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _g.logger.error(f"[TP] Failed to save honors.json: {e}")


def _compute_honors(tp_data: dict) -> dict:
    """Compute current KT, company, and cadre title tiers from the rolling 28-day window.

    Scoring (dual metric, rep-weighted):
      rep_index   (0-5): based on net rep delta earned in window
      comp_index  (0-5): based on completions in window
      final_index = round(0.75 * rep_index + 0.25 * comp_index), clamped 0-5

    Company tiers additionally gate on distinct contributing KTs in window.
    Titles go up AND down based on re-evaluation.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=28)

    # KT thresholds: (min_rep_delta, min_completions) → index
    _KT_REP_THRESHOLDS  = [0.0, 2.0, 8.0, 15.0, 27.0, 38.0]   # index 0-5
    _KT_COMP_THRESHOLDS = [0,   1,   3,   6,   9,    12]       # index 0-5
    # Company thresholds
    _CO_REP_THRESHOLDS  = [0.0, 5.0, 13.0, 23.0, 33.0, 46.0]
    _CO_COMP_THRESHOLDS = [0,   3,   6,   10,   14,   18]
    # Company KT contributor gates per tier index (minimum distinct KTs)
    _CO_KT_GATES = [0, 1, 2, 3, 4, 4]
    _CADRE_REP_THRESHOLDS = {
        "Blades": [0.0, 4.0, 11.0, 20.0, 31.0, 44.0],
        "Armory": [0.0, 3.0, 8.0, 15.0, 23.0, 33.0],
        "Apothecarion": [0.0, 2.0, 6.0, 11.0, 17.0, 25.0],
        "Librarius": [0.0, 2.0, 6.0, 11.0, 17.0, 25.0],
        "Reclusiam": [0.0, 2.0, 6.0, 11.0, 17.0, 25.0],
    }

    def _rep_index(delta: float, thresholds: list) -> int:
        idx = 0
        for i, t in enumerate(thresholds):
            if delta >= t:
                idx = i
        return idx

    def _comp_index(count: int, thresholds: list) -> int:
        idx = 0
        for i, t in enumerate(thresholds):
            if count >= t:
                idx = i
        return idx

    # Scan packages within 28-day window
    kt_completions: dict[str, int] = {}
    kt_rep_earned:  dict[str, float] = {}
    co_completions: dict[str, int] = {}
    co_rep_earned:  dict[str, float] = {}
    co_kt_contributors: dict[str, set] = {}
    cadre_completions: dict[str, int] = {}
    cadre_rep_earned: dict[str, float] = {}

    for pkg in tp_data.get("packages", {}).values():
        if pkg.get("status") != STATUS_COMPLETED:
            continue
        completed_at_str = pkg.get("completed_at")
        if not completed_at_str:
            continue
        try:
            completed_at = datetime.fromisoformat(completed_at_str)
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if completed_at < cutoff:
            continue

        kt = pkg.get("assigned_kt")
        company = pkg.get("assigned_company")
        rep_delta = float(pkg.get("rep_after", 0.0) or 0.0) - float(pkg.get("rep_before", 0.0) or 0.0)
        rep_alloc = pkg.get("rep_allocations") or {}
        kt_alloc = rep_alloc.get("kill_teams") if isinstance(rep_alloc, dict) else {}
        company_alloc = rep_alloc.get("companies") if isinstance(rep_alloc, dict) else {}
        cadre_alloc = rep_alloc.get("cadres") if isinstance(rep_alloc, dict) else {}

        if kt:
            kt_completions[kt] = kt_completions.get(kt, 0) + 1
        if isinstance(kt_alloc, dict) and kt_alloc:
            for kt_name, delta in kt_alloc.items():
                kt_rep_earned[kt_name] = kt_rep_earned.get(kt_name, 0.0) + max(float(delta or 0.0), 0.0)
        elif kt:
            kt_rep_earned[kt] = kt_rep_earned.get(kt, 0.0) + max(rep_delta, 0.0)

        if company:
            co_completions[company] = co_completions.get(company, 0) + 1
            if isinstance(company_alloc, dict) and company in company_alloc:
                co_rep_earned[company] = co_rep_earned.get(company, 0.0) + max(float(company_alloc.get(company) or 0.0), 0.0)
            else:
                co_rep_earned[company] = co_rep_earned.get(company, 0.0) + max(rep_delta, 0.0)

            if isinstance(kt_alloc, dict) and kt_alloc:
                co_kt_contributors.setdefault(company, set()).update(str(name) for name in kt_alloc.keys())
            elif kt:
                co_kt_contributors.setdefault(company, set()).add(kt)

        if isinstance(cadre_alloc, dict) and cadre_alloc:
            for cadre_name, delta in cadre_alloc.items():
                bucket = str(cadre_name)
                cadre_rep_earned[bucket] = cadre_rep_earned.get(bucket, 0.0) + max(float(delta or 0.0), 0.0)
                cadre_completions[bucket] = cadre_completions.get(bucket, 0) + 1

    # Score KTs
    kt_results: dict[str, dict] = {}
    all_kts = set(kt_completions) | set(kt_rep_earned)
    for kt in all_kts:
        ri = _rep_index(kt_rep_earned.get(kt, 0.0), _KT_REP_THRESHOLDS)
        ci = _comp_index(kt_completions.get(kt, 0), _KT_COMP_THRESHOLDS)
        final = min(5, round(0.75 * ri + 0.25 * ci))
        kt_results[kt] = {
            "tier": _KT_TITLE_TIERS[final],
            "tier_index": final,
            "completions_28d": kt_completions.get(kt, 0),
            "rep_earned_28d": round(kt_rep_earned.get(kt, 0.0), 2),
            "last_evaluated": now.isoformat(),
        }

    # Score companies
    co_results: dict[str, dict] = {}
    all_cos = set(co_completions) | set(co_rep_earned)
    for company in all_cos:
        ri = _rep_index(co_rep_earned.get(company, 0.0), _CO_REP_THRESHOLDS)
        ci = _comp_index(co_completions.get(company, 0), _CO_COMP_THRESHOLDS)
        raw_final = min(5, round(0.75 * ri + 0.25 * ci))
        # Apply KT contributor gate — cap tier if not enough distinct KTs
        kt_count = len(co_kt_contributors.get(company, set()))
        while raw_final > 0 and kt_count < _CO_KT_GATES[raw_final]:
            raw_final -= 1
        co_results[company] = {
            "tier": _COMPANY_TITLE_TIERS[raw_final],
            "tier_index": raw_final,
            "completions_28d": co_completions.get(company, 0),
            "rep_earned_28d": round(co_rep_earned.get(company, 0.0), 2),
            "contributing_kts": kt_count,
            "last_evaluated": now.isoformat(),
        }

    # Score cadres
    cadre_results: dict[str, dict] = {}
    all_cadres = set(cadre_completions) | set(cadre_rep_earned)
    for cadre_name in all_cadres:
        thresholds = _CADRE_REP_THRESHOLDS.get(cadre_name)
        titles = _CADRE_TITLE_TIERS.get(cadre_name)
        if not thresholds or not titles:
            continue
        ri = _rep_index(cadre_rep_earned.get(cadre_name, 0.0), thresholds)
        ci = _comp_index(cadre_completions.get(cadre_name, 0), _KT_COMP_THRESHOLDS)
        final = min(5, round(0.75 * ri + 0.25 * ci))
        cadre_results[cadre_name] = {
            "tier": titles[final],
            "tier_index": final,
            "completions_28d": cadre_completions.get(cadre_name, 0),
            "rep_earned_28d": round(cadre_rep_earned.get(cadre_name, 0.0), 2),
            "last_evaluated": now.isoformat(),
        }

    return {"kill_teams": kt_results, "companies": co_results, "cadres": cadre_results}


def _all_packages_terminal(data: dict) -> bool:
    """Return True if every directive in the store is in a terminal state."""
    return all(
        p["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
        for p in data.get("packages", {}).values()
    )


# ---------------------------------------------------------------------------
# Cycle-close reports (three scopes) + honors
# ---------------------------------------------------------------------------

async def _post_batch_summary(guild: discord.Guild, data: dict, batch_id: Optional[str] = None) -> bool:
    """Post cycle-close reports to three channels and update KT/company honors.

    batch_id: "BATCH-YYYYMMDD" to report on a specific batch. If None, uses the
    batch_id stored in cycle.batch_id, or derives it from package generated_at
    dates for legacy packages that predate the batch_id field.
    """
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    packages = data.get("packages", {})
    rep = data.get("rep", _REP_NEUTRAL)

    batch_id = _resolve_summary_batch_id(data, batch_id)
    if not batch_id:
        return False

    batch_pkgs = [p for p in packages.values() if _batch_id_for_package(p) == batch_id]
    if not batch_pkgs:
        return False

    posted_any = False

    total = len(batch_pkgs)
    completed = [p for p in batch_pkgs if p["status"] == STATUS_COMPLETED]
    failed    = [p for p in batch_pkgs if p["status"] == STATUS_FAILED]
    lapsed    = [p for p in batch_pkgs if p["status"] == STATUS_LAPSED]
    completion_rate = len(completed) / total if total else 0

    # Rep delta this cycle — derived from all terminal directives so failures/lapses
    # (which carry negative deltas) are included and the starting value is accurate
    # even when no directives completed.
    terminal_pkgs = completed + failed + lapsed
    rep_delta = sum(_rep_delta_for_package(p, p["status"]) for p in terminal_pkgs)
    rep_end   = rep
    rep_start = rep_end - rep_delta

    if completion_rate >= 0.75:
        color = 0x2ECC71
    elif completion_rate >= 0.4:
        color = 0xF39C12
    else:
        color = 0x8B0000

    standing_bar  = _standing_skull_bar(rep)
    standing_name = _standing_state_name(rep)

    # Human-readable batch label for embed headers: "Directive Batch 08 Jun – 15 Jun 2026"
    # Extract only the 8-digit date portion; batch IDs may include a suffix (e.g. BATCH-20260608-01).
    _batch_date_str = batch_id[6:14] if batch_id and batch_id.startswith("BATCH-") else ""
    try:
        _batch_date = datetime.strptime(_batch_date_str, "%Y%m%d")
        _deadlines = [
            datetime.fromisoformat(p["deadline"]) for p in batch_pkgs if p.get("deadline")
        ]
        if _deadlines:
            _cycle_end = max(_deadlines)
            _batch_label = f"Directive Batch {_batch_date.strftime('%d %b')} – {_cycle_end.strftime('%d %b %Y')}"
        else:
            _batch_label = f"Directive Batch {_batch_date.strftime('%d %b %Y')}"
    except Exception:
        _batch_label = batch_id or "Directive Batch"

    # ── HONORS EVALUATION (done first so results can be appended to fw_embed) ──
    _honors_kt_changes:  list[str] = []
    _honors_co_changes:  list[str] = []
    _honors_cadre_changes: list[str] = []
    try:
        old_honors = _load_honors()
        _new_honors = _compute_honors(data)

        _TIER_UP   = "⬆"
        _TIER_DOWN = "⬇"
        _TIER_SAME = "—"

        for kt_name, new_data in sorted(_new_honors["kill_teams"].items()):
            old_data  = old_honors.get("kill_teams", {}).get(kt_name, {})
            old_tier  = old_data.get("tier", "Unproven")
            new_tier  = new_data["tier"]
            old_idx   = _KT_TITLE_TIERS.index(old_tier) if old_tier in _KT_TITLE_TIERS else 0
            new_idx   = new_data["tier_index"]
            if new_idx == old_idx:
                continue  # no change — skip
            verb = "reached" if new_idx > old_idx else "dropped to"
            _honors_kt_changes.append(f"**{kt_name}** {verb} **{new_tier}**")

        for co_name, new_data in sorted(_new_honors["companies"].items()):
            old_data  = old_honors.get("companies", {}).get(co_name, {})
            old_tier  = old_data.get("tier", "Unrecorded")
            new_tier  = new_data["tier"]
            old_idx   = _COMPANY_TITLE_TIERS.index(old_tier) if old_tier in _COMPANY_TITLE_TIERS else 0
            new_idx   = new_data["tier_index"]
            if new_idx == old_idx:
                continue  # no change — skip
            verb = "reached" if new_idx > old_idx else "dropped to"
            _honors_co_changes.append(f"**{co_name}** {verb} **{new_tier}**")

        for cadre_name, new_data in sorted((_new_honors.get("cadres") or {}).items()):
            old_data = old_honors.get("cadres", {}).get(cadre_name, {})
            old_tier = old_data.get("tier", (_CADRE_TITLE_TIERS.get(cadre_name) or [new_data["tier"]])[0])
            titles = _CADRE_TITLE_TIERS.get(cadre_name)
            if not titles:
                continue
            new_tier = new_data["tier"]
            old_idx = titles.index(old_tier) if old_tier in titles else 0
            new_idx = new_data["tier_index"]
            if new_idx == old_idx:
                continue
            verb = "reached" if new_idx > old_idx else "dropped to"
            _honors_cadre_changes.append(f"**{cadre_name}** {verb} **{new_tier}**")

        _save_honors(_new_honors)
    except Exception as exc:
        _g.logger.warning(f"[TP] Honors evaluation failed: {exc}")

    # ── 1. FORTRESS-WIDE REPORT ──────────────────────────────────────────
    general_channel_id = config_tp.get("general_channel_id")
    if general_channel_id:
        try:
            gen_ch = guild.get_channel(int(general_channel_id))
            if not gen_ch:
                gen_ch = await guild.fetch_channel(int(general_channel_id))
        except Exception:
            gen_ch = None

        if gen_ch or _is_debug_mode():
            _FORTRESS_FLAVOR_STRONG = [
                "The Deathwatch held the line. Ordo Xenos acknowledges Watch Fortress Jericho's service.",
                "The xenos threat was answered. Sectors cleared, directives executed — the Watch endures.",
                "Watch Fortress Jericho's kill teams struck true. The Ordos are satisfied.",
            ]
            _FORTRESS_FLAVOR_MIXED = [
                "The Jericho Reach remains contested. Some directives went unanswered, but the Watch did not yield entirely.",
                "A mixed accounting reaches Ordos Xenos. Victories tempered by gaps in the line.",
                "Brothers answered the call — not all of them. Ordo Xenos notes both the resolved and the lapsed.",
            ]
            _FORTRESS_FLAVOR_POOR = [
                "The Watch stumbled. Directives lapsed, operations failed — the Ordos grow impatient.",
                "Too many directives went cold. Ordo Xenos logs its disappointment with Watch Fortress Jericho.",
                "A dark accounting. The Jericho Reach demands more than this cycle delivered.",
            ]
            if completion_rate >= 0.75:
                flavor = random.choice(_FORTRESS_FLAVOR_STRONG)
            elif completion_rate >= 0.4:
                flavor = random.choice(_FORTRESS_FLAVOR_MIXED)
            else:
                flavor = random.choice(_FORTRESS_FLAVOR_POOR)

            fw_embed = discord.Embed(
                title=f"{_DW_EMOJI} ꜰᴏʀᴛʀᴇss ᴅᴇᴘʟᴏʏᴍᴇɴᴛ ᴄʏᴄʟᴇ ᴄʟᴏsᴇᴅ {_DW_EMOJI}",
                description=flavor,
                color=color,
            )
            fw_embed.set_author(name=f"ᴏʀᴅᴏ xᴇɴᴏs · {_batch_label}")

            fw_embed.add_field(
                name="▸ Cycle Results",
                value=(
                    f"**Directives Issued:** {total}\n"
                    f"**Completed:** {len(completed)}  ·  "
                    f"**Failed:** {len(failed)}  ·  "
                    f"**Lapsed:** {len(lapsed)}\n"
                    f"**Completion Rate:** {completion_rate * 100:.0f}%"
                ),
                inline=False,
            )
            _bar_before = _standing_skull_bar(rep_start)
            _name_before = _standing_state_name(rep_start)
            _before_line = f"{_bar_before} **{_name_before}**" if _bar_before else f"**{_name_before}**"
            _after_line  = f"{standing_bar} **{standing_name}**" if standing_bar else f"**{standing_name}**"
            fw_embed.add_field(
                name="▸ Ordo Xenos Standing",
                value=(
                    f"{_before_line} `{rep_start:.2f}`\n"
                    f"→ {_after_line} `{rep_end:.2f}`\n"
                    f"**Delta:** `{rep_delta:+.2f}`"
                ),
                inline=False,
            )

            fw_embed.set_footer(
                text="ᴄʟᴇᴀʀᴀɴᴄᴇ: sᴄᴀʀʟᴇᴛ  ·  Honours reflect rolling 28-day window",
                icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
            )
            # Append honors fields to this embed (evaluated in section 4 above)
            if _honors_kt_changes:
                kt_hon_block = "\n".join(_honors_kt_changes)
                if len(kt_hon_block) > 1024:
                    kt_hon_block = kt_hon_block[:1020] + "\n…"
                fw_embed.add_field(name="▸ Kill Team Honours", value=kt_hon_block, inline=False)
            if _honors_co_changes:
                co_hon_block = "\n".join(_honors_co_changes)
                if len(co_hon_block) > 1024:
                    co_hon_block = co_hon_block[:1020] + "\n…"
                fw_embed.add_field(name="▸ Company Honours", value=co_hon_block, inline=False)
            if _honors_cadre_changes:
                cadre_hon_block = "\n".join(_honors_cadre_changes)
                if len(cadre_hon_block) > 1024:
                    cadre_hon_block = cadre_hon_block[:1020] + "\n…"
                fw_embed.add_field(name="▸ Cadre Honours", value=cadre_hon_block, inline=False)
            _fw_img, _fw_img_name = _random_strike_image_file("fortress")
            if _fw_img and _fw_img_name:
                fw_embed.set_image(url=f"attachment://{_fw_img_name}")
            try:
                await _notify_send(gen_ch, guild, content=f"<@&{WATCH_BROTHER_ROLE_ID}>", embed=fw_embed, **_file_kwarg(_fw_img))
                posted_any = True
                _g.logger.info("[TP] Fortress-wide cycle report posted.")
            except Exception as exc:
                _g.logger.warning(f"[TP] Fortress-wide report send failed: {exc}")

    # ── 2. KT REPORTS ────────────────────────────────────────────────────
    # Group completed/failed packages by KT for per-KT report embeds.
    # Channel resolved via _get_award_announcement_channel (same as active-flow embeds).
    kt_pkgs_map: dict[str, list] = {}
    for p in batch_pkgs:
        kt = p.get("assigned_kt")
        if kt:
            kt_pkgs_map.setdefault(kt, []).append(p)

    # Build a KT-name → sample-member map in a single pass so channel resolution
    # below is O(members) total rather than O(KTs × members).
    from .forge_ops import _get_award_announcement_channel, _resolve_killteam_for_member
    kt_sample_member: dict[str, object] = {}
    for _m in (guild.members if guild else []):
        if _m.bot or not _is_active(_m):
            continue
        _mkt = _resolve_killteam_for_member(_m)
        if _mkt and _mkt not in kt_sample_member:
            kt_sample_member[_mkt] = _m

    for kt_name, kt_batch in kt_pkgs_map.items():
        kt_completed = [p for p in kt_batch if p["status"] == STATUS_COMPLETED]
        kt_failed    = [p for p in kt_batch if p["status"] == STATUS_FAILED]
        if not kt_completed and not kt_failed:
            continue  # nothing terminal to report for this KT

        kt_rep_contributed = sum(
            float(((p.get("rep_allocations") or {}).get("kill_teams", {}) or {}).get(kt_name, 0.0) or 0.0)
            if ((p.get("rep_allocations") or {}).get("kill_teams", {}) or {}).get(kt_name) is not None
            else (float(p.get("rep_after", 0.0) or 0.0) - float(p.get("rep_before", 0.0) or 0.0))
            for p in kt_completed
        )
        kt_rate = len(kt_completed) / len(kt_batch) if kt_batch else 0
        if kt_rate >= 0.75:
            kt_color = 0x2ECC71
        elif kt_rate >= 0.4:
            kt_color = 0xF39C12
        else:
            kt_color = 0x8B0000

        kt_embed = discord.Embed(
            title=f"{_DW_EMOJI} ᴋɪʟʟ ᴛᴇᴀᴍ ᴅᴇᴘʟᴏʏᴍᴇɴᴛ ʀᴇᴄᴏʀᴅ {_DW_EMOJI}",
            color=kt_color,
        )
        kt_embed.set_author(name=f"{kt_name}  ·  {_batch_label}")

        kt_embed.add_field(
            name="▸ Cycle Summary",
            value=(
                f"**Directives Assigned:** {len(kt_batch)}\n"
                f"**Completed:** {len(kt_completed)}  ·  **Failed:** {len(kt_failed)}\n"
                f"**Rep Contributed:** `{kt_rep_contributed:+.2f}`"
            ),
            inline=False,
        )

        # Per-directive detail for completed ops
        if kt_completed:
            completed_lines = []
            for p in kt_completed:
                code = p.get("directive_code") or p["id"]
                name = p.get("directive_name", "")
                node = p.get("node", "")
                cls  = p.get("classification", "")
                mode_short = "HARD-STRAT" if "Hard" in p.get("mode", "") else "OMEGA-STRAT"
                intel = " · ⚠ Intel Lapse" if p.get("intel_lapse") else ""
                req_roles = p.get("required_roles", [])
                req_str = f" · Roles: {', '.join(req_roles)}" if req_roles else ""
                strats = p.get("stratagems", {}).get("core", [])
                strat_lines = [_strat_line(s) for s in strats]
                strat_block = ("```diff\n" + "\n".join(strat_lines) + "\n```") if strat_lines else ""
                completed_lines.append(
                    f"**{code}** — {name}\n"
                    f"  `{node}` · {cls} · {mode_short}{intel}{req_str}\n"
                    + strat_block
                )
            block = "\n".join(completed_lines)
            if len(block) > 1024:
                block = block[:1020] + "\n…"
            kt_embed.add_field(name=f"▸ Completed Operations ({len(kt_completed)})", value=block, inline=False)

        if kt_failed:
            fail_lines = [
                f"`{p.get('directive_code') or p['id']}` {p.get('directive_name', '')} — {p.get('node', '')}".strip()
                for p in kt_failed
            ]
            fail_block = "\n".join(fail_lines)
            if len(fail_block) > 1024:
                fail_block = fail_block[:1020] + "\n…"
            kt_embed.add_field(name=f"▸ Failed Operations ({len(kt_failed)})", value=fail_block, inline=False)

        kt_embed.set_footer(
            text="ᴄʟᴇᴀʀᴀɴᴄᴇ: ᴍᴀɢᴇɴᴛᴀ",
            icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
        )

        # Resolve KT channel via _get_award_announcement_channel — same resolver used
        # by _post_signup_embed. Prefers KT_ROLE_CHANNEL_MAP override, then active forum
        # thread fuzzy-matched by KT name, then falls back to SERVICE_STUDS_CHANNEL_ID.
        kt_ch = None
        _sample = kt_sample_member.get(kt_name)
        if _sample:
            try:
                kt_ch = await _get_award_announcement_channel(_sample, guild)
            except Exception:
                kt_ch = None

        # Resolve KT Discord role mention
        kt_role_mention = ""
        _kt_role = discord.utils.find(lambda r: r.name.lower() == kt_name.lower(), guild.roles) if guild else None
        if _kt_role:
            kt_role_mention = _kt_role.mention

        if kt_ch or _is_debug_mode():
            try:
                _kt_img, _kt_img_name = _random_strike_image_file(f"kt_{kt_name}")
                if _kt_img and _kt_img_name:
                    kt_embed.set_image(url=f"attachment://{_kt_img_name}")
                await _notify_send(kt_ch, guild, content=kt_role_mention or None, embed=kt_embed, **_file_kwarg(_kt_img))
                posted_any = True
            except Exception as exc:
                _g.logger.warning(f"[TP] KT report send failed for {kt_name}: {exc}")

    # ── 3. HIGHCOM REPORT ────────────────────────────────────────────────
    highcom_channel_id = (
        config_tp.get("highcom_audit_channel_id")
        or config_tp.get("highcom_strategium_channel_id")
        or _BATCH_SUMMARY_CHANNEL_ID
    )
    highcom_role_id    = config_tp.get("highcom_role_id")
    try:
        hc_ch = guild.get_channel(int(highcom_channel_id))
        if not hc_ch:
            hc_ch = await guild.fetch_channel(int(highcom_channel_id))
    except Exception:
        hc_ch = None

    if hc_ch or _is_debug_mode():
        hc_embed = discord.Embed(
            title=f"{_DW_EMOJI} ᴄᴏᴍᴍᴀɴᴅ sᴛʀᴀᴛᴀɢᴇᴍ ᴀᴜᴅɪᴛ {_DW_EMOJI}",
            color=color,
        )
        hc_embed.set_author(name=f"ᴏʀᴅᴏ xᴇɴᴏs · {_batch_label}")

        # Theatre summary
        _before_line2 = f"{_standing_skull_bar(rep_start)} **{_standing_state_name(rep_start)}**" if _standing_skull_bar(rep_start) else f"**{_standing_state_name(rep_start)}**"
        _after_line2  = f"{standing_bar} **{standing_name}**" if standing_bar else f"**{standing_name}**"
        hc_embed.add_field(
            name="▸ Theatre Summary",
            value=(
                f"**Directives Issued:** {total}\n"
                f"**Completed:** {len(completed)}  ·  "
                f"**Failed:** {len(failed)}  ·  "
                f"**Lapsed:** {len(lapsed)}\n"
                f"**Completion Rate:** {completion_rate * 100:.0f}%\n"
                f"**Standing:** {_before_line2} `{rep_start:.2f}` → {_after_line2} `{rep_end:.2f}` (`{rep_delta:+.2f}`)"
            ),
            inline=False,
        )

        # Per-company
        company_stats = _batch_company_stats(batch_pkgs)
        if company_stats:
            co_lines = []
            for cname, cdata in sorted(company_stats.items()):
                c_done = cdata.get("completed", 0)
                c_fail = cdata.get("failed", 0)
                c_total = c_done + c_fail
                icon = "🟢" if c_fail == 0 and c_total > 0 else ("🟡" if c_fail < c_done else "🔴")
                co_lines.append(f"{icon} **{cname}** — {c_done}/{c_total} completed" + (f"  ·  {c_fail} failed" if c_fail else ""))
            hc_embed.add_field(name="▸ Companies", value="\n".join(co_lines) or "—", inline=False)

        # Cadre sections — only include cadres that participated
        for section_name, cadre_roles in _CADRE_SECTIONS:
            # Directives requiring any of this cadre's roles
            cadre_required = [
                p for p in batch_pkgs
                if any(r in (p.get("required_roles") or []) for r in cadre_roles)
            ]
            if not cadre_required:
                continue  # cadre had no requisitions this cycle
            cadre_completed  = [p for p in cadre_required if p["status"] == STATUS_COMPLETED]
            cadre_failed     = [p for p in cadre_required if p["status"] == STATUS_FAILED]
            cadre_lapsed     = [p for p in cadre_required if p["status"] == STATUS_LAPSED]
            # Unfilled = required but the directive expired without the role being filled
            cadre_unfilled   = [
                p for p in cadre_required
                if p["status"] in (STATUS_FAILED, STATUS_LAPSED)
                and not any(
                    uid and (
                        lambda m: m and any(r in _member_role_names(m) for r in cadre_roles)
                    )(guild.get_member(int(uid)) if guild else None)
                    for uid in (p.get("assigned_specialist_ids", []) or [])
                )
            ]
            icon = "🟢" if not cadre_failed and not cadre_lapsed else ("🟡" if cadre_completed else "🔴")
            cadre_val = (
                f"{icon} **Assisted:** {len(cadre_required)}  ·  "
                f"**Completed:** {len(cadre_completed)}  ·  "
                f"**Failed/Lapsed:** {len(cadre_failed) + len(cadre_lapsed)}"
            )
            if cadre_unfilled:
                cadre_val += f"\n⚠ Unfilled requisitions: {len(cadre_unfilled)}"
            hc_embed.add_field(name=f"▸ {section_name}", value=cadre_val, inline=False)

        # Lapsed
        if lapsed:
            lapsed_lines = [
                f"`{p.get('directive_code') or p['id']}` {p.get('directive_name', '')} — {p.get('world_type', '')}".strip()
                for p in lapsed
            ]
            lapsed_block = "\n".join(lapsed_lines)
            if len(lapsed_block) > 1024:
                lapsed_block = lapsed_block[:1020] + "\n…"
            hc_embed.add_field(name="▸ Lapsed — Never Assigned", value=lapsed_block, inline=False)

        hc_embed.set_footer(
            text="ᴄʟᴇᴀʀᴀɴᴄᴇ: ᴠᴇʀᴍɪʟɪᴏɴ",
            icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
        )
        _hc_img, _hc_img_name = _random_strike_image_file("highcom")
        if _hc_img and _hc_img_name:
            hc_embed.set_image(url=f"attachment://{_hc_img_name}")
        hc_ping = f"<@&{highcom_role_id}>" if highcom_role_id else f"<@&{WATCH_MASTER_ROLE_ID}>"
        try:
            await _notify_send(hc_ch, guild, content=hc_ping, embed=hc_embed, **_file_kwarg(_hc_img))
            posted_any = True
            _g.logger.info("[TP] Highcom command audit posted.")
        except Exception as exc:
            _g.logger.warning(f"[TP] Highcom report send failed: {exc}")

    # ── 4. (Honors already evaluated and appended to fortress-wide report above) ──

    # ── 5. CADRE REPORTS ─────────────────────────────────────────────────
    # One embed per cadre posted to their staff channel.
    # Pings the cadre member roles (not just the leader).
    # Skipped if no cadre members deployed this cycle (required or voluntary).
    # Cadre → (config_key, [member roles], [leader roles], section_label)
    _CADRE_REPORT_DEFS = [
        ("techmarine", ["Watch Techmarine", "Honored Dreadnought", "Venerable Dreadnought"],
         ["Forgemaster"], "Armory Deployments"),
        ("apothecary",  ["Watch Apothecary"],
         ["Chief Apothecary"], "Apothecarion Interventions"),
        ("chaplain",    ["Watch Chaplain"],
         ["High Chaplain"], "Reclusiam Attachments"),
        ("librarian",   ["Watch Librarian"],
         ["Void Warden"], "Librarius Operations"),
        ("champion",    ["Bladeguard", "First Blade"],
         ["Blademaster"], "Blade Detachments"),
    ]
    cadre_cfg = config_tp.get("cadre_channels", {})
    for cadre_key, cadre_member_roles, cadre_leader_roles, section_label in _CADRE_REPORT_DEFS:
        cadre_ch_id = cadre_cfg.get(cadre_key)
        if not cadre_ch_id:
            continue  # channel not configured yet

        def _member_has_cadre_role(uid: int) -> bool:
            m = guild.get_member(uid) if guild else None
            return bool(m and any(r in _member_role_names(m) for r in cadre_member_roles))

        # Packages where cadre role was formally required (terminal only)
        cadre_required_pkgs = [
            p for p in batch_pkgs
            if any(r in (p.get("required_roles") or []) for r in cadre_member_roles)
            and p["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
        ]
        # Packages where a cadre member deployed but role wasn't required
        cadre_voluntary_pkgs = [
            p for p in batch_pkgs
            if not any(r in (p.get("required_roles") or []) for r in cadre_member_roles)
            and any(_member_has_cadre_role(int(uid)) for uid in (p.get("assigned_specialist_ids") or []) + (p.get("signed_up") or []) if uid)
            and p["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
        ]

        # Skip if no deployments at all
        if not cadre_required_pkgs and not cadre_voluntary_pkgs:
            continue

        cadre_color = 0x2ECC71
        for p in cadre_required_pkgs:
            if p["status"] in (STATUS_FAILED, STATUS_LAPSED):
                cadre_color = 0xF39C12 if any(q["status"] == STATUS_COMPLETED for q in cadre_required_pkgs) else 0x8B0000
                break

        c_embed = discord.Embed(
            title=f"{_DW_EMOJI} {_smallcaps(section_label)} — ᴄʏᴄʟᴇ ᴅᴇʙʀɪᴇꜰ {_DW_EMOJI}",
            color=cadre_color,
        )
        c_embed.set_author(name=f"ᴏʀᴅᴏ xᴇɴᴏs · {_batch_label}")

        def _fmt_cadre_pkg(p: dict) -> str:
            code = p.get("directive_code") or p["id"]
            name = p.get("directive_name", "")
            kt   = p.get("assigned_kt", "—")
            status_icon = {STATUS_COMPLETED: "✅", STATUS_FAILED: "❌", STATUS_LAPSED: "⬛"}.get(p["status"], "🔲")
            deployed_ids = list(dict.fromkeys(
                (p.get("assigned_specialist_ids") or []) + (p.get("signed_up") or [])
            ))
            deployed_members = [
                guild.get_member(int(uid)) for uid in deployed_ids
                if uid and guild and guild.get_member(int(uid))
                and any(r in _member_role_names(guild.get_member(int(uid))) for r in cadre_member_roles)
            ]
            names_str = ", ".join(m.display_name for m in deployed_members) if deployed_members else "— (unfilled)"
            return (
                f"{status_icon} **{code}**{' — ' + name if name else ''}  ·  {kt}\n"
                f"  ↳ {names_str}"
            )

        if cadre_required_pkgs:
            req_lines = [_fmt_cadre_pkg(p) for p in cadre_required_pkgs]
            req_block = "\n".join(req_lines)
            if len(req_block) > 1024:
                req_block = req_block[:1020] + "\n…"
            c_embed.add_field(name="▸ Required & Deployed", value=req_block, inline=False)

        if cadre_voluntary_pkgs:
            vol_lines = [_fmt_cadre_pkg(p) for p in cadre_voluntary_pkgs]
            vol_block = "\n".join(vol_lines)
            if len(vol_block) > 1024:
                vol_block = vol_block[:1020] + "\n…"
            c_embed.add_field(name="▸ Additional Deployments", value=vol_block, inline=False)

        c_embed.set_footer(
            text="ᴄʟᴇᴀʀᴀɴᴄᴇ: ᴏʙsɪᴅɪᴀɴ",
            icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
        )
        _c_img, _c_img_name = _random_strike_image_file(f"cadre_{cadre_key}")
        if _c_img and _c_img_name:
            c_embed.set_image(url=f"attachment://{_c_img_name}")

        try:
            cadre_ch = guild.get_channel(int(cadre_ch_id))
            if not cadre_ch:
                cadre_ch = await guild.fetch_channel(int(cadre_ch_id))
        except Exception:
            cadre_ch = None

        if cadre_ch or _is_debug_mode():
            # Ping cadre member roles + leader roles by Discord role mention
            ping_mentions = []
            for role_name in cadre_member_roles + cadre_leader_roles:
                role_obj = discord.utils.find(lambda r: r.name == role_name, guild.roles) if guild else None
                if role_obj:
                    ping_mentions.append(role_obj.mention)
            # Deduplicate while preserving order
            seen: set = set()
            unique_pings = [x for x in ping_mentions if not (x in seen or seen.add(x))]
            ping_str = " ".join(unique_pings) if unique_pings else None
            try:
                await _notify_send(cadre_ch, guild, content=ping_str, embed=c_embed, **_file_kwarg(_c_img))
                posted_any = True
                _g.logger.info(f"[TP] Cadre report posted for {section_label}.")
            except Exception as exc:
                _g.logger.warning(f"[TP] Cadre report send failed for {section_label}: {exc}")
        cadre_ch_id = cadre_cfg.get(cadre_key)
        if not cadre_ch_id:
            continue  # channel not configured yet (e.g. champion)

    return posted_any


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
        expired_ids: list[str] = []
        touched_batch_ids: set[str] = set()

        cycle = data.setdefault("cycle", {})

        for pkg in data["packages"].values():
            if pkg["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED):
                continue
            deadline = datetime.fromisoformat(pkg["deadline"])
            remaining = deadline - now
            if remaining > timedelta(0):
                continue

            if pkg["status"] in (STATUS_DEPLOYED, STATUS_RECRUITING, STATUS_PENDING_SGT):
                pkg["status"] = STATUS_FAILED
                data["cycle"]["failed"] += 1
                _apply_rep_delta(data, _rep_delta_for_package(pkg, STATUS_FAILED))
                # Update entity stats
                kt = pkg.get("assigned_kt")
                company = pkg.get("assigned_company")
                if kt:
                    data["entity_stats"]["kill_teams"].setdefault(kt, {"completed": 0, "failed": 0, "rep_earned": 0.0})
                    data["entity_stats"]["kill_teams"][kt]["failed"] += 1
                if company:
                    data["entity_stats"]["companies"].setdefault(company, {"completed": 0, "failed": 0, "rep_earned": 0.0})
                    data["entity_stats"]["companies"][company]["failed"] += 1
                changed = True
                expired_ids.append(pkg["id"])
                touched_batch_ids.add(_batch_id_for_package(pkg))

            elif pkg["status"] == STATUS_DISTRIBUTED:
                pkg["status"] = STATUS_LAPSED
                data["cycle"]["lapsed"] += 1
                _apply_rep_delta(data, _rep_delta_for_package(pkg, STATUS_LAPSED))
                changed = True
                expired_ids.append(pkg["id"])
                touched_batch_ids.add(_batch_id_for_package(pkg))

        if changed:
            _save_tp(data)

    try:
        _latest = _load_tp()
        _now = datetime.now(timezone.utc)
        _cycle = _latest.setdefault("cycle", {})
        batch_ids = sorted({_batch_id_for_package(p) for p in _latest.get("packages", {}).values()})
        for bid in batch_ids:
            if bid == "BATCH-UNKNOWN":
                continue
            if _batch_warning_sent_at(_cycle, bid):
                continue
            batch_pkgs = [p for p in _latest.get("packages", {}).values() if _batch_id_for_package(p) == bid]
            actionable = [
                p for p in batch_pkgs
                if p.get("status") in (
                    STATUS_UNASSIGNED,
                    STATUS_DISTRIBUTED,
                    STATUS_PENDING_SGT,
                    STATUS_RECRUITING,
                    STATUS_DEPLOYED,
                )
            ]
            nearest_deadline = min(
                (datetime.fromisoformat(p["deadline"]) for p in actionable if p.get("deadline")),
                default=None,
            )
            if nearest_deadline is None:
                continue
            if nearest_deadline.tzinfo is None:
                nearest_deadline = nearest_deadline.replace(tzinfo=timezone.utc)
            remaining = nearest_deadline - _now
            if timedelta(0) < remaining <= _GENERAL_WARNING_WINDOW:
                sent = await _send_single_batch_warning(guild, _latest, bid, _now)
                if sent:
                    _mark_batch_warning_sent(_cycle, bid, _now)
                    _save_tp(_latest)
    except Exception as exc:
        logger = getattr(_g, "logger", None)
        if logger:
            logger.debug(f"[TP] General batch warning pass failed: {exc}")

    if changed:
        # Delete Discord embeds for all expired directives
        for _eid in expired_ids:
            try:
                await _delete_package_messages(_eid, guild)
            except Exception as exc:
                _g.logger.debug(f"[TP] Cleanup failed for expired directive {_eid}: {exc}")

        # Fire rep embed update
        try:
            await _update_ox_rep_embed(guild)
        except Exception as exc:
            _g.logger.debug(f"[TP] Rep embed update failed: {exc}")

        # If expiry made batches terminal, post only the newest touched terminal batch.
        try:
            _final_data = _load_tp()
            cycle = _final_data.setdefault("cycle", {})
            candidate_batches = [
                bid for bid in touched_batch_ids
                if _should_post_batch_summary(_final_data, bid)
            ]
            if candidate_batches:
                bid = max(candidate_batches, key=_batch_recency_key)
                await _post_batch_summary(guild, _final_data, batch_id=bid)
                _mark_batch_summary_posted(cycle, bid, datetime.now(timezone.utc))
                _save_tp(_final_data)
        except Exception as exc:
            _g.logger.debug(f"[TP] Batch summary check failed after expiry: {exc}")


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

_DISTRIBUTE_FLAVOR = [
    "Astropathic relay inbound. Watch Captains to the strategium — {count} strike directive{s} transmitted from Ordo Xenos to Watch Fortress Jericho. Await your assignments.\nUse `/view_strike_directives` to review and assign to your Kill Teams.",
    "Ordo Xenos datalink established. {count} strike directive{s} received and logged to the strategium. Watch Captains, move to review.\nUse `/view_strike_directives` to assign directives to your Kill Teams.",
    "Intelligence packet cleared Vermilion. {count} strike directive{s} routed to Watch Fortress Jericho command. Captains — your orders await.\nUse `/view_strike_directives` to review and assign.",
]

_KT_ASSIGN_FLAVOR = [
    "Data-inload received, brother. Strike Directive `{pid}` has been assigned to {kt}. Blackstar is prepped — await final clearance before departure.",
    "Strategic orders received. {kt} has been tasked with Strike Directive `{pid}`. All brothers, stand ready.",
    "Orders transmitted. {kt}, you have your mission — Strike Directive `{pid}` is yours. Await specialist attachment if flagged.",
]

_KT_READY_FLAVOR = [
    "All conditions met. {kt} is cleared for immediate deployment on Strike Directive `{pid}`. Emperor guide your blades.",
    "Deployment authorised. {kt} — Strike Directive `{pid}` is fully active. Blackstar is green.",
    "Final clearance granted. {kt}, Strike Directive `{pid}` is live. Move out.",
]

_CADRE_FLAVOR = {
    "Forgemaster": [
        "{kt} of {company} requires Techmarine or Dreadnought attachment on Strike Directive `{pid}`. Forgemaster — designate your specialist.\nUse `/view_strike_directives` to assign.",
        "Forge-lord, {kt} needs a specialist from your cadre for Strike Directive `{pid}`. Forge-bond required before deployment.\nUse `/view_strike_directives` to assign.",
    ],
    "Chief Apothecary": [
        "{kt} of {company} requires an Apothecary on Strike Directive `{pid}`. Chief Apothecary — designate your brother.\nUse `/view_strike_directives` to assign.",
        "Chief Apothecary, {kt} needs your cadre's hand. Strike Directive `{pid}` cannot deploy without an Apothecary.\nUse `/view_strike_directives` to assign.",
    ],
    "High Chaplain": [
        "Reclusiam requisition raised. {kt} of {company} requires a Chaplain on Strike Directive `{pid}`. High Chaplain — assign from your cadre.\nUse `/view_strike_directives` to assign.",
        "High Chaplain, {kt} needs spiritual authority in the field. Strike Directive `{pid}` awaits your designation.\nUse `/view_strike_directives` to assign.",
    ],
    "Void Warden": [
        "Librarius requisition transmitted. {kt} of {company} requires a Librarian on Strike Directive `{pid}`. Void Warden — assign as required.\nUse `/view_strike_directives` to assign.",
        "Void Warden, the psyker's gift is needed by {kt}. Strike Directive `{pid}` awaits Librarian attachment.\nUse `/view_strike_directives` to assign.",
    ],
    "Castellan": [
        "Watch Keeper requisition flagged. {kt} of {company} requires a Keeper on Strike Directive `{pid}`. Castellan — designate your operative.\nUse `/view_strike_directives` to assign.",
        "Castellan, your intelligence cadre is needed by {kt}. Strike Directive `{pid}` awaits Watch Keeper attachment.\nUse `/view_strike_directives` to assign.",
    ],
    "Blademaster": [
        "Blade requisition raised. {kt} of {company} requires a Blade role on Strike Directive `{pid}`. Blademaster — designate as required.\nUse `/view_strike_directives` to assign.",
        "Blademaster, {kt} needs martial authority on Strike Directive `{pid}`. Blade assignment required before deployment.\nUse `/view_strike_directives` to assign.",
    ],
    "Huntmaster": [
        "Huntmaster, {kt} of {company} requires your personal engagement on Strike Directive `{pid}`. Your direct participation is demanded.\nUse `/view_strike_directives` to assign yourself.",
        "Huntmaster — {kt} is called to the field on Strike Directive `{pid}` and requires you. Await no further orders.\nUse `/view_strike_directives` to assign yourself.",
    ],
}

_CADRE_DEFAULT_FLAVOR = [
    "{kt} of {company} requires specialists on Strike Directive `{pid}`: {roles}. Cadre leaders — assign as required.\nUse `/view_strike_directives` to assign.",
]


async def _notify_kt_assigned(
    package_id: str, kt_name: str, pkg: dict, guild: discord.Guild, fully_active: bool = False, captain: discord.Member = None
) -> None:
    """Post assignment notice in watch command strategium channel."""
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    strategium_channel_id = config_tp.get("watch_command_deployment_channel_id")
    if not strategium_channel_id:
        return

    channel = guild.get_channel(int(strategium_channel_id)) if guild else None
    if not channel and not _is_debug_mode():
        return

    leader_member, leader_role = _resolve_kt_leader_for_package(pkg, guild)
    company_mention = _resolve_company_role_mention(guild, pkg.get("assigned_company"))

    data = _load_tp()
    rep = data.get("rep", 0.0)
    embed = _build_package_embed(pkg, rep, guild=guild)
    if captain:
        embed.set_author(
            name=f"{captain.display_name}",
            icon_url=captain.display_avatar.url if getattr(captain, "display_avatar", None) else None,
        )
    elif leader_member:
        embed.set_author(
            name=f"{leader_member.display_name}",
            icon_url=leader_member.display_avatar.url if getattr(leader_member, "display_avatar", None) else None,
        )
    req_roles = pkg.get("required_roles", [])
    if req_roles:
        req_counts = Counter(req_roles)
        req_lines = []
        for role_name, cnt in req_counts.items():
            req_lines.append(f"• {role_name}" if cnt == 1 else f"• {role_name} x{cnt}")
        embed.add_field(
            name="▸ Required Ranks",
            value="\n".join(req_lines),
            inline=False,
        )
    embed.add_field(
        name="▸ Orders",
        value=(
            f"Accepted by {captain.mention if captain else 'command'} for {pkg.get('assigned_company') or 'assigned company'}. "
            "Company recruitment is now active."
        ),
        inline=False,
    )

    _cls_file = _classification_file(pkg)
    msg = await _notify_send(
        channel,
        guild,
        content=company_mention or (leader_member.mention if leader_member else None),
        embed=embed,
        **_file_kwarg(_cls_file),
    )
    if msg:
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
    "Bladeguard": "champion",
    "First Blade": "champion",
    "Blademaster": "champion",
}

# Fallback constants if not set in config
_CADRE_CHANNEL_FALLBACKS: dict[str, int] = {
    "techmarine": TECHMARINE_STAFF_CHANNEL_ID,
    "librarian": LIBRARIUS_STAFF_CHANNEL_ID,
    "apothecary": APOTHECARY_STAFF_CHANNEL_ID,
    "chaplain": CHAPLAIN_STAFF_CHANNEL_ID,
    "dreadnought": TECHMARINE_STAFF_CHANNEL_ID,
    # champion falls back to watch_command_deployment_channel_id (resolved at runtime)
}


def _get_cadre_channel_id(role: str) -> int | None:
    """Return cadre staff channel ID for a role. Config takes precedence over constants.
    
    For the 'champion' cadre key, falls back to watch_command_deployment_channel_id
    when not explicitly configured.
    """
    cadre_key = _ROLE_TO_CADRE_KEY.get(role)
    if not cadre_key:
        return None
    config_ch = (((_b("CONFIG") or {}).get("target_packages") or {}).get("cadre_channels") or {}).get(cadre_key)
    if config_ch:
        return int(config_ch)
    if cadre_key == "champion":
        # No champion channel configured yet — fall back to watch command deployment channel
        wc = (((_b("CONFIG") or {}).get("target_packages") or {}).get("watch_command_deployment_channel_id"))
        return int(wc) if wc else None
    return _CADRE_CHANNEL_FALLBACKS.get(cadre_key)


async def _notify_specialist_assigned(
    specialist_member: discord.Member, package_id: str, pkg: dict, guild: discord.Guild, cadre_leader: discord.Member = None
) -> None:
    """Post a lightweight assignment notification with a link to the KT directive embed."""
    specialist_roles = _member_role_names(specialist_member)

    signup_channel_id = pkg.get("signup_channel_id")
    signup_message_id = pkg.get("signup_message_id")

    directive_url = None
    if guild and signup_channel_id and signup_message_id:
        directive_url = (
            f"https://discord.com/channels/{guild.id}/"
            f"{int(signup_channel_id)}/{int(signup_message_id)}"
        )

    # Determine the right channel for this specialist — all cadres including blades
    # route through _get_cadre_channel_id. Bladeguard is an exception: they
    # get pinged in their KT's signup channel since they operate at KT level.
    cadre_channel_id = None
    if "Bladeguard" in specialist_roles:
        cadre_channel_id = signup_channel_id
    else:
        for role in specialist_roles:
            ch_id = _get_cadre_channel_id(role)
            if ch_id:
                cadre_channel_id = ch_id
                break

    embed = discord.Embed(
        title=f"{_DW_EMOJI} Specialist Assignment {_DW_EMOJI}",
        color=0xE67E22,
        description=f"{specialist_member.mention} has been attached to directive `{pkg.get('directive_code') or package_id}`.",
    )
    if cadre_leader:
        embed.set_author(
            name=cadre_leader.display_name,
            icon_url=cadre_leader.display_avatar.url if getattr(cadre_leader, "display_avatar", None) else None,
        )
    directive_name = pkg.get("directive_name", "")
    directive_label = f"`{pkg.get('directive_code') or package_id}`"
    if directive_name:
        directive_label += f" - {directive_name}"

    link_line = f"[Open KT Directive]({directive_url})" if directive_url else "KT directive link unavailable"
    embed.add_field(
        name="▸ Directive",
        value=f"{directive_label}\n{link_line}",
        inline=False,
    )
    embed.add_field(
        name="▸ Status",
        value=(
            "You are attached as a specialist and remain locked until completion, failure, lapse, "
            "or cadre leader reassignment."
        ),
        inline=False,
    )

    if cadre_channel_id:
        cadre_channel = guild.get_channel(int(cadre_channel_id)) if guild else None
        if cadre_channel or _is_debug_mode():
            sent_msg = await _notify_send(cadre_channel, guild, content=specialist_member.mention, embed=embed)
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
                            "kind": "assignment_link",
                        })
                        _save_tp(_sp_data)

    # Gap 3 — Update KT sign-up embed with refreshed deployment checks + roster
    if signup_channel_id and signup_message_id:
        try:
            ch = await _resolve_channel(guild, int(signup_channel_id))
            if ch:
                msg = await ch.fetch_message(int(signup_message_id))
                if msg.embeds:
                    mode = pkg.get("mode", "")
                    total_capacity = 3 if "Hard" in mode else 5
                    signed_up = pkg.get("signed_up", [])
                    specialist_ids = pkg.get("assigned_specialist_ids", [])
                    specialist_assigners = pkg.get("specialist_assigners", {})

                    roster_names = []
                    for uid in signed_up:
                        m = guild.get_member(uid) if guild else None
                        roster_names.append(m.display_name if m else str(uid))
                    for uid in specialist_ids:
                        m = guild.get_member(uid) if guild else None
                        name = m.display_name if m else str(uid)
                        assigner_id = specialist_assigners.get(str(uid))
                        if assigner_id and int(assigner_id) != int(uid):
                            a = guild.get_member(assigner_id) if guild else None
                            name += f" _(via {a.display_name if a else str(assigner_id)})_"
                        else:
                            name += " _(specialist)_"
                        roster_names.append(name)

                    roster_total = len(signed_up) + len(specialist_ids)
                    roster_field_name = f"▸ Signed Up ({roster_total}/{total_capacity})"
                    roster_field_value = "\n".join(f"• {n}" for n in roster_names) if roster_names else "—"

                    req_roles = pkg.get("required_roles", [])
                    deploy_lines = [f"**Strike Team Size:** {total_capacity}"]
                    if req_roles and guild:
                        req_display = _resolve_requirements_display(pkg, guild)
                        for role_name, emoji, _who in req_display:
                            deploy_lines.append(f"{emoji} **{role_name}**")
                    elif req_roles:
                        for role_name in req_roles:
                            deploy_lines.append(f"🔲 **{role_name}**")
                    deploy_lines.append("Press **⚔ Comply** to register for this operation.")

                    embed = msg.embeds[0]
                    base_fields = [
                        f for f in embed.fields
                        if not f.name.startswith("▸ Signed Up")
                        and f.name != "▸ Deployment Requirements"
                        and f.name != "▸ Required Ranks"
                        and f.name != "▸ Attached Specialists"
                    ]
                    embed.clear_fields()
                    for f in base_fields:
                        embed.add_field(name=f.name, value=f.value, inline=f.inline)
                    embed.add_field(name="▸ Deployment Requirements", value="\n".join(deploy_lines), inline=False)
                    embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
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
        "Bladeguard": "Blademaster",
        "First Blade": "Blademaster",
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
        _display_id = _pkg_data.get("directive_code") or package_id
        flavor = random.choice(flavor_pool).format(
            pid=_display_id, roles=", ".join(owned_roles), kt=_kt, company=_company
        )

        # Look up the Watch Captain who assigned this directive for the author line
        _captain_id = _pkg_data.get("assigned_captain_id")
        _captain_member = guild.get_member(_captain_id) if (guild and _captain_id) else None

        cadre_embed = discord.Embed(
            title=f"{_DW_EMOJI} sᴘᴇᴄɪᴀʟɪsᴛ ʀᴇǫᴜɪsɪᴛɪᴏɴ {_DW_EMOJI}",
            description=flavor,
            color=0xE67E22,
        )
        if _captain_member:
            cadre_embed.set_author(
                name=f"Assigned by {_captain_member.display_name}",
                icon_url=_captain_member.display_avatar.url if _captain_member.display_avatar else None,
            )
        cadre_embed.set_footer(
            text="ᴄʟᴇᴀʀᴀɴᴄᴇ: ᴏʙsɪᴅɪᴀɴ",
            icon_url="https://cdn.discordapp.com/emojis/1501748904880767147.webp?size=44",
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
_OX_STANDING_EMOJI = "<:OrdoXenosStanding:1513298514913005568>"
_HERESY_EMOJI = "<:whatisthisheresy:1429676711108153384>"

_REP_TIER_LABELS = {
    "censured": "CENSURED",
    "suspect": "SUSPECT",
    "tolerated": "TOLERATED",
    "neutral": "NEUTRAL",
    "favoured": "FAVOURED",
    "endorsed": "ENDORSED",
    "mandated": "MANDATED",
}

_COMMAND_ROLES = {"Watch Captain", "Watch Lieutenant"}


def _clearance_for_member(member: Optional[discord.Member]) -> str:
    if member is None or _is_admin(member):
        return "VERMILION"
    roles = _member_role_names(member)
    if roles & _HC_ROLES:
        return "VERMILION"
    if roles & _COMMAND_ROLES:
        return "OBSIDIAN"
    if roles & _KT_COMMAND_ROLES:
        return "MAGENTA"
    return "SCARLET"


def _rep_display(rep: float) -> str:
    label = _standing_state_name(rep)
    icons = _standing_skull_bar(rep)
    return f"{icons} **{label}** `{rep:.2f}`" if icons else f"**{label}** `{rep:.2f}`"


def _standing_skull_bar(rep: float) -> str:
    """Render compact standing icons.

    0-60 scale:
    - Censured/Suspect/Tolerated use 3/2/1 heresy icons.
    - Neutral uses no icons.
    - Favoured/Endorsed/Mandated use 1/2/3 Ordo Xenos skulls.
    """
    rep_clamped = max(_REP_MIN, min(_REP_MAX, float(rep or _REP_NEUTRAL)))
    if rep_clamped >= 58.0:
        count = 3
        return " ".join([_OX_STANDING_EMOJI] * count)
    if rep_clamped >= 50.0:
        count = 2
        return " ".join([_OX_STANDING_EMOJI] * count)
    if rep_clamped >= 40.0:
        count = 1
        return " ".join([_OX_STANDING_EMOJI] * count)
    if rep_clamped < 10.0:
        count = 3
        return " ".join([_HERESY_EMOJI] * count)
    if rep_clamped < 20.0:
        count = 2
        return " ".join([_HERESY_EMOJI] * count)
    if rep_clamped < 30.0:
        count = 1
        return " ".join([_HERESY_EMOJI] * count)
    return ""


def _standing_state_name(rep: float) -> str:
    """Resolve named standing state from 0..60 bands."""
    rep_clamped = max(_REP_MIN, min(_REP_MAX, float(rep or _REP_NEUTRAL)))
    if rep_clamped < 10.0:
        return _REP_TIER_LABELS["censured"]
    if rep_clamped < 20.0:
        return _REP_TIER_LABELS["suspect"]
    if rep_clamped < 30.0:
        return _REP_TIER_LABELS["tolerated"]
    if rep_clamped < 40.0:
        return _REP_TIER_LABELS["neutral"]
    if rep_clamped < 50.0:
        return _REP_TIER_LABELS["favoured"]
    if rep_clamped < 58.0:
        return _REP_TIER_LABELS["endorsed"]
    return _REP_TIER_LABELS["mandated"]


def _strat_line(strat: dict) -> str:
    t = strat["type"]
    if t == "buff":
        prefix = "+"
    elif t == "debuff":
        prefix = "-"
    else:
        prefix = "~"
    return f"{prefix} {strat['name']}"


# Canonical embed ordering for strike-directive stratagems.
_POSITIVE_MODIFIER_ORDER = [
    "Combat Mastery",
    "Point Blank",
    "Sharpshooter",
    "Larraman Cells",
    "Technological Revolution",
    "Deep Pockets",
    "Harvest of Vitae",
    "Rhythm of Carnage",
    "Unleashed Fury",
    "Camaraderie",
    "Imperial Fervour",
    "Pointed Attack",
    "Avenger",
    "Enemy Sighted",
    "Unshaken",
    "Surgical Strike",
    "We Stand as One",
    "Migraine",
    "Astra Militarum",
    "Beset",
    "Intelligence Lapse",
    "Surplus",
    "Come Prepared",
    "The Emperor Protects",
    "Doomed Offensive",
    "Butcher's Gifts",
    "Measured Mercy",
    "Temporal Boosts",
    "Hallowed Relic",
    "Shockwave Plating",
]

_NEGATIVE_MODIFIER_ORDER = [
    "Suboptimal State",
    "Maintain Distance",
    "Close In",
    "Backup Plan",
    "Heavy Calibre",
    "Hyperopia",
    "Myopia",
    "Survival Training",
    "You Only Live Once",
    "Fatality",
    "Corrosion",
    "No Delays",
    "Tactical Weakness",
    "Aggravated Assault",
    "Fallen Vanguard",
    "Bleary Sniper",
    "Heavy Burden",
    "Broken Bulwark",
    "Buffed Enemies",
    "Hardened Skins",
    "Supremacy of the Strong",
    "Empathy",
    "Great Responsibility",
    "Killer Instinct",
    "Strike Out",
    "Reinforced Cranium",
    "Extreme Challenge",
    "Major Challenge",
    "Meat for the Slaughter",
    "Hunted",
    "Mine Field",
    "Summoner",
    "Effective Taunt",
    "Tsunami",
    "Clever Foe",
    "Coordinated Calls",
    "Depleted Armour",
    "Scavenger",
    "Rationing",
    "Equipment Malfunction",
    "No Apothecaries",
    "Detonation Risk",
    "Press the Attack",
    "Squad Unity",
    "Split Up",
    "Atrophy",
    "On the Clock",
    "Hazardous Environment",
    "Armour Malfunction",
    "Suspicious Behaviour",
    "Booby Trap",
    "Warp Storm",
    "Shadow of the Warp",
    "Microreactor Breach",
    "Enduring Foes",
    "Fatal Contamination",
    "Battlefield Instincts",
    "Spoils of War",
    "Corrupted Relic",
    "Twice the Foe",
    "Coordinated Elimination",
    "Posthumous Proliferation",
    "Personal Quarry",
]

_MODIFIER_NAME_ALIASES = {
    "Enemy Sighter": "Enemy Sighted",
    "Imperial Fevour": "Imperial Fervour",
    "Heavy Caliber": "Heavy Calibre",
    "Hazardous Encironment": "Hazardous Environment",
    "Suspicious Behavior": "Suspicious Behaviour",
    "Microreaptor Breach": "Microreactor Breach",
}

_POSITIVE_MODIFIER_RANK = {name: idx for idx, name in enumerate(_POSITIVE_MODIFIER_ORDER)}
_NEGATIVE_MODIFIER_RANK = {name: idx for idx, name in enumerate(_NEGATIVE_MODIFIER_ORDER)}


def _normalize_modifier_name(name: str) -> str:
    return _MODIFIER_NAME_ALIASES.get(name, name)


def _ordered_modifier_lines(strats: list[dict], positive: bool) -> list[str]:
    rank_map = _POSITIVE_MODIFIER_RANK if positive else _NEGATIVE_MODIFIER_RANK
    filtered = []
    for s in strats:
        stype = (s.get("type") or "").lower()
        if positive and stype != "buff":
            continue
        if not positive and stype != "debuff":
            continue
        raw_name = (s.get("name") or "").replace(" (forced)", "").strip()
        norm_name = _normalize_modifier_name(raw_name)
        filtered.append(norm_name)

    filtered.sort(key=lambda n: (rank_map.get(n, 10_000), n.lower()))
    return [f"• {name}" for name in filtered]



def _resolve_requirements_display(pkg: dict, guild: "discord.Guild | None") -> list[tuple[str, str, str]]:
    """Greedy assignment of required roles to participants.

    Returns list of (role_name, emoji, who_name) for each requirement slot.
    Specialists fill cadre roles first; signed-up members can fill line roles only
    when they explicitly hold the required role.
    Each participant satisfies at most one slot.
    """
    req_roles = pkg.get("required_roles", [])
    if not req_roles or not guild:
        return []

    signed_up = pkg.get("signed_up", [])
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    specialist_assigners = pkg.get("specialist_assigners", {})

    # Build ordered participant list: specialists first, then signed-up
    participants = []
    for uid in specialist_ids:
        m = guild.get_member(uid)
        if m:
            assigner_id = specialist_assigners.get(str(uid))
            assigner = guild.get_member(assigner_id) if assigner_id else None
            suffix = f" _(via {assigner.display_name})_" if (assigner and int(assigner_id) != int(uid)) else " _(specialist)_"
            participants.append((m, _member_role_names(m), m.display_name + suffix))
    for uid in signed_up:
        m = guild.get_member(uid)
        if m:
            participants.append((m, _member_role_names(m), m.display_name))

    # Process hardest-to-fill slots first (fewest matching participants), while
    # preserving original order in the returned list.
    req_slots = list(enumerate(req_roles))
    slot_priority = {}
    for idx, req in req_slots:
        candidate_count = sum(1 for _m, roles, _d in participants if req in roles)
        slot_priority[idx] = (candidate_count, -_RANK_SENIORITY_MAP.get(req, -1), idx)

    used: set[int] = set()
    results_by_idx: dict[int, tuple[str, str, str]] = {}

    for idx, req in sorted(req_slots, key=lambda item: slot_priority[item[0]]):
        filled_by = None
        # Exact role match required for all requirement types.
        for m, roles, display in participants:
            if m.id in used:
                continue
            if req in roles:
                filled_by = display
                used.add(m.id)
                break
        emoji = "✅" if filled_by else "🔲"
        results_by_idx[idx] = (req, emoji, filled_by or "")

    return [results_by_idx[i] for i in range(len(req_roles))]


def _inject_readiness_fields_for_view(
    embed: discord.Embed,
    pkg: dict,
    guild: "discord.Guild | None",
) -> discord.Embed:
    """Add live deployment readiness fields used by /view_strike_directives."""
    mode = pkg.get("mode", "")
    total_capacity = 3 if "Hard" in mode else 5
    req_roles = pkg.get("required_roles", [])

    # Remove stale copies if this embed is being rebuilt while paging.
    keep_fields = [
        f for f in embed.fields
        if f.name != "▸ Deployment Requirements" and not f.name.startswith("▸ Signed Up")
    ]
    embed.clear_fields()
    for f in keep_fields:
        embed.add_field(name=f.name, value=f.value, inline=f.inline)

    deploy_lines = [f"**Strike Team Size:** {total_capacity}"]
    req_met = 0
    req_total = len(req_roles)
    if req_roles and guild:
        req_display = _resolve_requirements_display(pkg, guild)
        req_met = sum(1 for _r, emoji, _w in req_display if emoji == "✅")
        for role_name, emoji, _who in req_display:
            deploy_lines.append(f"{emoji} **{role_name}**")
    elif req_roles:
        for role_name in req_roles:
            deploy_lines.append(f"🔲 **{role_name}**")
    if req_total:
        deploy_lines.append(f"**Requirements Met:** {req_met}/{req_total}")

    embed.add_field(
        name="▸ Deployment Requirements",
        value="\n".join(deploy_lines),
        inline=False,
    )

    signed_ids = pkg.get("signed_up", [])
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    specialist_assigners = pkg.get("specialist_assigners", {})
    roster_total = len(signed_ids) + len(specialist_ids)

    roster_lines: list[str] = []
    for uid in signed_ids:
        m = guild.get_member(uid) if guild else None
        roster_lines.append(m.display_name if m else str(uid))
    for uid in specialist_ids:
        m = guild.get_member(uid) if guild else None
        name = m.display_name if m else str(uid)
        assigner_id = specialist_assigners.get(str(uid))
        if assigner_id and int(assigner_id) != int(uid):
            assigner = guild.get_member(assigner_id) if guild else None
            name += f" (via {assigner.display_name if assigner else str(assigner_id)})"
        else:
            name += " (specialist)"
        roster_lines.append(name)

    embed.add_field(
        name=f"▸ Signed Up ({roster_total}/{total_capacity})",
        value="\n".join(f"• {n}" for n in roster_lines) if roster_lines else "—",
        inline=False,
    )
    return embed

def _build_package_embed(
    pkg: dict,
    rep: float,
    index: int = 0,
    total: int = 0,
    viewer: Optional[discord.Member] = None,
    guild: "discord.Guild | None" = None,
) -> discord.Embed:
    pid = pkg["id"]
    node = pkg.get("node", "Unknown")
    mission_id = pkg.get("mission_id")
    mode = pkg.get("mode", "")
    status = pkg.get("status", "")
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

    _dcode = pkg.get("directive_code") or pid
    _dname = pkg.get("directive_name", "")
    _dtitle = (
        f"{_smallcaps(_dcode)}: {_smallcaps(_dname)}" if _dname
        else _smallcaps(_dcode)
    )
    embed = discord.Embed(
        title=f"`sᴛʀɪᴋᴇ ᴅɪʀᴇᴄᴛɪᴠᴇ {_dtitle}{page_label}`",
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
    intel_value = "\n".join(intel_lines)
    if len(intel_value) > 1024:
        intel_value = intel_value[:1020] + "\n…"
    embed.add_field(name="▸ Intel Dossier", value=intel_value, inline=False)

    # ▸ Briefing
    if briefing:
        max_len = 380
        briefing_out = briefing
        if len(briefing_out) > max_len:
            cut = briefing_out[:max_len]
            split_idx = cut.rfind(" ")
            if split_idx > 260:
                cut = cut[:split_idx]
            briefing_out = cut.rstrip(" .,;:") + "..."
        embed.add_field(name="▸ Field Briefing", value=f"> {briefing_out}", inline=False)

    # ▸ Stratagem modifiers in canonical positive/negative order
    core_strats = stratagems.get("core", [])
    wildcards = stratagems.get("wildcards", [])
    all_strats = core_strats + wildcards
    if intel_lapse:
        all_strats.append({"name": "Intelligence Lapse (forced)", "type": "special"})
    if all_strats:
        positive_lines = _ordered_modifier_lines(all_strats, positive=True)
        negative_lines = _ordered_modifier_lines(all_strats, positive=False)

        if intel_lapse and "• Intelligence Lapse" not in positive_lines:
            positive_lines.append("• Intelligence Lapse")

        if positive_lines:
            pos_value = "\n".join(positive_lines)
            if len(pos_value) > 1024:
                pos_value = pos_value[:1020] + "…"
            embed.add_field(name="▸ Positive Modifiers", value=pos_value, inline=False)

        if negative_lines:
            neg_value = "\n".join(negative_lines)
            if len(neg_value) > 1024:
                neg_value = neg_value[:1020] + "…"
            embed.add_field(name="▸ Negative Modifiers", value=neg_value, inline=False)

    embed.set_footer(
        text=f"ᴄʟᴇᴀʀᴀɴᴄᴇ: {clearance}  ·  {_standing_state_name(rep)} {rep:.2f}",
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
_STRIKE_DIRECTIVE_IMAGES_DIR = os.path.join(_ASSETS_DIR, "strike directive images")

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
    "Bladeguard",
    "Watch Lieutenant",
    "Watch Captain",
    "First Blade",
    "Watch Techmarine", "Watch Apothecary", "Watch Chaplain", "Watch Librarian", "Watch Keeper",
    "Honored Dreadnought",
    "Blademaster",
    "Forgemaster", "Chief Apothecary", "High Chaplain", "Void Warden", "Castellan", "Huntmaster",
    "Venerable Dreadnought",
    "Watch Master",
]
_RANK_SENIORITY_MAP = {r: i for i, r in enumerate(_RANK_SENIORITY)}


def _meets_rank_requirement(member: discord.Member, required_role: str, pkg: dict, guild: discord.Guild) -> bool:
    """Return True if member explicitly holds required role and is in the right unit."""
    if required_role not in _RANK_SENIORITY_MAP:
        return False

    member_roles = _member_role_names(member)
    # Member must explicitly hold the required role.
    if required_role not in member_roles:
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
    if required_role in ("Watch Veteran", "Oathsworn", "Watch Sergeant", "Bladeguard"):
        return member_kt == assigned_kt or is_hc

    # Company command / specialists: same company or HC
    return member_company == assigned_company or is_hc


def _remaining_line_requirements(line_reqs: list[str], member_ids: list[int], guild: discord.Guild) -> list[str]:
    """Return unsatisfied line requirements after assigning one explicit role per signer.

    Supports duplicate requirements by consuming counts from a multiset.
    """
    if not line_reqs:
        return []

    participants: list[tuple[int, set[str]]] = []
    for uid in member_ids:
        m = guild.get_member(uid) if guild else None
        if not m:
            continue
        participants.append((int(uid), _member_role_names(m)))

    req_slots = list(enumerate(line_reqs))
    slot_priority = {}
    for idx, req in req_slots:
        candidate_count = sum(1 for _uid, roles in participants if req in roles)
        slot_priority[idx] = (candidate_count, -_RANK_SENIORITY_MAP.get(req, -1), idx)

    used_members: set[int] = set()
    satisfied: set[int] = set()
    for idx, req in sorted(req_slots, key=lambda item: slot_priority[item[0]]):
        for uid, roles in participants:
            if uid in used_members:
                continue
            if req in roles:
                used_members.add(uid)
                satisfied.add(idx)
                break

    return [req for idx, req in req_slots if idx not in satisfied]


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


def _specialist_slots_allowed(pkg: dict) -> int:
    """Maximum specialist attachments allowed for this directive based on requirement slots."""
    req_roles = pkg.get("required_roles", []) or []
    return len([r for r in req_roles if r in _CADRE_SPECIALIST_ROLES])


def _member_has_structural_scope(member_kt: str | None, member_company: str | None) -> bool:
    """Return True when a member is structurally attached to a KT and/or company."""
    return bool(member_kt or member_company)


def _is_eligible_to_sign_up(member: discord.Member, pkg: dict, guild: discord.Guild) -> tuple[bool, str]:
    """Return (eligible, reason). Watch Brother+ check, unit scope, not already signed up."""
    if member.bot or not _is_active(member):
        return False, "Not an active member."

    # In debug mode, skip rank/unit/company checks
    if _is_debug_mode() and _is_admin(member):
        if member.id in pkg.get("signed_up", []):
            return False, "You are already signed up for this directive."
        return True, ""

    # Already signed up on this directive
    if member.id in pkg.get("signed_up", []):
        return False, "You are already signed up for this directive."

    # Check not signed up on another active directive
    data = _load_tp()
    active_statuses = {STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED}
    for p in data["packages"].values():
        if p["id"] == pkg["id"]:
            continue
        if member.id in p.get("signed_up", []) and p["status"] in active_statuses:
            return False, f"You are already signed up for directive `{p.get('directive_code') or p['id']}`."
        if member.id in p.get("assigned_specialist_ids", []) and p["status"] in active_statuses:
            return False, f"You are already attached as a specialist to directive `{p.get('directive_code') or p['id']}`."

    # Must be Watch Brother+
    member_roles = _member_role_names(member)
    min_idx = _RANK_SENIORITY_MAP.get("Watch Brother", 0)
    member_max = max((_RANK_SENIORITY_MAP.get(r, -1) for r in member_roles), default=-1)
    if member_max < min_idx:
        return False, "You must be at least Watch Brother to sign up."

    # Structural scope is determined only by KT/company attachment.
    # Members without KT/company attachment are considered unscoped.
    from .forge_ops import _resolve_killteam_for_member
    from .roster_ops import _get_member_company_name
    member_kt = _resolve_killteam_for_member(member)
    member_company = _get_member_company_name(member)
    assigned_kt = pkg.get("assigned_kt")
    assigned_company = pkg.get("assigned_company")

    if _member_has_structural_scope(member_kt, member_company) and not (
        member_kt == assigned_kt or member_company == assigned_company
    ):
        return False, f"You are not part of {assigned_kt or assigned_company}."

    # Must not already be signed up on another active package
    data = _load_tp()
    for p in data.get("packages", {}).values():
        if p["id"] == pkg.get("id"):
            continue
        if (member.id in p.get("signed_up", [])
                and p["status"] in (STATUS_RECRUITING, STATUS_DEPLOYED)):
            return False, f"You are already committed to directive `{p.get('directive_code') or p['id']}`. Complete that operation first."
        if (member.id in p.get("assigned_specialist_ids", [])
                and p["status"] in (STATUS_RECRUITING, STATUS_DEPLOYED)):
            return False, f"You are already committed as a specialist to directive `{p.get('directive_code') or p['id']}`. Complete that operation first."

    # Enforce slot availability and rank requirements
    # Hard-Strat = 3 total slots, Omega-Strat = 5 total slots
    mode = pkg.get("mode", "")
    total_capacity = 3 if "Hard" in mode else 5
    req_roles = pkg.get("required_roles", [])
    line_reqs = [r for r in req_roles if r not in _CADRE_SPECIALIST_ROLES]
    cadre_reqs = [r for r in req_roles if r in _CADRE_SPECIALIST_ROLES]
    signed_up = pkg.get("signed_up", [])
    specialist_ids = pkg.get("assigned_specialist_ids", [])

    # Specialists count toward total capacity
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    if len(signed_up) + len(specialist_ids) >= total_capacity:
        return False, "This directive is already at full capacity."

    # Omega directives must keep console players at <= 2 total (signed + specialists).
    if "Omega" in mode:
        resolved_guild = guild or getattr(member, "guild", None) or _get_guild_from_bot()
        if not resolved_guild:
            return False, "Guild context unavailable to validate Omega platform limits."
        member_platform = _tp_get_player_platform(member)
        if not member_platform:
            return False, "Omega directives require a PC/Console role before sign-up."
        if member_platform == "console" and _tp_console_count(pkg, resolved_guild) >= 2:
            return False, "This Omega directive already has the maximum 2 console players."

    # Feasibility gate: simulate this member signing up and ensure the remaining
    # slots are still sufficient to satisfy all unresolved requirements.
    projected_signed = list(signed_up) + [member.id]
    projected_uncovered_line = (
        _remaining_line_requirements(line_reqs, projected_signed, guild) if line_reqs else []
    )
    projected_uncovered_cadre = (
        _remaining_cadre_requirements(cadre_reqs, projected_signed, specialist_ids, guild) if cadre_reqs else []
    )
    # Slots remaining = capacity minus (projected signed-up + already attached specialists)
    slots_after_signup = total_capacity - len(projected_signed) - len(specialist_ids)
    remaining_required_slots = len(projected_uncovered_line) + len(projected_uncovered_cadre)
    if remaining_required_slots > slots_after_signup:
        unfilled = sorted(set(projected_uncovered_line + projected_uncovered_cadre))
        unfilled_str = ", ".join(unfilled)
        return False, f"The remaining slot(s) require: **{unfilled_str}**. Your current rank/roles do not qualify."

    return True, ""


def _attached_kinds_for_target(pkg: dict, target_id: int) -> set[str]:
    """Return attachment kinds for a target in this package: signed and/or specialist."""
    kinds: set[str] = set()
    if int(target_id) in {int(uid) for uid in pkg.get("signed_up", [])}:
        kinds.add("signed")
    if int(target_id) in {int(uid) for uid in pkg.get("assigned_specialist_ids", [])}:
        kinds.add("specialist")
    return kinds


def _can_actor_remove_attached_target(
    actor: discord.Member,
    target_member: "discord.Member | None",
    target_id: int,
    pkg: dict,
    guild: "discord.Guild | None",
) -> tuple[bool, set[str], str]:
    """Return (allowed, removable_kinds, reason) for actor removing target from package.

    Rules:
    - Self-removal allowed for current attachments.
    - SGT command scope: own KT only.
    - CPT/LT command scope: directives under actor's company command.
    - Cadre scope: specialist detach only for required cadre roles actor owns.
    - Admin/Watch Master may remove any attached target.
    """
    attached = _attached_kinds_for_target(pkg, target_id)
    if not attached:
        return False, set(), "Target is not attached to this directive."

    if int(getattr(actor, "id", 0) or 0) == int(target_id):
        return True, set(attached), ""

    if _is_admin(actor) or _has_role(actor, "Watch Master"):
        return True, set(attached), ""

    actor_roles = _member_role_names(actor)
    target_roles = _member_role_names(target_member) if target_member is not None else set()

    # Guardrail: if target satisfies a required cadre role for this directive,
    # only the owning cadre leader may remove them (besides self/admin/Watch Master).
    required_cadre_roles = [
        r for r in (pkg.get("required_roles", []) or [])
        if r in _CADRE_SPECIALIST_ROLES
    ]
    matched_required_cadre = [r for r in required_cadre_roles if r in target_roles]
    if matched_required_cadre:
        if any(_cadre_leader_owns(actor, r) for r in matched_required_cadre):
            return True, set(attached), ""
        return False, set(), (
            "This member fulfills a required specialist role on this directive and "
            "can only be removed by the owning cadre leader or by self-removal."
        )

    # Company command can manage members on directives under their assigned company.
    def _safe_member_company_name(member: discord.Member) -> str | None:
        try:
            from .roster_ops import _get_member_company_name as _fn
            resolved = _fn(member)
            if resolved:
                return resolved
        except Exception:
            pass
        role_names = _member_role_names(member)
        for rn in (
            "Watch Company Primus",
            "Watch Company Secundus",
            "Watch Company Tertius",
            "Watch Company Quartus",
            "Watch Company Quintus",
            "Dreadnought Cadre",
        ):
            if rn in role_names:
                return rn
        return None

    def _safe_member_kt(member: discord.Member) -> str | None:
        try:
            from .forge_ops import _resolve_killteam_for_member as _fn
            resolved = _fn(member)
            if resolved:
                return resolved
        except Exception:
            pass
        role_names = _member_role_names(member)
        kill_teams = set(_b("KILL_TEAMS") or [])
        for rn in role_names:
            if rn in kill_teams:
                return rn
        for rn in role_names:
            if rn.lower().startswith("kill team "):
                return rn
        return None

    actor_company = _safe_member_company_name(actor)
    if (
        ("Watch Captain" in actor_roles or "Watch Lieutenant" in actor_roles)
        and actor_company
        and pkg.get("assigned_company")
        and actor_company == pkg.get("assigned_company")
    ):
        return True, set(attached), ""

    # KT command can manage members on their own KT directives.
    if "Watch Sergeant" in actor_roles:
        actor_kt = _safe_member_kt(actor)
        pkg_kt = pkg.get("assigned_kt")
        if actor_kt and pkg_kt and actor_kt == pkg_kt:
            if target_member is None:
                return True, set(attached), ""
            target_kt = _safe_member_kt(target_member)
            if target_kt == actor_kt:
                return True, set(attached), ""

    # Cadre authority path only applies to specialist attachments and only when
    # this directive explicitly requires a role the cadre leader owns.
    if "specialist" in attached and target_member is not None and (actor_roles & _CADRE_LEADER_ROLES):
        owned_required_roles = [
            r
            for r in (pkg.get("required_roles", []) or [])
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(actor, r)
        ]
        if owned_required_roles:
            if any(r in target_roles for r in owned_required_roles):
                return True, set(attached), ""

    return False, set(), "You are not authorized to remove this member from this directive."


def _remove_target_from_package(
    pkg: dict,
    target_id: int,
    removable_kinds: set[str],
    guild: "discord.Guild | None",
) -> tuple[bool, str]:
    """Mutate package in-place and remove allowed attachment kinds for target."""
    if not removable_kinds:
        return False, "No removable attachment found for target."

    removed_signed = False
    removed_specialist = False

    if "signed" in removable_kinds and int(target_id) in {int(uid) for uid in pkg.get("signed_up", [])}:
        pkg["signed_up"] = [uid for uid in pkg.get("signed_up", []) if int(uid) != int(target_id)]
        removed_signed = True

    if "specialist" in removable_kinds and int(target_id) in {int(uid) for uid in pkg.get("assigned_specialist_ids", [])}:
        pkg["assigned_specialist_ids"] = [
            uid for uid in pkg.get("assigned_specialist_ids", []) if int(uid) != int(target_id)
        ]
        pkg.setdefault("specialist_assigners", {})
        pkg["specialist_assigners"].pop(str(target_id), None)
        removed_specialist = True

    if not removed_signed and not removed_specialist:
        return False, "Target is no longer attached to this directive."

    # If readiness breaks after any removal, drop from deployed to recruiting.
    if pkg.get("status") == STATUS_DEPLOYED and not _check_deployed(pkg, guild):
        pkg["status"] = STATUS_RECRUITING

    if removed_signed and removed_specialist:
        return True, "Removed from sign-up roster and specialist attachment."
    if removed_specialist:
        return True, "Removed specialist attachment."
    return True, "Removed from sign-up roster."


async def remove_attached_member_from_directive(
    package_id: str,
    actor: discord.Member,
    target_id: int,
    guild: "discord.Guild | None",
) -> tuple[bool, str]:
    """Remove an attached member from a directive roster with scope-aware authority checks."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data.get("packages", {}).get(package_id)
        if not pkg:
            return False, "Directive not found."

        if pkg.get("status") not in (STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED):
            return False, (
                f"Directive `{pkg.get('directive_code') or package_id}` is `{pkg.get('status')}` "
                "and no longer allows member removal."
            )

        resolved_guild = guild or _get_guild_from_bot()
        target_member = resolved_guild.get_member(int(target_id)) if resolved_guild else None
        allowed, removable_kinds, deny_reason = _can_actor_remove_attached_target(
            actor,
            target_member,
            int(target_id),
            pkg,
            resolved_guild,
        )
        if not allowed:
            return False, deny_reason

        success, action_msg = _remove_target_from_package(
            pkg,
            int(target_id),
            removable_kinds,
            resolved_guild,
        )
        if not success:
            return False, action_msg

        _save_tp(data)

    target_display = target_member.display_name if target_member else str(target_id)
    return True, f"{target_display}: {action_msg}"


def _attached_target_ids(pkg: dict) -> list[int]:
    """Return attached member IDs in display order (signed first, then specialists)."""
    return [int(uid) for uid in dict.fromkeys([*pkg.get("signed_up", []), *pkg.get("assigned_specialist_ids", [])])]


async def _refresh_signup_embed_for_package(package_id: str, guild: "discord.Guild | None") -> None:
    """Best-effort refresh of KT signup embed roster/requirements after roster mutation."""
    try:
        resolved_guild = guild or _get_guild_from_bot()
        if not resolved_guild:
            return

        data = _load_tp()
        pkg = data.get("packages", {}).get(package_id)
        if not pkg:
            return

        signed_up = pkg.get("signed_up", [])
        specialists = pkg.get("assigned_specialist_ids", [])
        sp_assigners = pkg.get("specialist_assigners", {})
        mode = pkg.get("mode", "")
        total_capacity = 3 if "Hard" in mode else 5
        count = len(signed_up) + len(specialists)

        signed_names = []
        for uid in signed_up:
            m2 = resolved_guild.get_member(uid) if resolved_guild else None
            signed_names.append(m2.display_name if m2 else str(uid))
        for uid in specialists:
            m2 = resolved_guild.get_member(uid) if resolved_guild else None
            sp_assigner_id = sp_assigners.get(str(uid))
            sp_a = resolved_guild.get_member(sp_assigner_id) if (resolved_guild and sp_assigner_id) else None
            sp_suffix = f" _(via {sp_a.display_name})_" if (sp_a and int(sp_assigner_id) != int(uid)) else " _(specialist)_"
            signed_names.append((m2.display_name if m2 else str(uid)) + sp_suffix)

        roster_field_name = f"▸ Signed Up ({count}/{total_capacity})"
        roster_field_value = "\n".join(f"• {n}" for n in signed_names) or "—"

        signup_channel_id = pkg.get("signup_channel_id")
        signup_message_id = pkg.get("signup_message_id")
        if not (signup_channel_id and signup_message_id):
            return

        ch = await _resolve_channel(resolved_guild, int(signup_channel_id))
        if not ch:
            return
        msg = await ch.fetch_message(int(signup_message_id))
        if not msg.embeds:
            return

        upd_embed = msg.embeds[0]
        req_roles = pkg.get("required_roles", [])
        deploy_lines = [f"**Strike Team Size:** {total_capacity}"]
        if req_roles and resolved_guild:
            req_display = _resolve_requirements_display(pkg, resolved_guild)
            for rl, em, _wh in req_display:
                deploy_lines.append(f"{em} **{rl}**")
        elif req_roles:
            for rl in req_roles:
                deploy_lines.append(f"🔲 **{rl}**")
        deploy_lines.append("Press **⚔ Comply** to register for this operation.")

        new_fields = [
            f for f in upd_embed.fields
            if not f.name.startswith("▸ Signed Up")
            and f.name != "▸ Deployment Requirements"
            and f.name != "▸ Required Ranks"
        ]
        upd_embed.clear_fields()
        for f in new_fields:
            upd_embed.add_field(name=f.name, value=f.value, inline=f.inline)
        upd_embed.add_field(name="▸ Deployment Requirements", value="\n".join(deploy_lines), inline=False)
        upd_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
        await msg.edit(embed=upd_embed, view=SignUpView(package_id=package_id))
    except Exception as e:
        _g.logger.debug(f"[TP] Signup embed refresh failed for {package_id}: {e}")


def _get_removable_targets_for_actor(
    package_id: str,
    actor: "discord.Member | None",
    guild: "discord.Guild | None",
) -> tuple[dict | None, list[tuple[int, str]], str | None]:
    """Return roster targets this actor is authorized to remove for a directive."""
    data = _load_tp()
    pkg = data.get("packages", {}).get(package_id)
    if not pkg:
        return None, [], "Directive not found."
    if pkg.get("status") not in (STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED):
        return (
            pkg,
            [],
            f"Directive `{pkg.get('directive_code') or package_id}` is `{pkg.get('status')}` and no longer allows member removal.",
        )
    if not actor:
        return pkg, [], "Could not resolve your member context for roster authority checks."

    resolved_guild = guild or _get_guild_from_bot()
    removable: list[tuple[int, str]] = []
    for target_id in _attached_target_ids(pkg):
        target_member = resolved_guild.get_member(int(target_id)) if resolved_guild else None
        allowed, _kinds, _deny_reason = _can_actor_remove_attached_target(
            actor,
            target_member,
            int(target_id),
            pkg,
            resolved_guild,
        )
        if not allowed:
            continue
        target_name = target_member.display_name if target_member else str(target_id)
        removable.append((int(target_id), target_name))
    return pkg, removable, None


class _ManageRosterRemoveButton(discord.ui.Button):
    """Ephemeral remove button bound to a specific attached member ID."""

    def __init__(self, target_id: int, target_name: str, row: int):
        super().__init__(
            label=f"✖ {target_name}"[:80],
            style=discord.ButtonStyle.danger,
            row=row,
        )
        self.target_id = int(target_id)

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        if not parent or not hasattr(parent, "remove_target"):
            await interaction.response.send_message("Roster panel unavailable.", ephemeral=True)
            return
        await parent.remove_target(interaction, self.target_id)


class _ManageRosterView(discord.ui.View):
    """Ephemeral roster-management panel shown only to the requesting user."""

    def __init__(self, package_id: str, actor_id: int, guild: "discord.Guild | None"):
        super().__init__(timeout=300)
        self.package_id = package_id
        self.actor_id = int(actor_id)
        self._rebuild(guild)

    def _rebuild(self, guild: "discord.Guild | None") -> None:
        self.clear_items()
        resolved_guild = guild or _get_guild_from_bot()
        actor = resolved_guild.get_member(self.actor_id) if resolved_guild else None
        _pkg, removable, _err = _get_removable_targets_for_actor(self.package_id, actor, resolved_guild)
        for idx, (target_id, target_name) in enumerate(removable[:5]):
            row = 0 if idx < 3 else 1
            self.add_item(_ManageRosterRemoveButton(target_id=target_id, target_name=target_name, row=row))

    async def remove_target(self, interaction: discord.Interaction, target_id: int):
        if int(getattr(interaction.user, "id", 0) or 0) != int(self.actor_id):
            await interaction.response.send_message("This roster panel is bound to the requesting user.", ephemeral=True)
            return

        await interaction.response.defer()
        success, msg = await remove_attached_member_from_directive(
            self.package_id,
            interaction.user,
            int(target_id),
            interaction.guild,
        )
        if success:
            await _refresh_signup_embed_for_package(self.package_id, interaction.guild)

        self._rebuild(interaction.guild or _get_guild_from_bot())
        if self.children:
            await interaction.edit_original_response(content=msg, view=self)
        else:
            await interaction.edit_original_response(content=f"{msg}\nNo additional members are removable by you.", view=None)


async def _post_signup_embed(package_id: str, guild: discord.Guild, complier: discord.Member = None) -> None:
    """Post the KT sign-up embed in the KT's forum thread."""
    from .forge_ops import _get_award_announcement_channel, _resolve_killteam_for_member

    data = _load_tp()
    pkg = data["packages"].get(package_id)
    if not pkg:
        return

    kt_name = pkg.get("assigned_kt", "")
    company_mention = _resolve_company_role_mention(guild, pkg.get("assigned_company"))
    mode = pkg.get("mode", "")
    req_roles = pkg.get("required_roles", [])

    data_rep = data.get("rep", 0.0)
    embed = _build_package_embed(pkg, data_rep, guild=guild)
    leader_member, leader_role = _resolve_kt_leader_for_package(pkg, guild)
    if leader_member:
        embed.set_author(
            name=f"{leader_member.display_name}",
            icon_url=leader_member.display_avatar.url if getattr(leader_member, "display_avatar", None) else None,
        )
    total_capacity = 3 if "Hard" in mode else 5

    # Remove ▸ Required Ranks — it will be merged into ▸ Deployment Requirements below
    _embed_fields = [f for f in embed.fields if f.name != "▸ Required Ranks"]
    embed.clear_fields()
    for _f in _embed_fields:
        embed.add_field(name=_f.name, value=_f.value, inline=_f.inline)

    # Build merged ▸ Deployment Requirements: checkboxes (no names) + size + comply
    _deploy_lines = []
    if complier:
        _deploy_lines.append(f"{complier.mention} has accepted these orders.")
    _deploy_lines.append(f"**Strike Team Size:** {total_capacity}")
    if req_roles and guild:
        _req_disp = _resolve_requirements_display(pkg, guild)
        for _role, _emoji, _who in _req_disp:
            _deploy_lines.append(f"{_emoji} **{_role}**")
    elif req_roles:
        for _r in req_roles:
            _deploy_lines.append(f"🔲 **{_r}**")
    _deploy_lines.append("Press **⚔ Comply** to register for this operation.")
    embed.add_field(
        name="▸ Deployment Requirements",
        value="\n".join(_deploy_lines),
        inline=False,
    )

    # Add current roster if anyone is already signed up / attached (e.g. on repost)
    _current_signed = pkg.get("signed_up", [])
    _current_specialists = pkg.get("assigned_specialist_ids", [])
    _specialist_assigners = pkg.get("specialist_assigners", {})
    _roster_total = len(_current_signed) + len(_current_specialists)
    if _roster_total > 0:
        _roster_names = []
        for uid in _current_signed:
            m2 = guild.get_member(uid) if guild else None
            _roster_names.append(m2.display_name if m2 else str(uid))
        for uid in _current_specialists:
            m2 = guild.get_member(uid) if guild else None
            _sp_name = m2.display_name if m2 else str(uid)
            _assigner_id = _specialist_assigners.get(str(uid))
            if _assigner_id and int(_assigner_id) != int(uid):
                _a = guild.get_member(_assigner_id) if guild else None
                _sp_name += f" _(via {_a.display_name if _a else str(_assigner_id)})_"
            else:
                _sp_name += " _(specialist)_"
            _roster_names.append(_sp_name)
        embed.add_field(
            name=f"▸ Signed Up ({_roster_total}/{total_capacity})",
            value="\n".join(f"• {n}" for n in _roster_names),
            inline=False,
        )

    view = SignUpView(package_id=package_id)

    # Delete the old sign-up embed before reposting to avoid stale duplicates
    _old_signup_ch = pkg.get("signup_channel_id")
    _old_signup_msg = pkg.get("signup_message_id")
    if _old_signup_ch and _old_signup_msg and guild:
        try:
            _old_ch = await _resolve_channel(guild, int(_old_signup_ch))
            if _old_ch:
                _old_msg = await _old_ch.fetch_message(int(_old_signup_msg))
                await _old_msg.delete()
                _g.logger.info(f"[TP] Deleted old signup embed {_old_signup_msg} for {package_id} before repost")
            else:
                _g.logger.debug(f"[TP] Could not resolve old signup channel {_old_signup_ch} for deletion")
        except discord.NotFound:
            _g.logger.debug(f"[TP] Old signup embed {_old_signup_msg} already deleted")
        except Exception as exc:
            _g.logger.warning(f"[TP] Failed to delete old signup embed {_old_signup_msg}: {exc}")

    # Find KT channel via any KT member
    preferred_thread_id = int(pkg.get("forum_thread_id") or 0)
    if preferred_thread_id and guild:
        channel = await _resolve_channel(guild, preferred_thread_id)
        if isinstance(channel, discord.Thread):
            _cls_file = _classification_file(pkg)
            msg = await _notify_send(
                channel,
                guild,
                content=company_mention or None,
                embed=embed,
                view=view,
                **_file_kwarg(_cls_file),
            )
            if msg:
                try:
                    await msg.pin(reason=f"Pin directive {pkg.get('directive_code') or package_id} for KT coordination")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    _g.logger.debug(f"[TP] Failed to pin KT directive message {getattr(msg, 'id', '?')} for {package_id}: {exc}")
                async with _TP_LOCK:
                    data2 = _load_tp()
                    if package_id in data2["packages"]:
                        data2["packages"][package_id]["signup_message_id"] = msg.id
                        data2["packages"][package_id]["signup_channel_id"] = getattr(msg.channel, "id", channel.id)
                        _save_tp(data2)
                await _track_package_message(package_id, msg)
            return

    sent = False
    for m in guild.members if guild else []:
        if m.bot or not _is_active(m):
            continue
        if _resolve_killteam_for_member(m) == kt_name:
            channel = await _get_award_announcement_channel(m, guild)
            if channel:
                _cls_file = _classification_file(pkg)
                msg = await _notify_send(
                    channel, guild,
                    content=company_mention or None,
                    embed=embed,
                    view=view,
                    **_file_kwarg(_cls_file),
                )
                if msg:
                    try:
                        await msg.pin(reason=f"Pin directive {pkg.get('directive_code') or package_id} for KT coordination")
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        _g.logger.debug(f"[TP] Failed to pin KT directive message {getattr(msg, 'id', '?')} for {package_id}: {exc}")
                async with _TP_LOCK:
                    data2 = _load_tp()
                    if package_id in data2["packages"]:
                        data2["packages"][package_id]["signup_message_id"] = msg.id
                        data2["packages"][package_id]["signup_channel_id"] = getattr(msg.channel, "id", channel.id)
                        _save_tp(data2)
                await _track_package_message(package_id, msg)

                # If the KT signup embed was reposted, update previously-sent lightweight
                # specialist assignment notifications so their jump links stay valid.
                try:
                    _new_ch_id = int(getattr(msg.channel, "id", channel.id))
                    _new_msg_id = int(msg.id)
                    _new_url = f"https://discord.com/channels/{guild.id}/{_new_ch_id}/{_new_msg_id}"
                    _latest_pkg = (_load_tp().get("packages", {}) or {}).get(package_id, {})
                    for _ref in _latest_pkg.get("specialist_notification_msgs", []):
                        if (_ref or {}).get("kind") != "assignment_link":
                            continue
                        _sp_ch_id = (_ref or {}).get("channel_id")
                        _sp_msg_id = (_ref or {}).get("message_id")
                        if not _sp_ch_id or not _sp_msg_id:
                            continue
                        _sp_ch = await _resolve_channel(guild, int(_sp_ch_id))
                        if not _sp_ch:
                            continue
                        _sp_msg = await _sp_ch.fetch_message(int(_sp_msg_id))
                        if not _sp_msg or not _sp_msg.embeds:
                            continue

                        _sp_embed = _sp_msg.embeds[0]
                        _updated = False
                        _rebuilt = []
                        for _f in _sp_embed.fields:
                            if _f.name == "▸ Directive":
                                _lines = [ln for ln in (_f.value or "").split("\n") if ln.strip()]
                                _label = _lines[0] if _lines else f"`{_latest_pkg.get('directive_code') or package_id}`"
                                _rebuilt.append(("▸ Directive", f"{_label}\n[Open KT Directive]({_new_url})", _f.inline))
                                _updated = True
                            else:
                                _rebuilt.append((_f.name, _f.value, _f.inline))

                        if _updated:
                            _sp_embed.clear_fields()
                            for _name, _value, _inline in _rebuilt:
                                _sp_embed.add_field(name=_name, value=_value, inline=_inline)
                            await _sp_msg.edit(embed=_sp_embed)
                except Exception as _link_exc:
                    _g.logger.debug(f"[TP] Failed refreshing assignment-link notifications for {package_id}: {_link_exc}")
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
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    # Specialists count toward total capacity alongside signed-up brothers
    if len(signed_up) + len(specialist_ids) < total_capacity:
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
        guild = interaction.guild or _get_guild_from_bot()

        # Resolve package and expected KT leader before status update.
        pre_data = _load_tp()
        pre_pkg = pre_data.get("packages", {}).get(self.package_id)
        if not pre_pkg:
            await interaction.response.send_message("Package not found.", ephemeral=True)
            return

        if not (_is_debug_mode() and _is_admin(member)):
            expected_leader, expected_role = _resolve_kt_leader_for_package(pre_pkg, guild)
            if not expected_leader:
                await interaction.response.send_message(
                    "No Kill Team leader could be resolved for this directive. Contact command staff.",
                    ephemeral=True,
                )
                return
            if int(member.id) != int(expected_leader.id):
                await interaction.response.send_message(
                    f"Only {expected_leader.display_name} ({expected_role}) may accept these orders for {self.kt_name}.",
                    ephemeral=True,
                )
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
        await _ensure_directive_forum_thread(self.package_id, guild, pkg=pkg)
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

    @discord.ui.button(label="Manage Roster", style=discord.ButtonStyle.secondary, custom_id="tp_manage_roster")
    async def manage_roster(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved_guild = interaction.guild or _get_guild_from_bot()
        actor = resolved_guild.get_member(interaction.user.id) if resolved_guild else None
        pkg, removable, err = _get_removable_targets_for_actor(self.package_id, actor, resolved_guild)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not pkg:
            await interaction.response.send_message("Directive not found.", ephemeral=True)
            return
        if not removable:
            await interaction.response.send_message(
                "No removable roster entries are available for your authority scope on this directive.",
                ephemeral=True,
            )
            return

        panel = _ManageRosterView(self.package_id, interaction.user.id, resolved_guild)
        await interaction.response.send_message(
            f"Roster controls for `{pkg.get('directive_code') or self.package_id}`. "
            "These controls are visible only to you.",
            ephemeral=True,
            view=panel,
        )

    @discord.ui.button(label="⚔ Comply", style=discord.ButtonStyle.success, custom_id="tp_signup")
    async def sign_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild

        data = _load_tp()
        pkg = data["packages"].get(self.package_id)
        if not pkg:
            await interaction.response.send_message("Directive not found.", ephemeral=True)
            return
        if pkg["status"] not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            await interaction.response.send_message("This directive is no longer accepting sign-ups.", ephemeral=True)
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
                await interaction.followup.send("Directive not found.", ephemeral=True)
                return
            if pkg2.get("status") not in (STATUS_RECRUITING, STATUS_DEPLOYED):
                await interaction.followup.send("This directive is no longer accepting sign-ups.", ephemeral=True)
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

        await _remove_member_from_strike_queue(member.id)

        signed_up = pkg2.get("signed_up", [])
        _specialists_su = pkg2.get("assigned_specialist_ids", [])
        mode = pkg2.get("mode", "")
        total_capacity = 3 if "Hard" in mode else 5
        count = len(signed_up) + len(_specialists_su)

        # Update the sign-up embed to show current roster
        try:
            resolved_guild = guild or _get_guild_from_bot()
            signed_names = []
            for uid in pkg2.get("signed_up", []):
                m2 = resolved_guild.get_member(uid) if resolved_guild else None
                signed_names.append(m2.display_name if m2 else str(uid))
            for uid in _specialists_su:
                m2 = resolved_guild.get_member(uid) if resolved_guild else None
                sp_assigners = pkg2.get("specialist_assigners", {})
                sp_assigner_id = sp_assigners.get(str(uid))
                sp_a = resolved_guild.get_member(sp_assigner_id) if (resolved_guild and sp_assigner_id) else None
                sp_suffix = f" _(via {sp_a.display_name})_" if (sp_a and int(sp_assigner_id) != int(uid)) else " _(specialist)_"
                signed_names.append((m2.display_name if m2 else str(uid)) + sp_suffix)
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
                        # If status just flipped to DEPLOYED, rebuild the full embed
                        # so the Intel Dossier status line also updates.
                        if pkg2.get("status") == STATUS_DEPLOYED:
                            data_rep = _load_tp().get("rep", 0.0)
                            upd_embed = _build_package_embed(pkg2, data_rep, guild=resolved_guild)
                            _rck2 = []
                            if pkg2.get("required_roles"):
                                if resolved_guild:
                                    _rd2 = _resolve_requirements_display(pkg2, resolved_guild)
                                    _rck2 = [f"{em} **{rl}**" for rl, em, _ in _rd2]
                                else:
                                    _rck2 = [f"🔲 **{r}**" for r in pkg2.get("required_roles", [])]
                            _dp2 = _rck2 + [f"**Strike Team Size:** {total_capacity}", "Press **⚔ Comply** to register for this operation."]
                            upd_embed.add_field(
                                name="▸ Deployment Requirements",
                                value="\n".join(_dp2),
                                inline=False,
                            )
                            upd_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
                        else:
                            upd_embed = msg.embeds[0]
                            # Rebuild ▸ Deployment Requirements with updated checkboxes (no names)
                            _req_roles2 = pkg2.get("required_roles", [])
                            _new_deploy_lines = [f"**Strike Team Size:** {total_capacity}"]
                            if _req_roles2 and resolved_guild:
                                _req_display2 = _resolve_requirements_display(pkg2, resolved_guild)
                                for _rl, _em, _wh in _req_display2:
                                    _new_deploy_lines.append(f"{_em} **{_rl}**")
                            elif _req_roles2:
                                for _rl in _req_roles2:
                                    _new_deploy_lines.append(f"🔲 **{_rl}**")
                            _new_deploy_lines.append("Press **⚔ Comply** to register for this operation.")
                            _new_deploy_value = "\n".join(_new_deploy_lines)
                            new_fields = [
                                f for f in upd_embed.fields
                                if not f.name.startswith("▸ Signed Up")
                                and f.name != "▸ Deployment Requirements"
                                and f.name != "▸ Required Ranks"
                            ]
                            upd_embed.clear_fields()
                            for f in new_fields:
                                upd_embed.add_field(name=f.name, value=f.value, inline=f.inline)
                            upd_embed.add_field(name="▸ Deployment Requirements", value=_new_deploy_value, inline=False)
                            upd_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
                        await msg.edit(embed=upd_embed, view=SignUpView(package_id=self.package_id))

            # Update specialist notification embeds
            for sp_msg_ref in pkg2.get("specialist_notification_msgs", []):
                try:
                    if sp_msg_ref.get("kind") == "assignment_link":
                        continue
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
        success_message = None
        async with _TP_LOCK:
            data = _load_tp()
            pkg = data["packages"].get(self.package_id)
            if not pkg:
                await interaction.response.send_message("Package not found.", ephemeral=True)
                return
            if pkg.get("status") not in (STATUS_RECRUITING, STATUS_DEPLOYED):
                await interaction.response.send_message(
                    "You can only stand down while this directive is recruiting or deployed.",
                    ephemeral=True,
                )
                return

            in_signed = member.id in pkg.get("signed_up", [])
            in_specialist = member.id in pkg.get("assigned_specialist_ids", [])
            if not in_signed and not in_specialist:
                await interaction.response.send_message("You are not currently attached to this directive.", ephemeral=True)
                return

            if in_signed:
                pkg["signed_up"].remove(member.id)
            if in_specialist:
                pkg["assigned_specialist_ids"] = [
                    uid for uid in pkg.get("assigned_specialist_ids", [])
                    if int(uid) != int(member.id)
                ]
                pkg.setdefault("specialist_assigners", {})
                pkg["specialist_assigners"].pop(str(member.id), None)

            # Any stand-down while deployed immediately returns the directive to recruiting.
            if pkg.get("status") == STATUS_DEPLOYED:
                pkg["status"] = STATUS_RECRUITING

            if in_signed and in_specialist:
                success_message = "You have stood down and removed your specialist attachment from this directive."
            elif in_specialist:
                success_message = "You have removed your specialist attachment from this directive."
            else:
                success_message = "You have stood down from this directive."

            _save_tp(data)

        await interaction.response.send_message(success_message, ephemeral=True)

        # Update the signup embed roster
        try:
            resolved_guild = interaction.guild or _get_guild_from_bot()
            data3 = _load_tp()
            pkg3 = data3["packages"].get(self.package_id, {})
            signed_up3 = pkg3.get("signed_up", [])
            _specialists3 = pkg3.get("assigned_specialist_ids", [])
            mode3 = pkg3.get("mode", "")
            total_capacity3 = 3 if "Hard" in mode3 else 5
            count3 = len(signed_up3) + len(_specialists3)
            signed_names = []
            for uid in signed_up3:
                m2 = resolved_guild.get_member(uid) if resolved_guild else None
                signed_names.append(m2.display_name if m2 else str(uid))
            for uid in _specialists3:
                m2 = resolved_guild.get_member(uid) if resolved_guild else None
                signed_names.append((m2.display_name if m2 else str(uid)) + " _(specialist)_")
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
                        _req_roles3 = pkg3.get("required_roles", [])
                        _sd_deploy_lines = [f"**Strike Team Size:** {total_capacity3}"]
                        if _req_roles3 and resolved_guild:
                            _req_display3 = _resolve_requirements_display(pkg3, resolved_guild)
                            for _rl3, _em3, _wh3 in _req_display3:
                                _sd_deploy_lines.append(f"{_em3} **{_rl3}**")
                        elif _req_roles3:
                            for _rl3 in _req_roles3:
                                _sd_deploy_lines.append(f"🔲 **{_rl3}**")
                        _sd_deploy_lines.append("Press **⚔ Comply** to register for this operation.")
                        _sd_deploy_value = "\n".join(_sd_deploy_lines)
                        new_fields = [
                            f for f in upd_embed.fields
                            if not f.name.startswith("▸ Signed Up")
                            and f.name != "▸ Deployment Requirements"
                            and f.name != "▸ Required Ranks"
                        ]
                        upd_embed.clear_fields()
                        for f in new_fields:
                            upd_embed.add_field(name=f.name, value=f.value, inline=f.inline)
                        upd_embed.add_field(name="▸ Deployment Requirements", value=_sd_deploy_value, inline=False)
                        upd_embed.add_field(name=roster_field_name, value=roster_field_value, inline=False)
                        await msg.edit(embed=upd_embed, view=SignUpView(package_id=self.package_id))
        except Exception as e:
            _g.logger.debug(f"[TP] Stand Down embed update failed for {self.package_id}: {e}")


class SpecialistAssignView(discord.ui.View):
    """View for cadre leaders to assign a specialist to a package."""

    def __init__(self, package_id: str, required_roles: list, guild: discord.Guild):
        super().__init__(timeout=600)
        self.package_id = package_id
        self.required_roles = required_roles
        self.has_assignable_options = False

        # Build filtered member list: only members who hold a CADRE SPECIALIST role
        # (line roles like Watch Veteran / Oathsworn sign up via Comply, not here)
        # Also excludes specialists already locked on another active package.
        cadre_roles_needed = [r for r in required_roles if r in _CADRE_SPECIALIST_ROLES]

        # Collect IDs already locked on an active package (excluding this one)
        _tp_data = _load_tp()
        _active_statuses = {STATUS_RECRUITING, STATUS_DEPLOYED}
        _pkg = (_tp_data.get("packages", {}) or {}).get(package_id, {})
        _specialist_slots = _specialist_slots_allowed(_pkg)
        _current_specialists = len(_pkg.get("assigned_specialist_ids", []))
        if _current_specialists >= _specialist_slots:
            select = discord.ui.Select(
                placeholder=f"Specialist slots filled ({_current_specialists}/{_specialist_slots})",
                options=[discord.SelectOption(label="No additional specialist slots", value="none")],
                custom_id="tp_specialist_select",
                disabled=True,
            )
            self.add_item(select)
            return

        _cadre_slots = len(cadre_roles_needed)
        _cadre_assigned = 0
        _assigned_ids = _pkg.get("assigned_specialist_ids", [])
        for _uid in _assigned_ids:
            _m = guild.get_member(_uid) if guild else None
            if not _m:
                continue
            _m_roles = _member_role_names(_m)
            if any(_r in _m_roles for _r in cadre_roles_needed):
                _cadre_assigned += 1
        if _cadre_assigned >= _cadre_slots:
            select = discord.ui.Select(
                placeholder=f"Your cadre slots filled ({_cadre_assigned}/{_cadre_slots})",
                options=[discord.SelectOption(label="No additional specialist slots", value="none")],
                custom_id="tp_specialist_select",
                disabled=True,
            )
            self.add_item(select)
            return
        currently_assigned = set(_pkg.get("assigned_specialist_ids", []))
        currently_signed = set(_pkg.get("signed_up", []))
        already_assigned: set = set()
        already_signed: set = set()
        for _p in _tp_data.get("packages", {}).values():
            if _p["id"] == package_id:
                continue
            if _p["status"] in _active_statuses:
                already_assigned.update(_p.get("assigned_specialist_ids", []))
                already_signed.update(_p.get("signed_up", []))

        options = []
        seen = set()
        for role_name in cadre_roles_needed:
            for m in (guild.members if guild else []):
                if m.bot or m.id in seen:
                    continue
                if not _is_active(m):
                    continue
                if m.id in currently_assigned:
                    continue  # already attached to this directive
                if m.id in currently_signed:
                    continue  # already signed up on this directive
                if m.id in already_assigned:
                    continue  # already on another package
                if m.id in already_signed:
                    continue  # already signed up on another active package
                if any((getattr(r, "name", "") or "").strip() == role_name for r in getattr(m, "roles", [])):
                    options.append(discord.SelectOption(
                        label=m.display_name[:100],
                        value=str(m.id),
                        description=role_name[:100],
                    ))
                    seen.add(m.id)

        if options:
            self.has_assignable_options = True
            select = discord.ui.Select(
                placeholder="Select specialist to attach…",
                options=options[:25],
                custom_id="tp_specialist_select",
            )
            select.callback = self.on_select
            self.add_item(select)
        else:
            select = discord.ui.Select(
                placeholder="No assignable specialists available",
                options=[discord.SelectOption(label="No eligible specialists", value="none")],
                custom_id="tp_specialist_select",
                disabled=True,
            )
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
            _code = p.get("directive_code") or p["id"]
            _name = p.get("directive_name", "")
            label = f"{_code}: {_name}" if _name else _code
            desc = f"{mode_short} · {status_short}" + (f" → {kt}" if kt else "")
            options.append(discord.SelectOption(label=label[:100], description=desc[:100], value=p["id"]))

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
        embed = _build_package_embed(pkg, data.get("rep", 0.0), viewer=interaction.user, guild=interaction.guild)
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

        if show_distribute:
            distribute_btn = discord.ui.Button(
                label="Distribute All",
                style=discord.ButtonStyle.danger,
                custom_id="tp_distribute_all",
            )
            distribute_btn.callback = self.distribute_all
            self.add_item(distribute_btn)

        # Captain/LT: company acceptance button (no KT assignment selection)
        if not show_distribute and viewer and (_has_role(viewer, "Watch Captain") or _has_role(viewer, "Watch Lieutenant")):
            assign_btn = discord.ui.Button(
                label="Accept for Company",
                style=discord.ButtonStyle.success,
                custom_id="tp_assign_kt",
                disabled=False,
            )
            assign_btn.callback = self.assign_to_kt
            self.add_item(assign_btn)

        # Cadre leader: "Assign Specialist" button — only on cadre views, not the WM request board
        if not show_distribute and viewer and (_member_role_names(viewer) & _CADRE_LEADER_ROLES):
            spec_options, spec_placeholder, spec_enabled = self._build_specialist_options(viewer)
            spec_select = discord.ui.Select(
                placeholder=spec_placeholder,
                options=spec_options or [discord.SelectOption(label="No eligible specialists", value="none")],
                custom_id="tp_assign_specialist_inline",
                disabled=not spec_enabled,
            )
            spec_select.callback = self.on_specialist_select
            self.add_item(spec_select)

            unspec_options, unspec_placeholder, unspec_enabled = self._build_unassign_specialist_options(viewer)
            unspec_select = discord.ui.Select(
                placeholder=unspec_placeholder,
                options=unspec_options or [discord.SelectOption(label="No assigned specialists", value="none")],
                custom_id="tp_unassign_specialist_inline",
                disabled=not unspec_enabled,
            )
            unspec_select.callback = self.on_unassign_specialist_select
            self.add_item(unspec_select)

        # Select menu for quick navigation (max 25)
        if len(packages) > 1:
            options = []
            for p in packages[:25]:
                mode_short = "HS" if "Hard" in p.get("mode", "") else "Ω"
                status_short = p["status"].upper()[:12]
                kt = p.get("assigned_kt", "")
                _s_code = p.get("directive_code") or p["id"]
                _s_name = p.get("directive_name", "")
                _s_label = f"{_s_code}: {_s_name}" if _s_name else _s_code
                desc = f"{mode_short} · {status_short}" + (f" → {kt}" if kt else "")
                options.append(discord.SelectOption(label=_s_label[:100], description=desc[:100], value=p["id"]))
            select = discord.ui.Select(
                placeholder="Jump to directive…",
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

    def _build_specialist_options(self, viewer: discord.Member) -> tuple[list, str, bool]:
        """Build inline specialist options for a cadre leader on the current directive."""
        pkg = self._refresh_current_package_snapshot()
        if not pkg:
            return [], "No directive selected", False

        if pkg.get("status") not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            return [], "Specialists unavailable for this status", False

        mode = pkg.get("mode", "")
        total_capacity = 3 if "Hard" in mode else 5
        current_total = len(pkg.get("signed_up", [])) + len(pkg.get("assigned_specialist_ids", []))
        if current_total >= total_capacity:
            return [], f"Directive full ({current_total}/{total_capacity})", False

        specialist_slots = _specialist_slots_allowed(pkg)
        current_specialists = len(pkg.get("assigned_specialist_ids", []))
        if current_specialists >= specialist_slots:
            return [], f"Specialist slots filled ({current_specialists}/{specialist_slots})", False

        req_roles = pkg.get("required_roles", [])
        cadre_roles = [
            r for r in req_roles
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(viewer, r)
        ]
        if not cadre_roles:
            return [], "No specialist reqs for your cadre", False

        guild = getattr(viewer, "guild", None) or _get_guild_from_bot()
        if not guild:
            return [], "Guild context unavailable", False

        cadre_assigned = 0
        for uid in pkg.get("assigned_specialist_ids", []):
            m = guild.get_member(uid) if guild else None
            if not m:
                continue
            m_roles = _member_role_names(m)
            if any(r in m_roles for r in cadre_roles):
                cadre_assigned += 1
        if cadre_assigned >= len(cadre_roles):
            return [], f"Your cadre slots filled ({cadre_assigned}/{len(cadre_roles)})", False

        tp_data = _load_tp()
        active_statuses = {STATUS_RECRUITING, STATUS_DEPLOYED}
        pkg_id = pkg.get("id")
        currently_assigned = set(pkg.get("assigned_specialist_ids", []))
        currently_signed = set(pkg.get("signed_up", []))
        already_assigned_elsewhere: set[int] = set()
        already_signed_elsewhere: set[int] = set()
        for p in tp_data.get("packages", {}).values():
            if p.get("id") == pkg_id:
                continue
            if p.get("status") in active_statuses:
                already_assigned_elsewhere.update(p.get("assigned_specialist_ids", []))
                already_signed_elsewhere.update(p.get("signed_up", []))

        options = []
        seen: set[int] = set()
        for m in guild.members:
            if m.bot or m.id in seen:
                continue
            if not _is_active(m):
                continue
            if m.id in currently_assigned:
                continue
            if m.id in currently_signed:
                continue
            if m.id in already_assigned_elsewhere:
                continue
            if m.id in already_signed_elsewhere:
                continue
            member_roles = _member_role_names(m)
            matches = [r for r in cadre_roles if r in member_roles]
            if not matches:
                continue
            options.append(
                discord.SelectOption(
                    label=m.display_name[:100],
                    value=str(m.id),
                    description=matches[0][:100],
                )
            )
            seen.add(m.id)
            if len(options) >= 25:
                break

        if not options:
            return [], "No eligible specialists available", False
        return options, "Select specialist to attach…", True

    def _build_unassign_specialist_options(self, viewer: discord.Member) -> tuple[list, str, bool]:
        """Build inline specialist detach options for a cadre leader on the current directive."""
        pkg = self._refresh_current_package_snapshot()
        if not pkg:
            return [], "No directive selected", False

        if pkg.get("status") not in (STATUS_RECRUITING, STATUS_DEPLOYED):
            return [], "Specialists unavailable for this status", False

        req_roles = pkg.get("required_roles", [])
        cadre_roles = [
            r for r in req_roles
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(viewer, r)
        ]
        if not cadre_roles:
            return [], "No specialist reqs for your cadre", False

        guild = getattr(viewer, "guild", None) or _get_guild_from_bot()
        if not guild:
            return [], "Guild context unavailable", False

        options = []
        seen: set[int] = set()
        for uid in pkg.get("assigned_specialist_ids", []):
            m = guild.get_member(uid) if guild else None
            if not m or m.bot or m.id in seen:
                continue
            member_roles = _member_role_names(m)
            matches = [r for r in cadre_roles if r in member_roles]
            if not matches:
                continue
            options.append(
                discord.SelectOption(
                    label=m.display_name[:100],
                    value=str(m.id),
                    description=matches[0][:100],
                )
            )
            seen.add(m.id)
            if len(options) >= 25:
                break

        if not options:
            return [], "No assigned specialists from your cadre", False
        return options, "Select specialist to detach…", True

    async def on_specialist_select(self, interaction: discord.Interaction):
        pkg = self._refresh_current_package_snapshot()
        req_roles = pkg.get("required_roles", []) if pkg else []
        cadre_roles = [
            r for r in req_roles
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(interaction.user, r)
        ]
        if not cadre_roles:
            await interaction.response.send_message(
                "You cannot assign cadre specialists to this directive.",
                ephemeral=True,
            )
            return

        selected = (interaction.data.get("values") or [None])[0]
        if not selected or selected == "none":
            await interaction.response.send_message("No eligible specialist to assign.", ephemeral=True)
            return

        specialist_member = interaction.guild.get_member(int(selected)) if interaction.guild else None
        if not specialist_member:
            await interaction.response.send_message("Could not resolve selected specialist.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success, msg = await assign_specialist(
            self._refresh_current_package_snapshot().get("id", ""),
            specialist_member,
            interaction.user,
            interaction.guild,
        )

        self._refresh_specialist_btn()
        self._refresh_assign_btn()
        f = self.current_file()
        await interaction.edit_original_response(
            embed=self.current_embed(),
            view=self,
            attachments=[f] if f else [],
        )
        await interaction.followup.send(msg, ephemeral=True)

    async def on_unassign_specialist_select(self, interaction: discord.Interaction):
        pkg = self._refresh_current_package_snapshot()
        req_roles = pkg.get("required_roles", []) if pkg else []
        cadre_roles = [
            r for r in req_roles
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(interaction.user, r)
        ]
        if not cadre_roles:
            await interaction.response.send_message(
                "You cannot unassign cadre specialists from this directive.",
                ephemeral=True,
            )
            return

        selected = (interaction.data.get("values") or [None])[0]
        if not selected or selected == "none":
            await interaction.response.send_message("No assigned specialist to detach.", ephemeral=True)
            return

        specialist_member = interaction.guild.get_member(int(selected)) if interaction.guild else None
        if not specialist_member:
            await interaction.response.send_message("Could not resolve selected specialist.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        success, msg = await unassign_specialist(
            self._refresh_current_package_snapshot().get("id", ""),
            specialist_member,
            interaction.user,
            interaction.guild,
        )

        self._refresh_specialist_btn()
        self._refresh_assign_btn()
        f = self.current_file()
        await interaction.edit_original_response(
            embed=self.current_embed(),
            view=self,
            attachments=[f] if f else [],
        )
        await interaction.followup.send(msg, ephemeral=True)

    async def assign_to_kt(self, interaction: discord.Interaction):
        pkg = self._refresh_current_package_snapshot()
        if pkg["status"] != STATUS_DISTRIBUTED:
            await interaction.response.send_message(
                f"Directive `{pkg.get('directive_code') or pkg['id']}` is `{pkg['status']}` — cannot accept.", ephemeral=True
            )
            return
        member = interaction.user
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member) or ("Debug" if _is_debug_mode() else None)
        if not company:
            await interaction.response.send_message("You must have a company role to accept directives.", ephemeral=True)
            return
        success, msg = await assign_package_to_kt(
            pkg["id"], "", company, member, interaction.guild or _get_guild_from_bot()
        )
        if not success:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await interaction.response.defer()
        assigned_pid = pkg["id"]

        # Live-update the current captain/LT paginator view so accepted package disappears.
        if self.viewer and _is_captain_or_lt(self.viewer):
            self.packages = [p for p in self.packages if p.get("id") != assigned_pid]
            if not self.packages:
                await interaction.edit_original_response(
                    content="No active strike directives for your role.",
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
                f"Directive `{pkg.get('directive_code') or pkg['id']}` is `{pkg['status']}` — cannot assign specialist.", ephemeral=True
            )
            return
        mode = pkg.get("mode", "")
        total_capacity = 3 if "Hard" in mode else 5
        current_total = len(pkg.get("signed_up", [])) + len(pkg.get("assigned_specialist_ids", []))
        if current_total >= total_capacity:
            await interaction.response.send_message(
                f"Directive `{pkg.get('directive_code') or pkg['id']}` is already at full capacity ({current_total}/{total_capacity}).",
                ephemeral=True,
            )
            return
        req_roles = pkg.get("required_roles", [])
        # Only show the roles this cadre leader is responsible for
        cadre_roles = [
            r for r in req_roles
            if r in _CADRE_SPECIALIST_ROLES and _cadre_leader_owns(interaction.user, r)
        ]
        if not cadre_roles:
            await interaction.response.send_message("This directive has no specialist requirements for your cadre.", ephemeral=True)
            return
        view = SpecialistAssignView(package_id=pkg["id"], required_roles=cadre_roles, guild=interaction.guild)
        if not view.has_assignable_options:
            await interaction.response.send_message(
                "No eligible specialists are currently available for assignment.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Select the specialist to attach to `{pkg.get('directive_code') or pkg['id']}`:", view=view, ephemeral=True
        )

    def current_embed(self) -> discord.Embed:
        self._refresh_current_package_snapshot()
        pkg = self.packages[self.index]
        resolved_guild = getattr(self.viewer, "guild", None) if self.viewer else _get_guild_from_bot()
        embed = _build_package_embed(
            pkg,
            self.rep,
            index=self.index + 1,
            total=len(self.packages),
            viewer=self.viewer,
            guild=resolved_guild,
        )
        if pkg.get("status") in (STATUS_RECRUITING, STATUS_DEPLOYED):
            embed = _inject_readiness_fields_for_view(embed, pkg, resolved_guild)
        return embed

    def current_file(self) -> "discord.File | None":
        self._refresh_current_package_snapshot()
        return _classification_file(self.packages[self.index])

    def _refresh_specialist_btn(self) -> None:
        """Refresh inline specialist selector for the currently viewed directive."""
        for item in self.children:
            custom_id = getattr(item, "custom_id", None)
            if custom_id not in ("tp_assign_specialist_inline", "tp_unassign_specialist_inline"):
                continue
            if not self.viewer:
                item.disabled = True
                item.options = [discord.SelectOption(label="No viewer context", value="none")]
                item.placeholder = "Specialist selector unavailable"
                continue
            if custom_id == "tp_assign_specialist_inline":
                opts, placeholder, enabled = self._build_specialist_options(self.viewer)
                item.options = opts or [discord.SelectOption(label="No eligible specialists", value="none")]
            else:
                opts, placeholder, enabled = self._build_unassign_specialist_options(self.viewer)
                item.options = opts or [discord.SelectOption(label="No assigned specialists", value="none")]
            item.placeholder = placeholder
            item.disabled = not enabled

    def _refresh_assign_btn(self) -> None:
        """Disable company-accept button when package is not DISTRIBUTED."""
        pkg = self._refresh_current_package_snapshot()
        can_assign = pkg.get("status") == STATUS_DISTRIBUTED
        for item in self.children:
            if getattr(item, "custom_id", None) == "tp_assign_kt":
                item.disabled = not can_assign
                break

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
    "Blademaster", "Forgemaster", "Chief Apothecary",
    "High Chaplain", "Void Warden", "Castellan", "Huntmaster",
}

_HC_ROLES = {
    "Watch Master", "Blademaster", "Forgemaster", "Chief Apothecary",
    "High Chaplain", "Huntmaster", "Void Warden", "Castellan",
    "Venerable Dreadnought",
}
_COMMAND_ROLES = {"Watch Captain", "Watch Lieutenant"} | _CADRE_LEADER_ROLES
_KT_COMMAND_ROLES = {"Watch Sergeant", "Bladeguard"}


def _is_cadre_leader(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    roles = _member_role_names(member)
    return bool(roles.intersection(_CADRE_LEADER_ROLES))


def _cadre_leader_owns(cadre_leader: discord.Member, specialist_role: str) -> bool:
    """Return True if the cadre leader has authority over the given specialist role.
    
    Phase 1: Try config-backed mapping first, fall back to hardcoded if not available.
    Cadre leaders can assign themselves only if they personally hold the required role.
    Forgemaster manages Dreadnoughts administratively but cannot self-assign as one.
    """
    # Try config-backed mapping first (Phase 1+)
    try:
        config_mapping = _load_cadre_ownership_config()
        if config_mapping:
            cl_roles = _member_role_names(cadre_leader)
            for cl_role, cadre_info in config_mapping.items():
                if cl_role in cl_roles:
                    # Check if specialist_role is in owned members
                    member_names = {m.get("name") for m in cadre_info["members"] if m.get("name")}
                    if specialist_role in member_names:
                        return True
    except Exception as e:
        _g.logger.warning(f"[TP Phase1] Error checking config cadre ownership: {e}")
    
    # Fallback to hardcoded mapping (Phase 1 compatibility)
    hardcoded_mapping = _get_cadre_ownership_mapping_hardcoded()
    cl_roles = _member_role_names(cadre_leader)
    for cl_role, owned in hardcoded_mapping.items():
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


# /request_strike_directives — WM only
@app_commands.command(
    name="request_strike_directives",
    description="[Watch Master] Request a new batch of Ordo Xenos strike directives.",
)
async def request_strike_directives(interaction: discord.Interaction):
    if not _b("check_command_permission")(interaction.user, "request_strike_directives"):
        await interaction.response.send_message(
            "Only the Watch Master may request strike directives.", ephemeral=True
        )
        return

    # Check weekly request quota
    data = _load_tp()
    cycle = data.get("cycle", {})
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    max_per_week = config_tp.get("request_strike_directives_max_per_week", 2)
    
    now_utc = datetime.now(timezone.utc)
    can_request, error_msg = _can_request_strike_directives(cycle, now_utc, max_per_week)
    if not can_request:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    packages = await generate_packages(guild, actor=interaction.user)
    data = _load_tp()
    rep = data.get("rep", 0.0)

    if not packages:
        await interaction.followup.send("No active Kill Teams found — cannot generate packages.", ephemeral=True)
        return

    view = PackagePaginatorView(packages, rep, show_distribute=True, viewer=interaction.user)
    _pf = view.current_file()
    await interaction.followup.send(
        content=f"**{len(packages)} strike directive{'s' if len(packages) != 1 else ''} received from Ordo Xenos.** "
                f"Review below and press **Distribute All** when ready.",
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
        **_file_kwarg(_pf),
    )


# /view_strike_directives — role-overloaded view
@app_commands.command(
    name="view_strike_directives",
    description="View Ordo Xenos strike directives relevant to your role.",
)
async def view_strike_directives(interaction: discord.Interaction):
    member = interaction.user
    await interaction.response.defer(ephemeral=True)
    data = _load_tp()
    rep = data.get("rep", 0.0)
    packages = data.get("packages", {})
    pkgs = _visible_active_packages_for_member(member, packages)

    if not pkgs:
        await interaction.followup.send("No active strike directives for your role.", ephemeral=True)
        return

    view = PackagePaginatorView(pkgs, rep, show_distribute=False, viewer=member)
    _pf = view.current_file()
    await interaction.followup.send(
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
        **_file_kwarg(_pf),
    )


@app_commands.command(
    name="queue_strike",
    description="Mark yourself ready for eligible strike directives for a limited time.",
)
@app_commands.describe(
    minutes="How long to remain queued, in minutes.",
    mode_preference="Directive preference: any, hard, or omega.",
)
async def queue_strike(
    interaction: discord.Interaction,
    minutes: int = 60,
    mode_preference: str = "any",
):
    member = interaction.user
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)

    if not _member_meets_strike_queue_baseline(member):
        await interaction.followup.send("Only active brothers may join the strike queue.", ephemeral=True)
        return

    if minutes < 5 or minutes > 240:
        await interaction.followup.send("Queue duration must be between 5 and 240 minutes.", ephemeral=True)
        return

    normalized_mode = _normalize_strike_queue_mode(mode_preference)
    if str(mode_preference or "").strip().lower() not in {
        "", "any", "hard", "hard-strat", "hard_strat", "omega", "omega-strat", "omega_strat"
    }:
        await interaction.followup.send("Mode preference must be one of: any, hard, omega.", ephemeral=True)
        return
    if normalized_mode == "omega" and not _tp_get_player_platform(member):
        await interaction.followup.send("Omega queueing requires a PC or Console role.", ephemeral=True)
        return

    data = _load_tp()
    packages = data.get("packages", {})
    commitment = _member_active_directive_commitment(member.id, data)
    if commitment is not None:
        directive_code, is_specialist = commitment
        if is_specialist:
            await interaction.followup.send(
                f"You are already committed as a specialist to directive `{directive_code}`. Complete that operation first.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"You are already committed to directive `{directive_code}`. Complete that operation first.",
            ephemeral=True,
        )
        return

    queue_eligible = _queue_eligible_packages_for_member(member, packages, normalized_mode, guild)

    async with _STRIKE_QUEUE_LOCK:
        queue_data = _load_strike_queue()
        queue_data, _ = _prune_strike_queue(queue_data)
        queue_data.setdefault("entries", {})[str(member.id)] = {
            "user_id": int(member.id),
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
            "requested_minutes": int(minutes),
            "mode_preference": normalized_mode,
            "platform": _tp_get_player_platform(member),
        }
        _save_strike_queue(queue_data)

    match_count = await _evaluate_strike_queue_matches(guild)

    queue_eligible_count = len(queue_eligible)
    mode_text = normalized_mode.upper() if normalized_mode != "any" else "ANY"
    match_text = ""
    if match_count > 0:
        noun = "directive" if match_count == 1 else "directives"
        match_text = f" A full strike element was committed for **{match_count}** {noun}; the queue was cleared and the directive roster updated."
    await interaction.followup.send(
        (
            f"You are queued for strike directives for the next **{minutes}** minutes. "
            f"Mode preference: **{mode_text}**. "
            f"Current fully-open directives eligible for queue matching: **{queue_eligible_count}**."
            f"{match_text}"
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="leave_strike_queue",
    description="Remove yourself from the strike directive queue.",
)
async def leave_strike_queue(interaction: discord.Interaction):
    member = interaction.user
    await interaction.response.defer(ephemeral=True)

    async with _STRIKE_QUEUE_LOCK:
        queue_data = _load_strike_queue()
        queue_data, _ = _prune_strike_queue(queue_data)
        removed = queue_data.setdefault("entries", {}).pop(str(member.id), None)
        _save_strike_queue(queue_data)

    if removed:
        await interaction.followup.send("You have been removed from the strike queue.", ephemeral=True)
        return
    await interaction.followup.send("You are not currently in the strike queue.", ephemeral=True)


@app_commands.command(
    name="strike_queue_status",
    description="View your current strike queue status.",
)
async def strike_queue_status(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)

    data = _load_tp()
    packages = data.get("packages", {})

    async with _STRIKE_QUEUE_LOCK:
        queue_data = _load_strike_queue()
        queue_data, _ = _prune_strike_queue(queue_data)
        active_entries = queue_data.setdefault("entries", {})
        active_entry_ids = set(active_entries.keys())
        queue_data, _ = _prune_announced_strike_queue_matches(queue_data, packages, active_entry_ids)
        _save_strike_queue(queue_data)
        entry = active_entries.get(str(member.id))
        ordered_entries = _ordered_queue_entries(active_entries)

    if not entry:
        queue_eligible = _queue_eligible_packages_for_member(member, packages, "any", guild)
        embed = discord.Embed(title="Strike Queue Status", color=0xA31919)
        embed.description = (
            "You are not currently queued. "
            f"Current fully-open directives eligible for queue matching: **{len(queue_eligible)}**."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    queue_position = next((idx + 1 for idx, (uid, _e) in enumerate(ordered_entries) if uid == str(member.id)), 1)
    queue_total = len(ordered_entries)

    queued_at = str(entry.get("queued_at") or "").strip()
    queued_at_text = queued_at
    try:
        queued_dt = datetime.fromisoformat(queued_at)
        if queued_dt.tzinfo is None:
            queued_dt = queued_dt.replace(tzinfo=timezone.utc)
        queued_at_text = f"<t:{int(queued_dt.timestamp())}:R>"
    except Exception:
        pass

    expires_at = str(entry.get("expires_at") or "").strip()
    expiry_text = expires_at
    try:
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        expiry_text = f"<t:{int(expiry.timestamp())}:R>"
    except Exception:
        pass

    normalized_mode = str(entry.get("mode_preference") or "any")
    mode_text = normalized_mode.upper()
    queue_eligible = _queue_eligible_packages_for_member(member, packages, normalized_mode, guild)
    seats_per_sweep = sum(3 if "Hard" in str(pkg.get("mode") or "") else 5 for pkg in queue_eligible)
    sweep_minutes = _strike_queue_match_sweep_minutes()
    member_tentative_codes = _member_tentative_codes(queue_data, packages, int(member.id))
    eta_text = _queue_eta_window_text_with_context(
        queue_position,
        seats_per_sweep,
        sweep_minutes,
        queue_eligible,
        member_tentative_codes,
    )

    queue_names = []
    for uid, _e in ordered_entries:
        try:
            name = _queue_member_display(guild, int(uid))
        except Exception:
            name = f"Brother {uid}"
        if uid == str(member.id):
            name = f"{name} (you)"
        queue_names.append(name)

    queue_preview_cap = 10
    queue_preview = queue_names[:queue_preview_cap]
    queue_preview_text = "\n".join(f"{idx + 1}. {name}" for idx, name in enumerate(queue_preview))
    if queue_total > queue_preview_cap:
        queue_preview_text += f"\n+{queue_total - queue_preview_cap} more"

    tentative_groups = _tentative_groups_for_status(queue_data, packages, guild)
    tentative_cap = 3
    tentative_preview = tentative_groups[:tentative_cap]
    tentative_text = "\n".join(tentative_preview) if tentative_preview else "No tentative groups currently tracked."
    if len(tentative_groups) > tentative_cap:
        tentative_text += f"\n+{len(tentative_groups) - tentative_cap} more"

    embed = discord.Embed(title="Strike Queue Status", color=0xA31919)
    embed.add_field(
        name="Your Queue Status",
        value=(
            f"Mode: **{mode_text}**\n"
            f"Position: **{queue_position}/{queue_total}**\n"
            f"Queued: {queued_at_text}\n"
            f"Expires: {expiry_text}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Estimated Wait",
        value=(
            f"{eta_text}\n"
            f"Sweep cadence: every **{sweep_minutes} min**\n"
            f"Eligible fully-open directives now: **{len(queue_eligible)}**"
        ),
        inline=False,
    )
    embed.add_field(name="Brothers In Queue", value=queue_preview_text or "No queued brothers.", inline=False)
    embed.add_field(name="Tentative Groups", value=tentative_text, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


# /log_strike_report
@app_commands.command(
    name="log_strike_report",
    description="Log a completed Ordo Xenos strike report.",
)
@app_commands.describe(aar_link="Link to the After Action Report")
async def log_strike_report(
    interaction: discord.Interaction,
    aar_link: str,
):
    member = interaction.user
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)

    # Resolve AAR early so we can match against directives
    aar_key, aar_record = _resolve_aar_record_for_link(aar_link)
    if not aar_record:
        aar_record = await _parse_live_aar_for_link(aar_link, guild)
    if not aar_record:
        await interaction.followup.send(
            "AAR link could not be resolved or parsed.", ephemeral=True
        )
        return

    aar_mission_clean = _canonical_mission_name(
        str(aar_record.get("mission") or aar_record.get("mission_name") or "")
    )
    aar_diff = str(aar_record.get("difficulty_class") or "").strip().lower()

    # Find DEPLOYED directives this member is attached to
    data = _load_tp()
    candidates = [
        pkg for pkg in data.get("packages", {}).values()
        if pkg.get("status") == STATUS_DEPLOYED
        and (
            member.id in pkg.get("signed_up", [])
            or member.id in pkg.get("assigned_specialist_ids", [])
        )
    ]

    if not candidates:
        await interaction.followup.send(
            "You are not attached to any deployed strike directives. "
            "Ensure your Kill Team has signed up and all requirements are met before submitting.",
            ephemeral=True,
        )
        return

    # Narrow to directives whose mission + difficulty match the AAR
    matching = []
    for pkg in candidates:
        expected_mission = str(pkg.get("mission_id") or "")
        for op in (_load_operations() or []):
            if op.get("id") == pkg.get("mission_id"):
                expected_mission = str(op.get("name") or expected_mission)
                break
        if _canonical_mission_name(expected_mission) != aar_mission_clean:
            continue
        if _expected_difficulty_for_mode(pkg.get("mode", "")) != aar_diff:
            continue
        matching.append(pkg)

    # Use best match; fall back to first candidate so submit_package returns a proper error
    target_pkg = matching[0] if matching else candidates[0]

    success, msg = await submit_package(target_pkg["id"], aar_link, member, guild)
    if success:
        data = _load_tp()
        pkg = data.get("packages", {}).get(target_pkg["id"], target_pkg)
        classification = str(pkg.get("classification") or "STRIKE").strip().title()
        completed_kt = str(pkg.get("assigned_kt") or "Unassigned")
        rep_before = float(pkg.get("rep_before", data.get("rep", _REP_NEUTRAL)) or _REP_NEUTRAL)
        rep_after = float(pkg.get("rep_after", data.get("rep", _REP_NEUTRAL)) or _REP_NEUTRAL)
        standing_before = _standing_skull_bar(rep_before)
        standing_after = _standing_skull_bar(rep_after)
        state_before = _standing_state_name(rep_before)
        state_after = _standing_state_name(rep_after)
        standing_before_line = f"{standing_before} **{state_before}**" if standing_before else f"**{state_before}**"
        standing_after_line = f"{standing_after} **{state_after}**" if standing_after else f"**{state_after}**"

        display_code = pkg.get("directive_code") or target_pkg["id"]
        directive_name = pkg.get("directive_name", "")
        directive_display = f"`{display_code}`"
        if directive_name:
            directive_display = f"`{display_code}` — {directive_name}"

        embed = discord.Embed(
            title=f"{_DW_EMOJI} sᴛʀɪᴋᴇ ʀᴇᴘᴏʀᴛ ʟᴏɢɢᴇᴅ {_DW_EMOJI}",
            description=msg,
            color=0x2ECC71,
        )
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url if member.display_avatar else None,
        )
        embed.add_field(name=f"▸ {classification} Directive", value=directive_display, inline=True)
        embed.add_field(name="▸ Kill Team Completed", value=completed_kt, inline=True)
        embed.add_field(
            name="▸ Ordo Xenos Standing",
            value=(
                f"{standing_before_line} `{rep_before:.2f}`\n"
                f"-> {standing_after_line} `{rep_after:.2f}`\n"
                f"Delta: `{(rep_after - rep_before):+.2f}`"
            ),
            inline=False,
        )
        embed.add_field(name="▸ AAR", value=aar_link, inline=False)

        report_header = "**```++ 𝐒𝐓𝐑𝐈𝐊𝐄 𝐑𝐄𝐏𝐎𝐑𝐓 ++```**"
        report_footer = "**```++ 𝐄𝐍𝐃 𝐎𝐅 𝐑𝐄𝐏𝐎𝐑𝐓 ++```**"
        config_tp = (_b("CONFIG") or {}).get("target_packages", {})
        report_channel_id = config_tp.get("strike_report_channel_id")
        report_channel = await _resolve_channel(guild, int(report_channel_id)) if (guild and report_channel_id) else None
        if not report_channel:
            await interaction.followup.send(
                "Strike report logged, but configured strike report channel is not resolvable.",
                ephemeral=True,
            )
            return

        completion_img = os.path.join(_ASSETS_DIR, "Mission_Complete.png")
        if os.path.exists(completion_img):
            comp_file = discord.File(completion_img, filename="mission_complet.png")
            embed.set_image(url="attachment://mission_complet.png")
            await _notify_send(report_channel, guild, content=report_header, embed=embed, file=comp_file)
        else:
            await _notify_send(report_channel, guild, content=report_header, embed=embed)
        await _notify_send(report_channel, guild, content=report_footer)
        await interaction.followup.send(
            f"Strike report posted to {getattr(report_channel, 'mention', '#strike-reports')}",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(msg, ephemeral=True)


# Backward-compatible Python alias for older imports/call sites.
submit_target_package = log_strike_report


# /strike_directive_status
@app_commands.command(
    name="strike_directive_status",
    description="View the full status of a specific strike directive.",
)
@app_commands.describe(directive_id="The strike directive ID (e.g. SD-734-THETA)")
async def strike_directive_status(
    interaction: discord.Interaction,
    directive_id: str,
):
    member = interaction.user
    package_id = directive_id.strip().upper()

    can_view = (
        _is_admin(member)
        or _is_watch_master(member)
        or _is_captain_or_lt(member)
        or _is_cadre_leader(member)
    )

    data = _load_tp()
    pkg = data["packages"].get(package_id)
    if not pkg:
        # Try directive_code lookup
        for _pid_key, _p in data["packages"].items():
            if (_p.get("directive_code") or "").upper() == package_id:
                package_id = _pid_key
                pkg = _p
                break
    if not pkg:
        await interaction.response.send_message(f"Directive `{package_id}` not found.", ephemeral=True)
        return

    # Non-command members can only view their own KT's directives
    if not can_view:
        from .forge_ops import _resolve_killteam_for_member
        kt = _resolve_killteam_for_member(member)
        if pkg.get("assigned_kt") != kt:
            await interaction.response.send_message(
                f"Directive `{pkg.get('directive_code') or package_id}` is not assigned to your Kill Team.", ephemeral=True
            )
            return

    embed = _build_package_embed(pkg, data.get("rep", 0.0), viewer=interaction.user, guild=interaction.guild)

    await interaction.response.send_message(embed=embed, **_file_kwarg(_classification_file(pkg)), ephemeral=True)


# /repost_directive_embed — WM/admin only
@app_commands.command(
    name="repost_directive_embed",
    description="[Watch Master/Forgemaster] Re-post a directive sign-up embed to the correct channel.",
)
@app_commands.describe(directive_id="The directive code (e.g. 542-CHI) or internal ID")
async def repost_directive_embed(interaction: discord.Interaction, directive_id: str):
    if not _b("check_command_permission")(interaction.user, "repost_directive_embed"):
        await interaction.response.send_message("Only the Watch Master or Forgemaster may repost directive embeds.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    data = _load_tp()
    pkg_id = directive_id.strip().upper()
    pkg = data["packages"].get(pkg_id)
    if not pkg:
        for _pid, _p in data["packages"].items():
            if (_p.get("directive_code") or "").upper() == pkg_id:
                pkg_id = _pid
                pkg = _p
                break
    if not pkg:
        await interaction.followup.send(f"Directive `{directive_id}` not found.", ephemeral=True)
        return

    if pkg["status"] not in (STATUS_PENDING_SGT, STATUS_RECRUITING, STATUS_DEPLOYED):
        await interaction.followup.send(
            f"Directive is `{pkg['status']}` — can only repost for RECRUITING or DEPLOYED directives.",
            ephemeral=True,
        )
        return

    guild = interaction.guild or _get_guild_from_bot()
    if pkg["status"] == STATUS_PENDING_SGT:
        async with _TP_LOCK:
            latest = _load_tp()
            if pkg_id in latest.get("packages", {}):
                latest["packages"][pkg_id]["status"] = STATUS_RECRUITING
                latest["packages"][pkg_id]["sgt_accept_message_id"] = None
                latest["packages"][pkg_id]["sgt_accept_channel_id"] = None
                _save_tp(latest)
                pkg = latest["packages"][pkg_id]

    await _post_signup_embed(pkg_id, guild)

    code = pkg.get("directive_code") or pkg_id
    name = pkg.get("directive_name", "")
    await interaction.followup.send(
        f"sign-up embed reposted for `{code}`{': ' + name if name else ''}.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Register commands + expiry loop
# ---------------------------------------------------------------------------

# /post_cycle_reports — WM/admin only manual trigger
@app_commands.command(
    name="post_cycle_reports",
    description="[Watch Master] Manually post cycle-close reports and honors for a directive batch.",
)
@app_commands.describe(batch="Batch date to report on, e.g. 20260608. Defaults to the current batch.")
async def post_cycle_reports(interaction: discord.Interaction, batch: Optional[str] = None):
    if not _b("check_command_permission")(interaction.user, "post_cycle_reports"):
        await interaction.response.send_message(
            "You do not have permission to use this command.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild or _get_guild_from_bot()
    data = _load_tp()

    if not data.get("packages"):
        await interaction.followup.send("No directive data found.", ephemeral=True)
        return

    # Normalize batch arg: accept "20260608" or "BATCH-20260608"
    resolved_batch_id: Optional[str] = None
    if batch:
        cleaned = batch.strip().upper().replace("BATCH-", "")
        resolved_batch_id = f"BATCH-{cleaned}"
        # Verify at least one package matches
        matching = [p for p in data["packages"].values() if _batch_id_for_package(p) == resolved_batch_id]
        if not matching:
            await interaction.followup.send(
                f"No directives found for batch `{resolved_batch_id}`. "
                f"Available batches: {', '.join(sorted({_batch_id_for_package(p) for p in data['packages'].values()}, reverse=True))}",
                ephemeral=True,
            )
            return

    target_batch_id = _resolve_summary_batch_id(data, resolved_batch_id)
    if not target_batch_id:
        await interaction.followup.send("No directive batch could be resolved for reporting.", ephemeral=True)
        return

    try:
        posted = await _post_batch_summary(guild, data, batch_id=target_batch_id)
        if posted and _is_batch_terminal(data, target_batch_id):
            cycle = data.setdefault("cycle", {})
            _mark_batch_summary_posted(cycle, target_batch_id, datetime.now(timezone.utc))
            _save_tp(data)
        await interaction.followup.send(
            f"Cycle reports posted for `{target_batch_id}`: fortress-wide, per-KT, and highcom.",
            ephemeral=True,
        )
    except Exception as exc:
        _g.logger.error(f"[TP] Manual cycle report failed: {exc}")
        await interaction.followup.send(f"Report failed: {exc}", ephemeral=True)


def _register_commands(tree: app_commands.CommandTree) -> None:
    for cmd in (
        request_strike_directives,
        view_strike_directives,
        queue_strike,
        leave_strike_queue,
        strike_queue_status,
        log_strike_report,
        strike_directive_status,
        repost_directive_embed,
        post_cycle_reports,
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


@_tasks.loop(minutes=15)
async def _strike_queue_match_sweep_loop():
    """Periodically re-check queued brothers against current directive state."""
    try:
        m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
        bot = getattr(m, "bot", None) if m else None
        if not bot:
            return
        guild_id = _b("CONFIG") and (_b("CONFIG") or {}).get("guild_id")
        if not guild_id:
            for guild in bot.guilds:
                await _evaluate_strike_queue_matches(guild)
        else:
            guild = bot.get_guild(int(guild_id))
            if guild:
                await _evaluate_strike_queue_matches(guild)
    except Exception as e:
        _g.logger.error(f"[TP] Strike queue sweep loop error: {e}")



async def register_persistent_views() -> None:
    """Call from on_ready to restore TP persistent views after a bot restart.

    Each active package's SignUpView is re-registered scoped to its message ID.
    """
    try:
        data = _load_tp()
        signup_count = 0
        for package_id, pkg in data.get("packages", {}).items():
            status = pkg.get("status")
            if status == STATUS_PENDING_SGT:
                # Legacy state: normalize into recruiting so the sign-up view model is used.
                status = STATUS_RECRUITING

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
                f"target_packages_ops: registered {signup_count} SignUp persistent views"
            )
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"target_packages_ops: register_persistent_views failed: {exc}")


# Public exports
__all__ = [
    "request_strike_directives",
    "view_strike_directives",
    "log_strike_report",
    "submit_strike_package",
    "submit_target_package",
    "strike_directive_status",
    "post_cycle_reports",
    "request_strike_packages",
    "view_strike_packages",
    "strike_package_status",
    "request_target_packages",
    "view_target_packages",
    "target_package_status",
    "_register_commands",
    "_tp_expiry_loop",
    "generate_packages",
    "distribute_packages",
    "expire_packages",
    "register_persistent_views",
]

# Backward-compatible Python aliases for older imports/call sites.
submit_strike_package = log_strike_report
request_strike_packages = request_strike_directives
view_strike_packages = view_strike_directives
strike_package_status = strike_directive_status
request_target_packages = request_strike_directives
view_target_packages = view_strike_directives
target_package_status = strike_directive_status
