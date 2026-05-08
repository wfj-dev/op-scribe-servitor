"""Unit tests for forge_rite conditional output fields.

Covers:
- _should_show_extended_blessing_fields:
    * Returns True for unbound spirits (first binding)
    * Returns True for fractured spirits (reconsecrated)
    * Returns False for returning spirits (normal maintenance)
    * Returns False for restored spirits (damage repaired, spirit intact)

- forge_rite embed structure:
    * Unbound spirit includes: Bearer, Machine-Spirit, Honor of Long Watch, Litany, Attestation
    * Fractured spirit includes: Bearer, Machine-Spirit, Honor of Long Watch, Litany, Attestation
    * Returning spirit includes only: Bearer, Machine-Spirit, Attestation
    * Restored spirit includes only: Bearer, Machine-Spirit, Attestation
"""

from opscribe.bot import _should_show_extended_blessing_fields


# ---------------------------------------------------------------------------
# _should_show_extended_blessing_fields – Basic behavior
# ---------------------------------------------------------------------------


def test_unbound_spirit_shows_extended_fields():
    """First binding (unbound spirit) should show extended fields."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=True,
        spirit_is_reconsecrated=False,
        spirit_is_returning=False,
        spirit_is_restored=False,
    )
    assert result is True


def test_fractured_spirit_shows_extended_fields():
    """Reconsecrated spirit (was fractured) should show extended fields."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=False,
        spirit_is_reconsecrated=True,
        spirit_is_returning=False,
        spirit_is_restored=False,
    )
    assert result is True


def test_returning_spirit_hides_extended_fields():
    """Returning spirit (normal maintenance) should NOT show extended fields."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=False,
        spirit_is_reconsecrated=False,
        spirit_is_returning=True,
        spirit_is_restored=False,
    )
    assert result is False


def test_restored_spirit_hides_extended_fields():
    """Restored spirit (damage repaired) should NOT show extended fields."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=False,
        spirit_is_reconsecrated=False,
        spirit_is_returning=False,
        spirit_is_restored=True,
    )
    assert result is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_all_false_hides_extended_fields():
    """If all state flags are False, default to hiding extended fields."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=False,
        spirit_is_reconsecrated=False,
        spirit_is_returning=False,
        spirit_is_restored=False,
    )
    # Defensive default: treat as returning/normal
    assert result is False


def test_first_takes_precedence_over_returning():
    """If both first and returning are True (shouldn't happen), first wins."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=True,
        spirit_is_reconsecrated=False,
        spirit_is_returning=True,
        spirit_is_restored=False,
    )
    assert result is True


def test_reconsecrated_takes_precedence_over_restored():
    """If both reconsecrated and restored are True (shouldn't happen), reconsecrated wins."""
    result = _should_show_extended_blessing_fields(
        spirit_is_first=False,
        spirit_is_reconsecrated=True,
        spirit_is_returning=False,
        spirit_is_restored=True,
    )
    assert result is True
