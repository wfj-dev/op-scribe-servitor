import sys
import types
from types import SimpleNamespace

import pytest


def _install_discord_stub():
    discord_stub = sys.modules.get("discord") or types.ModuleType("discord")

    discord_stub.Member = object
    discord_stub.User = object
    discord_stub.Guild = object
    discord_stub.Role = object
    discord_stub.Interaction = object
    discord_stub.Attachment = object
    discord_stub.TextChannel = object
    discord_stub.AllowedMentions = type("AllowedMentions", (), {"__init__": lambda self, *args, **kwargs: None})
    discord_stub.ButtonStyle = SimpleNamespace(success=1, danger=2, secondary=3)
    discord_stub.utils = SimpleNamespace(
        get=lambda items, **kwargs: next(
            (item for item in items if all(getattr(item, key, None) == value for key, value in kwargs.items())),
            None,
        )
    )

    class _Embed:
        def __init__(self, *, title=None, description=None, color=None):
            self.title = title
            self.description = description
            self.color = color
            self.fields = []
            self.footer = None
            self.timestamp = None
            self.author = None

        def add_field(self, *, name, value, inline=False):
            self.fields.append(SimpleNamespace(name=name, value=value, inline=inline))

        def set_footer(self, *, text=None):
            self.footer = text

        def set_author(self, *, name=None, icon_url=None):
            self.author = SimpleNamespace(name=name, icon_url=icon_url)

    existing_embed = getattr(discord_stub, "Embed", None)
    if existing_embed is not None and existing_embed is not _Embed:
        if not hasattr(existing_embed, "set_footer"):
            setattr(existing_embed, "set_footer", lambda self, *, text=None: setattr(self, "footer", text))
        if not hasattr(existing_embed, "set_author"):
            setattr(existing_embed, "set_author", lambda self, *, name=None, icon_url=None: setattr(self, "author", SimpleNamespace(name=name, icon_url=icon_url)))
    else:
        discord_stub.Embed = _Embed

    app_commands_mod = types.ModuleType("discord.app_commands")
    app_commands_mod.command = lambda **_kwargs: (lambda func: func)
    app_commands_mod.describe = lambda **_kwargs: (lambda func: func)
    app_commands_mod.Choice = type("Choice", (), {"__class_getitem__": classmethod(lambda cls, _item: cls)})
    discord_stub.app_commands = app_commands_mod

    ui_mod = types.ModuleType("discord.ui")
    ui_mod.View = type("View", (), {"__init__": lambda self, *args, **kwargs: None, "add_item": lambda self, item: None})
    ui_mod.Button = type("Button", (), {"__init__": lambda self, *args, **kwargs: None})
    ui_mod.Modal = type("Modal", (), {"__init_subclass__": classmethod(lambda cls, **kwargs: None), "__init__": lambda self, *args, **kwargs: None})
    ui_mod.TextInput = type("TextInput", (), {"__init__": lambda self, *args, **kwargs: None})
    ui_mod.button = lambda **_kwargs: (lambda func: func)
    discord_stub.ui = ui_mod
    discord_stub.TextStyle = SimpleNamespace(paragraph=1)

    class _LoopStub:
        def __init__(self, func):
            self.func = func

        def start(self):
            return None

        def is_running(self):
            return False

    tasks_mod = types.ModuleType("discord.ext.tasks")
    tasks_mod.loop = lambda **_kwargs: (lambda func: _LoopStub(func))
    ext_mod = types.ModuleType("discord.ext")
    ext_mod.tasks = tasks_mod

    sys.modules["discord"] = discord_stub
    sys.modules["discord.app_commands"] = app_commands_mod
    sys.modules["discord.ui"] = ui_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.tasks"] = tasks_mod


_install_discord_stub()

bot_stub = sys.modules.get("opscribe.bot")
if bot_stub is None:
    bot_stub = types.ModuleType("opscribe.bot")
    sys.modules["opscribe.bot"] = bot_stub
    sys.modules["bot"] = bot_stub

if not hasattr(bot_stub, "bot"):
    bot_stub.bot = SimpleNamespace(tree=SimpleNamespace(command=lambda **_kwargs: (lambda func: func)))
if not hasattr(bot_stub, "check_command_permission"):
    bot_stub.check_command_permission = lambda *_args, **_kwargs: True
if not hasattr(bot_stub, "is_allowed_channel"):
    bot_stub.is_allowed_channel = lambda *_args, **_kwargs: True
if not hasattr(bot_stub, "HOME_CHAPTERS"):
    bot_stub.HOME_CHAPTERS = []
if not hasattr(bot_stub, "_resolve_notification_guild"):
    bot_stub._resolve_notification_guild = lambda: None

import opscribe._bot_globals as _g  # noqa: E402

_TEST_GOVERNANCE_CONFIG = {
    "governance_poll": {
        "quorum_percent": 0.60,
        "normal_pass_percent": 0.66,
        "high_command_pass_percent": 0.75,
        "abstain_revote_percent": 0.35,
        "close_margin_percent": 0.05,
        "duration_hours": 24,
    }
}
_import_bot = _g.bot
_import_config = _g.CONFIG
_g.bot = bot_stub.bot
_g.CONFIG = _TEST_GOVERNANCE_CONFIG

import opscribe.poll_ops as po  # noqa: E402

_g.bot = _import_bot
_g.CONFIG = _import_config


@pytest.fixture(autouse=True)
def _restore_bot_globals():
    original_bot = _g.bot
    original_config = _g.CONFIG
    _g.bot = bot_stub.bot
    _g.CONFIG = _TEST_GOVERNANCE_CONFIG
    try:
        yield
    finally:
        _g.bot = original_bot
        _g.CONFIG = original_config


class _Role(SimpleNamespace):
    pass


class _Member:
    def __init__(self, mid, role_names, *, bot=False):
        self.id = mid
        self.bot = bot
        self.roles = [_Role(name=rn, id=1000 + i) for i, rn in enumerate(role_names)]


class _Guild:
    def __init__(self, members):
        self.members = members


def _poll(votes, electorate_size=10, threshold=0.66):
    return {
        "votes": votes,
        "electorate_size": electorate_size,
        "quorum_percent": 0.60,
        "pass_threshold": threshold,
        "abstain_revote_percent": 0.35,
        "close_margin_percent": 0.05,
    }


def test_electorate_snapshot_excludes_reserves_interred_and_recused():
    guild = _Guild(
        [
            _Member(1, ["Watch Command"]),
            _Member(2, ["Watch Command", "Reserves"]),
            _Member(3, ["Watch Command", "Interred Brother"]),
            _Member(4, ["Watch Command"]),
            _Member(5, ["Watch Brother"]),
            _Member(6, ["Watch Command"], bot=True),
        ]
    )

    electorate = po._eligible_electorate_snapshot(guild, recuse_user_id=4)
    assert electorate == ["1"]


def test_target_role_high_command_detection():
    assert po._target_is_high_command("Watch Master") is True
    assert po._target_is_high_command("Watch Sergeant") is False


def test_evaluate_poll_passes_normal_threshold():
    poll = _poll(
        {
            "yay": ["1", "2", "3", "4", "5", "6"],
            "nay": ["7", "8"],
            "abstain": ["9"],
        },
        electorate_size=10,
        threshold=0.66,
    )
    result = po._evaluate_poll(poll)
    assert result["quorum_met"] is True
    assert result["outcome"] == "passed"


def test_evaluate_poll_marks_revote_required_for_abstain_threshold():
    poll = _poll(
        {
            "yay": ["1", "2"],
            "nay": ["3", "4"],
            "abstain": ["5", "6", "7"],
        },
        electorate_size=10,
        threshold=0.66,
    )
    result = po._evaluate_poll(poll)
    assert result["revote_required"] is True
    assert result["outcome"] == "revote_required"
    assert any("Abstain threshold" in line for line in result["revote_reasons"])


def test_evaluate_poll_marks_revote_required_for_close_margin():
    # yes rate = 70%, threshold = 66%, difference 4% (inside 5% margin)
    poll = _poll(
        {
            "yay": ["1", "2", "3", "4", "5", "6", "7"],
            "nay": ["8", "9", "10"],
            "abstain": [],
        },
        electorate_size=10,
        threshold=0.66,
    )
    result = po._evaluate_poll(poll)
    assert result["revote_required"] is True
    assert result["outcome"] == "revote_required"
    assert any("close margin" in line.lower() for line in result["revote_reasons"])


def test_active_embed_includes_subject_member_and_threshold_rule():
    poll = {
        "title": "test poll",
        "subject_user_id": "281651485782310914",
        "target_role": "@watch techmarine",
        "classification": "normal",
        "include_abstain": True,
        "votes": {"yay": [], "nay": [], "abstain": []},
        "electorate_size": 10,
        "quorum_percent": 0.60,
        "pass_threshold": 0.66,
        "expires_at": "2026-07-23T02:23:20.808067+00:00",
        "poll_id": "gov-0002",
    }

    embed = po._build_active_poll_embed(poll)
    assert "Vote Subject" in embed.description
    assert "<@281651485782310914>" in embed.description
    assert "Threshold Rule" in embed.description
    assert "Standard Watch Command threshold" in embed.description
    assert "per-option totals are anonymous" in embed.description

    field_names = [f.name for f in embed.fields]
    assert "`ᴘᴀʀᴛɪᴄɪᴘᴀᴛɪᴏɴ`" in field_names
    assert "`ᴛʜʀᴇsʜᴏʟᴅ ʀᴜʟᴇs`" in field_names
    assert "`ʏᴀʏ`" not in field_names
    assert "`ɴᴀʏ`" not in field_names
    assert "`ᴀʙsᴛᴀɪɴ`" not in field_names


def test_final_embed_includes_no_shows():
    poll = {
        "poll_id": "gov-0010",
        "title": "Promotion vote",
        "subject_user_id": None,
        "target_role": "Watch Sergeant",
        "classification": "normal",
        "include_abstain": True,
        "electorate_ids": ["1", "2", "3", "4"],
        "electorate_size": 4,
        "votes": {
            "yay": ["1"],
            "nay": ["2"],
            "abstain": ["3"],
        },
    }
    evaluation = po._evaluate_poll(poll)

    embed = po._build_final_embed(poll, evaluation)
    no_show_field = next((f for f in embed.fields if f.name == "`ɴᴏ-sʜᴏᴡs`"), None)
    assert no_show_field is not None
    assert "<@4>" in no_show_field.value


class _Response:
    def __init__(self):
        self.messages = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append({"content": content, "ephemeral": ephemeral})


class _Interaction:
    def __init__(self, user_id):
        self.guild = _Guild([])
        self.user = SimpleNamespace(id=user_id)
        self.response = _Response()


def test_subject_is_explicitly_told_they_are_recused(monkeypatch):
    interaction = _Interaction(42)
    poll = {
        "poll_id": "gov-0009",
        "status": "open",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "subject_user_id": "42",
        "electorate_ids": ["1", "2", "3"],
        "votes": {"yay": [], "nay": [], "abstain": []},
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: {"next_id": 10, "polls": {"gov-0009": poll}})

    import asyncio
    asyncio.run(po._handle_vote(interaction, "gov-0009", "yay"))

    assert interaction.response.messages == [
        {"content": "You are the subject of this poll and are recused from voting.", "ephemeral": True}
    ]
