# Contributing to OP-Scribe Servitor

This repository has a small Python dependency surface, but most product behavior lives in a large async Discord bot with JSON-backed state. New contributors should ramp up in layers instead of starting in the deepest runtime file.

## Local setup

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Run a narrow test file before making changes.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/test_studs.py tests/test_permissions.py
```

To run the bot locally, set `DISCORD_TOKEN` first:

```bash
export DISCORD_TOKEN='your-token'
python run.py --debug
```

`--debug` disables startup and shutdown status broadcasts and turns on debug logging.

## Production safety for local runs

If a developer starts a second bot process with the **production token**, it can interfere with live behavior.
That includes duplicate event handling and command-sync side effects.

Follow these rules for junior developer machines:

1. Never use the production bot token locally.
2. Use a separate Discord application + token for development.
3. Invite that dev bot only to a dedicated dev guild.
4. Always run local sessions with `--debug`.

Use this local shell profile before running the bot:

```bash
export DISCORD_TOKEN='dev-bot-token-only'
python run.py --debug
```

If you only need to work on code and tests, do not run the Discord bot process at all.
Use `pytest` and targeted unit tests instead.

When to avoid local runtime entirely:

- Any task that only changes pure helpers (`opscribe/studs.py`, `opscribe/permissions.py`)
- Any task where behavior can be verified through existing unit tests
- Any docs-only or formatting change

## Read this first

Use this order for your first pass through the repo:

1. `ARCHITECTURE.md` for the runtime shape, locks, scheduled tasks, and data flow.
2. `README.md` for the user-facing command surface and domain context.
3. `run.py` for the entry point.
4. `opscribe/constants.py` for shared paths, IDs, and global names.
5. `opscribe/studs.py` and `opscribe/permissions.py` for low-risk logic.
6. `opscribe/datastore.py` before touching stateful behavior.
7. `opscribe/bot.py` only after the earlier steps.

## Safe places to start

These files are the best starting points for developers who are new to Python or new to this codebase:

- `opscribe/studs.py`: pure calculation helpers with clear tests.
- `opscribe/permissions.py`: rank and role logic without much async complexity.
- `tests/test_studs.py`: straightforward example of behavior-first tests.
- `tests/test_permissions.py`: simple fake-object testing pattern.

These are good first tasks:

- Add an edge-case test to an existing test file.
- Tighten behavior in a pure helper function.
- Fix a small permission or parsing rule where a nearby test already exists.
- Improve a docstring or contributor-facing documentation when behavior is already clear.

## High-risk areas

Do not start here unless the change is tightly scoped and reviewed with someone who knows the repo:

- `opscribe/bot.py`: central async runtime, command registration, global state.
- `opscribe/roster_ops.py`: large stateful feature surface.
- `opscribe/aar_ops.py`: domain-heavy parsing and reconciliation flow.
- Anything that reads and writes files under `data/`.

## Repo rules that matter

- Shared JSON-backed state lives under `data/`.
- State changes often require an `asyncio.Lock` to avoid corrupting read-modify-write flows.
- `opscribe/datastore.py` uses a write-behind cache with periodic flushes and backup files.
- `config/config.json` controls many permission and channel policies.
- Tests import many helpers through `opscribe.bot`, even when the implementation lives in extracted modules.

Before changing stateful code, answer these questions:

1. Is this pure logic, or does it mutate persisted state?
2. If it mutates state, which lock protects that file or workflow?
3. Is there already a nearby test file that should grow with this change?

## Suggested first week

Day 1:

- Read `ARCHITECTURE.md` and `README.md`.
- Run `pytest tests/test_studs.py tests/test_permissions.py`.

Day 2:

- Read `opscribe/studs.py`, `opscribe/permissions.py`, and the matching tests.
- Add one small test-only change to get comfortable with the workflow.

Day 3:

- Read `opscribe/datastore.py` and `config/config.json`.
- Trace how runtime configuration and persistent state are loaded.

Day 4 and after:

- Move into a small code change with tests.
- Avoid `opscribe/bot.py` for the first few tasks unless paired.

## Pull request expectations

- Keep the first few changes narrow and behavior-scoped.
- Prefer adding or updating a focused test before broad refactors.
- If you touch async or stateful code, call out lock usage in the PR description.
- Do not mix onboarding-sized fixes with unrelated cleanup.

## Commit message convention and auto-versioning

This repository uses push-time automatic version bumps from commit significance.
The source of truth is `opscribe/__init__.py` and starts from the current baseline.

Use Conventional Commit style prefixes so bump level is deterministic:

- `feat: ...` or `feat(scope): ...` -> minor bump
- `fix: ...`, `chore: ...`, and other commit types -> patch bump
- `type(scope)!: ...` or a `BREAKING CHANGE:` footer -> major bump

Notes:

- A workflow commit message containing `[skip version bump]` is ignored by the bump job to avoid recursion.
- If multiple commits are in one push, the highest significance wins (`major > minor > patch`).
- For local verification without mutating files, run: `python3 scripts/auto_bump_version.py --dry-run HEAD~1..HEAD`.
- The workflow runs on pushes to `master` and `main`.

## Domain notes

This repo uses Warhammer-themed names heavily. Treat those names as business-domain terms, not as technical categories. If a term is unclear, check `README.md`, `GUIDE_WATCH_BROTHER.md`, `GUIDE_WATCH_COMMAND.md`, and the nearby tests before changing logic.