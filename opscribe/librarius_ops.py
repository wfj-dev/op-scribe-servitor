"""Librarian / Warp Corruption subsystem.

Mirrors the Techmarine armor system in shape but uses contagion + Librarian
self-burden mechanics. Provides:

- Persistent state for warp exposure (per brother + per Librarian)
- Personal warding charge pool (regenerates on a timer)
- Librarian exposure decay (calculated lazily on read)
- AAR hook: direct Black Laurels gain, contagion spread, penalty roll
- Warp Sanction status surfacing for brothers (visibility-restricted)
- Commands: /warp_cleanse, /psychic_status, /librarium_chronicle,
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
    for uid, raw in data.items():
        pts = int((raw or {}).get("points", 0) or 0)
        if (raw or {}).get("is_librarian"):
            lt = _librarian_tier_for_points(pts)
            librarian_tier_counts[lt] = librarian_tier_counts.get(lt, 0) + 1
        else:
            key = _warp_sanction_key_for_points(pts)
            bucket_counts[key] = bucket_counts.get(key, 0) + 1
            if (raw or {}).get("warp_corrupted"):
                corrupted_count += 1
            try:
                is_super, _ = _is_super_spreader(int(uid), states=data, window_hours=24)
                if is_super:
                    super_spreader_count += 1
            except Exception:
                pass

    ambient = random.choice(LIBRARIUM_AMBIENT_MESSAGES)

    embed = discord.Embed(
        title="᛭⋅ LIBRARIUM CHRONICLE ⋅᛭",
        description=f"*{ambient}*",
        color=0x9B59B6,
    )
    sanction_lines = []
    for key, (label, _desc) in WARP_SANCTION_STATUS.items():
        count = bucket_counts.get(key, 0)
        if count:
            sanction_lines.append(f"**{label}**: {count}")
    if corrupted_count:
        sanction_lines.append(f"⚠️ **Warp-Corrupted**: {corrupted_count}")
    if super_spreader_count:
        sanction_lines.append(f"🌀 **Super-Spreaders**: {super_spreader_count}")
    embed.add_field(
        name="▸ Sanction Roster",
        value="\n".join(sanction_lines) if sanction_lines else "All clear.",
        inline=False,
    )
    lib_lines = []
    for tier in (None, *WARP_LIBRARIAN_TIERS):
        n = librarian_tier_counts.get(tier, 0)
        if n:
            lbl, _ = WARP_LIBRARIAN_TIER_DESCRIPTIONS.get(tier, ("CLEAR", ""))
            lib_lines.append(f"**{lbl}**: {n}")
    embed.add_field(
        name="▸ Librarian Burden",
        value="\n".join(lib_lines) if lib_lines else "The Librarium is unburdened.",
        inline=False,
    )

    # Contagion Watch — top active spreaders in the last 24h (mirrors forge watchlist).
    threshold = _cfg_int("super_spreader_threshold", 3)
    watch_entries = []  # (out_count, name_label, is_super)
    if guild is not None:
        for uid, raw in data.items():
            if (raw or {}).get("is_librarian"):
                continue
            try:
                member = guild.get_member(int(uid))
            except Exception:
                member = None
            if member is None:
                continue
            targets = _compute_outgoing_infections(int(uid), states=data, window_hours=24)
            if not targets:
                continue
            is_super = len(targets) >= threshold and threshold > 0
            try:
                name_label = _b("_format_member_styled")(guild, str(uid), include_chapter=True) \
                    if _b("_format_member_styled") else member.display_name
            except Exception:
                name_label = member.display_name
            watch_entries.append((len(targets), name_label, is_super))
    watch_entries.sort(key=lambda r: r[0], reverse=True)
    watch_lines = []
    for out_count, name_label, is_super in watch_entries[:5]:
        icon = "🌀" if is_super else "🟣"
        suffix = " · **SUPER-SPREADER**" if is_super else ""
        watch_lines.append(f"{icon} {name_label} · {out_count} infected{suffix}")
    if watch_lines:
        embed.add_field(
            name=f"▸ Contagion Watch (24h, threshold ≥{threshold})",
            value="\n".join(watch_lines),
            inline=False,
        )

    # Append recent cleanse activity to the chronicle
    try:
        async with _g.LIBRARIUM_CHRONICLE_LOCK:
            chron = _load_librarium_chronicle()
    except Exception:
        chron = {}
    history = (chron.get("cleanse_history") or [])[-5:]
    if history:
        recent_lines = []
        for entry in reversed(history):
            outcome = entry.get("outcome", "?")
            removed = int(entry.get("removed", 0) or 0)
            recent_lines.append(f"• {outcome} — {removed} pts removed")
        embed.add_field(name="▸ Recent Rites", value="\n".join(recent_lines), inline=False)

    embed.set_footer(text=f"Last updated • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    return embed


async def _repost_librarium_chronicle_at_bottom(guild: Optional[discord.Guild]):
    """Delete the prior chronicle message (if any) and post a fresh one at the bottom."""
    if guild is None:
        return
    channel_id = _get_librarium_watch_channel_id()
    if not channel_id:
        return
    try:
        channel = guild.get_channel(int(channel_id))
    except Exception:
        channel = None
    if channel is None:
        return

    embed = await _build_librarium_chronicle_embed(guild)

    # Delete previous chronicle message if we have one
    prev_id = await _get_librarium_dashboard_message_id()
    if prev_id:
        try:
            old = await channel.fetch_message(int(prev_id))
            await old.delete()
        except Exception:
            pass

    try:
        sent = await channel.send(embed=embed)
        await _set_librarium_dashboard_message_id(sent.id)
    except Exception as e:
        try:
            _g.logger.debug(f"_repost_librarium_chronicle_at_bottom failed: {e}")
        except Exception:
            pass


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
            for bid in brother_ids:
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
    force="[Void Warden only] Bypass recipient cooldowns",
)
async def warp_cleanse(
    interaction: discord.Interaction,
    member: discord.Member,
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
            bearer_name = member.display_name.replace("●", "").replace("⚬", "").strip()
            cd = _format_cooldown(remaining)
            if reason == "per_ward":
                msg = (
                    f"**{bearer_name}** was recently cleansed. The mind must settle before further rites.\n"
                    f"Next cleansing available in {cd}."
                )
            else:
                msg = (
                    f"**{bearer_name}** has reached the daily cleansing limit "
                    f"({WARDING_RECIPIENT_MAX_PER_DAY} per day).\n"
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
    invoker_is_attestor = (attestor.id == invoker_id)

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

    charges_required = 1
    contributors: List[Tuple[int, int]] = []
    if not force:
        attestor_charges = await _get_librarian_available_charges(int(attestor.id))
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
            contributors = [(int(attestor.id), charges_required)]
        else:
            if attestor_charges >= charges_required:
                contributors = [(int(attestor.id), charges_required)]
            elif attestor_charges == 0 and invoker_charges >= charges_required:
                attestor = interaction.user
                contributors = [(invoker_id, charges_required)]
            else:
                await interaction.response.send_message(
                    "Both the attesting Librarian and your warding pools are depleted. "
                    "Seek another Librarian or wait for regeneration.",
                    ephemeral=True,
                )
                return

    recipient_state = await _get_warp_exposure_state(int(member.id))
    current_points = int(recipient_state.get("points", 0) or 0)

    outcome_key, fraction, extra = _roll_cleanse_outcome(cleanser_tier)

    # Source bonus: cleansing a super-spreader applies +bonus_fraction to removal
    # (capped at 1.0). Mirrors lore: snipping the root rot is more efficient than
    # chasing branches.
    source_bonus_applied = False
    source_bonus_outgoing = 0
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
            ok = await _consume_librarian_charges(uid, n, display_name=str(uid))
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
    bearer_name = member.display_name.replace("●", "").replace("⚬", "").strip()
    cleanser_name = cleanser.display_name.replace("●", "").replace("⚬", "").strip()

    title_emoji = {"full": "🧿", "partial": "🌀", "backlash": "⚠️"}.get(outcome_key, "🧿")

    embed = discord.Embed(
        title="᛭⋅ WARP CLEANSING RITE ⋅᛭",
        description=f"*{flavor}*",
        color=0x9B59B6 if outcome_key != "backlash" else 0xE67E22,
    )
    embed.add_field(name="▸ Bearer", value=f"**{bearer_name}**", inline=True)
    embed.add_field(name="▸ Cleanser", value=f"**{cleanser_name}**", inline=True)
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
    name="psychic_status",
    description="Show at-risk brothers in your company (Librarian) or all (Void Warden).",
)
@app_commands.describe(company="Optional company name (Void Warden only)")
async def psychic_status(interaction: discord.Interaction, company: Optional[str] = None):
    if not await _is_librarius_enabled():
        await interaction.response.send_message(
            "The Librarian subsystem is currently disabled.", ephemeral=True
        )
        return
    allowed, caller_role = _is_librarian_or_void_warden(interaction.user, command_name="psychic_status")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild context required.", ephemeral=True)
        return

    if caller_role == "librarian":
        target_company = _b("_get_member_company_name")(interaction.user) if _b("_get_member_company_name") else None
    else:
        target_company = company

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
    except Exception:
        data = {}

    threshold = _cfg_int("super_spreader_threshold", 3)
    rows = []  # (pts, name, tier_lbl, is_lib, corrupted, is_super, target_names)
    for uid, raw in data.items():
        try:
            member = guild.get_member(int(uid))
        except Exception:
            member = None
        if member is None:
            continue
        pts = int((raw or {}).get("points", 0) or 0)
        if pts <= 0:
            continue
        mem_company = _b("_get_member_company_name")(member) if _b("_get_member_company_name") else None
        if target_company and (mem_company or "").lower() != str(target_company).lower():
            continue
        is_lib = bool(raw.get("is_librarian"))
        corrupted = bool(raw.get("warp_corrupted"))
        tier = _librarian_tier_for_points(pts) if is_lib else _brother_tier_for_points(pts)
        if is_lib:
            tier_lbl, _desc = WARP_LIBRARIAN_TIER_DESCRIPTIONS.get(tier, ("UNKNOWN", ""))
        else:
            tier_lbl, _desc = WARP_BROTHER_TIER_DESCRIPTIONS.get(tier, ("UNKNOWN", ""))
        is_super = False
        target_names: List[str] = []
        if not is_lib:
            targets = _compute_outgoing_infections(int(uid), states=data, window_hours=24)
            is_super = len(targets) >= threshold and threshold > 0
            for tid in targets:
                try:
                    tmember = guild.get_member(int(tid))
                    target_names.append(tmember.display_name if tmember else f"<@{tid}>")
                except Exception:
                    target_names.append(f"<@{tid}>")
        rows.append((pts, member.display_name, tier_lbl, is_lib, corrupted, is_super, target_names))

    rows.sort(key=lambda r: r[0], reverse=True)

    if not rows:
        scope = f"company **{target_company}**" if target_company else "fortress"
        await interaction.response.send_message(
            f"No exposure detected in {scope}. The wards hold.", ephemeral=True
        )
        return

    lines = []
    chain_lines = []
    super_count = 0
    for pts, name, tier_lbl, is_lib, corrupted, is_super, target_names in rows[:25]:
        marker = "🧿" if is_lib else ("🌀" if is_super else "▸")
        suffix_bits = []
        if is_super:
            suffix_bits.append("**SUPER-SPREADER**")
            super_count += 1
        if corrupted:
            suffix_bits.append("⚠️ **CORRUPTED**")
        suffix = (" " + " · ".join(suffix_bits)) if suffix_bits else ""
        lines.append(f"{marker} **{name}** — {tier_lbl} ({pts} pts){suffix}")
        # Build chain entry for any spreader with downstream infections
        if not is_lib and target_names:
            chain_marker = "🌀" if is_super else "▸"
            if len(target_names) > 5:
                target_str = ", ".join(target_names[:5]) + f" (+{len(target_names) - 5} more)"
            else:
                target_str = ", ".join(target_names)
            chain_lines.append(
                f"{chain_marker} **{name}** → {len(target_names)}\n   ↳ {target_str}"
            )

    scope = f"Company: **{target_company}**" if target_company else "Fortress-wide"
    desc_bits = [scope]
    if super_count:
        desc_bits.append(
            f"⚠️ **{super_count} super-spreader{'s' if super_count != 1 else ''}** detected"
        )
    embed = discord.Embed(
        title="᛭⋅ PSYCHIC STATUS ⋅᛭",
        description="\n".join(desc_bits),
        color=0xE67E22 if super_count else 0x9B59B6,
    )
    embed.add_field(name="▸ Exposed Brothers", value="\n".join(lines), inline=False)
    if chain_lines:
        # Show top 10 chains to keep embed bounded
        embed.add_field(
            name=f"▸ Contagion Chains (24h, threshold ≥{threshold})",
            value="\n\n".join(chain_lines[:10]),
            inline=False,
        )
        embed.set_footer(text="Cleansing super-spreaders applies a source bonus to the rite.")
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
