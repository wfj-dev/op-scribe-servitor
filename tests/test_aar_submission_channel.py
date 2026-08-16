import asyncio

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


def test_mission_options_for_ops_include_full_mission_set():
    options = aar_ops._mission_options_for_mode("ops_strat")
    values = [opt.value for opt in options]

    assert values == [
        "pve_inferno",
        "pve_decapitation",
        "pve_vox_liberatis",
        "pve_reliquary",
        "pve_fall_of_atreus",
        "pve_ballistic_engine",
        "pve_termination",
        "pve_obelisk",
        "pve_vortex",
        "pve_reclamation",
        "pve_disruption",
        "pve_exfiltration",
        "pve_purgation",
    ]


def test_default_mission_for_siege_mode_is_template():
    assert aar_ops._default_mission_for_mode("siege") == "siege_template"


def test_max_brothers_for_mode_matches_requested_caps():
    assert aar_ops._max_brothers_for_mode("omega") == 5
    assert aar_ops._max_brothers_for_mode("induction_omega") == 5
    assert aar_ops._max_brothers_for_mode("ops_strat") == 3
    assert aar_ops._max_brothers_for_mode("pvp") == 3


def test_normalize_brother_selection_for_mode_trims_to_cap():
    mentions, ids = aar_ops._normalize_brother_selection_for_mode(
        "ops_strat",
        ["<@1>", "<@2>", "<@3>", "<@4>"],
        [1, 2, 3, 4],
    )
    omega_mentions, omega_ids = aar_ops._normalize_brother_selection_for_mode(
        "omega",
        ["<@1>", "<@2>", "<@3>", "<@4>", "<@5>", "<@6>"],
        [1, 2, 3, 4, 5, 6],
    )

    assert mentions == ["<@1>", "<@2>", "<@3>"]
    assert ids == [1, 2, 3]
    assert omega_mentions == ["<@1>", "<@2>", "<@3>", "<@4>", "<@5>"]
    assert omega_ids == [1, 2, 3, 4, 5]


def test_brother_mentions_from_ids_are_renderable_mentions():
    assert aar_ops._brother_mentions_from_ids([111, 222]) == ["<@111>", "<@222>"]


def test_difficulty_options_for_mode_are_filtered():
    pvp_values = [opt.value for opt in aar_ops._difficulty_options_for_mode("pvp")]
    siege_values = [opt.value for opt in aar_ops._difficulty_options_for_mode("siege")]
    omega_values = [opt.value for opt in aar_ops._difficulty_options_for_mode("omega")]
    ops_values = [opt.value for opt in aar_ops._difficulty_options_for_mode("ops_strat")]

    assert pvp_values == ["@PvP Difficulty"]
    assert siege_values == ["@Normal-Siege", "@Hard-Siege"]
    assert omega_values == ["@Omega", "@Omega-Strat"]
    assert ops_values == [
        "@Ruthless",
        "@Lethal",
        "@Absolute",
        "@Normal-Stratagem",
        "@Hard-Stratagem",
    ]


def test_select_option_builders_mark_current_selection():
    mission_options = aar_ops._mission_select_options("ops_strat", "pve_vortex")
    difficulty_options = aar_ops._difficulty_select_options("siege", "@Normal-Siege")
    mode_options = aar_ops._mode_select_options("omega")
    tag_options = aar_ops._tag_select_options(
        "ops_strat",
        "@Hard-Stratagem",
        "Termination",
        3,
        ["herisor_defense", "black_laurels"],
    )

    assert [opt.value for opt in mission_options if opt.default] == ["pve_vortex"]
    assert [opt.value for opt in difficulty_options if opt.default] == ["@Normal-Siege"]
    assert [opt.value for opt in mode_options if opt.default] == ["omega"]
    assert [opt.value for opt in tag_options if opt.default] == ["black_laurels", "herisor_defense"]


def test_allowed_tag_keys_are_filtered_by_current_aar_state():
    hard_strat_tags = aar_ops._allowed_tag_keys("ops_strat", "@Hard-Stratagem", "Termination", 3, [])
    omega_tags = aar_ops._allowed_tag_keys("omega", "@Omega", "Inferno", 5, [])
    octavian_tags = aar_ops._allowed_tag_keys("omega", "@Omega-Strat", "Vox Liberatis", 5, [])
    pvp_tags = aar_ops._allowed_tag_keys("pvp", "@PvP Difficulty", "PvP Match", 4, [])
    dual_vigil_tags = aar_ops._allowed_tag_keys("ops_strat", "@Absolute", "Inferno", 2, [])
    normal_siege_tags = aar_ops._allowed_tag_keys("siege", "@Normal-Siege", "", 3, [])
    kadaku_hard_strat_tags = aar_ops._allowed_tag_keys("ops_strat", "@Hard-Stratagem", "Inferno", 3, [])

    assert "herisor_defense" in hard_strat_tags
    assert "black_laurels" not in hard_strat_tags
    assert "black_laurels" in omega_tags
    assert "octavian_incident" in octavian_tags
    assert "dual_vigil" in dual_vigil_tags
    assert "herisor_defense" in normal_siege_tags
    assert "leviathan_protocol" in kadaku_hard_strat_tags
    assert pvp_tags == []


def test_allowed_tag_keys_treat_omega_strat_as_omega_for_black_laurels_unlock():
    omega_strat_tags = aar_ops._allowed_tag_keys("omega", "@Omega-Strat", "Inferno", 5, [])

    assert "black_laurels" in omega_strat_tags


def test_supported_aar_tag_keys_match_parser_supported_roles():
    assert aar_ops._AAR_SUBMISSION_TAG_KEY_SET == {
        "black_laurels",
        "leviathan_protocol",
        "black_reef_persecution",
        "herisor_defense",
        "octavian_incident",
        "dual_vigil",
        "pipehitter",
        "distinguished_pipehitter",
        "chapter_approved",
    }


def test_render_submission_difficulty_includes_omega_strat_role_mention():
    rendered = aar_ops._render_submission_difficulty("@Omega-Strat")

    assert rendered == f"<@&{aar_ops.OMEGA_STRAT_ROLE_ID}> @Omega-Strat"


def test_detail_select_option_builders_mark_current_values():
    rank_options = aar_ops._rank_select_options("C")
    armory_options = aar_ops._armory_data_select_options(7)
    kia_options = aar_ops._kia_select_options(2)
    waves_options = aar_ops._waves_select_options(15)
    gene_seed_status_options = aar_ops._gene_seed_status_select_options("carried")
    gene_seed_carrier_options = aar_ops._gene_seed_carrier_select_options(["<@111>", "<@222>"], "222")
    map_options = aar_ops._pvp_map_select_options("Bridge")
    mode_options = aar_ops._pvp_game_mode_select_options("Annihilation")
    result_options = aar_ops._pvp_result_select_options("L")

    assert [opt.value for opt in rank_options if opt.default] == ["C"]
    assert [opt.value for opt in armory_options if opt.default] == ["7"]
    assert [opt.value for opt in kia_options if opt.default] == ["2"]
    assert [opt.value for opt in waves_options if opt.default] == ["15"]
    assert [opt.value for opt in gene_seed_status_options if opt.default] == ["carried"]
    assert [opt.value for opt in gene_seed_carrier_options if opt.default] == ["222"]
    assert [opt.value for opt in map_options if opt.default] == ["Bridge"]
    assert [opt.value for opt in mode_options if opt.default] == ["Annihilation"]
    assert [opt.value for opt in result_options if opt.default] == ["L"]


def test_extract_brother_ids_preserves_unique_order():
    assert aar_ops._extract_brother_ids("<@111> <@!222> <@111>") == [111, 222]


def test_gene_seed_line_is_rendered_for_non_pvp_reports():
    async def _build_report():
        view = aar_ops.AARSubmissionView(guild=None, submitter=None, brother_mentions=["<@111>", "<@222>"])
        view.gene_seed_status = "carried"
        view.gene_seed_carrier_id = "222"
        return view._compose_report()

    report = asyncio.run(_build_report())

    assert "Gene-Seed: carried by <@222>" in report


def test_gene_seed_carrier_resyncs_when_selected_brothers_change():
    async def _carrier_sync():
        view = aar_ops.AARSubmissionView(guild=None, submitter=None, brother_mentions=["<@111>", "<@222>", "<@333>"])
        view.gene_seed_status = "carried"
        view.gene_seed_carrier_id = "333"
        view.mode = "ops_strat"
        view.brothers = ["<@111>", "<@222>"]
        view.brother_ids = [111, 222]
        view._sync_gene_seed_carrier_with_brothers()
        return view.gene_seed_carrier_id

    carrier_id = asyncio.run(_carrier_sync())

    assert carrier_id == "111"


def test_chunk_lines_for_embed_splits_when_over_limit():
    lines = ["a" * 700, "b" * 700, "c" * 100]

    chunks = aar_ops._chunk_lines_for_embed(lines, max_chars=1024)

    assert len(chunks) == 2
    assert "a" * 700 in chunks[0]
    assert "b" * 700 in chunks[1]


def test_chunk_lines_for_embed_keeps_single_page_when_small():
    lines = ["- one.png", "- two.png"]

    chunks = aar_ops._chunk_lines_for_embed(lines, max_chars=1024)

    assert chunks == ["- one.png\n- two.png"]
