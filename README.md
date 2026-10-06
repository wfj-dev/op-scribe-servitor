# OP-Scribe Servitor

Async Discord bot for Watch Fortress Jericho operations.

This README is the developer and maintainer landing page. End-user command usage is documented in role-specific guides.

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

The pilot defaults to `web_submission.access_mode: "staff"`, requiring the High Command role (ID `1452913063970865203`) or the Watch Techmarine role. Set `access_mode` to `"members"` later to open the page to every logged-in non-bot guild member. The signed private `/v1/aar/access` endpoint checks fresh guild membership/roles and returns the server display name; submit authorization is checked again by the intake. Unknown modes fail closed. Keep both access verification and submission endpoints private.

The optional Strategium website intake uses this bridge at `/v1/aar/submissions`. It is disabled unless the root `web_submission.enabled` setting is explicitly `true`. The request carries structured report fields and 1–10 PNG/JPEG/WebP screenshots (maximum 8 MiB each and 32 MiB total by default); the bot validates them, commits the canonical AAR record and processed ID through `DataStore`, runs the existing challenge/award/LOA hooks, and posts a separate lore-style embed receipt. That receipt intentionally has no `++ MISSION REPORT ++` marker and is skipped by message ingestion and reparse. New submissions from one member are rate-limited by `web_submission.cooldown_seconds` (60 seconds by default); the site allows at most two concurrent uploads, and the bot allows at most two in-flight uploads / 48 MiB total. Same-key retries bypass the cooldown. Posted-but-unprocessed receipts are retried in the background. The journal caps pending entries with `web_submission.max_pending_submissions` (100 by default) and prunes completed idempotency entries after `web_submission.completed_retention_days` (30 days) or above `web_submission.max_completed_entries` (5,000).

Receipt reconciliation requires an exact submission marker on a message authored by this bot. Send intent is journaled before contacting Discord; an uncertain send or an unavailable previously posted receipt is never blindly reposted. Retry with the same submission key so recovery can find the original receipt. If delivery remains unverified (for example, the receipt is deleted or falls outside the 100-message recovery window), a Watch Techmarine must reconcile the journal and channel before permitting another send. Pending entries continue to count toward the queue cap.

Set the same `STRATEGIUM_BOT_AAR_SHARED_SECRET` in the Strategium and bot runtime environments. The Strategium repository's `setup-secrets.sh` provisions it for local development. Keep `api.host` on `127.0.0.1`; Strategium calls the local bridge directly. The intake never trusts client-supplied participant IDs or posts a raw canonical report to the AAR channel. The existing `/submit_aar` command permissions and testing mode remain unchanged.

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
