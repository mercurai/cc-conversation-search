---
name: session-miner
description: Resolve and mine Claude Code or Codex session transcripts with provider-specific validation, redaction, search, and resume behavior.
---

# Session Miner

Use this skill when asked to inspect, review, mine, summarize, or recover
context from a Claude Code or Codex session ID or transcript.

## Commands

```bash
cc-conversation-search mine-session <session-id> --provider claude --json
cc-conversation-search mine-session <session-id> --provider codex --json
cc-conversation-search mine-session <session-id> \
  --provider auto --transcript "<path-to-jsonl>" --json
```

If the CLI is missing or stale, run the repository's `bash install.sh`.

## Rules

- Claude remains the default provider for backward compatibility.
- Treat Claude `tree --json` output containing `error` as unresolved.
- Validate Codex candidates against `session_meta`; filename matches alone are
  evidence, not resolution.
- Prefer the actual provider transcript over secondary database signals.
- Do not print raw transcript records, encrypted reasoning, or secrets. Use the
  miner's bounded, redacted JSON output.
- Resume Claude with `claude --resume <id>` and Codex with
  `codex resume <id>`.

Report `Resolution`, `Session Summary`, `Evidence`, `Signals`, and
`Recommendations`.
