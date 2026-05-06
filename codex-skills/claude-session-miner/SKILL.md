---
name: claude-session-miner
description: Inspect, resolve, and mine Claude Code session transcripts by session ID or transcript path using cc-conversation-search first, then local transcript and research fallbacks.
---

# Claude Session Miner

Use this skill when asked to inspect, review, mine, summarize, or recover context from a Claude Code session ID, a local Claude transcript, project session history, a tool-call log, or an agent/subagent transcript.

## Workflow

1. Prefer `cc-conversation-search` for session resolution.
   - Check the CLI: `cc-conversation-search --version`
   - If the tool is missing or stale, run the repo installer:
     - `bash install.sh`
   - For session IDs, use:
     - `cc-conversation-search tree <session-id> --json`
   - Treat JSON containing `"error"` as unresolved even when the command exits `0`.
2. For broader topic lookup, use:
   - `cc-conversation-search search "<topic>" --json --limit 20`
   - `cc-conversation-search context <message-uuid> --json --content`
3. For transcript mining and local fallback, use:
   - `python codex-skills/claude-session-miner/scripts/mine_claude_session.py <session-id>` — text report
   - `python codex-skills/claude-session-miner/scripts/mine_claude_session.py <session-id> --json` — structured machine-readable output (`schema_version: 1`)
   - If an explicit transcript path was provided:
     - `python codex-skills/claude-session-miner/scripts/mine_claude_session.py <session-id> --transcript "<path-to-jsonl>" [--json]`
   - The `--json` flag flows through both the wrapper script and the package CLI (`cc-conversation-search mine-session ... --json`); both produce identical output for identical args.
4. Prefer evidence from the actual Claude transcript over secondary logs or summaries.

## Resolution rules

These rules govern how Claude session IDs are resolved. They are shared with
the Claude-side `conversation-search` skill; both must agree.

- **`tree` is for session IDs.** Use `cc-conversation-search tree <session-id> --json` for ID lookup.
- **`tree` JSON containing an `error` field is unresolved**, even when the command exits `0`.
- **Explicit transcript path is the preferred fallback** when the index is stale or unavailable. Pass `--transcript "<path>"`.
- **Codex-side filename matches are evidence-only.** Encountering the session ID inside a Codex transcript or store does NOT count as resolution.
- `resume` expects a message UUID, not a session UUID.
- Report from evidence only.

## Output

Report with these sections:

- `Resolution`
- `Session Summary`
- `Evidence`
- `Signals`
- `Recommendations`
