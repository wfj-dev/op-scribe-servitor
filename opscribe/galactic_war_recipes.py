"""Versioned vanilla adaptations of the shared Atlas objective catalogue."""

from __future__ import annotations

from itertools import combinations


class VanillaRecipes:
    def __init__(self, definition: dict, operations: dict, stratagems: dict, catalogue: list[dict]):
        if definition.get("schema_version") != 1 or type(definition.get("version")) is not int or definition["version"] < 1:
            raise ValueError("Unsupported vanilla recipe version")
        self.definition = definition
        self.operations = {entry["name"]: entry for entry in operations["operations"]}
        self.stratagems = {entry["name"]: entry for entry in stratagems["stratagems"]}
        self.conflicts = set()
        conflict_map = stratagems.get("conflict_map", {})
        for group in conflict_map.get("category_groups", {}).values():
            if isinstance(group, dict):
                self.conflicts.update(frozenset(pair) for pair in combinations(group.get("members", []), 2))
        for name, entry in self.stratagems.items():
            self.conflicts.update(frozenset((name, other)) for other in entry.get("specific_conflicts", []))
        for name, entry in conflict_map.get("specific_conflicts", {}).items():
            if isinstance(entry, dict):
                self.conflicts.update(frozenset((name, other)) for other in entry.get("blocks", []))
        if set(definition["objectives"]) != {entry["id"] for entry in catalogue}:
            raise ValueError("Vanilla recipes must cover exactly the shared objective catalogue")
        for section in ("objectives", "worlds", "biomes"):
            for entry in definition[section].values():
                missions = entry.get("missions", [])
                if not missions or len(missions) != len(set(missions)):
                    raise ValueError("Vanilla recipe needs distinct operation references")
                for mission in missions:
                    operation = self.operations.get(mission, {})
                    if not operation.get("strats_allowed") or operation.get("faction_listed") not in {"tyranid", "mixed", "chaos"}:
                        raise ValueError(f"Unsupported recipe operation: {mission}")
                    if operation["faction_listed"] == "chaos" and not operation.get("intel_lapse_forced"):
                        raise ValueError("Chaos recipes require forced Intelligence Lapse")
        self.coverage_count = 0
        for entry in catalogue:
            if not entry.get("settings"):
                raise ValueError("Vanilla recipes require shared Atlas settings")
            for setting in entry["settings"]:
                world, biome = setting.split("|")
                self.resolve(world, biome, entry)
                self.coverage_count += 1

    def _validate_modifiers(self, positive: str, negatives: list[str], forced: list[str]) -> None:
        if len(negatives) != 3 or len(set(negatives)) != 3:
            raise ValueError("Vanilla recipe requires three distinct negative stratagems")
        selected = [positive, *negatives, *forced]
        if len(set(selected)) != len(selected):
            raise ValueError("Duplicate vanilla recipe stratagem")
        for name, kind in [(positive, "buff"), *((name, "debuff") for name in negatives), *((name, "special") for name in forced)]:
            entry = self.stratagems.get(name, {})
            if entry.get("excluded", True) or entry.get("type") != kind:
                raise ValueError(f"Unsupported recipe stratagem: {name}")
        if any(frozenset(pair) in self.conflicts for pair in combinations(selected, 2)):
            raise ValueError("Conflicting vanilla recipe stratagems")

    def resolve(self, world: str, biome: str, objective: dict) -> tuple[str, dict]:
        setting = f"{world}|{biome}"
        if setting not in objective.get("settings", []):
            raise ValueError("Unsupported Atlas recipe setting")
        try:
            entry = self.definition["objectives"][objective["id"]]
            terrain = self.definition["biomes"][biome]
            world_entry = self.definition["worlds"][world]
        except KeyError as error:
            raise ValueError("Missing vanilla recipe coverage") from error
        preferred = [mission for mission in entry["missions"] if mission in terrain["missions"]]
        candidates = terrain["missions"] if biome == "starship" else preferred or entry["missions"]
        mission = next((name for name in world_entry["missions"] if name in candidates), candidates[0])
        operation = self.operations[mission]
        forced = ["Intelligence Lapse"] if operation["faction_listed"] == "chaos" else []
        negatives = [entry["negative"], world_entry["negative"], terrain["negative"]]
        self._validate_modifiers(entry["positive"], negatives, forced)
        notes = ["Complete the assigned vanilla operation; the Atlas objective is a thematic adaptation, not an additional in-map task."]
        approximate = biome in {"ice", "asteroid"} or (biome != "starship" and not preferred)
        if approximate:
            notes.append("The vanilla environment approximates this Atlas biome; no custom terrain or environmental physics is required.")
        if biome == "starship" and objective["id"] not in {"sabotage", "exterminatus", "purge"}:
            notes.append("Reclamation preserves the ship setting, but its reactor-sabotage objective substitutes for the Atlas task.")
        if forced:
            notes.append("Intelligence Lapse is mandatory and additional to the one positive and three negative stratagems.")
        if mission == "Vortex":
            notes.append("Intelligence Lapse converts reinforcement waves, not the fixed Mutalith Vortex Beast boss.")
        return mission, {
            "id": f"{setting}|{objective['id']}", "version": self.definition["version"],
            "positive": entry["positive"], "negatives": negatives, "forced": forced,
            "adaptation": "approximate_environment" if approximate else "vanilla_adaptation",
            "notes": notes,
        }

    def select(self, world: str, biome: str, objective: dict, operations: list[dict]) -> tuple[dict, dict]:
        mission, recipe = self.resolve(world, biome, objective)
        operation = next((entry for entry in operations if entry.get("mission") == mission
                          and entry.get("mode") == "ops_strat" and entry.get("capacity") == 3
                          and entry.get("difficulty_class") == "hard_stratagem"), None)
        if operation is None:
            raise ValueError(f"No supported Hard-Stratagem operation for recipe: {mission}")
        return operation, recipe