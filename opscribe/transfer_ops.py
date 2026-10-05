"""Persistent, staff-reviewed company and Kill Team transfers."""

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
from .constants import DATA_DIR, LOA_ROLE_ID, RESERVES_ROLE_ID, VETERAN_SERGEANT_ROLE_ID
from .permissions import BATTLE_LINE_TRACK
from .roster_ops import _is_kill_team_membership_role


TRANSFER_REQUESTS_PATH = os.path.join(DATA_DIR, "transfer_requests.json")
_TRANSFER_LOCK = asyncio.Lock()
_OPEN_STATUSES = {"publishing", "pending", "processing"}
_STATUSES = _OPEN_STATUSES | {"approved", "denied", "superseded", "stale", "failed"}
_KT_RANKS = set(BATTLE_LINE_TRACK["Watch Brother"]) | {"Oathsworn", "Watch Master"}


def _b(name):
    module = sys.modules.get("opscribe.bot") or sys.modules.get("bot")
    return getattr(module, name, None)


def _settings():
    config = _g.CONFIG.get("transfers") or {}
    return {
        "guild_id": int(config.get("guild_id", 1429264578440597517)),
        "channel_id": int(config.get("channel_id", 1459043645499117630)),
        "notification_role_id": int(config.get("notification_role_id", VETERAN_SERGEANT_ROLE_ID)),
    }


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_state():
    if not os.path.exists(TRANSFER_REQUESTS_PATH):
        return {"entries": {}}
    with open(TRANSFER_REQUESTS_PATH, encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict) or not isinstance(state.get("entries"), dict):
        raise ValueError("Invalid transfer request state; contact the Forgemaster.")
    for request_id, entry in state["entries"].items():
        if not isinstance(entry, dict) or entry.get("status") not in _STATUSES:
            raise ValueError("Invalid transfer request record; contact the Forgemaster.")
        if entry.get("request_id") != request_id:
            raise ValueError("Invalid transfer request ID; contact the Forgemaster.")
        for key in ("guild_id", "channel_id", "member_id", "company_id", "kt_id"):
            if not isinstance(entry.get(key), int) or entry[key] <= 0:
                raise ValueError("Invalid transfer request destination; contact the Forgemaster.")
    return state


def _save_state(state):
    directory = os.path.dirname(TRANSFER_REQUESTS_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = TRANSFER_REQUESTS_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    if os.path.exists(TRANSFER_REQUESTS_PATH):
        shutil.copyfile(TRANSFER_REQUESTS_PATH, TRANSFER_REQUESTS_PATH + ".bak")
    os.replace(temporary, TRANSFER_REQUESTS_PATH)


def _companies(guild):
    result = {}
    for entry in (_g.CONFIG.get("companies") or {}).values():
        role = guild.get_role(int(entry.get("companyRoleId") or 0))
        if role is not None:
            result[role.id] = (role, guild.get_role(int(entry.get("companyCommandRoleId") or 0)))
    return result


def _assignment_ids(member):
    companies = _companies(member.guild)
    command_ids = {command.id for _, command in companies.values() if command is not None}
    return sorted(
        role.id for role in member.roles
        if role.id in companies or role.id in command_ids or _is_kt_role(role)
    )


def _is_kt_role(role):
    return role.name.casefold() != "kill team champion" and _is_kill_team_membership_role(role)


def _role_names(member):
    helper = _b("_canonical_role_names")
    return helper(member) if callable(helper) else {role.name for role in member.roles}


def _is_staff(member):
    allowed = BATTLE_LINE_TRACK["Veteran Sergeant"] | {"Watch Master"}
    permission = _b("check_command_permission")
    if not callable(permission) or not permission(member, "initiate_transfer"):
        return False
    return bool(_b("DEBUG_MODE")) or bool(_role_names(member) & allowed)


async def _authorize(interaction, *, staff=False, command=None):
    if interaction.guild is None or interaction.guild.id != _settings()["guild_id"]:
        raise ValueError("Transfers must be used in the configured Watch Fortress server.")
    member = await interaction.guild.fetch_member(interaction.user.id)
    if staff and not _is_staff(member):
        raise ValueError("Only Veteran Sergeant, Watch Lieutenant, Watch Captain, or Watch Master may process transfers.")
    if command:
        permission = _b("check_command_permission")
        channel_check = _b("is_allowed_channel")
        if not callable(permission) or not permission(member, command) or not callable(channel_check) or not channel_check(interaction):
            raise ValueError("Access denied: permission or command channel mismatch.")
    return member


def _validate_destination(guild, company_id, kt_id):
    companies = _companies(guild)
    if company_id not in companies:
        raise ValueError("Select a configured Watch Company.")
    kt = guild.get_role(kt_id)
    if kt is None or kt.id not in (_b("ALLOWED_KT_ROLE_IDS") or set()):
        raise ValueError("Select a configured Kill Team.")
    known_companies = set()
    for member in kt.members:
        if member.bot or any(role.id == RESERVES_ROLE_ID or role.name.casefold() == "reserves" for role in member.roles):
            continue
        known_companies.update(role.id for role in member.roles if role.id in companies)
    if known_companies and known_companies != {company_id}:
        raise ValueError("The Kill Team belongs to another company or has ambiguous company membership.")
    return companies[company_id][0], kt


def _validate_member(member, *, preview=False):
    from .loa_ops import _get_active_loa

    names = _role_names(member)
    if member.bot or names & {"Reserves", "Interred Brother"} or any(role.id == RESERVES_ROLE_ID for role in member.roles):
        raise ValueError("Only active members can transfer; Reserves returns use the existing activity process.")
    if any(role.id == LOA_ROLE_ID for role in member.roles) or _get_active_loa(member.id):
        raise ValueError("Members on LOA cannot transfer until their LOA ends.")
    if preview:
        return
    if "Watch Brother" not in names:
        raise ValueError("The member must hold their base Watch Brother role before joining a Kill Team.")
    highest = next((rank for rank in (_b("RANK_ROLES_PRIORITY") or []) if rank in names), None)
    if highest not in _KT_RANKS:
        raise ValueError("This member's rank is not eligible for Kill Team membership.")


def _target_roles(member, company_id, kt_id):
    _validate_member(member)
    company, kt = _validate_destination(member.guild, company_id, kt_id)
    companies = _companies(member.guild)
    assignment_ids = set(_assignment_ids(member))
    roles = [role for role in member.roles if role.id not in assignment_ids and not role.is_default()]
    roles.extend([company, kt])
    if _role_names(member) & {"Watch Lieutenant", "Watch Captain", "Watch Master"}:
        command = companies[company_id][1]
        if command is None:
            raise ValueError("The destination company command role is missing.")
        roles.append(command)
    if {role.id for role in roles} == {role.id for role in member.roles if not role.is_default()}:
        raise ValueError("The member already holds this company and Kill Team assignment.")
    changed = {role.id for role in roles} ^ {role.id for role in member.roles if not role.is_default()}
    bot_member = member.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        raise ValueError("The bot needs Manage Roles to perform transfers.")
    if member == member.guild.owner or member.top_role >= bot_member.top_role:
        raise ValueError("The member is above the bot in the role hierarchy.")
    for role in [*member.roles, *roles]:
        if role.id in changed and (role.managed or role >= bot_member.top_role):
            raise ValueError("The bot must be above every company/KT role being changed.")
    return roles


def _pending_for(state, guild_id, member_id):
    return next((entry for entry in state["entries"].values()
                 if entry["guild_id"] == guild_id and entry["member_id"] == member_id
                 and entry["status"] in _OPEN_STATUSES), None)


def _new_entry(member, company_id, kt_id, *, direct=False, initiator_id=None):
    return {
        "request_id": uuid.uuid4().hex[:12], "guild_id": member.guild.id,
        "channel_id": _settings()["channel_id"], "member_id": member.id,
        "company_id": company_id, "kt_id": kt_id,
        "source_role_ids": _assignment_ids(member), "created_at": _now(),
        "status": "publishing", "message_id": None, "direct": direct,
        "refresh_pending": False,
        "initiator_id": initiator_id or member.id,
    }


def _build_embed(entry):
    status = entry["status"]
    colors = {"approved": discord.Colour.green(), "denied": discord.Colour.red()}
    rulings = {"pending": "Awaiting Ruling", "publishing": "Awaiting Ruling", "approved": "Sanctioned",
               "denied": "Denied", "processing": "In Progress", "superseded": "Superseded",
               "stale": "Outdated", "failed": "Requires Review"}
    title = "Reassignment Order" if entry.get("direct") else "Reassignment Petition"
    embed = discord.Embed(title=f"{title} | {rulings[status]}",
                          colour=colors.get(status, discord.Colour.gold()))
    embed.add_field(name="Brother", value=f"<@{entry['member_id']}>", inline=False)
    command_ids = {int(company.get("companyCommandRoleId") or 0)
                   for company in (_g.CONFIG.get("companies") or {}).values()}
    source = " ".join(f"<@&{role_id}>" for role_id in entry["source_role_ids"] if role_id not in command_ids) or "Unassigned"
    embed.add_field(name="From", value=source, inline=True)
    embed.add_field(name="To", value=f"<@&{entry['company_id']}> / <@&{entry['kt_id']}>", inline=True)
    if entry.get("initiator_id") and entry["initiator_id"] != entry["member_id"]:
        embed.add_field(name="Petitioned by", value=f"<@{entry['initiator_id']}>", inline=False)
    if entry.get("reviewer_id"):
        reviewer_label = "Sanctioned by" if status == "approved" else "Ruled by"
        embed.add_field(name=reviewer_label, value=f"<@{entry['reviewer_id']}>", inline=False)
    if entry.get("resolved_at"):
        embed.timestamp = datetime.fromisoformat(entry["resolved_at"])
    if entry.get("reason"):
        embed.add_field(name="Grounds for Denial", value=discord.utils.escape_mentions(entry["reason"]), inline=False)
    if status == "superseded" and entry.get("actual_company_id"):
        embed.add_field(name="Ordered Reassignment",
                        value=f"<@&{entry['actual_company_id']}> / <@&{entry['actual_kt_id']}>", inline=False)
    if entry.get("error"):
        embed.add_field(name="Notice", value=entry["error"][:1024], inline=False)
    embed.set_footer(text=f"Dossier {entry['request_id']}")
    return embed


async def _channel(guild, channel_id):
    channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
    if channel is None:
        channel = await guild.fetch_channel(channel_id)
    if channel.guild.id != guild.id or not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError("The configured transfer channel must be a text channel or thread in this server.")
    return channel


async def _refresh_embed(guild, entry):
    if not entry.get("message_id"):
        return False
    try:
        channel = await _channel(guild, entry["channel_id"])
        message = await channel.fetch_message(entry["message_id"])
        view = TransferRequestView(entry["request_id"]) if entry["status"] == "pending" else None
        await message.edit(embed=_build_embed(entry), view=view, allowed_mentions=discord.AllowedMentions.none())
        entry["refresh_pending"] = False
        return True
    except Exception:
        _g.logger.exception("Could not refresh transfer request %s", entry["request_id"])
        entry["refresh_pending"] = True
        return False


def _resolve(entry, status, actor_id, **details):
    entry.update(status=status, reviewer_id=actor_id, resolved_at=_now(), refresh_pending=True, **details)


async def _publish(guild, state, entry):
    channel = await _channel(guild, entry["channel_id"])
    state["entries"][entry["request_id"]] = entry
    _save_state(state)
    ping = _settings()["notification_role_id"]
    try:
        message = await channel.send(
            content=f"<@&{ping}>" if not entry.get("direct") else None,
            embed=_build_embed({**entry, "status": "pending"}),
            view=TransferRequestView(entry["request_id"]) if not entry.get("direct") else None,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False,
                                                    roles=[discord.Object(id=ping)] if not entry.get("direct") else []),
        )
    except Exception:
        entry.update(status="failed", error="Could not publish the transfer embed.")
        _save_state(state)
        raise
    entry.update(message_id=message.id, status="pending")
    try:
        _save_state(state)
    except Exception:
        await message.edit(view=None)
        raise
    return message


async def _execute(guild, state, entry, actor_id, company_id, kt_id):
    try:
        member = await guild.fetch_member(entry["member_id"])
    except discord.NotFound:
        _resolve(entry, "failed", actor_id, error="The Brother has left the Watch Fortress; this petition is closed.")
        _save_state(state)
        await _refresh_embed(guild, entry)
        _save_state(state)
        raise ValueError(entry["error"]) from None
    if _assignment_ids(member) != entry["source_role_ids"]:
        _resolve(entry, "stale", actor_id, error="The member's assignment changed; submit a new request.")
        _save_state(state)
        await _refresh_embed(guild, entry)
        _save_state(state)
        raise ValueError(entry["error"])
    roles = _target_roles(member, company_id, kt_id)
    companies = _companies(guild)
    command_ids = {command.id for _, command in companies.values() if command}
    target_assignment = sorted(role.id for role in roles
                               if role.id in companies or role.id in command_ids or _is_kt_role(role))
    entry.update(status="processing", reviewer_id=actor_id, actual_company_id=company_id,
                 actual_kt_id=kt_id, target_assignment_ids=target_assignment, refresh_pending=True)
    _save_state(state)
    try:
        current_ids = {role.id for role in member.roles}
        target_ids = {role.id for role in roles}
        roles_to_add = [role for role in roles if role.id not in current_ids]
        roles_to_remove = [role for role in member.roles if role.id not in target_ids and not role.is_default()]
        reason = f"Transfer {entry['request_id']} authorized by {actor_id}"
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=reason, atomic=True)
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=reason, atomic=True)
        fresh = await guild.fetch_member(member.id)
        if _assignment_ids(fresh) != target_assignment:
            raise ValueError("Transfer result could not be confirmed; staff review is required.")
    except Exception:
        entry["error"] = "Role update could not be confirmed. Recovery will check the actual assignment; do not retry blindly."
        _save_state(state)
        try:
            await _recover_entry(guild, entry)
        except Exception:
            _g.logger.exception("Transfer %s is awaiting recovery", entry["request_id"])
        _save_state(state)
        refreshed = await _refresh_embed(guild, entry)
        _save_state(state)
        if entry["status"] in {"approved", "superseded"}:
            return refreshed
        raise
    status = "approved" if (company_id, kt_id) == (entry["company_id"], entry["kt_id"]) else "superseded"
    entry.pop("error", None)
    _resolve(entry, status, actor_id)
    _save_state(state)
    refreshed = await _refresh_embed(guild, entry)
    _save_state(state)
    return refreshed


async def _recover_entry(guild, entry):
    try:
        member = await guild.fetch_member(entry["member_id"])
    except discord.NotFound:
        _resolve(entry, "failed", entry.get("reviewer_id", 0),
                 error="The Brother has left the Watch Fortress; this petition is closed.")
        return
    current = _assignment_ids(member)
    if current == entry["target_assignment_ids"]:
        status = "approved" if (entry["actual_company_id"], entry["actual_kt_id"]) == (entry["company_id"], entry["kt_id"]) else "superseded"
        _resolve(entry, status, entry["reviewer_id"])
        entry.pop("error", None)
    elif current == entry["source_role_ids"] and not entry.get("direct"):
        entry.update(status="pending", refresh_pending=True)
    else:
        _resolve(entry, "failed", entry["reviewer_id"], error="Interrupted transfer requires staff review of the actual roles.")


def _bound_entry(state, interaction, request_id):
    entry = state["entries"].get(request_id)
    if entry is None or entry["guild_id"] != interaction.guild.id:
        raise ValueError("Transfer request not found in this server.")
    if interaction.message is None or interaction.message.id != entry["message_id"] or interaction.channel_id != entry["channel_id"]:
        raise ValueError("This interaction does not belong to the original transfer request.")
    if entry["status"] != "pending":
        raise ValueError("This transfer request is already resolved or being processed.")
    return entry


async def _reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _report_error(interaction, error):
    _g.logger.exception("Transfer interaction failed")
    text = str(error) if isinstance(error, ValueError) else "Transfer could not be completed. Check the request status or contact the Forgemaster."
    await _reply(interaction, text)


class TransferRequestView(discord.ui.View):
    def __init__(self, request_id):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.approve.custom_id = f"transfer_approve:{request_id}"
        self.deny.custom_id = f"transfer_deny:{request_id}"

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="transfer_approve:placeholder")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await _authorize(interaction, staff=True)
            async with _TRANSFER_LOCK:
                actor = await _authorize(interaction, staff=True)
                state = _load_state()
                entry = _bound_entry(state, interaction, self.request_id)
                refreshed = await _execute(interaction.guild, state, entry, actor.id, entry["company_id"], entry["kt_id"])
            await _reply(interaction, "Transfer approved." if refreshed else "Transfer approved; the original embed refresh is pending recovery.")
        except Exception as error:
            await _report_error(interaction, error)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="transfer_deny:placeholder")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await _authorize(interaction, staff=True)
            _bound_entry(_load_state(), interaction, self.request_id)
            await interaction.response.send_modal(TransferDenialReasonModal(self.request_id, interaction.message.id))
        except Exception as error:
            await _report_error(interaction, error)


class TransferDenialReasonModal(discord.ui.Modal, title="Deny Transfer Request"):
    reason = discord.ui.TextInput(label="Denial reason", style=discord.TextStyle.paragraph,
                                  required=True, min_length=1, max_length=500)

    def __init__(self, request_id, message_id):
        super().__init__()
        self.request_id = request_id
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await _authorize(interaction, staff=True)
            reason = self.reason.value.strip()
            if not reason or len(reason) > 500:
                raise ValueError("A nonblank denial reason of at most 500 characters is required.")
            async with _TRANSFER_LOCK:
                actor = await _authorize(interaction, staff=True)
                state = _load_state()
                entry = state["entries"].get(self.request_id)
                if entry is None or entry["guild_id"] != interaction.guild.id or entry["message_id"] != self.message_id or entry["channel_id"] != interaction.channel_id:
                    raise ValueError("Transfer request not found in this channel.")
                if entry["status"] != "pending":
                    raise ValueError("This transfer request is already resolved or being processed.")
                _resolve(entry, "denied", actor.id, reason=reason)
                _save_state(state)
                refreshed = await _refresh_embed(interaction.guild, entry)
                _save_state(state)
            await _reply(interaction, "Transfer denied." if refreshed else "Transfer denied; the original embed refresh is pending recovery.")
        except Exception as error:
            await _report_error(interaction, error)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await _report_error(interaction, error)


async def _company_autocomplete(interaction, current):
    if interaction.guild is None or interaction.guild.id != _settings()["guild_id"]:
        return []
    return [app_commands.Choice(name=role.name, value=str(role.id))
            for role, _ in _companies(interaction.guild).values() if current.casefold() in role.name.casefold()][:25]


async def _kt_autocomplete(interaction, current):
    if interaction.guild is None or interaction.guild.id != _settings()["guild_id"]:
        return []
    return [app_commands.Choice(name=role.name, value=str(role.id)) for role in interaction.guild.roles
            if role.id in (_b("ALLOWED_KT_ROLE_IDS") or set()) and current.casefold() in role.name.casefold()][:25]


def _destination_ids(company, kt):
    parsed = []
    for value in (company, kt):
        match = re.fullmatch(r"(?:<@&([0-9]+)>|([0-9]+))", value.strip())
        if match is None:
            raise ValueError("Choose both company and Kill Team from autocomplete, or supply role IDs/mentions.")
        parsed.append(int(match.group(1) or match.group(2)))
    return tuple(parsed)


@_g.bot.tree.command(name="request_transfer", description="Request reassignment to a Watch Company and Kill Team.")
@app_commands.guild_only()
@app_commands.describe(company="Destination Watch Company (required).", kt="Destination Kill Team (required).")
@app_commands.autocomplete(company=_company_autocomplete, kt=_kt_autocomplete)
async def request_transfer(interaction: discord.Interaction, company: str, kt: str):
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = await _authorize(interaction, command="request_transfer")
        company_id, kt_id = _destination_ids(company, kt)
        async with _TRANSFER_LOCK:
            member = await _authorize(interaction, command="request_transfer")
            state = _load_state()
            if _pending_for(state, member.guild.id, member.id):
                raise ValueError("You already have an open transfer request.")
            if _b("DEBUG_MODE"):
                _validate_member(member, preview=True)
                _validate_destination(member.guild, company_id, kt_id)
            else:
                _target_roles(member, company_id, kt_id)
            entry = _new_entry(member, company_id, kt_id)
            message = await _publish(member.guild, state, entry)
        await _reply(interaction, f"Transfer request submitted: {message.jump_url}")
    except Exception as error:
        await _report_error(interaction, error)


@_g.bot.tree.command(name="initiate_transfer", description="[Veteran Sergeant+] Petition for a Brother's company and Kill Team reassignment.")
@app_commands.guild_only()
@app_commands.describe(member="Brother proposed for reassignment.", company="Destination Watch Company (required).",
                       kt="Destination Kill Team (required).")
@app_commands.autocomplete(company=_company_autocomplete, kt=_kt_autocomplete)
async def initiate_transfer(interaction: discord.Interaction, member: discord.Member, company: str, kt: str):
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        actor = await _authorize(interaction, staff=True, command="initiate_transfer")
        company_id, kt_id = _destination_ids(company, kt)
        async with _TRANSFER_LOCK:
            actor = await _authorize(interaction, staff=True, command="initiate_transfer")
            state = _load_state()
            fresh = await interaction.guild.fetch_member(member.id)
            entry = _pending_for(state, fresh.guild.id, fresh.id)
            if entry and entry["status"] != "pending":
                raise ValueError("This Brother's transfer is already being processed; wait for recovery.")
            if entry:
                if (entry["company_id"], entry["kt_id"]) != (company_id, kt_id):
                    raise ValueError("This Brother has a different open petition. Deny it before proposing another destination.")
                link = f"https://discord.com/channels/{entry['guild_id']}/{entry['channel_id']}/{entry['message_id']}"
            else:
                _target_roles(fresh, company_id, kt_id)
                entry = _new_entry(fresh, company_id, kt_id, initiator_id=actor.id)
                message = await _publish(interaction.guild, state, entry)
                link = message.jump_url
        await _reply(interaction, f"Petition awaiting ruling: {link}")
    except Exception as error:
        await _report_error(interaction, error)


async def register_persistent_views():
    async with _TRANSFER_LOCK:
        state = _load_state()
        for entry in state["entries"].values():
            guild = _g.bot.get_guild(entry["guild_id"])
            if guild is None:
                continue
            try:
                if entry["status"] == "publishing":
                    _resolve(entry, "failed", 0, error="Submission was interrupted before its message could be saved; submit again.")
                    _save_state(state)
                if entry["status"] == "processing":
                    await _recover_entry(guild, entry)
                    _save_state(state)
                if entry["status"] == "pending" and entry.get("direct"):
                    entry.update(direct=False, refresh_pending=True)
                    _save_state(state)
                if entry.get("refresh_pending"):
                    await _refresh_embed(guild, entry)
                    _save_state(state)
                if entry["status"] == "pending" and not entry.get("direct") and entry.get("message_id"):
                    _g.bot.add_view(TransferRequestView(entry["request_id"]), message_id=entry["message_id"])
            except Exception:
                _g.logger.exception("Could not restore transfer request %s", entry["request_id"])