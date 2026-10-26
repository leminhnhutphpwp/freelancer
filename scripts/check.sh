#!/usr/bin/env bash
# Check environment prerequisites and report configuration status.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
load_env

echo -e "${C_BOLD}Environment check${C_RESET}"
echo "  Python:  $(python3 --version 2>&1 || echo MISSING)"
echo "  uv:      $(uv --version 2>&1 || echo MISSING)"
echo "  Docker:  $(docker --version 2>&1 || echo MISSING)"
echo "  .env:    $([[ -f .env ]] && echo present || echo MISSING)"
echo "  venv:    $([[ -d .venv ]] && echo present || echo MISSING)"

echo
echo -e "${C_BOLD}Configuration${C_RESET}"
echo "  FREELANCER_ACCESS_TOKEN: $([[ -n "${FREELANCER_ACCESS_TOKEN:-}" ]] && echo SET || echo EMPTY)"
echo "  FREELANCER_BASE_URL:     ${FREELANCER_BASE_URL:-https://www.freelancer.com/api}"
echo "  DATABASE_URL:            ${DATABASE_URL:-<default>}"
echo "  ADMIN_API_KEY:           $([[ -n "${ADMIN_API_KEY:-}" ]] && echo SET || echo EMPTY)"
echo "  TELEGRAM_BOT_TOKEN:      $([[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && echo SET || echo EMPTY)"
echo "  TELEGRAM_ALLOWED_CHAT_IDS: ${TELEGRAM_ALLOWED_CHAT_IDS:-<empty>}"

echo
echo -e "${C_BOLD}Recommendations${C_RESET}"
[[ -f .env ]] || warn "  Create .env from .env.example"
[[ -n "${FREELANCER_ACCESS_TOKEN:-}" ]] || warn "  Set FREELANCER_ACCESS_TOKEN to connect your account"
[[ -n "${ADMIN_API_KEY:-}" ]] || warn "  Set ADMIN_API_KEY (openssl rand -hex 32)"
[[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] || warn "  Set TELEGRAM_BOT_TOKEN to enable Telegram"
[[ -n "${TELEGRAM_ALLOWED_CHAT_IDS:-}" ]] || warn "  Set TELEGRAM_ALLOWED_CHAT_IDS to allow bot commands"

ok "Check complete."