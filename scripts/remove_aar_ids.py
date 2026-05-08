#!/usr/bin/env python3
"""Remove AAR id(s) from data/aar_records.json and data/processed_ids.json.

Usage examples:
  python scripts/remove_aar_ids.py 1467727365659562242
  python scripts/remove_aar_ids.py --file ids.txt --yes
  python scripts/remove_aar_ids.py 123 456 --dry-run

This script makes timestamped backups before writing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
from pathlib import Path
from typing import Iterable, List


ROOT = Path(__file__).resolve().parents[1]
AAR_FILE = ROOT / "data" / "aar_records.json"
PROCESSED_FILE = ROOT / "data" / "processed_ids.json"


def timestamp() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, obj):
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".backup.{timestamp()}")
    shutil.copy2(path, bak)
    return bak


def parse_ids_from_file(path: Path) -> List[str]:
    ids: List[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            ids.append(s)
    return ids


def normalize_ids(items: Iterable[str]) -> List[str]:
    return [str(i).strip() for i in items if str(i).strip()]


def remove_ids(aar_ids: List[str], do_write: bool) -> dict:
    result = {
        "aar_removed": [],
        "processed_removed": [],
        "aar_present": [],
        "processed_present": [],
    }

    aar = load_json(AAR_FILE)
    processed = load_json(PROCESSED_FILE)

    # aar_records.json is a dict keyed by id strings
    for aid in aar_ids:
        if aid in aar:
            result["aar_present"].append(aid)
        if aid in processed:
            result["processed_present"].append(aid)

    # perform removals
    if do_write:
        backup(AAR_FILE)
        backup(PROCESSED_FILE)

        for aid in result["aar_present"]:
            aar.pop(aid, None)
            result["aar_removed"].append(aid)

        # processed is a list of string ids
        new_processed = [p for p in processed if p not in aar_ids]
        removed = [p for p in processed if p not in new_processed]
        result["processed_removed"].extend(removed)

        write_json(AAR_FILE, aar)
        write_json(PROCESSED_FILE, new_processed)

    return result


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Remove AAR id(s) from records and processed list")
    ap.add_argument("ids", nargs="*", help="AAR id(s) to remove")
    ap.add_argument("--file", "-f", type=Path, help="file with one id per line")
    ap.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="apply changes (create backups and write files)",
    )
    ap.add_argument("--dry-run", action="store_true", help="show what would be removed (default)")

    args = ap.parse_args(argv)

    ids: List[str] = []
    if args.file:
        ids.extend(parse_ids_from_file(args.file))
    ids.extend(args.ids or [])
    ids = normalize_ids(ids)

    if not ids:
        ap.error("no ids provided; pass ids or --file")

    do_write = args.yes and not args.dry_run

    print(f"Found {len(ids)} id(s) to remove: {', '.join(ids)}")
    if not do_write:
        print("Running in dry-run mode. No files will be modified. Use --yes to apply.")

    result = remove_ids(ids, do_write=do_write)

    print("\nSummary:")
    print(f"  AAR entries present: {len(result['aar_present'])}")
    print(f"  Processed ids present: {len(result['processed_present'])}")
    if do_write:
        print(f"  AAR removed: {len(result['aar_removed'])}")
        print(f"  Processed removed: {len(result['processed_removed'])}")
        print("Backups were created with suffix .backup.<timestamp>")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
