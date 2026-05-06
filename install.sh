#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="cc-conversation-search"
DEFAULT_REMOTE="https://github.com/mercurai/cc-conversation-search.git"
TARGET_DIR="${HOME}/plugins/${PLUGIN_NAME}"
MARKETPLACE_PATH="${HOME}/.agents/plugins/marketplace.json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) say_unknown_arg=1; bad_arg="$arg" ;;
  esac
done

say() {
  printf '%s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    say "Missing required command: $1"
    exit 1
  fi
}

if [[ "${say_unknown_arg:-0}" == "1" ]]; then
  say "Unknown argument: ${bad_arg:-}"
  say "Usage: $0 [--dry-run]"
  exit 2
fi

run_or_print() {
  # Execute a command, or in dry-run mode print it as a `[DRY-RUN]` trace line.
  if [[ "${DRY_RUN}" == "1" ]]; then
    say "[DRY-RUN] $*"
  else
    "$@"
  fi
}

canonical_path() {
  python - "$1" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
}

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*|Windows*) LAUNCHER_NAME="cc-conversation-search.exe" ;;
  *) LAUNCHER_NAME="cc-conversation-search" ;;
esac
LAUNCHER_PATH="${HOME}/.local/bin/${LAUNCHER_NAME}"

# ---------------------------------------------------------------------------
# Pre-flight
#
# Distinguish three on-PATH states:
#   1. healthy: launcher exists AND uv tool list claims it
#   2. stale:   launcher exists in ~/.local/bin BUT uv tool list does not claim it
#   3. foreign: cc-conversation-search resolves to a path uv tool list does
#              not own (e.g. pip --user, brew, system Python)
#
# INSTALL_FORCE_STALE=1 forces the trace to report the stale-recovery branch
# in --dry-run mode, so the recovery code path is verifiable without
# destroying the working install.
# ---------------------------------------------------------------------------

preflight_state() {
  local on_path
  on_path="$(command -v cc-conversation-search 2>/dev/null || true)"

  if [[ -z "${on_path}" ]]; then
    echo "absent"
    return
  fi

  local uv_owned
  uv_owned="$(uv tool list 2>/dev/null | grep -E '^cc-conversation-search' || true)"

  if [[ "${INSTALL_FORCE_STALE:-0}" == "1" && "${DRY_RUN}" == "1" ]]; then
    echo "stale (forced via INSTALL_FORCE_STALE=1)"
    return
  fi

  if [[ -n "${uv_owned}" && -f "${LAUNCHER_PATH}" ]]; then
    echo "healthy (${on_path})"
  elif [[ -f "${LAUNCHER_PATH}" && -z "${uv_owned}" ]]; then
    echo "stale (launcher exists, uv tool list does not claim it)"
  else
    # On PATH but not at the expected ~/.local/bin location and not in uv tool list.
    echo "foreign (resolves to ${on_path}, not owned by uv tool list)"
  fi
}

recover_stale_launcher() {
  say "Recovering from stale launcher at ${LAUNCHER_PATH}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    say "[DRY-RUN] uv tool uninstall cc-conversation-search (idempotent; non-zero exit tolerated)"
  else
    # In the stale state, `uv tool list` does not claim cc-conversation-search,
    # so `uv tool uninstall` will exit non-zero. That's expected and must NOT
    # abort the script under `set -e`. Tolerate any exit code so we still
    # proceed to the launcher-removal step below.
    uv tool uninstall cc-conversation-search 2>/dev/null || true
  fi
  run_or_print rm -f "${LAUNCHER_PATH}"
}

# ---------------------------------------------------------------------------
# Repo checkout: clone or update into the canonical plugin path
# ---------------------------------------------------------------------------

REMOTE_URL="$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null || true)"
if [[ -z "${REMOTE_URL}" ]]; then
  REMOTE_URL="${DEFAULT_REMOTE}"
fi

require_cmd git
require_cmd python
require_cmd uv

TARGET_CANONICAL="$(canonical_path "${TARGET_DIR}")"
ROOT_CANONICAL="$(canonical_path "${REPO_ROOT}")"

if [[ "${ROOT_CANONICAL}" != "${TARGET_CANONICAL}" ]]; then
  say "Installing repo into canonical plugin path: ${TARGET_DIR}"
  run_or_print mkdir -p "$(dirname "${TARGET_DIR}")"

  if [[ -d "${TARGET_DIR}/.git" ]]; then
    EXISTING_REMOTE="$(git -C "${TARGET_DIR}" remote get-url origin 2>/dev/null || true)"
    if [[ "${EXISTING_REMOTE}" != "${REMOTE_URL}" ]]; then
      BACKUP_DIR="${TARGET_DIR}.backup.$(date +%Y%m%d%H%M%S)"
      say "Existing checkout uses a different remote."
      say "Backing it up to ${BACKUP_DIR}"
      run_or_print mv "${TARGET_DIR}" "${BACKUP_DIR}"
      say "Cloning ${REMOTE_URL} into ${TARGET_DIR}"
      run_or_print git clone "${REMOTE_URL}" "${TARGET_DIR}"
    else
      say "Updating existing checkout at ${TARGET_DIR}"
      run_or_print git -C "${TARGET_DIR}" fetch --all --prune
      run_or_print git -C "${TARGET_DIR}" pull --ff-only
    fi
  elif [[ -e "${TARGET_DIR}" ]]; then
    say "Target path exists and is not a git checkout: ${TARGET_DIR}"
    exit 1
  else
    say "Cloning ${REMOTE_URL} into ${TARGET_DIR}"
    run_or_print git clone "${REMOTE_URL}" "${TARGET_DIR}"
  fi

  if [[ "${DRY_RUN}" == "1" ]]; then
    say "[DRY-RUN] would re-exec ${TARGET_DIR}/install.sh from the canonical checkout"
    exit 0
  fi
  exec "${TARGET_DIR}/install.sh"
fi

# ---------------------------------------------------------------------------
# Pre-flight: detect stale or foreign launchers before install
# ---------------------------------------------------------------------------

state="$(preflight_state)"
say "Pre-flight: launcher state = ${state}"

case "${state}" in
  stale*)
    recover_stale_launcher
    ;;
  foreign*)
    say "Aborting: cc-conversation-search on PATH is not owned by 'uv tool list'."
    say "It resolves outside ${HOME}/.local/bin or is not registered with uv."
    say "Remove the foreign installation, then re-run this script. Repair commands:"
    say "  pip uninstall -y cc-conversation-search 2>/dev/null"
    say "  rm -f \"\$(command -v cc-conversation-search 2>/dev/null)\""
    if [[ "${DRY_RUN}" != "1" ]]; then
      exit 1
    fi
    say "[DRY-RUN] would have aborted; continuing trace."
    ;;
  healthy*|absent)
    : # no recovery needed
    ;;
esac

# ---------------------------------------------------------------------------
# Install: uv tool install --force
# ---------------------------------------------------------------------------

say "Installing Mercurai fork into uv tool environment"
run_or_print uv tool install --force "${TARGET_DIR}"

# ---------------------------------------------------------------------------
# Marketplace registration
# ---------------------------------------------------------------------------

say "Writing marketplace entry to ${MARKETPLACE_PATH}"
run_or_print mkdir -p "$(dirname "${MARKETPLACE_PATH}")"

if [[ "${DRY_RUN}" == "1" ]]; then
  say "[DRY-RUN] would update marketplace entry for cc-conversation-search at ${MARKETPLACE_PATH}"
else
  python - "${MARKETPLACE_PATH}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

plugin_entry = {
    "name": "cc-conversation-search",
    "source": {
        "source": "local",
        "path": "./plugins/cc-conversation-search",
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Productivity",
}

if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
else:
    data = {
        "name": "mercurai-local-plugins",
        "interface": {"displayName": "Mercurai Local Plugins"},
        "plugins": [],
    }

plugins = data.setdefault("plugins", [])
for idx, plugin in enumerate(plugins):
    if plugin.get("name") == plugin_entry["name"]:
        plugins[idx] = plugin_entry
        break
else:
    plugins.append(plugin_entry)

data.setdefault("name", "mercurai-local-plugins")
data.setdefault("interface", {"displayName": "Mercurai Local Plugins"})
data["interface"].setdefault("displayName", "Mercurai Local Plugins")

path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
fi

# ---------------------------------------------------------------------------
# Post-install verification
# ---------------------------------------------------------------------------

if [[ "${DRY_RUN}" == "1" ]]; then
  say "[DRY-RUN] would run: cc-conversation-search --version"
  say "[DRY-RUN] complete. Plugin path: ${TARGET_DIR}"
  exit 0
fi

say "Verifying installed CLI"
if installed_version="$(cc-conversation-search --version 2>&1)"; then
  say "  ${installed_version}"
else
  say "ERROR: cc-conversation-search --version failed after install."
  say "  See INSTALL.md and README.md for stale-launcher recovery steps."
  exit 1
fi

say "Install complete."
say "Plugin path: ${TARGET_DIR}"
say "Marketplace: ${MARKETPLACE_PATH}"
say "Start a fresh Codex session to reload plugin and skill listings."
