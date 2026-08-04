from opscribe import aar_ops


class DummyChannel:
    def __init__(self, channel_id):
        self.id = channel_id


class DummyGuild:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, channel_id):
        if self._channel and self._channel.id == channel_id:
            return self._channel
        return None


def test_resolve_aar_submission_channel_uses_config_override(monkeypatch):
    configured_channel = DummyChannel(1511147649003425973)
    guild = DummyGuild(configured_channel)
    monkeypatch.setattr(aar_ops._g, "CONFIG", {"aar_submission": {"channel_id": "1511147649003425973"}})

    resolved = aar_ops._resolve_aar_submission_channel(guild)

    assert resolved is configured_channel


def test_resolve_aar_submission_channel_falls_back_to_default(monkeypatch):
    fallback_channel = DummyChannel(1429318686447108300)
    guild = DummyGuild(fallback_channel)
    monkeypatch.setattr(aar_ops._g, "CONFIG", {})
    monkeypatch.setattr(aar_ops, "AAR_CHANNEL_ID", 1429318686447108300)

    resolved = aar_ops._resolve_aar_submission_channel(guild)

    assert resolved is fallback_channel


def test_aar_submission_testing_mode_defaults_true(monkeypatch):
    monkeypatch.setattr(aar_ops._g, "CONFIG", {})

    assert aar_ops._aar_submission_testing_mode() is True


def test_aar_submission_report_markers_for_testing_and_live():
    testing_markers = aar_ops._aar_submission_report_markers(True)
    live_markers = aar_ops._aar_submission_report_markers(False)

    assert testing_markers == ("++ TEST MISSION REPORT ++", "++ END OF TEST REPORT ++")
    assert live_markers == ("++ MISSION REPORT ++", "++ END OF REPORT ++")
