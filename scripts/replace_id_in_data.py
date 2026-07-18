from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OLD_ID = "1444810056821637133"
DEFAULT_NEW_ID = "1527827234486747150"


@dataclass
class FileChange:
    path: Path
    replacements: int


def _iter_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and ".bak." not in p.name
    )


def _replace_in_file(path: Path, old: str, new: str, *, backup: bool, stamp: str) -> int:
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count == 0:
        return 0

    if backup:
        backup_path = path.with_name(f"{path.name}.bak.{stamp}")
        backup_path.write_text(content, encoding="utf-8")

    path.write_text(content.replace(old, new), encoding="utf-8")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace one ID with another across all UTF-8 files under data/."
    )
    parser.add_argument("--old-id", default=DEFAULT_OLD_ID, help="ID value to replace")
    parser.add_argument("--new-id", default=DEFAULT_NEW_ID, help="Replacement ID value")
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).resolve().parents[1] / "data"),
        help="Path to data directory",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create per-file backups",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists() or not data_dir.is_dir():
        raise SystemExit(f"data directory not found: {data_dir}")

    if args.old_id == args.new_id:
        raise SystemExit("old-id and new-id are identical; nothing to do")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = not args.no_backup

    scanned = 0
    skipped_non_utf8 = 0
    total_replacements = 0
    changed: list[FileChange] = []

    for path in _iter_files(data_dir):
        scanned += 1
        try:
            replacements = _replace_in_file(
                path, args.old_id, args.new_id, backup=backup, stamp=stamp
            )
        except UnicodeDecodeError:
            skipped_non_utf8 += 1
            continue

        if replacements:
            total_replacements += replacements
            changed.append(FileChange(path=path, replacements=replacements))

    print(f"Data dir: {data_dir}")
    print(f"Scanned files: {scanned}")
    print(f"Changed files: {len(changed)}")
    print(f"Total replacements: {total_replacements}")
    print(f"Skipped non-UTF-8 files: {skipped_non_utf8}")
    print(f"Backups created: {backup}")

    if changed:
        print("\nChanged file details:")
        for item in changed:
            print(f"- {item.path}: {item.replacements}")


if __name__ == "__main__":
    main()