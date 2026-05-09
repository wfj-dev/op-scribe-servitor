"""Flavor text and large RP data tables extracted from bot.py.

This module contains pure literal data only (chapter blessings, rank
acknowledgments, mechanicus phrases, stud milestones, etc.). It has no
runtime dependencies on bot.py state. bot.py re-exports everything via
``from flavor_text import *`` so existing references and tests keep
working unchanged.
"""

from typing import Dict, List

MAX_RITE_LENGTH = 250

# Chapter-specific blessings keyed by home chapter name
CHAPTER_BLESSINGS: Dict[str, str] = {
    "Angels of Defiance": "Unyielding as the Lion, defiant unto death—your armor bears the Unforgiven's resolve.",
    "Angels of Vengeance": "The wrath of the Lion courses through your warplate.",
    "Black Templars": "No pity, no remorse, no fear—your armor embodies the Eternal Crusade.",
    "Bleeding Hearts": "The Rage burns close—your armor bears the weight of martyrdom and the trophies of the hunt.",
    "Blood Angels": "By the Blood of Sanguinius, your armor is sanctified.",
    "Blood Ravens": "Knowledge is power; guard it well within these sacred plates.",
    "Brazen Minotaurs": "Bronze and fury—your armor embodies the unstoppable siege.",
    "Carcharodons": "From the void you came, and to the void your enemies shall fall.",
    "Cowled Wardens": "The Unforgiven hunt eternal; your armor conceals the Lion's secret purpose.",
    "Crimson Fists": "The fist of Dorn strikes true; let your armor be unyielding.",
    "Dark Angels": "The secrets of the First are woven into your warplate's spirit.",
    "Dark Krakens": "From the abyssal depths, your armor rises to crush the foe.",
    "Dragonspears": "Vulkan's flame endures in your armor; the memory of fallen brothers drives you to hunt the Greenskin unto extinction.",
    "Death Spectres": "The shroud of death clings to your armor; let enemies despair.",
    "Epsilon Paladins": "For Honour! For Duty! For Dorn!—your armor gleams with the Paladin's steadfast resolve.",
    "Exorcists": "Thrice-bound against the Warp, your armor stands inviolate.",
    "Flesh Tearers": "The Red Thirst is tempered within your armor's adamantine heart.",
    "Genesis Chapter": "The purity of Guilliman's line flows through these blessed plates.",
    "Hawk Lords": "Swift as the raptor, your armor bears you to righteous war.",
    "Hospitallers": "Mercy and wrath unite—your armor shields the weak and smites the wicked.",
    "Imperial Fists": "Fortify your spirit as these plates fortify your flesh.",
    "Iron Hands": "The flesh is weak, but your armor is the strength of iron.",
    "Iron Ravens": "Silent shadow, tempered iron—your armor moves unseen and strikes with precision.",
    "Iron Snakes": "The waters of Ithaka anoint your armor; as the Snakes strike, so shall you.",
    "Iron Hounds": "Guilliman's hounds pursue without relent; your armor knows no surrender.",
    "Knights of the Raven": "In cunning silence, your armor conceals the Emperor's justice.",
    "Lamenters": "Though cursed, your armor shall not fail—for those we cherish, we die.",
    "Marines Errant": "The stars are your homeworld, brother—your armor carries Dorn's quest eternal.",
    "Mentors": "Precision and wisdom are encoded in your warplate's machine-spirit.",
    "Minotaurs": "The fury of the bull charges forth; your armor is wrath incarnate.",
    "Necropolis Hawks": "From ruin to ruin, your armor claims each domain for the Emperor—stoic, efficient, relentless.",
    "Raptors": "Silent and lethal, your armor whispers death to the enemies of Man.",
    "Raven Guard": "In the shadow of the Raven, your armor moves unseen.",
    "Red Scorpions": "Purity above all; your armor meets the Apothecary's exacting standards.",
    "Red Templars": "Dorn's fury given form—your armor strikes swift and unyielding.",
    "Salamanders": "Into the fires of battle, your armor shields the innocent.",
    "Scythes of the Emperor": "Sotha is lost, but your armor carries the chapter's vengeance eternal.",
    "Sons of Medusa": "Steel and logic strengthen your armor against all adversity.",
    "Space Wolves": "The spirit of Fenris howls within your blessed warplate.",
    "Storm Giants": "The giant's strength flows through your armor; towering might breaks all foes.",
    "Tempestuous Angels": "Vulkan's altruism lives on—your armor shields the defenseless against the storm.",
    "The Drakes": "Fire cleanses all—your armor emerges purified and ready.",
    "Ultramarines": "The Codex guides us; your armor upholds Guilliman's legacy.",
    "White Scars": "The wind of Chogoris propels your armor to swift victory.",
    "Black Shield": "Your past is forgotten; your armor serves only the Long Watch.",
}

# Rank-based honorifics and phrases (ordered from highest to lowest priority)
# Higher ranks should be checked first since members often have multiple rank roles
RANK_HONORIFICS: Dict[str, str] = {
    # High Command (check first)
    "Watch Master": "Lord of the Long Watch, Watch Master",
    "High Chaplain": "Voice of the Emperor, High Chaplain",
    "Chief Apothecary": "Keeper of Purity, Chief Apothecary",
    "Void Warden": "Aegis against the Void, Void Warden",
    "Forgemaster": "Hand of the Machine God, Forgemaster",
    "Castellan": "Warden of the Iron Vigil, Castellan",
    "Lord Executioner": "Blade of the Fortress, Lord Executioner",
    # Dreadnoughts
    "Venerable Dreadnought": "Ancient of the Long Watch, Venerable Dreadnought",
    "Honored Dreadnought": "Honored Dreadnought",
    "Interred Brother": "Interred Brother",
    # Specialists
    "Watch Chaplain": "Keeper of the faith, Watch Chaplain",
    "Watch Apothecary": "Guardian of the gene-seed, Watch Apothecary",
    "Watch Librarian": "Warden of the Immaterium, Watch Librarian",
    "Watch Techmarine": "Servant of the Omnissiah, Watch Techmarine",
    "Watch Keeper": "Guardian of the Watch Fortress, Watch Keeper",
    # Champions
    "Company Champion": "Blade of the Company, Company Champion",
    "Kill Team Champion": "Blade of the Kill Team, Kill Team Champion",
    # Battle line (highest to lowest)
    "Watch Captain": "Warden of the Company, Watch Captain",
    "Watch Lieutenant": "Shield of the Watch, Watch Lieutenant",
    "Watch Sergeant": "Bearer of command, Watch Sergeant",
    "Oathsworn": "Oathsworn Warrior",
    "Watch Veteran": "Honored Veteran",
    "Watch Brother": "Brother",
}

# Techmarine's recognition of bearer's experience/studs (tier-based, legacy - now using rank-specific)
TECHMARINE_STUDS_ACKNOWLEDGMENT: Dict[int, List[str]] = {
    1: [  # Tier 1 (1-3 studs): Fresh warrior
        "A warrior new-marked, yet the machine-spirit recognizes your potential.",
        "Your service begins; may this armor carry you through the trials ahead.",
        "Newly blooded, your armor learns your hand—grow together.",
    ],
    2: [  # Tier 2 (4-11 studs): Seasoned veteran
        "The armor recognizes a warrior of proven valor—we have seen many campaigns together.",
        "Your studs speak of battles endured; this armor is blessed to carry a veteran.",
        "Long service has earned you armor touched by countless glorious moments of war.",
    ],
    3: [  # Tier 3 (12-16 studs): Legendary
        "The machine-spirit trembles before one so honored; legends rarely grace such work.",
        "An ancient warrior comes forth—may this armor honor the centuries of your service.",
        "The armor itself is humbled; to bear the weight of such achievement is sacred duty.",
    ],
}

# Rank-specific Techmarine acknowledgments for forge_rite
# These express how the Techmarine addresses bearers based on their specific rank
TECHMARINE_RANK_ACKNOWLEDGMENTS: Dict[str, List[str]] = {
    # Watch Master - utmost reverence
    "Watch Master": [
        "It is the highest honor to minister to the Lord of the Long Watch.",
        "The machine-spirits themselves tremble with awe at your station, my Lord.",
        "To sanctify the armor of the Watch Master is the pinnacle of sacred duty.",
    ],
    # High Command
    "High Chaplain": [
        "The Voice of the Emperor deserves armor as unyielding as his faith.",
        "Your sermons steel the souls of warriors; may this armor steel your flesh.",
        "The machine-spirit bows before the Emperor's chosen herald.",
    ],
    "Chief Apothecary": [
        "Guardian of the gene-seed, your armor must be as pure as the legacy you protect.",
        "The Keeper of Purity deserves warplate untouched by flaw or imperfection.",
        "May this armor shield the one who shields our sacred bloodlines.",
    ],
    "Void Warden": [
        "Aegis against the Immaterium, your armor must resist more than mortal threats.",
        "The wards I inscribe upon this armor echo the barriers of your mind.",
        "The machine-spirit stands vigilant alongside your psychic watch.",
    ],
    "Forgemaster": [
        "Master, it is my honor to tend to your sacred warplate.",
        "The Hand of the Machine God deserves the Omnissiah's finest ministrations.",
        "I apply the rites you taught me—may they honor your armor as you honor the craft.",
    ],
    "Lord Executioner": [
        "The Blade of the Fortress demands armor as sharp as his judgment.",
        "Your armor has tasted the blood of traitors; I sanctify it for more to come.",
        "The machine-spirit hungers for righteous execution at your command.",
    ],
    # Dreadnoughts
    "Venerable Dreadnought": [
        "Ancient of the Long Watch, your sarcophagus is a reliquary of war eternal—I am humbled to tend it.",
        "Venerable One, the machine-spirits of ages past whisper your deeds—may your dreadnought frame endure as your legend.",
        "To service the war-casket of one so ancient is the highest honor the Omnissiah could bestow upon this servant.",
    ],
    "Honored Dreadnought": [
        "Honored warrior, your sarcophagus has become your eternal throne—may it carry you to glory unending.",
        "The Dreadnought frame is blessed to bear one of such valor—your service transcends mortal flesh.",
        "Your interment honors the Chapter; your continued crusade honors the Emperor.",
    ],
    "Interred Brother": [
        "Interred Brother, your sarcophagus awaits the call to war—the machine-spirit keeps vigil in your slumber.",
        "Rest now, warrior; when the Long Watch requires, your dreadnought shall rise once more.",
        "Your sacred rest preserves you for the battles yet to come—the fortress remembers.",
    ],
    # Company Command
    "Watch Captain": [
        "Warden of the Company, your armor must be as steadfast as your command.",
        "The warriors who follow you need see no flaw in their Captain's warplate.",
        "By your leadership, the Company prevails—by my rites, your armor endures.",
    ],
    "Watch Lieutenant": [
        "Shield of the Watch, your armor stands between command and the line.",
        "The Lieutenant's armor must inspire those who look to you for orders.",
        "May this warplate serve as faithfully as you serve your Captain.",
    ],
    # Specialists
    "Watch Chaplain": [
        "Keeper of the Faith, your armor must reflect the Emperor's light.",
        "The warriors you inspire deserve to see unshakeable strength in your warplate.",
        "The machine-spirit resonates with the litanies you speak.",
    ],
    "Watch Apothecary": [
        "Guardian of the gene-seed, your armor must protect the protector.",
        "The Narthecium demands a steady hand—may this armor never hinder your sacred work.",
        "Your duty preserves the Chapter eternal; my duty preserves your armor.",
    ],
    "Watch Librarian": [
        "Warden of the Immaterium, your armor must withstand more than physical blows.",
        "I inscribe protective glyphs into the machine-spirit's core—may the Warp find no purchase.",
        "The psychic wards are renewed; the machine-spirit stands vigilant.",
    ],
    "Watch Techmarine": [
        "Brother-Techmarine, your armor deserves the same devotion you show others.",
        "We who serve the Machine God must not neglect our own sacred warplate.",
        "The machine-spirit welcomes the ministrations of a fellow servant.",
    ],
    "Watch Keeper": [
        "Guardian of the Fortress, your armor must be as unyielding as the walls you defend.",
        "The vaults and armories you ward are reflected in this warplate's vigilance.",
        "May this armor serve as the first bulwark against any who threaten our sanctum.",
    ],
    "Castellan": [
        "Master of the Fortress's defenses, your warplate must embody impregnable resolve.",
        "The walls of Jericho stand because of your vigilance—may this armor honor that duty.",
        "I sanctify the armor of the one who holds the keys to our sacred stronghold.",
    ],
    # Champions
    "Company Champion": [
        "Blade of the Company, your armor must match your peerless skill.",
        "The Champion's warplate has witnessed countless duels—may it witness countless more.",
        "The machine-spirit yearns for the glory of single combat at your side.",
    ],
    "Kill Team Champion": [
        "Champion of the Kill Team, your armor reflects the honor you bring your brothers.",
        "The blade that leads the charge deserves armor that never falters.",
        "Victory follows where the Champion treads—may your armor bear you to glory.",
    ],
    # Line ranks
    "Watch Sergeant": [
        "Bearer of command, your armor must set the example for those you lead.",
        "The Sergeant's warplate has seen the crucible of leadership—I honor its service.",
        "Your brothers look to you; may this armor reflect your steadfast resolve.",
    ],
    "Oathsworn": [
        "Oathsworn Warrior, your dedication to Jericho is writ in every plate of this armor.",
        "The bonds of the Oathsworn are eternal—may your armor endure as long.",
        "Your oath binds you to the Watch; my rites bind this armor to your service.",
    ],
    "Watch Veteran": [
        "Honored Veteran, your experience is etched into the machine-spirit's memory.",
        "Many battles have tested this warplate—may many more prove its worth.",
        "The Veteran's armor knows war; I rekindle its readiness for the next campaign.",
    ],
    "Watch Brother": [
        "Brother, the machine-spirit is honored to shield a warrior of the Long Watch.",
        "The backbone of the Watch—may your armor serve as faithfully as you.",
        "Your service to Jericho is written in every plate of this armor.",
    ],
}


# Rank prestige weights for acknowledgment blending (0.0-1.0)
# Higher rank = more likely to get rank-specific acknowledgment
RANK_PRESTIGE_WEIGHTS: Dict[str, float] = {
    # High Command - very high prestige
    "Watch Master": 1.0,
    "High Chaplain": 0.9,
    "Chief Apothecary": 0.9,
    "Void Warden": 0.9,
    "Forgemaster": 0.9,
    "Lord Executioner": 0.9,
    "Castellan": 0.85,
    # Dreadnoughts - high prestige
    "Venerable Dreadnought": 0.85,
    "Honored Dreadnought": 0.75,
    "Interred Brother": 0.2,  # Inactive, lower prestige
    # Company Command - high prestige
    "Watch Captain": 0.75,
    "Watch Lieutenant": 0.65,
    # Specialists - medium-high prestige
    "Watch Chaplain": 0.6,
    "Watch Apothecary": 0.6,
    "Watch Librarian": 0.6,
    "Watch Techmarine": 0.6,
    "Watch Keeper": 0.55,
    # Champions - medium prestige
    "Company Champion": 0.5,
    "Kill Team Champion": 0.45,
    # Line ranks - lower prestige (studs matter more)
    "Watch Sergeant": 0.35,
    "Oathsworn": 0.25,
    "Watch Veteran": 0.2,
    "Watch Brother": 0.1,
}
TECHMARINE_SIGNATURES: List[str] = [
    "I speak the Rites of Activation, and the machine-spirit awakens.",
    "With sacred oils and binharic prayer, this work is sanctified.",
    "The Motive Force flows through my hands into this blessed armor.",
    "By cog and gear, by circuit and servo, I seal this consecration.",
    "The Omnissiah's blessing descends through my ministrations.",
    "Through the Litany of Ignition, the war-spirit stirs.",
    "I have communed with the machine-spirit; it is at peace.",
    "The holy unguents are applied; the rites are complete.",
    "In nomine Machinae, this armor is bound to sacred purpose.",
    "The data-hymns are sung; the spirit-core is awakened.",
]

# Random sacred Mechanicus phrases to include in attestations
SACRED_MECHANICUS_PHRASES: List[str] = [
    "Praise the Omnissiah.",
    "The Machine God watches over this work.",
    "Data is sacred. Knowledge is power.",
    "From iron, cometh strength.",
    "The spirit of the machine is willing.",
    "Let the blessed cogitator record this deed.",
    "The Motive Force guides all.",
    "In the name of the Machine God, so it is done.",
    "Blessed is the machine that serves.",
    "By the grace of the Fabricator-General.",
    "The Quest for Knowledge continues.",
    "Steel and silicon, blessed and true.",
    "The Cant Mechanicus sanctifies this moment.",
    "May your augmetics never falter.",
    "The Void Dragon stirs not against this work.",
]

# Phrases for when the Forgemaster performs rites upon their own armor
# Blends Mechanicus reverence with Hawk Lords identity (raptor/sky/hunt imagery)
# Generic Mechanicus self-attestation phrases (role-focused)
FORGEMASTER_SELF_ATTESTATION_GENERIC: List[str] = [
    "The Omnissiah witnesses—I am both priest and supplicant.",
    "The master's hand tends to the master's plate—this burden is mine alone.",
    "None may bless what I have wrought but I who forged it.",
    "In solitude, the Forgemaster communes with his own machine-spirit.",
    "I speak the canticles to myself, for who else would understand?",
    "From my forge, to my flesh, to my faith—the circle closes.",
    "The Long Watch demands self-reliance. I answer.",
    "My armor knows no other hand. This rite is mine to perform.",
]

# Chapter-specific self-attestation phrases (chapter identity when self-blessing)
FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER: Dict[str, List[str]] = {
    "Hawk Lords": [
        "The raptor tends its own talons—who else knows where they have struck?",
        "From forge to sky, I bless the wings that carry me to war.",
        "Swift as the hawk, patient as the artisan—the rite is mine alone.",
    ],
    "Iron Hands": [
        "Flesh is weak; I trust only myself to tend the machine.",
        "The Gorgon would approve—self-sufficiency in all things.",
        "Logic dictates: who better to bless my iron than I?",
    ],
    "Iron Snakes": [
        "The Wyrm-hunter tends his own lance—none know its balance better.",
        "Ithaka's sons know solitude upon the waves; I renew my own armor.",
        "The serpent sheds its skin unaided; so do I maintain my warplate.",
    ],
    "Salamanders": [
        "Vulkan's fire and my own hands—no other blessing is needed.",
        "The forge knows its master. I tend what I have wrought.",
        "In Nocturne's heart, we learn to rely upon ourselves.",
    ],
    "Imperial Fists": [
        "Dorn built his walls alone when needed. So do I.",
        "Stone and iron bend to my will; I need no other hand.",
        "The Praetorian taught self-reliance. I honor that lesson.",
    ],
    "Space Wolves": [
        "The lone wolf maintains his own fangs.",
        "No pack needed for this hunt—the rite is mine.",
        "Fenris bred self-reliance into my bones.",
    ],
    "Blood Angels": [
        "By Sanguinius, I hold the Thirst at bay with my own hands.",
        "The angel's grace flows through my work upon myself.",
        "Baal's nobility demands I tend my own perfection.",
    ],
    "Dark Angels": [
        "Some secrets are kept even from the forge. This rite is one.",
        "The Lion trusted few; I trust only myself for this.",
        "In solitude, the Unforgiven find their own absolution.",
    ],
    "Dragonspears": [
        "The memories of fallen brothers guide my hand upon my own armor.",
        "Self-reliance is the hunter's way—I tend what carries me to the kill.",
        "Vulkan's sons learn to forge alone; I honor that teaching.",
    ],
    "Necropolis Hawks": [
        "In the choking dust of ruins, I maintain my own armor—pragmatic and efficient.",
        "The urban hunter tends his own war-plate between city-fights.",
        "Corax's sons claim their own domains; I claim mastery over my armor.",
    ],
    "Raven Guard": [
        "From shadow I emerged; in shadow I bless my own war-plate.",
        "Corax worked alone when stealth demanded. So do I.",
        "The silent hand tends its own talons.",
    ],
    "Ultramarines": [
        "The Codex permits self-maintenance. I exercise that right.",
        "Guilliman's wisdom: know thyself, tend thyself.",
        "Macragge's sons are trained to be complete. I am complete.",
    ],
    "White Scars": [
        "The lone rider tends his own mount on the endless steppe.",
        "Speed demands self-reliance—no time to wait for others.",
        "The Khan rode alone when needed. So do I bless alone.",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Service Studs Announcement Components
# ─────────────────────────────────────────────────────────────────────────────
# Service studs mark extended service to the Long Watch. Marines earn them
# through time served AND AAR points accumulated. The announcements are
# flavorful and RP-oriented, incorporating rank, home chapter, and milestone.

# Chapter-specific service stud flavor - how each chapter views/honors service marks
CHAPTER_STUDS_FLAVOR: Dict[str, List[str]] = {
    "Angels of Defiance": [
        "The Unforgiven stand defiant; your studs mark battles where retreat was never considered.",
        "Each stud bears witness to the Lion's unyielding legacy—defiance in the face of all enemies.",
        "Your service marks honor the hunt eternal; the Fallen shall know no sanctuary.",
    ],
    "Angels of Vengeance": [
        "Each stud marks another debt repaid to the Lion's memory.",
        "The Unforgiven count your studs among the honors earned in penance.",
        "Your service marks shine like the Lion's own resolve.",
    ],
    "Black Templars": [
        "Your studs are earned in the fires of the Eternal Crusade.",
        "No pity, no remorse—only the marks of endless war upon your brow.",
        "The Emperor's Champion would nod at such dedication.",
    ],
    "Bleeding Hearts": [
        "Each stud is a fang torn from the xenos—trophies of the hunt eternal.",
        "The Rage walks close, yet your marks proclaim discipline over annihilation.",
        "For those we sacrifice, your studs shine through the martyr's curse.",
    ],
    "Blood Angels": [
        "By the blood of Sanguinius, your service marks are sanctified.",
        "Your studs gleam with the nobility of Baal.",
        "Each mark holds back the darkness within—service is your salvation.",
    ],
    "Blood Ravens": [
        "Knowledge accumulated, service recorded—your studs speak of both.",
        "The Librarius records your marks alongside your collected wisdom.",
        "Each stud is a chapter in your quest for knowledge.",
    ],
    "Brazen Minotaurs": [
        "Each stud is a bastion breached—your marks proclaim the siege eternal.",
        "Methodical fury burns bright; your studs gleam like bronze in the sun.",
        "The bull charges ever onward—your service marks the walls that fell before you.",
    ],
    "Carcharodons": [
        "From the void's depths, your service marks emerge.",
        "Silent and relentless—your studs speak where words cannot.",
        "The Outer Dark has forged these marks upon you.",
    ],
    "Cowled Wardens": [
        "The Unforgiven mark your service in pursuit of the Fallen.",
        "Your studs gleam beneath the cowl; the Lion takes note.",
        "From the Sirikoid Belt, your marks proclaim the hunt eternal.",
    ],
    "Crimson Fists": [
        "Rynn's World remembers—your studs honor the fallen.",
        "The fist of Dorn is strengthened by your service.",
        "Each mark is a defiant strike against those who would see us fall.",
    ],
    "Dark Angels": [
        "The Inner Circle takes note of your accumulated service.",
        "Your studs speak of secrets kept and duties fulfilled.",
        "The Lion watches; your marks do not go unnoticed.",
    ],
    "Dark Krakens": [
        "From the deep places, your service rises to be marked.",
        "The abyssal void reflects in each earned stud.",
        "Pressure and darkness forge these marks of honor.",
    ],
    "Dragonspears": [
        "Each stud holds the memory of a brother consumed—their sacrifice endures in you.",
        "Vulkan's fire and the hunt eternal mark your service upon the stars.",
        "Fleet-born, Ork-slayer—your studs proclaim a legacy written in Greenskin blood.",
    ],
    "Death Spectres": [
        "Between life and death, your service is eternal.",
        "The shroud parts to reveal your accumulated marks.",
        "Each stud pierces the veil of mortality.",
    ],
    "Epsilon Paladins": [
        "Each stud shines silver and gold—proof of honour earned in Dorn's name.",
        "The Paladins count your marks among the bastions held and battles won.",
        "For Duty fulfilled, your service studs gleam like the Paladin's own warplate.",
    ],
    "Exorcists": [
        "Thrice-tested, your studs proclaim purity of service.",
        "No daemon can claim one whose brow bears such marks.",
        "Your service is warded against the Warp itself.",
    ],
    "Flesh Tearers": [
        "The Red Thirst is held at bay by such devoted service.",
        "Fury tempered by discipline—your studs attest to both.",
        "Amit himself would honor such marks of controlled wrath.",
    ],
    "Genesis Chapter": [
        "Guilliman's purity flows through your earned marks.",
        "The Codex records such dedication with approval.",
        "Your studs reflect the Primarch's own commitment to excellence.",
    ],
    "Hawk Lords": [
        "Swift as the raptor, yet enduring—your studs prove both.",
        "The skies of countless worlds have witnessed your service.",
        "Each mark a feather in your chapter's proud plumage.",
    ],
    "Hospitallers": [
        "Healers and warriors both—your studs honor those saved and those avenged.",
        "The Hospitaller's vow endures in each mark upon your brow.",
        "Mercy and wrath in balance—your service studs attest to both.",
    ],
    "Imperial Fists": [
        "Dorn's own fortitude is measured in your studs.",
        "Stone and iron—your service stands unbreakable.",
        "The walls of Terra themselves honor such marks.",
    ],
    "Iron Hands": [
        "The flesh may be weak, but your service is steel.",
        "Your studs are data-points of unwavering duty.",
        "The machine appreciates such logical dedication.",
    ],
    "Iron Hounds": [
        "Relentless as the hunt—your studs mark each pursuit to the end.",
        "Orinus breeds no weakness; your marks prove Guilliman's lineage.",
        "The pack honors your enduring service until every foe is slain.",
    ],
    "Iron Ravens": [
        "Silent as shadow, enduring as iron—your studs mark both cunning and strength.",
        "The raven's wisdom and the machine's precision are etched upon your brow.",
        "From darkness, your service emerges tempered in steel.",
    ],
    "Iron Snakes": [
        "The waters of Ithaka witness your marks—each stud a testament to the phratry.",
        "Your studs gleam like pearls won from Ithaka's depths.",
        "For the Reef Stars and the Emperor, your service is inscribed in adamantium.",
    ],
    "Knights of the Raven": [
        "In cunning and patience, your studs are earned.",
        "Each mark a stratagem successfully executed.",
        "The Raven's wisdom shines through your service.",
    ],
    "Lamenters": [
        "Though cursed, your studs shine with undimmed hope.",
        "For those we cherish—each mark a sacrifice willingly made.",
        "Your service defies the doom that follows.",
    ],
    "Marines Errant": [
        "The void is your home; your studs chart a quest across the stars.",
        "Dorn's wandering sons earn marks far from Terra's light.",
        "Each stud a waypoint on the eternal errantry of duty.",
    ],
    "Mentors": [
        "Precision and wisdom mark each earned stud.",
        "Your service is a lesson to those who follow.",
        "Each mark encodes tactical excellence.",
    ],
    "Minotaurs": [
        "The fury of the bull is measured in your studs.",
        "Your marks proclaim wrath harnessed and directed.",
        "The bronze glare of your service intimidates all foes.",
    ],
    "Necropolis Hawks": [
        "Building by building, your studs claim domains for the Emperor.",
        "Stoic and efficient—your marks speak of city-fights endured and won.",
        "Corax's shadow falls upon the urban sprawl; your service clears every ruin.",
    ],
    "Raptors": [
        "Silent, lethal, enduring—your studs speak of all three.",
        "Each mark earned in shadows and patience.",
        "The pragmatic path leads to these honors.",
    ],
    "Raven Guard": [
        "From shadow, your accumulated service emerges.",
        "Corax's patience is reflected in your studs.",
        "Silent duty—each mark speaks louder than words.",
    ],
    "Red Scorpions": [
        "Purity verified—your studs meet the Apothecary's standards.",
        "Each mark subjected to the most exacting scrutiny.",
        "Your service is as pure as your gene-seed.",
    ],
    "Red Templars": [
        "Speed and fury—Dorn's sons earn studs at rapid pace.",
        "The momentum of your service honors the Praetorian.",
        "Unyielding as the Fist, swift as the blade—your marks attest.",
    ],
    "Salamanders": [
        "Vulkan's flame forges each mark upon your brow.",
        "Into the fires of service, your studs emerge tempered.",
        "Each mark protects those who cannot protect themselves.",
    ],
    "Scythes of the Emperor": [
        "Sotha remembers—each stud honors the brothers who fell.",
        "The harvest of your service defies the Great Devourer.",
        "From near-extinction, your marks proclaim survival and vengeance.",
    ],
    "Sons of Medusa": [
        "Logic and steel calculate your accumulated marks.",
        "Your studs are precise increments of duty.",
        "The machine-spirit approves this mathematical devotion.",
    ],
    "Space Wolves": [
        "The Fang howls approval at your accumulated marks!",
        "Fenrisian sagas will speak of such enduring service.",
        "Each stud a wolf-tooth in your saga of war.",
    ],
    "Storm Giants": [
        "The giant's strength is measured in your studs.",
        "Towering might forges these marks upon your brow.",
        "At close quarters your service is proven; your marks speak of victories hard-won.",
    ],
    "Tempestuous Angels": [
        "The Emperor's Altruists mark your service—each stud a life defended.",
        "From Drossmire's ashes, your studs honor those who fell protecting the people.",
        "Vulkan's fire tempers your devotion; your marks shine with the Salamanders' legacy.",
    ],
    "The Drakes": [
        "Fire-cleansed, your service marks emerge purified.",
        "Each stud forged in the dragon's flame.",
        "Your marks burn bright with dedication.",
    ],
    "Ultramarines": [
        "Guilliman's Codex approves such measured service.",
        "Theoretical and practical unite in your studs.",
        "Macragge honors your steadfast accumulation of duty.",
    ],
    "White Scars": [
        "The wind of Chogoris carries word of your marks.",
        "Swift as lightning, yet your service endures.",
        "Each stud earned on the endless hunt.",
    ],
    "Black Shield": [
        "Your past forgotten, but your service remembered forever.",
        "These marks speak only of the Long Watch—nothing before.",
        "Anonymous duty earns marks that speak louder than any lineage.",
    ],
}

# Ordo Xenos / Deathwatch-wide honor phrases (tiered by service studs)
# Tier 1 (1-3 studs): Foundational acknowledgments of watch membership
ORDO_XENOS_HONORS_TIER1: List[str] = [
    "The Ordo Xenos records {possessive} vigilance against the alien threat.",
    "{possessive_cap} service to the Long Watch brings honor to the Deathwatch.",
    "Watch Fortress Jericho acknowledges {possessive} presence in the Long Watch.",
    "The Long Watch welcomes those steadfast in duty.",
    "{possessive_cap} place among the Deathwatch is cemented by service.",
    "The Vigil takes note of those who stand firm.",
    "Jericho's halls hear {possessive} name spoken in service.",
]

# Tier 2 (4-11 studs): Formal record-keeping and established honor
ORDO_XENOS_HONORS_TIER2: List[str] = [
    "The Ordo Xenos archives record {possessive} steadfast vigilance against the xenos.",
    "Watch Fortress Jericho's ledgers mark {possessive} exceptional service and dedication.",
    "The Vigil Eternal inscribes {possessive} deeds in adamantium records.",
    "By the Vigil Oathstone, {possessive} commitment is formally recognized.",
    "The Deathwatch itself stands stronger for {possessive} continued presence.",
    "The Long Watch is strengthened by warriors such as {object}.",
    "Inquisitorial records acknowledge one whose vigilance spans the years.",
    "{possessive_cap} service echoes through corridors of the Fortress itself.",
]

# Tier 3 (12-16 studs): Supreme honors and legendary status
ORDO_XENOS_HONORS_TIER3: List[str] = [
    "The Ordo Xenos bows before one whose vigilance spans decades of endless war.",
    "Watch Fortress Jericho's highest honors are inscribed upon {possessive} name in perpetuity.",
    "The very archives of the Deathwatch tremble at the magnitude of {possessive} service.",
    "By the Vigil Oathstone, the Inquisition itself takes note of legendary duty.",
    "The Long Watch shall sing of {possessive} deeds until the stars themselves fade.",
    "Only legends of the Deathwatch stand so marked; {possessive} name echoes eternal.",
    "The Machine God itself records {possessive} deeds in the holiest data-vaults of the Imperium.",
    "Generations hence, brothers will speak {possessive} name in reverence and awe.",
]

# Rank-specific commentary on service studs - how different ranks view this achievement
RANK_STUDS_COMMENTARY: Dict[str, List[str]] = {
    # High Command - formal commendations
    "Watch Master": [
        "The Watch Master's own ledgers record this milestone.",
        "From the throne of Jericho, your service is acknowledged.",
    ],
    "High Chaplain": [
        "The Reclusiam's spiritual records mark this devotion.",
        "Your soul's dedication is measured in these studs.",
    ],
    "Chief Apothecary": [
        "The Apothecarion's archives log another milestone of service.",
        "Gene-seed purity and service devotion—both are recorded.",
    ],
    "Forgemaster": [
        "The Armorium's cogitators record this data-point of dedication.",
        "Machine-spirits sing of your accumulated service.",
    ],
    "Castellan": [
        "The Fortress's own walls bear witness to your enduring vigilance.",
        "Each mark upon your brow is a bastion held, a threat repelled.",
    ],
    # Dreadnoughts - eternal service beyond mortal flesh
    "Venerable Dreadnought": [
        "The Ancient's war-casket bears witness to service spanning ages.",
        "Centuries entombed cannot diminish such devotion—the sarcophagus records all.",
        "Your studs predate the living memory of the Watch—legend made manifest.",
    ],
    "Honored Dreadnought": [
        "Even death cannot halt your accumulation of honor.",
        "The Dreadnought's service transcends mortal limitation—these marks endure.",
        "Your sarcophagus preserves not just flesh, but a legacy of endless duty.",
    ],
    "Interred Brother": [
        "Though dormant, your service studs gleam eternal in the Fortress's halls.",
        "The marks you earned in war await your awakening—they are not forgotten.",
        "Interred, but never diminished—your studs speak of battles past and future.",
    ],
    # Senior Officers - respectful acknowledgments
    "Watch Captain": [
        "Company records reflect this commendable service.",
        "Your captain's scrolls mark another milestone.",
    ],
    "Watch Lieutenant": [
        "The shield-bearer's service strengthens the Watch.",
        "Lieutenants of such dedication are the Watch's backbone.",
    ],
    # Specialists - domain-specific observations
    "Watch Chaplain": [
        "The Emperor witnesses this faithful service.",
        "Your spiritual fortitude is marked in adamantium.",
    ],
    "Watch Apothecary": [
        "Healer and warrior—your dual service is honored.",
        "The Narthecium bears witness to your dedication.",
    ],
    "Watch Librarian": [
        "The Warp itself cannot deny such marks of service.",
        "Psychic focus and duty align in your accumulated studs.",
    ],
    "Watch Techmarine": [
        "The Omnissiah records this devotion in sacred data.",
        "Your service is a litany of binary perfection.",
    ],
    # Line ranks - appropriate recognition
    "Watch Sergeant": [
        "A Sergeant whose studs teach by example.",
        "Leadership tempered by long service.",
    ],
    "Watch Veteran": [
        "Veteran status confirmed in adamantium and honor.",
        "The marks of a true warrior of the Long Watch.",
    ],
}

# Venerations based on PIP TYPE earned (not total count)
# Applied when earning plasteel (⚬) or auramite (●) studs
# Plasteel: frequent earns, larger pool to avoid repetition (~25 entries)
SERVICE_STUDS_VENERATIONS_PLASTEEL: List[str] = [
    "Your service studs gleam with the promise of deeds yet to come.",
    "The marks upon your brow attest to proven commitment.",
    "Your service studs speak of steadfast duty to the Long Watch.",
    "The studs upon your temple record battles won and trials endured.",
    "Your marks of service command respect among your brothers.",
    "Each stud tells of campaigns fought and enemies destroyed.",
    "The machine-spirit recognizes one whose service is proven.",
    "Your studs speak of dedication to the Emperor's will.",
    "The Long Watch has marked you as a warrior of worth.",
    "Your service is recorded in adamantium upon your brow.",
    "Another mark is earned—your brow swells with honor.",
    "The Fortress takes note of your accumulating marks.",
    "With each plasteel stud, your legacy grows.",
    "Combat after combat, your marks multiply.",
    "The Emperor's work continues through your steadfast service.",
    "Your studs are born of countless hours in the Long Watch.",
    "Patience and duty are reflected in your marks.",
    "The machine-spirit senses a warrior of consistency.",
    "Your studs speak of trials endured and overcome.",
    "Honor accumulates upon your brow, stud by stud.",
    "The Watch records your name with each earned mark.",
    "Your plasteel studs proclaim a brother proven in duty.",
    "Each mark is a step upon the path of service.",
    "The stubborn persistence of the righteous is etched upon you.",
    "From years of vigilance, these studs are born.",
]

# Auramite: earned every 4 plasteel, focused pool (~10 entries)
SERVICE_STUDS_VENERATIONS_AURAMITE: List[str] = [
    "Your service studs rival those of the Ancients themselves.",
    "The studs upon your brow are a saga written in silver and blood.",
    "Even the machine-spirits whisper reverence for one so marked by duty.",
    "Your service marks proclaim a living legend of the Deathwatch.",
    "The Omnissiah himself takes note of such devotion to duty eternal.",
    "Your service studs proclaim a warrior whose experience shapes the Watch itself.",
    "Few bear such marks of enduring service—honor is yours by right.",
    "The weight of your studs reflects the weight of your deeds.",
    "Younger brothers look to your studs and see their own path illuminated.",
    "Your marks of service are a legacy etched in adamantium and honor.",
]

# Tiered milestone intros based on stud number being earned
# Tier 1: 1-3 (first marks), Tier 2: 4-11 (seasoned), Tier 3: 12-16 (legendary)
# First stud gets special templates that don't say "another"
SERVICE_STUDS_MILESTONE_FIRST: List[str] = [
    "The Apothecarion stands ready to affix your first mark of service.",
    "Your dedication has earned your first stud—seek the Apothecary's ministrations.",
    "The first mark is earned through steadfast duty.",
    "The Watch marks your service with your inaugural stud.",
    "Your commitment to the Long Watch earns its first visible recognition.",
]

SERVICE_STUDS_MILESTONE_TIER1: List[str] = [
    "The Apothecarion stands ready to affix your mark of service.",
    "Your dedication has earned a new stud—seek the Apothecary's ministrations.",
    "Another mark is earned through steadfast duty.",
    "The Watch marks your continued service with another stud.",
    "Your commitment to the Long Watch merits recognition.",
]

SERVICE_STUDS_MILESTONE_TIER2: List[str] = [
    "A seasoned warrior earns another mark—the Apothecarion awaits.",
    "Your growing collection of studs speaks of exceptional dedication.",
    "The Watch takes note: another stud joins your constellation of service.",
    "Veterans of the Long Watch honor one whose marks multiply.",
    "Your brow bears witness to campaigns beyond counting.",
]

SERVICE_STUDS_MILESTONE_TIER3: List[str] = [
    "LEGENDARY SERVICE! Even the Apothecarion's eldest brothers pause to witness this.",
    "An auramite stud! The highest honor the Watch can bestow!",
    "The Watch Fortress itself trembles at such monumental service!",
    "Legends walk among us—your marks proclaim it to all!",
    "The chronicles of Jericho inscribe this momentous milestone!",
]

# Special milestone announcements for exact stud numbers
# Max 4 auramite studs (16 plasteel total)
# 1 stud = 25 years, 4 studs = 100 years, 16 studs = 400 years
SERVICE_STUDS_SPECIAL_MILESTONES: Dict[int, str] = {
    1: "**FIRST SERVICE STUD** — Twenty-five years sworn to the Long Watch. The Vigil has claimed another soul.",
    4: "**FIRST AURAMITE STUD** — A century of service. Plasteel gives way to auramite—the mark of a true veteran.",
    16: "**FOURTH AURAMITE STUD** — Four hundred years. Few have ever borne such weight of duty. A living legend of the Deathwatch.",
}

# Deathwatch-themed opening phrases for service stud announcements
# Note: {name} uses the stripped display name (no rank/studs) in _get_service_studs_announcement
DEATHWATCH_STUD_OPENINGS: List[str] = [
    "Hear this, Brothers! **{name}** is brought before you for marking!",
    "The Long Watch turns its gaze—witness as **{name}** earns a new stud!",
    "The Fortress records this honor: **{name}** stands ready for the marking!",
    "By the Vigil Oathstone, **{name}** approaches the Apothecarion for the sacred rite!",
    "The chronicles inscribe a new mark. **{name}**, the path to honor lies before you!",
    "Heed this proclamation! The Apothecarion stands ready to affix the mark upon **{name}**!",
    "The Watch Eternal bears witness—**{name}** has earned another stud through devoted service!",
]

# Deathwatch themed closings
DEATHWATCH_STUD_CLOSINGS: List[str] = [
    "Vigilus Aeterna. The Watch endures.",
    "For the Emperor and the Long Watch!",
    "The Vigil continues. Ave Imperator.",
    "By bolt and blade, the Watch persists.",
    "In Vigilance, Eternal.",
]

# Oathsworn eligibility flavor text
# Openings declare a Watch Veteran has earned the right to be considered for Oathsworn
OATHSWORN_OPENINGS: List[str] = [
    "The Vigil Oathstone trembles! **{name}** has proven worthy of elevation!",
    "The Watch Eternal bears witness—**{name}** stands ready for the sacred oath!",
    "Hearken, Brothers! **{name}** has walked the Long Watch and earned the right to swear!",
    "The chronicles blaze with glory! **{name}** approaches the threshold of the Oathsworn!",
    "By bolt, blade, and blood, **{name}** has earned a seat among the Oathsworn!",
    "The machine-spirits whisper reverence—**{name}** is called to take the Oath!",
]

# Proclamations about what it means to become Oathsworn
OATHSWORN_PROCLAMATIONS: List[str] = [
    "Three marks of service gleam upon their brow, testament to campaigns beyond counting.",
    "Plasteel studs proclaim unwavering devotion to the Long Watch and the Emperor's cause.",
    "Through countless engagements, they have proven their value to the Watch Eternal.",
    "Their service studs speak louder than any proclamation—duty fulfilled, honor earned.",
    "The Watch has weighed their deeds and found them worthy of this sacred consideration.",
    "Steadfast service and proven valor have brought them to this threshold of honor.",
]


# ─────────────────────────────────────────────────────────────────────────────
# Armor Integrity / Forge subsystem data (extracted from bot.py)
# ─────────────────────────────────────────────────────────────────────────────

ARMOR_DAMAGE_TIERS = ["damaged", "compromised", "critical"]
ARMOR_DAMAGE_PENALTIES = {"damaged": 1, "compromised": 2, "critical": 3}  # Legacy fixed

# Mission name to planet mapping (for armor alert debrief)
MISSION_TO_PLANET = {
    "inferno": "Kadaku",
    "termination": "Kadaku",
    "normal_siege": "Kadaku",
    "hard_siege": "Kadaku",
    "decapitation": "Avarax",
    "vox liberatis": "Avarax",
    "ballistic engine": "Avarax",
    "exfiltration": "Avarax",
    "reclamation": "Avarax",
    "disruption": "Avarax",
    "reliquary": "Demerium",
    "fall of atreus": "Demerium",
    "obelisk": "Demerium",
    "vortex": "Demerium",
}

# Probability distributions for AAR penalties per damage tier
# Format: {tier: {penalty: probability}} where probabilities must sum to 1.0
# Penalty 0 = no penalty, 1-4 = AAR reduction
ARMOR_PENALTY_PROBABILITIES = {
    None: {0: 1.0},  # Nominal: no penalty
    "damaged": {0: 0.90, 1: 0.085, 2: 0.010, 3: 0.005},  # 10% penalty chance
    "compromised": {0: 0.835, 1: 0.10, 2: 0.05, 3: 0.015},  # ~17% penalty chance
    "critical": {0: 0.75, 1: 0.085, 2: 0.10, 3: 0.065},  # 25% penalty chance
    "fractured": {0: 0.70, 1: 0.05, 2: 0.085, 3: 0.10, 4: 0.065},  # 30% penalty chance
}

# Detection alert chances per AAR while damaged (early warning system)
# Roll checked each AAR; if successful, sends detection alert before penalty occurs
# Only one detection alert per tier (tracked in armor state)
ARMOR_DETECTION_CHANCES = {
    "damaged": 0.20,  # 20% chance per AAR
    "compromised": 0.35,  # 35% chance per AAR
    "critical": 0.50,  # 50% chance per AAR
    "fractured": 1.0,  # 100% - always alert
}

# Scan miss chances for armor_status command (brothers may not show)
# Flat 20% undetected chance across all tiers except fractured
ARMOR_SCAN_MISS_CHANCES = {
    "nominal": 0.20,  # 20% chance to miss
    "damaged": 0.20,  # 20% chance to miss
    "compromised": 0.20,  # 20% chance to miss
    "critical": 0.20,  # 20% chance to miss
    "fractured": 0.0,  # 0% - always visible
}

# Predictive detection chances for nominal brothers based on cycle count
# Used to warn Techmarines of impending damage risk
ARMOR_SCAN_PREDICTIVE_TIERS = [
    {"min": 0, "max": 4, "chance": 0.0},  # No warning in safe zone
    {"min": 5, "max": 9, "chance": 0.10},  # 10% chance to detect risk
    {"min": 10, "max": 14, "chance": 0.25},  # 25% chance
    {"min": 15, "max": 19, "chance": 0.40},  # 40% chance
    {"min": 20, "max": None, "chance": 0.60},  # 60% chance
]

# Intensive scan cost (armory points via requisition_supplies)
INTENSIVE_SCAN_COST = 20

# Default probability tiers (can be overridden in config)
# Gaps shrink as cycles increase to create mounting pressure
DEFAULT_ARMOR_PROBABILITY_TIERS = [
    {
        "min": 0,
        "max": 4,
        "chance": 0.0,
        "damage_weights": {"damaged": 100, "compromised": 0, "critical": 0},
    },
    {
        "min": 5,
        "max": 9,
        "chance": 0.02,
        "damage_weights": {"damaged": 90, "compromised": 8, "critical": 2},
    },
    {
        "min": 10,
        "max": 14,
        "chance": 0.08,
        "damage_weights": {"damaged": 80, "compromised": 15, "critical": 5},
    },
    {
        "min": 15,
        "max": 19,
        "chance": 0.20,
        "damage_weights": {"damaged": 65, "compromised": 25, "critical": 10},
    },
    {
        "min": 20,
        "max": None,
        "chance": 0.40,
        "damage_weights": {"damaged": 50, "compromised": 35, "critical": 15},
    },
]

# Grace period defaults
DEFAULT_ARMOR_GRACE_PERIOD_MIN_POINTS = 100
DEFAULT_ARMOR_GRACE_PERIOD_MIN_DAYS = 7

# Fracture threshold (AAR submissions at critical before spirit fractures)
DEFAULT_ARMOR_FRACTURE_THRESHOLD = 3

# Flavor text for armor status in forge_rite
ARMOR_STATUS_NOMINAL = {
    "plate": "NOMINAL",
    "spirit": "STABLE",
    "rite": "MAINTENANCE",
}
ARMOR_STATUS_DAMAGED = {
    "plate": "MINOR WEAR",
    "spirit": "STABLE",
    "rite": "RESTORATION",
}
ARMOR_STATUS_COMPROMISED = {
    "plate": "STRUCTURAL STRESS",
    "spirit": "AGITATED",
    "rite": "EMERGENCY RITES",
}
ARMOR_STATUS_CRITICAL = {
    "plate": "CRIT FAIL",
    "spirit": "UNSTABLE",
    "rite": "STABILIZATION",
}
ARMOR_STATUS_FRACTURED = {
    "plate": "CRIT FAIL",
    "spirit": "FRACTURED",
    "rite": "RE-CONSECRATION",
}

# Flavor text for spirit restoration (was damaged but not fractured)
SPIRIT_RESTORATION_PHRASES = [
    "Sacred oils soothe worn servos. The bond holds. What was stressed is now restored.",
    "The machine spirit's agitation fades as blessed unguents are applied. Integrity restored.",
    "Damaged systems repaired, seals renewed. The spirit settles into watchful calm.",
    "Rites of maintenance complete. The armor remembers its purpose.",
    "The Litany of Restoration calms the wounded spirit. Pain becomes memory; vigilance returns.",
    "Blessed lubricants ease damaged joints. The spirit's anger subsides into quiet readiness.",
    "Micro-fractures sealed, war-damage mended. The machine spirit exhales gratitude in binharic code.",
    "The Rite of Soothing is complete. What was wounded now stands whole.",
    "Damaged neural pathways rerouted. The spirit's core processes stabilize.",
    "Incense and unguents appease the troubled spirit. The bond endures.",
]

# Flavor text for spirit re-consecration (spirit fractured)
SPIRIT_RECONSECRATION_PHRASES = [
    "The previous spirit has departed, its bond severed through neglect. A new spirit must learn to trust you anew. This is not celebration. This is beginning again.",
    "What was bonded is now lost. Fresh spirit bound to old armor. The Omnissiah grants no second chances—only new beginnings.",
    "The machine spirit you knew is gone. Another takes its place, wary and untested. Earn its trust.",
    "Re-consecration complete. The new spirit knows nothing of your deeds. Prove yourself worthy once more.",
    "The death-cry of the old spirit echoes in the cogitator's memory. A new presence stirs—untrusting, watchful.",
    "Neglect has consequences. The old spirit fled into the data-void. This new one regards you with cold suspicion.",
    "The soul that knew you is gone. Another inhabits this warplate now—a stranger wearing familiar armor.",
    "Through sacred rites, a dormant spirit is awakened and bound. It does not know you. It does not yet trust you.",
    "The Rite of Severance is spoken. The Rite of Binding follows. One spirit dies; another is born. Begin again.",
    "The armor's old spirit has been released to the Motive Force. Its replacement must learn your worth from nothing.",
]

# Ambient messages for the forge channel (posted when forge is quiet)
FORGE_AMBIENT_MESSAGES = [
    "*The Forge rests in prepared silence.*",
    "*Servo-arms hang still, awaiting the next supplicant.*",
    "*Incense coils upward from dormant censers.*",
    "*Sacred oils gleam in their blessed containers, awaiting use.*",
    "*The hum of cogitators fills the space—ever watchful, ever patient.*",
    "*Machine spirits slumber in their blessed housings, dreams of duty.*",
    "*The smell of sacred unguents permeates the chamber.*",
    "*Somewhere in the Forge, a servo-skull catalogues ancient rites.*",
    "*The Forge awaits those who honor the Omnissiah.*",
    "*Cooling vents exhale measured breaths. The Forge persists.*",
    "*Data-candles flicker in alcoves, their light steady and true.*",
    "*The hiss of pneumatics fades. Silence returns.*",
    "*Augury crystals pulse with dormant potential.*",
    "*The Watch Techmarines' vigil continues, eternal and unwavering.*",
    "*In the deep places of the Forge, wisdom accumulates.*",
]
