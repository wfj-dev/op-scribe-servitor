"""Pure stud calculation helpers extracted from bot.py.

Most functions here are deterministic and depend only on stdlib + flavor_text
data with no coupling to the Discord client or runtime state.  The exception
is ``_get_studs_veneration``, which uses ``random.choice`` to pick a
flavour phrase and is therefore non-deterministic. bot.py re-exports all
symbols via ``from studs import *`` so existing references and tests keep
working unchanged.
"""

import random
from typing import Optional

from .flavor_text import (
    SERVICE_STUDS_VENERATIONS_AURAMITE,
    SERVICE_STUDS_VENERATIONS_PLASTEEL,
)

__all__ = [
    "_get_stud_weight",
    "_studs_tier",
    "_studs_pips",
    "_studs_next_target",
    "_format_stud_target",
    "_get_studs_veneration",
]


def _get_stud_weight(studs: int) -> float:
    """Calculate stud weight for acknowledgment blending (0.0-1.0).

    Scales linearly from 0.1 (1 stud) to 1.0 (16 studs).
    0 studs returns 0.05 (minimal weight).
    """
    if studs <= 0:
        return 0.05
    if studs >= 16:
        return 1.0
    # Linear scale: 1 stud = 0.1, 16 studs = 1.0
    return 0.1 + (studs - 1) * (0.9 / 15)


def _studs_tier(new_total: int) -> int:
    """Return the display tier (1, 2, or 3) for a given total stud count.

    Tier 1: 1-3 studs (new warriors)
    Tier 2: 4-11 studs (seasoned veterans)
    Tier 3: 12-16 studs (legendary; studs are capped at 16 system-wide)
    """
    if new_total <= 3:
        return 1
    elif new_total <= 11:
        return 2
    return 3


def _studs_pips(new_total: int) -> str:
    """Return the pip display string for a given total stud count.

    Each Auramite pip (●) represents 4 Plasteel studs.
    Once the first Auramite is earned (total ≥ 4), only Auramite pips
    are displayed; the Plasteel remainder is not shown.
    The display is capped at 4 Auramite studs (16 Plasteel total).
    Returns '—' when new_total is 0.
    """
    auramite = min(new_total // 4, 4)
    if auramite > 0:
        pips = "●" * auramite
    else:
        plasteel = new_total % 4
        pips = "⚬" * plasteel
    return pips if pips else "—"


def _studs_next_target(displayed_studs: int) -> int:
    """Return the next stud milestone for the promotion queue.

    Plasteel tier (0-3 studs): next individual stud (displayed_studs + 1).
    Auramite tier (4+ studs): next Auramite milestone in steps of 4 (8, 12, 16).
    """
    if displayed_studs < 4:
        return displayed_studs + 1
    return (displayed_studs // 4 + 1) * 4


def _format_stud_target(target: int) -> str:
    """Return a display string for the next stud target in the promotion queue.

    For milestones that reach the first auramite or beyond (target >= 4), shows
    auramite pip symbols (●). For earlier studs, shows the stud number (#n).
    """
    if target >= 4:
        return "●" * (target // 4)
    return f"#{target}"


def _get_studs_veneration(studs_count: int) -> Optional[str]:
    """Get a random veneration phrase appropriate for the service studs count.

    Maps count ranges to pip types:
    - 0: No veneration (newly promoted)
    - 1-3: Plasteel (newly earned)
    - 4+: Auramite (seasoned warrior, max 4 auramite = 16 plasteel)
    """
    if studs_count <= 0:
        return None
    elif studs_count <= 3:
        return random.choice(SERVICE_STUDS_VENERATIONS_PLASTEEL)
    else:
        return random.choice(SERVICE_STUDS_VENERATIONS_AURAMITE)
