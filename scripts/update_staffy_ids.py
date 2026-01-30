import json
import os
from collections import Counter
from datetime import datetime

# Remap all occurrences of OLD_ID to NEW_ID and update the paired brother name
# to the name associated with NEW_ID (discovered from data) or a provided hint.
OLD_ID = "933789838136717414"
NEW_ID = "376657426046517250"

# Optional override: if you know the preferred display name for NEW_ID, set here.
# If None, the script will try to discover the most common associated name for NEW_ID
# from existing records; if still unknown, it preserves the existing name at that index.
NEW_NAME_HINT: str | None = None

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "aar_records.json")
DATA_PATH = os.path.abspath(DATA_PATH)


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def _discover_name_for_id(data: dict, target_id: str) -> str | None:
    """Find the most common brother name paired with target_id across records."""
    names_seen: Counter[str] = Counter()
    for _aar_key, rec in data.items():
        names = rec.get("brother_names")
        ids = rec.get("broder_ids") or rec.get("brother_ids")
        if not isinstance(names, list) or not isinstance(ids, list):
            continue
        limit = min(len(names), len(ids))
        for i in range(limit):
            try:
                if str(ids[i]) == str(target_id):
                    nm = str(names[i]) if i < len(names) else None
                    if nm and nm.strip():
                        names_seen[nm.strip()] += 1
            except Exception:
                continue
    if not names_seen:
        return None
    # Return the most common name
    return names_seen.most_common(1)[0][0]


def main():
    # Backup
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = DATA_PATH.replace("aar_records.json", f"aar_records.backup.{ts}.json")

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Resolve the replacement name once (prefer hint, else discover from data)
    replacement_name = NEW_NAME_HINT or _discover_name_for_id(data, NEW_ID)

    updates = 0
    name_updates = 0
    records_touched = 0

    # Expecting top-level mapping of AAR ID -> record dict
    def _replace_in_obj(obj):
        """Recursively replace occurrences of OLD_ID with NEW_ID in obj.

        For strings, perform substring replacement; for numbers, compare numeric equality.
        Returns (new_obj, count_replacements).
        """
        cnt = 0
        if isinstance(obj, dict):
            newd = {}
            for k, v in obj.items():
                # Replace in key (but avoid changing top-level AAR key elsewhere)
                newk = k.replace(OLD_ID, NEW_ID) if isinstance(k, str) else k
                newv, c = _replace_in_obj(v)
                cnt += c
                newd[newk] = newv
            return newd, cnt
        if isinstance(obj, list):
            newl = []
            for item in obj:
                new_item, c = _replace_in_obj(item)
                cnt += c
                newl.append(new_item)
            return newl, cnt
        if isinstance(obj, str):
            if OLD_ID in obj:
                new_s = obj.replace(OLD_ID, NEW_ID)
                return new_s, obj.count(OLD_ID)
            return obj, 0
        if isinstance(obj, int):
            try:
                if int(obj) == int(OLD_ID):
                    return str(NEW_ID), 1
            except Exception:
                pass
            return obj, 0
        # Other types: leave unchanged
        return obj, 0

    for _aar_key, rec in data.items():
        if not isinstance(rec, dict):
            continue

        changed = False
        # Perform deep replacement within the record (but do NOT change the top-level key)
        new_rec, repl_count = _replace_in_obj(rec)
        if repl_count > 0:
            data[_aar_key] = new_rec
            updates += repl_count
            changed = True

        # If brother_ids/brother_names exist, ensure brother_names updated for replaced ids
        try:
            ids = new_rec.get("brother_ids") or []
            names = new_rec.get("brother_names") or []
            if isinstance(ids, list) and isinstance(names, list):
                limit = min(len(ids), len(names))
                for i in range(limit):
                    try:
                        if str(ids[i]) == str(NEW_ID):
                            # If user provided a hint or discovery, apply it
                            if isinstance(replacement_name, str) and replacement_name.strip():
                                if str(names[i]) != replacement_name:
                                    names[i] = replacement_name
                                    name_updates += 1
                                    changed = True
                    except Exception:
                        continue
                if changed:
                    data[_aar_key]["brother_ids"] = ids
                    data[_aar_key]["brother_names"] = names
                    records_touched += 1
        except Exception:
            pass

    # Persist
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "backup": os.path.basename(backup_path),
        "records_touched": records_touched,
        "ids_updated": updates,
        "names_updated": name_updates,
        "old_id": OLD_ID,
        "new_id": NEW_ID,
        "new_name": replacement_name or None
    }, indent=2))


if __name__ == "__main__":
    main()
