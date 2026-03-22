"""Unit tests for _induction_count_for_user in bot.py.

Covers:
- Omega operations: 1 trial per inductee = 1 complete induction
- Siege initiations: 15 waves per inductee = 1 induction
- Regular operations: 3 trials per inductee = 1 induction
- Multiple inductees in single AAR
- Excludes self from inductee count
- Non-initiation AARs are ignored
"""

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_aar_record(
    brother_ids,
    initiation_trial=True,
    initiate_ids=None,
    initiate_id=None,
    difficulty_class="Operation",
    waves=None,
):
    """Build a minimal AAR record for testing."""
    rec = {
        "brother_ids": brother_ids,
        "initiation_trial": initiation_trial,
        "difficulty_class": difficulty_class,
    }
    if initiate_ids is not None:
        rec["initiate_ids"] = initiate_ids
    if initiate_id is not None:
        rec["initiate_id"] = initiate_id
    if waves is not None:
        rec["waves"] = waves
    return rec


# ---------------------------------------------------------------------------
# Omega induction tests
# ---------------------------------------------------------------------------

@patch("bot.load_aar_data")
def test_omega_single_inductee_counts_as_one_induction(mock_load):
    """Omega operation with 1 inductee = 1 complete induction."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Omega",
        )
    }
    # User 100 participated, inductee is 200
    assert _induction_count_for_user("100") == 1


@patch("bot.load_aar_data")
def test_omega_two_inductees_counts_as_two_inductions(mock_load):
    """Omega operation with 2 inductees = 2 complete inductions."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200", "300"],
            initiate_ids=["200", "300"],
            difficulty_class="Omega",
        )
    }
    # User 100 inducted both 200 and 300
    assert _induction_count_for_user("100") == 2


@patch("bot.load_aar_data")
def test_omega_case_insensitive(mock_load):
    """Omega detection should be case-insensitive."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="OMEGA",  # uppercase
        )
    }
    assert _induction_count_for_user("100") == 1


# ---------------------------------------------------------------------------
# Regular operation induction tests
# ---------------------------------------------------------------------------

@patch("bot.load_aar_data")
def test_regular_op_three_trials_equals_one_induction(mock_load):
    """3 regular operation trials = 1 induction."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Operation",
        ),
        "aar2": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Operation",
        ),
        "aar3": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Operation",
        ),
    }
    assert _induction_count_for_user("100") == 1


@patch("bot.load_aar_data")
def test_regular_op_two_trials_equals_zero_inductions(mock_load):
    """2 regular operation trials (incomplete) = 0 inductions."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Operation",
        ),
        "aar2": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Operation",
        ),
    }
    assert _induction_count_for_user("100") == 0


# ---------------------------------------------------------------------------
# Siege induction tests
# ---------------------------------------------------------------------------

@patch("bot.load_aar_data")
def test_siege_15_waves_equals_one_induction(mock_load):
    """15 siege waves with 1 inductee = 1 induction."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Siege",
            waves=15,
        )
    }
    assert _induction_count_for_user("100") == 1


@patch("bot.load_aar_data")
def test_siege_14_waves_equals_zero_inductions(mock_load):
    """14 siege waves (incomplete) = 0 inductions."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Siege",
            waves=14,
        )
    }
    assert _induction_count_for_user("100") == 0


@patch("bot.load_aar_data")
def test_siege_accumulated_waves_across_aars(mock_load):
    """Siege waves accumulate across multiple AARs (10 + 5 = 15 = 1)."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Siege",
            waves=10,
        ),
        "aar2": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Siege",
            waves=5,
        ),
    }
    assert _induction_count_for_user("100") == 1


# ---------------------------------------------------------------------------
# Mixed induction tests
# ---------------------------------------------------------------------------

@patch("bot.load_aar_data")
def test_mixed_omega_and_regular_ops(mock_load):
    """Omega + 3 regular ops = 2 inductions."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Omega",
        ),
        "aar2": make_aar_record(
            brother_ids=["100", "300"],
            initiate_ids=["300"],
            difficulty_class="Operation",
        ),
        "aar3": make_aar_record(
            brother_ids=["100", "300"],
            initiate_ids=["300"],
            difficulty_class="Operation",
        ),
        "aar4": make_aar_record(
            brother_ids=["100", "300"],
            initiate_ids=["300"],
            difficulty_class="Operation",
        ),
    }
    # 1 from omega + 1 from 3 regular ops
    assert _induction_count_for_user("100") == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@patch("bot.load_aar_data")
def test_self_excluded_from_inductee_count(mock_load):
    """User's own induction is excluded from their count."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["100"],  # User 100 is the inductee
            difficulty_class="Omega",
        )
    }
    # User 100 was the inductee, not an inductor
    assert _induction_count_for_user("100") == 0


@patch("bot.load_aar_data")
def test_non_initiation_aar_ignored(mock_load):
    """Non-initiation AARs don't count toward induction."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_ids=["200"],
            difficulty_class="Omega",
            initiation_trial=False,  # Not an initiation
        )
    }
    assert _induction_count_for_user("100") == 0


@patch("bot.load_aar_data")
def test_legacy_initiate_id_field(mock_load):
    """Legacy initiate_id field is still honored."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["100", "200"],
            initiate_id="200",  # Legacy single field
            initiate_ids=None,
            difficulty_class="Omega",
        )
    }
    assert _induction_count_for_user("100") == 1


@patch("bot.load_aar_data")
def test_user_not_in_brothers_ignored(mock_load):
    """AARs where user wasn't a brother are ignored."""
    from bot import _induction_count_for_user

    mock_load.return_value = {
        "aar1": make_aar_record(
            brother_ids=["200", "300"],  # User 100 not present
            initiate_ids=["300"],
            difficulty_class="Omega",
        )
    }
    assert _induction_count_for_user("100") == 0
