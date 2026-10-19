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
  pool exactly once; failure retries the same offer. Omega's source can be changed to
  custom maps when that future content integration is available, without mixing the
  two boards or presenting unbuilt maps as playable.
  Abandoned recruitment expires after one hour without rerolling its objective.
- Filling the roster automatically locks its participants, operation, difficulty,
  specialist assignment and credit eligibility. There is no coordinator or manual
  Ready/Deploy step. A participant or Watch Master can cancel with a recorded reason
  before an AAR is reserved; cancellation releases the same offer without rerolling.
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
to a compatible lower rank in its track, or a compatible entry-level specialist from
another track. It never promotes the roll to a rarer rank or changes the 25% specialist
gate chance. These are base probabilities: individual specialist percentages can decrease
when that rank has no compatible briefing. Each planet needs at least one compatible
general briefing and one compatible entry-level specialist briefing.

## Deployment Configuration

Add `galactic_war` alongside the existing bot configuration, without putting any
credentials in this section:

```json
{
  "galactic_war": {
    "geography_path": "/srv/strategium/data/reach_geography.json",
    "definition_path": "/srv/op-scribe/reference/galactic_war.json",
    "notification_channel_id": 123456789012345678,
    "company_channel_ids": {"1": 234567890123456789},
    "strategium_url": "https://your-strategium-host/reach"
  }
}
```

Defaults locate geography in the sibling Strategium checkout and use
`reference/galactic_war.json`. Its 29 Atlas entries are themed briefings, not custom
gameplay. Vanilla strikes do not require custom-build certification.

For every battle-eligible body in a contested subsector, add an entry to `planets`
keyed by its existing geography ID:

```json
{"existing_body_id": {"world_type": "hive", "biome": "underhive"}}
```

The nine world types and eight biomes use the supplied Atlas identifiers.
Existing specific geography kinds map to their matching world type; generic planets
are never silently classified as hive worlds. All planets need an explicit biome.
Stars and non-battle-eligible fortresses do not receive strike boards.

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
and the canonical AAR form options. It includes supported three-player operations
and five-player Omega, excludes Siege/PvP and Chaos-only maps, and never assumes an
Intel Lapse faction conversion. The operation's actual map, report mode, difficulty
and capacity are stored on the offer and displayed separately from its Atlas theme.
Omega signups require PC/Console roles and permit at most two console participants.

The campaign world is strategic/thematic geography; it is not a claim that Inferno
has been rebuilt on a different planet. Completing the assigned vanilla operation
with the locked squad is the result being verified. No additional carry, escort,
specialist-only interaction or custom boss is required by a briefing. Custom game
builds can be integrated later through an explicitly separate content kind; they
are not generated by this initial vanilla pool.

## State And Reporting

Members can request a correction from the normal Submit AAR page using an AAR ID or
Discord receipt link and a description of the change. The bot checks that the member
submitted or participated in that AAR. Requests do not mutate canonical AARs or rewards.
They appear in a separate **Edit Requests** section of the existing, staff-only
**Manage AARs** page. Current edit permissions protect both the page and every queue
listing/decision endpoint. Editors use the existing correction flow, then mark the
request corrected with a note, or decline it with a reason. Resolution is refused
until the archived record has actually changed. Older Discord-only reports remain
visible as requests but require the appropriate archive staff correction path.

Pending member requests are kept independently of completed mutation-log retention,
limited to ten per requester and a bounded overall queue. Duplicate retry submissions
do not create duplicate requests. The staff queue shows the oldest 25 requests at a
time; resolving/declining entries exposes the next queued items. Ordinary members
cannot read this queue or directly apply edits, including by calling its API.

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