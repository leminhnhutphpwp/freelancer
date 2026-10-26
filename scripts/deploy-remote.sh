#!/usr/bin/env bash
# Deploy the Freelancer Assistant to the production server via SSH + rsync.
#
# Usage:
#   scripts/deploy-remote.sh            # full deploy: sync + build + start + health check
#   scripts/deploy-remote.sh sync       # rsync source + .env only
#   scripts/deploy-remote.sh up         # docker compose up --build -d on server
#   scripts/deploy-remote.sh restart    # docker compose restart on server
#   scripts/deploy-remote.sh logs [svc] # tail logs (api|worker|bot|db)
#   scripts/deploy-remote.sh ps         # show service status
#   scripts/deploy-remote.sh down       # stop and remove containers
#   scripts/deploy-remote.sh status     # health + account status + rules
#   scripts/deploy-remote.sh nginx      # install nginx vhost + certbot SSL
#
# Environment (override as needed):
#   DEPLOY_HOST   (default: root@your-server)
#   DEPLOY_DIR    (default: /srv/freelancer)
#   DEPLOY_ENV    (default: .env.production  → copied to server as .env)
#   API_PORT      (default: 8001 — host port mapped to container api:8000)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"

DEPLOY_HOST="${DEPLOY_HOST:-root@your-server}"
DEPLOY_DIR="${DEPLOY_DIR:-/srv/freelancer}"
DEPLOY_ENV="${DEPLOY_ENV:-.env.production}"
API_PORT="${API_PORT:-8001}"

require_cmd ssh
require_cmd rsync

ACTION="${1:-deploy}"
SVC="${2:-}"

# Files/dirs that must never be copied to the server.
RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude '.venv/'
  --exclude '.uv-cache/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.pytest_cache/'
  --exclude '.ruff_cache/'
  --exclude '.coverage'
  --exclude 'data/'
  --exclude 'backups/'
  --exclude '.env'
  --exclude '.env.production'
  --exclude 'dist/'
  --exclude '*.egg-info/'
)

remote() { ssh "$DEPLOY_HOST" "$@"; }

do_sync() {
  info "Syncing source → $DEPLOY_HOST:$DEPLOY_DIR ..."
  remote "mkdir -p '$DEPLOY_DIR'"
  rsync -az --delete \
    "${RSYNC_EXCLUDES[@]}" \
    -e ssh \
    ./ "$DEPLOY_HOST:$DEPLOY_DIR/"

  # Push the production env file as .env on the server (never committed to git).
  if [[ -f "$DEPLOY_ENV" ]]; then
    info "Pushing $DEPLOY_ENV → $DEPLOY_DIR/.env ..."
    rsync -az -e ssh "$DEPLOY_ENV" "$DEPLOY_HOST:$DEPLOY_DIR/.env"
  else
    warn "No $DEPLOY_ENV found — skipping .env push (server keeps existing .env)."
  fi
  ok "Sync complete."
}

do_up() {
  info "Building and starting containers on server ..."
  remote "cd '$DEPLOY_DIR' && docker compose up --build -d"
  remote "cd '$DEPLOY_DIR' && docker compose ps"
}

do_health() {
  info "Waiting for API health check ..."
  local url="http://127.0.0.1:${API_PORT}/health/live"
  local i
  for i in $(seq 1 30); do
    if remote "curl -fsS '$url' 2>/dev/null"; then
      echo
      ok "API is healthy at $url"
      return 0
    fi
    sleep 2
  done
  err "API did not become healthy within 60s."
  remote "cd '$DEPLOY_DIR' && docker compose logs --tail=50 api" || true
  return 1
}

case "$ACTION" in
  deploy)
    do_sync
    do_up
    do_health
    ;;
  sync)   do_sync ;;
  up)     do_up ;;
  restart)
    remote "cd '$DEPLOY_DIR' && docker compose restart ${SVC:-}"
    ;;
  logs)
    remote "cd '$DEPLOY_DIR' && docker compose logs -f --tail=100 ${SVC:-}"
    ;;
  ps)
    remote "cd '$DEPLOY_DIR' && docker compose ps"
    ;;
  down)
    remote "cd '$DEPLOY_DIR' && docker compose down"
    ;;
  status)
    remote "curl -fsS 'http://127.0.0.1:${API_PORT}/health/live' && echo"
    remote "curl -fsS -H 'x-api-key: \$(grep -E '^ADMIN_API_KEY=' '$DEPLOY_DIR/.env' | cut -d= -f2)' 'http://127.0.0.1:${API_PORT}/api/v1/account/status' && echo"
    ;;
  nginx)
    info "Installing nginx vhost for freelancer.example.com ..."
    remote "cp '$DEPLOY_DIR/deploy/freelancer.example.conf' /etc/nginx/sites-available/freelancer.example.com"
    remote "ln -sf /etc/nginx/sites-available/freelancer.example.com /etc/nginx/sites-enabled/freelancer.example.com"
    remote "nginx -t"
    remote "systemctl reload nginx"
    if remote "test -f /etc/letsencrypt/live/freelancer.example.com/fullchain.pem"; then
      info "Certificate already exists — skipping certbot."
    else
      info "Requesting Let's Encrypt certificate ..."
      remote "certbot --nginx -d freelancer.example.com --non-interactive --agree-tos --redirect"
    fi
    remote "nginx -t && systemctl reload nginx"
    ok "nginx + SSL configured."
    ;;
  *) die "Unknown action: $ACTION (use deploy|sync|up|restart|logs|ps|down|status|nginx)" ;;
esac

ok "Done."