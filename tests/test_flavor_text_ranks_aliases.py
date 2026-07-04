from opscribe.flavor_text import ranks


def test_champion_track_aliases_share_honorifics_with_legacy_blade_roles():
    assert ranks.RANK_HONORIFICS["Lord Executioner"] == ranks.RANK_HONORIFICS["Blademaster"]
    assert ranks.RANK_HONORIFICS["Company Champion"] == ranks.RANK_HONORIFICS["First Blade"]
    assert ranks.RANK_HONORIFICS["Kill Team Champion"] == ranks.RANK_HONORIFICS["Bladeguard"]


def test_champion_track_aliases_share_award_flavor_with_legacy_blade_roles():
    assert ranks.BLACK_LAURELS_RANK_LINES["Lord Executioner"] == ranks.BLACK_LAURELS_RANK_LINES["Blademaster"]
    assert ranks.BLACK_LAURELS_RANK_LINES["Company Champion"] == ranks.BLACK_LAURELS_RANK_LINES["First Blade"]
    assert ranks.BLACK_LAURELS_RANK_LINES["Kill Team Champion"] == ranks.BLACK_LAURELS_RANK_LINES["Bladeguard"]
    assert ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Lord Executioner"] == ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Blademaster"]
    assert ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Company Champion"] == ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["First Blade"]
    assert ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Kill Team Champion"] == ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Bladeguard"]
