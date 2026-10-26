#!/usr/bin/env bash
# Run the full test + lint + format suite.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
PY="$(python_runner)"

info "Running ruff lint..."
"$PY" -m ruff check app tests

info "Running ruff format check..."
"$PY" -m ruff format --check app tests

info "Running pytest..."
"$PY" -m pytest

ok "All checks passed."