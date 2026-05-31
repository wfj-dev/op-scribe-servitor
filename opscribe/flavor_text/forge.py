"""Armor integrity, forge, and Mechanicus subsystem data."""

from typing import Dict, List  # noqa: F401

TECHMARINE_SIGNATURES: List[str] = [
    "I speak the Rites of Activation, and the machine-spirit awakens.",
    "With sacred oils and binharic prayer, this work is sanctified.",
    "The Motive Force flows through my hands into this blessed armor.",
    "By cog and gear, by circuit and servo, I seal this consecration.",
    "The Omnissiah's blessing descends through my ministrations.",
    "Through the Litany of Ignition, the war-spirit stirs.",
    "I have communed with the machine-spirit; it is at peace.",
    "The holy unguents are applied; the rites are complete.",
    "In nomine Machinae, this armor is bound to sacred purpose.",
    "The data-hymns are sung; the spirit-core is awakened.",
]

# Random sacred Mechanicus phrases to include in attestations

SACRED_MECHANICUS_PHRASES: List[str] = [
    "Praise the Omnissiah.",
    "The Machine God watches over this work.",
    "Data is sacred. Knowledge is power.",
    "From iron, cometh strength.",
    "The spirit of the machine is willing.",
    "Let the blessed cogitator record this deed.",
    "The Motive Force guides all.",
    "In the name of the Machine God, so it is done.",
    "Blessed is the machine that serves.",
    "By the grace of the Fabricator-General.",
    "The Quest for Knowledge continues.",
    "Steel and silicon, blessed and true.",
    "The Cant Mechanicus sanctifies this moment.",
    "May your augmetics never falter.",
    "The Void Dragon stirs not against this work.",
]

# Phrases for when the Forgemaster performs rites upon their own armor
# Blends Mechanicus reverence with Hawk Lords identity (raptor/sky/hunt imagery)
# Generic Mechanicus self-attestation phrases (role-focused)

FORGEMASTER_SELF_ATTESTATION_GENERIC: List[str] = [
    "The Omnissiah witnesses—I am both priest and supplicant.",
    "The master's hand tends to the master's plate—this burden is mine alone.",
    "None may bless what I have wrought but I who forged it.",
    "In solitude, the Forgemaster communes with his own machine-spirit.",
    "I speak the canticles to myself, for who else would understand?",
    "From my forge, to my flesh, to my faith—the circle closes.",
    "The Long Watch demands self-reliance. I answer.",
    "My armor knows no other hand. This rite is mine to perform.",
]

# Chapter-specific self-attestation phrases (chapter identity when self-blessing)

SPIRIT_RESTORATION_PHRASES = [
    "Sacred oils soothe worn servos. The bond holds. What was stressed is now restored.",
    "The machine spirit's agitation fades as blessed unguents are applied. Integrity restored.",
    "Damaged systems repaired, seals renewed. The spirit settles into watchful calm.",
    "Rites of maintenance complete. The armor remembers its purpose.",
    "The Litany of Restoration calms the wounded spirit. Pain becomes memory; vigilance returns.",
    "Blessed lubricants ease damaged joints. The spirit's anger subsides into quiet readiness.",
    "Micro-fractures sealed, war-damage mended. The machine spirit exhales gratitude in binharic code.",
    "The Rite of Soothing is complete. What was wounded now stands whole.",
    "Damaged neural pathways rerouted. The spirit's core processes stabilize.",
    "Incense and unguents appease the troubled spirit. The bond endures.",
]

# Flavor text for spirit re-consecration (spirit fractured)

SPIRIT_RECONSECRATION_PHRASES = [
    "The previous spirit has departed, its bond severed through neglect. A new spirit must learn to trust you anew. This is not celebration. This is beginning again.",
    "What was bonded is now lost. Fresh spirit bound to old armor. The Omnissiah grants no second chances—only new beginnings.",
    "The machine spirit you knew is gone. Another takes its place, wary and untested. Earn its trust.",
    "Re-consecration complete. The new spirit knows nothing of your deeds. Prove yourself worthy once more.",
    "The death-cry of the old spirit echoes in the cogitator's memory. A new presence stirs—untrusting, watchful.",
    "Neglect has consequences. The old spirit fled into the data-void. This new one regards you with cold suspicion.",
    "The soul that knew you is gone. Another inhabits this warplate now—a stranger wearing familiar armor.",
    "Through sacred rites, a dormant spirit is awakened and bound. It does not know you. It does not yet trust you.",
    "The Rite of Severance is spoken. The Rite of Binding follows. One spirit dies; another is born. Begin again.",
    "The armor's old spirit has been released to the Motive Force. Its replacement must learn your worth from nothing.",
]

# Ambient messages for the forge channel (posted when forge is quiet)

FORGE_AMBIENT_MESSAGES = [
    "*The Forge rests in prepared silence.*",
    "*Servo-arms hang still, awaiting the next supplicant.*",
    "*Incense coils upward from dormant censers.*",
    "*Sacred oils gleam in their blessed containers, awaiting use.*",
    "*The hum of cogitators fills the space—ever watchful, ever patient.*",
    "*Machine spirits slumber in their blessed housings, dreams of duty.*",
    "*The smell of sacred unguents permeates the chamber.*",
    "*Somewhere in the Forge, a servo-skull catalogues ancient rites.*",
    "*The Forge awaits those who honor the Omnissiah.*",
    "*Cooling vents exhale measured breaths. The Forge persists.*",
    "*Data-candles flicker in alcoves, their light steady and true.*",
    "*The hiss of pneumatics fades. Silence returns.*",
    "*Augury crystals pulse with dormant potential.*",
    "*The Watch Techmarines' vigil continues, eternal and unwavering.*",
    "*In the deep places of the Forge, wisdom accumulates.*",
]


# ─────────────────────────────────────────────────────────────────────────────
# Librarian / Warp Corruption subsystem data
# ─────────────────────────────────────────────────────────────────────────────

# Brother infection tiers (exact mirror of armor's damaged/compromised/critical).
# The fourth state (warp_corrupted, parallel to spirit_fractured) is tracked as
# a separate boolean flag on the state record — not a tier value.

ARMOR_DAMAGE_TIERS = ["damaged", "compromised", "critical"]

ARMOR_DAMAGE_PENALTIES = {"damaged": 1, "compromised": 2, "critical": 3}  # Legacy fixed

# Mission name to planet mapping (for armor alert debrief)

MISSION_TO_PLANET = {
    "inferno": "Kadaku",
    "termination": "Kadaku",
    "purgation": "Kadaku",
    "normal_siege": "Kadaku",
    "hard_siege": "Kadaku",
    "decapitation": "Avarax",
    "vox liberatis": "Avarax",
    "ballistic engine": "Avarax",
    "exfiltration": "Avarax",
    "reclamation": "Avarax",
    "disruption": "Avarax",
    "reliquary": "Demerium",
    "fall of atreus": "Demerium",
    "obelisk": "Demerium",
    "vortex": "Demerium",
}

# Probability distributions for AAR penalties per damage tier
# Format: {tier: {penalty: probability}} where probabilities must sum to 1.0
# Penalty 0 = no penalty, 1-4 = AAR reduction

ARMOR_PENALTY_PROBABILITIES = {
    None: {0: 1.0},  # Nominal: no penalty
    "damaged": {0: 0.90, 1: 0.085, 2: 0.010, 3: 0.005},  # 10% penalty chance
    "compromised": {0: 0.835, 1: 0.10, 2: 0.05, 3: 0.015},  # ~17% penalty chance
    "critical": {0: 0.75, 1: 0.085, 2: 0.10, 3: 0.065},  # 25% penalty chance
    "fractured": {0: 0.70, 1: 0.05, 2: 0.085, 3: 0.10, 4: 0.065},  # 30% penalty chance
}

# Detection alert chances per AAR while damaged (early warning system)
# Roll checked each AAR; if successful, sends detection alert before penalty occurs
# Only one detection alert per tier (tracked in armor state)

ARMOR_DETECTION_CHANCES = {
    "damaged": 0.20,  # 20% chance per AAR
    "compromised": 0.35,  # 35% chance per AAR
    "critical": 0.50,  # 50% chance per AAR
    "fractured": 1.0,  # 100% - always alert
}

# Scan miss chances for armor_status command (brothers may not show)
# Flat 20% undetected chance across all tiers except fractured

ARMOR_SCAN_MISS_CHANCES = {
    "nominal": 0.20,  # 20% chance to miss
    "damaged": 0.20,  # 20% chance to miss
    "compromised": 0.20,  # 20% chance to miss
    "critical": 0.20,  # 20% chance to miss
    "fractured": 0.0,  # 0% - always visible
}

# Predictive detection chances for nominal brothers based on cycle count
# Used to warn Techmarines of impending damage risk

ARMOR_SCAN_PREDICTIVE_TIERS = [
    {"min": 0, "max": 4, "chance": 0.0},  # No warning in safe zone
    {"min": 5, "max": 9, "chance": 0.10},  # 10% chance to detect risk
    {"min": 10, "max": 14, "chance": 0.25},  # 25% chance
    {"min": 15, "max": 19, "chance": 0.40},  # 40% chance
    {"min": 20, "max": None, "chance": 0.60},  # 60% chance
]

# Intensive scan cost (armory points via requisition_supplies)

INTENSIVE_SCAN_COST = 20

# Default probability tiers (can be overridden in config)
# Gaps shrink as cycles increase to create mounting pressure

DEFAULT_ARMOR_PROBABILITY_TIERS = [
    {
        "min": 0,
        "max": 4,
        "chance": 0.0,
        "damage_weights": {"damaged": 100, "compromised": 0, "critical": 0},
    },
    {
        "min": 5,
        "max": 9,
        "chance": 0.02,
        "damage_weights": {"damaged": 90, "compromised": 8, "critical": 2},
    },
    {
        "min": 10,
        "max": 14,
        "chance": 0.08,
        "damage_weights": {"damaged": 80, "compromised": 15, "critical": 5},
    },
    {
        "min": 15,
        "max": 19,
        "chance": 0.20,
        "damage_weights": {"damaged": 65, "compromised": 25, "critical": 10},
    },
    {
        "min": 20,
        "max": None,
        "chance": 0.40,
        "damage_weights": {"damaged": 50, "compromised": 35, "critical": 15},
    },
]

# Grace period defaults

DEFAULT_ARMOR_GRACE_PERIOD_MIN_POINTS = 100

DEFAULT_ARMOR_GRACE_PERIOD_MIN_DAYS = 7

# Fracture threshold (AAR submissions at critical before spirit fractures)

DEFAULT_ARMOR_FRACTURE_THRESHOLD = 3

# Flavor text for armor status in forge_rite

ARMOR_STATUS_NOMINAL = {
    "plate": "NOMINAL",
    "spirit": "STABLE",
    "rite": "MAINTENANCE",
}

ARMOR_STATUS_DAMAGED = {
    "plate": "MINOR WEAR",
    "spirit": "STABLE",
    "rite": "RESTORATION",
}

ARMOR_STATUS_COMPROMISED = {
    "plate": "STRUCTURAL STRESS",
    "spirit": "AGITATED",
    "rite": "EMERGENCY RITES",
}

ARMOR_STATUS_CRITICAL = {
    "plate": "CRIT FAIL",
    "spirit": "UNSTABLE",
    "rite": "STABILIZATION",
}

ARMOR_STATUS_FRACTURED = {
    "plate": "CRIT FAIL",
    "spirit": "FRACTURED",
    "rite": "RE-CONSECRATION",
}

# Flavor text for spirit restoration (was damaged but not fractured)

