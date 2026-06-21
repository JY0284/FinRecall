from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from finrecall.models import DocumentRecord
from finrecall.storage import SearchStore
from finrecall.topics import classify_topics
from finrecall.utils import sha256_text


def run_synthetic_benchmark(
    db_path: str | Path,
    *,
    size: int = 10_000,
    query: str = "利润",
    save: bool = False,
) -> dict[str, float | int]:
    db_path = Path(db_path)
    store = SearchStore(db_path)
    store.migrate()
    started = time.perf_counter()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for idx in range(size):
        title = f"合成财经新闻 {idx}"
        content = f"A股 公司{idx} 600{idx % 1000:03d} 利润 增长 业绩 改善 政策 市场"
        classification = classify_topics(title=title, content=content, expected_topics=["a-share"])
        store.upsert_document(
            DocumentRecord(
                url=f"https://synthetic.example.com/news/{idx}",
                title=title,
                content=content,
                observed_at=base_time + timedelta(minutes=idx),
                published_at=base_time + timedelta(minutes=idx),
                raw_date_text=(base_time + timedelta(minutes=idx)).isoformat(),
                date_source="synthetic",
                date_confidence=1.0,
                content_hash=sha256_text(content),
                topics=classification.topics,
                mentions=classification.mentions,
            )
        )
    ingest_seconds = time.perf_counter() - started

    search_started = time.perf_counter()
    outcome = store.search_archive(query, limit=20, topics=["a-share"])
    search_seconds = time.perf_counter() - search_started
    store.checkpoint()
    db_size = db_path.stat().st_size if db_path.exists() else 0
    metrics = {
        "documents": size,
        "ingest_seconds": ingest_seconds,
        "archive_search_seconds": search_seconds,
        "archive_result_count": len(outcome.results),
        "db_size_bytes": db_size,
    }
    if save:
        baseline_path = db_path.with_suffix(".benchmark.json")
        baseline_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics
