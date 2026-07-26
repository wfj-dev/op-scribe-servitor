#!/usr/bin/env python3
"""Backfill strike-directive linkage between target_packages and aar_records.

What this fixes:
- For completed directives with an aar_link, set missing package aar_record_id/aar_message_id.
- On the matching AAR record, ensure target_package_id and target_package_ids include the package id.

Usage:
  python3 scripts/backfill_sd_linkage.py
  python3 scripts/backfill_sd_linkage.py --commit
  python3 scripts/backfill_sd_linkage.py --data-dir /path/to/data --commit

Behavior:
- Dry run by default (no writes).
- With --commit, writes both JSON files and creates timestamped backups first.
"""

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")

DISCORD_MSG_URL_RE = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)$",
    re.IGNORECASE,
)


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _extract_message_id(aar_link: str) -> str:
    if not aar_link:
        return ""
    m = DISCORD_MSG_URL_RE.match(aar_link.strip())
    if not m:
        return ""
    return str(m.group(2))


def backfill(aar: dict, tp: dict) -> Tuple[dict, dict, dict]:
    packages: Dict[str, dict] = tp.get("packages") or {}

    completed_with_link = 0
    unmatched_links: List[str] = []
    pkg_record_ids_set = 0
    pkg_message_ids_set = 0
    aar_single_set = 0
    aar_list_appends = 0

    for pkg_id, pkg in packages.items():
        try:
            status = str(pkg.get("status") or "")
            aar_link = str(pkg.get("aar_link") or "").strip()
        except Exception:
            continue

        if status != "completed" or not aar_link:
            continue

        completed_with_link += 1
        message_id = _extract_message_id(aar_link)
        if not message_id:
            unmatched_links.append(str(pkg_id))
            continue

        rec = aar.get(message_id)
        if not isinstance(rec, dict):
            unmatched_links.append(str(pkg_id))
            continue

        if not pkg.get("aar_record_id"):
            pkg["aar_record_id"] = str(message_id)
            pkg_record_ids_set += 1

        if not pkg.get("aar_message_id"):
            pkg["aar_message_id"] = str(message_id)
            pkg_message_ids_set += 1

        if not rec.get("target_package_id"):
            rec["target_package_id"] = str(pkg_id)
            aar_single_set += 1

        pkg_ids = [str(x) for x in (rec.get("target_package_ids") or []) if x]
        if str(pkg_id) not in pkg_ids:
            pkg_ids.append(str(pkg_id))
            rec["target_package_ids"] = pkg_ids
            aar_list_appends += 1

        aar[str(message_id)] = rec

    tp["packages"] = packages

    linked_records = sum(
        1
        for rec in aar.values()
        if isinstance(rec, dict) and (rec.get("target_package_id") or (rec.get("target_package_ids") or []))
    )

    report = {
        "completed_with_aar_link": completed_with_link,
        "unmatched_links": unmatched_links,
        "pkg_record_ids_set": pkg_record_ids_set,
        "pkg_message_ids_set": pkg_message_ids_set,
        "aar_single_set": aar_single_set,
        "aar_list_appends": aar_list_appends,
        "aar_records_with_any_sd_linkage": linked_records,
    }
    return aar, tp, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill strike-directive AAR linkage fields.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Path to data directory")
    parser.add_argument("--commit", action="store_true", help="Write changes to disk")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    aar_path = os.path.join(data_dir, "aar_records.json")
    tp_path = os.path.join(data_dir, "target_packages.json")

    if not os.path.exists(aar_path):
        raise FileNotFoundError(f"Missing file: {aar_path}")
    if not os.path.exists(tp_path):
        raise FileNotFoundError(f"Missing file: {tp_path}")

    aar = _load_json(aar_path)
    tp = _load_json(tp_path)

    aar_out, tp_out, report = backfill(aar, tp)

    print("Backfill report")
    print("-------------")
    print(f"completed_with_aar_link: {report['completed_with_aar_link']}")
    print(f"pkg_record_ids_set: {report['pkg_record_ids_set']}")
    print(f"pkg_message_ids_set: {report['pkg_message_ids_set']}")
    print(f"aar_single_set: {report['aar_single_set']}")
    print(f"aar_list_appends: {report['aar_list_appends']}")
    print(f"aar_records_with_any_sd_linkage: {report['aar_records_with_any_sd_linkage']}")

    unmatched = report["unmatched_links"]
    if unmatched:
        print(f"unmatched_links: {len(unmatched)}")
        print("examples:", unmatched[:10])
        print("No files written. Resolve unmatched links and re-run.")
        return

    if not args.commit:
        print("Dry run only. Re-run with --commit to write changes.")
        return

    stamp = _timestamp()
    aar_bak = os.path.join(data_dir, f"aar_records.backup.pre_sd_backfill.{stamp}.json")
    tp_bak = os.path.join(data_dir, f"target_packages.backup.pre_sd_backfill.{stamp}.json")
    shutil.copy2(aar_path, aar_bak)
    shutil.copy2(tp_path, tp_bak)

    _save_json(aar_path, aar_out)
    _save_json(tp_path, tp_out)

    print("Wrote files:")
    print(f"  {aar_path}")
    print(f"  {tp_path}")
    print("Backups:")
    print(f"  {aar_bak}")
    print(f"  {tp_bak}")


if __name__ == "__main__":
    main()
