"""Codex transcript adapter."""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .base import TranscriptProvider, iter_jsonl, redact_sensitive_text


def _datetime(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"input_text", "output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


class CodexTranscriptProvider(TranscriptProvider):
    name = "codex"
    transcript_format = "codex-jsonl"

    def detect(self, path: Path) -> bool:
        for row in iter_jsonl(path, max_records=40):
            if row.get("type") in {
                "session_meta",
                "turn_context",
                "response_item",
                "event_msg",
                "world_state",
                "inter_agent_communication_metadata",
            } and isinstance(row.get("payload"), dict):
                return True
        return False

    def session_metadata(self, path: Path):
        for row in iter_jsonl(path, max_records=200):
            if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
                return row["payload"]
        return {}

    def matches_session(self, path: Path, session_id: str) -> bool:
        metadata = self.session_metadata(path)
        return session_id in {metadata.get("id"), metadata.get("session_id")}

    def project_path(self, path: Path):
        value = self.session_metadata(path).get("cwd")
        return value if isinstance(value, str) and value else None

    def resume_command(self, session_id: str) -> str:
        return f"codex resume {session_id}"

    def conversation(self, path: Path):
        """Return indexer-compatible metadata and message rows."""
        metadata = self.session_metadata(path)
        session_id = metadata.get("id") or metadata.get("session_id") or path.stem
        messages = []
        previous_uuid = None

        for line_no, row in enumerate(iter_jsonl(path), start=1):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            record_type = row.get("type")
            payload_type = payload.get("type")
            message_type = None
            content = ""

            if record_type == "event_msg" and payload_type == "user_message":
                message_type = "user"
                content = payload.get("message") or payload.get("text") or ""
            elif record_type == "response_item" and payload_type in {
                "message",
                "agent_message",
            }:
                role = payload.get("role")
                if role == "user":
                    # event_msg.user_message is the canonical Codex user-turn
                    # source and avoids indexing the same turn twice.
                    continue
                if role == "assistant" or payload_type == "agent_message":
                    message_type = "assistant"
                    content = _content_text(payload.get("content"))
            elif record_type == "response_item" and payload_type == "function_call":
                message_type = "assistant"
                tool_name = str(payload.get("name", "unknown"))
                arguments = _mapping(payload.get("arguments"))
                command = arguments.get("cmd") or arguments.get("command")
                content = f"[Tool: {tool_name}]"
                if isinstance(command, str) and command:
                    content += "\n" + redact_sensitive_text(command)

            if message_type is None or not isinstance(content, str) or not content:
                continue

            message_uuid = payload.get("id")
            if not isinstance(message_uuid, str) or not message_uuid:
                message_uuid = f"{session_id}:{line_no}:{record_type}:{payload_type}"
            messages.append(
                {
                    "uuid": message_uuid,
                    "parent_uuid": previous_uuid,
                    "is_sidechain": False,
                    "timestamp": row.get("timestamp"),
                    "message_type": message_type,
                    "content": redact_sensitive_text(content),
                    "session_id": session_id,
                }
            )
            previous_uuid = message_uuid

        return (
            {
                "provider": self.name,
                "session_id": session_id,
                "project_path": metadata.get("cwd"),
                "summary": "Codex session",
                "leafUuid": previous_uuid,
            },
            messages,
        )

    def parse(self, path: Path) -> dict:
        counts = Counter()
        payload_counts = Counter()
        tool_counts = Counter()
        files_touched = Counter()
        shell_commands = []
        user_prompts = []
        assistant_messages = []
        queue_summaries = []
        errors = []
        timestamps = []
        metadata = {}

        for line_no, row in enumerate(iter_jsonl(path), start=1):
            record_type = str(row.get("type", "unknown"))
            counts[record_type] += 1
            timestamp = row.get("timestamp")
            parsed_time = _datetime(timestamp)
            if parsed_time:
                timestamps.append(parsed_time)

            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            payload_type = str(payload.get("type", "unknown"))
            payload_counts[payload_type] += 1

            if record_type == "session_meta":
                metadata = payload
                continue

            if record_type == "event_msg" and payload_type == "user_message":
                text = payload.get("message")
                if not isinstance(text, str):
                    text = payload.get("text")
                if isinstance(text, str) and text:
                    user_prompts.append(redact_sensitive_text(text))
                continue

            if record_type == "response_item" and payload_type in {"message", "agent_message"}:
                role = payload.get("role")
                text = _content_text(payload.get("content"))
                if not text:
                    continue
                if role == "user":
                    user_prompts.append(redact_sensitive_text(text))
                elif role == "assistant" or payload_type == "agent_message":
                    assistant_messages.append(redact_sensitive_text(text))
                continue

            if record_type == "response_item" and payload_type == "function_call":
                tool_name = str(payload.get("name", "unknown"))
                tool_counts[tool_name] += 1
                arguments = _mapping(payload.get("arguments"))
                command = arguments.get("cmd") or arguments.get("command")
                if tool_name in {"exec_command", "shell", "bash"} and isinstance(command, str):
                    shell_commands.append(redact_sensitive_text(command))
                for key in ("file_path", "path"):
                    file_path = arguments.get(key)
                    if isinstance(file_path, str) and file_path:
                        files_touched[file_path] += 1
                continue

            if record_type == "response_item" and payload_type == "function_call_output":
                output = _mapping(payload.get("output"))
                exit_code = output.get("exit_code")
                is_error = output.get("is_error") is True or (
                    isinstance(exit_code, int) and exit_code != 0
                )
                if is_error:
                    errors.append(
                        {
                            "timestamp": timestamp,
                            "type": "function_call_output_error",
                            "hook": None,
                            "command": None,
                            "stderr": redact_sensitive_text(
                                output.get("output") or output.get("error") or "tool call failed"
                            ),
                            "exit_code": exit_code,
                        }
                    )
                continue

            if record_type == "event_msg" and payload_type == "mcp_tool_call_end":
                result = payload.get("result")
                if isinstance(result, dict) and (
                    result.get("is_error") is True or result.get("error")
                ):
                    errors.append(
                        {
                            "timestamp": timestamp,
                            "type": "mcp_tool_call_error",
                            "hook": None,
                            "command": None,
                            "stderr": redact_sensitive_text(
                                result.get("error") or result.get("message") or "MCP tool call failed"
                            ),
                            "exit_code": None,
                        }
                    )
                continue

            if record_type in {"event_msg", "inter_agent_communication_metadata"} and (
                payload_type == "sub_agent_activity" or record_type == "inter_agent_communication_metadata"
            ):
                kind = payload.get("kind") or "activity"
                agent = payload.get("agent_path") or payload.get("agent_thread_id") or "agent"
                queue_summaries.append(f"Subagent {kind}: {agent}")

        timestamps.sort()
        session_id = metadata.get("id") or metadata.get("session_id") or path.stem
        return {
            "path": path,
            "provider": self.name,
            "transcript_format": self.transcript_format,
            "session_id": session_id,
            "project_path": metadata.get("cwd"),
            "provider_metadata": {
                key: metadata.get(key)
                for key in ("originator", "model_provider", "source", "thread_source")
                if metadata.get(key) is not None
            },
            "record_counts": counts,
            "payload_counts": payload_counts,
            "attachment_counts": Counter(),
            "tool_counts": tool_counts,
            "files_touched": files_touched,
            "shell_commands": list(dict.fromkeys(shell_commands)),
            "user_prompts": list(dict.fromkeys(user_prompts)),
            "assistant_messages": list(dict.fromkeys(assistant_messages)),
            "queue_summaries": list(dict.fromkeys(queue_summaries)),
            "errors": errors,
            "time_range": (
                timestamps[0].isoformat() if timestamps else None,
                timestamps[-1].isoformat() if timestamps else None,
            ),
            "records": sum(counts.values()),
            "user_turns": len(list(dict.fromkeys(user_prompts))),
            "assistant_turns": len(list(dict.fromkeys(assistant_messages))),
        }
