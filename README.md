# OP Scribe Servitor

A Discord bot that manages After-Action Reports (AARs) and generates service records and combat bonds for Watch Fortress Jericho.

## Setup

1. Create and activate a virtual environment (optional):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment:
- Set `DISCORD_TOKEN` in your environment.
- Optional: Edit `config/config.json` to configure channel IDs, role aliases, and permission thresholds.

4. Run the bot:

```bash
python bot.py
```

## Configuration
`config/config.json` supports:
- `guild_id`: Limit command sync to a specific guild during development.
- `allowed_command_channel_ids`: Restrict commands to these channel IDs.
- `admin_user_ids`: Additional admin overrides by Discord user ID.
- `role_aliases`: Map canonical role names to a list of accepted aliases.
- `permissions`: Min-rank thresholds and explicit role gates for commands.
- `logging.level`: Logging level (`INFO`, `DEBUG`, etc.).

## Tests
Run unit tests:

```bash
pytest -q
```
