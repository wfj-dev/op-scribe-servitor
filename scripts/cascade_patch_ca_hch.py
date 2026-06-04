#!/usr/bin/env python3
"""Add option pool expansions for chief_apothecary and high_chaplain to cascade_options.json."""
import json, os

CASCADE_FILE = os.path.join(os.path.dirname(__file__), "../reference/cascade_options.json")

ADDITIONS = {
    "chief_apothecary": {
        "apothecarion_field_forward": {
            "name": "Apothecarion Field Forward",
            "description": "The Chief Apothecary moves theatre medical capability to the front. Apothecaries operate in the kill team's operational zone — gene-seed recovered in the field, not after the fact.",
            "tags": ["recovery", "aggressive", "resilience", "faith"],
            "chapter_affinity": ["Blood Angels", "Salamanders", "Celestial Lions", "Raptors", "Flesh Tearers"],
            "node_affinity": ["war_world", "feral_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "iron_constitution": {
            "name": "Iron Constitution",
            "description": "Theatre-wide physiological hardening protocol. Stress-inoculation, environmental resistance, fatigue countermeasures — kill teams are prepared for prolonged operations in conditions that would break baseline forces.",
            "tags": ["resilience", "defensive", "recovery", "tech"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Iron Hounds", "Minotaurs", "Marines Malevolent"],
            "node_affinity": ["dead_world", "agri_world", "mining_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_chapter_endures": {
            "name": "The Chapter Endures",
            "description": "The Chief Apothecary declares the Chapter's continuation the absolute priority. Every progenoid recovered, every fallen brother witnessed, regardless of cost to tactical outcomes. The Chapter is the mission.",
            "tags": ["recovery", "faith", "resilience", "fortify"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Salamanders", "Bleeding Hearts", "Tempestuous Angels", "Celestial Lions"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith", "recovery"],
            "weight": 1.0
        },
        "black_carapace_interrogation": {
            "name": "Black Carapace Interrogation",
            "description": "The Chief Apothecary extracts intelligence from the fallen — enemy and Watch alike. Neurological traces, pheromone identification, combat bio-signature analysis. What the dead experienced informs what the living must do.",
            "tags": ["intel", "recovery", "void", "tech"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Mentors", "Iron Ravens"],
            "node_affinity": ["dead_world", "watch_station", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "combat_augmentation_protocol": {
            "name": "Combat Augmentation Protocol",
            "description": "Full pre-battle physiological optimisation. Stimm cocktails, organ-priming, threshold-expansion — the Chief Apothecary signs off on a biological doctrine that pushes kill teams beyond their limits before the first shot.",
            "tags": ["aggressive", "terminus", "resilience", "tech"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Marines Malevolent", "Minotaurs", "Bleeding Hearts", "Carmine Blades"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive", "terminus"],
            "weight": 1.0
        },
        "toxin_countermeasure": {
            "name": "Toxin Countermeasure",
            "description": "The Chief Apothecary prepares specific counter-biological protocols for the identified threat vector. Tyranid bio-toxins, Chaos corruption, xeno-spore environments — kill teams operate clean where others would be incapacitated.",
            "tags": ["defensive", "void", "tech", "resilience"],
            "chapter_affinity": ["Iron Hands", "Raptors", "Blood Ravens", "Tome Keepers", "Dark Angels"],
            "node_affinity": ["dead_world", "agri_world", "feral_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "defensive"],
            "weight": 1.0
        },
        "witness_every_fall": {
            "name": "Witness Every Fall",
            "description": "The Chief Apothecary mandates that no battle-brother falls without confirmation, harvest, and witness. This beat, every op carries formal Apothecarion oversight. The dead are not abandoned — they are received.",
            "tags": ["faith", "recovery", "resilience", "intel"],
            "chapter_affinity": ["Salamanders", "Blood Angels", "Celestial Lions", "Cowled Wardens", "Tome Keepers"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "sustained_operations_doctrine": {
            "name": "Sustained Operations Doctrine",
            "description": "The Chief Apothecary extends the operational window. Fatigue compressed, damage managed in the field, stimms controlled for sustained output. Kill teams operate for longer than the enemy can plan for.",
            "tags": ["resilience", "recovery", "aggressive", "tech"],
            "chapter_affinity": ["Raptors", "Iron Hands", "Mentors", "Crimson Fists", "Celestial Lions"],
            "node_affinity": ["frontier_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "psychofortification_rites": {
            "name": "Psychofortification Rites",
            "description": "The Chief Apothecary reinforces mental and neurological resilience across the theatre. Against psychic intrusion, warp-madness, and terror-weapon effects — the mind-resilience of the force is hardened by Apothecarion decree.",
            "tags": ["void", "defensive", "resilience", "faith"],
            "chapter_affinity": ["Dark Angels", "Tome Keepers", "Blood Ravens", "Cowled Wardens", "Black Templars"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "accelerated_recovery": {
            "name": "Accelerated Recovery",
            "description": "Reduced downtime between ops. The Chief Apothecary implements fast-return medical protocols — wounds stabilised and cleared in minimum time. Kill teams return to operational status faster than the enemy's reinforcement cycle.",
            "tags": ["recovery", "resilience", "tech", "aggressive"],
            "chapter_affinity": ["Iron Hands", "Raptors", "Mentors", "Celestial Lions", "Crimson Fists"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "high_chaplain": {
        "the_litany_of_hate": {
            "name": "The Litany of Hate",
            "description": "The High Chaplain delivers theatre-wide war-litanies that strip fear and hesitation from every brother in the Watch. This beat, kill teams fight without restraint — the enemy is granted no mercy and no quarter.",
            "tags": ["faith", "aggressive", "terminus", "suppression"],
            "chapter_affinity": ["Black Templars", "Blood Angels", "Flesh Tearers", "Tempestuous Angels", "Angels of Vengeance", "Bleeding Hearts"],
            "node_affinity": ["war_world", "feral_world", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive", "terminus"],
            "weight": 1.0
        },
        "oath_of_iron": {
            "name": "Oath of Iron",
            "description": "Every active brother in the theatre swears an iron oath before the ops window opens — an oath of endurance, not achievement. Whatever the battlefield demands, the Watch will still be standing at the end.",
            "tags": ["faith", "resilience", "fortify", "defensive"],
            "chapter_affinity": ["Iron Hands", "Iron Lords", "Crimson Fists", "Iron Hounds", "Minotaurs", "Black Templars"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive", "resilience"],
            "weight": 1.0
        },
        "the_speaking_of_names": {
            "name": "The Speaking of Names",
            "description": "The High Chaplain names the fallen of the Reach — dead Watch brothers whose sacrifice consecrates this theatre. The living fight knowing those names stand behind them. Every advance is for the dead.",
            "tags": ["faith", "resilience", "recovery", "elimination"],
            "chapter_affinity": ["Salamanders", "Blood Angels", "Celestial Lions", "Tome Keepers", "Space Wolves"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "condemn_the_heretic": {
            "name": "Condemn the Heretic",
            "description": "Formal theological condemnation issued against an identified traitor, xenos collaborator, or Chaos affiliate on the committed node. The High Chaplain's word is now the Watch's mandate. No mercy, no negotiation.",
            "tags": ["faith", "elimination", "terminus", "aggressive"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Angels of Defiance", "Angels of Vengeance", "Cowled Wardens"],
            "node_affinity": ["hive_world", "shrine_world", "fortress_world", "penal_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "the_fire_unquenched": {
            "name": "The Fire Unquenched",
            "description": "Theatre-wide spiritual fortification against despair. The High Chaplain addresses the force directly — whatever has happened, whatever will happen, the Watch does not break. The fire does not go out.",
            "tags": ["faith", "resilience", "fortify", "recovery"],
            "chapter_affinity": ["Salamanders", "Black Templars", "Blood Angels", "Crimson Fists", "Angels of Defiance"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "purgation_rites": {
            "name": "Purgation Rites",
            "description": "Theatre-wide spiritual cleansing declared before the ops window. Chaplains attend every kill team at formation — doubts, Chaos taints, warp-corruptions, broken oaths are purged. The Watch goes in clean.",
            "tags": ["faith", "defensive", "void", "resilience"],
            "chapter_affinity": ["Black Templars", "Cowled Wardens", "Dark Angels", "Tome Keepers", "Blood Ravens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "sanctioned_fury": {
            "name": "Sanctioned Fury",
            "description": "The High Chaplain releases the Watch from its restraint. Controlled aggression is dispensed with — sanctioned rage is the doctrine. The enemy will know what it means to face warriors who fight with spiritual fire and no leash.",
            "tags": ["faith", "aggressive", "elimination", "terminus"],
            "chapter_affinity": ["Flesh Tearers", "Blood Angels", "Tempestuous Angels", "Bleeding Hearts", "Carmine Blades", "Angels of Vengeance"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "vigil_unyielding": {
            "name": "Vigil Unyielding",
            "description": "The High Chaplain's declaration for the beat: not one position, not one gain, not one brother will be surrendered. Spiritual tenacity is the doctrine — the Watch holds everything it has. Nothing yields.",
            "tags": ["faith", "defensive", "fortify", "suppression"],
            "chapter_affinity": ["Black Templars", "Iron Hands", "Minotaurs", "Crimson Fists", "Iron Lords"],
            "node_affinity": ["fortress_world", "mining_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "litany_of_revelation": {
            "name": "Litany of Revelation",
            "description": "The High Chaplain speaks to the hidden enemy. Spiritual doctrine of exposure — what conceals itself, what lies, what operates in shadow is condemned and revealed. The Watch sees what hides.",
            "tags": ["faith", "intel", "void", "stealth"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Cowled Wardens", "Tome Keepers", "Necropolis Hawks"],
            "node_affinity": ["dead_world", "hive_world", "watch_station", "pleasure_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "void"],
            "weight": 1.0
        },
        "bond_of_brotherhood": {
            "name": "Bond of Brotherhood",
            "description": "The High Chaplain orders every kill team in the theatre to formally witness each other's oaths before the ops window. Brotherhood is combat doctrine — no brother fights alone, and no achievement goes unshared.",
            "tags": ["faith", "resilience", "recovery", "intel"],
            "chapter_affinity": ["Salamanders", "Space Wolves", "Blood Angels", "Celestial Lions", "Tome Keepers", "Iron Hounds"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
}

SCHEMA_UPDATES = {
    "chief_apothecary": {
        "gene_seed_imperative": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Salamanders", "Celestial Lions", "Bleeding Hearts"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "battlefield_augmentation": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Marines Malevolent", "Minotaurs", "Bleeding Hearts"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive", "terminus"],
            "weight": 1.0
        },
        "quarantine_authority": {
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Iron Hands"],
            "node_affinity": ["dead_world", "agri_world", "watch_station", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "defensive"],
            "weight": 1.0
        },
    },
    "high_chaplain": {
        "the_eternal_vigil": {
            "chapter_affinity": ["Black Templars", "Iron Hands", "Salamanders", "Crimson Fists", "Cowled Wardens"],
            "node_affinity": ["fortress_world", "shrine_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "crusade_sanction": {
            "chapter_affinity": ["Black Templars", "Blood Angels", "Tempestuous Angels", "Angels of Defiance", "Angels of Vengeance"],
            "node_affinity": ["war_world", "shrine_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "rites_of_absolution": {
            "chapter_affinity": ["Dark Angels", "Cowled Wardens", "Tome Keepers", "Blood Ravens", "Salamanders"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "faith"],
            "weight": 1.0
        },
    },
}

# ---- apply ----
data = json.load(open(CASCADE_FILE))

for role_key, options in ADDITIONS.items():
    role_data = data.setdefault(role_key, {})
    for opt_key, opt_val in options.items():
        role_data[opt_key] = opt_val

for role_key, updates in SCHEMA_UPDATES.items():
    role_data = data.get(role_key, {})
    for opt_key, fields in updates.items():
        if opt_key in role_data:
            role_data[opt_key].update(fields)

with open(CASCADE_FILE, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")

for role in ["chief_apothecary", "high_chaplain"]:
    count = sum(1 for k in data[role] if not k.startswith("_"))
    print(f"{role}: {count} options")
