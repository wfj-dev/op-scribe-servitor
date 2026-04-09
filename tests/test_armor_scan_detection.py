"""Unit tests for armor status scan detection system.

Covers:
- ARMOR_SCAN_MISS_CHANCES constant:
    * Validates expected miss chances per tier
    * Fractured has 0% miss chance

- ARMOR_SCAN_PREDICTIVE_TIERS constant:
    * Validates expected predictive chances per point range
    * No warnings in safe zone (0-40 pts)

- _roll_scan_result:
    * Returns detected=True for fractured (never missed)
    * Returns detected=False based on miss chance for damaged tiers
    * Returns predictive_warning=True based on point thresholds for nominal

- Scan state functions:
    * _load_scan_state / _save_scan_state
    * _increment_aar_generation clears cache
    * _get_or_roll_scan_result caches per AAR cycle
    * _purchase_intensive_scan / _has_intensive_scan

- INTENSIVE_SCAN_COST constant:
    * Validates expected cost (3000 pts)
"""

import asyncio
import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

import bot
from bot import (
    ARMOR_SCAN_MISS_CHANCES,
    ARMOR_SCAN_PREDICTIVE_TIERS,
    INTENSIVE_SCAN_COST,
    _roll_scan_result,
    _load_scan_state,
    _save_scan_state,
    _increment_aar_generation,
    _get_or_roll_scan_result,
    _purchase_intensive_scan,
    _has_intensive_scan,
    _get_aar_generation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# ARMOR_SCAN_MISS_CHANCES constant validation
# ---------------------------------------------------------------------------

def test_miss_chances_has_expected_tiers():
    """Miss chances should exist for damaged, compromised, critical, fractured."""
    assert "damaged" in ARMOR_SCAN_MISS_CHANCES
    assert "compromised" in ARMOR_SCAN_MISS_CHANCES
    assert "critical" in ARMOR_SCAN_MISS_CHANCES
    assert "fractured" in ARMOR_SCAN_MISS_CHANCES


def test_miss_chances_scaling():
    """Miss chances should decrease with severity (more damage = harder to miss)."""
    assert ARMOR_SCAN_MISS_CHANCES["damaged"] == 0.30  # 30% miss
    assert ARMOR_SCAN_MISS_CHANCES["compromised"] == 0.15  # 15% miss
    assert ARMOR_SCAN_MISS_CHANCES["critical"] == 0.05  # 5% miss
    assert ARMOR_SCAN_MISS_CHANCES["fractured"] == 0.0  # Never miss


def test_miss_chances_are_valid_probabilities():
    """All miss chances should be between 0 and 1."""
    for tier, chance in ARMOR_SCAN_MISS_CHANCES.items():
        assert 0.0 <= chance <= 1.0, f"{tier} has invalid miss chance: {chance}"


# ---------------------------------------------------------------------------
# ARMOR_SCAN_PREDICTIVE_TIERS constant validation
# ---------------------------------------------------------------------------

def test_predictive_tiers_has_expected_ranges():
    """Predictive tiers should cover expected point ranges."""
    tiers = ARMOR_SCAN_PREDICTIVE_TIERS
    assert len(tiers) == 5
    
    # Check ranges
    assert tiers[0]["min"] == 0 and tiers[0]["max"] == 40  # Safe zone
    assert tiers[1]["min"] == 41 and tiers[1]["max"] == 80
    assert tiers[2]["min"] == 81 and tiers[2]["max"] == 110
    assert tiers[3]["min"] == 111 and tiers[3]["max"] == 130
    assert tiers[4]["min"] == 131 and tiers[4]["max"] is None  # Unbounded


def test_predictive_tiers_safe_zone_no_warning():
    """Safe zone (0-40 pts) should have 0% warning chance."""
    safe_tier = ARMOR_SCAN_PREDICTIVE_TIERS[0]
    assert safe_tier["chance"] == 0.0


def test_predictive_tiers_increasing_chances():
    """Warning chances should increase with point count."""
    tiers = ARMOR_SCAN_PREDICTIVE_TIERS
    assert tiers[0]["chance"] == 0.0   # 0-40 pts
    assert tiers[1]["chance"] == 0.10  # 41-80 pts
    assert tiers[2]["chance"] == 0.25  # 81-110 pts
    assert tiers[3]["chance"] == 0.40  # 111-130 pts
    assert tiers[4]["chance"] == 0.60  # 131+ pts


def test_predictive_tiers_are_valid_probabilities():
    """All predictive chances should be between 0 and 1."""
    for tier in ARMOR_SCAN_PREDICTIVE_TIERS:
        assert 0.0 <= tier["chance"] <= 1.0, f"Invalid chance: {tier}"


# ---------------------------------------------------------------------------
# INTENSIVE_SCAN_COST constant validation
# ---------------------------------------------------------------------------

def test_intensive_scan_cost():
    """Intensive scan should cost 500 armory points."""
    assert INTENSIVE_SCAN_COST == 500


# ---------------------------------------------------------------------------
# _roll_scan_result
# ---------------------------------------------------------------------------

def test_roll_scan_fractured_always_detected():
    """Fractured spirits should always be detected."""
    for _ in range(20):
        result = _roll_scan_result(None, 0, spirit_fractured=True)
        assert result["detected"] is True
        assert result["predictive_warning"] is False
        assert result["miss_reason"] is None


def test_roll_scan_damaged_can_be_missed():
    """Damaged tiers can be missed based on miss chance."""
    with patch("bot.random.random") as mock_random:
        # Below miss threshold (0.30) - should be missed
        mock_random.return_value = 0.29
        result = _roll_scan_result("damaged", 50, spirit_fractured=False)
        assert result["detected"] is False
        assert result["miss_reason"] == "spirit_uncommunicative"
        
        # At/above miss threshold - should be detected
        mock_random.return_value = 0.30
        result = _roll_scan_result("damaged", 50, spirit_fractured=False)
        assert result["detected"] is True


def test_roll_scan_critical_rarely_missed():
    """Critical tier has only 5% miss chance."""
    with patch("bot.random.random") as mock_random:
        # Below 0.05 - should be missed
        mock_random.return_value = 0.04
        result = _roll_scan_result("critical", 100, spirit_fractured=False)
        assert result["detected"] is False
        
        # At 0.05 - should be detected
        mock_random.return_value = 0.05
        result = _roll_scan_result("critical", 100, spirit_fractured=False)
        assert result["detected"] is True


def test_roll_scan_nominal_predictive_warning():
    """Nominal brothers can trigger predictive warnings based on points."""
    with patch("bot.random.random") as mock_random:
        # 81-110 pts range: 25% warning chance
        # Below threshold - warning triggered
        mock_random.return_value = 0.24
        result = _roll_scan_result(None, 100, spirit_fractured=False)
        assert result["detected"] is True
        assert result["predictive_warning"] is True
        
        # At threshold - no warning
        mock_random.return_value = 0.25
        result = _roll_scan_result(None, 100, spirit_fractured=False)
        assert result["detected"] is True
        assert result["predictive_warning"] is False


def test_roll_scan_nominal_safe_zone_no_warning():
    """Nominal brothers in safe zone (0-40 pts) never get warnings."""
    for _ in range(20):
        result = _roll_scan_result(None, 30, spirit_fractured=False)
        assert result["detected"] is True
        assert result["predictive_warning"] is False


def test_roll_scan_high_points_predictive_warning():
    """High point count (131+) has 60% warning chance."""
    with patch("bot.random.random") as mock_random:
        # Below 0.60 - warning triggered
        mock_random.return_value = 0.59
        result = _roll_scan_result(None, 150, spirit_fractured=False)
        assert result["detected"] is True
        assert result["predictive_warning"] is True
        
        # At 0.60 - no warning
        mock_random.return_value = 0.60
        result = _roll_scan_result(None, 150, spirit_fractured=False)
        assert result["detected"] is True
        assert result["predictive_warning"] is False


# ---------------------------------------------------------------------------
# Scan state persistence (with temp file)
# ---------------------------------------------------------------------------

class TestScanStatePersistence:
    """Tests for scan state load/save functions."""
    
    def setup_method(self):
        """Create temp directory and patch paths."""
        self.temp_dir = tempfile.mkdtemp()
        self.scan_state_path = os.path.join(self.temp_dir, "armor_scan_state.json")
        
        # Patch the path constant
        self._original_path = bot.ARMOR_SCAN_STATE_PATH
        bot.ARMOR_SCAN_STATE_PATH = self.scan_state_path
    
    def teardown_method(self):
        """Restore original path and clean up."""
        bot.ARMOR_SCAN_STATE_PATH = self._original_path
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_scan_state_empty_file(self):
        """Loading non-existent state returns defaults."""
        state = _load_scan_state()
        assert state["aar_generation"] == 0
        assert state["intensive_scans"] == {}
        assert state["scan_cache"] == {}
    
    def test_save_and_load_scan_state(self):
        """State can be saved and loaded."""
        state = {
            "aar_generation": 5,
            "intensive_scans": {"123": 5},
            "scan_cache": {"456": {"aar_gen": 5, "detected": True}},
        }
        _save_scan_state(state)
        
        loaded = _load_scan_state()
        assert loaded["aar_generation"] == 5
        assert loaded["intensive_scans"]["123"] == 5
        assert loaded["scan_cache"]["456"]["detected"] is True
    
    def test_increment_aar_generation(self):
        """Incrementing generation clears cache and prunes old intensive scans."""
        # Setup initial state
        state = {
            "aar_generation": 5,
            "intensive_scans": {"123": 5, "456": 3},  # 456 is from old gen
            "scan_cache": {"789": {"aar_gen": 5, "detected": True}},
        }
        _save_scan_state(state)
        
        # Increment
        new_gen = _run(_increment_aar_generation())
        
        assert new_gen == 6
        
        loaded = _load_scan_state()
        assert loaded["aar_generation"] == 6
        assert loaded["scan_cache"] == {}  # Cache cleared
        # Only gen 6+ scans would remain (none)
        assert "456" not in loaded["intensive_scans"]  # Pruned
        # 123 was gen 5, which is < 6, so also pruned
        # Actually, 123 at gen 5 is < new gen 6, so it's pruned too
        assert "123" not in loaded["intensive_scans"]
    
    def test_get_aar_generation(self):
        """Can retrieve current AAR generation."""
        state = {"aar_generation": 10, "intensive_scans": {}, "scan_cache": {}}
        _save_scan_state(state)
        
        gen = _run(_get_aar_generation())
        assert gen == 10
    
    def test_purchase_intensive_scan(self):
        """Purchasing intensive scan records current generation."""
        state = {"aar_generation": 7, "intensive_scans": {}, "scan_cache": {}}
        _save_scan_state(state)
        
        result = _run(_purchase_intensive_scan(12345))
        assert result is True
        
        loaded = _load_scan_state()
        assert loaded["intensive_scans"]["12345"] == 7
    
    def test_has_intensive_scan_active(self):
        """Intensive scan is active if purchased in current generation."""
        state = {
            "aar_generation": 7,
            "intensive_scans": {"12345": 7},
            "scan_cache": {},
        }
        _save_scan_state(state)
        
        assert _run(_has_intensive_scan(12345)) is True
        assert _run(_has_intensive_scan(99999)) is False  # Not purchased
    
    def test_has_intensive_scan_expired(self):
        """Intensive scan is expired if gen < current."""
        state = {
            "aar_generation": 8,
            "intensive_scans": {"12345": 7},  # Purchased in gen 7
            "scan_cache": {},
        }
        _save_scan_state(state)
        
        # Gen 7 < current gen 8, so expired
        assert _run(_has_intensive_scan(12345)) is False
    
    def test_get_or_roll_scan_result_caches(self):
        """Scan results are cached per AAR cycle."""
        state = {"aar_generation": 5, "intensive_scans": {}, "scan_cache": {}}
        _save_scan_state(state)
        
        # First call should roll and cache
        with patch("bot.random.random", return_value=0.5):  # Above miss threshold
            result1 = _run(_get_or_roll_scan_result(123, "damaged", 50, False))
        
        assert result1["detected"] is True
        assert result1["aar_gen"] == 5
        
        # Second call should return cached result (random not called)
        with patch("bot.random.random", return_value=0.0):  # Would miss if called
            result2 = _run(_get_or_roll_scan_result(123, "damaged", 50, False))
        
        assert result2["detected"] is True  # Same as cached
        assert result2["aar_gen"] == 5
    
    def test_get_or_roll_scan_result_new_cycle_invalidates_cache(self):
        """New AAR cycle invalidates cached scan results."""
        state = {
            "aar_generation": 5,
            "intensive_scans": {},
            "scan_cache": {
                "123": {"aar_gen": 4, "detected": True, "predictive_warning": False}
            },
        }
        _save_scan_state(state)
        
        # Cached result is from gen 4, but current is gen 5
        # Should roll a new result
        with patch("bot.random.random", return_value=0.0):  # Would miss
            result = _run(_get_or_roll_scan_result(123, "damaged", 50, False))
        
        assert result["detected"] is False  # New roll (missed)
        assert result["aar_gen"] == 5


# ---------------------------------------------------------------------------
# Integration scenarios
# ---------------------------------------------------------------------------

class TestScanDetectionIntegration:
    """Integration tests for scan detection scenarios."""
    
    def setup_method(self):
        """Create temp directory and patch paths."""
        self.temp_dir = tempfile.mkdtemp()
        self.scan_state_path = os.path.join(self.temp_dir, "armor_scan_state.json")
        
        self._original_path = bot.ARMOR_SCAN_STATE_PATH
        bot.ARMOR_SCAN_STATE_PATH = self.scan_state_path
    
    def teardown_method(self):
        """Restore original path and clean up."""
        bot.ARMOR_SCAN_STATE_PATH = self._original_path
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_intensive_scan_expires_on_new_aar(self):
        """Intensive scan expires when new AAR is ingested (gen increments)."""
        # Purchase intensive scan at gen 3
        state = {"aar_generation": 3, "intensive_scans": {}, "scan_cache": {}}
        _save_scan_state(state)
        _run(_purchase_intensive_scan(999))
        
        assert _run(_has_intensive_scan(999)) is True
        
        # Ingest new AAR (increment generation)
        _run(_increment_aar_generation())
        
        assert _run(_has_intensive_scan(999)) is False  # Expired
    
    def test_scan_results_same_within_cycle(self):
        """Same brother returns same scan result within one AAR cycle."""
        state = {"aar_generation": 1, "intensive_scans": {}, "scan_cache": {}}
        _save_scan_state(state)
        
        results = []
        for _ in range(5):
            # Each call should return the cached result after the first
            with patch("bot.random.random", return_value=0.1):  # Would miss damaged
                result = _run(_get_or_roll_scan_result(555, "damaged", 80, False))
                results.append(result["detected"])
        
        # All results should be identical (first one cached)
        assert len(set(results)) == 1


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
