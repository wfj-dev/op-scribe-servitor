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

from .constants import CAMPAIGN_STATE_PATH, AAR_RECORDS_PATH, CAMPAIGN_ANNOUNCEMENT_CHANNEL_ID
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

# Base prestige values per difficulty class
_PRESTIGE_ABSOLUTE = 1
_PRESTIGE_HARD_SIEGE_PER_5_WAVES = 0.5
_PRESTIGE_OMEGA = 2
_PRESTIGE_HARD_STRAT = 3
_PRESTIGE_OMEGA_STRAT = 4

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
    """Generate a lore-flavoured beat codename: 'BEAT N: ADJECTIVE NOUN'.

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
    return f"BEAT {beat_num}: {adj} {noun}"


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

    Returns (success, message).
    """
    state, err = _get_campaign_state_checked()
    if err:
        return False, err

    enlistment = state.setdefault("enlistment", {})
    existing = enlistment.get(user_id)
    if existing and existing.get("active"):
        return False, "You are already enlisted in the current campaign."

    if tier == "KT" and not kt_sgt_id:
        return False, "KT-tier members must provide their Sergeant's user ID."

    if tier in ("Company", "HC") and not company_id:
        return False, "Company and HC tier members must provide their company."

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
                "display_name": f"Sgt {discord_name}'s Kill Team" if role == "Watch Sergeant" else f"Kill Team",
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

    _save_campaign_state(state)
    company_label = f" ({company_id.capitalize()})" if company_id else ""
    return True, (
        f"Enlisted successfully.\n"
        f"**Chapter:** {chapter} | **Tier:** {tier} | **Company:** {company_id or 'N/A'}{company_label}"
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
    terminus_killed: bool,
    strats_active: Optional[List[str]] = None,
) -> Tuple[bool, str, Optional[dict]]:
    """Submit a campaign log entry linked to a specific AAR by Discord message URL.

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

    beat = state.get("campaign", {}).get("beat")
    waves = aar_record.get("waves", 0) or 0
    base_prestige = _compute_base_prestige(difficulty_class, strats_active or [], waves)
    campaign_log = state.setdefault("campaign_log", {})

    # Determine the full list of co-runners (enrolled members in this AAR beyond the submitter)
    co_runner_ids = [
        bid for bid in brother_ids
        if bid != user_id and enlistment.get(bid, {}).get("active")
    ]

    # Classify co-runners for the submitter (for Iron Compact tracking on the entry)
    co_run_info = _classify_co_runners(user_id, record, brother_ids, enlistment)
    officer_tiers = co_run_info["officer_tiers"]

    mission_name = _parse_mission_name(aar_record.get("mission", ""))

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
            "terminus_killed": terminus_killed,
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
    _credit_prestige_for_entry(state, user_id, record, entry, aar_record, brother_ids, base_prestige)
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
        _credit_prestige_for_entry(state, co_id, co_record, co_entry, aar_record, brother_ids, base_prestige)
        _update_milestone_progress(state, co_id, co_record, co_entry, aar_record)

    # Record all newly credited members against this AAR
    credited_aars[aar_id] = list(set(already_credited + newly_credited))

    # If terminus killed, record it
    if terminus_killed:
        ops_window.setdefault("terminus_calls", [])
        ops_window["terminus_calls"].append({
            "user_id": user_id,
            "entry_id": entry["entry_id"],
            "reported_at": _iso_now(),
        })

    _save_campaign_state(state)
    co_count = len(newly_credited) - 1
    co_note = f" ({co_count} co-runner{'s' if co_count != 1 else ''} also credited)" if co_count else ""
    return True, (
        f"Campaign log submitted{co_note}.\n"
        f"**Mission:** {mission_name} | **Difficulty:** {difficulty_class} | **Beat:** {beat or 'unknown'}"
        + (" | Terminus kill recorded." if terminus_killed else "")
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

        # If no enrolled formations found, fall through without crediting



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
) -> dict:
    """Derive theatre, company, and KT mandates from the confirmed strat pool.

    Each tier gets 1-3 mandates depending on cascade participation (tier_counts).
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

    scored = score_strats_against_aggregate(doctrine_aggregate)
    pool_set = set(confirmed_pool)
    scored = [(name, score, strat) for name, score, strat in scored if name in pool_set]

    conflict_set = _build_conflict_set(confirmed_pool)
    # Global used set — prevents same strat appearing twice across all mandates
    used: List[str] = []

    def _pick_next(local_exclude: set) -> Optional[str]:
        exclude = local_exclude | set(used)
        for name, _score, _strat in scored:
            if name in exclude:
                continue
            if any(name in conflict_set.get(m, set()) for m in used):
                continue
            return name
        return None

    def _pick_n(n: int, extra_exclude: Optional[set] = None) -> List[str]:
        picks: List[str] = []
        ex = set(extra_exclude or [])
        for _ in range(n):
            s = _pick_next(ex)
            if s:
                used.append(s)
                picks.append(s)
                ex.add(s)
        return picks

    # Theatre mandates
    theatre_mandates = _pick_n(tier_counts.get("theatre", 1))

    # Company mandates — each company picks independently after theatre
    company_mandates: Dict[str, List[str]] = {}
    co_n = tier_counts.get("company", 1)
    for company_id in state.get("companies", {}).keys():
        co_picks = _pick_n(co_n)
        company_mandates[company_id] = co_picks

    # KT mandates — each KT picks independently after theatre + company
    kt_mandates: Dict[str, List[str]] = {}
    kt_n = tier_counts.get("kt", 1)
    for sgt_id in state.get("kill_teams", {}).keys():
        kt_picks = _pick_n(kt_n)
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
) -> dict:
    """Generate a scenario for a node for the upcoming beat.

    Returns a scenario dict matching the output_scenario schema in scenario_generation.json.
    """
    _ensure_refs_loaded()
    sg = _SCENARIO_GEN

    rng = random.Random(beat_seed) if beat_seed is not None else random.Random()

    node_affinity = sg.get("node_type_affinity", {})
    region_modifier = sg.get("region_modifier", {})
    pressure_rules = sg.get("pressure_rules", {}).get("pressure_thresholds", {})
    pressure_steps = sg.get("pressure_rules", {}).get("terminus_intel_steps", ["none", "suspected", "known"])
    pmt = sg.get("pressure_modifier_table", {})
    mission_bias = sg.get("mission_bias_table", {})
    codename_pools = sg.get("codename_pools", {})
    narrative_templates = sg.get("narrative_templates", {})

    # Step 1: Base dominant tags from node type
    nta = node_affinity.get(node_type, {})
    base_tags = list(nta.get("dominant_tags", ["aggressive", "recovery"]))
    terminus_affinity = nta.get("terminus_affinity", "low")

    # Step 2: Region modifier — push secondary tag
    if region:
        rm = region_modifier.get(region, {})
        pushed_tag = rm.get("push_tag")
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
    affinity_map = {"low": 0.2, "medium": 0.5, "high": 0.8}
    affinity_prob = affinity_map.get(terminus_affinity, 0.3)
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
    beat_num = _load_campaign_state().get("campaign", {}).get("beat") or 0
    scenario_id = f"{node_id.lower().replace(' ', '_')}_b{beat_num}_a"

    return {
        "scenario_id": scenario_id,
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
        incremented = False
        rule = milestone.get("tracking_rule", "")
        data_source = milestone.get("data_source", "")

        if "data_source" in milestone and data_source == "campaign_log":
            # terminus_killed type
            if "terminus_killed == true" in rule and entry.get("terminus_killed"):
                mp["count"] += 1
                incremented = True
        elif data_source == "aar_record":
            # gene_seed_carrier, armory_data, op count, mission specialist
            if "gene_seed_carrier_id == member_id" in rule:
                if str(aar_record.get("gene_seed_carrier_id")) == user_id:
                    mp["count"] += 1
                    incremented = True
            elif "armory_data > 0" in rule:
                if aar_record.get("armory_data", 0) > 0 and user_id in [str(b) for b in aar_record.get("brother_ids", [])]:
                    mp["count"] += 1
                    incremented = True
            elif "member_id in brother_ids" in rule:
                if user_id in [str(b) for b in aar_record.get("brother_ids", [])]:
                    mp["count"] += 1
                    incremented = True

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
}

# Which cascade keys are eligible per phase
_CASCADE_PHASE_ROLES: Dict[str, frozenset] = {
    "cascade_HC": frozenset({
        "watch_master", "lord_executioner", "forgemaster", "chief_apothecary",
        "high_chaplain", "huntmaster", "void_warden", "castellan",
    }),
    "cascade_Company": frozenset({
        "watch_captain", "watch_lieutenant", "company_champion", "watch_techmarine",
        "watch_apothecary", "watch_chaplain", "watch_librarian", "watch_keeper",
    }),
    "cascade_KT": frozenset({"watch_sergeant", "judiciar"}),
}

# Highest-authority order for role disambiguation
_CASCADE_ROLE_PRIORITY = [
    "watch_master", "lord_executioner", "forgemaster", "chief_apothecary",
    "high_chaplain", "huntmaster", "void_warden", "castellan",
    "watch_captain", "watch_lieutenant", "company_champion", "watch_techmarine",
    "watch_apothecary", "watch_chaplain", "watch_librarian", "watch_keeper",
    "watch_sergeant", "judiciar",
]

# Cascade window durations per phase
_CASCADE_DEADLINE_HOURS: Dict[str, int] = {
    "cascade_HC": 48,
    "cascade_Company": 48,
    "cascade_KT": 24,
}

_STRAT_POOL_SIZE = 12  # Target conflict-free pool size for beat resolution


def _get_user_cascade_role_key(user, phase: str) -> Optional[str]:
    """Return the user's highest-priority cascade role key valid for *phase*."""
    if not hasattr(user, "roles"):
        return None
    valid_keys = _CASCADE_PHASE_ROLES.get(phase, frozenset())
    user_role_names = {r.name for r in user.roles}
    user_keys = {
        cascade_key
        for role_name, cascade_key in _ROLE_TO_CASCADE_KEY.items()
        if role_name in user_role_names and cascade_key in valid_keys
    }
    for key in _CASCADE_ROLE_PRIORITY:
        if key in user_keys:
            return key
    return None


def _enter_cascade_phase(state: dict, phase: str) -> None:
    """Set the campaign phase to a cascade phase and record its deadline."""
    deadline_hours = _CASCADE_DEADLINE_HOURS.get(phase, 48)
    state["campaign"]["phase"] = phase
    cascade = state.setdefault("cascade", {})
    cascade.setdefault("submissions", {})
    now = _utcnow()
    deadline = now + timedelta(hours=deadline_hours)
    cascade[f"{phase}_started_at"] = now.isoformat()
    cascade[f"{phase}_deadline"] = deadline.isoformat()


def _aggregate_cascade_doctrine(state: dict) -> Dict[str, float]:
    """Sum doctrine tags from all cascade submissions into a doctrine aggregate."""
    aggregate: Dict[str, float] = {}
    for sub in state.get("cascade", {}).get("submissions", {}).values():
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

    # 1. Aggregate doctrine from all cascade submissions across all tiers
    doctrine_aggregate = _aggregate_cascade_doctrine(state)

    # 2. Score strats and build conflict-free pool
    scored = score_strats_against_aggregate(doctrine_aggregate)
    pool = _build_conflict_free_pool(scored, pool_size=_STRAT_POOL_SIZE)

    # 3. Compute per-tier mandate counts from cascade participation
    submissions = state.get("cascade", {}).get("submissions", {})
    hc_roles = _CASCADE_PHASE_ROLES["cascade_HC"]
    co_roles = _CASCADE_PHASE_ROLES["cascade_Company"]
    kt_roles_set = _CASCADE_PHASE_ROLES["cascade_KT"]
    hc_distinct = len({s["role_key"] for s in submissions.values() if s.get("role_key") in hc_roles})
    co_distinct = len({s["role_key"] for s in submissions.values() if s.get("role_key") in co_roles})
    kt_distinct = len({s["role_key"] for s in submissions.values() if s.get("role_key") in kt_roles_set})
    tier_counts = {
        "theatre": _tier_mandate_count(hc_distinct, "HC"),
        "company": _tier_mandate_count(co_distinct, "Company"),
        "kt": _tier_mandate_count(kt_distinct, "KT"),
    }

    # 4. Derive mandates — refresh prestige first so company/KT records are current
    state = refresh_prestige_cache(state)
    mandate_result = derive_strat_mandate(doctrine_aggregate, pool, state, tier_counts)

    # 5. Update strat pool
    beat_doctrine_tags = sorted(
        doctrine_aggregate.keys(), key=lambda k: -doctrine_aggregate[k]
    )
    state["strat_pool"] = {
        "locked": True,
        "pool": pool,
        "theatre_mandate": mandate_result.get("theatre_mandate", []),
        "company_mandates": mandate_result.get("company_mandates", {}),
        "kt_mandates": mandate_result.get("kt_mandates", {}),
        "tier_counts": tier_counts,
        "derived_at": _iso_now(),
        "doctrine_aggregate": doctrine_aggregate,
    }

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

    # 9. If campaign is over, close it; otherwise open the next ops window
    if summary["campaign_complete"]:
        campaign["phase"] = "complete"
        campaign["ended_at"] = _iso_now()
        campaign["outcome"] = f"Campaign concluded after {total_beats} beats."
    else:
        if ops_closes_at is None:
            duration_days = campaign.get("beat_duration_days") or 7
            auto_close = _utcnow() + timedelta(days=duration_days)
            ops_closes_at = auto_close.isoformat()
        state["ops_window"] = {
            "opened_at": _iso_now(),
            "closes_at": ops_closes_at,
            "terminus_calls": [],
        }
        campaign["phase"] = "ops"
        summary["ops_closes_at"] = ops_closes_at

    return summary


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


async def sweep_campaign_beat_clock() -> None:
    """Auto-advance campaign beat lifecycle: ops window expiry and cascade deadlines.

    Called every 15 minutes by the beat clock loop in bot.py.
    Transitions: ops → cascade_HC → cascade_Company → cascade_KT → ops (next beat).
    """
    state = _load_campaign_state()
    phase = state.get("campaign", {}).get("phase", "inactive")

    if phase not in ("ops", "cascade_HC", "cascade_Company", "cascade_KT"):
        return

    bot = _b("bot")
    if not bot:
        return

    now = _utcnow()
    changed = False
    announcement: Optional[str] = None

    camp_name = state["campaign"].get("name") or "Campaign"
    beat = state["campaign"].get("beat") or "?"

    # ops → cascade_HC when the ops window closes
    if phase == "ops":
        closes_at = _parse_iso(state.get("ops_window", {}).get("closes_at"))
        if closes_at and now >= closes_at:
            _enter_cascade_phase(state, "cascade_HC")
            deadline_ts = state["cascade"].get("cascade_HC_deadline", "")[:19]
            announcement = (
                f"⚔️ **{camp_name} — Beat {beat} ops window closed.**\n"
                f"The Cascade of Orders begins. **High Command**, submit your doctrine orders via `/campaign-cascade-submit`.\n"
                f"HC cascade window closes: `{deadline_ts}Z`"
            )
            changed = True

    # cascade_HC → cascade_Company on deadline
    elif phase == "cascade_HC":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_HC_deadline"))
        if deadline and now >= deadline:
            _enter_cascade_phase(state, "cascade_Company")
            deadline_ts = state["cascade"].get("cascade_Company_deadline", "")[:19]
            announcement = (
                f"⚔️ **{camp_name} — Beat {beat} cascade advancing: Company Command.**\n"
                f"HC orders have been logged. **Captains and Company officers**, submit your orders via `/campaign-cascade-submit`.\n"
                f"Company cascade window closes: `{deadline_ts}Z`"
            )
            changed = True

    # cascade_Company → cascade_KT on deadline
    elif phase == "cascade_Company":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_Company_deadline"))
        if deadline and now >= deadline:
            _enter_cascade_phase(state, "cascade_KT")
            deadline_ts = state["cascade"].get("cascade_KT_deadline", "")[:19]
            announcement = (
                f"⚔️ **{camp_name} — Beat {beat} cascade advancing: Kill Teams.**\n"
                f"Company orders logged. **Watch Sergeants**, submit your kill team doctrine via `/campaign-cascade-submit`.\n"
                f"KT cascade window closes: `{deadline_ts}Z`"
            )
            changed = True

    # cascade_KT → beat resolution on deadline
    elif phase == "cascade_KT":
        deadline = _parse_iso(state.get("cascade", {}).get("cascade_KT_deadline"))
        if deadline and now >= deadline:
            summary = _resolve_beat_and_open_next(state, ops_closes_at=None)
            new_beat_name = summary["new_beat_name"]
            new_beat = summary["new_beat"]
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
                announcement = (
                    f"⚔️ **{camp_name} — {new_beat_name} begins.**\n"
                    f"Cascade resolved. Theatre Mandates: {theatre_display} | Dominant doctrine: {top_tags}\n"
                    f"Ops window is **open** and will close automatically based on beat duration."
                )
            changed = True

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
@app_commands.describe(
    chapter="Your Space Marine chapter (e.g. 'Blood Angels')",
    company="Your company assignment: primus | secundus | tertius | quartus | quintus",
    kt_sgt_id="[KT-tier] Discord user ID of your Kill Team Sergeant",
)
async def _campaign_enlist(
    interaction: discord.Interaction,
    chapter: str,
    company: Optional[str] = None,
    kt_sgt_id: Optional[str] = None,
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

    company_id = (company or "").lower().strip() if company else None
    valid_companies = {"primus", "secundus", "tertius", "quartus", "quintus"}
    if company_id and company_id not in valid_companies:
        await interaction.response.send_message(
            f"Invalid company `{company}`. Choose from: {', '.join(sorted(valid_companies))}",
            ephemeral=True,
        )
        return

    if tier == "KT" and role_name == "Watch Sergeant" and not kt_sgt_id:
        kt_sgt_id = str(interaction.user.id)

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

@_g.bot.tree.command(
    name="campaign-log",
    description="Submit a campaign log entry for an op you completed.",
)
@app_commands.describe(
    aar_link="Discord message URL of the AAR post for this op.",
    terminus_killed="Was any terminus target killed during this op?",
    strats_active="Comma-separated list of active strats you ran (e.g. 'Unleashed Fury, Extreme Challenge')",
)
async def _campaign_log(
    interaction: discord.Interaction,
    aar_link: str,
    terminus_killed: bool,
    strats_active: Optional[str] = None,
):
    if not _b_check_command_permission(interaction.user, "campaign-log"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not _b_is_allowed_channel(interaction):
        await interaction.response.send_message("This command is not available in this channel.", ephemeral=True)
        return

    strats_list = [s.strip() for s in strats_active.split(",") if s.strip()] if strats_active else []
    success, msg, entry = log_campaign_entry(
        user_id=str(interaction.user.id),
        aar_link=aar_link,
        terminus_killed=terminus_killed,
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
        decision_key = role_data.get("_decision", self._role_key)
        cascade = state.setdefault("cascade", {})
        cascade.setdefault("submissions", {})
        cascade["submissions"][self._owner_id] = {
            "role_key": self._role_key,
            "phase": phase,
            "decision": decision_key,
            "choice_key": self._opt_key,
            "choice_name": opt_name,
            "tags": tags,
            "submitted_at": _iso_now(),
        }
        _save_campaign_state(state)
        beat = state["campaign"].get("beat") or "?"
        await interaction.response.edit_message(
            content=f"\u2705 **{opt_name}** submitted for Beat {beat}.",
            embed=None,
            view=None,
        )


class _CascadeChoiceView(discord.ui.View):
    """Ephemeral view that renders cascade doctrine buttons for /campaign-orders."""

    def __init__(self, user_id: str, role_key: str, phase: str):
        super().__init__(timeout=300)
        _ensure_refs_loaded()
        role_data = _CASCADE_OPTIONS.get(role_key, {})
        for opt_key, opt_val in role_data.items():
            if opt_key.startswith("_"):
                continue
            self.add_item(_CascadeButton(opt_key, opt_val, role_key, phase, user_id))


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
    tier = record.get("tier", "KT")
    ops_window = state.get("ops_window", {})
    strat_pool = state.get("strat_pool", {})

    embed = discord.Embed(
        title=f"Campaign Orders — Beat {beat or '?'}",
        description=f"Phase: **{phase}** | Your tier: **{tier}**",
        color=0x4B0082,
    )
    embed.add_field(name="Ops Window", value=(
        f"Opens: {ops_window.get('opened_at') or 'TBD'}\n"
        f"Closes: {ops_window.get('closes_at') or 'TBD'}"
    ), inline=False)

    # Strat mandate
    if strat_pool.get("locked"):
        theatre_list = strat_pool.get("theatre_mandate") or []
        if isinstance(theatre_list, str):
            theatre_list = [theatre_list]  # backwards compat
        theatre_display = ", ".join(f"`{s}`" for s in theatre_list) or "None"
        company_id = record.get("company_id")
        co_strats = strat_pool.get("company_mandates", {}).get(company_id, []) if company_id else []
        if isinstance(co_strats, str):
            co_strats = [co_strats]
        co_display = ", ".join(f"`{s}`" for s in co_strats) or "N/A"
        kt_sgt = record.get("kt_sgt_id")
        kt_strats = strat_pool.get("kt_mandates", {}).get(kt_sgt, []) if kt_sgt else []
        if isinstance(kt_strats, str):
            kt_strats = [kt_strats]
        kt_display = ", ".join(f"`{s}`" for s in kt_strats) or "N/A"
        embed.add_field(name="Theatre Strats", value=theatre_display, inline=False)
        embed.add_field(name="Company Strats", value=co_display, inline=True)
        embed.add_field(name="KT Strats", value=kt_display, inline=True)
    else:
        embed.add_field(name="Strat Mandate", value="Not yet published.", inline=False)

    # Terminus flag (for appropriate tiers)
    if tier in ("HC", "Company"):
        terminus_flag = ops_window.get("terminus_flag")
        if terminus_flag:
            embed.add_field(name="Terminus Flag", value=terminus_flag, inline=False)

    # Cascade phase: show doctrine choice buttons if user has an eligible role
    if phase in ("cascade_HC", "cascade_Company", "cascade_KT"):
        role_key = _get_user_cascade_role_key(interaction.user, phase)
        if role_key:
            _ensure_refs_loaded()
            existing_sub = state.get("cascade", {}).get("submissions", {}).get(user_id)
            phase_label = phase.replace("cascade_", "")
            if existing_sub:
                embed.add_field(
                    name=f"Cascade Orders — {phase_label}",
                    value=f"\u2705 You submitted **{existing_sub.get('choice_name', existing_sub.get('choice_key'))}**. Select a button to change your choice.",
                    inline=False,
                )
            else:
                embed.add_field(
                    name=f"Cascade Orders — {phase_label}",
                    value="Select your doctrine order below:",
                    inline=False,
                )
            view = _CascadeChoiceView(user_id, role_key, phase)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

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
        embed = discord.Embed(title="Kill Team Prestige Standings (28-day)", description="\n".join(lines) or "No kill teams registered.", color=0x4B0082)
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
        embed = discord.Embed(title="Company Prestige Standings (28-day)", description="\n".join(lines) or "No companies.", color=0x4B0082)

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
    phase = state.get("campaign", {}).get("phase", "inactive")
    if phase != "ops":
        await interaction.response.send_message(f"Strat mandate is only available during the **ops** phase (current: {phase}).", ephemeral=True)
        return

    strat_pool = state.get("strat_pool", {})
    if not strat_pool.get("locked"):
        await interaction.response.send_message("The strat pool has not been locked yet. Mandate is not available.", ephemeral=True)
        return

    user_id = str(interaction.user.id)
    enlistment = state.get("enlistment", {})
    record = enlistment.get(user_id)

    company_id = record.get("company_id") if record else None
    kt_sgt = record.get("kt_sgt_id") if record else None

    def _fmt_strats(val) -> str:
        if not val:
            return "None"
        if isinstance(val, str):
            return f"`{val}`"
        return ", ".join(f"`{s}`" for s in val) if val else "None"

    theatre_display = _fmt_strats(strat_pool.get("theatre_mandate"))
    co_display = _fmt_strats(strat_pool.get("company_mandates", {}).get(company_id)) if company_id else "N/A"
    kt_display = _fmt_strats(strat_pool.get("kt_mandates", {}).get(kt_sgt)) if kt_sgt else "N/A"
    tier_counts = strat_pool.get("tier_counts", {})
    total_mandates = (
        len(strat_pool.get("theatre_mandate") or []) +
        len(strat_pool.get("company_mandates", {}).get(company_id) or []) +
        len(strat_pool.get("kt_mandates", {}).get(kt_sgt) or [])
    )

    embed = discord.Embed(
        title="Strat Mandate — Current Beat",
        description=f"**{total_mandates} required strat(s)** this beat. Optional pool strats may be added.",
        color=0x8B0000,
    )
    embed.add_field(name="Theatre Strats (all)", value=theatre_display, inline=False)
    embed.add_field(name="Company Strats", value=co_display, inline=True)
    embed.add_field(name="Kill Team Strats", value=kt_display, inline=True)

    strat_pool_list = strat_pool.get("pool", [])
    if strat_pool_list:
        embed.add_field(name="Full Pool", value=", ".join(f"`{s}`" for s in strat_pool_list[:20]), inline=False)

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
        title=f"Campaign Dashboard — Beat {beat or '?'}",
        description=f"Phase: **{phase}**",
        color=0x1C1C1C,
    )
    embed.add_field(name="Ops Window", value=(
        f"Opens: {ops_window.get('opened_at') or 'TBD'}\n"
        f"Closes: {ops_window.get('closes_at') or 'TBD'}"
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
    embed.add_field(name="Beat", value=str(campaign.get("beat") or "—"), inline=True)
    embed.add_field(name="Current Node", value=campaign.get("current_node") or "—", inline=True)
    embed.add_field(name="Started", value=(campaign.get("started_at") or "—")[:19], inline=True)
    embed.add_field(name="Strat Pool Locked", value="Yes" if strat_pool.get("locked") else "No", inline=True)
    embed.add_field(name="Ops Closes", value=(ops_window.get("closes_at") or "—")[:19], inline=True)

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

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- /campaign-init ---

@_g.bot.tree.command(
    name="campaign-init",
    description="Initialise a new campaign. Seeds state and sets phase to 'ops'. (Forgemaster only)",
)
@app_commands.describe(
    campaign_id="Optional manual campaign ID slug (e.g. 'campaign_002'). Auto-generated if omitted.",
    beat_number="Starting beat number (default: 1).",
    beat_duration_days="Days each ops window stays open before cascade begins (default: 7).",
    ops_closes_at="Override: exact ISO timestamp for this first beat's close (e.g. 2026-07-01T20:00:00). Overrides beat_duration_days.",
    doctrine_tags="Optional comma-separated doctrine tags to influence the campaign name (e.g. 'aggressive,terminus').",
)
async def _campaign_init(
    interaction: discord.Interaction,
    campaign_id: Optional[str] = None,
    beat_number: int = 1,
    beat_duration_days: int = 7,
    ops_closes_at: Optional[str] = None,
    doctrine_tags: Optional[str] = None,
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
    camp_name = generate_campaign_name(seed=seed)
    beat_name = generate_beat_name(beat_number, doctrine_tags=tags, seed=seed + 1)

    # Randomly determine campaign length (short=3, medium=4, long=5 beats)
    _CAMPAIGN_LENGTH = {3: "Short", 4: "Medium", 5: "Long"}
    total_beats = random.Random(seed + 2).choice([3, 4, 5])
    length_label = _CAMPAIGN_LENGTH[total_beats]

    # Generate campaign ID if not provided
    if not campaign_id:
        ts_slug = _utcnow().strftime("%Y%m%d")
        campaign_id = f"campaign_{ts_slug}"

    # Parse ops window close time; fall back to beat_duration_days
    closes_dt = _parse_iso(ops_closes_at) if ops_closes_at else None
    if closes_dt is None:
        closes_dt = _utcnow() + timedelta(days=max(1, beat_duration_days))

    # Preserve existing formation state; reset only campaign-level fields
    state = _load_campaign_state()
    blank = _blank_campaign_state()
    for key in (
        "campaign", "ops_window", "strat_pool", "campaign_log", "credited_aars",
        "beat_scenarios", "pressure", "cascade", "lore_priority", "beat_record",
    ):
        state[key] = blank[key]
    state.setdefault("kill_teams", {})
    state.setdefault("companies", {})
    state.setdefault("enlistment", {})
    state["_schema_version"] = 1
    state["total_beats"] = 3  # will be overwritten below

    now_iso = _iso_now()
    state["campaign"].update({
        "id": campaign_id,
        "name": camp_name,
        "beat": beat_number,
        "beat_name": beat_name,
        "phase": "ops",
        "started_at": now_iso,
        "beat_duration_days": max(1, beat_duration_days),
        "total_beats": total_beats,
        "length_label": length_label,
    })
    state["total_beats"] = total_beats
    state["ops_window"] = {
        "opened_at": now_iso,
        "closes_at": closes_dt.isoformat(),
        "terminus_calls": [],
    }

    # Seed companies from config — only add companies not already present
    CONFIG = _b("CONFIG") or {}
    cfg_companies = CONFIG.get("companies", {})
    for co_id, co_cfg in cfg_companies.items():
        if co_id not in state["companies"]:
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

    # Persist
    os.makedirs(os.path.dirname(CAMPAIGN_STATE_PATH) or ".", exist_ok=True)
    _save_campaign_state(state)

    embed = discord.Embed(
        title=f"⚔️ {camp_name}",
        description="Campaign initialised and set to **ops** phase.",
        color=0xC4A030,
    )
    embed.add_field(name="Campaign ID", value=campaign_id, inline=True)
    embed.add_field(name="Beat", value=f"{beat_number} — {beat_name}", inline=True)
    embed.add_field(name="Phase", value="ops", inline=True)
    embed.add_field(name="Ops Window Closes", value=closes_dt.strftime("%Y-%m-%d %H:%M UTC"), inline=False)
    embed.add_field(name="Campaign Length", value=f"{length_label} ({total_beats} beats)", inline=True)
    embed.add_field(name="Beat Duration", value=f"{max(1, beat_duration_days)} days", inline=True)
    embed.add_field(name="Companies Seeded", value=", ".join(state["companies"].keys()) or "None", inline=False)
    embed.set_footer(text=f"Initialised by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


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
