# Conversation Search Plugin

This plugin provides provider-aware search and bounded, redacted transcript
mining across Claude Code and Codex history. It is Mercurai's fork of
[`akatz-ai/cc-conversation-search`](https://github.com/akatz-ai/cc-conversation-search).

## Three layers, three update paths

There are three independent install layers; understanding which layer needs an update is the most common source of confusion.

| Layer | What it is | How to update |
|---|---|---|
| **Repo checkout** | Source at `~/plugins/cc-conversation-search/` (the directory `install.sh` clones into) | `git pull` from inside the checkout |
| **Installed CLI** | The `cc-conversation-search` executable on your PATH (a `uv tool` venv built from this fork) | `bash ~/plugins/cc-conversation-search/install.sh` (do **not** use `uv tool upgrade cc-conversation-search` — see Updates section) |
| **Plugin registration** | Claude Code's plugin index that exposes the skill to your sessions | `/plugin update conversation-search` from inside Claude Code |

A `git pull` does **not** refresh the installed CLI. A `/plugin update` does
**not** refresh it either. Only this repository's `bash install.sh` refreshes
the installed fork.

## Installation

### Option 1: Install from GitHub (Recommended)

Users can install directly from this fork's GitHub repository:

```bash
# Add plugin to marketplace
/plugin marketplace add mercurai/cc-conversation-search

# Install the plugin
/plugin install conversation-search
```

This will:
- Install the conversation-search skill
- Display installation instructions for the CLI tool

### Option 2: Manual Installation

1. Clone the repository
2. Install this repository's CLI build:
   ```bash
   bash install.sh
   ```
3. Initialize one or both providers:
   ```bash
   cc-conversation-search init --provider all
   ```
4. Copy the skill to Claude Code:
   ```bash
   mkdir -p ~/.claude/skills/conversation-search
   cp skills/conversation-search/* ~/.claude/skills/conversation-search/
   ```

## Updates

When upstream publishes updates, refresh each layer separately:

```bash
# Plugin registration (Claude Code skill files)
/plugin update conversation-search

# Installed CLI (the cc-conversation-search binary on PATH) — the only
# supported refresh path for this fork:
bash ~/plugins/cc-conversation-search/install.sh
```

Do not run `uv tool upgrade cc-conversation-search` — that re-resolves from PyPI and would replace the mercurai build with the upstream package.

Pinned configuration managers should invoke
`install.sh --managed-checkout`. That mode installs from the invoking checkout
without Git network or redirect operations and verifies the enabled Codex
plugin source against the exact checkout.

If `cc-conversation-search` is on your PATH but fails, see the **Recovery** section in `INSTALL.md` — the most common failure mode is a launcher left over from a removed `uv tool` environment.

## What's Included

- **Skills**: `conversation-search`, generic `session-miner`, and explicit
  `claude-session-miner` / `codex-session-miner`
- **CLI Tool**: `cc-conversation-search`
- **Database**: Local provider-qualified SQLite index

## Requirements

- Claude Code and/or Codex transcripts
- Python 3.9+
- `uv` for the repository installer
