"""Unit tests for scripts/auto_bump_version.py bump-logic helpers."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.auto_bump_version import (
    CommitMessage,
    _bump,
    _commit_messages,
    _current_version,
    _infer_bump,
)


# ---------------------------------------------------------------------------
# _infer_bump
# ---------------------------------------------------------------------------


def _cm(subject: str, body: str = "") -> CommitMessage:
    return CommitMessage(subject=subject, body=body)


class TestInferBump:
    def test_no_commits_returns_patch(self):
        assert _infer_bump([]) == "patch"

    def test_fix_commit_returns_patch(self):
        assert _infer_bump([_cm("fix: correct off-by-one")]) == "patch"

    def test_chore_commit_returns_patch(self):
        assert _infer_bump([_cm("chore: update deps")]) == "patch"

    def test_feat_commit_returns_minor(self):
        assert _infer_bump([_cm("feat: add new endpoint")]) == "minor"

    def test_feat_with_scope_returns_minor(self):
        assert _infer_bump([_cm("feat(api): add new endpoint")]) == "minor"

    def test_feature_prefix_returns_minor(self):
        assert _infer_bump([_cm("feature: add new endpoint")]) == "minor"

    def test_perf_commit_returns_minor(self):
        assert _infer_bump([_cm("perf: optimize parser hot path")]) == "minor"

    def test_plain_language_subject_returns_minor(self):
        assert _infer_bump([_cm("Add resilient retry handling")]) == "minor"

    def test_breaking_bang_subject_returns_major(self):
        assert _infer_bump([_cm("feat!: drop Python 3.9")]) == "major"

    def test_breaking_bang_with_scope_returns_major(self):
        assert _infer_bump([_cm("fix(auth)!: change token format")]) == "major"

    def test_breaking_change_footer_returns_major(self):
        body = "BREAKING CHANGE: old API removed"
        assert _infer_bump([_cm("feat: revamp", body)]) == "major"

    def test_breaking_hyphen_footer_returns_major(self):
        body = "BREAKING-CHANGE: old API removed"
        assert _infer_bump([_cm("feat: revamp", body)]) == "major"

    def test_breaking_change_phrase_in_text_does_not_trigger_major(self):
        # "no breaking changes" must NOT trigger major
        body = "This commit introduces no breaking changes to the interface."
        assert _infer_bump([_cm("feat: minor improvement", body)]) == "minor"

    def test_non_breaking_phrase_does_not_trigger_major(self):
        body = "This is a non-breaking change and should stay compatible."
        assert _infer_bump([_cm("fix: adjust response schema", body)]) == "patch"

    def test_breaking_hint_phrase_in_subject_returns_major(self):
        assert _infer_bump([_cm("breaking change: remove legacy endpoint")]) == "major"

    def test_backward_incompatible_phrase_in_body_returns_major(self):
        body = "This migration is backward incompatible for old clients."
        assert _infer_bump([_cm("refactor: align payload format", body)]) == "major"

    def test_multiple_commits_highest_wins_major(self):
        commits = [
            _cm("fix: typo"),
            _cm("feat: new flag"),
            _cm("fix!: remove deprecated flag"),
        ]
        assert _infer_bump(commits) == "major"

    def test_multiple_commits_highest_wins_minor(self):
        commits = [
            _cm("fix: typo"),
            _cm("feat: new flag"),
            _cm("fix: another typo"),
        ]
        assert _infer_bump(commits) == "minor"

    def test_major_returns_immediately(self):
        """Once major is detected the remaining commits are not needed."""
        commits = [
            _cm("feat!: breaking"),
            _cm("feat: something else"),
        ]
        assert _infer_bump(commits) == "major"

    def test_breaking_footer_requires_colon_and_value(self):
        # "BREAKING CHANGE" without a colon and value should NOT trigger major
        body = "Mention of BREAKING CHANGE without footer format"
        assert _infer_bump([_cm("fix: something", body)]) == "patch"


# ---------------------------------------------------------------------------
# _bump
# ---------------------------------------------------------------------------


class TestBump:
    def test_patch_increments_patch(self):
        assert _bump((1, 2, 3), "patch") == (1, 2, 4)

    def test_minor_increments_minor_resets_patch(self):
        assert _bump((1, 2, 3), "minor") == (1, 3, 0)

    def test_major_increments_major_resets_minor_and_patch(self):
        assert _bump((1, 2, 3), "major") == (2, 0, 0)

    def test_patch_from_zero(self):
        assert _bump((0, 0, 0), "patch") == (0, 0, 1)

    def test_minor_from_zero(self):
        assert _bump((0, 0, 0), "minor") == (0, 1, 0)

    def test_major_from_zero(self):
        assert _bump((0, 0, 0), "major") == (1, 0, 0)


# ---------------------------------------------------------------------------
# _current_version
# ---------------------------------------------------------------------------


class TestCurrentVersion:
    def test_reads_version_from_file(self, tmp_path: Path):
        init_py = tmp_path / "opscribe" / "__init__.py"
        init_py.parent.mkdir()
        init_py.write_text('__version__ = "3.7.1"\n', encoding="utf-8")

        with patch("scripts.auto_bump_version.VERSION_FILE", init_py):
            assert _current_version() == (3, 7, 1)

    def test_raises_when_version_missing(self, tmp_path: Path):
        init_py = tmp_path / "__init__.py"
        init_py.write_text("# no version here\n", encoding="utf-8")

        with patch("scripts.auto_bump_version.VERSION_FILE", init_py):
            with pytest.raises(RuntimeError, match="Could not find __version__"):
                _current_version()

    def test_ignores_non_version_lines(self, tmp_path: Path):
        init_py = tmp_path / "__init__.py"
        init_py.write_text(
            textwrap.dedent("""\
                # comment
                __author__ = "Alice"
                __version__ = "2.0.0"
                __all__ = []
            """),
            encoding="utf-8",
        )

        with patch("scripts.auto_bump_version.VERSION_FILE", init_py):
            assert _current_version() == (2, 0, 0)


# ---------------------------------------------------------------------------
# _commit_messages – fallback behaviour
# ---------------------------------------------------------------------------


class TestCommitMessagesFallback:
    def test_reraises_for_non_default_range(self):
        err = subprocess.CalledProcessError(128, ["git"])
        with patch("scripts.auto_bump_version._git", side_effect=err):
            with pytest.raises(subprocess.CalledProcessError):
                _commit_messages("abc123..def456")

    def test_fallback_used_for_head_range(self):
        err = subprocess.CalledProcessError(128, ["git"])
        fallback_raw = "fix: single commit\x1f\x1e"

        def fake_git(*args: str) -> str:
            if "HEAD~1..HEAD" in args:
                raise err
            return fallback_raw

        with patch("scripts.auto_bump_version._git", side_effect=fake_git):
            msgs = _commit_messages("HEAD~1..HEAD")
        assert len(msgs) == 1
        assert msgs[0].subject == "fix: single commit"
