# OP-Scribe Servitor — Briefs Guide (Non-Technical)

Written for Watch Command and Kill Team sergeants who want quick, reliable insights without technical details.

## Combat Bonds — Spread and Promotion Thresholds

This project reports two new values alongside each Brother in the `combat_bonds` output:

- **Spread:** a normalized per-active-member measure of a Brother’s combat bond breadth and depth.
  - Internally computed as an inverse-Simpson (effective partners) × bounded-depth factor (sqrt by default), then normalized by the number of *active* members in the current window (active = participated in ≥1 AAR in the window). That yields a per-member average so larger fortresses don't automatically inflate raw spread values.
- **Pct:** the candidate’s percentile rank (0–100) among all Brothers in the window, computed from the normalized spread values. Higher = more competitive relative standing.

Additional metadata is returned for each Brother in the `spreads` map used by the bot:

- `raw` — the original integer spread value (legacy behavior).
- `normalized` — the per-active-member float (displayed as `Spread X.YY`).
- `percentile` — integer 0–100 representing relative rank among peers.
- `interactions` — number of partner interactions (pair frequency total) observed in the window.
- `eligible` — boolean guard indicating the Brother met the minimum interaction floor (config: `combat_bonds.min_interactions`, default 8).

Why both normalized and percentile?
- Normalized removes fortress-size bias so a spread of 2.0 means roughly the same level of breadth/depth regardless of whether the fortress has 20 or 200 active members.
- Percentile preserves the relative competitiveness within the same window: in an unusually active or unusually quiet window, a percentile check prevents promotions from being issued purely because the absolute scale shifted.

Interpreting the sample output below:

```
  Watch Veteran Hylair [Blood Angels] • Spread 0.25 (pct 31%)
  Watch Lieutenant Jack [Space Wolves] • Spread 1.14 (pct 75%)
  Watch Veteran Moloch [Minotaurs] • Spread 1.17 (pct 76%)
```

- `Hylair` has a normalized spread of 0.25 and is at the 31st percentile — modest breadth/depth and below most peers.
- `Jack` and `Moloch` have normalized spreads ≈1.14–1.17 and are in the mid-to-upper percentiles (75–76%), indicating stronger bonding across partners and volume.

Promotion thresholds (recommended)
- Use a combined rule: require BOTH a minimum `normalized` spread AND a minimum `percentile`, plus the `eligible` interaction guard.

Mapped thresholds (suggested — tune to historical data):

Battle Line Ranks
- `Watch Brother` → `Watch Veteran`:
  - `Watch Brother`: normalized ≥ 0.30 AND percentile ≥ 60% (min interactions)
  - `Watch Veteran`: normalized ≥ 0.75 AND percentile ≥ 50% (min interactions)
  - `Watch Sergeant`: normalized ≥ 1.25 AND percentile ≥ 35% (min interactions)
  - `Watch Lieutenant`: normalized ≥ 1.75 AND percentile ≥ 25% (min interactions)
  - `Watch Captain`: normalized ≥ 2.50 AND percentile ≥ 15% (min interactions)

Champion Ranks
- `Kill Team Champion`, `Company Champion`, `Lord Executioner`:
  - `Kill Team Champion`: normalized ≥ 1.00 AND percentile ≥ 45% (min interactions)
  - `Company Champion`: normalized ≥ 1.75 AND percentile ≥ 30% (min interactions)
  - `Lord Executioner`: normalized ≥ 2.50 AND percentile ≥ 12% (min interactions)

Specialist Ranks
- `Watch Chaplain`, `Watch Apothecary`, `Watch Librarian`, `Watch Techmarine`:
  - Suggested: normalized ≥ 1.50 AND percentile ≥ 30% (min interactions) plus role-specific criteria (e.g., Chaplain: oath/adherence; Apothecary: gene-seed preservation metrics).

High Command
- `Watch Master`, `High Chaplain`, `Chief Apothecary`, `Void Warden`, `Forgemaster`:
  - Suggested: normalized ≥ 3.00 AND percentile ≥ 5% (min interactions) — these are exceptional promotion bands and normally accompanied by command review.

Notes on thresholds and small forts
- If the fortress is small (active members < 12), percentiles become noisy; prefer relaxing the percentile cut or rely on percentile only.
- Always require `eligible == True` (the default min interactions = 8) to avoid promoting on sparse or bursty activity.
- These thresholds are intentionally conservative; they aim to reward breadth (many partners) and depth (repeat interactions) simultaneously.

Configuration knobs
- `combat_bonds.per_partner_cap` — caps partner frequency contribution (default 5).
- `combat_bonds.depth_exponent` — exponent on bounded depth (default 0.5, i.e., sqrt).
- `combat_bonds.min_interactions` — minimum interaction count to be considered eligible (default 8).

Want me to add a simple `is_promotion_candidate(spread_obj, rank)` helper that applies these rules and returns a boolean + which thresholds failed? I can add that to `bot.py` and include usage examples.

---

## Tally Deeds (Kill Team)

- What it shows: A compact, aligned roster of all members of the selected kill team, plus a 7‑day team summary.
- How to invoke: `/tally_deeds killteam:@Kill Team X`.
- Roster line (per brother): `Name :: Status | AAR A | Gene G | Armory R`.
  - Status: Active if any AAR in the last 28 days.
  - AAR/Gene/Armory: Lifetime tallies (commendations, gene‑seed stewardship, materiel recovery).
  - Ordering: Sergeant first, then Kill Team Champion, then Veterans, then Watch Brothers/Sisters.
- Kill Team Summary (last 7 days):
  - Veteran Lethality Index: Avg AAR
  - Operational Tempo: Ops count
  - Siegebreaker Rating: Avg Waves
  - Preservation — Gene: Average gene‑seed points
  - Preservation — Armory: Average armory points
  - Kill Team Reliability Index: Average total (AAR+Gene+Armory) ÷ (1 + dispersion)
  - Force Multiplier Rating: Avg AAR/Member

Notes:
- The roster is aligned for readability and truncated if needed to stay within Discord’s 2,000‑character limit (shows “...and N more” if many members).
- When `brother:@User` is provided instead, the detailed single‑brother Deeds Ledger is shown.
- Ordering and sorting (killteam rosters): When a role is passed (for example `@High Command`, `@Watch Command`, `@Watch Company Primus`, or any company/command role), the roster ordering still follows a canonical rank priority and additional sort keys. The ordering is:
  1. `Watch Master` (always at the top)
  2. `Lord Executioner`
  3. High‑Command specialists: `Forgemaster`, `Chief Apothecary`, `Void Warden`, `High Chaplain`
  4. `Watch Captain`
  5. `Watch Lieutenant`
  6. `Company Champion`
  7. Company specialists: `Watch Techmarine`, `Watch Apothecary`, `Watch Librarian`, `Watch Chaplain`
  8. `Watch Sergeant`
  9. `Kill Team Champion`
 10. `Watch Veteran`
 11. `Watch Brother` / `Watch Sister`

  Within each tier the roster is sorted by (descending) service studs, then (descending) AAR points, then name. Members with no recent activity are placed after active members (inactive members are pushed to the bottom).

- Permissions note: The `/set_rite` and `/forge_rite` commands are restricted to users holding the Techmarine or Forgemaster roles. Additionally, those commands are not usable in the `❖⋅data-vault⋅❖` channel.
