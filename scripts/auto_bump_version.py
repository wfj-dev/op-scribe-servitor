#!/usr/bin/env python3
"""Auto-bump opscribe package version from commit significance."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO_ROOT / "opscribe/__init__.py"
GIT_NULL_SHA = "0" * 40
VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$')
CONVENTIONAL_FEAT_RE = re.compile(r"^feat(?:\([^)]+\))?:", re.IGNORECASE)
CONVENTIONAL_BREAKING_RE = re.compile(r"^[a-z]+(?:\([^)]+\))?!:", re.IGNORECASE)
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:\s+\S", re.MULTILINE)


@dataclass(frozen=True)
class CommitMessage:
    subject: str
    body: str


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    return result.stdout


def _current_version() -> tuple[int, int, int]:
    for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
        match = VERSION_RE.match(line.strip())
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    raise RuntimeError(f"Could not find __version__ in {VERSION_FILE}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-bump opscribe version from commit significance."
    )
    parser.add_argument(
        "revision_range",
        nargs="?",
        help="Git revision range (for example: HEAD~3..HEAD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute bump level/version without modifying files.",
    )
    return parser.parse_args()


def _resolve_range(cli_range: str | None) -> str:
    if cli_range:
        return cli_range

    before = os.getenv("GITHUB_EVENT_BEFORE", "")
    after = os.getenv("GITHUB_SHA", "")
    if before and after and before != GIT_NULL_SHA:
        return f"{before}..{after}"

    # For branch-creation pushes where "before" is all zeroes, use the current commit.
    return "HEAD~1..HEAD"


def _commit_messages(revision_range: str) -> list[CommitMessage]:
    try:
        raw = _git("log", "--format=%s%x1f%b%x1e", revision_range)
    except subprocess.CalledProcessError:
        if revision_range != "HEAD~1..HEAD":
            raise
        # Single-commit history: HEAD~1 is unavailable, fall back to HEAD.
        raw = _git("log", "--format=%s%x1f%b%x1e", "-n", "1", "HEAD")

    messages: list[CommitMessage] = []
    for record in raw.strip("\x1e\n").split("\x1e"):
        if not record.strip():
            continue
        subject, _, body = record.partition("\x1f")
        messages.append(CommitMessage(subject=subject.strip(), body=body.strip()))
    return messages


def _infer_bump(commits: Iterable[CommitMessage]) -> str:
    highest = "patch"
    for commit in commits:
        subject = commit.subject
        body = commit.body
        if CONVENTIONAL_BREAKING_RE.match(subject) or BREAKING_FOOTER_RE.search(body):
            return "major"
        if CONVENTIONAL_FEAT_RE.match(subject):
            highest = "minor"
    return highest


def _bump(version: tuple[int, int, int], bump_level: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if bump_level == "major":
        return (major + 1, 0, 0)
    if bump_level == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def _write_version(new_version: tuple[int, int, int]) -> None:
    replacement = f'__version__ = "{new_version[0]}.{new_version[1]}.{new_version[2]}"'
    updated_lines: list[str] = []
    replaced = False
    for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
        if VERSION_RE.match(line.strip()) and not replaced:
            updated_lines.append(replacement)
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        raise RuntimeError(f"Could not replace __version__ in {VERSION_FILE}")

    VERSION_FILE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    revision_range = _resolve_range(args.revision_range)
    commits = _commit_messages(revision_range)
    if not commits:
        print("No commits found for range; skipping.")
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("new_version=\n")
                handle.write("bump_level=\n")
        return 0

    bump_level = _infer_bump(commits)
    current = _current_version()
    new_version = _bump(current, bump_level)
    if not args.dry_run:
        _write_version(new_version)

    rendered = f"{new_version[0]}.{new_version[1]}.{new_version[2]}"
    print(f"Revision range: {revision_range}")
    print(f"Bump level: {bump_level}")
    print(f"New version: {rendered}")
    if args.dry_run:
        print("Dry run: no file changes written.")

    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"new_version={rendered}\n")
            handle.write(f"bump_level={bump_level}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
