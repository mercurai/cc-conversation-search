---
name: claude-session-miner
description: Resolve and mine a Claude Code session transcript with Claude-specific validation and resume behavior.
allowed-tools: Bash
---

# Claude Session Miner

This is the explicit Claude Code provider entry point. Use:

```bash
cc-conversation-search mine-session <session-id> --provider claude --json
```

With an explicit transcript:

```bash
cc-conversation-search mine-session <session-id> \
  --provider claude --transcript "<path-to-jsonl>" --json
```

Treat `tree --json` output containing `error` as unresolved even if the command
exits zero. Prefer the actual transcript over secondary signals, do not dump
raw records, and resume with:

```bash
claude --resume <session-id>
```

For cross-provider or auto-detected work, use the generic `session-miner`.
