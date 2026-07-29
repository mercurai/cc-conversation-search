---
name: codex-session-miner
description: Resolve and mine a Codex session transcript with session_meta validation, bounded discovery, redaction, and Codex resume behavior.
allowed-tools: Bash
---

# Codex Session Miner

This is the explicit Codex provider entry point. Use:

```bash
cc-conversation-search mine-session <session-id> --provider codex --json
```

With an explicit transcript:

```bash
cc-conversation-search mine-session <session-id> \
  --provider codex --transcript "<path-to-jsonl>" --json
```

Require the transcript's `session_meta` to match the requested session ID.
A filename match alone is evidence, not resolution. Do not print raw transcript
records, encrypted reasoning, or secret values. Discovery remains read-only
and bounded to `~/.codex/sessions`.

Resume with:

```bash
codex resume <session-id>
```

For cross-provider or auto-detected work, use the generic `session-miner`.
