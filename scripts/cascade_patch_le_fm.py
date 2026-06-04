#!/usr/bin/env python3
"""Add option pool expansions for lord_executioner and forgemaster to cascade_options.json."""
import json, os

CASCADE_FILE = os.path.join(os.path.dirname(__file__), "../reference/cascade_options.json")

ADDITIONS = {
    "lord_executioner": {
        "death_by_degrees": {
            "name": "Death by Degrees",
            "description": "Attrition before the kill. The Lord Executioner prescribes systematic degradation — degrade across multiple ops, deny the enemy the ability to regenerate, then execute cleanly when nothing remains to resist.",
            "tags": ["suppression", "resilience", "terminus"],
            "chapter_affinity": ["Minotaurs", "Iron Hands", "Marines Malevolent", "Iron Hounds", "Crimson Fists"],
            "node_affinity": ["war_world", "agri_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "scorched_execution": {
            "name": "Scorched Execution",
            "description": "Not one target — one capability. The Lord Executioner removes the enemy's means entirely: logistics, command infrastructure, ritual nodes, breeding chambers. The killing is systemic.",
            "tags": ["suppression", "elimination", "tech", "aggressive"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Dragonspears", "Blood Ravens", "Dark Angels"],
            "node_affinity": ["forge_world", "hive_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "champion_killer": {
            "name": "Champion Killer",
            "description": "The Lord Executioner marks the enemy's finest warrior for immediate destruction. Champions draw attention — kill teams are vectored accordingly, and the enemy loses its warrior-elite in a single focused beat.",
            "tags": ["elimination", "terminus", "aggressive", "faith"],
            "chapter_affinity": ["Space Wolves", "Blood Angels", "Flesh Tearers", "Tempestuous Angels", "Dragonspears", "Minotaurs", "Carmine Blades"],
            "node_affinity": ["war_world", "feral_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "vanishing_blade": {
            "name": "Vanishing Blade",
            "description": "Kill clean. Leave nothing. The Lord Executioner's doctrine: the Watch's presence is felt only in the enemy's absence — stealth, precision, silence. No declaration. Just the dead.",
            "tags": ["stealth", "elimination", "intel", "void"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Necropolis Hawks", "Cowled Wardens"],
            "node_affinity": ["hive_world", "pleasure_world", "dead_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_binding_sentence": {
            "name": "The Binding Sentence",
            "description": "Warp-touched, daemonically influenced, Chaos-tainted — the Lord Executioner issues formal condemning sentence against a designated abomination. Kill teams carry both Imperial authority and the precise methodology to carry it out.",
            "tags": ["void", "terminus", "faith", "elimination"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Tome Keepers", "Blood Ravens", "Cowled Wardens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "faith"],
            "weight": 1.0
        },
        "overkill_mandate": {
            "name": "Overkill Mandate",
            "description": "Sufficiency is insufficient. The Lord Executioner prescribes excess — every asset committed to every target, every strat employed, every kill team that can reach an objective does. Nothing confirmed alive.",
            "tags": ["aggressive", "terminus", "suppression"],
            "chapter_affinity": ["Marines Malevolent", "Minotaurs", "Bleeding Hearts", "Angels of Vengeance", "Angels of Defiance", "Black Templars"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "patience_before_the_strike": {
            "name": "Patience Before the Strike",
            "description": "The Lord Executioner issues a stay of execution — intel runs first, every kill team confirms before committing. The killing doctrine: know everything, then strike once, with everything. No wasted blade.",
            "tags": ["intel", "stealth", "elimination"],
            "chapter_affinity": ["Mentors", "Raptors", "Blood Ravens", "Tome Keepers", "Dark Angels", "Celestial Lions"],
            "node_affinity": ["fortress_world", "watch_station", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "tear_the_throat": {
            "name": "Tear the Throat",
            "description": "Brutal. Immediate. The Lord Executioner identifies the most exposed point and prescribes direct assault without hesitation. Wherever the enemy is weakest, the killing begins there and does not stop.",
            "tags": ["aggressive", "elimination", "resilience", "terminus"],
            "chapter_affinity": ["Space Wolves", "Flesh Tearers", "Tempestuous Angels", "Blood Angels", "Bleeding Hearts", "Carmine Blades"],
            "node_affinity": ["feral_world", "war_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "the_emperors_sentence": {
            "name": "The Emperor's Sentence",
            "description": "Holy authority carried in the blade. The Lord Executioner invokes Imperial sanction — every kill this beat is a formal execution, absolute and righteous. The killing doctrine elevates beyond tactic into theology.",
            "tags": ["faith", "elimination", "terminus", "aggressive"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Tempestuous Angels", "Cowled Wardens", "Angels of Defiance"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "surgical_priority": {
            "name": "Surgical Priority",
            "description": "Minimum expenditure, maximum result. The Lord Executioner names the single kill that changes everything else — one target, precisely identified, precisely removed. The rest of the battlefield resolves by consequence.",
            "tags": ["elimination", "stealth", "intel", "terminus"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Necropolis Hawks", "Dark Angels", "Mentors", "Iron Ravens"],
            "node_affinity": ["hive_world", "fortress_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "forgemaster": {
        "weapon_litany_invoked": {
            "name": "Weapon Litany Invoked",
            "description": "The Forgemaster consecrates the armament of every kill team in the theatre. Weapon spirits stirred, machine souls aligned — this beat, the Watch's weapons are blessed before they fire. More than maintenance; this is preparation as doctrine.",
            "tags": ["tech", "faith", "aggressive"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Iron Hounds", "Blood Angels", "Iron Lords"],
            "node_affinity": ["fortress_world", "shrine_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "forge_the_breach": {
            "name": "Forge the Breach",
            "description": "Technical doctrine of assault. The Forgemaster prepares equipment specifically for breaking fortified positions — melta charges, breaching frames, void-seal cutters. Walls do not stop the Watch.",
            "tags": ["tech", "aggressive", "suppression"],
            "chapter_affinity": ["Crimson Fists", "Iron Hands", "Dragonspears", "Marines Malevolent", "Minotaurs"],
            "node_affinity": ["fortress_world", "mining_world", "hive_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "relic_reclamation_protocol": {
            "name": "Relic Reclamation Protocol",
            "description": "The Forgemaster activates theatre-wide relic-detection doctrine. Every op this beat prioritises recovery of Adeptus Mechanicus artefacts, lost STCs, corrupted machine spirits, and Watch Station legacies.",
            "tags": ["tech", "recovery", "intel", "faith"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Blood Ravens", "Tome Keepers", "Dragonspears", "Dark Angels"],
            "node_affinity": ["dead_world", "forge_world", "watch_station", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "null_the_machines": {
            "name": "Null the Machines",
            "description": "Enemy technological capabilities targeted. Void shields mapped and jammed, Daemon engines identified for priority kill, automata command links disrupted. The Forgemaster turns machine-war doctrine against the enemy's own systems.",
            "tags": ["tech", "suppression", "void", "elimination"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Blood Ravens", "Dark Angels", "Dragonspears"],
            "node_affinity": ["forge_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "tech"],
            "weight": 1.0
        },
        "adaptive_logistics": {
            "name": "Adaptive Logistics",
            "description": "The Forgemaster restructures the theatre's supply architecture on the fly. Kill teams in the field receive technical support tailored to the specific threat encountered — the machine doctrine bends to serve the mission.",
            "tags": ["tech", "resilience", "intel", "recovery"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Celestial Lions", "Dragonspears", "Iron Ravens"],
            "node_affinity": ["frontier_world", "watch_station", "mining_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_iron_covenant": {
            "name": "The Iron Covenant",
            "description": "The Forgemaster declares a covenant with the machine spirits of the entire theatre: weapons will not fail, armour will not break, vehicles will not die until the mission is complete. He backs it with ritual and labour alike.",
            "tags": ["tech", "faith", "resilience", "fortify"],
            "chapter_affinity": ["Iron Hands", "Iron Lords", "Iron Hounds", "Iron Ravens", "Salamanders"],
            "node_affinity": ["fortress_world", "forge_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith", "resilience"],
            "weight": 1.0
        },
        "field_auspex_augmentation": {
            "name": "Field Auspex Augmentation",
            "description": "Theatre-wide auspex enhancement. The Forgemaster upgrades every kill team's scanning and detection capability — nothing hides, no signature is too faint, no threat goes unlocated before kill teams close.",
            "tags": ["tech", "intel", "stealth", "void"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Raptors", "Iron Ravens", "Tome Keepers", "Celestial Lions"],
            "node_affinity": ["dead_world", "watch_station", "hive_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "stealth"],
            "weight": 1.0
        },
        "armour_rites_supreme": {
            "name": "Armour Rites Supreme",
            "description": "Total focus on protection. The Forgemaster prescribes augmented armour rites across the theatre — ceramite blessed, ablative layers reinforced, auto-sanguine systems primed. Kill teams take hits that would shatter lesser warriors.",
            "tags": ["tech", "defensive", "resilience", "fortify"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Iron Lords", "Iron Hounds", "Minotaurs"],
            "node_affinity": ["fortress_world", "war_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive", "resilience"],
            "weight": 1.0
        },
        "corrosive_countermeasures": {
            "name": "Corrosive Countermeasures",
            "description": "The Forgemaster prepares counter-tech specifically for the identified enemy: xenos bio-technology, Heretek corruption, warp-machine hybrids. Kill teams carry specialised weapons and protocols the enemy cannot anticipate.",
            "tags": ["tech", "void", "terminus", "elimination"],
            "chapter_affinity": ["Iron Hands", "Dark Angels", "Blood Ravens", "Tome Keepers", "Dragonspears"],
            "node_affinity": ["dead_world", "forge_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "zero_failure_tolerance": {
            "name": "Zero Failure Tolerance",
            "description": "The Forgemaster implements a theatre-wide equipment audit under war conditions. No degraded weapon, no compromised system goes to the field. The cost is time and labour; the benefit is that nothing breaks at the critical moment.",
            "tags": ["tech", "resilience", "fortify", "faith"],
            "chapter_affinity": ["Iron Hands", "Iron Hounds", "Iron Lords", "Crimson Fists", "Salamanders"],
            "node_affinity": ["fortress_world", "watch_station", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
}

# Add new schema fields to existing options (chapter_affinity, node_affinity, etc.)
SCHEMA_UPDATES = {
    "lord_executioner": {
        "the_headsmans_mark": {
            "chapter_affinity": ["Minotaurs", "Marines Malevolent", "Blood Angels", "Angels of Vengeance", "Dragonspears"],
            "node_affinity": ["war_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "no_quarter_given": {
            "chapter_affinity": ["Marines Malevolent", "Black Templars", "Flesh Tearers", "Bleeding Hearts", "Minotaurs"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "righteous_severance": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Mentors", "Dark Angels", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "fortress_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "forgemaster": {
        "omnissianic_mandate": {
            "chapter_affinity": ["Iron Hands", "Iron Lords", "Iron Ravens", "Dragonspears", "Salamanders"],
            "node_affinity": ["forge_world", "fortress_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "hostile_acquisition": {
            "chapter_affinity": ["Blood Ravens", "Iron Ravens", "Dragonspears", "Mentors", "Tome Keepers"],
            "node_affinity": ["dead_world", "forge_world", "mining_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "recovery"],
            "weight": 1.0
        },
        "iron_resupply": {
            "chapter_affinity": ["Iron Hands", "Iron Hounds", "Crimson Fists", "Iron Lords", "Mentors"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["resilience", "defensive"],
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

for role in ["lord_executioner", "forgemaster"]:
    count = sum(1 for k in data[role] if not k.startswith("_"))
    print(f"{role}: {count} options")
