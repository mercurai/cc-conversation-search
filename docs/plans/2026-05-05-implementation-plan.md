# cc-conversation-search Implementation Plan

## Goal

Turn `mercurai/cc-conversation-search` into the durable shared transcript-resolution and session-mining layer for:

- direct CLI usage
- Claude Code packaging
- Codex packaging
- downstream autoresearch ingestion

The implementation should keep core transcript logic in one place, avoid wrapper-only drift, and make mixed Windows/Linux transcript resolution reliable.

## Constraints

- Keep transcript discovery, indexing, and mining logic in this repo, not duplicated in `claude-code-config`.
- Preserve the existing CLI commands and current package entry point:
  - `cc-conversation-search tree`
  - `cc-conversation-search mine-session`
- Treat `tree` JSON responses containing `"error"` as unresolved, even when exit code is `0`.
- Do not treat incidental mentions of a session ID in Codex logs as proof of Claude transcript resolution.
- Keep packaging adapters thin:
  - `.claude-plugin/`
  - `.codex-plugin/`
  - `skills/conversation-search/`
  - `codex-skills/claude-session-miner/`
- Mixed-environment support is required:
  - Windows transcript paths
  - `~/.claude/projects/**/<session>.jsonl`
  - local Codex-side stores used only as secondary evidence

## Current Relevant Files

- `README.md`
- `pyproject.toml`
- `src/conversation_search/cli.py`
- `src/conversation_search/core/indexer.py`
- `src/conversation_search/core/search.py`
- `src/conversation_search/core/session_miner.py`
- `src/conversation_search/core/date_utils.py`
- `src/conversation_search/data/schema.sql`
- `skills/conversation-search/SKILL.md`
- `codex-skills/claude-session-miner/SKILL.md`
- `codex-skills/claude-session-miner/scripts/mine_claude_session.py`
- `install.sh`
- `tests/test_session_miner.py`
- `tests/test_date_filtering.py`
- `tests/test_full_content_search.py`

## Implementation Principles

1. Reuse-first

Use the existing CLI and mining path as the canonical logic. Improve and extend it rather than creating new resolution code in packaging layers.

2. Seam-first

Separate core logic from adapters. Packaging layers should call stable Python entry points, not embed logic that can drift.

3. Phase-first

Land correctness and contract work before adding downstream analytics or broader harness integration.

## Phased Plan

### Phase 1: Core resolution correctness

Objective:
Make session resolution deterministic and correct across direct path input, indexed lookup, raw transcript search, and secondary stores.

Files:

- `src/conversation_search/core/session_miner.py`
- `src/conversation_search/cli.py`
- `tests/test_session_miner.py`

Actions:

- Split resolution into explicit stages:
  - CLI `tree` lookup
  - explicit transcript path
  - raw `~/.claude/projects` search
  - Codex-side filename matches as non-resolving evidence only
- Normalize and preserve Windows paths correctly.
- Fix project-path normalization so mined output does not degrade `D:\projects\...` into `D//projects/...`.
- Keep `tree` payload parsing resilient when output shape changes slightly.
- Return stable machine-readable resolution metadata from the core miner for both human and adapter use.

Verification:

- `python -m py_compile src/conversation_search/core/session_miner.py`
- `pytest tests/test_session_miner.py`
- `cc-conversation-search tree 5f21428b-056a-473c-a6f8-c2ab49aa90bb --json`
- `cc-conversation-search mine-session 5f21428b-056a-473c-a6f8-c2ab49aa90bb --transcript "C:/Users/svere/.claude/projects/D--projects-claude-code-config/5f21428b-056a-473c-a6f8-c2ab49aa90bb.jsonl"`

Dependency:

None.

### Phase 2: Shared miner contract and adapter cleanup

Objective:
Make the transcript-mining contract reusable by Claude Code, Codex, and later autoresearch consumers without duplicating logic.

Files:

- `src/conversation_search/core/session_miner.py`
- `src/conversation_search/cli.py`
- `codex-skills/claude-session-miner/SKILL.md`
- `codex-skills/claude-session-miner/scripts/mine_claude_session.py`
- `skills/conversation-search/SKILL.md`
- `README.md`

Actions:

- Promote a stable programmatic API in `session_miner.py`, not just string-building helpers.
- Add structured output support for `mine-session`, ideally `--json`.
- Keep the existing human-readable report, but build it from structured data.
- Make Codex and Claude packaging reference the same behavior and constraints:
  - `tree` for session IDs
  - explicit path fallback
  - no incidental-match resolution
- Remove or minimize adapter-local logic where the core module already provides it.

Verification:

- `python -m py_compile src/conversation_search/cli.py src/conversation_search/core/session_miner.py`
- `pytest tests/test_session_miner.py`
- compare `mine-session` text mode and JSON mode on the same target transcript

Dependency:

Phase 1.

### Phase 3: Install and packaging robustness

Objective:
Prevent the stale-launcher failure mode that broke the local install and ensure both plugin surfaces are easy to recover.

Files:

- `install.sh`
- `README.md`
- `.codex-plugin/plugin.json`
- `.claude-plugin/README.md`
- `.claude-plugin/INSTALL.md`

Actions:

- Harden `install.sh` for local repo installs via `uv tool install <repo-path>`.
- Detect and recover from stale launchers or partial local installs.
- Document the repair path for Windows Git Bash and Unix shells.
- Verify the manifest/version/docs all align with the shipped package version.
- Clarify the difference between:
  - repo contents
  - installed CLI
  - plugin registration

Verification:

- clean reinstall from local repo path
- `cc-conversation-search --version`
- `cc-conversation-search --help`
- `bash install.sh`

Dependency:

Independent, but easier once Phase 2 contract language is stable.

### Phase 4: Tests, fixtures, and mixed-environment coverage

Objective:
Add regression protection for the exact class of failures and transcript patterns seen in the mined session.

Files:

- `tests/test_session_miner.py`
- new test fixture files under `tests/fixtures/` if needed

Actions:

- Add fixtures for:
  - explicit Windows transcript path
  - `tree` returning JSON with `"error"`
  - Codex filename/session mentions that must not resolve
  - hook error attachments
  - mixed transcript record types from real Claude session structure
- Add assertions for:
  - resolved transcript path
  - preserved project path semantics
  - shell command extraction
  - file-touch extraction
  - error extraction

Verification:

- `pytest tests/test_session_miner.py`
- `pytest`

Dependency:

Phase 1 and Phase 2.

### Phase 5: Harness-facing integration hooks

Objective:
Prepare this repo to feed downstream autoresearch or harness analytics without coupling this package to `claude-code-config`.

Files:

- `src/conversation_search/core/session_miner.py`
- `README.md`
- optional new docs file if output schema needs a dedicated contract document

Actions:

- Define a stable structured output contract for downstream ingestion.
- Include:
  - resolution metadata
  - counts
  - tool calls
  - shell commands
  - files touched
  - error summaries
- Keep autoresearch DB writes out of this repo for now.
- Document how downstream systems should consume `mine-session --json`.

Verification:

- CLI JSON output matches the documented schema
- one real transcript export can be parsed by an external consumer without brittle text scraping

Dependency:

Phase 2.

## Recommended Delivery Order

1. Phase 1
2. Phase 4
3. Phase 2
4. Phase 3
5. Phase 5

Rationale:

- correctness first
- regression tests immediately after correctness
- then stabilize the public contract
- then harden install/docs
- then expose downstream integration hooks

## Risks and Assumptions

- The indexed `project_path` field already has normalization debt and may need migration logic or compatibility handling.
- Real Claude transcripts contain more record types than earlier assumptions covered.
- Text-only reports are useful for humans but are the wrong contract for downstream tooling.
- This repo should not directly own autoresearch DB side effects yet; exporting structured session facts is the safer seam.

## Rollback Notes

- If `mine-session --json` adds risk, keep the existing text report unchanged and add JSON output as an additive flag.
- If path normalization changes affect old indexed rows, prefer compatibility transforms rather than destructive DB resets.
- If installer hardening becomes platform-specific, keep the repo-path `uv tool install` path as the reference recovery mechanism.
