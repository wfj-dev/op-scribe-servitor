#!/usr/bin/env python3
"""Add option pool expansions for huntmaster, void_warden, and castellan."""
import json, os

CASCADE_FILE = os.path.join(os.path.dirname(__file__), "../reference/cascade_options.json")

ADDITIONS = {
    "huntmaster": {
        "corner_the_prey": {
            "name": "Corner the Prey",
            "description": "Multiple kill teams converge from every available approach, denying the prey any route of withdrawal. The Huntmaster doesn't chase — he closes every exit and waits for the inevitable.",
            "tags": ["terminus", "suppression", "intel", "aggressive"],
            "chapter_affinity": ["Space Wolves", "Blood Angels", "Flesh Tearers", "Raptors", "Dark Angels"],
            "node_affinity": ["fortress_world", "hive_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "read_the_spoor": {
            "name": "Read the Spoor",
            "description": "Intelligence phase runs before the hunt begins. The Huntmaster maps the prey's movement patterns, feeding routes, and lair locations — kill teams deploy knowing exactly where the kill will happen.",
            "tags": ["terminus", "intel", "stealth", "void"],
            "chapter_affinity": ["Space Wolves", "Raven Guard", "Raptors", "Blood Ravens", "Mentors"],
            "node_affinity": ["dead_world", "feral_world", "frontier_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "running_the_beast_to_ground": {
            "name": "Running the Beast to Ground",
            "description": "Relentless pursuit doctrine. The Huntmaster will not let the prey rest, resupply, or regenerate. Kill teams rotate, maintain constant pressure, and drive the terminus quarry past the point of recovery.",
            "tags": ["terminus", "aggressive", "resilience", "suppression"],
            "chapter_affinity": ["Space Wolves", "Flesh Tearers", "Blood Angels", "Carmine Blades", "Bleeding Hearts"],
            "node_affinity": ["feral_world", "war_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "the_killing_ground": {
            "name": "The Killing Ground",
            "description": "The Huntmaster prepares the terrain before the prey arrives. Kill zones established, approach vectors seeded with covered positions — the terminus target walks into a battlefield already set for its death.",
            "tags": ["terminus", "stealth", "intel", "fortify"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Necropolis Hawks", "Mentors"],
            "node_affinity": ["dead_world", "feral_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth", "intel"],
            "weight": 1.0
        },
        "feast_on_the_worthy": {
            "name": "Feast on the Worthy",
            "description": "The Huntmaster opens the hunt to all kill teams. No single designated quarry — whatever terminus-class threat presents, it dies. The watch-list is the whole battlefield.",
            "tags": ["terminus", "aggressive", "elimination", "faith"],
            "chapter_affinity": ["Space Wolves", "Blood Angels", "Black Templars", "Minotaurs", "Dragonspears"],
            "node_affinity": ["war_world", "dead_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "deny_the_warlord": {
            "name": "Deny the Warlord",
            "description": "The Huntmaster's priority: command decapitation. Remove the entity that commands, coordinates, or empowers the enemy force. The kill is not the terminus creature — it is the leadership that makes the enemy coherent.",
            "tags": ["terminus", "elimination", "intel", "stealth"],
            "chapter_affinity": ["Dark Angels", "Raven Guard", "Raptors", "Necropolis Hawks", "Blood Ravens"],
            "node_affinity": ["fortress_world", "hive_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "void_hunt": {
            "name": "Void Hunt",
            "description": "The prey is not physical. The Huntmaster turns his doctrine toward warp-entities, psyker-constructs, and void-beasts that register on the terminus threat scale. Hunting doctrine adapted for prey that bleeds warp-fire.",
            "tags": ["terminus", "void", "elimination", "intel"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Cowled Wardens", "Mentors"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "the_long_hunt": {
            "name": "The Long Hunt",
            "description": "The Huntmaster extends the hunt beyond this beat. Terminus prey marked this cycle, intelligence gathered, approach angles confirmed — even if it escapes now, the next beat the Watch knows exactly where it will be.",
            "tags": ["terminus", "intel", "resilience", "recovery"],
            "chapter_affinity": ["Space Wolves", "Raptors", "Mentors", "Celestial Lions", "Iron Hounds"],
            "node_affinity": ["frontier_world", "agri_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "sanctified_kill": {
            "name": "Sanctified Kill",
            "description": "The Huntmaster declares the terminus prey a spiritual abomination and consecrates the kill in advance. Kill teams carry both tactical doctrine and chaplaincy authority. The target is hunted for the Watch's soul as much as its mission.",
            "tags": ["terminus", "faith", "elimination", "void"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Space Wolves", "Blood Angels"],
            "node_affinity": ["shrine_world", "dead_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "maximum_yield": {
            "name": "Maximum Yield",
            "description": "Every kill team capable of engaging a terminus target this beat is cleared to do so. The Huntmaster abandons exclusivity — this is not a precision hunt. It is a coordinated annihilation.",
            "tags": ["terminus", "aggressive", "suppression", "resilience"],
            "chapter_affinity": ["Marines Malevolent", "Minotaurs", "Bleeding Hearts", "Angels of Vengeance", "Black Templars"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
    },
    "void_warden": {
        "silence_the_choir": {
            "name": "Silence the Choir",
            "description": "Enemy psyker networks identified and targeted for suppression. The Void Warden coordinates theatre-wide psychic denial operations — no enemy choir sings in concert this beat, every summoning disrupted before it manifests.",
            "tags": ["void", "suppression", "elimination", "intel"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Cowled Wardens", "Mentors"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "warp_cartography": {
            "name": "Warp Cartography",
            "description": "The Void Warden maps the warp-topology of the committed node. Rift locations, psychic resonance points, intrusion vectors — kill teams move with full awareness of where the immaterium bleeds into real-space.",
            "tags": ["void", "intel", "stealth", "defensive"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Iron Ravens", "Celestial Lions"],
            "node_affinity": ["dead_world", "watch_station", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "the_emperors_shield": {
            "name": "The Emperor's Shield",
            "description": "The Void Warden raises a psychic fortress across the theatre. Daemons find no purchase, summoning rituals collapse before completion, the warp's influence on this operation is reduced to near-nothing. Absolute psychic defence.",
            "tags": ["void", "defensive", "faith", "fortify"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Cowled Wardens", "Blood Ravens", "Salamanders"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive", "faith"],
            "weight": 1.0
        },
        "forge_a_weapon": {
            "name": "Forge a Weapon",
            "description": "The Void Warden identifies a psyker of value among the Watch and focuses theatre-level resources on one combined strike through the warp. The enemy's psychic strength becomes the kill team's targeting solution.",
            "tags": ["void", "aggressive", "terminus", "elimination"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Tempestuous Angels", "Angels of Vengeance"],
            "node_affinity": ["dead_world", "watch_station", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive", "terminus"],
            "weight": 1.0
        },
        "deep_warp_surveillance": {
            "name": "Deep Warp Surveillance",
            "description": "The Void Warden extends psychic intelligence assets into the deep warp. Enemy reinforcement vectors, daemon-world coordinates, immaterial staging points — the Watch knows what is coming before it arrives in real-space.",
            "tags": ["void", "intel", "stealth", "resilience"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Iron Ravens", "Mentors", "Cowled Wardens"],
            "node_affinity": ["watch_station", "dead_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "the_veil_holds": {
            "name": "The Veil Holds",
            "description": "The Void Warden commits every psychic asset to maintaining the integrity of real-space on the committed node. No rift, no summoning, no warp-intrusion succeeds while the Chief Librarian stands the watch.",
            "tags": ["void", "defensive", "resilience", "suppression"],
            "chapter_affinity": ["Dark Angels", "Cowled Wardens", "Tome Keepers", "Blood Ravens", "Iron Hands"],
            "node_affinity": ["dead_world", "shrine_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "psychic_decapitation": {
            "name": "Psychic Decapitation",
            "description": "One psychic threat of command-class magnitude identified and targeted for elimination. The Void Warden directs all theatre psychic assets toward a single entity — remove the mind that directs the enemy's warp capability.",
            "tags": ["void", "elimination", "terminus", "intel"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Necropolis Hawks", "Cowled Wardens"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "resonance_reading": {
            "name": "Resonance Reading",
            "description": "The Void Warden reads the psychic resonance of the committed node's recent history. Atrocities, rites, mass deaths — all leave warp-signatures the Chief Librarian translates into tactical intelligence for the kill teams.",
            "tags": ["void", "intel", "recovery", "faith"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Celestial Lions", "Iron Ravens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "faith"],
            "weight": 1.0
        },
        "warp_weaponisation": {
            "name": "Warp Weaponisation",
            "description": "The Void Warden turns the enemy's own warp signature against it. Psychic doctrine of aggression — every rift, every daemonic presence, every warp-taint on the node is identified and weaponised by the Watch's Librarians.",
            "tags": ["void", "aggressive", "suppression", "terminus"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Tempestuous Angels", "Dark Angels", "Angels of Defiance"],
            "node_affinity": ["dead_world", "watch_station", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "null_the_prophet": {
            "name": "Null the Prophet",
            "description": "Enemy morale relies on their warp-seer, daemon-speaker, or prophet-demagogue. The Void Warden designates that individual for psychic nullification — silence the voice, and the enemy's certainty collapses with it.",
            "tags": ["void", "suppression", "elimination", "stealth"],
            "chapter_affinity": ["Dark Angels", "Raven Guard", "Raptors", "Blood Ravens", "Cowled Wardens"],
            "node_affinity": ["dead_world", "hive_world", "penal_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "castellan": {
        "eyes_everywhere": {
            "name": "Eyes Everywhere",
            "description": "The Castellan deploys a network of surveillance assets across the committed node before the ops window opens. No enemy movement, no reinforcement, no retreat goes unobserved by the Watch's intelligence infrastructure.",
            "tags": ["intel", "stealth", "void", "resilience"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Mentors", "Blood Ravens", "Celestial Lions", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "fortress_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "cut_the_signal": {
            "name": "Cut the Signal",
            "description": "Enemy communications and command-net targeted for systematic disruption. The Castellan's doctrine: before the kill teams move, the enemy cannot coordinate, cannot reinforce, cannot even confirm to their commanders that the Watch has arrived.",
            "tags": ["intel", "suppression", "stealth", "tech"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Iron Ravens", "Mentors", "Dragonspears"],
            "node_affinity": ["hive_world", "fortress_world", "forge_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "stealth"],
            "weight": 1.0
        },
        "the_prepared_ground": {
            "name": "The Prepared Ground",
            "description": "Intelligence gathered. Contingencies placed. The Castellan has pre-positioned assets on the committed node before the cascade began — safe houses, observation points, cached equipment. The Watch arrives to a battlefield it already understands.",
            "tags": ["intel", "stealth", "aggressive", "resilience"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Mentors", "Cowled Wardens", "Necrophos Hawks"],
            "node_affinity": ["hive_world", "fortress_world", "frontier_world", "pleasure_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "dossier_protocol": {
            "name": "Dossier Protocol",
            "description": "Every identified hostile of interest receives a full threat dossier before kill teams deploy. Command structures, movement patterns, personal vulnerabilities — the Watch goes in with knowledge that should be impossible to have.",
            "tags": ["intel", "elimination", "stealth", "void"],
            "chapter_affinity": ["Mentors", "Blood Ravens", "Tome Keepers", "Raptors", "Dark Angels"],
            "node_affinity": ["fortress_world", "hive_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "counterintelligence_lockdown": {
            "name": "Counterintelligence Lockdown",
            "description": "The Castellan declares total information denial across the theatre. Enemy intelligence assets are identified, traced, and neutralised. The Watch operates in total secrecy — the enemy fights blind while the Watch sees everything.",
            "tags": ["intel", "defensive", "stealth", "suppression"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Cowled Wardens", "Necropolis Hawks"],
            "node_affinity": ["fortress_world", "hive_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "first_light_analysis": {
            "name": "First Light Analysis",
            "description": "The Castellan compiles a full strategic assessment of the committed node — pressure vectors, population centres of interest, terrain exploitation opportunities, enemy order-of-battle. Kill teams receive it before first contact.",
            "tags": ["intel", "defensive", "resilience", "tech"],
            "chapter_affinity": ["Mentors", "Blood Ravens", "Celestial Lions", "Tome Keepers", "Raptors"],
            "node_affinity": ["fortress_world", "hive_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "ghost_protocol": {
            "name": "Ghost Protocol",
            "description": "The Castellan erases the Watch's operational signature entirely. No record of deployment, no confirmed presence, no intelligence trail. What happened on this node this beat will never be attributable to the Watch.",
            "tags": ["stealth", "intel", "void", "recovery"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Cowled Wardens", "Necropolis Hawks", "Dark Angels"],
            "node_affinity": ["pleasure_world", "hive_world", "penal_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "threat_escalation_report": {
            "name": "Threat Escalation Report",
            "description": "The Castellan compiles and disseminates intelligence confirming this node's threat has escalated beyond previous assessments. Kill teams go in with accurate expectations — no false confidence, no operational shocks.",
            "tags": ["intel", "resilience", "terminus", "void"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Tome Keepers", "Celestial Lions", "Raptors"],
            "node_affinity": ["dead_world", "fortress_world", "war_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "asset_recovery_mandate": {
            "name": "Asset Recovery Mandate",
            "description": "The Castellan identifies intelligence assets — agents, vaults, cogitator networks — on the committed node that must be recovered or destroyed before the enemy can exploit them. Kill teams operate with a dual mission.",
            "tags": ["intel", "recovery", "stealth", "aggressive"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Blood Ravens", "Mentors", "Dark Angels"],
            "node_affinity": ["hive_world", "fortress_world", "watch_station", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["recovery"],
            "weight": 1.0
        },
        "know_your_enemy": {
            "name": "Know Your Enemy",
            "description": "The Castellan prepares a complete xenological or tactical brief on the identified enemy — biology, psychology, command doctrine, known weaknesses. Kill teams fight what they understand, and understanding is the first step to killing.",
            "tags": ["intel", "void", "tech", "terminus"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Tome Keepers", "Iron Ravens", "Celestial Lions"],
            "node_affinity": ["dead_world", "fortress_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
}

SCHEMA_UPDATES = {
    "huntmaster": {
        "mark_the_apex": {
            "chapter_affinity": ["Space Wolves", "Dark Angels", "Minotaurs", "Blood Angels", "Dragonspears"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "hunt_in_packs": {
            "chapter_affinity": ["Space Wolves", "Flesh Tearers", "Blood Angels", "Raptors", "Iron Hounds"],
            "node_affinity": ["feral_world", "war_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "bleed_the_beast": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Iron Hands", "Mentors", "Marines Malevolent"],
            "node_affinity": ["agri_world", "fortress_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["suppression", "resilience"],
            "weight": 1.0
        },
    },
    "void_warden": {
        "warp_interdiction": {
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Iron Hands"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "prescient_command": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Mentors", "Raptors", "Dark Angels"],
            "node_affinity": ["watch_station", "fortress_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "void_condemnation": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Tempestuous Angels", "Cowled Wardens"],
            "node_affinity": ["dead_world", "watch_station", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["terminus", "void"],
            "weight": 1.0
        },
    },
    "castellan": {
        "theatre_intelligence": {
            "chapter_affinity": ["Mentors", "Raptors", "Blood Ravens", "Celestial Lions", "Tome Keepers"],
            "node_affinity": ["fortress_world", "hive_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "black_operations": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Necropolis Hawks", "Cowled Wardens"],
            "node_affinity": ["hive_world", "pleasure_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth", "intel"],
            "weight": 1.0
        },
        "seal_the_reach": {
            "chapter_affinity": ["Dark Angels", "Cowled Wardens", "Iron Hands", "Crimson Fists", "Raptors"],
            "node_affinity": ["fortress_world", "watch_station", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
    },
}

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

for role in ["huntmaster", "void_warden", "castellan"]:
    count = sum(1 for k in data[role] if not k.startswith("_"))
    print(f"{role}: {count} options")
