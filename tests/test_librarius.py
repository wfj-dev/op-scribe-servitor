"""Unit tests for the Librarian / Warp Corruption subsystem.

Covers:
- Sanction tiering: ``_warp_sanction_key_for_points`` boundary behavior.
- Config readers: ``_get_penalty_probabilities`` returns the flavor-text
  defaults (no longer config-overridable) and ``_get_spread_chances`` reads
  ``spread_chances_by_tier`` with a sane fallback.
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
    assert _warp_sanction_key_for_points(4) == "screening_due"


def test_sanction_key_under_review_band():
    assert _warp_sanction_key_for_points(5) == "under_review"
    assert _warp_sanction_key_for_points(9) == "under_review"


def test_sanction_key_restricted_band():
    assert _warp_sanction_key_for_points(10) == "restricted"
    assert _warp_sanction_key_for_points(999) == "restricted"


# ---------------------------------------------------------------------------
# Config readers — new schema
# ---------------------------------------------------------------------------


def test_get_penalty_probabilities_returns_flavor_defaults():
    # In the new (armor-mirror) schema the penalty distribution lives in the
    # flavor-text constant; config does not override it.
    out = lib._get_penalty_probabilities()
    assert out is WARP_PENALTY_PROBABILITIES


def test_get_spread_chances_reads_from_config_block():
    cfg = {
        "spread_chances_by_tier": {
            "tainted": 0.20,
            "exposed": 0.50,
            "volatile": 1.00,
        }
    }
    with patch.object(lib, "_warp_config", return_value=cfg):
        out = lib._get_spread_chances()
    assert out == {"tainted": 0.20, "exposed": 0.50, "volatile": 1.00}


def test_get_spread_chances_falls_back_when_block_missing():
    with patch.object(lib, "_warp_config", return_value={}):
        out = lib._get_spread_chances()
    assert out == dict(WARP_SPREAD_CHANCES)


def test_get_bl_exposure_gain_partial_override_keeps_new_defaults():
    cfg = {"bl_susceptibility_gain": {"absolute": 9}}
    with patch.object(lib, "_warp_config", return_value=cfg):
        out = lib._get_bl_exposure_gain()
    assert out == {"absolute": 9, "hard_stratagem": 5, "omega_ops": 20}


# ---------------------------------------------------------------------------
# Infection roll state machine
# ---------------------------------------------------------------------------


def test_infection_probability_tier_selects_open_ended_top_band():
    with patch.object(lib, "_get_infection_probability_tiers", return_value=[
        {"min": 0, "max": 4, "chance": 0.0},
        {"min": 5, "max": None, "chance": 0.4},
    ]):
        tier = lib._get_infection_probability_tier_for_points(123)
    assert tier == {"min": 5, "max": None, "chance": 0.4}


def test_roll_infection_tier_non_positive_points_never_infect():
    with patch.object(lib, "_get_infection_probability_tiers", return_value=[
        {"min": 0, "max": 10, "chance": 1.0, "infection_weights": {"volatile": 1}},
    ]):
        assert lib._roll_infection_tier(0) is None
        assert lib._roll_infection_tier(-3) is None


def test_roll_infection_tier_malformed_weights_falls_back_to_tainted():
    with (
        patch.object(lib, "_get_infection_probability_tiers", return_value=[
            {"min": 1, "max": None, "chance": 1.0, "infection_weights": {"tainted": "bad"}},
        ]),
        patch.object(lib.random, "random", return_value=0.0),
    ):
        rolled = lib._roll_infection_tier(5)
    assert rolled == "tainted"


def test_escalate_infection_volatile_reroll_sets_corrupted_flag():
    new_state, became_corrupted = lib._escalate_infection("volatile", "volatile")
    assert new_state == "volatile"
    assert became_corrupted is True


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


# ---------------------------------------------------------------------------
# Intensive cleanse cost lookup
# ---------------------------------------------------------------------------


def test_intensive_cleanse_cost_scales_with_severity():
    cfg = {"intensive_cleanse_costs": {
        "screening_due": 2, "under_review": 3, "restricted": 4, "corrupted": 4,
    }}
    with patch.object(lib, "_warp_config", return_value=cfg):
        assert lib._get_intensive_cleanse_cost("screening_due") == 2
        assert lib._get_intensive_cleanse_cost("under_review") == 3
        assert lib._get_intensive_cleanse_cost("restricted") == 4


def test_intensive_cleanse_cost_corrupted_promotes_to_top_tier():
    cfg = {"intensive_cleanse_costs": {
        "screening_due": 2, "under_review": 3, "restricted": 4, "corrupted": 5,
    }}
    with patch.object(lib, "_warp_config", return_value=cfg):
        # Corrupted flag overrides the tier cost (mirrors armor's fractured override)
        assert lib._get_intensive_cleanse_cost("screening_due", warp_corrupted=True) == 5
        assert lib._get_intensive_cleanse_cost("under_review", warp_corrupted=True) == 5


def test_intensive_cleanse_cost_falls_back_to_defaults():
    with patch.object(lib, "_warp_config", return_value={}):
        assert lib._get_intensive_cleanse_cost("restricted") == 4
        assert lib._get_intensive_cleanse_cost("screening_due", warp_corrupted=True) == 4


# ---------------------------------------------------------------------------
# Display name normalization
# ---------------------------------------------------------------------------


def test_normalize_display_name_small_caps():
    from opscribe.constants import _normalize_display_name
    assert _normalize_display_name("ᴡᴀᴛᴄʜ ᴄʜᴀᴘʟᴀɪɴ").lower() == "watch chaplain"


def test_normalize_display_name_mathematical_alphanumeric():
    from opscribe.constants import _normalize_display_name
    assert _normalize_display_name("𝐡𝐞𝐥𝐥𝐨") == "hello"


def test_normalize_display_name_preserves_plain_ascii():
    from opscribe.constants import _normalize_display_name
    assert _normalize_display_name("Brother Marcus") == "Brother Marcus"


def test_strip_display_name_removes_pips_and_normalizes():
    from opscribe.constants import _strip_display_name
    assert _strip_display_name("●● ᴋᴏʀᴀ ⚬⚬").lower() == "kora"


# ---------------------------------------------------------------------------
# Company scope ring (gap-filling for /armor_status, /warp_status)
# ---------------------------------------------------------------------------


def test_company_scope_ring_own_company_is_zero():
    from opscribe.roster_ops import _company_scope_ring
    orphans = {"Watch Company Tertius"}
    assert _company_scope_ring(
        member_company="Watch Company Primus",
        caller_company="Watch Company Primus",
        orphan_companies=orphans,
    ) == 0


def test_company_scope_ring_orphan_is_one():
    from opscribe.roster_ops import _company_scope_ring
    orphans = {"Watch Company Tertius"}
    assert _company_scope_ring(
        member_company="Watch Company Tertius",
        caller_company="Watch Company Primus",
        orphan_companies=orphans,
    ) == 1


def test_company_scope_ring_peer_covered_is_two():
    from opscribe.roster_ops import _company_scope_ring
    orphans = {"Watch Company Tertius"}
    # Secundus is not in orphan set, so it's peer-covered
    assert _company_scope_ring(
        member_company="Watch Company Secundus",
        caller_company="Watch Company Primus",
        orphan_companies=orphans,
    ) == 2


def test_company_scope_ring_no_company_is_three():
    from opscribe.roster_ops import _company_scope_ring
    assert _company_scope_ring(
        member_company=None,
        caller_company="Watch Company Primus",
        orphan_companies=set(),
    ) == 3


def test_company_scope_ring_no_caller_company_treats_match_as_zero():
    """When caller has no company, no member matches as ring 0; orphans still ring 1."""
    from opscribe.roster_ops import _company_scope_ring
    orphans = {"Watch Company Tertius"}
    # caller has no company, so no member should land in ring 0
    assert _company_scope_ring(
        member_company="Watch Company Primus",
        caller_company=None,
        orphan_companies=orphans,
    ) == 2  # peer-covered, not own
