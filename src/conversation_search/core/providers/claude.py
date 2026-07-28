"""Claude Code transcript adapter."""

from pathlib import Path

from .base import TranscriptProvider, iter_jsonl


class ClaudeTranscriptProvider(TranscriptProvider):
    name = "claude"
    transcript_format = "claude-jsonl"

    def detect(self, path: Path) -> bool:
        for row in iter_jsonl(path, max_records=40):
            if (
                row.get("type") in {"user", "assistant", "attachment", "summary"}
                and (
                    "sessionId" in row
                    or "uuid" in row
                    or "message" in row
                    or "attachment" in row
                )
            ):
                return True
        return False

    def matches_session(self, path: Path, session_id: str) -> bool:
        for row in iter_jsonl(path, max_records=200):
            if row.get("sessionId") == session_id:
                return True
        return path.stem == session_id

    def parse(self, path: Path) -> dict:
        # Local import avoids a module cycle while the backward-compatible
        # parser remains publicly available from session_miner.py.
        from conversation_search.core.session_miner import parse_claude_transcript

        summary = parse_claude_transcript(path)
        summary["provider"] = self.name
        summary["transcript_format"] = self.transcript_format
        summary["session_id"] = self.session_id(path)
        summary["project_path"] = self.project_path(path)
        return summary

    def session_id(self, path: Path):
        for row in iter_jsonl(path, max_records=200):
            value = row.get("sessionId")
            if isinstance(value, str) and value:
                return value
        return path.stem

    def project_path(self, path: Path):
        for row in iter_jsonl(path, max_records=200):
            value = row.get("cwd")
            if isinstance(value, str) and value:
                return value
        return None

    def resume_command(self, session_id: str) -> str:
        return f"claude --resume {session_id}"
