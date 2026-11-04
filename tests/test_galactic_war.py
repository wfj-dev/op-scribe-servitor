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
    shared_campaign_content, activate_pressure_policy, active_offensive,
)
from opscribe.galactic_war_store import GalacticWarStore

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def test_authored_objectives_require_exact_world_and_biome_settings():
    general = {"settings": ["hive|industrial", "feudal|industrial"]}
    assert atlas_compatible("hive", "industrial", general)
    assert atlas_compatible("feudal", "industrial", general)
    assert not atlas_compatible("hive", "jungle", general)
    assert not atlas_compatible("hive", "underhive", {"settings": ["hive|underhive"]})
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
    assert "underhive" not in recipes.definition["biomes"]
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
            if biome in {"ice", "asteroid", "ocean", "gasgiant"}:
                assert recipe["adaptation"] == "approximate_environment"
    with pytest.raises(ValueError, match="Unsupported Atlas"):
        recipes.resolve("hive", "unknown", recipe_content[3][0])


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
def test_recipe_strikes_settle_without_review_and_freeze_assignments_across_content_changes(recipe_war, members, monkeypatch, success):
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
    record = {
        "aar_id": "recipe-aar", "strike_id": attempt_id, "brother_ids": participants,
        "mission": offer["aar_mission"], "difficulty_class": "hard_stratagem",
        "strike_success": success, "strike_evidence_hashes": ["selection", "result"],
    }
    with pytest.raises(ValueError, match="mission or difficulty"):
        war.submit_report({**record, "difficulty_class": "absolute_ops"}, NOW)
    with pytest.raises(ValueError, match="Only completed"):
        war.submit_report({**record, "strike_success": False}, NOW)
    settled = war.submit_report(record, NOW)
    assert settled["aar_id"] == "recipe-aar"
    with pytest.raises(ValueError, match="already been settled"):
        war.settle(attempt_id, "different-aar", participants, NOW, success=success)
    assert war.submit_report(record, NOW) == settled
    state = war.snapshot(NOW)
    assert not state["reviews"]
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
        assert offer["recipe"]["id"] == f"hive|industrial|{offer['objective_id']}"
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
        catalog["objectives"][0]["settings"] = ["research|unknown"]
    else:
        geography["bodies"][0]["biome"] = "unknown"
    with pytest.raises(ValueError):
        shared_campaign_content(geography, definition)


@pytest.mark.parametrize("biome", ["ocean", "gasgiant"])
def test_shared_content_accepts_updated_atlas_biomes(biome):
    geography, definition = shared_content_fixture()
    geography["bodies"][0]["biome"] = biome
    geography["objectiveCatalog"]["objectives"][0]["settings"] = [f"research|{biome}"]
    planets, objectives = shared_campaign_content(geography, definition)
    assert planets[0]["biome"] == biome
    assert atlas_compatible("research", biome, objectives[0])
    assert not atlas_compatible("research", biome, {"required_rank": "Watch Librarian"})


@pytest.mark.parametrize("score,tier", [(0, 0), (3.99, 0), (4, 1), (8, 2), (16, 3), (31.99, 3), (32, 4)])
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
        "planets": [{"id": name, "subsector_id": name, "system_id": f"{name}-0", "world_type": "hive", "biome": "industrial"} for name in ("alpha", "beta", "gamma")],
    }
    catalogue = [{"id": str(index), "name": f"Objective {index}"} for index in range(5)]
    catalogue += [{"id": rank, "name": rank, "required_rank": rank, "worlds": ["hive"], "biomes": ["industrial"]} for rank in REQUIREMENT_WEIGHTS if rank]
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


def test_queue_muster_cooldown_is_channel_wide_and_survives_restart(war, members):
    channel = "guild:lfg"
    act(war, members, "brother", "queue_join")
    batch = war.claim_queue_muster(channel, members, NOW)
    assert [entry["member_id"] for entry in batch["entries"]] == ["brother"]
    assert war.claim_queue_muster(channel, members, NOW) is None
    sending = war.begin_queue_muster_send(channel, batch["id"], members, NOW)
    assert sending["status"] == "sending"
    war.finish_queue_muster(channel, batch["id"], "message-one", NOW)
    act(war, members, "other", "queue_join")
    act(war, members, "captain", "queue_join")
    reopened = GalacticWarStore(war.path, war.geography, war.catalogue, war.operations)
    assert reopened.claim_queue_muster(channel, members, NOW + timedelta(minutes=29)) is None
    combined = reopened.claim_queue_muster(channel, members, NOW + timedelta(minutes=30))
    assert {entry["member_id"] for entry in combined["entries"]} == {"other", "captain"}
    reopened.begin_queue_muster_send(channel, combined["id"], members, NOW + timedelta(minutes=30))
    reopened.finish_queue_muster(channel, combined["id"], "message-two", NOW + timedelta(minutes=30))
    assert reopened.claim_queue_muster(channel, members, NOW + timedelta(hours=1)) is None


def test_queue_muster_updates_do_not_repeat_and_empty_queue_does_not_send(war, members):
    channel = "guild:lfg"
    act(war, members, "brother", "queue_join")
    identity = war.snapshot(NOW)["matchmaking_queue"]["brother"]["entry_id"]
    act(war, members, "brother", "queue_join", preferences={"pool": "omega"})
    assert war.snapshot(NOW)["matchmaking_queue"]["brother"]["entry_id"] == identity
    batch = war.claim_queue_muster(channel, members, NOW)
    sending = war.begin_queue_muster_send(channel, batch["id"], members, NOW)
    assert sending["entries"][0]["preferences"]["pool"] == "omega"
    war.finish_queue_muster(channel, batch["id"], "sent", NOW)
    act(war, members, "brother", "queue_join", preferences={"pool": "vanilla"})
    assert war.claim_queue_muster(channel, members, NOW + timedelta(minutes=30)) is None
    act(war, members, "other", "queue_join")
    act(war, members, "other", "queue_leave")
    assert war.claim_queue_muster(channel, members, NOW + timedelta(minutes=30)) is None
    act(war, members, "brother", "queue_leave")
    assert war.claim_queue_muster(channel, members, NOW + timedelta(hours=1)) is None


@pytest.fixture
def muster_delivery(war, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from opscribe import galactic_war_runtime as runtime
    import discord

    clock = [NOW]
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    members = {str(identity): WarMember(str(identity), frozenset({"Watch Brother"}), "1", "1-team", watch_brother=True)
               for identity in range(42, 50)}
    guild = SimpleNamespace(id=1429264578440597517, me=SimpleNamespace(id=999))
    role = SimpleNamespace(id=1429678423290281984, guild=guild)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1429942816741523570
    channel.guild = guild
    sent = []
    async def send(**kwargs):
        message = SimpleNamespace(id=100 + len(sent), author=guild.me,
                                  embeds=[kwargs["embed"]], created_at=clock[0])
        sent.append(message)
        return message
    async def history(**kwargs):
        for message in reversed(sent):
            yield message
    channel.send = AsyncMock(side_effect=send)
    channel.history = history
    guild.get_channel = MagicMock(return_value=channel)
    guild.get_role = lambda identity: role if identity == role.id else None
    guild.get_member = lambda identity: SimpleNamespace(display_name=f"Brother {identity}") if str(identity) in members else None
    teams = [{"id": "1-team", "company_id": "1", "name": "Kill-Team Timotheos"}]
    monkeypatch.setattr(runtime, "directory", lambda guild: (members, {}, {}, teams))
    monkeypatch.setattr(runtime, "datetime", FixedDateTime)
    monkeypatch.setattr(runtime._g, "logger", MagicMock())
    config = {"queue_notification_guild_id": guild.id, "queue_notification_channel_id": channel.id,
              "queue_notification_role_id": role.id, "strategium_url": "https://example.test/reach"}
    war.geography["planets"][0]["name"] = "Calyx"
    war.geography["systems"][0]["name"] = "Penumbral Choir"
    war.geography["sectors"][0]["name"] = "The Canticle Reach"
    return SimpleNamespace(runtime=runtime, clock=clock, guild=guild, channel=channel,
                           role=role, members=members, sent=sent, config=config, teams=teams)


def test_queue_muster_delivery_batches_waiting_brothers_and_pings_only_lfg(war, muster_delivery):
    import asyncio
    fixture = muster_delivery
    act(war, fixture.members, "42", "queue_join", preferences={"planet_id": "alpha", "pool": "vanilla", "specialist": "Watch Apothecary"})
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    request = fixture.channel.send.await_args.kwargs
    assert request["content"] == "<@&1429678423290281984>"
    mentions = request["allowed_mentions"]
    assert mentions.everyone is mentions.users is mentions.replied_user is False
    assert [role.id for role in mentions.roles] == [fixture.role.id]
    embed = request["embed"]
    assert embed.title == "❱❱❱ 𝐌𝐔𝐒𝐓𝐄𝐑 𝐂𝐀𝐋𝐋:"
    assert embed.colour.value == 0xE34242
    assert "<:DeathSentence:1528598025604300911> **`ʀᴇɪɴғᴏʀᴄᴇᴍᴇɴᴛs ʀᴇǫᴜᴇsᴛᴇᴅ`**" in embed.description
    assert "**`ʙʀᴏᴛʜᴇʀʜᴏᴏᴅ:`** Kill-Team Timotheos\n" in embed.fields[0].value
    assert "**`ᴀʟʟᴇɢɪᴀɴᴄᴇ:`** Watch Company Primus\n\n" in embed.fields[0].value
    assert "**`ᴅᴇᴘʟᴏʏᴍᴇɴᴛ:`**\nSeeks brothers" in embed.fields[0].value
    assert "\n\n**`sᴜᴘᴘᴏʀᴛ:`** Watch Apothecary" in embed.fields[0].value
    assert "three-brother strike force on Calyx, in the Penumbral Choir system" in embed.fields[0].value
    assert "Watch Apothecary support is sought" in embed.fields[0].value
    assert "Kill-Team Timotheos" in embed.fields[0].value
    assert "Watch Company Primus" in embed.fields[0].value
    assert "Answer the Muster" in embed.description
    assert "vanilla" not in str(embed.to_dict()).lower()
    act(war, fixture.members, "43", "queue_join", preferences={"pool": "omega"})
    act(war, fixture.members, "44", "queue_join")
    act(war, fixture.members, "45", "queue_join")
    act(war, fixture.members, "45", "queue_leave")
    fixture.clock[0] += timedelta(minutes=29)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert fixture.channel.send.await_count == 1
    fixture.clock[0] += timedelta(minutes=1)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert fixture.channel.send.await_count == 2
    combined = fixture.channel.send.await_args.kwargs["embed"]
    assert combined.title == embed.title and combined.colour == embed.colour
    assert all(not field.inline for field in combined.fields)
    assert {field.name for field in combined.fields} == {"Brother 43", "Brother 44"}
    assert "five-brother strike force" in combined.fields[0].value
    fixture.clock[0] += timedelta(minutes=30)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert fixture.channel.send.await_count == 2


def test_queue_muster_immediate_matches_and_stale_claims_never_announce(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    for who in ("brother", "captain", "other"):
        act(war, members, who, "queue_join", preferences={"pool": "vanilla"})
    assert war.claim_queue_muster("guild:lfg", members, NOW) is None
    act(war, members, "sergeant", "queue_join")
    batch = war.claim_queue_muster("guild:lfg", members, NOW)
    act(war, members, "sergeant", "queue_leave")
    assert war.begin_queue_muster_send("guild:lfg", batch["id"], members, NOW) is None
    assert war.claim_queue_muster("guild:lfg", members, NOW + timedelta(hours=1)) is None


def test_queue_muster_leaving_and_rejoining_cannot_reuse_old_batch(war, members):
    channel = "guild:lfg"
    act(war, members, "brother", "queue_join")
    batch = war.claim_queue_muster(channel, members, NOW)
    act(war, members, "brother", "queue_leave")
    act(war, members, "brother", "queue_join")
    assert war.begin_queue_muster_send(channel, batch["id"], members, NOW) is None
    fresh = war.claim_queue_muster(channel, members, NOW)
    assert fresh["entries"][0]["entry_id"] != batch["entries"][0]["entry_id"]


def test_queue_muster_concurrent_claims_and_abandoned_claim_recovery(war, members):
    act(war, members, "brother", "queue_join")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: war.claim_queue_muster("guild:lfg", members, NOW), range(2)))
    assert sum(result is not None for result in results) == 1
    original = next(result for result in results if result)
    recovered = war.claim_queue_muster("guild:lfg", members, NOW + timedelta(minutes=3))
    assert recovered["id"] != original["id"]
    assert war.begin_queue_muster_send("guild:lfg", original["id"], members, NOW) is None


def test_queue_muster_overflow_is_bounded_and_newcomers_survive_acknowledgement(war, muster_delivery):
    import asyncio
    fixture = muster_delivery
    for identity in fixture.members:
        act(war, fixture.members, identity, "queue_join")
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert len(fixture.sent[0].embeds[0].fields) == 5
    fixture.clock[0] += timedelta(minutes=30)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert len(fixture.sent[1].embeds[0].fields) == 3
    fixture.clock[0] += timedelta(hours=1)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert len(fixture.sent) == 2


def test_queue_muster_timeout_reconciles_bot_message_without_reping(war, muster_delivery):
    import asyncio
    fixture = muster_delivery
    act(war, fixture.members, "42", "queue_join")
    original_send = fixture.channel.send.side_effect
    async def timeout_after_delivery(**kwargs):
        await original_send(**kwargs)
        raise TimeoutError("Delivery outcome unknown")
    fixture.channel.send.side_effect = timeout_after_delivery
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    channel_key = f"{fixture.guild.id}:{fixture.channel.id}"
    assert war.snapshot(NOW)["queue_musters"][channel_key]["batch"]["status"] == "uncertain"
    reopened = GalacticWarStore(war.path, war.geography, war.catalogue, war.operations)
    fixture.clock[0] += timedelta(minutes=10)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, reopened, fixture.config))
    assert fixture.channel.send.await_count == 1
    delivery = reopened.snapshot(NOW)["queue_musters"][channel_key]
    assert "batch" not in delivery
    assert delivery["next_allowed_at"] == (NOW + timedelta(minutes=30)).isoformat()


def test_queue_muster_uncertain_send_ignores_forged_marker_and_never_blindly_retries(war, muster_delivery):
    import asyncio
    from types import SimpleNamespace
    fixture = muster_delivery
    act(war, fixture.members, "42", "queue_join")
    fixture.channel.send.side_effect = TimeoutError("Unknown outcome")
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    channel_key = f"{fixture.guild.id}:{fixture.channel.id}"
    batch = war.snapshot(NOW)["queue_musters"][channel_key]["batch"]
    embed = fixture.runtime.discord.Embed()
    embed.set_footer(text=f"galactic-war:muster:{batch['id']}")
    fixture.sent.append(SimpleNamespace(id=888, author=SimpleNamespace(id=42), embeds=[embed], created_at=NOW))
    fixture.clock[0] += timedelta(hours=1)
    act(war, fixture.members, "43", "queue_join")
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert fixture.channel.send.await_count == 1
    assert war.snapshot(NOW)["queue_musters"][channel_key]["batch"]["status"] == "uncertain"


def test_queue_muster_definitive_failure_preserves_membership_and_allows_later_delivery(war, muster_delivery):
    import asyncio
    from types import SimpleNamespace
    fixture = muster_delivery
    act(war, fixture.members, "42", "queue_join")
    original_send = fixture.channel.send.side_effect
    fixture.channel.send.side_effect = fixture.runtime.discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No access")
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    entry = war.snapshot(NOW)["matchmaking_queue"]["42"]
    assert entry["status"] == "waiting" and entry["muster_pending"]
    fixture.channel.send.side_effect = original_send
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    assert len(fixture.sent) == 1


@pytest.mark.parametrize("problem", ["channel", "role", "guild", "sandbox", "eligibility"])
def test_queue_muster_fails_closed_for_invalid_destination_or_members(war, muster_delivery, problem):
    import asyncio
    fixture = muster_delivery
    act(war, fixture.members, "42", "queue_join")
    if problem == "channel":
        fixture.channel.guild = type("OtherGuild", (), {"id": 123})()
    elif problem == "role":
        fixture.guild.get_role = lambda identity: None
    elif problem == "guild":
        fixture.config["queue_notification_guild_id"] = 123
    elif problem == "sandbox":
        with war._connection() as connection:
            state = war._load(connection)
            state["sandbox"] = {"active": True}
            war._save(connection, state)
    else:
        fixture.members["42"] = replace(fixture.members["42"], active=False)
    asyncio.run(fixture.runtime.deliver_notices(fixture.guild, war, fixture.config))
    fixture.channel.send.assert_not_awaited()


@pytest.mark.parametrize("specialist,expected", [
    ("any", "Seeks brothers"), ("none", "without a specialist mandate"),
    ("required", "calling for specialist support"), ("Watch Apothecary", "Watch Apothecary support"),
])
def test_queue_muster_narrative_is_safe_and_does_not_invent_specialist_membership(war, muster_delivery, specialist, expected):
    fixture = muster_delivery
    fixture.members["42"] = replace(fixture.members["42"], cadre_id="librarius")
    fixture.guild.get_member = lambda identity: type("Member", (), {"display_name": "Brother @everyone **Varian**"})()
    act(war, fixture.members, "42", "queue_join", preferences={
        "pool": "any", "system_id": "alpha-0", "specialist": specialist,
        "query": "@everyone <@&1429678423290281984> **purge**",
    })
    batch = war.claim_queue_muster("guild:lfg", fixture.members, NOW)
    batch = war.begin_queue_muster_send("guild:lfg", batch["id"], fixture.members, NOW)
    embed = fixture.runtime._queue_muster_embed(fixture.guild, war, batch, fixture.members, fixture.teams, fixture.config)
    content = " ".join([embed.title, embed.description, *[field.name + field.value for field in embed.fields]])
    assert expected in content
    assert "Librarius" in content
    assert "in the Penumbral Choir system" in content
    assert "@everyone" not in content
    assert "<@&1429678423290281984>" not in content
    assert "**Varian**" not in content
    assert all(term not in content.lower() for term in ("queue", "vanilla", "matchmaking", "pool"))
    assert "42" not in embed.fields[0].name
    assert len(embed) < 6000


def test_commitment_notices_snapshot_destinations_and_roster_events_are_silent(war, members):
    open_campaign(war, members)
    act(war, members, "captain", "commit", company_id="1", subsector_id="alpha")
    act(war, members, "sergeant", "assign", kill_team_id="1-1", system_id="alpha-0")
    state = war.snapshot(NOW)
    notices = {notice["event"]: notice for notice in state["outbox"].values()}
    assert notices["company_committed"]["subsector_id"] == "alpha"
    assert notices["kill_team_committed"]["system_id"] == "alpha-0"
    assert notices["company_committed"]["campaign"]["id"] == state["campaigns"]["alpha"]["id"]
    before = dict(state["outbox"])
    war._notice(state, "roster_locked", "GW-test", NOW)
    war._notice(state, "roster_cancelled", "GW-test", NOW)
    assert state["outbox"] == before


def test_multiple_companies_and_killteams_can_share_locations(war, members):
    open_campaign(war, members)
    for company, actor in (("1", "captain"), ("2", "other")):
        act(war, members, actor, "commit", company_id=company, subsector_id="alpha")
        act(war, members, actor, "assign", kill_team_id=f"{company}-1", system_id="alpha-0", reason="Sergeant unavailable")
    state = war.snapshot(NOW)
    assert state["commitments"] == {"1": "alpha", "2": "alpha"}
    assert state["assignments"] == {"1-1": "alpha-0", "2-1": "alpha-0"}
    assert len(state["boards"]["alpha"]) == 8


def test_signup_availability_reuses_join_rules_and_tracks_specialist_coverage(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    state = war.snapshot(NOW)
    offer = state["boards"]["alpha"][0]
    assert war.signup_availability(state, members["brother"], offer, members, NOW)["can_join"]
    no_role = replace(members["brother"], watch_brother=False)
    assert "Watch Brother" in war.signup_availability(state, no_role, offer, members, NOW)["reason"]
    offer["required_rank"] = "Watch Apothecary"
    offer["participants"] = ["captain", "other"]
    availability = war.signup_availability(state, members["brother"], offer, members, NOW)
    assert not availability["can_join"]
    assert "required rank" in availability["reason"]
    assert not availability["specialist_fulfilled"]
    members["captain"] = replace(members["captain"], roles=frozenset({"Watch Apothecary"}))
    availability = war.signup_availability(state, members["brother"], offer, members, NOW)
    assert availability["can_join"] and availability["specialist_fulfilled"]
    offer["participants"].append("brother")
    members["brother"] = replace(members["brother"], watch_brother=False)
    assert "signed up" in war.signup_availability(state, members["brother"], offer, members, NOW)["reason"]
    assert not state["settlements"]


def test_matchmaking_waits_for_full_vanilla_party_then_locks_roster(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    vanilla_offer_ids = {item["id"] for item in war.snapshot(NOW)["boards"]["alpha"] if item.get("pool") == "vanilla"}
    preferences = {"subsector_id": "alpha", "pool": "vanilla"}

    act(war, members, "brother", "queue_join", preferences=preferences)
    act(war, members, "captain", "queue_join", preferences=preferences)
    state = war.snapshot(NOW)
    assert all(item["status"] == "available" for item in state["boards"]["alpha"] if item["id"] in vanilla_offer_ids)
    assert state["matchmaking_queue"]["brother"]["status"] == "waiting"
    assert state["matchmaking_queue"]["captain"]["status"] == "waiting"

    act(war, members, "other", "queue_join", preferences=preferences)
    state = war.snapshot(NOW)
    matched_offer = next(item for item in state["boards"]["alpha"] if item["id"] in vanilla_offer_ids and item["status"] == "locked")
    assert matched_offer["status"] == "locked"
    assert set(matched_offer["participants"]) == {"brother", "captain", "other"}
    attempt_id = matched_offer["attempt_id"]
    assert all(state["matchmaking_queue"][identity]["status"] == "matched" for identity in matched_offer["participants"])
    assert all(state["matchmaking_queue"][identity]["attempt_id"] == attempt_id for identity in matched_offer["participants"])
    assert not state["settlements"]
    assert not state["contributions"]


def test_direct_signup_removes_member_from_waiting_matchmaking_queue(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer = next(item for item in war.snapshot(NOW)["boards"]["alpha"] if item.get("pool") == "vanilla")
    act(war, members, "brother", "queue_join", preferences={"pool": "vanilla"})

    act(war, members, "brother", "join", strike_id=offer["id"])

    state = war.snapshot(NOW)
    assert "brother" not in state["matchmaking_queue"]
    signed_offer = next(item for item in state["boards"]["alpha"] if item["id"] == offer["id"])
    assert signed_offer["participants"] == ["brother"]


def test_matchmaking_never_mixes_incompatible_preferences_and_updates_waiting_member(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    vanilla = {"subsector_id": "alpha", "pool": "vanilla"}
    omega = {"subsector_id": "alpha", "pool": "omega"}
    act(war, members, "brother", "queue_join", preferences=vanilla)
    act(war, members, "captain", "queue_join", preferences=vanilla)
    act(war, members, "other", "queue_join", preferences=omega)
    state = war.snapshot(NOW)
    assert all(state["matchmaking_queue"][identity]["status"] == "waiting" for identity in ("brother", "captain", "other"))
    assert all(offer["status"] == "available" for offer in state["boards"]["alpha"])

    act(war, members, "other", "queue_join", preferences=vanilla)
    state = war.snapshot(NOW)
    assert sum(offer["status"] == "locked" and offer.get("pool") == "vanilla" for offer in state["boards"]["alpha"]) == 1
    assert all(state["matchmaking_queue"][identity]["status"] == "matched" for identity in ("brother", "captain", "other"))


def test_periodic_matcher_removes_members_who_lost_queue_eligibility(war, members):
    open_campaign(war, members)
    act(war, members, "brother", "queue_join", preferences={"pool": "vanilla"})
    members["brother"] = replace(members["brother"], watch_brother=False)

    assert war.match_queue(members, NOW) == 0
    assert "brother" not in war.snapshot(NOW)["matchmaking_queue"]


def test_matchmaking_waits_for_full_omega_party_and_obeys_console_limit(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    omega_members = {}
    for index in range(5):
        identity = f"omega-{index}"
        platform = "console" if index < 2 else "pc"
        omega_members[identity] = WarMember(
            identity, frozenset({"Watch Brother"}), platform=platform, watch_brother=True,
        )
    members.update(omega_members)
    offer = next(item for item in war.snapshot(NOW)["boards"]["alpha"] if item.get("pool") == "omega")
    preferences = {"subsector_id": "alpha", "pool": "omega"}

    for identity in list(omega_members)[:4]:
        act(war, members, identity, "queue_join", preferences=preferences)
    state = war.snapshot(NOW)
    assert next(item for item in state["boards"]["alpha"] if item["id"] == offer["id"])["status"] == "available"
    act(war, members, "omega-4", "queue_join", preferences=preferences)

    state = war.snapshot(NOW)
    matched_offer = next(item for item in state["boards"]["alpha"] if item.get("pool") == "omega" and item["status"] == "locked")
    attempt = state["attempts"][matched_offer["attempt_id"]]
    assert len(attempt["participants"]) == 5
    assert sum(members[item["member_id"]].platform == "console" for item in attempt["participants"]) == 2
    assert set(matched_offer["participants"]) == set(omega_members)


def test_signup_moves_between_open_strikes_and_advertises_replacement(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    first, second = war.snapshot(NOW)["boards"]["alpha"][:2]

    act(war, members, "brother", "join", strike_id=first["id"])
    state = war.snapshot(NOW)
    first = next(offer for offer in state["boards"]["alpha"] if offer["id"] == first["id"])
    second = next(offer for offer in state["boards"]["alpha"] if offer["id"] == second["id"])
    availability = war.signup_availability(state, members["brother"], second, members, NOW)
    assert availability["can_join"]
    assert availability["replaces_strike"] == {"id": first["id"], "name": first["name"]}

    act(war, members, "brother", "join", strike_id=second["id"])
    state = war.snapshot(NOW)
    offers = {offer["id"]: offer for offer in state["boards"]["alpha"]}
    assert offers[first["id"]]["participants"] == []
    assert offers[first["id"]]["status"] == "available"
    assert "recruitment_expires_at" not in offers[first["id"]]
    assert offers[second["id"]]["participants"] == ["brother"]
    assert offers[second["id"]]["status"] == "recruiting"


def test_signup_cannot_move_from_locked_strike_or_failed_target(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    first, second = war.snapshot(NOW)["boards"]["alpha"][:2]
    for identity in ("captain", "brother", "other"):
        act(war, members, identity, "join", strike_id=first["id"])
    before = war.snapshot(NOW)
    with pytest.raises(ValueError, match="roster is locked"):
        act(war, members, "brother", "join", strike_id=second["id"])
    assert war.snapshot(NOW) == before

    act(war, members, "captain", "cancel", strike_id=first["id"], reason="Test open roster")
    act(war, members, "brother", "join", strike_id=first["id"])
    state = war.snapshot(NOW)
    target = next(offer for offer in state["boards"]["alpha"] if offer["id"] == second["id"])
    target["status"] = "locked"
    state["version"] += 1
    with war._connection() as connection:
        war._save(connection, state)
    with pytest.raises(ValueError, match="Strike roster is locked"):
        act(war, members, "brother", "join", strike_id=second["id"])
    assert "brother" in next(offer for offer in war.snapshot(NOW)["boards"]["alpha"] if offer["id"] == first["id"])["participants"]


@pytest.mark.parametrize("rank,reason", [
    ("Watch Sergeant", "Assigned system"), ("Veteran Sergeant", "Assigned system"),
    ("Watch Brother", "Assigned system"), ("Watch Captain", "Company commitment"),
    ("Watch Lieutenant", "Company commitment"), ("Watch Master", "Campaign priority"),
])
def test_recommendations_prioritize_assignment_and_fill_with_eligible_support(war, members, rank, reason):
    import copy
    from opscribe.galactic_war_runtime import recommended_strikes

    open_campaign(war, members)
    open_campaign(war, members, "beta")
    state = war.snapshot(NOW)
    state["commitments"] = {"1": "alpha"}
    state["assignments"] = {"1-1": "alpha-0"}
    actor = replace(members["sergeant"], roles=frozenset({rank}))
    members[actor.member_id] = actor
    for planet, board in state["boards"].items():
        for index, offer in enumerate(board):
            offer.update(required_rank="", status="closed", participants=[], capacity=3)
        board[0].update(status="available")
    state["boards"]["beta"][0].update(status="recruiting", participants=["brother", "captain"])
    before = copy.deepcopy(state)
    result = recommended_strikes(war, state, actor, members, NOW)["strikes"]
    if rank != "Watch Master":
        assert result[0]["planet_id"] == "alpha" and result[0]["reason"] == reason
        assert result[1]["planet_id"] == "beta" and result[1]["reason"] == "Eligible support"
    else:
        assert result[0]["planet_id"] == "beta" and result[0]["reason"] == reason
    assert len(result) == 2
    assert state == before


def test_recommendations_specialist_current_first_and_locked_roster_blocks_new_strikes(war, members):
    from opscribe.galactic_war_runtime import recommended_strikes

    open_campaign(war, members)
    state = war.snapshot(NOW)
    actor = replace(members["brother"], roles=frozenset({"Void Warden"}), cadre_id="librarius", platform="pc")
    members[actor.member_id] = actor
    for offer in state["boards"]["alpha"]:
        offer.update(required_rank="", participants=[], status="available")
    current, preferred, covered = state["boards"]["alpha"][:3]
    current.update(status="recruiting", participants=[actor.member_id])
    preferred.update(required_rank="Watch Librarian")
    covered.update(required_rank="Watch Librarian", status="recruiting", participants=["captain"])
    members["captain"] = replace(members["captain"], roles=frozenset({"Watch Librarian"}))
    result = recommended_strikes(war, state, actor, members, NOW)["strikes"]
    assert result[0]["id"] == current["id"] and result[0]["current"]
    assert result[1]["id"] == preferred["id"] and result[1]["reason"] == "Specialist needed"
    assert result[2]["id"] == covered["id"] and result[2]["reason"] == "Eligible support"
    current["status"] = "locked"
    assert len(recommended_strikes(war, state, actor, members, NOW)["strikes"]) == 1
    assert not recommended_strikes(war, state, replace(actor, watch_brother=False), members, NOW)["strikes"]
    assert not recommended_strikes(war, state, replace(actor, active=False), members, NOW)["strikes"]


@pytest.mark.parametrize("rank", [rank for rank in REQUIREMENT_WEIGHTS if rank])
def test_recommendations_match_each_specialist_rank_and_ignore_expired_campaigns(war, members, rank):
    from opscribe.galactic_war_runtime import recommended_strikes

    open_campaign(war, members)
    state = war.snapshot(NOW)
    actor = replace(members["brother"], roles=frozenset({rank}), platform="pc")
    members[actor.member_id] = actor
    for offer in state["boards"]["alpha"]:
        offer.update(status="closed")
    offer = state["boards"]["alpha"][0]
    offer.update(status="available", required_rank=rank, participants=[])
    result = recommended_strikes(war, state, actor, members, NOW)["strikes"]
    assert len(result) == 1 and result[0]["reason"] == "Specialist needed"
    state["campaigns"]["alpha"]["deadline"] = NOW.isoformat()
    assert not recommended_strikes(war, state, actor, members, NOW)["strikes"]


def test_recommendations_are_capped_and_stable_and_skip_ineligible_omega(war, members):
    from opscribe.galactic_war_runtime import recommended_strikes

    open_campaign(war, members)
    state = war.snapshot(NOW)
    for offer in state["boards"]["alpha"]:
        offer.update(required_rank="", participants=[], status="available")
    actor = members["brother"]
    before = war.snapshot(NOW)
    first = recommended_strikes(war, state, actor, members, NOW)["strikes"]
    assert len(first) == 3 and all(offer["pool"] == "vanilla" for offer in first)
    assert first == recommended_strikes(war, state, actor, members, NOW)["strikes"]
    assert war.snapshot(NOW) == before


def test_strike_search_composes_filters_and_returns_only_currently_joinable_offers(war, members, monkeypatch):
    from opscribe.galactic_war_runtime import search_strikes

    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    vanilla = next(item for item in war.snapshot(NOW)["boards"]["alpha"] if item.get("pool") == "vanilla")
    act(war, members, "captain", "join", strike_id=vanilla["id"])

    results = search_strikes(
        war, war.snapshot(NOW), members["brother"], members, NOW,
        {"query": "inferno", "subsector_id": "alpha", "system_id": "alpha-0", "planet_id": "alpha", "pool": "vanilla", "specialist": "any", "availability": "recruiting"},
    )
    assert results["total"] == 1
    assert results["items"][0]["id"] == vanilla["id"]
    assert results["items"][0]["viewer_signup"]["can_join"] is True
    assert results["facets"] == {
        "subsectors": [{"id": "alpha", "name": "alpha"}],
        "systems": [{"id": "alpha-0", "name": "alpha-0", "subsector_id": "alpha"}],
        "worlds": [{"id": "alpha", "name": "alpha", "subsector_id": "alpha", "system_id": "alpha-0"}],
    }
    later_page = search_strikes(war, war.snapshot(NOW), members["brother"], members, NOW, page=1)
    assert not later_page["items"]
    assert later_page["facets"] == results["facets"]

    ineligible = replace(members["brother"], watch_brother=False)
    assert not search_strikes(war, war.snapshot(NOW), ineligible, members, NOW)["items"]
    assert search_strikes(war, war.snapshot(NOW), ineligible, members, NOW)["facets"] == {"subsectors": [], "systems": [], "worlds": []}
    assert search_strikes(war, war.snapshot(NOW), members["brother"], members, NOW, {"pool": "omega"})["total"] == 0


def test_web_snapshot_exposes_only_viewers_queue_and_bounded_activity(war, members, monkeypatch):
    from datetime import datetime as DateTime
    from types import SimpleNamespace
    from opscribe import galactic_war_runtime as runtime

    open_campaign(war, members)
    act(war, members, "brother", "queue_join", preferences={"subsector_id": "alpha", "pool": "vanilla"})
    war.geography.update(objective_catalog_version=1, world_classes={})
    with war._connection() as connection:
        state = war._load(connection)
        state["campaigns"]["alpha"]["onslaughts"].insert(0, {
            "starts_at": NOW.isoformat(), "ends_at": (NOW + timedelta(hours=8)).isoformat(),
            "base_damage": 6,
        })
        war._save(connection, state)
    class FixedDateTime(DateTime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(runtime, "datetime", FixedDateTime)
    monkeypatch.setattr(runtime, "get_store", lambda: war)
    monkeypatch.setattr(runtime, "directory", lambda guild: (members, {}, {}, []))
    guild = SimpleNamespace(get_member=lambda _identity: None)

    brother_view = runtime.web_snapshot(guild, "brother")
    captain_view = runtime.web_snapshot(guild, "captain")
    assert brother_view["matchmaking"]["status"]["status"] == "waiting"
    assert captain_view["matchmaking"]["status"] == {"status": "not_queued"}
    assert "matchmaking_queue" not in brother_view
    assert brother_view["activity"]["musters"]
    assert len(brother_view["activity"]["completed"]) <= 25
    assert "onslaughts" not in brother_view["campaigns"]["alpha"]
    offensive = brother_view["campaigns"]["alpha"]["active_offensive"]
    assert offensive["name"] == "Synaptic Onslaught"
    assert offensive["extra_drain_per_hour"] == pytest.approx(0.75)
    assert offensive["mitigation_percent"] == 0
    assert "base_damage" not in offensive


def test_activity_feed_pages_completed_settlements_without_duplicates(war, members):
    from opscribe.galactic_war_runtime import _activity_snapshot

    open_campaign(war, members)
    state = war.snapshot(NOW)
    offer = state["boards"]["alpha"][0]
    for index in range(30):
        attempt_id = f"GW-{index:032x}"
        timestamp = (NOW + timedelta(minutes=index)).isoformat()
        state["attempts"][attempt_id] = {
            "id": attempt_id, "offer": dict(offer), "status": "completed",
            "participants": [{"member_id": "brother", "company_id": "2", "kill_team_id": "2-1", "formation_id": "apothecarion"}], "locked_at": NOW.isoformat(),
        }
        state["settlements"][attempt_id] = {
            "aar_id": str(index), "participant_ids": ["brother"],
            "processed_at": (NOW + timedelta(days=1, minutes=index)).isoformat(),
            "strategic_at": timestamp,
        }

    teams = [{"id": "2-1", "name": "Secundus Strike Team"}]
    first = _activity_snapshot(war, state, NOW, members=members, teams=teams)
    assert len(first["completed"]) == 25
    assert first["completed"][0]["id"] == "GW-" + f"{29:032x}"
    assert first["completed"][0]["completed_at"] == (NOW + timedelta(minutes=29)).isoformat()
    assert first["next_cursor"] == first["completed"][-1]["id"]
    assert first["completed"][0]["kill_teams"] == ["Secundus Strike Team"]
    assert first["completed"][0]["companies"] == ["Secundus"]
    assert first["completed"][0]["company_refs"] == [{"id": "2", "name": "Secundus"}]
    assert first["completed"][0]["kill_team_refs"] == [{"id": "2-1", "name": "Secundus Strike Team", "company_id": "2"}]
    assert first["completed"][0]["specialist_cadres"] == [{"id": "apothecarion"}]
    newest = state["attempts"][first["completed"][0]["id"]]["participants"][0]
    newest["formation_id"] = ""
    promoted = dict(members)
    promoted["brother"] = replace(members["brother"], cadre_id="reclusiam")
    assert _activity_snapshot(war, state, NOW, members=promoted, teams=teams)["completed"][0]["specialist_cadres"] == []
    newest["formation_id"] = "2-1"
    assert _activity_snapshot(war, state, NOW, members=members, teams=teams)["completed"][0]["specialist_cadres"] == []
    second = _activity_snapshot(war, state, NOW, first["next_cursor"], members, teams)
    assert len(second["completed"]) == 5
    assert not second["next_cursor"]
    assert not ({item["id"] for item in first["completed"]} & {item["id"] for item in second["completed"]})


def test_system_snapshot_includes_all_its_worlds_and_excludes_other_systems(war, members, monkeypatch):
    from types import SimpleNamespace
    from opscribe import galactic_war_runtime as runtime

    war.geography["planets"].append({**war.geography["planets"][0], "id": "alpha-moon"})
    war.geography.update(objective_catalog_version=1, world_classes={})
    open_campaign(war, members)
    monkeypatch.setattr(runtime, "get_store", lambda: war)
    monkeypatch.setattr(runtime, "directory", lambda guild: (members, {}, {}, []))
    guild = SimpleNamespace(get_member=lambda identity: None)
    full_map = runtime.web_snapshot(guild, "brother")
    assert full_map["boards"] == {}
    assert len(full_map["recommendations"]["strikes"]) <= 3
    snapshot = runtime.web_snapshot(guild, "brother", "alpha", board_system_id="alpha-0")
    assert set(snapshot["boards"]) == {"alpha", "alpha-moon"}
    assert sum(len(board) for board in snapshot["boards"].values()) == 16
    assert snapshot["board_system_id"] == "alpha-0"
    with pytest.raises(ValueError, match="Unknown strike-board system"):
        runtime.web_snapshot(guild, "brother", board_system_id="missing")


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
    with pytest.raises(ValueError, match="signup roster"):
        war.validate_report(attempt, {**record, "brother_ids": ["captain", "other"]}, NOW)
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
    assert "company_id" in state["contributions"][1]
    recognition = war.recognition({"1-1": ["captain", "brother"]}, {"1": ["1-1"]}, NOW + timedelta(hours=2))
    assert recognition["members"]["brother"]["completed"] == 1
    assert recognition["members"]["other"]["completed"] == 1
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


def test_pressure_upgrade_is_prospective_and_fivefold():
    campaign = {"id": "upgrade", "status": "contested", "balance": 100,
                "calculated_at": NOW.isoformat(), "last_success_at": NOW.isoformat(),
                "deadline": (NOW + timedelta(days=14)).isoformat()}
    assert activate_pressure_policy(campaign, NOW + timedelta(hours=12))
    assert campaign["balance"] == pytest.approx(99)
    assert not activate_pressure_policy(campaign, NOW + timedelta(hours=13))
    advance_balance(campaign, NOW + timedelta(hours=24))
    assert campaign["balance"] == pytest.approx(94)
    idle = {**campaign, "balance": 100, "calculated_at": (NOW + timedelta(hours=72)).isoformat()}
    advance_balance(idle, NOW + timedelta(hours=96))
    assert idle["balance"] == pytest.approx(85)


def test_onslaught_integrates_activity_disruption_and_tick_boundaries():
    campaign = {"id": "onslaught", "status": "contested", "balance": 100,
                "calculated_at": NOW.isoformat(), "last_success_at": NOW.isoformat(),
                "deadline": (NOW + timedelta(days=14)).isoformat()}
    activate_pressure_policy(campaign, NOW)
    campaign["onslaughts"] = [{"starts_at": NOW.isoformat(),
        "ends_at": (NOW + timedelta(hours=8)).isoformat(), "base_damage": 6}]
    successes = [NOW - timedelta(hours=1)] * 10 + [NOW + timedelta(hours=4)] * 6
    one_step = dict(campaign)
    advance_balance(one_step, NOW + timedelta(hours=8), successes)
    for minute in range(1, 481):
        advance_balance(campaign, NOW + timedelta(minutes=minute), successes)
    assert campaign["balance"] == pytest.approx(one_step["balance"])
    assert campaign["balance"] == pytest.approx(100 - 10 / 3 - 4 - 1.6)
    status = active_offensive(campaign, NOW + timedelta(hours=4), successes)
    assert status["mitigation_percent"] == 60
    assert status["extra_drain_per_hour"] == pytest.approx(0.4)
    assert active_offensive(campaign, NOW + timedelta(hours=8), successes) is None


@pytest.mark.parametrize("recent_count,expected_damage", [(0, 4), (5, 5), (10, 6), (50, 6)])
def test_onslaught_activity_budget_and_deadline_truncation(recent_count, expected_damage):
    campaign = {"id": "deadline", "status": "contested", "balance": 100,
                "calculated_at": NOW.isoformat(), "last_success_at": NOW.isoformat(),
                "deadline": (NOW + timedelta(hours=4)).isoformat()}
    activate_pressure_policy(campaign, NOW)
    campaign["onslaughts"] = [{"starts_at": NOW.isoformat(),
        "ends_at": (NOW + timedelta(hours=8)).isoformat(), "base_damage": 4}]
    successes = [NOW - timedelta(hours=1)] * recent_count
    successes += [NOW - timedelta(hours=25)] * 10
    status = active_offensive(campaign, NOW, successes)
    assert status["ends_at"] == campaign["deadline"]
    assert status["extra_drain_per_hour"] == pytest.approx(expected_damage / 8)
    advance_balance(campaign, NOW + timedelta(hours=24), successes)
    assert campaign["balance"] == pytest.approx(100 - 10 / 6 - expected_damage / 2)
    assert campaign["calculated_at"] == campaign["deadline"]
    assert active_offensive(campaign, NOW + timedelta(hours=4), successes) is None
    before = dict(campaign)
    advance_balance(campaign, NOW, successes)
    assert campaign == before


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_onslaught_report_replay_and_reversal_preserve_attack(war, members, monkeypatch, order):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    with war._connection() as connection:
        state = war._load(connection)
        windows = [{"starts_at": NOW.isoformat(),
                    "ends_at": (NOW + timedelta(hours=8)).isoformat(), "base_damage": 6}]
        state["campaigns"]["alpha"]["onslaughts"] = windows
        war._save(connection, state)
    attempts = []
    for slot in range(2):
        participants = [f"onslaught-{slot}-{index}" for index in range(3)]
        offer = war.snapshot(NOW)["boards"]["alpha"][slot]["id"]
        for participant in participants:
            members[participant] = WarMember(participant, frozenset({"Watch Brother"}), watch_brother=True)
            result = act(war, members, participant, "join", strike_id=offer)
        attempts.append((result["attempt_id"], participants))
        war.reserve_report(result["attempt_id"], f"report-{slot}", NOW + timedelta(hours=2 + slot * 2))
    later = NOW + timedelta(hours=6)
    for slot in order:
        identity, participants = attempts[slot]
        war.settle(identity, f"aar-{slot}", participants, later)
    state = war.snapshot(later)
    campaign = state["campaigns"]["alpha"]
    assert campaign["balance"] == pytest.approx(20.45)
    assert campaign["active_offensive"]["mitigation_percent"] == 20
    reopened = GalacticWarStore(war.path, war.geography, war.catalogue, war.operations)
    assert reopened.tick(later)["campaigns"]["alpha"]["balance"] == pytest.approx(20.45)
    reopened.execute("reverse", members["wm"], {
        "version": state["version"], "strike_id": attempts[0][0], "reason": "Correct duplicate credit",
    }, members, later)
    corrected = reopened.snapshot(later)["campaigns"]["alpha"]
    assert corrected["balance"] == pytest.approx(19.15)
    assert corrected["active_offensive"]["mitigation_percent"] == 10
    assert corrected["onslaughts"] == windows


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


def test_legacy_campaign_pressure_activation_survives_reopen_and_replay(war, members):
    open_campaign(war, members)
    with war._connection() as connection:
        state = war._load(connection)
        campaign = state["campaigns"]["alpha"]
        campaign.pop("pressure_policy_at")
        campaign.pop("onslaughts")
        campaign["policy_version"] = 3
        war._save(connection, state)
    later = NOW + timedelta(hours=12)
    assert "pressure_policy_at" not in war.snapshot(later)["campaigns"]["alpha"]
    activated = war.tick(later)["campaigns"]["alpha"]
    assert activated["balance"] == pytest.approx(24)
    assert activated["pressure_policy_at"] == later.isoformat()
    assert activated["id"] == campaign["id"]
    reopened = GalacticWarStore(war.path, war.geography, war.catalogue)
    state = reopened.tick(NOW + timedelta(hours=24))
    replayed = dict(state["campaigns"]["alpha"])
    reopened._recalculate_campaign(state, replayed, NOW + timedelta(hours=24))
    assert replayed["balance"] == pytest.approx(19)
    assert replayed["balance"] == pytest.approx(state["campaigns"]["alpha"]["balance"])
    assert replayed["onslaughts"] == activated["onslaughts"]
    assert replayed["pressure_policy_at"] == later.isoformat()


def test_onslaught_schedule_bounds_and_independent_campaigns(war, members):
    open_campaign(war, members)
    campaign = war.snapshot(NOW)["campaigns"]["alpha"]
    previous = NOW
    assert len(campaign["onslaughts"]) in {2, 3}
    for window in campaign["onslaughts"]:
        starts = datetime.fromisoformat(window["starts_at"])
        ends = datetime.fromisoformat(window["ends_at"])
        assert 96 <= (starts - previous).total_seconds() / 3600 <= 120
        assert 6 <= (ends - starts).total_seconds() / 3600 <= 12
        assert 4 <= window["base_damage"] <= 6
        previous = starts
    other = {**campaign, "id": "different"}
    other.pop("pressure_policy_at")
    activate_pressure_policy(other, NOW)
    assert other["onslaughts"] != campaign["onslaughts"]
    assert war.snapshot(NOW)["campaigns"]["alpha"]["onslaughts"] == campaign["onslaughts"]


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


@pytest.mark.parametrize("company,name,destination", [
    ("1", "Primus", 1468422902440267934), ("2", "Secundus", 1468421779629543536),
    ("3", "Tertius", 1527777380838543421), ("4", "Quartus", 1564742093253902356),
])
def test_company_commitment_uses_proclamation_banner_and_retry_deduplication(war, monkeypatch, company, name, destination):
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from opscribe import galactic_war_runtime as runtime

    config = json.loads((runtime.ROOT / "config" / "config.json").read_text())["galactic_war"]
    role = SimpleNamespace(id=100, name=f"Watch Company {name}")
    guild = SimpleNamespace(id=1, roles=[role])
    channel = MagicMock(spec=runtime.discord.TextChannel)
    channel.guild = guild
    messages = []

    async def history(**kwargs):
        for message in messages:
            yield message

    async def send(**kwargs):
        message = SimpleNamespace(id=123, embeds=[kwargs["embed"]])
        messages.append(message)
        return message

    channel.history.side_effect = history
    channel.send = AsyncMock(side_effect=send)
    guild.get_channel = MagicMock(side_effect=lambda identity: channel if identity == destination else None)
    notice = {"event": "company_committed", "target": company, "company_id": company,
              "subsector_id": "alpha", "campaign": {"deadline": (NOW + timedelta(days=14)).isoformat()}, "delivered": False}
    war.geography["sectors"][0]["name"] = "The Canticle Reach"
    monkeypatch.setattr(war, "snapshot", lambda now: {"outbox": {"notice": notice}})
    monkeypatch.setattr(war, "acknowledge_notice", MagicMock(side_effect=OSError("Interrupted after send")))
    with pytest.raises(OSError):
        asyncio.run(runtime.deliver_notices(guild, war, config))
    request = channel.send.await_args.kwargs
    assert request["embed"].title == "❱❱❱ COMPANY COMMITTED"
    assert role.name in request["embed"].description and "The Canticle Reach" in request["embed"].description
    assert request["embed"].image.url == "attachment://COMPANY_COMMITTED.png"
    assert request["file"].fp.closed
    assert request["content"] == "<@&100>"
    assert [entry.id for entry in request["allowed_mentions"].roles] == [100]
    assert request["allowed_mentions"].everyone is request["allowed_mentions"].users is False
    assert len(request["embed"]) < 6000
    monkeypatch.setattr(war, "acknowledge_notice", MagicMock())
    asyncio.run(runtime.deliver_notices(guild, war, config))
    channel.send.assert_awaited_once()
    assert {call.args[0] for call in guild.get_channel.call_args_list} == {destination}


@pytest.mark.parametrize("event", ["kill_team_committed", "kill_team_assigned"])
def test_kt_commitment_uses_its_forum_chat_and_original_destination(war, monkeypatch, event):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from opscribe import galactic_war_runtime as runtime, transfer_ops

    company = SimpleNamespace(id=100, name="Watch Company Quartus")
    team = SimpleNamespace(id=301, name="Kill-Team Amar")
    guild = SimpleNamespace(roles=[company], get_role=lambda identity: team if identity == 301 else None)
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))

    async def history(**kwargs):
        if False:
            yield

    channel.history = history
    resolver = AsyncMock(return_value=channel)
    monkeypatch.setattr(transfer_ops, "_welcome_channel", resolver)
    war.geography["systems"][0]["name"] = "Solenne Crown"
    war.geography["sectors"][0]["name"] = "The Canticle Reach"
    notice = {"event": event, "target": "4-301", "subsector_id": "alpha", "system_id": "alpha-0", "delivered": False}
    monkeypatch.setattr(war, "snapshot", lambda now: {"outbox": {"notice": notice}, "assignments": {"4-301": "beta-0"}})
    monkeypatch.setattr(war, "acknowledge_notice", MagicMock())
    asyncio.run(runtime.deliver_notices(guild, war, {}))
    resolver.assert_awaited_once_with(guild, {"company_id": 100}, team)
    request = channel.send.await_args.kwargs
    assert request["embed"].title == "❱❱❱ KILL-TEAM COMMITTED"
    assert "Solenne Crown" in request["embed"].description
    assert "beta" not in request["embed"].description
    assert request["content"] == "<@&301>"
    assert request["embed"].image.url == "attachment://KILL_TEAM_COMMITTED.png"
    assert request["file"].fp.closed
    assert [entry.id for entry in request["allowed_mentions"].roles] == [301]
    assert request["allowed_mentions"].everyone is request["allowed_mentions"].users is False
    notice.pop("subsector_id")
    notice.pop("system_id")
    embed, _filename = runtime._commitment_embed(war, notice, team, {}, "legacy")
    assert "Destination not recorded" in embed.description


def test_queued_roster_notices_are_suppressed_and_unroutable_commitments_stay_pending(war, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from opscribe import galactic_war_runtime as runtime

    notices = {event: {"event": event, "target": "5", "delivered": False}
               for event in ("roster_locked", "roster_cancelled", "company_committed")}
    monkeypatch.setattr(war, "snapshot", lambda now: {"outbox": notices})
    acknowledge = MagicMock()
    monkeypatch.setattr(war, "acknowledge_notice", acknowledge)
    monkeypatch.setattr(runtime._g, "logger", MagicMock())
    guild = SimpleNamespace(roles=[SimpleNamespace(id=100, name="Watch Company Quintus")], get_channel=MagicMock())
    asyncio.run(runtime.deliver_notices(guild, war, {"notification_channel_id": 999}))
    assert acknowledge.call_count == 2
    acknowledge.assert_any_call("roster_locked", "suppressed")
    acknowledge.assert_any_call("roster_cancelled", "suppressed")
    guild.get_channel.assert_not_called()
    runtime._g.logger.exception.assert_called_once()


def test_invasion_notice_has_flavor_watch_brother_ping_and_retry_deduplication(war, members, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from opscribe import galactic_war_runtime as runtime
    from opscribe.constants import WATCH_BROTHER_ROLE_ID

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
    guild = SimpleNamespace(get_channel=lambda identity: channel if identity == 1430055064969674777 else None)
    config = {"notification_channel_id": 1430055064969674777, "test_notification_channel_id": 1511147649003425973, "strategium_url": "https://jericho-strategium.duckdns.org/reach"}
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
    assert "𝐏𝐑𝐈𝐎𝐑𝐈𝐓𝐘" in message.embeds[0].title
    assert "<:DeathSentence:1528598025604300911>" in message.embeds[0].description
    assert message.embeds[0].image.url == "attachment://INCURSION_ALERT.png"
    assert message.request["file"].filename == "INCURSION_ALERT.png"
    assert message.request["file"].fp.closed
    assert len(message.embeds[0]) < 6000
    assert [field.name for field in message.embeds[0].fields] == ["Campaign"]
    assert "The Long Watch calls" in message.embeds[0].description
    assert "Report Completion" not in message.embeds[0].description
    assert "Captains:" not in message.embeds[0].description
    assert message.request["content"] == f"<@&{WATCH_BROTHER_ROLE_ID}>"
    assert [role.id for role in message.request["allowed_mentions"].roles] == [WATCH_BROTHER_ROLE_ID]
    assert message.request["allowed_mentions"].everyone is False
    monkeypatch.setattr(war, "acknowledge_notice", acknowledge)
    asyncio.run(runtime.deliver_notices(guild, war, config))
    asyncio.run(runtime.deliver_notices(guild, war, config))
    assert len(channel.messages) == 1


def test_sandbox_notices_preview_live_alert_in_test_channel_and_revert_without_live_outbox(war, members):
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
            message = SimpleNamespace(id=len(messages) + 1, embeds=[kwargs["embed"]], request=kwargs)
            messages.append(message)
            return message

    guild = SimpleNamespace(get_channel=lambda identity: Channel() if identity == 1511147649003425973 else None)
    config = {"notification_channel_id": 1430055064969674777, "test_notification_channel_id": 1511147649003425973, "strategium_url": "https://jericho-strategium.duckdns.org/reach"}
    sandbox = InvasionSandbox(war, members["wm"])
    session = sandbox.start("alpha", NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert not messages
    sandbox.execute(session, "send_test_notice", {"version": 0}, members, NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, {"notification_channel_id": config["notification_channel_id"]}))
    assert not messages
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert len(messages) == 1
    store, clock = sandbox.context(session, NOW)
    job = store.snapshot(clock)["test_notices"][0]
    expected = runtime._invasion_embed(job["sector_name"], job["campaign"], config, messages[0].embeds[0].footer.text)
    assert messages[0].embeds[0].to_dict() == expected.to_dict()
    assert [role.id for role in messages[0].request["allowed_mentions"].roles] == [runtime.WATCH_BROTHER_ROLE_ID]
    assert messages[0].request["content"] == f"<@&{runtime.WATCH_BROTHER_ROLE_ID}>"
    assert messages[0].request["file"].fp.closed
    sandbox.revert(session, NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert len(messages) == 2
    assert "REVERTED" in messages[1].embeds[0].title
    assert messages[1].request["allowed_mentions"].to_dict()["parse"] == []
    assert not war.snapshot(NOW)["outbox"]
    session = sandbox.start("alpha", NOW)
    sandbox.execute(session, "send_test_notice", {"version": 0}, members, NOW)
    sandbox.revert(session, NOW)
    asyncio.run(runtime.deliver_test_notices(guild, war, config))
    assert len(messages) == 2


def test_invasion_banner_is_fresh_and_closed_after_failed_send():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from opscribe import galactic_war_runtime as runtime

    channel = SimpleNamespace(send=AsyncMock(side_effect=[OSError("Interrupted"), SimpleNamespace(id=1)]))
    embed = runtime._invasion_embed("Alpha", None, {}, "galactic-war:test:legacy")
    with pytest.raises(OSError):
        asyncio.run(runtime._send_invasion(channel, embed))
    first = channel.send.await_args.kwargs["file"]
    assert first.fp.closed
    asyncio.run(runtime._send_invasion(channel, embed))
    second = channel.send.await_args.kwargs["file"]
    assert second is not first
    assert second.fp.closed
    assert "legacy notice" in embed.fields[-1].value
    assert len(embed) < 6000


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
        {"id": "general", "name": "General", "settings": ["hive|industrial"]},
        {"id": "wall", "name": "The Last Wall", "required_rank": "High Blade", "settings": ["hive|industrial"]},
        {"id": "iron", "name": "Iron Sleeper", "required_rank": "Forgemaster", "settings": ["hive|industrial"]},
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
    for planet in planets:
        compatible = [objective for objective in objectives if atlas_compatible(planet["world_type"], planet["biome"], objective)]
        assert any(not objective.get("required_rank") for objective in compatible), planet["id"]
        assert any(objective.get("required_rank") for objective in compatible), planet["id"]
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


def test_reused_evidence_is_rejected_without_credit_and_can_be_corrected(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    def report(identity, aar_id):
        for member in ("captain", "brother", "other"):
            result = act(war, members, member, "join", strike_id=identity)
        return {"aar_id": aar_id, "strike_id": result["attempt_id"], "brother_ids": ["captain", "brother", "other"], "mission": "Inferno", "difficulty_class": "absolute_ops", "strike_success": True, "strike_evidence_hashes": ["shared-image"]}
    first = report(war.snapshot(NOW)["boards"]["alpha"][0]["id"], "aar-first")
    war.submit_report(first, NOW)
    second = report(war.snapshot(NOW)["boards"]["alpha"][0]["id"], "aar-second")
    with pytest.raises(ValueError, match="already submitted for another strike"):
        war.reserve_report(second["strike_id"], "duplicate-evidence", NOW, ["shared-image"])
    assert "report_key" not in war.snapshot(NOW)["attempts"][second["strike_id"]]
    with pytest.raises(ValueError, match="already submitted for another strike"):
        war.submit_report(second, NOW)
    state = war.snapshot(NOW)
    assert len(state["contributions"]) == 3
    assert state["attempts"][second["strike_id"]]["status"] == "locked"
    assert not state["reviews"]
    corrected = {**second, "strike_evidence_hashes": ["second-operation-evidence"]}
    assert war.submit_report(corrected, NOW)["aar_id"] == "aar-second"
    assert len(war.snapshot(NOW)["contributions"]) == 6


def test_legacy_pending_review_is_revalidated_and_auto_settled(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer = war.snapshot(NOW)["boards"]["alpha"][0]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=offer["id"])
    attempt_id = result["attempt_id"]
    record = {
        "aar_id": "legacy-aar", "strike_id": attempt_id,
        "brother_ids": ["captain", "brother", "other"], "mission": "Inferno",
        "difficulty_class": "absolute_ops", "strike_success": True,
        "strike_evidence_hashes": ["legacy-selection"],
    }
    with war._connection() as connection:
        state = war._load(connection)
        state["attempts"][attempt_id]["status"] = "review_pending"
        state["attempts"][attempt_id]["evidence_hashes"] = ["legacy-selection"]
        next(item for item in state["boards"]["alpha"] if item.get("attempt_id") == attempt_id)["status"] = "review_pending"
        state["reviews"][attempt_id] = {
            "strike_id": attempt_id, "aar_id": "legacy-aar", "status": "pending",
            "reason": "Legacy review", "record": record, "received_at": NOW.isoformat(),
            "archive_pending": False,
        }
        war._save(connection, state)

    assert war.submit_report(record, NOW)["aar_id"] == "legacy-aar"
    state = war.snapshot(NOW)
    assert state["reviews"][attempt_id]["status"] == "auto_accepted"
    assert state["attempts"][attempt_id]["status"] == "completed"
    assert len(state["contributions"]) == 3


def test_invalid_legacy_pending_review_requires_correction_and_remains_retryable(war, members, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from opscribe import aar_ops, galactic_war_runtime as runtime

    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    offer = war.snapshot(NOW)["boards"]["alpha"][0]
    for member in ("captain", "brother", "other"):
        result = act(war, members, member, "join", strike_id=offer["id"])
    attempt_id = result["attempt_id"]
    record = {
        "aar_id": "invalid-legacy-aar", "strike_id": attempt_id,
        "brother_ids": ["captain", "other"], "mission": "Inferno",
        "difficulty_class": "absolute_ops", "strike_success": True,
    }
    with war._connection() as connection:
        state = war._load(connection)
        state["attempts"][attempt_id]["status"] = "review_pending"
        state["attempts"][attempt_id]["evidence_hashes"] = ["invalid-legacy-evidence"]
        next(item for item in state["boards"]["alpha"] if item.get("attempt_id") == attempt_id)["status"] = "review_pending"
        state["reviews"][attempt_id] = {
            "strike_id": attempt_id, "aar_id": "invalid-legacy-aar", "status": "pending",
            "reason": "Legacy review", "record": record, "received_at": NOW.isoformat(),
            "archive_pending": False,
        }
        war._save(connection, state)

    async def save_legacy_report(saved_record):
        return war.submit_report(saved_record, NOW)

    monkeypatch.setattr(aar_ops, "save_aar_record", save_legacy_report)
    monkeypatch.setattr(runtime._g, "DATASTORE", SimpleNamespace(flush=AsyncMock()))
    monkeypatch.setattr(runtime._g, "logger", SimpleNamespace(warning=lambda *args, **kwargs: None))
    asyncio.run(runtime.recover_review_archives(SimpleNamespace(), war))
    state = war.snapshot(NOW)
    assert state["reviews"][attempt_id]["status"] == "correction_required"
    assert state["reviews"][attempt_id]["decision_reason"] == "AAR participants do not match the locked signup roster"
    assert state["attempts"][attempt_id]["status"] == "locked"
    assert state["boards"]["alpha"][0]["status"] == "locked"
    assert not state["settlements"]
    assert not state["contributions"]
    assert "report_key" not in state["attempts"][attempt_id]
    assert "evidence_hashes" not in state["attempts"][attempt_id]

    war.geography.update(objective_catalog_version=1, world_classes={})
    monkeypatch.setattr(runtime, "get_store", lambda: war)
    monkeypatch.setattr(runtime, "directory", lambda _guild: (members, {}, {}, []))
    guild = SimpleNamespace(get_member=lambda _identity: None)
    visible = runtime.web_snapshot(guild, "brother", "alpha")
    assert visible["boards"]["alpha"][0]["report_correction"] == {
        "aar_id": "invalid-legacy-aar",
        "reason": "AAR participants do not match the locked signup roster",
    }
    members["outsider"] = WarMember("outsider", frozenset({"Watch Brother"}), watch_brother=True)
    hidden = runtime.web_snapshot(guild, "outsider", "alpha")
    assert hidden["boards"]["alpha"][0]["report_correction"] is None

    corrected = {**record, "aar_id": "corrected-legacy-aar", "brother_ids": ["captain", "brother", "other"]}
    war.reserve_report(attempt_id, "corrected-report-key", NOW)
    assert war.submit_report(corrected, NOW)["aar_id"] == "corrected-legacy-aar"
    assert len(war.snapshot(NOW)["contributions"]) == 3


def test_recipe_reports_settle_once_without_staff_review(war, members, monkeypatch):
    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    open_campaign(war, members)
    for index in range(2):
        identity = war.snapshot(NOW)["boards"]["alpha"][0]["id"]
        for member in ("captain", "brother", "other"):
            result = act(war, members, member, "join", strike_id=identity)
        record = {"aar_id": f"aar-{index}", "strike_id": result["attempt_id"], "brother_ids": ["captain", "brother", "other"], "mission": "Inferno", "difficulty_class": "absolute_ops", "strike_success": True, "strike_evidence_hashes": [f"image-{index}"]}
        assert war.submit_report(record, NOW)["aar_id"] == record["aar_id"]
        assert war.submit_report(record, NOW)["aar_id"] == record["aar_id"]
    state = war.snapshot(NOW)
    assert len(state["contributions"]) == 6
    assert not state["reviews"]


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
    assert campaign["balance"] == pytest.approx(25.75)
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