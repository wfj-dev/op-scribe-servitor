#!/usr/bin/env python3

import os
import json
import argparse
from datetime import datetime
from collections import OrderedDict


def _parse_iso_timestamp(ts: str) -> float:
    if not ts:
        return 0.0
    s = str(ts)
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        # Handle trailing 'Z' or minor variants
        try:
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0


def _read_json_dict(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _atomic_write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def sort_records(
    input_path: str, output_path: str | None, order: str, dry_run: bool, backup: bool
):
    data = _read_json_dict(input_path)
    if not data:
        print(f"No records found in {input_path} or file is invalid.")
        return 0

    items = []
    for key, rec in data.items():
        ts = _parse_iso_timestamp(rec.get("timestamp"))
        items.append((key, rec, ts))

    reverse = order == "desc"
    items.sort(key=lambda t: t[2], reverse=reverse)

    ordered = OrderedDict()
    for key, rec, _ts in items:
        ordered[str(key)] = rec

    if dry_run:
        print(f"Dry-run: {len(items)} records sorted ({order}). No files changed.")
        if items:
            first_ts = items[0][2]
            last_ts = items[-1][2]
            print(f"First timestamp: {first_ts} | Last timestamp: {last_ts}")
        return len(items)

    target_path = output_path or input_path
    # Optional backup
    if backup and target_path == input_path:
        backup_path = os.path.join(
            os.path.dirname(input_path), "aar_records.backup.json"
        )
        _atomic_write_json(backup_path, data)
        print(f"Backup written to {backup_path}")

    _atomic_write_json(target_path, ordered)
    print(
        f"Sorted records written to {target_path} ({len(items)} entries, order={order})."
    )
    return len(items)


def main():
    parser = argparse.ArgumentParser(description="Sort AAR records JSON by timestamp.")
    parser.add_argument(
        "--input",
        default=os.path.join("data", "aar_records.json"),
        help="Input JSON path (default: data/aar_records.json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. If omitted, writes in-place.",
    )
    parser.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="desc",
        help="Sort order (asc or desc). Default: desc",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not modify files; just report the sort.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Write a backup alongside the input before in-place sorting.",
    )

    args = parser.parse_args()
    sort_records(args.input, args.output, args.order, args.dry_run, args.backup)


if __name__ == "__main__":
    main()
