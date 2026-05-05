#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="cc-conversation-search"
DEFAULT_REMOTE="https://github.com/mercurai/cc-conversation-search.git"
TARGET_DIR="${HOME}/plugins/${PLUGIN_NAME}"
MARKETPLACE_PATH="${HOME}/.agents/plugins/marketplace.json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

say() {
  printf '%s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    say "Missing required command: $1"
    exit 1
  fi
}

canonical_path() {
  python - "$1" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
}

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
  mkdir -p "$(dirname "${TARGET_DIR}")"

  if [[ -d "${TARGET_DIR}/.git" ]]; then
    EXISTING_REMOTE="$(git -C "${TARGET_DIR}" remote get-url origin 2>/dev/null || true)"
    if [[ "${EXISTING_REMOTE}" != "${REMOTE_URL}" ]]; then
      BACKUP_DIR="${TARGET_DIR}.backup.$(date +%Y%m%d%H%M%S)"
      say "Existing checkout uses a different remote."
      say "Backing it up to ${BACKUP_DIR}"
      mv "${TARGET_DIR}" "${BACKUP_DIR}"
      say "Cloning ${REMOTE_URL} into ${TARGET_DIR}"
      git clone "${REMOTE_URL}" "${TARGET_DIR}"
    else
      say "Updating existing checkout at ${TARGET_DIR}"
      git -C "${TARGET_DIR}" fetch --all --prune
      git -C "${TARGET_DIR}" pull --ff-only
    fi
  elif [[ -e "${TARGET_DIR}" ]]; then
    say "Target path exists and is not a git checkout: ${TARGET_DIR}"
    exit 1
  else
    say "Cloning ${REMOTE_URL} into ${TARGET_DIR}"
    git clone "${REMOTE_URL}" "${TARGET_DIR}"
  fi

  exec "${TARGET_DIR}/install.sh"
fi

say "Installing Mercurai fork into uv tool environment"
uv tool install --force "${TARGET_DIR}"

say "Writing marketplace entry to ${MARKETPLACE_PATH}"
mkdir -p "$(dirname "${MARKETPLACE_PATH}")"

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

say "Install complete."
say "Plugin path: ${TARGET_DIR}"
say "Marketplace: ${MARKETPLACE_PATH}"
say "Start a fresh Codex session to reload plugin and skill listings."
