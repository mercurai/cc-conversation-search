---
name: codex-session-miner
description: Resolve and mine a Codex session transcript with session_meta validation, bounded discovery, redaction, and provider-native resume behavior.
---

# Codex Session Miner

Use this explicit provider skill for Codex sessions:

```bash
cc-conversation-search mine-session <session-id> --provider codex --json
cc-conversation-search mine-session <session-id> \
  --provider codex --transcript "<path-to-jsonl>" --json
```

Require matching `session_meta`; filename matches alone are evidence. Keep
discovery read-only and bounded to `~/.codex/sessions`. Never print raw
encrypted reasoning or secrets. Resume with `codex resume <session-id>`.

Use the generic `session-miner` only when provider selection is intentionally
cross-provider or auto-detected.
