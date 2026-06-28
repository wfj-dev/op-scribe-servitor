import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


def _install_discord_stub():
    discord_stub = sys.modules.get("discord") or types.ModuleType("discord")

    class _StubEmbed:
        def __init__(self, *, color=None, title=None, description=None):
            self.color = color
            self.title = title
            self.description = description
            self.fields = []
            self.footer = None
            self.image = None

        def add_field(self, *, name, value, inline=True):
            self.fields.append(SimpleNamespace(name=name, value=value, inline=inline))

        def set_footer(self, *, text=None):
            self.footer = SimpleNamespace(text=text)

        def set_image(self, *, url=None):
            self.image = SimpleNamespace(url=url)

    discord_stub.Intents = type("Intents", (), {"default": classmethod(lambda cls: SimpleNamespace(message_content=False, members=False))})
    discord_stub.Client = type("Client", (), {"__init__": lambda self, *args, **kwargs: None})
    discord_stub.Member = object
    discord_stub.User = object
    discord_stub.Guild = object
    discord_stub.Role = object
    discord_stub.Interaction = object
    discord_stub.TextChannel = object
    discord_stub.Thread = type("Thread", (), {})
    discord_stub.ForumChannel = type("ForumChannel", (), {})
    discord_stub.NotFound = Exception
    discord_stub.Forbidden = Exception
    discord_stub.Embed = _StubEmbed
    discord_stub.Color = type("Color", (), {"from_rgb": classmethod(lambda cls, *args, **kwargs: cls())})
    discord_stub.utils = types.SimpleNamespace(get=lambda items, **kwargs: next((item for item in items if all(getattr(item, key, None) == value for key, value in kwargs.items())), None))
    discord_stub.ButtonStyle = types.SimpleNamespace(secondary=2, success=3, danger=4, primary=1)
    discord_stub.abc = types.SimpleNamespace(Messageable=object)
    discord_stub.__getattr__ = lambda name: type(name, (), {})

    app_commands_mod = types.ModuleType("discord.app_commands")
    _fallback_type = type(
        "_FallbackType",
        (),
        {
            "__init__": lambda self, *args, **kwargs: None,
            "__class_getitem__": classmethod(lambda cls, _item: cls),
        },
    )
    app_commands_mod.CommandTree = type("CommandTree", (), {"__init__": lambda self, bot: None})
    app_commands_mod.command = lambda **_kwargs: (lambda func: func)
    app_commands_mod.describe = lambda **_kwargs: (lambda func: func)
    app_commands_mod.choices = lambda **_kwargs: (lambda func: func)
    app_commands_mod.autocomplete = lambda **_kwargs: (lambda func: func)
    app_commands_mod.Choice = _fallback_type
    app_commands_mod.__getattr__ = lambda name: _fallback_type

    ui_mod = types.ModuleType("discord.ui")
    ui_mod.View = type("View", (), {"__init_subclass__": classmethod(lambda cls, **_kwargs: None)})
    ui_mod.Button = object
    ui_mod.Select = object
    ui_mod.UserSelect = object
    ui_mod.RoleSelect = object
    ui_mod.button = lambda **_kwargs: (lambda func: func)
    ui_mod.select = lambda **_kwargs: (lambda func: func)
    discord_stub.ui = ui_mod

    class _LoopStub:
        def __init__(self, func):
            self.func = func
            self.coro = func

        def before_loop(self, _func):
            return _func

        def after_loop(self, _func):
            return _func

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    tasks_mod = types.ModuleType("discord.ext.tasks")
    tasks_mod.loop = lambda **_kwargs: (lambda func: _LoopStub(func))
    ext_mod = types.ModuleType("discord.ext")
    ext_mod.tasks = tasks_mod

    discord_stub.app_commands = app_commands_mod
    discord_stub.ext = ext_mod

    sys.modules["discord"] = discord_stub
    sys.modules["discord.app_commands"] = app_commands_mod
    sys.modules["discord.ui"] = ui_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.tasks"] = tasks_mod


_install_discord_stub()

bot_stub = types.ModuleType("opscribe.bot")
bot_tree_stub = SimpleNamespace(command=lambda **_kwargs: (lambda func: func))
bot_stub.bot = SimpleNamespace(tree=bot_tree_stub)
bot_stub.tree = SimpleNamespace()
bot_stub.CONFIG = {}
bot_stub.DEBUG_MODE = False
bot_stub.ALLOWED_KT_ROLE_IDS = set()
sys.modules["opscribe.bot"] = bot_stub
sys.modules["bot"] = bot_stub

import opscribe._bot_globals as _g
_g.bot = bot_stub.bot

bot = bot_stub
import opscribe.roster_ops as ro


def _run(coro):
    return asyncio.run(coro)


def _make_member(member_id: int, role_names: list[str]):
    roles = [SimpleNamespace(name=name, id=idx + 1000) for idx, name in enumerate(role_names)]
    return SimpleNamespace(id=member_id, display_name=f"Member{member_id}", name=f"Member{member_id}", bot=False, roles=roles)


def test_save_role_integrity_state_creates_parent_directory(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "nested" / "audit" / "state.json"
    monkeypatch.setattr(ro._g, "CONFIG", {"role_integrity_audit": {"state_path": str(state_path)}})

    ro._save_role_integrity_state({"last_run_date": "2026-06-26"})

    assert state_path.exists()
    assert json.loads(state_path.read_text())["last_run_date"] == "2026-06-26"


def test_post_role_integrity_findings_accepts_preformatted_role_mention(monkeypatch):
    sent_messages = []

    class _Channel:
        async def send(self, content=None, embed=None):
            sent_messages.append({"content": content, "embed": embed})

    channel = _Channel()
    guild = SimpleNamespace(get_channel=lambda _cid: channel)
    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {"target_packages": {"highcom_audit_channel_id": "123", "highcom_role_id": "<@&456>"}},
    )

    findings = [{"member_id": 1, "code": "watch_command_missing", "detail": "Expected Watch Command role is missing."}]
    posted = _run(ro._post_role_integrity_findings(guild, findings))

    assert posted is True
    assert sent_messages[0]["content"] == "<@&456>"


def test_collect_role_integrity_findings_reports_track_and_prereq_conflicts(monkeypatch):
    member = _make_member(7, ["Watch Brother", "Watch Captain", "Oathsworn"])
    guild = SimpleNamespace(members=[member], get_role=lambda _rid: None)

    monkeypatch.setattr(ro._g, "CONFIG", {"role_integrity_audit": {}, "companies": {}})
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", set())

    findings = _run(ro._collect_role_integrity_findings(guild))
    codes = {f["code"] for f in findings}

    assert "command_skip" in codes
    assert "oathsworn_skip" in codes
    assert "track_mixing" in codes
    assert "oathsworn_terminal" in codes


def test_collect_role_integrity_findings_treats_huntmaster_as_high_command(monkeypatch):
    member = _make_member(
        8,
        [
            "Watch Brother",
            "Watch Veteran",
            "Deathwatch Specialist",
            "Watch Command",
            "High Command",
            "Huntmaster",
        ],
    )
    guild = SimpleNamespace(members=[member], get_role=lambda _rid: None)

    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {
            "role_integrity_audit": {
                "high_command_required_rank_role_names": [
                    "Watch Captain",
                    "Watch Master",
                    "Forgemaster",
                    "Void Warden",
                    "Chief Apothecary",
                    "High Chaplain",
                    "Lord Executioner",
                    "Venerable Dreadnought",
                    "Huntmaster",
                ],
                "huntmaster_required_rank_role_names": [
                    "Watch Brother",
                    "Watch Veteran",
                    "Deathwatch Specialist",
                    "Watch Command",
                    "High Command",
                    "Huntmaster",
                ]
            },
            "companies": {},
        },
    )
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", set())

    findings = _run(ro._collect_role_integrity_findings(guild))
    codes = {f["code"] for f in findings}

    assert "high_command_excess" not in codes
    assert "high_command_missing" not in codes
    assert "missing_specialist_marker" not in codes
    assert "huntmaster_skip" not in codes


def test_collect_role_integrity_findings_reports_huntmaster_prereq_gaps(monkeypatch):
    member = _make_member(9, ["Watch Brother", "Huntmaster"])
    guild = SimpleNamespace(members=[member], get_role=lambda _rid: None)

    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {
            "role_integrity_audit": {
                "high_command_required_rank_role_names": [
                    "Watch Captain",
                    "Watch Master",
                    "Forgemaster",
                    "Void Warden",
                    "Chief Apothecary",
                    "High Chaplain",
                    "Lord Executioner",
                    "Venerable Dreadnought",
                    "Huntmaster",
                ],
                "huntmaster_required_rank_role_names": [
                    "Watch Brother",
                    "Watch Veteran",
                    "Deathwatch Specialist",
                    "Watch Command",
                    "High Command",
                    "Huntmaster",
                ],
            },
            "companies": {},
        },
    )
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", set())

    findings = _run(ro._collect_role_integrity_findings(guild))
    codes = {f["code"] for f in findings}

    assert "huntmaster_skip" in codes
    assert "high_command_missing" in codes


def test_role_integrity_audit_loop_respects_hour_gate(monkeypatch):
    now = datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    loop_globals = ro._role_integrity_audit_loop.coro.__globals__
    monkeypatch.setitem(loop_globals, "datetime", _FixedDateTime)
    monkeypatch.setattr(ro, "_role_integrity_cfg", lambda: {})
    monkeypatch.setattr(ro, "_schedule_role_audit_hour_utc", lambda: 12)
    collect_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ro, "_collect_role_integrity_findings", collect_mock)

    _run(ro._role_integrity_audit_loop.coro())

    collect_mock.assert_not_awaited()


def test_role_integrity_audit_loop_skips_if_already_ran_today(monkeypatch):
    now = datetime(2026, 6, 26, 15, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    loop_globals = ro._role_integrity_audit_loop.coro.__globals__
    monkeypatch.setitem(loop_globals, "datetime", _FixedDateTime)
    monkeypatch.setattr(ro, "_role_integrity_cfg", lambda: {})
    monkeypatch.setattr(ro, "_schedule_role_audit_hour_utc", lambda: 12)
    monkeypatch.setattr(ro, "_load_role_integrity_state", lambda: {"last_run_date": "2026-06-26"})
    collect_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ro, "_collect_role_integrity_findings", collect_mock)

    _run(ro._role_integrity_audit_loop.coro())

    collect_mock.assert_not_awaited()


def test_collect_role_integrity_findings_does_not_require_company_command_for_specialists(monkeypatch):
    member = _make_member(8, ["Watch Brother", "Watch Apothecary", "Watch Company Primus"])
    guild = SimpleNamespace(members=[member], get_role=lambda rid: SimpleNamespace(id=rid, name="Primus Command" if rid == 2002 else "Watch Company Primus"))

    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {
            "role_integrity_audit": {},
            "companies": {
                "primus": {
                    "name": "Watch Company Primus",
                    "companyRoleId": 2001,
                    "companyCommandRoleId": 2002,
                }
            },
        },
    )
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", set())
    monkeypatch.setattr(ro, "_role_id_set", lambda _m: {2001})

    findings = _run(ro._collect_role_integrity_findings(guild))
    codes = {f["code"] for f in findings}
    assert "company_command_missing" not in codes


def test_collect_role_integrity_findings_requires_company_command_for_captain(monkeypatch):
    member = _make_member(9, ["Watch Brother", "Watch Captain", "Watch Company Primus"])
    guild = SimpleNamespace(members=[member], get_role=lambda rid: SimpleNamespace(id=rid, name="Primus Command" if rid == 2002 else "Watch Company Primus"))

    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {
            "role_integrity_audit": {},
            "companies": {
                "primus": {
                    "name": "Watch Company Primus",
                    "companyRoleId": 2001,
                    "companyCommandRoleId": 2002,
                }
            },
        },
    )
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", set())
    monkeypatch.setattr(ro, "_role_id_set", lambda _m: {2001})

    findings = _run(ro._collect_role_integrity_findings(guild))
    codes = {f["code"] for f in findings}
    assert "company_command_missing" in codes


def test_parse_roster_section_from_source_filters_embed_sections():
    field_cmd = SimpleNamespace(name="▸ Company Captain & Lieutenant", value="· | <@101>")
    field_specialist = SimpleNamespace(name="▸ Forge Master's Specialists", value="· | <@202>")
    embed = SimpleNamespace(fields=[field_cmd, field_specialist])
    message = SimpleNamespace(content="", embeds=[embed])

    parsed = ro._parse_roster_section_from_source(
        message,
        include_sections={"Company Captain & Lieutenant"},
    )

    assert set(parsed.keys()) == {101}


def test_parse_kill_teams_section_from_embed_fields_resolves_role_id(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", {3001})

    roles = [SimpleNamespace(id=3001, name="Kill Team Alpha")]
    guild = SimpleNamespace(roles=roles)
    field = SimpleNamespace(name="▸ Kill Team Alpha", value="**2 Brothers Assigned**\n· | <@11>\n· | <@12>")
    embed = SimpleNamespace(fields=[field])
    message = SimpleNamespace(content="", embeds=[embed])

    parsed = ro._parse_kill_teams_section(message, guild=guild)

    assert 3001 in parsed
    assert set(parsed[3001].keys()) == {11, 12}
    assert parsed[3001][11]["rank"] == "Unknown"


def test_find_roster_messages_prefers_state_message_ids(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "roster_state.json"
    state_path.write_text(
        json.dumps(
            {
                "Watch Company Primus": {
                    "channel_id": 777,
                    "hc_message_id": 10,
                    "command_message_id": 20,
                    "killteam_message_ids": {"Kill Team Alpha": 30},
                }
            }
        )
    )
    monkeypatch.setattr(ro, "ROSTER_STATE_PATH", str(state_path))

    messages = {
        10: SimpleNamespace(id=10, content="hc", embeds=[]),
        20: SimpleNamespace(id=20, content="cmd", embeds=[]),
        30: SimpleNamespace(id=30, content="kt", embeds=[]),
    }

    class _Channel:
        async def fetch_message(self, message_id):
            return messages[message_id]

    guild = SimpleNamespace(get_channel=lambda _cid: _Channel())

    high_cmd, company_cmd, kill_teams = _run(ro._find_roster_messages(guild, 777))

    assert getattr(high_cmd, "id", None) == 10
    assert getattr(company_cmd, "id", None) == 20
    assert [m.id for m in kill_teams] == [30]


def test_run_role_integrity_audit_once_force_bypasses_day_and_hour(monkeypatch):
    now = datetime(2026, 6, 26, 1, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    fn_globals = ro._run_role_integrity_audit_once.__globals__
    monkeypatch.setitem(fn_globals, "datetime", _FixedDateTime)
    monkeypatch.setattr(ro, "_role_integrity_cfg", lambda: {})
    monkeypatch.setattr(ro, "_schedule_role_audit_hour_utc", lambda: 12)
    monkeypatch.setattr(ro, "_load_role_integrity_state", lambda: {"last_run_date": "2026-06-26"})

    guild = SimpleNamespace()
    monkeypatch.setattr(ro, "_b", lambda name: (lambda: guild) if name == "_resolve_notification_guild" else None)

    collect_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ro, "_collect_role_integrity_findings", collect_mock)

    save_calls = []
    monkeypatch.setattr(ro, "_save_role_integrity_state", lambda state: save_calls.append(state))

    ran = _run(ro._run_role_integrity_audit_once(force=True))

    assert ran is True
    collect_mock.assert_awaited_once()
    assert save_calls and save_calls[0].get("last_forced") is True
