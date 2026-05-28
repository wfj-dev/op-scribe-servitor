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
    "Carmine Blades": "Sanguinius's truth runs crimson in your veins—what Astorath revealed, your armor now proclaims without shame.",
    "Celestial Lions": "Pride of Elysium and vengeance for Armageddon—your armor endures against every betrayal.",
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
    "Imperius Reavers": "The Eastern Fringe demands warriors without mercy—your armor carries the Stormbringers' unyielding defiance to every xenos threat.",
    "Iron Hands": "The flesh is weak, but your armor is the strength of iron.",
    "Iron Ravens": "Silent shadow, tempered iron—your armor moves unseen and strikes with precision.",
    "Iron Snakes": "The waters of Ithaka anoint your armor; as the Snakes strike, so shall you.",
    "Iron Hounds": "Guilliman's hounds pursue without relent; your armor knows no surrender.",
    "Iron Lords": "Iron of vigil, iron of grip—your armor upholds the Iron Watch; the Grendl Stars hold because you do.",
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
    "Tome Keepers": "Every battle is another chapter written upon these plates—your armor bears the accumulated knowledge of Istrouma's aeons.",
    "Ultramarines": "The Codex guides us; your armor upholds Guilliman's legacy.",
    "White Scars": "The wind of Chogoris propels your armor to swift victory.",
    "Wolfspear": "Your oaths are carved into bolt and plate alike—the Dark Terror strides forward armored in Fenris's iron will.",
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
    "Iron Lords": [
        "The Iron Hands taught self-sufficiency; the Iron Lords have held that vigil for three thousand years.",
        "My vigil demands I trust only iron-hard certainty—my own hands upon my own warplate.",
        "Iron of vigil, iron of grip. Who better than I to bless what I alone have held?",
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
    "Imperius Reavers": [
        "Guilliman's teachings reach the Eastern Fringe—self-reliance in the void is not deviation, it is doctrine.",
        "The Vidar Sector does not wait for aid, and I do not wait for another's hand upon my warplate.",
        "The Reavers seize what they require; I claim this blessing with my own hands.",
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
    "Carmine Blades": [
        "The blood-curse runs in my armor as in my veins—I sanctify what Astorath revealed.",
        "Haldroth bred self-reliance into my bones; I need no other hand upon my warplate.",
        "The old ways of the Swords of Haldroth endure—I tend my own armor as the feral warrior tradition demands.",
    ],
    "Celestial Lions": [
        "Elysium's pride does not bow to misfortune; I tend my own armor and stand unbroken.",
        "Armageddon taught us endurance through betrayal; these rites I trust to my own hand.",
        "The lion still roars. I renew my warplate to carry that roar into battle.",
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
    "Tome Keepers": [
        "I have made detailed notes on this rite; the methodology is my own and the execution shall be as well.",
        "My personal tome records the blessing I now perform—what is written is what I do.",
        "The sons of Istrouma document everything; I document this rite as I conduct it, by my own hand.",
    ],
    "White Scars": [
        "The lone rider tends his own mount on the endless steppe.",
        "Speed demands self-reliance—no time to wait for others.",
        "The Khan rode alone when needed. So do I bless alone.",
    ],
    "Wolfspear": [
        "The lone wolf in the void must maintain its own fangs—no pack at hand, the rite is mine.",
        "My oaths are carved into this warplate; who better to renew the blessing than the one who swore them?",
        "Grimwolves trust no hearth but the one they carry—I tend my own armor in the dark between stars.",
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
    "Carmine Blades": [
        "Eight companies answered Baal's call; your studs honor the 643 who never left Baal Secundus.",
        "Long called Swords of Haldroth, now Carmine Blades—each mark a truth no longer concealed.",
        "Haldroth's feral blood runs hot in you; your studs are earned in the old ways, through carnage and survival.",
    ],
    "Celestial Lions": [
        "Each stud is a vow remembered—Elysium's lions do not forget their dead.",
        "Armageddon's ashes could not silence your chapter; your marks prove it still fights on.",
        "Pride and resolve endure in every service stud upon your brow.",
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
    "Imperius Reavers": [
        "Each stud marks another enemy of the Eastern Fringe denied their victory—the Stormbringers do not tire.",
        "Praedis Zeta stands because the Reavers endure; your marks honor every campaign fought on the Fringe's edge.",
        "The alien and the Daemon test the Vidar Sector without end—your service marks prove you have answered them, blow for blow.",
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
    "Iron Lords": [
        "Each stud marks another span of vigil in the Iron Grip—the Barghesi do not sleep, and neither do you.",
        "The Holdfasts of the Grendl Stars are built upon such endurance; your marks honor three thousand years of unyielding watch.",
        "The Iron Lords count service not in glory but in years of steel-willed duty—your studs are that count made manifest.",
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
    "Tome Keepers": [
        "Each stud is another chapter written in service—the Tome Keepers record every mark upon the flesh as they do every mark upon the page.",
        "The Closing of the Book shall come for all—your studs proclaim that your chapters are still being written.",
        "The scholars of Istrouma lived short lives so that knowledge endures; your marks honor every Keeper who could not live to earn theirs.",
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
    "Wolfspear": [
        "Each stud marks another hunt completed in the void—the pack's oaths are carved deeper into warplate and bone alike.",
        "Carved like runes upon ancient iron—your marks are oaths as much as honors, binding you to every packmate who fell before.",
        "The Dark Terror earns its name one mission at a time; your studs record the tally of prey taken and oaths kept.",
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


# ---------------------------------------------------------------------------
# Watch Veteran promotion announcement flavor text
# ---------------------------------------------------------------------------
WATCH_VETERAN_OPENINGS: List[str] = [
    "By blood and bolter, by vigil and void, **{name}** has earned the right to stand among the Honoured Veterans.",
    "The Long Watch does not grant rank lightly. **{name}** has proven the measure of an Astartes in full.",
    "The Watch has tested **{name}** and found them worthy. Let the brotherhood bear witness.",
    "Few among the Deathwatch reach this threshold—**{name}** has crossed it, forged in the fires of the Long Watch.",
    "In the crucible of Fortress Jericho's campaigns, **{name}** has emerged tempered and true.",
    "The Long Watch remembers every campaign, every mission, every sacrifice. Today it calls **{name}** Veteran.",
]

WATCH_VETERAN_PROCLAMATIONS: List[str] = [
    "The rank of Watch Veteran is not a gift—it is recognition that this warrior has already become indispensable.",
    "Watch Veteran. The title borne by those who have bled for the Watch and returned for more.",
    "The Watch Veteran stands where the Battle-Brother once stood, and looks back from a vantage of hard-won experience.",
    "Those who wear the Veteran's mark have already paid its price a hundred times over. Today, the Watch names the debt settled.",
    "Let the Long Watch know this name. Let the fortress remember. A Watch Veteran has been forged in the fires of Jericho.",
    "The measure has been taken and found worthy. The rank of Watch Veteran is granted without reservation.",
]

# ---------------------------------------------------------------------------
# Ardent Raider Ribbon announcement flavor text
# ---------------------------------------------------------------------------
ARDENT_RAIDER_OPENINGS: List[str] = [
    "The void holds secrets the Xenos guard jealously—until **{name}** reclaims them for the Watch.",
    "Among those who serve the Watch, **{name}** has proven a singular gift for the retrieval of knowledge most others cannot grasp.",
    "Intelligence is the sword arm of the Deathwatch, and **{name}** has shown mastery of it beyond all expectation.",
    "There are warriors who fight with bolter and blade—and those who fight with data. **{name}** stands among the finest of the latter.",
    "**{name}** has plundered the secrets of the Xenos and delivered them to the Watch—a deed of incalculable worth.",
]

ARDENT_RAIDER_PROCLAMATIONS: List[str] = [
    "The Ardent Raider Ribbon is bestowed upon those who have delivered the Watch's greatest advantage: knowledge of the enemy.",
    "Without the tireless work of specialists like this, the Deathwatch would lose its edge. The Watch does not forget.",
    "Archeotech retrieved. Xenoform data secured. The arsenal of Fortress Jericho is sharper for this warrior's service.",
    "To gather what the enemy most fears losing—their secrets—is a victory worth a hundred firefights.",
    "The Ribbon is earned not in a single act of glory but in the patient, relentless pursuit of every advantage.",
]

# ---------------------------------------------------------------------------
# Apothecarion Service Medal announcement flavor text
# ---------------------------------------------------------------------------
APOTHECARION_MEDAL_OPENINGS: List[str] = [
    "There are duties that transcend battle-glory, and **{name}** has answered the most sacred among them.",
    "The gene-seed of the fallen does not perish so long as warriors like **{name}** still draw breath.",
    "In the carnage of ferocious firefights, when others fought only to survive, **{name}** fought to preserve the Watch's future.",
    "The heaviest burden a Watch Brother can carry is not their weapon—it is the legacy of their fallen brothers. **{name}** carried it.",
    "**{name}** has done what few can, and fewer still dare: preserved the sacred gene-seed in the heart of battle.",
]

APOTHECARION_MEDAL_PROCLAMATIONS: List[str] = [
    "The Apothecarion Service Medal honors those who carry the weight of the Watch Fortress's future upon their shoulders.",
    "Though their bodies fall, their spirit must return to the Watch. This warrior has made it so—again and again.",
    "Let the names of the fallen be preserved. Let the Watch endure. It is done through acts such as these.",
    "In the fires of battle, when all else is chaos, this warrior answered the highest call of duty without hesitation.",
    "The gene-seed of Fortress Jericho is richer for this warrior's service. The Watch owes a debt that cannot be repaid.",
]

# ---------------------------------------------------------------------------
# Crimson Laurels announcement flavor text
# ---------------------------------------------------------------------------
CRIMSON_LAURELS_OPENINGS: List[str] = [
    "**{name}** has walked where others would not follow, and returned where others would not dare.",
    "There are warriors who seek battle—and there are warriors who become legend. **{name}** is the latter.",
    "The chronicles of Watch Fortress Jericho speak **{name}**'s name with the reverence reserved for the Watch's greatest reapers.",
    "Spoken of with whispered awe and dread. **{name}** has passed through the crucible that forges Crimson Laurels bearers.",
    "The Long Watch has many servants. **{name}** is something rarer still—a warrior of surpassing legend.",
]

CRIMSON_LAURELS_PROCLAMATIONS: List[str] = [
    "The Crimson Laurels are not merely an honor—they are testament to a warrior who has surpassed what any could ask of an Astartes.",
    "It is said the Laurels are crimson not through metallurgy, but from the oceans of blood it took to earn them.",
    "Legends in their own right, bearers of the Crimson Laurels are spoken of with whispered awe. Today a new name joins their ranks.",
    "To have seen what they have seen, and lived to tell the tale, takes a rare kind indeed. The Watch names this warrior that kind.",
    "The record is written. The measure has been taken. The Crimson Laurels are earned.",
]

# ---------------------------------------------------------------------------
# Chapter-specific lines for award announcements
# One line per chapter, keyed by HOME_CHAPTERS name.
# Used to append a chapter-flavored coda to the proclamation field.
# ---------------------------------------------------------------------------

WATCH_VETERAN_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven do not bow—and neither does a warrior who has earned the Veteran's mark.",
    "Angels of Vengeance": "The Lion's vengeance is patient; this veteran's service proves the same.",
    "Black Templars": "The Eternal Crusade forges veterans—this one has proven themselves worthy of the blade.",
    "Bleeding Hearts": "Through the hunt and the rage, this warrior has endured to stand among the Honored.",
    "Blood Angels": "The sons of Sanguinius bleed freely—and this veteran has shed enough to earn this mark.",
    "Blood Ravens": "Knowledge and valor together have earned this honor—the Blood Ravens' dual legacy serves well.",
    "Brazen Minotaurs": "Every bastion breached, every siege endured—the Minotaur earns their honors through relentless advance.",
    "Carcharodons": "Silence and service—the Void-born earn their marks without ceremony and without complaint.",
    "Carmine Blades": "The blood-curse has not broken this warrior—Baal demanded much, and this veteran gave more than most ever had to give.",
    "Celestial Lions": "Elysium's sons do not falter; this veteran stands as proof that the Lions endure.",
    "Cowled Wardens": "The cowl conceals the warrior—but their veteran's mark cannot be hidden.",
    "Crimson Fists": "From the ashes of Rynn's World, the Fists are reborn; this veteran continues that defiant legacy.",
    "Dark Angels": "The First Legion's resolve endures in this warrior—the veteran's mark is well earned.",
    "Dark Krakens": "From the deep, this warrior rises—proven by pressure and darkness alike.",
    "Dragonspears": "Fleet-born and fearless, this veteran hunts in the name of brothers consumed by the flame.",
    "Death Spectres": "Neither life nor death could deter this warrior—the Veteran's mark is only the beginning.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn—the Paladin's creed lives in every mission this veteran completed.",
    "Exorcists": "Thrice-proven against the Warp itself, this warrior has earned more than a title.",
    "Flesh Tearers": "The Red Thirst demands fury; the Long Watch demands discipline—this veteran has mastered both.",
    "Genesis Chapter": "Guilliman's purity endures in this warrior; the veteran's mark joins a legacy of measured excellence.",
    "Hawk Lords": "Swift to engage, swift to succeed—the Hawk Lords' speed of service earns swift recognition.",
    "Hospitallers": "Healer of the broken and breaker of the wicked—this veteran's service is beyond reproach.",
    "Imperial Fists": "Dorn's stone endures what others cannot—this veteran is proof of that heritage.",
    "Imperius Reavers": "The Eastern Fringe is a crucible without equal—this veteran emerged from it already proven before the Long Watch ever called them.",
    "Iron Hands": "The flesh proved adequate; the service proved exceptional—the Iron Hands would approve.",
    "Iron Hounds": "The hunt does not end until every quarry falls—this veteran knows no other way.",
    "Iron Lords": "The Iron Grip holds because warriors like this hold it—the Grendl Stars remain contained by such iron resolve.",
    "Iron Ravens": "In shadow and in silence, this warrior's service has spoken loud enough for all to hear.",
    "Knights of the Raven": "Patient, cunning, and proven—the Raven's own shadow falls upon those who earn this mark.",
    "Lamenters": "Cursed they may be, but this warrior's veteran status is a blessing none can deny.",
    "Marines Errant": "The void-wanderers find honor wherever duty takes them—this veteran has ranged far and served well.",
    "Mentors": "A veteran whose service is itself a lesson to those who serve alongside them.",
    "Minotaurs": "The bronze bull does not yield—and this veteran's service has proven that lineage true.",
    "Necropolis Hawks": "City after city reclaimed, ruin after ruin cleared—a veteran forged in the fires of urban war.",
    "Raptors": "Silent, lethal, patient—this veteran's marks speak where the Raptor never would.",
    "Raven Guard": "From shadow and silence, a veteran emerges—proof that endurance is the truest weapon.",
    "Red Scorpions": "Purity in gene-seed and purity in service—the Red Scorpions would find no fault here.",
    "Red Templars": "Fast as the blade, unyielding as the Fist—this veteran is Dorn's creed made flesh.",
    "Salamanders": "Vulkan's sons protect those who cannot protect themselves—and this veteran has done so, again and again.",
    "Scythes of the Emperor": "Sotha falls, but the Scythes endure—this veteran carries that spirit forward.",
    "Sons of Medusa": "Calculated, disciplined, relentless—the Sons of Medusa mark service in data and deed alike.",
    "Space Wolves": "The saga-skalds of Fenris would tell this warrior's deeds with pride.",
    "Storm Giants": "Towering in deed as in stature—this veteran's service befits the Giant's legend.",
    "Tempestuous Angels": "Drossmire's fire tempered this warrior—and their veteran's mark honors every soul they protected.",
    "The Drakes": "From the dragon's flame, a veteran emerges—scorched, tempered, and unbreakable.",
    "Tome Keepers": "The Tome Keepers would inscribe this warrior's deeds in the Chapter chronicles—a veteran whose book is far from closed.",
    "Ultramarines": "The Codex describes the ideal warrior; this veteran has proven it more than theory.",
    "White Scars": "The steppe wind does not remember the fallen; this veteran ensures the Watch will.",
    "Wolfspear": "The Dark Terror earns its name in the darkness between stars—this veteran has hunted there long enough to know every shadow by heart.",
    "Black Shield": "No lineage to claim—only deeds, and these deeds stand among the finest the Watch has witnessed.",
}

ARDENT_RAIDER_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven have always kept their secrets close—and recognized those who wrest others' from them.",
    "Angels of Vengeance": "The Lion's sons know the worth of intelligence in the war eternal.",
    "Black Templars": "Even the Crusade requires supply lines—and this warrior has filled them beyond measure.",
    "Bleeding Hearts": "The trophies of this hunt are not trophies of flesh, but of recovered secrets—both serve the Watch.",
    "Blood Angels": "The sons of Sanguinius are as gifted at the acquisition of knowledge as they are at its destruction.",
    "Blood Ravens": "The Blood Ravens know better than any the worth of recovered knowledge—this warrior honors that legacy.",
    "Brazen Minotaurs": "The siege yields its secrets to those who press hard enough—and this warrior pressed hardest.",
    "Carcharodons": "What the void swallows, this warrior reclaims—silent, patient, relentless.",
    "Carmine Blades": "The feral world of Haldroth breeds warriors who take what they need—this warrior has taken much in the Emperor's name.",
    "Celestial Lions": "The Lions know the worth of intelligence; hard lessons of betrayal taught them well.",
    "Cowled Wardens": "The hooded sons of the Lion have always valued what is hidden—and recognized those who uncover it.",
    "Crimson Fists": "Rynn's World was not lost for lack of will—this warrior ensures no future failure from lack of intelligence.",
    "Dark Angels": "The First Legion guards secrets jealously; this warrior has the rare gift of acquiring others'.",
    "Dark Krakens": "From the abyss, knowledge is dragged to the surface—this warrior excels at the dragging.",
    "Dragonspears": "Fleet-born hunters acquire not only blood but intelligence—this warrior has mastered both arts.",
    "Death Spectres": "Between the living and the dead lies intelligence most never reach—this warrior crosses that threshold.",
    "Epsilon Paladins": "For Dorn! Even the mightiest defense requires intelligence—this Paladin has provided it.",
    "Exorcists": "Bound against the Warp and armed with its secrets—this warrior has earned a rare honor.",
    "Flesh Tearers": "The berserker who also retrieves—a rarer breed, and a more dangerous one.",
    "Genesis Chapter": "Guilliman would approve: the Codex values intelligence as much as blade and bolter.",
    "Hawk Lords": "The raptor's eye misses nothing—and this warrior has returned with everything.",
    "Hospitallers": "Those who tend the wounded learn much; those who fight their enemies learn more—this warrior does both.",
    "Imperial Fists": "Every fortress requires supply; every war requires intelligence—Dorn built both into his doctrine.",
    "Imperius Reavers": "The Stormbringers of Praedis Zeta know every trick of the alien—this warrior has turned those tricks against the enemy and brought their secrets home.",
    "Iron Hands": "Data recovered, artefacts secured, the machine-spirit satisfied—the Iron Hands could ask no more.",
    "Iron Hounds": "The hound retrieves what the hunter needs—and this warrior retrieves it faster than any.",
    "Iron Lords": "The Iron Lords study every xenos foe with cold precision and absolute hatred—this warrior's intelligence serves the Watch's vigil as the Iron Grip serves the Grendl Stars.",
    "Iron Ravens": "Silent, precise, and laden with recovered intelligence—the Iron Raven's gift made manifest.",
    "Knights of the Raven": "The strategist's most valuable weapon is information—this warrior has delivered it in abundance.",
    "Lamenters": "Even the cursed may shine brightly in service—and this warrior's light is undimmed.",
    "Marines Errant": "The void-wanderers find what others cannot reach—and this warrior has ranged furthest of all.",
    "Mentors": "A warrior who demonstrates that gathering intelligence is as vital as drawing a blade.",
    "Minotaurs": "The Minotaur's advance yields not just ground but secrets—and this warrior yields more than most.",
    "Necropolis Hawks": "Every ruin holds secrets; this warrior has emptied them all.",
    "Raptors": "Patient, unseen, and rich with recovered intelligence—the Raptor's way perfected.",
    "Raven Guard": "From shadow, intelligence is plucked like fruit—and Corax's sons know every orchard.",
    "Red Scorpions": "Purity of purpose is reflected in this warrior's exceptional service to the Watch's arsenal.",
    "Red Templars": "Fast hands acquire much—and this warrior's acquisition record speaks for itself.",
    "Salamanders": "Vulkan's artisans value rare materials; this warrior delivers them in abundance.",
    "Scythes of the Emperor": "Against the Devourer, every weapon matters—this warrior has stacked the arsenal high.",
    "Sons of Medusa": "Systematic, thorough, and precise—the Sons of Medusa's doctrine yields this ribbon.",
    "Space Wolves": "A fine haul, fit for a Jarl's feast—the pack is better armed for this wolf's work.",
    "Storm Giants": "The giant's grasp is wide—and this warrior has filled it with everything the Watch requires.",
    "Tempestuous Angels": "To protect the people, you must first be armed—this warrior ensures neither ever fails.",
    "The Drakes": "Fire reveals and destroys—this warrior knows which deserves which.",
    "Tome Keepers": "The sons of Istrouma know that knowledge is the first weapon and the last defense—this warrior has stacked both arsenals high.",
    "Ultramarines": "Theoretical and practical alignment—the Codex approves this exceptional service to the Watch.",
    "White Scars": "The swift raid yields the most—and none raid swifter than the sons of Chogoris.",
    "Wolfspear": "The Wolfspear track their prey by every trace left behind—and this warrior retrieved the most valuable trace of all: the enemy's own secrets.",
    "Black Shield": "No chapter's legacy, only deeds—and this warrior's deeds fill the Watch's arsenal and its archives alike.",
}

APOTHECARION_MEDAL_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Lion's line endures through warriors like this—the Unforgiven remember every recovered gene-seed.",
    "Angels of Vengeance": "Every gene-seed recovered is vengeance denied to the alien—the Angels of Vengeance understand this well.",
    "Black Templars": "The Crusade must be sustained—this warrior ensures the Chapter's future survives every battle.",
    "Bleeding Hearts": "Even in death, the Brothers of this warrior's Chapter are preserved—the Rage cannot consume what this warrior protects.",
    "Blood Angels": "Sanguinius' own gene-seed flows through those preserved by this warrior—a sacred duty answered.",
    "Blood Ravens": "The deepest lore of a Chapter rests in its gene-seed—this warrior guards it with scholarly devotion.",
    "Brazen Minotaurs": "Bronze and bone endure—this warrior ensures the Minotaur's legacy survives every siege.",
    "Carcharodons": "The Void-born do not allow their kind to vanish into the dark—this warrior pulls them back.",
    "Carmine Blades": "The blood-curse makes every drop of Sanguinius's gene-seed precious beyond measure—this warrior ensures none is lost to the dark.",
    "Celestial Lions": "Against all odds and all betrayal, the Lions endure—because warriors like this refused to let them fall.",
    "Cowled Wardens": "The Unforgiven know the weight of legacy—this warrior preserves it in bone and blood.",
    "Crimson Fists": "Rynn's World was rebuilt from almost nothing—this warrior ensures the Fists need never face that again.",
    "Dark Angels": "The First Legion's secrets are preserved in bone and blood—this warrior honors that duty without reservation.",
    "Dark Krakens": "The deep swallows many—but this warrior ensures the Kraken endures where the abyss would claim them.",
    "Dragonspears": "The fallen brothers remembered in the fleet—their legacy preserved by this warrior's sacred work.",
    "Death Spectres": "Life and death are this chapter's domain—and this warrior walks that boundary to preserve what must survive.",
    "Epsilon Paladins": "For Duty! The Paladin's doctrine demands that every fallen brother be honored—and preserved.",
    "Exorcists": "Purity of gene-seed is preservation of faith—this warrior carries both burdens with equal resolve.",
    "Flesh Tearers": "The line of Seth is precious—this warrior keeps it from the abyss of extinction.",
    "Genesis Chapter": "Guilliman's purest heritage is preserved by warriors like this—the gene-seed of Macragge endures.",
    "Hawk Lords": "The raptor's nest is preserved—this warrior ensures the Lords fly on for another generation.",
    "Hospitallers": "The healer who heals the Chapter itself—there is no higher calling in the Apothecarion's tradition.",
    "Imperial Fists": "Dorn's legacy is in the gene-seed—this warrior holds it against all who would see it lost.",
    "Imperius Reavers": "The Reavers' line has stood the Eastern Fringe for millennia—this warrior ensures it stands for millennia more.",
    "Iron Hands": "The machine may fail; the flesh may fail; but the gene-seed must not—this warrior ensures it does not.",
    "Iron Hounds": "The pack's lineage endures through this warrior's service—the hounds run on.",
    "Iron Lords": "Three thousand years of vigil over the Grendl Stars—the Iron Lords' line endures because warriors like this refuse to let it falter.",
    "Iron Ravens": "In darkness, the genetic legacy is guarded—this warrior sees to it without fail.",
    "Knights of the Raven": "The Chapter's continuity depends on those who guard the gene-seed—this warrior is indispensable.",
    "Lamenters": "The cursed Chapter endures, stud by sacred stud—this warrior refuses to let the Lamenters fall silent.",
    "Marines Errant": "Far from home, the wanderers still preserve each other—this warrior ensures no Errant is truly lost.",
    "Mentors": "Teaching by example: there is no greater lesson than preservation of the Chapter's future.",
    "Minotaurs": "The bull's bloodline must endure—this warrior ensures the Minotaurs charge on for another era.",
    "Necropolis Hawks": "Among the ruins of the fallen, this warrior reclaims what must survive—efficient, steadfast, vital.",
    "Raptors": "Silent in death as in life—and this warrior ensures their silence is not the silence of extinction.",
    "Raven Guard": "Corax's legacy is too precious to lose—this warrior guards it in the dark where others cannot see.",
    "Red Scorpions": "The Red Scorpions hold gene-seed purity above all—and this warrior's service is impeccable proof.",
    "Red Templars": "The crusade of preservation is not one of blades alone—but without it, no crusade can continue.",
    "Salamanders": "Vulkan taught that every brother matters—this warrior lives that teaching in the most direct way possible.",
    "Scythes of the Emperor": "The Great Devourer could not devour what this warrior has preserved—the Scythes endure.",
    "Sons of Medusa": "Logic demands that genetic continuity be maintained—this warrior satisfies that requirement absolutely.",
    "Space Wolves": "The Allfather's children do not vanish from the sagas while warriors like this still live.",
    "Storm Giants": "The giant's bloodline is a legacy worth defending—and this warrior defends it every time blades are drawn.",
    "Tempestuous Angels": "To protect is to serve—and no service is more protective than this warrior's sacred recovery.",
    "The Drakes": "Fire consumes, but this warrior preserves—proof that the Drake's soul is more than mere destruction.",
    "Tome Keepers": "When a Tome Keeper's book is closed, the gene-seed must be recovered—this warrior honors that sacred rite and ensures every future chapter can be written.",
    "Ultramarines": "The sons of Guilliman must endure—and this warrior has ensured it, again and again and again.",
    "White Scars": "The Great Khan's blood must run on—this warrior ensures the steppe wind carries his legacy forward.",
    "Wolfspear": "The Wolfspear swear oaths upon the First-slain; this warrior ensures the pack's bloodline endures to honor every oath sworn by those who fell before the Chapter was named.",
    "Black Shield": "Though their own past is gone, this warrior preserves the future of every brother who falls beside them.",
}

CRIMSON_LAURELS_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The First Legion has always walked in legend—and this warrior has earned a place among the greatest of them.",
    "Angels of Vengeance": "Vengeance exacted across a thousand battles—the Lion would name this warrior worthy.",
    "Black Templars": "The Eternal Crusade sings this warrior's name. Few reach this pinnacle; none of them regret the cost.",
    "Bleeding Hearts": "The hunter of hunters, the martyr of martyrs—this warrior's legend is written in blood and purpose.",
    "Blood Angels": "Sanguinius himself would pause to honor a warrior who bears the Crimson Laurels.",
    "Blood Ravens": "A living library of war—this warrior's knowledge, bought with battle, fills volumes.",
    "Brazen Minotaurs": "Every fortress falls. Every enemy yields. This warrior's legend is the siege that never ends.",
    "Carcharodons": "From the darkest reaches of the void emerges one who has become legend even among the silent.",
    "Carmine Blades": "The Swords of Haldroth are a name no longer spoken—but the Carmine Blades? This warrior has made certain that name endures in the highest halls of honor.",
    "Celestial Lions": "Elysium's pride walks taller today—a Lion who has become legend in the face of every enemy's attempt to silence them.",
    "Cowled Wardens": "The Unforgiven hunt eternal—and none has proven more relentless in that hunt than this warrior.",
    "Crimson Fists": "A thousand battles answered, a thousand enemies unmade—Dorn's fist has never struck harder.",
    "Dark Angels": "The Inner Circle looks upon this warrior and sees a brother whose deeds span the length of legend.",
    "Dark Krakens": "What the abyss forged, the Watch has witnessed—and named it legendary without reservation.",
    "Dragonspears": "A thousand fallen avenged, a thousand more to come—the fleet remembers, and the Dragonspears honor this warrior's name.",
    "Death Spectres": "Between life and legend lies a threshold this warrior crossed long ago, without looking back.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn—and now, for legend. The Paladins stand proud.",
    "Exorcists": "Thrice-tested, a thousand times proven—the daemon itself would flee the warrior who bears these Laurels.",
    "Flesh Tearers": "The wrath of Seth's heirs is legendary—this warrior has made that legend true.",
    "Genesis Chapter": "The Codex predicted excellence; this warrior has far exceeded every theoretical expectation.",
    "Hawk Lords": "The raptor circles over a thousand campaigns—and always returns with victory.",
    "Hospitallers": "Mercy and wrath, a thousand times over—the Hospitaller's creed written in the Watch's own annals.",
    "Imperial Fists": "Dorn built walls to last eternity—this warrior's legend is equally unyielding, equally eternal.",
    "Imperius Reavers": "The Vidar Sector's long war against alien and Daemon forges legends—and this warrior has become the greatest the Eastern Fringe has ever witnessed.",
    "Iron Hands": "A thousand engagements calculated and executed—the Iron Hands' doctrine cannot fault a single one.",
    "Iron Hounds": "A thousand quarries pursued; a thousand quarries taken—the Iron Hound never loses the scent.",
    "Iron Lords": "Iron of vigil, iron of grip—and this warrior's legend is the strongest link in the Iron Chain that has held the Grendl Stars for three thousand years.",
    "Iron Ravens": "From shadow to legend, without a sound—the Ravens have always known this warrior's worth.",
    "Knights of the Raven": "Ten thousand gambits waged, a thousand battles won—the Knight's board has never looked more assured.",
    "Lamenters": "The curse has not broken this warrior. The Long Watch could not be more certain it never will.",
    "Marines Errant": "The stars themselves have witnessed this warrior's deeds—and not one of them would dispute the honor.",
    "Mentors": "This warrior's legend is the greatest lesson they could ever teach—and the Watch has learned it well.",
    "Minotaurs": "A thousand sieges. A thousand victories. The bronze bull charges on into living legend.",
    "Necropolis Hawks": "A thousand ruins reclaimed, a thousand domains delivered to the Emperor—the Hawks' legacy soars.",
    "Raptors": "Silent, patient, and utterly devastating—a thousand times over, without ever being seen until it was too late.",
    "Raven Guard": "Corax's son has moved through the dark and emerged bearing the highest honor the Watch can bestow.",
    "Red Scorpions": "A thousand examinations of combat purity—each one passed without a single deviation from doctrine.",
    "Red Templars": "Dorn's blade, a thousand times drawn, a thousand times victorious—legend forged in speed and steel.",
    "Salamanders": "Vulkan's sons fight for others—and this warrior has done so more times than any could have asked.",
    "Scythes of the Emperor": "The Great Devourer tried. A thousand times it tried. A thousand times this warrior refused.",
    "Sons of Medusa": "A thousand engagements optimized—the data compiled by this warrior's career could fill the Librarium itself.",
    "Space Wolves": "The sagas of the Fang will speak this warrior's name for generations—and every word will be earned.",
    "Storm Giants": "The giant's legend stands taller than any mountain—and this warrior has earned every inch of that height.",
    "Tempestuous Angels": "A thousand souls protected, a thousand enemies made to pay for threatening them—the Tempestuous Angels endure.",
    "The Drakes": "Fire without end—this warrior has burned through a thousand battles and emerged legendary and unextinguished.",
    "Tome Keepers": "The chronicles of Istrouma have never carried a longer entry—this warrior's deeds fill volumes that will outlast the stars themselves.",
    "Ultramarines": "The Codex could not have theorized a more exemplary warrior—the practical has exceeded every theoretical.",
    "White Scars": "A thousand hunts at full gallop, a thousand kills—the Great Khan rides beside this warrior's legend.",
    "Wolfspear": "From the darkest reaches of the Imperium Nihilus, a legend returns—oaths carved into every plate, fulfilled in every engagement, a thousand times over.",
    "Black Shield": "No name, no lineage, no past—but a legend the Long Watch itself will carry forward, forever.",
}

# ─────────────────────────────────────────────────────────────────────────────
# Armor Integrity / Forge subsystem data (extracted from bot.py)
# ─────────────────────────────────────────────────────────────────────────────

ARMOR_DAMAGE_TIERS = ["damaged", "compromised", "critical"]
ARMOR_DAMAGE_PENALTIES = {"damaged": 1, "compromised": 2, "critical": 3}  # Legacy fixed

# Mission name to planet mapping (for armor alert debrief)
MISSION_TO_PLANET = {
    "inferno": "Kadaku",
    "termination": "Kadaku",
    "purgation": "Kadaku",
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


# ─────────────────────────────────────────────────────────────────────────────
# Librarian / Warp Corruption subsystem data
# ─────────────────────────────────────────────────────────────────────────────

# Brother infection tiers (exact mirror of armor's damaged/compromised/critical).
# The fourth state (warp_corrupted, parallel to spirit_fractured) is tracked as
# a separate boolean flag on the state record — not a tier value.
WARP_INFECTION_TIERS = ["tainted", "exposed", "volatile"]

# Legacy alias retained for read-side compatibility; do NOT use for new code.
WARP_EXPOSURE_TIERS = WARP_INFECTION_TIERS

# Susceptibility bands — these map a brother's current susceptibility points to
# the infection-roll probability tier (mirror of armor's probability_tiers config).
# Bands no longer directly produce a tier label; they only determine the chance/
# weights of the next infection roll. Kept here for legacy display fallback.
WARP_BROTHER_TIER_BANDS = {
    "tainted": (1, 4),
    "exposed": (5, 9),
    "volatile": (10, None),
}

# Librarian personal exposure tiers (2x brother bands; reflects psychic tolerance)
WARP_LIBRARIAN_TIERS = ["stable", "resonant", "surging", "overloaded", "abyssal"]
WARP_LIBRARIAN_TIER_BANDS = {
    "stable": (1, 8),
    "resonant": (9, 18),
    "surging": (19, 28),
    "overloaded": (29, 38),
    "abyssal": (39, None),
}

# Brother-facing Warp Sanction status (label, description). Brothers see this only.
# Mirrors Techmarine armor outcome layer: 1 clean state (sanctioned ≈ nominal) +
# 3 roled states (screening_due/under_review/restricted ≈ damaged/compromised/critical).
# A separate boolean flag (warp_corrupted ≈ spirit_fractured) is tracked on top.
#
# TERMINOLOGY NOTE (partial display-only migration):
#   User-facing labels have been updated in three places while the internal keys,
#   helpers, and JSON fields below intentionally retain the legacy "sanction"
#   vocabulary (full refactor deferred to a later migration):
#       • Librarian clearing a brother of warp taint — displayed as "Cleansed"
#         (internal dict key/state remains "sanctioned").
#       • Techmarine clearing armor — conceptually displayed as "Attested"
#         (internal already uses _find_responsible_attestor / attestor).
#       • AAR accepted into the archive — displayed as "Chronicled"
#         (internal helpers / state still talk about sanctioned AARs).
#   Treat dict keys like "sanctioned", `_warp_sanction_key_for_points`,
#   `_apply_sanction_role`, JSON field `is_sanctioned`, etc., as the legacy
#   term for what is now displayed as "Cleansed" / "Attested" / "Chronicled".
WARP_SANCTION_STATUS = {
    "sanctioned": ("Cleansed", "No corruption detected. Spirit clear."),
    "screening_due": ("Screening Due", "Trace contamination detected. Report for psychic screening."),
    "under_review": ("Under Review", "Significant exposure noted. Librarium oversight engaged."),
    "restricted": ("Restricted", "Severe exposure. Operational restrictions in effect pending Void Warden review."),
}

# Map a brother's INFECTION STATE to a Warp Sanction key.
# Sanction keys now derive from the discrete infection state, not from
# susceptibility points — exactly parallel to armor's sanction roles, which
# track damage_tier (damaged/compromised/critical) rather than wear points.
_WARP_INFECTION_TO_SANCTION = {
    None: "sanctioned",
    "tainted": "screening_due",
    "exposed": "under_review",
    "volatile": "restricted",
}


def _warp_sanction_key_for_state(infection_state, warp_corrupted: bool = False) -> str:
    """Map a brother's infection_state (+ warp_corrupted flag) to a sanction key.

    warp_corrupted brothers always surface as "restricted" regardless of the
    current infection_state (mirrors spirit_fractured forcing a permanent
    high-severity Discord role).
    """
    if warp_corrupted:
        return "restricted"
    return _WARP_INFECTION_TO_SANCTION.get(infection_state, "sanctioned")


# Legacy helper retained for read-side compatibility. Internally it just maps
# the legacy point bands to the equivalent sanction key. New code should call
# _warp_sanction_key_for_state instead.
def _warp_sanction_key_for_points(points: int) -> str:
    """DEPRECATED: legacy point-band → sanction mapping."""
    if points <= 0:
        return "sanctioned"
    if points <= 4:
        return "screening_due"
    if points <= 9:
        return "under_review"
    return "restricted"


# Warp corruption threshold (AAR submissions at restricted before brother is corrupted).
# Mirrors DEFAULT_ARMOR_FRACTURE_THRESHOLD.
DEFAULT_WARP_CORRUPTION_THRESHOLD = 3


# Penalty tables — exact mirror of ARMOR_PENALTY_PROBABILITIES, keyed by
# the brother's infection_state. warp_corrupted (the spirit_fractured
# parallel) is handled by the resolver function below.
WARP_PENALTY_PROBABILITIES = {
    None: {0: 1.0},
    "tainted": {0: 0.90, 1: 0.085, 2: 0.010, 3: 0.005},
    "exposed": {0: 0.835, 1: 0.10, 2: 0.05, 3: 0.015},
    "volatile": {0: 0.75, 1: 0.085, 2: 0.10, 3: 0.065},
}

# Penalty table for warp_corrupted (parallel to fractured) — strictly worse
# than volatile alone.
WARP_PENALTY_PROBABILITIES_CORRUPTED = {0: 0.70, 1: 0.05, 2: 0.085, 3: 0.10, 4: 0.065}

# Detection alert chances per AAR while infected (mirrors ARMOR_DETECTION_CHANCES).
WARP_DETECTION_CHANCES = {
    "tainted": 0.20,
    "exposed": 0.35,
    "volatile": 0.50,
}

# Spread chance from an infected source by the source's infection_state.
# Only infected brothers can spread — clean brothers cannot.
WARP_SPREAD_CHANCES = {
    "tainted": 0.20,
    "exposed": 0.35,
    "volatile": 0.50,
}

# Infection probability tiers — exact mirror of forge_ops probability_tiers.
# Keyed by susceptibility point ranges; each entry gives the chance an
# infection roll succeeds AND the weights for picking which tier (when it does).
WARP_INFECTION_PROBABILITY_TIERS = [
    {"min": 0,  "max": 4,    "chance": 0.00, "infection_weights": {"tainted": 100, "exposed": 0,  "volatile": 0}},
    {"min": 5,  "max": 9,    "chance": 0.02, "infection_weights": {"tainted": 90,  "exposed": 8,  "volatile": 2}},
    {"min": 10, "max": 14,   "chance": 0.08, "infection_weights": {"tainted": 80,  "exposed": 15, "volatile": 5}},
    {"min": 15, "max": 19,   "chance": 0.20, "infection_weights": {"tainted": 65,  "exposed": 25, "volatile": 10}},
    {"min": 20, "max": None, "chance": 0.40, "infection_weights": {"tainted": 50,  "exposed": 35, "volatile": 15}},
]

# Cleanse outcome probabilities — exact mirror of BLESSING_ROLL_PROBABILITIES.
# Keyed by recipient's current infection_state ("corrupted" used when warp_corrupted=True).
WARP_CLEANSE_OUTCOME_PROBABILITIES = {
    None:        {"crit_fail": 0.01, "crit_success": 0.01},
    "tainted":   {"crit_fail": 0.03, "crit_success": 0.03},
    "exposed":   {"crit_fail": 0.05, "crit_success": 0.05},
    "volatile":  {"crit_fail": 0.08, "crit_success": 0.06},
    "corrupted": {"crit_fail": 0.10, "crit_success": 0.10},
}

# Cleanse outcome matrix keyed by the cleansing Librarian's current tier.
# Each entry is a list of (probability, outcome_key, fraction_removed, librarian_extra).
# - outcome_key: "full", "partial", "backlash"
# - fraction_removed: 0.0 - 1.0 of recipient's current exposure (full = 1.0)
# - librarian_extra: extra exposure added to the Librarian on top of the standard transfer
WARP_CLEANSE_OUTCOMES = {
    None: [  # Clear Librarian — most reliable
        (0.90, "full", 1.00, 0),
        (0.10, "partial", 0.75, 0),
    ],
    "stable": [
        (0.80, "full", 1.00, 0),
        (0.15, "partial", 0.75, 0),
        (0.05, "backlash", 0.50, 1),
    ],
    "resonant": [
        (0.65, "full", 1.00, 0),
        (0.25, "partial", 0.60, 0),
        (0.10, "backlash", 0.40, 2),
    ],
    "surging": [
        (0.45, "full", 1.00, 0),
        (0.35, "partial", 0.50, 0),
        (0.20, "backlash", 0.30, 3),
    ],
    # overloaded/abyssal: cannot cleanse others (handled in command guard)
}

# Sanitized public flavor for cleanse outcomes
WARP_CLEANSE_OUTCOME_FLAVOR = {
    "full": [
        "The Librarian seals the rift cleanly. Corruption recedes; the spirit clears.",
        "Wards complete. The taint is purged in full.",
        "Litanies hold. The brother stands cleansed.",
    ],
    "partial": [
        "The cleanse holds, but residue clings to the spirit.",
        "Most of the taint is purged. A faint shadow remains.",
        "The wards cut deep but do not finish the work.",
    ],
    "backlash": [
        "The cleanse falters. Corruption lashes back into the Librarian.",
        "Wards crack. The Librarian absorbs the backlash to spare the brother.",
        "The rite holds—barely. Burden flows to the cleanser.",
    ],
    # New three-outcome keys (mirror armor blessing outcomes)
    "crit_success": [
        "The wards sing in perfect harmony — the brother emerges fortified beyond cleansed.",
        "A confluence of light: every shadow is burned away and a grace lingers in the warp.",
        "The rite ascends. The brother walks free, and the warp recoils from him for a while.",
    ],
    "normal": [
        "Wards hold. The taint is purged in full.",
        "Litanies complete. The brother stands cleansed.",
        "The Librarian seals the breach; the spirit clears.",
    ],
    "crit_fail": [
        "The rite shatters. Corruption surges back twofold into the cleanser.",
        "Wards splinter. The Librarian reels under doubled backlash; the taint deepens.",
        "The cleanse inverts. The brother's affliction worsens and the cleanser bleeds the cost.",
    ],
}

# Brief Librarian tier descriptions for warp_status displays
WARP_LIBRARIAN_TIER_DESCRIPTIONS = {
    None: ("CLEAR", "Mind shielded; full reliability."),
    "stable": ("STABLE", "Minor strain. Cleansing reliable."),
    "resonant": ("RESONANT", "Marked resonance. Cleansing less predictable."),
    "surging": ("SURGING", "Severe instability. Backlash likely."),
    "overloaded": ("OVERLOADED", "Cannot cleanse others. Self-cleanse only."),
    "abyssal": ("ABYSSAL", "Void Warden intervention required."),
}

WARP_BROTHER_TIER_DESCRIPTIONS = {
    None: ("CLEAR", "No infection detected."),
    "tainted": ("TAINTED", "Minor warp residue."),
    "exposed": ("EXPOSED", "Notable contamination."),
    "volatile": ("VOLATILE", "Severe contamination; psychic instability."),
}

# ---------------------------------------------------------------------------
# Compact icon ladders (parity with armor: 🟡 → 🟠 → 🔴 → 💀 → ⚫)
# Used by /warp_status and Librarium Chronicle to keep lines short.
# ---------------------------------------------------------------------------
WARP_BROTHER_TIER_ICON = {
    None: "🟢",
    "tainted": "🟡",
    "exposed": "🟠",
    "volatile": "🔴",
}

# Librarian tiers use square icons to stay visually distinct from brother
# circle icons in the same field/legend (parity with armor ladder shape).
WARP_LIBRARIAN_TIER_ICON = {
    None: "🟩",
    "stable": "🟨",
    "resonant": "🟧",
    "surging": "🟥",
    "overloaded": "⬛",
    "abyssal": "🟫",
}

# Sanction status uses a 4-key ladder (sanctioned/screening_due/under_review/restricted),
# distinct from the 5-tier brother exposure ladder above.
WARP_SANCTION_STATUS_ICON = {
    "sanctioned": "🟢",
    "screening_due": "🟡",
    "under_review": "🟠",
    "restricted": "🔴",
}

# Boolean flag icons (orthogonal to tier ladders)
WARP_CORRUPTED_ICON = "⚠️"
WARP_SPREADER_ICON = "🌀"
WARP_LIBRARIAN_MARKER_ICON = "🧿"

# Ambient lines for Librarium chronicle posts
LIBRARIUM_AMBIENT_MESSAGES = [
    "*Wards hum quietly in the sanctum.*",
    "*The Librarium's silence is a held breath.*",
    "*Psychic hoods rest on their stands, awaiting need.*",
    "*Warp-glass lenses catch a light no one cast.*",
    "*Somewhere, a litany ends. Another begins.*",
    "*The Librarians' vigil continues, unspoken and unbroken.*",
]

# ---------------------------------------------------------------------------
# SOK-G: Pipehitter award announcement flavor text
# ---------------------------------------------------------------------------
SOK_G_PIPEHITTER_OPENINGS: List[str] = [
    "Where defeat was assured, **{name}** clawed back victory — and the Watch Master takes note.",
    "**{name}** has walked into operations no sane warrior would accept, and walked back out with the mission accomplished.",
    "The Watch Master's own answer their call without question. **{name}** has proven worthy to stand among them.",
    "Few are tapped for Special Operations Kill-Group: Pipehitter. **{name}** is one of those few.",
    "On a mission written off as lost, **{name}** rewrote the ending in the enemy's blood.",
]

SOK_G_PIPEHITTER_PROCLAMATIONS: List[str] = [
    "Special Operations Kill-Group: Pipehitter is no formal company — it is a designation earned in the impossible operations of Watch Fortress Jericho.",
    "The Watch Master's own move outside the normal hierarchy, called upon only when no other warriors will suffice. This warrior now stands in that select circle.",
    "Pipehitters do not parade. They do not boast. They are dispatched, they perform, and they return — usually alone.",
    "To bear the Pipehitter designation is to be the Watch Master's hidden blade — drawn only when nothing else will cut deep enough.",
    "The hard-stratagem operations that broke other teams have been completed by this warrior — repeatedly, decisively, and without protest.",
]

SOK_G_PIPEHITTER_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven trust few with their secrets — they have trusted this warrior with the worst of theirs.",
    "Angels of Vengeance": "The Lion's vengeance is patient, but its blade is swift; this Pipehitter is its swift edge.",
    "Black Templars": "The Eternal Crusade has no shortage of zealots — this one was singled out for the unwinnable.",
    "Bleeding Hearts": "Hunt-rage tempered by Pipehitter discipline becomes something the foe has no answer for.",
    "Blood Angels": "Sanguinius' sons embrace the impossible operation with the same grace they embrace death itself.",
    "Blood Ravens": "Knowledge of every battlefield, courage to walk into the worst of them — the Blood Raven's gift.",
    "Brazen Minotaurs": "Where the bull charges first, the Pipehitter charges alone — and this warrior thrives at that vanguard.",
    "Carcharodons": "Silent, lethal, predatory — the Void-born were Pipehitters before the term existed.",
    "Carmine Blades": "Baal's curse never stopped this warrior; the Watch Master's impossible orders certainly will not.",
    "Celestial Lions": "Elysium's last sons march again — and one of them now bears the Pipehitter mark.",
    "Cowled Wardens": "The cowl hides the warrior; the Pipehitter designation makes plain what they have done.",
    "Crimson Fists": "Rynn's World left no luxury of easy operations; this warrior has carried that hardness into the Watch.",
    "Dark Angels": "The First Legion guards its secrets; the Watch Master guards his — and trusts them both to this brother.",
    "Dark Krakens": "From the crushing deeps to the impossible ops — pressure is this warrior's natural habitat.",
    "Dragonspears": "The fleet-born hunt of the Dragonspears finds new expression in Pipehitter operations.",
    "Death Spectres": "Between the worlds of the living and dead, this warrior walks the Watch Master's blackest errands.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and now for the Watch Master's most desperate orders.",
    "Exorcists": "Thrice-tested against the Warp; once again tested by an operation no one else would take.",
    "Flesh Tearers": "Where the Red Thirst is leashed by Pipehitter precision, the foe finds only butcher's work.",
    "Genesis Chapter": "Guilliman's measured perfection in service of the Watch Master's hidden hand — a deadly composition.",
    "Hawk Lords": "Swift to the impossible, swift back from it — the Hawk Lords' speed serves this warrior well.",
    "Hospitallers": "Healer and slayer in one — the Hospitaller's dual blade cuts deepest where the Pipehitter goes.",
    "Imperial Fists": "Dorn's resolve does not break, and this warrior has tested that truth on operations meant to break anyone.",
    "Imperius Reavers": "The Eastern Fringe forged a warrior the Watch Master could trust with operations no one else wanted.",
    "Iron Hands": "The flesh proved adequate; the impossible orders proved well-suited to it.",
    "Iron Hounds": "Hounds run down the prey no one else can catch — Pipehitters complete the missions no one else can finish.",
    "Iron Lords": "The Iron Grip holds because warriors like this complete the missions that would otherwise loosen it.",
    "Iron Ravens": "Shadow-craft and silence — the Iron Raven was a Pipehitter long before the Watch named them so.",
    "Knights of the Raven": "Patient stalking and decisive strike — exactly what the Watch Master's blackest orders demand.",
    "Lamenters": "Cursed they may be; but on a Pipehitter operation, this warrior's curse is borne by the foe alone.",
    "Marines Errant": "Wherever the void carried them, this warrior found a way — Pipehitter work suits the wanderer.",
    "Mentors": "A Pipehitter whose every operation is a study — for themselves, and for those who one day might follow.",
    "Minotaurs": "The bronze bull does not flinch; this warrior has not flinched from any operation, no matter the odds.",
    "Necropolis Hawks": "Urban war's hardest streets prepared this warrior for the Watch Master's most thankless errands.",
    "Raptors": "Strike from nowhere, vanish into less — the Raptor's gift is the Pipehitter's tool.",
    "Raven Guard": "Corax's shadow lies long upon this warrior, and the foe never sees their end until it has come.",
    "Red Scorpions": "Purity in form and purity in execution — the Red Scorpion is a Pipehitter of cold perfection.",
    "Red Templars": "Fast as the blade, hard as the Fist — this warrior carries both into the Watch Master's hidden orders.",
    "Salamanders": "Vulkan's sons protect the helpless — and Pipehitters protect missions no one else can finish.",
    "Scythes of the Emperor": "Sotha's loss made warriors who fear no impossibility; this Pipehitter is one of them.",
    "Sons of Medusa": "Calculated lethality and pragmatic resolve — Pipehitter operations were made for warriors like this.",
    "Space Wolves": "The skalds will sing of this warrior's lone runs into the dark — for the Watch Master's ears alone.",
    "Storm Giants": "Tower-tall in deed and Pipehitter-quiet in execution — a contradiction the foe never reconciles.",
    "Tempestuous Angels": "Drossmire's fire taught patience and fury alike; the Pipehitter wields both in measured doses.",
    "The Drakes": "Flame-tempered and unbreakable — this Drake's Pipehitter operations have left only ash behind.",
    "Tome Keepers": "Every Pipehitter run this warrior survives is one more entry in the Chapter's most secret records.",
    "Ultramarines": "The Codex teaches victory; this Pipehitter has taught the Codex what victory looks like in the dark.",
    "White Scars": "The steppe wind blows where the Watch Master orders, and this warrior is that wind made flesh.",
    "Wolfspear": "The Dark Terror hunts where no one else dares — a Pipehitter by lineage as well as by mark.",
    "Black Shield": "No lineage, no name — only the Pipehitter's mark and the operations no one else will speak of.",
}

# ---------------------------------------------------------------------------
# Distinguished SOK-G: Pipehitter award announcement flavor text
# ---------------------------------------------------------------------------
DISTINGUISHED_PIPEHITTER_OPENINGS: List[str] = [
    "Once was happenstance. Twice is a pattern. **{name}** has made a pattern of the impossible.",
    "**{name}** has answered the Watch Master's call more than once — and prevailed each time.",
    "The Pipehitter designation is rare. To earn its Distinguished mark is rarer still — and **{name}** has done so.",
    "Lightning, they say, does not strike twice. **{name}** has struck thrice and more.",
    "**{name}** has stood on the brink of certain defeat repeatedly, and dragged the Watch's victory back from each.",
]

DISTINGUISHED_PIPEHITTER_PROCLAMATIONS: List[str] = [
    "To earn the Distinguished mark of Pipehitter is to have proven beyond doubt that the first victory was no accident.",
    "The Watch Master's own are dispatched only to the most assured defeats. To be dispatched repeatedly, and to repeatedly succeed, is the measure of this warrior.",
    "Where one Pipehitter operation makes a legend, two makes a fixture of the Watch Master's hidden order.",
    "This warrior is no longer called upon as a last resort — they are called upon first, because no one else will do.",
    "The Distinguished Pipehitter has made impossibility their trade, and the Watch their forge.",
]

DISTINGUISHED_PIPEHITTER_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "Once-trusted with secrets; now thrice-trusted with their execution. The Unforgiven do not give that trust lightly.",
    "Angels of Vengeance": "Patient vengeance demands patient warriors — and this brother has answered the call again and again.",
    "Black Templars": "The Eternal Crusade now counts this warrior among its most reliable blades for the Watch Master's hidden fronts.",
    "Bleeding Hearts": "Rage repeatedly mastered, hunts repeatedly closed — the Distinguished mark is well-earned by such consistency.",
    "Blood Angels": "Sanguinius' line has answered the impossible operation more than once, and prevailed every time.",
    "Blood Ravens": "Knowledge and valor compounding — each Pipehitter run sharpens this warrior into a finer instrument.",
    "Brazen Minotaurs": "Where the bull charges once is bravery; charging again and again is the Distinguished Pipehitter's trade.",
    "Carcharodons": "The Void-born do not boast; they return again. This warrior has returned more times than most ever leave.",
    "Carmine Blades": "Baal's curse demands much; this warrior has given more than most, and returned to give again.",
    "Celestial Lions": "Elysium's sons endure beyond all reason — proven yet again on operations meant to extinguish them.",
    "Cowled Wardens": "Once is the cowl's secret; twice is its tradition — and the Watch Master honors that tradition.",
    "Crimson Fists": "Rynn's World forged stubbornness; the Watch Master has tested it repeatedly and never found it wanting.",
    "Dark Angels": "Two impossible operations, two flawless returns — the First Legion's resolve is no myth in this warrior.",
    "Dark Krakens": "Pressure compounds; this warrior compounds with it, returning from each crushing op deadlier than before.",
    "Dragonspears": "The fleet hunt continues, and this Dragonspear has now ranged farther into the dark than most ever will.",
    "Death Spectres": "The dead's company is familiar to this warrior; the Watch Master's blackest orders are now equally so.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and now repeatedly for the Watch Master's most ruinous orders.",
    "Exorcists": "Tested by the Warp, then tested by the Watch Master — and tested again. Each test answered in full.",
    "Flesh Tearers": "Repeated mastery of the Red Thirst on impossible operations — the Distinguished mark is the truest measure of this warrior.",
    "Genesis Chapter": "Repetition perfects the Codex; this Pipehitter has perfected the Codex's hidden chapters.",
    "Hawk Lords": "Swift again. Swift again. Swift again. The Hawk Lord's wings do not tire on the Watch Master's errands.",
    "Hospitallers": "Healer and slayer — each Pipehitter run sees this warrior do both, and do them better than the last.",
    "Imperial Fists": "Stone-resolve under repeated strain; the Distinguished Pipehitter proves Dorn's lineage true with each operation.",
    "Imperius Reavers": "The Eastern Fringe forged a warrior whose every Pipehitter operation only sharpens that forging.",
    "Iron Hands": "Each Pipehitter operation a data-point; each Distinguished mark a confirmation. The flesh is sufficient.",
    "Iron Hounds": "Hounds run until the prey falls; this Hound has run further than most, and the prey continues to fall.",
    "Iron Lords": "The Iron Grip closes tighter with each Distinguished Pipehitter — and this warrior has earned that mark.",
    "Iron Ravens": "Shadow-craft repeated becomes mastery; the Iron Raven has mastered the Watch Master's hidden ways.",
    "Knights of the Raven": "Patient stalking, decisive strike — this warrior has run the cycle more times than the Watch Master usually permits.",
    "Lamenters": "Cursed lineage notwithstanding, this warrior's Pipehitter record is now an undeniable blessing for the Watch.",
    "Marines Errant": "Wanderers carry their honors with them; this Marine Errant carries the Distinguished Pipehitter mark with quiet pride.",
    "Mentors": "An exemplar repeatedly proven — the Mentor's own pupils could find no better model for the Pipehitter's craft.",
    "Minotaurs": "The bronze bull charges again. And again. The Watch Master finds his stride satisfactory.",
    "Necropolis Hawks": "Urban war's hardest fronts again — and again. The Distinguished mark marks this warrior's relentless return.",
    "Raptors": "Silent strike, silent return, repeated — the Raptor's gift is now the Distinguished Pipehitter's signature.",
    "Raven Guard": "Corax's shadow lengthens with each operation; this Raven Guard has walked it longer than most.",
    "Red Scorpions": "Purity demands consistency; this warrior has been consistent in the most impossible of operations.",
    "Red Templars": "Fast and firm — and now repeatedly proven to be both, on operations meant to test only one of those.",
    "Salamanders": "Vulkan's sons protect again, and again. The Distinguished Pipehitter's anvil rings with this warrior's strikes.",
    "Scythes of the Emperor": "Sotha's heirs do not surrender — repeatedly proven by this warrior on operations meant to break them.",
    "Sons of Medusa": "Calculated, repeatable, lethal — exactly the Pipehitter virtues the Watch Master prizes most.",
    "Space Wolves": "The skalds will need a longer saga; this Wolf has hunted in the Watch Master's name many times now.",
    "Storm Giants": "Tower-tall and tower-steady; the Distinguished mark is small recognition for such repeated greatness.",
    "Tempestuous Angels": "Drossmire's fire-tempered Pipehitter has now tempered the foe in fires of their own making — more than once.",
    "The Drakes": "Ash piles upon ash; this Drake's Distinguished Pipehitter operations have left landscapes of cinder.",
    "Tome Keepers": "Each operation a new chapter; each Distinguished mark a new volume. The Chapter's secret library grows.",
    "Ultramarines": "The Codex describes excellence; this Distinguished Pipehitter has written excellence into operations no Codex would attempt.",
    "White Scars": "The steppe wind blows through the Watch Master's blackest orders — and blows back, again and again, with this warrior.",
    "Wolfspear": "The Dark Terror hunts repeatedly in the Watch Master's dark — a hunter perfectly suited to the work.",
    "Black Shield": "No name, no lineage — only the Distinguished Pipehitter mark, and operations whose details will never be spoken.",
}

# ---------------------------------------------------------------------------
# Black Laurels award announcement flavor text
# ---------------------------------------------------------------------------
BLACK_LAURELS_OPENINGS: List[str] = [
    "**{name}** has placed the Kill Team above all notion of personal glory — and the Watch sees it.",
    "Where lesser warriors seek their own legend, **{name}** has built the legend of their brothers.",
    "**{name}** has shown, time and again, that the strength of the Kill Team is the only strength that matters.",
    "The Tyranid plague devours all — except where warriors like **{name}** stand the line together.",
    "**{name}** has been forged in the crucible of battle, and emerged not as a hero, but as a brother of brothers.",
]

BLACK_LAURELS_PROCLAMATIONS: List[str] = [
    "The Black Laurels are awarded to those who place the success of the Kill Team above any notion of personal glory.",
    "The Tyranid plague spreads ever deeper into the galaxy. The Deathwatch Kill Team is the first line of defense — and this warrior is one of its truest sons.",
    "Each Kill Team is a force multiplier greater than the sum of its parts. This warrior is what makes that mathematics possible.",
    "Forged from bonds built through battle, this warrior has put the team before themselves until that bond became unbreakable.",
    "The Black Laurels honor not the individual blade, but the warrior who sharpened every blade beside them.",
]

BLACK_LAURELS_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "Even the Unforgiven recognize when a brother places the Kill Team's purpose above his own pursuit of redemption.",
    "Angels of Vengeance": "The Lion's wrath is best wielded together — this warrior has made that lesson their creed.",
    "Black Templars": "The Eternal Crusade marches as one; this Templar has carried that truth into every Kill Team they have stood beside.",
    "Bleeding Hearts": "Hunt-rage shared with brothers becomes Kill Team fury — and this warrior has shared theirs without hesitation.",
    "Blood Angels": "Sanguinius died for his sons; this son has lived for his brothers, repeatedly and without ceremony.",
    "Blood Ravens": "Knowledge of every brother's strength, devotion to every brother's success — the Blood Raven's truest gift.",
    "Brazen Minotaurs": "The bronze herd breaks no line; this warrior has been the brazen heart of every Kill Team they have joined.",
    "Carcharodons": "The Void-born speak little; this one has spoken volumes through deeds for their brothers in arms.",
    "Carmine Blades": "Baal's curse divides; this warrior's actions have united every Kill Team they have ever served in.",
    "Celestial Lions": "Elysium's sons stand together or not at all — this Lion has stood, again and again.",
    "Cowled Wardens": "The cowl hides one warrior, but reveals the brotherhood of all — this Warden has lived that truth.",
    "Crimson Fists": "Few in number, mighty in bond — the Fists know what every Kill Team costs, and this warrior has paid it gladly.",
    "Dark Angels": "The First Legion holds its secrets, but holds its brothers tighter still; this warrior is proof of that priority.",
    "Dark Krakens": "Crushing pressure binds the pod; this Kraken has bound every Kill Team they have joined in the same iron grip.",
    "Dragonspears": "Fleet brothers fight as one or perish alone; this Dragonspear has ensured the former, always.",
    "Death Spectres": "Between life and death walks the Kill Team's bond; this Spectre has walked it for every brother beside them.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and above all, for the brothers who stand beside them.",
    "Exorcists": "Thrice-purified, thrice-tested — and yet this warrior's truest test was always the bond of brotherhood.",
    "Flesh Tearers": "Where Red Thirst threatens to isolate, this warrior has chained themselves to their Kill Team and to no other purpose.",
    "Genesis Chapter": "Guilliman's purity finds its highest form in the Kill Team; this Genesis-son has lived that doctrine.",
    "Hawk Lords": "Swift to support, swift to shield — the Hawk Lord's wing covers every brother under it.",
    "Hospitallers": "Healer of the wounded brother, slayer for the wounded brother — the Hospitaller's bond is unbreakable.",
    "Imperial Fists": "Dorn's wall is built of brothers; this warrior has been one of its most steadfast stones.",
    "Imperius Reavers": "The Eastern Fringe taught isolation; the Watch taught brotherhood — this warrior chose the latter.",
    "Iron Hands": "Flesh is weak, the brotherhood is not — this Iron Hand has stripped away pride in service of the Kill Team.",
    "Iron Hounds": "Hounds hunt in packs, and this Hound has never broken from theirs — no matter the temptation.",
    "Iron Lords": "The Iron Grip holds when every warrior trusts every other — this Iron Lord has been worthy of that trust.",
    "Iron Ravens": "Shadow-work is solitary; this Iron Raven has nonetheless built bonds in the darkness no one else could see.",
    "Knights of the Raven": "The Raven hunts patient, hunts together — and this warrior has been the patience of every Kill Team they have served.",
    "Lamenters": "The cursed brother holds tightest to those who would share their burden — and this Lamenter has done so without flinching.",
    "Marines Errant": "Wherever they wander, they find brothers — and bind themselves to them with the same fierce loyalty.",
    "Mentors": "A brother whose presence elevates every Kill Team they join — the Mentor's truest virtue.",
    "Minotaurs": "The bull-herd breaks no formation; this Minotaur has held the line beside every brother they have known.",
    "Necropolis Hawks": "Ruined cities are reclaimed by warriors who reclaim each other first — this Hawk lives that order.",
    "Raptors": "Silent shadows shielding silent brothers — the Raptor's bond, when given, is unbreakable.",
    "Raven Guard": "Corax taught that shadow protects shadow — this Raven Guard has been shadow for every brother they have stood beside.",
    "Red Scorpions": "Purity of purpose, purity of brotherhood — the Red Scorpion's standard is no easy thing, and this warrior has met it.",
    "Red Templars": "Fast as the blade, faithful to the brother — the Red Templar binds both virtues in one warrior.",
    "Salamanders": "Vulkan protected the helpless; this Salamander has protected every brother beside them as if they were the most helpless of all.",
    "Scythes of the Emperor": "Sotha fell, but the brotherhood did not — this Scythe has carried that lesson into every Kill Team they have served.",
    "Sons of Medusa": "Calculated devotion to the brotherhood — the Sons of Medusa's preferred form of love.",
    "Space Wolves": "The pack is everything; this Wolf has lived the pack-bond as if no other oath ever existed.",
    "Storm Giants": "Tower-tall and tower-loyal — this Giant's shadow has sheltered every brother who fought beside them.",
    "Tempestuous Angels": "Drossmire's fire forged this warrior, but the Kill Team's bond forged them better — and they know it.",
    "The Drakes": "Dragon-flame protects the brood; this Drake has been flame for every brother who needed shelter.",
    "Tome Keepers": "The Chapter chronicles speak of warriors; the Black Laurels chronicle speaks of brothers — and this Tome Keeper is in both books.",
    "Ultramarines": "The Codex teaches that no Ultramarine fights alone — this warrior has lived that teaching to the letter.",
    "White Scars": "The steppe wind passes; the brotherhood remains — this White Scar has been part of that remaining, again and again.",
    "Wolfspear": "The Dark Terror hunts as a pack of two; this Wolfspear has extended that pack-bond to every Kill Team they have joined.",
    "Black Shield": "No Chapter, no lineage — only the brothers beside them, and a devotion to those brothers beyond all other purpose.",
}

# ---------------------------------------------------------------------------
# Crux Terminatus award announcement flavor text
# ---------------------------------------------------------------------------
CRUX_TERMINATUS_OPENINGS: List[str] = [
    "Of the elite, the most elite. **{name}** has earned the right to don the venerated Terminator plate.",
    "**{name}** has stood at the edge of extermination and not flinched. The Crux Terminatus is theirs by right.",
    "The Crux Terminatus is bestowed only upon the most dedicated. **{name}** is now counted among them.",
    "Among warriors already considered the finest the Astartes can offer, **{name}** has been judged the finest still.",
    "**{name}** carries the honor of the Deathwatch before all else — and the Watch acknowledges this with the highest mark it can give.",
]

CRUX_TERMINATUS_PROCLAMATIONS: List[str] = [
    "The Crux Terminatus is the highest honor of Watch Fortress Jericho. Its bearers guide the junior battle-brothers in times of doubt and confusion.",
    "Only those who have proven themselves elite among the elite are granted the right to don one of the venerated suits of Terminator armor maintained by the Watch.",
    "To bear the Crux is to carry the honor of the Deathwatch before all others — a burden no lesser warrior could sustain.",
    "The venerated Terminator plate is not granted lightly. It is granted to those who have earned the right to extinguish the foe at the closest of ranges, on the gravest of fronts.",
    "Of all the warriors of Watch Fortress Jericho, only the few who have walked through the fires of Black Laurels missions at the highest rank may wear the Crux. This warrior is one of them.",
]

CRUX_TERMINATUS_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven do not don Terminator plate lightly — and this warrior has earned the right to don it.",
    "Angels of Vengeance": "The Lion's wrath wrapped in adamantium; the Crux upon the chest — a fearsome thing to behold.",
    "Black Templars": "The Eternal Crusade's veterans don the venerated plate as a holy oath; this Templar takes that oath now.",
    "Bleeding Hearts": "Rage tempered to Terminator discipline — the Crux mark sanctifies what the Black Rage has refined.",
    "Blood Angels": "Sanguinius himself wore plate of legend; this son of his line now bears its lineage upon their breast.",
    "Blood Ravens": "Knowledge and devastation made flesh — the Blood Raven Terminator is among the deadliest of any Chapter.",
    "Brazen Minotaurs": "The bronze bull in adamantium plate is a sight to break sieges and ruin foes — this warrior makes it real.",
    "Carcharodons": "The Void-born in Terminator plate — silent, impossibly heavy, deadly beyond reckoning.",
    "Carmine Blades": "Baal's tainted blood does not stain the Crux; this Terminator stands proof against the curse and the foe alike.",
    "Celestial Lions": "Elysium's last sons in venerated plate — a legend wearing legends, marching to war once more.",
    "Cowled Wardens": "The cowl gives way to Terminator helm; the Warden's secret deepens into the holy plate's silence.",
    "Crimson Fists": "Few Fists remain to wear the Crux; this warrior bears it for every brother lost on Rynn's World.",
    "Dark Angels": "The First Legion's Terminator companies are legend — this Dark Angel now writes themselves into that legend.",
    "Dark Krakens": "Crushing pressure of the deep meets the crushing weight of Terminator plate — the Kraken at home in both.",
    "Dragonspears": "Fleet-born to wear the plate of veterans — this Dragonspear honors brothers lost to the flame.",
    "Death Spectres": "The Spectre in venerated plate is a sight that shakes even the Watch's own — a fearsome blessing.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and now in venerated plate, for all of them at once.",
    "Exorcists": "Thrice-purged, thrice-purified — and now thrice-armored. The Crux is the final consecration.",
    "Flesh Tearers": "The Red Thirst in Terminator plate is a horror to behold for the foe and a holy thing for the Watch.",
    "Genesis Chapter": "Guilliman's purest doctrine made manifest in venerated plate — exactly as the primarch intended.",
    "Hawk Lords": "Even the Hawk grounds himself in Terminator plate when the Watch demands — and this warrior is one such.",
    "Hospitallers": "Healer in venerated plate, slayer in venerated plate — the duality consecrated by the Crux.",
    "Imperial Fists": "Dorn's chosen sons wore Terminator plate at the Siege; this Fist now walks in that lineage.",
    "Imperius Reavers": "The Eastern Fringe seldom sees venerated plate — this Reaver carries it home in their soul.",
    "Iron Hands": "Adamantium plate over flesh that wishes it were adamantium — the Iron Hand's perfect form.",
    "Iron Hounds": "The hunt continues in Terminator plate; few prey survive the Iron Hound's approach in such armor.",
    "Iron Lords": "The Iron Grip holds even tighter when sealed in venerated plate — and this warrior is now part of that grip.",
    "Iron Ravens": "Shadow-work in Terminator plate is a contradiction the Iron Raven somehow makes work.",
    "Knights of the Raven": "Patient hunter in patient plate — the Crux suits this Knight as if it were forged for them.",
    "Lamenters": "Cursed lineage in venerated plate — the Crux is no cure, but it is a recognition long overdue.",
    "Marines Errant": "The wanderer who returns to the Watch in Terminator plate is a wanderer no more — they are vanguard.",
    "Mentors": "A teacher in venerated plate is the strongest lesson the Mentor could give — and this warrior is now that lesson.",
    "Minotaurs": "Bronze bull in adamantium plate — the foe sees this and breaks before the charge even begins.",
    "Necropolis Hawks": "Urban war is reshaped by warriors in Terminator plate; this Hawk reshapes it again with the Crux.",
    "Raptors": "Silent shadow in venerated plate is no less silent — only deadlier. The Raptor adapts.",
    "Raven Guard": "Corax's shadow heavy with adamantium — this Raven Guard inherits the plate of legends past.",
    "Red Scorpions": "Purity in venerated plate — the Red Scorpion's standard reaches its highest expression here.",
    "Red Templars": "Fast as the blade, hard as the Fist, sealed in venerated plate — the trifecta is complete.",
    "Salamanders": "Vulkan's sons in Terminator plate forge a wall of protection no foe can breach — this Salamander stands at that wall.",
    "Scythes of the Emperor": "Sotha's heirs in venerated plate — every Crux a quiet vengeance against the swarm that took their home.",
    "Sons of Medusa": "Calculated lethality in venerated plate — the Sons of Medusa's preferred composition for the Crux.",
    "Space Wolves": "The Wolf in Terminator plate is no less wild — only deadlier, and now consecrated by the Crux.",
    "Storm Giants": "Tower-tall in adamantium plate — the Giant's silhouette upon the battlefield brings dread to all foes.",
    "Tempestuous Angels": "Drossmire's fire forged warriors worthy of venerated plate; this Angel now wears it as if born to it.",
    "The Drakes": "Dragon-flame within Terminator plate — the Crux only deepens the heat the foe must endure.",
    "Tome Keepers": "The Chapter's chronicles will mark this Crux Terminatus among the highest honors any Tome Keeper has worn.",
    "Ultramarines": "The Codex prescribes Terminator deployment with precision; this Ultramarine has earned the right to embody it.",
    "White Scars": "The steppe wind seldom carries Terminator plate — but this White Scar has earned the right to make it carry them.",
    "Wolfspear": "The Dark Terror in Terminator plate is the dark itself — a Crux-bearer the foe never sees coming.",
    "Black Shield": "No name, no lineage — only the Crux upon their breast, the venerated plate upon their shoulders, and the Watch's full trust.",
}

# ---------------------------------------------------------------------------
# Kadaku Campaign Medal announcement flavor text
# ---------------------------------------------------------------------------
KADAKU_CAMPAIGN_OPENINGS: List[str] = [
    "**{name}** has marched the length of the Leviathan front on Kadaku — and the swarm has marched no further.",
    "Kadaku's defense was not granted; it was earned by warriors like **{name}**.",
    "The Tyranid surge upon Kadaku met its match in **{name}** and the Kill Teams they served beside.",
    "From the first beachhead to the final hive-spire, **{name}** carried the Watch's banner across Kadaku.",
    "**{name}** has completed the Kadaku Campaign — every operation, every wave, every hive-cleansing.",
]

KADAKU_CAMPAIGN_PROCLAMATIONS: List[str] = [
    "The Kadaku Campaign Medal is bestowed upon those who completed every operation of the Leviathan Protocol upon that world.",
    "Kadaku's defense was a campaign of weeks, of waves, of swarms beyond counting — and this warrior endured it all.",
    "The Leviathan Hive Fleet was turned back from Kadaku because of the persistence of warriors who completed every operation, no matter the cost.",
    "To bear the Kadaku Campaign Medal is to have stood the line where Leviathan threatened, and held it from beginning to end.",
    "This warrior did not pick their battles upon Kadaku — they fought them all, and the Watch records every one.",
]

KADAKU_CAMPAIGN_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "Even on Kadaku's worst fronts, the Unforgiven kept their secrets — and held their line.",
    "Angels of Vengeance": "Vengeance for every soul Leviathan consumed — this Angel paid that debt across the entire campaign.",
    "Black Templars": "The Eternal Crusade marched the length of Kadaku, and this Templar marched with it.",
    "Bleeding Hearts": "Hunt-rage carried this warrior through every Kadaku operation without slowing.",
    "Blood Angels": "Sanguinius' sons gave Kadaku no quarter, and this Angel held the line for the campaign's full length.",
    "Blood Ravens": "Every Kadaku operation a study; every study a victory — the Blood Raven's gift to the campaign.",
    "Brazen Minotaurs": "The bronze charge broke Leviathan formations on Kadaku again and again, with this Minotaur at the fore.",
    "Carcharodons": "Silent through Kadaku's killing fields, this Carcharodon left only ruin in their wake.",
    "Carmine Blades": "Baal's curse held no power on Kadaku; this Carmine Blade held every line.",
    "Celestial Lions": "Elysium's sons fought on Kadaku as if it were home — and held it as such.",
    "Cowled Wardens": "The cowl hid this Warden through every operation; the campaign's full length, achieved in silence.",
    "Crimson Fists": "Few Fists were available; this one fought as if they were a Chapter unto themselves on Kadaku.",
    "Dark Angels": "The First Legion's secrets stayed secret; the Tyranid threat did not. Both outcomes are this warrior's doing.",
    "Dark Krakens": "From the crushing deep to the crushing wave-fronts of Kadaku — pressure was no novelty.",
    "Dragonspears": "Fleet-born brothers carried the Dragonspear's flame across every operation of the Kadaku Campaign.",
    "Death Spectres": "Between life and death walked this Spectre on Kadaku — and walked back, again and again.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and for every operation of the Kadaku Campaign.",
    "Exorcists": "Tested against the Warp, tested against Leviathan — Kadaku was just another test passed in full.",
    "Flesh Tearers": "The Red Thirst found ample feeding on Kadaku; this Tearer's discipline kept the Watch's banner clean.",
    "Genesis Chapter": "Codex-perfect across every operation of Kadaku — Guilliman would have approved.",
    "Hawk Lords": "Swift across every operation, swift to the next — the Hawk Lord's campaign rhythm was relentless.",
    "Hospitallers": "Healer of brothers, slayer of swarms — the Hospitaller's dual duty fulfilled on every Kadaku front.",
    "Imperial Fists": "Dorn's wall held on Kadaku because of warriors like this — campaign-length and unyielding.",
    "Imperius Reavers": "The Eastern Fringe's hardness served this Reaver well across Kadaku's worst operations.",
    "Iron Hands": "The flesh held adequate; the campaign was completed in full. Acceptable parameters.",
    "Iron Hounds": "Hounds run the prey down; this Hound ran Leviathan down across the entire Kadaku Campaign.",
    "Iron Lords": "The Iron Grip held on Kadaku — this Iron Lord ensured the grip closed every time.",
    "Iron Ravens": "Shadow-craft kept this Iron Raven alive through every Kadaku operation — and Leviathan in the dark.",
    "Knights of the Raven": "Patient through every Kadaku front; decisive at every Kadaku strike — the Knight's discipline made plain.",
    "Lamenters": "The cursed Chapter gave Kadaku no less than any other; this Lamenter gave more than most.",
    "Marines Errant": "Wherever they wandered on Kadaku, this Errant brought the Watch's banner — to every operation.",
    "Mentors": "A Mentor whose Kadaku service teaches every brother who comes after them.",
    "Minotaurs": "The bull-herd broke no formation on Kadaku; this Minotaur held the line for every operation.",
    "Necropolis Hawks": "Kadaku's ruined hive-spires were urban war made literal — and this Hawk's element.",
    "Raptors": "Silent strikes across every Kadaku operation — the Raptor's signature, unbroken.",
    "Raven Guard": "Corax's shadow lay across Kadaku; this Raven Guard ensured Leviathan never saw it lift.",
    "Red Scorpions": "Purity across every Kadaku operation — the Red Scorpion's standard unwavering.",
    "Red Templars": "Fast and faithful across every Kadaku front — the Red Templar's twin virtues.",
    "Salamanders": "Vulkan's sons protected Kadaku's civilians wherever they fought — and they fought everywhere.",
    "Scythes of the Emperor": "Sotha's heirs faced Leviathan once more on Kadaku, and this Scythe did not flinch.",
    "Sons of Medusa": "Calculated, campaign-length excellence — Kadaku's defense in the Sons of Medusa style.",
    "Space Wolves": "The skalds will sing of this Wolf's Kadaku saga for sagas to come.",
    "Storm Giants": "Tower-tall across Kadaku's hive-spires — the Giant's silhouette became a Leviathan's nightmare.",
    "Tempestuous Angels": "Drossmire's fire-tempered Angel returned to defend another world; Kadaku is the brighter for it.",
    "The Drakes": "Dragon-flame across every Kadaku front — Leviathan's biomass burned and burned.",
    "Tome Keepers": "Every Kadaku operation an entry; every entry a victory. The Chapter's chronicle is fuller for this warrior.",
    "Ultramarines": "The Codex's campaign-doctrine made manifest — Kadaku's defense an Ultramarine's masterwork.",
    "White Scars": "The steppe wind blew across Kadaku, and this White Scar rode it from operation to operation.",
    "Wolfspear": "The Dark Terror hunted Leviathan through Kadaku's nights — every operation, every kill.",
    "Black Shield": "No lineage to claim — only the Kadaku Campaign Medal, and the Watch's full recognition of every operation completed.",
}

# ---------------------------------------------------------------------------
# Black Reef Campaign Medal announcement flavor text
# ---------------------------------------------------------------------------
BLACK_REEF_CAMPAIGN_OPENINGS: List[str] = [
    "**{name}** has marched the length of the Black Reef Persecution — and the heretic finds no harbor where this warrior treads.",
    "From the first incursion to the final cleansing, **{name}** has carried the Black Reef Persecution to its end.",
    "The Black Reef offered no easy operation; **{name}** completed them all regardless.",
    "**{name}** has earned the Black Reef Campaign Medal through persistence no lesser warrior could match.",
    "Every operation, every void-stretch, every cleansing on the Black Reef — **{name}** stood among the warriors who saw them through.",
]

BLACK_REEF_CAMPAIGN_PROCLAMATIONS: List[str] = [
    "The Black Reef Campaign Medal is bestowed upon those who completed every operation of the Black Reef Persecution.",
    "The Black Reef's foes were many, varied, and never easy — yet this warrior addressed each in turn, and saw each undone.",
    "The Persecution upon the Black Reef was no swift victory; it was a campaign of patience, of attrition, and of unbroken faith.",
    "To bear the Black Reef Campaign Medal is to have walked the void-fronts of the Reef from first incursion to final accounting.",
    "This warrior did not pick their battles upon the Black Reef — they answered every call, and the Watch records every one.",
]

BLACK_REEF_CAMPAIGN_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven held their secrets through the Black Reef's worst — and held the line.",
    "Angels of Vengeance": "Patient vengeance across the entire Persecution — the Lion's wrath sated, at last.",
    "Black Templars": "The Eternal Crusade carried this Templar across every operation of the Black Reef.",
    "Bleeding Hearts": "Hunt-rage drove this warrior through every Reef-front without rest.",
    "Blood Angels": "Sanguinius' sons gave the Black Reef their best — and this Angel was foremost among them.",
    "Blood Ravens": "Every Reef-operation a study, every study a Black Reef victory — the Blood Raven's gift.",
    "Brazen Minotaurs": "The bronze charge broke the Reef's foes again and again; this Minotaur led from the front.",
    "Carcharodons": "The Void-born were home upon the Black Reef — and this Carcharodon ranged its full length.",
    "Carmine Blades": "Baal's curse held no leverage on the Reef; this Blade held every front instead.",
    "Celestial Lions": "Elysium's sons gave the Black Reef no quarter — and this Lion gave even less.",
    "Cowled Wardens": "The cowl hid this Warden through every Reef-operation; the campaign passed in silence and success.",
    "Crimson Fists": "Few Fists remain to defend the Imperium — this one defended the Black Reef as if it were Rynn itself.",
    "Dark Angels": "The First Legion's secrets held; the Reef's foes did not. Both outcomes traceable to this warrior.",
    "Dark Krakens": "Void-pressure was no novelty to this Kraken; the Black Reef's depths were merely home.",
    "Dragonspears": "Fleet-born brothers carried the Dragonspear's flame across every Reef-front.",
    "Death Spectres": "Between life and death walked this Spectre across every Black Reef operation.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and for every operation of the Black Reef Persecution.",
    "Exorcists": "Warp-tested, Reef-tested — this Exorcist passed both crucibles in full.",
    "Flesh Tearers": "The Red Thirst found prey on the Reef; this Tearer's discipline channeled the hunger into victories.",
    "Genesis Chapter": "Codex-perfect across every Reef-operation — Guilliman's doctrine vindicated again.",
    "Hawk Lords": "Swift across the Reef-fronts, swift to the next; the Hawk Lord rode the entire campaign without slowing.",
    "Hospitallers": "Healer and slayer across every Reef-operation — the Hospitaller's dual duty unbroken.",
    "Imperial Fists": "Dorn's wall held the Black Reef because of warriors like this — campaign-length, unyielding.",
    "Imperius Reavers": "The Eastern Fringe was kin to the Black Reef's lawlessness; this Reaver was at home in both.",
    "Iron Hands": "Flesh proved adequate across every Reef-operation; the campaign closed within parameters.",
    "Iron Hounds": "Hounds run the prey down; this Hound ran the Reef's heretics down across the entire Persecution.",
    "Iron Lords": "The Iron Grip held the Black Reef shut — this Iron Lord locked the door at every operation.",
    "Iron Ravens": "Shadow-craft made the Reef's heretics this Iron Raven's prey across the full campaign.",
    "Knights of the Raven": "Patient through every Reef-front, decisive at every strike — the Knight's discipline complete.",
    "Lamenters": "The cursed Chapter gave the Black Reef no less than any other; this Lamenter gave more.",
    "Marines Errant": "Wherever the void took them, the Errant carried the Watch's banner — across the entire Persecution.",
    "Mentors": "A Mentor whose Reef-record will teach brothers for sagas to come.",
    "Minotaurs": "The bull-herd held formation across the Reef; this Minotaur held the line every operation.",
    "Necropolis Hawks": "Reef-stations as ruined as any hive; this Hawk's urban war translated perfectly.",
    "Raptors": "Silent strikes across every Reef-operation — the Raptor's signature unbroken.",
    "Raven Guard": "Corax's shadow lay across the Reef; this Raven Guard ensured the heretic never saw it lift.",
    "Red Scorpions": "Purity across the entire Persecution — the Red Scorpion's standard unwavering.",
    "Red Templars": "Fast and firm across every Reef-front — the Red Templar's twin virtues complete.",
    "Salamanders": "Vulkan's sons protected the Reef's innocents wherever they fought, and fought everywhere.",
    "Scythes of the Emperor": "Sotha's heirs gave the Reef their best — this Scythe was among the foremost.",
    "Sons of Medusa": "Calculated, campaign-length excellence — Reef-defense in the Sons of Medusa style.",
    "Space Wolves": "The skalds will sing of this Wolf's Black Reef saga for sagas to come.",
    "Storm Giants": "Tower-tall across the Reef's void-stations — the Giant's silhouette a heretic's nightmare.",
    "Tempestuous Angels": "Drossmire's fire-tempered Angel returned to defend another front; the Reef is the brighter for it.",
    "The Drakes": "Dragon-flame across every Reef-operation — the heretic burned and burned again.",
    "Tome Keepers": "Every Reef-operation an entry; every entry a victory. The Chapter's chronicle grows.",
    "Ultramarines": "The Codex's campaign-doctrine perfected — the Reef's defense an Ultramarine's masterwork.",
    "White Scars": "The steppe wind blew across the Reef, and this White Scar rode every operation to its end.",
    "Wolfspear": "The Dark Terror hunted heretics across the Reef's nights — every operation, every kill.",
    "Black Shield": "No lineage to claim — only the Black Reef Campaign Medal, and the Watch's full recognition of every operation completed.",
}

# ---------------------------------------------------------------------------
# Distinguished Black Reef Campaign Medal announcement flavor text
# ---------------------------------------------------------------------------
DISTINGUISHED_BLACK_REEF_OPENINGS: List[str] = [
    "**{name}** has marched the Black Reef Persecution as a Kill Team's heart — every operation, every bond, every brother.",
    "The Black Reef's foes met not one warrior in **{name}** — they met a Kill Team in which **{name}** was every brother's strength.",
    "**{name}** carried the Black Reef Persecution and the Black Laurels both — a warrior who refused to fight alone.",
    "Distinguished service across every Reef-operation, with brothers always at hand — **{name}** has earned the deeper mark.",
    "**{name}** completed every Black Reef operation, and did so as part of every Kill Team they joined. None were left behind.",
]

DISTINGUISHED_BLACK_REEF_PROCLAMATIONS: List[str] = [
    "The Distinguished Black Reef Campaign Medal honors those who completed every Reef-operation while serving as the heart of a Kill Team.",
    "Where the campaign medal recognizes presence, the Distinguished mark recognizes purpose — the warrior who carried their brothers through.",
    "To complete the Persecution is honor enough. To complete it without leaving a single Kill Team brother behind is something greater still.",
    "This warrior fought every Reef-operation, and fought every one alongside brothers — the Distinguished mark recognizes the bond as much as the victory.",
    "The Black Reef Persecution was won by Kill Teams, not by lone warriors. This warrior was the Kill Team made flesh.",
]

DISTINGUISHED_BLACK_REEF_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "Even the Unforgiven recognize when a brother carries the Kill Team through every operation — and this one did.",
    "Angels of Vengeance": "Vengeance shared, vengeance compounded — every Reef-operation was the Lion's wrath spoken by a Kill Team in unison.",
    "Black Templars": "The Eternal Crusade marches as one; this Templar marched the entire Reef Persecution at the heart of every Kill Team they joined.",
    "Bleeding Hearts": "Hunt-rage tempered by Kill Team bond — this warrior's Reef-record is one of shared fury.",
    "Blood Angels": "Sanguinius' sons fought every Reef-operation together; this Angel was their truest beating heart.",
    "Blood Ravens": "Knowledge of every brother's strength deployed across every Reef-operation — the Blood Raven's distinguishing gift.",
    "Brazen Minotaurs": "The bronze herd held formation through every Reef-front; this Minotaur was the herd's truest heart.",
    "Carcharodons": "Silence shared across every Reef-operation; the Carcharodon Kill Team bound tighter than the void itself.",
    "Carmine Blades": "Baal's curse divides; the Distinguished Black Reef proves this Blade fought every operation as one with their brothers.",
    "Celestial Lions": "Elysium's sons stood together through every Reef-operation — and this Lion stood centermost.",
    "Cowled Wardens": "The cowl hides one warrior, but the Kill Team includes them all — this Warden held that truth every operation.",
    "Crimson Fists": "Few in number, mighty in bond — this Fist held every Reef-Kill-Team together through the full Persecution.",
    "Dark Angels": "Secrets held, brothers held — the First Legion's two highest virtues, both completed across the entire campaign.",
    "Dark Krakens": "Pod-bond carried through every Reef-operation — the Kraken's deep solidarity made campaign-wide.",
    "Dragonspears": "Fleet brothers fought every Reef-operation together; this Dragonspear ensured no one fell alone.",
    "Death Spectres": "Between life and death walked the Kill Team's bond; this Spectre walked it every Reef-operation for every brother.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — and for every Kill Team brother across every Reef-operation.",
    "Exorcists": "Thrice-purified bond across every Reef-front; this Exorcist's brothers were never alone.",
    "Flesh Tearers": "Red Thirst leashed by Kill Team bond — the Distinguished mark is the truest test of a Flesh Tearer, and this one passed it.",
    "Genesis Chapter": "Codex-perfect Kill Team composition across the entire Persecution — Guilliman's truest vision realized.",
    "Hawk Lords": "Swift to support, swift to shield, every Reef-operation — the Hawk Lord's distinguished service is brother-bond made plain.",
    "Hospitallers": "Healer of every brother across every Reef-front — the Hospitaller's distinguished thread runs unbroken.",
    "Imperial Fists": "Dorn's wall is built of brothers; this Fist was the cornerstone of every Reef-Kill-Team they served.",
    "Imperius Reavers": "The Eastern Fringe taught isolation; the Reef Persecution taught brotherhood — this Reaver chose, decisively, the latter.",
    "Iron Hands": "Flesh is weak; brotherhood is not. This Iron Hand stripped pride for brotherhood across every Reef-operation.",
    "Iron Hounds": "Pack-hunt through every Reef-front; this Hound never broke from their Kill Team across the full Persecution.",
    "Iron Lords": "The Iron Grip holds when every brother trusts every other — this Iron Lord earned that trust at every operation.",
    "Iron Ravens": "Shadow-bond across every Reef-operation; the Iron Raven's solitary craft made plural by Kill Team devotion.",
    "Knights of the Raven": "Patient hunt, shared kill — every Reef-operation a Kill Team's joint work, with this Knight at its quiet center.",
    "Lamenters": "The cursed brother holds tightest to those who would share their burden — this Lamenter held the Kill Team through every operation.",
    "Marines Errant": "Wherever they wandered, brothers were beside them — this Errant ensured the Reef Persecution was no exception.",
    "Mentors": "A brother whose Reef-presence elevated every Kill Team — the Mentor's truest distinguishing virtue.",
    "Minotaurs": "The bull-herd held every Reef-line together; this Minotaur was the herd's truest center.",
    "Necropolis Hawks": "Reef-stations were ruined cities to this Hawk; every brother was reclaimed alongside every chamber.",
    "Raptors": "Shadow shielding shadow across every Reef-operation — the Raptor's bond, unspoken but absolute.",
    "Raven Guard": "Corax taught that shadow protects shadow; this Raven Guard was shadow for every Reef-brother, every operation.",
    "Red Scorpions": "Purity of brotherhood across the Reef Persecution — the Red Scorpion's distinguishing standard met in full.",
    "Red Templars": "Fast as the blade, faithful to the brother — the Red Templar's twin virtues across every Reef-operation.",
    "Salamanders": "Vulkan protected the helpless; this Salamander protected every brother as if they were the helpless of the Reef.",
    "Scythes of the Emperor": "Sotha fell, but the brotherhood did not. This Scythe carried that lesson through every Reef-operation.",
    "Sons of Medusa": "Calculated devotion across every Reef-front — the Sons of Medusa's brotherhood quantified.",
    "Space Wolves": "The pack was everything across every Reef-operation; this Wolf lived the pack-bond as creed.",
    "Storm Giants": "Tower-tall and tower-loyal across every Reef-operation — the Giant's shadow shielded every brother.",
    "Tempestuous Angels": "Drossmire's bond carried into the Reef — this Angel kept it through every operation.",
    "The Drakes": "Dragon-flame protects the brood; this Drake was flame for every Reef-brother who needed shelter.",
    "Tome Keepers": "Every Reef-operation a chapter; every chapter a Kill Team's joint work — this Tome Keeper's distinguished chronicle is rich.",
    "Ultramarines": "The Codex teaches no Ultramarine fights alone; this warrior lived that doctrine across every Reef-operation.",
    "White Scars": "The steppe wind passes, the brotherhood remains — this White Scar held the brotherhood through every Reef-front.",
    "Wolfspear": "The Dark Terror hunts as a pack of two; this Wolfspear extended that pack-bond across every Reef-Kill-Team they joined.",
    "Black Shield": "No name, no lineage — only the brothers beside them, every operation of the Persecution, every Kill Team's heart.",
}

# ---------------------------------------------------------------------------
# The Order Omega announcement flavor text
# ---------------------------------------------------------------------------
ORDER_OMEGA_OPENINGS: List[str] = [
    "**{name}** has walked through the final difficulty of every operation, bearing the Black Laurels each step — the Watch knows few honors greater.",
    "At the highest difficulty the Watch can offer, **{name}** has prevailed — and done so without ever forsaking their brothers.",
    "**{name}** has earned admittance to The Order Omega — a fellowship whose name is whispered, never shouted.",
    "Where Omega difficulty broke other warriors, **{name}** broke Omega — and did so as the heart of every Kill Team they joined.",
    "**{name}** stands among the warriors who have walked the impossible difficulty without ever walking alone — and that is what The Order Omega honors.",
]

ORDER_OMEGA_PROCLAMATIONS: List[str] = [
    "The Order Omega is a fellowship few enter. Its members have endured the Watch's highest difficulty and emerged carrying their brothers with them.",
    "To bear the Order Omega mark is to have crossed the threshold past which the Watch tests no further — there is no higher operational measure.",
    "The Omega-difficulty operations were not designed to be survived alone. The Order Omega gathers those who survived them as a Kill Team's heart.",
    "Where the Crux honors elite warriors, The Order Omega honors elite Kill Teams — and the warriors who held them together at the impossible difficulty.",
    "This warrior has done what the Watch did not believe possible — at Omega difficulty, with Black Laurels, on every required operation. The Order Omega opens to them now.",
]

ORDER_OMEGA_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven recognize impossibility met by brotherhood — and this Angel met it, again and again.",
    "Angels of Vengeance": "The Lion's vengeance at Omega difficulty is an answer to every Fallen wrong — and this Angel gave that answer.",
    "Black Templars": "The Eternal Crusade has known few honors as quiet and as profound as The Order Omega — this Templar now bears it.",
    "Bleeding Hearts": "Hunt-rage at the impossible difficulty, leashed by the Kill Team's bond — this warrior's Omega service is poetry.",
    "Blood Angels": "Sanguinius' sons walked the impossible difficulty together; this Angel led that walk for every brother beside them.",
    "Blood Ravens": "Knowledge of every battlefield, devotion to every brother — Omega difficulty was no match for the Blood Raven's compound gift.",
    "Brazen Minotaurs": "The bronze charge at Omega difficulty broke what no other charge could; this Minotaur was at its bronze heart.",
    "Carcharodons": "Silent through Omega difficulty, this Carcharodon left only ruin and a Kill Team intact.",
    "Carmine Blades": "Baal's curse held no leverage at Omega; this Blade held the Kill Team instead.",
    "Celestial Lions": "Elysium's sons walked Omega difficulty together; this Lion ensured every brother walked back.",
    "Cowled Wardens": "The cowl hides; The Order Omega reveals — this Warden's Omega service speaks where their words never would.",
    "Crimson Fists": "Few Fists remain; this one walked Omega difficulty for every Fist who could not.",
    "Dark Angels": "The First Legion's deepest secrets and the Watch's highest difficulty — this Dark Angel has carried both, intact.",
    "Dark Krakens": "Crushing pressure of the deep, crushing pressure of Omega — this Kraken was at home in both, and brought every pod-brother through.",
    "Dragonspears": "Fleet brothers carried the Dragonspear's flame through Omega difficulty; this warrior carried the flame brightest.",
    "Death Spectres": "Between life and death walked this Spectre at Omega difficulty — and brought every brother back across the line.",
    "Epsilon Paladins": "For Honour, for Duty, for Dorn — at Omega difficulty, for every brother. The Order Omega is no small recognition.",
    "Exorcists": "Thrice-purified, thrice-tested, then tested again at Omega — this Exorcist passed every crucible without losing a brother.",
    "Flesh Tearers": "Red Thirst at Omega difficulty would consume any lesser warrior; this Tearer's Kill Team bond consumed it instead.",
    "Genesis Chapter": "Codex-perfect across Omega difficulty — Guilliman's purest doctrine met its hardest test, and this warrior was its perfect instrument.",
    "Hawk Lords": "Swift across Omega difficulty, swift to shield brothers — the Hawk Lord at The Order Omega is a fearsome thing.",
    "Hospitallers": "Healer and slayer at Omega difficulty — the Hospitaller's dual virtue stretched to its absolute and unbroken limit.",
    "Imperial Fists": "Dorn's wall did not crack at Omega difficulty; this Fist was the cornerstone.",
    "Imperius Reavers": "The Eastern Fringe's hardness met Omega difficulty's impossibility — and this Reaver carried both, and their brothers, through.",
    "Iron Hands": "Flesh proved adequate at Omega; the Kill Team proved more than adequate. Optimal parameters.",
    "Iron Hounds": "Hounds run the prey down at any difficulty; at Omega, the hunt is shared with brothers — this Hound ran it true.",
    "Iron Lords": "The Iron Grip at Omega difficulty is the Order's truest test, and this Iron Lord locked it shut every operation.",
    "Iron Ravens": "Shadow-craft at Omega difficulty served the Kill Team rather than the lone hunter — this Iron Raven understood the lesson.",
    "Knights of the Raven": "Patient through Omega's impossible difficulty, decisive at every strike — the Knight's discipline at its absolute peak.",
    "Lamenters": "The cursed Chapter walked Omega difficulty; this Lamenter walked it as the Kill Team's truest blessing.",
    "Marines Errant": "Wherever the Errant wandered at Omega difficulty, brothers were beside them — and brothers returned with them.",
    "Mentors": "A Mentor's Omega service is the lesson of a lifetime, and this warrior has given that lesson without speaking a word.",
    "Minotaurs": "The bronze bull at Omega difficulty broke what no lesser charge could — and the herd held formation throughout.",
    "Necropolis Hawks": "Omega-difficulty hive-spires were urban war's apex; this Hawk's element, intensified.",
    "Raptors": "Silent strikes at Omega difficulty — the Raptor's signature, refined to its absolute edge, with Kill Team support unbroken.",
    "Raven Guard": "Corax's shadow at Omega difficulty was the longest shadow the Watch ever cast — this Raven Guard cast it.",
    "Red Scorpions": "Purity at Omega difficulty — the Red Scorpion's standard met at the impossibility, with brothers intact.",
    "Red Templars": "Fast and firm at Omega difficulty — the Red Templar's twin virtues at their absolute peak, with the Kill Team held.",
    "Salamanders": "Vulkan's sons protected the helpless at Omega difficulty; this Salamander considered every Kill Team brother the most helpless of all.",
    "Scythes of the Emperor": "Sotha fell, but Omega did not — this Scythe ensured the Kill Team prevailed where their home could not.",
    "Sons of Medusa": "Calculated lethality at Omega difficulty — the Sons of Medusa's preferred composition for the Order's induction.",
    "Space Wolves": "The skalds will sing of this Wolf's Omega saga — every operation, every brother, every impossibility.",
    "Storm Giants": "Tower-tall at Omega difficulty — the Giant's shadow sheltered every brother through the impossible.",
    "Tempestuous Angels": "Drossmire's fire-tempered Angel walked Omega difficulty as if Drossmire had been merely the prelude.",
    "The Drakes": "Dragon-flame at Omega difficulty consumed every foe — and warmed every brother through the cold.",
    "Tome Keepers": "Every Omega operation is the Chapter's most carefully recorded; this Tome Keeper's Order Omega chronicle is the rarest of volumes.",
    "Ultramarines": "The Codex's highest doctrine met Omega's impossible difficulty — and this Ultramarine made the meeting victory.",
    "White Scars": "The steppe wind blew through Omega difficulty; this White Scar rode it for every brother who could not.",
    "Wolfspear": "The Dark Terror at Omega difficulty hunted what nothing else would; this Wolfspear's Order Omega is dread incarnate.",
    "Black Shield": "No lineage to claim — only Omega difficulty, every operation, every Kill Team brother brought through. The Order Omega is theirs by right.",
}

# ---------------------------------------------------------------------------
# Rank-specific codas for challenge awards
# ---------------------------------------------------------------------------
# Each dict maps a rank name (as in RANK_HONORIFICS) to a 1-sentence coda
# appended to the proclamation when the bearer holds that rank.

SOK_G_PIPEHITTER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "That the Watch Master themselves has answered the Pipehitter call is the deepest seal upon the designation's worth.",
    "High Chaplain": "The High Chaplain bearing the Pipehitter mark is a sermon written in operations rather than words.",
    "Chief Apothecary": "The Chief Apothecary as Pipehitter — healer and slayer fused into the Watch Master's blackest order.",
    "Void Warden": "The Void Warden's vigilance carried into Pipehitter operations is a singular thing — feared by the foe, treasured by the Watch.",
    "Forgemaster": "The Forgemaster as Pipehitter brings the machine-spirits' approval to operations no machine should witness.",
    "Castellan": "The Castellan as Pipehitter is the fortress's hidden hand reaching far beyond its walls.",
    "Lord Executioner": "The Lord Executioner as Pipehitter — the title and the designation share the same blade.",
    "Venerable Dreadnought": "A Venerable Dreadnought answering Pipehitter calls is a force of impossibility unleashed.",
    "Honored Dreadnought": "The Honored Dreadnought as Pipehitter — an ancient's wrath repurposed for the Watch Master's hidden errands.",
    "Interred Brother": "An Interred Brother walking Pipehitter operations is the rarest of weapons drawn for the rarest of needs.",
    "Watch Chaplain": "The Chaplain's faith carried into Pipehitter operations is itself a weapon the foe cannot answer.",
    "Watch Apothecary": "The Apothecary as Pipehitter saves brothers on operations brothers should not survive.",
    "Watch Librarian": "The Librarian's mind as Pipehitter tool — the foe is read before they are slain.",
    "Watch Techmarine": "The Techmarine as Pipehitter ensures the machine-spirits never falter, even on operations the spirits would refuse.",
    "Watch Keeper": "The Keeper's vigil applied to Pipehitter operations brings closure to every impossible front.",
    "Company Champion": "The Company Champion as Pipehitter — the Chapter's sharpest blade in the Watch's hidden sheath.",
    "Kill Team Champion": "The Kill Team Champion as Pipehitter is the team's spearpoint on the Watch Master's blackest fronts.",
    "Watch Captain": "A Watch Captain bearing the Pipehitter mark commands by example on operations command was never expected to reach.",
    "Watch Lieutenant": "The Lieutenant as Pipehitter is a mark of leadership tempered in the most lonely of fires.",
    "Watch Sergeant": "The Sergeant's discipline as Pipehitter is the team's keel on operations meant to capsize them.",
    "Oathsworn": "Oathsworn warriors as Pipehitters fulfill oaths in operations no oath should bind them to.",
    "Watch Veteran": "The Veteran as Pipehitter is the truest test of veteran service — and this brother has passed it.",
    "Watch Brother": "A Watch Brother as Pipehitter is rare praise indeed — this warrior has earned it without ceremony.",
}

DISTINGUISHED_PIPEHITTER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master themselves repeatedly bearing the Pipehitter mark is the Watch's deepest secret made manifest.",
    "High Chaplain": "The High Chaplain's repeated Pipehitter service is sermon and example for every brother to come.",
    "Chief Apothecary": "The Chief Apothecary as repeated Pipehitter — healer of the impossible, again and again.",
    "Void Warden": "The Void Warden's vigilance, repeatedly tested on Pipehitter operations, has yet to be found wanting.",
    "Forgemaster": "The Forgemaster's repeated Pipehitter service has the machine-spirits' quiet approval.",
    "Castellan": "The Castellan as repeated Pipehitter — the fortress's reach grows with each operation.",
    "Lord Executioner": "The Lord Executioner's blade and the Pipehitter's mark grow more deeply joined with each operation.",
    "Venerable Dreadnought": "A Venerable Dreadnought as repeated Pipehitter is a force the foe should pray to never face again.",
    "Honored Dreadnought": "The Honored Dreadnought's repeated Pipehitter service is ancient wrath rendered in the modern Watch's blackest ink.",
    "Interred Brother": "An Interred Brother walking repeated Pipehitter operations is a weapon the Watch deploys only when nothing else will suffice.",
    "Watch Chaplain": "The Chaplain's repeated Pipehitter faith is litany answered, again and again, on operations meant to silence prayer entirely.",
    "Watch Apothecary": "The Apothecary's repeated Pipehitter saves are a quiet legend among brothers who survived because of them.",
    "Watch Librarian": "The Librarian's repeated Pipehitter mind has unraveled foes the Watch Master barely understands.",
    "Watch Techmarine": "The Techmarine's repeated Pipehitter service has the Omnissiah's approval encoded into the machine-spirits' very rhythms.",
    "Watch Keeper": "The Keeper's repeated Pipehitter vigil has closed operation after operation that no one else could have closed.",
    "Company Champion": "The Company Champion's blade has cut deepest in repeated Pipehitter operations — the Chapter's legend grows with every return.",
    "Kill Team Champion": "The Kill Team Champion's repeated Pipehitter service is the team's blade sharpened to its absolute edge.",
    "Watch Captain": "A Watch Captain repeatedly bearing the Distinguished mark commands the Watch's highest respect for the work.",
    "Watch Lieutenant": "The Lieutenant's repeated Pipehitter service marks a leader the Watch will not soon let return to ordinary duty.",
    "Watch Sergeant": "The Sergeant's discipline at repeated Pipehitter operations is the keel that has kept many impossible teams afloat.",
    "Oathsworn": "Oathsworn repeated Pipehitter service is oath fulfilled beyond the letter and into the spirit, again and again.",
    "Watch Veteran": "The Veteran's repeated Pipehitter service is the highest validation any Veteran can earn short of higher rank.",
    "Watch Brother": "A Watch Brother repeatedly bearing the Distinguished mark is a brother on the cusp of higher honors — and the Watch sees it.",
}

BLACK_LAURELS_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master themselves earning the Black Laurels is the deepest proof that no rank is above the brotherhood.",
    "High Chaplain": "The High Chaplain's Black Laurels is sermon by example — the highest faith placed in brothers, not in self.",
    "Chief Apothecary": "The Chief Apothecary as Black Laurels bearer is the truest healer — one who heals the team before the self.",
    "Void Warden": "The Void Warden's Black Laurels is a vigil shared with every brother — no warden stands alone.",
    "Forgemaster": "The Forgemaster's Black Laurels is the machine-spirits' recognition of brotherhood above all metal.",
    "Castellan": "The Castellan's Black Laurels is the fortress turned inward — every brother as precious as every wall.",
    "Lord Executioner": "The Lord Executioner's Black Laurels is the blade laid down — for the Kill Team, before any foe.",
    "Venerable Dreadnought": "A Venerable Dreadnought as Black Laurels bearer is the ancients' final lesson: even legend serves the team.",
    "Honored Dreadnought": "The Honored Dreadnought's Black Laurels reminds every brother that even the eternal serves something greater than self.",
    "Interred Brother": "An Interred Brother's Black Laurels is the truest mark — even from within iron, the brotherhood remains.",
    "Watch Chaplain": "The Chaplain's Black Laurels is faith in the brotherhood made flesh, again and again.",
    "Watch Apothecary": "The Apothecary's Black Laurels is the healer's truest vow — every brother kept, every team held.",
    "Watch Librarian": "The Librarian's Black Laurels is mind given over to the Kill Team's purpose — no secret kept that could save a brother.",
    "Watch Techmarine": "The Techmarine's Black Laurels is the Omnissiah's recognition that the Kill Team is the machine's truest spirit.",
    "Watch Keeper": "The Keeper's Black Laurels is the vigil shared — every brother watched over, every team kept whole.",
    "Company Champion": "The Company Champion's Black Laurels is the Chapter's blade serving the team, not the legend.",
    "Kill Team Champion": "The Kill Team Champion's Black Laurels is the team's blade, sharpened by the team's own bond.",
    "Watch Captain": "A Watch Captain's Black Laurels is leadership measured not in command but in shared sacrifice.",
    "Watch Lieutenant": "The Lieutenant's Black Laurels is leadership earned on the front, alongside every brother.",
    "Watch Sergeant": "The Sergeant's Black Laurels is the team's keel — discipline given as gift to every brother.",
    "Oathsworn": "Oathsworn's Black Laurels is oath kept to the team above any individual creed.",
    "Watch Veteran": "The Veteran's Black Laurels marks a warrior who has chosen brotherhood as the highest honor — and the Watch agrees.",
    "Watch Brother": "A Watch Brother's Black Laurels is a foundation laid early — this brother's whole service will be built upon it.",
}

CRUX_TERMINATUS_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master bearing the Crux is the Watch's own pinnacle — the highest authority in the highest plate.",
    "High Chaplain": "The High Chaplain in venerated plate, bearing the Crux, is faith and adamantium fused.",
    "Chief Apothecary": "The Chief Apothecary as Crux-bearer is the truest healer — armored not for personal protection but to outlast every brother needing aid.",
    "Void Warden": "The Void Warden's Crux is the vigil made physical — adamantium walls between brothers and the dark.",
    "Forgemaster": "The Forgemaster as Crux-bearer has the machine-spirits' deepest approval — the venerated plate sings beneath their hand.",
    "Castellan": "The Castellan's Crux is the fortress walking — every step a wall against the foe.",
    "Lord Executioner": "The Lord Executioner in venerated plate is the Watch's heaviest blade — and the Crux confirms it.",
    "Venerable Dreadnought": "A Venerable Dreadnought wearing the Crux is contradiction made manifest — and welcome.",
    "Honored Dreadnought": "The Honored Dreadnought's Crux is recognition the ancient still walks the line with their brothers in plate.",
    "Interred Brother": "An Interred Brother bearing the Crux is a sight few will ever see — and fewer still will forget.",
    "Watch Chaplain": "The Chaplain's Crux is faith armored — the litanies carried through the impossible in venerated plate.",
    "Watch Apothecary": "The Apothecary's Crux is the healer in venerated plate — the brother who endures so others may be saved.",
    "Watch Librarian": "The Librarian's Crux is mind and adamantium fused — the foe outthought and outendured in one form.",
    "Watch Techmarine": "The Techmarine's Crux is the Omnissiah's blessing rendered in machine-spirit and plate alike.",
    "Watch Keeper": "The Keeper's Crux is the vigil made absolute — venerated plate where no plate should be needed, and yet always is.",
    "Company Champion": "The Company Champion's Crux is the Chapter's blade in the Watch's heaviest sheath.",
    "Kill Team Champion": "The Kill Team Champion's Crux is the team's pinnacle — the spearpoint clad in venerated plate.",
    "Watch Captain": "A Watch Captain's Crux is leadership in adamantium — command that walks every front in venerated plate.",
    "Watch Lieutenant": "The Lieutenant's Crux is leadership earned, then armored — every step a promise to the team.",
    "Watch Sergeant": "The Sergeant's Crux is the team's keel sealed in venerated plate — discipline beyond breaking.",
    "Oathsworn": "Oathsworn's Crux is oath made armor — the warrior's word rendered in adamantium.",
    "Watch Veteran": "The Veteran's Crux is the minimum threshold of the honor — and yet the highest honor a Veteran can hold without further rank.",
    "Watch Brother": "A Watch Brother does not bear the Crux. If you see this, an exception has been made — and the Watch has reasons.",
}

KADAKU_CAMPAIGN_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's presence at every Kadaku operation is the campaign's deepest seal of importance.",
    "High Chaplain": "The High Chaplain's Kadaku service is sermon written in operations no sermon could have prevented.",
    "Chief Apothecary": "The Chief Apothecary at every Kadaku front is the campaign's silent salvation.",
    "Void Warden": "The Void Warden's Kadaku vigil ensured the Tyranid surge never broke the line unchallenged.",
    "Forgemaster": "The Forgemaster's Kadaku service ensured every machine-spirit endured the Leviathan strain.",
    "Castellan": "The Castellan's Kadaku service was the fortress's reach made campaign-wide.",
    "Lord Executioner": "The Lord Executioner's blade fell at every Kadaku front — Leviathan met its match in the title and the warrior alike.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Kadaku service is ancient wrath against the swarm, repeated until the swarm relented.",
    "Honored Dreadnought": "The Honored Dreadnought at every Kadaku operation is a campaign-long lesson in endurance for every younger brother.",
    "Interred Brother": "An Interred Brother walking the Kadaku Campaign is rare and singular — the Watch deploys their weight only when the weight is required.",
    "Watch Chaplain": "The Chaplain's faith carried Kadaku through its darkest hours; this Chaplain was there for every one.",
    "Watch Apothecary": "The Apothecary at every Kadaku front is uncounted brothers' survival made flesh.",
    "Watch Librarian": "The Librarian's mind across Kadaku read every Leviathan tide before it crested.",
    "Watch Techmarine": "The Techmarine's Kadaku service kept the machine-spirits steady where Leviathan biomass would have devoured lesser tools.",
    "Watch Keeper": "The Keeper's Kadaku vigil closed every operation with the meticulousness only the Keeper provides.",
    "Company Champion": "The Company Champion's Kadaku service set the standard every brother strove to match.",
    "Kill Team Champion": "The Kill Team Champion's Kadaku service was the spearpoint of every operation they joined.",
    "Watch Captain": "A Watch Captain's Kadaku service is leadership tested by tide and tooth alike — and proven each time.",
    "Watch Lieutenant": "The Lieutenant's Kadaku service marks a leader who walked every front their command demanded.",
    "Watch Sergeant": "The Sergeant's discipline across Kadaku was the keel many Kill Teams sailed the campaign upon.",
    "Oathsworn": "Oathsworn Kadaku service is oath fulfilled across every operation, with no portion held back.",
    "Watch Veteran": "The Veteran's Kadaku service marks a brother whose worth no Veteran-rank can fully capture.",
    "Watch Brother": "A Watch Brother across the entire Kadaku Campaign is no small thing — this brother has built a foundation that will outlast many higher honors.",
}

BLACK_REEF_CAMPAIGN_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's presence at every Black Reef operation is the Persecution's deepest seal of importance.",
    "High Chaplain": "The High Chaplain's Reef service is faith carried through every void-front of the campaign.",
    "Chief Apothecary": "The Chief Apothecary at every Reef-operation is the campaign's quiet salvation.",
    "Void Warden": "The Void Warden's Reef vigil ensured the heretic never claimed a single station unchallenged.",
    "Forgemaster": "The Forgemaster's Reef service kept the machine-spirits aligned through the entire campaign.",
    "Castellan": "The Castellan's Reef service was the fortress's reach made campaign-wide upon the void's edge.",
    "Lord Executioner": "The Lord Executioner's blade fell at every Reef-front — the title and the warrior in perfect alignment.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Reef service is ancient wrath against the heretic across the full Persecution.",
    "Honored Dreadnought": "The Honored Dreadnought at every Reef-operation is endurance written in adamantium for every brother to study.",
    "Interred Brother": "An Interred Brother walking the Reef Persecution is the Watch's heaviest weapon used with great deliberation.",
    "Watch Chaplain": "The Chaplain's faith carried the Reef Persecution through its darkest fronts; this Chaplain held the line through them all.",
    "Watch Apothecary": "The Apothecary at every Reef-operation is the campaign's survival made flesh.",
    "Watch Librarian": "The Librarian's mind across the Reef read every heretic tide before it broke.",
    "Watch Techmarine": "The Techmarine's Reef service kept the machine-spirits steady across the void's many tests.",
    "Watch Keeper": "The Keeper's Reef vigil closed every operation with the meticulousness only the Keeper provides.",
    "Company Champion": "The Company Champion's Reef service set the standard every brother strove to match.",
    "Kill Team Champion": "The Kill Team Champion's Reef service was the spearpoint of every operation they joined.",
    "Watch Captain": "A Watch Captain's Reef service is leadership tested by the Persecution's full length, and proven at every front.",
    "Watch Lieutenant": "The Lieutenant's Reef service marks a leader who walked every void-front their command demanded.",
    "Watch Sergeant": "The Sergeant's discipline across the Reef was the keel many Kill Teams rode through the void.",
    "Oathsworn": "Oathsworn Reef service is oath fulfilled across every void-operation, with no portion held back.",
    "Watch Veteran": "The Veteran's Reef service marks a brother whose worth no Veteran-rank can fully capture.",
    "Watch Brother": "A Watch Brother across the entire Reef Persecution is a brother on the cusp of higher honors — the Watch sees it.",
}

DISTINGUISHED_BLACK_REEF_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's Distinguished Reef service is the campaign's heart made manifest at its highest authority.",
    "High Chaplain": "The High Chaplain's Distinguished Reef service is faith and brotherhood twined across every operation.",
    "Chief Apothecary": "The Chief Apothecary's Distinguished Reef service is healer's vow extended to every Kill Team brother of every front.",
    "Void Warden": "The Void Warden's Distinguished Reef service is vigil shared with every brother through every void-station.",
    "Forgemaster": "The Forgemaster's Distinguished Reef service is the machine-spirits' recognition of brotherhood above all else.",
    "Castellan": "The Castellan's Distinguished Reef service is the fortress's brothers held tighter than the fortress's walls.",
    "Lord Executioner": "The Lord Executioner's Distinguished Reef service is the blade laid alongside every brother's, never above.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Distinguished Reef service is the ancient lesson — even legend serves the Kill Team.",
    "Honored Dreadnought": "The Honored Dreadnought's Distinguished Reef service reminds every brother that the eternal still stands the team's line.",
    "Interred Brother": "An Interred Brother's Distinguished Reef service is brotherhood preserved even from within iron.",
    "Watch Chaplain": "The Chaplain's Distinguished Reef service is faith placed in the team across every front of the Persecution.",
    "Watch Apothecary": "The Apothecary's Distinguished Reef service is every brother kept, every Kill Team held, across the full campaign.",
    "Watch Librarian": "The Librarian's Distinguished Reef service is mind given over to the team's purpose at every void-front.",
    "Watch Techmarine": "The Techmarine's Distinguished Reef service is the Omnissiah's recognition that the Kill Team is the truest machine-spirit.",
    "Watch Keeper": "The Keeper's Distinguished Reef service is the vigil shared with every brother across every operation.",
    "Company Champion": "The Company Champion's Distinguished Reef service is the Chapter's blade serving the team, every front, every operation.",
    "Kill Team Champion": "The Kill Team Champion's Distinguished Reef service is the team made manifest at its absolute fullest.",
    "Watch Captain": "A Watch Captain's Distinguished Reef service is command shared with every brother — leadership at its truest.",
    "Watch Lieutenant": "The Lieutenant's Distinguished Reef service is leadership earned alongside every brother, every operation.",
    "Watch Sergeant": "The Sergeant's Distinguished Reef service is the team's keel made unbreakable across the entire Persecution.",
    "Oathsworn": "Oathsworn's Distinguished Reef service is oath kept to the team above all individual creed.",
    "Watch Veteran": "The Veteran's Distinguished Reef service marks a brother whose worth far exceeds the Veteran rank.",
    "Watch Brother": "A Watch Brother bearing the Distinguished Reef mark is a brother whose full service will be built on this foundation.",
}

ORDER_OMEGA_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master in The Order Omega is the Watch's pinnacle confirmed — the highest authority within the highest fellowship.",
    "High Chaplain": "The High Chaplain in The Order Omega is faith carried through impossibility, with every brother held alongside.",
    "Chief Apothecary": "The Chief Apothecary in The Order Omega is the truest healer's vow — every brother saved at Omega difficulty.",
    "Void Warden": "The Void Warden in The Order Omega is vigil at the Watch's absolute edge, shared with every brother who walked beside.",
    "Forgemaster": "The Forgemaster in The Order Omega is the machine-spirits' recognition that the Kill Team has reached its truest form.",
    "Castellan": "The Castellan in The Order Omega is the fortress made absolute — every brother held safe through impossibility.",
    "Lord Executioner": "The Lord Executioner in The Order Omega is the heaviest blade laid alongside every brother's at the impossible difficulty.",
    "Venerable Dreadnought": "A Venerable Dreadnought in The Order Omega is the ancient lesson at its highest — even legend serves the team at Omega.",
    "Honored Dreadnought": "The Honored Dreadnought in The Order Omega is endurance's purest form — eternity at the impossible difficulty, with brothers intact.",
    "Interred Brother": "An Interred Brother in The Order Omega is the rarest of weapons used in the rarest of fellowships.",
    "Watch Chaplain": "The Chaplain in The Order Omega is faith carried through Omega difficulty without leaving a brother behind.",
    "Watch Apothecary": "The Apothecary in The Order Omega is the healer's vow extended to every brother through every Omega operation.",
    "Watch Librarian": "The Librarian in The Order Omega is mind and brotherhood twined at the impossible difficulty.",
    "Watch Techmarine": "The Techmarine in The Order Omega is the Omnissiah's recognition that the Kill Team is the truest spirit at every difficulty.",
    "Watch Keeper": "The Keeper in The Order Omega is the vigil made absolute — every brother watched over, every operation closed at Omega.",
    "Company Champion": "The Company Champion in The Order Omega is the Chapter's blade serving the team at the Watch's absolute edge.",
    "Kill Team Champion": "The Kill Team Champion in The Order Omega is the team's pinnacle confirmed — spearpoint at Omega difficulty, with the team intact.",
    "Watch Captain": "A Watch Captain in The Order Omega is leadership at the absolute edge — command shared with every brother at Omega difficulty.",
    "Watch Lieutenant": "The Lieutenant in The Order Omega is leadership earned at the impossible difficulty, alongside every brother.",
    "Watch Sergeant": "The Sergeant in The Order Omega is the team's keel through impossibility — discipline at its absolute strongest.",
    "Oathsworn": "Oathsworn in The Order Omega is oath kept to the team at the impossible difficulty.",
    "Watch Veteran": "The Veteran in The Order Omega is the highest validation a Veteran can earn short of greater rank.",
    "Watch Brother": "A Watch Brother in The Order Omega is rare almost beyond reckoning — this brother's foundation is the Watch's deepest stone.",
}

# ---------------------------------------------------------------------------
# Dual Vigil award announcement flavor text
# ---------------------------------------------------------------------------
DUAL_VIGIL_OPENINGS: List[str] = [
    "**{name}** has proven that two brothers, bound by purpose, can hold the line as surely as any full fireteam.",
    "Two warriors. Every mission. No retreat. **{name}** has demonstrated what it means to place absolute trust in a single brother.",
    "The Watch does not always measure strength in numbers. **{name}** has carried the Dual Vigil through every required operation — and earned what few can claim.",
    "**{name}** has stood watch with a single brother at their side across the entirety of verified Black Laurels operations. That is not fortune. That is discipline.",
    "There are warriors who fight in the shadow of a full Kill Team. Then there are warriors like **{name}** — who go to the absolute edge of operations with one brother, and come back.",
    "**{name}** walked every required operation in the tightest formation the Watch recognises — two, together, absolute. The Vigil is earned.",
    "Two brothers. All missions. **{name}** has borne the Dual Vigil's demands without compromise.",
]

DUAL_VIGIL_PROCLAMATIONS: List[str] = [
    "The Dual Vigil is awarded to those who completed every verified Black Laurels operation at Absolute difficulty with exactly one brother at their side.",
    "Where the Black Laurels honour the Kill Team, the Dual Vigil honours the pair — the two warriors who held together long after others would have called for reinforcement.",
    "The Dual Vigil recognises a singular depth of trust: the warrior who chose, every time, to fight the hardest operations with one brother instead of three.",
    "To earn the Dual Vigil is to have proven that two brothers, operating as one, can face the Watch's most demanding missions and prevail.",
    "The Black Laurels speak of the team. The Dual Vigil speaks of the bond — two brothers, standing vigil across every absolute front.",
    "Not every warrior finds a brother they would take to every absolute operation. **{name}** has done exactly that — and the Watch records it accordingly.",
    "The Dual Vigil is not given lightly. It is the mark of brothers who treated every absolute operation as a test of what two warriors in complete trust can achieve.",
]

DUAL_VIGIL_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven know the weight of an unshared burden. **{name}** found one brother willing to carry half of it — across every absolute operation required.",
    "Angels of Vengeance": "The Lion's sons carry their purpose without ceremony. **{name}** carried theirs through every required absolute front with one brother and no fanfare.",
    "Black Templars": "The Eternal Crusade does not always require a full crusade force. **{name}** has proven what two Templars, committed to every absolute operation, can achieve.",
    "Bleeding Hearts": "Pack-bond concentrated to a single pairing — **{name}** has run every absolute operation required with one brother. The hunt was answered.",
    "Blood Angels": "Sanguinius died in the company of the greatest. **{name}** chose one brother for every absolute operation and made that pairing into something worthy of the primarch's memory.",
    "Blood Ravens": "Knowledge is nothing without the brother beside you. **{name}** has shared every absolute operation with one companion — the Dual Vigil is the record of that partnership.",
    "Brazen Minotaurs": "The bronze herd knows that two warriors in lockstep are more than the sum of their parts. **{name}** has proven it across every required absolute mission.",
    "Carcharodons": "The Void-born speak through deeds. **{name}**'s deeds are nine absolute operations completed alongside one brother — the Dual Vigil is the only words required.",
    "Carmine Blades": "The curse of Baal isolates none who refuse to be isolated. **{name}** refused, choosing one brother for every absolute engagement — and completing them all.",
    "Celestial Lions": "Elysium's sons stand together or not at all. **{name}** stood with one brother through every absolute operation — the Dual Vigil is Elysium's mark on the Watch.",
    "Cowled Wardens": "The cowl shelters one; two Wardens together are a fortress. **{name}** made every absolute operation a fortress with one companion at their side.",
    "Crimson Fists": "Few in number is the Fist's legacy. **{name}** took that legacy to its sharpest point — one brother, every absolute operation, complete.",
    "Dark Angels": "The First Legion guards its greatest secrets with pairs, not crowds. **{name}** has operated in that spirit across every required absolute mission.",
    "Dark Krakens": "The deep hunts in pairs when the prey is greatest. **{name}** has hunted every absolute operation that way — one brother, complete darkness, complete commitment.",
    "Dragonspears": "Fleet warriors know that two can hold a corridor as well as four, given the right pair. **{name}** has held every absolute operation with one brother.",
    "Death Spectres": "Between death and the next breath, there is only one companion worth having. **{name}** chose that companion for every absolute operation — and returned from all of them.",
    "Epsilon Paladins": "For Honour, for Duty, for the brother beside you. **{name}** has carried all three through every absolute operation alongside one companion.",
    "Exorcists": "What the thrice-tested warrior fears least is the front. **{name}** faced every required absolute front with one brother and the Dual Vigil is the measure of that fearlessness.",
    "Flesh Tearers": "Seth's sons turn the Red Thirst into purpose. **{name}** turned it into a two-warrior absolute assault across every required mission — and the results speak for themselves.",
    "Genesis Chapter": "Guilliman's purest heirs know that doctrine serves the team. **{name}** served one teammate across every required absolute operation — perfection of the pairing.",
    "Hawk Lords": "Swift to close, swift to commit — **{name}** closed on every required absolute mission with one brother and never once broke formation.",
    "Hospitallers": "The healer's bond is with one patient at a time. **{name}** extended that bond to one brother across every absolute operation — and neither fell.",
    "Imperial Fists": "Dorn built his walls two stones at a time. **{name}** built the Dual Vigil the same way — one mission, one brother, every absolute requirement met.",
    "Imperius Reavers": "The Eastern Fringe teaches that two warriors can hold what a dozen cannot if those two are committed enough. **{name}** has proven it nine times at absolute difficulty.",
    "Iron Hands": "The weakest link breaks the chain. **{name}** forged a two-link chain across every required absolute operation — and neither link failed.",
    "Iron Hounds": "Two hounds on the same scent are worth ten scattered. **{name}** and one brother tracked every required absolute mission together without deviation.",
    "Iron Lords": "The Iron Grip requires anchor points. **{name}** was one anchor; their one chosen brother was the other — every absolute operation, complete.",
    "Iron Ravens": "Shadow falls heaviest on two warriors who share it. **{name}**'s Dual Vigil is shadow shared with one brother across every required absolute mission.",
    "Knights of the Raven": "The Raven's patience is sharpest when the hunt is small. **{name}** hunted every absolute operation with one companion — patient, precise, complete.",
    "Lamenters": "The cursed Chapter finds hope in the smallest companionships. **{name}**'s Dual Vigil is one such hope, forged over nine absolute operations with one unwavering brother.",
    "Marines Errant": "The errant finds their purpose in unexpected partnerships. **{name}** found theirs in one brother, every absolute operation — the Dual Vigil records the finding.",
    "Mentors": "The finest lesson a Mentor can teach is commitment. **{name}** taught it by example — one brother, every required absolute operation, nothing withheld.",
    "Minotaurs": "The bull charges hardest in pairs. **{name}** paired with one brother for every absolute operation the Dual Vigil requires — and the charge was sufficient every time.",
    "Necropolis Hawks": "Ruined cities are held by two warriors who refuse to yield. **{name}** refused across every required absolute mission — the Dual Vigil is the holding.",
    "Raptors": "The Raptor's shadow is quietest when shared. **{name}** shared it with one brother through every absolute operation required, and the silence served them both.",
    "Raven Guard": "Corax taught that two shadows are harder to find than one. **{name}** and one brother proved it at absolute difficulty, nine required missions, complete.",
    "Red Scorpions": "Purist standards applied to the pairing: one brother, absolute operations, every mission completed. **{name}** has met the Red Scorpion standard in all things.",
    "Red Templars": "Fast, faithful, and paired — **{name}** brought the Red Templar's values into every required absolute operation with one unwavering companion.",
    "Salamanders": "Vulkan's sons do not abandon. **{name}** did not abandon their one chosen brother across a single absolute operation — every mission, together, complete.",
    "Scythes of the Emperor": "Sotha's memory is carried by those who refuse to be reduced below two. **{name}** carried it through every required absolute operation alongside one brother.",
    "Sons of Medusa": "The calculation was simple: one brother, every absolute operation, full commitment. **{name}** ran the calculation and produced the Dual Vigil as the result.",
    "Space Wolves": "Two wolves are a hunting pair. **{name}** ran every required absolute operation as that pair — one brother, full hunt, all nine missions answered.",
    "Storm Giants": "Two towers together hold what one cannot. **{name}** and one brother held every required absolute operation — the Dual Vigil is the record of what two Giants can do.",
    "Tempestuous Angels": "Drossmire's fire is most controlled when shared between two. **{name}** shared every absolute operation with one brother — the fire was sufficient every time.",
    "The Drakes": "Dragon-flame is given freely to those who stand closest. **{name}** stood closest to one brother through every required absolute operation — the Dual Vigil is that closeness recorded.",
    "Tome Keepers": "The Chapter writes what it has witnessed. **{name}**'s Dual Vigil is already in the Tome — one brother, nine absolute operations, every mission complete.",
    "Ultramarines": "The Codex describes formations of three; **{name}** has written the addendum — two warriors with absolute commitment can complete every required absolute mission.",
    "White Scars": "Two riders on the same path, at full speed, through every absolute front required. **{name}** has completed the ride — the Dual Vigil is the trophy of that journey.",
    "Wolfspear": "The Dark Terror hunts as a pair. **{name}** has taken that Wolfspear principle into every required absolute operation — one brother, full commitment, complete.",
    "Black Shield": "No name, no Chapter, one brother, every absolute operation. **{name}** has built the Dual Vigil from the most elemental materials available.",
}

DUAL_VIGIL_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master bearing the Dual Vigil is the Watch's highest authority confirming what two warriors can achieve when fully committed.",
    "High Chaplain": "The High Chaplain's Dual Vigil is faith made manifest in partnership — two voices, every absolute front, every oath held.",
    "Chief Apothecary": "The Chief Apothecary's Dual Vigil is protection extended to one brother, every absolute operation, without fail.",
    "Void Warden": "The Void Warden's Dual Vigil is the paired vigil taken to the Watch's absolute edge — two companions, every station, every front.",
    "Forgemaster": "The Forgemaster's Dual Vigil is the Omnissiah's recognition that even the machine-spirits respect a bond forged across every absolute operation.",
    "Castellan": "The Castellan's Dual Vigil is the fortress reduced to its truest element — two warriors, holding together through everything.",
    "Lord Executioner": "The Lord Executioner's Dual Vigil is the executioner's blade sharpened by partnership — one brother at the side, every absolute mission.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Dual Vigil is antiquity's proof that the deepest bonds are forged in the smallest formations.",
    "Honored Dreadnought": "The Honored Dreadnought's Dual Vigil is endurance's testament — the eternal paired with one brother through every absolute front.",
    "Interred Brother": "An Interred Brother's Dual Vigil is the rarest bond of all — the preserved warrior and one companion, absolute operations, absolute commitment.",
    "Watch Chaplain": "The Chaplain's Dual Vigil is faith and partnership made indistinguishable — every absolute mission, one brother, one purpose.",
    "Watch Apothecary": "The Apothecary's Dual Vigil is the healer's vow concentrated — one brother kept whole across every absolute operation required.",
    "Watch Librarian": "The Librarian's Dual Vigil is the psyker's focus narrowed to one companion — every absolute front, every required mission, together.",
    "Watch Techmarine": "The Techmarine's Dual Vigil is the Omnissiah's blessing on a partnership honed across every absolute operation.",
    "Watch Keeper": "The Keeper's Dual Vigil is the vigil shared with one brother, every operation, without deviation.",
    "Company Champion": "The Company Champion's Dual Vigil is the Chapter's blade paired with one brother's — every absolute mission a testament to what two champions can achieve.",
    "Kill Team Champion": "The Kill Team Champion's Dual Vigil is the team distilled to its truest pair — two warriors, absolute operations, complete.",
    "Watch Captain": "A Watch Captain's Dual Vigil is leadership reduced to its most essential form — two warriors, every absolute mission, no compromise.",
    "Watch Lieutenant": "The Lieutenant's Dual Vigil is the bond between warriors who earned their rank in the hardest possible company — one brother, every front.",
    "Watch Sergeant": "The Sergeant's Dual Vigil marks a warrior who led from the smallest possible formation — one brother, absolute difficulty, every required mission.",
    "Oathsworn": "Oathsworn's Dual Vigil is an oath kept in the most concentrated form — every absolute mission, exactly one brother, no deviation.",
    "Watch Veteran": "The Veteran's Dual Vigil is the mark of a warrior whose experience runs deep enough to trust a single brother through every absolute operation.",
    "Watch Brother": "A Watch Brother bearing the Dual Vigil has already shown a depth of commitment that veterans twice their seniority would respect.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Assault) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_ASSAULT_OPENINGS: List[str] = [
    "\"Cover me brothers — this one is mine.\" **{name}** said it. **{name}** meant it. The Thunder Hammer has spoken.",
    "The jump pack roared. The Thunder Hammer fell. **{name}** has taken the Terminus threat in the way only an Assault warrior can — close, loud, and final.",
    "**{name}** crossed the battlefield in seconds and delivered the killing blow. The Assault's answer to a Terminus-level threat is the only answer they know: straight at it.",
    "Three classes of Terminus threat. Nine verified kills. **{name}** went in under jump pack thrust and came back through every one.",
    "The Assault class was forged for moments exactly like this — **{name}** has proven it nine times over at the highest threat level the Watch logs.",
]

TERMINUS_SLAYER_ASSAULT_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Assault) is awarded to those who have proven, nine times in verified combat, that the Thunder Hammer is the truest answer to every class of Terminus threat.",
    "To close on a Terminus-level enemy under jump pack thrust, alone in purpose if not alone on the field, is the Assault's highest expression. **{name}** has expressed it nine times.",
    "Where others engage from range or behind shields, the Assault warrior commits everything to the strike. The Terminus Slayer mark is the Watch's recognition of that commitment carried through.",
    "Nine verified kills. Three Terminus classes. The Assault's answer — the jump pack, the Thunder Hammer, the charge — proven against every threat the Watch tracks.",
    "The Terminus Slayer (Assault) is the mark of a warrior who treated every Terminus threat as an opportunity to demonstrate what close-quarters commitment achieves.",
]

TERMINUS_SLAYER_ASSAULT_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven do not cede the charge to any threat. **{name}** has upheld that truth against nine Terminus-level enemies.",
    "Angels of Vengeance": "The Lion's wrath directed at Terminus-level threats — **{name}** has delivered it nine times under jump pack thrust.",
    "Black Templars": "The Eternal Crusade does not pause before a Terminus threat. **{name}** has charged nine of them and returned with nine verified kills.",
    "Bleeding Hearts": "Hunt-rage focused on the greatest target on the field — **{name}** has expressed that focus nine times at Terminus level.",
    "Blood Angels": "The sons of Sanguinius were made for this — the red thirst focused, the Thunder Hammer answered. **{name}** has done it nine times.",
    "Blood Ravens": "Knowledge of the prey, then the leap, then the kill. **{name}** has applied the Blood Raven's doctrine against nine Terminus threats.",
    "Brazen Minotaurs": "The bronze bull charges without hesitation. **{name}** has charged nine Terminus threats under jump pack thrust and left nine verified kills.",
    "Carcharodons": "The Void-born close in silence and strike without warning. **{name}** has done this nine times against Terminus-class enemies.",
    "Carmine Blades": "The fury of Baal channelled into the Thunder Hammer — **{name}** has delivered that fury nine times at the highest threat tier the Watch tracks.",
    "Celestial Lions": "Elysium's sons do not back down from the greatest prey. **{name}** has proven it nine times at Terminus level beneath the jump pack's roar.",
    "Cowled Wardens": "The cowl does not slow the charge. **{name}** has closed on nine Terminus threats and ended them all in the Assault's unambiguous fashion.",
    "Crimson Fists": "The Fist's answer to the mightiest enemy on the field is the same as to any other — close, strike, confirm. **{name}** has confirmed nine Terminus kills.",
    "Dark Angels": "The First Legion's Assault warriors carry the weight of the Crusade's unfinished end. **{name}** has settled nine Terminus accounts on behalf of that legacy.",
    "Dark Krakens": "The deepest predator closes on the greatest prey. **{name}** has been that predator nine times at Terminus level, Thunder Hammer in hand.",
    "Dragonspears": "Fleet warriors know the value of decisive engagement. **{name}** has been decisive nine times at Terminus level — the jump pack launched, the hammer fell.",
    "Death Spectres": "Between living and the kill, there is the charge. **{name}** has made that charge nine times at Terminus level and confirmed every result.",
    "Epsilon Paladins": "For Honour, for Duty, for the kill — **{name}** has answered all three nine times at Terminus level under the Assault's jump pack.",
    "Exorcists": "What was twice-tested now tests Terminus threats. **{name}** has tested nine of them personally and confirmed nine kills.",
    "Flesh Tearers": "Seth's sons show the galaxy what the Red Thirst in the Assault class achieves. **{name}** has shown it nine times at the Watch's highest threat tier.",
    "Genesis Chapter": "Guilliman's doctrine refined into a charge — **{name}** has charged nine Terminus threats in the Assault's fashion and confirmed each kill.",
    "Hawk Lords": "Swift under the jump pack, lethal with the hammer — **{name}** has proven both nine times against Terminus-class enemies in the Watch's logs.",
    "Hospitallers": "The Hospitaller strikes so others may be healed. **{name}** has struck nine Terminus threats from the Assault's position and confirmed each result.",
    "Imperial Fists": "The Fist's answer to a Terminus threat is not a wall — it is a warrior, a jump pack, and a hammer. **{name}** has delivered that answer nine times.",
    "Imperius Reavers": "The Eastern Fringe taught **{name}** to close fast and strike hard. Nine Terminus kills under the Assault class pay that teaching's highest dividend.",
    "Iron Hands": "The Iron Hands do not flinch from Terminus threats. **{name}** has crossed the closing distance nine times and returned with the verification record to prove it.",
    "Iron Hounds": "The Hound locks onto the greatest prey and does not release. **{name}** has locked onto nine Terminus threats and confirmed every kill in the Assault class.",
    "Iron Lords": "The Iron Grip at close range — **{name}** has delivered it nine times at Terminus level beneath jump pack thrust and Thunder Hammer.",
    "Iron Ravens": "The Iron Raven strikes from unexpected angles. **{name}** struck nine Terminus threats from the angle they least predicted — directly, at full speed.",
    "Knights of the Raven": "Patient until the jump pack fires, then absolute. **{name}** has been absolute nine times at Terminus level in the Assault class.",
    "Lamenters": "The cursed Chapter finds its catharsis in the charge. **{name}** has charged nine Terminus threats and confirmed nine kills — the Dual Vigil's hardest cousin.",
    "Marines Errant": "The wanderer who finds purpose in the greatest fight — **{name}** found it nine times at Terminus level, jump pack roaring.",
    "Mentors": "A Mentor who leads by the Thunder Hammer's example. **{name}** has set that example nine times at Terminus level and the Watch has recorded every one.",
    "Minotaurs": "The bull charges hardest toward the greatest resistance. **{name}** charged toward nine Terminus threats and confirmed every kill under the Assault class.",
    "Necropolis Hawks": "Ruins do not slow the Assault's jump pack. **{name}** has closed on nine Terminus threats across whatever ground stood in the way.",
    "Raptors": "Swift across any terrain, lethal at any range — the Raptor Assault warrior charges where others hesitate. **{name}** has done so nine Terminus times.",
    "Raven Guard": "The Raven Guard strike from unexpected angles. **{name}** struck nine Terminus threats from the angle they could not predict — directly, loudly, finally.",
    "Red Scorpions": "Purist form applied to the Assault class — **{name}** has charged nine Terminus threats with textbook precision and confirmed nine kills.",
    "Red Templars": "Fast as the blade, committed as the Crusade — **{name}** has delivered both against nine Terminus threats in the Assault class.",
    "Salamanders": "The sons of Nocturne do not rush for glory's sake — they rush because the threat demands it. **{name}** has answered that demand nine times at Terminus level.",
    "Scythes of the Emperor": "Sotha taught that the greatest threats must be met personally. **{name}** has met nine Terminus threats personally under jump pack thrust.",
    "Sons of Medusa": "Calculated charge, calculated kill — **{name}** has applied the Sons of Medusa's precision to nine Terminus threats in the Assault class.",
    "Space Wolves": "A wolf at the throat of the greatest prey on the field. **{name}** has been that wolf nine times at Terminus level.",
    "Storm Giants": "Tower-tall warriors strike from above. **{name}** has struck nine Terminus threats from the Assault's elevated angle and confirmed every kill.",
    "Tempestuous Angels": "Drossmire's fire channelled into the charge — **{name}** has directed that fire at nine Terminus threats and confirmed nine kills.",
    "The Drakes": "Dragon-fire at close range is the Assault Drake's gift. **{name}** has delivered it nine times at Terminus level in the Watch's logs.",
    "Tome Keepers": "The Chapter records great charges. **{name}**'s nine Terminus kills under the Assault class are already in the book.",
    "Ultramarines": "The sons of Macragge do not cede the charge to any threat. **{name}** has upheld that truth against nine Terminus-level enemies.",
    "White Scars": "Speed above all — **{name}** brought the White Scars' principle directly to nine Terminus-level enemies and walked away nine times.",
    "Wolfspear": "The Dark Terror hunts at speed. **{name}** has hunted nine Terminus threats at the Assault's speed and confirmed every kill in the Watch's records.",
    "Black Shield": "No Chapter claims the kill — only the record. **{name}**'s record shows nine Terminus threats met, nine Thunder Hammers delivered, nine threats resolved.",
}

TERMINUS_SLAYER_ASSAULT_RANK_LINES: Dict[str, str] = {
    "Watch Master": "Where the Watch Master charges, the Thunder Hammer arrives first — at full thrust, without ceremony, and without anything left standing.",
    "High Chaplain": "The litanies the High Chaplain carries into combat are most effective at Thunder Hammer range. The jump pack's arc is the faith made kinetic.",
    "Chief Apothecary": "The Chief Apothecary beneath jump pack thrust has settled on the most direct form of medicine — remove the threat before it requires a response.",
    "Void Warden": "The Void Warden's vigilance resolves at the end of a jump pack's arc. No threat warranting a Thunder Hammer should have the leisure to see the Warden coming.",
    "Forgemaster": "The Forgemaster has examined every weapon the Watch carries. The one brought to Thunder Hammer range under full thrust is the one that requires no further refinement.",
    "Castellan": "The Castellan does not always defend from a fixed position — sometimes the fortress launches itself at jump pack velocity, Thunder Hammer already in hand.",
    "Lord Executioner": "The Lord Executioner who interprets the execution mandate as a jump pack charge at Thunder Hammer distance has interpreted it correctly.",
    "Venerable Dreadnought": "The ancient has carried the close-quarters charge across centuries. Where the jump pack shortens the approach, the Venerable Dreadnought already knew the destination.",
    "Honored Dreadnought": "The Honored Dreadnought needs no novel doctrine to confirm the oldest truth — close the distance, deliver the blow, and the rest follows.",
    "Interred Brother": "Preserved in iron, uninterested in waiting — the Interred Brother beneath jump pack thrust is the Watch's most surprising argument for closing first and asking nothing.",
    "Watch Chaplain": "The Chaplain's creed flies furthest when launched beneath a jump pack. The Thunder Hammer is where the sermon ends and the result begins.",
    "Watch Apothecary": "The Apothecary who masters the direct charge has decided that the finest preventative care is delivered at Thunder Hammer range, before any wound is possible.",
    "Watch Librarian": "The Librarian brings considerable gifts to every engagement. At Thunder Hammer range, the most valuable gift is the willingness to close without hesitation.",
    "Watch Techmarine": "The Techmarine has calibrated the jump pack's thrust with the same precision applied to everything else. The Thunder Hammer's delivery point is exactly where the calculation said it would be.",
    "Watch Keeper": "The Keeper's watch ends when the jump pack fires. There is a form of vigilance that can only be fulfilled at close-quarters distance, Thunder Hammer in hand.",
    "Company Champion": "The Company Champion at jump pack range is the Chapter's finest argument delivered in person — the blade and the hammer both carried to the point of maximum decision.",
    "Kill Team Champion": "The Kill Team Champion who closes beneath jump pack thrust leads the charge the team cannot. The Thunder Hammer at the end of it makes the result available to everyone.",
    "Watch Captain": "The Captain who arrives first under a jump pack's thrust commands in the oldest sense of the word. Thunder Hammer range is where leadership becomes undeniable.",
    "Watch Lieutenant": "The Lieutenant who closes at Thunder Hammer distance has concluded that the correct position for an officer is at the end of the charge, not behind it.",
    "Watch Sergeant": "The Sergeant beneath jump pack thrust shows the squad exactly what the front looks like. The Thunder Hammer confirms the lesson before the squad arrives to learn it.",
    "Oathsworn": "The oath to close on what others cannot reach has a specific mechanism in Assault class. The jump pack provides the commitment; the Thunder Hammer provides the resolution.",
    "Watch Veteran": "The Veteran's experience turns every jump pack launch into certainty. The Thunder Hammer has been delivered enough times that the approach requires no reconsideration.",
    "Watch Brother": "The Watch Brother who masters the Assault class has committed to the close-quarters charge before most have decided it is warranted. The jump pack confirms the decision in flight.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Bulwark) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_BULWARK_OPENINGS: List[str] = [
    "The banner rose. The shield held. And then **{name}** stepped out from behind it and ended the Terminus threat personally.",
    "**{name}** raised the shield, dropped the banner's benefit on waiting brothers, and then walked through whatever was left to finish the Terminus target alone.",
    "\"Cover me brothers — this one is mine.\" From a Bulwark. Nine times. **{name}** has proven that a shield does not only absorb — it enables.",
    "The Bulwark is designed to absorb and support. **{name}** used both, and then turned that platform into nine confirmed Terminus kills.",
    "Nine Terminus kills. A shield. A banner. And a warrior who used both as a launching pad rather than a shelter. That is **{name}**'s Terminus Slayer record.",
]

TERMINUS_SLAYER_BULWARK_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Bulwark) is awarded to those whose shield has faced every class of Terminus threat — and whose blade has answered it, nine times verified.",
    "The Bulwark is a class built for others' survival. The Terminus Slayer mark recognises the Bulwark who turned that survival into a weapon, nine Terminus threats confirmed.",
    "To plant a banner, absorb the Terminus threat's attention, and then close to deliver the killing blow is the Bulwark's singular expression of 'this one is mine.' **{name}** has done it nine times.",
    "Nine verified kills across all three Terminus classes. A shield that protected and a blade that ended. The Terminus Slayer (Bulwark) is the Watch's highest recognition of that combination.",
    "The Terminus Slayer (Bulwark) mark honours the warrior who gave their brothers every possible advantage — and then used that advantage to make nine Terminus kills of their own.",
]

TERMINUS_SLAYER_BULWARK_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven hold the line and then advance through it. **{name}** has done both — nine Terminus kills behind the shield that first protected then enabled.",
    "Angels of Vengeance": "The Lion's sons absorb punishment and return it threefold. **{name}**'s nine Terminus kills are the return on nine operations of absorbed wrath.",
    "Black Templars": "The Templar crusade does not pause behind the shield forever. **{name}** stepped out from behind it nine times to end Terminus threats personally.",
    "Bleeding Hearts": "The pack's shield protects; the pack's blade answers. **{name}** did both — the banner raised, the brothers covered, and nine Terminus threats ended personally.",
    "Blood Angels": "The sons of Sanguinius carry both guardian's vow and warrior's fury. **{name}**'s nine Terminus kills are the fury half — the shield was the vow.",
    "Blood Ravens": "Knowledge of when to stand and when to strike — **{name}** has demonstrated the timing nine times at Terminus level from behind the Bulwark's shield.",
    "Brazen Minotaurs": "The bronze shield absorbs; the bronze blade answers. **{name}** has made that answer nine times at Terminus level with banner planted and kills confirmed.",
    "Carcharodons": "The Void-born shield is silence. **{name}**'s nine Terminus kills are what the silence produced — nine threats ended from behind the Bulwark's position.",
    "Carmine Blades": "Baal's fury directed through shield and banner — **{name}** has turned every Bulwark advantage into nine Terminus kills.",
    "Celestial Lions": "Elysium's sons stand firm and strike true. **{name}** stood firm behind the shield and struck true nine times at the Watch's highest threat tier.",
    "Cowled Wardens": "The Warden's shield is both protection and platform. **{name}** used both aspects against nine Terminus threats and confirmed every kill.",
    "Crimson Fists": "Few in number means every shield counts twice. **{name}** made the Bulwark's shield count nine times over — nine Terminus kills confirmed.",
    "Dark Angels": "The First Legion's Bulwark warriors hold the line in silence and strike in certainty. **{name}** has struck in that certainty nine Terminus times.",
    "Dark Krakens": "The deep holds fast and strikes when the moment is right. **{name}** held fast behind the shield and struck at nine Terminus threats with full certainty.",
    "Dragonspears": "Fleet warriors shield their brothers and answer with the blade. **{name}** has answered nine times at Terminus level from the Bulwark's position.",
    "Death Spectres": "Between death and the Death Spectre'ss shield there is only the Terminus threat. **{name}** has placed nine Terminus threats on the wrong side of that shield.",
    "Epsilon Paladins": "The Paladin shields with honour and strikes with duty. **{name}** has done both nine times at Terminus level — nine kills confirmed behind banner and shield.",
    "Exorcists": "Thrice-tested warriors do not hide behind the shield — they use it. **{name}** has used it as a launching platform for nine Terminus kills.",
    "Flesh Tearers": "The Flesh Tearer's shield is a temporary restraint. **{name}** restrained the fury long enough to plant the banner, then released it nine Terminus times.",
    "Genesis Chapter": "Guilliman's doctrine applied to the Bulwark class — banner, shield, kill. **{name}** has run that sequence nine times at Terminus level.",
    "Hawk Lords": "The Hawk Lord's shield is swift as their charge. **{name}** has made that shield into nine Terminus opportunities — every one confirmed.",
    "Hospitallers": "The Hospitaller shields by creed. **{name}** has extended that creed into nine Terminus kills — the shield protected the brothers, the blade answered the threat.",
    "Imperial Fists": "A Fist's shield was forged to hold. **{name}** proved it holds against Terminus threats too — and that the blade behind it is just as reliable, nine times over.",
    "Imperius Reavers": "The Eastern Fringe taught **{name}** to absorb and strike back. Nine Terminus kills from behind the Bulwark's shield are the record of that lesson applied.",
    "Iron Hands": "The Iron Hands' Bulwark is efficiency made physical — shield, banner, kill. **{name}** has applied that efficiency nine times at Terminus level.",
    "Iron Hounds": "The Hound's shield is the pack's anchor. **{name}** anchored nine Terminus engagements and used the position to confirm nine kills personally.",
    "Iron Lords": "The Iron Grip begins behind the shield. **{name}** has gripped nine Terminus threats after planting the banner — nine kills confirmed.",
    "Iron Ravens": "The Iron Raven's shield conceals the strike until the moment it cannot. **{name}**'s nine Terminus kills are what emerged from behind the concealment.",
    "Knights of the Raven": "Patient behind the shield, precise with the strike — **{name}** has demonstrated both nine times at Terminus level.",
    "Lamenters": "The cursed Chapter protects what it still has. **{name}** protected brothers behind the shield and then answered nine Terminus threats personally.",
    "Marines Errant": "The wandering Bulwark finds their purpose in the protection and then the answer. **{name}** has answered nine Terminus threats personally.",
    "Mentors": "The Mentor teaches by doing. **{name}** has done the Bulwark's full mission nine times at Terminus level — the lesson is in the kill record.",
    "Minotaurs": "The bull's shield is the charge's preparation. **{name}** prepared nine Terminus engagements with banner and shield, then finished nine with the blade.",
    "Necropolis Hawks": "Ruined ground needs a Bulwark's anchor. **{name}** has been that anchor nine times at Terminus level — and confirmed nine kills from that position.",
    "Raptors": "The Raptor Bulwark disappears behind the shield and reappears at the kill. **{name}** has made that reappearance nine times at Terminus level.",
    "Raven Guard": "The Raven Guard do not advertise their kills. **{name}**'s nine Terminus kills speak for themselves — the shield concealed the warrior until the moment it didn't need to.",
    "Red Scorpions": "Purist form applied to shield, banner, and blade — **{name}** has run the Bulwark's full sequence nine times at Terminus level with verified results.",
    "Red Templars": "Fast as the blade, faithful to the shield's purpose — **{name}** has used the Bulwark's platform for nine Terminus kills without deviation.",
    "Salamanders": "The sons of Nocturne protect. **{name}** protected their brothers and then used that protective position to secure nine Terminus kills of their own.",
    "Scythes of the Emperor": "Sotha's lesson is protection and answer. **{name}** protected and answered nine Terminus threats personally from the Bulwark's position.",
    "Sons of Medusa": "Calculated shield deployment, calculated kill — **{name}** has applied the Sons of Medusa's precision to nine Terminus threats from behind the banner.",
    "Space Wolves": "The pack's shield is the pack's strength. **{name}** made that shield into a weapon as well — nine Terminus kills beneath the Fenrisian banner.",
    "Storm Giants": "Tower-tall, shield-forward, blade-ready — **{name}** has confirmed nine Terminus kills from the Bulwark's position in the Watch's records.",
    "Tempestuous Angels": "Drossmire's fire banked behind the shield, then released — **{name}** has released it nine times at Terminus level with confirmed results.",
    "The Drakes": "The Drake's shield is the brood's protection. **{name}** protected the brood and then answered nine Terminus threats personally.",
    "Tome Keepers": "The Tome Keeper's shield entry is already written. **{name}** — nine Terminus kills from behind the Bulwark's position — the record is clear.",
    "Ultramarines": "The sons of Macragge serve the team and the mission. **{name}** served both by securing nine Terminus kills behind the most disciplined shield in the Watch.",
    "White Scars": "Speed does not require a jump pack. **{name}** has proven the Bulwark can close on Terminus threats quickly enough — nine times over.",
    "Wolfspear": "The Dark Terror's shield protects the hunt. **{name}** protected the hunt for nine Terminus engagements and confirmed every kill personally.",
    "Black Shield": "No banner marks the chapter — but nine Terminus kills mark the warrior. **{name}**'s record stands without lineage, without qualification.",
}

TERMINUS_SLAYER_BULWARK_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's Bulwark discipline is the Watch's doctrine in two movements — the banner for brothers first, the blade for the threat second. Neither phase is optional.",
    "High Chaplain": "The High Chaplain plants the banner for the brothers and draws the blade for the threat. Faith is not passive behind the shield — it is the preparation for what follows.",
    "Chief Apothecary": "The Chief Apothecary behind shield and banner has mastered the Bulwark's first truth: protect what matters, then answer what threatens it. Both acts belong to the same warrior.",
    "Void Warden": "The Void Warden's shield is a watching post. The vigil continues until the banner is planted and the brothers are covered — and then the blade settles what remains.",
    "Forgemaster": "The Forgemaster understands that the shield and the blade are not opposed tools. They are sequential ones, and the Bulwark's craft is knowing exactly when the sequence advances.",
    "Castellan": "The Castellan's walls take every form available — including the shield that comes before the blade. The fortress is never finished until both phases are complete.",
    "Lord Executioner": "The Lord Executioner who works from behind a banner has not abandoned the mandate — they have staged it. The shield absorbs; the blade executes on schedule.",
    "Venerable Dreadnought": "The ancient has watched warriors learn the Bulwark's lesson across centuries. The shield comes first. The blade earns its moment. This is not a doctrine — it is an understanding.",
    "Honored Dreadnought": "The Honored Dreadnought endures long enough to complete both phases of the Bulwark's work — the banner planted for brothers, the blade drawn when the position has served its purpose.",
    "Interred Brother": "The Interred Brother behind shield and banner is the Watch's most fortified position made mobile. The blade that emerges after the banner has been planted is the position's second phase.",
    "Watch Chaplain": "The Chaplain behind the banner is the creed made structural — brothers sheltered by the shield's width, the blade held for the moment the creed requires answering what threatens them.",
    "Watch Apothecary": "The Apothecary behind the Bulwark's shield protects in both directions — the banner gives brothers what the narthecium would have needed, and the blade settles what made it necessary.",
    "Watch Librarian": "The Librarian's Bulwark discipline is strategy made physical — the banner positioned where it serves, the shield held on purpose, the blade drawn precisely when the position it created has done its work.",
    "Watch Techmarine": "The Techmarine behind shield and banner has assessed the engagement sequence and found the Bulwark's two-phase approach to be the most mechanically sound available.",
    "Watch Keeper": "The Keeper's vigil has a second act. After the banner is planted and the brothers are covered, the blade answers whatever the shield absorbed. Both phases belong to the same watch.",
    "Company Champion": "The Company Champion behind the Bulwark's shield is the Chapter's defender in the fullest sense — the blade that emerges from the protected position is the Chapter's answer after the protection has been given.",
    "Kill Team Champion": "The Kill Team Champion who masters the Bulwark class gives the team everything the shield and banner offer, and then gives the team one more thing — the blade drawn when the position has served its purpose.",
    "Watch Captain": "A Watch Captain's Bulwark discipline is command demonstrated in sequence — the banner for the brothers before the blade for the threat. Both phases are the officer's responsibility.",
    "Watch Lieutenant": "The Lieutenant who raises the banner first and draws the blade second has learned the Bulwark's lesson. The shield is not the end of the engagement — it is the beginning of the second phase.",
    "Watch Sergeant": "The Sergeant's Bulwark discipline shows the squad what sequence looks like in practice — the shield deployed, the banner raised, and then the blade drawn at exactly the right moment.",
    "Oathsworn": "The oath behind the Bulwark's shield comes in two parts. The banner honors the first part. The blade honors the second. Neither is ever forgotten by the warrior who has sworn both.",
    "Watch Veteran": "The Veteran knows the Bulwark's sequence well enough that the transition from banner to blade requires no decision. By this point, the shield has done its work and the blade knows it.",
    "Watch Brother": "The Watch Brother who masters the Bulwark class has already learned what takes most warriors years to understand — protect first, answer second, and never confuse the order.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Heavy) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_HEAVY_OPENINGS: List[str] = [
    "The barrier locked. The heavy weapon levelled. And **{name}** made nine Terminus-level threats understand what crowd control means at the highest possible target.",
    "\"Cover me brothers — this one is mine.\" From a warrior carrying weaponry designed for whole armies. **{name}** applied it to nine Terminus targets specifically.",
    "**{name}** brought the heaviest weapon in the Watch's standard toolkit to bear on nine Terminus threats and verified every kill.",
    "The Heavy is the Watch's powerhouse. **{name}** has turned that power toward nine Terminus-level enemies and stood behind verified results every time.",
    "Nine Terminus kills. Heavy weaponry. A barrier that protected long enough for **{name}** to ensure every shot counted.",
]

TERMINUS_SLAYER_HEAVY_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Heavy) is awarded to those who brought heavy weaponry to bear on every class of Terminus threat — nine verified kills, three classes, one warrior.",
    "The Heavy class exists to dominate the battlefield. The Terminus Slayer mark recognises the Heavy who directed that domination at nine Terminus-level threats specifically.",
    "To place a barrier, secure a firing position, and personally eliminate nine Terminus-class enemies is the Heavy's 'cover me brothers' — volume, accuracy, and finality.",
    "Nine verified kills across all three Terminus types. The barrier held, the weapon spoke, and **{name}** recorded the results. The Terminus Slayer (Heavy) is that record's highest seal.",
    "The Terminus Slayer (Heavy) honours the warrior who applied the Watch's most powerful standard weaponry to the Watch's most dangerous standard targets — nine times, each verified.",
]

TERMINUS_SLAYER_HEAVY_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven's Heavy warriors lay down firepower without ceremony. **{name}** laid it down nine times at Terminus level and confirmed every kill.",
    "Angels of Vengeance": "The Lion's wrath in heavy weapon form — **{name}** directed it at nine Terminus threats and produced nine confirmed kills.",
    "Black Templars": "The Crusade requires warriors who can end what others cannot reach. **{name}** reached nine Terminus threats with heavy weaponry and ended them.",
    "Bleeding Hearts": "Pack fury focused through a heavy weapon barrel — **{name}** has focused it at nine Terminus threats and confirmed every result.",
    "Blood Angels": "The sons of Sanguinius carry fury even behind heavy weapon batteries. **{name}** directed that fury at nine Terminus-level targets and verified every result.",
    "Blood Ravens": "Knowledge of the target's weakness, then the heavy weapon's answer — **{name}** has applied that sequence nine times at Terminus level.",
    "Brazen Minotaurs": "The bronze herd's heaviest element is its most devastating. **{name}** has devastated nine Terminus threats with heavy weaponry in the Watch's confirmed logs.",
    "Carcharodons": "The Void-born Heavy speaks through heavy weapon fire. **{name}** has spoken nine times at Terminus level — every word confirmed.",
    "Carmine Blades": "Baal's fury expressed through crowd control weaponry — **{name}** has controlled nine Terminus threats personally and confirmed each kill.",
    "Celestial Lions": "Elysium's sons bring the heaviest response to the heaviest threat. **{name}** brought it nine times at Terminus level and confirmed every result.",
    "Cowled Wardens": "The Warden's heavy weapon clears the path. **{name}** has cleared nine Terminus threats from the path with verified kills in the Watch's logs.",
    "Crimson Fists": "A Fist's heavy weapon doctrine is attrition made precise. **{name}** directed that precision at nine Terminus-level enemies and produced nine confirmed kills.",
    "Dark Angels": "The First Legion's Heavy warriors are patient until they are not. **{name}**'s nine Terminus kills are the record of patience applied to maximum effect.",
    "Dark Krakens": "The deep's heaviest element crushes what the lighter elements cannot. **{name}** has crushed nine Terminus threats with heavy weaponry and confirmed every kill.",
    "Dragonspears": "Fleet warriors bring fleet firepower. **{name}** brought the heaviest of it to bear on nine Terminus threats and confirmed nine kills.",
    "Death Spectres": "Between life and the heavy weapon's muzzle there is only trajectory. **{name}** has calculated that trajectory nine times at Terminus level.",
    "Epsilon Paladins": "For Honour, for Duty, for the heavy weapon's absolute answer — **{name}** has provided that answer nine times at Terminus level.",
    "Exorcists": "What the barrier protects, the heavy weapon ends. **{name}** has ended nine Terminus threats from behind the barrier with confirmed kills in the record.",
    "Flesh Tearers": "Seth's sons do not lack for stopping power. **{name}** provided the heaviest of it against nine Terminus threats — all confirmed.",
    "Genesis Chapter": "Guilliman's doctrine applied to crowd control — target the greatest threat with the greatest weapon. **{name}** has applied it nine times at Terminus level.",
    "Hawk Lords": "Swift positioning, heavy fire — **{name}** has positioned behind the barrier and confirmed nine Terminus kills with heavy weaponry.",
    "Hospitallers": "The Hospitaller's heavy weapon protects by eliminating. **{name}** has eliminated nine Terminus threats personally with heavy weaponry and confirmed each kill.",
    "Imperial Fists": "The Fist's heavy weapon doctrine is attrition made precise. **{name}** directed that precision at nine Terminus-level enemies and produced nine confirmed kills.",
    "Imperius Reavers": "The Eastern Fringe taught **{name}** to bring the heaviest response to the heaviest threat. Nine Terminus kills confirm the lesson was learned.",
    "Iron Hands": "The Iron Hands trust in machinery above flesh. **{name}** trusted in heavy weaponry above all else — nine Terminus threats confirmed the trust was warranted.",
    "Iron Hounds": "The Hound's heaviest bite is the kill. **{name}** has bitten nine Terminus threats with heavy weaponry and confirmed every result in the Watch's logs.",
    "Iron Lords": "The Iron Grip expressed through the heavy weapon's barrel — **{name}** has gripped nine Terminus threats at range and confirmed nine kills.",
    "Iron Ravens": "The Iron Raven's heavy weapon speaks without echo in the void. **{name}** has spoken nine times at Terminus level and left nine confirmed kills.",
    "Knights of the Raven": "Patient positioning, then the heavy weapon's answer — **{name}** has answered nine Terminus threats from that patient position.",
    "Lamenters": "The cursed Chapter's heavy warriors answer grief with firepower. **{name}**'s nine Terminus kills are that answer at its most deliberate.",
    "Marines Errant": "The wandering Heavy finds the ideal firing position and stays there. **{name}** stayed nine times at Terminus level and confirmed nine kills.",
    "Mentors": "The Mentor teaches by demonstrating the barrier's value and the heavy weapon's precision. **{name}** has demonstrated both nine times at Terminus level.",
    "Minotaurs": "The bull's heaviest element levels what stands before it. **{name}** has levelled nine Terminus threats with heavy weaponry and confirmed every kill.",
    "Necropolis Hawks": "Ruined ground is ideal heavy weapon territory. **{name}** has used that ground nine times to confirm Terminus kills with heavy weaponry.",
    "Raptors": "The Raptor Heavy establishes position without sound and fires without mercy. **{name}** has fired on nine Terminus threats and confirmed nine kills.",
    "Raven Guard": "The Raven Guard prefer silence. **{name}**'s heavy weapon said everything that needed saying — nine times, at Terminus level.",
    "Red Scorpions": "Purist form applied to heavy weapon engagement — **{name}** has engaged nine Terminus threats with textbook precision and confirmed nine kills.",
    "Red Templars": "Fast to position, precise with the heavy weapon — **{name}** has confirmed nine Terminus kills from behind the barrier in the Watch's records.",
    "Salamanders": "The sons of Nocturne pour everything into their work. **{name}** poured heavy weapon firepower into nine Terminus threats, with verified results every time.",
    "Scythes of the Emperor": "Sotha's sons know that overwhelming firepower is the answer to overwhelming threats. **{name}** has provided it nine times at Terminus level.",
    "Sons of Medusa": "Calculated heavy weapon deployment — **{name}** has calculated nine Terminus engagements and confirmed nine kills with the precision the Sons demand.",
    "Space Wolves": "A Fenrisian pack does not lack for firepower. **{name}** brought the heaviest portion of it directly to nine Terminus threats.",
    "Storm Giants": "The Giant's heaviest weapon speaks the loudest. **{name}** has spoken nine times at Terminus level and the Watch has recorded every word.",
    "Tempestuous Angels": "Drossmire's fire in heavy weapon form — **{name}** has directed it at nine Terminus threats and confirmed every kill in the Watch's logs.",
    "The Drakes": "Dragon-fire at sustained range — **{name}** has sustained it against nine Terminus threats and confirmed nine kills.",
    "Tome Keepers": "The Chapter records great firepower. **{name}**'s nine Terminus kills with heavy weaponry are already in the book — the barrier held, the weapon spoke.",
    "Ultramarines": "The sons of Macragge do not waste firepower. **{name}** applied every round with the precision the Codex demands — nine Terminus threats confirmed.",
    "White Scars": "Speed without stopping power is insufficient. **{name}** provided the stopping power against nine Terminus threats that even the fastest enemy could not outrun.",
    "Wolfspear": "The Dark Terror's heaviest hunters confirm the kill with heavy weapon certainty. **{name}** has confirmed nine Terminus kills in that fashion.",
    "Black Shield": "No lineage accompanies the record. Nine Terminus kills in heavy weapon class. **{name}**'s Terminus Slayer mark requires nothing else.",
}

TERMINUS_SLAYER_HEAVY_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master behind the barrier with a heavy weapon in hand has taken the Watch's most powerful standard tools and applied them from the most defensible position available. The question of whether this is sufficient answers itself.",
    "High Chaplain": "The High Chaplain's heaviest sermon is delivered from behind the barrier. The heavy weapon's argument is theological in its own way — sustained, authoritative, and final.",
    "Chief Apothecary": "The Chief Apothecary behind a barrier with a heavy weapon has determined that the most effective form of care is applied before the wound can occur. Sustained firepower makes this determination in bulk.",
    "Void Warden": "The Void Warden's watch from behind the barrier is the vigil fortified — the heavy weapon extending the reach of the position further than any wall the watch could occupy.",
    "Forgemaster": "The Forgemaster knows which weapons the Watch carries and which position makes those weapons most effective. The barrier confirms the position; the heavy weapon confirms the Forgemaster's assessment.",
    "Castellan": "The Castellan's barrier is a wall in miniature — portable, sufficient, and deployed forward of the threat rather than behind it. The heavy weapon behind it is the wall's active component.",
    "Lord Executioner": "The Lord Executioner behind the barrier with sustained heavy fire has determined that the warrant does not require proximity. Maximum range and maximum firepower produce the same finality.",
    "Venerable Dreadnought": "The ancient behind the barrier demonstrates that the longest effective range and the heaviest weapons are as valid a counsel as any — and that patience is the virtue that makes both possible.",
    "Honored Dreadnought": "The Honored Dreadnought behind the barrier is endurance applied to the heaviest position available. The heavy weapon speaks for as long as the position demands.",
    "Interred Brother": "The Interred Brother behind the barrier is the Watch's most sustained position — the heavy weapon held at the angle the preserved warrior calculated, for as long as the engagement requires.",
    "Watch Chaplain": "The Chaplain's creed from behind the barrier is the Watch's heaviest theology — the barrier shelters brothers, the heavy weapon delivers the argument at sustained range.",
    "Watch Apothecary": "The Apothecary behind the Heavy's barrier has made the barrier itself the first form of care — the heavy weapon ensuring that what shelters behind it never has to be mended.",
    "Watch Librarian": "The Librarian behind the Heavy's barrier has identified the most effective firing solution and committed to it. Sustained fire at the correct target is not a compromise — it is the conclusion of the calculation.",
    "Watch Techmarine": "The Techmarine behind the barrier has assessed the structural integrity, verified the firing angles, and brought the heavy weapon to bear with the mechanical satisfaction the Omnissiah expects.",
    "Watch Keeper": "The Keeper's vigil from behind the barrier with a heavy weapon is the watch at its most fortified — the position held, the weapon speaking, the vigil sustained for as long as the engagement requires.",
    "Company Champion": "The Company Champion behind the Heavy's barrier is the Chapter's heaviest argument — sustained fire at the range where the heavy weapon is most effective, the position held until the argument is resolved.",
    "Kill Team Champion": "The Kill Team Champion who holds the Heavy's firing position gives the team the most sustained form of fire support available. The barrier makes the position permanent; the heavy weapon makes it lethal.",
    "Watch Captain": "A Watch Captain who holds the Heavy's position is an officer who understands that the best position is not always the most forward one — it is the one from which the heavy weapon speaks most effectively.",
    "Watch Lieutenant": "The Lieutenant who masters the Heavy class has learned that some engagements are won from a fixed position with sustained fire rather than a charge. The barrier is the decision made physical.",
    "Watch Sergeant": "The Sergeant behind the barrier shows the squad what the Heavy's position looks like when it is held correctly — the heavy weapon speaking at range, the squad supported by the fire it provides.",
    "Oathsworn": "The oath behind the Heavy's barrier is sustained in every sense — the position held, the weapon firing, the vow maintained for as long as the engagement requires the heavy weapon to speak.",
    "Watch Veteran": "The Veteran behind the Heavy's barrier has held enough positions to know when the barrier is where the engagement will be decided. The heavy weapon confirms the assessment over sustained fire.",
    "Watch Brother": "The Watch Brother who masters the Heavy class has committed to the discipline of holding the position — the barrier set, the heavy weapon levelled, the line held for as long as the engagement requires.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Sniper) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_SNIPER_OPENINGS: List[str] = [
    "The cloak dropped. The shot arrived before the Terminus threat knew its hunter. **{name}** has done this nine times.",
    "\"Cover me brothers — this one is mine.\" From a distance most warriors cannot reach, **{name}** made nine Terminus threats into confirmed kills.",
    "**{name}** located the high ground, found the angle, cloaked when necessary, and precisely eliminated nine Terminus-level threats across three classes.",
    "Distance is not a weakness for the Sniper — it is the weapon. **{name}** wielded it against nine Terminus threats and left nine verified kills in the record.",
    "Nine kills. Three Terminus types. One warrior who never needed to be close to be lethal. **{name}** has made that truth official.",
]

TERMINUS_SLAYER_SNIPER_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Sniper) is awarded to those who eliminated every class of Terminus threat from range — nine verified kills, each precise, each final.",
    "The Sniper class is built on precision and distance. The Terminus Slayer mark recognises the Sniper who applied that precision to nine Terminus-level threats at range.",
    "To cloak, to aim, to fire at the exact point a Terminus threat is vulnerable — that is the Sniper's 'cover me brothers.' **{name}** has said it nine times in action.",
    "Nine verified kills across all three Terminus classes. No close engagement required. **{name}** ended nine Terminus threats from exactly the distance the Sniper was designed to operate.",
    "The Terminus Slayer (Sniper) honours the warrior who removed the most dangerous enemy on the battlefield from the position their enemy least expected — nine times, precisely, finally.",
]

TERMINUS_SLAYER_SNIPER_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven's patience runs deep. **{name}** applied it at Sniper range nine times against Terminus threats — each shot a secret confirmed.",
    "Angels of Vengeance": "The Lion's wrath delivered from distance — **{name}** has delivered it nine times against Terminus-level threats without once closing to melee.",
    "Black Templars": "The Templar's faith extends to the trigger as freely as to the blade. **{name}** expressed that faith nine times against Terminus-level threats from range.",
    "Bleeding Hearts": "The pack's eyes find the prey at distance. **{name}** found nine Terminus threats from range and confirmed each kill before any brother needed to close.",
    "Blood Angels": "The sons of Sanguinius carry extraordinary senses into battle. **{name}** used those senses to find the angles on nine Terminus threats before those threats found them.",
    "Blood Ravens": "Knowledge of the target before the engagement defines the Blood Raven's Sniper. **{name}** has demonstrated that knowledge nine times at Terminus level.",
    "Brazen Minotaurs": "The bronze patience holds longest at distance. **{name}** has held it nine times at Terminus level — the scope found the angle, the shot confirmed the kill.",
    "Carcharodons": "The Void-born see in darkness. **{name}** has seen nine Terminus threats from distances that concealed the warrior entirely — nine kills confirmed.",
    "Carmine Blades": "Baal's curse does not reach the Sniper's range. **{name}** has maintained that range against nine Terminus threats and confirmed every kill.",
    "Celestial Lions": "Elysium's sons find the angle. **{name}** found the angle on nine Terminus threats from the Sniper's preferred distance — all confirmed.",
    "Cowled Wardens": "The cowl hides the Sniper perfectly. **{name}** has hidden from nine Terminus threats and confirmed each kill before being detected.",
    "Crimson Fists": "A Fist's precision is not limited to the melee. **{name}** proved it at range, nine times, against targets the Watch considers its greatest individual threats.",
    "Dark Angels": "The Dark Angels operate with layered patience. **{name}**'s nine Terminus kills from range are patience's perfect expression — the target never chose the encounter.",
    "Dark Krakens": "The deep's most patient hunters wait longest. **{name}** waited for nine Terminus threats at Sniper range and confirmed every kill.",
    "Dragonspears": "Fleet warriors know the value of the long shot. **{name}** has taken it nine times at Terminus level from positions no threat could close in time.",
    "Death Spectres": "Between the Spectre and the target there is only distance and certainty. **{name}** has closed that distance with nine Terminus kills from range.",
    "Epsilon Paladins": "For Honour, for Duty, for the precise shot — **{name}** has provided it nine times at Terminus level from the Sniper's preferred range.",
    "Exorcists": "The cloak conceals; the shot reveals. **{name}** has revealed nine Terminus threats' vulnerability and confirmed nine kills from the Sniper's position.",
    "Flesh Tearers": "The Red Thirst is quietest at distance. **{name}** has kept their distance from nine Terminus threats — close enough only to confirm the kill.",
    "Genesis Chapter": "Guilliman's doctrine applied to precision engagement — one shot, one kill, nine times at Terminus level. **{name}**'s record is doctrinally sound.",
    "Hawk Lords": "The Hawk Lord sees furthest. **{name}** has seen nine Terminus threats from the greatest distance the Sniper class allows — and confirmed every kill.",
    "Hospitallers": "The Hospitaller Sniper protects by eliminating at range before harm reaches the brothers. **{name}** has done this nine times at Terminus level.",
    "Imperial Fists": "A Fist's precision is not limited to the melee. **{name}** proved it at range, nine times, against targets the Watch considers its greatest individual threats.",
    "Imperius Reavers": "The Eastern Fringe taught **{name}** to find the angle no enemy expects. Nine Terminus kills from Sniper range confirm the lesson was mastered.",
    "Iron Hands": "The calculation was performed. The variables were assessed. **{name}** executed nine Terminus kills with the efficiency the Iron Hands demand from every engagement.",
    "Iron Hounds": "The Hound tracks at distance as readily as close. **{name}** has tracked nine Terminus threats from Sniper range and confirmed every kill.",
    "Iron Lords": "The Iron Lord's long sight finds the weakness at range. **{name}** has found nine Terminus weaknesses from the Sniper's position and confirmed every kill.",
    "Iron Ravens": "The Iron Raven's cloak is the Sniper's best ally. **{name}** has used both to confirm nine Terminus kills from positions the enemy never identified.",
    "Knights of the Raven": "The Raven's patience is the Sniper's virtue. **{name}** has been patient nine times at Terminus level — the cloak deployed, the scope aligned, the kill confirmed.",
    "Lamenters": "The cursed Chapter finds clarity at distance. **{name}** has found it nine times at Terminus level — the scope steady, the result confirmed.",
    "Marines Errant": "The errant warrior finds the best angle wherever they are. **{name}** has found nine Terminus angles from range and confirmed every kill.",
    "Mentors": "The Mentor demonstrates the Sniper's value from range. **{name}** has demonstrated it nine times at Terminus level — cloaked, precise, confirmed.",
    "Minotaurs": "The bull does not always charge. **{name}** has demonstrated the Minotaur's patience nine times at Terminus level from the Sniper's position with confirmed results.",
    "Necropolis Hawks": "The Hawk sees from the highest ruin's peak. **{name}** has seen nine Terminus threats from that elevation and confirmed every kill.",
    "Raptors": "This is the Raven Guard's Sniper in their truest form — **{name}** chose the angle, cloaked, and ended nine Terminus threats from positions they never yielded.",
    "Raven Guard": "This is the Raven Guard's Sniper in their truest form — **{name}** chose the angle, cloaked, and ended nine Terminus threats from positions they never yielded.",
    "Red Scorpions": "Purist precision applied at Sniper range — **{name}** has confirmed nine Terminus kills with the exactness the Red Scorpions demand of every engagement.",
    "Red Templars": "Fast to position, precise at range — **{name}** has confirmed nine Terminus kills from the Sniper's position with the Red Templar's characteristic efficiency.",
    "Salamanders": "The sons of Nocturne are deliberate in all things. **{name}**'s nine Terminus kills are deliberateness applied to the Watch's most demanding targets.",
    "Scythes of the Emperor": "Sotha's sons find the precise angle. **{name}** has found it nine times at Terminus level — the Sniper's scope aligned, the kill confirmed.",
    "Sons of Medusa": "Calculated long-range precision — **{name}** has calculated nine Terminus engagements from Sniper range and confirmed every kill.",
    "Space Wolves": "The wolf selects the moment of the hunt as carefully as the quarry. **{name}** selected nine Terminus moments from range and never missed.",
    "Storm Giants": "Tower-tall warriors see furthest. **{name}** has seen nine Terminus threats from the Sniper's elevated position and confirmed every kill.",
    "Tempestuous Angels": "Drossmire's fire expressed as precision at range — **{name}** has expressed it nine times against Terminus threats from the Sniper's position.",
    "The Drakes": "Dragon-fire delivered at precise long range — **{name}** has delivered it nine times at Terminus level from positions no threat could reach.",
    "Tome Keepers": "The Chapter records precise engagements. **{name}**'s nine Terminus kills from Sniper range — cloaked, aimed, confirmed — are already in the book.",
    "Ultramarines": "The sons of Macragge value precision in all things. **{name}** expressed that precision against nine Terminus threats at the distance the Codex recommends.",
    "White Scars": "The hunt chose a new path — not the charge, but the patient shot. **{name}** adapted the White Scars' principle to nine Terminus kills at range.",
    "Wolfspear": "The Dark Terror's long sight finds the prey before the prey finds the pack. **{name}** has found nine Terminus threats from Sniper range and confirmed every kill.",
    "Black Shield": "The warrior behind the scope leaves no trace but the result. **{name}**'s result is nine Terminus kills. The record confirms everything the Watch requires.",
}

TERMINUS_SLAYER_SNIPER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master behind the scope has decided that authority does not require proximity. The cloak, the angle, and the patience to wait for certainty — that is command at maximum range.",
    "High Chaplain": "The High Chaplain's litanies carry furthest when delivered from concealment. The scope finds the angle; the trigger is the sermon's conclusion. The target's awareness is irrelevant.",
    "Chief Apothecary": "The Chief Apothecary behind the Sniper's cloak has chosen prevention over remedy — the most precise intervention available, delivered at the range where the threat cannot close before the result is already final.",
    "Void Warden": "The Void Warden watches longest at the furthest range. From behind the cloak, the vigil is perfect — the threat observed, the angle confirmed, the shot placed before the threat can act on being watched.",
    "Forgemaster": "The Forgemaster has calibrated the Sniper's tools to their theoretical maximum. The cloak, the scope, the precise application of force at distance — all of it performs exactly as the Omnissiah intended.",
    "Castellan": "The Castellan's longest wall is the range the Sniper holds. Nothing that threatens from inside the scope's reach has any path to the position — the cloak ensures it stays that way.",
    "Lord Executioner": "The Lord Executioner who delivers the warrant from concealment has found the most final form of execution — the target has no opportunity to contest the order, no way to locate the officer, and no forewarning whatsoever.",
    "Venerable Dreadnought": "The ancient has learned patience in ways few warriors can comprehend. From behind the Sniper's cloak, that patience resolves the moment the angle is certain.",
    "Honored Dreadnought": "The Honored Dreadnought behind the scope is endurance applied to precision — the position held, the cloak maintained, the shot fired exactly when the engagement demands it.",
    "Interred Brother": "The Interred Brother beneath the Sniper's cloak is the Watch's most patient combatant — the preserved warrior who waited for the angle and took it from the range no one expected.",
    "Watch Chaplain": "The Chaplain's faith from behind the scope is quiet and certain. The cloak conceals the warrior; the trigger delivers the creed at maximum range.",
    "Watch Apothecary": "The Apothecary at Sniper range has determined that the most effective form of care is placed before the wound can occur. The cloak ensures the placement is made from a distance that cannot be closed in time.",
    "Watch Librarian": "The Librarian behind the scope has calculated the optimal approach and found it to be patience, concealment, and the single shot placed where it cannot be recovered from.",
    "Watch Techmarine": "The Techmarine behind the Sniper's rifle has assessed the weapon's effective range, the target's vulnerabilities, and the optimal deployment of the cloak — the shot that follows is the calculation's conclusion.",
    "Watch Keeper": "The Keeper behind the scope watches longest and from furthest. The cloak is the vigil's form; the trigger is its resolution.",
    "Company Champion": "The Company Champion at Sniper range is the Chapter's finest precision — the blade's equivalent at distance, delivered from behind the cloak where no counterargument is possible.",
    "Kill Team Champion": "The Kill Team Champion who masters the Sniper class gives the team its longest reach — the cloak covering the approach, the scope finding the angle, the shot removing what the team could not otherwise safely close on.",
    "Watch Captain": "A Watch Captain behind the scope leads from a position that cannot be countered. The cloak conceals the command; the trigger exercises it from the range the threat cannot bridge.",
    "Watch Lieutenant": "The Lieutenant who masters the Sniper class has learned that precision at range serves the team as fully as a charge at close quarters — from behind the cloak, both the officer and the mission are effectively invisible.",
    "Watch Sergeant": "The Sergeant's Sniper discipline teaches the squad that the furthest position is sometimes the most effective one. The cloak stabilises the approach; the scope confirms when 'effective' becomes 'final.'",
    "Oathsworn": "The oath behind the Sniper's cloak is fulfilled at the furthest possible range — the vow that no threat warranting a scope's attention will outlast the patience required to place the shot correctly.",
    "Watch Veteran": "The Veteran behind the scope has spent enough time in the cloak to know exactly when to fire. The patience required to reach that certainty is the Sniper class's deepest discipline.",
    "Watch Brother": "The Watch Brother who masters the Sniper class has already learned what most warriors sit with for years — that the cloak, the patience, and the precisely placed shot are the fullest expression of what one warrior can accomplish at range.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Tactical) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_TACTICAL_OPENINGS: List[str] = [
    "The auspex scan marked the weakpoint. The weapon was already aimed. **{name}** eliminated nine Terminus threats before the rest of the battlefield had finished reacting.",
    "\"Cover me brothers — this one is mine.\" The Tactical says it with every weapon option available to them. **{name}** exercised every option nine times at Terminus level.",
    "**{name}** demonstrated nine times why the Tactical's versatility is as lethal against elite targets as any specialist doctrine.",
    "Auspex scan, weakpoint identified, weapon applied. **{name}** ran this sequence nine times against the Watch's highest-tier individual threats and produced nine confirmed kills.",
    "Nine Terminus kills. The Tactical's full arsenal. **{name}** has proven that all-round capability is not a compromise — it is a threat to everything, including Terminus-class enemies.",
]

TERMINUS_SLAYER_TACTICAL_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Tactical) is awarded to those versatile enough to find the correct approach to every class of Terminus threat — nine verified kills, three classes, one adaptable warrior.",
    "The Tactical class carries no single doctrinal identity. The Terminus Slayer mark recognises the Tactical who applied every identity to nine Terminus-level threats and produced nine kills.",
    "To scan the enemy, identify the vulnerability, and select the correct weapon for the moment — then to do it nine times at Terminus level — that is the Tactical's ultimate expression.",
    "Nine verified kills. Three Terminus types. The Tactical's answer was different every time and final every time. **{name}** has earned the Watch's recognition of that adaptability.",
    "The Terminus Slayer (Tactical) is the Watch's acknowledgement that versatility, in the hands of a warrior like **{name}**, is as deadly as any specialist's precision.",
]

TERMINUS_SLAYER_TACTICAL_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven's Tactical warriors are the Chapter's most complete soldiers. **{name}** has proven it against nine Terminus-level targets — complete, adaptable, final.",
    "Angels of Vengeance": "The Lion's versatility made lethal — **{name}** has adapted to nine Terminus threats and applied the correct approach every time.",
    "Black Templars": "The Crusade is won by warriors who can meet every enemy on every front. **{name}** met nine Terminus threats on nine fronts and prevailed on all of them.",
    "Bleeding Hearts": "Pack adaptability focused on the greatest hunt — **{name}** has applied the Tactical's full arsenal to nine Terminus threats and confirmed every kill.",
    "Blood Angels": "The sons of Sanguinius carry extraordinary adaptability alongside their fury. **{name}** adapted to nine Terminus threats and was lethal against every one.",
    "Blood Ravens": "Knowledge leads the Tactical's choice of weapon. **{name}** knew the correct approach to nine Terminus threats and confirmed nine kills.",
    "Brazen Minotaurs": "The bronze herd meets every threat with the correct tool. **{name}** has found the correct tool for nine Terminus threats and confirmed each result.",
    "Carcharodons": "The Void-born Tactical speaks in adaptation. **{name}** has adapted to nine Terminus threats and confirmed nine kills in the Watch's records.",
    "Carmine Blades": "Baal's versatility expressed through the Tactical's arsenal — **{name}** has applied the full range of it to nine Terminus threats with confirmed results.",
    "Celestial Lions": "Elysium's sons bring what the moment requires. **{name}** brought the correct approach to nine Terminus moments and confirmed every kill.",
    "Cowled Wardens": "The Warden's versatility is the Watch's asset. **{name}** has used every tool available to confirm nine Terminus kills in the Tactical class.",
    "Crimson Fists": "The Fist does not limit their kills to one method. **{name}** used every available tool to verify nine Terminus kills — a complete Tactical record.",
    "Dark Angels": "The First Legion's Tactical warriors are the Chapter's most complete soldiers. **{name}** proved it against nine Terminus-level targets — complete, adaptable, final.",
    "Dark Krakens": "The deep's most versatile hunters find the angle others cannot. **{name}** found the angle on nine Terminus threats in the Tactical class and confirmed every kill.",
    "Dragonspears": "Fleet warriors adapt to every engagement. **{name}** adapted the Tactical's full arsenal to nine Terminus threats and confirmed nine kills.",
    "Death Spectres": "Between life and the kill, the Tactical chooses the method. **{name}** has chosen correctly nine times at Terminus level.",
    "Epsilon Paladins": "For Honour, for Duty, for the auspex scan's precise answer — **{name}** has answered nine Terminus threats with the Tactical's full capability.",
    "Exorcists": "The thrice-tested warrior finds their fullest expression in the Tactical class. **{name}** has expressed it nine times against Terminus-level targets.",
    "Flesh Tearers": "The Red Thirst is most useful when the warrior can choose what to do with it. **{name}** has chosen correctly nine times at Terminus level in the Tactical class.",
    "Genesis Chapter": "Guilliman's purity finds its fullest form in the Tactical warrior. **{name}** has demonstrated that form nine times against Terminus-level threats.",
    "Hawk Lords": "Swift and adaptable — **{name}** has adapted the Tactical's full toolkit to nine Terminus threats and confirmed every kill in the Watch's records.",
    "Hospitallers": "The Hospitaller Tactical protects by being lethal at every range. **{name}** has been lethal at the correct range nine times at Terminus level.",
    "Imperial Fists": "The Fist does not limit their kills to one method. **{name}** used every available tool to verify nine Terminus kills — a complete Tactical record.",
    "Imperius Reavers": "The Eastern Fringe taught **{name}** that the correct approach changes with every engagement. Nine Terminus kills in the Tactical class confirm the lesson.",
    "Iron Hands": "The Iron Hands value efficiency above all. **{name}** found the efficient solution to nine Terminus threats without limiting themselves to a single method.",
    "Iron Hounds": "The Hound adapts to every prey. **{name}** has adapted to nine Terminus-class prey in the Tactical class and confirmed every kill.",
    "Iron Lords": "The Iron Lord's grip adapts to every tool. **{name}** has gripped the correct tool for nine Terminus threats and confirmed each kill.",
    "Iron Ravens": "The Iron Raven adapts their approach to every engagement. **{name}** has adapted nine times at Terminus level and confirmed nine kills.",
    "Knights of the Raven": "The Raven's patience extends to weapon choice. **{name}** chose the correct weapon for nine Terminus threats and confirmed every kill.",
    "Lamenters": "The cursed Chapter's Tactical warriors find their best expression in versatility. **{name}** has expressed that versatility nine times at Terminus level.",
    "Marines Errant": "The errant warrior carries every skill the Watch requires. **{name}** has deployed each of them against nine Terminus threats with confirmed results.",
    "Mentors": "The Mentor demonstrates versatility as doctrine. **{name}** has demonstrated it nine times at Terminus level — the auspex found the weakness, the weapon confirmed the kill.",
    "Minotaurs": "The bull's full strength is the sum of every capability. **{name}** has applied every Tactical capability to nine Terminus threats and confirmed nine kills.",
    "Necropolis Hawks": "Ruined ground rewards the most adaptable warrior. **{name}** has adapted the Tactical's full capability to nine Terminus threats in whatever ground presented itself.",
    "Raptors": "Adaptability is the Raptor's deepest weapon. **{name}** adapted to every Terminus class the Watch tracks and left nine kills in the record.",
    "Raven Guard": "Adaptability is the Raven Guard's deepest weapon. **{name}** adapted to every Terminus class the Watch tracks and left nine kills in the record.",
    "Red Scorpions": "Purist adaptability — every approach must be correct before it is deployed. **{name}** has deployed the correct approach nine times at Terminus level.",
    "Red Templars": "Fast and versatile — **{name}** has applied the Tactical's complete toolkit to nine Terminus threats with confirmed results.",
    "Salamanders": "The sons of Nocturne master their craft before applying it. **{name}** mastered the Tactical's full craft and applied it nine times at Terminus level.",
    "Scythes of the Emperor": "Sotha's sons bring what the moment requires. **{name}** has brought the correct approach to nine Terminus moments and confirmed every kill.",
    "Sons of Medusa": "Calculated adaptability — every Terminus threat received the approach it could not counter. **{name}** has confirmed nine kills as the result.",
    "Space Wolves": "The wolf adapts to every terrain. **{name}** adapted to three Terminus types and eliminated nine of them across the Watch's most demanding operational record.",
    "Storm Giants": "Tower-tall warriors meet every threat at the correct range. **{name}** has met nine Terminus threats at the correct range and confirmed nine kills.",
    "Tempestuous Angels": "Drossmire's fire applied with the Tactical's precision — **{name}** has applied the correct method to nine Terminus threats and confirmed every result.",
    "The Drakes": "Dragon-fire in every form the Tactical allows — **{name}** has applied all of them to nine Terminus threats with nine confirmed kills.",
    "Tome Keepers": "The Chapter records versatile engagements. **{name}**'s nine Terminus kills in the Tactical class — varied, precise, always confirmed — are already in the book.",
    "Ultramarines": "The sons of Macragge are the Codex's versatility made flesh. **{name}** applied every lesson the Codex offers to nine Terminus threats and confirmed nine kills.",
    "White Scars": "Speed and versatility are the same quality at high enough calibre. **{name}** demonstrated both against nine Terminus-level threats in the Tactical class.",
    "Wolfspear": "The Dark Terror's hunters adapt to every prey. **{name}** has adapted the Tactical's full capability to nine Terminus threats with confirmed results.",
    "Black Shield": "No chapter doctrine. No doctrinal limitation. Only nine Terminus kills recorded under the Tactical class. **{name}**'s record speaks for the warrior.",
}

TERMINUS_SLAYER_TACTICAL_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's authority extends to every approach the Watch teaches. The Tactical class is the doctrine of that authority made personal — no preferred weapon, no preferred range, only the response that the auspex confirms is correct.",
    "High Chaplain": "The High Chaplain carries every weapon the faith demands. The auspex scan identifies which one the moment requires; the High Chaplain carries no preference that would slow the answer.",
    "Chief Apothecary": "The Chief Apothecary who masters the Tactical class has extended the same all-round competence that defines the apothecary's art into the full range of combat capabilities the Watch recognises.",
    "Void Warden": "The Void Warden watches for threats in every direction and answers them with whatever the auspex prescribes. The Tactical class is the vigil expressed as comprehensive capability.",
    "Forgemaster": "The Forgemaster's mastery of the Watch's weapons extends to the Tactical class's full range of approaches. The auspex identifies the weakness; the Forgemaster carries whatever addresses it.",
    "Castellan": "The Castellan's fortress is most effective when it adapts. The Tactical class is adaptation made doctrine — the auspex scan as the first act, the correct response as the second, no preference limiting either.",
    "Lord Executioner": "The Lord Executioner's mandate specifies no preferred method. The Tactical class is the fullest expression of that mandate — the auspex identifies the weakpoint; the weapon is selected accordingly.",
    "Venerable Dreadnought": "The ancient has applied every approach the Watch teaches across more engagements than most warriors can count. The Tactical class, in the Venerable Dreadnought's hands, is accumulated mastery applied without limitation.",
    "Honored Dreadnought": "The Honored Dreadnought who masters the Tactical class has outlasted the limitations most warriors impose on themselves. Every approach is available; the auspex determines which one the engagement requires.",
    "Interred Brother": "The Interred Brother in the Tactical class brings preserved experience to bear across the full range of approaches the Watch recognises — the auspex scan, the weakpoint, and whatever weapon the moment demands.",
    "Watch Chaplain": "The Chaplain behind the auspex carries every weapon the faith allows and applies whichever one the scan confirms as necessary. No approach is beneath the creed; every weakpoint is within its scope.",
    "Watch Apothecary": "The Apothecary in the Tactical class is all-round care extended to combat — the auspex identifies the threat's vulnerability, and the narthecium is still in one hand while the correct weapon fills the other.",
    "Watch Librarian": "The Librarian behind the auspex scan has the fullest picture of what the engagement requires. The Tactical class is the mind's most complete expression — every approach kept ready, every weakpoint addressed by the optimal response.",
    "Watch Techmarine": "The Techmarine who masters the Tactical class has verified that every approach the Watch teaches is mechanically sound. The auspex confirms which one the engagement requires; the preparation ensures it is always available.",
    "Watch Keeper": "The Keeper's vigil in the Tactical class encompasses every approach the Watch teaches. The auspex extends the watch; the correct weapon extends the resolution.",
    "Company Champion": "The Company Champion who masters the Tactical class is the Chapter's most complete expression — every weapon applied, every weakpoint addressed, no approach refused.",
    "Kill Team Champion": "The Kill Team Champion in the Tactical class is the team's most complete asset — the auspex identifies what the team cannot close on alone, and the Champion selects from every approach available to address it.",
    "Watch Captain": "A Watch Captain who masters the Tactical class leads from the widest possible understanding — every approach available, every weakpoint identified, every weapon in the arsenal considered before the one most suited is selected.",
    "Watch Lieutenant": "The Lieutenant who masters the Tactical class has extended their effectiveness across every range and approach the Watch teaches. The auspex scan is the officer's widest command — it precedes every other decision.",
    "Watch Sergeant": "The Sergeant in the Tactical class gives the squad the most adaptable anchor available — the auspex reading the engagement, the NCO selecting the correct response from every approach the Watch has taught.",
    "Oathsworn": "The oath behind the Tactical class has no preferred direction — it applies to every approach, every weakpoint, every weapon the Watch carries. The auspex confirms which one the moment requires; the oath ensures the warrior is ready for all of them.",
    "Watch Veteran": "The Veteran in the Tactical class has made every approach available and every option reliable. The auspex scan runs faster because the warrior has seen every result it can recommend.",
    "Watch Brother": "The Watch Brother who masters the Tactical class has already demonstrated what takes most warriors years to learn — that the Tactical's full capability, properly applied, arrives at the correct answer faster than any single preference would.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Techmarine) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_TECHMARINE_OPENINGS: List[str] = [
    "The Tarantula Sentry Guns locked target. The servo gun calculated trajectory. **{name}** made nine Terminus threats understand that the battlefield itself was the weapon.",
    "\"Cover me brothers — this one is mine.\" **{name}** deployed the battlefield, activated the machinery, and watched nine Terminus-level threats fall into the kill zone.",
    "**{name}** converted the engagement zone into a mechanism for killing Terminus threats — nine times, verified, logged in the Omnissiah's own accounting.",
    "The Techmarine does not merely fight — they engineer the conditions for victory. **{name}** engineered nine Terminus kills and submits the record to the Omnissiah's attention.",
    "Nine Terminus kills. Servo gun. Tarantula Sentry. **{name}** proved that zone control at Terminus level is not passive. It is precision destruction.",
]

TERMINUS_SLAYER_TECHMARINE_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Techmarine) is awarded to those who applied the battlefield's machinery to the Watch's most dangerous individual targets — nine verified kills across all three Terminus classes.",
    "The Techmarine class commands the zone. The Terminus Slayer mark recognises the Techmarine who made that zone lethal for nine Terminus-level threats specifically.",
    "To position the Tarantula, to level the servo gun, to make the battlefield itself an engine of elimination — that is the Techmarine's 'this one is mine.' **{name}** has said it nine times.",
    "Nine verified kills. Each one a product of preparation, positioning, and machine-spirit cooperation. **{name}** has earned the Terminus Slayer (Techmarine) and the Omnissiah's recognition.",
    "The Terminus Slayer (Techmarine) honours the warrior who treated every Terminus threat as an engineering problem — and solved it nine times, to the Omnissiah's eternal satisfaction.",
]

TERMINUS_SLAYER_TECHMARINE_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven tend ancient mechanisms with ancient devotion. **{name}** turned that devotion into nine Terminus kills through zone control and machine-spirit cooperation.",
    "Angels of Vengeance": "The Lion's Techmarines serve the Chapter's secrets and the Chapter's kill record. **{name}**'s nine Terminus kills in the Techmarine class serve both.",
    "Black Templars": "The Templar's crusade finds unlikely Techmarines. **{name}** carried the Omnissiah's tools into nine Terminus engagements — the Crusade is stronger for the results.",
    "Bleeding Hearts": "Pack territory dominated by machine-spirit cooperation — **{name}** has confirmed nine Terminus kills through zone control in the Watch's records.",
    "Blood Angels": "The sons of Sanguinius carry artistry into everything. **{name}**'s nine Terminus kills are the art of zone control applied at the Watch's most demanding target class.",
    "Blood Ravens": "Knowledge of the machine-spirit's potential, then the deployment, then the kill. **{name}** has applied this sequence nine times at Terminus level.",
    "Brazen Minotaurs": "Bronze machinery and bronze patience — **{name}** has combined both into nine Terminus kills through zone control with the Omnissiah's approval.",
    "Carcharodons": "The Void-born Techmarine commands the zone in silence. **{name}**'s nine Terminus kills were processed by the battlefield before the threats even identified the danger.",
    "Carmine Blades": "The machine-spirits serve where pure fury cannot reach. **{name}** has commanded those spirits against nine Terminus threats and confirmed every kill.",
    "Celestial Lions": "Elysium's Techmarines bring the Omnissiah's tools to the fiercest fronts. **{name}** brought them to nine Terminus fronts and confirmed nine kills.",
    "Cowled Wardens": "The Warden's zone is protected by more than the cowl. **{name}** has extended machine-spirit protection into nine Terminus engagements with confirmed results.",
    "Crimson Fists": "The Fists' Techmarines keep the Chapter's remaining machines alive. **{name}** has also kept nine Terminus threats in the kill column through zone control.",
    "Dark Angels": "The First Legion's Techmarines tend ancient machines with ancient care. **{name}** turned that care into nine Terminus kills and a record the Omnissiah approves.",
    "Dark Krakens": "The deep's machinery is the pack's greatest advantage. **{name}** has deployed that advantage nine times at Terminus level with confirmed kills.",
    "Dragonspears": "Fleet Techmarines keep the void-engines alive and the enemies dead. **{name}** has kept nine Terminus threats in the dead column through zone control.",
    "Death Spectres": "Between the machine-spirit and the target there is only the kill zone. **{name}** has built nine Terminus kill zones and confirmed nine results.",
    "Epsilon Paladins": "For Honour, for Duty, for the Omnissiah's perfect mechanism — **{name}** has built that mechanism nine times at Terminus level with confirmed kills.",
    "Exorcists": "The Exorcist's Techmarine purifies the zone before the enemy enters it. **{name}** has purified nine Terminus engagements with verified kills as the result.",
    "Flesh Tearers": "Seth's Techmarines keep the Chapter's fury mechanically directed. **{name}** has directed it through zone control at nine Terminus threats with confirmed results.",
    "Genesis Chapter": "Guilliman's Techmarines apply doctrine to deployment. **{name}** has applied zone control doctrine to nine Terminus threats and confirmed nine kills.",
    "Hawk Lords": "Swift deployment of the Tarantula, swift activation of the servo gun — **{name}** has confirmed nine Terminus kills through the Hawk Lords' efficient methodology.",
    "Hospitallers": "The Hospitaller Techmarine heals the zone by clearing the threat. **{name}** has cleared nine Terminus threats through zone control with confirmed results.",
    "Imperial Fists": "The Fists built Rogal Dorn's fortresses. **{name}** built a kill zone for nine Terminus threats and did not leave any of them standing.",
    "Imperius Reavers": "The Eastern Fringe taught **{name}** that the correct mechanical deployment transforms any engagement. Nine Terminus kills confirm the lesson.",
    "Iron Hands": "The Iron Hands are the Omnissiah's truest servants. **{name}** served the Omnissiah nine times at Terminus level — the machine-spirits have recorded every kill.",
    "Iron Hounds": "The Hound's zone is the pack's domain. **{name}** has dominated nine Terminus engagements through zone control and confirmed nine kills.",
    "Iron Lords": "The Iron Lord's zone is the Iron Grip extended through machine-spirit cooperation. **{name}** has extended it nine times at Terminus level.",
    "Iron Ravens": "The Iron Raven's zone control is shadow made mechanical. **{name}** has used that mechanical shadow to confirm nine Terminus kills.",
    "Knights of the Raven": "Patient deployment, then the kill zone's answer — **{name}** has answered nine Terminus threats through zone control with the Omnissiah's full blessing.",
    "Lamenters": "The cursed Chapter's Techmarines keep what little remains operational. **{name}** has kept nine Terminus threats in the confirmed-kill column through zone control.",
    "Marines Errant": "The errant Techmarine deploys wherever the Omnissiah directs. **{name}** has deployed nine times at Terminus level and confirmed nine kills.",
    "Mentors": "The Mentor teaches zone control doctrine by demonstrating it. **{name}** has demonstrated it nine times at Terminus level — Tarantula deployed, kills confirmed.",
    "Minotaurs": "The bull's zone is impassable. **{name}** has made it impassable nine times for Terminus threats with confirmed kills as the result.",
    "Necropolis Hawks": "Ruined ground is ideal for Tarantula deployment. **{name}** has deployed into nine Terminus engagements and confirmed nine kills from that position.",
    "Raptors": "The Raven Guard trust in their own preparation. **{name}** prepared the battlefield for nine Terminus threats and the preparation was sufficient every time.",
    "Raven Guard": "The Raven Guard trust in their own preparation. **{name}** prepared the battlefield for nine Terminus threats and the preparation was sufficient every time.",
    "Red Scorpions": "Purist zone control applied at Terminus level — **{name}** has deployed with textbook precision nine times and confirmed nine kills.",
    "Red Templars": "Fast deployment, precise zone control — **{name}** has confirmed nine Terminus kills through the Techmarine's methodology in the Watch's records.",
    "Salamanders": "The sons of Nocturne pour mastery into their craft. **{name}** poured mastery into machinery, zone control, and nine Terminus kills — a record Vulkan would note.",
    "Scythes of the Emperor": "Sotha's Techmarines keep the Chapter's machines running and the enemy kill count rising. **{name}** has raised it nine times at Terminus level.",
    "Sons of Medusa": "The Sons serve the Omnissiah's machines with precision. **{name}** has served those machines into nine Terminus kills through zone control.",
    "Space Wolves": "The Wolves do not always trust the machine — but **{name}** made the machine trustworthy, nine Terminus times, and the pack is stronger for it.",
    "Storm Giants": "Tower-tall and machine-backed — **{name}** has confirmed nine Terminus kills from behind the Techmarine's zone control in the Watch's records.",
    "Tempestuous Angels": "Drossmire's fire channelled through machine-spirit cooperation — **{name}** has directed that fire at nine Terminus threats through zone control with confirmed results.",
    "The Drakes": "The Drake's machinery burns as hot as their fire. **{name}** has used that machine-heat to confirm nine Terminus kills through zone control.",
    "Tome Keepers": "The Chapter records mechanical mastery. **{name}**'s nine Terminus kills through zone control are already in the Tome — the machine-spirits filed their approval.",
    "Ultramarines": "The sons of Macragge apply discipline to machinery as freely as to tactics. **{name}** applied both to nine Terminus threats and produced nine kills.",
    "White Scars": "The White Scars do not favour the static position. **{name}** found a way to bring zone control to a mobile campaign — nine Terminus kills confirms the method works.",
    "Wolfspear": "The Dark Terror's Techmarines keep the hunt mechanically supported. **{name}** has supported nine Terminus hunts through zone control with confirmed kills.",
    "Black Shield": "No forge-world claims the credits. The kills are logged, the machine-spirits satisfied, and **{name}**'s nine Terminus kills stand without further annotation required.",
}

TERMINUS_SLAYER_TECHMARINE_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master who engineers the kill zone has elevated command to its most deliberate form — the battlefield itself made into the weapon, the machine-spirits serving as execution.",
    "High Chaplain": "The High Chaplain who deploys the Tarantula Sentry Guns has found the Omnissiah's most practical form of faith — the machine-spirits prepared, the zone locked, the litany backed by automated fire.",
    "Chief Apothecary": "The Chief Apothecary who masters zone control has found the broadest form of care — the kill zone prevents more wounds than the narthecium could ever address, built before the engagement begins.",
    "Void Warden": "The Void Warden's watch becomes structural when the Tarantula Sentry Guns are deployed. The machine-spirits watch where the warrior cannot; the zone controls what the vigil alone could not.",
    "Forgemaster": "The Forgemaster who builds the kill zone is working in the most familiar medium — the battlefield engineered with the same craft applied to the armoury, the machine-spirits directed with the same authority.",
    "Castellan": "The Castellan deploys the Tarantula Sentry Guns as forward battlements — the zone control a portable fortress, the machine-spirits the garrison that never tires.",
    "Lord Executioner": "The Lord Executioner who makes the Tarantula Sentry Gun the instrument of the warrant has found the most mechanical form of execution — the zone built, the machine-spirit aimed, the result independent of the blade.",
    "Venerable Dreadnought": "The ancient who deploys the kill zone has refined the patience of centuries into a single deliberate act — the Tarantula Sentry Guns placed where only experience would know to put them.",
    "Honored Dreadnought": "The Honored Dreadnought who masters zone control has built endurance into the battlefield itself. The machine-spirits sustain the position; the Honored Dreadnought sustains the machine-spirits.",
    "Interred Brother": "The Interred Brother who engineers the kill zone has converted preserved knowledge into terrain — the Tarantula Sentry Guns placed where only accumulated experience identifies the correct angle.",
    "Watch Chaplain": "The Chaplain who deploys the Tarantula Sentry Guns has engineered the creed into the battlefield. The machine-spirits are as dutiful as any brother; the zone they control is the Chaplain's most permanent sermon.",
    "Watch Apothecary": "The Apothecary who masters zone control has constructed the broadest form of care — the Tarantula Sentry Guns addressing the threat before it reaches the brothers, the preparation serving in place of the remedy.",
    "Watch Librarian": "The Librarian who deploys the kill zone has applied calculation to the battlefield rather than the individual. The Tarantula Sentry Guns execute the analysis; the zone is the Librarian's most spatial conclusion.",
    "Watch Techmarine": "The Watch Techmarine who engineers the kill zone has given the Omnissiah's tools their fullest expression — the machine-spirits deployed, the zone locked, the battlefield itself made into the mechanism of the Watch's intent.",
    "Watch Keeper": "The Keeper's vigil extends through the Tarantula Sentry Guns' sensors. The kill zone is the watch made permanent — the machine-spirits maintaining where the warrior cannot stand alone.",
    "Company Champion": "The Company Champion who deploys the kill zone has made the battlefield the Chapter's champion — the Tarantula Sentry Guns fighting where the blade cannot reach, the zone protecting what the blade stands before.",
    "Kill Team Champion": "The Kill Team Champion who engineers the kill zone gives the team the most deliberate support available — the zone built before the engagement, the machine-spirits active so the team can operate inside the preparation.",
    "Watch Captain": "A Watch Captain who engineers the kill zone commands before the engagement begins — the Tarantula Sentry Guns placed, the zone established, the engagement shaped by the preparation rather than the reaction.",
    "Watch Lieutenant": "The Lieutenant who masters zone control has found the widest form of leadership in this class — the Tarantula Sentry Guns extending the officer's reach across the full depth of the engagement.",
    "Watch Sergeant": "The Sergeant who deploys the kill zone has given the squad the most mechanically sophisticated form of support available — the Tarantula Sentry Guns watching the approach, the zone controlling what the squad cannot.",
    "Oathsworn": "The oath behind the Techmarine's zone control is kept before the first enemy arrives — the machine-spirits deployed, the Tarantula Sentry Guns aimed, the vow fulfilled in the engineering of the conditions for victory.",
    "Watch Veteran": "The Veteran who masters zone control has learned to engineer the engagement rather than simply enter it. The Tarantula Sentry Guns are placed where experience says they will matter most.",
    "Watch Brother": "The Watch Brother who masters the Techmarine class has already learned the discipline the class demands above all others — the kill zone must be built before it is needed, and the machine-spirits must be prepared before they are called on.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Vanguard) award announcement flavor text
# ---------------------------------------------------------------------------
TERMINUS_SLAYER_VANGUARD_OPENINGS: List[str] = [
    "The grapnel launched. The distance closed in an instant. **{name}** was already inside the Terminus threat's reach before it could respond — nine times.",
    "\"Cover me brothers — this one is mine.\" From a warrior who could cross any battlefield gap in moments, **{name}** chose to close nine Terminus threats personally.",
    "**{name}** used the grapnel launcher not to escape but to arrive — at nine Terminus-level threats, at close quarters, at the moment of maximum danger.",
    "High mobility. Close-quarters weapons. Nine Terminus kills. **{name}** has built a Terminus Slayer record that demonstrates exactly what the Vanguard was designed to achieve.",
    "Nine times the grapnel found the angle. Nine times **{name}** arrived where a Terminus threat least expected a warrior. Nine times the encounter was one-sided.",
]

TERMINUS_SLAYER_VANGUARD_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer (Vanguard) is awarded to those who brought close-quarters mobility to bear on every class of Terminus threat — nine verified kills, each faster than the enemy expected.",
    "The Vanguard class is built on mobility and aggression. The Terminus Slayer mark recognises the Vanguard who applied both to nine Terminus-level threats and returned nine kills.",
    "To grapnel into engagement range, to close where others would hold back, and to end the Terminus threat at close quarters — that is the Vanguard's 'this one is mine.' **{name}** has done it nine times.",
    "Nine verified kills across all three Terminus types. The grapnel found a line to every one of them. **{name}** has earned the Terminus Slayer (Vanguard) on the simplest terms: high speed, close weapons, confirmed results.",
    "The Terminus Slayer (Vanguard) honours the warrior who treated every Terminus threat as a destination — arrived fast, engaged close, and verified nine kills as the outcome.",
]

TERMINUS_SLAYER_VANGUARD_CHAPTER_LINES: Dict[str, str] = {
    "Ultramarines": "The sons of Macragge value decisive action. **{name}** was decisive nine times at Terminus level — the grapnel launched, the threat engaged, the kill confirmed.",
    "Blood Angels": "The sons of Sanguinius are drawn to the close engagement. **{name}** drew nine Terminus threats into the close engagement and settled all nine accounts.",
    "Dark Angels": "The First Legion's Vanguard warriors are the blade's point. **{name}** pointed that blade at nine Terminus threats and drove it home every time.",
    "Space Wolves": "The wolf closes on its prey. **{name}** closed on nine Terminus-level prey faster than any of them could predict — the Vanguard's lesson delivered nine times.",
    "Imperial Fists": "The Fist does not always hold ground — sometimes the Fist advances on the most dangerous point of the field. **{name}** advanced on nine Terminus threats and found success every time.",
    "Salamanders": "The sons of Nocturne strike with purpose. **{name}** found nine Terminus threats and struck each one with the close-quarters purpose the Vanguard is built for.",
    "Raven Guard": "This is the Raven Guard's Vanguard in their truest form — **{name}** arrived from an unexpected angle, at maximum speed, nine times against Terminus-level targets.",
    "Iron Hands": "The grapnel is a mechanism. The close-quarter weapon is a mechanism. **{name}** operated both with Iron Hands efficiency against nine Terminus threats.",
    "White Scars": "Speed above all — and the Vanguard's grapnel makes speed absolute. **{name}** applied White Scars principle to nine Terminus kills at the closest possible range.",
    "Black Templars": "The eternal charge is not always on foot. **{name}** launched the grapnel and charged nine Terminus threats in the Templar's eternal spirit.",
    "Black Shield": "No chapter. No lineage. Only the grapnel's trajectory and nine Terminus kills logged under the Vanguard class. **{name}**'s record is the complete statement.",
}

TERMINUS_SLAYER_VANGUARD_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's authority does not require a gradual approach. The grapnel closes the distance; everything else follows from the moment of arrival.",
    "High Chaplain": "The High Chaplain who masters the Vanguard class delivers the creed at the speed the grapnel allows — the litany arriving with the warrior, at the range where the warrior is already inside the response.",
    "Chief Apothecary": "The Chief Apothecary who closes by grapnel has decided the fastest form of intervention is the one that arrives before the wound is possible. The grapnel is the narthecium's preemptive argument.",
    "Void Warden": "The Void Warden's vigil becomes something else when the grapnel fires. The watch ends the moment the line reaches the target — the distance collapsed, the engagement decided at close quarters.",
    "Forgemaster": "The Forgemaster who masters the Vanguard class has verified that the grapnel's mechanism functions precisely as designed. The close-quarters weapon at the end of the line functions the same way.",
    "Castellan": "The Castellan does not always hold ground — sometimes the fortress launches the grapnel and arrives at the threat's position before the threat can choose the terms of the engagement.",
    "Lord Executioner": "The Lord Executioner who closes by grapnel has delivered the warrant at the speed it deserves — the target reached before it could prepare, the outcome settled at the range the grapnel selected.",
    "Venerable Dreadnought": "The ancient who masters the Vanguard class has found a way to close distance that centuries of experience confirm is correct. The grapnel is the oldest argument for arriving first, made modern.",
    "Honored Dreadnought": "The Honored Dreadnought who closes by grapnel has refused to accept that preservation limits the approach. The line launches, the distance collapses, and the close-quarters weapon does what it was made for.",
    "Interred Brother": "The Interred Brother who fires the grapnel is the Watch's most unexpected arrival — the preserved warrior who collapses the distance at Vanguard speed and engages at close quarters before anyone has accounted for the trajectory.",
    "Watch Chaplain": "The Chaplain who closes by grapnel delivers the creed at the range where the target cannot fail to receive it. The grapnel commits; the close-quarters weapon concludes.",
    "Watch Apothecary": "The Apothecary who closes by grapnel has chosen the most direct form of prevention — arriving at the threat's position at Vanguard speed ensures the threat cannot reach the brothers it was aimed at.",
    "Watch Librarian": "The Librarian who masters the Vanguard class has calculated that the optimal engagement distance is sometimes the shortest one. The grapnel commits to that calculation; the close-quarters weapon resolves it.",
    "Watch Techmarine": "The Techmarine who fires the grapnel has verified the mechanism, assessed the anchor point, and committed to the trajectory. The close-quarters engagement at the end of the line is the calculation concluded.",
    "Watch Keeper": "The Keeper's vigil ends when the grapnel fires. The distance maintained through the approach collapses the moment the line reaches its anchor — the vigil becomes an arrival.",
    "Company Champion": "The Company Champion who closes by grapnel is the Chapter's finest argument delivered at speed — the blade arriving at the threat's position before the threat has finished deciding how to meet it.",
    "Kill Team Champion": "The Kill Team Champion who masters the Vanguard class closes on what the team cannot. The grapnel launches before the formation reaches the target; the engagement at close quarters removes the threat before it can reverse the distance.",
    "Watch Captain": "A Watch Captain who closes by grapnel commands from the front in the most direct sense — arriving at the threat's position first, engaging at close quarters, and leaving no ambiguity about who led the approach.",
    "Watch Lieutenant": "The Lieutenant who masters the Vanguard class has found that the fastest contribution to the engagement is sometimes the most useful one. The grapnel closes the distance; the close-quarters weapon closes the account.",
    "Watch Sergeant": "The Sergeant who launches the grapnel has decided that the threat is the squad's first problem and the squad's least patient NCO is the first response. The close-quarters weapon confirms the Sergeant's intent on arrival.",
    "Oathsworn": "The oath behind the grapnel's line is kinetic — the vow to close on what others cannot reach is fulfilled the moment the anchor holds and the distance is no longer what it was.",
    "Watch Veteran": "The Veteran who masters the Vanguard class has learned when to fire the grapnel — the calculation is instant now, the commitment reflexive, the close-quarters engagement at the end of the line already decided before the trigger is pulled.",
    "Watch Brother": "The Watch Brother who masters the Vanguard class has answered the question that every warrior considers at some point — when the grapnel is the right tool, and how to commit to the close-quarters engagement at the end of the line without hesitation.",
}

# ---------------------------------------------------------------------------
# Master Terminus Slayer award announcement flavor text
# ---------------------------------------------------------------------------
MASTER_TERMINUS_SLAYER_OPENINGS: List[str] = [
    "Every class. Every Terminus type. Every kill verified. **{name}** has completed the most comprehensive personal Terminus Slayer record the Watch logs.",
    "Seven classes. Sixty-three verified kills. Three Terminus types faced in every configuration the Watch tracks. **{name}** has earned the title that fewer than anyone expected would ever be claimed.",
    "\"Cover me brothers — this one is mine.\" **{name}** said it as an Assault warrior. As a Bulwark. As a Heavy. As a Sniper. As a Tactical. As a Techmarine. As a Vanguard. All nine types, all seven times, all verified.",
    "There is a word for what **{name}** has done — the Watch's kill logs contain seven class completions, all Terminus types, all verification records intact. The word is Master.",
    "The Master Terminus Slayer is not given to warriors who merely fought well. It is given to **{name}**, who fought well in every class, against every Terminus threat, and produced the verified record for all of it.",
]

MASTER_TERMINUS_SLAYER_PROCLAMATIONS: List[str] = [
    "The Master Terminus Slayer is the Watch's highest individual combat honour — awarded only to those whose verified kill records span every class, every Terminus type, every required kill.",
    "To complete the Terminus Slayer challenge in one class is remarkable. To complete it in all six is something the Watch's records have rarely if ever required an entry for. **{name}** has required one.",
    "Six class completions. Every Terminus type met in every configuration. The Master Terminus Slayer title is not a rank — it is a comprehensive record that speaks every language of combat the Watch teaches.",
    "The Watch teaches versatility by principle. The Master Terminus Slayer proves versatility in practice — every class deployed, every Terminus threat eliminated, every kill logged and verified.",
    "The Master Terminus Slayer is the Watch's recognition that **{name}** has not found a preferred approach to Terminus-level threats. They have mastered every approach.",
    "Where most warriors find their class and their method, **{name}** found the answer to a question the Watch rarely asks: what does a warrior look like who has done it all? This. This is what it looks like.",
]

MASTER_TERMINUS_SLAYER_CHAPTER_LINES: Dict[str, str] = {
    "Angels of Defiance": "The Unforgiven's secrets run deep. **{name}**'s Master Terminus Slayer record runs deeper — six classes, every Terminus type, all verified.",
    "Angels of Vengeance": "The Lion's wrath was never meant to be contained in one class. **{name}** has expressed it in all six, against every Terminus type the Watch tracks.",
    "Black Templars": "The Crusade never ends. **{name}** has fought it in every class the Watch recognises, against every class of Terminus enemy, and produced the verification record for all of it.",
    "Bleeding Hearts": "The hunt in every form the Watch teaches — **{name}** has hunted in all six classes, confirmed every Terminus type, and the hunt record stands as the fullest in the Watch's logs.",
    "Blood Angels": "The sons of Sanguinius carry extraordinary gifts into battle. **{name}** has carried them across every class, every Terminus type, and produced a record that no single gift can explain.",
    "Blood Ravens": "All knowledge applied to all classes — **{name}**'s Master Terminus Slayer record is the Blood Raven's highest expression: six disciplines mastered, every Terminus type confirmed.",
    "Brazen Minotaurs": "The bronze herd meets every threat in every configuration. **{name}** has met every Terminus type in every class — the bull's most comprehensive record in the Watch.",
    "Carcharodons": "The Void-born speak through kill records. **{name}**'s Master Terminus Slayer record speaks in six languages of combat — every class, every Terminus type, all confirmed.",
    "Carmine Blades": "The curse of Baal does not limit the warrior who has mastered all six classes. **{name}**'s Master Terminus Slayer record is the Carmine Blade's highest combat achievement.",
    "Celestial Lions": "Elysium's sons stand in every formation. **{name}** has stood in all six Terminus Slayer formations — every class completed, every Terminus type confirmed.",
    "Cowled Wardens": "The Warden watches in all directions. **{name}** has engaged in all six directions the Terminus Slayer challenge permits — every class, every type, all confirmed.",
    "Crimson Fists": "Few in number, mastered in all classes — **{name}**'s Master Terminus Slayer record is the Crimson Fist's answer to the challenge of comprehensive combat mastery.",
    "Dark Angels": "The First Legion's deepest secrets are kept within. **{name}**'s Master Terminus Slayer record is not a secret — but the discipline required to build it is entirely consistent with the First Legion's deepest values.",
    "Dark Krakens": "The deep's greatest predator hunts in all conditions. **{name}** has hunted in all six classes, confirmed every Terminus type, and the record stands as the Watch's most comprehensive.",
    "Dragonspears": "Fleet warriors find their highest expression in mastery of all classes. **{name}** has found it — six completions, every Terminus type, all confirmed in the Watch's records.",
    "Death Spectres": "Between life and the Master Terminus Slayer title there are fifty-four verified kills across six classes. **{name}** has crossed that distance in its entirety.",
    "Epsilon Paladins": "For Honour, for Duty, for all six classes of Terminus Slayer completion — **{name}** has answered every form of that call and earned the Watch's highest individual combat honour.",
    "Exorcists": "The thrice-tested warrior finds their fullest expression in the Master Terminus Slayer. **{name}** has been tested in every class — and confirmed every Terminus type.",
    "Flesh Tearers": "Seth's sons find their fullest catharsis in the Master Terminus Slayer. **{name}** has expressed that catharsis across all six classes with every Terminus type confirmed.",
    "Genesis Chapter": "Guilliman's purity finds its highest combat application in the warrior who has mastered all six classes. **{name}** is that warrior — every Terminus type, all confirmed.",
    "Hawk Lords": "The Hawk Lord sees from every angle. **{name}** has engaged from every angle the Terminus Slayer challenge permits — six classes, every type, all confirmed.",
    "Hospitallers": "The Hospitaller who has mastered all six classes has nothing left to prove and everything left to demonstrate. **{name}** has demonstrated all of it — Master Terminus Slayer.",
    "Imperial Fists": "Rogal Dorn built warriors for every contingency. **{name}** proved worthy of every contingency — all six combat disciplines, all Terminus types, all verified.",
    "Imperius Reavers": "The Eastern Fringe produced a warrior who would master all six Terminus Slayer classes. **{name}** is that warrior — every type confirmed, every class complete.",
    "Iron Hands": "The Iron Hands believe the body should be made as capable as possible. **{name}** has made themselves as capable as the Watch's kill log system can measure — all six classes, all Terminus types, complete.",
    "Iron Hounds": "The Hound that hunts in all six formations is the pack's most complete asset. **{name}** has proven their completeness — Master Terminus Slayer, all types confirmed.",
    "Iron Lords": "The Iron Grip in all six classes — **{name}** has gripped every Terminus type in every Terminus Slayer class and confirmed the full record.",
    "Iron Ravens": "The Iron Raven in all six shadows — **{name}** has operated in every Terminus Slayer class, confirmed every Terminus type, and earned the Watch's highest individual combat distinction.",
    "Knights of the Raven": "The Raven's patience rewarded in all six classes — **{name}** has confirmed every Terminus type in every class the Watch tracks. The patience and the record are both complete.",
    "Lamenters": "The cursed Chapter has found its fullest, least cursed expression in **{name}**'s Master Terminus Slayer record — six classes, every Terminus type, all verified.",
    "Marines Errant": "The errant warrior has found every class. **{name}**'s Master Terminus Slayer record is the most complete itinerary in the Watch — six classes, all Terminus types, all confirmed.",
    "Mentors": "The Mentor's most complete lesson — **{name}** has demonstrated all six classes of Terminus Slayer completion with every type confirmed. The example has been set in full.",
    "Minotaurs": "The bull that meets every challenge in every form — **{name}**'s Master Terminus Slayer record is the Minotaur's fullest answer to the Watch's hardest question.",
    "Necropolis Hawks": "Ruined cities do not limit the warrior who has mastered all six Terminus Slayer classes. **{name}** has mastered all of them — every type confirmed, every class complete.",
    "Raptors": "The Raptor who operates in all six classes is the Watch's most complete Terminus hunter. **{name}** is that Raptor — every type confirmed, Master Terminus Slayer earned.",
    "Raven Guard": "The Raven Guard do not need acknowledgement. The kill logs acknowledge everything that needs acknowledging. **{name}**'s six class, all-Terminus-type record is the acknowledgement.",
    "Red Scorpions": "Purist standards applied to all six classes — **{name}** has met the Red Scorpion standard in every Terminus Slayer form the Watch recognises.",
    "Red Templars": "Fast and complete — **{name}**'s Master Terminus Slayer record is the Red Templar's highest individual achievement in the Watch's combat logs.",
    "Salamanders": "The sons of Nocturne master their craft above all else. **{name}** has mastered six crafts and applied each one to Terminus threats. Vulkan would find this entirely appropriate.",
    "Scythes of the Emperor": "Sotha's sons honour their lost Chapter by achieving what it set out to achieve. **{name}**'s Master Terminus Slayer record honours that legacy in every class and every Terminus type.",
    "Sons of Medusa": "The Sons calculate mastery precisely. **{name}**'s Master Terminus Slayer record is the most precise calculation in the Watch — six classes completed, every Terminus type confirmed.",
    "Space Wolves": "The Great Hunt is answered in full by **{name}** — every class ridden, every Terminus prey brought down, all fifty-four kills verified in the Kill-Leader's own records.",
    "Storm Giants": "Tower-tall in all six classes — **{name}**'s Master Terminus Slayer record is the Storm Giants' highest individual combat achievement in the Watch's logs.",
    "Tempestuous Angels": "Drossmire's fire expressed in all six Terminus Slayer classes — **{name}** has confirmed every Terminus type in every class and earned the highest individual combat honour the Watch confers.",
    "The Drakes": "Dragon-fire in all six forms the Terminus Slayer challenge requires — **{name}** has provided it, confirmed it, and earned the Master Terminus Slayer title in full.",
    "Tome Keepers": "The Tome Keepers record great deeds. **{name}**'s Master Terminus Slayer record occupies an entire chapter of the Watch's kill logs — six completions, every Terminus type, all confirmed.",
    "Ultramarines": "The Codex Astartes does not contain a single chapter on the Master Terminus Slayer — because it assumes every warrior will specialise. **{name}** rendered that assumption incomplete.",
    "White Scars": "The Great Hunt answered in every class — **{name}** rode every Terminus Slayer form the Watch teaches and brought back verified kills from all of them.",
    "Wolfspear": "The Dark Terror hunts in all configurations. **{name}** has hunted in all six Terminus Slayer classes and confirmed every Terminus type — the fullest hunt record in the Watch.",
    "Black Shield": "No Chapter claims the Master Terminus Slayer. The title claims **{name}**. No lineage is required when the kill logs speak this clearly.",
}

MASTER_TERMINUS_SLAYER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master as Master Terminus Slayer is not a surprise to those who know what the Watch demands of its highest authority. **{name}** has confirmed it in every kill record.",
    "High Chaplain": "The High Chaplain's Master Terminus Slayer record is faith expressed in six classes of combat — the preacher who backed every sermon with verified kills across all of them.",
    "Chief Apothecary": "The Chief Apothecary saved brothers in every class. The Master Terminus Slayer record confirms **{name}** also ended threats in every class — the complete healer-hunter.",
    "Void Warden": "The Void Warden's Master Terminus Slayer record is the vigil made comprehensive — six classes, every Terminus front, every kill verified.",
    "Forgemaster": "The Forgemaster's Master Terminus Slayer record is the Omnissiah's complete recognition — every class mastered, every Terminus type eliminated, all verified.",
    "Castellan": "The Castellan's Master Terminus Slayer record is the fortress with six gates, each one defended and each one used for offensive action against Terminus threats.",
    "Lord Executioner": "The Lord Executioner's Master Terminus Slayer record is the execution mandate applied across six disciplines — the Watch's highest combat title at the Watch's highest kill tier.",
    "Venerable Dreadnought": "A Venerable Dreadnought with the Master Terminus Slayer title is antiquity's most complete statement — every era of warfare represented in one kill record.",
    "Honored Dreadnought": "The Honored Dreadnought's Master Terminus Slayer record is endurance's full expression — the eternal warrior who found time across six classes to verify every kill.",
    "Interred Brother": "An Interred Brother's Master Terminus Slayer record is the rarest of legacies — the warrior preserved within iron who still found the method for six class completions.",
    "Watch Chaplain": "The Chaplain's Master Terminus Slayer record is faith backed by the most complete kill record in the Watch's logs. The sermons write themselves.",
    "Watch Apothecary": "The Apothecary's Master Terminus Slayer record is the healer's full truth — every class deployed, every Terminus threat ended, every kill verified alongside the saves.",
    "Watch Librarian": "The Librarian's Master Terminus Slayer record is mind and combat fully unified — six classes of combat knowledge applied to every Terminus type the Watch tracks.",
    "Watch Techmarine": "The Techmarine's Master Terminus Slayer record is already filed with the Omnissiah. Every machine-spirit in the armoury registered its approval simultaneously.",
    "Watch Keeper": "The Keeper's Master Terminus Slayer record is the Watch's deepest vigil completed — every class, every Terminus type, every kill maintained in the permanent record.",
    "Company Champion": "The Company Champion's Master Terminus Slayer record is the Chapter's blade applied across six disciplines — the champion who found a way to champion everything.",
    "Kill Team Champion": "The Kill Team Champion's Master Terminus Slayer record is the team's greatest truth confirmed — the champion who fought for every brother, in every class, at Terminus level.",
    "Watch Captain": "A Watch Captain's Master Terminus Slayer record is command's ultimate proof — the leader who could demonstrate every discipline they ordered their warriors to master.",
    "Watch Lieutenant": "The Lieutenant's Master Terminus Slayer record is leadership earned in the most complete way possible — six class records, every Terminus type, all verified.",
    "Watch Sergeant": "The Sergeant's Master Terminus Slayer record is the squad's backbone multiplied across six disciplines — the NCO who set the example in every class.",
    "Oathsworn": "Oathsworn's Master Terminus Slayer record is the oath's full expression — sworn to every class, every Terminus type, every kill verified in the Watch's permanent record.",
    "Watch Veteran": "The Veteran's Master Terminus Slayer record is the rarest Veteran achievement in the Watch's logs — every class completed, every Terminus type confirmed.",
    "Watch Brother": "A Watch Brother bearing the Master Terminus Slayer title has built a foundation that most warriors retire without ever approaching. What follows will be extraordinary.",
}
