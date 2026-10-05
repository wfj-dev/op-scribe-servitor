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
    guild.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True),
                               top_role=SimpleNamespace(position=100))
    guild.owner = None
    member = SimpleNamespace(id=111, guild=guild, bot=False, roles=[roles[index] for index in (1, 10, 30, 50, 70)],
                              top_role=roles[50])
    actor = SimpleNamespace(id=222, guild=guild, bot=False, roles=[roles[60]], top_role=roles[60])

    async def edit(**kwargs):
        member.roles = [roles[1], *kwargs["roles"]]

    member.edit = AsyncMock(side_effect=edit)
    guild.fetch_member = AsyncMock(side_effect=lambda member_id: member if member_id == 111 else actor)
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild, channel.id = guild, 1459043645499117630
    message = SimpleNamespace(id=900, jump_url="https://discord.com/channels/1/2/900", edit=AsyncMock())
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(return_value=message)
    guild.get_channel.return_value = channel
    core = {"check_command_permission": lambda member, command: True, "is_allowed_channel": lambda interaction: True,
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
                           message=message, core=core)


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


def test_role_update_preserves_unrelated_roles(environment):
    env = environment
    roles = transfer._target_roles(env.member, 20, 40)
    assert {role.id for role in roles} == {20, 40, 50, 70}


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


def test_debug_admin_can_initiate_and_resolve_request(environment):
    env = environment
    entry = seed(env)
    env.actor.roles = [SimpleNamespace(name="Forgemaster")]
    env.core["DEBUG_MODE"] = True
    env.core["check_command_permission"] = lambda member, command: member.id == env.actor.id
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "approved"
    env.member.edit.assert_awaited_once()


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
    env.member.edit.assert_awaited_once()
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


def test_initiate_resolves_original_embed(environment):
    env = environment
    entry = seed(env)
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "approved"
    env.channel.send.assert_not_awaited()
    env.channel.fetch_message.assert_awaited_once_with(env.message.id)
    assert env.message.edit.await_args.kwargs["view"] is None


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
    assert env.member.edit.await_count == (1 if resolved["status"] == "approved" else 0)
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
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    assert transfer._load_state()["entries"][entry["request_id"]]["status"] == "stale"
    env.member.edit.assert_not_awaited()
    assert env.message.edit.await_args.kwargs["view"] is None


def test_direct_transfer_posts_and_resolves_audit_embed(environment):
    env = environment
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    env.member.edit.assert_awaited_once()
    entry = next(iter(transfer._load_state()["entries"].values()))
    assert entry["direct"] is True
    assert entry["status"] == "approved"
    assert env.channel.send.await_args.kwargs["content"] is None
    assert env.channel.send.await_args.kwargs["view"] is None
    assert env.message.edit.await_args.kwargs["view"] is None


def test_different_direct_destination_supersedes_request(environment):
    env = environment
    entry = seed(env)
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "30"))
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    assert resolved["status"] == "superseded"
    assert resolved["actual_kt_id"] == 30
    env.channel.send.assert_not_awaited()
    assert env.message.edit.await_args.kwargs["view"] is None


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
    env.member.edit.side_effect = RuntimeError("Role update unavailable")
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    resolved = transfer._load_state()["entries"][entry["request_id"]]
    assert resolved["status"] == "pending"
    assert "error" in resolved


def test_message_edit_failure_recovers_without_repeating_transfer(environment, monkeypatch):
    env = environment
    entry = seed(env)
    env.message.edit.side_effect = RuntimeError("Message unavailable")
    run(transfer.initiate_transfer(interaction(env), env.member, "20", "40"))
    assert transfer._load_state()["entries"][entry["request_id"]]["refresh_pending"]
    env.message.edit.side_effect = None
    restored_bot = SimpleNamespace(get_guild=lambda guild_id: env.guild, add_view=MagicMock())
    monkeypatch.setattr(_g, "bot", restored_bot)
    run(transfer.register_persistent_views())
    env.member.edit.assert_awaited_once()
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