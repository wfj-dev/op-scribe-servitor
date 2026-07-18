"""Unit tests for forge acknowledgment helpers.

Covers:
- _get_techmarine_acknowledgment_blended:
    * Rank detection picks the highest-priority matching rank
    * Falls back to Watch Brother when no rank matches
    * With very high rank weight the result is drawn from rank acknowledgments
    * With very high stud weight the result is drawn from stud acknowledgments
    * Tier thresholds: <=3 → tier 1, 4-11 → tier 2, >=12 → tier 3
"""

import sys
import unittest.mock

from opscribe.bot import (
    _get_techmarine_acknowledgment_blended,
    TECHMARINE_RANK_ACKNOWLEDGMENTS,
    TECHMARINE_STUDS_ACKNOWLEDGMENT,
)

forge_ops = sys.modules["opscribe.forge_ops"]


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
