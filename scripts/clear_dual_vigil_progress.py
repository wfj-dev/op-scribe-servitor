"""
clear_dual_vigil_progress.py

Removes all dual_vigil progress and notifications from challenge_progress.json
so the bot will repopulate from AARs and fire the Order of the Aquiline Brotherhood
award announcement on next processing.
"""

import json
import shutil
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "challenge_progress.json"
BACKUP_FILE = DATA_FILE.with_suffix(".json.bak")


def main() -> None:
    data = json.loads(DATA_FILE.read_text())

    removed_progress = 0
    removed_notified = 0

    for user_id, entry in data.items():
        if "dual_vigil" in entry:
            del entry["dual_vigil"]
            removed_progress += 1
            print(f"  Cleared dual_vigil progress for {entry.get('display_name', user_id)}")

        if "dual_vigil" in entry.get("notified", []):
            entry["notified"].remove("dual_vigil")
            removed_notified += 1
            print(f"  Cleared dual_vigil from notified for {entry.get('display_name', user_id)}")

    # Write backup then save
    shutil.copy2(DATA_FILE, BACKUP_FILE)
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"\nDone. Removed progress entries: {removed_progress}, notified flags: {removed_notified}")
    print(f"Backup written to {BACKUP_FILE}")


if __name__ == "__main__":
    main()
