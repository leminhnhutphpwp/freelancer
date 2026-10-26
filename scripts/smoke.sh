#!/usr/bin/env bash
# Smoke test the Freelancer connection (read-only, safe).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
load_env
PY="$(python_runner)"

require_env FREELANCER_ACCESS_TOKEN

info "Running read-only smoke test against ${FREELANCER_BASE_URL:-https://www.freelancer.com/api}..."
"$PY" scripts/smoke.py