#!/bin/bash
# quickstart.sh — Set up a basic Verse swarm in one command
#
# Usage:
#   ./examples/quickstart.sh <golden-commit-id>
#
# This will:
# 1. Verify VERS_API_KEY is set
# 2. Create 3 lieutenants (backend, frontend, infra)
# 3. Verify they're running
# 4. Show dashboard
#
# Author: Carter Schonwald
# Date: 2026-04-03

set -e

GOLDEN_COMMIT="${1:-}"

# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────

if [ -z "$VERS_API_KEY" ]; then
    echo "❌ VERS_API_KEY not set"
    echo ""
    echo "Set it with:"
    echo '  export VERS_API_KEY="your-api-key"'
    echo ""
    echo "Or add to your shell profile:"
    echo '  echo '\''export VERS_API_KEY="your-key"'\'' >> ~/.bashrc'
    echo '  echo '\''export VERS_API_KEY="your-key"'\'' >> ~/.config/fish/config.fish'
    exit 1
fi

if [ -z "$GOLDEN_COMMIT" ]; then
    echo "Usage: $0 <golden-commit-id>"
    echo ""
    echo "Find commit IDs with:"
    echo "  uv run scripts/vers_api.py commits-public"
    echo "  uv run scripts/vers_api.py commits"
    echo ""
    echo "Or create a new golden image:"
    echo "  See: verse_swarm_setup.md Step 2"
    exit 1
fi

echo "🚀 Verse Swarm Quickstart"
echo "━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Golden commit: $GOLDEN_COMMIT"
echo "API key: ${VERS_API_KEY:0:20}..."
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# Check if lieutenants already exist
# ──────────────────────────────────────────────────────────────────────────────

STATE_PATH="$HOME/.vers/lieutenants.json"

if [ -f "$STATE_PATH" ]; then
    EXISTING=$(jq -r '.lieutenants | keys | join(", ")' "$STATE_PATH" 2>/dev/null || echo "")
    if [ -n "$EXISTING" ]; then
        echo "⚠️  Existing lieutenants found: $EXISTING"
        echo ""
        read -p "Destroy and recreate? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "🗑️  Destroying existing lieutenants..."
            uv run scripts/lt.py lt-destroy "*"
        else
            echo "Keeping existing lieutenants. Exiting."
            exit 0
        fi
    fi
fi

# ──────────────────────────────────────────────────────────────────────────────
# Create lieutenants
# ──────────────────────────────────────────────────────────────────────────────

echo "📦 Creating lieutenants..."
echo ""

lieutenants=(
    "backend:backend API and database:⚙️"
    "frontend:UI and components:🎨"
    "infra:deployment and infrastructure:🏗️"
)

for entry in "${lieutenants[@]}"; do
    IFS=':' read -r name role icon <<< "$entry"
    echo "$icon  Creating $name..."
    uv run scripts/lt.py lt-create "$name" "$role" "$GOLDEN_COMMIT"
    echo ""
done

# ──────────────────────────────────────────────────────────────────────────────
# Verify
# ──────────────────────────────────────────────────────────────────────────────

echo "🔍 Verifying lieutenants..."
echo ""
sleep 2

uv run scripts/lt.py lt-status --probe

echo ""
echo "✅ Swarm ready!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Next steps:"
echo ""
echo "1. Send a task:"
echo "   uv run scripts/lt.py lt-send backend 'Create FastAPI hello world'"
echo ""
echo "2. Monitor output:"
echo "   uv run scripts/lt.py lt-read backend --follow"
echo ""
echo "3. Run a workflow:"
echo "   uv run examples/swarm_harness.py run examples/workflow_example.json"
echo ""
echo "4. Dashboard:"
echo "   uv run examples/swarm_harness.py dashboard"
echo ""
echo "5. When idle, pause to save costs:"
echo "   uv run scripts/lt.py lt-pause backend"
echo "   uv run scripts/lt.py lt-pause frontend"
echo "   uv run scripts/lt.py lt-pause infra"
echo ""
echo "6. Destroy when done:"
echo "   uv run scripts/lt.py lt-destroy '*'"
echo ""
