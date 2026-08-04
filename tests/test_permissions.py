from opscribe.bot import (
    _role_index,
    is_sergeant_or_higher,
    can_reconcile_records,
    is_high_command,
    check_command_permission,
)
import opscribe.bot as bot


class FakeRole:
    def __init__(self, name):
        self.name = name


class FakeMember:
    def __init__(self, id, roles, nick=None):
        self.id = id
        self.roles = roles
        self.nick = nick


def test_role_index_valid():
    assert _role_index("Watch Master") == 0
    assert _role_index("Watch Sergeant") is not None
    assert _role_index("Veteran Sergeant") is not None


def test_sergeant_or_higher_threshold():
    member_brother = FakeMember(1001, [FakeRole("Watch Brother")])
    member_sergeant = FakeMember(1002, [FakeRole("Watch Sergeant")])
    member_veteran_sergeant = FakeMember(1004, [FakeRole("Veteran Sergeant")])
    member_captain = FakeMember(1003, [FakeRole("Watch Captain")])

    assert not is_sergeant_or_higher(member_brother)
    assert is_sergeant_or_higher(member_sergeant)
    assert is_sergeant_or_higher(member_veteran_sergeant)
    assert is_sergeant_or_higher(member_captain)


def test_can_reconcile_records_default_roles():
    member_ok = FakeMember(2001, [FakeRole("Watch Techmarine")])
    member_no = FakeMember(2002, [FakeRole("Watch Sergeant")])

    assert can_reconcile_records(member_ok)
    assert not can_reconcile_records(member_no)


def test_is_high_command_roles():
    member_watch_master = FakeMember(3001, [FakeRole("Watch Master")])
    member_forgemaster = FakeMember(3002, [FakeRole("Forgemaster")])
    member_sergeant = FakeMember(3003, [FakeRole("Watch Sergeant")])

    assert is_high_command(member_watch_master)
    assert is_high_command(member_forgemaster)
    assert not is_high_command(member_sergeant)


def test_is_high_command_accepts_alias_role_names():
    member_forge_master = FakeMember(3004, [FakeRole("Forge Master")])
    member_hunt_master = FakeMember(3005, [FakeRole("Hunt Master")])
    member_blademaster = FakeMember(3006, [FakeRole("Blademaster")])

    assert is_high_command(member_forge_master)
    assert is_high_command(member_hunt_master)
    assert is_high_command(member_blademaster)


def test_check_command_permission_roles_accept_alias_role_name(monkeypatch):
    member = FakeMember(4001, [FakeRole("Forge Master")])
    monkeypatch.setattr(
        bot,
        "CONFIG",
        {
            "admin_user_ids": [],
            "role_aliases": {
                "Forgemaster": ["Forge Master"],
            },
            "permissions": {
                "forge_only": {"roles": ["Forgemaster"]},
            },
        },
        raising=False,
    )
    monkeypatch.setattr(bot, "DEBUG_MODE", False, raising=False)

    assert check_command_permission(member, "forge_only")


def test_check_command_permission_min_rank_accepts_alias_role_name(monkeypatch):
    member = FakeMember(4002, [FakeRole("Blademaster")])
    monkeypatch.setattr(
        bot,
        "CONFIG",
        {
            "admin_user_ids": [],
            "role_aliases": {
                "Blade Master": ["Blademaster"],
            },
            "permissions": {
                "champion_gate": {"min_rank": "Blade Master"},
            },
        },
        raising=False,
    )
    monkeypatch.setattr(bot, "DEBUG_MODE", False, raising=False)

    assert check_command_permission(member, "champion_gate")


def test_check_command_permission_default_roles_accept_alias_role_name(monkeypatch):
    member = FakeMember(4003, [FakeRole("Hunt Master")])
    monkeypatch.setattr(
        bot,
        "CONFIG",
        {
            "admin_user_ids": [],
            "role_aliases": {
                "Huntmaster": ["Hunt Master"],
            },
            "permissions": {
                "_default": {"roles": ["Huntmaster"]},
            },
        },
        raising=False,
    )
    monkeypatch.setattr(bot, "DEBUG_MODE", False, raising=False)

    assert check_command_permission(member, "unknown_command")
