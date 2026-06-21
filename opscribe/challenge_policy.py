"""Shared challenge policy helpers.

This module keeps Crux Terminatus Black Laurels audit rules in one place so
grace-period revocation and progress display stay consistent.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Any

from .constants import (
    BLACK_LAURELS_MISSION_ADD_DATES,
    BLACK_LAURELS_GRANDFATHERED_MISSIONS,
    BLACK_LAURELS_REQUIRED_MISSIONS,
    BLACK_LAURELS_STRICT_ENFORCEMENT_DATE,
    CHALLENGE_POLICY_PATCH_RELEASE_DATE,
)


def _clean_mission_name(record: Mapping[str, Any]) -> str:
    """Normalize mission text from datastore records."""
    raw = re.sub(r"<@&\d+>", "", (record.get("mission") or record.get("mission_name") or "")).lower().strip()
    return re.split(r"\s*@", raw)[0].strip()


def _parse_record_ts(value: Any) -> datetime | None:
    """Parse ISO timestamps from records, accepting a trailing Z."""
    if not isinstance(value, str) or not value.strip():
        return None
    ts = value.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def split_missing_requirements_by_policy(
    completed_missions: set[str],
    required_missions: set[str],
    mission_add_dates: Mapping[str, datetime],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify missing requirements using patch baseline + per-item grace.

    Users who satisfy all requirements that were already required at patch release
    are baseline-frozen: only requirements added after patch release can trigger
    revocation, and only after 28 days per added requirement.

    Returns:
      baseline_frozen: bool
      missing_all: set[str]
      missing_overdue: set[str]
      missing_in_grace: set[str]
      baseline_required: set[str]
    """
    now_utc = now or datetime.now(timezone.utc)
    baseline_required = {
        m
        for m in required_missions
        if (mission_add_dates.get(m) or datetime(2000, 1, 1, tzinfo=timezone.utc)) <= CHALLENGE_POLICY_PATCH_RELEASE_DATE
    }

    missing_all = set(required_missions) - set(completed_missions)
    baseline_frozen = len(missing_all & baseline_required) == 0

    if not missing_all:
        return {
            "baseline_frozen": baseline_frozen,
            "missing_all": set(),
            "missing_overdue": set(),
            "missing_in_grace": set(),
            "baseline_required": baseline_required,
        }

    if not baseline_frozen:
        return {
            "baseline_frozen": False,
            "missing_all": missing_all,
            "missing_overdue": set(missing_all),
            "missing_in_grace": set(),
            "baseline_required": baseline_required,
        }

    missing_overdue: set[str] = set()
    missing_in_grace: set[str] = set()
    for mission in missing_all:
        added_at = mission_add_dates.get(mission)
        if added_at is None:
            missing_overdue.add(mission)
            continue
        if added_at <= CHALLENGE_POLICY_PATCH_RELEASE_DATE:
            missing_overdue.add(mission)
            continue
        if now_utc >= (added_at + timedelta(days=28)):
            missing_overdue.add(mission)
        else:
            missing_in_grace.add(mission)

    return {
        "baseline_frozen": True,
        "missing_all": missing_all,
        "missing_overdue": missing_overdue,
        "missing_in_grace": missing_in_grace,
        "baseline_required": baseline_required,
    }


def evaluate_crux_bl_rank_a(user_id: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate Crux BL Rank-A requirement with grandfathered mission baseline.

    Returns:
      all_rank_a: bool
      saw_any: bool
      non_a_missions: sorted list[str] (post-enforcement only)
      missing_missions: sorted list[str]
      grandfathered: bool (legacy 7-mission grandfathering)
      baseline_frozen: bool (qualified at patch release)
      effective_missions: set[str]
    """
    recs = list(records)

    user_bl_records: list[Mapping[str, Any]] = []
    for rec in recs:
        if user_id not in [str(b) for b in (rec.get("brother_ids") or [])]:
            continue
        if not (rec.get("black_laurels_in_mission") or rec.get("black_laurels_in_difficulty")):
            continue
        user_bl_records.append(rec)

    # First pass: detect if user completed the legacy baseline before cutoff.
    legacy_completed: set[str] = set()
    for rec in user_bl_records:
        mission = _clean_mission_name(rec)
        if mission not in BLACK_LAURELS_GRANDFATHERED_MISSIONS:
            continue
        rec_dt = _parse_record_ts(rec.get("timestamp"))
        if rec_dt is not None and rec_dt < BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
            legacy_completed.add(mission)

    legacy_grandfathered = legacy_completed >= BLACK_LAURELS_GRANDFATHERED_MISSIONS

    baseline_required = {
        m
        for m in BLACK_LAURELS_REQUIRED_MISSIONS
        if (BLACK_LAURELS_MISSION_ADD_DATES.get(m) or datetime(2000, 1, 1, tzinfo=timezone.utc)) <= CHALLENGE_POLICY_PATCH_RELEASE_DATE
    }
    completed_baseline_missions: set[str] = set()
    for rec in user_bl_records:
        mission = _clean_mission_name(rec)
        if mission in baseline_required:
            completed_baseline_missions.add(mission)
    baseline_frozen = completed_baseline_missions >= baseline_required

    matured_new_requirements = {
        m
        for m in BLACK_LAURELS_REQUIRED_MISSIONS
        if (BLACK_LAURELS_MISSION_ADD_DATES.get(m) and BLACK_LAURELS_MISSION_ADD_DATES[m] > CHALLENGE_POLICY_PATCH_RELEASE_DATE)
        and datetime.now(timezone.utc) >= (BLACK_LAURELS_MISSION_ADD_DATES[m] + timedelta(days=28))
    }

    if legacy_grandfathered:
        effective_missions = set(BLACK_LAURELS_GRANDFATHERED_MISSIONS) | matured_new_requirements
    elif baseline_frozen:
        effective_missions = set(baseline_required) | matured_new_requirements
    else:
        effective_missions = set(BLACK_LAURELS_REQUIRED_MISSIONS)

    # Second pass: audit only the effective mission set.
    saw_any = False
    all_rank_a = True
    non_a_missions: set[str] = set()
    seen_effective_missions: set[str] = set()

    for rec in user_bl_records:
        mission = _clean_mission_name(rec)
        if mission not in effective_missions:
            continue

        saw_any = True
        seen_effective_missions.add(mission)
        rank = (rec.get("rank") or "A").strip().upper()
        if rank == "A":
            continue

        # Only post-enforcement non-A records fail the Crux BL requirement.
        rec_dt = _parse_record_ts(rec.get("timestamp"))
        if rec_dt is not None and rec_dt >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
            all_rank_a = False
            if mission:
                non_a_missions.add(mission)

    missing_missions = sorted(effective_missions - seen_effective_missions)

    return {
        "all_rank_a": saw_any and all_rank_a and not missing_missions,
        "saw_any": saw_any,
        "non_a_missions": sorted(non_a_missions),
        "missing_missions": missing_missions,
        "grandfathered": legacy_grandfathered,
        "baseline_frozen": baseline_frozen,
        "effective_missions": effective_missions,
    }
