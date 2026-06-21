from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
from typing import Any, Callable
from urllib.request import Request, urlopen

from finrecall.extract import EXTRACTION_VERSION, extract_document
from finrecall.models import (
    ArchiveSearchOutcome,
    DocumentRecord,
    ProviderSearchItem,
    SearchError,
    SearchOutcome,
    SearchResult,
)
from finrecall.native_finance import NativeFinanceProvider
from finrecall.providers import ProviderError
from finrecall.storage import SearchStore
from finrecall.topics import classify_topics
from finrecall.utils import (
    canonicalize_url,
    source_domain,
    stable_json_hash,
    utc_now,
)

Fetcher = Callable[[str], dict[str, Any]]


class FinRecallClient:
    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        provider: Any | None = None,
        fetcher: Fetcher | None = None,
        cache_ttl_seconds: int | None = None,
        storage: SearchStore | None = None,
    ) -> None:
        self.storage = storage or SearchStore(db_path or default_db_path())
        self.provider = provider or default_provider()
        self.fetcher = fetcher or _default_fetcher
        self.cache_ttl_seconds = cache_ttl_seconds or int(
            os.environ.get("FINRECALL_CACHE_TTL_SECONDS", "900")
        )
        self._singleflight_lock = threading.Lock()
        self._singleflight: dict[str, threading.Event] = {}

    def search_web(
        self,
        query: str,
        *,
        max_results: int = 3,
        topic: str = "general",
        time_window: str | None = None,
        topics: list[str] | None = None,
        force_refresh: bool = False,
        caller: str | None = None,
    ) -> SearchOutcome:
        max_results = min(max(1, int(max_results)), 10)
        cache_key = stable_json_hash(
            {
                "query": query.strip(),
                "max_results": max_results,
                "topic": topic,
                "time_window": time_window,
                "topics": sorted(topics or []),
            }
        )
        if not force_refresh:
            cached = self.storage.get_cached_search(cache_key)
            if cached is not None:
                return cached

        owner = False
        with self._singleflight_lock:
            event = self._singleflight.get(cache_key)
            if event is None:
                event = threading.Event()
                self._singleflight[cache_key] = event
                owner = True

        if not owner:
            event.wait()
            cached = self.storage.get_cached_search(cache_key)
            if cached is not None:
                return cached

        try:
            return self._search_web_uncached(
                cache_key=cache_key,
                query=query,
                max_results=max_results,
                topic=topic,
                time_window=time_window,
                topics=topics,
                caller=caller,
            )
        finally:
            if owner:
                with self._singleflight_lock:
                    self._singleflight.pop(cache_key, None)
                    event.set()

    def _search_web_uncached(
        self,
        *,
        cache_key: str,
        query: str,
        max_results: int,
        topic: str,
        time_window: str | None,
        topics: list[str] | None,
        caller: str | None,
    ) -> SearchOutcome:
        observed_at = utc_now()
        try:
            provider_items = self.provider.search(
                query,
                max_results=max_results,
                topic=topic,
                time_window=time_window,
            )
            results = [
                self._provider_item_to_result(item, topic=topic, expected_topics=topics)
                for item in provider_items
            ]
            error = None
        except ProviderError as exc:
            results = []
            error = exc.to_search_error()
        except Exception as exc:  # noqa: BLE001
            results = []
            error = SearchError(
                message=str(exc),
                error_class=exc.__class__.__name__,
                retryable=False,
            )

        ttl = self.cache_ttl_seconds if error is None else min(self.cache_ttl_seconds, 15)
        source = str(getattr(self.provider, "source_name", "finrecall"))
        return self.storage.record_search_event(
            cache_key=cache_key,
            query=query,
            max_results=max_results,
            topic=topic,
            time_window=time_window,
            topics=topics,
            caller=caller,
            source=source,
            results=results,
            error=error,
            ttl_seconds=ttl,
            observed_at=observed_at,
        )

    def search_archive(
        self,
        query: str,
        *,
        limit: int = 10,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        topics: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> ArchiveSearchOutcome:
        return self.storage.search_archive(
            query,
            limit=limit,
            published_after=published_after,
            published_before=published_before,
            topics=topics,
            sources=sources,
        )

    def fetch_and_store(
        self,
        url: str,
        *,
        force_refresh: bool = False,
        expected_topics: list[str] | None = None,
    ) -> DocumentRecord:
        canonical = canonicalize_url(url)
        if not force_refresh:
            existing = self.storage.get_document(canonical)
            if existing and existing.fetched_at and existing.content:
                return existing

        started_at = utc_now()
        try:
            response = self.fetcher(url)
            status = int(response.get("status") or 0)
            headers = {str(k).lower(): str(v) for k, v in dict(response.get("headers") or {}).items()}
            body = bytes(response.get("body") or b"")
            final_url = str(response.get("final_url") or url)
            extracted = extract_document(final_url, body, headers=headers)
            classification = classify_topics(
                title=extracted.title,
                content=extracted.content,
                expected_topics=expected_topics,
            )
            document = DocumentRecord(
                url=canonicalize_url(final_url),
                canonical_url=canonicalize_url(final_url),
                title=extracted.title,
                content=extracted.content,
                source_domain=source_domain(canonicalize_url(final_url)),
                observed_at=started_at,
                fetched_at=utc_now(),
                published_at=extracted.published_at,
                updated_at=extracted.updated_at,
                raw_date_text=extracted.raw_date_text,
                date_source=extracted.date_source,
                date_confidence=extracted.date_confidence,
                content_hash=extracted.content_hash,
                http_status=status,
                headers=headers,
                raw_payload={"extraction_version": EXTRACTION_VERSION},
                topics=classification.topics,
                mentions=classification.mentions,
            )
            stored = self.storage.upsert_document(document)
            self.storage.record_fetch_attempt(
                url=canonical,
                status=status,
                started_at=started_at,
                finished_at=utc_now(),
                headers=headers,
            )
            return stored
        except Exception as exc:
            error = SearchError(str(exc), error_class=exc.__class__.__name__, retryable=False)
            self.storage.record_fetch_attempt(
                url=canonical,
                status=None,
                started_at=started_at,
                finished_at=utc_now(),
                error=error,
            )
            raise

    def _provider_item_to_result(
        self,
        item: ProviderSearchItem,
        *,
        topic: str,
        expected_topics: list[str] | None,
    ) -> SearchResult:
        canonical = canonicalize_url(item.url)
        provider_topic = str(item.raw.get("category") or topic or "")
        classification = classify_topics(
            title=item.title,
            content=item.content,
            provider_topic=provider_topic,
            expected_topics=expected_topics,
        )
        return SearchResult(
            title=item.title,
            url=canonical,
            content=item.content,
            published_at=item.published_at,
            updated_at=item.updated_at,
            raw_date_text=item.raw_date_text,
            date_source=item.date_source,
            date_confidence=item.date_confidence,
            source=source_domain(canonical),
            topics=classification.topics,
            mentions=classification.mentions,
            raw=item.raw,
            score=_initial_score(item, classification_topics=[topic.topic for topic in classification.topics]),
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return local runtime state for deployment and operations checks."""
        db_path = str(self.storage.path)
        stats = self.storage.stats()
        return {
            "provider": str(getattr(self.provider, "source_name", self.provider.__class__.__name__)),
            "database": {
                "path": db_path,
                "exists": self.storage.path.exists(),
            },
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "stats": stats,
            "checks": {
                "database": "ok",
            },
        }


def default_db_path() -> Path:
    configured = os.environ.get("FINRECALL_DB")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "finrecall" / "finrecall.sqlite"


def default_provider() -> Any:
    provider_name = os.environ.get("FINRECALL_PROVIDER", "native").strip().lower()
    if provider_name in {"native", "native_finance", "finance"}:
        return NativeFinanceProvider()
    raise ValueError(
        "Unsupported FINRECALL_PROVIDER. FinRecall v1 ships the native finance "
        "provider only; pass a custom provider to FinRecallClient for experiments."
    )


def _default_fetcher(url: str) -> dict[str, Any]:
    request = Request(url, headers={"user-agent": "finrecall/0.1"})
    timeout = float(os.environ.get("FINRECALL_FETCH_TIMEOUT_SECONDS", "10"))
    with urlopen(request, timeout=timeout) as response:
        return {
            "status": response.status,
            "headers": dict(response.headers.items()),
            "body": response.read(),
            "final_url": response.geturl(),
        }


_DEFAULT_CLIENT_LOCK = threading.Lock()
_DEFAULT_CLIENT: FinRecallClient | None = None


def get_default_client() -> FinRecallClient:
    global _DEFAULT_CLIENT
    with _DEFAULT_CLIENT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = FinRecallClient()
        return _DEFAULT_CLIENT


def reset_default_client() -> None:
    global _DEFAULT_CLIENT
    with _DEFAULT_CLIENT_LOCK:
        _DEFAULT_CLIENT = None


def search_web(
    query: str,
    max_results: int = 3,
    topic: str = "general",
    time_window: str | None = None,
    topics: list[str] | None = None,
    force_refresh: bool = False,
    caller: str | None = None,
) -> SearchOutcome:
    return get_default_client().search_web(
        query,
        max_results=max_results,
        topic=topic,
        time_window=time_window,
        topics=topics,
        force_refresh=force_refresh,
        caller=caller,
    )


def search_archive(
    query: str,
    limit: int = 10,
    published_after: datetime | None = None,
    published_before: datetime | None = None,
    topics: list[str] | None = None,
    sources: list[str] | None = None,
) -> ArchiveSearchOutcome:
    return get_default_client().search_archive(
        query,
        limit=limit,
        published_after=published_after,
        published_before=published_before,
        topics=topics,
        sources=sources,
    )


def fetch_and_store(
    url: str,
    force_refresh: bool = False,
    expected_topics: list[str] | None = None,
) -> DocumentRecord:
    return get_default_client().fetch_and_store(
        url,
        force_refresh=force_refresh,
        expected_topics=expected_topics,
    )


def _initial_score(item: ProviderSearchItem, *, classification_topics: list[str]) -> float:
    score = 1.0
    if item.published_at:
        score += item.date_confidence
    if "a-share" in classification_topics:
        score += 0.4
    if item.content:
        score += min(0.3, len(item.content) / 1000)
    return score
