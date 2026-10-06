"""Tests for skip-unchanged indexing and UTF-8 console output."""

import json
import os
import subprocess
import sys
from pathlib import Path

from conversation_search.core.indexer import ConversationIndexer

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def _record(uuid, parent, role, text, stamp):
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": SESSION_ID,
        "cwd": r"D:\projects\demo",
        "type": role,
        "timestamp": f"2026-05-01T10:00:{stamp:02d}Z",
        "message": {"role": role, "content": text},
    }


def _write_transcript(path: Path, turns: int) -> None:
    rows = [
        {"type": "summary", "summary": "Incremental fixture", "leafUuid": f"m{turns}"}
    ]
    parent = None
    for i in range(1, turns + 1):
        role = "user" if i % 2 else "assistant"
        rows.append(_record(f"m{i}", parent, role, f"turn {i} about the widget", i))
        parent = f"m{i}"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


def _parse_counter(indexer, monkeypatch):
    calls = []
    original = indexer.parse_conversation_file

    def counting(file_path, **kwargs):
        calls.append(file_path)
        return original(file_path, **kwargs)

    monkeypatch.setattr(indexer, "parse_conversation_file", counting)
    return calls


def _signature_row(indexer, transcript):
    return indexer.conn.execute(
        "SELECT message_count, file_mtime_ns, file_size FROM conversations WHERE conversation_file = ?",
        (str(transcript),),
    ).fetchone()


def test_unchanged_file_is_skipped_without_parsing(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, turns=4)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript]

        row = _signature_row(indexer, transcript)
        assert row["message_count"] == 4
        assert (row["file_mtime_ns"], row["file_size"]) == (
            transcript.stat().st_mtime_ns,
            transcript.stat().st_size,
        )
    finally:
        indexer.close()


def test_modified_file_is_reparsed_and_restamped(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, turns=4)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)

        _write_transcript(transcript, turns=6)  # two new turns appended
        _bump_mtime(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript, transcript]

        row = _signature_row(indexer, transcript)
        assert row["message_count"] == 6
        assert (row["file_mtime_ns"], row["file_size"]) == (
            transcript.stat().st_mtime_ns,
            transcript.stat().st_size,
        )

        indexer.index_conversation(transcript)  # unchanged again
        assert len(calls) == 2
    finally:
        indexer.close()


def test_touched_file_with_no_new_messages_is_restamped(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, turns=4)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        _bump_mtime(transcript)

        indexer.index_conversation(transcript)  # parses, finds nothing new, restamps
        indexer.index_conversation(transcript)  # skipped on the new signature
        assert len(calls) == 2
        assert (
            _signature_row(indexer, transcript)["file_mtime_ns"]
            == transcript.stat().st_mtime_ns
        )
    finally:
        indexer.close()


def test_legacy_database_gains_signature_columns(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, turns=2)
    db_path = tmp_path / "legacy.db"
    indexer = ConversationIndexer(db_path=str(db_path), quiet=True)
    indexer.conn.execute("ALTER TABLE conversations DROP COLUMN file_mtime_ns")
    indexer.conn.execute("ALTER TABLE conversations DROP COLUMN file_size")
    indexer.conn.commit()
    indexer.close()

    reopened = ConversationIndexer(db_path=str(db_path), quiet=True)
    try:
        columns = {
            row["name"]
            for row in reopened.conn.execute("PRAGMA table_info(conversations)")
        }
        assert {"file_mtime_ns", "file_size"} <= columns
        reopened.index_conversation(transcript)
        assert (
            _signature_row(reopened, transcript)["file_size"]
            == transcript.stat().st_size
        )
    finally:
        reopened.close()


def test_search_output_survives_cp1252_stdout(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, turns=4)
    home = tmp_path / "home"
    indexer = ConversationIndexer(
        db_path=str(home / ".conversation-search" / "index.db"), quiet=True
    )
    try:
        indexer.index_conversation(transcript)
    finally:
        indexer.close()

    env = os.environ.copy()
    env["HOME"] = home.as_posix()
    env["USERPROFILE"] = str(home)
    env["PYTHONIOENCODING"] = "cp1252"  # what a piped stdout gets on Windows
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "conversation_search.cli",
            "search",
            "widget",
            "--no-index",
        ],
        env=env,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "\U0001f50d Found" in result.stdout.decode("utf-8")
