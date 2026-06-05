"""Campaign system operations: enlistment, prestige, strat mandate, scenario
generation, beat management, rewards, and slash commands."""

import os
import json
import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import sys as _sys

import discord
from discord import app_commands

from .constants import (
    CAMPAIGN_STATE_PATH, AAR_RECORDS_PATH, CAMPAIGN_ANNOUNCEMENT_CHANNEL_ID,
    WATCH_MASTER_ROLE_ID, WATCH_BROTHER_ROLE_ID,
    HIGH_COMMAND_ROLE_ID, WATCH_SERGEANT_ROLE_ID,
    PHASE_DISPLAY,
)
from . import _bot_globals as _g

# ---------------------------------------------------------------------------
# Reference data (loaded once at module import)
# ---------------------------------------------------------------------------

_REF_DIR = "reference"


def _load_ref(filename: str) -> dict:
    path = os.path.join(_REF_DIR, filename)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


_STRATAGEMS: List[dict] = []
_DOCTRINE_STRAT_MAP: dict = {}
_SCENARIO_GEN: dict = {}
_CASCADE_OPTIONS: dict = {}
_REWARDS: dict = {}
_MILESTONES: dict = {}
_STRAT_MANDATE: dict = {}


def _ensure_refs_loaded():
    global _STRATAGEMS, _DOCTRINE_STRAT_MAP, _SCENARIO_GEN
    global _CASCADE_OPTIONS, _REWARDS, _MILESTONES, _STRAT_MANDATE
    if not _STRATAGEMS:
        data = _load_ref("stratagems.json")
        _STRATAGEMS = data.get("stratagems", [])
    if not _DOCTRINE_STRAT_MAP:
        _DOCTRINE_STRAT_MAP = _load_ref("doctrine_strat_map.json")
    if not _SCENARIO_GEN:
        _SCENARIO_GEN = _load_ref("scenario_generation.json")
    if not _CASCADE_OPTIONS:
        _CASCADE_OPTIONS = _load_ref("cascade_options.json")
    if not _REWARDS:
        _REWARDS = _load_ref("campaign_rewards.json")
    if not _MILESTONES:
        _MILESTONES = _load_ref("personal_milestones.json")
    if not _STRAT_MANDATE:
        _STRAT_MANDATE = _load_ref("strat_mandate.json")


# ---------------------------------------------------------------------------
# State load / save (atomic write with .bak)
# ---------------------------------------------------------------------------


def _blank_campaign_state() -> dict:
    """Return a fully-structured inactive campaign state (never persisted here)."""
    return {
        "_schema_version": 1,
        "campaign": {
            "id": None,
            "name": None,
            "beat": None,
            "beat_name": None,
            "phase": "inactive",
            "started_at": None,
            "ended_at": None,
            "outcome": None,
            "beat_duration_days": 7,
            "current_node": None,
            "visited_nodes": [],
            "beat_history": [],
            "beat_schedule": [],
            "total_beats": 3,
        },
        "enlistment": {},
        "companies": {},
        "kill_teams": {},
        "lore_priority": {
            "kill_team": {"sgt_user_id": None, "display_name": None, "prestige": None, "held_since": None},
            "company": {"company_id": None, "display_name": None, "prestige": None, "held_since": None},
        },
        "ops_window": {},
        "strat_pool": {"locked": False, "pool": [], "theatre_mandate": [], "company_mandates": {}, "kt_mandates": {}},
        "campaign_log": {},
        "credited_aars": {},
        "beat_scenarios": {},
        "pressure": {},
        "cascade": {},
        "beat_record": {},
    }


def _load_campaign_state() -> dict:
    """Load campaign state from disk, seeding a blank inactive state if absent."""
    try:
        if os.path.exists(CAMPAIGN_STATE_PATH):
            with open(CAMPAIGN_STATE_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    # File missing or unreadable — seed a blank state (don't persist it here;
    # the first write will create the file).
    return _blank_campaign_state()


def _save_campaign_state(state: dict):
    try:
        state_dir = os.path.dirname(CAMPAIGN_STATE_PATH)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        tmp_path = CAMPAIGN_STATE_PATH + ".tmp"
        bak_path = CAMPAIGN_STATE_PATH + ".bak"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(CAMPAIGN_STATE_PATH):
            try:
                os.replace(CAMPAIGN_STATE_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, CAMPAIGN_STATE_PATH)
    except Exception:
        logger = getattr(_g, "logger", None)
        if logger is not None:
            logger.exception("Failed to save campaign state to %s", CAMPAIGN_STATE_PATH)
        raise


# ---------------------------------------------------------------------------
# Bot module helper (test-mock compatible)
# ---------------------------------------------------------------------------


def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

# Base prestige values per difficulty class (halved from original; mandate bonuses layer on top)
_PRESTIGE_ABSOLUTE = 0.5
_PRESTIGE_HARD_SIEGE_PER_5_WAVES = 0.25
_PRESTIGE_OMEGA = 1.0
_PRESTIGE_HARD_STRAT = 1.5
_PRESTIGE_OMEGA_STRAT = 2.0

# Mandate adherence prestige bonuses (flat, before formation multiplier)
_PRESTIGE_BONUS_OPS_MANDATE = 0.25       # mission matches active ops mandate
_PRESTIGE_BONUS_STRAT_MANDATE = 0.25     # per mandated strat run (theatre/company/kt)
_PRESTIGE_BONUS_TERMINUS_MANDATE = 0.25  # terminus kill when Huntmaster mandate active

_PRESTIGE_WINDOW_DAYS = 28

# All difficulty_class values that qualify for campaign prestige
_QUALIFYING_DIFFICULTY_CLASSES = {"absolute_ops", "hard_siege", "omega_ops", "hard_stratagem", "omega_stratagem"}

# Formation base rates (before squad depth bonus)
_RATE_KT = 1.0
_RATE_COMPANY = 0.75
_RATE_HC = 0.60

# Squad depth bonus steps per enrolled co-runner
_KT_BROTHER_STEP = 0.15       # KT tier: same-KT enrolled co-runner
_CO_BROTHER_STEP = 0.07       # KT tier: same-company different-KT enrolled co-runner
_OWN_KT_STEP_COMPANY = 0.10  # Company tier: own-company KT member in run
_KT_STEP_HC = 0.10            # HC tier: per enrolled KT member in run
_CO_CMD_STEP_HC = 0.08        # HC tier: per enrolled Company Command member in run

# Iron Compact thresholds
_KT_IRON_COMPACT_RIBBON_OPS = 10   # unique ops per beat with Command/HC co-runner → beat ribbon
_CO_IRON_COMPACT_RIBBON_OPS = 10
# Honour requires qualifying in every beat of the campaign (dynamic, read from state.total_beats)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fmt_ts(ts: Optional[str]) -> str:
    """Format an ISO timestamp as a human-readable UTC string.

    Returns e.g. 'Wednesday, 4 Jun at 18:30 UTC' or 'in 2 days, 4 hours'.
    Falls back to the raw string if unparseable.
    """
    if not ts:
        return "Unknown"
    dt = _parse_iso(ts)
    if dt is None:
        return ts
    now = _utcnow()
    delta = dt - now
    total_secs = delta.total_seconds()
    day_name = dt.strftime("%A")
    date_str = dt.strftime("%-d %b")
    time_str = dt.strftime("%H:%M UTC")
    abs_label = f"{day_name}, {date_str} at {time_str}"
    if total_secs < 0:
        return f"{abs_label} (passed)"
    elif total_secs < 3600:
        mins = int(total_secs // 60)
        rel = f"in {mins}m"
    elif total_secs < 86400:
        hours = int(total_secs // 3600)
        mins = int((total_secs % 3600) // 60)
        rel = f"in {hours}h {mins}m" if mins else f"in {hours}h"
    elif total_secs < 172800:
        hours = int(total_secs // 3600)
        rel = f"in ~{hours}h"
    else:
        days = int(total_secs // 86400)
        rel = f"in {days} days"
    return f"{abs_label} ({rel})"


def _fmt_ts_abs(ts: Optional[str]) -> str:
    """Format an ISO timestamp as absolute UTC string only — no relative suffix."""
    if not ts:
        return "Unknown"
    dt = _parse_iso(ts)
    if dt is None:
        return ts
    return f"{dt.strftime('%A')}, {dt.strftime('%-d %b')} at {dt.strftime('%H:%M UTC')}"


def _entry_id() -> str:
    """Generate a unique log entry ID from current timestamp + randomness."""
    h = hashlib.sha1(f"{_utcnow().isoformat()}{random.random()}".encode()).hexdigest()
    return h[:12]


def _compute_base_prestige(
    difficulty_class: str,
    strats_active: List[str],
    waves: int,
) -> float:
    """Return the base prestige value for a completed op."""
    if difficulty_class == "absolute_ops":
        return float(_PRESTIGE_ABSOLUTE)
    if difficulty_class == "hard_siege":
        return max(0, int(waves / 5)) * _PRESTIGE_HARD_SIEGE_PER_5_WAVES
    if difficulty_class == "omega_ops":
        return float(_PRESTIGE_OMEGA_STRAT if strats_active else _PRESTIGE_OMEGA)
    if difficulty_class == "hard_stratagem":
        return float(_PRESTIGE_HARD_STRAT)
    if difficulty_class == "omega_stratagem":
        return float(_PRESTIGE_OMEGA_STRAT)
    return 0.0


def _compute_mandate_bonus(
    state: dict,
    mission_name: str,
    strats_active: List[str],
    terminus_killed: bool,
) -> Tuple[float, List[str]]:
    """Return (mandate_bonus, list_of_bonus_reason_strings).

    Bonuses are flat values added to base prestige before formation multiplier.
    - Ops mandate: +0.25 if mission matches an active ops mandate eligible mission.
    - Strat mandate: +0.25 per mandated strat run (across all mandate tiers).
    - Terminus mandate: +0.25 if terminus killed while Huntmaster mandate is active.
    """
    strat_pool = state.get("strat_pool", {})
    if not strat_pool.get("locked"):
        return 0.0, []

    bonus = 0.0
    reasons: List[str] = []

    # Ops mandate bonus
    ops_mandate = strat_pool.get("ops_mandate", {})
    eligible_missions = ops_mandate.get("eligible_missions", [])
    if eligible_missions and mission_name in eligible_missions:
        bonus += _PRESTIGE_BONUS_OPS_MANDATE
        reasons.append("ops mandate ✓")

    # Strat mandate bonus: collect all mandated strats across all tiers
    all_mandated: set = set()
    theatre_strats = strat_pool.get("theatre_mandate") or []
    if isinstance(theatre_strats, str):
        theatre_strats = [theatre_strats]
    all_mandated.update(theatre_strats)
    for co_strats in (strat_pool.get("company_mandates") or {}).values():
        if isinstance(co_strats, list):
            all_mandated.update(co_strats)
        elif isinstance(co_strats, str):
            all_mandated.add(co_strats)
    for kt_strats in (strat_pool.get("kt_mandates") or {}).values():
        if isinstance(kt_strats, list):
            all_mandated.update(kt_strats)
        elif isinstance(kt_strats, str):
            all_mandated.add(kt_strats)

    matched_mandated = all_mandated & set(strats_active)
    if matched_mandated:
        strat_bonus = len(matched_mandated) * _PRESTIGE_BONUS_STRAT_MANDATE
        bonus += strat_bonus
        count = len(matched_mandated)
        reasons.append(f"strat mandate ✓ ×{count}" if count > 1 else "strat mandate ✓")

    # Terminus mandate bonus
    terminus_directive = strat_pool.get("terminus_directive", {})
    huntmaster_active = terminus_directive.get("huntmaster_active", False)
    if terminus_killed and huntmaster_active:
        bonus += _PRESTIGE_BONUS_TERMINUS_MANDATE
        reasons.append("terminus mandate ✓")

    return bonus, reasons


def _resolve_aar_by_link(aar_link: str) -> Optional[dict]:
    """Look up an AAR record by its Discord message URL.

    Parses the message ID from the last URL segment and matches against
    aar_records keyed by aar_id.
    """
    import re as _re
    m = _re.search(r"/(\d+)/?$", aar_link.strip())
    if not m:
        return None
    msg_id = m.group(1)
    try:
        with open(AAR_RECORDS_PATH, "r") as f:
            records = json.load(f)
    except Exception:
        return None
    # Records are keyed by aar_id (string message ID)
    rec = records.get(msg_id)
    if rec:
        return rec
    # Fallback: scan values for matching aar_id field
    for rec in records.values():
        if str(rec.get("aar_id", "")) == msg_id:
            return rec
    return None


def _classify_co_runners(
    user_id: str,
    enlistment_record: dict,
    brother_ids: List[str],
    enlistment: dict,
) -> dict:
    """Classify enrolled co-runners by their formation relationship to user_id.

    Returns a dict with:
      kt_brothers        — same KT (KT tier only)
      co_brothers        — same company, different KT (KT tier only)
      kt_formations      — {sgt_id: count} of enrolled KT members (Company/HC tier)
      co_formations      — {co_id: count} of enrolled Company cmd members (HC tier)
      officer_tiers      — set of tiers (Company/HC) present among enrolled co-runners
    """
    tier = enlistment_record.get("tier")
    kt_sgt_id = enlistment_record.get("kt_sgt_id")
    company_id = enlistment_record.get("company_id")

    kt_brothers = 0
    co_brothers = 0
    kt_formations: Dict[str, int] = {}
    co_formations: Dict[str, int] = {}
    officer_tiers: set = set()

    for bid in brother_ids:
        if bid == user_id:
            continue
        rec = enlistment.get(bid)
        if not rec or not rec.get("active"):
            continue
        b_tier = rec.get("tier", "")
        b_kt = rec.get("kt_sgt_id")
        b_co = rec.get("company_id")

        # Track officer tier presence for Iron Compact
        if b_tier in ("Company", "HC"):
            officer_tiers.add(b_tier)

        if tier == "KT":
            if b_kt == kt_sgt_id:
                kt_brothers += 1
            elif b_co == company_id:
                co_brothers += 1
        elif tier == "Company":
            if b_tier == "KT" and b_kt:
                kt_formations[b_kt] = kt_formations.get(b_kt, 0) + 1
        elif tier == "HC":
            if b_tier == "KT" and b_kt:
                kt_formations[b_kt] = kt_formations.get(b_kt, 0) + 1
            elif b_tier == "Company" and b_co:
                co_formations[b_co] = co_formations.get(b_co, 0) + 1

    return {
        "kt_brothers": kt_brothers,
        "co_brothers": co_brothers,
        "kt_formations": kt_formations,
        "co_formations": co_formations,
        "officer_tiers": officer_tiers,
    }


def generate_campaign_name(seed: Optional[int] = None) -> str:
    """Generate a lore-flavoured campaign codename: 'OPERATION ADJECTIVE NOUN'.

    Draws from the _default codename pools so the name is always valid even
    before a campaign node/doctrine is established.
    """
    _ensure_refs_loaded()
    pools = _SCENARIO_GEN.get("codename_pools", {})
    adj_pool = pools.get("adjectives", {}).get("_default", ["DARK", "GREY", "LONG", "DEEP", "COLD"])
    noun_pool = pools.get("nouns", {}).get("_default", ["REACH", "WATCH", "VIGIL", "GATE", "FRONT"])
    rng = random.Random(seed)
    adj = rng.choice(adj_pool)
    noun = rng.choice(noun_pool)
    return f"OPERATION {adj} {noun}"


def generate_beat_name(beat_num: int, doctrine_tags: Optional[List[str]] = None, seed: Optional[int] = None) -> str:
    """Generate a lore-flavoured cycle codename: 'CYCLE N: ADJECTIVE NOUN'.

    If doctrine_tags are supplied (e.g. ['aggressive', 'terminus']) the pools
    for the first matching tag are used; otherwise falls back to _default.
    """
    _ensure_refs_loaded()
    pools = _SCENARIO_GEN.get("codename_pools", {})
    adj_pools = pools.get("adjectives", {})
    noun_pools = pools.get("nouns", {})

    adj_list = adj_pools.get("_default", ["DARK", "GREY", "LONG", "DEEP", "COLD"])
    noun_list = noun_pools.get("_default", ["REACH", "WATCH", "VIGIL", "GATE", "FRONT"])
    for tag in (doctrine_tags or []):
        if tag in adj_pools:
            adj_list = adj_pools[tag]
            break
    for tag in (doctrine_tags or []):
        if tag in noun_pools:
            noun_list = noun_pools[tag]
            break

    rng = random.Random(seed)
    adj = rng.choice(adj_list)
    noun = rng.choice(noun_list)
    return f"CYCLE {beat_num}: {adj} {noun}"


# ---------------------------------------------------------------------------
# Enlistment
# ---------------------------------------------------------------------------


def _get_campaign_state_checked() -> Tuple[Optional[dict], Optional[str]]:
    """Return (state, None) if campaign is active, else (None, error_msg)."""
    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase == "inactive":
        return None, "No campaign is currently active."
    if phase == "paused":
        camp_name = state.get("campaign", {}).get("name") or "The campaign"
        return None, f"{camp_name} is currently **paused**. Wait for the Forgemaster to resume it."
    if phase in ("evaluating", "complete"):
        return None, f"Campaign is in **{phase}** phase — enlistment is closed."
    return state, None


def enlist_member(
    user_id: str,
    discord_name: str,
    chapter: str,
    company_id: str,
    tier: str,
    role: str,
    kt_sgt_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """Enlist a member into the current campaign.

    Enlistment is allowed whether or not a campaign is currently active —
    it records intent to participate so the member is ready when a campaign starts.
    Returns (success, message).
    """
    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase in ("evaluating", "complete"):
        return False, f"Campaign is in **{phase}** phase — enlistment is closed."

    enlistment = state.setdefault("enlistment", {})
    existing = enlistment.get(user_id)
    if existing and existing.get("active"):
        return False, "You are already enlisted in the current campaign."

    if tier == "KT" and not kt_sgt_id:
        return False, "KT-tier members must provide their Sergeant's user ID."

    if tier == "Company" and not company_id:
        return False, "Company-tier members must provide their company."

    now = _iso_now()
    enlistment[user_id] = {
        "discord_name": discord_name,
        "role": role,
        "tier": tier,
        "chapter": chapter,
        "enlisted_at": now,
        "active": True,
        "last_aar_timestamp": None,
        "auto_de_enlist_warning_sent": False,
        "company_id": company_id if tier in ("Company", "HC", "KT") else None,
        "kt_sgt_id": kt_sgt_id if tier == "KT" else None,
        "operational_attachment": (
            None
            if tier == "KT"
            else {
                "attached_kt_sgt_id": None,
                "attached_company_id": None,
                "derived_at": None,
                "shared_aars_with_kt": 0,
            }
        ),
        "milestone_progress": {},
    }

    # Ensure kill_team entry exists for this member's KT if they are a KT-tier
    if tier == "KT" and kt_sgt_id:
        kill_teams = state.setdefault("kill_teams", {})
        if kt_sgt_id not in kill_teams:
            kill_teams[kt_sgt_id] = {
                "sgt_discord_name": "",
                "display_name": f"Sgt {discord_name}'s Kill Team" if role == "Watch Sergeant" else "Kill Team",
                "company_id": company_id,
                "title": None,
                "title_granted_by": None,
                "title_granted_at": None,
                "honour": [],
                "ribbon": None,
                "lore_priority": False,
                "prestige_window_total": 0,
                "last_prestige_check": None,
                "prestige_log": [],
            }
            if role == "Watch Sergeant":
                kill_teams[kt_sgt_id]["sgt_discord_name"] = discord_name

    # Ensure company entry exists for Company-tier (and HC with a company) members
    if company_id and tier in ("Company", "HC"):
        companies = state.setdefault("companies", {})
        if company_id not in companies:
            companies[company_id] = {
                "display_name": company_id.capitalize(),
                "title": None,
                "title_granted_by": None,
                "title_granted_at": None,
                "honour": [],
                "ribbon": None,
                "lore_priority": False,
                "prestige_window_total": 0,
                "last_prestige_check": None,
                "prestige_log": [],
            }

    _save_campaign_state(state)
    return True, (
        f"Enlisted successfully.\n"
        f"**Chapter:** {chapter} | **Tier:** {tier} | **Company:** {company_id.capitalize() if company_id else 'N/A'}"
    )


def de_enlist_member(user_id: str) -> Tuple[bool, str]:
    """Voluntarily de-enlist a member. Milestone progress is preserved."""
    state = _load_campaign_state()
    enlistment = state.get("enlistment", {})
    record = enlistment.get(user_id)
    if not record or not record.get("active"):
        return False, "You are not currently enlisted."
    record["active"] = False
    _save_campaign_state(state)
    return True, "De-enlisted. Your milestone progress is saved and can be resumed if you re-enlist before campaign end."


# ---------------------------------------------------------------------------
# Campaign log
# ---------------------------------------------------------------------------


def log_campaign_entry(
    user_id: str,
    aar_link: str,
    terminus_slain: Optional[Dict[str, int]] = None,
    strats_active: Optional[List[str]] = None,
) -> Tuple[bool, str, Optional[dict]]:
    """Submit a campaign log entry linked to a specific AAR by Discord message URL.

    terminus_slain: dict of {terminus_type: count} for terminus kills this run.
    Also auto-credits all other enrolled members found in the AAR's brother_ids
    that have not yet been credited for this op.
    Returns (success, message, entry_dict_or_None).
    """
    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase not in ("ops",):
        return False, f"Campaign log submissions are only accepted during the **ops** phase (current: {phase}).", None

    enlistment = state.get("enlistment", {})
    record = enlistment.get(user_id)
    if not record or not record.get("active"):
        return False, "You must be enlisted to submit a campaign log.", None

    ops_window = state.get("ops_window", {})
    closes_at = _parse_iso(ops_window.get("closes_at"))
    if closes_at and _utcnow() >= closes_at:
        return False, "The ops window has closed. Campaign log submissions are blocked during beat resolution.", None

    # Resolve AAR by link
    aar_record = _resolve_aar_by_link(aar_link)
    if not aar_record:
        return False, "Could not find an AAR matching that link. Check the URL and try again.", None

    # Validate submitter participated
    brother_ids: List[str] = [str(b) for b in aar_record.get("brother_ids", [])]
    if user_id not in brother_ids:
        return False, "You are not listed as a participant in that AAR.", None

    # Validate difficulty class
    difficulty_class = aar_record.get("difficulty_class", "")
    if difficulty_class not in _QUALIFYING_DIFFICULTY_CLASSES:
        return False, (
            f"That AAR's difficulty class (`{difficulty_class}`) does not qualify for campaign prestige. "
            f"Qualifying types: {', '.join(sorted(_QUALIFYING_DIFFICULTY_CLASSES))}."
        ), None

    aar_id = str(aar_record.get("aar_id", ""))
    credited_aars = state.setdefault("credited_aars", {})

    # Check whether this user has already been credited for this op
    already_credited: List[str] = credited_aars.get(aar_id, [])
    if user_id in already_credited:
        return False, "You have already been credited for this op.", None

    # Validate strats_active against locked pool
    strat_pool = state.get("strat_pool", {})
    if strats_active and strat_pool.get("locked"):
        valid_pool: List[str] = strat_pool.get("pool", [])
        invalid = [s for s in strats_active if s not in valid_pool]
        if invalid:
            pool_list = ", ".join(f"`{s}`" for s in sorted(valid_pool))
            return False, (
                f"The following strats are not in the locked pool: {', '.join(f'`{s}`' for s in invalid)}.\n"
                f"Valid pool: {pool_list}"
            ), None

    terminus_killed = bool(terminus_slain)
    beat = state.get("campaign", {}).get("beat")
    waves = aar_record.get("waves", 0) or 0
    mission_name = _parse_mission_name(aar_record.get("mission", ""))
    base_prestige = _compute_base_prestige(difficulty_class, strats_active or [], waves)
    mandate_bonus, bonus_reasons = _compute_mandate_bonus(
        state, mission_name, strats_active or [], terminus_killed
    )
    effective_base = base_prestige + mandate_bonus
    campaign_log = state.setdefault("campaign_log", {})

    # Determine the full list of co-runners (enrolled members in this AAR beyond the submitter)
    co_runner_ids = [
        bid for bid in brother_ids
        if bid != user_id and enlistment.get(bid, {}).get("active")
    ]

    # Classify co-runners for the submitter (for Iron Compact tracking on the entry)
    co_run_info = _classify_co_runners(user_id, record, brother_ids, enlistment)
    officer_tiers = co_run_info["officer_tiers"]

    def _make_entry(for_user_id: str) -> dict:
        return {
            "entry_id": _entry_id(),
            "submitted_by": for_user_id,
            "submitted_at": _iso_now(),
            "aar_id": aar_id,
            "aar_link": aar_link,
            "aar_timestamp": aar_record.get("timestamp"),
            "mission_name": mission_name,
            "difficulty_class": difficulty_class,
            "beat": beat,
            "terminus_slain": terminus_slain or {},
            "terminus_killed": terminus_killed,  # derived bool for backward compat
            "is_omega": difficulty_class == "omega_ops",
            "strats_active": strats_active or [],
            "co_runner_ids": co_runner_ids,
            "officer_tiers": list(officer_tiers),
        }

    # Build and record the submitter's entry
    entry = _make_entry(user_id)
    campaign_log[entry["entry_id"]] = entry
    newly_credited = [user_id]

    # Update submitter metadata
    record["last_aar_timestamp"] = aar_record.get("timestamp")

    # Credit prestige and update milestones for the submitter
    _credit_prestige_for_entry(state, user_id, record, entry, aar_record, brother_ids, effective_base)
    _update_milestone_progress(state, user_id, record, entry, aar_record)

    # Auto-credit enrolled co-runners not yet credited for this AAR
    for co_id in co_runner_ids:
        if co_id in already_credited:
            continue
        co_record = enlistment.get(co_id)
        if not co_record or not co_record.get("active"):
            continue
        co_entry = _make_entry(co_id)
        co_entry["entry_id"] = _entry_id()
        campaign_log[co_entry["entry_id"]] = co_entry
        newly_credited.append(co_id)
        co_record["last_aar_timestamp"] = aar_record.get("timestamp")
        _credit_prestige_for_entry(state, co_id, co_record, co_entry, aar_record, brother_ids, effective_base)
        _update_milestone_progress(state, co_id, co_record, co_entry, aar_record)

    # Record all newly credited members against this AAR
    credited_aars[aar_id] = list(set(already_credited + newly_credited))

    # If terminus killed, record it with type/count detail
    if terminus_killed:
        ops_window.setdefault("terminus_calls", [])
        ops_window["terminus_calls"].append({
            "user_id": user_id,
            "entry_id": entry["entry_id"],
            "reported_at": _iso_now(),
            "terminus_slain": terminus_slain or {},
        })

    _save_campaign_state(state)
    co_count = len(newly_credited) - 1
    co_note = f" ({co_count} co-runner{'s' if co_count != 1 else ''} also credited)" if co_count else ""

    prestige_str = f"+{effective_base:.2g}"
    if bonus_reasons:
        prestige_str += f" ({' · '.join(bonus_reasons)})"
    terminus_note = ""
    if terminus_slain:
        kills = ", ".join(f"{v}× {k}" for k, v in terminus_slain.items())
        terminus_note = f"\nTerminus slain: {kills}"

    return True, (
        f"Campaign log submitted{co_note}.\n"
        f"**Mission:** {mission_name} | **Difficulty:** {difficulty_class} | **Cycle:** {beat or 'unknown'}\n"
        f"**Prestige:** {prestige_str} base (× formation multiplier)"
        + terminus_note
    ), entry


def _parse_mission_name(raw: str) -> str:
    """Strip Discord role mentions from mission name."""
    import re
    return re.sub(r"<[^>]+>", "", raw).strip()


def _credit_prestige_for_entry(
    state: dict,
    user_id: str,
    enlistment_record: dict,
    entry: dict,
    aar_record: dict,
    brother_ids: List[str],
    base_prestige: float,
):
    """Credit prestige to the appropriate kill team or company for this log entry."""
    tier = enlistment_record.get("tier")
    kt_sgt_id = enlistment_record.get("kt_sgt_id")

    kill_teams = state.setdefault("kill_teams", {})
    companies = state.setdefault("companies", {})
    enlistment = state.get("enlistment", {})

    co_run_info = _classify_co_runners(user_id, enlistment_record, brother_ids, enlistment)

    def _write_kt_prestige(sgt_id: str, amount: float, multiplier: float):
        kt = kill_teams.get(sgt_id)
        if not kt:
            return
        kt.setdefault("prestige_log", []).append({
            "earned_at": entry.get("aar_timestamp") or entry.get("submitted_at"),
            "member_id": user_id,
            "base_amount": round(base_prestige, 4),
            "multiplier": round(multiplier, 4),
            "credited_amount": round(amount, 4),
            "campaign_log_entry_id": entry["entry_id"],
        })

    def _write_company_prestige(co_id: str, amount: float, multiplier: float):
        co = companies.get(co_id)
        if not co:
            return
        co.setdefault("prestige_log", []).append({
            "earned_at": entry.get("aar_timestamp") or entry.get("submitted_at"),
            "member_id": user_id,
            "base_amount": round(base_prestige, 4),
            "multiplier": round(multiplier, 4),
            "credited_amount": round(amount, 4),
            "campaign_log_entry_id": entry["entry_id"],
        })

    if tier == "KT":
        if not kt_sgt_id:
            return
        kt_brothers = co_run_info["kt_brothers"]
        co_brothers = co_run_info["co_brothers"]
        multiplier = _RATE_KT + kt_brothers * _KT_BROTHER_STEP + co_brothers * _CO_BROTHER_STEP
        _write_kt_prestige(kt_sgt_id, base_prestige * multiplier, multiplier)

    elif tier == "Company":
        kt_formations = co_run_info["kt_formations"]
        company_id = enlistment_record.get("company_id")
        if kt_formations:
            # Credit the own-company KT with the most members present
            best_kt = max(kt_formations, key=lambda k: kt_formations[k])
            count = kt_formations[best_kt]
            multiplier = _RATE_COMPANY + count * _OWN_KT_STEP_COMPANY
            _write_kt_prestige(best_kt, base_prestige * multiplier, multiplier)
        else:
            # No enrolled KT members — credit directly to the company pool
            if company_id:
                multiplier = _RATE_COMPANY
                _write_company_prestige(company_id, base_prestige * multiplier, multiplier)

    elif tier == "HC":
        kt_formations = co_run_info["kt_formations"]
        co_formations = co_run_info["co_formations"]
        all_formation_ids = list(kt_formations.keys()) + list(co_formations.keys())
        n = max(1, len(set(all_formation_ids)))

        for sgt_id, count in kt_formations.items():
            multiplier = (_RATE_HC / n) + count * _KT_STEP_HC
            _write_kt_prestige(sgt_id, base_prestige * multiplier, multiplier)

        for co_id, count in co_formations.items():
            multiplier = (_RATE_HC / n) + count * _CO_CMD_STEP_HC
            _write_company_prestige(co_id, base_prestige * multiplier, multiplier)

        # Pure HC run — no line formations present.
        # Broadcast a small fixed rate to all enrolled companies and KTs.
        # Represents the Watch's general command activity benefiting the whole force.
        if not kt_formations and not co_formations:
            _HC_BROADCAST_RATE = 0.10
            all_kts = list(kill_teams.keys())
            all_cos = list(companies.keys())
            recipients = len(all_kts) + len(all_cos)
            if recipients:
                share = (_HC_BROADCAST_RATE * base_prestige) / recipients
                for sgt_id in all_kts:
                    _write_kt_prestige(sgt_id, share, _HC_BROADCAST_RATE / recipients)
                for co_id in all_cos:
                    _write_company_prestige(co_id, share, _HC_BROADCAST_RATE / recipients)



# ---------------------------------------------------------------------------
# Prestige computation
# ---------------------------------------------------------------------------


def compute_kt_prestige(kt_sgt_id: str, window_days: int = _PRESTIGE_WINDOW_DAYS, state: Optional[dict] = None) -> int:
    """Return rolling window prestige total for a kill team.

    Sums credited_amount for all prestige_log entries within the window.
    """
    if state is None:
        state = _load_campaign_state()
    kt = state.get("kill_teams", {}).get(kt_sgt_id)
    if not kt:
        return 0

    cutoff = _utcnow() - timedelta(days=window_days)
    total = 0
    for entry in kt.get("prestige_log", []):
        earned = _parse_iso(entry.get("earned_at"))
        if earned and earned >= cutoff:
            total += entry.get("credited_amount", 0)
    return total


def compute_company_prestige(company_id: str, window_days: int = _PRESTIGE_WINDOW_DAYS, state: Optional[dict] = None) -> int:
    """Return rolling window prestige for a company.

    Sums:
    - Each KT's prestige (capped at 25% per KT) for KTs belonging to this company.
    - Direct prestige credits written to the company's own prestige_log.
    """
    if state is None:
        state = _load_campaign_state()

    cutoff = _utcnow() - timedelta(days=window_days)
    kill_teams = state.get("kill_teams", {})
    total = 0

    # KT rollup with per-KT 25% cap
    for sgt_id, kt in kill_teams.items():
        if kt.get("company_id") != company_id:
            continue
        kt_total = compute_kt_prestige(sgt_id, window_days, state=state)
        total += round(kt_total * 0.25)

    # Direct credits on the company record (Company cmd / HC submissions)
    company = state.get("companies", {}).get(company_id, {})
    for entry in company.get("prestige_log", []):
        earned = _parse_iso(entry.get("earned_at"))
        if earned and earned >= cutoff:
            total += entry.get("credited_amount", 0)

    return total


def refresh_prestige_cache(state: Optional[dict] = None) -> dict:
    """Recompute and cache prestige totals for all kill teams and companies.

    Returns the updated state.
    """
    if state is None:
        state = _load_campaign_state()
    now_iso = _iso_now()

    # KTs
    for sgt_id, kt in state.get("kill_teams", {}).items():
        kt["prestige_window_total"] = compute_kt_prestige(sgt_id, state=state)
        kt["last_prestige_check"] = now_iso

    # Companies
    for company_id, company in state.get("companies", {}).items():
        company["prestige_window_total"] = compute_company_prestige(company_id, state=state)
        company["last_prestige_check"] = now_iso

    return state


# ---------------------------------------------------------------------------
# Reward checks (hysteresis: acquire > retain threshold)
# ---------------------------------------------------------------------------

# KT numeric thresholds
_KT_RIBBON_ACTIVE_ACQUIRE = 150
_KT_RIBBON_ACTIVE_RETAIN = 90
_KT_TITLE_ACQUIRE = 450
_KT_TITLE_RETAIN = 270
_KT_HONOUR_STALWART_ACQUIRE = 1000
_KT_HONOUR_STALWART_RETAIN = 650
_KT_LORE_FLOOR = 250
_KT_LORE_RETAIN = 140

# Company numeric thresholds
_CO_RIBBON_ACTIVE_ACQUIRE = 250
_CO_RIBBON_ACTIVE_RETAIN = 150
_CO_TITLE_ACQUIRE = 700
_CO_TITLE_RETAIN = 420
_CO_HONOUR_STALWART_ACQUIRE = 1600
_CO_HONOUR_STALWART_RETAIN = 1000
_CO_LORE_FLOOR = 400
_CO_LORE_RETAIN = 220


def check_kt_ribbon_active(kt_sgt_id: str, state: dict) -> bool:
    """Return True if KT meets condition for kt_ribbon_active (hysteresis)."""
    kt = state.get("kill_teams", {}).get(kt_sgt_id, {})
    prestige = kt.get("prestige_window_total", 0)
    current = kt.get("ribbon") == "kt_ribbon_active"
    if current:
        return prestige >= _KT_RIBBON_ACTIVE_RETAIN
    return prestige >= _KT_RIBBON_ACTIVE_ACQUIRE


def check_reward_thresholds(state: dict) -> dict:
    """Check and update automatic ribbon and honour awards for all KTs and companies.

    Mutates state in place. Returns state.
    """
    # --- Kill team ribbons (numeric threshold) ---
    all_kt_prestige = {
        sgt_id: kt.get("prestige_window_total", 0)
        for sgt_id, kt in state.get("kill_teams", {}).items()
    }

    # kt_ribbon_active: numeric hysteresis
    for sgt_id, kt in state.get("kill_teams", {}).items():
        prestige = all_kt_prestige.get(sgt_id, 0)
        current = kt.get("ribbon") == "kt_ribbon_active"
        if current:
            qualifies = prestige >= _KT_RIBBON_ACTIVE_RETAIN
        else:
            qualifies = prestige >= _KT_RIBBON_ACTIVE_ACQUIRE
        if qualifies and not current:
            kt["ribbon"] = "kt_ribbon_active"
        elif not qualifies and current:
            kt["ribbon"] = None

    # kt_ribbon_omega: relative — KT with most omega ops (min 2)
    kt_omega_counts = _count_kt_omega_ops(state)
    top_omega = max(kt_omega_counts.values(), default=0)
    for sgt_id, kt in state.get("kill_teams", {}).items():
        current_is_omega = kt.get("ribbon") == "kt_ribbon_omega"
        count = kt_omega_counts.get(sgt_id, 0)
        qualifies = (count == top_omega and top_omega >= 2)
        if qualifies and not current_is_omega:
            if kt.get("ribbon") in (None, "kt_ribbon_active"):
                kt["ribbon"] = "kt_ribbon_omega"
        elif not qualifies and current_is_omega:
            kt["ribbon"] = None

    # kt_ribbon_vanguard: relative — KT with most total ops (min 5)
    kt_op_counts = _count_kt_total_ops(state)
    top_ops = max(kt_op_counts.values(), default=0)
    for sgt_id, kt in state.get("kill_teams", {}).items():
        current_is_vanguard = kt.get("ribbon") == "kt_ribbon_vanguard"
        count = kt_op_counts.get(sgt_id, 0)
        qualifies = (count == top_ops and top_ops >= 5)
        if qualifies and not current_is_vanguard:
            if kt.get("ribbon") in (None, "kt_ribbon_active"):
                kt["ribbon"] = "kt_ribbon_vanguard"
        elif not qualifies and current_is_vanguard:
            kt["ribbon"] = None

    # kt_honour_stalwart: numeric hysteresis
    for sgt_id, kt in state.get("kill_teams", {}).items():
        prestige = all_kt_prestige.get(sgt_id, 0)
        current = "kt_honour_stalwart" in (kt.get("honour") or [])
        if current:
            qualifies = prestige >= _KT_HONOUR_STALWART_RETAIN
        else:
            qualifies = prestige >= _KT_HONOUR_STALWART_ACQUIRE
        honours = kt.setdefault("honour", [])
        if qualifies and not current:
            honours.append("kt_honour_stalwart")
        elif not qualifies and current:
            try:
                honours.remove("kt_honour_stalwart")
            except ValueError:
                pass

    # --- Company ribbons ---
    all_co_prestige = {
        company_id: co.get("prestige_window_total", 0)
        for company_id, co in state.get("companies", {}).items()
    }

    # co_ribbon_active: numeric hysteresis
    for company_id, company in state.get("companies", {}).items():
        prestige = all_co_prestige.get(company_id, 0)
        current = company.get("ribbon") == "co_ribbon_active"
        if current:
            qualifies = prestige >= _CO_RIBBON_ACTIVE_RETAIN
        else:
            qualifies = prestige >= _CO_RIBBON_ACTIVE_ACQUIRE
        if qualifies and not current:
            company["ribbon"] = "co_ribbon_active"
        elif not qualifies and current:
            company["ribbon"] = None

    # co_ribbon_vanguard: relative — most total ops (min 15)
    co_op_counts = _count_company_total_ops(state)
    top_co_ops = max(co_op_counts.values(), default=0)
    for company_id, company in state.get("companies", {}).items():
        current_is_vanguard = company.get("ribbon") == "co_ribbon_vanguard"
        count = co_op_counts.get(company_id, 0)
        qualifies = (count == top_co_ops and top_co_ops >= 15)
        if qualifies and not current_is_vanguard:
            if company.get("ribbon") in (None, "co_ribbon_active"):
                company["ribbon"] = "co_ribbon_vanguard"
        elif not qualifies and current_is_vanguard:
            company["ribbon"] = None

    # co_honour_stalwart: numeric hysteresis
    for company_id, company in state.get("companies", {}).items():
        prestige = all_co_prestige.get(company_id, 0)
        current = company.get("honour") == "co_honour_stalwart"
        if current:
            qualifies = prestige >= _CO_HONOUR_STALWART_RETAIN
        else:
            qualifies = prestige >= _CO_HONOUR_STALWART_ACQUIRE
        if qualifies and not current:
            company["honour"] = "co_honour_stalwart"
        elif not qualifies and current:
            company["honour"] = None

    # --- Iron Compact ribbons and honours ---
    current_beat = state.get("campaign", {}).get("beat")
    total_beats = state.get("total_beats") or state.get("campaign", {}).get("total_beats") or 3
    if current_beat is not None:
        # KT Iron Compact: unique ops per beat where a Company/HC co-runner was present
        kt_ic_ops = _count_kt_iron_compact_ops(state, current_beat)
        for sgt_id, kt in state.get("kill_teams", {}).items():
            count = kt_ic_ops.get(sgt_id, 0)
            ic_beats = kt.setdefault("iron_compact_beats", [])
            if count >= _KT_IRON_COMPACT_RIBBON_OPS and current_beat not in ic_beats:
                ic_beats.append(current_beat)
            honours = kt.setdefault("honour", [])
            if (
                len(ic_beats) >= total_beats
                and "kt_honour_iron_compact" not in honours
            ):
                honours.append("kt_honour_iron_compact")

        # Company Iron Compact: unique ops per beat where an HC co-runner was present
        co_ic_ops = _count_company_iron_compact_ops(state, current_beat)
        for co_id, company in state.get("companies", {}).items():
            count = co_ic_ops.get(co_id, 0)
            ic_beats = company.setdefault("iron_compact_beats", [])
            if count >= _CO_IRON_COMPACT_RIBBON_OPS and current_beat not in ic_beats:
                ic_beats.append(current_beat)
            if (
                len(ic_beats) >= total_beats
                and not company.get("honour_iron_compact")
            ):
                company["honour_iron_compact"] = True

    return state


def _count_kt_iron_compact_ops(state: dict, beat) -> Dict[str, int]:
    """Count unique op (AAR) appearances per KT for a given beat where a Command/HC
    co-runner was present.  Returns {sgt_id: unique_aar_count}."""
    enlistment = state.get("enlistment", {})
    # {sgt_id: set of aar_ids}
    seen: Dict[str, set] = {}
    for entry in state.get("campaign_log", {}).values():
        if not isinstance(entry, dict) or entry.get("beat") != beat:
            continue
        officer_tiers = entry.get("officer_tiers", [])
        if not any(t in ("Company", "HC") for t in officer_tiers):
            continue
        submitter = entry.get("submitted_by")
        rec = enlistment.get(submitter)
        if not rec or rec.get("tier") != "KT":
            continue
        sgt = rec.get("kt_sgt_id")
        if sgt:
            seen.setdefault(sgt, set()).add(entry.get("aar_id"))
    return {sgt: len(aars) for sgt, aars in seen.items()}


def _count_company_iron_compact_ops(state: dict, beat) -> Dict[str, int]:
    """Count unique op (AAR) appearances per company for a given beat where an HC
    co-runner was present.  Returns {company_id: unique_aar_count}."""
    enlistment = state.get("enlistment", {})
    seen: Dict[str, set] = {}
    for entry in state.get("campaign_log", {}).values():
        if not isinstance(entry, dict) or entry.get("beat") != beat:
            continue
        officer_tiers = entry.get("officer_tiers", [])
        if "HC" not in officer_tiers:
            continue
        submitter = entry.get("submitted_by")
        rec = enlistment.get(submitter)
        if not rec or rec.get("tier") != "Company":
            continue
        co_id = rec.get("company_id")
        if co_id:
            seen.setdefault(co_id, set()).add(entry.get("aar_id"))
    return {co_id: len(aars) for co_id, aars in seen.items()}


def _count_kt_omega_ops(state: dict) -> Dict[str, int]:
    """Return {sgt_id: omega_op_count} from campaign_log."""
    counts: Dict[str, int] = {}
    enlistment = state.get("enlistment", {})
    for entry in state.get("campaign_log", {}).values():
        if not isinstance(entry, dict) or not entry.get("is_omega"):
            continue
        submitter = entry.get("submitted_by")
        rec = enlistment.get(submitter)
        if rec:
            sgt = rec.get("kt_sgt_id") or (rec.get("operational_attachment") or {}).get("attached_kt_sgt_id")
            if sgt:
                counts[sgt] = counts.get(sgt, 0) + 1
    return counts


def _count_kt_total_ops(state: dict) -> Dict[str, int]:
    """Return {sgt_id: total_op_count} from campaign_log."""
    counts: Dict[str, int] = {}
    enlistment = state.get("enlistment", {})
    for entry in state.get("campaign_log", {}).values():
        if not isinstance(entry, dict):
            continue
        submitter = entry.get("submitted_by")
        rec = enlistment.get(submitter)
        if rec:
            sgt = rec.get("kt_sgt_id") or (rec.get("operational_attachment") or {}).get("attached_kt_sgt_id")
            if sgt:
                counts[sgt] = counts.get(sgt, 0) + 1
    return counts


def _count_company_total_ops(state: dict) -> Dict[str, int]:
    """Return {company_id: total_op_count} from campaign_log."""
    counts: Dict[str, int] = {}
    enlistment = state.get("enlistment", {})
    for entry in state.get("campaign_log", {}).values():
        if not isinstance(entry, dict):
            continue
        submitter = entry.get("submitted_by")
        rec = enlistment.get(submitter)
        if rec:
            company_id = rec.get("company_id")
            if company_id:
                counts[company_id] = counts.get(company_id, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Lore priority
# ---------------------------------------------------------------------------


def update_lore_priority(state: Optional[dict] = None, save: bool = True) -> dict:
    """Recompute and update lore_priority for top KT and company.

    Floors: KT >= 250 (retain 140), company >= 400 (retain 220).
    """
    if state is None:
        state = _load_campaign_state()

    now_iso = _iso_now()
    lp = state.setdefault("lore_priority", {"kill_team": {}, "company": {}})

    # --- Kill team ---
    current_kt = lp.get("kill_team", {}).get("sgt_user_id")
    best_sgt = None
    best_sgt_prestige = 0
    for sgt_id, kt in state.get("kill_teams", {}).items():
        p = kt.get("prestige_window_total", 0)
        if p > best_sgt_prestige:
            best_sgt_prestige = p
            best_sgt = sgt_id

    # Hysteresis: if already held, retain floor; else acquire floor
    if current_kt and current_kt == best_sgt:
        kt_qualifies = best_sgt_prestige >= _KT_LORE_RETAIN
    else:
        kt_qualifies = best_sgt_prestige >= _KT_LORE_FLOOR

    if kt_qualifies and best_sgt:
        kt_data = state.get("kill_teams", {}).get(best_sgt, {})
        if lp.get("kill_team", {}).get("sgt_user_id") != best_sgt:
            lp["kill_team"] = {
                "sgt_user_id": best_sgt,
                "display_name": kt_data.get("display_name"),
                "prestige": best_sgt_prestige,
                "held_since": now_iso,
            }
        else:
            lp["kill_team"]["prestige"] = best_sgt_prestige
    else:
        lp["kill_team"] = {"sgt_user_id": None, "display_name": None, "prestige": None, "held_since": None}

    # Sync lore_priority flag on kill teams
    for sgt_id, kt in state.get("kill_teams", {}).items():
        kt["lore_priority"] = (lp["kill_team"].get("sgt_user_id") == sgt_id)

    # --- Company ---
    current_co = lp.get("company", {}).get("company_id")
    best_co = None
    best_co_prestige = 0
    for company_id, company in state.get("companies", {}).items():
        p = company.get("prestige_window_total", 0)
        if p > best_co_prestige:
            best_co_prestige = p
            best_co = company_id

    if current_co and current_co == best_co:
        co_qualifies = best_co_prestige >= _CO_LORE_RETAIN
    else:
        co_qualifies = best_co_prestige >= _CO_LORE_FLOOR

    if co_qualifies and best_co:
        co_data = state.get("companies", {}).get(best_co, {})
        if lp.get("company", {}).get("company_id") != best_co:
            lp["company"] = {
                "company_id": best_co,
                "display_name": co_data.get("display_name"),
                "prestige": best_co_prestige,
                "held_since": now_iso,
            }
        else:
            lp["company"]["prestige"] = best_co_prestige
    else:
        lp["company"] = {"company_id": None, "display_name": None, "prestige": None, "held_since": None}

    # Sync lore_priority flag on companies
    for company_id, company in state.get("companies", {}).items():
        company["lore_priority"] = (lp["company"].get("company_id") == company_id)

    if save:
        _save_campaign_state(state)
    return state


# ---------------------------------------------------------------------------
# Strat mandate scoring (doctrine_strat_map.json algorithm)
# ---------------------------------------------------------------------------


def score_strats_against_aggregate(
    doctrine_aggregate: Dict[str, float],
    include_blacklisted: bool = False,
) -> List[Tuple[str, float, dict]]:
    """Score all eligible stratagems against a doctrine tag aggregate.

    Returns list of (strat_name, score, strat_dict) sorted by score descending.

    Algorithm:
      1. Aggregate doctrine tags (provided as input).
      2. For each non-excluded strat, look up its game_tags in doctrine_families.
         For each game_tag, find all doctrine families that include that tag
         and score: doctrine_value * tag_weight.
         Terminus tags get 1.5x multiplier on the terminus doctrine score.
      3. Sum scores across all tags.
      4. Filter out blacklisted strats (unless include_blacklisted=True).
      5. Return sorted list.
    """
    _ensure_refs_loaded()

    blacklist = set(_DOCTRINE_STRAT_MAP.get("pool_blacklist", {}).get("entries", {}).keys())

    # Build reverse map: game_tag -> [(doctrine_family, weight)]
    tag_to_families: Dict[str, List[Tuple[str, float]]] = {}
    for family, fdata in _DOCTRINE_STRAT_MAP.get("doctrine_families", {}).items():
        if family.startswith("_"):
            continue
        for tag, weight in fdata.get("strat_game_tags", {}).items():
            tag_to_families.setdefault(tag, []).append((family, weight))

    # Score strats
    results = []
    for strat in _STRATAGEMS:
        if strat.get("excluded"):
            continue
        name = strat.get("name", "")
        if not include_blacklisted and name in blacklist:
            continue
        score = 0.0
        for game_tag in strat.get("tags", []):
            for (family, weight) in tag_to_families.get(game_tag, []):
                doctrine_val = doctrine_aggregate.get(family, 0.0)
                # terminus family gets 1.5x bonus when tag maps to terminus
                effective_weight = weight * (1.5 if family == "terminus" else 1.0)
                score += doctrine_val * effective_weight
        results.append((name, score, strat))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _build_conflict_set(pool: List[str]) -> Dict[str, set]:
    """Return {strat_name: set_of_blocked_names} for all strats in pool."""
    _ensure_refs_loaded()
    ref_strats = _load_ref("stratagems.json")
    cat_groups = ref_strats.get("conflict_map", {}).get("category_groups", {})
    specific = ref_strats.get("conflict_map", {}).get("specific_conflicts", {})

    blocked: Dict[str, set] = {name: set() for name in pool}

    # Category groups: all pairs within a group block each other
    for group in cat_groups.values():
        members = [m for m in group.get("members", []) if m in blocked]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                blocked[a].add(b)
                blocked[b].add(a)

    # Specific conflicts (bidirectional)
    for strat_name, info in specific.items():
        if strat_name.startswith("_"):
            continue
        for blocked_strat in info.get("blocks", []):
            if strat_name in blocked:
                blocked[strat_name].add(blocked_strat)
            if blocked_strat in blocked:
                blocked[blocked_strat].add(strat_name)

    return blocked


def _derive_ops_mandate(state: dict) -> dict:
    """Derive the eligible mission pool for the current cycle.

    Returns:
        {
            'committed_node': str,          # planet name
            'eligible_mission_ids': [int],  # union of planet + enrolled role affinities
            'eligible_missions': [{'id': int, 'name': str, 'terminus_boss': str|None}],
        }
    """
    _ensure_refs_loaded()
    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "operations.json")
        ) as _f:
            ops_ref = json.load(_f)
    except Exception:
        return {"committed_node": None, "eligible_mission_ids": [], "eligible_missions": []}

    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "rank_mappings.json")
        ) as _f:
            rank_ref = json.load(_f)
    except Exception:
        rank_ref = {}

    all_ops = ops_ref.get("operations", [])
    op_by_id: Dict[int, dict] = {op["id"]: op for op in all_ops}

    current_node = (state.get("campaign", {}).get("current_node") or "").lower()
    # Planet-based eligibility
    planet_eligible: set = {
        op["id"] for op in all_ops
        if (op.get("planet") or "").lower() == current_node
    }

    # Role affinity missions from enrolled active members
    # Vote each non-planet op by how many enrolled roles unlock it; cap extras at 3.
    enlistment = state.get("enlistment", {})
    submissions = state.get("cascade", {}).get("submissions", {})
    submitted_roles: set = {s.get("role_key", "") for s in submissions.values() if s.get("role_key")}
    affinity_votes: Dict[int, int] = {}
    for tier_key in ("HC", "Company", "KT"):
        for rank_name, rank_data in rank_ref.get(tier_key, {}).items():
            if rank_name.startswith("_"):
                continue
            role_key = rank_name.lower().replace(" ", "_")
            role_present = any(
                rec.get("role", "") == rank_name and rec.get("active")
                for rec in enlistment.values()
            )
            if role_present or role_key in submitted_roles:
                for mid in rank_data.get("ops", {}).get("mission_ids", []):
                    if mid not in planet_eligible:
                        affinity_votes[mid] = affinity_votes.get(mid, 0) + 1

    # Take top-3 non-planet affinity ops by vote count (capped so pool stays ≤8)
    _MAX_AFFINITY_EXTRA = 3
    top_affinity: set = set(
        sorted(affinity_votes, key=lambda m: -affinity_votes[m])[:_MAX_AFFINITY_EXTRA]
    )

    eligible_ids = sorted(planet_eligible | top_affinity)
    eligible_missions = [
        {
            "id": mid,
            "name": op_by_id[mid]["name"],
            "terminus_boss": op_by_id[mid].get("terminus_boss"),
        }
        for mid in eligible_ids
        if mid in op_by_id
    ]

    return {
        "committed_node": state.get("campaign", {}).get("current_node"),
        "eligible_mission_ids": eligible_ids,
        "eligible_missions": eligible_missions,
    }


def _derive_terminus_directive(state: dict) -> dict:
    """Derive the terminus directive for the current cycle.

    Huntmaster (if enlisted) sets the flag. Champions/domain Specialists
    provide call_engagement. sentence_mark (Judiciar) is a KT-level call.

    Returns:
        {
            'huntmaster_active': bool,
            'flagged_targets': [str],   # roaming + boss names eligible this cycle
            'callers': [str],           # display names of call_engagement roles enlisted
            'prestige_terminus_live': bool,  # True if Huntmaster active (gates omega prestige)
        }
    """
    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "rank_mappings.json")
        ) as _f:
            rank_ref = json.load(_f)
    except Exception:
        rank_ref = {}

    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "operations.json")
        ) as _f:
            ops_ref = json.load(_f)
    except Exception:
        ops_ref = {}

    enlistment = state.get("enlistment", {})
    active_roles: set = {
        rec.get("role", "")
        for rec in enlistment.values()
        if rec.get("active") and rec.get("role")
    }

    huntmaster_active = "Huntmaster" in active_roles
    callers: List[str] = []
    for tier_key in ("HC", "Company", "KT"):
        for rank_name, rank_data in rank_ref.get(tier_key, {}).items():
            if rank_name.startswith("_"):
                continue
            term = rank_data.get("terminus", {})
            if not isinstance(term, dict):
                continue
            if term.get("mode") in ("call_engagement", "sentence_mark") and rank_name in active_roles:
                callers.append(rank_name)

    # Eligible terminus targets: roaming + fixed boss missions in eligible pool
    ops_mandate = state.get("strat_pool", {}).get("ops_mandate", {})
    eligible_missions = ops_mandate.get("eligible_missions", [])
    eligible_ids = ops_mandate.get("eligible_mission_ids", [])
    all_ops = {op["id"]: op for op in ops_ref.get("operations", [])}
    has_tyranid = any(
        "tyranid" in (all_ops.get(mid, {}).get("faction_effective", "") or "")
        for mid in eligible_ids
    )
    has_mixed = any(
        "mixed" in (all_ops.get(mid, {}).get("faction_effective", "") or "")
        for mid in eligible_ids
    )

    # flagged_targets: list of dicts with name, source_op, type (fixed/roaming)
    flagged: List[dict] = []
    for m in eligible_missions:
        if m.get("terminus_boss"):
            flagged.append({"name": m["terminus_boss"], "source_op": m["name"], "type": "fixed"})
    for t in ops_ref.get("roaming_terminus", []):
        faction = t.get("faction", "")
        if faction == "tyranid" and (has_tyranid or has_mixed):
            flagged.append({"name": t["name"], "source_op": None, "type": "roaming"})
        elif faction == "chaos" and has_mixed:
            flagged.append({"name": t["name"], "source_op": None, "type": "roaming"})

    return {
        "huntmaster_active": huntmaster_active,
        "flagged_targets": flagged,
        "callers": callers,
        "prestige_terminus_live": huntmaster_active,
    }


def _tier_mandate_count(n_distinct_roles: int, tier: str) -> int:
    """Return how many mandate strats to pick for a tier, based on participation.

    KT tier caps at 2 (only 2 eligible roles exist).
    HC and Company tiers: 1-2 roles → 1, 3-4 → 2, 5+ → 3.
    """
    if tier == "KT":
        return min(2, max(1, n_distinct_roles))
    if n_distinct_roles <= 2:
        return 1
    if n_distinct_roles <= 4:
        return 2
    return 3


def derive_strat_mandate(
    doctrine_aggregate: Dict[str, float],
    confirmed_pool: List[str],
    state: Optional[dict] = None,
    tier_counts: Optional[Dict[str, int]] = None,
    theatre_aggregate: Optional[Dict[str, float]] = None,
    company_aggregate: Optional[Dict[str, float]] = None,
) -> dict:
    """Derive theatre, company, and KT mandates from the confirmed strat pool.

    Each tier gets 1-3 mandates depending on cascade participation (tier_counts).
    When *theatre_aggregate* and *company_aggregate* are supplied, each tier's
    mandate is scored against its own aggregate (tiered doctrine authority):
      - Theatre: WM + HC submissions only
      - Company: WM + HC + Company submissions
      - KT: full aggregate (all tiers)
    Falls back to *doctrine_aggregate* for any tier whose aggregate is absent.

    Returns:
      {
        'theatre_mandate': [str, ...],       # 1-3 strats
        'company_mandates': {company_id: [str, ...]},
        'kt_mandates': {sgt_user_id: [str, ...]},
      }
    """
    _ensure_refs_loaded()
    if state is None:
        state = _load_campaign_state()
    if tier_counts is None:
        tier_counts = {"theatre": 1, "company": 1, "kt": 1}

    pool_set = set(confirmed_pool)
    conflict_set = _build_conflict_set(confirmed_pool)

    def _scored_for(agg: Dict[str, float]) -> List[Tuple[str, float, dict]]:
        result = score_strats_against_aggregate(agg)
        return [(name, score, strat) for name, score, strat in result if name in pool_set]

    t_scored = _scored_for(theatre_aggregate if theatre_aggregate else doctrine_aggregate)
    c_scored = _scored_for(company_aggregate if company_aggregate else doctrine_aggregate)
    kt_scored = _scored_for(doctrine_aggregate)

    # Global used set — prevents same strat appearing twice across all mandates
    used: List[str] = []

    def _pick_next(scored_list: List[Tuple], local_exclude: set) -> Optional[str]:
        exclude = local_exclude | set(used)
        for name, _score, _strat in scored_list:
            if name in exclude:
                continue
            if any(name in conflict_set.get(m, set()) for m in used):
                continue
            return name
        return None

    def _pick_n(n: int, scored_list: List[Tuple], extra_exclude: Optional[set] = None) -> List[str]:
        picks: List[str] = []
        ex = set(extra_exclude or [])
        for _ in range(n):
            s = _pick_next(scored_list, ex)
            if s:
                used.append(s)
                picks.append(s)
                ex.add(s)
        return picks

    # Theatre mandates — driven by WM + HC doctrine only
    theatre_mandates = _pick_n(tier_counts.get("theatre", 1), t_scored)

    # Company mandates — driven by WM + HC + Company doctrine
    company_mandates: Dict[str, List[str]] = {}
    co_n = tier_counts.get("company", 1)
    for company_id in state.get("companies", {}).keys():
        co_picks = _pick_n(co_n, c_scored)
        company_mandates[company_id] = co_picks

    # KT mandates — driven by full doctrine (all tiers)
    kt_mandates: Dict[str, List[str]] = {}
    kt_n = tier_counts.get("kt", 1)
    for sgt_id in state.get("kill_teams", {}).keys():
        kt_picks = _pick_n(kt_n, kt_scored)
        kt_mandates[sgt_id] = kt_picks

    return {
        "theatre_mandate": theatre_mandates,
        "company_mandates": company_mandates,
        "kt_mandates": kt_mandates,
    }


# ---------------------------------------------------------------------------
# Scenario generation
# ---------------------------------------------------------------------------


def generate_beat_scenario(
    node_id: str,
    node_type: str,
    region: Optional[str],
    current_pressure: int,
    beat_seed: Optional[int] = None,
    beat_num: int = 0,
    slot: int = 0,
) -> dict:
    """Generate a scenario for a node for the upcoming beat.

    *slot* selects which of the three threat_vectors to use (0-2). Each vector
    is a distinct doctrinal angle; region/pressure modifiers are applied on top.

    Returns a scenario dict matching the output_scenario schema in scenario_generation.json.
    """
    _ensure_refs_loaded()
    sg = _SCENARIO_GEN

    rng = random.Random(beat_seed ^ (slot * 0xDEAD)) if beat_seed is not None else random.Random()

    node_affinity = sg.get("node_type_affinity", {})
    region_modifier = sg.get("region_modifier", {})
    pressure_rules = sg.get("pressure_rules", {}).get("pressure_thresholds", {})
    pressure_steps = sg.get("pressure_rules", {}).get("terminus_intel_steps", ["none", "suspected", "known"])
    pmt = sg.get("pressure_modifier_table", {})
    mission_bias = sg.get("mission_bias_table", {})
    codename_pools = sg.get("codename_pools", {})
    narrative_templates = sg.get("narrative_templates", {})

    # Step 1: Base dominant tags from node type — use threat_vectors[slot] if available
    nta = node_affinity.get(node_type, {})
    vectors = nta.get("threat_vectors", [])
    if vectors:
        vector = vectors[slot % len(vectors)]
        base_tags = list(vector.get("tags", ["aggressive", "recovery"]))
        terminus_affinity = vector.get("terminus_affinity") or nta.get("terminus_affinity", "low")
    else:
        # Fallback for node types without threat_vectors
        base_tags = list(nta.get("base_tags", nta.get("dominant_tags", ["aggressive", "recovery"])))
        terminus_affinity = nta.get("terminus_affinity", "low")

    # Step 2: Region modifier — push secondary tag
    if region:
        rm = region_modifier.get(region, {})
        pushed_tag = rm.get("push_tag") or rm.get("push")
        if pushed_tag and len(base_tags) >= 2:
            base_tags[1] = pushed_tag

    # Step 3: Pressure modifier on tags and terminus_intel
    pressure_key = "4+" if current_pressure >= 4 else str(current_pressure)
    pr = pressure_rules.get(pressure_key, {})
    pushed_by_pressure = pr.get("tag_push")
    if pushed_by_pressure:
        if len(base_tags) >= 2:
            base_tags[1] = pushed_by_pressure
        else:
            base_tags.append(pushed_by_pressure)

    # Step 4: Base terminus intel
    affinity_map = {"low": 0.2, "medium": 0.5, "high": 0.8, "known": 1.0}
    affinity_prob = affinity_map.get(terminus_affinity, 0.3)
    if terminus_affinity == "known":
        base_terminus = "known"
    else:
        roll = rng.random()
        if roll < affinity_prob * 0.5:
            base_terminus = "known"
        elif roll < affinity_prob:
            base_terminus = "suspected"
        else:
            base_terminus = "none"

    # Apply pressure terminus modifier
    intel_mod = pr.get("terminus_intel_modifier", "none")
    terminus_intel = base_terminus
    if intel_mod == "force_known":
        terminus_intel = "known"
    elif intel_mod == "+1_step":
        idx = pressure_steps.index(base_terminus) if base_terminus in pressure_steps else 0
        terminus_intel = pressure_steps[min(idx + 1, len(pressure_steps) - 1)]

    # If terminus intel is suspected/known, push secondary tag to terminus
    if terminus_intel in ("suspected", "known") and len(base_tags) >= 2:
        base_tags[1] = "terminus"

    # Step 5: Pressure modifier (what Watch presence achieves)
    pressure_modifier = pmt.get(node_type, 0)

    # Step 6: Mission bias
    tag_key = f"{base_tags[0]}+{base_tags[1]}" if len(base_tags) >= 2 else base_tags[0]
    bias = mission_bias.get(tag_key)
    if not bias:
        # Try reversed
        if len(base_tags) >= 2:
            bias = mission_bias.get(f"{base_tags[1]}+{base_tags[0]}")
    if not bias:
        bias = mission_bias.get("_fallback", [1, 7])

    # Step 7: Codename
    adjective_pool = codename_pools.get("adjectives", {})
    noun_pool = codename_pools.get("nouns", {})
    primary_tag = base_tags[0] if base_tags else "aggressive"
    secondary_tag = base_tags[1] if len(base_tags) >= 2 else "recovery"
    adj_list = adjective_pool.get(primary_tag) or adjective_pool.get("_default", ["DARK"])
    noun_list = noun_pool.get(secondary_tag) or noun_pool.get("_default", ["VIGIL"])
    adjective = rng.choice(adj_list)
    noun = rng.choice(noun_list)
    codename = f"{adjective} {noun}"

    # Step 8: Narrative template
    templates = narrative_templates.get(node_type, [])
    if templates:
        template_entry = rng.choice(templates)
        narrative_raw = template_entry.get("text", "") if isinstance(template_entry, dict) else str(template_entry)
        narrative = narrative_raw.replace("{node}", node_id)
    else:
        narrative = f"The Watch deploys to {node_id}."

    # Build scenario_id
    slot_label = chr(ord('a') + slot)  # 0→a, 1→b, 2→c
    scenario_id = f"{node_id.lower().replace(' ', '_')}_b{beat_num}_{slot_label}"

    return {
        "scenario_id": scenario_id,
        "slot": slot,
        "codename": codename,
        "node_id": node_id,
        "node_type": node_type,
        "region": region,
        "dominant_tags": base_tags[:2] if len(base_tags) >= 2 else base_tags,
        "terminus_intel": terminus_intel,
        "pressure_modifier": pressure_modifier,
        "mission_bias": bias,
        "narrative": narrative,
        "generated_at": _iso_now(),
    }


# ---------------------------------------------------------------------------
# Discord embed character-limit helpers
# ---------------------------------------------------------------------------

_EMBED_DESC_MAX = 4096
_EMBED_FIELD_MAX = 1024
_EMBED_TOTAL_MAX = 6000


def _trunc(text: str, limit: int, suffix: str = "…") -> str:
    """Truncate *text* to *limit* characters, appending *suffix* if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


# ---------------------------------------------------------------------------
# Node/scenario helpers
# ---------------------------------------------------------------------------

_JERICHO_GRAPH: Optional[dict] = None


def _load_graph() -> dict:
    """Load jericho_reach_graph.json once and cache."""
    global _JERICHO_GRAPH
    if _JERICHO_GRAPH is None:
        try:
            _JERICHO_GRAPH = _load_ref("jericho_reach_graph.json")
        except Exception:
            _JERICHO_GRAPH = {"nodes": [], "edges": []}
    return _JERICHO_GRAPH


def _graph_node(node_id: str) -> Optional[dict]:
    """Return the graph node dict for *node_id*, or None."""
    for n in _load_graph().get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def _fmt_strategic_position(current_node: str) -> str:
    """Return a formatted string showing the current node and its warp approaches.

    Format:
        Hethgard · Fortress World · Iron Collar
        Warp approaches:
          ├ Eleusis [close] · Shrine World
          └ Alphos [medium] · Dead World
    """
    graph = _load_graph()
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    node_data = nodes_by_id.get(current_node, {})
    world_type = node_data.get("type", "unknown").replace("_", " ").title()
    region = node_data.get("region", "unknown").replace("_", " ").title()

    # Build proximity lookup from edges (bidirectional)
    prox_lookup: Dict[tuple, str] = {}
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        prx = e.get("proximity", "unknown")
        if src and tgt:
            prox_lookup[(src, tgt)] = prx
            prox_lookup[(tgt, src)] = prx

    adj = graph.get("adjacency", {}).get(current_node, [])
    header = f"**{current_node}** · {world_type} · {region}"
    if not adj:
        return header

    lines = [header, "Warp approaches:"]
    adj_sorted = sorted(adj)[:12]  # cap at 12 to stay within field limit
    for i, neighbor in enumerate(adj_sorted):
        prox = prox_lookup.get((current_node, neighbor), "unknown")
        n_data = nodes_by_id.get(neighbor, {})
        n_type = n_data.get("type", "unknown").replace("_", " ").title()
        connector = "└" if i == len(adj_sorted) - 1 else "├"
        lines.append(f"  {connector} {neighbor} [{prox}] · {n_type}")
    if len(adj) > 12:
        lines.append(f"  … +{len(adj) - 12} more")
    return "\n".join(lines)


def _generate_node_scenarios(state: dict) -> None:
    """Generate beat scenarios for the current node (+ adjacent nodes for WM preview).

    Stores results into state["beat_scenarios"][node_id]. Safe to call multiple
    times — existing entries are overwritten to reflect current pressure.
    """
    current_node = state.get("campaign", {}).get("current_node")
    if not current_node:
        return

    graph = _load_graph()
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    # Collect current node + adjacent nodes (bidirectional edges)
    adjacent_ids: set = set()
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        if src == current_node:
            adjacent_ids.add(tgt)
        elif tgt == current_node:
            adjacent_ids.add(src)

    target_ids = [current_node] + sorted(adjacent_ids)

    campaign = state.get("campaign", {})
    beat = campaign.get("beat") or 1
    campaign_id = campaign.get("id") or "campaign"
    pressure_data = state.get("pressure", {})

    beat_scenarios = state.setdefault("beat_scenarios", {})
    for nid in target_ids:
        node = nodes_by_id.get(nid)
        if not node:
            continue
        node_type = node.get("type", "dead_world")
        region = node.get("region")
        pressure = int(pressure_data.get(nid, {}).get("level", 0) if isinstance(pressure_data.get(nid), dict) else pressure_data.get(nid, 0))
        # Seed: deterministic per campaign/beat/node; slot XOR'd in generate_beat_scenario
        seed_str = f"{campaign_id}:{beat}:{nid}"
        beat_seed = hash(seed_str) & 0x7FFFFFFF
        # Generate 3 scenario variants (one per threat_vector slot)
        scenarios = [
            generate_beat_scenario(
                node_id=nid,
                node_type=node_type,
                region=region,
                current_pressure=pressure,
                beat_seed=beat_seed,
                beat_num=beat,
                slot=s,
            )
            for s in range(3)
        ]
        beat_scenarios[nid] = scenarios


def _resolve_cascade_WM(state: dict) -> None:
    """Apply WM's movement decision, update current_node, generate scenarios, then open cascade_HC.

    If no WM submission exists (WM not enlisted / window timed out), holds position.
    Modifies *state* in-place.
    """
    submissions = state.get("cascade", {}).get("submissions", {})
    wm_sub = next((s for s in submissions.values() if s.get("role_key") == "watch_master"), None)
    if wm_sub:
        target_node = wm_sub.get("target_node")
        if target_node:
            state["campaign"]["current_node"] = target_node
            visited = state["campaign"].setdefault("visited_nodes", [])
            if target_node not in visited:
                visited.append(target_node)
        # Commit the chosen scenario (slot picked by WM in two-step UI)
        committed_node = target_node or state["campaign"].get("current_node")
        scenario_slot = wm_sub.get("scenario_slot", 0)
        scenarios = state.get("beat_scenarios", {}).get(committed_node, [])
        if isinstance(scenarios, list) and scenarios:
            committed_scenario = scenarios[scenario_slot % len(scenarios)]
        elif isinstance(scenarios, dict):
            committed_scenario = scenarios  # legacy single-scenario fallback
        else:
            committed_scenario = {}
        state.setdefault("cascade", {})["committed_scenario"] = committed_scenario
    # Generate scenarios for the (possibly new) current node
    _generate_node_scenarios(state)
    _enter_cascade_phase(state, "cascade_HC")


# Node type → tags contributed when WM moves to that node type
_NODE_MOVE_TAGS: Dict[str, list] = {
    # Types present in the Jericho Reach graph
    "fortress_world":  ["aggressive", "terminus", "advance"],
    "hive_world":      ["urban", "aggressive", "dominance"],
    "agri_world":      ["reclaim", "resupply", "defensive"],
    "feral_world":     ["hunters", "elimination", "attrition"],
    "mining_world":    ["resupply", "fortify", "dominance"],
    "dead_world":      ["attrition", "fortify", "resilience"],
    "war_world":       ["aggressive", "attrition", "terminus"],
    "forge_world":     ["resupply", "dominance", "fortify"],
    "frontier_world":  ["advance", "hunters", "reclaim"],
    "penal_world":     ["attrition", "elimination", "reclaim"],
    "pleasure_world":  ["dominance", "intelligence", "reclaim"],
    "shrine_world":    ["defensive", "resilience", "reclaim"],
    "watch_station":   ["defensive", "fortify", "intelligence"],
    "special":         ["advance", "aggressive", "terminus"],
}


def _build_wm_movement_options(state: dict) -> dict:
    """Build dynamic WM cascade_WM options: hold current planet + advance to each adjacent node."""
    current_node = state.get("campaign", {}).get("current_node")
    opts: dict = {
        "_decision": "movement_order",
        "_description": (
            "The Watch Master sets the Watch's theatre — hold position or reposition to an "
            "adjacent world. This choice determines the scenario intelligence for all cascade "
            "tiers below."
        ),
    }
    hold_name = f"Hold Position — {current_node}" if current_node else "Hold Position"
    opts["hold"] = {
        "name": hold_name,
        "description": "Maintain current positioning. Doctrine is drawn from familiar ground.",
        "tags": ["defensive", "fortify", "resilience"],
        "target_node": None,
    }
    if current_node:
        graph = _load_graph()
        adjacent_ids: set = set()
        for edge in graph.get("edges", []):
            src, tgt = edge.get("source"), edge.get("target")
            if src == current_node:
                adjacent_ids.add(tgt)
            elif tgt == current_node:
                adjacent_ids.add(src)
        for nid in sorted(adjacent_ids):
            nd = _graph_node(nid)
            if not nd:
                continue
            ntype = nd.get("type", "")
            tags = _NODE_MOVE_TAGS.get(ntype, ["advance", "aggressive"])
            # key must be a valid identifier-like string
            safe_id = nid.lower().replace(" ", "_").replace("-", "_").replace("'", "")
            opts[f"move_to_{safe_id}"] = {
                "name": f"Advance to {nid}",
                "description": f"{nid} — {ntype.replace('_', ' ').title()}. Reposition the strike force.",
                "tags": tags[:2],
                "target_node": nid,
            }
    return opts


# ---------------------------------------------------------------------------
# Milestone progress
# ---------------------------------------------------------------------------


def _update_milestone_progress(
    state: dict,
    user_id: str,
    enlistment_record: dict,
    entry: dict,
    aar_record: dict,
):
    """Update personal milestone progress for a member based on a new campaign log entry."""
    _ensure_refs_loaded()
    milestones_data = _MILESTONES

    chapter = enlistment_record.get("chapter", "")
    milestone_progress = enlistment_record.setdefault("milestone_progress", {})

    # Get applicable milestones: universal + chapter-specific
    applicable = list(milestones_data.get("universal", []))
    for ca in milestones_data.get("chapter_affinity", []):
        if ca.get("chapter") == chapter or ca.get("chapter") == "all":
            applicable.extend(ca.get("milestones", []))

    for milestone in applicable:
        mid = milestone.get("id")
        if not mid:
            continue
        mp = milestone_progress.setdefault(mid, {
            "count": 0,
            "threshold": milestone.get("threshold", 0),
            "completed": False,
            "completed_at": None,
            "prestige_awarded": None,
        })
        if mp.get("completed"):
            continue

        # Evaluate tracking rule
        rule = milestone.get("tracking_rule", "")
        data_source = milestone.get("data_source", "")

        if "data_source" in milestone and data_source == "campaign_log":
            # terminus_killed type
            if "terminus_killed == true" in rule and entry.get("terminus_killed"):
                mp["count"] += 1
        elif data_source == "aar_record":
            # gene_seed_carrier, armory_data, op count, mission specialist
            if "gene_seed_carrier_id == member_id" in rule:
                if str(aar_record.get("gene_seed_carrier_id")) == user_id:
                    mp["count"] += 1
            elif "armory_data > 0" in rule:
                if aar_record.get("armory_data", 0) > 0 and user_id in [str(b) for b in aar_record.get("brother_ids", [])]:
                    mp["count"] += 1
            elif "member_id in brother_ids" in rule:
                if user_id in [str(b) for b in aar_record.get("brother_ids", [])]:
                    mp["count"] += 1

        # Check completion
        if not mp.get("completed") and mp["count"] >= mp["threshold"]:
            mp["completed"] = True
            mp["completed_at"] = _iso_now()
            prestige_reward = milestone.get("prestige_reward", 0)
            mp["prestige_awarded"] = prestige_reward
            # Award prestige to KT if applicable
            if prestige_reward and enlistment_record.get("kt_sgt_id"):
                kt = state.get("kill_teams", {}).get(enlistment_record["kt_sgt_id"])
                if kt:
                    kt.setdefault("prestige_log", []).append({
                        "earned_at": _iso_now(),
                        "member_id": user_id,
                        "base_amount": prestige_reward,
                        "multiplier": 1.0,
                        "credited_amount": prestige_reward,
                        "campaign_log_entry_id": entry.get("entry_id", ""),
                    })


# ---------------------------------------------------------------------------
# Auto de-enlist sweep
# ---------------------------------------------------------------------------


async def sweep_auto_de_enlist():
    """Warn members at 21 days inactivity; de-enlist at 28 days.

    Sends DMs via bot. Intended to run as a periodic task.
    """
    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase not in ("ops", "cascade_HC", "cascade_Company", "cascade_KT"):
        return

    bot = _b("bot")
    if not bot:
        return

    now = _utcnow()
    warn_threshold = timedelta(days=21)
    de_enlist_threshold = timedelta(days=28)
    changed = False

    for user_id, record in state.get("enlistment", {}).items():
        if not record.get("active"):
            continue
        last_aar_ts = _parse_iso(record.get("last_aar_timestamp"))
        # If never posted qualifying AAR, use enlisted_at as reference
        if not last_aar_ts:
            last_aar_ts = _parse_iso(record.get("enlisted_at")) or now

        inactive_duration = now - last_aar_ts

        if inactive_duration >= de_enlist_threshold:
            record["active"] = False
            changed = True
            try:
                user = await bot.fetch_user(int(user_id))
                await user.send(
                    "**Campaign De-enlistment Notice**\n"
                    "You have been automatically de-enlisted from the current campaign due to 28 days without a qualifying op (absolute, hard siege, omega, or hard stratagem).\n"
                    "Your milestone progress has been preserved. You may re-enlist at any time before campaign end."
                )
            except Exception:
                pass
        elif inactive_duration >= warn_threshold and not record.get("auto_de_enlist_warning_sent"):
            record["auto_de_enlist_warning_sent"] = True
            changed = True
            days_left = (de_enlist_threshold - inactive_duration).days
            try:
                user = await bot.fetch_user(int(user_id))
                await user.send(
                    f"**Campaign Activity Warning**\n"
                    f"You have been inactive in the campaign for {inactive_duration.days} days. "
                    f"If you do not complete a qualifying op (absolute, hard siege, omega, or hard stratagem) within {days_left} days, "
                    f"you will be automatically de-enlisted."
                )
            except Exception:
                pass

    if changed:
        _save_campaign_state(state)


# ---------------------------------------------------------------------------
# Cascade role mapping + beat lifecycle helpers
# ---------------------------------------------------------------------------

# Maps Discord role display name → cascade_options.json key
_ROLE_TO_CASCADE_KEY: Dict[str, str] = {
    "Watch Master": "watch_master",
    "Lord Executioner": "lord_executioner",
    "Forgemaster": "forgemaster",
    "Chief Apothecary": "chief_apothecary",
    "High Chaplain": "high_chaplain",
    "Huntmaster": "huntmaster",
    "Void Warden": "void_warden",
    "Castellan": "castellan",
    "Watch Captain": "watch_captain",
    "Watch Lieutenant": "watch_lieutenant",
    "Company Champion": "company_champion",
    "Watch Techmarine": "watch_techmarine",
    "Watch Apothecary": "watch_apothecary",
    "Watch Chaplain": "watch_chaplain",
    "Watch Librarian": "watch_librarian",
    "Watch Keeper": "watch_keeper",
    "Watch Sergeant": "watch_sergeant",
    "Judiciar": "judiciar",
    # Battle-line — personal focus phase
    "Oathsworn": "personal_focus",
    "Watch Veteran": "personal_focus",
    "Watch Brother": "personal_focus",
}

# Which cascade keys are eligible per phase
_CASCADE_PHASE_ROLES: Dict[str, frozenset] = {
    "cascade_WM": frozenset({"watch_master"}),
    "cascade_HC": frozenset({
        "lord_executioner", "forgemaster", "chief_apothecary",
        "high_chaplain", "huntmaster", "void_warden", "castellan",
    }),
    "cascade_Company": frozenset({
        "watch_captain", "watch_lieutenant", "company_champion", "watch_techmarine",
        "watch_apothecary", "watch_chaplain", "watch_librarian", "watch_keeper",
    }),
    "cascade_KT": frozenset({"watch_sergeant", "judiciar"}),
    "cascade_personal": frozenset({"personal_focus"}),
}

# Highest-authority order for role disambiguation
_CASCADE_ROLE_PRIORITY = [
    "watch_master", "lord_executioner", "forgemaster", "chief_apothecary",
    "high_chaplain", "huntmaster", "void_warden", "castellan",
    "watch_captain", "watch_lieutenant", "company_champion", "watch_techmarine",
    "watch_apothecary", "watch_chaplain", "watch_librarian", "watch_keeper",
    "watch_sergeant", "judiciar",
    "personal_focus",  # battle-line: Oathsworn, Watch Veteran, Watch Brother
]

# Cascade window durations per phase
_STRAT_POOL_SIZE = 12  # Target conflict-free pool size for beat resolution


def _get_user_cascade_role_key(user, phase: str) -> Optional[str]:
    """Return the user's highest-priority cascade role key, but only if that role
    is eligible for *phase*.

    We find the user's globally highest-priority cascade role first (across all
    phases). If that top role belongs to a different phase tier, they are not
    eligible here — even if they hold a secondary role that would qualify. A
    Forgemaster who also has Watch Techmarine should not receive Company cascade
    options; their mandate was submitted at HC tier.
    """
    if not hasattr(user, "roles"):
        return None
    user_role_names = {r.name for r in user.roles}
    # Find the single highest-priority cascade role the user holds (globally).
    # A key may map from multiple role names (e.g. "personal_focus" covers
    # Oathsworn, Watch Veteran, and Watch Brother), so check all names for it.
    top_key: Optional[str] = None
    for key in _CASCADE_ROLE_PRIORITY:
        role_names_for_key = {rn for rn, rk in _ROLE_TO_CASCADE_KEY.items() if rk == key}
        if role_names_for_key & user_role_names:
            top_key = key
            break
    if top_key is None:
        return None
    # Only return it if it is eligible for the requested phase
    if top_key in _CASCADE_PHASE_ROLES.get(phase, frozenset()):
        return top_key
    return None


_CASCADE_DEADLINE_DEFAULTS: Dict[str, int] = {
    "cascade_WM": 12,
    "cascade_HC": 48,
    "cascade_Company": 24,
    "cascade_KT": 24,
    "cascade_personal": 12,  # battle-line personal focus — shorter window
}


def _open_ops_window(state: dict) -> None:
    """Lock the strat pool and open the ops window for the current beat.

    Called when all cascade phases complete (cascade_personal resolves or is
    skipped). Idempotent: _lock_strat_pool is a no-op if already locked.
    """
    _lock_strat_pool(state)
    duration_days = state["campaign"].get("beat_duration_days") or 7
    ops_close = _utcnow() + timedelta(days=duration_days)
    state["ops_window"] = {
        "opened_at": _iso_now(),
        "closes_at": ops_close.isoformat(),
        "terminus_calls": [],
    }
    state["campaign"]["phase"] = "ops"


def _enter_cascade_phase(state: dict, phase: str) -> None:
    """Set the campaign phase to a cascade phase and record its deadline.
    Hours are read from config['cascade_deadline_hours']; falls back to _CASCADE_DEADLINE_DEFAULTS.

    For cascade_WM: if no Watch Master is enrolled, the phase is resolved immediately
    (hold position, generate scenarios, open cascade_HC) rather than blocking the whole
    cascade for the duration of the WM window.

    For cascade_personal: if no battle-line members are enrolled, skip directly to ops.
    """
    # Auto-skip cascade_WM if nobody eligible is enrolled
    if phase == "cascade_WM":
        eligible_keys = _CASCADE_PHASE_ROLES.get("cascade_WM", frozenset())
        wm_enrolled = any(
            _ROLE_TO_CASCADE_KEY.get(rec.get("role", "")) in eligible_keys
            for rec in state.get("enlistment", {}).values()
            if rec.get("active")
        )
        if not wm_enrolled:
            _resolve_cascade_WM(state)
            return

    # Auto-skip cascade_personal if no battle-line enrolled
    if phase == "cascade_personal":
        eligible_keys = _CASCADE_PHASE_ROLES.get("cascade_personal", frozenset())
        bl_enrolled = any(
            _ROLE_TO_CASCADE_KEY.get(rec.get("role", "")) in eligible_keys
            for rec in state.get("enlistment", {}).values()
            if rec.get("active")
        )
        if not bl_enrolled:
            _open_ops_window(state)
            return
        # Lock strat pool now so mandate is immediately visible during personal focus window
        _lock_strat_pool(state)

    CONFIG = _b("CONFIG")
    _hours_cfg = CONFIG.get("cascade_deadline_hours") if isinstance(CONFIG, dict) else None
    deadline_hours = (
        _hours_cfg.get(phase, _CASCADE_DEADLINE_DEFAULTS.get(phase, 48))
        if isinstance(_hours_cfg, dict)
        else _CASCADE_DEADLINE_DEFAULTS.get(phase, 48)
    )
    state["campaign"]["phase"] = phase
    cascade = state.setdefault("cascade", {})
    cascade.setdefault("submissions", {})
    now = _utcnow()
    deadline = now + timedelta(hours=deadline_hours)
    cascade[f"{phase}_started_at"] = now.isoformat()
    cascade[f"{phase}_deadline"] = deadline.isoformat()


def _aggregate_cascade_doctrine(
    state: dict,
    role_keys: Optional[frozenset] = None,
) -> Dict[str, float]:
    """Sum doctrine tags from cascade submissions into a doctrine aggregate.

    If *role_keys* is provided, only submissions whose ``role_key`` is in
    that set are counted (used for tiered mandate derivation).
    """
    aggregate: Dict[str, float] = {}
    for sub in state.get("cascade", {}).get("submissions", {}).values():
        if role_keys is not None and sub.get("role_key") not in role_keys:
            continue
        for tag in sub.get("tags", []):
            aggregate[tag] = aggregate.get(tag, 0.0) + 1.0
    return aggregate


def _build_conflict_free_pool(
    scored: List[Tuple[str, float, dict]], pool_size: int = _STRAT_POOL_SIZE
) -> List[str]:
    """Greedy conflict-free pool selection from a pre-scored strat list."""
    _ensure_refs_loaded()
    ref_strats = _load_ref("stratagems.json")
    cat_groups = ref_strats.get("conflict_map", {}).get("category_groups", {})
    specific = ref_strats.get("conflict_map", {}).get("specific_conflicts", {})

    all_conflicts: Dict[str, set] = {}
    for group in cat_groups.values():
        members = group.get("members", [])
        for a in members:
            for b in members:
                if a != b:
                    all_conflicts.setdefault(a, set()).add(b)
    for strat_name, info in specific.items():
        if strat_name.startswith("_"):
            continue
        for blocked in info.get("blocks", []):
            all_conflicts.setdefault(strat_name, set()).add(blocked)
            all_conflicts.setdefault(blocked, set()).add(strat_name)

    pool: List[str] = []
    for name, _score, _strat in scored:
        if len(pool) >= pool_size:
            break
        if not any(name in all_conflicts.get(p, set()) for p in pool):
            pool.append(name)
    return pool


def _lock_strat_pool(state: dict) -> None:
    """Build and lock the strat pool from current cascade submissions.

    Called when cascade_KT resolves and ops opens, so /campaign-mandate is
    available immediately when the ops window begins. Also called from
    _resolve_beat_and_open_next (which archives and advances the beat) to
    avoid rebuilding if already locked.
    """
    if state.get("strat_pool", {}).get("locked"):
        return  # already locked (e.g. called again at ops-close)

    _ensure_refs_loaded()

    wm_keys = _CASCADE_PHASE_ROLES["cascade_WM"]
    hc_keys = _CASCADE_PHASE_ROLES["cascade_HC"]
    co_keys = _CASCADE_PHASE_ROLES["cascade_Company"]
    kt_keys = _CASCADE_PHASE_ROLES["cascade_KT"]
    wm_hc_keys = wm_keys | hc_keys

    # Full aggregate drives the shared conflict-free strat pool
    full_aggregate = _aggregate_cascade_doctrine(state)
    scored = score_strats_against_aggregate(full_aggregate)
    pool = _build_conflict_free_pool(scored, pool_size=_STRAT_POOL_SIZE)

    # Tiered aggregates: each tier's mandate is shaped by submissions at or above it
    theatre_aggregate = _aggregate_cascade_doctrine(state, role_keys=wm_hc_keys)
    company_aggregate = _aggregate_cascade_doctrine(state, role_keys=wm_hc_keys | co_keys)
    # KT uses full_aggregate (all tiers contribute to ground-truth doctrine)

    # Mandate counts based on enrolled active roles (not just submitters)
    enlistment = state.get("enlistment", {})
    enrolled_keys: set = {
        rec.get("role", "").lower().replace(" ", "_")
        for rec in enlistment.values()
        if rec.get("active") and rec.get("role")
    }
    hc_distinct = len(enrolled_keys & wm_hc_keys)  # WM counts toward theatre
    co_distinct = len(enrolled_keys & co_keys)
    kt_distinct = len(enrolled_keys & kt_keys)
    tier_counts = {
        "theatre": _tier_mandate_count(hc_distinct, "HC"),
        "company": _tier_mandate_count(co_distinct, "Company"),
        "kt": _tier_mandate_count(kt_distinct, "KT"),
    }

    state_ref = refresh_prestige_cache(state)
    mandate_result = derive_strat_mandate(
        full_aggregate, pool, state_ref, tier_counts,
        theatre_aggregate=theatre_aggregate,
        company_aggregate=company_aggregate,
    )

    state["strat_pool"] = {
        "locked": True,
        "pool": pool,
        "theatre_mandate": mandate_result.get("theatre_mandate", []),
        "company_mandates": mandate_result.get("company_mandates", {}),
        "kt_mandates": mandate_result.get("kt_mandates", {}),
        "tier_counts": tier_counts,
        "derived_at": _iso_now(),
        "doctrine_aggregate": full_aggregate,
    }

    # Derive ops and terminus mandates and store alongside strat pool
    ops_mandate = _derive_ops_mandate(state)
    state["strat_pool"]["ops_mandate"] = ops_mandate
    terminus_directive = _derive_terminus_directive(state)
    state["strat_pool"]["terminus_directive"] = terminus_directive


def _resolve_beat_and_open_next(
    state: dict, ops_closes_at: Optional[str] = None
) -> dict:
    """Aggregate cascade doctrine → derive strat pool → advance beat → open next ops window.

    Modifies *state* in-place. Does NOT save to disk.
    Returns a summary dict suitable for announcement text.
    """
    _ensure_refs_loaded()

    campaign = state.get("campaign", {})
    old_beat = campaign.get("beat") or 0
    old_beat_name = campaign.get("beat_name")
    new_beat = old_beat + 1
    total_beats = campaign.get("total_beats") or 3

    # 1–5. Build and lock the strat pool (no-op if already locked from cascade_KT→ops)
    _lock_strat_pool(state)
    strat_pool = state["strat_pool"]
    doctrine_aggregate = strat_pool.get("doctrine_aggregate", {})
    pool = strat_pool.get("pool", [])
    mandate_result = {
        "theatre_mandate": strat_pool.get("theatre_mandate", []),
        "company_mandates": strat_pool.get("company_mandates", {}),
        "kt_mandates": strat_pool.get("kt_mandates", {}),
    }
    tier_counts = strat_pool.get("tier_counts", {})

    # 6. Archive the completed beat
    campaign.setdefault("beat_history", []).append({
        "beat": old_beat,
        "beat_name": old_beat_name,
        "resolved_at": _iso_now(),
        "doctrine_aggregate": doctrine_aggregate,
        "pool": pool,
        "theatre_mandate": mandate_result.get("theatre_mandate", []),
        "tier_counts": tier_counts,
    })

    # 7. Advance beat counter and generate new beat name
    beat_doctrine_tags = sorted(doctrine_aggregate.keys(), key=lambda k: -doctrine_aggregate[k])
    campaign["beat"] = new_beat
    new_beat_name = generate_beat_name(new_beat, beat_doctrine_tags[:3], seed=new_beat)
    campaign["beat_name"] = new_beat_name

    # 8. Clear cascade submissions
    state.setdefault("cascade", {})["submissions"] = {}

    theatre_strats = mandate_result.get("theatre_mandate", [])
    summary = {
        "new_beat": new_beat,
        "new_beat_name": new_beat_name,
        "theatre_mandate": theatre_strats[0] if theatre_strats else None,
        "theatre_mandates": theatre_strats,
        "top_tags": beat_doctrine_tags[:3],
        "tier_counts": tier_counts,
        "campaign_complete": new_beat > total_beats,
    }

    # 9. If campaign is over, close it; otherwise open cascade for the next beat
    if summary["campaign_complete"]:
        campaign["phase"] = "complete"
        campaign["ended_at"] = _iso_now()
        campaign["outcome"] = f"Campaign concluded after {total_beats} beats."
    else:
        _enter_cascade_phase(state, "cascade_WM")
        summary["cascade_WM_deadline"] = state["cascade"].get("cascade_WM_deadline")

    return summary


def _cascade_phase_ping(state: dict, phase: str) -> str:
    """Return a Discord role mention string for the given cascade phase.

    cascade_WM    → @Watch Master
    cascade_HC    → @High Command
    cascade_Company → all unique companyCommandRoleId values for active companies
    cascade_KT    → @Watch Sergeant
    """
    if phase == "cascade_WM":
        return f"<@&{WATCH_MASTER_ROLE_ID}>"
    if phase == "cascade_HC":
        return f"<@&{HIGH_COMMAND_ROLE_ID}>"
    if phase == "cascade_Company":
        CONFIG = _b("CONFIG") or {}
        cfg_companies = CONFIG.get("companies", {})
        active_cos = set(state.get("companies", {}).keys())
        role_ids: list[str] = []
        seen: set = set()
        for co_id, co_cfg in cfg_companies.items():
            if co_id in active_cos:
                rid = str(co_cfg.get("companyCommandRoleId", "") or "")
                if rid and rid not in seen:
                    role_ids.append(f"<@&{rid}>")
                    seen.add(rid)
        return " ".join(role_ids) if role_ids else ""
    if phase == "cascade_KT":
        return f"<@&{WATCH_SERGEANT_ROLE_ID}>"
    if phase == "cascade_personal":
        return f"<@&{WATCH_BROTHER_ROLE_ID}>"
    return ""


def _pending_cascade_members(state: dict, phase: str) -> list[str]:
    """Return list of user_id strings for active enrolled members in *phase* who haven't submitted."""
    eligible_keys = _CASCADE_PHASE_ROLES.get(phase, frozenset())
    submissions = state.get("cascade", {}).get("submissions", {})
    pending: list[str] = []
    for uid, rec in state.get("enlistment", {}).items():
        if not rec.get("active"):
            continue
        rk = _ROLE_TO_CASCADE_KEY.get(rec.get("role", ""))
        if rk and rk in eligible_keys:
            sub = submissions.get(uid)
            if not sub or sub.get("phase") != phase:
                pending.append(uid)
    return pending


async def _post_campaign_announcement(bot, text: str) -> None:
    """Post *text* to the configured campaign announcement channel, if set."""
    channel_id = CAMPAIGN_ANNOUNCEMENT_CHANNEL_ID
    if not channel_id:
        return
    try:
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(text)
    except Exception:
        pass


async def _maybe_send_cascade_warning(state: dict, phase: str, now, camp_name: str, beat) -> bool:
    """If the cascade deadline for *phase* is within the warning window and no warning has been
    sent yet, post a ping to the brothers who haven't submitted.

    Returns True if a warning was sent (so the caller knows to save state).
    Warning threshold is read from config['cascade_warning_minutes'] (default 15).
    """
    cascade = state.get("cascade", {})
    warning_key = f"{phase}_warning_sent"
    if cascade.get(warning_key):
        return False
    deadline = _parse_iso(cascade.get(f"{phase}_deadline"))
    if not deadline:
        return False
    CONFIG = _b("CONFIG") or {}
    warn_minutes = CONFIG.get("cascade_warning_minutes", 15) if isinstance(CONFIG, dict) else 15
    remaining = (deadline - now).total_seconds() / 60
    if remaining < 0 or remaining > warn_minutes:
        return False

    # Find pending members and build pings
    pending_ids = _pending_cascade_members(state, phase)
    if not pending_ids:
        return False

    pending_mentions = " ".join(f"<@{uid}>" for uid in pending_ids)
    deadline_fmt = _fmt_ts(deadline.isoformat()[:19])
    phase_labels = {
        "cascade_WM": "Watch Master",
        "cascade_HC": "High Command",
        "cascade_Company": "Company Command",
        "cascade_KT": "Kill Teams",
    }
    label = phase_labels.get(phase, phase)
    warning_text = (
        f"⏰ **{camp_name} — {label} cascade closing soon.**\n"
        f"{pending_mentions} — you have not yet submitted your orders for Cycle {beat}.\n"
        f"Use `/campaign-orders` now. Window closes: {deadline_fmt}"
    )

    cascade[warning_key] = True
    await _post_campaign_announcement(_b("bot"), warning_text)
    return True


async def sweep_campaign_beat_clock() -> None:
    """Auto-advance campaign beat lifecycle: ops window expiry and cascade deadlines.

    Called every 15 minutes by the beat clock loop in bot.py.
    Transitions: ops → cascade_WM → cascade_HC → cascade_Company → cascade_KT → ops (next beat).
    """
    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")

    if phase not in ("ops", "cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal"):
        return

    bot = _b("bot")
    if not bot:
        return

    now = _utcnow()
    changed = False
    announcement: Optional[str] = None

    camp_name = state["campaign"].get("name") or "Campaign"
    beat = state["campaign"].get("beat") or "?"

    # ops → beat resolution when the ops window closes
    if phase == "ops":
        closes_at = _parse_iso(state.get("ops_window", {}).get("closes_at"))
        if closes_at and now >= closes_at:
            summary = _resolve_beat_and_open_next(state)
            theatre_strats = summary.get("theatre_mandates") or []
            theatre_display = ", ".join(f"`{s}`" for s in theatre_strats) if theatre_strats else "—"
            top_tags = ", ".join(summary.get("top_tags", [])) or "—"
            if summary.get("campaign_complete"):
                total_b = state["campaign"].get("total_beats", 3)
                length_lbl = state["campaign"].get("length_label") or {3: "Short", 4: "Medium", 5: "Long"}.get(total_b, "")
                announcement = (
                    f"⚔️ **{camp_name} — Campaign Concluded.**\n"
                    f"The {length_lbl.lower()} campaign ({total_b} beats) is complete.\n"
                    f"Final Theatre Mandates: {theatre_display} | Dominant doctrine: {top_tags}"
                )
            else:
                wm_deadline = summary.get("cascade_WM_deadline") or ""
                hc_deadline = state["cascade"].get("cascade_HC_deadline") or ""
                current_node = state["campaign"].get("current_node") or "current position"
                if wm_deadline:
                    # cascade_WM is open (WM enrolled)
                    _wm_ping = _cascade_phase_ping(state, "cascade_WM")
                    announcement = (
                        f"{_wm_ping}\n"
                        f"⚔️ **{camp_name} — Cycle {beat} ops window closed.**\n"
                        f"The Watch Master must set the Watch's position. **Watch Master**, set your theatre order via `/campaign-orders`.\n"
                        f"WM positioning window closes: {_fmt_ts(wm_deadline[:19])}"
                    )
                else:
                    # No WM enrolled — cascade_WM was skipped; cascade_HC is now open
                    _hc_ping = _cascade_phase_ping(state, "cascade_HC")
                    announcement = (
                        f"{_hc_ping}\n"
                        f"⚔️ **{camp_name} — Cycle {beat} ops window closed.**\n"
                        f"No Watch Master enlisted — Watch holds at **{current_node}**. Scenario intelligence is live.\n"
                        f"**High Command**, submit your doctrine orders via `/campaign-orders`.\n"
                        f"HC cascade window closes: {_fmt_ts(hc_deadline[:19])}"
                    )
            changed = True

    # cascade_WM → cascade_HC when deadline expires (or auto if no WM enlisted)
    elif phase == "cascade_WM":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_WM_deadline"))
        if deadline and now >= deadline:
            _resolve_cascade_WM(state)
            deadline_ts = state["cascade"].get("cascade_HC_deadline", "")[:19]
            current_node = state["campaign"].get("current_node") or "current position"
            _hc_ping = _cascade_phase_ping(state, "cascade_HC")
            announcement = (
                f"{_hc_ping}\n"
                f"⚔️ **{camp_name} — Cycle {beat}: Watch Master positioning window closed.**\n"
                f"Warband holds at **{current_node}**. **High Command**, submit your doctrine orders via `/campaign-orders`.\n"
                f"HC cascade window closes: {_fmt_ts(deadline_ts)}"
            )
            changed = True
        else:
            # Closing-soon warning for cascade_WM
            changed = await _maybe_send_cascade_warning(state, phase, now, camp_name, beat) or changed
    elif phase == "cascade_HC":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_HC_deadline"))
        if deadline and now >= deadline:
            _enter_cascade_phase(state, "cascade_Company")
            deadline_ts = state["cascade"].get("cascade_Company_deadline", "")[:19]
            _co_ping = _cascade_phase_ping(state, "cascade_Company")
            announcement = (
                f"{_co_ping}\n"
                f"⚔️ **{camp_name} — Cycle {beat} orders advancing: Company Command.**\n"
                f"HC orders have been logged. **Captains and Company officers**, submit your orders via `/campaign-orders`.\n"
                f"Company cascade window closes: {_fmt_ts(deadline_ts)}"
            )
            changed = True
        else:
            changed = await _maybe_send_cascade_warning(state, phase, now, camp_name, beat) or changed

    # cascade_Company → cascade_KT on deadline
    elif phase == "cascade_Company":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_Company_deadline"))
        if deadline and now >= deadline:
            _enter_cascade_phase(state, "cascade_KT")
            deadline_ts = state["cascade"].get("cascade_KT_deadline", "")[:19]
            _kt_ping = _cascade_phase_ping(state, "cascade_KT")
            announcement = (
                f"{_kt_ping}\n"
                f"⚔️ **{camp_name} — Cycle {beat} orders advancing: Kill Teams.**\n"
                f"Company orders logged. **Watch Sergeants**, submit your kill team doctrine via `/campaign-orders`.\n"
                f"KT cascade window closes: {_fmt_ts(deadline_ts)}"
            )
            changed = True
        else:
            changed = await _maybe_send_cascade_warning(state, phase, now, camp_name, beat) or changed

    # cascade_KT → enter personal focus or skip to ops
    elif phase == "cascade_KT":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_KT_deadline"))
        if deadline and now >= deadline:
            _enter_cascade_phase(state, "cascade_personal")
            if state["campaign"]["phase"] == "ops":
                # No battle-line enrolled — ops opened directly
                ops_close_ts = state["ops_window"]["closes_at"]
                _wb_ping = f"<@&{WATCH_BROTHER_ROLE_ID}>"
                announcement = (
                    f"{_wb_ping}\n"
                    f"⚔️ **{camp_name} — Cycle {beat} orders resolved. Operations window open.**\n"
                    f"Strat mandates are locked. **All Brothers**, get your ops in via `/campaign-log`.\n"
                    f"Ops window closes: {_fmt_ts(ops_close_ts)}"
                )
            else:
                # cascade_personal opened
                deadline_ts = state["cascade"].get("cascade_personal_deadline", "")
                _bl_ping = _cascade_phase_ping(state, "cascade_personal")
                announcement = (
                    f"{_bl_ping}\n"
                    f"⚔️ **{camp_name} — Cycle {beat} Kill Team doctrine locked. Personal focus cascade open.**\n"
                    f"**Battle-line**, choose your personal focus via `/campaign-orders`.\n"
                    f"Personal focus closes: {_fmt_ts(deadline_ts)}"
                )
            changed = True
        else:
            changed = await _maybe_send_cascade_warning(state, phase, now, camp_name, beat) or changed

    # cascade_personal → open ops window
    elif phase == "cascade_personal":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_personal_deadline"))
        if deadline and now >= deadline:
            _open_ops_window(state)
            ops_close_ts = state["ops_window"]["closes_at"]
            _wb_ping = f"<@&{WATCH_BROTHER_ROLE_ID}>"
            announcement = (
                f"{_wb_ping}\n"
                f"⚔️ **{camp_name} — Cycle {beat} orders resolved. Operations window open.**\n"
                f"Strat mandates are locked. **All Brothers**, get your ops in via `/campaign-log`.\n"
                f"Ops window closes: {_fmt_ts(ops_close_ts)}"
            )
            changed = True
        else:
            changed = await _maybe_send_cascade_warning(state, phase, now, camp_name, beat) or changed

    if changed:
        _save_campaign_state(state)
        if announcement:
            await _post_campaign_announcement(bot, announcement)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def _b_check_command_permission(user, cmd_name):
    fn = _b("check_command_permission")
    if fn:
        return fn(user, cmd_name)
    return True


def _b_is_allowed_channel(interaction):
    fn = _b("is_allowed_channel")
    if fn:
        return fn(interaction)
    return True


# --- /campaign-enlist ---

@_g.bot.tree.command(
    name="campaign-enlist",
    description="Enlist yourself in the current campaign.",
)
async def _campaign_enlist(
    interaction: discord.Interaction,
):
    if not _b_check_command_permission(interaction.user, "campaign-enlist"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    # Determine tier from Discord roles
    user_roles = {r.name for r in getattr(interaction.user, "roles", [])}
    HC_ROLES = {
        "Watch Master", "Lord Executioner", "Forgemaster", "Chief Apothecary",
        "High Chaplain", "Huntmaster", "Void Warden", "Castellan", "Venerable Dreadnought",
    }
    COMPANY_ROLES = {
        "Watch Captain", "Watch Lieutenant", "Company Champion", "Watch Techmarine",
        "Watch Apothecary", "Watch Chaplain", "Watch Librarian", "Watch Keeper",
        "Honored Dreadnought",
    }
    KT_ROLES = {
        "Watch Sergeant", "Kill Team Champion", "Judiciar", "Oathsworn",
        "Watch Veteran", "Watch Brother",
    }

    tier = "KT"
    role_name = ""
    if user_roles & HC_ROLES:
        tier = "HC"
        role_name = (user_roles & HC_ROLES).pop()
    elif user_roles & COMPANY_ROLES:
        tier = "Company"
        role_name = (user_roles & COMPANY_ROLES).pop()
    else:
        tier = "KT"
        role_name = (user_roles & KT_ROLES).pop() if user_roles & KT_ROLES else "Watch Brother"

    # Resolve chapter from Discord roles
    home_chapters = _b("HOME_CHAPTERS") or []
    chapter = next((hc for hc in home_chapters if hc in user_roles), "")
    if not chapter:
        await interaction.response.send_message(
            "Could not resolve your chapter — make sure you have a chapter role assigned before enlisting.",
            ephemeral=True,
        )
        return

    # Resolve company from Discord roles (e.g. "Primus Company", "Secundus", etc.)
    valid_companies = ("primus", "secundus", "tertius", "quartus", "quintus")
    company_id = None
    for rname in user_roles:
        rl = rname.lower()
        for cn in valid_companies:
            if cn in rl:
                company_id = cn
                break
        if company_id:
            break

    if tier == "Company" and not company_id:
        await interaction.response.send_message(
            "Could not resolve your company assignment — make sure you have a company role (Primus/Secundus/etc.) before enlisting.",
            ephemeral=True,
        )
        return

    # Resolve KT sergeant ID from Discord roles
    kt_sgt_id = None
    if tier == "KT":
        if role_name == "Watch Sergeant":
            kt_sgt_id = str(interaction.user.id)
        else:
            # Find the Kill Team role the user belongs to
            kt_role_name = None
            for rname in user_roles:
                rl = rname.lower()
                if "kill" in rl and "team" in rl and "champion" not in rl:
                    kt_role_name = rname
                    break
            if kt_role_name and interaction.guild:
                # Find a Watch Sergeant in the same Kill Team
                for m in interaction.guild.members:
                    m_roles = {r.name for r in getattr(m, "roles", [])}
                    if kt_role_name in m_roles and "Watch Sergeant" in m_roles:
                        kt_sgt_id = str(m.id)
                        break
            if not kt_sgt_id:
                await interaction.response.send_message(
                    "Could not resolve your Kill Team Sergeant — make sure you share a Kill Team role with your Sergeant.",
                    ephemeral=True,
                )
                return

    success, msg = enlist_member(
        user_id=str(interaction.user.id),
        discord_name=str(interaction.user),
        chapter=chapter,
        company_id=company_id or "",
        tier=tier,
        role=role_name,
        kt_sgt_id=kt_sgt_id,
    )
    await interaction.response.send_message(msg, ephemeral=True)


# --- /campaign-de-enlist ---

@_g.bot.tree.command(
    name="campaign-de-enlist",
    description="Voluntarily de-enlist from the current campaign.",
)
async def _campaign_de_enlist(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-de-enlist"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    success, msg = de_enlist_member(str(interaction.user.id))
    await interaction.response.send_message(msg, ephemeral=True)


# --- /campaign-log ---

# All non-excluded stratagem names (sorted, for autocomplete)
_ALL_STRAT_NAMES: list[str] = [
    "Aggravated Assault", "Astra Militarum", "Atrophy", "Avenger", "Backup Plan",
    "Battlefield Instincts", "Beset", "Bleary Sniper", "Broken Bulwark", "Buffed Enemies",
    "Butcher's Gifts", "Camaraderie", "Close In", "Combat Mastery", "Come Prepared",
    "Coordinated Calls", "Corrupted Relic", "Deep Pockets", "Detonation Risk",
    "Doomed Offensive", "Effective Taunt", "Enduring Foes", "Enemy Sighted",
    "Extreme Challenge", "Fallen Vanguard", "Fatality", "Great Responsibility",
    "Hallowed Relic", "Hardened Skins", "Harvest of Vitae", "Heavy Burden",
    "Heavy Calibre", "Hyperopia", "Imperial Fervour", "Intelligence Lapse",
    "Killer Instinct", "Larraman Cells", "Maintain Distance", "Major Challenge",
    "Measured Mercy", "Meat for the Slaughter", "Microreactor Breach", "Migraine",
    "Myopia", "No Delays", "Point Blank", "Pointed Attack", "Rationing",
    "Reinforced Cranium", "Rhythm of Carnage", "Scavenger", "Sharpshooter",
    "Shockwave Plating", "Spoils of War", "Strike Out", "Summoner",
    "Supremacy of the Strong", "Surgical Strike", "Surplus", "Tactical Weakness",
    "Technological Revolution", "Temporal Boosts", "The Emperor Protects", "Tsunami",
    "Twice the Foe", "Unleashed Fury", "Unshaken", "We Stand as One", "You Only Live Once",
]

# All terminus types (roaming + boss), for autocomplete
_ALL_TERMINUS_NAMES: list[str] = [
    "Carnifex", "Helbrute", "Hierophant Bio-Titan",
    "Mutalith Vortex Beast", "Neurothrope", "Trygon", "Tyranid Prime",
]


async def _strat_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=s, value=s)
        for s in _ALL_STRAT_NAMES
        if current.lower() in s.lower()
    ][:25]


async def _terminus_type_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=t, value=t)
        for t in _ALL_TERMINUS_NAMES
        if current.lower() in t.lower()
    ][:25]


@_g.bot.tree.command(
    name="campaign-log",
    description="Submit a campaign log entry for an op you completed.",
)
@app_commands.describe(
    aar_link="Discord message URL of the AAR post for this op.",
    terminus_type="Terminus target killed (leave blank if none).",
    terminus_count="How many of that terminus were slain (default 1).",
    strat_1="First active stratagem you ran.",
    strat_2="Second active stratagem (optional).",
    strat_3="Third active stratagem (optional).",
    strat_4="Fourth active stratagem (optional).",
)
@app_commands.autocomplete(
    terminus_type=_terminus_type_autocomplete,
    strat_1=_strat_autocomplete,
    strat_2=_strat_autocomplete,
    strat_3=_strat_autocomplete,
    strat_4=_strat_autocomplete,
)
async def _campaign_log(
    interaction: discord.Interaction,
    aar_link: str,
    terminus_type: Optional[str] = None,
    terminus_count: Optional[int] = None,
    strat_1: Optional[str] = None,
    strat_2: Optional[str] = None,
    strat_3: Optional[str] = None,
    strat_4: Optional[str] = None,
):
    if not _b_check_command_permission(interaction.user, "campaign-log"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    terminus_slain: Optional[Dict[str, int]] = None
    if terminus_type:
        count = max(1, terminus_count or 1)
        terminus_slain = {terminus_type: count}

    strats_list: List[str] = [s for s in [strat_1, strat_2, strat_3, strat_4] if s]
    success, msg, entry = log_campaign_entry(
        user_id=str(interaction.user.id),
        aar_link=aar_link,
        terminus_slain=terminus_slain,
        strats_active=strats_list,
    )
    await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Cascade choice UI (buttons on /campaign-orders)
# ---------------------------------------------------------------------------


class _CascadeButton(discord.ui.Button):
    """A button representing one cascade doctrine choice."""

    def __init__(self, opt_key: str, opt_val: dict, role_key: str, phase: str, owner_id: str):
        super().__init__(label=opt_val.get("name", opt_key)[:80], style=discord.ButtonStyle.primary)
        self._opt_key = opt_key
        self._opt_val = opt_val
        self._role_key = role_key
        self._phase = phase
        self._owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._owner_id:
            await interaction.response.send_message("These orders are not yours.", ephemeral=True)
            return
        _ensure_refs_loaded()
        state = _load_campaign_state()
        phase = state.get("campaign", {}).get("phase", "inactive")
        if phase != self._phase:
            await interaction.response.edit_message(
                content=f"The cascade phase has advanced ({phase}). Run `/campaign-orders` again.",
                embed=None,
                view=None,
            )
            return
        tags = self._opt_val.get("tags", [])
        opt_name = self._opt_val.get("name", self._opt_key)
        role_data = _CASCADE_OPTIONS.get(self._role_key, {})
        # For cascade_WM movement options, _decision lives in the dynamic opts dict
        decision_key = role_data.get("_decision") or self._opt_val.get("_decision", self._role_key)

        # cascade_WM is two-step: first pick node, then pick scenario
        if self._phase == "cascade_WM":
            target_node = self._opt_val.get("target_node")
            committed_node = target_node or state["campaign"].get("current_node")
            scenarios = state.get("beat_scenarios", {}).get(committed_node, [])
            if not isinstance(scenarios, list):
                scenarios = [scenarios] if scenarios else []
            if not scenarios:
                await interaction.response.edit_message(
                    content="No scenarios available for this node. Contact a Marshal.",
                    embed=None, view=None,
                )
                return
            scenario_view = _WMScenarioView(
                owner_id=self._owner_id,
                target_node=target_node,
                node_name=committed_node or "Unknown",
                movement_name=opt_name,
                movement_tags=tags,
                scenarios=scenarios,
                phase=self._phase,
                decision_key=decision_key,
            )
            node_label = f"**Advance to {committed_node}**" if target_node else f"**Hold at {committed_node}**"
            await interaction.response.edit_message(
                content=f"{node_label} — Choose your doctrine angle for this cycle:",
                embed=None,
                view=scenario_view,
            )
            return
        cascade = state.setdefault("cascade", {})
        cascade.setdefault("submissions", {})
        cascade["submissions"][self._owner_id] = {
            "role_key": self._role_key,
            "phase": phase,
            "decision": decision_key,
            "choice_key": self._opt_key,
            "choice_name": opt_name,
            "tags": tags,
            "target_node": self._opt_val.get("target_node"),  # WM movement
            "submitted_at": _iso_now(),
        }
        _save_campaign_state(state)
        # Early advance: if all eligible members in this phase have now submitted, move on
        await _try_early_cascade_advance(state, self._phase)
        beat = state["campaign"].get("beat") or "?"
        await interaction.response.edit_message(
            content=f"\u2705 **{opt_name}** submitted for Cycle {beat}.",
            embed=None,
            view=None,
        )


def _all_phase_members_submitted(state: dict, phase: str) -> bool:
    """Return True if every active enrolled member eligible for *phase* has submitted."""
    eligible_keys = _CASCADE_PHASE_ROLES.get(phase, frozenset())
    submissions = state.get("cascade", {}).get("submissions", {})
    for uid, rec in state.get("enlistment", {}).items():
        if not rec.get("active"):
            continue
        rk = _ROLE_TO_CASCADE_KEY.get(rec.get("role", ""))
        if rk and rk in eligible_keys:
            sub = submissions.get(uid)
            if not sub or sub.get("phase") != phase:
                return False
    return True


async def _try_early_cascade_advance(state: dict, phase: str) -> bool:
    """If all eligible enrolled members have submitted for *phase*, advance immediately.

    Modifies *state* in-place, saves, and posts announcement. Returns True if advanced.
    """
    if not _all_phase_members_submitted(state, phase):
        return False

    camp_name = state["campaign"].get("name") or "The campaign"
    beat = state["campaign"].get("beat") or "?"

    _ADVANCE_MAP = {
        "cascade_HC": "cascade_Company",
        "cascade_Company": "cascade_KT",
    }

    if phase == "cascade_WM":
        # Apply movement, generate scenarios, open cascade_HC
        _resolve_cascade_WM(state)
        deadline_ts = state["cascade"].get("cascade_HC_deadline", "")
        current_node = state["campaign"].get("current_node") or "current position"
        _hc_ping = _cascade_phase_ping(state, "cascade_HC")
        announcement = (
            f"{_hc_ping}\n"
            f"⚔️ **{camp_name} — Cycle {beat}: Watch Master has set the theatre.**\n"
            f"Warband positioned at **{current_node}**. Scenario intelligence is now live.\n"
            f"**High Command**, submit your doctrine orders via `/campaign-orders`.\n"
            f"HC cascade window closes: {_fmt_ts(deadline_ts)}"
        )
    elif phase in _ADVANCE_MAP:
        next_phase = _ADVANCE_MAP[phase]
        _enter_cascade_phase(state, next_phase)
        deadline_ts = state["cascade"].get(f"{next_phase}_deadline", "")
        phase_labels = {"cascade_Company": "Company Command", "cascade_KT": "Kill Teams"}
        next_label = phase_labels.get(next_phase, next_phase)
        cmd_mention = "/campaign-orders"
        _next_ping = _cascade_phase_ping(state, next_phase)
        announcement = (
            f"{_next_ping}\n"
            f"⚔️ **{camp_name} — Cycle {beat} orders advancing early: {next_label}.**\n"
            f"All {phase.replace('cascade_', '').upper()} orders are in. "
            f"**{next_label}**, submit your orders via `{cmd_mention}`.\n"
            f"{next_phase.replace('cascade_', '').upper()} cascade closes: {_fmt_ts(deadline_ts)}"
        )
    elif phase == "cascade_KT":
        # All KT submitted — enter personal focus or skip to ops
        _enter_cascade_phase(state, "cascade_personal")
        if state["campaign"]["phase"] == "ops":
            # No battle-line enrolled — ops opened directly
            ops_close_ts = state["ops_window"]["closes_at"]
            _wb_ping = f"<@&{WATCH_BROTHER_ROLE_ID}>"
            announcement = (
                f"{_wb_ping}\n"
                f"⚔️ **{camp_name} — Cycle {beat} orders resolved early. Operations window open.**\n"
                f"All Kill Team orders are in. Strat mandates are locked. "
                f"**All Brothers**, get your ops in via `/campaign-log`.\n"
                f"Ops window closes: {_fmt_ts(ops_close_ts)}"
            )
        else:
            # cascade_personal opened
            deadline_ts = state["cascade"].get("cascade_personal_deadline", "")
            _bl_ping = _cascade_phase_ping(state, "cascade_personal")
            announcement = (
                f"{_bl_ping}\n"
                f"⚔️ **{camp_name} — Cycle {beat} Kill Team doctrine locked early. Personal focus cascade open.**\n"
                f"All Kill Team orders are in. **Battle-line**, choose your personal focus via `/campaign-orders`.\n"
                f"Personal focus closes: {_fmt_ts(deadline_ts)}"
            )
    elif phase == "cascade_personal":
        # All battle-line submitted — open ops
        _open_ops_window(state)
        ops_close_ts = state["ops_window"]["closes_at"]
        _wb_ping = f"<@&{WATCH_BROTHER_ROLE_ID}>"
        announcement = (
            f"{_wb_ping}\n"
            f"⚔️ **{camp_name} — Cycle {beat} orders resolved early. Operations window open.**\n"
            f"All battle-line focus submitted. Strat mandates are locked. "
            f"**All Brothers**, get your ops in via `/campaign-log`.\n"
            f"Ops window closes: {_fmt_ts(ops_close_ts)}"
        )
    else:
        return False

    _save_campaign_state(state)
    await _post_campaign_announcement(_b("bot"), announcement)
    return True


def _select_cascade_options(
    state: dict,
    user_record: dict,
    role_key: str,
    max_options: int = 4,
) -> dict:
    """Return a scored, filtered subset of cascade options to present to this player.

    Scoring per option:
      base                   = option.get("weight", 1.0)
      +1.5 per scenario dominant_tag matching option["tags"]
      +1.0 if player's home chapter in option["chapter_affinity"]
      +1.0 per matching node_affinity type (current node type)
      +0.5 per upstream cascade tag matching option["requires_upstream_tags"]
      Hard suppress: suppress_if_previous=True AND player chose this key last beat → score 0
      Soft gate: requires_upstream_tags non-empty AND gate not met → score * 0.5
                 (gate entirely skipped if fewer than 2 ungated options would remain)

    Returns top max_options entries as a dict {opt_key: opt_val}.
    Falls back to the full role pool if the filtered set is empty.
    """
    _ensure_refs_loaded()
    pool: dict = _CASCADE_OPTIONS.get(role_key, {})

    # --- context gathering -------------------------------------------------------
    campaign = state.get("campaign", {})
    current_node = campaign.get("current_node", "")

    node_data = _graph_node(current_node) if current_node else {}
    node_type: str = (node_data or {}).get("type", "")

    chapter: str = user_record.get("chapter", "")

    # Scenario tags: use committed_scenario if WM has already picked one,
    # otherwise fall back to slot 0 of the current node's scenarios
    cascade_block = state.get("cascade", {})
    committed_scenario = cascade_block.get("committed_scenario")
    if not committed_scenario:
        raw = state.get("beat_scenarios", {}).get(current_node)
        if isinstance(raw, list):
            committed_scenario = raw[0] if raw else {}
        elif isinstance(raw, dict):
            committed_scenario = raw
        else:
            committed_scenario = {}
    scenario_tags: set = set((committed_scenario or {}).get("dominant_tags", []))

    submissions = cascade_block.get("submissions", {})
    upstream_tags: set = set()
    for sub in submissions.values():
        upstream_tags.update(sub.get("tags", []))

    user_id_str = str(user_record.get("discord_id", ""))
    prev_choices = cascade_block.get("previous_choices", {})
    prev_key: Optional[str] = prev_choices.get(user_id_str, {}).get(role_key)

    # --- score each option -------------------------------------------------------
    scored: list[tuple[float, str, dict]] = []  # (score, opt_key, opt_val)
    for opt_key, opt_val in pool.items():
        if opt_key.startswith("_"):
            continue

        # hard suppress: player chose this exact option last beat
        if opt_val.get("suppress_if_previous") and opt_key == prev_key:
            continue

        score = float(opt_val.get("weight", 1.0))

        # scenario dominant tags
        for tag in opt_val.get("tags", []):
            if tag in scenario_tags:
                score += 1.5

        # chapter affinity
        if chapter and chapter in opt_val.get("chapter_affinity", []):
            score += 1.0

        # node affinity
        if node_type and node_type in opt_val.get("node_affinity", []):
            score += 1.0

        # upstream tag bonus / soft gate
        req_tags = opt_val.get("requires_upstream_tags", [])
        if req_tags:
            matched = [t for t in req_tags if t in upstream_tags]
            if matched:
                score += 0.5 * len(matched)
            # soft gate applied after we know total pool; mark for second pass
            scored.append((score, opt_key, opt_val, bool(matched), req_tags))
            continue

        scored.append((score, opt_key, opt_val, True, []))

    # resolve soft gates: if fewer than 2 ungated options would remain, lift gates
    ungated = [t for t in scored if t[3]]
    if len(ungated) < 2:
        # lift all gates — keep everything with any score
        final_scored = [(s, k, v) for s, k, v, _met, _req in scored]
    else:
        # drop gated options that did not meet their upstream tag requirement
        final_scored = [(s, k, v) for s, k, v, met, req in scored if met or not req]

    if not final_scored:
        # fallback: return full pool (no filtering applied)
        return {k: v for k, v in pool.items() if not k.startswith("_")}

    final_scored.sort(key=lambda t: t[0], reverse=True)

    # Dynamic count: show 2-max_options based on score gaps.
    # A gap of >= 1.5 between consecutive options indicates a natural cut point —
    # the options below it are significantly less relevant than those above.
    # Always show at least 2; stop early when a cliff appears.
    _GAP_THRESHOLD = 1.5
    count = min(2, len(final_scored))  # floor
    for i in range(2, min(max_options, len(final_scored))):
        if final_scored[i - 1][0] - final_scored[i][0] >= _GAP_THRESHOLD:
            break  # significant drop — cut here
        count = i + 1
    else:
        count = min(max_options, len(final_scored))

    top = final_scored[:count]
    return {k: v for _, k, v in top}


class _CascadeChoiceView(discord.ui.View):
    """Ephemeral view that renders cascade doctrine buttons for /campaign-orders."""

    def __init__(self, user_id: str, role_key: str, phase: str, options_override: Optional[dict] = None):
        super().__init__(timeout=300)
        _ensure_refs_loaded()
        role_data = options_override if options_override is not None else _CASCADE_OPTIONS.get(role_key, {})
        for opt_key, opt_val in role_data.items():
            if opt_key.startswith("_"):
                continue
            self.add_item(_CascadeButton(opt_key, opt_val, role_key, phase, user_id))


class _WMScenarioButton(discord.ui.Button):
    """Second-step button: WM selects one of 3 scenario angles for a committed node."""

    def __init__(
        self,
        scenario: dict,
        owner_id: str,
        target_node: Optional[str],
        movement_name: str,
        movement_tags: list,
        phase: str,
        decision_key: str,
    ):
        slot = scenario.get("slot", 0)
        tags = scenario.get("dominant_tags", [])
        terminus = scenario.get("terminus_intel", "none")
        terminus_marker = " ⚠" if terminus == "known" else (" ~" if terminus == "suspected" else "")
        label = f"{scenario.get('codename', f'Vector {slot+1}')}  [{'/'.join(tags)}]{terminus_marker}"
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary)
        self._scenario = scenario
        self._owner_id = owner_id
        self._target_node = target_node
        self._movement_name = movement_name
        self._movement_tags = movement_tags
        self._phase = phase
        self._decision_key = decision_key

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._owner_id:
            await interaction.response.send_message("These orders are not yours.", ephemeral=True)
            return
        state = _load_campaign_state()
        phase = state.get("campaign", {}).get("phase", "inactive")
        if phase != self._phase:
            await interaction.response.edit_message(
                content=f"The cascade phase has advanced ({phase}). Run `/campaign-orders` again.",
                embed=None, view=None,
            )
            return
        tags = self._movement_tags + [t for t in self._scenario.get("dominant_tags", []) if t not in self._movement_tags]
        state.setdefault("cascade", {}).setdefault("submissions", {})[self._owner_id] = {
            "role_key": "watch_master",
            "phase": phase,
            "decision": self._decision_key,
            "choice_key": f"move_{self._scenario.get('node_id', 'unknown')}_s{self._scenario.get('slot', 0)}",
            "choice_name": self._movement_name,
            "tags": tags[:4],
            "target_node": self._target_node,
            "scenario_slot": self._scenario.get("slot", 0),
            "submitted_at": _iso_now(),
        }
        _save_campaign_state(state)
        await _try_early_cascade_advance(state, self._phase)
        beat = state["campaign"].get("beat") or "?"
        codename = self._scenario.get("codename", "—")
        await interaction.response.edit_message(
            content=f"✅ **{self._movement_name}** — *{codename}* submitted for Cycle {beat}.",
            embed=None, view=None,
        )


class _WMScenarioView(discord.ui.View):
    """Second-step view: WM picks one of 3 scenario angles after choosing a node."""

    def __init__(
        self,
        owner_id: str,
        target_node: Optional[str],
        node_name: str,
        movement_name: str,
        movement_tags: list,
        scenarios: list,
        phase: str,
        decision_key: str,
    ):
        super().__init__(timeout=300)
        for sc in scenarios[:3]:
            self.add_item(_WMScenarioButton(
                scenario=sc,
                owner_id=owner_id,
                target_node=target_node,
                movement_name=movement_name,
                movement_tags=movement_tags,
                phase=phase,
                decision_key=decision_key,
            ))


# Maps _decision key → readable in-universe phrase used in orders narrative
_DECISION_PHRASES: Dict[str, str] = {
    "movement_order": "theatre positioning order",
    "theatre_order": "theatre posture",
    "execution": "execution priority",
    "company_order": "company mandate",
    "directive": "field directive",
    "doctrine": "chapter doctrine",
    "sacred_directive": "sacred directive",
    "sacred_mission": "sacred mission",
    "medical_authority": "medicae authority",
    "apothecary_purpose": "Apothecarion purpose",
    "spiritual_decree": "spiritual decree",
    "litany": "battle litany",
    "hunt_priority": "hunt priority",
    "psychic_decree": "psychic decree",
    "psychic_angle": "angle of psychic engagement",
    "strategic_intelligence": "strategic intelligence assessment",
    "wardens_watch": "warden's watch designation",
    "sentence": "sentence upon the enemy",
    "ancients_will": "ancient's will",
    "personal_focus": "personal focus",
}

# 5 opening lines — rotated by beat number so each beat has a consistent but distinct opener
_ORDERS_OPENINGS = [
    "Your orders arrive.",
    "The cascade has reached you.",
    "The chain of command delivers its word.",
    "The cascade opens. Speak.",
    "Mandate cut and sealed.",
]


def _compose_orders_narrative(
    state: dict,
    phase: str,
    tier: str,
    role: str,
    beat_name: str,
) -> str:
    """Build contextual narrative for /campaign-orders based on campaign state.

    Pulls upstream submission text, campaign context, tag doctrine vocabulary,
    and the user's own role description to compose an in-universe situation report.
    """
    _ensure_refs_loaded()
    cascade = state.get("cascade", {})
    submissions = cascade.get("submissions", {})
    enlistment = state.get("enlistment", {})
    campaign = state.get("campaign", {})
    beat = campaign.get("beat") or "?"
    campaign_name = campaign.get("name") or "The Campaign"
    tag_vocab: Dict[str, str] = _CASCADE_OPTIONS.get("_tag_vocabulary", {})

    phase_order = ["cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal"]
    # Determine user's cascade phase from their role_key (handles cascade_WM correctly)
    rk_self_pre = _ROLE_TO_CASCADE_KEY.get(role, "")
    user_phase: Optional[str] = None
    for cp in phase_order:
        if rk_self_pre and rk_self_pre in _CASCADE_PHASE_ROLES.get(cp, frozenset()):
            user_phase = cp
            break

    # Beat-indexed opening
    try:
        beat_idx = int(beat)
    except (ValueError, TypeError):
        beat_idx = 0
    opener = _ORDERS_OPENINGS[beat_idx % len(_ORDERS_OPENINGS)]
    heading = f"**{campaign_name} — {beat_name}.** {opener}"

    # Collect upstream submissions visible to this user
    upstream: list[dict] = []
    for cp in phase_order:
        if cp == user_phase:
            break
        eligible = _CASCADE_PHASE_ROLES.get(cp, frozenset())
        for uid, rec in enlistment.items():
            rk = _ROLE_TO_CASCADE_KEY.get(rec.get("role", ""))
            if rk and rk in eligible and uid in submissions:
                sub = submissions[uid]
                if sub.get("phase") == cp:
                    # For cascade_WM movement submissions, description is in the submission itself
                    if cp == "cascade_WM":
                        desc = sub.get("choice_name", "?")
                        opt_data = {}
                    else:
                        opt_data = _CASCADE_OPTIONS.get(rk, {}).get(sub.get("choice_key", ""), {})
                        desc = opt_data.get("description", "")
                    upstream.append({
                        "role": rec.get("role", rk),
                        "choice_name": sub.get("choice_name", "?"),
                        "description": desc,
                        "tags": sub.get("tags", []),
                        "phase": cp,
                    })

    # Role metadata
    rk_self = rk_self_pre
    role_meta = _CASCADE_OPTIONS.get(rk_self, {})
    role_desc = role_meta.get("_description", "")
    role_decision_key = role_meta.get("_decision", "")
    # For cascade_WM the decision is movement, not doctrine — override
    if user_phase == "cascade_WM":
        role_decision_key = "movement_order"
        role_desc = (
            "The Watch Master sets the Watch's position for this cycle — hold in place or "
            "reposition to an adjacent world. This choice determines the scenario intelligence "
            "every brother below will face when they open their orders."
        )
    decision_phrase = _DECISION_PHRASES.get(
        role_decision_key,
        role_decision_key.replace("_", " ") if role_decision_key else "order",
    )
    role_desc_snip = role_desc.split(".")[0].strip() + "." if role_desc else ""

    # Aggregate doctrine tags from upstream
    tag_counts: Dict[str, int] = {}
    for u in upstream:
        for t in u["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts, key=lambda t: -tag_counts[t])[:3]
    top_tag_bold = ", ".join(f"**{t}**" for t in top_tags)

    def _doctrine_summary(tags: list[str]) -> str:
        """One-line doctrine direction from top tags using tag vocabulary."""
        if not tags:
            return ""
        snippets = []
        for t in tags[:2]:
            entry = tag_vocab.get(t, "")
            # Trim to the clause before a dash or period
            short = entry.split("\u2014")[0].split(".")[0].strip().rstrip(",").lower()
            if short:
                snippets.append(short)
        if not snippets:
            return ""
        if len(snippets) == 1:
            return snippets[0]
        return f"{snippets[0]}, with {snippets[1]}"

    doctrine_line = _doctrine_summary(top_tags)

    def _fmt_upstream_block(ups: list[dict]) -> str:
        """Format upstream submissions as a labelled briefing block."""
        lines = []
        for u in ups:
            tag_str = ", ".join(u["tags"][:3])
            line = f"▸ **{u['role']}** — {u['choice_name']}"
            if tag_str:
                line += f" `[{tag_str}]`"
            lines.append(line)
        return "\n".join(lines)

    # --- Branch: ops phase ---
    if phase == "ops":
        all_tags: list[str] = []
        for sub in submissions.values():
            all_tags.extend(sub.get("tags", []))
        ops_counts: Dict[str, int] = {}
        for t in all_tags:
            ops_counts[t] = ops_counts.get(t, 0) + 1
        ops_top = sorted(ops_counts, key=lambda t: -ops_counts[t])[:3]
        ops_tag_bold = ", ".join(f"**{t}**" for t in ops_top) if ops_top else "mixed doctrine"
        ops_doctrine = _doctrine_summary(ops_top)
        doctrine_desc = f" — {ops_doctrine}" if ops_doctrine else ""
        return (
            f"{heading}\n\n"
            f"The cascade is sealed. Doctrine for **{beat_name}**: {ops_tag_bold}{doctrine_desc}. "
            f"The ops window is open. Your orders flow from the decisions above — take the fight to the enemy."
        )

    # --- Branch: cascade_personal phase ---
    if phase == "cascade_personal":
        user_rk = _ROLE_TO_CASCADE_KEY.get(role, "")
        is_battle_line = user_rk == "personal_focus"
        if is_battle_line:
            upstream_section = (
                f"**Orders received from above:**\n{_fmt_upstream_block(upstream)}\n\n"
                f"Combined cascade doctrine: {top_tag_bold} — {doctrine_line}.\n\n"
                if upstream and doctrine_line
                else (
                    f"**Orders received from above:**\n{_fmt_upstream_block(upstream)}\n\n"
                    if upstream else ""
                )
            )
            return (
                f"{heading}\n\n"
                f"{upstream_section}"
                f"Kill Team doctrine is set. Now choose your **personal focus** for **{beat_name}**. "
                f"Where do you direct your oath? Select below."
            )
        else:
            # Spectator: not eligible for personal focus (HC/Company/KT tier)
            return (
                f"{heading}\n\n"
                f"Kill Team doctrine is locked. Battle-line are choosing their personal focus for **{beat_name}**. "
                f"Your orders are set — the cascade is in its final stage."
            )

    if not user_phase:
        return f"**{campaign_name}** is in **{phase}** phase — {beat_name}."

    current_idx = phase_order.index(phase) if phase in phase_order else -1
    user_idx = phase_order.index(user_phase) if user_phase in phase_order else -1

    # --- Branch: already submitted (tier above current phase) ---
    if user_idx < current_idx:
        own_sub = next(
            (s for s in submissions.values()
             if s.get("phase") == user_phase
             and _ROLE_TO_CASCADE_KEY.get(role, "") == s.get("role_key")),
            None,
        )
        own_name = own_sub.get("choice_name", "your order") if own_sub else "your order"
        doc_note = f" The cascade is trending {top_tag_bold} — {doctrine_line}." if doctrine_line else ""
        return (
            f"{heading}\n\n"
            f"Your tier has spoken. **{own_name}** is committed to the cascade record and cannot be withdrawn.{doc_note} "
            f"The orders now flow to those below — your word stands."
        )

    # --- Branch: waiting (tier below current phase) ---
    if user_idx > current_idx:
        above_label = phase.replace("cascade_", "").upper()
        wait_line = (
            "Brief your kill team. When Company speaks, your orders will follow."
            if tier == "KT"
            else "Read the situation. Prepare your formation."
        )
        if upstream:
            upstream_section = (
                f"**What has filtered down:**\n{_fmt_upstream_block(upstream)}\n\n"
                f"Combined doctrine trends toward {top_tag_bold} — {doctrine_line}.\n\n"
                if doctrine_line
                else f"**What has filtered down:**\n{_fmt_upstream_block(upstream)}\n\n"
            )
        else:
            upstream_section = "No orders have filtered down yet.\n\n"
        return (
            f"{heading}\n\n"
            f"**{above_label}** is deliberating for **{beat_name}**. Your window has not yet opened.\n\n"
            f"{upstream_section}"
            f"{wait_line}"
        )

    # --- Branch: active — this tier's turn ---
    if upstream:
        upstream_section = (
            f"**Orders received from above:**\n{_fmt_upstream_block(upstream)}\n\n"
            f"Combined cascade doctrine: {top_tag_bold} — {doctrine_line}.\n\n"
            if doctrine_line
            else f"**Orders received from above:**\n{_fmt_upstream_block(upstream)}\n\n"
        )
    else:
        upstream_section = (
            "You are first in the cascade — no orders have filtered down. "
            "The initiative rests with you.\n\n"
        )
    mandate_line = (
        f"Your mandate as **{role}**: {role_desc_snip}\n\n" if role_desc_snip else ""
    )
    close = (
        f"Issue your **{decision_phrase}** — your choice enters the cascade record "
        f"and shapes the ops pool for every brother below. Choose deliberately."
    )
    return f"{heading}\n\n{upstream_section}{mandate_line}{close}"


def _cascade_peer_summary(state: dict, phase: str, enlistment: dict) -> str:
    """Return a short 'X of Y submitted' string for the given cascade phase."""
    eligible_keys = _CASCADE_PHASE_ROLES.get(phase, frozenset())
    # Collect all active enrolled members eligible for this phase (uid, role_name, rk)
    eligible_members: list[tuple[str, str, str]] = []
    for uid, rec in enlistment.items():
        if not rec.get("active"):
            continue
        role_name = rec.get("role", "")
        rk = _ROLE_TO_CASCADE_KEY.get(role_name)
        if rk and rk in eligible_keys:
            eligible_members.append((uid, role_name, rk))

    total = len(eligible_members)
    if total == 0:
        return "No eligible members enrolled."
    submissions = state.get("cascade", {}).get("submissions", {})
    submitted = sum(
        1 for uid, _, _ in eligible_members
        if uid in submissions and submissions[uid].get("phase") == phase
    )
    pending_roles = [
        role_name for uid, role_name, _ in eligible_members
        if uid not in submissions or submissions[uid].get("phase") != phase
    ]
    pending_str = ", ".join(pending_roles)
    if submitted == total:
        return f"\u2705 All {total} of {total} have submitted."
    return f"**{submitted}/{total} submitted.** Awaiting: {pending_str}"


_CASCADE_PHASE_NARRATIVE = {
    "cascade_HC": {
        "active": (
            "High Command speaks first. The strategic posture for this cycle is set from the top — "
            "your doctrine choice shapes what orders flow down to Company and Kill Team. "
            "Choose with the full weight of command behind you."
        ),
        "waiting_above": None,
        "waiting_as_company": (
            "High Command is deliberating. Company Command stands ready — "
            "your orders will be issued once the strategic posture is set from above. Stand by."
        ),
        "waiting_as_kt": (
            "High Command is deliberating. Your Sergeants will receive their cascade once "
            "Company Command relays the strategic posture downward. Prepare your kill teams."
        ),
        "done_hc": "High Command has spoken. The strategic posture is set.",
    },
    "cascade_Company": {
        "active": (
            "High Command has issued the strategic posture. Company Command now translates those orders "
            "into operational doctrine — your choice defines how your formation fights this beat. "
            "The Kill Teams await your word."
        ),
        "waiting_as_kt": (
            "Company Command is issuing operational doctrine. Your Sergeants will receive cascade orders "
            "once the formation posture is confirmed. Hold ready."
        ),
        "done_company": "Company Command has issued operational doctrine. Kill Teams stand by for cascade.",
    },
    "cascade_KT": {
        "active": (
            "The orders have cascaded all the way down. Kill Team Sergeants and Judiciars now lock in "
            "their tactical focus for the cycle — your choice commits your kill team's doctrine "
            "to the campaign record. The ops window opens when the cascade closes."
        ),
    },
    "ops": {
        "active": "The cascade is sealed. The ops window is open — take the fight to the enemy.",
    },
}


# --- /campaign-orders ---

@_g.bot.tree.command(
    name="campaign-orders",
    description="View your current orders for this beat.",
)
async def _campaign_orders(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-orders"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    state = _load_campaign_state()
    user_id = str(interaction.user.id)
    enlistment = state.get("enlistment", {})
    record = enlistment.get(user_id)
    if not record or not record.get("active"):
        await interaction.response.send_message("You are not currently enlisted in the campaign.", ephemeral=True)
        return

    campaign = state.get("campaign", {})
    phase = campaign.get("phase", "inactive")
    beat = campaign.get("beat")
    beat_name = campaign.get("beat_name") or f"Cycle {beat or '?'}"
    tier = record.get("tier", "KT")
    ops_window = state.get("ops_window", {})
    strat_pool = state.get("strat_pool", {})
    cascade = state.get("cascade", {})
    submissions = cascade.get("submissions", {})
    _ensure_refs_loaded()

    # --- Narrative description ---
    # Determine user_cascade_phase from role (handles cascade_WM correctly for Watch Master)
    _all_phases = ["cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal"]
    _role_key_self = _ROLE_TO_CASCADE_KEY.get(record.get("role", ""), "")
    user_cascade_phase: Optional[str] = None
    for _cp in _all_phases:
        if _role_key_self and _role_key_self in _CASCADE_PHASE_ROLES.get(_cp, frozenset()):
            user_cascade_phase = _cp
            break
    if user_cascade_phase is None:
        # Fallback to tier-based mapping for non-cascade roles
        user_cascade_phase = {"HC": "cascade_HC", "Company": "cascade_Company", "KT": "cascade_KT"}.get(tier)

    narr = _compose_orders_narrative(state, phase, tier, record.get("role", ""), beat_name)

    # --- Planet / scenario intel block ---
    current_node = campaign.get("current_node")
    raw_scenario = state.get("beat_scenarios", {}).get(current_node) if current_node else None
    # beat_scenarios[node_id] is now a list of 3 vectors; use committed_scenario if set,
    # otherwise fall back to slot 0 of the list (or the dict itself for legacy data)
    committed_sc = state.get("cascade", {}).get("committed_scenario")
    if committed_sc:
        node_scenario = committed_sc
    elif isinstance(raw_scenario, list):
        node_scenario = raw_scenario[0] if raw_scenario else None
    else:
        node_scenario = raw_scenario  # legacy single-dict fallback

    if node_scenario:
        codename = node_scenario.get("codename", "")
        dominant = ", ".join(f"**{t}**" for t in node_scenario.get("dominant_tags", []))
        terminus = node_scenario.get("terminus_intel", "none")
        terminus_icon = {"known": "🔴", "suspected": "🟡", "none": "⬛"}.get(terminus, "⬛")
        scenario_narrative = node_scenario.get("narrative", "")
        # Prepend node intel to narrative
        node_block = (
            f"📍 **{current_node}** — Operation **{codename}**\n"
            f"Doctrine: {dominant} · Terminus: {terminus_icon} {terminus.capitalize()}\n"
            f"*{scenario_narrative}*\n\n"
        )
        full_narr = node_block + narr
    else:
        full_narr = narr

    embed = discord.Embed(
        title=f"⚔️ Campaign Orders — {beat_name}",
        description=_trunc(full_narr, _EMBED_DESC_MAX),
        color=0x4B0082,
    )
    _orders_img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "campaign_orders.jpg")
    _orders_file: Optional[discord.File] = None
    if os.path.isfile(_orders_img_path):
        _orders_file = discord.File(_orders_img_path, filename="campaign_orders.jpg")
        embed.set_image(url="attachment://campaign_orders.jpg")
    camp_name = campaign.get("name") or "Jericho Watch Campaign"
    embed.set_footer(text=f"{camp_name}  ·  {PHASE_DISPLAY.get(phase, phase)}  ·  {current_node or '—'}")

    # --- Strategic position ---
    if current_node:
        pos_text = _fmt_strategic_position(current_node)
        embed.add_field(name="▸ Strategic Position", value=_trunc(pos_text, _EMBED_FIELD_MAX), inline=False)

    # --- Cascade status block ---
    if phase in ("cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal"):
        deadline_key = f"{phase}_deadline"
        deadline = cascade.get(deadline_key, "Unknown")
        peer_summary = _cascade_peer_summary(state, phase, enlistment)
        embed.add_field(
            name=f"▸ Orders Phase — {PHASE_DISPLAY.get(phase, phase)}",
            value=_trunc(f"Deadline: {_fmt_ts(deadline)}\n{peer_summary}", _EMBED_FIELD_MAX),
            inline=False,
        )

        # Own submission status for earlier tiers
        own_sub = submissions.get(user_id)
        if user_cascade_phase and user_cascade_phase != phase:
            # This member's cascade window has either passed or hasn't arrived
            _full_phase_order = ["cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT"]
            ucpidx = _full_phase_order.index(user_cascade_phase) if user_cascade_phase in _full_phase_order else -1
            cpidx = _full_phase_order.index(phase) if phase in _full_phase_order else -1
            if ucpidx >= 0 and cpidx >= 0 and ucpidx < cpidx:
                # Already submitted
                if own_sub:
                    choice_name = own_sub.get("choice_name", own_sub.get("choice_key", "?"))
                    _ensure_refs_loaded()
                    if user_cascade_phase == "cascade_WM":
                        choice_desc = ""
                    else:
                        role_data = _CASCADE_OPTIONS.get(own_sub.get("role_key", ""), {})
                        choice_desc = role_data.get(own_sub.get("choice_key", ""), {}).get("description", "")
                    val = f"✅ **{choice_name}**"
                    if choice_desc:
                        val += f"\n*{choice_desc[:200]}{'...' if len(choice_desc) > 200 else ''}*"
                    embed.add_field(
                        name=f"▸ Your Submission — {PHASE_DISPLAY.get(user_cascade_phase, user_cascade_phase)}",
                        value=val,
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name=f"▸ Your Submission — {PHASE_DISPLAY.get(user_cascade_phase, user_cascade_phase)}",
                        value="⚠️ No submission recorded for your tier.",
                        inline=False,
                    )

    # --- Ops phase: show personal strats only + redirect to /campaign-mandate ---
    if phase == "ops" and strat_pool.get("locked"):
        company_id = record.get("company_id")
        co_strats = strat_pool.get("company_mandates", {}).get(company_id, []) if company_id else []
        if isinstance(co_strats, str):
            co_strats = [co_strats]
        kt_sgt = record.get("kt_sgt_id")
        kt_strats = strat_pool.get("kt_mandates", {}).get(kt_sgt, []) if kt_sgt else []
        if isinstance(kt_strats, str):
            kt_strats = [kt_strats]
        theatre_list = strat_pool.get("theatre_mandate") or []
        if isinstance(theatre_list, str):
            theatre_list = [theatre_list]

        personal_lines = []
        if theatre_list:
            personal_lines.append(f"**Watch-wide:** {', '.join(f'`{s}`' for s in theatre_list)}")
        if co_strats:
            personal_lines.append(f"**Your company:** {', '.join(f'`{s}`' for s in co_strats)}")
        if kt_strats:
            personal_lines.append(f"**Your kill team:** {', '.join(f'`{s}`' for s in kt_strats)}")
        if not personal_lines:
            personal_lines.append("No mandate strats assigned to your unit.")
        personal_lines.append("\nFor operations, terminus targets, and the full Watch brief — `/campaign-mandate`.")

        ops_close = ops_window.get("closes_at")
        if ops_close:
            personal_lines.append(f"Ops window closes: {_fmt_ts(ops_close)}")

        embed.add_field(name="▸ Your Mandate This Cycle", value="\n".join(personal_lines), inline=False)
        await interaction.response.send_message(embed=embed, **({"file": _orders_file} if _orders_file else {}), ephemeral=True)
        return

    elif phase == "ops":
        embed.add_field(name="▸ Cycle Mandate", value="Mandate not yet published — use `/campaign-mandate`.", inline=False)

    # --- Ops window timing (cascade phases only) ---
    if phase not in ("ops",) and ops_window:
        embed.add_field(name="▸ Ops Window", value=(
            f"Opens: {_fmt_ts(ops_window.get('opened_at')) if ops_window.get('opened_at') else 'TBD'}\n"
            f"Closes: {_fmt_ts(ops_window.get('closes_at')) if ops_window.get('closes_at') else 'TBD'}"
        ), inline=False)

    # --- Cascade choice buttons (if it's this member's turn) ---
    if phase in ("cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal"):
        role_key = _get_user_cascade_role_key(interaction.user, phase)
        if role_key:
            existing_sub = submissions.get(user_id)
            # pre-compute filtered options for non-WM phases (used in both branches)
            filtered_opts: Optional[dict] = None
            if phase != "cascade_WM":
                # Rank-based ceiling for battle-line: Oathsworn≤4, Veteran≤3, Brother≤2
                _bl_max = {"Oathsworn": 4, "Watch Veteran": 3, "Watch Brother": 2}
                _max_opts = _bl_max.get(record.get("role", ""), 4) if phase == "cascade_personal" and record else 4
                filtered_opts = _select_cascade_options(state, record, role_key, max_options=_max_opts)
            if existing_sub and existing_sub.get("phase") == phase:
                choice_name = existing_sub.get("choice_name", existing_sub.get("choice_key", "?"))
                if phase == "cascade_WM":
                    choice_desc = existing_sub.get("choice_name", "")
                else:
                    role_data = _CASCADE_OPTIONS.get(existing_sub.get("role_key", ""), {})
                    choice_desc = role_data.get(existing_sub.get("choice_key", ""), {}).get("description", "")
                val = f"✅ **{choice_name}** — *select a button below to change.*"
                if choice_desc and phase != "cascade_WM":
                    val += f"\n\n*{choice_desc[:300]}{'...' if len(choice_desc) > 300 else ''}*"
                embed.add_field(name=f"▸ Your Orders — {PHASE_DISPLAY.get(phase, phase)}", value=val, inline=False)
            else:
                prompt = (
                    "Set your positioning order — hold or advance to an adjacent world:"
                    if phase == "cascade_WM"
                    else "Your orders await your word. Select your doctrine below:"
                )
                embed.add_field(
                    name=f"▸ Your Orders — {PHASE_DISPLAY.get(phase, phase)}",
                    value=prompt,
                    inline=False,
                )
                if filtered_opts:
                    options_lines = []
                    for _ok, _ov in filtered_opts.items():
                        _desc = _ov.get("description", "")
                        _short = _desc.split(".")[0].strip() + "." if _desc else ""
                        _line = f"**{_ov.get('name', _ok)}** — *{_short}*" if _short else f"**{_ov.get('name', _ok)}**"
                        options_lines.append(_line)
                    if options_lines:
                        embed.add_field(
                            name="▸ Available Doctrines",
                            value="\n".join(options_lines),
                            inline=False,
                        )
            if phase == "cascade_WM":
                wm_opts = _build_wm_movement_options(state)
                view = _CascadeChoiceView(user_id, role_key, phase, options_override=wm_opts)
            else:
                view = _CascadeChoiceView(user_id, role_key, phase, options_override=filtered_opts)
            await interaction.response.send_message(embed=embed, view=view, **({"file": _orders_file} if _orders_file else {}), ephemeral=True)
            return

    await interaction.response.send_message(embed=embed, **({"file": _orders_file} if _orders_file else {}), ephemeral=True)


# --- /campaign-cascade ---

@_g.bot.tree.command(
    name="campaign-cascade",
    description="View the full Cascade of Orders — who has submitted and who is pending.",
)
async def _campaign_cascade(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-cascade"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    state = _load_campaign_state()
    campaign = state.get("campaign", {})
    phase = campaign.get("phase", "inactive")
    beat = campaign.get("beat")
    beat_name = campaign.get("beat_name") or f"Cycle {beat or '?'}"

    if phase not in ("cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal", "ops"):
        await interaction.response.send_message(
            f"No cascade is active (current phase: **{phase}**).", ephemeral=True
        )
        return

    enlistment = state.get("enlistment", {})
    cascade = state.get("cascade", {})
    submissions = cascade.get("submissions", {})
    _ensure_refs_loaded()

    # Build a display-name resolver: prefer server nickname over stored discord_name
    guild = interaction.guild
    def _member_display(uid: str, fallback: str) -> str:
        if guild:
            m = guild.get_member(int(uid))
            if m:
                return m.display_name
        return fallback

    # Build role → user_id map for all phases
    phase_order = ["cascade_WM", "cascade_HC", "cascade_Company", "cascade_KT", "cascade_personal"]
    phase_labels = {
        "cascade_WM": "Watch Master",
        "cascade_HC": "High Command",
        "cascade_Company": "Company Command",
        "cascade_KT": "Kill Teams",
        "cascade_personal": "Battle-line",
    }

    embed = discord.Embed(
        title=f"📜 Orders Phase — {beat_name}",
        description=(
            "The Watch Master sets the theatre. High Command issues doctrine. "
            "Company Command and Kill Teams follow. Each tier's choices shape the strat pool for this beat."
        ),
        color=0x2F3136,
    )
    camp_name = campaign.get("name") or "Jericho Watch Campaign"

    for i, cp in enumerate(phase_order):
        eligible_keys = _CASCADE_PHASE_ROLES[cp]
        label = phase_labels[cp]

        # Map role_key → (role_display, member_name, user_id) for enrolled members eligible in this phase
        role_entries: list[tuple[str, str, str, str]] = []  # (role_key, role_display, member_name, user_id)
        for uid, rec in enlistment.items():
            if not rec.get("active"):
                continue
            rk = _ROLE_TO_CASCADE_KEY.get(rec.get("role", ""))
            if rk and rk in eligible_keys:
                member_name = _member_display(uid, rec.get("discord_name", ""))
                role_entries.append((rk, rec.get("role", rk), member_name, uid))

        if not role_entries:
            embed.add_field(name=f"▸ {label}", value="No eligible members enrolled.", inline=False)
            continue

        deadline = cascade.get(f"{cp}_deadline")
        lines: list[str] = []
        for rk, role_display, member_name, uid in sorted(role_entries, key=lambda x: _CASCADE_ROLE_PRIORITY.index(x[0]) if x[0] in _CASCADE_ROLE_PRIORITY else 99):
            name_tag = f" ({member_name})" if member_name else ""
            sub = submissions.get(uid)
            current_phase_idx = phase_order.index(phase) if phase in phase_order else len(phase_order)
            if sub and sub.get("phase") == cp:
                choice_name = sub.get("choice_name", sub.get("choice_key", "?"))
                lines.append(f"✅ **{role_display}**{name_tag} — {choice_name}")
            elif i < current_phase_idx:
                lines.append(f"❌ **{role_display}**{name_tag} — did not submit")
            elif i == current_phase_idx:
                lines.append(f"⏳ **{role_display}**{name_tag} — pending")
            else:
                lines.append(f"🔒 **{role_display}**{name_tag} — window not yet open")

        if i == (phase_order.index(phase) if phase in phase_order else -1):
            header = f"▸ {label}  —  accepting orders"
        elif i < (phase_order.index(phase) if phase in phase_order else len(phase_order)) or phase == "ops":
            header = f"▸ {label}  —  orders received"
        else:
            header = f"▸ {label}  —  standing by"

        field_val = "\n".join(lines)
        if deadline and i == (phase_order.index(phase) if phase in phase_order else -1):
            field_val += f"\n\nDeadline: {_fmt_ts(deadline)}"
        embed.add_field(name=header, value=_trunc(field_val, _EMBED_FIELD_MAX) or "—", inline=False)

    if phase == "ops":
        embed.set_footer(text=f"{camp_name}  ·  Orders resolved — Operations Window is open")
    elif phase in phase_order:
        embed.set_footer(text=f"{camp_name}  ·  {PHASE_DISPLAY.get(phase, phase)}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- /campaign-prestige ---

@_g.bot.tree.command(
    name="campaign-prestige",
    description="View the rolling 28-day prestige standings for kill teams and companies.",
)
@app_commands.describe(
    scope="View kill_team or company standings (default: kill_team for KT-tier, company for Company/HC)",
)
async def _campaign_prestige(
    interaction: discord.Interaction,
    scope: Optional[str] = None,
):
    if not _b_check_command_permission(interaction.user, "campaign-prestige"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    state = refresh_prestige_cache()
    user_id = str(interaction.user.id)
    enlistment = state.get("enlistment", {})
    record = enlistment.get(user_id)
    tier = record.get("tier", "KT") if record else "KT"

    if scope is None:
        scope = "company" if tier in ("Company", "HC") else "kill_team"

    if scope == "kill_team":
        rows = []
        for sgt_id, kt in state.get("kill_teams", {}).items():
            rows.append((kt.get("prestige_window_total", 0), kt.get("display_name", sgt_id), kt))
        rows.sort(key=lambda x: x[0], reverse=True)
        lines = []
        for i, (prestige, name, kt) in enumerate(rows[:20], 1):
            ribbon = f" [{kt.get('ribbon') or '—'}]" if kt.get("ribbon") else ""
            lore = " ★" if kt.get("lore_priority") else ""
            lines.append(f"{i}. **{name}** — {prestige} prestige{ribbon}{lore}")
        embed = discord.Embed(title="Kill Team Prestige Standings (28-day)", description=_trunc("\n".join(lines), _EMBED_DESC_MAX) or "No kill teams registered.", color=0x4B0082)
    else:
        rows = []
        for company_id, company in state.get("companies", {}).items():
            rows.append((company.get("prestige_window_total", 0), company.get("display_name", company_id), company))
        rows.sort(key=lambda x: x[0], reverse=True)
        lines = []
        for i, (prestige, name, co) in enumerate(rows, 1):
            ribbon = f" [{co.get('ribbon') or '—'}]" if co.get("ribbon") else ""
            lore = " ★" if co.get("lore_priority") else ""
            lines.append(f"{i}. **{name}** — {prestige} prestige{ribbon}{lore}")
        embed = discord.Embed(title="Company Prestige Standings (28-day)", description=_trunc("\n".join(lines), _EMBED_DESC_MAX) or "No companies.", color=0x4B0082)

    camp_name = state.get("campaign", {}).get("name") or "Jericho Watch Campaign"
    embed.set_footer(text=f"{camp_name}  ·  Rolling 28-day window")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- /campaign-mandate ---

@_g.bot.tree.command(
    name="campaign-mandate",
    description="View the published strat mandate for the current beat.",
)
async def _campaign_mandate(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-mandate"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    state = _load_campaign_state()
    campaign = state.get("campaign", {})
    phase = campaign.get("phase", "inactive")
    beat = campaign.get("beat")
    if phase != "ops":
        await interaction.response.send_message(
            f"Strat mandate is only available during the Operations Window (current phase: **{PHASE_DISPLAY.get(phase, phase)}**).",
            ephemeral=True,
        )
        return

    strat_pool = state.get("strat_pool", {})
    if not strat_pool.get("locked"):
        await interaction.response.send_message("The strat pool has not been locked yet. Mandate is not available.", ephemeral=True)
        return

    _strat_descs: Optional[dict] = None

    def _get_strat_desc(name: str) -> str:
        """Look up a strat description from stratagems.json (cached)."""
        nonlocal _strat_descs
        if _strat_descs is None:
            _ensure_refs_loaded()
            ref = _load_ref("stratagems.json")
            _strat_descs = {
                s["name"]: s.get("description", "")
                for s in ref.get("stratagems", [])
            }
        return _strat_descs.get(name, "")

    def _fmt_strats(val) -> str:
        if not val:
            return "None"
        if isinstance(val, str):
            val = [val]
        if not val:
            return "None"
        lines = []
        for s in val:
            desc = _get_strat_desc(s)
            line = f"**{s}**"
            if desc:
                line += f" — *{desc}*"
            lines.append(line)
        return "\n".join(lines)

    theatre_display = _fmt_strats(strat_pool.get("theatre_mandate"))

    # All company mandates, each labelled by display_name
    company_mandates_map = strat_pool.get("company_mandates", {})
    companies_state = state.get("companies", {})
    if company_mandates_map:
        co_lines = []
        for co_id, strats in sorted(company_mandates_map.items()):
            co_name = companies_state.get(co_id, {}).get("display_name") or co_id.capitalize()
            strat_text = ", ".join(f"**{s}**" for s in strats) if strats else "None"
            co_lines.append(f"*{co_name}*: {strat_text}")
        all_co_display = "\n".join(co_lines)
    else:
        all_co_display = "No company mandates derived."

    # All KT mandates, each labelled by KT display_name, grouped under company
    kt_mandates_map = strat_pool.get("kt_mandates", {})
    kill_teams_state = state.get("kill_teams", {})
    if kt_mandates_map:
        # Group KTs by company_id for display
        by_company: Dict[str, List[str]] = {}
        for sgt_id, strats in kt_mandates_map.items():
            kt_info = kill_teams_state.get(sgt_id, {})
            kt_name = kt_info.get("display_name") or f"KT ({sgt_id})"
            co = kt_info.get("company_id") or "unattached"
            co_label = companies_state.get(co, {}).get("display_name") or co.capitalize()
            strat_text = ", ".join(f"**{s}**" for s in strats) if strats else "None"
            by_company.setdefault(co_label, []).append(f"*{kt_name}*: {strat_text}")
        kt_lines = []
        for co_label in sorted(by_company):
            kt_lines.append(f"__*{co_label}*__")
            kt_lines.extend(f"\u00a0\u00a0{line}" for line in by_company[co_label])
        all_kt_display = "\n".join(kt_lines)
    else:
        all_kt_display = "No kill team mandates derived."

    total_mandates = (
        len(strat_pool.get("theatre_mandate") or []) +
        sum(len(v) for v in company_mandates_map.values()) +
        sum(len(v) for v in kt_mandates_map.values())
    )

    # Ops mandate
    ops_mandate = strat_pool.get("ops_mandate", {})
    eligible_missions = ops_mandate.get("eligible_missions", [])
    committed_node = ops_mandate.get("committed_node") or campaign.get("current_node") or "Unknown"
    if eligible_missions:
        ops_lines = []
        for m in eligible_missions:
            line = f"`{m['name']}`"
            if m.get("terminus_boss"):
                line += f" — *boss: {m['terminus_boss']}*"
            ops_lines.append(line)
        ops_display = "\n".join(ops_lines)
    else:
        ops_display = "All missions eligible (no node committed)"

    # Prestige kill targets
    terminus_directive = strat_pool.get("terminus_directive", {})
    huntmaster_active = terminus_directive.get("huntmaster_active", False)
    flagged_targets = terminus_directive.get("flagged_targets", [])
    callers = terminus_directive.get("callers", [])
    if huntmaster_active:
        target_lines = []
        for t in flagged_targets:
            if isinstance(t, dict):
                if t.get("source_op"):
                    target_lines.append(f"`{t['name']}` *({t['source_op']})*")
                else:
                    target_lines.append(f"`{t['name']}` *(roaming)*")
            else:
                target_lines.append(f"`{t}`")
        terminus_display = "**Huntmaster active** — high-value kills earn prestige this cycle."
        if target_lines:
            terminus_display += "\nTargets: " + ",  ".join(target_lines)
        terminus_display += "\n" + (f"Engagement callers: {', '.join(callers)}" if callers else "No engagement callers enlisted.")
    else:
        terminus_display = "Huntmaster not enlisted — no prestige kill targets this cycle."

    camp_name = campaign.get("name") or "Jericho Watch Campaign"
    beat_label = f"Cycle {beat}" if beat else "—"

    embed = discord.Embed(
        title="Cycle Mandate",
        description=f"**{total_mandates} required strat(s)** this cycle. Operations: **{committed_node}**.",
        color=0x8B0000,
    )
    embed.add_field(name="▸ Operations", value=_trunc(ops_display, _EMBED_FIELD_MAX), inline=False)
    embed.add_field(name="▸ Theatre Stratagem (Watch-wide)", value=_trunc(theatre_display, _EMBED_FIELD_MAX), inline=False)
    embed.add_field(name="▸ Company Stratagems", value=_trunc(all_co_display, _EMBED_FIELD_MAX), inline=False)
    embed.add_field(name="▸ Kill Team Stratagems", value=_trunc(all_kt_display, _EMBED_FIELD_MAX), inline=False)
    embed.add_field(name="▸ Prestige Kill Targets", value=_trunc(terminus_display, _EMBED_FIELD_MAX), inline=False)
    embed.set_footer(text=f"{camp_name}  ·  {beat_label}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- /campaign-title ---

@_g.bot.tree.command(
    name="campaign-title",
    description="Grant or revoke a narrative title for a kill team or company.",
)
@app_commands.describe(
    target_type="kill_team or company",
    target="User ID of the KT Sergeant, or company name (primus/secundus/etc.)",
    title="The narrative title to grant (empty to revoke)",
    action="grant or revoke",
)
async def _campaign_title(
    interaction: discord.Interaction,
    target_type: str,
    target: str,
    title: Optional[str] = None,
    action: str = "grant",
):
    if not _b_check_command_permission(interaction.user, "campaign-title"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    user_roles = {r.name for r in getattr(interaction.user, "roles", [])}
    state = _load_campaign_state()

    if target_type == "company":
        if "Watch Master" not in user_roles:
            await interaction.response.send_message("Only the Watch Master may grant company titles.", ephemeral=True)
            return
        company = state.get("companies", {}).get(target.lower())
        if not company:
            await interaction.response.send_message(f"Company `{target}` not found.", ephemeral=True)
            return
        if action == "revoke":
            company["title"] = None
            company["title_granted_by"] = None
            company["title_granted_at"] = None
        else:
            if not title:
                await interaction.response.send_message("Provide a title to grant.", ephemeral=True)
                return
            prestige = company.get("prestige_window_total", 0)
            if prestige < _CO_TITLE_ACQUIRE:
                await interaction.response.send_message(
                    f"Company prestige ({prestige}) is below the grant floor ({_CO_TITLE_ACQUIRE}). Title cannot be granted.",
                    ephemeral=True,
                )
                return
            company["title"] = title
            company["title_granted_by"] = str(interaction.user.id)
            company["title_granted_at"] = _iso_now()
        _save_campaign_state(state)
        verb = "revoked" if action == "revoke" else f"granted: **{title}**"
        await interaction.response.send_message(f"Company `{target.capitalize()}` title {verb}.", ephemeral=False)

    elif target_type == "kill_team":
        captain_roles = {"Watch Captain", "Watch Lieutenant"}
        if not (user_roles & captain_roles):
            await interaction.response.send_message("Only a Watch Captain or Watch Lieutenant may grant KT titles.", ephemeral=True)
            return
        kt = state.get("kill_teams", {}).get(target)
        if not kt:
            await interaction.response.send_message(f"Kill team with Sgt ID `{target}` not found.", ephemeral=True)
            return
        # Granter must be in the same company as the KT
        user_id = str(interaction.user.id)
        granter_record = state.get("enlistment", {}).get(user_id, {})
        granter_company = granter_record.get("company_id")
        kt_company = kt.get("company_id")
        if granter_company != kt_company:
            await interaction.response.send_message("You may only grant titles to kill teams within your own company.", ephemeral=True)
            return
        if action == "revoke":
            kt["title"] = None
            kt["title_granted_by"] = None
            kt["title_granted_at"] = None
        else:
            if not title:
                await interaction.response.send_message("Provide a title to grant.", ephemeral=True)
                return
            prestige = kt.get("prestige_window_total", 0)
            if prestige < _KT_TITLE_ACQUIRE:
                await interaction.response.send_message(
                    f"Kill team prestige ({prestige}) is below the grant floor ({_KT_TITLE_ACQUIRE}). Title cannot be granted.",
                    ephemeral=True,
                )
                return
            kt["title"] = title
            kt["title_granted_by"] = user_id
            kt["title_granted_at"] = _iso_now()
        _save_campaign_state(state)
        verb = "revoked" if action == "revoke" else f"granted: **{title}**"
        await interaction.response.send_message(f"Kill team `{target}` title {verb}.", ephemeral=False)
    else:
        await interaction.response.send_message("Invalid target_type. Choose `kill_team` or `company`.", ephemeral=True)


# --- /campaign-dashboard ---

@_g.bot.tree.command(
    name="campaign-dashboard",
    description="Post or refresh the campaign dashboard embed. (Forgemaster only)",
)
async def _campaign_dashboard(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-dashboard"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    user_roles = {r.name for r in getattr(interaction.user, "roles", [])}
    if "Forgemaster" not in user_roles:
        await interaction.response.send_message("Only the Forgemaster may post the campaign dashboard.", ephemeral=True)
        return

    state = refresh_prestige_cache()
    campaign_phase = state.get("campaign", {}).get("phase", "inactive")
    if campaign_phase == "inactive":
        await interaction.response.send_message("No campaign is currently active.", ephemeral=True)
        return
    state = update_lore_priority(state, save=False)
    state = check_reward_thresholds(state)
    _save_campaign_state(state)

    campaign = state.get("campaign", {})
    beat = campaign.get("beat")
    phase = campaign.get("phase", "inactive")
    ops_window = state.get("ops_window", {})

    embed = discord.Embed(
        title=f"Campaign Dashboard — Cycle {beat or '?'}",
        description=f"Phase: **{phase}**",
        color=0x1C1C1C,
    )
    embed.add_field(name="Ops Window", value=(
        f"Opens: {_fmt_ts(ops_window.get('opened_at')) if ops_window.get('opened_at') else 'TBD'}\n"
        f"Closes: {_fmt_ts(ops_window.get('closes_at')) if ops_window.get('closes_at') else 'TBD'}"
    ), inline=False)

    # Lore priority
    lp = state.get("lore_priority", {})
    lp_kt = lp.get("kill_team", {})
    lp_co = lp.get("company", {})
    embed.add_field(
        name="Lore Priority",
        value=(
            f"KT: {lp_kt.get('display_name') or 'Vacant'}\n"
            f"Company: {lp_co.get('display_name') or 'Vacant'}"
        ),
        inline=False,
    )

    # Top 5 KTs by prestige
    kt_rows = sorted(
        [(kt.get("prestige_window_total", 0), kt.get("display_name", sgt_id))
         for sgt_id, kt in state.get("kill_teams", {}).items()],
        reverse=True,
    )[:5]
    if kt_rows:
        embed.add_field(
            name="Top Kill Teams",
            value="\n".join(f"{i+1}. {name} — {p}" for i, (p, name) in enumerate(kt_rows)),
            inline=True,
        )

    # Companies
    co_rows = sorted(
        [(co.get("prestige_window_total", 0), co.get("display_name", cid))
         for cid, co in state.get("companies", {}).items()],
        reverse=True,
    )
    if co_rows:
        embed.add_field(
            name="Company Prestige",
            value="\n".join(f"{name}: {p}" for p, name in co_rows),
            inline=True,
        )

    embed.set_footer(text=f"Refreshed: {_iso_now()[:19]}Z")
    await interaction.response.send_message(embed=embed)


# --- /campaign-status ---

@_g.bot.tree.command(
    name="campaign-status",
    description="View campaign phase, beat schedule, and resolution state.",
)
async def _campaign_status(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-status"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    user_roles = {r.name for r in getattr(interaction.user, "roles", [])}
    allowed_roles = {"Watch Master", "Forgemaster", "Castellan"}
    if not (user_roles & allowed_roles):
        await interaction.response.send_message("Access denied — Watch Master, Forgemaster, or Castellan only.", ephemeral=True)
        return

    state = _load_campaign_state()
    campaign = state.get("campaign", {})
    strat_pool = state.get("strat_pool", {})
    ops_window = state.get("ops_window", {})

    embed = discord.Embed(title="Campaign Status", color=0x333333)
    embed.add_field(name="ID", value=campaign.get("id") or "Not started", inline=True)
    embed.add_field(name="Phase", value=campaign.get("phase") or "inactive", inline=True)
    embed.add_field(name="Cycle", value=str(campaign.get("beat") or "—"), inline=True)
    embed.add_field(name="Current Node", value=campaign.get("current_node") or "—", inline=True)
    embed.add_field(name="Started", value=_fmt_ts(campaign.get("started_at")), inline=True)
    embed.add_field(name="Strat Pool Locked", value="Yes" if strat_pool.get("locked") else "No", inline=True)
    embed.add_field(name="Ops Closes", value=_fmt_ts(ops_window.get("closes_at")), inline=True)

    # Enlistment count
    active_count = sum(1 for r in state.get("enlistment", {}).values() if r.get("active"))
    embed.add_field(name="Enlisted (active)", value=str(active_count), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- /campaign-milestone ---

@_g.bot.tree.command(
    name="campaign-milestone",
    description="View your personal milestone progress for the current campaign.",
)
async def _campaign_milestone(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-milestone"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    _ensure_refs_loaded()
    state = _load_campaign_state()
    user_id = str(interaction.user.id)
    enlistment = state.get("enlistment", {})
    record = enlistment.get(user_id)
    if not record or not record.get("active"):
        await interaction.response.send_message("You are not currently enlisted in the campaign.", ephemeral=True)
        return

    milestone_progress = record.get("milestone_progress", {})
    chapter = record.get("chapter", "")

    # Get applicable milestones for this chapter
    applicable = list(_MILESTONES.get("universal", []))
    for ca in _MILESTONES.get("chapter_affinity", []):
        if ca.get("chapter") == chapter or ca.get("chapter") == "all":
            applicable.extend(ca.get("milestones", []))

    if not applicable:
        await interaction.response.send_message("No milestones found for your chapter.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Personal Milestones",
        description=f"Chapter: **{chapter}** | {len([m for m in milestone_progress.values() if m.get('completed')])} completed",
        color=0x2F4F4F,
    )

    for milestone in applicable[:20]:
        mid = milestone.get("id")
        label = milestone.get("label", mid)
        threshold = milestone.get("threshold", 0)
        reward = milestone.get("prestige_reward", 0)
        mp = milestone_progress.get(mid, {"count": 0, "completed": False})
        count = mp.get("count", 0)
        completed = mp.get("completed", False)
        status = "✅" if completed else f"{count}/{threshold}"
        embed.add_field(
            name=f"{status} {label}",
            value=f"{milestone.get('description', '')} (+{reward} prestige on completion)",
            inline=False,
        )

    camp_name = state.get("campaign", {}).get("name") or "Jericho Watch Campaign"
    embed.set_footer(text=f"{camp_name}  ·  Personal milestones")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- /campaign-init ---

@_g.bot.tree.command(
    name="campaign-init",
    description="Initialise a new campaign. Opens cascade immediately. (Forgemaster only)",
)
@app_commands.describe(
    campaign_id="Optional manual campaign ID slug (e.g. 'campaign_002'). Auto-generated if omitted.",
    campaign_name="Optional name for the campaign. Auto-generated if omitted.",
    beat_number="Starting beat number (default: 1).",
    cycle_name="Optional name for the first cycle. Auto-generated if omitted.",
    beat_duration_days="Days each ops window stays open (default: 7). Ops open after cascade resolves.",
    doctrine_tags="Optional comma-separated doctrine tags to influence the campaign name (e.g. 'aggressive,terminus').",
    starting_node="Starting planet/node from the Jericho Reach graph (default: random from Kadaku/Avarax/Demerium).",
)
async def _campaign_init(
    interaction: discord.Interaction,
    campaign_id: Optional[str] = None,
    campaign_name: Optional[str] = None,
    beat_number: int = 1,
    cycle_name: Optional[str] = None,
    beat_duration_days: int = 7,
    doctrine_tags: Optional[str] = None,
    starting_node: Optional[str] = None,
):
    if not _b_check_command_permission(interaction.user, "campaign-init"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    user_roles = {r.name for r in getattr(interaction.user, "roles", [])}
    if "Forgemaster" not in user_roles:
        await interaction.response.send_message("Only the Forgemaster may initialise a campaign.", ephemeral=True)
        return

    # Refuse if campaign already active
    existing = _load_campaign_state()
    current_phase = existing.get("campaign", {}).get("phase", "inactive")
    if current_phase not in ("inactive", "complete"):
        await interaction.response.send_message(
            f"A campaign is already running (phase: **{current_phase}**). "
            f"Use `/campaign-status` to view it. End the current campaign before initialising a new one.",
            ephemeral=True,
        )
        return

    # Parse doctrine tags for naming
    tags = [t.strip() for t in doctrine_tags.split(",") if t.strip()] if doctrine_tags else []

    # Generate names
    seed = int(_utcnow().timestamp())
    camp_name = campaign_name.strip() if campaign_name and campaign_name.strip() else generate_campaign_name(seed=seed)
    beat_name = cycle_name.strip() if cycle_name and cycle_name.strip() else generate_beat_name(beat_number, doctrine_tags=tags, seed=seed + 1)

    # Randomly determine campaign length (short=3, medium=4, long=5 beats)
    _CAMPAIGN_LENGTH = {3: "Short", 4: "Medium", 5: "Long"}
    total_beats = random.Random(seed + 2).choice([3, 4, 5])
    length_label = _CAMPAIGN_LENGTH[total_beats]

    # Generate campaign ID if not provided
    if not campaign_id:
        ts_slug = _utcnow().strftime("%Y%m%d")
        campaign_id = f"campaign_{ts_slug}"

    # Preserve existing formation state; reset only campaign-level fields
    state = _load_campaign_state()
    blank = _blank_campaign_state()
    for key in (
        "campaign", "ops_window", "strat_pool", "campaign_log", "credited_aars",
        "beat_scenarios", "pressure", "cascade", "lore_priority", "beat_record",
    ):
        state[key] = blank[key]
    state.setdefault("kill_teams", {})
    state["companies"] = {}
    state.setdefault("enlistment", {})
    state["_schema_version"] = 1
    state["total_beats"] = 3  # will be overwritten below

    # Validate / default starting node
    _DEFAULT_NODES = ["Kadaku", "Avarax", "Demerium"]
    node_id = (starting_node or random.Random(seed).choice(_DEFAULT_NODES)).strip()
    node_data = _graph_node(node_id)
    if not node_data:
        # Try case-insensitive match
        for n in _load_graph().get("nodes", []):
            if n["id"].lower() == node_id.lower():
                node_data = n
                node_id = n["id"]
                break
    if not node_data:
        await interaction.response.send_message(
            f"Unknown node **{node_id}**. Check the Jericho Reach graph for valid planet names.",
            ephemeral=True,
        )
        return

    now_iso = _iso_now()
    state["campaign"].update({
        "id": campaign_id,
        "name": camp_name,
        "beat": beat_number,
        "beat_name": beat_name,
        "phase": "cascade_WM",
        "started_at": now_iso,
        "beat_duration_days": max(1, beat_duration_days),
        "total_beats": total_beats,
        "length_label": length_label,
        "current_node": node_id,
        "visited_nodes": [node_id],
    })
    state["total_beats"] = total_beats
    # Open cascade at WM tier first; WM sets the Watch's position, then cascade_HC opens
    _enter_cascade_phase(state, "cascade_WM")

    # Seed companies from config — only add companies that have at least one active enlisted member
    CONFIG = _b("CONFIG") or {}
    cfg_companies = CONFIG.get("companies", {})
    active_company_ids = {
        rec.get("company_id")
        for rec in state.get("enlistment", {}).values()
        if rec.get("active") and rec.get("company_id")
    }
    for co_id, co_cfg in cfg_companies.items():
        if co_id not in state["companies"]:
            if co_id not in active_company_ids:
                continue  # skip companies with no active members
            co_name = co_cfg.get("name") or co_id.capitalize()
            state["companies"][co_id] = {
                "display_name": f"Watch Company {co_name}",
                "prestige_window_total": 0,
                "prestige_log": [],
                "ribbon": None,
                "honour": None,
                "honour_iron_compact": False,
                "iron_compact_beats": [],
                "lore_priority": False,
                "last_prestige_check": None,
                "title": None,
                "title_granted_by": None,
                "title_granted_at": None,
            }
        else:
            # Ensure new fields exist on legacy company records
            state["companies"][co_id].setdefault("prestige_log", [])
            state["companies"][co_id].setdefault("honour_iron_compact", False)
            state["companies"][co_id].setdefault("iron_compact_beats", [])

    _save_campaign_state(state)

    _wm_is_open = state["campaign"].get("phase", "cascade_WM") == "cascade_WM"
    actual_phase = state["campaign"].get("phase", "cascade_WM")
    cascade_data = state.get("cascade", {})
    opening_deadline_ts = cascade_data.get(f"{actual_phase}_deadline", "")

    wm_enlisted = any(
        rec.get("active") and _ROLE_TO_CASCADE_KEY.get(rec.get("role", "")) == "watch_master"
        for rec in state.get("enlistment", {}).values()
    )

    if _wm_is_open:
        desc = (
            f"The Jericho Watch deploys to **{node_id}**. "
            f"The {length_label.lower()} campaign begins.\n\n"
            f"**Watch Master** — set your theatre positioning order. "
            f"Your choice opens the cascade for High Command."
        )
    else:
        desc = (
            f"The Jericho Watch deploys to **{node_id}**. "
            f"The {length_label.lower()} campaign begins.\n\n"
            f"The Watch Master is occupied with other duties. The Watch holds its position. "
            f"**High Command** — the cascade falls to you. Issue your doctrine orders."
        )

    embed = discord.Embed(
        title=f"⚔️ {camp_name}",
        description=desc,
        color=0xC4A030,
    )

    _cascade_deadline_label = "Watch Master orders due" if _wm_is_open else "High Command orders due"
    deadline_val = _fmt_ts_abs(opening_deadline_ts[:19]) if opening_deadline_ts else "—"

    # Compact force summary
    company_display = ", ".join(
        co.get("display_name") or co_id.capitalize()
        for co_id, co in state["companies"].items()
    ) or "None"
    _kt_lines: list[str] = []
    for sgt_id, kt in state.get("kill_teams", {}).items():
        active_member_ids = [
            uid for uid, rec in state.get("enlistment", {}).items()
            if rec.get("active") and rec.get("kt_sgt_id") == sgt_id
        ]
        if not active_member_ids:
            continue
        kt_role_name = None
        if interaction.guild:
            for uid in active_member_ids:
                member = interaction.guild.get_member(int(uid))
                if not member:
                    continue
                for r in member.roles:
                    rl = r.name.lower()
                    if "kill" in rl and "team" in rl and "champion" not in rl:
                        kt_role_name = r.name
                        break
                if kt_role_name:
                    break
        _kt_lines.append(kt_role_name or kt.get("display_name") or sgt_id)
    kt_display = ", ".join(_kt_lines) or "None"

    embed.add_field(name="Cycle", value=beat_name, inline=True)
    embed.add_field(name="Length", value=f"{length_label} · {total_beats} cycles", inline=True)
    embed.add_field(name=_cascade_deadline_label, value=deadline_val, inline=False)
    embed.add_field(
        name="Force",
        value=f"{company_display}\n{kt_display}",
        inline=False,
    )
    embed.set_footer(text="use /campaign-orders when your phase opens")

    _phase_ping = _cascade_phase_ping(state, "cascade_WM" if wm_enlisted else "cascade_HC")
    await interaction.response.send_message(
        content=f"<@&{WATCH_BROTHER_ROLE_ID}> {_phase_ping}",
        embed=embed,
    )


# --- /campaign-rename ---

@_g.bot.tree.command(
    name="campaign-rename",
    description="Rename the active campaign or the current cycle. (Forgemaster only)",
)
@app_commands.describe(
    campaign_name="New name for the campaign (leave blank to keep current).",
    cycle_name="New name for the current cycle (leave blank to keep current).",
)
async def _campaign_rename(
    interaction: discord.Interaction,
    campaign_name: Optional[str] = None,
    cycle_name: Optional[str] = None,
):
    if not _b_check_command_permission(interaction.user, "campaign-rename"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    user_roles = {r.name for r in getattr(interaction.user, "roles", [])}
    if "Forgemaster" not in user_roles:
        await interaction.response.send_message("Only the Forgemaster may rename the campaign or cycle.", ephemeral=True)
        return

    if not campaign_name and not cycle_name:
        await interaction.response.send_message("Provide at least one of `campaign_name` or `cycle_name`.", ephemeral=True)
        return

    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase == "inactive":
        await interaction.response.send_message("No active campaign to rename.", ephemeral=True)
        return

    changed: list[str] = []
    if campaign_name and campaign_name.strip():
        state["campaign"]["name"] = campaign_name.strip()
        changed.append(f"Campaign renamed to **{campaign_name.strip()}**")
    if cycle_name and cycle_name.strip():
        state["campaign"]["beat_name"] = cycle_name.strip()
        changed.append(f"Cycle renamed to **{cycle_name.strip()}**")

    _save_campaign_state(state)
    await interaction.response.send_message("\n".join(changed), ephemeral=False)


# --- /campaign-pause ---

@_g.bot.tree.command(
    name="campaign-pause",
    description="Pause the active campaign. Halts new log entries and enlistment. (Forgemaster only)",
)
async def _campaign_pause(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-pause"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase == "inactive":
        await interaction.response.send_message("No campaign is currently active.", ephemeral=True)
        return
    if phase == "paused":
        await interaction.response.send_message("Campaign is already paused.", ephemeral=True)
        return
    if phase in ("complete", "evaluating"):
        await interaction.response.send_message(f"Campaign is already in **{phase}** phase.", ephemeral=True)
        return

    state["campaign"]["phase"] = "paused"
    state["campaign"].setdefault("pause_history", []).append({
        "paused_at": _iso_now(),
        "paused_by": str(interaction.user.id),
        "previous_phase": phase,
    })
    _save_campaign_state(state)

    camp_name = state["campaign"].get("name") or state["campaign"].get("id") or "Campaign"
    await interaction.response.send_message(
        f"⏸ **{camp_name}** paused. No new log entries or enlistments will be accepted until resumed.",
    )


# --- /campaign-resume ---

@_g.bot.tree.command(
    name="campaign-resume",
    description="Resume a paused campaign, returning it to ops phase. (Forgemaster only)",
)
async def _campaign_resume(interaction: discord.Interaction):
    if not _b_check_command_permission(interaction.user, "campaign-resume"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase != "paused":
        await interaction.response.send_message(
            f"Campaign is not paused (current phase: **{phase or 'inactive'}**).",
            ephemeral=True,
        )
        return

    state["campaign"]["phase"] = "ops"
    pause_history = state["campaign"].get("pause_history", [])
    if pause_history:
        pause_history[-1]["resumed_at"] = _iso_now()
        pause_history[-1]["resumed_by"] = str(interaction.user.id)
    _save_campaign_state(state)

    camp_name = state["campaign"].get("name") or state["campaign"].get("id") or "Campaign"
    await interaction.response.send_message(
        f"▶️ **{camp_name}** resumed. Phase is now **ops**.",
    )


# --- /campaign-end ---

@_g.bot.tree.command(
    name="campaign-end",
    description="End the current campaign and record final standings. (Forgemaster only)",
)
@app_commands.describe(
    outcome="Optional campaign outcome note (e.g. 'Victory — Sector Secured').",
)
async def _campaign_end(
    interaction: discord.Interaction,
    outcome: Optional[str] = None,
):
    if not _b_check_command_permission(interaction.user, "campaign-end"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase in ("inactive", "complete"):
        await interaction.response.send_message(
            f"No active campaign to end (phase: **{phase}**).",
            ephemeral=True,
        )
        return

    # Snapshot final prestige standings before closing
    state = refresh_prestige_cache()
    now_iso = _iso_now()
    state["campaign"]["phase"] = "complete"
    state["campaign"]["ended_at"] = now_iso
    if outcome:
        state["campaign"]["outcome"] = outcome

    # Record final standings snapshot
    kt_standings = sorted(
        [
            {"sgt_id": sgt_id, "display_name": kt.get("display_name", sgt_id), "prestige": kt.get("prestige_window_total", 0)}
            for sgt_id, kt in state.get("kill_teams", {}).items()
        ],
        key=lambda x: x["prestige"],
        reverse=True,
    )
    co_standings = sorted(
        [
            {"company_id": co_id, "display_name": co.get("display_name", co_id), "prestige": co.get("prestige_window_total", 0)}
            for co_id, co in state.get("companies", {}).items()
        ],
        key=lambda x: x["prestige"],
        reverse=True,
    )
    state["campaign"]["final_standings"] = {
        "recorded_at": now_iso,
        "kill_teams": kt_standings,
        "companies": co_standings,
    }
    _save_campaign_state(state)

    camp_name = state["campaign"].get("name") or state["campaign"].get("id") or "Campaign"
    embed = discord.Embed(
        title=f"⚔️ {camp_name} — Concluded",
        description=outcome or "Campaign ended.",
        color=0x555555,
    )
    embed.add_field(name="Ended At", value=now_iso[:19] + "Z", inline=False)
    if kt_standings:
        top_kts = "\n".join(f"{i+1}. {e['display_name']} — {e['prestige']}" for i, e in enumerate(kt_standings[:5]))
        embed.add_field(name="Top Kill Teams", value=top_kts, inline=True)
    if co_standings:
        co_lines = "\n".join(f"{e['display_name']}: {e['prestige']}" for e in co_standings)
        embed.add_field(name="Company Standings", value=co_lines, inline=True)
    embed.set_footer(text=f"Closed by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


# --- /campaign-reset ---

@_g.bot.tree.command(
    name="campaign-reset",
    description="Wipe all campaign progress back to a blank inactive state. Irreversible. (Forgemaster only)",
)
@app_commands.describe(
    confirm="Type 'CONFIRM' to wipe all campaign data.",
)
async def _campaign_reset(
    interaction: discord.Interaction,
    confirm: str,
):
    if not _b_check_command_permission(interaction.user, "campaign-reset"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    if confirm.strip().upper() != "CONFIRM":
        await interaction.response.send_message(
            "Reset aborted. Pass `confirm: CONFIRM` (all caps) to wipe campaign data.",
            ephemeral=True,
        )
        return

    os.makedirs(os.path.dirname(CAMPAIGN_STATE_PATH) or ".", exist_ok=True)
    # Preserve formation records (kill_teams, companies); reset campaign progress only
    state = _load_campaign_state()
    blank = _blank_campaign_state()
    for key in (
        "campaign", "ops_window", "strat_pool", "campaign_log", "credited_aars",
        "beat_scenarios", "pressure", "cascade", "lore_priority", "beat_record",
    ):
        state[key] = blank[key]
    state["_schema_version"] = 1
    state["total_beats"] = blank["campaign"]["total_beats"]

    # Reset campaign-specific fields on every enlistment record, keep formation assignments
    for rec in state.get("enlistment", {}).values():
        rec.pop("last_aar_timestamp", None)
        rec.pop("auto_de_enlist_warning_sent", None)
        rec.pop("milestone_progress", None)

    _save_campaign_state(state)

    await interaction.response.send_message(
        "🗑️ Campaign progress wiped. Formation assignments and prestige history are preserved. "
        "Run `/campaign-init` to start a new campaign.",
    )
