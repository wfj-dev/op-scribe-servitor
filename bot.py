#!/usr/bin/env python3

import os
import json
import discord

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

    # Reconcile: ingest unprocessed AARs, then re-check error entries; accepted records are untouched
    if message.content.startswith("!reconcile-records"):
        # if not is_watch_command(message.author):
        #     return

        aar_channel = discord.utils.get(
            message.guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        # aar_channel = discord.utils.get(message.guild.channels, name="demo")
        if not aar_channel:
            await message.reply(
                "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++"
            )
            return

        ingested = 0
        rejected = 0
        fixed = 0
        still_broken = 0

        error_entries = _load_json_dict(AAR_ERRORS_PATH)

        if len(error_entries) > 0:
            # Phase A: re-check errors first
            for aar_id_str in list(error_entries.keys()):
                try:
                    aar_id = int(aar_id_str)
                except ValueError:
                    del error_entries[aar_id_str]
                    continue
                if has_been_processed(aar_id):
                    data = _load_json_dict(AAR_ERRORS_PATH)
                    sid = str(aar_id)
                    if sid in data:
                        del data[sid]
                        _save_json_dict(AAR_ERRORS_PATH, data)
                    fixed += 1
                    continue
                try:
                    msg = await aar_channel.fetch_message(aar_id)
                except Exception:
                    msg = None
                if not msg:
                    log_aar_errors(
                        aar_id, ["Original AAR message not found in channel."]
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
                    still_broken += 1
                    continue
                errors = validate_aar(record)
                if errors:
                    log_aar_error_with_meta(
                        aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg
                    )
                    still_broken += 1
                else:
                    save_aar_record(record)
                    data = _load_json_dict(AAR_ERRORS_PATH)
                    sid = str(aar_id)
                    if sid in data:
                        del data[sid]
                        _save_json_dict(AAR_ERRORS_PATH, data)
                    fixed += 1

        # Phase B or only phase: ingest any new, unprocessed AARs from channel
        async for msg in aar_channel.history(limit=None):
            if not is_aar_message(msg):
                continue
            record = parse_aar(msg)
            if record is None:
                log_aar_error_with_meta(
                    msg.id,
                    [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                    msg,
                )
                rejected += 1
                continue
            aar_id = record.get("aar_id", msg.id)
            if has_been_processed(aar_id):
                continue
            errors = validate_aar(record)
            if errors:
                log_aar_error_with_meta(
                    aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg
                )
                rejected += 1
                continue
            save_aar_record(record)
            ingested += 1

        # After reconcile, compute remaining errors directly from the error log
        remaining_errors = _load_json_dict(AAR_ERRORS_PATH)
        still_broken = len(remaining_errors)

        # Build author rejection summary from current error log
        author_summaries = summarize_error_authors()
        author_lines = []
        for a in author_summaries:
            label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
            author_lines.append(f"- {label}: {a['count']}")

        # Final report with metrics clarified
        report = (
            "```ansi\n"
            "\u001b[32m===============================================\n"
            "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
            "  OPERATION-SCRIBE SERVITOR — RECONCILIATION RITE\n"
            "===============================================\n"
            "  ++ LITANY OF RECONCILIATION COMPLETE ++\n"
            f"  Sanctioned Operational Records: {ingested}\n"
            f"  Logs Judged Corrupted or Unworthy: {rejected}\n"
            f"  Restored Entries Returned to the Annals: {fixed}\n"
            f"  Faulted Reports Under Quarantine: {still_broken}\n"
        )

        if author_lines:
            report += "-----------------------------------------------\n"
            report += "Entries Rejected Due to Authorial Deviation:\n"
            for line in author_lines:
                report += f"  {line}\n"

        # Close ANSI block
        report += "\u001b[0m```"


        await message.reply(report)
        return

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
            "```ansi\n"
            "\u001b[32m===============================================\n"
            "  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE\n"
            "  OPERATION-SCRIBE SERVITOR — DEEDS LEDGER\n"
            "===============================================\n"
            f"  Tally for: {target.nick}\n"
            "-----------------------------------------------\n"
            f"  Current Rank: {current_rank}\n"
            f"  AAR Commendation Points: {stats['aar_points']}\n"
            f"  Gene-seed Retrieval Points: {stats['gene_seed_points']}\n"
            f"  Armory Data Acquisition Points: {stats['armory_points']}\n"
            "===============================================\n"
            "\u001b[0m```"
        )
        await message.channel.send(reply_text)
        return

    # Ignore the bot's own messages
    if message.author == client.user:
        return

        # AAR handling for live ingestion (disabled in favor of reconcile command)
    # if is_aar_message(message):
    #     record = parse_aar(message)
    #     aar_id = record["aar_id"]

    #     # Check if already processed
    #     if has_been_processed(aar_id):
    #         print(f"AAR {aar_id} already processed, skipping.")
    #         return

    #     # Validate before saving
    #     errors = validate_aar(record)
    #     if errors:
    #         error_text = "AAR rejected, please correct and repost:\n" + "\n".join(
    #             f"- {e}" for e in errors
    #         )
    #         await message.reply(error_text)
    #         # Log errors and mark this AAR as processed
    #         log_aar_error_with_meta(aar_id, errors, message)
    #         add_processed_id(aar_id)
    #         # print(f"AAR {aar_id} rejected with errors: {errors}")
    #         return

    #     # All good: save and confirm
    #     save_aar_record(record)

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
    # Initiation Trial fields
    initiation_trial_active = False
    initiation_trial_progress = None  # type: int | None
    initiation_trial_max = 3
    initiation_trial_watch_command = False
    initiation_trial_tag_in_mission = False
    initiation_trial_line_present = False

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
            # Also detect Initiation Trial tokens on the mission line
            mlow = mission.lower()
            if "initiation trial" in mlow:
                initiation_trial_active = True
                initiation_trial_tag_in_mission = True
                try:
                    # Look for a token like n/3 in the mission value
                    for token in mission.replace("@", " ").split():
                        if "/" in token:
                            num_part = token.split("/", 1)[0]
                            try:
                                initiation_trial_progress = int(num_part)
                            except Exception:
                                initiation_trial_progress = None
                            break
                except Exception:
                    initiation_trial_progress = None
            if "watch command" in mlow:
                initiation_trial_watch_command = True
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
                            print(
                                f"Failed to get nickname for user ID {gene_seed_carrier_id}"
                            )

        # Initiation Trial lines (e.g., "@Initiation Trial: -/3")
        elif "initiation trial" in lower:
            initiation_trial_active = True
            initiation_trial_line_present = True
            try:
                after_colon = line.split(":", 1)[1].strip() if ":" in line else line
                for token in after_colon.replace("@", " ").split():
                    if "/" in token:
                        num_part = token.split("/", 1)[0]
                        try:
                            initiation_trial_progress = int(num_part)
                        except Exception:
                            initiation_trial_progress = None
                        break
            except Exception:
                initiation_trial_progress = None

        # Watch Command marker sometimes present on trial templates
        elif "watch command" in lower:
            initiation_trial_watch_command = True

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
                                print(f"Failed to get nickname for user/ID {user.name}\/{uid}")

    # Always return a record, even if Brothers section is missing; validation will handle errors
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
        # initiation trial meta
        "initiation_trial_active": initiation_trial_active,
        "initiation_trial_progress": initiation_trial_progress,
        "initiation_trial_max": initiation_trial_max,
        "initiation_trial_watch_command": initiation_trial_watch_command,
        "initiation_trial_tag_in_mission": initiation_trial_tag_in_mission,
        "initiation_trial_line_present": initiation_trial_line_present,
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

    # 1) Mission required
    if not mission:
        errors.append("Mission is missing (line starting with 'Mission:').")
    else:
        mstr = str(mission)
        # Reject any user or role mentions or trial-style tokens in Mission
        # if "<@&" in mstr or "<@" in mstr:
        #     errors.append("Mission must be plain text; no Discord mentions are allowed after 'Mission:'.")
        if "/" in mstr:
            errors.append("Mission must not include trial-style progress tokens like 'n/m' or '-/m'.")

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
        has_black_laurels = ("black" in dlower and "laurel" in dlower)
        has_absolute = ("absolute" in dlower)
        if has_black_laurels and not has_absolute:
            errors.append("@Black_Laurels may only be present when @Absolute is selected on the Difficulty line.")

    # 3) Siege must have valid Waves: line
    if "normal-siege" in dlower or "hard-siege" in dlower:
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


def _author_info_from_message(msg: discord.Message) -> dict:
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
