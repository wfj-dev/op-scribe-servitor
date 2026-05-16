"""Specialist cadre pressure registry for auto-AAR-ingest.

Each specialist cadre (Techmarines, Librarians, and future cadres — three
more planned) exposes an async evaluator that returns a ``CadrePressure``
snapshot describing current supply (available charges) and demand
(brothers needing attention) for that cadre.

The decision engine in ``auto_ingest.py`` aggregates the registered
cadres with **mean + hard-cap** semantics:

* normal ingest fires when ``mean(score) < READY_THRESHOLD`` **and**
  no cadre exceeds ``HARD_BLOCK_THRESHOLD``
* a single overloaded cadre (score >= HARD_BLOCK_THRESHOLD) vetoes ingest
* a forced override (handled in auto_ingest) fires when backlog or
  staleness pass their own thresholds regardless of cadre scores

Adding a new cadre is just: implement an evaluator, decorate with
``@register_cadre`` (or call ``register_cadre(...)`` at module import),
and the loop picks it up automatically.

Score convention per cadre::

    score = demand / max(1, supply)

* 0.0 .. <1.0  green, idle/manageable
* 1.0          balanced
* >1.0         blocker
* inf          supply==0 with demand>0 (always a hard blocker)
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

import discord


# Thresholds (kept here for easy tuning; mirrors design discussion).
READY_THRESHOLD: float = 1.0
HARD_BLOCK_THRESHOLD: float = 2.0


@dataclass
class CadrePressure:
    """Snapshot of one cadre's supply/demand at a point in time.

    ``demand`` is charge-weighted: the total number of intensive charges
    needed to restore all brothers requiring attention to a green state,
    including a fractional at-risk contribution for brothers not yet
    damaged but at elevated risk.  ``supply`` is the pool of available
    charges the cadre can deploy right now.
    """

    cadre_id: str            # stable identifier, e.g. "techmarine"
    display_name: str        # human-facing, e.g. "Techmarines"
    demand: float            # charges of work outstanding (charge-weighted; may be fractional)
    supply: int              # available charges (or analogous capacity)
    notify_role_id: Optional[int] = None  # role to ping when blocking
    notify_channel_id: Optional[int] = None  # cadre-specific tier-1 channel
    detail: str = ""         # optional human-readable status detail

    @property
    def score(self) -> float:
        if self.supply <= 0:
            return float("inf") if self.demand > 0 else 0.0
        return self.demand / self.supply

    @property
    def is_blocker(self) -> bool:
        """True if this cadre's score is at or above the ready threshold."""
        return self.score >= READY_THRESHOLD

    @property
    def is_hard_blocker(self) -> bool:
        """True if this cadre alone vetoes ingestion (score >= hard cap)."""
        s = self.score
        return s == float("inf") or s >= HARD_BLOCK_THRESHOLD


Evaluator = Callable[[discord.Guild], Awaitable[CadrePressure]]


_REGISTRY: List[Evaluator] = []


def register_cadre(evaluator: Evaluator) -> Evaluator:
    """Register a cadre's pressure evaluator. Usable as a decorator.

    Idempotent: registering the same callable twice is a no-op.
    """
    if evaluator not in _REGISTRY:
        _REGISTRY.append(evaluator)
    return evaluator


def registered_cadres() -> List[Evaluator]:
    """Return a copy of the registered evaluators (read-only)."""
    return list(_REGISTRY)


def clear_registry() -> None:
    """Reset the registry. Intended for tests."""
    _REGISTRY.clear()


@dataclass
class PressureSnapshot:
    """Aggregate of every cadre's pressure for a single check tick."""

    cadres: List[CadrePressure]

    @property
    def mean_score(self) -> float:
        if not self.cadres:
            return 0.0
        scores = [c.score for c in self.cadres]
        if any(s == float("inf") for s in scores):
            return float("inf")
        return sum(scores) / len(scores)

    @property
    def max_score(self) -> float:
        if not self.cadres:
            return 0.0
        return max(c.score for c in self.cadres)

    @property
    def is_ready(self) -> bool:
        """All conditions for normal ingest are met."""
        return (
            self.mean_score < READY_THRESHOLD
            and self.max_score < HARD_BLOCK_THRESHOLD
        )

    def blockers(self) -> List[CadrePressure]:
        """Cadres at or above the ready threshold."""
        return [c for c in self.cadres if c.is_blocker]

    def hard_blockers(self) -> List[CadrePressure]:
        """Cadres at or above the hard-block cap (single-cadre veto)."""
        return [c for c in self.cadres if c.is_hard_blocker]


async def evaluate_all(guild: discord.Guild) -> PressureSnapshot:
    """Run every registered evaluator and return the aggregate snapshot.

    A failing evaluator is logged and skipped; it does not crash the loop.
    """
    cadres: List[CadrePressure] = []
    for evaluator in _REGISTRY:
        try:
            cadres.append(await evaluator(guild))
        except Exception:
            try:
                from . import _bot_globals as _g  # local import to avoid cycles
                _g.logger.exception(
                    "Cadre evaluator %s failed", getattr(evaluator, "__name__", "?")
                )
            except Exception:
                pass
    return PressureSnapshot(cadres=cadres)
