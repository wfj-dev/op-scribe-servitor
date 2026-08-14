import asyncio
import sys
import types
from types import SimpleNamespace

import pytest


def _install_discord_stub():
    try:
        import discord as _real_discord  # type: ignore
        import discord.app_commands  # type: ignore  # noqa: F401
        import discord.ext.tasks  # type: ignore  # noqa: F401
        import discord.ui  # type: ignore  # noqa: F401
        sys.modules.setdefault("discord", _real_discord)
        return
    except Exception:
        pass

    discord_stub = sys.modules.get("discord") or types.ModuleType("discord")
    _compat_type = type("_CompatType", (), {"__init__": lambda self, *args, **kwargs: [setattr(self, k, v) for k, v in kwargs.items()] and None})

    class _StubEmbed:
        def __init__(self, *, color=None, title=None, description=None):
            self.color = color
            self.title = title
            self.description = description
            self.fields = []
            self.author = None

        def add_field(self, *, name, value, inline=True):
            self.fields.append(SimpleNamespace(name=name, value=value, inline=inline))

        def set_author(self, *, name=None, icon_url=None):
            self.author = SimpleNamespace(name=name, icon_url=icon_url)

    discord_stub.Member = object
    discord_stub.User = object
    discord_stub.Guild = object
    discord_stub.Role = object
    discord_stub.TextChannel = object
    discord_stub.Interaction = object
    discord_stub.Thread = type("Thread", (), {})
    discord_stub.ForumChannel = type("ForumChannel", (), {})
    discord_stub.File = object
    discord_stub.Object = object
    discord_stub.AllowedMentions = type("AllowedMentions", (), {"__init__": lambda self, *args, **kwargs: None})
    discord_stub.NotFound = Exception
    discord_stub.Forbidden = Exception
    discord_stub.ButtonStyle = types.SimpleNamespace(secondary=2, success=3, danger=4, primary=1)
    discord_stub.Embed = _StubEmbed
    discord_stub.abc = types.SimpleNamespace(Messageable=object, GuildChannel=object, MessageableChannel=object)
    discord_stub.utils = types.SimpleNamespace(
        get=lambda items, **kwargs: next(
            (item for item in items if all(getattr(item, key, None) == value for key, value in kwargs.items())),
            None,
        )
    )
    discord_stub.__getattr__ = lambda name: type(name, (), {})

    app_commands_mod = types.ModuleType("discord.app_commands")
    app_commands_mod.CommandTree = type("CommandTree", (), {"__init__": lambda self, bot: None})
    app_commands_mod.command = lambda **_kwargs: (lambda func: func)
    app_commands_mod.describe = lambda **_kwargs: (lambda func: func)
    app_commands_mod.choices = lambda **_kwargs: (lambda func: func)
    app_commands_mod.autocomplete = lambda **_kwargs: (lambda func: func)
    app_commands_mod.rename = lambda **_kwargs: (lambda func: func)
    _fallback_type = type(
        "_FallbackType",
        (),
        {
            "__init__": lambda self, *args, **kwargs: None,
            "__class_getitem__": classmethod(lambda cls, _item: cls),
        },
    )
    app_commands_mod.Choice = _fallback_type
    app_commands_mod.__getattr__ = lambda _name: _fallback_type
    discord_stub.app_commands = app_commands_mod

    ui_mod = types.ModuleType("discord.ui")
    ui_mod.View = type(
        "View",
        (),
        {
            "__init_subclass__": classmethod(lambda cls, **_kwargs: None),
            "__init__": lambda self, *args, **kwargs: None,
        },
    )
    ui_mod.Modal = type(
        "Modal",
        (),
        {
            "__init_subclass__": classmethod(lambda cls, **_kwargs: None),
            "__init__": lambda self, *args, **kwargs: None,
        },
    )
    ui_mod.TextInput = type("TextInput", (), {"__init__": lambda self, *args, **kwargs: None})
    ui_mod.Button = _compat_type
    ui_mod.Select = _compat_type
    ui_mod.UserSelect = _compat_type
    ui_mod.RoleSelect = _compat_type
    ui_mod.button = lambda **_kwargs: (lambda func: func)
    ui_mod.select = lambda **_kwargs: (lambda func: func)
    discord_stub.ui = ui_mod
    discord_stub.TextStyle = types.SimpleNamespace(paragraph=1)

    class _LoopStub:
        def __init__(self, func):
            self.func = func

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
    discord_stub.ext = ext_mod

    sys.modules["discord"] = discord_stub
    sys.modules["discord.app_commands"] = app_commands_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.tasks"] = tasks_mod
    sys.modules["discord.ui"] = ui_mod


_install_discord_stub()

bot_stub = sys.modules.get("opscribe.bot")
if bot_stub is None:
    bot_stub = types.ModuleType("opscribe.bot")
    sys.modules["opscribe.bot"] = bot_stub
    sys.modules["bot"] = bot_stub

if not hasattr(bot_stub, "bot"):
    bot_tree_stub = SimpleNamespace(command=lambda **_kwargs: (lambda func: func))
    bot_stub.bot = SimpleNamespace(tree=bot_tree_stub)
if not hasattr(bot_stub, "tree"):
    bot_stub.tree = SimpleNamespace()
if not hasattr(bot_stub, "CONFIG"):
    bot_stub.CONFIG = {}
if not hasattr(bot_stub, "DEBUG_MODE"):
    bot_stub.DEBUG_MODE = False
if not hasattr(bot_stub, "ALLOWED_KT_ROLE_IDS"):
    bot_stub.ALLOWED_KT_ROLE_IDS = set()
if not hasattr(bot_stub, "KILL_TEAMS"):
    bot_stub.KILL_TEAMS = []
if not hasattr(bot_stub, "HOME_CHAPTERS"):
    bot_stub.HOME_CHAPTERS = []
if not hasattr(bot_stub, "check_command_permission"):
    bot_stub.check_command_permission = lambda _user, _command: True
if not hasattr(bot_stub, "is_allowed_channel"):
    bot_stub.is_allowed_channel = lambda _interaction: True
if not hasattr(bot_stub, "_resolve_notification_guild"):
    bot_stub._resolve_notification_guild = lambda: None
if not hasattr(bot_stub, "_induction_count_for_user"):
    bot_stub._induction_count_for_user = lambda *_args, **_kwargs: 0
if not hasattr(bot_stub, "__getattr__"):
    bot_stub.__getattr__ = lambda _name: (lambda *args, **kwargs: None)

import opscribe._bot_globals as _g  # noqa: E402

_g.bot = bot_stub.bot

import opscribe.roster_ops as ro  # noqa: E402
import opscribe.constants as constants  # noqa: E402
import opscribe.flavor_text.chapters as chapter_flavor  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_chapter_request_state(monkeypatch):
    state_store = {}

    def _fake_load():
        return dict(state_store)

    def _fake_save(state):
        state_store.clear()
        state_store.update(state)

    monkeypatch.setattr(ro, "_load_chapter_request_state", _fake_load)
    monkeypatch.setattr(ro, "_save_chapter_request_state", _fake_save)
    return state_store


class _Role(SimpleNamespace):
    pass


class _Guild:
    def __init__(self, roles, channels=None):
        self.roles = roles
        self._channels = channels or {}

    def get_role(self, role_id):
        return next((role for role in self.roles if getattr(role, "id", None) == role_id), None)

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)


class _Channel:
    def __init__(self):
        self.messages = []
        self._messages_by_id = {}
        self._next_id = 1000

    async def send(self, content=None, embed=None, **kwargs):
        message_id = self._next_id
        self._next_id += 1
        msg = _Message(message_id, self, content=content, embed=embed, view=kwargs.get("view"), extra=kwargs)
        self._messages_by_id[message_id] = msg
        self.messages.append({"id": message_id, "content": content, "embed": embed, "view": kwargs.get("view"), "kwargs": kwargs, "message": msg})
        return msg

    async def fetch_message(self, message_id):
        return self._messages_by_id[message_id]


class _Message:
    def __init__(self, message_id, channel, *, content=None, embed=None, view=None, extra=None):
        self.id = message_id
        self.channel = channel
        self.content = content
        self.embeds = [embed] if embed is not None else []
        self.view = view
        self.extra = extra or {}

    async def edit(self, *, content=None, embed=None, view=None):
        if content is not None:
            self.content = content
        if embed is not None:
            self.embeds = [embed]
        self.view = view


class _Member:
    def __init__(self, member_id, roles, guild):
        self.id = member_id
        self.name = f"Member{member_id}"
        self.display_name = self.name
        self.mention = f"<@{member_id}>"
        self.roles = list(roles)
        self.guild = guild
        self.bot = False
        self.display_avatar = SimpleNamespace(url=f"https://avatar.example/{member_id}.png")

    async def remove_roles(self, *roles, reason=None):
        remove_ids = {role.id for role in roles}
        self.roles = [role for role in self.roles if role.id not in remove_ids]

    async def add_roles(self, *roles, reason=None):
        existing_ids = {role.id for role in self.roles}
        for role in roles:
            if role.id not in existing_ids:
                self.roles.append(role)
                existing_ids.add(role.id)


class _Response:
    def __init__(self):
        self.messages = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append({"content": content, "ephemeral": ephemeral})


class _Interaction:
    def __init__(self, guild, user):
        self.guild = guild
        self.user = user
        self.response = _Response()


def _role(role_id, name):
    return _Role(id=role_id, name=name, mention=f"<@&{role_id}>")


def _attachment(filename="pauldron.png", content_type="image/png", url="https://cdn.example/pauldron.png"):
    return SimpleNamespace(filename=filename, content_type=content_type, url=url)


def test_assign_member_to_reserves_removes_company_and_kill_team_roles():
    reserves = _role(ro.RESERVES_ROLE_ID, "Reserves")
    company = _role(2001, "Watch Company Primus")
    kill_team = _role(3001, "Kill Team Talon")
    unrelated = _role(4001, "Watch Brother")
    guild = _Guild([reserves, company, kill_team, unrelated])
    member = _Member(77, [company, kill_team, unrelated], guild)

    bot_stub.ALLOWED_KT_ROLE_IDS = {kill_team.id}
    bot_stub.KILL_TEAMS = [kill_team.name]

    result = _run(ro._assign_member_to_reserves(member))

    remaining_names = {role.name for role in member.roles}
    assert remaining_names == {"Watch Brother", "Reserves"}
    assert set(result["removed"]) == {"Watch Company Primus", "Kill Team Talon"}
    assert result["added"] == ["Reserves"]


def test_assign_member_to_reserves_uses_configured_company_roles(monkeypatch):
    reserves = _role(ro.RESERVES_ROLE_ID, "Reserves")
    company = _role(2002, "Watch Company Quartus")
    unrelated = _role(4001, "Watch Brother")
    guild = _Guild([reserves, company, unrelated])
    member = _Member(78, [company, unrelated], guild)

    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {"companies": {"quartus": {"name": "Quartus", "companyRoleId": 2002}}},
    )

    result = _run(ro._assign_member_to_reserves(member))

    remaining_names = {role.name for role in member.roles}
    assert remaining_names == {"Watch Brother", "Reserves"}
    assert set(result["removed"]) == {"Watch Company Quartus"}
    assert result["added"] == ["Reserves"]


def test_chapter_assign_swaps_existing_chapter_roles():
    old_chapter = _role(5001, "Blood Angels")
    new_chapter = _role(5002, "Hawk Lords")
    non_chapter = _role(5003, "Watch Brother")
    announce_channel = _Channel()
    guild = _Guild([old_chapter, new_chapter, non_chapter], channels={ro.SERVICE_STUDS_CHANNEL_ID: announce_channel})
    member = _Member(88, [old_chapter, non_chapter], guild)
    interaction = _Interaction(guild, user=SimpleNamespace(id=1, name="Apothecary"))
    interaction.user.display_name = "Apothecary"
    interaction.user.display_avatar = SimpleNamespace(url="https://avatar.example/apothecary.png")

    bot_stub.HOME_CHAPTERS = ["Blood Angels", "Hawk Lords"]
    bot_stub.check_command_permission = lambda _user, command: command == "chapter_assign"
    bot_stub.is_allowed_channel = lambda _interaction: True
    bot_stub._get_award_announcement_channel = None

    _run(ro.chapter_assign(interaction, member, "Hawk Lords"))

    role_names = {role.name for role in member.roles}
    assert role_names == {"Hawk Lords", "Watch Brother"}
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "Removed other chapter roles: Blood Angels." in interaction.response.messages[0]["content"]
    assert len(announce_channel.messages) == 1
    announce_embed = announce_channel.messages[0]["embed"]
    assert announce_embed is not None
    assert announce_embed.author.name == "Apothecary"
    assert "Blood Angels -> Hawk Lords" in announce_embed.description


def test_chapter_assign_rejects_invalid_chapter():
    guild = _Guild([])
    member = _Member(99, [], guild)
    interaction = _Interaction(guild, user=SimpleNamespace(id=1, name="Apothecary"))

    bot_stub.HOME_CHAPTERS = ["Blood Angels", "Hawk Lords"]
    bot_stub.check_command_permission = lambda _user, command: command == "chapter_assign"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(ro.chapter_assign(interaction, member, "Ultramarines"))

    assert "is not a valid chapter" in interaction.response.messages[0]["content"]


def test_chapter_assign_denies_when_permission_fails():
    guild = _Guild([])
    member = _Member(101, [], guild)
    interaction = _Interaction(guild, user=SimpleNamespace(id=2, name="Brother"))

    bot_stub.HOME_CHAPTERS = ["Blood Angels"]
    bot_stub.check_command_permission = lambda _user, _command: False
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(ro.chapter_assign(interaction, member, "Blood Angels"))

    assert interaction.response.messages == [{"content": "Access denied.", "ephemeral": True}]


def test_chapter_request_with_matching_role_name_notifies_apothecary_with_requester_as_author():
    apothecary_role = _role(6001, ro.APOTHECARY_ROLE_NAME)
    requested_role = _role(6002, "Hawk Lords")
    current_role = _role(6003, "Blood Angels")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, requested_role, current_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(111, [current_role], guild)
    requester.display_name = "Brother Titus"
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = ["Blood Angels", "Hawk Lords"]
    bot_stub.check_command_permission = lambda _user, command: command == "chapter_request"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(ro.chapter_request(interaction, "  hawk   lords "))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert "sent to the Apothecary staff channel" in interaction.response.messages[0]["content"]
    assert len(staff_channel.messages) == 1
    sent = staff_channel.messages[0]
    assert sent["content"] == apothecary_role.mention
    embed = sent["embed"]
    assert embed.author.name == "Brother Titus"
    requested_field = next(field for field in embed.fields if field.name == "`ʀᴇǫᴜᴇsᴛᴇᴅ ᴄʜᴀᴘᴛᴇʀ`")
    assert requested_role.mention in requested_field.value


def test_chapter_request_without_matching_role_escalates_watch_master_and_forgemaster():
    apothecary_role = _role(7001, ro.APOTHECARY_ROLE_NAME)
    watch_master_role = _role(7002, "Watch Master")
    forgemaster_role = _role(7003, "Forgemaster")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, watch_master_role, forgemaster_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(112, [], guild)
    requester.display_name = "Brother Leon"
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = ["Blood Angels"]
    bot_stub.check_command_permission = lambda _user, command: command == "chapter_request"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(ro.chapter_request(interaction, "Imperial Fists"))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert "sent to the Apothecary staff channel" in interaction.response.messages[0]["content"]
    assert len(staff_channel.messages) == 1
    sent = staff_channel.messages[0]
    assert apothecary_role.mention in sent["content"]
    assert watch_master_role.mention in sent["content"]
    assert forgemaster_role.mention in sent["content"]
    field_map = {field.name: field.value for field in sent["embed"].fields}
    assert "Imperial Fists" in field_map["`ʀᴇǫᴜᴇsᴛᴇᴅ ᴄʜᴀᴘᴛᴇʀ`"]
    assert "No existing Discord role matched" in field_map["`Support Onboarding Required`"]


def test_chapter_request_blank_name_returns_error():
    apothecary_role = _role(7051, ro.APOTHECARY_ROLE_NAME)
    staff_channel = _Channel()
    guild = _Guild([apothecary_role], channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel})
    requester = _Member(115, [], guild)
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = ["Blood Angels"]
    bot_stub.check_command_permission = lambda _user, command: command == "chapter_request"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(ro.chapter_request(interaction, "   "))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert "must provide a chapter name" in interaction.response.messages[0]["content"].lower()
    assert len(staff_channel.messages) == 0


def test_chapter_request_has_28_day_cooldown(monkeypatch):
    apothecary_role = _role(7101, ro.APOTHECARY_ROLE_NAME)
    requested_role = _role(7102, "Hawk Lords")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, requested_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(113, [], guild)
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = ["Hawk Lords"]
    bot_stub.check_command_permission = lambda _user, command: command == "chapter_request"
    bot_stub.is_allowed_channel = lambda _interaction: True

    state_store = {}

    def _fake_load():
        return dict(state_store)

    def _fake_save(state):
        state_store.clear()
        state_store.update(state)

    monkeypatch.setattr(ro, "_load_chapter_request_state", _fake_load)
    monkeypatch.setattr(ro, "_save_chapter_request_state", _fake_save)

    _run(ro.chapter_request(interaction, "Hawk Lords"))
    assert len(staff_channel.messages) == 1
    assert "sent to the Apothecary staff channel" in interaction.response.messages[0]["content"]

    second_interaction = _Interaction(guild, requester)
    _run(ro.chapter_request(second_interaction, "Hawk Lords"))

    assert len(staff_channel.messages) == 1
    assert "cooldown active" in second_interaction.response.messages[0]["content"].lower()
    assert second_interaction.response.messages[0]["ephemeral"] is True


def test_angels_encarmine_is_registered_as_a_canonical_chapter():
    assert "Angels Encarmine" in chapter_flavor._CANONICAL_HOME_CHAPTERS
    assert constants.CHAPTER_EMBED_COLORS["Angels Encarmine"] == 0x7A0D14


def test_angels_encarmine_has_bespoke_flavor_entries():
    assert chapter_flavor.CHAPTER_BLESSINGS["Angels Encarmine"].startswith("Every campaign is a liturgy")
    assert chapter_flavor.FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Angels Encarmine"][0].startswith(
        "The Angels Encarmine trust the brother"
    )
    assert chapter_flavor.CHAPTER_STUDS_FLAVOR["Angels Encarmine"][0].startswith("Each stud is another campaign")
    assert "Order Omega" in chapter_flavor.ORDER_OMEGA_CHAPTER_LINES["Angels Encarmine"]
    assert "Terminus" in chapter_flavor.TERMINUS_SLAYER_ASSAULT_CHAPTER_LINES["Angels Encarmine"]


def test_revilers_is_registered_as_a_canonical_chapter():
    assert "Revilers" in chapter_flavor._CANONICAL_HOME_CHAPTERS
    assert constants.CHAPTER_EMBED_COLORS["Revilers"] == 0x4A4E54


def test_revilers_has_bespoke_flavor_entries():
    assert chapter_flavor.CHAPTER_BLESSINGS["Revilers"].startswith("Scarred helms and pitiless resolve")
    assert chapter_flavor.FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Revilers"][0].startswith(
        "Fear is a tool, not an accident"
    )
    assert chapter_flavor.CHAPTER_STUDS_FLAVOR["Revilers"][0].startswith("Each stud is another terror-front")
    assert "Revilers" in chapter_flavor.WATCH_VETERAN_CHAPTER_LINES["Revilers"]
    assert "Master Terminus Slayer" in chapter_flavor.MASTER_TERMINUS_SLAYER_CHAPTER_LINES["Revilers"]


def test_blood_scythes_is_registered_as_a_canonical_chapter():
    assert "Blood Scythes" in chapter_flavor._CANONICAL_HOME_CHAPTERS
    assert constants.CHAPTER_EMBED_COLORS["Blood Scythes"] == 0xF2F2F2


def test_blood_scythes_has_bespoke_flavor_entries():
    assert chapter_flavor.CHAPTER_BLESSINGS["Blood Scythes"].startswith("White plate, black trim")
    assert chapter_flavor.FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Blood Scythes"][0].startswith(
        "Sanguinius's honor is carried"
    )
    assert chapter_flavor.CHAPTER_STUDS_FLAVOR["Blood Scythes"][0].startswith("Each stud marks another harvest")
    assert chapter_flavor.WATCH_VETERAN_CHAPTER_LINES["Blood Scythes"].startswith(
        "White and black beneath the blood-drop sigil"
    )
    assert "Master" in chapter_flavor.MASTER_TERMINUS_SLAYER_CHAPTER_LINES["Blood Scythes"]


def test_knights_of_abhorrence_is_registered_as_a_canonical_chapter():
    assert "Knights of Abhorrence" in chapter_flavor._CANONICAL_HOME_CHAPTERS
    assert constants.CHAPTER_EMBED_COLORS["Knights of Abhorrence"] == 0x1B6B73


def test_knights_of_abhorrence_has_bespoke_flavor_entries():
    assert chapter_flavor.CHAPTER_BLESSINGS["Knights of Abhorrence"].startswith("Black and teal warplate")
    assert chapter_flavor.FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Knights of Abhorrence"][0].startswith(
        "The Ghoul Stars taught us"
    )
    assert chapter_flavor.CHAPTER_STUDS_FLAVOR["Knights of Abhorrence"][0].startswith(
        "Each stud is another horror"
    )
    assert chapter_flavor.WATCH_VETERAN_CHAPTER_LINES["Knights of Abhorrence"].startswith(
        "Tempered in the Ghoul Stars"
    )
    assert "mastered all six" in chapter_flavor.MASTER_TERMINUS_SLAYER_CHAPTER_LINES["Knights of Abhorrence"]


def test_white_templars_is_registered_as_a_canonical_chapter():
    assert "White Templars" in chapter_flavor._CANONICAL_HOME_CHAPTERS
    assert constants.CHAPTER_EMBED_COLORS["White Templars"] == 0xF2F2F2


def test_white_templars_has_bespoke_flavor_entries():
    assert chapter_flavor.CHAPTER_BLESSINGS["White Templars"].startswith("White and black warplate")
    assert chapter_flavor.FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["White Templars"][0].startswith(
        "Sanctum endures"
    )
    assert chapter_flavor.CHAPTER_STUDS_FLAVOR["White Templars"][0].startswith("Each stud marks another siege-line")
    assert "White Templars" in chapter_flavor.WATCH_VETERAN_CHAPTER_LINES["White Templars"]
    assert "Master Terminus Slayer" in chapter_flavor.MASTER_TERMINUS_SLAYER_CHAPTER_LINES["White Templars"]
