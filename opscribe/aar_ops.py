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


def _normalize_challenge_mission_name(raw_mission: str) -> str:
    """Normalize mission text for challenge set comparisons.

    Handles role mentions and inline tag suffixes such as '@Black Laurels'.
    """
    mission = (raw_mission or "").lower().strip()
    if not mission:
        return ""

    mission = re.sub(r"<@&\d+>", "", mission).strip()
    mission = re.split(r"\s*@", mission, maxsplit=1)[0].strip()
    mission = re.sub(
        r"\b(black\s+laurels|dual\s+vigil|leviathan\s+protocol|black\s+reef\s+persecution)\b",
        "",
        mission,
    )
    mission = re.sub(r"\s+", " ", mission).strip(" -|:,;\t")
    return mission


def _normalize_progress_entries(entries: list) -> tuple[list, bool]:
    """Normalize + de-duplicate mission entries in challenge progress lists.
    
    De-duplicates by AAR ID (not mission name) to preserve multiple AAR submissions
    of the same mission type (e.g., multiple Termination runs with different outcomes).
    This is critical for Herisor medal tracking which requires tracking individual
    AAR completions with their Black Laurels status.
    """
    changed = False
    normalized: list = []
    seen_aar_ids: set[str] = set()

    for entry in entries or []:
        if not isinstance(entry, dict) or "mission" not in entry:
            normalized.append(entry)
            continue

        old_mission = str(entry.get("mission") or "")
        new_mission = _normalize_challenge_mission_name(old_mission) or old_mission
        if new_mission != old_mission:
            changed = True

        new_entry = dict(entry)
        new_entry["mission"] = new_mission

        # De-duplicate by AAR ID, not mission name. This preserves multiple AAR
        # submissions for the same mission type while removing true duplicates.
        aar_id_str = str(new_entry.get("aar_id", ""))
        if aar_id_str and aar_id_str in seen_aar_ids:
            changed = True
            continue
        if aar_id_str:
            seen_aar_ids.add(aar_id_str)
        normalized.append(new_entry)

    return normalized, changed


async def _process_challenge_tracking(record: dict, guild: discord.Guild) -> List[Tuple[str, str, int, str, List[str]]]:
    """Process an AAR record for challenge progress tracking.

    Returns list of (user_id, challenge_display_name, role_id, award_type, aar_urls)
    tuples for newly qualified members. Only returns each challenge once per
    member (won't notify again).

    The bot only returns notifications that include a valid role_id and
    award_type for auto-assignment and public award announcement dispatch.
    """
    notifications = []

    # PvP AARs do not contribute to PvE campaign/challenge progress.
    if (record.get("aar_type") or "").lower() == "pvp":
        return notifications

    # Extract AAR fields
    # Strip role ID mentions (e.g., "<@&123456>") from mission name before comparisons so that
    # missions like "Inferno <@&1435812894532042843>" match the clean set entries like "inferno".
    _raw_mission = record.get("mission") or record.get("mission_name") or ""
    mission_name = _normalize_challenge_mission_name(_raw_mission)
    brother_ids = record.get("brother_ids", [])
    aar_id = record.get("aar_id") or record.get("id", "")
    message_url = record.get("message_url", "")
    timestamp = record.get("timestamp", "")

    # Tag detection
    pipehitter_mentioned = record.get("pipehitter_mentioned", False)
    leviathan_protocol = record.get("leviathan_protocol_in_mission", False)
    black_reef_persecution = record.get("black_reef_persecution_in_mission", False)
    # Black Laurels may appear on either the Mission or Difficulty line.
    # Legacy wave-based Herisor reports can also surface the tag via the
    # parser's fallback flag, so include that for the Herisor award path.
    black_laurels = (
        record.get("black_laurels_in_mission", False)
        or record.get("black_laurels_in_difficulty", False)
        or record.get("black_laurels_mentioned_elsewhere", False)
    )
    # Dual Vigil tag must be on the Mission line; tracked separately from Black Laurels.
    dual_vigil = record.get("dual_vigil_in_mission", False)
    # Defense of Herisor tag must be on Mission line (mention-only).
    herisor_defense = record.get("herisor_defense_in_mission", False)
    difficulty_class = record.get("difficulty_class") or ""
    waves = record.get("waves")

    # Skip if no participants. Mission can be omitted for Herisor Hard-Siege.
    if not brother_ids:
        return notifications
    if not mission_name and not (herisor_defense and difficulty_class == "hard_siege"):
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

            # Sanitize stored mission entries so legacy tag-suffixed missions
            # (e.g., "purgation @black laurels") still count correctly.
            for _k in (
                "sok_g_pipehitter",
                "kadaku_campaign",
                "distinguished_kadaku",
                "black_reef",
                "distinguished_black_reef",
                "dual_vigil",
                "black_laurels",
                "order_omega",
                "herisor_defense_siege",
                "herisor_defense_termination",
                "herisor_defense_reclamation",
            ):
                _entries = user_progress.get(_k)
                if isinstance(_entries, list):
                    _normalized, _changed = _normalize_progress_entries(_entries)
                    if _changed:
                        user_progress[_k] = _normalized

            # Keep challenge progress in sync when an already-accepted AAR is edited:
            # remove any previous contribution for this same AAR ID, then re-apply
            # the current parsed/validated record below.
            _aar_id_str = str(aar_id)
            for _k in (
                "sok_g_pipehitter",
                "kadaku_campaign",
                "distinguished_kadaku",
                "black_reef",
                "distinguished_black_reef",
                "dual_vigil",
                "black_laurels",
                "order_omega",
                "herisor_defense_siege",
                "herisor_defense_termination",
                "herisor_defense_reclamation",
                "crux_bl_aars",
            ):
                _entries = user_progress.get(_k)
                if isinstance(_entries, list):
                    _pruned = [
                        e for e in _entries
                        if not (isinstance(e, dict) and str(e.get("aar_id", "")) == _aar_id_str)
                    ]
                    if len(_pruned) != len(_entries):
                        user_progress[_k] = _pruned
            
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
                            "Watch Veteran", "Oathsworn", "Bladeguard",
                            "Watch Sergeant", "Veteran Sergeant", "Watch Techmarine", "Watch Librarian",
                            "Watch Apothecary", "Watch Chaplain", "Watch Keeper",
                            "First Blade", "Watch Lieutenant", "Watch Captain",
                            "Venerable Dreadnought", "Honored Dreadnought", "Forgemaster",
                            "Void Warden", "High Chaplain", "Chief Apothecary",
                            "Castellan", "Blade Master", "Watch Master",
                        )
                    )
                )

            # === SOK-G: Pipehitter tracking ===
            # Pipehitter challenges require Hard-Stratagem difficulty AND Rank A.
            # Legacy AARs that pre-date the rank field are treated as Rank A.
            _aar_rank = (record.get("rank") or "A").upper()
            if (
                pipehitter_mentioned
                and mission_name in PIPEHITTER_ELIGIBLE_MISSIONS
                and difficulty_class == "hard_stratagem"
                and _aar_rank == "A"
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

                    user_progress["sok_g_pipehitter"].append(
                        {
                            "mission": mission_name,
                            "aar_id": aar_id,
                            "message_url": message_url,
                            "timestamp": timestamp,
                        }
                    )

                    # Check if qualified for SOK-G: Pipehitter (1 op)
                    pip_entries = user_progress["sok_g_pipehitter"]
                    if (
                        len(pip_entries) >= 1
                        and "sok_g_pipehitter" not in notified_challenges
                        and is_watch_brother_or_higher
                        and not discord.utils.get(member.roles, id=PIPEHITTER_ROLE_ID)
                    ):
                        aar_urls = [m["message_url"] for m in pip_entries if m["message_url"]]
                        notifications.append((user_id_str, "SOK-G: Pipehitter", PIPEHITTER_ROLE_ID, "sok_g_pipehitter", aar_urls))
                        notified_challenges.append("sok_g_pipehitter")

                    # Check if qualified for Distinguished SOK-G: Pipehitter (2+ ops)
                    if (
                        len(pip_entries) >= 2
                        and "distinguished_sok_g_pipehitter" not in notified_challenges
                        and is_watch_brother_or_higher
                        and not discord.utils.get(member.roles, id=DISTINGUISHED_PIPEHITTER_ROLE_ID)
                    ):
                        aar_urls = [m["message_url"] for m in user_progress["sok_g_pipehitter"] if m["message_url"]]
                        notifications.append((user_id_str, "Distinguished SOK-G: Pipehitter", DISTINGUISHED_PIPEHITTER_ROLE_ID, "distinguished_pipehitter", aar_urls))
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
                    notifications.append((user_id_str, "Kadaku Campaign Medal", KADAKU_CAMPAIGN_MEDAL_ROLE_ID, "kadaku_campaign_medal", aar_urls))
                    notified_challenges.append("kadaku_campaign")

            # === Distinguished Kadaku Campaign Medal tracking ===
            if leviathan_protocol and black_laurels and mission_name in KADAKU_CAMPAIGN_REQUIRED_MISSIONS:
                if "distinguished_kadaku" not in user_progress:
                    user_progress["distinguished_kadaku"] = []

                existing_missions = {m["mission"] for m in user_progress["distinguished_kadaku"]}
                if mission_name not in existing_missions:
                    user_progress["distinguished_kadaku"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )

                unique_missions = {m["mission"] for m in user_progress["distinguished_kadaku"]}
                if (
                    len(unique_missions) >= len(KADAKU_CAMPAIGN_REQUIRED_MISSIONS)
                    and unique_missions == KADAKU_CAMPAIGN_REQUIRED_MISSIONS
                    and "distinguished_kadaku" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["distinguished_kadaku"] if m["message_url"]]
                    notifications.append(
                        (
                            user_id_str,
                            "Distinguished Kadaku Campaign Medal",
                            DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID,
                            "distinguished_kadaku_campaign_medal",
                            aar_urls,
                        )
                    )
                    notified_challenges.append("distinguished_kadaku")

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
                    notifications.append((user_id_str, "Black Reef Campaign Medal", BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID, "black_reef_campaign_medal", aar_urls))
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
                    notifications.append((user_id_str, "Distinguished Black Reef Campaign Medal", DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID, "distinguished_black_reef_campaign_medal", aar_urls))
                    notified_challenges.append("distinguished_black_reef")

            # === Dual Vigil tracking (auto-award) ===
            # Track unique Absolute 2-brother missions with @Dual Vigil tag; award once all 9 unique missions completed
            if dual_vigil and difficulty_class == "absolute_ops" and mission_name in DUAL_VIGIL_REQUIRED_MISSIONS:
                if "dual_vigil" not in user_progress:
                    user_progress["dual_vigil"] = []
                existing_missions = {m["mission"] for m in user_progress["dual_vigil"]}
                if mission_name not in existing_missions:
                    user_progress["dual_vigil"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )
                unique_missions = {m["mission"] for m in user_progress["dual_vigil"]}
                if (
                    unique_missions >= DUAL_VIGIL_REQUIRED_MISSIONS
                    and "dual_vigil" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=DUAL_VIGIL_AWARD_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["dual_vigil"] if m["message_url"]]
                    notifications.append((user_id_str, "Order of the Aquiline Brotherhood", DUAL_VIGIL_AWARD_ROLE_ID, "dual_vigil", aar_urls))
                    notified_challenges.append("dual_vigil")

            # === Black Laurels tracking (auto-award) ===
            # Track unique Black Laurels missions; auto-assign Black Laurels role once all 9 unique missions completed
            if black_laurels and mission_name in BLACK_LAURELS_REQUIRED_MISSIONS:
                if "black_laurels" not in user_progress:
                    user_progress["black_laurels"] = []
                existing_missions = {m["mission"] for m in user_progress["black_laurels"]}
                if mission_name not in existing_missions:
                    user_progress["black_laurels"].append(
                        {"mission": mission_name, "aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )
                unique_missions = {m["mission"] for m in user_progress["black_laurels"]}
                if (
                    unique_missions >= BLACK_LAURELS_REQUIRED_MISSIONS
                    and "black_laurels" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=BLACK_LAURELS_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["black_laurels"] if m["message_url"]]
                    notifications.append((user_id_str, "Black Laurels", BLACK_LAURELS_ROLE_ID, "black_laurels", aar_urls))
                    notified_challenges.append("black_laurels")

            # === Defense of Herisor tracking (auto-award; no submit command required) ===
            # Tally individually by qualifying AAR categories:
            #   - Hard-Siege, Wave 10+ with Herisor tag
            #   - Hard-Stratagem Termination with Herisor tag
            #   - Hard-Stratagem Reclamation with Herisor tag
            _waves_ok = False
            _brother_waves = record.get("brother_waves") or {}
            _member_wave_counts = []
            if isinstance(_brother_waves, dict):
                for _wv in _brother_waves.values():
                    try:
                        _member_wave_counts.append(int(_wv))
                    except Exception:
                        pass

            if _member_wave_counts:
                _waves_ok = max(_member_wave_counts) >= 10
            else:
                try:
                    _waves_ok = int(waves) >= 10
                except Exception:
                    _waves_ok = False

            _is_herisor_siege = herisor_defense and difficulty_class == "hard_siege" and _waves_ok
            _is_herisor_term = herisor_defense and difficulty_class == "hard_stratagem" and mission_name == "termination"
            _is_herisor_rec = herisor_defense and difficulty_class == "hard_stratagem" and mission_name == "reclamation"

            if _is_herisor_siege or _is_herisor_term or _is_herisor_rec:
                _herisor_key = (
                    "herisor_defense_siege"
                    if _is_herisor_siege
                    else ("herisor_defense_termination" if _is_herisor_term else "herisor_defense_reclamation")
                )
                if _herisor_key not in user_progress:
                    user_progress[_herisor_key] = []

                _existing_ids = {str(m.get("aar_id")) for m in user_progress[_herisor_key] if isinstance(m, dict)}
                if str(aar_id) not in _existing_ids:
                    user_progress[_herisor_key].append(
                        {
                            "mission": mission_name if mission_name else "siege",
                            "aar_id": aar_id,
                            "message_url": message_url,
                            "timestamp": timestamp,
                            "black_laurels": bool(black_laurels),
                        }
                    )

                _siege_entries = user_progress.get("herisor_defense_siege", [])
                _term_entries = user_progress.get("herisor_defense_termination", [])
                _rec_entries = user_progress.get("herisor_defense_reclamation", [])
                _strat_entries = _term_entries + _rec_entries

                # Base: siege done OR (both termination AND reclamation done)
                _has_base = bool(_siege_entries) or bool(_term_entries and _rec_entries)
                # Distinguished: siege with BL OR (both term AND rec with BL)
                _siege_bl = any(bool(e.get("black_laurels")) for e in _siege_entries if isinstance(e, dict))
                _term_bl = any(bool(e.get("black_laurels")) for e in _term_entries if isinstance(e, dict))
                _rec_bl = any(bool(e.get("black_laurels")) for e in _rec_entries if isinstance(e, dict))
                _strat_bl = bool(_term_entries and _rec_entries and _term_bl and _rec_bl)
                _has_distinguished = _siege_bl or _strat_bl
                # Valor: siege with BL AND (both term AND rec with BL)
                _has_valor = _siege_bl and _strat_bl

                _herisor_urls = sorted(
                    {
                        m.get("message_url", "")
                        for m in (_siege_entries + _term_entries + _rec_entries)
                        if isinstance(m, dict) and m.get("message_url")
                    }
                )

                if (
                    _has_base
                    and "herisor_defense" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=HERISOR_DEFENSE_MEDAL_ROLE_ID)
                ):
                    notifications.append(
                        (
                            user_id_str,
                            "Herisor Defense Medal",
                            HERISOR_DEFENSE_MEDAL_ROLE_ID,
                            "herisor_defense_medal",
                            _herisor_urls,
                        )
                    )
                    notified_challenges.append("herisor_defense")

                if (
                    _has_distinguished
                    and "distinguished_herisor_defense" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID)
                ):
                    notifications.append(
                        (
                            user_id_str,
                            "Distinguished Herisor Defense Medal",
                            DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID,
                            "distinguished_herisor_defense_medal",
                            _herisor_urls,
                        )
                    )
                    notified_challenges.append("distinguished_herisor_defense")

                if (
                    _has_valor
                    and "distinguished_herisor_defense_valor" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID)
                ):
                    notifications.append(
                        (
                            user_id_str,
                            "Distinguished Herisor Defense Medal with Valor",
                            DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID,
                            "distinguished_herisor_defense_medal_with_valor",
                            _herisor_urls,
                        )
                    )
                    notified_challenges.append("distinguished_herisor_defense_valor")

            # === Crux Terminatus tracking (auto-verification) ===
            # Auto-verify: Watch Veteran rank, 2+ SOK-G missions, All 8 Black Laurels, 2+ Terminus Slayer classes,
            # plus at least one Rank A or higher extermination on a Black Laurels mission.

            # Track Black Laurels AARs for Crux Terminatus Rank A audit
            if black_laurels and message_url:
                if "crux_bl_aars" not in user_progress:
                    user_progress["crux_bl_aars"] = []
                existing_ids = {m["aar_id"] for m in user_progress["crux_bl_aars"]}
                if aar_id not in existing_ids:
                    user_progress["crux_bl_aars"].append(
                        {"aar_id": aar_id, "message_url": message_url, "timestamp": timestamp}
                    )

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
                    # Gather AAR URLs for Black Laurels missions (Rank A audit).
                    # The pre-built crux_bl_aars list only captures AARs processed
                    # after this tracking was added.  Fall back to a live datastore
                    # scan so that historical records with message_url are included.
                    aar_url_set = {
                        m["message_url"]
                        for m in user_progress.get("crux_bl_aars", [])
                        if m.get("message_url")
                    }
                    if _g.DATASTORE:
                        for _rec in _g.DATASTORE.iter_records():
                            _bl = _rec.get("black_laurels_in_mission") or _rec.get("black_laurels_in_difficulty")
                            if not _bl:
                                continue
                            if user_id_str not in [str(b) for b in (_rec.get("brother_ids") or [])]:
                                continue
                            _url = _rec.get("message_url")
                            if _url:
                                aar_url_set.add(_url)
                    aar_urls = sorted(aar_url_set)
                    notifications.append((user_id_str, "Crux Terminatus", CRUX_TERMINATUS_ROLE_ID, "crux_terminatus", aar_urls))
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

                # Check if all 13 missions completed at Omega with Black Laurels.
                unique_missions = {m["mission"] for m in user_progress["order_omega"]}
                if (
                    len(unique_missions) >= 13
                    and unique_missions == ORDER_OMEGA_REQUIRED_MISSIONS
                    and "order_omega" not in notified_challenges
                    and member
                    and is_watch_brother_or_higher
                    and not discord.utils.get(member.roles, id=THE_ORDER_OMEGA_ROLE_ID)
                ):
                    aar_urls = [m["message_url"] for m in user_progress["order_omega"] if m["message_url"]]
                    notifications.append((user_id_str, "The Order Omega", THE_ORDER_OMEGA_ROLE_ID, "the_order_omega", aar_urls))
                    notified_challenges.append("order_omega")

            # Update notified list
            user_progress["notified"] = notified_challenges

        # Save updated progress
        _save_challenge_progress(progress_data)

    return notifications


def _get_challenge_keeper_mention(guild: discord.Guild) -> str:
    """Return the Watch Keeper role mention for challenge notifications."""
    role = discord.utils.get(guild.roles, name="Watch Keeper")
    if role:
        return role.mention
    return "@Watch Keeper"


async def _send_challenge_eligibility_notifications(
    notifications: List[Tuple[str, str, int, str, List[str]]], guild: discord.Guild
):
    """Auto-assign roles and announce qualified challenge awards.

    All challenge awards are auto-assigned and announced publicly via the
    award announcement queue. The librarius staff audit pathway has been
    retired in favor of public announcements with rich award embeds.

    Args:
        notifications: list of (user_id, challenge_name, role_id, award_type, aar_urls)
        guild: Discord guild
    """
    if not notifications:
        return

    home_chapters = _b("HOME_CHAPTERS") or []

    for user_id, challenge_name, role_id, award_type, _aar_urls in notifications:
        try:
            member = guild.get_member(int(user_id))
            if not (role_id and award_type and member is not None):
                _g.logger.warning(
                    f"Challenge notification missing role_id/award_type/member for {user_id} {challenge_name}; skipping"
                )
                continue
            role = guild.get_role(role_id)
            if role is None:
                _g.logger.warning(
                    f"Auto-award role id {role_id} not found in guild for {challenge_name}; skipping {user_id}"
                )
                continue
            try:
                await member.add_roles(role, reason=f"Auto-award: {challenge_name}")
                # Record acquisition timestamp for grace-period baseline
                from datetime import datetime, timezone
                now_iso = datetime.now(timezone.utc).isoformat()
                if _g.DATASTORE:
                    await _g.DATASTORE.set_role_acquisition_date(str(user_id), challenge_name, now_iso)
            except Exception as e:
                _g.logger.warning(
                    f"Failed to assign {challenge_name} role to {user_id}: {e}"
                )
                continue
            member_chapter = "Unknown"
            for r in getattr(member, "roles", []):
                if getattr(r, "name", "") in home_chapters:
                    member_chapter = r.name
                    break
            ann_channel = None
            try:
                ann_channel = await _b("_get_award_announcement_channel")(member, guild)
            except Exception as e:
                _g.logger.warning(f"Failed to resolve announcement channel for {user_id}: {e}")
            if ann_channel is None:
                _g.logger.warning(
                    f"No announcement channel found for {user_id} {award_type}; role assigned but no announcement sent"
                )
            else:
                _b("_enqueue_award_announcement")(
                    str(member.id), award_type, member_chapter, str(ann_channel.id), str(guild.id)
                )
            _g.logger.info(f"Auto-awarded {challenge_name} to {user_id}")
            await asyncio.sleep(0.5)

        except Exception as e:
            _g.logger.exception(f"Failed to process challenge notification for {user_id} - {challenge_name}: {e}")


# ---------------------------------------------------------------------------
# Periodic challenge completion sweep
# ---------------------------------------------------------------------------

_WATCH_BROTHER_OR_HIGHER = {
    "Watch Brother", "Watch Sister",
    "Watch Veteran", "Oathsworn", "Bladeguard",
    "Watch Sergeant", "Veteran Sergeant", "Watch Techmarine", "Watch Librarian",
    "Watch Apothecary", "Watch Chaplain", "Watch Keeper",
    "First Blade", "Watch Lieutenant", "Watch Captain",
    "Venerable Dreadnought", "Honored Dreadnought", "Forgemaster",
    "Void Warden", "High Chaplain", "Chief Apothecary",
    "Castellan", "Blade Master", "Watch Master",
}

# Map of challenge_key → (required_missions_set, award_role_id, display_name, award_type, notified_key)
# Pipehitter and Crux Terminatus have non-standard conditions and are handled separately below.
_SIMPLE_CHALLENGE_SPECS = [
    ("kadaku_campaign",       KADAKU_CAMPAIGN_REQUIRED_MISSIONS,       KADAKU_CAMPAIGN_MEDAL_ROLE_ID,                   "Kadaku Campaign Medal",                     "kadaku_campaign_medal",                    "kadaku_campaign"),
    ("distinguished_kadaku",  KADAKU_CAMPAIGN_REQUIRED_MISSIONS,       DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID,     "Distinguished Kadaku Campaign Medal",       "distinguished_kadaku_campaign_medal",      "distinguished_kadaku"),
    ("black_reef",            BLACK_REEF_REQUIRED_MISSIONS,            BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,               "Black Reef Campaign Medal",                 "black_reef_campaign_medal",                "black_reef"),
    ("distinguished_black_reef", BLACK_REEF_REQUIRED_MISSIONS,         DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID, "Distinguished Black Reef Campaign Medal",   "distinguished_black_reef_campaign_medal",  "distinguished_black_reef"),
    ("dual_vigil",            DUAL_VIGIL_REQUIRED_MISSIONS,            DUAL_VIGIL_AWARD_ROLE_ID,                        "Order of the Aquiline Brotherhood",         "dual_vigil",                               "dual_vigil"),
    ("black_laurels",         BLACK_LAURELS_REQUIRED_MISSIONS,         BLACK_LAURELS_ROLE_ID,                           "Black Laurels",                             "black_laurels",                            "black_laurels"),
    ("order_omega",           ORDER_OMEGA_REQUIRED_MISSIONS,           THE_ORDER_OMEGA_ROLE_ID,                         "The Order Omega",                           "the_order_omega",                          "order_omega"),
]


async def _sweep_challenge_completions(guild: discord.Guild) -> int:
    """Scan all tracked members and fire awards for anyone whose challenge
    progress is complete but whose award has not yet been queued.

    This catches members who finished all required ops before a bot restart,
    code change, or data reset — cases where the AAR-triggered path never
    had a chance to evaluate completion.

    Returns the number of awards queued.
    """
    if guild is None:
        _g.logger.warning("challenge sweep: called with no guild; skipping")
        return 0

    notifications: List[Tuple[str, str, int, str, List[str]]] = []
    scanned = 0
    skipped_no_member = 0
    skipped_rank = 0
    errors = 0

    _g.logger.info("challenge sweep: starting")

    async with _g.CHALLENGE_PROGRESS_LOCK:
        try:
            progress_data = _load_challenge_progress()
        except Exception as exc:
            _g.logger.exception(f"challenge sweep: failed to load challenge_progress.json: {exc}")
            return 0

        changed = False

        for user_id_str, user_progress in progress_data.items():
            scanned += 1
            try:
                member = guild.get_member(int(user_id_str))
            except Exception as exc:
                _g.logger.debug(f"challenge sweep: could not resolve member {user_id_str}: {exc}")
                errors += 1
                continue
            if member is None:
                skipped_no_member += 1
                continue

            member_role_names = {getattr(r, "name", "") for r in member.roles}
            if not member_role_names & _WATCH_BROTHER_OR_HIGHER:
                skipped_rank += 1
                continue

            notified = user_progress.get("notified", [])

            try:
                # --- Simple set-complete challenges ---
                for prog_key, required, role_id, display_name, award_type, notified_key in _SIMPLE_CHALLENGE_SPECS:
                    if notified_key in notified:
                        continue
                    if discord.utils.get(member.roles, id=role_id):
                        continue
                    entries = user_progress.get(prog_key, [])
                    if not entries:
                        continue
                    normalized_entries, normalized_changed = _normalize_progress_entries(entries)
                    if normalized_changed:
                        user_progress[prog_key] = normalized_entries
                        changed = True
                    unique = {e["mission"] for e in normalized_entries if isinstance(e, dict) and "mission" in e}
                    if unique >= required:
                        aar_urls = [e["message_url"] for e in normalized_entries if isinstance(e, dict) and e.get("message_url")]
                        notifications.append((user_id_str, display_name, role_id, award_type, aar_urls))
                        notified.append(notified_key)
                        user_progress["notified"] = notified
                        changed = True
                        _g.logger.info(
                            f"challenge sweep: {display_name} queued for "
                            f"{member.display_name} ({user_id_str}) "
                            f"[{len(unique)}/{len(required)} missions]"
                        )

                # --- SOK-G Pipehitter (1 op) ---
                if "sok_g_pipehitter" not in notified and not discord.utils.get(member.roles, id=PIPEHITTER_ROLE_ID):
                    entries = user_progress.get("sok_g_pipehitter", [])
                    if len(entries) >= 1:
                        aar_urls = [e["message_url"] for e in entries if e.get("message_url")]
                        notifications.append((user_id_str, "SOK-G: Pipehitter", PIPEHITTER_ROLE_ID, "sok_g_pipehitter", aar_urls))
                        notified.append("sok_g_pipehitter")
                        user_progress["notified"] = notified
                        changed = True
                        _g.logger.info(f"challenge sweep: SOK-G: Pipehitter queued for {member.display_name} ({user_id_str})")

                # --- Distinguished SOK-G Pipehitter (2+ ops) ---
                if "distinguished_sok_g_pipehitter" not in notified and not discord.utils.get(member.roles, id=DISTINGUISHED_PIPEHITTER_ROLE_ID):
                    entries = user_progress.get("sok_g_pipehitter", [])
                    if len(entries) >= 2:
                        aar_urls = [e["message_url"] for e in entries if e.get("message_url")]
                        notifications.append((user_id_str, "Distinguished SOK-G: Pipehitter", DISTINGUISHED_PIPEHITTER_ROLE_ID, "distinguished_pipehitter", aar_urls))
                        notified.append("distinguished_sok_g_pipehitter")
                        user_progress["notified"] = notified
                        changed = True
                        _g.logger.info(f"challenge sweep: Distinguished SOK-G: Pipehitter queued for {member.display_name} ({user_id_str})")

                # --- Defense of Herisor family ---
                def _as_list(value) -> list:
                    return value if isinstance(value, list) else []

                siege_entries = _as_list(user_progress.get("herisor_defense_siege"))
                term_entries = _as_list(user_progress.get("herisor_defense_termination"))
                rec_entries = _as_list(user_progress.get("herisor_defense_reclamation"))
                legacy_subs = _as_list(user_progress.get("defense_of_herisor_submissions"))

                auto_base = bool(siege_entries) or bool(term_entries and rec_entries)
                siege_bl = any(bool(e.get("black_laurels")) for e in siege_entries if isinstance(e, dict))
                term_bl = any(bool(e.get("black_laurels")) for e in term_entries if isinstance(e, dict))
                rec_bl = any(bool(e.get("black_laurels")) for e in rec_entries if isinstance(e, dict))
                auto_strat_bl = bool(term_entries and rec_entries and term_bl and rec_bl)
                auto_distinguished = siege_bl or auto_strat_bl
                auto_valor = siege_bl and auto_strat_bl

                legacy_base = len(legacy_subs) > 0
                legacy_distinguished = any(bool(s.get("distinguished")) for s in legacy_subs if isinstance(s, dict))
                legacy_valor = any(bool(s.get("distinguished_with_valor")) for s in legacy_subs if isinstance(s, dict))

                has_herisor_base = auto_base or legacy_base
                has_herisor_distinguished = auto_distinguished or legacy_distinguished
                has_herisor_valor = auto_valor or legacy_valor

                herisor_urls = sorted(
                    {
                        e.get("message_url", "")
                        for e in (siege_entries + term_entries + rec_entries + legacy_subs)
                        if isinstance(e, dict) and e.get("message_url")
                    }
                )

                if (
                    has_herisor_base
                    and "herisor_defense" not in notified
                    and not discord.utils.get(member.roles, id=HERISOR_DEFENSE_MEDAL_ROLE_ID)
                ):
                    notifications.append(
                        (
                            user_id_str,
                            "Herisor Defense Medal",
                            HERISOR_DEFENSE_MEDAL_ROLE_ID,
                            "herisor_defense_medal",
                            herisor_urls,
                        )
                    )
                    notified.append("herisor_defense")
                    user_progress["notified"] = notified
                    changed = True
                    _g.logger.info(f"challenge sweep: Herisor Defense Medal queued for {member.display_name} ({user_id_str})")

                if (
                    has_herisor_distinguished
                    and "distinguished_herisor_defense" not in notified
                    and not discord.utils.get(member.roles, id=DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID)
                ):
                    notifications.append(
                        (
                            user_id_str,
                            "Distinguished Herisor Defense Medal",
                            DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID,
                            "distinguished_herisor_defense_medal",
                            herisor_urls,
                        )
                    )
                    notified.append("distinguished_herisor_defense")
                    user_progress["notified"] = notified
                    changed = True
                    _g.logger.info(
                        f"challenge sweep: Distinguished Herisor Defense Medal queued for {member.display_name} ({user_id_str})"
                    )

                if (
                    has_herisor_valor
                    and "distinguished_herisor_defense_valor" not in notified
                    and not discord.utils.get(member.roles, id=DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID)
                ):
                    notifications.append(
                        (
                            user_id_str,
                            "Distinguished Herisor Defense Medal with Valor",
                            DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID,
                            "distinguished_herisor_defense_medal_with_valor",
                            herisor_urls,
                        )
                    )
                    notified.append("distinguished_herisor_defense_valor")
                    user_progress["notified"] = notified
                    changed = True
                    _g.logger.info(
                        f"challenge sweep: Distinguished Herisor Defense Medal with Valor queued for {member.display_name} ({user_id_str})"
                    )

            except Exception as exc:
                _g.logger.exception(
                    f"challenge sweep: error evaluating member {user_id_str} "
                    f"({getattr(member, 'display_name', '?')}): {exc}"
                )
                errors += 1

            # NOTE: Crux Terminatus is intentionally excluded from the sweep.
            # It requires a live Rank-A audit across all Black Laurels AARs in
            # the datastore — that evaluation only makes sense per-AAR when the
            # datastore is fully loaded, not in a background sweep.

        if changed:
            try:
                _save_challenge_progress(progress_data)
            except Exception as exc:
                _g.logger.exception(f"challenge sweep: failed to save challenge_progress.json: {exc}")

    _g.logger.info(
        f"challenge sweep: done — scanned={scanned}, awards_queued={len(notifications)}, "
        f"skipped_no_member={skipped_no_member}, skipped_rank={skipped_rank}, errors={errors}"
    )

    if notifications:
        try:
            await _send_challenge_eligibility_notifications(notifications, guild)
        except Exception as exc:
            _g.logger.exception(f"challenge sweep: error in _send_challenge_eligibility_notifications: {exc}")

    return len(notifications)


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
        ids_to_remove: set[str] = set()

        def _entry_dt_from_meta(aar_id: int, entry: dict) -> Optional[datetime]:
            ts_raw = entry.get("timestamp") if isinstance(entry, dict) else None
            if isinstance(ts_raw, str) and ts_raw.strip():
                try:
                    dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if dt.tzinfo is not None:
                        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    return dt
                except Exception:
                    pass
            try:
                dt = _snowflake_to_datetime(aar_id)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except Exception:
                return None

        candidates: list[tuple[int, str]] = []
        for aar_id_str in list(error_entries.keys()):
            try:
                aar_id = int(aar_id_str)
            except ValueError:
                ids_to_remove.add(aar_id_str)
                continue
            if cutoff_dt is not None:
                entry_dt = _entry_dt_from_meta(aar_id, error_entries.get(aar_id_str, {}))
                # If we can confidently determine the entry is older than the
                # requested window, skip before any Discord API fetches.
                if entry_dt is not None and entry_dt < cutoff_dt:
                    continue
            candidates.append((aar_id, aar_id_str))

        total_errs = len(candidates)
        done_errs = 0
        for aar_id, aar_id_str in candidates:
            if has_been_processed(aar_id):
                # If the AAR has been processed since the error was recorded,
                # remove it from the errors archive rather than touching the
                # saved records. Previously this removed the record file by
                # mistake which prevented error entries from being cleared.
                try:
                    sid = str(aar_id)
                    entry = error_entries.get(sid, {}) if isinstance(error_entries, dict) else {}
                    reply_id = entry.get("reply_id") if isinstance(entry, dict) else None
                    if reply_id:
                        if reply_id:
                            try:
                                reply_msg = await aar_channel.fetch_message(int(reply_id))
                                try:
                                    await reply_msg.delete()
                                except Exception:
                                    try:
                                        _g.logger.debug(f"Unable to delete reply {reply_id} for AAR {sid}")
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    ids_to_remove.add(sid)
                except Exception:
                    pass
                fixed += 1
                done_errs += 1
                if (done_errs % 5 == 0) or (done_errs == total_errs):
                    _b("_print_progress")("Recheck Errors", done_errs, total_errs)
                continue
            try:
                msg = await aar_channel.fetch_message(aar_id)
            except Exception:
                msg = None
            if not msg:
                log_aar_errors(aar_id, ["Original message not found; cannot reprocess."])
                still_broken += 1
                done_errs += 1
                if (done_errs % 5 == 0) or (done_errs == total_errs):
                    _b("_print_progress")("Recheck Errors", done_errs, total_errs)
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

                    # --- Challenge Tracking: Process AAR for challenge eligibility ---
                    _guild = getattr(aar_channel, "guild", None)
                    if _guild:
                        try:
                            challenge_notifications = await _process_challenge_tracking(record, _guild)
                            if challenge_notifications:
                                await _send_challenge_eligibility_notifications(challenge_notifications, _guild)
                        except Exception as e:
                            _g.logger.error(f"Error processing challenge tracking for AAR {aar_id}: {e}")
                        try:
                            await _b("_check_award_milestones_for_members")(
                                [str(uid) for uid in record.get("brother_ids", [])], _guild
                            )
                        except Exception as e:
                            _g.logger.error(f"Error checking award milestones for AAR {aar_id}: {e}")

                    # If an error entry exists for this AAR, attempt to remove
                    # the bot's previous reply and clear the error record.
                    try:
                        sid = str(aar_id)
                        entry = error_entries.get(sid, {}) if isinstance(error_entries, dict) else {}
                        reply_id = entry.get("reply_id") if isinstance(entry, dict) else None
                        if sid in error_entries:
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
                            ids_to_remove.add(sid)
                    except Exception:
                        pass
                    await _set_aar_reaction(msg, "ok")
                    fixed += 1
            done_errs += 1
            if (done_errs % 5 == 0) or (done_errs == total_errs):
                _b("_print_progress")("Recheck Errors", done_errs, total_errs)

        if ids_to_remove:
            latest_errors = _load_json_dict(AAR_ERRORS_PATH)
            changed = False
            for sid in ids_to_remove:
                if sid in latest_errors:
                    del latest_errors[sid]
                    changed = True
            if changed:
                _save_json_dict(AAR_ERRORS_PATH, latest_errors)

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

        await save_aar_record(record)

        # --- Challenge Tracking: Process AAR for challenge eligibility ---
        _guild = getattr(aar_channel, "guild", None)
        if _guild:
            try:
                challenge_notifications = await _process_challenge_tracking(record, _guild)
                if challenge_notifications:
                    await _send_challenge_eligibility_notifications(challenge_notifications, _guild)
            except Exception as e:
                _g.logger.error(f"Error processing challenge tracking for AAR {aar_id}: {e}")
            try:
                await _b("_check_award_milestones_for_members")(
                    [str(uid) for uid in record.get("brother_ids", [])], _guild
                )
            except Exception as e:
                _g.logger.error(f"Error checking award milestones for AAR {aar_id}: {e}")
            # Clear LOA for any members with an active LOA who appear in this AAR
            try:
                from . import loa_ops as _loa_ops
                for uid in record.get("brother_ids", []):
                    if _loa_ops._get_active_loa(int(uid)):
                        await _loa_ops.clear_loa_on_aar(int(uid), _guild)
            except Exception as e:
                _g.logger.debug(f"[LOA] AAR ingest LOA check failed: {e}")

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

        # Trigger a background promotion/stud eligibility refresh so veteran auto-promotion
        # and related queues reflect the new induction date without waiting for the next cycle.
        try:
            promotion_check = _b("_check_promotion_milestones")
            if callable(promotion_check):
                asyncio.create_task(promotion_check())
        except Exception:
            pass

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

            # Compute entitlement using same rules as roster/tally.
            # Watch Brother/Sister become stud-eligible once they meet Watch Veteran
            # thresholds (200 AAR + 2 weeks), even before role sync occurs.
            stats = _b("compute_stats_for_user")(str(getattr(member, "id", "")))
            try:
                aar_points_val = int(round(float(stats.get("aar_points", 0) or 0)))
            except Exception:
                aar_points_val = 0

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
                weeks = 0
                studs_time = 0

            highest_idx = _b("get_highest_rank_index")(member)
            is_veteran_or_higher = (idx_veteran is not None) and (highest_idx is not None) and (highest_idx <= idx_veteran)
            is_watch_brother = ("Watch Brother" in member_role_names) or ("Watch Sister" in member_role_names)
            is_veteran_eligible = aar_points_val >= 200 and weeks >= 2

            studs_count = 0
            if is_veteran_or_higher or (is_watch_brother and is_veteran_eligible):
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
            _g.logger.warning(
                f"Reparse skipped AAR {rec.get('aar_id', key)}: missing message_url"
            )
            failed += 1
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
                failed += 1
                _g.logger.warning(
                    f"Reparse failed for AAR {rec.get('aar_id', key)} (message_url={msg_url}): parse_aar returned no record"
                )
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
                    try:
                        await _b("_check_award_milestones_for_members")(
                            [str(uid) for uid in merged.get("brother_ids", [])], guild_obj
                        )
                    except Exception as e:
                        _g.logger.error(f"Error checking award milestones during reparse for AAR {merged.get('aar_id')}: {e}")
            except Exception as e:
                _g.logger.error(f"Error processing challenge tracking during reparse for AAR {merged.get('aar_id')}: {e}")

            if merged != rec:
                # Track which fields changed
                for field in set(rec.keys()) | set(merged.keys()):
                    if rec.get(field) != merged.get(field):
                        changes_by_field[field] = changes_by_field.get(field, 0) + 1
                await _g.DATASTORE.set_record(str(merged.get("aar_id")), merged)
                updated += 1
        except Exception as e:
            failed += 1
            _g.logger.warning(
                f"Reparse failed for AAR {rec.get('aar_id', key)} (message_url={msg_url}): {type(e).__name__}: {e}"
            )

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

    started_at = datetime.now(timezone.utc)
    outcome = "failed"
    response_text = None
    total = updated = failed = 0
    changes_by_field: dict[str, int] = {}
    _g.logger.info(f"reparse_records: acquiring lock (user={interaction.user.id})")
    async with _g.RECONCILE_LOCK:
        _g.logger.info(f"reparse_records: lock acquired (user={interaction.user.id})")
        try:
            total, updated, failed, changes_by_field = await _run_reparse_records(limit=limit, days=days)
            outcome = "complete"
        except ValueError as e:
            response_text = str(e)
            _g.logger.warning(
                f"reparse_records failed for user={interaction.user.id}: {type(e).__name__}: {e}"
            )
            try:
                await interaction.followup.send(response_text, ephemeral=True)
            except Exception as send_error:
                _g.logger.warning(
                    f"reparse_records could not send failure response for user={interaction.user.id}: {type(send_error).__name__}: {send_error}"
                )
        except Exception as e:
            response_text = f"Reparse failed: {type(e).__name__}: {e}"
            _g.logger.exception(
                f"reparse_records failed unexpectedly for user={interaction.user.id}: {type(e).__name__}: {e}"
            )
            try:
                await interaction.followup.send(response_text, ephemeral=True)
            except Exception as send_error:
                _g.logger.warning(
                    f"reparse_records could not send failure response for user={interaction.user.id}: {type(send_error).__name__}: {send_error}"
                )
        else:
            days_info = f" (last {days} days)" if days else ""
            # Build changes summary
            if changes_by_field:
                sorted_changes = sorted(changes_by_field.items(), key=lambda x: -x[1])
                changes_summary = ", ".join(f"{k}={v}" for k, v in sorted_changes)
                changes_line = f"\nFields updated: {changes_summary}"
            else:
                changes_line = ""
            response_text = f"Reparse complete{days_info}: processed={total}, updated={updated}, failed={failed}{changes_line}"
            try:
                await interaction.followup.send(response_text, ephemeral=True)
            except Exception as send_error:
                _g.logger.warning(
                    f"reparse_records could not send success response for user={interaction.user.id}: {type(send_error).__name__}: {send_error}"
                )

    duration = (datetime.now(timezone.utc) - started_at).total_seconds()
    if outcome == "complete":
        _g.logger.info(
            f"Complete /reparse_records by user={interaction.user.id} duration={duration:.3f}s processed={total} updated={updated} failed={failed}"
            + (f" | {response_text}" if response_text else "")
        )
    else:
        _g.logger.info(
            f"Failed /reparse_records by user={interaction.user.id} duration={duration:.3f}s"
            + (f" | {response_text}" if response_text else "")
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
        n = waves // 5
        return n * (n + 5) // 2
    if difficulty_class == "hard_siege":
        if waves is None:
            return 0
        n = waves // 5
        return n * (n + 7) // 2
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
        or "++ 𝐌𝐈𝐒𝐒𝐈𝐎𝐍 𝐑𝐄𝐏𝐎𝐑𝐓 ++" in content
    )


def get_user_ids_in_line(line: str, message: discord.Message):
    """Return list of user IDs whose mention appears in this line."""
    ids = []
    for user in message.mentions:
        patterns = (f"<@{user.id}>", f"<@!{user.id}>")
        if any(p in line for p in patterns):
            ids.append(str(user.id))
    return ids


def get_all_user_ids_in_line(line: str, message: discord.Message):
    """Return list of mentioned user IDs in line order, preserving duplicates."""
    mentioned_user_ids = {str(user.id) for user in message.mentions}
    return [
        match.group(1)
        for match in re.finditer(r"<@!?(\d+)>", line)
        if match.group(1) in mentioned_user_ids
    ]


def _normalize_pvp_result_token(raw: str | None) -> str | None:
    """Normalize PvP result values into canonical W/L tokens."""
    text = (raw or "").strip().lower()
    if not text:
        return None

    cleaned = re.sub(r"[^a-z]", "", text)
    if cleaned in ("w", "win"):
        return "W"
    if cleaned in ("l", "lose", "loss"):
        return "L"
    return None


def _resolve_aar_submission_channel(guild: discord.Guild | None):
    """Resolve the channel for interactive AAR submissions from config or fallback constants."""
    if guild is None:
        return None

    cfg = (_g.CONFIG or {}).get("aar_submission") or {}
    raw_channel_id = cfg.get("channel_id") or (_g.CONFIG or {}).get("aar_submission_channel_id")
    if raw_channel_id is not None:
        try:
            resolved = guild.get_channel(int(raw_channel_id))
            if resolved is not None:
                return resolved
        except Exception:
            pass

    try:
        return guild.get_channel(int(AAR_CHANNEL_ID))
    except Exception:
        return None


def _aar_submission_testing_mode() -> bool:
    """Return whether interactive AAR submissions should be sent as non-ingestable test reports."""
    cfg = (_g.CONFIG or {}).get("aar_submission") or {}
    raw = cfg.get("testing_mode")
    if raw is None:
        # Default to safe behavior during rollout.
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    return text in {"1", "true", "yes", "on", "enabled"}


def _aar_submission_report_markers(testing_mode: bool) -> tuple[str, str]:
    """Return start/end markers for submission report generation."""
    if testing_mode:
        return "++ TEST MISSION REPORT ++", "++ END OF TEST REPORT ++"
    return "++ MISSION REPORT ++", "++ END OF REPORT ++"


def _normalize_submission_tags(raw_tags: str) -> list[str]:
    """Normalize free-form submission tags into canonical keys."""
    alias_map = {
        "blacklaurels": "black_laurels",
        "black_laurels": "black_laurels",
        "black-laurels": "black_laurels",
        "leviathanprotocol": "leviathan_protocol",
        "leviathan_protocol": "leviathan_protocol",
        "leviathan-protocol": "leviathan_protocol",
        "dualvigil": "dual_vigil",
        "dual_vigil": "dual_vigil",
        "dual-vigil": "dual_vigil",
        "herisordefense": "herisor_defense",
        "herisor_defense": "herisor_defense",
        "herisor-defense": "herisor_defense",
    }
    normalized: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\n]", raw_tags or ""):
        cleaned = token.strip().lower().replace(" ", "")
        if not cleaned:
            continue
        key = alias_map.get(cleaned)
        if key and key not in seen:
            seen.add(key)
            normalized.append(key)
    return normalized[:4]


def _submission_tag_label(tag_key: str) -> str:
    labels = {
        "black_laurels": "Black Laurels",
        "leviathan_protocol": "Leviathan Protocol",
        "dual_vigil": "Dual Vigil",
        "herisor_defense": "Herisor Defense",
    }
    return labels.get(tag_key, tag_key)


def _submission_tag_mentions(tag_keys: list[str]) -> str:
    mentions = {
        "black_laurels": f"<@&{BLACK_LAURELS_ROLE_ID}>",
        "leviathan_protocol": f"<@&{LEVIATHAN_PROTOCOL_ROLE_ID}>",
        "dual_vigil": f"<@&{DUAL_VIGIL_ROLE_ID}>",
        "herisor_defense": f"<@&{HERISOR_DEFENSE_TAG_ROLE_ID}>",
    }
    return " ".join(mentions[k] for k in tag_keys if k in mentions)


def _extract_brother_mentions(raw_text: str) -> list[str]:
    """Extract unique user mentions from free-form text while preserving order."""
    ids: list[str] = []
    seen: set[str] = set()
    for uid in re.findall(r"<@!?(\d+)>", raw_text or ""):
        if uid not in seen:
            seen.add(uid)
            ids.append(uid)
    return [f"<@{uid}>" for uid in ids]


class AARSubmissionDetailsModal(discord.ui.Modal, title="AAR Report Details"):
    def __init__(self, parent_view: "AARSubmissionView"):
        super().__init__()
        self.parent_view = parent_view
        self.rank_input = discord.ui.TextInput(
            label="Rank (A/B/C/D)",
            style=discord.TextStyle.short,
            required=True,
            max_length=1,
            default=parent_view.rank,
            placeholder="A",
        )
        self.brothers_input = discord.ui.TextInput(
            label="Brother Mentions",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
            default=" ".join(parent_view.brothers),
            placeholder="Add mentions like <@123> <@456>",
        )
        self.tags_input = discord.ui.TextInput(
            label="Tags (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=100,
            default=", ".join(_submission_tag_label(t) for t in parent_view.tags),
            placeholder="Black Laurels, Leviathan Protocol",
        )
        self.add_item(self.rank_input)
        self.add_item(self.brothers_input)
        self.add_item(self.tags_input)

    async def on_submit(self, interaction: discord.Interaction):
        rank_val = str(self.rank_input.value or "").strip().upper()
        if rank_val not in {"A", "B", "C", "D"}:
            await interaction.response.send_message("Rank must be one of: A, B, C, D.", ephemeral=True)
            return

        mentions = _extract_brother_mentions(str(self.brothers_input.value or ""))
        if not mentions:
            fallback_mention = (
                self.parent_view.submitter.mention
                if self.parent_view.submitter is not None and hasattr(self.parent_view.submitter, "mention")
                else "@brother"
            )
            mentions = [fallback_mention]

        self.parent_view.rank = rank_val
        self.parent_view.brothers = mentions
        self.parent_view.tags = _normalize_submission_tags(str(self.tags_input.value or ""))
        await interaction.response.edit_message(embed=self.parent_view._build_preview_embed(), view=self.parent_view)


class AARSubmissionView(discord.ui.View):
    """Interactive AAR submission composer with dynamic selects and a preview embed."""

    def __init__(self, guild: discord.Guild | None, submitter: discord.Member | discord.User | None):
        super().__init__(timeout=180)
        self.guild = guild
        self.submitter = submitter
        self.testing_mode = _aar_submission_testing_mode()
        self.aar_type = "pve"
        self.mission = "Inferno"
        self.difficulty = "@Ruthless"
        self.rank = "A"
        self.tags: list[str] = []
        self.brothers = [submitter.mention] if submitter is not None else ["@brother"]

        self.type_select = discord.ui.Select(
            placeholder="Choose AAR type",
            options=[
                discord.SelectOption(label="PvE", value="pve", description="Mission report", default=True),
                discord.SelectOption(label="PvP", value="pvp", description="PvP match report"),
            ],
            custom_id="aar_submit_type",
        )
        self.type_select.callback = self._type_select_callback
        self.add_item(self.type_select)

        self.mission_select = discord.ui.Select(
            placeholder="Choose mission",
            options=self._mission_options(),
            custom_id="aar_submit_mission",
        )
        self.mission_select.callback = self._mission_select_callback
        self.add_item(self.mission_select)

        self.difficulty_select = discord.ui.Select(
            placeholder="Choose difficulty",
            options=self._difficulty_options(),
            custom_id="aar_submit_difficulty",
        )
        self.difficulty_select.callback = self._difficulty_select_callback
        self.add_item(self.difficulty_select)

        self.details_button = discord.ui.Button(
            label="Edit Brothers/Rank/Tags",
            style=discord.ButtonStyle.secondary,
            custom_id="aar_submit_details",
        )
        self.details_button.callback = self._details_button_callback
        self.add_item(self.details_button)

        submit_label = "Submit Test AAR" if self.testing_mode else "Submit AAR"
        self.submit_button = discord.ui.Button(label=submit_label, style=discord.ButtonStyle.success, custom_id="aar_submit_submit")
        self.submit_button.callback = self._submit_button_callback
        self.add_item(self.submit_button)

    def _mission_options(self) -> list[discord.SelectOption]:
        if self.aar_type == "pvp":
            return [
                discord.SelectOption(label="PvP Match", value="PvP Match", default=True),
                discord.SelectOption(label="PvP Scrim", value="PvP Scrim"),
            ]
        return [
            discord.SelectOption(label="Inferno", value="Inferno", default=True),
            discord.SelectOption(label="Decapitation", value="Decapitation"),
            discord.SelectOption(label="Vox Liberatis", value="Vox Liberatis"),
            discord.SelectOption(label="Reliquary", value="Reliquary"),
            discord.SelectOption(label="Termination", value="Termination"),
            discord.SelectOption(label="Reclamation", value="Reclamation"),
        ]

    def _difficulty_options(self) -> list[discord.SelectOption]:
        if self.aar_type == "pvp":
            return [
                discord.SelectOption(label="PvP Difficulty", value="@PvP Difficulty", default=True),
            ]
        return [
            discord.SelectOption(label="@Ruthless", value="@Ruthless", default=True),
            discord.SelectOption(label="@Lethal", value="@Lethal"),
            discord.SelectOption(label="@Absolute", value="@Absolute"),
            discord.SelectOption(label="@Normal-Stratagem", value="@Normal-Stratagem"),
            discord.SelectOption(label="@Hard-Stratagem", value="@Hard-Stratagem"),
            discord.SelectOption(label="@Normal-Siege", value="@Normal-Siege"),
            discord.SelectOption(label="@Hard-Siege", value="@Hard-Siege"),
        ]

    def _build_preview_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Interactive AAR Composer", color=discord.Color.gold())
        embed.add_field(name="Type", value="PvP" if self.aar_type == "pvp" else "PvE", inline=True)
        embed.add_field(name="Mission", value=self.mission, inline=True)
        embed.add_field(name="Difficulty", value=self.difficulty, inline=True)
        embed.add_field(name="Rank", value=self.rank, inline=True)
        embed.add_field(
            name="Brothers",
            value=" ".join(self.brothers) if self.brothers else "None",
            inline=False,
        )
        embed.add_field(
            name="Tags",
            value=", ".join(_submission_tag_label(t) for t in self.tags) if self.tags else "None",
            inline=False,
        )
        embed.add_field(name="Preview", value=self._compose_report(), inline=False)
        footer = "Testing mode: report will not be ingested" if self.testing_mode else "Live mode: report is ingestible"
        embed.set_footer(text=footer)
        return embed

    def _compose_report(self) -> str:
        report_start, report_end = _aar_submission_report_markers(self.testing_mode)
        tag_mentions = _submission_tag_mentions(self.tags)
        mission_line = f"Mission: {self.mission}" + (f" {tag_mentions}" if tag_mentions else "")
        lines = [report_start, ""]
        if self.aar_type == "pvp":
            lines.extend([
                mission_line,
                f"Difficulty: {self.difficulty}",
                f"Rank: {self.rank}",
                "Map: Arena",
                "Game Mode: Team Deathmatch",
                "Result: W",
                "",
                "Brothers:",
                *self.brothers,
                "",
                report_end,
            ])
            return "\n".join(lines)

        lines.extend([
            mission_line,
            f"Difficulty: {self.difficulty}",
            f"Rank: {self.rank}",
            "",
            "Brothers:",
            *self.brothers,
            "",
            report_end,
        ])
        return "\n".join(lines)

    async def _refresh(self, interaction: discord.Interaction):
        self.mission_select.options = self._mission_options()
        self.difficulty_select.options = self._difficulty_options()
        await interaction.response.edit_message(embed=self._build_preview_embed(), view=self)

    async def _type_select_callback(self, interaction: discord.Interaction):
        self.aar_type = self.type_select.values[0]
        if self.aar_type == "pvp":
            self.difficulty = "@PvP Difficulty"
            self.mission = "PvP Match"
        else:
            self.difficulty = "@Ruthless"
            self.mission = "Inferno"
        await self._refresh(interaction)

    async def _mission_select_callback(self, interaction: discord.Interaction):
        self.mission = self.mission_select.values[0]
        await self._refresh(interaction)

    async def _difficulty_select_callback(self, interaction: discord.Interaction):
        self.difficulty = self.difficulty_select.values[0]
        await self._refresh(interaction)

    async def _details_button_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AARSubmissionDetailsModal(self))

    async def _submit_button_callback(self, interaction: discord.Interaction):
        report = self._compose_report()
        target_channel = _resolve_aar_submission_channel(interaction.guild)
        if target_channel is None:
            await interaction.response.send_message("Unable to resolve an AAR submission channel.", ephemeral=True)
            return

        try:
            await target_channel.send(report, allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True))
        except Exception as exc:
            _g.logger.exception(f"submit_aar failed to post to channel {getattr(target_channel,'id',None)}: {exc}")
            await interaction.response.send_message("The AAR could not be posted to the configured channel.", ephemeral=True)
            return

        await interaction.response.edit_message(
            content=(
                f"Submitted test report to {target_channel.mention} (non-ingestable)."
                if self.testing_mode
                else f"Submitted to {target_channel.mention}."
            ),
            embed=self._build_preview_embed(),
            view=None,
        )


@_g.bot.tree.command(
    name="submit_aar",
    description="Compose and submit an AAR draft to the configured AAR channel.",
)
async def submit_aar(interaction: discord.Interaction):
    if not (_b("check_command_permission")(interaction.user, "submit_aar") and _b("is_allowed_channel")(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    view = AARSubmissionView(interaction.guild, interaction.user)
    await interaction.response.send_message(embed=view._build_preview_embed(), view=view, ephemeral=True)


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
    # Defense of Herisor mission tag tracking (mention-only on Mission line)
    herisor_defense_in_mission = False
    # Dual Vigil tracking (2-brother Absolute-only Black Laurels missions)
    dual_vigil_in_mission = False
    # Pipehitter tracking
    pipehitter_mentioned = False
    # Watch Command role mention (required for Initiation Trials)
    watch_command_mentioned = False
    # Mission rank (A/B/C/D)
    rank = None
    # PvP AAR fields
    pvp_map = None
    pvp_map_line_present = False
    pvp_game_mode = None
    pvp_game_mode_line_present = False
    pvp_result = None
    pvp_result_line_present = False
    pvp_difficulty_role_present = False
    team_mentions_count = 0

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("rank:"):
            rank = line.split(":", 1)[1].strip().upper()

        elif lower.startswith("mission:"):
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
            # Defense of Herisor tag must be a role mention on the Mission line
            if f"<@&{HERISOR_DEFENSE_TAG_ROLE_ID}>" in mission:
                herisor_defense_in_mission = True
            # Check if Dual Vigil is in mission line (role ID or resolved name)
            if f"<@&{DUAL_VIGIL_ROLE_ID}>" in mission or ("dual vigil" in mission.lower()):
                dual_vigil_in_mission = True
            # If mission contains a trial-like token, mark the legacy initiation flag
            try:
                if re.search(r"\b-?\d+/\d+\b", mission) or "trial" in mission.lower():
                    initiation_trial = True
            except Exception:
                pass
        elif lower.startswith("difficulty:") or lower.startswith("threat:"):
            if f"<@&{PVP_DIFFICULTY_ROLE_ID}>" in raw_line:
                pvp_difficulty_role_present = True
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

        elif lower.startswith("map:"):
            pvp_map_line_present = True
            pvp_map = line.split(":", 1)[1].strip()

        elif lower.startswith("game mode:"):
            pvp_game_mode_line_present = True
            pvp_game_mode = line.split(":", 1)[1].strip()

        elif lower.startswith("result:"):
            pvp_result_line_present = True
            raw_result = line.split(":", 1)[1].strip()
            pvp_result = _normalize_pvp_result_token(raw_result)

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
            # A single initiation report may include two inductees even if the report
            # also shows mixed progress markers such as "1/3 & 2/3".
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
            # Extract the first number from the line (may have role mentions after it)
            parts = line.split(":", 1)
            try:
                remainder = parts[1].strip()
                match = re.search(r'\d+', remainder)
                if match:
                    waves = int(match.group())
                else:
                    waves = None
            except Exception:
                waves = None
            # Siege templates often omit Mission and place challenge tags on Wave(s) line.
            if f"<@&{HERISOR_DEFENSE_TAG_ROLE_ID}>" in raw_line:
                herisor_defense_in_mission = True

    is_pvp_aar = bool(pvp_map_line_present or pvp_game_mode_line_present or pvp_result_line_present)
    aar_type = "pvp" if is_pvp_aar else "pve"

    difficulty_class = "pvp_ops" if is_pvp_aar else classify_difficulty(difficulty)
    points_for_op = PVP_RESULT_POINTS.get(pvp_result or "", 0) if is_pvp_aar else compute_points_for_op(difficulty_class, waves)
    gene_seed_base_points_for_carrier = 0
    if (not is_pvp_aar) and gene_seed_status == "carried":
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
        role_id=CHAPTER_APPROVED_ROLE_ID,
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
        if (not is_pvp_aar) and chapter_approved and getattr(message, "created_at", None):
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
            if (
                "++ end of report ++" in line.lower()
                or "ᴇɴᴅ ᴏғ ʀᴇᴘᴏʀᴛ" in line
                or "++ 𝐄𝐍𝐃 𝐎𝐅 𝐑𝐄𝐏𝐎𝐑𝐓 ++" in line
            ):
                break
            if not line:
                continue

            all_ids_here = get_all_user_ids_in_line(raw_line, message)
            team_mentions_count += len(all_ids_here)
            ids_here = list(dict.fromkeys(all_ids_here))
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
    _author = getattr(message, "author", None)
    _submitter_id = str(_author.id) if _author and _author.id is not None else None
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
        # Defense of Herisor mission tag tracking for validation
        "herisor_defense_in_mission": herisor_defense_in_mission,
        # Dual Vigil tracking for validation and challenge progress
        "dual_vigil_in_mission": dual_vigil_in_mission,
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
        # ID of the member who posted the AAR (for verifier tier bonus); may be
        # absent when message stubs (e.g. in unit tests) lack an author.
        "submitter_id": _submitter_id,
        # Mission rank (A/B/C/D)
        "rank": rank,
        # AAR type discriminator + PvP metadata
        "aar_type": aar_type,
        "pvp_map": pvp_map,
        "pvp_map_line_present": pvp_map_line_present,
        "pvp_game_mode": pvp_game_mode,
        "pvp_game_mode_line_present": pvp_game_mode_line_present,
        "pvp_result": pvp_result,
        "pvp_result_line_present": pvp_result_line_present,
        "pvp_difficulty_role_present": pvp_difficulty_role_present,
        "team_mentions_count": team_mentions_count,
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

    if (record.get("aar_type") or "").lower() == "pvp":
        pvp_map = (record.get("pvp_map") or "").strip().lower()
        pvp_game_mode = (record.get("pvp_game_mode") or "").strip().lower()
        pvp_result = (record.get("pvp_result") or "").strip().upper()
        brothers = [str(b) for b in (record.get("brother_ids") or [])]

        if not record.get("pvp_map_line_present"):
            errors.append("Map is missing (line starting with 'Map:').")
        elif not pvp_map:
            errors.append("Map is missing (line starting with 'Map:').")
        elif pvp_map not in PVP_ALLOWED_MAPS:
            allowed_maps = ", ".join(sorted(m.title() for m in PVP_ALLOWED_MAPS))
            errors.append(f"Map '{record.get('pvp_map')}' is not valid; expected one of: {allowed_maps}.")

        if not record.get("pvp_game_mode_line_present"):
            errors.append("Game Mode is missing (line starting with 'Game Mode:').")
        elif not pvp_game_mode:
            errors.append("Game Mode is missing (line starting with 'Game Mode:').")
        elif pvp_game_mode not in PVP_ALLOWED_GAME_MODES:
            allowed_modes = ", ".join(sorted(m.title() for m in PVP_ALLOWED_GAME_MODES))
            errors.append(
                f"Game Mode '{record.get('pvp_game_mode')}' is not valid; expected one of: {allowed_modes}."
            )

        if not record.get("pvp_result_line_present"):
            errors.append("Result is missing (line starting with 'Result:').")
        elif not pvp_result or pvp_result not in PVP_RESULT_POINTS:
            errors.append("Result must be Win/Lose (or W/L).")

        if not record.get("difficulty"):
            errors.append("Difficulty is missing (line starting with 'Difficulty:').")
        elif not record.get("pvp_difficulty_role_present"):
            errors.append(f"PvP Difficulty must include <@&{PVP_DIFFICULTY_ROLE_ID}>.")

        distinct_brothers = list(dict.fromkeys(brothers))
        total_team_mentions = int(record.get("team_mentions_count", len(brothers)) or 0)
        if len(distinct_brothers) < 2 or len(distinct_brothers) > 6:
            errors.append("PvP Team requires between 2 and 6 Brothers listed under the 'Team:' section.")
        if total_team_mentions != len(distinct_brothers):
            errors.append("PvP Team must not contain duplicate Brother mentions.")

        return _annotate_aar_error_messages(errors)

    mission = record.get("mission")
    difficulty = record.get("difficulty") or ""
    waves = record.get("waves")
    brother_waves = record.get("brother_waves") or {}
    armory_data = record.get("armory_data")
    brothers = record.get("brother_ids") or []
    gene_status = record.get("gene_seed_status")
    gene_carrier = record.get("gene_seed_carrier_id")
    rank = record.get("rank")

    # 0) Rank required and must be A, B, C, or D — only when a mission is present
    if mission:
        if not rank:
            errors.append("Rank is missing (line starting with 'Rank:').")
        elif rank not in ("A", "B", "C", "D"):
            errors.append(f"Rank '{rank}' is not valid; must be A, B, C, or D.")

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
        # Disruption, Exfiltration, Purgation
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
                    "purgation",
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
                # Parse ISO format timestamp (accept trailing 'Z' and normalize to UTC)
                normalized_timestamp = timestamp_str.strip()
                if normalized_timestamp.endswith("Z"):
                    normalized_timestamp = normalized_timestamp[:-1] + "+00:00"
                message_created_at = datetime.fromisoformat(normalized_timestamp)
                if message_created_at.tzinfo is None:
                    message_created_at = message_created_at.replace(tzinfo=timezone.utc)
                else:
                    message_created_at = message_created_at.astimezone(timezone.utc)
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
        has_hard_siege = "hard-siege" in dlower
        has_black_reef_persecution = record.get("black_reef_persecution_in_mission", False)
        has_leviathan_in_mission = record.get("leviathan_protocol_in_mission", False)
        has_herisor_defense = record.get("herisor_defense_in_mission", False)
        mission_lower = (mission or "").lower().strip()
        mission_clean = re.sub(r"<.*", "", mission_lower).strip()
        bl_leviathan_kadaku_unlock = has_leviathan_in_mission and mission_clean in KADAKU_CAMPAIGN_REQUIRED_MISSIONS
        herisor_hard_strat_allowed = mission_clean in {"termination", "reclamation"}
        waves_ok_for_herisor_siege = False
        try:
            if isinstance(brother_waves, dict) and brother_waves:
                wave_counts = []
                for value in brother_waves.values():
                    try:
                        wave_counts.append(int(value))
                    except Exception:
                        pass
                if wave_counts:
                    waves_ok_for_herisor_siege = max(wave_counts) >= 10
            if not waves_ok_for_herisor_siege:
                waves_ok_for_herisor_siege = int(waves or 0) >= 10
        except Exception:
            waves_ok_for_herisor_siege = False
        # Black Reef Persecution on Mission line unlocks Black Laurels with Hard-Stratagem
        bl_hard_strat_unlocked = has_hard_stratagem and has_black_reef_persecution
        # Defense of Herisor on Mission line can also unlock Black Laurels for:
        # - Hard-Stratagem Termination/Reclamation
        # - Hard-Siege with Waves >= 10
        bl_herisor_hard_unlock = has_herisor_defense and (
            (has_hard_stratagem and herisor_hard_strat_allowed)
            or (has_hard_siege and waves_ok_for_herisor_siege)
        )

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
            elif has_herisor_defense:
                # @Defense_of_Herisor owns its 3-brother validation in the dedicated block below.
                pass
            elif bl_hard_strat_unlocked:
                if len(brothers) not in (2, 3):
                    errors.append("@Black_Laurels with @Black_Reef_Persecution requires 2 or 3 Brothers.")
            else:
                if len(brothers) != 3:
                    errors.append("@Black_Laurels requires exactly 3 Brothers (a full fireteam).")
            if is_in_grace_period:
                # GRACE PERIOD (before Feb 20, 2026): Allow Black Laurels on Mission OR Difficulty
                # Only check: must have @Absolute or @Omega when Black Laurels is present
                if (
                    not has_absolute
                    and not has_omega
                    and not bl_hard_strat_unlocked
                    and not bl_herisor_hard_unlock
                    and not bl_leviathan_kadaku_unlock
                ):
                    errors.append(
                        "@Black_Laurels requires @Absolute or @Omega on the Difficulty line "
                        "(or @Leviathan_Protocol on the Mission line for Kadaku missions)."
                    )
                # Check eligible missions (Omega and BRP+Hard-Strat allow any mission)
                if not has_omega and not bl_hard_strat_unlocked:
                    if mission_clean and mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS:
                        errors.append(
                            "@Black_Laurels may only be used on eligible missions: "
                            "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                            "Exfiltration, Termination, Reclamation, Disruption, Purgation."
                        )
            else:
                # STRICT MODE (Feb 20, 2026+): Black Laurels ONLY on Mission line with @Absolute/@Omega on Difficulty
                # Exception: @Hard-Stratagem is also allowed when @Black_Reef_Persecution is on the Mission line
                if has_black_laurels_difficulty and not has_black_laurels_mission:
                    errors.append("@Black_Laurels must be placed on the Mission line only.")
                if (
                    not has_absolute
                    and not has_omega
                    and not bl_hard_strat_unlocked
                    and not bl_herisor_hard_unlock
                    and not bl_leviathan_kadaku_unlock
                ):
                    errors.append(
                        "@Black_Laurels requires @Absolute or @Omega on the Difficulty line "
                        "(or @Hard-Stratagem when @Black_Reef_Persecution is on the Mission line, "
                        "or @Hard-Stratagem/@Hard-Siege when @Defense_of_Herisor is on the Mission line, "
                        "or @Leviathan_Protocol on the Mission line for Kadaku missions)."
                    )
                # Check eligible missions (Omega and BRP+Hard-Strat allow any mission)
                if not has_omega and not bl_hard_strat_unlocked:
                    if mission_clean and mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS:
                        errors.append(
                            "@Black_Laurels may only be used on eligible missions: "
                            "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                            "Exfiltration, Termination, Reclamation, Disruption, Purgation."
                        )
                # Black Laurels cannot be mentioned elsewhere in strict mode
                if record.get("black_laurels_mentioned_elsewhere", False):
                    errors.append("@Black_Laurels must be placed on the Mission line, not elsewhere in the AAR.")

        # Dual Vigil validation: Absolute only, exactly 2 brothers, eligible missions
        has_dual_vigil = record.get("dual_vigil_in_mission", False)
        if has_dual_vigil:
            if not has_absolute:
                errors.append("@Dual_Vigil requires @Absolute on the Difficulty line.")
            if len(brothers) != 2:
                errors.append("@Dual_Vigil requires exactly 2 Brothers.")
            dv_mission_lower = (mission or "").lower().strip()
            dv_mission_clean = re.sub(r"<.*", "", dv_mission_lower).strip()
            if dv_mission_clean and dv_mission_clean not in DUAL_VIGIL_REQUIRED_MISSIONS:
                errors.append(
                    "@Dual_Vigil may only be used on Black Laurels-eligible missions: "
                    "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                    "Exfiltration, Termination, Reclamation, Disruption, Purgation."
                )

        # Defense of Herisor tag validation: mention-only tag is parsed from Mission line
        # (and from Waves line for Siege templates).
        # If present, enforce challenge-safe operation constraints at ingest time.
        if has_herisor_defense:
            if not has_hard_stratagem and not has_hard_siege:
                errors.append("@Defense_of_Herisor requires @Hard-Stratagem or @Hard-Siege on the Difficulty line.")
            if len(brothers) != 3:
                errors.append("@Defense_of_Herisor requires exactly 3 Brothers.")
            if has_hard_stratagem and not herisor_hard_strat_allowed:
                errors.append("@Defense_of_Herisor with @Hard-Stratagem is only valid for Termination or Reclamation.")
            if has_hard_siege and not waves_ok_for_herisor_siege:
                errors.append("@Defense_of_Herisor with @Hard-Siege requires Waves 10+.")

        # Leviathan Protocol validation: must be on Mission line only
        leviathan_in_difficulty = record.get("leviathan_protocol_in_difficulty", False)
        _leviathan_in_mission = record.get("leviathan_protocol_in_mission", False)  # Reserved for future validation
        if leviathan_in_difficulty:
            errors.append("@Leviathan_Protocol must be placed on the Mission line, not the Difficulty line.")

        # Pipehitter validation: only allowed on eligible missions
        if record.get("pipehitter_mentioned", False):
            if not has_hard_stratagem:
                errors.append("@Pipehitter/@Distinguished_Pipehitter requires @Hard-Stratagem on the Difficulty line.")
            mission_lower = (mission or "").lower().strip()
            mission_clean = re.sub(r"<.*", "", mission_lower).strip()
            if mission_clean and mission_clean not in PIPEHITTER_ELIGIBLE_MISSIONS:
                errors.append(
                    "@Pipehitter/@Distinguished_Pipehitter may only be used on eligible missions: "
                    "Inferno, Vox Liberatis, Reliquary, Fall of Atreus, Termination, Obelisk, "
                    "Exfiltration, Vortex, Reclamation, Disruption, Purgation."
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

    return _annotate_aar_error_messages(errors)


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


AAR_EDIT_INSTRUCTION_NOTE = "DO NOT DELETE AAR JUST EDIT"


def _annotate_aar_error_messages(errors: list[str]) -> list[str]:
    """Append a standard edit instruction to human-facing AAR error reasons."""
    annotated: list[str] = []
    for err in errors or []:
        text = str(err or "").strip()
        if not text:
            continue
        if text.startswith("Jump URL:"):
            annotated.append(text)
            continue
        if AAR_EDIT_INSTRUCTION_NOTE in text:
            annotated.append(text)
            continue
        annotated.append(f"{text} ({AAR_EDIT_INSTRUCTION_NOTE})")
    return annotated


def log_aar_errors(aar_id: int, errors: list[str]):
    data = _load_json_dict(AAR_ERRORS_PATH)
    data[str(aar_id)] = {"errors": _annotate_aar_error_messages(errors)}
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
    annotated_errors = _annotate_aar_error_messages(errors)
    entry = {
        "errors": annotated_errors,
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
        annotated_errors = _annotate_aar_error_messages(errors)
        filtered = [e for e in annotated_errors if e and not e.startswith("Jump URL:")]
        if not filtered:
            filtered = (
                _annotate_aar_error_messages(errors[:1])
                if errors
                else [f"Rejected by archive bot. ({AAR_EDIT_INSTRUCTION_NOTE})"]
            )
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

    # Store the verifier tier bonus separately so it can be applied only to the
    # submitter's own totals; do NOT mutate the shared per-member points_for_op.
    submitter_id = record.get("submitter_id")
    if submitter_id:
        try:
            from . import terminus_ops as _terminus
            bonus = _terminus.get_verifier_tier_bonus(submitter_id)
            if bonus > 0:
                record["verifier_tier_bonus"] = bonus
        except Exception:
            pass  # never block a save on a bonus lookup failure

    await _g.DATASTORE.set_record(key, record)
    await _g.DATASTORE.add_processed_id(key)


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
    "_sweep_challenge_completions",
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
