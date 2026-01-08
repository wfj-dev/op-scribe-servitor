# OP-Scribe Servitor — Briefs Guide (Non-Technical)

This guide explains the five “briefs” the bot can generate and how to read the metrics in plain language. It’s written for Watch Command and Kill Team leaders who want quick, reliable insights without technical details.

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

If you want screenshots or examples added, we can include a short “Sample Output” section next.

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
