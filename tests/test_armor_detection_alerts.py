"""Unit tests for armor integrity detection alert system.

Covers:
- _roll_detection_alert:
    * Returns True with probability based on tier
    * Returns False for None tier
    * Fractured tier always returns True (100% chance)

- Detection tracking:
    * Detection alert sent only once per tier level
    * Detection alert resets on blessing
    * Tier escalation triggers new detection opportunity
    * Sustained alert (damage) takes priority over detection
    * Sustained alert updates detection tracking

- ARMOR_DETECTION_CHANCES constant:
    * Validates expected chances per tier
"""

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from opscribe.bot import (
    _roll_detection_alert,
    ARMOR_DETECTION_CHANCES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# ARMOR_DETECTION_CHANCES constant validation
# ---------------------------------------------------------------------------


def test_detection_chances_has_expected_tiers():
    """Detection chances should exist for damaged, compromised, critical, fractured."""
    assert "damaged" in ARMOR_DETECTION_CHANCES
    assert "compromised" in ARMOR_DETECTION_CHANCES
    assert "critical" in ARMOR_DETECTION_CHANCES
    assert "fractured" in ARMOR_DETECTION_CHANCES


def test_detection_chances_scaling():
    """Detection chances should increase with severity."""
    assert ARMOR_DETECTION_CHANCES["damaged"] == 0.20
    assert ARMOR_DETECTION_CHANCES["compromised"] == 0.35
    assert ARMOR_DETECTION_CHANCES["critical"] == 0.50
    assert ARMOR_DETECTION_CHANCES["fractured"] == 1.0  # Always detect


def test_detection_chances_are_valid_probabilities():
    """All detection chances should be between 0 and 1."""
    for tier, chance in ARMOR_DETECTION_CHANCES.items():
        assert 0.0 <= chance <= 1.0, f"{tier} has invalid chance: {chance}"


# ---------------------------------------------------------------------------
# _roll_detection_alert
# ---------------------------------------------------------------------------


def test_roll_detection_none_tier_returns_false():
    """None tier should never trigger detection."""
    # Multiple calls to ensure it's not random
    for _ in range(10):
        assert _roll_detection_alert(None) is False


def test_roll_detection_unknown_tier_returns_false():
    """Unknown tier should never trigger detection."""
    for _ in range(10):
        assert _roll_detection_alert("nonexistent") is False


def test_roll_detection_fractured_always_true():
    """Fractured tier should always trigger detection (100% chance)."""
    # Multiple calls to verify it's deterministic
    for _ in range(20):
        assert _roll_detection_alert("fractured") is True


def test_roll_detection_damaged_probability():
    """Damaged tier should trigger ~20% of the time."""
    # Use a fixed seed for deterministic test
    _successes = 0  # Reserved for statistical analysis
    _trials = 1000  # Reserved for statistical analysis
    with patch("opscribe.forge_ops.random.random") as mock_random:
        # Test boundary: < 0.20 should succeed
        mock_random.return_value = 0.19
        assert _roll_detection_alert("damaged") is True

        mock_random.return_value = 0.20
        assert _roll_detection_alert("damaged") is False

        mock_random.return_value = 0.21
        assert _roll_detection_alert("damaged") is False


def test_roll_detection_compromised_probability():
    """Compromised tier should trigger ~35% of the time."""
    with patch("opscribe.forge_ops.random.random") as mock_random:
        mock_random.return_value = 0.34
        assert _roll_detection_alert("compromised") is True

        mock_random.return_value = 0.35
        assert _roll_detection_alert("compromised") is False

        mock_random.return_value = 0.36
        assert _roll_detection_alert("compromised") is False


def test_roll_detection_critical_probability():
    """Critical tier should trigger ~50% of the time."""
    with patch("opscribe.forge_ops.random.random") as mock_random:
        mock_random.return_value = 0.49
        assert _roll_detection_alert("critical") is True

        mock_random.return_value = 0.50
        assert _roll_detection_alert("critical") is False

        mock_random.return_value = 0.51
        assert _roll_detection_alert("critical") is False


# ---------------------------------------------------------------------------
# Detection tracking in armor state
# ---------------------------------------------------------------------------


def test_detection_alert_tracked_in_state():
    """When detection alert succeeds, last_detection_alert_tier should be updated."""
    mock_member = MagicMock()
    mock_member.id = 12345
    mock_member.roles = []

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = mock_member

    captured_state = {}

    async def mock_get_armor_state(uid):
        return {
            "points_since_blessing": 50,
            "damage_tier": "damaged",
            "last_detection_alert_tier": None,
        }

    async def mock_set_armor_state(uid, state):
        captured_state.update(state)

    # Force detection roll to succeed
    with (
        patch("opscribe.bot._get_armor_state", side_effect=mock_get_armor_state),
        patch("opscribe.bot._set_armor_state", side_effect=mock_set_armor_state),
        patch("opscribe.bot._get_member_damage_tier", return_value="damaged"),
        patch("opscribe.bot._get_damage_penalty", return_value=1),
        patch("opscribe.bot.compute_stats_for_user", return_value={"aar_points": 200}),
        patch("opscribe.bot._check_armor_grace_period", return_value=True),
        patch("opscribe.bot._run_armor_integrity_check", new_callable=AsyncMock, return_value=False),
        patch("opscribe.bot._roll_detection_alert", return_value=True),
    ):
        from opscribe.bot import _process_armor_integrity_for_aar

        penalty, alert_info = _run(_process_armor_integrity_for_aar("12345", 4, mock_guild))

    assert alert_info is not None
    assert alert_info["alert_type"] == "detected"
    assert captured_state.get("last_detection_alert_tier") == "damaged"


def test_detection_alert_not_repeated_for_same_tier():
    """Detection alert should not be sent again if already alerted for same tier."""
    mock_member = MagicMock()
    mock_member.id = 12345
    mock_member.roles = []

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = mock_member

    async def mock_get_armor_state(uid):
        return {
            "points_since_blessing": 50,
            "damage_tier": "damaged",
            "last_detection_alert_tier": "damaged",  # Already alerted
        }

    with (
        patch("opscribe.bot._get_armor_state", side_effect=mock_get_armor_state),
        patch("opscribe.bot._set_armor_state", new_callable=AsyncMock),
        patch("opscribe.bot._get_member_damage_tier", return_value="damaged"),
        patch("opscribe.bot._get_damage_penalty", return_value=1),
        patch("opscribe.bot.compute_stats_for_user", return_value={"aar_points": 200}),
        patch("opscribe.bot._check_armor_grace_period", return_value=True),
        patch("opscribe.bot._run_armor_integrity_check", new_callable=AsyncMock, return_value=False),
        patch("opscribe.bot._roll_detection_alert", return_value=True),
    ):  # Would succeed, but shouldn't be called
        from opscribe.bot import _process_armor_integrity_for_aar

        penalty, alert_info = _run(_process_armor_integrity_for_aar("12345", 4, mock_guild))

    # No alert because already alerted for this tier
    assert alert_info is None


def test_detection_alert_allowed_for_escalated_tier():
    """Detection alert should be sent if tier escalated beyond last alerted."""
    mock_member = MagicMock()
    mock_member.id = 12345
    mock_member.roles = []

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = mock_member

    async def mock_get_armor_state(uid):
        return {
            "points_since_blessing": 80,
            "damage_tier": "compromised",
            "last_detection_alert_tier": "damaged",  # Previously alerted for damaged
        }

    with (
        patch("opscribe.bot._get_armor_state", side_effect=mock_get_armor_state),
        patch("opscribe.bot._set_armor_state", new_callable=AsyncMock),
        patch("opscribe.bot._get_member_damage_tier", return_value="compromised"),
        patch("opscribe.bot._get_damage_penalty", return_value=2),
        patch("opscribe.bot.compute_stats_for_user", return_value={"aar_points": 200}),
        patch("opscribe.bot._check_armor_grace_period", return_value=True),
        patch("opscribe.bot._run_armor_integrity_check", new_callable=AsyncMock, return_value=False),
        patch("opscribe.bot._roll_detection_alert", return_value=True),
    ):
        from opscribe.bot import _process_armor_integrity_for_aar

        penalty, alert_info = _run(_process_armor_integrity_for_aar("12345", 4, mock_guild))

    # Alert should be sent for new (escalated) tier
    assert alert_info is not None
    assert alert_info["alert_type"] == "detected"
    assert alert_info["tier"] == "compromised"


def test_sustained_alert_takes_priority_over_detection():
    """When damage escalates, sustained alert should be sent (not detection)."""
    mock_member = MagicMock()
    mock_member.id = 12345
    mock_member.roles = []

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = mock_member

    async def mock_get_armor_state(uid):
        return {
            "points_since_blessing": 100,
            "damage_tier": None,  # Was nominal
            "last_detection_alert_tier": None,
        }

    async def mock_apply_damage_tier(member, guild, current, rolled):
        return "damaged"  # Damage occurs

    with (
        patch("opscribe.bot._get_armor_state", side_effect=mock_get_armor_state),
        patch("opscribe.bot._set_armor_state", new_callable=AsyncMock),
        patch("opscribe.bot._get_member_damage_tier", return_value=None),
        patch("opscribe.bot._get_damage_penalty", return_value=0),
        patch("opscribe.bot.compute_stats_for_user", return_value={"aar_points": 200}),
        patch("opscribe.bot._check_armor_grace_period", return_value=True),
        patch("opscribe.bot._run_armor_integrity_check", new_callable=AsyncMock, return_value=True),
        patch("opscribe.bot._roll_damage_tier", return_value="damaged"),
        patch("opscribe.bot._apply_damage_tier", side_effect=mock_apply_damage_tier),
    ):
        from opscribe.bot import _process_armor_integrity_for_aar

        penalty, alert_info = _run(_process_armor_integrity_for_aar("12345", 4, mock_guild))

    # Should be sustained, not detected
    assert alert_info is not None
    assert alert_info["alert_type"] == "sustained"
    assert alert_info["tier"] == "damaged"


def test_sustained_alert_updates_detection_tracking():
    """When sustained alert is sent, last_detection_alert_tier should be updated."""
    mock_member = MagicMock()
    mock_member.id = 12345
    mock_member.roles = []

    mock_guild = MagicMock()
    mock_guild.get_member.return_value = mock_member

    captured_state = {}

    async def mock_get_armor_state(uid):
        return {
            "points_since_blessing": 100,
            "damage_tier": None,
            "last_detection_alert_tier": None,
        }

    async def mock_set_armor_state(uid, state):
        captured_state.update(state)

    async def mock_apply_damage_tier(member, guild, current, rolled):
        return "compromised"

    with (
        patch("opscribe.bot._get_armor_state", side_effect=mock_get_armor_state),
        patch("opscribe.bot._set_armor_state", side_effect=mock_set_armor_state),
        patch("opscribe.bot._get_member_damage_tier", return_value=None),
        patch("opscribe.bot._get_damage_penalty", return_value=0),
        patch("opscribe.bot.compute_stats_for_user", return_value={"aar_points": 200}),
        patch("opscribe.bot._check_armor_grace_period", return_value=True),
        patch("opscribe.bot._run_armor_integrity_check", new_callable=AsyncMock, return_value=True),
        patch("opscribe.bot._roll_damage_tier", return_value="compromised"),
        patch("opscribe.bot._apply_damage_tier", side_effect=mock_apply_damage_tier),
    ):
        from opscribe.bot import _process_armor_integrity_for_aar

        penalty, alert_info = _run(_process_armor_integrity_for_aar("12345", 4, mock_guild))

    assert captured_state.get("last_detection_alert_tier") == "compromised"


# ---------------------------------------------------------------------------
# Blessing resets detection tracking
# ---------------------------------------------------------------------------


def test_blessing_resets_detection_tracking():
    """Blessing should reset last_detection_alert_tier to None."""
    from opscribe.bot import _clear_armor_damage

    mock_member = MagicMock()
    mock_member.id = 99999
    mock_member.roles = []

    mock_guild = MagicMock()
    mock_guild.get_role.return_value = None  # No damage roles to remove

    captured_state = {}

    async def mock_get_armor_state(uid):
        return {
            "blessing_timestamps": [],
            "last_detection_alert_tier": "critical",  # Had prior detection
        }

    async def mock_set_armor_state(uid, state):
        captured_state.update(state)

    with (
        patch("opscribe.bot._get_armor_state", side_effect=mock_get_armor_state),
        patch("opscribe.bot._set_armor_state", side_effect=mock_set_armor_state),
        patch("opscribe.bot._get_armor_damage_role_ids", return_value={}),
    ):
        _run(_clear_armor_damage(mock_member, mock_guild))

    # Detection tracking should be reset
    assert captured_state.get("last_detection_alert_tier") is None
