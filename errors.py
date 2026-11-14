"""Structured errors. Modules raise these; the pipeline catches them per stage
so one bad clip or stage never kills the whole run (or the batch queue)."""
from __future__ import annotations


class FramezaError(Exception):
    """Base structured error: stage + message + optional detail payload."""

    stage = "general"

    def __init__(self, message: str, detail: str | None = None, retryable: bool = True):
        self.message = message
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"[{self.stage}] {message}" + (f" — {detail}" if detail else ""))


# Backwards-compatibility alias
ClipForgeError = FramezaError


class ConfigError(FramezaError):
    stage = "config"


class IngestError(FramezaError):
    stage = "ingest"


class TranscribeError(FramezaError):
    stage = "transcribe"


class SceneError(FramezaError):
    stage = "scenes"


class HighlightError(FramezaError):
    stage = "highlights"


class CutError(FramezaError):
    stage = "cut"


class ReframeError(FramezaError):
    stage = "reframe"


class CaptionError(FramezaError):
    stage = "captions"


class MetadataError(FramezaError):
    stage = "metadata"


class StyleError(FramezaError):
    stage = "style"


class LLMError(FramezaError):
    stage = "llm"


class JobCancelled(FramezaError):
    """Raised between pipeline stages when the caller requested a cancel.
    Not a failure: the job record is still written with status 'cancelled'."""

    stage = "cancel"


class UploadError(FramezaError):
    stage = "upload"


class UploadQuotaError(UploadError):
    """YouTube API quota exhausted — surfaced as a friendly message, never a crash."""
