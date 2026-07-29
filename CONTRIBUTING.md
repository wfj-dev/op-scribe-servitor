# Contributing to OP-Scribe Servitor

This repo is an async Discord bot with JSON-backed state. Keep changes small, test-backed, and privacy-friendly.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Run a narrow test slice before changing code.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/test_studs.py tests/test_permissions.py
```

## Local bot safety

If you only need code or test changes, do not run the bot at all. Use `pytest` instead.

If you do run it locally, use a separate dev token and keep it off the live guild:

```bash
export DISCORD_TOKEN='dev-bot-token-only'
python run.py --debug
```

`--debug` turns on debug logging and disables startup/shutdown broadcasts. Do not use the production token locally; that is the case that can interfere with the hosted bot through duplicate event handling and command-sync side effects.

## Privacy and contribution rules

- Use a pseudonymous GitHub name and a GitHub noreply email.
- Do not share real names, emails, socials, workplaces, or locations in commits, PRs, or comments.
- Keep discussion in the repo or an anonymous-friendly channel if you want to stay private.

## If you need to rewrite commit history

Do this before you invite outside contributors:

1. Freeze new pushes and create a backup ref or tag.
2. Rewrite the history with `git filter-repo` or interactive rebase.
3. Force-push the cleaned branch.
4. Tell contributors to reclone or hard reset to the new history.

## Read first

1. `ARCHITECTURE.md` for runtime shape, locks, and data flow.
2. `README.md` for commands and domain context.
3. `run.py` for the entry point.
4. `opscribe/studs.py` and `opscribe/permissions.py` for safe starter logic.
5. `opscribe/datastore.py` before touching stateful behavior.
6. `opscribe/bot.py` last.

## Commit messages

Use Conventional Commits so the version bump job stays deterministic:

- `feat: ...` or `feat(scope): ...` -> minor bump
- `fix: ...`, `chore: ...`, and other non-breaking commits -> patch bump
- `type(scope)!: ...` or a `BREAKING CHANGE:` footer -> major bump

For workflow commits, append `[skip version bump]`.
For a dry-run check, use `python3 scripts/auto_bump_version.py --dry-run HEAD~1..HEAD`.
