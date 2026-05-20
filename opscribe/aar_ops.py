"""AAR operations: parsing, validation, ingestion, reconciliation,
audit, challenge tracking."""

import os
import asyncio
import json
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
import re
from typing import Dict, List, Tuple, Optional
import hashlib
import sys as _sys

from .constants import *  # noqa: F401,F403
from .flavor_text import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from .studs import *  # noqa: F401,F403
from . import _bot_globals as _g
from .forge_ops import (
    _load_armor_integrity,
    _save_armor_batch,
    _process_armor_integrity_for_aar,
    _post_armor_alert,
    _get_member_damage_tier,
    _get_armor_state,
    _roll_armor_penalty,
    _increment_aar_generation,
)
from .librarius_ops import (
    _apply_warp_exposure_for_aar,
    _get_warp_exposure_state,
    _roll_warp_penalty,
)


def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


def _load_challenge_progress() -> Dict[str, Dict]:
    """Load challenge progress tracking data: user_id -> {challenge_key -> [mission_entries]}.

    Structure:
    {
        "user_id": {
            "sok_g_pipehitter": [
                {"mission": "inferno", "aar_id": "123", "message_url": "...", "timestamp": "..."},
                ...
            ],
            "kadaku_campaign": [...],
            "black_reef": [...],
            "black_reef_distinguished": [...],
            "notified": ["sok_g_pipehitter", "kadaku_campaign"]  # List of challenges already notified
        }
    }
    """
    try:
        if os.path.exists(CHALLENGE_PROGRESS_PATH):
            with open(CHALLENGE_PROGRESS_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_challenge_progress(progress_data: Dict[str, Dict]):
    """Persist challenge progress data to disk with backup."""
    try:
        tmp_path = CHALLENGE_PROGRESS_PATH + ".tmp"
        bak_path = CHALLENGE_PROGRESS_PATH + ".bak"
        with open(tmp_path, "w") as f:
            json.dump(progress_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(CHALLENGE_PROGRESS_PATH):
            try:
                os.replace(CHALLENGE_PROGRESS_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, CHALLENGE_PROGRESS_PATH)
    except Exception as e:
        _g.logger.exception(f"Failed to save challenge progress: {e}")


async def _process_challenge_tracking(record: dict, guild: discord.Guild) -> List[Tuple[str, str, List[str]]]:
    """Process an AAR record for challenge progress tracking.

    Returns list of (user_id, challenge_name, aar_urls) tuples for newly qualified members.
    Only returns each challenge once per member (won't notify again).

    Challenge tracking:
    - sok_g_pipehitter: 10 SOK-G missions with @SOK-G: Pipehitter tag + existing Pipehitter on team
    - distinguished_sok_g_pipehitter: 2+ SOK-G missions with tag + team requirement
    - kadaku_campaign: All 3 Kadaku missions with @Leviathan Protocol tag
    - black_reef: All 8 Black Reef missions with @Black Reef Persecution tag
    - distinguished_black_reef: All 8 missions with BOTH @Black Reef Persecution and @Black Laurels
    - crux_terminatus: Watch Veteran + 2+ SOK-G + All 8 Black Laurels + 2+ Terminus Slayer (auto-verify these)
    - order_omega: All 12 missions at Omega difficulty with @Black Laurels tag
    """
    notifications = []

    # Extract AAR fields
    # Strip role ID mentions (e.g., "<@&123456>") from mission name before comparisons so that
    # missions like "Inferno <@&1435812894532042843>" match the clean set entries like "inferno".
    _raw_mission = (record.get("mission") or record.get("mission_name") or "").lower()
    mission_name = re.sub(r"<@&\d+>", "", _raw_mission).strip()
    brother_ids = record.get("brother_ids", [])
    aar_id = record.get("aar_id") or record.get("id", "")
    message_url = record.get("message_url", "")
    timestamp = record.get("timestamp", "")

    # Tag detection
    pipehitter_mentioned = record.get("pipehitter_mentioned", False)
    leviathan_protocol = record.get("leviathan_protocol_in_mission", False)
    black_reef_persecution = record.get("black_reef_persecution_in_mission", False)
    # Black Laurels may appear on either the Mission or Difficulty line; treat both as valid.
    black_laurels = record.get("black_laurels_in_mission", False) or record.get("black_laurels_in_difficulty", False)
    difficulty_class = record.get("difficulty_class") or ""

    # Skip if no mission name or no participants
    if not mission_name or not brother_ids:
        return notifications

    # Load current progress
    async with _g.CHALLENGE_PROGRESS_LOCK:
        progress_data = _load_challenge_progress()

        # Check each brother in the AAR
        for brother_id in brother_ids:
            user_id_str = str(brother_id)

            # Get member object for display name and role checks
            member = guild.get_member(int(brother_id)) if guild else None

            # Initialize user progress if needed
            if user_id_str not in progress_data:
                progress_data[user_id_str] = {"notified": []}

            user_progress = progress_data[user_id_str]
            
            # Update display name if we have the member
            if member:
                user_progress["display_name"] = member.display_name
            
            notified_challenges = user_progress.get("notified", [])

            # Rank gate: only send notifications for Watch Brother or higher.
            # Progress is still accumulated regardless so retroactive alerts fire
            # on the next AAR submission after the member reaches Watch Brother rank.
            is_watch_brother_or_higher = False
            if member:
                _rn = {getattr(r, "name", "") for r in member.roles}
                is_watch_brother_or_higher = (
                    "Watch Brother" in _rn
                    or "Watch Sister" in _rn
                    or any(
                        r in _rn
                        for r in (
                            "Watch Veteran", "Oathsworn", "Kill Team Champion",
                            "Watch Sergeant", "Watch Techmarine", "Watch Librarian",
                            "Watch Apothecary", "Watch Chaplain", "Watch Keeper",
                            "Company Champion", "Watch Lieutenant", "Watch Captain",
                            "Venerable Dreadnought", "Honored Dreadnought", "Forgemaster",
                            "Void Warden", "High Chaplain", "Chief Apothecary",
                            "Castellan", "Lord Executioner", "Watch Master",
                        )
                    )
                )

            # === SOK-G: Pipehitter tracking ===
            # Pipehitter challenges require Hard-Stratagem difficulty.
            if (
                pipehitter_mentioned
                and mission_name in PIPEHITTER_ELIGIBLE_MISSIONS
                and difficulty_class == "hard_stratagem"
            ):
                # Check if team has existing Pipehitter or Distinguished Pipehitter
                team_has_pipehitter = False
                if member:
                    # Check all team members for Pipehitter role
                    for other_brother_id in brother_ids:
                        other_member = guild.get_member(int(other_brother_id))
                        if other_member and (
                            discord.utils.get(other_member.roles, id=PIPEHITTER_ROLE_ID)
                            or discord.utils.get(other_member.roles, id=DISTINGUISHED_PIPEHITTER_ROLE_ID)
                        ):
                            team_has_pipehitter = True
                            break

                if team_has_pipehitter:
                    # Track this mission for SOK-G: Pipehitter
                    if "sok_g_pipehitter" not in user_progress:
                        user_progress["sok_g_pipehitter"] = []

                    # Check if this mission already tracked
                    existing_missions = {m["mission"] for m in user_progress["sok_g_pipehitter"]}
                    if mission_name not in existing_missions:
                        user_progress["sok_g_pipehitter"].append(
                            {
                                "mission": mission_name,
                                "aar_id": aar_id,
                                "message_url": message_url,
                                "timestamp": timestamp,
                            }
                        )

                    # Check if qualified for SOK-G: Pipehitter (10 missions)
                    unique_missions = {m["mission"] for m in user_progress["sok_g_pipehitter"]}
                    if (
                        len(unique_missions) >= 10
                        and "sok_g_pipehitter" not in notified_challenges
                        and is_watch_brother_or_higher
                        and not discord.utils.get(member.roles, id=PIPEHITTER_ROLE_ID)
                    ):
                        aar_urls = [m["message_url"] for m in user_progress["sok_g_pipehitter"] if m["message_url"]]
                        notifications.append((user_id_str, "SOK-G: Pipehitter", aar_urls))
                        notified_challenges.append("sok_g_pipehitter")

                    # Check if qualified for Distinguished SOK-G: Pipehitter (2+ missions)
                    if (
                        len(unique_missions) >= 2
                        and "distinguished_sok_g_pipehitter" not in notified_challenges
                        and is_watch_brother_or_higher
                        and not discord.utils.get(member.roles, id=DISTINGUISHED_PIPEHITTER_ROLE_ID)
                    ):
                        aar_urls = [m["message_url"] for m in user_progress["sok_g_pipehitter"] if m["message_url"]]
                        notifications.append((user_id_str, "Distinguished SOK-G: Pipehitter", aar_urls))
                        notified_challenges.append("distinguished_sok_g_pipehitter")

            # === Kadaku Campaign Medal tracking ===
            if leviathan_protocol and mission_name in KADAKU_CAMPAIGN_REQUIRED_MISSIONS:
                if "kadaku_campaign" not in user_progress:
                    user_progress["kadaku_campaign"] = []

                # Check if this mission already tracked
                existing_missions = {m["mission"] for m in user_progress["kadaku_campaign"]}
                if mission_name not in existing_missions:
                    user_progress["kadaku_campaign"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )

                # Check if all 3 missions completed
                unique_missions = {m["mission"] for m in user_progress["kadaku_campaign"]}
                if (
                    len(unique_missions) >= 3
                    and unique_missions == KADAKU_CAMPAIGN_REQUIRED_MISSIONS
                    and "kadaku_campaign" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=KADAKU_CAMPAIGN_MEDAL_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["kadaku_campaign"] if m["message_url"]]
                    notifications.append((user_id_str, "Kadaku Campaign Medal", aar_urls))
                    notified_challenges.append("kadaku_campaign")

            # === Black Reef Campaign Medal tracking ===
            if black_reef_persecution and mission_name in BLACK_REEF_REQUIRED_MISSIONS:
                if "black_reef" not in user_progress:
                    user_progress["black_reef"] = []

                # Check if this mission already tracked
                existing_missions = {m["mission"] for m in user_progress["black_reef"]}
                if mission_name not in existing_missions:
                    user_progress["black_reef"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )

                # Check if all 8 missions completed
                unique_missions = {m["mission"] for m in user_progress["black_reef"]}
                if (
                    len(unique_missions) >= 8
                    and unique_missions == BLACK_REEF_REQUIRED_MISSIONS
                    and "black_reef" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["black_reef"] if m["message_url"]]
                    notifications.append((user_id_str, "Black Reef Campaign Medal", aar_urls))
                    notified_challenges.append("black_reef")

            # === Distinguished Black Reef Campaign Medal tracking ===
            if black_reef_persecution and black_laurels and mission_name in BLACK_REEF_REQUIRED_MISSIONS:
                if "distinguished_black_reef" not in user_progress:
                    user_progress["distinguished_black_reef"] = []

                # Check if this mission already tracked
                existing_missions = {m["mission"] for m in user_progress["distinguished_black_reef"]}
                if mission_name not in existing_missions:
                    user_progress["distinguished_black_reef"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )

                # Check if all 8 missions completed with both tags
                unique_missions = {m["mission"] for m in user_progress["distinguished_black_reef"]}
                if (
                    len(unique_missions) >= 8
                    and unique_missions == BLACK_REEF_REQUIRED_MISSIONS
                    and "distinguished_black_reef" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["distinguished_black_reef"] if m["message_url"]]
                    notifications.append((user_id_str, "Distinguished Black Reef Campaign Medal", aar_urls))
                    notified_challenges.append("distinguished_black_reef")

            # === Crux Terminatus tracking (auto-verification) ===
            # Auto-verify: Watch Veteran rank, 2+ SOK-G missions, All 8 Black Laurels, 2+ Terminus Slayer classes
            # Manual verification needed: Rank A or higher extermination requirement only
            if member:
                # Check Watch Veteran rank
                has_watch_veteran = any(r.name == "Watch Veteran" for r in member.roles)

                # Check SOK-G missions (2+ required)
                sok_g_count = len(user_progress.get("sok_g_pipehitter", []))

                # Check Black Laurels role (implies all 8 missions completed)
                has_black_laurels = discord.utils.get(member.roles, id=BLACK_LAURELS_ROLE_ID) is not None

                # Check Terminus Slayer class completions (2+ required)
                terminus_slayer_count = sum(1 for r in member.roles if r.id in TERMINUS_SLAYER_ROLE_IDS)

                if (
                    has_watch_veteran
                    and sok_g_count >= 2
                    and has_black_laurels
                    and terminus_slayer_count >= 2
                    and "crux_terminatus" not in notified_challenges
                    and not discord.utils.get(member.roles, id=CRUX_TERMINATUS_ROLE_ID)
                ):
                    # Gather AAR URLs for SOK-G missions
                    aar_urls = [m["message_url"] for m in user_progress.get("sok_g_pipehitter", []) if m["message_url"]]
                    notifications.append((user_id_str, "Crux Terminatus", aar_urls))
                    notified_challenges.append("crux_terminatus")

            # === The Order Omega tracking ===
            # Track omega difficulty missions with Black Laurels tag (all 12 missions required)
            difficulty_class = record.get("difficulty_class") or ""
            if black_laurels and difficulty_class == "omega_ops" and mission_name in ORDER_OMEGA_REQUIRED_MISSIONS:
                if "order_omega" not in user_progress:
                    user_progress["order_omega"] = []

                # Check if this mission already tracked
                existing_missions = {m["mission"] for m in user_progress["order_omega"]}
                if mission_name not in existing_missions:
                    user_progress["order_omega"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )

                # Check if all 12 missions completed at Omega with Black Laurels
                unique_missions = {m["mission"] for m in user_progress["order_omega"]}
                if (
                    len(unique_missions) >= 12
                    and unique_missions == ORDER_OMEGA_REQUIRED_MISSIONS
                    and "order_omega" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=THE_ORDER_OMEGA_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["order_omega"] if m["message_url"]]
                    notifications.append((user_id_str, "The Order Omega", aar_urls))
                    notified_challenges.append("order_omega")

            # Update notified list
            user_progress["notified"] = notified_challenges

        # Save updated progress
        _save_challenge_progress(progress_data)

    return notifications


def _get_challenge_librarian_mention(guild: discord.Guild) -> str:
    """Return the Watch Librarian role mention for challenge notifications.

    Resolves in order: config warp_corruption.librarian_role_id, name lookup, plain text.
    """
    try:
        warp_cfg = ((_g.CONFIG or {}).get("warp_corruption") or {})
        raw = warp_cfg.get("librarian_role_id")
        if raw:
            return f"<@&{int(raw)}>"
    except (ValueError, TypeError):
        pass
    role = discord.utils.get(guild.roles, name="Watch Librarian")
    if role:
        return role.mention
    return "@Watch Librarian"


def _get_challenge_keeper_mention(guild: discord.Guild) -> str:
    """Return the Watch Keeper role mention for challenge notifications."""
    role = discord.utils.get(guild.roles, name="Watch Keeper")
    if role:
        return role.mention
    return "@Watch Keeper"


_EMBED_FIELD_LIMIT = 1024


def _build_url_field_text(aar_urls: List[str]) -> str:
    """Build AAR URL list text for an embed field, capped at Discord's 1024-char field limit."""
    lines: list[str] = []
    for i, url in enumerate(aar_urls):
        line = f"• {url}"
        remaining = len(aar_urls) - i - 1
        overflow = f"\n_(+{remaining} more)_" if remaining > 0 else ""
        candidate = "\n".join(lines + [line]) + overflow
        if len(candidate) > _EMBED_FIELD_LIMIT:
            omitted = len(aar_urls) - i
            overflow = f"\n_(+{omitted} more)_"
            return ("\n".join(lines) + overflow) if lines else "_(none)_"
        lines.append(line)
    return "\n".join(lines) or "_(none)_"


async def _send_challenge_eligibility_notifications(
    notifications: List[Tuple[str, str, List[str]]], guild: discord.Guild
):
    """Send challenge eligibility notifications to Librarius Staff channel.

    Args:
        notifications: List of (user_id, challenge_name, aar_urls) tuples
        guild: Discord guild object
    """
    if not notifications:
        return

    # Get Librarius Staff channel
    librarius_channel = guild.get_channel(LIBRARIUS_STAFF_CHANNEL_ID)
    if not librarius_channel:
        _g.logger.warning(f"Librarius Staff channel {LIBRARIUS_STAFF_CHANNEL_ID} not found")
        return

    librarian_mention = _get_challenge_librarian_mention(guild)
    keeper_mention = _get_challenge_keeper_mention(guild)

    # Send one notification per qualified member+challenge
    for user_id, challenge_name, aar_urls in notifications:
        try:
            member = guild.get_member(int(user_id))
            member_mention = member.mention if member else f"<@{user_id}>"

            url_text = _build_url_field_text(aar_urls)

            if "Crux Terminatus" in challenge_name:
                # Special embed for Crux Terminatus (auto-verification complete except Rank A)
                ping_content = f"{librarian_mention} {keeper_mention}"
                embed = discord.Embed(
                    title="᛭⋅ Challenge Qualification: Crux Terminatus ⋅᛭",
                    description=f"{member_mention} has met all auto-verified requirements for **Crux Terminatus**.",
                    color=0xC0392B,
                )
                embed.add_field(
                    name="✅ Auto-verified Requirements",
                    value=(
                        "Watch Veteran rank\n"
                        "2+ SOK-G: Pipehitter missions completed\n"
                        "All 8 Black Laurels missions completed\n"
                        "2+ Terminus Slayer class completions"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="❓ Manual Verification Required",
                    value="Rank A or higher extermination (highest difficulty requirement)",
                    inline=False,
                )
                embed.add_field(name="Relevant SOK-G AAR Links", value=url_text or "_(none)_", inline=False)
                embed.set_footer(text="Please audit the qualifying AARs and verify the Rank A extermination requirement.")
            elif "The Order Omega" in challenge_name:
                # Special embed for The Order Omega
                ping_content = librarian_mention
                embed = discord.Embed(
                    title="᛭⋅ Challenge Qualification: The Order Omega ⋅᛭",
                    description=(
                        f"{member_mention} has completed all 12 required missions "
                        "at Omega difficulty with Black Laurels tag."
                    ),
                    color=0x9B59B6,
                )
                embed.add_field(name="Qualifying AAR Links", value=url_text or "_(none)_", inline=False)
                embed.set_footer(text="Please audit the qualifying AARs for verification.")
            else:
                # Standard embed for other challenges
                ping_content = librarian_mention
                embed = discord.Embed(
                    title=f"᛭⋅ Challenge Qualification: {challenge_name} ⋅᛭",
                    description=f"{member_mention} has met qualification for **{challenge_name}** — please audit relevant AARs.",
                    color=0xF1C40F,
                )
                embed.add_field(name="Qualifying AAR Links", value=url_text or "_(none)_", inline=False)

            await librarius_channel.send(content=ping_content, embed=embed)
            _g.logger.info(f"Sent challenge eligibility notification for {user_id} - {challenge_name}")

            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)

        except Exception as e:
            _g.logger.exception(f"Failed to send challenge notification for {user_id} - {challenge_name}: {e}")


@_g.bot.tree.command(name="reconcile_records", description="Reprocess AARs and update the archive.")
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def reconcile_records(interaction: discord.Interaction, span_days: int | None = None):
    if not (
        _b("check_command_permission")(interaction.user, "reconcile_records") and _b("is_allowed_channel")(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Serialize concurrent invocations to avoid file races
    if _g.RECONCILE_LOCK.locked():
        _g.logger.info(f"reconcile_records blocked: lock held (user={interaction.user.id})")
        try:
            await interaction.response.send_message(
                "Another reconciliation is in progress. Please try again shortly.",
                ephemeral=True,
            )
        except Exception:
            _g.logger.debug("Could not send 'locked' response to interaction; continuing.")
        return
    # Defer may fail (Unknown interaction) if the interaction is stale; handle gracefully.
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception as e:
        _g.logger.debug(f"Interaction defer failed: {e}")

    _g.logger.info(f"reconcile_records: acquiring lock (user={interaction.user.id})")
    async with _g.RECONCILE_LOCK:
        _g.logger.info(f"reconcile_records: lock acquired (user={interaction.user.id})")
        await _reconciliation_core(interaction, span_days)


@_g.bot.tree.command(
    name="record_of_blood",
    description="Scan Watch Brothers' home chapters and cross-reference records in the record-of-blood channel.",
)
async def record_of_blood(interaction: discord.Interaction):
    # Restrict to Watch Master or Forgemaster only
    try:
        names = _b("_canonical_role_names")(interaction.user)
    except Exception:
        names = set()
    if not ("Watch Master" in names or "Forgemaster" in names):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception:
        pass

    guild = interaction.guild or _b("_resolve_notification_guild")()
    if not guild:
        await interaction.followup.send("Unable to resolve guild.", ephemeral=True)
        return

    # Resolve Watch Brother role and members
    wb_role = discord.utils.get(guild.roles, name="Watch Brother")
    watch_brothers = []
    if wb_role:
        try:
            watch_brothers = list(getattr(wb_role, "members", []) or [])
        except Exception:
            watch_brothers = []
    # If role exists but members cache is empty, or role wasn't present, scan guild members as a fallback
    if not watch_brothers:
        try:
            for m in getattr(guild, "members", []) or []:
                try:
                    if "Watch Brother" in _b("_canonical_role_names")(m):
                        watch_brothers.append(m)
                except Exception:
                    continue
        except Exception:
            watch_brothers = watch_brothers or []

    # Map member id -> resolved home chapter role (from their roles)
    member_home: dict[str, str] = {}
    members_with_noncanonical_home: list[tuple[str, str]] = []
    for m in watch_brothers:
        chap = ""
        try:
            member_role_names = {(getattr(r, "name", "") or "").strip() for r in m.roles if getattr(r, "name", None)}
            match = next(
                (hc for hc in _b("HOME_CHAPTERS") if any(rn.lower() == hc.lower() for rn in member_role_names)),
                None,
            )
            if match:
                chap = match
            else:
                # Try datastore fallback if available
                try:
                    if _g.DATASTORE:
                        ds_val = _g.DATASTORE.get_home_chapter(str(getattr(m, "id", "")))
                        if ds_val:
                            chap = ds_val
                except Exception:
                    pass
        except Exception:
            chap = ""
        member_home[str(getattr(m, "id", ""))] = chap or ""
        if chap and chap not in _b("HOME_CHAPTERS"):
            members_with_noncanonical_home.append((m.display_name or m.name, chap))

    # Channel to cross-reference (from the provided URL)
    # URL: https://discord.com/channels/1429264578440597517/1446926555732250674
    target_channel_id = 1446926555732250674
    resolved_bot = getattr(_g, "bot", None) or _b("bot")
    target_channel = (resolved_bot.get_channel(target_channel_id) if resolved_bot else None) or guild.get_channel(
        target_channel_id
    )
    if not target_channel:
        await interaction.followup.send(f"Unable to find target channel <#{target_channel_id}>.", ephemeral=True)
        return

    # Scan messages for mentions of _b('HOME_CHAPTERS') (and any guild role names not in _b('HOME_CHAPTERS'))
    chapter_mentions_by_msg: list[dict] = []
    noncanonical_mentioned: set[str] = set()
    _g.logger.info(f"/record_of_blood: scanning channel {target_channel_id} for {len(watch_brothers)} watch brothers")
    try:
        async for msg in target_channel.history(limit=2000):
            content = msg.content or ""
            if not content:
                continue
            low = content.lower()
            # Detect chapter declared on first line in format ":emoji: ⋅ chaptername:".
            first_line = content.splitlines()[0].strip() if content.splitlines() else ""
            first_chap = None
            try:
                m = re.match(r"^:[^:]+:\s*⋅\s*(.+?):", first_line)
                if m:
                    first_chap = m.group(1).strip()
            except Exception:
                first_chap = None

            # Find explicit canonical chapter mentions in the body
            found = [hc for hc in _b("HOME_CHAPTERS") if hc.lower() in low]

            # Only consider messages that tag members — ignore others entirely
            mentions = getattr(msg, "mentions", []) or []
            if not mentions:
                continue

            # If a first-line chapter was declared, treat it as a referenced chapter
            if first_chap:
                if all(first_chap.lower() != hc.lower() for hc in found):
                    found.append(first_chap)
                if all(first_chap.lower() != hc.lower() for hc in _b("HOME_CHAPTERS")):
                    noncanonical_mentioned.add(first_chap)

            # Also detect guild role names mentioned that are not in _b('HOME_CHAPTERS')
            extra = [
                r.name for r in guild.roles if r.name and r.name.lower() in low and r.name not in _b("HOME_CHAPTERS")
            ]
            if extra:
                for e in extra:
                    noncanonical_mentioned.add(e)

            if not found and not extra:
                continue

            # Record for each mentioned member which chapters the message referenced
            rec = {
                "msg": msg,
                "chapters": found or extra,
                "mentions": [],
                "first_chap": first_chap,
            }
            for mm in mentions:
                try:
                    rec["mentions"].append(
                        {
                            "id": str(getattr(mm, "id", "")),
                            "display": mm.display_name or mm.name,
                        }
                    )
                except Exception:
                    continue
            chapter_mentions_by_msg.append(rec)
    except Exception as e:
        _g.logger.debug(f"Failed scanning channel history: {e}")

    # Determine which canonical _b('HOME_CHAPTERS') were mentioned in the channel
    try:
        mentioned_canonical: set[str] = set()
        for rec in chapter_mentions_by_msg:
            try:
                for ch in rec.get("chapters", []) or []:
                    # normalize against canonical list
                    for hc in _b("HOME_CHAPTERS"):
                        if ch and ch.lower() == hc.lower():
                            mentioned_canonical.add(hc)
            except Exception:
                continue
        missing_home_chapters = [hc for hc in _b("HOME_CHAPTERS") if hc not in mentioned_canonical]
    except Exception:
        mentioned_canonical = set()
        missing_home_chapters = []

    # Build report
    lines: list[str] = []
    lines.append("```ansi")
    lines.append("\u001b[32m==============================================================================")
    lines.append("  WATCH FORTRESS JERICHO // RECORD-OF-BLOOD AUDIT")
    lines.append("==============================================================================")
    lines.append(f"  Watch Brothers scanned: {len(watch_brothers)}")
    lines.append("")

    # Members whose home chapter is absent or non-canonical
    if members_with_noncanonical_home:
        lines.append("Members with home chapter not in canonical _b('HOME_CHAPTERS'):")
        for nm, ch in members_with_noncanonical_home:
            lines.append(f"  - {nm}: {ch}")
        lines.append("")

    # Chapters mentioned in channel but not canonical
    if noncanonical_mentioned:
        lines.append("Chapters/roles mentioned in channel not found in _b('HOME_CHAPTERS'):")
        for ch in sorted(noncanonical_mentioned):
            lines.append(f"  - {ch}")
        lines.append("")

    # _b('HOME_CHAPTERS') that were not mentioned in the scanned channel
    # BUT only report if we have brothers who rep that chapter
    try:
        missing_with_members = [
            ch
            for ch in missing_home_chapters
            if any(member_home.get(mid, "").lower() == ch.lower() for mid in member_home)
        ]
        if missing_with_members:
            lines.append("Home chapters not mentioned in target channel (but have members):")
            for ch in missing_with_members:
                lines.append(f"  - {ch}")
            lines.append("")
    except Exception:
        pass

    # Per-message findings
    if chapter_mentions_by_msg:
        lines.append("Channel message cross-references:")
        for rec in chapter_mentions_by_msg:
            try:
                msg = rec.get("msg")
                mids = rec.get("mentions", [])
                chs = rec.get("chapters", [])
                first_claim = rec.get("first_chap")
                first_claim_noncanonical = bool(
                    first_claim and all(first_claim.lower() != hc.lower() for hc in _b("HOME_CHAPTERS"))
                )

                # Build concise one-line issues for each mismatch
                issues: list[str] = []
                for mrec in mids:
                    mid = mrec.get("id")
                    disp = mrec.get("display")
                    actual = member_home.get(mid, "")
                    claimed = first_claim or (chs[0] if chs else "")
                    is_match = bool(claimed and claimed.lower() == (actual or "").lower())
                    if not is_match:
                        issues.append(
                            f"Message {getattr(msg, 'id', 'unknown')} | {disp}: record_of_blood='{claimed or ', '.join(chs)}' role='{actual or 'UNKNOWN'}'"
                        )

                # If declared chapter is non-canonical, add an issue for it
                if first_claim_noncanonical:
                    issues.insert(
                        0,
                        f"Message {getattr(msg, 'id', 'unknown')} | Declared chapter not in _b('HOME_CHAPTERS'): '{first_claim}'",
                    )

                # Append only the concise issue lines (one per mismatch/issue)
                for it in issues:
                    lines.append(it)
            except Exception:
                continue
        lines.append("")

    if not members_with_noncanonical_home and not noncanonical_mentioned and not chapter_mentions_by_msg:
        lines.append("No discrepancies or chapter mentions found in target channel.")

    lines.append("==============================================================================")
    lines.append("\u001b[0m```")

    report = "\n".join(lines)

    # Build mobile-friendly embed
    embed = discord.Embed(
        title="Record-of-Blood Audit",
        description=f"Watch Brothers scanned: {len(watch_brothers)}",
        color=0x2ECC71,
    )

    if members_with_noncanonical_home:
        noncanon_text = "\n".join(f"• {nm}: {ch}" for nm, ch in members_with_noncanonical_home[:10])
        if len(members_with_noncanonical_home) > 10:
            noncanon_text += f"\n... and {len(members_with_noncanonical_home) - 10} more"
        embed.add_field(name="Non-canonical Home Chapters", value=noncanon_text, inline=False)

    if noncanonical_mentioned:
        noncm_text = ", ".join(sorted(noncanonical_mentioned)[:10])
        if len(noncanonical_mentioned) > 10:
            noncm_text += f" (+{len(noncanonical_mentioned) - 10} more)"
        embed.add_field(name="Non-canonical Chapters Mentioned", value=noncm_text, inline=False)

    # Collect discrepancy details
    discrepancy_details: list[str] = []
    for rec in chapter_mentions_by_msg:
        mids = rec.get("mentions", [])
        first_claim = rec.get("first_chap")
        for mrec in mids:
            mid = mrec.get("id")
            disp = mrec.get("display", "Unknown")
            actual = member_home.get(mid, "")
            claimed = first_claim or (rec.get("chapters", [])[0] if rec.get("chapters") else "")
            if claimed and claimed.lower() != (actual or "").lower():
                discrepancy_details.append(f"• {disp}: claimed **{claimed}**, role **{actual or 'NONE'}**")
        if first_claim and all(first_claim.lower() != hc.lower() for hc in _b("HOME_CHAPTERS")):
            discrepancy_details.append(f"• Non-canonical chapter declared: **{first_claim}**")

    if discrepancy_details:
        # Show up to 10 discrepancies in the embed, with a note if there are more
        disc_text = "\n".join(discrepancy_details[:10])
        if len(discrepancy_details) > 10:
            disc_text += f"\n... and {len(discrepancy_details) - 10} more"
        embed.add_field(
            name=f"Discrepancies Found ({len(discrepancy_details)})",
            value=disc_text,
            inline=False,
        )
    else:
        embed.add_field(name="Status", value="No discrepancies found", inline=False)

    embed.set_footer(text="Use PC/Console button for detailed ANSI view")

    try:
        # Send as followup (deferred earlier). If the report is too large
        # for a single message, attach it as a file instead.
        if len(report) > 1900:
            import io

            fp = io.BytesIO(report.encode("utf-8"))
            fp.seek(0)
            try:
                await interaction.followup.send(
                    "Report too large for toggle view; attached as file.",
                    embed=embed,
                    file=discord.File(fp, filename="record_of_blood.txt"),
                    ephemeral=True,
                )
            finally:
                try:
                    fp.close()
                except Exception:
                    pass
        else:
            view = _b("ToggleFormatView")(text_content=report, embed=embed, default="embed")
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        _g.logger.exception(f"record_of_blood: followup.send failed: {e}")
        try:
            if len(report) > 1900:
                import io

                fp = io.BytesIO(report.encode("utf-8"))
                fp.seek(0)
                try:
                    await interaction.response.send_message(
                        "Report attached.",
                        file=discord.File(fp, filename="record_of_blood.txt"),
                        ephemeral=True,
                    )
                finally:
                    try:
                        fp.close()
                    except Exception:
                        pass
            else:
                await interaction.response.send_message(report, ephemeral=True)
        except Exception as e2:
            _g.logger.exception(f"record_of_blood: response.send_message fallback failed: {e2}")


@_g.bot.tree.command(
    name="audit_archive_discrepancies",
    description="Recheck previously rejected AARs and restore any fixed entries.",
)
@app_commands.describe(span_days="Optional: only recheck errors from the last N days.")
async def audit_archive_discrepancies(interaction: discord.Interaction, span_days: int | None = None):
    if not (
        _b("check_command_permission")(interaction.user, "audit_archive_discrepancies") and _b("is_allowed_channel")(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if _g.RECONCILE_LOCK.locked():
        _g.logger.info(f"audit_archive_discrepancies blocked: lock held (user={interaction.user.id})")
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return

    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

    _g.logger.info(f"audit_archive_discrepancies: acquiring lock (user={interaction.user.id})")
    async with _g.RECONCILE_LOCK:
        _g.logger.info(f"audit_archive_discrepancies: lock acquired (user={interaction.user.id})")
        guild = interaction.guild
        aar_channel = guild.get_channel(AAR_CHANNEL_ID)
        if not aar_channel:
            await interaction.followup.send(
                f"++ ERROR: AAR CHANNEL (ID: {AAR_CHANNEL_ID}) NOT FOUND. ++",
                ephemeral=True,
            )
            return
        fixed, still_broken = await _run_recheck_errors(aar_channel, span_days)

        author_summaries, stale_count = summarize_error_authors(max_age_weeks=4)
        author_lines = []
        for a in author_summaries:
            label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
            author_lines.append(f"  {label}: {a['count']}")

        report = (
            "```ansi\n"
            "\u001b[32m==============================================================================\n"
            "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
            "  OPERATION-SCRIBE SERVITOR — ERROR RECHECK RITE\n"
            "==============================================================================\n"
            f"  Restored: {fixed}\n"
            f"  Still Broken: {still_broken}\n"
        )
        if stale_count > 0:
            report += f"  Stale AARs (>4 weeks): {stale_count}\n"
        if author_lines:
            report += "-----------------------------------------------\n"
            report += "  Errors by Author (last 4 weeks):\n"
            for line in author_lines:
                report += f"{line}\n"
        report += "==============================================================================\n\u001b[0m```"
        # Try to send the report via followup if we successfully deferred.
        if interaction_deferred:
            try:
                await interaction.followup.send(report, ephemeral=True)
            except Exception as e:
                _g.logger.debug(f"Failed to send followup report: {e}")
                # Fallback: attempt to post the report to the invoking channel
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(report)
                    else:
                        _g.logger.error("Unable to deliver report: no channel available.")
                except Exception:
                    _g.logger.error("Unable to deliver report to channel; check bot permissions.")
        else:
            # Interaction was not defer-able; post the report to the invoking channel if possible
            try:
                ch = interaction.channel
                if ch:
                    await ch.send(report)
                else:
                    _g.logger.error("Unable to deliver report: no channel available and DM disabled.")
            except Exception:
                _g.logger.error("Unable to deliver report to channel; interaction unknown and channel send failed.")


@_g.bot.tree.command(
    name="sanctify_battle_records",
    description="Ingest new chronicled AARs (optionally scoped by span of days).",
)
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def sanctify_battle_records(interaction: discord.Interaction, span_days: int | None = None):
    if not (_b("check_command_permission")(interaction.user, "sanctify_battle_records") and _b("is_allowed_channel")(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if _g.RECONCILE_LOCK.locked():
        _g.logger.info(f"sanctify_battle_records blocked: lock held (user={interaction.user.id})")
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

    _g.logger.info(f"sanctify_battle_records: acquiring lock (user={interaction.user.id})")
    async with _g.RECONCILE_LOCK:
        _g.logger.info(f"sanctify_battle_records: lock acquired (user={interaction.user.id})")
        guild = interaction.guild
        aar_channel = guild.get_channel(AAR_CHANNEL_ID)
        if not aar_channel:
            error_msg = f"++ ERROR: AAR CHANNEL (ID: {AAR_CHANNEL_ID}) NOT FOUND. ++"
            if interaction_deferred:
                try:
                    await interaction.followup.send(
                        error_msg,
                        ephemeral=True,
                    )
                except Exception as e:
                    _g.logger.debug(f"Failed to send followup: {e}")
            else:
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(error_msg)
                    else:
                        _g.logger.error("Unable to deliver error report: no channel available and DM disabled.")
                except Exception:
                    _g.logger.error("Unable to deliver error report to channel; check bot permissions.")
            return
        ingested, rejected = await _run_ingest_new(aar_channel, span_days)

        report = (
            "```ansi\n"
            "\u001b[32m==============================================================================\n"
            "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
            "  OPERATION-SCRIBE SERVITOR — INGESTION RITE\n"
            "==============================================================================\n"
            + (f"  Scan Window: Last {span_days} day(s)\n" if span_days else "  Scan Window: Full history\n")
            + f"  Chronicled: {ingested}\n"
            + f"  Rejected: {rejected}\n"
            + "==============================================================================\n"
            + "\u001b[0m```"
        )
        if interaction_deferred:
            try:
                await interaction.followup.send(report, ephemeral=True)
            except Exception as e:
                _g.logger.debug(f"Failed to send followup report: {e}")
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(report)
                    else:
                        _g.logger.error("Unable to deliver report: no channel available.")
                except Exception:
                    _g.logger.error("Unable to deliver report to channel; check bot permissions.")
        else:
            try:
                ch = interaction.channel
                if ch:
                    await ch.send(report)
                else:
                    _g.logger.error("Unable to deliver report: no channel available and DM disabled.")
            except Exception:
                _g.logger.error("Unable to deliver report to channel; check bot permissions.")


async def _reconciliation_core(interaction: discord.Interaction, span_days: int | None):
    guild = interaction.guild
    aar_channel = guild.get_channel(AAR_CHANNEL_ID)
    if not aar_channel:
        await interaction.followup.send(f"++ ERROR: AAR CHANNEL (ID: {AAR_CHANNEL_ID}) NOT FOUND. ++")
        return
    # First recheck errors, then ingest new
    fixed, still_broken = await _run_recheck_errors(aar_channel, span_days)
    ingested, rejected = await _run_ingest_new(aar_channel, span_days)

    # Compose combined report
    author_summaries = summarize_error_authors()
    author_lines = []
    for a in author_summaries:
        label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
        author_lines.append(f"- {label}: {a['count']}")

    report_header = (
        "```ansi\n"
        "\u001b[32m==============================================================================\n"
        "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
        "  OPERATION-SCRIBE SERVITOR — RECONCILIATION RITE\n"
        "==============================================================================\n"
        "  ++ LITANY OF RECONCILIATION COMPLETE ++\n"
        + (f"  Scan Window: Last {span_days} day(s)\n" if span_days else "  Scan Window: Full history\n")
    )

    report = (
        report_header
        + f"  Chronicled Operational Records: {ingested}\n"
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


async def _run_recheck_errors(aar_channel: discord.TextChannel, span_days: Optional[int] = None):
    fixed = 0
    still_broken = 0
    cutoff_dt = None
    if span_days and span_days > 0:
        cutoff_dt = datetime.utcnow() - timedelta(days=span_days)
    error_entries = _load_json_dict(AAR_ERRORS_PATH)
    if len(error_entries) > 0:
        # If windowed, compute total within window for progress counters
        total_errs = 0
        done_errs = 0
        if cutoff_dt is not None:
            # We will determine window membership lazily when fetching messages
            pass
        else:
            total_errs = len(error_entries)
        for aar_id_str in list(error_entries.keys()):
            try:
                aar_id = int(aar_id_str)
            except ValueError:
                del error_entries[aar_id_str]
                continue
            if has_been_processed(aar_id):
                # If the AAR has been processed since the error was recorded,
                # remove it from the errors archive rather than touching the
                # saved records. Previously this removed the record file by
                # mistake which prevented error entries from being cleared.
                try:
                    data = _load_json_dict(AAR_ERRORS_PATH)
                    sid = str(aar_id)
                    if sid in data:
                        reply_id = data.get(sid, {}).get("reply_id")
                        if reply_id:
                            try:
                                # reply is in the same channel as original message
                                dummy_msg = await aar_channel.fetch_message(aar_id)
                                try:
                                    reply_msg = await dummy_msg.channel.fetch_message(int(reply_id))
                                    try:
                                        await reply_msg.delete()
                                    except Exception:
                                        try:
                                            _g.logger.debug(f"Unable to delete reply {reply_id} for AAR {sid}")
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        del data[sid]
                        _save_json_dict(AAR_ERRORS_PATH, data)
                except Exception:
                    pass
                fixed += 1
                done_errs += 1
                if cutoff_dt is None:
                    if (done_errs % 5 == 0) or (done_errs == total_errs):
                        _b("_print_progress")("Recheck Errors", done_errs, total_errs)
                continue
            try:
                msg = await aar_channel.fetch_message(aar_id)
            except Exception:
                msg = None
            if not msg:
                log_aar_errors(aar_id, ["Original message not found; cannot reprocess."])
                # Count as broken only for full scans (no reliable timestamp)
                if cutoff_dt is None:
                    still_broken += 1
                done_errs += 1
                if cutoff_dt is None:
                    if (done_errs % 5 == 0) or (done_errs == total_errs):
                        _b("_print_progress")("Recheck Errors", done_errs, total_errs)
                continue
            # Window filter: skip messages older than cutoff
            if cutoff_dt is not None:
                try:
                    msg_dt = msg.created_at
                    if msg_dt.tzinfo is not None:
                        msg_dt = msg_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    if total_errs == 0:
                        # First time we see a windowed message, estimate total as count of window hits
                        pass
                    if msg_dt < cutoff_dt:
                        continue
                    total_errs += 1
                except Exception:
                    # If timestamp parse fails, conservatively skip from windowed run
                    continue
            record = parse_aar(msg)
            if record is None:
                log_aar_error_with_meta(
                    aar_id,
                    [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                    msg,
                )
                try:
                    await _reply_aar_rejection(
                        msg,
                        [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                    )
                except Exception:
                    pass
                await _set_aar_reaction(msg, "error")
                still_broken += 1
            else:
                errors = validate_aar(record)
                if errors:
                    log_aar_error_with_meta(aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg)
                    try:
                        await _reply_aar_rejection(msg, [f"Jump URL: {msg.jump_url}"] + errors)
                    except Exception:
                        pass
                    await _set_aar_reaction(msg, "error")
                    still_broken += 1
                else:
                    await save_aar_record(record)

                    # --- Armor Integrity: Process cycles for reingested AAR ---
                    try:
                        guild = aar_channel.guild
                        brother_ids = record.get("brother_ids") or []
                        difficulty_class = record.get("difficulty_class")
                        if guild and brother_ids:
                            # Calculate base points per brother (same logic as _run_ingest_new)
                            base_points = {}
                            is_siege = difficulty_class in ("normal_siege", "hard_siege")
                            brother_waves = record.get("brother_waves") or {}
                            global_waves = record.get("waves") or 0
                            try:
                                global_waves = int(global_waves)
                            except Exception:
                                global_waves = 0
                            base_difficulty_points = {
                                "normal_op": 3,
                                "hard_op": 4,
                                "lethal_op": 5,
                                "suicide_op": 6,
                                "omega_op": 10,
                            }.get(difficulty_class, 0)
                            for bid in brother_ids:
                                if is_siege:
                                    waves_for_brother = brother_waves.get(bid)
                                    if waves_for_brother is None:
                                        waves_for_brother = global_waves
                                    try:
                                        waves_for_brother = int(waves_for_brother or 0)
                                    except Exception:
                                        waves_for_brother = 0
                                    if difficulty_class == "normal_siege":
                                        base_points[bid] = 3 * (waves_for_brother // 5)
                                    else:
                                        base_points[bid] = 4 * (waves_for_brother // 5)
                                else:
                                    base_points[bid] = base_difficulty_points

                            # Roll penalties for each brother (same logic as _run_ingest_new)
                            armor_penalties = {}
                            for bid in brother_ids:
                                try:
                                    member = guild.get_member(int(bid))
                                    if member:
                                        tier = _get_member_damage_tier(member)
                                        armor_state = await _get_armor_state(int(bid))
                                        spirit_fractured = armor_state.get("spirit_fractured", False)
                                        rolled_penalty = _roll_armor_penalty(tier, spirit_fractured)
                                        if rolled_penalty > 0:
                                            armor_penalties[bid] = rolled_penalty
                                except Exception:
                                    pass

                            # Process armor integrity for each brother
                            op_mission = record.get("mission")
                            op_url = record.get("message_url")
                            alerts_to_post = []
                            for bid in brother_ids:
                                try:
                                    bid_base_points = base_points.get(bid, 0)
                                    bid_actual_penalty = armor_penalties.get(bid, 0)
                                    penalty, alert_info = await _process_armor_integrity_for_aar(
                                        bid,
                                        bid_base_points,
                                        guild,
                                        None,  # No batch mode for recheck
                                        op_mission=op_mission,
                                        op_difficulty_class=difficulty_class,
                                        op_url=op_url,
                                        squad_member_ids=brother_ids,
                                        actual_penalty=bid_actual_penalty,
                                    )
                                    if alert_info:
                                        alerts_to_post.append(alert_info)
                                except Exception:
                                    pass
                            # Post any armor alerts
                            for alert in alerts_to_post:
                                try:
                                    await _post_armor_alert(
                                        alert["member"],
                                        alert["tier"],
                                        alert.get("critical_count", 0),
                                        guild,
                                        op_mission=alert.get("op_mission"),
                                        op_difficulty_class=alert.get("op_difficulty_class"),
                                        op_url=alert.get("op_url"),
                                        squad_member_ids=alert.get("squad_member_ids"),
                                        alert_type=alert.get("alert_type", "sustained"),
                                        penalty_amount=alert.get("penalty_amount", 0),
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    # --- Challenge Tracking: Process AAR for challenge eligibility ---
                    if guild:
                        try:
                            challenge_notifications = await _process_challenge_tracking(record, guild)
                            if challenge_notifications:
                                await _send_challenge_eligibility_notifications(challenge_notifications, guild)
                        except Exception as e:
                            _g.logger.error(f"Error processing challenge tracking for AAR {aar_id}: {e}")

                    # If an error entry exists for this AAR, attempt to remove
                    # the bot's previous reply and clear the error record.
                    try:
                        data = _load_json_dict(AAR_ERRORS_PATH)
                        sid = str(aar_id)
                        if sid in data:
                            reply_id = data.get(sid, {}).get("reply_id")
                            if reply_id:
                                try:
                                    # reply is in the same channel as the original message
                                    reply_msg = await msg.channel.fetch_message(int(reply_id))
                                    try:
                                        await reply_msg.delete()
                                    except Exception:
                                        try:
                                            _g.logger.debug(f"Unable to delete reply {reply_id} for AAR {sid}")
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            del data[sid]
                            _save_json_dict(AAR_ERRORS_PATH, data)
                    except Exception:
                        pass
                    await _set_aar_reaction(msg, "ok")
                    fixed += 1
            done_errs += 1
            if cutoff_dt is None:
                if (done_errs % 5 == 0) or (done_errs == total_errs):
                    _b("_print_progress")("Recheck Errors", done_errs, total_errs)

    if cutoff_dt is None:
        remaining_errors = _load_json_dict(AAR_ERRORS_PATH)
        still_broken = len(remaining_errors)
    # For windowed runs, still_broken already reflects the subset processed
    return fixed, still_broken


async def _run_ingest_new(aar_channel: discord.TextChannel, span_days: Optional[int]):
    ingested = 0
    rejected = 0
    history_kwargs = {"limit": None}
    cutoff_dt = None
    if span_days and span_days > 0:
        cutoff_dt = datetime.utcnow() - timedelta(days=span_days)
        history_kwargs["after"] = cutoff_dt

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
    if cutoff_dt is None and latest_processed_id:
        try:
            history_kwargs["after"] = discord.Object(id=latest_processed_id)
        except Exception:
            pass

    # Load armor integrity data once for batch processing (avoids repeated file I/O)
    armor_batch = _load_armor_integrity()
    armor_batch_modified = False

    async for msg in aar_channel.history(**history_kwargs):
        if not is_aar_message(msg):
            continue
        scanned += 1
        if cutoff_dt is None and latest_processed_id and msg.id <= latest_processed_id:
            _b("_print_progress")("Ingest New AARs", scanned, scanned)
            break
        record = parse_aar(msg)
        if record is None:
            log_aar_error_with_meta(
                msg.id,
                [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                msg,
            )
            try:
                await _reply_aar_rejection(msg, [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"])
            except Exception:
                pass
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _b("_print_progress")("Ingest New AARs", scanned, scanned)
            continue
        aar_id = record.get("aar_id", msg.id)
        if has_been_processed(aar_id):
            existing = _g.DATASTORE.get_record(aar_id)
            existing_hash = (existing or {}).get("content_hash") if isinstance(existing, dict) else None
            existing_edited = (existing or {}).get("edited_at") if isinstance(existing, dict) else None
            msg_hash = record.get("content_hash")
            msg_edited = record.get("edited_at")
            needs_update = (msg_hash and msg_hash != existing_hash) or (msg_edited and msg_edited != existing_edited)
            if not needs_update:
                if scanned % 10 == 0:
                    _b("_print_progress")("Ingest New AARs", scanned, scanned)
                continue
        errors = validate_aar(record)
        if errors:
            log_aar_error_with_meta(aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg)
            try:
                await _reply_aar_rejection(msg, [f"Jump URL: {msg.jump_url}"] + errors)
            except Exception:
                pass
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _b("_print_progress")("Ingest New AARs", scanned, scanned)
            continue

        # --- Armor Integrity: Check penalties BEFORE saving ---
        guild = aar_channel.guild
        brother_ids = record.get("brother_ids", [])
        # Compute per-brother base points for armor tracking
        # For siege ops: points based on waves per brother
        # For other ops: use points_for_op (same for all brothers)
        difficulty_class = record.get("difficulty_class") or ""
        global_waves = record.get("waves") or 0
        brother_waves = record.get("brother_waves") or {}
        # Calculate base difficulty points directly (pre-penalty value for armor wear)
        base_difficulty_points = compute_points_for_op(difficulty_class, global_waves)
        base_points = {}
        if brother_ids:
            is_siege = difficulty_class in ("normal_siege", "hard_siege")
            for bid in brother_ids:
                if is_siege:
                    # Siege: compute per-brother from waves
                    waves_for_brother = brother_waves.get(bid)
                    if waves_for_brother is None:
                        waves_for_brother = global_waves
                    try:
                        waves_for_brother = int(waves_for_brother or 0)
                    except Exception:
                        waves_for_brother = 0
                    if difficulty_class == "normal_siege":
                        base_points[bid] = 3 * (waves_for_brother // 5)
                    else:
                        base_points[bid] = 4 * (waves_for_brother // 5)
                else:
                    # Non-siege: use base difficulty points (before penalties)
                    base_points[bid] = base_difficulty_points
        armor_penalties = {}
        warp_penalties = {}

        if guild and brother_ids:
            for bid in brother_ids:
                try:
                    member = guild.get_member(int(bid))
                    if member:
                        tier = _get_member_damage_tier(member)
                        # Check for spirit fractured state
                        armor_state = await _get_armor_state(int(bid))
                        spirit_fractured = armor_state.get("spirit_fractured", False)
                        # Roll probabilistic penalty instead of fixed
                        penalty = _roll_armor_penalty(tier, spirit_fractured)
                        if penalty > 0:
                            armor_penalties[bid] = penalty

                        # Warp penalty mirrors Techmarine probabilities by
                        # infection state (3-tier + warp_corrupted flag).
                        warp_state = await _get_warp_exposure_state(int(bid))
                        warp_inf = warp_state.get("infection_state")
                        warp_corrupted = bool(warp_state.get("warp_corrupted"))
                        warp_pen = _roll_warp_penalty(warp_inf, warp_corrupted)
                        if warp_pen > 0:
                            warp_penalties[bid] = warp_pen
                except Exception:
                    pass

        # Store armor penalties in the record
        if armor_penalties:
            record["armor_penalties"] = armor_penalties
        if warp_penalties:
            record["warp_penalties"] = warp_penalties

        await save_aar_record(record)

        # Apply warp corruption gains/spread after recording this AAR.
        try:
            await _apply_warp_exposure_for_aar(record, guild)
        except Exception as e:
            _g.logger.debug(f"Warp exposure update failed for AAR {aar_id}: {e}")

        # --- Armor Integrity: Run checks and post alerts AFTER saving ---
        alerts_to_post = []
        # Extract op context for debrief in alerts
        op_mission = record.get("mission")
        op_url = record.get("message_url")
        if guild and brother_ids:
            for bid in brother_ids:
                try:
                    bid_base_points = base_points.get(bid, 0)
                    bid_actual_penalty = armor_penalties.get(bid, 0)
                    penalty, alert_info = await _process_armor_integrity_for_aar(
                        bid,
                        bid_base_points,
                        guild,
                        armor_batch,
                        op_mission=op_mission,
                        op_difficulty_class=difficulty_class,
                        op_url=op_url,
                        squad_member_ids=brother_ids,
                        actual_penalty=bid_actual_penalty,
                    )
                    if alert_info:
                        alerts_to_post.append(alert_info)
                        armor_batch_modified = True
                except Exception:
                    pass
            # Mark batch as modified if any brother was processed
            if brother_ids:
                armor_batch_modified = True

        # Post any armor alerts (outside the loop to avoid rate limits)
        for alert in alerts_to_post:
            try:
                await _post_armor_alert(
                    alert["member"],
                    alert["tier"],
                    alert.get("critical_count", 0),
                    guild,
                    op_mission=alert.get("op_mission"),
                    op_difficulty_class=alert.get("op_difficulty_class"),
                    op_url=alert.get("op_url"),
                    squad_member_ids=alert.get("squad_member_ids"),
                    alert_type=alert.get("alert_type", "sustained"),
                    penalty_amount=alert.get("penalty_amount", 0),
                )
            except Exception as e:
                _g.logger.error(f"Error calling _post_armor_alert: {e}")

        # --- Challenge Tracking: Process AAR for challenge eligibility ---
        if guild:
            try:
                challenge_notifications = await _process_challenge_tracking(record, guild)
                if challenge_notifications:
                    await _send_challenge_eligibility_notifications(challenge_notifications, guild)
            except Exception as e:
                _g.logger.error(f"Error processing challenge tracking for AAR {aar_id}: {e}")

        # If an error entry exists for this AAR/message, remove stored reply and clear the error
        try:
            data = _load_json_dict(AAR_ERRORS_PATH)
            sid = str(aar_id)
            if sid in data:
                reply_id = data.get(sid, {}).get("reply_id")
                if reply_id:
                    try:
                        reply_msg = await msg.channel.fetch_message(int(reply_id))
                        try:
                            await reply_msg.delete()
                        except Exception:
                            try:
                                _g.logger.debug(f"Unable to delete reply {reply_id} for AAR {sid}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                del data[sid]
                _save_json_dict(AAR_ERRORS_PATH, data)
        except Exception:
            pass
        to_react_ok.append(msg)
        ingested += 1
        if scanned % 10 == 0:
            _b("_print_progress")("Ingest New AARs", scanned, scanned)

        if len(to_react_ok) + len(to_react_err) >= 25:
            for m in to_react_ok:
                await _set_aar_reaction(m, "ok")
            for m in to_react_err:
                await _set_aar_reaction(m, "error")
            to_react_ok.clear()
            to_react_err.clear()

    if to_react_ok or to_react_err:
        for m in to_react_ok:
            await _set_aar_reaction(m, "ok")
        for m in to_react_err:
            await _set_aar_reaction(m, "error")

    # Save armor batch data once at end (avoid repeated file I/O during loop)
    if armor_batch_modified:
        await _save_armor_batch(armor_batch)
        # Increment AAR generation to invalidate scan caches
        await _increment_aar_generation()

    return ingested, rejected


# Admin-only command to print cache sizes, dirty flags, last flush time, and cache hit/miss counters
@_g.bot.tree.command(name="cache_stats", description="Show DataStore cache and flush stats (admin only)")
async def cache_stats(interaction: discord.Interaction):
    if not (_b("check_command_permission")(interaction.user, "cache_stats") and _b("is_allowed_channel")(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    stats = _g.DATASTORE.get_cache_stats()
    import datetime

    last_flush = stats["last_flush_time"]
    if last_flush:
        try:
            lf = datetime.fromtimestamp(last_flush, tz=timezone.utc)
            last_flush_str = lf.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            last_flush_str = datetime.datetime.utcfromtimestamp(last_flush).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        last_flush_str = "Never"
    # Format the user stats cache built timestamp into a single string
    try:
        ts = stats.get("user_stats_cache_built_ts")
        if ts:
            try:
                user_stats_built_str = datetime.datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S %Z"
                )
            except Exception:
                user_stats_built_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            user_stats_built_str = "Never"
    except Exception:
        user_stats_built_str = "Never"

    msg = (
        f"```ansi\n"
        f"\u001b[32m==============================================================================\n"
        f"  WATCH FORTRESS JERICHO // SERVITOR CACHE DIAGNOSTICS\n"
        f"==============================================================================\n"
        f"  User Stats Cache Size:        {stats['user_stats_cache_size']}\n"
        f"  Combat Cache Size:            {stats.get('combat_cache_size', 0)}\n"
        f"  Combat Cache Spans:           {', '.join(stats.get('combat_cache_spans', [])) if stats.get('combat_cache_spans') else 'None'}\n"
        f"  Dirty AAR Records:            {stats['dirty_records']}\n"
        f"  Dirty Processed IDs:          {stats['dirty_ids']}\n"
        f"  Last Flush Time:              {last_flush_str}\n"
        f"  User Stats Cache Built:       {user_stats_built_str}\n"
        f"==============================================================================\n"
        f"\u001b[0m```"
    )
    await interaction.response.send_message(msg, ephemeral=True)


@_g.bot.tree.command(
    name="set_induction",
    description="[Forgemaster] Set or clear a custom induction date for a member.",
)
@app_commands.describe(
    member="The member whose induction date to set",
    date="Induction date in YYYY-MM-DD format (e.g., 2024-06-15). Leave blank to clear override.",
)
async def set_induction(
    interaction: discord.Interaction,
    member: discord.Member,
    date: Optional[str] = None,
):
    """Set or clear a custom induction date override for a member.

    Only Forgemaster can use this command. If date is omitted,
    any existing override is cleared and the Discord join date will be used.
    """
    # Require Forgemaster (via config permissions)
    if not _b("check_command_permission")(interaction.user, "set_induction"):
        await interaction.response.send_message("Only the Forgemaster can set induction dates.", ephemeral=True)
        return

    user_id = str(member.id)

    async with _g.INDUCTION_OVERRIDES_LOCK:
        overrides = _b("_load_induction_overrides")()

        if date is None or date.strip() == "":
            # Clear override
            if user_id in overrides:
                del overrides[user_id]
                _b("_save_induction_overrides")(overrides)
                # Get Discord join date to show what it reverts to
                discord_join = getattr(member, "joined_at", None)
                if discord_join:
                    if discord_join.tzinfo is None:
                        discord_join = discord_join.replace(tzinfo=timezone.utc)
                    discord_date = discord_join.strftime("%Y-%m-%d")
                    await interaction.response.send_message(
                        f"Cleared induction override for {member.mention}. "
                        f"Now using Discord join date: **{discord_date}**",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"Cleared induction override for {member.mention}.",
                        ephemeral=True,
                    )
            else:
                await interaction.response.send_message(
                    f"No induction override exists for {member.mention}.",
                    ephemeral=True,
                )
            return

        # Validate date format
        date_str = date.strip()
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "Invalid date format. Please use YYYY-MM-DD (e.g., 2024-06-15).",
                ephemeral=True,
            )
            return

        # Validate date is not in the future
        if parsed_date.date() > datetime.now().date():
            await interaction.response.send_message("Induction date cannot be in the future.", ephemeral=True)
            return

        # Save override
        overrides[user_id] = date_str
        _b("_save_induction_overrides")(overrides)

        # Calculate days since induction for display
        days_ago = (datetime.now().date() - parsed_date.date()).days
        await interaction.response.send_message(
            f"Set induction date for {member.mention} to **{date_str}** ({days_ago}d ago).",
            ephemeral=True,
        )


@_g.bot.tree.command(
    name="audit_service_studs",
    description="List brothers whose displayed service studs differ from computed entitlement (Watch Command only).",
)
async def audit_service_studs(interaction: discord.Interaction):
    await interaction.response.defer(thinking=False, ephemeral=True)

    if not (_b("check_command_permission")(interaction.user, "audit_service_studs") and _b("is_allowed_channel")(interaction)):
        await interaction.followup.send("Access denied.", ephemeral=True)
        return

    guild = interaction.guild or _b("_resolve_notification_guild")()
    if not guild:
        await interaction.followup.send("Guild not available.", ephemeral=True)
        return

    idx_veteran = _b("_role_index")("Watch Veteran")
    now = datetime.utcnow()
    mismatches: list[tuple[discord.Member, int, int, str, str]] = []

    for member in getattr(guild, "members", []) or []:
        try:
            # Consider only users who have any canonical Watch rank/role
            member_role_names = _b("_canonical_role_names")(member)
            if not any(r in member_role_names for r in _b("RANK_ROLES_PRIORITY")):
                continue

            # Compute entitlement using same rules as roster/tally
            studs_count = 0
            highest_idx = _b("get_highest_rank_index")(member)
            if (idx_veteran is not None) and (highest_idx is not None) and (highest_idx <= idx_veteran):
                joined_at = _b("_get_effective_induction_date")(member)
                if joined_at:
                    ja = joined_at
                    if ja.tzinfo is not None:
                        try:
                            ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
                        except Exception:
                            ja = ja.replace(tzinfo=None)
                    weeks = max(0, (now - ja).days // 7)
                    studs_time = weeks // 4
                else:
                    studs_time = 0

                stats = _b("compute_stats_for_user")(str(getattr(member, "id", "")))
                try:
                    aar_points_val = int(round(float(stats.get("aar_points", 0) or 0)))
                except Exception:
                    aar_points_val = 0
                studs_aar = aar_points_val // 400
                studs_count = min(studs_time, studs_aar)
                studs_count = min(studs_count, 16)

            # Extract existing pips shown in nickname/display name
            # New system: ●=4 (Auramite), ⚬=1 (Plasteel), max 16
            dn = str(member.nick or member.display_name or "")
            existing_aur = dn.count("●")
            existing_plas = dn.count("⚬")
            existing_total = existing_aur * 4 + existing_plas
            # Build actual pip string from display name (sorted: auramite first)
            existing_pips = "●" * existing_aur + "⚬" * existing_plas
            if not existing_pips and existing_total == 0:
                existing_pips = "—"
            expected_pips = _studs_pips(studs_count)

            # Compare expected pips (auramite-only post-4) against what's in
            # the display name. Any deviation is a mismatch.
            is_mismatch = existing_pips != expected_pips

            if is_mismatch:
                mismatches.append((member, studs_count, existing_total, expected_pips, existing_pips))
        except Exception:
            continue

    if not mismatches:
        await interaction.followup.send("No service-stud discrepancies found.", ephemeral=True)
        return

    # Build an ANSI-styled, column-aligned report (green text)
    mismatches.sort(key=lambda t: t[1] - t[2], reverse=True)

    # Prepare printable rows and compute column widths
    rows: list[tuple[str, str, str, str]] = []
    name_max = 4
    exp_max = len("Expected")
    cur_max = len("Current")
    action_max = len("Action")
    for mem, comp, disp, exp_pips, cur_pips in mismatches:
        diff = comp - disp
        action = f"AWARD {diff}" if diff > 0 else ("REFORMAT" if diff == 0 else f"REMOVE {abs(diff)}")
        name = getattr(mem, "display_name", str(getattr(mem, "id", "")))
        rows.append((name, exp_pips, cur_pips, action))
        name_max = max(name_max, len(name))
        exp_max = max(exp_max, len(exp_pips))
        cur_max = max(cur_max, len(cur_pips))
        action_max = max(action_max, len(action))

    # Cap name width to avoid excessively wide blocks
    NAME_CAP = 36
    name_w = min(NAME_CAP, name_max)

    sep = "=" * (name_w + exp_max + cur_max + action_max + 10)

    lines: list[str] = []
    lines.append("```ansi")
    lines.append("\u001b[32m" + sep)
    lines.append("  WATCH FORTRESS JERICHO // SERVICE-STUDS AUDIT")
    lines.append(sep)
    # Build header using safe string methods to avoid nested format fields
    header = (
        "  "
        + "Brother".ljust(name_w)
        + "  "
        + "Expected".rjust(exp_max)
        + "  "
        + "Current".rjust(cur_max)
        + "  "
        + "Action".rjust(action_max)
    )
    lines.append(header)
    lines.append(sep)
    for name, exp_pips, cur_pips, action in rows:
        # Truncate name if necessary
        display_name = name if len(name) <= name_w else name[: name_w - 1] + "…"
        line = (
            "  "
            + display_name.ljust(name_w)
            + "  "
            + exp_pips.rjust(exp_max)
            + "  "
            + cur_pips.rjust(cur_max)
            + "  "
            + action.rjust(action_max)
        )
        lines.append(line)
    lines.append(sep)
    lines.append("\u001b[0m```")

    report = "\n".join(lines)

    # Build mobile-friendly embed
    embed = discord.Embed(
        title="Service-Studs Audit",
        description=f"Found {len(mismatches)} discrepancies",
        color=0x2ECC71,
    )

    # Add up to 10 mismatches to embed fields
    awards_needed = [
        (name, exp_pips, cur_pips, action) for name, exp_pips, cur_pips, action in rows if "AWARD" in action
    ]
    removals_needed = [
        (name, exp_pips, cur_pips, action) for name, exp_pips, cur_pips, action in rows if "REMOVE" in action
    ]
    reformat_needed = [
        (name, exp_pips, cur_pips, action) for name, exp_pips, cur_pips, action in rows if action == "REFORMAT"
    ]

    if awards_needed:
        award_text = "\n".join(f"• {name}: {action}" for name, _, _, action in awards_needed[:8])
        if len(awards_needed) > 8:
            award_text += f"\n... and {len(awards_needed) - 8} more"
        embed.add_field(name=f"Need Awards ({len(awards_needed)})", value=award_text, inline=False)

    if removals_needed:
        remove_text = "\n".join(f"• {name}: {action}" for name, _, _, action in removals_needed[:8])
        if len(removals_needed) > 8:
            remove_text += f"\n... and {len(removals_needed) - 8} more"
        embed.add_field(
            name=f"Need Removal ({len(removals_needed)})",
            value=remove_text,
            inline=False,
        )

    if reformat_needed:
        reformat_text = "\n".join(
            f"• {name}: {cur_pips} → {exp_pips}" for name, exp_pips, cur_pips, _ in reformat_needed[:8]
        )
        if len(reformat_needed) > 8:
            reformat_text += f"\n... and {len(reformat_needed) - 8} more"
        embed.add_field(
            name=f"Need Reformat ({len(reformat_needed)})",
            value=reformat_text,
            inline=False,
        )

    embed.set_footer(text="Use PC/Console button for detailed ANSI table")

    # Send with toggle view
    if len(report) <= 1900:
        view = _b("ToggleFormatView")(text_content=report, embed=embed, default="embed")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        # Report too long for toggle, send embed only
        await interaction.followup.send(embed=embed, ephemeral=True)


async def _run_reparse_records(
    limit: int | None = None, days: int | None = None
) -> tuple[int, int, int, dict[str, int]]:
    """Re-parse AAR records from their message URLs.

    Args:
        limit: Max number of records to reparse (None = unlimited)
        days: Only reparse records from the last N days (None = all)

    Returns:
        Tuple of (total_processed, updated_count, failed_count, changes_by_field)
    """
    total = 0
    updated = 0
    failed = 0
    changes_by_field: dict[str, int] = {}

    # Snapshot of records to process
    now_utc = datetime.now(timezone.utc)
    if days is not None:
        if days <= 0:
            raise ValueError("days must be a positive integer when specified")
        cutoff = now_utc - timedelta(days=days)
    else:
        cutoff = None

    def _in_window(rec: dict) -> bool:
        if cutoff is None:
            return True
        ts_str = rec.get("timestamp")
        if not ts_str:
            return False
        try:
            ts = _b("_parse_iso8601_to_utc")(ts_str)
            return ts is not None and ts >= cutoff
        except Exception:
            return False

    records_list = [(k, v) for k, v in _g.DATASTORE._records.items() if _in_window(v)]
    if limit is not None and limit > 0:
        records_list = records_list[:limit]
    total_records = len(records_list)

    def _print_progress(done: int, total: int) -> None:
        if not _sys.stdout.isatty():
            return
        bar_len = 40
        filled = int(round(bar_len * done / float(total))) if total else bar_len
        perc = (done / total * 100) if total else 100.0
        bar = "#" * filled + "-" * (bar_len - filled)
        _sys.stdout.write(f"\rReparsing records: [{bar}] {done}/{total} ({perc:5.1f}%)")
        _sys.stdout.flush()

    # Iterate snapshot of records
    for idx, (key, rec) in enumerate(records_list, start=1):
        _print_progress(idx - 1, total_records)
        total += 1
        msg_url = rec.get("message_url")
        if not msg_url:
            _print_progress(idx, total_records)
            continue
        try:
            parts = msg_url.rstrip("/").split("/")
            # Expect .../channels/<channel_id>/<message_id> or .../<channel_id>/<message_id>
            if len(parts) < 2:
                raise ValueError("invalid message_url")
            message_id = int(parts[-1])
            channel_id = int(parts[-2])
            bot_obj = getattr(_g, "bot", None) or _b("bot")
            if bot_obj is None:
                raise RuntimeError("bot instance is not available")
            channel = bot_obj.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot_obj.fetch_channel(channel_id)
                except Exception:
                    channel = None
            if channel is None:
                raise RuntimeError(f"channel {channel_id} not available")
            msg = await channel.fetch_message(message_id)
            new_rec = parse_aar(msg)
            if not new_rec:
                continue
            # Preserve some metadata from existing record
            merged = rec.copy()
            merged.update(new_rec)
            # Ensure aar_id remains the same key
            merged["aar_id"] = rec.get("aar_id")

            # --- Challenge Tracking: Process AAR for challenge eligibility (always, even if unchanged) ---
            try:
                guild_obj = channel.guild if hasattr(channel, 'guild') else None
                if guild_obj:
                    challenge_notifications = await _process_challenge_tracking(merged, guild_obj)
                    if challenge_notifications:
                        await _send_challenge_eligibility_notifications(challenge_notifications, guild_obj)
            except Exception as e:
                _g.logger.error(f"Error processing challenge tracking during reparse for AAR {merged.get('aar_id')}: {e}")

            if merged != rec:
                # Track which fields changed
                for field in set(rec.keys()) | set(merged.keys()):
                    if rec.get(field) != merged.get(field):
                        changes_by_field[field] = changes_by_field.get(field, 0) + 1
                await _g.DATASTORE.set_record(str(merged.get("aar_id")), merged)
                updated += 1
        except Exception:
            failed += 1

    # Finalize progress output in terminal
    _print_progress(total_records, total_records)
    if _sys.stdout.isatty():
        _sys.stdout.write("\n")
        _sys.stdout.flush()
    return total, updated, failed, changes_by_field


@_g.bot.tree.command(
    name="reparse_records",
    description="Re-parse stored AAR records from their message_url and update records (admin).",
)
@app_commands.describe(
    limit="Optional: max number of records to reparse.",
    days="Optional: only reparse records from the last N days.",
)
async def reparse_records(
    interaction: discord.Interaction,
    limit: int | None = None,
    days: int | None = None,
):
    if not (_b("check_command_permission")(interaction.user, "reparse_records") and _b("is_allowed_channel")(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if _g.RECONCILE_LOCK.locked():
        _g.logger.info(f"reparse_records blocked: lock held (user={interaction.user.id})")
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    _g.logger.info(f"reparse_records: acquiring lock (user={interaction.user.id})")
    async with _g.RECONCILE_LOCK:
        _g.logger.info(f"reparse_records: lock acquired (user={interaction.user.id})")
        try:
            total, updated, failed, changes_by_field = await _run_reparse_records(limit=limit, days=days)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        days_info = f" (last {days} days)" if days else ""
        # Build changes summary
        if changes_by_field:
            sorted_changes = sorted(changes_by_field.items(), key=lambda x: -x[1])
            changes_summary = ", ".join(f"{k}={v}" for k, v in sorted_changes)
            changes_line = f"\nFields updated: {changes_summary}"
        else:
            changes_line = ""
        await interaction.followup.send(
            f"Reparse complete{days_info}: processed={total}, updated={updated}, failed={failed}{changes_line}",
            ephemeral=True,
        )


def classify_difficulty(difficulty: str | None):
    if not difficulty:
        return None

    lower = difficulty.lower()

    # Use word boundaries to match only complete difficulty terms
    if re.search(r"\bruthless\b", lower):
        return "ruthless_ops"
    if re.search(r"\blethal\b", lower):
        return "lethal_ops"
    if re.search(r"\babsolute\b", lower):
        return "absolute_ops"
    if re.search(r"\bnormal-stratagem\b", lower):
        return "normal_stratagem"
    if re.search(r"\bhard-stratagem\b", lower):
        return "hard_stratagem"
    if re.search(r"\bnormal-siege\b", lower):
        return "normal_siege"
    if re.search(r"\bhard-siege\b", lower):
        return "hard_siege"
    if re.search(r"\bomega\b", lower):
        return "omega_ops"
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
    if difficulty_class == "omega_ops":
        # Omega operations are fixed-value high-intensity missions
        return 20

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
    if difficulty_class == "omega_ops":
        # Omega uses Absolute's base + 1
        return 5
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

    if difficulty_class == "omega_ops":
        # Omega awards one extra armory point per absolute multiplier
        return armory_data * 4

    return 0


def is_aar_message(message: discord.Message):
    content = message.content
    # Treat presence of the start marker as sufficient; END marker optional
    return (
        "++ MISSION REPORT ++" in content
        or "++MISSION REPORT++" in content
        or "++ ᴍɪѕѕɪᴏɴ ʀᴇᴘᴏʀᴛ ++" in content
        or "++ᴍɪѕѕɪᴏɴ ʀᴇᴘᴏʀᴛ++" in content
    )


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
    # Siege per-brother waves participation (parsed from Team lines as '@Brother N')
    brother_waves: Dict[str, int] = {}
    # Initiation Trial (legacy boolean) and initiate ids (list, max 2)
    initiation_trial = False
    initiate_ids: List[str] = []
    # KIA count (Killed In Action)
    kia_count = 0
    kia_line_present = False
    # Chapter Approved tag present (role mention)
    chapter_approved = False
    chapter_approved_extra_point_applied = False
    # Black Laurels tracking
    black_laurels_in_difficulty = False
    black_laurels_in_mission = False
    black_laurels_mentioned_elsewhere = False
    # Leviathan Protocol tracking
    leviathan_protocol_in_mission = False
    leviathan_protocol_in_difficulty = False
    # Black Reef Persecution tracking (allows Black Laurels on Hard-Stratagem when present on Mission line)
    black_reef_persecution_in_mission = False
    # Pipehitter tracking
    pipehitter_mentioned = False
    # Watch Command role mention (required for Initiation Trials)
    watch_command_mentioned = False

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
            # Check if Black Laurels is in mission line (role ID or resolved name)
            if f"<@&{BLACK_LAURELS_ROLE_ID}>" in mission or (
                "black" in mission.lower() and "laurel" in mission.lower()
            ):
                black_laurels_in_mission = True
            # Check if Leviathan Protocol is in mission line
            if f"<@&{LEVIATHAN_PROTOCOL_ROLE_ID}>" in mission or (
                "leviathan" in mission.lower() and "protocol" in mission.lower()
            ):
                leviathan_protocol_in_mission = True
            # Check if Black Reef Persecution is in mission line
            if f"<@&{BLACK_REEF_PERSECUTION_ROLE_ID}>" in mission or ("black reef persecution" in mission.lower()):
                black_reef_persecution_in_mission = True
            # If mission contains a trial-like token, mark the legacy initiation flag
            try:
                import re

                if re.search(r"\b-?\d+/\d+\b", mission) or "trial" in mission.lower():
                    initiation_trial = True
            except Exception:
                pass
        elif lower.startswith("difficulty:") or lower.startswith("threat:"):
            after_colon = line.split(":", 1)[1]
            for role in message.role_mentions:
                mention = f"<@&{role.id}>"
                after_colon = after_colon.replace(mention, role.name)
            difficulty = after_colon.strip()
            # Check if Black Laurels is in difficulty line
            if "black" in after_colon.lower() and "laurel" in after_colon.lower():
                black_laurels_in_difficulty = True
            # Check if Leviathan Protocol is in difficulty line
            if "leviathan" in after_colon.lower() and "protocol" in after_colon.lower():
                leviathan_protocol_in_difficulty = True

        # Armory / Armoury Data in any order, any capitalization
        elif ("armory" in lower or "armoury" in lower) and "data" in lower:
            # e.g. "Armory Data: 3" or "Armory data: 3"
            parts = line.split(":", 1)
            try:
                armory_data = int(parts[1].strip()) if len(parts) > 1 else 0
            except ValueError:
                _g.logger.debug(f"Failed to parse armory data from line: {line}")
                armory_data = 0

        # KIA (Killed In Action) line, e.g. 'KIA: 1' or 'KIA: <@12345>'
        elif lower.startswith("kia:"):
            kia_line_present = True
            parts = line.split(":", 1)
            kia_val = parts[1].strip() if len(parts) > 1 else ""
            # Prefer numeric count if present, otherwise count mentions on that line
            try:
                kia_count = int(kia_val)
            except Exception:
                # fallback: count mentions on this line
                kia_count = 0
                for uid in get_user_ids_in_line(raw_line, message):
                    kia_count += 1
            # Clamp KIA to allowed range 0-4
            try:
                kia_count = max(0, min(4, int(kia_count)))
            except Exception:
                kia_count = 0

        # Gene-Seed / Geneseed: lost / carried by @Brother / @Brother (just tag)
        # Valid "carried" formats:
        #   - "Gene-Seed: @Brother" (just a tag, nothing else)
        #   - "Gene-Seed: carried by @Brother" (explicit "carried by")
        # Anything else (e.g., random text with a tag) is NOT parsed as carried
        elif ("gene-seed" in lower) or ("geneseed" in lower):
            parts = line.split(":", 1)
            rest = parts[1].strip() if len(parts) > 1 else ""
            rest_lower = rest.lower()

            if "lost" in rest_lower:
                gene_seed_status = "lost"
            else:
                ids_here = get_user_ids_in_line(raw_line, message)
                if ids_here:
                    # Check if it's "carried by" format OR just a bare tag
                    # Remove the mention from rest to see what's left
                    rest_without_mentions = rest
                    for uid in ids_here:
                        rest_without_mentions = rest_without_mentions.replace(f"<@{uid}>", "").replace(f"<@!{uid}>", "")
                    rest_without_mentions = rest_without_mentions.strip().lower()

                    # Valid if: "carried by" OR nothing left (just the tag)
                    if (
                        "carried" in rest_without_mentions
                        or rest_without_mentions == ""
                        or rest_without_mentions == "by"
                    ):
                        gene_seed_status = "carried"
                        gene_seed_carrier_id = ids_here[0]
                        # Also set gene_seed_carried_name to the Discord nickname of the carrier
                        for user in message.mentions:
                            if str(user.id) == gene_seed_carrier_id:
                                try:
                                    gene_seed_carried_name = user.nick
                                except AttributeError:
                                    _g.logger.debug(f"Failed to get nickname for user ID {gene_seed_carrier_id}")
                    # Otherwise leave as unknown (tag with other random text)

        # Check if any Initiation Trial or Neophyte role is mentioned ON THIS LINE
        for role in message.role_mentions:
            # Only process if role mention is actually on this line
            role_pattern = f"<@&{role.id}>"
            if role_pattern not in raw_line:
                continue
            # Detect Initiation Trial role or Neophyte role (ID 1434942334914662501)
            if role.name == "Initiation Trial" or role.id == 1434942334914662501:
                initiation_trial = True
                # Capture up to 2 initiate mentions on the same line
                ids_here = get_user_ids_in_line(raw_line, message)
                for uid in ids_here[:2]:
                    if uid not in initiate_ids:
                        initiate_ids.append(uid)
                    if len(initiate_ids) >= 2:
                        break

        # Detect explicit "Initiation Trial:" header and capture initiate mentions
        # This handles text like "@Initiation Trial: @inductee1 @inductee2" after role resolution
        if "initiation trial" in lower:
            initiation_trial = True
            # Capture up to 2 initiates on the same line as the header
            ids_here = get_user_ids_in_line(raw_line, message)
            for uid in ids_here[:2]:
                if uid not in initiate_ids:
                    initiate_ids.append(uid)
                if len(initiate_ids) >= 2:
                    break

        # Detect Trial: lines (e.g. 'Trial: 1/1' or 'Trial: -/3') - just marks the trial flag
        # Don't capture inductees here since they're on the @Initiation Trial line
        if lower.startswith("trial:"):
            initiation_trial = True

        # Watch Command marker sometimes present on trial templates (deprecated persistence)
        if "watch command" in lower:
            # Deprecated: ignore trial template markers for persistence
            pass

        elif lower.startswith("brothers") or lower.startswith("team"):
            # Brothers/Team can appear on the same line as the header; include this line
            brothers_start_idx = i

        elif lower.startswith("waves:") or lower.startswith("wave:"):
            # Legacy/global waves support (non-siege or old format)
            parts = line.split(":", 1)
            try:
                waves = int(parts[1].strip())
            except Exception:
                waves = None

    difficulty_class = classify_difficulty(difficulty)
    points_for_op = compute_points_for_op(difficulty_class, waves)
    gene_seed_base_points_for_carrier = 0
    if gene_seed_status == "carried":
        gene_seed_base_points_for_carrier = compute_gene_seed_base_points_for_carrier(difficulty_class)
    # Omega ops: subtract KIA from the base 20 points (floor at 0)
    try:
        if difficulty_class == "omega_ops":
            points_for_op = max(0, int(points_for_op) - int(kia_count))
    except Exception:
        pass

    def role_mentioned(message, *, role_id=None, role_name=None, name_contains=None):
        """
        Check whether a role matching the given criteria is mentioned in the message.

        :param message: Discord message object with a role_mentions attribute.
        :param role_id: Optional int or str role ID to match.
        :param role_name: Optional canonical role name (case-insensitive).
        :param name_contains: Optional iterable of substrings that must all be present
                             in the role name (case-insensitive).
        :return: True if a matching role is found, False otherwise.
        """
        try:
            roles = getattr(message, "role_mentions", [])
        except Exception:
            return False

        try:
            for role in roles:
                try:
                    rn = (getattr(role, "name", "") or "").strip().lower()
                    rid = getattr(role, "id", None)

                    # Match by role ID (accept either int or string form)
                    if role_id is not None:
                        if rid == role_id or str(rid) == str(role_id):
                            return True

                    # Match by exact canonical role name (case-insensitive)
                    if role_name is not None:
                        if rn == role_name.strip().lower():
                            return True

                    # Match by all required substrings in the role name
                    if name_contains:
                        try:
                            if all(token in rn for token in name_contains):
                                return True
                        except Exception:
                            # If name_contains is not iterable or another error occurs, ignore.
                            pass
                except Exception:
                    # Ignore issues with individual role objects and continue scanning.
                    continue
        except Exception:
            return False

        return False

    # Detect Chapter Approved role mention anywhere in the message.
    chapter_approved = role_mentioned(
        message,
        role_id=1467960627795464344,
        role_name="chapter approved",
    )

    # Detect Black Laurels role mention anywhere in the message.
    # Track if it's in difficulty/mission lines OR mentioned as a role elsewhere.
    black_laurels_role_mentioned = role_mentioned(
        message,
        name_contains=("black", "laurel"),
    )
    if black_laurels_role_mentioned and not black_laurels_in_difficulty and not black_laurels_in_mission:
        black_laurels_mentioned_elsewhere = True

    # Detect Pipehitter role mentions anywhere in the message.
    pipehitter_mentioned = role_mentioned(
        message,
        role_id=PIPEHITTER_ROLE_ID,
    ) or role_mentioned(
        message,
        role_id=DISTINGUISHED_PIPEHITTER_ROLE_ID,
    )

    # Detect Watch Command role mention anywhere in the message (required for Initiation Trials).
    watch_command_mentioned = role_mentioned(
        message,
        role_id=WATCH_COMMAND_ROLE_ID,
        role_name="watch command",
    )

    # If Chapter Approved tag present, apply +1 point only when the AAR
    # is recorded on the 1st or 3rd weekend (Saturday or Sunday) of the month.
    try:
        if chapter_approved and getattr(message, "created_at", None):
            dt = message.created_at
            # weekday(): Monday=0 .. Sunday=6 ; Saturday == 5, Sunday == 6
            day = getattr(dt, "day", None)
            wd = getattr(dt, "weekday", lambda: None)()
            if wd in (5, 6) and day is not None and ((1 <= day <= 8) or (15 <= day <= 22)):
                try:
                    points_for_op = int(points_for_op) + 1
                    chapter_approved_extra_point_applied = True
                except Exception:
                    pass
    except Exception:
        pass

    # Collect Brothers from the "Brothers:" line and subsequent lines until END OF REPORT
    if brothers_start_idx is not None:
        for raw_line in lines[brothers_start_idx:]:
            line = raw_line.strip()
            if "++ end of report ++" in line.lower() or "ᴇɴᴅ ᴏғ ʀᴇᴘᴏʀᴛ" in line:
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
                                _g.logger.debug(f"Failed to get nickname for user/ID {user.name}/{uid}")
                # Try to parse per-brother waves from the same line, expecting an integer
                try:
                    # Find last integer token in the line
                    tokens = [t for t in line.replace("/", " ").split()]
                    nums = [int(t) for t in tokens if t.isdigit()]
                    if nums:
                        brother_waves[uid] = nums[-1]
                except Exception:
                    pass

    # Always return a record, even if Brothers section is missing; validation will handle errors
    return {
        "aar_id": aar_id,
        "content": content,  # Store full message content for resilience against deletion
        "mission": mission,
        "difficulty": difficulty,
        "difficulty_class": difficulty_class,
        # deprecated: removed from persisted record
        "armory_data": armory_data,
        "armory_challenge_points": compute_armory_bonus_points(difficulty_class, armory_data),
        "gene_seed_status": gene_seed_status,
        "gene_seed_carrier_id": gene_seed_carrier_id,
        "gene_seed_carried_name": gene_seed_carried_name,
        "gene_seed_base_points_for_carrier": gene_seed_base_points_for_carrier,
        "brother_ids": brothers_ids,
        "brother_names": brother_names,
        "brother_waves": brother_waves,
        "waves": waves,
        "killed_in_action": kia_count if difficulty_class == "omega_ops" else 0,
        "kia_line_present": kia_line_present,
        "points_for_op": points_for_op,
        "timestamp": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if getattr(message, "edited_at", None) else None,
        "content_hash": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        "initiation_trial": initiation_trial,
        "initiate_ids": initiate_ids,
        # Legacy field for backward compat with old records
        "initiate_id": initiate_ids[0] if initiate_ids else None,
        "watch_command_mentioned": watch_command_mentioned,
        "chapter_approved": chapter_approved,
        "chapter_approved_extra_point_applied": chapter_approved_extra_point_applied,
        # Black Laurels tracking for validation
        "black_laurels_in_difficulty": black_laurels_in_difficulty,
        "black_laurels_in_mission": black_laurels_in_mission,
        "black_laurels_mentioned_elsewhere": black_laurels_mentioned_elsewhere,
        "leviathan_protocol_in_mission": leviathan_protocol_in_mission,
        "leviathan_protocol_in_difficulty": leviathan_protocol_in_difficulty,
        # Black Reef Persecution tracking for validation
        "black_reef_persecution_in_mission": black_reef_persecution_in_mission,
        # Pipehitter tracking for validation
        "pipehitter_mentioned": pipehitter_mentioned,
        # Link back to the original Discord message (if available)
        "message_url": (
            f"https://discord.com/channels/{getattr(getattr(message, 'guild', None), 'id', None)}/"
            f"{getattr(getattr(message, 'channel', None), 'id', None)}/{message.id}"
            if getattr(getattr(message, "guild", None), "id", None)
            and getattr(getattr(message, "channel", None), "id", None)
            else None
        ),
        # ID of the member who posted the AAR (for verifier tier bonus)
        "submitter_id": str(message.author.id),
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
    brother_waves = record.get("brother_waves") or {}
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
            errors.append("Mission must not include trial-style progress tokens like 'n/m' or '-/m'.")
        # Enforce canonical mission names for non-siege ops (case-insensitive).
        # Allowable missions:
        # Inferno, Decapitation, Vox Liberatis, Reliquary, Fall of Atreus,
        # Ballistic Engine, Termination, Obelisk, Vortex, Reclamation,
        # Disruption, Exfiltration
        try:
            if not is_siege:
                allowed_missions = {
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
                # Strip any trailing role/mention tokens (e.g., '<@&...>') and BOMs
                mclean = re.sub(r"<.*", "", mstr or "").strip()
                mclean = mclean.replace("\ufeff", "").strip()
                if mclean and mclean.lower() not in allowed_missions:
                    errors.append(f"Mission '{mclean}' is not a recognized mission name.")
        except Exception:
            pass

    # 2) Difficulty must be one of the known tags
    dlower = difficulty.lower()
    known_tags = [
        "ruthless",
        "lethal",
        "absolute",
        "omega",
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
        # Check if we're in the grace period (before Feb 20, 2026)
        is_in_grace_period = True
        try:
            timestamp_str = record.get("timestamp", "")
            if timestamp_str:
                # Parse ISO format timestamp
                message_created_at = datetime.fromisoformat(timestamp_str)
                if message_created_at >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
                    is_in_grace_period = False
        except Exception:
            # If we can't parse timestamp, assume grace period is still active
            pass

        # Black Laurels validation
        has_black_laurels_difficulty = "black" in dlower and "laurel" in dlower
        has_black_laurels_mission = record.get("black_laurels_in_mission", False)
        has_absolute = "absolute" in dlower
        has_omega = "omega" in dlower
        has_hard_stratagem = "hard-stratagem" in dlower
        has_black_reef_persecution = record.get("black_reef_persecution_in_mission", False)
        # Black Reef Persecution on Mission line unlocks Black Laurels with Hard-Stratagem
        bl_hard_strat_unlocked = has_hard_stratagem and has_black_reef_persecution

        if has_black_laurels_difficulty or has_black_laurels_mission:
            # Black Laurels on Omega requires 5 brothers and 0 KIA
            # Black Laurels on Absolute requires exactly 3 brothers
            # Black Laurels with Black Reef Persecution (Hard-Stratagem) requires exactly 2 brothers
            if has_omega:
                if len(brothers) != 5:
                    errors.append("@Black_Laurels on @Omega requires exactly 5 Brothers (full squad).")
                kia = record.get("killed_in_action", 0)
                if kia != 0:
                    errors.append("@Black_Laurels on @Omega requires 0 KIA (no deaths).")
            elif bl_hard_strat_unlocked:
                if len(brothers) not in (2, 3):
                    errors.append("@Black_Laurels with @Black_Reef_Persecution requires 2 or 3 Brothers.")
            else:
                if len(brothers) != 3:
                    errors.append("@Black_Laurels requires exactly 3 Brothers (a full fireteam).")
            if is_in_grace_period:
                # GRACE PERIOD (before Feb 20, 2026): Allow Black Laurels on Mission OR Difficulty
                # Only check: must have @Absolute or @Omega when Black Laurels is present
                if not has_absolute and not has_omega and not bl_hard_strat_unlocked:
                    errors.append("@Black_Laurels requires @Absolute or @Omega on the Difficulty line.")
                # Check eligible missions (Omega and BRP+Hard-Strat allow any mission)
                if not has_omega and not bl_hard_strat_unlocked:
                    mission_lower = (mission or "").lower().strip()
                    mission_clean = re.sub(r"<.*", "", mission_lower).strip()
                    if mission_clean and mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS:
                        errors.append(
                            "@Black_Laurels may only be used on eligible missions: "
                            "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                            "Exfiltration, Termination, Reclamation, Disruption."
                        )
            else:
                # STRICT MODE (Feb 20, 2026+): Black Laurels ONLY on Mission line with @Absolute/@Omega on Difficulty
                # Exception: @Hard-Stratagem is also allowed when @Black_Reef_Persecution is on the Mission line
                if has_black_laurels_difficulty and not has_black_laurels_mission:
                    errors.append("@Black_Laurels must be placed on the Mission line only.")
                if not has_absolute and not has_omega and not bl_hard_strat_unlocked:
                    errors.append(
                        "@Black_Laurels requires @Absolute or @Omega on the Difficulty line "
                        "(or @Hard-Stratagem when @Black_Reef_Persecution is on the Mission line)."
                    )
                # Check eligible missions (Omega and BRP+Hard-Strat allow any mission)
                if not has_omega and not bl_hard_strat_unlocked:
                    mission_lower = (mission or "").lower().strip()
                    mission_clean = re.sub(r"<.*", "", mission_lower).strip()
                    if mission_clean and mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS:
                        errors.append(
                            "@Black_Laurels may only be used on eligible missions: "
                            "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                            "Exfiltration, Termination, Reclamation, Disruption."
                        )
                # Black Laurels cannot be mentioned elsewhere in strict mode
                if record.get("black_laurels_mentioned_elsewhere", False):
                    errors.append("@Black_Laurels must be placed on the Mission line, not elsewhere in the AAR.")

        # Leviathan Protocol validation: must be on Mission line only
        leviathan_in_difficulty = record.get("leviathan_protocol_in_difficulty", False)
        _leviathan_in_mission = record.get("leviathan_protocol_in_mission", False)  # Reserved for future validation
        if leviathan_in_difficulty:
            errors.append("@Leviathan_Protocol must be placed on the Mission line, not the Difficulty line.")

        # Pipehitter validation: only allowed on eligible missions
        if record.get("pipehitter_mentioned", False):
            mission_lower = (mission or "").lower().strip()
            mission_clean = re.sub(r"<.*", "", mission_lower).strip()
            if mission_clean and mission_clean not in PIPEHITTER_ELIGIBLE_MISSIONS:
                errors.append(
                    "@Pipehitter/@Distinguished_Pipehitter may only be used on eligible missions: "
                    "Inferno, Vox Liberatis, Reliquary, Fall of Atreus, Termination, Obelisk, "
                    "Exfiltration, Vortex, Reclamation, Disruption."
                )

    # 3) Siege must have waves data. Accept either global 'Waves:' or per-brother waves parsed from Team lines.
    if "normal-siege" in dlower or "hard-siege" in dlower:
        global_ok = False
        if waves is not None:
            try:
                int(waves)
                global_ok = True
            except (TypeError, ValueError):
                errors.append("Waves value could not be parsed as an integer.")
        per_bro_ok = any(isinstance(v, int) for v in brother_waves.values())
        if not (global_ok or per_bro_ok):
            errors.append("Siege requires waves data: provide 'Waves:' or per-brother counts after mentions.")

    # 4) Armory/Armoury Data required and numeric
    if armory_data is None:
        errors.append("Armory/Armoury Data line is missing.")
    else:
        try:
            int(armory_data)
        except ValueError:
            errors.append("Armory/Armoury Data must be an integer (e.g. 3).")

    # 5) Brother count requirements
    # Special-case: Omega requires 2-5 brothers; all others require 2-3
    if "omega" in dlower:
        if not (2 <= len(brothers) <= 5):
            errors.append("Omega difficulty requires between 2 and 5 Brothers listed under the 'Brothers:' section.")
        # Omega ops must have an explicit KIA line
        if not record.get("kia_line_present", False):
            errors.append("Omega difficulty requires an explicit 'KIA:' line (e.g. 'KIA: 0' or 'KIA: 1').")
    else:
        if len(brothers) < 2:
            errors.append("At least two Brothers must be listed under the 'Brothers:' section.")
        elif len(brothers) > 3:
            errors.append("Non-Omega operations allow a maximum of 3 Brothers (a full kill team).")

    # 6) Initiation Trial placement rules (simplified)
    if record.get("initiation_trial"):
        # Check both initiate_ids (new) and initiate_id (legacy) for backward compat
        has_initiates = bool(record.get("initiate_ids")) or bool(record.get("initiate_id"))
        if not has_initiates:
            errors.append("Initiation Trial present but no initiate mention found; include the person being initiated.")
        # Watch Command role must be mentioned for Initiation Trials
        if not record.get("watch_command_mentioned"):
            errors.append("Initiation Trial requires @Watch Command to be mentioned.")

    # 7) Gene-seed logic
    allowed_statuses = {"lost", "carried", "unknown"}
    if gene_status not in allowed_statuses:
        errors.append("Gene-Seed status must be 'lost', 'carried', or omitted (which becomes 'unknown').")

    if gene_status == "carried":
        if gene_carrier is None:
            errors.append("Gene-Seed is 'carried' but no carrier is mentioned.")
        elif gene_carrier not in brothers:
            errors.append("Gene-Seed carrier must also be listed under 'Brothers:'.")

    return errors


# Deprecated: replaced by DataStore
def load_aar_data(filename: str):
    # Use _g.DATASTORE for AAR_RECORDS_PATH
    if filename == AAR_RECORDS_PATH:
        # Return a dict for compatibility
        return {str(k): v for k, v in _g.DATASTORE._records.items()}
    # Fallback to old logic for other files (should not be used)
    try:
        with open(filename, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


# Deprecated: replaced by DataStore for AAR_RECORDS_PATH
def _load_json_dict(path: str):
    if path == AAR_RECORDS_PATH:
        return {str(k): v for k, v in _g.DATASTORE._records.items()}
    # For AAR_ERRORS_PATH and others, keep old logic
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


# Only used for files other than AAR_RECORDS_PATH
def _save_json_dict(path: str, data: dict):
    if path == AAR_RECORDS_PATH:
        raise RuntimeError("Direct writes to AAR_RECORDS_PATH are not allowed; use DataStore.set_record.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


# Deprecated: replaced by DataStore for PROCESSED_IDS_PATH
def _load_json_list(path: str):
    if path == PROCESSED_IDS_PATH:
        return list(_g.DATASTORE._processed_ids)
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


# Only used for files other than PROCESSED_IDS_PATH
def _save_json_list(path: str, data: list):
    if path == PROCESSED_IDS_PATH:
        raise RuntimeError("Direct writes to PROCESSED_IDS_PATH are not allowed; use DataStore.add_processed_id.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


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
    sid = str(aar_id)
    existing = data.get(sid) if isinstance(data, dict) else None
    entry = {
        "errors": errors,
        "author": _author_info_from_message(msg),
        "content": msg.content[:2000] if msg.content else "",
        "timestamp": msg.created_at.isoformat() if msg.created_at else None,
    }
    # Preserve reply_id if present so we don't lose reference to previous bot reply
    try:
        if isinstance(existing, dict) and existing.get("reply_id"):
            entry["reply_id"] = existing.get("reply_id")
    except Exception:
        pass
    data[sid] = entry
    _save_json_dict(AAR_ERRORS_PATH, data)


async def _reply_aar_rejection(msg: discord.Message, errors: list[str]):
    """Attempt to reply to the original AAR message with a concise rejection reason.
    This is best-effort: failures are logged and ignored so they don't break the
    ingest/recheck flow."""
    try:
        if not msg:
            return
        # Filter and format errors: avoid including jump URLs or huge stacks
        filtered = [e for e in errors if e and not e.startswith("Jump URL:")]
        if not filtered:
            filtered = errors[:1] if errors else ["Rejected by archive bot."]
        # Limit to a few lines for readability
        max_lines = 6
        lines = ["Your After-Action Report was rejected by the archive bot for the following reason(s):"]
        for e in filtered[:max_lines]:
            lines.append(f"- {e}")
        content = "\n".join(lines)
        # Keep comfortably under Discord message limits
        if len(content) > 1900:
            content = content[:1900].rsplit("\n", 1)[0] + "\n…"
        # Load current stored error entry (if any) so we can deduplicate / edit
        try:
            data = _load_json_dict(AAR_ERRORS_PATH)
        except Exception:
            data = {}

        sid = str(getattr(msg, "id", ""))
        existing = data.get(sid) if isinstance(data, dict) else None
        reply_id = existing.get("reply_id") if isinstance(existing, dict) else None

        if reply_id:
            # Try to fetch the stored reply; if it exists, prefer updating it
            # to avoid duplicates. However, editing does not notify the user,
            # so if the existing reply does not mention the author, also send
            # a short ping so the author receives a notification.
            try:
                try:
                    reply_msg = await msg.channel.fetch_message(int(reply_id))
                except Exception:
                    reply_msg = None
                if reply_msg:
                    try:
                        await reply_msg.edit(content=content)
                        # Update stored errors in case they changed
                        data[sid]["errors"] = filtered[:max_lines]
                        _save_json_dict(AAR_ERRORS_PATH, data)

                        # Preserve author mention when editing existing bot replies.
                        # If the stored error entry includes an author id and the
                        # new content does not contain that mention, prefix the
                        # edited content with the mention so the visible reply
                        # continues to include the author tag.
                        try:
                            entry = data.get(sid) if isinstance(data, dict) else None
                            author_info = entry.get("author") if isinstance(entry, dict) else None
                            author_id = author_info.get("id") if isinstance(author_info, dict) else None
                        except Exception:
                            author_id = None
                        try:
                            if author_id and f"<@{author_id}>" not in (reply_msg.content or ""):
                                try:
                                    new_content = f"<@{author_id}>\n{content}"
                                    await reply_msg.edit(content=new_content)
                                    # persist updated errors and reply id
                                    data[sid]["errors"] = filtered[:max_lines]
                                    _save_json_dict(AAR_ERRORS_PATH, data)
                                except Exception:
                                    pass
                            else:
                                # no author to preserve or already present; nothing more to do
                                pass
                        except Exception:
                            pass

                        return
                    except Exception:
                        # If edit fails, continue to attempt sending a new reply
                        pass
            except Exception:
                # any unexpected failure - continue to send a new reply
                pass

        # No existing reply found or edit failed: send a new reply and record its id
        # Before sending a new reply, scan recent channel messages to see if the
        # bot already posted a reply to this AAR (possible if reply_id was not
        # recorded or is stale). If found, edit that message instead of sending
        # a new one to avoid duplicates.
        try:
            existing_reply = None
            try:
                async for recent in msg.channel.history(limit=64):
                    try:
                        ref = getattr(recent, "reference", None)
                        if not ref:
                            continue
                        if getattr(ref, "message_id", None) == getattr(msg, "id", None):
                            if getattr(recent.author, "id", None) == getattr(_g.bot.user, "id", None):
                                existing_reply = recent
                                break
                    except Exception:
                        continue
            except Exception:
                existing_reply = None
            if existing_reply:
                try:
                    await existing_reply.edit(content=content)
                    # Update stored reply_id for this AAR
                    sid = str(getattr(msg, "id", ""))
                    ent = data.get(sid) or {}
                    ent["errors"] = filtered[:max_lines]
                    ent["author"] = _author_info_from_message(msg)
                    try:
                        ent["reply_id"] = str(getattr(existing_reply, "id", ""))
                    except Exception:
                        ent["reply_id"] = None
                    data[sid] = ent
                    try:
                        _save_json_dict(AAR_ERRORS_PATH, data)
                    except Exception:
                        pass
                    # Preserve author mention when editing existing bot replies.
                    try:
                        sid = str(getattr(msg, "id", ""))
                        entry = data.get(sid) if isinstance(data, dict) else None
                        author_info = entry.get("author") if isinstance(entry, dict) else None
                        author_id = author_info.get("id") if isinstance(author_info, dict) else None
                    except Exception:
                        author_id = None
                    try:
                        if author_id and f"<@{author_id}>" not in (existing_reply.content or ""):
                            try:
                                new_content = f"<@{author_id}>\n{content}"
                                await existing_reply.edit(content=new_content)
                                sid = str(getattr(msg, "id", ""))
                                ent = data.get(sid) or {}
                                ent["errors"] = filtered[:max_lines]
                                ent["author"] = _author_info_from_message(msg)
                                try:
                                    ent["reply_id"] = str(getattr(existing_reply, "id", ""))
                                except Exception:
                                    ent["reply_id"] = None
                                data[sid] = ent
                                try:
                                    _save_json_dict(AAR_ERRORS_PATH, data)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        else:
                            # Either no author info or mention already present; nothing to do
                            pass
                    except Exception:
                        pass
                    return
                except Exception:
                    # fall through to sending a new reply
                    pass

            sent = None
            # Send a new reply and ensure the author is mentioned so they
            # receive a notification. Use an explicit mention prefix and
            # set allowed_mentions to permit user pings (safer than relying
            # on `mention_author=True` which can be affected by global
            # allowed-mentions settings).
            try:
                author_id = getattr(msg.author, "id", "")
                mention_prefix = f"<@{author_id}>\n" if author_id else ""
                sent = await msg.reply(
                    mention_prefix + content,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                # Last-resort fallback: try replying without explicit allowed_mentions
                try:
                    sent = await msg.reply(f"<@{getattr(msg.author, 'id', '')}>\n{content}")
                except Exception:
                    sent = None
            if sent and isinstance(data, dict):
                sid = str(getattr(msg, "id", ""))
                # Ensure there's an entry for this aar in the errors file
                ent = data.get(sid) or {}
                ent["errors"] = filtered[:max_lines]
                ent["author"] = _author_info_from_message(msg)
                try:
                    ent["reply_id"] = str(getattr(sent, "id", ""))
                except Exception:
                    ent["reply_id"] = None
                data[sid] = ent
                try:
                    _save_json_dict(AAR_ERRORS_PATH, data)
                except Exception:
                    pass
        except Exception as e:
            try:
                _g.logger.debug(f"Failed to reply to AAR {getattr(msg, 'id', None)}: {e}")
            except Exception:
                pass
    except Exception as e:
        try:
            _g.logger.debug(f"Failed to reply to AAR {getattr(msg, 'id', None)}: {e}")
        except Exception:
            pass


def _snowflake_to_datetime(snowflake_id: int) -> datetime:
    """Extract the creation datetime from a Discord snowflake ID."""
    # Discord epoch: January 1, 2015 00:00:00 UTC
    discord_epoch = 1420070400000
    timestamp_ms = (snowflake_id >> 22) + discord_epoch
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def summarize_error_authors(max_age_weeks: int = 4):
    """Return a tuple: (list of author summaries for recent errors, stale_count).

    Recent errors are those from the last max_age_weeks.
    Stale errors are older than max_age_weeks.

    Each author entry: {"id": str, "username": str|None, "nickname": str|None, "count": int}
    """
    data = _load_json_dict(AAR_ERRORS_PATH)
    by_author: dict[str, dict] = {}
    stale_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=max_age_weeks)

    for aar_id_str, entry in data.items():
        # Check if this error is stale (older than cutoff)
        try:
            aar_id = int(aar_id_str)
            msg_time = _snowflake_to_datetime(aar_id)
            if msg_time < cutoff:
                stale_count += 1
                continue  # Skip stale entries from author breakdown
        except (ValueError, TypeError):
            pass  # If we can't parse ID, include it in recent

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
    summaries.sort(key=lambda x: (-x["count"], (x["nickname"] or x["username"] or "").lower()))
    return summaries, stale_count


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
        _g.logger.debug(f"Failed to set reaction on message {msg.id}: {e}")


# Use DataStore for processed IDs
def load_processed_ids():
    return set(_g.DATASTORE._processed_ids)


# Use DataStore for processed IDs (async)
async def add_processed_id(aar_id: int):
    await _g.DATASTORE.add_processed_id(aar_id)


# Use DataStore for AAR records and processed IDs (async)
async def save_aar_record(record: dict):
    key = str(record["aar_id"])

    # Apply verifier tier bonus to points_for_op if the submitter has earned one
    submitter_id = record.get("submitter_id")
    if submitter_id:
        try:
            from . import terminus_ops as _terminus
            bonus = _terminus.get_verifier_tier_bonus(submitter_id)
            if bonus > 0:
                record["points_for_op"] = (record.get("points_for_op") or 0) + bonus
                record["verifier_tier_bonus"] = bonus
        except Exception:
            pass  # never block a save on a bonus lookup failure

    await _g.DATASTORE.set_record(key, record)
    await _g.DATASTORE.add_processed_id(key)
    # Add armory points to the community forge pool
    armory_pts = record.get("armory_challenge_points", 0) or 0
    if armory_pts > 0:
        increment_forge_pool_balance = _b("_increment_forge_pool_balance")
        if increment_forge_pool_balance is None:
            raise NameError("_increment_forge_pool_balance is not available in aar_ops.py or bot")
        await increment_forge_pool_balance(armory_pts)


# Use DataStore for processed IDs
def has_been_processed(aar_id: int):
    return _g.DATASTORE.is_processed(aar_id)


# Use DataStore user_stats_cache for user stats


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
    "_get_challenge_librarian_mention",
    "_get_challenge_keeper_mention",
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
