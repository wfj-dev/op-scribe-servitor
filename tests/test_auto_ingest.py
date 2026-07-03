import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import opscribe.bot as bot_module
from opscribe import auto_ingest as ai


def _make_state(**overrides):
    state = ai.AutoIngestState(**overrides)
    state.save = MagicMock()
    return state


def _setup_tick_common(state, backlog):
    assert bot_module is not None
    aar_channel = MagicMock()
    guild = MagicMock()
    guild.get_channel.return_value = aar_channel
    run_ingest = AsyncMock(return_value="ok")
    return (
        patch.object(ai, "_enabled_in_config", return_value=True),
        patch.object(ai, "_count_backlog", new=AsyncMock(return_value=backlog)),
        patch.object(ai, "_run_ingest", new=run_ingest),
        patch("opscribe.bot._resolve_notification_guild", return_value=guild),
        patch.object(ai.AutoIngestState, "load", return_value=state),
        patch.object(ai._g, "DEBUG_MODE", False, create=True),
    ), run_ingest


def test_is_forced_when_backlog_exceeds_threshold():
    with (
        patch.object(ai, "_forced_max_backlog", return_value=10),
        patch.object(ai, "_forced_max_stale_days", return_value=10),
        patch.object(ai, "_hours_since", return_value=1.0),
    ):
        assert ai._is_forced(backlog=10, last_ingest_iso="2024-01-01T00:00:00+00:00") is True


def test_is_forced_when_last_ingest_is_too_stale():
    with (
        patch.object(ai, "_forced_max_backlog", return_value=999),
        patch.object(ai, "_forced_max_stale_days", return_value=10),
        patch.object(ai, "_hours_since", return_value=24.0 * 10.0),
    ):
        assert ai._is_forced(backlog=1, last_ingest_iso="2024-01-01T00:00:00+00:00") is True


def test_in_cooldown_when_last_ingest_recent():
    state = _make_state(last_ingest_at="2024-01-01T00:00:00+00:00")
    with (
        patch.object(ai, "_hours_since", return_value=1.0),
        patch.object(ai, "_cooldown_hours", return_value=12.0),
    ):
        assert ai._in_cooldown(state) is True


def test_tick_ready_with_zero_backlog_skips_ingest():
    state = _make_state()
    patches, run_ingest = _setup_tick_common(state, backlog=0)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=False),
        patch.object(ai, "_is_forced", return_value=False),
    ):
        asyncio.run(ai._tick())
    assert state.last_check_outcome == "READY"
    run_ingest.assert_not_called()
    state.save.assert_called_once()


def test_tick_forced_runs_ingest_and_updates_state():
    state = _make_state()
    patches, run_ingest = _setup_tick_common(state, backlog=3)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=False),
        patch.object(ai, "_is_forced", return_value=True),
    ):
        asyncio.run(ai._tick())
    assert state.last_check_outcome == "FORCED"
    assert state.last_ingest_mode == "forced"
    run_ingest.assert_called_once()
    state.save.assert_called_once()


def test_tick_ready_with_backlog_runs_ingest():
    state = _make_state()
    patches, run_ingest = _setup_tick_common(state, backlog=4)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=False),
        patch.object(ai, "_is_forced", return_value=False),
    ):
        asyncio.run(ai._tick())
    assert state.last_check_outcome == "READY"
    assert state.last_ingest_mode == "ready"
    run_ingest.assert_called_once()
    state.save.assert_called_once()


def test_tick_cooldown_skips_ingest():
    state = _make_state(last_ingest_at="2024-01-01T00:00:00+00:00")
    patches, run_ingest = _setup_tick_common(state, backlog=5)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=True),
    ):
        asyncio.run(ai._tick())
    assert state.last_check_outcome == "COOLDOWN"
    run_ingest.assert_not_called()
    state.save.assert_called_once()
