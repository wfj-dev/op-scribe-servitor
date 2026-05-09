"""Unit tests for the Librarian / Warp Corruption subsystem.

Covers:
- Sanction tiering: ``_warp_sanction_key_for_points`` boundary behavior.
- Config readers: ``_get_penalty_probabilities`` / ``_get_spread_chances``
  derive correctly from the consolidated ``brother_probability_tiers`` block,
  and fall back to ``WARP_PENALTY_PROBABILITIES`` / ``WARP_SPREAD_CHANCES``
  when the block is absent.
- Contagion graph: ``_compute_outgoing_infections`` honors the time window
  and inverts the recipient-side ``spread_history`` correctly.
- Super-spreader detection: ``_is_super_spreader`` honors the
  ``super_spreader_threshold`` config knob.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import opscribe.bot  # noqa: F401  (initializes the bot/tree before importing librarius_ops)
from opscribe import librarius_ops as lib
from opscribe.flavor_text import (
    WARP_PENALTY_PROBABILITIES,
    WARP_SPREAD_CHANCES,
    _warp_sanction_key_for_points,
)


# ---------------------------------------------------------------------------
# Sanction tiering
# ---------------------------------------------------------------------------


def test_sanction_key_clear_at_zero_or_below():
    assert _warp_sanction_key_for_points(0) == "sanctioned"
    assert _warp_sanction_key_for_points(-5) == "sanctioned"


def test_sanction_key_screening_due_band():
    assert _warp_sanction_key_for_points(1) == "screening_due"
    assert _warp_sanction_key_for_points(9) == "screening_due"


def test_sanction_key_under_review_band():
    assert _warp_sanction_key_for_points(10) == "under_review"
    assert _warp_sanction_key_for_points(19) == "under_review"


def test_sanction_key_restricted_band():
    assert _warp_sanction_key_for_points(20) == "restricted"
    assert _warp_sanction_key_for_points(999) == "restricted"


# ---------------------------------------------------------------------------
# Config readers — consolidated tier list
# ---------------------------------------------------------------------------


def _stub_warp_cfg(tiers=None, **extra):
    cfg = {"brother_probability_tiers": tiers or []}
    cfg.update(extra)
    return cfg


def test_get_penalty_probabilities_reads_from_consolidated_tiers():
    tiers = [
        {
            "min": 0,
            "max": 4,
            "tier": "tainted",
            "spread_chance": 0.20,
            "penalty_distribution": {"0": 0.9, "1": 0.1},
        },
        {
            "min": 5,
            "max": 9,
            "tier": "exposed",
            "spread_chance": 0.35,
            "penalty_distribution": {"0": 0.5, "1": 0.3, "2": 0.2},
        },
    ]
    with patch.object(lib, "_warp_config", return_value=_stub_warp_cfg(tiers)):
        out = lib._get_penalty_probabilities()
    assert out["tainted"] == {0: 0.9, 1: 0.1}
    assert out["exposed"] == {0: 0.5, 1: 0.3, 2: 0.2}


def test_get_penalty_probabilities_falls_back_when_block_missing():
    with patch.object(lib, "_warp_config", return_value={}):
        out = lib._get_penalty_probabilities()
    assert out is WARP_PENALTY_PROBABILITIES


def test_get_spread_chances_reads_from_consolidated_tiers():
    tiers = [
        {"tier": "tainted", "spread_chance": 0.20, "penalty_distribution": {}},
        {"tier": "exposed", "spread_chance": 0.50, "penalty_distribution": {}},
        {"tier": "breached", "spread_chance": 1.00, "penalty_distribution": {}},
    ]
    with patch.object(lib, "_warp_config", return_value=_stub_warp_cfg(tiers)):
        out = lib._get_spread_chances()
    assert out == {"tainted": 0.20, "exposed": 0.50, "breached": 1.00}


def test_get_spread_chances_falls_back_when_block_missing():
    with patch.object(lib, "_warp_config", return_value={}):
        out = lib._get_spread_chances()
    assert out == dict(WARP_SPREAD_CHANCES)


# ---------------------------------------------------------------------------
# Contagion graph
# ---------------------------------------------------------------------------


def _ts(hours_ago: float) -> str:
    return (datetime.utcnow() - timedelta(hours=hours_ago)).isoformat()


def test_compute_outgoing_infections_inverts_recipient_history():
    # Brother A (id=100) infected B, C, D in last 24h; E was infected by F.
    states = {
        "200": {"spread_history": [{"source_id": "100", "ts": _ts(1)}]},
        "300": {"spread_history": [{"source_id": "100", "ts": _ts(5)}]},
        "400": {"spread_history": [{"source_id": "100", "ts": _ts(20)}]},
        "500": {"spread_history": [{"source_id": "999", "ts": _ts(2)}]},
    }
    targets = lib._compute_outgoing_infections(100, states=states, window_hours=24)
    assert sorted(targets) == ["200", "300", "400"]


def test_compute_outgoing_infections_excludes_outside_window():
    states = {
        "200": {"spread_history": [{"source_id": "100", "ts": _ts(1)}]},
        "300": {"spread_history": [{"source_id": "100", "ts": _ts(48)}]},  # too old
    }
    targets = lib._compute_outgoing_infections(100, states=states, window_hours=24)
    assert targets == ["200"]


def test_compute_outgoing_infections_dedupes_repeat_edges():
    # Same (source, target) pair recorded twice should still count once.
    states = {
        "200": {
            "spread_history": [
                {"source_id": "100", "ts": _ts(1)},
                {"source_id": "100", "ts": _ts(2)},
            ]
        },
    }
    targets = lib._compute_outgoing_infections(100, states=states, window_hours=24)
    assert targets == ["200"]


def test_compute_outgoing_infections_handles_empty_states():
    assert lib._compute_outgoing_infections(100, states={}, window_hours=24) == []


def test_compute_outgoing_infections_skips_malformed_timestamps():
    states = {
        "200": {"spread_history": [{"source_id": "100", "ts": "not-a-date"}]},
        "300": {"spread_history": [{"source_id": "100"}]},  # missing ts
        "400": {"spread_history": [{"source_id": "100", "ts": _ts(2)}]},
    }
    targets = lib._compute_outgoing_infections(100, states=states, window_hours=24)
    assert targets == ["400"]


# ---------------------------------------------------------------------------
# Super-spreader detection
# ---------------------------------------------------------------------------


def test_is_super_spreader_at_threshold():
    states = {
        str(uid): {"spread_history": [{"source_id": "100", "ts": _ts(1)}]}
        for uid in (200, 300, 400)
    }
    with patch.object(lib, "_cfg_int", return_value=3):
        is_super, count = lib._is_super_spreader(100, states=states, window_hours=24)
    assert is_super is True
    assert count == 3


def test_is_super_spreader_below_threshold():
    states = {
        str(uid): {"spread_history": [{"source_id": "100", "ts": _ts(1)}]}
        for uid in (200, 300)
    }
    with patch.object(lib, "_cfg_int", return_value=3):
        is_super, count = lib._is_super_spreader(100, states=states, window_hours=24)
    assert is_super is False
    assert count == 2


def test_is_super_spreader_with_zero_threshold_disables_detection():
    states = {
        str(uid): {"spread_history": [{"source_id": "100", "ts": _ts(1)}]}
        for uid in (200, 300, 400, 500, 600)
    }
    with patch.object(lib, "_cfg_int", return_value=0):
        is_super, count = lib._is_super_spreader(100, states=states, window_hours=24)
    assert is_super is False
    assert count == 5


def test_is_super_spreader_no_outgoing_infections():
    with patch.object(lib, "_cfg_int", return_value=3):
        is_super, count = lib._is_super_spreader(100, states={}, window_hours=24)
    assert is_super is False
    assert count == 0
