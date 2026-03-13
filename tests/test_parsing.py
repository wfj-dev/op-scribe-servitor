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


INITIATION_TRIAL_ROLE_ID = 1434942334914662501
WATCH_COMMAND_ROLE_ID = 1429281421931057283


def _make_initiation_trial_message(include_watch_command: bool):
    """Build a FakeMessage representing an Initiation Trial AAR."""
    brother = FakeUser(201, "Veteran", nick="Veteran")
    initiate = FakeUser(202, "Neophyte", nick="Neophyte")
    difficulty_role = FakeRole(501, "Normal-Stratagem")
    initiation_trial_role = FakeRole(INITIATION_TRIAL_ROLE_ID, "Initiation Trial")

    role_mentions = [difficulty_role, initiation_trial_role]
    if include_watch_command:
        watch_command_role = FakeRole(WATCH_COMMAND_ROLE_ID, "Watch Command")
        role_mentions.append(watch_command_role)

    watch_command_line = (
        f"<@&{WATCH_COMMAND_ROLE_ID}>\n" if include_watch_command else ""
    )

    content = (
        "++ MISSION REPORT ++\n"
        "Mission: Inferno\n"
        f"Difficulty: <@&{difficulty_role.id}>\n"
        "Armory Data: 2\n"
        f"<@&{INITIATION_TRIAL_ROLE_ID}> <@{initiate.id}>\n"
        f"{watch_command_line}"
        "Brothers:\n"
        f" - <@{brother.id}>\n"
        f" - <@{initiate.id}>\n"
        "++ END OF REPORT ++\n"
    )

    return FakeMessage(
        content,
        mentions=[brother, initiate],
        role_mentions=role_mentions,
    )


def test_initiation_trial_with_watch_command_validates():
    """An Initiation Trial that mentions @Watch Command should pass the Watch Command check."""
    msg = _make_initiation_trial_message(include_watch_command=True)
    rec = parse_aar(msg)
    assert rec is not None
    assert rec.get("initiation_trial") is True
    assert rec.get("watch_command_mentioned") is True
    errs = validate_aar(rec)
    watch_command_errors = [e for e in errs if "Watch Command" in e]
    assert watch_command_errors == [], (
        "Expected no Watch Command error when @Watch Command is mentioned, "
        f"but got: {watch_command_errors}"
    )


def test_initiation_trial_without_watch_command_returns_error():
    """An Initiation Trial that omits @Watch Command should return the required mention error."""
    msg = _make_initiation_trial_message(include_watch_command=False)
    rec = parse_aar(msg)
    assert rec is not None
    assert rec.get("initiation_trial") is True
    assert rec.get("watch_command_mentioned") is False
    errs = validate_aar(rec)
    assert any("Watch Command" in e for e in errs), (
        "Expected an error about @Watch Command being required, but got: "
        + str(errs)
    )
