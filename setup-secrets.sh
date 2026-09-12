#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-detect Strategium and Bot directories from various possible invocation paths
STRATEGIUM_DIR=""
BOT_DIR=""

for cand in "$SCRIPT_DIR" "$SCRIPT_DIR/.." "$SCRIPT_DIR/../.." "/home/julian/strategium"; do
  if [[ -f "$cand/server.py" && -f "$cand/jericho-strategium.html" ]]; then
    STRATEGIUM_DIR="$(cd "$cand" && pwd)"
    break
  fi
done

for cand in "$SCRIPT_DIR" "$SCRIPT_DIR/.." "$SCRIPT_DIR/../../discord-bots/op-scribe-servitor" "$SCRIPT_DIR/../discord-bots/op-scribe-servitor" "/home/julian/discord-bots/op-scribe-servitor"; do
  if [[ -f "$cand/run.py" && -d "$cand/opscribe" ]]; then
    BOT_DIR="$(cd "$cand" && pwd)"
    break
  fi
done

if [[ -z "$STRATEGIUM_DIR" ]]; then
  echo "Error: Could not locate Strategium directory."
  exit 1
fi

STRATEGIUM_ENV="${STRATEGIUM_DIR}/.env"
BOT_ENV="${BOT_DIR}/.env"

gen_secret() {
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
}

get_existing_val() {
  local file="$1"
  local key="$2"
  if [[ -f "$file" ]]; then
    grep -E "^${key}=" "$file" | head -n 1 | cut -d'=' -f2- | tr -d '\r"' || true
  fi
}

upsert_env_val() {
  local file="$1"
  local key="$2"
  local value="$3"
  local temp_file="${file}.tmp.$$"
  touch "$file"
  awk -v key="$key" -v value="$value" '
    BEGIN { updated = 0 }
    $0 ~ "^[[:space:]]*" key "=" {
      if (!updated) {
        print key "=" value
        updated = 1
      }
      next
    }
    { print }
    END {
      if (!updated) print key "=" value
    }
  ' "$file" > "$temp_file"
  mv "$temp_file" "$file"
}

echo "=== Setting up local development secrets ==="

# 1. Resolve or generate STRATEGIUM_BOT_SHARED_SECRET
SHARED_SECRET="$(get_existing_val "$STRATEGIUM_ENV" "STRATEGIUM_BOT_SHARED_SECRET")"
if [[ -z "$SHARED_SECRET" && -n "$BOT_DIR" ]]; then
  SHARED_SECRET="$(get_existing_val "$BOT_ENV" "STRATEGIUM_BOT_SHARED_SECRET")"
fi
if [[ -z "$SHARED_SECRET" ]]; then
  SHARED_SECRET="$(gen_secret)"
  echo "✔ Generated new STRATEGIUM_BOT_SHARED_SECRET"
else
  echo "✔ Reusing existing STRATEGIUM_BOT_SHARED_SECRET"
fi

# 2. Resolve or generate STRATEGIUM_SESSION_SECRET
SESSION_SECRET="$(get_existing_val "$STRATEGIUM_ENV" "STRATEGIUM_SESSION_SECRET")"
if [[ -z "$SESSION_SECRET" ]]; then
  SESSION_SECRET="$(gen_secret)"
  echo "✔ Generated new STRATEGIUM_SESSION_SECRET"
else
  echo "✔ Reusing existing STRATEGIUM_SESSION_SECRET"
fi

# 3. Write strategium .env
DISCORD_CLIENT_ID_VAL="$(get_existing_val "$STRATEGIUM_ENV" "DISCORD_OAUTH_CLIENT_ID")"
DISCORD_CLIENT_SECRET_VAL="$(get_existing_val "$STRATEGIUM_ENV" "DISCORD_OAUTH_CLIENT_SECRET")"
DISCORD_REDIRECT_URI_VAL="$(get_existing_val "$STRATEGIUM_ENV" "DISCORD_OAUTH_REDIRECT_URI")"
if [[ -z "$DISCORD_REDIRECT_URI_VAL" ]]; then
  DISCORD_REDIRECT_URI_VAL="http://127.0.0.1:8787/api/auth/discord/callback"
fi
DISCORD_GUILD_ID_VAL="$(get_existing_val "$STRATEGIUM_ENV" "DISCORD_GUILD_ID")"

upsert_env_val "$STRATEGIUM_ENV" "STRATEGIUM_HOST" "127.0.0.1"
upsert_env_val "$STRATEGIUM_ENV" "STRATEGIUM_PORT" "8787"
upsert_env_val "$STRATEGIUM_ENV" "STRATEGIUM_BOT_SHARED_SECRET" "$SHARED_SECRET"
upsert_env_val "$STRATEGIUM_ENV" "STRATEGIUM_SESSION_SECRET" "$SESSION_SECRET"
upsert_env_val "$STRATEGIUM_ENV" "STRATEGIUM_ALLOWED_ORIGIN" "http://127.0.0.1:8787"
upsert_env_val "$STRATEGIUM_ENV" "DISCORD_OAUTH_CLIENT_ID" "$DISCORD_CLIENT_ID_VAL"
upsert_env_val "$STRATEGIUM_ENV" "DISCORD_OAUTH_CLIENT_SECRET" "$DISCORD_CLIENT_SECRET_VAL"
upsert_env_val "$STRATEGIUM_ENV" "DISCORD_OAUTH_REDIRECT_URI" "$DISCORD_REDIRECT_URI_VAL"
upsert_env_val "$STRATEGIUM_ENV" "DISCORD_GUILD_ID" "$DISCORD_GUILD_ID_VAL"
chmod 600 "$STRATEGIUM_ENV"
echo "✔ Saved Strategium config to: ${STRATEGIUM_ENV}"

# 4. Handle Bot .env if bot directory exists
if [[ -n "$BOT_DIR" && -d "$BOT_DIR" ]]; then
  DISCORD_TOKEN_VAL="${DISCORD_TOKEN:-$(get_existing_val "$BOT_ENV" "DISCORD_TOKEN")}"
  if [[ -z "$DISCORD_TOKEN_VAL" && -t 0 ]]; then
    read -r -s -p "Discord bot token (leave blank to configure later): " DISCORD_TOKEN_VAL
    echo
  fi
  upsert_env_val "$BOT_ENV" "STRATEGIUM_PUBLISH_URL" "https://127.0.0.1:8787/internal/roster/snapshot"
  upsert_env_val "$BOT_ENV" "STRATEGIUM_BOT_SHARED_SECRET" "$SHARED_SECRET"
  if [[ -n "$DISCORD_TOKEN_VAL" ]]; then
    upsert_env_val "$BOT_ENV" "DISCORD_TOKEN" "$DISCORD_TOKEN_VAL"
    echo "✔ Saved Bot config (with DISCORD_TOKEN) to: ${BOT_ENV}"
  else
    echo "✔ Saved Bot config to: ${BOT_ENV}"
    echo "ℹ Note: Add your DISCORD_TOKEN to ${BOT_ENV} or export DISCORD_TOKEN before running this script"
  fi
  chmod 600 "$BOT_ENV"
fi

echo ""
echo "=== Done! You can now run both services directly ==="
echo "Terminal 1 (Strategium Webapp):"
echo "  cd ${STRATEGIUM_DIR} && python3 server.py"
echo ""
echo "Terminal 2 (Discord Bot):"
if [[ -n "$BOT_DIR" ]]; then
  echo "  cd ${BOT_DIR} && source .venv/bin/activate && python3 run.py --debug"
fi
echo ""
