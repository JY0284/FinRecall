from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
import threading
import time

from finrecall import FinRecallClient
from finrecall.models import ProviderSearchItem
from finrecall.providers import ProviderError
from finrecall.storage import SearchStore


class FakeProvider:
    def __init__(self, items: list[ProviderSearchItem], delay: float = 0.0) -> None:
        self.items = items
        self.delay = delay
        self.calls: list[tuple[str, int, str]] = []
        self._lock = threading.Lock()

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        with self._lock:
            self.calls.append((query, max_results, topic))
        if self.delay:
            time.sleep(self.delay)
        return self.items[:max_results]


class FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        self.calls += 1
        raise ProviderError("provider request timed out", error_class="timeout", retryable=True)


def test_migrations_are_idempotent_and_enable_wal(tmp_path) -> None:
    db_path = tmp_path / "research.sqlite"
    store = SearchStore(db_path)

    store.migrate()
    store.migrate()

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert {
        "schema_migrations",
        "search_events",
        "search_results",
        "documents",
        "document_topics",
        "document_mentions",
        "document_fts",
        "fetch_attempts",
    }.issubset(tables)
    assert journal_mode == "wal"
    assert busy_timeout >= 1000


def test_search_web_caches_provider_results_and_indexes_archive(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="贵州茅台一季度利润增长",
                url="https://news.example.com/a?utm_source=x",
                content="贵州茅台 600519 A股 业绩 利润 增长",
                published_at=datetime(2026, 6, 19, 9, 30, tzinfo=timezone.utc),
                raw_date_text="2026-06-19 17:30",
                date_source="provider",
                date_confidence=0.7,
                raw={"category": "finance"},
            )
        ]
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        cache_ttl_seconds=60,
    )

    first = client.search_web("贵州茅台 最新 新闻", max_results=3, topic="news")
    second = client.search_web("贵州茅台 最新 新闻", max_results=3, topic="news")
    archive = client.search_archive(
        "贵州茅台 利润",
        topics=["a-share"],
        published_after=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    assert first.error is None
    assert first.cached is False
    assert first.results[0].url == "https://news.example.com/a"
    assert second.cached is True
    assert len(provider.calls) == 1
    assert archive.results[0].url == "https://news.example.com/a"
    assert "a-share" in {topic.topic for topic in archive.results[0].topics}
    assert any(mention.value == "600519" for mention in archive.results[0].mentions)


def test_search_web_records_provider_errors_without_crashing(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=FailingProvider(),
        cache_ttl_seconds=60,
    )

    outcome = client.search_web("A股 今日新闻", max_results=3)

    assert outcome.results == []
    assert outcome.error is not None
    assert outcome.error.error_class == "timeout"
    assert "timed out" in outcome.error.message
    assert client.storage.stats()["search_events"] == 1


def test_concurrent_identical_queries_dedupe_provider_calls(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="A股政策新闻",
                url="https://news.example.com/policy",
                content="A股 政策 证监会",
            )
        ],
        delay=0.05,
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        cache_ttl_seconds=60,
    )

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(lambda _: client.search_web("A股 政策", max_results=1), range(5)))

    assert [outcome.results[0].url for outcome in outcomes] == [
        "https://news.example.com/policy",
        "https://news.example.com/policy",
        "https://news.example.com/policy",
        "https://news.example.com/policy",
        "https://news.example.com/policy",
    ]
    assert len(provider.calls) == 1
    assert sum(outcome.cached for outcome in outcomes) >= 4


def test_fetch_and_store_extracts_dates_topics_and_mentions(tmp_path) -> None:
    html = """
    <html>
      <head>
        <title>贵州茅台发布业绩公告</title>
        <meta property="article:published_time" content="2026-06-19T10:00:00+08:00">
      </head>
      <body>
        <article>贵州茅台 600519 A股 业绩 公告 政策 现金流改善。</article>
      </body>
    </html>
    """

    def fetcher(url: str):
        return {
            "status": 200,
            "headers": {"content-type": "text/html"},
            "body": html.encode("utf-8"),
            "final_url": url,
        }

    client = FinRecallClient(db_path=tmp_path / "research.sqlite", fetcher=fetcher)

    document = client.fetch_and_store(
        "https://news.example.com/moutai?utm_campaign=test#section",
        expected_topics=["a-share"],
    )
    archive = client.search_archive(
        "现金流 改善",
        topics=["a-share"],
        published_after=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    assert document.url == "https://news.example.com/moutai"
    assert document.title == "贵州茅台发布业绩公告"
    assert document.published_at is not None
    assert document.published_at.isoformat() == "2026-06-19T02:00:00+00:00"
    assert document.date_source == "metadata"
    assert any(topic.topic == "a-share" for topic in document.topics)
    assert any(mention.value == "600519" for mention in document.mentions)
    assert archive.results[0].url == "https://news.example.com/moutai"
