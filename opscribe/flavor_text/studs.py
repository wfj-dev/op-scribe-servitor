"""Service studs announcement data."""

from typing import Dict, List  # noqa: F401

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

