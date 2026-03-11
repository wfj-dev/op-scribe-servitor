"""Unit tests for stud tier and pip-display helpers in bot.py.

Covers:
- _studs_tier: tier boundary logic (cutoffs at 3, 11, 12)
- _studs_pips: Plasteel-to-Auramite conversion (4 pips = 1 Auramite)
              and the 16-stud display cap
"""

import pytest

from bot import _studs_tier, _studs_pips


# ---------------------------------------------------------------------------
# _studs_tier — tier boundary tests
# ---------------------------------------------------------------------------

def test_tier1_lower_bound():
    assert _studs_tier(1) == 1

def test_tier1_upper_bound():
    assert _studs_tier(3) == 1

def test_tier2_lower_bound():
    assert _studs_tier(4) == 2

def test_tier2_mid():
    assert _studs_tier(7) == 2

def test_tier2_upper_bound():
    assert _studs_tier(11) == 2

def test_tier3_lower_bound():
    assert _studs_tier(12) == 3

def test_tier3_upper_bound():
    assert _studs_tier(16) == 3

def test_tier3_above_cap():
    """Values beyond 16 are still tier 3 (the tier function does not cap)."""
    assert _studs_tier(17) == 3

def test_tier_zero():
    """Zero studs lands in tier 1 (≤ 3)."""
    assert _studs_tier(0) == 1


# ---------------------------------------------------------------------------
# _studs_pips — pip conversion and cap tests
# ---------------------------------------------------------------------------

def test_pips_zero_returns_dash():
    assert _studs_pips(0) == "—"

def test_pips_one_plasteel():
    assert _studs_pips(1) == "⚬"

def test_pips_three_plasteel():
    assert _studs_pips(3) == "⚬⚬⚬"

def test_pips_four_converts_to_one_auramite():
    """4 Plasteel studs should display as 1 Auramite (●) with no Plasteel remainder."""
    assert _studs_pips(4) == "●"

def test_pips_five_is_one_auramite_one_plasteel():
    assert _studs_pips(5) == "●⚬"

def test_pips_eight_is_two_auramite():
    assert _studs_pips(8) == "●●"

def test_pips_twelve_is_three_auramite():
    assert _studs_pips(12) == "●●●"

def test_pips_sixteen_is_four_auramite():
    """16 studs = 4 Auramite with no Plasteel remainder (display cap)."""
    assert _studs_pips(16) == "●●●●"

def test_pips_fifteen_is_three_auramite_three_plasteel():
    assert _studs_pips(15) == "●●●⚬⚬⚬"

def test_pips_above_cap_shows_only_four_auramite():
    """Values beyond 16 are capped to 4 Auramite with no Plasteel pips shown."""
    assert _studs_pips(17) == "●●●●"
    assert _studs_pips(20) == "●●●●"
