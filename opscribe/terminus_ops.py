"""Terminus Kill Log subsystem.

Tracks kill log entries for the Terminus Slayer challenge:
  - /submit_kill_log  — submit a Terminus kill (video required)
  - /verifier_standing — ephemeral leaderboard of rolling 7-day verifier activity
  - Persistent Verify/Deny buttons on each kill log embed
  - Apothecary notification + Force Approve/Remove buttons on denial
  - 72-hour reminder for stale pending entries
  - Verifier tier system: rolling 7-day verify+deny count → +1/+2/+3 AAR bonus
"""

import json
import os
import re
import shutil
import sys as _sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

from .constants import (  # noqa: F401
    AAR_CHANNEL_ID,
    APOTHECARY_ROLE_NAME,
    APOTHECARY_STAFF_CHANNEL_ID,
    BLACK_LAURELS_ROLE_ID,
    BLACK_LAURELS_STRICT_ENFORCEMENT_DATE,
    BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
    BLACK_REEF_REQUIRED_MISSIONS,
    BLACK_LAURELS_REQUIRED_MISSIONS,
    CHALLENGE_ROLES,
    CHALLENGE_PROGRESS_PATH,
    CRUX_TERMINATUS_ROLE_ID,
    DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
    DISTINGUISHED_PIPEHITTER_ROLE_ID,
    DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID,
    DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID,
    DUAL_VIGIL_REQUIRED_MISSIONS,
    DUAL_VIGIL_ROLE_ID,
    HERISOR_DEFENSE_MEDAL_ROLE_ID,
    KADAKU_CAMPAIGN_MEDAL_ROLE_ID,
    KADAKU_CAMPAIGN_REQUIRED_MISSIONS,
    KILL_LOG_CHANNEL_ID,
    KILL_LOG_CLASS_ROLES,
    KILL_LOG_REVIEW_DELAY_MINUTES,
    KILL_LOG_REMINDER_HOURS,
    MASTER_TERMINUS_SLAYER_ROLE_ID,
    ORDER_OMEGA_REQUIRED_MISSIONS,
    PIPEHITTER_ROLE_ID,
    THE_ORDER_OMEGA_ROLE_ID,
    TERMINUS_SLAYER_CLASS_AWARD_TYPES,
    TERMINUS_SLAYER_PATH,
    TERMINUS_SLAYER_ROLE_IDS,
    TERMINUS_TYPES,
    TERMINUS_VERIFIER_RANKS,
    VERIFIER_TIER_THRESHOLDS,
)
from .permissions import BATTLE_LINE_RANKS, CHAMPION_RANKS, SPECIALIST_RANKS, HIGH_COMMAND_RANKS, WATCH_COMMAND_ROLES
from . import _bot_globals as _g
from .challenge_policy import evaluate_crux_bl_rank_a


# Any role that counts as a server member (Watch Brother or higher on any track)
_MEMBER_RANKS = BATTLE_LINE_RANKS | CHAMPION_RANKS | SPECIALIST_RANKS | HIGH_COMMAND_RANKS


def _b(name):
    """Resolve name via bot module (test-mock compatibility)."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


_AAR_LINK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)$")


async def _validate_aar_link(
    aar_link: str, guild: discord.Guild, brother_id: str
) -> Optional[str]:
    """Return an error string if aar_link fails any kill log pre-condition."""
    m = _AAR_LINK_RE.match(aar_link.strip())
    if not m:
        return "The AAR link must be a Discord message URL (`https://discord.com/channels/…`)."
    channel_id_str, message_id_str = m.groups()
    if int(channel_id_str) != AAR_CHANNEL_ID:
        return f"The AAR link must point to a message in <#{AAR_CHANNEL_ID}>."
    aar_ch = guild.get_channel(AAR_CHANNEL_ID)
    if aar_ch is None:
        return "AAR channel not accessible. Contact the Forgemaster."
    try:
        msg = await aar_ch.fetch_message(int(message_id_str))
    except discord.NotFound:
        return "That AAR message was not found. Double-check the link."
    except discord.Forbidden:
        return "Bot lacks permission to read the AAR channel."

    # Check difficulty and participation. Use the ingested DATASTORE record when
    # available; fall back to raw message content for un-ingested AARs.
    record = _g.DATASTORE.get_record(message_id_str) if _g.DATASTORE else None
    if record:
        if brother_id not in [str(b) for b in record.get("brother_ids", [])]:
            return "You must have participated in the linked AAR to submit a kill log for it."
    else:
        content = msg.content or ""
        if not re.search(rf"<@!?{re.escape(brother_id)}>", content):
            return "You must have participated in the linked AAR to submit a kill log for it."
    return None


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict = {
    "entries": {},
    "progress": {},
    "verifier_actions": {},
    "next_id": 1,
}


def _load_state() -> dict:
    if not os.path.exists(TERMINUS_SLAYER_PATH):
        return {k: (v.copy() if isinstance(v, dict) else v) for k, v in _EMPTY_STATE.items()}
    with open(TERMINUS_SLAYER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Ensure all keys exist for forward compatibility
    for k, v in _EMPTY_STATE.items():
        if k not in data:
            data[k] = v.copy() if isinstance(v, dict) else v
    return data


def _save_state(state: dict) -> None:
    bak = TERMINUS_SLAYER_PATH + ".bak"
    if os.path.exists(TERMINUS_SLAYER_PATH):
        shutil.copy2(TERMINUS_SLAYER_PATH, bak)
    tmp = TERMINUS_SLAYER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, TERMINUS_SLAYER_PATH)


def _next_kill_log_id(state: dict) -> str:
    n = state.get("next_id", 1)
    state["next_id"] = n + 1
    return f"KL-{n:04d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _get_verified_count(state: dict, brother_id: str, class_role_id: int, terminus_type: str) -> int:
    prog = state.get("progress", {})
    return prog.get(str(brother_id), {}).get(str(class_role_id), {}).get(terminus_type, 0)


def _increment_progress(state: dict, brother_id: str, class_role_id: int, terminus_type: str) -> int:
    """Increment verified kill count and return new total."""
    prog = state.setdefault("progress", {})
    bid = str(brother_id)
    cid = str(class_role_id)
    prog.setdefault(bid, {}).setdefault(cid, {k: 0 for k in TERMINUS_TYPES})
    current = prog[bid][cid].get(terminus_type, 0)
    new_val = min(current + 1, 3)
    prog[bid][cid][terminus_type] = new_val
    return new_val


def _class_is_complete(state: dict, brother_id: str, class_role_id: int) -> bool:
    """Return True if all 3 terminus types have 3 verified kills for this class."""
    prog = state.get("progress", {})
    class_data = prog.get(str(brother_id), {}).get(str(class_role_id), {})
    return all(class_data.get(t, 0) >= 3 for t in TERMINUS_TYPES)


def _decrement_progress(state: dict, brother_id: str, class_role_id: int, terminus_type: str) -> int:
    """Decrement verified kill count (floor 0) and return new total."""
    prog = state.setdefault("progress", {})
    bid = str(brother_id)
    cid = str(class_role_id)
    prog.setdefault(bid, {}).setdefault(cid, {k: 0 for k in TERMINUS_TYPES})
    current = prog[bid][cid].get(terminus_type, 0)
    new_val = max(current - 1, 0)
    prog[bid][cid][terminus_type] = new_val
    return new_val


def _would_break_class_completion(state: dict, brother_id: str, class_role_id: int, terminus_type: str) -> bool:
    """Return True if decrementing this terminus type would make the class incomplete.

    The class is currently complete only if *every* terminus type is at 3.  If
    the entry being revoked holds one of those 3s, removing it drops the class
    below the completion threshold.
    """
    prog = state.get("progress", {})
    class_data = prog.get(str(brother_id), {}).get(str(class_role_id), {})
    # Class must currently be fully complete AND this type must be at exactly 3
    # (or above, though 3 is the cap) for the decrement to break it.
    if not all(class_data.get(t, 0) >= 3 for t in TERMINUS_TYPES):
        return False
    return class_data.get(terminus_type, 0) >= 3


def _dequeue_terminus_class_award(member_id: str, class_role_id: int) -> bool:
    """Remove a queued Terminus Slayer class award announcement if present.

    Returns True if an entry was found and removed, False if the queue held no
    matching entry (meaning the announcement was already delivered).
    """
    award_type = TERMINUS_SLAYER_CLASS_AWARD_TYPES.get(class_role_id)
    if not award_type:
        return False
    load_fn = _b("_load_award_queue")
    save_fn = _b("_save_award_queue")
    if load_fn is None or save_fn is None:
        return False
    queue = load_fn()
    new_queue = [
        item for item in queue
        if not (str(item.get("member_id")) == str(member_id) and item.get("award_type") == award_type)
    ]
    if len(new_queue) == len(queue):
        return False  # nothing removed
    save_fn(new_queue)
    return True


# ---------------------------------------------------------------------------
# Verifier tier helpers
# ---------------------------------------------------------------------------

def _record_verifier_action(state: dict, vet_id: str, action: str, kill_log_id: str) -> None:
    """Record a verify or deny action for tier calculation."""
    va = state.setdefault("verifier_actions", {})
    vid = str(vet_id)
    va.setdefault(vid, []).append({
        "action": action,
        "kill_log_id": kill_log_id,
        "timestamp": _now_iso(),
    })
    # Prune entries older than 8 days to keep file size bounded
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    va[vid] = [a for a in va[vid] if a["timestamp"] >= cutoff]


def get_verifier_tier(vet_id: str) -> int:
    """Return 0-3 verifier tier for a vet based on rolling 7-day action count."""
    try:
        state = _load_state()
        actions = state.get("verifier_actions", {}).get(str(vet_id), [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = sum(1 for a in actions if _parse_dt(a["timestamp"]) >= cutoff)
        for threshold, tier, _bonus in VERIFIER_TIER_THRESHOLDS:
            if recent >= threshold:
                return tier
        return 0
    except Exception:
        return 0


def get_verifier_tier_bonus(vet_id: str) -> int:
    """Return AAR point bonus (0-3) for a vet based on their verifier tier."""
    tier = get_verifier_tier(vet_id)
    for _threshold, t, bonus in VERIFIER_TIER_THRESHOLDS:
        if tier == t:
            return bonus
    return 0


def _get_rolling_action_count(state: dict, vet_id: str) -> int:
    actions = state.get("verifier_actions", {}).get(str(vet_id), [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    return sum(1 for a in actions if _parse_dt(a["timestamp"]) >= cutoff)


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _status_line(entry: dict) -> str:
    status = entry.get("status", "pending")
    verifications = entry.get("verifications", [])
    if status == "pending":
        return f"⏳ Awaiting Verification ({len(verifications)}/3)"
    if status == "verified":
        return "✅ Kill Confirmed (3/3 verified)"
    if status == "force_approved":
        return "✅ Kill Confirmed (Apothecary Approved)"
    if status == "under_review":
        return "⚠️ Under Review — Awaiting Apothecary Decision"
    if status == "rejected":
        return "❌ Rejected by Apothecary"
    if status == "apo_revoked":
        return "🛑 Revoked by Apothecary"
    return status


def _format_verifications_field(entry: dict) -> str:
    """Return a human-readable verifier list with timestamps for the kill log embed.

    Uses the per-verifier ``verification_log`` if present; falls back to a
    bare list of vet_ids from ``verifications`` for legacy entries without
    per-verifier timestamps.
    """
    log = entry.get("verification_log") or []
    if log:
        lines = []
        for rec in log:
            vid = rec.get("vet_id")
            at = rec.get("at") or ""
            if not vid:
                continue
            try:
                ts = int(_parse_dt(at).timestamp()) if at else None
            except Exception:
                ts = None
            if ts is not None:
                lines.append(f"<@{vid}> — <t:{ts}:f>")
            else:
                lines.append(f"<@{vid}>")
        return "\n".join(lines)
    # Legacy fallback
    verifications = entry.get("verifications") or []
    if not verifications:
        return ""
    return "\n".join(f"<@{v}>" for v in verifications)


def _build_kill_log_embed(entry: dict, guild: Optional[discord.Guild] = None) -> discord.Embed:
    brother_id = entry["brother_id"]
    class_name = entry["class_name"]
    terminus = entry["terminus_type"]
    kill_number = entry["kill_number"]
    aar_link = entry["aar_link"]
    video_url = entry.get("video_url") or entry.get("video_attachment_url") or ""
    verified_count = entry.get("verified_prior_count", 0)

    colour = {
        "pending": discord.Colour.from_rgb(80, 80, 80),
        "verified": discord.Colour.green(),
        "force_approved": discord.Colour.green(),
        "under_review": discord.Colour.orange(),
        "rejected": discord.Colour.red(),
    }.get(entry.get("status", "pending"), discord.Colour.from_rgb(80, 80, 80))

    embed = discord.Embed(
        title="⚔️ Terminus Kill Report",
        colour=colour,
    )
    embed.add_field(name="Brother", value=f"<@{brother_id}>", inline=True)
    embed.add_field(name="Class", value=class_name, inline=True)
    embed.add_field(name="Terminus", value=terminus, inline=True)
    embed.add_field(name="Kill Attempt", value=f"{kill_number}/3", inline=True)
    embed.add_field(
        name="Prior Verified",
        value=f"{verified_count}/3 confirmed before this submission",
        inline=True,
    )
    embed.add_field(name="AAR", value=f"[View Report]({aar_link})", inline=True)
    if video_url:
        embed.add_field(name="Recording", value=f"[Watch]({video_url})", inline=True)
    embed.add_field(
        name="Status",
        value=_status_line(entry),
        inline=False,
    )
    verifications_field = _format_verifications_field(entry)
    if verifications_field:
        embed.add_field(name="Verifications", value=verifications_field, inline=False)
    embed.set_footer(text=f"Kill Log ID: {entry['kill_log_id']}")
    embed.timestamp = _parse_dt(entry["submitted_at"])
    return embed


def _build_apo_notification_embed(entry: dict) -> discord.Embed:
    embed = discord.Embed(
        title="⚠️ Terminus Kill Log — Under Review",
        description=(
            "A kill log entry has been **denied** by a Watch Veteran. "
            "Apothecary action required."
        ),
        colour=discord.Colour.orange(),
    )
    embed.add_field(name="Kill Log", value=entry["kill_log_id"], inline=True)
    embed.add_field(name="Brother", value=f"<@{entry['brother_id']}>", inline=True)
    embed.add_field(
        name="Class / Terminus",
        value=f"{entry['class_name']} / {entry['terminus_type']}",
        inline=True,
    )
    denied_by = entry.get("denied_by")
    denied_at = entry.get("denied_at", "")
    if denied_by:
        embed.add_field(name="Denied By", value=f"<@{denied_by}>", inline=True)
    if denied_at:
        ts = int(_parse_dt(denied_at).timestamp())
        embed.add_field(name="Denied At", value=f"<t:{ts}:f>", inline=True)
    deny_reason = entry.get("deny_reason", "").strip()
    if deny_reason:
        embed.add_field(name="Deny Reason", value=deny_reason, inline=False)
    video_url = entry.get("video_url") or entry.get("video_attachment_url") or ""
    if video_url:
        embed.add_field(name="Recording", value=f"[Watch]({video_url})", inline=False)
    embed.set_footer(text="Use the buttons below to make a final ruling.")
    return embed


def _build_completion_embed(brother_id: str, class_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="🏆 Terminus Slayer — Class Cleared",
        description=(
            f"<@{brother_id}> has confirmed all 3 kills on every current Terminus type "
            f"as **{class_name}**.\n\n"
            "The Terminus Slayer award for this class may now be granted."
        ),
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Brother", value=f"<@{brother_id}>", inline=True)
    embed.add_field(name="Class", value=class_name, inline=True)
    for t in TERMINUS_TYPES:
        embed.add_field(name=t, value="✅ 3/3", inline=True)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def _build_reminder_embed(stale_entries: list[dict], guild_id: int) -> discord.Embed:
    embed = discord.Embed(
        title="📋 Kill Log — Pending Verification",
        description=(
            "The following kill log entries have been awaiting verification "
            f"for over {KILL_LOG_REMINDER_HOURS} hours. "
            "Watch Veterans, please review."
        ),
        colour=discord.Colour.from_rgb(180, 140, 60),
    )
    for entry in stale_entries[:10]:  # cap at 10 lines
        ts = int(_parse_dt(entry["submitted_at"]).timestamp())
        embed_msg_id = entry.get("embed_message_id")
        if embed_msg_id:
            link = (
                f"https://discord.com/channels/{guild_id}/"
                f"{KILL_LOG_CHANNEL_ID}/{embed_msg_id}"
            )
            entry_label = f"[{entry['kill_log_id']}]({link})"
        else:
            entry_label = entry["kill_log_id"]
        embed.add_field(
            name=f"{entry['kill_log_id']} — {entry['class_name']} / {entry['terminus_type']}",
            value=(
                f"{entry_label} · <@{entry['brother_id']}> · "
                f"Submitted <t:{ts}:R> · "
                f"{len(entry.get('verifications', []))}/3 verified"
            ),
            inline=False,
        )
    if len(stale_entries) > 10:
        embed.set_footer(text=f"…and {len(stale_entries) - 10} more. Check the kill log channel.")
    return embed


# ---------------------------------------------------------------------------
# Persistent View: kill log entry (Verify / Deny)
# ---------------------------------------------------------------------------

class TerminusKillLogView(discord.ui.View):
    """Persistent verify/deny buttons for a single kill log entry."""

    def __init__(self, kill_log_id: str):
        super().__init__(timeout=None)
        self.kill_log_id = kill_log_id
        self._verify_btn.custom_id = f"terminus_verify:{kill_log_id}"
        self._deny_btn.custom_id = f"terminus_deny:{kill_log_id}"

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="terminus_verify:__placeholder__",
    )
    async def _verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_verify(interaction, self.kill_log_id)

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="terminus_deny:__placeholder__",
    )
    async def _deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DenyReasonModal(self.kill_log_id))


# ---------------------------------------------------------------------------
# Modal: deny reason
# ---------------------------------------------------------------------------

class DenyReasonModal(discord.ui.Modal, title="Deny Kill Log Entry"):
    """Optional reason modal shown when a verifier clicks Deny."""

    reason: discord.ui.TextInput = discord.ui.TextInput(
        label="Reason (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Describe why this entry is being flagged for review…",
        required=False,
        max_length=500,
    )

    def __init__(self, kill_log_id: str):
        super().__init__()
        self.kill_log_id = kill_log_id

    async def on_submit(self, interaction: discord.Interaction):
        await _handle_deny(interaction, self.kill_log_id, reason=self.reason.value or "")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if _g.logger:
            _g.logger.error(f"terminus_ops: DenyReasonModal error for {self.kill_log_id}: {error}")
        try:
            await interaction.response.send_message(
                "An error occurred while processing the denial. The entry status may already be updated — check the kill log channel.",
                ephemeral=True,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Persistent View: apothecary ruling (Force Approve / Remove Entry)
# ---------------------------------------------------------------------------

class TerminusApoView(discord.ui.View):
    """Persistent force-approve / remove buttons on the apo notification."""

    def __init__(self, kill_log_id: str):
        super().__init__(timeout=None)
        self.kill_log_id = kill_log_id
        self._approve_btn.custom_id = f"terminus_force_approve:{kill_log_id}"
        self._remove_btn.custom_id = f"terminus_remove:{kill_log_id}"

    @discord.ui.button(
        label="Force Approve",
        style=discord.ButtonStyle.success,
        emoji="⚔️",
        custom_id="terminus_force_approve:__placeholder__",
    )
    async def _approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_force_approve(interaction, self.kill_log_id)

    @discord.ui.button(
        label="Remove Entry",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="terminus_remove:__placeholder__",
    )
    async def _remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_remove_entry(interaction, self.kill_log_id)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _is_verifier(member: discord.Member) -> bool:
    return any(r.name in TERMINUS_VERIFIER_RANKS for r in member.roles)


def _is_apothecary(member: discord.Member) -> bool:
    apothecary_ranks = {"Watch Apothecary", "Chief Apothecary"}
    return any(r.name in apothecary_ranks for r in member.roles)


def _verifier_in_aar(vet_id: str, entry: dict) -> bool:
    """Return True if vet_id is listed as a participant in the entry's linked AAR.

    Uses the ingested DATASTORE record when available so the check is
    consistent with the participation gate in _validate_aar_link.
    """
    aar_link = entry.get("aar_link", "")
    m = _AAR_LINK_RE.match(aar_link.strip())
    if not m:
        return False
    _ch_id, message_id_str = m.groups()
    if _g.DATASTORE is None:
        return False
    record = _g.DATASTORE.get_record(message_id_str)
    if not record:
        return False
    return vet_id in [str(b) for b in record.get("brother_ids", [])]


# ---------------------------------------------------------------------------
# Button handlers
# ---------------------------------------------------------------------------

async def _handle_verify(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_verifier(interaction.user):
        await interaction.response.send_message(
            "Only Watch Veterans and above may verify kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    shame_msg: Optional[str] = None
    entry: Optional[dict] = None
    newly_confirmed = False
    class_complete = False
    brother_id: Optional[str] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "pending":
            error_msg = f"This entry is no longer pending (status: {entry['status']})."
        elif not _is_apothecary(interaction.user) and (
            datetime.now(timezone.utc) - _parse_dt(entry["submitted_at"])
        ) < timedelta(minutes=KILL_LOG_REVIEW_DELAY_MINUTES):
            vet_id = str(interaction.user.id)
            if _verifier_in_aar(vet_id, entry):
                error_msg = (
                    "Kill log entries cannot be verified until "
                    f"{KILL_LOG_REVIEW_DELAY_MINUTES} minutes after submission."
                )
            else:
                shame_msg = (
                    f"\N{EYES} {interaction.user.mention} is trying to **VERIFY** a kill log "
                    f"without watching the video or checking the AAR. "
                    f"Please make fun of them."
                )
        else:
            vet_id = str(interaction.user.id)
            brother_id = str(entry["brother_id"])

            if vet_id == brother_id:
                error_msg = "You cannot verify your own kill log entry."
            elif not _is_apothecary(interaction.user) and _verifier_in_aar(vet_id, entry):
                error_msg = (
                    "You participated in this operation and cannot verify this kill log. "
                    "Only an Apothecary may act on an entry from an AAR they ran in."
                )
            else:
                verifications = entry.setdefault("verifications", [])
                if vet_id in verifications:
                    error_msg = "You have already verified this entry."
                else:
                    verifications.append(vet_id)
                    entry.setdefault("verification_log", []).append(
                        {"vet_id": vet_id, "at": _now_iso()}
                    )
                    _record_verifier_action(state, vet_id, "verify", kill_log_id)

                    newly_confirmed = len(verifications) >= 3
                    if newly_confirmed:
                        entry["status"] = "verified"
                        entry["verified_at"] = _now_iso()
                        # Update progress
                        new_count = _increment_progress(
                            state,
                            brother_id,
                            entry["class_role_id"],
                            entry["terminus_type"],
                        )
                        class_complete = new_count >= 3 and _class_is_complete(
                            state, brother_id, entry["class_role_id"]
                        )
                    _save_state(state)

    # All Discord API calls happen outside the lock.
    if shame_msg:
        await interaction.response.send_message(shame_msg, ephemeral=False)
        return
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Update embed in-place
    guild = interaction.guild
    embed = _build_kill_log_embed(entry, guild)
    view = TerminusKillLogView(kill_log_id) if entry["status"] == "pending" else None
    await interaction.response.edit_message(embed=embed, view=view)

    # If confirmed, send completion notification if class is done
    if newly_confirmed and class_complete:
        await _notify_class_complete(guild, brother_id, entry["class_role_id"], entry["class_name"])


async def _handle_deny(interaction: discord.Interaction, kill_log_id: str, reason: str = "") -> None:
    if not _is_verifier(interaction.user):
        await interaction.response.send_message(
            "Only Watch Veterans and above may deny kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    shame_msg: Optional[str] = None
    entry: Optional[dict] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "pending":
            error_msg = f"This entry is no longer pending (status: {entry['status']})."
        elif not _is_apothecary(interaction.user) and (
            datetime.now(timezone.utc) - _parse_dt(entry["submitted_at"])
        ) < timedelta(minutes=KILL_LOG_REVIEW_DELAY_MINUTES):
            vet_id = str(interaction.user.id)
            if _verifier_in_aar(vet_id, entry):
                error_msg = (
                    "Kill log entries cannot be denied until "
                    f"{KILL_LOG_REVIEW_DELAY_MINUTES} minutes after submission."
                )
            else:
                shame_msg = (
                    f"\N{EYES} {interaction.user.mention} is trying to **DENY** a kill log "
                    f"without watching the video or checking the AAR. "
                    f"Please make fun of them."
                )
        else:
            vet_id = str(interaction.user.id)
            brother_id = str(entry["brother_id"])

            if vet_id == brother_id:
                error_msg = "You cannot deny your own kill log entry."
            elif not _is_apothecary(interaction.user) and _verifier_in_aar(vet_id, entry):
                error_msg = (
                    "You participated in this operation and cannot deny this kill log. "
                    "Only an Apothecary may act on an entry from an AAR they ran in."
                )
            else:
                entry["status"] = "under_review"
                entry["denied_by"] = vet_id
                entry["denied_at"] = _now_iso()
                if reason.strip():
                    entry["deny_reason"] = reason.strip()
                _record_verifier_action(state, vet_id, "deny", kill_log_id)
                _save_state(state)

    # All Discord API calls happen outside the lock.
    if shame_msg:
        await interaction.response.send_message(shame_msg, ephemeral=False)
        return
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Acknowledge the modal submission, then update the kill log embed independently.
    guild = interaction.guild
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: could not defer deny interaction: {exc}")

    try:
        await _refresh_kill_log_embed(guild, entry)
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: could not refresh kill log embed after deny: {exc}")

    # Notify apothecary channel — always attempted regardless of embed refresh outcome
    await _notify_apo_denial(guild, entry)


async def _handle_force_approve(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_apothecary(interaction.user):
        await interaction.response.send_message(
            "Only Watch Apothecaries may force-approve kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    entry: Optional[dict] = None
    class_complete = False
    brother_id: Optional[str] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "under_review":
            error_msg = f"Entry is not under review (status: {entry['status']})."
        else:
            entry["status"] = "force_approved"
            entry["apo_action"] = "force_approved"
            entry["apo_actor_id"] = str(interaction.user.id)
            entry["apo_acted_at"] = _now_iso()

            brother_id = str(entry["brother_id"])
            new_count = _increment_progress(
                state,
                brother_id,
                entry["class_role_id"],
                entry["terminus_type"],
            )
            class_complete = new_count >= 3 and _class_is_complete(
                state, brother_id, entry["class_role_id"]
            )
            _save_state(state)

    # All Discord API calls happen outside the lock.
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Disable buttons on the apo notification
    await interaction.response.edit_message(
        content="✅ Kill log force-approved.",
        embed=_build_apo_notification_embed(entry),
        view=None,
    )

    # Update the original kill log embed if we can find it
    await _refresh_kill_log_embed(interaction.guild, entry)

    if class_complete:
        await _notify_class_complete(
            interaction.guild, brother_id, entry["class_role_id"], entry["class_name"]
        )


async def _handle_remove_entry(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_apothecary(interaction.user):
        await interaction.response.send_message(
            "Only Watch Apothecaries may remove kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    entry: Optional[dict] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "under_review":
            error_msg = f"Entry is not under review (status: {entry['status']})."
        else:
            entry["status"] = "rejected"
            entry["apo_action"] = "rejected"
            entry["apo_actor_id"] = str(interaction.user.id)
            entry["apo_acted_at"] = _now_iso()
            _save_state(state)

    # All Discord API calls happen outside the lock.
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    await interaction.response.edit_message(
        content="❌ Kill log entry removed from record.",
        embed=_build_apo_notification_embed(entry),
        view=None,
    )

    await _refresh_kill_log_embed(interaction.guild, entry)
    await _notify_kill_log_denied(interaction.guild, entry)


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

async def _notify_apo_denial(guild: Optional[discord.Guild], entry: dict) -> None:
    if guild is None:
        return
    channel = guild.get_channel(APOTHECARY_STAFF_CHANNEL_ID)
    if channel is None:
        return
    apo_role = discord.utils.get(guild.roles, name=APOTHECARY_ROLE_NAME)
    ping = apo_role.mention if apo_role else "@Watch Apothecary"
    view = TerminusApoView(entry["kill_log_id"])
    try:
        msg = await channel.send(content=ping, embed=_build_apo_notification_embed(entry), view=view)
        async with _g.TERMINUS_SLAYER_LOCK:
            state = _load_state()
            if entry["kill_log_id"] in state["entries"]:
                state["entries"][entry["kill_log_id"]]["apo_notification_message_id"] = str(msg.id)
                _save_state(state)
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: failed to send apo denial notification: {exc}")


async def _notify_kill_log_denied(guild: Optional[discord.Guild], entry: dict) -> None:
    """Post a denial notice in the kill log channel tagging the brother."""
    if guild is None:
        return
    channel = guild.get_channel(KILL_LOG_CHANNEL_ID)
    if channel is None:
        return

    brother_mention = f"<@{entry['brother_id']}>"
    kill_log_id = entry["kill_log_id"]

    # Build a link to the original kill log embed if we have the message ID.
    embed_message_id = entry.get("embed_message_id")
    if embed_message_id:
        msg_link = f"https://discord.com/channels/{guild.id}/{KILL_LOG_CHANNEL_ID}/{embed_message_id}"
        entry_ref = f"[{kill_log_id}]({msg_link})"
    else:
        entry_ref = f"`{kill_log_id}`"

    deny_reason = entry.get("deny_reason", "").strip()
    reason_line = f"\n**Reason:** {deny_reason}" if deny_reason else ""

    try:
        await channel.send(
            f"{brother_mention} your kill log {entry_ref} has been **denied** by the Apothecarium.{reason_line}"
        )
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: failed to send kill log denial notice: {exc}")


async def _notify_class_complete(
    guild: Optional[discord.Guild],
    brother_id: str,
    class_role_id: int,
    class_name: str,
) -> None:
    """Assign the class Terminus Slayer role, enqueue a public award announcement,
    and check whether Master Terminus Slayer has now been earned."""
    if guild is None:
        return

    member = guild.get_member(int(brother_id))
    if member is None:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: member {brother_id} not found in guild for class completion")
        return

    # Assign the class role
    class_role = guild.get_role(class_role_id)
    if class_role is not None and class_role not in member.roles:
        try:
            await member.add_roles(class_role, reason=f"Auto-award: Terminus Slayer ({class_name})")
        except Exception as exc:
            if _g.logger:
                _g.logger.warning(f"terminus_ops: failed to assign {class_name} role to {brother_id}: {exc}")

    # Determine member chapter
    home_chapters = _b("HOME_CHAPTERS") or []
    member_chapter = "Unknown"
    for r in getattr(member, "roles", []):
        if getattr(r, "name", "") in home_chapters:
            member_chapter = r.name
            break

    # Enqueue public announcement for this class award
    award_type = TERMINUS_SLAYER_CLASS_AWARD_TYPES.get(class_role_id)
    if award_type:
        try:
            ann_channel = await _b("_get_award_announcement_channel")(member, guild)
            if ann_channel:
                _enqueue_fn = _b("_enqueue_award_announcement")
                if _enqueue_fn is not None:
                    _enqueue_fn(
                        str(member.id), award_type, member_chapter, str(ann_channel.id), str(guild.id)
                    )
                else:
                    if _g.logger:
                        _g.logger.warning(
                            f"terminus_ops: _enqueue_award_announcement not found; skipping {award_type}"
                        )
            else:
                if _g.logger:
                    _g.logger.warning(
                        f"terminus_ops: no announcement channel for {brother_id} {award_type}; role assigned"
                    )
        except Exception as exc:
            if _g.logger:
                _g.logger.warning(f"terminus_ops: failed to enqueue announcement for {award_type}: {exc}")

    # Check if all 7 class roles are now held → award Master Terminus Slayer
    class_role_ids = set(TERMINUS_SLAYER_CLASS_AWARD_TYPES.keys())
    member_role_ids = {r.id for r in getattr(member, "roles", [])}
    # Re-include the just-assigned role in case Discord hasn't reflected it yet
    if class_role is not None:
        member_role_ids.add(class_role_id)
    all_classes_done = class_role_ids <= member_role_ids

    if all_classes_done:
        master_role = guild.get_role(MASTER_TERMINUS_SLAYER_ROLE_ID)
        if master_role is not None and master_role not in member.roles:
            try:
                await member.add_roles(master_role, reason="Auto-award: Master Terminus Slayer")
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(f"terminus_ops: failed to assign Master Terminus Slayer to {brother_id}: {exc}")
            try:
                ann_channel = await _b("_get_award_announcement_channel")(member, guild)
                if ann_channel:
                    _b("_enqueue_award_announcement")(
                        str(member.id), "master_terminus_slayer", member_chapter, str(ann_channel.id), str(guild.id)
                    )
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(f"terminus_ops: failed to enqueue master announcement for {brother_id}: {exc}")


async def _refresh_kill_log_embed(guild: Optional[discord.Guild], entry: dict) -> None:
    """Edit the original kill log channel message to reflect the new status."""
    if guild is None:
        return
    msg_id = entry.get("embed_message_id")
    if not msg_id:
        return
    channel = guild.get_channel(KILL_LOG_CHANNEL_ID)
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=_build_kill_log_embed(entry, guild), view=None)
    except Exception as exc:
        if _g.logger:
            _g.logger.debug(f"terminus_ops: could not refresh kill log embed: {exc}")


# ---------------------------------------------------------------------------
# Startup: re-register persistent views for all non-final entries
# ---------------------------------------------------------------------------

async def register_persistent_views() -> None:
    """Call from on_ready to restore persistent buttons after restart."""
    try:
        state = _load_state()
        pending_count = 0
        review_count = 0
        for entry in state.get("entries", {}).values():
            status = entry.get("status", "pending")
            kill_log_id = entry["kill_log_id"]
            if status == "pending":
                _g.bot.add_view(TerminusKillLogView(kill_log_id))
                pending_count += 1
            elif status == "under_review":
                apo_msg_id = entry.get("apo_notification_message_id")
                if apo_msg_id:
                    _g.bot.add_view(TerminusApoView(kill_log_id))
                    review_count += 1
        if _g.logger:
            _g.logger.info(
                f"terminus_ops: registered {pending_count} pending + "
                f"{review_count} under-review persistent views"
            )
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: register_persistent_views failed: {exc}")


# ---------------------------------------------------------------------------
# 72-hour reminder task (called from bot.py scheduled task)
# ---------------------------------------------------------------------------

async def check_stale_kill_logs() -> None:
    """Post a reminder for kill log entries pending > KILL_LOG_REMINDER_HOURS hours."""
    try:
        guild = _b("_resolve_notification_guild")()
        if guild is None:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=KILL_LOG_REMINDER_HOURS)
        async with _g.TERMINUS_SLAYER_LOCK:
            state = _load_state()
            stale = [
                e for e in state["entries"].values()
                if e.get("status") == "pending"
                and not e.get("reminder_sent")
                and _parse_dt(e["submitted_at"]) < cutoff
            ]
            if not stale:
                return
            stale_ids = [e["kill_log_id"] for e in stale]

        channel = guild.get_channel(KILL_LOG_CHANNEL_ID)
        if channel is None:
            return

        # Mention @Watch Veteran role if resolvable; fall back to plain text
        vet_role = discord.utils.find(
            lambda r: r.name == "Watch Veteran", guild.roles
        )
        mention = vet_role.mention if vet_role else "Watch Veterans"
        await channel.send(
            content=f"{mention} — kill log entries require verification:",
            embed=_build_reminder_embed(stale, guild.id),
        )

        # Mark as reminder_sent only after the message was successfully sent.
        async with _g.TERMINUS_SLAYER_LOCK:
            state = _load_state()
            for eid in stale_ids:
                if eid in state["entries"]:
                    state["entries"][eid]["reminder_sent"] = True
            _save_state(state)
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: check_stale_kill_logs failed: {exc}")


# ---------------------------------------------------------------------------
# /submit_kill_log  command
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
    name="submit_kill_log",
    description="Submit a Terminus kill log entry for the Terminus Slayer challenge.",
)
@app_commands.describe(
    slayer_class="Your class for this kill (must match the challenge class).",
    terminus="Which Terminus enemy was killed.",
    aar_link="Link to the AAR for the mission where this kill occurred.",
    video_url="URL to your kill recording (YouTube, Medal, Streamable, etc.).",
    video="Direct video upload (attach a file). Use video_url for large recordings.",
)
@app_commands.rename(slayer_class="class")
@app_commands.choices(
    terminus=[
        app_commands.Choice(name=t, value=t) for t in TERMINUS_TYPES
    ]
)
async def submit_kill_log(
    interaction: discord.Interaction,
    slayer_class: discord.Role,
    terminus: app_commands.Choice[str],
    aar_link: str,
    video_url: Optional[str] = None,
    video: Optional[discord.Attachment] = None,
):
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            f"This command can only be used in <#{KILL_LOG_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    # Member check — must hold at least Watch Brother (or any equivalent rank)
    member = interaction.user
    if not any(r.name in _MEMBER_RANKS for r in getattr(member, "roles", [])):
        await interaction.response.send_message(
            "Only Watch Brothers and above may submit kill logs.",
            ephemeral=True,
        )
        return

    # Validate class role
    if slayer_class.id not in KILL_LOG_CLASS_ROLES:
        await interaction.response.send_message(
            "Invalid class role. Please select one of the 7 Terminus Slayer class roles.",
            ephemeral=True,
        )
        return

    # Require at least one video source
    if not video_url and not video:
        await interaction.response.send_message(
            "A recording is required. Attach a video file or provide a `video_url`.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    channel = guild.get_channel(KILL_LOG_CHANNEL_ID) if guild else None
    if channel is None:
        await interaction.response.send_message(
            "Kill log channel not found. Contact the Forgemaster.",
            ephemeral=True,
        )
        return

    # Defer now — AAR validation and video download can exceed the 3-second response window
    await interaction.response.defer(ephemeral=True)

    # Validate AAR link — must be a real Absolute AAR the submitter participated in
    aar_error = await _validate_aar_link(aar_link, guild, str(interaction.user.id))
    if aar_error:
        await interaction.followup.send(aar_error, ephemeral=True)
        return

    class_name = KILL_LOG_CLASS_ROLES[slayer_class.id]
    brother_id = str(interaction.user.id)

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        kill_log_id = _next_kill_log_id(state)

        verified_prior = _get_verified_count(
            state, brother_id, slayer_class.id, terminus.value
        )
        kill_number = min(verified_prior + 1, 3)

        entry: dict = {
            "kill_log_id": kill_log_id,
            "brother_id": brother_id,
            "class_role_id": slayer_class.id,
            "class_name": class_name,
            "terminus_type": terminus.value,
            "kill_number": kill_number,
            "aar_link": aar_link,
            "video_url": video_url or "",
            "video_attachment_url": video.url if video else "",
            "embed_message_id": "",
            "channel_id": KILL_LOG_CHANNEL_ID,
            "submitted_at": _now_iso(),
            "status": "pending",
            "verifications": [],
            "denied_by": None,
            "denied_at": None,
            "apo_action": None,
            "apo_actor_id": None,
            "apo_acted_at": None,
            "apo_notification_message_id": None,
            "reminder_sent": False,
            "verified_at": None,
            "verified_prior_count": verified_prior,
        }
        state["entries"][kill_log_id] = entry
        _save_state(state)

    # Post embed to kill log channel
    view = TerminusKillLogView(kill_log_id)
    _g.bot.add_view(view)

    embed = _build_kill_log_embed(entry, guild)
    vet_role = discord.utils.find(lambda r: r.name == "Watch Veteran", guild.roles) if guild else None
    vet_mention = vet_role.mention if vet_role else "Watch Veterans"

    # Re-upload attached video as a file so Discord renders it inline rather than as a download link
    video_file = None
    if video is not None:
        try:
            video_file = await video.to_file()
        except Exception as exc:
            # Roll back the saved entry before aborting
            async with _g.TERMINUS_SLAYER_LOCK:
                state = _load_state()
                state["entries"].pop(kill_log_id, None)
                _save_state(state)
            await interaction.followup.send(
                f"❌ Could not process your video attachment: `{exc}`\n"
                "Use `video_url` with a YouTube, Medal, or Streamable link for recordings over 8 MB.",
                ephemeral=True,
            )
            return

    video_too_large = False
    try:
        msg = await channel.send(
            content=f"{vet_mention} — new kill log submitted for verification:",
            embed=embed,
            view=view,
            file=video_file,
        )
    except discord.HTTPException as exc:
        if video_file is not None:
            # File exceeds server upload limit — retry without it and warn the user
            video_file = None
            video_too_large = True
            msg = await channel.send(
                content=f"{vet_mention} — new kill log submitted for verification:",
                embed=embed,
                view=view,
            )
        else:
            # Unrelated channel send failure — roll back and abort
            async with _g.TERMINUS_SLAYER_LOCK:
                state = _load_state()
                state["entries"].pop(kill_log_id, None)
                _save_state(state)
            await interaction.followup.send(
                f"❌ Failed to post to the kill log channel: `{exc}`",
                ephemeral=True,
            )
            return

    # Store the message ID and (if re-uploaded) the channel attachment URL
    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        if kill_log_id in state["entries"]:
            state["entries"][kill_log_id]["embed_message_id"] = str(msg.id)
            if video_file and msg.attachments:
                state["entries"][kill_log_id]["video_attachment_url"] = msg.attachments[0].url
            _save_state(state)

    await interaction.followup.send(
        f"✅ Kill log **{kill_log_id}** submitted. Watch Veterans will verify it shortly."
        + (
            "\n\n⚠️ Your video attachment was too large to upload directly — it was not included. "
            "Re-submit using `video_url` with a YouTube, Medal, or Streamable link."
            if video_too_large else ""
        ),
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /verifier_standing  command
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
    name="verifier_standing",
    description="[Vet+] View the rolling 7-day verifier activity leaderboard.",
)
async def verifier_standing(interaction: discord.Interaction):
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            f"This command can only be used in <#{KILL_LOG_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    if not _is_verifier(interaction.user):
        await interaction.response.send_message(
            "This command is restricted to Watch Veterans and above.",
            ephemeral=True,
        )
        return

    state = _load_state()
    va = state.get("verifier_actions", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    rows = []
    for vet_id, actions in va.items():
        count = sum(1 for a in actions if _parse_dt(a["timestamp"]) >= cutoff)
        if count > 0:
            tier = 0
            for threshold, t, _bonus in VERIFIER_TIER_THRESHOLDS:
                if count >= threshold:
                    tier = t
                    break
            rows.append((vet_id, count, tier))

    rows.sort(key=lambda x: x[1], reverse=True)

    embed = discord.Embed(
        title="⚔️ Verifier Standing — Rolling 7 Days",
        colour=discord.Colour.from_rgb(120, 80, 160),
    )

    if not rows:
        embed.description = "No verification activity in the past 7 days."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (vet_id, count, tier) in enumerate(rows):
            medal = medals[i] if i < 3 else "  "
            tier_str = f"[Tier {tier}  +{tier}]" if tier > 0 else "[—]"
            bar = "█" * min(count, 15)
            lines.append(f"{medal} <@{vet_id}> `{bar}` {count} actions  {tier_str}")
        embed.description = "\n".join(lines)

    embed.set_footer(text="Verify or deny kill logs to build your standing. Resets on a rolling 7-day window.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /challenge-progress command
# ---------------------------------------------------------------------------

class _ChallengePagesView(discord.ui.View):
    """Paginator for the two-page challenge-progress embed (ephemeral)."""

    def __init__(self, embeds: list[discord.Embed]):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.page = 0
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= len(self.embeds) - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page > 0:
            self.page -= 1
            self._refresh_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.page], view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page < len(self.embeds) - 1:
            self.page += 1
            self._refresh_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.page], view=self)
        else:
            await interaction.response.defer()


@_g.bot.tree.command(
    name="challenge-progress",
    description="View your challenge progress — mission awards and Terminus Slayer kills.",
)
@app_commands.describe(
    member="[Watch Command+] View another member's challenge progress.",
    verbose="Show missing missions for incomplete challenges.",
)
async def challenge_progress(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
    verbose: bool = False,
):
    # Only Watch Command+ may query other members
    if member is not None:
        if not any(r.name in WATCH_COMMAND_ROLES for r in interaction.user.roles):
            await interaction.response.send_message(
                "Only Watch Command and above may view another member's challenge progress.",
                ephemeral=True,
            )
            return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await _challenge_progress_inner(interaction, member, verbose=verbose)
    except Exception:
        _g.logger.exception("challenge-progress: unhandled error")
        try:
            await interaction.followup.send("An error occurred building your challenge progress. Contact the Forgemaster.", ephemeral=True)
        except Exception:
            pass


async def _challenge_progress_inner(
    interaction: discord.Interaction,
    member: Optional[discord.Member],
    verbose: bool = False,
) -> None:

    target = member or interaction.user
    user_id_str = str(target.id)

    # Load challenge progress
    challenge_all: dict = {}
    if os.path.exists(CHALLENGE_PROGRESS_PATH):
        with open(CHALLENGE_PROGRESS_PATH, "r", encoding="utf-8") as f:
            challenge_all = json.load(f)
    user_progress = challenge_all.get(user_id_str, {})

    # Load Terminus Slayer state
    ts_state = _load_state()
    ts_progress = ts_state.get("progress", {}).get(user_id_str, {})

    # --- Helpers ---
    def _normalize_mission_name(raw: str) -> str:
        """Normalize mission strings by stripping mentions/extra tokens and lowercasing."""
        text = re.sub(r"<@&\d+>", "", raw or "").lower().strip()
        if "@" in text:
            text = text.split("@", 1)[0].strip()
        return text

    def _unique_missions(key: str) -> set:
        # Base from persisted challenge_progress.json
        completed = {
            _normalize_mission_name(str(m.get("mission", "")))
            for m in user_progress.get(key, [])
            if isinstance(m, dict)
        }

        # Live union from datastore for Black Laurels / Order Omega to avoid stale undercounts.
        if _g.DATASTORE and key in {"black_laurels", "order_omega"}:
            required_set = BLACK_LAURELS_REQUIRED_MISSIONS if key == "black_laurels" else ORDER_OMEGA_REQUIRED_MISSIONS
            for rec in _g.DATASTORE.iter_records():
                brothers = {str(b) for b in (rec.get("brother_ids") or [])}
                if user_id_str not in brothers:
                    continue

                if not (rec.get("black_laurels_in_mission") or rec.get("black_laurels_in_difficulty")):
                    continue

                if key == "order_omega" and (rec.get("difficulty_class") or "") != "omega_ops":
                    continue

                mission = _normalize_mission_name(rec.get("mission") or rec.get("mission_name") or "")
                if mission in required_set:
                    completed.add(mission)

        return completed

    def _unique_mission_count(key: str) -> int:
        return len(_unique_missions(key))

    # Collect target's role IDs for completed-role detection.
    target_role_ids: set[int] = {r.id for r in getattr(target, "roles", [])}
    challenge_emoji_by_role_id = {rid: hint for rid, _name, hint in CHALLENGE_ROLES}

    def _award_prefix(role_id: Optional[int]) -> str:
        """Return challenge emoji prefix for a role, regardless of completion state."""
        if role_id is None:
            return ""
        hint = challenge_emoji_by_role_id.get(role_id)
        if not hint:
            return ""
        if hint.startswith("unicode:"):
            return f"{hint[8:]} "
        get_emoji = _b("_get_emoji_by_name")
        if get_emoji:
            try:
                emoji = get_emoji(interaction.guild, hint)
                if emoji:
                    return f"{emoji} "
            except Exception:
                pass
        return ""

    def _bar(current: int, total: int, role_id: Optional[int] = None) -> str:
        # If the member already holds the award role, treat as fully complete.
        if role_id is not None and role_id in target_role_ids:
            current = total
        filled = min(current, total)
        empty = max(total - filled, 0)
        check = "✅" if current >= total else "🔲"
        blocks = "█" * filled + "░" * empty
        return f"{check} `{blocks}` {current}/{total}"

    def _missing_line(completed: set, required: set, role_id: Optional[int] = None) -> str:
        """Return a short 'Missing: x, y' line if verbose and missions are missing."""
        if not verbose:
            return ""
        if role_id is not None and role_id in target_role_ids:
            return ""
        missing = sorted(required - completed)
        if not missing:
            return ""
        return "\n_Missing: " + ", ".join(missing) + "_"

    # --- Section 1: Mission Challenges ---
    # Tuples: (label, progress_key, required_set, role_id)
    challenge_rows = [
        ("Kadaku Campaign Medal",              "kadaku_campaign",       KADAKU_CAMPAIGN_REQUIRED_MISSIONS,              KADAKU_CAMPAIGN_MEDAL_ROLE_ID),
        ("Black Reef Campaign Medal",          "black_reef",            BLACK_REEF_REQUIRED_MISSIONS,                   BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID),
        ("Distinguished Black Reef",           "distinguished_black_reef", BLACK_REEF_REQUIRED_MISSIONS,               DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID),
        ("Black Laurels",                      "black_laurels",         BLACK_LAURELS_REQUIRED_MISSIONS,               BLACK_LAURELS_ROLE_ID),
        ("Distinguished SOK-G: Pipehitter",    "sok_g_pipehitter",      None,                                          DISTINGUISHED_PIPEHITTER_ROLE_ID),
        ("SOK-G: Pipehitter",                  "sok_g_pipehitter",      None,                                          PIPEHITTER_ROLE_ID),
        ("Order Omega",                        "order_omega",           ORDER_OMEGA_REQUIRED_MISSIONS,                 THE_ORDER_OMEGA_ROLE_ID),
    ]
    # Fixed totals for non-mission-set rows
    _fixed_totals = {
        "Distinguished SOK-G: Pipehitter": 2,
        "SOK-G: Pipehitter": 1,
    }

    challenge_lines = []
    for label, key, required, role_id in challenge_rows:
        label_with_emoji = f"{_award_prefix(role_id)}{label}"
        if required is not None:
            total = len(required)
            completed = _unique_missions(key)
            current = len(completed)
            line = f"**{label_with_emoji}**\n{_bar(current, total, role_id)}{_missing_line(completed, required, role_id)}"
        else:
            total = _fixed_totals[label]
            current = _unique_mission_count(key)
            # If a member has Pipehitter, show at least 1/2 toward Distinguished Pipehitter.
            if label == "Distinguished SOK-G: Pipehitter" and PIPEHITTER_ROLE_ID in target_role_ids and current < 1:
                current = 1
            line = f"**{label_with_emoji}**\n{_bar(current, total, role_id)}"
        challenge_lines.append(line)

    # Defense of Herisor is tallied automatically from qualifying tagged AARs.
    # Backward-compat: also respect legacy command submissions if present.
    def _as_list(value) -> list:
        return value if isinstance(value, list) else []

    _herisor_siege = _as_list(user_progress.get("herisor_defense_siege"))
    _herisor_term = _as_list(user_progress.get("herisor_defense_termination"))
    _herisor_rec = _as_list(user_progress.get("herisor_defense_reclamation"))

    _auto_strat = _herisor_term + _herisor_rec
    # Base: siege done OR (both term AND rec done)
    _auto_base = bool(_herisor_siege) or bool(_herisor_term and _herisor_rec)
    _auto_siege_bl = any(bool(x.get("black_laurels")) for x in _herisor_siege if isinstance(x, dict))
    _auto_term_bl = any(bool(x.get("black_laurels")) for x in _herisor_term if isinstance(x, dict))
    _auto_rec_bl = any(bool(x.get("black_laurels")) for x in _herisor_rec if isinstance(x, dict))
    _auto_strat_bl = bool(_herisor_term and _herisor_rec and _auto_term_bl and _auto_rec_bl)
    # Distinguished: siege with BL OR (both term AND rec with BL)
    _auto_distinguished = _auto_siege_bl or _auto_strat_bl
    # Valor: siege with BL AND (both term AND rec with BL)
    _auto_valor = _auto_siege_bl and _auto_strat_bl

    _legacy_subs = _as_list(user_progress.get("defense_of_herisor_submissions"))
    _legacy_base = len(_legacy_subs) > 0
    _legacy_distinguished = any(bool(s.get("distinguished")) for s in _legacy_subs if isinstance(s, dict))
    _legacy_valor = any(bool(s.get("distinguished_with_valor")) for s in _legacy_subs if isinstance(s, dict))

    herisor_base = _auto_base or _legacy_base
    herisor_distinguished = _auto_distinguished or _legacy_distinguished
    herisor_valor = _auto_valor or _legacy_valor

    siege_done = bool(_herisor_siege)
    term_done = bool(_herisor_term)
    rec_done = bool(_herisor_rec)
    challenge_lines.append(
        f"<:HerisorDefense:1511109884521742416> **Defense of Herisor** — siege or both ops\n"
        f"{_bar(1 if herisor_base else 0, 1, HERISOR_DEFENSE_MEDAL_ROLE_ID)} "
        f"{'✅' if siege_done else '🔲'} Siege  "
        f"{'✅' if term_done else '🔲'} Term  "
        f"{'✅' if rec_done else '🔲'} Rec"
    )
    challenge_lines.append(
        f"<:DistinguishedHerisorDefense:1511109951106584778> **Distinguished Defense of Herisor** — no downs (BL)\n"
        f"{_bar(1 if herisor_distinguished else 0, 1, DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID)} "
        f"{'✅' if _auto_siege_bl else '🔲'} Siege+BL  "
        f"{'✅' if _auto_strat_bl else '🔲'} Term+Rec+BL"
    )
    challenge_lines.append(
        f"<:HerisorDefensewithValor:1511110040566763630> **Distinguished Defense of Herisor with Valor** — both teams, no downs\n"
        f"{_bar(1 if herisor_valor else 0, 1, DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID)} "
        f"{'✅' if _auto_siege_bl else '🔲'} Siege+BL  "
        f"{'✅' if _auto_strat_bl else '🔲'} Term+Rec+BL"
    )

    # --- Crux Terminatus eligibility checklist ---
    # All requirements are evaluated live against current roles and AAR records.
    # Holding the Crux role does NOT short-circuit — roles can be revoked if new
    # missions are added and requirements are no longer met.

    # Requirement 1: Black Laurels role held AND every post-enforcement BL AAR is Rank A
    # over the user's effective mission baseline.
    has_bl_role = BLACK_LAURELS_ROLE_ID in target_role_ids
    all_bl_rank_a = False
    non_a_missions: list[str] = []  # verbose: missions with non-A rank post-enforcement
    _grandfathered_bl = False
    if has_bl_role and _g.DATASTORE:
        audit = evaluate_crux_bl_rank_a(user_id_str, _g.DATASTORE.iter_records())
        all_bl_rank_a = bool(audit["all_rank_a"])
        _grandfathered_bl = bool(audit["grandfathered"])
        if verbose:
            non_a_missions = list(audit["non_a_missions"])

    # Requirement 2: Distinguished SOK-G Pipehitter role held.
    has_distinguished = DISTINGUISHED_PIPEHITTER_ROLE_ID in target_role_ids

    # Requirement 3: 2+ Terminus Slayer roles held.
    ts_role_count = sum(1 for rid in TERMINUS_SLAYER_ROLE_IDS if rid in target_role_ids)
    ts_slays_met = ts_role_count >= 2

    bl_check = "✅" if all_bl_rank_a else "🔲"
    dist_check = "✅" if has_distinguished else "🔲"
    ts_check = "✅" if ts_slays_met else "🔲"
    bl_rank_detail = ""
    if verbose and non_a_missions:
        bl_rank_detail = "\n  _Non-Rank A (post-enforcement): " + ", ".join(sorted(non_a_missions)) + "_"
    bl_scope_detail = ""
    if verbose and _grandfathered_bl:
        bl_scope_detail = "\n  _Grandfathered baseline in effect for Crux BL audit._"
    challenge_lines.append(
        f"**Crux Terminatus**\n"
        f"{bl_check} Black Laurels — baseline missions, Rank A{bl_rank_detail}{bl_scope_detail}\n"
        f"{dist_check} Distinguished SOK-G: Pipehitter\n"
        f"{ts_check} Terminus Slayer roles held: {ts_role_count}/2"
    )

    # --- Dual Vigil eligibility checklist ---
    # Dual Vigil requires all BL missions completed at Absolute with exactly 2 brothers.
    # The role can be lost if a new mission is added and not completed in time.
    # Always evaluate live progress — holding the role does NOT short-circuit,
    # because the required mission set may have grown since the role was awarded.
    dv_unique_missions = {m["mission"] for m in user_progress.get("dual_vigil", [])}
    dv_met = dv_unique_missions >= DUAL_VIGIL_REQUIRED_MISSIONS
    dv_count = len(dv_unique_missions)
    dv_total = len(DUAL_VIGIL_REQUIRED_MISSIONS)
    dv_check = "✅" if dv_met else "🔲"
    dv_blocks = "█" * min(dv_count, dv_total) + "░" * max(dv_total - dv_count, 0)
    dv_missing_line = _missing_line(dv_unique_missions, DUAL_VIGIL_REQUIRED_MISSIONS)
    challenge_lines.append(
        f"**Dual Vigil**\n{dv_check} `{dv_blocks}` {dv_count}/{dv_total}{dv_missing_line}"
    )

    def _add_chunked_fields(embed: discord.Embed, name: str, items: list[str], sep: str = "\n\n") -> None:
        """Add items joined by sep across as many fields as needed, each ≤ 1024 chars."""
        current = ""
        first = True
        for item in items:
            prefix = "" if current == "" else sep
            candidate = current + prefix + item
            if len(candidate) > 1024:
                embed.add_field(name=name if first else "\u200b", value=current, inline=False)
                first = False
                current = item
            else:
                current = candidate
        if current:
            embed.add_field(name=name if first else "\u200b", value=current, inline=False)

    base_colour = discord.Colour.from_rgb(80, 140, 200)
    embed1 = discord.Embed(
        title=f"Challenge Progress — {target.display_name}",
        description="Page 1 of 2 — Mission Awards",
        colour=base_colour,
    )
    _add_chunked_fields(embed1, "⚔️ Mission Awards", challenge_lines)

    # --- Section 2: Terminus Slayer Kill Grid ---
    ts_lines = []
    for class_role_id, class_name in KILL_LOG_CLASS_ROLES.items():
        class_prog = ts_progress.get(str(class_role_id), {})
        has_class_role = class_role_id in target_role_ids
        counts = {}
        for t_type in TERMINUS_TYPES:
            count = class_prog.get(t_type, 0)
            if has_class_role:
                count = 3
            counts[t_type] = count
        # If all types complete, collapse to a single checkmark line to save space
        if all(c >= 3 for c in counts.values()):
            ts_lines.append(f"**{class_name}** ✅")
        else:
            type_parts = []
            for t_type, count in counts.items():
                check = "✅" if count >= 3 else "🔲"
                type_parts.append(f"{check} {t_type}: {count}/3")
            ts_lines.append(f"**{class_name}**\n" + "  |  ".join(type_parts))

    embed2 = discord.Embed(
        title=f"Challenge Progress — {target.display_name}",
        description="Page 2 of 2 — Terminus Slayer Kills",
        colour=base_colour,
    )
    _add_chunked_fields(embed2, "💀 Terminus Slayer Kills", ts_lines, sep="\n")

    footer_text = "Progress updates automatically as AARs and kill logs are processed. Use verbose=True to see missing missions."
    embed1.set_footer(text=footer_text)
    embed2.set_footer(text=footer_text)

    view = _ChallengePagesView([embed1, embed2])
    await interaction.followup.send(embed=embed1, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# /revoke_slay command
# ---------------------------------------------------------------------------

async def _apo_revoke_kill_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete: list verified/force_approved entries, filtered by current input."""
    try:
        state = _load_state()
        choices = []
        current_lower = current.lower()
        for entry in state.get("entries", {}).values():
            if entry.get("status") not in ("verified", "force_approved"):
                continue
            kid = entry["kill_log_id"]
            label = (
                f"{kid} — {entry['class_name']} / {entry['terminus_type']} "
                f"(Brother: {entry['brother_id']})"
            )
            if current_lower and current_lower not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label[:100], value=kid))
            if len(choices) >= 25:
                break
        return choices
    except Exception:
        return []


async def _handle_apo_revoke_kill(
    interaction: discord.Interaction,
    kill_log_id: str,
    reason: str,
) -> None:
    """Core logic for /revoke_slay — runs outside the view layer so it can
    be tested directly and reused if a button surface is ever added."""

    check_perm = _b("check_command_permission")
    if check_perm is None or not check_perm(interaction.user, "revoke_slay"):
        await interaction.response.send_message(
            "Only Watch Apothecaries or Chief Apothecaries may revoke verified or force-approved kill log entries.",
            ephemeral=True,
        )
        return

    reason_clean = reason.strip()
    if not reason_clean:
        await interaction.response.send_message(
            "Reason is required and cannot be blank.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    actor_id = str(interaction.user.id)
    guild = interaction.guild

    # --- State mutation (inside lock) ---
    error_msg: Optional[str] = None
    blocked_msg: Optional[str] = None
    entry: Optional[dict] = None
    award_stripped: bool = False
    role_stripped: bool = False
    breaks_completion: bool = False

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)

        if entry is None:
            error_msg = f"Kill log entry `{kill_log_id}` not found."
        elif entry["status"] not in ("verified", "force_approved"):
            error_msg = (
                f"Entry `{kill_log_id}` is not in a revocable state "
                f"(current status: `{entry['status']}`)."
            )
        else:
            brother_id = str(entry["brother_id"])
            class_role_id = int(entry["class_role_id"])
            terminus_type = entry["terminus_type"]

            breaks_completion = _would_break_class_completion(
                state, brother_id, class_role_id, terminus_type
            )

            if breaks_completion:
                # Check if the award is still in the queue (not yet delivered).
                award_in_queue = _dequeue_terminus_class_award(brother_id, class_role_id)
                if award_in_queue:
                    award_stripped = True
                    # Award removed from queue — safe to proceed; role may have
                    # been assigned already (role grant happens before enqueue).
                    # We attempt role removal below, outside the lock.
                else:
                    # Queue had nothing — check if the member already holds the role.
                    member = guild.get_member(int(brother_id)) if guild else None
                    class_role = guild.get_role(class_role_id) if guild else None
                    if member is not None and class_role is not None and class_role in member.roles:
                        # Award was delivered. Block revocation.
                        blocked_msg = (
                            f"❌ Cannot revoke `{kill_log_id}`: the Terminus Slayer class award for "
                            f"**{entry['class_name']}** has already been delivered to <@{brother_id}>. "
                            "Manual escalation to High Command is required to strip the role."
                        )
                    # else: queue is empty AND role not held — edge case (e.g. prior
                    # revoke already cleared it). Safe to proceed without award action.

            if blocked_msg is None and error_msg is None:
                # Commit the revocation.
                _decrement_progress(state, brother_id, class_role_id, terminus_type)
                entry["status"] = "apo_revoked"
                entry["apo_revoke_actor_id"] = actor_id
                entry["apo_revoke_at"] = _now_iso()
                entry["apo_revoke_reason"] = reason_clean
                _save_state(state)

    # --- Bail paths (no state was mutated) ---
    if error_msg:
        await interaction.followup.send(error_msg, ephemeral=True)
        return

    if blocked_msg:
        await interaction.followup.send(blocked_msg, ephemeral=True)
        # Also alert the apo-staff channel so the situation is visible.
        if guild:
            apo_ch = guild.get_channel(APOTHECARY_STAFF_CHANNEL_ID)
            if apo_ch:
                try:
                    await apo_ch.send(
                        f"⚠️ **Kill revoke blocked** — <@{actor_id}> attempted to revoke "
                        f"`{kill_log_id}` for <@{entry['brother_id']}> "
                        f"(**{entry['class_name']} / {entry['terminus_type']}**) "
                        "but the class award has already been delivered. "
                        "High Command intervention required."
                    )
                except Exception as exc:
                    if _g.logger:
                        _g.logger.warning(f"terminus_ops: failed to send blocked-revoke notice: {exc}")
        return

    # --- Post-commit side-effects ---
    # Strip the class role when the award was still in the queue (grant had
    # already executed before enqueue, so the member likely holds the role).
    if breaks_completion and guild:
        brother_id_str = str(entry["brother_id"])
        member = guild.get_member(int(brother_id_str))
        class_role = guild.get_role(int(entry["class_role_id"]))
        if member is not None and class_role is not None and class_role in member.roles:
            try:
                await member.remove_roles(
                    class_role,
                    reason=f"Terminus kill revoked by Apothecary {actor_id}: {kill_log_id}",
                )
                role_stripped = True
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(
                        f"terminus_ops: failed to strip class role for {brother_id_str} "
                        f"after revoke of {kill_log_id}: {exc}"
                    )

    # Refresh the original kill log embed (no buttons — revoked is a terminal state).
    await _refresh_kill_log_embed(guild, entry)

    # Notify the brother in the kill log channel.
    if guild:
        kl_channel = guild.get_channel(KILL_LOG_CHANNEL_ID)
        if kl_channel:
            embed_message_id = entry.get("embed_message_id")
            if embed_message_id:
                msg_link = (
                    f"https://discord.com/channels/{guild.id}"
                    f"/{KILL_LOG_CHANNEL_ID}/{embed_message_id}"
                )
                entry_ref = f"[{kill_log_id}]({msg_link})"
            else:
                entry_ref = f"`{kill_log_id}`"
            reason_line = f"\n**Reason:** {reason.strip()}" if reason.strip() else ""
            try:
                await kl_channel.send(
                    f"<@{entry['brother_id']}> your kill log {entry_ref} has been "
                    f"**retroactively revoked** by the Apothecarium.{reason_line}"
                )
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(f"terminus_ops: failed to send revoke notice to kill log channel: {exc}")

    # Post confirmation to the apo-staff channel.
    if guild:
        apo_ch = guild.get_channel(APOTHECARY_STAFF_CHANNEL_ID)
        if apo_ch:
            embed = discord.Embed(
                title="🩸 Kill Log — Apothecary Revocation",
                colour=discord.Colour.dark_red(),
            )
            embed.add_field(name="Kill Log ID", value=kill_log_id, inline=True)
            embed.add_field(name="Brother", value=f"<@{entry['brother_id']}>", inline=True)
            embed.add_field(
                name="Class / Terminus",
                value=f"{entry['class_name']} / {entry['terminus_type']}",
                inline=True,
            )
            embed.add_field(name="Revoked By", value=f"<@{actor_id}>", inline=True)
            embed.add_field(
                name="Award Queue Entry Removed",
                value="Yes" if award_stripped else "N/A",
                inline=True,
            )
            embed.add_field(
                name="Class Role Stripped",
                value="Yes" if role_stripped else "No",
                inline=True,
            )
            if reason_clean:
                embed.add_field(name="Reason", value=reason_clean, inline=False)
            embed.timestamp = datetime.now(timezone.utc)
            try:
                await apo_ch.send(embed=embed)
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(f"terminus_ops: failed to send revoke confirmation to apo channel: {exc}")

    await interaction.followup.send(
        f"✅ Kill log `{kill_log_id}` has been retroactively revoked."
        + (" The queued class award announcement was removed." if award_stripped else "")
        + (" The class role has been stripped." if role_stripped else ""),
        ephemeral=True,
    )


@_g.bot.tree.command(
    name="revoke_slay",
    description="[Apothecary] Retroactively revoke a verified kill log entry.",
)
@app_commands.describe(
    kill_log_id="Kill log entry to revoke (type to search by ID, class, or terminus).",
    reason="Reason for revocation (required — recorded in the audit trail).",
)
@app_commands.autocomplete(kill_log_id=_apo_revoke_kill_autocomplete)
async def revoke_slay(
    interaction: discord.Interaction,
    kill_log_id: str,
    reason: str,
):
    await _handle_apo_revoke_kill(interaction, kill_log_id, reason)
