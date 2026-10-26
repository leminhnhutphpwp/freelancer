#!/usr/bin/env bash
# Manage search rules via the REST API.
# Usage:
#   scripts/rule.sh list
#   scripts/rule.sh create <name> [json_file]     # json_file optional; else prompts via heredoc
#   scripts/rule.sh test <rule_id>                # dry-run scan (no notify)
#   scripts/rule.sh scan <rule_id>                # force scan + notify
#   scripts/rule.sh delete <rule_id>
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$ROOT_DIR"
load_env
require_cmd curl

BASE="${API_BASE_URL:-http://localhost:8001}"
require_env ADMIN_API_KEY
AUTH=(-H "x-api-key: $ADMIN_API_KEY" -H "Content-Type: application/json")

ACTION="${1:-list}"

case "$ACTION" in
  list)
    curl -s "${AUTH[@]}" "$BASE/api/v1/rules" | python3 -m json.tool
    ;;
  create)
    NAME="${2:?Usage: scripts/rule.sh create <name> [json_file]}"
    FILE="${3:-}"
    if [[ -n "$FILE" ]]; then
      BODY="@$FILE"
    else
      BODY=$(cat <<JSON
{"name": "$NAME", "filters": {"query": "python"}, "match": {"must_keywords": ["fastapi"]}}
JSON
      )
    fi
    curl -s -X POST "${AUTH[@]}" -d "$BODY" "$BASE/api/v1/rules" | python3 -m json.tool
    ;;
  test)
    ID="${2:?Usage: scripts/rule.sh test <rule_id>}"
    curl -s -X POST "${AUTH[@]}" "$BASE/api/v1/rules/$ID/test" | python3 -m json.tool
    ;;
  scan)
    ID="${2:?Usage: scripts/rule.sh scan <rule_id>}"
    curl -s -X POST "${AUTH[@]}" "$BASE/api/v1/rules/$ID/scan" | python3 -m json.tool
    ;;
  delete)
    ID="${2:?Usage: scripts/rule.sh delete <rule_id>}"
    curl -s -X DELETE "${AUTH[@]}" -o /dev/null -w "HTTP %{http_code}\n" "$BASE/api/v1/rules/$ID"
    ;;
  *) die "Unknown action: $ACTION (use list|create|test|scan|delete)" ;;
esac