"""Unit tests for campaign prestige calculation.

Covers:
- compute_kt_prestige: rolling 28-day window, entries inside/outside window
- compute_kt_prestige: 28-day boundary exact edge (in vs out)
- compute_company_prestige: 25% per-KT cap, multi-KT sum
- refresh_prestige_cache: caches totals on state objects
"""

import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup (before campaign_ops import)
# ---------------------------------------------------------------------------

def _setup_mock_bot():
    bg = types.ModuleType("opscribe._bot_globals")
    class FakeBot:
        class tree:
            @staticmethod
            def command(**kw):
                def dec(fn):
                    return fn
                return dec
    bg.bot = FakeBot()
    sys.modules["opscribe._bot_globals"] = bg


_setup_mock_bot()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def campaign_state_file(tmp_path, monkeypatch):
    """Redirect CAMPAIGN_STATE_PATH to a temp file and return it."""
    from opscribe import campaign_ops as c
    path = str(tmp_path / "campaign_state.json")
    monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
    return path, c


def _now():
    return datetime.now(tz=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _prestige_entry(earned_at, amount):
    return {
        "earned_at": _iso(earned_at),
        "member_id": "user1",
        "base_amount": amount,
        "multiplier": 1.0,
        "credited_amount": amount,
        "campaign_log_entry_id": "abc",
    }


def _make_state(kt_sgt_id, prestige_log, companies=None):
    return {
        "campaign": {"phase": "ops", "beat": 1},
        "enlistment": {},
        "companies": companies or {
            "primus": {"prestige_window_total": 0, "last_prestige_check": None},
        },
        "kill_teams": {
            kt_sgt_id: {
                "display_name": "Alpha",
                "company_id": "primus",
                "prestige_window_total": 0,
                "last_prestige_check": None,
                "prestige_log": prestige_log,
            }
        },
        "lore_priority": {"kill_team": {}, "company": {}},
        "ops_window": {},
        "strat_pool": {},
        "campaign_log": {},
        "beat_scenarios": {},
        "pressure": {},
        "cascade": {},
        "beat_record": {},
    }


# ---------------------------------------------------------------------------
# compute_kt_prestige — rolling window
# ---------------------------------------------------------------------------

class TestComputeKtPrestige:
    def test_sums_entries_within_window(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        entries = [
            _prestige_entry(now - timedelta(days=1), 5),
            _prestige_entry(now - timedelta(days=10), 20),
            _prestige_entry(now - timedelta(days=27), 5),
        ]
        state = _make_state("sgt1", entries)
        c._save_campaign_state(state)
        result = c.compute_kt_prestige("sgt1", window_days=28)
        assert result == 30  # 5 + 20 + 5

    def test_excludes_entries_outside_window(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        entries = [
            _prestige_entry(now - timedelta(days=1), 5),
            _prestige_entry(now - timedelta(days=29), 20),  # outside 28-day window
        ]
        state = _make_state("sgt1", entries)
        c._save_campaign_state(state)
        result = c.compute_kt_prestige("sgt1", window_days=28)
        assert result == 5

    def test_boundary_exactly_28_days_is_excluded(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        entries = [
            _prestige_entry(now - timedelta(days=28, seconds=1), 10),
        ]
        state = _make_state("sgt1", entries)
        c._save_campaign_state(state)
        result = c.compute_kt_prestige("sgt1", window_days=28)
        assert result == 0

    def test_returns_zero_for_unknown_sgt(self, campaign_state_file):
        path, c = campaign_state_file
        state = _make_state("sgt1", [])
        c._save_campaign_state(state)
        result = c.compute_kt_prestige("sgt_unknown")
        assert result == 0

    def test_empty_prestige_log_returns_zero(self, campaign_state_file):
        path, c = campaign_state_file
        state = _make_state("sgt1", [])
        c._save_campaign_state(state)
        result = c.compute_kt_prestige("sgt1")
        assert result == 0


# ---------------------------------------------------------------------------
# compute_company_prestige — 25% per KT cap
# ---------------------------------------------------------------------------

class TestComputeCompanyPrestige:
    def test_single_kt_25_percent_cap(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        # KT earns 40 prestige. Company should receive 25% = 10
        entries = [_prestige_entry(now - timedelta(days=1), 40)]
        state = _make_state("sgt1", entries)
        c._save_campaign_state(state)
        # KT total = 40 → company contribution = round(40 * 0.25) = 10
        result = c.compute_company_prestige("primus")
        assert result == 10

    def test_multiple_kts_summed(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        state = {
            "campaign": {"phase": "ops", "beat": 1},
            "enlistment": {},
            "companies": {
                "primus": {"prestige_window_total": 0, "last_prestige_check": None},
            },
            "kill_teams": {
                "sgt1": {
                    "display_name": "Alpha",
                    "company_id": "primus",
                    "prestige_window_total": 0,
                    "last_prestige_check": None,
                    "prestige_log": [_prestige_entry(now - timedelta(days=1), 40)],
                },
                "sgt2": {
                    "display_name": "Beta",
                    "company_id": "primus",
                    "prestige_window_total": 0,
                    "last_prestige_check": None,
                    "prestige_log": [_prestige_entry(now - timedelta(days=2), 80)],
                },
            },
            "lore_priority": {"kill_team": {}, "company": {}},
            "ops_window": {},
            "strat_pool": {},
            "campaign_log": {},
            "beat_scenarios": {},
            "pressure": {},
            "cascade": {},
            "beat_record": {},
        }
        c._save_campaign_state(state)
        # sgt1 contributes round(40*0.25)=10, sgt2 contributes round(80*0.25)=20
        result = c.compute_company_prestige("primus")
        assert result == 30

    def test_kts_from_other_company_excluded(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        state = {
            "campaign": {"phase": "ops", "beat": 1},
            "enlistment": {},
            "companies": {
                "primus": {"prestige_window_total": 0, "last_prestige_check": None},
                "secundus": {"prestige_window_total": 0, "last_prestige_check": None},
            },
            "kill_teams": {
                "sgt1": {
                    "display_name": "Alpha",
                    "company_id": "primus",
                    "prestige_window_total": 0,
                    "last_prestige_check": None,
                    "prestige_log": [_prestige_entry(now - timedelta(days=1), 40)],
                },
                "sgt2": {
                    "display_name": "Beta",
                    "company_id": "secundus",  # different company
                    "prestige_window_total": 0,
                    "last_prestige_check": None,
                    "prestige_log": [_prestige_entry(now - timedelta(days=1), 100)],
                },
            },
            "lore_priority": {"kill_team": {}, "company": {}},
            "ops_window": {},
            "strat_pool": {},
            "campaign_log": {},
            "beat_scenarios": {},
            "pressure": {},
            "cascade": {},
            "beat_record": {},
        }
        c._save_campaign_state(state)
        result = c.compute_company_prestige("primus")
        assert result == 10  # Only sgt1's 25% of 40


# ---------------------------------------------------------------------------
# refresh_prestige_cache
# ---------------------------------------------------------------------------

class TestRefreshPrestigeCache:
    def test_updates_kt_prestige_window_total(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        entries = [_prestige_entry(now - timedelta(days=1), 20)]
        state = _make_state("sgt1", entries)
        c._save_campaign_state(state)
        updated = c.refresh_prestige_cache()
        assert updated["kill_teams"]["sgt1"]["prestige_window_total"] == 20

    def test_updates_company_prestige_window_total(self, campaign_state_file):
        path, c = campaign_state_file
        now = _now()
        entries = [_prestige_entry(now - timedelta(days=1), 40)]
        state = _make_state("sgt1", entries)
        c._save_campaign_state(state)
        updated = c.refresh_prestige_cache()
        assert updated["companies"]["primus"]["prestige_window_total"] == 10  # 25% of 40

    def test_sets_last_prestige_check(self, campaign_state_file):
        path, c = campaign_state_file
        state = _make_state("sgt1", [])
        c._save_campaign_state(state)
        updated = c.refresh_prestige_cache()
        assert updated["kill_teams"]["sgt1"]["last_prestige_check"] is not None
        assert updated["companies"]["primus"]["last_prestige_check"] is not None
