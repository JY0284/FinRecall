from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
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
        trace_teacher_replay: bool | None = None,
    ) -> None:
        self.storage = storage or SearchStore(db_path or default_db_path())
        self.provider = provider or default_provider()
        self.fetcher = fetcher or _default_fetcher
        self.cache_ttl_seconds = cache_ttl_seconds or int(
            os.environ.get("FINRECALL_CACHE_TTL_SECONDS", "900")
        )
        self.trace_teacher_replay = (
            _env_flag("FINRECALL_TRACE_TEACHER_REPLAY", default=False)
            if trace_teacher_replay is None
            else bool(trace_teacher_replay)
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
                "trace_teacher_replay": self.trace_teacher_replay,
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
        if self.trace_teacher_replay and (
            teacher_results := self._trace_teacher_results(
                query,
                max_results=max_results,
                topics=topics,
            )
        ):
            return self.storage.record_search_event(
                cache_key=cache_key,
                query=query,
                max_results=max_results,
                topic=topic,
                time_window=time_window,
                topics=topics,
                caller=caller,
                source="trace_teacher_archive",
                results=teacher_results,
                error=None,
                ttl_seconds=self.cache_ttl_seconds,
                observed_at=observed_at,
            )

        try:
            provider_items = self.provider.search(
                query,
                max_results=max_results,
                topic=topic,
                time_window=time_window,
            )
            source = str(getattr(self.provider, "source_name", "finrecall"))
            results = [
                self._provider_item_to_result(item, topic=topic, expected_topics=topics)
                for item in provider_items
            ]
            results = _filter_low_value_native_results(query, source=source, results=results)
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

    def _trace_teacher_results(
        self,
        query: str,
        *,
        max_results: int,
        topics: list[str] | None,
    ) -> list[SearchResult]:
        seen: set[str] = set()
        results: list[SearchResult] = []
        for document in self.storage.trace_teacher_documents_for_query(query, limit=max_results):
            if not _is_trace_teacher_usable_for_query(query, document):
                continue
            canonical = canonicalize_url(document.canonical_url or document.url)
            seen.add(canonical)
            results.append(self._document_to_result(document))
        if len(results) >= max_results:
            return results

        for document in self._similar_trace_teacher_documents(query, max_results=max_results):
            if not _is_trace_teacher_usable_for_query(query, document):
                continue
            canonical = canonicalize_url(document.canonical_url or document.url)
            if canonical in seen:
                continue
            seen.add(canonical)
            results.append(self._document_to_result(document))
            if len(results) >= max_results:
                return results

        for candidate in _teacher_query_candidates(query):
            archive = self.storage.search_archive(
                candidate,
                limit=max(max_results * 4, 10),
                topics=topics,
            )
            for document in archive.results:
                if not _is_trace_teacher_document(document):
                    continue
                if not _is_trace_teacher_usable_for_query(query, document):
                    continue
                canonical = canonicalize_url(document.canonical_url or document.url)
                if canonical in seen:
                    continue
                seen.add(canonical)
                results.append(self._document_to_result(document))
                if len(results) >= max_results:
                    return results
        return results

    def _similar_trace_teacher_documents(self, query: str, *, max_results: int) -> list[DocumentRecord]:
        tokens = _significant_query_tokens(query)
        if len(tokens) < 2:
            return []
        candidates = self.storage.trace_teacher_documents_matching_terms(
            tokens,
            limit=max(max_results * 30, 100),
        )
        scored: list[tuple[float, DocumentRecord]] = []
        for document in candidates:
            score = _trace_teacher_similarity_score(query, tokens, document)
            if score >= _minimum_trace_teacher_similarity(tokens):
                scored.append((score, document))
        scored.sort(
            key=lambda item: (
                item[0],
                -(item[1].raw_payload.get("rank") or 999999),
                item[1].observed_at.timestamp() if item[1].observed_at else 0.0,
            ),
            reverse=True,
        )
        return [document for _, document in scored[:max_results]]

    def _document_to_result(self, document: DocumentRecord) -> SearchResult:
        canonical = canonicalize_url(document.canonical_url or document.url)
        return SearchResult(
            title=document.title,
            url=canonical,
            content=document.content,
            published_at=document.published_at,
            updated_at=document.updated_at,
            raw_date_text=document.raw_date_text,
            date_source=document.date_source,
            date_confidence=document.date_confidence,
            source=document.source_domain or source_domain(canonical),
            topics=document.topics,
            mentions=document.mentions,
            raw=document.raw_payload,
            score=document.score,
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
            "trace_teacher_replay": self.trace_teacher_replay,
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


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def _is_trace_teacher_document(document: DocumentRecord) -> bool:
    return (
        document.raw_payload.get("source") == "tool_web_search_trace"
        and document.raw_payload.get("teacher") == "tavily"
    )


def _teacher_query_candidates(query: str) -> list[str]:
    raw = query.strip()
    if not raw:
        return []
    tokens = [
        token
        for token in raw.split()
        if token and token.lower() not in {"news", "latest", "update"}
    ]
    filtered = [
        token
        for token in tokens
        if token not in {"最新", "消息", "新闻", "最新消息", "相关", "资料"}
        and not _looks_like_date_token(token)
    ]
    candidates = [raw]
    if filtered and " ".join(filtered) != raw:
        candidates.append(" ".join(filtered))
    if len(filtered) > 4:
        candidates.append(" ".join(filtered[:4]))
    return list(dict.fromkeys(candidates))


def _looks_like_date_token(token: str) -> bool:
    lowered = token.lower()
    if lowered.isdigit() and len(lowered) == 4:
        return True
    return any(unit in lowered for unit in ("年", "月", "日")) and any(char.isdigit() for char in lowered)


def _significant_query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)
    significant: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = token.lower()
        if normalized in {"news", "latest", "update"}:
            continue
        if token in {"最新", "消息", "新闻", "最新消息", "相关", "资料", "分析", "动态"}:
            continue
        if _looks_like_date_token(token):
            continue
        if len(token) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        significant.append(token)
    return significant[:10]


def _trace_teacher_similarity_score(query: str, tokens: list[str], document: DocumentRecord) -> float:
    raw_query = str(document.raw_payload.get("query") or "")
    haystack = f"{raw_query}\n{document.title}\n{document.content}".lower()
    score = sum(1.0 for token in tokens if token.lower() in haystack)
    ticker_tokens = [token for token in tokens if token.isdigit() and len(token) == 6]
    if ticker_tokens:
        if any(token in haystack for token in ticker_tokens):
            score += 2.0
        else:
            return 0.0
    query_lower = query.lower()
    raw_query_lower = raw_query.lower()
    if raw_query_lower and query_lower == raw_query_lower:
        score += 4.0
    return score


def _minimum_trace_teacher_similarity(tokens: list[str]) -> float:
    ticker_bonus = 2.0 if any(token.isdigit() and len(token) == 6 for token in tokens) else 0.0
    return min(5.0, max(2.0, len(tokens) * 0.55)) + ticker_bonus


LOW_VALUE_NATIVE_SOURCES = {
    "eastmoney_notice",
    "sina_notice",
    "xueqiu_stock",
    "eastmoney_quote",
    "sina_quote",
    "eastmoney_kline",
    "eastmoney_moneyflow_stock",
    "eastmoney_search",
    "stcn_search",
    "cls_search",
}
MARKET_DATA_NATIVE_SOURCES = {
    "eastmoney_quote",
    "sina_quote",
    "eastmoney_kline",
    "eastmoney_realtime_quote",
    "investing_us_indices",
    "yahoo_us_market",
    "investing_sp_info_tech",
    "investing_philly_semiconductor",
    "yahoo_xbi",
    "investing_xbi",
    "spglobal_biotech_index",
    "moomoo_xbi",
    "sina_a_share_market",
    "eastmoney_a_share_market",
    "csindex_star50",
    "cnfin_star50_semiconductor",
    "lixinger_star_semiconductor_pe",
    "investing_a50",
    "ftse_russell",
}


def _filter_low_value_native_results(
    query: str,
    *,
    source: str,
    results: list[SearchResult],
) -> list[SearchResult]:
    if source != "native_finance":
        return results
    if _query_explicitly_requests_announcements(query):
        return [
            result
            for result in results
            if str(result.raw.get("native_source") or "")
            in {"sse_notice", "szse_notice", "bse_notice", "cninfo_notice", "sse_notice_document"}
        ]
    if _query_explicitly_requests_market_data(query):
        return [
            result
            for result in results
            if str(result.raw.get("native_source") or "") in MARKET_DATA_NATIVE_SOURCES
        ]
    if _query_explicitly_requests_moneyflow(query):
        return [
            result
            for result in results
            if str(result.raw.get("native_source") or "")
            in {
                "eastmoney_moneyflow_stock",
                "eastmoney_moneyflow",
                "eastmoney_industry_moneyflow",
                "sina_moneyflow",
                "10jqka_moneyflow",
            }
        ]
    return [
        result
        for result in results
        if str(result.raw.get("native_source") or "") not in LOW_VALUE_NATIVE_SOURCES
    ]


def _query_explicitly_requests_announcements(query: str) -> bool:
    return any(
        term in query
        for term in (
            "公告",
            "定期报告",
            "临时公告",
            "研报",
            "减持",
            "限售",
            "解禁",
            "权益变动",
            "询价转让",
            "转让计划",
            "风险提示",
            "异常波动",
            "异动",
            "一季报",
            "季报",
            "年报",
            "半年报",
            "财报",
        )
    )


def _query_explicitly_requests_moneyflow(query: str) -> bool:
    return any(term in query for term in ("主力资金", "资金流向", "龙虎榜", "游资", "庄家", "行业资金", "板块流入"))


LOW_VALUE_TEACHER_URL_MARKERS = (
    "cn.investing.com/equities/",
    "xueqiu.com/s/",
    "quote.eastmoney.com/",
    "vip.stock.finance.sina.com.cn/quotes_service/",
    "fund.eastmoney.com/",
    "basic.10jqka.com.cn/",
)
LOW_VALUE_TEACHER_TITLE_MARKERS = (
    "股票最新价格行情",
    "实时走势图",
    "股价分析预测",
    "行情走势",
    "k线图",
    "f10",
)


def _is_trace_teacher_usable_for_query(query: str, document: DocumentRecord) -> bool:
    if _query_explicitly_requests_market_data(query):
        return True
    if not any(term in query for term in ("新闻", "公告", "最新消息", "最新动态")):
        return True
    url = canonicalize_url(document.canonical_url or document.url).lower()
    title = document.title.lower()
    if any(marker in url for marker in LOW_VALUE_TEACHER_URL_MARKERS):
        return False
    return not any(marker in title for marker in LOW_VALUE_TEACHER_TITLE_MARKERS)


def _query_explicitly_requests_market_data(query: str) -> bool:
    return any(term in query for term in ("行情", "走势", "收盘价", "涨跌幅", "跌幅", "股价", "价格", "K线", "净值", "换手率"))
