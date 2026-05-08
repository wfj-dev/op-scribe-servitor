#!/usr/bin/env python3
"""Refactoring script to split bot.py into domain modules."""

import re
import shutil

# Restore from backup
shutil.copy('bot.py.bak', 'bot.py')
print("Restored bot.py from backup")

with open('bot.py', 'r') as f:
    bot_lines = f.readlines()

total_lines = len(bot_lines)
print(f"bot.py has {total_lines} lines")

# ─────────────────────────────────────────────────────────────────────────────
# Text substitution helpers
# ─────────────────────────────────────────────────────────────────────────────

# Global variable substitutions to apply in ALL extracted code
GLOBAL_SUBS = [
    # Remove global declarations FIRST (before variable substitutions)
    (r'^(\s*)global\s+LAST_MILESTONE_CHECK_DATE\s*$', r'\1# (LAST_MILESTONE_CHECK_DATE accessed via _g)'),
    (r'^(\s*)global\s+MONTHLY_AUDIT_PENDING\s*$', r'\1# (MONTHLY_AUDIT_PENDING accessed via _g)'),
    (r'^(\s*)global\s+SHUTDOWN_INITIATED\s*$', r'\1# (SHUTDOWN_INITIATED accessed via _g)'),
    # Locks and shared state
    (r'\bRECONCILE_LOCK\b', '_g.RECONCILE_LOCK'),
    (r'\bRITES_LOCK\b', '_g.RITES_LOCK'),
    (r'\bMACHINE_SPIRITS_LOCK\b', '_g.MACHINE_SPIRITS_LOCK'),
    (r'\bROTATION_LOCK\b', '_g.ROTATION_LOCK'),
    (r'\bACTIVITY_STATUS_LOCK\b', '_g.ACTIVITY_STATUS_LOCK'),
    (r'\bPROMOTION_TRACKING_LOCK\b', '_g.PROMOTION_TRACKING_LOCK'),
    (r'\bARMOR_INTEGRITY_LOCK\b', '_g.ARMOR_INTEGRITY_LOCK'),
    (r'\bARMOR_SCAN_STATE_LOCK\b', '_g.ARMOR_SCAN_STATE_LOCK'),
    (r'\bINDUCTION_OVERRIDES_LOCK\b', '_g.INDUCTION_OVERRIDES_LOCK'),
    (r'\bCHALLENGE_PROGRESS_LOCK\b', '_g.CHALLENGE_PROGRESS_LOCK'),
    (r'\bBLESSING_POOL_LOCK\b', '_g.BLESSING_POOL_LOCK'),
    (r'\bFORGE_POOL_LOCK\b', '_g.FORGE_POOL_LOCK'),
    (r'\bFORGE_CHRONICLE_LOCK\b', '_g.FORGE_CHRONICLE_LOCK'),
    (r'\bLFG_QUEUE_LOCK\b', '_g.LFG_QUEUE_LOCK'),
    (r'\bLFG_ACTIVE_QUEUES\b', '_g.LFG_ACTIVE_QUEUES'),
    (r'\bMONTHLY_AUDIT_PENDING\b', '_g.MONTHLY_AUDIT_PENDING'),
    (r'\bSHUTDOWN_INITIATED\b', '_g.SHUTDOWN_INITIATED'),
    (r'\bLAST_MILESTONE_CHECK_DATE\b', '_g.LAST_MILESTONE_CHECK_DATE'),
    # CONFIG (not CONFIG_PATH)
    (r'\bCONFIG\b(?!_)', '_g.CONFIG'),
    # DATASTORE
    (r'\bDATASTORE\b', '_g.DATASTORE'),
    # logger
    (r'\blogger\b', '_g.logger'),
    # bot.* references
    (r'\bbot\.add_view\b', '_g.bot.add_view'),
    (r'@bot\.tree\.command\b', '@_g.bot.tree.command'),
    (r'\bbot\.guilds\b', '_g.bot.guilds'),
    # (global declarations handled at top of GLOBAL_SUBS)
    # Constants/functions that stay in bot.py - use _b() for runtime lookup
    (r'\bRANK_ROLES_PRIORITY\b', "_b('RANK_ROLES_PRIORITY')"),
    (r'\bHOME_CHAPTERS\b', "_b('HOME_CHAPTERS')"),
    (r'\bKILL_TEAMS\b', "_b('KILL_TEAMS')"),
    (r'\bCOMMAND_TEAMS\b', "_b('COMMAND_TEAMS')"),
    (r'\bCOMMAND_TEAM_ROLE_IDS\b', "_b('COMMAND_TEAM_ROLE_IDS')"),
    (r'\bget_highest_rank_index\b', "_b('get_highest_rank_index')"),
    (r'\b_canonical_role_names\b', "_b('_canonical_role_names')"),
    (r'\b_is_techmarine_or_forgemaster\b', "_b('_is_techmarine_or_forgemaster')"),
    (r'\b_find_responsible_attestor\b', "_b('_find_responsible_attestor')"),
    (r'\b_role_index\b', "_b('_role_index')"),
]

def apply_global_subs(code: str) -> str:
    for pattern, replacement in GLOBAL_SUBS:
        code = re.sub(pattern, replacement, code, flags=re.MULTILINE)
    return code

# ─────────────────────────────────────────────────────────────────────────────
# Module headers
# ─────────────────────────────────────────────────────────────────────────────

COMMON_IMPORTS = """\
import os
import asyncio
import json
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
import re
import itertools
from typing import Dict, List, Set, Tuple, Optional
import hashlib
import logging
import time
import random
import sys as _sys
import statistics

from .datastore import DataStore
from .constants import *  # noqa: F401,F403
from .flavor_text import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from .studs import *  # noqa: F401,F403
from . import _bot_globals as _g


def _b(name):
    \"\"\"Resolve name via bot module for test-mock compatibility.\"\"\"
    m = _sys.modules.get('bot')
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)

"""

FORGE_OPS_HEADER = '''\
"""Forge operations: armor integrity, blessing pool, forge pool, rites,
machine spirits, forge rite rendering, LFG, forge chronicle functions."""
''' + COMMON_IMPORTS

AAR_OPS_HEADER = '''\
"""AAR operations: parsing, validation, ingestion, reconciliation,
audit, challenge tracking."""
''' + COMMON_IMPORTS

ROSTER_OPS_HEADER = '''\
"""Roster operations: activity status, promotion milestones, deeds/stats,
combat bonds, milestone announcements, roster audit."""
''' + COMMON_IMPORTS

# ─────────────────────────────────────────────────────────────────────────────
# Extraction ranges  (1-indexed inclusive)
# ─────────────────────────────────────────────────────────────────────────────

FORGE_OPS_RANGES = [
    (3040, 5931),   # armor/rites/blessing/LFG helpers + LFGQueueView + LogToForgeView
    (7144, 10672),  # forge commands, forge_rite, armor_status, etc.
    (19919, 20389), # LFG commands (lfg_queue, lfg_close, lfg_join, lfg_leave)
]

AAR_OPS_RANGES = [
    (979, 1329),    # challenge tracking functions
    (10673, 12438), # reconcile_records and related commands
    (15017, 16268), # classify_difficulty, parse_aar, validate_aar, load_aar_data, etc.
]

ROSTER_OPS_RANGES = [
    (828, 978),     # activity status loading/saving
    (1330, 2593),   # induction overrides, company functions, _check_promotion_milestones
    (6690, 7143),   # litany_of_function, home chapter rotation
    (12439, 14867), # _forum_post_autocomplete, tally_deeds, my_deeds
    (14868, 15016), # combat_bonds command
    (16269, 17573), # compute_stats, bond functions, ToggleFormatView, _embed_from_ansi
    (17605, 18064), # BATTLE_LINE_ORDER etc., _compute_fortress_rankings
    (18065, 19918), # milestone functions, roster audit, promotion_queue, company_roster
]

# ─────────────────────────────────────────────────────────────────────────────
# Module-specific _b() substitutions (for test-mocked functions)
# ─────────────────────────────────────────────────────────────────────────────

FORGE_OPS_B_SUBS = [
    # armor integrity mocked functions ((?<!def ) avoids matching function definitions)
    (r'(?<!def )_get_member_damage_tier\(', "_b('_get_member_damage_tier')("),
    (r'(?<!def )_get_damage_penalty\(', "_b('_get_damage_penalty')("),
    (r'(?<!def )compute_stats_for_user\(', "_b('compute_stats_for_user')("),
    (r'(?<!def )_check_armor_grace_period\(', "_b('_check_armor_grace_period')("),
    (r'await _get_armor_state\(', "await _b('_get_armor_state')("),
    (r'await _run_armor_integrity_check\(', "await _b('_run_armor_integrity_check')("),
    (r'(?<!def )_roll_damage_tier\(', "_b('_roll_damage_tier')("),
    (r'await _apply_damage_tier\(', "await _b('_apply_damage_tier')("),
    (r'(?<!def )_roll_detection_alert\(', "_b('_roll_detection_alert')("),
    (r'await _set_armor_state\(', "await _b('_set_armor_state')("),
    # clear armor damage
    (r'(?<!def )_get_armor_damage_role_ids\(', "_b('_get_armor_damage_role_ids')("),
    # blessing pool mocked functions
    (r'await _get_techmarine_pool_state\(', "await _b('_get_techmarine_pool_state')("),
    (r'await _set_techmarine_pool_state\(', "await _b('_set_techmarine_pool_state')("),
    # LFG mocked functions
    (r'(?<!def )_load_lfg_queues\(', "_b('_load_lfg_queues')("),
    (r'(?<!def )_save_lfg_queues\(', "_b('_save_lfg_queues')("),
    (r'(?<!def )_resolve_notification_guild\(', "_b('_resolve_notification_guild')("),
    (r'(?<!def )_get_player_platform\(', "_b('_get_player_platform')("),
    (r'(?<!def )_get_lfg_queue_types\(', "_b('_get_lfg_queue_types')("),
    (r'(?<!def )_get_lfg_default_expiry_minutes\(', "_b('_get_lfg_default_expiry_minutes')("),
    (r'(?<!def )_get_lfg_max_expiry_minutes\(', "_b('_get_lfg_max_expiry_minutes')("),
    (r'(?<!def )_build_lfg_embed\(', "_b('_build_lfg_embed')("),
    (r'(?<!def )is_allowed_channel\(', "_b('is_allowed_channel')("),
    # scan state functions (tests mock bot._load_scan_state / bot._save_scan_state)
    (r'(?<!def )_load_scan_state\(\)', "_b('_load_scan_state')()"),
    (r'(?<!def )_save_scan_state\(', "_b('_save_scan_state')("),
    # forge chronicle functions (tests mock bot._load_forge_chronicle / bot._save_forge_chronicle)
    (r'(?<!def )_load_forge_chronicle\(\)', "_b('_load_forge_chronicle')()"),
    (r'(?<!def )_save_forge_chronicle\(', "_b('_save_forge_chronicle')("),
]

AAR_OPS_B_SUBS = [
    # load_aar_data is patched in test_induction.py
    (r'(?<!def )load_aar_data\(', "_b('load_aar_data')("),
    # audit_service_studs calls functions defined in roster_ops
    (r'(?<!def )_get_effective_induction_date\(', "_b('_get_effective_induction_date')("),
    (r'(?<!def )compute_stats_for_user\(', "_b('compute_stats_for_user')("),
]

ROSTER_OPS_B_SUBS = [
    # load_aar_data is patched in test_induction.py; roster_ops calls it too
    (r'(?<!def )load_aar_data\(', "_b('load_aar_data')("),
    # _resolve_notification_guild lives in bot.py, not roster_ops
    (r"(?<!def )(?<!_b\(')_resolve_notification_guild\(\)", "_b('_resolve_notification_guild')()"),
    # bare bot.fetch_channel -> _g.bot.fetch_channel
    (r'(?<!_g\.)(?<!\w)bot\.fetch_channel\(', '_g.bot.fetch_channel('),
]

# ─────────────────────────────────────────────────────────────────────────────
# Special fix for datetime in _expire_old_lfg_queues
# ─────────────────────────────────────────────────────────────────────────────

def apply_expire_lfg_datetime_fix(code: str) -> str:
    """Fix datetime calls in _expire_old_lfg_queues to use _b('datetime')."""
    code = code.replace(
        '        now = datetime.now(timezone.utc)\n',
        "        now = _b('datetime').now(timezone.utc)\n"
    )
    code = code.replace(
        '                    expires_at = datetime.fromisoformat(expires_at_str)\n',
        "                    expires_at = _b('datetime').fromisoformat(expires_at_str)\n"
    )
    return code

# ─────────────────────────────────────────────────────────────────────────────
# Extract and transform code
# ─────────────────────────────────────────────────────────────────────────────

def extract_module(ranges, header, b_subs=None):
    collected = []
    for start, end in ranges:
        collected.extend(bot_lines[start-1:end])
    code = ''.join(collected)
    code = apply_global_subs(code)
    if b_subs:
        for pattern, replacement in b_subs:
            code = re.sub(pattern, replacement, code)
    return header + code

# ─────────────────────────────────────────────────────────────────────────────
# Create module files
# ─────────────────────────────────────────────────────────────────────────────

print("Creating forge_ops.py...")
forge_code = extract_module(FORGE_OPS_RANGES, FORGE_OPS_HEADER, FORGE_OPS_B_SUBS)
forge_code = apply_expire_lfg_datetime_fix(forge_code)

# Replace stub pending alert functions with real implementations
forge_code = forge_code.replace(
    '''async def _store_pending_alert(user_id: int, message_id: int, channel_id: int):
    """Store a pending armor alert for thread reply tracking. (UNUSED - kept for schema compat)"""
    pass''',
    '''async def _store_pending_alert(user_id: int, message_id: int, channel_id: int):
    """Store a pending armor alert for thread reply tracking."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b('_load_forge_chronicle')()
        data.setdefault("pending_alerts", {})
        data["pending_alerts"][str(user_id)] = {
            "message_id": message_id,
            "channel_id": channel_id,
            "ts": datetime.utcnow().isoformat(),
        }
        _b('_save_forge_chronicle')(data)''',
)
forge_code = forge_code.replace(
    '''async def _get_pending_alert(user_id: int) -> Optional[dict]:
    """Get pending alert info for a user (if any). (UNUSED - kept for schema compat)"""
    return None''',
    '''async def _get_pending_alert(user_id: int) -> Optional[dict]:
    """Get pending alert info for a user (if any)."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b('_load_forge_chronicle')()
        return data.get("pending_alerts", {}).get(str(user_id))''',
)
forge_code = forge_code.replace(
    '''async def _clear_pending_alert(user_id: int):
    """Clear a pending alert. (UNUSED - kept for schema compat)"""
    pass''',
    '''async def _clear_pending_alert(user_id: int):
    """Clear a pending alert for a user (no-op if not stored)."""
    async with _g.FORGE_CHRONICLE_LOCK:
        data = _b('_load_forge_chronicle')()
        key = str(user_id)
        if key in data.get("pending_alerts", {}):
            data["pending_alerts"].pop(key)
            _b('_save_forge_chronicle')(data)''',
)

forge_code += '''

# ---------------------------------------------------------------------------
# Pure helper functions for forge_rite output
# ---------------------------------------------------------------------------

def _should_show_extended_blessing_fields(
    spirit_is_first: bool,
    spirit_is_reconsecrated: bool,
    spirit_is_returning: bool,
    spirit_is_restored: bool,
) -> bool:
    """Determine whether to show extended blessing embed fields.

    Returns True for first bindings and reconsecrated (reborn) spirits.
    Returns False for returning (routine maintenance) and restored spirits.
    """
    if spirit_is_first or spirit_is_reconsecrated:
        return True
    return False


def _get_compact_rite_status(
    blessing_roll_outcome: str,
    is_intensive: bool,
    armor_was_damaged: bool,
) -> tuple:
    """Return (icon, status_text) for the compact rite status line.

    Priority: crit_fail / crit_success beat intensive / damage flags.
    """
    if blessing_roll_outcome == "crit_fail":
        return ("\\u26a0\\ufe0f", "RESISTED")
    if blessing_roll_outcome == "crit_success":
        return ("\\u2728", "BLESSED *(grace)*")
    if is_intensive:
        return ("\\u2728", "RESTORED")
    if armor_was_damaged:
        return ("\\U0001f7e2", "REPAIRED")
    return ("\\U0001f7e2", "MAINTAINED")


def _get_thread_reply_text(
    spirit_is_reconsecrated: bool,
    blessing_roll_outcome: str,
    attester: str,
    machine_spirit_emoji: str,
    spirit_designation: str,
) -> str:
    """Return the short thread-reply text for a completed forge rite."""
    if spirit_is_reconsecrated:
        return (
            f"\\u2728 **Spirit Reborn** \\u2014 {machine_spirit_emoji} **{spirit_designation}** "
            f"has been reborn through the rites of the Omnissiah. "
            f"Consecrated by {attester}."
        )
    if blessing_roll_outcome == "crit_fail":
        return (
            f"\\u26a0\\ufe0f **Rite Resisted** \\u2014 {machine_spirit_emoji} **{spirit_designation}** "
            f"resisted the blessing. The spirit stirs but remains unquiet."
        )
    return (
        f"\\U0001f7e2 **Armor Restored** \\u2014 {machine_spirit_emoji} **{spirit_designation}** "
        f"has been tended by {attester}."
    )


# ---------------------------------------------------------------------------
# __all__: export all names needed by tests and bot.py re-imports.
# Must include underscore-prefixed names (Python's `import *` skips them
# by default; __all__ overrides that behaviour).
# ---------------------------------------------------------------------------

__all__ = [
    # ── Scan / detection ────────────────────────────────────────────────────
    "_roll_detection_alert",
    "_roll_scan_result",
    "_load_scan_state",
    "_save_scan_state",
    "_increment_aar_generation",
    "_get_aar_generation",
    "_get_or_roll_scan_result",
    "_purchase_intensive_scan",
    "_has_intensive_scan",
    # ── Armor state / damage ─────────────────────────────────────────────────
    "_get_armor_state",
    "_set_armor_state",
    "_save_armor_batch",
    "_get_armor_state_from_batch",
    "_set_armor_state_in_batch",
    "_get_armor_config",
    "_get_armor_probability_tiers",
    "_get_probability_tier_for_points",
    "_get_damage_probability",
    "_roll_damage_tier",
    "_run_armor_integrity_check",
    "_apply_damage_tier",
    "_clear_armor_damage",
    "_drop_armor_tier",
    "_get_member_damage_tier",
    "_get_damage_penalty",
    "_roll_armor_penalty",
    "_get_tier_risk_display",
    "_check_armor_grace_period",
    "_get_armor_status_for_blessing",
    "_get_armor_damage_role_ids",
    "_get_arming_chamber_channel_id",
    "_get_techmarine_role_id",
    "_get_armor_status_allowed_channels",
    "_calculate_armor_risk_score",
    "_show_armor_leaderboard",
    "_post_armor_alert",
    "_process_armor_integrity_for_aar",
    # ── Rites / machine spirits ──────────────────────────────────────────────
    "_load_rites",
    "_save_rites",
    "_get_user_rite",
    "_set_user_rite",
    "_load_machine_spirits",
    "_save_machine_spirits",
    "_get_machine_spirit",
    "_set_machine_spirit",
    "_delete_machine_spirit",
    # ── Blessing pool ────────────────────────────────────────────────────────
    "_check_recipient_cooldown",
    "_check_techmarine_can_bless",
    "_check_spirit_fracture",
    "_consume_blessing",
    "_get_intensive_charge_cost",
    "_get_techmarine_available_charges",
    "_consume_multiple_blessings",
    "_get_techmarine_pool_state",
    "_set_techmarine_pool_state",
    "_load_blessing_pool",
    "_save_blessing_pool",
    "_get_blessing_pool_display",
    "_filter_active_blessing_timestamps",
    "_calculate_regenerated_blessings",
    "_grant_blessing_charge",
    "_roll_blessing_outcome",
    "_apply_blessing_crit_fail",
    "_apply_blessing_normal",
    "_apply_blessing_crit_success",
    "_apply_blessing_intensive_normal",
    "_handle_intensive_scan_requisition",
    # ── Forge pool ───────────────────────────────────────────────────────────
    "_load_forge_pool",
    "_save_forge_pool",
    "_increment_forge_pool_balance",
    "_deduct_forge_pool_balance",
    "_get_forge_pool_available",
    "_consume_forge_requisition",
    "_get_techmarine_daily_requisitions",
    "_get_forge_pool_status",
    # ── Forge chronicle ──────────────────────────────────────────────────────
    "_load_forge_chronicle",
    "_save_forge_chronicle",
    "_build_forge_chronicle_embed",
    "_repost_chronicle_at_bottom",
    "_maybe_post_ambient_message",
    "_get_dashboard_message_id",
    "_set_dashboard_message_id",
    "_get_last_ambient_ts",
    "_set_last_ambient_ts",
    "_record_spirit_released",
    "_record_spirit_fractured",
    "_abbreviate_spirit",
    "_format_time_ago",
    # ── Pending alerts ───────────────────────────────────────────────────────
    "_store_pending_alert",
    "_get_pending_alert",
    "_clear_pending_alert",
    # ── Rite events / chronicle recording ───────────────────────────────────
    "_record_rite_in_chronicle",
    "_classify_forge_rite_event",
    "_should_show_extended_blessing_fields",
    "_get_compact_rite_status",
    "_get_thread_reply_text",
    # ── Forge rite helpers ───────────────────────────────────────────────────
    "_get_techmarine_acknowledgment_blended",
    "_blend_forgemaster_self_attestation",
    "_get_emoji_by_name",
    "_get_chapter_emoji",
    "_get_rank_emoji",
    "_get_rank_category_for_blend",
    "_blend_stud_flavor_by_rank",
    "_get_stud_marking_recipients",
    "_get_service_studs_announcement",
    "_get_oathsworn_announcement",
    "_get_member_rank_title",
    "_compute_member_service_studs",
    "_get_bearer_rank_and_title",
    "_get_bearer_home_chapter",
    "_find_company_or_chapter",
    "_format_cooldown_time",
    # ── LFG ─────────────────────────────────────────────────────────────────
    "_get_lfg_config",
    "_get_lfg_pc_role_id",
    "_get_lfg_console_role_id",
    "_get_lfg_default_expiry_minutes",
    "_get_lfg_max_expiry_minutes",
    "_get_lfg_queue_types",
    "_get_lfg_initiation_trial_role_id",
    "_load_lfg_queues",
    "_save_lfg_queues",
    "_get_player_platform",
    "_build_lfg_embed",
    "_restore_lfg_queue_views",
    "_expire_old_lfg_queues",
    "_lfg_queue_autocomplete",
    "LFGQueueView",
    "LogToForgeView",
    # ── Loops (tasks) ────────────────────────────────────────────────────────
    "_forge_ambient_loop",
    "_forge_dashboard_loop",
    "_lfg_queue_expiration_loop",
    # ── Public command functions ─────────────────────────────────────────────
    "lfg_queue",
    "lfg_close",
    "lfg_join",
    "lfg_leave",
    "_set_rite",
    "_attest",
    "_armor_status",
    "_requisition_supplies",
    "_forge_chronicle_cmd",
    "_preview_armor_alert",
    "_test_armor_alert",
    "_preview_stud_announcement",
]
'''

with open('forge_ops.py', 'w') as f:
    f.write(forge_code)
print(f"  forge_ops.py: {len(forge_code.splitlines())} lines")

print("Creating aar_ops.py...")
aar_code = extract_module(AAR_OPS_RANGES, AAR_OPS_HEADER, AAR_OPS_B_SUBS)
aar_code += '''

# ---------------------------------------------------------------------------
# __all__: export all names for `from aar_ops import *` re-export in bot.py
# ---------------------------------------------------------------------------

__all__ = [
    # ── Public helpers ───────────────────────────────────────────────────────
    "load_aar_data",
    "load_processed_ids",
    "add_processed_id",
    "save_aar_record",
    "has_been_processed",
    "parse_aar",
    "validate_aar",
    "classify_difficulty",
    "compute_points_for_op",
    "compute_armory_bonus_points",
    "compute_gene_seed_base_points_for_carrier",
    "get_user_ids_in_line",
    "is_aar_message",
    "log_aar_errors",
    "log_aar_error_with_meta",
    "summarize_error_authors",
    # ── Commands ─────────────────────────────────────────────────────────────
    "reconcile_records",
    "sanctify_battle_records",
    "audit_archive_discrepancies",
    "reparse_records",
    "record_of_blood",
    "cache_stats",
    "set_induction",
    "audit_service_studs",
    # ── Underscore helpers ───────────────────────────────────────────────────
    "_load_challenge_progress",
    "_save_challenge_progress",
    "_process_challenge_tracking",
    "_send_challenge_eligibility_notifications",
    "_reconciliation_core",
    "_run_ingest_new",
    "_run_recheck_errors",
    "_run_reparse_records",
    "_reply_aar_rejection",
    "_set_aar_reaction",
    "_load_json_dict",
    "_save_json_dict",
    "_load_json_list",
    "_save_json_list",
    "_snowflake_to_datetime",
    "_author_info_from_message",
]
'''

with open('aar_ops.py', 'w') as f:
    f.write(aar_code)
print(f"  aar_ops.py: {len(aar_code.splitlines())} lines")

print("Creating roster_ops.py...")
roster_code = extract_module(ROSTER_OPS_RANGES, ROSTER_OPS_HEADER, ROSTER_OPS_B_SUBS)
roster_code += '''

# ---------------------------------------------------------------------------
# __all__: export all names needed by tests and by bot.py references.
# Must include underscore-prefixed names explicitly.
# ---------------------------------------------------------------------------

__all__ = [
    # ── Activity status ──────────────────────────────────────────────────────
    "_load_activity_status",
    "_save_activity_status",
    "_load_member_last_post_times",
    "_save_member_last_post_times",
    "_load_activity_status_last_check",
    "_check_activity_status_changes",
    "_send_activity_status_notification",
    "_handle_dreadnought_inactivity",
    "_activity_status_check_loop",
    # ── Induction / member helpers ───────────────────────────────────────────
    "_load_induction_overrides",
    "_save_induction_overrides",
    "_get_effective_induction_date",
    "_get_member_company_name",
    "_extract_company_short_name",
    "_find_company_command_staff",
    "_find_kt_sergeant",
    "_find_all_captains_and_lieutenants",
    "_find_watch_master",
    "_get_member_display_name",
    "_get_member_rank_role",
    # ── Promotion milestones ─────────────────────────────────────────────────
    "_load_promotion_tracking",
    "_save_promotion_tracking",
    "_check_promotion_milestones",
    # ── Home chapter rotation ────────────────────────────────────────────────
    "_month_key_for_offset",
    "_load_home_chapter_rotation",
    "_save_home_chapter_rotation",
    "_get_saturdays_for_month",
    "_select_home_chapters_for_month",
    "ROTATION_STATE_PATH",
    # ── Deeds / stats ────────────────────────────────────────────────────────
    "_get_missions_last_days",
    "_get_eligible_combat_bonds_ids",
    "_filter_pair_counts_by_eligible",
    "_build_pair_counts",
    "_build_triple_bonds",
    "_build_group_bonds",
    "_build_spread_counts",
    "_select_top_global_bonds",
    "_select_personal_bonds",
    "_select_personal_pair_bonds",
    "_bond_tier",
    "_percentile",
    "_compute_bond_cutoffs",
    "_bond_tier_dynamic",
    "_resolve_home_chapters",
    "_format_bonds_for_discord",
    "_format_bonds_embed",
    "_format_personal_bonds_jericho_embed",
    "_embed_from_ansi",
    "_compute_fortress_rankings",
    "_parse_iso8601_to_utc",
    "_format_member_styled",
    "_format_imperial_date",
    "_forum_post_autocomplete",
    "_induction_count_for_user",
    "_count_inductions_from_records",
    # ── Milestone announcements ──────────────────────────────────────────────
    "_load_milestone_tracking",
    "_save_milestone_tracking",
    "_calculate_current_milestones",
    "_check_milestone_thresholds",
    "_get_milestone_display_info",
    "_build_milestone_embed",
    "_scheduled_milestone_check",
    # ── Roster audit ─────────────────────────────────────────────────────────
    "_extract_mentions_from_text",
    "_extract_role_mention_from_text",
    "_extract_position_label",
    "_find_roster_messages",
    "_parse_roster_section",
    "_parse_kill_teams_section",
    "_get_user_roles_by_id",
    "_validate_high_command_roles",
    "_validate_company_command_roles",
    "_validate_kill_team_member_roles",
    "_audit_company_roster",
    "_format_audit_summary",
    "_format_audit_full",
    "_parse_iso_ts_to_utc_naive",
    # ── Public names ─────────────────────────────────────────────────────────
    "HIGH_COMMAND_ROLES",
    "BATTLE_LINE_ORDER",
    "CHAMPION_ROLES",
    "SPECIALIST_ROLES",
    "POSITION_LABEL_MAP",
    "ToggleFormatView",
    # ── Public command functions ──────────────────────────────────────────────
    "litany_of_function",
    "pick_home_chapters",
    "tally_deeds",
    "my_deeds",
    "combat_bonds",
    "promotion_queue",
    "company_roster",
    # ── Public stats/data functions ───────────────────────────────────────────
    "compute_stats_for_user",
    "compute_stats_for_user_in_records",
]
'''

with open('roster_ops.py', 'w') as f:
    f.write(roster_code)
print(f"  roster_ops.py: {len(roster_code.splitlines())} lines")

# ─────────────────────────────────────────────────────────────────────────────
# Modify bot.py
# ─────────────────────────────────────────────────────────────────────────────

print("\nModifying bot.py...")

extracted_lines = set()
for start, end in FORGE_OPS_RANGES + AAR_OPS_RANGES + ROSTER_OPS_RANGES:
    for i in range(start, end+1):
        extracted_lines.add(i)

print(f"  Removing {len(extracted_lines)} lines from bot.py")

new_bot_lines = []
i = 1
while i <= total_lines:
    if i in extracted_lines:
        start = i
        while i <= total_lines and i in extracted_lines:
            i += 1
        end = i - 1
        # Determine which module
        module_name = 'extracted module'
        for ranges, name in [(FORGE_OPS_RANGES, 'forge_ops'), 
                              (AAR_OPS_RANGES, 'aar_ops'),
                              (ROSTER_OPS_RANGES, 'roster_ops')]:
            for r_start, r_end in ranges:
                if r_start <= start <= r_end:
                    module_name = name
                    break
            else:
                continue
            break
        new_bot_lines.append(f"# Lines {start}-{end} extracted to {module_name}.py\n")
    else:
        new_bot_lines.append(bot_lines[i-1])
        i += 1

# Find insertion point: after logger definition, before first extracted comment
insert_idx = None
for idx, line in enumerate(new_bot_lines):
    if 'logger = logging.getLogger' in line:
        insert_idx = idx + 1
        break

# Insert right before first extracted content placeholder
for idx, line in enumerate(new_bot_lines):
    if '# Lines' in line and 'extracted to' in line:
        insert_idx = idx
        break

setup_code = '''
# ─────────────────────────────────────────────────────────────────────────────
# Populate shared globals, then import extracted domain modules.
# Must come after all locks, CONFIG, and logger are defined above.
# ─────────────────────────────────────────────────────────────────────────────
import _bot_globals as _g
_g.bot = bot
_g.DATASTORE = DATASTORE
_g.CONFIG = CONFIG
_g.logger = logger
_g.RECONCILE_LOCK = RECONCILE_LOCK
_g.MONTHLY_AUDIT_PENDING = MONTHLY_AUDIT_PENDING
_g.RITES_LOCK = RITES_LOCK
_g.MACHINE_SPIRITS_LOCK = MACHINE_SPIRITS_LOCK
_g.ROTATION_LOCK = ROTATION_LOCK
_g.ACTIVITY_STATUS_LOCK = ACTIVITY_STATUS_LOCK
_g.PROMOTION_TRACKING_LOCK = PROMOTION_TRACKING_LOCK
_g.ARMOR_INTEGRITY_LOCK = ARMOR_INTEGRITY_LOCK
_g.ARMOR_SCAN_STATE_LOCK = ARMOR_SCAN_STATE_LOCK
_g.INDUCTION_OVERRIDES_LOCK = INDUCTION_OVERRIDES_LOCK
_g.CHALLENGE_PROGRESS_LOCK = CHALLENGE_PROGRESS_LOCK
_g.BLESSING_POOL_LOCK = BLESSING_POOL_LOCK
_g.FORGE_POOL_LOCK = FORGE_POOL_LOCK
_g.FORGE_CHRONICLE_LOCK = FORGE_CHRONICLE_LOCK
_g.LFG_QUEUE_LOCK = LFG_QUEUE_LOCK
_g.LFG_ACTIVE_QUEUES = LFG_ACTIVE_QUEUES
_g.SHUTDOWN_INITIATED = SHUTDOWN_INITIATED
_g.LAST_MILESTONE_CHECK_DATE = LAST_MILESTONE_CHECK_DATE

import forge_ops  # noqa: E402
import aar_ops    # noqa: E402
import roster_ops # noqa: E402

from forge_ops import *   # noqa: F401,F403
from aar_ops import *     # noqa: F401,F403
from roster_ops import *  # noqa: F401,F403

'''

new_bot_lines.insert(insert_idx, setup_code)
print(f"  Inserted module setup code at position {insert_idx+1}")

# Write the stripped bot.py first
with open('bot.py', 'w') as f:
    f.writelines(new_bot_lines)

# Post-process: add can_reconcile_records after is_high_command in bot.py
with open('bot.py', 'r') as f:
    bot_content = f.read()

can_reconcile = '''

def can_reconcile_records(user: "discord.User | discord.Member") -> bool:
    """Return True if the user may run reconcile_records commands.

    Requires Watch Techmarine (or Forgemaster / Watch Master).
    """
    user_roles = _canonical_role_names(user)
    return bool(user_roles & {"Watch Techmarine", "Forgemaster", "Watch Master"})
'''

if 'def can_reconcile_records' not in bot_content:
    # Insert before @bot.event decorator
    idx = bot_content.find('\n\n\n@bot.event')
    if idx == -1:
        idx = bot_content.find('\n@bot.event')
    if idx != -1:
        bot_content = bot_content[:idx] + can_reconcile + bot_content[idx:]
    with open('bot.py', 'w') as f:
        f.write(bot_content)
    print("  Added can_reconcile_records to bot.py")

print(f"  bot.py: {len(new_bot_lines)} lines")
print("\nDone!")
