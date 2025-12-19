from datetime import datetime

from bot import parse_aar, validate_aar

class FakeUser:
    def __init__(self, id, name, nick=None):
        self.id = id
        self.name = name
        self.nick = nick

class FakeRole:
    def __init__(self, id, name):
        self.id = id
        self.name = name

class FakeMessage:
    def __init__(self, content, mentions=None, role_mentions=None):
        self.content = content
        self.mentions = mentions or []
        self.role_mentions = role_mentions or []
        self.created_at = datetime.utcnow()
        self.edited_at = None
        self.id = 999999
        self.jump_url = "https://discord.example/jump"


def test_parse_and_validate_basic_stratagem():
    # Build basic message content
    u1 = FakeUser(101, "Alpha", nick="Alpha")
    u2 = FakeUser(102, "Beta", nick="Beta")
    r1 = FakeRole(501, "Normal-Stratagem")

    content = (
        "++ MISSION REPORT ++\n"
        "Mission: Test Operation\n"
        f"Difficulty: <@&{r1.id}>\n"
        "Armory Data: 2\n"
        "Brothers:\n"
        f" - <@{u1.id}>\n"
        f" - <@{u2.id}>\n"
        "++ END OF REPORT ++\n"
    )

    msg = FakeMessage(content, mentions=[u1, u2], role_mentions=[r1])
    rec = parse_aar(msg)
    assert rec is not None
    errs = validate_aar(rec)
    assert errs == []
