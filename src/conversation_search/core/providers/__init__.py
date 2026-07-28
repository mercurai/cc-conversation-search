"""Transcript-provider adapters.

The package keeps transcript-format knowledge out of the CLI and index/search
layers.  "Provider" always means the producer of the transcript, not the
consumer invoking the tool.
"""

from pathlib import Path
from typing import Union

from .base import TranscriptProvider
from .claude import ClaudeTranscriptProvider
from .codex import CodexTranscriptProvider

SUPPORTED_PROVIDERS = ("claude", "codex")

_PROVIDERS = {
    "claude": ClaudeTranscriptProvider(),
    "codex": CodexTranscriptProvider(),
}


def detect_transcript_provider(path: Union[str, Path]) -> str:
    """Detect a transcript provider from bounded structural evidence."""
    transcript = Path(path)
    matches = [name for name, provider in _PROVIDERS.items() if provider.detect(transcript)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Unable to detect transcript provider for: {transcript}")
    raise ValueError(f"Ambiguous transcript provider for: {transcript}")


def get_provider(name: str) -> TranscriptProvider:
    """Return a provider adapter, rejecting ambiguous/unknown names."""
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported transcript provider: {name}") from exc


__all__ = [
    "SUPPORTED_PROVIDERS",
    "TranscriptProvider",
    "detect_transcript_provider",
    "get_provider",
]
