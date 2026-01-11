import types

from bot import RANK_ROLES_PRIORITY, _role_index, is_sergeant_or_higher, can_reconcile_records, is_high_command

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

def test_sergeant_or_higher_threshold():
    member_brother = FakeMember(1001, [FakeRole("Watch Brother")])
    member_sergeant = FakeMember(1002, [FakeRole("Watch Sergeant")])
    member_captain = FakeMember(1003, [FakeRole("Watch Captain")])

    assert not is_sergeant_or_higher(member_brother)
    assert is_sergeant_or_higher(member_sergeant)
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
