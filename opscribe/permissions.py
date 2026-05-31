"""Permission track data tables extracted from bot.py.

Three rank tracks (Battle Line, Champion, Specialist), High Command, and
Watch Command convenience groups. Pure literal data only — no runtime
dependencies. bot.py re-exports everything via ``from permissions import *``
so existing references and tests keep working unchanged.
"""

# ============================================================================
# PERMISSION TRACKS
# ============================================================================
# Three tracks exist:
#   1. Battle Line: Watch Brother → Watch Veteran → Oathsworn → Watch Sergeant
#                   → Watch Lieutenant → Watch Captain
#   2. Champion: Kill Team Champion → Company Champion → Lord Executioner
#   3. Specialist (4 sub-tracks, each leading to High Command):
#        Chaplain → High Chaplain, Apothecary → Chief Apothecary,
#        Librarian → Void Warden, Techmarine → Forgemaster
#
# High Command = senior specialists (High Chaplain, Chief Apothecary, Void Warden,
#                Forgemaster) + Watch Master
# Watch Master is at the top of ALL tracks.
# ============================================================================

# Battle line ranks (linear progression)
BATTLE_LINE_TRACK = {
    "Watch Brother": {
        "Watch Brother",
        "Watch Veteran",
        "Oathsworn",
        "Watch Sergeant",
        "Watch Lieutenant",
        "Watch Captain",
    },
    "Watch Veteran": {
        "Watch Veteran",
        "Oathsworn",
        "Watch Sergeant",
        "Watch Lieutenant",
        "Watch Captain",
    },
    "Oathsworn": {"Oathsworn", "Watch Sergeant", "Watch Lieutenant", "Watch Captain"},
    "Watch Sergeant": {"Watch Sergeant", "Watch Lieutenant", "Watch Captain"},
    "Watch Lieutenant": {"Watch Lieutenant", "Watch Captain"},
    "Watch Captain": {"Watch Captain"},
}
BATTLE_LINE_RANKS = {
    "Watch Brother",
    "Watch Veteran",
    "Oathsworn",
    "Watch Sergeant",
    "Watch Lieutenant",
    "Watch Captain",
}

# Champion track (linear progression)
CHAMPION_TRACK = {
    "Kill Team Champion": {
        "Kill Team Champion",
        "Company Champion",
        "Lord Executioner",
    },
    "Company Champion": {"Company Champion", "Lord Executioner"},
    "Lord Executioner": {"Lord Executioner"},
}
CHAMPION_RANKS = {"Kill Team Champion", "Company Champion", "Lord Executioner"}

# Specialist tracks: each sub-track is independent, leads to High Command
SPECIALIST_TRACKS = {
    "Watch Techmarine": {"Watch Techmarine", "Forgemaster"},
    "Forgemaster": {"Forgemaster"},
    "Watch Librarian": {"Watch Librarian", "Void Warden"},
    "Void Warden": {"Void Warden"},
    "Watch Chaplain": {"Watch Chaplain", "High Chaplain"},
    "High Chaplain": {"High Chaplain"},
    "Watch Apothecary": {"Watch Apothecary", "Chief Apothecary"},
    "Chief Apothecary": {"Chief Apothecary"},
    "Watch Keeper": {"Watch Keeper", "Castellan"},
    "Castellan": {"Castellan"},
}
SPECIALIST_RANKS = set(SPECIALIST_TRACKS.keys())

# High Command (senior specialists + Watch Master)
HIGH_COMMAND_RANKS = {
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
    "Castellan",
    "Watch Master",
    "Venerable Dreadnought",
    "Watch Captain",
    "Lord Executioner",
    "Huntmaster",
}

# Watch Command = Sergeant+ from Battle Line, Company Champion+ from champion track, all Specialists, High Command
# This is a convenience group for "everyone who isn't a line brother"
WATCH_COMMAND_ROLES = {
    # Battle Line (Sergeant+)
    "Watch Sergeant",
    "Watch Lieutenant",
    "Watch Captain",
    # Champion track (Company Champion+)
    "Company Champion",
    "Lord Executioner",
    "Huntmaster",
    # Specialist track (all)
    "Watch Chaplain",
    "Watch Apothecary",
    "Watch Librarian",
    "Watch Techmarine",
    "Watch Keeper",
    # High Command
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
    "Castellan",
    "Watch Master",
    "Venerable Dreadnought",
    "Honored Dreadnought",
}
