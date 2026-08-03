#!/usr/bin/env bash
# Run once after cloning to set the correct git identity and verify the remote.
set -euo pipefail

git config user.name "Forge Master Jules"
git config user.email "312649628+watchfortressjericho-svg@users.noreply.github.com"

REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
if [[ "$REMOTE" != *"github-wfj"* ]]; then
    echo "WARNING: remote is not using the project SSH alias."
    echo "Update it with:"
    echo "  git remote set-url origin git@github-wfj:YOUR-ORG/op-scribe-servitor.git"
else
    echo "Identity and remote OK."
fi
