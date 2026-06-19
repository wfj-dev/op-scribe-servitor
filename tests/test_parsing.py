from datetime import datetime

from opscribe.bot import parse_aar, validate_aar


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
        "Mission: Inferno\n"
        "Rank: A\n"
        f"Difficulty: <@&{r1.id}>\n"
        f"Gene-seed: <@{u1.id}>\n"
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

    watch_command_line = f"<@&{WATCH_COMMAND_ROLE_ID}>\n" if include_watch_command else ""

    content = (
        "++ MISSION REPORT ++\n"
        "Mission: Inferno\n"
        "Rank: B\n"
        f"Difficulty: <@&{difficulty_role.id}>\n"
        f"<@&{INITIATION_TRIAL_ROLE_ID}> <@{initiate.id}>\n"
        f"{watch_command_line}\n"
        f"Gene-seed: <@{brother.id}>\n"
        "Armory Data: 2\n"
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
        f"Expected no Watch Command error when @Watch Command is mentioned, but got: {watch_command_errors}"
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
        "Expected an error about @Watch Command being required, but got: " + str(errs)
    )


def _make_omega_message(include_kia_line: bool, kia_value: int = 0):
    """Build a FakeMessage representing an Omega difficulty AAR."""
    u1 = FakeUser(301, "BrotherA", nick="BrotherA")
    u2 = FakeUser(302, "BrotherB", nick="BrotherB")
    u3 = FakeUser(303, "BrotherC", nick="BrotherC")
    omega_role = FakeRole(601, "Omega")

    kia_line = f"KIA: {kia_value}\n" if include_kia_line else ""

    content = (
        "++ MISSION REPORT ++\n"
        "Mission: Terminus Protocol\n"
        "Rank: C\n"
        f"Difficulty: <@&{omega_role.id}>\n"
        f"Gene-seed: <@{u1.id}>\n"
        "Armory Data: 3\n"
        f"{kia_line}"
        "Brothers:\n"
        f" - <@{u1.id}>\n"
        f" - <@{u2.id}>\n"
        f" - <@{u3.id}>\n"
        "++ END OF REPORT ++\n"
    )

    return FakeMessage(content, mentions=[u1, u2, u3], role_mentions=[omega_role])


def test_omega_with_kia_line_validates():
    """An Omega AAR with an explicit 'KIA: 0' line should pass validation."""
    msg = _make_omega_message(include_kia_line=True, kia_value=0)
    rec = parse_aar(msg)
    assert rec is not None
    assert rec.get("kia_line_present") is True
    errs = validate_aar(rec)
    kia_errors = [e for e in errs if "KIA" in e]
    assert not kia_errors, f"Expected no KIA error when KIA line is present, but got: {kia_errors}"


def test_omega_without_kia_line_returns_error():
    """An Omega AAR missing the 'KIA:' line should fail validation."""
    msg = _make_omega_message(include_kia_line=False)
    rec = parse_aar(msg)
    assert rec is not None
    assert rec.get("kia_line_present") is False
    errs = validate_aar(rec)
    assert any("KIA" in e for e in errs), (
        f"Expected a KIA line required error for Omega without KIA line, but got: {errs}"
    )


HERISOR_DEFENSE_TAG_ROLE_ID = 1511108024922673233


def _make_herisor_message(difficulty_name: str, brother_count: int):
    """Build a FakeMessage with the Defense of Herisor tag on the Mission line.

    Args:
        difficulty_name: Role name for the Difficulty line (e.g. 'Hard-Stratagem', 'Normal-Stratagem').
        brother_count: Number of Brothers to include in the Brothers list.

    Returns:
        A FakeMessage instance suitable for parse_aar() / validate_aar().
    """
    users = [FakeUser(400 + i, f"Brother{i}", nick=f"Brother{i}") for i in range(brother_count)]
    difficulty_role = FakeRole(700, difficulty_name)
    herisor_role = FakeRole(HERISOR_DEFENSE_TAG_ROLE_ID, "Defense of Herisor")

    brothers_lines = "".join(f" - <@{u.id}>\n" for u in users)
    gene_seed_user = users[0] if users else FakeUser(400, "Brother0", nick="Brother0")

    content = (
        "++ MISSION REPORT ++\n"
        f"Mission: Reclamation <@&{HERISOR_DEFENSE_TAG_ROLE_ID}>\n"
        "Rank: A\n"
        f"Difficulty: <@&{difficulty_role.id}>\n"
        f"Gene-seed: <@{gene_seed_user.id}>\n"
        "Armory Data: 3\n"
        "Brothers:\n"
        f"{brothers_lines}"
        "++ END OF REPORT ++\n"
    )

    return FakeMessage(content, mentions=users, role_mentions=[difficulty_role, herisor_role])


def test_herisor_wrong_difficulty_returns_error():
    """Defense of Herisor with Normal-Stratagem (not Hard) should fail validation."""
    msg = _make_herisor_message(difficulty_name="Normal-Stratagem", brother_count=3)
    rec = parse_aar(msg)
    assert rec is not None
    assert rec.get("herisor_defense_in_mission") is True
    errs = validate_aar(rec)
    assert any("Hard-Stratagem" in e or "Hard-Siege" in e for e in errs), (
        f"Expected a difficulty error for @Defense_of_Herisor without Hard difficulty, but got: {errs}"
    )


def test_herisor_wrong_brother_count_returns_error():
    """Defense of Herisor with 2 brothers (not exactly 3) should fail validation."""
    msg = _make_herisor_message(difficulty_name="Hard-Stratagem", brother_count=2)
    rec = parse_aar(msg)
    assert rec is not None
    assert rec.get("herisor_defense_in_mission") is True
    errs = validate_aar(rec)
    assert any("3 Brothers" in e or "exactly 3" in e for e in errs), (
        f"Expected a brother count error for @Defense_of_Herisor with 2 brothers, but got: {errs}"
    )
