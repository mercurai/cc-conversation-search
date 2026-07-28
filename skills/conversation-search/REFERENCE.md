# Conversation Search - Technical Reference

## Complete Command Reference

All examples use the installed `cc-conversation-search` command. Add
`--provider claude`, `--provider codex`, or (for index/search/list)
`--provider all`. Claude is the default.

### cc-conversation-search init

Initialize the database and perform initial indexing.

```bash
cc-conversation-search init [--provider claude|codex|all] [--days DAYS] [--no-extract] [--force]
```

**Options:**
- `--days DAYS`: Index last N days of conversations (default: 7)
- `--no-extract`: Skip smart extraction, store only raw content
- `--force`: Reinitialize existing database

**What it does:**
1. Creates `~/.conversation-search/index.db` SQLite database
2. Scans the selected `~/.claude/projects/` and/or `~/.codex/sessions/` roots
3. Parses JSONL conversation format
4. Extracts searchable content using smart hybrid extraction (instant, no AI)
5. Builds FTS5 search index

**Example:**
```bash
# Initialize with last 30 days
cc-conversation-search init --provider all --days 30

# Store only raw content (skip extraction)
cc-conversation-search init --no-extract
```

---

### cc-conversation-search search

Search conversations using full-text search on smart-extracted content.

```bash
cc-conversation-search search QUERY [--provider claude|codex|all] [--days DAYS] [--project PROJECT] [--limit LIMIT] [--content] [--json]
```

**Arguments:**
- `QUERY`: Search query (supports FTS5 syntax)

**Options:**
- `--days DAYS`: Limit to last N days
- `--project PROJECT`: Filter by project path
- `--limit LIMIT`: Max results (default: 20)
- `--content`: Show full message content instead of summaries
- `--json`: Output as JSON

**Search Syntax:**
- Simple: `authentication bug`
- Multiple terms: `react hooks useEffect` (implicit AND)
- Phrases: `"exact phrase"`
- Operators: `auth AND bug`, `react OR vue`

**Examples:**
```bash
# Basic search
cc-conversation-search search "authentication"

# Time-scoped search
cc-conversation-search search "database" --provider all --days 30

# Project-specific search
cc-conversation-search search "api" --project /home/user/myapp

# Get JSON output (for programmatic use)
cc-conversation-search search "hooks" --json
```

---

### cc-conversation-search context

Get conversation context around a specific message.

```bash
cc-conversation-search context MESSAGE_UUID [--provider claude|codex] [--depth DEPTH] [--content] [--json]
```

**Arguments:**
- `MESSAGE_UUID`: Message UUID from search results

**Options:**
- `--depth DEPTH`: How many parent levels to show (default: 3)
- `--content`: Show full content instead of summaries
- `--json`: Output as JSON

**What it returns:**
- Parent messages (conversation history leading to this message)
- Target message
- Child messages (responses to this message)

**Example:**
```bash
# Get context for a message
cc-conversation-search context abc-123-def --depth 5

# With full content
cc-conversation-search context abc-123-def --content --json
```

---

### cc-conversation-search list

List recent conversations.

```bash
cc-conversation-search list [--provider claude|codex|all] [--days DAYS] [--limit LIMIT] [--json]
```

**Options:**
- `--days DAYS`: Show conversations from last N days (default: 7)
- `--limit LIMIT`: Max conversations to show (default: 20)
- `--json`: Output as JSON

**Example:**
```bash
# List last week's conversations
cc-conversation-search list --provider all --days 7

# List last 50 conversations
cc-conversation-search list --limit 50 --json
```

---

### cc-conversation-search tree

Show the conversation tree structure for a session.

```bash
cc-conversation-search tree SESSION_ID [--provider claude|codex] [--json]
```

**Arguments:**
- `SESSION_ID`: Session ID from list or search results

**Options:**
- `--json`: Output as JSON

**Use case:** Visualize conversation branching and checkpoint structure.

**Example:**
```bash
cc-conversation-search tree session-abc-123
```

---

### cc-conversation-search index

JIT index conversations (instant, no AI calls). The skill runs this before every search.

```bash
cc-conversation-search index [--provider claude|codex|all] [--days DAYS] [--all] [--no-extract]
```

**Options:**
- `--days DAYS`: Index last N days (default: 1)
- `--all`: Index all conversations
- `--no-extract`: Skip smart extraction

**What it does:**
- Scans for new/modified conversations
- Extracts searchable content (instant, deterministic)
- Updates FTS5 search index
- Typically completes in <1 second for recent conversations

**Example:**
```bash
# JIT index last week (typical usage)
cc-conversation-search index --provider all --days 7

# Reindex everything
cc-conversation-search index --provider all --all
```

---

## Database Schema

**Location:** `~/.conversation-search/index.db`

**Tables:**
- `messages`: Provider-qualified messages with summaries and tree structure
- `conversations`: Provider-qualified session metadata and summaries
- `message_content_fts`: FTS5 full-text search index
- `index_queue`: Processing queue (internal use)

**Key Fields:**
- `message_uuid`: Unique message identifier
- `parent_uuid`: Parent message (tree structure)
- `session_id`: Conversation session
- `summary`: Smart-extracted searchable content
- `full_content`: Original message content
- `summary_method`: 'smart_extraction', 'too_short', or 'tool_noise'

---

## How Smart Extraction Works

1. **User Messages**: Full content indexed (avg 3.5K chars, important info upfront)
2. **Assistant Messages**: First 500 + last 200 chars + tool usage metadata
3. **Tool Noise**: Pure tool markers filtered automatically
4. **Short Messages**: Raw content used (< 50 chars)
5. **Instant**: No AI API calls, deterministic, ~1000+ messages/second

**Advantages:**
- Zero cost (no API calls)
- 100% coverage (never miss content)
- Instant indexing (no network latency)
- Deterministic (same input = same output)

---

## JSON Output Format

All commands support `--json` for structured output.

**Search results:**
```json
[
  {
    "provider": "claude",
    "message_uuid": "abc-123",
    "timestamp": "2025-01-13T10:30:00",
    "message_type": "user",
    "summary": "User asks about authentication bug",
    "project_path": "/home/user/projects/myapp",
    "conversation_summary": "Auth Bug Fix",
    "session_id": "session-xyz",
    "depth": 3,
    "is_sidechain": false
  }
]
```

**Context results:**
```json
{
  "message": { /* target message */ },
  "parents": [ /* ancestor messages */ ],
  "children": [ /* responses */ ]
}
```

---

## Performance Tips

1. **Use `--days` to scope searches** - Faster and more relevant
2. **Start with summaries** - Only use `--content` when needed
3. **JIT indexing** - Skill runs `index --days 7` before search (instant)
4. **Periodic full reindex** - `cc-conversation-search index --provider all --all` monthly
5. **Project filtering** - Use `--project` for focused searches

---

## Integration with Claude Code

This skill supports both Claude Code and Codex JSONL through provider adapters.
The Claude format is:

**Conversation File Format (JSONL):**
```jsonl
{"type": "summary", "leafUuid": "...", "conversationSummary": "..."}
{"uuid": "msg-1", "type": "user", "message": {...}, "timestamp": "..."}
{"uuid": "msg-2", "type": "assistant", "message": {...}, "parentUuid": "msg-1"}
```

**Key Features:**
- Preserves tree structure (branches, checkpoints)
- Filters tool noise automatically
- Handles multi-project setups
- Concurrent-safe with SQLite WAL mode

---

## Troubleshooting

**Import errors after installation:**
- Ensure using Python 3.9+
- Run this repository's `bash install.sh`

**Search returns no results:**
- Check if database exists: `ls ~/.conversation-search/index.db`
- Run JIT index: `cc-conversation-search index --provider all --days 30`
- Verify conversations exist under `~/.claude/projects/` or `~/.codex/sessions/`

**Database locked errors:**
- Close other instances of `cc-conversation-search`
- Database uses WAL mode for concurrent access
- Check permissions: `ls -la ~/.conversation-search/`

**Indexing seems slow:**
- Smart extraction is instant (~1000+ msgs/sec)
- If slow, check disk I/O or file system latency
- Try: `cc-conversation-search index --provider all --all` to rebuild

---

## Advanced Usage

**Custom database path:**
```python
from conversation_search.core.indexer import ConversationIndexer
indexer = ConversationIndexer(db_path="/custom/path/index.db")
```

**Programmatic search:**
```python
from conversation_search.core.search import ConversationSearch
search = ConversationSearch()
results = search.search_conversations("query", days_back=7)
for r in results:
    print(r['summary'])
```

**Batch operations:**
```bash
# Export all conversations about "database"
cc-conversation-search search "database" --provider all --json > database_convs.json

# Reindex specific time range
for days in 7 14 30; do
    cc-conversation-search index --provider all --days $days
done
```
