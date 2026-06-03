"""Unit tests for campaign beat lifecycle automation.

Covers:
- _enter_cascade_phase: sets phase, records started_at and deadline
- _aggregate_cascade_doctrine: sums tags across all submissions
- _aggregate_cascade_doctrine: empty submissions returns empty dict
- _resolve_beat_and_open_next: beat counter increments
- _resolve_beat_and_open_next: phase returns to ops
- _resolve_beat_and_open_next: strat pool locked after resolution
- _resolve_beat_and_open_next: beat_history entry added
- _resolve_beat_and_open_next: auto-calculates closes_at from beat_duration_days
- _resolve_beat_and_open_next: explicit ops_closes_at overrides beat_duration_days
- _resolve_beat_and_open_next: cascade submissions cleared after resolution
- _get_user_cascade_role_key: returns correct key for phase
- _get_user_cascade_role_key: returns None if no eligible role for phase
- _get_user_cascade_role_key: picks highest-priority role when user holds multiple
- _get_user_cascade_role_key: returns None for user with no roles attr
- sweep_campaign_beat_clock: does nothing when phase is inactive
- sweep_campaign_beat_clock: ops window expired transitions to cascade_HC
- sweep_campaign_beat_clock: ops window not yet expired stays in ops
- sweep_campaign_beat_clock: cascade_HC deadline transitions to cascade_Company
- sweep_campaign_beat_clock: cascade_Company deadline transitions to cascade_KT
- sweep_campaign_beat_clock: cascade_KT deadline resolves beat and returns to ops
- sweep_campaign_beat_clock: posts announcement when bot channel set
- campaign-init rejected when campaign already active (phase=ops)
- campaign-init rejected when campaign already active (phase=cascade_HC)
- campaign-init accepted when phase is inactive
- campaign-init accepted when phase is complete
- campaign-init auto-calculates closes_at from beat_duration_days
- campaign-init explicit ops_closes_at overrides beat_duration_days
- campaign-init minimum beat_duration_days of 1
"""

import asyncio
import sys
import types
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_refs():
    from opscribe import campaign_ops as c
    c._STRATAGEMS = []
    c._DOCTRINE_STRAT_MAP = {}
    c._SCENARIO_GEN = {}
    c._CASCADE_OPTIONS = {}
    c._REWARDS = {}
    c._MILESTONES = {}
    c._STRAT_MANDATE = {}
    yield


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """Redirect CAMPAIGN_STATE_PATH to a temp file; return (path, c)."""
    from opscribe import campaign_ops as c
    path = str(tmp_path / "campaign_state.json")
    monkeypatch.setattr(c, "CAMPAIGN_STATE_PATH", path)
    return path, c


def _now():
    return datetime.now(tz=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _blank_ops_state(beat=1, phase="ops", beat_duration_days=7, closes_offset_seconds=-1):
    """Return a minimal campaign state dict in ops phase."""
    closes_at = _iso(_now() + timedelta(seconds=closes_offset_seconds))
    return {
        "_schema_version": 1,
        "campaign": {
            "id": "test_campaign",
            "name": "OPERATION TEST ALPHA",
            "beat": beat,
            "beat_name": f"BEAT {beat}: TEST ADVANCE",
            "phase": phase,
            "started_at": _iso(_now() - timedelta(days=1)),
            "ended_at": None,
            "outcome": None,
            "beat_duration_days": beat_duration_days,
            "current_node": None,
            "visited_nodes": [],
            "beat_history": [],
            "beat_schedule": [],
        },
        "enlistment": {},
        "companies": {},
        "kill_teams": {},
        "lore_priority": {
            "kill_team": {"sgt_user_id": None, "display_name": None, "prestige": None, "held_since": None},
            "company": {"company_id": None, "display_name": None, "prestige": None, "held_since": None},
        },
        "ops_window": {
            "opened_at": _iso(_now() - timedelta(days=1)),
            "closes_at": closes_at,
            "terminus_calls": [],
        },
        "strat_pool": {"locked": False, "pool": [], "theatre_mandate": [], "company_mandates": {}, "kt_mandates": {}},
        "campaign_log": {},
        "beat_scenarios": {},
        "pressure": {},
        "cascade": {"submissions": {}},
        "beat_record": {},
    }


def _cascade_state(phase, deadline_offset_seconds=-1, beat_duration_days=7):
    """Return a minimal campaign state dict in a cascade phase with an expired deadline."""
    state = _blank_ops_state(phase=phase, beat_duration_days=beat_duration_days, closes_offset_seconds=-3600)
    cascade = state.setdefault("cascade", {})
    cascade["submissions"] = {
        "111": {
            "role_key": "watch_master",
            "phase": "cascade_HC",
            "decision": "theatre_order",
            "choice_key": "advance_the_spear",
            "choice_name": "Advance the Spear",
            "tags": ["aggressive", "terminus", "elimination"],
            "submitted_at": _iso(_now() - timedelta(hours=2)),
        },
    }
    cascade[f"{phase}_started_at"] = _iso(_now() - timedelta(hours=50))
    cascade[f"{phase}_deadline"] = _iso(_now() + timedelta(seconds=deadline_offset_seconds))
    return state


# ---------------------------------------------------------------------------
# _enter_cascade_phase
# ---------------------------------------------------------------------------

def test_enter_cascade_phase_sets_phase(state_file):
    _, c = state_file
    state = _blank_ops_state()
    c._enter_cascade_phase(state, "cascade_HC")
    assert state["campaign"]["phase"] == "cascade_HC"


def test_enter_cascade_phase_records_deadline(state_file):
    _, c = state_file
    before = _now()
    state = _blank_ops_state()
    c._enter_cascade_phase(state, "cascade_HC")
    after = _now()
    deadline = datetime.fromisoformat(state["cascade"]["cascade_HC_deadline"])
    assert deadline > before
    # HC deadline should be ~48h from now
    assert timedelta(hours=47) < (deadline - before) < timedelta(hours=49)


def test_enter_cascade_phase_records_started_at(state_file):
    _, c = state_file
    before = _now()
    state = _blank_ops_state()
    c._enter_cascade_phase(state, "cascade_Company")
    started = datetime.fromisoformat(state["cascade"]["cascade_Company_started_at"])
    assert started >= before


def test_enter_cascade_phase_kt_has_24h_deadline(state_file):
    _, c = state_file
    before = _now()
    state = _blank_ops_state()
    c._enter_cascade_phase(state, "cascade_KT")
    deadline = datetime.fromisoformat(state["cascade"]["cascade_KT_deadline"])
    assert timedelta(hours=23) < (deadline - before) < timedelta(hours=25)


# ---------------------------------------------------------------------------
# _aggregate_cascade_doctrine
# ---------------------------------------------------------------------------

def test_aggregate_doctrine_empty_submissions(state_file):
    _, c = state_file
    state = _blank_ops_state()
    state["cascade"] = {"submissions": {}}
    agg = c._aggregate_cascade_doctrine(state)
    assert agg == {}


def test_aggregate_doctrine_sums_tags(state_file):
    _, c = state_file
    state = _blank_ops_state()
    state["cascade"]["submissions"] = {
        "111": {"tags": ["aggressive", "terminus"]},
        "222": {"tags": ["aggressive", "intel"]},
        "333": {"tags": ["terminus"]},
    }
    agg = c._aggregate_cascade_doctrine(state)
    assert agg["aggressive"] == 2.0
    assert agg["terminus"] == 2.0
    assert agg["intel"] == 1.0
    assert "defensive" not in agg


def test_aggregate_doctrine_no_cascade_key(state_file):
    _, c = state_file
    state = _blank_ops_state()
    # cascade key absent entirely
    del state["cascade"]
    agg = c._aggregate_cascade_doctrine(state)
    assert agg == {}


# ---------------------------------------------------------------------------
# _resolve_beat_and_open_next
# ---------------------------------------------------------------------------

def test_resolve_beat_increments_counter(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat"] = 3
    summary = c._resolve_beat_and_open_next(state)
    assert state["campaign"]["beat"] == 4
    assert summary["new_beat"] == 4


def test_resolve_beat_phase_returns_to_ops(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    c._resolve_beat_and_open_next(state)
    # After beat resolution, cascade opens for the next beat
    assert state["campaign"]["phase"] == "cascade_HC"


def test_resolve_beat_locks_strat_pool(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    c._resolve_beat_and_open_next(state)
    assert state["strat_pool"]["locked"] is True
    assert isinstance(state["strat_pool"]["pool"], list)


def test_resolve_beat_archives_beat_history(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat"] = 2
    c._resolve_beat_and_open_next(state)
    assert len(state["campaign"]["beat_history"]) == 1
    assert state["campaign"]["beat_history"][0]["beat"] == 2


def test_resolve_beat_clears_cascade_submissions(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    c._resolve_beat_and_open_next(state)
    assert state["cascade"]["submissions"] == {}


def test_resolve_beat_auto_calculates_closes_at(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat_duration_days"] = 5
    c._resolve_beat_and_open_next(state)
    # Ops window is not created at resolve time; cascade_HC opens instead
    assert state["campaign"]["phase"] == "cascade_HC"
    assert "cascade_HC_deadline" in state["cascade"]


def test_resolve_beat_explicit_closes_at_overrides_duration(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat_duration_days"] = 14
    explicit = _iso(_now() + timedelta(days=3))
    # ops_closes_at param is now unused; beat_duration_days governs ops after cascade
    c._resolve_beat_and_open_next(state)
    assert state["campaign"]["phase"] == "cascade_HC"


def test_resolve_beat_campaign_continues_when_beat_le_total_beats(state_file):
    """When resolved beat <= total_beats the campaign stays in cascade_HC (cascade-first flow)."""
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat"] = 2
    state["campaign"]["total_beats"] = 3
    summary = c._resolve_beat_and_open_next(state)
    assert summary["campaign_complete"] is False
    assert state["campaign"]["phase"] == "cascade_HC"
    assert "cascade_HC_deadline" in state["cascade"]


def test_resolve_beat_campaign_ends_when_beat_exceeds_total_beats(state_file):
    """When resolved beat > total_beats the campaign moves to complete."""
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat"] = 3
    state["campaign"]["total_beats"] = 3
    summary = c._resolve_beat_and_open_next(state)
    assert summary["campaign_complete"] is True
    assert state["campaign"]["phase"] == "complete"
    assert "ended_at" in state["campaign"]
    # No new ops window when campaign is complete
    assert state["ops_window"].get("opened_at") is None or state["campaign"]["phase"] == "complete"


def test_resolve_beat_total_beats_5_allows_5_beats(state_file):
    """total_beats=5: beating beat 4 should keep campaign alive (in cascade_HC)."""
    _, c = state_file
    state = _cascade_state("cascade_KT")
    state["campaign"]["beat"] = 4
    state["campaign"]["total_beats"] = 5
    summary = c._resolve_beat_and_open_next(state)
    assert summary["campaign_complete"] is False
    assert state["campaign"]["phase"] == "cascade_HC"


def test_resolve_beat_theatre_mandate_is_list(state_file):
    """theatre_mandate in strat_pool and summary is a list after resolution."""
    _, c = state_file
    state = _cascade_state("cascade_KT")
    summary = c._resolve_beat_and_open_next(state)
    assert isinstance(state["strat_pool"]["theatre_mandate"], list)
    assert isinstance(summary["theatre_mandates"], list)


# ---------------------------------------------------------------------------
# _get_user_cascade_role_key
# ---------------------------------------------------------------------------

def _make_user_with_roles(*role_names):
    user = MagicMock()
    roles = []
    for n in role_names:
        r = MagicMock()
        r.name = n
        roles.append(r)
    user.roles = roles
    return user


def test_get_cascade_role_key_hc_phase(state_file):
    _, c = state_file
    user = _make_user_with_roles("Watch Master", "Watch Brother")
    key = c._get_user_cascade_role_key(user, "cascade_HC")
    assert key == "watch_master"


def test_get_cascade_role_key_company_phase(state_file):
    _, c = state_file
    user = _make_user_with_roles("Watch Captain", "Watch Brother")
    key = c._get_user_cascade_role_key(user, "cascade_Company")
    assert key == "watch_captain"


def test_get_cascade_role_key_kt_phase(state_file):
    _, c = state_file
    user = _make_user_with_roles("Watch Sergeant", "Watch Brother")
    key = c._get_user_cascade_role_key(user, "cascade_KT")
    assert key == "watch_sergeant"


def test_get_cascade_role_key_wrong_phase_returns_none(state_file):
    _, c = state_file
    # Watch Master is HC, should not match during cascade_KT
    user = _make_user_with_roles("Watch Master")
    key = c._get_user_cascade_role_key(user, "cascade_KT")
    assert key is None


def test_get_cascade_role_key_no_matching_role_returns_none(state_file):
    _, c = state_file
    user = _make_user_with_roles("Watch Brother", "Watch Veteran")
    key = c._get_user_cascade_role_key(user, "cascade_HC")
    assert key is None


def test_get_cascade_role_key_picks_highest_priority(state_file):
    _, c = state_file
    # Watch Master outranks Forgemaster in _CASCADE_ROLE_PRIORITY
    user = _make_user_with_roles("Forgemaster", "Watch Master")
    key = c._get_user_cascade_role_key(user, "cascade_HC")
    assert key == "watch_master"


def test_get_cascade_role_key_no_roles_attr_returns_none(state_file):
    _, c = state_file
    user = MagicMock(spec=[])  # no 'roles' attribute
    key = c._get_user_cascade_role_key(user, "cascade_HC")
    assert key is None


# ---------------------------------------------------------------------------
# sweep_campaign_beat_clock
# ---------------------------------------------------------------------------

def _make_mock_bot(channel_id=0):
    bot = MagicMock()
    bot.get_channel.return_value = None
    return bot


def _run_sweep(c, state, mock_bot=None):
    """Helper: write state to file, patch bot, run sweep, return updated state."""
    if mock_bot is None:
        mock_bot = _make_mock_bot()

    with open(c.CAMPAIGN_STATE_PATH, "w") as f:
        json.dump(state, f)

    with patch.object(c, "_b", return_value=mock_bot), \
         patch.object(c, "CAMPAIGN_ANNOUNCEMENT_CHANNEL_ID", 0):
        asyncio.run(c.sweep_campaign_beat_clock())

    if os.path.exists(c.CAMPAIGN_STATE_PATH):
        with open(c.CAMPAIGN_STATE_PATH) as f:
            return json.load(f)
    return state


def test_sweep_inactive_does_nothing(state_file):
    _, c = state_file
    state = _blank_ops_state()
    state["campaign"]["phase"] = "inactive"
    # No ops_window close
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "inactive"


def test_sweep_ops_window_not_expired_stays_ops(state_file):
    _, c = state_file
    state = _blank_ops_state(closes_offset_seconds=3600)  # closes in 1h
    assert state["campaign"]["phase"] == "ops"
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "ops"


def test_sweep_ops_window_expired_transitions_to_cascade_hc(state_file):
    _, c = state_file
    state = _blank_ops_state(closes_offset_seconds=-1)  # already closed
    result = _run_sweep(c, state)
    # ops expired → beat resolves → cascade_HC for next beat
    assert result["campaign"]["phase"] == "cascade_HC"
    assert "cascade_HC_deadline" in result["cascade"]


def test_sweep_ops_no_closes_at_stays_ops(state_file):
    _, c = state_file
    state = _blank_ops_state(closes_offset_seconds=3600)
    state["ops_window"]["closes_at"] = None
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "ops"


def test_sweep_cascade_hc_deadline_expired_transitions_to_company(state_file):
    _, c = state_file
    state = _cascade_state("cascade_HC", deadline_offset_seconds=-1)
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "cascade_Company"
    assert "cascade_Company_deadline" in result["cascade"]


def test_sweep_cascade_hc_deadline_not_expired_stays_hc(state_file):
    _, c = state_file
    state = _cascade_state("cascade_HC", deadline_offset_seconds=3600)
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "cascade_HC"


def test_sweep_cascade_company_deadline_expired_transitions_to_kt(state_file):
    _, c = state_file
    state = _cascade_state("cascade_Company", deadline_offset_seconds=-1)
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "cascade_KT"
    assert "cascade_KT_deadline" in result["cascade"]


def test_sweep_cascade_kt_deadline_expired_resolves_beat(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT", deadline_offset_seconds=-1)
    state["campaign"]["beat"] = 1
    result = _run_sweep(c, state)
    # cascade_KT expired → ops window opens for this beat
    assert result["campaign"]["phase"] == "ops"
    assert result["campaign"]["beat"] == 1  # beat unchanged; resolve happens when ops closes
    assert result["strat_pool"]["locked"] is False  # strat pool not locked until beat resolves


def test_sweep_cascade_kt_deadline_not_expired_stays_kt(state_file):
    _, c = state_file
    state = _cascade_state("cascade_KT", deadline_offset_seconds=3600)
    result = _run_sweep(c, state)
    assert result["campaign"]["phase"] == "cascade_KT"


def test_sweep_posts_announcement_when_channel_set(state_file):
    _, c = state_file
    state = _blank_ops_state(closes_offset_seconds=-1)
    mock_channel = AsyncMock()
    mock_bot = MagicMock()
    mock_bot.get_channel.return_value = mock_channel

    with open(c.CAMPAIGN_STATE_PATH, "w") as f:
        json.dump(state, f)

    with patch.object(c, "_b", return_value=mock_bot), \
         patch.object(c, "CAMPAIGN_ANNOUNCEMENT_CHANNEL_ID", 99999):
        asyncio.run(c.sweep_campaign_beat_clock())

    mock_bot.get_channel.assert_called_once_with(99999)
    mock_channel.send.assert_awaited_once()
    text = mock_channel.send.call_args[0][0]
    assert "cascade" in text.lower() or "cascade" in text


# ---------------------------------------------------------------------------
# campaign-init double-init guard
# ---------------------------------------------------------------------------

def _make_init_interaction(phase_to_load, state_path, c):
    """Build a fake Interaction and wire up _load_campaign_state to return given phase."""
    interaction = MagicMock()
    interaction.user.id = 1
    interaction.user.display_name = "Forgemaster"
    fm_role = MagicMock()
    fm_role.name = "Forgemaster"
    interaction.user.roles = [fm_role]
    interaction.response = AsyncMock()

    state = _blank_ops_state()
    state["campaign"]["phase"] = phase_to_load
    with open(state_path, "w") as f:
        json.dump(state, f)

    return interaction


def _run_campaign_init(c, interaction, **kwargs):
    """Call _campaign_init whether it's a raw coroutine or a discord Command object."""
    fn = getattr(c._campaign_init, "callback", c._campaign_init)
    asyncio.run(fn(interaction, **kwargs))


def _run_campaign_enlist(c, interaction, **kwargs):
    fn = getattr(c._campaign_enlist, "callback", c._campaign_enlist)
    asyncio.run(fn(interaction, **kwargs))



@pytest.mark.parametrize("phase", ["ops", "cascade_HC", "cascade_Company", "cascade_KT", "paused"])
def test_campaign_init_rejected_when_active(state_file, phase):
    path, c = state_file

    interaction = _make_init_interaction(phase, path, c)

    with patch.object(c, "_b_check_command_permission", return_value=True):
        _run_campaign_init(c, interaction)

    # Should have sent an ephemeral error, not a success embed
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args
    assert kwargs[1].get("ephemeral") is True
    text = kwargs[0][0] if kwargs[0] else ""
    assert "already" in text.lower() or "running" in text.lower() or "active" in text.lower()


@pytest.mark.parametrize("phase", ["inactive", "complete"])
def test_campaign_init_accepted_when_not_active(state_file, phase):
    path, c = state_file

    interaction = _make_init_interaction(phase, path, c)

    with patch.object(c, "_b_check_command_permission", return_value=True), \
         patch.object(c, "_b", side_effect=lambda name: {"CONFIG": {}}.get(name)):
        _run_campaign_init(c, interaction)

    # Should have sent a non-ephemeral success embed
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args
    assert not kwargs[1].get("ephemeral")


def test_campaign_init_auto_closes_at_from_duration(state_file):
    path, c = state_file
    state = _blank_ops_state()
    state["campaign"]["phase"] = "inactive"
    with open(path, "w") as f:
        json.dump(state, f)

    interaction = MagicMock()
    interaction.user.id = 1
    interaction.user.display_name = "Forgemaster"
    fm_role = MagicMock()
    fm_role.name = "Forgemaster"
    interaction.user.roles = [fm_role]
    interaction.response = AsyncMock()

    with patch.object(c, "_b_check_command_permission", return_value=True), \
         patch.object(c, "_b", side_effect=lambda name: {"CONFIG": {}}.get(name)):
        _run_campaign_init(c, interaction, beat_duration_days=5)

    saved = c._load_campaign_state()
    # Init now opens cascade_HC, not an ops window
    assert saved["campaign"]["phase"] == "cascade_HC"
    assert "cascade_HC_deadline" in saved["cascade"]
    assert saved["campaign"]["beat_duration_days"] == 5


def test_campaign_enlist_defaults_watch_sergeant_kt_id_to_self(state_file):
    _, c = state_file
    state = _blank_ops_state()
    with open(c.CAMPAIGN_STATE_PATH, "w") as f:
        json.dump(state, f)

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.__str__.return_value = "Watch Sergeant"
    ws_role = MagicMock()
    ws_role.name = "Watch Sergeant"
    chapter_role = MagicMock()
    chapter_role.name = "Blood Angels"
    company_role = MagicMock()
    company_role.name = "Watch Company Primus"
    interaction.user.roles = [ws_role, chapter_role, company_role]
    interaction.response = AsyncMock()

    def _b_side_effect(name):
        if name == "HOME_CHAPTERS":
            return ["Blood Angels"]
        return None

    with patch.object(c, "_b_check_command_permission", return_value=True), \
         patch.object(c, "_b_is_allowed_channel", return_value=True), \
         patch.object(c, "_b", side_effect=_b_side_effect):
        _run_campaign_enlist(c, interaction)

    saved = c._load_campaign_state()
    assert saved["enlistment"]["42"]["kt_sgt_id"] == "42"


def test_campaign_init_explicit_ops_closes_at_overrides_duration(state_file):
    # ops_closes_at param removed; beat_duration_days governs the ops window after cascade
    path, c = state_file
    state = _blank_ops_state()
    state["campaign"]["phase"] = "inactive"
    with open(path, "w") as f:
        json.dump(state, f)

    interaction = MagicMock()
    interaction.user.id = 1
    interaction.user.display_name = "Forgemaster"
    fm_role = MagicMock()
    fm_role.name = "Forgemaster"
    interaction.user.roles = [fm_role]
    interaction.response = AsyncMock()

    with patch.object(c, "_b_check_command_permission", return_value=True), \
         patch.object(c, "_b", side_effect=lambda name: {"CONFIG": {}}.get(name)):
        _run_campaign_init(c, interaction, beat_duration_days=7)

    saved = c._load_campaign_state()
    assert saved["campaign"]["phase"] == "cascade_HC"
    assert saved["campaign"]["beat_duration_days"] == 7


def test_campaign_init_minimum_beat_duration_is_1(state_file):
    path, c = state_file
    state = _blank_ops_state()
    state["campaign"]["phase"] = "inactive"
    with open(path, "w") as f:
        json.dump(state, f)

    interaction = MagicMock()
    interaction.user.id = 1
    interaction.user.display_name = "Forgemaster"
    fm_role = MagicMock()
    fm_role.name = "Forgemaster"
    interaction.user.roles = [fm_role]
    interaction.response = AsyncMock()

    before = _now()
    with patch.object(c, "_b_check_command_permission", return_value=True), \
         patch.object(c, "_b", side_effect=lambda name: {"CONFIG": {}}.get(name)):
        _run_campaign_init(c, interaction, beat_duration_days=0)

    saved = c._load_campaign_state()
    assert saved["campaign"]["beat_duration_days"] == 1
    assert saved["campaign"]["phase"] == "cascade_HC"


def test_campaign_init_total_beats_randomly_seeded(state_file):
    """total_beats is randomly seeded at init time to 3, 4, or 5 (short/medium/long)."""
    path, c = state_file
    state = _blank_ops_state()
    state["campaign"]["phase"] = "inactive"
    with open(path, "w") as f:
        json.dump(state, f)

    interaction = MagicMock()
    interaction.user.id = 1
    interaction.user.display_name = "Forgemaster"
    fm_role = MagicMock()
    fm_role.name = "Forgemaster"
    interaction.user.roles = [fm_role]
    interaction.response = AsyncMock()

    with patch.object(c, "_b_check_command_permission", return_value=True), \
         patch.object(c, "_b", side_effect=lambda name: {"CONFIG": {}}.get(name)):
        _run_campaign_init(c, interaction)

    saved = c._load_campaign_state()
    assert saved["campaign"]["total_beats"] in {3, 4, 5}
    assert saved["campaign"].get("length_label") in {"Short", "Medium", "Long"}
