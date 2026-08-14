"""Award announcement openings and proclamations (non-chapter-keyed)."""

from typing import Dict, List  # noqa: F401

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

BLACK_LAURELS_OPENINGS: List[str] = [
    "**{name}** has placed the Kill Team above all notion of personal glory — and the Watch sees it.",
    "Where lesser warriors seek their own legend, **{name}** has built the legend of their brothers.",
    "**{name}** has shown, time and again, that the strength of the Kill Team is the only strength that matters.",
    "The Tyranid plague devours all — except where warriors like **{name}** stand the line together.",
    "**{name}** has been forged in the crucible of battle, and emerged not as a hero, but as a brother of brothers.",
]

BLACK_LAURELS_PROCLAMATIONS: List[str] = [
    "The Black Laurels are awarded to those who place the success of the Kill Team above any notion of personal glory — and the Watch sets that devotion in adamantium.",
    "The Tyranid plague spreads ever deeper into the galaxy. The Deathwatch Kill Team is the first line of defense — and this warrior is one of its truest sons.",
    "Each Kill Team is a force multiplier greater than the sum of its parts. This warrior is what makes that mathematics possible — and the Black Laurels are the Watch's recognition of it.",
    "Forged from bonds built through battle, this warrior has put the team before themselves until that bond became unbreakable — and the Watch has honored that bond with wargear worthy of it.",
    "The Black Laurels are not earned by the warrior who claims the killing blow — they are earned by the warrior who ensured every brother was still standing when the last blow fell.",
]

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
    "Of all the warriors of Watch Fortress Jericho, only the few who have bled on the hardest fronts the Long Watch can offer may wear the Crux. This warrior is one of them.",
]

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

BLACK_REEF_CAMPAIGN_OPENINGS: List[str] = [
    "**{name}** has answered every call of the Black Reef Persecution — and the heretic finds no harbor where this warrior treads.",
    "From the first incursion to the final accounting, **{name}** has seen the Black Reef Persecution through to its end.",
    "The Black Reef Persecution offered no easy operation; **{name}** completed them all regardless.",
    "**{name}** has earned the Black Reef Campaign Medal through persistence no lesser warrior could match.",
    "Every operation, every void-stretch, every cleansing — **{name}** stood among the warriors who saw the Black Reef Persecution through.",
]

BLACK_REEF_CAMPAIGN_PROCLAMATIONS: List[str] = [
    "The Black Reef Campaign Medal is bestowed upon those who completed every operation of the Black Reef Persecution.",
    "The Black Reef's foes were many, varied, and never easy — yet this warrior addressed each in turn, and saw each undone.",
    "The Persecution upon the Black Reef was no swift victory; it was a campaign of patience, of attrition, and of unbroken faith.",
    "To bear the Black Reef Campaign Medal is to have walked the void-fronts of the Reef from first incursion to final accounting.",
    "This warrior did not pick their battles upon the Black Reef — they answered every call, and the Watch records every one.",
]

DISTINGUISHED_BLACK_REEF_OPENINGS: List[str] = [
    "**{name}** has marched the Black Reef Persecution as a Kill Team's heart — every operation, every bond, every brother.",
    "The Black Reef's foes met not one warrior in **{name}** — they met a Kill Team in which **{name}** was every brother's strength.",
    "**{name}** carried every operation of the Black Reef Persecution as a Kill Team's strength — a warrior who refused to fight alone.",
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

ORDER_OMEGA_OPENINGS: List[str] = [
    "**{name}** has walked through the hardest fronts the Watch could offer and emerged with every brother still standing — the Watch knows few who can claim the same.",
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
    "This warrior has done what the Watch did not believe possible — at the peak of Omega difficulty, without fail, without retreat. The Order Omega opens to them now.",
]

OCTAVIAN_OPERATION_OPENINGS: List[str] = [
    "**{name}** answered the vox-call from Octavian and carried the campaign through every required front.",
    "The Octavian Incident trapped Imperial, Traitor, and Tyranid alike — **{name}** was among those who endured every ordered strike.",
    "From Reliquary to Purgation, **{name}** has seen the Octavian Campaign through in full.",
    "When the Shadow in the Warp silenced all but one command, **{name}** obeyed it to the last operation.",
    "**{name}** has completed the Octavian operation set beneath the only directive that reached the system: suffer not the alien to live.",
]

OCTAVIAN_OPERATION_PROCLAMATIONS: List[str] = [
    "The Octavian Operation Medal is bestowed upon those who completed every required operation of The Octavian Incident campaign.",
    "The Octavian Incident marked the opening of a campaign fought in isolation, beneath the Tyranid Shadow in the Warp and against every force trapped within the system.",
    "When Octavian became the crucible of the campaign, this warrior answered every required operation and brought each to completion.",
    "To bear the Octavian Operation Medal is to have stood through the campaign's ordered strikes from first deployment to final accounting.",
    "The sealed history of the Helfrost Crystal remains for the Ordo Xenos. The record of this warrior's service on Octavian remains for the Watch.",
]

DISTINGUISHED_OCTAVIAN_OPERATION_OPENINGS: List[str] = [
    "**{name}** completed every required Octavian operation without a single brother falling incapacitated.",
    "The Octavian Campaign exacted a price from lesser Kill Teams. **{name}** brought every required strike to completion cleanly.",
    "Where others endured Octavian by attrition, **{name}** completed the entire required campaign with every brother still battle-ready.",
    "The Distinguished mark is reserved for warriors who answered Octavian's calls without yielding a single incapacitation. **{name}** has done so.",
    "**{name}** earned the deeper honor of Octavian by pairing total campaign completion with flawless Kill Team preservation.",
]

DISTINGUISHED_OCTAVIAN_OPERATION_PROCLAMATIONS: List[str] = [
    "The Distinguished Octavian Operation Medal honors those who completed every required Octavian operation with Black Laurels discipline.",
    "At Octavian, clean victories mattered as much as hard-won ones. This warrior achieved the former across the full required campaign set.",
    "To complete the Octavian Incident is one honor. To complete it without a single incapacitation across every required operation is another entirely.",
    "This distinction records more than survival. It records mastery of the campaign's full operation set while preserving every brother through each deployment.",
    "The Watch remembers not only that this warrior answered Octavian's call, but that they did so without allowing a brother to fall.",
]

DUAL_VIGIL_OPENINGS: List[str] = [
    "**{name}** has proven that two brothers, bound by purpose, can hold the line as surely as any full fireteam.",
    "Two warriors. Every mission. No retreat. **{name}** has demonstrated what it means to place absolute trust in a single brother.",
    "The Watch does not always measure strength in numbers. **{name}** has carried the hardest operations at the absolute edge — and earned what few can claim.",
    "**{name}** has stood watch with a single brother at their side across the Watch's most demanding fronts. That is not fortune. That is discipline.",
    "There are warriors who fight in the shadow of a full Kill Team. Then there are warriors like **{name}** — who go to the absolute edge with one brother, and come back.",
    "**{name}** walked the hardest fronts in the tightest formation the Watch recognises — two, together, absolute. The Order of the Aquiline Brotherhood is earned.",
    "Two brothers. All missions. **{name}** has borne every demand without compromise — the Order of the Aquiline Brotherhood is theirs.",
]

DUAL_VIGIL_PROCLAMATIONS: List[str] = [
    "The Order of the Aquiline Brotherhood is awarded to those who faced the Watch's most demanding battles at Absolute difficulty with exactly one brother at their side.",
    "The Watch recognises a singular depth of trust: the warrior who chose, every time, to fight the hardest operations with one brother instead of three.",
    "To earn the Order of the Aquiline Brotherhood is to have proven that two brothers, operating as one, can face the Watch's most demanding missions and prevail.",
    "Not every warrior finds a brother they would take to the edge of annihilation. **{name}** has done exactly that — and the Watch records it accordingly.",
    "The Order of the Aquiline Brotherhood is not given lightly. It is the mark of brothers who treated every absolute operation as a test of what two warriors in complete trust can achieve.",
    "Where others demand a full fireteam, the pair who earned this order demanded only each other — and answered everything the Watch put before them.",
    "Two warriors who refused to be separated by difficulty, by odds, or by the weight of the mission. The Order of the Aquiline Brotherhood is the Watch's answer.",
]

TERMINUS_SLAYER_ASSAULT_OPENINGS: List[str] = [
    "\"Cover me brothers — this one is mine.\" **{name}** said it. **{name}** meant it. The killing blow has been verified.",
    "The jump pack roared. The blade fell. **{name}** has taken the Terminus threat to its conclusion — close, loud, and final.",
    "**{name}** crossed the battlefield in seconds and delivered the killing blow. For a warrior beneath the jump pack, there is only one answer: straight at it.",
    "Three classes of Terminus threat. Nine verified kills. **{name}** went in under jump pack thrust and came back through every one.",
    "The warrior beneath the jump pack was forged for moments exactly like this — **{name}** has proven it nine times over at the highest threat level the Watch logs.",
]

TERMINUS_SLAYER_ASSAULT_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those who have proven, nine times in verified combat, that the close-quarters charge is the truest answer to every class of Terminus threat.",
    "To close on a Terminus-level enemy under jump pack thrust, alone in purpose if not alone on the field, is the close-quarters warrior's highest expression. **{name}** has expressed it nine times.",
    "Where others engage from range or behind shields, the warrior beneath the jump pack commits everything to the strike. The Terminus Slayer mark is the Watch's recognition of that commitment carried through.",
    "Nine verified kills. Three Terminus classes. The answer — the jump pack, the charge, the blade — proven against every threat the Watch tracks.",
    "This mark honours a warrior who treated every Terminus threat as an opportunity to demonstrate what close-quarters commitment achieves.",
]

TERMINUS_SLAYER_BULWARK_OPENINGS: List[str] = [
    "The banner rose. The shield held. And then **{name}** stepped out from behind it and ended the Terminus threat personally.",
    "**{name}** raised the shield, dropped the banner's benefit on waiting brothers, and then walked through whatever was left to finish the Terminus target alone.",
    "\"Cover me brothers — this one is mine.\" From a Bulwark. Nine times. **{name}** has proven that a shield does not only absorb — it enables.",
    "The Bulwark is designed to absorb and support. **{name}** used both, and then turned that platform into nine confirmed Terminus kills.",
    "Nine Terminus kills. A shield. A banner. And a warrior who used both as a launching pad rather than a shelter. That is **{name}**'s Terminus Slayer record.",
]

TERMINUS_SLAYER_BULWARK_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those whose shield has faced every class of Terminus threat — and whose blade has answered it, nine times verified.",
    "The shield-bearer fights for others' survival. The Terminus Slayer mark recognises the warrior who turned that survival into a weapon, nine Terminus threats confirmed.",
    "To plant a banner, absorb the Terminus threat's attention, and then close to deliver the killing blow is the Bulwark's singular expression of 'this one is mine.' **{name}** has done it nine times.",
    "Nine verified kills across all three Terminus classes. A shield that protected and a blade that ended. This mark is the Watch's highest recognition of that combination.",
    "This mark honours the warrior who gave their brothers every possible advantage — and then used that advantage to make nine Terminus kills of their own.",
]

TERMINUS_SLAYER_HEAVY_OPENINGS: List[str] = [
    "The barrier locked. The heavy weapon levelled. And **{name}** made nine Terminus-level threats understand what crowd control means at the highest possible target.",
    "\"Cover me brothers — this one is mine.\" From a warrior carrying weaponry designed for whole armies. **{name}** applied it to nine Terminus targets specifically.",
    "**{name}** brought the heaviest weapon in the Watch's standard toolkit to bear on nine Terminus threats and verified every kill.",
    "The Heavy is the Watch's powerhouse. **{name}** has turned that power toward nine Terminus-level enemies and stood behind verified results every time.",
    "Nine Terminus kills. Heavy weaponry. A barrier that protected long enough for **{name}** to ensure every shot counted.",
]

TERMINUS_SLAYER_HEAVY_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those who brought heavy weaponry to bear on every class of Terminus threat — nine verified kills, three classes, one warrior.",
    "Heavy weapons exist to dominate the battlefield. The Terminus Slayer mark recognises the warrior who directed that domination at nine Terminus-level threats specifically.",
    "To place a barrier, secure a firing position, and personally eliminate nine Terminus-class enemies is a heavy weapon warrior's 'cover me brothers' — volume, accuracy, and finality.",
    "Nine verified kills across all three Terminus types. The barrier held, the weapon spoke, and **{name}** recorded the results. This mark is that record's highest seal.",
    "This mark honours the warrior who applied the Watch's most powerful standard weaponry to the Watch's most dangerous standard targets — nine times, each verified.",
]

TERMINUS_SLAYER_SNIPER_OPENINGS: List[str] = [
    "The cloak dropped. The shot arrived before the Terminus threat knew its hunter. **{name}** has done this nine times.",
    "\"Cover me brothers — this one is mine.\" From a distance most warriors cannot reach, **{name}** made nine Terminus threats into confirmed kills.",
    "**{name}** located the high ground, found the angle, cloaked when necessary, and precisely eliminated nine Terminus-level threats across three classes.",
    "Distance is not a weakness for the long-range warrior — it is the weapon. **{name}** wielded it against nine Terminus threats and left nine verified kills in the record.",
    "Nine kills. Three Terminus types. One warrior who never needed to be close to be lethal. **{name}** has made that truth official.",
]

TERMINUS_SLAYER_SNIPER_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those who eliminated every class of Terminus threat from range — nine verified kills, each precise, each final.",
    "The marksman is built on precision and distance. The Terminus Slayer mark recognises the warrior who applied that precision to nine Terminus-level threats at range.",
    "To cloak, to aim, to fire at the exact point a Terminus threat is vulnerable — that is the marksman's 'cover me brothers.' **{name}** has said it nine times in action.",
    "Nine verified kills across all three Terminus classes. No close engagement required. **{name}** ended nine Terminus threats from exactly the distance the marksman was built to operate.",
    "This mark honours the warrior who removed the most dangerous enemy on the battlefield from the position their enemy least expected — nine times, precisely, finally.",
]

TERMINUS_SLAYER_TACTICAL_OPENINGS: List[str] = [
    "The auspex scan marked the weakpoint. The weapon was already aimed. **{name}** eliminated nine Terminus threats before the rest of the battlefield had finished reacting.",
    "\"Cover me brothers — this one is mine.\" The Tactical says it with every weapon option available to them. **{name}** exercised every option nine times at Terminus level.",
    "**{name}** demonstrated nine times why the Tactical's versatility is as lethal against elite targets as any specialist doctrine.",
    "Auspex scan, weakpoint identified, weapon applied. **{name}** ran this sequence nine times against the Watch's highest-tier individual threats and produced nine confirmed kills.",
    "Nine Terminus kills. The Tactical's full arsenal. **{name}** has proven that all-round capability is not a compromise — it is a threat to everything, including Terminus-class enemies.",
]

TERMINUS_SLAYER_TACTICAL_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those versatile enough to find the correct approach to every class of Terminus threat — nine verified kills, three classes, one adaptable warrior.",
    "The versatile warrior carries no single doctrinal identity. The Terminus Slayer mark recognises the warrior who applied every identity to nine Terminus-level threats and produced nine kills.",
    "To scan the enemy, identify the vulnerability, and select the correct weapon for the moment — then to do it nine times at Terminus level — that is the versatile warrior's ultimate expression.",
    "Nine verified kills. Three Terminus types. The Tactical's answer was different every time and final every time. **{name}** has earned the Watch's recognition of that adaptability.",
    "This mark is the Watch's acknowledgement that versatility, in the hands of a warrior like **{name}**, is as deadly as any specialist's precision.",
]

TERMINUS_SLAYER_TECHMARINE_OPENINGS: List[str] = [
    "The Tarantula Sentry Guns locked target. The servo gun calculated trajectory. **{name}** made nine Terminus threats understand that the battlefield itself was the weapon.",
    "\"Cover me brothers — this one is mine.\" **{name}** deployed the battlefield, activated the machinery, and watched nine Terminus-level threats fall into the kill zone.",
    "**{name}** converted the engagement zone into a mechanism for killing Terminus threats — nine times, verified, logged in the Omnissiah's own accounting.",
    "The Techmarine does not merely fight — they engineer the conditions for victory. **{name}** engineered nine Terminus kills and submits the record to the Omnissiah's attention.",
    "Nine Terminus kills. Servo gun. Tarantula Sentry. **{name}** proved that zone control at Terminus level is not passive. It is precision destruction.",
]

TERMINUS_SLAYER_TECHMARINE_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those who applied the battlefield's machinery to the Watch's most dangerous individual targets — nine verified kills across all three Terminus classes.",
    "The zone-controller commands the battlefield. The Terminus Slayer mark recognises the warrior who made that zone lethal for nine Terminus-level threats specifically.",
    "To position the Tarantula, to level the servo gun, to make the battlefield itself an engine of elimination — that is the Techmarine's 'this one is mine.' **{name}** has said it nine times.",
    "Nine verified kills. Each one a product of preparation, positioning, and machine-spirit cooperation. **{name}** has earned this mark and the Omnissiah's recognition.",
    "This mark honours the warrior who treated every Terminus threat as an engineering problem — and solved it nine times, to the Omnissiah's eternal satisfaction.",
]

TERMINUS_SLAYER_VANGUARD_OPENINGS: List[str] = [
    "The grapnel launched. The distance closed in an instant. **{name}** was already inside the Terminus threat's reach before it could respond — nine times.",
    "\"Cover me brothers — this one is mine.\" From a warrior who could cross any battlefield gap in moments, **{name}** chose to close nine Terminus threats personally.",
    "**{name}** used the grapnel launcher not to escape but to arrive — at nine Terminus-level threats, at close quarters, at the moment of maximum danger.",
    "High mobility. Close-quarters weapons. Nine Terminus kills. **{name}** has built a Terminus Slayer record that demonstrates exactly what the grapnel-launched warrior was designed to achieve.",
    "Nine times the grapnel found the angle. Nine times **{name}** arrived where a Terminus threat least expected a warrior. Nine times the encounter was one-sided.",
]

TERMINUS_SLAYER_VANGUARD_PROCLAMATIONS: List[str] = [
    "The Terminus Slayer mark is awarded to those who brought close-quarters mobility to bear on every class of Terminus threat — nine verified kills, each faster than the enemy expected.",
    "The grapnel-launched warrior is built on mobility and aggression. The Terminus Slayer mark recognises the warrior who applied both to nine Terminus-level threats and returned nine kills.",
    "To grapnel into engagement range, to close where others would hold back, and to end the Terminus threat at close quarters — that is the Vanguard's 'this one is mine.' **{name}** has done it nine times.",
    "Nine verified kills across all three Terminus types. The grapnel found a line to every one of them. **{name}** has earned this mark on the simplest terms: high speed, close weapons, confirmed results.",
    "This mark honours the warrior who treated every Terminus threat as a destination — arrived fast, engaged close, and verified nine kills as the outcome.",
]

MASTER_TERMINUS_SLAYER_OPENINGS: List[str] = [
    "Every class. Every Terminus type. Every kill verified. **{name}** has completed the most comprehensive personal Terminus Slayer record the Watch logs.",
    "Seven classes. Sixty-three verified kills. Three Terminus types faced in every configuration the Watch tracks. **{name}** has earned the title that fewer than anyone expected would ever be claimed.",
    "\"Cover me brothers — this one is mine.\" **{name}** said it beneath a jump pack. Behind a shield. Behind a barrier. Through a scope. With every weapon available. At zone-control range. From a grapnel line. All nine types, all seven times, all verified.",
    "There is a word for what **{name}** has done — the Watch's kill logs contain seven class completions, all Terminus types, all verification records intact. The word is Master.",
    "The Master Terminus Slayer is not given to warriors who merely fought well. It is given to **{name}**, who fought well in every class, against every Terminus threat, and produced the verified record for all of it.",
]

MASTER_TERMINUS_SLAYER_PROCLAMATIONS: List[str] = [
    "The Master Terminus Slayer is the Watch's highest individual combat honour — awarded only to those whose verified kill records span every class, every Terminus type, every configuration the Long Watch has faced.",
    "To complete the Terminus Slayer challenge in one class is remarkable. To complete it in all seven is something the Watch's records have rarely if ever required an entry for. **{name}** has required one.",
    "Seven class completions. Every Terminus type met in every configuration. The Master Terminus Slayer title is not a rank — it is a comprehensive record that speaks every language of combat the Watch teaches.",
    "The Watch teaches versatility by principle. The Master Terminus Slayer proves versatility in practice — every class deployed, every Terminus threat eliminated, every kill logged and verified.",
    "The Master Terminus Slayer is the Watch's recognition that **{name}** has not found a preferred approach to Terminus-level threats. They have mastered every approach.",
    "Where most warriors find their class and their method, **{name}** found the answer to a question the Watch rarely asks: what does a warrior look like who has done it all? This. This is what it looks like.",
]

