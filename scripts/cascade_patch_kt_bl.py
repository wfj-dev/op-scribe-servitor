#!/usr/bin/env python3
"""Expand KT + battle-line roles: watch_sergeant, judiciar, dread_angle + personal_focus."""
import json, os

CASCADE_FILE = os.path.join(os.path.dirname(__file__), "../reference/cascade_options.json")

ADDITIONS = {
    "watch_sergeant": {
        "the_killing_oath": {
            "name": "The Killing Oath",
            "description": "The Sergeant names a target and binds the kill team to its destruction by personal oath. Not a tactical objective — a sworn promise made before the team commits. The kill team does not return until the oath is fulfilled.",
            "tags": ["faith", "terminus", "elimination", "aggressive"],
            "chapter_affinity": ["Black Templars", "Space Wolves", "Blood Angels", "Cowled Wardens", "Tempestuous Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "ghost_advance": {
            "name": "Ghost Advance",
            "description": "The Sergeant leads the kill team through without contact until the optimal moment — patience, concealment, and absolute noise discipline until the killing ground is set. The enemy never sees them coming.",
            "tags": ["stealth", "intel", "elimination", "aggressive"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Necropolis Hawks", "Cowled Wardens"],
            "node_affinity": ["feral_world", "hive_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "line_unbroken": {
            "name": "Line Unbroken",
            "description": "The Sergeant establishes a holding position and refuses to yield it. The kill team does not advance and does not fall back — they hold, they endure, they make the enemy pay for every attempt to move them.",
            "tags": ["defensive", "fortify", "resilience", "suppression"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Minotaurs", "Iron Lords", "Iron Hounds"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "coordinated_volley": {
            "name": "Coordinated Volley",
            "description": "Every weapon fires together — the Sergeant coordinates timing so the kill team's fire output is a single combined event rather than individual shots. No target survives a focused volley from a Deathwatch kill team acting as one.",
            "tags": ["aggressive", "suppression", "terminus", "elimination"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Blood Ravens", "Crimson Fists", "Raptors"],
            "node_affinity": ["war_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "brothers_covered": {
            "name": "Brothers Covered",
            "description": "No one goes unsupported. The Sergeant ensures every movement has cover, every advance is screened, every brother who is out in the open has eyes and weapons on the angles that could threaten them. The kill team fights as one organism.",
            "tags": ["defensive", "resilience", "recovery", "stealth"],
            "chapter_affinity": ["Salamanders", "Blood Angels", "Iron Hands", "Raptors", "Celestial Lions"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "contact_imminent": {
            "name": "Contact Imminent",
            "description": "The Sergeant reads the intelligence picture and prepares the kill team for an engagement that is already in motion. No approach phase — straight to the confrontation, from the first moment of deployment. Reaction speed and aggression are the doctrine.",
            "tags": ["aggressive", "terminus", "suppression", "elimination"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Space Wolves", "Marines Malevolent", "Tempestuous Angels"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "by_the_numbers": {
            "name": "By the Numbers",
            "description": "The Sergeant executes the assigned doctrine with textbook precision. No improvisation, no deviation from the plan — every battle-drill executed correctly, every contingency covered. The kill team performs exactly as trained.",
            "tags": ["intel", "resilience", "defensive", "recovery"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Blood Ravens", "Celestial Lions"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_target_marked": {
            "name": "The Target Marked",
            "description": "The Sergeant designates the kill team's primary target before deployment and builds the entire engagement plan around its elimination. Everything else is secondary. The target is the operation.",
            "tags": ["terminus", "elimination", "intel", "void"],
            "chapter_affinity": ["Dark Angels", "Raven Guard", "Necropolis Hawks", "Raptors", "Blood Ravens"],
            "node_affinity": ["fortress_world", "dead_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["terminus"],
            "weight": 1.0
        },
        "void_trained": {
            "name": "Void-Trained",
            "description": "The Sergeant adapts the kill team's doctrine to the specific characteristics of a warp-touched or daemonically influenced environment. Standard engagement protocols modified for what can only be killed by specific means — the kill team is prepared for what standard doctrine cannot address.",
            "tags": ["void", "terminus", "intel", "resilience"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Black Templars"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "make_every_shot_count": {
            "name": "Make Every Shot Count",
            "description": "The Sergeant enforces a doctrine of absolute precision — no ammunition expended without a confirmed target, no kill without maximum effectiveness. The kill team achieves its objectives with fewer shots and more kills.",
            "tags": ["elimination", "intel", "terminus", "suppression"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Mentors", "Iron Hands", "Necropolis Hawks"],
            "node_affinity": ["frontier_world", "agri_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "judiciar": {
        "the_sentence_pronounced": {
            "name": "The Sentence Pronounced",
            "description": "The Judiciar formally pronounces the kill team's mandate as irrevocable judgement — not an operation, not a mission, but a sentence already passed. The kill team executes what has already been decided in the Emperor's name.",
            "tags": ["faith", "terminus", "elimination", "aggressive"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Blood Angels", "Cowled Wardens", "Angels of Defiance"],
            "node_affinity": ["shrine_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.2
        },
        "no_appeal": {
            "name": "No Appeal",
            "description": "The Judiciar removes the possibility of mercy from the kill team's doctrine. This target cannot be spared, cannot be captured, cannot be permitted to survive. The sentence is death and the Judiciar ensures every brother understands there are no alternatives.",
            "tags": ["terminus", "elimination", "faith", "aggressive"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Angels of Vengeance", "Marines Malevolent", "Flesh Tearers"],
            "node_affinity": ["war_world", "dead_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["terminus"],
            "weight": 1.0
        },
        "the_law_applied": {
            "name": "The Law Applied",
            "description": "The Judiciar invokes the specific doctrinal framework — Codex clause, Chapter edict, or Deathwatch standing order — that governs this engagement's permissible methods. Clarity of mandate is the Judiciar's gift; the kill team knows exactly what the law requires.",
            "tags": ["faith", "intel", "defensive", "resilience"],
            "chapter_affinity": ["Dark Angels", "Black Templars", "Crimson Fists", "Celestial Lions", "Tome Keepers"],
            "node_affinity": ["fortress_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "guilt_confirmed": {
            "name": "Guilt Confirmed",
            "description": "The Judiciar has assessed the intelligence and confirmed the target's nature beyond dispute. No wasted operations against misidentified threats — the Judiciar's assessment gives the kill team absolute certainty about what they are killing and why.",
            "tags": ["intel", "terminus", "void", "elimination"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Raptors", "Mentors"],
            "node_affinity": ["fortress_world", "dead_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "tempus_mortis": {
            "name": "Tempus Mortis",
            "description": "The Judiciar's temporal judgement weapon extends its influence — the kill team operates in the shadow of slowed time, striking with certainty while the enemy's reactions drag. The Judiciar manages the field's temporal character to ensure kills land before defences rise.",
            "tags": ["terminus", "elimination", "aggressive", "void"],
            "chapter_affinity": ["Dark Angels", "Blood Angels", "Tome Keepers", "Dragonspears", "Iron Ravens"],
            "node_affinity": ["dead_world", "feral_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "accountability_rendered": {
            "name": "Accountability Rendered",
            "description": "The Judiciar ensures the company's own conduct is beyond reproach. Civilian protocols, rules of engagement, collateral assessment — the kill team operates within the law because the Judiciar holds them to it. The Watch does not become what it fights.",
            "tags": ["intel", "faith", "recovery", "defensive"],
            "chapter_affinity": ["Salamanders", "Celestial Lions", "Tome Keepers", "Blood Angels", "Mentors"],
            "node_affinity": ["shrine_world", "agri_world", "hive_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_old_sentence": {
            "name": "The Old Sentence",
            "description": "The Judiciar invokes a sentence passed long before this beat — an ancient mandate, a founding judgement, a standing order from decades prior that has never been rescinded. Whatever was decreed then governs now, and the Judiciar ensures it is honoured.",
            "tags": ["faith", "terminus", "void", "resilience"],
            "chapter_affinity": ["Dark Angels", "Black Templars", "Tome Keepers", "Cowled Wardens", "Angels of Defiance"],
            "node_affinity": ["dead_world", "watch_station", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "the_blade_consecrated": {
            "name": "The Blade Consecrated",
            "description": "The Judiciar performs rites over the kill team's close-quarters weapons before deployment. Every blade is consecrated to specific use this beat — the kill that the Judiciar has ordained. The weapons are prepared for the work they were made for.",
            "tags": ["faith", "elimination", "aggressive", "terminus"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Dragonspears", "Blood Angels", "Cowled Wardens"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "zero_clemency": {
            "name": "Zero Clemency",
            "description": "The Judiciar has reviewed the files and found no grounds for anything except total elimination. This is not aggression — it is adjudicated certainty. The kill team carries out what the evidence demands with complete moral clarity.",
            "tags": ["terminus", "elimination", "void", "aggressive"],
            "chapter_affinity": ["Marines Malevolent", "Minotaurs", "Dark Angels", "Angels of Vengeance", "Black Templars"],
            "node_affinity": ["dead_world", "feral_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["terminus"],
            "weight": 1.0
        },
        "the_witness_stands": {
            "name": "The Witness Stands",
            "description": "The Judiciar ensures this beat's most significant actions are formally witnessed and entered into the record. Whatever the kill team achieves or fails to achieve, it is attested — the Judiciar's testimony cannot be disputed.",
            "tags": ["faith", "intel", "recovery", "resilience"],
            "chapter_affinity": ["Celestial Lions", "Tome Keepers", "Dark Angels", "Blood Ravens", "Salamanders"],
            "node_affinity": ["fortress_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "dread_angle": {
        "the_silence_before": {
            "name": "The Silence Before",
            "description": "The Dread Angel operates in total quiet before the strike. No contact, no communications, no announcement — and then complete violence from no direction the enemy could identify as the threat's origin. The kill is everything; the approach is nothing.",
            "tags": ["stealth", "elimination", "terminus", "aggressive"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Necropolis Hawks", "Dark Angels", "Cowled Wardens"],
            "node_affinity": ["feral_world", "dead_world", "hive_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.2
        },
        "the_angle_known": {
            "name": "The Angle Known",
            "description": "The Dread Angel scouts the target's position, identifies the approach that no defender is watching, and commits to it at the moment the enemy's attention is elsewhere. Intelligence-driven assassination — not speed, but placement.",
            "tags": ["intel", "stealth", "elimination", "terminus"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Necropolis Hawks", "Mentors"],
            "node_affinity": ["fortress_world", "hive_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "stealth"],
            "weight": 1.0
        },
        "the_target_alone": {
            "name": "The Target Alone",
            "description": "The Dread Angel isolates the primary kill from the protective structure around it. Draw the guards away, collapse their cohesion, create the window — and in that window, the kill. Clean, certain, and impossible to defend against.",
            "tags": ["stealth", "elimination", "intel", "terminus"],
            "chapter_affinity": ["Raven Guard", "Dark Angels", "Raptors", "Blood Ravens", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "pleasure_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "void_touched_hunt": {
            "name": "Void-Touched Hunt",
            "description": "The target is not entirely material. The Dread Angel adapts their approach for a quarry that exists partially in the warp — psyker, daemonhost, or entity that cannot be killed by standard means. The hunt is modified to address what the target actually is.",
            "tags": ["void", "terminus", "elimination", "stealth"],
            "chapter_affinity": ["Dark Angels", "Raven Guard", "Blood Ravens", "Cowled Wardens", "Tome Keepers"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "terminus"],
            "weight": 1.0
        },
        "the_distance_closed": {
            "name": "The Distance Closed",
            "description": "The Dread Angel accepts that the target is defended and plans for the closing movement regardless. Not stealth — direct approach under fire, movement too fast and too controlled for the defenders to effectively engage. The distance closes; the target dies.",
            "tags": ["aggressive", "elimination", "terminus", "resilience"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Raven Guard", "Raptors", "Dragonspears"],
            "node_affinity": ["war_world", "feral_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "the_faith_of_execution": {
            "name": "The Faith of Execution",
            "description": "The kill is a sacred act. The Dread Angel approaches the target as a rite — preparation, consecration, execution performed in the correct sequence for a correct result. Faith informs technique; the Emperor's will guides the blade.",
            "tags": ["faith", "terminus", "elimination", "stealth"],
            "chapter_affinity": ["Black Templars", "Cowled Wardens", "Salamanders", "Dark Angels", "Blood Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "witnessed_only_in_death": {
            "name": "Witnessed Only in Death",
            "description": "The Dread Angel ensures the kill team has no observable presence until the moment of the strike. Every approach angle screened, every signature suppressed, every contingency planned so that the first the enemy knows of the kill team is the last thing they know at all.",
            "tags": ["stealth", "elimination", "intel", "suppression"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Cowled Wardens", "Necropolis Hawks", "Dark Angels"],
            "node_affinity": ["hive_world", "frontier_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "the_blade_sufficient": {
            "name": "The Blade Sufficient",
            "description": "The Dread Angel strips back the kill team's doctrine to a single focused question: is the blade sufficient? Everything reduced to the minimum required to make the kill — no excess, no distraction, no consideration beyond the act and its immediate requirements.",
            "tags": ["terminus", "elimination", "aggressive", "resilience"],
            "chapter_affinity": ["Dragonspears", "Space Wolves", "Blood Angels", "Iron Hands", "Black Templars"],
            "node_affinity": ["fortress_world", "war_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_pattern_broken": {
            "name": "The Pattern Broken",
            "description": "The Dread Angel identifies the defensive routine around the target and acts in the gap between cycles — the moment the guards have just checked a position and won't check it again for a predictable interval. The window exists; the Dread Angel knows its length.",
            "tags": ["stealth", "intel", "elimination", "terminus"],
            "chapter_affinity": ["Raptors", "Raven Guard", "Mentors", "Necropolis Hawks", "Blood Ravens"],
            "node_affinity": ["hive_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "the_xenos_specifics": {
            "name": "The Xenos Specifics",
            "description": "The Dread Angel researches the specific physiological vulnerabilities of the target species. Where it can be killed, how it can be killed, what it cannot survive — the hunt is built around eliminating exactly what the enemy is rather than applying generic doctrine to a specific threat.",
            "tags": ["intel", "terminus", "elimination", "void"],
            "chapter_affinity": ["Raptors", "Mentors", "Blood Ravens", "Tome Keepers", "Celestial Lions"],
            "node_affinity": ["dead_world", "feral_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["terminus"],
            "weight": 1.0
        },
    },
    "personal_focus": {
        "the_enemy_studied": {
            "name": "The Enemy Studied",
            "description": "Before deployment this cycle, dedicate personal study to the specific enemy configuration faced at the current node. Kill zone preference, response patterns, command signals — knowledge that translates directly into kills.",
            "tags": ["intel", "terminus", "void", "elimination"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Raptors", "Tome Keepers", "Celestial Lions"],
            "node_affinity": ["dead_world", "frontier_world", "watch_station"],
            "suppress_if_previous": True,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_armour_maintained": {
            "name": "The Armour Maintained",
            "description": "Perform the full armour rite cycle yourself rather than relying on Techmarine resources. Your ceramite is your responsibility and your investment — maximum integrity going into the cycle's operations.",
            "tags": ["tech", "resilience", "defensive", "recovery"],
            "chapter_affinity": ["Iron Hands", "Iron Lords", "Crimson Fists", "Minotaurs", "Iron Hounds"],
            "node_affinity": ["fortress_world", "mining_world", "forge_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": ["resilience"],
            "weight": 1.0
        },
        "the_blade_honed": {
            "name": "The Blade Honed",
            "description": "Every weapon in your wargear brought to optimal condition by your own hand. Not maintenance — preparation with intent. The blade knows the work it is being prepared for.",
            "tags": ["aggressive", "elimination", "terminus", "faith"],
            "chapter_affinity": ["Space Wolves", "Dragonspears", "Blood Angels", "Black Templars", "Salamanders"],
            "node_affinity": ["war_world", "fortress_world", "feral_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "witness_carried": {
            "name": "Witness Carried",
            "description": "You volunteer to carry the kill team's documentation obligation personally this cycle — every confirmed kill, every significant event, every brother lost. The tally is yours to keep and the record will be accurate.",
            "tags": ["intel", "faith", "recovery", "resilience"],
            "chapter_affinity": ["Celestial Lions", "Tome Keepers", "Blood Ravens", "Salamanders", "Mentors"],
            "node_affinity": ["fortress_world", "shrine_world", "agri_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_oath_specific": {
            "name": "The Oath Specific",
            "description": "You swear a personal oath tied to this cycle's specific objective — not general excellence, but a precise commitment with a concrete success condition. The oath creates accountability; accountability creates performance.",
            "tags": ["faith", "terminus", "resilience", "aggressive"],
            "chapter_affinity": ["Black Templars", "Space Wolves", "Salamanders", "Cowled Wardens", "Dragonspears"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "fire_discipline_perfect": {
            "name": "Fire Discipline Perfect",
            "description": "You hold your own fire until the moment is certain — no wasted ammunition, no suppression that achieves nothing, no engagement without a kill. Your personal output this cycle is entirely composed of kills.",
            "tags": ["elimination", "intel", "terminus", "suppression"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Mentors", "Iron Hands", "Necropolis Hawks"],
            "node_affinity": ["agri_world", "frontier_world", "feral_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_void_endured": {
            "name": "The Void Endured",
            "description": "Exposure to warp influence is acknowledged rather than suppressed. You do not fight what you have absorbed — you hold it, understand it, and ensure it serves the mission rather than compromising you. The void is your territory, not your enemy.",
            "tags": ["void", "resilience", "recovery", "faith"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Iron Ravens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": True,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "silence_as_doctrine": {
            "name": "Silence as Doctrine",
            "description": "You commit to maintaining absolute operational quiet this cycle — no unnecessary transmissions, no announced positions, no communications that do not directly serve the mission. Your personal signature in this operation is zero.",
            "tags": ["stealth", "intel", "elimination", "defensive"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Cowled Wardens", "Necropolis Hawks", "Dark Angels"],
            "node_affinity": ["hive_world", "frontier_world", "feral_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "the_kill_clean": {
            "name": "The Kill Clean",
            "description": "Your focus is singular: one primary target, engaged and eliminated with minimum time and maximum certainty. No secondary engagements distract from the kill. You are the blade for one specific purpose this cycle.",
            "tags": ["terminus", "elimination", "aggressive", "stealth"],
            "chapter_affinity": ["Raven Guard", "Dark Angels", "Raptors", "Necropolis Hawks", "Dragonspears"],
            "node_affinity": ["feral_world", "dead_world", "fortress_world"],
            "suppress_if_previous": True,
            "requires_upstream_tags": ["terminus"],
            "weight": 1.0
        },
    },
}

SCHEMA_UPDATES = {
    "watch_sergeant": {
        "oath_of_moment": {
            "chapter_affinity": ["Black Templars", "Space Wolves", "Blood Angels", "Cowled Wardens", "Tempestuous Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["faith"], "weight": 1.2
        },
        "combined_arms_drill": {
            "chapter_affinity": ["Mentors", "Iron Hands", "Crimson Fists", "Raptors", "Blood Ravens"],
            "node_affinity": ["fortress_world", "agri_world", "war_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "duty_unto_death": {
            "chapter_affinity": ["Iron Hands", "Salamanders", "Crimson Fists", "Blood Angels", "Celestial Lions"],
            "node_affinity": ["war_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["resilience"], "weight": 1.0
        },
    },
    "judiciar": {
        "the_final_verdict": {
            "chapter_affinity": ["Black Templars", "Dark Angels", "Blood Angels", "Cowled Wardens", "Angels of Defiance"],
            "node_affinity": ["shrine_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["faith"], "weight": 1.2
        },
        "the_condemned_marked": {
            "chapter_affinity": ["Dark Angels", "Black Templars", "Angels of Vengeance", "Flesh Tearers", "Marines Malevolent"],
            "node_affinity": ["war_world", "dead_world", "fortress_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["terminus"], "weight": 1.0
        },
        "walk_in_judgement": {
            "chapter_affinity": ["Black Templars", "Salamanders", "Celestial Lions", "Cowled Wardens", "Dark Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
    },
    "dread_angle": {
        "the_killing_ground_set": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Necropolis Hawks", "Dark Angels", "Cowled Wardens"],
            "node_affinity": ["feral_world", "dead_world", "hive_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["stealth"], "weight": 1.2
        },
        "unannounced": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Cowled Wardens", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "feral_world", "frontier_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["stealth", "elimination"], "weight": 1.0
        },
        "the_hunt_joined": {
            "chapter_affinity": ["Space Wolves", "Flesh Tearers", "Blood Angels", "Raven Guard", "Raptors"],
            "node_affinity": ["feral_world", "dead_world", "war_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["terminus"], "weight": 1.0
        },
    },
    "personal_focus": {
        "personal_excellence": {
            "chapter_affinity": ["Mentors", "Raptors", "Blood Ravens", "Celestial Lions", "Tome Keepers"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": True, "requires_upstream_tags": [], "weight": 1.0
        },
        "the_chapter_honoured": {
            "chapter_affinity": ["Space Wolves", "Blood Angels", "Salamanders", "Black Templars", "Iron Hands"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": True, "requires_upstream_tags": ["faith"], "weight": 1.0
        },
        "the_long_watch": {
            "chapter_affinity": ["Dark Angels", "Raven Guard", "Raptors", "Mentors", "Tome Keepers"],
            "node_affinity": ["watch_station", "dead_world", "frontier_world"],
            "suppress_if_previous": True, "requires_upstream_tags": [], "weight": 1.0
        },
        "nothing_left_behind": {
            "chapter_affinity": ["Salamanders", "Blood Angels", "Celestial Lions", "Raptors", "Iron Hands"],
            "node_affinity": ["agri_world", "fortress_world", "war_world"],
            "suppress_if_previous": True, "requires_upstream_tags": [], "weight": 1.0
        },
        "the_mark_earned": {
            "chapter_affinity": ["Space Wolves", "Dragonspears", "Carmine Blades", "Blood Angels", "Tempestuous Angels"],
            "node_affinity": ["war_world", "feral_world", "fortress_world"],
            "suppress_if_previous": True, "requires_upstream_tags": [], "weight": 1.0
        },
        "kill_team_first": {
            "chapter_affinity": ["Salamanders", "Iron Hands", "Celestial Lions", "Raptors", "Blood Angels"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": True, "requires_upstream_tags": ["resilience"], "weight": 1.0
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

for role in ["watch_sergeant", "judiciar", "dread_angle", "personal_focus"]:
    count = sum(1 for k in data[role] if not k.startswith("_"))
    print(f"{role}: {count} options")
