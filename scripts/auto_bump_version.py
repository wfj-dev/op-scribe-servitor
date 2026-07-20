#!/usr/bin/env python3
"""Auto-bump opscribe package version from commit significance."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

VERSION_FILE = Path("opscribe/__init__.py")
VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$')
CONVENTIONAL_FEAT_RE = re.compile(r"^feat(?:\([^)]+\))?:", re.IGNORECASE)
CONVENTIONAL_BREAKING_RE = re.compile(r"^[a-z]+(?:\([^)]+\))?!:", re.IGNORECASE)


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
    )
    return result.stdout


def _current_version() -> tuple[int, int, int]:
    for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
        match = VERSION_RE.match(line.strip())
        if match:
            return tuple(int(part) for part in match.groups())
    raise RuntimeError(f"Could not find __version__ in {VERSION_FILE}")


def _resolve_range() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]

    before = os.getenv("GITHUB_EVENT_BEFORE", "")
    after = os.getenv("GITHUB_SHA", "")
    if before and after and before != "0" * 40:
        return f"{before}..{after}"

    # For branch-creation pushes where "before" is all zeroes, use the current commit.
    return "HEAD~1..HEAD"


def _commit_messages(revision_range: str) -> list[CommitMessage]:
    try:
        raw = _git("log", "--format=%s%x1f%b%x1e", revision_range)
    except subprocess.CalledProcessError:
        # If HEAD~1 is not available (single-commit history), fall back to HEAD.
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
        if CONVENTIONAL_BREAKING_RE.match(subject) or "BREAKING CHANGE" in body.upper():
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
    revision_range = _resolve_range()
    commits = _commit_messages(revision_range)
    if not commits:
        print("No commits found for range; skipping.")
        return 0

    bump_level = _infer_bump(commits)
    current = _current_version()
    new_version = _bump(current, bump_level)
    _write_version(new_version)

    rendered = f"{new_version[0]}.{new_version[1]}.{new_version[2]}"
    print(f"Bump level: {bump_level}")
    print(f"New version: {rendered}")

    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"new_version={rendered}\n")
            handle.write(f"bump_level={bump_level}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
