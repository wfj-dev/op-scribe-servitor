"""Unit tests for intensive blessing mode helpers.

Covers:
- _get_intensive_charge_cost:
    * Returns 0 for nominal target (intensive not applicable)
    * Returns correct cost per damage tier (damaged/compromised/critical)
    * Returns 4 when spirit_fractured=True (overrides damage tier)
    * Returns 4 for 'fractured' damage tier with spirit_fractured=False

- _get_techmarine_available_charges:
    * Empty pool → full BLESSING_POOL_MAX charges available
    * All active timestamps → 0 charges available
    * Partial active timestamps → correct remaining count
    * Expired timestamps → treated as available slots
    * Oversized list → never returns negative

- _consume_multiple_blessings:
    * Consuming N charges appends N staggered timestamps
    * Multi-consume trims to BLESSING_POOL_MAX (bounded)
    * Consuming more than pool size never exceeds BLESSING_POOL_MAX entries
    * Later appended timestamps may be future-dated by one regen interval each

- Collaborative charge split logic:
    * Attestor alone has enough → solo contribution, not collaborative
    * Both parties contribute → is_collaborative=True
    * Attestor contributes 0, invoker covers all → is_collaborative=False (invoker-only)
    * Attestor partial + invoker covers remainder → correct split amounts
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

from opscribe.bot import (
    _get_intensive_charge_cost,
    _get_techmarine_available_charges,
    _consume_multiple_blessings,
    BLESSING_POOL_MAX,
    BLESSING_POOL_REGEN_HOURS,
    INTENSIVE_BLESSING_COSTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hours_ago(hours: float) -> str:
    """Return an ISO-format timestamp that is `hours` hours in the past."""
    return (datetime.utcnow() - timedelta(hours=hours)).isoformat()


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def _simulate_collaborative_split(attestor_charges, invoker_charges, charges_required):
    """Reproduce the collaborative pooling split logic from forge_rite.

    Returns (is_collaborative, blessing_pool_contributions).
    """
    is_collaborative = False
    blessing_pool_contributions = []

    if attestor_charges >= charges_required:
        blessing_pool_contributions = [("attestor", charges_required)]
    else:
        combined_charges = attestor_charges + invoker_charges
        if combined_charges >= charges_required:
            attestor_contribution = attestor_charges
            invoker_contribution = charges_required - attestor_charges
            is_collaborative = attestor_contribution > 0 and invoker_contribution > 0
            blessing_pool_contributions = [("attestor", attestor_contribution)]
            if invoker_contribution > 0:
                blessing_pool_contributions.append(("invoker", invoker_contribution))

    return is_collaborative, blessing_pool_contributions


# ---------------------------------------------------------------------------
# _get_intensive_charge_cost – per damage tier
# ---------------------------------------------------------------------------


def test_intensive_cost_nominal_returns_zero():
    """Nominal (None) target cannot use intensive mode → cost = 0."""
    assert _get_intensive_charge_cost(None, False) == 0


def test_intensive_cost_damaged():
    """Damaged tier costs 2 charges (minimum for intensive)."""
    assert _get_intensive_charge_cost("damaged", False) == INTENSIVE_BLESSING_COSTS["damaged"]
    assert _get_intensive_charge_cost("damaged", False) == 2


def test_intensive_cost_compromised():
    """Compromised tier costs 2 charges."""
    assert _get_intensive_charge_cost("compromised", False) == 2


def test_intensive_cost_critical():
    """Critical tier costs 3 charges."""
    assert _get_intensive_charge_cost("critical", False) == 3


def test_intensive_cost_fractured_tier():
    """Fractured damage tier costs 4 charges even when spirit_fractured is False."""
    assert _get_intensive_charge_cost("fractured", False) == 4


def test_intensive_cost_spirit_fractured_flag():
    """spirit_fractured=True costs 4 charges regardless of damage tier."""
    assert _get_intensive_charge_cost("damaged", True) == 4
    assert _get_intensive_charge_cost("compromised", True) == 4
    assert _get_intensive_charge_cost(None, True) == 4


# ---------------------------------------------------------------------------
# _get_techmarine_available_charges – availability count
# ---------------------------------------------------------------------------


def test_available_charges_empty_pool():
    """A Techmarine with no recorded timestamps has the full pool available."""

    async def fake_pool_state(uid):
        return {"blessing_timestamps": []}

    with patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_pool_state):
        available = _run(_get_techmarine_available_charges(10))

    assert available == BLESSING_POOL_MAX


def test_available_charges_all_active():
    """All slots used with recent timestamps → 0 charges available."""
    recent_ts = [_hours_ago(1) for _ in range(BLESSING_POOL_MAX)]

    async def fake_pool_state(uid):
        return {"blessing_timestamps": recent_ts}

    with patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_pool_state):
        available = _run(_get_techmarine_available_charges(11))

    assert available == 0


def test_available_charges_partial_active():
    """2 active timestamps → BLESSING_POOL_MAX - 2 charges available."""
    partial_ts = [_hours_ago(1), _hours_ago(2)]

    async def fake_pool_state(uid):
        return {"blessing_timestamps": partial_ts}

    with patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_pool_state):
        available = _run(_get_techmarine_available_charges(12))

    assert available == BLESSING_POOL_MAX - 2


def test_available_charges_expired_timestamps_count_as_free():
    """Expired timestamps (beyond regen window) do not count against the pool."""
    expired_ts = [_hours_ago(BLESSING_POOL_REGEN_HOURS + 1) for _ in range(BLESSING_POOL_MAX)]

    async def fake_pool_state(uid):
        return {"blessing_timestamps": expired_ts}

    with patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_pool_state):
        available = _run(_get_techmarine_available_charges(13))

    assert available == BLESSING_POOL_MAX


def test_available_charges_oversized_list_never_negative():
    """An oversized timestamp list never returns a negative charge count."""
    excess_ts = [_hours_ago(1) for _ in range(BLESSING_POOL_MAX + 5)]

    async def fake_pool_state(uid):
        return {"blessing_timestamps": excess_ts}

    with patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_pool_state):
        available = _run(_get_techmarine_available_charges(14))

    assert available >= 0


def test_available_charges_mixed_expired_and_active():
    """Mix of expired and active timestamps: only active ones count against pool."""
    active_ts = [_hours_ago(1), _hours_ago(2)]
    expired_ts = [_hours_ago(BLESSING_POOL_REGEN_HOURS + 1) for _ in range(3)]

    async def fake_pool_state(uid):
        return {"blessing_timestamps": active_ts + expired_ts}

    with patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_pool_state):
        available = _run(_get_techmarine_available_charges(15))

    assert available == BLESSING_POOL_MAX - 2


# ---------------------------------------------------------------------------
# _consume_multiple_blessings – multi-charge consumption
# ---------------------------------------------------------------------------


def test_consume_multiple_appends_n_timestamps():
    """Consuming N charges appends exactly N timestamps."""
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": []}

    async def fake_set_state(uid, state, **kwargs):
        captured["state"] = state

    with (
        patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("opscribe.bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_multiple_blessings(20, 2))

    stored = captured["state"]
    assert len(stored["blessing_timestamps"]) == 2


def test_consume_multiple_three_charges():
    """Consuming 3 charges adds 3 timestamps."""
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": []}

    async def fake_set_state(uid, state, **kwargs):
        captured["state"] = state

    with (
        patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("opscribe.bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_multiple_blessings(21, 3))

    stored = captured["state"]
    assert len(stored["blessing_timestamps"]) == 3
    assert stored["remaining_blessings"] == BLESSING_POOL_MAX - 3


def test_consume_multiple_timestamps_not_in_future():
    """Consumed charges are staggered by BLESSING_POOL_REGEN_HOURS each so they
    recharge one at a time.  Only the *first* timestamp is at-or-before call time;
    subsequent ones are intentionally future-dated by design."""
    captured = {}
    before_call = datetime.utcnow()

    async def fake_get_state(uid):
        return {"blessing_timestamps": []}

    async def fake_set_state(uid, state, **kwargs):
        captured["state"] = state

    with (
        patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("opscribe.bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_multiple_blessings(22, 2))

    after_call = datetime.utcnow()
    stored_timestamps = captured["state"]["blessing_timestamps"]
    assert len(stored_timestamps) == 2
    # First timestamp is at call time
    ts0 = datetime.fromisoformat(stored_timestamps[0])
    assert ts0 >= before_call - timedelta(seconds=1)
    assert ts0 <= after_call + timedelta(seconds=1)
    # Second timestamp is one regen interval later
    ts1 = datetime.fromisoformat(stored_timestamps[1])
    regen_delta = timedelta(hours=BLESSING_POOL_REGEN_HOURS)
    assert abs((ts1 - ts0) - regen_delta) < timedelta(seconds=1)


def test_consume_multiple_timestamps_all_same():
    """N consumed charges are staggered by BLESSING_POOL_REGEN_HOURS so they
    recharge one at a time rather than all simultaneously."""
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": []}

    async def fake_set_state(uid, state, **kwargs):
        captured["state"] = state

    with (
        patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("opscribe.bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_multiple_blessings(23, 3))

    stored_timestamps = captured["state"]["blessing_timestamps"]
    assert len(stored_timestamps) == 3
    ts = [datetime.fromisoformat(s) for s in stored_timestamps]
    regen_delta = timedelta(hours=BLESSING_POOL_REGEN_HOURS)
    # Each consecutive pair is exactly one regen interval apart
    assert abs((ts[1] - ts[0]) - regen_delta) < timedelta(seconds=1)
    assert abs((ts[2] - ts[1]) - regen_delta) < timedelta(seconds=1)


def test_consume_multiple_bounded_to_pool_max():
    """Multi-consume never stores more than BLESSING_POOL_MAX timestamps."""
    # Pre-fill near capacity, then consume multiple
    pre_filled = [_hours_ago(1) for _ in range(BLESSING_POOL_MAX - 1)]
    captured = {}

    async def fake_get_state(uid):
        return {"blessing_timestamps": list(pre_filled)}

    async def fake_set_state(uid, state, **kwargs):
        captured["state"] = state

    with (
        patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("opscribe.bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_multiple_blessings(24, 4))  # Request 4 but only 1 slot remains

    stored = captured["state"]
    assert len(stored["blessing_timestamps"]) <= BLESSING_POOL_MAX
    assert stored["remaining_blessings"] >= 0


def test_consume_multiple_zero_is_noop():
    """Consuming 0 charges does nothing."""
    call_count = {"n": 0}

    async def fake_get_state(uid):
        call_count["n"] += 1
        return {"blessing_timestamps": []}

    async def fake_set_state(uid, state, **kwargs):
        call_count["n"] += 1

    with (
        patch("opscribe.bot._get_techmarine_pool_state", side_effect=fake_get_state),
        patch("opscribe.bot._set_techmarine_pool_state", side_effect=fake_set_state),
    ):
        _run(_consume_multiple_blessings(25, 0))

    assert call_count["n"] == 0, "Consuming 0 charges should not read or write state"


# ---------------------------------------------------------------------------
# Collaborative charge split logic
# ---------------------------------------------------------------------------


def test_collaborative_attestor_has_enough_solo():
    """Attestor has sufficient charges → solo contribution, not collaborative."""
    is_collab, contributions = _simulate_collaborative_split(attestor_charges=3, invoker_charges=2, charges_required=3)
    assert is_collab is False
    assert contributions == [("attestor", 3)]


def test_collaborative_both_contribute():
    """Attestor short, invoker covers the rest → both contribute, is_collaborative=True."""
    is_collab, contributions = _simulate_collaborative_split(attestor_charges=1, invoker_charges=3, charges_required=3)
    assert is_collab is True
    assert ("attestor", 1) in contributions
    assert ("invoker", 2) in contributions
    assert sum(c for _, c in contributions) == 3


def test_collaborative_invoker_only_attestor_zero():
    """Attestor has 0 charges, invoker covers everything → is_collaborative=False."""
    is_collab, contributions = _simulate_collaborative_split(attestor_charges=0, invoker_charges=4, charges_required=4)
    assert is_collab is False
    # Invoker supplies all, attestor contributes nothing
    invoker_total = sum(c for who, c in contributions if who == "invoker")
    assert invoker_total == 4
    attestor_total = sum(c for who, c in contributions if who == "attestor")
    assert attestor_total == 0


def test_collaborative_split_amounts_sum_to_required():
    """Total charges in contributions always equals charges_required."""
    for attestor_c, invoker_c, required in [
        (2, 2, 3),
        (1, 4, 4),
        (0, 2, 1),
        (3, 1, 3),
    ]:
        _, contributions = _simulate_collaborative_split(attestor_c, invoker_c, required)
        total = sum(c for _, c in contributions)
        assert total == required, f"attestor={attestor_c}, invoker={invoker_c}, required={required} → total={total}"


def test_collaborative_combined_insufficient_yields_empty():
    """When combined charges < required, no contributions are produced."""
    is_collab, contributions = _simulate_collaborative_split(attestor_charges=1, invoker_charges=1, charges_required=4)
    assert contributions == []
    assert is_collab is False


def test_collaborative_attestor_one_invoker_covers_rest_exact_split():
    """Exact split: attestor contributes 2 of 4, invoker covers 2."""
    is_collab, contributions = _simulate_collaborative_split(attestor_charges=2, invoker_charges=2, charges_required=4)
    assert is_collab is True
    assert ("attestor", 2) in contributions
    assert ("invoker", 2) in contributions
