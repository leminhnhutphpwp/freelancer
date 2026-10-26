#!/usr/bin/env bash
# Run the local dev stack (API + worker + bot) with uv, each in its own terminal.
# Usage: scripts/dev.sh [api|worker|bot|all]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
load_env
require_cmd uv

TARGET="${1:-all}"

run_api() {
  info "Starting API on :8001 (docs at /api/v1/docs)..."
  uv run uvicorn app.main:app --reload --port 8000
}

run_worker() {
  info "Starting worker..."
  uv run python -m app.worker
}

run_bot() {
  info "Starting Telegram bot..."
  uv run python -m app.telegram.bot
}

case "$TARGET" in
  api)    run_api ;;
  worker) run_worker ;;
  bot)    run_bot ;;
  all)
    info "Starting API, worker and bot in separate terminals..."
    # macOS
    if command -v osascript >/dev/null 2>&1; then
      osascript -e "tell application \"Terminal\" to do script \"cd '$ROOT_DIR' && ./scripts/dev.sh api\"" >/dev/null
      osascript -e "tell application \"Terminal\" to do script \"cd '$ROOT_DIR' && ./scripts/dev.sh worker\"" >/dev/null
      osascript -e "tell application \"Terminal\" to do script \"cd '$ROOT_DIR' && ./scripts/dev.sh bot\"" >/dev/null
    # Linux with gnome-terminal
    elif command -v gnome-terminal >/dev/null 2>&1; then
      gnome-terminal -- bash -c "cd '$ROOT_DIR' && ./scripts/dev.sh api; exec bash"
      gnome-terminal -- bash -c "cd '$ROOT_DIR' && ./scripts/dev.sh worker; exec bash"
      gnome-terminal -- bash -c "cd '$ROOT_DIR' && ./scripts/dev.sh bot; exec bash"
    else
      warn "No terminal multiplexer found; starting all three in the background (logs to /tmp)."
      nohup uv run uvicorn app.main:app --port 8000 >/tmp/fa-api.log 2>&1 &
      nohup uv run python -m app.worker >/tmp/fa-worker.log 2>&1 &
      nohup uv run python -m app.telegram.bot >/tmp/fa-bot.log 2>&1 &
      info "Background PIDs: api=$! (see /tmp/fa-*.log)"
    fi
    ;;
  *) die "Unknown target: $TARGET (use api|worker|bot|all)" ;;
esac