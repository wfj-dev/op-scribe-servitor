import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from opscribe import _bot_globals as _g


class FakeTree:
    def command(self, **kwargs):
        return lambda callback: callback


_g.bot = SimpleNamespace(tree=FakeTree())
transfer = importlib.import_module("opscribe.transfer_ops")


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def environment(monkeypatch, tmp_path):
    roles = {}
    for role_id, name, position in [(1, "@everyone", 0), (10, "Watch Company Primus", 2),
                                    (11, "Primus Command", 3), (20, "Watch Company Secundus", 2),
                                    (21, "Secundus Command", 3), (30, "Kill Team Alpha", 2),
                                    (40, "Kill Team Beta", 2), (50, "Watch Brother", 4),
                                    (60, "Veteran Sergeant", 5), (70, "Unrelated award", 1)]:
        role = MagicMock(spec=discord.Role)
        role.id, role.name, role.position = role_id, name, position
        role.managed, role.members = False, []
        role.is_default.return_value = role_id == 1
        role.__ge__.side_effect = lambda other, position=position: position >= other.position
        roles[role_id] = role
    guild = MagicMock()
    guild.id = 1429264578440597517
    guild.get_role.side_effect = roles.get
    guild.roles = list(roles.values())
    guild.me = SimpleNamespace(id=333, guild_permissions=SimpleNamespace(manage_roles=True),
                               top_role=SimpleNamespace(position=100))
    guild.owner = None
    member = SimpleNamespace(id=111, guild=guild, bot=False, roles=[roles[index] for index in (1, 10, 30, 50, 70)],
                              top_role=roles[50])
    actor = SimpleNamespace(id=222, guild=guild, bot=False, roles=[roles[60]], top_role=roles[60])

    async def edit(**kwargs):
        member.roles = [roles[1], *kwargs["roles"]]

    async def add_roles(*added, **kwargs):
        present = {role.id for role in member.roles}
        member.roles.extend(role for role in added if role.id not in present)

    async def remove_roles(*removed, **kwargs):
        removed_ids = {role.id for role in removed}
        member.roles = [role for role in member.roles if role.id not in removed_ids]

    member.edit = AsyncMock(side_effect=edit)
    member.add_roles = AsyncMock(side_effect=add_roles)
    member.remove_roles = AsyncMock(side_effect=remove_roles)
    guild.fetch_member = AsyncMock(side_effect=lambda member_id: member if member_id == 111 else actor)
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild, channel.id = guild, 1459043645499117630
    message = SimpleNamespace(id=900, jump_url="https://discord.com/channels/1/2/900", edit=AsyncMock())
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(return_value=message)
    guild.get_channel.return_value = channel
    welcome_channel = MagicMock(spec=discord.TextChannel)
    welcome_channel.id, welcome_channel.guild = 901, guild
    welcome_channel.send = AsyncMock(return_value=SimpleNamespace(id=902))
    guild.get_channel.side_effect = lambda channel_id: welcome_channel if channel_id == 901 else channel
    recipient = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(_g, "bot", SimpleNamespace(
        get_user=MagicMock(return_value=recipient), fetch_user=AsyncMock(return_value=recipient)))
    core = {"check_command_permission": lambda member, command: True, "is_allowed_channel": lambda interaction: True,
            "KT_ROLE_CHANNEL_MAP": {40: 901}, "ALLOWED_KT_FORUM_PARENT_IDS": {800},
            "ALLOWED_KT_ROLE_IDS": {30, 40}, "KILL_TEAMS": [],
            "RANK_ROLES_PRIORITY": ["Watch Master", "Watch Captain", "Watch Lieutenant", "Veteran Sergeant",
                                    "Watch Sergeant", "Oathsworn", "Watch Veteran", "Watch Brother"]}
    monkeypatch.setattr(transfer, "_b", core.get)
    monkeypatch.setattr(transfer, "TRANSFER_REQUESTS_PATH", str(tmp_path / "transfers.json"))
    monkeypatch.setattr(transfer, "_TRANSFER_LOCK", asyncio.Lock())
    monkeypatch.setattr(_g, "CONFIG", {"companies": {
        "primus": {"companyRoleId": 10, "companyCommandRoleId": 11},
        "secundus": {"companyRoleId": 20, "companyCommandRoleId": 21}}})
    monkeypatch.setattr(_g, "logger", MagicMock())
    from opscribe import loa_ops
    monkeypatch.setattr(loa_ops, "_get_active_loa", lambda user_id: None)
    return SimpleNamespace(roles=roles, guild=guild, member=member, actor=actor, channel=channel,
                           message=message, core=core, welcome_channel=welcome_channel, recipient=recipient)


def interaction(env, user=None):
    return SimpleNamespace(user=user or env.actor, guild=env.guild, channel_id=env.channel.id,
                           channel=env.channel, message=env.message,
                           response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(),
                                                    send_modal=AsyncMock(), is_done=lambda: True),
                           followup=SimpleNamespace(send=AsyncMock()))


def seed(env):
    entry = transfer._new_entry(env.member, 20, 40)
    entry.update(status="pending", message_id=env.message.id)
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    return entry


def approve_request(env, entry):
    async def approve():
        await transfer.TransferRequestView(entry["request_id"]).approve.callback(interaction(env))

    run(approve())


def test_role_update_preserves_unrelated_roles(environment):
    env = environment
    roles = transfer._target_roles(env.member, 20, 40)
    assert {role.id for role in roles} == {20, 40, 50, 70}


def test_approved_transfer_posts_compact_kt_welcome(environment):
    env = environment
    entry = seed(env)
    approve_request(env, entry)
    kwargs = env.welcome_channel.send.await_args.kwargs
    assert kwargs["content"] == "<@&40> <@111>"
    assert kwargs["embed"].title == "Welcome to Kill Team Beta"
    assert "Brother <@111>" in kwargs["embed"].description
    assert not kwargs["embed"].fields
    assert "By bolt and blade" in kwargs["embed"].footer.text
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [40]
    assert [user.id for user in kwargs["allowed_mentions"].users] == [111]
    assert kwargs["allowed_mentions"].everyone is False
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["welcome_status"] == "sent" and record["welcome_message_id"] == 902
    env.recipient.send.assert_not_awaited()


def test_missing_kt_thread_reports_error_without_failing_transfer(environment):
    env = environment
    env.core["KT_ROLE_CHANNEL_MAP"] = {}
    env.guild.active_threads = AsyncMock(return_value=[])
    entry = seed(env)
    approve_request(env, entry)
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "approved" and record["welcome_status"] == "pending"
    env.recipient.send.assert_awaited_once()
    _g.bot.get_user.assert_called_once_with(281651485782310914)
    env.welcome_channel.send.assert_not_awaited()
    assert env.message.edit.await_args.kwargs["view"] is None


def approved_welcome(env):
    entry = seed(env)
    env.member.roles = [env.roles[index] for index in (1, 20, 40, 50, 70)]
    transfer._resolve(entry, "approved", env.actor.id, actual_company_id=20, actual_kt_id=40)
    state = {"entries": {entry["request_id"]: entry}}
    transfer._save_state(state)
    return state, entry


def test_ambiguous_kt_threads_never_use_general_fallback(environment):
    env = environment
    env.core["KT_ROLE_CHANNEL_MAP"] = {}
    env.guild.active_threads = AsyncMock(return_value=[
        SimpleNamespace(id=901, guild=env.guild, parent_id=800, name="Kill-Team Beta"),
        SimpleNamespace(id=903, guild=env.guild, parent_id=800, name="Kill Team Beta"),
    ])
    state, entry = approved_welcome(env)
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["welcome_status"] == "pending"
    env.channel.send.assert_not_awaited()
    env.welcome_channel.send.assert_not_awaited()
    env.recipient.send.assert_awaited_once()


def test_unique_kt_thread_matches_and_ignores_other_parents(environment):
    env = environment
    env.core["KT_ROLE_CHANNEL_MAP"] = {}
    env.guild.active_threads = AsyncMock(return_value=[
        SimpleNamespace(id=901, guild=env.guild, parent_id=800, name="Kill-Team Beta"),
        SimpleNamespace(id=903, guild=env.guild, parent_id=999, name="Kill Team Beta"),
    ])
    state, entry = approved_welcome(env)
    run(transfer._deliver_welcome(env.guild, state, entry))
    env.welcome_channel.send.assert_awaited_once()
    assert entry["welcome_status"] == "sent"


def test_mapped_channel_api_lookup(environment):
    env = environment
    env.guild.get_channel.side_effect = lambda channel_id: None
    env.guild.get_thread.return_value = None
    env.guild.fetch_channel = AsyncMock(return_value=env.welcome_channel)
    state, entry = approved_welcome(env)
    run(transfer._deliver_welcome(env.guild, state, entry))
    env.guild.fetch_channel.assert_awaited_once_with(901)
    assert entry["welcome_status"] == "sent"


def test_deleted_mapped_channel_remains_pending_and_dms(environment):
    env = environment
    env.guild.get_channel.side_effect = lambda channel_id: None
    env.guild.get_thread.return_value = None
    env.guild.fetch_channel = AsyncMock(side_effect=discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel"))
    state, entry = approved_welcome(env)
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["status"] == "approved" and entry["welcome_status"] == "pending"
    env.recipient.send.assert_awaited_once()


def test_rejected_send_can_retry_without_duplicate_error_dms(environment):
    env = environment
    env.welcome_channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
    state, entry = approved_welcome(env)
    run(transfer._deliver_welcome(env.guild, state, entry))
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["status"] == "approved" and entry["welcome_status"] == "pending"
    env.recipient.send.assert_awaited_once()
    assert entry["welcome_dm_sent"] is True
    env.welcome_channel.send.side_effect = None
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["welcome_status"] == "sent"
    assert "welcome_error" not in entry
    env.member.add_roles.assert_not_awaited()


def test_error_dm_failure_does_not_fail_or_repeat_transfer(environment):
    env = environment
    env.core["KT_ROLE_CHANNEL_MAP"] = {}
    env.guild.active_threads = AsyncMock(return_value=[])
    env.recipient.send.side_effect = RuntimeError("DM unavailable")
    state, entry = approved_welcome(env)
    run(transfer._deliver_welcome(env.guild, state, entry))
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["status"] == "approved" and entry["welcome_dm_sent"] is False
    env.recipient.send.assert_awaited_once()
    env.member.add_roles.assert_not_awaited()


def test_sent_welcome_is_not_repeated_after_restart(environment, monkeypatch):
    env = environment
    entry = seed(env)
    approve_request(env, entry)
    restored = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored)
    run(transfer.register_persistent_views())
    env.welcome_channel.send.assert_awaited_once()
    env.member.add_roles.assert_awaited_once()


def test_uncertain_send_recovers_by_dossier_without_resending(environment):
    env = environment
    state, entry = approved_welcome(env)
    env.welcome_channel.send.side_effect = TimeoutError("Unknown outcome")
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["welcome_status"] == "uncertain"
    message = SimpleNamespace(id=902, author=env.guild.me, embeds=[transfer._build_welcome_embed(entry, env.roles[40])])

    async def history(**kwargs):
        yield message

    env.welcome_channel.history = history
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["welcome_status"] == "sent" and entry["welcome_message_id"] == 902
    env.welcome_channel.send.assert_awaited_once()


def test_uncertain_send_without_matching_message_does_not_resend(environment):
    env = environment
    state, entry = approved_welcome(env)
    env.welcome_channel.send.side_effect = TimeoutError("Unknown outcome")
    run(transfer._deliver_welcome(env.guild, state, entry))

    async def history(**kwargs):
        if False:
            yield None

    env.welcome_channel.history = history
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["welcome_status"] == "uncertain"
    env.welcome_channel.send.assert_awaited_once()
    assert env.recipient.send.await_count == 2


def test_historical_approval_does_not_get_backfilled(environment):
    env = environment
    state, entry = approved_welcome(env)
    entry.pop("welcome_status")
    run(transfer._deliver_welcome(env.guild, state, entry))
    env.welcome_channel.send.assert_not_awaited()


@pytest.mark.parametrize("status", ["pending", "denied", "failed", "stale", "superseded"])
def test_nonapproved_petitions_never_send_welcome(environment, status):
    env = environment
    state, entry = approved_welcome(env)
    entry["status"] = status
    run(transfer._deliver_welcome(env.guild, state, entry))
    env.welcome_channel.send.assert_not_awaited()
    env.recipient.send.assert_not_awaited()


def test_delayed_welcome_skips_changed_assignment(environment):
    env = environment
    state, entry = approved_welcome(env)
    env.member.roles = [env.roles[index] for index in (1, 10, 30, 50, 70)]
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["status"] == "approved" and entry["welcome_status"] == "skipped"
    env.welcome_channel.send.assert_not_awaited()
    env.recipient.send.assert_awaited_once()


def test_recovered_approval_sends_one_welcome(environment, monkeypatch):
    env = environment
    state, entry = approved_welcome(env)
    entry.update(status="processing", welcome_status="not_due", target_assignment_ids=[20, 40])
    transfer._save_state(state)
    restored = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored)
    run(transfer.register_persistent_views())
    run(transfer.register_persistent_views())
    assert transfer._load_state()["entries"][entry["request_id"]]["welcome_status"] == "sent"
    env.welcome_channel.send.assert_awaited_once()
    env.member.add_roles.assert_not_awaited()


def test_petition_refresh_failure_does_not_suppress_welcome(environment):
    env = environment
    entry = seed(env)
    env.message.edit.side_effect = RuntimeError("Petition refresh unavailable")
    approve_request(env, entry)
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "approved" and record["refresh_pending"] is True
    assert record["welcome_status"] == "sent"
    env.welcome_channel.send.assert_awaited_once()


def test_lost_role_update_response_recovers_and_sends_welcome(environment):
    env = environment
    entry = seed(env)
    remove_roles = env.member.remove_roles.side_effect

    async def lost_response(*roles, **kwargs):
        await remove_roles(*roles, **kwargs)
        raise TimeoutError("Response lost after role removal")

    env.member.remove_roles.side_effect = lost_response
    approve_request(env, entry)
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "approved" and record["welcome_status"] == "sent"
    env.welcome_channel.send.assert_awaited_once()
    env.member.add_roles.assert_awaited_once()
    env.member.remove_roles.assert_awaited_once()


def test_uncertain_reconciliation_ignores_other_authors(environment):
    env = environment
    state, entry = approved_welcome(env)
    entry.update(welcome_status="sending", welcome_channel_id=901, welcome_started_at=transfer._now())
    foreign = SimpleNamespace(id=902, author=SimpleNamespace(id=123),
                              embeds=[transfer._build_welcome_embed(entry, env.roles[40])])

    async def history(**kwargs):
        yield foreign

    env.welcome_channel.history = history
    run(transfer._deliver_welcome(env.guild, state, entry))
    assert entry["welcome_status"] == "uncertain"
    env.welcome_channel.send.assert_not_awaited()
    env.recipient.send.assert_awaited_once()


@pytest.mark.parametrize("name,allowed", [("Watch Sergeant", False), ("Veteran Sergeant", True),
                                         ("Watch Lieutenant", True), ("Watch Captain", True),
                                         ("Watch Master", True), ("Forgemaster", False)])
def test_staff_threshold(environment, name, allowed):
    environment.actor.roles = [SimpleNamespace(name=name)]
    assert transfer._is_staff(environment.actor) is allowed


@pytest.mark.parametrize("debug,admin,allowed", [
    (True, True, True),
    (True, False, False),
    (False, True, False),
])
def test_debug_admin_override_without_staff_rank(environment, debug, admin, allowed):
    env = environment
    env.actor.roles = [SimpleNamespace(name="Forgemaster")]
    env.core["DEBUG_MODE"] = debug
    env.core["check_command_permission"] = lambda member, command: admin if debug else True
    assert transfer._is_staff(env.actor) is allowed


def test_debug_admin_can_initiate_petition_without_changing_roles(environment):
    env = environment
    env.actor.roles = [SimpleNamespace(name="Forgemaster")]
    env.core["DEBUG_MODE"] = True
    env.core["check_command_permission"] = lambda member, command: member.id == env.actor.id
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    entry = next(iter(transfer._load_state()["entries"].values()))
    assert entry["status"] == "pending"
    assert entry["initiator_id"] == env.actor.id
    env.member.edit.assert_not_awaited()


@pytest.mark.parametrize("debug,admin,submitted", [
    (True, True, True),
    (True, False, False),
    (False, True, False),
])
def test_forgemaster_request_preview_is_debug_admin_only(environment, debug, admin, submitted):
    env = environment
    env.roles[50].name = "Forgemaster"
    env.core["RANK_ROLES_PRIORITY"].insert(0, "Forgemaster")
    env.core["DEBUG_MODE"] = debug
    env.core["check_command_permission"] = lambda member, command: admin if debug else True
    env.guild.owner = env.member
    run(transfer.request_transfer(interaction(env, env.member), "20", "40"))
    assert env.channel.send.await_count == int(submitted)
    env.member.edit.assert_not_awaited()


def test_debug_preview_does_not_bypass_approval_rank_safety(environment):
    env = environment
    env.roles[50].name = "Forgemaster"
    env.core["RANK_ROLES_PRIORITY"].insert(0, "Forgemaster")
    env.core["DEBUG_MODE"] = True
    run(transfer.request_transfer(interaction(env, env.member), "20", "40"))
    entry = next(iter(transfer._load_state()["entries"].values()))

    async def approve():
        await transfer.TransferRequestView(entry["request_id"]).approve.callback(interaction(env))

    run(approve())
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "pending"
    env.member.edit.assert_not_awaited()


def test_debug_preview_still_rejects_loa(environment):
    env = environment
    env.core["DEBUG_MODE"] = True
    env.member.roles.append(SimpleNamespace(id=transfer.LOA_ROLE_ID, name="LOA"))
    run(transfer.request_transfer(interaction(env, env.member), "20", "40"))
    env.channel.send.assert_not_awaited()


def test_corrupt_state_is_not_overwritten(environment):
    with open(transfer.TRANSFER_REQUESTS_PATH, "w") as handle:
        json.dump({"entries": []}, handle)
    with pytest.raises(ValueError):
        transfer._load_state()
    with open(transfer.TRANSFER_REQUESTS_PATH) as handle:
        assert json.load(handle) == {"entries": []}


def test_known_mismatched_company_rejected(environment):
    environment.roles[40].members = [environment.member]
    with pytest.raises(ValueError, match="another company"):
        transfer._validate_destination(environment.guild, 20, 40)


def test_approve_updates_requester_not_reviewer(environment):
    env = environment
    entry = seed(env)

    async def approve():
        view = transfer.TransferRequestView(entry["request_id"])
        assert view.is_persistent()
        await view.approve.callback(interaction(env))

    run(approve())
    env.member.edit.assert_not_awaited()
    assert {role.id for role in env.member.add_roles.await_args.args} == {20, 40}
    assert {role.id for role in env.member.remove_roles.await_args.args} == {10, 30}
    assert env.member.add_roles.await_args.kwargs["atomic"] is True
    assert env.member.remove_roles.await_args.kwargs["atomic"] is True
    assert {role.id for role in env.actor.roles} == {60}
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    assert resolved["status"] == "approved"
    assert resolved["reviewer_id"] == env.actor.id
    env.message.edit.assert_awaited_once()
    assert env.message.edit.await_args.kwargs["view"] is None


def test_request_posts_embed_and_narrow_role_ping(environment):
    env = environment
    run(transfer.request_transfer(interaction(env, env.member), "20", "40"))
    kwargs = env.channel.send.await_args.kwargs
    assert isinstance(kwargs["embed"], discord.Embed)
    assert kwargs["content"] == f"<@&{transfer.VETERAN_SERGEANT_ROLE_ID}>"
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [transfer.VETERAN_SERGEANT_ROLE_ID]
    assert kwargs["allowed_mentions"].users is False
    assert kwargs["embed"].title == "Reassignment Petition | Awaiting Ruling"
    assert [field.name for field in kwargs["embed"].fields] == ["Brother", "From", "To"]
    assert len(kwargs["view"].children) == 2
    assert next(iter(transfer._load_state()["entries"].values()))["status"] == "pending"


def test_initiate_reuses_matching_pending_petition(environment):
    env = environment
    entry = seed(env)
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "pending"
    env.channel.send.assert_not_awaited()
    env.member.edit.assert_not_awaited()
    env.message.edit.assert_not_awaited()


def test_unauthorized_deny_does_not_open_modal(environment):
    env = environment
    entry = seed(env)
    env.actor.roles = [SimpleNamespace(name="Watch Sergeant")]
    request = interaction(env)

    async def deny():
        await transfer.TransferRequestView(entry["request_id"]).deny.callback(request)

    run(deny())
    request.response.send_modal.assert_not_awaited()
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "pending"


@pytest.mark.parametrize("reason", ["", "   ", "Insufficient staffing"])
def test_denial_requires_and_displays_reason(environment, reason):
    env = environment
    entry = seed(env)
    request = interaction(env)

    async def submit():
        modal = transfer.TransferDenialReasonModal(entry["request_id"], env.message.id)
        assert modal.reason.required
        modal.reason._value = reason
        await modal.on_submit(request)

    run(submit())
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    env.member.edit.assert_not_awaited()
    if not reason.strip():
        assert resolved["status"] == "pending"
        env.message.edit.assert_not_awaited()
    else:
        assert resolved["status"] == "denied"
        assert resolved["reason"] == reason
        kwargs = env.message.edit.await_args.kwargs
        assert kwargs["view"] is None
        assert next(field.value for field in kwargs["embed"].fields if field.name == "Grounds for Denial") == reason


def test_sanctioned_embed_is_compact_without_duplicate_destination(environment):
    env = environment
    entry = seed(env)
    entry["source_role_ids"].append(11)
    transfer._resolve(entry, "approved", env.actor.id, actual_company_id=20, actual_kt_id=40)
    embed = transfer._build_embed(entry)
    assert embed.title == "Reassignment Petition | Sanctioned"
    assert [field.name for field in embed.fields] == ["Brother", "From", "To", "Sanctioned by"]
    assert "<@&11>" not in embed.fields[1].value
    assert embed.timestamp is not None
    assert embed.description is None


def test_authorized_deny_opens_modal_without_resolving(environment):
    env = environment
    entry = seed(env)
    request = interaction(env)

    async def deny():
        await transfer.TransferRequestView(entry["request_id"]).deny.callback(request)

    run(deny())
    request.response.defer.assert_not_awaited()
    modal = request.response.send_modal.await_args.args[0]
    assert modal.reason.required
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "pending"


def test_permission_lost_before_denial_submission(environment):
    env = environment
    entry = seed(env)

    async def submit():
        modal = transfer.TransferDenialReasonModal(entry["request_id"], env.message.id)
        modal.reason._value = "Reason"
        env.actor.roles = [SimpleNamespace(name="Watch Sergeant")]
        await modal.on_submit(interaction(env))

    run(submit())
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "pending"
    env.message.edit.assert_not_awaited()


def test_concurrent_reviews_only_resolve_once(environment):
    env = environment
    entry = seed(env)

    async def reviews():
        view = transfer.TransferRequestView(entry["request_id"])
        modal = transfer.TransferDenialReasonModal(entry["request_id"], env.message.id)
        modal.reason._value = "Denied"
        await asyncio.gather(view.approve.callback(interaction(env)), modal.on_submit(interaction(env)))

    run(reviews())
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    assert resolved["status"] in {"approved", "denied"}
    assert env.member.add_roles.await_count == (1 if resolved["status"] == "approved" else 0)
    env.message.edit.assert_awaited_once()


def test_duplicate_requests_rejected(environment):
    env = environment
    seed(env)
    run(transfer.request_transfer(interaction(env, env.member), "20", "40"))
    env.channel.send.assert_not_awaited()
    assert len(transfer._load_state()["entries"]) == 1


def test_stale_request_is_resolved_without_changing_roles(environment):
    env = environment
    entry = seed(env)
    env.member.roles.remove(env.roles[30])
    approve_request(env, entry)
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "stale"
    env.member.edit.assert_not_awaited()
    assert env.message.edit.await_args.kwargs["view"] is None


def test_initiate_posts_reviewable_petition_and_only_approval_changes_roles(environment):
    env = environment
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    env.member.edit.assert_not_awaited()
    entry = next(iter(transfer._load_state()["entries"].values()))
    assert entry["direct"] is False
    assert entry["status"] == "pending"
    assert env.channel.send.await_args.kwargs["content"] == f"<@&{transfer.VETERAN_SERGEANT_ROLE_ID}>"
    assert len(env.channel.send.await_args.kwargs["view"].children) == 2
    embed = env.channel.send.await_args.kwargs["embed"]
    assert embed.title == "Reassignment Petition | Awaiting Ruling"
    assert next(field.value for field in embed.fields if field.name == "Brother") == f"<@{env.member.id}>"
    assert next(field.value for field in embed.fields if field.name == "Petitioned by") == f"<@{env.actor.id}>"
    approve_request(env, entry)
    env.member.edit.assert_not_awaited()
    env.member.add_roles.assert_awaited_once()
    env.member.remove_roles.assert_awaited_once()
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "approved"
    assert env.message.edit.await_args.kwargs["view"] is None


def test_initiate_rejects_conflicting_pending_petition(environment):
    env = environment
    entry = seed(env)
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "30"))
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    assert resolved["status"] == "pending"
    assert resolved["kt_id"] == 40
    env.channel.send.assert_not_awaited()
    env.member.edit.assert_not_awaited()
    env.message.edit.assert_not_awaited()


@pytest.mark.parametrize("role_id", [transfer.RESERVES_ROLE_ID, transfer.LOA_ROLE_ID])
def test_inactive_members_rejected(environment, role_id):
    env = environment
    env.member.roles.append(SimpleNamespace(id=role_id, name="Status"))
    with pytest.raises(ValueError):
        transfer._target_roles(env.member, 20, 40)


def test_active_loa_record_rejected(environment, monkeypatch):
    from opscribe import loa_ops
    monkeypatch.setattr(loa_ops, "_get_active_loa", lambda user_id: {"active": True})
    with pytest.raises(ValueError, match="LOA"):
        transfer._target_roles(environment.member, 20, 40)


def test_company_command_role_moves_with_captain(environment):
    env = environment
    env.member.roles.extend([env.roles[11], SimpleNamespace(id=80, name="Watch Captain", is_default=lambda: False)])
    assert {role.id for role in transfer._target_roles(env.member, 20, 40)} == {20, 21, 40, 50, 70, 80}


def test_kill_team_champion_marker_is_not_removed(environment):
    env = environment
    marker = SimpleNamespace(id=80, name="Kill Team Champion", is_default=lambda: False)
    env.member.roles.append(marker)
    assert 80 in {role.id for role in transfer._target_roles(env.member, 20, 40)}


def test_role_error_leaves_request_retryable(environment):
    env = environment
    entry = seed(env)
    env.member.add_roles.side_effect = RuntimeError("Role update unavailable")
    approve_request(env, entry)
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    assert resolved["status"] == "pending"
    assert "error" in resolved


def test_message_edit_failure_recovers_without_repeating_transfer(environment, monkeypatch):
    env = environment
    entry = seed(env)
    env.message.edit.side_effect = RuntimeError("Message unavailable")
    approve_request(env, entry)
    assert transfer._load_state()["entries"][entry["request_id"]]["refresh_pending"]
    env.message.edit.side_effect = None
    restored_bot = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored_bot)
    run(transfer.register_persistent_views())
    env.member.edit.assert_not_awaited()
    env.member.add_roles.assert_awaited_once()
    env.member.remove_roles.assert_awaited_once()
    assert not transfer._load_state()["entries"][entry["request_id"]]["refresh_pending"]
    restored_bot.add_view.assert_not_called()


def test_restart_restores_pending_view(environment, monkeypatch):
    env = environment
    entry = seed(env)
    restored_bot = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored_bot)
    run(transfer.register_persistent_views())
    restored = restored_bot.add_view.call_args
    assert restored.kwargs["message_id"] == env.message.id
    assert restored.args[0].is_persistent()
    assert restored.args[0].request_id == entry["request_id"]


def test_restart_confirms_interrupted_transfer_with_unrelated_role_change(environment, monkeypatch):
    env = environment
    entry = seed(env)
    entry.update(status="processing", reviewer_id=env.actor.id, actual_company_id=20, actual_kt_id=40,
                 target_assignment_ids=[20, 40])
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    env.member.roles = [env.roles[index] for index in (1, 20, 40, 50)]
    monkeypatch.setattr(_g, "bot", SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock()))
    run(transfer.register_persistent_views())
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "approved"
    env.member.edit.assert_not_awaited()


@pytest.mark.parametrize("company,kt", [("bad", "40"), ("20", "bad"), ("<@&20", "40")])
def test_invalid_arguments_rejected(environment, company, kt):
    with pytest.raises(ValueError):
        transfer._destination_ids(company, kt)


def test_missing_roles_and_noop_rejected(environment):
    env = environment
    with pytest.raises(ValueError):
        transfer._validate_destination(env.guild, 999, 40)
    with pytest.raises(ValueError):
        transfer._validate_destination(env.guild, 20, 999)
    with pytest.raises(ValueError, match="already holds"):
        transfer._target_roles(env.member, 10, 30)


def test_bot_hierarchy_and_manage_roles_checked(environment):
    env = environment
    env.guild.me.guild_permissions.manage_roles = False
    with pytest.raises(ValueError, match="Manage Roles"):
        transfer._target_roles(env.member, 20, 40)
    env.guild.me.guild_permissions.manage_roles = True
    env.guild.me.top_role.position = 3
    with pytest.raises(ValueError, match="hierarchy"):
        transfer._target_roles(env.member, 20, 40)


def test_approval_preserves_concurrent_unrelated_role_changes(environment):
    env = environment
    entry = seed(env)
    add_roles = env.member.add_roles.side_effect
    award = SimpleNamespace(id=80, name="New award")

    async def concurrent_update(*roles, **kwargs):
        env.member.roles.append(award)
        env.member.roles.remove(env.roles[70])
        await add_roles(*roles, **kwargs)

    env.member.add_roles.side_effect = concurrent_update
    approve_request(env, entry)
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "approved"
    assert {role.id for role in env.member.roles} == {1, 20, 40, 50, 80}
    env.member.edit.assert_not_awaited()


@pytest.mark.parametrize("action", ["approve", "deny", "initiate"])
def test_staff_rank_loss_while_queued_prevents_action(environment, monkeypatch, action):
    env = environment
    entry = seed(env)
    original = transfer._authorize

    async def queued_action():
        authorized = asyncio.Event()

        async def track_authorization(*args, **kwargs):
            member = await original(*args, **kwargs)
            authorized.set()
            return member

        monkeypatch.setattr(transfer, "_authorize", track_authorization)
        request = interaction(env)
        if action == "approve":
            callback = transfer.TransferRequestView(entry["request_id"]).approve.callback(request)
        elif action == "deny":
            modal = transfer.TransferDenialReasonModal(entry["request_id"], env.message.id)
            modal.reason._value = "Staffing"
            callback = modal.on_submit(request)
        else:
            callback = transfer.initiate_transfer(request, env.member, "20", "40")
        async with transfer._TRANSFER_LOCK:
            task = asyncio.create_task(callback)
            await authorized.wait()
            env.actor.roles = [SimpleNamespace(name="Watch Brother")]
        await task

    run(queued_action())
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "pending"
    env.member.add_roles.assert_not_awaited()
    env.member.remove_roles.assert_not_awaited()
    env.channel.send.assert_not_awaited()
    env.message.edit.assert_not_awaited()


def test_partial_assignment_failure_requires_review_without_replay(environment, monkeypatch):
    env = environment
    entry = seed(env)
    env.member.remove_roles.side_effect = RuntimeError("Removal unavailable")
    approve_request(env, entry)
    failed = transfer._load_state()["entries"][entry["request_id"]]
    assert failed["status"] == "failed"
    assert "staff review" in failed["error"]
    assert {role.id for role in env.member.roles} == {1, 10, 20, 30, 40, 50, 70}
    monkeypatch.setattr(_g, "bot", SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock()))
    run(transfer.register_persistent_views())
    env.member.add_roles.assert_awaited_once()
    env.member.remove_roles.assert_awaited_once()
    env.member.edit.assert_not_awaited()


def test_restart_closes_processing_request_for_departed_brother(environment, monkeypatch):
    env = environment
    entry = seed(env)
    entry.update(status="processing", reviewer_id=env.actor.id, actual_company_id=20, actual_kt_id=40,
                 target_assignment_ids=[20, 40])
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    env.guild.fetch_member.side_effect = discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), {"code": 10007, "message": "Unknown Member"})
    restored = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored)
    run(transfer.register_persistent_views())
    state = transfer._load_state()
    assert state["entries"][entry["request_id"]]["status"] == "failed"
    assert "left the Watch Fortress" in state["entries"][entry["request_id"]]["error"]
    assert transfer._pending_for(state, env.guild.id, env.member.id) is None
    assert env.message.edit.await_args.kwargs["view"] is None
    restored.add_view.assert_not_called()
    env.member.add_roles.assert_not_awaited()
    env.member.remove_roles.assert_not_awaited()


def test_approval_closes_pending_request_for_departed_brother(environment):
    env = environment
    entry = seed(env)

    async def fetch_member(member_id):
        if member_id == env.member.id:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
        return env.actor

    env.guild.fetch_member.side_effect = fetch_member
    approve_request(env, entry)
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "failed"
    assert env.message.edit.await_args.kwargs["view"] is None
    env.member.add_roles.assert_not_awaited()
    env.member.remove_roles.assert_not_awaited()


def test_transient_recovery_failure_keeps_processing_state(environment, monkeypatch):
    env = environment
    entry = seed(env)
    entry.update(status="processing", reviewer_id=env.actor.id, actual_company_id=20, actual_kt_id=40,
                 target_assignment_ids=[20, 40])
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    env.guild.fetch_member.side_effect = RuntimeError("Temporary network failure")
    monkeypatch.setattr(_g, "bot", SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock()))
    run(transfer.register_persistent_views())
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "processing"
    env.message.edit.assert_not_awaited()


def test_legacy_pending_direct_record_restores_as_reviewable_petition(environment, monkeypatch):
    env = environment
    entry = seed(env)
    entry["direct"] = True
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    restored = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored)
    run(transfer.register_persistent_views())
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "pending" and record["direct"] is False
    assert env.message.edit.await_args.kwargs["view"].is_persistent()
    assert env.message.edit.await_args.kwargs["embed"].title == "Reassignment Petition | Awaiting Ruling"
    restored.add_view.assert_called_once()
    env.member.add_roles.assert_not_awaited()
    env.member.remove_roles.assert_not_awaited()


@pytest.mark.parametrize("role_id,role_name", [
    (transfer.RESERVES_ROLE_ID, "Reserves"),
    (transfer.LOA_ROLE_ID, "LOA"),
])
def test_inflight_status_change_fails_approval(environment, role_id, role_name):
    env = environment
    entry = seed(env)
    add_roles = env.member.add_roles.side_effect

    async def status_change(*roles, **kwargs):
        env.member.roles.append(SimpleNamespace(id=role_id, name=role_name))
        await add_roles(*roles, **kwargs)

    env.member.add_roles.side_effect = status_change
    approve_request(env, entry)
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "failed"
    assert "Eligibility changed" in record["error"]
    assert "Staff review" in record["error"]
    assert role_id in {role.id for role in env.member.roles}
    assert env.message.edit.await_args.kwargs["view"] is None
    assert "Requires Review" in env.message.edit.await_args.kwargs["embed"].title


def test_inflight_loa_record_change_fails_approval(environment, monkeypatch):
    from opscribe import loa_ops

    env = environment
    entry = seed(env)
    active_loa = {}
    monkeypatch.setattr(loa_ops, "_get_active_loa", lambda user_id: active_loa.get(user_id))
    remove_roles = env.member.remove_roles.side_effect

    async def begin_loa(*roles, **kwargs):
        active_loa[env.member.id] = {"active": True}
        await remove_roles(*roles, **kwargs)

    env.member.remove_roles.side_effect = begin_loa
    approve_request(env, entry)
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "failed"
    assert "LOA" in record["error"]
    assert env.message.edit.await_args.kwargs["view"] is None


@pytest.mark.parametrize("role_id,role_name", [
    (transfer.RESERVES_ROLE_ID, "Reserves"),
    (transfer.LOA_ROLE_ID, "LOA"),
])
@pytest.mark.parametrize("assignment", ["source", "target"])
def test_recovery_rejects_ineligible_brother(environment, monkeypatch, role_id, role_name, assignment):
    env = environment
    entry = seed(env)
    entry.update(status="processing", reviewer_id=env.actor.id, actual_company_id=20, actual_kt_id=40,
                 target_assignment_ids=[20, 40])
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    if assignment == "target":
        env.member.roles = [env.roles[index] for index in (1, 20, 40, 50, 70)]
    env.member.roles.append(SimpleNamespace(id=role_id, name=role_name))
    restored = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored)
    run(transfer.register_persistent_views())
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "failed"
    assert "Eligibility changed" in record["error"]
    assert env.message.edit.await_args.kwargs["view"] is None
    restored.add_view.assert_not_called()
    env.member.add_roles.assert_not_awaited()
    env.member.remove_roles.assert_not_awaited()


def test_recovery_rejects_active_loa_record_without_loa_role(environment, monkeypatch):
    from opscribe import loa_ops

    env = environment
    entry = seed(env)
    entry.update(status="processing", reviewer_id=env.actor.id, actual_company_id=20, actual_kt_id=40,
                 target_assignment_ids=[20, 40])
    transfer._save_state({"entries": {entry["request_id"]: entry}})
    env.member.roles = [env.roles[index] for index in (1, 20, 40, 50, 70)]
    monkeypatch.setattr(loa_ops, "_get_active_loa", lambda user_id: {"active": True})
    monkeypatch.setattr(_g, "bot", SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock()))
    run(transfer.register_persistent_views())
    record = transfer._load_state()["entries"][entry["request_id"]]
    assert record["status"] == "failed"
    assert "LOA" in record["error"]
    assert env.message.edit.await_args.kwargs["view"] is None