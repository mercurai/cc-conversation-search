"""conversation-search - Semantic search across Claude Code conversation history.

Public API for downstream consumers (Codex skill, autoresearch ingest, etc.):

    from conversation_search import (
        RESOLUTION_STAGES,
        resolve_session,
        mine_session,
        run_mine_session,
        parse_transcript,
        build_report,
        add_mine_session_args,
    )
"""

from conversation_search.core.session_miner import (
    RESOLUTION_STAGES,
    SCHEMA_VERSION,
    add_mine_session_args,
    build_report,
    mine_session,
    parse_transcript,
    resolve_session,
    run_mine_session,
)

__all__ = [
    "RESOLUTION_STAGES",
    "SCHEMA_VERSION",
    "add_mine_session_args",
    "build_report",
    "mine_session",
    "parse_transcript",
    "resolve_session",
    "run_mine_session",
]
