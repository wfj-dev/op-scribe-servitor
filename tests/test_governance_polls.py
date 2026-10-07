import sys
import types
from types import SimpleNamespace

import pytest


def _install_discord_stub():
    try:
        import discord as discord_stub  # type: ignore
        import discord.app_commands as _real_app_commands  # type: ignore

        app_commands_mod = types.ModuleType("discord.app_commands")
        for _name in dir(_real_app_commands):
            setattr(app_commands_mod, _name, getattr(_real_app_commands, _name))
        app_commands_mod.command = lambda **_kwargs: (lambda func: func)
        app_commands_mod.describe = lambda **_kwargs: (lambda func: func)
        app_commands_mod.choices = lambda **_kwargs: (lambda func: func)
        app_commands_mod.autocomplete = lambda **_kwargs: (lambda func: func)
        app_commands_mod.rename = lambda **_kwargs: (lambda func: func)

        discord_stub.app_commands = app_commands_mod
        sys.modules["discord"] = discord_stub
        sys.modules["discord.app_commands"] = app_commands_mod
        return
    except Exception:
        pass

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
    app_commands_mod.choices = lambda **_kwargs: (lambda func: func)
    app_commands_mod.autocomplete = lambda **_kwargs: (lambda func: func)
    app_commands_mod.rename = lambda **_kwargs: (lambda func: func)
    app_commands_mod.Choice = type(
        "Choice",
        (),
        {
            "__init__": lambda self, *args, **kwargs: [setattr(self, k, v) for k, v in kwargs.items()] and None,
            "__class_getitem__": classmethod(lambda cls, _item: cls),
        },
    )
    discord_stub.app_commands = app_commands_mod

    ui_mod = types.ModuleType("discord.ui")
    ui_mod.View = type("View", (), {"__init__": lambda self, *args, **kwargs: None, "add_item": lambda self, item: None})
    ui_mod.Button = type("Button", (), {"__init__": lambda self, *args, **kwargs: None})
    ui_mod.Select = type("Select", (), {"__init__": lambda self, *args, **kwargs: None})
    ui_mod.UserSelect = type("UserSelect", (), {"__init__": lambda self, *args, **kwargs: None})
    ui_mod.RoleSelect = type("RoleSelect", (), {"__init__": lambda self, *args, **kwargs: None})
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
# Keep these deterministic even when another test module pre-populates opscribe.bot.
bot_stub.check_command_permission = lambda *_args, **_kwargs: True
bot_stub.is_allowed_channel = lambda *_args, **_kwargs: True
if not hasattr(bot_stub, "HOME_CHAPTERS"):
    bot_stub.HOME_CHAPTERS = []
if not hasattr(bot_stub, "_resolve_notification_guild"):
    bot_stub._resolve_notification_guild = lambda: None

import opscribe._bot_globals as _g  # noqa: E402

_TEST_GOVERNANCE_CONFIG = {
    "governance_poll": {
        "quorum_percent": 0.60,
        "pass_percent": 0.80,
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


class _Message:
    def __init__(self):
        self.edits = []
        self.deleted = False

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

    async def delete(self):
        self.deleted = True


class _Channel:
    def __init__(self):
        self.messages = []
        self._message = _Message()

    async def fetch_message(self, _message_id):
        return self._message

    async def send(self, content=None, embed=None):
        self.messages.append({"content": content, "embed": embed})


class _GuildWithChannels:
    def __init__(self, channel_map):
        self._channel_map = channel_map

    def get_channel(self, channel_id):
        return self._channel_map.get(channel_id)


class _PollCreateChannel:
    def __init__(self):
        self.messages = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(id=777001)


class _CreatePollGuild:
    def __init__(self, members, roles, channel, channel_id):
        self.members = members
        self.roles = roles
        self._channel = channel
        self._channel_id = channel_id

    def get_channel(self, channel_id):
        if int(channel_id) == int(self._channel_id):
            return self._channel
        return None


def _poll(votes, electorate_size=10, threshold=0.80):
    return {
        "votes": votes,
        "electorate_size": electorate_size,
        "quorum_percent": 0.60,
        "pass_threshold": threshold,
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


def test_allowed_target_role_name_accepts_huntmaster_aliases():
    assert po._is_allowed_target_role_name("Huntmaster") is True
    assert po._is_allowed_target_role_name("Hunt Master") is True


def test_allowed_target_role_name_accepts_blademaster_aliases():
    assert po._is_allowed_target_role_name("Blade Master") is True
    assert po._is_allowed_target_role_name("Blademaster") is True


def test_allowed_target_role_name_does_not_apply_blanket_whitespace_aliasing():
    assert po._is_allowed_target_role_name("WatchCaptain") is False


def test_evaluate_poll_passes_normal_threshold():
    poll = _poll(
        {
            "yay": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
            "nay": ["10"],
        },
        electorate_size=10,
        threshold=0.80,
    )
    result = po._evaluate_poll(poll)
    assert result["quorum_met"] is True
    assert result["outcome"] == "passed"


def test_evaluate_poll_marks_revote_required_for_close_margin():
    # yes rate = 76%, threshold = 80%, difference 4% (inside 5% margin)
    poll = _poll(
        {
            "yay": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19"],
            "nay": ["20", "21", "22", "23", "24", "25"],
        },
        electorate_size=25,
        threshold=0.80,
    )
    result = po._evaluate_poll(poll)
    assert result["revote_required"] is True
    assert result["outcome"] == "revote_required"
    assert any("close margin" in line.lower() for line in result["revote_reasons"])


def test_active_embed_includes_subject_member():
    poll = {
        "title": "test poll",
        "subject_user_id": "281651485782310914",
        "target_role": "@watch techmarine",
        "votes": {"yay": [], "nay": []},
        "electorate_size": 10,
        "quorum_percent": 0.60,
        "pass_threshold": 0.80,
        "expires_at": "2026-07-23T02:23:20.808067+00:00",
        "poll_id": "gov-0002",
    }

    embed = po._build_active_poll_embed(poll)
    assert "Vote Subject" in embed.description
    assert "<@281651485782310914>" in embed.description
    assert "per-option totals remain anonymous" in embed.description

    field_names = [f.name for f in embed.fields]
    assert "`ᴘᴀʀᴛɪᴄɪᴘᴀᴛɪᴏɴ`" in field_names
    assert "`ᴛʜʀᴇsʜᴏʟᴅ ʀᴜʟᴇs`" in field_names
    assert "`ʏᴀʏ`" not in field_names
    assert "`ɴᴀʏ`" not in field_names


def test_final_embed_uses_anonymous_vote_breakdown():
    poll = {
        "poll_id": "gov-0010",
        "title": "Promotion vote",
        "subject_user_id": None,
        "target_role": "Watch Sergeant",
        "electorate_ids": ["1", "2", "3", "4"],
        "electorate_size": 4,
        "votes": {
            "yay": ["1"],
            "nay": ["2"],
        },
    }
    evaluation = po._evaluate_poll(poll)

    embed = po._build_final_embed(poll, evaluation)
    field_map = {f.name: f.value for f in embed.fields}
    assert "`ʏᴀʏ`" in field_map
    assert "`ɴᴀʏ`" in field_map
    assert "Ballots: **1**" in field_map["`ʏᴀʏ`"]
    assert "Share: **50.00%**" in field_map["`ʏᴀʏ`"]
    assert "Ballots: **1**" in field_map["`ɴᴀʏ`"]
    assert "Share: **50.00%**" in field_map["`ɴᴀʏ`"]
    assert all("<@" not in f.value for f in embed.fields)


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


class _InteractionWithGuild:
    def __init__(self, user_id, guild):
        self.guild = guild
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
        "votes": {"yay": [], "nay": []},
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: {"next_id": 10, "polls": {"gov-0009": poll}})

    import asyncio
    asyncio.run(po._handle_vote(interaction, "gov-0009", "yay"))

    assert interaction.response.messages == [
        {"content": "You are the subject of this poll and are recused from voting.", "ephemeral": True}
    ]


def test_close_poll_sets_revote_required_when_quorum_not_met():
    channel = _Channel()
    guild = _GuildWithChannels({1489282103119052903: channel})
    poll = {
        "poll_id": "gov-0042",
        "status": "open",
        "title": "Promotion vote",
        "channel_id": 1489282103119052903,
        "message_id": 9001,
        "votes": {
            "yay": ["1", "2"],
            "nay": ["3", "4"],
        },
        "electorate_size": 10,
        "quorum_percent": 0.60,
        "pass_threshold": 0.80,
        "close_margin_percent": 0.05,
    }

    import asyncio
    asyncio.run(po._close_poll(guild, poll))

    assert poll["status"] == "closed"
    assert poll["evaluation"]["outcome"] == "revote_required"
    assert any("Quorum not met" in r for r in poll["evaluation"]["revote_reasons"])
    assert channel.messages


def test_delete_poll_creator_can_delete_open_poll(monkeypatch):
    channel = _Channel()
    guild = _GuildWithChannels({1489282103119052903: channel})
    interaction = _InteractionWithGuild(42, guild)

    state = {
        "next_id": 2,
        "polls": {
            "gov-0001": {
                "poll_id": "gov-0001",
                "title": "Promotion vote",
                "status": "open",
                "created_by": "42",
                "channel_id": 1489282103119052903,
                "message_id": 9001,
            }
        },
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)

    import asyncio
    asyncio.run(po._handle_delete_poll(interaction, "gov-0001"))

    assert "gov-0001" not in state["polls"]
    assert channel._message.deleted is True
    assert interaction.response.messages == [
        {"content": "Deleted poll **Promotion vote** (`gov-0001`).", "ephemeral": True}
    ]


def test_delete_poll_non_creator_is_denied(monkeypatch):
    channel = _Channel()
    guild = _GuildWithChannels({1489282103119052903: channel})
    interaction = _InteractionWithGuild(7, guild)

    state = {
        "next_id": 2,
        "polls": {
            "gov-0001": {
                "poll_id": "gov-0001",
                "title": "Promotion vote",
                "status": "open",
                "created_by": "42",
                "channel_id": 1489282103119052903,
                "message_id": 9001,
            }
        },
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)

    import asyncio
    asyncio.run(po._handle_delete_poll(interaction, "gov-0001"))

    assert "gov-0001" in state["polls"]
    assert channel._message.deleted is False
    assert interaction.response.messages == [
        {"content": "Only the poll creator can delete this poll.", "ephemeral": True}
    ]


def test_delete_poll_rejects_closed_poll(monkeypatch):
    channel = _Channel()
    guild = _GuildWithChannels({1489282103119052903: channel})
    interaction = _InteractionWithGuild(42, guild)

    state = {
        "next_id": 2,
        "polls": {
            "gov-0001": {
                "poll_id": "gov-0001",
                "title": "Promotion vote",
                "status": "closed",
                "created_by": "42",
                "channel_id": 1489282103119052903,
                "message_id": 9001,
            }
        },
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)

    import asyncio
    asyncio.run(po._handle_delete_poll(interaction, "gov-0001"))

    assert "gov-0001" in state["polls"]
    assert interaction.response.messages == [
        {"content": "Only open polls can be deleted.", "ephemeral": True}
    ]


def test_generate_poll_without_target_role_is_rejected(monkeypatch):
    channel_id = po.GOVERNANCE_POLL_CHANNEL_ID
    channel = _PollCreateChannel()
    guild = _CreatePollGuild(
        members=[_Member(1, ["Watch Command"])],
        roles=[SimpleNamespace(name="Watch Command", mention="@Watch Command")],
        channel=channel,
        channel_id=channel_id,
    )
    interaction = _InteractionWithGuild(42, guild)

    state = {"next_id": 1, "polls": {}}
    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)
    monkeypatch.setattr(po._g.bot, "add_view", lambda *args, **kwargs: None, raising=False)

    import asyncio
    generate_poll = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(
        generate_poll(
            interaction,
            title="Doctrine vote",
            target_role=None,
            subject_member=None,
        )
    )

    assert state["polls"] == {}
    assert interaction.response.messages == [
        {"content": "A destination role is required.", "ephemeral": True}
    ]
    assert not channel.messages


def test_generate_poll_blade_master_target_also_uses_universal_threshold(monkeypatch):
    channel_id = po.GOVERNANCE_POLL_CHANNEL_ID
    channel = _PollCreateChannel()
    guild = _CreatePollGuild(
        members=[_Member(1, ["Watch Command", "Blade Master"])],
        roles=[SimpleNamespace(name="Watch Command", mention="@Watch Command")],
        channel=channel,
        channel_id=channel_id,
    )
    interaction = _InteractionWithGuild(42, guild)

    state = {"next_id": 1, "polls": {}}
    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)
    monkeypatch.setattr(po._g.bot, "add_view", lambda *args, **kwargs: None, raising=False)

    import asyncio
    generate_poll = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(
        generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="Blademaster"),
            subject_member=_Member(9, ["First Blade"]),
        )
    )

    poll = state["polls"]["gov-0001"]
    assert poll["pass_threshold"] == pytest.approx(0.80)
    assert poll["target_role"] == "Blademaster"


def test_generate_poll_rejects_non_role_target(monkeypatch):
    guild = _CreatePollGuild(
        members=[_Member(1, ["Watch Command"])],
        roles=[SimpleNamespace(name="Watch Command", mention="@Watch Command")],
        channel=_PollCreateChannel(),
        channel_id=po.GOVERNANCE_POLL_CHANNEL_ID,
    )
    interaction = _InteractionWithGuild(42, guild)

    import asyncio
    generate_poll = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(
        generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="   "),
            subject_member=None,
        )
    )

    assert interaction.response.messages == [
        {"content": "Target role must be a server role.", "ephemeral": True}
    ]


def test_generate_poll_rejects_disallowed_target_role(monkeypatch):
    guild = _CreatePollGuild(
        members=[_Member(1, ["Watch Command"])],
        roles=[SimpleNamespace(name="Watch Command", mention="@Watch Command")],
        channel=_PollCreateChannel(),
        channel_id=po.GOVERNANCE_POLL_CHANNEL_ID,
    )
    interaction = _InteractionWithGuild(42, guild)

    import asyncio
    generate_poll = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(
        generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="Watch Brother"),
            subject_member=None,
        )
    )

    assert interaction.response.messages == [
        {"content": "Target role is not allowed for governance polls.", "ephemeral": True}
    ]


def test_generate_poll_watch_captain_target_is_allowed(monkeypatch):
    channel_id = po.GOVERNANCE_POLL_CHANNEL_ID
    channel = _PollCreateChannel()
    guild = _CreatePollGuild(
        members=[_Member(1, ["Watch Command", "Watch Captain"])],
        roles=[SimpleNamespace(name="Watch Command", mention="@Watch Command")],
        channel=channel,
        channel_id=channel_id,
    )
    interaction = _InteractionWithGuild(42, guild)

    state = {"next_id": 1, "polls": {}}
    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)
    monkeypatch.setattr(po._g.bot, "add_view", lambda *args, **kwargs: None, raising=False)

    import asyncio
    generate_poll = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(
        generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="Watch Captain"),
            subject_member=_Member(9, ["Watch Lieutenant"]),
        )
    )

    poll = state["polls"]["gov-0001"]
    assert poll["target_role"] == "Watch Captain"
    assert poll["pass_threshold"] == pytest.approx(0.80)


@pytest.mark.parametrize("rank,expected", [
    ("Watch Sergeant", 200), ("Veteran Sergeant", 230),
    ("Watch Lieutenant", 260), ("Watch Captain", 350), ("Watch Master", 400),
])
def test_rank_weights_and_high_command_bonus(rank, expected):
    snapshot = po._weighted_electorate_snapshot(_Guild([_Member(1, [rank])]), None, "Watch Sergeant")
    assert snapshot["voter_weights"]["1"] == expected
    assert snapshot["high_command_ids"] == (["1"] if rank in {"Watch Captain", "Watch Master"} else [])


@pytest.mark.parametrize("target,cadre", [
    ("Watch Sergeant", "battle_line"), ("Veteran Sergeant", "battle_line"),
    ("Watch Lieutenant", "battle_line"), ("Watch Captain", "battle_line"),
    ("Watch Master", "battle_line"), ("Oathsworn", "battle_line"),
    ("Watch Techmarine", "armory"), ("Forge Master", "armory"),
    ("Watch Librarian", "librarius"), ("Void Warden", "librarius"),
    ("Watch Chaplain", "reclusiam"), ("High Chaplain", "reclusiam"),
    ("Watch Apothecary", "apothecarion"), ("Chief Apothecary", "apothecarion"),
    ("First Blade", "blades"), ("Blademaster", "blades"),
    ("Kill-Marine", "black_vault"), ("Hunt Master", "black_vault"),
    ("Honored Dreadnought", "dreadnought"), ("Venerable Dreadnought", "dreadnought"),
])
def test_destination_mapping(target, cadre):
    assert po._destination_cadre(target) == cadre


def test_specialists_remain_separate_and_bladeguard_do_not_vote():
    guild = _Guild([
        _Member(1, ["Watch Sergeant", "Watch Techmarine"]),
        _Member(2, ["Watch Librarian"]),
        _Member(3, ["Watch Command", "Bladeguard", "Watch Sergeant"]),
        _Member(4, ["First Blade"]),
        _Member(5, ["Watch Techmarine", "Reserves"]),
        _Member(6, ["Watch Techmarine", "Interred Brother"]),
        _Member(7, ["Watch Techmarine"], bot=True),
        _Member(8, ["Watch Brother"]),
    ])
    snapshot = po._weighted_electorate_snapshot(guild, None, "Watch Techmarine")
    assert snapshot["turnout_ids"] == ["1"]
    assert snapshot["voter_weights"] == {"1": 200, "2": 100, "4": 100}


def test_equerry_is_configured_by_member_and_destination_not_discord_role(monkeypatch):
    monkeypatch.setattr(_g, "CONFIG", {"governance_poll": {"equerry_assignments": {"1": "armory"}}})
    snapshot = po._weighted_electorate_snapshot(_Guild([
        _Member(1, ["Watch Techmarine"]), _Member(2, ["Watch Librarian"]),
    ]), None, "Watch Techmarine")
    assert snapshot["voter_weights"] == {"1": 310, "2": 100}
    assert snapshot["high_command_ids"] == ["1"]
    assert snapshot["turnout_ids"] == ["1"]


def test_recusal_removes_subject_from_both_groups():
    guild = _Guild([_Member(1, ["Watch Captain"]), _Member(2, ["Watch Master"])])
    snapshot = po._weighted_electorate_snapshot(guild, 1, "Watch Captain")
    assert snapshot["electorate_ids"] == ["2"]
    assert snapshot["turnout_ids"] == ["2"]
    assert snapshot["high_command_ids"] == ["2"]


def test_highest_tier_is_not_stacked():
    snapshot = po._weighted_electorate_snapshot(_Guild([
        _Member(1, ["Watch Sergeant", "Veteran Sergeant", "Watch Lieutenant", "Watch Captain", "Watch Master"]),
    ]), None, "Watch Sergeant")
    assert snapshot["voter_weights"] == {"1": 400}


@pytest.mark.parametrize("members,target,recuse", [
    ([_Member(1, ["Watch Captain"])], "Watch Captain", 1),
    ([_Member(1, ["Watch Librarian"])], "Watch Sergeant", None),
    ([_Member(1, ["Watch Techmarine"])], "Forgemaster", None),
])
def test_empty_required_electorate_blocks_creation(members, target, recuse):
    with pytest.raises(ValueError, match="No eligible"):
        po._weighted_electorate_snapshot(_Guild(members), recuse, target)


def _weighted_poll(target="Watch Techmarine"):
    guild = _Guild([
        _Member(1, ["Watch Techmarine"]), _Member(2, ["Watch Techmarine"]),
        _Member(3, ["Forgemaster"]), _Member(4, ["Watch Captain"]),
        _Member(5, ["Watch Librarian"]),
    ])
    return {**_poll({"yay": [], "nay": []}), **po._weighted_electorate_snapshot(guild, None, target)}


def test_outsiders_cannot_satisfy_turnout():
    poll = _weighted_poll()
    poll["votes"] = {"yay": ["4", "5"], "nay": []}
    result = po._evaluate_poll(poll)
    assert result["turnout_percent"] == 0
    assert result["quorum_met"] is False
    assert result["outcome"] == "revote_required"


def test_weighted_result_and_overlapping_groups_count_each_person_once():
    poll = _weighted_poll("Forgemaster")
    poll["votes"] = {"yay": ["1", "3", "4"], "nay": ["5"]}
    result = po._evaluate_poll(poll)
    assert result["yes_weight"] == 725
    assert result["no_weight"] == 100
    assert result["yes_rate"] == pytest.approx(725 / 825)
    assert result["turnout_percent"] == pytest.approx(200 / 3)
    assert result["high_command_turnout_percent"] == 100
    assert result["votes_cast"] == 4
    assert result["outcome"] == "passed"


def test_high_command_requires_its_own_quorum():
    poll = _weighted_poll("Forgemaster")
    poll["votes"] = {"yay": ["1", "2"], "nay": []}
    result = po._evaluate_poll(poll)
    assert result["turnout_percent"] == pytest.approx(200 / 3)
    assert result["high_command_quorum_met"] is False
    assert result["outcome"] == "revote_required"
    assert any("High Command quorum" in reason for reason in result["revote_reasons"])


def test_snapshot_is_unchanged_by_later_config_or_roles(monkeypatch):
    member = _Member(1, ["Watch Sergeant"])
    poll = {**_poll({"yay": ["1"], "nay": []}),
            **po._weighted_electorate_snapshot(_Guild([member]), None, "Watch Sergeant")}
    member.roles = [_Role(name="Watch Master")]
    monkeypatch.setattr(_g, "CONFIG", {"governance_poll": {"base_weight_units": {"watch_master": 10000}}})
    result = po._evaluate_poll(poll)
    assert result["yes_weight"] == 200
    assert result["outcome"] == "passed"


def test_duplicate_and_unknown_ballots_do_not_inflate_weight_or_turnout():
    poll = _weighted_poll()
    poll["votes"] = {"yay": ["1", "1", "2", "unknown"], "nay": []}
    result = po._evaluate_poll(poll)
    assert result["votes_cast"] == 2
    assert result["yes_weight"] == 400
    assert result["turnout_percent"] == pytest.approx(200 / 3)


def test_conflicting_ballots_are_rejected():
    poll = _weighted_poll()
    poll["votes"] = {"yay": ["1"], "nay": ["1"]}
    with pytest.raises(ValueError, match="both sides"):
        po._evaluate_poll(poll)


@pytest.mark.parametrize("config", [
    {"base_weight_units": {"watch_master": 150}},
    {"base_weight_units": {"watch_master": -1}},
    {"base_weight_units": []},
    {"base_weight_units": {"unknown_tier": 100}},
    {"high_command_bonus_units": 0},
    {"destination_multiplier": 1},
    {"equerry_assignments": {"1": "unknown"}},
    {"equerry_assignments": {"1": []}},
    {"equerry_assignments": {1: "armory"}},
])
def test_invalid_voting_policy_fails_closed(monkeypatch, config):
    monkeypatch.setattr(_g, "CONFIG", {"governance_poll": config})
    with pytest.raises(ValueError, match="Invalid governance"):
        po._weighted_electorate_snapshot(_Guild([_Member(1, ["Watch Sergeant"])]), None, "Watch Sergeant")


def test_turnout_display_uses_percentages_not_cadre_label():
    poll = _weighted_poll("Forgemaster")
    poll.update({"title": "Promotion", "expires_at": "2099-01-01T00:00:00+00:00",
                 "votes": {"yay": ["1", "3", "4"], "nay": []}})
    for embed in (po._build_active_poll_embed(poll), po._build_final_embed(poll, po._evaluate_poll(poll))):
        text = "\n".join(field.value for field in embed.fields)
        assert "Turnout: **66.67%**" in text
        assert "High Command turnout: **100.00%**" in text
        assert "cadre turnout" not in text.lower()
        assert "3/" not in text


def test_equerry_appointment_is_explicit_and_persisted(monkeypatch):
    guild = _CreatePollGuild(
        [_Member(1, ["Watch Techmarine"]), _Member(2, ["Forgemaster"])], [],
        _PollCreateChannel(), po.GOVERNANCE_POLL_CHANNEL_ID,
    )
    interaction = _InteractionWithGuild(42, guild)
    state = {"next_id": 1, "polls": {}}
    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)
    monkeypatch.setattr(po._g.bot, "add_view", lambda *args, **kwargs: None, raising=False)
    import asyncio
    callback = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(callback(interaction, "Equerry appointment", _Role(name="Watch Techmarine"),
                         subject_member=_Member(9, ["Watch Techmarine"]), equerry_appointment=True))
    poll = state["polls"]["gov-0001"]
    assert poll["equerry_appointment"] is True
    assert poll["high_command_quorum_required"] is True
    assert poll["subject_user_id"] == "9"
    assert "Equerry appointment" in po._build_active_poll_embed(poll).description


@pytest.mark.parametrize("target,subject", [
    ("Watch Techmarine", None), ("Watch Sergeant", _Member(9, ["Watch Sergeant"])),
])
def test_equerry_appointment_requires_subject_and_specialist_role(target, subject):
    interaction = _Interaction(42)
    import asyncio
    callback = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(callback(interaction, "Equerry", _Role(name=target), subject, True))
    expected = "A subject member is required." if subject is None else "Equerry appointments require a subject member and a destination specialist role."
    assert interaction.response.messages[0]["content"] == expected


def test_stale_equerry_registry_cannot_enfranchise_bladeguard_or_brother(monkeypatch):
    monkeypatch.setattr(_g, "CONFIG", {"governance_poll": {"equerry_assignments": {"1": "blades", "2": "armory"}}})
    snapshot = po._weighted_electorate_snapshot(_Guild([
        _Member(1, ["Bladeguard"]), _Member(2, ["Watch Brother"]), _Member(3, ["First Blade"]),
    ]), None, "First Blade")
    assert snapshot["electorate_ids"] == ["3"]
    assert snapshot["high_command_ids"] == []


@pytest.mark.parametrize("debug_mode,permission_granted", [(False, False), (True, True)])
def test_configured_active_equerry_can_create_poll_without_equerry_role(monkeypatch, debug_mode, permission_granted):
    monkeypatch.setattr(_g, "CONFIG", {"governance_poll": {"equerry_assignments": {"42": "armory"}}})
    monkeypatch.setattr(bot_stub, "DEBUG_MODE", debug_mode, raising=False)
    monkeypatch.setattr(bot_stub, "check_command_permission", lambda *_args: permission_granted)
    guild = _CreatePollGuild([_Member(42, ["Watch Techmarine"])], [],
                             _PollCreateChannel(), po.GOVERNANCE_POLL_CHANNEL_ID)
    interaction = _InteractionWithGuild(42, guild)
    interaction.user = guild.members[0]
    state = {"next_id": 1, "polls": {}}
    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)
    monkeypatch.setattr(po._g.bot, "add_view", lambda *args, **kwargs: None, raising=False)
    import asyncio
    callback = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(callback(interaction, "Specialist admission", _Role(name="Watch Techmarine"),
                         _Member(9, ["Watch Brother"])))
    assert state["polls"]["gov-0001"]["voter_weights"] == {"42": 310}


def test_configured_equerry_cannot_override_debug_mode_denial(monkeypatch):
    monkeypatch.setattr(_g, "CONFIG", {"governance_poll": {"equerry_assignments": {"42": "armory"}}})
    monkeypatch.setattr(bot_stub, "DEBUG_MODE", True, raising=False)
    monkeypatch.setattr(bot_stub, "check_command_permission", lambda *_args: False)
    interaction = _Interaction(42)
    interaction.user = _Member(42, ["Watch Techmarine"])
    saved = []
    monkeypatch.setattr(po, "_save_polls_state", saved.append)
    import asyncio
    callback = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(callback(interaction, "Specialist admission", _Role(name="Watch Techmarine"),
                         _Member(9, ["Watch Brother"])))
    assert interaction.response.messages == [{"content": "Access denied.", "ephemeral": True}]
    assert saved == []


def test_generate_poll_requires_both_role_and_subject_in_signature():
    import inspect
    callback = getattr(po.generate_poll, "callback", po.generate_poll)
    parameters = inspect.signature(callback).parameters
    assert parameters["target_role"].default is inspect.Parameter.empty
    assert parameters["subject_member"].default is inspect.Parameter.empty


def test_generate_poll_without_subject_is_rejected_before_saving(monkeypatch):
    interaction = _Interaction(42)
    saved = []
    monkeypatch.setattr(po, "_save_polls_state", saved.append)
    import asyncio
    callback = getattr(po.generate_poll, "callback", po.generate_poll)
    asyncio.run(callback(interaction, "Promotion", _Role(name="Watch Sergeant"), None))
    assert interaction.response.messages == [
        {"content": "A subject member is required.", "ephemeral": True}
    ]
    assert saved == []


def test_legacy_poll_is_not_reweighted():
    poll = _poll({"yay": ["1", "2", "3"], "nay": ["4"]}, electorate_size=4)
    poll["voter_weights"] = {"1": 10000, "2": 10000, "3": 10000, "4": 1}
    assert po._evaluate_poll(poll)["yes_rate"] == 0.75


def test_weighted_poll_survives_json_roundtrip():
    import json
    poll = _weighted_poll("Forgemaster")
    poll["votes"] = {"yay": ["1", "3", "4"], "nay": ["5"]}
    restored = json.loads(json.dumps(poll))
    assert po._evaluate_poll(restored) == po._evaluate_poll(poll)


def test_exact_sixty_percent_turnout_and_eighty_percent_approval():
    poll = {
        **_poll({"yay": ["1", "2"], "nay": ["3"]}),
        "policy_version": 2, "electorate_ids": ["1", "2", "3", "4", "5"],
        "turnout_ids": ["1", "2", "3", "4", "5"], "high_command_ids": [],
        "voter_weights": {"1": 200, "2": 200, "3": 100, "4": 100, "5": 100},
    }
    result = po._evaluate_poll(poll)
    assert result["turnout_percent"] == 60
    assert result["quorum_met"] is True
    assert result["yes_rate"] == 0.8
    assert result["close_margin_hit"] is True
    assert result["outcome"] == "revote_required"
