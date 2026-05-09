#!/usr/bin/env python3
"""Prune `data/aar_records.json` of entries not present in `data/processed_ids.json`.

Usage:
  python scripts/prune_aar_records.py        # dry-run (shows what would be removed)
  python scripts/prune_aar_records.py --apply --yes

Options:
  --apply    Write changes to `data/aar_records.json` (default: dry-run)
  --yes      Skip confirmation when using --apply
  --backup   Path to write backup (default: data/aar_records.json.YYYYmmddHHMMSS.bak)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PROCESSED_IDS_PATH = DATA_DIR / "processed_ids.json"
AAR_RECORDS_PATH = DATA_DIR / "aar_records.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json_atomic(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def make_backup(path: Path, backup_path: Path | None = None) -> Path:
    if backup_path:
        dest = Path(backup_path)
    else:
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        dest = path.with_suffix(path.suffix + f".{ts}.bak")
    shutil.copy2(path, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prune AAR records not in processed_ids.json")
    p.add_argument("--apply", action="store_true", help="Write changes to aar_records.json")
    p.add_argument("--yes", action="store_true", help="Skip confirmation when applying changes")
    p.add_argument("--backup", help="Explicit backup path")
    args = p.parse_args(argv)

    if not PROCESSED_IDS_PATH.exists():
        print(f"Missing processed ids: {PROCESSED_IDS_PATH}")
        return 2
    if not AAR_RECORDS_PATH.exists():
        print(f"Missing aar records: {AAR_RECORDS_PATH}")
        return 2

    processed = load_json(PROCESSED_IDS_PATH)
    if not isinstance(processed, list):
        print("Unexpected format for processed_ids.json: expected JSON list of strings")
        return 2
    processed_set = set(str(x) for x in processed)

    records = load_json(AAR_RECORDS_PATH)
    if not isinstance(records, dict):
        print("Unexpected format for aar_records.json: expected JSON object mapping ids->record")
        return 2

    record_ids = set(records.keys())
    to_remove = sorted(record_ids - processed_set)
    _keep = sorted(record_ids & processed_set)  # Available for reference but not used in pruning logic

    print(f"Total aar_records: {len(record_ids)}")
    print(f"Processed ids: {len(processed_set)}")
    print(f"Records to remove: {len(to_remove)}")

    if to_remove:
        print("Sample to remove (up to 50):")
        for rid in to_remove[:50]:
            print(" -", rid)
    else:
        print("Nothing to remove.")

    if not args.apply:
        print("Dry-run: no changes made. Rerun with --apply to write changes.")
        return 0

    if args.apply and not args.yes:
        confirm = input("Proceed to remove these records from aar_records.json? [y/N]: ")
        if confirm.lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    # backup
    try:
        backup = make_backup(AAR_RECORDS_PATH, Path(args.backup) if args.backup else None)
        print(f"Backup written to: {backup}")
    except Exception as exc:
        print("Failed to create backup:", exc)
        return 3

    # prune
    new_records = {k: v for k, v in records.items() if k in processed_set}
    try:
        save_json_atomic(AAR_RECORDS_PATH, new_records)
        print(f"Wrote pruned aar_records.json ({len(new_records)} records remain).")
    except Exception as exc:
        print("Failed to write pruned file:", exc)
        print("Attempting to restore backup...")
        try:
            shutil.copy2(backup, AAR_RECORDS_PATH)
            print("Backup restored.")
        except Exception:
            print("Failed to restore backup. Manual intervention required.")
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
