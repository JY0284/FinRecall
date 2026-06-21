from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from finrecall.models import (
    ArchiveSearchOutcome,
    DocumentRecord,
    MentionRecord,
    SearchError,
    SearchOutcome,
    SearchResult,
    TopicRecord,
)
from finrecall.utils import (
    canonicalize_url,
    datetime_from_storage,
    datetime_to_storage,
    source_domain,
    utc_now,
)

SCHEMA_VERSION = 1


class SearchStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._migration_lock = threading.Lock()
        self._write_lock = threading.RLock()
        self._migrated = False

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def migrate(self) -> None:
        with self._migration_lock:
            with self.connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS search_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cache_key TEXT NOT NULL,
                        query TEXT NOT NULL,
                        max_results INTEGER NOT NULL,
                        topic TEXT NOT NULL,
                        time_window TEXT,
                        topics_json TEXT NOT NULL DEFAULT '[]',
                        caller TEXT,
                        source TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        error_class TEXT,
                        error_message TEXT,
                        raw_payload_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_search_events_cache
                        ON search_events(cache_key, expires_at DESC, id DESC);

                    CREATE TABLE IF NOT EXISTS search_results (
                        event_id INTEGER NOT NULL REFERENCES search_events(id) ON DELETE CASCADE,
                        rank INTEGER NOT NULL,
                        canonical_url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        content TEXT NOT NULL,
                        published_at TEXT,
                        updated_at TEXT,
                        raw_date_text TEXT,
                        date_source TEXT NOT NULL DEFAULT 'none',
                        date_confidence REAL NOT NULL DEFAULT 0,
                        raw_payload_json TEXT NOT NULL DEFAULT '{}',
                        PRIMARY KEY (event_id, rank)
                    );

                    CREATE TABLE IF NOT EXISTS documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        canonical_url TEXT NOT NULL UNIQUE,
                        url TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        content_hash TEXT,
                        source_domain TEXT,
                        observed_at TEXT NOT NULL,
                        fetched_at TEXT,
                        published_at TEXT,
                        updated_at TEXT,
                        raw_date_text TEXT,
                        date_source TEXT NOT NULL DEFAULT 'none',
                        date_confidence REAL NOT NULL DEFAULT 0,
                        extraction_version TEXT,
                        http_status INTEGER,
                        headers_json TEXT NOT NULL DEFAULT '{}',
                        raw_payload_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_documents_published_at ON documents(published_at);
                    CREATE INDEX IF NOT EXISTS idx_documents_source_domain ON documents(source_domain);
                    CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);

                    CREATE TABLE IF NOT EXISTS document_topics (
                        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        topic TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        source TEXT NOT NULL,
                        evidence TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (document_id, topic, source, evidence)
                    );
                    CREATE INDEX IF NOT EXISTS idx_document_topics_topic
                        ON document_topics(topic, document_id);

                    CREATE TABLE IF NOT EXISTS document_mentions (
                        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        value TEXT NOT NULL,
                        evidence TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (document_id, kind, value)
                    );
                    CREATE INDEX IF NOT EXISTS idx_document_mentions_value
                        ON document_mentions(kind, value);

                    CREATE TABLE IF NOT EXISTS fetch_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        canonical_url TEXT NOT NULL,
                        url TEXT NOT NULL,
                        status INTEGER,
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        error_class TEXT,
                        error_message TEXT,
                        headers_json TEXT NOT NULL DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
                    USING fts5(document_id UNINDEXED, title, content, tokenize='unicode61')
                    """
                )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, datetime_to_storage(utc_now())),
                )
            self._migrated = True

    def ensure_migrated(self) -> None:
        if not self._migrated:
            self.migrate()

    def get_cached_search(self, cache_key: str, now: datetime | None = None) -> SearchOutcome | None:
        self.ensure_migrated()
        now_text = datetime_to_storage(now or utc_now())
        with self.connect() as conn:
            event = conn.execute(
                """
                SELECT * FROM search_events
                WHERE cache_key = ? AND expires_at > ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (cache_key, now_text),
            ).fetchone()
            if event is None:
                return None
            rows = conn.execute(
                "SELECT * FROM search_results WHERE event_id = ? ORDER BY rank ASC",
                (event["id"],),
            ).fetchall()
            results = [self._search_result_from_row(conn, row) for row in rows]
            error = None
            if event["error_class"] or event["error_message"]:
                error = SearchError(
                    message=event["error_message"] or "Search failed.",
                    error_class=event["error_class"] or "unknown",
                )
            return SearchOutcome(
                query=event["query"],
                results=results,
                error=error,
                source=event["source"],
                cached=True,
                observed_at=datetime_from_storage(event["observed_at"]),
            )

    def record_search_event(  # noqa: PLR0913
        self,
        *,
        cache_key: str,
        query: str,
        max_results: int,
        topic: str,
        time_window: str | None,
        topics: list[str] | None,
        caller: str | None,
        source: str,
        results: list[SearchResult],
        error: SearchError | None,
        ttl_seconds: int,
        observed_at: datetime | None = None,
    ) -> SearchOutcome:
        self.ensure_migrated()
        observed = observed_at or utc_now()
        expires_at = observed + timedelta(seconds=max(1, ttl_seconds))
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO search_events(
                    cache_key, query, max_results, topic, time_window, topics_json, caller, source,
                    observed_at, expires_at, error_class, error_message, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    query,
                    max_results,
                    topic,
                    time_window,
                    json.dumps(topics or [], ensure_ascii=False),
                    caller,
                    source,
                    datetime_to_storage(observed),
                    datetime_to_storage(expires_at),
                    error.error_class if error else None,
                    error.message if error else None,
                    "{}",
                ),
            )
            event_id = int(cursor.lastrowid)
            stored_results: list[SearchResult] = []
            for rank, result in enumerate(results, start=1):
                canonical = canonicalize_url(result.url)
                document = DocumentRecord(
                    url=result.url,
                    canonical_url=canonical,
                    title=result.title,
                    content=result.content,
                    source_domain=source_domain(canonical),
                    observed_at=observed,
                    published_at=result.published_at,
                    updated_at=result.updated_at,
                    raw_date_text=result.raw_date_text,
                    date_source=result.date_source,
                    date_confidence=result.date_confidence,
                    raw_payload=result.raw,
                    topics=result.topics,
                    mentions=result.mentions,
                )
                stored = self._upsert_document(conn, document)
                conn.execute(
                    """
                    INSERT INTO search_results(
                        event_id, rank, canonical_url, title, url, content, published_at,
                        updated_at, raw_date_text, date_source, date_confidence, raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        rank,
                        canonical,
                        result.title,
                        result.url,
                        result.content,
                        datetime_to_storage(result.published_at),
                        datetime_to_storage(result.updated_at),
                        result.raw_date_text,
                        result.date_source,
                        result.date_confidence,
                        json.dumps(result.raw, ensure_ascii=False),
                    ),
                )
                stored_results.append(
                    replace(
                        result,
                        url=stored.canonical_url or canonical,
                        source=stored.source_domain,
                        topics=stored.topics,
                        mentions=stored.mentions,
                    )
                )
        return SearchOutcome(
            query=query,
            results=stored_results,
            error=error,
            source=source,
            cached=False,
            observed_at=observed,
        )

    def get_document(self, url: str) -> DocumentRecord | None:
        self.ensure_migrated()
        canonical = canonicalize_url(url)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE canonical_url = ?",
                (canonical,),
            ).fetchone()
            if row is None:
                return None
            return self._document_from_row(conn, row)

    def upsert_document(self, record: DocumentRecord) -> DocumentRecord:
        self.ensure_migrated()
        with self._write_lock, self.connect() as conn:
            return self._upsert_document(conn, record)

    def record_fetch_attempt(
        self,
        *,
        url: str,
        status: int | None,
        started_at: datetime,
        finished_at: datetime,
        error: SearchError | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        self.ensure_migrated()
        canonical = canonicalize_url(url)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO fetch_attempts(
                    canonical_url, url, status, started_at, finished_at,
                    error_class, error_message, headers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical,
                    url,
                    status,
                    datetime_to_storage(started_at),
                    datetime_to_storage(finished_at),
                    error.error_class if error else None,
                    error.message if error else None,
                    json.dumps(headers or {}, ensure_ascii=False),
                ),
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
        self.ensure_migrated()
        limit = min(max(1, limit), 100)
        with self.connect() as conn:
            rows = self._search_archive_fts(
                conn,
                query,
                limit=limit,
                published_after=published_after,
                published_before=published_before,
                topics=topics,
                sources=sources,
            )
            if query.strip() and not rows:
                rows = self._search_archive_like(
                    conn,
                    query,
                    limit=limit,
                    published_after=published_after,
                    published_before=published_before,
                    topics=topics,
                    sources=sources,
                )
            documents = [self._document_from_row(conn, row) for row in rows]
        return ArchiveSearchOutcome(query=query, results=documents, total=len(documents))

    def stats(self) -> dict[str, int]:
        self.ensure_migrated()
        names = ("documents", "search_events", "search_results", "fetch_attempts")
        with self.connect() as conn:
            return {
                name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in names
            }

    def checkpoint(self) -> None:
        self.ensure_migrated()
        with self.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _upsert_document(self, conn: sqlite3.Connection, record: DocumentRecord) -> DocumentRecord:
        canonical = record.canonical_url or canonicalize_url(record.url)
        domain = record.source_domain or source_domain(canonical)
        observed_at = record.observed_at or utc_now()
        existing = conn.execute(
            "SELECT * FROM documents WHERE canonical_url = ?",
            (canonical,),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO documents(
                    canonical_url, url, title, content, content_hash, source_domain,
                    observed_at, fetched_at, published_at, updated_at, raw_date_text,
                    date_source, date_confidence, extraction_version, http_status,
                    headers_json, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._document_values(record, canonical, domain, observed_at),
            )
            document_id = int(cursor.lastrowid)
        else:
            document_id = int(existing["id"])
            title = record.title or existing["title"]
            content = record.content or existing["content"]
            conn.execute(
                """
                UPDATE documents
                SET url = ?, title = ?, content = ?, content_hash = ?, source_domain = ?,
                    observed_at = ?, fetched_at = COALESCE(?, fetched_at),
                    published_at = COALESCE(?, published_at),
                    updated_at = COALESCE(?, updated_at),
                    raw_date_text = COALESCE(?, raw_date_text),
                    date_source = ?,
                    date_confidence = MAX(date_confidence, ?),
                    extraction_version = COALESCE(?, extraction_version),
                    http_status = COALESCE(?, http_status),
                    headers_json = ?,
                    raw_payload_json = ?
                WHERE id = ?
                """,
                (
                    record.url,
                    title,
                    content,
                    record.content_hash or existing["content_hash"],
                    domain,
                    datetime_to_storage(observed_at),
                    datetime_to_storage(record.fetched_at),
                    datetime_to_storage(record.published_at),
                    datetime_to_storage(record.updated_at),
                    record.raw_date_text,
                    record.date_source if record.date_source != "none" else existing["date_source"],
                    record.date_confidence,
                    record.raw_payload.get("extraction_version"),
                    record.http_status,
                    json.dumps(record.headers, ensure_ascii=False),
                    json.dumps(record.raw_payload, ensure_ascii=False),
                    document_id,
                ),
            )

        conn.execute("DELETE FROM document_topics WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM document_mentions WHERE document_id = ?", (document_id,))
        for topic in record.topics:
            conn.execute(
                """
                INSERT OR REPLACE INTO document_topics(
                    document_id, topic, confidence, source, evidence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, topic.topic, topic.confidence, topic.source, topic.evidence),
            )
        for mention in record.mentions:
            conn.execute(
                """
                INSERT OR REPLACE INTO document_mentions(document_id, kind, value, evidence)
                VALUES (?, ?, ?, ?)
                """,
                (document_id, mention.kind, mention.value, mention.evidence),
            )

        conn.execute("DELETE FROM document_fts WHERE document_id = ?", (document_id,))
        conn.execute(
            "INSERT INTO document_fts(document_id, title, content) VALUES (?, ?, ?)",
            (document_id, record.title, record.content),
        )
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._document_from_row(conn, row)

    def _document_values(
        self,
        record: DocumentRecord,
        canonical: str,
        domain: str,
        observed_at: datetime,
    ) -> tuple[Any, ...]:
        return (
            canonical,
            record.url,
            record.title,
            record.content,
            record.content_hash,
            domain,
            datetime_to_storage(observed_at),
            datetime_to_storage(record.fetched_at),
            datetime_to_storage(record.published_at),
            datetime_to_storage(record.updated_at),
            record.raw_date_text,
            record.date_source,
            record.date_confidence,
            record.raw_payload.get("extraction_version"),
            record.http_status,
            json.dumps(record.headers, ensure_ascii=False),
            json.dumps(record.raw_payload, ensure_ascii=False),
        )

    def _search_archive_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        limit: int,
        published_after: datetime | None,
        published_before: datetime | None,
        topics: list[str] | None,
        sources: list[str] | None,
    ) -> list[sqlite3.Row]:
        where, params = self._archive_filters(
            published_after=published_after,
            published_before=published_before,
            topics=topics,
            sources=sources,
        )
        if query.strip():
            fts_query = _to_fts_query(query)
            sql = f"""
                SELECT d.*, bm25(document_fts) AS score
                FROM document_fts
                JOIN documents d ON d.id = document_fts.document_id
                WHERE document_fts MATCH ? {where}
                ORDER BY score ASC, COALESCE(d.published_at, d.observed_at) DESC
                LIMIT ?
            """
            try:
                return list(conn.execute(sql, [fts_query, *params, limit]).fetchall())
            except sqlite3.OperationalError:
                return []
        sql = f"""
            SELECT d.*, 0.0 AS score
            FROM documents d
            WHERE 1 = 1 {where}
            ORDER BY COALESCE(d.published_at, d.observed_at) DESC
            LIMIT ?
        """
        return list(conn.execute(sql, [*params, limit]).fetchall())

    def _search_archive_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        limit: int,
        published_after: datetime | None,
        published_before: datetime | None,
        topics: list[str] | None,
        sources: list[str] | None,
    ) -> list[sqlite3.Row]:
        where, params = self._archive_filters(
            published_after=published_after,
            published_before=published_before,
            topics=topics,
            sources=sources,
        )
        tokens = _query_tokens(query)
        if not tokens:
            tokens = [query.strip()]
        like_clauses = " AND ".join("(d.title LIKE ? OR d.content LIKE ?)" for _ in tokens)
        like_params: list[str] = []
        for token in tokens:
            like = f"%{token}%"
            like_params.extend([like, like])
        sql = f"""
            SELECT d.*, 0.0 AS score
            FROM documents d
            WHERE {like_clauses} {where}
            ORDER BY COALESCE(d.published_at, d.observed_at) DESC
            LIMIT ?
        """
        return list(conn.execute(sql, [*like_params, *params, limit]).fetchall())

    def _archive_filters(
        self,
        *,
        published_after: datetime | None,
        published_before: datetime | None,
        topics: list[str] | None,
        sources: list[str] | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if published_after:
            clauses.append("AND d.published_at >= ?")
            params.append(datetime_to_storage(published_after))
        if published_before:
            clauses.append("AND d.published_at <= ?")
            params.append(datetime_to_storage(published_before))
        if topics:
            placeholders = ", ".join("?" for _ in topics)
            clauses.append(
                f"""
                AND EXISTS (
                    SELECT 1 FROM document_topics dt
                    WHERE dt.document_id = d.id AND dt.topic IN ({placeholders})
                )
                """
            )
            params.extend(topics)
        if sources:
            placeholders = ", ".join("?" for _ in sources)
            clauses.append(f"AND d.source_domain IN ({placeholders})")
            params.extend(sources)
        return " ".join(clauses), params

    def _search_result_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> SearchResult:
        document = conn.execute(
            "SELECT * FROM documents WHERE canonical_url = ?",
            (row["canonical_url"],),
        ).fetchone()
        topics: list[TopicRecord] = []
        mentions: list[MentionRecord] = []
        source = None
        if document is not None:
            source = document["source_domain"]
            topics = self._topics_for_document(conn, int(document["id"]))
            mentions = self._mentions_for_document(conn, int(document["id"]))
        return SearchResult(
            title=row["title"],
            url=row["canonical_url"],
            content=row["content"],
            published_at=datetime_from_storage(row["published_at"]),
            updated_at=datetime_from_storage(row["updated_at"]),
            raw_date_text=row["raw_date_text"],
            date_source=row["date_source"],
            date_confidence=float(row["date_confidence"]),
            source=source,
            topics=topics,
            mentions=mentions,
            raw=_loads(row["raw_payload_json"]),
        )

    def _document_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> DocumentRecord:
        document_id = int(row["id"])
        return DocumentRecord(
            id=document_id,
            url=row["url"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            content=row["content"],
            content_hash=row["content_hash"],
            source_domain=row["source_domain"],
            observed_at=datetime_from_storage(row["observed_at"]),
            fetched_at=datetime_from_storage(row["fetched_at"]),
            published_at=datetime_from_storage(row["published_at"]),
            updated_at=datetime_from_storage(row["updated_at"]),
            raw_date_text=row["raw_date_text"],
            date_source=row["date_source"],
            date_confidence=float(row["date_confidence"]),
            http_status=row["http_status"],
            headers=_loads(row["headers_json"]),
            raw_payload=_loads(row["raw_payload_json"]),
            topics=self._topics_for_document(conn, document_id),
            mentions=self._mentions_for_document(conn, document_id),
            score=float(row["score"]) if "score" in row.keys() and row["score"] is not None else 0.0,
        )

    def _topics_for_document(self, conn: sqlite3.Connection, document_id: int) -> list[TopicRecord]:
        rows = conn.execute(
            """
            SELECT topic, confidence, source, evidence
            FROM document_topics
            WHERE document_id = ?
            ORDER BY confidence DESC, topic ASC
            """,
            (document_id,),
        ).fetchall()
        return [
            TopicRecord(row["topic"], float(row["confidence"]), row["source"], row["evidence"])
            for row in rows
        ]

    def _mentions_for_document(self, conn: sqlite3.Connection, document_id: int) -> list[MentionRecord]:
        rows = conn.execute(
            """
            SELECT kind, value, evidence
            FROM document_mentions
            WHERE document_id = ?
            ORDER BY kind ASC, value ASC
            """,
            (document_id,),
        ).fetchall()
        return [MentionRecord(row["kind"], row["value"], row["evidence"]) for row in rows]


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _to_fts_query(query: str) -> str:
    tokens = _query_tokens(query)
    if not tokens:
        return query
    return " ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens)


def _query_tokens(query: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", query)
