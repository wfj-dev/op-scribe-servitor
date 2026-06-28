import sys
import types
from types import SimpleNamespace
from unittest.mock import patch


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

    discord_stub.Embed = _StubEmbed
    discord_stub.Intents = type("Intents", (), {"default": classmethod(lambda cls: SimpleNamespace(message_content=False, members=False))})
    discord_stub.Client = type("Client", (), {"__init__": lambda self, *args, **kwargs: None})
    discord_stub.Member = object
    discord_stub.User = object
    discord_stub.Guild = object
    discord_stub.Role = object
    discord_stub.TextChannel = object
    discord_stub.Emoji = object
    discord_stub.Interaction = object
    discord_stub.AllowedMentions = object
    discord_stub.SelectOption = object
    discord_stub.Thread = type("Thread", (), {})
    discord_stub.ForumChannel = type("ForumChannel", (), {})
    discord_stub.File = object
    discord_stub.Object = object
    discord_stub.Color = type("Color", (), {"from_rgb": classmethod(lambda cls, *args, **kwargs: cls())})
    discord_stub.NotFound = Exception
    discord_stub.Forbidden = Exception
    discord_stub.utils = types.SimpleNamespace(get=lambda items, **kwargs: next((item for item in items if all(getattr(item, key, None) == value for key, value in kwargs.items())), None))

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
    app_commands_mod.__getattr__ = lambda name: type(
        name,
        (),
        {
            "__init__": lambda self, *args, **kwargs: None,
            "__class_getitem__": classmethod(lambda cls, _item: cls),
        },
    )
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
    discord_stub.ButtonStyle = types.SimpleNamespace(secondary=2, success=3, danger=4, primary=1)
    discord_stub.abc = types.SimpleNamespace(Messageable=object, GuildChannel=object, MessageableChannel=object)
    discord_stub.__getattr__ = lambda name: type(name, (), {})

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
bot_stub.RANK_ROLES_PRIORITY = [
    "Watch Master",
    "Watch Captain",
    "Watch Lieutenant",
    "Watch Sergeant",
    "Watch Brother",
]
sys.modules["opscribe.bot"] = bot_stub
sys.modules["bot"] = bot_stub

import opscribe._bot_globals as _g  # noqa: E402
_g.bot = bot_stub.bot

import opscribe.roster_embeds as roster_embeds


def _member(*, member_id=1, nick=None, display_name=None, name=None, roles=None):
    return SimpleNamespace(
        id=member_id,
        nick=nick,
        display_name=display_name,
        name=name,
        mention=f"<@{member_id}>",
        roles=roles or [],
    )


def _role(role_id, name):
    return SimpleNamespace(id=role_id, name=name)


def test_clean_roster_name_strips_markup_and_truncates():
    member = _member(
        nick="  <:skull:123>  ᴡᴀᴛᴄʜ   ●   BROTHER   ▬   NAME   ⚬   " + ("X" * 60),
        name="fallback",
    )

    cleaned = roster_embeds._clean_roster_name(member)

    assert "<:" not in cleaned
    assert "●" not in cleaned
    assert "⚬" not in cleaned
    assert "▬" not in cleaned
    assert "  " not in cleaned
    assert cleaned.startswith("NAME")
    assert len(cleaned) == 38
    assert cleaned.endswith("…")


def test_is_in_reserves_by_role_id_or_name():
    by_id = _member(roles=[_role(roster_embeds.RESERVES_ROLE_ID, "Unrelated")])
    by_name = _member(roles=[_role(123, "Reserve Cadre")])
    not_reserve = _member(roles=[_role(124, "Kill Team Alpha")])

    assert roster_embeds._is_in_reserves(by_id) is True
    assert roster_embeds._is_in_reserves(by_name) is True
    assert roster_embeds._is_in_reserves(not_reserve) is False


def test_load_roster_state_returns_defaults_when_file_missing():
    with patch.object(roster_embeds, "ROSTER_STATE_PATH", "/tmp/roster-state-does-not-exist.json"), patch.object(
        roster_embeds,
        "ROSTER_COMPANY_CHANNELS",
        {"Watch Company Primus": 11, "Watch Company Secundus": 22},
    ):
        state = roster_embeds._load_roster_state()

    assert state == {
        "Watch Company Primus": {
            "channel_id": 11,
            "hc_message_id": None,
            "command_message_id": None,
            "killteam_message_ids": {},
        },
        "Watch Company Secundus": {
            "channel_id": 22,
            "hc_message_id": None,
            "command_message_id": None,
            "killteam_message_ids": {},
        },
    }


def test_load_roster_state_merges_existing_data_with_defaults(tmp_path):
    state_path = tmp_path / "roster_state.json"
    state_path.write_text(
        '{"Watch Company Primus":{"channel_id":999,"hc_message_id":101,"killteam_message_ids":{"Kill Team Alpha":202}}}',
        encoding="utf-8",
    )

    with patch.object(roster_embeds, "ROSTER_STATE_PATH", str(state_path)), patch.object(
        roster_embeds,
        "ROSTER_COMPANY_CHANNELS",
        {"Watch Company Primus": 11, "Watch Company Secundus": 22},
    ):
        state = roster_embeds._load_roster_state()

    assert state == {
        "Watch Company Primus": {
            "channel_id": 999,
            "hc_message_id": 101,
            "command_message_id": None,
            "killteam_message_ids": {"Kill Team Alpha": 202},
        },
        "Watch Company Secundus": {
            "channel_id": 22,
            "hc_message_id": None,
            "command_message_id": None,
            "killteam_message_ids": {},
        },
    }


def test_sort_key_for_member_uses_rank_priority_then_clean_name():
    captain = _member(
        nick="Watch Captain Zephon",
        roles=[_role(1, "Watch Captain")],
    )
    sergeant = _member(
        nick="Watch Sergeant Alecto",
        roles=[_role(2, "Watch Sergeant")],
    )

    with patch.object(roster_embeds, "_b", return_value=["Watch Captain", "Watch Sergeant"]):
        captain_key = roster_embeds._sort_key_for_member(captain)
        sergeant_key = roster_embeds._sort_key_for_member(sergeant)

    assert captain_key == (0, "zephon")
    assert sergeant_key == (1, "alecto")


def _make_pkg(kt_name, status, pkg_id="pkg1"):
    return {pkg_id: {"id": pkg_id, "assigned_kt": kt_name, "status": status}}


def test_tp_status_for_kt_no_packages_returns_ready():
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages={})
    assert result == "-# 🟢 Ready for Deployment"


def test_tp_status_for_kt_pending_sgt_shows_assigned():
    packages = _make_pkg("Kill Team Alpha", "pending_sgt")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "-# 🟡 Assigned (1 directive)"


def test_tp_status_for_kt_recruiting_shows_assigned():
    packages = _make_pkg("Kill Team Alpha", "recruiting")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "-# 🟡 Assigned (1 directive)"


def test_tp_status_for_kt_deployed_shows_deployed():
    packages = _make_pkg("Kill Team Alpha", "deployed")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "-# 🔴 Deployed (1 directive)"


def test_tp_status_for_kt_multiple_packages_plural():
    packages = {
        "pkg1": {"id": "pkg1", "assigned_kt": "Kill Team Alpha", "status": "deployed"},
        "pkg2": {"id": "pkg2", "assigned_kt": "Kill Team Alpha", "status": "recruiting"},
    }
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "-# 🔴 Deployed (2 directives)"


def test_tp_status_for_kt_ignores_other_kts():
    packages = _make_pkg("Kill Team Bravo", "deployed")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "-# 🟢 Ready for Deployment"


# ---------------------------------------------------------------------------
# Honors title helpers
# ---------------------------------------------------------------------------

def test_honors_title_for_kt_returns_empty_when_no_honors_file():
    with patch.object(roster_embeds.os.path, "exists", return_value=False):
        result = roster_embeds._honors_title_for_kt("Kill Team Alpha")
    assert result.startswith("-# ")
    assert "**Unproven**" in result


def test_honors_title_for_kt_returns_empty_for_unknown_kt():
    honors = {"kill_teams": {}, "companies": {}}
    result = roster_embeds._honors_title_for_kt("Kill Team Alpha", honors=honors)
    assert result.startswith("-# ")
    assert "**Unproven**" in result


def test_honors_title_for_kt_returns_empty_for_unproven():
    honors = {"kill_teams": {"Kill Team Alpha": {"tier": "Unproven"}}, "companies": {}}
    result = roster_embeds._honors_title_for_kt("Kill Team Alpha", honors=honors)
    assert result.startswith("-# ")
    assert "**Unproven**" in result


def test_honors_title_for_kt_returns_formatted_tier():
    honors = {"kill_teams": {"Kill Team Alpha": {"tier": "Initiated"}}, "companies": {}}
    result = roster_embeds._honors_title_for_kt("Kill Team Alpha", honors=honors)
    assert result.startswith("-# ")
    assert "**Initiated**" in result


def test_honors_title_for_company_returns_empty_for_unrecorded():
    honors = {"kill_teams": {}, "companies": {"Watch Company Primus": {"tier": "Unrecorded"}}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result.startswith("-# ")
    assert "**Unrecorded**" in result


def test_honors_title_for_company_returns_empty_for_unknown_company():
    honors = {"kill_teams": {}, "companies": {}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result.startswith("-# ")
    assert "**Unrecorded**" in result


def test_honors_title_for_company_returns_formatted_tier():
    honors = {"kill_teams": {}, "companies": {"Watch Company Primus": {"tier": "Marked"}}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result.startswith("-# ")
    assert "**Marked**" in result


def test_load_honors_returns_empty_dict_when_file_missing():
    with patch.object(roster_embeds.os.path, "exists", return_value=False):
        result = roster_embeds._load_honors()
    assert result == {}


# ---------------------------------------------------------------------------
# _fortress_rep_state_name
# ---------------------------------------------------------------------------

def test_fortress_rep_state_name_zero_is_censured():
    """rep=0 must yield CENSURED, not default to NEUTRAL via falsy check."""
    assert roster_embeds._fortress_rep_state_name(0) == "CENSURED"
    assert roster_embeds._fortress_rep_state_name(0.0) == "CENSURED"


def test_fortress_rep_state_name_bands():
    assert roster_embeds._fortress_rep_state_name(5.0) == "CENSURED"
    assert roster_embeds._fortress_rep_state_name(10.0) == "SUSPECT"
    assert roster_embeds._fortress_rep_state_name(15.0) == "SUSPECT"
    assert roster_embeds._fortress_rep_state_name(20.0) == "TOLERATED"
    assert roster_embeds._fortress_rep_state_name(25.0) == "TOLERATED"
    assert roster_embeds._fortress_rep_state_name(30.0) == "NEUTRAL"
    assert roster_embeds._fortress_rep_state_name(35.0) == "NEUTRAL"
    assert roster_embeds._fortress_rep_state_name(40.0) == "FAVOURED"
    assert roster_embeds._fortress_rep_state_name(45.0) == "FAVOURED"
    assert roster_embeds._fortress_rep_state_name(50.0) == "ENDORSED"
    assert roster_embeds._fortress_rep_state_name(55.0) == "ENDORSED"
    assert roster_embeds._fortress_rep_state_name(58.0) == "MANDATED"
    assert roster_embeds._fortress_rep_state_name(60.0) == "MANDATED"


def test_fortress_rep_state_name_none_defaults_to_neutral():
    """None should be treated as missing and fall back to 30.0 (NEUTRAL)."""
    assert roster_embeds._fortress_rep_state_name(None) == "NEUTRAL"


def test_fortress_rep_state_name_empty_string_defaults_to_neutral():
    """Empty string should fall back to 30.0 (NEUTRAL)."""
    assert roster_embeds._fortress_rep_state_name("") == "NEUTRAL"


# ---------------------------------------------------------------------------
# _fortress_rep_title
# ---------------------------------------------------------------------------

def test_fortress_rep_title_format():
    result = roster_embeds._fortress_rep_title({"rep": 30.0})
    assert "Fortress Standing" in result
    assert "NEUTRAL" in result
    assert "30.0/60" in result
    assert "[" in result and "]" in result


def test_fortress_rep_title_rep_zero_is_censured():
    """Stored rep=0 must render as CENSURED, not default to NEUTRAL."""
    result = roster_embeds._fortress_rep_title({"rep": 0})
    assert "CENSURED" in result
    assert "0.0/60" in result


def test_fortress_rep_title_none_tp_data_uses_neutral():
    result = roster_embeds._fortress_rep_title(None)
    assert "NEUTRAL" in result
    assert "30.0/60" in result


def test_fortress_rep_title_missing_rep_key_uses_neutral():
    result = roster_embeds._fortress_rep_title({})
    assert "NEUTRAL" in result
    assert "30.0/60" in result


def test_fortress_rep_title_none_rep_value_uses_neutral():
    """Explicit None stored as rep value should fall back to 30.0."""
    result = roster_embeds._fortress_rep_title({"rep": None})
    assert "NEUTRAL" in result
    assert "30.0/60" in result


def test_sectioned_embed_builds_field_sections_and_description_lines():
    captain = _member(member_id=1, display_name="Captain One", roles=[])
    lieutenant = _member(member_id=2, display_name="Lieutenant Two", roles=[])
    embed = roster_embeds._build_sectioned_embed(
        "<@&123>",
        [("Company Command", [captain, lieutenant], ["Status line"])],
        guild=SimpleNamespace(members=[captain, lieutenant]),
        image_url=None,
        description_lines=["Honor line"],
    )

    assert embed.description == "<@&123>\nHonor line"
    assert len(embed.fields) == 1
    assert embed.fields[0].name == "▸ Company Command"
    assert embed.fields[0].value.startswith("Status line\n**2 Brothers Assigned**")
    assert "<@1>" in embed.fields[0].value
    assert "<@2>" in embed.fields[0].value


def test_lord_executioner_specialist_group_contains_both_champion_roles():
    groups = dict(roster_embeds._SPECIALIST_SECTION_ROLE_GROUPS)
    assert "Champion Cadre" in groups
    assert "Company Champion" in groups["Champion Cadre"]
    assert "Kill Team Champion" in groups["Champion Cadre"]


def test_mention_style_label_prefixes_at_symbol_for_embed_field_names():
    assert roster_embeds._mention_style_label("Kill Team Jason") == "@Kill Team Jason"
    assert roster_embeds._mention_style_label("@Kill Team Jason") == "@Kill Team Jason"
