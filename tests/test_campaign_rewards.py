"""Unit tests for campaign rewards: hysteresis thresholds, lore priority,
and home chapter priority logic.

Covers:
- check_kt_ribbon_active: acquire threshold gate (below → no ribbon)
- check_kt_ribbon_active: acquire threshold gate (at/above → ribbon granted)
- check_kt_ribbon_active: retain threshold (below retain → ribbon lost)
- check_kt_ribbon_active: retain threshold (above retain, below acquire → ribbon kept)
- check_reward_thresholds: co_ribbon_active numeric hysteresis
- check_reward_thresholds: kt_ribbon_vanguard relative (most ops, min 5)
- check_reward_thresholds: kt_honour_stalwart acquire/retain
- update_lore_priority: no KT above floor → lore_priority.kill_team is null
- update_lore_priority: KT above floor → lore_priority.kill_team set
- update_lore_priority: retain floor: KT holding lore priority at retain (< floor)
- update_lore_priority: company lore priority acquire/retain
"""

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup
# ---------------------------------------------------------------------------

def _setup_mock_bot():
    if "opscribe._bot_globals" not in sys.modules:
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
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(tz=timezone.utc)


def _make_kt(prestige, company_id="primus", ribbon=None, lore_priority=False):
    return {
        "display_name": "Test KT",
        "company_id": company_id,
        "prestige_window_total": prestige,
        "last_prestige_check": None,
        "prestige_log": [],
        "ribbon": ribbon,
        "honour": [],
        "title": None,
        "title_granted_by": None,
        "title_granted_at": None,
        "lore_priority": lore_priority,
    }


def _make_company(prestige, ribbon=None, honour=None, lore_priority=False):
    return {
        "display_name": "Test Company",
        "prestige_window_total": prestige,
        "last_prestige_check": None,
        "ribbon": ribbon,
        "honour": honour,
        "lore_priority": lore_priority,
        "title": None,
        "title_granted_by": None,
        "title_granted_at": None,
    }


def _base_state(kill_teams=None, companies=None, campaign_log=None, enlistment=None):
    return {
        "campaign": {"phase": "ops", "beat": 1},
        "enlistment": enlistment or {},
        "companies": companies or {"primus": _make_company(0)},
        "kill_teams": kill_teams or {},
        "lore_priority": {
            "kill_team": {"sgt_user_id": None, "display_name": None, "prestige": None, "held_since": None},
            "company": {"company_id": None, "display_name": None, "prestige": None, "held_since": None},
        },
        "ops_window": {},
        "strat_pool": {},
        "campaign_log": campaign_log or {},
        "beat_scenarios": {},
        "pressure": {},
        "cascade": {},
        "beat_record": {},
    }


# ---------------------------------------------------------------------------
# check_kt_ribbon_active (hysteresis)
# ---------------------------------------------------------------------------

class TestKtRibbonActiveHysteresis:
    def test_below_acquire_threshold_no_ribbon(self):
        from opscribe import campaign_ops as c
        state = _base_state(kill_teams={"sgt1": _make_kt(90)})
        # Below acquire threshold (100) → should NOT qualify
        assert c.check_kt_ribbon_active("sgt1", state) is False

    def test_at_acquire_threshold_gets_ribbon(self):
        from opscribe import campaign_ops as c
        state = _base_state(kill_teams={"sgt1": _make_kt(100)})
        assert c.check_kt_ribbon_active("sgt1", state) is True

    def test_above_acquire_threshold_gets_ribbon(self):
        from opscribe import campaign_ops as c
        state = _base_state(kill_teams={"sgt1": _make_kt(150)})
        assert c.check_kt_ribbon_active("sgt1", state) is True

    def test_holding_ribbon_below_retain_loses_it(self):
        from opscribe import campaign_ops as c
        # Already has ribbon, but drops below retain (60)
        state = _base_state(kill_teams={"sgt1": _make_kt(50, ribbon="kt_ribbon_active")})
        assert c.check_kt_ribbon_active("sgt1", state) is False

    def test_holding_ribbon_at_retain_keeps_it(self):
        from opscribe import campaign_ops as c
        # Already has ribbon, at retain threshold (60) — should keep
        state = _base_state(kill_teams={"sgt1": _make_kt(60, ribbon="kt_ribbon_active")})
        assert c.check_kt_ribbon_active("sgt1", state) is True

    def test_holding_ribbon_between_retain_and_acquire_keeps_it(self):
        from opscribe import campaign_ops as c
        # Has ribbon, between retain (60) and acquire (100) — keeps ribbon (hysteresis)
        state = _base_state(kill_teams={"sgt1": _make_kt(80, ribbon="kt_ribbon_active")})
        assert c.check_kt_ribbon_active("sgt1", state) is True

    def test_not_holding_ribbon_between_retain_and_acquire_no_grant(self):
        from opscribe import campaign_ops as c
        # Does NOT have ribbon, between retain (60) and acquire (100) — no grant
        state = _base_state(kill_teams={"sgt1": _make_kt(80, ribbon=None)})
        assert c.check_kt_ribbon_active("sgt1", state) is False


# ---------------------------------------------------------------------------
# check_reward_thresholds — company ribbon active hysteresis
# ---------------------------------------------------------------------------

class TestCompanyRibbonHysteresis:
    def test_company_below_acquire_no_ribbon(self):
        from opscribe import campaign_ops as c
        state = _base_state(companies={"primus": _make_company(150)})
        result = c.check_reward_thresholds(state)
        assert result["companies"]["primus"]["ribbon"] is None

    def test_company_at_acquire_gets_ribbon(self):
        from opscribe import campaign_ops as c
        state = _base_state(companies={"primus": _make_company(200)})
        result = c.check_reward_thresholds(state)
        assert result["companies"]["primus"]["ribbon"] == "co_ribbon_active"

    def test_company_holding_ribbon_below_retain_loses_it(self):
        from opscribe import campaign_ops as c
        state = _base_state(companies={"primus": _make_company(100, ribbon="co_ribbon_active")})
        result = c.check_reward_thresholds(state)
        assert result["companies"]["primus"]["ribbon"] is None

    def test_company_holding_ribbon_above_retain_keeps_it(self):
        from opscribe import campaign_ops as c
        state = _base_state(companies={"primus": _make_company(130, ribbon="co_ribbon_active")})
        result = c.check_reward_thresholds(state)
        assert result["companies"]["primus"]["ribbon"] == "co_ribbon_active"


# ---------------------------------------------------------------------------
# check_reward_thresholds — kt_ribbon_vanguard (relative)
# ---------------------------------------------------------------------------

class TestKtRibbonVanguard:
    def _make_log_entries(self, n, user_id, sgt_id):
        """Build campaign_log with n entries for the given user."""
        log = {}
        enlistment = {
            user_id: {"tier": "KT", "kt_sgt_id": sgt_id, "company_id": "primus", "active": True}
        }
        for i in range(n):
            log[f"e{i}_{sgt_id}"] = {"submitted_by": user_id, "is_omega": False, "terminus_killed": False}
        return log, enlistment

    def test_kt_with_most_ops_above_min_gets_vanguard(self):
        from opscribe import campaign_ops as c
        log1, enl1 = self._make_log_entries(8, "u1", "sgt1")
        log2, enl2 = self._make_log_entries(3, "u2", "sgt2")
        log = {**log1, **log2}
        enlistment = {**enl1, **enl2}
        state = _base_state(
            kill_teams={
                "sgt1": _make_kt(0),
                "sgt2": _make_kt(0),
            },
            campaign_log=log,
            enlistment=enlistment,
        )
        result = c.check_reward_thresholds(state)
        assert result["kill_teams"]["sgt1"]["ribbon"] == "kt_ribbon_vanguard"

    def test_kt_below_min_5_ops_no_vanguard(self):
        from opscribe import campaign_ops as c
        log1, enl1 = self._make_log_entries(4, "u1", "sgt1")
        state = _base_state(
            kill_teams={"sgt1": _make_kt(0)},
            campaign_log=log1,
            enlistment=enl1,
        )
        result = c.check_reward_thresholds(state)
        assert result["kill_teams"]["sgt1"]["ribbon"] is None


# ---------------------------------------------------------------------------
# KT honour stalwart hysteresis
# ---------------------------------------------------------------------------

class TestKtHonourStalwart:
    def test_below_acquire_no_honour(self):
        from opscribe import campaign_ops as c
        state = _base_state(kill_teams={"sgt1": _make_kt(500)})
        result = c.check_reward_thresholds(state)
        assert "kt_honour_stalwart" not in result["kill_teams"]["sgt1"]["honour"]

    def test_at_acquire_gets_honour(self):
        from opscribe import campaign_ops as c
        state = _base_state(kill_teams={"sgt1": _make_kt(550)})
        result = c.check_reward_thresholds(state)
        assert "kt_honour_stalwart" in result["kill_teams"]["sgt1"]["honour"]

    def test_holding_honour_above_retain_keeps_it(self):
        from opscribe import campaign_ops as c
        kt = _make_kt(400)
        kt["honour"] = ["kt_honour_stalwart"]
        state = _base_state(kill_teams={"sgt1": kt})
        result = c.check_reward_thresholds(state)
        assert "kt_honour_stalwart" in result["kill_teams"]["sgt1"]["honour"]

    def test_holding_honour_below_retain_loses_it(self):
        from opscribe import campaign_ops as c
        kt = _make_kt(300)  # below retain (350)
        kt["honour"] = ["kt_honour_stalwart"]
        state = _base_state(kill_teams={"sgt1": kt})
        result = c.check_reward_thresholds(state)
        assert "kt_honour_stalwart" not in result["kill_teams"]["sgt1"]["honour"]


# ---------------------------------------------------------------------------
# update_lore_priority
# ---------------------------------------------------------------------------

class TestUpdateLorePriority:
    def test_no_kt_above_floor_lore_priority_vacant(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        state = _base_state(kill_teams={"sgt1": _make_kt(150)})  # below floor (180)
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["kill_team"]["sgt_user_id"] is None

    def test_kt_above_floor_gets_lore_priority(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        state = _base_state(kill_teams={"sgt1": _make_kt(200)})  # above floor (180)
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["kill_team"]["sgt_user_id"] == "sgt1"

    def test_kt_holding_lore_priority_at_retain_keeps_it(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        # Already holds priority; prestige is between retain (100) and floor (180)
        kt = _make_kt(120, lore_priority=True)
        state = _base_state(kill_teams={"sgt1": kt})
        state["lore_priority"]["kill_team"] = {
            "sgt_user_id": "sgt1",
            "display_name": "Test KT",
            "prestige": 120,
            "held_since": "2026-01-01T00:00:00+00:00",
        }
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["kill_team"]["sgt_user_id"] == "sgt1"

    def test_kt_holding_lore_priority_below_retain_loses_it(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        kt = _make_kt(80, lore_priority=True)  # below retain (100)
        state = _base_state(kill_teams={"sgt1": kt})
        state["lore_priority"]["kill_team"] = {
            "sgt_user_id": "sgt1",
            "display_name": "Test KT",
            "prestige": 80,
            "held_since": "2026-01-01T00:00:00+00:00",
        }
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["kill_team"]["sgt_user_id"] is None

    def test_company_above_floor_gets_lore_priority(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        state = _base_state(companies={"primus": _make_company(400)})  # above floor (350)
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["company"]["company_id"] == "primus"

    def test_company_below_floor_no_lore_priority(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        state = _base_state(companies={"primus": _make_company(300)})  # below floor (350)
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["company"]["company_id"] is None

    def test_highest_prestige_kt_wins_lore_priority(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        state = _base_state(kill_teams={
            "sgt1": _make_kt(200),
            "sgt2": _make_kt(350),  # sgt2 should win
        })
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["lore_priority"]["kill_team"]["sgt_user_id"] == "sgt2"

    def test_lore_priority_flag_set_on_kt(self, tmp_path, monkeypatch):
        from opscribe import campaign_ops as c
        path = str(tmp_path / "campaign_state.json")
        monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
        state = _base_state(kill_teams={
            "sgt1": _make_kt(200),
            "sgt2": _make_kt(350),
        })
        c._save_campaign_state(state)
        updated = c.update_lore_priority(state, save=True)
        assert updated["kill_teams"]["sgt2"]["lore_priority"] is True
        assert updated["kill_teams"]["sgt1"]["lore_priority"] is False
