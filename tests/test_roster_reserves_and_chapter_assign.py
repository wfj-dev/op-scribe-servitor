import asyncio
import sys
import types
from types import SimpleNamespace


def _install_discord_stub():
    discord_stub = sys.modules.get("discord") or types.ModuleType("discord")

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

bot_stub = types.ModuleType("opscribe.bot")
bot_tree_stub = SimpleNamespace(command=lambda **_kwargs: (lambda func: func))
bot_stub.bot = SimpleNamespace(tree=bot_tree_stub)
bot_stub.tree = SimpleNamespace()
bot_stub.CONFIG = {}
bot_stub.DEBUG_MODE = False
bot_stub.ALLOWED_KT_ROLE_IDS = set()
bot_stub.KILL_TEAMS = []
bot_stub.HOME_CHAPTERS = []
bot_stub.check_command_permission = lambda _user, _command: True
bot_stub.is_allowed_channel = lambda _interaction: True
sys.modules["opscribe.bot"] = bot_stub
sys.modules["bot"] = bot_stub

import opscribe._bot_globals as _g  # noqa: E402

_g.bot = bot_stub.bot

import opscribe.roster_ops as ro  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


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

    async def send(self, content=None, embed=None):
        self.messages.append({"content": content, "embed": embed})


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
    requested_field = next(field for field in embed.fields if field.name == "Requested Chapter")
    assert requested_field.value == requested_role.mention


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
    assert field_map["Requested Chapter"] == "Imperial Fists"
    assert "No existing Discord role matched" in field_map["Support Onboarding Required"]


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


def test_request_homebrew_chapter_sends_to_staff_and_pings_watch_command():
    apothecary_role = _role(7201, ro.APOTHECARY_ROLE_NAME)
    watch_master_role = _role(7202, "Watch Master")
    forgemaster_role = _role(7203, "Forgemaster")
    current_role = _role(7204, "Blood Angels")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, watch_master_role, forgemaster_role, current_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(121, [current_role], guild)
    requester.display_name = "Brother Cassian"
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = ["Blood Angels"]
    bot_stub.check_command_permission = lambda _user, command: command == "request_homebrew_chapter"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(
        ro.request_homebrew_chapter(
            interaction,
            "Ebon Wardens",
            "Raven Guard",
            "Stealth-obsessed brotherhood forged in long-void boarding actions.",
            _attachment(),
        )
    )

    assert interaction.response.messages[0]["ephemeral"] is True
    assert "homebrew chapter request has been sent" in interaction.response.messages[0]["content"].lower()
    assert len(staff_channel.messages) == 1
    sent = staff_channel.messages[0]
    assert apothecary_role.mention in sent["content"]
    assert watch_master_role.mention in sent["content"]
    assert forgemaster_role.mention in sent["content"]
    embed = sent["embed"]
    assert embed.author.name == "Brother Cassian"
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Requested Chapter"] == "Ebon Wardens"
    assert field_map["Geneseed Lineage"] == "Raven Guard"
    assert "Stealth-obsessed brotherhood" in field_map["Lore Blurb"]
    assert field_map["Pauldron Proof (Space Marine 2)"] == "https://cdn.example/pauldron.png"


def test_request_homebrew_chapter_rejects_lore_over_discord_limit():
    apothecary_role = _role(7301, ro.APOTHECARY_ROLE_NAME)
    watch_master_role = _role(7302, "Watch Master")
    forgemaster_role = _role(7303, "Forgemaster")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, watch_master_role, forgemaster_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(122, [], guild)
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = []
    bot_stub.check_command_permission = lambda _user, command: command == "request_homebrew_chapter"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(ro.request_homebrew_chapter(interaction, "Ebon Wardens", "Raven Guard", "x" * 1025, _attachment()))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert "max 1024 characters" in interaction.response.messages[0]["content"].lower()
    assert len(staff_channel.messages) == 0


def test_request_homebrew_chapter_rejects_non_image_attachment():
    apothecary_role = _role(7351, ro.APOTHECARY_ROLE_NAME)
    watch_master_role = _role(7352, "Watch Master")
    forgemaster_role = _role(7353, "Forgemaster")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, watch_master_role, forgemaster_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(124, [], guild)
    interaction = _Interaction(guild, requester)

    bot_stub.HOME_CHAPTERS = []
    bot_stub.check_command_permission = lambda _user, command: command == "request_homebrew_chapter"
    bot_stub.is_allowed_channel = lambda _interaction: True

    _run(
        ro.request_homebrew_chapter(
            interaction,
            "Ebon Wardens",
            "Raven Guard",
            "Stealth-obsessed brotherhood forged in long-void boarding actions.",
            _attachment(filename="proof.txt", content_type="text/plain", url="https://cdn.example/proof.txt"),
        )
    )

    assert interaction.response.messages[0]["ephemeral"] is True
    assert "must attach an in-game space marine 2 pauldron image" in interaction.response.messages[0]["content"].lower()
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


def test_homebrew_request_shares_chapter_request_cooldown(monkeypatch):
    apothecary_role = _role(7401, ro.APOTHECARY_ROLE_NAME)
    watch_master_role = _role(7402, "Watch Master")
    forgemaster_role = _role(7403, "Forgemaster")
    requested_role = _role(7404, "Hawk Lords")
    staff_channel = _Channel()
    guild = _Guild(
        [apothecary_role, watch_master_role, forgemaster_role, requested_role],
        channels={ro.APOTHECARY_STAFF_CHANNEL_ID: staff_channel},
    )
    requester = _Member(123, [], guild)

    bot_stub.HOME_CHAPTERS = ["Hawk Lords"]
    bot_stub.check_command_permission = lambda _user, command: command in {"chapter_request", "request_homebrew_chapter"}
    bot_stub.is_allowed_channel = lambda _interaction: True

    state_store = {}

    def _fake_load():
        return dict(state_store)

    def _fake_save(state):
        state_store.clear()
        state_store.update(state)

    monkeypatch.setattr(ro, "_load_chapter_request_state", _fake_load)
    monkeypatch.setattr(ro, "_save_chapter_request_state", _fake_save)

    first_interaction = _Interaction(guild, requester)
    _run(ro.chapter_request(first_interaction, "Hawk Lords"))
    assert len(staff_channel.messages) == 1

    second_interaction = _Interaction(guild, requester)
    _run(
        ro.request_homebrew_chapter(
            second_interaction,
            "Ebon Wardens",
            "Raven Guard",
            "Stealth-obsessed brotherhood forged in long-void boarding actions.",
            _attachment(),
        )
    )

    assert len(staff_channel.messages) == 1
    assert "cooldown active" in second_interaction.response.messages[0]["content"].lower()
    assert second_interaction.response.messages[0]["ephemeral"] is True
