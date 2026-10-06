"""Tests for skip-unchanged indexing and UTF-8 console output."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from conversation_search.core import indexer as indexer_module
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


def _rows(turns: int):
    rows = [
        {"type": "summary", "summary": "Incremental fixture", "leafUuid": f"m{turns}"}
    ]
    parent = None
    for i in range(1, turns + 1):
        role = "user" if i % 2 else "assistant"
        rows.append(_record(f"m{i}", parent, role, f"turn {i} about the widget", i))
        parent = f"m{i}"
    return rows


def _write_rows(path: Path, rows) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _write_transcript(path: Path, turns: int) -> None:
    _write_rows(path, _rows(turns))


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


def _stamp(indexer, path):
    return indexer.conn.execute(
        "SELECT file_mtime_ns, file_size, index_version, outcome FROM transcript_files WHERE path = ?",
        (str(path),),
    ).fetchone()


def _message_count(indexer):
    return indexer.conn.execute(
        "SELECT message_count FROM conversations WHERE session_id = ?", (SESSION_ID,)
    ).fetchone()["message_count"]


def _fresh(tmp_path, monkeypatch, turns=4):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, turns)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    return transcript, indexer, _parse_counter(indexer, monkeypatch)


def test_unchanged_file_is_skipped_without_parsing(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript]

        row = _stamp(indexer, transcript)
        assert tuple(row) == (
            transcript.stat().st_mtime_ns,
            transcript.stat().st_size,
            indexer_module.INDEX_VERSION,
            "indexed",
        )
        assert _message_count(indexer) == 4
    finally:
        indexer.close()


def test_modified_file_is_reparsed_and_restamped(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    try:
        indexer.index_conversation(transcript)

        _write_transcript(transcript, turns=6)  # two new turns appended
        _bump_mtime(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript, transcript]
        assert _message_count(indexer) == 6
        assert (
            _stamp(indexer, transcript)["file_mtime_ns"]
            == transcript.stat().st_mtime_ns
        )

        indexer.index_conversation(transcript)  # unchanged again
        assert len(calls) == 2
    finally:
        indexer.close()


def test_size_change_with_same_mtime_is_detected(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        stat = transcript.stat()

        _write_transcript(transcript, turns=6)
        os.utime(
            transcript, ns=(stat.st_atime_ns, stat.st_mtime_ns)
        )  # mtime restored, size differs
        indexer.index_conversation(transcript)
        assert len(calls) == 2
        assert _message_count(indexer) == 6
    finally:
        indexer.close()


def test_touched_file_with_no_new_messages_is_restamped(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        _bump_mtime(transcript)

        indexer.index_conversation(transcript)  # parses, finds nothing new, restamps
        assert _stamp(indexer, transcript)["outcome"] == "unchanged"
        indexer.index_conversation(transcript)  # skipped on the new signature
        assert len(calls) == 2
    finally:
        indexer.close()


def test_file_without_messages_is_stamped_and_skipped(tmp_path, monkeypatch):
    transcript = tmp_path / "empty.jsonl"
    _write_rows(transcript, _rows(0))  # summary line only
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript]
        assert _stamp(indexer, transcript)["outcome"] == "no-messages"
    finally:
        indexer.close()


def test_two_files_for_one_session_each_skip(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    copy = tmp_path / "copy.jsonl"
    shutil.copy(transcript, copy)
    try:
        for path in (transcript, copy, transcript, copy):
            indexer.index_conversation(path)
        assert calls == [transcript, copy]
        assert _stamp(indexer, transcript)["outcome"] == "indexed"
        assert _stamp(indexer, copy)["outcome"] == "unchanged"
    finally:
        indexer.close()


def test_duplicate_message_uuid_indexes_once_and_skips(tmp_path, monkeypatch):
    transcript = tmp_path / "dup.jsonl"
    rows = _rows(4)
    rows.append(dict(rows[2]))  # the same uuid twice, as forked rollouts produce
    _write_rows(transcript, rows)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        stored = indexer.conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = ?", (SESSION_ID,)
        ).fetchone()[0]
        assert stored == 4
        assert _message_count(indexer) == 4
        assert _stamp(indexer, transcript)["outcome"] == "indexed"

        indexer.index_conversation(transcript)
        assert len(calls) == 1
    finally:
        indexer.close()


def test_index_version_bump_forces_reparse(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        monkeypatch.setattr(
            indexer_module, "INDEX_VERSION", indexer_module.INDEX_VERSION + 1
        )

        indexer.index_conversation(transcript)
        assert len(calls) == 2
        assert (
            _stamp(indexer, transcript)["index_version"] == indexer_module.INDEX_VERSION
        )
        indexer.index_conversation(transcript)
        assert len(calls) == 2
    finally:
        indexer.close()


def test_database_indexed_before_stamps_reparses_once(tmp_path, monkeypatch):
    transcript, indexer, calls = _fresh(tmp_path, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        indexer.conn.execute(
            "DELETE FROM transcript_files"
        )  # rows from before the table existed
        indexer.conn.commit()

        indexer.index_conversation(transcript)  # parses once, nothing new, stamps
        assert len(calls) == 2
        assert _stamp(indexer, transcript)["outcome"] == "unchanged"
        indexer.index_conversation(transcript)
        assert len(calls) == 2
    finally:
        indexer.close()


def test_fork_indexed_before_parent_counts_stored_rows(tmp_path, monkeypatch):
    parent = tmp_path / "parent.jsonl"
    _write_transcript(parent, turns=4)
    fork_rows = [dict(r, sessionId="fork-session") if "uuid" in r else r for r in _rows(4)]
    fork = tmp_path / "fork.jsonl"
    _write_rows(fork, fork_rows)  # same message ids under another session, as forked rollouts do
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(fork)
        indexer.index_conversation(parent)
        counts = dict(
            indexer.conn.execute("SELECT session_id, message_count FROM conversations").fetchall()
        )
        assert counts == {"fork-session": 4, SESSION_ID: 0}
        assert _stamp(indexer, parent)["outcome"] == "indexed"

        indexer.index_conversation(parent)
        assert len(calls) == 2
    finally:
        indexer.close()


def test_summarizer_transcript_is_stamped_and_skipped(tmp_path, monkeypatch):
    transcript = tmp_path / "summarizer.jsonl"
    rows = _rows(2)
    rows[1]["message"]["content"] = "Summarize this conversation. Messages to summarize: ..."
    _write_rows(transcript, rows)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript]
        assert _stamp(indexer, transcript)["outcome"] == "summarizer"
        assert indexer.conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    finally:
        indexer.close()


def test_transcript_without_session_id_is_stamped_and_skipped(tmp_path, monkeypatch):
    transcript = tmp_path / "no-session.jsonl"
    rows = [{k: v for k, v in r.items() if k != "sessionId"} for r in _rows(4)]
    _write_rows(transcript, rows)
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    calls = _parse_counter(indexer, monkeypatch)
    try:
        indexer.index_conversation(transcript)
        indexer.index_conversation(transcript)
        assert calls == [transcript]
        assert _stamp(indexer, transcript)["outcome"] == "no-session-id"
    finally:
        indexer.close()


def test_calculate_depth_handles_branches_duplicates_and_cycles(tmp_path):
    indexer = ConversationIndexer(db_path=str(tmp_path / "index.db"), quiet=True)
    try:
        messages = [
            {"uuid": "r", "parent_uuid": None},
            {"uuid": "a", "parent_uuid": "r"},
            {"uuid": "b", "parent_uuid": "r"},
            {"uuid": "c", "parent_uuid": "a"},
            {"uuid": "c", "parent_uuid": "a"},  # duplicated line
            {"uuid": "x", "parent_uuid": "y"},  # cycle, unreachable from a root
            {"uuid": "y", "parent_uuid": "x"},
            {"uuid": "s", "parent_uuid": ""},  # empty parent counts as a root
        ]
        depths = indexer.calculate_depth(messages, {m["uuid"]: m["parent_uuid"] for m in messages})
        assert depths == {"r": 0, "a": 1, "b": 1, "c": 2, "s": 0}

        chain = [{"uuid": f"m{i}", "parent_uuid": f"m{i - 1}" if i else None} for i in range(20_000)]
        depths = indexer.calculate_depth(chain, {m["uuid"]: m["parent_uuid"] for m in chain})
        assert depths["m19999"] == 19_999
    finally:
        indexer.close()


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
