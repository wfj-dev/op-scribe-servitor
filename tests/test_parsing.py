import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from opscribe.bot import _process_challenge_tracking, _run_recheck_errors, _sweep_challenge_completions, parse_aar, validate_aar
from opscribe.constants import (
    BLACK_LAURELS_ROLE_ID,
    BLACK_REEF_PERSECUTION_ROLE_ID,
    CHAPTER_APPROVED_ROLE_ID,
    DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID,
    DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID,
    DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID,
    HERISOR_DEFENSE_MEDAL_ROLE_ID,
    HERISOR_DEFENSE_TAG_ROLE_ID,
    LEVIATHAN_PROTOCOL_ROLE_ID,
    PVP_DIFFICULTY_ROLE_ID,
    PIPEHITTER_ROLE_ID,
)


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


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeGuild:
    def __init__(self, member):
        self._member = member

    def get_member(self, member_id):
        if member_id == self._member.id:
            return self._member
        return None


class _FakeChannel:
    def __init__(self, guild, messages):
        self.guild = guild
        self._messages = messages

    async def fetch_message(self, msg_id):
        return self._messages[msg_id]


class _FakeRecheckMessage:
    def __init__(self, msg_id):
        self.id = msg_id
        self.created_at = datetime.utcnow()
        self.jump_url = f"https://discord.example/jump/{msg_id}"
        self.channel = self

    async def fetch_message(self, _msg_id):
        raise Exception("No reply message")


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


def test_process_challenge_tracking_herisor_siege_uses_brother_waves():
    role = SimpleNamespace(id=999001, name="Watch Brother")
    member = SimpleNamespace(id=777, display_name="Brother777", roles=[role])
    guild = _FakeGuild(member)
    progress_data = {}
    record = {
        "mission": "Siege",
        "difficulty_class": "hard_siege",
        "herisor_defense_in_mission": True,
        "brother_ids": [str(member.id)],
        "brother_waves": {str(member.id): 10},
        "waves": 0,
        "aar_id": "herisor-siege-1",
        "message_url": "https://discord.example/aar/1",
        "timestamp": "2026-06-19T00:00:00Z",
    }

    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
    ):
        notifications = asyncio.run(_process_challenge_tracking(record, guild))

    # Under new rules: completing on either team alone triggers the base medal.
    award_role_ids = {n[2] for n in notifications}
    assert HERISOR_DEFENSE_MEDAL_ROLE_ID in award_role_ids
    assert progress_data[str(member.id)]["herisor_defense_siege"][0]["aar_id"] == "herisor-siege-1"


def test_sweep_challenge_completions_backfills_herisor_medal():
    role = SimpleNamespace(id=999003, name="Watch Brother")
    member = SimpleNamespace(id=889, display_name="Brother889", roles=[role])
    guild = _FakeGuild(member)
    progress_data = {
        str(member.id): {
            "display_name": member.display_name,
            "notified": [],
            "herisor_defense_siege": [
                {
                    "mission": "siege",
                    "aar_id": "herisor-siege-sweep-1",
                    "message_url": "https://discord.example/aar/sweep1",
                    "timestamp": "2026-06-20T00:00:00Z",
                    "black_laurels": False,
                }
            ],
        }
    }

    send_mock = AsyncMock()
    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
        patch("opscribe.aar_ops._send_challenge_eligibility_notifications", send_mock),
    ):
        count = asyncio.run(_sweep_challenge_completions(guild))

    assert count == 1
    sent_notifications = send_mock.await_args.args[0]
    assert sent_notifications[0][2] == HERISOR_DEFENSE_MEDAL_ROLE_ID
    assert "herisor_defense" in progress_data[str(member.id)]["notified"]


def test_process_challenge_tracking_herisor_awards_and_normalized_missions():
    role = SimpleNamespace(id=999002, name="Watch Brother")
    member = SimpleNamespace(id=888, display_name="Brother888", roles=[role])
    guild = _FakeGuild(member)
    progress_data = {}

    records = [
        {
            "mission": " Siege ",
            "difficulty_class": "hard_siege",
            "herisor_defense_in_mission": True,
            "brother_ids": [str(member.id)],
            "brother_waves": {str(member.id): 16},
            "waves": 0,
            "black_laurels_in_mission": True,
            "aar_id": "herisor-siege-2",
            "message_url": "https://discord.example/aar/2",
            "timestamp": "2026-06-19T00:01:00Z",
        },
        {
            "mission": f"  Termination <@&{HERISOR_DEFENSE_TAG_ROLE_ID}> @Defense_of_Herisor ",
            "difficulty_class": "hard_stratagem",
            "herisor_defense_in_mission": True,
            "brother_ids": [str(member.id)],
            "black_laurels_in_mission": True,
            "aar_id": "herisor-term-1",
            "message_url": "https://discord.example/aar/3",
            "timestamp": "2026-06-19T00:02:00Z",
        },
        {
            "mission": "Reclamation @Defense_of_Herisor",
            "difficulty_class": "hard_stratagem",
            "herisor_defense_in_mission": True,
            "brother_ids": [str(member.id)],
            "black_laurels_in_mission": True,
            "aar_id": "herisor-rec-1",
            "message_url": "https://discord.example/aar/4",
            "timestamp": "2026-06-19T00:03:00Z",
        },
    ]

    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
    ):
        # Record 1: siege with BL — triggers base AND distinguished (siege+BL), NOT valor yet
        n1 = asyncio.run(_process_challenge_tracking(records[0], guild))
        n1_ids = {n[2] for n in n1}
        assert HERISOR_DEFENSE_MEDAL_ROLE_ID in n1_ids
        assert DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID in n1_ids
        assert DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID not in n1_ids

        # Record 2: termination with BL — strat_bl needs BOTH term+BL and rec+BL; valor not yet
        n2 = asyncio.run(_process_challenge_tracking(records[1], guild))
        n2_ids = {n[2] for n in n2}
        assert DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID not in n2_ids

        # Record 3: reclamation with BL — now siege+BL AND (term+BL AND rec+BL) → valor fires
        final_notifications = asyncio.run(_process_challenge_tracking(records[2], guild))

    award_role_ids = {n[2] for n in final_notifications}
    assert DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID in award_role_ids


def test_process_challenge_tracking_herisor_wave_line_bl_fallback_awards_distinguished():
    role = SimpleNamespace(id=999003, name="Watch Brother")
    member = SimpleNamespace(id=889, display_name="Brother889", roles=[role])
    guild = _FakeGuild(member)
    progress_data = {}

    record = {
        "mission": None,
        "difficulty_class": "hard_siege",
        "herisor_defense_in_mission": True,
        "brother_ids": [str(member.id)],
        "brother_waves": {str(member.id): 10},
        "waves": 10,
        "black_laurels_mentioned_elsewhere": True,
        "aar_id": "herisor-siege-wave-bl",
        "message_url": "https://discord.example/aar/5",
        "timestamp": "2026-06-19T00:04:00Z",
    }

    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
    ):
        notifications = asyncio.run(_process_challenge_tracking(record, guild))

    award_role_ids = {n[2] for n in notifications}
    assert HERISOR_DEFENSE_MEDAL_ROLE_ID in award_role_ids
    assert DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID in award_role_ids
    assert DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID not in award_role_ids


def test_process_challenge_tracking_distinguished_kadaku_requires_bl_and_leviathan_all_missions():
    role = SimpleNamespace(id=999111, name="Watch Brother")
    member = SimpleNamespace(id=9901, display_name="KadakuBrother", roles=[role])
    guild = _FakeGuild(member)
    progress_data = {}

    records = [
        {
            "mission": "Inferno",
            "difficulty_class": "absolute_ops",
            "leviathan_protocol_in_mission": True,
            "black_laurels_in_mission": True,
            "brother_ids": [str(member.id)],
            "aar_id": "kadaku-dist-1",
            "message_url": "https://discord.example/aar/kadaku1",
            "timestamp": "2026-07-08T00:01:00Z",
        },
        {
            "mission": "Termination",
            "difficulty_class": "absolute_ops",
            "leviathan_protocol_in_mission": True,
            "black_laurels_in_mission": True,
            "brother_ids": [str(member.id)],
            "aar_id": "kadaku-dist-2",
            "message_url": "https://discord.example/aar/kadaku2",
            "timestamp": "2026-07-08T00:02:00Z",
        },
        {
            "mission": "Reclamation",
            "difficulty_class": "absolute_ops",
            "leviathan_protocol_in_mission": True,
            "black_laurels_in_mission": True,
            "brother_ids": [str(member.id)],
            "aar_id": "kadaku-dist-3",
            "message_url": "https://discord.example/aar/kadaku3",
            "timestamp": "2026-07-08T00:03:00Z",
        },
    ]

    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
    ):
        n1 = asyncio.run(_process_challenge_tracking(records[0], guild))
        n2 = asyncio.run(_process_challenge_tracking(records[1], guild))
        n3 = asyncio.run(_process_challenge_tracking(records[2], guild))

    assert DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID not in {n[2] for n in n1}
    assert DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID not in {n[2] for n in n2}
    assert DISTINGUISHED_KADAKU_CAMPAIGN_MEDAL_ROLE_ID in {n[2] for n in n3}


def _make_black_laurels_exception_message(
    *,
    mission_line: str,
    difficulty_name: str,
    brothers: int = 3,
    waves_line: str = "",
    include_herisor: bool = False,
    include_black_reef: bool = False,
):
    users = [FakeUser(800 + i, f"BLBrother{i}", nick=f"BLBrother{i}") for i in range(brothers)]
    role_mentions = [FakeRole(8800, difficulty_name), FakeRole(BLACK_LAURELS_ROLE_ID, "Black Laurels")]
    mission_suffix = ""
    if include_herisor:
        role_mentions.append(FakeRole(HERISOR_DEFENSE_TAG_ROLE_ID, "Defense of Herisor"))
        mission_suffix += f" <@&{HERISOR_DEFENSE_TAG_ROLE_ID}>"
    if include_black_reef:
        role_mentions.append(FakeRole(BLACK_REEF_PERSECUTION_ROLE_ID, "Black Reef Persecution"))
        mission_suffix += f" <@&{BLACK_REEF_PERSECUTION_ROLE_ID}>"
    brothers_lines = "".join(f" - <@{u.id}>\n" for u in users)
    waves = f"{waves_line}\n" if waves_line else ""

    content = (
        "++ MISSION REPORT ++\n"
        f"Mission: {mission_line} <@&{BLACK_LAURELS_ROLE_ID}>{mission_suffix}\n"
        "Rank: A\n"
        f"Difficulty: <@&{role_mentions[0].id}>\n"
        f"Gene-seed: <@{users[0].id}>\n"
        "Armory Data: 3\n"
        f"{waves}"
        "Brothers:\n"
        f"{brothers_lines}"
        "++ END OF REPORT ++\n"
    )
    return FakeMessage(content, mentions=users, role_mentions=role_mentions)


def test_black_laurels_hard_strat_herisor_termination_valid():
    msg = _make_black_laurels_exception_message(
        mission_line="Termination",
        difficulty_name="Hard-Stratagem",
        include_herisor=True,
    )
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert errs == [], f"Expected Herisor Hard-Strat Termination BL to validate, got: {errs}"


def test_black_laurels_hard_strat_herisor_wrong_mission_invalid():
    msg = _make_black_laurels_exception_message(
        mission_line="Inferno",
        difficulty_name="Hard-Stratagem",
        include_herisor=True,
    )
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("Defense_of_Herisor with @Hard-Stratagem is only valid" in e for e in errs), errs


def test_black_laurels_hard_siege_herisor_waves_10_valid():
    msg = _make_black_laurels_exception_message(
        mission_line="Reclamation",
        difficulty_name="Hard-Siege",
        include_herisor=True,
        waves_line="Waves: 10",
    )
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert errs == [], f"Expected Herisor Hard-Siege BL with Waves 10 to validate, got: {errs}"


def test_black_laurels_hard_siege_herisor_wave_line_without_mission_valid():
    users = [FakeUser(901, "Brother1"), FakeUser(902, "Brother2"), FakeUser(903, "Brother3")]
    hard_siege = FakeRole(1431732824708485170, "Hard-Siege")
    herisor = FakeRole(HERISOR_DEFENSE_TAG_ROLE_ID, "Defense of Herisor")
    black_laurels = FakeRole(BLACK_LAURELS_ROLE_ID, "Black Laurels")

    content = (
        "++ MISSION REPORT ++\n"
        f"Wave: 10 <@&{HERISOR_DEFENSE_TAG_ROLE_ID}> <@&{BLACK_LAURELS_ROLE_ID}>\n"
        f"Difficulty: <@&{hard_siege.id}>\n"
        "Armory Data: 3\n"
        "Team:\n"
        f"<@{users[0].id}>\n"
        f"<@{users[1].id}>\n"
        f"<@{users[2].id}>\n"
        "++ END OF REPORT ++\n"
    )

    msg = FakeMessage(content, mentions=users, role_mentions=[hard_siege, herisor, black_laurels])
    rec = parse_aar(msg)
    assert rec.get("waves") == 10
    errs = validate_aar(rec)
    assert errs == [], f"Expected wave-only Herisor Hard-Siege BL report to validate, got: {errs}"


def test_black_laurels_hard_siege_herisor_waves_below_10_invalid():
    msg = _make_black_laurels_exception_message(
        mission_line="Reclamation",
        difficulty_name="Hard-Siege",
        include_herisor=True,
        waves_line="Waves: 9",
    )
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("@Defense_of_Herisor with @Hard-Siege requires Waves 10+." in e for e in errs), errs


def test_black_laurels_hard_siege_without_exceptions_invalid():
    msg = _make_black_laurels_exception_message(
        mission_line="Reclamation",
        difficulty_name="Hard-Siege",
        include_herisor=False,
        waves_line="Waves: 20",
    )
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("@Black_Laurels requires @Absolute or @Omega on the Difficulty line" in e for e in errs), errs


def test_black_laurels_black_reef_hard_strat_still_valid():
    msg = _make_black_laurels_exception_message(
        mission_line="Inferno",
        difficulty_name="Hard-Stratagem",
        include_black_reef=True,
        brothers=2,
    )
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert errs == [], f"Expected Black Reef Hard-Strat BL path to remain valid, got: {errs}"


def test_black_laurels_leviathan_kadaku_mission_non_absolute_valid():
    users = [FakeUser(9101, "KadakuA", nick="KadakuA"), FakeUser(9102, "KadakuB", nick="KadakuB"), FakeUser(9103, "KadakuC", nick="KadakuC")]
    difficulty = FakeRole(9901, "Lethal")
    black_laurels = FakeRole(BLACK_LAURELS_ROLE_ID, "Black Laurels")
    leviathan = FakeRole(LEVIATHAN_PROTOCOL_ROLE_ID, "Leviathan Protocol")

    msg = FakeMessage(
        (
            "++ MISSION REPORT ++\n"
            f"Mission: Inferno <@&{BLACK_LAURELS_ROLE_ID}> <@&{leviathan.id}>\n"
            "Rank: A\n"
            f"Difficulty: <@&{difficulty.id}>\n"
            f"Gene-seed: <@{users[0].id}>\n"
            "Armory Data: 2\n"
            "Team:\n"
            f"<@{users[0].id}>\n"
            f"<@{users[1].id}>\n"
            f"<@{users[2].id}>\n"
            "++ END OF REPORT ++\n"
        ),
        mentions=users,
        role_mentions=[difficulty, black_laurels, leviathan],
    )

    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert errs == [], f"Expected Leviathan+Kadaku BL path to validate on non-Absolute difficulty, got: {errs}"


def test_chapter_approved_role_mention_is_detected():
    u1 = FakeUser(9501, "ChapA", nick="ChapA")
    u2 = FakeUser(9502, "ChapB", nick="ChapB")
    difficulty = FakeRole(9801, "Normal-Stratagem")
    chapter_approved = FakeRole(CHAPTER_APPROVED_ROLE_ID, "Chapter Approved")
    msg = FakeMessage(
        (
            "++ MISSION REPORT ++\n"
            "Mission: Inferno\n"
            "Rank: A\n"
            f"Difficulty: <@&{difficulty.id}>\n"
            f"Mission Tag: <@&{CHAPTER_APPROVED_ROLE_ID}>\n"
            f"Gene-seed: <@{u1.id}>\n"
            "Armory Data: 1\n"
            "Brothers:\n"
            f" - <@{u1.id}>\n"
            f" - <@{u2.id}>\n"
            "++ END OF REPORT ++\n"
        ),
        mentions=[u1, u2],
        role_mentions=[difficulty, chapter_approved],
    )
    rec = parse_aar(msg)
    assert rec.get("chapter_approved") is True


def _make_pipehitter_message(difficulty_name: str, mission_name: str = "Inferno"):
    users = [FakeUser(9601, "PipeA", nick="PipeA"), FakeUser(9602, "PipeB", nick="PipeB")]
    difficulty = FakeRole(9902, difficulty_name)
    pipehitter = FakeRole(PIPEHITTER_ROLE_ID, "SOK-G: Pipehitter")

    msg = FakeMessage(
        (
            "++ MISSION REPORT ++\n"
            f"Mission: {mission_name} <@&{PIPEHITTER_ROLE_ID}>\n"
            "Rank: A\n"
            f"Difficulty: <@&{difficulty.id}>\n"
            f"Gene-seed: <@{users[0].id}>\n"
            "Armory Data: 1\n"
            "Brothers:\n"
            f" - <@{users[0].id}>\n"
            f" - <@{users[1].id}>\n"
            "++ END OF REPORT ++\n"
        ),
        mentions=users,
        role_mentions=[difficulty, pipehitter],
    )
    return msg


def test_pipehitter_requires_hard_stratagem_difficulty():
    msg = _make_pipehitter_message(difficulty_name="Absolute")
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("requires @Hard-Stratagem" in e for e in errs), errs


def test_pipehitter_hard_stratagem_on_eligible_mission_has_no_difficulty_error():
    msg = _make_pipehitter_message(difficulty_name="Hard-Stratagem", mission_name="Inferno")
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert not any("requires @Hard-Stratagem" in e for e in errs), errs


def test_editing_existing_aar_replaces_challenge_progress_entry():
    role = SimpleNamespace(id=999003, name="Watch Brother")
    member = SimpleNamespace(id=889, display_name="Brother889", roles=[role])
    guild = _FakeGuild(member)
    progress_data = {}

    original = {
        "mission": "Inferno",
        "difficulty_class": "absolute_ops",
        "black_laurels_in_mission": True,
        "black_laurels_in_difficulty": False,
        "brother_ids": [str(member.id)],
        "aar_id": "edited-aar-1",
        "message_url": "https://discord.example/aar/original",
        "timestamp": "2026-06-19T00:10:00Z",
    }
    edited = {
        "mission": "Reclamation",
        "difficulty_class": "absolute_ops",
        "black_laurels_in_mission": True,
        "black_laurels_in_difficulty": False,
        "brother_ids": [str(member.id)],
        "aar_id": "edited-aar-1",
        "message_url": "https://discord.example/aar/edited",
        "timestamp": "2026-06-19T00:11:00Z",
    }

    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
    ):
        asyncio.run(_process_challenge_tracking(original, guild))
        asyncio.run(_process_challenge_tracking(edited, guild))

    entries = progress_data[str(member.id)]["black_laurels"]
    missions = [e.get("mission") for e in entries]
    aar_ids = [str(e.get("aar_id")) for e in entries]

    assert "reclamation" in missions
    assert "inferno" not in missions
    assert aar_ids.count("edited-aar-1") == 1


def test_recheck_errors_recovered_aar_updates_challenge_progress():
    role = SimpleNamespace(id=999004, name="Watch Brother")
    member = SimpleNamespace(id=890, display_name="Brother890", roles=[role])
    guild = _FakeGuild(member)
    msg_id = 123456789
    channel = _FakeChannel(guild, {msg_id: _FakeRecheckMessage(msg_id)})

    # Simulated previously rejected AAR, now reparsed as valid.
    recovered_record = {
        "aar_id": msg_id,
        "mission": "Inferno",
        "difficulty_class": "absolute_ops",
        "black_laurels_in_mission": True,
        "black_laurels_in_difficulty": False,
        "brother_ids": [str(member.id)],
        "message_url": "https://discord.example/aar/recovered",
        "timestamp": "2026-06-19T01:00:00Z",
    }

    error_store = {str(msg_id): {"errors": ["old validation error"]}}
    progress_data = {}

    def _fake_load_json_dict(path):
        # _run_recheck_errors only needs the error archive in this test.
        return error_store

    with (
        patch("opscribe.aar_ops._g.CHALLENGE_PROGRESS_LOCK", _AsyncLock()),
        patch("opscribe.aar_ops._load_challenge_progress", return_value=progress_data),
        patch("opscribe.aar_ops._save_challenge_progress"),
        patch("opscribe.aar_ops._load_json_dict", side_effect=_fake_load_json_dict),
        patch("opscribe.aar_ops._save_json_dict"),
        patch("opscribe.aar_ops.has_been_processed", return_value=False),
        patch("opscribe.aar_ops.parse_aar", return_value=recovered_record),
        patch("opscribe.aar_ops.validate_aar", return_value=[]),
        patch("opscribe.aar_ops.save_aar_record"),
        patch("opscribe.aar_ops._set_aar_reaction"),
        patch("opscribe.aar_ops._send_challenge_eligibility_notifications"),
    ):
        fixed, still_broken = asyncio.run(_run_recheck_errors(channel, span_days=None))

    assert fixed == 1
    assert still_broken == 0
    entries = progress_data[str(member.id)]["black_laurels"]
    assert any(str(e.get("aar_id")) == str(msg_id) for e in entries)


def _make_pvp_message(
    *,
    map_name: str = "Cathedrum",
    game_mode: str = "Capture and Control",
    result: str = "[W]in",
    team_size: int = 6,
    difficulty_role_id: int = PVP_DIFFICULTY_ROLE_ID,
    duplicate_first_member: bool = False,
):
    users = [FakeUser(2000 + i, f"PvP{i}", nick=f"PvP{i}") for i in range(max(1, team_size))]
    difficulty_role = FakeRole(difficulty_role_id, "PvP Difficulty")

    team_users = users[:team_size]
    if duplicate_first_member and team_users:
        team_users = list(team_users)
        team_users[-1] = team_users[0]

    team_lines = "".join(f"<@{u.id}>\n" for u in team_users)

    content = (
        "++ MISSION REPORT ++\n"
        f"Map: {map_name}\n"
        f"Game Mode: {game_mode}\n"
        f"Difficulty: <@&{difficulty_role.id}>\n"
        f"Result: {result}\n"
        "Team:\n"
        f"{team_lines}"
        "++ END OF REPORT ++\n"
    )

    return FakeMessage(content, mentions=users, role_mentions=[difficulty_role])


def test_pvp_win_validates_and_awards_4_points():
    msg = _make_pvp_message(result="[W]in", team_size=6)
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert errs == [], errs
    assert rec.get("aar_type") == "pvp"
    assert rec.get("difficulty_class") == "pvp_ops"
    assert rec.get("pvp_result") == "W"
    assert rec.get("points_for_op") == 4


def test_pvp_loss_validates_and_awards_2_points():
    msg = _make_pvp_message(result="[L]ose", team_size=6)
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert errs == [], errs
    assert rec.get("pvp_result") == "L"
    assert rec.get("points_for_op") == 2


def test_pvp_invalid_map_returns_error():
    msg = _make_pvp_message(map_name="Unknown Map")
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("Map" in e and "not valid" in e for e in errs), errs


def test_pvp_invalid_game_mode_returns_error():
    msg = _make_pvp_message(game_mode="King of the Hill")
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("Game Mode" in e and "not valid" in e for e in errs), errs


def test_pvp_invalid_result_returns_error():
    msg = _make_pvp_message(result="Draw")
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("Result" in e and "missing" in e for e in errs), errs


def test_pvp_invalid_difficulty_role_returns_error():
    msg = _make_pvp_message(difficulty_role_id=999999999999)
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("PvP Difficulty" in e for e in errs), errs


def test_pvp_team_size_bounds():
    rec_too_small = parse_aar(_make_pvp_message(team_size=1))
    errs_too_small = validate_aar(rec_too_small)
    assert any("between 2 and 6" in e for e in errs_too_small), errs_too_small

    rec_min = parse_aar(_make_pvp_message(team_size=2))
    assert validate_aar(rec_min) == []

    rec_max = parse_aar(_make_pvp_message(team_size=6))
    assert validate_aar(rec_max) == []

    rec_too_large = parse_aar(_make_pvp_message(team_size=7))
    errs_too_large = validate_aar(rec_too_large)
    assert any("between 2 and 6" in e for e in errs_too_large), errs_too_large


def test_pvp_duplicate_team_mentions_return_error():
    msg = _make_pvp_message(team_size=6, duplicate_first_member=True)
    rec = parse_aar(msg)
    errs = validate_aar(rec)
    assert any("duplicate" in e.lower() for e in errs), errs


def test_pvp_records_do_not_trigger_challenge_tracking():
    role = SimpleNamespace(id=999005, name="Watch Brother")
    member = SimpleNamespace(id=891, display_name="Brother891", roles=[role])
    guild = _FakeGuild(member)
    record = {
        "aar_type": "pvp",
        "brother_ids": [str(member.id)],
        "aar_id": "pvp-aar-1",
    }

    load_mock = patch("opscribe.aar_ops._load_challenge_progress", return_value={})
    save_mock = patch("opscribe.aar_ops._save_challenge_progress")

    with load_mock as load_fn, save_mock as save_fn:
        notifications = asyncio.run(_process_challenge_tracking(record, guild))

    assert notifications == []
    load_fn.assert_not_called()
    save_fn.assert_not_called()
