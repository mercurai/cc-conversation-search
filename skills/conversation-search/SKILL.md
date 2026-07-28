---
name: conversation-search
description: Find and resume Claude Code and Codex conversations by provider, topic, project, or date. Returns provider-qualified session IDs and exact resume commands.
allowed-tools: Bash, TodoWrite
---

# Conversation Search

Find past Claude Code or Codex conversations and get the provider-specific
commands to resume them. Claude is the default for backward compatibility.

## See also

For **transcript mining** (resolving a single session and parsing it into structured
facts — tool calls, files touched, errors, recommendations), use the
`session-miner` skill or run
`cc-conversation-search mine-session <id> --provider <provider> --json`
directly. This skill covers search, listing, and resumption only.

## MANDATORY FIRST STEP - CREATE TODO CHECKLIST

**Before doing ANYTHING else, you MUST use the TodoWrite tool to create this exact checklist:**

```
- Ensure the local cc-conversation-search fork is installed
- Select provider (claude, codex, or all)
- Classify query type (temporal/topic/hybrid)
- Execute Level 1: focused search with cc-conversation-search
- Execute Level 2: broader search if Level 1 fails
- Execute Level 3: manual exploration if Level 1 and 2 fail
- Present results to user
```

**CRITICAL CONSTRAINTS:**
- DO NOT use grep, find, cat, or any manual file operations on .jsonl files
- DO NOT skip the todo creation step
- DO NOT jump to Level 3 without attempting Levels 1 and 2
- ONLY use cc-conversation-search commands for all search operations

Mark each todo as `in_progress` when starting it, `completed` when done.

## Prerequisites & Auto-Installation

The skill requires the `cc-conversation-search` CLI tool (v0.4.0+ minimum).

**First todo: Ensure the local fork is installed**

```bash
if command -v cc-conversation-search &> /dev/null; then
    cc-conversation-search --version
else
    bash install.sh
    cc-conversation-search init --days 7 --provider claude
fi
```

**If installation fails**, guide the user:
```
The conversation-search plugin requires the cc-conversation-search CLI tool.

Install the Mercurai fork from its checkout:
  bash install.sh

Then initialize:
  cc-conversation-search init --provider claude
```

**Do not proceed with search** until installation is confirmed.
Do not run `uv tool upgrade cc-conversation-search`; it may replace this fork
with a provider-incomplete PyPI build.

## Provider selection

- `--provider claude`: Claude Code only; default and backward-compatible.
- `--provider codex`: Codex only.
- `--provider all`: search or list both providers.

Use a single provider for `context`, `tree`, and `resume`, because message and
session IDs can overlap across providers.

## Query Type Classification

**Second todo: Classify the user's query**

Determine which type before executing search:

### Type 1: Temporal Queries
User asks about time periods WITHOUT specific topics:
- "What did we work on yesterday?"
- "Summarize this week"
- "Show today's conversations"

**Action:** Use `list` command with date filters

### Type 2: Topic Queries
User asks about CONTENT/TOPICS:
- "Find that Redis conversation"
- "Where did we discuss authentication?"
- "Show me where we worked on the API"

**Action:** Use `search "topic"` command

### Type 3: Hybrid Queries
User asks about TOPIC + TIME:
- "Show me yesterday's authentication work"
- "Find Redis discussions from last week"
- "How many times did you say X in the past week?"

**Action:** Use `search "topic"` with date filters

## Three-Level Search Workflow

**Execute in order. Do not skip levels.**

### Level 1: Focused Search (ALWAYS START HERE)

Based on query classification:

**For Topic or Hybrid queries:**
```bash
cc-conversation-search search "search terms" --provider <claude|codex|all> --days 14 --json
```

**For Temporal queries:**
```bash
cc-conversation-search list --provider <claude|codex|all> --date yesterday --json
```

**Parse the JSON output.** If you find relevant matches → skip to Level 4 (present results).

**Note:** Search auto-indexes recent conversations for fresh data.

### Level 2: Broader Search

**Only if Level 1 found nothing useful.**

For topic/hybrid queries:
- Remove time constraints:
  `cc-conversation-search search "terms" --provider <provider> --json`
- Try alternative keywords: "auth" vs "authentication"
- Try broader terms: "database" vs "postgres"

For temporal queries:
- Expand time range: `--days 30` instead of `--days 7`

**If matches found** → skip to Level 4.

### Level 3: Manual Exploration

**Only if Levels 1 and 2 both failed.**

1. List conversations:
   `cc-conversation-search list --provider <provider> --days 30 --json`
2. Review conversation summaries in JSON
3. For promising sessions:
   `cc-conversation-search tree <SESSION_ID> --provider <claude|codex> --json`
4. Read message summaries to locate content

### Level 4: Present Results

**Format results for the user:**

For found conversations:

```markdown
**Session Details**
- **Session**: abc-123-session-id
- **Project**: /home/user/projects/myproject
- **Time**: 2025-11-13 22:50
- **Message**: def-456-message-uuid (if applicable)

**To Resume This Conversation**
```bash
cd /home/user/projects/myproject
claude --resume abc-123-session-id
# or
codex resume abc-123-session-id
```
```

For counting/analysis queries:
- Parse JSON results
- Filter by message_type if needed (user vs assistant)
- Count matches
- Present clear answer with evidence

**If not found after all 3 levels:**
- "No matching conversations found after exhaustive search"
- Suggest: `cc-conversation-search index --days 90` to reindex older history
- "The conversation may not exist or may be older than indexed range"

## Command Reference

### Search (for topic and hybrid queries)
```bash
# With time scope
cc-conversation-search search "query" --days N --json

# Specific date
cc-conversation-search search "query" --date yesterday --json
cc-conversation-search search "query" --date 2025-11-13 --json

# Date range
cc-conversation-search search "query" --since 2025-11-10 --until 2025-11-13 --json

# All time
cc-conversation-search search "query" --json
```

**Date filter options:**
- `--days N`: Last N days from now
- `--date DATE`: Specific calendar day
- `--since DATE`: From date onwards
- `--until DATE`: Up to date (inclusive)
- DATE formats: `yyyy-mm-dd`, `yesterday`, `today`
- Cannot mix `--days` with `--date/--since/--until`

### List (for temporal queries)
```bash
cc-conversation-search list --date yesterday --json
cc-conversation-search list --days 7 --json
cc-conversation-search list --since 2025-11-10 --until today --json
```

### Context & Tree
```bash
cc-conversation-search context <UUID> --json
cc-conversation-search tree <SESSION_ID> --json
```

**Always use `--json` for structured output.**

**Resolution rule (shared with the `session-miner` skill):** Treat any
`cc-conversation-search tree <SESSION_ID> --json` response containing an
`"error"` field as unresolved, even when the command exits `0`. For Codex,
validate the candidate file's `session_meta`; a filename match alone is not
resolution.

## Examples

**Example 1: Topic query**
```
User: "Find that conversation where we fixed the authentication bug"
```

Todo workflow:
1. ✓ Tool installed/upgraded
2. ✓ Classify: TOPIC query
3. ✓ Level 1: `cc-conversation-search search "authentication bug" --days 14 --json`
4. If no results → Level 2: `cc-conversation-search search "auth bug" --json`
5. Present results with resume commands

**Example 2: Temporal query**
```
User: "What did we work on yesterday?"
```

Todo workflow:
1. ✓ Tool installed/upgraded
2. ✓ Classify: TEMPORAL query
3. ✓ Level 1: `cc-conversation-search list --date yesterday --json`
4. Parse conversations, group by project
5. Present organized summary

**Example 3: Hybrid query**
```
User: "Show me yesterday's authentication work"
```

Todo workflow:
1. ✓ Tool installed/upgraded
2. ✓ Classify: HYBRID query (topic + time)
3. ✓ Level 1: `cc-conversation-search search "authentication" --date yesterday --json`
4. Present matching sessions

**Example 4: Counting/analysis query**
```
User: "How many times did you say 'absolutely right' in the past week?"
```

Todo workflow:
1. ✓ Tool installed/upgraded
2. ✓ Classify: HYBRID query (phrase + time)
3. ✓ Level 1: `cc-conversation-search search "absolutely right" --days 7 --json`
4. Parse JSON, filter `message_type == "assistant"`, count results
5. Present count with context snippets

## Error Handling

**Tool not installed:**
- Guide user through installation (see Prerequisites section)
- Do not proceed until confirmed

**Database not found:**
- User must run: `cc-conversation-search init`
- Creates `~/.conversation-search/index.db`

**Empty results:**
- Follow Level 1 → 2 → 3 progression
- Do not give up after Level 1
- Only report "not found" after Level 3 fails
