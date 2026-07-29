---
name: session-miner
description: Resolve and mine a Claude Code or Codex session transcript by session ID or explicit JSONL path, returning bounded, redacted evidence about prompts, tool calls, files, commands, errors, and agent activity.
allowed-tools: Bash
---

# Session Miner

Use `cc-conversation-search mine-session` for retrospective analysis of one
Claude Code or Codex session.

## Provider selection

- Default behavior remains Claude-compatible: `--provider claude`.
- Use `--provider codex` for Codex rollout transcripts.
- Use `--provider auto` only with an explicit transcript path whose provider
  should be detected structurally.

## Workflow

1. Confirm the locally checked-out fork is installed:

   ```bash
   cc-conversation-search --version
   ```

   If unavailable, run `bash install.sh` from this repository. Do not use
   `uv tool upgrade`; that can replace this fork with a different PyPI build.

2. Mine the session as structured JSON:

   ```bash
   cc-conversation-search mine-session <session-id> --provider claude --json
   cc-conversation-search mine-session <session-id> --provider codex --json
   ```

   With an explicit transcript:

   ```bash
   cc-conversation-search mine-session <session-id> \
     --provider auto --transcript "<path-to-jsonl>" --json
   ```

3. Report only evidence in the returned payload. Keep provider, resolution
   stage, project path, time range, tool counts, files, shell commands, errors,
   and recommendations distinct.

## Resolution and safety rules

- A Claude `tree --json` payload containing `error` is unresolved even when the
  command exits zero.
- A Codex filename match is evidence only until the file's `session_meta`
  matches the requested session ID.
- Explicit Codex transcripts must pass structural provider detection and
  session metadata validation.
- Never expose encrypted reasoning records, credentials, bearer tokens, or
  secret values. The miner excludes reasoning payloads and redacts common
  secret patterns; do not bypass it with direct transcript dumps.
- Transcript discovery is read-only and bounded to the provider's transcript
  roots. Do not recursively search unrelated configuration or cache trees.

## Resume commands

Use the provider-specific syntax returned by the tool:

```bash
claude --resume <session-id>
codex resume <session-id>
```
