import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve and mine a Claude Code session transcript."
    )
    parser.add_argument("session_id", help="Claude Code session ID to resolve and mine")
    parser.add_argument(
        "--transcript",
        help="Explicit path to a transcript JSONL file. Windows paths are supported.",
    )
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


def run_cc_tree(session_id):
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

    result = subprocess.run(
        [cli, "tree", session_id, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        shell=False,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    payload = None
    error = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            error = "Non-JSON output from cc-conversation-search tree"

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
            "error": error or "Unexpected tree output",
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


def canonicalize_path(path_str):
    if not path_str:
        return None
    try:
        path = Path(path_str).expanduser()
        if path.exists():
            return path.resolve()
        return path
    except OSError:
        return Path(path_str)


def find_transcript_candidates(session_id, explicit_path=None):
    checked = []
    resolved = []
    mentioned = []

    if explicit_path:
        explicit = canonicalize_path(explicit_path)
        checked.append(f"explicit transcript: {explicit}")
        if explicit and explicit.is_file():
            resolved.append(explicit)

    claude_root = Path.home() / ".claude" / "projects"
    checked.append(str(claude_root))
    if claude_root.exists():
        for match in claude_root.rglob(f"{session_id}.jsonl"):
            if match.is_file():
                resolved.append(match.resolve())

    codex_roots = [
        Path.home() / ".codex",
        Path.home() / ".agents",
        Path.home() / "AppData" / "Roaming" / "Codex",
    ]
    for root in codex_roots:
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
        "codex_filename_matches": list(dict.fromkeys(mentioned)),
    }


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


def build_report(session_id, transcript=None):
    tree_result = run_cc_tree(session_id)
    transcript_search = find_transcript_candidates(session_id, transcript)

    resolved_path = None
    raw_metadata = tree_result.get("metadata") if tree_result.get("status") == "resolved" else None
    tree_metadata = raw_metadata.get("conversation", raw_metadata) if isinstance(raw_metadata, dict) else None
    if tree_metadata:
        conversation_file = tree_metadata.get("conversation_file")
        if conversation_file:
            candidate = canonicalize_path(conversation_file)
            if candidate and candidate.is_file():
                resolved_path = candidate
    if not resolved_path and transcript_search["resolved"]:
        resolved_path = transcript_search["resolved"][0]

    transcript_summary = parse_transcript(resolved_path) if resolved_path else None
    db_paths = [
        Path.home() / ".claude" / "research.db",
        Path.home() / ".claude" / "skills" / "autoresearch" / "research.db",
        Path.home() / ".claude" / "skills" / "autoresearch" / "autoresearch.db",
    ]
    db_results = [lookup_db(session_id, db_path) for db_path in db_paths]

    lines = []
    lines.extend(["Resolution", "-" * len("Resolution")])
    lines.append(f"Session ID: {session_id}")
    lines.append(f"cc-conversation-search status: {tree_result.get('status')}")
    if tree_result.get("error"):
        lines.append(f"cc-conversation-search detail: {tree_result['error']}")
    if tree_metadata:
        lines.append(f"Project path: {tree_metadata.get('project_path')}")
        lines.append(f"Conversation file: {tree_metadata.get('conversation_file')}")
        lines.append(f"First message: {tree_metadata.get('first_message_at')}")
        lines.append(f"Last message: {tree_metadata.get('last_message_at')}")
        lines.append(f"Message count: {tree_metadata.get('message_count')}")
        lines.append(f"Indexed at: {tree_metadata.get('indexed_at')}")
    lines.append(f"Resolved transcript: {resolved_path if resolved_path else '(not found)'}")
    lines.append("Checked locations:")
    for item in tree_result.get("checked", []) + transcript_search["checked"]:
        lines.append(f"- {item}")
    if transcript_search["codex_filename_matches"]:
        lines.append("Codex filename matches (not treated as resolution):")
        for match in transcript_search["codex_filename_matches"][:10]:
            lines.append(f"- {match}")

    lines.extend(["", "Session Summary", "-" * len("Session Summary")])
    if not transcript_summary:
        lines.append("Transcript could not be resolved.")
    else:
        lines.append(f"Records: {transcript_summary['records']}")
        lines.append(
            "Time range: "
            f"{transcript_summary['time_range'][0]} -> {transcript_summary['time_range'][1]}"
        )
        lines.append(f"User turns: {transcript_summary['user_turns']}")
        lines.append(f"Assistant turns: {transcript_summary['assistant_turns']}")
        lines.append("Top record types:")
        lines.extend(f"- {line}" for line in format_counter(transcript_summary["record_counts"], 10))
        lines.append("Top attachment types:")
        lines.extend(f"- {line}" for line in format_counter(transcript_summary["attachment_counts"], 10))
        lines.append("Top tool calls:")
        lines.extend(f"- {line}" for line in format_counter(transcript_summary["tool_counts"], 15))

    lines.extend(["", "Evidence", "-" * len("Evidence")])
    if not transcript_summary:
        lines.append("No transcript evidence available.")
    else:
        lines.append("Sample user prompts:")
        lines.extend(f"- {shorten(prompt, 180)}" for prompt in transcript_summary["user_prompts"][:12])
        lines.append("Shell commands:")
        lines.extend(f"- {shorten(command, 220)}" for command in transcript_summary["shell_commands"][:20])
        lines.append("Files touched:")
        lines.extend(
            f"- {path} ({count})"
            for path, count in transcript_summary["files_touched"].most_common(20)
        )
        lines.append("Background or agent summaries:")
        lines.extend(
            f"- {shorten(summary, 220)}"
            for summary in transcript_summary["queue_summaries"][:20]
        )
        lines.append("Errors or failures:")
        if transcript_summary["errors"]:
            for error in transcript_summary["errors"][:20]:
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
    lines.append("- Use cc-conversation-search tree for session IDs and treat JSON errors as unresolved.")
    lines.append("- Prefer explicit transcript paths when working across machines or mixed Windows/Linux environments.")
    lines.append("- Keep Codex transcript references separate from Claude transcript resolution unless the filename itself matches the target session ID.")
    lines.append("- Feed resolved session metadata and transcript-derived metrics into autoresearch after transcript resolution succeeds.")
    return "\n".join(lines)


def main(argv=None):
    configure_stdio()
    args = parse_args(argv)
    print(build_report(args.session_id, args.transcript))
