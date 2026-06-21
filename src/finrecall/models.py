from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _dt_to_str(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class SearchError:
    message: str
    error_class: str = "unknown"
    retryable: bool = False
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "error_class": self.error_class,
            "retryable": self.retryable,
            "status_code": self.status_code,
        }


@dataclass(frozen=True)
class TopicRecord:
    topic: str
    confidence: float
    source: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "confidence": self.confidence,
            "source": self.source,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class MentionRecord:
    kind: str
    value: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "evidence": self.evidence}


@dataclass(frozen=True)
class TopicClassification:
    topics: list[TopicRecord] = field(default_factory=list)
    mentions: list[MentionRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderSearchItem:
    title: str
    url: str
    content: str = ""
    published_at: datetime | None = None
    updated_at: datetime | None = None
    raw_date_text: str | None = None
    date_source: str = "none"
    date_confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str = ""
    published_at: datetime | None = None
    updated_at: datetime | None = None
    raw_date_text: str | None = None
    date_source: str = "none"
    date_confidence: float = 0.0
    source: str | None = None
    topics: list[TopicRecord] = field(default_factory=list)
    mentions: list[MentionRecord] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "published_at": _dt_to_str(self.published_at),
            "updated_at": _dt_to_str(self.updated_at),
            "raw_date_text": self.raw_date_text,
            "date_source": self.date_source,
            "date_confidence": self.date_confidence,
            "source": self.source,
            "topics": [topic.to_dict() for topic in self.topics],
            "mentions": [mention.to_dict() for mention in self.mentions],
            "score": self.score,
        }


@dataclass(frozen=True)
class DocumentRecord:
    url: str
    title: str
    content: str
    id: int | None = None
    canonical_url: str | None = None
    source_domain: str | None = None
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    raw_date_text: str | None = None
    date_source: str = "none"
    date_confidence: float = 0.0
    content_hash: str | None = None
    http_status: int | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    topics: list[TopicRecord] = field(default_factory=list)
    mentions: list[MentionRecord] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "content": self.content,
            "source_domain": self.source_domain,
            "observed_at": _dt_to_str(self.observed_at),
            "fetched_at": _dt_to_str(self.fetched_at),
            "published_at": _dt_to_str(self.published_at),
            "updated_at": _dt_to_str(self.updated_at),
            "raw_date_text": self.raw_date_text,
            "date_source": self.date_source,
            "date_confidence": self.date_confidence,
            "content_hash": self.content_hash,
            "http_status": self.http_status,
            "topics": [topic.to_dict() for topic in self.topics],
            "mentions": [mention.to_dict() for mention in self.mentions],
            "score": self.score,
        }


@dataclass(frozen=True)
class ExtractedDocument:
    url: str
    title: str
    content: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    raw_date_text: str | None = None
    date_source: str = "none"
    date_confidence: float = 0.0
    content_hash: str | None = None


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    error: SearchError | None = None
    source: str = "finrecall"
    cached: bool = False
    observed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [result.to_dict() for result in self.results],
            "error": self.error.to_dict() if self.error else None,
            "source": self.source,
            "cached": self.cached,
            "observed_at": _dt_to_str(self.observed_at),
        }


@dataclass(frozen=True)
class ArchiveSearchOutcome:
    query: str
    results: list[DocumentRecord] = field(default_factory=list)
    total: int = 0
    error: SearchError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [result.to_dict() for result in self.results],
            "total": self.total,
            "error": self.error.to_dict() if self.error else None,
        }
