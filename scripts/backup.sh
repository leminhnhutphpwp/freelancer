#!/usr/bin/env bash
# Backup the PostgreSQL database (docker compose) to a timestamped dump.
# Usage: scripts/backup.sh [output_dir]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
require_cmd docker

OUT_DIR="${1:-$ROOT_DIR/backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/freelancer_assistant_$STAMP.sql.gz"

# Use the compose service name if running, else a standalone container.
if docker compose ps db >/dev/null 2>&1 && docker compose ps -q db >/dev/null 2>&1; then
  CONTAINER="$(docker compose ps -q db)"
  info "Backing up via compose container $CONTAINER ..."
  docker exec "$CONTAINER" pg_dump -U freelancer freelancer_assistant | gzip > "$OUT_FILE"
else
  info "Compose db not running; trying container 'freelancer-test-pg'..."
  docker exec freelancer-test-pg pg_dump -U freelancer freelancer_assistant | gzip > "$OUT_FILE"
fi

SIZE=$(du -h "$OUT_FILE" | cut -f1)
ok "Backup written: $OUT_FILE ($SIZE)"