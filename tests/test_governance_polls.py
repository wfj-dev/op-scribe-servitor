import sys
import types
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

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
    assert "per-option totals remain anonymous" in embed.description

    field_names = [f.name for f in embed.fields]
    assert "`ᴘᴀʀᴛɪᴄɪᴘᴀᴛɪᴏɴ`" in field_names
    assert "`ᴛʜʀᴇsʜᴏʟᴅ ʀᴜʟᴇs`" in field_names
    assert "`ʏᴀʏ`" not in field_names
    assert "`ɴᴀʏ`" not in field_names
    assert "`ᴀʙsᴛᴀɪɴ`" not in field_names


def test_final_embed_uses_anonymous_vote_breakdown():
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
    field_map = {f.name: f.value for f in embed.fields}
    assert "`ʏᴀʏ`" in field_map
    assert "`ɴᴀʏ`" in field_map
    assert "`ᴀʙsᴛᴀɪɴ`" in field_map
    assert "Ballots: **1**" in field_map["`ʏᴀʏ`"]
    assert "Share: **33.33%**" in field_map["`ʏᴀʏ`"]
    assert "Ballots: **1**" in field_map["`ɴᴀʏ`"]
    assert "Share: **33.33%**" in field_map["`ɴᴀʏ`"]
    assert "Ballots: **1**" in field_map["`ᴀʙsᴛᴀɪɴ`"]
    assert "Share: **33.33%**" in field_map["`ᴀʙsᴛᴀɪɴ`"]
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
        "votes": {"yay": [], "nay": [], "abstain": []},
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: {"next_id": 10, "polls": {"gov-0009": poll}})

    import asyncio
    asyncio.run(po._handle_vote(interaction, "gov-0009", "yay"))

    assert interaction.response.messages == [
        {"content": "You are the subject of this poll and are recused from voting.", "ephemeral": True}
    ]


def test_close_poll_sets_revote_due_for_abstain_threshold():
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
            "abstain": ["5", "6", "7"],
        },
        "electorate_size": 10,
        "quorum_percent": 0.60,
        "pass_threshold": 0.66,
        "abstain_revote_percent": 0.35,
        "close_margin_percent": 0.05,
        "include_abstain": True,
        "classification": "normal",
    }

    import asyncio
    asyncio.run(po._close_poll(guild, poll))

    assert poll["status"] == "closed"
    assert poll["evaluation"]["outcome"] == "revote_required"
    assert poll["revote_reason"] == "abstain_threshold"
    assert poll.get("revote_due_at")
    assert channel.messages
    assert "without yay/nay outcome" in channel.messages[-1]["content"]


def test_due_revote_reminder_posts_once_and_marks_sent(monkeypatch):
    channel = _Channel()
    guild = _GuildWithChannels({1489282103119052903: channel})
    state = {
        "next_id": 2,
        "polls": {
            "gov-0001": {
                "poll_id": "gov-0001",
                "title": "Promotion vote",
                "status": "closed",
                "channel_id": 1489282103119052903,
                "evaluation": {"outcome": "revote_required"},
                "revote_reason": "abstain_threshold",
                "revote_due_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "revote_reminder_sent_at": None,
            }
        },
    }

    monkeypatch.setattr(po, "_load_polls_state", lambda: state)
    monkeypatch.setattr(po, "_save_polls_state", lambda _state: None)

    import asyncio
    asyncio.run(po._send_due_revote_reminders(guild))
    first_count = len(channel.messages)
    assert first_count == 1
    assert state["polls"]["gov-0001"].get("revote_reminder_sent_at")

    asyncio.run(po._send_due_revote_reminders(guild))
    assert len(channel.messages) == first_count


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


def test_generate_poll_without_target_role_uses_standard_threshold(monkeypatch):
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
    asyncio.run(
        po.generate_poll(
            interaction,
            title="Doctrine vote",
            target_role=None,
            subject_member=None,
            include_abstain=False,
        )
    )

    poll = state["polls"]["gov-0001"]
    assert poll["classification"] == "normal"
    assert poll["pass_threshold"] == pytest.approx(0.66)
    assert poll["target_role"] == "Not specified"
    assert interaction.response.messages == [
        {"content": f"Poll created in <#{channel_id}> (ID: `gov-0001`).", "ephemeral": True}
    ]

    assert channel.messages
    embed = channel.messages[0]["embed"]
    assert "Target Role/Rank" in embed.description
    assert "Not specified" in embed.description


def test_generate_poll_blade_master_target_uses_high_command_threshold(monkeypatch):
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
    asyncio.run(
        po.generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="Blademaster"),
            subject_member=None,
            include_abstain=False,
        )
    )

    poll = state["polls"]["gov-0001"]
    assert poll["classification"] == "high_command"
    assert poll["pass_threshold"] == pytest.approx(0.75)
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
    asyncio.run(
        po.generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="   "),
            subject_member=None,
            include_abstain=False,
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
    asyncio.run(
        po.generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="Watch Brother"),
            subject_member=None,
            include_abstain=False,
        )
    )

    assert interaction.response.messages == [
        {"content": "Target role is not allowed for governance polls.", "ephemeral": True}
    ]


def test_generate_poll_watch_captain_target_is_allowed(monkeypatch):
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
    asyncio.run(
        po.generate_poll(
            interaction,
            title="Promotion vote",
            target_role=SimpleNamespace(name="Watch Captain"),
            subject_member=None,
            include_abstain=False,
        )
    )

    poll = state["polls"]["gov-0001"]
    assert poll["target_role"] == "Watch Captain"
    assert poll["classification"] == "high_command"
    assert poll["pass_threshold"] == pytest.approx(0.75)
