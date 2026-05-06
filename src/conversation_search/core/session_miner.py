"""Resolve and mine a Claude Code session transcript.

Phase 1 (issue #2): correctness work for stage-based session resolution and
project-path normalization. Phase 2 (issue #3): structured machine-readable
contract via `mine_session()` and `mine-session --json`.

Public surface used by adapters and tests:

    resolve_session(session_id, explicit_path=None, claude_root=None,
                    codex_roots=None) -> dict
        Stage-based resolver. Returns stable structured metadata describing
        each resolution stage that was attempted, what it found, and which
        stage (if any) succeeded. Codex filename matches are recorded as
        evidence-only and never count as resolution.

    mine_session(session_id, transcript=None) -> dict
        Single source of structured truth. Combines `resolve_session`,
        `parse_transcript`, and DB lookups into one fully JSON-serializable
        dict (no Path / datetime / Counter leakage). `build_report` and the
        `--json` CLI mode both source data from this function.

    parse_transcript(path) -> dict
        Low-level transcript JSONL summary. Note: returns Counter and Path
        objects; consumers that need JSON should use `mine_session` instead.

    build_report(session_id, transcript=None) -> str
        Human-readable text report; the existing default CLI output mode.

    add_mine_session_args(parser)
        Shared argparse helper. Registers `session_id`, `--transcript`,
        `--json` on the supplied parser. Used by both `parse_args` (the
        Codex wrapper path) and `cli.py`'s mine-session subparser so the
        flag surface cannot drift between entry points.

    run_mine_session(session_id, transcript, json_output) -> int
        Shared execution function. Both the package CLI and the Codex
        wrapper end up here, ensuring identical output for identical args.

    main(argv=None)
        Entry point used by `codex-skills/claude-session-miner/scripts/
        mine_claude_session.py` via `core.session_miner.main()`.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# Schema version for the structured `mine_session()` / `mine-session --json`
# output contract. Bump on breaking changes; subkey additions are additive.
SCHEMA_VERSION = 1

# Ordered resolution stages. `codex_filename_matches` is intentionally NOT
# present in this list — Codex-side filename matches are evidence only.
RESOLUTION_STAGES = ("tree", "explicit", "claude_root")


def add_mine_session_args(parser):
    """Register the mine-session CLI surface on `parser`.

    Used by both `session_miner.parse_args()` (Codex wrapper path) and
    `cli.py`'s `mine-session` subparser so the flag set cannot drift
    between entry points.
    """
    parser.add_argument(
        "session_id",
        help="Claude Code session ID to resolve and mine",
    )
    parser.add_argument(
        "--transcript",
        help="Explicit path to a transcript JSONL file. Windows paths are supported.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the structured mine_session() result as JSON instead of the text report.",
    )
    return parser


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve and mine a Claude Code session transcript.",
    )
    add_mine_session_args(parser)
    return parser.parse_args(argv)


def configure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def iso_to_datetime(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def shorten(text, limit=240):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def normalize_exit_code(value):
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def safe_quote(value):
    return '"' + str(value).replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------

def canonicalize_path(path_str):
    """Canonicalize a path string. Forward and back slashes both supported,
    `~` expanded. Returns a Path even if the file does not exist."""
    if not path_str:
        return None
    try:
        path = Path(path_str).expanduser()
        if path.exists():
            return path.resolve()
        return path
    except OSError:
        return Path(path_str)


def _read_jsonl_cwd(path, max_lines=200):
    """Scan up to `max_lines` of a JSONL transcript looking for the first
    record that carries a `cwd` field. Returns the cwd string or None.

    Real Claude Code transcripts often begin with summary/compactSummary
    records that have no `cwd`; only user/assistant message records carry
    the working directory. Reading just the first line is not enough.
    """
    if not path:
        return None
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for i, line in enumerate(handle):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                cwd = obj.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        return None
    return None


def normalize_project_path(raw_value=None, transcript_path=None, encoded_dir=None):
    """Return a best-effort original project path for a session.

    Resolution order:
      1. If transcript_path exists, read the first JSONL record and use its
         `cwd` field. This is the authoritative source — Claude Code records
         the working directory on every message.
      2. Otherwise, if `raw_value` looks already-correct (contains `:` or
         starts with `/` and does not contain the `//` artifact), return it.
      3. Otherwise, if `encoded_dir` is provided, return it as-is. The
         encoded directory name is lossless even though it is not pretty.
      4. Otherwise return raw_value unchanged.

    The earlier indexer used `name.replace('-', '/')` which destroys
    information whenever the original path contained literal hyphens (e.g.
    `D:\\projects\\claude-code-config`). We never reproduce that
    transformation here; we either recover the truth from the transcript or
    preserve what we have.
    """
    # Stage 1 — authoritative recovery from transcript
    if transcript_path:
        cwd = _read_jsonl_cwd(transcript_path)
        if cwd:
            return cwd

    # Stage 2 — already-correct value
    if isinstance(raw_value, str) and raw_value:
        looks_degraded = "//" in raw_value and not raw_value.startswith("//")
        looks_correct = (":" in raw_value) or raw_value.startswith("/") or raw_value.startswith("\\")
        if looks_correct and not looks_degraded:
            return raw_value

    # Stage 3 — encoded directory name (lossless even if not pretty)
    if isinstance(encoded_dir, str) and encoded_dir:
        return encoded_dir

    # Stage 4 — give back what we got
    return raw_value


# ---------------------------------------------------------------------------
# Stage 1 of resolution: cc-conversation-search tree
# ---------------------------------------------------------------------------

def run_cc_tree(session_id):
    """Invoke `cc-conversation-search tree <id> --json` and classify the
    result. Treats a JSON payload containing `"error"` as unresolved even
    when the process exited 0.
    """
    cli = shutil.which("cc-conversation-search")
    if not cli:
        return {
            "status": "missing",
            "checked": ["PATH: cc-conversation-search"],
            "error": "cc-conversation-search is not installed",
        }

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    try:
        result = subprocess.run(
            [cli, "tree", session_id, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "failed",
            "checked": ["cc-conversation-search tree"],
            "error": f"subprocess error: {exc}",
        }

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    payload = None
    parse_error = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            parse_error = "Non-JSON output from cc-conversation-search tree"

    # Treat `"error"` in payload as unresolved regardless of exit code.
    if isinstance(payload, dict) and payload.get("error"):
        return {
            "status": "unresolved",
            "checked": ["cc-conversation-search tree"],
            "error": payload["error"],
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }

    if result.returncode != 0:
        return {
            "status": "failed",
            "checked": ["cc-conversation-search tree"],
            "error": stderr or stdout or f"exit {result.returncode}",
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }

    if not isinstance(payload, dict):
        return {
            "status": "failed",
            "checked": ["cc-conversation-search tree"],
            "error": parse_error or "Unexpected tree output",
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }

    return {
        "status": "resolved",
        "checked": ["cc-conversation-search tree"],
        "metadata": payload,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": result.returncode,
    }


def _extract_tree_conversation(tree_result):
    """Pull the `conversation` block from a resolved tree response,
    accommodating both nested and flat shapes."""
    if not isinstance(tree_result, dict):
        return None
    if tree_result.get("status") != "resolved":
        return None
    raw_metadata = tree_result.get("metadata")
    if not isinstance(raw_metadata, dict):
        return None
    conv = raw_metadata.get("conversation")
    if isinstance(conv, dict):
        return conv
    # Flat shape: the metadata itself is the conversation record.
    if "session_id" in raw_metadata or "conversation_file" in raw_metadata:
        return raw_metadata
    return None


# ---------------------------------------------------------------------------
# Stages 2/3: explicit path and raw Claude transcript search
# ---------------------------------------------------------------------------

def find_transcript_candidates(session_id, explicit_path=None, claude_root=None, codex_roots=None):
    """Walk the explicit path, ~/.claude/projects, and Codex-side stores.

    Codex matches are returned as `codex_filename_matches` and are never
    treated as a resolved transcript by `resolve_session`.

    `claude_root` and `codex_roots` are optional injection points used by
    tests; production callers leave them unset.
    """
    checked = []
    resolved = []
    mentioned = []

    explicit_info = {"provided": False, "path": None, "exists": False}
    if explicit_path:
        explicit_info["provided"] = True
        explicit = canonicalize_path(explicit_path)
        explicit_info["path"] = str(explicit) if explicit else None
        checked.append(f"explicit transcript: {explicit}")
        if explicit and explicit.is_file():
            explicit_info["exists"] = True
            resolved.append(explicit)

    if claude_root is None:
        claude_root = Path.home() / ".claude" / "projects"
    claude_root = Path(claude_root)
    checked.append(str(claude_root))
    claude_matches = []
    if claude_root.exists():
        for match in claude_root.rglob(f"{session_id}.jsonl"):
            if match.is_file():
                resolved.append(match.resolve())
                claude_matches.append(match.resolve())

    if codex_roots is None:
        codex_roots = [
            Path.home() / ".codex",
            Path.home() / ".agents",
            Path.home() / "AppData" / "Roaming" / "Codex",
        ]
    for root in codex_roots:
        root = Path(root)
        checked.append(str(root))
        if not root.exists():
            continue
        for pattern in (f"{session_id}.jsonl", f"*{session_id}*.jsonl"):
            for match in root.rglob(pattern):
                if match.is_file():
                    mentioned.append(match.resolve())

    return {
        "checked": list(dict.fromkeys(checked)),
        "resolved": list(dict.fromkeys(resolved)),
        "claude_root_matches": list(dict.fromkeys(claude_matches)),
        "codex_filename_matches": list(dict.fromkeys(mentioned)),
        "explicit": explicit_info,
        "claude_root": str(claude_root),
    }


# ---------------------------------------------------------------------------
# Public stage-based resolver
# ---------------------------------------------------------------------------

def resolve_session(session_id, explicit_path=None, claude_root=None, codex_roots=None):
    """Run all four resolution stages and return a stable dict.

    Schema:
        {
          "session_id": str,
          "resolved": bool,
          "resolved_path": str | None,
          "resolution_stage": "tree" | "explicit" | "claude_root" | None,
          "stages": {
            "tree": {status, checked, error, metadata},
            "explicit": {provided, path, exists},
            "claude_root": {checked, matches},
            "codex_filename_matches": [path, ...]   # evidence only
          },
          "tree_metadata": {...} | None,
          "project_path": str | None,        # normalized
          "project_path_raw": str | None,    # what tree returned
        }

    `claude_root` and `codex_roots` exist for testability.
    """
    tree_result = run_cc_tree(session_id)
    candidates = find_transcript_candidates(
        session_id, explicit_path,
        claude_root=claude_root,
        codex_roots=codex_roots,
    )
    tree_conv = _extract_tree_conversation(tree_result)

    resolved_path = None
    resolution_stage = None

    if tree_conv:
        candidate = canonicalize_path(tree_conv.get("conversation_file"))
        if candidate and candidate.is_file():
            resolved_path = candidate
            resolution_stage = "tree"

    if resolved_path is None and candidates["explicit"]["exists"]:
        resolved_path = canonicalize_path(candidates["explicit"]["path"])
        resolution_stage = "explicit"

    if resolved_path is None and candidates["claude_root_matches"]:
        resolved_path = candidates["claude_root_matches"][0]
        resolution_stage = "claude_root"

    project_path_raw = tree_conv.get("project_path") if isinstance(tree_conv, dict) else None
    encoded_dir = resolved_path.parent.name if resolved_path else None
    project_path = normalize_project_path(
        raw_value=project_path_raw,
        transcript_path=resolved_path,
        encoded_dir=encoded_dir,
    )

    return {
        "session_id": session_id,
        "resolved": resolution_stage is not None,
        "resolved_path": str(resolved_path) if resolved_path else None,
        "resolution_stage": resolution_stage,
        "stages": {
            "tree": {
                "status": tree_result.get("status"),
                "checked": tree_result.get("checked", []),
                "error": tree_result.get("error"),
                "metadata": tree_conv,
            },
            "explicit": candidates["explicit"],
            "claude_root": {
                "checked": candidates["claude_root"],
                "matches": [str(p) for p in candidates["claude_root_matches"]],
            },
            "codex_filename_matches": [str(p) for p in candidates["codex_filename_matches"]],
        },
        "tree_metadata": tree_conv,
        "project_path": project_path,
        "project_path_raw": project_path_raw,
    }


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def extract_user_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(part for part in parts if part).strip()
    return ""


def extract_attachment_error(obj):
    attachment = obj.get("attachment")
    if not isinstance(attachment, dict):
        return None
    attachment_type = attachment.get("type")
    if attachment_type == "hook_success":
        return None
    exit_code = normalize_exit_code(attachment.get("exitCode"))
    if attachment_type in {
        "hook_non_blocking_error",
        "hook_error",
        "tool_error",
        "error",
    } or (exit_code is not None and exit_code != 0):
        return {
            "timestamp": obj.get("timestamp"),
            "type": attachment_type,
            "hook": attachment.get("hookName"),
            "command": attachment.get("command"),
            "stderr": attachment.get("stderr") or attachment.get("message"),
            "exit_code": exit_code,
        }
    return None


def parse_transcript(path):
    counts = Counter()
    tool_counts = Counter()
    attachment_counts = Counter()
    files_touched = Counter()
    shell_commands = []
    user_prompts = []
    queue_summaries = []
    errors = []
    timestamps = []

    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                errors.append(
                    {
                        "timestamp": None,
                        "type": "json_decode_error",
                        "hook": None,
                        "command": None,
                        "stderr": f"Line {line_no}: {exc}",
                        "exit_code": None,
                    }
                )
                continue

            record_type = obj.get("type", "unknown")
            counts[record_type] += 1
            timestamp = obj.get("timestamp")
            dt = iso_to_datetime(timestamp)
            if dt:
                timestamps.append(dt)

            if record_type == "attachment":
                attachment = obj.get("attachment", {})
                if isinstance(attachment, dict):
                    attachment_counts[attachment.get("type", "unknown")] += 1
                maybe_error = extract_attachment_error(obj)
                if maybe_error:
                    errors.append(maybe_error)

            if record_type == "queue-operation":
                summary = shorten(obj.get("content", ""))
                if summary:
                    queue_summaries.append(summary)

            if record_type == "user":
                message = obj.get("message", {})
                prompt_text = extract_user_text(message)
                if prompt_text:
                    user_prompts.append(prompt_text)
                content = message.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result" and item.get("is_error"):
                            errors.append(
                                {
                                    "timestamp": timestamp,
                                    "type": "tool_result_error",
                                    "hook": None,
                                    "command": None,
                                    "stderr": item.get("content"),
                                    "exit_code": None,
                                }
                            )

            if record_type != "assistant":
                continue

            message = obj.get("message", {})
            for item in message.get("content", []):
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                tool_name = item.get("name", "unknown")
                tool_counts[tool_name] += 1
                tool_input = item.get("input", {})
                if not isinstance(tool_input, dict):
                    continue
                file_path = tool_input.get("file_path")
                if file_path:
                    files_touched[file_path] += 1
                if tool_name == "Bash":
                    command = tool_input.get("command")
                    if command:
                        shell_commands.append(command)
                if tool_name == "Agent":
                    description = tool_input.get("description")
                    if description:
                        queue_summaries.append(f"Agent launch: {description}")

    timestamps.sort()
    return {
        "path": path,
        "record_counts": counts,
        "attachment_counts": attachment_counts,
        "tool_counts": tool_counts,
        "files_touched": files_touched,
        "shell_commands": list(dict.fromkeys(shell_commands)),
        "user_prompts": list(dict.fromkeys(user_prompts)),
        "queue_summaries": list(dict.fromkeys(queue_summaries)),
        "errors": errors,
        "time_range": (
            timestamps[0].isoformat() if timestamps else None,
            timestamps[-1].isoformat() if timestamps else None,
        ),
        "records": sum(counts.values()),
        "user_turns": counts.get("user", 0),
        "assistant_turns": counts.get("assistant", 0),
    }


# ---------------------------------------------------------------------------
# Optional autoresearch / research-db lookups
# ---------------------------------------------------------------------------

def lookup_db(session_id, db_path):
    if not db_path.exists():
        return {"path": db_path, "status": "missing", "matches": []}

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return {"path": db_path, "status": "error", "error": str(exc), "matches": []}

    matches = []
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = conn.execute(f"PRAGMA table_info({safe_quote(table)})").fetchall()
            text_columns = [
                row["name"]
                for row in columns
                if isinstance(row["name"], str)
                and (not row["type"] or "CHAR" in row["type"].upper() or "TEXT" in row["type"].upper())
            ]
            if not text_columns:
                continue
            clauses = [f"{safe_quote(col)} = ?" for col in text_columns]
            clauses += [f"{safe_quote(col)} LIKE ?" for col in text_columns]
            params = [session_id for _ in text_columns]
            params += [f"%{session_id}%" for _ in text_columns]
            query = f"SELECT * FROM {safe_quote(table)} WHERE " + " OR ".join(clauses) + " LIMIT 3"
            rows = conn.execute(query, params).fetchall()
            for row in rows:
                row_map = dict(row)
                matches.append(
                    {
                        "table": table,
                        "row": {key: shorten(value, 160) for key, value in row_map.items()},
                    }
                )
    except sqlite3.Error as exc:
        return {"path": db_path, "status": "error", "error": str(exc), "matches": matches}
    finally:
        conn.close()

    return {"path": db_path, "status": "ok", "matches": matches}


def format_counter(counter, limit=None):
    items = counter.most_common(limit)
    if not items:
        return ["(none)"]
    return [f"{name}: {count}" for name, count in items]


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """Recursively coerce a value into something `json.dumps` accepts without
    needing a `default=` fallback.

    - Counter -> list of {"name", "count"} ordered by count desc
    - dict    -> dict with normalized values
    - list/tuple/set -> list with normalized members
    - Path    -> str
    - datetime/date -> isoformat()
    - other   -> str fallback only for unknown non-trivial types
    """
    if isinstance(value, Counter):
        return [{"name": str(name), "count": int(count)} for name, count in value.most_common()]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _summary_to_json(summary: Optional[dict]) -> Optional[dict]:
    """Coerce a parse_transcript() return value into a JSON-clean dict."""
    if summary is None:
        return None
    out = {
        "path": str(summary["path"]) if summary.get("path") else None,
        "records": int(summary.get("records", 0)),
        "user_turns": int(summary.get("user_turns", 0)),
        "assistant_turns": int(summary.get("assistant_turns", 0)),
        "time_range": list(summary.get("time_range", (None, None))),
        "record_counts": _jsonable(summary.get("record_counts", Counter())),
        "attachment_counts": _jsonable(summary.get("attachment_counts", Counter())),
        "tool_counts": _jsonable(summary.get("tool_counts", Counter())),
        "files_touched": [
            {"path": str(p), "count": int(c)}
            for p, c in summary.get("files_touched", Counter()).most_common()
        ],
        "shell_commands": list(summary.get("shell_commands", [])),
        "user_prompts": list(summary.get("user_prompts", [])),
        "queue_summaries": list(summary.get("queue_summaries", [])),
        "errors": [
            {
                "timestamp": e.get("timestamp"),
                "type": e.get("type"),
                "hook": e.get("hook"),
                "command": e.get("command"),
                "stderr": e.get("stderr") if isinstance(e.get("stderr"), (str, type(None))) else str(e.get("stderr")),
                "exit_code": e.get("exit_code"),
            }
            for e in summary.get("errors", [])
        ],
    }
    return out


def _db_signal_to_json(result: dict) -> dict:
    """Coerce a lookup_db() return value into a JSON-clean dict."""
    return {
        "path": str(result.get("path")) if result.get("path") is not None else None,
        "status": result.get("status"),
        "error": result.get("error"),
        "matches": [
            {"table": m.get("table"), "row": _jsonable(m.get("row", {}))}
            for m in result.get("matches", [])
        ],
    }


# ---------------------------------------------------------------------------
# Recommendations (single source — used by both text and JSON modes)
# ---------------------------------------------------------------------------

_RECOMMENDATIONS = (
    "Use cc-conversation-search tree for session IDs and treat JSON errors as unresolved.",
    "Prefer explicit transcript paths when working across machines or mixed Windows/Linux environments.",
    "Keep Codex transcript references separate from Claude transcript resolution unless the filename itself matches the target session ID.",
    "Feed resolved session metadata and transcript-derived metrics into autoresearch after transcript resolution succeeds.",
)


# ---------------------------------------------------------------------------
# mine_session: single source of structured truth
# ---------------------------------------------------------------------------

def mine_session(session_id: str, transcript: Optional[str] = None) -> dict:
    """Run the full mining pipeline and return a fully JSON-serializable dict.

    Both `build_report` (text mode) and `run_mine_session` (--json mode)
    consume this function's output. The dict contains no Path / datetime /
    Counter objects and survives `json.dumps(...)` without `default=`.

    Schema (`schema_version: 1`):
        {
          "schema_version": 1,
          "session_id": "...",
          "resolution": { ... resolve_session output, JSON-clean ... },
          "summary":    { ... parse_transcript output, JSON-clean ... } | null,
          "db_signals": [ {path, status, error, matches}, ... ],
          "recommendations": [ "...", ... ]
        }
    """
    resolution = resolve_session(session_id, transcript)
    resolved_path = Path(resolution["resolved_path"]) if resolution["resolved_path"] else None
    summary_raw = parse_transcript(resolved_path) if resolved_path else None
    db_paths = [
        Path.home() / ".claude" / "research.db",
        Path.home() / ".claude" / "skills" / "autoresearch" / "research.db",
        Path.home() / ".claude" / "skills" / "autoresearch" / "autoresearch.db",
    ]
    db_results = [_db_signal_to_json(lookup_db(session_id, p)) for p in db_paths]

    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "resolution": _jsonable(resolution),
        "summary": _summary_to_json(summary_raw),
        "db_signals": db_results,
        "recommendations": list(_RECOMMENDATIONS),
    }


# ---------------------------------------------------------------------------
# Human-readable report (built from mine_session() output)
# ---------------------------------------------------------------------------

def build_report(session_id, transcript=None):
    data = mine_session(session_id, transcript)
    resolution = data["resolution"]
    tree_metadata = resolution.get("tree_metadata")
    summary = data["summary"]
    db_results = data["db_signals"]

    def _name_count_lines(items, limit):
        items = items[:limit] if limit else items
        if not items:
            return ["(none)"]
        return [f"{it['name']}: {it['count']}" for it in items]

    lines = []
    lines.extend(["Resolution", "-" * len("Resolution")])
    lines.append(f"Session ID: {session_id}")
    lines.append(f"cc-conversation-search status: {resolution['stages']['tree']['status']}")
    if resolution["stages"]["tree"].get("error"):
        lines.append(f"cc-conversation-search detail: {resolution['stages']['tree']['error']}")
    if tree_metadata:
        lines.append(f"Project path (raw): {resolution['project_path_raw']}")
        lines.append(f"Project path (normalized): {resolution['project_path']}")
        lines.append(f"Conversation file: {tree_metadata.get('conversation_file')}")
        lines.append(f"First message: {tree_metadata.get('first_message_at')}")
        lines.append(f"Last message: {tree_metadata.get('last_message_at')}")
        lines.append(f"Message count: {tree_metadata.get('message_count')}")
        lines.append(f"Indexed at: {tree_metadata.get('indexed_at')}")
    elif resolution["project_path"]:
        lines.append(f"Project path (recovered): {resolution['project_path']}")
    resolved_path = resolution["resolved_path"]
    lines.append(f"Resolved transcript: {resolved_path if resolved_path else '(not found)'}")
    if resolution["resolution_stage"]:
        lines.append(f"Resolution stage: {resolution['resolution_stage']}")
    lines.append("Checked locations:")
    for item in resolution["stages"]["tree"]["checked"]:
        lines.append(f"- {item}")
    if resolution["stages"]["explicit"]["provided"]:
        lines.append(
            f"- explicit transcript: {resolution['stages']['explicit']['path']}"
            + (" (exists)" if resolution["stages"]["explicit"]["exists"] else " (missing)")
        )
    lines.append(f"- {resolution['stages']['claude_root']['checked']}")
    if resolution["stages"]["codex_filename_matches"]:
        lines.append("Codex filename matches (evidence only, not treated as resolution):")
        for match in resolution["stages"]["codex_filename_matches"][:10]:
            lines.append(f"- {match}")

    lines.extend(["", "Session Summary", "-" * len("Session Summary")])
    if not summary:
        lines.append("Transcript could not be resolved.")
    else:
        lines.append(f"Records: {summary['records']}")
        lines.append(
            "Time range: "
            f"{summary['time_range'][0]} -> {summary['time_range'][1]}"
        )
        lines.append(f"User turns: {summary['user_turns']}")
        lines.append(f"Assistant turns: {summary['assistant_turns']}")
        lines.append("Top record types:")
        lines.extend(f"- {ln}" for ln in _name_count_lines(summary["record_counts"], 10))
        lines.append("Top attachment types:")
        lines.extend(f"- {ln}" for ln in _name_count_lines(summary["attachment_counts"], 10))
        lines.append("Top tool calls:")
        lines.extend(f"- {ln}" for ln in _name_count_lines(summary["tool_counts"], 15))

    lines.extend(["", "Evidence", "-" * len("Evidence")])
    if not summary:
        lines.append("No transcript evidence available.")
    else:
        lines.append("Sample user prompts:")
        lines.extend(f"- {shorten(prompt, 180)}" for prompt in summary["user_prompts"][:12])
        lines.append("Shell commands:")
        lines.extend(f"- {shorten(command, 220)}" for command in summary["shell_commands"][:20])
        lines.append("Files touched:")
        lines.extend(
            f"- {it['path']} ({it['count']})"
            for it in summary["files_touched"][:20]
        )
        lines.append("Background or agent summaries:")
        lines.extend(
            f"- {shorten(item, 220)}"
            for item in summary["queue_summaries"][:20]
        )
        lines.append("Errors or failures:")
        if summary["errors"]:
            for error in summary["errors"][:20]:
                details = [
                    error.get("type"),
                    error.get("hook"),
                    error.get("command"),
                    error.get("stderr"),
                ]
                lines.append(
                    f"- {shorten(' | '.join(str(part) for part in details if part), 220)}"
                )
        else:
            lines.append("- (none)")

    lines.extend(["", "Signals", "-" * len("Signals")])
    lines.append("Autoresearch and local DB correlation:")
    for result in db_results:
        lines.append(f"- {result['path']}: {result['status']}")
        if result.get("error"):
            lines.append(f"  error: {shorten(result['error'], 200)}")
        for match in result.get("matches", [])[:6]:
            lines.append(f"  match: {match['table']} -> {shorten(match['row'], 220)}")

    lines.extend(["", "Recommendations", "-" * len("Recommendations")])
    for rec in data["recommendations"]:
        lines.append(f"- {rec}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared execution surface
# ---------------------------------------------------------------------------

def run_mine_session(session_id: str, transcript: Optional[str] = None,
                     json_output: bool = False, stream=None) -> int:
    """Render the mine-session result.

    Both `cli.py`'s `cmd_mine_session` and `session_miner.main()` call this
    so the two entry points cannot drift. Returns a process exit code.
    """
    out = stream if stream is not None else sys.stdout
    if json_output:
        data = mine_session(session_id, transcript)
        # No default= — if a non-serializable type leaks through, surface it
        # as a hard failure rather than silently stringify.
        out.write(json.dumps(data, indent=2))
        out.write("\n")
    else:
        out.write(build_report(session_id, transcript))
        out.write("\n")
    return 0


def main(argv=None):
    configure_stdio()
    args = parse_args(argv)
    return run_mine_session(args.session_id, args.transcript, json_output=args.json_output)
