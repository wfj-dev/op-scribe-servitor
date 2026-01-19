# OP-Scribe Servitor — Briefs Guide (Non-Technical)

This guide explains the five “briefs” the bot can generate and how to read the metrics in plain language. It’s written for Watch Command and Kill Team sergeants who want quick, reliable insights without technical details.

## How to Use This Guide
- Each brief summarizes a different aspect of operations.
- For every brief, we list what it shows, the key lines you’ll see, and how to interpret the values and labels.
- Loreful labels are intentional: they map to posture and doctrine without exposing raw internal numbers.

---

## Command Brief (Company Overview)

- **What it shows:** Each team’s posture across Aggression (lethality) and Preservation* (stewardship), plus overall efficiency.
- **Key lines:**
  - **Risk Appetite — KT X :: Label (deltas & index)**
  - **Force Multiplier Rating :: KT X (Avg AAR/Member)**
  - **High Command Notes :: Oversight vs elevated tempo (qualitative)**
- **How to read it:**
  - **Veteran Lethality Index:** Higher means more lethal operations.
  - **Preservation*:** Balance of gene-seed handling and materiel recovery.
  - **Risk Appetite label examples:**
    - **Orthodox:** Near the company’s typical posture.
    - **Sanctifier:** High stewardship, restrained aggression.
    - **Purgator:** High aggression, restrained stewardship.
  - **Force Multiplier:** Bigger means the team gets more outcome per marine. ~0.8–1.2 steady; ≥1.5 often means strong coordination.
  - **High Command Notes:** Narrative-only guidance on oversight vs direct battlefield presence.

---

## Techmarine Brief (Materiel Recovery)

- **What it shows:** Typical armory recovery per team, how often high-value salvage occurs, and how effective recovery is under risk.
- **Key lines:**
  - **Typical Salvage Yield :: Median recovery, difficulty-aware**
  - **High-Value Salvage Frequency :: Share of top-quartile recoveries**
  - **Risk-Adjusted Yield :: Bonus points by mission class**
  - **High Command Notes :: Stewardship vs targeted interventions**
- **How to read it:**
  - **Typical Yield:** Steady stewardship when the median stays solid over time.
  - **High-Value Frequency:**
    - **Frequent:** ≳ 35% of ops yield top-tier salvage.
    - **Rare:** ≲ 15% of ops do.
  - **Risk-Adjusted Yield:** Compares recovery effectiveness in harder mission modes (OPS/Stratagem/Siege).
  - **Notes:** Zero-ops → audits/upkeep; limited but high yield → targeted interventions; high tempo with modest yields → risk accepted under exigency.

---

## Librarian Brief (Operations & Doctrine)

- **What it shows:** Where you fought (environments), which doctrine patterns you used, how broad and repetitive operations were, and how focused doctrine is.
- **Key lines:**
  - **Operational Environments :: Top-3 theatres by share**
  - **Doctrinal Pattern :: Top-3 doctrine tags by count**
  - **Operational Saturation :: Tier (Team Coverage & Replay Rate)**
  - **Operational Equilibrium :: Band (CV) — Top Missions: INF (33%) BE (22%) VL (18%)**
  - **Cohesion Trend :: Balanced/Leaning/Focused/Orthodox/Monolithic**
  - **Doctrine Diversity :: Eclectic/Mixed/Concentrated/Dominant**
  - **Doctrinal Divergence :: Team(s) with the strongest single-doctrine emphasis**
- **How to read it:**
  - **Environments:** Theatres (e.g., INDUSTRIAL, URBAN) by share.
  - **Patterns:** Which doctrine families dominated.
  - **Operational Saturation:**
    - **Team Coverage:** Fraction of company teams (incl. Company Command) that engaged in the window.
    - **Replay Rate:** Average runs per mission type (1.2–1.6 moderate; ≥2.0 high repetition).
  - **Operational Equilibrium (balance):**
    - **CV bands:** Balanced (≤0.25), Mixed (0.25–0.50), Skewed (>0.50).
    - **Top Missions:** Abbreviated for clarity — e.g., INF (Inferno), BE (Ballistic Engine), VL (Vox Liberatis).
  - **Cohesion Trend (focus):** Top doctrine share:
    - **Balanced (≤35%)** → broad doctrine spread.
    - **Leaning (35–50%)** → one doctrine is often preferred.
    - **Focused (50–65%)** → strong preference.
    - **Orthodox (65–80%)** → very strong preference.
    - **Monolithic (>80%)** → overwhelming focus on a single doctrine.
  - **Doctrine Diversity (spread):** HHI bands:
    - **Eclectic (≤0.20)** → very diverse.
    - **Mixed (≤0.35)** → diverse but with notable focus.
    - **Concentrated (≤0.50)** → focused on fewer doctrines.
    - **Dominant (>0.50)** → one/few doctrines dominate.
  - **Divergence:** Highlights teams leaning most into a single doctrine, especially if different from the company’s overall top.

- **Mission Abbreviations:**
  - **INF** Inferno, **DEC** Decapitation, **VL** Vox Liberatis, **REL** Reliquary,
  - **FOA** Fall of Atreus, **BE** Ballistic Engine, **TER** Termination,
  - **OBL** Obelisk, **EXF** Exfiltration, **VTX** Vortex, **REC** Reclamation,
  - **SGE** Siege.

---

## Apothecary Brief (Readiness & Stability)

- **What it shows:** Team readiness, stability/cohesion, care load, CC status, gene-seed stewardship, and initiation leadership.
- **Key lines:**
  - **Readiness / Stability / Care :: Qualitative tiers**
  - **Company Command Status :: READINESS — STABILITY**
  - **Gene-Seed Preservation :: Team with strongest stewardship**
  - **Initiation Rites Leadership :: Teams with highest average inductions**
  - **High Command Notes :: Green posture, mixed signals, emergency footing**
- **How to read it:**
  - **Readiness/Stability/Care:** Narrative tiers from availability and recovery signals.
  - **CC Status:** Snapshot for Company Command.
  - **Gene-Seed Preservation:** Strong averages (≈1.2–1.6) are steady; ≥2.0 exceptional.
  - **Initiation Leadership:** Siege trials count immediately; Ops require three trials per induction.
  - **Notes:** 
    - **Green posture:** Favorable signals; forward drills at discretion.
    - **Mixed signals:** Targeted remediation; avoid broad escalation.
    - **Emergency footing:** Elevated tempo with degraded readiness.

---

## Chaplain Brief (Discipline & Oaths)

- **What it shows:** Team discipline status from challenge progression and leadership, oath adherence, and discipline posture for High Command.
- **Key lines:**
  - **Discipline Status :: Exemplaris/Stalwart/Steadfast/Liturgical Correction Required/Discipline Derelict**
  - **Oath Adherence :: % with counts (fulfilled/unfulfilled/unclassified)**
  - **Challenge Compliance :: Team average across challenge roles**
  - **High Command Notes :: Discipline posture**
- **How to read it:**
  - **Discipline Status:**
    - **Requires leadership:** Kill Teams need at least a Sergeant (not also LT/Capt); Company Command needs a Captain.
    - **Top tiers:** Achievable either by outperforming peers or meeting a high absolute bar.
    - **Loreful case:** All Kill Teams can be **Exemplaris** at once if leadership is present and progression is high across the board.
  - **Oath Adherence:** Window snapshot; informs counsel but doesn’t directly affect tiering.
  - **Challenge Compliance:** Average progression rate across challenge roles; used relatively (vs peers) with absolute floors.
  - **Notes:** Exemplar discipline, measured progression, or edicts predominating.

---

## High Command Brief (Strategic Company Summary)

- **What it shows:** A concise, High Command–facing dashboard summarizing where HC deployed and the company-level health signals: apothecarion readiness (gene/induction), chaplaincy discipline (avg tier), Librarius doctrine posture (cohesion & top theatres), and Mechanicus armory priority.
- **Key lines:**
  - **Deployment Distribution ::** compact list of companies with ops and percent of HC-attended missions.
  - **Apothecarion Readiness Index :: NAME (Score)** — combined gene recovery + initiation efficiency (0.0–1.0). Higher is better.
  - **Chaplaincy — Avg Discipline :: NAME TIER (score)** — company average discipline (uppercase tier and numeric 1–5 average).
  - **Librarius — Cohesion :: TIER (TopDoc XX%)** — qualitative cohesion band and dominant doctrine share.
  - **Librarius — Top Environments ::** macro theatres (JUNGLE, URBAN, INDUSTRIAL, etc.) by share.
  - **Mechanicus Yield Priority :: NAME (Avg Armory: X.YZ)** — company prioritized for armory inspection.
- **Plain-language guide:**
  - **Apothecarion Readiness:** GOOD≥0.75 | FAIR≥0.50 | POOR≥0.25 | CRITICAL<0.25 — higher = healthier seed/induction signals.
  - **Chaplain Discipline (tiers):** EXEMPLARIS / STALWART / STEADFAST / LITURGICAL CORRECTION REQUIRED / DISCIPLINE DERELICT — higher = better discipline; numeric averages (1–5) offer quick ranking.
  - **Librarius Cohesion:** BALANCED / LEANING / FOCUSED / ORTHODOX / MONOLITHIC — BALANCED = mixed tactics; MONOLITHIC = doctrine concentrated.
  - **Deployment Distribution:** the percent is HC bandwidth per company; a high single-company percent implies concentrated oversight and possible redistribution.

**How to act on it:** use the brief's bottom notes (synthesized counsel) for immediate action: redistribute oversight, request Chaplain visits, or schedule Mechanicus inspections. The lines are intentionally short and human-centered so non-technical High Command can act quickly.

## Reading Labels at a Glance
- **Orthodox (Command):** Near typical posture; measured aggression and stewardship.
- **Sanctifier / Purgator (Command):** Highlights stewardship-first vs aggression-first postures.
- **Balanced / Leaning / Focused / Orthodox / Monolithic (Librarian):** Doctrine concentration levels.
- **Eclectic / Mixed / Concentrated / Dominant (Librarian):** Doctrine spread across tags.
- **Balanced / Mixed / Skewed (Librarian):** How evenly missions were replayed.
- **Steady / Variable / Fraught (Apothecary-like language):** Readiness and stability feel.
- **Exemplaris / Stalwart / Steadfast (Chaplain):** Discipline banding by leadership + progression.

---

## Tips
- **Look for patterns, not one-offs:** The briefs summarize a window. Sustained trends matter more than a single engagement.
- **Use labels as signals:** Loreful labels are calibrated to reflect posture without drowning you in numbers.
- **Cross-reference:** E.g., high **Operational Saturation** with **Skewed Equilibrium** suggests heavy repetition in a few missions.

If you want screenshots or examples added, we can include a short "Sample Output" section next.

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

## Doctrine & Mission Tags (Context)

Doctrine in these briefs is a house-developed taxonomy — our way of describing operational patterns consistently across missions. Each mission in the catalog carries two kinds of tags:

- Environment tags: the theatres you fight in (e.g., URBAN, INDUSTRIAL, JUNGLE). The bot also groups similar environments into macro-categories so the “Operational Environments” line is easy to scan.
- Doctrine tags: the pattern of action used (e.g., Sabotage, Extraction, Assassination). These power the “Doctrinal Pattern,” “Cohesion Trend,” “Doctrine Diversity,” and “Doctrinal Divergence” lines in the Librarian Brief.

What this means in practice:
- A single engagement can carry multiple doctrine tags. Over a window, counts show which patterns are most common.
- “Cohesion Trend” looks at how concentrated doctrine use is (the share of the single most-used doctrine). Low share → broad, flexible posture; high share → a focused or orthodox posture.
- “Doctrine Diversity” uses a diversity index (HHI) to summarize how spread-out doctrine usage is overall. Lower = more varied.
- “Doctrinal Divergence” highlights team(s) leaning hardest into a single doctrine pattern (especially if different from the company’s overall top).

Special handling and quality-of-life:
- Siege missions are treated specially: they always include doctrinal tags like Hold and Attrition to reflect sustained defenses and grinding engagements.
- Mission names are normalized and fuzzy-matched, so minor typos still map to the right catalog entry and its tags.
- Environment tags are mapped to macro categories (e.g., multiple urban-like locations roll up under URBAN) so the top-3 theatres are meaningful at a glance.

Examples (mission → doctrine tags):
- Inferno → Sabotage, Perimeter Strike, Extraction
- Decapitation → Assassination, Target Elimination
- Vox Liberatis → Communications, Heretic Purge
- Reliquary → Beacon Destruction, Infiltration
- Fall of Atreus → Advance & Prepare, Securement
- Ballistic Engine → Weapon Delivery, Sabotage
- Termination → Extermination, Area Clearing
- Obelisk → Objective Disruption, Dark Labyrinth
- Exfiltration → Extraction, Break Contact
- Vortex → Containment, Ritual Disruption
- Reclamation → Asset Recovery, Area Securement
- Siege (mode) → Hold, Attrition

How to interpret doctrine lines together:
- Doctrinal Pattern (top-3 counts) tells you “what we did most.”
- Cohesion Trend (top share band) tells you “how focused we were on one pattern.”
- Doctrine Diversity (HHI band) tells you “how varied the playbook was overall.”
- Doctrinal Divergence tells you “who leaned hardest into a single pattern.”

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
