"""Module-level constants extracted from bot.py.

This module contains pure literal constants only (role IDs, channel IDs,
data file paths, thresholds, configuration defaults, mission sets, etc.).
It deliberately avoids any runtime state (locks, mutable globals, the
Discord client). bot.py re-exports everything here via ``from constants
import *`` so existing references and tests remain unchanged.
"""

import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Role IDs
# ---------------------------------------------------------------------------
WATCH_COMMAND_ROLE_ID = 1429281421931057283
# Watch Sergeant Role ID (for vet promotions)
WATCH_SERGEANT_ROLE_ID = 1429339146371203112
# Watch Librarian Role ID (for challenge eligibility notifications)
WATCH_LIBRARIAN_ROLE_ID = 1429339231654924318
# Watch Keeper Role ID
WATCH_KEEPER_ROLE_ID = 1488211606813806693
# Role ID for Reserves (inactive members)
RESERVES_ROLE_ID = 1443825801345765386

# ---------------------------------------------------------------------------
# Data file locations
# ---------------------------------------------------------------------------
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")
RITES_PATH = os.path.join(DATA_DIR, "rites.json")
MACHINE_SPIRITS_PATH = os.path.join(DATA_DIR, "machine_spirits.json")
ACTIVITY_STATUS_PATH = os.path.join(DATA_DIR, "activity_status.json")
ACTIVITY_STATUS_LAST_CHECK_PATH = os.path.join(DATA_DIR, "activity_status_last_check.json")
PROMOTION_TRACKING_PATH = os.path.join(DATA_DIR, "promotion_tracking.json")
MILESTONE_TRACKING_PATH = os.path.join(DATA_DIR, "milestone_tracking.json")
ARMOR_INTEGRITY_PATH = os.path.join(DATA_DIR, "armor_integrity.json")
ARMOR_SCAN_STATE_PATH = os.path.join(DATA_DIR, "armor_scan_state.json")
INDUCTION_OVERRIDES_PATH = os.path.join(DATA_DIR, "induction_overrides.json")
CHALLENGE_PROGRESS_PATH = os.path.join(DATA_DIR, "challenge_progress.json")
BLESSING_POOL_PATH = os.path.join(DATA_DIR, "blessing_pool.json")
FORGE_POOL_PATH = os.path.join(DATA_DIR, "forge_pool.json")
FORGE_CHRONICLE_PATH = os.path.join(DATA_DIR, "forge_chronicle.json")
FORGE_OVERRIDE_PATH = os.path.join(DATA_DIR, "forge_override.json")
LFG_QUEUE_PATH = os.path.join(DATA_DIR, "lfg_queues.json")
# Librarian / Warp Corruption subsystem
WARP_EXPOSURE_PATH = os.path.join(DATA_DIR, "warp_exposure.json")
WARDING_POOL_PATH = os.path.join(DATA_DIR, "warding_pool.json")
LIBRARIUM_CHRONICLE_PATH = os.path.join(DATA_DIR, "librarium_chronicle.json")
LIBRARIUM_OVERRIDE_PATH = os.path.join(DATA_DIR, "librarium_override.json")

# ---------------------------------------------------------------------------
# Channel IDs
# ---------------------------------------------------------------------------
AAR_CHANNEL_ID = 1429318686447108300  # ᛭⋅⋅after-action-reports⋅⋅᛭
ACTIVITY_STATUS_CHANNEL_ID = 1459043645499117630
VETERAN_PROMOTION_CHANNEL_ID = 1443813516979994634
SERVICE_STUDS_CHANNEL_ID = 1430055064969674777  # ᛭⋅⋅general-chat⋅⋅᛭
BLACK_LAURELS_CHANNEL_ID = 1443813633220935774
OATHSWORN_CHANNEL_ID = 1489282103119052903
TECHMARINE_STAFF_CHANNEL_ID = 1485797067577102377
LIBRARIUS_STAFF_CHANNEL_ID = 1482786608137769182
# Librarian operations / monitoring channel (set after creation; falls back to LIBRARIUS_STAFF_CHANNEL_ID)
LIBRARIUM_WATCH_CHANNEL_ID: int = 0  # populate when channel is created
# Dreadnought inactivity notification channel (High Command)
DREADNOUGHT_INACTIVITY_CHANNEL_ID = 1443813516979994634

# ---------------------------------------------------------------------------
# LFG Queue defaults (can be overridden in config.json under "lfg")
# ---------------------------------------------------------------------------
LFG_PC_PLAYER_ROLE_ID_DEFAULT = 1470455014022582343
LFG_CONSOLE_PLAYER_ROLE_ID_DEFAULT = 1470455285230469180
LFG_QUEUE_EXPIRY_MINUTES_DEFAULT = 30
LFG_QUEUE_TYPES_DEFAULT = {
    "operation": {"max_players": 3, "max_console": None, "display": "Operation", "ping_role_id": None},
    "siege": {"max_players": 3, "max_console": None, "display": "Siege", "ping_role_id": None},
    "omega": {"max_players": 5, "max_console": 2, "display": "Omega", "ping_role_id": None},
}

# ---------------------------------------------------------------------------
# Forge requisition pool / blessing pool configuration
# ---------------------------------------------------------------------------
FORGE_POOL_COST_PER_CHARGE = 10  # Armory points spent per blessing charge
FORGE_POOL_DAILY_LIMIT = 5  # Max requisitions per Techmarine per day
FORGE_POOL_MAX_CHARGES = 60  # Maximum charges the forge can hold (600 pts)

BLESSING_POOL_MAX = 10  # Maximum blessings per Techmarine
BLESSING_POOL_REGEN_HOURS = 24 / 10  # 2.4 hours per blessing regeneration
BLESSING_RECIPIENT_COOLDOWN_HOURS = 24  # Cooldown window for recipient blessing count
BLESSING_RECIPIENT_MAX_PER_DAY = 3  # Maximum blessings per recipient per 24h
BLESSING_RECIPIENT_PER_BLESSING_COOLDOWN_HOURS = 4  # Minimum hours between blessings for same recipient

# Intensive blessing charge costs (full heal to nominal, guaranteed - no roll)
INTENSIVE_BLESSING_COSTS = {
    None: 0,  # Nominal: cannot use intensive
    "damaged": 2,  # Damaged -> Nominal: 2 charges (guaranteed)
    "compromised": 2,  # Compromised -> Nominal: 2 charges (guaranteed)
    "critical": 3,  # Critical -> Nominal: 3 charges (guaranteed)
    "fractured": 4,  # Fractured -> Nominal: 4 charges (guaranteed)
}

# Forge drain per charge used - scales with tier being healed
FORGE_DRAIN_PER_CHARGE = {
    None: 1,  # Nominal: 1 pt per charge
    "damaged": 2,  # Damaged: 2 pts per charge
    "compromised": 3,  # Compromised: 3 pts per charge
    "critical": 4,  # Critical: 4 pts per charge
    "fractured": 5,  # Fractured: 5 pts per charge
}

# Blessing roll configuration - asymmetric state-based probabilities
BLESSING_ROLL_PROBABILITIES = {
    None: (0.015, 0.015),  # Nominal: 1.5/97/1.5 - routine maintenance
    "damaged": (0.045, 0.045),  # Damaged: 4.5/91/4.5 - minor repair
    "compromised": (0.075, 0.075),  # Compromised: 7.5/85/7.5 - agitated spirit
    "critical": (0.12, 0.09),  # Critical: 12/79/9 - volatile, asymmetric
    "fractured": (0.15, 0.15),  # Fractured: 15/70/15 - desperate spirit
}
# Legacy thresholds (used as fallback)
BLESSING_ROLL_CRIT_FAIL_THRESHOLD = 0.05  # Bottom 5% = crit fail
BLESSING_ROLL_CRIT_SUCCESS_THRESHOLD = 0.95  # Top 5% = crit success
BLESSING_CRIT_SUCCESS_GRACE_POINTS = -10  # Grace points on crit success

# ---------------------------------------------------------------------------
# Librarian / Warp Corruption pool configuration
# ---------------------------------------------------------------------------
WARDING_POOL_MAX = 10  # Max wards per Librarian
WARDING_POOL_REGEN_HOURS = 24 / 10  # 2.4 hours per ward regeneration
WARDING_RECIPIENT_COOLDOWN_HOURS = 24  # 24h window for recipient ward count
WARDING_RECIPIENT_MAX_PER_DAY = 3  # Max wards per recipient per 24h
WARDING_RECIPIENT_PER_WARDING_COOLDOWN_HOURS = 4  # Min hours between cleanses on same recipient

# Direct susceptibility gain from Black Laurels missions (by difficulty class).
# Points accumulate as exposure/risk — not direct corruption. An infection
# roll is rolled after the gain is applied (mirrors armor: wear accumulates,
# damage rolls on each exposure event).
WARP_BL_SUSCEPTIBILITY_GAIN = {
    "absolute": 4,
    "hard_stratagem": 5,
    "omega_ops": 20,
}
# Backwards-compat alias (some legacy code/tests may still import this name).
WARP_BL_EXPOSURE_GAIN = WARP_BL_SUSCEPTIBILITY_GAIN

# Susceptibility gain from /warp_scry on the casting Librarian.
WARP_SCRY_SUSCEPTIBILITY_GAIN = 1

# Daily cap on unique infectious squadmates that can spread to a brother (24h rolling)
WARP_SPREAD_DAILY_UNIQUE_SOURCE_CAP = 2
# Susceptibility added to a target when contagion successfully spreads.
WARP_SPREAD_SUSCEPTIBILITY_GAIN = 1
# Legacy alias retained for compatibility.
WARP_SPREAD_AMOUNT = WARP_SPREAD_SUSCEPTIBILITY_GAIN

# Grace susceptibility granted on a crit_success cleanse (per charge invested).
# Mirrors armor's BLESSING_CRIT_SUCCESS_GRACE_POINTS (-10).
WARP_CRIT_SUCCESS_GRACE_POINTS = -10

# Librarian decay: -1 susceptibility every N hours (mirrors regen cadence).
# Applies to Librarians regardless of infection_state — passive psychic discipline.
WARP_LIBRARIAN_DECAY_HOURS = 24 / 10  # one point every 2.4h

# Librarian transfer ratio: fraction of cleansed susceptibility absorbed by the
# cleansing Librarian as their own susceptibility gain on normal/crit_fail outcomes.
WARP_LIBRARIAN_TRANSFER_RATIO = 0.10
# Minimum transfer amount when any exposure was removed.
WARP_LIBRARIAN_TRANSFER_MIN = 1

# ---------------------------------------------------------------------------
# Scheduler settings (defaults; can be overridden in config.json under
# 'schedules')
# ---------------------------------------------------------------------------
SCHEDULE_DAILY_AUDIT_ENABLED = False
SCHEDULE_DAILY_AUDIT_SPAN_DAYS = 1

# Weekly maintenance settings (Tuesday 8 AM UTC by default)
SCHEDULE_WEEKLY_MAINTENANCE_ENABLED = True
SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS = 45
SCHEDULE_WEEKLY_MAINTENANCE_DAY = 1  # 0=Monday, 1=Tuesday, ..., 6=Sunday
SCHEDULE_WEEKLY_MAINTENANCE_HOUR = 8  # Hour in UTC

# Weekly reparse settings (Sunday 6 AM UTC by default, before weekly maintenance)
SCHEDULE_WEEKLY_REPARSE_ENABLED = True
SCHEDULE_WEEKLY_REPARSE_SPAN_DAYS = 7
SCHEDULE_WEEKLY_REPARSE_DAY = 6  # 0=Monday, 1=Tuesday, ..., 6=Sunday
SCHEDULE_WEEKLY_REPARSE_HOUR = 6  # Hour in UTC

# Monthly archive audit settings (runs audit_archive_discrepancies on the 1st of each month)
SCHEDULE_MONTHLY_ARCHIVE_AUDIT_ENABLED = True
SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS = 45
SCHEDULE_MONTHLY_ARCHIVE_AUDIT_HOUR = 10

# ---------------------------------------------------------------------------
# Milestone announcement settings (weekly check)
# ---------------------------------------------------------------------------
MILESTONES_ENABLED = True
MILESTONES_CHANNEL_ID: int = 1430055064969674777  # ᛭⋅⋅general-chat⋅⋅᛭
MILESTONES_CHECK_INTERVAL_DAYS = 7  # Check once per week
MILESTONES_INCREMENTS = {
    "aar_points": 2500,
    "aar_count": 500,
    "geneseed_recoveries": 500,
    "armory_data": 1000,
    "hive_tyrant_kills": 100,
    "bio_titan_kills": 100,
    "tyranid_prime_kills": 100,
}

# ---------------------------------------------------------------------------
# Black Laurels / Campaign Medal configuration
# ---------------------------------------------------------------------------
# Black Laurels strict enforcement begins on Feb 20, 2026 at 00:00 UTC
BLACK_LAURELS_STRICT_ENFORCEMENT_DATE = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
# Black Laurels role ID for parsing
BLACK_LAURELS_ROLE_ID = 1440108298115485716
# Leviathan Protocol role ID for parsing
LEVIATHAN_PROTOCOL_ROLE_ID = 1486066148834541619
# Black Reef Persecution role ID - allows Black Laurels with Hard-Stratagem when present on Mission line
BLACK_REEF_PERSECUTION_ROLE_ID = 1496892435496833054
# Pipehitter role IDs for parsing
PIPEHITTER_ROLE_ID = 1435812894532042843
DISTINGUISHED_PIPEHITTER_ROLE_ID = 1480420419063386275

# Missions eligible for Pipehitter mentions
PIPEHITTER_ELIGIBLE_MISSIONS = {
    "inferno",
    "vox liberatis",
    "reliquary",
    "fall of atreus",
    "termination",
    "obelisk",
    "exfiltration",
    "vortex",
    "reclamation",
    "disruption",
}
# Required missions for Black Laurels eligibility (all required for new earners)
BLACK_LAURELS_REQUIRED_MISSIONS = {
    "inferno",
    "decapitation",
    "vox liberatis",
    "ballistic engine",
    "exfiltration",
    "termination",
    "reclamation",
    "disruption",
}
# Grandfathered missions - users who already have the role are assumed to have completed these
BLACK_LAURELS_GRANDFATHERED_MISSIONS = {
    "inferno",
    "decapitation",
    "vox liberatis",
    "ballistic engine",
    "exfiltration",
    "termination",
    "reclamation",
}

# Kadaku Campaign Medal required missions (all required with @Leviathan Protocol tag)
KADAKU_CAMPAIGN_REQUIRED_MISSIONS = {
    "inferno",
    "termination",
    "reclamation",
}

# Black Reef Campaign Medal required missions (all required with @Black Reef Persecution tag)
BLACK_REEF_REQUIRED_MISSIONS = {
    "inferno",
    "decapitation",
    "fall of atreus",
    "ballistic engine",
    "termination",
    "obelisk",
    "vortex",
    "reclamation",
}

# The Order Omega required missions (all 12 available missions at Omega difficulty with Black Laurels tag)
ORDER_OMEGA_REQUIRED_MISSIONS = {
    "inferno",
    "decapitation",
    "vox liberatis",
    "reliquary",
    "fall of atreus",
    "ballistic engine",
    "termination",
    "obelisk",
    "vortex",
    "reclamation",
    "disruption",
    "exfiltration",
}

# Challenge award role IDs for eligibility checking
KADAKU_CAMPAIGN_MEDAL_ROLE_ID = 1486067010747236472
BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID = 1497087426219348069
DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID = 1497087831074537562
CRUX_TERMINATUS_ROLE_ID = 1476288996756820109
THE_ORDER_OMEGA_ROLE_ID = 1502135764312526858

# ---------------------------------------------------------------------------
# Specialist award thresholds and role mappings
# ---------------------------------------------------------------------------
# Award role IDs (looked up by ID to avoid name change issues)
ARDENT_RAIDER_ROLE_ID = 1436170746283163770  # Ardent Raider Ribbon
APOTHECARION_SERVICE_MEDAL_ROLE_ID = 1436434868652212275  # Apothecarion Service Medal
CRIMSON_LAURELS_ROLE_NAME = "Crimson Laurels"

# Specialist role names for mentions (looked up dynamically)
TECHMARINE_ROLE_NAME = "Watch Techmarine"
APOTHECARY_ROLE_NAME = "Watch Apothecary"
LIBRARIAN_ROLE_NAME = "Watch Librarian"
VOID_WARDEN_ROLE_NAME = "Void Warden"
FORGEMASTER_ROLE_NAME = "Forgemaster"

# Award eligibility thresholds
ARDENT_RAIDER_ARMORY_POINTS_THRESHOLD = 200
FOR_THE_FALLEN_GENESEED_POINTS_THRESHOLD = 150
CRIMSON_LAURELS_AAR_POINTS_THRESHOLD = 1000

# ---------------------------------------------------------------------------
# Dreadnought role IDs
# ---------------------------------------------------------------------------
DREADNOUGHT_CADRE_ROLE_ID = 1497783424792924331
VENERABLE_DREADNOUGHT_ROLE_ID = 1436522565110726686
HONORED_DREADNOUGHT_ROLE_ID = 1497089446833819658
INTERRED_BROTHER_ROLE_ID = 1497089965685739582

# ---------------------------------------------------------------------------
# Terminus Slayer role IDs for Crux Terminatus verification
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_ROLE_IDS = {
    1452803611477147668,  # Master Terminus Slayer
    1449257352112111646,  # Terminus Slayer (Assault)
    1450230281599713451,  # Terminus Slayer (Tactical)
    1450230501804609697,  # Terminus Slayer (Vanguard)
    1450230789034737748,  # Terminus Slayer (Bulwark)
    1450231020686278656,  # Terminus Slayer (Sniper)
    1450231189028737166,  # Terminus Slayer (Heavy)
    1476623936254115992,  # Terminus Slayer (Techmarine)
}

# ---------------------------------------------------------------------------
# Challenge roles for /completed_challenges command
# Each entry is (role_id, display_name, emoji_hint)
# emoji_hint can be a custom emoji name to look up, "unicode:<char>" for a
# literal unicode emoji, or None to skip
# ---------------------------------------------------------------------------
CHALLENGE_ROLES = [
    # SOK-G Elite
    (1480420419063386275, "Distinguished SOK-G: Pipehitter", "DistinguishedSOKGServiceMedal"),
    (1435812894532042843, "SOK-G: Pipehitter", "SOKGServiceMedal"),
    # Terminus Slayer variants
    (1452803611477147668, "Master Terminus Slayer", "MasterTerminusSlayer"),
    (1449257352112111646, "Terminus Slayer (Assault)", "1stAwardTerminusSlayer"),
    (1450230281599713451, "Terminus Slayer (Tactical)", "1stAwardTerminusSlayer"),
    (1450230501804609697, "Terminus Slayer (Vanguard)", "1stAwardTerminusSlayer"),
    (1450230789034737748, "Terminus Slayer (Bulwark)", "1stAwardTerminusSlayer"),
    (1450231020686278656, "Terminus Slayer (Sniper)", "1stAwardTerminusSlayer"),
    (1450231189028737166, "Terminus Slayer (Heavy)", "1stAwardTerminusSlayer"),
    (1476623936254115992, "Terminus Slayer (Techmarine)", "1stAwardTerminusSlayer"),
    # Laurels
    (1450595241508733183, "Crimson Laurels", "CrimsonLaurelsMedal"),
    (1440108298115485716, "Black Laurels", "BlackLaurelsMedal"),
    # Service awards
    (1436434868652212275, "Apothecarion Service Medal", "ApothecarionServiceMedal"),
    (1436170746283163770, "Ardent Raider Ribbon", "ArdentRaiderRibbon"),
    # Elite challenges
    (1476288996756820109, "Crux Terminatus", "CruxTerminatusMedal"),
    (1465020459794956349, "White Hand of Death", "ClandestineOperationsMedal"),
    (1465021610812637214, "Red Hand of Doom", "DistinguishedClandestineoperati"),
    (1486067010747236472, "Kadaku Campaign Medal", "KadakuCampaignMedal"),
    (1497087426219348069, "Black Reef Campaign Medal", "BlackReefCampaignMedal"),
    (1497087831074537562, "Distinguished Black Reef Campaign Medal", "DistinguishedBlackReefCampaign"),
    (1502135764312526858, "The Order Omega", "TheOrderOmega"),
]

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
# Control whether startup/shutdown status broadcasts are sent.
BROADCAST_STATUS = True


# ---------------------------------------------------------------------------
# Display-name normalization
# ---------------------------------------------------------------------------
# Some users set Discord nicknames using "small caps" / phonetic-block unicode
# characters (e.g. "ᴡᴀᴛᴄʜ ᴄʜᴀᴘʟᴀɪɴ sᴏғᴀ"). These code points have NO Unicode
# compatibility decomposition, so unicodedata.normalize("NFKD", ...) leaves
# them untouched, which breaks rank-prefix matching and styled name display.
# This translation table maps the common stylistic variants back to ASCII.
_DECORATIVE_LETTER_MAP = {
    # Latin small-caps (Phonetic Extensions, Latin Extended-D, etc.)
    "ᴀ": "A", "ʙ": "B", "ᴄ": "C", "ᴅ": "D", "ᴇ": "E",
    "ꜰ": "F", "ɢ": "G", "ʜ": "H", "ɪ": "I", "ᴊ": "J",
    "ᴋ": "K", "ʟ": "L", "ᴍ": "M", "ɴ": "N", "ᴏ": "O",
    "ᴘ": "P", "ǫ": "Q", "ʀ": "R", "ꜱ": "S", "ᴛ": "T",
    "ᴜ": "U", "ᴠ": "V", "ᴡ": "W", "x": "x",            "ʏ": "Y", "ᴢ": "Z",
    # Cyrillic look-alike used as small-caps F
    "ғ": "F",
    # Bold / italic / monospace mathematical alphanumeric letters
    # (𝐀-𝐳, 𝐴-𝑧, 𝑨-𝒛, 𝒜-𝓏, 𝓐-𝔃, 𝔄-𝔷, 𝔸-𝕫, 𝕬-𝖟, 𝖠-𝗓, 𝗔-𝘇, 𝘈-𝘻, 𝘼-𝙯, 𝙰-𝚣)
    # We handle these via NFKD which decomposes them properly; this dict only
    # covers the small-cap block which NFKD leaves alone.
}
# Build str.translate-friendly table (ord -> str)
_DECORATIVE_TRANSLATE = {ord(k): v for k, v in _DECORATIVE_LETTER_MAP.items()}


def _normalize_display_name(name: str) -> str:
    """Normalize decorative unicode in a display name back to plain ASCII letters.

    Handles:
    - Small-caps / phonetic letterforms (e.g. ``ᴡᴀᴛᴄʜ`` -> ``WATCH``)
    - Mathematical alphanumeric variants via NFKD (e.g. ``𝗁𝖾𝗅𝗅𝗈`` -> ``hello``)

    Does NOT remove stud pips (●⚬▬) — callers strip those separately so they
    can keep that step optional. Returns the input unchanged on any error.
    """
    if not isinstance(name, str) or not name:
        return name
    try:
        import unicodedata as _ud
        # First pass: bold/italic/monospace mathematical letters decompose via NFKD
        out = _ud.normalize("NFKD", name)
        # Second pass: small-caps block (no NFKD path) — direct translation
        out = out.translate(_DECORATIVE_TRANSLATE)
        # Drop combining marks left over from NFKD (e.g. accents); preserve
        # spaces and punctuation.
        out = "".join(ch for ch in out if not _ud.combining(ch))
        return out
    except Exception:
        return name


def _strip_display_name(name: str) -> str:
    """Normalize decorative unicode AND strip stud pips (●⚬▬). Whitespace-trimmed.

    Centralizes the common pattern previously implemented inline as
    ``display_name.replace("●", "").replace("⚬", "").strip()`` across modules.
    """
    if not isinstance(name, str) or not name:
        return name
    out = _normalize_display_name(name)
    out = out.replace("●", "").replace("⚬", "").replace("▬", "").strip()
    return out
