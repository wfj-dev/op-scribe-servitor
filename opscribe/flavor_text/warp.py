"""Warp / Librarium subsystem data."""

from typing import Dict, List  # noqa: F401

WARP_INFECTION_TIERS = ["tainted", "exposed", "volatile"]

# Legacy alias retained for read-side compatibility; do NOT use for new code.

WARP_EXPOSURE_TIERS = WARP_INFECTION_TIERS

# Susceptibility bands — these map a brother's current susceptibility points to
# the infection-roll probability tier (mirror of armor's probability_tiers config).
# Bands no longer directly produce a tier label; they only determine the chance/
# weights of the next infection roll. Kept here for legacy display fallback.

WARP_BROTHER_TIER_BANDS = {
    "tainted": (1, 4),
    "exposed": (5, 9),
    "volatile": (10, None),
}

# Librarian personal exposure tiers (2x brother bands; reflects psychic tolerance)

WARP_LIBRARIAN_TIERS = ["stable", "resonant", "surging", "overloaded", "abyssal"]

WARP_LIBRARIAN_TIER_BANDS = {
    "stable": (1, 8),
    "resonant": (9, 18),
    "surging": (19, 28),
    "overloaded": (29, 38),
    "abyssal": (39, None),
}

# Brother-facing Warp Sanction status (label, description). Brothers see this only.
# Mirrors Techmarine armor outcome layer: 1 clean state (sanctioned ≈ nominal) +
# 3 roled states (screening_due/under_review/restricted ≈ damaged/compromised/critical).
# A separate boolean flag (warp_corrupted ≈ spirit_fractured) is tracked on top.
#
# TERMINOLOGY NOTE (partial display-only migration):
#   User-facing labels have been updated in three places while the internal keys,
#   helpers, and JSON fields below intentionally retain the legacy "sanction"
#   vocabulary (full refactor deferred to a later migration):
#       • Librarian clearing a brother of warp taint — displayed as "Cleansed"
#         (internal dict key/state remains "sanctioned").
#       • Techmarine clearing armor — conceptually displayed as "Attested"
#         (internal already uses _find_responsible_attestor / attestor).
#       • AAR accepted into the archive — displayed as "Chronicled"
#         (internal helpers / state still talk about sanctioned AARs).
#   Treat dict keys like "sanctioned", `_warp_sanction_key_for_points`,
#   `_apply_sanction_role`, JSON field `is_sanctioned`, etc., as the legacy
#   term for what is now displayed as "Cleansed" / "Attested" / "Chronicled".

WARP_SANCTION_STATUS = {
    "sanctioned": ("Cleansed", "No corruption detected. Spirit clear."),
    "screening_due": ("Screening Due", "Trace contamination detected. Report for psychic screening."),
    "under_review": ("Under Review", "Significant exposure noted. Librarium oversight engaged."),
    "restricted": ("Restricted", "Severe exposure. Operational restrictions in effect pending Void Warden review."),
}

# Map a brother's INFECTION STATE to a Warp Sanction key.
# Sanction keys now derive from the discrete infection state, not from
# susceptibility points — exactly parallel to armor's sanction roles, which
# track damage_tier (damaged/compromised/critical) rather than wear points.

_WARP_INFECTION_TO_SANCTION = {
    None: "sanctioned",
    "tainted": "screening_due",
    "exposed": "under_review",
    "volatile": "restricted",
}

def _warp_sanction_key_for_state(infection_state, warp_corrupted: bool = False) -> str:
    """Map a brother's infection_state (+ warp_corrupted flag) to a sanction key.

    warp_corrupted brothers always surface as "restricted" regardless of the
    current infection_state (mirrors spirit_fractured forcing a permanent
    high-severity Discord role).
    """
    if warp_corrupted:
        return "restricted"
    return _WARP_INFECTION_TO_SANCTION.get(infection_state, "sanctioned")


# Legacy helper retained for read-side compatibility. Internally it just maps
# the legacy point bands to the equivalent sanction key. New code should call
# _warp_sanction_key_for_state instead.

def _warp_sanction_key_for_points(points: int) -> str:
    """DEPRECATED: legacy point-band → sanction mapping."""
    if points <= 0:
        return "sanctioned"
    if points <= 4:
        return "screening_due"
    if points <= 9:
        return "under_review"
    return "restricted"


# Warp corruption threshold (AAR submissions at restricted before brother is corrupted).
# Mirrors DEFAULT_ARMOR_FRACTURE_THRESHOLD.

DEFAULT_WARP_CORRUPTION_THRESHOLD = 3


# Penalty tables — exact mirror of ARMOR_PENALTY_PROBABILITIES, keyed by
# the brother's infection_state. warp_corrupted (the spirit_fractured
# parallel) is handled by the resolver function below.

WARP_PENALTY_PROBABILITIES = {
    None: {0: 1.0},
    "tainted": {0: 0.90, 1: 0.085, 2: 0.010, 3: 0.005},
    "exposed": {0: 0.835, 1: 0.10, 2: 0.05, 3: 0.015},
    "volatile": {0: 0.75, 1: 0.085, 2: 0.10, 3: 0.065},
}

# Penalty table for warp_corrupted (parallel to fractured) — strictly worse
# than volatile alone.

WARP_PENALTY_PROBABILITIES_CORRUPTED = {0: 0.70, 1: 0.05, 2: 0.085, 3: 0.10, 4: 0.065}

# Detection alert chances per AAR while infected (mirrors ARMOR_DETECTION_CHANCES).

WARP_DETECTION_CHANCES = {
    "tainted": 0.20,
    "exposed": 0.35,
    "volatile": 0.50,
}

# Spread chance from an infected source by the source's infection_state.
# Only infected brothers can spread — clean brothers cannot.

WARP_SPREAD_CHANCES = {
    "tainted": 0.20,
    "exposed": 0.35,
    "volatile": 0.50,
}

# Infection probability tiers — exact mirror of forge_ops probability_tiers.
# Keyed by susceptibility point ranges; each entry gives the chance an
# infection roll succeeds AND the weights for picking which tier (when it does).

WARP_INFECTION_PROBABILITY_TIERS = [
    {"min": 0,  "max": 4,    "chance": 0.00, "infection_weights": {"tainted": 100, "exposed": 0,  "volatile": 0}},
    {"min": 5,  "max": 9,    "chance": 0.02, "infection_weights": {"tainted": 90,  "exposed": 8,  "volatile": 2}},
    {"min": 10, "max": 14,   "chance": 0.08, "infection_weights": {"tainted": 80,  "exposed": 15, "volatile": 5}},
    {"min": 15, "max": 19,   "chance": 0.20, "infection_weights": {"tainted": 65,  "exposed": 25, "volatile": 10}},
    {"min": 20, "max": None, "chance": 0.40, "infection_weights": {"tainted": 50,  "exposed": 35, "volatile": 15}},
]

# Cleanse outcome probabilities — exact mirror of BLESSING_ROLL_PROBABILITIES.
# Keyed by recipient's current infection_state ("corrupted" used when warp_corrupted=True).

WARP_CLEANSE_OUTCOME_PROBABILITIES = {
    None:        {"crit_fail": 0.01, "crit_success": 0.01},
    "tainted":   {"crit_fail": 0.03, "crit_success": 0.03},
    "exposed":   {"crit_fail": 0.05, "crit_success": 0.05},
    "volatile":  {"crit_fail": 0.08, "crit_success": 0.06},
    "corrupted": {"crit_fail": 0.10, "crit_success": 0.10},
}

# Cleanse outcome matrix keyed by the cleansing Librarian's current tier.
# Each entry is a list of (probability, outcome_key, fraction_removed, librarian_extra).
# - outcome_key: "full", "partial", "backlash"
# - fraction_removed: 0.0 - 1.0 of recipient's current exposure (full = 1.0)
# - librarian_extra: extra exposure added to the Librarian on top of the standard transfer

WARP_CLEANSE_OUTCOMES = {
    None: [  # Clear Librarian — most reliable
        (0.90, "full", 1.00, 0),
        (0.10, "partial", 0.75, 0),
    ],
    "stable": [
        (0.80, "full", 1.00, 0),
        (0.15, "partial", 0.75, 0),
        (0.05, "backlash", 0.50, 1),
    ],
    "resonant": [
        (0.65, "full", 1.00, 0),
        (0.25, "partial", 0.60, 0),
        (0.10, "backlash", 0.40, 2),
    ],
    "surging": [
        (0.45, "full", 1.00, 0),
        (0.35, "partial", 0.50, 0),
        (0.20, "backlash", 0.30, 3),
    ],
    # overloaded/abyssal: cannot cleanse others (handled in command guard)
}

# Sanitized public flavor for cleanse outcomes

WARP_CLEANSE_OUTCOME_FLAVOR = {
    "full": [
        "The Librarian seals the rift cleanly. Corruption recedes; the spirit clears.",
        "Wards complete. The taint is purged in full.",
        "Litanies hold. The brother stands cleansed.",
    ],
    "partial": [
        "The cleanse holds, but residue clings to the spirit.",
        "Most of the taint is purged. A faint shadow remains.",
        "The wards cut deep but do not finish the work.",
    ],
    "backlash": [
        "The cleanse falters. Corruption lashes back into the Librarian.",
        "Wards crack. The Librarian absorbs the backlash to spare the brother.",
        "The rite holds—barely. Burden flows to the cleanser.",
    ],
    # New three-outcome keys (mirror armor blessing outcomes)
    "crit_success": [
        "The wards sing in perfect harmony — the brother emerges fortified beyond cleansed.",
        "A confluence of light: every shadow is burned away and a grace lingers in the warp.",
        "The rite ascends. The brother walks free, and the warp recoils from him for a while.",
    ],
    "normal": [
        "Wards hold. The taint is purged in full.",
        "Litanies complete. The brother stands cleansed.",
        "The Librarian seals the breach; the spirit clears.",
    ],
    "crit_fail": [
        "The rite shatters. Corruption surges back twofold into the cleanser.",
        "Wards splinter. The Librarian reels under doubled backlash; the taint deepens.",
        "The cleanse inverts. The brother's affliction worsens and the cleanser bleeds the cost.",
    ],
}

# Brief Librarian tier descriptions for warp_status displays

WARP_LIBRARIAN_TIER_DESCRIPTIONS = {
    None: ("CLEAR", "Mind shielded; full reliability."),
    "stable": ("STABLE", "Minor strain. Cleansing reliable."),
    "resonant": ("RESONANT", "Marked resonance. Cleansing less predictable."),
    "surging": ("SURGING", "Severe instability. Backlash likely."),
    "overloaded": ("OVERLOADED", "Cannot cleanse others. Self-cleanse only."),
    "abyssal": ("ABYSSAL", "Void Warden intervention required."),
}

WARP_BROTHER_TIER_DESCRIPTIONS = {
    None: ("CLEAR", "No infection detected."),
    "tainted": ("TAINTED", "Minor warp residue."),
    "exposed": ("EXPOSED", "Notable contamination."),
    "volatile": ("VOLATILE", "Severe contamination; psychic instability."),
}

# ---------------------------------------------------------------------------
# Compact icon ladders (parity with armor: 🟡 → 🟠 → 🔴 → 💀 → ⚫)
# Used by /warp_status and Librarium Chronicle to keep lines short.
# ---------------------------------------------------------------------------

WARP_BROTHER_TIER_ICON = {
    None: "🟢",
    "tainted": "🟡",
    "exposed": "🟠",
    "volatile": "🔴",
}

# Librarian tiers use square icons to stay visually distinct from brother
# circle icons in the same field/legend (parity with armor ladder shape).

WARP_LIBRARIAN_TIER_ICON = {
    None: "🟩",
    "stable": "🟨",
    "resonant": "🟧",
    "surging": "🟥",
    "overloaded": "⬛",
    "abyssal": "🟫",
}

# Sanction status uses a 4-key ladder (sanctioned/screening_due/under_review/restricted),
# distinct from the 5-tier brother exposure ladder above.

WARP_SANCTION_STATUS_ICON = {
    "sanctioned": "🟢",
    "screening_due": "🟡",
    "under_review": "🟠",
    "restricted": "🔴",
}

# Boolean flag icons (orthogonal to tier ladders)

WARP_CORRUPTED_ICON = "⚠️"

WARP_SPREADER_ICON = "🌀"

WARP_LIBRARIAN_MARKER_ICON = "🧿"

# Ambient lines for Librarium chronicle posts

LIBRARIUM_AMBIENT_MESSAGES = [
    "*Wards hum quietly in the sanctum.*",
    "*The Librarium's silence is a held breath.*",
    "*Psychic hoods rest on their stands, awaiting need.*",
    "*Warp-glass lenses catch a light no one cast.*",
    "*Somewhere, a litany ends. Another begins.*",
    "*The Librarians' vigil continues, unspoken and unbroken.*",
]

# ---------------------------------------------------------------------------
# SOK-G: Pipehitter award announcement flavor text
# ---------------------------------------------------------------------------

