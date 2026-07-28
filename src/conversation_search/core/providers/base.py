"""Shared provider primitives."""

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Iterator, Optional


SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
)


def redact_sensitive_text(value):
    """Best-effort redaction for text copied from transcript tool output."""
    if not isinstance(value, str):
        return value
    redacted = value
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)\\bbearer"):
            redacted = pattern.sub("Bearer <redacted>", redacted)
        else:
            redacted = pattern.sub(r"\1\2<redacted>", redacted)
    return redacted


def iter_jsonl(path: Path, max_records: Optional[int] = None) -> Iterator[Dict]:
    """Yield JSON-object records while ignoring malformed/non-object lines."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            yielded = 0
            for raw_line in handle:
                if max_records is not None and yielded >= max_records:
                    break
                try:
                    row = json.loads(raw_line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(row, dict):
                    continue
                yielded += 1
                yield row
    except OSError:
        return


class TranscriptProvider(ABC):
    """Adapter for one transcript producer."""

    name: str
    transcript_format: str

    @abstractmethod
    def detect(self, path: Path) -> bool:
        """Return true when bounded structural evidence identifies this format."""

    @abstractmethod
    def matches_session(self, path: Path, session_id: str) -> bool:
        """Validate that the transcript belongs to ``session_id``."""

    @abstractmethod
    def parse(self, path: Path) -> dict:
        """Return the normalized transcript summary."""

    @abstractmethod
    def project_path(self, path: Path):
        """Return the transcript's recorded working directory when available."""

    @abstractmethod
    def resume_command(self, session_id: str) -> str:
        """Return the provider-native session resume command."""
