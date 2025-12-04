#!/usr/bin/env python3

import os
import json
import discord
from typing import Any

# Data file locations
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message):
    # Copilot: in on_message, add handling for a "!litany-of-function" command:
    # this command should only respond if the sender has the role "Watch COmmand"
    if message.content.startswith("!litany-of-function"):
        # if not is_watch_command(message.author):
        #     return

        litany_text = """
            # ++ SECURE VOX-CHANNEL ESTABLISHED ++
Designation: Watch-Scribe Logi-Servitor V-1, “Operation-Scribe.”
Status: Active. Machine-spirit nominal. Awaiting Watch Command directives.

This servitor is bound by the Edict of Record-Keeping to serve the officers
of Watch Fortress Jericho. Unauthorized personnel will be disregarded.

# Recognized High-Authority Commands:

• **!tally-deeds @Brother**
Queries the Record of Deeds for the specified Watch Brother.
Returns: AAR Points, Apothecarion Gene-Seed Credit, Armory Data Tally,
and current service rank.

• **!reconcile-records**
Initiates a full archival sweep of the After-Action-Report vox-channel.
Reprocesses all recorded missions, amends the Record of Deeds,
and flags any corrupted or improperly formatted entries.

**Operational Restrictions:**
Only those bearing the mantle of Watch Command or Watch Master may issue
orders to this unit. All others shall be logged and ignored according to
Protocol Purity-Seventeen.

This servitor exists to record deeds, preserve honor, and maintain the
eternal ledger of the Long Watch.

# ++ END OF TRANSMISSION ++
            """
        await message.reply(litany_text)
        return

    # Copilot: in on_message, add handling for a "!reconcile-records":
    # this command needs to read an entire history of messages in a channel
    # called "after-action-reports" and compute the total AAR points, geneseed
    # points, and armory points for each brother mentioned in any AAR and update
    # the current record in aar_records.json accordingly. This command is only available
    # for watch command.
    if message.content.startswith("!reconcile-records"):
        # if not is_watch_command(message.author):
        #     returnc

        # aar_channel = discord.utils.get(message.guild.channels, name="᛭⋅after-action-reports⋅᛭")
        aar_channel = discord.utils.get(message.guild.channels, name="demo")
        # print discord channels
        if not aar_channel:
            await message.reply("++ ERROR ++\n +⋅after-action-reports⋅+ \n ++ CHANNEL NOT FOUND. ++")
            return

        async for msg in aar_channel.history(limit=None):
            if is_aar_message(msg):
                record = parse_aar(msg)
                try:
                    aar_id = record["aar_id"]
                except TypeError:
                    print(f"Failed to parse AAR ID from message {msg.id}, skipping.")
                    continue

                if has_been_processed(aar_id):
                    continue

                errors = validate_aar(record)
                if errors:
                    # Log errors and mark as processed, then skip
                    log_aar_errors(aar_id, errors)
                    # Mark as rejected: log errors with jump URL, and mark processed
                    log_aar_errors(aar_id, [f"Jump URL: {msg.jump_url}"] + errors)
                    add_processed_id(aar_id)
                    # exit()
                    continue

                save_aar_record(record)

        await message.reply("++ COGNIFICATION COMPLETE: AAR RECORDS UPDATED. ++")
        return

    # Copilot: In on_message, add handling for a "!tally-deeds" command:
    # - Only respond if is_watch_command(message.author) is True.
    # - Require that exactly one user is mentioned; if none, reply with usage.
    # - Let target = first mentioned user.
    # - Call stats = compute_stats_for_user(str(target.id)).
    # - Determine "Current Rank" by choosing the highest-priority rank role
    #   from the target's roles (e.g. "Watch Sergeant", "Watch Brother", etc.).
    # - Reply in the same channel with:
    #   Stats for <mention>:
    #   Current Rank: <rank>
    #   AAR Points: <aar_points>
    #   Gene-seed Points: <gene_seed_points>
    #   Armory Data Points: <armory_points>
    # - Then return so the AAR handler does not run for this message.
    ############## HONORS COMMAND HANDLING ##############
    if message.content.startswith("!tally-deeds"):
        # if not is_watch_command(message.author):
        #     return

        if len(message.mentions) != 1:
            await message.reply(
                """++ tally-deeds DIRECTIVE ++\n
                Proper Invocation: !tally-deeds @Brother\n
                One—and only one—Brother must be specified.\n
                ++ END DIRECTIVE ++"""
            )
            return

        target = message.mentions[0]
        stats = compute_stats_for_user(str(target.id))

        # Determine Current Rank
        rank_roles_priority = [
            "Watch Master",
            "Watch Techmarine",
            "Watch Librarian",
            "Watch Apothecary",
            "Watch Chaplain",
            "Lord Executioner",
            "Watch Captain",
            "Watch Lieutenant",
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

        reply_text = (
            f"Stats for {target.mention}:\n"
            f"Current Rank: {current_rank}\n"
            f"AAR Points: {stats['aar_points']}\n"
            f"Gene-seed Points: {stats['gene_seed_points']}\n"
            f"Armory Data Points: {stats['armory_points']}"
        )
        await message.channel.send(reply_text)
        return

    # Ignore the bot's own messages
    if message.author == client.user:
        return

        # AAR handling
    if is_aar_message(message):
        record = parse_aar(message)
        aar_id = record["aar_id"]

        # Check if already processed
        if has_been_processed(aar_id):
            print(f"AAR {aar_id} already processed, skipping.")
            return

        # Validate before saving
        errors = validate_aar(record)
        if errors:
            error_text = "AAR rejected, please correct and repost:\n" + "\n".join(
                f"- {e}" for e in errors
            )
            await message.reply(error_text)
            # Log errors and mark this AAR as processed
            log_aar_errors(aar_id, errors)
            add_processed_id(aar_id)
            print(f"AAR {aar_id} rejected with errors: {errors}")
            return

        # All good: save and confirm
        save_aar_record(record)
        # print_aar_summary(record)

        # Optional: short confirmation reply
        # await message.reply(f"AAR accepted and recorded as ID {aar_id}.")


# Copilot: Replace any use of record["points"] with record["points_for_op"],
# and remove any remaining calls to compute_points_for_difficulty. We only
# use classify_difficulty + compute_points_for_op now.


# Copilot: Add a helper classify_difficulty(difficulty: str | None) -> str | None
# It should map the difficulty string to one of:
# "ruthless_ops", "lethal_ops", "absolute_ops",
# "normal_stratagem", "hard_stratagem",
# "normal_siege", "hard_siege", or None.
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


# Copilot: Add a helper compute_points_for_op(difficulty_class: str | None, waves: int | None) -> int
# Use this table:
# Ruthless Ops -> 2
# Lethal Ops -> 3
# Absolute Ops -> 4
# Normal-Stratagem -> 2
# Hard-Stratagem -> 5
# Normal-Siege -> 3 * (waves // 5)
# Hard-Siege -> 4 * (waves // 5)
# Return 0 if info is insufficient.
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


# Copilot: Add a helper compute_gene_seed_base_points_for_carrier(difficulty_class: str | None) -> int
# Use this table for when gene-seed is carried:
# Ruthless Ops / Normal-Stratagem -> 2
# Lethal Ops -> 3
# Absolute Ops -> 4
# Hard-Stratagem -> 5
# Siege difficulties -> 0.
#
# Return 0 by default.
def compute_gene_seed_base_points_for_carrier(difficulty_class: str | None):
    if not difficulty_class:
        return 0
    if difficulty_class == "ruthless_ops" or difficulty_class == "normal_stratagem":
        return 2 + 1
    if difficulty_class == "lethal_ops":
        return 3 + 1
    if difficulty_class == "absolute_ops":
        return 4 + 1
    if difficulty_class == "hard_stratagem":
        return 5 + 1
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
    return "++ MISSION REPORT ++" in content


def get_user_ids_in_line(line: str, message: discord.Message):
    """Return list of user IDs whose mention appears in this line."""
    ids = []
    for user in message.mentions:
        patterns = (f"<@{user.id}>", f"<@!{user.id}>")
        if any(p in line for p in patterns):
            ids.append(str(user.id))
    return ids


def parse_aar(message: discord.Message):
    # Copilot: After computing difficulty_class and parsing armory_data,
    # compute armory_challenge_points = compute_armory_challenge_points(armory_data, difficulty_class)
    # and include this field in the returned record dict.

    content = message.content
    aar_id = message.id
    lines = content.splitlines()

    mission = None
    difficulty = None
    difficulty_tags = []
    black_laurels_active = False
    armory_data = 0
    gene_seed_status = "unknown"
    gene_seed_carrier_id = None
    gene_seed_carried_name = None
    brothers_ids = []
    brother_names = []
    waves = 0

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
        elif lower.startswith("difficulty:"):
            after_colon = line.split(":", 1)[1]
            for role in message.role_mentions:
                mention = f"<@&{role.id}>"
                after_colon = after_colon.replace(mention, role.name)
            difficulty = after_colon.strip()
            difficulty_tags = [t for t in difficulty.split() if t.startswith("@")]
            black_laurels_active = any(
                "black" in t.lower() and "laurel" in t.lower() for t in difficulty_tags
            )

        # Armory / Armoury Data in any order, any capitalization
        elif ("armory" in lower or "armoury" in lower) and "data" in lower:
            # e.g. "Armory Data: 3" or "Armory data: 3"
            parts = line.split(":", 1)
            try:
                armory_data = int(parts[1].strip()) if len(parts) > 1 else 0
            except ValueError:
                print(f"Failed to parse armory data from line: {line}")
                armory_data = 0

        # Gene-Seed: lost / carried by @Brother
        elif "gene-seed" in lower:
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
                           print(f"Failed to get nickname for user ID {gene_seed_carrier_id}")

        elif lower.startswith("brothers"):
            # Everything after this (until END OF REPORT) is the Brothers section
            brothers_start_idx = i + 1

        elif lower.startswith("waves:"):
            parts = line.split(":", 1)
            try:
                waves = int(parts[1].strip())
            except:
                waves = None

    # Copilot: After parsing mission, difficulty, waves, armory_data, and gene seed fields,
    # compute:
    # difficulty_class = classify_difficulty(difficulty)
    # points_for_op = compute_points_for_op(difficulty_class, waves)
    # gene_seed_base_points_for_carrier = 0 unless gene_seed_status == "carried",
    # in which case call compute_gene_seed_base_points_for_carrier(difficulty_class).
    #
    # Include these in the returned record dict.
    difficulty_class = classify_difficulty(difficulty)
    points_for_op = compute_points_for_op(difficulty_class, waves)
    gene_seed_base_points_for_carrier = 0
    if gene_seed_status == "carried":
        gene_seed_base_points_for_carrier = compute_gene_seed_base_points_for_carrier(
            difficulty_class
        )

    # Collect Brothers from lines after "Brothers:" until END OF REPORT
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
                                print(f"Failed to get nickname for user ID {uid}")
                                print(user.name)
        return {
            "aar_id": aar_id,
            "mission": mission,
            "difficulty": difficulty,
            "difficulty_class": difficulty_class,
            "difficulty_tags": difficulty_tags,
            "black_laurels_active": black_laurels_active,
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
        }


def validate_aar(record: dict):
    """
    Validate a parsed AAR record.
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

    # 1) Mission required
    if not mission:
        errors.append("Mission is missing (line starting with 'Mission:').")

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

    # 3) Siege must have valid Waves: line
    if "@normal-siege" in dlower or "@hard-siege" in dlower:
        if waves is None:
            errors.append("Siege difficulty requires a 'Waves:' line.")
        else:
            try:
                w = int(waves)
                if w < 5 or w % 5 != 0:
                    errors.append("Waves must be a multiple of 5 (e.g. 5, 10, 15).")
            except ValueError:
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

    # 6) Gene-seed logic
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


def _load_json_dict(path: str) -> dict:
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


def load_processed_ids() -> set[str]:
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

    print(f"Saved AAR {record['aar_id']} to {filename}.")


def has_been_processed(aar_id: int):
    processed = load_processed_ids()
    return str(aar_id) in processed


def print_aar_summary(record: dict):
    print("AAR Summary:")
    print(f"  AAR ID: {record['aar_id']}")
    print(f"  Mission: {record['mission']}")
    print(f"  Difficulty: {record['difficulty']}")
    print(f"  Armory Data: {record['armory_data']}")
    print(f"  Gene-Seed Status: {record['gene_seed_status']}")
    print(f"  Gene-Seed Carrier ID: {record.get('gene_seed_carrier_name')}")
    print(f"  Brothers (user IDs): {', '.join(record['brother_names'])}")


# Copilot: Add a helper is_watch_command(member: discord.Member) -> bool
# It should return True if any of the member's roles has a name like
# "Watch Command" or "Watch Master". Make the list of allowed role names
# easy to modify.
# def is_watch_command(member: discord.Member):
#     allowed_role_names = {"Watch Command", "Watch Master"}
#     for role in member.roles:
#         if role.name in allowed_role_names:
#             return True
#     return False


# Copilot: Add a function compute_stats_for_user(user_id: str).
# It should:
# - load aar_records.json using load_aar_data
# - initialize:
#   ops = 0
#   aar_points = 0
#   armory_raw = 0
#   armory_points = 0
#   gene_carries = 0
#   gene_seed_points = 0
# - for each record:
#   * if user_id in record["brothers"]:
#       - ops += 1
#       - aar_points += record["points_for_op"]
#       - armory_raw += armory_data (parsed as int or 0)
#       - armory_points += record["armory_points"]
#   * if record["gene_seed_status"] == "carried":
#       - if record["gene_seed_carrier_id"] == user_id:
#           gene_carries += 1
#           gene_seed_points += record["gene_seed_base_points_for_carrier"]
#       - elif user_id in brothers:
#           gene_seed_points += 1  # assist
# - return all of these in a dict.
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

        if record.get("gene_seed_status") == "carried":
            gene_carrier = record.get("gene_seed_carrier_id")
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


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable not set")

client.run(token)
