#!/usr/bin/env bash
# Alembic migration helper.
# Usage:
#   scripts/migrate.sh upgrade [head|<rev>]
#   scripts/migrate.sh downgrade [base|<rev>]
#   scripts/migrate.sh revision "<message>"   # autogenerate a new migration
#   scripts/migrate.sh current
#   scripts/migrate.sh history
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
load_env
PY="$(python_runner)"

ACTION="${1:-upgrade}"
ARG="${2:-head}"

case "$ACTION" in
  upgrade)
    info "Running alembic upgrade $ARG..."
    "$PY" -m alembic upgrade "$ARG"
    ;;
  downgrade)
    info "Running alembic downgrade $ARG..."
    "$PY" -m alembic downgrade "$ARG"
    ;;
  revision)
    [[ -n "$ARG" ]] || die "Usage: scripts/migrate.sh revision \"<message>\""
    info "Autogenerating migration: $ARG"
    "$PY" -m alembic revision --autogenerate -m "$ARG"
    ;;
  current)
    "$PY" -m alembic current
    ;;
  history)
    "$PY" -m alembic history
    ;;
  *) die "Unknown action: $ACTION (use upgrade|downgrade|revision|current|history)" ;;
esac

ok "Done."