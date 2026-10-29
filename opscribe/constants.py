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
# Watch Master Role ID
WATCH_MASTER_ROLE_ID = 1429281838794543167
# Watch Brother Role ID (base role for all enlisted brothers)
WATCH_BROTHER_ROLE_ID = 1429338953227440148
# High Command role ID (members with this role appear in the HC roster embed)
HIGH_COMMAND_ROLE_ID = 1452913063970865203
# Watch Sergeant Role ID (for vet promotions)
WATCH_SERGEANT_ROLE_ID = 1429339146371203112
# Veteran Sergeant Role ID
VETERAN_SERGEANT_ROLE_ID = 1530699848272052357
# Watch Captain and Watch Lieutenant role IDs (for transfer and campaign notifications)
WATCH_CAPTAIN_ROLE_ID = 1429341171301220502
WATCH_LIEUTENANT_ROLE_ID = 1429340527924613120
# Watch Librarian Role ID (for challenge eligibility notifications)
WATCH_LIBRARIAN_ROLE_ID = 1429339231654924318
# Watch Keeper Role ID
WATCH_KEEPER_ROLE_ID = 1488211606813806693
# Huntmaster Role ID (High Command)
HUNTMASTER_ROLE_ID = 1510397444113039581
# Kill-Marine Role ID (Black Vault Cadre specialist, led by the Huntmaster)
KILL_MARINE_ROLE_ID = 1553797140340416633
# Role ID for Reserves (inactive members)
RESERVES_ROLE_ID = 1443825801345765386
# Role name for dreadnought reserve-equivalent status
INTERRED_BROTHER_ROLE_NAME = "Interred Brother"
# Role ID for Leave of Absence (LOA) — members on LOA are excluded from specialist requirement generation
LOA_ROLE_ID = 1513198169217830912
# Ping role ID for Black Laurels challenge announcements
BLACK_LAURELS_PING_ROLE_ID = 1429343212421644479

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
AWARD_QUEUE_PATH = os.path.join(DATA_DIR, "award_announcement_queue.json")
INDUCTION_OVERRIDES_PATH = os.path.join(DATA_DIR, "induction_overrides.json")
CHALLENGE_PROGRESS_PATH = os.path.join(DATA_DIR, "challenge_progress.json")
LFG_QUEUE_PATH = os.path.join(DATA_DIR, "lfg_queues.json")
# Terminus Kill Log subsystem
TERMINUS_SLAYER_PATH = os.path.join(DATA_DIR, "terminus_slayer.json")
# Kill-team / company honors tracking
HONORS_PATH = os.path.join(DATA_DIR, "honors.json")
# Governance poll state tracking
GOVERNANCE_POLLS_PATH = os.path.join(DATA_DIR, "governance_polls.json")

# ---------------------------------------------------------------------------
# Kill-team renown tiers (ordered lowest → highest; index = tier level)
# ---------------------------------------------------------------------------
KT_TITLE_TIERS = ["Unproven", "Initiated", "Vigilant", "Sworn", "Hallowed", "Eternal"]

# ---------------------------------------------------------------------------
# Channel IDs
# ---------------------------------------------------------------------------
AAR_CHANNEL_ID = 1429318686447108300  # ᛭⋅⋅after-action-reports⋅⋅᛭
ACTIVITY_STATUS_CHANNEL_ID = 1459043645499117630
VETERAN_PROMOTION_CHANNEL_ID = 1443813516979994634
SERVICE_STUDS_CHANNEL_ID = 1430055064969674777  # ᛭⋅⋅general-chat⋅⋅᛭
# Legacy alias: used by non-milestone notifications (e.g., roster grace-period revocation notices)
MILESTONES_CHANNEL_ID: int = SERVICE_STUDS_CHANNEL_ID
BLACK_LAURELS_CHANNEL_ID = 1443813633220935774
OATHSWORN_CHANNEL_ID = 1489282103119052903
# Backward-compatibility alias still referenced by roster_ops helpers.
MILESTONES_CHANNEL_ID = 1430055064969674777  # ᛭⋅⋅general-chat⋅⋅᛭
GOVERNANCE_POLL_CHANNEL_ID = 1489282103119052903
TECHMARINE_STAFF_CHANNEL_ID = 1485797067577102377
LIBRARIUS_STAFF_CHANNEL_ID = 1482786608137769182
KILL_LOG_CHANNEL_ID = 1538739010422186014  # kill-log channel
CHAPLAIN_STAFF_CHANNEL_ID = 1508553227656757308  # chaplain / reclusiam staff
APOTHECARY_STAFF_CHANNEL_ID = 1484793764634693692  # apothecary staff / notifications
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

# Monthly archive audit settings (runs audit_archive_discrepancies on the 1st of each month)
SCHEDULE_MONTHLY_ARCHIVE_AUDIT_ENABLED = True
SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS = 45
SCHEDULE_MONTHLY_ARCHIVE_AUDIT_HOUR = 10

# Daily role integrity audit settings
SCHEDULE_ROLE_INTEGRITY_AUDIT_ENABLED = False
SCHEDULE_ROLE_INTEGRITY_AUDIT_HOUR = 12

# ---------------------------------------------------------------------------
# Black Laurels / Campaign Medal configuration
# ---------------------------------------------------------------------------
# Black Laurels strict enforcement begins on Jun 1, 2026 at 00:00 UTC
BLACK_LAURELS_STRICT_ENFORCEMENT_DATE = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
# Policy patch release anchor for baseline freeze behavior.
CHALLENGE_POLICY_PATCH_RELEASE_DATE = datetime(2026, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
# Black Laurels role ID for parsing
BLACK_LAURELS_ROLE_ID = 1440108298115485716
# Leviathan Protocol role ID for parsing
LEVIATHAN_PROTOCOL_ROLE_ID = 1486066148834541619
# Black Reef Persecution role ID - allows Black Laurels with Hard-Stratagem when present on Mission line
BLACK_REEF_PERSECUTION_ROLE_ID = 1496892435496833054
# Defense of Herisor mission tag role ID (must be present on Mission line for challenge submissions)
HERISOR_DEFENSE_TAG_ROLE_ID = 1511108024922673233
HERISOR_DEFENSE_MIN_WAVES = 5
# Dual Vigil tag role ID - used on the Mission line in AARs (detection only)
DUAL_VIGIL_ROLE_ID = 1509277797611470931
# Dual Vigil award role ID - the role actually granted when the challenge is completed
DUAL_VIGIL_AWARD_ROLE_ID = 1509561627580694638
# Chapter Approved role ID (used for weekend point bonus parsing)
CHAPTER_APPROVED_ROLE_ID = 1467960627795464344
# Pipehitter role IDs for parsing
PIPEHITTER_ROLE_ID = 1435812894532042843
DISTINGUISHED_PIPEHITTER_ROLE_ID = 1480420419063386275
# Omega-Strat difficulty role ID for strict Octavian validation
OMEGA_STRAT_ROLE_ID = 1511760588936712293
CRUCIBLE_ROLE_ID = 1556313768286355557
ABSOLUTE_CHALLENGE_DIFFICULTIES = frozenset({"absolute_ops", "crucible_ops"})

# PvP AAR constants
PVP_DIFFICULTY_ROLE_ID = 1532428453725470851
PVP_ALLOWED_MAPS = {
    "cathedrum",
    "bastion",
    "mausoleum",
    "tomb",
    "bridge",
    "facility",
    "sanctum",
}
PVP_ALLOWED_GAME_MODES = {
    "sieze ground",
    "seize ground",
    "capture and control",
    "annihilation",
    "helbrute onslaught",
}
PVP_RESULT_POINTS = {
    "W": 4,
    "L": 2,
}

# ---------------------------------------------------------------------------
# Operational rating model (0-5000)
# ---------------------------------------------------------------------------
OP_RATING_MIN = 0
OP_RATING_MAX = 5000
OP_RATING_BASELINE = 1500
OP_RATING_SCALE_K = 190.0
OP_RATING_VOLUME_BETA = 0.09
OP_RATING_WINDOW_DAYS = 7
OP_RATING_DECAY_HALF_LIFE_DAYS = 7.0
OP_RATING_STRIKE_BONUS_FACTOR = 1.08
OP_RATING_SOFT_CAP_ENABLED = True
OP_RATING_SOFT_CAP_TAU = 3000.0

# Positive bucket weights
OP_RATING_WEIGHT_ABSOLUTE_OPS = 1.00
OP_RATING_WEIGHT_CRUCIBLE_OPS = OP_RATING_WEIGHT_ABSOLUTE_OPS
OP_RATING_WEIGHT_HARD_SIEGE = 1.25
OP_RATING_WEIGHT_HARD_STRATAGEM = 1.30
OP_RATING_WEIGHT_OMEGA_OPS = 1.65
OP_RATING_WEIGHT_OMEGA_STRAT = 2.00

# Loss bucket weights
OP_RATING_WEIGHT_NORMAL_SIEGE = -0.30
OP_RATING_WEIGHT_NORMAL_STRATAGEM = -0.30
OP_RATING_WEIGHT_LETHAL_OPS = -0.35
OP_RATING_WEIGHT_RUTHLESS_OPS = -0.40

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
    "purgation",
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
    "purgation",
}
# Per-mission requirement introduction timestamps used for 28-day grace gating.
BLACK_LAURELS_MISSION_ADD_DATES = {
    "inferno": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "decapitation": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "vox liberatis": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "ballistic engine": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "exfiltration": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "termination": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "reclamation": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "disruption": datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
    "purgation": datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
}
# Dual Vigil required missions — identical to Black Laurels; all 9 missions at Absolute with exactly 2 brothers
DUAL_VIGIL_REQUIRED_MISSIONS = BLACK_LAURELS_REQUIRED_MISSIONS
DUAL_VIGIL_MISSION_ADD_DATES = BLACK_LAURELS_MISSION_ADD_DATES

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

# The Octavian Incident required missions (all required with @The Octavian Incident tag)
OCTAVIAN_INCIDENT_REQUIRED_MISSIONS = {
    "vox liberatis",
    "obelisk",
    "fall of atreus",
    "reliquary",
}

# The Order Omega required missions (all 13 available missions at Omega difficulty with Black Laurels tag)
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
    "purgation",
}
ORDER_OMEGA_MISSION_ADD_DATES = {
    "inferno": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "decapitation": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "vox liberatis": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "reliquary": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "fall of atreus": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "ballistic engine": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "termination": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "obelisk": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "vortex": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "reclamation": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "disruption": datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
    "exfiltration": datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    "purgation": datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
}

# Dedicated Master Terminus Slayer role ID constant for terminus_ops auto-award
MASTER_TERMINUS_SLAYER_ROLE_ID = 1452803611477147668

# Mapping from Terminus Slayer class role ID → award_type string used in the dispatch queue
TERMINUS_SLAYER_CLASS_AWARD_TYPES: dict[int, str] = {
    1449257352112111646: "terminus_slayer_assault",
    1450230789034737748: "terminus_slayer_bulwark",
    1450231189028737166: "terminus_slayer_heavy",
    1450231020686278656: "terminus_slayer_sniper",
    1450230281599713451: "terminus_slayer_tactical",
    1476623936254115992: "terminus_slayer_techmarine",
    1450230501804609697: "terminus_slayer_vanguard",
}

# Challenge award role IDs for eligibility checking
KADAKU_CAMPAIGN_MEDAL_ROLE_ID = 1486067010747236472
DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID = 1519841481118978119
BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID = 1497087426219348069
DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID = 1497087831074537562
OCTAVIAN_INCIDENT_ROLE_ID = 1535280426938277990
OCTAVIAN_OPERATION_MEDAL_ROLE_ID = 1535280882145824798
DISTINGUISHED_OCTAVIAN_OPERATION_MEDAL_ROLE_ID = 1535281207007387729
CRUX_TERMINATUS_ROLE_ID = 1476288996756820109
THE_ORDER_OMEGA_ROLE_ID = 1502135764312526858
HERISOR_DEFENSE_MEDAL_ROLE_ID = 1511108645104910526
DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID = 1511109108231700690
DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID = 1511109325769281633

# ---------------------------------------------------------------------------
# Specialist award thresholds and role mappings
# ---------------------------------------------------------------------------
# Award role IDs (looked up by ID to avoid name change issues)
WATCH_VETERAN_ROLE_ID = 1429340812902273044  # Watch Veteran
ARDENT_RAIDER_ROLE_ID = 1436170746283163770  # Ardent Raider Ribbon
APOTHECARION_SERVICE_MEDAL_ROLE_ID = 1436434868652212275  # Apothecarion Service Medal
CRIMSON_LAURELS_ROLE_ID = 1450595241508733183  # Crimson Laurels

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

# Kill Log class roles (role_id -> display name).  Excludes Master Terminus Slayer.
KILL_LOG_CLASS_ROLES: dict[int, str] = {
    1449257352112111646: "Assault",
    1450230789034737748: "Bulwark",
    1450231189028737166: "Heavy",
    1450231020686278656: "Sniper",
    1450230281599713451: "Tactical",
    1476623936254115992: "Techmarine",
    1450230501804609697: "Vanguard",
}

# Valid terminus types for kill log submissions
TERMINUS_TYPES = ["Neurothrope", "Carnifex", "Helbrute"]

# Ranks that may verify/deny kill log entries (Watch Veteran+)
TERMINUS_VERIFIER_RANKS = {
    "Watch Veteran",
    "Oathsworn",
    "Bladeguard",
    "First Blade",
    "Blade Master",
    "Watch Sergeant",
    "Veteran Sergeant",
    "Watch Lieutenant",
    "Watch Captain",
    "Watch Chaplain",
    "Watch Apothecary",
    "Watch Librarian",
    "Watch Techmarine",
    "Watch Keeper",
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
    "Castellan",
    "Watch Master",
    "Venerable Dreadnought",
    "Honored Dreadnought",
    "Interred Brother",
}

# Verifier tier thresholds (rolling 7-day window, verify + deny count equally)
VERIFIER_TIER_THRESHOLDS = [
    (7, 1, 1),   # 7+ actions -> Tier 1 -> +1 AAR bonus
]

# Hours before an unverified kill log triggers a reminder ping
KILL_LOG_REMINDER_HOURS = 72

# Minutes before non-apothecary verifiers may verify/deny a submitted kill log
KILL_LOG_REVIEW_DELAY_MINUTES = 2

# Per-user cap on kill-log verify/deny review actions in a rolling time window
KILL_LOG_REVIEW_ACTION_LIMIT = 3
KILL_LOG_REVIEW_ACTION_WINDOW_HOURS = 24

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
    # Laurels / Vigil
    (1450595241508733183, "Crimson Laurels", "CrimsonLaurelsMedal"),
    (1440108298115485716, "Black Laurels", "BlackLaurelsMedal"),
    (1509561627580694638, "Order of the Aquiline Brotherhood", "AquilineBrotherhood"),
    # Service awards
    (1436434868652212275, "Apothecarion Service Medal", "ApothecarionServiceMedal"),
    (1436170746283163770, "Ardent Raider Ribbon", "ArdentRaiderRibbon"),
    # Elite challenges
    (1476288996756820109, "Crux Terminatus", "CruxTerminatusMedal"),
    (1465020459794956349, "White Hand of Death", "ClandestineOperationsMedal"),
    (1465021610812637214, "Red Hand of Doom", "DistinguishedClandestineoperati"),
    (1486067010747236472, "Kadaku Campaign Medal", "KadakuCampaignMedal"),
    (1519841481118978119, "Distinguished Kadaku Campaign Medal", "DistinguishedKadakuCampaign"),
    (1497087426219348069, "Black Reef Campaign Medal", "BlackReefCampaignMedal"),
    (1497087831074537562, "Distinguished Black Reef Campaign Medal", "DistinguishedBlackReefCampaign"),
    (1535280882145824798, "Octavian Operation Medal", "OctavianMedal"),
    (1535281207007387729, "Distinguished Octavian Operation Medal", "DistinguishedOctavianMedal"),
    (1502135764312526858, "The Order Omega", "TheOrderOmega"),
    (1511108645104910526, "Herisor Defense Medal", "HerisorDefense"),
    (1511109108231700690, "Distinguished Herisor Defense Medal", "DistinguishedHerisorDefense"),
    (1511109325769281633, "Distinguished Herisor Defense Medal with Valor", "HerisorDefensewithValor"),
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


def _is_member_in_reserves(member) -> bool:
    roles = getattr(member, "roles", []) or []
    return any(
        getattr(role, "id", None) == RESERVES_ROLE_ID
        or (getattr(role, "name", "") or "").strip().casefold() == "reserves"
        for role in roles
    )
