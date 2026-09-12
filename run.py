#!/usr/bin/env python3
"""Entry point for OP-Scribe Servitor Discord bot."""

import os
from pathlib import Path


def _load_dotenv() -> None:
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.is_file():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if val and not os.environ.get(key):
                os.environ[key] = val
    except Exception:
        pass


if __name__ == "__main__":
    _load_dotenv()
    # Import and run the bot
    from opscribe.bot import _main
    _main()
