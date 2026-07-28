---
name: claude-session-miner
description: Backward-compatible Claude-only alias for the provider-aware session-miner skill.
---

# Claude Session Miner (Compatibility Alias)

Prefer the provider-aware `session-miner` skill for new work. This alias
preserves existing Claude workflows and always passes `--provider claude`.

## Workflow

1. Prefer `cc-conversation-search` for session resolution.
   - Check the CLI: `cc-conversation-search --version`
   - If the tool is missing or stale, run the repo installer:
     - `bash install.sh`
   - For session IDs, use:
     - `cc-conversation-search tree <session-id> --provider claude --json`
   - Treat JSON containing `"error"` as unresolved even when the command exits `0`.
2. For broader topic lookup, use:
   - `cc-conversation-search search "<topic>" --provider claude --json --limit 20`
   - `cc-conversation-search context <message-uuid> --provider claude --json --content`
3. For transcript mining and local fallback, use:
   - `cc-conversation-search mine-session <session-id> --provider claude` — text report
   - `cc-conversation-search mine-session <session-id> --provider claude --json` — structured machine-readable output (`schema_version: 1`)
   - If an explicit transcript path was provided:
     - `cc-conversation-search mine-session <session-id> --provider claude --transcript "<path-to-jsonl>" [--json]`
   - The `--json` flag flows through both the wrapper script and the package CLI (`cc-conversation-search mine-session ... --json`); both produce identical output for identical args.
4. Prefer evidence from the actual Claude transcript over secondary logs or summaries.

## Resolution rules

These rules govern how Claude session IDs are resolved. They are shared with
the Claude-side `conversation-search` skill; both must agree.

- **`tree` is for session IDs.** Use `cc-conversation-search tree <session-id> --json` for ID lookup.
- **`tree` JSON containing an `error` field is unresolved**, even when the command exits `0`.
- **Explicit transcript path is the preferred fallback** when the index is stale or unavailable. Pass `--transcript "<path>"`.
- **Codex-side filename matches are evidence-only.** Encountering the session
  ID inside a Codex transcript or store does not resolve a Claude session.
- `resume` expects a message UUID, not a session UUID.
- Report from evidence only.

## Output

Report with these sections:

- `Resolution`
- `Session Summary`
- `Evidence`
- `Signals`
- `Recommendations`
