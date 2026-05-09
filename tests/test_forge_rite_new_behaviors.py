"""Unit tests for the new forge_rite behaviors introduced with the immersive
armor system update:

- Tiered verbosity selection (_classify_forge_rite_event):
    * first_binding → significant event, "first_binding" chronicle key
    * rebirth → significant event, "rebirth" chronicle key
    * restoration → routine event, "restoration" chronicle key
    * maintenance → routine event, "maintenance" chronicle key
    * first_binding takes precedence when multiple flags are True

- Compact status icons (_get_compact_rite_status):
    * crit_fail → ⚠️ RESISTED
    * crit_success → ✨ BLESSED (grace)
    * intensive (non-crit) → ✨ RESTORED
    * damaged + normal → 🟢 REPAIRED
    * nominal + normal → 🟢 MAINTAINED
    * crit_fail always beats intensive/damaged

- Thread reply text (_get_thread_reply_text):
    * reconsecrated → ✨ Spirit Reborn message
    * crit_fail → ⚠️ Rite Resisted message
    * default → 🟢 Armor Restored message
    * reconsecrated takes precedence over crit_fail

- Chronicle recording (_record_rite_in_chronicle):
    * Records bearer_id, techmarine_id, rite_type, spirit, event
    * Increments total_rites for the techmarine
    * Increments first_bindings for first_binding events
    * Increments rebirths for rebirth events
    * Maintenance/restoration do not increment first_bindings or rebirths
    * Appends to rite_history; history is capped at 500 entries

- Pending alert helpers:
    * _store_pending_alert stores message_id, channel_id, and timestamp
    * _get_pending_alert returns stored alert or None
    * _clear_pending_alert removes the stored alert; missing key is a no-op
"""

import asyncio
from unittest.mock import patch

from opscribe.bot import (
    _classify_forge_rite_event,
    _get_compact_rite_status,
    _get_thread_reply_text,
    _record_rite_in_chronicle,
    _store_pending_alert,
    _get_pending_alert,
    _clear_pending_alert,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _default_chronicle():
    """Return a fresh chronicle data structure (no file I/O needed)."""
    return {
        "pending_alerts": {},
        "rite_history": [],
        "techmarine_stats": {},
        "dashboard_message_id": None,
        "last_ambient_ts": None,
    }


# ---------------------------------------------------------------------------
# _classify_forge_rite_event – verbosity tier
# ---------------------------------------------------------------------------


def test_classify_first_binding_is_significant():
    """First binding should produce a significant event with 'first_binding' key."""
    is_significant, spirit_event = _classify_forge_rite_event(
        spirit_is_first=True,
        spirit_is_reconsecrated=False,
        spirit_is_restored=False,
    )
    assert is_significant is True
    assert spirit_event == "first_binding"


def test_classify_rebirth_is_significant():
    """Reconsecrated spirit should produce a significant event with 'rebirth' key."""
    is_significant, spirit_event = _classify_forge_rite_event(
        spirit_is_first=False,
        spirit_is_reconsecrated=True,
        spirit_is_restored=False,
    )
    assert is_significant is True
    assert spirit_event == "rebirth"


def test_classify_restoration_is_routine():
    """Damage-repaired spirit should produce a routine event with 'restoration' key."""
    is_significant, spirit_event = _classify_forge_rite_event(
        spirit_is_first=False,
        spirit_is_reconsecrated=False,
        spirit_is_restored=True,
    )
    assert is_significant is False
    assert spirit_event == "restoration"


def test_classify_maintenance_is_routine():
    """Normal returning spirit should produce a routine event with 'maintenance' key."""
    is_significant, spirit_event = _classify_forge_rite_event(
        spirit_is_first=False,
        spirit_is_reconsecrated=False,
        spirit_is_restored=False,
    )
    assert is_significant is False
    assert spirit_event == "maintenance"


def test_classify_first_binding_takes_precedence():
    """When both first and restored are True, first_binding wins."""
    is_significant, spirit_event = _classify_forge_rite_event(
        spirit_is_first=True,
        spirit_is_reconsecrated=False,
        spirit_is_restored=True,
    )
    assert is_significant is True
    assert spirit_event == "first_binding"


def test_classify_rebirth_takes_precedence_over_restored():
    """When both reconsecrated and restored are True, rebirth wins."""
    is_significant, spirit_event = _classify_forge_rite_event(
        spirit_is_first=False,
        spirit_is_reconsecrated=True,
        spirit_is_restored=True,
    )
    assert is_significant is True
    assert spirit_event == "rebirth"


def test_classify_returns_tuple():
    """_classify_forge_rite_event always returns a 2-tuple."""
    result = _classify_forge_rite_event(False, False, False)
    assert isinstance(result, tuple)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# _get_compact_rite_status – status icon mapping
# ---------------------------------------------------------------------------


def test_compact_status_crit_fail():
    """Critical failure → ⚠️ RESISTED."""
    icon, text = _get_compact_rite_status("crit_fail", False, True)
    assert icon == "⚠️"
    assert text == "RESISTED"


def test_compact_status_crit_success():
    """Critical success → ✨ BLESSED *(grace)*."""
    icon, text = _get_compact_rite_status("crit_success", False, True)
    assert icon == "✨"
    assert text == "BLESSED *(grace)*"


def test_compact_status_intensive_normal():
    """Intensive normal blessing → ✨ RESTORED."""
    icon, text = _get_compact_rite_status("normal", True, True)
    assert icon == "✨"
    assert text == "RESTORED"


def test_compact_status_standard_damaged():
    """Standard blessing on damaged armor → 🟢 REPAIRED."""
    icon, text = _get_compact_rite_status("normal", False, True)
    assert icon == "🟢"
    assert text == "REPAIRED"


def test_compact_status_standard_nominal():
    """Standard blessing on nominal armor → 🟢 MAINTAINED."""
    icon, text = _get_compact_rite_status("normal", False, False)
    assert icon == "🟢"
    assert text == "MAINTAINED"


def test_compact_status_crit_fail_beats_intensive():
    """crit_fail takes priority over intensive flag."""
    icon, text = _get_compact_rite_status("crit_fail", True, True)
    assert icon == "⚠️"
    assert text == "RESISTED"


def test_compact_status_crit_success_beats_intensive():
    """crit_success takes priority over intensive flag."""
    icon, text = _get_compact_rite_status("crit_success", True, True)
    assert icon == "✨"
    assert text == "BLESSED *(grace)*"


# ---------------------------------------------------------------------------
# _get_thread_reply_text – thread reply content
# ---------------------------------------------------------------------------


def test_thread_reply_reconsecrated():
    """Reconsecrated spirit → Spirit Reborn reply."""
    text = _get_thread_reply_text(
        spirit_is_reconsecrated=True,
        blessing_roll_outcome="normal",
        attester="Brother Castellan",
        machine_spirit_emoji="⚙️",
        spirit_designation="FURY-ABC123-Α",
    )
    assert "Spirit Reborn" in text
    assert "Brother Castellan" in text
    assert "FURY-ABC123-Α" in text
    assert text.startswith("✨")


def test_thread_reply_crit_fail():
    """Critical failure → Rite Resisted reply."""
    text = _get_thread_reply_text(
        spirit_is_reconsecrated=False,
        blessing_roll_outcome="crit_fail",
        attester="Brother Castellan",
        machine_spirit_emoji="⚙️",
        spirit_designation="FURY-ABC123-Α",
    )
    assert "Rite Resisted" in text
    assert text.startswith("⚠️")


def test_thread_reply_normal():
    """Normal outcome → Armor Restored reply."""
    text = _get_thread_reply_text(
        spirit_is_reconsecrated=False,
        blessing_roll_outcome="normal",
        attester="Brother Castellan",
        machine_spirit_emoji="⚙️",
        spirit_designation="AEGIS-DEF456-Β",
    )
    assert "Armor Restored" in text
    assert "Brother Castellan" in text
    assert "AEGIS-DEF456-Β" in text
    assert text.startswith("🟢")


def test_thread_reply_reconsecrated_beats_crit_fail():
    """Reconsecrated flag takes priority over crit_fail outcome."""
    text = _get_thread_reply_text(
        spirit_is_reconsecrated=True,
        blessing_roll_outcome="crit_fail",
        attester="Tech",
        machine_spirit_emoji="⚙️",
        spirit_designation="SPIRIT-XYZ",
    )
    assert "Spirit Reborn" in text
    assert "Rite Resisted" not in text


def test_thread_reply_crit_success_falls_to_normal_branch():
    """crit_success (not a fail) hits the default 'Armor Restored' branch."""
    text = _get_thread_reply_text(
        spirit_is_reconsecrated=False,
        blessing_roll_outcome="crit_success",
        attester="Tech",
        machine_spirit_emoji="⚙️",
        spirit_designation="SPIRIT-XYZ",
    )
    assert "Armor Restored" in text


# ---------------------------------------------------------------------------
# _record_rite_in_chronicle – chronicle recording
# ---------------------------------------------------------------------------


def test_record_rite_appends_to_history():
    """Recording a rite appends exactly one entry to rite_history."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(
            _record_rite_in_chronicle(
                bearer_id=100,
                techmarine_id=200,
                rite_type="standard",
                spirit_designation="FURY-001-Α",
                spirit_event="maintenance",
            )
        )

    saved = mock_save.call_args[0][0]
    assert len(saved["rite_history"]) == 1
    entry = saved["rite_history"][0]
    assert entry["bearer_id"] == "100"
    assert entry["techmarine_id"] == "200"
    assert entry["rite_type"] == "standard"
    assert entry["spirit"] == "FURY-001-Α"
    assert entry["event"] == "maintenance"


def test_record_rite_increments_total_rites():
    """Recording any rite increments the techmarine's total_rites counter."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "standard", "SPIRIT", "maintenance"))

    saved = mock_save.call_args[0][0]
    assert saved["techmarine_stats"]["200"]["total_rites"] == 1


def test_record_rite_first_binding_increments_first_bindings():
    """first_binding event increments the techmarine's first_bindings counter."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "standard", "SPIRIT", "first_binding"))

    saved = mock_save.call_args[0][0]
    assert saved["techmarine_stats"]["200"]["first_bindings"] == 1
    assert saved["techmarine_stats"]["200"]["rebirths"] == 0


def test_record_rite_rebirth_increments_rebirths():
    """rebirth event increments the techmarine's rebirths counter."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "intensive", "SPIRIT", "rebirth"))

    saved = mock_save.call_args[0][0]
    assert saved["techmarine_stats"]["200"]["rebirths"] == 1
    assert saved["techmarine_stats"]["200"]["first_bindings"] == 0


def test_record_rite_maintenance_does_not_increment_bindings_or_rebirths():
    """maintenance event does not increment first_bindings or rebirths."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "standard", "SPIRIT", "maintenance"))

    saved = mock_save.call_args[0][0]
    stats = saved["techmarine_stats"]["200"]
    assert stats["first_bindings"] == 0
    assert stats["rebirths"] == 0
    assert stats["total_rites"] == 1


def test_record_rite_restoration_does_not_increment_bindings_or_rebirths():
    """restoration event does not increment first_bindings or rebirths."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "intensive", "SPIRIT", "restoration"))

    saved = mock_save.call_args[0][0]
    stats = saved["techmarine_stats"]["200"]
    assert stats["first_bindings"] == 0
    assert stats["rebirths"] == 0
    assert stats["total_rites"] == 1


def test_record_rite_history_capped_at_500():
    """When rite_history exceeds 500 entries, older ones are pruned."""
    data = _default_chronicle()
    # Pre-fill with 500 entries
    data["rite_history"] = [{"dummy": i} for i in range(500)]

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "standard", "SPIRIT", "maintenance"))

    saved = mock_save.call_args[0][0]
    assert len(saved["rite_history"]) == 500
    # The newest entry should be the last one
    assert saved["rite_history"][-1]["event"] == "maintenance"


def test_record_rite_accumulates_multiple_calls():
    """Multiple recordings accumulate correctly for the same techmarine."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_record_rite_in_chronicle(100, 200, "standard", "S1", "first_binding"))
        # Re-use data returned by save for the second call
        data = mock_save.call_args[0][0]

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save2,
    ):
        _run(_record_rite_in_chronicle(101, 200, "standard", "S2", "rebirth"))

    saved = mock_save2.call_args[0][0]
    stats = saved["techmarine_stats"]["200"]
    assert stats["total_rites"] == 2
    assert stats["first_bindings"] == 1
    assert stats["rebirths"] == 1
    assert len(saved["rite_history"]) == 2


# ---------------------------------------------------------------------------
# Pending alert helpers – store / get / clear
# ---------------------------------------------------------------------------


def test_store_pending_alert_saves_fields():
    """_store_pending_alert persists message_id and channel_id for the user."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_store_pending_alert(user_id=999, message_id=111, channel_id=222))

    saved = mock_save.call_args[0][0]
    alert = saved["pending_alerts"]["999"]
    assert alert["message_id"] == 111
    assert alert["channel_id"] == 222
    assert "ts" in alert


def test_get_pending_alert_returns_stored_alert():
    """_get_pending_alert returns the stored dict for a known user."""
    data = _default_chronicle()
    data["pending_alerts"]["42"] = {"message_id": 10, "channel_id": 20, "ts": "x"}

    with patch("opscribe.bot._load_forge_chronicle", return_value=data):
        result = _run(_get_pending_alert(42))

    assert result == {"message_id": 10, "channel_id": 20, "ts": "x"}


def test_get_pending_alert_returns_none_for_unknown_user():
    """_get_pending_alert returns None when no alert is stored for the user."""
    data = _default_chronicle()

    with patch("opscribe.bot._load_forge_chronicle", return_value=data):
        result = _run(_get_pending_alert(9999))

    assert result is None


def test_clear_pending_alert_removes_entry():
    """_clear_pending_alert removes the stored alert for the given user."""
    data = _default_chronicle()
    data["pending_alerts"]["77"] = {"message_id": 1, "channel_id": 2, "ts": "x"}

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_clear_pending_alert(77))

    saved = mock_save.call_args[0][0]
    assert "77" not in saved["pending_alerts"]


def test_clear_pending_alert_noop_for_unknown_user():
    """_clear_pending_alert is a no-op (no save) when the user has no alert."""
    data = _default_chronicle()

    with (
        patch("opscribe.bot._load_forge_chronicle", return_value=data),
        patch("opscribe.bot._save_forge_chronicle") as mock_save,
    ):
        _run(_clear_pending_alert(9999))

    # _save_forge_chronicle should NOT have been called because nothing changed
    mock_save.assert_not_called()


def test_store_and_get_round_trip():
    """Storing an alert and immediately getting it returns the same data."""
    data = _default_chronicle()
    saved_state = {}

    def fake_load():
        return saved_state.get("data", data)

    def fake_save(d):
        saved_state["data"] = d

    with (
        patch("opscribe.bot._load_forge_chronicle", side_effect=fake_load),
        patch("opscribe.bot._save_forge_chronicle", side_effect=fake_save),
    ):
        _run(_store_pending_alert(user_id=55, message_id=300, channel_id=400))
        result = _run(_get_pending_alert(55))

    assert result is not None
    assert result["message_id"] == 300
    assert result["channel_id"] == 400


def test_clear_after_store_removes_alert():
    """Clearing an alert after storing it leaves the user without an alert."""
    data = _default_chronicle()
    saved_state = {}

    def fake_load():
        return saved_state.get("data", data)

    def fake_save(d):
        saved_state["data"] = d

    with (
        patch("opscribe.bot._load_forge_chronicle", side_effect=fake_load),
        patch("opscribe.bot._save_forge_chronicle", side_effect=fake_save),
    ):
        _run(_store_pending_alert(user_id=66, message_id=500, channel_id=600))
        _run(_clear_pending_alert(66))
        result = _run(_get_pending_alert(66))

    assert result is None
