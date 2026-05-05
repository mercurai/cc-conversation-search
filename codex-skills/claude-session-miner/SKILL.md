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
   - `python codex-skills/claude-session-miner/scripts/mine_claude_session.py <session-id>`
   - If an explicit transcript path was provided:
     - `python codex-skills/claude-session-miner/scripts/mine_claude_session.py <session-id> --transcript "<path-to-jsonl>"`
4. Prefer evidence from the actual Claude transcript over secondary logs or summaries.

## Constraints

- `tree` is for session IDs.
- `resume` expects a message UUID, not a session UUID.
- Do not treat incidental mentions of a session ID in Codex transcripts as proof of session resolution.
- Report from evidence only.

## Output

Report with these sections:

- `Resolution`
- `Session Summary`
- `Evidence`
- `Signals`
- `Recommendations`
