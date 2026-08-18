from datetime import datetime, timedelta, timezone

from opscribe.constants import OP_RATING_BASELINE, OP_RATING_MAX, OP_RATING_MIN
from opscribe.datastore import (
    _compute_operational_rating_for_user_from_records,
    _compute_stats_for_user_from_records,
)


def _make_record(
    ts: datetime,
    difficulty_class: str,
    *,
    user_id: str = "u1",
    team_size: int = 1,
    strike_linked: bool = False,
    omega_strat: bool = False,
):
    brother_ids = [user_id] + [f"ally{i}" for i in range(max(0, team_size - 1))]
    record = {
        "timestamp": ts.isoformat(),
        "aar_type": "pve",
        "difficulty_class": difficulty_class,
        "brother_ids": brother_ids,
        "points_for_op": 4,
        "armory_data": 0,
        "armory_challenge_points": 0,
        "gene_seed_status": "unknown",
    }
    if strike_linked:
        record["target_package_id"] = "OX-TEST"
    if omega_strat:
        record["omega_strat_difficulty_role_present"] = True
    return record


def test_positive_bucket_increases_rating():
    now = datetime.now(timezone.utc)
    rec = _make_record(now, "absolute_ops")

    rating = _compute_operational_rating_for_user_from_records("u1", [rec])

    assert rating["operational_rating"] > OP_RATING_BASELINE
    assert rating["operational_rating"] <= OP_RATING_MAX


def test_loss_bucket_decreases_rating():
    now = datetime.now(timezone.utc)
    rec = _make_record(now, "normal_stratagem")

    rating = _compute_operational_rating_for_user_from_records("u1", [rec])

    assert rating["operational_rating"] < OP_RATING_BASELINE
    assert rating["operational_rating"] >= OP_RATING_MIN


def test_strike_bonus_applies_to_positive_only():
    now = datetime.now(timezone.utc)
    pos_plain = _make_record(now, "absolute_ops", strike_linked=False)
    pos_strike = _make_record(now, "absolute_ops", strike_linked=True)

    pos_plain_rating = _compute_operational_rating_for_user_from_records("u1", [pos_plain])
    pos_strike_rating = _compute_operational_rating_for_user_from_records("u1", [pos_strike])
    assert pos_strike_rating["operational_rating"] > pos_plain_rating["operational_rating"]

    loss_plain = _make_record(now, "normal_siege", strike_linked=False)
    loss_strike = _make_record(now, "normal_siege", strike_linked=True)

    loss_plain_rating = _compute_operational_rating_for_user_from_records("u1", [loss_plain])
    loss_strike_rating = _compute_operational_rating_for_user_from_records("u1", [loss_strike])
    assert loss_strike_rating["operational_rating"] == loss_plain_rating["operational_rating"]


def test_rating_is_clamped_to_range():
    now = datetime.now(timezone.utc)

    many_high = [
        _make_record(now + timedelta(minutes=i), "omega_ops", omega_strat=True)
        for i in range(40)
    ]
    high_rating = _compute_operational_rating_for_user_from_records("u1", many_high)
    assert high_rating["operational_rating"] <= OP_RATING_MAX
    assert high_rating["operational_rating"] > OP_RATING_BASELINE

    many_low = [
        _make_record(now + timedelta(minutes=i), "ruthless_ops")
        for i in range(140)
    ]
    low_rating = _compute_operational_rating_for_user_from_records("u1", many_low)
    assert low_rating["operational_rating"] == OP_RATING_MIN


def test_soft_cap_preserves_high_end_ordering():
    now = datetime.now(timezone.utc)

    high = [
        _make_record(now + timedelta(minutes=i), "omega_ops", omega_strat=True)
        for i in range(40)
    ]
    higher = [
        _make_record(now + timedelta(minutes=i), "omega_ops", omega_strat=True)
        for i in range(80)
    ]

    high_rating = _compute_operational_rating_for_user_from_records("u1", high)
    higher_rating = _compute_operational_rating_for_user_from_records("u1", higher)

    assert higher_rating["operational_rating_raw"] > high_rating["operational_rating_raw"]
    assert higher_rating["operational_rating_raw"] <= OP_RATING_MAX


def test_compute_stats_includes_operational_rating_fields():
    now = datetime.now(timezone.utc)
    recs = [
        _make_record(now, "hard_stratagem", team_size=3),
        _make_record(now + timedelta(minutes=1), "normal_stratagem", team_size=3),
    ]

    stats = _compute_stats_for_user_from_records("u1", recs)

    assert "operational_rating" in stats
    assert "operational_rating_raw" in stats
    assert "operational_rating_delta" in stats
    assert "operational_rating_events" in stats
