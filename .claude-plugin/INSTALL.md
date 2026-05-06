# Installation Instructions

Thank you for installing the **conversation-search** plugin!

## Step 1: Install the CLI Tool

The skill requires the `conversation-search` CLI tool.

**Note**: The package name is `cc-conversation-search` but the command is `conversation-search`.

### Recommended: Using uv
```bash
uv tool install cc-conversation-search
```

### Alternative: Using pip
```bash
pip install cc-conversation-search
```

## Step 2: Initialize the Database

Create the search index for your conversation history:

```bash
cc-conversation-search init
```

This will:
- Create `~/.conversation-search/index.db`
- Index your last 7 days of conversations
- Extract searchable content using smart hybrid extraction (instant, no AI calls)

## Step 3: Test the Installation

Verify everything is working:

```bash
cc-conversation-search search "test" --json
```

## You're Ready!

The **conversation-search** skill is now active. Try asking Claude:

- "Find that message where we discussed authentication"
- "What did we talk about regarding React hooks?"
- "Locate the conversation where we fixed the database bug"

Claude will use a progressive search strategy to find specific message UUIDs you can branch from.

## Troubleshooting

**Tool not found:**
- Make sure `cc-conversation-search` is in your PATH
- Try: `which cc-conversation-search`

**No conversations found:**
- Verify `~/.claude/projects/` exists and contains .jsonl files
- Try: `cc-conversation-search list --days 30`

**Stale launcher (CLI on PATH but broken):**

If `cc-conversation-search` is on your PATH but fails, the launcher may be
left over from a removed `uv tool` environment. Repair on Windows Git Bash:

```bash
uv tool uninstall cc-conversation-search 2>/dev/null || true
rm -f "$HOME/.local/bin/cc-conversation-search.exe"
bash ~/plugins/cc-conversation-search/install.sh
```

On Linux/macOS, drop the `.exe`:

```bash
uv tool uninstall cc-conversation-search 2>/dev/null || true
rm -f "$HOME/.local/bin/cc-conversation-search"
bash ~/plugins/cc-conversation-search/install.sh
```

The `|| true` matters: in the stale state, `uv tool list` does not claim
the tool, so `uv tool uninstall` exits non-zero — that is expected and must
not abort the recovery before the launcher file is removed.

If you do not yet have a repo checkout, clone it first:

```bash
git clone https://github.com/mercurai/cc-conversation-search ~/plugins/cc-conversation-search
bash ~/plugins/cc-conversation-search/install.sh
```

Do not run `uv tool upgrade cc-conversation-search` — that re-resolves from
PyPI and would replace this mercurai build with the upstream package.

**For help:**
- Documentation: https://github.com/mercurai/cc-conversation-search
- Issues: https://github.com/mercurai/cc-conversation-search/issues
