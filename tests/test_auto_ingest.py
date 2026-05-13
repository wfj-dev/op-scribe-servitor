import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import opscribe.bot as bot_module
from opscribe import auto_ingest as ai
from opscribe.pressure_registry import CadrePressure, PressureSnapshot


def _make_state(**overrides):
    state = ai.AutoIngestState(**overrides)
    state.save = MagicMock()
    return state


def _blocked_snapshot() -> PressureSnapshot:
    return PressureSnapshot(
        cadres=[
            CadrePressure(
                cadre_id="techmarine",
                display_name="Techmarines",
                demand=1,
                supply=1,  # score == 1.0 => blocker
            )
        ]
    )


def _setup_tick_common(state, snapshot, backlog):
    assert bot_module is not None
    aar_channel = MagicMock()
    guild = MagicMock()
    guild.get_channel.return_value = aar_channel
    run_ingest = AsyncMock(return_value="ok")
    return (
        patch.object(ai, "_enabled_in_config", return_value=True),
        patch.object(ai, "evaluate_all", new=AsyncMock(return_value=snapshot)),
        patch.object(ai, "_count_backlog", new=AsyncMock(return_value=backlog)),
        patch.object(ai, "_run_ingest", new=run_ingest),
        patch("opscribe.bot._resolve_notification_guild", return_value=guild),
        patch.object(ai.AutoIngestState, "load", return_value=state),
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
    snapshot = PressureSnapshot(cadres=[])
    patches, run_ingest = _setup_tick_common(state, snapshot, backlog=0)
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


def test_tick_blocked_posts_tier1_notice_on_first_block():
    state = _make_state()
    snapshot = _blocked_snapshot()
    patches, _ = _setup_tick_common(state, snapshot, backlog=3)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=False),
        patch.object(ai, "_is_forced", return_value=False),
        patch.object(ai, "_post_tier1_blocker_notice", new=AsyncMock()) as post_tier1,
        patch.object(ai, "_dm_forgemaster_tier2", new=AsyncMock()),
        patch.object(ai, "_hours_since", return_value=0.0),
    ):
        asyncio.run(ai._tick())
    assert state.last_check_outcome == "BLOCKED"
    assert state.blocked_since is not None
    post_tier1.assert_called_once()


def test_tick_blocked_does_not_repost_tier1_for_same_blockers():
    state = _make_state(
        blocked_since="2024-01-01T00:00:00+00:00",
        last_blocker_notice_at="2024-01-01T00:00:00+00:00",
        last_blocker_set=["techmarine"],
    )
    snapshot = _blocked_snapshot()
    patches, _ = _setup_tick_common(state, snapshot, backlog=3)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=False),
        patch.object(ai, "_is_forced", return_value=False),
        patch.object(ai, "_post_tier1_blocker_notice", new=AsyncMock()) as post_tier1,
        patch.object(ai, "_dm_forgemaster_tier2", new=AsyncMock()),
        patch.object(ai, "_hours_since", return_value=0.0),
    ):
        asyncio.run(ai._tick())
    post_tier1.assert_not_called()


def test_tick_blocked_escalates_dm_after_escalation_window():
    state = _make_state(blocked_since="2024-01-01T00:00:00+00:00")
    snapshot = _blocked_snapshot()
    patches, _ = _setup_tick_common(state, snapshot, backlog=3)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch.object(ai, "_in_cooldown", return_value=False),
        patch.object(ai, "_is_forced", return_value=False),
        patch.object(ai, "_post_tier1_blocker_notice", new=AsyncMock()),
        patch.object(ai, "_dm_forgemaster_tier2", new=AsyncMock()) as dm_tier2,
        patch.object(ai, "_hours_since", return_value=49.0),
        patch.object(ai, "_escalation_hours", return_value=48.0),
    ):
        asyncio.run(ai._tick())
    dm_tier2.assert_called_once_with(snapshot, state, 3)
