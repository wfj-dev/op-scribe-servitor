# Galactic War Replacement

This branch replaces live directive issuance, matchmaking, expiry, recruitment views,
Ordo Xenos standing, and the published six-tier renown calculation. Legacy JSON files
remain historical records; they are not imported into new renown or rewritten.
Do not deploy this branch as a finished campaign until the content prerequisites and
remaining rollout work below have been completed.

## Implemented Rules

- Watch Master or Forge Master declares a Tyranid invasion from Strategium.
- Multiple companies share a subsector; multiple KTs share a system.
- Captains commit directly. LT proposals require their Captain or Watch Master.
- Sergeants assign their own KT. Own-company Captains/LTs or the Watch Master can
  cover an unavailable Sergeant with a recorded reason.
- Company and KT voluntary moves have separate, shared limits of two in 14 days.
- Strike signup requires the actual Watch Brother Discord role ID, including for
  Captains, High Command and specialists. A matching display name or higher rank is
  not a substitute. The role is rechecked across the signup roster before locking;
  already-signed players can still withdraw or cancel without a rank bypass.
- Each playable planet has two independent boards: four Vanilla slots and four Omega
  slots, including recruiting/locked strikes. The map switches between these formats.
  Both currently use vanilla SM2 maps. Accepted success replaces one slot in its own
  pool exactly once; uncompleted strikes retain their offer after roster cancellation.
  No failed-operation AAR is recorded. Omega's source can be changed to
  custom maps when that future content integration is available, without mixing the
  two boards or presenting unbuilt maps as playable.
  Abandoned recruitment expires after one hour without rerolling its objective.
- Filling the roster automatically locks its participants, operation, difficulty,
  specialist assignment and credit eligibility. There is no coordinator or manual
  Ready/Deploy step. A participant or Watch Master can cancel with a recorded reason
  before an AAR is reserved; cancellation releases the same offer without rerolling
  and is not a failed-operation report. Strike AARs report completions only.
- Ordinary KT members require their assigned system for full credit. Veteran
  Sergeants, LTs and Captains qualify across their company's committed subsector.
  Specialists qualify through one designated required specialist slot.
- Full credit is 1 and the initial configurable-in-code assist default is 0.25.
  Assists have normal strategic effect. No additional diminishing penalty is applied
  while that policy remains unsettled.
- KT/cadre renown divides credited participations by all current formation members,
  including those with no strikes and members hidden from the public roster.
  Thresholds over 28 days: I=4, II=8, III=28, IV=56.
- Captain/LT renown averages all company KT scores equally, then applies those
  thresholds. Lifetime individual laurels are 100/200/300 accepted successes.
- Invasions begin at 25%, last 14 days, and pass at an exact balance of at least 50%.
  Baseline drain is 2 percentage points/day and success adds 1 point. After 24 hours
  without a success, pressure ramps to 1.5 at 72 hours, increasing drain and reducing
  success gains. Spread multiplies required work by 1.5, capped at 3.375.

These numerical defaults are initial estimates, not historically calibrated values.

## Specialist Requirement Chances

Each initial offer and successful replacement independently rolls on a 200-point
table. A planet can show any combination of ungated and specialist-required offers;
there is no fixed two-and-two board. No battle-line or Dreadnought rank can be a
mission requirement. Command authority is unaffected.

| Track | Requirement chances out of all generated strikes |
| --- | --- |
| Ungated | 75% |
| Blades | Bladeguard 2%, First Blade 1.5%, High Blade 1%, Blade Master 0.5% |
| Chaplaincy | Watch Chaplain 2.5%, Reclusiarch 1%, High Chaplain 0.5% |
| Apothecarion | Watch Apothecary 2.5%, Primus Medicae 1%, Chief Apothecary 0.5% |
| Librarius | Watch Librarian 2.5%, Lexicanum Primus 1%, Void Warden 0.5% |
| Forge | Watch Techmarine 2.5%, Forge Adept 1%, Forge Master 0.5% |
| Hunt | Kill-Marine 2.5%, Venator Primus 1%, Hunt Master 0.5% |

The intermediate titles are existing base specialist roles plus High Command Equerry;
High Blade is First Blade plus Equerry. Standalone future title roles also qualify.
Senior substitution is restricted to the same specialist track, never all High Command.

World/biome compatibility still applies. An unavailable rolled requirement falls back
to a compatible lower rank in its track, then to a compatible entry-level specialist
weighted by the same table. If no entry-level specialists exist for the setting,
fallback can use any compatible specialist rank with those weights instead. A world
does not need an entry-level specialist mission: for example,
an Industrial Forge World can use High Blade and Forgemaster missions. The initial roll
still gives exactly 75% ungated and 25% specialist-gated probability; it is not a fixed
one-in-four board quota. Individual rank percentages are base probabilities, not promises
for every setting: fallback redistributes unavailable requirements among supported ranks.
Senior-only fallback can select a senior rank, but never removes a specialist gate or picks an incompatible
objective. Each planet needs at least one compatible general briefing and one compatible
specialist briefing at any rank.

The refreshed shared atlas includes Ocean and Gas Giant biomes and no longer
authors Underhive objectives. Vhaloris and Castobel's provisional campaign terrain
use Industrial; stable world IDs, saved attempts and existing campaign state are
preserved. Vanilla recipe revision 2 adds Ocean and Gas Giant adaptations with
explicit approximate-environment notes, not custom terrain requirements. Deploy
the matching Strategium geography, recipe definitions and bot code together.

## Deployment Configuration

### Memory Budget

Campaign reference data is held in a single bounded cache entry, keyed by the paths,
modification times and sizes of geography, war definition and operation references.
Changing a reference or data directory replaces that entry. Cached sectors/systems omit
map polygons, labels and coordinates that campaign rules do not use. Campaign snapshots,
SQLite connections and guild member directories are not cached by this mechanism.

Local reference-only measurements with two fixed operation formats retained about
0.57 MiB, peaked at 1.82 MiB during loading, and grew by about 4 KiB across 1,000 cache
hits. A synthetic fresh campaign in all 29 sectors (473 worlds, 3,784 offers) serialized
to 2.54 MiB and peaked at about 16 MiB while retaining the original state, JSON and a
decoded copy simultaneously. These are traced Python allocations, not the full running
bot's resident memory or a production capacity guarantee.

Planet PNGs are offline build inputs. The site serves compressed WebPs in bounded chunks
without decoding them; GPU/decoded portrait memory is allocated by the visitor's browser,
not the server. Existing browser caching reduces repeated transfers.

On a 2 GiB host, monitor bot/site RSS and total service memory under real traffic, leaving
room for the OS, Discord's member/message caches and any other services. Campaign audit,
attempt and settlement history currently remains in the SQLite state document and is
loaded for each transaction; historical growth needs monitoring and a separately reviewed
retention/migration policy. This change does not silently prune campaign or award records.

Add `galactic_war` alongside the existing bot configuration, without putting any
credentials in this section:

```json
{
  "galactic_war": {
    "geography_path": "/srv/strategium/data/reach_geography.json",
    "definition_path": "/srv/op-scribe/reference/galactic_war.json",
    "notification_channel_id": 1511147649003425973,
    "company_channel_ids": {"1": 234567890123456789},
    "strategium_url": "https://jericho-strategium.duckdns.org/reach"
  }
}
```

Defaults locate geography in the sibling Strategium checkout and use
`reference/galactic_war.json`. The geography document is the shared source for authored
world metadata, Imperial world classes, and `objectiveCatalog` version 1. The bot joins
its 29 objective contracts to that catalogue by ID, takes display names and exact authored
world/biome settings from it, and retains its approved specialist ranks and briefing
themes. Missing or unsupported catalogue versions, unknown IDs and mismatched categories
are rejected. Deploy the matching Strategium geography with this bot version; there is
no separate copied objective allowlist to maintain. These are themed briefings, not
custom gameplay. Vanilla strikes do not require custom-build certification.

For every battle-eligible body in a contested subsector, add an entry to `planets`
keyed by its existing geography ID:

```json
{"existing_body_id": {"world_type": "hive", "biome": "industrial"}}
```

The ten world types (including feudal) and nine biomes use the supplied Atlas identifiers. Underhive is removed; Vhaloris uses Industrial.
Authored geography kinds and biomes take precedence over legacy provisional terrain in
the war definition. Research Station and Research World both use `research` compatibility,
without changing their displayed identities. Older worlds without authored biomes retain
their explicit definition metadata; generic planets are never silently classified as hive
worlds. Stars and non-battle-eligible fortresses do not receive strike boards.

The shared Imperial classes are Alpha (agri), Gamma (civilised), Delta (dead), Delta Tau
(death), Epsilon (cemetery), Eta (hive), Mu (feudal), Rho (research), Phi (forge), and
Phi Lambda (feral). Their Greek symbols are preserved in each planet's `world_class`
metadata and in snapshot `content.world_classes`. These classes are not operation IDs,
specialist ranks, or the Vanilla/Omega format split.

Sector 29, **The Canticle Reach**, has compatible general and specialist objectives
for all 19 worlds, including Isolen. Its campaign can generate both mission pools
without adding or bypassing objective requirements. The current atlas has 3-4 general
objectives for each of its 80 world/biome settings and at most two specialist types per
setting. These are authored content counts, not runtime quotas; future atlas updates
can change them without changing the 75%/25% category roll. Two settings outside the
Canticle roster, `feral|starship` and `hive|jungle`, currently have no specialist content.
The bot rejects such missing-category content rather than inventing support or bypassing
a gate.

Populate `neighbors` with reviewed, symmetric subsector-ID arrays. These must match
the visible numbered map areas, not the Erioch-centered warp route graph.

The initial reference now contains 66 shared artwork borders and 473 eligible planet
terrain entries. Automatically supplied terrain has `provisional: true` and is visibly
marked in the site. These are conservative gameplay tags, not new canon claims; manual
classifications are preserved. From the Strategium checkout, regenerate with:

```bash
.venv/bin/python scripts/build_reach_geography.py --war-definition ../discord-bots/op-scribe-servitor/reference/galactic_war.json
```

That command is a dry run. Add `--write` to update only the war reference, not canonical
geography or live campaign state. Adjacency is derived from neighboring artwork masks
across narrow border strokes, excluding point contacts; symmetry and connectivity are
checked. Review provisional tags before a production pilot.

`galactic_war_runtime.vanilla_operations()` reads the existing operations reference
and the canonical AAR form options. New three-player Vanilla offers use Hard-Stratagem
recipes from `reference/galactic_war_vanilla_recipes.json`. All 29 objectives and all
387 currently authored world/biome/objective settings are covered. Siege/PvP are
excluded. Five-player Omega retains its previous mission/difficulty pool, including
its exclusion of Chaos-only maps. Omega signups require PC/Console roles and permit
at most two console participants.

Each Vanilla recipe has exactly one positive and three distinct negative stratagems:
one negative from the objective, one from the world type and one from the biome.
Operation selection prefers the intersection of the objective shortlist and biome
map list, then the world's ordered preferences; without a biome match it retains
the objective shortlist and marks the environment approximate. Starship always uses
Reclamation, with its reactor-sabotage substitution explicitly disclosed. Ice and
asteroid environments are approximations, not claims of custom terrain or physics.
The shared Atlas catalogue still decides which settings and specialist requirements
exist; recipes do not create new objectives or change the specialist probability table.

Chaos-only Vanilla operations always add **Intelligence Lapse** as a forced faction
conversion, additional to the one positive and three negatives. Structured operation
faction metadata controls this rule, not old prose lists in the stratagem reference.
Naturally Tyranid and mixed maps are not automatically swapped. Vortex's reinforcement
waves convert, but its fixed Mutalith Vortex Beast boss does not; the offer states
this exception rather than claiming an entirely Tyranid operation.

Recipes are validated against canonical operations, supported AAR form options,
stratagem types/exclusions, explicit conflict groups and bidirectional named conflicts.
Unsupported settings or unavailable assigned operations fail closed. The complete
recipe ID, version, selected modifiers, mandatory conversion and adaptation notes are
stored on each new offer and retained in its locked attempt. Reference edits invalidate
the bounded runtime cache but never rewrite existing offers or attempts. Old offers
without recipes continue under their original mission/difficulty contract; cancellation
retains the same assignment and completion replaces only that pool's completed slot.

The normal AAR verifies mission, difficulty and the locked roster, but does not prove
which stratagems were selected. Every recipe-based report therefore waits for an
independent staff evidence review before settlement, archival or rewards. Evidence
must show the stratagem selection and operation result, including forced Intelligence
Lapse when assigned. Staff see the saved recipe beside the receipt, cannot approve
their own strike, and must record an approval/rejection reason. Existing approved-review
recovery resumes archival; repeated reports cannot award credit twice. Legacy and Omega
reports keep their existing review rules. These recipes still require in-game playtests;
catalogue validation is not difficulty calibration or proof of current game behavior.

The campaign world is strategic/thematic geography; it is not a claim that Inferno
has been rebuilt on a different planet. Completing the assigned vanilla operation
with the locked squad is the result being verified. No additional carry, escort,
specialist-only interaction or custom boss is required by a briefing. Custom game
builds can be integrated later through an explicitly separate content kind; they
are not generated by this initial vanilla pool.

## Invasion Notices And Testing

The configured live campaign channel is `1430055064969674777` in guild
`1429264578440597517`. The bot needs View Channel, Send Messages, Embed Links and
Read Message History and Attach Files. The one-minute campaign loop delivers durable outbox notices;
an invasion alert uses the supplied `INCURSION_ALERT.png` banner and a red priority
transmission embed, links to Strategium, identifies the campaign/deadline and gives
company and kill-team orders. Only Watch Brother is explicitly mentioned for an
invasion; everyone/user mentions are disabled. Captains commit
companies, Lieutenants request approval, and Sergeants assign their KTs to systems.
Optional company-channel overrides remain supported. Notices retain their original
campaign metadata. Footer markers recover sends interrupted before acknowledgement
within the latest 100 channel messages; this is bounded retry deduplication, not an
unlimited exactly-once guarantee. Review dormant outbox entries before rollout.

**Invasion testing** is available only to an active Watch Master or Forge Master.
Start Test Invasion creates an owner-private SQLite scratch store under
`data_dir/galactic_war_sandboxes`, not a production-database backup or reset. Company
commitments and KT assignments use the same campaign rules in the isolated context.
Both roles may exercise orders there. Assign a KT to the selected system before
Simulate Completion; synthetic participants then exercise completion, slot replacement
and formation-credit calculations. No real AAR, challenge reward, cosmetic role or
live renown is written. Advance Test Time can exercise inactivity/deadlines and spread
only inside scratch state. Real AARs cannot resolve scratch strike IDs.

Return to live campaign exits the test view without discarding it; Resume Test Invasion
opens it again. Revert Test Invasion discards scratch orders, boards, attempts and
contributions, invalidates the session and keeps only minimal audit/notice metadata.
It is idempotent and never restores an old snapshot over live member work. Each request
rechecks role and ownership; an ended or inaccessible test does not fall back to a live
mutation. The UI offers a live-view exit after a stale-session error.

Tests never post automatically. Send Test Notice is an explicit confirmed action to
`galactic_war.test_notification_channel_id` (`1511147649003425973`), never a fallback
to the live `notification_channel_id`. The preview uses the same full invasion embed,
banner and Watch Brother-only ping as live. The confirmation warns about the ping;
the footer identifies the test session. Campaign details are saved when the test
notice is queued; legacy notices without saved details say so rather than borrowing
live campaign data. Its separate durable queue emits a matching unpinged
TEST INVASION REVERTED notice if the start notice was sent.
Reverting before an unsent notice is delivered cancels it. The regular campaign outbox
inside scratch state is never delivered. Delivery retries survive server restarts.

## Completion And Credit

Displayed offers include read-only `viewer_signup` availability/reason and current
specialist coverage, calculated using the same validation as the transactional join.
These hints never authorize a mutation; signup rechecks current roster, role, platform,
capacity and campaign state. Snapshot `content.stratagems` includes canonical names,
types and effects for displayed recipes only. No new data is persisted on offers for
these viewer-specific fields, and mission rolls/reward values are unchanged.

The map requests strike boards by selected system. The authenticated snapshot includes
all playable worlds' boards in that system only; unknown system IDs are rejected.
Planet-only requests remain supported for older clients. Live and sandbox requests
use the same bounded system scope, and mutations return the same scoped board so
signups, completions and order changes do not collapse the UI back to one planet.

Strike reports record completions only; there is no failed-operation AAR or outcome
selector. An uncompleted roster can be cancelled without campaign credit or renown.
Pressure is elapsed time since the last accepted completion, not failed-attempt counts.
Historical failed records remain untouched, but new explicit failure submissions are
rejected. The stored `last_success_at`/`strike_success` names remain compatibility fields.

| Credit | Current value |
| --- | --- |
| Campaign | Normally +1 percentage point per completed strike, divided by spread escalation and inactivity pressure |
| Formation participation | 1 full credit or 0.25 assist per participant; rolling 28-day total divided by current formation membership |
| Renown tiers | I=4, II=8, III=28, IV=56; command score is an equal average of company KT scores |
| Personal laurels | 100/200/300 accepted completed participations |
| Ordinary AAR points | Hard-Stratagem 5 base; Omega 20 minus recorded KIA; existing bonuses remain separate |

Vanilla and Omega have equal campaign/renown weight. Recipe completions wait for
independent evidence approval; on-time report receipt and the existing processing
grace preserve their campaign timestamp. Numerical tuning remains provisional.

## State And Reporting

Members use **Edit My AAR** on the normal Submit AAR page to open a website report
by ID or Discord receipt link. The bot checks the original submitter and stored
participants before returning editable fields. Structured changes are staged without
mutating canonical AARs, receipts or rewards. Existing evidence is retained.
Techmarines and High Command review the before/after values under the staff-only
**Manage AARs > Edit Requests** section and approve or decline with a note. Approval
uses the audited correction/recovery flow; stale proposals cannot overwrite a newer
report. Approval is not marked complete until archive reconciliation succeeds.
Members can request reasoned deletion of their own reports, but only a Forgemaster
can confirm it. Both editing and deletion cover website-submitted AARs only;
member-authored Discord messages are outside this workflow.

Pending member requests are kept independently of completed mutation-log retention,
limited to ten per requester and a bounded overall queue. Duplicate retry submissions
do not create duplicate requests. The staff review queues are paged together with
at most ten items and a bounded byte size per response; independent request-page
controls expose later entries. Ordinary members
cannot read this queue or directly apply edits, including by calling its API.

Deletion requests have a ten-pending-request member quota and a 1,000-pending-request
overall cap. Editors can decline a requested deletion with an audited reason and
unlock the original AAR without deleting it. A started deletion cannot be declined;
only a Forgemaster can confirm or resume it. Request-specific tokens protect both
confirmation and decline against stale screens after cancellation and re-request.

The bot owns `data/galactic_war.sqlite3` using SQLite transactions and full synchronous
writes. Include it in backups; do not copy it while writes are active without SQLite
backup support or stopping the bot. The website never writes campaign files.

`/api/war` proxies authenticated commands to the signed `/v1/aar/war` bot endpoint.
Mutations require login, CSRF, current Discord permissions, and an expected state
version. Planet boards are fetched separately, avoiding the legacy 500-directive cap.

The map's Submit AAR action prefills the automatically locked attempt ID, operation,
difficulty and signed-up players. Ordinary website AARs
include `strike_id` and boolean `strike_success`. Text reports may include:

```text
Strike ID: GW-0123456789abcdef0123456789abcdef
Strike Result: Success
```

The canonical AAR save validates roster, mission and difficulty. Each attempt accepts
one result; retries do not duplicate strategic progress, recognition or its successor.
Linked strike identity cannot be removed or reassigned by editing an AAR. New
historical laurel grants and existing cosmetic-role migration are not performed.

Website intake records trusted receipt timestamps and hashes uploaded image bytes.
Reusing an image from another strike holds the report for independent evidence review.
While held, it grants neither ordinary AAR rewards nor campaign/renown credit. Existing
AAR review staff can approve or reject with a reason, but participants and the report
submitter cannot review their own result. Approved reports have durable archive retry
jobs; pending reports are not presented as archived, and rejected reports reopen their
original slot without a reroll. Hash-reuse detection applies to website uploads; text
AARs still undergo the same strike-contract checks but do not have uploaded-byte hashes.

At the campaign deadline, pressure freezes and new signups close. On-time received
reports can finish processing within a 24-hour grace period before final resolution
and spread. Their strategic events are replayed in receipt order, not processing order.
Late reports cannot reopen a finalized campaign or create post-deadline replacement
strikes, although accepted results can still contribute personal/formation history.

Staff can reverse strike credit with a reason and independent reviewer. Reversal removes
its renown and lifetime strike contribution and recalculates an active campaign, but
never reopens a resolved campaign or rerolls an already-created successor. It does not
delete the ordinary AAR: the existing archive management workflow handles that separate
record. Deleting a linked AAR requires its strike credit to have been reversed first;
ordinary staff edits cannot silently unlink a strike or change its settled outcome.

Campaign events use a persisted Discord outbox. Configure a notification channel to
deliver them. Retries look for a per-event receipt marker before posting again; this
is best-effort Discord delivery deduplication, not a cross-system transaction.

## Remaining Rollout Work

- Review the provisional gameplay terrain and artwork-derived neighbor graph; configure
  the notification channels, guild and signed bot bridge for the deployment environment.
- Finalize assist/diminishing-return policy, roster-transfer denominator policy and
  any approved historical laurel backfill. No retrospective membership is invented.
- Rehearse independent review and credit reversal using the actual staff role configuration.
  A specialist is selected automatically from the locked roster; players do not have a
  separate readiness step.
- Design and certify custom mission content only when that future integration is added.
- Perform backed-up migration/rehearsal and a limited pilot before production cutover.

## Verification

Run each bot test file in its own process as required by `CONTRIBUTING.md`:

```bash
.venv/bin/python -m pytest tests/test_galactic_war.py -q
.venv/bin/python -m pytest tests/test_api_bridge_routes.py -q
.venv/bin/python -m pytest tests/test_strategium_publish.py -q
.venv/bin/python -m pytest tests/test_sendto_snapshot.py -q
.venv/bin/python -m pytest tests/test_parsing.py -q
```

Use fake members, temporary SQLite stores and mocked Discord delivery. Do not run
the bot against the live guild as a test. Strategium has HTTP/CSRF regressions in
its existing security suite; browser checks should use synthetic campaigns.