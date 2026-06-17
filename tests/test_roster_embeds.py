from types import SimpleNamespace
from unittest.mock import patch

import opscribe.bot  # noqa: F401 – initialises _g.bot before forge_ops is imported
import opscribe.roster_embeds as roster_embeds


def _member(*, member_id=1, nick=None, display_name=None, name=None, roles=None):
    return SimpleNamespace(
        id=member_id,
        nick=nick,
        display_name=display_name,
        name=name,
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
    assert result == "🟢 Ready for Deployment"


def test_tp_status_for_kt_pending_sgt_shows_assigned():
    packages = _make_pkg("Kill Team Alpha", "pending_sgt")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "🟡 Assigned (1 pkg)"


def test_tp_status_for_kt_recruiting_shows_assigned():
    packages = _make_pkg("Kill Team Alpha", "recruiting")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "🟡 Assigned (1 pkg)"


def test_tp_status_for_kt_deployed_shows_deployed():
    packages = _make_pkg("Kill Team Alpha", "deployed")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "🔴 Deployed (1 pkg)"


def test_tp_status_for_kt_multiple_packages_plural():
    packages = {
        "pkg1": {"id": "pkg1", "assigned_kt": "Kill Team Alpha", "status": "deployed"},
        "pkg2": {"id": "pkg2", "assigned_kt": "Kill Team Alpha", "status": "recruiting"},
    }
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "🔴 Deployed (2 pkgs)"


def test_tp_status_for_kt_ignores_other_kts():
    packages = _make_pkg("Kill Team Bravo", "deployed")
    result = roster_embeds._tp_status_for_kt("Kill Team Alpha", packages=packages)
    assert result == "🟢 Ready for Deployment"


# ---------------------------------------------------------------------------
# Honors title helpers
# ---------------------------------------------------------------------------

def test_honors_title_for_kt_returns_empty_when_no_honors_file():
    with patch.object(roster_embeds.os.path, "exists", return_value=False):
        result = roster_embeds._honors_title_for_kt("Kill Team Alpha")
    assert result == ""


def test_honors_title_for_kt_returns_empty_for_unknown_kt():
    honors = {"kill_teams": {}, "companies": {}}
    result = roster_embeds._honors_title_for_kt("Kill Team Alpha", honors=honors)
    assert result == ""


def test_honors_title_for_kt_returns_empty_for_unproven():
    honors = {"kill_teams": {"Kill Team Alpha": {"tier": "Unproven"}}, "companies": {}}
    result = roster_embeds._honors_title_for_kt("Kill Team Alpha", honors=honors)
    assert result == ""


def test_honors_title_for_kt_returns_formatted_tier():
    honors = {"kill_teams": {"Kill Team Alpha": {"tier": "Stalwart"}}, "companies": {}}
    result = roster_embeds._honors_title_for_kt("Kill Team Alpha", honors=honors)
    assert result == "🎖 **Stalwart**"


def test_honors_title_for_company_returns_empty_for_unrecorded():
    honors = {"kill_teams": {}, "companies": {"Watch Company Primus": {"tier": "Unrecorded"}}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result == ""


def test_honors_title_for_company_returns_empty_for_unknown_company():
    honors = {"kill_teams": {}, "companies": {}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result == ""


def test_honors_title_for_company_returns_formatted_tier():
    honors = {"kill_teams": {}, "companies": {"Watch Company Primus": {"tier": "Renowned"}}}
    result = roster_embeds._honors_title_for_company("Watch Company Primus", honors=honors)
    assert result == "🏅 **Renowned**"


def test_load_honors_returns_empty_dict_when_file_missing():
    with patch.object(roster_embeds.os.path, "exists", return_value=False):
        result = roster_embeds._load_honors()
    assert result == {}
