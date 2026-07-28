#!/usr/bin/env python3
"""Unified CLI for conversation-search"""

import argparse
import json
import os
import sys
from datetime import datetime
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from typing import Any, Dict, List, Union

from conversation_search.core.indexer import ConversationIndexer
from conversation_search.core.providers import detect_transcript_provider, get_provider
from conversation_search.core.search import ConversationSearch, format_timestamp
from conversation_search.core.session_miner import (
    add_mine_session_args,
    run_mine_session,
)

try:
    __version__ = version("cc-conversation-search")
except PackageNotFoundError:
    __version__ = "dev"

# Configurable Claude command (default: 'claude')
# Set CC_CONVERSATION_SEARCH_CMD env var to override (e.g., 'clauded' for alias)
CLAUDE_CMD = os.environ.get('CC_CONVERSATION_SEARCH_CMD', 'claude')


def _selected_providers(args) -> tuple[str, ...]:
    provider = getattr(args, "provider", "claude")
    return ("claude", "codex") if provider == "all" else (provider,)


def _provider_for_path(path: Path, providers: tuple[str, ...]) -> str:
    return providers[0] if len(providers) == 1 else detect_transcript_provider(path)


def _resume_command(provider: str, session_id: str) -> str:
    if provider == "claude":
        return f"{CLAUDE_CMD} --resume {session_id}"
    return get_provider(provider).resume_command(session_id)


def localize_timestamps(data: Any) -> Any:
    """Recursively convert UTC ISO timestamps to local timezone"""
    if isinstance(data, list):
        return [localize_timestamps(item) for item in data]
    elif isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Convert timestamp fields from UTC to local
            if key in ('timestamp', 'first_message_at', 'last_message_at', 'indexed_at'):
                if isinstance(value, str) and value.endswith('Z'):
                    dt_utc = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    dt_local = dt_utc.astimezone()
                    result[key] = dt_local.isoformat()
                else:
                    result[key] = value
            else:
                result[key] = localize_timestamps(value) if isinstance(value, (dict, list)) else value
        return result
    else:
        return data


def cmd_init(args):
    """Initialize the database and run initial indexing"""
    quiet = args.quiet

    if not quiet:
        print("Conversation Search - Initializing")
        print("=" * 50)

    db_path = Path.home() / ".conversation-search" / "index.db"

    if db_path.exists() and not args.force:
        if not quiet:
            print(f"✓ Database already exists: {db_path}")
            print("  Use --force to reinitialize")
        return

    if not quiet:
        print(f"Creating database: {db_path}")
    indexer = ConversationIndexer(db_path=str(db_path), quiet=quiet)

    days = args.days
    if not quiet:
        print(f"\nIndexing conversations from last {days} days...")
    providers = _selected_providers(args)
    files = indexer.scan_conversations(days_back=days, providers=providers)

    if not files:
        if not quiet:
            print("  No conversations found")
        indexer.close()
        return

    if not quiet:
        print(f"  Found {len(files)} conversation files")

    for i, conv_file in enumerate(files, 1):
        try:
            if not quiet:
                print(f"  [{i}/{len(files)}] {conv_file.name}", end="\r")
            indexer.index_conversation(
                conv_file,
                summarize=not args.no_extract,
                provider=_provider_for_path(conv_file, providers),
            )
        except Exception as e:
            print(f"\n  Error indexing {conv_file.name}: {e}")

    if quiet:
        print(f"✓ Indexed {len(files)} conversations")
    else:
        print(f"\n\n✓ Initialization complete!")
        print(f"  Database: {db_path}")
        print(f"\nNext steps:")
        print(f"  • Search conversations: cc-conversation-search search '<query>'")
        print(f"  • List recent: cc-conversation-search list")
        print(f"  • Re-index: cc-conversation-search index")

    indexer.close()


def cmd_index(args):
    """Index conversations (JIT - fast without AI calls)"""
    quiet = args.quiet
    indexer = ConversationIndexer(quiet=quiet)

    providers = _selected_providers(args)
    files = indexer.scan_conversations(
        days_back=args.days if not args.all else None,
        providers=providers,
    )

    if not files:
        if not quiet:
            print("No conversations to index")
        return

    if not quiet:
        print(f"Indexing {len(files)} conversations...")

    for i, conv_file in enumerate(files, 1):
        try:
            if not quiet:
                print(f"[{i}/{len(files)}] {conv_file.name}", end="\r")
            indexer.index_conversation(
                conv_file,
                summarize=not args.no_extract,
                provider=_provider_for_path(conv_file, providers),
            )
        except Exception as e:
            if not quiet:
                print(f"\nError indexing {conv_file.name}: {e}")

    if not quiet:
        print(f"✓ Indexed {len(files)} conversations")
    indexer.close()


def cmd_search(args):
    """Search conversations"""
    # Auto-index before searching to ensure fresh data
    if not getattr(args, 'no_index', False):
        indexer = ConversationIndexer(quiet=True)
        providers = _selected_providers(args)
        # Index at least as far back as search range, minimum 30 days
        days_to_index = max(args.days if args.days else 30, 30)
        files = indexer.scan_conversations(
            days_back=days_to_index,
            providers=providers,
        )
        if files:
            for conv_file in files:
                try:
                    indexer.index_conversation(
                        conv_file,
                        summarize=True,
                        provider=_provider_for_path(conv_file, providers),
                    )
                except Exception:
                    pass  # Silent failures for auto-indexing
        indexer.close()

    search = ConversationSearch()

    try:
        results = search.search_conversations(
            query=args.query,
            days_back=args.days,
            since=getattr(args, 'since', None),
            until=getattr(args, 'until', None),
            date=getattr(args, 'date', None),
            limit=args.limit,
            project_path=args.project,
            provider=None if args.provider == "all" else args.provider,
        )
    except Exception as e:
        print(f"Error: {e}")
        raise

    if args.json:
        print(json.dumps(localize_timestamps([dict(r) for r in results]), indent=2))
        return

    if not results:
        print(f"No results found for: {args.query}")
        return

    print(f"🔍 Found {len(results)} matches for '{args.query}':\n")

    for result in results:
        icon = "👤" if result['message_type'] == 'user' else "🤖"
        timestamp = format_timestamp(result['timestamp'])

        project_dir = result['project_path']

        print(f"{icon}  {result['conversation_summary']}")
        print(f"   Session: {result['session_id']}")
        print(f"   Project: {project_dir}")
        print(f"   Time: {timestamp}")
        print(f"   Message: {result['message_uuid']}")

        if args.content:
            content = search.get_full_message_content(
                result['message_uuid'],
                provider=result["provider"],
            )
            if content:
                print(f"\n   {content[:300]}...")
        else:
            print(f"\n   {result['context_snippet']}")

        print(f"\n   Resume:")
        print(f"     cd {project_dir}")
        print(f"     {_resume_command(result['provider'], result['session_id'])}")
        print()


def cmd_context(args):
    """Get context around a message"""
    # Auto-index recent conversations to ensure fresh data
    if not getattr(args, 'no_index', False):
        indexer = ConversationIndexer(quiet=True)
        providers = _selected_providers(args)
        files = indexer.scan_conversations(days_back=30, providers=providers)
        if files:
            for conv_file in files:
                try:
                    indexer.index_conversation(
                        conv_file,
                        summarize=True,
                        provider=_provider_for_path(conv_file, providers),
                    )
                except Exception:
                    pass  # Silent failures for auto-indexing
        indexer.close()

    search = ConversationSearch()

    result = search.get_conversation_context(
        message_uuid=args.uuid,
        depth=args.depth,
        provider=args.provider,
    )

    if args.json:
        print(json.dumps(localize_timestamps(result), indent=2))
        return

    print(f"Context for message: {args.uuid}\n")

    if 'error' in result:
        print(f"Error: {result['error']}")
        return

    # Show parents
    if result.get('ancestors'):
        print("📜 Parent messages:")
        for msg in result['ancestors']:
            icon = "👤" if msg.get('message_type') == 'user' else "🤖"
            print(f"  {icon} {msg.get('summary', 'No summary')}")
        print()

    # Show target message
    if result.get('message'):
        print("🎯 Target message:")
        msg = result['message']
        icon = "👤" if msg.get('message_type') == 'user' else "🤖"
        if args.content and msg.get('full_content'):
            print(f"  {icon} {msg['full_content']}")
        else:
            print(f"  {icon} {msg.get('summary', 'No summary')}")
        print()

    # Show children
    if result.get('children'):
        print("💬 Responses:")
        for msg in result['children']:
            icon = "👤" if msg.get('message_type') == 'user' else "🤖"
            print(f"  {icon} {msg.get('summary', 'No summary')}")


def cmd_list(args):
    """List recent conversations"""
    # Auto-index before listing to ensure fresh data
    if not getattr(args, 'no_index', False):
        indexer = ConversationIndexer(quiet=True)
        providers = _selected_providers(args)
        days_to_index = max(args.days if args.days else 30, 30)
        files = indexer.scan_conversations(
            days_back=days_to_index,
            providers=providers,
        )
        if files:
            for conv_file in files:
                try:
                    indexer.index_conversation(
                        conv_file,
                        summarize=True,
                        provider=_provider_for_path(conv_file, providers),
                    )
                except Exception:
                    pass  # Silent failures for auto-indexing
        indexer.close()

    search = ConversationSearch()

    convs = search.list_recent_conversations(
        days_back=args.days,
        since=getattr(args, 'since', None),
        until=getattr(args, 'until', None),
        date=getattr(args, 'date', None),
        limit=args.limit,
        provider=None if args.provider == "all" else args.provider,
    )

    if args.json:
        print(json.dumps(localize_timestamps([dict(c) for c in convs]), indent=2))
        return

    if not convs:
        print("No conversations found")
        return

    print(f"Recent conversations (last {args.days} days):\n")

    for conv in convs:
        timestamp = format_timestamp(conv['last_message_at'])
        print(f"[{timestamp}] {conv['conversation_summary']}")
        print(f"  {conv['message_count']} messages")
        print(f"  {conv['project_path']}")
        print(f"  Session: {conv['session_id']}")
        print()


def cmd_tree(args):
    """Show conversation tree"""
    search = ConversationSearch()

    tree = search.get_conversation_tree(
        args.session_id,
        provider=args.provider,
    )

    if args.json:
        print(json.dumps(localize_timestamps(tree), indent=2))
        return

    print(f"Conversation tree: {args.session_id}\n")

    if 'error' in tree:
        print(f"Error: {tree['error']}")
        return

    # Simple tree visualization
    def print_tree(nodes, indent=0):
        for node in nodes:
            icon = "👤" if node['message_type'] == 'user' else "🤖"
            prefix = "  " * indent
            summary = node['summary'][:80]
            print(f"{prefix}{icon} {summary}")
            if node.get('children'):
                print_tree(node['children'], indent + 1)

    print_tree(tree['tree'])


def cmd_resume(args):
    """Get session resumption commands for a message UUID"""
    search = ConversationSearch()

    # Get message info
    cursor = search.conn.cursor()
    result = None
    if search._has_provider:
        cursor.execute("""
            SELECT m.provider, m.session_id, m.project_path, m.timestamp, m.summary
            FROM messages m
            WHERE m.provider = ? AND m.message_uuid = ?
        """, (args.provider, args.uuid))
        result = cursor.fetchone()
    elif args.provider == "claude":
        cursor.execute("""
            SELECT 'claude' AS provider, m.session_id, m.project_path,
                   m.timestamp, m.summary
            FROM messages m
            WHERE m.message_uuid = ?
        """, (args.uuid,))
        result = cursor.fetchone()

    if not result:
        print(f"Message not found: {args.uuid}")
        sys.exit(1)

    session_id = result['session_id']
    project_path = result['project_path']

    print(f"cd {project_path}")
    print(_resume_command(result["provider"], session_id))


def cmd_mine_session(args):
    """Resolve and mine a Claude Code or Codex session transcript.

    Delegates to `run_mine_session` so the package CLI and the Codex
    wrapper (`codex-skills/claude-session-miner/scripts/mine_claude_session.py`,
    which goes through `session_miner.main()`) share the exact same
    execution path. See issue #3.
    """
    run_mine_session(
        args.session_id,
        args.transcript,
        json_output=args.json_output,
        provider=args.provider,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='cc-conversation-search',
        description='Find, resume, and mine Claude Code and Codex conversations'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # init command
    init_parser = subparsers.add_parser('init', help='Initialize database and index')
    init_parser.add_argument('--days', type=int, default=7, help='Days of history to index (default: 7)')
    init_parser.add_argument('--no-extract', action='store_true', help='Skip smart extraction (store only raw content)')
    init_parser.add_argument('--force', action='store_true', help='Reinitialize existing database')
    init_parser.add_argument('--quiet', action='store_true', help='Minimal output')
    init_parser.add_argument(
        '--provider',
        choices=('claude', 'codex', 'all'),
        default='claude',
        help='Transcript provider to index (default: claude)',
    )
    init_parser.set_defaults(func=cmd_init)

    # index command
    index_parser = subparsers.add_parser('index', help='Index conversations (JIT - runs before search)')
    index_parser.add_argument('--days', type=int, default=1, help='Days back to index (default: 1)')
    index_parser.add_argument('--all', action='store_true', help='Index all conversations')
    index_parser.add_argument('--no-extract', action='store_true', help='Skip smart extraction')
    index_parser.add_argument('--quiet', action='store_true', help='Minimal output')
    index_parser.add_argument(
        '--provider',
        choices=('claude', 'codex', 'all'),
        default='claude',
        help='Transcript provider to index (default: claude)',
    )
    index_parser.set_defaults(func=cmd_index)

    # search command
    search_parser = subparsers.add_parser('search', help='Search conversations')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('--days', type=int, help='Limit to last N days')
    search_parser.add_argument('--since', help='Start date (YYYY-MM-DD, yesterday, today)')
    search_parser.add_argument('--until', help='End date (YYYY-MM-DD, yesterday, today)')
    search_parser.add_argument('--date', help='Specific date (YYYY-MM-DD, yesterday, today)')
    search_parser.add_argument('--project', help='Filter by project path')
    search_parser.add_argument('--limit', type=int, default=20, help='Max results (default: 20)')
    search_parser.add_argument('--content', action='store_true', help='Show full content')
    search_parser.add_argument('--json', action='store_true', help='Output as JSON')
    search_parser.add_argument('--no-index', action='store_true', help='Skip auto-indexing (faster but may be stale)')
    search_parser.add_argument(
        '--provider',
        choices=('claude', 'codex', 'all'),
        default='claude',
        help='Transcript provider to search (default: claude)',
    )
    search_parser.set_defaults(func=cmd_search)

    # context command
    context_parser = subparsers.add_parser('context', help='Get context around a message')
    context_parser.add_argument('uuid', help='Message UUID')
    context_parser.add_argument('--depth', type=int, default=3, help='Parent depth (default: 3)')
    context_parser.add_argument('--content', action='store_true', help='Show full content')
    context_parser.add_argument('--json', action='store_true', help='Output as JSON')
    context_parser.add_argument('--no-index', action='store_true', help='Skip auto-indexing (faster but may be stale)')
    context_parser.add_argument(
        '--provider',
        choices=('claude', 'codex'),
        default='claude',
        help='Transcript provider (default: claude)',
    )
    context_parser.set_defaults(func=cmd_context)

    # list command
    list_parser = subparsers.add_parser('list', help='List recent conversations')
    list_parser.add_argument('--days', type=int, help='Days back (default: 7)')
    list_parser.add_argument('--since', help='Start date (YYYY-MM-DD, yesterday, today)')
    list_parser.add_argument('--until', help='End date (YYYY-MM-DD, yesterday, today)')
    list_parser.add_argument('--date', help='Specific date (YYYY-MM-DD, yesterday, today)')
    list_parser.add_argument('--limit', type=int, default=20, help='Max results (default: 20)')
    list_parser.add_argument('--json', action='store_true', help='Output as JSON')
    list_parser.add_argument('--no-index', action='store_true', help='Skip auto-indexing (faster but may be stale)')
    list_parser.add_argument(
        '--provider',
        choices=('claude', 'codex', 'all'),
        default='claude',
        help='Transcript provider to list (default: claude)',
    )
    list_parser.set_defaults(func=cmd_list)

    # tree command
    tree_parser = subparsers.add_parser('tree', help='Show conversation tree')
    tree_parser.add_argument('session_id', help='Session ID')
    tree_parser.add_argument('--json', action='store_true', help='Output as JSON')
    tree_parser.add_argument(
        '--provider',
        choices=('claude', 'codex'),
        default='claude',
        help='Transcript provider (default: claude)',
    )
    tree_parser.set_defaults(func=cmd_tree)

    # resume command
    resume_parser = subparsers.add_parser('resume', help='Get session resumption commands')
    resume_parser.add_argument('uuid', help='Message UUID')
    resume_parser.add_argument(
        '--provider',
        choices=('claude', 'codex'),
        default='claude',
        help='Transcript provider (default: claude)',
    )
    resume_parser.set_defaults(func=cmd_resume)

    # mine-session command — flag surface lives in session_miner.add_mine_session_args
    # so this subparser and the Codex wrapper (which goes through
    # session_miner.main() / parse_args()) cannot drift.
    mine_parser = subparsers.add_parser(
        'mine-session',
        help='Resolve and mine a Claude Code or Codex session transcript',
    )
    add_mine_session_args(mine_parser)
    mine_parser.set_defaults(func=cmd_mine_session)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nThe cc-conversation-search tool requires initialization.")
        print("Install: uv tool install cc-conversation-search")
        print("Initialize: cc-conversation-search init")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
