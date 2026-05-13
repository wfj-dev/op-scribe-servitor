import pytest

from opscribe.pressure_registry import CadrePressure, PressureSnapshot


def _cadre(cadre_id: str, demand: int, supply: int) -> CadrePressure:
    return CadrePressure(
        cadre_id=cadre_id,
        display_name=cadre_id.title(),
        demand=demand,
        supply=supply,
    )


def test_pressure_snapshot_empty_registry_defaults_ready():
    snap = PressureSnapshot(cadres=[])
    assert snap.mean_score == 0.0
    assert snap.max_score == 0.0
    assert snap.is_ready is True


def test_pressure_snapshot_finite_scores_are_aggregated():
    snap = PressureSnapshot(
        cadres=[
            _cadre("techmarine", demand=1, supply=2),   # 0.5
            _cadre("librarian", demand=1, supply=1),    # 1.0
        ]
    )
    assert snap.mean_score == pytest.approx(0.75)
    assert snap.max_score == pytest.approx(1.0)
    assert snap.is_ready is True


def test_pressure_snapshot_supply_zero_yields_inf_and_not_ready():
    snap = PressureSnapshot(cadres=[_cadre("librarian", demand=3, supply=0)])
    assert snap.cadres[0].score == float("inf")
    assert snap.mean_score == float("inf")
    assert snap.max_score == float("inf")
    assert snap.is_ready is False


def test_pressure_snapshot_mean_below_one_but_hard_cap_blocks():
    snap = PressureSnapshot(
        cadres=[
            _cadre("a", demand=0, supply=1),  # 0.0
            _cadre("b", demand=0, supply=1),  # 0.0
            _cadre("c", demand=2, supply=1),  # 2.0
        ]
    )
    assert snap.mean_score == pytest.approx(2.0 / 3.0)
    assert snap.max_score == pytest.approx(2.0)
    assert snap.is_ready is False
