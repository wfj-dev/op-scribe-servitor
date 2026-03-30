import json
import os
import re
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

    # Targeted replacement: only update the following fields when they exactly match OLD_ID
    def _extract_exact_id(val):
        """Return the normalized ID string if val represents OLD_ID in exact forms, else None.

        Accepts:
          - numeric (int)
          - numeric string '12345'
          - mention strings '<@123>', '<@!123>', '<@&123>'
        Returns normalized id string (digits) or None.
        """
        if val is None:
            return None
        # ints
        try:
            if isinstance(val, int) and int(val) == int(OLD_ID):
                return str(OLD_ID)
        except Exception:
            pass
        # strings
        if isinstance(val, str):
            s = val.strip()
            # pure digits
            if s.isdigit():
                try:
                    if int(s) == int(OLD_ID):
                        return str(OLD_ID)
                except Exception:
                    pass
            # mentions
            m = re.match(r"^<@!?(\d+)>$", s)
            if m and m.group(1) == OLD_ID:
                return str(OLD_ID)
            mr = re.match(r"^<@&(\d+)>$", s)
            if mr and mr.group(1) == OLD_ID:
                return str(OLD_ID)
        return None

    for _aar_key, rec in data.items():
        if not isinstance(rec, dict):
            continue

        changed = False

        # 1) brother_ids + brother_names
        ids = rec.get("brother_ids")
        names = rec.get("brother_names")
        if isinstance(ids, list) and isinstance(names, list):
            limit = min(len(ids), len(names))
            for i in range(limit):
                try:
                    if _extract_exact_id(ids[i]) == str(OLD_ID):
                        # replace id with NEW_ID (as string)
                        if str(ids[i]) != str(NEW_ID):
                            ids[i] = str(NEW_ID)
                            updates += 1
                            changed = True
                        # update name if replacement available
                        if (
                            isinstance(replacement_name, str)
                            and replacement_name.strip()
                        ):
                            if str(names[i]) != replacement_name:
                                names[i] = replacement_name
                                name_updates += 1
                                changed = True
                except Exception:
                    continue
            if changed:
                rec["brother_ids"] = ids
                rec["brother_names"] = names
                records_touched += 1

        # 2) gene_seed_carrier_id and gene_seed_carried_name
        try:
            gcid = rec.get("gene_seed_carrier_id")
            if _extract_exact_id(gcid) == str(OLD_ID):
                rec["gene_seed_carrier_id"] = str(NEW_ID)
                updates += 1
                changed = True
                if isinstance(replacement_name, str) and replacement_name.strip():
                    if rec.get("gene_seed_carried_name") != replacement_name:
                        rec["gene_seed_carried_name"] = replacement_name
                        name_updates += 1
        except Exception:
            pass

        # 3) initiate_id
        try:
            iid = rec.get("initiate_id")
            if _extract_exact_id(iid) == str(OLD_ID):
                rec["initiate_id"] = str(NEW_ID)
                updates += 1
                changed = True
        except Exception:
            pass

        if changed:
            # persist the mutated record back to data map
            data[_aar_key] = rec

    # Persist
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "backup": os.path.basename(backup_path),
                "records_touched": records_touched,
                "ids_updated": updates,
                "names_updated": name_updates,
                "old_id": OLD_ID,
                "new_id": NEW_ID,
                "new_name": replacement_name or None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
