"""Unit tests for _should_send_award_notification in bot.py.

Covers the helper that gates award notification dispatch:
- First-run initialisation when the member already holds the role
- First-run initialisation when the member does not hold the role
- Subsequent-run (restart-safe) behaviour: no duplicate notifications
- Eligible + no role + not previously notified → should notify
- Not eligible → should not notify
- Already notified → should not notify regardless of eligibility
"""

from bot import _should_send_award_notification


# ---------------------------------------------------------------------------
# First-run behaviour (key not yet in tracking dict)
# ---------------------------------------------------------------------------


def test_first_run_has_role_marks_notified_no_send():
    """On first run, if the member already has the role, the key is silently
    initialised to True and the helper returns False (no notification sent)."""
    tracking = {}
    result = _should_send_award_notification(
        is_eligible=True,
        has_role=True,
        tracking_key="ardent_raider_notified",
        tracking=tracking,
    )
    assert result is False
    assert tracking["ardent_raider_notified"] is True


def test_first_run_no_role_not_yet_eligible_no_send():
    """On first run, member doesn't have the role and is not eligible → False."""
    tracking = {}
    result = _should_send_award_notification(
        is_eligible=False,
        has_role=False,
        tracking_key="for_the_fallen_notified",
        tracking=tracking,
    )
    assert result is False
    assert "for_the_fallen_notified" not in tracking


def test_first_run_eligible_no_role_should_send():
    """On first run, member is eligible and doesn't have the role → True (send)."""
    tracking = {}
    result = _should_send_award_notification(
        is_eligible=True,
        has_role=False,
        tracking_key="crimson_laurels_notified",
        tracking=tracking,
    )
    assert result is True


# ---------------------------------------------------------------------------
# Subsequent-run / restart-safe behaviour (key already in tracking dict)
# ---------------------------------------------------------------------------


def test_subsequent_run_already_notified_no_send():
    """After a previous notification the helper must not send again, even if
    the member is still eligible and still lacks the role (restart safety)."""
    tracking = {"ardent_raider_notified": True}
    result = _should_send_award_notification(
        is_eligible=True,
        has_role=False,
        tracking_key="ardent_raider_notified",
        tracking=tracking,
    )
    assert result is False


def test_subsequent_run_not_notified_eligible_no_role_should_send():
    """If the key is present but False, and member is eligible without the role,
    the helper returns True."""
    tracking = {"for_the_fallen_notified": False}
    result = _should_send_award_notification(
        is_eligible=True,
        has_role=False,
        tracking_key="for_the_fallen_notified",
        tracking=tracking,
    )
    assert result is True


def test_subsequent_run_not_notified_not_eligible_no_send():
    """Key present but False, member not eligible → False."""
    tracking = {"crimson_laurels_notified": False}
    result = _should_send_award_notification(
        is_eligible=False,
        has_role=False,
        tracking_key="crimson_laurels_notified",
        tracking=tracking,
    )
    assert result is False


def test_subsequent_run_not_notified_has_role_no_send():
    """Key present but False, member has role but is eligible → False (role already held)."""
    tracking = {"ardent_raider_notified": False}
    result = _should_send_award_notification(
        is_eligible=True,
        has_role=True,
        tracking_key="ardent_raider_notified",
        tracking=tracking,
    )
    assert result is False


# ---------------------------------------------------------------------------
# Tracking dict mutation
# ---------------------------------------------------------------------------


def test_tracking_dict_not_mutated_on_send():
    """The helper does NOT set the tracking key to True on a positive return;
    the caller owns that mutation."""
    tracking = {}
    _should_send_award_notification(
        is_eligible=True,
        has_role=False,
        tracking_key="ardent_raider_notified",
        tracking=tracking,
    )
    # Key should not be set — caller is responsible for updating it
    assert "ardent_raider_notified" not in tracking


def test_tracking_dict_mutated_silently_when_has_role_first_run():
    """When silently initialising (first run + has role), the dict is mutated."""
    tracking = {}
    _should_send_award_notification(
        is_eligible=False,
        has_role=True,
        tracking_key="crimson_laurels_notified",
        tracking=tracking,
    )
    assert tracking.get("crimson_laurels_notified") is True
