import argparse
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from conversation_search.core.session_miner import (
    RESOLUTION_STAGES,
    SCHEMA_VERSION,
    _jsonable,
    add_mine_session_args,
    extract_attachment_error,
    mine_session,
    normalize_exit_code,
    normalize_project_path,
    resolve_session,
    run_mine_session,
)


# ---------------------------------------------------------------------------
# Existing helpers (preserved)
# ---------------------------------------------------------------------------

def test_normalize_exit_code():
    assert normalize_exit_code(0) == 0
    assert normalize_exit_code("200") == 200
    assert normalize_exit_code("") is None
    assert normalize_exit_code("abc") is None


def test_hook_success_is_not_error():
    obj = {
        "attachment": {
            "type": "hook_success",
            "hookName": "PreToolUse:Bash",
            "exitCode": 200,
            "command": "http://127.0.0.1:29063/notify",
        }
    }
    assert extract_attachment_error(obj) is None


def test_hook_non_blocking_error_is_error():
    obj = {
        "timestamp": "2026-05-01T20:03:53.120Z",
        "attachment": {
            "type": "hook_non_blocking_error",
            "hookName": "SessionStart:startup",
            "stderr": "cannot execute binary file",
            "exitCode": 126,
            "command": "sh ${CLAUDE_PLUGIN_ROOT}/scripts/check-peer.sh",
        },
    }
    result = extract_attachment_error(obj)
    assert result is not None
    assert result["hook"] == "SessionStart:startup"
    assert result["exit_code"] == 126


# ---------------------------------------------------------------------------
# Phase 1 — path normalization
# ---------------------------------------------------------------------------

def test_normalize_project_path_returns_correct_value_unchanged():
    assert normalize_project_path("D:/projects/claude-code-config") == "D:/projects/claude-code-config"
    assert normalize_project_path("/home/svere/projects/foo") == "/home/svere/projects/foo"


def test_normalize_project_path_recovers_from_transcript_cwd(tmp_path):
    """When a transcript is provided, the first record's cwd is authoritative
    even if the indexed raw value is degraded."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "user",
            "cwd": "D:\\projects\\claude-code-config",
            "session_id": "abc",
        }) + "\n",
        encoding="utf-8",
    )
    out = normalize_project_path(
        raw_value="D//projects/claude/code/config",  # the degraded form
        transcript_path=transcript,
    )
    assert out == "D:\\projects\\claude-code-config"


def test_normalize_project_path_falls_back_to_encoded_dir():
    """Without a transcript and given a degraded raw value, returning the
    encoded directory name is preferable to perpetuating the degradation."""
    out = normalize_project_path(
        raw_value="D//projects/claude/code/config",
        encoded_dir="D--projects-claude-code-config",
    )
    assert out == "D--projects-claude-code-config"
    assert "//" not in out


def test_normalize_project_path_handles_missing_input():
    assert normalize_project_path() is None
    assert normalize_project_path(None, None, None) is None


def test_normalize_project_path_preserves_correct_unix_path_when_transcript_missing(tmp_path):
    fake = tmp_path / "missing.jsonl"
    out = normalize_project_path(raw_value="/home/u/project", transcript_path=fake)
    assert out == "/home/u/project"


# ---------------------------------------------------------------------------
# Phase 1 — stage-based resolver
# ---------------------------------------------------------------------------

def _stub_tree(monkeypatch, payload):
    """Patch run_cc_tree on the session_miner module to return `payload`."""
    from conversation_search.core import session_miner
    monkeypatch.setattr(session_miner, "run_cc_tree", lambda sid: payload)


def test_resolve_session_unresolved_when_nothing_found(tmp_path, monkeypatch):
    _stub_tree(monkeypatch, {
        "status": "missing",
        "checked": ["PATH: cc-conversation-search"],
        "error": "cc-conversation-search is not installed",
    })

    out = resolve_session(
        "nope-1234",
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    assert out["session_id"] == "nope-1234"
    assert out["resolved"] is False
    assert out["resolution_stage"] is None
    assert out["resolved_path"] is None
    assert out["stages"]["tree"]["status"] == "missing"
    assert out["stages"]["codex_filename_matches"] == []
    assert "codex_filename_matches" not in RESOLUTION_STAGES


def test_resolve_session_tree_error_payload_is_unresolved(tmp_path, monkeypatch):
    """A `tree --json` payload containing an `error` key must be treated as
    unresolved even if the process exited 0."""
    _stub_tree(monkeypatch, {
        "status": "unresolved",
        "checked": ["cc-conversation-search tree"],
        "error": "Session not found: missing-session-id",
        "stdout": '{"error": "Session not found"}',
        "stderr": "",
        "returncode": 0,
    })

    out = resolve_session(
        "missing-session-id",
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    assert out["resolved"] is False
    assert out["resolution_stage"] is None
    assert out["stages"]["tree"]["status"] == "unresolved"
    assert "Session not found" in out["stages"]["tree"]["error"]


def test_resolve_session_explicit_windows_path_wins_when_tree_unresolved(tmp_path, monkeypatch):
    """An explicit Windows-style transcript path must resolve correctly even
    when the tree lookup fails."""
    transcript = tmp_path / "explicit.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "cwd": "C:/work/project"}) + "\n",
        encoding="utf-8",
    )

    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})

    out = resolve_session(
        "any-id",
        explicit_path=str(transcript).replace("\\", "/"),
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    assert out["resolved"] is True
    assert out["resolution_stage"] == "explicit"
    assert out["resolved_path"] is not None
    assert Path(out["resolved_path"]).is_file()
    assert out["project_path"] == "C:/work/project"


def test_resolve_session_codex_match_alone_does_not_resolve(tmp_path, monkeypatch):
    """A session ID that only appears as a Codex-side filename must NOT be
    treated as resolved."""
    codex_dir = tmp_path / ".codex" / "sessions"
    codex_dir.mkdir(parents=True, exist_ok=True)
    codex_match = codex_dir / "incidental-mention-of-feed-me.jsonl"
    codex_match.write_text("{}\n", encoding="utf-8")

    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})

    out = resolve_session(
        "feed-me",
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    assert out["resolved"] is False
    assert out["resolution_stage"] is None
    assert any("incidental-mention-of-feed-me" in p for p in out["stages"]["codex_filename_matches"])


def test_resolve_session_tree_resolution_uses_conversation_file(tmp_path, monkeypatch):
    """When the tree response has a valid conversation_file pointing to a
    real transcript, that's the authoritative resolution."""
    encoded_dir = tmp_path / ".claude" / "projects" / "D--projects-claude-code-config"
    encoded_dir.mkdir(parents=True, exist_ok=True)
    transcript = encoded_dir / "abc-session.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "cwd": "D:\\projects\\claude-code-config"}) + "\n",
        encoding="utf-8",
    )

    _stub_tree(monkeypatch, {
        "status": "resolved",
        "checked": ["cc-conversation-search tree"],
        "metadata": {
            "conversation": {
                "session_id": "abc-session",
                "project_path": "D//projects/claude/code/config",  # the bug
                "conversation_file": str(transcript),
                "first_message_at": "2026-05-01T16:04:54Z",
                "last_message_at": "2026-05-05T13:04:04Z",
                "message_count": 1,
            }
        },
        "stdout": "",
        "stderr": "",
        "returncode": 0,
    })

    out = resolve_session(
        "abc-session",
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    assert out["resolved"] is True
    assert out["resolution_stage"] == "tree"
    assert out["project_path_raw"] == "D//projects/claude/code/config"
    # Normalized must NOT carry the degraded `//` artifact.
    assert out["project_path"] != "D//projects/claude/code/config"
    assert "//" not in out["project_path"]


def test_resolve_session_flat_tree_payload_resilience(tmp_path, monkeypatch):
    """Some tree responses may arrive without the `conversation` wrapper —
    accept both shapes."""
    encoded_dir = tmp_path / ".claude" / "projects" / "X--flat"
    encoded_dir.mkdir(parents=True, exist_ok=True)
    transcript = encoded_dir / "flat-id.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "cwd": "X:\\flat"}) + "\n",
        encoding="utf-8",
    )

    _stub_tree(monkeypatch, {
        "status": "resolved",
        "checked": [],
        "metadata": {
            "session_id": "flat-id",
            "project_path": "X--flat",
            "conversation_file": str(transcript),
        },
        "returncode": 0,
    })

    out = resolve_session(
        "flat-id",
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    assert out["resolved"] is True
    assert out["resolution_stage"] == "tree"
    assert out["tree_metadata"] is not None
    assert out["tree_metadata"].get("session_id") == "flat-id"


def test_resolve_session_schema_is_stable(tmp_path, monkeypatch):
    """The structured output must always include the documented top-level
    keys, regardless of resolution success."""
    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})

    out = resolve_session(
        "anything",
        claude_root=tmp_path / ".claude" / "projects",
        codex_roots=[tmp_path / ".codex"],
    )
    expected_keys = {
        "session_id", "resolved", "resolved_path", "resolution_stage",
        "stages", "tree_metadata", "project_path", "project_path_raw",
    }
    assert set(out.keys()) == expected_keys
    assert set(out["stages"].keys()) == {
        "tree", "explicit", "claude_root", "codex_filename_matches",
    }


# ---------------------------------------------------------------------------
# Phase 2 — _jsonable coercion helper
# ---------------------------------------------------------------------------

def test_jsonable_counter_to_name_count_pairs():
    c = Counter()
    c["Bash"] = 12
    c["Read"] = 5
    c["Edit"] = 1
    out = _jsonable(c)
    assert out == [
        {"name": "Bash", "count": 12},
        {"name": "Read", "count": 5},
        {"name": "Edit", "count": 1},
    ]


def test_jsonable_path_to_string():
    p = Path("/tmp/foo/bar.jsonl")
    out = _jsonable(p)
    assert isinstance(out, str)
    assert out == str(p)


def test_jsonable_datetime_to_isoformat():
    dt = datetime(2026, 5, 1, 12, 30, 45)
    out = _jsonable(dt)
    assert out == dt.isoformat()


def test_jsonable_nested_structure():
    """Cross-platform: Path stringification follows the OS native separator."""
    a_path = Path("/tmp/a")
    src = {
        "a": Counter({"x": 3}),
        "b": [a_path, datetime(2026, 1, 1)],
        "c": (1, 2, 3),
        "d": None,
        "e": "leave alone",
    }
    out = _jsonable(src)
    assert out == {
        "a": [{"name": "x", "count": 3}],
        "b": [str(a_path), "2026-01-01T00:00:00"],
        "c": [1, 2, 3],
        "d": None,
        "e": "leave alone",
    }
    # Round-trip through json.dumps with no default= must succeed
    json.dumps(out)


# ---------------------------------------------------------------------------
# Phase 2 — mine_session() structured output
# ---------------------------------------------------------------------------

def test_mine_session_unresolved_returns_full_schema(tmp_path, monkeypatch):
    """The structured output must always include the documented top-level
    keys, even when resolution fails."""
    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})
    # Don't let mine_session walk the real ~/.claude/projects when the test
    # can't easily inject claude_root through this entry point — use an
    # isolated HOME instead.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    out = mine_session("nope-1234")
    assert out["schema_version"] == SCHEMA_VERSION == 1
    assert out["session_id"] == "nope-1234"
    assert set(out.keys()) == {
        "schema_version", "session_id", "resolution",
        "summary", "db_signals", "recommendations",
    }
    assert out["resolution"]["resolved"] is False
    assert out["summary"] is None
    assert isinstance(out["db_signals"], list)
    assert isinstance(out["recommendations"], list) and len(out["recommendations"]) >= 1


def test_mine_session_full_output_is_json_serializable(tmp_path, monkeypatch):
    """End-to-end: the entire mine_session() return value must round-trip
    through json.dumps with no default= safety net. This is the contract
    that --json mode relies on."""
    encoded_dir = tmp_path / ".claude" / "projects" / "D--projects-claude-code-config"
    encoded_dir.mkdir(parents=True, exist_ok=True)
    transcript = encoded_dir / "abc-session.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"type": "summary", "content": "intro"}),
            json.dumps({"type": "user", "cwd": "D:\\projects\\claude-code-config",
                        "timestamp": "2026-05-01T10:00:00Z",
                        "message": {"content": "hi"}}),
            json.dumps({"type": "assistant",
                        "timestamp": "2026-05-01T10:00:01Z",
                        "message": {"content": [
                            {"type": "tool_use", "name": "Bash",
                             "input": {"command": "ls"}},
                            {"type": "tool_use", "name": "Read",
                             "input": {"file_path": "/tmp/foo"}},
                        ]}}),
        ]) + "\n",
        encoding="utf-8",
    )

    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    out = mine_session("abc-session", transcript=str(transcript))

    # Round-trip with no default= — the test fails if anything leaks
    serialized = json.dumps(out)
    reparsed = json.loads(serialized)

    # Schema invariants
    assert reparsed["schema_version"] == 1
    assert reparsed["resolution"]["resolved"] is True
    assert reparsed["resolution"]["resolution_stage"] == "explicit"
    assert reparsed["summary"] is not None
    assert reparsed["summary"]["records"] == 3
    assert reparsed["summary"]["tool_counts"] == [
        {"name": "Bash", "count": 1},
        {"name": "Read", "count": 1},
    ]
    # files_touched is name/count style with `path` instead of `name`
    assert reparsed["summary"]["files_touched"] == [
        {"path": "/tmp/foo", "count": 1},
    ]
    # cwd recovered for the project_path
    assert reparsed["resolution"]["project_path"] == "D:\\projects\\claude-code-config"


# ---------------------------------------------------------------------------
# Phase 2 — shared CLI helper and execution function
# ---------------------------------------------------------------------------

def test_add_mine_session_args_registers_session_id_transcript_and_json():
    parser = argparse.ArgumentParser()
    add_mine_session_args(parser)
    parsed = parser.parse_args(["abc-id", "--transcript", "x.jsonl", "--json"])
    assert parsed.session_id == "abc-id"
    assert parsed.transcript == "x.jsonl"
    assert parsed.json_output is True


def test_add_mine_session_args_json_defaults_false():
    parser = argparse.ArgumentParser()
    add_mine_session_args(parser)
    parsed = parser.parse_args(["abc-id"])
    assert parsed.json_output is False
    assert parsed.transcript is None


def test_run_mine_session_text_mode_writes_human_report(tmp_path, monkeypatch):
    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    buf = io.StringIO()
    rc = run_mine_session("abc", transcript=None, json_output=False, stream=buf)
    assert rc == 0
    out = buf.getvalue()
    # Text mode keeps the existing section headers
    assert "Resolution" in out
    assert "Session Summary" in out
    assert "Recommendations" in out
    # Must not be a JSON document
    assert not out.lstrip().startswith("{")


def test_run_mine_session_json_mode_writes_parseable_json(tmp_path, monkeypatch):
    _stub_tree(monkeypatch, {"status": "missing", "checked": [], "error": "stub"})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    buf = io.StringIO()
    rc = run_mine_session("abc", transcript=None, json_output=True, stream=buf)
    assert rc == 0
    out = buf.getvalue()
    parsed = json.loads(out)
    assert parsed["schema_version"] == 1
    assert parsed["session_id"] == "abc"
    assert parsed["resolution"]["resolved"] is False
    assert parsed["summary"] is None
