"""Certification, induction, and first company/Kill-Team assignment workflow."""

import asyncio
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands

from . import _bot_globals as _g
from .constants import (
    ACTIVITY_STATUS_CHANNEL_ID,
    DATA_DIR,
    VETERAN_PROMOTION_CHANNEL_ID,
    WATCH_BROTHER_ROLE_ID,
    WATCH_CAPTAIN_ROLE_ID,
    WATCH_LIEUTENANT_ROLE_ID,
)
from .flavor_text.chapters import KILL_TEAM_WELCOME_CHAPTER_LINES


INDUCTION_PATH = os.path.join(DATA_DIR, "induction_certifications.json")
_INDUCTION_LOCK = asyncio.Lock()
_STUD_PIPS = re.compile(r"[●⚬]+$")
_OPEN_STATUSES = {
    "certification_publish_pending",
    "certification_publishing",
    "pending",
    "induction_processing",
    "assignment_publish_pending",
    "assignment_publishing",
    "assignment_pending",
    "assignment_processing",
    "welcome_pending",
    "welcome_sending",
    "welcome_uncertain",
    "review",
}


def _bot_symbol(name):
    module = sys.modules.get("opscribe.bot") or sys.modules.get("bot")
    return getattr(module, name, None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_state():
    if not os.path.exists(INDUCTION_PATH):
        return {"entries": {}}
    with open(INDUCTION_PATH, encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict) or not isinstance(state.get("entries"), dict):
        raise ValueError("Invalid induction workflow state; contact command staff.")
    for request_id, entry in state["entries"].items():
        if not isinstance(entry, dict) or entry.get("request_id") != request_id:
            raise ValueError("Invalid induction workflow record; contact command staff.")
        if entry.get("status") not in _OPEN_STATUSES | {"assigned", "review", "cancelled"}:
            raise ValueError("Invalid induction workflow status; contact command staff.")
    return state


def _save_state(state):
    directory = os.path.dirname(INDUCTION_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = INDUCTION_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    if os.path.exists(INDUCTION_PATH):
        shutil.copyfile(INDUCTION_PATH, INDUCTION_PATH + ".bak")
    os.replace(temporary, INDUCTION_PATH)


def _persist_entry(entry):
    state = _load_state()
    state["entries"][entry["request_id"]] = entry
    _save_state(state)


def _canonical_chapter(value):
    requested = (value or "").strip().casefold()
    chapters = _bot_symbol("HOME_CHAPTERS") or []
    return next((chapter for chapter in chapters if chapter.casefold() == requested), None)


def _nickname_with_stud_suffix(chosen_name, current_nickname):
    name = " ".join(str(chosen_name or "").split())
    if not name:
        raise ValueError("Chosen Name cannot be blank.")
    suffix_match = _STUD_PIPS.search(str(current_nickname or "").rstrip())
    suffix = f" {suffix_match.group(0)}" if suffix_match else ""
    target = f"{name}{suffix}"
    if len(target) > 32:
        raise ValueError("Chosen Name and existing service-stud marks must fit Discord's 32-character nickname limit.")
    return target


def _resolve_chapter_role(guild, chapter_name):
    chapter = _canonical_chapter(chapter_name)
    if chapter is None:
        raise ValueError("Choose a canonical home chapter.")
    role = discord.utils.get(getattr(guild, "roles", []) or [], name=chapter)
    if role is None:
        raise ValueError(f"The `{chapter}` role does not exist in this server.")
    return chapter, role


async def _chapter_autocomplete(interaction, current):
    guild = interaction.guild
    if guild is None:
        return []
    chapters = _bot_symbol("HOME_CHAPTERS") or []
    role_names = {role.name.casefold() for role in getattr(guild, "roles", []) or []}
    return [
        app_commands.Choice(name=chapter, value=chapter)
        for chapter in chapters
        if chapter.casefold() in role_names and current.casefold() in chapter.casefold()
    ][:25]


def _certification_embed(entry, guild):
    inductee = guild.get_member(entry["member_id"])
    display_name = inductee.display_name if inductee else f"<@{entry['member_id']}>"
    embed = discord.Embed(title="///INDUCTION COMPLETE", colour=discord.Colour.gold())
    embed.add_field(name="Inductee", value=display_name, inline=False)
    embed.add_field(name="Certified By", value=f"<@{entry['certifier_id']}>", inline=True)
    embed.add_field(name="Chosen Name", value=discord.utils.escape_mentions(entry["chosen_name"]), inline=True)
    embed.add_field(name="Chosen Chapter", value=entry["chapter_name"], inline=True)
    embed.set_footer(text=f"Induction {entry['request_id']}")
    return embed


def _assignment_embed(entry):
    embed = discord.Embed(title="///WATCH BROTHER READY FOR ASSIGNMENT", colour=discord.Colour.gold())
    embed.add_field(name="Name", value=discord.utils.escape_mentions(entry["chosen_name"]), inline=False)
    embed.add_field(name="Induction Approved By", value=f"<@{entry['induction_approver_id']}>", inline=True)
    if entry.get("assigner_id"):
        embed.add_field(name="Assigned By", value=f"<@{entry['assigner_id']}>", inline=True)
    embed.add_field(name="Company", value=_selected_role_mention(entry.get("company_id")), inline=True)
    embed.add_field(name="Kill-Team", value=_selected_role_mention(entry.get("kt_id")), inline=True)
    if entry.get("status") == "review":
        embed.add_field(name="Status", value="Requires staff review.", inline=False)
    elif entry.get("status") == "cancelled":
        embed.add_field(name="Status", value=f"Closed by <@{entry.get('closed_by', 0)}>.", inline=False)
    if entry.get("error"):
        embed.add_field(name="Notice", value=discord.utils.escape_mentions(entry["error"])[:1024], inline=False)
    embed.set_footer(text=f"Induction {entry['request_id']}")
    return embed


def _selected_role_mention(role_id):
    return f"<@&{role_id}>" if role_id else "Not selected"


def _welcome_embed(entry, kt_role):
    team_name = re.sub(r"^kill[ -]?team\s+", "", kt_role.name, flags=re.IGNORECASE).strip()
    team_name = discord.utils.escape_markdown(discord.utils.escape_mentions(team_name))
    chosen_name = discord.utils.escape_markdown(discord.utils.escape_mentions(entry["chosen_name"]))
    chapter_name = entry.get("chapter_name") or "Unknown"
    chapter_line = KILL_TEAM_WELCOME_CHAPTER_LINES.get(
        chapter_name,
        "Whatever road brought you to Jericho, your oath has earned you a place here. "
        "The brothers beside you will help you take the next step.",
    )
    embed = discord.Embed(
        title=f"`ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴋɪʟʟ-ᴛᴇᴀᴍ {team_name.upper()}`",
        description=(
            f"**Welcome, {chosen_name}.**\n\n"
            "The oath is sworn, the silver arm bears witness, and a place in the line is yours. "
            "Within Watch Fortress Jericho, warriors of many Chapters keep a single vigil. "
            "Today, that brotherhood welcomes you.\n\n"
            "You are not expected to know every rite or face every trial alone. "
            "Ask questions freely of your Kill-Team brothers and Watch Sergeant. "
            "Their experience is yours to learn from, and your voice belongs among theirs.\n\n"
            "[Enter the Strategium](https://jericho-strategium.duckdns.org/)"
        ),
        colour=discord.Colour.gold(),
    )
    lineage = "Brother of the Long Watch" if chapter_name == "Black Shield" else chapter_name
    lineage = discord.utils.escape_markdown(discord.utils.escape_mentions(lineage))
    embed.add_field(name="`ʙʀᴏᴛʜᴇʀʜᴏᴏᴅ ᴏғ ᴛʜᴇ ᴡᴀᴛᴄʜ`", value=f"**{lineage}**\n{chapter_line}", inline=False)
    embed.set_footer(text=f"Induction {entry['request_id']}")
    return embed


async def _resolve_text_channel(guild, channel_id):
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        channel = await guild.fetch_channel(int(channel_id))
    if channel.guild.id != guild.id or not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError("The configured induction destination must be a text channel or thread in this server.")
    return channel


async def _find_marked_message(channel, guild, footer, started_at):
    after = discord.utils.parse_time(started_at)
    async for message in channel.history(limit=100, after=after, oldest_first=True):
        if message.author.id == guild.me.id and any(
            getattr(embed, "footer", None) and embed.footer.text == footer for embed in message.embeds
        ):
            return message
    return None


def _footer(entry):
    return f"Induction {entry['request_id']}"


async def _authorize_command(interaction):
    if interaction.guild is None:
        raise ValueError("This command must be used in a server.")
    permission = _bot_symbol("check_command_permission")
    channel_check = _bot_symbol("is_allowed_channel")
    if (
        not callable(permission)
        or not callable(channel_check)
        or not channel_check(interaction)
    ):
        raise ValueError("Access denied.")
    actor = await interaction.guild.fetch_member(interaction.user.id)
    if not permission(actor, "certify_induction"):
        raise ValueError("Access denied.")
    return actor


def _authorized_for_induction(member):
    permission = _bot_symbol("check_command_permission")
    return bool(callable(permission) and permission(member, "certify_induction"))


def _authorized_for_assignment(member):
    role_ids = {role.id for role in getattr(member, "roles", []) or []}
    return bool(role_ids & {WATCH_CAPTAIN_ROLE_ID, WATCH_LIEUTENANT_ROLE_ID})


def _check_role_hierarchy(guild, member, changed_roles):
    bot_member = getattr(guild, "me", None)
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        raise ValueError("The bot needs Manage Roles to complete induction and assignment.")
    if member == guild.owner or member.top_role >= bot_member.top_role:
        raise ValueError("The inductee is above the bot in the role hierarchy.")
    for role in changed_roles:
        if role.managed or role >= bot_member.top_role:
            raise ValueError(f"The bot must be above the `{role.name}` role to assign it.")


def _entry_from_member(member, certifier, chapter_name, chapter_role, chosen_name):
    desired_nickname = _nickname_with_stud_suffix(chosen_name, member.nick or member.display_name)
    chapters = {chapter.casefold() for chapter in (_bot_symbol("HOME_CHAPTERS") or [])}
    chapter_role_ids = sorted(role.id for role in member.roles if role.name.casefold() in chapters)
    return {
        "request_id": uuid.uuid4().hex[:12],
        "guild_id": member.guild.id,
        "member_id": member.id,
        "certifier_id": certifier.id,
        "chosen_name": " ".join(chosen_name.split()),
        "desired_nickname": desired_nickname,
        "chapter_name": chapter_name,
        "chapter_role_id": chapter_role.id,
        "source_chapter_role_ids": chapter_role_ids,
        "source_nickname": member.nick,
        "created_at": _now(),
        "status": "certification_publish_pending",
        "certification_channel_id": VETERAN_PROMOTION_CHANNEL_ID,
        "certification_message_id": None,
        "assignment_channel_id": ACTIVITY_STATUS_CHANNEL_ID,
        "assignment_message_id": None,
        "company_id": None,
        "kt_id": None,
        "selection_version": 0,
        "selection_sync_pending": False,
        "welcome_status": "not_due",
    }


async def _publish_certification(guild, entry):
    channel = await _resolve_text_channel(guild, entry["certification_channel_id"])
    if entry["status"] == "certification_publishing":
        message = await _find_marked_message(channel, guild, _footer(entry), entry["certification_started_at"])
        if message is None:
            entry.update(status="review", error="Certification post outcome is uncertain; inspect Promotions before retrying.")
            _persist_entry(entry)
            return None
        entry.update(status="pending", certification_message_id=message.id)
        _persist_entry(entry)
        return message

    entry.update(status="certification_publishing", certification_started_at=_now())
    state = _load_state()
    state["entries"][entry["request_id"]] = entry
    _save_state(state)
    try:
        message = await channel.send(
            embed=_certification_embed(entry, guild),
            view=InductionApprovalView(entry["request_id"]),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as error:
        if 400 <= error.status < 500:
            entry.update(status="certification_publish_pending", error=str(error)[:500])
            _persist_entry(entry)
        raise
    entry.update(status="pending", certification_message_id=message.id)
    _persist_entry(entry)
    return message


async def _complete_induction(guild, entry):
    member = await guild.fetch_member(entry["member_id"])
    chapter_role = guild.get_role(entry["chapter_role_id"])
    brother_role = guild.get_role(WATCH_BROTHER_ROLE_ID)
    if chapter_role is None or brother_role is None:
        raise ValueError("The chosen Chapter or Watch Brother role is missing; induction requires staff review.")
    if chapter_role.name != entry["chapter_name"]:
        raise ValueError("The chosen Chapter role changed after certification; induction requires staff review.")
    chapters = {chapter.casefold() for chapter in (_bot_symbol("HOME_CHAPTERS") or [])}
    current_chapters = [role for role in member.roles if role.name.casefold() in chapters]
    if sorted(role.id for role in current_chapters) != entry["source_chapter_role_ids"]:
        raise ValueError("The inductee's Chapter roles changed after certification; submit a new certification.")
    if member.nick != entry["source_nickname"]:
        raise ValueError("The inductee's nickname changed after certification; submit a new certification.")

    roles_to_add = [role for role in (chapter_role, brother_role) if role not in member.roles]
    roles_to_remove = [role for role in current_chapters if role.id != chapter_role.id]
    _check_role_hierarchy(guild, member, [*roles_to_add, *roles_to_remove])
    entry.update(
        status="induction_processing",
        induction_started_at=_now(),
        source_role_ids=sorted(role.id for role in member.roles),
        target_role_ids=sorted(
            ({role.id for role in member.roles} - {role.id for role in roles_to_remove})
            | {role.id for role in roles_to_add}
        ),
    )
    state = _load_state()
    state["entries"][entry["request_id"]] = entry
    _save_state(state)

    reason = f"Induction {entry['request_id']} approved by {entry['induction_approver_id']}"
    if roles_to_add:
        await member.add_roles(*roles_to_add, reason=reason, atomic=True)
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason=reason, atomic=True)
    if member.nick != entry["desired_nickname"]:
        await member.edit(nick=entry["desired_nickname"], reason=reason)

    fresh = await guild.fetch_member(member.id)
    fresh_role_ids = {role.id for role in fresh.roles}
    if not set(entry["target_role_ids"]).issubset(fresh_role_ids) or fresh.nick != entry["desired_nickname"]:
        raise ValueError("Induction changes could not be confirmed; staff review is required.")
    if any(role.id in fresh_role_ids for role in roles_to_remove):
        raise ValueError("Conflicting Chapter role removal could not be confirmed; staff review is required.")
    entry.update(status="assignment_publish_pending", induction_completed_at=_now(), welcome_status="not_due")
    _persist_entry(entry)


async def _recover_induction(guild, entry):
    member = await guild.fetch_member(entry["member_id"])
    role_ids = sorted(role.id for role in member.roles)
    source_ids = entry.get("source_role_ids")
    target_ids = entry.get("target_role_ids")
    if source_ids is None or target_ids is None:
        entry["status"] = "pending"
    elif role_ids == source_ids and member.nick == entry.get("source_nickname"):
        entry["status"] = "pending"
    elif role_ids == target_ids and member.nick == entry["desired_nickname"]:
        entry.update(status="assignment_publish_pending", induction_completed_at=entry.get("induction_started_at"))
    elif role_ids == target_ids and member.nick == entry.get("source_nickname"):
        try:
            await member.edit(
                nick=entry["desired_nickname"],
                reason=f"Complete induction {entry['request_id']} nickname recovery",
            )
            fresh = await guild.fetch_member(member.id)
            if fresh.nick == entry["desired_nickname"]:
                entry.update(status="assignment_publish_pending", induction_completed_at=entry.get("induction_started_at"))
            else:
                raise ValueError("Recovered nickname could not be confirmed.")
        except Exception as error:
            entry.update(status="review", error=f"Nickname recovery failed: {error}"[:500])
    else:
        entry.update(status="review", error="Induction was interrupted after a partial role or nickname change; reconcile manually.")


def _company_roles(guild):
    from .transfer_ops import _companies

    return [role for role, _command_role in _companies(guild).values()]


def _kill_team_roles(guild):
    allowed_ids = _bot_symbol("ALLOWED_KT_ROLE_IDS") or set()
    return sorted(
        (role for role in getattr(guild, "roles", []) or [] if role.id in allowed_ids),
        key=lambda role: role.name.casefold(),
    )


class _AssignmentSelect(discord.ui.Select):
    def __init__(self, request_id, kind, options, default_value=None):
        super().__init__(
            placeholder="Select Watch Company" if kind == "company" else "Select Kill-Team",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=option.name[:100],
                    value=str(option.id),
                    default=str(option.id) == str(default_value),
                )
                for option in options[:25]
            ],
            custom_id=f"induction_{kind}:{request_id}",
        )
        self.request_id = request_id
        self.kind = kind

    async def callback(self, interaction):
        await _handle_assignment_selection(interaction, self.request_id, self.kind, self.values[0])


class InductionApprovalView(discord.ui.View):
    def __init__(self, request_id):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.approve.custom_id = f"induction_approve:{request_id}"

    @discord.ui.button(label="APPROVE", style=discord.ButtonStyle.success, custom_id="induction_approve:placeholder")
    async def approve(self, interaction, button):
        await _handle_induction_approval(interaction, self.request_id)


class InductionAssignmentView(discord.ui.View):
    def __init__(self, request_id, guild, entry):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.assign.custom_id = f"induction_assign:{request_id}"
        company_roles = _company_roles(guild)
        kt_roles = _kill_team_roles(guild)
        if company_roles:
            self.add_item(_AssignmentSelect(request_id, "company", company_roles, entry.get("company_id")))
        if kt_roles:
            self.add_item(_AssignmentSelect(request_id, "kt", kt_roles, entry.get("kt_id")))
        self.assign.disabled = (
            not (entry.get("company_id") and entry.get("kt_id"))
            or bool(entry.get("selection_sync_pending"))
        )

    @discord.ui.button(label="ASSIGN", style=discord.ButtonStyle.success, custom_id="induction_assign:placeholder")
    async def assign(self, interaction, button):
        await _handle_assignment(interaction, self.request_id)


async def _handle_induction_approval(interaction, request_id):
    try:
        guild = interaction.guild
        if guild is None:
            raise ValueError("This approval must be used in the Watch Fortress server.")
        actor = await guild.fetch_member(interaction.user.id)
        if not _authorized_for_induction(actor):
            raise ValueError("Only induction-authorized personnel may approve this induction.")
        async with _INDUCTION_LOCK:
            actor = await guild.fetch_member(interaction.user.id)
            if not _authorized_for_induction(actor):
                raise ValueError("Only induction-authorized personnel may approve this induction.")
            state = _load_state()
            entry = state["entries"].get(request_id)
            if (
                entry is None
                or entry["guild_id"] != guild.id
                or entry.get("certification_message_id") != getattr(interaction.message, "id", None)
                or entry.get("certification_channel_id") != interaction.channel_id
                or entry["status"] != "pending"
            ):
                raise ValueError("This induction is missing, stale, or already being processed.")
            entry.update(induction_approver_id=actor.id, status="induction_processing")
            state["entries"][request_id] = entry
            _save_state(state)

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            actor = await guild.fetch_member(interaction.user.id)
            if not _authorized_for_induction(actor):
                raise ValueError("Only induction-authorized personnel may approve this induction.")
            entry["induction_approver_id"] = actor.id
            await _complete_induction(guild, entry)
        except Exception as error:
            state = _load_state()
            stored = state["entries"].get(request_id, entry)
            if stored.get("source_role_ids") is None:
                stored.update(status="pending", error=str(error)[:500])
            else:
                await _recover_induction(guild, stored)
            entry["error"] = str(error)[:500]
            entry.update(stored)
            _persist_entry(stored)
            await _refresh_certification_message(guild, stored)
            raise
        try:
            await _publish_assignment(guild, entry)
        except Exception as error:
            entry["error"] = str(error)[:500]
            state = _load_state()
            state["entries"][request_id] = entry
            _save_state(state)
            if entry.get("induction_completed_at"):
                await _refresh_certification_message(guild, entry)
            raise
        if entry.get("induction_completed_at"):
            await _refresh_certification_message(guild, entry)
        if entry["status"] == "assignment_pending":
            await _reply(interaction, "Induction approved. Watch Brother and Chapter roles are assigned; the assignment embed is posted.")
        else:
            await _reply(interaction, "Induction approved. Role changes are confirmed, but assignment publication requires review.")
    except Exception as error:
        await _report_interaction_error(interaction, error)


async def _refresh_certification_message(guild, entry):
    try:
        channel = await _resolve_text_channel(guild, entry["certification_channel_id"])
        message = await channel.fetch_message(entry["certification_message_id"])
        embed = _certification_embed(entry, guild)
        if entry.get("induction_completed_at"):
            embed.add_field(name="Induction Approved By", value=f"<@{entry['induction_approver_id']}>", inline=True)
        if entry.get("status") == "review":
            embed.add_field(name="Status", value="Requires staff review.", inline=False)
        elif entry.get("status") == "cancelled":
            embed.add_field(name="Status", value=f"Closed by <@{entry.get('closed_by', 0)}>.", inline=False)
        if entry.get("error"):
            embed.add_field(name="Notice", value=discord.utils.escape_mentions(entry["error"])[:1024], inline=False)
        view = InductionApprovalView(entry["request_id"]) if entry.get("status") == "pending" else None
        await message.edit(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        _g.logger.exception("Could not refresh completed induction %s", entry["request_id"])


async def _publish_assignment(guild, entry):
    channel = await _resolve_text_channel(guild, entry["assignment_channel_id"])
    if entry.get("assignment_message_id"):
        message = await channel.fetch_message(entry["assignment_message_id"])
        await message.edit(
            embed=_assignment_embed(entry),
            view=InductionAssignmentView(entry["request_id"], guild, entry)
            if entry["status"] == "assignment_pending" else None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return message
    if entry["status"] == "assignment_publishing":
        message = await _find_marked_message(channel, guild, _footer(entry), entry["assignment_publish_started_at"])
        if message is None:
            entry.update(status="review", error="Assignment post outcome is uncertain; inspect Assignments before retrying.")
            _persist_entry(entry)
            return None
        entry.update(status="assignment_pending", assignment_message_id=message.id)
        _persist_entry(entry)
        await message.edit(view=InductionAssignmentView(entry["request_id"], guild, entry))
        return message

    entry.update(status="assignment_publishing", assignment_publish_started_at=_now())
    state = _load_state()
    state["entries"][entry["request_id"]] = entry
    _save_state(state)
    try:
        message = await channel.send(
            embed=_assignment_embed(entry),
            view=InductionAssignmentView(entry["request_id"], guild, entry),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as error:
        if 400 <= error.status < 500:
            entry.update(status="assignment_publish_pending", error=str(error)[:500])
            _persist_entry(entry)
        raise
    entry.update(status="assignment_pending", assignment_message_id=message.id)
    _persist_entry(entry)
    return message


async def _handle_assignment_selection(interaction, request_id, kind, value):
    try:
        guild = interaction.guild
        if guild is None:
            raise ValueError("This selection must be used in the Watch Fortress server.")
        actor = await guild.fetch_member(interaction.user.id)
        if not _authorized_for_assignment(actor):
            raise ValueError("Only Watch Captains or Watch Lieutenants may choose the assignment.")
        async with _INDUCTION_LOCK:
            actor = await guild.fetch_member(interaction.user.id)
            if not _authorized_for_assignment(actor):
                raise ValueError("Only Watch Captains or Watch Lieutenants may choose the assignment.")
            state = _load_state()
            entry = state["entries"].get(request_id)
            if (
                entry is None
                or entry["guild_id"] != guild.id
                or entry.get("assignment_message_id") != getattr(interaction.message, "id", None)
                or entry.get("assignment_channel_id") != interaction.channel_id
                or entry["status"] != "assignment_pending"
            ):
                raise ValueError("This assignment is missing, stale, or already being processed.")
            role_id = int(value)
            if kind == "company":
                valid_ids = {role.id for role in _company_roles(guild)}
                if role_id not in valid_ids:
                    raise ValueError("Choose a configured Watch Company.")
                entry["company_id"] = role_id
                entry["kt_id"] = None
            elif kind == "kt":
                valid_ids = {role.id for role in _kill_team_roles(guild)}
                if role_id not in valid_ids:
                    raise ValueError("Choose a configured Kill-Team.")
                entry["kt_id"] = role_id
            else:
                raise ValueError("Unknown assignment selection.")
            if entry.get("company_id") and entry.get("kt_id"):
                from .transfer_ops import _validate_destination

                _validate_destination(guild, entry["company_id"], entry["kt_id"])
            entry["selection_version"] = int(entry.get("selection_version", 0)) + 1
            entry["selection_sync_pending"] = True
            selection_version = entry["selection_version"]
            state["entries"][request_id] = entry
            _save_state(state)
        render_entry = dict(entry)
        render_entry["selection_sync_pending"] = False
        await interaction.response.edit_message(
            embed=_assignment_embed(render_entry),
            view=InductionAssignmentView(request_id, guild, render_entry),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        reconcile_message = False
        async with _INDUCTION_LOCK:
            state = _load_state()
            current = state["entries"].get(request_id)
            if current and current.get("status") == "assignment_pending":
                if int(current.get("selection_version", 0)) == selection_version:
                    current["selection_sync_pending"] = False
                else:
                    current["selection_sync_pending"] = True
                    reconcile_message = True
                _persist_entry(current)
            else:
                reconcile_message = True
        if reconcile_message and current:
            await _refresh_assignment_message(guild, current)
    except Exception as error:
        await _report_interaction_error(interaction, error)


async def _handle_assignment(interaction, request_id):
    try:
        guild = interaction.guild
        if guild is None:
            raise ValueError("This assignment must be used in the Watch Fortress server.")
        actor = await guild.fetch_member(interaction.user.id)
        if not _authorized_for_assignment(actor):
            raise ValueError("Only Watch Captains or Watch Lieutenants may assign a new Brother.")
        async with _INDUCTION_LOCK:
            state = _load_state()
            entry = state["entries"].get(request_id)
            if (
                entry is None
                or entry["guild_id"] != guild.id
                or entry.get("assignment_message_id") != getattr(interaction.message, "id", None)
                or entry.get("assignment_channel_id") != interaction.channel_id
                or entry["status"] != "assignment_pending"
            ):
                raise ValueError("This assignment is missing, stale, or already being processed.")
            if entry.get("selection_sync_pending"):
                raise ValueError("The selected destination is still synchronizing. Review the embed, then press ASSIGN again.")
            if not entry.get("company_id") or not entry.get("kt_id"):
                raise ValueError("Select both a Watch Company and Kill-Team before assigning.")
            entry.update(status="assignment_processing", assigner_id=actor.id)
            state["entries"][request_id] = entry
            _save_state(state)

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            actor = await guild.fetch_member(interaction.user.id)
            if not _authorized_for_assignment(actor):
                raise ValueError("Only Watch Captains or Watch Lieutenants may assign a new Brother.")
            await _execute_assignment(guild, entry)
        except Exception as error:
            state = _load_state()
            stored = state["entries"].get(request_id, entry)
            await _recover_assignment(guild, stored)
            stored["error"] = str(error)[:500]
            entry.update(stored)
            _persist_entry(stored)
            await _refresh_assignment_message(guild, stored)
            raise
        await _deliver_welcome(guild, entry)
        await _refresh_assignment_message(guild, entry)
        await _reply(interaction, "Company and Kill-Team assigned. The Kill-Team welcome was delivered or queued for review.")
    except Exception as error:
        await _report_interaction_error(interaction, error)


async def _execute_assignment(guild, entry):
    from .transfer_ops import _assignment_ids, _companies, _target_roles

    member = await guild.fetch_member(entry["member_id"])
    target_roles = _target_roles(member, entry["company_id"], entry["kt_id"])
    source_ids = _assignment_ids(member)
    configured_companies = _companies(guild)
    assignment_role_ids = {
        role.id
        for company_role, command_role in configured_companies.values()
        for role in (company_role, command_role)
        if role is not None
    } | set(_bot_symbol("ALLOWED_KT_ROLE_IDS") or set())
    target_ids = sorted(role.id for role in target_roles if role.id in assignment_role_ids)
    member_role_ids = {role.id for role in member.roles}
    changed_roles = [role for role in target_roles if role.id not in member_role_ids]
    remove_roles = [role for role in member.roles if role.id in source_ids and role not in target_roles]
    _check_role_hierarchy(guild, member, [*changed_roles, *remove_roles])
    entry.update(assignment_started_at=_now(), source_assignment_ids=source_ids, target_assignment_ids=target_ids)
    state = _load_state()
    state["entries"][entry["request_id"]] = entry
    _save_state(state)
    reason = f"First assignment for induction {entry['request_id']} authorized by {entry['assigner_id']}"
    if changed_roles:
        await member.add_roles(*changed_roles, reason=reason, atomic=True)
    if remove_roles:
        await member.remove_roles(*remove_roles, reason=reason, atomic=True)
    fresh = await guild.fetch_member(member.id)
    if sorted(_assignment_ids(fresh)) != target_ids:
        raise ValueError("Company/Kill-Team role update could not be confirmed; staff review is required.")
    entry.update(status="assigned", assignment_completed_at=_now(), welcome_status="welcome_pending")
    _persist_entry(entry)


async def _recover_assignment(guild, entry):
    from .transfer_ops import _assignment_ids

    member = await guild.fetch_member(entry["member_id"])
    current = _assignment_ids(member)
    source = entry.get("source_assignment_ids")
    target = entry.get("target_assignment_ids")
    if source is None or target is None:
        entry["status"] = "assignment_pending"
    elif current == target:
        entry.update(status="assigned", assignment_completed_at=entry.get("assignment_started_at"), welcome_status="welcome_pending")
    elif current == source:
        entry["status"] = "assignment_pending"
    else:
        entry.update(status="review", error="Assignment was interrupted after partial role changes; reconcile manually.")


async def _refresh_assignment_message(guild, entry):
    try:
        channel = await _resolve_text_channel(guild, entry["assignment_channel_id"])
        message = await channel.fetch_message(entry["assignment_message_id"])
        for _attempt in range(3):
            state = _load_state()
            current = state["entries"].get(entry["request_id"], entry)
            selection_version = int(current.get("selection_version", 0))
            render_entry = dict(current)
            render_entry["selection_sync_pending"] = False
            await message.edit(
                embed=_assignment_embed(render_entry),
                view=InductionAssignmentView(current["request_id"], guild, render_entry)
                if current["status"] == "assignment_pending" else None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            latest_state = _load_state()
            latest = latest_state["entries"].get(entry["request_id"], current)
            if (
                latest.get("status") != current.get("status")
                or int(latest.get("selection_version", 0)) != selection_version
            ):
                if latest.get("status") == "assignment_pending":
                    latest["selection_sync_pending"] = True
                _persist_entry(latest)
                entry.update(latest)
                continue
            latest["selection_sync_pending"] = False
            _persist_entry(latest)
            entry.update(latest)
            return True
        return False
    except Exception:
        _g.logger.exception("Could not refresh assignment message for induction %s", entry["request_id"])
        return False


async def _deliver_welcome(guild, entry):
    if entry.get("status") != "assigned" or entry.get("welcome_status") not in {
        "welcome_pending", "welcome_sending", "welcome_uncertain"
    }:
        return
    from .transfer_ops import _channel, _welcome_channel

    try:
        kt_role = guild.get_role(entry["kt_id"])
        if kt_role is None:
            raise ValueError("The assigned Kill-Team role no longer exists.")
        if entry["welcome_status"] in {"welcome_sending", "welcome_uncertain"}:
            channel = await _channel(guild, entry["welcome_channel_id"])
            message = await _find_marked_message(channel, guild, _footer(entry), entry["welcome_started_at"])
            if message is None:
                entry["welcome_status"] = "welcome_uncertain"
                entry["error"] = "Welcome send outcome is uncertain; inspect the Kill-Team chat before retrying."
                return
        else:
            member = await guild.fetch_member(entry["member_id"])
            if not {entry["company_id"], entry["kt_id"]} <= {role.id for role in member.roles}:
                raise ValueError("The Brother's Company/Kill-Team roles changed before welcome delivery.")
            channel = await _welcome_channel(
                guild,
                {"company_id": entry["company_id"], "kt_id": entry["kt_id"],
                 "actual_company_id": entry["company_id"], "actual_kt_id": entry["kt_id"]},
                kt_role,
            )
            entry.update(welcome_status="welcome_sending", welcome_channel_id=channel.id, welcome_started_at=_now())
            state = _load_state()
            state["entries"][entry["request_id"]] = entry
            _save_state(state)
            try:
                message = await channel.send(
                    content=f"<@&{kt_role.id}> <@{entry['member_id']}>",
                    embed=_welcome_embed(entry, kt_role),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        roles=[discord.Object(id=kt_role.id)],
                        users=[discord.Object(id=entry["member_id"])],
                        replied_user=False,
                    ),
                )
            except discord.HTTPException as error:
                entry["welcome_status"] = "welcome_pending" if 400 <= error.status < 500 else "welcome_uncertain"
                raise
        entry.update(welcome_status="sent", welcome_message_id=message.id)
        entry.pop("error", None)
    except Exception as error:
        entry["error"] = str(error)[:500]
        _g.logger.exception("Induction welcome delivery failed for %s", entry["request_id"])
    finally:
        state = _load_state()
        state["entries"][entry["request_id"]] = entry
        _save_state(state)


async def _reply(interaction, message):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        _g.logger.exception("Could not reply to induction interaction")


async def _report_interaction_error(interaction, error):
    _g.logger.exception("Induction interaction failed")
    if isinstance(error, (ValueError, TypeError)):
        message = str(error)
    else:
        message = "The induction could not be completed. Check the workflow status or contact command staff."
    await _reply(interaction, message)


async def _authorize_resolution(interaction):
    if interaction.guild is None:
        raise ValueError("This command must be used in the Watch Fortress server.")
    permission = _bot_symbol("check_command_permission")
    channel_check = _bot_symbol("is_allowed_channel")
    if not callable(permission) or not callable(channel_check) or not channel_check(interaction):
        raise ValueError("Access denied.")
    actor = await interaction.guild.fetch_member(interaction.user.id)
    if not permission(actor, "resolve_induction"):
        raise ValueError("Only Watch Command may resolve induction records.")
    return actor


async def _resolve_review_record(interaction, request_id, action):
    actor = await _authorize_resolution(interaction)
    guild = interaction.guild
    async with _INDUCTION_LOCK:
        state = _load_state()
        entry = state["entries"].get(request_id)
        if entry is None or entry["guild_id"] != guild.id:
            raise ValueError("Induction record not found in this server.")
        if entry["status"] != "review":
            raise ValueError("Only review-required induction records can be resolved here.")
        if action == "close":
            entry.update(status="cancelled", closed_by=actor.id, closed_at=_now())
            _persist_entry(entry)
            phase = "closed"
        elif action == "reconcile":
            if not entry.get("certification_message_id") and entry.get("certification_started_at") and not entry.get("induction_started_at"):
                entry["status"] = "certification_publishing"
                phase = "certification_publish"
            elif not entry.get("assignment_message_id") and entry.get("assignment_publish_started_at"):
                entry["status"] = "assignment_publishing"
                phase = "assignment_publish"
            elif entry.get("assignment_message_id") and entry.get("induction_completed_at"):
                entry["status"] = "assignment_pending"
                phase = "assignment_existing"
            elif entry.get("source_role_ids") and not entry.get("induction_completed_at"):
                entry["status"] = "induction_processing"
                phase = "induction_roles"
            elif entry.get("source_assignment_ids") and not entry.get("assignment_completed_at"):
                entry["status"] = "assignment_processing"
                phase = "assignment_roles"
            else:
                raise ValueError("No safe automatic reconciliation applies. Correct roles or inspect the target channel, then retry or close this record.")
            _persist_entry(entry)
        else:
            raise ValueError("Choose Reconcile or Close.")

    if phase == "closed":
        if entry.get("certification_message_id"):
            await _refresh_certification_message(guild, entry)
        if entry.get("assignment_message_id"):
            await _refresh_assignment_message(guild, entry)
        await _reply(interaction, "Induction record closed. This does not roll back any roles already applied.")
        return

    try:
        if phase == "certification_publish":
            await _publish_certification(guild, entry)
        elif phase == "assignment_publish":
            await _publish_assignment(guild, entry)
        elif phase == "induction_roles":
            await _recover_induction(guild, entry)
            _persist_entry(entry)
        elif phase == "assignment_roles":
            await _recover_assignment(guild, entry)
            _persist_entry(entry)

        if entry["status"] == "assignment_publish_pending":
            await _publish_assignment(guild, entry)
        if entry.get("certification_message_id"):
            await _refresh_certification_message(guild, entry)
        if entry.get("assignment_message_id"):
            await _refresh_assignment_message(guild, entry)
        if entry["status"] == "pending" and entry.get("certification_message_id"):
            _g.bot.add_view(InductionApprovalView(request_id), message_id=entry["certification_message_id"])
        elif entry["status"] == "assignment_pending" and entry.get("assignment_message_id"):
            _g.bot.add_view(InductionAssignmentView(request_id, guild, entry), message_id=entry["assignment_message_id"])
        elif entry["status"] == "assigned":
            await _deliver_welcome(guild, entry)
        _persist_entry(entry)
    except Exception as error:
        entry.update(status="review", error=str(error)[:500])
        _persist_entry(entry)
        if entry.get("certification_message_id"):
            await _refresh_certification_message(guild, entry)
        if entry.get("assignment_message_id"):
            await _refresh_assignment_message(guild, entry)
        raise

    if entry["status"] == "review":
        await _reply(interaction, "This record still requires manual review. Correct the member roles or inspect the target channel, then reconcile or close it.")
    else:
        await _reply(interaction, f"Induction record reconciled to `{entry['status']}`.")


@_g.bot.tree.command(name="resolve_induction", description="Reconcile or close a review-required induction record.")
@app_commands.guild_only()
@app_commands.describe(request_id="Induction ID shown in the workflow embed footer.", action="Reconcile actual state or close the record.")
@app_commands.choices(action=[
    app_commands.Choice(name="Reconcile", value="reconcile"),
    app_commands.Choice(name="Close", value="close"),
])
async def resolve_induction(interaction, request_id: str, action: app_commands.Choice[str]):
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _resolve_review_record(interaction, request_id.strip(), action.value)
    except Exception as error:
        await _report_interaction_error(interaction, error)


@_g.bot.tree.command(name="certify_induction", description="Certify a completed Watch Brother induction.")
@app_commands.guild_only()
@app_commands.describe(
    inductee="Member who completed induction.",
    chosen_name="Name the inductee wishes to use.",
    chapter="Chosen home Chapter role.",
)
@app_commands.autocomplete(chapter=_chapter_autocomplete)
async def certify_induction(interaction, inductee: discord.Member, chosen_name: str, chapter: str):
    try:
        certifier = await _authorize_command(interaction)
        if inductee.bot or inductee.guild.id != interaction.guild.id:
            raise ValueError("Choose a human member of this Watch Fortress.")
        if not any((getattr(role, "name", "") or "").casefold() == "watch inductee" for role in inductee.roles):
            raise ValueError("The selected member must hold the Watch Inductee role.")
        canonical_chapter, chapter_role = _resolve_chapter_role(interaction.guild, chapter)
        entry = _entry_from_member(inductee, certifier, canonical_chapter, chapter_role, chosen_name)
        async with _INDUCTION_LOCK:
            state = _load_state()
            existing = next(
                (item for item in state["entries"].values()
                 if item["guild_id"] == interaction.guild.id
                 and item["member_id"] == inductee.id
                 and item["status"] in _OPEN_STATUSES),
                None,
            )
            if existing:
                raise ValueError("This member already has an open induction workflow.")
            state["entries"][entry["request_id"]] = entry
            _save_state(state)
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await _publish_certification(interaction.guild, entry)
        if message:
            await _reply(interaction, f"Induction certification posted: {message.jump_url}")
        else:
            await _reply(interaction, "Certification post requires staff review; no duplicate post was created.")
    except Exception as error:
        await _report_interaction_error(interaction, error)


async def register_persistent_views():
    async with _INDUCTION_LOCK:
        state = _load_state()
        for entry in state["entries"].values():
            guild = _g.bot.get_guild(entry["guild_id"])
            if guild is None:
                continue
            try:
                if entry["status"] == "induction_processing":
                    await _recover_induction(guild, entry)
                    _persist_entry(entry)
                elif entry["status"] == "assignment_processing":
                    await _recover_assignment(guild, entry)
                    _persist_entry(entry)
                elif entry["status"] == "certification_publish_pending":
                    await _publish_certification(guild, entry)
                elif entry["status"] == "certification_publishing":
                    await _publish_certification(guild, entry)
                elif entry["status"] == "assignment_publish_pending":
                    await _publish_assignment(guild, entry)
                elif entry["status"] == "assignment_publishing":
                    await _publish_assignment(guild, entry)
                if entry["status"] == "assignment_pending" and entry.get("selection_sync_pending"):
                    await _refresh_assignment_message(guild, entry)
                if entry["status"] == "pending" and entry.get("certification_message_id"):
                    _g.bot.add_view(InductionApprovalView(entry["request_id"]), message_id=entry["certification_message_id"])
                elif entry["status"] == "assignment_pending" and entry.get("assignment_message_id"):
                    _g.bot.add_view(
                        InductionAssignmentView(entry["request_id"], guild, entry),
                        message_id=entry["assignment_message_id"],
                    )
                elif entry["status"] == "assigned" and entry.get("welcome_status") in {
                    "welcome_pending", "welcome_sending", "welcome_uncertain"
                }:
                    await _deliver_welcome(guild, entry)
                _persist_entry(entry)
            except Exception:
                _g.logger.exception("Could not restore induction workflow %s", entry.get("request_id"))