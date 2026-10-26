#!/usr/bin/env bash
# Shared helpers for the scripts/ directory.
set -euo pipefail

# Resolve the repo root (parent of scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colours (no-op when not a TTY).
if [[ -t 1 ]]; then
  C_RESET=$'\e[0m'; C_BOLD=$'\e[1m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_RED=$'\e[31m'; C_CYAN=$'\e[36m'
else
  C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""
fi

info()  { echo -e "${C_CYAN}[info]${C_RESET} $*"; }
ok()    { echo -e "${C_GREEN}[ok]${C_RESET} $*"; }
warn()  { echo -e "${C_YELLOW}[warn]${C_RESET} $*" >&2; }
err()   { echo -e "${C_RED}[error]${C_RESET} $*" >&2; }
die()   { err "$*"; exit 1; }

# Load .env if present, WITHOUT overriding already-set environment variables.
# Values are assigned literally (no $ expansion) so tokens with '$' survive.
load_env() {
  [[ -f "$ROOT_DIR/.env" ]] || return 0
  while IFS='=' read -r key value; do
    # Skip comments, blank lines, and keys already set in the environment.
    [[ -z "$key" || "$key" == \#* ]] && continue
    if [[ -z "${!key:-}" ]]; then
      printf -v "$key" '%s' "$value"
      export "$key"
    fi
  done < <(grep -v '^[[:space:]]*#' "$ROOT_DIR/.env" | grep '=')
}

# Pick a python runner: prefer the project venv, else uv run, else system python.
python_runner() {
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    echo "$ROOT_DIR/.venv/bin/python"
  elif command -v uv >/dev/null 2>&1; then
    echo "uv run"
  else
    echo "python3"
  fi
}

# Require a non-empty env var.
require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    die "Missing required env var: $name (set it in .env or export it)"
  fi
}

# Require a command to exist.
require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "Required command not found: $cmd"
}
