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
