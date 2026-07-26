"""Rank, prestige, and Techmarine rank acknowledgment data."""

from typing import Dict, List  # noqa: F401

RANK_HONORIFICS: Dict[str, str] = {
    # High Command (check first)
    "Watch Master": "Lord of the Long Watch, Watch Master",
    "High Chaplain": "Voice of the Emperor, High Chaplain",
    "Chief Apothecary": "Keeper of Purity, Chief Apothecary",
    "Void Warden": "Aegis against the Void, Void Warden",
    "Forgemaster": "Hand of the Machine God, Forgemaster",
    "Castellan": "Warden of the Iron Vigil, Castellan",
    "Blade Master": "Blade of the Fortress, Blade Master",
    "Huntmaster": "Keeper of the Hunting Grounds, Huntmaster",
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
    "First Blade": "Blade of the Company, First Blade",
    "Bladeguard": "Blade of the Kill Team, Bladeguard",
    # Battle line (highest to lowest)
    "Watch Captain": "Warden of the Company, Watch Captain",
    "Watch Lieutenant": "Shield of the Watch, Watch Lieutenant",
    "Veteran Sergeant": "Veteran of command, Veteran Sergeant",
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
    "Blade Master": [
        "The Blade of the Fortress demands armor as sharp as his judgment.",
        "Your armor has tasted the blood of traitors; I sanctify it for more to come.",
        "The machine-spirit hungers for righteous execution at your command.",
    ],
    "Huntmaster": [
        "The Hunting Grounds demand armor that endures every environment — frozen void, scorching desert, crushing pressure. I ensure the Huntmaster's plate meets them all.",
        "The arcane mechanisms of the Hunting Grounds run because the Huntmaster and Forgemaster tend them together. I am honored to tend the armor that bears that burden in turn.",
        "Every kill-team sharpened within the Hunting Grounds owes something to the Huntmaster's stewardship. The least I can do is ensure the Huntmaster's own armor wants for nothing.",
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
    "Veteran Sergeant": [
        "Veteran Sergeant, your armor carries both command discipline and hard-won field wisdom.",
        "The veterans beneath your command read your warplate as a standard for steadiness under pressure.",
        "May this warplate honor the bridge you hold between squad command and company leadership.",
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
    "First Blade": [
        "Blade of the Company, your armor must match your peerless skill.",
        "The Champion's warplate has witnessed countless duels—may it witness countless more.",
        "The machine-spirit yearns for the glory of single combat at your side.",
    ],
    "Bladeguard": [
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
    "Blade Master": 0.9,
    "Huntmaster": 0.9,
    "Castellan": 0.9,
    # Dreadnoughts - high prestige
    "Venerable Dreadnought": 0.85,
    "Honored Dreadnought": 0.75,
    "Interred Brother": 0.2,  # Inactive, lower prestige
    # Company Command - high prestige
    "Watch Captain": 0.75,
    "Watch Lieutenant": 0.65,
    "Veteran Sergeant": 0.5,
    # Specialists - medium-high prestige
    "Watch Chaplain": 0.6,
    "Watch Apothecary": 0.6,
    "Watch Librarian": 0.6,
    "Watch Techmarine": 0.6,
    "Watch Keeper": 0.55,
    # Champions - medium prestige
    "First Blade": 0.5,
    "Bladeguard": 0.45,
    # Line ranks - lower prestige (studs matter more)
    "Watch Sergeant": 0.35,
    "Oathsworn": 0.25,
    "Watch Veteran": 0.2,
    "Watch Brother": 0.1,
}

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
    "Huntmaster": [
        "Each stud marks another span of stewardship — the Hunting Grounds have shaped more kill-teams than these walls remember.",
        "The Huntmaster's marks of service are tallied in cohesion forged and xenos-lore passed on — a living record of every warrior sharpened within the Grounds.",
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
    "Veteran Sergeant": [
        "A Veteran Sergeant's marks speak of command entrusted and command proven.",
        "Between line and company, your service has become the hinge others depend on.",
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

SOK_G_PIPEHITTER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "That the Watch Master themselves has answered the Pipehitter call is the deepest seal upon the designation's worth.",
    "High Chaplain": "The High Chaplain bearing the Pipehitter mark is a sermon written in operations rather than words.",
    "Chief Apothecary": "The Chief Apothecary as Pipehitter — healer and slayer fused into the Watch Master's blackest order.",
    "Void Warden": "The Void Warden's vigilance carried into Pipehitter operations is a singular thing — feared by the foe, treasured by the Watch.",
    "Forgemaster": "The Forgemaster as Pipehitter brings the machine-spirits' approval to operations no machine should witness.",
    "Castellan": "The Castellan as Pipehitter is the fortress's hidden hand reaching far beyond its walls.",
    "Blade Master": "The Blade Master as Pipehitter — the title and the designation share the same blade.",
    "Huntmaster": "The Huntmaster on Pipehitter operations carries the Hunting Grounds' hardest lessons beyond Jericho's walls — the only difference from a training run is the prey does not know the session has begun.",
    "Venerable Dreadnought": "A Venerable Dreadnought answering Pipehitter calls is a force of impossibility unleashed.",
    "Honored Dreadnought": "The Honored Dreadnought as Pipehitter — an ancient's wrath repurposed for the Watch Master's hidden errands.",
    "Interred Brother": "An Interred Brother walking Pipehitter operations is the rarest of weapons drawn for the rarest of needs.",
    "Watch Chaplain": "The Chaplain's faith carried into Pipehitter operations is itself a weapon the foe cannot answer.",
    "Watch Apothecary": "The Apothecary as Pipehitter saves brothers on operations brothers should not survive.",
    "Watch Librarian": "The Librarian's mind as Pipehitter tool — the foe is read before they are slain.",
    "Watch Techmarine": "The Techmarine as Pipehitter ensures the machine-spirits never falter, even on operations the spirits would refuse.",
    "Watch Keeper": "The Keeper's vigil applied to Pipehitter operations brings closure to every impossible front.",
    "First Blade": "The First Blade as Pipehitter — the Chapter's sharpest blade in the Watch's hidden sheath.",
    "Bladeguard": "The Bladeguard as Pipehitter is the team's spearpoint on the Watch Master's blackest fronts.",
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
    "Blade Master": "The Blade Master's blade and the Pipehitter's mark grow more deeply joined with each operation.",
    "Huntmaster": "The Huntmaster's repeated Pipehitter service is the Hunting Grounds taken beyond Jericho's walls — each operation another environment catalogued, another xenos truth confirmed in the field.",
    "Venerable Dreadnought": "A Venerable Dreadnought as repeated Pipehitter is a force the foe should pray to never face again.",
    "Honored Dreadnought": "The Honored Dreadnought's repeated Pipehitter service is ancient wrath rendered in the modern Watch's blackest ink.",
    "Interred Brother": "An Interred Brother walking repeated Pipehitter operations is a weapon the Watch deploys only when nothing else will suffice.",
    "Watch Chaplain": "The Chaplain's repeated Pipehitter faith is litany answered, again and again, on operations meant to silence prayer entirely.",
    "Watch Apothecary": "The Apothecary's repeated Pipehitter saves are a quiet legend among brothers who survived because of them.",
    "Watch Librarian": "The Librarian's repeated Pipehitter mind has unraveled foes the Watch Master barely understands.",
    "Watch Techmarine": "The Techmarine's repeated Pipehitter service has the Omnissiah's approval encoded into the machine-spirits' very rhythms.",
    "Watch Keeper": "The Keeper's repeated Pipehitter vigil has closed operation after operation that no one else could have closed.",
    "First Blade": "The First Blade's blade has cut deepest in repeated Pipehitter operations — the Chapter's legend grows with every return.",
    "Bladeguard": "The Bladeguard's repeated Pipehitter service is the team's blade sharpened to its absolute edge.",
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
    "Blade Master": "The Blade Master's Black Laurels is the blade laid down — for the Kill Team, before any foe.",
    "Huntmaster": "The Huntmaster's Black Laurels is the Hunting Grounds' foundational teaching confirmed — kill-teams prevail not through individual glory but through the cohesion the Grounds were built to forge, and this Huntmaster lived that truth in every mission.",
    "Venerable Dreadnought": "A Venerable Dreadnought as Black Laurels bearer is the ancients' final lesson: even legend serves the team.",
    "Honored Dreadnought": "The Honored Dreadnought's Black Laurels reminds every brother that even the eternal serves something greater than self.",
    "Interred Brother": "An Interred Brother's Black Laurels is the truest mark — even from within iron, the brotherhood remains.",
    "Watch Chaplain": "The Chaplain's Black Laurels is faith in the brotherhood made flesh, again and again.",
    "Watch Apothecary": "The Apothecary's Black Laurels is the healer's truest vow — every brother kept, every team held.",
    "Watch Librarian": "The Librarian's Black Laurels is mind given over to the Kill Team's purpose — no secret kept that could save a brother.",
    "Watch Techmarine": "The Techmarine's Black Laurels is the Omnissiah's recognition that the Kill Team is the machine's truest spirit.",
    "Watch Keeper": "The Keeper's Black Laurels is the vigil shared — every brother watched over, every team kept whole.",
    "First Blade": "The First Blade's Black Laurels is the Chapter's blade serving the team, not the legend.",
    "Bladeguard": "The Bladeguard's Black Laurels is the team's blade, sharpened by the team's own bond.",
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
    "Blade Master": "The Blade Master in venerated plate is the Watch's heaviest blade — and the Crux confirms it.",
    "Huntmaster": "The Huntmaster in venerated plate is the Hunting Grounds' deepest environmental record given form — centuries of xenos-lore and kill-team doctrine sealed in adamantium.",
    "Venerable Dreadnought": "A Venerable Dreadnought wearing the Crux is contradiction made manifest — and welcome.",
    "Honored Dreadnought": "The Honored Dreadnought's Crux is recognition the ancient still walks the line with their brothers in plate.",
    "Interred Brother": "An Interred Brother bearing the Crux is a sight few will ever see — and fewer still will forget.",
    "Watch Chaplain": "The Chaplain's Crux is faith armored — the litanies carried through the impossible in venerated plate.",
    "Watch Apothecary": "The Apothecary's Crux is the healer in venerated plate — the brother who endures so others may be saved.",
    "Watch Librarian": "The Librarian's Crux is mind and adamantium fused — the foe outthought and outendured in one form.",
    "Watch Techmarine": "The Techmarine's Crux is the Omnissiah's blessing rendered in machine-spirit and plate alike.",
    "Watch Keeper": "The Keeper's Crux is the vigil made absolute — venerated plate where no plate should be needed, and yet always is.",
    "First Blade": "The First Blade's Crux is the Chapter's blade in the Watch's heaviest sheath.",
    "Bladeguard": "The Bladeguard's Crux is the team's pinnacle — the spearpoint clad in venerated plate.",
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
    "Blade Master": "The Blade Master's blade fell at every Kadaku front — Leviathan met its match in the title and the warrior alike.",
    "Huntmaster": "The Huntmaster brought the Hunting Grounds' full catalogue of xenos environments to Kadaku's fronts — the Leviathan surge met a warrior who had already hunted in every terrain the Hive Mind could deploy.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Kadaku service is ancient wrath against the swarm, repeated until the swarm relented.",
    "Honored Dreadnought": "The Honored Dreadnought at every Kadaku operation is a campaign-long lesson in endurance for every younger brother.",
    "Interred Brother": "An Interred Brother walking the Kadaku Campaign is rare and singular — the Watch deploys their weight only when the weight is required.",
    "Watch Chaplain": "The Chaplain's faith carried Kadaku through its darkest hours; this Chaplain was there for every one.",
    "Watch Apothecary": "The Apothecary at every Kadaku front is uncounted brothers' survival made flesh.",
    "Watch Librarian": "The Librarian's mind across Kadaku read every Leviathan tide before it crested.",
    "Watch Techmarine": "The Techmarine's Kadaku service kept the machine-spirits steady where Leviathan biomass would have devoured lesser tools.",
    "Watch Keeper": "The Keeper's Kadaku vigil closed every operation with the meticulousness only the Keeper provides.",
    "First Blade": "The First Blade's Kadaku service set the standard every brother strove to match.",
    "Bladeguard": "The Bladeguard's Kadaku service was the spearpoint of every operation they joined.",
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
    "Blade Master": "The Blade Master's blade fell at every Reef-front — the title and the warrior in perfect alignment.",
    "Huntmaster": "The Huntmaster's Reef service applied the Hunting Grounds' void-training to the campaign's space-lanes — the heretic encountered a warrior who had already stalked every corridor-configuration in Jericho's sealed vaults.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Reef service is ancient wrath against the heretic across the full Persecution.",
    "Honored Dreadnought": "The Honored Dreadnought at every Reef-operation is endurance written in adamantium for every brother to study.",
    "Interred Brother": "An Interred Brother walking the Reef Persecution is the Watch's heaviest weapon used with great deliberation.",
    "Watch Chaplain": "The Chaplain's faith carried the Reef Persecution through its darkest fronts; this Chaplain held the line through them all.",
    "Watch Apothecary": "The Apothecary at every Reef-operation is the campaign's survival made flesh.",
    "Watch Librarian": "The Librarian's mind across the Reef read every heretic tide before it broke.",
    "Watch Techmarine": "The Techmarine's Reef service kept the machine-spirits steady across the void's many tests.",
    "Watch Keeper": "The Keeper's Reef vigil closed every operation with the meticulousness only the Keeper provides.",
    "First Blade": "The First Blade's Reef service set the standard every brother strove to match.",
    "Bladeguard": "The Bladeguard's Reef service was the spearpoint of every operation they joined.",
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
    "Blade Master": "The Blade Master's Distinguished Reef service is the blade laid alongside every brother's, never above.",
    "Huntmaster": "The Huntmaster's Distinguished Reef service is the Hunting Grounds' complete environmental curriculum applied to the Persecution — every terrain trained for within Jericho's walls, every operation of the campaign completed beyond them.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Distinguished Reef service is the ancient lesson — even legend serves the Kill Team.",
    "Honored Dreadnought": "The Honored Dreadnought's Distinguished Reef service reminds every brother that the eternal still stands the team's line.",
    "Interred Brother": "An Interred Brother's Distinguished Reef service is brotherhood preserved even from within iron.",
    "Watch Chaplain": "The Chaplain's Distinguished Reef service is faith placed in the team across every front of the Persecution.",
    "Watch Apothecary": "The Apothecary's Distinguished Reef service is every brother kept, every Kill Team held, across the full campaign.",
    "Watch Librarian": "The Librarian's Distinguished Reef service is mind given over to the team's purpose at every void-front.",
    "Watch Techmarine": "The Techmarine's Distinguished Reef service is the Omnissiah's recognition that the Kill Team is the truest machine-spirit.",
    "Watch Keeper": "The Keeper's Distinguished Reef service is the vigil shared with every brother across every operation.",
    "First Blade": "The First Blade's Distinguished Reef service is the Chapter's blade serving the team, every front, every operation.",
    "Bladeguard": "The Bladeguard's Distinguished Reef service is the team made manifest at its absolute fullest.",
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
    "Blade Master": "The Blade Master in The Order Omega is the heaviest blade laid alongside every brother's at the impossible difficulty.",
    "Huntmaster": "The Huntmaster in The Order Omega is the Hunting Grounds' most punishing session made real — Omega difficulty is simply the Huntmaster's daily curriculum administered without a safety line.",
    "Venerable Dreadnought": "A Venerable Dreadnought in The Order Omega is the ancient lesson at its highest — even legend serves the team at Omega.",
    "Honored Dreadnought": "The Honored Dreadnought in The Order Omega is endurance's purest form — eternity at the impossible difficulty, with brothers intact.",
    "Interred Brother": "An Interred Brother in The Order Omega is the rarest of weapons used in the rarest of fellowships.",
    "Watch Chaplain": "The Chaplain in The Order Omega is faith carried through Omega difficulty without leaving a brother behind.",
    "Watch Apothecary": "The Apothecary in The Order Omega is the healer's vow extended to every brother through every Omega operation.",
    "Watch Librarian": "The Librarian in The Order Omega is mind and brotherhood twined at the impossible difficulty.",
    "Watch Techmarine": "The Techmarine in The Order Omega is the Omnissiah's recognition that the Kill Team is the truest spirit at every difficulty.",
    "Watch Keeper": "The Keeper in The Order Omega is the vigil made absolute — every brother watched over, every operation closed at Omega.",
    "First Blade": "The First Blade in The Order Omega is the Chapter's blade serving the team at the Watch's absolute edge.",
    "Bladeguard": "The Bladeguard in The Order Omega is the team's pinnacle confirmed — spearpoint at Omega difficulty, with the team intact.",
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

DUAL_VIGIL_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master bearing the Order of the Aquiline Brotherhood is the Watch's highest authority confirming what two warriors can achieve when fully committed.",
    "High Chaplain": "The High Chaplain's Order of the Aquiline Brotherhood is faith made manifest in partnership — two voices, every absolute front, every oath held.",
    "Chief Apothecary": "The Chief Apothecary's Order of the Aquiline Brotherhood is protection extended to one brother, every absolute operation, without fail.",
    "Void Warden": "The Void Warden's Order of the Aquiline Brotherhood is the paired vigil taken to the Watch's absolute edge — two companions, every station, every front.",
    "Forgemaster": "The Forgemaster's Order of the Aquiline Brotherhood is the Omnissiah's recognition that even the machine-spirits respect a bond forged across every absolute operation.",
    "Castellan": "The Castellan's Order of the Aquiline Brotherhood is the fortress reduced to its truest element — two warriors, holding together through everything.",
    "Blade Master": "The Blade Master's Order of the Aquiline Brotherhood is the executioner's blade sharpened by partnership — one brother at the side, every absolute mission.",
    "Huntmaster": "The Huntmaster's Order of the Aquiline Brotherhood is the Hunting Grounds' founding lesson carried into the field — kill-teams hunt together or they do not hunt at all, and this Huntmaster proved it at absolute difficulty.",
    "Venerable Dreadnought": "A Venerable Dreadnought's Order of the Aquiline Brotherhood is antiquity's proof that the deepest bonds are forged in the smallest formations.",
    "Honored Dreadnought": "The Honored Dreadnought's Order of the Aquiline Brotherhood is endurance's testament — the eternal paired with one brother through every absolute front.",
    "Interred Brother": "An Interred Brother's Order of the Aquiline Brotherhood is the rarest bond of all — the preserved warrior and one companion, absolute operations, absolute commitment.",
    "Watch Chaplain": "The Chaplain's Order of the Aquiline Brotherhood is faith and partnership made indistinguishable — every absolute mission, one brother, one purpose.",
    "Watch Apothecary": "The Apothecary's Order of the Aquiline Brotherhood is the healer's vow concentrated — one brother kept whole across every absolute operation.",
    "Watch Librarian": "The Librarian's Order of the Aquiline Brotherhood is the psyker's focus narrowed to one companion — every absolute front, every absolute mission, together.",
    "Watch Techmarine": "The Techmarine's Order of the Aquiline Brotherhood is the Omnissiah's blessing on a partnership honed across every absolute operation.",
    "Watch Keeper": "The Keeper's Order of the Aquiline Brotherhood is the vigil shared with one brother, every operation, without deviation.",
    "First Blade": "The First Blade's Order of the Aquiline Brotherhood is the Chapter's blade paired with one brother's — every absolute mission a testament to what two champions can achieve.",
    "Bladeguard": "The Bladeguard's Order of the Aquiline Brotherhood is the team distilled to its truest pair — two warriors, absolute operations, complete.",
    "Watch Captain": "A Watch Captain's Order of the Aquiline Brotherhood is leadership reduced to its most essential form — two warriors, every absolute mission, no compromise.",
    "Watch Lieutenant": "The Lieutenant's Order of the Aquiline Brotherhood is the bond between warriors who earned their rank in the hardest possible company — one brother, every front.",
    "Watch Sergeant": "The Sergeant's Order of the Aquiline Brotherhood marks a warrior who led from the smallest possible formation — one brother, absolute difficulty, every absolute mission.",
    "Oathsworn": "Oathsworn's Order of the Aquiline Brotherhood is an oath kept in the most concentrated form — every absolute mission, exactly one brother, no deviation.",
    "Watch Veteran": "The Veteran's Order of the Aquiline Brotherhood is the mark of a warrior whose experience runs deep enough to trust a single brother through every absolute operation.",
    "Watch Brother": "A Watch Brother bearing the Order of the Aquiline Brotherhood has already shown a depth of commitment that veterans twice their seniority would respect.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Assault) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_ASSAULT_RANK_LINES: Dict[str, str] = {
    "Watch Master": "Where the Watch Master charges, the killing blow arrives first — at full thrust, without ceremony, and without anything left standing.",
    "High Chaplain": "The litanies the High Chaplain carries into combat are most effective at close-quarters range. The jump pack's arc is the faith made kinetic.",
    "Chief Apothecary": "The Chief Apothecary beneath jump pack thrust has settled on the most direct form of medicine — remove the threat before it requires a response.",
    "Void Warden": "The Void Warden's vigilance resolves at the end of a jump pack's arc. No Terminus-level threat should have the leisure to see the Warden coming.",
    "Forgemaster": "The Forgemaster has examined every weapon the Watch carries. The one brought to close-quarters range under full thrust is the one that requires no further refinement.",
    "Castellan": "The Castellan does not always defend from a fixed position — sometimes the fortress launches itself at jump pack velocity, blade already in hand.",
    "Blade Master": "The Blade Master who interprets the execution mandate as a jump pack charge at close-quarters distance has interpreted it correctly.",
    "Huntmaster": "The Huntmaster's assault Terminus kill is the Hunting Grounds' close-quarters corridors applied at their conclusion — the specimen at the end of the run simply happened to be Terminus-class.",
    "Venerable Dreadnought": "The ancient has carried the close-quarters charge across centuries. Where the jump pack shortens the approach, the Venerable Dreadnought already knew the destination.",
    "Honored Dreadnought": "The Honored Dreadnought needs no novel doctrine to confirm the oldest truth — close the distance, deliver the blow, and the rest follows.",
    "Interred Brother": "Preserved in iron, uninterested in waiting — the Interred Brother beneath jump pack thrust is the Watch's most surprising argument for closing first and asking nothing.",
    "Watch Chaplain": "The Chaplain's creed flies furthest when launched beneath a jump pack. Close-quarters range is where the sermon ends and the result begins.",
    "Watch Apothecary": "The Apothecary who masters the direct charge has decided that the finest preventative care is delivered at close-quarters range, before any wound is possible.",
    "Watch Librarian": "The Librarian brings considerable gifts to every engagement. At close-quarters range, the most valuable gift is the willingness to close without hesitation.",
    "Watch Techmarine": "The Techmarine has calibrated the jump pack's thrust with the same precision applied to everything else. The killing blow's delivery point is exactly where the calculation said it would be.",
    "Watch Keeper": "The Keeper's watch ends when the jump pack fires. There is a form of vigilance that can only be fulfilled at close-quarters distance, blade in hand.",
    "First Blade": "The First Blade at jump pack range is the Chapter's finest argument delivered in person — the blade and the hammer both carried to the point of maximum decision.",
    "Bladeguard": "The Bladeguard who closes beneath jump pack thrust leads the charge the team cannot. The killing blow at the end of it makes the result available to everyone.",
    "Watch Captain": "The Captain who arrives first under a jump pack's thrust commands in the oldest sense of the word. Close-quarters range is where leadership becomes undeniable.",
    "Watch Lieutenant": "The Lieutenant who closes at blade distance has concluded that the correct position for an officer is at the end of the charge, not behind it.",
    "Watch Sergeant": "The Sergeant beneath jump pack thrust shows the squad exactly what the front looks like. The killing blow confirms the lesson before the squad arrives to learn it.",
    "Oathsworn": "The oath to close on what others cannot reach finds its mechanism in the jump pack's thrust. The pack provides the commitment; the blade provides the resolution.",
    "Watch Veteran": "The Veteran's experience turns every jump pack launch into certainty. The killing blow has been delivered enough times that the approach requires no reconsideration.",
    "Watch Brother": "The Watch Brother who commits to the close-quarters charge has made the decision before most have decided it is warranted. The jump pack confirms it in flight.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Bulwark) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_BULWARK_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's Bulwark discipline is the Watch's doctrine in two movements — the banner for brothers first, the blade for the threat second. Neither phase is optional.",
    "High Chaplain": "The High Chaplain plants the banner for the brothers and draws the blade for the threat. Faith is not passive behind the shield — it is the preparation for what follows.",
    "Chief Apothecary": "The Chief Apothecary behind shield and banner has mastered the Bulwark's first truth: protect what matters, then answer what threatens it. Both acts belong to the same warrior.",
    "Void Warden": "The Void Warden's shield is a watching post. The vigil continues until the banner is planted and the brothers are covered — and then the blade settles what remains.",
    "Forgemaster": "The Forgemaster understands that the shield and the blade are not opposed tools. They are sequential ones, and the Bulwark's craft is knowing exactly when the sequence advances.",
    "Castellan": "The Castellan's walls take every form available — including the shield that comes before the blade. The fortress is never finished until both phases are complete.",
    "Blade Master": "The Blade Master who works from behind a banner has not abandoned the mandate — they have staged it. The shield absorbs; the blade executes on schedule.",
    "Huntmaster": "The Huntmaster as Bulwark Terminus Slayer held the line the way the Hunting Grounds contain their most dangerous xenos specimens — absolute, immovable, until the threat is fully resolved.",
    "Venerable Dreadnought": "The ancient has watched warriors learn the Bulwark's lesson across centuries. The shield comes first. The blade earns its moment. This is not a doctrine — it is an understanding.",
    "Honored Dreadnought": "The Honored Dreadnought endures long enough to complete both phases of the Bulwark's work — the banner planted for brothers, the blade drawn when the position has served its purpose.",
    "Interred Brother": "The Interred Brother behind shield and banner is the Watch's most fortified position made mobile. The blade that emerges after the banner has been planted is the position's second phase.",
    "Watch Chaplain": "The Chaplain behind the banner is the creed made structural — brothers sheltered by the shield's width, the blade held for the moment the creed requires answering what threatens them.",
    "Watch Apothecary": "The Apothecary behind the Bulwark's shield protects in both directions — the banner gives brothers what the narthecium would have needed, and the blade settles what made it necessary.",
    "Watch Librarian": "The Librarian's Bulwark discipline is strategy made physical — the banner positioned where it serves, the shield held on purpose, the blade drawn precisely when the position it created has done its work.",
    "Watch Techmarine": "The Techmarine behind shield and banner has assessed the engagement sequence and found the Bulwark's two-phase approach to be the most mechanically sound available.",
    "Watch Keeper": "The Keeper's vigil has a second act. After the banner is planted and the brothers are covered, the blade answers whatever the shield absorbed. Both phases belong to the same watch.",
    "First Blade": "The First Blade behind the Bulwark's shield is the Chapter's defender in the fullest sense — the blade that emerges from the protected position is the Chapter's answer after the protection has been given.",
    "Bladeguard": "The Bladeguard who takes up the shield and banner gives the team everything the shield and banner offer, and then gives the team one more thing — the blade drawn when the position has served its purpose.",
    "Watch Captain": "A Watch Captain's Bulwark discipline is command demonstrated in sequence — the banner for the brothers before the blade for the threat. Both phases are the officer's responsibility.",
    "Watch Lieutenant": "The Lieutenant who raises the banner first and draws the blade second has learned the Bulwark's lesson. The shield is not the end of the engagement — it is the beginning of the second phase.",
    "Watch Sergeant": "The Sergeant's Bulwark discipline shows the squad what sequence looks like in practice — the shield deployed, the banner raised, and then the blade drawn at exactly the right moment.",
    "Oathsworn": "The oath behind the Bulwark's shield comes in two parts. The banner honors the first part. The blade honors the second. Neither is ever forgotten by the warrior who has sworn both.",
    "Watch Veteran": "The Veteran knows the Bulwark's sequence well enough that the transition from banner to blade requires no decision. By this point, the shield has done its work and the blade knows it.",
    "Watch Brother": "The Watch Brother who takes up the shield and banner has already learned what takes most warriors years to understand — protect first, answer second, and never confuse the order.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Heavy) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_HEAVY_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master behind the barrier with a heavy weapon in hand has taken the Watch's most powerful standard tools and applied them from the most defensible position available. The question of whether this is sufficient answers itself.",
    "High Chaplain": "The High Chaplain's heaviest sermon is delivered from behind the barrier. The heavy weapon's argument is theological in its own way — sustained, authoritative, and final.",
    "Chief Apothecary": "The Chief Apothecary behind a barrier with a heavy weapon has determined that the most effective form of care is applied before the wound can occur. Sustained firepower makes this determination in bulk.",
    "Void Warden": "The Void Warden's watch from behind the barrier is the vigil fortified — the heavy weapon extending the reach of the position further than any wall the watch could occupy.",
    "Forgemaster": "The Forgemaster knows which weapons the Watch carries and which position makes those weapons most effective. The barrier confirms the position; the heavy weapon confirms the Forgemaster's assessment.",
    "Castellan": "The Castellan's barrier is a wall in miniature — portable, sufficient, and deployed forward of the threat rather than behind it. The heavy weapon behind it is the wall's active component.",
    "Blade Master": "The Blade Master behind the barrier with sustained heavy fire has determined that the warrant does not require proximity. Maximum range and maximum firepower produce the same finality.",
    "Huntmaster": "The Huntmaster's heavy-weapon Terminus kill is the Hunting Grounds' long-range environments given their final expression — the target at range, the weapon steady, the record clean.",
    "Venerable Dreadnought": "The ancient behind the barrier demonstrates that the longest effective range and the heaviest weapons are as valid a counsel as any — and that patience is the virtue that makes both possible.",
    "Honored Dreadnought": "The Honored Dreadnought behind the barrier is endurance applied to the heaviest position available. The heavy weapon speaks for as long as the position demands.",
    "Interred Brother": "The Interred Brother behind the barrier is the Watch's most sustained position — the heavy weapon held at the angle the preserved warrior calculated, for as long as the engagement requires.",
    "Watch Chaplain": "The Chaplain's creed from behind the barrier is the Watch's heaviest theology — the barrier shelters brothers, the heavy weapon delivers the argument at sustained range.",
    "Watch Apothecary": "The Apothecary behind the Heavy's barrier has made the barrier itself the first form of care — the heavy weapon ensuring that what shelters behind it never has to be mended.",
    "Watch Librarian": "The Librarian behind the Heavy's barrier has identified the most effective firing solution and committed to it. Sustained fire at the correct target is not a compromise — it is the conclusion of the calculation.",
    "Watch Techmarine": "The Techmarine behind the barrier has assessed the structural integrity, verified the firing angles, and brought the heavy weapon to bear with the mechanical satisfaction the Omnissiah expects.",
    "Watch Keeper": "The Keeper's vigil from behind the barrier with a heavy weapon is the watch at its most fortified — the position held, the weapon speaking, the vigil sustained for as long as the engagement requires.",
    "First Blade": "The First Blade behind the Heavy's barrier is the Chapter's heaviest argument — sustained fire at the range where the heavy weapon is most effective, the position held until the argument is resolved.",
    "Bladeguard": "The Bladeguard who holds the Heavy's firing position gives the team the most sustained form of fire support available. The barrier makes the position permanent; the heavy weapon makes it lethal.",
    "Watch Captain": "A Watch Captain who holds the Heavy's position is an officer who understands that the best position is not always the most forward one — it is the one from which the heavy weapon speaks most effectively.",
    "Watch Lieutenant": "The Lieutenant who positions behind the barrier with a heavy weapon has learned that some engagements are won from a fixed position with sustained fire rather than a charge. The barrier is the decision made physical.",
    "Watch Sergeant": "The Sergeant behind the barrier shows the squad what the Heavy's position looks like when it is held correctly — the heavy weapon speaking at range, the squad supported by the fire it provides.",
    "Oathsworn": "The oath behind the Heavy's barrier is sustained in every sense — the position held, the weapon firing, the vow maintained for as long as the engagement requires the heavy weapon to speak.",
    "Watch Veteran": "The Veteran behind the Heavy's barrier has held enough positions to know when the barrier is where the engagement will be decided. The heavy weapon confirms the assessment over sustained fire.",
    "Watch Brother": "The Watch Brother who sets the barrier and levels the heavy weapon has committed to the discipline of holding the position — the line held for as long as the engagement requires.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Sniper) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_SNIPER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master behind the scope has decided that authority does not require proximity. The cloak, the angle, and the patience to wait for certainty — that is command at maximum range.",
    "High Chaplain": "The High Chaplain's litanies carry furthest when delivered from concealment. The scope finds the angle; the trigger is the sermon's conclusion. The target's awareness is irrelevant.",
    "Chief Apothecary": "The Chief Apothecary behind the Sniper's cloak has chosen prevention over remedy — the most precise intervention available, delivered at the range where the threat cannot close before the result is already final.",
    "Void Warden": "The Void Warden watches longest at the furthest range. From behind the cloak, the vigil is perfect — the threat observed, the angle confirmed, the shot placed before the threat can act on being watched.",
    "Forgemaster": "The Forgemaster has calibrated the Sniper's tools to their theoretical maximum. The cloak, the scope, the precise application of force at distance — all of it performs exactly as the Omnissiah intended.",
    "Castellan": "The Castellan's longest wall is the range the Sniper holds. Nothing that threatens from inside the scope's reach has any path to the position — the cloak ensures it stays that way.",
    "Blade Master": "The Blade Master who delivers the warrant from concealment has found the most final form of execution — the target has no opportunity to contest the order, no way to locate the officer, and no forewarning whatsoever.",
    "Huntmaster": "The Huntmaster's sniper Terminus kill is the Hunting Grounds' patient stalk given permanent expression — decades of tracking every xenos behavioral pattern, resolved in a single pull.",
    "Venerable Dreadnought": "The ancient has learned patience in ways few warriors can comprehend. From behind the Sniper's cloak, that patience resolves the moment the angle is certain.",
    "Honored Dreadnought": "The Honored Dreadnought behind the scope is endurance applied to precision — the position held, the cloak maintained, the shot fired exactly when the engagement demands it.",
    "Interred Brother": "The Interred Brother beneath the Sniper's cloak is the Watch's most patient combatant — the preserved warrior who waited for the angle and took it from the range no one expected.",
    "Watch Chaplain": "The Chaplain's faith from behind the scope is quiet and certain. The cloak conceals the warrior; the trigger delivers the creed at maximum range.",
    "Watch Apothecary": "The Apothecary at Sniper range has determined that the most effective form of care is placed before the wound can occur. The cloak ensures the placement is made from a distance that cannot be closed in time.",
    "Watch Librarian": "The Librarian behind the scope has calculated the optimal approach and found it to be patience, concealment, and the single shot placed where it cannot be recovered from.",
    "Watch Techmarine": "The Techmarine behind the scope has assessed the kill line, the target's vulnerabilities, and the optimal deployment of the cloak — the shot that follows is the calculation's conclusion.",
    "Watch Keeper": "The Keeper behind the scope watches longest and from furthest. The cloak is the vigil's form; the trigger is its resolution.",
    "First Blade": "The First Blade at Sniper range is the Chapter's finest precision — the blade's equivalent at distance, delivered from behind the cloak where no counterargument is possible.",
    "Bladeguard": "The Bladeguard who masters the long-range approach gives the team its longest reach — the cloak covering the approach, the scope finding the angle, the shot removing what the team could not otherwise safely close on.",
    "Watch Captain": "A Watch Captain behind the scope leads from a position that cannot be countered. The cloak conceals the command; the trigger exercises it from the range the threat cannot bridge.",
    "Watch Lieutenant": "The Lieutenant who masters the cloak and long shot has learned that precision at range serves the team as fully as a charge at close quarters — from behind the cloak, both the officer and the mission are effectively invisible.",
    "Watch Sergeant": "The Sergeant's Sniper discipline teaches the squad that the furthest position is sometimes the most effective one. The cloak stabilises the approach; the scope confirms when 'effective' becomes 'final.'",
    "Oathsworn": "The oath behind the Sniper's cloak is fulfilled at the furthest possible range — the vow that no threat warranting a scope's attention will outlast the patience required to place the shot correctly.",
    "Watch Veteran": "The Veteran behind the scope has spent enough time in the cloak to know exactly when to fire. The patience required to reach that certainty is the marksman's deepest discipline.",
    "Watch Brother": "The Watch Brother who masters the long-range cloak-and-scope approach has already learned what most warriors sit with for years — that the cloak, the patience, and the precisely placed shot are the fullest expression of what one warrior can accomplish at range.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Tactical) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_TACTICAL_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's authority extends to every approach the Watch teaches. Versatility is the doctrine of that authority made personal — no preferred weapon, no preferred range, only the response that the auspex confirms is correct.",
    "High Chaplain": "The High Chaplain carries every weapon the faith demands. The auspex scan identifies which one the moment requires; the High Chaplain carries no preference that would slow the answer.",
    "Chief Apothecary": "The Chief Apothecary applies the same all-round competence that defines their art into the full range of combat capabilities the Watch recognises.",
    "Void Warden": "The Void Warden watches for threats in every direction and answers them with whatever the auspex prescribes. Versatility is the vigil expressed as comprehensive capability.",
    "Forgemaster": "The Forgemaster's mastery of the Watch's weapons has no upper limit. The auspex identifies the weakness; the Forgemaster carries whatever addresses it.",
    "Castellan": "The Castellan's fortress is most effective when it adapts. Versatility is adaptation made doctrine — the auspex scan as the first act, the correct response as the second, no preference limiting either.",
    "Blade Master": "The Blade Master's mandate specifies no preferred method. Versatility is the fullest expression of that mandate — the auspex identifies the weakpoint; the weapon is selected accordingly.",
    "Huntmaster": "The Huntmaster's tactical Terminus kill is the Hunting Grounds' core lesson applied — read the xenos, choose the terrain, close the session. The Terminus-class rating changes nothing about the methodology.",
    "Venerable Dreadnought": "The ancient has applied every approach the Watch teaches across more engagements than most warriors can count. Every approach, in the Venerable Dreadnought's hands, is accumulated mastery applied without limitation.",
    "Honored Dreadnought": "The Honored Dreadnought who outlasts every limitation has the limitations most warriors impose on themselves. Every approach is available; the auspex determines which one the engagement requires.",
    "Interred Brother": "The Interred Brother who deploys every approach brings preserved experience to bear across the full range of approaches the Watch recognises — the auspex scan, the weakpoint, and whatever weapon the moment demands.",
    "Watch Chaplain": "The Chaplain behind the auspex carries every weapon the faith allows and applies whichever one the scan confirms as necessary. No approach is beneath the creed; every weakpoint is within its scope.",
    "Watch Apothecary": "The Apothecary who masters every approach extends all-round care to combat — the auspex identifies the threat's vulnerability, and the narthecium is still in one hand while the correct weapon fills the other.",
    "Watch Librarian": "The Librarian behind the auspex scan has the fullest picture of what the engagement requires. Every approach ready is the mind's most complete expression — every approach kept ready, every weakpoint addressed by the optimal response.",
    "Watch Techmarine": "The Techmarine who verifies every approach has confirmed that every approach the Watch teaches is mechanically sound. The auspex confirms which one the engagement requires; the preparation ensures it is always available.",
    "Watch Keeper": "The Keeper's vigil encompasses every approach the Watch teaches. The auspex extends the watch; the correct weapon extends the resolution.",
    "First Blade": "The First Blade who masters every approach is the Chapter's most complete expression — every weapon applied, every weakpoint addressed, no approach refused.",
    "Bladeguard": "The Bladeguard who masters every approach is the team's most complete asset — the auspex identifies what the team cannot close on alone, and the Champion selects from every approach available to address it.",
    "Watch Captain": "A Watch Captain who masters every approach leads from the widest possible understanding — every approach available, every weakpoint identified, every weapon in the arsenal considered before the one most suited is selected.",
    "Watch Lieutenant": "The Lieutenant who masters every approach has extended their effectiveness across every range and approach the Watch teaches. The auspex scan is the officer's widest command — it precedes every other decision.",
    "Watch Sergeant": "The Sergeant who reads every engagement through the auspex gives the squad the most adaptable anchor available — the auspex reading the engagement, the NCO selecting the correct response from every approach the Watch has taught.",
    "Oathsworn": "The oath has no preferred direction — it applies to every approach, every weakpoint, every weapon the Watch carries. The auspex confirms which one the moment requires; the oath ensures the warrior is ready for all of them.",
    "Watch Veteran": "The Veteran who masters every approach has made every option available and every option reliable. The auspex scan runs faster because the warrior has seen every result it can recommend.",
    "Watch Brother": "The Watch Brother who masters every approach has already demonstrated what takes most warriors years to learn — that full capability, properly applied, arrives at the correct answer faster than any single preference would.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Techmarine) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_TECHMARINE_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master who engineers the kill zone has elevated command to its most deliberate form — the battlefield itself made into the weapon, the machine-spirits serving as execution.",
    "High Chaplain": "The High Chaplain who deploys the Tarantula Sentry Guns has found the Omnissiah's most practical form of faith — the machine-spirits prepared, the zone locked, the litany backed by automated fire.",
    "Chief Apothecary": "The Chief Apothecary who masters zone control has found the broadest form of care — the kill zone prevents more wounds than the narthecium could ever address, built before the engagement begins.",
    "Void Warden": "The Void Warden's watch becomes structural when the Tarantula Sentry Guns are deployed. The machine-spirits watch where the warrior cannot; the zone controls what the vigil alone could not.",
    "Forgemaster": "The Forgemaster who builds the kill zone is working in the most familiar medium — the battlefield engineered with the same craft applied to the armoury, the machine-spirits directed with the same authority.",
    "Castellan": "The Castellan deploys the Tarantula Sentry Guns as forward battlements — the zone control a portable fortress, the machine-spirits the garrison that never tires.",
    "Blade Master": "The Blade Master who makes the Tarantula Sentry Gun the instrument of the warrant has found the most mechanical form of execution — the zone built, the machine-spirit aimed, the result independent of the blade.",
    "Huntmaster": "The Huntmaster's Techmarine Terminus kill is the Hunting Grounds' arcane mechanisms scaled beyond Jericho's walls — the Forgemaster partnership extended to the field, directed at a specimen worthy of the collaboration.",
    "Venerable Dreadnought": "The ancient who deploys the kill zone has refined the patience of centuries into a single deliberate act — the Tarantula Sentry Guns placed where only experience would know to put them.",
    "Honored Dreadnought": "The Honored Dreadnought who masters zone control has built endurance into the battlefield itself. The machine-spirits sustain the position; the Honored Dreadnought sustains the machine-spirits.",
    "Interred Brother": "The Interred Brother who engineers the kill zone has converted preserved knowledge into terrain — the Tarantula Sentry Guns placed where only accumulated experience identifies the correct angle.",
    "Watch Chaplain": "The Chaplain who deploys the Tarantula Sentry Guns has engineered the creed into the battlefield. The machine-spirits are as dutiful as any brother; the zone they control is the Chaplain's most permanent sermon.",
    "Watch Apothecary": "The Apothecary who masters zone control has constructed the broadest form of care — the Tarantula Sentry Guns addressing the threat before it reaches the brothers, the preparation serving in place of the remedy.",
    "Watch Librarian": "The Librarian who deploys the kill zone has applied calculation to the battlefield rather than the individual. The Tarantula Sentry Guns execute the analysis; the zone is the Librarian's most spatial conclusion.",
    "Watch Techmarine": "The Watch Techmarine who engineers the kill zone has given the Omnissiah's tools their fullest expression — the machine-spirits deployed, the zone locked, the battlefield itself made into the mechanism of the Watch's intent.",
    "Watch Keeper": "The Keeper's vigil extends through the Tarantula Sentry Guns' sensors. The kill zone is the watch made permanent — the machine-spirits maintaining where the warrior cannot stand alone.",
    "First Blade": "The First Blade who deploys the kill zone has made the battlefield the Chapter's champion — the Tarantula Sentry Guns fighting where the blade cannot reach, the zone protecting what the blade stands before.",
    "Bladeguard": "The Bladeguard who engineers the kill zone gives the team the most deliberate support available — the zone built before the engagement, the machine-spirits active so the team can operate inside the preparation.",
    "Watch Captain": "A Watch Captain who engineers the kill zone commands before the engagement begins — the Tarantula Sentry Guns placed, the zone established, the engagement shaped by the preparation rather than the reaction.",
    "Watch Lieutenant": "The Lieutenant who masters zone control has found the widest form of leadership in this class — the Tarantula Sentry Guns extending the officer's reach across the full depth of the engagement.",
    "Watch Sergeant": "The Sergeant who deploys the kill zone has given the squad the most mechanically sophisticated form of support available — the Tarantula Sentry Guns watching the approach, the zone controlling what the squad cannot.",
    "Oathsworn": "The oath behind the Techmarine's zone control is kept before the first enemy arrives — the machine-spirits deployed, the Tarantula Sentry Guns aimed, the vow fulfilled in the engineering of the conditions for victory.",
    "Watch Veteran": "The Veteran who masters zone control has learned to engineer the engagement rather than simply enter it. The Tarantula Sentry Guns are placed where experience says they will matter most.",
    "Watch Brother": "The Watch Brother who deploys the Tarantula Sentry Guns has already learned what the discipline demands above all else — the kill zone must be built before it is needed, and the machine-spirits must be prepared before they are called on.",
}

# ---------------------------------------------------------------------------
# Terminus Slayer (Vanguard) award announcement flavor text
# ---------------------------------------------------------------------------

TERMINUS_SLAYER_VANGUARD_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master's authority does not require a gradual approach. The grapnel closes the distance; everything else follows from the moment of arrival.",
    "High Chaplain": "The High Chaplain who closes by grapnel delivers the creed at the speed the grapnel allows — the litany arriving with the warrior, at the range where the warrior is already inside the response.",
    "Chief Apothecary": "The Chief Apothecary who closes by grapnel has decided the fastest form of intervention is the one that arrives before the wound is possible. The grapnel is the narthecium's preemptive argument.",
    "Void Warden": "The Void Warden's vigil becomes something else when the grapnel fires. The watch ends the moment the line reaches the target — the distance collapsed, the engagement decided at close quarters.",
    "Forgemaster": "The Forgemaster who closes by grapnel has verified that the grapnel's mechanism functions precisely as designed. The close-quarters weapon at the end of the line functions the same way.",
    "Castellan": "The Castellan does not always hold ground — sometimes the fortress launches the grapnel and arrives at the threat's position before the threat can choose the terms of the engagement.",
    "Blade Master": "The Blade Master who closes by grapnel has delivered the warrant at the speed it deserves — the target reached before it could prepare, the outcome settled at the range the grapnel selected.",
    "Huntmaster": "The Huntmaster's vanguard Terminus kill is the Hunting Grounds' flanking-lanes taken to the field — the specimen never perceived the approach it should have been trained to fear.",
    "Venerable Dreadnought": "The ancient who fires the grapnel has found a way to close distance that centuries of experience confirm is correct. The grapnel is the oldest argument for arriving first, made modern.",
    "Honored Dreadnought": "The Honored Dreadnought who closes by grapnel has refused to accept that preservation limits the approach. The line launches, the distance collapses, and the close-quarters weapon does what it was made for.",
    "Interred Brother": "The Interred Brother who fires the grapnel is the Watch's most unexpected arrival — the preserved warrior who collapses the distance at Vanguard speed and engages at close quarters before anyone has accounted for the trajectory.",
    "Watch Chaplain": "The Chaplain who closes by grapnel delivers the creed at the range where the target cannot fail to receive it. The grapnel commits; the close-quarters weapon concludes.",
    "Watch Apothecary": "The Apothecary who closes by grapnel has chosen the most direct form of prevention — arriving at the threat's position at Vanguard speed ensures the threat cannot reach the brothers it was aimed at.",
    "Watch Librarian": "The Librarian who fires the grapnel has calculated that the optimal engagement distance is sometimes the shortest one. The grapnel commits to that calculation; the close-quarters weapon resolves it.",
    "Watch Techmarine": "The Techmarine who fires the grapnel has verified the mechanism, assessed the anchor point, and committed to the trajectory. The close-quarters engagement at the end of the line is the calculation concluded.",
    "Watch Keeper": "The Keeper's vigil ends when the grapnel fires. The distance maintained through the approach collapses the moment the line reaches its anchor — the vigil becomes an arrival.",
    "First Blade": "The First Blade who closes by grapnel is the Chapter's finest argument delivered at speed — the blade arriving at the threat's position before the threat has finished deciding how to meet it.",
    "Bladeguard": "The Bladeguard who fires the grapnel closes on what the team cannot. The grapnel launches before the formation reaches the target; the engagement at close quarters removes the threat before it can reverse the distance.",
    "Watch Captain": "A Watch Captain who closes by grapnel commands from the front in the most direct sense — arriving at the threat's position first, engaging at close quarters, and leaving no ambiguity about who led the approach.",
    "Watch Lieutenant": "The Lieutenant who masters the grapnel approach has found that the fastest contribution to the engagement is sometimes the most useful one. The grapnel closes the distance; the close-quarters weapon closes the account.",
    "Watch Sergeant": "The Sergeant who launches the grapnel has decided that the threat is the squad's first problem and the squad's least patient NCO is the first response. The close-quarters weapon confirms the Sergeant's intent on arrival.",
    "Oathsworn": "The oath behind the grapnel's line is kinetic — the vow to close on what others cannot reach is fulfilled the moment the anchor holds and the distance is no longer what it was.",
    "Watch Veteran": "The Veteran who masters the grapnel has learned when to fire the grapnel — the calculation is instant now, the commitment reflexive, the close-quarters engagement at the end of the line already decided before the trigger is pulled.",
    "Watch Brother": "The Watch Brother who masters the grapnel approach has answered the question that every warrior considers at some point — when the grapnel is the right tool, and how to commit to the close-quarters engagement at the end of the line without hesitation.",
}

# ---------------------------------------------------------------------------
# Master Terminus Slayer award announcement flavor text
# ---------------------------------------------------------------------------

MASTER_TERMINUS_SLAYER_RANK_LINES: Dict[str, str] = {
    "Watch Master": "The Watch Master as Master Terminus Slayer is not a surprise to those who know what the Watch demands of its highest authority. **{name}** has confirmed it in every kill record.",
    "High Chaplain": "The High Chaplain's Master Terminus Slayer record is faith expressed in six classes of combat — the preacher who backed every sermon with verified kills across all of them.",
    "Chief Apothecary": "The Chief Apothecary saved brothers in every class. The Master Terminus Slayer record confirms **{name}** also ended threats in every class — the complete healer-hunter.",
    "Void Warden": "The Void Warden's Master Terminus Slayer record is the vigil made comprehensive — six classes, every Terminus front, every kill verified.",
    "Forgemaster": "The Forgemaster's Master Terminus Slayer record is the Omnissiah's complete recognition — every class mastered, every Terminus type eliminated, all verified.",
    "Castellan": "The Castellan's Master Terminus Slayer record is the fortress with six gates, each one defended and each one used for offensive action against Terminus threats.",
    "Blade Master": "The Blade Master's Master Terminus Slayer record is the execution mandate applied across six disciplines — the Watch's highest combat title at the Watch's highest kill tier.",
    "Huntmaster": "The Master Terminus Slayer Huntmaster is the Hunting Grounds' ultimate graduate — the one who designed the curriculum has passed its highest examination, nine times over and without equivocation.",
    "Venerable Dreadnought": "A Venerable Dreadnought with the Master Terminus Slayer title is antiquity's most complete statement — every era of warfare represented in one kill record.",
    "Honored Dreadnought": "The Honored Dreadnought's Master Terminus Slayer record is endurance's full expression — the eternal warrior who found time across six classes to verify every kill.",
    "Interred Brother": "An Interred Brother's Master Terminus Slayer record is the rarest of legacies — the warrior preserved within iron who still found the method for six class completions.",
    "Watch Chaplain": "The Chaplain's Master Terminus Slayer record is faith backed by the most complete kill record in the Watch's logs. The sermons write themselves.",
    "Watch Apothecary": "The Apothecary's Master Terminus Slayer record is the healer's full truth — every class deployed, every Terminus threat ended, every kill verified alongside the saves.",
    "Watch Librarian": "The Librarian's Master Terminus Slayer record is mind and combat fully unified — six classes of combat knowledge applied to every Terminus type the Watch tracks.",
    "Watch Techmarine": "The Techmarine's Master Terminus Slayer record is already filed with the Omnissiah. Every machine-spirit in the armoury registered its approval simultaneously.",
    "Watch Keeper": "The Keeper's Master Terminus Slayer record is the Watch's deepest vigil completed — every class, every Terminus type, every kill maintained in the permanent record.",
    "First Blade": "The First Blade's Master Terminus Slayer record is the Chapter's blade applied across six disciplines — the champion who found a way to champion everything.",
    "Bladeguard": "The Bladeguard's Master Terminus Slayer record is the team's greatest truth confirmed — the champion who fought for every brother, in every class, at Terminus level.",
    "Watch Captain": "A Watch Captain's Master Terminus Slayer record is command's ultimate proof — the leader who could demonstrate every discipline they ordered their warriors to master.",
    "Watch Lieutenant": "The Lieutenant's Master Terminus Slayer record is leadership earned in the most complete way possible — six class records, every Terminus type, all verified.",
    "Watch Sergeant": "The Sergeant's Master Terminus Slayer record is the squad's backbone multiplied across six disciplines — the NCO who set the example in every class.",
    "Oathsworn": "Oathsworn's Master Terminus Slayer record is the oath's full expression — sworn to every class, every Terminus type, every kill verified in the Watch's permanent record.",
    "Watch Veteran": "The Veteran's Master Terminus Slayer record is the rarest Veteran achievement in the Watch's logs — every class completed, every Terminus type confirmed.",
    "Watch Brother": "A Watch Brother bearing the Master Terminus Slayer title has built a foundation that most warriors retire without ever approaching. What follows will be extraordinary.",
}


_BLADE_ROLE_ALIASES: Dict[str, str] = {
    "Lord Executioner": "Blade Master",
    "Company Champion": "First Blade",
    "Kill Team Champion": "Bladeguard",
}


def _apply_blade_role_aliases(mapping: Dict[str, object]) -> None:
    """Copy legacy blade-role flavor to champion-track aliases when absent."""
    for alias_role, canonical_role in _BLADE_ROLE_ALIASES.items():
        if alias_role in mapping:
            continue
        if canonical_role in mapping:
            mapping[alias_role] = mapping[canonical_role]


def _copy_rank_fallback(mapping: Dict[str, object], *, target_role: str, source_role: str) -> None:
    """Copy source rank flavor to target rank when target is not yet authored."""
    if target_role in mapping:
        return
    if source_role in mapping:
        mapping[target_role] = mapping[source_role]


for _rank_mapping in (
    RANK_HONORIFICS,
    TECHMARINE_RANK_ACKNOWLEDGMENTS,
    RANK_PRESTIGE_WEIGHTS,
    RANK_STUDS_COMMENTARY,
    SOK_G_PIPEHITTER_RANK_LINES,
    DISTINGUISHED_PIPEHITTER_RANK_LINES,
    BLACK_LAURELS_RANK_LINES,
    CRUX_TERMINATUS_RANK_LINES,
    KADAKU_CAMPAIGN_RANK_LINES,
    BLACK_REEF_CAMPAIGN_RANK_LINES,
    DISTINGUISHED_BLACK_REEF_RANK_LINES,
    ORDER_OMEGA_RANK_LINES,
    DUAL_VIGIL_RANK_LINES,
    TERMINUS_SLAYER_ASSAULT_RANK_LINES,
    TERMINUS_SLAYER_BULWARK_RANK_LINES,
    TERMINUS_SLAYER_HEAVY_RANK_LINES,
    TERMINUS_SLAYER_SNIPER_RANK_LINES,
    TERMINUS_SLAYER_TACTICAL_RANK_LINES,
    TERMINUS_SLAYER_TECHMARINE_RANK_LINES,
    TERMINUS_SLAYER_VANGUARD_RANK_LINES,
    MASTER_TERMINUS_SLAYER_RANK_LINES,
):
    _apply_blade_role_aliases(_rank_mapping)
    _copy_rank_fallback(_rank_mapping, target_role="Veteran Sergeant", source_role="Watch Sergeant")

