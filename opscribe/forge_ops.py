"""Forge operations: armor integrity, blessing pool, forge pool, rites,
machine spirits, forge rite rendering, LFG, forge chronicle functions."""

import os
import json
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
import re
from typing import List, Tuple, Optional
import hashlib
import random
import sys as _sys

from .constants import *  # noqa: F401,F403
from .constants import _strip_display_name
from .flavor_text import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from .studs import *  # noqa: F401,F403
from . import _bot_globals as _g


def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


def _load_rites() -> dict:
    try:
        if not os.path.exists(RITES_PATH):
            return {}
        with open(RITES_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_rites(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RITES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_user_rite(user_id: int) -> Optional[str]:
    try:
        async with _g.RITES_LOCK:
            data = _load_rites()
            return data.get(str(user_id))
    except Exception:
        return None


async def _set_user_rite(user_id: int, text: str):
    try:
        async with _g.RITES_LOCK:
            data = _load_rites()
            data[str(user_id)] = text
            _save_rites(data)
    except Exception:
        pass


# --- Machine Spirit Persistence for Forge Rite ---


def _load_machine_spirits() -> dict:
    try:
        if not os.path.exists(MACHINE_SPIRITS_PATH):
            return {}
        with open(MACHINE_SPIRITS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_machine_spirits(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MACHINE_SPIRITS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_machine_spirit(user_id: int) -> Optional[str]:
    """Get the stored machine spirit designation for a user's armor.

    Handles both old format (string) and new format (dict with designation/bound_ts).
    Always returns just the designation string for backward compatibility.
    """
    try:
        async with _g.MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            entry = data.get(str(user_id))
            if entry is None:
                return None
            # Handle both formats
            if isinstance(entry, dict):
                return entry.get("designation")
            return entry  # Old string format
    except Exception:
        return None


async def _set_machine_spirit(user_id: int, spirit: str):
    """Store the machine spirit designation for a user's armor.

    Saves in new format with designation and bound_ts for Forge Chronicle tracking.
    """
    try:
        async with _g.MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            data[str(user_id)] = {
                "designation": spirit,
                "bound_ts": datetime.utcnow().isoformat(),
            }
            _save_machine_spirits(data)
    except Exception:
        pass


async def _delete_machine_spirit(user_id: int) -> Optional[str]:
    """Delete a machine spirit and return its designation if it existed."""
    try:
        async with _g.MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            entry = data.pop(str(user_id), None)
            if entry:
                _save_machine_spirits(data)
                if isinstance(entry, dict):
                    return entry.get("designation")
                return entry
            return None
    except Exception:
        return None


# --- Armor Integrity System ---
# Tracks armor wear and damage for brothers, with Techmarine maintenance requirements.


# Damage tier definitions: role name -> penalty
# Armor Integrity / Forge subsystem data tables (ARMOR_DAMAGE_TIERS,
# ARMOR_DAMAGE_PENALTIES, MISSION_TO_PLANET, ARMOR_PENALTY_PROBABILITIES,
# ARMOR_DETECTION_CHANCES, ARMOR_SCAN_*, ARMOR_STATUS_*, INTENSIVE_SCAN_COST,
# DEFAULT_ARMOR_*, SPIRIT_RESTORATION_PHRASES, SPIRIT_RECONSECRATION_PHRASES,
# FORGE_AMBIENT_MESSAGES) live in flavor_text.py.


def _load_armor_integrity() -> dict:
    """Load armor integrity data from disk."""
    try:
        if not os.path.exists(ARMOR_INTEGRITY_PATH):
            return {}
        with open(ARMOR_INTEGRITY_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_armor_integrity(data: dict):
    """Save armor integrity data to disk with backup."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # Create backup if file exists
        if os.path.exists(ARMOR_INTEGRITY_PATH):
            bak_path = ARMOR_INTEGRITY_PATH + ".bak"
            try:
                import shutil

                shutil.copy2(ARMOR_INTEGRITY_PATH, bak_path)
            except Exception:
                pass
        with open(ARMOR_INTEGRITY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# Batch armor integrity helpers for bulk ingest operations
# These avoid repeated file I/O by working with an in-memory dict


def _get_armor_state_from_batch(user_id: int, batch_data: dict) -> dict:
    """Get armor state from batch data (in-memory, no file I/O)."""
    return batch_data.get(
        str(user_id),
        {
            "points_since_blessing": 0,
            "damage_tier": None,
            "critical_aar_count": 0,
            "spirit_fractured": False,
            "last_blessing_timestamp": None,
        },
    )


def _set_armor_state_in_batch(user_id: int, state: dict, batch_data: dict):
    """Set armor state in batch data (in-memory, no file I/O)."""
    batch_data[str(user_id)] = state


async def _save_armor_batch(batch_data: dict):
    """Save batch armor data to disk (call once at end of bulk operation)."""
    try:
        async with _g.ARMOR_INTEGRITY_LOCK:
            _save_armor_integrity(batch_data)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Armor Scan State - Detection caching per AAR cycle
# ---------------------------------------------------------------------------


def _load_scan_state() -> dict:
    """Load armor scan state from disk."""
    try:
        if not os.path.exists(ARMOR_SCAN_STATE_PATH):
            return {"aar_generation": 0, "intensive_scans": {}, "scan_cache": {}}
        with open(ARMOR_SCAN_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            # Ensure all required keys exist
            data.setdefault("aar_generation", 0)
            data.setdefault("intensive_scans", {})
            data.setdefault("scan_cache", {})
            return data
    except Exception:
        return {"aar_generation": 0, "intensive_scans": {}, "scan_cache": {}}


def _save_scan_state(data: dict):
    """Save armor scan state to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ARMOR_SCAN_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _increment_aar_generation():
    """Increment AAR generation counter and clear stale scan cache."""
    async with _g.ARMOR_SCAN_STATE_LOCK:
        data = _b("_load_scan_state")()
        data["aar_generation"] = data.get("aar_generation", 0) + 1
        # Clear scan cache on new AAR cycle (all results are now stale)
        data["scan_cache"] = {}
        # Intensive scans purchased in previous cycles are now expired
        # (will be checked when used, but we can prune here)
        current_gen = data["aar_generation"]
        data["intensive_scans"] = {k: v for k, v in data.get("intensive_scans", {}).items() if v >= current_gen}
        _b("_save_scan_state")(data)
        return data["aar_generation"]


async def _get_aar_generation() -> int:
    """Get the current AAR generation counter."""
    async with _g.ARMOR_SCAN_STATE_LOCK:
        data = _b("_load_scan_state")()
        return data.get("aar_generation", 0)


async def _purchase_intensive_scan(techmarine_id: int) -> bool:
    """Mark a Techmarine as having an active intensive scan for this AAR cycle."""
    async with _g.ARMOR_SCAN_STATE_LOCK:
        data = _b("_load_scan_state")()
        current_gen = data.get("aar_generation", 0)
        data.setdefault("intensive_scans", {})[str(techmarine_id)] = current_gen
        _b("_save_scan_state")(data)
        return True


async def _has_intensive_scan(techmarine_id: int) -> bool:
    """Check if a Techmarine has an active intensive scan for this AAR cycle."""
    async with _g.ARMOR_SCAN_STATE_LOCK:
        data = _b("_load_scan_state")()
        current_gen = data.get("aar_generation", 0)
        tech_gen = data.get("intensive_scans", {}).get(str(techmarine_id))
        # Intensive scan is active if purchased in current generation
        return tech_gen is not None and tech_gen >= current_gen


async def _get_or_roll_scan_result(
    brother_id: int,
    current_tier: Optional[str],
    points_since_blessing: int,
    spirit_fractured: bool,
) -> dict:
    """Get cached scan result or roll a new one for this AAR cycle.

    Returns dict with:
        - detected: bool (True if brother shows up in scan)
        - predictive_warning: bool (True if risk warning triggered for nominal)
        - miss_reason: str or None (if not detected, why)
    """
    async with _g.ARMOR_SCAN_STATE_LOCK:
        data = _b("_load_scan_state")()
        current_gen = data.get("aar_generation", 0)
        cache = data.setdefault("scan_cache", {})
        brother_key = str(brother_id)

        # Check if we have a cached result for this AAR cycle
        cached = cache.get(brother_key)
        if cached and cached.get("aar_gen") == current_gen:
            return cached

        # Roll new scan result
        result = _roll_scan_result(current_tier, points_since_blessing, spirit_fractured)
        result["aar_gen"] = current_gen

        # Cache the result
        cache[brother_key] = result
        _b("_save_scan_state")(data)

        return result


def _roll_scan_result(
    current_tier: Optional[str],
    points_since_blessing: int,
    spirit_fractured: bool,
) -> dict:
    """Roll fresh scan detection result based on tier/points.

    Returns dict with detected, predictive_warning, miss_reason.
    """
    import random

    # Fractured spirits are always detected
    if spirit_fractured:
        return {"detected": True, "predictive_warning": False, "miss_reason": None}

    # Damaged tiers have miss chances
    if current_tier and current_tier in ARMOR_SCAN_MISS_CHANCES:
        miss_chance = ARMOR_SCAN_MISS_CHANCES[current_tier]
        if random.random() < miss_chance:
            return {
                "detected": False,
                "predictive_warning": False,
                "miss_reason": "spirit_uncommunicative",
            }
        # Detected
        return {"detected": True, "predictive_warning": False, "miss_reason": None}

    # Nominal brother - first roll for miss chance
    nominal_miss_chance = ARMOR_SCAN_MISS_CHANCES.get("nominal", 0.20)
    if random.random() < nominal_miss_chance:
        return {
            "detected": False,
            "predictive_warning": False,
            "miss_reason": "spirit_uncommunicative",
        }

    # Nominal brother detected. The ⚡ "at risk" marker now reflects actual
    # statistical danger on the NEXT ingest — not a separate flavor roll.
    # A brother is at risk iff their per-AAR damage probability meets the
    # configured threshold (armor_integrity.at_risk_probability_threshold,
    # default 0.20 = 20%). Under the default tiers that's 15+ cycles.
    threshold = float(
        _get_armor_config().get("at_risk_probability_threshold", 0.20)
    )
    if _get_damage_probability(points_since_blessing) >= threshold:
        return {
            "detected": True,
            "predictive_warning": True,
            "miss_reason": None,
        }

    # No warning triggered for nominal brother with low risk
    # They are "detected" but without any warning status
    return {"detected": True, "predictive_warning": False, "miss_reason": None}


async def _get_armor_state(user_id: int) -> dict:
    """Get armor integrity state for a user."""
    try:
        async with _g.ARMOR_INTEGRITY_LOCK:
            data = _load_armor_integrity()
            return data.get(
                str(user_id),
                {
                    "points_since_blessing": 0,
                    "damage_tier": None,
                    "critical_aar_count": 0,
                    "spirit_fractured": False,
                    "last_blessing_timestamp": None,
                },
            )
    except Exception:
        return {
            "points_since_blessing": 0,
            "damage_tier": None,
            "critical_aar_count": 0,
            "spirit_fractured": False,
            "last_blessing_timestamp": None,
        }


async def _set_armor_state(user_id: int, state: dict):
    """Update armor integrity state for a user."""
    try:
        async with _g.ARMOR_INTEGRITY_LOCK:
            data = _load_armor_integrity()
            data[str(user_id)] = state
            _save_armor_integrity(data)
    except Exception:
        pass


def _get_armor_config() -> dict:
    """Get armor integrity configuration from _g.CONFIG or defaults."""
    return _g.CONFIG.get("armor_integrity", {})


def _get_armor_probability_tiers() -> list:
    """Get probability tiers from config or defaults."""
    config = _get_armor_config()
    return config.get("probability_tiers", DEFAULT_ARMOR_PROBABILITY_TIERS)


def _get_probability_tier_for_points(points_since_blessing: int) -> Optional[dict]:
    """Get the probability tier config for a given point total."""
    tiers = _get_armor_probability_tiers()
    for tier in tiers:
        min_pts = tier.get("min", 0)
        max_pts = tier.get("max")
        if max_pts is None:
            # Unbounded upper tier
            if points_since_blessing >= min_pts:
                return tier
        else:
            if min_pts <= points_since_blessing <= max_pts:
                return tier
    return None


def _get_damage_probability(points_since_blessing: int) -> float:
    """Get damage probability for a given point total."""
    tier = _get_probability_tier_for_points(points_since_blessing)
    if tier:
        return tier.get("chance", 0.0)
    return 0.0


def _roll_damage_tier(points_since_blessing: int) -> str:
    """Roll which damage tier to apply based on weighted probabilities.

    Returns one of: 'damaged', 'compromised', 'critical'
    """
    tier = _get_probability_tier_for_points(points_since_blessing)

    # Default weights if not specified
    default_weights = {"damaged": 100, "compromised": 0, "critical": 0}
    weights = tier.get("damage_weights", default_weights) if tier else default_weights

    # Build weighted list
    damage_tiers = []
    tier_weights = []
    for damage_tier in ARMOR_DAMAGE_TIERS:
        weight = weights.get(damage_tier, 0)
        if weight > 0:
            damage_tiers.append(damage_tier)
            tier_weights.append(weight)

    # If no valid weights, default to damaged
    if not damage_tiers:
        return "damaged"

    # Weighted random selection
    total = sum(tier_weights)
    roll = random.uniform(0, total)
    cumulative = 0
    for i, weight in enumerate(tier_weights):
        cumulative += weight
        if roll <= cumulative:
            return damage_tiers[i]

    return damage_tiers[-1]


def _roll_detection_alert(current_tier: str) -> bool:
    """Roll whether to send an early detection alert for current damage tier.

    Args:
        current_tier: Current damage tier (damaged, compromised, critical, fractured)

    Returns:
        True if detection alert should be sent, False otherwise.
    """
    if not current_tier:
        return False

    chance = ARMOR_DETECTION_CHANCES.get(current_tier, 0.0)
    return random.random() < chance


def _get_armor_damage_role_ids() -> dict:
    """Get damage role IDs from config."""
    config = _get_armor_config()
    return config.get("damage_role_ids", {})


def _get_arming_chamber_channel_id() -> Optional[int]:
    """Get the arming chamber channel ID for alerts."""
    config = _get_armor_config()
    cid = config.get("arming_chamber_channel_id")
    if cid:
        try:
            return int(cid)
        except (ValueError, TypeError):
            pass
    return None


def _get_techmarine_role_id() -> Optional[int]:
    """Get the Techmarine role ID for pinging."""
    config = _get_armor_config()
    rid = config.get("techmarine_role_id")
    if rid:
        try:
            return int(rid)
        except (ValueError, TypeError):
            pass
    return None


def _get_member_damage_tier(member: discord.Member) -> Optional[str]:
    """Check a member's roles and return their current damage tier, or None if undamaged."""
    role_ids = _b("_get_armor_damage_role_ids")()
    if not role_ids:
        return None

    member_role_ids = {r.id for r in getattr(member, "roles", [])}

    # Check in order of severity (return highest)
    for tier in reversed(ARMOR_DAMAGE_TIERS):
        tier_role_id = role_ids.get(tier)
        if tier_role_id:
            try:
                if int(tier_role_id) in member_role_ids:
                    return tier
            except (ValueError, TypeError):
                pass
    return None


def _get_damage_penalty(tier: Optional[str]) -> int:
    """Get the AAR point penalty for a damage tier."""
    if not tier:
        return 0
    return ARMOR_DAMAGE_PENALTIES.get(tier, 0)


def _roll_armor_penalty(tier: Optional[str], spirit_fractured: bool = False) -> int:
    """Roll a probabilistic AAR penalty based on damage tier.

    Uses ARMOR_PENALTY_PROBABILITIES to determine outcome.
    Returns the penalty amount (0 = no penalty, 1-4 = AAR reduction).
    """
    import random

    # Fractured state overrides tier
    if spirit_fractured:
        probs = ARMOR_PENALTY_PROBABILITIES.get("fractured", {0: 1.0})
    else:
        probs = ARMOR_PENALTY_PROBABILITIES.get(tier, {0: 1.0})

    # Roll against cumulative probabilities
    roll = random.random()
    cumulative = 0.0
    for penalty, prob in sorted(probs.items()):
        cumulative += prob
        if roll < cumulative:
            return penalty

    # Fallback (shouldn't happen if probabilities sum to 1.0)
    return 0


def _get_tier_risk_display(tier: Optional[str], spirit_fractured: bool = False) -> str:
    """Get a human-readable risk display string for a damage tier.

    Returns format like "75% (-1 to -3 AAR)" or "No risk" for nominal.
    """
    if spirit_fractured:
        probs = ARMOR_PENALTY_PROBABILITIES.get("fractured", {0: 1.0})
    else:
        probs = ARMOR_PENALTY_PROBABILITIES.get(tier, {0: 1.0})

    # Calculate total penalty chance
    penalty_chance = sum(prob for penalty, prob in probs.items() if penalty > 0)

    if penalty_chance == 0:
        return "No risk"

    # Find min and max penalties (excluding 0)
    penalties_with_chance = [p for p, prob in probs.items() if p > 0 and prob > 0]
    if not penalties_with_chance:
        return "No risk"

    min_penalty = min(penalties_with_chance)
    max_penalty = max(penalties_with_chance)

    percent = int(penalty_chance * 100)
    if min_penalty == max_penalty:
        return f"{percent}% (-{min_penalty} AAR)"
    else:
        return f"{percent}% (-{min_penalty} to -{max_penalty} AAR)"


def _check_armor_grace_period(member: discord.Member, total_aar_points: int) -> bool:
    """Check if a member has cleared the grace period.

    Returns True if BOTH conditions are met:
    - At least grace_period_min_points AAR points earned
    - At least grace_period_min_days since joining
    """
    config = _get_armor_config()
    min_points = config.get("grace_period_min_points", DEFAULT_ARMOR_GRACE_PERIOD_MIN_POINTS)
    min_days = config.get("grace_period_min_days", DEFAULT_ARMOR_GRACE_PERIOD_MIN_DAYS)

    # Check points threshold
    if total_aar_points < min_points:
        return False

    # Check time threshold (supports induction override)
    joined_at = _b("_get_effective_induction_date")(member)
    if not joined_at:
        return False

    days_since_join = (datetime.utcnow() - joined_at.replace(tzinfo=None)).days
    if days_since_join < min_days:
        return False

    return True


async def _run_armor_integrity_check(points_since_blessing: int) -> bool:
    """Run the armor integrity check and return True if damage occurs."""
    probability = _get_damage_probability(points_since_blessing)
    if probability <= 0:
        return False
    return random.random() < probability


async def _apply_damage_tier(
    member: discord.Member,
    guild: discord.Guild,
    current_tier: Optional[str],
    rolled_tier: str,
) -> Optional[str]:
    """Apply a rolled damage tier if it's worse than current. Returns the new tier."""
    role_ids = _b("_get_armor_damage_role_ids")()
    if not role_ids:
        return None

    # Determine current index
    if current_tier is None:
        current_idx = -1
    else:
        try:
            current_idx = ARMOR_DAMAGE_TIERS.index(current_tier)
        except ValueError:
            current_idx = -1

    # Determine rolled tier index
    try:
        rolled_idx = ARMOR_DAMAGE_TIERS.index(rolled_tier)
    except ValueError:
        return None

    # Only apply if rolled tier is worse (higher index) than current
    if rolled_idx <= current_idx:
        return current_tier

    new_tier = rolled_tier
    new_role_id = role_ids.get(new_tier)

    if not new_role_id:
        return None

    try:
        # Remove current damage role if any
        if current_tier:
            current_role_id = role_ids.get(current_tier)
            if current_role_id:
                current_role = guild.get_role(int(current_role_id))
                if current_role and current_role in member.roles:
                    await member.remove_roles(current_role, reason="Armor integrity: applying damage tier")

        # Add new damage role
        new_role = guild.get_role(int(new_role_id))
        if new_role:
            await member.add_roles(new_role, reason=f"Armor integrity: {new_tier} damage")

        return new_tier
    except Exception:
        return None


async def _clear_armor_damage(member: discord.Member, guild: discord.Guild, grace_points: int = 0):
    """Remove all damage roles from a member and reset their armor state.

    Args:
        grace_points: Starting points (negative = grace period, e.g., -25 for crit success)
    """
    role_ids = _b("_get_armor_damage_role_ids")()

    # Remove all damage roles
    for tier in ARMOR_DAMAGE_TIERS:
        role_id = role_ids.get(tier)
        if role_id:
            try:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    await member.remove_roles(role, reason="Armor integrity: blessed by Techmarine")
            except Exception:
                pass

    # Get current state to preserve/update blessing timestamps
    current_state = await _b("_get_armor_state")(member.id)
    blessing_timestamps = current_state.get("blessing_timestamps", [])

    # Filter old timestamps and add current
    now = datetime.utcnow()
    cooldown_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < cooldown_window:
                active_timestamps.append(ts_str)
        except Exception:
            continue
    active_timestamps.append(now.isoformat())

    # Reset armor state with updated timestamps
    await _b("_set_armor_state")(
        member.id,
        {
            "display_name": member.display_name,
            "points_since_blessing": grace_points,
            "damage_tier": None,
            "critical_aar_count": 0,
            "spirit_fractured": False,
            "last_blessing_timestamp": now.isoformat(),
            "blessing_timestamps": active_timestamps,
            "last_detection_alert_tier": None,  # Reset detection tracking
        },
    )


async def _apply_blessing_crit_fail(member: discord.Member, guild: discord.Guild):
    """Apply crit fail blessing result: escalate damage tier.

    A botched rite agitates the machine spirit, causing it to worsen:
    - Nominal → Damaged
    - Damaged → Compromised
    - Compromised → Critical
    - Critical → Fractured (spirit breaks!)

    Returns the new damage tier after escalation.
    """
    current_tier = _b("_get_member_damage_tier")(member)

    # Get current state to preserve/update blessing timestamps
    current_state = await _b("_get_armor_state")(member.id)
    blessing_timestamps = current_state.get("blessing_timestamps", [])

    # Filter old timestamps and add current
    now = datetime.utcnow()
    cooldown_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < cooldown_window:
                active_timestamps.append(ts_str)
        except Exception:
            continue
    active_timestamps.append(now.isoformat())

    # Escalate damage tier
    tier_order = [None, "damaged", "compromised", "critical", "fractured"]
    current_idx = tier_order.index(current_tier) if current_tier in tier_order else 0
    new_idx = min(current_idx + 1, len(tier_order) - 1)
    new_tier = tier_order[new_idx]

    # Check if spirit fractured from this escalation
    spirit_fractured = new_tier == "fractured"

    # Apply the damage role to the member
    role_ids = _b("_get_armor_damage_role_ids")()
    if role_ids and new_tier and new_tier != "fractured":
        try:
            # Remove current damage role if any
            if current_tier:
                current_role_id = role_ids.get(current_tier)
                if current_role_id:
                    current_role = guild.get_role(int(current_role_id))
                    if current_role and current_role in member.roles:
                        await member.remove_roles(current_role, reason="Armor integrity: crit fail escalation")

            # Add new damage role
            new_role_id = role_ids.get(new_tier)
            if new_role_id:
                new_role = guild.get_role(int(new_role_id))
                if new_role:
                    await member.add_roles(new_role, reason=f"Armor integrity: crit fail → {new_tier}")
        except Exception:
            pass  # Role application failed but state update should still proceed

    # If fractured, apply critical role (fractured is critical + flag)
    if spirit_fractured and role_ids:
        try:
            # Remove current damage role if any
            if current_tier:
                current_role_id = role_ids.get(current_tier)
                if current_role_id:
                    current_role = guild.get_role(int(current_role_id))
                    if current_role and current_role in member.roles:
                        await member.remove_roles(current_role, reason="Armor integrity: spirit fractured")
            # Add critical role for fractured state
            critical_role_id = role_ids.get("critical")
            if critical_role_id:
                critical_role = guild.get_role(int(critical_role_id))
                if critical_role:
                    await member.add_roles(critical_role, reason="Armor integrity: spirit fractured")
        except Exception:
            pass

    await _b("_set_armor_state")(
        member.id,
        {
            "display_name": member.display_name,
            "points_since_blessing": 0,
            "damage_tier": new_tier if not spirit_fractured else "critical",  # Store as critical, flag as fractured
            "critical_aar_count": current_state.get("critical_aar_count", 0),
            "spirit_fractured": spirit_fractured or current_state.get("spirit_fractured", False),
            "last_blessing_timestamp": now.isoformat(),
            "blessing_timestamps": active_timestamps,
        },
    )

    return new_tier


async def _apply_blessing_normal(member: discord.Member, guild: discord.Guild) -> Optional[str]:
    """Apply normal blessing result: drop one damage tier.

    Returns the new damage tier (or None if now nominal).
    """
    current_tier = _b("_get_member_damage_tier")(member)

    if not current_tier:
        # Already nominal - just reset points and add timestamp
        await _clear_armor_damage(member, guild)
        return None

    # Drop one tier
    new_tier = await _drop_armor_tier(member, guild)

    # Get current state to preserve/update blessing timestamps
    current_state = await _b("_get_armor_state")(member.id)
    blessing_timestamps = current_state.get("blessing_timestamps", [])

    # Filter old timestamps and add current
    now = datetime.utcnow()
    cooldown_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < cooldown_window:
                active_timestamps.append(ts_str)
        except Exception:
            continue
    active_timestamps.append(now.isoformat())

    # Update state with new tier
    await _b("_set_armor_state")(
        member.id,
        {
            "display_name": member.display_name,
            "points_since_blessing": 0,
            "damage_tier": new_tier,
            "critical_aar_count": 0 if not new_tier else current_state.get("critical_aar_count", 0),
            "spirit_fractured": False if not new_tier else current_state.get("spirit_fractured", False),
            "last_blessing_timestamp": now.isoformat(),
            "blessing_timestamps": active_timestamps,
        },
    )

    return new_tier


async def _apply_blessing_crit_success(member: discord.Member, guild: discord.Guild, charges_invested: int = 1):
    """Apply crit success blessing result: full heal + grace period.

    Args:
        charges_invested: Number of charges used (1 for standard, 2-4 for intensive).
            Grace period scales with charges: -25 × charges_invested.

    Returns None (always results in nominal status).
    """
    grace_points = BLESSING_CRIT_SUCCESS_GRACE_POINTS * charges_invested
    await _clear_armor_damage(member, guild, grace_points=grace_points)
    return None


async def _apply_blessing_intensive_normal(member: discord.Member, guild: discord.Guild):
    """Apply intensive blessing normal result: full heal to nominal, no crit-success grace.

    Returns None (always results in nominal status).
    """
    await _clear_armor_damage(member, guild)
    return None


async def _check_spirit_fracture(user_id: int) -> bool:
    """Check if a user's machine spirit has fractured (should be replaced on blessing)."""
    state = await _b("_get_armor_state")(user_id)
    return state.get("spirit_fractured", False)


# ─────────────────────────────────────────────────────────────────────────────
# Blessing Pool (Techmarine daily blessing limits)
# ─────────────────────────────────────────────────────────────────────────────


def _load_blessing_pool() -> dict:
    """Load blessing pool data from disk."""
    try:
        if not os.path.exists(BLESSING_POOL_PATH):
            return {}
        with open(BLESSING_POOL_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_blessing_pool(data: dict):
    """Save blessing pool data to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(BLESSING_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_techmarine_pool_state(user_id: int) -> dict:
    """Get blessing pool state for a Techmarine."""
    try:
        async with _g.BLESSING_POOL_LOCK:
            data = _load_blessing_pool()
            state = data.get(str(user_id), {})
            # Initialize with defaults if empty
            if not state:
                return {
                    "remaining_blessings": BLESSING_POOL_MAX,
                    "blessing_timestamps": [],
                }
            return state
    except Exception:
        return {
            "remaining_blessings": BLESSING_POOL_MAX,
            "blessing_timestamps": [],
        }


async def _set_techmarine_pool_state(user_id: int, state: dict, display_name: str = None):
    """Update blessing pool state for a Techmarine."""
    try:
        async with _g.BLESSING_POOL_LOCK:
            data = _load_blessing_pool()
            if display_name:
                state["display_name"] = display_name
            data[str(user_id)] = state
            _save_blessing_pool(data)
    except Exception:
        pass


def _filter_active_blessing_timestamps(timestamps: List[str]) -> List[str]:
    """Return only the blessing timestamps still within the regen window.

    Malformed or unparseable entries are silently discarded.
    """
    now = datetime.utcnow()
    regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
    active = []
    for ts_str in timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if (now - ts).total_seconds() < regen_seconds:
                active.append(ts_str)
        except Exception:
            pass
    return active


def _calculate_regenerated_blessings(blessing_timestamps: List[str]) -> int:
    """Calculate how many blessings have regenerated based on timestamps.

    Each blessing regenerates after BLESSING_POOL_REGEN_HOURS (2.4h).
    Returns the number of blessings currently available.
    """
    on_cooldown = len(_filter_active_blessing_timestamps(blessing_timestamps))
    return max(0, BLESSING_POOL_MAX - on_cooldown)


async def _check_techmarine_can_bless(user_id: int) -> Tuple[bool, int, Optional[timedelta]]:
    """Check if a Techmarine can perform a blessing.

    Returns (can_bless, remaining_pool, time_until_next_regen).
    """
    state = await _b("_get_techmarine_pool_state")(user_id)
    timestamps = state.get("blessing_timestamps", [])

    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    # Trim to the most recent BLESSING_POOL_MAX entries to keep state bounded
    active_timestamps = active_timestamps[-BLESSING_POOL_MAX:]
    available = max(0, min(BLESSING_POOL_MAX - len(active_timestamps), BLESSING_POOL_MAX))

    if available > 0:
        return True, available, None

    # Calculate when next blessing will be available
    now = datetime.utcnow()
    regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
    oldest_ts = None
    for ts_str in active_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
        except Exception:
            pass

    if oldest_ts:
        time_until_regen = timedelta(seconds=regen_seconds) - (now - oldest_ts)
        if time_until_regen.total_seconds() > 0:
            return False, 0, time_until_regen

    return False, 0, timedelta(hours=BLESSING_POOL_REGEN_HOURS)


async def _get_blessing_pool_display(user_id: int) -> Tuple[int, Optional[timedelta]]:
    """Get blessing pool count and time until next regen (even if pool not empty).

    Returns (remaining_blessings, time_until_next_regen_or_None_if_full).
    """
    state = await _b("_get_techmarine_pool_state")(user_id)
    timestamps = state.get("blessing_timestamps", [])

    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    # Trim to the most recent BLESSING_POOL_MAX entries to keep state bounded
    active_timestamps = active_timestamps[-BLESSING_POOL_MAX:]
    available = max(0, min(BLESSING_POOL_MAX - len(active_timestamps), BLESSING_POOL_MAX))

    # If pool is full, no regen time needed
    if available >= BLESSING_POOL_MAX:
        return available, None

    # Calculate when next blessing will regenerate (oldest timestamp)
    now = datetime.utcnow()
    regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
    oldest_ts = None
    for ts_str in active_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
        except Exception:
            pass
    if oldest_ts:
        time_until_regen = timedelta(seconds=regen_seconds) - (now - oldest_ts)
        if time_until_regen.total_seconds() > 0:
            return available, time_until_regen

    return available, None


async def _consume_blessing(user_id: int, display_name: str = None):
    """Record that a Techmarine has used a blessing."""
    state = await _b("_get_techmarine_pool_state")(user_id)
    timestamps = state.get("blessing_timestamps", [])

    now = datetime.utcnow()
    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    # Trim to most recent (BLESSING_POOL_MAX - 1) entries before adding the new one,
    # to keep the list bounded and prevent the pool from going negative.
    active_timestamps = active_timestamps[-(BLESSING_POOL_MAX - 1) :]

    # Add current blessing timestamp
    active_timestamps.append(now.isoformat())

    await _b("_set_techmarine_pool_state")(
        user_id,
        {
            "remaining_blessings": max(0, BLESSING_POOL_MAX - len(active_timestamps)),
            "blessing_timestamps": active_timestamps,
        },
        display_name=display_name,
    )


def _get_intensive_charge_cost(damage_tier: Optional[str], spirit_fractured: bool) -> int:
    """Get the number of charges required for an intensive blessing.

    `spirit_fractured` takes priority and always returns 4 regardless of
    `damage_tier`.  Returns 0 when `damage_tier` is nominal (i.e. not in
    INTENSIVE_BLESSING_COSTS) and `spirit_fractured` is False.
    """
    if spirit_fractured:
        return INTENSIVE_BLESSING_COSTS.get("fractured", 4)
    return INTENSIVE_BLESSING_COSTS.get(damage_tier, 0)


async def _get_techmarine_available_charges(user_id: int) -> int:
    """Get the number of available blessing charges for a Techmarine."""
    state = await _b("_get_techmarine_pool_state")(user_id)
    timestamps = state.get("blessing_timestamps", [])
    active_count = len(_filter_active_blessing_timestamps(timestamps))
    return max(0, BLESSING_POOL_MAX - active_count)


async def _consume_multiple_blessings(user_id: int, count: int, display_name: str = None):
    """Record that a Techmarine has used multiple blessings at once.

    Used for intensive blessings which consume 2-4 charges.
    """
    if count <= 0:
        return

    state = await _b("_get_techmarine_pool_state")(user_id)
    timestamps = state.get("blessing_timestamps", [])

    now = datetime.utcnow()
    active_timestamps = _filter_active_blessing_timestamps(timestamps)

    # Stagger timestamps by one regen interval each so charges recharge
    # one at a time rather than all simultaneously.
    regen_delta = timedelta(hours=BLESSING_POOL_REGEN_HOURS)
    for i in range(count):
        staggered_ts = now + regen_delta * i
        active_timestamps.append(staggered_ts.isoformat())

    # Trim to BLESSING_POOL_MAX entries to keep bounded
    active_timestamps = active_timestamps[-BLESSING_POOL_MAX:]

    await _b("_set_techmarine_pool_state")(
        user_id,
        {
            "remaining_blessings": max(0, BLESSING_POOL_MAX - len(active_timestamps)),
            "blessing_timestamps": active_timestamps,
        },
        display_name=display_name,
    )


async def _check_recipient_cooldown(user_id: int) -> Tuple[bool, Optional[timedelta], int, Optional[str]]:
    """Check if a recipient can receive a blessing (max 3 per 24h, 4h between each).

    Returns (can_receive, time_until_next_slot, blessings_used, block_reason).
    block_reason is None if can_receive, 'per_blessing' for 4h cooldown, 'daily_cap' for 3/day limit.
    """
    state = await _b("_get_armor_state")(user_id)
    blessing_timestamps = state.get("blessing_timestamps", [])

    # Also check legacy field for backwards compatibility
    if not blessing_timestamps:
        last_blessing = state.get("last_blessing_timestamp")
        if last_blessing:
            blessing_timestamps = [last_blessing]

    if not blessing_timestamps:
        return True, None, 0, None

    now = datetime.utcnow()
    daily_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    per_blessing_window = timedelta(hours=BLESSING_RECIPIENT_PER_BLESSING_COOLDOWN_HOURS)

    # Filter to timestamps within the last 24h for daily cap
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < daily_window:
                active_timestamps.append(ts)
        except Exception:
            continue

    blessings_used = len(active_timestamps)

    # Check per-blessing cooldown first (most recent blessing must be 4h+ ago)
    if active_timestamps:
        most_recent = max(active_timestamps)
        time_since_last = now - most_recent
        if time_since_last < per_blessing_window:
            time_until_next = per_blessing_window - time_since_last
            return False, time_until_next, blessings_used, "per_blessing"

    # Check daily cap
    if blessings_used >= BLESSING_RECIPIENT_MAX_PER_DAY:
        # At max - find when the oldest one expires
        oldest = min(active_timestamps)
        time_until_slot = (oldest + daily_window) - now
        return False, time_until_slot, blessings_used, "daily_cap"

    return True, None, blessings_used, None


def _roll_blessing_outcome(
    damage_tier: Optional[str] = None,
    spirit_fractured: bool = False,
) -> str:
    """Roll for blessing outcome based on armor state.

    Probabilities vary by state (asymmetric spread):
    - Nominal: 1% fail / 98% normal / 1% crit
    - Damaged: 3% fail / 94% normal / 3% crit
    - Compromised: 5% fail / 90% normal / 5% crit
    - Critical: 8% fail / 86% normal / 6% crit (asymmetric - less punishing)
    - Fractured: 10% fail / 80% normal / 10% crit

    Returns one of: 'crit_fail', 'normal', 'crit_success'
    """
    import random

    # Determine which probability set to use
    if spirit_fractured:
        state_key = "fractured"
    else:
        state_key = damage_tier  # None, "damaged", "compromised", or "critical"

    # Get probabilities for this state (fallback to nominal)
    crit_fail_chance, crit_success_chance = BLESSING_ROLL_PROBABILITIES.get(
        state_key, BLESSING_ROLL_PROBABILITIES[None]
    )

    roll = random.random()

    if roll < crit_fail_chance:
        return "crit_fail"
    elif roll >= (1.0 - crit_success_chance):
        return "crit_success"
    else:
        return "normal"


async def _drop_armor_tier(member: discord.Member, guild: discord.Guild) -> Optional[str]:
    """Drop a member's armor damage by one tier.

    Returns the new tier (or None if now undamaged).
    Tier progression: critical -> compromised -> damaged -> None (nominal)
    """
    current_tier = _b("_get_member_damage_tier")(member)
    role_ids = _b("_get_armor_damage_role_ids")()

    if not current_tier:
        return None  # Already undamaged

    # Remove current tier role
    current_role_id = role_ids.get(current_tier)
    if current_role_id:
        try:
            role = guild.get_role(int(current_role_id))
            if role and role in member.roles:
                await member.remove_roles(role, reason="Armor integrity: blessing reduced damage tier")
        except Exception:
            pass

    # Determine new tier (one level better)
    tier_order = ["damaged", "compromised", "critical"]
    try:
        current_idx = tier_order.index(current_tier)
        if current_idx == 0:
            # Was damaged, now nominal
            return None
        else:
            # Drop one tier
            new_tier = tier_order[current_idx - 1]
            new_role_id = role_ids.get(new_tier)
            if new_role_id:
                try:
                    new_role = guild.get_role(int(new_role_id))
                    if new_role:
                        await member.add_roles(new_role, reason="Armor integrity: blessing reduced damage tier")
                except Exception:
                    pass
            return new_tier
    except ValueError:
        return None


def _format_cooldown_time(td: timedelta) -> str:
    """Format a timedelta as 'Xh Ym' or 'Ym' if under an hour."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ─────────────────────────────────────────────────────────────────────────────
# Forge Requisition Pool (Community armory -> blessing charges)
# ─────────────────────────────────────────────────────────────────────────────


def _load_forge_pool() -> dict:
    """Load forge requisition pool data from disk."""
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    default = {"balance": max_balance, "daily_usage": {}}
    try:
        if not os.path.exists(FORGE_POOL_PATH):
            return default
        with open(FORGE_POOL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        # Migration: if old format (total_spent) exists, convert to balance
        if "balance" not in data and "total_spent" in data:
            # Start at max, already spent some
            data["balance"] = max(0, max_balance - data.get("total_spent", 0))
        elif "balance" not in data:
            data["balance"] = max_balance
        return data
    except Exception:
        return default


def _save_forge_pool(data: dict):
    """Save forge requisition pool data to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FORGE_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Forge Chronicle (Immersive Armor Channel Data)
# ─────────────────────────────────────────────────────────────────────────────


def _load_forge_chronicle() -> dict:
    """Load forge chronicle data from disk."""
    default = {
        "pending_alerts": {},
        "rite_history": [],
        "techmarine_stats": {},
        "dashboard_message_id": None,
        "last_ambient_ts": None,
    }
    try:
        if not os.path.exists(FORGE_CHRONICLE_PATH):
            return default.copy()
        with open(FORGE_CHRONICLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Merge with defaults to handle missing keys
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        return default.copy()


def _save_forge_chronicle(data: dict):
    """Save forge chronicle data to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FORGE_CHRONICLE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _store_pending_alert(user_id: int, message_id: int, channel_id: int):
    """Store a pending armor alert for thread reply tracking."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        data.setdefault("pending_alerts", {})
        data["pending_alerts"][str(user_id)] = {
            "message_id": message_id,
            "channel_id": channel_id,
            "ts": datetime.utcnow().isoformat(),
        }
        _b("_save_forge_chronicle")(data)


async def _get_pending_alert(user_id: int) -> Optional[dict]:
    """Get pending alert info for a user (if any)."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        return data.get("pending_alerts", {}).get(str(user_id))


async def _clear_pending_alert(user_id: int):
    """Clear a pending alert for a user (no-op if not stored)."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        key = str(user_id)
        if key in data.get("pending_alerts", {}):
            data["pending_alerts"].pop(key)
            _b("_save_forge_chronicle")(data)


async def _record_rite_in_chronicle(
    bearer_id: int,
    techmarine_id: int,
    rite_type: str,
    spirit_designation: str,
    spirit_event: str,
):
    """Record a forge rite in the chronicle for dashboard stats.

    Args:
        bearer_id: User ID of the brother blessed
        techmarine_id: User ID of the attesting Techmarine
        rite_type: "standard" or "intensive"
        spirit_designation: The machine spirit ID
        spirit_event: "first_binding", "rebirth", "restoration", "maintenance"
    """
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()

        # Add to rite history (keep last 500 entries)
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "bearer_id": str(bearer_id),
            "techmarine_id": str(techmarine_id),
            "rite_type": rite_type,
            "spirit": spirit_designation,
            "event": spirit_event,
        }
        data["rite_history"].append(entry)
        if len(data["rite_history"]) > 500:
            data["rite_history"] = data["rite_history"][-500:]

        # Update techmarine stats
        tech_key = str(techmarine_id)
        if tech_key not in data["techmarine_stats"]:
            data["techmarine_stats"][tech_key] = {
                "total_rites": 0,
                "successes": 0,
                "first_bindings": 0,
                "rebirths": 0,
            }
        data["techmarine_stats"][tech_key]["total_rites"] += 1
        # Track successes (anything except resisted)
        if spirit_event != "resisted":
            data["techmarine_stats"][tech_key].setdefault("successes", 0)
            data["techmarine_stats"][tech_key]["successes"] += 1
        if spirit_event == "first_binding":
            data["techmarine_stats"][tech_key]["first_bindings"] += 1
        elif spirit_event == "rebirth":
            data["techmarine_stats"][tech_key]["rebirths"] += 1

        _b("_save_forge_chronicle")(data)


async def _record_spirit_released(bearer_id: int, spirit_designation: str, age_days: int = 0):
    """Record a spirit release (member went inactive) in the chronicle.

    This creates a 'released' event in rite_history for the memorial.
    No techmarine is involved - this is an automatic system event.
    """
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()

        # Guard: skip if this spirit already has a recent released/fractured entry (within 10 min)
        now_dt = datetime.utcnow()
        for r in reversed(data["rite_history"]):
            if (
                r.get("bearer_id") == str(bearer_id)
                and r.get("spirit") == spirit_designation
                and r.get("event") in ("released", "fractured")
            ):
                try:
                    existing_ts = datetime.fromisoformat(r["ts"])
                    if (now_dt - existing_ts).total_seconds() < 600:
                        return
                except Exception:
                    pass
                break

        entry = {
            "ts": now_dt.isoformat(),
            "bearer_id": str(bearer_id),
            "techmarine_id": None,
            "rite_type": None,
            "spirit": spirit_designation,
            "event": "released",
            "age_days": age_days,
        }
        data["rite_history"].append(entry)
        if len(data["rite_history"]) > 500:
            data["rite_history"] = data["rite_history"][-500:]

        _b("_save_forge_chronicle")(data)


async def _record_spirit_fractured(bearer_id: int, spirit_designation: str, age_days: int):
    """Record a spirit fracture in the chronicle.

    This creates a 'fractured' event in rite_history for the memorial.
    The spirit is lost - will require a new binding.
    """
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()

        # Guard: skip if this spirit already has a recent fractured/released entry (within 10 min)
        now_dt = datetime.utcnow()
        for r in reversed(data["rite_history"]):
            if (
                r.get("bearer_id") == str(bearer_id)
                and r.get("spirit") == spirit_designation
                and r.get("event") in ("fractured", "released")
            ):
                try:
                    existing_ts = datetime.fromisoformat(r["ts"])
                    if (now_dt - existing_ts).total_seconds() < 600:
                        return
                except Exception:
                    pass
                break

        entry = {
            "ts": now_dt.isoformat(),
            "bearer_id": str(bearer_id),
            "techmarine_id": None,
            "rite_type": None,
            "spirit": spirit_designation,
            "event": "fractured",
            "age_days": age_days,
        }
        data["rite_history"].append(entry)
        if len(data["rite_history"]) > 500:
            data["rite_history"] = data["rite_history"][-500:]

        _b("_save_forge_chronicle")(data)


async def _get_dashboard_message_id() -> Optional[int]:
    """Get the stored dashboard message ID (if any)."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        msg_id = data.get("dashboard_message_id")
        return int(msg_id) if msg_id else None


async def _set_dashboard_message_id(message_id: int):
    """Store the dashboard message ID."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        data["dashboard_message_id"] = message_id
        _b("_save_forge_chronicle")(data)


async def _get_last_ambient_ts() -> Optional[datetime]:
    """Get the timestamp of the last ambient message."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        ts_str = data.get("last_ambient_ts")
        if ts_str:
            try:
                return datetime.fromisoformat(ts_str)
            except Exception:
                pass
        return None


async def _set_last_ambient_ts():
    """Update the timestamp of the last ambient message."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()
        data["last_ambient_ts"] = datetime.utcnow().isoformat()
        _b("_save_forge_chronicle")(data)


async def _increment_forge_pool_balance(points: int):
    """Add armory points to the forge pool balance (capped at max)."""
    if points <= 0:
        return
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    async with _g.FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        current = pool_data.get("balance", max_balance)
        pool_data["balance"] = min(current + points, max_balance)
        _save_forge_pool(pool_data)


async def _deduct_forge_pool_balance(points: int, tier: Optional[str] = None):
    """Deduct points from forge pool balance and log the drain.

    Args:
        points: Forge points to deduct
        tier: Damage tier being healed (for tracking)
    """
    if points <= 0:
        return
    async with _g.FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        current = pool_data.get("balance", 0)
        pool_data["balance"] = max(0, current - points)

        # Log drain for weekly tracking
        drain_log = pool_data.get("weekly_drain_log", [])
        drain_log.append(
            {
                "ts": datetime.utcnow().isoformat(),
                "points": points,
                "tier": tier,
            }
        )
        # Keep only last 30 days of drain history
        cutoff = datetime.utcnow() - timedelta(days=30)
        drain_log = [entry for entry in drain_log if datetime.fromisoformat(entry.get("ts", "")) >= cutoff]
        pool_data["weekly_drain_log"] = drain_log

        _save_forge_pool(pool_data)


async def _get_forge_pool_available() -> int:
    """Get the number of armory points available in the community forge pool."""
    async with _g.FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        return pool_data.get("balance", FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE)


async def _get_techmarine_daily_requisitions(user_id: int) -> int:
    """Get how many requisitions a Techmarine has used today."""
    async with _g.FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        daily_usage = pool_data.get("daily_usage", {})

        today = datetime.utcnow().strftime("%Y-%m-%d")
        user_data = daily_usage.get(str(user_id), {})

        # Check if the usage is from today
        if user_data.get("date") == today:
            return user_data.get("count", 0)
        return 0


async def _consume_forge_requisition(user_id: int) -> Tuple[bool, str]:
    """Attempt to consume a forge requisition for a Techmarine.

    Returns (success, message).
    """
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    async with _g.FORGE_POOL_LOCK:
        # Check daily limit
        pool_data = _load_forge_pool()
        daily_usage = pool_data.get("daily_usage", {})

        today = datetime.utcnow().strftime("%Y-%m-%d")
        user_data = daily_usage.get(str(user_id), {})

        # Reset if different day
        if user_data.get("date") != today:
            user_data = {"date": today, "count": 0}

        if user_data.get("count", 0) >= FORGE_POOL_DAILY_LIMIT:
            return False, f"Daily requisition limit reached ({FORGE_POOL_DAILY_LIMIT} per day)."

        # Check pool availability (balance-based)
        balance = pool_data.get("balance", max_balance)

        if balance < FORGE_POOL_COST_PER_CHARGE:
            return (
                False,
                f"Insufficient forge supplies ({balance}/{FORGE_POOL_COST_PER_CHARGE} armory points available).",
            )

        # Consume from balance
        pool_data["balance"] = balance - FORGE_POOL_COST_PER_CHARGE
        user_data["count"] = user_data.get("count", 0) + 1
        daily_usage[str(user_id)] = user_data
        pool_data["daily_usage"] = daily_usage

        _save_forge_pool(pool_data)

        return True, f"Requisition approved. Forge pool: {pool_data['balance']} armory points remaining."


async def _get_forge_pool_status() -> dict:
    """Get full forge pool status for display."""
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    async with _g.FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        balance = pool_data.get("balance", max_balance)
        charges_available = balance // FORGE_POOL_COST_PER_CHARGE

        return {
            "available": balance,
            "charges_available": charges_available,
            "cost_per_charge": FORGE_POOL_COST_PER_CHARGE,
            "max_charges": FORGE_POOL_MAX_CHARGES,
        }


async def _post_armor_alert(
    member: discord.Member,
    tier: str,
    critical_aar_count: int = 0,
    guild: Optional[discord.Guild] = None,
    op_mission: Optional[str] = None,
    op_difficulty_class: Optional[str] = None,
    op_url: Optional[str] = None,
    squad_member_ids: Optional[List[str]] = None,
    alert_type: str = "sustained",
    penalty_amount: int = 0,
):
    """Post an armor damage alert to the arming chamber channel.

    Args:
        member: The brother whose armor was damaged
        tier: Damage tier (damaged, compromised, critical, fractured)
        critical_aar_count: Number of AARs at critical (for fracture warning)
        guild: Discord guild
        op_mission: Mission name from the AAR that triggered the damage
        op_difficulty_class: Difficulty class (e.g., normal_siege, hard_siege) for planet lookup
        op_url: Jump URL to the AAR message
        squad_member_ids: List of brother IDs on the same op (for debrief)
        alert_type: "sustained" (penalty applied, AAR loss) or "detected" (early warning)
        penalty_amount: How many AAR points were lost (for sustained alerts)
    """
    channel_id = _get_arming_chamber_channel_id()
    if not channel_id:
        return

    guild = guild or member.guild
    if not guild:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    config = _get_armor_config()
    fracture_threshold = config.get("fracture_threshold", DEFAULT_ARMOR_FRACTURE_THRESHOLD)

    # Get bearer info using the same pattern as forge_rite/stud announcements
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(member)
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()

    # Service studs computation
    bearer_studs = _compute_member_service_studs(member)

    # Machine spirit designation
    machine_spirit = await _get_machine_spirit(int(member.id))

    # Home chapter (lineage)
    bearer_chapter = _get_bearer_home_chapter(member)
    chapter_emoji = _get_emoji_by_name(guild, bearer_chapter) if bearer_chapter and guild else None

    # Get rank emoji
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""

    # Build bearer display string (matching forge_rite style)
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_display = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_display = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"

    if bearer_title:
        bearer_display += f"\n*{bearer_title}*"
    # Lineage (home chapter)
    if bearer_chapter and bearer_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        if bearer_chapter == "Black Shield":
            bearer_display += f"\nLineage: {chapter_prefix}REDACTED"
        else:
            bearer_display += f"\nLineage: {chapter_prefix}{bearer_chapter}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_display += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    # Machine spirit
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    if machine_spirit:
        bearer_display += f"\n{machine_spirit_emoji} `{machine_spirit}`"
    else:
        bearer_display += f"\n{machine_spirit_emoji} *UNBOUND*"

    # Determine embed color, title, and description based on tier and alert_type
    is_detection = alert_type == "detected"

    # Build penalty string for sustained alerts
    penalty_str = f" (-{penalty_amount} AAR)" if penalty_amount > 0 else ""

    if tier == "fractured":
        color = 0x8B0000  # Dark red
        title = "᛭⋅ MACHINE SPIRIT FRACTURED ⋅᛭"
        description = "*The bond is broken — immediate re-consecration required*"
    elif tier == "critical":
        if is_detection:
            color = 0xE74C3C  # Red
            title = "᛭⋅ CRITICAL DAMAGE DETECTED ⋅᛭"
            description = "*Machine spirit strains — intervention window open*"
        else:
            color = 0xE74C3C  # Red
            title = f"᛭⋅ CRITICAL ARMOR FAILURE ⋅᛭{penalty_str}"
            description = "*AAR points lost due to machine spirit instability*"
    elif tier == "compromised":
        if is_detection:
            color = 0xF39C12  # Dark orange/amber
            title = "᛭⋅ INTEGRITY DEGRADATION DETECTED ⋅᛭"
            description = "*Structural stress detected — maintenance window open*"
        else:
            color = 0xE67E22  # Orange
            title = f"᛭⋅ ARMOR INTEGRITY COMPROMISED ⋅᛭{penalty_str}"
            description = "*AAR points lost due to structural damage*"
    else:  # damaged
        if is_detection:
            color = 0xF1C40F  # Yellow
            title = "᛭⋅ WEAR DETECTED ⋅᛭"
            description = "*Minor degradation noted — preventive maintenance available*"
        else:
            color = 0xE67E22  # Orange
            title = f"᛭⋅ ARMOR INTEGRITY ALERT ⋅᛭{penalty_str}"
            description = "*AAR points lost due to armor wear*"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    # Affected brother field with proper rank display
    tier_display = tier.title() if tier else "Unknown"
    spirit_fractured = tier == "fractured"
    penalty_risk = _get_tier_risk_display(tier, spirit_fractured=spirit_fractured)

    # Adjust status display for detection alerts
    if is_detection:
        status_label = f"{tier_display} (Early Warning)"
    else:
        status_label = tier_display

    embed.add_field(
        name="▸ Affected Brother",
        value=f"{bearer_display}\n**Status:** {status_label}\n**Penalty Risk:** {penalty_risk}",
        inline=False,
    )

    # Debrief field (if op context provided)

    # Debrief field (if op context provided)
    if op_mission or op_difficulty_class or op_url or squad_member_ids:
        debrief_lines = []
        # Look up planet name - check difficulty_class first (for siege ops), then mission
        # Strip any mentions from mission name for lookup
        planet = None
        clean_mission = None
        if op_mission:
            # Strip role/user mentions from mission name (e.g., "Vortex @Black Laurels" -> "Vortex")
            clean_mission = re.sub(r"\s*<@[!&]?\d+>.*$", "", op_mission).strip()
            # Also strip any text after @ if @ is present (fallback for resolved mentions)
            if "@" in clean_mission:
                clean_mission = clean_mission.split("@")[0].strip()
        if op_difficulty_class:
            planet = MISSION_TO_PLANET.get(op_difficulty_class.lower().strip())
        if not planet and clean_mission:
            planet = MISSION_TO_PLANET.get(clean_mission.lower().strip())

        if planet:
            debrief_lines.append(f"Integrity degraded during deployment to **{planet}**")
        elif clean_mission:
            debrief_lines.append(f"Integrity degraded during **{clean_mission}** deployment")

        # Build squad list (exclude the affected brother)
        if squad_member_ids and guild:
            squad_names = []
            for sid in squad_member_ids:
                if str(sid) == str(member.id):
                    continue  # Skip the affected brother
                try:
                    squad_member = guild.get_member(int(sid))
                    if squad_member:
                        # Get display name stripped of pips
                        name = _strip_display_name(squad_member.display_name)
                        squad_names.append(name)
                except Exception:
                    pass
            if squad_names:
                debrief_lines.append(f"Kill Team: {', '.join(squad_names)}")

        if op_url:
            debrief_lines.append(f"[View After Action Report]({op_url})")

        if debrief_lines:
            embed.add_field(
                name="▸ Debrief",
                value="\n".join(debrief_lines),
                inline=False,
            )

    # Warning field for critical/fractured and response guidance
    if tier == "fractured":
        embed.add_field(
            name="▸ Emergency",
            value="⚠️ Machine spirit has **FRACTURED**. No further field operations until re-consecration.",
            inline=False,
        )
        embed.add_field(
            name="▸ Immediate Techmarine Response Required",
            value="Administer intensive blessing via `/forge_rite intensive:True` to re-consecrate the spirit.",
            inline=False,
        )
    elif tier == "critical":
        remaining = fracture_threshold - critical_aar_count
        embed.add_field(
            name="▸ Warning",
            value=f"⚠️ AAR submissions until spirit fracture: **{remaining}**",
            inline=False,
        )
        if is_detection:
            embed.add_field(
                name="▸ Intervention Window Open",
                value="Brother is still operational. Administer blessing via `/forge_rite` before penalties accumulate.",
                inline=False,
            )
        else:
            embed.add_field(
                name="▸ Immediate Techmarine Response Required",
                value="Administer blessing via `/forge_rite` to preserve machine spirit bond.",
                inline=False,
            )
    else:
        if is_detection:
            embed.add_field(
                name="▸ Preventive Maintenance Available",
                value="Damage detected before penalty. Administer blessing via `/forge_rite` to prevent AAR losses.",
                inline=False,
            )
        else:
            embed.add_field(
                name="▸ Techmarine Response Required",
                value="Administer blessing via `/forge_rite` to restore armor integrity.",
                inline=False,
            )

    # Build message content with Techmarine ping BEFORE the embed
    # Only ping techmarine role, not the affected brother
    content = ""
    tech_role_id = _get_techmarine_role_id()
    if tech_role_id:
        content = f"<@&{tech_role_id}>"

    _g.logger.debug(
        f"Armor alert for {member.display_name}: tier={tier}, alert_type={alert_type}, "
        f"bearer_display_len={len(bearer_display)}, embed_fields={len(embed.fields)}, "
        f"content_len={len(content)}"
    )

    # Check bot permissions
    perms = channel.permissions_for(channel.guild.me)
    if not perms.embed_links:
        _g.logger.error(f"Bot lacks 'Embed Links' permission in channel {channel.name}")
    if not perms.send_messages:
        _g.logger.error(f"Bot lacks 'Send Messages' permission in channel {channel.name}")

    try:
        sent_msg = await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        # Verify embed was actually sent
        if not sent_msg.embeds:
            _g.logger.warning(
                f"Armor alert sent but embed was dropped! embed_links={perms.embed_links}, content={content[:50]}"
            )
        else:
            _g.logger.info(f"Posted armor alert for {member.display_name} (tier={tier}, type={alert_type})")
            # Trigger chronicle repost at bottom after alert
            await _repost_chronicle_at_bottom(guild)
    except Exception as e:
        _g.logger.error(f"Failed to post armor alert for {member.display_name}: {e}")


# ---------------------------------------------------------------------------
# Forge / Armor subsystem override (kill switch)
# ---------------------------------------------------------------------------

def _load_forge_override() -> dict:
    try:
        if not os.path.exists(FORGE_OVERRIDE_PATH):
            return {"enabled": True}
        with open(FORGE_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {"enabled": True}
    except Exception:
        return {"enabled": True}


def _save_forge_override(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FORGE_OVERRIDE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _is_forge_enabled() -> bool:
    try:
        async with _g.FORGE_OVERRIDE_LOCK:
            return bool(_load_forge_override().get("enabled", True))
    except Exception:
        return True


async def _process_armor_integrity_for_aar(
    brother_id: str,
    base_points: int,
    guild: discord.Guild,
    armor_batch: Optional[dict] = None,
    op_mission: Optional[str] = None,
    op_difficulty_class: Optional[str] = None,
    op_url: Optional[str] = None,
    squad_member_ids: Optional[List[str]] = None,
    actual_penalty: int = 0,
) -> Tuple[int, Optional[dict]]:
    """Process armor integrity for a single brother in an AAR.

    Args:
        brother_id: Discord user ID string
        base_points: Base AAR points for this brother (before penalties)
        guild: Discord guild for role operations
        armor_batch: Optional pre-loaded armor data dict for batch processing.
                     If provided, state is read/written to this dict (no file I/O).
                     If None, uses individual file I/O per call.
        op_mission: Mission name from the AAR (for debrief in alerts)
        op_difficulty_class: Difficulty class (e.g., normal_siege) for planet lookup
        op_url: Jump URL to the AAR message (for debrief in alerts)
        squad_member_ids: List of all brother IDs in this AAR (for debrief in alerts)
        actual_penalty: The penalty that was actually applied to this AAR (0 = no loss)

    Returns:
        Tuple of (penalty_amount, alert_info_or_none)
        alert_info is a dict with member, tier, critical_count, alert_type, and op context if an alert should be posted.
        alert_type is "sustained" (penalty applied, AAR loss) or "detected" (early warning, no loss yet).
    """
    alert_info = None
    penalty = 0

    # Honor forge subsystem kill switch (parity with librarius override).
    if not await _is_forge_enabled():
        return 0, None

    try:
        member = guild.get_member(int(brother_id))
        if not member:
            return 0, None

        # Must be an active participant (ranked, non-Reserves, non-Interred).
        # Symmetric with the warp AAR hook in librarius_ops.
        is_active_fn = _b("_is_active_participant")
        if is_active_fn:
            if not is_active_fn(member):
                return 0, None
        else:
            # Fallback: any rank role (legacy behavior)
            if not any(r.name in RANK_HONORIFICS for r in member.roles):
                return 0, None

        # Check current damage tier from roles
        current_tier = _b("_get_member_damage_tier")(member)
        penalty = _b("_get_damage_penalty")(current_tier)

        # Get user stats for grace period check
        stats = _b("compute_stats_for_user")(str(brother_id))
        total_aar_points = int(stats.get("aar_points", 0) or 0)

        # Check grace period
        if not _b("_check_armor_grace_period")(member, total_aar_points):
            return penalty, None

        # Get current armor state (from batch if provided, else from file)
        if armor_batch is not None:
            state = _get_armor_state_from_batch(int(brother_id), armor_batch)
        else:
            state = await _b("_get_armor_state")(int(brother_id))

        # Update display name for data file readability
        state["display_name"] = member.display_name

        # Check for spirit fracture
        spirit_fractured = state.get("spirit_fractured", False)
        effective_tier = "fractured" if spirit_fractured else current_tier

        # Accumulate points (use base unpenalized points for tracking)
        state["points_since_blessing"] = state.get("points_since_blessing", 0) + base_points

        # Check if damage occurs (escalation)
        damage_occurred = await _b("_run_armor_integrity_check")(state["points_since_blessing"])

        new_tier = None
        if damage_occurred:
            # Roll which damage tier to apply based on current points
            rolled_tier = _b("_roll_damage_tier")(state["points_since_blessing"])
            new_tier = await _b("_apply_damage_tier")(member, guild, current_tier, rolled_tier)
            if new_tier and new_tier != current_tier:
                state["damage_tier"] = new_tier
                if new_tier == "critical":
                    state["critical_aar_count"] = 0  # Reset on entering critical

        # Sustained alert: fires when brother actually lost AAR points (penalty > 0)
        if actual_penalty > 0 and effective_tier:
            alert_info = {
                "member": member,
                "tier": effective_tier,
                "critical_count": state.get("critical_aar_count", 0),
                "alert_type": "sustained",
                "op_mission": op_mission,
                "op_difficulty_class": op_difficulty_class,
                "op_url": op_url,
                "squad_member_ids": squad_member_ids,
                "penalty_amount": actual_penalty,
            }
            # Update detection tracking since we're alerting for this tier
            state["last_detection_alert_tier"] = effective_tier

        # Detection alert: fires when damaged but no penalty this AAR (early warning)
        if alert_info is None and effective_tier and actual_penalty == 0:
            last_detection_tier = state.get("last_detection_alert_tier")

            # Tier severity for comparison
            tier_severity = {"damaged": 1, "compromised": 2, "critical": 3, "fractured": 4}
            current_severity = tier_severity.get(effective_tier, 0)
            last_severity = tier_severity.get(last_detection_tier, 0)

            # Only roll detection if we haven't already alerted for this tier level or higher
            if current_severity > last_severity:
                if _b("_roll_detection_alert")(effective_tier):
                    alert_info = {
                        "member": member,
                        "tier": effective_tier,
                        "critical_count": state.get("critical_aar_count", 0),
                        "alert_type": "detected",
                        "op_mission": op_mission,
                        "op_difficulty_class": op_difficulty_class,
                        "op_url": op_url,
                        "squad_member_ids": squad_member_ids,
                    }
                    # Update detection tracking
                    state["last_detection_alert_tier"] = effective_tier

        # If at critical (whether damage occurred or not), increment fracture countdown
        if current_tier == "critical":
            state["critical_aar_count"] = state.get("critical_aar_count", 0) + 1
            config = _get_armor_config()
            fracture_threshold = config.get("fracture_threshold", DEFAULT_ARMOR_FRACTURE_THRESHOLD)

            if state["critical_aar_count"] >= fracture_threshold:
                # Spirit fractures
                state["spirit_fractured"] = True

                # Record fracture in chronicle with spirit age
                spirits_data = _load_machine_spirits()
                spirit_info = spirits_data.get(str(brother_id), {})
                spirit_name = spirit_info.get("designation") if isinstance(spirit_info, dict) else spirit_info
                age_days = 0
                if isinstance(spirit_info, dict) and spirit_info.get("bound_ts"):
                    try:
                        bound_dt = datetime.fromisoformat(spirit_info["bound_ts"])
                        age_days = (datetime.utcnow() - bound_dt).days
                    except Exception:
                        pass
                if spirit_name:
                    await _record_spirit_fractured(int(brother_id), spirit_name, age_days)

                # Guaranteed alert for fracture
                if alert_info is None or alert_info.get("tier") != "fractured":
                    alert_info = {
                        "member": member,
                        "tier": "fractured",
                        "critical_count": state["critical_aar_count"],
                        "alert_type": "sustained",
                        "op_mission": op_mission,
                        "op_difficulty_class": op_difficulty_class,
                        "op_url": op_url,
                        "squad_member_ids": squad_member_ids,
                    }

        # Save updated state (to batch if provided, else to file)
        if armor_batch is not None:
            _set_armor_state_in_batch(int(brother_id), state, armor_batch)
        else:
            await _b("_set_armor_state")(int(brother_id), state)

        return penalty, alert_info

    except Exception:
        return penalty, None


def _get_armor_status_for_blessing(
    was_damaged: bool,
    damage_tier: Optional[str],
    spirit_fractured: bool,
) -> dict:
    """Get the status line values for a forge_rite blessing based on armor state."""
    if spirit_fractured:
        return ARMOR_STATUS_FRACTURED
    elif damage_tier == "critical":
        return ARMOR_STATUS_CRITICAL
    elif damage_tier == "compromised":
        return ARMOR_STATUS_COMPROMISED
    elif damage_tier == "damaged" or was_damaged:
        return ARMOR_STATUS_DAMAGED
    else:
        return ARMOR_STATUS_NOMINAL


def _classify_forge_rite_event(
    spirit_is_first: bool,
    spirit_is_reconsecrated: bool,
    spirit_is_restored: bool,
) -> tuple:
    """Classify a forge rite into a verbosity tier and a chronicle event type.

    Returns (is_significant, spirit_event) where:
    - is_significant (bool): True when a full embed with @mention should be sent
      (first binding or rebirth), False for routine compact-format responses.
    - spirit_event (str): one of "first_binding", "rebirth", "restoration",
      "maintenance" — used to record the event in the forge chronicle.
    """
    is_significant = spirit_is_first or spirit_is_reconsecrated
    if spirit_is_first:
        spirit_event = "first_binding"
    elif spirit_is_reconsecrated:
        spirit_event = "rebirth"
    elif spirit_is_restored:
        spirit_event = "restoration"
    else:
        spirit_event = "maintenance"
    return is_significant, spirit_event


# ─────────────────────────────────────────────────────────────────────────────
# LFG Queue System - Sign-up queues for operations and omega missions
# ─────────────────────────────────────────────────────────────────────────────


def _get_lfg_config() -> dict:
    """Get LFG configuration from config.json, with defaults."""
    return _g.CONFIG.get("lfg") or {}


def _get_lfg_pc_role_id() -> int:
    """Get PC Player role ID from config or default."""
    cfg = _get_lfg_config()
    return int(cfg.get("pc_player_role_id") or LFG_PC_PLAYER_ROLE_ID_DEFAULT)


def _get_lfg_console_role_id() -> int:
    """Get Console Player role ID from config or default."""
    cfg = _get_lfg_config()
    return int(cfg.get("console_player_role_id") or LFG_CONSOLE_PLAYER_ROLE_ID_DEFAULT)


def _get_lfg_default_expiry_minutes() -> int:
    """Get default queue expiry time in minutes from config or default."""
    cfg = _get_lfg_config()
    return int(cfg.get("default_expiry_minutes") or LFG_QUEUE_EXPIRY_MINUTES_DEFAULT)


def _get_lfg_max_expiry_minutes() -> int:
    """Get maximum queue expiry time in minutes from config or default (120)."""
    cfg = _get_lfg_config()
    return int(cfg.get("max_expiry_minutes") or 120)


def _get_lfg_queue_types() -> dict:
    """Get queue type configurations from config or defaults."""
    cfg = _get_lfg_config()
    return cfg.get("queue_types") or LFG_QUEUE_TYPES_DEFAULT


def _get_lfg_initiation_trial_role_id() -> Optional[int]:
    """Get Initiation Trial ping role ID from config, or None if not configured."""
    cfg = _get_lfg_config()
    role_id = cfg.get("initiation_trial_role_id")
    return int(role_id) if role_id else None


def _load_lfg_queues() -> dict:
    """Load LFG queues from disk."""
    try:
        if not os.path.exists(LFG_QUEUE_PATH):
            return {}
        with open(LFG_QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_lfg_queues(data: dict):
    """Save LFG queues to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = LFG_QUEUE_PATH + ".tmp"
        bak = LFG_QUEUE_PATH + ".bak"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        if os.path.exists(LFG_QUEUE_PATH):
            try:
                os.replace(LFG_QUEUE_PATH, bak)
            except Exception:
                pass
        os.replace(tmp, LFG_QUEUE_PATH)
    except Exception as e:
        _g.logger.warning(f"Failed to save LFG queues: {e}")


def _get_player_platform(member: discord.Member) -> Optional[str]:
    """Determine if a member is PC or Console player based on roles.

    Returns:
        "pc" if they have PC Player role (or both roles)
        "console" if they have only Console Player role
        None if they have neither role
    """
    role_ids = {r.id for r in member.roles}
    pc_role_id = _get_lfg_pc_role_id()
    console_role_id = _get_lfg_console_role_id()
    has_pc = pc_role_id in role_ids
    has_console = console_role_id in role_ids

    if has_pc:
        return "pc"  # PC takes priority if they have both
    elif has_console:
        return "console"
    return None


def _build_lfg_embed(queue_data: dict, guild: discord.Guild) -> discord.Embed:
    """Build the embed for an LFG queue display."""
    queue_type = queue_data["queue_type"]
    queue_types = _b("_get_lfg_queue_types")()
    type_config = queue_types.get(queue_type, {})
    creator_id = queue_data["creator_id"]
    players = queue_data["players"]  # List of {"user_id": int, "platform": str}
    expires_at = queue_data.get("expires_at")
    initiation_trial = queue_data.get("initiation_trial", False)
    custom_message = queue_data.get("message")

    # Count players and console players
    player_count = len(players)
    max_players = type_config.get("max_players", 3)
    console_count = sum(1 for p in players if p["platform"] == "console")
    max_console = type_config.get("max_console")

    # Determine embed color based on fill status
    if player_count >= max_players:
        color = 0x2ECC71  # Green - full
    elif player_count > 0:
        color = 0xF1C40F  # Yellow - partially filled
    else:
        color = 0x3498DB  # Blue - empty

    # Build title with queue-specific emoji
    queue_display = type_config.get("display", queue_data.get("type", "Unknown"))
    if queue_type == "omega":
        queue_emoji = _get_emoji_by_name(guild, "Omega") or "⚔️"
    else:
        queue_emoji = "⚔️"
    title = f"{queue_emoji} {queue_display} Queue"
    if initiation_trial:
        title += " (Initiation Trial)"
    if player_count >= max_players:
        title += " [FULL]"

    embed = discord.Embed(title=title, color=color)

    # Creator info
    creator = guild.get_member(creator_id)
    creator_name = creator.display_name if creator else f"User {creator_id}"
    embed.set_author(name=f"Created by {creator_name}")

    # Build description with expires time and custom message
    desc_parts = []
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            exp_ts = int(exp_dt.timestamp())
            desc_parts.append(f"⏰ Expires <t:{exp_ts}:R>")
        except Exception:
            pass
    if custom_message:
        desc_parts.append(f"📝 *{custom_message}*")
    if desc_parts:
        embed.description = "\n".join(desc_parts)

    # Player slots
    slot_lines = []
    for i in range(max_players):
        if i < len(players):
            p = players[i]
            member = guild.get_member(p["user_id"])
            name = member.display_name if member else f"User {p['user_id']}"
            platform_emoji = "🖥️" if p["platform"] == "pc" else "🎮"
            slot_lines.append(f"{i + 1}. {platform_emoji} {name}")
        else:
            slot_lines.append(f"{i + 1}. ─ *Empty* ─")

    embed.add_field(
        name=f"Players ({player_count}/{max_players})",
        value="\n".join(slot_lines),
        inline=False,
    )

    # Console limit info for Omega
    if max_console is not None:
        console_status = f"🎮 Console: {console_count}/{max_console}"
        if console_count >= max_console:
            console_status += " (limit reached)"
        embed.add_field(name="Platform Limits", value=console_status, inline=False)

    embed.set_footer(text="Click buttons to join/leave")

    return embed


class LFGQueueView(discord.ui.View):
    """View with Join/Leave buttons for LFG queue sign-ups.

    Uses dynamic custom_ids with queue_id to ensure buttons work
    across bot restarts and don't conflict between different queues.
    """

    def __init__(self, queue_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.queue_id = queue_id

        # Add buttons with dynamic custom_ids - NO callbacks here
        # Interactions are handled by on_interaction -> _handle_lfg_button
        join_button = discord.ui.Button(
            label="Join Queue",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"lfg_join:{queue_id}",
        )
        self.add_item(join_button)

        leave_button = discord.ui.Button(
            label="Leave Queue",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"lfg_leave:{queue_id}",
        )
        self.add_item(leave_button)

        close_button = discord.ui.Button(
            label="Close Queue",
            style=discord.ButtonStyle.secondary,
            emoji="🔒",
            custom_id=f"lfg_close:{queue_id}",
        )
        self.add_item(close_button)

    async def _get_queue_data(self) -> Optional[dict]:
        """Get queue data from memory or disk."""
        async with _g.LFG_QUEUE_LOCK:
            if self.queue_id in _g.LFG_ACTIVE_QUEUES:
                return _g.LFG_ACTIVE_QUEUES[self.queue_id]
            # Try loading from disk
            all_queues = _b("_load_lfg_queues")()
            if str(self.queue_id) in all_queues:
                queue_data = all_queues[str(self.queue_id)]
                _g.LFG_ACTIVE_QUEUES[self.queue_id] = queue_data
                return queue_data
        return None

    async def _save_queue_data(self, queue_data: dict):
        """Save queue data to memory and disk."""
        async with _g.LFG_QUEUE_LOCK:
            _g.LFG_ACTIVE_QUEUES[self.queue_id] = queue_data
            all_queues = _b("_load_lfg_queues")()
            all_queues[str(self.queue_id)] = queue_data
            _b("_save_lfg_queues")(all_queues)

    async def _update_embed(self, interaction: discord.Interaction):
        """Update the queue embed with current state."""
        queue_data = await self._get_queue_data()
        if not queue_data:
            return

        embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
        try:
            # After defer(), we need to edit the original message directly
            # interaction.message is the message containing the button
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            _g.logger.warning(f"Failed to update LFG embed: {e}")

    async def join_queue(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(interaction.user.id)

        if not member:
            await interaction.response.send_message("Could not resolve your membership.", ephemeral=True)
            return

        # Check platform role
        platform = _b("_get_player_platform")(member)
        if not platform:
            pc_role = _get_lfg_pc_role_id()
            console_role = _get_lfg_console_role_id()
            await interaction.response.send_message(
                f"❌ You must have either the <@&{pc_role}> or "
                f"<@&{console_role}> role to join a queue.\n"
                "Please assign yourself one of these roles first.",
                ephemeral=True,
            )
            return

        queue_data = await self._get_queue_data()
        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        queue_types = _b("_get_lfg_queue_types")()
        type_config = queue_types.get(queue_data["queue_type"], {})
        players = queue_data["players"]

        # Check if already in queue
        if any(p["user_id"] == member.id for p in players):
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        # Check if queue is full
        if len(players) >= type_config.get("max_players", 3):
            await interaction.response.send_message("This queue is already full.", ephemeral=True)
            return

        # Check console limit for Omega
        max_console = type_config.get("max_console")
        if max_console is not None and platform == "console":
            console_count = sum(1 for p in players if p["platform"] == "console")
            if console_count >= max_console:
                await interaction.response.send_message(
                    f"❌ This Omega queue has reached the console player limit ({max_console}).\n"
                    "Only PC players can join at this time.",
                    ephemeral=True,
                )
                return

        # Add player to queue
        players.append({"user_id": member.id, "platform": platform})
        queue_data["players"] = players
        await self._save_queue_data(queue_data)

        # Update embed by editing the message directly
        embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

        # Check if queue is now full and notify creator
        if len(players) >= type_config.get("max_players", 3):
            creator = interaction.guild.get_member(queue_data["creator_id"])
            if creator:
                player_mentions = []
                for p in players:
                    m = interaction.guild.get_member(p["user_id"])
                    if m:
                        player_mentions.append(m.mention)
                try:
                    await interaction.followup.send(
                        f"🎉 **Queue Full!** {creator.mention}, your {type_config.get('display', 'Mission')} queue is ready!\n"
                        f"Players: {', '.join(player_mentions)}",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                except Exception:
                    pass

    async def leave_queue(self, interaction: discord.Interaction):
        member = interaction.user

        queue_data = await self._get_queue_data()
        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        players = queue_data["players"]

        # Check if in queue
        player_entry = next((p for p in players if p["user_id"] == member.id), None)
        if not player_entry:
            await interaction.response.send_message("You are not in this queue.", ephemeral=True)
            return

        # Remove player
        players.remove(player_entry)
        queue_data["players"] = players
        await self._save_queue_data(queue_data)

        # Update embed by editing the message directly
        embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def close_queue(self, interaction: discord.Interaction):
        queue_data = await self._get_queue_data()
        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        # Only creator can close
        if interaction.user.id != queue_data["creator_id"]:
            await interaction.response.send_message("Only the queue creator can close this queue.", ephemeral=True)
            return

        # Remove from storage
        async with _g.LFG_QUEUE_LOCK:
            if self.queue_id in _g.LFG_ACTIVE_QUEUES:
                del _g.LFG_ACTIVE_QUEUES[self.queue_id]
            all_queues = _b("_load_lfg_queues")()
            if str(self.queue_id) in all_queues:
                del all_queues[str(self.queue_id)]
                _b("_save_lfg_queues")(all_queues)

        # Update message to show closed
        embed = discord.Embed(
            title="🔒 Queue Closed",
            description="This queue has been closed by the creator.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(embed=embed, view=None)


async def _restore_lfg_queue_views():
    """Restore persistent views for existing LFG queues on bot startup."""
    try:
        all_queues = _b("_load_lfg_queues")()
        for queue_id_str, queue_data in all_queues.items():
            try:
                queue_id = int(queue_id_str)
                _g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
                # Register the view with unique custom_ids per queue
                _g.bot.add_view(LFGQueueView(queue_id))
            except Exception as e:
                _g.logger.debug(f"Failed to restore LFG queue view {queue_id_str}: {e}")
        if all_queues:
            _g.logger.info(f"Restored {len(all_queues)} LFG queue view(s)")
    except Exception as e:
        _g.logger.warning(f"Failed to restore LFG queue views: {e}")


async def _expire_old_lfg_queues():
    """Check for and expire old LFG queues."""
    try:
        now = _b("datetime").now(timezone.utc)
        expired = []

        async with _g.LFG_QUEUE_LOCK:
            all_queues = _b("_load_lfg_queues")()

            for queue_id_str, queue_data in list(all_queues.items()):
                expires_at_str = queue_data.get("expires_at")
                if not expires_at_str:
                    continue

                try:
                    expires_at = _b("datetime").fromisoformat(expires_at_str)
                    if now >= expires_at:
                        expired.append((int(queue_id_str), queue_data))
                        del all_queues[queue_id_str]
                        if int(queue_id_str) in _g.LFG_ACTIVE_QUEUES:
                            del _g.LFG_ACTIVE_QUEUES[int(queue_id_str)]
                except Exception:
                    continue

            if expired:
                _b("_save_lfg_queues")(all_queues)

        # Update expired queue messages
        for queue_id, queue_data in expired:
            try:
                guild = _b("_resolve_notification_guild")()
                if not guild:
                    continue
                # Get channel from stored channel_id in queue_data
                channel_id = queue_data.get("channel_id")
                if not channel_id:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue
                msg = await channel.fetch_message(queue_id)
                embed = discord.Embed(
                    title="⏰ Queue Expired",
                    description="This queue has expired and is no longer accepting sign-ups.",
                    color=0x95A5A6,
                )
                await msg.edit(embed=embed, view=None)
            except discord.NotFound:
                pass
            except Exception as e:
                _g.logger.debug(f"Failed to update expired queue message {queue_id}: {e}")

        if expired:
            _g.logger.info(f"Expired {len(expired)} LFG queue(s)")
    except Exception as e:
        _g.logger.warning(f"Failed to expire LFG queues: {e}")


@tasks.loop(minutes=5)
async def _lfg_queue_expiration_loop():
    """Check for expired LFG queues every 5 minutes."""
    try:
        await _expire_old_lfg_queues()
    except Exception:
        _g.logger.exception("Error in LFG queue expiration loop")


# ─────────────────────────────────────────────────────────────────────────────
# Log to Forge View - Button for posting ephemeral blessings publicly
# ─────────────────────────────────────────────────────────────────────────────


class LogToForgeView(discord.ui.View):
    """View with a 'Log to Forge' button for blessing attestations.

    When clicked, posts the blessing publicly to the arming chamber
    and triggers a chronicle repost at the bottom of the channel.
    """

    def __init__(
        self,
        embed: discord.Embed,
        member_id: int,
        member_mention: str,
        techmarine_id: int,
        spirit_designation: str,
        spirit_event: str,
        is_intensive: bool,
        is_significant: bool,
    ):
        super().__init__(timeout=300)  # 5 minute timeout
        self.embed = embed
        self.member_id = member_id
        self.member_mention = member_mention
        self.techmarine_id = techmarine_id
        self.spirit_designation = spirit_designation
        self.spirit_event = spirit_event
        self.is_intensive = is_intensive
        self.is_significant = is_significant
        self.logged = False

    @discord.ui.button(
        label="Log to Forge",
        style=discord.ButtonStyle.primary,
        emoji="📜",
        custom_id="log_to_forge",
    )
    async def log_to_forge(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.logged:
            await interaction.response.send_message("Already logged to forge.", ephemeral=True)
            return

        self.logged = True
        button.disabled = True
        button.label = "Logged"
        button.style = discord.ButtonStyle.secondary

        # Update the ephemeral message to show button is disabled
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        # Post blessing publicly to arming chamber
        channel_id = _get_arming_chamber_channel_id()
        if not channel_id or not interaction.guild:
            return

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            return

        # Post the blessing - always mention the blessed brother before embed
        try:
            await channel.send(
                content=self.member_mention,
                embed=self.embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except Exception as e:
            _g.logger.warning(f"Failed to log blessing to forge: {e}")
            return

        # Trigger chronicle repost at bottom
        await _repost_chronicle_at_bottom(interaction.guild)


async def _repost_chronicle_at_bottom(guild: discord.Guild):
    """Delete the old chronicle and repost it at the bottom of the arming chamber."""
    channel_id = _get_arming_chamber_channel_id()
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    # Delete old chronicle message if exists
    existing_msg_id = await _get_dashboard_message_id()
    if existing_msg_id:
        try:
            existing_msg = await channel.fetch_message(existing_msg_id)
            await existing_msg.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            _g.logger.debug(f"Failed to delete old chronicle: {e}")

    # Build and post new chronicle
    try:
        embed = await _build_forge_chronicle_embed(guild)
        sent_msg = await channel.send(embed=embed)
        await _set_dashboard_message_id(sent_msg.id)
        _g.logger.debug("Chronicle reposted at bottom")
    except Exception as e:
        _g.logger.warning(f"Failed to repost chronicle: {e}")


def _extract_killteam_name(name: str) -> str:
    """Return a display-friendly Kill Team name by stripping the 'Kill Team' prefix.
    Handles optional separators like ':', '-', and varying whitespace/case.
    Also handles forum channel format 'Kill-Team X' (hyphen between Kill and Team).
    If no match, returns the original name (or 'Unknown' if empty).
    Ignores role names like 'Kill Team Champion' that aren't actual kill teams.
    """
    try:
        # Skip non-KT role names that start with "Kill Team"
        if name and name.lower().strip() == "kill team champion":
            return name or "Unknown"
        # Match 'Kill Team X', 'Kill-Team X', 'KillTeam X', etc.
        m = re.match(r"(?i)\s*kill[\s\-]*team\s*[:\-]?\s*(.+)", (name or ""))
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return name or "Unknown"


def _resolve_killteam_for_member(
    member: discord.User | discord.Member,
) -> Optional[str]:
    """Return the canonical Kill Team name for a member by inspecting their roles.

    Matching strategy (in order):
    1. Role ID in ALLOWED_KT_ROLE_IDS (most reliable).
    2. Exact case-insensitive role name match against entries in `_b('KILL_TEAMS')`.

    Returns the canonical `_b('KILL_TEAMS')` entry on match, else `None`.
    """
    try:
        roles = getattr(member, "roles", []) or []
        # map lower->canonical for fast lookup
        canonical_map = {kt.lower(): kt for kt in _b("KILL_TEAMS")}

        for r in roles:
            # 1) Check role ID against ALLOWED_KT_ROLE_IDS (most reliable)
            rid = getattr(r, "id", None)
            if rid and _b("ALLOWED_KT_ROLE_IDS") and rid in _b("ALLOWED_KT_ROLE_IDS"):
                rn = (getattr(r, "name", "") or "").strip()
                # Return the role name if it's in _b('KILL_TEAMS'), otherwise return as-is
                if rn.lower() in canonical_map:
                    return canonical_map[rn.lower()]
                return rn  # Role ID matched but name not in _b('KILL_TEAMS') yet

            # 2) Exact case-insensitive match against _b('KILL_TEAMS') entries
            rn = (getattr(r, "name", "") or "").strip()
            if not rn:
                continue
            if rn.lower() in canonical_map:
                return canonical_map[rn.lower()]
    except Exception:
        return None
    return None


def _resolve_killteams_for_member(member: discord.User | discord.Member) -> List[str]:
    """Return a list of Kill Team-like identifiers this member should contribute to.

    Rules:
    - Include any canonical Kill Team from `_b('KILL_TEAMS')` the member holds.
    - Include any command team from `_b('COMMAND_TEAMS')` the member holds as a role.
    - A member may contribute to multiple teams simultaneously.
    """
    out: List[str] = []
    try:
        # 1) canonical kill teams
        try:
            kt = _resolve_killteam_for_member(member)
            if kt:
                out.append(kt)
        except Exception:
            pass

        # 2) command teams (check for actual roles matching _b('COMMAND_TEAMS'))
        try:
            names = _b("_canonical_role_names")(member)
            for cmd_team in _b("COMMAND_TEAMS"):
                if cmd_team in names and cmd_team not in out:
                    out.append(cmd_team)
        except Exception:
            pass
    except Exception:
        return []

    # Deduplicate preserving order
    seen = set()
    res: List[str] = []
    for x in out:
        if x and x not in seen:
            res.append(x)
            seen.add(x)
    return res


def _get_techmarine_acknowledgment_blended(member: "discord.Member", bearer_studs: int) -> str:
    """Get a dynamically blended acknowledgment phrase for forge_rite.

    Blends rank-specific and stud-specific acknowledgments based on:
    - Higher studs → more likely stud acknowledgment
    - Higher rank → more likely rank acknowledgment

    Examples:
    - Watch Veteran + 16 studs → ~83% stud ack (studs are impressive for low rank)
    - High Chaplain + 2 studs → ~86% rank ack (rank is impressive vs low studs)
    - Forgemaster + 16 studs → ~50/50 (both equally impressive)
    """
    import random

    # Determine bearer's rank name (highest priority first based on _b('RANK_ROLES_PRIORITY') order)
    bearer_rank_name = None
    try:
        for rank_name in _b("RANK_ROLES_PRIORITY"):
            for r in getattr(member, "roles", []) or []:
                rn = (getattr(r, "name", "") or "").strip()
                if rn == rank_name:
                    bearer_rank_name = rank_name
                    break
            if bearer_rank_name:
                break
    except Exception:
        pass

    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    # Calculate weights
    rank_weight = RANK_PRESTIGE_WEIGHTS.get(bearer_rank_name, 0.1)
    stud_weight = _get_stud_weight(bearer_studs)

    # Probability of rank acknowledgment = rank_weight / (rank_weight + stud_weight)
    prob_rank = rank_weight / (rank_weight + stud_weight)

    # Choose based on probability
    if random.random() < prob_rank:
        # Use rank-specific acknowledgment
        rank_options = TECHMARINE_RANK_ACKNOWLEDGMENTS.get(
            bearer_rank_name, TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Brother"]
        )
        return random.choice(rank_options)
    else:
        # Use stud-tier acknowledgment via shared _studs_tier()
        studs_tier = _studs_tier(bearer_studs)
        stud_options = TECHMARINE_STUDS_ACKNOWLEDGMENT.get(studs_tier, TECHMARINE_STUDS_ACKNOWLEDGMENT[1])
        return random.choice(stud_options)


# Techmarine signature variation phrases (randomly chosen)
# Additional flavor data (TECHMARINE_SIGNATURES, SACRED_MECHANICUS_PHRASES,
# FORGEMASTER_SELF_ATTESTATION_*, CHAPTER_STUDS_FLAVOR, ORDO_XENOS_HONORS_*,
# RANK_STUDS_COMMENTARY, SERVICE_STUDS_*, DEATHWATCH_STUD_*, OATHSWORN_*) lives
# in flavor_text.py.


def _get_emoji_by_name(guild: discord.Guild, name: str) -> Optional[str]:
    """Lookup a custom emoji by name from the guild.

    Returns the emoji string (e.g., '<:HawkLords:123456>') if found, else None.
    The name should be without colons, e.g., 'HawkLords' not ':HawkLords:'.
    """
    if not guild:
        return None
    # Normalize: remove spaces and special chars for lookup
    # e.g., 'Hawk Lords' -> 'HawkLords', 'Watch Brother' -> 'WatchBrother'
    normalized = name.replace(" ", "").replace("-", "").replace("'", "")
    for emoji in guild.emojis:
        if emoji.name.lower() == normalized.lower():
            return str(emoji)
    return None


def _blend_forgemaster_self_attestation(member_chapter: str) -> str:
    """Blend chapter identity and role identity for Forgemaster self-blessing.

    Follows High Command Specialist ratio: 80% role (generic Mechanicus), 20% chapter.
    Falls back to generic if chapter not found.
    """
    import random

    chapter_options = FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER.get(member_chapter, [])

    # 80% role (generic Mechanicus), 20% chapter
    if random.random() < 0.8:
        return random.choice(FORGEMASTER_SELF_ATTESTATION_GENERIC)

    if chapter_options:
        return random.choice(chapter_options)

    # Fallback to generic if chapter not in dict
    return random.choice(FORGEMASTER_SELF_ATTESTATION_GENERIC)


def _get_chapter_emoji(guild: discord.Guild, chapter_name: str) -> str:
    """Get chapter emoji or fallback to just the chapter name."""
    emoji = _get_emoji_by_name(guild, chapter_name)
    if emoji:
        return f"{emoji} {chapter_name}"
    return chapter_name


def _get_rank_emoji(guild: discord.Guild, rank_name: str) -> str:
    """Get rank emoji or fallback to just the rank name."""
    # Special mappings where emoji name differs from role name
    RANK_EMOJI_OVERRIDES = {
        "Company Champion": "WatchChampion",
        "Kill Team Champion": "KillteamChampion",
    }
    emoji_name = RANK_EMOJI_OVERRIDES.get(rank_name, rank_name)
    emoji = _get_emoji_by_name(guild, emoji_name)
    if emoji:
        return f"{emoji}"
    return ""


def _get_rank_category_for_blend(rank_name: str) -> str:
    """Categorize rank for stud flavor blending.

    Returns one of: 'watchers', 'high_cmd_specialist', 'company_cmd', 'specialist', 'line'

    - watchers: Watch Master (100% role, 0% chapter)
    - high_cmd_specialist: Chaplain, Apothecary, Librarian, Techmarine at High Command level
    - company_cmd: Captains, Lieutenants, Champions at company level
    - specialist: Watch Chaplain, Watch Apothecary, Watch Librarian, Watch Techmarine (not high cmd)
    - line: Everyone else (Sergeant, Veteran, Brother, Champions at KT level)
    """
    if rank_name == "Watch Master":
        return "watchers"

    high_cmd_roles = {
        "High Chaplain",
        "Chief Apothecary",
        "Watch Librarian",
        "Watch Techmarine",
        "Forgemaster",
        "Void Warden",
        "Venerable Dreadnought",  # Ancient of the Long Watch, high command level
    }
    if rank_name in high_cmd_roles:
        return "high_cmd_specialist"

    company_cmd_roles = {
        "Watch Captain",
        "Watch Lieutenant",
        "Company Champion",
        "Honored Dreadnought",  # Honored warriors, company command level
    }
    if rank_name in company_cmd_roles:
        return "company_cmd"

    specialist_roles = {"Watch Chaplain", "Watch Apothecary"}
    if rank_name in specialist_roles:
        return "specialist"

    # Interred Brother falls into "line" category (inactive, lowest priority)
    return "line"


def _blend_stud_flavor_by_rank(member_chapter: str, member_rank_name: str, pip_type: str) -> str:
    """Blend chapter identity and role identity based on rank hierarchy.

    - Line (KB/Oathsworn/KT members): 80% chapter, 20% role
    - Specialist (Watch Chaplain/Apothecary): 50% chapter, 50% role
    - Company Command: 50% chapter, 50% role
    - High Command Specialist: 20% chapter, 80% role
    - Watch Master: 10% chapter, 90% role

    pip_type: "plasteel" or "auramite" for veneration fallback selection.
    Returns blended flavor text or falls back to pip-type-based veneration.
    """
    import random

    category = _get_rank_category_for_blend(member_rank_name)

    # Get chapter flavor (3 options per chapter)
    chapter_options = CHAPTER_STUDS_FLAVOR.get(member_chapter, [])

    # Get role-specific commentary (if available)
    role_options = RANK_STUDS_COMMENTARY.get(member_rank_name, [])

    # Select veneration pool based on pip type
    if pip_type == "auramite":
        veneration_pool = SERVICE_STUDS_VENERATIONS_AURAMITE
    else:  # plasteel or unknown
        veneration_pool = SERVICE_STUDS_VENERATIONS_PLASTEEL

    # Blend based on category
    if category == "watchers":
        # 90% role, 10% chapter
        if random.random() < 0.9:
            if role_options:
                return random.choice(role_options)
        if chapter_options:
            return random.choice(chapter_options)
        # Fallback to pip-type veneration
        return random.choice(veneration_pool)

    elif category == "high_cmd_specialist":
        # 80% role, 20% chapter
        if random.random() < 0.8:
            if role_options:
                return random.choice(role_options)
        if chapter_options:
            return random.choice(chapter_options)
        return random.choice(veneration_pool)

    elif category == "company_cmd" or category == "specialist":
        # 50% chapter, 50% role
        if random.random() < 0.5:
            if chapter_options:
                return random.choice(chapter_options)
            if role_options:
                return random.choice(role_options)
        else:
            if role_options:
                return random.choice(role_options)
            if chapter_options:
                return random.choice(chapter_options)
        return random.choice(veneration_pool)

    else:  # line (default: KB, Oathsworn, KT members)
        # 80% chapter, 20% role
        if random.random() < 0.8:
            if chapter_options:
                return random.choice(chapter_options)
        if role_options:
            return random.choice(role_options)
        return random.choice(veneration_pool)


def _get_stud_marking_recipients(member: discord.Member, guild: discord.Guild) -> Tuple[str, str]:
    """Determine who receives stud marking and who witnesses. Returns (primary, secondary).

    The Apothecarion always performs the actual stud implantation (surgical procedure).
    This function determines who witnesses/authorizes based on chain of command:
    - Watch Master: The Chief Apothecary personally attends
    - High Command: The Chief Apothecary attends
    - Company members: Report to their Company Apothecary → Chief Apothecary → CO (in order)
    - Line/Kill Team: Same as company members

    Returns (primary_text, secondary_text) where text is bold name with rank emoji.
    """

    def strip_studs(name: str) -> str:
        """Remove service studs (●⚬) from a name."""
        return name.replace("●", "").replace("⚬", "").strip()

    def find_company_apothecary(company_name: str) -> Optional[discord.Member]:
        """Find the Watch Apothecary for a specific company."""
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Watch Apothecary" not in mbr_roles:
                    continue
                # Check if this apothecary is in the same company
                mbr_company = _find_company_or_chapter(mbr)
                if mbr_company and mbr_company == company_name:
                    return mbr
        except Exception:
            pass
        return None

    def find_chief_apothecary() -> Optional[discord.Member]:
        """Find the Chief Apothecary."""
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Chief Apothecary" in mbr_roles:
                    return mbr
        except Exception:
            pass
        return None

    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]

    # Determine highest rank
    member_rank_name = "Watch Brother"
    for rank in _b("RANK_ROLES_PRIORITY"):
        if rank in role_names:
            member_rank_name = rank
            break

    # Watch Master: Chief Apothecary personally attends
    if member_rank_name == "Watch Master":
        chief_apo = find_chief_apothecary()
        if chief_apo:
            emoji = _get_rank_emoji(guild, "Chief Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(chief_apo.display_name)
            return f"The {emoji_prefix}**{clean_name}** personally attends.", ""
        return "The Chief Apothecary personally attends.", ""

    # High Command: Chief Apothecary attends
    high_cmd = {
        "High Chaplain",
        "Chief Apothecary",
        "Void Warden",
        "Lord Executioner",
        "Forgemaster",
        "Castellan",
    }
    if member_rank_name in high_cmd:
        # If they ARE the Chief Apothecary, another Apothecary handles it
        if member_rank_name == "Chief Apothecary":
            return "Another Apothecary of the Watch attends.", ""
        chief_apo = find_chief_apothecary()
        if chief_apo:
            emoji = _get_rank_emoji(guild, "Chief Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(chief_apo.display_name)
            return f"The {emoji_prefix}**{clean_name}** attends.", ""
        return "Report to the Chief Apothecary.", ""

    # All company members (command, specialists, line, kill team):
    # Try Company Apothecary → Chief Apothecary → CO
    member_company = _find_company_or_chapter(member)

    # Special case: if member IS the Watch Apothecary, go to Chief directly
    if member_rank_name == "Watch Apothecary":
        chief_apo = find_chief_apothecary()
        if chief_apo:
            emoji = _get_rank_emoji(guild, "Chief Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(chief_apo.display_name)
            return f"The {emoji_prefix}**{clean_name}** attends.", ""
        return "Report to the Chief Apothecary.", ""

    # Try to find Company Apothecary first
    if member_company:
        company_apo = find_company_apothecary(member_company)
        if company_apo and company_apo.id != member.id:
            emoji = _get_rank_emoji(guild, "Watch Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(company_apo.display_name)
            return f"Report to {emoji_prefix}**{clean_name}**.", ""

    # Fallback: Chief Apothecary
    chief_apo = find_chief_apothecary()
    if chief_apo:
        emoji = _get_rank_emoji(guild, "Chief Apothecary")
        emoji_prefix = f"{emoji} " if emoji else ""
        clean_name = strip_studs(chief_apo.display_name)
        return f"Report to {emoji_prefix}**{clean_name}**.", ""

    # Fallback: Company CO (Captain/Lieutenant)
    if member_company:
        captains, lieutenants = _b("_find_company_command_staff")(guild, member_company)
        co_member = lieutenants[0] if lieutenants else (captains[0] if captains else None)
        if co_member:
            co_roles = {getattr(r, "name", "") for r in co_member.roles}
            co_rank = "Watch Lieutenant" if "Watch Lieutenant" in co_roles else "Watch Captain"
            emoji = _get_rank_emoji(guild, co_rank)
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(co_member.display_name)
            return f"Report to {emoji_prefix}**{clean_name}**.", ""

    return "Report to the Apothecarion.", ""


def _get_service_studs_announcement(
    member: discord.Member,
    member_chapter: str,
    displayed_studs: int,
    new_studs: int,
    earned_studs: int,
    owed_studs: int,
    guild: discord.Guild,
) -> str:
    """Generate a flavorful, RP-oriented service studs announcement.

    Incorporates the member's rank, home chapter, and which stud they're earning
    to create a personalized and immersive notification.
    Mobile-friendly with shorter lines and Deathwatch theming.
    """
    import random

    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]

    # Use shared function for dynamic champion honorifics (same as forge_rite)
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)

    # Determine raw rank name for emoji lookup
    member_rank_name = "Watch Brother"
    for rank in _b("RANK_ROLES_PRIORITY"):
        if rank in role_names:
            member_rank_name = rank
            break

    stud_word = "Stud" if new_studs == 1 else "Studs"

    # Determine tier and pip display based on EARNED studs (actual total earned)
    # This is the true count based on time and AAR, not displayed count
    tier = _studs_tier(earned_studs)
    studs_pips = _studs_pips(earned_studs)

    # Also track what they'll have after this announcement for pip change display
    new_total = displayed_studs + new_studs

    # Get Watch Brother role for pinging in content (outside embed)
    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""

    # Get emojis for rank and chapter
    rank_emoji = _get_rank_emoji(guild, member_rank_name)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None

    # Build embed
    embed = discord.Embed(
        title="᛭⋅ MARK OF SERVICE ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xC0C0C0,  # Silver for service studs
    )

    # Generate opening and milestone intro (for first embed field)
    # Format opening with stripped display name (no rank/studs)
    opening_template = random.choice(DEATHWATCH_STUD_OPENINGS)
    opening = opening_template.format(name=display_name)

    # Use first-stud templates when earning stud #1 to avoid "another" phrasing
    if earned_studs == 1:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_FIRST)
    elif tier == 1:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER1)
    elif tier == 2:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER2)
    else:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER3)

    # Add Watch's Proclamation as first field with mentions baked in
    # Opening and milestone intro flow together without line break (plain narrative text, no italics/quotes)
    proclamation_value = f"{opening} {milestone_intro}"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_value,
        inline=False,
    )

    # Bearer field with rank emoji (exactly matching forge_rite format)
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    # to put title on one line and rank + name on the next
    if ", " in rank_honorific:
        title_part, rank_part = rank_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {display_name}**"
    else:
        bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter and member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if earned_studs > 0:
        bearer_value += f"\nService Studs: [{studs_pips}] ({earned_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Calculate visual pip change (what pips change from BEFORE to AFTER)
    # displayed_studs = what they had before, new_total = what they'll have after
    prev_studs = max(0, displayed_studs)
    curr_studs = new_total

    prev_auramite = min(prev_studs // 4, 4)
    prev_plasteel = prev_studs % 4 if prev_studs <= 16 else 0

    curr_auramite = min(curr_studs // 4, 4)
    curr_plasteel = curr_studs % 4 if curr_studs <= 16 else 0

    # Compute net change in each pip type
    delta_auramite = curr_auramite - prev_auramite
    delta_plasteel = curr_plasteel - prev_plasteel

    # Build visual pip change string showing what was gained
    # Show the highest tier pip that increased (the "upgrade")
    # If multiple pip types changed, show all positive deltas
    pip_changes = []
    if delta_auramite > 0:
        pip_word = "Stud" if delta_auramite == 1 else "Studs"
        pip_changes.append(f"+{delta_auramite}● Auramite {pip_word}")
    if delta_plasteel > 0:
        pip_word = "Stud" if delta_plasteel == 1 else "Studs"
        pip_changes.append(f"+{delta_plasteel}⚬ Plasteel {pip_word}")

    # Service Record field (bold values for numerical emphasis)
    if pip_changes:
        pip_change = ", ".join(pip_changes)
    else:
        pip_change = f"+{new_studs} {stud_word}"
    record_value = f"**{pip_change}** Earned\n"
    record_value += f"Total: **{earned_studs}** | Displayed: **{displayed_studs}**"
    if owed_studs > 0:
        record_value += f"\nOwed: **{owed_studs}**"
    embed.add_field(name="▸ Service Record", value=record_value, inline=True)

    # Special milestone callout (bold labels, plain narrative - check against earned studs)
    special_milestone = SERVICE_STUDS_SPECIAL_MILESTONES.get(earned_studs)
    if special_milestone:
        embed.add_field(name="▸ Milestone", value=special_milestone, inline=False)

    # Honor of the Long Watch: Tiered Ordo Xenos phrase + blended chapter/role flavor
    # Select tier-appropriate Ordo Xenos honor
    if tier == 1:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER1)
    elif tier == 2:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER2)
    else:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER3)

    # Format pronouns (always second person for awarding to others)
    ordo_honor = ordo_honor.format(possessive="your", possessive_cap="Your", object="you")

    # Determine which pip type is being earned (priority: auramite > plasteel)
    if delta_auramite > 0:
        pip_type = "auramite"
    else:
        pip_type = "plasteel"

    # Blend chapter and role flavor based on rank hierarchy (italics + quotes for honor/reverential phrases)
    blended_flavor = _blend_stud_flavor_by_rank(member_chapter, member_rank_name, pip_type)

    embed.add_field(
        name="▸ Honor of the Long Watch",
        value=f'*"{ordo_honor} {blended_flavor}"*',
        inline=False,
    )

    # Call to action: determine who administers/witnesses marking based on rank (plain narrative with bold names)
    marking_primary, marking_secondary = _get_stud_marking_recipients(member, guild)
    marking_value = marking_primary
    if marking_secondary:
        marking_value = f"{marking_primary}\n{marking_secondary}"

    embed.add_field(
        name="▸ Rite of Marking",
        value=marking_value,
        inline=False,
    )

    # Footer with closing phrase from ceremonial closings
    closing_phrase = random.choice(DEATHWATCH_STUD_CLOSINGS)
    embed.set_footer(text=f"᛭⋅ {closing_phrase} Jericho Stands! ⋅᛭")

    # Content has @Watch Brother and member mention for actual pings (outside embed)
    content = f"{wb_mention} {member.mention}" if wb_mention else member.mention
    return content, embed


def _get_oathsworn_announcement(
    member: discord.Member,
    member_chapter: str,
    earned_studs: int,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, discord.Poll]:
    """Generate a flavorful Oathsworn eligibility announcement with embed and poll.

    Called when a Watch Veteran has earned 3+ service studs and is eligible
    for consideration to become Oathsworn. Returns content (mentions), embed
    (flavorful announcement), and a 48-hour poll for voting.
    """
    import random

    # Extract bearer info using shared function
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)

    # Get emojis
    rank_emoji = _get_rank_emoji(guild, "Watch Veteran")
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    oathsworn_emoji = _get_emoji_by_name(guild, "Oathsworn")
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")

    # Compute stud pips display using shared helper (auramite-only post-4)
    studs_pips = _studs_pips(earned_studs)

    # Generate opening and proclamation
    opening_template = random.choice(OATHSWORN_OPENINGS)
    opening = opening_template.format(name=display_name)
    proclamation = random.choice(OATHSWORN_PROCLAMATIONS)

    # Build embed
    dw_emoji_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    oath_emoji_str = f"{oathsworn_emoji} " if oathsworn_emoji else ""
    embed = discord.Embed(
        title=f"{dw_emoji_str}᛭⋅ OATHSWORN CONSIDERATION ⋅᛭{dw_emoji_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xFFD700,  # Gold for Oathsworn consideration
    )

    # Proclamation field
    proclamation_value = f"{opening}\n\n{proclamation}"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_value,
        inline=False,
    )

    # Candidate field (same format as Bearer in service studs/forge_rite)
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    # to put title on one line and rank + name on the next
    if ", " in rank_honorific:
        title_part, rank_part = rank_honorific.rsplit(", ", 1)
        candidate_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {display_name}**"
    else:
        candidate_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        candidate_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        candidate_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    candidate_value += f"\nService Studs: **[{studs_pips}]** ({earned_studs})"
    embed.add_field(name="▸ Candidate", value=candidate_value, inline=True)

    # Eligibility field
    eligibility_value = (
        f"Rank: **Watch Veteran** ✓\n"
        f"Service Studs: **{earned_studs}** (3 required) ✓\n"
        f"Eligible for: {oath_emoji_str}**Oathsworn**"
    )
    embed.add_field(name="▸ Eligibility", value=eligibility_value, inline=True)

    # Call to action
    embed.add_field(
        name="▸ Rite of Elevation",
        value=(
            "The Watch awaits your judgment, Brothers.\n"
            "Cast your vote below to determine if this warrior shall take the Oath."
        ),
        inline=False,
    )

    # Footer
    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")

    # Create poll - 48 hour duration
    poll = discord.Poll(
        question=f"Shall {display_name} be elevated to Oathsworn?",
        duration=timedelta(hours=48),
        multiple=False,
    )
    poll.add_answer(text="Aye, elevate to Oathsworn", emoji="⚔️")
    poll.add_answer(text="Nay, more service required", emoji="🛡️")

    # Content with mentions
    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()

    return content, embed, poll


def _get_member_rank_title(member: discord.Member) -> str:
    """Get the rank honorific for a member based on their highest rank role."""
    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]
    # Check ranks in priority order (highest first)
    for rank in _b("RANK_ROLES_PRIORITY"):
        if rank in role_names:
            return RANK_HONORIFICS.get(rank, rank)
    return "Brother"


async def _get_award_announcement_channel(
    member: discord.Member,
    guild: discord.Guild,
) -> Optional[discord.abc.Messageable]:
    """Return the channel for a public award announcement.

    Resolution order:
    1. KT_ROLE_CHANNEL_MAP override (role_id → channel_id) if populated.
    2. Active forum thread in ALLOWED_KT_FORUM_PARENT_IDS whose name matches
       the member's Kill Team role (via _extract_killteam_name fuzzy match).
    3. General channel (SERVICE_STUDS_CHANNEL_ID) as fallback.
    """
    # 1) Static map override
    kt_channel_map: dict = _b("KT_ROLE_CHANNEL_MAP") or {}
    if kt_channel_map:
        for role in getattr(member, "roles", []):
            channel_id = kt_channel_map.get(role.id)
            if channel_id:
                ch = guild.get_channel(channel_id)
                if ch:
                    return ch

    # 2) Dynamic forum thread search
    kt_name = _resolve_killteam_for_member(member)
    if kt_name:
        kt_short = _extract_killteam_name(kt_name).lower()
        forum_parent_ids = _b("ALLOWED_KT_FORUM_PARENT_IDS") or set()
        try:
            active_threads = await guild.active_threads()
            for thread in active_threads:
                parent = thread.parent
                if parent and parent.id in forum_parent_ids:
                    thread_short = _extract_killteam_name(thread.name).lower()
                    if thread_short and (kt_short in thread_short or thread_short in kt_short):
                        return thread
        except Exception as e:
            _g.logger.debug(f"Failed to resolve KT thread for award announcement ({member.id}): {e}")

    # 3) Fallback: general
    return guild.get_channel(SERVICE_STUDS_CHANNEL_ID)


_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def _get_award_image(filename: str) -> Optional[discord.File]:
    path = os.path.join(_ASSETS_DIR, filename)
    if os.path.isfile(path):
        return discord.File(path, filename=filename)
    return None


def _get_watch_veteran_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Watch Veteran promotion announcement embed.

    Called after the bot auto-assigns the Watch Veteran role.
    Returns (content, embed) where content holds the ping mentions.
    """
    # Use shared helper for display name / title but override honorific since
    # the member was just promoted and may not yet reflect the new role.
    _, display_name, member_title = _get_bearer_rank_and_title(member)

    rank_emoji = _get_rank_emoji(guild, "Watch Veteran")
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")

    opening = random.choice(WATCH_VETERAN_OPENINGS).format(name=display_name)
    proclamation = random.choice(WATCH_VETERAN_PROCLAMATIONS)
    chapter_coda = WATCH_VETERAN_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ WATCH VETERAN PROMOTION ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xC0C0C0,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_text,
        inline=False,
    )

    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**Honored Veteran {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Promoted Warrior", value=bearer_value, inline=True)

    embed.add_field(
        name="▸ Service Record",
        value="Service: **200+ AAR Points** ✓\nTime: **2+ Weeks** ✓\nPromoted to: **Watch Veteran**",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_watch_veteran.png")
    if award_file:
        embed.set_image(url="attachment://award_watch_veteran.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_ardent_raider_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Ardent Raider Ribbon award announcement embed."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    rank_emoji = None
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    ribbon_emoji = _get_emoji_by_name(guild, "ArdentRaiderRibbon")

    opening = random.choice(ARDENT_RAIDER_OPENINGS).format(name=display_name)
    proclamation = random.choice(ARDENT_RAIDER_PROCLAMATIONS)
    chapter_coda = ARDENT_RAIDER_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ ARDENT RAIDER RIBBON ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xD4AF37,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_text,
        inline=False,
    )

    role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
    for rank in RANK_HONORIFICS:
        if rank in role_names:
            rank_emoji = _get_rank_emoji(guild, rank)
            break
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    ribbon_str = f"{ribbon_emoji} " if ribbon_emoji else "🎖️ "
    embed.add_field(
        name="▸ Award",
        value=f"{ribbon_str}**Ardent Raider Ribbon**\n200+ Armory Points ✓",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_ardent_raider.png")
    if award_file:
        embed.set_image(url="attachment://award_ardent_raider.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_apothecarion_medal_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Apothecarion Service Medal award announcement embed."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    medal_emoji = _get_emoji_by_name(guild, "ApothecarionServiceMedal")

    opening = random.choice(APOTHECARION_MEDAL_OPENINGS).format(name=display_name)
    proclamation = random.choice(APOTHECARION_MEDAL_PROCLAMATIONS)
    chapter_coda = APOTHECARION_MEDAL_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ APOTHECARION SERVICE MEDAL ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xFFFFFF,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_text,
        inline=False,
    )

    rank_emoji = None
    for rank in RANK_HONORIFICS:
        role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
        if rank in role_names:
            rank_emoji = _get_rank_emoji(guild, rank)
            break
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    medal_str = f"{medal_emoji} " if medal_emoji else "🎖️ "
    embed.add_field(
        name="▸ Award",
        value=f"{medal_str}**Apothecarion Service Medal**\n150+ Gene-Seed Points ✓",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_apothecarion_medal.png")
    if award_file:
        embed.set_image(url="attachment://award_apothecarion_medal.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_crimson_laurels_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Crimson Laurels award announcement embed."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    laurels_emoji = _get_emoji_by_name(guild, "CrimsonLaurelsMedal")

    opening = random.choice(CRIMSON_LAURELS_OPENINGS).format(name=display_name)
    proclamation = random.choice(CRIMSON_LAURELS_PROCLAMATIONS)
    chapter_coda = CRIMSON_LAURELS_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ CRIMSON LAURELS ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xDC143C,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_text,
        inline=False,
    )

    rank_emoji = None
    for rank in RANK_HONORIFICS:
        role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
        if rank in role_names:
            rank_emoji = _get_rank_emoji(guild, rank)
            break
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    laurels_str = f"{laurels_emoji} " if laurels_emoji else "🎖️ "
    embed.add_field(
        name="▸ Award",
        value=f"{laurels_str}**Crimson Laurels**\n1000+ AAR Points ✓\nBlack Laurels ✓",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_crimson_laurels.png")
    if award_file:
        embed.set_image(url="attachment://award_crimson_laurels.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _build_challenge_award_embed(
    *,
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
    title: str,
    color: int,
    openings: List[str],
    proclamations: List[str],
    chapter_lines: dict,
    award_label: str,
    award_image: Optional[str],
    ping_role_id: int,
    rank_lines: Optional[dict] = None,
    award_emoji_name: Optional[str] = None,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Shared builder for the challenge award announcements."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    award_emoji = _get_emoji_by_name(guild, award_emoji_name) if award_emoji_name else None

    # Detect member's primary rank (highest-precedence role in RANK_HONORIFICS order).
    role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
    member_rank: Optional[str] = None
    for rank in RANK_HONORIFICS:
        if rank in role_names:
            member_rank = rank
            break

    opening = random.choice(openings).format(name=display_name)
    proclamation = random.choice(proclamations).format(name=display_name)
    chapter_coda = (chapter_lines.get(member_chapter, "") or "").format(name=display_name) if chapter_lines else ""
    rank_coda = (rank_lines.get(member_rank, "") or "").format(name=display_name) if (rank_lines and member_rank) else ""

    # Blend coda selection: when both chapter and rank codas are available, pick one
    # based on rank-tier blend ratios (same as forge rite stud flavor blending).
    # Higher ranks skew toward rank coda; line warriors skew toward chapter coda.
    if chapter_coda and rank_coda:
        rank_category = _get_rank_category_for_blend(member_rank or "")
        blend_thresholds = {
            "watchers": 0.9,          # 90% rank, 10% chapter
            "high_cmd_specialist": 0.8,  # 80% rank, 20% chapter
            "company_cmd": 0.5,        # 50/50
            "specialist": 0.5,         # 50/50
            "line": 0.2,               # 20% rank, 80% chapter
        }
        rank_weight = blend_thresholds.get(rank_category, 0.5)
        selected_coda = rank_coda if random.random() < rank_weight else chapter_coda
    elif rank_coda:
        selected_coda = rank_coda
    else:
        selected_coda = chapter_coda

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ {title} ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=color,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if selected_coda:
        proclamation_text += f"\n\n*{selected_coda}*"
    embed.add_field(name="▸ Watch's Proclamation", value=proclamation_text, inline=False)

    rank_emoji = _get_rank_emoji(guild, member_rank) if member_rank else None
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    award_prefix = f"{award_emoji} " if award_emoji else "🎖️ "
    embed.add_field(name="▸ Award", value=f"{award_prefix}**{award_label}**", inline=True)

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image(award_image) if award_image else None
    if award_file:
        embed.set_image(url=f"attachment://{award_image}")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_sok_g_pipehitter_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful SOK-G: Pipehitter award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="SOK-G: PIPEHITTER",
        color=0x607D8B,
        openings=SOK_G_PIPEHITTER_OPENINGS,
        proclamations=SOK_G_PIPEHITTER_PROCLAMATIONS,
        chapter_lines=SOK_G_PIPEHITTER_CHAPTER_LINES,
        rank_lines=SOK_G_PIPEHITTER_RANK_LINES,
        award_label="SOK-G: Pipehitter",
        award_image="award_sok_g_pipehitter.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_distinguished_pipehitter_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Distinguished SOK-G: Pipehitter award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DISTINGUISHED SOK-G: PIPEHITTER",
        color=0x455A64,
        openings=DISTINGUISHED_PIPEHITTER_OPENINGS,
        proclamations=DISTINGUISHED_PIPEHITTER_PROCLAMATIONS,
        chapter_lines=DISTINGUISHED_PIPEHITTER_CHAPTER_LINES,
        rank_lines=DISTINGUISHED_PIPEHITTER_RANK_LINES,
        award_label="Distinguished SOK-G: Pipehitter",
        award_image="award_distinguished_pipehitter.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_black_laurels_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Black Laurels award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="BLACK LAURELS",
        color=0x1C2833,
        openings=BLACK_LAURELS_OPENINGS,
        proclamations=BLACK_LAURELS_PROCLAMATIONS,
        chapter_lines=BLACK_LAURELS_CHAPTER_LINES,
        rank_lines=BLACK_LAURELS_RANK_LINES,
        award_label="Black Laurels",
        award_image="award_black_laurels.png",
        ping_role_id=BLACK_LAURELS_PING_ROLE_ID,
    )


def _get_crux_terminatus_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Crux Terminatus award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="CRUX TERMINATUS",
        color=0xC0392B,
        openings=CRUX_TERMINATUS_OPENINGS,
        proclamations=CRUX_TERMINATUS_PROCLAMATIONS,
        chapter_lines=CRUX_TERMINATUS_CHAPTER_LINES,
        rank_lines=CRUX_TERMINATUS_RANK_LINES,
        award_label="Crux Terminatus",
        award_image="award_crux_terminatus.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_kadaku_campaign_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Kadaku Campaign Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="KADAKU CAMPAIGN MEDAL",
        color=0x6B5B3A,
        openings=KADAKU_CAMPAIGN_OPENINGS,
        proclamations=KADAKU_CAMPAIGN_PROCLAMATIONS,
        chapter_lines=KADAKU_CAMPAIGN_CHAPTER_LINES,
        rank_lines=KADAKU_CAMPAIGN_RANK_LINES,
        award_label="Kadaku Campaign Medal",
        award_image="award_kadaku_campaign_medal.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_black_reef_campaign_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Black Reef Campaign Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="BLACK REEF CAMPAIGN MEDAL",
        color=0x2C3E50,
        openings=BLACK_REEF_CAMPAIGN_OPENINGS,
        proclamations=BLACK_REEF_CAMPAIGN_PROCLAMATIONS,
        chapter_lines=BLACK_REEF_CAMPAIGN_CHAPTER_LINES,
        rank_lines=BLACK_REEF_CAMPAIGN_RANK_LINES,
        award_label="Black Reef Campaign Medal",
        award_image="award_black_reef_campaign_medal.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_distinguished_black_reef_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Distinguished Black Reef Campaign Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DISTINGUISHED BLACK REEF CAMPAIGN MEDAL",
        color=0x1B2631,
        openings=DISTINGUISHED_BLACK_REEF_OPENINGS,
        proclamations=DISTINGUISHED_BLACK_REEF_PROCLAMATIONS,
        chapter_lines=DISTINGUISHED_BLACK_REEF_CHAPTER_LINES,
        rank_lines=DISTINGUISHED_BLACK_REEF_RANK_LINES,
        award_label="Distinguished Black Reef Campaign Medal",
        award_image="award_distinguished_black_reef.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_order_omega_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Order Omega announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="THE ORDER OMEGA",
        color=0x6C3483,
        openings=ORDER_OMEGA_OPENINGS,
        proclamations=ORDER_OMEGA_PROCLAMATIONS,
        chapter_lines=ORDER_OMEGA_CHAPTER_LINES,
        rank_lines=ORDER_OMEGA_RANK_LINES,
        award_label="The Order Omega",
        award_image="award_order_omega.png",
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_dual_vigil_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Dual Vigil award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DUAL VIGIL",
        color=0x1A252F,
        openings=DUAL_VIGIL_OPENINGS,
        proclamations=DUAL_VIGIL_PROCLAMATIONS,
        chapter_lines=DUAL_VIGIL_CHAPTER_LINES,
        rank_lines=DUAL_VIGIL_RANK_LINES,
        award_label="Dual Vigil",
        award_image=None,
        ping_role_id=DUAL_VIGIL_ROLE_ID,
    )


def _get_terminus_slayer_assault_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Assault) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — ASSAULT",
        color=0xC0392B,
        openings=TERMINUS_SLAYER_ASSAULT_OPENINGS,
        proclamations=TERMINUS_SLAYER_ASSAULT_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_ASSAULT_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_ASSAULT_RANK_LINES,
        award_label="Terminus Slayer (Assault)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_terminus_slayer_bulwark_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Bulwark) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — BULWARK",
        color=0x1A5276,
        openings=TERMINUS_SLAYER_BULWARK_OPENINGS,
        proclamations=TERMINUS_SLAYER_BULWARK_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_BULWARK_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_BULWARK_RANK_LINES,
        award_label="Terminus Slayer (Bulwark)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_terminus_slayer_heavy_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Heavy) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — HEAVY",
        color=0x1B4F2A,
        openings=TERMINUS_SLAYER_HEAVY_OPENINGS,
        proclamations=TERMINUS_SLAYER_HEAVY_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_HEAVY_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_HEAVY_RANK_LINES,
        award_label="Terminus Slayer (Heavy)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_terminus_slayer_sniper_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Sniper) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — SNIPER",
        color=0x4E5B4A,
        openings=TERMINUS_SLAYER_SNIPER_OPENINGS,
        proclamations=TERMINUS_SLAYER_SNIPER_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_SNIPER_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_SNIPER_RANK_LINES,
        award_label="Terminus Slayer (Sniper)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_terminus_slayer_tactical_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Tactical) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — TACTICAL",
        color=0x2E4057,
        openings=TERMINUS_SLAYER_TACTICAL_OPENINGS,
        proclamations=TERMINUS_SLAYER_TACTICAL_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_TACTICAL_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_TACTICAL_RANK_LINES,
        award_label="Terminus Slayer (Tactical)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_terminus_slayer_techmarine_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Techmarine) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — TECHMARINE",
        color=0x871A16,
        openings=TERMINUS_SLAYER_TECHMARINE_OPENINGS,
        proclamations=TERMINUS_SLAYER_TECHMARINE_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_TECHMARINE_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_TECHMARINE_RANK_LINES,
        award_label="Terminus Slayer (Techmarine)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_terminus_slayer_vanguard_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Vanguard) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — VANGUARD",
        color=0x4A235A,
        openings=TERMINUS_SLAYER_VANGUARD_OPENINGS,
        proclamations=TERMINUS_SLAYER_VANGUARD_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_VANGUARD_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_VANGUARD_RANK_LINES,
        award_label="Terminus Slayer (Vanguard)",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _get_master_terminus_slayer_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Master Terminus Slayer award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="MASTER TERMINUS SLAYER",
        color=0xB7950B,
        openings=MASTER_TERMINUS_SLAYER_OPENINGS,
        proclamations=MASTER_TERMINUS_SLAYER_PROCLAMATIONS,
        chapter_lines=MASTER_TERMINUS_SLAYER_CHAPTER_LINES,
        rank_lines=MASTER_TERMINUS_SLAYER_RANK_LINES,
        award_label="Master Terminus Slayer",
        award_image=None,
        ping_role_id=WATCH_COMMAND_ROLE_ID,
    )


def _compute_member_service_studs(member: discord.Member) -> int:
    """Compute the number of service studs a member has earned.

    Service studs are earned at 1 per 4 weeks AND 400 AAR points (minimum of both).
    Only Watch Veteran rank and above are eligible.
    """
    try:
        idx_veteran = _b("_role_index")("Watch Veteran")
        highest_idx = _b("get_highest_rank_index")(member)

        # Must be Watch Veteran or higher
        if idx_veteran is None or highest_idx is None:
            return 0
        if highest_idx > idx_veteran:
            return 0

        now = datetime.utcnow()
        joined_at = _b("_get_effective_induction_date")(member)

        if not joined_at:
            return 0

        # Normalize to naive UTC
        ja = joined_at
        if ja.tzinfo is not None:
            try:
                ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                ja = ja.replace(tzinfo=None)

        weeks = max(0, (now - ja).days // 7)
        studs_time = weeks // 4

        # Get AAR points
        stats = _b("compute_stats_for_user")(str(getattr(member, "id", "")))
        try:
            aar_points = int(round(float(stats.get("aar_points", 0) or 0)))
        except Exception:
            aar_points = 0

        studs_aar = aar_points // 400

        # Studs are the minimum of time-based and points-based, capped at 16
        # (4 Auramite studs maximum, consistent with pip display and promotion tracking)
        return min(min(studs_time, studs_aar), 16)
    except Exception:
        return 0


def _get_bearer_rank_and_title(
    member: discord.Member,
) -> Tuple[str, str, Optional[str]]:
    """Extract bearer's rank honorific, display title, and optional Kill Team/Company."""
    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]
    role_names_set = {rn.lower() for rn in role_names}

    # Determine Kill Team and Company first (needed for dynamic champion honorifics)
    kill_team = None
    company = None
    command_team = None
    for rn in role_names:
        if rn in _b("KILL_TEAMS") and not kill_team:
            kill_team = rn
        if "Watch Company" in rn and not company:
            company = rn
        if rn in _b("COMMAND_TEAMS") and not command_team:
            command_team = rn

    # Determine rank honorific and which rank was matched
    honorific = "Brother"
    matched_rank = None
    for rank_name, hon in RANK_HONORIFICS.items():
        if rank_name.lower() in role_names_set:
            # Handle dynamic Lord Executioner honorific
            if rank_name == "Lord Executioner":
                # Find the Watch Master and use their name
                guild = getattr(member, "guild", None)
                watchmaster_name = None
                if guild:
                    try:
                        wm = _b("_find_watch_master")(guild)
                        if wm:
                            wm_name = wm.display_name
                            # Strip "Watch Master" prefix
                            if wm_name.lower().startswith("watch master"):
                                wm_name = wm_name[len("Watch Master") :].lstrip()
                            # Strip stud pips from name
                            wm_name = wm_name.replace("●", "").replace("⚬", "").strip()
                            watchmaster_name = wm_name
                    except Exception:
                        pass
                if watchmaster_name:
                    honorific = f"Blade of {watchmaster_name}, Lord Executioner"
                else:
                    # Fallback to fortress
                    honorific = "Blade of the Fortress, Lord Executioner"
            # Handle dynamic champion honorifics
            elif rank_name == "Kill Team Champion" and kill_team:
                # Extract KT short name: "Kill Team Falcon" -> "Falcon"
                kt_short = _extract_killteam_name(kill_team)
                honorific = f"Blade of {kt_short}, Champion"
            elif rank_name == "Company Champion" and company:
                # Find the captain of this company and use their name
                guild = getattr(member, "guild", None)
                captain_name = None
                if guild:
                    try:
                        captains, _ = _b("_find_company_command_staff")(guild, company)
                        if captains:
                            # Use first captain's display name, stripped of rank prefix
                            cap = captains[0]
                            cap_name = cap.display_name
                            # Strip "Watch Captain" or "Captain" prefix
                            for prefix in ["Watch Captain", "Captain"]:
                                if cap_name.lower().startswith(prefix.lower()):
                                    cap_name = cap_name[len(prefix) :].lstrip()
                                    break
                            # Strip stud pips from name
                            cap_name = cap_name.replace("●", "").replace("⚬", "").strip()
                            captain_name = cap_name
                    except Exception:
                        pass
                if captain_name:
                    honorific = f"Blade of {captain_name}, Champion"
                else:
                    # Fallback to company short name
                    company_short = _b("_extract_company_short_name")(company)
                    honorific = f"Blade of {company_short}, Champion"
            else:
                honorific = hon
            matched_rank = rank_name
            break

    # Get display name and strip rank prefix if present to avoid "Brother Watch Brother X"
    display_name = member.display_name
    if matched_rank:
        # Strip the rank prefix from display name (case-insensitive)
        name_lower = display_name.lower()
        rank_lower = matched_rank.lower()
        if name_lower.startswith(rank_lower):
            # Remove the rank prefix and any leading whitespace
            display_name = display_name[len(matched_rank) :].lstrip()

    # Also strip any other rank prefixes that might be in the name
    # (in case they have a different rank in their name than their role)
    for rank_name in RANK_HONORIFICS.keys():
        rank_lower = rank_name.lower()
        if display_name.lower().startswith(rank_lower):
            display_name = display_name[len(rank_name) :].lstrip()
            break

    # Also strip honorific-style prefixes from display name to avoid
    # "Brother Brother X" when someone has "Brother X" as their nickname
    honorific_prefixes = [
        "Brother",
        "Honored Veteran",
        "Veteran",
        "Oathsworn Warrior",
        "Oathsworn",
        "Sergeant",
        "Lieutenant",
        "Captain",
        "Chaplain",
        "Apothecary",
        "Librarian",
        "Techmarine",
        "Watch Master",
        "High Chaplain",
        "Chief Apothecary",
        "Void Warden",
        "Forgemaster",
        "Champion",
        "Lord Executioner",
    ]
    for prefix in honorific_prefixes:
        if display_name.lower().startswith(prefix.lower()):
            display_name = display_name[len(prefix) :].lstrip()
            break

    # Strip stud pips from display name (we report studs separately)
    display_name = _strip_display_name(display_name)

    # Build combined title: prefer "Kill Team X, Company Y" format
    # Dreadnoughts show "Dreadnought Cadre" instead of their company
    title_parts = []
    if kill_team:
        title_parts.append(kill_team)

    # Check if member is in Dreadnought Cadre
    role_ids = {getattr(r, "id", 0) for r in roles}
    is_dreadnought = DREADNOUGHT_CADRE_ROLE_ID in role_ids
    if is_dreadnought:
        title_parts.append("Dreadnought Cadre")
    elif company:
        title_parts.append(company)

    if not title_parts and command_team:
        title_parts.append(command_team)

    title = ", ".join(title_parts) if title_parts else None

    return honorific, display_name, title


def _get_bearer_home_chapter(user: discord.User | discord.Member) -> Optional[str]:
    """Return the bearer's home chapter only (not company). Used for chapter blessings."""
    try:
        roles = getattr(user, "roles", []) or []
        hc_lower = {hc.lower(): hc for hc in _b("HOME_CHAPTERS")}
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn and rn.lower() in hc_lower:
                return hc_lower[rn.lower()]  # Return canonical name
    except Exception:
        pass
    return None


def _find_company_or_chapter(user: discord.User | discord.Member) -> Optional[str]:
    """Get authority for attestation: company or High Command only (never chapter)."""
    try:
        roles = getattr(user, "roles", []) or []
        # 1) Exact company role match (official company roles)
        company_roles = {
            "Watch Company Primus",
            "Watch Company Secundus",
            "Watch Company Tertius",
            "Watch Company Quartus",
            "Watch Company Quintus",
        }
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn in company_roles:
                return rn

        # 2) If user is in High Command, return Jericho High Command
        try:
            names = _b("_canonical_role_names")(user)
            if any(r in names for r in _b("HIGH_COMMAND_ROLES")):
                return "Jericho High Command"
        except Exception:
            pass

        # 3) Final fallback - not in a company or high command
        return "Watch Fortress Jericho"
    except Exception:
        pass
    return None


@_g.bot.tree.command(name="set_rite", description="Set your personal consecration rite text.")
@app_commands.describe(rite_text="Your consecration rite text (multiline allowed)")
async def _set_rite(interaction: discord.Interaction, rite_text: str):
    # Restrict to Forgemaster or Techmarine
    allowed, _role_key = _b("_is_techmarine_or_forgemaster")(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Disallow usage in the data-vault channel
    try:
        ch = interaction.channel
        if getattr(ch, "name", None) == "❖⋅data-vault⋅❖":
            await interaction.response.send_message(
                "This command is not usable in ❖⋅data-vault⋅❖.",
                ephemeral=True,
            )
            return
    except Exception:
        pass
    # Check rite length to avoid exceeding Discord's message limit in forge_rite
    if len(rite_text) > MAX_RITE_LENGTH:
        await interaction.response.send_message(
            f"Your consecration rite is too long ({len(rite_text)} chars). "
            f"The Machine God requires brevity—keep it under {MAX_RITE_LENGTH} characters.",
            ephemeral=True,
        )
        return
    try:
        await _set_user_rite(int(interaction.user.id), rite_text)
        await interaction.response.send_message(
            f"Consecration rite saved ({len(rite_text)}/{MAX_RITE_LENGTH} chars).",
            ephemeral=True,
        )
    except Exception:
        await interaction.response.send_message("Failed to save rite.", ephemeral=True)


@_g.bot.tree.command(
    name="forge_rite",
    description="Generate and post a cogitator attestation block for a member.",
)
@app_commands.describe(
    member="Member to attest",
    intensive="Full heal to nominal (costs more charges based on damage severity)",
    force="[Forgemaster only] Override cooldowns and company restrictions",
)
async def _attest(
    interaction: discord.Interaction,
    member: discord.Member,
    intensive: bool = False,
    force: bool = False,
):
    import random

    if not await _is_forge_enabled():
        await interaction.response.send_message(
            "The Techmarine subsystem is currently disabled by Forgemaster decree.",
            ephemeral=True,
        )
        return

    # Permission check: caller must be techmarine or forgemaster to run command
    allowed, _caller_role_key = _b("_is_techmarine_or_forgemaster")(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Force flag is Forgemaster-only
    is_forgemaster = _caller_role_key == "forgemaster"
    if force and not is_forgemaster:
        await interaction.response.send_message(
            "The `force` parameter is restricted to the Forgemaster.",
            ephemeral=True,
        )
        return

    # Disallow usage in the data-vault channel
    try:
        ch = interaction.channel
        if getattr(ch, "name", None) == "❖⋅data-vault⋅❖":
            await interaction.response.send_message(
                "This command is not usable in ❖⋅data-vault⋅❖.",
                ephemeral=True,
            )
            return
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # Reserves check - inactive members cannot receive blessings
    # ─────────────────────────────────────────────────────────────────────────
    member_role_ids = {r.id for r in member.roles}
    member_role_names = {r.name.lower() for r in member.roles}
    if RESERVES_ROLE_ID in member_role_ids or "reserves" in member_role_names:
        bearer_name = _strip_display_name(member.display_name)
        await interaction.response.send_message(
            f"**{bearer_name}** is currently in Reserves. Blessings cannot be performed on inactive members.",
            ephemeral=True,
        )
        return

    # ─────────────────────────────────────────────────────────────────────────
    # Recipient cooldown check (max 3 blessings per 24h, 4h between each)
    # ─────────────────────────────────────────────────────────────────────────
    if not force:
        can_receive, cooldown_remaining, blessings_used, block_reason = await _check_recipient_cooldown(int(member.id))
        if not can_receive and cooldown_remaining:
            bearer_name = _strip_display_name(member.display_name)
            cooldown_str = _format_cooldown_time(cooldown_remaining)
            if block_reason == "per_blessing":
                await interaction.response.send_message(
                    f"**{bearer_name}** was recently blessed. The machine spirit must settle before further rites.\n"
                    f"Next blessing available in {cooldown_str}.",
                    ephemeral=True,
                )
            else:  # daily_cap
                await interaction.response.send_message(
                    f"**{bearer_name}** has reached their daily blessing limit ({BLESSING_RECIPIENT_MAX_PER_DAY} per day).\n"
                    f"Next blessing slot available in {cooldown_str}.",
                    ephemeral=True,
                )
            return

    # ─────────────────────────────────────────────────────────────────────────
    # Check armor integrity state BEFORE clearing
    # ─────────────────────────────────────────────────────────────────────────
    current_damage_tier = _b("_get_member_damage_tier")(member)
    was_damaged = current_damage_tier is not None
    spirit_fractured = await _check_spirit_fracture(int(member.id))

    # Get armor status for status lines
    armor_status = _get_armor_status_for_blessing(was_damaged, current_damage_tier, spirit_fractured)

    # Find the responsible attestor based on BEARER's company/role (not caller)
    attestor_member, role_key = _b("_find_responsible_attestor")(member, interaction.guild)
    if attestor_member is None:
        # No forgemaster found in guild - fall back to caller with their actual role
        attestor_member = interaction.user
        role_key = _caller_role_key

    # ─────────────────────────────────────────────────────────────────────────
    # Intensive mode validation and charge calculation
    # ─────────────────────────────────────────────────────────────────────────
    charges_required = 1  # Standard blessing
    is_intensive = intensive

    if intensive:
        charges_required = _get_intensive_charge_cost(current_damage_tier, spirit_fractured)
        if charges_required == 0:
            # Target is nominal - intensive not applicable
            await interaction.response.send_message(
                "No damage to repair. Use standard blessing for routine maintenance.",
                ephemeral=True,
            )
            return

    # ─────────────────────────────────────────────────────────────────────────
    # Techmarine blessing pool check with collaborative pooling for intensive
    # ─────────────────────────────────────────────────────────────────────────
    # Track charge contributions: list of (user_id, charges_to_consume)
    blessing_pool_contributions = []
    is_collaborative = False

    if not force:
        invoker_id = int(interaction.user.id)
        attestor_id = int(attestor_member.id)
        invoker_is_attestor = invoker_id == attestor_id

        # Get available charges for both parties
        attestor_charges = await _get_techmarine_available_charges(attestor_id)
        invoker_charges = await _get_techmarine_available_charges(invoker_id) if not invoker_is_attestor else 0

        if invoker_is_attestor:
            # Solo mode: invoker IS the attestor
            if attestor_charges >= charges_required:
                blessing_pool_contributions = [(attestor_id, charges_required)]
            else:
                if intensive:
                    await interaction.response.send_message(
                        f"Intensive blessing requires **{charges_required}** charges. You have **{attestor_charges}**.\n"
                        f"Ask another Techmarine to invoke this rite for collaborative pooling.",
                        ephemeral=True,
                    )
                else:
                    _, _, attestor_time_until_regen = await _check_techmarine_can_bless(attestor_id)
                    regen_str = (
                        _format_cooldown_time(attestor_time_until_regen) if attestor_time_until_regen else "4h 48m"
                    )
                    await interaction.response.send_message(
                        f"Your blessing pool is depleted. The sacred oils must be replenished.\n"
                        f"Next blessing available in: **{regen_str}**",
                        ephemeral=True,
                    )
                return
        else:
            # Invoker is different from attestor - collaborative pooling possible
            combined_charges = attestor_charges + invoker_charges

            if attestor_charges >= charges_required:
                # Attestor alone can handle it
                blessing_pool_contributions = [(attestor_id, charges_required)]
            elif attestor_charges == 0 and invoker_charges >= charges_required:
                # Attestor has no charges - invoker takes over as attestor entirely
                attestor_member = interaction.user
                attestor_id = invoker_id
                role_key = _caller_role_key
                blessing_pool_contributions = [(invoker_id, charges_required)]
            elif combined_charges >= charges_required:
                # Combined pool is sufficient. Only treat this as collaborative
                # when both parties materially contribute charges.
                attestor_contribution = attestor_charges
                invoker_contribution = charges_required - attestor_charges
                is_collaborative = attestor_contribution > 0 and invoker_contribution > 0
                blessing_pool_contributions = [
                    (attestor_id, attestor_contribution),
                ]
                if invoker_contribution > 0:
                    blessing_pool_contributions.append((invoker_id, invoker_contribution))
            else:
                # Neither has enough even combined
                if intensive:
                    await interaction.response.send_message(
                        f"Intensive blessing requires **{charges_required}** charges.\n"
                        f"**{attestor_member.display_name}** has {attestor_charges}, you have {invoker_charges} "
                        f"(combined: {combined_charges}).\n"
                        f"Requisition more supplies or reduce scope.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "Both the attesting Techmarine and your blessing pools are depleted. "
                        "Seek another Techmarine to perform this rite.",
                        ephemeral=True,
                    )
                return

    # ─────────────────────────────────────────────────────────────────────────
    # Forge balance check - every blessing drains forge reserves
    # ─────────────────────────────────────────────────────────────────────────
    forge_drain_cost = 0
    if not force:
        # Calculate forge drain based on tier being healed
        drain_per_charge = FORGE_DRAIN_PER_CHARGE.get(current_damage_tier, 1)
        forge_drain_cost = drain_per_charge * charges_required

        # Check if forge has sufficient balance
        forge_available = await _get_forge_pool_available()
        if forge_available < forge_drain_cost:
            bearer_name = _strip_display_name(member.display_name)
            tier_name = current_damage_tier.upper() if current_damage_tier else "NOMINAL"
            await interaction.response.send_message(
                f"**FORGE RESERVES DEPLETED**\n\n"
                f"Target: **{bearer_name}** ({tier_name})\n"
                f"Forge cost: **{forge_drain_cost}** pts ({drain_per_charge} pts/charge × {charges_required} charges)\n"
                f"Available: **{forge_available}** pts\n\n"
                f"*The Chapter must recover more armory data before blessings can continue.*",
                ephemeral=True,
            )
            return

    # Build attestation using standardized Imperial date format
    try:
        ts = _b("_format_imperial_date")(datetime.utcnow())
    except Exception:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Authority based on attestor's company/role (or combined for collab)
    if role_key == "forgemaster" and not is_collaborative:
        authority = "Jericho High Command"
    elif is_collaborative:
        # For collaborative, check if either party is Forgemaster
        # Forgemasters use "Jericho High Command", Techmarines use their company
        if role_key == "forgemaster":
            attestor_comp = "Jericho High Command"
        else:
            attestor_comp = _find_company_or_chapter(attestor_member) or "Unknown"

        if _caller_role_key == "forgemaster":
            invoker_comp = "Jericho High Command"
        else:
            invoker_comp = _find_company_or_chapter(interaction.user) or "Unknown"

        if attestor_comp != invoker_comp:
            authority = f"{attestor_comp} & {invoker_comp}"
        else:
            authority = attestor_comp
    else:
        comp = _find_company_or_chapter(attestor_member) or "Unknown Company"
        authority = comp

    # Attesting name from the RESPONSIBLE attestor (strip stud pips)
    attester = getattr(attestor_member, "display_name", None) or getattr(
        attestor_member, "name", str(attestor_member.id)
    )
    attester = attester.replace("●", "").replace("⚬", "").strip()

    # Get techmarine's rank emoji for attestation
    tech_rank_name = "Forgemaster" if role_key == "forgemaster" else "Watch Techmarine"
    tech_rank_emoji = _get_rank_emoji(interaction.guild, tech_rank_name) if interaction.guild else ""

    # Optional personal rite from the RESPONSIBLE attestor
    try:
        rite_text = await _get_user_rite(int(attestor_member.id))
        # Safety truncation for legacy rites that may exceed the limit
        if rite_text and len(rite_text) > MAX_RITE_LENGTH:
            rite_text = rite_text[: MAX_RITE_LENGTH - 3] + "..."
    except Exception:
        rite_text = None

    # ─────────────────────────────────────────────────────────────────────────
    # Dynamic personalization
    # ─────────────────────────────────────────────────────────────────────────

    # Bearer info: rank honorific, display name, and Kill Team/Company title
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(member)
    # Defensive pip stripping - ensure no stud pips in display name
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()

    # Bearer's home chapter for chapter-specific blessing (use dedicated function)
    bearer_chapter = _get_bearer_home_chapter(member)
    chapter_blessing = None
    if bearer_chapter and bearer_chapter in CHAPTER_BLESSINGS:
        chapter_blessing = CHAPTER_BLESSINGS[bearer_chapter]
    elif bearer_chapter:
        # Check case-insensitive fallback
        for chap_name, blessing in CHAPTER_BLESSINGS.items():
            if chap_name.lower() == bearer_chapter.lower():
                chapter_blessing = blessing
                break

    # Service studs computation
    bearer_studs = _compute_member_service_studs(member)

    # Techmarine acknowledgment (dynamically blended by rank prestige vs stud count)
    stud_acknowledgment = _get_techmarine_acknowledgment_blended(member, bearer_studs)

    # Random sacred Mechanicus phrase (special phrases for self-blessing)
    is_self_blessing = attestor_member.id == member.id
    if is_self_blessing:
        sacred_phrase = _blend_forgemaster_self_attestation(bearer_chapter)
    else:
        sacred_phrase = random.choice(SACRED_MECHANICUS_PHRASES)

    # ─────────────────────────────────────────────────────────────────────────
    # Machine-spirit designation with armor integrity awareness
    # ─────────────────────────────────────────────────────────────────────────

    existing_spirit = await _get_machine_spirit(int(member.id))
    spirit_is_returning = False
    spirit_is_reconsecrated = False
    spirit_is_restored = False
    spirit_is_first = False

    if spirit_fractured:
        # Spirit was lost due to neglect at critical - generate new spirit (re-consecration)
        spirit_hash = hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:6].upper()
        spirit_prefixes = [
            # Aggression/Combat
            "FURY",
            "WRATH",
            "MORTIS",
            "VENATOR",
            "GLADIUS",
            "BELLATOR",
            "FEROX",
            "CARNIFEX",
            "VINDICTA",
            "MALLEUS",
            # Protection/Vigilance
            "AEGIS",
            "VIGIL",
            "PURITY",
            "CUSTODIAN",
            "SENTINEL",
            "BULWARK",
            "DEFENSOR",
            "CASTELLAN",
            "PRAESIDIUM",
            "SCUTUM",
            # Strength/Endurance
            "FERRUM",
            "ADAMANT",
            "TITANICUS",
            "INVICTUS",
            "FORTIS",
            # Mechanicus/Sacred
            "SACRIS",
            "SANCTUS",
            "FERVOR",
            "COGNIS",
            "ANIMUS",
            # Predatory
            "TALON",
            "RAPTOR",
            "LUPUS",
            "AQUILA",
            "CORVUS",
        ]
        spirit_suffixes = [
            # Greek letters (expanded)
            "Α",
            "Β",
            "Γ",
            "Δ",
            "Ε",
            "Ζ",
            "Η",
            "Θ",
            "Ι",
            "Κ",
            "Λ",
            "Μ",
            "Ν",
            "Ξ",
            "Ο",
            "Π",
            "Ρ",
            "Σ",
            "Τ",
            "Υ",
            "Φ",
            "Χ",
            "Ψ",
            "Ω",
            # Roman numerals
            "I",
            "II",
            "III",
            "IV",
            "V",
            "VI",
            "VII",
            "VIII",
            "IX",
            "X",
        ]
        spirit_designation = f"{random.choice(spirit_prefixes)}-{spirit_hash}-{random.choice(spirit_suffixes)}"
        await _set_machine_spirit(int(member.id), spirit_designation)
        spirit_is_reconsecrated = True
    elif existing_spirit:
        # Spirit intact - preserve it
        spirit_designation = existing_spirit
        if was_damaged:
            spirit_is_restored = True
        else:
            spirit_is_returning = True
    else:
        # First blessing - generate and store new spirit
        spirit_hash = hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:6].upper()
        spirit_prefixes = [
            # Aggression/Combat
            "FURY",
            "WRATH",
            "MORTIS",
            "VENATOR",
            "GLADIUS",
            "BELLATOR",
            "FEROX",
            "CARNIFEX",
            "VINDICTA",
            "MALLEUS",
            # Protection/Vigilance
            "AEGIS",
            "VIGIL",
            "PURITY",
            "CUSTODIAN",
            "SENTINEL",
            "BULWARK",
            "DEFENSOR",
            "CASTELLAN",
            "PRAESIDIUM",
            "SCUTUM",
            # Strength/Endurance
            "FERRUM",
            "ADAMANT",
            "TITANICUS",
            "INVICTUS",
            "FORTIS",
            # Mechanicus/Sacred
            "SACRIS",
            "SANCTUS",
            "FERVOR",
            "COGNIS",
            "ANIMUS",
            # Predatory
            "TALON",
            "RAPTOR",
            "LUPUS",
            "AQUILA",
            "CORVUS",
        ]
        spirit_suffixes = [
            # Greek letters (expanded)
            "Α",
            "Β",
            "Γ",
            "Δ",
            "Ε",
            "Ζ",
            "Η",
            "Θ",
            "Ι",
            "Κ",
            "Λ",
            "Μ",
            "Ν",
            "Ξ",
            "Ο",
            "Π",
            "Ρ",
            "Σ",
            "Τ",
            "Υ",
            "Φ",
            "Χ",
            "Ψ",
            "Ω",
            # Roman numerals
            "I",
            "II",
            "III",
            "IV",
            "V",
            "VI",
            "VII",
            "VIII",
            "IX",
            "X",
        ]
        spirit_designation = f"{random.choice(spirit_prefixes)}-{spirit_hash}-{random.choice(spirit_suffixes)}"
        await _set_machine_spirit(int(member.id), spirit_designation)
        spirit_is_first = True

    # Flavor text for spirit status
    if spirit_is_reconsecrated:
        spirit_status_text = random.choice(SPIRIT_RECONSECRATION_PHRASES)
    elif spirit_is_restored:
        spirit_status_text = random.choice(SPIRIT_RESTORATION_PHRASES)
    elif spirit_is_returning:
        spirit_status_phrases = [
            "The machine spirit stirs, recognizing its bearer",
            "Ancient recognition-rites confirm: spirit and bearer are one",
            "The spirit awakens from dormancy, its vigilance renewed",
            "Cogitator confirms: spirit-bond integrity remains absolute",
            "The spirit hums with familiarity—it knows your biorhythms well",
            "Binharic acknowledgment received. The spirit welcomes its master home",
            "Neural handshake successful. Spirit-bond resonance at optimal levels",
            "The armor's animus pulses with recognition. You are known. You are accepted.",
            "Data-communion confirms: bearer identity verified across all subroutines",
            "The spirit's sensors sweep you with mechanical affection. The bond holds true.",
        ]
        spirit_status_text = random.choice(spirit_status_phrases)
    else:  # spirit_is_first
        spirit_status_phrases = [
            "First binding complete. Spirit and bearer are now one",
            "Virgin armor awakened. The spirit stirs for the first time",
            "Inaugural consecration. May this bond endure ten thousand years",
            "New spirit bound to bearer by sacred rite of the Omnissiah",
            "The machine spirit opens its awareness for the first time—and finds you waiting",
            "Activation protocols complete. The spirit learns your name, your scent, your purpose",
            "From dormancy, consciousness. From emptiness, bond. The spirit claims you as its own.",
            "The first data-handshake is always sacred. Spirit and bearer, now interlinked.",
            "Boot sequence finalized. The spirit's first thought is of duty—and of you.",
            "The Rite of First Awakening concludes. A new partnership is forged in sacred code.",
        ]
        spirit_status_text = random.choice(spirit_status_phrases)

    # ─────────────────────────────────────────────────────────────────────────
    # Roll blessing outcome and apply effect
    # Standard: rolls for crit_fail/normal/crit_success based on damage state
    # Intensive: guaranteed full heal to nominal (no roll, no crits)
    # ─────────────────────────────────────────────────────────────────────────
    blessing_result_tier = current_damage_tier  # Track resulting damage tier

    if is_intensive:
        # Intensive mode: guaranteed full heal, no roll
        blessing_roll_outcome = "normal"  # For display purposes
        if interaction.guild:
            blessing_result_tier = await _apply_blessing_intensive_normal(member, interaction.guild)
    else:
        # Standard mode: roll for outcome
        blessing_roll_outcome = _roll_blessing_outcome(
            damage_tier=current_damage_tier,
            spirit_fractured=spirit_fractured,
        )
        if interaction.guild:
            if blessing_roll_outcome == "crit_fail":
                # Crit fail: reset points but damage stays
                blessing_result_tier = await _apply_blessing_crit_fail(member, interaction.guild)
            elif blessing_roll_outcome == "crit_success":
                # Crit success: full heal + grace period
                blessing_result_tier = await _apply_blessing_crit_success(
                    member, interaction.guild, charges_invested=charges_required
                )
            else:
                # Normal: drop one damage tier
                blessing_result_tier = await _apply_blessing_normal(member, interaction.guild)

    # Consume blessings from the contributing Techmarine(s) pools (unless force override)
    if not force and blessing_pool_contributions:
        for contrib_user_id, contrib_charges in blessing_pool_contributions:
            # Get display name for the contributing techmarine
            contrib_member = interaction.guild.get_member(contrib_user_id) if interaction.guild else None
            contrib_display_name = contrib_member.display_name if contrib_member else None
            
            if contrib_charges == 1:
                await _consume_blessing(contrib_user_id, display_name=contrib_display_name)
            elif contrib_charges > 1:
                await _consume_multiple_blessings(contrib_user_id, contrib_charges, display_name=contrib_display_name)

    # Deduct forge reserves (unless force override)
    if not force and forge_drain_cost > 0:
        await _deduct_forge_pool_balance(forge_drain_cost, current_damage_tier)

    # ─────────────────────────────────────────────────────────────────────────
    # Build embed
    # ─────────────────────────────────────────────────────────────────────────

    # Get emojis for rank and chapter
    guild = interaction.guild
    # Extract raw rank name from bearer_honorific by reverse-lookup
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    chapter_emoji = _get_emoji_by_name(guild, bearer_chapter) if guild and bearer_chapter else None

    embed = discord.Embed(
        title="⚙️ COGITATOR RITE — FORGE ATTESTATION",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0x2ECC71,
    )

    # Bearer field with emojis
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_value = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"
    if bearer_title:
        bearer_value += f"\n*{bearer_title}*"
    if bearer_chapter:
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if bearer_chapter == "Black Shield" else bearer_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_value += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Status field with dynamic armor status
    # Determine status emoji based on armor state
    plate_status = armor_status.get("plate", "NOMINAL")
    spirit_status = armor_status.get("spirit", "STABLE")
    rite_status = armor_status.get("rite", "MAINTENANCE")

    # Use appropriate emoji based on status
    plate_emoji = "🟢" if plate_status == "NOMINAL" else ("🔴" if "CRITICAL" in plate_status else "⚠️")
    spirit_emoji = "🟢" if spirit_status == "STABLE" else ("🔴" if spirit_status == "FRACTURED" else "⚠️")
    rite_emoji = "🟢" if rite_status == "MAINTENANCE" else ("⚠️" if rite_status == "RE-CONSECRATION" else "🟢")

    # Get MachineSpirit emoji for spirit field
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"

    status_value = (
        f"{machine_spirit_emoji} `{spirit_designation}`\n"
        f"*{spirit_status_text}*\n"
        f"{plate_emoji} Plate: {plate_status}\n"
        f"{spirit_emoji} Spirit: {spirit_status}\n"
        f"{rite_emoji} Rite: {rite_status}"
    )
    embed.add_field(name="▸ Machine-Spirit", value=status_value, inline=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Rite Outcome field (shows roll result)
    # ─────────────────────────────────────────────────────────────────────────
    charges_text = f" ({charges_required} charges)" if is_intensive and charges_required > 1 else ""

    if blessing_roll_outcome == "crit_fail":
        outcome_emoji = "⚠️"
        outcome_title = "RITE RESISTED"
        if current_damage_tier:
            tier_display = current_damage_tier.upper()
            if is_intensive:
                outcome_text = f"The machine spirit resists the intensive rites{charges_text}.\nDamage persists: **{tier_display}**"
            else:
                outcome_text = f"The machine spirit resists the sacred oils.\nDamage persists: **{tier_display}**"
        else:
            outcome_text = "The machine spirit stirs uneasily.\nThe rite takes imperfect hold."
    elif blessing_roll_outcome == "crit_success":
        outcome_emoji = "✨"
        outcome_title = "SACRED COMMUNION"
        if is_intensive and charges_required > 1:
            grace_multiplier = f"×{charges_required}"
            if current_damage_tier:
                outcome_text = f"The Omnissiah rewards the {charges_required}-charge offering.\nAll damage purged. **Enhanced grace period** ({grace_multiplier}) granted."
            else:
                outcome_text = f"Perfect communion achieved through intensive rites.\nThe machine spirit radiates profound contentment. **Enhanced grace period** ({grace_multiplier}) granted."
        elif current_damage_tier:
            outcome_text = "The Omnissiah's blessing flows through the armor.\nAll damage purged. Grace period granted."
        else:
            outcome_text = "Perfect communion achieved.\nThe machine spirit radiates contentment. Grace period granted."
    else:  # normal
        if is_intensive:
            outcome_emoji = "✨"
            outcome_title = "INTENSIVE RITE COMPLETE"
            if current_damage_tier:
                outcome_text = f"Full restoration{charges_text}: {current_damage_tier.upper()} → NOMINAL\nThe armor is whole once more."
            else:
                outcome_text = "Maintenance rites complete.\nThe machine spirit rests content."
        else:
            outcome_emoji = "🟢"
            outcome_title = "RITE COMPLETE"
            if current_damage_tier and blessing_result_tier:
                outcome_text = f"Damage reduced: {current_damage_tier.upper()} → {blessing_result_tier.upper()}"
            elif current_damage_tier and not blessing_result_tier:
                outcome_text = f"Damage repaired: {current_damage_tier.upper()} → NOMINAL"
            else:
                outcome_text = "Maintenance rites complete.\nThe machine spirit rests content."

    outcome_value = f"{outcome_emoji} **{outcome_title}**\n{outcome_text}"

    # Add forge cost info if applicable
    if not force and forge_drain_cost > 0:
        drain_per_charge = FORGE_DRAIN_PER_CHARGE.get(current_damage_tier, 1)
        forge_remaining = await _get_forge_pool_available()

        if charges_required > 1:
            forge_cost_text = f"\n\n⚙️ Forge: **-{forge_drain_cost}** pts ({drain_per_charge}×{charges_required}) → {forge_remaining} pts"
        else:
            forge_cost_text = f"\n\n⚙️ Forge: **-{forge_drain_cost}** pts → {forge_remaining} pts"
        outcome_value += forge_cost_text

    embed.add_field(name="▸ Rite Outcome", value=outcome_value, inline=True)

    # Determine whether to show extended fields (Honor of Long Watch, Litany)
    # Always show extended fields for all blessings
    show_extended_fields = True

    # Honor of the Long Watch (only for unbound/fractured spirits)
    if show_extended_fields:
        tier_for_honor = _studs_tier(bearer_studs)
        if tier_for_honor == 1:
            ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER1)
        elif tier_for_honor == 2:
            ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER2)
        else:
            ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER3)

        # Format pronouns based on self-blessing
        if is_self_blessing:
            ordo_honor_embed = ordo_honor_embed.format(possessive="my", possessive_cap="My", object="me")
        else:
            ordo_honor_embed = ordo_honor_embed.format(possessive="your", possessive_cap="Your", object="you")

        if chapter_blessing:
            embed.add_field(
                name="▸ Honor of the Long Watch",
                value=f'*"{ordo_honor_embed} {stud_acknowledgment} {chapter_blessing}"*',
                inline=False,
            )
        else:
            embed.add_field(
                name="▸ Honor of the Long Watch",
                value=f'*"{ordo_honor_embed} {stud_acknowledgment}"*',
                inline=False,
            )

    # Litany to the Machine-Spirit (only for unbound/fractured spirits with custom rite)
    if show_extended_fields and rite_text:
        rite_display = str(rite_text)[:400] + ("…" if len(str(rite_text)) > 400 else "")
        embed.add_field(name="▸ Litany to the Machine-Spirit", value=f"{rite_display}", inline=False)

    # Attestation (self-blessing uses different field name, collaborative shows both Techmarines)
    rank_emoji_prefix = f"{tech_rank_emoji} " if tech_rank_emoji else ""

    if is_collaborative:
        # Collaborative attestation: show both Techmarines with charge contributions
        # Get invoker's rank emoji
        invoker_rank_name = "Forgemaster" if _caller_role_key == "forgemaster" else "Watch Techmarine"
        invoker_rank_emoji = _get_rank_emoji(interaction.guild, invoker_rank_name) if interaction.guild else ""
        invoker_prefix = f"{invoker_rank_emoji} " if invoker_rank_emoji else ""
        invoker_name = _strip_display_name(interaction.user.display_name)

        # Build contribution lines
        contrib_lines = []
        for contrib_user_id, contrib_charges in blessing_pool_contributions:
            if contrib_user_id == int(attestor_member.id):
                contrib_lines.append(f"{rank_emoji_prefix}**{attester}** ({contrib_charges})")
            else:
                contrib_lines.append(f"{invoker_prefix}**{invoker_name}** ({contrib_charges})")

        tech_value = f'{chr(10).join(contrib_lines)}\n{authority} • {ts}\n*"{sacred_phrase}"*'
        attestation_field_name = "▸ Joint Attestation"
    else:
        # Solo attestation (original logic)
        attester_with_rank = f"{rank_emoji_prefix}**{attester}**"
        tech_value = f'{attester_with_rank}\n{authority} • {ts}\n*"{sacred_phrase}"*'
        attestation_field_name = "▸ Self-Attestation" if is_self_blessing else "▸ Attestation"

    embed.add_field(name=attestation_field_name, value=tech_value, inline=True)

    # ─────────────────────────────────────────────────────────────────────────
    # All blessings are ephemeral with "Log to Forge" button
    # Chronicle always tracks the rite, public posting is optional via button
    # ─────────────────────────────────────────────────────────────────────────
    is_significant_event, spirit_event = _classify_forge_rite_event(
        spirit_is_first, spirit_is_reconsecrated, spirit_is_restored
    )

    # Always use the full embed format
    display_embed = embed

    # Create the view with Log to Forge button
    log_view = LogToForgeView(
        embed=display_embed,
        member_id=int(member.id),
        member_mention=member.mention,
        techmarine_id=int(attestor_member.id),
        spirit_designation=spirit_designation,
        spirit_event=spirit_event,
        is_intensive=is_intensive,
        is_significant=is_significant_event,
    )

    # Send ephemeral blessing with button and mention
    send_succeeded = False
    try:
        await interaction.response.send_message(
            content=member.mention,
            embed=display_embed,
            view=log_view,
            ephemeral=True,
        )
        send_succeeded = True
    except Exception:
        try:
            await interaction.response.send_message("Failed to post attestation.", ephemeral=True)
        except Exception:
            pass

    # Record rite in chronicle (tracks all blessings, not just logged ones)
    # Skip chronicle entry when force override is used (Forgemaster testing/admin use)
    if send_succeeded and not force:
        await _record_rite_in_chronicle(
            bearer_id=int(member.id),
            techmarine_id=int(attestor_member.id),
            rite_type="intensive" if is_intensive else "standard",
            spirit_designation=spirit_designation,
            spirit_event=spirit_event,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Armor Status Command
# ─────────────────────────────────────────────────────────────────────────────


def _get_armor_status_allowed_channels() -> set:
    """Get allowed channel IDs for armor_status command from config."""
    config = _get_armor_config()
    channel_ids = config.get("armor_status_allowed_channels", [])

    allowed_channels: set[int] = set()
    for c in channel_ids:
        if not c:
            continue
        try:
            allowed_channels.add(int(c))
        except (TypeError, ValueError):
            # Skip invalid entries to avoid breaking the command on bad config
            continue

    return allowed_channels


def _calculate_armor_risk_score(
    damage_tier: Optional[str],
    points_since_blessing: int,
    spirit_fractured: bool,
) -> int:
    """Calculate a risk score for sorting armor status leaderboard.

    Higher score = more urgent/at-risk.
    Score components:
    - Fractured spirit: +10000
    - Critical tier: +3000
    - Compromised tier: +2000
    - Damaged tier: +1000
    - Points since blessing: direct add
    """
    score = 0
    if spirit_fractured:
        score += 10000
    elif damage_tier == "critical":
        score += 3000
    elif damage_tier == "compromised":
        score += 2000
    elif damage_tier == "damaged":
        score += 1000
    score += points_since_blessing
    return score


async def _show_armor_leaderboard(
    interaction: discord.Interaction,
    guild: discord.Guild,
    company_filter: Optional[str] = None,
    pool_remaining: Optional[int] = None,
    pool_next_regen: Optional[timedelta] = None,
    techmarine_id: Optional[int] = None,
    authority_bracket_ids: Optional[set] = None,
):
    """Show top 10 brothers at risk of armor damage.

    Scope is gap-filling: ``company_filter`` is the caller's own company.
      • Ring 0 = own company (primary responsibility)
      • Ring 1 = orphan companies (no Watch Techmarine assigned)
      • Ring 2 = peer-covered companies
    Top 10 fills from ring 0 first, then 1, then 2. If ``company_filter`` is
    ``None`` (Forgemaster), the leaderboard is fortress-wide with no rings.
    pool_remaining/pool_next_regen show invoker's blessing pool status.
    techmarine_id is used to check intensive scan status.
    """
    # Check if Techmarine has intensive scan active
    has_intensive = False
    if techmarine_id:
        has_intensive = await _has_intensive_scan(techmarine_id)

    # Load all armor states
    armor_data = _load_armor_integrity()

    if not armor_data:
        embed = discord.Embed(
            title="᛭⋅ ARMOR INTEGRITY SCAN ⋅᛭",
            description="*No armor integrity records on file.*",
            color=0x5D6D7E,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Compute orphan-company set once for ring assignment.
    orphan_companies: set = (
        _b("_orphan_companies_for_role")(guild, "Watch Techmarine")
        if company_filter
        else set()
    )

    # Build list of (member, state, current_tier, risk_score, scan_result, ring, member_company)
    risk_list = []
    for user_id_str, state in armor_data.items():
        try:
            user_id = int(user_id_str)
        except ValueError:
            continue

        member = guild.get_member(user_id)
        if not member:
            continue

        # Skip non-participants (no rank, Reserves, Interred).
        is_active_fn = _b("_is_active_participant")
        if is_active_fn:
            if not is_active_fn(member):
                continue
        else:
            if not any(r.name in RANK_HONORIFICS for r in member.roles):
                continue

        member_company = _b("_get_member_company_name")(member)
        # Determine ring: caller_company=None means fortress-wide (everyone in ring 0)
        ring = (
            _b("_company_scope_ring")(member_company, company_filter, orphan_companies)
            if company_filter
            else 0
        )

        # Get damage tier from roles (more accurate than stored state)
        current_tier = _b("_get_member_damage_tier")(member)
        points_since_blessing = state.get("points_since_blessing", 0)
        spirit_fractured = state.get("spirit_fractured", False)

        # Roll detection for this brother (cached per AAR cycle)
        scan_result = await _get_or_roll_scan_result(user_id, current_tier, points_since_blessing, spirit_fractured)

        # Intensive scan bypasses miss chance
        if has_intensive and not scan_result["detected"]:
            scan_result = {"detected": True, "predictive_warning": False, "miss_reason": None}

        risk_score = _calculate_armor_risk_score(current_tier, points_since_blessing, spirit_fractured)

        # Include if they have risk OR if there's a predictive warning OR if scan missed (damaged but undetected)
        if risk_score > 0 or scan_result.get("predictive_warning") or not scan_result["detected"]:
            risk_list.append((member, state, current_tier, risk_score, scan_result, ring, member_company))

    # Sort by (ring asc, risk_score desc) — own-company first, then orphans, then peers.
    risk_list.sort(key=lambda x: (x[5], -x[3]))

    # Authority-bracket filter: if the viewer's bracket has any brother whose
    # armor is *actually damaged* (damaged/compromised/critical tier, or
    # spirit_fractured), suppress out-of-bracket entries. Nominal brothers
    # with accumulated cycles since their last blessing do NOT keep the gate
    # closed — only real damage does. Out-of-bracket falls through only when
    # the in-bracket cohort is at-or-below nominal. authority_bracket_ids
    # being None disables the gate.
    bracket_suppressed_out_of_bracket = False
    if authority_bracket_ids is not None:
        in_bracket = [e for e in risk_list if e[0].id in authority_bracket_ids]

        def _needs_attention(entry) -> bool:
            _m, _state, _tier, _risk, _scan, _ring, _co = entry
            if _tier in ("damaged", "compromised", "critical"):
                return True
            if (_state or {}).get("spirit_fractured"):
                return True
            return False

        any_in_bracket_damaged = any(_needs_attention(e) for e in in_bracket)
        if any_in_bracket_damaged:
            risk_list = in_bracket
            bracket_suppressed_out_of_bracket = True

    # Filter out brothers on cooldown before taking top 10
    available_brothers = []
    for entry in risk_list:
        member = entry[0]
        can_receive, _, _, _ = await _check_recipient_cooldown(member.id)
        if can_receive:
            available_brothers.append(entry)

    # Take top 10 from available brothers
    top_10 = available_brothers[:10]

    # Randomize only nominal and undetected brothers (damaged tiers stay risk-ordered)
    import random

    def _get_display_tier(entry):
        """Get display tier for grouping (detected status + damage tier)."""
        _, state, tier, _, scan_result, _ring, _co = entry
        if not scan_result["detected"]:
            return "undetected"
        fractured = state.get("spirit_fractured", False)
        if fractured:
            return "fractured"
        return tier or "nominal"

    # Split into risk-ordered (damaged tiers) and randomized (nominal/undetected),
    # but preserve ring-then-severity order *within* each group.
    damaged_entries = [e for e in top_10 if _get_display_tier(e) not in ("nominal", "undetected")]
    nominal_entries = [e for e in top_10 if _get_display_tier(e) == "nominal"]
    undetected_entries = [e for e in top_10 if _get_display_tier(e) == "undetected"]

    # Damaged stay sorted by (ring, risk), randomize nominal/undetected
    random.shuffle(nominal_entries)
    random.shuffle(undetected_entries)

    # Reassemble: damaged first (by ring/risk), then nominal (random), then undetected (random)
    top_10 = damaged_entries + nominal_entries + undetected_entries

    # Count expansion across rings for description text
    expansion_count = sum(1 for e in top_10 if e[5] > 0)

    # Build description based on company filter
    if bracket_suppressed_out_of_bracket:
        # Authority-bracket gate is active — only in-bracket brothers are listed.
        if company_filter:
            company_short = _b("_extract_company_short_name")(company_filter)
            with_risk_desc = (
                f"*Top brothers in **{company_short}** requiring attention "
                f"(wider fortress hidden until your company is nominal)*"
            )
            no_risk_desc = (
                f"*All brothers in {company_short} are nominal. "
                f"No maintenance required.*"
            )
        else:
            with_risk_desc = (
                "*Top brothers requiring attention (High Command + Techmarines; "
                "wider fortress hidden until your authority is nominal)*"
            )
            no_risk_desc = "*High Command and Techmarines all nominal.*"
    elif company_filter:
        company_short = _b("_extract_company_short_name")(company_filter)
        if expansion_count > 0:
            no_risk_desc = "*All brothers nominal across all companies. No maintenance required.*"
            with_risk_desc = (
                f"*Top 10 — **{company_short}** + {expansion_count} backfilled "
                f"from companies needing coverage*"
            )
        else:
            no_risk_desc = (
                f"*All brothers in {company_short} and beyond are nominal. "
                f"No maintenance required.*"
            )
            with_risk_desc = f"*Top 10 brothers in {company_short} requiring attention*"
    else:
        no_risk_desc = "*All brothers nominal. No maintenance required.*"
        with_risk_desc = "*Top 10 brothers requiring attention (fortress-wide)*"

    # Get MachineSpirit emoji
    _machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"  # Reserved for future use

    # Build intensive scan indicator for embed description
    intensive_indicator = "\n🔬 **Intensive Scan ACTIVE** — 100% detection" if has_intensive else ""

    if not top_10:
        embed = discord.Embed(
            title="᛭⋅ ARMOR INTEGRITY SCAN ⋅᛭",
            description=f"{no_risk_desc}{intensive_indicator}",
            color=0x2ECC71,  # Green
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Build the leaderboard embed
    embed = discord.Embed(
        title="᛭⋅ ARMOR INTEGRITY SCAN ⋅᛭",
        description=f"{with_risk_desc}{intensive_indicator}",
        color=0xE67E22,  # Orange
    )

    lines = []
    for i, (member, state, current_tier, risk_score, scan_result, _ring, _co) in enumerate(top_10, 1):
        points = state.get("points_since_blessing", 0)
        spirit_fractured = state.get("spirit_fractured", False)
        predictive_warning = scan_result.get("predictive_warning", False)
        scan_missed = not scan_result["detected"]

        # Get display name (short) - always show name even if scan missed
        bearer_honorific, bearer_name, _ = _get_bearer_rank_and_title(member)
        bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
        # Truncate long names
        if len(bearer_name) > 18:
            bearer_name = bearer_name[:16] + "…"

        # Get rank emoji
        bearer_rank_name = None
        for rank, hon in RANK_HONORIFICS.items():
            if hon == bearer_honorific or rank in bearer_honorific:
                bearer_rank_name = rank
                break
        if not bearer_rank_name:
            bearer_rank_name = "Watch Brother"
        rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
        rank_str = f"{rank_emoji} " if rank_emoji else ""

        # Get home chapter emoji
        bearer_chapter = _get_bearer_home_chapter(member)
        chapter_emoji = _get_emoji_by_name(guild, bearer_chapter) if bearer_chapter and guild else None
        chapter_str = f"{chapter_emoji}" if chapter_emoji else ""

        # Per-line company tag for entries outside the caller's company (ring > 0).
        # Lead Forgemaster (no caller company) shows no tags.
        company_tag = ""
        if company_filter and _ring and _ring > 0 and _co:
            try:
                company_tag = f" `({_b('_extract_company_short_name')(_co)})`"
            except Exception:
                company_tag = ""

        # Handle missed scans - show name but mask data
        if scan_missed:
            icon = "⚫"
            chapter_sep = f"{chapter_str} · " if chapter_str else "· "
            lines.append(f"`{i:>2}.` {icon} {rank_str}{bearer_name}{company_tag} {chapter_sep}???")
            continue

        # Status icon - predictive warnings get special indicator
        if spirit_fractured:
            icon = "💀"
        elif current_tier == "critical":
            icon = "🔴"
        elif current_tier == "compromised":
            icon = "🟠"
        elif current_tier == "damaged":
            icon = "🟡"
        elif predictive_warning:
            icon = "⚡"  # Warning for nominal brothers at risk
        else:
            icon = "🟢"

        # Format compact line: "1. 🔴 :rank: Name :chapter: · 275c"
        # Status indicated by icon only (no text label needed)
        # Only show cycles for at-risk/damaged brothers, not nominal
        chapter_sep = f"{chapter_str} · " if chapter_str else "· "
        if icon == "🟢":
            # Nominal brothers don't need cycle count shown
            lines.append(f"`{i:>2}.` {icon} {rank_str}{bearer_name}{company_tag} {chapter_str}")
        else:
            # At-risk/damaged brothers show cycles for triage
            lines.append(f"`{i:>2}.` {icon} {rank_str}{bearer_name}{company_tag} {chapter_sep}{points}c")

    embed.add_field(
        name="▸ Brothers at Risk",
        value="\n".join(lines),
        inline=False,
    )

    # Add legend (compact) - include undetected symbol
    legend = "💀Fractured 🔴Critical 🟠Compromised 🟡Damaged ⚡At Risk 🟢Nominal ⚫Undetected"
    embed.add_field(
        name="▸ Key",
        value=legend,
        inline=False,
    )

    # Add invoker's blessing pool status with color-coded regen indicator
    if pool_remaining is not None:
        # Color code regen based on pool level (percentage of max):
        # 🔴 Red: 0-33% (critical/empty)
        # 🟡 Yellow: 33-66% (depleted)
        # 🟢 Green: 66-100% (nominal/full)
        pool_percent = pool_remaining / BLESSING_POOL_MAX if BLESSING_POOL_MAX > 0 else 0
        if pool_percent <= 0.33:
            regen_icon = "🔴"
        elif pool_percent <= 0.66:
            regen_icon = "🟡"
        else:
            regen_icon = "🟢"

        if pool_next_regen and pool_remaining < BLESSING_POOL_MAX:
            hours, remainder = divmod(int(pool_next_regen.total_seconds()), 3600)
            minutes = remainder // 60
            regen_str = f" · {regen_icon} +1 in {hours}h {minutes}m" if hours else f" · {regen_icon} +1 in {minutes}m"
        else:
            regen_str = ""
        embed.add_field(
            name="▸ Your Blessing Pool",
            value=f"({pool_remaining}/{BLESSING_POOL_MAX}){regen_str}\n`/forge_rite @brother`",
            inline=True,
        )

    # Add forge requisition pool status
    try:
        forge_status = await _get_forge_pool_status()
        forge_available = forge_status["available"]
        forge_charges = forge_status["charges_available"]
        intensive_scans_available = forge_available // INTENSIVE_SCAN_COST
        embed.add_field(
            name="▸ Forge Reserves",
            value=(
                f"**{forge_available:,}** pts │ {forge_charges} charges │ {intensive_scans_available} scans\n"
                f"`/requisition_supplies`"
            ),
            inline=True,
        )
    except Exception:
        pass

    await interaction.response.send_message(embed=embed, ephemeral=True)


@_g.bot.tree.command(
    name="armor_status",
    description="View top 10 at-risk brothers in your company (Techmarine) or all (Forgemaster).",
)
async def _armor_status(interaction: discord.Interaction):
    """Display armor integrity leaderboard scoped by role."""
    if not await _is_forge_enabled():
        await interaction.response.send_message(
            "The Techmarine subsystem is currently disabled.", ephemeral=True
        )
        return

    # Permission check: caller must be techmarine or forgemaster
    allowed, role_key = _b("_is_techmarine_or_forgemaster")(interaction.user, command_name="armor_status")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Channel restriction
    channel_id = getattr(interaction.channel, "id", None)
    allowed_channels = _get_armor_status_allowed_channels()
    if channel_id not in allowed_channels:
        await interaction.response.send_message(
            "This command may only be used in the arming chamber or Techmarine channels.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    # Determine company scope based on role.
    # Forgemaster (lead) is always fortress-wide. Techmarines start with their
    # own company as ring 0, with gap-filling backfilling from other companies
    # when their home turf is quiet. A Techmarine not assigned to any company
    # falls through to fortress-wide.
    if role_key == "forgemaster":
        company_filter = None
    else:
        company_filter = _b("_get_member_company_name")(interaction.user)

    # Resolve authority bracket so out-of-bracket entries are suppressed until
    # the viewer's own bracket is fully clear. See _compute_authority_bracket_member_ids
    # in bot.py for semantics.
    bracket_fn = _b("_compute_authority_bracket_member_ids")
    authority_bracket_ids = (
        bracket_fn(interaction.user, guild, role_key, "techmarine")
        if bracket_fn else None
    )

    # Get invoker's blessing pool status
    pool_remaining, pool_next_regen = await _get_blessing_pool_display(interaction.user.id)

    await _show_armor_leaderboard(
        interaction,
        guild,
        company_filter=company_filter,
        pool_remaining=pool_remaining,
        pool_next_regen=pool_next_regen,
        techmarine_id=interaction.user.id,
        authority_bracket_ids=authority_bracket_ids,
    )


@_g.bot.tree.command(
    name="requisition_supplies",
    description="Spend community armory reserves for blessing charges or intensive scans.",
)
@app_commands.describe(
    requisition_type="What to requisition: blessing charge (restore pool) or intensive scan (guaranteed detection)",
)
@app_commands.choices(
    requisition_type=[
        app_commands.Choice(name="Blessing Charge (+1 to pool)", value="blessing_charge"),
        app_commands.Choice(name="Intensive Scan (20 pts, 100% detection)", value="intensive_scan"),
    ]
)
async def _requisition_supplies(
    interaction: discord.Interaction,
    requisition_type: str = "blessing_charge",
):
    """Techmarine command to requisition supplies from the forge pool."""
    if not await _is_forge_enabled():
        await interaction.response.send_message(
            "The Techmarine subsystem is currently disabled.", ephemeral=True
        )
        return

    # Permission check: caller must be techmarine or forgemaster
    allowed, role_key = _b("_is_techmarine_or_forgemaster")(interaction.user, command_name="requisition_supplies")
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Channel restriction (same as armor_status)
    channel_id = getattr(interaction.channel, "id", None)
    allowed_channels = _get_armor_status_allowed_channels()
    if channel_id not in allowed_channels:
        await interaction.response.send_message(
            "This command may only be used in the arming chamber or Techmarine channels.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    # Branch based on requisition type
    if requisition_type == "intensive_scan":
        await _handle_intensive_scan_requisition(interaction, guild)
        return

    # Default: blessing charge requisition
    # Check current blessing pool status
    pool_remaining, pool_next_regen = await _get_blessing_pool_display(interaction.user.id)

    # Don't allow requisition if pool is full
    if pool_remaining >= BLESSING_POOL_MAX:
        await interaction.response.send_message(
            f"Your blessing pool is already full ({pool_remaining}/{BLESSING_POOL_MAX}). No requisition needed.",
            ephemeral=True,
        )
        return

    # Check daily usage
    daily_used = await _get_techmarine_daily_requisitions(interaction.user.id)
    if daily_used >= FORGE_POOL_DAILY_LIMIT:
        await interaction.response.send_message(
            f"Daily requisition limit reached ({FORGE_POOL_DAILY_LIMIT} per day). "
            "The Forge requires time to process additional requests.",
            ephemeral=True,
        )
        return

    # Get forge pool status for display
    forge_status = await _get_forge_pool_status()
    available = forge_status["available"]
    cost = forge_status["cost_per_charge"]

    if available < cost:
        await interaction.response.send_message(
            f"**Forge Requisition Denied**\n\n"
            f"Community armory reserves: **{available}** points\n"
            f"Required for blessing charge: **{cost}** points\n\n"
            f"*The Chapter must recover more armory data before supplies can be requisitioned.*",
            ephemeral=True,
        )
        return

    # Attempt to consume the requisition
    success, message = await _consume_forge_requisition(interaction.user.id)

    if not success:
        await interaction.response.send_message(
            f"**Forge Requisition Failed**\n\n{message}",
            ephemeral=True,
        )
        return

    # Grant an immediate blessing charge by resetting the oldest timestamp
    # This effectively gives them back one blessing slot immediately
    await _grant_blessing_charge(interaction.user.id)

    # Get updated pool status
    new_pool, _ = await _get_blessing_pool_display(interaction.user.id)
    new_forge_status = await _get_forge_pool_status()

    # Get the Techmarine's name
    tech_name = _strip_display_name(interaction.user.display_name)

    embed = discord.Embed(
        title="⚙️ FORGE REQUISITION APPROVED",
        description="*Sacred oils and blessed unguents have been allocated.*",
        color=0x2ECC71,
    )

    embed.add_field(
        name="▸ Requisitioner",
        value=f"**{tech_name}**",
        inline=True,
    )

    embed.add_field(
        name="▸ Blessing Pool",
        value=f"({new_pool}/{BLESSING_POOL_MAX})",
        inline=True,
    )

    embed.add_field(
        name="▸ Forge Reserves",
        value=f"**{new_forge_status['available']}** armory points\n({new_forge_status['charges_available']} charges available)",
        inline=True,
    )

    embed.add_field(
        name="▸ Daily Usage",
        value=f"{daily_used + 1}/{FORGE_POOL_DAILY_LIMIT} requisitions today",
        inline=True,
    )

    embed.set_footer(text="The Omnissiah provides. Use these gifts wisely.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _grant_blessing_charge(user_id: int):
    """Grant one blessing charge to a Techmarine by removing the oldest timestamp."""
    async with _g.BLESSING_POOL_LOCK:
        data = _load_blessing_pool()
        state = data.get(
            str(user_id),
            {
                "remaining_blessings": BLESSING_POOL_MAX,
                "blessing_timestamps": [],
            },
        )

        timestamps = state.get("blessing_timestamps", [])

        if not timestamps:
            # Already at max, nothing to remove
            return

        # Sort timestamps and remove the oldest one
        now = datetime.utcnow()
        regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600

        # Find active (non-regenerated) timestamps
        active_timestamps = []
        for ts_str in timestamps:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
                elapsed = (now - ts).total_seconds()
                if elapsed < regen_seconds:
                    active_timestamps.append((ts, ts_str))
            except Exception:
                pass

        if active_timestamps:
            # Remove the oldest active timestamp (grants one blessing back)
            active_timestamps.sort(key=lambda x: x[0])
            remaining_ts = [ts_str for _, ts_str in active_timestamps[1:]]
        else:
            remaining_ts = []

        state["blessing_timestamps"] = remaining_ts
        state["remaining_blessings"] = BLESSING_POOL_MAX - len(remaining_ts)

        data[str(user_id)] = state
        _save_blessing_pool(data)


async def _handle_intensive_scan_requisition(
    interaction: discord.Interaction,
    guild: discord.Guild,
):
    """Handle intensive scan requisition (100% detection for this AAR cycle)."""
    tech_id = interaction.user.id

    # Check if already has active intensive scan
    if await _has_intensive_scan(tech_id):
        await interaction.response.send_message(
            "**Intensive Scan Already Active**\n\n"
            "*Your augur arrays are already operating at maximum sensitivity for this cycle.*\n"
            "The scan expires when new armory data is ingested.",
            ephemeral=True,
        )
        return

    # Get forge pool status
    forge_status = await _get_forge_pool_status()
    available = forge_status["available"]

    if available < INTENSIVE_SCAN_COST:
        await interaction.response.send_message(
            f"**Intensive Scan Denied**\n\n"
            f"Community armory reserves: **{available}** points\n"
            f"Required for intensive scan: **{INTENSIVE_SCAN_COST}** points\n\n"
            f"*Insufficient resources to power the augur arrays at maximum sensitivity.*",
            ephemeral=True,
        )
        return

    # Consume the points directly from forge pool
    async with _g.FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
        pool_data["balance"] = pool_data.get("balance", max_balance) - INTENSIVE_SCAN_COST
        _save_forge_pool(pool_data)

    # Activate intensive scan for this Techmarine
    await _purchase_intensive_scan(tech_id)

    # Get updated forge status
    new_forge_status = await _get_forge_pool_status()

    # Get the Techmarine's name
    tech_name = _strip_display_name(interaction.user.display_name)

    embed = discord.Embed(
        title="🔬 INTENSIVE SCAN ACTIVATED",
        description=(
            "*Augur arrays recalibrated to maximum sensitivity.*\n"
            "*All armor spirits shall be revealed, none shall hide from the Omnissiah's gaze.*"
        ),
        color=0x9B59B6,  # Purple for special scan
    )

    embed.add_field(
        name="▸ Requisitioner",
        value=f"**{tech_name}**",
        inline=True,
    )

    embed.add_field(
        name="▸ Cost",
        value=f"**{INTENSIVE_SCAN_COST}** armory points",
        inline=True,
    )

    embed.add_field(
        name="▸ Forge Reserves",
        value=f"**{new_forge_status['available']}** pts remaining",
        inline=True,
    )

    embed.add_field(
        name="▸ Effect",
        value=(
            "• 100% detection for all armor states\n"
            "• Bypasses spirit uncommunicative readings\n"
            "• Expires when new armory data is ingested"
        ),
        inline=False,
    )

    embed.set_footer(text="The Machine Spirit yields its secrets. Use /armor_status now.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Forge Chronicle Dashboard
# ─────────────────────────────────────────────────────────────────────────────


def _abbreviate_spirit(designation: str) -> str:
    """Abbreviate a machine spirit designation for compact display.

    'SANCTUS-FD35EE-Μ' → 'FD35-Μ'
    Falls back to the original string if the format is unexpected.
    """
    parts = designation.split("-")
    if len(parts) == 3:
        return f"{parts[1][:4]}-{parts[2]}"
    return designation


def _format_time_ago(ts: datetime) -> str:
    """Format a timestamp as a human-readable 'X ago' string."""
    now = datetime.utcnow()
    delta = now - ts
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return "just now"
    elif total_seconds < 3600:
        mins = total_seconds // 60
        return f"{mins}m ago"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours}h ago"
    else:
        days = total_seconds // 86400
        return f"{days}d ago"


async def _build_forge_chronicle_embed(guild: discord.Guild) -> discord.Embed:
    """Build the Forge Chronicle dashboard embed with atmospheric stats."""
    # Load chronicle data
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()

    rite_history = data.get("rite_history", [])
    techmarine_stats = data.get("techmarine_stats", {})

    # Get forge pool status
    forge_status = await _get_forge_pool_status()
    available = forge_status["available"]
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE

    # Load machine spirits
    spirits_data = _load_machine_spirits()
    _total_spirits = len(spirits_data)  # Reserved for future use

    # Load armor integrity data
    armor_data = _load_armor_integrity()

    now = datetime.utcnow()
    _first_of_month = datetime(now.year, now.month, 1)  # Reserved for future use

    # Get MachineSpirit emoji
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"

    # ─────────────────────────────────────────────────────────────
    # Section 0: Fortress Status
    # ─────────────────────────────────────────────────────────────
    # Count brothers by damage status + calculate forge pressure metrics
    # Only includes brothers whose armor status is readable (scan detected)
    total_brothers_with_armor = 0
    nominal_count = 0
    damaged_count = 0
    compromised_count = 0
    critical_count = 0
    fractured_count = 0

    # Brothers needing attention: damaged+ OR nominal at 5+ cycles (entering risk zone)
    brothers_needing_attention = 0

    is_active_fn = _b("_is_active_participant")
    for member in guild.members:
        if is_active_fn:
            if not is_active_fn(member):
                continue
        else:
            # Fallback: legacy ranked + non-Reserves filter
            if member.bot:
                continue
            if not any(r.name in RANK_HONORIFICS for r in member.roles):
                continue
            role_ids = {r.id for r in member.roles}
            role_names = {r.name.lower() for r in member.roles}
            if RESERVES_ROLE_ID in role_ids or "reserves" in role_names:
                continue

        user_id_str = str(member.id)
        state = armor_data.get(user_id_str, {})
        points = state.get("points_since_blessing", 0)
        spirit_fractured = state.get("spirit_fractured", False)
        damage_tier = _b("_get_member_damage_tier")(member)

        # Check scan detection - skip unreadable brothers
        scan_result = await _get_or_roll_scan_result(member.id, damage_tier, points, spirit_fractured)
        if not scan_result["detected"]:
            continue

        total_brothers_with_armor += 1

        # Count by damage tier and track brothers needing attention
        if spirit_fractured:
            fractured_count += 1
            brothers_needing_attention += 1
        elif damage_tier == "critical":
            critical_count += 1
            brothers_needing_attention += 1
        elif damage_tier == "compromised":
            compromised_count += 1
            brothers_needing_attention += 1
        elif damage_tier == "damaged":
            damaged_count += 1
            brothers_needing_attention += 1
        else:
            nominal_count += 1
            # Nominal brothers at 5+ cycles are entering risk zone
            if points >= 5:
                brothers_needing_attention += 1

    _total_damaged = damaged_count + compromised_count + critical_count + fractured_count  # Reserved for future use
    nominal_pct = (nominal_count / total_brothers_with_armor * 100) if total_brothers_with_armor > 0 else 100

    # ─────────────────────────────────────────────────────────────
    # Section 1: Machine Spirits of the Watch
    # ─────────────────────────────────────────────────────────────
    activity_status = _b("_load_activity_status")()

    # Helper to check if a member is active (not in Reserves) AND has Watch rank
    def _is_member_eligible(member_id_str: str) -> bool:
        try:
            member = guild.get_member(int(member_id_str))
            if not member:
                return False
            is_active_fn = _b("_is_active_participant")
            if is_active_fn:
                return bool(is_active_fn(member))
            # Fallback: legacy ranked + non-Reserves filter
            if not any(r.name in RANK_HONORIFICS for r in member.roles):
                return False
            role_ids = {r.id for r in member.roles}
            role_names = {r.name.lower() for r in member.roles}
            if RESERVES_ROLE_ID in role_ids or "reserves" in role_names:
                return False
            return True
        except Exception:
            pass
        # Fallback to activity_status
        member_status = activity_status.get(member_id_str, {}).get("status")
        return member_status == "active"

    eldest_spirit = None
    newest_spirit = None
    eldest_date = None
    newest_date = None

    for member_id, spirit_info in spirits_data.items():
        if isinstance(spirit_info, str):
            continue
        bound_ts = spirit_info.get("bound_ts")
        if bound_ts:
            try:
                bound_dt = datetime.fromisoformat(bound_ts)
                # Only consider active members for both eldest and youngest
                if _is_member_eligible(member_id):
                    if eldest_date is None or bound_dt < eldest_date:
                        eldest_date = bound_dt
                        eldest_spirit = (member_id, spirit_info)
                    if newest_date is None or bound_dt > newest_date:
                        newest_date = bound_dt
                        newest_spirit = (member_id, spirit_info)
            except Exception:
                pass

    # Find most attended spirit (lifetime maintenance rites, active members only)
    maintenance_counts = {}
    for r in rite_history:
        if r.get("event") == "maintenance":
            spirit = r.get("spirit")
            bearer_id = r.get("bearer_id")
            if spirit and bearer_id and _is_member_eligible(bearer_id):
                key = (bearer_id, spirit)
                maintenance_counts[key] = maintenance_counts.get(key, 0) + 1

    most_attended = None
    most_attended_count = 0
    for (bearer_id, spirit), count in maintenance_counts.items():
        if count > most_attended_count:
            most_attended_count = count
            most_attended = (bearer_id, spirit)

    spirit_lines = []
    now = datetime.utcnow()
    if eldest_spirit:
        member_id, info = eldest_spirit
        designation = info.get("designation", "UNKNOWN") if isinstance(info, dict) else info
        member_label = _b("_format_member_styled")(guild, member_id, include_chapter=True)
        eldest_days = (now - eldest_date).days if eldest_date else 0
        spirit_lines.append(f"Eldest ({eldest_days}d): **{_abbreviate_spirit(designation)}** {member_label}")

    if newest_spirit and newest_spirit != eldest_spirit:
        member_id, info = newest_spirit
        designation = info.get("designation", "UNKNOWN") if isinstance(info, dict) else info
        member_label = _b("_format_member_styled")(guild, member_id, include_chapter=True)
        newest_hours = int((now - newest_date).total_seconds() // 3600) if newest_date else 0
        spirit_lines.append(f"Youngest ({newest_hours}h): **{_abbreviate_spirit(designation)}** {member_label}")

    # Find most resilient spirit (lifetime restoration events, active members only)
    restoration_counts = {}
    for r in rite_history:
        if r.get("event") == "restoration":
            spirit = r.get("spirit")
            bearer_id = r.get("bearer_id")
            if spirit and bearer_id and _is_member_eligible(bearer_id):
                key = (bearer_id, spirit)
                restoration_counts[key] = restoration_counts.get(key, 0) + 1

    most_resilient = None
    most_resilient_count = 0
    for (bearer_id, spirit), count in restoration_counts.items():
        if count > most_resilient_count:
            most_resilient_count = count
            most_resilient = (bearer_id, spirit)

    # Show most attended spirit
    if most_attended:
        bearer_id, spirit = most_attended
        member_label = _b("_format_member_styled")(guild, bearer_id, include_chapter=True)
        spirit_lines.append(f"Devoted ({most_attended_count} rites): **{_abbreviate_spirit(spirit)}** {member_label}")

    # Show most resilient spirit (if any restorations this month)
    if most_resilient:
        bearer_id, spirit = most_resilient
        member_label = _b("_format_member_styled")(guild, bearer_id, include_chapter=True)
        spirit_lines.append(f"Unbowed ({most_resilient_count} wounds): **{_abbreviate_spirit(spirit)}** {member_label}")

    # ─────────────────────────────────────────────────────────────
    # Section 3: Watchlist (5 random brothers with armor)
    # ─────────────────────────────────────────────────────────────
    watchlist_entries = []
    is_active_fn_wl = _b("_is_active_participant")
    for member in guild.members:
        if is_active_fn_wl:
            if not is_active_fn_wl(member):
                continue
        else:
            if member.bot:
                continue
            if not any(r.name in RANK_HONORIFICS for r in member.roles):
                continue
            role_ids = {r.id for r in member.roles}
            role_names = {r.name.lower() for r in member.roles}
            if RESERVES_ROLE_ID in role_ids or "reserves" in role_names:
                continue

        user_id_str = str(member.id)
        state = armor_data.get(user_id_str, {})
        # Include any brother with armor record
        if not state:
            continue

        # Get damage tier from roles (consistent with armor_status command)
        damage_tier = _b("_get_member_damage_tier")(member)
        spirit_fractured = state.get("spirit_fractured", False)
        points = state.get("points_since_blessing", 0)

        # Use same scan detection as armor_status (cached per AAR cycle)
        scan_result = await _get_or_roll_scan_result(member.id, damage_tier, points, spirit_fractured)
        detected = scan_result["detected"]
        predictive_warning = scan_result.get("predictive_warning", False)

        risk_score = _calculate_armor_risk_score(damage_tier, points, spirit_fractured)

        watchlist_entries.append(
            (member, damage_tier, spirit_fractured, points, risk_score, detected, predictive_warning)
        )

    # Randomly select 5 from all brothers (cycles through different brothers each refresh)
    import random

    if len(watchlist_entries) > 5:
        watchlist_top5 = random.sample(watchlist_entries, 5)
    else:
        watchlist_top5 = list(watchlist_entries)

    # Sort by risk, but randomize only nominal and undetected (damaged tiers stay risk-ordered)
    def _watchlist_tier(entry):
        """Get tier for sorting watchlist entries."""
        _, tier, fractured, pts, _, detected, predictive_warning = entry
        if not detected:
            return "undetected"
        if fractured:
            return "fractured"
        if tier in ("critical", "compromised", "damaged"):
            return tier
        if predictive_warning:
            return "at_risk"
        return "nominal"

    # Split: damaged tiers stay risk-ordered, nominal/undetected get randomized
    damaged_wl = [e for e in watchlist_top5 if _watchlist_tier(e) not in ("nominal", "undetected")]
    nominal_wl = [e for e in watchlist_top5 if _watchlist_tier(e) == "nominal"]
    undetected_wl = [e for e in watchlist_top5 if _watchlist_tier(e) == "undetected"]

    # Sort damaged by risk score (desc), randomize nominal/undetected
    damaged_wl.sort(key=lambda x: x[4], reverse=True)
    random.shuffle(nominal_wl)
    random.shuffle(undetected_wl)

    watchlist_top5 = damaged_wl + nominal_wl + undetected_wl

    watchlist_lines = []
    for member, tier, fractured, pts, score, detected, predictive_warning in watchlist_top5:
        # Check cooldown status
        can_receive, _, _, _ = await _check_recipient_cooldown(member.id)
        cooldown_indicator = " ⏳" if not can_receive else ""

        if not detected:
            icon = "⚫"
        elif fractured:
            icon = "💀"
        elif tier == "critical":
            icon = "🔴"
        elif tier == "compromised":
            icon = "🟠"
        elif tier == "damaged":
            icon = "🟡"
        elif predictive_warning:
            icon = "⚡"  # At risk (predictive warning from scan)
        else:
            icon = "🟢"  # Nominal
        name = _b("_format_member_styled")(guild, str(member.id), include_chapter=True)
        # Only show cycles for at-risk/damaged brothers, not nominal or unreadable
        if icon == "🟢":
            watchlist_lines.append(f"{icon} {name}{cooldown_indicator}")
        elif icon == "⚫":
            # Unreadable - mask data like armor_status does
            watchlist_lines.append(f"{icon} {name} · ???")
        else:
            watchlist_lines.append(f"{icon} {name} · {pts}c{cooldown_indicator}")

    if not watchlist_lines:
        watchlist_lines.append("*No armor records found.*")

    # ─────────────────────────────────────────────────────────────
    # Section 4: Forge Readiness (Enhanced)
    # ─────────────────────────────────────────────────────────────
    reserve_pct = (available / max_balance) * 100 if max_balance > 0 else 0
    filled_blocks = int(reserve_pct / 10)
    empty_blocks = 10 - filled_blocks
    reserve_bar = "█" * filled_blocks + "░" * empty_blocks

    # Load blessing pool data for artificers section AND forge pressure calculation
    blessing_pool_data = _load_blessing_pool()

    def _get_charges_from_pool_state(state: dict) -> int:
        """Calculate available charges from pool state timestamps."""
        timestamps = state.get("blessing_timestamps", [])
        active = _filter_active_blessing_timestamps(timestamps)
        return max(0, min(BLESSING_POOL_MAX - len(active), BLESSING_POOL_MAX))

    def _get_soonest_regen_from_pool_state(state: dict) -> Optional[timedelta]:
        """Get time until next charge regenerates for a pool state, or None if full."""
        timestamps = state.get("blessing_timestamps", [])
        active = _filter_active_blessing_timestamps(timestamps)
        if len(active) == 0:
            return None  # Pool is full

        now = datetime.utcnow()
        regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
        oldest_ts = None
        for ts_str in active:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
                if oldest_ts is None or ts < oldest_ts:
                    oldest_ts = ts
            except Exception:
                pass

        if oldest_ts:
            time_until_regen = timedelta(seconds=regen_seconds) - (now - oldest_ts)
            if time_until_regen.total_seconds() > 0:
                return time_until_regen
        return timedelta(seconds=0)  # About to regen

    # Calculate total Techmarine charges and soonest regen across all Techmarines
    techmarine_role = discord.utils.get(guild.roles, name=TECHMARINE_ROLE_NAME)
    total_techmarine_charges = 0
    soonest_regen: Optional[timedelta] = None
    total_regenning = 0  # Count of charges currently regenerating

    if techmarine_role:
        for member in techmarine_role.members:
            if member.bot:
                continue
            tech_pool = blessing_pool_data.get(str(member.id), {})
            charges = _get_charges_from_pool_state(tech_pool)
            total_techmarine_charges += charges

            # Track regenning charges and soonest regen time
            timestamps = tech_pool.get("blessing_timestamps", [])
            active = _filter_active_blessing_timestamps(timestamps)
            total_regenning += len(active)

            regen_time = _get_soonest_regen_from_pool_state(tech_pool)
            if regen_time is not None:
                if soonest_regen is None or regen_time < soonest_regen:
                    soonest_regen = regen_time

    # Calculate Forge Pressure = demand / available charges
    # If no charges available, pressure is infinite (shown as "∞")
    if total_techmarine_charges > 0:
        forge_pressure = brothers_needing_attention / total_techmarine_charges
    else:
        forge_pressure = float("inf") if brothers_needing_attention > 0 else 0.0

    # ─────────────────────────────────────────────────────────────
    # Section 5: Artificers of the Watch
    # ─────────────────────────────────────────────────────────────
    artificer_lines = []
    if techmarine_stats:
        # Sort by: charges (desc), success rate (desc), total rites (desc)
        def artificer_sort_key(item):
            tech_id, stats = item
            total = stats.get("total_rites", 0)
            successes = stats.get("successes", 0)
            success_rate = (successes / total) * 100 if total > 0 else 0
            tech_pool = blessing_pool_data.get(str(tech_id), {})
            charges = _get_charges_from_pool_state(tech_pool)
            return (charges, success_rate, total)

        sorted_techs = sorted(
            techmarine_stats.items(),
            key=artificer_sort_key,
            reverse=True,
        )[:3]

        for tech_id, stats in sorted_techs:
            total = stats.get("total_rites", 0)
            if total > 0:
                member = guild.get_member(int(tech_id))
                if member:
                    name = _b("_format_member_styled")(guild, str(tech_id), include_chapter=True)
                    # Get current charges for this techmarine
                    tech_pool = blessing_pool_data.get(str(tech_id), {})
                    charges = _get_charges_from_pool_state(tech_pool)
                    artificer_lines.append(f"{name} ({charges})")

    # ─────────────────────────────────────────────────────────────
    # Section 6: Spirit Memorial (Spirits lost in last 28 days)
    # ─────────────────────────────────────────────────────────────
    memorial_lines = []

    # Show fractured/released events from the last 28 days
    cutoff = now - timedelta(days=28)
    lost_spirits = []
    for r in rite_history:
        if r.get("event") in ("fractured", "released"):
            try:
                ts = datetime.fromisoformat(r.get("ts", ""))
                if ts >= cutoff:
                    lost_spirits.append(r)
            except Exception:
                pass

    # Sort by most recent first
    lost_spirits.sort(key=lambda x: x.get("ts", ""), reverse=True)

    # Deduplicate: keep only the most recent event per (bearer_id, spirit) pair
    seen_spirits: set = set()
    deduped_spirits = []
    for r in lost_spirits:
        key = (r.get("bearer_id"), r.get("spirit"))
        if key not in seen_spirits:
            seen_spirits.add(key)
            deduped_spirits.append(r)
    lost_spirits = deduped_spirits

    for entry in lost_spirits[:3]:  # Max 3
        bearer_id = entry.get("bearer_id")
        spirit = entry.get("spirit")
        event_type = entry.get("event")
        age_days = entry.get("age_days")
        if bearer_id and spirit:
            member_label = _b("_format_member_styled")(guild, str(bearer_id), include_chapter=True)
            age_str = f"({age_days}d) " if age_days is not None else ""
            # 💀 for fractured, 💤 for released (dormant)
            if event_type == "fractured":
                memorial_lines.append(f"💀 **{_abbreviate_spirit(spirit)}** {age_str}{member_label}")
            else:
                memorial_lines.append(f"💤 **{_abbreviate_spirit(spirit)}** {age_str}{member_label}")


    # ─────────────────────────────────────────────────────────────
    # Build the embed description
    # ─────────────────────────────────────────────────────────────
    # Build the embed with fields for inline layout
    # ─────────────────────────────────────────────────────────────

    embed = discord.Embed(
        title=f"{machine_spirit_emoji} FORGE CHRONICLE {machine_spirit_emoji}",
        color=0x5D6D7E,
    )

    # Armory Telemetry (description - top prominence)
    fortress_icon = "🟢" if nominal_pct >= 90 else ("🟡" if nominal_pct >= 70 else "🔴")
    # Pressure: 🟢 < 1.0 covered, 🟡 < 2.0 elevated, 🔴 >= 2.0 strained, ⚠️ ∞ critical
    if forge_pressure == float("inf"):
        pressure_icon = "⚠️"
        pressure_str = "∞"
    elif forge_pressure < 1.0:
        pressure_icon = "🟢"
        pressure_str = f"{forge_pressure:.1f}x"
    elif forge_pressure < 2.0:
        pressure_icon = "🟡"
        pressure_str = f"{forge_pressure:.1f}x"
    else:
        pressure_icon = "🔴"
        pressure_str = f"{forge_pressure:.1f}x"

    # Regen display: show "+N in Xh" or "Full" if all techmarines at max
    if total_regenning == 0:
        regen_icon = "🟢"
        regen_str = "Full"
    elif soonest_regen is not None:
        regen_icon = "🟡"
        regen_hours = soonest_regen.total_seconds() / 3600
        if regen_hours < 1:
            regen_mins = int(soonest_regen.total_seconds() / 60)
            regen_str = f"+1 in {regen_mins}m ({total_regenning} on CD)"
        else:
            regen_str = f"+1 in {regen_hours:.1f}h ({total_regenning} on CD)"
    else:
        regen_icon = "🟢"
        regen_str = "Full"

    fortress_text = (
        f"**▸ Armory Telemetry**\n"
        f"{fortress_icon} **{nominal_pct:.0f}%** Nominal  "
        f"{pressure_icon} **{pressure_str}** Pressure  "
        f"{regen_icon} **{regen_str}** Regen"
    )
    embed.description = fortress_text

    # Watchlist (full width)
    if watchlist_top5:
        watchlist_value = "\n".join(watchlist_lines)
        # Truncate if exceeds Discord's 1024 character limit
        if len(watchlist_value) > 1024:
            truncated_lines = []
            current_length = 0
            for line in watchlist_lines:
                line_with_newline = line + "\n"
                footer = f"\n*...and {len(watchlist_lines) - len(truncated_lines)} more*"
                if current_length + len(line_with_newline) + len(footer) > 1024:
                    break
                truncated_lines.append(line)
                current_length += len(line_with_newline)
            
            hidden_count = len(watchlist_lines) - len(truncated_lines)
            if hidden_count > 0:
                watchlist_value = "\n".join(truncated_lines) + f"\n*...and {hidden_count} more*"
            else:
                watchlist_value = "\n".join(truncated_lines)
        
        embed.add_field(
            name="▸ Watchlist",
            value=watchlist_value,
            inline=False,
        )

    # ─────────────────────────────────────────────────────────────
    # Recent Rites (Last 5 blessing rites)
    # ─────────────────────────────────────────────────────────────
    recent_rites_lines = []
    
    # Get Omnissiah Seal emoji for rites
    forge_blessing_emoji = _get_emoji_by_name(guild, "OmnissianSeal") or "⚙️"
    
    # Filter for blessing events only
    blessing_events = []
    for r in rite_history:
        if r.get("event") in ("first_binding", "rebirth", "restoration", "maintenance"):
            blessing_events.append(r)
    
    # Sort by timestamp descending (most recent first)
    blessing_events.sort(key=lambda x: x.get("ts", ""), reverse=True)
    
    # Take last 5
    for entry in blessing_events[:5]:
        bearer_id = entry.get("bearer_id")
        tech_id = entry.get("techmarine_id")
        spirit = entry.get("spirit")
        event_type = entry.get("event")
        ts_str = entry.get("ts")
        
        if bearer_id and tech_id and spirit:
            # Format member names
            tech_name = _b("_format_member_styled")(guild, str(tech_id), include_chapter=True)
            bearer_name = _b("_format_member_styled")(guild, str(bearer_id), include_chapter=True)
            
            # Format rite type display
            if event_type == "first_binding":
                rite_display = "First Binding"
            elif event_type == "rebirth":
                rite_display = "Rebirth"
            elif event_type == "restoration":
                rite_display = "Restoration"
            else:  # maintenance
                rite_display = "Maintenance"
            
            # Calculate time ago
            try:
                ts = datetime.fromisoformat(ts_str)
                time_ago = _b("_format_time_ago")(ts)
            except Exception:
                time_ago = "???"
            
            recent_rites_lines.append(
                f"{forge_blessing_emoji} {tech_name} → {bearer_name} ({rite_display}) • {time_ago}"
            )
    
    if not recent_rites_lines:
        recent_rites_lines.append("*No recent rites recorded.*")
    
    # Truncate to fit Discord's 1024 character limit for field values
    recent_rites_value = "\n".join(recent_rites_lines)
    if len(recent_rites_value) > 1024:
        # Build truncated version by including lines until we hit the limit
        truncated_lines = []
        current_length = 0
        hidden_count = 0
        
        for line in recent_rites_lines:
            line_with_newline = line + "\n"
            # Reserve space for "...and X more" message
            footer = f"\n*...and {len(recent_rites_lines) - len(truncated_lines)} more*"
            if current_length + len(line_with_newline) + len(footer) > 1024:
                hidden_count = len(recent_rites_lines) - len(truncated_lines)
                break
            truncated_lines.append(line)
            current_length += len(line_with_newline)
        
        if hidden_count > 0:
            recent_rites_value = "\n".join(truncated_lines) + f"\n*...and {hidden_count} more*"
        else:
            recent_rites_value = "\n".join(truncated_lines)
    
    embed.add_field(
        name="▸ Recent Rites",
        value=recent_rites_value,
        inline=False,
    )

    # Machine Spirits (full width)
    spirit_text = "\n".join(spirit_lines)
    # Truncate if exceeds Discord's 1024 character limit
    if len(spirit_text) > 1024:
        truncated_lines = []
        current_length = 0
        for line in spirit_lines:
            line_with_newline = line + "\n"
            footer = f"\n*...and {len(spirit_lines) - len(truncated_lines)} more*"
            if current_length + len(line_with_newline) + len(footer) > 1024:
                break
            truncated_lines.append(line)
            current_length += len(line_with_newline)
        
        hidden_count = len(spirit_lines) - len(truncated_lines)
        if hidden_count > 0:
            spirit_text = "\n".join(truncated_lines) + f"\n*...and {hidden_count} more*"
        else:
            spirit_text = "\n".join(truncated_lines)
    
    embed.add_field(
        name=f"▸ {machine_spirit_emoji} Machine Spirits",
        value=spirit_text,
        inline=False,
    )

    # Spirit Memorial (full width, only if exists)
    if memorial_lines:
        embed.add_field(
            name="▸ Spirit Memorial",
            value="\n".join(memorial_lines),
            inline=False,
        )

    # ─────────────────────────────────────────────────────────────
    # Forge Reserves: Calculate weekly intake/drain/net
    # ─────────────────────────────────────────────────────────────
    cutoff_7d = now - timedelta(days=7)

    # Calculate 7-day intake from AAR records
    weekly_intake = 0
    all_records = _g.DATASTORE.get_all_records()
    for record in all_records.values():
        try:
            rec_ts_str = record.get("timestamp", "")
            if rec_ts_str:
                rec_ts = datetime.fromisoformat(rec_ts_str)
                # Make rec_ts naive to match cutoff_7d (both UTC)
                if rec_ts.tzinfo is not None:
                    rec_ts = rec_ts.replace(tzinfo=None)
                if rec_ts >= cutoff_7d:
                    weekly_intake += record.get("armory_challenge_points", 0) or 0
        except Exception:
            pass

    # Calculate 7-day drain from forge pool log
    weekly_drain = 0
    forge_pool_data = _load_forge_pool()
    drain_log = forge_pool_data.get("weekly_drain_log", [])
    for entry in drain_log:
        try:
            entry_ts = datetime.fromisoformat(entry.get("ts", ""))
            if entry_ts >= cutoff_7d:
                weekly_drain += entry.get("points", 0)
        except Exception:
            pass

    weekly_net = weekly_intake - weekly_drain

    # Format net with trend icon
    if weekly_net > 0:
        net_icon = "📈"
        net_text = f"+{weekly_net}"
    elif weekly_net < 0:
        net_icon = "📉"
        net_text = str(weekly_net)
    else:
        net_icon = "➡️"
        net_text = "0"

    forge_reserves_value = (
        f"{reserve_bar} {available:,} / {max_balance:,} pts\n"
        f"📊 7d: +{weekly_intake} in | -{weekly_drain} out | {net_icon} {net_text} net"
    )

    # Forge Reserves + Artificers (inline pair)
    embed.add_field(
        name="▸ Forge Reserves",
        value=forge_reserves_value,
        inline=True,
    )

    if artificer_lines:
        embed.add_field(
            name="▸ Artificers",
            value="\n".join(artificer_lines),
            inline=True,
        )

    # Key (full width - bottom)
    embed.add_field(
        name="▸ Key",
        value="💀🔴🟠🟡⚡🟢⚫ Status | 💤 Dormant",
        inline=False,
    )

    embed.set_footer(text="The machine spirits await the sacred oils.")

    return embed


@_g.bot.tree.command(
    name="forge_chronicle",
    description="Post or update the Forge Chronicle dashboard (atmospheric forge stats).",
)
async def _forge_chronicle_cmd(interaction: discord.Interaction):
    """Post or update the Forge Chronicle dashboard in the current channel."""
    # Defer immediately to avoid 3-second timeout
    await interaction.response.defer(ephemeral=True)

    if not await _is_forge_enabled():
        await interaction.followup.send(
            "The Techmarine subsystem is currently disabled.", ephemeral=True
        )
        return

    # Permission check: uses config command_permissions (Forgemaster only)
    if not _b("check_command_permission")(interaction.user, "forge_chronicle"):
        await interaction.followup.send("Access denied.", ephemeral=True)
        return

    # Channel restriction: arming chamber or techmarine channel
    channel_id = getattr(interaction.channel, "id", None)
    arming_chamber_id = _get_arming_chamber_channel_id()
    allowed_channels = _get_armor_status_allowed_channels()
    if channel_id not in allowed_channels:
        await interaction.followup.send(
            "This command may only be used in the arming chamber or Techmarine channels.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Guild not found.", ephemeral=True)
        return

    # Always post to arming chamber regardless of where command was invoked
    channel = guild.get_channel(arming_chamber_id)
    if not channel:
        await interaction.followup.send("Arming chamber not found.", ephemeral=True)
        return

    # Build the new dashboard embed
    embed = await _build_forge_chronicle_embed(guild)

    # Delete existing chronicle if present
    existing_msg_id = await _get_dashboard_message_id()
    if existing_msg_id:
        try:
            existing_msg = await channel.fetch_message(existing_msg_id)
            await existing_msg.delete()
            _g.logger.debug(f"Deleted old chronicle message {existing_msg_id}")
        except Exception:
            pass

    # Create new message at bottom
    try:
        sent_msg = await channel.send(embed=embed)
        await _set_dashboard_message_id(sent_msg.id)
        # Silent completion - delete the deferred response
        await interaction.delete_original_response()
    except Exception as e:
        await interaction.followup.send(f"Failed to post chronicle: {e}", ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Ambient Messages Task
# ─────────────────────────────────────────────────────────────────────────────

# Ambient message configuration
AMBIENT_MESSAGE_MIN_QUIET_HOURS = 6  # Hours of quiet before ambient can trigger
AMBIENT_MESSAGE_MIN_INTERVAL_HOURS = 12  # Minimum hours between ambient messages
AMBIENT_MESSAGE_CHANCE = 0.25  # 25% chance to post when eligible


async def _maybe_post_ambient_message():
    """Check if the forge has been quiet and maybe post an ambient message."""
    import random

    channel_id = _get_arming_chamber_channel_id()
    if not channel_id:
        return

    guild = None
    channel = None
    for g in _g.bot.guilds:
        channel = g.get_channel(channel_id)
        if channel:
            guild = g
            break

    if not guild or not channel:
        return

    # Check last ambient timestamp
    last_ambient = await _get_last_ambient_ts()
    now = datetime.utcnow()

    if last_ambient:
        hours_since_ambient = (now - last_ambient).total_seconds() / 3600
        if hours_since_ambient < AMBIENT_MESSAGE_MIN_INTERVAL_HOURS:
            return  # Too soon since last ambient

    # Check recent rite activity
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b("_load_forge_chronicle")()

    rite_history = data.get("rite_history", [])

    # Find most recent rite timestamp
    most_recent_rite = None
    for entry in reversed(rite_history):
        try:
            most_recent_rite = datetime.fromisoformat(entry.get("ts", ""))
            break
        except Exception:
            pass

    if most_recent_rite:
        hours_since_rite = (now - most_recent_rite).total_seconds() / 3600
        if hours_since_rite < AMBIENT_MESSAGE_MIN_QUIET_HOURS:
            return  # Forge has been active recently

    # Random chance to post
    if random.random() > AMBIENT_MESSAGE_CHANCE:
        return

    # Post ambient message
    try:
        message = random.choice(FORGE_AMBIENT_MESSAGES)
        await channel.send(message)
        await _set_last_ambient_ts()
        _g.logger.info(f"Posted ambient forge message: {message[:50]}...")
    except Exception as e:
        _g.logger.warning(f"Failed to post ambient message: {e}")


@tasks.loop(minutes=30)
async def _forge_ambient_loop():
    """Check every 30 minutes whether to post an ambient forge message."""
    try:
        # Skip first run to avoid immediate post on startup
        if not getattr(_forge_ambient_loop, "_first_run_done", False):
            setattr(_forge_ambient_loop, "_first_run_done", True)
            return

        await _maybe_post_ambient_message()
    except Exception as e:
        _g.logger.warning(f"Ambient message loop error: {e}")


@tasks.loop(minutes=30)
async def _forge_dashboard_loop():
    """Update the Forge Chronicle dashboard every 30 minutes."""
    try:
        # Skip first run
        if not getattr(_forge_dashboard_loop, "_first_run_done", False):
            setattr(_forge_dashboard_loop, "_first_run_done", True)
            return

        dashboard_msg_id = await _get_dashboard_message_id()
        if not dashboard_msg_id:
            return  # No dashboard to update

        channel_id = _get_arming_chamber_channel_id()
        if not channel_id:
            return

        guild = None
        channel = None
        for g in _g.bot.guilds:
            ch = g.get_channel(channel_id)
            if ch:
                guild = g
                channel = ch
                break

        if not guild or not channel:
            return

        try:
            msg = await channel.fetch_message(dashboard_msg_id)
            embed = await _build_forge_chronicle_embed(guild)
            await msg.edit(embed=embed)
            _g.logger.info("Updated Forge Chronicle dashboard")
        except discord.NotFound:
            # Dashboard message was deleted, clear the stored ID
            async with _g.FORGE_CHRONICLE_LOCK:
                data = _b("_load_forge_chronicle")()
                data["dashboard_message_id"] = None
                _b("_save_forge_chronicle")(data)
        except Exception as e:
            _g.logger.warning(f"Failed to update dashboard: {e}")
    except Exception as e:
        _g.logger.warning(f"Dashboard loop error: {e}")


@_g.bot.tree.command(
    name="preview_armor_alert",
    description="[DEBUG] Preview armor damage alert for a brother.",
)
@app_commands.describe(
    brother="Brother to preview",
    tier="Damage tier to simulate",
    critical_count="Number of AARs at critical (for critical tier countdown)",
)
@app_commands.choices(
    tier=[
        app_commands.Choice(name="Damaged", value="damaged"),
        app_commands.Choice(name="Compromised", value="compromised"),
        app_commands.Choice(name="Critical", value="critical"),
    ]
)
async def _preview_armor_alert(
    interaction: discord.Interaction,
    brother: discord.Member,
    tier: str = "damaged",
    critical_count: int = 1,
):
    """Preview armor damage alert without modifying roles or state."""
    # Permission check: caller must be techmarine or forgemaster
    allowed, _ = _b("_is_techmarine_or_forgemaster")(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild
    config = _get_armor_config()
    fracture_threshold = config.get("fracture_threshold", DEFAULT_ARMOR_FRACTURE_THRESHOLD)

    # Get bearer info using the same pattern as forge_rite/stud announcements
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(brother)
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()

    # Service studs computation
    bearer_studs = _compute_member_service_studs(brother)

    # Machine spirit designation
    machine_spirit = await _get_machine_spirit(int(brother.id))

    # Home chapter (lineage)
    bearer_chapter = _get_bearer_home_chapter(brother)
    chapter_emoji = _get_emoji_by_name(guild, bearer_chapter) if bearer_chapter and guild else None

    # Get rank emoji
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""

    # Build bearer display string (matching forge_rite style)
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_display = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_display = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"

    if bearer_title:
        bearer_display += f"\n*{bearer_title}*"
    # Lineage (home chapter)
    if bearer_chapter and bearer_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        if bearer_chapter == "Black Shield":
            bearer_display += f"\nLineage: {chapter_prefix}REDACTED"
        else:
            bearer_display += f"\nLineage: {chapter_prefix}{bearer_chapter}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_display += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    # Machine spirit
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    if machine_spirit:
        bearer_display += f"\n{machine_spirit_emoji} `{machine_spirit}`"
    else:
        bearer_display += f"\n{machine_spirit_emoji} *UNBOUND*"

    # Determine embed color and title based on tier
    if tier == "critical":
        color = 0xE74C3C  # Red
        title = "᛭⋅ CRITICAL ARMOR FAILURE ⋅᛭"
        description = "*Machine spirit instability detected*"
    else:
        color = 0xE67E22  # Orange
        title = "᛭⋅ ARMOR INTEGRITY ALERT ⋅᛭"
        description = "*Maintenance required*"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    # Affected brother field with proper rank display
    tier_display = tier.title() if tier else "Unknown"
    penalty_risk = _get_tier_risk_display(tier, spirit_fractured=False)
    penalty = _b("_get_damage_penalty")(tier)
    embed.add_field(
        name="▸ Affected Brother",
        value=(
            f"{bearer_display}"
            f"\n**Status:** {tier_display}"
            f"\n**Penalty Risk:** {penalty_risk}"
            f"\n**Fixed Penalty:** {penalty}"
        ),
        inline=False,
    )

    # Warning field for critical
    if tier == "critical":
        remaining = fracture_threshold - critical_count
        embed.add_field(
            name="▸ Warning",
            value=f"⚠️ AAR submissions until spirit fracture: **{remaining}**",
            inline=False,
        )
        embed.add_field(
            name="▸ Immediate Techmarine Response Required",
            value="Administer blessing via `/forge_rite` to preserve machine spirit bond.",
            inline=False,
        )
    else:
        embed.add_field(
            name="▸ Techmarine Response Required",
            value="Administer blessing via `/forge_rite` to restore armor integrity.",
            inline=False,
        )

    # Build preview content
    tech_role_id = _get_techmarine_role_id()
    content = "**[PREVIEW]** "
    if tech_role_id:
        content += f"<@&{tech_role_id}> {brother.mention}"
    else:
        content += f"@Watch Techmarine {brother.mention}"

    await interaction.response.send_message(
        content=content,
        embed=embed,
        ephemeral=True,
    )


@_g.bot.tree.command(
    name="test_armor_alert",
    description="[DEBUG] Force-send a real armor alert to the arming chamber.",
)
@app_commands.describe(
    brother="Brother to test alert for",
    tier="Damage tier to simulate",
    critical_count="Number of AARs at critical (for critical tier countdown)",
)
@app_commands.choices(
    tier=[
        app_commands.Choice(name="Damaged", value="damaged"),
        app_commands.Choice(name="Compromised", value="compromised"),
        app_commands.Choice(name="Critical", value="critical"),
    ]
)
async def _test_armor_alert(
    interaction: discord.Interaction,
    brother: discord.Member,
    tier: str = "damaged",
    critical_count: int = 1,
):
    """Force-send a real armor alert to test the system."""
    # Permission check: admin only
    user_id = str(interaction.user.id)
    admin_ids = [str(a) for a in _g.CONFIG.get("admin_user_ids", [])]
    if user_id not in admin_ids:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await _post_armor_alert(
            member=brother,
            tier=tier,
            critical_aar_count=critical_count,
            guild=interaction.guild,
        )
        await interaction.followup.send(
            f"✅ Alert sent for {brother.display_name} (tier={tier}). Check the arming chamber and logs.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error: {type(e).__name__}: {e}",
            ephemeral=True,
        )


@_g.bot.tree.command(
    name="preview_stud_announcement",
    description="[DEBUG] Preview a service stud announcement for a member.",
)
@app_commands.describe(
    member="Member to preview",
    displayed_studs="Number of studs they're displaying (simulated). Omit to use actual nickname.",
    new_studs="Number of new studs being added to display (simulated). Omit to auto-calculate.",
    earned_studs_override="Override earned studs (for testing owed). Omit to use actual.",
)
async def _preview_stud_announcement(
    interaction: discord.Interaction,
    member: discord.Member,
    displayed_studs: Optional[int] = None,
    new_studs: Optional[int] = None,
    earned_studs_override: Optional[int] = None,
):
    """Debug command to preview service stud announcement output."""
    # Only allow in DEBUG_MODE or for admins
    if not _b("DEBUG_MODE"):
        user_id = str(interaction.user.id)
        admin_ids = [str(a) for a in _g.CONFIG.get("admin_user_ids", [])]
        if user_id not in admin_ids:
            await interaction.response.send_message("This command is only available in debug mode.", ephemeral=True)
            return

    # Defer to avoid interaction timeout during computation
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Must be used in a guild.", ephemeral=True)
        return

    # Get member's home chapter
    member_chapter = "Unknown"
    for role in getattr(member, "roles", []):
        role_name = getattr(role, "name", "")
        if role_name in _b("HOME_CHAPTERS"):
            member_chapter = role_name
            break

    # Calculate actual studs using same logic as activity check
    user_id = str(member.id)
    stats = _b("compute_stats_for_user")(user_id)
    aar_points = int(stats.get("aar_points", 0) or 0)

    # Get weeks since induction (supports override)
    joined_at = _b("_get_effective_induction_date")(member)
    if joined_at:
        if joined_at.tzinfo is not None:
            joined_at = joined_at.replace(tzinfo=None)
        weeks_in_server = max(0, (datetime.utcnow() - joined_at).days // 7)
    else:
        weeks_in_server = 0

    # Compute earned studs (min of time-based and AAR-based)
    studs_time = weeks_in_server // 4
    studs_aar = aar_points // 400
    earned_studs = min(studs_time, studs_aar)

    # Allow override for testing owed studs
    if earned_studs_override is not None:
        earned_studs = earned_studs_override

    # Read displayed studs from nickname
    # New system: ●=4 (Auramite), ⚬=1 (Plasteel), max 16
    dn = str(member.nick or member.display_name or "")
    displayed_aur = dn.count("●")
    displayed_plas = dn.count("⚬")
    actual_displayed = displayed_aur * 4 + displayed_plas

    # Use provided displayed_studs or fall back to actual
    if displayed_studs is None:
        displayed_studs = actual_displayed

    # If new_studs not provided, default to displayed_studs (as if going from 0 to displayed)
    if new_studs is None:
        new_studs = displayed_studs

    owed_studs = max(0, earned_studs - displayed_studs)

    content, embed = _get_service_studs_announcement(
        member=member,
        member_chapter=member_chapter,
        displayed_studs=displayed_studs,
        new_studs=new_studs,
        earned_studs=earned_studs,
        owed_studs=owed_studs,
        guild=guild,
    )

    # Send ephemeral preview with debug info
    debug_info = (
        f"**[PREVIEW DEBUG]**\n"
        f"• Actual in nickname: {actual_displayed}\n"
        f"• Displayed (param): {displayed_studs}\n"
        f"• New (param): {new_studs}\n"
        f"• Earned: {earned_studs}\n"
        f"• Owed: {owed_studs}\n\n"
    )
    await interaction.followup.send(
        f"{debug_info}{content}",
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# No explicit group registration required for top-level commands


@_g.bot.tree.command(
    name="lfg_queue",
    description="Create a Looking For Group queue for operations or omega missions.",
)
@app_commands.choices(
    queue_type=[
        app_commands.Choice(name="Operation (3 players)", value="operation"),
        app_commands.Choice(name="Siege (3 players)", value="siege"),
        app_commands.Choice(name="Omega (5 players, max 2 console)", value="omega"),
    ]
)
@app_commands.describe(
    queue_type="The type of queue to create",
    initiation_trial="Is this an Initiation Trial? (pings additional role)",
    expire_minutes="Minutes until queue expires (default: 30, max: 120)",
    message="Optional message (e.g. 'need slays', 'teaching run')",
)
async def lfg_queue(
    interaction: discord.Interaction,
    queue_type: app_commands.Choice[str],
    initiation_trial: bool = False,
    expire_minutes: Optional[int] = None,
    message: Optional[str] = None,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)

    if not member:
        await interaction.response.send_message("Could not resolve your membership.", ephemeral=True)
        return

    # Check platform role
    platform = _b("_get_player_platform")(member)
    if not platform:
        pc_role = _get_lfg_pc_role_id()
        console_role = _get_lfg_console_role_id()
        await interaction.response.send_message(
            f"❌ You must have either the <@&{pc_role}> or "
            f"<@&{console_role}> role to create a queue.\n"
            "Please assign yourself one of these roles first.",
            ephemeral=True,
        )
        return

    # Get queue type config
    queue_types = _b("_get_lfg_queue_types")()
    type_config = queue_types.get(queue_type.value, {})

    # Validate and set expiry time
    default_expiry = _b("_get_lfg_default_expiry_minutes")()
    max_expiry = _b("_get_lfg_max_expiry_minutes")()

    if expire_minutes is not None:
        if expire_minutes < 1:
            await interaction.response.send_message(
                "❌ Expire time must be at least 1 minute.",
                ephemeral=True,
            )
            return
        if expire_minutes > max_expiry:
            await interaction.response.send_message(
                f"❌ Expire time cannot exceed {max_expiry} minutes.",
                ephemeral=True,
            )
            return
        expiry_minutes = expire_minutes
    else:
        expiry_minutes = default_expiry

    # Calculate expiration time
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expiry_minutes)

    # Build initial queue data (creator auto-joins)
    queue_data = {
        "queue_type": queue_type.value,
        "initiation_trial": initiation_trial,
        "message": message,
        "creator_id": member.id,
        "channel_id": interaction.channel_id,
        "players": [{"user_id": member.id, "platform": platform}],
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }

    # Build embed
    embed = _b("_build_lfg_embed")(queue_data, interaction.guild)

    # Build ping content from queue type config (add initiation trial role if applicable)
    ping_role_id = type_config.get("ping_role_id")
    pings = []
    if ping_role_id:
        pings.append(f"<@&{ping_role_id}>")
    if initiation_trial:
        trial_role_id = _get_lfg_initiation_trial_role_id()
        if trial_role_id:
            pings.append(f"<@&{trial_role_id}>")
    content = " ".join(pings) if pings else None

    # Send message with view
    await interaction.response.send_message(
        content=content,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True) if content else discord.AllowedMentions.none(),
    )
    msg = await interaction.original_response()

    # Store queue data keyed by message ID
    queue_data["message_id"] = msg.id

    async with _g.LFG_QUEUE_LOCK:
        _g.LFG_ACTIVE_QUEUES[msg.id] = queue_data
        all_queues = _b("_load_lfg_queues")()
        all_queues[str(msg.id)] = queue_data
        _b("_save_lfg_queues")(all_queues)

    # Add view to message
    view = LFGQueueView(msg.id)
    await msg.edit(view=view)

    trial_str = " [Initiation Trial]" if initiation_trial else ""
    _g.logger.info(
        f"LFG queue created: {queue_type.value}{trial_str} by {member.display_name} "
        f"(msg={msg.id}, expires={expires_at.isoformat()})"
    )


async def _lfg_queue_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for LFG queue selection."""
    choices = []
    try:
        all_queues = _b("_load_lfg_queues")()
        guild = interaction.guild
        queue_types = _b("_get_lfg_queue_types")()

        for queue_id_str, queue_data in all_queues.items():
            queue_type = queue_data.get("queue_type", "unknown")
            type_config = queue_types.get(queue_type, {})
            display_type = type_config.get("display", queue_type)

            creator_id = queue_data.get("creator_id")
            creator = guild.get_member(creator_id) if guild and creator_id else None
            creator_name = creator.display_name if creator else f"User {creator_id}"

            players = queue_data.get("players", [])
            player_count = len(players)
            max_players = type_config.get("max_players", "?")

            label = f"{display_type} by {creator_name} ({player_count}/{max_players})"

            # Filter by current input
            if current.lower() in label.lower() or current in queue_id_str:
                choices.append(app_commands.Choice(name=label[:100], value=queue_id_str))

            if len(choices) >= 25:
                break
    except Exception:
        pass

    return choices


@_g.bot.tree.command(
    name="lfg_close",
    description="Close/delete your LFG queue.",
)
@app_commands.describe(
    queue="Select the queue to close (only your own queues can be closed)",
)
@app_commands.autocomplete(queue=_lfg_queue_autocomplete)
async def lfg_close(
    interaction: discord.Interaction,
    queue: str,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    try:
        queue_id = int(queue)
    except ValueError:
        await interaction.response.send_message("Invalid queue selection.", ephemeral=True)
        return

    # Get queue data
    async with _g.LFG_QUEUE_LOCK:
        all_queues = _b("_load_lfg_queues")()
        queue_data = all_queues.get(str(queue_id))

        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        # Only creator can close
        if interaction.user.id != queue_data.get("creator_id"):
            await interaction.response.send_message("Only the queue creator can close this queue.", ephemeral=True)
            return

        # Save channel_id before removing from storage
        channel_id = queue_data.get("channel_id")

        # Remove from storage
        if queue_id in _g.LFG_ACTIVE_QUEUES:
            del _g.LFG_ACTIVE_QUEUES[queue_id]
        del all_queues[str(queue_id)]
        _b("_save_lfg_queues")(all_queues)

    # Update the queue message
    try:
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
        else:
            channel = interaction.channel
        if channel:
            msg = await channel.fetch_message(queue_id)
            embed = discord.Embed(
                title="🔒 Queue Closed",
                description="This queue has been closed by the creator.",
                color=0x95A5A6,
            )
            await msg.edit(embed=embed, view=None)
    except discord.NotFound:
        pass
    except Exception as e:
        _g.logger.debug(f"Failed to update closed queue message: {e}")

    await interaction.response.send_message("✅ Queue closed successfully.", ephemeral=True)
    _g.logger.info(f"LFG queue {queue_id} closed by {interaction.user.display_name}")


@_g.bot.tree.command(
    name="lfg_join",
    description="Join an existing LFG queue.",
)
@app_commands.describe(
    queue="Select the queue to join",
)
@app_commands.autocomplete(queue=_lfg_queue_autocomplete)
async def lfg_join(
    interaction: discord.Interaction,
    queue: str,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)

    if not member:
        await interaction.response.send_message("Could not resolve your membership.", ephemeral=True)
        return

    # Check platform role
    platform = _b("_get_player_platform")(member)
    if not platform:
        pc_role = _get_lfg_pc_role_id()
        console_role = _get_lfg_console_role_id()
        await interaction.response.send_message(
            f"❌ You must have either the <@&{pc_role}> or "
            f"<@&{console_role}> role to join a queue.\n"
            "Please assign yourself one of these roles first.",
            ephemeral=True,
        )
        return

    try:
        queue_id = int(queue)
    except ValueError:
        await interaction.response.send_message("Invalid queue selection.", ephemeral=True)
        return

    async with _g.LFG_QUEUE_LOCK:
        all_queues = _b("_load_lfg_queues")()
        queue_data = all_queues.get(str(queue_id))

        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        queue_types = _b("_get_lfg_queue_types")()
        type_config = queue_types.get(queue_data["queue_type"], {})
        players = queue_data["players"]

        # Check if already in queue
        if any(p["user_id"] == member.id for p in players):
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        # Check if queue is full
        if len(players) >= type_config.get("max_players", 3):
            await interaction.response.send_message("This queue is already full.", ephemeral=True)
            return

        # Check console limit for Omega
        max_console = type_config.get("max_console")
        if max_console is not None and platform == "console":
            console_count = sum(1 for p in players if p["platform"] == "console")
            if console_count >= max_console:
                await interaction.response.send_message(
                    f"❌ This Omega queue has reached the console player limit ({max_console}).\n"
                    "Only PC players can join at this time.",
                    ephemeral=True,
                )
                return

        # Add player to queue
        players.append({"user_id": member.id, "platform": platform})
        queue_data["players"] = players
        _g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
        all_queues[str(queue_id)] = queue_data
        _b("_save_lfg_queues")(all_queues)

    # Update the queue message embed
    try:
        channel_id = queue_data.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
        else:
            channel = interaction.channel
        if channel:
            msg = await channel.fetch_message(queue_id)
            embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
            view = LFGQueueView(queue_id)
            await msg.edit(embed=embed, view=view)
    except Exception as e:
        _g.logger.debug(f"Failed to update queue embed: {e}")

    await interaction.response.send_message("✅ You joined the queue!", ephemeral=True)

    # Check if queue is now full and notify
    if len(players) >= type_config.get("max_players", 3):
        creator = interaction.guild.get_member(queue_data["creator_id"])
        if creator:
            player_mentions = []
            for p in players:
                m = interaction.guild.get_member(p["user_id"])
                if m:
                    player_mentions.append(m.mention)
            try:
                await interaction.followup.send(
                    f"🎉 **Queue Full!** {creator.mention}, your {type_config.get('display', 'Mission')} queue is ready!\n"
                    f"Players: {', '.join(player_mentions)}",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                pass


@_g.bot.tree.command(
    name="lfg_leave",
    description="Leave an LFG queue you're in.",
)
@app_commands.describe(
    queue="Select the queue to leave",
)
@app_commands.autocomplete(queue=_lfg_queue_autocomplete)
async def lfg_leave(
    interaction: discord.Interaction,
    queue: str,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    member = interaction.user

    try:
        queue_id = int(queue)
    except ValueError:
        await interaction.response.send_message("Invalid queue selection.", ephemeral=True)
        return

    async with _g.LFG_QUEUE_LOCK:
        all_queues = _b("_load_lfg_queues")()
        queue_data = all_queues.get(str(queue_id))

        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        players = queue_data["players"]

        # Check if in queue
        player_entry = next((p for p in players if p["user_id"] == member.id), None)
        if not player_entry:
            await interaction.response.send_message("You are not in this queue.", ephemeral=True)
            return

        # Remove player
        players.remove(player_entry)
        queue_data["players"] = players
        _g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
        all_queues[str(queue_id)] = queue_data
        _b("_save_lfg_queues")(all_queues)

    # Update the queue message embed
    try:
        channel_id = queue_data.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
        else:
            channel = interaction.channel
        if channel:
            msg = await channel.fetch_message(queue_id)
            embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
            view = LFGQueueView(queue_id)
            await msg.edit(embed=embed, view=view)
    except Exception as e:
        _g.logger.debug(f"Failed to update queue embed: {e}")

    await interaction.response.send_message("✅ You left the queue.", ephemeral=True)


if __name__ == "__main__":
    _b("_main")()


# ---------------------------------------------------------------------------
# Pure helper functions for forge_rite output
# ---------------------------------------------------------------------------


def _should_show_extended_blessing_fields(
    spirit_is_first: bool,
    spirit_is_reconsecrated: bool,
    spirit_is_returning: bool,
    spirit_is_restored: bool,
) -> bool:
    """Determine whether to show extended blessing embed fields.

    Returns True for first bindings and reconsecrated (reborn) spirits.
    Returns False for returning (routine maintenance) and restored spirits.
    """
    if spirit_is_first or spirit_is_reconsecrated:
        return True
    return False


def _get_compact_rite_status(
    blessing_roll_outcome: str,
    is_intensive: bool,
    armor_was_damaged: bool,
) -> tuple:
    """Return (icon, status_text) for the compact rite status line.

    Priority: crit_fail / crit_success beat intensive / damage flags.
    """
    if blessing_roll_outcome == "crit_fail":
        return ("\u26a0\ufe0f", "RESISTED")
    if blessing_roll_outcome == "crit_success":
        return ("\u2728", "BLESSED *(grace)*")
    if is_intensive:
        return ("\u2728", "RESTORED")
    if armor_was_damaged:
        return ("\U0001f7e2", "REPAIRED")
    return ("\U0001f7e2", "MAINTAINED")


def _get_thread_reply_text(
    spirit_is_reconsecrated: bool,
    blessing_roll_outcome: str,
    attester: str,
    machine_spirit_emoji: str,
    spirit_designation: str,
) -> str:
    """Return the short thread-reply text for a completed forge rite."""
    if spirit_is_reconsecrated:
        return (
            f"\u2728 **Spirit Reborn** \u2014 {machine_spirit_emoji} **{spirit_designation}** "
            f"has been reborn through the rites of the Omnissiah. "
            f"Consecrated by {attester}."
        )
    if blessing_roll_outcome == "crit_fail":
        return (
            f"\u26a0\ufe0f **Rite Resisted** \u2014 {machine_spirit_emoji} **{spirit_designation}** "
            f"resisted the blessing. The spirit stirs but remains unquiet."
        )
    return (
        f"\U0001f7e2 **Armor Restored** \u2014 {machine_spirit_emoji} **{spirit_designation}** "
        f"has been tended by {attester}."
    )


# ---------------------------------------------------------------------------
# /forge_override — Forgemaster-only kill switch for the forge / armor subsystem
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
    name="forge_override",
    description="Enable or disable the Techmarine / armor subsystem (Forgemaster only).",
)
@app_commands.describe(enabled="True to enable, False to disable")
async def _forge_override(interaction: discord.Interaction, enabled: bool):
    check = _b("check_command_permission")
    if not (check and check(interaction.user, "forge_override")):
        await interaction.response.send_message(
            "Only the Forgemaster may toggle the Techmarine subsystem.", ephemeral=True
        )
        return
    try:
        async with _g.FORGE_OVERRIDE_LOCK:
            _save_forge_override({
                "enabled": bool(enabled),
                "set_by": str(interaction.user.id),
                "ts": datetime.utcnow().isoformat(),
            })
    except Exception:
        pass
    state_word = "ENABLED" if enabled else "DISABLED"
    await interaction.response.send_message(
        f"Techmarine subsystem **{state_word}**.", ephemeral=True
    )


# ---------------------------------------------------------------------------
# Auto-AAR-ingest: Techmarine cadre pressure contributor.
#
# Demand = active brothers needing armor attention:
#     - any non-nominal damage tier (damaged/compromised/critical), OR
#     - spirit_fractured, OR
#     - nominal but points_since_blessing >= 5 (entering risk zone)
#
# Supply = sum of available blessing-pool charges across all members of the
# Watch Techmarine role.
#
# See opscribe/pressure_registry.py for the aggregation contract.
# ---------------------------------------------------------------------------

async def evaluate_techmarine_pressure(guild: discord.Guild):
    """Pressure evaluator for the Techmarine cadre. See pressure_registry.

    Uses charge-weighted demand: each brother contributes the number of
    intensive blessing charges required to restore him to nominal, rather
    than a flat head-count of 1.  At-risk nominal brothers (predictive
    warning triggered) contribute 1 preventative charge each — a lighter
    signal that matches the watchlist display.  This keeps the evaluator
    consistent with the Forge Chronicle's ``brothers_needing_attention``
    definition.
    """
    from .pressure_registry import CadrePressure

    armor_data = _load_armor_integrity()
    is_active_fn = _b("_is_active_participant")
    try:
        prob_threshold = float(
            _get_armor_config().get("at_risk_probability_threshold", 0.20) or 0.20
        )
    except Exception:
        prob_threshold = 0.20

    demand: float = 0.0
    for member in guild.members:
        if is_active_fn and not is_active_fn(member):
            continue
        state = armor_data.get(str(member.id), {}) or {}
        spirit_fractured = bool(state.get("spirit_fractured", False))
        damage_tier = _get_member_damage_tier(member)
        cost = _get_intensive_charge_cost(damage_tier, spirit_fractured)
        if cost > 0:
            demand += cost
        elif damage_tier is None and not spirit_fractured:
            # Nominal: check for predictive-warning (at-risk) state.
            pts = int(state.get("points_since_blessing", 0) or 0)
            if _get_damage_probability(pts) >= prob_threshold:
                demand += 1  # 1 preventative charge per at-risk nominal

    supply = 0
    techmarine_role = discord.utils.get(guild.roles, name=TECHMARINE_ROLE_NAME)
    notify_role_id = techmarine_role.id if techmarine_role else None
    if techmarine_role:
        for member in techmarine_role.members:
            if member.bot:
                continue
            try:
                supply += await _get_techmarine_available_charges(int(member.id))
            except Exception:
                pass

    # Cadre-specific tier-1 notification channel (config override → default).
    try:
        cfg = _b("CONFIG") or {}
        notify_channel_id = (
            int(cfg.get("auto_ingest", {}).get("techmarine_blocker_channel_id", 0) or 0)
            or 1485797067577102377
        )
    except Exception:
        notify_channel_id = 1485797067577102377

    result = CadrePressure(
        cadre_id="techmarine",
        display_name="Techmarines",
        demand=demand,
        supply=supply,
        notify_role_id=notify_role_id,
        notify_channel_id=notify_channel_id,
    )
    result.detail = f"{result.demand_display} charge(s) of forge work outstanding; {supply} charge(s) available"
    return result


def _register_pressure_contributors() -> None:
    """Register this module's cadre evaluator with the pressure registry.

    Called once from bot.py at startup. Idempotent.
    """
    from .pressure_registry import register_cadre
    register_cadre(evaluate_techmarine_pressure)


# ---------------------------------------------------------------------------
# __all__: export all names needed by tests and bot.py re-imports.
# Must include underscore-prefixed names (Python's `import *` skips them
# by default; __all__ overrides that behaviour).
# ---------------------------------------------------------------------------

__all__ = [
    # ── Scan / detection ────────────────────────────────────────────────────
    "_roll_detection_alert",
    "_roll_scan_result",
    "_load_scan_state",
    "_save_scan_state",
    "_increment_aar_generation",
    "_get_aar_generation",
    "_get_or_roll_scan_result",
    "_purchase_intensive_scan",
    "_has_intensive_scan",
    # ── Armor state / damage ─────────────────────────────────────────────────
    "_get_armor_state",
    "_set_armor_state",
    "_save_armor_batch",
    "_get_armor_state_from_batch",
    "_set_armor_state_in_batch",
    "_get_armor_config",
    "_get_armor_probability_tiers",
    "_get_probability_tier_for_points",
    "_get_damage_probability",
    "_roll_damage_tier",
    "_run_armor_integrity_check",
    "_apply_damage_tier",
    "_clear_armor_damage",
    "_drop_armor_tier",
    "_get_member_damage_tier",
    "_get_damage_penalty",
    "_roll_armor_penalty",
    "_get_tier_risk_display",
    "_check_armor_grace_period",
    "_get_armor_status_for_blessing",
    "_get_armor_damage_role_ids",
    "_get_arming_chamber_channel_id",
    "_get_techmarine_role_id",
    "_get_armor_status_allowed_channels",
    "_calculate_armor_risk_score",
    "_show_armor_leaderboard",
    "_post_armor_alert",
    "_process_armor_integrity_for_aar",
    # ── Forge override (kill switch) ─────────────────────────────────────────
    "_load_forge_override",
    "_save_forge_override",
    "_is_forge_enabled",
    # ── Rites / machine spirits ──────────────────────────────────────────────
    "_load_rites",
    "_save_rites",
    "_get_user_rite",
    "_set_user_rite",
    "_load_machine_spirits",
    "_save_machine_spirits",
    "_get_machine_spirit",
    "_set_machine_spirit",
    "_delete_machine_spirit",
    # ── Blessing pool ────────────────────────────────────────────────────────
    "_check_recipient_cooldown",
    "_check_techmarine_can_bless",
    "_check_spirit_fracture",
    "_consume_blessing",
    "_get_intensive_charge_cost",
    "_get_techmarine_available_charges",
    "_consume_multiple_blessings",
    "_get_techmarine_pool_state",
    "_set_techmarine_pool_state",
    "_load_blessing_pool",
    "_save_blessing_pool",
    "_get_blessing_pool_display",
    "_filter_active_blessing_timestamps",
    "_calculate_regenerated_blessings",
    "_grant_blessing_charge",
    "_roll_blessing_outcome",
    "_apply_blessing_crit_fail",
    "_apply_blessing_normal",
    "_apply_blessing_crit_success",
    "_apply_blessing_intensive_normal",
    "_handle_intensive_scan_requisition",
    # ── Forge pool ───────────────────────────────────────────────────────────
    "_load_forge_pool",
    "_save_forge_pool",
    "_increment_forge_pool_balance",
    "_deduct_forge_pool_balance",
    "_get_forge_pool_available",
    "_consume_forge_requisition",
    "_get_techmarine_daily_requisitions",
    "_get_forge_pool_status",
    # ── Forge chronicle ──────────────────────────────────────────────────────
    "_load_forge_chronicle",
    "_save_forge_chronicle",
    "_build_forge_chronicle_embed",
    "_repost_chronicle_at_bottom",
    "_maybe_post_ambient_message",
    "_get_dashboard_message_id",
    "_set_dashboard_message_id",
    "_get_last_ambient_ts",
    "_set_last_ambient_ts",
    "_record_spirit_released",
    "_record_spirit_fractured",
    "_abbreviate_spirit",
    "_format_time_ago",
    # ── Pending alerts ───────────────────────────────────────────────────────
    "_store_pending_alert",
    "_get_pending_alert",
    "_clear_pending_alert",
    # ── Rite events / chronicle recording ───────────────────────────────────
    "_record_rite_in_chronicle",
    "_classify_forge_rite_event",
    "_should_show_extended_blessing_fields",
    "_get_compact_rite_status",
    "_get_thread_reply_text",
    # ── Forge rite helpers ───────────────────────────────────────────────────
    "_get_techmarine_acknowledgment_blended",
    "_blend_forgemaster_self_attestation",
    "_get_emoji_by_name",
    "_get_chapter_emoji",
    "_get_rank_emoji",
    "_get_rank_category_for_blend",
    "_blend_stud_flavor_by_rank",
    "_get_stud_marking_recipients",
    "_get_service_studs_announcement",
    "_get_oathsworn_announcement",
    "_get_award_announcement_channel",
    "_get_watch_veteran_announcement",
    "_get_ardent_raider_announcement",
    "_get_apothecarion_medal_announcement",
    "_get_crimson_laurels_announcement",
    "_get_sok_g_pipehitter_announcement",
    "_get_distinguished_pipehitter_announcement",
    "_get_black_laurels_announcement",
    "_get_crux_terminatus_announcement",
    "_get_kadaku_campaign_announcement",
    "_get_black_reef_campaign_announcement",
    "_get_distinguished_black_reef_announcement",
    "_get_order_omega_announcement",
    "_get_member_rank_title",
    "_compute_member_service_studs",
    "_get_bearer_rank_and_title",
    "_get_bearer_home_chapter",
    "_find_company_or_chapter",
    "_format_cooldown_time",
    "_extract_killteam_name",
    "_resolve_killteam_for_member",
    "_resolve_killteams_for_member",
    # ── LFG ─────────────────────────────────────────────────────────────────
    "_get_lfg_config",
    "_get_lfg_pc_role_id",
    "_get_lfg_console_role_id",
    "_get_lfg_default_expiry_minutes",
    "_get_lfg_max_expiry_minutes",
    "_get_lfg_queue_types",
    "_get_lfg_initiation_trial_role_id",
    "_load_lfg_queues",
    "_save_lfg_queues",
    "_get_player_platform",
    "_build_lfg_embed",
    "_restore_lfg_queue_views",
    "_expire_old_lfg_queues",
    "_lfg_queue_autocomplete",
    "LFGQueueView",
    "LogToForgeView",
    # ── Loops (tasks) ────────────────────────────────────────────────────────
    "_forge_ambient_loop",
    "_forge_dashboard_loop",
    "_lfg_queue_expiration_loop",
    # ── Public command functions ─────────────────────────────────────────────
    "lfg_queue",
    "lfg_close",
    "lfg_join",
    "lfg_leave",
    "_set_rite",
    "_attest",
    "_armor_status",
    "_requisition_supplies",
    "_forge_chronicle_cmd",
    "_preview_armor_alert",
    "_test_armor_alert",
    "_preview_stud_announcement",
    # ── Auto-ingest pressure contributor ────────────────────────────────────
    "evaluate_techmarine_pressure",
    "_register_pressure_contributors",
]
