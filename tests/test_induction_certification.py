import importlib
import sys
import types
import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from opscribe import _bot_globals as _g


class _FakeTree:
    def command(self, **_kwargs):
        return lambda callback: callback


_bot_stub = types.ModuleType("opscribe.bot")
_bot_stub.bot = SimpleNamespace(tree=_FakeTree())
_bot_stub.HOME_CHAPTERS = ["Dark Angels", "Raven Guard"]
_bot_stub.check_command_permission = lambda _member, _command: True
_bot_stub.ALLOWED_KT_ROLE_IDS = {301}
sys.modules["opscribe.bot"] = _bot_stub
_g.bot = _bot_stub.bot

induction = importlib.import_module("opscribe.induction_ops")


class _Guild:
    def __init__(self, roles=(), members=()):
        self.roles = list(roles)
        self.members = {member.id: member for member in members}

    def get_member(self, member_id):
        return self.members.get(member_id)


def test_nickname_preserves_service_marks_and_checks_discord_limit():
    assert induction._nickname_with_stud_suffix("Brother Nyx", "Old Name ●⚬") == "Brother Nyx ●⚬"
    assert induction._nickname_with_stud_suffix("Brother Nyx", "Old Name") == "Brother Nyx"

    with pytest.raises(ValueError, match="32-character"):
        induction._nickname_with_stud_suffix("N" * 32, "Old Name ●")
    with pytest.raises(ValueError, match="cannot be blank"):
        induction._nickname_with_stud_suffix("  ", "Old Name")


def test_chapter_resolution_requires_a_canonical_existing_role():
    chapter_role = SimpleNamespace(id=101, name="Dark Angels")
    guild = _Guild(roles=[chapter_role])

    assert induction._resolve_chapter_role(guild, "dark angels") == ("Dark Angels", chapter_role)

    with pytest.raises(ValueError, match="canonical"):
        induction._resolve_chapter_role(guild, "Unknown Chapter")
    with pytest.raises(ValueError, match="does not exist"):
        induction._resolve_chapter_role(_Guild(), "Dark Angels")


def test_certify_induction_persists_and_posts_certification(monkeypatch, tmp_path):
    chapter_role = SimpleNamespace(id=101, name="Dark Angels")
    guild = _Guild(roles=[chapter_role])
    guild.id = 1
    guild.fetch_member = AsyncMock(return_value=SimpleNamespace(id=55, roles=[]))
    certifier = SimpleNamespace(id=55)
    guild.fetch_member.return_value = certifier
    inductee = SimpleNamespace(
        id=44,
        guild=guild,
        bot=False,
        nick="Old Name ●",
        display_name="Old Name ●",
        roles=[SimpleNamespace(id=56, name="Watch Inductee")],
    )
    channel = SimpleNamespace(
        send=AsyncMock(return_value=SimpleNamespace(id=700, jump_url="https://discord.com/channels/1/2/700"))
    )
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_resolve_text_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(_bot_stub, "check_command_permission", lambda _actor, command: command == "certify_induction")
    monkeypatch.setattr(_bot_stub, "is_allowed_channel", lambda _interaction: True, raising=False)
    response = SimpleNamespace(defer=AsyncMock(), is_done=lambda: True)
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=certifier,
        channel=SimpleNamespace(id=808),
        response=response,
        followup=followup,
    )

    _run(induction.certify_induction(interaction, inductee, "Brother Nyx", "dark angels"))

    state = induction._load_state()
    entry = next(iter(state["entries"].values()))
    assert entry["status"] == "pending"
    assert entry["certifier_id"] == certifier.id
    assert entry["desired_nickname"] == "Brother Nyx ●"
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["embed"].title == "///INDUCTION COMPLETE"
    assert "https://discord.com/channels/1/2/700" in followup.send.await_args.args[0]


def test_certify_induction_rejects_member_without_watch_inductee_role(monkeypatch, tmp_path):
    guild = _Guild()
    guild.id = 1
    certifier = SimpleNamespace(id=55)
    inductee = SimpleNamespace(
        id=44,
        guild=guild,
        bot=False,
        nick="Old Name",
        display_name="Old Name",
        roles=[SimpleNamespace(id=57, name="Watch Brother")],
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=certifier,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_authorize_command", AsyncMock(return_value=certifier))
    monkeypatch.setattr(induction._g, "logger", MagicMock())

    _run(induction.certify_induction(interaction, inductee, "Brother Nyx", "Dark Angels"))

    assert induction._load_state() == {"entries": {}}
    response.send_message.assert_awaited_once()
    assert "Watch Inductee role" in response.send_message.await_args.args[0]


def test_certification_publish_preserves_record_added_during_send(monkeypatch, tmp_path):
    guild = _Guild()
    guild.id = 1
    guild.get_member = lambda _member_id: None
    message = SimpleNamespace(id=700, jump_url="https://discord.com/channels/1/2/700")
    entry = {
        "request_id": "publish01",
        "guild_id": guild.id,
        "member_id": 44,
        "certifier_id": 55,
        "chosen_name": "Nyx",
        "chapter_name": "Dark Angels",
        "status": "certification_publish_pending",
        "certification_channel_id": 123,
    }

    async def send_while_another_record_is_added(**_kwargs):
        state = induction._load_state()
        state["entries"]["concurrent"] = {"request_id": "concurrent", "status": "pending"}
        induction._save_state(state)
        return message

    channel = SimpleNamespace(send=AsyncMock(side_effect=send_while_another_record_is_added))
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_resolve_text_channel", AsyncMock(return_value=channel))
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._publish_certification(guild, entry))

    state = induction._load_state()
    assert state["entries"][entry["request_id"]]["status"] == "pending"
    assert state["entries"]["concurrent"]["request_id"] == "concurrent"


def test_embeds_render_requested_induction_and_kill_team_welcome_content():
    member = SimpleNamespace(id=44, display_name="Brother Nyx")
    guild = _Guild(members=[member])
    record = {
        "request_id": "abc123",
        "member_id": member.id,
        "certifier_id": 55,
        "chosen_name": "Nyx",
        "chapter_name": "Dark Angels",
        "induction_approver_id": 56,
        "company_id": None,
        "kt_id": None,
    }

    certification = induction._certification_embed(record, guild)
    assert certification.title == "///INDUCTION COMPLETE"
    assert {field.name: field.value for field in certification.fields} == {
        "Inductee": "Brother Nyx",
        "Certified By": "<@55>",
        "Chosen Name": "Nyx",
        "Chosen Chapter": "Dark Angels",
    }

    assignment = induction._assignment_embed(record)
    assert assignment.title == "///WATCH BROTHER READY FOR ASSIGNMENT"
    assert {field.name: field.value for field in assignment.fields}["Induction Approved By"] == "<@56>"

    welcome = induction._welcome_embed(record, SimpleNamespace(name="Kill Team Alpha"))
    assert welcome.title == "`ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴋɪʟʟ-ᴛᴇᴀᴍ ALPHA`"
    assert "ask questions freely" in welcome.description.casefold()
    assert "https://jericho-strategium.duckdns.org/" in welcome.description
    assert "First Legion" in welcome.fields[0].value
    assert welcome.footer.text == "Induction abc123"


def test_every_canonical_chapter_has_a_distinct_welcome():
    root = Path(__file__).resolve().parents[1]
    module = ast.parse((root / "opscribe/bot.py").read_text(encoding="utf-8"))
    chapters = next(
        ast.literal_eval(statement.value)
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "HOME_CHAPTERS" for target in statement.targets)
    )
    lines = induction.KILL_TEAM_WELCOME_CHAPTER_LINES
    assert set(lines) == set(chapters)
    from opscribe.flavor_text.chapters import _CANONICAL_HOME_CHAPTERS
    assert set(_CANONICAL_HOME_CHAPTERS) == set(chapters)
    assert len(set(lines.values())) == len(chapters)
    for chapter in chapters:
        welcome = induction._welcome_embed(
            {"chosen_name": "Nyx", "chapter_name": chapter, "request_id": "preview"},
            SimpleNamespace(name="Kill Team Alpha"),
        )
        assert lines[chapter] in welcome.fields[0].value
        assert len(welcome.title) <= 256
        assert len(welcome.description) <= 4096
        assert len(welcome.fields[0].value) <= 1024
        assert len(welcome) < 1500


@pytest.mark.parametrize("chapter", [None, "Unknown", "Unrecorded Chapter", "Black Shield", "Consecrators"])
def test_welcome_handles_lineage_and_escapes_untrusted_names(chapter):
    welcome = induction._welcome_embed(
        {"chosen_name": "**Nyx** @everyone <@123456789012345678>", "chapter_name": chapter, "request_id": "safe"},
        SimpleNamespace(name="Kill Team **Alpha** @here"),
    )
    assert "\\*\\*Nyx\\*\\*" in welcome.description
    assert "@everyone" not in welcome.description
    assert "<@123456789012345678>" not in welcome.description
    assert "@HERE" not in welcome.title
    assert welcome.footer.text == "Induction safe"
    if chapter == "Black Shield":
        assert "No account of your past" in welcome.fields[0].value
        assert "Black Shield" not in welcome.fields[0].value
    elif chapter == "Consecrators":
        assert "First Legion" in welcome.fields[0].value
        assert "relics" in welcome.fields[0].value
    else:
        assert "Whatever road brought you" in welcome.fields[0].value


@pytest.mark.parametrize("chapter_name, lore_marker", [("Consecrators", "First Legion"), ("Doom Eagles", "Guilliman")])
def test_new_chapter_reference_has_lore_and_canonical_induction_support(monkeypatch, chapter_name, lore_marker):
    root = Path(__file__).resolve().parents[1]
    reference = json.loads((root / "reference/chapters.json").read_text(encoding="utf-8"))
    chapters = [chapter for chapter in reference["chapters"] if chapter["name"] == chapter_name]
    assert len(chapters) == 1
    assert lore_marker in chapters[0]["lore_summary"]
    assert chapters[0]["tag_weights"] == {}
    monkeypatch.setattr(_bot_stub, "HOME_CHAPTERS", [chapter_name])
    role = SimpleNamespace(id=101, name=chapter_name)
    assert induction._resolve_chapter_role(_Guild(roles=[role]), chapter_name.casefold()) == (chapter_name, role)


def _run(coroutine):
    return asyncio.run(coroutine)


def _member(member_id, roles, guild, nick="Old Name ●"):
    member = SimpleNamespace(
        id=member_id,
        guild=guild,
        bot=False,
        nick=nick,
        display_name=nick,
        roles=list(roles),
    )

    async def add_roles(*added, **_kwargs):
        current_ids = {role.id for role in member.roles}
        member.roles.extend(role for role in added if role.id not in current_ids)

    async def remove_roles(*removed, **_kwargs):
        removed_ids = {role.id for role in removed}
        member.roles = [role for role in member.roles if role.id not in removed_ids]

    async def edit(**kwargs):
        member.nick = kwargs["nick"]

    member.add_roles = AsyncMock(side_effect=add_roles)
    member.remove_roles = AsyncMock(side_effect=remove_roles)
    member.edit = AsyncMock(side_effect=edit)
    return member


def test_induction_approval_adds_roles_replaces_chapter_and_changes_nickname(monkeypatch, tmp_path):
    old_chapter = SimpleNamespace(id=10, name="Raven Guard")
    chosen_chapter = SimpleNamespace(id=11, name="Dark Angels")
    brother_role = SimpleNamespace(id=induction.WATCH_BROTHER_ROLE_ID, name="Watch Brother")
    unrelated_role = SimpleNamespace(id=12, name="Award")
    guild = _Guild(roles=[old_chapter, chosen_chapter, brother_role, unrelated_role])
    member = _member(44, [old_chapter, unrelated_role], guild)
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_role = lambda role_id: next((role for role in guild.roles if role.id == role_id), None)
    entry = {
        "request_id": "approval1",
        "member_id": member.id,
        "chapter_role_id": chosen_chapter.id,
        "chapter_name": chosen_chapter.name,
        "source_chapter_role_ids": [old_chapter.id],
        "source_nickname": member.nick,
        "desired_nickname": "Brother Nyx ●",
        "induction_approver_id": 55,
        "status": "induction_processing",
    }
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_check_role_hierarchy", lambda *_args: None)
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._complete_induction(guild, entry))

    assert {role.id for role in member.roles} == {chosen_chapter.id, brother_role.id, unrelated_role.id}
    assert member.nick == "Brother Nyx ●"
    assert entry["status"] == "assignment_publish_pending"
    member.add_roles.assert_awaited_once()
    member.remove_roles.assert_awaited_once_with(old_chapter, reason="Induction approval1 approved by 55", atomic=True)
    member.edit.assert_awaited_once_with(nick="Brother Nyx ●", reason="Induction approval1 approved by 55")


def test_assignment_authority_requires_captain_or_lieutenant_role_id():
    captain = SimpleNamespace(id=induction.WATCH_CAPTAIN_ROLE_ID)
    lieutenant = SimpleNamespace(id=induction.WATCH_LIEUTENANT_ROLE_ID)
    inductee = SimpleNamespace(id=99)

    assert induction._authorized_for_assignment(SimpleNamespace(roles=[captain]))
    assert induction._authorized_for_assignment(SimpleNamespace(roles=[lieutenant]))
    assert not induction._authorized_for_assignment(SimpleNamespace(roles=[inductee]))


def test_unauthorized_member_cannot_change_assignment_selection(monkeypatch, tmp_path):
    member = SimpleNamespace(id=44, roles=[SimpleNamespace(id=999)])
    guild = SimpleNamespace(id=1, fetch_member=AsyncMock(return_value=member))
    response = SimpleNamespace(
        is_done=lambda: False,
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
    )
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=member.id),
        channel_id=808,
        message=SimpleNamespace(id=909),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    entry = {
        "request_id": "unauth01",
        "guild_id": guild.id,
        "member_id": 100,
        "status": "assignment_pending",
        "assignment_message_id": interaction.message.id,
        "assignment_channel_id": interaction.channel_id,
        "company_id": None,
        "kt_id": None,
    }
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction._g, "logger", MagicMock())
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._handle_assignment_selection(interaction, entry["request_id"], "company", "201"))

    assert induction._load_state()["entries"][entry["request_id"]]["company_id"] is None
    response.send_message.assert_awaited_once()
    assert "Watch Captains or Watch Lieutenants" in response.send_message.await_args.args[0]


def test_startup_restores_pending_approval_and_assignment_views(monkeypatch, tmp_path):
    company = SimpleNamespace(id=201, name="Watch Company Primus")
    command = SimpleNamespace(id=202, name="Primus Command")
    kill_team = SimpleNamespace(id=301, name="Kill Team Alpha")
    guild = _Guild(roles=[company, command, kill_team])
    guild.id = 1
    guild.get_role = lambda role_id: next((role for role in guild.roles if role.id == role_id), None)
    pending_approval = {
        "request_id": "restore01",
        "guild_id": guild.id,
        "member_id": 44,
        "status": "pending",
        "certification_message_id": 701,
    }
    pending_assignment = {
        "request_id": "restore02",
        "guild_id": guild.id,
        "member_id": 45,
        "status": "assignment_pending",
        "assignment_message_id": 702,
        "company_id": company.id,
        "kt_id": kill_team.id,
    }
    bot = SimpleNamespace(
        tree=_FakeTree(),
        get_guild=lambda _guild_id: guild,
        add_view=MagicMock(),
    )
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction._g, "bot", bot)
    monkeypatch.setattr(induction._g, "logger", MagicMock())
    monkeypatch.setattr(induction._g, "CONFIG", {
        "companies": {"primus": {"companyRoleId": company.id, "companyCommandRoleId": command.id}},
    })
    _bot_stub.ALLOWED_KT_ROLE_IDS = {kill_team.id}
    induction._save_state({"entries": {
        pending_approval["request_id"]: pending_approval,
        pending_assignment["request_id"]: pending_assignment,
    }})

    _run(induction.register_persistent_views())

    assert bot.add_view.call_count == 2
    restored = {call.kwargs["message_id"]: type(call.args[0]).__name__ for call in bot.add_view.call_args_list}
    assert restored == {
        pending_approval["certification_message_id"]: "InductionApprovalView",
        pending_assignment["assignment_message_id"]: "InductionAssignmentView",
    }


def test_assign_is_blocked_when_selector_edit_does_not_synchronize(monkeypatch, tmp_path):
    from opscribe import transfer_ops

    company = SimpleNamespace(id=201, name="Watch Company Primus")
    old_kt = SimpleNamespace(id=301, name="Kill Team Alpha")
    selected_kt = SimpleNamespace(id=302, name="Kill Team Beta")
    guild = _Guild(roles=[company, old_kt, selected_kt])
    guild.id = 1
    actor = SimpleNamespace(id=55, roles=[SimpleNamespace(id=induction.WATCH_CAPTAIN_ROLE_ID)])
    guild.fetch_member = AsyncMock(return_value=actor)
    response = SimpleNamespace(
        is_done=lambda: True,
        edit_message=AsyncMock(side_effect=RuntimeError("message edit failed")),
        defer=AsyncMock(),
        send_message=AsyncMock(),
    )
    interaction = SimpleNamespace(
        guild=guild,
        user=actor,
        channel_id=808,
        message=SimpleNamespace(id=909),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    entry = {
        "request_id": "syncfail1",
        "guild_id": guild.id,
        "member_id": 100,
        "status": "assignment_pending",
        "assignment_message_id": interaction.message.id,
        "assignment_channel_id": interaction.channel_id,
        "company_id": company.id,
        "kt_id": old_kt.id,
        "selection_version": 0,
        "selection_sync_pending": False,
    }
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction._g, "logger", MagicMock())
    monkeypatch.setattr(induction, "_kill_team_roles", lambda _guild: [old_kt, selected_kt])
    monkeypatch.setattr(transfer_ops, "_validate_destination", lambda *_args: (company, selected_kt))
    execute = AsyncMock()
    monkeypatch.setattr(induction, "_execute_assignment", execute)
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._handle_assignment_selection(interaction, entry["request_id"], "kt", str(selected_kt.id)))

    stored = induction._load_state()["entries"][entry["request_id"]]
    assert stored["kt_id"] == selected_kt.id
    assert stored["selection_sync_pending"] is True
    _run(induction._handle_assignment(interaction, entry["request_id"]))

    execute.assert_not_awaited()
    response.defer.assert_not_awaited()
    assert induction._load_state()["entries"][entry["request_id"]]["selection_sync_pending"] is True


def test_assignment_applies_company_and_kill_team_then_welcomes_in_selected_chat(monkeypatch, tmp_path):
    from opscribe import transfer_ops

    company = SimpleNamespace(id=201, name="Watch Company Primus")
    command = SimpleNamespace(id=202, name="Primus Command")
    kill_team = SimpleNamespace(id=301, name="Kill Team Alpha")
    unrelated = SimpleNamespace(id=401, name="Award")
    guild = _Guild(roles=[company, command, kill_team, unrelated])
    member = _member(44, [unrelated], guild, nick="Nyx")
    captain = SimpleNamespace(id=55, roles=[SimpleNamespace(id=induction.WATCH_CAPTAIN_ROLE_ID)])
    guild.fetch_member = AsyncMock(side_effect=lambda member_id: captain if member_id == captain.id else member)
    guild.get_role = lambda role_id: next((role for role in guild.roles if role.id == role_id), None)
    welcome_channel = SimpleNamespace(id=909, guild=guild, send=AsyncMock(return_value=SimpleNamespace(id=910)))

    async def resolve_welcome_channel(_guild, _entry, _role):
        return welcome_channel

    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_check_role_hierarchy", lambda *_args: None)
    monkeypatch.setattr(transfer_ops, "_companies", lambda _guild: {company.id: (company, command)})
    monkeypatch.setattr(transfer_ops, "_target_roles", lambda _member, _company_id, _kt_id: [company, kill_team])
    monkeypatch.setattr(
        transfer_ops,
        "_assignment_ids",
        lambda target: sorted(role.id for role in target.roles if role.id in {company.id, command.id, kill_team.id}),
    )
    monkeypatch.setattr(transfer_ops, "_welcome_channel", resolve_welcome_channel)
    monkeypatch.setattr(induction._g, "logger", MagicMock())
    monkeypatch.setattr(induction._g, "CONFIG", {})
    _bot_stub.ALLOWED_KT_ROLE_IDS = {kill_team.id}
    entry = {
        "request_id": "assign01",
        "guild_id": 1,
        "member_id": member.id,
        "chosen_name": "Nyx",
        "induction_approver_id": 54,
        "company_id": company.id,
        "kt_id": kill_team.id,
        "status": "assignment_processing",
        "assigner_id": captain.id,
        "welcome_status": "welcome_pending",
    }
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._execute_assignment(guild, entry))
    _run(induction._deliver_welcome(guild, entry))

    assert entry["status"] == "assigned"
    assert entry["welcome_status"] == "sent"
    assert {field.name: field.value for field in induction._assignment_embed(entry).fields}["Assigned By"] == "<@55>"
    assert {role.id for role in member.roles} == {company.id, kill_team.id, unrelated.id}
    welcome_channel.send.assert_awaited_once()
    sent = welcome_channel.send.await_args.kwargs
    assert sent["embed"].title == "`ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴋɪʟʟ-ᴛᴇᴀᴍ ALPHA`"
    assert sent["content"] == f"<@&{kill_team.id}> <@{member.id}>"


def test_review_record_can_be_closed_and_no_longer_blocks_new_inductions(monkeypatch, tmp_path):
    guild = SimpleNamespace(id=1)
    actor = SimpleNamespace(id=55)
    interaction = SimpleNamespace(
        guild=guild,
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    entry = {"request_id": "review01", "guild_id": guild.id, "status": "review"}
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_authorize_resolution", AsyncMock(return_value=actor))
    monkeypatch.setattr(induction._g, "logger", MagicMock())
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._resolve_review_record(interaction, entry["request_id"], "close"))

    closed = induction._load_state()["entries"][entry["request_id"]]
    assert closed["status"] == "cancelled"
    assert closed["closed_by"] == actor.id
    assert closed["status"] not in induction._OPEN_STATUSES
    assert "does not roll back" in interaction.followup.send.await_args.args[0]


def test_review_record_reconciles_confirmed_induction_and_restores_assignment(monkeypatch, tmp_path):
    guild = SimpleNamespace(id=1)
    actor = SimpleNamespace(id=55)
    member = SimpleNamespace(id=44, roles=[SimpleNamespace(id=10)], nick="Nyx")
    guild.fetch_member = AsyncMock(return_value=member)
    interaction = SimpleNamespace(
        guild=guild,
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    entry = {
        "request_id": "recover01",
        "guild_id": guild.id,
        "member_id": member.id,
        "status": "review",
        "source_role_ids": [1],
        "target_role_ids": [10],
        "source_nickname": "Old Name",
        "desired_nickname": "Nyx",
        "induction_started_at": "2026-10-15T00:00:00+00:00",
    }

    async def publish_assignment(_guild, current):
        current.update(status="assignment_pending", assignment_message_id=702)

    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_authorize_resolution", AsyncMock(return_value=actor))
    monkeypatch.setattr(induction, "_publish_assignment", publish_assignment)
    monkeypatch.setattr(induction, "_refresh_assignment_message", AsyncMock())
    monkeypatch.setattr(induction, "InductionAssignmentView", lambda *_args: "assignment-view")
    monkeypatch.setattr(induction._g, "bot", SimpleNamespace(add_view=MagicMock()))
    monkeypatch.setattr(induction._g, "logger", MagicMock())
    induction._save_state({"entries": {entry["request_id"]: entry}})

    _run(induction._resolve_review_record(interaction, entry["request_id"], "reconcile"))

    reconciled = induction._load_state()["entries"][entry["request_id"]]
    assert reconciled["status"] == "assignment_pending"
    assert reconciled["assignment_message_id"] == 702
    induction._g.bot.add_view.assert_called_once_with("assignment-view", message_id=702)


def test_assignment_refresh_reenables_assign_after_selector_sync(monkeypatch, tmp_path):
    company = SimpleNamespace(id=201, name="Watch Company Primus")
    kill_team = SimpleNamespace(id=301, name="Kill Team Alpha")
    message = SimpleNamespace(edit=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    guild = SimpleNamespace()
    entry = {
        "request_id": "syncok01",
        "guild_id": 1,
        "member_id": 44,
        "status": "assignment_pending",
        "assignment_channel_id": 808,
        "assignment_message_id": 909,
        "company_id": company.id,
        "kt_id": kill_team.id,
        "induction_approver_id": 55,
        "chosen_name": "Nyx",
        "selection_sync_pending": True,
    }
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_resolve_text_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(induction, "_company_roles", lambda _guild: [company])
    monkeypatch.setattr(induction, "_kill_team_roles", lambda _guild: [kill_team])
    induction._save_state({"entries": {entry["request_id"]: entry}})

    assert _run(induction._refresh_assignment_message(guild, entry))

    rendered_view = message.edit.await_args.kwargs["view"]
    assert rendered_view.assign.disabled is False
    assert induction._load_state()["entries"][entry["request_id"]]["selection_sync_pending"] is False


def test_assignment_refresh_retries_when_selection_changes_during_edit(monkeypatch, tmp_path):
    old_company = SimpleNamespace(id=201, name="Watch Company Primus")
    new_company = SimpleNamespace(id=202, name="Watch Company Secundus")
    kill_team = SimpleNamespace(id=301, name="Kill Team Alpha")
    guild = SimpleNamespace()
    entry = {
        "request_id": "syncrace1",
        "guild_id": 1,
        "member_id": 44,
        "status": "assignment_pending",
        "assignment_channel_id": 808,
        "assignment_message_id": 909,
        "company_id": old_company.id,
        "kt_id": kill_team.id,
        "induction_approver_id": 55,
        "chosen_name": "Nyx",
        "selection_version": 1,
        "selection_sync_pending": True,
    }
    edit_count = 0

    async def edit_message(**_kwargs):
        nonlocal edit_count
        edit_count += 1
        if edit_count == 1:
            state = induction._load_state()
            state["entries"][entry["request_id"]].update(
                company_id=new_company.id,
                selection_version=2,
                selection_sync_pending=True,
            )
            induction._save_state(state)

    message = SimpleNamespace(edit=AsyncMock(side_effect=edit_message))
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    monkeypatch.setattr(induction, "INDUCTION_PATH", str(tmp_path / "induction.json"))
    monkeypatch.setattr(induction, "_resolve_text_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(induction, "_company_roles", lambda _guild: [old_company, new_company])
    monkeypatch.setattr(induction, "_kill_team_roles", lambda _guild: [kill_team])
    induction._save_state({"entries": {entry["request_id"]: entry}})

    assert _run(induction._refresh_assignment_message(guild, entry))

    assert message.edit.await_count == 2
    last_embed = message.edit.await_args.kwargs["embed"]
    company_field = next(field for field in last_embed.fields if field.name == "Company")
    assert company_field.value == f"<@&{new_company.id}>"
    assert induction._load_state()["entries"][entry["request_id"]]["selection_sync_pending"] is False