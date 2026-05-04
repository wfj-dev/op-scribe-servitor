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
    (r'^(\s*)global\s+LAST_MILESTONE_CHECK_DATE\s*$', '\1# (LAST_MILESTONE_CHECK_DATE accessed via _g)'),
    (r'^(\s*)global\s+MONTHLY_AUDIT_PENDING\s*$', '\1# (MONTHLY_AUDIT_PENDING accessed via _g)'),
    (r'^(\s*)global\s+SHUTDOWN_INITIATED\s*$', '\1# (SHUTDOWN_INITIATED accessed via _g)'),
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

from datastore import DataStore
from constants import *  # noqa: F401,F403
from flavor_text import *  # noqa: F401,F403
from permissions import *  # noqa: F401,F403
from studs import *  # noqa: F401,F403
import _bot_globals as _g


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
]

AAR_OPS_B_SUBS = [
    # load_aar_data is patched in test_induction.py
    (r'(?<!def )load_aar_data\(', "_b('load_aar_data')("),
]

ROSTER_OPS_B_SUBS = [
    # No additional test-specific mocks needed for roster_ops
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
with open('forge_ops.py', 'w') as f:
    f.write(forge_code)
print(f"  forge_ops.py: {len(forge_code.splitlines())} lines")

print("Creating aar_ops.py...")
aar_code = extract_module(AAR_OPS_RANGES, AAR_OPS_HEADER, AAR_OPS_B_SUBS)
with open('aar_ops.py', 'w') as f:
    f.write(aar_code)
print(f"  aar_ops.py: {len(aar_code.splitlines())} lines")

print("Creating roster_ops.py...")
roster_code = extract_module(ROSTER_OPS_RANGES, ROSTER_OPS_HEADER, ROSTER_OPS_B_SUBS)
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

with open('bot.py', 'w') as f:
    f.writelines(new_bot_lines)

print(f"  bot.py: {len(new_bot_lines)} lines")
print("\nDone!")
