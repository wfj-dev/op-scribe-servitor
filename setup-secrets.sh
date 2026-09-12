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

cat > "$STRATEGIUM_ENV" <<EOF
STRATEGIUM_HOST=127.0.0.1
STRATEGIUM_PORT=8787
STRATEGIUM_BOT_SHARED_SECRET=${SHARED_SECRET}
STRATEGIUM_SESSION_SECRET=${SESSION_SECRET}
STRATEGIUM_ALLOWED_ORIGIN=http://127.0.0.1:8787
DISCORD_OAUTH_CLIENT_ID=${DISCORD_CLIENT_ID_VAL}
DISCORD_OAUTH_CLIENT_SECRET=${DISCORD_CLIENT_SECRET_VAL}
DISCORD_OAUTH_REDIRECT_URI=${DISCORD_REDIRECT_URI_VAL}
DISCORD_GUILD_ID=${DISCORD_GUILD_ID_VAL}
EOF
chmod 600 "$STRATEGIUM_ENV"
echo "✔ Saved Strategium config to: ${STRATEGIUM_ENV}"

# 4. Handle Bot .env if bot directory exists
if [[ -n "$BOT_DIR" && -d "$BOT_DIR" ]]; then
  DISCORD_TOKEN_VAL="${1:-${DISCORD_TOKEN:-$(get_existing_val "$BOT_ENV" "DISCORD_TOKEN")}}"

  cat > "$BOT_ENV" <<EOF
STRATEGIUM_PUBLISH_URL=http://127.0.0.1:8787/internal/roster/snapshot
STRATEGIUM_BOT_SHARED_SECRET=${SHARED_SECRET}
EOF

  if [[ -n "$DISCORD_TOKEN_VAL" ]]; then
    echo "DISCORD_TOKEN=${DISCORD_TOKEN_VAL}" >> "$BOT_ENV"
    echo "✔ Saved Bot config (with DISCORD_TOKEN) to: ${BOT_ENV}"
  else
    echo "DISCORD_TOKEN=" >> "$BOT_ENV"
    echo "✔ Saved Bot config to: ${BOT_ENV}"
    echo "ℹ Note: Add your DISCORD_TOKEN to ${BOT_ENV} or pass it as an argument: ./setup-secrets.sh <DISCORD_TOKEN>"
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
