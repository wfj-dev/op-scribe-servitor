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
# Watch Captain and Watch Lieutenant role IDs (for strike directive distribution pings)
WATCH_CAPTAIN_ROLE_ID = 1429341171301220502
WATCH_LIEUTENANT_ROLE_ID = 1429340527924613120
# Watch Librarian Role ID (for challenge eligibility notifications)
WATCH_LIBRARIAN_ROLE_ID = 1429339231654924318
# Watch Keeper Role ID
WATCH_KEEPER_ROLE_ID = 1488211606813806693
# Huntmaster Role ID (High Command)
HUNTMASTER_ROLE_ID = 1510397444113039581
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
KILL_LOG_CHANNEL_ID = 1450572668750532699  # kill-log channel
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
# Dual Vigil tag role ID - used on the Mission line in AARs (detection only)
DUAL_VIGIL_ROLE_ID = 1509277797611470931
# Dual Vigil award role ID - the role actually granted when the challenge is completed
DUAL_VIGIL_AWARD_ROLE_ID = 1509561627580694638
# Chapter Approved role ID (used for weekend point bonus parsing)
CHAPTER_APPROVED_ROLE_ID = 1467960627795464344
# Pipehitter role IDs for parsing
PIPEHITTER_ROLE_ID = 1435812894532042843
DISTINGUISHED_PIPEHITTER_ROLE_ID = 1480420419063386275

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
    (1502135764312526858, "The Order Omega", "TheOrderOmega"),
    (1511108645104910526, "Herisor Defense Medal", "HerisorDefense"),
    (1511109108231700690, "Distinguished Herisor Defense Medal", "DistinguishedHerisorDefense"),
    (1511109325769281633, "Distinguished Herisor Defense Medal with Valor", "HerisorDefensewithValor"),
]

# ---------------------------------------------------------------------------
# Auto-roster embed configuration
# ---------------------------------------------------------------------------
ROSTER_STATE_PATH = os.path.join(DATA_DIR, "roster_state.json")

# Embed banner images (Discord attachment URLs — base URL without expiry params)
ROSTER_IMAGE_HIGH_COMMAND = "https://cdn.discordapp.com/attachments/1444855164023472192/1511919151302705203/High_Command.png?ex=6a2233ef&is=6a20e26f&hm=c88ba02f3f4046ddb5acbb3cf6bb5e404b3f112dddf295cfd0b4ebc4436fda40&"
ROSTER_IMAGE_COMPANY_COMMAND = "https://cdn.discordapp.com/attachments/1444855164023472192/1511919134290481213/Command.png?ex=6a2233eb&is=6a20e26b&hm=d0b8e09712f34e4527a62b17bf1e2380851f37970e0f6fe951f15f3a1042389e&"
ROSTER_IMAGE_KILLTEAM = "https://cdn.discordapp.com/attachments/1444855164023472192/1511919121988845670/Kill_Team.png?ex=6a2233e8&is=6a20e268&hm=925e89a9452defdc158150856e6da4939c15235bf448df84f26c477d93cf66eb&"
# Per-company KT embed banner images.  Falls back to ROSTER_IMAGE_KILLTEAM if a company is absent.
ROSTER_IMAGE_KILLTEAM_BY_COMPANY: dict[str, str] = {
    "Watch Company Primus": "https://cdn.discordapp.com/attachments/1444855164023472192/1512256170079948970/Primus_KT_Art.png?ex=6a236dcf&is=6a221c4f&hm=022125bbbc00a25f6ed194b79ec9a84185f7e3f6c1182de4d3626d3bd0fc2602&",
    "Watch Company Secundus": "https://cdn.discordapp.com/attachments/1444855164023472192/1511919121988845670/Kill_Team.png?ex=6a2233e8&is=6a20e268&hm=925e89a9452defdc158150856e6da4939c15235bf448df84f26c477d93cf66eb&",
}
# Per-company Command embed banner images.  Falls back to ROSTER_IMAGE_COMPANY_COMMAND if a company is absent.
ROSTER_IMAGE_COMPANY_COMMAND_BY_COMPANY: dict[str, str] = {
    "Watch Company Primus": "https://cdn.discordapp.com/attachments/1444066753427800165/1512655809669234918/image.png?ex=6a24e200&is=6a239080&hm=b7c19784cb8af6f88bf99361ed5e9efb6c0200736d73d1da074e7c41375731c4&"
}

# Maps configured Watch Company role name -> roster channel ID.
# The bot posts and maintains embeds inside each configured company channel.
ROSTER_COMPANY_CHANNELS: dict[str, int] = {
    "Watch Company Primus": 1433351509722267658,
    "Watch Company Secundus": 1458255466189684999,
    "Watch Company Tertius": 1527777716076548216,
}

# Ranks that appear in the Company Command roster section.
# Specialists now live in the dedicated HC + Specialists embed.
ROSTER_COMPANY_COMMAND_RANKS: set[str] = {
    "Watch Captain",
    "Watch Lieutenant",
}

# Maximum characters to use inside a single roster embed description
# before graceful truncation. Discord hard limit is 4096; we stay well below.
ROSTER_EMBED_DESC_LIMIT = 3800

# ---------------------------------------------------------------------------
# Chapter embed accent colors
# Used by tally_deeds to color the embed sidebar per-chapter.
# Fallback: 0x2ECC71 (default green) for any chapter not listed here.
# ---------------------------------------------------------------------------
CHAPTER_EMBED_COLORS: dict = {
    # --- Canon chapters ---
    "Angels of Defiance":     0xE3DAC9,  # Quartered bone & black; bone used as accent
    "Angels Encarmine":       0x7A0D14,  # Deep blood red with black trim
    "Angels of Vengeance":    0x0B0B0B,  # Jet black (Dark Angels Legion black)
    "Atlantian Spears":       0x1D7A8C,  # Teal blue (gold trim accents)
    "Black Shield":           0x0A0A0A,  # Black armor, no chapter heraldry
    "Black Templars":         0x100F0F,  # Black with white insets
    "Blood Angels":           0xBA0C2F,  # Bright vibrant red
    "Blood Ravens":           0x9E1B1B,  # Blood red with bone pauldrons
    "Brazen Minotaurs":       0xB08D57,  # Bronze
    "Carcharodons":           0x8A8F94,  # Grey ceramite
    "Carmine Blades":         0xB31B1B,  # Carnelian red
    "Celestial Lions":        0xD4AF37,  # Gold
    "Crimson Fists":          0x1F3A5F,  # Dark blue
    "Dark Angels":            0x14342B,  # Caliban green
    "Exorcists":              0x7C1518,  # Deep red/black
    "Flesh Tearers":          0x6E1414,  # Dark crimson
    "Genesis Chapter":        0xB01B1B,  # Red
    "Hawk Lords":             0x6E4B8B,  # Purple
    "Howling Griffons":       0xC83A1E,  # Quartered red/yellow; red used as accent
    "Imperial Fists":         0xF4C20D,  # Yellow
    "Iron Hands":             0x0C0C0C,  # Black (bare-metal augmetics)
    "Iron Lords":             0x0B0B0B,  # Black with red thigh plates
    "Lamenters":              0xE3B505,  # Yellow/mustard
    "Marines Errant":         0x1D4E89,  # Halved blue/white; blue used as accent
    "Marines Malevolent":     0xF4C20D,  # Sunburst yellow
    "Mentors":                0x1F6B3F,  # Dark green (white arms/legs)
    "Minotaurs":              0xB08D57,  # Bronze
    "Necropolis Hawks":       0x5F7480,  # Blue-grey (official Ultima Founding)
    "Revilers":               0x4A4E54,  # Sooted gunmetal / terror-war iconography
    "Raptors":                0x4B5320,  # Dull olive/camo green
    "Raven Guard":            0x0A0A0A,  # Black
    "Red Scorpions":          0x3A3F44,  # Charcoal/dark grey
    "Red Templars":           0xB01B1B,  # Red
    "Salamanders":            0x0A6B3B,  # Bright dark green/emerald
    "Sable Knights":          0x5A0E14,  # Dark red with black accents
    "Scythes of the Emperor": 0xF2C200,  # Yellow
    "Sons of Medusa":         0x1A7A4C,  # Emerald green
    "Space Wolves":           0x6B8499,  # Blue-grey (The Fang)
    "Storm Giants":           0xC8B584,  # Tan/pale-yellow (official Codex: Armageddon)
    "Tome Keepers":           0xE6DBC0,  # Bone/page color
    "Ultramarines":           0x21437F,  # Macragge blue
    "White Templars":         0xF2F2F2,  # White plate with black heraldic accents
    "White Scars":            0xEDEDED,  # White
    "Wolfspear":              0x8A95A0,  # Pale slate grey
    # --- Homebrew chapters (best-guess from name/lineage) ---
    "Bleeding Hearts":        0x8B1A2F,  # Deep crimson (Blood Angels-style)
    "Cowled Wardens":         0x5A6066,  # Slate/stone grey
    "Dark Krakens":           0x16404D,  # Dark sea blue-teal
    "Death Exorcists":        0x1C1C1C,  # Near-black dark grey
    "Death Spectres":         0x2B2E33,  # Dark gunmetal (Raven Guard 13th Founding)
    "Dragonspears":           0x154A3A,  # Dark jade green
    "Epsilon Paladins":       0xB7BCC4,  # Steel silver-grey
    "Hospitallers":           0xECECEC,  # White (medical)
    "Imperius Reavers":       0xB8902F,  # Deep imperial gold
    "Iron Hounds":            0x4A4E54,  # Gunmetal grey
    "Iron Ravens":            0x2B2E33,  # Dark gunmetal/black
    "Iron Snakes":            0x2D6F78,  # Sea green (Ithaka oceanic heraldry)
    "Knights of the Raven":   0x14342B,  # Dark green (Dark Angels successor)
    "Tempestuous Angels":     0x3A6EA5,  # Storm blue
    "The Drakes":             0x1E5631,  # Dark green (fire/drake themed)
    "Tigers Argent":          0x8A8F98,  # Metallic silver-grey
}

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
