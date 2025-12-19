import json
import os
from datetime import datetime

TARGET_NAME = "Watch Chaplain Staffy"
TARGET_ID = "933789838136717414"

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "aar_records.json")
DATA_PATH = os.path.abspath(DATA_PATH)


def normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def main():
    # Backup
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = DATA_PATH.replace("aar_records.json", f"aar_records.backup.{ts}.json")

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    updates = 0
    records_touched = 0

    # Expecting top-level mapping of AAR ID -> record dict
    for aar_key, rec in data.items():
        names = rec.get("brother_names")
        ids = rec.get("broder_ids")  # common typo guard (if any)
        if ids is None:
            ids = rec.get("brother_ids")

        if not isinstance(names, list) or not isinstance(ids, list):
            continue

        changed_this_record = False
        # Keep lengths safe
        limit = min(len(names), len(ids))
        for i in range(limit):
            if normalize_name(names[i]) == normalize_name(TARGET_NAME):
                if ids[i] != TARGET_ID:
                    ids[i] = TARGET_ID
                    updates += 1
                    changed_this_record = True
        if changed_this_record:
            # Reassign list back to handle the case we modified a temporary ref
            rec["brother_ids"] = ids
            records_touched += 1

    # Persist
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "backup": os.path.basename(backup_path),
        "records_touched": records_touched,
        "ids_updated": updates,
        "target_name": TARGET_NAME,
        "target_id": TARGET_ID
    }, indent=2))


if __name__ == "__main__":
    main()
