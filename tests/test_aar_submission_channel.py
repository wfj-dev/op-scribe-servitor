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


def test_extract_brother_mentions_deduplicates_and_preserves_order():
    raw = "<@111> <@!222> <@111> text <@333>"

    mentions = aar_ops._extract_brother_mentions(raw)

    assert mentions == ["<@111>", "<@222>", "<@333>"]


def test_normalize_submission_tags_accepts_common_aliases():
    raw = "Black Laurels, leviathan-protocol; dual vigil\nherisor_defense"

    tags = aar_ops._normalize_submission_tags(raw)

    assert tags == [
        "black_laurels",
        "leviathan_protocol",
        "dual_vigil",
        "herisor_defense",
    ]


def test_submission_tag_mentions_include_supported_role_mentions():
    tag_mentions = aar_ops._submission_tag_mentions([
        "black_laurels",
        "herisor_defense",
        "pipehitter",
        "distinguished_pipehitter",
    ])

    assert f"<@&{aar_ops.BLACK_LAURELS_ROLE_ID}>" in tag_mentions
    assert f"<@&{aar_ops.HERISOR_DEFENSE_TAG_ROLE_ID}>" in tag_mentions
    assert f"<@&{aar_ops.PIPEHITTER_ROLE_ID}>" in tag_mentions
    assert f"<@&{aar_ops.DISTINGUISHED_PIPEHITTER_ROLE_ID}>" in tag_mentions


def test_mission_options_for_mode_are_filtered_for_pvp():
    options = aar_ops._mission_options_for_mode("pvp")
    values = [opt.value for opt in options]

    assert values == ["pvp_match", "pvp_scrim"]
