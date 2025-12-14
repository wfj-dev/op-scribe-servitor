#!/usr/bin/env python3

import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import itertools
from typing import Dict, List, Tuple, Optional
import hashlib
from collections import Counter

# Data file locations
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


ARCHETYPE_MOTIFS = {
    "angelic": {
        "adjectives": [
            "Crimson",
            "Sanguine",
            "Weeping",
            "Pale",
            "Golden",
            "Penitent",
            "Lamenting",
        ],
        "nouns": [
            "Chalice",
            "Host",
            "Wing",
            "Spear",
            "Lament",
            "Choir",
            "Passion",
        ],
    },
    "zealot": {
        "adjectives": [
            "Oathbound",
            "Sanctified",
            "Hallowed",
            "Unbroken",
            "Pure",
            "Vowed",
        ],
        "nouns": [
            "Crusade",
            "Vow",
            "Sepulcher",
            "Benediction",
            "Edict",
            "Reliquary",
        ],
    },
    "shadow": {
        "adjectives": [
            "Shrouded",
            "Hidden",
            "Veiled",
            "Umbral",
            "Obsidian",
            "Cloaked",
        ],
        "nouns": [
            "Veil",
            "Shroud",
            "Cipher",
            "Raven",
            "Watcher",
            "Ward",
        ],
    },
    "void": {
        "adjectives": [
            "Abyssal",
            "Spectral",
            "Voidborne",
            "Pale",
            "Drowned",
            "Black-Sun",
        ],
        "nouns": [
            "Abyss",
            "Maw",
            "Horizon",
            "Tide",
            "Kraken",
            "Depth",
            "Null-Spear",
        ],
    },
    "forge": {
        "adjectives": [
            "Tempered",
            "Molten",
            "Ferric",
            "Adamant",
            "Ember-lit",
            "Forged",
        ],
        "nouns": [
            "Anvil",
            "Hammer",
            "Forge",
            "Ember",
            "Crucible",
            "Pyre",
        ],
    },
    "bastion": {
        "adjectives": [
            "Resolute",
            "Exemplary",
            "Unyielding",
            "Aureate",
            "Vigilant",
        ],
        "nouns": [
            "Phalanx",
            "Wall",
            "Aegis",
            "Bulwark",
            "Standard",
            "Cohort",
        ],
    },
    "feral": {
        "adjectives": [
            "Fenrisian",
            "Howling",
            "Savage",
            "Blooded",
            "Winterborn",
        ],
        "nouns": [
            "Fang",
            "Maw",
            "Hunt",
            "Pack",
            "Prowl",
            "Totem",
        ],
    },
    "sky": {
        "adjectives": [
            "Soaring",
            "Gilded",
            "Ascendant",
            "Violet-Winged",
            "Gale-forged",
            "Highborn",
        ],
        "nouns": [
            "Talon",
            "Descent",
            "Gale",
            "Skyfall",
            "Heaven-Spear",
            "Wingblade",
        ],
    },
    "renegade": {
        "adjectives": [
            "Nameless",
            "Masked",
            "Forsaken",
            "Unmarked",
            "Exiled",
            "Broken-Edict",
        ],
        "nouns": [
            "Shard",
            "Mask",
            "Oblivion",
            "Remnant",
            "Fragment",
            "Ash-Vow",
        ],
    },
    "unknown": {
        "adjectives": ["Unknown", "Silent", "Redacted"],
        "nouns": ["Cohort", "Band", "Triad"],
    },
}

CHAPTER_ARCHETYPE: dict[str, str] = {
    # Angelic / Sanguinary
    "Blood Angels": "angelic",
    "Flesh Tearers": "angelic",
    "Flesh Eaters": "angelic",
    "Lamenters": "angelic",
    # Zealot / Crusader
    "Black Templars": "zealot",
    # Shadow / Secrets
    "Dark Angels": "shadow",
    "Raven Guard": "shadow",
    "Blood Ravens": "shadow",
    "Cowled Wardens": "shadow",
    # Void / Abyssal
    "Death Spectres": "void",
    "Dark Krakens": "void",
    "Storm Giants": "void",
    # Forge / Iron
    "Iron Hands": "forge",
    "Sons of Medusa": "forge",
    "Salamanders": "forge",
    # Bastion / Exemplars
    "Imperial Fists": "bastion",
    "Minotaurs": "bastion",
    "Ultramarines": "bastion",
    # Feral / Predatory
    "Space Wolves": "feral",
    # Sky / Aerial Assault
    "Hawk Lords": "sky",
    # Renegade / Oath-broken
    "Black Shields": "renegade",
}

ARCHETYPE_PRIORITY = [
    "angelic",
    "zealot",
    "shadow",
    "void",
    "forge",
    "bastion",
    "feral",
    "sky",
    "renegade",
    "unknown",
]


# Restrict commands to a specific channel (demo/training)
ALLOWED_COMMAND_CHANNELS = {
    # Update to your desired demo channel name
    "❖⋅data-vault⋅❖",
    "demo"
}


def is_allowed_channel(interaction: discord.Interaction):
    try:
        ch = interaction.channel
        name = getattr(ch, "name", None)
        return bool(name) and name in ALLOWED_COMMAND_CHANNELS
    except Exception:
        return False


# ===== Progress bar helper =====
def _print_progress(prefix: str, current: int, total: int, width: int = 40):
    try:
        if total <= 0:
            total = 1
        ratio = max(0.0, min(1.0, current / total))
        filled = int(ratio * width)
        bar = "#" * filled + "-" * (width - filled)
        print(
            f"\r{prefix} [{bar}] {current}/{total} ({ratio * 100:.1f}%)",
            end="",
            flush=True,
        )
        if current >= total:
            print("", flush=True)
    except Exception:
        # Avoid crashing on printing failures
        pass


# create a function is_watch_command(user: discord.User | discord.Member) which returns true if the user has a role named "Watch Command" or "Watch Master" or is the discord user "plzjules"
def is_watch_command(user: discord.User | discord.Member):
    if isinstance(user, discord.Member):
        for role in user.roles:
            if role.name in ("Watch Command", "Watch Master"):
                return True
    if str(user.nick) == "Watch Veteran Jules":  # plzjules
        return True
    return False


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # sync app_commands (slash commands)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(
    name="litany_of_function",
    description="Describe the duties of Jericho Logi-Scribe Servitor V-1.",
)
async def litany_of_function(interaction: discord.Interaction):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    litany_text = """```ansi
\u001b[32m===============================================
  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR
  JERICHO LOGI-SCRIBE SERVITOR V-1 — FUNCTION LITANY
===============================================
            ++ SECURE VOX-CHANNEL ESTABLISHED ++
Designation: Watch-Scribe Logi-Servitor V-1, “Operation-Scribe.”
Status: Active. Machine-spirit nominal. Awaiting Watch Command directives.

This servitor is bound by the Edict of Record-Keeping to serve the officers
of Watch Fortress Jericho. Unauthorized personnel will be disregarded.

# Recognized High-Authority Commands:

• /tally_deeds @Brother
Queries the Record of Deeds for the specified Watch Brother.
Returns: AAR Points, Apothecarion Gene-Seed Credit, Armory Data Tally,
and current service rank.

• /reconcile_records
Initiates a full archival sweep of the After-Action-Report vox-channel.
Reprocesses all recorded missions, amends the Record of Deeds,
and flags any corrupted or improperly formatted entries.

• /combat_bonds [@Brother] [window:N]
Analyzes recent missions (default last 50) to reveal Combat Bonds.
Without an argument: shows top 3 fortress-wide triads with no repeated Brothers.
With @Brother: shows that Brother’s strongest triads.

**Operational Restrictions:**
Only those bearing the mantle of Watch Command or Watch Master may issue
orders to this unit. All others shall be logged and ignored according to
Protocol Purity-Seventeen.

This servitor exists to record deeds, preserve honor, and maintain the
eternal ledger of the Long Watch.

# ++ END OF TRANSMISSION ++
\u001b[0m```"""
    await interaction.response.send_message(litany_text, ephemeral=True)


@bot.tree.command(
    name="reconcile_records", description="Reprocess AARs and update the archive."
)
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def reconcile_records(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    guild = interaction.guild
    aar_channel = discord.utils.get(guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭")
    if not aar_channel:
        await interaction.followup.send(
            "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++"
        )
        return

    ingested = 0
    rejected = 0
    fixed = 0
    still_broken = 0

    error_entries = _load_json_dict(AAR_ERRORS_PATH)

    # Phase A: re-check errors
    if len(error_entries) > 0:
        total_errs = len(error_entries)
        done_errs = 0
        for aar_id_str in list(error_entries.keys()):
            try:
                aar_id = int(aar_id_str)
            except ValueError:
                del error_entries[aar_id_str]
                continue
            if has_been_processed(aar_id):
                data = _load_json_dict(AAR_RECORDS_PATH)
                sid = str(aar_id)
                if sid in data:
                    del data[sid]
                    _save_json_dict(AAR_RECORDS_PATH, data)
                fixed += 1
                continue
            try:
                msg = await aar_channel.fetch_message(aar_id)
            except Exception:
                msg = None
            if not msg:
                log_aar_errors(
                    aar_id, ["Original message not found; cannot reprocess."]
                )
                still_broken += 1
                continue
            record = parse_aar(msg)
            if record is None:
                log_aar_error_with_meta(
                    aar_id,
                    [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                    msg,
                )
                await _set_aar_reaction(msg, "error")
                still_broken += 1
                continue
            errors = validate_aar(record)
            if errors:
                log_aar_error_with_meta(
                    aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg
                )
                await _set_aar_reaction(msg, "error")
                still_broken += 1
            else:
                save_aar_record(record)
                data = _load_json_dict(AAR_ERRORS_PATH)
                sid = str(aar_id)
                if sid in data:
                    del data[sid]
                    _save_json_dict(AAR_ERRORS_PATH, data)
                await _set_aar_reaction(msg, "ok")
                fixed += 1
            done_errs += 1
            # Update progress bar every 5 items or at the end
            if (done_errs % 5 == 0) or (done_errs == total_errs):
                _print_progress("Phase A (re-check errors)", done_errs, total_errs)

    # Phase B: ingest any new, unprocessed AARs (optimized single-pass)
    # If span_days is provided, limit scan to messages after that cutoff
    history_kwargs = {"limit": None}
    cutoff_dt = None
    if span_days and span_days > 0:
        cutoff_dt = datetime.utcnow() - timedelta(days=span_days)
        history_kwargs["after"] = cutoff_dt

    # Cursor optimization: get latest processed ID; when scanning full history,
    # stop once we encounter a processed message (assuming older ones are processed).
    processed_ids = load_processed_ids()
    latest_processed_id: Optional[int] = None
    try:
        if processed_ids:
            latest_processed_id = max(int(x) for x in processed_ids if str(x).isdigit())
    except Exception:
        latest_processed_id = None

    scanned = 0
    to_react_ok: list[discord.Message] = []
    to_react_err: list[discord.Message] = []
    async for msg in aar_channel.history(**history_kwargs):
        if not is_aar_message(msg):
            continue
        scanned += 1
        # Early break: when doing a full scan (no cutoff), stop once we hit a processed message,
        # assuming earlier history is already ingested.
        if cutoff_dt is None and latest_processed_id and msg.id <= latest_processed_id:
            _print_progress("Phase B (ingest new AARs)", scanned, scanned)
            break
        record = parse_aar(msg)
        if record is None:
            log_aar_error_with_meta(
                msg.id,
                [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                msg,
            )
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Phase B (ingest new AARs)", scanned, scanned)
            continue
        aar_id = record.get("aar_id", msg.id)
        if has_been_processed(aar_id):
            # If already processed, only re-save when content changed or edited timestamp differs
            existing = _load_json_dict(AAR_RECORDS_PATH).get(str(aar_id))
            existing_hash = (
                (existing or {}).get("content_hash")
                if isinstance(existing, dict)
                else None
            )
            existing_edited = (
                (existing or {}).get("edited_at")
                if isinstance(existing, dict)
                else None
            )
            msg_hash = record.get("content_hash")
            msg_edited = record.get("edited_at")
            needs_update = (msg_hash and msg_hash != existing_hash) or (
                msg_edited and msg_edited != existing_edited
            )
            if not needs_update:
                if scanned % 10 == 0:
                    _print_progress("Phase B (ingest new AARs)", scanned, scanned)
                continue
        errors = validate_aar(record)
        if errors:
            log_aar_error_with_meta(aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg)
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Phase B (ingest new AARs)", scanned, scanned)
            continue
        save_aar_record(record)
        to_react_ok.append(msg)
        ingested += 1
        if scanned % 10 == 0:
            _print_progress("Phase B (ingest new AARs)", scanned, scanned)

        # Batch reactions to reduce API calls
        if len(to_react_ok) + len(to_react_err) >= 25:
            for m in to_react_ok:
                await _set_aar_reaction(m, "ok")
            for m in to_react_err:
                await _set_aar_reaction(m, "error")
            to_react_ok.clear()
            to_react_err.clear()

    # Flush any remaining reactions
    if to_react_ok or to_react_err:
        for m in to_react_ok:
            await _set_aar_reaction(m, "ok")
        for m in to_react_err:
            await _set_aar_reaction(m, "error")

    remaining_errors = _load_json_dict(AAR_ERRORS_PATH)
    still_broken = len(remaining_errors)

    author_summaries = summarize_error_authors()
    author_lines = []
    for a in author_summaries:
        label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
        author_lines.append(f"- {label}: {a['count']}")

    report_header = (
        "```ansi\n"
        "\u001b[32m===============================================\n"
        "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
        "  OPERATION-SCRIBE SERVITOR — RECONCILIATION RITE\n"
        "===============================================\n"
        "  ++ LITANY OF RECONCILIATION COMPLETE ++\n"
    )

    report_header += (
        f"  Scan Window: Last {span_days} day(s)\n"
        if span_days
        else "  Scan Window: Full history\n"
    )

    report = (
        report_header
        + f"  Sanctioned Operational Records: {ingested}\n"
        + f"  Logs Judged Corrupted or Unworthy: {rejected}\n"
        + f"  Restored Entries Returned to the Annals: {fixed}\n"
        + f"  Faulted Reports Under Quarantine: {still_broken}\n"
    )

    if author_lines:
        report += "-----------------------------------------------\n"
        report += "Entries Rejected Due to Authorial Deviation:\n"
        for line in author_lines:
            report += f"  {line}\n"

    report += "==============================================================================\n"
    report += "  Machine-Spirit Addendum:\n"
    report += "  These Records are logged for future deployment rites\n"
    report += "  and may be invoked by decree of Watch Command alone.\n"
    report += "==============================================================================\n"
    report += "\u001b[0m```"

    await interaction.followup.send(report, ephemeral=True)


@bot.tree.command(
    name="tally_deeds", description="Display the Deeds Ledger for a Brother."
)
@app_commands.describe(brother="The Watch Brother to query.")
async def tally_deeds(interaction: discord.Interaction, brother: discord.Member):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # First response: defer, so we can do slower work safely
    await interaction.response.defer(thinking=False, ephemeral=True)

    target = brother
    stats = compute_stats_for_user(str(target.id))

    rank_roles_priority = [
        "Watch Master",
        "Venerable",
        "Lord Executioner",
        "Reclusiarch",
        "Forgemaster",
        "Chief Apothecary",
        "Void Warden",
        "Watch Captain",
        "Watch Lieutenant",
        "Watch Chaplain",
        "Watch Techmarine",
        "Watch Apothecary",
        "Watch Librarian",
        "Watch Champion",
        "Watch Sergeant",
        "Kill Team Champion",
        "Watch Veteran",
        "Watch Brother",
    ]
    current_rank = "Unknown"
    for rank in rank_roles_priority:
        for role in target.roles:
            if role.name == rank:
                current_rank = rank
                break
        if current_rank != "Unknown":
            break

    display_name = target.nick or target.display_name

    # Member join date (server join time); fallback to 'Unknown' if unavailable
    try:
        joined_at = getattr(target, "joined_at", None)
        joined_str = (
            joined_at.strftime("%Y-%m-%d %H:%M UTC") if joined_at else "Unknown"
        )
    except Exception:
        joined_str = "Unknown"

    data = load_aar_data(AAR_RECORDS_PATH)
    trials_raw = sum(
        1
        for rec in data.values()
        if str(target.id) in (rec.get("brother_ids") or [])
        and bool(rec.get("initiation_trial"))
    )
    trials_reported = max(1, trials_raw - 1)

    reply_text = (
        "```ansi\n"
        "\u001b[32m==============================================================================\n"
        "  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE\n"
        "  OPERATION-SCRIBE SERVITOR — DEEDS LEDGER\n"
        "==============================================================================\n"
        f"  Tally for: {display_name}\n"
        "------------------------------------------------------------------------------\n"
        f"  Current Rank: {current_rank}\n"
        f"  Joined Watch Fortress Jericho: {joined_str}\n"
        f"  Brothers Sanctioned: {trials_reported}\n"
        f"  AAR Commendation Points: {stats['aar_points']}\n"
        f"  Gene-seed Retrieval Points: {stats['gene_seed_points']}\n"
        f"  Armory Data Acquisition Points: {stats['armory_points']}\n"
        "==============================================================================\n"
        "  Machine-Spirit Addendum:\n"
        "  These Deeds are logged for future deployment rites\n"
        "  and may be invoked by decree of Watch Command alone.\n"
        "==============================================================================\n"
        "\u001b[0m```"
    )

    # After defer, always use followup
    await interaction.followup.send(reply_text, ephemeral=True)


@bot.tree.command(
    name="combat_bonds", description="Show top Combat Bonds (global or for a Brother)."
)
@app_commands.describe(
    brother="Optional: limit to bonds including this Brother.",
    window="Optional: number of most recent missions to consider.",
)
async def combat_bonds(
    interaction: discord.Interaction,
    brother: Optional[discord.Member] = None,
    window: Optional[int] = None,
):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # No defer: send a direct response to clear the interaction state

    span = window if (isinstance(window, int) and window > 0) else 50
    missions = _get_recent_missions(limit=span)
    # Collect all brothers seen in window
    all_bros: List[str] = []
    for rec in missions:
        all_bros.extend([str(b) for b in (rec.get("brother_ids") or [])])
    all_bros = sorted(set(all_bros))

    pair_counts = _build_pair_counts(missions)
    triples = _build_triple_bonds(pair_counts, all_bros)

    if brother is None:
        top_global = _select_top_global_bonds(triples, top_n=3)
        # Resolve chapters for all user IDs appearing in selected bonds
        uids: List[str] = []
        for tri, _score in top_global:
            uids.extend(list(tri))
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(uids)))
        text = _format_bonds_for_discord(
            top_global, interaction.guild, window_span=span, chapters=chapters
        )
        await interaction.response.send_message(text, ephemeral=True)
    else:
        target_id = str(brother.id)
        personal = _select_personal_bonds(triples, target_id, max_n=3)
        uids: List[str] = []
        for tri, _score in personal:
            uids.extend(list(tri))
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(uids)))
        text = _format_bonds_for_discord(
            personal, interaction.guild, window_span=span, chapters=chapters
        )
        await interaction.response.send_message(text, ephemeral=True)


def classify_difficulty(difficulty: str | None):
    if not difficulty:
        return None

    lower = difficulty.lower()

    if "ruthless" in lower:
        return "ruthless_ops"
    if "lethal" in lower:
        return "lethal_ops"
    if "absolute" in lower:
        return "absolute_ops"
    if "normal-stratagem" in lower:
        return "normal_stratagem"
    if "hard-stratagem" in lower:
        return "hard_stratagem"
    if "normal-siege" in lower:
        return "normal_siege"
    if "hard-siege" in lower:
        return "hard_siege"
    return None


def compute_points_for_op(difficulty_class: str | None, waves: int | None):
    if not difficulty_class:
        return 0

    if difficulty_class == "ruthless_ops":
        return 2
    if difficulty_class == "lethal_ops":
        return 3
    if difficulty_class == "absolute_ops":
        return 4
    if difficulty_class == "normal_stratagem":
        return 2
    if difficulty_class == "hard_stratagem":
        return 5
    if difficulty_class == "normal_siege":
        if waves is None:
            return 0
        return 3 * (waves // 5)
    if difficulty_class == "hard_siege":
        if waves is None:
            return 0
        return 4 * (waves // 5)

    return 0


def compute_gene_seed_base_points_for_carrier(difficulty_class: str | None):
    if not difficulty_class:
        return 0
    if difficulty_class == "ruthless_ops" or difficulty_class == "normal_stratagem":
        return 2
    if difficulty_class == "lethal_ops":
        return 3
    if difficulty_class == "absolute_ops":
        return 4
    if difficulty_class == "hard_stratagem":
        return 5
    if difficulty_class in ("normal_siege", "hard_siege"):
        return 0
    return 0


def compute_armory_bonus_points(difficulty_class: str | None, armory_data: int | None):
    if not difficulty_class or armory_data is None:
        return 0

    if difficulty_class == "normal_siege" or difficulty_class == "lethal_ops":
        return armory_data * 1
    elif difficulty_class == "hard_siege" or difficulty_class == "absolute_ops":
        return armory_data * 2
    elif difficulty_class == "hard_stratagem":
        return armory_data * 3

    return 0


def is_aar_message(message: discord.Message):
    content = message.content
    # Treat presence of the start marker as sufficient; END marker optional
    return "++ MISSION REPORT ++" in content or "++MISSION REPORT++" in content


def get_user_ids_in_line(line: str, message: discord.Message):
    """Return list of user IDs whose mention appears in this line."""
    ids = []
    for user in message.mentions:
        patterns = (f"<@{user.id}>", f"<@!{user.id}>")
        if any(p in line for p in patterns):
            ids.append(str(user.id))
    return ids


def parse_aar(message: discord.Message):
    content = message.content
    aar_id = message.id
    lines = content.splitlines()

    mission = None
    difficulty = None
    # Deprecated fields removed from persistence
    armory_data = 0
    gene_seed_status = "unknown"
    gene_seed_carrier_id = None
    gene_seed_carried_name = None
    brothers_ids = []
    brother_names = []
    waves = 0
    # Initiation Trial mention flag (lightweight)
    initiation_trial = False

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
            # Also detect Initiation Trial tokens on the mission line
            # Deprecated: no longer tracking initiation trial state here
        elif lower.startswith("difficulty:") or lower.startswith("threat:"):
            after_colon = line.split(":", 1)[1]
            for role in message.role_mentions:
                mention = f"<@&{role.id}>"
                after_colon = after_colon.replace(mention, role.name)
            difficulty = after_colon.strip()
            # No longer persisting difficulty_tags or black_laurels_active

        # Armory / Armoury Data in any order, any capitalization
        elif ("armory" in lower or "armoury" in lower) and "data" in lower:
            # e.g. "Armory Data: 3" or "Armory data: 3"
            parts = line.split(":", 1)
            try:
                armory_data = int(parts[1].strip()) if len(parts) > 1 else 0
            except ValueError:
                print(f"Failed to parse armory data from line: {line}")
                armory_data = 0

        # Gene-Seed / Geneseed: lost / carried by @Brother
        elif ("gene-seed" in lower) or ("geneseed" in lower):
            parts = line.split(":", 1)
            rest = parts[1].strip() if len(parts) > 1 else ""
            rest_lower = rest.lower()

            if "lost" in rest_lower:
                gene_seed_status = "lost"
            elif "carried" in rest_lower:
                gene_seed_status = "carried"

            ids_here = get_user_ids_in_line(raw_line, message)
            if ids_here:
                gene_seed_carrier_id = ids_here[0]
                # Copilot: also set gene_seed_carried_name to the Discord nickname of the carrier
                for user in message.mentions:
                    if str(user.id) == gene_seed_carrier_id:
                        try:
                            gene_seed_carried_name = user.nick
                        except AttributeError:
                            print(
                                f"Failed to get nickname for user ID {gene_seed_carrier_id}"
                            )
                # If a Brother is tagged here, treat as carried regardless of wording
                gene_seed_status = "carried"

        for role in message.role_mentions:
            if role.name == "Initiation Trial":
                initiation_trial = True

        # Watch Command marker sometimes present on trial templates (deprecated persistence)
        if "watch command" in lower:
            # Deprecated: ignore trial template markers for persistence
            pass

        elif lower.startswith("brothers") or lower.startswith("team"):
            # Brothers/Team can appear on the same line as the header; include this line
            brothers_start_idx = i

        elif lower.startswith("waves:") or lower.startswith("wave:"):
            parts = line.split(":", 1)
            try:
                waves = int(parts[1].strip())
            except Exception:
                waves = None

    difficulty_class = classify_difficulty(difficulty)
    points_for_op = compute_points_for_op(difficulty_class, waves)
    gene_seed_base_points_for_carrier = 0
    if gene_seed_status == "carried":
        gene_seed_base_points_for_carrier = compute_gene_seed_base_points_for_carrier(
            difficulty_class
        )

    # Collect Brothers from the "Brothers:" line and subsequent lines until END OF REPORT
    if brothers_start_idx is not None:
        for raw_line in lines[brothers_start_idx:]:
            line = raw_line.strip()
            if "++ end of report ++" in line.lower():
                break
            if not line:
                continue

            ids_here = get_user_ids_in_line(raw_line, message)
            for uid in ids_here:
                if uid not in brothers_ids:
                    brothers_ids.append(uid)
                    # Copilot: also append brother names as represented in discord
                    for user in message.mentions:
                        if str(user.id) == uid:
                            try:
                                brother_names.append(user.nick)
                            except AttributeError:
                                print(
                                    f"Failed to get nickname for user/ID {user.name}\/{uid}"
                                )

    # Always return a record, even if Brothers section is missing; validation will handle errors
    return {
        "aar_id": aar_id,
        "mission": mission,
        "difficulty": difficulty,
        "difficulty_class": difficulty_class,
        # deprecated: removed from persisted record
        "armory_data": armory_data,
        "armory_challenge_points": compute_armory_bonus_points(
            difficulty_class, armory_data
        ),
        "gene_seed_status": gene_seed_status,
        "gene_seed_carrier_id": gene_seed_carrier_id,
        "gene_seed_carried_name": gene_seed_carried_name,
        "gene_seed_base_points_for_carrier": gene_seed_base_points_for_carrier,
        "brother_ids": brothers_ids,
        "brother_names": brother_names,
        "waves": waves,
        "points_for_op": points_for_op,
        "timestamp": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat()
        if getattr(message, "edited_at", None)
        else None,
        "content_hash": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        "initiation_trial": initiation_trial,
    }


def validate_aar(record: dict):
    """
    Validate a parsed AAR record.
            "initiation_trial_tag_in_mission": initiation_trial_tag_in_mission,
            "initiation_trial_line_present": initiation_trial_line_present,
    Returns a list of human-readable error messages.
    If the list is empty, the record is considered valid.
    """
    errors: list[str] = []

    mission = record.get("mission")
    difficulty = record.get("difficulty") or ""
    waves = record.get("waves")
    armory_data = record.get("armory_data")
    brothers = record.get("brother_ids") or []
    gene_status = record.get("gene_seed_status")
    gene_carrier = record.get("gene_seed_carrier_id")

    # 1) Mission required (except Siege templates where Mission may be omitted)
    dlower = (record.get("difficulty") or "").lower()
    is_siege = ("normal-siege" in dlower) or ("hard-siege" in dlower)
    if not mission and not is_siege:
        errors.append("Mission is missing (line starting with 'Mission:').")
    elif mission:
        mstr = str(mission)
        # Reject any user or role mentions or trial-style tokens in Mission
        # if "<@&" in mstr or "<@" in mstr:
        #     errors.append("Mission must be plain text; no Discord mentions are allowed after 'Mission:'.")
        if "/" in mstr:
            errors.append(
                "Mission must not include trial-style progress tokens like 'n/m' or '-/m'."
            )

    # 2) Difficulty must be one of the known tags
    dlower = difficulty.lower()
    known_tags = [
        "ruthless",
        "lethal",
        "absolute",
        "normal-stratagem",
        "hard-stratagem",
        "normal-siege",
        "hard-siege",
    ]
    if not difficulty or not any(tag in dlower for tag in known_tags):
        errors.append(
            "Difficulty is missing or does not contain a known tag "
            "(@Ruthless, @Lethal, @Absolute, @Normal-Stratagem, "
            "@Hard-Stratagem, @Normal-Siege, @Hard-Siege)."
        )
    else:
        # Only allow Black Laurels on Difficulty when Absolute is present
        has_black_laurels = "black" in dlower and "laurel" in dlower
        has_absolute = "absolute" in dlower
        if has_black_laurels and not has_absolute:
            errors.append(
                "@Black_Laurels may only be present when @Absolute is selected on the Difficulty line."
            )

    # 3) Siege must have valid Waves: line (any integer allowed; scoring floors to multiple of 5)
    if "normal-siege" in dlower or "hard-siege" in dlower:
        if waves is None:
            errors.append("Siege difficulty requires a 'Waves:' line.")
        else:
            try:
                int(waves)
            except (TypeError, ValueError):
                errors.append("Waves value could not be parsed as an integer.")

    # 4) Armory/Armoury Data required and numeric
    if armory_data is None:
        errors.append("Armory/Armoury Data line is missing.")
    else:
        try:
            int(armory_data)
        except ValueError:
            errors.append("Armory/Armoury Data must be an integer (e.g. 3).")

    # 5) At least two Brothers
    if len(brothers) < 2:
        errors.append(
            "At least two Brothers must be listed under the 'Brothers:' section."
        )

    # 6) Initiation Trial placement rules
    if record.get("initiation_trial_active"):
        if record.get("initiation_trial_tag_in_mission"):
            errors.append(
                "Initiation Trial tag must be on its own line (e.g., '@Initiation Trial: n/m'), not inside 'Mission:'."
            )
        if not record.get("initiation_trial_line_present"):
            errors.append(
                "Provide a dedicated '@Initiation Trial: n/m' line; do not rely on Mission text alone."
            )
        if not record.get("initiation_trial_watch_command"):
            errors.append(
                "Trial template requires '@Watch Command' marker. Please include it."
            )

    # 7) Gene-seed logic
    allowed_statuses = {"lost", "carried", "unknown"}
    if gene_status not in allowed_statuses:
        errors.append(
            "Gene-Seed status must be 'lost', 'carried', or omitted "
            "(which becomes 'unknown')."
        )

    if gene_status == "carried":
        if gene_carrier is None:
            errors.append("Gene-Seed is 'carried' but no carrier is mentioned.")
        elif gene_carrier not in brothers:
            errors.append("Gene-Seed carrier must also be listed under 'Brothers:'.")

    return errors


def load_aar_data(filename: str):
    try:
        with open(filename, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        # Handle empty or malformed JSON file gracefully
        return {}


def _load_json_dict(path: str):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _save_json_dict(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_json_list(path: str):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


def _save_json_list(path: str, data: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def log_aar_errors(aar_id: int, errors: list[str]):
    data = _load_json_dict(AAR_ERRORS_PATH)
    data[str(aar_id)] = {"errors": errors}
    _save_json_dict(AAR_ERRORS_PATH, data)


def _author_info_from_message(msg: discord.Message):
    author = msg.author
    info = {
        "id": str(getattr(author, "id", "")),
        "username": getattr(author, "name", None) or getattr(author, "username", None),
        "nickname": getattr(author, "nick", None),
    }
    # Some guild member objects expose display_name for nickname
    try:
        if hasattr(author, "display_name") and info["nickname"] is None:
            info["nickname"] = author.display_name
    except Exception:
        pass
    return info


def log_aar_error_with_meta(aar_id: int, errors: list[str], msg: discord.Message):
    data = _load_json_dict(AAR_ERRORS_PATH)
    entry = {
        "errors": errors,
        "author": _author_info_from_message(msg),
    }
    data[str(aar_id)] = entry
    _save_json_dict(AAR_ERRORS_PATH, data)


def summarize_error_authors():
    """Return a list of author summaries from the error log.
    Each entry: {"id": str, "username": str|None, "nickname": str|None, "count": int}
    """
    data = _load_json_dict(AAR_ERRORS_PATH)
    by_author: dict[str, dict] = {}
    for _aar_id, entry in data.items():
        author = entry.get("author", {})
        aid = str(author.get("id", ""))
        if not aid:
            # Bucket unknown authors under empty id
            aid = ""
        if aid not in by_author:
            by_author[aid] = {
                "id": aid,
                "username": author.get("username"),
                "nickname": author.get("nickname"),
                "count": 0,
            }
        by_author[aid]["count"] += 1
        # Prefer latest known nickname/username if missing
        if not by_author[aid]["nickname"] and author.get("nickname"):
            by_author[aid]["nickname"] = author.get("nickname")
        if not by_author[aid]["username"] and author.get("username"):
            by_author[aid]["username"] = author.get("username")

    # Sort by count desc, then nickname/username
    summaries = list(by_author.values())
    summaries.sort(
        key=lambda x: (-x["count"], (x["nickname"] or x["username"] or "").lower())
    )
    return summaries


async def _set_aar_reaction(msg: discord.Message, status: str):
    """Set a single reaction on an AAR message based on status.
    status: 'ok' -> ✅, 'error' -> 🚫
    Ensures only one of these two reactions remains (no stacking).
    """
    ok_emoji = "✅"
    err_emoji = "🚫"
    try:
        # Remove previous bot-added status reactions to avoid stacking
        for reaction in msg.reactions:
            if str(reaction.emoji) in (ok_emoji, err_emoji):
                async for user in reaction.users():
                    if user == msg.guild.me:
                        await reaction.remove(user)
        # Add the desired reaction
        if status == "ok":
            await msg.add_reaction(ok_emoji)
        elif status == "error":
            await msg.add_reaction(err_emoji)
    except Exception as e:
        print(f"Failed to set reaction on message {msg.id}: {e}")


def load_processed_ids():
    ids = _load_json_list(PROCESSED_IDS_PATH)
    return set(str(x) for x in ids)


def add_processed_id(aar_id: int):
    ids = _load_json_list(PROCESSED_IDS_PATH)
    sid = str(aar_id)
    if sid not in ids:
        ids.append(sid)
        _save_json_list(PROCESSED_IDS_PATH, ids)


def save_aar_record(record: dict):
    filename = AAR_RECORDS_PATH
    data = load_aar_data(filename)

    key = str(record["aar_id"])
    data[key] = record

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

    # Mark as processed after successful save
    add_processed_id(record["aar_id"])

    # print(f"Saved AAR {record['aar_id']} to {filename}.")


def has_been_processed(aar_id: int):
    processed = load_processed_ids()
    return str(aar_id) in processed


# def print_aar_summary(record: dict):
#     print("AAR Summary:")
#     print(f"  AAR ID: {record['aar_id']}")
#     print(f"  Mission: {record['mission']}")
#     print(f"  Difficulty: {record['difficulty']}")
#     print(f"  Armory Data: {record['armory_data']}")
#     print(f"  Gene-Seed Status: {record['gene_seed_status']}")
#     print(f"  Gene-Seed Carrier ID: {record.get('gene_seed_carrier_name')}")
#     print(f"  Brothers (user IDs): {', '.join(record['brother_names'])}")


def compute_stats_for_user(user_id: str):
    data = load_aar_data(AAR_RECORDS_PATH)

    ops = 0
    aar_points = 0
    armory_raw = 0
    armory_points = 0
    gene_carries = 0
    gene_seed_points = 0

    for record in data.values():
        brother_ids = record.get("brother_ids", [])
        if user_id in brother_ids:
            ops += 1
            aar_points += record.get("points_for_op", 0)
            armory_data = record.get("armory_data")
            try:
                armory_raw += int(armory_data) if armory_data is not None else 0
            except ValueError:
                armory_raw += 0
            armory_points += record.get("armory_challenge_points", 0)

        # Treat as carried if status is 'carried' OR a carrier is named and status is not 'lost'
        status = (record.get("gene_seed_status") or "").lower()
        gene_carrier = record.get("gene_seed_carrier_id")
        effective_carried = status == "carried" or (
            gene_carrier is not None and status != "lost"
        )

        if effective_carried:
            if gene_carrier == user_id:
                gene_carries += 1
                gene_seed_points += record.get("gene_seed_base_points_for_carrier", 0)
            elif user_id in brother_ids:
                gene_seed_points += 1  # assist

    return {
        "ops": ops,
        "aar_points": aar_points,
        "armory_raw": armory_raw,
        "armory_points": armory_points,
        "gene_carries": gene_carries,
        "gene_seed_points": gene_seed_points,
    }


# ===== Combat Bonds helpers =====
def _get_recent_missions(limit: int = 50):
    """Return the most recent missions (AAR records) sorted by timestamp desc."""
    data = load_aar_data(AAR_RECORDS_PATH)
    records = list(data.values())

    def _parse_ts(r: dict):
        ts = r.get("timestamp")
        try:
            return datetime.fromisoformat(ts).timestamp() if ts else 0.0
        except Exception:
            return 0.0

    records.sort(key=_parse_ts, reverse=True)
    return records[:limit]


def _build_pair_counts(missions):
    """Count how often each pair of brothers appears together in provided missions.
    Keys are sorted tuples of brother IDs (str, str).
    """
    pair_counts: Dict[Tuple[str, str], int] = {}
    for rec in missions:
        bros: List[str] = [str(b) for b in (rec.get("brother_ids") or [])]
        # unique per mission to avoid duplicate counting same brother twice
        unique_bros = sorted(set(bros))
        for a, b in itertools.combinations(unique_bros, 2):
            key = (a, b) if a < b else (b, a)
            pair_counts[key] = pair_counts.get(key, 0) + 1
    return pair_counts


def _build_triple_bonds(pair_counts: Dict[Tuple[str, str], int], brothers: List[str]):
    """Create 3-brother bonds and score them as sum of the three pairwise counts.
    Skip any triple where a pair never appeared together (pair count == 0).
    Returns list of ((id1,id2,id3), score) sorted by score desc.
    """
    triples: List[Tuple[Tuple[str, str, str], int]] = []
    uniq_bros = sorted(set(brothers))
    for x, y, z in itertools.combinations(uniq_bros, 3):
        pairs = [tuple(sorted((x, y))), tuple(sorted((x, z))), tuple(sorted((y, z)))]
        # all pairs must exist at least once
        if any(pair_counts.get(p, 0) <= 0 for p in pairs):
            continue
        score = sum(pair_counts.get(p, 0) for p in pairs)
        triples.append(((x, y, z), score))
    triples.sort(key=lambda t: t[1], reverse=True)
    return triples


def _select_top_global_bonds(
    triples: List[Tuple[Tuple[str, str, str], int]], top_n: int = 3
):
    """Select top-N global bonds ensuring no brother repeats across groups."""
    selected: List[Tuple[Tuple[str, str, str], int]] = []
    used: set[str] = set()
    for triple, score in triples:
        if any(b in used for b in triple):
            continue
        selected.append((triple, score))
        used.update(triple)
        if len(selected) >= top_n:
            break
    return selected


def _select_personal_bonds(
    triples: List[Tuple[Tuple[str, str, str], int]], target_id: str, max_n: int = 3
):
    """Return up to max_n bonds that include the target brother."""
    results = [t for t in triples if target_id in t[0]]
    return results[:max_n]


def _bond_tier(score: int):
    """Map bond score to a tier label."""
    if score <= 2:
        return "FRAGILE"
    if score <= 4:
        return "FORMING"
    if score <= 7:
        return "RELIABLE"
    if score <= 10:
        return "STALWART"
    return "INDOMITABLE"


# def _bond_note_for_tier(tier: str):
#     """Short RP flavor note based on bond tier."""
#     notes = {
#         "FRAGILE": "Newly forged; deploy with caution and oversight.",
#         "FORMING": "Solidifying cohesion; suitable for standard interdictions.",
#         "RELIABLE": "Dependable triad; recommended for key objectives.",
#         "STALWART": "Battle-proven; excels under sustained pressure.",
#         "INDOMITABLE": "Elite assault element; unleash against priority threats.",
#     }
#     return notes.get(tier, "Operational performance under review.")


# def _member_rank_label(member: Optional[discord.Member]):
#     """Return best-fit Watch rank label for a member, defaulting to 'Watch Brother'."""
#     if not member:
#         return "Watch Brother"
#     rank_roles_priority = [
#         "Watch Master",
#         "Venerable",
#         "Lord Executioner",
#         "Reclusiarch",
#         "Forgemaster",
#         "Chief Apothecary",
#         "Void Warden",
#         "Watch Captain",
#         "Watch Lieutenant",
#         "Watch Chaplain",
#         "Watch Techmarine",
#         "Watch Apothecary",
#         "Watch Librarian",
#         "Watch Champion",
#         "Watch Sergeant",
#         "Kill Team Champion",
#         "Watch Veteran",
#         "Watch Brother",
#     ]
#     names = {r.name for r in getattr(member, "roles", [])}
#     for rank in rank_roles_priority:
#         if rank in names:
#             return rank
#     return "Watch Brother"


async def _resolve_home_chapters(
    guild: Optional[discord.Guild], user_ids: List[str], limit: int = 500
):
    """Resolve home chapters for given users by scanning the '#◈⋅⋅record-of-blood⋅⋅◈' channel.
    Logic: find a message that mentions the user; detect the chapter within that same message's content.
    The chapter is detected by matching any of the known `home_chapters` names within the message.
    Returns mapping of user_id -> chapter string. Missing entries map to 'REDACTED'.
    """
    home_chapters = [
        "Black Templars",
        "Blood Angels",
        "Blood Ravens",
        "Cowled Wardens",
        "Dark Angels",
        "Dark Krakens",
        "Death Spectres",
        "Flesh Eaters",
        "Flesh Tearers",
        "Hawk Lords",
        "Imperial Fists",
        "Iron Hands",
        "Lamenters",
        "Minotaurs",
        "Raven Guard",
        "Salamanders",
        "Sons of Medusa",
        "Space Wolves",
        "Storm Giants",
        "Ultramarines",
        "Black Shields",
    ]
    chapters: Dict[str, str] = {}
    if not guild:
        return chapters
    channel = discord.utils.get(guild.channels, name="◈⋅⋅record-of-blood⋅⋅◈")
    if not channel:
        return chapters
    target_set = set(user_ids)
    # Oldest first so 'prev_msg' is the message above (older) when we hit a mention line
    async for msg in channel.history(limit=limit, oldest_first=True):
        # Collect mentioned IDs in this message
        mentioned = {str(u.id) for u in msg.mentions}
        intersect = mentioned & target_set
        if intersect:
            for uid in intersect:
                if uid not in chapters:
                    chapter = "REDACTED"
                    # Adjusted: find chapter within the SAME message content
                    if msg.content:
                        text = msg.content.strip()
                        lower_text = text.lower()
                        match = next(
                            (hc for hc in home_chapters if hc.lower() in lower_text),
                            None,
                        )
                        if match:
                            chapter = match
                    chapters[uid] = chapter
        if len(chapters) == len(target_set):
            break
    return chapters


def _normalize_chapter(name: str):
    """Best-effort normalization to match our mapping keys."""
    n = name.strip()
    # Simple canonicalization pass
    aliases = {
        "black templars": "Black Templars",
        "blood angels": "Blood Angels",
        "blood ravens": "Blood Ravens",
        "cowled wardens": "Cowled Wardens",
        "dark angels": "Dark Angels",
        "dark krakens": "Dark Krakens",
        "death spectres": "Death Spectres",
        "flesh eaters": "Flesh Eaters",
        "flesh tearers": "Flesh Tearers",
        "hawk lords": "Hawk Lords",
        "imperial fists": "Imperial Fists",
        "iron hands": "Iron Hands",
        "lamenters": "Lamenters",
        "minotaurs": "Minotaurs",
        "raven guard": "Raven Guard",
        "salamanders": "Salamanders",
        "sons of medusa": "Sons of Medusa",
        "space wolves": "Space Wolves",
        "storm giants": "Storm Giants",
        "ultramarines": "Ultramarines",
        "black shields": "Black Shields",
    }
    lower = n.lower()
    return aliases.get(lower, n)


def _chapters_to_archetypes(chapters: List[str]):
    archetypes: List[str] = []
    for ch in chapters:
        key = _normalize_chapter(ch)
        archetypes.append(CHAPTER_ARCHETYPE.get(key, "unknown"))
    return archetypes


def _pick_primary_secondary(archetypes: List[str]):
    """Choose a primary and secondary archetype based on frequency.
    - Normally: by count desc, then fixed priority.
    - If all counts are equal: choose using a seed-based pseudo-random selection
      for variety while remaining deterministic per input.
    """
    counter = Counter(archetypes)
    if not counter:
        return "unknown", "unknown"

    items = list(counter.items())
    counts = {c for _, c in items}

    # Build deterministic seed from the multiset of archetypes
    seed = "|".join(sorted(archetypes))

    if len(counts) == 1 and len(items) > 1:
        # All present archetypes have equal frequency; pick via seed-based indices
        arches_only = sorted(a for a, _ in items)
        if len(arches_only) == 1:
            return arches_only[0], arches_only[0]
        i1 = _stable_index(seed, len(arches_only), salt="eq1")
        primary = arches_only[i1]
        # Ensure secondary differs when possible
        i2_base = _stable_index(seed, len(arches_only), salt="eq2")
        i2 = i2_base
        if len(arches_only) > 1 and arches_only[i2] == primary:
            i2 = (i2 + 1) % len(arches_only)
        secondary = arches_only[i2]
        return primary, secondary

    # Sort by count desc, then by our fixed priority
    def sort_key(item):
        arch, count = item
        prio = (
            ARCHETYPE_PRIORITY.index(arch)
            if arch in ARCHETYPE_PRIORITY
            else len(ARCHETYPE_PRIORITY)
        )
        return (-count, prio)

    sorted_arches = sorted(items, key=sort_key)
    primary = sorted_arches[0][0]

    # Secondary is the next distinct archetype if it exists
    secondary = primary
    for arch, _ in sorted_arches[1:]:
        if arch != primary:
            secondary = arch
            break

    return primary, secondary


def _stable_index(seed: str, modulo: int, salt: str):
    """Deterministically pick an index using sha256(seed+salt)."""
    h = hashlib.sha256((seed + salt).encode("utf-8")).hexdigest()
    num = int(h[:8], 16)
    return num % max(1, modulo)


def generate_combat_bond_name(ch1: str, ch2: str, ch3: str):
    """
    Given three home chapters, generate a 40k-flavored Combat Bond name.
    Deterministic: same trio of chapters always yields the same title.
    """
    chapters = [ch1, ch2, ch3]
    arches = _chapters_to_archetypes(chapters)
    primary, secondary = _pick_primary_secondary(arches)

    # Seed: deterministic for this set of chapters
    seed = "|".join(sorted(_normalize_chapter(c) for c in chapters))

    # Pull motif pools, falling back to 'unknown' if needed
    prim_motifs = ARCHETYPE_MOTIFS.get(primary, ARCHETYPE_MOTIFS["unknown"])
    sec_motifs = ARCHETYPE_MOTIFS.get(secondary, ARCHETYPE_MOTIFS["unknown"])

    prim_adjs = prim_motifs["adjectives"]
    prim_nouns = prim_motifs["nouns"]
    sec_nouns = sec_motifs["nouns"]

    # Indices are salted so we don't accidentally pick the same word for both slots
    adj_idx = _stable_index(seed, len(prim_adjs), salt="adj")
    noun_primary_idx = _stable_index(seed, len(prim_nouns), salt="noun_primary")
    noun_secondary_idx = _stable_index(seed, len(sec_nouns), salt="noun_secondary")

    adj = prim_adjs[adj_idx]
    noun_primary = prim_nouns[noun_primary_idx]
    noun_secondary = sec_nouns[noun_secondary_idx]

    # Pick a template
    template_choice = _stable_index(seed, 3, salt="tpl")

    if template_choice == 0:
        # THE CRIMSON SPEAR
        title = f"THE {adj.upper()} {noun_secondary.upper()}"
    elif template_choice == 1:
        # CRIMSON SPEAR
        title = f"{adj.upper()} {noun_secondary.upper()}"
    else:
        if template_choice == 2 and noun_primary == noun_secondary:
            # Nudge the primary noun index by 1
            noun_primary_idx = (noun_primary_idx + 1) % len(prim_nouns)
            noun_primary = prim_nouns[noun_primary_idx]
        # THE SPEAR OF THE ABYSS (noun from secondary of primary)
        # If primary == secondary, this will still pick two different nouns
        # because of different salts.
        title = f"THE {noun_secondary.upper()} OF {noun_primary.upper()}"

    return title


def _format_bonds_for_discord(
    bonds: List[Tuple[Tuple[str, str, str], int]],
    guild: Optional[discord.Guild] = None,
    window_span: int = 50,
    chapters: Optional[Dict[str, str]] = None,
):
    """Produce styled Combat Bonds output matching the requested layout."""
    if not bonds:
        return "No qualifying Combat Bonds found in the current window."
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // COMBAT BONDS COGITATOR")
    lines.append("  SUB-ROUTINE: TRIADIC BATTLE-LITANY INDEX")
    lines.append(
        "=============================================================================="
    )
    lines.append(f"  Auspex Window: Last {window_span} sanctioned engagement(s)")
    rank = 1
    ordinal_labels = {1: "PRIMARY", 2: "SECONDARY", 3: "TERTIARY"}
    for triple, score in bonds:
        tier = _bond_tier(score)
        a, b, c = triple

        # Resolve members and labels (rank + name + chapter)
        def _member_label(uid: str):
            member = None
            name = "REDACTED"
            if guild:
                try:
                    member = guild.get_member(int(uid))
                except Exception:
                    member = None
            if member:
                name = member.nick or member.display_name

            chap = (chapters or {}).get(uid)
            chap_str = chap if chap else "REDACTED"
            return f"{name} [{chap_str}]"

        # Optional codename derived from majority chapter
        tri_chapters = [(chapters or {}).get(x) for x in (a, b, c)]
        tri_chapters = [ch for ch in tri_chapters if ch]
        codename = (
            generate_combat_bond_name(*tri_chapters) if len(tri_chapters) == 3 else None
        )
        title = ordinal_labels.get(rank, "BOND")
        if codename:
            lines.append(f'    ++ {title} BOND: "{codename}" ++')
        else:
            lines.append(f"    ++ {title} BOND ++")

        lines.append(f"    {_member_label(a)}")
        lines.append(f"    {_member_label(b)}")
        lines.append(f"    {_member_label(c)}")
        lines.append(f"    Veneration: Bond Integrity classified as {tier}")
        lines.append("")
        rank += 1
    lines.append(
        "=============================================================================="
    )
    lines.append("  Machine-Spirit Addendum:")
    lines.append("  These Combat Bonds are logged for future deployment rites")
    lines.append("  and may be invoked by decree of Watch Command alone.")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")
    return "\n".join(lines)


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable not set")

bot.run(token)
