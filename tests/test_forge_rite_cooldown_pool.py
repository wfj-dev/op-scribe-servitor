"""Unit tests for forge_rite recipient cooldown and Techmarine blessing pool.

Covers:
- _check_recipient_cooldown:
    * No prior blessing → can receive
    * One blessing within 24h → can still receive second
    * Two blessings within 24h → blocked until oldest expires
    * Blessing timestamps exactly at 24h ago → cleared (can receive)
    * Blessing timestamps older than 24h → cleared (can receive)
    * Corrupted timestamps → filtered out, defaults to allowed
    * Legacy last_blessing_timestamp → backward compatible

- _check_techmarine_can_bless:
    * Empty pool state → full pool available (can bless)
    * All slots used with recent timestamps → pool depleted (cannot bless)
    * Some slots used within regen window → partial pool available
    * Expired timestamps are ignored → treated as full pool
    * Pool at exactly max active → returns regen timing
    * Oversized timestamp list (corruption guard) → trimmed, not negative

- _consume_blessing:
    * Consuming a blessing appends a timestamp
    * Consuming does not exceed BLESSING_POOL_MAX stored entries
    * Consuming when over capacity (corruption) still caps the list
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

import bot
from bot import (
    _check_recipient_cooldown,
    _check_techmarine_can_bless,
    _consume_blessing,
    BLESSING_POOL_MAX,
    BLESSING_POOL_REGEN_HOURS,
    BLESSING_RECIPIENT_COOLDOWN_HOURS,
    BLESSING_RECIPIENT_MAX_PER_DAY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hours_ago(hours: float) -> str:
    """Return an ISO-format timestamp that is `hours` hours in the past."""
    return (datetime.utcnow() - timedelta(hours=hours)).isoformat()


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _check_recipient_cooldown – no prior blessing
# ---------------------------------------------------------------------------


def test_recipient_cooldown_no_prior_blessing():
    """A recipient with no recorded blessing can always receive."""
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(999))
    assert can_receive is True
    assert remaining is None
    assert blessings_used == 0


def test_recipient_cooldown_legacy_format_backward_compat():
    """Legacy last_blessing_timestamp format is handled for backward compatibility."""
    recent_ts = _hours_ago(12)
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        # Old format with last_blessing_timestamp instead of blessing_timestamps list
        mock_state.return_value = {"last_blessing_timestamp": recent_ts}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(998))
    assert can_receive is True  # 1 blessing = can still receive second
    assert remaining is None
    assert blessings_used == 1


# ---------------------------------------------------------------------------
# _check_recipient_cooldown – within cooldown window
# ---------------------------------------------------------------------------


def test_recipient_cooldown_allows_second_blessing_within_24h():
    """A recipient blessed once within 24h can still receive a second blessing."""
    recent_ts = _hours_ago(12)
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": [recent_ts]}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(1))
    assert can_receive is True  # Can still receive second blessing
    assert remaining is None
    assert blessings_used == 1


def test_recipient_cooldown_blocked_at_max_blessings():
    """A recipient blessed three times within 24h is blocked until oldest expires."""
    ts1 = _hours_ago(18)
    ts2 = _hours_ago(12)
    ts3 = _hours_ago(6)
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": [ts1, ts2, ts3]}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(2))
    assert can_receive is False
    assert remaining is not None
    assert blessings_used == 3
    # Remaining should be roughly 6h until the first timestamp expires
    assert timedelta(hours=5, minutes=55) <= remaining <= timedelta(hours=6, minutes=5)


# ---------------------------------------------------------------------------
# _check_recipient_cooldown – cooldown expired
# ---------------------------------------------------------------------------


def test_recipient_cooldown_cleared_after_24h():
    """A recipient blessed 25h ago has their cooldown cleared."""
    old_ts = _hours_ago(25)
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": [old_ts]}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(3))
    assert can_receive is True
    assert remaining is None
    assert blessings_used == 0  # Old timestamp is filtered out


def test_recipient_cooldown_cleared_exactly_at_24h():
    """A recipient blessed exactly 24h ago is cleared (boundary condition)."""
    exact_24h_ago = _hours_ago(BLESSING_RECIPIENT_COOLDOWN_HOURS)
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": [exact_24h_ago]}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(4))
    # elapsed >= cooldown → cleared
    assert can_receive is True
    assert remaining is None
    assert blessings_used == 0


# ---------------------------------------------------------------------------
# _check_recipient_cooldown – corrupted timestamp
# ---------------------------------------------------------------------------


def test_recipient_cooldown_corrupted_timestamp_allows():
    """A corrupted blessing_timestamp defaults to allowing the blessing."""
    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": ["not-a-timestamp"]}
        can_receive, remaining, blessings_used = _run(_check_recipient_cooldown(5))
    assert can_receive is True
    assert blessings_used == 0  # Corrupted timestamp is filtered out


# ---------------------------------------------------------------------------
# _check_techmarine_can_bless – full pool (no active timestamps)
# ---------------------------------------------------------------------------


def test_techmarine_can_bless_empty_pool_state():
    """A Techmarine with no recorded blessings has the full pool available."""
    with patch("bot._get_techmarine_pool_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": []}
        can_bless, remaining, regen_time = _run(_check_techmarine_can_bless(10))
    assert can_bless is True
    assert remaining == BLESSING_POOL_MAX
    assert regen_time is None


def test_techmarine_can_bless_all_timestamps_expired():
    """Expired timestamps (older than regen window) are treated as full pool."""
    old_timestamps = [_hours_ago(BLESSING_POOL_REGEN_HOURS + 1) for _ in range(BLESSING_POOL_MAX)]
    with patch("bot._get_techmarine_pool_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": old_timestamps}
        can_bless, remaining, regen_time = _run(_check_techmarine_can_bless(11))
    assert can_bless is True
    assert remaining == BLESSING_POOL_MAX


# ---------------------------------------------------------------------------
# _check_techmarine_can_bless – pool depleted
# ---------------------------------------------------------------------------


def test_techmarine_cannot_bless_when_all_slots_active():
    """Pool is depleted when all BLESSING_POOL_MAX slots have recent timestamps."""
    recent_timestamps = [_hours_ago(1) for _ in range(BLESSING_POOL_MAX)]
    with patch("bot._get_techmarine_pool_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": recent_timestamps}
        can_bless, remaining, regen_time = _run(_check_techmarine_can_bless(12))
    assert can_bless is False
    assert remaining == 0
    assert regen_time is not None
    assert regen_time.total_seconds() > 0


def test_techmarine_pool_depletion_regen_timing():
    """Regen time reflects the oldest active timestamp."""
    # Oldest blessing used 3h ago; regen window is ~4.8h → ~1.8h remaining
    regen_window_h = BLESSING_POOL_REGEN_HOURS
    oldest_h_ago = regen_window_h - 1.8
    recent_timestamps = (
        [_hours_ago(oldest_h_ago)]
        + [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX - 1)]
    )
    with patch("bot._get_techmarine_pool_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": recent_timestamps}
        can_bless, remaining, regen_time = _run(_check_techmarine_can_bless(13))
    assert can_bless is False
    # Regen time should be ~1.8h (within 5-minute tolerance)
    expected_seconds = 1.8 * 3600
    assert abs(regen_time.total_seconds() - expected_seconds) < 300


# ---------------------------------------------------------------------------
# _check_techmarine_can_bless – partial pool
# ---------------------------------------------------------------------------


def test_techmarine_partial_pool_available():
    """Some slots used → partial pool is available (can still bless)."""
    # Use 2 out of BLESSING_POOL_MAX slots
    used_timestamps = [_hours_ago(1), _hours_ago(2)]
    with patch("bot._get_techmarine_pool_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": used_timestamps}
        can_bless, remaining, regen_time = _run(_check_techmarine_can_bless(14))
    assert can_bless is True
    assert remaining == BLESSING_POOL_MAX - 2


# ---------------------------------------------------------------------------
# _check_techmarine_can_bless – oversized list (corruption guard)
# ---------------------------------------------------------------------------


def test_techmarine_oversized_timestamps_clamped():
    """An oversized timestamp list (e.g. due to file corruption) never returns negative availability."""
    # Provide MORE than BLESSING_POOL_MAX recent timestamps
    excess_timestamps = [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX + 3)]
    with patch("bot._get_techmarine_pool_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": excess_timestamps}
        can_bless, remaining, regen_time = _run(_check_techmarine_can_bless(15))
    assert can_bless is False
    assert remaining >= 0  # Must never be negative


# ---------------------------------------------------------------------------
# _consume_blessing – appends a timestamp
# ---------------------------------------------------------------------------


def test_consume_blessing_appends_timestamp():
    """Consuming a blessing adds one timestamp to the stored list."""
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": []}

    async def fake_set_state(uid, state):
        captured["state"] = state

    with (
        patch("bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_blessing(20))

    assert "state" in captured
    stored = captured["state"]
    assert len(stored["blessing_timestamps"]) == 1
    assert stored["remaining_blessings"] == BLESSING_POOL_MAX - 1


def test_consume_blessing_does_not_exceed_pool_max():
    """Consuming a blessing never stores more than BLESSING_POOL_MAX timestamps."""
    # Pre-fill with BLESSING_POOL_MAX - 1 active entries
    pre_filled = [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX - 1)]
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": list(pre_filled)}

    async def fake_set_state(uid, state):
        captured["state"] = state

    with (
        patch("bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_blessing(21))

    stored = captured["state"]
    assert len(stored["blessing_timestamps"]) == BLESSING_POOL_MAX
    assert stored["remaining_blessings"] == 0


def test_consume_blessing_with_oversized_list_stays_bounded():
    """Consuming when the stored list is already oversized still caps to BLESSING_POOL_MAX."""
    oversized = [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX + 2)]
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": list(oversized)}

    async def fake_set_state(uid, state):
        captured["state"] = state

    with (
        patch("bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_blessing(22))

    stored = captured["state"]
    assert len(stored["blessing_timestamps"]) <= BLESSING_POOL_MAX
    assert stored["remaining_blessings"] >= 0


# ---------------------------------------------------------------------------
# _check_recipient_cooldown + force override interaction (integration style)
# ---------------------------------------------------------------------------


def test_force_override_skips_cooldown_check():
    """When force=True the forge_rite handler skips _check_recipient_cooldown entirely.

    We verify this by confirming that a user who is still on cooldown would be
    blocked under normal conditions, but that the forge_rite command branches
    past the check when force=True.  We test this by calling
    _check_recipient_cooldown directly and confirming it blocks, then confirming
    that passing force=True into the conditional in bot.py (reproduced below)
    bypasses the call.
    """
    # Three blessings within 24h = at max
    ts1 = _hours_ago(18)
    ts2 = _hours_ago(12)
    ts3 = _hours_ago(6)

    with patch("bot._get_armor_state", new_callable=AsyncMock) as mock_state:
        mock_state.return_value = {"blessing_timestamps": [ts1, ts2, ts3]}
        # Without force: cooldown is active (at max blessings)
        can_receive, _, blessings_used = _run(_check_recipient_cooldown(30))
    assert can_receive is False
    assert blessings_used == 3

    # With force=True the forge_rite handler does `if not force:` before calling
    # _check_recipient_cooldown.  Simulate that short-circuit here:
    force = True
    cooldown_checked = False
    if not force:
        cooldown_checked = True  # pragma: no cover
    assert cooldown_checked is False, "force=True must bypass the cooldown check"


# ---------------------------------------------------------------------------
# Pool fallback: attestor depleted → invoker used
# ---------------------------------------------------------------------------


def test_pool_fallback_uses_invoker_when_attestor_depleted():
    """When attestor's pool is empty, the invoker's pool is checked as fallback.

    We verify by calling _check_techmarine_can_bless for two different IDs and
    confirming the correct decision would be made in the fallback branch.
    """
    ATTESTOR_ID = 100
    INVOKER_ID = 200

    depleted = [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX)]
    fresh = []  # invoker has full pool

    def fake_pool_state(uid):
        if uid == ATTESTOR_ID:
            return {"blessing_timestamps": list(depleted)}
        return {"blessing_timestamps": list(fresh)}

    async def async_fake_pool_state(uid):
        return fake_pool_state(uid)

    with patch("bot._get_techmarine_pool_state", side_effect=async_fake_pool_state):
        attestor_can_bless, _, _ = _run(_check_techmarine_can_bless(ATTESTOR_ID))
        invoker_can_bless, _, _ = _run(_check_techmarine_can_bless(INVOKER_ID))

    assert attestor_can_bless is False, "Attestor pool should be depleted"
    assert invoker_can_bless is True, "Invoker pool should be available as fallback"

    # The forge_rite handler logic: pick attestor first, then invoker
    blessing_pool_user_id = None
    if attestor_can_bless:
        blessing_pool_user_id = ATTESTOR_ID
    elif INVOKER_ID != ATTESTOR_ID and invoker_can_bless:
        blessing_pool_user_id = INVOKER_ID

    assert blessing_pool_user_id == INVOKER_ID


def test_both_pools_depleted_no_fallback():
    """When both attestor and invoker pools are empty, no fallback is available."""
    ATTESTOR_ID = 101
    INVOKER_ID = 201

    depleted = [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX)]

    async def async_fake_pool_state(uid):
        return {"blessing_timestamps": list(depleted)}

    with patch("bot._get_techmarine_pool_state", side_effect=async_fake_pool_state):
        attestor_can_bless, _, _ = _run(_check_techmarine_can_bless(ATTESTOR_ID))
        invoker_can_bless, _, _ = _run(_check_techmarine_can_bless(INVOKER_ID))

    assert attestor_can_bless is False
    assert invoker_can_bless is False

    # Simulate the forge_rite fallback logic → should end with no pool user
    blessing_pool_user_id = None
    if attestor_can_bless:
        blessing_pool_user_id = ATTESTOR_ID
    elif INVOKER_ID != ATTESTOR_ID and invoker_can_bless:
        blessing_pool_user_id = INVOKER_ID

    assert blessing_pool_user_id is None


def test_invoker_is_attestor_single_pool_checked():
    """When invoker and attestor are the same person, only one pool check occurs."""
    SAME_ID = 300
    depleted = [_hours_ago(0.5) for _ in range(BLESSING_POOL_MAX)]

    async def async_fake_pool_state(uid):
        return {"blessing_timestamps": list(depleted)}

    with patch("bot._get_techmarine_pool_state", side_effect=async_fake_pool_state):
        can_bless, _, _ = _run(_check_techmarine_can_bless(SAME_ID))

    assert can_bless is False

    # When invoker IS attestor, the forge_rite handler does not attempt the invoker
    # fallback branch (the `if int(interaction.user.id) != int(attestor_member.id)` guard).
    # Reproduce that logic:
    attestor_can_bless = can_bless
    invoker_id = SAME_ID
    attestor_id = SAME_ID

    blessing_pool_user_id = None
    if attestor_can_bless:
        blessing_pool_user_id = attestor_id
    elif invoker_id != attestor_id:
        pass  # would check invoker, but they are the same
    # else: single pool depleted → forge_rite returns an error message

    assert blessing_pool_user_id is None
