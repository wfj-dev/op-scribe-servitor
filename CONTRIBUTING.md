# Contributing to OP-Scribe Servitor

This repo is an async Discord bot with JSON-backed state. Keep changes small and test-backed.

## Scope of this document

This file is for contributor workflow only: identity, setup, testing, and pull request expectations.

This file is not the user command manual. For slash-command usage and operational support flows, use:

- `GUIDE_WATCH_BROTHER.md`
- `GUIDE_WATCH_COMMAND.md`
- `GUIDE_TECHMARINE_TROUBLESHOOTING.md`

## Getting access

Post your GitHub username in `#⁠❖⋅⋅ɪɴɴᴇʀ-ғᴏʀɢᴇ⋅⋅❖` in Watch Fortress Jericho.  You must be a techmarine of WFJ.  All contributions go through a fork and pull request — no direct pushes to `master`.

## Identity requirements

- Use a pseudonymous GitHub display name (no real name).
- Enable **Keep my email address private** in GitHub Settings → Emails, then copy the `XXXXXXXX+handle@users.noreply.github.com` address it shows you.
- Configure git for this repo:

```bash
git config user.name "YourChosenName"
git config user.email "XXXXXXXX+handle@users.noreply.github.com"
```

Do not include real names, emails, socials, or location in commits, PRs, or comments.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the identity helper once after cloning:

```bash
bash scripts/set-project-identity.sh
```

## Testing without running the bot

For most code changes you do not need to run the bot at all — use `pytest`:

```bash
# Run all tests (each file runs in its own process to avoid import side effects)
find tests -name "test_*.py" | sort | xargs -I{} pytest {} -q

# Or run a specific file
pytest tests/test_studs.py -q
```

Read `ARCHITECTURE.md` before touching any stateful paths (`opscribe/datastore.py`, `opscribe/bot.py`).

## Testing with a live bot (optional)

You will never have access to the production bot token or the live Watch Fortress Jericho server. If you need to test slash commands or Discord event handling:

1. Create a **personal Discord server** (free, takes 30 seconds).
2. Register a **new bot application** at [discord.com/developers/applications](https://discord.com/developers/applications) — this is your dev bot, completely separate from the production bot.
3. Add the dev bot to your personal server.
4. Export only the dev token:

```bash
export DISCORD_TOKEN='your-dev-token-here'
python run.py --debug
```

`--debug` disables startup/shutdown broadcasts and enables debug logging. It will never sync commands to or receive events from the live server.

Do not commit or share the dev token. The production token is never stored in this repo.

## Read first

- `ARCHITECTURE.md` — runtime shape, locks, and data flow
- `README.md` — developer landing page and docs index
- `opscribe/studs.py` and `opscribe/permissions.py` — safe starter logic

## Commit messages

Use Conventional Commits:

- `feat: ...` → minor version bump
- `fix: ...`, `chore: ...` → patch bump
- `type!: ...` or `BREAKING CHANGE:` footer → major bump

Append `[skip version bump]` for CI/workflow-only commits.
Dry-run check: `python3 scripts/auto_bump_version.py --dry-run HEAD~1..HEAD`.
Version bump automation runs on pull requests targeting `main`/`master` and commits the bump to the PR branch.
