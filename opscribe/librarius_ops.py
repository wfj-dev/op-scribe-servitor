"""Librarian / Warp Corruption subsystem.

Mirrors the Techmarine armor system in shape but uses contagion + Librarian
self-burden mechanics. Provides:

- Persistent state for warp exposure (per brother + per Librarian)
- Personal warding charge pool (regenerates on a timer)
- Librarian exposure decay (calculated lazily on read)
- AAR hook: direct Black Laurels gain, contagion spread, penalty roll
- Warp Sanction status surfacing for brothers (visibility-restricted)
- Commands: /warp_cleanse, /warp_status, /librarium_chronicle,
  /librarium_override
"""

import os
import json
import math
import random
import shutil
import discord
from discord import app_commands
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sys as _sys

from .constants import *  # noqa: F401,F403
from .constants import _strip_display_name
from .flavor_text import *  # noqa: F401,F403
from .flavor_text import _warp_sanction_key_for_points  # private name, not re-exported by *
from .permissions import *  # noqa: F401,F403
from . import _bot_globals as _g


def _b(name):
    """Resolve name via bot module (test-mock compatibility)."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


# ---------------------------------------------------------------------------
# Config accessors — config.warp_corruption block (with safe fallbacks)
# ---------------------------------------------------------------------------

def _warp_config() -> dict:
    try:
        return (_g.CONFIG or {}).get("warp_corruption", {}) or {}
    except Exception:
        return {}


def _get_librarium_watch_channel_id() -> Optional[int]:
    cfg = _warp_config()
    raw = cfg.get("librarium_watch_channel_id")
    try:
        if raw:
            return int(raw)
    except Exception:
        pass
    # Fallback to constant or Librarius staff channel
    fallback = LIBRARIUM_WATCH_CHANNEL_ID or LIBRARIUS_STAFF_CHANNEL_ID
    return int(fallback) if fallback else None


def _get_warp_status_allowed_channels() -> set:
    cfg = _warp_config()
    raw = cfg.get("warp_status_allowed_channels") or []
    out = set()
    for v in raw:
        try:
            out.add(int(v))
        except Exception:
            continue
    return out


def _get_sanction_role_ids() -> dict:
    """Return {sanction_key: role_id_int} from config (only populated entries)."""
    cfg = _warp_config()
    raw = cfg.get("sanction_role_ids", {}) or {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        try:
            if v:
                out[str(k)] = int(v)
        except Exception:
            continue
    return out


def _get_brother_tier_bands() -> Dict[Optional[str], Tuple[int, Optional[int]]]:
    cfg = _warp_config()
    tiers = cfg.get("brother_probability_tiers")
    if not tiers:
        return {k: v for k, v in WARP_BROTHER_TIER_BANDS.items()}
    bands: Dict[Optional[str], Tuple[int, Optional[int]]] = {}
    for entry in tiers:
        tier = entry.get("tier")
        lo = int(entry.get("min", 0))
        hi = entry.get("max")
        hi_val = int(hi) if hi is not None else None
        bands[tier] = (lo, hi_val)
    return bands


def _get_librarian_tier_bands() -> Dict[Optional[str], Tuple[int, Optional[int]]]:
    cfg = _warp_config()
    tiers = cfg.get("librarian_probability_tiers")
    if not tiers:
        return {k: v for k, v in WARP_LIBRARIAN_TIER_BANDS.items()}
    bands: Dict[Optional[str], Tuple[int, Optional[int]]] = {}
    for entry in tiers:
        tier = entry.get("tier")
        lo = int(entry.get("min", 0))
        hi = entry.get("max")
        hi_val = int(hi) if hi is not None else None
        bands[tier] = (lo, hi_val)
    return bands


def _get_penalty_probabilities() -> dict:
    """Derive ``{tier: {penalty: prob}}`` from the consolidated tier list.

    Each ``brother_probability_tiers`` entry now owns its ``penalty_distribution``
    (mirrors armor's ``damage_weights`` baked into ``probability_tiers``).
    Falls back to ``WARP_PENALTY_PROBABILITIES`` if config is missing.
    """
    cfg = _warp_config()
    tiers = cfg.get("brother_probability_tiers") or []
    if not tiers:
        return WARP_PENALTY_PROBABILITIES
    out: Dict[Optional[str], Dict[int, float]] = {None: {0: 1.0}}
    for entry in tiers:
        tier = entry.get("tier")
        dist = entry.get("penalty_distribution") or {}
        try:
            out[tier] = {int(k): float(v) for k, v in dist.items()}
        except Exception:
            continue
    return out


def _get_spread_chances() -> Dict[str, float]:
    """Derive ``{tier: chance}`` from the consolidated tier list."""
    cfg = _warp_config()
    tiers = cfg.get("brother_probability_tiers") or []
    if not tiers:
        return dict(WARP_SPREAD_CHANCES)
    out: Dict[str, float] = {}
    for entry in tiers:
        tier = entry.get("tier")
        if not tier:
            continue
        try:
            out[str(tier)] = float(entry.get("spread_chance", 0.0))
        except Exception:
            continue
    return out


def _cfg_int(key: str, default: int) -> int:
    cfg = _warp_config()
    try:
        v = cfg.get(key)
        return int(v) if v is not None else default
    except Exception:
        return default


def _cfg_float(key: str, default: float) -> float:
    cfg = _warp_config()
    try:
        v = cfg.get(key)
        return float(v) if v is not None else default
    except Exception:
        return default


def _get_bl_exposure_gain() -> Dict[str, int]:
    cfg = _warp_config()
    raw = cfg.get("bl_exposure_gain") or {}
    if not raw:
        return dict(WARP_BL_EXPOSURE_GAIN)
    out: Dict[str, int] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except Exception:
            continue
    return out


# Default intensive cleanse costs (mirrors INTENSIVE_BLESSING_COSTS for armor).
# Charges scale with severity; corrupted recipients pay the top tier.
_DEFAULT_INTENSIVE_CLEANSE_COSTS = {
    "screening_due": 2,
    "under_review": 3,
    "restricted": 4,
    "corrupted": 4,
}


def _get_intensive_cleanse_cost(sanction_key: str, warp_corrupted: bool = False) -> int:
    """Return the charge cost for an intensive cleanse given the recipient state.

    Mirrors armor's ``_get_intensive_blessing_cost`` shape: the cost climbs with
    severity, and the corrupted flag promotes any sanction tier to the top cost.
    """
    cfg = _warp_config()
    raw = cfg.get("intensive_cleanse_costs") or _DEFAULT_INTENSIVE_CLEANSE_COSTS
    key = "corrupted" if warp_corrupted else sanction_key
    try:
        return int(raw.get(key) or _DEFAULT_INTENSIVE_CLEANSE_COSTS.get(key, 4))
    except Exception:
        return _DEFAULT_INTENSIVE_CLEANSE_COSTS.get(key, 4)


# ---------------------------------------------------------------------------
# Contagion graph helpers (super-spreader detection + map rendering)
# ---------------------------------------------------------------------------

def _compute_outgoing_infections(
    source_id: int,
    states: Optional[dict] = None,
    window_hours: int = 24,
) -> List[str]:
    """Return target user IDs (str) infected *by* ``source_id`` in the window.

    Reads each brother's ``spread_history`` (recipient-side records) and
    inverts the lookup. Caller may pass pre-loaded ``states`` to avoid IO.
    """
    if states is None:
        try:
            states = _load_warp_exposure()
        except Exception:
            return []
    src = str(source_id)
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    targets: List[str] = []
    for uid, raw in (states or {}).items():
        history = (raw or {}).get("spread_history") or []
        for entry in history:
            if entry.get("source_id") != src:
                continue
            try:
                ts = datetime.fromisoformat(entry.get("ts", ""))
            except Exception:
                continue
            if ts >= cutoff:
                targets.append(str(uid))
                break  # one outbound edge per (source, target)
    return targets


def _is_super_spreader(
    source_id: int,
    states: Optional[dict] = None,
    window_hours: int = 24,
) -> Tuple[bool, int]:
    """Return ``(is_super_spreader, outgoing_count)`` for a source."""
    threshold = _cfg_int("super_spreader_threshold", 3)
    targets = _compute_outgoing_infections(source_id, states=states, window_hours=window_hours)
    return (len(targets) >= threshold and threshold > 0, len(targets))


def _warp_node_label(guild: "discord.Guild", data: dict, node_uid: str) -> str:
    """Render compact single-line node label (icon + styled name + cycles + flags)."""
    nraw = data.get(str(node_uid)) or {}
    npts = int(nraw.get("points", 0) or 0)
    n_is_lib = bool(nraw.get("is_librarian"))
    if n_is_lib:
        nt = _librarian_tier_for_points(npts)
        n_icon = f"{WARP_LIBRARIAN_MARKER_ICON}{WARP_LIBRARIAN_TIER_ICON.get(nt, '🟢')}"
    else:
        nt = _brother_tier_for_points(npts)
        n_icon = WARP_BROTHER_TIER_ICON.get(nt, "🟢")
    n_flags = ""
    try:
        n_is_super, _ = _is_super_spreader(int(node_uid), states=data, window_hours=24)
    except Exception:
        n_is_super = False
    if n_is_super:
        n_flags += WARP_SPREADER_ICON
    if nraw.get("warp_corrupted"):
        n_flags += WARP_CORRUPTED_ICON
    n_flag_str = f" {n_flags}" if n_flags else ""
    try:
        styled = _b("_format_member_styled")(guild, str(node_uid), include_chapter=True) \
            if _b("_format_member_styled") else None
    except Exception:
        styled = None
    if not styled:
        try:
            nm = guild.get_member(int(node_uid))
            styled = nm.display_name if nm else f"<@{node_uid}>"
        except Exception:
            styled = f"<@{node_uid}>"
    return f"{n_icon} {styled} · {npts}c{n_flag_str}"


def _warp_render_subtree(
    guild: "discord.Guild",
    data: dict,
    node_uid: str,
    depth: int,
    visited: set,
    lines_out: List[str],
    max_depth: int = 2,
) -> None:
    """Recursively render downstream contagion tree with indent-only style.

    Mirrors ``/armor_status`` discipline (no ├─└─│ chrome). ``visited`` is
    mutated to track all reached uids; ``lines_out`` collects rendered lines.
    """
    if depth >= max_depth:
        return
    children = _compute_outgoing_infections(int(node_uid), states=data, window_hours=24)
    children = [c for c in children if c not in visited]
    for child in children:
        visited.add(child)
        indent = "   " * (depth + 1)
        lines_out.append(f"{indent}{_warp_node_label(guild, data, child)}")
        _warp_render_subtree(guild, data, child, depth + 1, visited, lines_out, max_depth)


# ---------------------------------------------------------------------------
# Persistence — warp_exposure.json
# ---------------------------------------------------------------------------

def _default_exposure_state() -> dict:
    return {
        "points": 0,
        # Brother fields
        "exposure_tier": None,
        "last_detection_alert_tier": None,
        "spread_history": [],  # list of {"source_id": str, "ts": iso}
        "immunity_until": None,
        "last_warding_timestamp": None,
        # Warp corruption (mirrors armor spirit_fractured) — set when a brother
        # accumulates ``warp_corruption_threshold`` AAR submissions at restricted.
        "restricted_aar_count": 0,
        "warp_corrupted": False,
        # Librarian fields
        "is_librarian": False,
        "librarian_tier": None,
        "last_decay_check": None,
    }


def _load_warp_exposure() -> dict:
    try:
        if not os.path.exists(WARP_EXPOSURE_PATH):
            return {}
        with open(WARP_EXPOSURE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_warp_exposure(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(WARP_EXPOSURE_PATH):
            try:
                shutil.copy2(WARP_EXPOSURE_PATH, WARP_EXPOSURE_PATH + ".bak")
            except Exception:
                pass
        with open(WARP_EXPOSURE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tier resolution
# ---------------------------------------------------------------------------

def _brother_tier_for_points(points: int) -> Optional[str]:
    if points <= 0:
        return None
    bands = _get_brother_tier_bands()
    for tier in WARP_EXPOSURE_TIERS:
        if tier not in bands:
            continue
        lo, hi = bands[tier]
        if hi is None:
            if points >= lo:
                return tier
        elif lo <= points <= hi:
            return tier
    return None


def _librarian_tier_for_points(points: int) -> Optional[str]:
    if points <= 0:
        return None
    bands = _get_librarian_tier_bands()
    for tier in WARP_LIBRARIAN_TIERS:
        if tier not in bands:
            continue
        lo, hi = bands[tier]
        if hi is None:
            if points >= lo:
                return tier
        elif lo <= points <= hi:
            return tier
    return None


def _is_member_librarian(member: discord.Member) -> Tuple[bool, bool]:
    """Returns (is_any_librarian, is_void_warden)."""
    role_names = {r.name for r in getattr(member, "roles", [])}
    is_void = VOID_WARDEN_ROLE_NAME in role_names
    is_lib = is_void or (LIBRARIAN_ROLE_NAME in role_names)
    return is_lib, is_void


def _apply_decay(state: dict) -> dict:
    """Apply Librarian decay lazily based on last_decay_check timestamp.

    Brothers do not decay. Librarians lose 1 point per ``librarian_decay_hours``.
    """
    if not state.get("is_librarian"):
        return state
    points = int(state.get("points", 0) or 0)
    if points <= 0:
        state["last_decay_check"] = datetime.utcnow().isoformat()
        state["librarian_tier"] = None
        return state
    last = state.get("last_decay_check")
    now = datetime.utcnow()
    if not last:
        state["last_decay_check"] = now.isoformat()
        return state
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        state["last_decay_check"] = now.isoformat()
        return state
    decay_hours = _cfg_float("librarian_decay_hours", WARP_LIBRARIAN_DECAY_HOURS)
    elapsed_hours = (now - last_dt).total_seconds() / 3600.0
    decay_steps = int(elapsed_hours // decay_hours) if decay_hours > 0 else 0
    if decay_steps <= 0:
        return state
    new_points = max(0, points - decay_steps)
    consumed = timedelta(hours=decay_steps * decay_hours)
    state["points"] = new_points
    state["last_decay_check"] = (last_dt + consumed).isoformat()
    state["librarian_tier"] = _librarian_tier_for_points(new_points)
    return state


async def _get_warp_exposure_state(user_id: int) -> dict:
    """Return the warp exposure state for a user, applying lazy decay if Librarian."""
    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
            state = dict(data.get(str(user_id), _default_exposure_state()))
            # Backfill defaults for older records
            base = _default_exposure_state()
            for k, v in base.items():
                state.setdefault(k, v)
            state = _apply_decay(state)
            # Recompute tier each read for safety
            if state.get("is_librarian"):
                state["librarian_tier"] = _librarian_tier_for_points(int(state.get("points", 0) or 0))
            else:
                state["exposure_tier"] = _brother_tier_for_points(int(state.get("points", 0) or 0))
            # Persist any changes from decay
            data[str(user_id)] = state
            _save_warp_exposure(data)
            return state
    except Exception:
        return _default_exposure_state()


async def _set_warp_exposure_state(user_id: int, state: dict):
    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
            data[str(user_id)] = state
            _save_warp_exposure(data)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Compatibility shims used by roster_ops/tally_deeds
# ---------------------------------------------------------------------------

async def _get_warp_sanction_status(points: int, user_id: int) -> str:
    """Return a Warp Sanction key for the given exposure points."""
    return _warp_sanction_key_for_points(int(points or 0))


# Backwards-compat alias (roster_ops references "points_since_warding")
def _state_to_legacy_view(state: dict) -> dict:
    out = dict(state)
    out["points_since_warding"] = out.get("points", 0)
    return out


# Patch the read function so consumers reading "points_since_warding" succeed.
_orig_get_state = _get_warp_exposure_state


async def _get_warp_exposure_state(user_id: int) -> dict:  # noqa: F811
    state = await _orig_get_state(user_id)
    state = dict(state)
    state["points_since_warding"] = state.get("points", 0)
    return state


# ---------------------------------------------------------------------------
# Penalty rolls
# ---------------------------------------------------------------------------

def _roll_warp_penalty(tier: Optional[str]) -> int:
    """Roll a probabilistic AAR penalty by exposure tier (config-driven)."""
    table = _get_penalty_probabilities()
    probs = table.get(tier, {0: 1.0})
    roll = random.random()
    cumulative = 0.0
    for penalty, prob in sorted(probs.items()):
        cumulative += prob
        if roll < cumulative:
            return int(penalty)
    return 0


def _get_warp_tier_risk_display(tier: Optional[str]) -> str:
    probs = WARP_PENALTY_PROBABILITIES.get(tier, {0: 1.0})
    penalty_chance = sum(p for k, p in probs.items() if k > 0)
    if penalty_chance <= 0:
        return "No risk"
    nonzero = [k for k, p in probs.items() if k > 0 and p > 0]
    if not nonzero:
        return "No risk"
    pct = int(penalty_chance * 100)
    lo, hi = min(nonzero), max(nonzero)
    if lo == hi:
        return f"{pct}% (-{lo} AAR)"
    return f"{pct}% (-{lo} to -{hi} AAR)"


# ---------------------------------------------------------------------------
# Override / kill switch
# ---------------------------------------------------------------------------

def _load_override() -> dict:
    try:
        if not os.path.exists(LIBRARIUM_OVERRIDE_PATH):
            return {"enabled": True}
        with open(LIBRARIUM_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {"enabled": True}
    except Exception:
        return {"enabled": True}


def _save_override(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LIBRARIUM_OVERRIDE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _is_librarius_enabled() -> bool:
    try:
        async with _g.LIBRARIUM_OVERRIDE_LOCK:
            return bool(_load_override().get("enabled", True))
    except Exception:
        return True


# ---------------------------------------------------------------------------
# AAR hook — direct gain, spread, immunity, alerts
# ---------------------------------------------------------------------------

def _bl_gain_for_record(record: dict) -> int:
    """Compute direct exposure gain for a Black Laurels AAR.

    1 = absolute BL, 2 = hard-stratagem BL, 3 = omega BL, 0 otherwise.
    Values come from ``config.warp_corruption.bl_exposure_gain``.
    """
    if not record:
        return 0
    bl = bool(record.get("black_laurels_in_mission")) or bool(record.get("black_laurels_in_difficulty"))
    if not bl:
        return 0
    gains = _get_bl_exposure_gain()
    diff = (record.get("difficulty_class") or "").lower()
    if diff == "omega_ops":
        return int(gains.get("omega_ops", 3))
    if diff == "hard_stratagem":
        return int(gains.get("hard_stratagem", 2))
    return int(gains.get("absolute", 1))


def _prune_spread_history(history: List[dict], window_hours: int = 24) -> List[dict]:
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    pruned = []
    for entry in history or []:
        try:
            ts = datetime.fromisoformat(entry.get("ts"))
            if ts >= cutoff:
                pruned.append(entry)
        except Exception:
            continue
    return pruned


def _is_immune(state: dict) -> bool:
    until = state.get("immunity_until")
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until)
        return datetime.utcnow() < until_dt
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sanction role assignment (mirrors armor _apply_damage_tier / _clear_armor_damage)
# ---------------------------------------------------------------------------

# Sanction key severity ordering (higher = more severe).
_SANCTION_ORDER = {
    "sanctioned": 0,
    "screening_due": 1,
    "under_review": 2,
    "restricted": 3,
    "quarantine": 4,
    "lockdown": 5,
}


async def _apply_sanction_role(
    member: discord.Member,
    guild: discord.Guild,
    new_sanction_key: str,
) -> Optional[str]:
    """Set ``member``'s sanction role to match ``new_sanction_key``.

    Removes any other configured sanction roles. Returns the applied key (or
    None if no roles configured). The cleared baseline is ``"sanctioned"``
    which is treated as 'no role assigned'.
    """
    role_ids = _get_sanction_role_ids()
    if not role_ids:
        return None

    target_role_id = role_ids.get(new_sanction_key) if new_sanction_key != "sanctioned" else None

    try:
        # Remove any sanction role that isn't the target
        for key, rid in role_ids.items():
            if target_role_id and int(rid) == int(target_role_id):
                continue
            try:
                role = guild.get_role(int(rid))
                if role and role in member.roles:
                    await member.remove_roles(role, reason="Warp sanction: tier change")
            except Exception:
                pass

        # Add the target role if defined
        if target_role_id:
            try:
                role = guild.get_role(int(target_role_id))
                if role and role not in member.roles:
                    await member.add_roles(role, reason=f"Warp sanction: {new_sanction_key}")
            except Exception:
                pass
        return new_sanction_key
    except Exception:
        return None


async def _clear_sanction_roles(member: discord.Member, guild: discord.Guild):
    """Remove all configured sanction roles from a member."""
    role_ids = _get_sanction_role_ids()
    for key, rid in role_ids.items():
        try:
            role = guild.get_role(int(rid))
            if role and role in member.roles:
                await member.remove_roles(role, reason="Warp sanction: cleansed")
        except Exception:
            pass


async def _sync_sanction_role_for_member(
    member: discord.Member,
    guild: discord.Guild,
    new_points: int,
    is_librarian: bool,
) -> None:
    """Ensure the member's sanction role matches their current exposure points."""
    if is_librarian:
        # Librarians don't get sanction roles (their burden is private)
        await _clear_sanction_roles(member, guild)
        return
    if new_points <= 0:
        await _clear_sanction_roles(member, guild)
        return
    new_key = _warp_sanction_key_for_points(new_points)
    await _apply_sanction_role(member, guild, new_key)


# ---------------------------------------------------------------------------
# Librarium Chronicle persistent message
# ---------------------------------------------------------------------------

def _load_librarium_chronicle() -> dict:
    try:
        if not os.path.exists(LIBRARIUM_CHRONICLE_PATH):
            return {"dashboard_message_id": None, "cleanse_history": [], "librarian_stats": {}}
        with open(LIBRARIUM_CHRONICLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    data.setdefault("dashboard_message_id", None)
    data.setdefault("cleanse_history", [])
    data.setdefault("librarian_stats", {})
    return data


def _save_librarium_chronicle(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(LIBRARIUM_CHRONICLE_PATH):
            try:
                shutil.copy2(LIBRARIUM_CHRONICLE_PATH, LIBRARIUM_CHRONICLE_PATH + ".bak")
            except Exception:
                pass
        with open(LIBRARIUM_CHRONICLE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_librarium_dashboard_message_id() -> Optional[int]:
    async with _g.LIBRARIUM_CHRONICLE_LOCK:
        data = _load_librarium_chronicle()
        msg_id = data.get("dashboard_message_id")
        try:
            return int(msg_id) if msg_id else None
        except Exception:
            return None


async def _set_librarium_dashboard_message_id(message_id: Optional[int]):
    async with _g.LIBRARIUM_CHRONICLE_LOCK:
        data = _load_librarium_chronicle()
        data["dashboard_message_id"] = int(message_id) if message_id else None
        _save_librarium_chronicle(data)


async def _check_warp_scry_cooldown(caller_id: int) -> Tuple[bool, Optional[timedelta], Optional[str]]:
    """Return ``(allowed, remaining, reason)`` for a librarian's next /warp_scry.

    Two gates, in order:
      1. Daily cap — ``warp_scry_daily_limit`` uses per caller in last 24h
         (default 2). Reason ``"daily"`` when blocked.
      2. Per-cast cooldown — ``warp_scry_cooldown_minutes`` (default 30)
         between scryings for the same caller. Reason ``"cooldown"`` when
         blocked.

    Persisted in the librarium chronicle:
      - ``scry_log[caller_id] = iso_ts`` (last cast)
      - ``scry_history[]`` — bounded list with ``caller_id`` + ``ts``
    """
    daily_limit = _cfg_int("warp_scry_daily_limit", 2)
    cooldown_min = _cfg_int("warp_scry_cooldown_minutes", 30)
    now = datetime.utcnow()
    async with _g.LIBRARIUM_CHRONICLE_LOCK:
        data = _load_librarium_chronicle()
        log = data.get("scry_log") or {}
        history = data.get("scry_history") or []
    # Daily cap check
    if daily_limit > 0:
        window_start = now - timedelta(hours=24)
        used_in_window: List[datetime] = []
        for entry in history:
            if str(entry.get("caller_id")) != str(caller_id):
                continue
            try:
                ts = datetime.fromisoformat(entry.get("ts", ""))
            except Exception:
                continue
            if ts >= window_start:
                used_in_window.append(ts)
        if len(used_in_window) >= daily_limit:
            # Reset when the oldest in-window entry rolls off.
            oldest = min(used_in_window)
            remaining = (oldest + timedelta(hours=24)) - now
            return False, remaining, "daily"
    # Per-cast cooldown check
    if cooldown_min > 0:
        last_iso = log.get(str(caller_id))
        if last_iso:
            try:
                last = datetime.fromisoformat(last_iso)
            except Exception:
                last = None
            if last is not None:
                elapsed = now - last
                window = timedelta(minutes=cooldown_min)
                if elapsed < window:
                    return False, window - elapsed, "cooldown"
    return True, None, None


async def _record_warp_scry(caller_id: int, target_id: int) -> None:
    """Stamp the caller's last-scry timestamp and append to a bounded log."""
    async with _g.LIBRARIUM_CHRONICLE_LOCK:
        data = _load_librarium_chronicle()
        log = data.setdefault("scry_log", {})
        log[str(caller_id)] = datetime.utcnow().isoformat()
        history = data.setdefault("scry_history", [])
        history.append({
            "ts": datetime.utcnow().isoformat(),
            "caller_id": str(caller_id),
            "target_id": str(target_id),
        })
        if len(history) > 200:
            data["scry_history"] = history[-200:]
        _save_librarium_chronicle(data)


async def _record_cleanse_in_chronicle(
    bearer_id: int,
    librarian_id: int,
    outcome_key: str,
    removed: int,
    transfer: int,
):
    async with _g.LIBRARIUM_CHRONICLE_LOCK:
        data = _load_librarium_chronicle()
        history = data.setdefault("cleanse_history", [])
        history.append({
            "ts": datetime.utcnow().isoformat(),
            "bearer_id": str(bearer_id),
            "librarian_id": str(librarian_id),
            "outcome": outcome_key,
            "removed": int(removed),
            "transfer": int(transfer),
        })
        if len(history) > 500:
            data["cleanse_history"] = history[-500:]
        stats = data.setdefault("librarian_stats", {})
        key = str(librarian_id)
        rec = stats.setdefault(key, {
            "total_cleanses": 0,
            "successes": 0,
            "removed_total": 0,
            "transfer_total": 0,
        })
        rec["total_cleanses"] = int(rec.get("total_cleanses", 0)) + 1
        if removed > 0:
            rec["successes"] = int(rec.get("successes", 0)) + 1
        rec["removed_total"] = int(rec.get("removed_total", 0)) + int(removed)
        rec["transfer_total"] = int(rec.get("transfer_total", 0)) + int(transfer)
        _save_librarium_chronicle(data)


async def _build_librarium_chronicle_embed(guild: Optional[discord.Guild]) -> discord.Embed:
    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
    except Exception:
        data = {}

    bucket_counts: Dict[str, int] = {k: 0 for k in WARP_SANCTION_STATUS.keys()}
    librarian_tier_counts: Dict[Optional[str], int] = {k: 0 for k in (None, *WARP_LIBRARIAN_TIERS)}
    corrupted_count = 0
    super_spreader_count = 0
    brothers_needing_cleanse = 0  # any sanctioned brother (>0 pts) or warp_corrupted
    librarian_user_ids: List[int] = []
    for uid, raw in data.items():
        pts = int((raw or {}).get("points", 0) or 0)
        if (raw or {}).get("is_librarian"):
            lt = _librarian_tier_for_points(pts)
            librarian_tier_counts[lt] = librarian_tier_counts.get(lt, 0) + 1
            try:
                librarian_user_ids.append(int(uid))
            except Exception:
                pass
        else:
            key = _warp_sanction_key_for_points(pts)
            bucket_counts[key] = bucket_counts.get(key, 0) + 1
            if (raw or {}).get("warp_corrupted"):
                corrupted_count += 1
            if key != "sanctioned" or (raw or {}).get("warp_corrupted"):
                brothers_needing_cleanse += 1
            try:
                is_super, _ = _is_super_spreader(int(uid), states=data, window_hours=24)
                if is_super:
                    super_spreader_count += 1
            except Exception:
                pass

    # Warp Pressure = demand (brothers needing cleanse) / supply (active librarian charges).
    # Mirrors forge_pressure semantics. Stable+ librarians only count as supply
    # (overloaded/abyssal cannot cleanse others, per the cleanse guard).
    total_librarian_charges = 0
    for lib_id in librarian_user_ids:
        try:
            lib_state = data.get(str(lib_id)) or {}
            lib_tier = _librarian_tier_for_points(int(lib_state.get("points", 0) or 0))
            if lib_tier in ("overloaded", "abyssal"):
                continue
            total_librarian_charges += await _get_librarian_available_charges(lib_id)
        except Exception:
            continue
    if total_librarian_charges > 0:
        warp_pressure = brothers_needing_cleanse / total_librarian_charges
    else:
        warp_pressure = float("inf") if brothers_needing_cleanse > 0 else 0.0


    ambient = random.choice(LIBRARIUM_AMBIENT_MESSAGES)

    # Load chronicle data once (used for Recent Rites, Custodians, Breach Memorial).
    try:
        async with _g.LIBRARIUM_CHRONICLE_LOCK:
            chron = _load_librarium_chronicle()
    except Exception:
        chron = {}
    cleanse_history = chron.get("cleanse_history") or []
    librarian_stats: Dict[str, Dict[str, int]] = chron.get("librarian_stats") or {}

    # Sanctioned % — fortress-wide clean fraction (mirrors forge nominal %).
    # Counts only active participants (ranked, non-Reserves, non-Interred).
    clean_pct = 100.0
    if guild is not None:
        is_active_fn = _b("_is_active_participant")
        total_brothers = 0
        for member in guild.members:
            if is_active_fn:
                if not is_active_fn(member):
                    continue
            else:
                # Fallback if helper unavailable
                if member.bot:
                    continue
                if not any(r.name in RANK_HONORIFICS for r in member.roles):
                    continue
                role_ids = {r.id for r in member.roles}
                role_names = {(r.name or "").lower() for r in member.roles}
                if RESERVES_ROLE_ID in role_ids or "reserves" in role_names:
                    continue
            total_brothers += 1
        if total_brothers > 0:
            clean_pct = max(0.0, (total_brothers - brothers_needing_cleanse) / total_brothers * 100)

    embed = discord.Embed(
        title="᛭⋅ LIBRARIUM CHRONICLE ⋅᛭",
        color=0x9B59B6,
    )

    # ─── Warp Telemetry (description, top prominence — mirrors forge Armory Telemetry)
    sanctioned_icon = "🟢" if clean_pct >= 90 else ("🟡" if clean_pct >= 70 else "🔴")
    if warp_pressure == float("inf"):
        pressure_icon = "⚠️"
        pressure_str = "∞"
    elif warp_pressure < 1.0:
        pressure_icon = "🟢"
        pressure_str = f"{warp_pressure:.1f}x"
    elif warp_pressure < 2.0:
        pressure_icon = "🟡"
        pressure_str = f"{warp_pressure:.1f}x"
    else:
        pressure_icon = "🔴"
        pressure_str = f"{warp_pressure:.1f}x"
    if total_librarian_charges == 0:
        charges_icon = "🔴"
    elif total_librarian_charges < max(1, brothers_needing_cleanse):
        charges_icon = "🟡"
    else:
        charges_icon = "🟢"
    embed.description = (
        f"*{ambient}*\n\n"
        f"**▸ Warp Telemetry**\n"
        f"{sanctioned_icon} **{clean_pct:.0f}%** Sanctioned  "
        f"{pressure_icon} **{pressure_str}** Pressure  "
        f"{charges_icon} **{total_librarian_charges}** Charges"
    )

    # ─── Watchlist (sanctioned brothers — mirrors forge watchlist)
    watchlist_entries = []  # (severity_idx, pts, uid, raw)
    severity_order = {
        "catastrophic": 0, "breached": 1, "volatile": 2, "exposed": 3, "tainted": 4, None: 5,
    }
    if guild is not None:
        for uid, raw in data.items():
            if (raw or {}).get("is_librarian"):
                continue
            pts = int((raw or {}).get("points", 0) or 0)
            if pts <= 0 and not (raw or {}).get("warp_corrupted"):
                continue
            try:
                member = guild.get_member(int(uid))
            except Exception:
                member = None
            if member is None:
                continue
            tier = _brother_tier_for_points(pts)
            watchlist_entries.append((severity_order.get(tier, 5), -pts, uid, raw, member, tier))
    watchlist_entries.sort(key=lambda r: (r[0], r[1]))
    watch_lines = []
    for _, _, uid, raw, member, tier in watchlist_entries[:5]:
        pts = int((raw or {}).get("points", 0) or 0)
        icon = WARP_BROTHER_TIER_ICON.get(tier, "🟢")
        flags = ""
        try:
            is_super, _t = _is_super_spreader(int(uid), states=data, window_hours=24)
        except Exception:
            is_super = False
        if is_super:
            flags += WARP_SPREADER_ICON
        if (raw or {}).get("warp_corrupted"):
            flags += WARP_CORRUPTED_ICON
        flag_str = f" {flags}" if flags else ""
        try:
            name = _b("_format_member_styled")(guild, str(uid), include_chapter=True) \
                if _b("_format_member_styled") else member.display_name
        except Exception:
            name = member.display_name
        watch_lines.append(f"{icon} {name} · {pts}c{flag_str}")
    if not watch_lines:
        watch_lines.append("*The wards hold. No sanctioned brothers.*")
    embed.add_field(name="▸ Watchlist", value="\n".join(watch_lines), inline=False)

    # ─── Recent Rites (last 5 cleanses — mirrors forge Recent Rites)
    outcome_display = {
        "full": ("✅", "Full Cleanse"),
        "partial": ("🟡", "Partial"),
        "backlash": ("⚡", "Backlash"),
    }
    recent_lines = []
    for entry in reversed(cleanse_history[-5:]):
        outcome = entry.get("outcome", "?")
        removed = int(entry.get("removed", 0) or 0)
        bearer_id = entry.get("bearer_id")
        librarian_id = entry.get("librarian_id")
        ts_str = entry.get("ts")
        o_icon, o_label = outcome_display.get(outcome, ("•", outcome))
        try:
            lib_name = _b("_format_member_styled")(guild, str(librarian_id), include_chapter=True) \
                if (guild and librarian_id and _b("_format_member_styled")) else f"<@{librarian_id}>"
        except Exception:
            lib_name = f"<@{librarian_id}>"
        try:
            bearer_name = _b("_format_member_styled")(guild, str(bearer_id), include_chapter=True) \
                if (guild and bearer_id and _b("_format_member_styled")) else f"<@{bearer_id}>"
        except Exception:
            bearer_name = f"<@{bearer_id}>"
        try:
            ts = datetime.fromisoformat(ts_str)
            time_ago = _b("_format_time_ago")(ts) if _b("_format_time_ago") else ""
        except Exception:
            time_ago = ""
        time_suffix = f" • {time_ago}" if time_ago else ""
        recent_lines.append(
            f"{o_icon} {lib_name} → {bearer_name} ({o_label} · {removed}c){time_suffix}"
        )
    if not recent_lines:
        recent_lines.append("*No rites recorded.*")
    embed.add_field(name="▸ Recent Rites", value="\n".join(recent_lines), inline=False)

    # ─── Librarian Custodians (mirrors forge Machine Spirits stats)
    custodian_lines = []
    if librarian_stats:
        # Devoted: most cleanses performed.
        devoted = max(
            librarian_stats.items(),
            key=lambda kv: int(kv[1].get("total_cleanses", 0) or 0),
            default=None,
        )
        # Unwavering: highest success rate (min 3 rites to qualify).
        qualified = [
            (lid, s) for lid, s in librarian_stats.items()
            if int(s.get("total_cleanses", 0) or 0) >= 3
        ]
        unwavering = None
        if qualified:
            unwavering = max(
                qualified,
                key=lambda kv: (
                    int(kv[1].get("successes", 0) or 0)
                    / max(1, int(kv[1].get("total_cleanses", 0) or 0))
                ),
            )
        # Stalwart: most points purged.
        stalwart = max(
            librarian_stats.items(),
            key=lambda kv: int(kv[1].get("removed_total", 0) or 0),
            default=None,
        )

        def _styled(lid: str) -> str:
            try:
                if guild and _b("_format_member_styled"):
                    return _b("_format_member_styled")(guild, str(lid), include_chapter=True)
            except Exception:
                pass
            return f"<@{lid}>"

        if devoted and int(devoted[1].get("total_cleanses", 0) or 0) > 0:
            custodian_lines.append(
                f"Devoted ({devoted[1].get('total_cleanses', 0)} rites): {_styled(devoted[0])}"
            )
        if unwavering:
            total = int(unwavering[1].get("total_cleanses", 0) or 0)
            successes = int(unwavering[1].get("successes", 0) or 0)
            rate = (successes / total * 100) if total else 0
            custodian_lines.append(
                f"Unwavering ({rate:.0f}% over {total}): {_styled(unwavering[0])}"
            )
        if stalwart and int(stalwart[1].get("removed_total", 0) or 0) > 0 and (
            not devoted or stalwart[0] != devoted[0]
        ):
            custodian_lines.append(
                f"Stalwart ({stalwart[1].get('removed_total', 0)}c purged): {_styled(stalwart[0])}"
            )
    if custodian_lines:
        embed.add_field(name="▸ 🧿 Librarian Custodians", value="\n".join(custodian_lines), inline=False)

    # ─── Breach Memorial (recent backlash events last 28d — mirrors forge Spirit Memorial)
    memorial_lines = []
    cutoff = datetime.utcnow() - timedelta(days=28)
    backlashes = []
    for entry in cleanse_history:
        if entry.get("outcome") != "backlash":
            continue
        try:
            ts = datetime.fromisoformat(entry.get("ts", ""))
            if ts >= cutoff:
                backlashes.append((ts, entry))
        except Exception:
            pass
    backlashes.sort(key=lambda x: x[0], reverse=True)
    for ts, entry in backlashes[:3]:
        bearer_id = entry.get("bearer_id")
        transfer = int(entry.get("transfer", 0) or 0)
        age_days = max(0, (datetime.utcnow() - ts).days)
        try:
            bearer_name = _b("_format_member_styled")(guild, str(bearer_id), include_chapter=True) \
                if (guild and bearer_id and _b("_format_member_styled")) else f"<@{bearer_id}>"
        except Exception:
            bearer_name = f"<@{bearer_id}>"
        suffix = f" · {transfer}c bled-back" if transfer else ""
        memorial_lines.append(f"⚡ ({age_days}d) {bearer_name}{suffix}")
    if memorial_lines:
        embed.add_field(name="▸ Breach Memorial", value="\n".join(memorial_lines), inline=False)

    # ─── Sanction Roster + Librarian Burden (inline pair — mirrors forge Reserves+Artificers)
    sanction_bits = []
    for key in ("screening_due", "under_review", "restricted"):
        count = bucket_counts.get(key, 0)
        if count:
            sanction_bits.append(f"{WARP_SANCTION_STATUS_ICON[key]} {count}")
    flag_bits = []
    if corrupted_count:
        flag_bits.append(f"{WARP_CORRUPTED_ICON} {corrupted_count}")
    if super_spreader_count:
        flag_bits.append(f"{WARP_SPREADER_ICON} {super_spreader_count}")
    sanction_value_lines = []
    if sanction_bits:
        sanction_value_lines.append(" · ".join(sanction_bits))
    if flag_bits:
        sanction_value_lines.append(" · ".join(flag_bits))
    embed.add_field(
        name="▸ Sanction Roster",
        value="\n".join(sanction_value_lines) if sanction_value_lines else "All clear.",
        inline=True,
    )
    lib_bits = []
    for tier in (None, *WARP_LIBRARIAN_TIERS):
        n = librarian_tier_counts.get(tier, 0)
        if n:
            lib_bits.append(f"{WARP_LIBRARIAN_TIER_ICON.get(tier, '🟢')} {n}")
    embed.add_field(
        name="▸ Librarian Burden",
        value=" · ".join(lib_bits) if lib_bits else "Unburdened.",
        inline=True,
    )

    # ─── Key (bottom — mirrors forge legend)
    embed.add_field(
        name="▸ Key",
        value=(
            "🟡 Screening 🟠 Review 🔴 Restricted | "
            f"{WARP_CORRUPTED_ICON} Corrupted {WARP_SPREADER_ICON} Spreader | "
            "Librarian: 🟡 Stable 🟠 Resonant 🔴 Surging 💀 Overloaded ⚫ Abyssal | "
            "c = cycles since cleansing"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Last updated • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    return embed


async def _repost_librarium_chronicle_at_bottom(guild: Optional[discord.Guild]):
    """Delete the prior chronicle message (if any) and post a fresh one at the bottom.

    Raises on real failures (channel not found, send fails) so callers can
    surface the error to the user. Deletion of the old message is best-effort.
    """
    if guild is None:
        raise RuntimeError("Guild context required.")
    channel_id = _get_librarium_watch_channel_id()
    if not channel_id:
        raise RuntimeError("No Librarium watch channel configured.")
    try:
        channel = guild.get_channel(int(channel_id))
    except Exception:
        channel = None
    if channel is None:
        try:
            channel = await _g.bot.fetch_channel(int(channel_id))
        except Exception as e:
            raise RuntimeError(
                f"Librarium watch channel {channel_id} not accessible: {e}"
            )

    embed = await _build_librarium_chronicle_embed(guild)

    # Delete previous chronicle message if we have one (best-effort)
    prev_id = await _get_librarium_dashboard_message_id()
    if prev_id:
        try:
            old = await channel.fetch_message(int(prev_id))
            await old.delete()
        except Exception:
            pass

    sent = await channel.send(embed=embed)
    await _set_librarium_dashboard_message_id(sent.id)


async def _apply_warp_exposure_for_aar(record: dict, guild: Optional[discord.Guild]):
    """Apply Black Laurels direct gain + contagion spread for an AAR record.

    Penalty rolling is performed by the caller (aar_ops) prior to record save
    so that AAR points reflect the loss; this hook handles state updates and
    spread bookkeeping after save.
    """
    if not await _is_librarius_enabled():
        return
    if not record:
        return
    brother_ids = list(record.get("brother_ids") or [])
    if not brother_ids:
        return

    bl_gain = _bl_gain_for_record(record)

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()

            # Hydrate states for all squad members
            states: Dict[str, dict] = {}
            is_active_fn = _b("_is_active_participant")
            for bid in brother_ids:
                # Skip non-participants (no rank, Reserves, Interred) — symmetric
                # with armor's _process_armor_integrity_for_aar gate.
                if guild is not None and is_active_fn:
                    try:
                        m = guild.get_member(int(bid))
                    except Exception:
                        m = None
                    if m is None or not is_active_fn(m):
                        continue
                base = _default_exposure_state()
                state = dict(data.get(str(bid), base))
                for k, v in base.items():
                    state.setdefault(k, v)
                # Tag librarian status from current guild membership
                if guild is not None:
                    try:
                        m = guild.get_member(int(bid))
                        if m is not None:
                            is_lib, _ = _is_member_librarian(m)
                            state["is_librarian"] = bool(is_lib)
                    except Exception:
                        pass
                # Apply lazy decay before mutating
                state = _apply_decay(state)
                states[str(bid)] = state

            now_iso = datetime.utcnow().isoformat()

            # 1) Direct BL gain — applies to all squadmates on a BL mission.
            if bl_gain > 0:
                for bid, state in states.items():
                    state["points"] = int(state.get("points", 0) or 0) + bl_gain

            # 2) Contagion spread — for every infected squadmate, roll spread
            # against every other squadmate (subject to immunity + daily cap).
            sources: List[Tuple[str, str]] = []  # (source_id, source_tier)
            for bid, state in states.items():
                pts = int(state.get("points", 0) or 0)
                tier = _brother_tier_for_points(pts) if not state.get("is_librarian") else None
                # Only brother-tier infections spread; Librarians have shielded minds
                if tier:
                    sources.append((bid, tier))

            if sources:
                spread_chances = _get_spread_chances()
                spread_cap = _cfg_int("spread_daily_unique_source_cap", WARP_SPREAD_DAILY_UNIQUE_SOURCE_CAP)
                spread_amt = _cfg_int("spread_amount", WARP_SPREAD_AMOUNT)
                for tgt_id, tgt_state in states.items():
                    if _is_immune(tgt_state):
                        continue
                    history = _prune_spread_history(tgt_state.get("spread_history") or [])
                    unique_today = {h.get("source_id") for h in history}
                    for src_id, src_tier in sources:
                        if src_id == tgt_id:
                            continue
                        if len(unique_today) >= spread_cap and src_id not in unique_today:
                            continue
                        if src_id in unique_today:
                            continue
                        chance = spread_chances.get(src_tier, 0.0)
                        if random.random() < chance:
                            tgt_state["points"] = int(tgt_state.get("points", 0) or 0) + spread_amt
                            history.append({"source_id": str(src_id), "ts": now_iso})
                            unique_today.add(src_id)
                    tgt_state["spread_history"] = history

            # 3) Recompute tiers + corruption flag, then persist
            corruption_threshold = _cfg_int(
                "warp_corruption_threshold", DEFAULT_WARP_CORRUPTION_THRESHOLD
            )
            for bid, state in states.items():
                pts = int(state.get("points", 0) or 0)
                if state.get("is_librarian"):
                    state["librarian_tier"] = _librarian_tier_for_points(pts)
                    state["exposure_tier"] = None
                    # Librarians don't accumulate brother-corruption; clear flags
                    state["restricted_aar_count"] = 0
                    state["warp_corrupted"] = False
                else:
                    state["exposure_tier"] = _brother_tier_for_points(pts)
                    state["librarian_tier"] = None
                    sanction = _warp_sanction_key_for_points(pts)
                    if sanction == "restricted":
                        state["restricted_aar_count"] = int(
                            state.get("restricted_aar_count", 0) or 0
                        ) + 1
                        if (
                            not state.get("warp_corrupted")
                            and corruption_threshold > 0
                            and state["restricted_aar_count"] >= corruption_threshold
                        ):
                            state["warp_corrupted"] = True
                data[str(bid)] = state

            _save_warp_exposure(data)

        # 4) Sync sanction roles outside the lock
        if guild is not None:
            for bid, state in states.items():
                try:
                    member = guild.get_member(int(bid))
                except Exception:
                    member = None
                if member is None:
                    continue
                pts = int(state.get("points", 0) or 0)
                try:
                    await _sync_sanction_role_for_member(
                        member, guild, pts, bool(state.get("is_librarian"))
                    )
                except Exception:
                    pass

        # 5) Repost the persistent Librarium Chronicle
        try:
            await _repost_librarium_chronicle_at_bottom(guild)
        except Exception:
            pass
    except Exception as e:
        try:
            _g.logger.debug(f"_apply_warp_exposure_for_aar failed: {e}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Warding pool (Librarian charges)
# ---------------------------------------------------------------------------

def _load_warding_pool() -> dict:
    try:
        if not os.path.exists(WARDING_POOL_PATH):
            return {}
        with open(WARDING_POOL_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_warding_pool(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(WARDING_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _filter_active_warding_timestamps(timestamps: List[str]) -> List[str]:
    now = datetime.utcnow()
    regen_seconds = _cfg_float("warding_pool_regen_hours", WARDING_POOL_REGEN_HOURS) * 3600
    active = []
    for ts in timestamps or []:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
            if (now - dt).total_seconds() < regen_seconds:
                active.append(ts)
        except Exception:
            pass
    return active


async def _get_librarian_pool_state(user_id: int) -> dict:
    try:
        async with _g.WARDING_POOL_LOCK:
            data = _load_warding_pool()
            return dict(data.get(str(user_id), {"warding_timestamps": []}))
    except Exception:
        return {"warding_timestamps": []}


async def _set_librarian_pool_state(user_id: int, state: dict, display_name: Optional[str] = None):
    try:
        async with _g.WARDING_POOL_LOCK:
            data = _load_warding_pool()
            if display_name:
                state["display_name"] = display_name
            data[str(user_id)] = state
            _save_warding_pool(data)
    except Exception:
        pass


async def _get_librarian_available_charges(user_id: int) -> int:
    state = await _get_librarian_pool_state(user_id)
    timestamps = _filter_active_warding_timestamps(state.get("warding_timestamps") or [])
    pool_max = _cfg_int("warding_pool_max", WARDING_POOL_MAX)
    timestamps = timestamps[-pool_max:]
    return max(0, pool_max - len(timestamps))


async def _consume_librarian_charges(user_id: int, count: int, display_name: Optional[str] = None) -> bool:
    if count <= 0:
        return True
    try:
        async with _g.WARDING_POOL_LOCK:
            data = _load_warding_pool()
            state = dict(data.get(str(user_id), {"warding_timestamps": []}))
            timestamps = _filter_active_warding_timestamps(state.get("warding_timestamps") or [])
            pool_max = _cfg_int("warding_pool_max", WARDING_POOL_MAX)
            available = max(0, pool_max - len(timestamps))
            if available < count:
                return False
            now_iso = datetime.utcnow().isoformat()
            for _ in range(count):
                timestamps.append(now_iso)
            state["warding_timestamps"] = timestamps[-pool_max:]
            if display_name:
                state["display_name"] = display_name
            data[str(user_id)] = state
            _save_warding_pool(data)
            return True
    except Exception:
        return False


async def _next_warding_regen(user_id: int) -> Optional[timedelta]:
    state = await _get_librarian_pool_state(user_id)
    timestamps = _filter_active_warding_timestamps(state.get("warding_timestamps") or [])
    pool_max = _cfg_int("warding_pool_max", WARDING_POOL_MAX)
    if len(timestamps) < pool_max:
        return None
    now = datetime.utcnow()
    regen_seconds = _cfg_float("warding_pool_regen_hours", WARDING_POOL_REGEN_HOURS) * 3600
    oldest = None
    for ts in timestamps:
        try:
            dt = datetime.fromisoformat(ts)
            if oldest is None or dt < oldest:
                oldest = dt
        except Exception:
            pass
    if oldest is None:
        return None
    return timedelta(seconds=regen_seconds) - (now - oldest)


# ---------------------------------------------------------------------------
# Recipient cooldowns (mirrors blessing recipient cooldown contract)
# ---------------------------------------------------------------------------

async def _check_warding_recipient_cooldown(recipient_id: int) -> Tuple[bool, Optional[timedelta], int, Optional[str]]:
    """Return (can_receive, cooldown_remaining, wards_used_today, block_reason)."""
    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
            state = data.get(str(recipient_id), _default_exposure_state())
            history = state.get("ward_recipient_history") or []
    except Exception:
        history = []
    now = datetime.utcnow()
    cooldown_window = _cfg_int("warding_recipient_cooldown_hours", WARDING_RECIPIENT_COOLDOWN_HOURS)
    per_ward_cooldown = _cfg_float(
        "warding_recipient_per_warding_cooldown_hours",
        WARDING_RECIPIENT_PER_WARDING_COOLDOWN_HOURS,
    )
    daily_cap = _cfg_int("warding_recipient_max_per_day", WARDING_RECIPIENT_MAX_PER_DAY)
    window_start = now - timedelta(hours=cooldown_window)
    recent: List[datetime] = []
    for ts in history:
        try:
            dt = datetime.fromisoformat(ts)
            if dt >= window_start:
                recent.append(dt)
        except Exception:
            pass
    recent.sort(reverse=True)
    used = len(recent)
    if recent:
        since_last = (now - recent[0]).total_seconds() / 3600.0
        if since_last < per_ward_cooldown:
            remaining = timedelta(hours=per_ward_cooldown) - (now - recent[0])
            return False, remaining, used, "per_ward"
    if used >= daily_cap:
        remaining = timedelta(hours=cooldown_window) - (now - recent[-1])
        return False, remaining, used, "daily_cap"
    return True, None, used, None


async def _record_warding_for_recipient(recipient_id: int):
    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
            state = dict(data.get(str(recipient_id), _default_exposure_state()))
            for k, v in _default_exposure_state().items():
                state.setdefault(k, v)
            history = state.get("ward_recipient_history") or []
            history.append(datetime.utcnow().isoformat())
            cooldown_window = _cfg_int("warding_recipient_cooldown_hours", WARDING_RECIPIENT_COOLDOWN_HOURS)
            cutoff = datetime.utcnow() - timedelta(hours=cooldown_window)
            trimmed = []
            for ts in history:
                try:
                    if datetime.fromisoformat(ts) >= cutoff:
                        trimmed.append(ts)
                except Exception:
                    pass
            state["ward_recipient_history"] = trimmed
            data[str(recipient_id)] = state
            _save_warp_exposure(data)
    except Exception:
        pass


def _format_cooldown(td: timedelta) -> str:
    total = max(0, int(td.total_seconds()))
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


# ---------------------------------------------------------------------------
# Cleanse outcome resolution
# ---------------------------------------------------------------------------

def _roll_cleanse_outcome(librarian_tier: Optional[str]) -> Tuple[str, float, int]:
    """Returns (outcome_key, fraction_removed, librarian_extra)."""
    table = WARP_CLEANSE_OUTCOMES.get(librarian_tier)
    if not table:
        # Treat unknown librarian tier as Clear
        table = WARP_CLEANSE_OUTCOMES[None]
    roll = random.random()
    cumulative = 0.0
    for prob, key, frac, extra in table:
        cumulative += prob
        if roll < cumulative:
            return key, float(frac), int(extra)
    # Fallback to last entry
    _, key, frac, extra = table[-1]
    return key, float(frac), int(extra)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _is_librarian_or_void_warden(
    user: discord.Member,
    command_name: str = "warp_cleanse",
) -> Tuple[bool, str]:
    """Config-driven permission gate for Librarian commands.

    Permission is resolved via ``check_command_permission`` against
    ``CONFIG['permissions'][command_name]`` so role lists remain editable
    without code changes. The returned ``role_key`` (``void_warden``,
    ``librarian``, ``forgemaster_debug``, ``forgemaster``, or ``""``) drives
    attestor selection and outcome flavor.

    Forgemaster receives access when DEBUG_MODE is enabled, even if the config
    does not list the role — this is the single hard-coded escape hatch for
    live troubleshooting.
    """
    check = _b("check_command_permission")
    allowed = bool(check(user, command_name)) if check else False
    role_names = {r.name for r in getattr(user, "roles", [])}
    if allowed:
        if VOID_WARDEN_ROLE_NAME in role_names:
            return True, "void_warden"
        if LIBRARIAN_ROLE_NAME in role_names:
            return True, "librarian"
        if FORGEMASTER_ROLE_NAME in role_names:
            return True, "forgemaster_debug" if bool(_b("DEBUG_MODE")) else "forgemaster"
        # Admin / config whitelist hit without a recognised specialist role
        return True, ""
    # DEBUG_MODE bypass for Forgemaster (not in config, not admin)
    if FORGEMASTER_ROLE_NAME in role_names and bool(_b("DEBUG_MODE")):
        return True, "forgemaster_debug"
    return False, ""


def _is_forgemaster(user: discord.Member, command_name: str = "librarium_override") -> bool:
    """Config-driven Forgemaster check (used by /librarium_override)."""
    check = _b("check_command_permission")
    return bool(check(user, command_name)) if check else False


def _find_responsible_warden(bearer: discord.Member, guild: discord.Guild) -> Tuple[Optional[discord.Member], str]:
    """Mirror of forge attestor logic.

    - Bearer is High Command or Librarian -> Void Warden attests
    - Else -> Librarian of bearer's company attests
    - Fallback -> Void Warden
    """
    bearer_roles = {r.name for r in getattr(bearer, "roles", [])}

    def find_void_warden() -> Optional[discord.Member]:
        for m in guild.members:
            if VOID_WARDEN_ROLE_NAME in {r.name for r in m.roles}:
                return m
        return None

    if HIGH_COMMAND_RANKS & bearer_roles or LIBRARIAN_ROLE_NAME in bearer_roles:
        vw = find_void_warden()
        return (vw, "void_warden") if vw else (None, "void_warden")

    bearer_company = _b("_get_member_company_name")(bearer) if _b("_get_member_company_name") else None
    if bearer_company:
        candidates = []
        for m in guild.members:
            if LIBRARIAN_ROLE_NAME not in {r.name for r in m.roles}:
                continue
            m_company = _b("_get_member_company_name")(m) if _b("_get_member_company_name") else None
            if m_company and m_company.lower() == bearer_company.lower():
                candidates.append(m)
        if candidates:
            return random.choice(candidates), "librarian"

    vw = find_void_warden()
    return (vw, "void_warden") if vw else (None, "librarian")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------



@_g.bot.tree.command(
    name="warp_cleanse",
    description="Perform a Warp Cleansing rite to purge corruption from a brother.",
)
@app_commands.describe(
    member="Brother to cleanse",
    intensive="Pay extra charges for a guaranteed full purge (no roll, no backlash).",
    force="[Void Warden only] Bypass recipient cooldowns",
)
async def warp_cleanse(
    interaction: discord.Interaction,
    member: discord.Member,
    intensive: bool = False,
    force: bool = False,
):
    if not await _is_librarius_enabled():
        await interaction.response.send_message(
            "The Librarian subsystem is currently disabled by Forgemaster decree.",
            ephemeral=True,
        )
        return

    allowed, caller_role = _is_librarian_or_void_warden(interaction.user, command_name="warp_cleanse")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    if force and caller_role != "void_warden":
        await interaction.response.send_message(
            "The `force` parameter is restricted to the Void Warden.",
            ephemeral=True,
        )
        return

    if not force:
        can_recv, remaining, _, reason = await _check_warding_recipient_cooldown(int(member.id))
        if not can_recv and remaining is not None:
            bearer_name = _strip_display_name(member.display_name)
            cd = _format_cooldown(remaining)
            if reason == "per_ward":
                msg = (
                    f"**{bearer_name}** was recently cleansed. The mind must settle before further rites.\n"
                    f"Next cleansing available in {cd}."
                )
            else:
                daily_cap = _cfg_int("warding_recipient_max_per_day", WARDING_RECIPIENT_MAX_PER_DAY)
                msg = (
                    f"**{bearer_name}** has reached the daily cleansing limit "
                    f"({daily_cap} per day).\n"
                    f"Next cleansing slot available in {cd}."
                )
            await interaction.response.send_message(msg, ephemeral=True)
            return

    attestor, attestor_role = _find_responsible_warden(member, interaction.guild)
    if attestor is None:
        attestor = interaction.user
        attestor_role = caller_role
    _ = attestor_role  # documented for future audit logging; intentionally unused

    invoker_id = int(interaction.user.id)
    attestor_id = int(attestor.id)
    invoker_is_attestor = (attestor_id == invoker_id)

    cleanser = interaction.user if invoker_is_attestor else attestor
    cleanser_state = await _get_warp_exposure_state(int(cleanser.id))
    cleanser_tier = cleanser_state.get("librarian_tier")
    if cleanser_tier in ("overloaded", "abyssal"):
        if int(member.id) != int(cleanser.id):
            await interaction.response.send_message(
                f"**{cleanser.display_name}** is too unstable ({cleanser_tier.upper()}) to cleanse others. "
                "Self-cleansing or Void Warden intervention required.",
                ephemeral=True,
            )
            return
    # Intensive rite eligibility: only stable / resonant librarians may attempt
    # the high-burden guaranteed purge. Surging+ are too volatile.
    if intensive and cleanser_tier not in (None, "stable", "resonant"):
        await interaction.response.send_message(
            f"**{cleanser.display_name}** is too volatile ({(cleanser_tier or '').upper()}) "
            "to perform an intensive cleansing rite. Standard rite only.",
            ephemeral=True,
        )
        return

    # Recipient state needed early so we can size the intensive cost.
    recipient_state_preview = await _get_warp_exposure_state(int(member.id))
    preview_points = int(recipient_state_preview.get("points", 0) or 0)
    preview_corrupted = bool(recipient_state_preview.get("warp_corrupted"))
    if intensive:
        sanction_key_for_cost = _warp_sanction_key_for_points(preview_points)
        if sanction_key_for_cost == "sanctioned" and not preview_corrupted:
            await interaction.response.send_message(
                "Intensive rite requires an active sanction. The brother is already clear.",
                ephemeral=True,
            )
            return
        charges_required = _get_intensive_cleanse_cost(sanction_key_for_cost, preview_corrupted)
    else:
        charges_required = 1
    contributors: List[Tuple[int, int]] = []
    if not force:
        attestor_charges = await _get_librarian_available_charges(attestor_id)
        invoker_charges = (
            attestor_charges if invoker_is_attestor else await _get_librarian_available_charges(invoker_id)
        )
        if invoker_is_attestor:
            if attestor_charges < charges_required:
                nxt = await _next_warding_regen(int(attestor.id))
                nxt_str = _format_cooldown(nxt) if nxt else "soon"
                await interaction.response.send_message(
                    f"Your warding pool is depleted. Next charge in **{nxt_str}**.",
                    ephemeral=True,
                )
                return
            contributors = [(attestor_id, charges_required)]
        else:
            if attestor_charges >= charges_required:
                contributors = [(attestor_id, charges_required)]
            elif attestor_charges == 0 and invoker_charges >= charges_required:
                attestor = interaction.user
                attestor_id = invoker_id
                contributors = [(invoker_id, charges_required)]
            elif attestor_charges + invoker_charges >= charges_required:
                attestor_contribution = attestor_charges
                invoker_contribution = charges_required - attestor_charges
                contributors = [(attestor_id, attestor_contribution)]
                contributors.append((invoker_id, invoker_contribution))
            else:
                await interaction.response.send_message(
                    "Both the attesting Librarian and your warding pools are depleted. "
                    "Seek another Librarian or wait for regeneration.",
                    ephemeral=True,
                )
                return

    recipient_state = await _get_warp_exposure_state(int(member.id))
    current_points = int(recipient_state.get("points", 0) or 0)

    if intensive:
        # Intensive rite: guaranteed full purge, no roll, no crit/backlash.
        # Mirrors armor's intensive blessing (forge_ops _apply_blessing_intensive_normal).
        outcome_key, fraction, extra = "full", 1.0, 0
    else:
        outcome_key, fraction, extra = _roll_cleanse_outcome(cleanser_tier)

    # Source bonus: cleansing a super-spreader applies +bonus_fraction to removal
    # (capped at 1.0). Mirrors lore: snipping the root rot is more efficient than
    # chasing branches. Skipped on intensive (already at 100%).
    source_bonus_applied = False
    source_bonus_outgoing = 0
    if not intensive:
        try:
            is_super, outgoing = _is_super_spreader(int(member.id), window_hours=24)
            if is_super and not recipient_state.get("is_librarian"):
                bonus = _cfg_float("super_spreader_cleanse_bonus_fraction", 0.10)
                if bonus > 0:
                    fraction = min(1.0, fraction + bonus)
                    source_bonus_applied = True
                    source_bonus_outgoing = outgoing
        except Exception:
            pass

    removed = int(round(current_points * fraction))
    new_recipient_points = max(0, current_points - removed)

    transfer = 0
    if removed > 0:
        transfer_min = _cfg_int("librarian_transfer_min", WARP_LIBRARIAN_TRANSFER_MIN)
        transfer_ratio = _cfg_float("librarian_transfer_ratio", WARP_LIBRARIAN_TRANSFER_RATIO)
        transfer = max(transfer_min, math.ceil(removed * transfer_ratio))
    librarian_gain = transfer + max(0, extra)

    if not force:
        for uid, n in contributors:
            ok = await _consume_librarian_charges(uid, n)
            if not ok:
                await interaction.response.send_message(
                    "Charge consumption race detected. Try again.",
                    ephemeral=True,
                )
                return

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
            rstate = dict(data.get(str(int(member.id)), _default_exposure_state()))
            for k, v in _default_exposure_state().items():
                rstate.setdefault(k, v)
            rstate["points"] = new_recipient_points
            rstate["exposure_tier"] = (
                _brother_tier_for_points(new_recipient_points) if not rstate.get("is_librarian") else None
            )
            if rstate.get("is_librarian"):
                rstate["librarian_tier"] = _librarian_tier_for_points(new_recipient_points)
            rstate["last_warding_timestamp"] = datetime.utcnow().isoformat()
            immunity_h = random.randint(
                _cfg_int("post_cleanse_immunity_min_hours", WARP_POST_CLEANSE_IMMUNITY_MIN_HOURS),
                _cfg_int("post_cleanse_immunity_max_hours", WARP_POST_CLEANSE_IMMUNITY_MAX_HOURS),
            )
            rstate["immunity_until"] = (datetime.utcnow() + timedelta(hours=immunity_h)).isoformat()
            if removed > 0:
                rstate["last_detection_alert_tier"] = None
            # Cleanse fully resets corruption tracking when brother returns to clean
            if new_recipient_points <= 0:
                rstate["warp_corrupted"] = False
                rstate["restricted_aar_count"] = 0
            else:
                # Demoting below restricted clears the count (mirrors armor: leaving
                # critical resets the fracture counter for the next escalation).
                if _warp_sanction_key_for_points(new_recipient_points) != "restricted":
                    rstate["restricted_aar_count"] = 0
            data[str(int(member.id))] = rstate
            _save_warp_exposure(data)
    except Exception as e:
        try:
            _g.logger.error(f"warp_cleanse: failed to update recipient state: {e}")
        except Exception:
            pass

    if librarian_gain > 0:
        try:
            async with _g.WARP_EXPOSURE_LOCK:
                data = _load_warp_exposure()
                cid = str(int(cleanser.id))
                cstate = dict(data.get(cid, _default_exposure_state()))
                for k, v in _default_exposure_state().items():
                    cstate.setdefault(k, v)
                cstate["is_librarian"] = True
                if not cstate.get("last_decay_check"):
                    cstate["last_decay_check"] = datetime.utcnow().isoformat()
                cstate["points"] = int(cstate.get("points", 0) or 0) + librarian_gain
                cstate["librarian_tier"] = _librarian_tier_for_points(cstate["points"])
                cstate["exposure_tier"] = None
                data[cid] = cstate
                _save_warp_exposure(data)
        except Exception:
            pass

    await _record_warding_for_recipient(int(member.id))

    # Sync sanction roles for the bearer (and clear if zeroed)
    if interaction.guild is not None:
        try:
            await _sync_sanction_role_for_member(
                member,
                interaction.guild,
                new_recipient_points,
                bool(recipient_state.get("is_librarian")),
            )
        except Exception:
            pass

    # Record cleanse + repost Librarium Chronicle
    try:
        await _record_cleanse_in_chronicle(
            int(member.id),
            int(cleanser.id),
            outcome_key,
            removed,
            transfer,
        )
    except Exception:
        pass
    try:
        await _repost_librarium_chronicle_at_bottom(interaction.guild)
    except Exception:
        pass

    flavor = random.choice(WARP_CLEANSE_OUTCOME_FLAVOR.get(outcome_key, ["The rite is complete."]))
    new_sanction_key = _warp_sanction_key_for_points(new_recipient_points)
    sanction_label, sanction_desc = WARP_SANCTION_STATUS.get(new_sanction_key, ("Sanctioned", ""))
    bearer_name = _strip_display_name(member.display_name)
    cleanser_name = _strip_display_name(cleanser.display_name)

    title_emoji = {"full": "🧿", "partial": "🌀", "backlash": "⚠️"}.get(outcome_key, "🧿")
    title_text = "᛭⋅ INTENSIVE CLEANSING RITE ⋅᛭" if intensive else "᛭⋅ WARP CLEANSING RITE ⋅᛭"

    embed = discord.Embed(
        title=title_text,
        description=f"*{flavor}*",
        color=0x9B59B6 if outcome_key != "backlash" else 0xE67E22,
    )
    embed.add_field(name="▸ Bearer", value=f"**{bearer_name}**", inline=True)
    embed.add_field(name="▸ Cleanser", value=f"**{cleanser_name}**", inline=True)
    if intensive:
        embed.add_field(
            name="▸ Rite Type",
            value=f"🧿 **INTENSIVE** — {charges_required} charges · guaranteed full purge",
            inline=False,
        )
    embed.add_field(
        name="▸ Outcome",
        value=f"{title_emoji} **{outcome_key.upper()}** — {int(fraction*100)}% removed",
        inline=False,
    )
    if source_bonus_applied:
        embed.add_field(
            name="▸ Source Bonus",
            value=(
                f"🌀 **Super-spreader cleansed** — {source_bonus_outgoing} active downstream "
                f"infection{'s' if source_bonus_outgoing != 1 else ''} severed at the root."
            ),
            inline=False,
        )
    embed.add_field(
        name="▸ Warp Sanction (post-cleanse)",
        value=f"🧿 **{sanction_label.upper()}**\n{sanction_desc}",
        inline=False,
    )
    if librarian_gain > 0:
        embed.add_field(
            name="▸ Librarian Burden",
            value=f"+{librarian_gain} exposure absorbed by **{cleanser_name}**",
            inline=False,
        )
    embed.set_footer(text="The Librarium watches. The wards hold.")

    await interaction.response.send_message(embed=embed)


@_g.bot.tree.command(
    name="warp_status",
    description="Show at-risk brothers (Librarian: own company + backfill, Void Warden: fortress-wide).",
)
async def warp_status(interaction: discord.Interaction):
    if not await _is_librarius_enabled():
        await interaction.response.send_message(
            "The Librarian subsystem is currently disabled.", ephemeral=True
        )
        return
    allowed, caller_role = _is_librarian_or_void_warden(interaction.user, command_name="warp_status")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild context required.", ephemeral=True)
        return

    # Scope is gap-filling, mirroring /armor_status:
    #   • Librarian → own company is ring 0, orphan companies (no Watch Librarian
    #     assigned) ring 1, peer-covered companies ring 2.
    #   • Void Warden / Forgemaster (debug) → fortress-wide, no rings.
    # A Librarian not in any company falls through to fortress-wide.
    if caller_role == "librarian":
        caller_company = _b("_get_member_company_name")(interaction.user) if _b("_get_member_company_name") else None
    else:
        caller_company = None

    orphan_companies: set = (
        _b("_orphan_companies_for_role")(guild, "Watch Librarian")
        if caller_company
        else set()
    )

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
    except Exception:
        data = {}

    threshold = _cfg_int("super_spreader_threshold", 3)
    rows = []  # (ring, pts, uid, name, tier, is_lib, corrupted, is_super, has_targets, member_company)
    for uid, raw in data.items():
        try:
            member = guild.get_member(int(uid))
        except Exception:
            member = None
        if member is None:
            continue
        # Skip non-participants (no rank, Reserves, Interred) — symmetric with
        # /armor_status and the warp AAR hook.
        is_active_fn = _b("_is_active_participant")
        if is_active_fn and not is_active_fn(member):
            continue
        pts = int((raw or {}).get("points", 0) or 0)
        if pts <= 0:
            continue
        mem_company = _b("_get_member_company_name")(member) if _b("_get_member_company_name") else None
        ring = (
            _b("_company_scope_ring")(mem_company, caller_company, orphan_companies)
            if caller_company
            else 0
        )
        is_lib = bool(raw.get("is_librarian"))
        corrupted = bool(raw.get("warp_corrupted"))
        tier = _librarian_tier_for_points(pts) if is_lib else _brother_tier_for_points(pts)
        is_super = False
        has_targets = False
        if not is_lib:
            targets = _compute_outgoing_infections(int(uid), states=data, window_hours=24)
            is_super = len(targets) >= threshold and threshold > 0
            has_targets = bool(targets)
        try:
            row_name = _b("_format_member_styled")(guild, str(uid), include_chapter=True) \
                if _b("_format_member_styled") else member.display_name
        except Exception:
            row_name = member.display_name
        rows.append((ring, pts, str(uid), row_name, tier, is_lib, corrupted, is_super, has_targets, mem_company))

    # Sort by (ring asc, severity desc) — own company first, then orphans, then peers.
    rows.sort(key=lambda r: (r[0], -r[1]))

    if not rows:
        if caller_company:
            scope = (
                f"company **{_b('_extract_company_short_name')(caller_company)}** "
                f"and the wider fortress"
            )
        else:
            scope = "the fortress"
        await interaction.response.send_message(
            f"No exposure detected in {scope}. The wards hold.", ephemeral=True
        )
        return

    top_rows = rows[:25]

    # Node-label + subtree rendering live at module scope (_warp_node_label,
    # _warp_render_subtree) so /warp_scry can reuse them.

    # ── Pass 1: identify tree roots within authority and expand subtrees. ─────
    # A "root" is a non-librarian spreader with downstream infections in the
    # caller's authority (ring 0 = own company, ring 1 = orphan companies).
    # Out-of-authority rings (>= 2) never anchor a tree — their outbreaks belong
    # to peer librarians. The caller still sees ring-2 brothers as solo entries
    # if they're isolated cases, but won't see other librarians' chains.
    tree_roots = []  # (direct_count, uid, name, pts, corrupted, is_super, ring, mem_company)
    for ring, pts, uid, name, tier, is_lib, corrupted, is_super, has_targets, mem_company in top_rows:
        in_authority = (caller_company is None) or (ring <= 1)
        if not is_lib and has_targets and in_authority:
            direct = len(_compute_outgoing_infections(int(uid), states=data, window_hours=24))
            tree_roots.append((direct, uid, name, pts, corrupted, is_super, ring, mem_company))
    tree_roots.sort(key=lambda r: -r[0])

    # ── Pass 2: build the unified at-risk list. Roots render with their
    # downstream subtree indented; isolated entries (not covered by any tree)
    # render flat. Mirrors the single ▸ Brothers at Risk field of /armor_status.
    lines: List[str] = []
    covered_uids: set = set()
    for _direct, root_uid, root_name, root_pts, root_corrupted, root_is_super, root_ring, root_company in tree_roots[:3]:
        rflags = ""
        if root_is_super:
            rflags += WARP_SPREADER_ICON
        if root_corrupted:
            rflags += WARP_CORRUPTED_ICON
        rflag_str = f" {rflags}" if rflags else ""
        rcompany_tag = ""
        if caller_company and root_ring > 0 and root_company:
            try:
                rcompany_tag = f" `({_b('_extract_company_short_name')(root_company)})`"
            except Exception:
                rcompany_tag = ""
        # Default scope: 1 hop only. Librarians use /warp_scry to trace deeper.
        visited = {str(root_uid)}
        subtree_lines: List[str] = []
        _warp_render_subtree(guild, data, str(root_uid), depth=0, visited=visited, lines_out=subtree_lines, max_depth=1)
        # Total downstream count (full graph) — distinct from rendered hop count.
        full_visited: set = {str(root_uid)}
        _warp_render_subtree(guild, data, str(root_uid), depth=0, visited=full_visited, lines_out=[], max_depth=99)
        downstream = len(full_visited) - 1
        direct = len(visited) - 1
        deeper = downstream - direct
        deeper_tag = f" _(+{deeper} deeper — /warp_scry)_" if deeper > 0 else ""
        lines.append(
            f"{WARP_SPREADER_ICON} {root_name}{rcompany_tag} · {root_pts}c{rflag_str} _(→ {direct})_{deeper_tag}"
        )
        lines.extend(subtree_lines)
        covered_uids |= visited

    for ring, pts, uid, name, tier, is_lib, corrupted, is_super, has_targets, mem_company in top_rows:
        if str(uid) in covered_uids:
            continue
        if is_lib:
            tier_icon = WARP_LIBRARIAN_TIER_ICON.get(tier, "🟢")
            marker = f"{WARP_LIBRARIAN_MARKER_ICON}{tier_icon}"
        else:
            tier_icon = WARP_BROTHER_TIER_ICON.get(tier, "🟢")
            marker = tier_icon
        flags = ""
        if is_super:
            flags += WARP_SPREADER_ICON
        if corrupted:
            flags += WARP_CORRUPTED_ICON
        flag_str = f" {flags}" if flags else ""
        company_tag = ""
        if caller_company and ring > 0 and mem_company:
            try:
                company_tag = f" `({_b('_extract_company_short_name')(mem_company)})`"
            except Exception:
                company_tag = ""
        lines.append(f"{marker} {name}{company_tag} · {pts}c{flag_str}")

    embed = discord.Embed(
        title="᛭⋅ WARP STATUS ⋅᛭",
        color=0x9B59B6,
    )

    # Truncate to Discord's 1024-char field limit, append soft footer if cut.
    value = "\n".join(lines)
    if len(value) > 1024:
        kept: List[str] = []
        running = 0
        for ln in lines:
            addition = (("\n" if kept else "") + ln)
            remaining = len(lines) - len(kept)
            tentative_footer = f"\n_…and {remaining - 1} more_" if remaining > 1 else ""
            if running + len(addition) + len(tentative_footer) > 1024:
                kept.append(f"_…and {remaining} more_")
                break
            kept.append(ln)
            running += len(addition)
        value = "\n".join(kept)

    embed.add_field(name="▸ Brothers at Risk", value=value, inline=False)
    embed.add_field(
        name="▸ Key",
        value=(
            "🟡 Tainted 🟠 Exposed 🔴 Volatile 💀 Breached ⚫ Catastrophic | "
            f"{WARP_CORRUPTED_ICON} Corrupted {WARP_SPREADER_ICON} Spreader "
            f"{WARP_LIBRARIAN_MARKER_ICON} Librarian | c = cycles | "
            "trace deeper with `/warp_scry`"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@_g.bot.tree.command(
    name="warp_scry",
    description="Trace a brother's full contagion subtree (deeper than /warp_status).",
)
@app_commands.describe(member="Brother whose downstream contagion to scry.")
async def warp_scry(interaction: discord.Interaction, member: discord.Member):
    if not await _is_librarius_enabled():
        await interaction.response.send_message(
            "The Librarian subsystem is currently disabled.", ephemeral=True
        )
        return
    allowed, caller_role = _is_librarian_or_void_warden(interaction.user, command_name="warp_scry")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Channel gate — same allowlist as /warp_status (warp_status_allowed_channels).
    allowed_channels = _get_warp_status_allowed_channels()
    if allowed_channels:
        ch_id = getattr(interaction.channel, "id", None)
        if ch_id is None or int(ch_id) not in allowed_channels:
            await interaction.response.send_message(
                "This rite may only be performed in the Librarium watch channels.",
                ephemeral=True,
            )
            return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild context required.", ephemeral=True)
        return

    # Target must be an active participant (ranked, not Reserves, not Interred).
    is_active_fn = _b("_is_active_participant")
    if is_active_fn and not is_active_fn(member):
        await interaction.response.send_message(
            f"**{member.display_name}** is not an active participant in the Librarium watch. "
            "There are no warp-currents to trace.",
            ephemeral=True,
        )
        return

    # Authority check: librarians may only scry brothers in their own company
    # or in orphan companies (no Watch Librarian assigned). Void Warden /
    # Forgemaster (debug) have fortress-wide reach.
    is_unrestricted = caller_role in ("void_warden", "forgemaster_debug", "")
    if not is_unrestricted:
        caller_company = _b("_get_member_company_name")(interaction.user) if _b("_get_member_company_name") else None
        target_company = _b("_get_member_company_name")(member) if _b("_get_member_company_name") else None
        orphan_companies: set = (
            _b("_orphan_companies_for_role")(guild, "Watch Librarian")
            if caller_company
            else set()
        )
        ring = (
            _b("_company_scope_ring")(target_company, caller_company, orphan_companies)
            if caller_company
            else 0
        )
        if ring > 1:
            await interaction.response.send_message(
                f"**{member.display_name}** lies beyond your wardship. "
                "Their scrying belongs to that company's Watch Librarian.",
                ephemeral=True,
            )
            return

    # Cooldown gate
    can_scry, remaining, reason = await _check_warp_scry_cooldown(int(interaction.user.id))
    if not can_scry:
        cd = _format_cooldown(remaining) if remaining else "soon"
        if reason == "daily":
            limit = _cfg_int("warp_scry_daily_limit", 2)
            msg = (
                f"You have spent your scrying allotment ({limit} per day). "
                f"The currents will steady for another rite in {cd}."
            )
        else:
            msg = f"Your mind is still adrift from the last scrying. The currents will steady in {cd}."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
    except Exception:
        data = {}

    target_uid = str(member.id)
    target_raw = data.get(target_uid) or {}
    target_pts = int(target_raw.get("points", 0) or 0)

    # Render full downstream subtree (depth 3). Depth 3 covers most realistic
    # contagion chains while keeping embeds within the 1024-char field cap.
    visited: set = {target_uid}
    subtree_lines: List[str] = []
    _warp_render_subtree(
        guild, data, target_uid, depth=0, visited=visited,
        lines_out=subtree_lines, max_depth=3,
    )
    direct = len(_compute_outgoing_infections(int(target_uid), states=data, window_hours=24))
    full_visited: set = {target_uid}
    _warp_render_subtree(guild, data, target_uid, depth=0, visited=full_visited, lines_out=[], max_depth=99)
    downstream_total = len(full_visited) - 1
    deeper = downstream_total - (len(visited) - 1)

    # Stamp the cooldown regardless of whether the target had downstream
    # (the rite was performed; the warp was probed).
    await _record_warp_scry(int(interaction.user.id), int(member.id))

    embed = discord.Embed(
        title="᛭⋅ WARP SCRY ⋅᛭",
        color=0x9B59B6,
    )
    root_label = _warp_node_label(guild, data, target_uid)
    if direct == 0:
        if target_pts <= 0:
            body = f"{root_label}\n_The brother carries no taint. The currents pass clean._"
        else:
            body = f"{root_label}\n_The taint is held — no transmission detected within the last 24 cycles._"
    else:
        header = f"{root_label} _(→ {direct} direct, {downstream_total} total)_"
        body_lines = [header] + subtree_lines
        if deeper > 0:
            body_lines.append(f"_…+{deeper} deeper beyond the scry's reach._")
        body = "\n".join(body_lines)
        if len(body) > 1024:
            kept: List[str] = []
            running = 0
            for ln in body_lines:
                addition = (("\n" if kept else "") + ln)
                remaining_count = len(body_lines) - len(kept)
                tentative_footer = f"\n_…and {remaining_count - 1} more_" if remaining_count > 1 else ""
                if running + len(addition) + len(tentative_footer) > 1024:
                    kept.append(f"_…and {remaining_count} more_")
                    break
                kept.append(ln)
                running += len(addition)
            body = "\n".join(kept)

    embed.add_field(name="▸ Contagion Trace", value=body, inline=False)
    embed.add_field(
        name="▸ Key",
        value=(
            "🟡 Tainted 🟠 Exposed 🔴 Volatile 💀 Breached ⚫ Catastrophic | "
            f"{WARP_CORRUPTED_ICON} Corrupted {WARP_SPREADER_ICON} Spreader "
            f"{WARP_LIBRARIAN_MARKER_ICON} Librarian | c = cycles"
        ),
        inline=False,
    )
    cooldown_min = _cfg_int("warp_scry_cooldown_minutes", 30)
    daily_limit = _cfg_int("warp_scry_daily_limit", 2)
    footer_bits: List[str] = []
    if daily_limit > 0:
        footer_bits.append(f"{daily_limit}/day per Librarian")
    if cooldown_min > 0:
        footer_bits.append(f"{cooldown_min}m between rites")
    if footer_bits:
        embed.set_footer(text=" · ".join(footer_bits))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@_g.bot.tree.command(
    name="librarium_chronicle",
    description="Post a sanitized Librarium status snapshot (Void Warden only).",
)
async def librarium_chronicle(interaction: discord.Interaction):
    allowed, role = _is_librarian_or_void_warden(interaction.user, command_name="librarium_chronicle")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if role not in ("void_warden", "forgemaster_debug"):
        await interaction.response.send_message(
            "Only the Void Warden may post the Librarium Chronicle.", ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message("Guild context required.", ephemeral=True)
        return

    channel_id = _get_librarium_watch_channel_id()
    if not channel_id:
        await interaction.response.send_message(
            "No Librarium watch channel configured. Set `warp_corruption.librarium_watch_channel_id` in config.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await _repost_librarium_chronicle_at_bottom(interaction.guild)
        await interaction.followup.send(
            "Librarium Chronicle reposted.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"Failed to repost Librarium Chronicle: {e}", ephemeral=True
        )


@_g.bot.tree.command(
    name="librarium_override",
    description="Enable or disable the Librarian subsystem (Forgemaster only).",
)
@app_commands.describe(enabled="True to enable, False to disable")
async def librarium_override(interaction: discord.Interaction, enabled: bool):
    if not _is_forgemaster(interaction.user, command_name="librarium_override"):
        await interaction.response.send_message(
            "Only the Forgemaster may toggle the Librarian subsystem.", ephemeral=True
        )
        return
    try:
        async with _g.LIBRARIUM_OVERRIDE_LOCK:
            _save_override({
                "enabled": bool(enabled),
                "set_by": str(interaction.user.id),
                "ts": datetime.utcnow().isoformat(),
            })
    except Exception:
        pass
    state_word = "ENABLED" if enabled else "DISABLED"
    await interaction.response.send_message(
        f"Librarian subsystem **{state_word}**.", ephemeral=True
    )
