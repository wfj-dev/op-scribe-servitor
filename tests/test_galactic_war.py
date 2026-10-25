from datetime import datetime, timedelta, timezone
from fractions import Fraction
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import replace

import pytest

from opscribe.galactic_war import (
    Contribution, Renown, command_renown, formation_renown,
    lifetime_laurel, renown_tier, WarMember, full_commitment_credit, advance_balance, atlas_compatible,
    REQUIREMENT_WEIGHTS, EQUERRY_TITLES, effective_roles, specialist_requirement, meets_specialist_requirement,
    WarReportPending,
    shared_campaign_content,
)
from opscribe.galactic_war_store import GalacticWarStore

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def test_authored_objectives_require_exact_world_and_biome_settings():
    general = {"settings": ["hive|underhive", "feudal|industrial"]}
    assert atlas_compatible("hive", "underhive", general)
    assert atlas_compatible("feudal", "industrial", general)
    assert not atlas_compatible("hive", "industrial", general)
    assert not atlas_compatible("unknown", "underhive", general)
    assert not atlas_compatible("hive", "unknown", general)
    assert not atlas_compatible("hive", "underhive", {"settings": []})
    specialist = {"settings": ["hive|asteroid"], "required_rank": "Watch Apothecary"}
    assert atlas_compatible("hive", "asteroid", specialist)
    assert not atlas_compatible("hive", "underhive", specialist)
    assert not atlas_compatible("hive", "asteroid", {**specialist, "required_rank": "Watch Master"})


def shared_content_fixture():
    geography = {
        "systems": [{"id": "system", "sectorId": "sector_29"}],
        "worldClasses": {world: {"name": name, "symbol": symbol} for world, name, symbol in (
            ("agri", "Alpha", "\u03b1"), ("civilised", "Gamma", "\u03b3"),
            ("dead", "Delta", "\u03b4"), ("death", "Delta Tau", "\u03b4\u03c4"),
            ("cemetery", "Epsilon", "\u03b5"), ("hive", "Eta", "\u03b7"),
            ("feudal", "Mu", "\u03bc"), ("research", "Rho", "\u03c1"),
            ("forge", "Phi", "\u03c6"), ("feral", "Phi Lambda", "\u03c6\u03bb"),
        )},
        "bodies": [
            {"id": "calyx", "systemId": "system", "name": "Calyx", "kind": "research_station", "biome": "ice", "battleEligible": True},
            {"id": "legacy", "systemId": "system", "name": "Legacy", "kind": "planet", "battleEligible": True},
            {"id": "star", "kind": "star", "battleEligible": False},
        ],
        "objectiveCatalog": {"schemaVersion": 1, "objectives": [
            {"id": "purge", "name": "Shared Name", "category": "general", "settings": ["research|ice"]},
            {"id": "study", "name": "Study", "category": "specialist", "settings": ["research|ice"]},
        ]},
    }
    definition = {"objectives": [{"id": "purge", "name": "Old", "theme": "Preserved"}, {"id": "study", "required_rank": "Watch Librarian"}], "planets": {
        "calyx": {"world_type": "civilised", "biome": "warzone", "provisional": True},
        "legacy": {"world_type": "feral", "biome": "desert", "provisional": True},
    }}
    return geography, definition


def test_shared_content_prefers_authored_terrain_and_preserves_bot_policy():
    geography, definition = shared_content_fixture()
    planets, objectives = shared_campaign_content(geography, definition)
    assert len(planets) == 2
    assert (planets[0]["world_type"], planets[0]["biome"], planets[0]["terrain_provisional"]) == ("research", "ice", False)
    assert planets[0]["world_class"] == {"name": "Rho", "symbol": "\u03c1"}
    assert (planets[1]["world_type"], planets[1]["biome"], planets[1]["terrain_provisional"]) == ("feral", "desert", True)
    assert objectives[0]["name"] == "Shared Name"
    assert objectives[0]["theme"] == "Preserved"
    assert objectives[1]["required_rank"] == "Watch Librarian"
    assert "settings" not in definition["objectives"][0]


@pytest.fixture
def recipe_content():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    geography_path = root.parent.parent / "strategium" / "data" / "reach_geography.json"
    if not geography_path.exists():
        pytest.skip("Matching Strategium geography is required for recipe integration")
    reference = root / "reference"
    load = lambda name: json.loads((reference / name).read_text())
    _, catalogue = shared_campaign_content(json.loads(geography_path.read_text()), load("galactic_war.json"))
    return load("galactic_war_vanilla_recipes.json"), load("operations.json"), load("stratagems.json"), catalogue


def test_vanilla_recipes_cover_every_shared_setting_and_preserve_modifier_counts(recipe_content):
    from opscribe.galactic_war_recipes import VanillaRecipes

    recipes = VanillaRecipes(*recipe_content)
    assert len(recipes.definition["objectives"]) == 29
    assert recipes.coverage_count == sum(len(entry["settings"]) for entry in recipe_content[3])
    assert recipes.coverage_count >= 387
    for objective in recipe_content[3]:
        for setting in objective["settings"]:
            world, biome = setting.split("|")
            mission, recipe = recipes.resolve(world, biome, objective)
            assert recipes.resolve(world, biome, objective) == (mission, recipe)
            assert recipe["id"] == f"{setting}|{objective['id']}"
            assert len(recipe["negatives"]) == len(set(recipe["negatives"])) == 3
            assert recipes.stratagems[recipe["positive"]]["type"] == "buff"
            assert all(recipes.stratagems[name]["type"] == "debuff" for name in recipe["negatives"])
            assert recipe["forced"] == (["Intelligence Lapse"] if recipes.operations[mission]["faction_listed"] == "chaos" else [])
            if biome == "starship":
                assert mission == "Reclamation"
            if biome in {"ice", "asteroid"}:
                assert recipe["adaptation"] == "approximate_environment"
    with pytest.raises(ValueError, match="Unsupported Atlas"):
        recipes.resolve("hive", "ocean", recipe_content[3][0])


@pytest.mark.parametrize("invalid", ["coverage", "excluded", "kind", "duplicate", "conflict", "category", "operation", "conversion", "version"])
def test_vanilla_recipes_reject_invalid_content(recipe_content, invalid):
    from opscribe.galactic_war_recipes import VanillaRecipes

    definition, operations, stratagems, catalogue = recipe_content
    if invalid == "coverage":
        definition["objectives"].pop("purge")
    elif invalid == "excluded":
        definition["objectives"]["purge"]["positive"] = "Harvest of Vitae"
    elif invalid == "kind":
        definition["objectives"]["purge"]["positive"] = "Tsunami"
    elif invalid == "duplicate":
        definition["worlds"]["forge"]["negative"] = "Tsunami"
    elif invalid == "conflict":
        definition["objectives"]["vanguard"]["negative"] = "Enduring Foes"
    elif invalid == "category":
        definition["worlds"]["agri"]["negative"] = "Scavenger"
        definition["biomes"]["starship"]["negative"] = "Rationing"
    elif invalid == "operation":
        definition["objectives"]["purge"]["missions"] = ["Siege"]
    elif invalid == "conversion":
        next(entry for entry in operations["operations"] if entry["name"] == "Reliquary")["intel_lapse_forced"] = False
    else:
        definition["version"] = True
    with pytest.raises(ValueError):
        VanillaRecipes(definition, operations, stratagems, catalogue)


def test_vanilla_recipe_examples_and_vortex_boss_caveat(recipe_content):
    from opscribe.galactic_war_recipes import VanillaRecipes

    recipes = VanillaRecipes(*recipe_content)
    catalogue = {entry["id"]: entry for entry in recipe_content[3]}
    mission, recipe = recipes.resolve("forge", "jungle", catalogue["purge"])
    assert mission == "Inferno"
    assert recipe["positive"] == "Surplus"
    assert recipe["negatives"] == ["Tsunami", "Detonation Risk", "Myopia"]
    mission, recipe = recipes.resolve("cemetery", "desert", catalogue["geneseed"])
    assert mission == "Fall of Atreus"
    assert recipe["forced"] == ["Intelligence Lapse"]
    mission, recipe = recipes.resolve("research", "ice", catalogue["specimen"])
    assert mission == "Purgation"
    assert recipe["adaptation"] == "approximate_environment"
    definition = recipe_content[0]
    definition["objectives"]["psyhunt"]["missions"] = ["Vortex"]
    recipes = VanillaRecipes(*recipe_content)
    mission, recipe = recipes.resolve("research", "jungle", catalogue["psyhunt"])
    assert mission == "Vortex"
    assert recipe["forced"] == ["Intelligence Lapse"]
    assert any("not the fixed Mutalith" in note for note in recipe["notes"])


def test_runtime_operations_support_all_recipes_without_changing_omega(recipe_content):
    from opscribe import galactic_war_runtime as runtime
    from opscribe import aar_ops
    from opscribe.galactic_war_recipes import VanillaRecipes

    operations = runtime.vanilla_operations()
    vanilla = [entry for entry in operations if entry["mode"] == "ops_strat"]
    assert len(vanilla) == 13
    assert {entry["difficulty_class"] for entry in vanilla} == {"hard_stratagem"}
    canonical = {entry["name"]: entry for entry in recipe_content[1]["operations"]}
    expected_omega = {
        (mission.label, difficulty.value)
        for mission in aar_ops._mission_options_for_mode("omega")
        if canonical[mission.label]["faction_listed"] in {"tyranid", "mixed"}
        and not canonical[mission.label].get("intel_lapse_forced")
        and mission.label != "Siege"
        for difficulty in aar_ops._difficulty_options_for_mode("omega")
    }
    assert {(entry["mission"], entry["difficulty"]) for entry in operations if entry["mode"] == "omega"} == expected_omega
    recipes = VanillaRecipes(*recipe_content)
    for objective in recipe_content[3]:
        for setting in objective["settings"]:
            operation, recipe = recipes.select(*setting.split("|"), objective, operations)
            assert operation["capacity"] == 3
            assert operation["difficulty_class"] == "hard_stratagem"
    with pytest.raises(ValueError, match="No supported Hard-Stratagem"):
        recipes.select("forge", "jungle", next(entry for entry in recipe_content[3] if entry["id"] == "purge"), [])


@pytest.fixture
def recipe_war(war, recipe_content):
    from opscribe.galactic_war_recipes import VanillaRecipes
    from opscribe.galactic_war_runtime import vanilla_operations

    war.catalogue = recipe_content[3]
    war.operations = vanilla_operations()
    war.recipes = VanillaRecipes(*recipe_content)
    return war


@pytest.mark.parametrize("success", [True])
def test_recipe_strikes_require_review_and_freeze_assignments_across_content_changes(recipe_war, members, monkeypatch, success):
    from copy import deepcopy

    war = recipe_war
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    original = war.snapshot(NOW)["boards"]["alpha"]
    assert all("recipe" in offer for offer in original[:4])
    assert all("recipe" not in offer for offer in original[4:])
    offer = original[0]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=offer["id"])
    attempt_id = result["attempt_id"]
    frozen = deepcopy(offer["recipe"])
    war.recipes.definition["version"] += 1
    war.recipes.definition["objectives"][offer["objective_id"]]["positive"] = "Surplus"
    reopened = GalacticWarStore(war.path, war.geography, war.catalogue, war.operations, recipes=war.recipes)
    assert reopened.snapshot(NOW)["attempts"][attempt_id]["offer"]["recipe"] == frozen
    participants = ["captain", "brother", "other"]
    with pytest.raises(WarReportPending, match="independent evidence approval"):
        war.settle(attempt_id, "recipe-aar", participants, NOW, success=success)
    record = {
        "aar_id": "recipe-aar", "strike_id": attempt_id, "brother_ids": participants,
        "mission": offer["aar_mission"], "difficulty_class": "hard_stratagem",
        "strike_success": success, "strike_evidence_hashes": ["selection", "result"],
    }
    with pytest.raises(ValueError, match="mission or difficulty"):
        war.submit_report({**record, "difficulty_class": "absolute_ops"}, NOW)
    for _retry in range(2):
        with pytest.raises(WarReportPending):
            war.submit_report(record, NOW)
    pending = war.snapshot(NOW)
    assert not pending["settlements"]
    assert not pending["contributions"]
    assert "recipe stratagems" in pending["reviews"][attempt_id]["reason"]
    members["captain"] = replace(members["captain"], can_review=True)
    with pytest.raises(PermissionError, match="own strike"):
        act(war, members, "captain", "review", strike_id=attempt_id, decision="approve", reason="Self approval")
    act(war, members, "wm", "review", strike_id=attempt_id, decision="approve", reason="Evidence matches the locked recipe and result")
    with pytest.raises(WarReportPending):
        war.settle(attempt_id, "different-aar", participants, NOW, success=success)
    settled = war.submit_report(record, NOW)
    assert war.submit_report(record, NOW) == settled
    state = war.snapshot(NOW)
    assert state["attempts"][attempt_id]["offer"]["recipe"] == frozen
    assert state["boards"]["alpha"][1:] == original[1:]
    current = state["boards"]["alpha"][0]
    if success:
        assert current["id"] != offer["id"]
        assert current["generation"] == 1
        assert current["recipe"]["version"] == frozen["version"] + 1
        assert len(state["contributions"]) == 3
    else:
        assert current["id"] == offer["id"]
        assert current["recipe"] == frozen
        assert current["status"] == "available"
        assert not state["contributions"]


def test_recipe_selection_preserves_specialist_rolls_and_omega_without_recipes(recipe_war):
    war = recipe_war
    planet = war.geography["planets"][0]
    for generation in range(200):
        offer = war._offer("campaign", planet, 0, generation)
        assert bool(offer["required_rank"]) == (offer["requirement_roll"] >= 150)
        assert offer["aar_difficulty_class"] == "hard_stratagem"
        assert offer["recipe"]["id"] == f"hive|underhive|{offer['objective_id']}"
        omega = war._offer("campaign", planet, 0, generation, pool="omega")
        assert "recipe" not in omega
        assert omega["capacity"] == 5


def test_runtime_loads_real_recipes_and_generates_both_boards_for_sector_29(tmp_path, monkeypatch):
    from opscribe import galactic_war_runtime as runtime

    monkeypatch.setattr(runtime._g, "CONFIG", {"data_dir": str(tmp_path)})
    runtime._reference_store.cache_clear()
    try:
        store = runtime.get_store()
        assert store.recipes.coverage_count >= 387
        assert runtime.get_store() is store
        actor = WarMember("wm", frozenset({"Watch Master"}))
        store.execute("invade", actor, {"version": 0, "subsector_id": "sector_29"}, {"wm": actor}, NOW)
        boards = store.snapshot(NOW)["boards"]
        assert len(boards) == 19
        assert sum(len(board) for board in boards.values()) == 152
        for board in boards.values():
            assert Counter(offer["pool"] for offer in board) == {"vanilla": 4, "omega": 4}
            for offer in board:
                if offer["pool"] == "vanilla":
                    assert offer["aar_difficulty_class"] == "hard_stratagem"
                    assert len(offer["recipe"]["negatives"]) == 3
                else:
                    assert "recipe" not in offer
    finally:
        runtime._reference_store.cache_clear()


def test_runtime_reference_cache_is_bounded_invalidated_and_does_not_cache_state(tmp_path, monkeypatch):
    import json
    from opscribe import galactic_war_runtime as runtime

    geography, definition = shared_content_fixture()
    geography["sectors"] = [{"id": "sector_29", "name": "The Canticle Reach", "boundary": [[0, 0], [1, 0], [1, 1]], "label": [0, 0]}]
    geography["systems"][0].update(x=100, y=200)
    reference = tmp_path / "reference"
    reference.mkdir()
    geography_path = reference / "geography.json"
    definition_path = reference / "war.json"
    operations_path = reference / "operations.json"
    stratagems_path = reference / "stratagems.json"
    recipes_path = reference / "galactic_war_vanilla_recipes.json"
    geography_path.write_text(json.dumps(geography))
    definition_path.write_text(json.dumps(definition))
    operations_path.write_text("{}")
    stratagems_path.write_text("{}")
    recipes_path.write_text("{}")
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    config = {"data_dir": str(tmp_path / "state"), "galactic_war": {"geography_path": str(geography_path), "definition_path": str(definition_path)}}
    monkeypatch.setattr(runtime._g, "CONFIG", config)
    loads = []
    monkeypatch.setattr(runtime, "vanilla_operations", lambda: loads.append(True) or [])
    class FakeRecipes:
        def __init__(self, *args):
            pass

        def select(self, *args):
            return {}, {}

    monkeypatch.setattr(runtime, "VanillaRecipes", FakeRecipes)
    runtime._reference_store.cache_clear()
    try:
        first = runtime.get_store()
        assert runtime.get_store() is first
        assert len(loads) == 1
        assert "boundary" not in first.geography["sectors"][0]
        assert "label" not in first.geography["sectors"][0]
        assert "x" not in first.geography["systems"][0]
        assert "y" not in first.geography["systems"][0]
        assert first.snapshot(NOW)["version"] == 0
        with first._connection() as connection:
            state = first._load(connection)
            state["version"] = 1
            first._save(connection, state)
        assert runtime.get_store().snapshot(NOW)["version"] == 1
        for path in (geography_path, definition_path, operations_path, stratagems_path, recipes_path):
            path.write_text(path.read_text() + " ")
            refreshed = runtime.get_store()
            assert refreshed is not first
            assert runtime.get_store() is refreshed
            assert runtime._reference_store.cache_info().currsize == 1
            first = refreshed
        assert len(loads) == 6
        config["data_dir"] = str(tmp_path / "other-state")
        assert runtime.get_store().path == tmp_path / "other-state" / "galactic_war.sqlite3"
        assert runtime._reference_store.cache_info().currsize == 1
        geography["objectiveCatalog"]["schemaVersion"] = 2
        geography_path.write_text(json.dumps(geography))
        with pytest.raises(ValueError, match="catalogue version"):
            runtime.get_store()
    finally:
        runtime._reference_store.cache_clear()


@pytest.mark.parametrize("invalid", ["version", "missing", "unknown_id", "duplicate", "category", "pair", "terrain"])
def test_shared_content_rejects_mismatched_or_invalid_reference_data(invalid):
    geography, definition = shared_content_fixture()
    catalog = geography["objectiveCatalog"]
    if invalid == "version":
        catalog["schemaVersion"] = 2
    elif invalid == "missing":
        catalog["objectives"].pop()
    elif invalid == "unknown_id":
        catalog["objectives"][0]["id"] = "unrecognized"
    elif invalid == "duplicate":
        catalog["objectives"].append(catalog["objectives"][0])
    elif invalid == "category":
        catalog["objectives"][0]["category"] = "specialist"
    elif invalid == "pair":
        catalog["objectives"][0]["settings"] = ["research|ocean"]
    else:
        geography["bodies"][0]["biome"] = "unknown"
    with pytest.raises(ValueError):
        shared_campaign_content(geography, definition)


@pytest.mark.parametrize("score,tier", [(0, 0), (3.99, 0), (4, 1), (8, 2), (28, 3), (56, 4)])
def test_renown_thresholds(score, tier):
    assert renown_tier(score) == tier


def test_requirement_table_exactly_matches_approved_percentages():
    assert Counter(specialist_requirement(roll) for roll in range(200)) == REQUIREMENT_WEIGHTS
    assert REQUIREMENT_WEIGHTS[""] == 150
    assert sum(weight for rank, weight in REQUIREMENT_WEIGHTS.items() if rank) == 50
    assert all(REQUIREMENT_WEIGHTS[rank] == 1 for rank in ("Blade Master", "High Chaplain", "Chief Apothecary", "Void Warden", "Forgemaster", "Huntmaster"))
    for rank in ("Watch Captain", "Watch Master", "Watch Sergeant", "Oathsworn", "Honored Dreadnought", "Venerable Dreadnought"):
        assert rank not in REQUIREMENT_WEIGHTS
        assert not atlas_compatible("hive", "underhive", {"required_rank": rank, "gate_kind": "prestige"})


@pytest.mark.parametrize("base,title", EQUERRY_TITLES.items())
def test_intermediate_requirements_use_base_role_plus_equerry(base, title):
    basic = WarMember("basic", frozenset({base}))
    equerry = WarMember("equerry", frozenset({base, "High Command Equerry"}))
    assert title not in effective_roles(basic.roles)
    assert not meets_specialist_requirement(basic, title)
    assert meets_specialist_requirement(equerry, title)
    assert meets_specialist_requirement(WarMember("future", frozenset({title})), title)
    assert not meets_specialist_requirement(WarMember("wm", frozenset({"Watch Master"})), title)


def test_senior_substitution_is_same_track_only():
    chief = WarMember("chief", frozenset({"Chief Apothecary"}))
    assert meets_specialist_requirement(chief, "Primus Medicae")
    assert meets_specialist_requirement(chief, "Watch Apothecary")
    assert not meets_specialist_requirement(chief, "Watch Chaplain")
    assert not meets_specialist_requirement(chief, "Watch Captain")


def test_formation_average_includes_members_who_never_played():
    contributions = [Contribution(str(index), "one", "cadre", NOW, True) for index in range(40)]
    result = formation_renown({"cadre": map(str, range(10))}, contributions, NOW)
    assert result["cadre"].score == 4
    assert result["cadre"].tier == 1


def test_each_actual_participant_counts_once_not_once_per_mission():
    contributions = [Contribution("strike", member, "kt", NOW, True) for member in ("a", "b", "c")]
    assert formation_renown({"kt": ["a", "b", "c", "d", "e", "f"]}, contributions, NOW)["kt"].score == Fraction(1, 2)


def test_assists_and_rolling_window_do_not_add_hidden_diminishing_penalty():
    contributions = [Contribution(str(index), "a", "kt", NOW, True) for index in range(56)]
    contributions.extend([
        Contribution("assist", "a", "kt", NOW, False),
        Contribution("expired", "a", "kt", NOW - timedelta(days=28), True),
        Contribution("future", "a", "kt", NOW + timedelta(seconds=1), True),
    ])
    assert formation_renown({"kt": ["a"]}, contributions, NOW)["kt"].score == Fraction(225, 4)


def test_command_averages_all_kt_scores_equally_not_tiers_or_member_totals():
    result = command_renown([Renown(Fraction(4), 2), Renown(Fraction(8), 20), Renown(Fraction(30), 3)])
    assert result.score == 14
    assert result.tier == 2
    assert command_renown([Renown(), Renown(Fraction(8))]).score == 4
    assert command_renown([]).tier == 0


def test_duplicate_participation_is_rejected():
    contribution = Contribution("strike", "a", "kt", NOW, True)
    with pytest.raises(ValueError, match="Duplicate"):
        formation_renown({"kt": ["a"]}, [contribution, contribution], NOW)


@pytest.mark.parametrize("count,laurel", [(99, ""), (100, "Bronze"), (199, "Bronze"), (200, "Silver"), (299, "Silver"), (300, "Gold")])
def test_lifetime_laurels(count, laurel):
    assert lifetime_laurel(count) == laurel


@pytest.fixture
def war(tmp_path):
    geography = {
        "sectors": [{"id": name} for name in ("alpha", "beta", "gamma")],
        "systems": [{"id": f"{name}-{index}", "sectorId": name} for name in ("alpha", "beta", "gamma") for index in range(3)],
        "planets": [{"id": name, "subsector_id": name, "system_id": f"{name}-0", "world_type": "hive", "biome": "underhive"} for name in ("alpha", "beta", "gamma")],
    }
    catalogue = [{"id": str(index), "name": f"Objective {index}"} for index in range(5)]
    catalogue += [{"id": rank, "name": rank, "required_rank": rank, "worlds": ["hive"], "biomes": ["underhive"]} for rank in REQUIREMENT_WEIGHTS if rank]
    operations = [{"mode": "ops_strat", "capacity": 3, "mission": "Inferno", "mission_value": "pve_inferno", "difficulty": "@Absolute", "difficulty_class": "absolute_ops"}]
    operations.append({**operations[0], "mode": "omega", "capacity": 5, "difficulty": "@Omega", "difficulty_class": "omega_ops"})
    return GalacticWarStore(tmp_path / "war.sqlite3", geography, catalogue, operations)


@pytest.fixture
def members():
    return {member.member_id: replace(member, watch_brother=True) for member in (
        WarMember("wm", frozenset({"Watch Master"})),
        WarMember("captain", frozenset({"Watch Captain"}), "1", "1-1"),
        WarMember("lt", frozenset({"Watch Lieutenant"}), "1", "1-1"),
        WarMember("sergeant", frozenset({"Watch Sergeant"}), "1", "1-1"),
        WarMember("brother", frozenset({"Watch Brother"}), "1", "1-1"),
        WarMember("other", frozenset({"Watch Captain"}), "2", "2-1"),
    )}


def act(war, members, who, action, **payload):
    return war.execute(action, members[who], {"version": war.snapshot(NOW)["version"], **payload}, members, NOW)


def open_campaign(war, members, subsector="alpha"):
    act(war, members, "wm", "invade", subsector_id=subsector)


def test_multiple_companies_and_killteams_can_share_locations(war, members):
    open_campaign(war, members)
    for company, actor in (("1", "captain"), ("2", "other")):
        act(war, members, actor, "commit", company_id=company, subsector_id="alpha")
        act(war, members, actor, "assign", kill_team_id=f"{company}-1", system_id="alpha-0", reason="Sergeant unavailable")
    state = war.snapshot(NOW)
    assert state["commitments"] == {"1": "alpha", "2": "alpha"}
    assert state["assignments"] == {"1-1": "alpha-0", "2-1": "alpha-0"}
    assert len(state["boards"]["alpha"]) == 8


def test_strike_reports_are_completion_only_and_cancellation_is_not_a_failure(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer = war.snapshot(NOW)["boards"]["alpha"][0]
    for who in ("captain", "brother", "other"):
        result = act(war, members, who, "join", strike_id=offer["id"])
    attempt_id = result["attempt_id"]
    record = {"aar_id": "completion", "strike_id": attempt_id, "brother_ids": ["captain", "brother", "other"], "mission": "Inferno", "difficulty_class": "absolute_ops"}
    before = war.snapshot(NOW)
    with pytest.raises(ValueError, match="Only completed"):
        war.submit_report({**record, "strike_success": False}, NOW)
    with pytest.raises(ValueError, match="Only completed"):
        war.settle(attempt_id, "failure", record["brother_ids"], NOW, success=False)
    assert war.snapshot(NOW) == before
    act(war, members, "captain", "cancel", strike_id=offer["id"], reason="Roster unavailable")
    assert not war.snapshot(NOW)["settlements"]
    assert not war.snapshot(NOW)["contributions"]
    assert war.snapshot(NOW)["campaigns"]["alpha"]["last_success_at"] == before["campaigns"]["alpha"]["last_success_at"]
    for who in ("captain", "brother", "other"):
        result = act(war, members, who, "join", strike_id=offer["id"])
    assert result["attempt_id"] != attempt_id
    completed = war.submit_report({**record, "strike_id": result["attempt_id"]}, NOW)
    assert completed["success"] is True


@pytest.mark.parametrize("role", ["Watch Master", "Forgemaster"])
def test_invasion_sandbox_is_private_reversible_and_never_changes_live_state(war, members, role):
    from opscribe.galactic_war_sandbox import InvasionSandbox

    actor = replace(members["wm"], roles=frozenset({role}))
    before = war.snapshot(NOW)
    sandbox = InvasionSandbox(war, actor)
    session = sandbox.start("alpha", NOW)
    store, clock = sandbox.context(session, NOW)
    assert len(store.snapshot(clock)["boards"]["alpha"]) == 8
    sandbox.execute(session, "commit", {"version": 0, "company_id": "1", "subsector_id": "alpha"}, members, NOW)
    sandbox.execute(session, "assign", {"version": 1, "kill_team_id": "1-1", "system_id": "alpha-0", "reason": "Test assignment"}, members, NOW)
    identity = store.snapshot(clock)["boards"]["alpha"][0]["id"]
    sandbox.execute(session, "simulate_completion", {"version": 2, "strike_id": identity}, members, NOW)
    assert len(store.snapshot(clock)["contributions"]) == 3
    assert war.snapshot(NOW) == before
    stranger = InvasionSandbox(war, replace(actor, member_id="other-owner"))
    with pytest.raises(ValueError):
        stranger.context(session, NOW)
    with pytest.raises(PermissionError):
        InvasionSandbox(war, replace(actor, roles=frozenset({"Watch Captain"})))
    reopened = InvasionSandbox(war, actor)
    assert reopened.current()["id"] == session
    reopened.revert(session, NOW)
    reopened.revert(session, NOW)
    assert reopened.current() is None
    with pytest.raises(ValueError, match="ended or changed"):
        store.snapshot(clock)
    assert war.snapshot(NOW) == before


def test_lt_approval_is_required_but_cover_assignment_is_direct(war, members):
    open_campaign(war, members)
    result = act(war, members, "lt", "commit", company_id="1", subsector_id="alpha")
    assert not war.snapshot(NOW)["commitments"]
    with pytest.raises(PermissionError):
        act(war, members, "lt", "approve", proposal_id=result["proposal_id"])
    with pytest.raises(PermissionError):
        act(war, members, "other", "approve", proposal_id=result["proposal_id"])
    act(war, members, "captain", "approve", proposal_id=result["proposal_id"])
    act(war, members, "lt", "assign", kill_team_id="1-1", system_id="alpha-0", reason="Sergeant unavailable")
    assert war.snapshot(NOW)["assignments"]["1-1"] == "alpha-0"


def test_assignment_redeployment_budget_is_shared_between_officers(war, members):
    open_campaign(war, members)
    act(war, members, "captain", "commit", company_id="1", subsector_id="alpha")
    for actor, system in (("sergeant", "alpha-0"), ("lt", "alpha-1"), ("captain", "alpha-2")):
        act(war, members, actor, "assign", kill_team_id="1-1", system_id=system, reason="Cover")
    with pytest.raises(ValueError, match="Two redeployments"):
        act(war, members, "wm", "assign", kill_team_id="1-1", system_id="alpha-0", reason="Cover")


def test_stale_mutations_and_missing_content_fail_without_partial_state(war, members):
    with pytest.raises(ValueError, match="State changed"):
        war.execute("invade", members["wm"], {"version": 42}, members, NOW)
    war.catalogue.clear()
    with pytest.raises(ValueError, match="compatible"):
        open_campaign(war, members)
    assert war.snapshot(NOW)["version"] == 0
    assert not war.snapshot(NOW)["campaigns"]


def test_line_command_and_specialist_credit_have_different_scopes(members):
    commitments, assignments = {"1": "alpha"}, {"1-1": "alpha-0"}
    assert full_commitment_credit(members["brother"], "alpha", "alpha-0", commitments, assignments)
    assert not full_commitment_credit(members["brother"], "alpha", "alpha-1", commitments, assignments)
    assert full_commitment_credit(members["captain"], "alpha", "alpha-1", commitments, assignments)
    specialist = WarMember("spec", frozenset({"Watch Apothecary"}), cadre_id="apothecarion")
    assert not full_commitment_credit(specialist, "alpha", "alpha-0", commitments, assignments)
    assert full_commitment_credit(specialist, "alpha", "alpha-0", commitments, assignments, ["spec"])


def test_full_signup_freezes_credit_and_completion_replaces_one_slot_once(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    act(war, members, "captain", "commit", company_id="1", subsector_id="alpha")
    act(war, members, "sergeant", "assign", kill_team_id="1-1", system_id="alpha-0")
    offer_id = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for actor in ("captain", "brother", "other"):
        result = act(war, members, actor, "join", strike_id=offer_id)
    attempt = result["attempt_id"]
    record = {"aar_id": "aar-1", "brother_ids": ["captain", "brother", "other"], "mission": "Inferno", "difficulty_class": "absolute_ops", "strike_success": True}
    war.validate_report(attempt, record, NOW)
    with pytest.raises(ValueError, match="mission or difficulty"):
        war.validate_report(attempt, {**record, "mission": "Decapitation"}, NOW)
    with pytest.raises(ValueError, match="signup roster"):
        war.validate_report(attempt, {**record, "brother_ids": record["brother_ids"] + ["brother"]}, NOW)
    war.reserve_report(attempt, "original-report", NOW)
    war.reserve_report(attempt, "original-report", NOW)
    with pytest.raises(ValueError, match="already has an AAR"):
        war.reserve_report(attempt, "duplicate-report", NOW)
    assert len(war.snapshot(NOW)["boards"]["alpha"]) == 8
    act(war, members, "lt", "assign", kill_team_id="1-1", system_id="alpha-1", reason="Cover")
    settled = war.settle(attempt, "aar-1", ["captain", "brother", "other"], NOW + timedelta(hours=1), success=True)
    assert war.settle(attempt, "aar-1", ["other", "brother", "captain"], NOW + timedelta(hours=2), success=True) == settled
    state = war.snapshot(NOW + timedelta(hours=2))
    assert len(state["contributions"]) == 3
    assert state["contributions"][1]["full_credit"] is True
    assert state["contributions"][2]["full_credit"] is False
    assert len(state["boards"]["alpha"]) == 8
    assert state["boards"]["alpha"][0]["generation"] == 1
    assert state["boards"]["alpha"][0]["id"] != offer_id
    with pytest.raises(ValueError, match="different result"):
        war.settle(attempt, "aar-2", ["captain", "brother", "other"], NOW, success=True)


def test_pressure_integration_is_restart_and_tick_independent():
    campaign = {"balance": 25, "calculated_at": NOW.isoformat(), "last_success_at": NOW.isoformat(), "deadline": (NOW + timedelta(days=14)).isoformat()}
    one_step = dict(campaign)
    advance_balance(one_step, NOW + timedelta(days=4))
    for hour in range(1, 97):
        advance_balance(campaign, NOW + timedelta(hours=hour))
    assert campaign["balance"] == pytest.approx(one_step["balance"])
    assert campaign["balance"] == pytest.approx(15)


def test_pressure_tracks_time_since_completion_not_cancelled_rosters(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for who in ("captain", "brother", "other"):
        act(war, members, who, "join", strike_id=identity)
    act(war, members, "captain", "cancel", strike_id=identity, reason="No completion")
    later = NOW + timedelta(hours=72)
    campaign = war.tick(later)["campaigns"]["alpha"]
    assert campaign["last_success_at"] == NOW.isoformat()
    before = campaign["balance"]
    for who in ("captain", "brother", "other"):
        result = war.execute("join", members[who], {"version": war.snapshot(later)["version"], "strike_id": identity}, members, later)
    war.settle(result["attempt_id"], "completed-after-gap", ["captain", "brother", "other"], later)
    campaign = war.snapshot(later)["campaigns"]["alpha"]
    assert campaign["last_success_at"] == later.isoformat()
    assert campaign["balance"] == pytest.approx(before + 1 / 1.5)


def test_deadline_loss_spreads_once_and_archives_closed_boards(war, members):
    war.geography["neighbors"] = {"alpha": ["beta"], "beta": ["alpha"]}
    open_campaign(war, members)
    after = NOW + timedelta(days=14)
    state = war.tick(after)
    assert state["campaigns"]["alpha"]["status"] == "lost"
    assert state["campaigns"]["alpha"]["spread_to"] == "beta"
    assert state["campaigns"]["beta"]["escalation"] == 1.5
    assert state["campaigns"]["beta"]["balance"] == 25
    identity = state["campaigns"]["beta"]["id"]
    assert war.tick(after)["campaigns"]["beta"]["id"] == identity
    assert all(offer["status"] == "closed" for offer in state["boards"]["alpha"])


def test_exactly_fifty_passes_without_rounded_comparison(war, members):
    open_campaign(war, members)
    deadline = NOW + timedelta(days=14)
    with war._connection() as connection:
        state = war._load(connection)
        state["campaigns"]["alpha"].update(balance=50, calculated_at=deadline.isoformat())
        war._save(connection, state)
    assert war.tick(deadline)["campaigns"]["alpha"]["status"] == "secure"


def test_company_score_includes_empty_teams(war):
    result = war.recognition({"1-1": []}, {"1": ["1-1", "1-2"]}, NOW)
    assert result["companies"]["1"]["score"] == 0


def test_store_can_reopen_after_writes(war, members):
    open_campaign(war, members)
    reopened = GalacticWarStore(war.path, war.geography, war.catalogue)
    assert reopened.snapshot(NOW)["campaigns"]["alpha"]["balance"] == 25


def test_runtime_no_longer_starts_or_registers_legacy_directives():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "opscribe" / "bot.py").read_text()
    assert "bot.tree.remove_command(command_name)" in source
    assert '"request_strike_directives"' in source
    assert '"post_cycle_reports"' in source
    assert "target_packages_ops" not in source


def test_atlas_specialist_compatibility_matches_world_or_natural_biome_rule():
    objective = {"required_rank": "Watch Apothecary", "worlds": ["research"], "biomes": ["jungle"]}
    assert atlas_compatible("research", "desert", objective)
    assert atlas_compatible("death", "jungle", objective)
    assert not atlas_compatible("hive", "jungle", objective)
    assert not atlas_compatible("research", "ice", {**objective, "worlds": ["feral"], "biomes": ["jungle"]})
    assert not atlas_compatible("forge", "jungle", {**objective, "worlds": ["forge"]})
    assert atlas_compatible("forge", "jungle", {})
    assert not atlas_compatible("unknown", "jungle", {})


def test_notification_acknowledgement_is_durable(war, members):
    open_campaign(war, members)
    notice_id = next(iter(war.snapshot(NOW)["outbox"]))
    war.acknowledge_notice(notice_id, "discord-message")
    notice = GalacticWarStore(war.path, war.geography, war.catalogue).snapshot(NOW)["outbox"][notice_id]
    assert notice["delivered"] is True
    assert notice["message_id"] == "discord-message"


def test_invasion_notice_has_orders_leader_mentions_and_retry_deduplication(war, members, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from opscribe import galactic_war_runtime as runtime
    from opscribe.constants import WATCH_SERGEANT_ROLE_ID, VETERAN_SERGEANT_ROLE_ID

    class Channel:
        def __init__(self):
            self.messages = []

        async def history(self, **kwargs):
            for message in self.messages:
                yield message

        async def send(self, **kwargs):
            message = SimpleNamespace(id=len(self.messages) + 1, embeds=[kwargs["embed"]], request=kwargs)
            self.messages.append(message)
            return message

    channel = Channel()
    guild = SimpleNamespace(get_channel=lambda identity: channel if identity == 1511147649003425973 else None)
    config = {"notification_channel_id": 1511147649003425973, "strategium_url": "https://jericho-strategium.duckdns.org/reach"}
    open_campaign(war, members)
    asyncio.run(runtime.deliver_notices(guild, war, {}))
    assert not channel.messages
    acknowledge = war.acknowledge_notice
    monkeypatch.setattr(war, "acknowledge_notice", lambda *args: (_ for _ in ()).throw(OSError("Interrupted after send")))
    with pytest.raises(OSError):
        asyncio.run(runtime.deliver_notices(guild, war, config))
    assert len(channel.messages) == 1
    message = channel.messages[0]
    assert config["strategium_url"] in message.embeds[0].description
    assert message.embeds[0].title == "Tyranid Invasion Declared"
    assert {field.name for field in message.embeds[0].fields} >= {"Company orders", "Kill-team orders", "Complete strikes", "Campaign"}
    assert f"<@&{WATCH_SERGEANT_ROLE_ID}>" in message.request["content"]
    assert f"<@&{VETERAN_SERGEANT_ROLE_ID}>" in message.request["content"]
    assert message.request["allowed_mentions"].everyone is False
    monkeypatch.setattr(war, "acknowledge_notice", acknowledge)
    asyncio.run(runtime.deliver_notices(guild, war, config))
    asyncio.run(runtime.deliver_notices(guild, war, config))
    assert len(channel.messages) == 1


def test_sandbox_notices_are_opt_in_unpinged_and_reverted_without_live_outbox(war, members):
    import asyncio
    from types import SimpleNamespace
    from opscribe import galactic_war_runtime as runtime
    from opscribe.galactic_war_sandbox import InvasionSandbox

    messages = []
    class Channel:
        async def history(self, **kwargs):
            for message in messages:
                yield message

        async def send(self, **kwargs):
            assert kwargs["allowed_mentions"].to_dict()["parse"] == []
            message = SimpleNamespace(id=len(messages) + 1, embeds=[kwargs["embed"]])
            messages.append(message)
            return message

    guild = SimpleNamespace(get_channel=lambda identity: Channel())
    config = {"notification_channel_id": 1511147649003425973, "strategium_url": "https://jericho-strategium.duckdns.org/reach"}
    sandbox = InvasionSandbox(war, members["wm"])
    session = sandbox.start("alpha", NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert not messages
    sandbox.execute(session, "send_test_notice", {"version": 0}, members, NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert len(messages) == 1
    assert "TEST INVASION" in messages[0].embeds[0].title
    sandbox.revert(session, NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert len(messages) == 2
    assert "REVERTED" in messages[1].embeds[0].title
    assert not war.snapshot(NOW)["outbox"]
    session = sandbox.start("alpha", NOW)
    sandbox.execute(session, "send_test_notice", {"version": 0}, members, NOW)
    sandbox.revert(session, NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert len(messages) == 2


def test_recruitment_expiry_reopens_the_same_slot_without_progress(war, members):
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    act(war, members, "brother", "join", strike_id=identity)
    state = war.tick(NOW + timedelta(hours=1))
    assert state["boards"]["alpha"][0]["id"] == identity
    assert state["boards"]["alpha"][0]["status"] == "available"
    assert state["boards"]["alpha"][0]["participants"] == []
    assert not state["contributions"]


def test_vanilla_briefings_do_not_require_custom_build_certification(war):
    planet = war.geography["planets"][0]
    offer = war._offer("campaign", planet, 0, 0)
    assert offer["content_kind"] == "vanilla"
    assert offer["aar_mission"] == "Inferno"
    assert "build_id" not in offer


def test_generation_uses_independent_rolls_and_stable_slot_seeds(war):
    planet = war.geography["planets"][0]
    offers = [war._offer("campaign", planet, 0, generation) for generation in range(1000)]
    gated = sum(bool(offer["required_rank"]) for offer in offers)
    assert 200 < gated < 300
    assert {offer["required_rank"] for offer in offers} == set(REQUIREMENT_WEIGHTS)
    repeated = war._offer("campaign", planet, 0, 0)
    assert repeated["objective_id"] == offers[0]["objective_id"]
    assert repeated["requirement_roll"] == offers[0]["requirement_roll"]


def test_missing_rare_briefings_fall_back_without_inflating_high_command(war):
    war.catalogue = [entry for entry in war.catalogue if not entry.get("required_rank") or entry["required_rank"] in {"Watch Apothecary", "Chief Apothecary"}]
    planet = war.geography["planets"][0]
    for generation in range(200):
        offer = war._offer("campaign", planet, 0, generation)
        assert bool(offer["required_rank"]) == (offer["requirement_roll"] >= 150)
        if offer["required_rank"] == "Chief Apothecary":
            assert offer["rolled_required_rank"] == "Chief Apothecary"


def test_reference_catalogue_covers_exactly_the_approved_specialist_ranks():
    import json
    from pathlib import Path
    definition = json.loads((Path(__file__).resolve().parents[1] / "reference" / "galactic_war.json").read_text())
    assert definition["content_kind"] == "vanilla_briefings"
    assert len(definition["objectives"]) == 29
    assert {entry.get("required_rank", "") for entry in definition["objectives"]} == set(REQUIREMENT_WEIGHTS)
    assert all(entry.get("theme") and "certified" not in entry for entry in definition["objectives"])


def test_world_without_entry_level_missions_preserves_specialist_rolls(war, monkeypatch):
    war.catalogue = [
        {"id": "general", "name": "General", "settings": ["hive|underhive"]},
        {"id": "wall", "name": "The Last Wall", "required_rank": "High Blade", "settings": ["hive|underhive"]},
        {"id": "iron", "name": "Iron Sleeper", "required_rank": "Forgemaster", "settings": ["hive|underhive"]},
    ]
    for roll in range(200):
        monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda _value, roll=roll: specialist_requirement(roll))
        offer = war._offer("campaign", war.geography["planets"][0], 0, roll)
        assert bool(offer["required_rank"]) == (roll >= 150)
        if roll >= 150:
            assert offer["required_rank"] in {"High Blade", "Forgemaster"}
            assert offer["objective_id"] in {"wall", "iron"}
        else:
            assert offer["objective_id"] == "general"


def test_shared_sector_29_worlds_use_exact_atlas_settings_and_imperial_classes(tmp_path):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    geography_path = root.parent.parent / "strategium" / "data" / "reach_geography.json"
    if not geography_path.exists():
        pytest.skip("Matching Strategium geography is required for cross-repository integration")
    raw = json.loads(geography_path.read_text())
    definition = json.loads((root / "reference" / "galactic_war.json").read_text())
    planets, objectives = shared_campaign_content(raw, definition)
    worlds = [planet for planet in planets if planet["subsector_id"] == "sector_29"]
    assert len(worlds) == 19
    source = {entry["id"]: entry for entry in raw["objectiveCatalog"]["objectives"]}
    operations = [{"mode": mode, "capacity": capacity, "mission": "Inferno", "mission_value": "pve_inferno", "difficulty": "@Absolute" if mode == "ops_strat" else "@Omega", "difficulty_class": "absolute_ops" if mode == "ops_strat" else "omega_ops"} for mode, capacity in (("ops_strat", 3), ("omega", 5))]
    store = GalacticWarStore(tmp_path / "shared.sqlite3", {"planets": planets, "sectors": raw["sectors"]}, objectives, operations)
    for planet in worlds:
        setting = f"{planet['world_type']}|{planet['biome']}"
        matches = [objective for objective in objectives if setting in source[objective["id"]]["settings"]]
        assert any(objective.get("required_rank") for objective in matches)
        assert any(not objective.get("required_rank") for objective in matches), f"Missing general objective for {planet['name']}"
        assert not planet["terrain_provisional"]
        assert planet["world_class"] == raw["worldClasses"][planet["world_type"]]
        for pool in ("vanilla", "omega"):
            for generation in range(20):
                offer = store._offer("campaign", planet, 0, generation, pool=pool)
                assert offer["objective_id"] in {objective["id"] for objective in matches}
                assert bool(offer["required_rank"]) == (offer["requirement_roll"] >= 150)
    state = {"campaigns": {}, "boards": {}, "outbox": {}}
    store._open_campaign(state, "sector_29", NOW)
    assert len(state["boards"]) == 19
    assert sum(len(board) for board in state["boards"].values()) == 152


def test_concurrent_duplicate_results_create_one_successor(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=identity)

    def settle(_index):
        return war.settle(result["attempt_id"], "one-aar", ["captain", "brother", "other"], NOW, success=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(settle, range(8)))
    state = war.snapshot(NOW)
    assert all(result == results[0] for result in results)
    assert len(state["settlements"]) == 1
    assert len(state["contributions"]) == 3
    assert len(state["boards"]["alpha"]) == 8
    assert state["boards"]["alpha"][0]["generation"] == 1


def test_full_roster_locks_without_a_manual_launch_and_can_cancel(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=identity)
    state = war.snapshot(NOW)
    offer = state["boards"]["alpha"][0]
    assert offer["status"] == "locked"
    assert "coordinator" not in offer
    assert state["attempts"][result["attempt_id"]]["status"] == "locked"
    with pytest.raises(ValueError, match="Roster is locked"):
        act(war, members, "brother", "leave", strike_id=identity)
    with pytest.raises(ValueError, match="Unknown Galactic War action"):
        act(war, members, "captain", "deploy", strike_id=identity)
    with pytest.raises(PermissionError):
        act(war, members, "lt", "cancel", strike_id=identity, reason="Not in this squad")
    act(war, members, "brother", "cancel", strike_id=identity, reason="Unable to play")
    state = war.snapshot(NOW)
    assert state["boards"]["alpha"][0]["status"] == "available"
    assert state["boards"]["alpha"][0]["id"] == identity
    assert state["attempts"][result["attempt_id"]]["status"] == "cancelled"
    assert not state["contributions"]


def test_omega_requires_platform_roles_and_caps_console_signups(war, members, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][4]["id"]
    with pytest.raises(ValueError, match="platform role"):
        act(war, members, "brother", "join", strike_id=identity)
    for member in ("captain", "brother", "other"):
        members[member] = replace(members[member], platform="console")
    act(war, members, "captain", "join", strike_id=identity)
    act(war, members, "brother", "join", strike_id=identity)
    with pytest.raises(ValueError, match="two console"):
        act(war, members, "other", "join", strike_id=identity)
    for member in ("other", "lt", "sergeant"):
        members[member] = replace(members[member], platform="pc")
        result = act(war, members, member, "join", strike_id=identity)
    assert war.snapshot(NOW)["boards"]["alpha"][4]["status"] == "locked"
    assert len(war.snapshot(NOW)["attempts"][result["attempt_id"]]["participants"]) == 5


def test_cancelled_roster_reopens_same_offer_with_new_locked_report_id(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=identity)
    cancelled_attempt = result["attempt_id"]
    act(war, members, "captain", "cancel", strike_id=identity, reason="Uncompleted roster released")
    state = war.snapshot(NOW)
    assert state["boards"]["alpha"][0]["id"] == identity
    assert state["boards"]["alpha"][0]["status"] == "available"
    assert not state["contributions"]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=identity)
    assert result["attempt_id"] != cancelled_attempt
    with pytest.raises(ValueError, match="Unknown locked strike"):
        war.settle(cancelled_attempt, "new-aar", ["captain", "brother", "other"], NOW, success=True)


def test_specialist_slot_uses_real_equerry_combination_on_automatic_lock(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "Forge Adept")
    members["tech"] = WarMember("tech", frozenset({"Watch Techmarine", "High Command Equerry"}), cadre_id="armory", watch_brother=True)
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    act(war, members, "captain", "join", strike_id=identity)
    act(war, members, "brother", "join", strike_id=identity)
    with pytest.raises(ValueError, match="remaining slot"):
        act(war, members, "wm", "join", strike_id=identity)
    result = act(war, members, "tech", "join", strike_id=identity)
    state = war.snapshot(NOW)
    assert state["boards"]["alpha"][0]["specialist_id"] == "tech"
    participant = next(item for item in state["attempts"][result["attempt_id"]]["participants"] if item["member_id"] == "tech")
    assert participant["full_credit"] is True


def test_each_planet_has_four_vanilla_and_four_omega_strikes(war, members):
    open_campaign(war, members)
    board = war.snapshot(NOW)["boards"]["alpha"]
    assert Counter(offer["pool"] for offer in board) == {"vanilla": 4, "omega": 4}
    assert all(offer["capacity"] == (5 if offer["pool"] == "omega" else 3) for offer in board)
    assert all(offer["aar_mode"] == ("omega" if offer["pool"] == "omega" else "ops_strat") for offer in board)
    assert all(offer["aar_mission"] == "Inferno" for offer in board)


def test_omega_completion_replenishes_only_its_own_pool(war, members, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    original = war.snapshot(NOW)["boards"]["alpha"]
    offer_id = original[4]["id"]
    participants = ["captain", "brother", "other", "lt", "sergeant"]
    for identity in participants:
        members[identity] = replace(members[identity], platform="pc")
        result = act(war, members, identity, "join", strike_id=offer_id)
    war.settle(result["attempt_id"], "omega-aar", participants, NOW, success=True)
    board = war.snapshot(NOW)["boards"]["alpha"]
    assert board[:4] == original[:4]
    assert board[5:] == original[5:]
    assert board[4]["id"] != offer_id
    assert board[4]["pool"] == "omega"
    assert board[4]["generation"] == 1
    assert Counter(offer["pool"] for offer in board) == {"vanilla": 4, "omega": 4}


def test_predeadline_receipt_delays_resolution_and_uses_receipt_time(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer_id = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for identity in ("captain", "brother", "other"):
        result = act(war, members, identity, "join", strike_id=offer_id)
    attempt_id = result["attempt_id"]
    deadline = NOW + timedelta(days=14)
    received = deadline - timedelta(minutes=1)
    war.reserve_report(attempt_id, "report", received)
    war.reserve_report(attempt_id, "report", deadline + timedelta(hours=1))
    state = war.tick(deadline)
    assert state["campaigns"]["alpha"]["status"] == "contested"
    assert state["campaigns"]["alpha"]["resolution_pending"] is True
    with pytest.raises(ValueError, match="no open campaign"):
        war.execute("join", members["lt"], {"version": state["version"], "strike_id": state["boards"]["alpha"][1]["id"]}, members, deadline)
    settled = war.settle(attempt_id, "aar", ["captain", "brother", "other"], deadline + timedelta(hours=2), success=True)
    assert settled["campaign_credit"] is True
    assert settled["strategic_at"] == received.isoformat()
    assert war.snapshot(deadline)["boards"]["alpha"][0]["id"] == offer_id
    assert war.snapshot(deadline)["boards"]["alpha"][0]["generation"] == 0
    assert war.tick(deadline + timedelta(hours=2))["campaigns"]["alpha"]["status"] == "lost"


def test_processing_grace_expires_without_reopening_finalized_campaign(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer_id = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for identity in ("captain", "brother", "other"):
        result = act(war, members, identity, "join", strike_id=offer_id)
    deadline = NOW + timedelta(days=14)
    war.reserve_report(result["attempt_id"], "report", deadline - timedelta(seconds=1))
    assert war.tick(deadline + timedelta(hours=24))["campaigns"]["alpha"]["status"] == "lost"
    settled = war.settle(result["attempt_id"], "aar", ["captain", "brother", "other"], deadline + timedelta(hours=25), success=True)
    assert settled["campaign_credit"] is False
    assert len(war.snapshot(deadline)["contributions"]) == 3


def test_reused_evidence_waits_for_independent_review_and_can_be_rejected(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    def report(identity, aar_id):
        for member in ("captain", "brother", "other"):
            result = act(war, members, member, "join", strike_id=identity)
        return {"aar_id": aar_id, "strike_id": result["attempt_id"], "brother_ids": ["captain", "brother", "other"], "mission": "Inferno", "difficulty_class": "absolute_ops", "strike_success": True, "strike_evidence_hashes": ["shared-image"]}
    first = report(war.snapshot(NOW)["boards"]["alpha"][0]["id"], "aar-first")
    war.submit_report(first, NOW)
    second = report(war.snapshot(NOW)["boards"]["alpha"][0]["id"], "aar-second")
    with pytest.raises(WarReportPending):
        war.submit_report(second, NOW)
    assert war.submit_report(first, NOW)["aar_id"] == "aar-first"
    state = war.snapshot(NOW)
    assert len(state["contributions"]) == 3
    assert state["attempts"][second["strike_id"]]["status"] == "review_pending"
    with pytest.raises(PermissionError):
        act(war, members, "captain", "review", strike_id=second["strike_id"], decision="approve", reason="Self approval")
    act(war, members, "wm", "review", strike_id=second["strike_id"], decision="reject", reason="Evidence belongs to another operation")
    assert war.snapshot(NOW)["boards"]["alpha"][0]["status"] == "available"
    with pytest.raises(ValueError, match="rejected"):
        war.submit_report(second, NOW)


def test_approved_review_settles_once_and_keeps_archive_recovery_pending(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    for index in range(2):
        identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
        for member in ("captain", "brother", "other"):
            result = act(war, members, member, "join", strike_id=identity)
        record = {"aar_id": f"aar-{index}", "strike_id": result["attempt_id"], "brother_ids": ["captain", "brother", "other"], "mission": "Inferno", "difficulty_class": "absolute_ops", "strike_success": True, "strike_evidence_hashes": ["image"]}
        if index == 0:
            war.submit_report(record, NOW)
        else:
            with pytest.raises(WarReportPending):
                war.submit_report(record, NOW)
    act(war, members, "wm", "review", strike_id=record["strike_id"], decision="approve", reason="Independent evidence confirms distinct operations")
    war.submit_report(record, NOW)
    war.submit_report(record, NOW)
    state = war.snapshot(NOW)
    assert len(state["contributions"]) == 6
    assert state["reviews"][record["strike_id"]]["archive_pending"] is True
    war.acknowledge_review_archive(record["strike_id"])
    assert war.snapshot(NOW)["reviews"][record["strike_id"]]["archive_pending"] is False


def test_out_of_order_processing_replays_successes_in_receipt_order(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    attempts = []
    for slot in range(2):
        participants = [f"squad-{slot}-{index}" for index in range(3)]
        identity = war.snapshot(NOW)["boards"]["alpha"][slot]["id"]
        for participant in participants:
            members[participant] = WarMember(participant, frozenset({"Watch Brother"}), watch_brother=True)
            result = act(war, members, participant, "join", strike_id=identity)
        attempts.append((result["attempt_id"], participants))
        war.reserve_report(result["attempt_id"], f"report-{slot}", NOW + timedelta(hours=slot + 1))
    for index in (1, 0):
        identity, participants = attempts[index]
        war.settle(identity, f"aar-{index}", participants, NOW + timedelta(hours=3), success=True)
    campaign = war.snapshot(NOW + timedelta(hours=3))["campaigns"]["alpha"]
    assert campaign["balance"] == pytest.approx(26.75)
    assert campaign["last_success_at"] == (NOW + timedelta(hours=2)).isoformat()


def test_reversing_credit_after_resolution_does_not_reopen_the_campaign(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=identity)
    war.settle(result["attempt_id"], "aar", ["captain", "brother", "other"], NOW, success=True)
    after = NOW + timedelta(days=14)
    before = war.tick(after)["campaigns"]["alpha"]
    war.execute("reverse", members["wm"], {"version": war.snapshot(after)["version"], "strike_id": result["attempt_id"], "reason": "Independent retrospective review"}, members, after)
    state = war.snapshot(after)
    assert state["campaigns"]["alpha"] == before
    assert not state["contributions"]


@pytest.mark.parametrize("roles", [frozenset({"Watch Brother"}), frozenset({"Watch Captain"}), frozenset({"Watch Techmarine"}), frozenset({"Watch Master"})])
def test_strike_signup_requires_actual_base_role_without_rank_bypass(war, members, roles):
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    outsider = WarMember("outsider", roles, watch_brother=False)
    with pytest.raises(PermissionError, match="Watch Brother role is required"):
        war.execute("join", outsider, {"version": war.snapshot(NOW)["version"], "strike_id": identity}, {**members, "outsider": outsider}, NOW)
    assert not war.snapshot(NOW)["boards"]["alpha"][0]["participants"]


def test_watch_brother_role_is_rechecked_for_existing_signups_before_lock(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    act(war, members, "captain", "join", strike_id=identity)
    members["captain"] = replace(members["captain"], watch_brother=False)
    with pytest.raises(PermissionError, match="retain the Watch Brother role"):
        act(war, members, "brother", "join", strike_id=identity)


def test_strike_credit_reversal_is_audited_and_does_not_reroll_successor(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer_id = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=offer_id)
    identity = result["attempt_id"]
    war.settle(identity, "aar", ["captain", "brother", "other"], NOW, success=True)
    successor = war.snapshot(NOW)["boards"]["alpha"][0]
    act(war, members, "wm", "reverse", strike_id=identity, reason="Result invalidated after evidence review")
    state = war.snapshot(NOW)
    assert not state["contributions"]
    assert state["campaigns"]["alpha"]["balance"] == 25
    assert state["boards"]["alpha"][0] == successor
    assert state["settlements"][identity]["reversal_reason"]
    with pytest.raises(ValueError, match="unreversed"):
        act(war, members, "wm", "reverse", strike_id=identity, reason="Retry")