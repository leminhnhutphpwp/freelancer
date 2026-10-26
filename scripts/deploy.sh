#!/usr/bin/env bash
# Build and deploy with Docker Compose.
# Usage:
#   scripts/deploy.sh up          # build + start all services
#   scripts/deploy.sh build       # build images only
#   scripts/deploy.sh restart     # restart services
#   scripts/deploy.sh logs [svc]  # tail logs (api|worker|bot|db)
#   scripts/deploy.sh down        # stop and remove containers
#   scripts/deploy.sh ps          # show service status
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
require_cmd docker

ACTION="${1:-up}"
SVC="${2:-}"

case "$ACTION" in
  up)
    [[ -f .env ]] || die "Missing .env — copy .env.example and fill in secrets first."
    info "Building and starting all services..."
    docker compose up --build -d
    docker compose ps
    ;;
  build)
    docker compose build
    ;;
  restart)
    docker compose restart ${SVC:-}
    ;;
  logs)
    docker compose logs -f --tail=100 ${SVC:-}
    ;;
  down)
    docker compose down
    ;;
  ps)
    docker compose ps
    ;;
  *) die "Unknown action: $ACTION (use up|build|restart|logs|down|ps)" ;;
esac

ok "Done."