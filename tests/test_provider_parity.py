import argparse
import io
import json
import sqlite3
from pathlib import Path

import pytest

from conversation_search.core.providers import (
    detect_transcript_provider,
    get_provider,
)
from conversation_search.core.indexer import ConversationIndexer
from conversation_search.core.search import ConversationSearch
from conversation_search.core.session_miner import (
    add_mine_session_args,
    mine_session,
    parse_transcript,
    resolve_session,
    run_mine_session,
)
from conversation_search import cli


FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parents[1]
CODEX_FIXTURE = FIXTURES / "codex_session.jsonl"
CLAUDE_FIXTURE = FIXTURES / "rich_session.jsonl"


def test_detects_both_transcript_providers_from_structure():
    assert detect_transcript_provider(CLAUDE_FIXTURE) == "claude"
    assert detect_transcript_provider(CODEX_FIXTURE) == "codex"


def test_codex_provider_parses_normalized_summary_without_reasoning_leakage():
    summary = parse_transcript(CODEX_FIXTURE, provider="codex")

    assert summary["provider"] == "codex"
    assert summary["session_id"] == "codex-session-001"
    assert summary["project_path"] == r"D:\projects\provider-parity-fixture"
    assert summary["records"] == 8
    assert summary["user_turns"] == 1
    assert summary["assistant_turns"] == 1
    assert summary["shell_commands"] == ["git status"]
    assert summary["tool_counts"]["exec_command"] == 1
    assert summary["errors"][0]["type"] == "function_call_output_error"
    # parse_transcript intentionally preserves Path/Counter objects for
    # backward compatibility; mine_session is the JSON-clean public surface.
    serialized = repr(summary)
    assert "fixture-encrypted-content" not in serialized


def test_codex_resolution_requires_matching_session_metadata(tmp_path):
    codex_root = tmp_path / ".codex" / "sessions"
    codex_root.mkdir(parents=True)
    matching = codex_root / "rollout-codex-session-001.jsonl"
    matching.write_text(CODEX_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    incidental = codex_root / "rollout-wrong-session.jsonl"
    incidental.write_text(
        '{"type":"session_meta","payload":{"id":"different-session"}}\n',
        encoding="utf-8",
    )

    resolved = resolve_session(
        "codex-session-001",
        provider="codex",
        codex_roots=[codex_root],
    )
    unresolved = resolve_session(
        "wrong-session",
        provider="codex",
        codex_roots=[codex_root],
    )

    assert resolved["resolved"] is True
    assert resolved["resolved_provider"] == "codex"
    assert resolved["resolution_stage"] == "codex_root"
    assert Path(resolved["resolved_path"]) == matching.resolve()
    assert unresolved["resolved"] is False
    assert unresolved["stages"]["codex_filename_matches"]


def test_auto_provider_detects_explicit_transcript():
    resolved = resolve_session(
        "codex-session-001",
        explicit_path=str(CODEX_FIXTURE),
        provider="auto",
        claude_root=Path("does-not-exist"),
        codex_roots=[],
    )

    assert resolved["resolved"] is True
    assert resolved["requested_provider"] == "auto"
    assert resolved["resolved_provider"] == "codex"
    assert resolved["transcript_format"] == "codex-jsonl"


def test_mine_session_defaults_to_claude_for_backward_compatibility(monkeypatch):
    monkeypatch.setattr(
        "conversation_search.core.session_miner.run_cc_tree",
        lambda _session_id: {"status": "unresolved", "checked": [], "error": "fixture"},
    )
    monkeypatch.setattr(
        "conversation_search.core.session_miner.lookup_db",
        lambda _session_id, path: {"path": path, "status": "missing", "matches": []},
    )

    implicit = mine_session("claude-session", transcript=str(CLAUDE_FIXTURE))
    explicit = mine_session(
        "claude-session",
        transcript=str(CLAUDE_FIXTURE),
        provider="claude",
    )

    assert implicit == explicit
    assert implicit["requested_provider"] == "claude"
    assert implicit["resolved_provider"] == "claude"


def test_mine_session_argument_parser_accepts_provider_and_defaults_to_claude():
    parser = argparse.ArgumentParser()
    add_mine_session_args(parser)

    defaulted = parser.parse_args(["session-id"])
    selected = parser.parse_args(["session-id", "--provider", "codex"])

    assert defaulted.provider == "claude"
    assert selected.provider == "codex"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unsupported transcript provider"):
        get_provider("other")


def test_codex_mining_redacts_common_secrets_and_omits_reasoning(tmp_path):
    transcript = tmp_path / "redacted.jsonl"
    rows = [
        {
            "type": "session_meta",
            "payload": {"id": "redacted-session", "cwd": "/fixture"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "password=fixture-password Bearer fixture-token",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": "tool --api-key=fixture-api-key"}
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "encrypted_content": "fixture-encrypted-reasoning",
            },
        },
    ]
    transcript.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary = parse_transcript(transcript, provider="codex")
    serialized = repr(summary)

    assert serialized.count("<redacted>") >= 3
    assert "fixture-password" not in serialized
    assert "fixture-token" not in serialized
    assert "fixture-api-key" not in serialized
    assert "fixture-encrypted-reasoning" not in serialized


def test_provider_detection_is_bounded_to_initial_records(tmp_path):
    transcript = tmp_path / "late-provider-marker.jsonl"
    rows = [{"type": "unrelated", "payload": {}} for _ in range(40)]
    rows.append(
        {
            "type": "session_meta",
            "payload": {"id": "too-late"},
        }
    )
    transcript.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unable to detect"):
        detect_transcript_provider(transcript)


def test_public_mining_payload_bounds_repeated_evidence(monkeypatch, tmp_path):
    transcript = tmp_path / "bounded.jsonl"
    rows = [
        {
            "type": "session_meta",
            "payload": {"id": "bounded-session", "cwd": "/fixture"},
        }
    ]
    rows.extend(
        {
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": f"sanitized prompt {index}",
            },
        }
        for index in range(205)
    )
    transcript.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "conversation_search.core.session_miner.lookup_db",
        lambda _session_id, path: {"path": path, "status": "missing", "matches": []},
    )

    payload = mine_session(
        "bounded-session",
        transcript=str(transcript),
        provider="codex",
    )

    assert len(payload["summary"]["user_prompts"]) == 200


def test_indexer_parses_codex_messages_into_common_shape():
    indexer = ConversationIndexer(db_path=":memory:", quiet=True)
    try:
        metadata, messages = indexer.parse_conversation_file(
            CODEX_FIXTURE,
            provider="codex",
        )
    finally:
        indexer.close()

    assert metadata["provider"] == "codex"
    assert metadata["session_id"] == "codex-session-001"
    assert metadata["project_path"] == r"D:\projects\provider-parity-fixture"
    assert [message["message_type"] for message in messages] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert messages[-1]["content"].startswith("[Tool: exec_command]")


def test_database_can_store_same_ids_for_both_providers():
    indexer = ConversationIndexer(db_path=":memory:", quiet=True)
    try:
        cursor = indexer.conn.cursor()
        for provider in ("claude", "codex"):
            cursor.execute(
                """
                INSERT INTO conversations (
                    provider, session_id, project_path, conversation_file
                ) VALUES (?, ?, ?, ?)
                """,
                (provider, "shared-session", "/fixture", f"{provider}.jsonl"),
            )
            cursor.execute(
                """
                INSERT INTO messages (
                    provider, message_uuid, session_id, timestamp,
                    message_type, full_content
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    "shared-message",
                    "shared-session",
                    "2026-01-02T03:04:05Z",
                    "user",
                    provider,
                ),
            )
        indexer.conn.commit()

        rows = cursor.execute(
            "SELECT provider FROM conversations WHERE session_id = ? ORDER BY provider",
            ("shared-session",),
        ).fetchall()
    finally:
        indexer.close()

    assert [row["provider"] for row in rows] == ["claude", "codex"]


def test_search_filters_results_by_provider(tmp_path):
    db_path = tmp_path / "provider.db"
    indexer = ConversationIndexer(db_path=str(db_path), quiet=True)
    cursor = indexer.conn.cursor()
    for provider in ("claude", "codex"):
        cursor.execute(
            """
            INSERT INTO conversations (
                provider, session_id, project_path, conversation_file,
                first_message_at, last_message_at, message_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                "shared-session",
                "/fixture",
                f"{provider}.jsonl",
                "2026-01-02T03:04:05Z",
                "2026-01-02T03:04:05Z",
                1,
            ),
        )
        cursor.execute(
            """
            INSERT INTO messages (
                provider, message_uuid, session_id, timestamp,
                message_type, full_content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                "shared-message",
                "shared-session",
                "2026-01-02T03:04:05Z",
                "user",
                f"{provider} provider fixture",
            ),
        )
    indexer.conn.commit()
    indexer.close()

    search = ConversationSearch(db_path=str(db_path))
    try:
        rows = search.search_conversations(
            "provider fixture",
            provider="codex",
        )
    finally:
        search.close()

    assert len(rows) == 1
    assert rows[0]["provider"] == "codex"


def test_provider_schema_migrates_existing_claude_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            session_id TEXT PRIMARY KEY,
            project_path TEXT,
            conversation_file TEXT,
            root_message_uuid TEXT,
            leaf_message_uuid TEXT,
            conversation_summary TEXT,
            first_message_at TEXT,
            last_message_at TEXT,
            message_count INTEGER DEFAULT 0,
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE messages (
            message_uuid TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_uuid TEXT,
            is_sidechain BOOLEAN DEFAULT FALSE,
            depth INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL,
            message_type TEXT NOT NULL,
            project_path TEXT,
            conversation_file TEXT,
            summary TEXT,
            full_content TEXT NOT NULL,
            is_summarized BOOLEAN DEFAULT FALSE,
            is_tool_noise BOOLEAN DEFAULT FALSE,
            is_meta_conversation BOOLEAN DEFAULT FALSE,
            summary_method TEXT,
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO conversations (
            session_id, project_path, conversation_file, root_message_uuid,
            first_message_at, last_message_at, message_count
        ) VALUES (
            'legacy-session', '/fixture', 'legacy.jsonl', 'legacy-message',
            '2026-01-02T03:04:05Z', '2026-01-02T03:04:05Z', 1
        );
        INSERT INTO messages (
            message_uuid, session_id, timestamp, message_type, full_content
        ) VALUES (
            'legacy-message', 'legacy-session', '2026-01-02T03:04:05Z',
            'user', 'legacy fixture'
        );
        """
    )
    conn.close()

    indexer = ConversationIndexer(db_path=str(db_path), quiet=True)
    try:
        conversation = indexer.conn.execute(
            "SELECT provider, session_id FROM conversations"
        ).fetchone()
        message = indexer.conn.execute(
            "SELECT provider, message_uuid FROM messages"
        ).fetchone()
    finally:
        indexer.close()

    assert dict(conversation) == {
        "provider": "claude",
        "session_id": "legacy-session",
    }
    assert dict(message) == {
        "provider": "claude",
        "message_uuid": "legacy-message",
    }


def test_codex_fixture_indexes_and_searches_end_to_end(tmp_path):
    db_path = tmp_path / "codex.db"
    indexer = ConversationIndexer(db_path=str(db_path), quiet=True)
    try:
        indexer.index_conversation(CODEX_FIXTURE, provider="codex")
    finally:
        indexer.close()

    search = ConversationSearch(db_path=str(db_path))
    try:
        rows = search.search_conversations(
            "sanitized provider",
            provider="codex",
        )
        tree = search.get_conversation_tree(
            "codex-session-001",
            provider="codex",
        )
    finally:
        search.close()

    assert rows
    assert rows[0]["provider"] == "codex"
    assert tree["conversation"]["project_path"] == r"D:\projects\provider-parity-fixture"
    assert tree["total_messages"] == 3


def test_provider_schema_indexes_with_foreign_keys_enabled():
    indexer = ConversationIndexer(db_path=":memory:", quiet=True)
    try:
        indexer.conn.execute("PRAGMA foreign_keys=ON")
        indexer.index_conversation(CODEX_FIXTURE, provider="codex")
        count = indexer.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE provider = 'codex'"
        ).fetchone()[0]
    finally:
        indexer.close()

    assert count == 3


@pytest.mark.parametrize(
    ("provider", "fixture", "session_id"),
    [
        ("claude", CLAUDE_FIXTURE, "claude-session"),
        ("codex", CODEX_FIXTURE, "codex-session-001"),
    ],
)
def test_mine_session_json_surface_supports_both_providers(
    monkeypatch,
    provider,
    fixture,
    session_id,
):
    monkeypatch.setattr(
        "conversation_search.core.session_miner.run_cc_tree",
        lambda _session_id: {"status": "unresolved", "checked": [], "error": "fixture"},
    )
    monkeypatch.setattr(
        "conversation_search.core.session_miner.lookup_db",
        lambda _session_id, path: {"path": path, "status": "missing", "matches": []},
    )
    output = io.StringIO()

    exit_code = run_mine_session(
        session_id,
        str(fixture),
        json_output=True,
        stream=output,
        provider=provider,
    )
    payload = json.loads(output.getvalue())

    assert exit_code == 0
    assert payload["requested_provider"] == provider
    assert payload["resolved_provider"] == provider
    assert payload["summary"]["provider"] == provider


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("claude", "claude --resume session-id"),
        ("codex", "codex resume session-id"),
    ],
)
def test_provider_resume_commands(provider, expected):
    assert get_provider(provider).resume_command("session-id") == expected


@pytest.mark.parametrize(
    ("argv", "expected_provider"),
    [
        (["search", "fixture", "--provider", "codex", "--no-index"], "codex"),
        (["list", "--provider", "all", "--no-index"], "all"),
        (["tree", "session-id", "--provider", "codex"], "codex"),
        (["resume", "message-id", "--provider", "codex"], "codex"),
    ],
)
def test_cli_provider_matrix_is_explicit_and_parseable(monkeypatch, argv, expected_provider):
    captured = {}

    def capture(args):
        captured["provider"] = args.provider

    monkeypatch.setattr(cli, f"cmd_{argv[0]}", capture)
    cli.main(argv)

    assert captured["provider"] == expected_provider


def test_both_plugin_surfaces_package_explicit_provider_skills():
    claude_manifest = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude_skills = set(claude_manifest["skills"])

    assert "./skills/claude-session-miner" in claude_skills
    assert "./skills/codex-session-miner" in claude_skills
    assert (REPO_ROOT / "codex-skills" / "claude-session-miner" / "SKILL.md").is_file()
    assert (REPO_ROOT / "codex-skills" / "codex-session-miner" / "SKILL.md").is_file()


def test_installer_activates_codex_plugin_through_supported_cli():
    installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")

    assert (
        'codex plugin add "${PLUGIN_SPEC}" --json'
        in installer
    )
    assert 'MARKETPLACE_NAME="mercurai-managed-plugins"' in installer
    assert 'expected_plugin_source="${ROOT_CANONICAL}"' in installer
    assert "codex_plugin_ready" in installer
    assert 'get("enabled") is not True' in installer
