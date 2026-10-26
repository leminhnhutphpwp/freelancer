#!/usr/bin/env bash
# Check API health + account connection status via the REST API.
# Usage: scripts/status.sh [base_url]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
load_env
require_cmd curl

BASE="${1:-${API_BASE_URL:-http://localhost:8001}}"
require_env ADMIN_API_KEY

info "Checking $BASE ..."

echo -e "${C_BOLD}Health${C_RESET}"
for ep in live ready; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/health/$ep" || echo 000)
  echo "  /health/$ep -> $code"
done

echo -e "${C_BOLD}Account${C_RESET}"
resp=$(curl -s -H "x-api-key: $ADMIN_API_KEY" "$BASE/api/v1/account/status" || true)
if [[ -z "$resp" ]]; then
  warn "  No response from $BASE/api/v1/account/status (is the API running?)"
else
  echo "  $resp"
fi

echo -e "${C_BOLD}Rules${C_RESET}"
rules=$(curl -s -H "x-api-key: $ADMIN_API_KEY" "$BASE/api/v1/rules" || true)
if [[ -n "$rules" ]]; then
  echo "  $rules"
else
  warn "  No rules response."
fi

ok "Status check complete."