"""Shared challenge policy helpers.

This module keeps Crux Terminatus Black Laurels audit rules in one place so
grace-period revocation and progress display stay consistent.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Mapping, Any

from .constants import (
    BLACK_LAURELS_GRANDFATHERED_MISSIONS,
    BLACK_LAURELS_REQUIRED_MISSIONS,
    BLACK_LAURELS_STRICT_ENFORCEMENT_DATE,
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


def evaluate_crux_bl_rank_a(user_id: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate Crux BL Rank-A requirement with grandfathered mission baseline.

    Returns:
      all_rank_a: bool
      saw_any: bool
      non_a_missions: sorted list[str] (post-enforcement only)
      grandfathered: bool
      effective_missions: set[str]
    """
    recs = list(records)

    # First pass: detect if user completed the legacy baseline before cutoff.
    legacy_completed: set[str] = set()
    for rec in recs:
        if user_id not in [str(b) for b in (rec.get("brother_ids") or [])]:
            continue
        if not (rec.get("black_laurels_in_mission") or rec.get("black_laurels_in_difficulty")):
            continue
        mission = _clean_mission_name(rec)
        if mission not in BLACK_LAURELS_GRANDFATHERED_MISSIONS:
            continue
        rec_dt = _parse_record_ts(rec.get("timestamp"))
        if rec_dt is not None and rec_dt < BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
            legacy_completed.add(mission)

    grandfathered = legacy_completed >= BLACK_LAURELS_GRANDFATHERED_MISSIONS
    effective_missions = (
        set(BLACK_LAURELS_GRANDFATHERED_MISSIONS)
        if grandfathered
        else set(BLACK_LAURELS_REQUIRED_MISSIONS)
    )

    # Second pass: audit only the effective mission set.
    saw_any = False
    all_rank_a = True
    non_a_missions: set[str] = set()

    for rec in recs:
        if user_id not in [str(b) for b in (rec.get("brother_ids") or [])]:
            continue
        if not (rec.get("black_laurels_in_mission") or rec.get("black_laurels_in_difficulty")):
            continue
        mission = _clean_mission_name(rec)
        if mission not in effective_missions:
            continue

        saw_any = True
        rank = (rec.get("rank") or "A").upper()
        if rank == "A":
            continue

        # Only post-enforcement non-A records fail the Crux BL requirement.
        rec_dt = _parse_record_ts(rec.get("timestamp"))
        if rec_dt is not None and rec_dt >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
            all_rank_a = False
            if mission:
                non_a_missions.add(mission)

    return {
        "all_rank_a": saw_any and all_rank_a,
        "saw_any": saw_any,
        "non_a_missions": sorted(non_a_missions),
        "grandfathered": grandfathered,
        "effective_missions": effective_missions,
    }
