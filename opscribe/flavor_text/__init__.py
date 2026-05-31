"""Flavor text package for op-scribe-servitor.

Sub-modules:
  ranks    — rank honorifics, prestige weights, Techmarine acknowledgments, *_RANK_LINES
  chapters — chapter-keyed blessings, studs flavor, self-attestation, *_CHAPTER_LINES
  forge    — armor/forge constants, probability tables, Mechanicus phrases
  warp     — warp/librarium subsystem data and helper functions
  studs    — service stud announcement pools
  awards   — award announcement openings and proclamations

All public names are re-exported here so existing ``from .flavor_text import *``
calls across the codebase continue to work without modification.
"""

from typing import Dict, List  # noqa: F401
from .ranks import *  # noqa: F401, F403
from .chapters import *  # noqa: F401, F403
from .forge import *  # noqa: F401, F403
from .warp import *  # noqa: F401, F403
from .studs import *  # noqa: F401, F403
from .awards import *  # noqa: F401, F403

# Re-export private helpers that librarius_ops imports by name
from .warp import _warp_sanction_key_for_state, _warp_sanction_key_for_points  # noqa: F401

MAX_RITE_LENGTH = 250
