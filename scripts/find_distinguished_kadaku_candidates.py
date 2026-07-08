#!/usr/bin/env python3
"""Print user IDs eligible for Distinguished Kadaku from AAR records.

Eligibility:
- Mission is one of Kadaku campaign missions.
- Leviathan Protocol is present on Mission line.
- Black Laurels tag is present (mission, difficulty, or fallback elsewhere).
- User has all Kadaku missions meeting the above.

Default input: data/aar_records.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opscribe.constants import KADAKU_CAMPAIGN_REQUIRED_MISSIONS


def _canonical_mission_name(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"<@&\d+>", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _iter_records(payload: object):
    if isinstance(payload, dict):
        for record in payload.values():
            if isinstance(record, dict):
                yield record
        return
    if isinstance(payload, list):
        for record in payload:
            if isinstance(record, dict):
                yield record


def _record_has_black_laurels(record: dict) -> bool:
    return bool(
        record.get("black_laurels_in_mission")
        or record.get("black_laurels_in_difficulty")
        or record.get("black_laurels_mentioned_elsewhere")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Find Distinguished Kadaku candidates from AAR JSON.")
    parser.add_argument(
        "--aar-path",
        default="data/aar_records.json",
        help="Path to AAR records JSON (default: data/aar_records.json)",
    )
    args = parser.parse_args()

    aar_path = Path(args.aar_path)
    if not aar_path.exists():
        raise SystemExit(f"AAR file not found: {aar_path}")

    with aar_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    required = set(KADAKU_CAMPAIGN_REQUIRED_MISSIONS)
    missions_by_user: dict[str, set[str]] = {}

    for record in _iter_records(payload):
        mission = _canonical_mission_name(record.get("mission") or record.get("mission_name"))
        if mission not in required:
            continue
        if not record.get("leviathan_protocol_in_mission"):
            continue
        if not _record_has_black_laurels(record):
            continue

        for uid in (record.get("brother_ids") or []):
            uid_str = str(uid).strip()
            if not uid_str:
                continue
            missions_by_user.setdefault(uid_str, set()).add(mission)

    eligible = [uid for uid, missions in missions_by_user.items() if missions >= required]

    def _sort_key(uid: str):
        try:
            return (0, int(uid))
        except Exception:
            return (1, uid)

    for uid in sorted(eligible, key=_sort_key):
        print(uid)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
