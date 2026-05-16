"""Unit tests for charge-weighted pressure evaluator demand calculations.

Covers:
- CadrePressure.demand_display property formatting
- evaluate_techmarine_pressure charge-weighted demand:
    * Each damage tier contributes the correct intensive charge cost
    * Fractured (spirit_fractured) always costs 4 regardless of tier
    * Nominal brothers at predictive-warning threshold contribute 1 preventative charge
    * Nominal brothers below threshold contribute 0
    * String config values for at_risk_probability_threshold are coerced to float
- evaluate_librarian_pressure SIR-style background transmission signal:
    * Empty infected population → background_prob == 0, no at-risk contribution
    * Mixed infected population → correct compound-probability calculation
    * background_prob below threshold → no at-risk fractional demand
    * background_prob at or above threshold → clean brothers contribute fractional demand
    * Librarian brothers are excluded from clean-brother count
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import opscribe.bot  # noqa: F401  (ensures module-level setup runs)
from opscribe import forge_ops as fo
from opscribe import librarius_ops as lib
from opscribe import _bot_globals as _g
from opscribe.pressure_registry import CadrePressure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_member(member_id: int, bot: bool = False) -> MagicMock:
    m = MagicMock()
    m.id = member_id
    m.bot = bot
    m.roles = []
    return m


def _make_guild(members, roles=None):
    g = MagicMock()
    g.members = members
    g.roles = roles or []
    g.get_member = lambda uid: next((m for m in members if m.id == uid), None)
    return g


@asynccontextmanager
async def _null_lock():
    """Async context manager that does nothing — replaces asyncio.Lock in tests."""
    yield


# ---------------------------------------------------------------------------
# CadrePressure.demand_display
# ---------------------------------------------------------------------------


def test_demand_display_whole_number():
    c = CadrePressure(cadre_id="x", display_name="X", demand=7.0, supply=4)
    assert c.demand_display == "7"


def test_demand_display_fractional():
    c = CadrePressure(cadre_id="x", display_name="X", demand=3.4, supply=4)
    assert c.demand_display == "3.4"


def test_demand_display_zero():
    c = CadrePressure(cadre_id="x", display_name="X", demand=0.0, supply=4)
    assert c.demand_display == "0"


def test_demand_display_one_tenth():
    c = CadrePressure(cadre_id="x", display_name="X", demand=0.1, supply=4)
    assert c.demand_display == "0.1"


# ---------------------------------------------------------------------------
# evaluate_techmarine_pressure — charge-weighted demand
# ---------------------------------------------------------------------------


def _run_techmarine(armor_data, damage_tiers_by_id, armor_config=None):
    """Run evaluate_techmarine_pressure with mocked guild and armor data.

    ``damage_tiers_by_id`` maps member_id → (damage_tier, spirit_fractured).
    """
    members = [_make_member(mid) for mid in damage_tiers_by_id]
    guild = _make_guild(members)

    def _mock_damage_tier(member):
        return damage_tiers_by_id[member.id][0]

    async def _run():
        with (
            patch.object(fo, "_load_armor_integrity", return_value=armor_data),
            patch.object(fo, "_get_member_damage_tier", side_effect=_mock_damage_tier),
            patch.object(fo, "_get_armor_config", return_value=armor_config or {}),
            patch.object(fo, "_get_techmarine_available_charges", new=AsyncMock(return_value=10)),
            # new=None makes _b("_is_active_participant") return None (falsy),
            # which causes the evaluator to include ALL guild members.
            patch("opscribe.bot._is_active_participant", new=None),
            patch("opscribe.bot.CONFIG", {}),
        ):
            return await fo.evaluate_techmarine_pressure(guild)

    return asyncio.run(_run())


def test_techmarine_demand_nominal_brother_contributes_nothing():
    """A nominal, non-at-risk brother adds zero demand."""
    result = _run_techmarine(
        armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 0}},
        damage_tiers_by_id={1: (None, False)},
    )
    assert result.demand == 0.0


def test_techmarine_demand_damaged_brother_costs_two():
    result = _run_techmarine(
        armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 0}},
        damage_tiers_by_id={1: ("damaged", False)},
    )
    assert result.demand == 2.0


def test_techmarine_demand_compromised_brother_costs_two():
    result = _run_techmarine(
        armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 0}},
        damage_tiers_by_id={1: ("compromised", False)},
    )
    assert result.demand == 2.0


def test_techmarine_demand_critical_brother_costs_three():
    result = _run_techmarine(
        armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 0}},
        damage_tiers_by_id={1: ("critical", False)},
    )
    assert result.demand == 3.0


def test_techmarine_demand_fractured_brother_costs_four():
    """spirit_fractured=True always costs 4 regardless of damage_tier."""
    result = _run_techmarine(
        armor_data={"1": {"spirit_fractured": True, "points_since_blessing": 0}},
        damage_tiers_by_id={1: (None, True)},
    )
    assert result.demand == 4.0


def test_techmarine_demand_critical_plus_fractured_costs_seven():
    """A critical (3) + a fractured (4) brother = 7 combined charges."""
    result = _run_techmarine(
        armor_data={
            "1": {"spirit_fractured": False, "points_since_blessing": 0},
            "2": {"spirit_fractured": True, "points_since_blessing": 0},
        },
        damage_tiers_by_id={1: ("critical", False), 2: (None, True)},
    )
    assert result.demand == 7.0


def test_techmarine_demand_at_risk_nominal_adds_one():
    """A nominal brother with predictive-warning (high points) adds 1 preventative charge."""
    # Patch _get_damage_probability to return a value above threshold
    with patch.object(fo, "_get_damage_probability", return_value=0.25):
        result = _run_techmarine(
            armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 20}},
            damage_tiers_by_id={1: (None, False)},
            armor_config={"at_risk_probability_threshold": 0.20},
        )
    assert result.demand == 1.0


def test_techmarine_demand_below_at_risk_threshold_adds_nothing():
    """A nominal brother below the predictive-warning threshold contributes 0."""
    with patch.object(fo, "_get_damage_probability", return_value=0.05):
        result = _run_techmarine(
            armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 3}},
            damage_tiers_by_id={1: (None, False)},
            armor_config={"at_risk_probability_threshold": 0.20},
        )
    assert result.demand == 0.0


def test_techmarine_demand_at_risk_threshold_string_config_coerced():
    """String config value for at_risk_probability_threshold is safely coerced to float."""
    with patch.object(fo, "_get_damage_probability", return_value=0.25):
        result = _run_techmarine(
            armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 20}},
            damage_tiers_by_id={1: (None, False)},
            armor_config={"at_risk_probability_threshold": "0.20"},  # string from config
        )
    assert result.demand == 1.0


def test_techmarine_demand_display_integer_when_whole():
    result = _run_techmarine(
        armor_data={"1": {"spirit_fractured": False, "points_since_blessing": 0}},
        damage_tiers_by_id={1: ("critical", False)},
    )
    assert result.demand_display == "3"
    assert result.detail.startswith("3 charge(s)")


# ---------------------------------------------------------------------------
# evaluate_librarian_pressure — SIR background transmission
# ---------------------------------------------------------------------------


def _run_librarian(
    warp_data,
    guild_members,
    librarian_ids=None,
    warp_config_override=None,
):
    """Run evaluate_librarian_pressure with fully mocked inputs.

    ``librarian_ids`` - set of member IDs treated as Librarians (excluded from demand).
    """
    librarian_ids = set(librarian_ids or [])
    guild = _make_guild(guild_members)

    def _is_lib(mid):
        return mid in librarian_ids

    async def _run():
        lock_mock = MagicMock()
        lock_mock.__aenter__ = AsyncMock(return_value=None)
        lock_mock.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(_g, "WARP_EXPOSURE_LOCK", lock_mock),
            patch.object(lib, "_load_warp_exposure", return_value=warp_data),
            patch.object(lib, "_get_librarian_available_charges", new=AsyncMock(return_value=5)),
            patch.object(lib, "_librarian_tier_for_points", return_value=None),
            patch.object(lib, "_warp_config", return_value=warp_config_override or {}),
            # new=None makes _b("_is_active_participant") return None (falsy),
            # which causes the evaluator to include ALL guild members.
            patch("opscribe.bot._is_active_participant", new=None),
            patch("opscribe.bot.CONFIG", {}),
        ):
            # Give members the right role names so librarian detection works
            for m in guild_members:
                if m.id in librarian_ids:
                    role = MagicMock()
                    role.name = "Watch Librarian"
                    m.roles = [role]
                else:
                    m.roles = []
            return await lib.evaluate_librarian_pressure(guild)

    return asyncio.run(_run())


def test_librarian_demand_clean_population_no_demand():
    """All brothers clean → demand == 0, no at-risk signal."""
    members = [_make_member(1), _make_member(2)]
    result = _run_librarian(warp_data={}, guild_members=members)
    assert result.demand == 0.0


def test_librarian_demand_tainted_brother_costs_two():
    """A tainted brother (screening_due) contributes 2 charges."""
    members = [_make_member(1)]
    warp_data = {"1": {"infection_state": "tainted", "points": 2, "warp_corrupted": False}}
    result = _run_librarian(warp_data=warp_data, guild_members=members)
    assert result.demand == 2.0


def test_librarian_demand_exposed_brother_costs_three():
    """An exposed brother (under_review) contributes 3 charges."""
    members = [_make_member(1)]
    warp_data = {"1": {"infection_state": "exposed", "points": 6, "warp_corrupted": False}}
    result = _run_librarian(warp_data=warp_data, guild_members=members)
    assert result.demand == 3.0


def test_librarian_demand_volatile_brother_costs_four():
    """A volatile brother (restricted) contributes 4 charges."""
    members = [_make_member(1)]
    warp_data = {"1": {"infection_state": "volatile", "points": 12, "warp_corrupted": False}}
    result = _run_librarian(warp_data=warp_data, guild_members=members)
    assert result.demand == 4.0


def test_librarian_demand_warp_corrupted_costs_four():
    """A warp_corrupted brother always contributes 4 charges."""
    members = [_make_member(1)]
    warp_data = {"1": {"infection_state": "volatile", "points": 12, "warp_corrupted": True}}
    result = _run_librarian(warp_data=warp_data, guild_members=members)
    assert result.demand == 4.0


def test_librarian_sir_no_infected_no_background_risk():
    """No infected brothers → background_prob == 0 → no at-risk demand added."""
    members = [_make_member(1), _make_member(2)]
    warp_data = {}
    # threshold irrelevant when background_prob == 0
    result = _run_librarian(
        warp_data=warp_data,
        guild_members=members,
        warp_config_override={"at_risk_threshold": 0.20},
    )
    assert result.demand == 0.0


def test_librarian_sir_single_tainted_below_threshold():
    """1 tainted brother (20%) at exactly the threshold fires the at-risk signal
    for the remaining clean brothers.  P = 0.20 >= 0.20 threshold."""
    # 1 infected (tainted, uid=1) + 1 clean (uid=2)
    members = [_make_member(1), _make_member(2)]
    warp_data = {"1": {"infection_state": "tainted", "points": 2, "warp_corrupted": False}}

    result = _run_librarian(
        warp_data=warp_data,
        guild_members=members,
        warp_config_override={"at_risk_threshold": 0.20},
    )

    # Direct demand from infected brother: 2 charges (tainted → screening_due)
    # Background prob: 1 - (1 - 0.20) = 0.20 >= 0.20 threshold
    # Clean brothers: uid=2 has no warp_data record → clean_active_count = 1
    # At-risk fractional demand: 1 * 0.20 * 2 = 0.40
    assert result.demand == pytest.approx(2.0 + 0.40)


def test_librarian_sir_probability_below_threshold_no_at_risk():
    """background_prob below threshold does not add fractional demand."""
    members = [_make_member(1), _make_member(2)]
    warp_data = {"1": {"infection_state": "tainted", "points": 2, "warp_corrupted": False}}

    # Set threshold above the 20% tainted spread chance → no at-risk signal
    result = _run_librarian(
        warp_data=warp_data,
        guild_members=members,
        warp_config_override={"at_risk_threshold": 0.50},
    )
    assert result.demand == 2.0  # Only the tainted brother's cost, no at-risk addition


def test_librarian_sir_compound_probability():
    """Two tainted (20% each) + one volatile (50%): P = 1-(0.8*0.8*0.5) = 0.68."""
    # uids 1,2,3 = infected; uid 4 = clean
    members = [_make_member(i) for i in range(1, 5)]
    warp_data = {
        "1": {"infection_state": "tainted", "points": 2, "warp_corrupted": False},
        "2": {"infection_state": "tainted", "points": 2, "warp_corrupted": False},
        "3": {"infection_state": "volatile", "points": 12, "warp_corrupted": False},
    }

    result = _run_librarian(
        warp_data=warp_data,
        guild_members=members,
        warp_config_override={"at_risk_threshold": 0.20},
    )

    # Direct demand: tainted(2) + tainted(2) + volatile(4) = 8
    # P_no_infection = 0.8 * 0.8 * 0.5 = 0.32  → background_prob = 0.68
    # clean_active_count = 1 (uid=4 has no record)
    # at-risk demand = 1 * 0.68 * 2 = 1.36
    assert result.demand == pytest.approx(8.0 + 1 * 0.68 * 2, rel=1e-4)


def test_librarian_sir_librarian_not_counted_as_clean_brother():
    """Librarian brothers are excluded from the clean-brother at-risk count."""
    # uid=1 is infected; uid=2 is clean non-lib; uid=3 is librarian (should be excluded)
    m1 = _make_member(1)  # infected
    m2 = _make_member(2)  # clean non-lib
    m3 = _make_member(3)  # librarian

    members = [m1, m2, m3]
    warp_data = {"1": {"infection_state": "tainted", "points": 2, "warp_corrupted": False}}

    result = _run_librarian(
        warp_data=warp_data,
        guild_members=members,
        librarian_ids={3},
        warp_config_override={"at_risk_threshold": 0.20},
    )

    # background_prob = 0.20, clean count = 1 (uid=2), uid=3 excluded
    assert result.demand == pytest.approx(2.0 + 1 * 0.20 * 2, rel=1e-4)
