"""Unit tests for milestone interval gating and threshold-crossing logic.

Covers:
- _check_milestone_thresholds: single-increment, multi-increment, no-cross,
  zero-baseline, and subset metric scenarios
- _scheduled_milestone_check interval gating: skips when fewer than N days
  have passed, runs when enough days have passed, uses persisted
  last_check_date as source of truth (restart-safe), and falls back to
  in-memory LAST_MILESTONE_CHECK_DATE when no persisted value exists
"""

import json
import os
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opscribe.bot import _check_milestone_thresholds, MILESTONES_INCREMENTS


# ---------------------------------------------------------------------------
# _check_milestone_thresholds tests
# ---------------------------------------------------------------------------


class TestCheckMilestoneThresholds:
    """Tests for the pure function that detects which milestones were crossed."""

    def test_no_milestones_crossed(self):
        """If current values are below the next threshold, nothing is returned."""
        last_announced = {k: 0 for k in MILESTONES_INCREMENTS}
        current = {k: MILESTONES_INCREMENTS[k] - 1 for k in MILESTONES_INCREMENTS}
        result = _check_milestone_thresholds(current, last_announced)
        assert result == []

    def test_single_milestone_crossed(self):
        """Crossing exactly one increment for one metric returns that metric."""
        last_announced = {"aar_points": 0}
        current = {"aar_points": 2500}
        result = _check_milestone_thresholds(current, last_announced)
        assert len(result) == 1
        metric, milestone_val, current_val = result[0]
        assert metric == "aar_points"
        assert milestone_val == 2500
        assert current_val == 2500

    def test_multiple_increments_crossed(self):
        """Jumping past several increments in one check yields one entry per
        crossed threshold."""
        last_announced = {"aar_count": 0}
        # aar_count increment is 500; value 1200 should cross 500 and 1000
        current = {"aar_count": 1200}
        result = _check_milestone_thresholds(current, last_announced)
        milestones = [(m, v) for m, v, _ in result]
        assert ("aar_count", 500) in milestones
        assert ("aar_count", 1000) in milestones
        assert len(result) == 2

    def test_already_announced_not_recrossed(self):
        """If last_announced is at or above the current value no crossing occurs."""
        last_announced = {"geneseed_recoveries": 1000}
        current = {"geneseed_recoveries": 1000}
        result = _check_milestone_thresholds(current, last_announced)
        assert result == []

    def test_zero_current_no_crossing(self):
        """Zero current values never produce crossings."""
        last_announced = {k: 0 for k in MILESTONES_INCREMENTS}
        current = {k: 0 for k in MILESTONES_INCREMENTS}
        assert _check_milestone_thresholds(current, last_announced) == []

    def test_missing_metric_in_current_treated_as_zero(self):
        """If a metric exists in MILESTONES_INCREMENTS but not in *current*
        the function treats the current value as 0 and skips it."""
        last_announced = {"aar_points": 0}
        current = {}  # no metrics at all
        result = _check_milestone_thresholds(current, last_announced)
        assert result == []

    def test_multiple_metrics_crossed_together(self):
        """Crossings across different metrics are all reported."""
        last_announced = {"aar_points": 0, "aar_count": 0, "armory_data": 0}
        current = {"aar_points": 2500, "aar_count": 600, "armory_data": 999}
        result = _check_milestone_thresholds(current, last_announced)
        metrics = [m for m, _, _ in result]
        assert "aar_points" in metrics
        assert "aar_count" in metrics
        # armory_data increment is 1000; 999 doesn't cross
        assert "armory_data" not in metrics


# ---------------------------------------------------------------------------
# _scheduled_milestone_check interval-gating tests
# ---------------------------------------------------------------------------


class TestScheduledMilestoneCheckGating:
    """Tests for the interval gating inside _scheduled_milestone_check.

    These tests patch I/O and bot internals so only the gating logic is
    exercised — no Discord messages are actually sent.
    """

    @pytest.fixture(autouse=True)
    def _setup_patches(self, tmp_path):
        """Common patches for every test in this class."""
        self.tracking_path = str(tmp_path / "milestone_tracking.json")

        # Patch module-level constants / globals used by the function
        self.patches = []

        p1 = patch("opscribe.bot.MILESTONES_ENABLED", True)
        p2 = patch("opscribe.bot.DATASTORE", MagicMock())
        p3 = patch("opscribe.bot.MILESTONE_TRACKING_PATH", self.tracking_path)
        p4 = patch("opscribe.bot.MILESTONES_CHECK_INTERVAL_DAYS", 7)

        self.patches.extend([p1, p2, p3, p4])
        for p in self.patches:
            p.start()

        yield

        for p in self.patches:
            p.stop()

    def _write_tracking(self, data: dict):
        os.makedirs(os.path.dirname(self.tracking_path) or ".", exist_ok=True)
        with open(self.tracking_path, "w") as f:
            json.dump(data, f)

    def _read_tracking(self) -> dict:
        with open(self.tracking_path, "r") as f:
            return json.load(f)

    # -- Persisted last_check_date is source of truth --

    @pytest.mark.asyncio
    async def test_skips_when_persisted_date_is_recent(self):
        """If the persisted last_check_date is recent (< 7 days), skip."""
        import opscribe.bot as bot

        recent = str((date.today() - timedelta(days=3)))
        self._write_tracking(
            {
                "last_announced": {},
                "last_check_date": recent,
            }
        )

        # In-memory value is None (simulates fresh restart)
        with patch.object(bot, "LAST_MILESTONE_CHECK_DATE", None):
            from opscribe.bot import _scheduled_milestone_check

            await _scheduled_milestone_check()

        # Should not have advanced the check date — function returned early
        data = self._read_tracking()
        assert data["last_check_date"] == recent

    @pytest.mark.asyncio
    async def test_runs_when_persisted_date_is_old_enough(self):
        """If persisted last_check_date is ≥ 7 days old the check proceeds."""
        import opscribe.bot as bot

        old_date = str(date.today() - timedelta(days=8))
        self._write_tracking(
            {
                "last_announced": {},
                "last_check_date": old_date,
            }
        )

        mock_guild = MagicMock()
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_guild.get_channel = MagicMock(return_value=mock_channel)
        mock_guild.roles = []

        with (
            patch.object(bot, "LAST_MILESTONE_CHECK_DATE", None),
            patch("opscribe.bot._resolve_notification_guild", return_value=mock_guild),
            patch("opscribe.bot._calculate_current_milestones", return_value={k: 0 for k in MILESTONES_INCREMENTS}),
        ):
            from opscribe.bot import _scheduled_milestone_check

            await _scheduled_milestone_check()

        # Check ran — last_check_date should be updated to today
        data = self._read_tracking()
        assert data["last_check_date"] == str(date.today())

    @pytest.mark.asyncio
    async def test_fallback_to_in_memory_when_no_persisted_date(self):
        """If persisted last_check_date is None, use in-memory value."""
        import opscribe.bot as bot

        recent = str(date.today() - timedelta(days=2))
        # No persisted date
        self._write_tracking(
            {
                "last_announced": {},
                "last_check_date": None,
            }
        )

        with patch.object(bot, "LAST_MILESTONE_CHECK_DATE", recent):
            from opscribe.bot import _scheduled_milestone_check

            await _scheduled_milestone_check()

        # Should have skipped — in-memory date is recent
        data = self._read_tracking()
        assert data["last_check_date"] is None  # unchanged

    @pytest.mark.asyncio
    async def test_persists_date_even_when_no_milestones_crossed(self):
        """On a no-op week (no milestones crossed), last_check_date is still
        persisted so the gate works correctly next time."""
        import opscribe.bot as bot

        old_date = str(date.today() - timedelta(days=10))
        self._write_tracking(
            {
                "last_announced": {},
                "last_check_date": old_date,
            }
        )

        mock_guild = MagicMock()
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_guild.get_channel = MagicMock(return_value=mock_channel)
        mock_guild.roles = []

        with (
            patch.object(bot, "LAST_MILESTONE_CHECK_DATE", None),
            patch("opscribe.bot._resolve_notification_guild", return_value=mock_guild),
            patch("opscribe.bot._calculate_current_milestones", return_value={k: 0 for k in MILESTONES_INCREMENTS}),
        ):
            from opscribe.bot import _scheduled_milestone_check

            await _scheduled_milestone_check()

        data = self._read_tracking()
        assert data["last_check_date"] == str(date.today())
