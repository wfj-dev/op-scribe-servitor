#!/usr/bin/env python3
"""Expand Company specialist roles: watch_techmarine, watch_apothecary, watch_chaplain, watch_librarian, watch_keeper."""
import json, os

CASCADE_FILE = os.path.join(os.path.dirname(__file__), "../reference/cascade_options.json")

ADDITIONS = {
    "watch_techmarine": {
        "machine_spirit_alignment": {
            "name": "Machine Spirit Alignment",
            "description": "The Techmarine binds the kill teams' wargear to the specific character of this world's machine spirits. Weapons that understand where they are perform better — the Techmarine ensures every piece of equipment is attuned.",
            "tags": ["tech", "resilience", "recovery", "aggressive"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Dragonspears", "Mentors", "Blood Ravens"],
            "node_affinity": ["forge_world", "fortress_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["tech"],
            "weight": 1.2
        },
        "field_fabrication": {
            "name": "Field Fabrication",
            "description": "The Techmarine works without the Manufactorum. Salvage, captured materials, and improvised forging extend the company's operational endurance beyond what the supply chain can sustain. Necessity breeds innovation.",
            "tags": ["tech", "recovery", "resilience", "fortify"],
            "chapter_affinity": ["Iron Hands", "Mentors", "Iron Lords", "Crimson Fists", "Salamanders"],
            "node_affinity": ["frontier_world", "agri_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "auspex_saturation": {
            "name": "Auspex Saturation",
            "description": "Every kill team carries Techmarine-calibrated auspex units. The Techmarine processes the data feeds in real-time and routes targeting intelligence to where it can be actioned. Blind spots become kill zones.",
            "tags": ["tech", "intel", "suppression", "defensive"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Raptors", "Iron Ravens", "Dark Angels"],
            "node_affinity": ["fortress_world", "hive_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "denial_rites": {
            "name": "Denial Rites",
            "description": "The Techmarine applies active countermeasures against enemy technology. Disruption patterns, anti-signal rites, precision jamming — the enemy's war machines and communications are denied to them. What the machine gives, the Techmarine can take away.",
            "tags": ["tech", "suppression", "intel", "fortify"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Mentors", "Blood Ravens", "Dragonspears"],
            "node_affinity": ["forge_world", "fortress_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["tech"],
            "weight": 1.0
        },
        "armour_rites_intensive": {
            "name": "Armour Rites Intensive",
            "description": "The Techmarine sacrifices offensive output to maintain every suit of power armour at its operational peak. Ceramite integrity, void shield ratings, system redundancies — the company's armour becomes its primary weapon this beat.",
            "tags": ["tech", "resilience", "defensive", "fortify"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Iron Lords", "Minotaurs", "Iron Hounds"],
            "node_affinity": ["fortress_world", "mining_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive", "resilience"],
            "weight": 1.0
        },
        "scavenge_and_adapt": {
            "name": "Scavenge and Adapt",
            "description": "The Techmarine catalogues enemy technology and recovers whatever can be reconditioned. Not simply trophies — functional enhancements, counter-measures, and analytical data that give the company an edge in the next engagement.",
            "tags": ["tech", "intel", "recovery", "elimination"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Raptors", "Iron Ravens", "Tome Keepers"],
            "node_affinity": ["dead_world", "frontier_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "warp_shielding_protocols": {
            "name": "Warp-Shielding Protocols",
            "description": "The Techmarine implements machine-based defences against psychic and warp interference. Not the Librarian's path — iron and circuitry, null-field generators, mechanically enforced barriers. The machine pushes the warp back.",
            "tags": ["tech", "void", "fortify", "defensive"],
            "chapter_affinity": ["Iron Hands", "Dragonspears", "Blood Ravens", "Iron Ravens", "Cowled Wardens"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "blessed_ammunition_rites": {
            "name": "Blessed Ammunition Rites",
            "description": "Every bolt, every shell, every plasma charge is consecrated at the Techmarine's forge station. The blessing is technical as much as spiritual — quality control, sacred geometry, optimal charge state. These rounds do not miss their purpose.",
            "tags": ["tech", "faith", "aggressive", "terminus"],
            "chapter_affinity": ["Black Templars", "Salamanders", "Cowled Wardens", "Blood Angels", "Dragonspears"],
            "node_affinity": ["shrine_world", "fortress_world", "forge_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "infrastructure_seizure": {
            "name": "Infrastructure Seizure",
            "description": "The Techmarine leads the operation to capture and activate enemy technology in place. Power plants, vox networks, defensive systems — turned over to the Watch's use before the enemy can deny them. Technology becomes a strategic trophy.",
            "tags": ["tech", "fortify", "intel", "recovery"],
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Mentors", "Blood Ravens", "Dragonspears"],
            "node_affinity": ["forge_world", "hive_world", "mining_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_living_fortress": {
            "name": "The Living Fortress",
            "description": "Every position the company holds becomes a fortified strongpoint under the Techmarine's direction. Defensive constructions, automated sentinels, layered sensors — each Watch position becomes harder to assault than it was to take.",
            "tags": ["tech", "fortify", "defensive", "resilience"],
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Minotaurs", "Iron Lords", "Iron Hounds"],
            "node_affinity": ["fortress_world", "agri_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
    },
    "watch_apothecary": {
        "combat_extraction": {
            "name": "Combat Extraction",
            "description": "The Apothecary builds extraction protocols into every kill team's engagement plan. No brother is abandoned — planned withdrawal routes, prioritised targets, coordinated collapse into defended positions. The Apothecary does not wait to be called.",
            "tags": ["recovery", "resilience", "defensive", "stealth"],
            "chapter_affinity": ["Blood Angels", "Salamanders", "Celestial Lions", "Iron Hands", "Raptors"],
            "node_affinity": ["war_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.2
        },
        "gene_seed_integrity": {
            "name": "Gene-Seed Integrity",
            "description": "The Apothecary prioritises the biological continuity of the Watch above tactical considerations. Progenoids are secured, gene-seed recovered, the biological chain of the Adeptus Astartes maintained even at cost to the mission. Some things matter more than the operation.",
            "tags": ["recovery", "faith", "resilience", "defensive"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Salamanders", "Space Wolves", "Iron Hands"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "stimm_doctrine": {
            "name": "Stimm Doctrine",
            "description": "The Apothecary administers combat stimulants with calculated precision before operations begin. Enhanced reaction time, pain suppression, physical output at the edge of tolerance — the kill teams perform at maximum capacity for a defined window.",
            "tags": ["recovery", "aggressive", "resilience", "terminus"],
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Marines Malevolent", "Raptors", "Bleeding Hearts"],
            "node_affinity": ["war_world", "feral_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "the_living_defiance": {
            "name": "The Living Defiance",
            "description": "Brothers who would otherwise fall are kept in the fight. The Apothecary moves through the engagement, maintaining wounded brothers at combat effectiveness, refusing to allow the enemy the satisfaction of the kill. The Watch fights until it chooses not to.",
            "tags": ["recovery", "resilience", "faith", "aggressive"],
            "chapter_affinity": ["Salamanders", "Blood Angels", "Iron Hands", "Crimson Fists", "Celestial Lions"],
            "node_affinity": ["fortress_world", "agri_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["resilience"],
            "weight": 1.0
        },
        "toxin_analysis": {
            "name": "Toxin Analysis",
            "description": "The Apothecary catalogues xenos biological weapons encountered this beat — toxins, pathogens, biological agents — and develops countermeasures in the field. Current threat neutralised; future threats anticipated. The company does not die to the same poison twice.",
            "tags": ["recovery", "intel", "void", "defensive"],
            "chapter_affinity": ["Raptors", "Blood Ravens", "Mentors", "Celestial Lions", "Raven Guard"],
            "node_affinity": ["feral_world", "frontier_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "field_surgery_bay": {
            "name": "Field Surgery Bay",
            "description": "The Apothecary establishes a forward surgical position within the operational area. Brothers cycle through between engagements — wounds treated, armour patched, combat effectiveness restored before the next op begins. The company's attrition rate drops dramatically.",
            "tags": ["recovery", "resilience", "fortify", "defensive"],
            "chapter_affinity": ["Salamanders", "Celestial Lions", "Blood Angels", "Iron Hands", "Mentors"],
            "node_affinity": ["agri_world", "frontier_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["defensive"],
            "weight": 1.0
        },
        "the_martyred_record": {
            "name": "The Martyred Record",
            "description": "Every brother who falls this beat is acknowledged by name, their manner of death recorded, their gene-seed secured or their sacrifice documented. The Apothecary carries the company's losses as a sacred obligation — none fall without witness.",
            "tags": ["recovery", "faith", "resilience", "intel"],
            "chapter_affinity": ["Celestial Lions", "Tome Keepers", "Blood Ravens", "Salamanders", "Blood Angels"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
        "pre_deployment_assessment": {
            "name": "Pre-Deployment Assessment",
            "description": "The Apothecary evaluates every brother before they are committed. Combat-effective, reserve, or medically withdrawn — the Apothecary makes the call and the company deploys only those who are ready. Better to fight with fewer brothers at full capability.",
            "tags": ["recovery", "intel", "defensive", "resilience"],
            "chapter_affinity": ["Mentors", "Iron Hands", "Raptors", "Celestial Lions", "Blood Ravens"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_red_tithe": {
            "name": "The Red Tithe",
            "description": "The Apothecary accounts for the blood spent this beat against the blood available. Calculated expenditure — no brother is committed without analysis, no casualty without tactical value. The Apothecary holds the company's biological budget.",
            "tags": ["recovery", "intel", "resilience", "aggressive"],
            "chapter_affinity": ["Flesh Tearers", "Blood Angels", "Marines Malevolent", "Carmine Blades", "Minotaurs"],
            "node_affinity": ["war_world", "agri_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "warp_exposure_triage": {
            "name": "Warp Exposure Triage",
            "description": "The Apothecary performs psychic contamination assessments and administers counteracting treatments. Brothers exposed to warp influence are evaluated, quarantined if necessary, treated with the full spectrum of the Apothecary's art. The warp does not claim them if the Apothecary can prevent it.",
            "tags": ["recovery", "void", "faith", "defensive"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Mentors"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
    },
    "watch_chaplain": {
        "the_rite_of_battle": {
            "name": "The Rite of Battle",
            "description": "The Chaplain performs the formal battle-rites before every kill team commits. Purpose clarified, oaths renewed, hatred consecrated. The kill teams leave the briefing knowing exactly what is required of them and exactly why it matters.",
            "tags": ["faith", "aggressive", "terminus", "resilience"],
            "chapter_affinity": ["Black Templars", "Blood Angels", "Salamanders", "Cowled Wardens", "Space Wolves"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.2
        },
        "the_litany_of_hatred": {
            "name": "The Litany of Hatred",
            "description": "The Chaplain names the enemy with precision — their nature, their heresy, their threat to the Imperium. The kill teams do not fight abstractions; they fight specific, named evil. Hatred made concrete becomes the most reliable weapon.",
            "tags": ["faith", "elimination", "terminus", "void"],
            "chapter_affinity": ["Black Templars", "Angels of Vengeance", "Dark Angels", "Flesh Tearers", "Marines Malevolent"],
            "node_affinity": ["dead_world", "feral_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "the_burden_carried": {
            "name": "The Burden Carried",
            "description": "The Chaplain tends to those who struggle beneath the weight of what they have seen and done. Warp exposure, terrible kills, the accumulation of the campaign's costs — the Chaplain ensures the brothers remain themselves through it all.",
            "tags": ["faith", "recovery", "resilience", "void"],
            "chapter_affinity": ["Salamanders", "Blood Angels", "Space Wolves", "Celestial Lions", "Tome Keepers"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "oath_renewed": {
            "name": "Oath Renewed",
            "description": "In a formal ceremony before the company, the Chaplain leads the renewal of battle-oaths. Brothers whose commitments have grown stale or wavered under attrition find purpose re-instilled. The oath is the chain that holds — the Chaplain ensures it holds fast.",
            "tags": ["faith", "resilience", "recovery", "defensive"],
            "chapter_affinity": ["Black Templars", "Space Wolves", "Salamanders", "Cowled Wardens", "Blood Angels"],
            "node_affinity": ["fortress_world", "shrine_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith", "resilience"],
            "weight": 1.0
        },
        "suffer_not_the_heretic": {
            "name": "Suffer Not the Heretic",
            "description": "The Chaplain provides formal doctrinal clarity on exactly what must be destroyed without mercy or negotiation. No ambiguity about where mercy ends and duty begins — the Chaplain draws the line in fire and the kill teams know exactly which side everything falls on.",
            "tags": ["faith", "elimination", "terminus", "aggressive"],
            "chapter_affinity": ["Black Templars", "Dark Angels", "Blood Angels", "Angels of Vengeance", "Cowled Wardens"],
            "node_affinity": ["shrine_world", "dead_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_martyr_doctrine": {
            "name": "The Martyr Doctrine",
            "description": "The Chaplain consecrates the cost — every brother who falls does so in full knowledge of what their sacrifice advances. Purpose transforms death. The kill teams fight harder because they know the Chaplain has already consecrated the worst outcome.",
            "tags": ["faith", "resilience", "terminus", "recovery"],
            "chapter_affinity": ["Celestial Lions", "Blood Angels", "Salamanders", "Tempestuous Angels", "Black Templars"],
            "node_affinity": ["war_world", "dead_world", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith", "resilience"],
            "weight": 1.0
        },
        "the_darkness_named": {
            "name": "The Darkness Named",
            "description": "The Chaplain delivers a formal accounting of the warp's specific expression on this world — its source, its nature, the name of what has invited it. Knowledge of the enemy's spiritual architecture is the first step in its dismantling.",
            "tags": ["faith", "void", "intel", "elimination"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Raptors"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "sanctify_the_fallen": {
            "name": "Sanctify the Fallen",
            "description": "The Chaplain moves through the aftermath and performs rites over those lost this beat. The fallen are not forgotten — they are formally received into the company's memory. The living fight harder when they know the dead will be honoured.",
            "tags": ["faith", "recovery", "resilience", "defensive"],
            "chapter_affinity": ["Salamanders", "Celestial Lions", "Blood Angels", "Tome Keepers", "Cowled Wardens"],
            "node_affinity": ["shrine_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_wrath_channelled": {
            "name": "The Wrath Channelled",
            "description": "The Chaplain converts grief and rage into precision. The company has suffered — the Chaplain ensures that suffering becomes directed violence rather than disorganised fury. Anger made doctrine is the most effective anger.",
            "tags": ["faith", "aggressive", "terminus", "elimination"],
            "chapter_affinity": ["Flesh Tearers", "Blood Angels", "Marines Malevolent", "Bleeding Hearts", "Carmine Blades"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["aggressive"],
            "weight": 1.0
        },
        "the_covenant_held": {
            "name": "The Covenant Held",
            "description": "The Chaplain draws explicit line of continuity from the founding oaths of the Deathwatch to the current operation. Every kill team is part of something centuries old and worth every cost demanded. The covenant holds — it has always held.",
            "tags": ["faith", "resilience", "recovery", "intel"],
            "chapter_affinity": ["Tome Keepers", "Blood Ravens", "Dark Angels", "Celestial Lions", "Space Wolves"],
            "node_affinity": ["fortress_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["faith"],
            "weight": 1.0
        },
    },
    "watch_librarian": {
        "the_sight_maintained": {
            "name": "The Sight Maintained",
            "description": "The Librarian anchors psykanic perception across the operational area — eyes that do not blink, awareness that does not drift. What moves in the shadows, what hides in the warp-touched ruins, what the enemy thinks it conceals: the Librarian sees it and routes intelligence to where it is needed.",
            "tags": ["void", "intel", "stealth", "suppression"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Cowled Wardens", "Iron Ravens"],
            "node_affinity": ["dead_world", "watch_station", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.2
        },
        "the_warding_field": {
            "name": "The Warding Field",
            "description": "The Librarian establishes a psykanic barrier around the company's positions. Not offensive capability — pure denial. What the warp sends, the Librarian turns away. The kill teams operate behind a barrier their enemy cannot understand how to breach.",
            "tags": ["void", "defensive", "fortify", "resilience"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Cowled Wardens", "Dark Angels", "Iron Ravens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "the_breaking_word": {
            "name": "The Breaking Word",
            "description": "A single concentrated psychic utterance that severs the command-link in the enemy's structure. Not individual combat — targeted disruption of the psychic or electromagnetic bonds that make the enemy a coherent force. They fragment; the Watch harvests the pieces.",
            "tags": ["void", "elimination", "suppression", "aggressive"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Iron Ravens", "Mentors"],
            "node_affinity": ["dead_world", "fortress_world", "hive_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "deep_reading": {
            "name": "Deep Reading",
            "description": "The Librarian dedicates this beat to thorough analysis of the psychic currents surrounding the current node. What has happened here, what is happening, what the warp intends — a comprehensive intelligence picture that supplements everything the Kill Team can observe directly.",
            "tags": ["void", "intel", "recovery", "defensive"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Raven Guard", "Dark Angels", "Mentors"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_psychic_lance": {
            "name": "The Psychic Lance",
            "description": "The Librarian identifies a single significant threat and prepares a focused psychic elimination. All psykanic resource is conserved and channelled to this one execution — singular, devastating, and absolute.",
            "tags": ["void", "terminus", "elimination", "aggressive"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Iron Ravens", "Cowled Wardens"],
            "node_affinity": ["dead_world", "feral_world", "war_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void", "terminus"],
            "weight": 1.0
        },
        "the_veil_pierced": {
            "name": "The Veil Pierced",
            "description": "The Librarian reaches through the warp's membrane and extracts specific knowledge — what lies at the node's heart, what the enemy intends at the strategic level, what the campaign's true shape is. Intelligence that no conventional means could reach.",
            "tags": ["void", "intel", "recovery", "resilience"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Raptors", "Dark Angels", "Mentors"],
            "node_affinity": ["dead_world", "watch_station", "special"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "the_memoria_bound": {
            "name": "The Memoria Bound",
            "description": "The Librarian inscribes this beat's significant events into the psychic chronicle — the immutable record maintained in the warp's own substrate. What is written here cannot be altered, cannot be suppressed. The truth is preserved regardless of what the campaign's record otherwise claims.",
            "tags": ["void", "intel", "faith", "recovery"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Celestial Lions", "Mentors"],
            "node_affinity": ["fortress_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "shatter_the_working": {
            "name": "Shatter the Working",
            "description": "A specific enemy psychic operation has been identified — ritual, warding, compulsion, or presence. The Librarian dedicates everything to dismantling it. No other contribution this beat; the working is ended.",
            "tags": ["void", "fortify", "elimination", "defensive"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Cowled Wardens", "Dark Angels", "Iron Ravens"],
            "node_affinity": ["dead_world", "shrine_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
        "the_quiet_channel": {
            "name": "The Quiet Channel",
            "description": "The Librarian maintains a psychic communication network between kill teams that no signal-jam or vox disruption can interfere with. Whatever the electromagnetic environment does to conventional communications, the kill teams remain coordinated.",
            "tags": ["void", "intel", "resilience", "suppression"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Raptors", "Tome Keepers", "Raven Guard"],
            "node_affinity": ["fortress_world", "hive_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_daemon_unmasked": {
            "name": "The Daemon Unmasked",
            "description": "The Librarian strips the glamours and concealment from warp-entities on this world, forcing their true nature into observable reality. What was hidden becomes visible; what was deniable becomes undeniable. The kill teams can now kill what they could previously only suspect.",
            "tags": ["void", "terminus", "intel", "elimination"],
            "chapter_affinity": ["Dark Angels", "Blood Ravens", "Tome Keepers", "Cowled Wardens", "Iron Ravens"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
    },
    "watch_keeper": {
        "the_record_unsealed": {
            "name": "The Record Unsealed",
            "description": "The Watch Keeper opens relevant xenos intelligence files to the kill teams — encounters, behavioural patterns, weaknesses confirmed by previous Watch operations. Brothers who know what they face kill it more efficiently.",
            "tags": ["intel", "terminus", "elimination", "stealth"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Mentors", "Raptors", "Dark Angels"],
            "node_affinity": ["fortress_world", "watch_station", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.2
        },
        "the_silence_maintained": {
            "name": "The Silence Maintained",
            "description": "The Watch Keeper ensures operational security is absolute this beat — no intelligence leaks, no communications compromise, no indication to the enemy that the Watch is aware of their disposition. Information advantage is only valuable when the enemy does not know you have it.",
            "tags": ["intel", "stealth", "defensive", "void"],
            "chapter_affinity": ["Raven Guard", "Raptors", "Dark Angels", "Cowled Wardens", "Necropolis Hawks"],
            "node_affinity": ["fortress_world", "hive_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["stealth"],
            "weight": 1.0
        },
        "cross_reference_active": {
            "name": "Cross-Reference Active",
            "description": "The Watch Keeper analyses this beat's operational data against the Librarium's holdings — identifying patterns the kill teams may not see themselves. Tactical anomalies, strategic deceptions, threat signatures that match historical encounters. Nothing about this enemy is truly new.",
            "tags": ["intel", "recovery", "void", "elimination"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Mentors", "Raptors", "Blood Ravens"],
            "node_affinity": ["fortress_world", "watch_station", "dead_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_sealed_account": {
            "name": "The Sealed Account",
            "description": "The Watch Keeper creates a classified record of this beat's most sensitive intelligence — sealed, compartmentalised, accessible only to those with need and authorisation. Some truths are too dangerous to circulate freely.",
            "tags": ["intel", "void", "stealth", "recovery"],
            "chapter_affinity": ["Dark Angels", "Cowled Wardens", "Raven Guard", "Tome Keepers", "Mentors"],
            "node_affinity": ["watch_station", "dead_world", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "identify_the_apex": {
            "name": "Identify the Apex",
            "description": "The Watch Keeper directs intelligence efforts to profiling the enemy's most dangerous element — its leadership structure, its most capable warrior, its strategic centre of gravity. Intelligence precedes the blade; the Watch Keeper ensures the blade is aimed correctly.",
            "tags": ["intel", "terminus", "elimination", "void"],
            "chapter_affinity": ["Raptors", "Mentors", "Blood Ravens", "Dark Angels", "Celestial Lions"],
            "node_affinity": ["fortress_world", "dead_world", "feral_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["terminus"],
            "weight": 1.0
        },
        "the_cartographic_update": {
            "name": "The Cartographic Update",
            "description": "The Watch Keeper integrates this beat's reconnaissance into the operational map — confirmed routes, denied zones, objective locations updated, enemy dispositions refined. The map the kill teams use next beat will be better because of what was learned this one.",
            "tags": ["intel", "fortify", "defensive", "recovery"],
            "chapter_affinity": ["Raptors", "Mentors", "Blood Ravens", "Tome Keepers", "Iron Ravens"],
            "node_affinity": ["frontier_world", "agri_world", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_cipher_broken": {
            "name": "The Cipher Broken",
            "description": "Enemy communications captured this beat are fed through the Watch Keeper's analytical processes. The cipher broken, the signal traffic read, the enemy's plan understood before it is fully executed. The Watch Keeper's quiet work has wider consequences than most will know.",
            "tags": ["intel", "suppression", "stealth", "elimination"],
            "chapter_affinity": ["Blood Ravens", "Mentors", "Raptors", "Raven Guard", "Dark Angels"],
            "node_affinity": ["hive_world", "fortress_world", "watch_station"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["intel"],
            "weight": 1.0
        },
        "historical_precedent": {
            "name": "Historical Precedent",
            "description": "The Watch Keeper locates records of a previous Watch operation on this world or against this specific enemy configuration. Lessons from that campaign — what worked, what failed, what was learned at terrible cost — are shared with the company in time to be actionable.",
            "tags": ["intel", "recovery", "resilience", "defensive"],
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Mentors", "Dark Angels", "Celestial Lions"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "the_tally_updated": {
            "name": "The Tally Updated",
            "description": "Every kill confirmed, every objective secured, every intelligence asset recovered — the Watch Keeper maintains the operational accounting. This beat's tally matters because it feeds into the campaign's strategic assessment. Precision in record-keeping is its own form of mission-critical work.",
            "tags": ["intel", "recovery", "faith", "terminus"],
            "chapter_affinity": ["Celestial Lions", "Tome Keepers", "Blood Ravens", "Mentors", "Salamanders"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": [],
            "weight": 1.0
        },
        "threat_reclassification": {
            "name": "Threat Reclassification",
            "description": "Based on evidence gathered this beat, the Watch Keeper formally revises the threat classification of the enemy force. Resources are reallocated, priorities are updated, the campaign's response is calibrated to what is actually present rather than what was assumed at the outset.",
            "tags": ["intel", "void", "defensive", "suppression"],
            "chapter_affinity": ["Raptors", "Mentors", "Blood Ravens", "Tome Keepers", "Celestial Lions"],
            "node_affinity": ["dead_world", "watch_station", "fortress_world"],
            "suppress_if_previous": False,
            "requires_upstream_tags": ["void"],
            "weight": 1.0
        },
    },
}

SCHEMA_UPDATES = {
    "watch_techmarine": {
        "forge_the_breach": {
            "chapter_affinity": ["Iron Hands", "Dragonspears", "Iron Ravens", "Mentors", "Salamanders"],
            "node_affinity": ["fortress_world", "forge_world", "mining_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["tech"], "weight": 1.2
        },
        "rites_of_maintenance": {
            "chapter_affinity": ["Iron Hands", "Crimson Fists", "Iron Lords", "Iron Hounds", "Mentors"],
            "node_affinity": ["fortress_world", "agri_world", "mining_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["resilience"], "weight": 1.0
        },
        "machine_curse": {
            "chapter_affinity": ["Iron Hands", "Iron Ravens", "Blood Ravens", "Dragonspears", "Mentors"],
            "node_affinity": ["forge_world", "dead_world", "fortress_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
        },
    },
    "watch_apothecary": {
        "keep_them_fighting": {
            "chapter_affinity": ["Blood Angels", "Salamanders", "Celestial Lions", "Iron Hands", "Raptors"],
            "node_affinity": ["war_world", "fortress_world", "agri_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.2
        },
        "recover_the_fallen": {
            "chapter_affinity": ["Salamanders", "Blood Angels", "Space Wolves", "Flesh Tearers", "Iron Hands"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["recovery"], "weight": 1.0
        },
        "pain_is_doctrine": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Marines Malevolent", "Bleeding Hearts", "Minotaurs"],
            "node_affinity": ["war_world", "feral_world", "dead_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["aggressive"], "weight": 1.0
        },
    },
    "watch_chaplain": {
        "the_litanies_spoken": {
            "chapter_affinity": ["Black Templars", "Blood Angels", "Salamanders", "Cowled Wardens", "Space Wolves"],
            "node_affinity": ["shrine_world", "fortress_world", "war_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["faith"], "weight": 1.2
        },
        "penance_and_purpose": {
            "chapter_affinity": ["Blood Angels", "Flesh Tearers", "Dark Angels", "Space Wolves", "Tempestuous Angels"],
            "node_affinity": ["war_world", "dead_world", "agri_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["faith", "resilience"], "weight": 1.0
        },
        "unbroken_by_the_warp": {
            "chapter_affinity": ["Black Templars", "Dark Angels", "Tome Keepers", "Cowled Wardens", "Blood Ravens"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["void"], "weight": 1.0
        },
    },
    "watch_librarian": {
        "the_sight_beyond": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Cowled Wardens", "Iron Ravens"],
            "node_affinity": ["dead_world", "watch_station", "shrine_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["void"], "weight": 1.2
        },
        "psychic_shield": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Cowled Wardens", "Dark Angels", "Iron Ravens"],
            "node_affinity": ["dead_world", "shrine_world", "fortress_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["void", "defensive"], "weight": 1.0
        },
        "the_aetherial_strike": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Dark Angels", "Iron Ravens", "Mentors"],
            "node_affinity": ["dead_world", "feral_world", "war_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["void", "aggressive"], "weight": 1.0
        },
    },
    "watch_keeper": {
        "the_librarium_consulted": {
            "chapter_affinity": ["Blood Ravens", "Tome Keepers", "Mentors", "Dark Angels", "Raptors"],
            "node_affinity": ["fortress_world", "watch_station", "dead_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.2
        },
        "intelligence_distributed": {
            "chapter_affinity": ["Raptors", "Mentors", "Blood Ravens", "Tome Keepers", "Iron Ravens"],
            "node_affinity": ["fortress_world", "agri_world", "frontier_world"],
            "suppress_if_previous": False, "requires_upstream_tags": ["intel"], "weight": 1.0
        },
        "the_archive_secured": {
            "chapter_affinity": ["Dark Angels", "Cowled Wardens", "Blood Ravens", "Tome Keepers", "Mentors"],
            "node_affinity": ["watch_station", "dead_world", "shrine_world"],
            "suppress_if_previous": False, "requires_upstream_tags": [], "weight": 1.0
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

for role in ["watch_techmarine", "watch_apothecary", "watch_chaplain", "watch_librarian", "watch_keeper"]:
    count = sum(1 for k in data[role] if not k.startswith("_"))
    print(f"{role}: {count} options")
