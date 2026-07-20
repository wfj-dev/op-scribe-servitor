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
    discord_stub.abc = types.SimpleNamespace(User=object, Messageable=object, GuildChannel=object, MessageableChannel=object)
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


def test_roster_company_channels_includes_tertius():
    assert roster_embeds.ROSTER_COMPANY_CHANNELS["Watch Company Tertius"] == 1527777716076548216


def test_configured_roster_company_channels_prefers_config_values():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "companies": {
                "tertius": {"name": "Tertius", "rosterChannelId": "1527777716076548216"},
                "quartus": {"name": "Quartus", "rosterChannelId": "1999"},
            }
        }
        if name == "CONFIG"
        else None,
    ):
        channels = roster_embeds._configured_roster_company_channels()

    assert channels == {
        "Watch Company Tertius": 1527777716076548216,
        "Watch Company Quartus": 1999,
    }


def test_company_command_image_filename_uses_config_override():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "companies": {
                "quartus": {"name": "Quartus", "commandImageAsset": "Quartus Special.png"},
            }
        }
        if name == "CONFIG"
        else None,
    ):
        assert roster_embeds._company_command_image_filename("Watch Company Quartus") == "Quartus Special.png"


def test_company_command_image_filename_uses_convention_then_generic():
    with patch.object(roster_embeds, "_b", lambda _name: {"companies": {}}), patch.object(
        roster_embeds.os.path,
        "exists",
        side_effect=lambda path: path.endswith("quartus command.png"),
    ):
        assert roster_embeds._company_command_image_filename("Watch Company Quartus") == "quartus command.png"
        assert roster_embeds._company_command_image_filename("Watch Company Quintus") == "Command.png"


def test_company_banner_image_filenames_uses_company_then_art_convention():
    with patch.object(roster_embeds, "_b", lambda _name: {"companies": {}}), patch.object(
        roster_embeds.os.path,
        "exists",
        return_value=False,
    ):
        assert roster_embeds._company_banner_image_filenames("Watch Company Primus") == [
            "primus company.png",
            "primus art.png",
        ]


def test_company_banner_image_filenames_prefers_config_overrides():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "companies": {
                "primus": {
                    "name": "Primus",
                    "companyImageAsset": "primus crest override.png",
                    "companyArtImageAsset": "primus mural override.png",
                }
            }
        }
        if name == "CONFIG"
        else None,
    ):
        assert roster_embeds._company_banner_image_filenames("Watch Company Primus") == [
            "primus crest override.png",
            "primus mural override.png",
        ]


def test_kill_team_image_filename_prefers_role_id_config_mapping():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "target_packages": {
                "kt_role_image_assets": {
                    "1458254904819974386": "Kill Team Duke.png",
                }
            }
        }
        if name == "CONFIG"
        else None,
    ):
        assert (
            roster_embeds._kill_team_image_filename(
                "Kill Team Whatever",
                1458254904819974386,
            )
            == "Kill Team Duke.png"
        )


def test_kill_team_image_filename_role_id_falls_back_to_name_convention_when_unmapped():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {"target_packages": {"kt_role_image_assets": {}}} if name == "CONFIG" else None,
    ):
        assert roster_embeds._kill_team_image_filename("Kill Team Devito", 1433355179020914688) == "Kill Team Devito.png"


def test_asset_path_prefers_roster_images_directory_when_present():
    with patch.object(
        roster_embeds.os.path,
        "exists",
        side_effect=lambda path: path.replace("\\", "/").endswith("assets/roster images/Kill Team Duke.png"),
    ):
        resolved = roster_embeds._asset_path("Kill Team Duke.png")

    assert resolved.replace("\\", "/").endswith("assets/roster images/Kill Team Duke.png")


def test_asset_path_falls_back_to_assets_root_when_roster_images_missing():
    with patch.object(
        roster_embeds.os.path,
        "exists",
        side_effect=lambda path: path.replace("\\", "/").endswith("assets/Kill Team Duke.png"),
    ):
        resolved = roster_embeds._asset_path("Kill Team Duke.png")

    assert resolved.replace("\\", "/").endswith("assets/Kill Team Duke.png")


def test_configured_cadre_section_image_assets_accepts_case_insensitive_section_keys():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "target_packages": {
                "cadre_section_image_assets": {
                    "watch armory": "Watch Armory Special.png",
                    "Librarius": "Librarius Special.png",
                }
            }
        }
        if name == "CONFIG"
        else None,
    ):
        configured = roster_embeds._configured_cadre_section_image_assets()

    assert configured == {
        "Watch Armory": "Watch Armory Special.png",
        "Librarius": "Librarius Special.png",
    }


def test_configured_cadre_section_image_assets_accepts_hall_of_blades_alias():
    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "target_packages": {
                "cadre_section_image_assets": {
                    "Hall of Blades": "hall of blades.png",
                }
            }
        }
        if name == "CONFIG"
        else None,
    ):
        configured = roster_embeds._configured_cadre_section_image_assets()

    assert configured == {"Blade Hall": "hall of blades.png"}


def test_specialist_image_filename_prefers_section_override_over_role_mapping():
    guild = SimpleNamespace(
        roles=[
            SimpleNamespace(id=1455226254897975389, name="Watch Librarian"),
        ]
    )

    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "target_packages": {
                "cadre_section_image_assets": {
                    "Librarius": "Librarius Section Override.png",
                },
                "cadre_role_image_assets": {
                    "1455226254897975389": "Librarius Role Override.png",
                },
            }
        }
        if name == "CONFIG"
        else None,
    ):
        image_name = roster_embeds._specialist_image_filename("Librarius", {"Watch Librarian"}, guild)

    assert image_name == "Librarius Section Override.png"


def test_specialist_image_filename_uses_role_mapping_when_no_section_override():
    guild = SimpleNamespace(
        roles=[
            SimpleNamespace(id=1455226254897975389, name="Watch Librarian"),
        ]
    )

    with patch.object(
        roster_embeds,
        "_b",
        lambda name: {
            "target_packages": {
                "cadre_section_image_assets": {},
                "cadre_role_image_assets": {
                    "1455226254897975389": "Librarius Role Override.png",
                },
            }
        }
        if name == "CONFIG"
        else None,
    ):
        image_name = roster_embeds._specialist_image_filename("Librarius", {"Watch Librarian"}, guild)

    assert image_name == "Librarius Role Override.png"


def _member(*, member_id=1, nick=None, display_name=None, name=None, roles=None):
    return SimpleNamespace(
        id=member_id,
        bot=False,
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
        "_configured_roster_company_channels",
        lambda: {"Watch Company Primus": 11, "Watch Company Secundus": 22},
    ):
        state = roster_embeds._load_roster_state()

    assert state == {
        "Watch Company Primus": {
            "channel_id": 11,
            "company_message_id": None,
            "company_art_message_id": None,
            "hc_message_id": None,
            "specialist_message_id": None,
            "specialist_message_ids": {},
            "command_message_id": None,
            "killteam_message_ids": {},
        },
        "Watch Company Secundus": {
            "channel_id": 22,
            "company_message_id": None,
            "company_art_message_id": None,
            "hc_message_id": None,
            "specialist_message_id": None,
            "specialist_message_ids": {},
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
        "_configured_roster_company_channels",
        lambda: {"Watch Company Primus": 11, "Watch Company Secundus": 22},
    ):
        state = roster_embeds._load_roster_state()

    assert state == {
        "Watch Company Primus": {
            "channel_id": 999,
            "company_message_id": None,
            "company_art_message_id": None,
            "hc_message_id": 101,
            "specialist_message_id": None,
            "specialist_message_ids": {},
            "command_message_id": None,
            "killteam_message_ids": {"Kill Team Alpha": 202},
        },
        "Watch Company Secundus": {
            "channel_id": 22,
            "company_message_id": None,
            "company_art_message_id": None,
            "hc_message_id": None,
            "specialist_message_id": None,
            "specialist_message_ids": {},
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


def test_honors_title_for_company_maps_legacy_unrecorded_to_shared_tier():
    honors = {"kill_teams": {}, "companies": {"Watch Company Primus": {"tier": "Unrecorded"}}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result.startswith("-# ")
    assert "**Unproven**" in result


def test_honors_title_for_company_returns_shared_default_for_unknown_company():
    honors = {"kill_teams": {}, "companies": {}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result.startswith("-# ")
    assert "**Unproven**" in result


def test_honors_title_for_company_returns_formatted_tier():
    honors = {"kill_teams": {}, "companies": {"Watch Company Primus": {"tier": "Marked"}}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result.startswith("-# ")
    assert "**Initiated**" in result


def test_honors_title_for_cadre_maps_legacy_tier_to_shared_name():
    honors = {"kill_teams": {}, "companies": {}, "cadres": {"Armory": {"tier": "Tempered"}}}
    result = roster_embeds._honors_title_for_cadre("Watch Armory", honors=honors)
    assert result is not None
    assert "**Initiated**" in result


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
    assert roster_embeds._fortress_rep_state_name(17.0) == "SUSPECT"
    assert roster_embeds._fortress_rep_state_name(30.0) == "SUSPECT"
    assert roster_embeds._fortress_rep_state_name(34.0) == "TOLERATED"
    assert roster_embeds._fortress_rep_state_name(45.0) == "TOLERATED"
    assert roster_embeds._fortress_rep_state_name(50.0) == "NEUTRAL"
    assert roster_embeds._fortress_rep_state_name(60.0) == "NEUTRAL"
    assert roster_embeds._fortress_rep_state_name(67.0) == "FAVOURED"
    assert roster_embeds._fortress_rep_state_name(80.0) == "FAVOURED"
    assert roster_embeds._fortress_rep_state_name(84.0) == "ENDORSED"
    assert roster_embeds._fortress_rep_state_name(95.0) == "ENDORSED"
    assert roster_embeds._fortress_rep_state_name(97.0) == "MANDATED"
    assert roster_embeds._fortress_rep_state_name(100.0) == "MANDATED"


def test_fortress_rep_state_name_none_defaults_to_neutral():
    """None should be treated as missing and fall back to 50.0 (NEUTRAL)."""
    assert roster_embeds._fortress_rep_state_name(None) == "NEUTRAL"


def test_fortress_rep_state_name_empty_string_defaults_to_neutral():
    """Empty string should fall back to 50.0 (NEUTRAL)."""
    assert roster_embeds._fortress_rep_state_name("") == "NEUTRAL"


# ---------------------------------------------------------------------------
# _fortress_rep_title
# ---------------------------------------------------------------------------

def test_fortress_rep_title_format():
    result = roster_embeds._fortress_rep_title({"rep": 50.0})
    assert result.startswith("-# ")
    assert "NEUTRAL" in result
    assert "50.0/100" in result
    assert "[" in result and "]" in result


def test_fortress_rep_title_rep_zero_is_censured():
    """Stored rep=0 must render as CENSURED, not default to NEUTRAL."""
    result = roster_embeds._fortress_rep_title({"rep": 0})
    assert "CENSURED" in result
    assert "0.0/100" in result


def test_fortress_rep_title_none_tp_data_uses_neutral():
    result = roster_embeds._fortress_rep_title(None)
    assert "NEUTRAL" in result
    assert "50.0/100" in result


def test_fortress_rep_title_missing_rep_key_uses_neutral():
    result = roster_embeds._fortress_rep_title({})
    assert "NEUTRAL" in result
    assert "50.0/100" in result


def test_fortress_rep_title_none_rep_value_uses_neutral():
    """Explicit None stored as rep value should fall back to 50.0."""
    result = roster_embeds._fortress_rep_title({"rep": None})
    assert "NEUTRAL" in result
    assert "50.0/100" in result


def test_sectioned_embed_builds_field_sections_and_description_lines():
    captain = _member(member_id=1, display_name="Captain One", roles=[])
    lieutenant = _member(member_id=2, display_name="Lieutenant Two", roles=[])
    embed = roster_embeds._build_sectioned_embed(
        "<@&123>",
        [(roster_embeds._EMPTY_FIELD_NAME, [captain, lieutenant], ["<@&456>", "Status line"])],
        guild=SimpleNamespace(members=[captain, lieutenant]),
        image_url=None,
        description_lines=["Honor line"],
    )

    assert embed.description == "<@&123>\nHonor line"
    assert len(embed.fields) == 1
    assert embed.fields[0].name == roster_embeds._EMPTY_FIELD_NAME
    assert embed.fields[0].value.startswith("<@&456>\nStatus line\n**2 Brothers Assigned**")
    assert "<@1>" in embed.fields[0].value
    assert "<@2>" in embed.fields[0].value


def test_sectioned_embed_supports_watch_master_and_cadre_leaders_fields():
    watch_master = _member(member_id=3, display_name="Watch Master", roles=[])
    captain = _member(member_id=1, display_name="Captain One", roles=[])
    lieutenant = _member(member_id=2, display_name="Lieutenant Two", roles=[])
    embed = roster_embeds._build_sectioned_embed(
        "<@&123>",
        [
            ("Watch Master", [watch_master]),
            ("Cadre Leaders", [captain, lieutenant]),
        ],
        guild=SimpleNamespace(members=[watch_master, captain, lieutenant]),
        image_url=None,
        description_lines=["Honor line"],
    )

    assert [field.name for field in embed.fields] == ["▸ Watch Master", "▸ Cadre Leaders"]
    assert "<@3>" in embed.fields[0].value
    assert "<@1>" in embed.fields[1].value
    assert "<@2>" in embed.fields[1].value


def test_blademaster_specialist_group_contains_both_blade_roles():
    groups = dict(roster_embeds._SPECIALIST_SECTION_ROLE_GROUPS)
    assert "Blade Hall" in groups
    assert "First Blade" in groups["Blade Hall"]
    assert "Bladeguard" in groups["Blade Hall"]


def test_blade_hall_specialist_group_supports_champion_track_roles():
    groups = dict(roster_embeds._SPECIALIST_SECTION_ROLE_GROUPS)
    blade_hall = groups["Blade Hall"]
    assert "Company Champion" in blade_hall
    assert "Kill Team Champion" in blade_hall
    assert "Lord Executioner" in blade_hall


def test_get_hc_members_includes_watch_captain_and_excludes_reserves():
    captain = _member(
        member_id=30,
        display_name="Captain Primus",
        roles=[
            _role(roster_embeds.HIGH_COMMAND_ROLE_ID, "High Command"),
            _role(2, "Watch Captain"),
        ],
    )
    watch_master = _member(
        member_id=31,
        display_name="Watch Master",
        roles=[_role(3, "Watch Master")],
    )
    reserve_captain = _member(
        member_id=32,
        display_name="Reserve Captain",
        roles=[
            _role(roster_embeds.HIGH_COMMAND_ROLE_ID, "High Command"),
            _role(4, "Watch Captain"),
            _role(roster_embeds.RESERVES_ROLE_ID, "Reserves"),
        ],
    )
    non_hc = _member(
        member_id=33,
        display_name="Line Brother",
        roles=[_role(5, "Watch Brother")],
    )
    guild = SimpleNamespace(members=[captain, watch_master, reserve_captain, non_hc])

    members = roster_embeds._get_hc_members(guild)

    assert [m.id for m in members] == [31, 30]


def test_get_hc_members_orders_watch_master_then_captains_by_company_then_remaining_hc():
    watch_master = _member(
        member_id=1,
        display_name="Watch Master",
        roles=[_role(100, "Watch Master")],
    )
    captain_secundus = _member(
        member_id=2,
        display_name="Captain Secundus",
        roles=[
            _role(roster_embeds.HIGH_COMMAND_ROLE_ID, "High Command"),
            _role(101, "Watch Captain"),
            _role(102, "Watch Company Secundus"),
        ],
    )
    captain_primus = _member(
        member_id=3,
        display_name="Captain Primus",
        roles=[
            _role(roster_embeds.HIGH_COMMAND_ROLE_ID, "High Command"),
            _role(103, "Watch Captain"),
            _role(104, "Watch Company Primus"),
        ],
    )
    blademaster = _member(
        member_id=4,
        display_name="Blade Master",
        roles=[
            _role(roster_embeds.HIGH_COMMAND_ROLE_ID, "High Command"),
            _role(105, "Blade Master"),
        ],
    )
    first_blade = _member(
        member_id=5,
        display_name="First Blade",
        roles=[
            _role(roster_embeds.HIGH_COMMAND_ROLE_ID, "High Command"),
            _role(106, "First Blade"),
        ],
    )
    guild = SimpleNamespace(members=[first_blade, captain_secundus, watch_master, blademaster, captain_primus])

    members = roster_embeds._get_hc_members(guild)

    assert [m.id for m in members] == [1, 3, 2, 4, 5]


def test_blade_hall_sort_key_orders_role_bands_top_mid_bottom():
    bladeguard = _member(member_id=1, display_name="Bladeguard", roles=[_role(10, "Bladeguard")])
    first_blade = _member(member_id=2, display_name="First Blade", roles=[_role(11, "First Blade")])
    blademaster = _member(member_id=3, display_name="Blade Master", roles=[_role(12, "Blade Master")])
    company_champion = _member(member_id=4, display_name="Company Champion", roles=[_role(13, "Company Champion")])
    kt_champion = _member(member_id=5, display_name="Kill Team Champion", roles=[_role(14, "Kill Team Champion")])
    lord_executioner = _member(member_id=6, display_name="Lord Executioner", roles=[_role(15, "Lord Executioner")])

    members = [bladeguard, first_blade, blademaster, company_champion, kt_champion, lord_executioner]
    ordered = sorted(members, key=roster_embeds._blade_hall_sort_key)

    top_ids = {m.id for m in ordered[:2]}
    mid_ids = {m.id for m in ordered[2:4]}
    bottom_ids = {m.id for m in ordered[4:6]}

    assert top_ids == {3, 6}
    assert mid_ids == {2, 4}
    assert bottom_ids == {1, 5}


def test_blade_hall_sort_key_keeps_blademaster_before_first_blade():
    first_blade = _member(member_id=21, display_name="First Blade", roles=[_role(201, "First Blade")])
    blademaster = _member(member_id=22, display_name="Blade Master", roles=[_role(202, "Blade Master")])

    ordered = sorted([first_blade, blademaster], key=roster_embeds._blade_hall_sort_key)

    assert [m.id for m in ordered] == [22, 21]


def test_blade_hall_sort_key_treats_blade_master_alias_as_top_tier():
    first_blade = _member(member_id=31, display_name="First Blade", roles=[_role(301, "First Blade")])
    blade_master = _member(member_id=32, display_name="Blade Master", roles=[_role(302, "Blade Master")])

    ordered = sorted([first_blade, blade_master], key=roster_embeds._blade_hall_sort_key)

    assert [m.id for m in ordered] == [32, 31]


def test_get_company_champion_members_filters_by_company_and_role():
    champion_primus = _member(
        member_id=20,
        display_name="Champion Primus",
        roles=[_role(1, "Watch Company Primus"), _role(2, "First Blade")],
    )
    champion_other_company = _member(
        member_id=21,
        display_name="Champion Secundus",
        roles=[_role(3, "Watch Company Secundus"), _role(4, "First Blade")],
    )
    not_champion = _member(
        member_id=22,
        display_name="Captain Primus",
        roles=[_role(5, "Watch Company Primus"), _role(6, "Watch Captain")],
    )

    guild = SimpleNamespace(members=[champion_primus, champion_other_company, not_champion])

    members = roster_embeds._get_company_champion_members(guild, "Watch Company Primus")

    assert [m.id for m in members] == [20]


def test_mention_style_label_prefixes_at_symbol_for_embed_field_names():
    assert roster_embeds._mention_style_label("Kill Team Jason") == "@Kill Team Jason"
    assert roster_embeds._mention_style_label("@Kill Team Jason") == "@Kill Team Jason"


def test_role_mention_uses_role_id_when_available_and_fallback_otherwise():
    assert roster_embeds._role_mention(12345, fallback="ignored") == "<@&12345>"
    assert roster_embeds._role_mention(None, fallback="@Kill Team Jason") == "@Kill Team Jason"


def test_tp_status_for_members_aggregates_active_directives():
    packages = {
        "pkg1": {"id": "pkg1", "status": "deployed", "signed_up": [10]},
        "pkg2": {"id": "pkg2", "status": "recruiting", "assigned_specialist_ids": [10]},
    }
    assert roster_embeds._tp_status_for_members({10}, packages=packages) == "-# 🔴 Deployed (2 directives)"


def test_normalize_member_casing_mixed_case_words():
    assert roster_embeds._normalize_member_casing("WATCH MAsTER VAN") == "Watch Master Van"


def test_normalize_member_casing_all_lower():
    assert roster_embeds._normalize_member_casing("watch master") == "Watch Master"


def test_normalize_member_casing_all_upper():
    assert roster_embeds._normalize_member_casing("IRON FIST") == "Iron Fist"


def test_normalize_member_casing_preserves_single_letter_initials():
    # Single-letter tokens like "D." should be forced to uppercase.
    assert roster_embeds._normalize_member_casing("D. grimm") == "D. Grimm"


def test_normalize_member_casing_preserves_hyphenated_names():
    # Hyphens are left untouched; each alphabetic run is cased independently.
    assert roster_embeds._normalize_member_casing("GRIMM-KNIGHT") == "Grimm-Knight"


def test_normalize_member_casing_preserves_apostrophes():
    assert roster_embeds._normalize_member_casing("d'AMORE") == "D'Amore"


def test_normalize_member_casing_empty_string():
    assert roster_embeds._normalize_member_casing("") == ""


def test_normalize_member_casing_non_alpha_only():
    # Strings with no alpha characters are returned unchanged.
    assert roster_embeds._normalize_member_casing("123 ·|·") == "123 ·|·"

