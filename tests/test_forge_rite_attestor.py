"""Unit tests for _find_responsible_attestor and _get_techmarine_acknowledgment_blended.

Covers:
- _find_responsible_attestor:
    * High Command bearer → Forgemaster blesses
    * Techmarine bearer → Forgemaster blesses
    * Bearer with company → Company's Techmarine blesses
    * Multiple techmarines in company → one is chosen (random selection)
    * No company → Forgemaster fallback
    * No Techmarine for company → Forgemaster fallback
    * No Forgemaster found → returns (None, 'forgemaster')

- _get_techmarine_acknowledgment_blended:
    * Rank detection picks the highest-priority matching rank
    * Falls back to Watch Brother when no rank matches
    * With very high rank weight the result is drawn from rank acknowledgments
    * With very high stud weight the result is drawn from stud acknowledgments
    * Tier thresholds: <=3 → tier 1, 4-11 → tier 2, >=12 → tier 3
"""

import unittest.mock

from opscribe.bot import (
    _find_responsible_attestor,
    _get_techmarine_acknowledgment_blended,
    HIGH_COMMAND_ROLES,
    TECHMARINE_RANK_ACKNOWLEDGMENTS,
    TECHMARINE_STUDS_ACKNOWLEDGMENT,
)


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


# ---------------------------------------------------------------------------
# _find_responsible_attestor – High Command bearer
# ---------------------------------------------------------------------------


def test_high_command_bearer_selects_forgemaster():
    """A High Command bearer should be blessed by the Forgemaster."""
    # Pick one role from HIGH_COMMAND_ROLES that is NOT 'Forgemaster'
    hc_role = next(r for r in HIGH_COMMAND_ROLES if r != "Forgemaster")
    bearer = FakeMember(1, [hc_role])
    forgemaster = FakeMember(2, ["Forgemaster"])
    guild = FakeGuild([bearer, forgemaster])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is forgemaster
    assert role_key == "forgemaster"


def test_high_command_bearer_no_forgemaster_returns_none():
    """If bearer is High Command and no Forgemaster exists, return (None, 'forgemaster')."""
    hc_role = next(r for r in HIGH_COMMAND_ROLES if r != "Forgemaster")
    bearer = FakeMember(1, [hc_role])
    guild = FakeGuild([bearer])  # no forgemaster

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is None
    assert role_key == "forgemaster"


def test_watch_captain_bearer_selects_forgemaster():
    """Watch Captain is High Command and should be blessed by the Forgemaster."""
    assert "Watch Captain" in HIGH_COMMAND_ROLES, "Watch Captain must be in HIGH_COMMAND_ROLES"
    bearer = FakeMember(1, ["Watch Captain", "Watch Company Primus"])
    company_tech = FakeMember(2, ["Watch Techmarine", "Watch Company Primus"])
    forgemaster = FakeMember(3, ["Forgemaster"])
    guild = FakeGuild([bearer, company_tech, forgemaster])

    # Even though there's a company techmarine, Watch Captain gets Forgemaster
    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is forgemaster
    assert role_key == "forgemaster"


# ---------------------------------------------------------------------------
# _find_responsible_attestor – Techmarine bearer
# ---------------------------------------------------------------------------


def test_techmarine_bearer_selects_forgemaster():
    """A Techmarine bearer should be blessed by the Forgemaster."""
    bearer = FakeMember(1, ["Watch Techmarine"])
    forgemaster = FakeMember(2, ["Forgemaster"])
    guild = FakeGuild([bearer, forgemaster])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is forgemaster
    assert role_key == "forgemaster"


def test_techmarine_bearer_no_forgemaster_returns_none():
    """Techmarine bearer with no Forgemaster in guild → (None, 'forgemaster')."""
    bearer = FakeMember(1, ["Watch Techmarine"])
    guild = FakeGuild([bearer])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is None
    assert role_key == "forgemaster"


# ---------------------------------------------------------------------------
# _find_responsible_attestor – Company Techmarine
# ---------------------------------------------------------------------------


def test_company_bearer_selects_company_techmarine():
    """A bearer in a company should be blessed by that company's Techmarine."""
    bearer = FakeMember(1, ["Watch Brother", "Watch Company Primus"])
    company_tech = FakeMember(2, ["Watch Techmarine", "Watch Company Primus"])
    guild = FakeGuild([bearer, company_tech])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is company_tech
    assert role_key == "techmarine"


def test_company_bearer_different_company_tech_not_selected():
    """A bearer should not get a Techmarine from a different company."""
    bearer = FakeMember(1, ["Watch Brother", "Watch Company Primus"])
    other_tech = FakeMember(2, ["Watch Techmarine", "Watch Company Secundus"])
    forgemaster = FakeMember(3, ["Forgemaster"])
    guild = FakeGuild([bearer, other_tech, forgemaster])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is forgemaster
    assert role_key == "forgemaster"


def test_multiple_company_techmarines_one_is_chosen():
    """When multiple Techmarines share the bearer's company, one is selected."""
    bearer = FakeMember(1, ["Watch Brother", "Watch Company Secundus"])
    tech_a = FakeMember(2, ["Watch Techmarine", "Watch Company Secundus"])
    tech_b = FakeMember(3, ["Watch Techmarine", "Watch Company Secundus"])
    guild = FakeGuild([bearer, tech_a, tech_b])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor in (tech_a, tech_b)
    assert role_key == "techmarine"


def test_multiple_company_techmarines_both_can_be_chosen():
    """Random selection among company Techmarines is exercised; both are reachable."""
    bearer = FakeMember(1, ["Watch Brother", "Watch Company Tertius"])
    tech_a = FakeMember(2, ["Watch Techmarine", "Watch Company Tertius"])
    tech_b = FakeMember(3, ["Watch Techmarine", "Watch Company Tertius"])
    guild = FakeGuild([bearer, tech_a, tech_b])

    chosen = set()
    for _ in range(200):
        attestor, _ = _find_responsible_attestor(bearer, guild)
        chosen.add(attestor.id)
    assert tech_a.id in chosen
    assert tech_b.id in chosen


# ---------------------------------------------------------------------------
# _find_responsible_attestor – Forgemaster fallback
# ---------------------------------------------------------------------------


def test_no_company_bearer_falls_back_to_forgemaster():
    """A bearer with no company role should fall back to the Forgemaster."""
    bearer = FakeMember(1, ["Watch Brother"])  # no company
    forgemaster = FakeMember(2, ["Forgemaster"])
    guild = FakeGuild([bearer, forgemaster])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is forgemaster
    assert role_key == "forgemaster"


def test_no_company_no_forgemaster_returns_none():
    """No company and no Forgemaster → (None, 'forgemaster')."""
    bearer = FakeMember(1, ["Watch Brother"])
    guild = FakeGuild([bearer])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is None
    assert role_key == "forgemaster"


def test_company_bearer_no_company_tech_falls_back_to_forgemaster():
    """Bearer in a company with no matching Techmarine → Forgemaster fallback."""
    bearer = FakeMember(1, ["Watch Brother", "Watch Company Quartus"])
    forgemaster = FakeMember(2, ["Forgemaster"])
    guild = FakeGuild([bearer, forgemaster])

    attestor, role_key = _find_responsible_attestor(bearer, guild)
    assert attestor is forgemaster
    assert role_key == "forgemaster"


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
