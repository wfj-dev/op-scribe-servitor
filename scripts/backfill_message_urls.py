#!/usr/bin/env python3
"""Backfill `message_url` for existing AAR records.

Usage:
  python3 scripts/backfill_message_urls.py --guild-id GUILDID --channel-id CHANNELID [--commit]

If --commit is omitted the script runs in dry-run mode and only reports what
would be changed.
"""
import argparse
import json
import os
import shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT, "data")
AAR_PATH = os.path.join(DATA_DIR, "aar_records.json")


def build_url(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def load_records(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_records(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--guild-id", required=True)
    p.add_argument("--channel-id", required=True)
    p.add_argument("--commit", action="store_true", help="Write changes back to disk")
    args = p.parse_args()

    if not os.path.exists(AAR_PATH):
        print(f"AAR records file not found: {AAR_PATH}")
        return

    data = load_records(AAR_PATH)
    updated = 0
    total = 0
    for key, rec in list(data.items()):
        total += 1
        # message id should be the aar id (stored as int or in key)
        mid = None
        if isinstance(rec, dict) and rec.get("aar_id"):
            mid = str(rec.get("aar_id"))
        else:
            mid = str(key)
        current = rec.get("message_url") if isinstance(rec, dict) else None
        desired = build_url(args.guild_id, args.channel_id, mid)
        if current != desired:
            print(f"Will update {mid}:\n  from: {current}\n  to:   {desired}\n")
            updated += 1
            if args.commit:
                if isinstance(rec, dict):
                    rec["message_url"] = desired
                else:
                    data[key] = {"message_url": desired}

    print(f"Processed {total} records, {updated} to update.")
    if args.commit and updated > 0:
        # backup
        bak = AAR_PATH + ".backup." + datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        shutil.copyfile(AAR_PATH, bak)
        save_records(AAR_PATH, data)
        print(f"Wrote changes and created backup: {bak}")
    elif not args.commit:
        print("Dry run only; re-run with --commit to apply changes.")


if __name__ == "__main__":
    main()
