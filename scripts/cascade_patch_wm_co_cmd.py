#!/usr/bin/env python3
"""Expand watch_master pool + watch_captain, watch_lieutenant, company_champion."""
import json, os

CASCADE_FILE = os.path.join(os.path.dirname(__file__), "../reference/cascade_options.json")

ADDITIONS = {
    "watch_master": {
        "burn_the_ground": {
            "name": "Burn the Ground",
            "description": "Deny the enemy any advantage from held terrain. What cannot be claimed by the Watch is destroyed — the Huntmaster does not permit the enemy to inherit a useful battlefield.",
            "tags": ["suppression", "aggressive", "fortify"],
            "chapter_affinity": ["Minotaurs", "Marines Malevolent", "Black Templars", "Flesh Tearers", "Angels of Vengeance"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "strike_and_withdraw": {
            "name": "Strike and Withdraw",
            "description": "Hit fast, extract before commitment, strike again from a different angle. Tempo is the weapon — kill teams never become the fixed point the enemy can mass against.",
            "tags": ["aggressive", "stealth", "resilience"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Celestial Lions", "Carmine Blades", "Necropolis Hawks"],
            "node_affinity": ["frontier_world", "hive_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "lay_the_ambush": {
            "name": "Lay the Ambush",
            "description": "The Watch does not reveal itself until the moment is perfect. Intelligence gathers, positions are set, kill teams wait — the enemy walks into ground that has already been prepared for their death.",
            "tags": ["stealth", "intel", "elimination"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Cowled Wardens", "Necropolis Hawks"],
            "node_affinity": ["feral_world", "mining_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "claim_the_high_ground": {
            "name": "Claim the High Ground",
            "description": "Strategic position secured before operations begin. The Watch chooses the terrain and the enemy must come to it — position is force, and the Watch will not give it up once taken.",
            "tags": ["fortify", "defensive", "suppression"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Iron Hounds", "Minotaurs", "Iron Lords"],
            "node_affinity": ["fortress_world", "mining_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "the_long_campaign": {
            "name": "The Long Campaign",
            "description": "Not this battle — the war. Every engagement this beat feeds intelligence, every kill team preserves strength, every position taken with an eye to what comes next cycle. Strategic patience is the posture.",
            "tags": ["intel", "resilience", "recovery", "defensive"],
            "chapter_affinity": ["Mentors", "Raptors", "Blood Ravens", "Tome Keepers", "Celestial Lions"],
            "node_affinity": ["fortress_world", "watch_station", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
    },
    "watch_captain": {
        "forward_without_pause": {
            "name": "Forward Without Pause",
            "description": "No hesitation, no consolidation between objectives. The company drives through, takes each gain as a staging point for the next push, and denies the enemy any moment to regroup.",
            "tags": ["aggressive", "terminus", "suppression"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Black Templars", "Tempestuous Angels", "Bleeding Hearts"],
            "node_affinity": ["war_world", "feral_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "command_the_flanks": {
            "name": "Command the Flanks",
            "description": "The Captain opens secondary approaches. Kill teams operate on vectors the enemy cannot defend simultaneously — not a frontal assault, but a company-wide pressure that comes from every direction at once.",
            "tags": ["stealth", "intel", "aggressive", "suppression"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Mentors", "Celestial Lions", "Carmine Blades"],
            "node_affinity": ["fortress_world", "hive_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "stealth"],
            "weight": 1.0
        },
        "iron_line": {
            "name": "Iron Line",
            "description": "The company holds. The Captain establishes a defensible perimeter and refuses to yield it — every kill team anchored to defined positions, the line maintained regardless of pressure.",
            "tags": ["defensive", "fortify", "resilience", "suppression"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Minotaurs", "Iron Lords", "Iron Hounds"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "coordinated_strike": {
            "name": "Coordinated Strike",
            "description": "Every kill team in the company hits simultaneously. The Captain synchronises operations so the enemy cannot respond to one threat without exposing themselves to another — pressure is total, response is impossible.",
            "tags": ["aggressive", "elimination", "intel", "terminus"],
            "chapter_affinity": ["Mentors", "Blood Ravens", "Raptors", "Dark Angels", "Space Wolves"],
            "node_affinity": ["fortress_world", "war_world", "hive_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "preserve_combat_power": {
            "name": "Preserve Combat Power",
            "description": "The Captain prioritises the company's longevity over short-term gains. Kill teams take objectives with minimal exposure, avoid attritional engagements, and return stronger than they left. Efficiency is the mandate.",
            "tags": ["defensive", "resilience", "recovery", "intel"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Celestial Lions", "Tome Keepers"],
            "node_affinity": ["frontier_world", "agri_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "faith_and_steel": {
            "name": "Faith and Steel",
            "description": "The Captain declares the company's purpose in the Emperor's name. Tactical objectives become sacred duty — every kill team fights with the combined weight of doctrine and devotion.",
            "tags": ["faith", "aggressive", "resilience", "terminus"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Blood Angels", "Tempestuous Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "punish_overextension": {
            "name": "Punish Overextension",
            "description": "The Captain watches for the enemy to commit too far and hits them in the moment of their extension. Every kill team ready to respond when the enemy shows their flank — reactive aggression at company scale.",
            "tags": ["defensive", "elimination", "intel", "aggressive"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Dark Angels", "Necropolis Hawks"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_company_endures": {
            "name": "The Company Endures",
            "description": "Whatever this beat costs, the company will be stronger at the end of it. Recovery, sustainability, maintaining the force — the Captain's mandate is not the kill, but the company's operational continuity.",
            "tags": ["resilience", "recovery", "defensive", "faith"],
            "chapter_affinity": ["Salamanders", "Blood Angels", "Celestial Lions", "Iron Hands", "Crimson Fists"],
            "node_affinity": ["agri_world", "fortress_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["resilience"],
            "weight": 1.0
        },
        "machine_supported_advance": {
            "name": "Machine-Supported Advance",
            "description": "The Captain incorporates the company's technological assets as the advance's backbone. Techmarine support, auspex coverage, pre-cleared kill zones — the company advances behind an intelligence and mechanical superiority the enemy cannot match.",
            "tags": ["tech", "aggressive", "intel", "resilience"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Dragonspears", "Mentors", "Blood Ravens"],
            "node_affinity": ["forge_world", "fortress_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["tech"],
            "weight": 1.0
        },
        "silent_dominion": {
            "name": "Silent Dominion",
            "description": "The company operates without announcement. Objectives taken, threats neutralised, positions secured — all without the enemy knowing who is responsible or where the Watch will strike next.",
            "tags": ["stealth", "intel", "elimination", "recovery"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Cowled Wardens", "Dark Angels", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "pleasure_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
    },
    "watch_lieutenant": {
        "find_the_seam": {
            "name": "Find the Seam",
            "description": "The Lieutenant hunts the gap between the enemy's defensive sectors — the point where two units' responsibilities meet and neither owns. Kill teams are pushed through it before it closes.",
            "tags": ["stealth", "intel", "aggressive", "elimination"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Mentors", "Necropolis Hawks", "Dark Angels"],
            "node_affinity": ["fortress_world", "hive_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel", "stealth"],
            "weight": 1.0
        },
        "maximum_pressure": {
            "name": "Maximum Pressure",
            "description": "The Lieutenant pushes every kill team to their operational limit simultaneously. No rotation, no reserve — maximum output for the entire ops window. The enemy runs dry before the Watch does.",
            "tags": ["aggressive", "suppression", "terminus", "resilience"],
            "chapter_affinity": ["Marines Malevolent", "Minotaurs", "Black Templars", "Bleeding Hearts", "Angels of Vengeance"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "anchor_and_exploit": {
            "name": "Anchor and Exploit",
            "description": "One kill team holds the enemy's attention; the others exploit what that engagement opens. The Lieutenant uses the Captain's line as a lever — holding and striking simultaneously from positions the enemy cannot address at once.",
            "tags": ["defensive", "aggressive", "intel", "elimination"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Space Wolves", "Blood Angels", "Mentors"],
            "node_affinity": ["fortress_world", "war_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "hardened_advance": {
            "name": "Hardened Advance",
            "description": "The Lieutenant's kill teams advance with armour rites fresh and positions covered. Every gain is secured before the next movement. Slow, methodical, and absolutely impossible to roll back.",
            "tags": ["defensive", "fortify", "resilience", "aggressive"],
            "chapter_affinity": ["Iron Hands", "Iron Lords", "Crimson Fists", "Minotaurs", "Iron Hounds"],
            "node_affinity": ["fortress_world", "mining_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive", "resilience"],
            "weight": 1.0
        },
        "lightning_exploitation": {
            "name": "Lightning Exploitation",
            "description": "When the Captain's assault creates a breach, the Lieutenant drives kill teams through it before the enemy can seal it. Speed is everything — the exploitation is faster than the enemy's reaction time.",
            "tags": ["aggressive", "stealth", "elimination", "terminus"],
            "chapter_affinity": ["Blood Angels", "Raven Guard", "Flesh Tearers", "Carmine Blades", "Raptors"],
            "node_affinity": ["war_world", "feral_world", "hive_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "watch_the_voids": {
            "name": "Watch the Voids",
            "description": "The Lieutenant assigns kill teams to the sectors the Captain's main directive does not fully cover. Every gap is closed, every approach denied, every blind spot covered. The Captain attacks; the Lieutenant ensures nothing attacks back.",
            "tags": ["defensive", "intel", "resilience", "suppression"],
            "chapter_affinity": ["Iron Hands", "Raptors", "Mentors", "Celestial Lions", "Iron Hounds"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "bleed_them_white": {
            "name": "Bleed Them White",
            "description": "The Lieutenant directs a sustained attrition campaign on the enemy's secondary elements. Not the decisive kill — the slow drain. Every op this beat degrades the enemy's reinforcement pool and its ability to sustain the fight.",
            "tags": ["suppression", "resilience", "terminus", "aggressive"],
            "chapter_affinity": ["Minotaurs", "Iron Hands", "Marines Malevolent", "Crimson Fists", "Iron Lords"],
            "node_affinity": ["war_world", "agri_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_killing_tempo": {
            "name": "The Killing Tempo",
            "description": "The Lieutenant sets an operational rhythm the enemy cannot match. Kill teams cycle in and out at a pace that denies the enemy any window to respond, reorganise, or recover. Tempo is the weapon.",
            "tags": ["aggressive", "suppression", "resilience", "elimination"],
            "chapter_affinity": ["Raptors", "Blood Angels", "Mentors", "Space Wolves", "Celestial Lions"],
            "node_affinity": ["war_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_dark_work": {
            "name": "The Dark Work",
            "description": "Secondary objectives that the official record does not acknowledge. The Lieutenant ensures certain targets are addressed quietly — evidence secured, witnesses removed, assets recovered — without the Captain's broader mandate needing to account for them.",
            "tags": ["stealth", "intel", "elimination", "recovery"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Cowled Wardens", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "pleasure_world", "penal_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "sacred_deployment": {
            "name": "Sacred Deployment",
            "description": "The Lieutenant places kill teams with chaplaincy blessing before commitment — each team consecrated for the specific threat they face. Faith becomes tactical doctrine: the right brothers, prepared in the right way, for the right enemy.",
            "tags": ["faith", "resilience", "aggressive", "recovery"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Blood Angels", "Cowled Wardens", "Space Wolves"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
    },
    "company_champion": {
        "the_open_challenge": {
            "name": "The Open Challenge",
            "description": "The Champion steps forward — the enemy's finest is identified and challenged before the kill teams engage. Draw out the apex threat, force the enemy to commit their strongest, then remove it. The rest is cleanup.",
            "tags": ["elimination", "terminus", "aggressive", "faith"],
            "chapter_affinity": ["Space Wolves", "Blood Angels", "Dragonspears", "Tempestuous Angels", "Minotaurs"],
            "node_affinity": ["war_world", "feral_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "shatter_the_spine": {
            "name": "Shatter the Spine",
            "description": "The Champion targets the command structure directly. Not the warriors — the officers, the leaders, the communications nodes. Remove what coordinates the enemy and the body falls apart.",
            "tags": ["elimination", "intel", "stealth", "suppression"],
            "chapter_affinity": ["Dark Angels", "Raven Guard", "Raptors", "Necropolis Hawks", "Blood Ravens"],
            "node_affinity": ["fortress_world", "hive_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_rite_of_killing": {
            "name": "The Rite of Killing",
            "description": "Every death is a rite performed correctly. The Champion prescribes a killing doctrine built on precision and ceremony — each kill made in the manner that honours the Chapter's traditions and demonstrates the Watch's authority over this battlefield.",
            "tags": ["elimination", "faith", "stealth", "terminus"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Dark Angels", "Blood Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "force_the_error": {
            "name": "Force the Error",
            "description": "Patience. The Champion applies calculated pressure until the enemy makes a mistake — overcommits, exposes a flank, breaks formation. When the error comes, the killing stroke is already in motion.",
            "tags": ["intel", "elimination", "suppression", "defensive"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Blood Ravens", "Dark Angels"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_red_work": {
            "name": "The Red Work",
            "description": "No elegance, no doctrine, no pretension. The Champion sets a killing mandate of pure, sustained violence — maximise enemy dead, minimise Watch casualties, by any means required. The field is cleared.",
            "tags": ["aggressive", "terminus", "suppression", "elimination"],
            "chapter_affinity": ["Flesh Tearers", "Marines Malevolent", "Bleeding Hearts", "Minotaurs", "Carmine Blades"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "witness_and_record": {
            "name": "Witness and Record",
            "description": "The Champion ensures every kill made by this company is acknowledged, documented, and reported. Excellence is not merely achieved — it is proven. The company's kills become the campaign record.",
            "tags": ["elimination", "intel", "faith", "recovery"],
            "chapter_affinity": ["Blood Ravens", "Celestial Lions", "Tome Keepers", "Salamanders", "Mentors"],
            "node_affinity": ["fortress_world", "shrine_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "void_touched_quarry": {
            "name": "Void-Touched Quarry",
            "description": "The Champion designates a warp-corrupted or daemon-possessed target as the company's primary kill. Fighting the warp requires different methodology — the Champion provides the execution doctrine for what cannot be killed by ordinary means.",
            "tags": ["void", "terminus", "faith", "elimination"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Tome Keepers", "Blood Ravens", "Cowled Wardens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "blademaster_doctrine": {
            "name": "Blademaster Doctrine",
            "description": "The Champion introduces a formal melee-excellence mandate for the company. Kill teams are evaluated on close-quarters performance — those who meet the Champion's standard receive his personal commendation and the doctrine intensifies around their example.",
            "tags": ["elimination", "aggressive", "faith", "terminus"],
            "chapter_affinity": ["Dragonspears", "Blood Angels", "Space Wolves", "Black Templars", "Tempestuous Angels"],
            "node_affinity": ["war_world", "fortress_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "cut_the_sinew": {
            "name": "Cut the Sinew",
            "description": "The Champion targets the connections that make the enemy dangerous — the bonds between units, the supply links, the communication threads. Remove the sinew and the musculature of the enemy force tears itself apart.",
            "tags": ["suppression", "elimination", "intel", "stealth"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Iron Hands", "Dark Angels", "Mentors"],
            "node_affinity": ["fortress_world", "hive_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "unbreakable_execution": {
            "name": "Unbreakable Execution",
            "description": "The Champion holds the company's killing doctrine immovable in the face of whatever the enemy brings. Casualties do not alter the mandate. Setbacks do not change the method. The company kills the same way, regardless of cost.",
            "tags": ["elimination", "resilience", "faith", "aggressive"],
            "chapter_affinity": ["Black Templars", "Crimson Fists", "Iron Hands", "Angels of Defiance", "Minotaurs"],
            "node_affinity": ["fortress_world", "war_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["resilience"],
            "weight": 1.0
        },
    },
}

SCHEMA_UPDATES = {
    "watch_master": {
        "advance_the_spear": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Space Wolves", "Black Templars", "Tempestuous Angels", "Bleeding Hearts"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "iron_vigil": {
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Iron Hounds", "Minotaurs", "Iron Lords"],
            "node_affinity": ["fortress_world", "mining_world", "agri_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "scour_the_reach": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Raven Guard", "Raptors", "Mentors"],
            "node_affinity": ["dead_world", "watch_station", "frontier_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "sanctify_the_ground": {
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Blood Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "sever_the_cord": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Necropolis Hawks"],
            "node_affinity": ["hive_world", "pleasure_world", "penal_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "deny_the_tide": {
            "chapter_affinity": ["Minotaurs", "Marines Malevolent", "Iron Hands", "Crimson Fists"],
            "node_affinity": ["war_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "void_interdiction": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Cowled Wardens"],
            "node_affinity": ["dead_world", "watch_station"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "reclaim_the_reach": {
            "chapter_affinity": ["Salamanders", "Blood Angels", "Celestial Lions", "Raptors"],
            "node_affinity": ["agri_world", "frontier_world", "shrine_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
    },
    "watch_captain": {
        "prosecute_with_vigour": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Black Templars", "Marines Malevolent", "Bleeding Hearts"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["aggressive"], "weight": 1.2
        },
        "calculated_advance": {
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Dark Angels", "Celestial Lions"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
        "hold_and_endure": {
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Minotaurs", "Iron Lords", "Iron Hounds"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["defensive"], "weight": 1.0
        },
    },
    "watch_lieutenant": {
        "press_the_flanks": {
            "chapter_affinity": ["Raven Guard", "Raptors", "Blood Angels", "Carmine Blades", "Space Wolves"],
            "node_affinity": ["feral_world", "hive_world", "fortress_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.2
        },
        "fire_and_advance": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Crimson Fists", "Marines Malevolent", "Tempestuous Angels"],
            "node_affinity": ["war_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["aggressive"], "weight": 1.0
        },
        "consolidate_gains": {
            "chapter_affinity": ["Iron Hands", "Iron Lords", "Crimson Fists", "Mentors", "Celestial Lions"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["defensive", "resilience"], "weight": 1.0
        },
    },
    "company_champion": {
        "honour_the_duel": {
            "chapter_affinity": ["Space Wolves", "Dragonspears", "Blood Angels", "Tempestuous Angels", "Minotaurs"],
            "node_affinity": ["war_world", "feral_world", "fortress_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.2
        },
        "break_the_shield": {
            "chapter_affinity": ["Marines Malevolent", "Minotaurs", "Crimson Fists", "Angels of Vengeance", "Bleeding Hearts"],
            "node_affinity": ["fortress_world", "war_world", "hive_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["aggressive", "suppression"], "weight": 1.0
        },
        "blade_consecrated": {
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Dark Angels", "Dragonspears"],
            "node_affinity": ["shrine_world", "fortress_world", "dead_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["faith"], "weight": 1.0
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

for role in ["watch_master", "watch_captain", "watch_lieutenant", "company_champion"]:
    count = sum(1 for k in data[role] if not k.startswith("_"))
    print(f"{role}: {count} options")
