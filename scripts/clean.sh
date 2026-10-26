#!/usr/bin/env bash
# Clean up caches, temp files and build artifacts (keeps .env and data).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"

info "Removing Python caches..."
find app tests migrations -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find app tests migrations -name "*.pyc" -delete 2>/dev/null || true

info "Removing test/cache dirs..."
rm -rf .pytest_cache .ruff_cache .uv-cache 2>/dev/null || true

info "Removing build artifacts..."
rm -rf dist *.egg-info 2>/dev/null || true

info "Removing local sqlite data (if any)..."
rm -f data.db data.db-* 2>/dev/null || true

ok "Clean complete. (.env and backups/ are preserved)"