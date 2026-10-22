# OP-Scribe Servitor

Async Discord bot for Watch Fortress Jericho operations.

This README is the developer and maintainer landing page. End-user command usage is documented in role-specific guides.

## Galactic War Cutover

The replacement campaign/renown implementation is documented in [GALACTIC_WAR.md](GALACTIC_WAR.md).
Legacy strike-directive issuance, queue matching and persistent recruitment views are
retired on this branch. Older directive instructions in the role guides describe the
historical system, not the website-led Galactic War. Strikes now use vanilla operations
and existing five-player Omega in independent four-strike boards per planet, with
automatic signup locking and normal linked AARs. Evidence holds, independent review,
processing grace and audited strike-credit reversal are implemented.
The documented review, metadata and rollout gates remain before production deployment.

## Award routing and chapter reference

Defense of Herisor Hard-Siege reports qualify at **5+ waves**, not ten.
The existing three-Brother and difficulty/tag requirements still apply, including
the Herisor exception for Black Laurels.

Award announcements recognize the Kill Team roles in `kill_teams.role_ids`,
including `1559221547590221965`. Routing prefers `kill_teams.role_channel_map`;
map a role to its **post/thread ID**, never its parent forum ID. Automatic lookup
prefers an exact team name, scopes to the member's configured company forum when
available, and searches archived forum posts if no active match exists. Missing
or ambiguous destinations produce a warning before the existing general-channel
fallback. The supplied Secundus forum ID `1458255656682258504` is already allowed;
it is not itself a team announcement destination.

The four-hour challenge sweep also repairs missing earned medal roles after a
member returns to Discord. A previously announced campaign, Pipehitter, Herisor,
Black Laurels or Order Omega award must still satisfy its saved progress; a
notification flag alone is not entitlement. New completions keep their normal
announcement path. Terminus class medals require all verified kill thresholds,
and Master Terminus Slayer requires every class to be complete in verified
progress, not merely possession of class roles. Previously awarded Crux Terminatus
is restored only after fresh prerequisite-role checks and the shared Black
Laurels Rank-A archive audit. Rank roles are not restored by this repair.

Restoration runs after member-cache warmup on startup, then every four hours.
The member must have an eligible Watch rank; inductees must complete induction
first. Existing acquisition dates and announcement history are retained, and
restoration does not enqueue public posts. Missing roles, Discord permission
failures and unavailable Crux verification are logged and retried on the next
sweep. No role is inferred from a member ID or an empty/missing archive.
Point-based Ardent Raider, Apothecarion and Crimson Laurels medals continue to
use their existing periodic stats-based assignment paths, which already assign
eligible missing roles independently of announcement flags.

Black Vipers are supported as a canonical chapter in role selection, induction,
chapter rotation, embed colors, welcome text and award flavor. Their original
reference summary in [reference/chapters.json](reference/chapters.json) cites
the published lore; unknown doctrine is deliberately assigned no gameplay weights.
The Discord chapter role must exist before induction can select it. Artwork is
supplied separately through the Strategium's pauldron assets.

## Choose your path

- Developers and maintainers: start with this README, then `ARCHITECTURE.md`, then `CONTRIBUTING.md`.
- Contributors from forks: read `CONTRIBUTING.md` for identity, setup, testing, and PR expectations.
- Discord users and command staff: use the role guides below instead of this README.

## User guides

- `GUIDE_WATCH_BROTHER.md` - enlisted slash-command usage and fast troubleshooting.
- `GUIDE_WATCH_COMMAND.md` - Watch Sergeant+ command and workflow guidance.
- `GUIDE_TECHMARINE_TROUBLESHOOTING.md` - support triage and escalation flow.

## Developer quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run identity setup once after cloning:

```bash
bash scripts/set-project-identity.sh
```

## Run tests

Most changes can be validated without running a live bot process.

```bash
# Run all tests as isolated file invocations
find tests -name "test_*.py" | sort | xargs -I{} pytest {} -q

# Run one test file
pytest tests/test_studs.py -q
```

## Optional live-bot testing

Never use the production token for local development.

Use a personal Discord server and a separate dev bot token:

```bash
export DISCORD_TOKEN='your-dev-token-here'
python run.py --debug
```

`--debug` suppresses startup and shutdown broadcasts and enables debug logging.

## Jericho Loader API bridge (WIP)

The repository now includes an optional local HTTP bridge in [opscribe/api_bridge.py](opscribe/api_bridge.py).

- It is disabled by default.
- It should listen on localhost only.
- Public HTTPS is handled by a reverse proxy (Caddy or equivalent).

### Enable local bridge

Set [config/config.json](config/config.json) `api.enabled` to `true`, then configure:

- `api.host`: keep as `127.0.0.1`
- `api.port`: local API port (default `8080`)
- `api.public_base_url`: public HTTPS URL used for OAuth callbacks
- `api.queue_channel_id`: channel used for queue message posting
- `api.token_ttl_seconds`: bearer token lifetime (default 30 days)

Provide OAuth secrets through environment variables (not in git-tracked config):

```bash
export DISCORD_OAUTH_CLIENT_ID='...'
export DISCORD_OAUTH_CLIENT_SECRET='...'
```

### Strategium AAR submissions

The `/submit_aar` slash command, including its staff test mode, is retired.
Submit reports through the Strategium's `/submit-aar` page instead. The bot keeps
the shared report formatter, signed intake, Discord receipt processing and manual
Discord report ingestion. Deploy and run the normal command sync to remove the
old command from Discord's menu; the other ledger, challenge, portrait and
maintenance commands remain available.

Any logged-in, non-bot member of the configured Discord guild may submit a website AAR; no Discord role is required. The signed private `/v1/aar/access` endpoint checks fresh guild membership and returns the server display name, and the intake checks membership again at submission time. Keep both access verification and submission endpoints private.

The optional Strategium website intake uses this bridge at `/v1/aar/submissions`. It is disabled unless the root `web_submission.enabled` setting is explicitly `true`. The request carries structured report fields and 1–10 PNG/JPEG/WebP screenshots; each image is limited by Discord's current guild upload limit, with a 32 MiB application ceiling and 32 MiB combined per AAR. The bot validates them, commits the canonical AAR record and processed ID through `DataStore`, runs the existing challenge/award/LOA hooks, and posts a separate lore-style embed receipt. That receipt intentionally has no `++ MISSION REPORT ++` marker and is skipped by message ingestion and reparse. New submissions from one member are rate-limited by `web_submission.cooldown_seconds` (60 seconds by default); the site allows at most two concurrent uploads, and the bot allows at most two in-flight uploads / 48 MiB total. Same-key retries bypass the cooldown. Posted-but-unprocessed receipts are retried in the background. The journal caps pending entries with `web_submission.max_pending_submissions` (100 by default) and prunes completed idempotency entries after `web_submission.completed_retention_days` (30 days) or above `web_submission.max_completed_entries` (5,000).

Receipt reconciliation requires an exact submission marker on a message authored by this bot. Send intent is journaled before contacting Discord; an uncertain send or an unavailable previously posted receipt is never blindly reposted. Retry with the same submission key so recovery can find the original receipt. If delivery remains unverified (for example, the receipt is deleted or falls outside the 100-message recovery window), a Watch Techmarine must reconcile the journal and channel before permitting another send. Pending entries continue to count toward the queue cap.

The private `/v1/aar/management/*` routes support staff-only corrections of `strategium_web` records. High Command and Watch Techmarines may edit; any current Forgemaster may confirm a reasoned deletion request and acknowledge manual-award review items. Edits update the bot-owned Components-v2 receipt and canonical record, preserving the original submitter and operation timestamp. If receipt delivery succeeded but archive/reconciliation did not, the normalized submission and affected participants are journaled; the staff manager can resume from the current receipt after a browser or bot restart. Confirmed deletion removes the receipt, canonical record, processed ID, and submission journal entry while retaining a minimal actor/reason/phase audit. AAR challenge progress is rebuilt for affected participants without re-announcing old awards; only roles with a recorded bot acquisition timestamp are revoked automatically, while uncertain grants are returned for staff review and can be acknowledged by a Forgemaster. Mutation phases are durable and idempotent so retries can resume after receipt or datastore failure.

Set the same `STRATEGIUM_BOT_AAR_SHARED_SECRET` in the Strategium and bot runtime environments. The bot also accepts the existing `STRATEGIUM_BOT_SHARED_SECRET` as a fallback. The Strategium repository's `setup-secrets.sh` provisions the AAR-specific name in both local `.env` files. Keep `api.host` on `127.0.0.1`; Strategium calls the local bridge directly. The intake never trusts client-supplied participant IDs or posts a raw canonical report to the AAR channel.

The Strategium manager offers Forgemasters a reason-plus-confirmation deletion flow,
sequencing the existing request and confirm routes. Pending deletion items include
the original Discord receipt URL; the mutation retains that URL through recovery
even after the canonical record is removed. A duplicate request returns its original
audited reason rather than overwriting it.

Members can open website AARs they submitted or participated in through the private
`/v1/aar/own/records/{record_id}` route. Proposed report fields are validated and staged
through `/v1/aar/edit-requests`, with a baseline record hash and unchanged evidence.
Techmarines or High Command approve the exact proposal through the existing audited
edit endpoint; stale proposals are rejected and interrupted approvals remain resumable.
Members can request deletion through `/own/records/{record_id}/delete-request`, but
only Forgemasters can confirm deletion. Member-posted Discord AARs are excluded.

The management list accepts a separate `queue_offset` and returns `queue_total`,
`queue_next_offset` and `queue_has_more` for its combined review queues. Pages contain
at most ten review items and stay below a 480 KiB queue-response budget. Pending
deletion requests are limited to ten per member and 1,000 overall. Editors may POST
`/management/records/{record_id}/delete-reject` with an 8-400 character `note` and
the `request_token` to decline an unstarted request. Confirmations also carry that
token. Declines retain the original request and reviewer audit and unlock editing;
destructive processing cannot be cancelled through this endpoint.

`queue_kind` filters `all`, `edit`, `delete` or `recovery` before paging, with
`queue_counts` providing per-kind totals. Staff record detail accepts a validated
`review_type` (`edit`, `delete`, `recovery` or `award`) and `review_id`, resolves
that exact mutation against the requested record, and includes its review context.
This lets website preview permalinks work independently of the current queue page.
Member-owned record detail never includes staff review metadata. A partially deleted
record returns its retained deletion/recovery status rather than a broken preview.

The website uses `queue_kind=requests` for member edits/deletions and `recovery`
only in its separate unfinished-update view. Staff AAR capabilities use the shared
configured role canonicalizer, including the `Forge Master` alias for `Forgemaster`;
display names do not confer authority.

The archive list's `query` matches all entered words across record IDs, receipt
URLs, mission, difficulty, operation date, submitter/participant IDs, stored
participant names, challenge tag keys/labels and structured report fields.
Underscores and hyphens are treated as spaces, so `black_laurels` and
`black laurels` both work. Filtering happens before archive pagination, and history
cards receive mode, participant IDs, tags, rank, waves, KIA and PvP result metadata.
Only website-submitted records are included, as before. Member review tickets
are ordered oldest-request-first across edits and deletions before queue pagination.

### Reverse proxy (Caddy example)

```caddy
jericho-api.example.com {
	encode zstd gzip
	@privateAAR path /v1/aar/*
	respond @privateAAR "not found" 404

	@health path /health
	reverse_proxy 127.0.0.1:8080

	# Optional basic hardening for public API exposure.
	header {
		X-Content-Type-Options nosniff
		Referrer-Policy no-referrer
		X-Frame-Options DENY
	}
}
```

Production note: keep the Python bridge bound to localhost and expose only ports 80/443 for the reverse proxy.

### Operator checklist

Before turning on `api.enabled`, verify all of the following:

1. `api.host` is `127.0.0.1`.
2. `api.port` is not used by another process.
3. `api.public_base_url` matches the reverse proxy HTTPS hostname.
4. `api.queue_channel_id` points to the desired queue channel in the target guild.
5. `DISCORD_OAUTH_CLIENT_ID` and `DISCORD_OAUTH_CLIENT_SECRET` are set in the runtime environment.
6. Discord OAuth redirect URI exactly matches `https://<your-domain><api.oauth_redirect_path>`.
7. Reverse proxy forwards traffic to `127.0.0.1:<api.port>` and terminates TLS publicly.

## High-signal docs index

- `ARCHITECTURE.md` - runtime shape, task loops, data flow, and lock model.
- `CONTRIBUTING.md` - contribution process, identity constraints, and commit policy.
- `FORMATTING.md` - Discord message formatting standards.
- `EMBED_COMPONENT_AUDIT.md` - roster container vs classic embed guidance.

## Key code locations

- `opscribe/bot.py` - core Discord client and command registration.
- `opscribe/datastore.py` - JSON-backed cache and flush behavior.
- `opscribe/permissions.py` - role and permission checks.
- `opscribe/studs.py` - pure stud calculation logic.
- `opscribe/aar_ops.py` - AAR parsing and reconciliation logic.
- `config/config.json` - guild policy, permissions, and channel constraints.
- `data/` - persisted bot state JSON files.

## Operational guardrails

- Keep edits small and test-backed.
- Treat `config/config.json` and `data/` as sensitive runtime surfaces.
- Review `ARCHITECTURE.md` before touching stateful paths or scheduled task logic.
- Use role guides for command behavior changes to avoid duplicate docs.

## Strategium Snapshot Publisher

The bot can optionally publish a signed roster snapshot to the separate Strategium backend. It does not store Strategium backstories or serve Strategium site routes.

Set these environment variables in the bot process:

```bash
export STRATEGIUM_PUBLISH_URL="https://your-strategium-host/internal/roster/snapshot"
export STRATEGIUM_BOT_SHARED_SECRET="use-the-same-random-secret-as-the-strategium-backend"
```

The publisher is disabled when either variable is missing. It sends Discord-role assignments and bot-computed stats every five minutes over HTTPS. Keep the shared secret outside the repository and rotate it if it is exposed.
