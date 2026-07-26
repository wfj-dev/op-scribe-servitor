"""Unit tests for forge acknowledgment helpers.

Covers:
- _get_techmarine_acknowledgment_blended:
    * Rank detection picks the highest-priority matching rank
    * Falls back to Watch Brother when no rank matches
    * With very high rank weight the result is drawn from rank acknowledgments
    * With very high stud weight the result is drawn from stud acknowledgments
    * Tier thresholds: <=3 → tier 1, 4-11 → tier 2, >=12 → tier 3
- armor submission cooldown helpers:
    * legacy single-timestamp storage remains readable
    * rolling limit allows up to 7 submissions in 7 days
"""

import unittest.mock
from datetime import datetime, timedelta, timezone

from opscribe.bot import (
    _get_techmarine_acknowledgment_blended,
    TECHMARINE_RANK_ACKNOWLEDGMENTS,
    TECHMARINE_STUDS_ACKNOWLEDGMENT,
)
import opscribe.forge_ops as forge_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRole:
    def __init__(self, name: str):
        self.name = name


class FakeMember:
    def __init__(self, member_id: int, role_names):
        self.id = member_id
        self.roles = [FakeRole(n) for n in role_names]
        self.display_name = f"Member{member_id}"


class FakeGuild:
    def __init__(self, members):
        self.members = members


def test_find_company_or_chapter_uses_configured_company_names(monkeypatch):
    member = FakeMember(1, ["Watch Company Sextus", "Watch Brother"])
    monkeypatch.setattr(
        forge_ops._g,
        "CONFIG",
        {"companies": {"sextus": {"name": "Sextus", "companyRoleId": 6001}}},
    )

    assert forge_ops._find_company_or_chapter(member) == "Watch Company Sextus"


# ---------------------------------------------------------------------------
# _get_techmarine_acknowledgment_blended – rank detection
# ---------------------------------------------------------------------------


def test_blended_ack_detects_watch_captain_rank():
    """Rank detection should identify 'Watch Captain' among a member's roles."""
    member = FakeMember(1, ["Watch Captain", "Watch Company Primus"])
    with unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.0):  # force rank path
        result = _get_techmarine_acknowledgment_blended(member, 0)
    assert result in TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Captain"]


def test_blended_ack_detects_watch_master_rank():
    """Highest-priority rank 'Watch Master' is detected and acknowledged."""
    member = FakeMember(1, ["Watch Master", "Watch Brother"])
    with unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.0):
        result = _get_techmarine_acknowledgment_blended(member, 1)
    assert result in TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Master"]


def test_blended_ack_defaults_to_watch_brother_when_no_rank():
    """A member with no recognized rank falls back to Watch Brother acknowledgments."""
    member = FakeMember(1, ["Unknown Role"])
    with unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.0):
        result = _get_techmarine_acknowledgment_blended(member, 1)
    assert result in TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Brother"]


def test_blended_ack_no_roles_falls_back_to_watch_brother():
    """A member with no roles at all falls back to Watch Brother acknowledgments."""
    member = FakeMember(1, [])
    with unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.0):
        result = _get_techmarine_acknowledgment_blended(member, 0)
    assert result in TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Brother"]


# ---------------------------------------------------------------------------
# _get_techmarine_acknowledgment_blended – weight / path selection
# ---------------------------------------------------------------------------


def test_blended_ack_uses_rank_path_when_random_below_prob_rank():
    """When random() < prob_rank, the rank acknowledgment pool is used."""
    member = FakeMember(1, ["Watch Sergeant"])
    # With 0 studs, stud_weight is minimal, so prob_rank is high; force rank path.
    with (
        unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.0),
        unittest.mock.patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        result = _get_techmarine_acknowledgment_blended(member, 0)
    assert result in TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Sergeant"]


def test_blended_ack_uses_stud_path_when_random_above_prob_rank():
    """When random() >= prob_rank, the stud-tier acknowledgment pool is used."""
    member = FakeMember(1, ["Watch Brother"])
    # Watch Brother rank_weight = 0.1, 16 studs → stud_weight = 1.0
    # prob_rank = 0.1 / (0.1 + 1.0) ≈ 0.091
    # random.random = 0.99 → stud path chosen
    with (
        unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.99),
        unittest.mock.patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        result = _get_techmarine_acknowledgment_blended(member, 16)
    # 16 studs → tier 3
    assert result in TECHMARINE_STUDS_ACKNOWLEDGMENT[3]


# ---------------------------------------------------------------------------
# _get_techmarine_acknowledgment_blended – stud tier thresholds
# ---------------------------------------------------------------------------


def test_blended_ack_tier1_for_low_studs():
    """1-3 studs maps to tier 1 stud acknowledgment."""
    member = FakeMember(1, ["Watch Brother"])
    with (
        unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.99),
        unittest.mock.patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        result = _get_techmarine_acknowledgment_blended(member, 3)
    assert result in TECHMARINE_STUDS_ACKNOWLEDGMENT[1]


def test_blended_ack_tier2_for_mid_studs():
    """4-11 studs maps to tier 2 stud acknowledgment."""
    member = FakeMember(1, ["Watch Brother"])
    with (
        unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.99),
        unittest.mock.patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        result = _get_techmarine_acknowledgment_blended(member, 8)
    assert result in TECHMARINE_STUDS_ACKNOWLEDGMENT[2]


def test_blended_ack_tier3_for_high_studs():
    """12+ studs maps to tier 3 stud acknowledgment."""
    member = FakeMember(1, ["Watch Brother"])
    with (
        unittest.mock.patch("opscribe.forge_ops.random.random", return_value=0.99),
        unittest.mock.patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        result = _get_techmarine_acknowledgment_blended(member, 12)
    assert result in TECHMARINE_STUDS_ACKNOWLEDGMENT[3]


def test_blended_ack_returns_string():
    """_get_techmarine_acknowledgment_blended always returns a non-empty string."""
    member = FakeMember(1, ["Watch Veteran"])
    result = _get_techmarine_acknowledgment_blended(member, 8)
    assert isinstance(result, str)
    assert result


def test_armor_submission_recent_timestamps_supports_legacy_single_string(monkeypatch):
    now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(forge_ops, "datetime", _FrozenDateTime)
    state = {
        "last_submit_by_user": {
            "42": (now - timedelta(days=1)).isoformat(),
        }
    }

    recent = forge_ops._armor_submission_recent_timestamps(state, 42)
    assert len(recent) == 1
    assert recent[0] == now - timedelta(days=1)


def test_armor_submission_cooldown_allows_seventh_but_blocks_eighth(monkeypatch):
    now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(forge_ops, "datetime", _FrozenDateTime)
    state = {
        "last_submit_by_user": {
            "42": [
                (now - timedelta(days=6, hours=23)).isoformat(),
                (now - timedelta(days=6)).isoformat(),
                (now - timedelta(days=5)).isoformat(),
                (now - timedelta(days=4)).isoformat(),
                (now - timedelta(days=3)).isoformat(),
                (now - timedelta(days=2)).isoformat(),
            ]
        }
    }

    assert forge_ops._armor_submission_cooldown_remaining(state, 42) is None

    state["last_submit_by_user"]["42"].append((now - timedelta(days=1)).isoformat())
    remaining = forge_ops._armor_submission_cooldown_remaining(state, 42)
    assert remaining is not None
    assert remaining == timedelta(hours=1)


class _FakeAttachment:
    def __init__(self, url: str):
        self.url = url


def test_armor_attachment_field_value_includes_all_links_when_under_limit():
    attachments = [
        _FakeAttachment("https://example.com/a.png"),
        _FakeAttachment("https://example.com/b.png"),
        _FakeAttachment("https://example.com/c.png"),
    ]

    value = forge_ops._build_armor_attachment_field_value(attachments)

    assert "Image 1" in value
    assert "Image 3" in value
    assert "+" not in value
    assert len(value) <= 1024


def test_armor_attachment_field_value_truncates_and_reports_omitted_links():
    long_url = "https://example.com/" + ("a" * 260)
    attachments = [_FakeAttachment(f"{long_url}{idx}.png") for idx in range(1, 11)]

    value = forge_ops._build_armor_attachment_field_value(attachments)
    lines = value.splitlines()

    assert "more image link(s) recorded in submission data" in value
    assert "Image 10" not in value
    assert all(line.endswith(")") or "more image link(s)" in line for line in lines)
    assert len(value) <= 1024
