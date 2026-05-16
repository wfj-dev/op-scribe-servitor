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
import re
import shutil
import discord
from discord import app_commands
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import sys as _sys

from .constants import *  # noqa: F401,F403
from .constants import _strip_display_name
from .flavor_text import *  # noqa: F401,F403
from .flavor_text import (  # private names, not re-exported by *
    _warp_sanction_key_for_points,
    _warp_sanction_key_for_state,
)
from .permissions import *  # noqa: F401,F403
from . import _bot_globals as _g


def _b(name):
    """Resolve name via bot module (test-mock compatibility)."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


_SANCTION_STATE_UNSET = object()


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
    """Susceptibility display bands keyed by infection tier label.

    The new schema doesn't ship per-tier point bands in config (the gate is the
    ``infection_probability_tiers`` ladder), so this returns the flavor-text
    defaults — only used for human-readable risk labels.
    """
    return {k: v for k, v in WARP_BROTHER_TIER_BANDS.items()}


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
    """Return ``{infection_state: {penalty: prob}}`` table.

    The new schema keeps these probabilities as flavor constants — config no
    longer overrides the brother penalty distribution (mirrors the armor
    system, which keeps damage-tier penalty weights in code).
    """
    return WARP_PENALTY_PROBABILITIES


def _get_spread_chances() -> Dict[str, float]:
    """Return ``{infection_state: chance}`` for contagion spread rolls."""
    cfg = _warp_config()
    raw = cfg.get("spread_chances_by_tier") or {}
    if not raw:
        return dict(WARP_SPREAD_CHANCES)
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except Exception:
            continue
    return out or dict(WARP_SPREAD_CHANCES)


def _get_spread_susceptibility_gain() -> int:
    """Return contagion spread gain (prefers new key, supports legacy alias)."""
    cfg = _warp_config()
    if cfg.get("spread_susceptibility_gain") is not None:
        return _cfg_int("spread_susceptibility_gain", WARP_SPREAD_SUSCEPTIBILITY_GAIN)
    return _cfg_int("spread_amount", WARP_SPREAD_SUSCEPTIBILITY_GAIN)


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
    """Return ``{mission_kind: susceptibility_gain}`` from config.

    Prefers the new ``bl_susceptibility_gain`` key; falls back to legacy
    ``bl_exposure_gain`` for backward-compat with older config files.
    """
    cfg = _warp_config()
    raw = cfg.get("bl_susceptibility_gain") or cfg.get("bl_exposure_gain") or {}
    if not isinstance(raw, dict):
        return dict(WARP_BL_SUSCEPTIBILITY_GAIN)
    out: Dict[str, int] = dict(WARP_BL_SUSCEPTIBILITY_GAIN)
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

# Demand cost per Librarian exposure tier — mirrors _DEFAULT_INTENSIVE_CLEANSE_COSTS
# for brothers.  Librarians only receive standard rites (1 charge each), but the
# tier scaling preserves the severity signal in the pressure score.
_DEFAULT_LIBRARIAN_DEMAND_COSTS = {
    "stable": 1,
    "resonant": 2,
    "surging": 3,
    "overloaded": 4,
    "abyssal": 4,
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


def _get_librarian_demand_cost(librarian_tier: Optional[str]) -> int:
    """Return the demand charge contribution for a Librarian at the given exposure tier.

    Mirrors ``_get_intensive_cleanse_cost`` for non-Librarian brothers: cost scales
    with burden severity so that heavily-loaded Librarians weight the pressure signal
    appropriately.  Configurable via ``librarian_demand_costs`` in warp config.
    """
    if not librarian_tier:
        return 0
    cfg = _warp_config()
    raw = cfg.get("librarian_demand_costs") or _DEFAULT_LIBRARIAN_DEMAND_COSTS
    try:
        return int(raw.get(librarian_tier) or _DEFAULT_LIBRARIAN_DEMAND_COSTS.get(librarian_tier, 1))
    except Exception:
        return _DEFAULT_LIBRARIAN_DEMAND_COSTS.get(librarian_tier, 1)


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
        # Distinct shape (squares) already differentiates librarian tier from
        # brother tier; rank emoji on the styled name conveys "Librarian" status.
        n_icon = WARP_LIBRARIAN_TIER_ICON.get(nt, "🟩")
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
    parent_last: bool = True,
    prefix: str = "",
) -> None:
    """Recursively render downstream contagion tree with box-drawing characters.

    Uses ├─, └─, │ to create visually distinct tree structure that survives
    Discord embed rendering. ``visited`` is mutated to track all reached uids;
    ``lines_out`` collects rendered lines.
    """
    if depth >= max_depth:
        return
    children = _compute_outgoing_infections(int(node_uid), states=data, window_hours=24)
    children = [c for c in children if c not in visited]
    
    for idx, child in enumerate(children):
        visited.add(child)
        is_last = (idx == len(children) - 1)
        connector = "└─ " if is_last else "├─ "
        lines_out.append(f"{prefix}{connector}{_warp_node_label(guild, data, child)}")
        
        if depth + 1 < max_depth:
            child_prefix = prefix + ("   " if is_last else "│  ")
            _warp_render_subtree(
                guild, data, child, depth + 1, visited, lines_out,
                max_depth, is_last, child_prefix
            )


# ---------------------------------------------------------------------------
# Persistence — warp_exposure.json
# ---------------------------------------------------------------------------

def _default_exposure_state() -> dict:
    return {
        # Susceptibility points (was: "exposure points"). Accumulates from BL
        # missions, contagion, and scrying. Drives the infection-roll probability
        # band. Only resets on cleanse (mirrors armor points_since_blessing).
        # May be negative on a crit_success cleanse (grace susceptibility).
        "points": 0,
        # Cached susceptibility band label for display. Recomputed on read.
        # Mechanical decisions use ``infection_state`` instead.
        "exposure_tier": None,
        # NEW: discrete infection state (mirrors armor damage_tier).
        # None | "tainted" | "exposed" | "volatile". Set by infection rolls,
        # cleared by cleanse. Escalate-only on re-rolls.
        "infection_state": None,
        # Warp corruption flag (mirrors armor spirit_fractured) — set when an
        # infection roll while at "volatile" escalates further, or on a cleanse
        # crit_fail at "volatile". Permanent until cleared by a successful cleanse.
        "warp_corrupted": False,
        "last_detection_alert_tier": None,
        "spread_history": [],  # list of {"source_id": str, "ts": iso}
        "last_warding_timestamp": None,
        # Librarian fields
        "is_librarian": False,
        "librarian_tier": None,
        "last_decay_check": None,
    }


def _migrate_exposure_record(state: dict) -> dict:
    """One-shot migration from legacy 5-tier schema to 3-tier+flag schema.

    - Maps legacy ``exposure_tier`` ("breached"/"catastrophic") to
      ``infection_state="volatile"`` + ``warp_corrupted=True``.
    - Maps ``volatile``/``exposed``/``tainted`` exposure_tier values to the
      equivalent infection_state.
    - Drops obsolete fields (``immunity_until``, ``restricted_aar_count``).
    - Honours legacy ``warp_corrupted`` and ``restricted_aar_count >= 3``.
    """
    base = _default_exposure_state()
    for k, v in base.items():
        state.setdefault(k, v)
    # Migrate legacy exposure_tier -> infection_state (idempotent — only sets
    # infection_state if it's still None).
    if state.get("infection_state") is None:
        legacy = state.get("exposure_tier")
        if legacy in ("breached", "catastrophic"):
            state["infection_state"] = "volatile"
            state["warp_corrupted"] = True
        elif legacy in ("volatile", "exposed", "tainted"):
            state["infection_state"] = legacy
    # Promote any record with restricted_aar_count >= threshold to corrupted.
    try:
        legacy_count = int(state.get("restricted_aar_count", 0) or 0)
    except Exception:
        legacy_count = 0
    threshold = _cfg_int("warp_corruption_threshold", DEFAULT_WARP_CORRUPTION_THRESHOLD)
    if legacy_count >= int(threshold):
        state["warp_corrupted"] = True
    # Strip obsolete fields.
    state.pop("immunity_until", None)
    state.pop("restricted_aar_count", None)
    return state


def _load_warp_exposure() -> dict:
    try:
        if not os.path.exists(WARP_EXPOSURE_PATH):
            return {}
        with open(WARP_EXPOSURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        # Apply migration lazily on every read so older records normalize as
        # they're touched. The first save after load will persist the changes.
        for uid, rec in list(data.items()):
            if isinstance(rec, dict):
                data[uid] = _migrate_exposure_record(rec)
        return data
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

def _roll_warp_penalty(tier: Optional[str], warp_corrupted: bool = False) -> int:
    """Roll a probabilistic AAR penalty by infection_state (config-driven).

    When ``warp_corrupted`` is True the corrupted distribution is used regardless
    of the current infection_state — mirrors armor's spirit_fractured penalty.
    """
    if warp_corrupted:
        probs = WARP_PENALTY_PROBABILITIES_CORRUPTED
    else:
        table = _get_penalty_probabilities()
        probs = table.get(tier, {0: 1.0})
    roll = random.random()
    cumulative = 0.0
    for penalty, prob in sorted(probs.items()):
        cumulative += prob
        if roll < cumulative:
            return int(penalty)
    return 0


# ---------------------------------------------------------------------------
# Infection-roll helpers (exact mirror of forge_ops._roll_damage_tier)
# ---------------------------------------------------------------------------

def _get_infection_probability_tiers() -> list:
    """Return the configured infection probability ladder.

    Falls back to ``WARP_INFECTION_PROBABILITY_TIERS`` when config missing.
    """
    cfg = _warp_config()
    tiers = cfg.get("infection_probability_tiers")
    if not tiers:
        return list(WARP_INFECTION_PROBABILITY_TIERS)
    return list(tiers)


def _get_infection_probability_tier_for_points(points: int) -> Optional[dict]:
    """Return the probability-tier entry whose [min,max] band contains ``points``."""
    for entry in _get_infection_probability_tiers():
        try:
            lo = int(entry.get("min", 0))
            hi = entry.get("max")
            if hi is None:
                if points >= lo:
                    return entry
            elif lo <= points <= int(hi):
                return entry
        except Exception:
            continue
    return None


def _get_infection_probability(points: int) -> float:
    """Probability that an infection roll succeeds at the given susceptibility."""
    tier = _get_infection_probability_tier_for_points(points)
    if not tier:
        return 0.0
    try:
        return float(tier.get("chance", 0.0))
    except Exception:
        return 0.0


def _roll_infection_tier(susceptibility: int) -> Optional[str]:
    """Roll an infection event at the given susceptibility.

    Returns one of "tainted"/"exposed"/"volatile", or None if no infection.
    Two-step roll (gate by ``chance``, then weighted pick from
    ``infection_weights``) — exact mirror of forge_ops._roll_damage_tier.
    """
    if int(susceptibility or 0) <= 0:
        return None
    tier = _get_infection_probability_tier_for_points(int(susceptibility or 0))
    if not tier:
        return None
    try:
        chance = float(tier.get("chance", 0.0))
    except Exception:
        chance = 0.0
    if chance <= 0 or random.random() >= chance:
        return None
    weights = tier.get("infection_weights") or {}
    candidates: List[str] = []
    weight_list: List[float] = []
    for state in WARP_INFECTION_TIERS:
        try:
            w = float(weights.get(state, 0))
        except Exception:
            w = 0.0
        if w > 0:
            candidates.append(state)
            weight_list.append(w)
    if not candidates:
        return "tainted"
    total = sum(weight_list)
    roll = random.uniform(0, total)
    cumulative = 0.0
    for state, w in zip(candidates, weight_list):
        cumulative += w
        if roll <= cumulative:
            return state
    return candidates[-1]


def _escalate_infection(current: Optional[str], rolled: Optional[str]) -> Tuple[Optional[str], bool]:
    """Return (new_state, became_corrupted).

    Escalate-only — a rolled tier lower than current does NOT downgrade.
    Rolling above the top tier ("volatile") sets the warp_corrupted flag.
    """
    order = [None, "tainted", "exposed", "volatile"]
    try:
        cur_i = order.index(current)
    except ValueError:
        cur_i = 0
    try:
        new_i = order.index(rolled) if rolled is not None else 0
    except ValueError:
        new_i = 0
    # Re-roll while already at volatile that rolls volatile again → corrupted.
    if cur_i >= len(order) - 1 and rolled == "volatile":
        return current, True
    if new_i > cur_i:
        return order[new_i], False
    return current, False


# ---------------------------------------------------------------------------
# Cleanse outcome roll (mirror of forge_ops._roll_blessing_outcome)
# ---------------------------------------------------------------------------

def _get_cleanse_outcome_probabilities() -> Dict[str, Dict[str, float]]:
    cfg = _warp_config()
    raw = cfg.get("cleanse_outcome_probabilities") or {}
    if not raw:
        return dict(WARP_CLEANSE_OUTCOME_PROBABILITIES)
    out: Dict[str, Dict[str, float]] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            try:
                out[str(k)] = {
                    "crit_fail": float(v.get("crit_fail", 0.0)),
                    "crit_success": float(v.get("crit_success", 0.0)),
                }
            except Exception:
                continue
    return out or dict(WARP_CLEANSE_OUTCOME_PROBABILITIES)


def _roll_cleanse_outcome_v2(
    infection_state: Optional[str], warp_corrupted: bool = False
) -> str:
    """Roll cleanse outcome: 'crit_fail' / 'normal' / 'crit_success'.

    Probabilities keyed by recipient state (corrupted overrides infection_state).
    Exact mirror of forge_ops._roll_blessing_outcome.
    """
    key = "corrupted" if warp_corrupted else (infection_state or "clean")
    table = _get_cleanse_outcome_probabilities()
    entry = table.get(key) or table.get("clean", {"crit_fail": 0.01, "crit_success": 0.01})
    try:
        crit_fail = float(entry.get("crit_fail", 0.0))
        crit_success = float(entry.get("crit_success", 0.0))
    except Exception:
        crit_fail, crit_success = 0.0, 0.0
    roll = random.random()
    if roll < crit_fail:
        return "crit_fail"
    if roll >= (1.0 - crit_success):
        return "crit_success"
    return "normal"


def _get_warp_tier_risk_display(tier: Optional[str], warp_corrupted: bool = False) -> str:
    if not tier:
        if not warp_corrupted:
            return "No risk"
    if warp_corrupted:
        probs = WARP_PENALTY_PROBABILITIES_CORRUPTED
    else:
        probs = WARP_PENALTY_PROBABILITIES.get(tier, {0: 1.0})
    if not probs:
        return "No risk"
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


def _get_warp_detection_chances() -> Dict[str, float]:
    """Per-infection-state early-warning detection chance.

    Reuses ``spread_chances_by_tier`` from config (a brother more likely to
    spread is also more likely to trigger a detection alert) and falls back
    to ``WARP_DETECTION_CHANCES``.
    """
    cfg = _warp_config()
    raw = cfg.get("spread_chances_by_tier") or {}
    if not raw:
        return dict(WARP_DETECTION_CHANCES)
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except Exception:
            continue
    return out or dict(WARP_DETECTION_CHANCES)


def _roll_warp_detection_alert(tier: Optional[str]) -> bool:
    """Roll whether to post an early warning alert for this exposure tier."""
    if not tier:
        return False
    chances = _get_warp_detection_chances()
    chance = float(chances.get(tier, 0.0))
    if chance <= 0:
        return False
    return random.random() < chance


def _get_librarian_ping(guild: Optional[discord.Guild]) -> str:
    """Return role mention for Librarian notifications."""
    try:
        cfg = _warp_config()
        raw = cfg.get("librarian_role_id")
        if raw:
            return f"<@&{int(raw)}>"
    except Exception:
        pass
    if guild is not None:
        try:
            role = discord.utils.get(guild.roles, name=LIBRARIAN_ROLE_NAME)
            if role:
                return role.mention
        except Exception:
            pass
    return f"@{LIBRARIAN_ROLE_NAME}"


async def _post_warp_alert(
    member: discord.Member,
    tier: Optional[str],
    guild: Optional[discord.Guild] = None,
    op_mission: Optional[str] = None,
    op_difficulty_class: Optional[str] = None,
    op_url: Optional[str] = None,
    squad_member_ids: Optional[List[str]] = None,
    alert_type: str = "sustained",
    penalty_amount: int = 0,
    points: int = 0,
    warp_corrupted: bool = False,
):
    """Post Librarium alert for sustained loss, detection warning, or corruption."""
    channel_id = _get_librarium_watch_channel_id()
    if not channel_id:
        return

    guild = guild or member.guild
    if not guild:
        return

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await _g.bot.fetch_channel(int(channel_id))
        except Exception:
            return
    if channel is None:
        return

    is_detection = alert_type == "detected"
    is_corrupted = alert_type == "corrupted"
    penalty_str = f" (-{penalty_amount} AAR)" if (penalty_amount > 0 and not is_detection) else ""

    if is_corrupted:
        color = 0x8B0000
        title = "᛭⋅ WARP CORRUPTION MANIFEST ⋅᛭"
        description = "*The wardline has failed — immediate Librarian intervention required*"
    elif tier == "volatile":
        if is_detection:
            color = 0xF39C12
            title = "᛭⋅ WARP INSTABILITY DETECTED ⋅᛭"
            description = "*Escalation risk detected — cleansing window open*"
        else:
            color = 0xE67E22
            title = f"᛭⋅ WARP SANCTION BREACH ⋅᛭{penalty_str}"
            description = "*AAR points lost due to warp instability*"
    else:
        if is_detection:
            color = 0xF1C40F
            title = "᛭⋅ TAINT DETECTED ⋅᛭"
            description = "*Early contamination detected — preventive cleansing advised*"
        else:
            color = 0xE67E22
            title = f"᛭⋅ WARP SANCTION ALERT ⋅᛭{penalty_str}"
            description = "*AAR points lost due to warp taint*"

    embed = discord.Embed(title=title, description=description, color=color)

    try:
        styled = (
            _b("_format_member_styled")(guild, str(member.id), include_chapter=True)
            if _b("_format_member_styled")
            else _strip_display_name(member.display_name)
        )
    except Exception:
        styled = _strip_display_name(member.display_name)

    sanction_key = _warp_sanction_key_for_state(tier, warp_corrupted)
    # Display fallback uses "Cleansed" (the post-rename clean label).
    sanction_label, _sanction_desc = WARP_SANCTION_STATUS.get(sanction_key, ("Cleansed", ""))
    flags = WARP_CORRUPTED_ICON if warp_corrupted else ""
    flag_str = f" {flags}" if flags else ""
    risk = _get_warp_tier_risk_display(tier, warp_corrupted)
    if warp_corrupted:
        tier_label = "Corrupted"
    else:
        tier_label = tier.title() if tier else "Clear"
    if is_detection and tier:
        tier_label += " (Early Warning)"

    embed.add_field(
        name="▸ Affected Brother",
        value=(
            f"{styled}\n"
            f"**Exposure Tier:** {tier_label}{flag_str}\n"
            f"**Warp Sanction:** {sanction_label}\n"
            f"**Penalty Risk:** {risk}"
        ),
        inline=False,
    )

    if op_mission or op_difficulty_class or op_url or squad_member_ids:
        debrief_lines = []
        planet = None
        clean_mission = None
        if op_mission:
            clean_mission = re.sub(r"\s*<@[!&]?\d+>.*$", "", op_mission).strip()
            if "@" in clean_mission:
                clean_mission = clean_mission.split("@")[0].strip()
        if op_difficulty_class:
            planet = MISSION_TO_PLANET.get(op_difficulty_class.lower().strip())
        if not planet and clean_mission:
            planet = MISSION_TO_PLANET.get(clean_mission.lower().strip())
        if planet:
            debrief_lines.append(f"Warp signatures spiked during deployment to **{planet}**")
        elif clean_mission:
            debrief_lines.append(f"Warp signatures spiked during **{clean_mission}** deployment")

        if squad_member_ids and guild:
            squad_names = []
            for sid in squad_member_ids:
                if str(sid) == str(member.id):
                    continue
                try:
                    squad_member = guild.get_member(int(sid))
                except Exception:
                    squad_member = None
                if squad_member:
                    squad_names.append(_strip_display_name(squad_member.display_name))
            if squad_names:
                debrief_lines.append(f"Kill Team: {', '.join(squad_names)}")

        if op_url:
            debrief_lines.append(f"[View After Action Report]({op_url})")

        if debrief_lines:
            embed.add_field(name="▸ Debrief", value="\n".join(debrief_lines), inline=False)

    if is_corrupted:
        guidance = "Administer intensive cleansing via `/warp_cleanse intensive:True` and escalate to Void Warden."
        field_name = "▸ Immediate Librarian Response Required"
    elif is_detection:
        guidance = "Taint detected before AAR loss. Administer `/warp_cleanse` to prevent penalties."
        field_name = "▸ Preventive Cleansing Available"
    else:
        guidance = "AAR loss confirmed. Administer `/warp_cleanse` to restore cleansed status."
        field_name = "▸ Librarian Response Required"
    embed.add_field(name=field_name, value=guidance, inline=False)

    content = _get_librarian_ping(guild)
    await channel.send(content=content, embed=embed)


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
    """Deprecated. Returns False — the immunity-window model has been replaced
    by negative grace susceptibility on cleanse crit_success.
    """
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
    *,
    infection_state: Any = _SANCTION_STATE_UNSET,
    warp_corrupted: Optional[bool] = None,
) -> None:
    """Ensure the member's sanction role matches their current infection state.

    The new schema keys roles off ``infection_state`` (and the ``warp_corrupted``
    flag) instead of raw susceptibility points. ``new_points`` is retained for
    backward-compat with older call sites that only know the legacy points.
    """
    if is_librarian:
        # Librarians don't get sanction roles (their burden is private)
        await _clear_sanction_roles(member, guild)
        return
    # Prefer the new state-driven mapping when callers pass it through.
    if infection_state is not _SANCTION_STATE_UNSET or warp_corrupted is not None:
        resolved_state = None if infection_state is _SANCTION_STATE_UNSET else infection_state
        is_corrupted = bool(warp_corrupted)
        new_key = _warp_sanction_key_for_state(resolved_state, is_corrupted)
        if new_key == "sanctioned":
            await _clear_sanction_roles(member, guild)
            return
        await _apply_sanction_role(member, guild, new_key)
        return
    # Legacy fallback: derive from points (only used by older display paths).
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


def _normalize_cleanse_outcome_key(outcome: Optional[str]) -> str:
    """Normalize legacy/new cleanse outcome keys for dashboard aggregation."""
    key = str(outcome or "").strip().lower()
    mapping = {
        "full": "normal",
        "partial": "normal",
        "backlash": "crit_fail",
    }
    return mapping.get(key, key)


def _is_backlash_outcome(outcome: Optional[str]) -> bool:
    return _normalize_cleanse_outcome_key(outcome) == "crit_fail"


def _is_full_cleanse_outcome(outcome: Optional[str]) -> bool:
    return _normalize_cleanse_outcome_key(outcome) in {"normal", "crit_success"}


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
    """Stamp the caller's last-scry timestamp, append to a bounded log, and
    apply the scry susceptibility tax to the caller.

    Per the spec: scrying is a divinatory act that brushes the caller against
    the warp — they gain ``scry_susceptibility_gain`` susceptibility and roll
    for infection at the new total (escalate-only).
    """
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

    # Apply susceptibility + infection roll on the caller.
    try:
        gain = _cfg_int("scry_susceptibility_gain", WARP_SCRY_SUSCEPTIBILITY_GAIN)
        if gain != 0:
            async with _g.WARP_EXPOSURE_LOCK:
                exposure = _load_warp_exposure()
                cstate = dict(exposure.get(str(caller_id), _default_exposure_state()))
                for k, v in _default_exposure_state().items():
                    cstate.setdefault(k, v)
                cstate["is_librarian"] = True
                cstate["points"] = int(cstate.get("points", 0) or 0) + int(gain)
                # Librarians soak their burden via decay — they still gain pts
                # but don't roll for infection_state (their minds are warded).
                if not cstate.get("is_librarian"):
                    rolled = _roll_infection_tier(int(cstate["points"]))
                    if rolled is not None:
                        new_state, became_corrupted = _escalate_infection(
                            cstate.get("infection_state"), rolled
                        )
                        cstate["infection_state"] = new_state
                        if became_corrupted:
                            cstate["warp_corrupted"] = True
                exposure[str(caller_id)] = cstate
                _save_warp_exposure(exposure)
    except Exception:
        pass


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

    # Enumerate all active guild Librarians/Void Wardens up front so we count
    # charges and tier population correctly even when no warp_exposure record
    # exists yet (new librarians, never been in a cleanse/squad with corruption).
    if guild is not None:
        is_active_fn = _b("_is_active_participant")
        for member in guild.members:
            if member.bot:
                continue
            role_names = {r.name for r in member.roles}
            if not (LIBRARIAN_ROLE_NAME in role_names or VOID_WARDEN_ROLE_NAME in role_names):
                continue
            if is_active_fn and not is_active_fn(member):
                continue
            librarian_user_ids.append(int(member.id))

    seen_lib_ids = set(librarian_user_ids)
    for uid, raw in data.items():
        pts = int((raw or {}).get("points", 0) or 0)
        if (raw or {}).get("is_librarian"):
            lt = _librarian_tier_for_points(pts)
            librarian_tier_counts[lt] = librarian_tier_counts.get(lt, 0) + 1
            try:
                if int(uid) not in seen_lib_ids:
                    librarian_user_ids.append(int(uid))
                    seen_lib_ids.add(int(uid))
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

    # Librarians with no exposure record yet count as Stable (None tier = 0c).
    accounted_lib_count = sum(librarian_tier_counts.values())
    untracked_libs = max(0, len(librarian_user_ids) - accounted_lib_count)
    if untracked_libs:
        librarian_tier_counts[None] = librarian_tier_counts.get(None, 0) + untracked_libs

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
        f"**▸ Warp Telemetry**\n"
        f"{sanctioned_icon} **{clean_pct:.0f}%** Cleansed  "
        f"{pressure_icon} **{pressure_str}** Pressure  "
        f"{charges_icon} **{total_librarian_charges}** Charges"
    )

    # ─── Watchlist (warp-exposed brothers — mirrors forge watchlist)
    watchlist_entries = []  # (severity_idx, pts, uid, raw)
    severity_order = {
        "corrupted": 0, "volatile": 1, "exposed": 2, "tainted": 3, None: 4,
    }
    if guild is not None:
        for uid, raw in data.items():
            if (raw or {}).get("is_librarian"):
                continue
            inf = (raw or {}).get("infection_state")
            is_corrupted = bool((raw or {}).get("warp_corrupted"))
            if not inf and not is_corrupted:
                continue
            try:
                member = guild.get_member(int(uid))
            except Exception:
                member = None
            if member is None:
                continue
            pts = int((raw or {}).get("points", 0) or 0)
            severity_key = "corrupted" if is_corrupted else inf
            watchlist_entries.append((severity_order.get(severity_key, 5), -pts, uid, raw, member, inf))
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
        watch_lines.append("*The wards hold. No tainted brothers.*")
    embed.add_field(name="▸ Watchlist", value="\n".join(watch_lines), inline=False)

    # ─── Recent Rites (last 5 cleanses — mirrors forge Recent Rites)
    # Icons chosen to be thematically distinct from severity-tier circles
    # used in Watchlist/Key (🟡=tainted), avoiding ambiguity.
    outcome_display = {
        "normal": ("🧿", "Cleansed"),
        "crit_success": ("✨", "Crit Success"),
        "crit_fail": ("⚠️", "Backlash"),
    }
    recent_lines = []
    for entry in reversed(cleanse_history[-5:]):
        outcome = _normalize_cleanse_outcome_key(entry.get("outcome", "?"))
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

    # ─── Librarian Custodians removed in v2.5.1: the Epistolaries panel
    # already ranks active librarians by (charges, success rate, total
    # cleanses), so re-ranking the same data under opaque honorifics was
    # redundant. Stats are still recorded to `librarian_stats` and surfaced
    # via the Epistolaries panel + Recent Rites.

    # ─── Breach Memorial (recent backlash events last 28d — mirrors forge Spirit Memorial)
    memorial_lines = []
    cutoff = datetime.utcnow() - timedelta(days=28)
    backlashes = []
    for entry in cleanse_history:
        if not _is_backlash_outcome(entry.get("outcome")):
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

    # ─── Ward Highlights (curated 2-3 line digest — no Forge analog; librarium-
    # specific facets distilled from cleanse_history + librarian_stats so the
    # eye lands on something other than recency.)
    highlight_lines: List[str] = []
    # Vigil: librarian with the longest *current* consecutive non-backlash streak.
    try:
        streaks: Dict[str, int] = {}
        # Per-librarian, walk newest → oldest; count until first backlash.
        per_lib: Dict[str, List[dict]] = {}
        for entry in cleanse_history:
            lib_id = str(entry.get("librarian_id") or "")
            if not lib_id:
                continue
            per_lib.setdefault(lib_id, []).append(entry)
        for lib_id, entries in per_lib.items():
            # Newest first
            try:
                entries_sorted = sorted(
                    entries,
                    key=lambda e: e.get("ts", ""),
                    reverse=True,
                )
            except Exception:
                entries_sorted = list(reversed(entries))
            streak = 0
            for e in entries_sorted:
                if _is_backlash_outcome(e.get("outcome")):
                    break
                streak += 1
            streaks[lib_id] = streak
        if streaks:
            best_lib, best_streak = max(streaks.items(), key=lambda kv: kv[1])
            if best_streak >= 3:
                try:
                    name = _b("_format_member_styled")(guild, best_lib, include_chapter=True) \
                        if (guild and _b("_format_member_styled")) else f"<@{best_lib}>"
                except Exception:
                    name = f"<@{best_lib}>"
                highlight_lines.append(
                    f"🛡️ **Vigil** _(longest clean streak)_: {name} · "
                    f"{best_streak} rites without backlash"
                )
    except Exception:
        pass
    # Purge: largest single-rite removed value in last 28d.
    try:
        purge_cutoff = datetime.utcnow() - timedelta(days=28)
        best_purge = None  # (removed, entry, ts)
        for entry in cleanse_history:
            try:
                ts = datetime.fromisoformat(entry.get("ts", ""))
            except Exception:
                continue
            if ts < purge_cutoff:
                continue
            removed = int(entry.get("removed", 0) or 0)
            if removed <= 0:
                continue
            if best_purge is None or removed > best_purge[0]:
                best_purge = (removed, entry, ts)
        if best_purge:
            removed, entry, ts = best_purge
            age_days = max(0, (datetime.utcnow() - ts).days)
            try:
                lname = _b("_format_member_styled")(
                    guild, str(entry.get("librarian_id")), include_chapter=True
                ) if (guild and _b("_format_member_styled")) else f"<@{entry.get('librarian_id')}>"
            except Exception:
                lname = f"<@{entry.get('librarian_id')}>"
            highlight_lines.append(
                f"⚔️ **Purge** _(biggest single rite, 28d)_: {lname} · "
                f"{removed}c removed ({age_days}d ago)"
            )
    except Exception:
        pass
    # First Light: most-recent successful cleanse.
    try:
        latest_full = None
        for entry in reversed(cleanse_history):
            if not _is_full_cleanse_outcome(entry.get("outcome")):
                continue
            try:
                ts = datetime.fromisoformat(entry.get("ts", ""))
            except Exception:
                continue
            latest_full = (ts, entry)
            break
        if latest_full:
            ts, entry = latest_full
            age_days = max(0, (datetime.utcnow() - ts).days)
            age_str = "today" if age_days == 0 else f"{age_days}d ago"
            try:
                bname = _b("_format_member_styled")(
                    guild, str(entry.get("bearer_id")), include_chapter=True
                ) if (guild and _b("_format_member_styled")) else f"<@{entry.get('bearer_id')}>"
            except Exception:
                bname = f"<@{entry.get('bearer_id')}>"
            highlight_lines.append(
                f"✨ **First Light** _(latest successful cleanse)_: {bname} · {age_str}"
            )
    except Exception:
        pass
    if highlight_lines:
        embed.add_field(
            name="▸ Ward Highlights",
            value="\n".join(highlight_lines[:3]),
            inline=False,
        )

    # ─── Contagion Watch (no Forge analog — surfaces active spread topology
    # rather than per-brother severity. Three quick metrics: spreaders, longest
    # active chain, and brothers already in the high-tier band.)
    contagion_lines: List[str] = []
    try:
        # 1) Active super-spreaders count (already computed at top of builder).
        if super_spreader_count > 0:
            contagion_lines.append(
                f"🕸️ **Spreaders active**: {super_spreader_count} · "
                f"threshold ≥{_cfg_int('super_spreader_threshold', 3)} infections / 24h"
            )

        # 2) Largest active downstream chain (BFS over spread edges).
        def _full_downstream(root_uid: str, states: dict) -> int:
            visited = {root_uid}
            stack = [root_uid]
            while stack:
                cur = stack.pop()
                try:
                    children = _compute_outgoing_infections(
                        int(cur), states=states, window_hours=24
                    )
                except Exception:
                    children = []
                for tgt in children:
                    if tgt not in visited:
                        visited.add(tgt)
                        stack.append(tgt)
            return len(visited) - 1

        biggest = None  # (count, uid)
        for uid, raw in data.items():
            if (raw or {}).get("is_librarian"):
                continue
            try:
                count = _full_downstream(str(uid), data)
            except Exception:
                count = 0
            if count <= 0:
                continue
            if biggest is None or count > biggest[0]:
                biggest = (count, str(uid))
        if biggest:
            count, root_uid = biggest
            try:
                rname = _b("_format_member_styled")(guild, root_uid, include_chapter=True) \
                    if (guild and _b("_format_member_styled")) else f"<@{root_uid}>"
            except Exception:
                rname = f"<@{root_uid}>"
            contagion_lines.append(
                f"🌳 **Largest chain**: {rname} → +{count} downstream"
            )

        # 3) Brothers already in the high-severity band (volatile) —
        # distinct from the sanction watchlist which uses
        # different thresholds.
        high_tier_count = 0
        for uid, raw in data.items():
            if (raw or {}).get("is_librarian"):
                continue
            pts = int((raw or {}).get("points", 0) or 0)
            if pts <= 0:
                continue
            inf = (raw or {}).get("infection_state")
            is_corrupted = bool((raw or {}).get("warp_corrupted"))
            if inf == "volatile" or is_corrupted:
                high_tier_count += 1
        if high_tier_count > 0:
            contagion_lines.append(
                f"🚨 **In Volatile+**: {high_tier_count} brother"
                f"{'s' if high_tier_count != 1 else ''} above the cleanse-priority line"
            )
    except Exception:
        pass
    if contagion_lines:
        embed.add_field(
            name="▸ Contagion Watch",
            value="\n".join(contagion_lines[:3]),
            inline=False,
        )

    # ─── Warp Reservoir + Epistolaries (inline pair — mirrors forge Reserves+Artificers)
    # Reservoir: aggregate charge capacity across all eligible librarians
    # (excluding overloaded/abyssal, who cannot cleanse) plus a 7-day net
    # tempo: rites completed vs backlashes.
    max_reservoir = 0
    pool_max = _cfg_int("warding_pool_max", WARDING_POOL_MAX)
    for lib_id in librarian_user_ids:
        lib_state = data.get(str(lib_id)) or {}
        lib_tier = _librarian_tier_for_points(int(lib_state.get("points", 0) or 0))
        if lib_tier in ("overloaded", "abyssal"):
            continue
        max_reservoir += pool_max
    available_reservoir = int(total_librarian_charges)
    reservoir_pct = (available_reservoir / max_reservoir * 100) if max_reservoir > 0 else 0
    filled_blocks = int(reservoir_pct / 10)
    empty_blocks = 10 - filled_blocks
    reservoir_bar = "█" * filled_blocks + "░" * empty_blocks

    week_cutoff = datetime.utcnow() - timedelta(days=7)
    weekly_rites = 0
    weekly_backlashes = 0
    for entry in cleanse_history:
        try:
            ts = datetime.fromisoformat(entry.get("ts", ""))
        except Exception:
            continue
        if ts < week_cutoff:
            continue
        weekly_rites += 1
        if _is_backlash_outcome(entry.get("outcome")):
            weekly_backlashes += 1
    weekly_net = weekly_rites - weekly_backlashes
    if weekly_net > 0:
        net_icon = "📈"
        net_text = f"+{weekly_net}"
    elif weekly_net < 0:
        net_icon = "📉"
        net_text = str(weekly_net)
    else:
        net_icon = "➡️"
        net_text = "0"

    reservoir_value = (
        f"{reservoir_bar} {available_reservoir} / {max_reservoir} charges\n"
        f"📊 7d: +{weekly_rites} rites | -{weekly_backlashes} backlash | {net_icon} {net_text} net"
    )
    embed.add_field(
        name="▸ Warp Reservoir",
        value=reservoir_value,
        inline=True,
    )

    # ─── Librarians (mirrors forge "Artificers" — top 3 by charges/success/rites)
    librarian_lines: List[str] = []
    # Score every active guild librarian, even those without a stats record yet,
    # so a freshly-promoted Librarian still appears with their charge count.
    candidates: List[tuple] = []  # (charges, success_rate, total, lib_id)
    for lib_id in librarian_user_ids:
        # Suppress overloaded/abyssal librarians (cannot cleanse — same gate as supply).
        lib_state = data.get(str(lib_id)) or {}
        lib_tier = _librarian_tier_for_points(int(lib_state.get("points", 0) or 0))
        if lib_tier in ("overloaded", "abyssal"):
            continue
        try:
            charges = await _get_librarian_available_charges(int(lib_id))
        except Exception:
            charges = 0
        stats = librarian_stats.get(str(lib_id), {}) or {}
        total = int(stats.get("total_cleanses", 0) or 0)
        successes = int(stats.get("successes", 0) or 0)
        success_rate = (successes / total) * 100 if total > 0 else 0.0
        candidates.append((int(charges), success_rate, total, int(lib_id)))
    candidates.sort(reverse=True)
    for charges, _rate, _total, lib_id in candidates[:3]:
        try:
            name = _b("_format_member_styled")(guild, str(lib_id), include_chapter=True) \
                if (guild and _b("_format_member_styled")) else f"<@{lib_id}>"
        except Exception:
            name = f"<@{lib_id}>"
        librarian_lines.append(f"{name} ({charges})")
    embed.add_field(
        name="▸ Epistolaries",
        value="\n".join(librarian_lines) if librarian_lines else "*No active librarians.*",
        inline=True,
    )

    # ─── Key (bottom — mirrors forge legend)
    embed.add_field(
        name="▸ Key",
        value=(
            f"🟡 Tainted · 🟠 Exposed · 🔴 Volatile · {WARP_CORRUPTED_ICON} Corrupted\n"
            f"{WARP_SPREADER_ICON} Super-spreader · "
            "Epistolaries `(N)` = available charges\n"
            "Rites: ✨ Crit Success · 🧿 Cleansed · ⚠️ Backlash"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Last updated • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    return embed


class LogToLibrariumView(discord.ui.View):
    """View with a 'Log to Librarium' button for cleanse/scry attestations.

    When clicked, posts the embed publicly to the Librarium watch channel
    and (for cleanses) triggers a Chronicle repost at the bottom. Mirrors
    the Forge's LogToForgeView pattern.
    """

    def __init__(
        self,
        embed: discord.Embed,
        bearer_mention: Optional[str] = None,
        repost_chronicle: bool = True,
    ):
        super().__init__(timeout=300)  # 5 minute timeout
        self.embed = embed
        self.bearer_mention = bearer_mention
        self.repost_chronicle = repost_chronicle
        self.logged = False

    @discord.ui.button(
        label="Log to Librarium",
        style=discord.ButtonStyle.primary,
        emoji="🧿",
        custom_id="log_to_librarium",
    )
    async def log_to_librarium(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.logged:
            await interaction.response.send_message("Already logged to Librarium.", ephemeral=True)
            return

        self.logged = True
        button.disabled = True
        button.label = "Logged"
        button.style = discord.ButtonStyle.secondary

        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        channel_id = _get_librarium_watch_channel_id()
        if not channel_id or not interaction.guild:
            return
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            return

        try:
            kwargs = {"embed": self.embed}
            if self.bearer_mention:
                kwargs["content"] = self.bearer_mention
                kwargs["allowed_mentions"] = discord.AllowedMentions(users=True)
            await channel.send(**kwargs)
        except Exception as e:
            _g.logger.warning(f"Failed to log to Librarium: {e}")
            return

        if self.repost_chronicle:
            try:
                await _repost_librarium_chronicle_at_bottom(interaction.guild)
            except Exception as e:
                _g.logger.debug(f"Chronicle repost after log failed: {e}")


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

    # Per-brother AAR loss from warp penalties (rolled pre-save in aar_ops).
    warp_penalties = record.get("warp_penalties") or {}
    if not isinstance(warp_penalties, dict):
        warp_penalties = {}

    bl_gain = _bl_gain_for_record(record)
    alerts_to_post: Dict[str, dict] = {}

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()

            # Hydrate states for all squad members
            states: Dict[str, dict] = {}
            prior_corrupted: Dict[str, bool] = {}
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
                prior_corrupted[str(bid)] = bool(state.get("warp_corrupted"))

            now_iso = datetime.utcnow().isoformat()

            # 1) Direct BL susceptibility gain — all squadmates on a BL mission.
            if bl_gain > 0:
                for bid, state in states.items():
                    if state.get("is_librarian"):
                        # Librarians soak their burden through self-cleansing; the
                        # BL gain still applies to feed their decay ladder.
                        state["points"] = int(state.get("points", 0) or 0) + bl_gain
                    else:
                        state["points"] = int(state.get("points", 0) or 0) + bl_gain

            # 1b) Infection roll on the new susceptibility — escalate-only.
            #     Librarians and already-corrupted brothers do not roll for new
            #     infection_state changes (corrupted is the terminal flag and
            #     escalation rolls cannot raise it further; cleanse is the only
            #     exit). However, rolling at "volatile" can still set the
            #     corruption flag, so we keep those in the pool.
            if bl_gain > 0:
                for bid, state in states.items():
                    if state.get("is_librarian"):
                        continue
                    pts = int(state.get("points", 0) or 0)
                    rolled = _roll_infection_tier(pts)
                    if rolled is None:
                        continue
                    new_state, became_corrupted = _escalate_infection(
                        state.get("infection_state"), rolled
                    )
                    state["infection_state"] = new_state
                    if became_corrupted:
                        state["warp_corrupted"] = True

            # 2) Contagion spread — sources are squadmates whose infection_state
            # is non-None after step 1b. Spread gives the target +1 susceptibility
            # and triggers an infection roll at the new susceptibility.
            sources: List[Tuple[str, str]] = []  # (source_id, source_infection_state)
            for bid, state in states.items():
                if state.get("is_librarian"):
                    continue
                inf = state.get("infection_state")
                if inf:
                    sources.append((bid, str(inf)))

            if sources:
                spread_chances = _get_spread_chances()
                spread_cap = _cfg_int("spread_daily_unique_source_cap", WARP_SPREAD_DAILY_UNIQUE_SOURCE_CAP)
                spread_gain = _get_spread_susceptibility_gain()
                for tgt_id, tgt_state in states.items():
                    if tgt_state.get("is_librarian"):
                        continue
                    history = _prune_spread_history(tgt_state.get("spread_history") or [])
                    unique_today = {h.get("source_id") for h in history}
                    for src_id, src_inf in sources:
                        if src_id == tgt_id:
                            continue
                        if src_id in unique_today:
                            continue
                        if len(unique_today) >= spread_cap:
                            continue
                        chance = spread_chances.get(src_inf, 0.0)
                        if random.random() < chance:
                            tgt_state["points"] = int(tgt_state.get("points", 0) or 0) + spread_gain
                            # Roll infection at the new susceptibility.
                            rolled = _roll_infection_tier(int(tgt_state.get("points", 0) or 0))
                            if rolled is not None:
                                new_state, became_corrupted = _escalate_infection(
                                    tgt_state.get("infection_state"), rolled
                                )
                                tgt_state["infection_state"] = new_state
                                if became_corrupted:
                                    tgt_state["warp_corrupted"] = True
                            history.append({"source_id": str(src_id), "ts": now_iso})
                            unique_today.add(src_id)
                    tgt_state["spread_history"] = history

            # 3) Recompute display tiers and emit alerts
            for bid, state in states.items():
                pts = int(state.get("points", 0) or 0)
                if state.get("is_librarian"):
                    state["librarian_tier"] = _librarian_tier_for_points(pts)
                    state["exposure_tier"] = None
                    # Librarians don't accumulate brother-corruption
                    state["warp_corrupted"] = False
                    state["infection_state"] = None
                else:
                    state["exposure_tier"] = _brother_tier_for_points(pts)
                    state["librarian_tier"] = None

                    inf_state = state.get("infection_state")
                    is_corrupted = bool(state.get("warp_corrupted"))
                    actual_penalty = int(warp_penalties.get(str(bid), 0) or 0)

                    # Alerting parity with armor:
                    # - sustained alert if AAR loss occurred
                    # - detection alert when newly infected/escalated and not corrupted
                    # - corruption alert when warp_corrupted flips true this AAR
                    if actual_penalty > 0 and (inf_state or is_corrupted):
                        alerts_to_post[str(bid)] = {
                            "member": None,
                            "tier": inf_state,
                            "alert_type": "sustained",
                            "penalty_amount": actual_penalty,
                            "points": pts,
                            "warp_corrupted": is_corrupted,
                        }
                    elif inf_state and not is_corrupted:
                        last_alert_tier = state.get("last_detection_alert_tier")
                        if last_alert_tier != inf_state and _roll_warp_detection_alert(inf_state):
                            state["last_detection_alert_tier"] = inf_state
                            alerts_to_post[str(bid)] = {
                                "member": None,
                                "tier": inf_state,
                                "alert_type": "detected",
                                "penalty_amount": 0,
                                "points": pts,
                                "warp_corrupted": False,
                            }

                    if is_corrupted and not prior_corrupted.get(str(bid), False):
                        alerts_to_post[str(bid)] = {
                            "member": None,
                            "tier": inf_state,
                            "alert_type": "corrupted",
                            "penalty_amount": actual_penalty,
                            "points": pts,
                            "warp_corrupted": True,
                        }
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
                        member,
                        guild,
                        pts,
                        bool(state.get("is_librarian")),
                        infection_state=state.get("infection_state"),
                        warp_corrupted=bool(state.get("warp_corrupted")),
                    )
                except Exception:
                    pass

        # 4b) Post Librarian alerts outside locks (parity with armor flow)
        if guild is not None and alerts_to_post:
            op_mission = record.get("mission")
            op_difficulty_class = record.get("difficulty_class")
            op_url = record.get("message_url")
            for bid, alert in alerts_to_post.items():
                try:
                    member = guild.get_member(int(bid))
                except Exception:
                    member = None
                if member is None:
                    continue
                try:
                    await _post_warp_alert(
                        member=member,
                        tier=alert.get("tier"),
                        guild=guild,
                        op_mission=op_mission,
                        op_difficulty_class=op_difficulty_class,
                        op_url=op_url,
                        squad_member_ids=brother_ids,
                        alert_type=alert.get("alert_type", "sustained"),
                        penalty_amount=int(alert.get("penalty_amount", 0) or 0),
                        points=int(alert.get("points", 0) or 0),
                        warp_corrupted=bool(alert.get("warp_corrupted")),
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
    """Deprecated. Returns (outcome_key, fraction_removed, librarian_extra).

    Retained as a legacy shim for any out-of-tree caller; the new cleanse path
    in ``warp_cleanse`` uses ``_roll_cleanse_outcome_v2`` driven by recipient
    infection state.
    """
    table = WARP_CLEANSE_OUTCOMES.get(librarian_tier)
    if not table:
        table = WARP_CLEANSE_OUTCOMES[None]
    roll = random.random()
    cumulative = 0.0
    for prob, key, frac, extra in table:
        cumulative += prob
        if roll < cumulative:
            return key, float(frac), int(extra)
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
    preview_inf = recipient_state_preview.get("infection_state")
    preview_corrupted = bool(recipient_state_preview.get("warp_corrupted"))
    if intensive:
        sanction_key_for_cost = _warp_sanction_key_for_state(preview_inf, preview_corrupted)
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
    current_inf = recipient_state.get("infection_state")
    current_corrupted = bool(recipient_state.get("warp_corrupted"))

    if intensive:
        # Intensive rite: guaranteed full purge, no roll, no crit/backlash.
        # Mirrors armor's intensive blessing (forge_ops _apply_blessing_intensive_normal).
        outcome_key = "normal"
    else:
        outcome_key = _roll_cleanse_outcome_v2(current_inf, current_corrupted)

    # Mechanical effects per outcome (mirror armor blessing outcomes):
    #   normal       — fully cleanse (clear infection_state, points→0),
    #                  standard librarian transfer.
    #   crit_success — fully cleanse + grace susceptibility (negative points),
    #                  no librarian transfer.
    #   crit_fail    — no cleansing; infection escalates by one tier (or sets
    #                  warp_corrupted when already volatile); librarian
    #                  absorbs DOUBLE the standard transfer as backlash.
    grace_pts = _cfg_int(
        "crit_success_grace_points", WARP_CRIT_SUCCESS_GRACE_POINTS
    )
    if outcome_key == "crit_fail":
        removed = 0
        new_recipient_points = current_points
        fraction = 0.0
    elif outcome_key == "crit_success":
        removed = current_points
        new_recipient_points = grace_pts * max(1, int(charges_required))
        fraction = 1.0
    else:  # normal
        removed = current_points
        new_recipient_points = 0
        fraction = 1.0

    # Source bonus only meaningful on a successful cleanse with downstream
    # infections to sever.
    source_bonus_applied = False
    source_bonus_outgoing = 0
    if outcome_key in ("normal", "crit_success"):
        try:
            is_super, outgoing = _is_super_spreader(int(member.id), window_hours=24)
            if is_super and not recipient_state.get("is_librarian"):
                source_bonus_applied = True
                source_bonus_outgoing = outgoing
        except Exception:
            pass

    # Standard librarian transfer scales with how much susceptibility was removed.
    transfer = 0
    if removed > 0:
        transfer_min = _cfg_int("librarian_transfer_min", WARP_LIBRARIAN_TRANSFER_MIN)
        transfer_ratio = _cfg_float("librarian_transfer_ratio", WARP_LIBRARIAN_TRANSFER_RATIO)
        transfer = max(transfer_min, math.ceil(removed * transfer_ratio))

    if outcome_key == "crit_fail":
        # Doubled backlash even though nothing was removed — based on the
        # recipient's pre-cleanse susceptibility so the cost scales with
        # severity (mirror: armor crit_fail spreads damage from current
        # damage_tier).
        transfer_min = _cfg_int("librarian_transfer_min", WARP_LIBRARIAN_TRANSFER_MIN)
        transfer_ratio = _cfg_float("librarian_transfer_ratio", WARP_LIBRARIAN_TRANSFER_RATIO)
        baseline = max(transfer_min, math.ceil(max(1, current_points) * transfer_ratio))
        librarian_gain = 2 * baseline
    elif outcome_key == "crit_success":
        librarian_gain = 0
    else:
        librarian_gain = transfer

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

            if outcome_key == "crit_fail":
                # Escalate infection — exact mirror of armor crit_fail damage step.
                order = [None, "tainted", "exposed", "volatile"]
                try:
                    idx = order.index(current_inf)
                except ValueError:
                    idx = 0
                if idx >= len(order) - 1:
                    # Already at volatile → flip the corruption flag.
                    rstate["warp_corrupted"] = True
                else:
                    rstate["infection_state"] = order[idx + 1]
                # Detection alert tier holds — failure is visible
            else:
                # Successful cleanse — clear infection state and corruption flag.
                rstate["infection_state"] = None
                rstate["warp_corrupted"] = False
                rstate["last_detection_alert_tier"] = None
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
    post_inf = current_inf
    post_corrupted = current_corrupted
    if interaction.guild is not None:
        try:
            # Re-read the updated state so the role reflects the post-cleanse
            # infection_state / warp_corrupted flags.
            updated = await _get_warp_exposure_state(int(member.id))
            post_inf = updated.get("infection_state")
            post_corrupted = bool(updated.get("warp_corrupted"))
            await _sync_sanction_role_for_member(
                member,
                interaction.guild,
                new_recipient_points,
                bool(updated.get("is_librarian")),
                infection_state=post_inf,
                warp_corrupted=post_corrupted,
            )
        except Exception:
            pass

    # Record cleanse to chronicle datastore. Public posting + chronicle repost
    # are deferred to the 'Log to Librarium' button so spam-free private use
    # remains an option for the cleanser.
    try:
        await _record_cleanse_in_chronicle(
            int(member.id),
            int(cleanser.id),
            outcome_key,
            removed,
            librarian_gain,
        )
    except Exception:
        pass

    flavor = random.choice(WARP_CLEANSE_OUTCOME_FLAVOR.get(outcome_key, ["The rite is complete."]))
    new_sanction_key = _warp_sanction_key_for_state(post_inf, post_corrupted)
    sanction_label, sanction_desc = WARP_SANCTION_STATUS.get(new_sanction_key, ("Cleansed", ""))
    bearer_name = _strip_display_name(member.display_name)
    cleanser_name = _strip_display_name(cleanser.display_name)

    title_emoji = {
        "crit_success": "✨",
        "normal": "🧿",
        "crit_fail": "⚠️",
        # legacy keys
        "full": "🧿",
        "partial": "🌀",
        "backlash": "⚠️",
    }.get(outcome_key, "🧿")
    title_text = "᛭⋅ INTENSIVE CLEANSING RITE ⋅᛭" if intensive else "᛭⋅ WARP CLEANSING RITE ⋅᛭"

    embed = discord.Embed(
        title=title_text,
        description=f"*{flavor}*",
        color=0xF1C40F if outcome_key == "crit_success" else (
            0xE67E22 if outcome_key in ("crit_fail", "backlash") else 0x9B59B6
        ),
    )
    embed.add_field(name="▸ Bearer", value=f"**{bearer_name}**", inline=True)
    embed.add_field(name="▸ Cleanser", value=f"**{cleanser_name}**", inline=True)
    if intensive:
        embed.add_field(
            name="▸ Rite Type",
            value=f"🧿 **INTENSIVE** — {charges_required} charges · guaranteed full purge",
            inline=False,
        )
    outcome_pct = int(fraction * 100)
    outcome_summary = {
        "crit_success": f"✨ **CRITICAL SUCCESS** — full purge + grace ({new_recipient_points} susceptibility)",
        "normal": f"{title_emoji} **CLEANSED** — {outcome_pct}% removed",
        "crit_fail": f"{title_emoji} **BACKLASH** — cleanse failed, infection escalated",
    }.get(outcome_key, f"{title_emoji} **{outcome_key.upper()}** — {outcome_pct}% removed")
    embed.add_field(name="▸ Outcome", value=outcome_summary, inline=False)
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

    view = LogToLibrariumView(
        embed=embed,
        bearer_mention=member.mention,
        repost_chronicle=True,
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


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

    # Authority-bracket filter: hide out-of-bracket brothers unless the viewer's
    # own bracket is fully clear. Mirrors /armor_status behavior. Bracket scope:
    #   • Void Warden / Forgemaster (debug) → HighCom + Librarians
    #   • Librarian → own company
    #   • Anyone else → no bracket (flat list)
    #
    # "Needs cleansing" parity with armor's "actually damaged" rule:
    # rows are already pre-filtered upstream to pts > 0 (anyone at the
    # "sanctioned" / nominal tier is excluded before the bracket gate runs),
    # so the only thing in-bracket here is a brother with screening_due+
    # sanction OR warp_corrupted set. Either condition keeps the gate closed.
    bracket_fn = _b("_compute_authority_bracket_member_ids")
    authority_bracket_ids = (
        bracket_fn(interaction.user, guild, caller_role, "librarian")
        if bracket_fn else None
    )
    bracket_suppressed_out_of_bracket = False
    if authority_bracket_ids is not None:
        in_bracket_rows = []
        for r in rows:
            try:
                if int(r[2]) in authority_bracket_ids:
                    in_bracket_rows.append(r)
            except (TypeError, ValueError):
                continue
        # Row tuple: (ring, pts, uid, name, tier, is_lib, corrupted, ...)
        # Needs attention = non-librarian brother with pts > 0 OR warp_corrupted.
        # Librarians' own exposure is private and does NOT keep the gate
        # closed for their authority's view.
        def _needs_cleansing(r) -> bool:
            _ring, _pts, _uid, _name, _tier, _is_lib, _corrupted = r[:7]
            if _is_lib:
                return False
            return _pts > 0 or bool(_corrupted)

        any_in_bracket_tainted = any(_needs_cleansing(r) for r in in_bracket_rows)
        if any_in_bracket_tainted:
            rows = in_bracket_rows
            bracket_suppressed_out_of_bracket = True

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
    tree_roots = []  # (direct_count, uid, name, pts, tier, corrupted, is_super, ring, mem_company)
    for ring, pts, uid, name, tier, is_lib, corrupted, is_super, has_targets, mem_company in top_rows:
        in_authority = (caller_company is None) or (ring <= 1)
        if not is_lib and has_targets and in_authority:
            direct = len(_compute_outgoing_infections(int(uid), states=data, window_hours=24))
            tree_roots.append((direct, uid, name, pts, tier, corrupted, is_super, ring, mem_company))
    tree_roots.sort(key=lambda r: -r[0])

    # ── Pass 2: build the unified at-risk list. Roots render with their
    # downstream subtree indented; isolated entries (not covered by any tree)
    # render flat. Mirrors the single ▸ Brothers at Risk field of /armor_status.
    lines: List[str] = []
    covered_uids: set = set()
    for _direct, root_uid, root_name, root_pts, root_tier, root_corrupted, root_is_super, root_ring, root_company in tree_roots[:3]:
        if str(root_uid) in covered_uids:
            continue
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
        # Show the same tier icon convention used by flat rows.
        root_icon = WARP_BROTHER_TIER_ICON.get(root_tier, "🟢")
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
            f"{root_icon} {root_name}{rcompany_tag} · {root_pts}c{rflag_str} _(→ {direct})_{deeper_tag}"
        )
        lines.extend(subtree_lines)
        covered_uids |= visited

    for ring, pts, uid, name, tier, is_lib, corrupted, is_super, has_targets, mem_company in top_rows:
        if str(uid) in covered_uids:
            continue
        if is_lib:
            tier_icon = WARP_LIBRARIAN_TIER_ICON.get(tier, "🟩")
            marker = tier_icon
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
    if bracket_suppressed_out_of_bracket:
        if caller_role == "librarian":
            scope_short = _b("_extract_company_short_name")(caller_company) if caller_company else "your company"
            embed.description = (
                f"*Scope: **{scope_short}** — the wider fortress is hidden "
                f"until your company is fully clear.*"
            )
        else:
            embed.description = (
                "*Scope: **High Command + Librarians** — the wider fortress is "
                "hidden until your authority is fully clear.*"
            )

    # ─── Your Vigil (personal panel — librarians only; void wardens / debug
    # callers have no charge pool of their own, so this section is skipped).
    if caller_role == "librarian":
        try:
            caller_state = data.get(str(interaction.user.id)) or {}
            caller_pts = int(caller_state.get("points", 0) or 0)
            caller_tier = _librarian_tier_for_points(caller_pts)
            tier_icon = WARP_LIBRARIAN_TIER_ICON.get(caller_tier, "🟩")
            tier_label = WARP_LIBRARIAN_TIER_DESCRIPTIONS.get(
                caller_tier, ("CLEAR", "")
            )[0].title()
            pool_max_self = _cfg_int("warding_pool_max", WARDING_POOL_MAX)
            own_charges = await _get_librarian_available_charges(int(interaction.user.id))
            # Next-regen ETA: regen window minus age of oldest active (used) charge.
            pool_state = await _get_librarian_pool_state(int(interaction.user.id))
            active_ts = _filter_active_warding_timestamps(
                pool_state.get("warding_timestamps") or []
            )
            regen_text = ""
            if active_ts:
                try:
                    regen_seconds = _cfg_float(
                        "warding_pool_regen_hours", WARDING_POOL_REGEN_HOURS
                    ) * 3600
                    oldest = min(
                        datetime.fromisoformat(ts) for ts in active_ts if ts
                    )
                    remaining = timedelta(seconds=regen_seconds) - (
                        datetime.utcnow() - oldest
                    )
                    total_s = int(remaining.total_seconds())
                    if total_s > 0:
                        hrs, mins = divmod(total_s // 60, 60)
                        if hrs > 0:
                            regen_text = f" · next +1 in {hrs}h{mins:02d}m"
                        else:
                            regen_text = f" · next +1 in {mins}m"
                except Exception:
                    pass
            # Today's rites given (UTC day) sourced from the chronicle.
            today_start = datetime.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cleanses_today = 0
            backlashes_today = 0
            try:
                async with _g.LIBRARIUM_CHRONICLE_LOCK:
                    _vigil_chron = _load_librarium_chronicle()
                for _entry in (_vigil_chron.get("cleanse_history") or []):
                    if str(_entry.get("librarian_id")) != str(interaction.user.id):
                        continue
                    try:
                        _ts = datetime.fromisoformat(_entry.get("ts", ""))
                    except Exception:
                        continue
                    if _ts < today_start:
                        continue
                    cleanses_today += 1
                    if _is_backlash_outcome(_entry.get("outcome")):
                        backlashes_today += 1
            except Exception:
                pass
            vigil_value = (
                f"{tier_icon} **{tier_label}** · {caller_pts}c · "
                f"🧿 {own_charges}/{pool_max_self} charges{regen_text}\n"
                f"Today: {cleanses_today} rite"
                f"{'s' if cleanses_today != 1 else ''} · "
                f"{backlashes_today} backlash"
                f"{'es' if backlashes_today != 1 else ''}"
            )
            embed.add_field(name="▸ Your Vigil", value=vigil_value, inline=False)
        except Exception:
            pass

    # Calculate available charges for Librarians.
    if caller_role == "forgemaster_debug":
        # Forgemaster testing: use placeholder values
        total_charges = 12
        charges_status = "🟢"  # Green (sufficient)
    else:
        # Real calculation: sum charges from all Librarians with exposure
        total_charges = 0
        charges_status = "🟢"
        try:
            for uid, raw in data.items():
                if bool(raw.get("is_librarian")):
                    try:
                        available = await _get_librarian_available_charges(int(uid))
                        total_charges += available
                    except Exception:
                        pass
            # Status indicators: red if none, yellow if insufficient, green if adequate
            brothers_needing_cleanse = len([r for r in rows if not r[5]])  # non-librarians with exposure
            if total_charges == 0:
                charges_status = "🔴"
            elif brothers_needing_cleanse > 0 and total_charges < brothers_needing_cleanse:
                charges_status = "🟡"
        except Exception:
            total_charges = 0
            charges_status = "❓"

    embed.add_field(
        name="▸ Available Charges",
        value=f"{charges_status} **{total_charges}** Warding Charges",
        inline=False,
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
            f"**Brothers** (circles): 🟡 Tainted · 🟠 Exposed · 🔴 Volatile · {WARP_CORRUPTED_ICON} Corrupted\n"
            "**Librarians** (squares): 🟨 Stable · 🟧 Resonant · 🟥 Surging · ⬛ Overloaded · 🟫 Abyssal\n"
            f"{WARP_SPREADER_ICON} Super-spreader · `Nc` = cycles · "
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
            f"**Brothers** (circles): 🟡 Tainted · 🟠 Exposed · 🔴 Volatile · {WARP_CORRUPTED_ICON} Corrupted\n"
            "**Librarians** (squares): 🟨 Stable · 🟧 Resonant · 🟥 Surging · ⬛ Overloaded · 🟫 Abyssal\n"
            f"{WARP_SPREADER_ICON} Super-spreader · `Nc` = cycles"
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
    view = LogToLibrariumView(embed=embed, bearer_mention=None, repost_chronicle=False)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@_g.bot.tree.command(
    name="librarium_chronicle",
    description="Post a sanitized Librarium status snapshot (Void Warden only).",
)
async def librarium_chronicle(interaction: discord.Interaction):
    allowed, role = _is_librarian_or_void_warden(interaction.user, command_name="librarium_chronicle")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
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


# ---------------------------------------------------------------------------
# Auto-AAR-ingest: Librarian cadre pressure contributor.
#
# Demand = charge-weighted sum of:
#   (a) intensive cleanse costs for all active non-Librarian brothers who need
#       cleansing,
#   (b) tier-scaled demand for active Librarians with a non-None exposure tier
#       (cleansing them costs charges, whether via self-cleanse or Void Warden rite),
#   (c) fractional at-risk contribution for clean brothers based on the
#       population's background transmission probability (SIR-style: 1 - ∏(1-spread_i)).
#
# Supply = sum of available warding charges across all members of the
# Watch Librarian / Void Warden roles whose personal exposure tier is NOT
# overloaded/abyssal (they cannot cleanse others while in those tiers).
#
# See opscribe/pressure_registry.py for the aggregation contract.
# ---------------------------------------------------------------------------

async def evaluate_librarian_pressure(guild: discord.Guild):
    """Pressure evaluator for the Librarian cadre. See pressure_registry.

    Uses charge-weighted demand: each infected brother contributes the
    number of intensive cleanse charges required to restore him rather
    than a flat head-count of 1.

    Additionally, a fractional at-risk demand is added for clean brothers
    based on the background warp transmission probability — the cumulative
    probability that a clean brother would contract an infection in a
    typical AAR given the current infected population (SIR-style product
    formula).  This mirrors the techmarine predictive-warning signal and
    fires when background transmission probability reaches the configured
    warp_at_risk_threshold (default 20 %).
    """
    from .pressure_registry import CadrePressure

    try:
        async with _g.WARP_EXPOSURE_LOCK:
            data = _load_warp_exposure()
    except Exception:
        data = {}

    is_active_fn = _b("_is_active_participant")

    # Collect active Librarian IDs (Librarians + Void Wardens).
    librarian_ids: List[int] = []
    for member in guild.members:
        if member.bot:
            continue
        role_names = {r.name for r in member.roles}
        if not (LIBRARIAN_ROLE_NAME in role_names or VOID_WARDEN_ROLE_NAME in role_names):
            continue
        if is_active_fn and not is_active_fn(member):
            continue
        librarian_ids.append(int(member.id))
    lib_id_set = set(librarian_ids)

    # Demand pass: charge-weighted cost for infected brothers and burdened
    # Librarians + collect infected tier list for background-transmission.
    demand: float = 0.0
    infected_tiers: List[str] = []  # infection_state of each infected active non-lib brother

    for uid_str, raw in (data or {}).items():
        try:
            uid = int(uid_str)
        except Exception:
            continue

        is_lib_record = uid in lib_id_set or bool((raw or {}).get("is_librarian"))
        member = guild.get_member(uid)
        if member is None:
            continue
        if is_active_fn and not is_active_fn(member):
            continue

        if is_lib_record:
            # Librarians contribute demand based on their personal exposure tier.
            # Cleansing them (self-cleanse or Void Warden rite) costs charges even
            # though they do not accumulate infection_state like brothers do.
            lib_pts = int((raw or {}).get("points", 0) or 0)
            lib_tier = _librarian_tier_for_points(lib_pts)
            if lib_tier is not None:
                demand += _get_librarian_demand_cost(lib_tier)
            continue

        infection_state = (raw or {}).get("infection_state")
        pts = int((raw or {}).get("points", 0) or 0)
        warp_corrupted = bool((raw or {}).get("warp_corrupted", False))

        # Determine sanction key: prefer infection_state (canonical) over legacy points.
        if warp_corrupted:
            sanction_key = "restricted"
        elif infection_state:
            sanction_key = _warp_sanction_key_for_state(infection_state)
        else:
            sanction_key = _warp_sanction_key_for_points(pts)

        if sanction_key != "sanctioned":
            demand += _get_intensive_cleanse_cost(sanction_key, warp_corrupted)
            tier_for_spread = infection_state or (
                "volatile" if pts >= 10 else "exposed" if pts >= 5 else "tainted"
            )
            infected_tiers.append(tier_for_spread)

    # At-risk signal: background transmission probability for clean brothers.
    # P(at least one exposure) = 1 - ∏(1 - spread_chance[tier_i]) for each infected
    # active non-lib brother.  Clean brothers contribute fractional demand equal to
    # their background infection probability × the cost of an initial-tier cleanse.
    spread_chances = _get_spread_chances()
    p_no_infection = 1.0
    for tier in infected_tiers:
        p_no_infection *= 1.0 - spread_chances.get(tier, 0.0)
    # Round to 10 sig figs to avoid floating-point representation artefacts
    # (e.g. 1 - (1 - 0.20) = 0.19999...96 without rounding).
    background_prob = round(1.0 - p_no_infection, 10)

    try:
        warp_at_risk_threshold = float(
            _warp_config().get("at_risk_threshold", 0.20) or 0.20
        )
    except Exception:
        warp_at_risk_threshold = 0.20

    if background_prob >= warp_at_risk_threshold:
        # Count active non-lib guild members with no infection record (clean brothers).
        cleanse_cost_initial = _get_intensive_cleanse_cost("screening_due")
        clean_active_count = 0
        for member in guild.members:
            if member.bot:
                continue
            if int(member.id) in lib_id_set:
                continue
            if is_active_fn and not is_active_fn(member):
                continue
            raw = (data or {}).get(str(member.id)) or {}
            if (
                not raw.get("infection_state")
                and not raw.get("warp_corrupted")
                and int(raw.get("points", 0) or 0) <= 0
            ):
                clean_active_count += 1
        demand += clean_active_count * background_prob * cleanse_cost_initial

    # Supply: sum of available warding charges across capable Librarians.
    supply = 0
    for lib_id in librarian_ids:
        try:
            lib_state = (data or {}).get(str(lib_id)) or {}
            lib_tier = _librarian_tier_for_points(int(lib_state.get("points", 0) or 0))
            if lib_tier in ("overloaded", "abyssal"):
                continue
            supply += await _get_librarian_available_charges(lib_id)
        except Exception:
            continue

    # Prefer the Watch Librarian role for blocker pings; fall back to Void Warden.
    notify_role = (
        discord.utils.get(guild.roles, name=LIBRARIAN_ROLE_NAME)
        or discord.utils.get(guild.roles, name=VOID_WARDEN_ROLE_NAME)
    )
    notify_role_id = notify_role.id if notify_role else None

    # Cadre-specific tier-1 notification channel (config override → default).
    try:
        cfg = _b("CONFIG") or {}
        notify_channel_id = (
            int(cfg.get("auto_ingest", {}).get("librarian_blocker_channel_id", 0) or 0)
            or 1502840890446708766
        )
    except Exception:
        notify_channel_id = 1502840890446708766

    result = CadrePressure(
        cadre_id="librarian",
        display_name="Librarians",
        demand=demand,
        supply=supply,
        notify_role_id=notify_role_id,
        notify_channel_id=notify_channel_id,
    )
    result.detail = f"{result.demand_display} charge(s) of warding work outstanding; {supply} warding charge(s) available"
    return result


def _register_pressure_contributors() -> None:
    """Register this module's cadre evaluator. Idempotent. Called from bot.py."""
    from .pressure_registry import register_cadre
    register_cadre(evaluate_librarian_pressure)
