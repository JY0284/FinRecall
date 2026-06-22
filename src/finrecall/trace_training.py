from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from finrecall.api import FinRecallClient
from finrecall.models import DocumentRecord
from finrecall.topics import classify_topics
from finrecall.utils import (
    canonicalize_url,
    parse_datetime_text,
    sha256_text,
    source_domain,
    utc_now,
)

SUMMARY_KEYS = (
    "files",
    "tool_calls",
    "results_seen",
    "documents_imported",
    "results_skipped",
)

PORTAL_DOMAINS = {
    "so.eastmoney.com",
    "quote.eastmoney.com",
    "data.eastmoney.com",
    "guba.eastmoney.com",
}
PORTAL_URL_MARKERS = (
    "/search",
    "/web/s",
    "keyword=",
    "vcb_allbulletin",
    "/notices/",
)
PORTAL_TITLE_MARKERS = ("搜索", "财经搜索", "行情", "数据中心", "公告列表")


def discover_trace_files(trace_dir: str | Path, *, limit_files: int | None = None) -> list[Path]:
    root = Path(trace_dir)
    files = sorted(
        (path for path in root.rglob("*.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if limit_files is not None:
        return files[: max(0, limit_files)]
    return files


def import_web_search_traces(
    client: FinRecallClient,
    paths: Iterable[str | Path],
) -> dict[str, int]:
    summary = {key: 0 for key in SUMMARY_KEYS}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        summary["files"] += 1
        _import_trace_file(client, path, summary)
    return summary


def _import_trace_file(client: FinRecallClient, path: Path, summary: dict[str, int]) -> None:
    pending_calls: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            event = _loads_json(line)
            if not isinstance(event, dict):
                continue
            for tool_call in _iter_tool_calls(event):
                call_id = str(tool_call.get("id") or "")
                if call_id:
                    pending_calls[call_id] = tool_call
            tool_result = _tool_result(event, pending_calls)
            if tool_result is None:
                continue
            summary["tool_calls"] += 1
            query = tool_result["query"]
            observed_at = _event_time(event)
            for rank, item in enumerate(tool_result["results"], start=1):
                summary["results_seen"] += 1
                document = _document_from_result(
                    item,
                    query=query,
                    tool_call_id=tool_result["tool_call_id"],
                    trace_file=path,
                    observed_at=observed_at,
                    rank=rank,
                )
                if document is None:
                    summary["results_skipped"] += 1
                    continue
                client.storage.upsert_document(document)
                summary["documents_imported"] += 1


def _iter_tool_calls(event: dict[str, Any]) -> Iterable[dict[str, Any]]:
    raw_calls = event.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        name = raw_call.get("name")
        args = raw_call.get("args")
        if isinstance(function, dict):
            name = name or function.get("name")
            args = args or function.get("arguments")
        if name != "tool_web_search":
            continue
        parsed_args = _loads_json(args) if isinstance(args, str) else args
        calls.append(
            {
                "id": raw_call.get("id") or raw_call.get("tool_call_id"),
                "name": name,
                "args": parsed_args if isinstance(parsed_args, dict) else {},
            }
        )
    return calls


def _tool_result(
    event: dict[str, Any],
    pending_calls: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    call_id = str(event.get("tool_call_id") or event.get("id") or "")
    pending = pending_calls.get(call_id, {})
    name = event.get("name") or event.get("tool_name") or pending.get("name")
    if name != "tool_web_search":
        return None

    payload = _loads_json(event.get("content"))
    if not isinstance(payload, dict):
        return None
    pending_args = pending.get("args") if isinstance(pending.get("args"), dict) else {}
    query = str(payload.get("query") or pending_args.get("query") or "").strip()
    results = payload.get("results")
    if not query or not isinstance(results, list):
        return None
    return {
        "query": query,
        "tool_call_id": call_id,
        "results": [item for item in results if isinstance(item, dict)],
    }


def _document_from_result(
    item: dict[str, Any],
    *,
    query: str,
    tool_call_id: str,
    trace_file: Path,
    observed_at: datetime,
    rank: int,
) -> DocumentRecord | None:
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or item.get("link") or "").strip()
    content = str(item.get("content") or item.get("snippet") or item.get("description") or "").strip()
    if _looks_like_finrecall_result(item) or not _should_import_result(
        title=title,
        url=url,
        content=content,
    ):
        return None

    canonical = canonicalize_url(url)
    published_text = (
        item.get("published_at")
        or item.get("published")
        or item.get("date")
        or item.get("datetime")
        or item.get("raw_date_text")
    )
    published_at = _parse_optional_datetime(published_text)
    classification = classify_topics(title=title, content=content)
    return DocumentRecord(
        url=canonical,
        canonical_url=canonical,
        title=title,
        content=content,
        source_domain=source_domain(canonical),
        observed_at=observed_at,
        published_at=published_at,
        raw_date_text=str(published_text) if published_text else None,
        date_source="trace" if published_at else "none",
        date_confidence=0.5 if published_at else 0.0,
        content_hash=sha256_text(f"{title}\n{content}"),
        raw_payload={
            "source": "tool_web_search_trace",
            "teacher": "tavily",
            "query": query,
            "rank": rank,
            "tool_call_id": tool_call_id,
            "trace_file": str(trace_file),
            "raw_result": item,
        },
        topics=classification.topics,
        mentions=classification.mentions,
    )


def _should_import_result(*, title: str, url: str, content: str) -> bool:
    if not title or not url:
        return False
    canonical = canonicalize_url(url)
    lowered_url = canonical.lower()
    domain = source_domain(canonical)
    if domain in PORTAL_DOMAINS:
        return False
    if any(marker in lowered_url for marker in PORTAL_URL_MARKERS):
        return False
    if len(title) + len(content) < 40:
        return False
    if len(content) < 20 and any(marker in title for marker in PORTAL_TITLE_MARKERS):
        return False
    if any(marker in title for marker in PORTAL_TITLE_MARKERS) and len(content) < 80:
        return False
    return True


def _looks_like_finrecall_result(item: dict[str, Any]) -> bool:
    finrecall_keys = {"published_at", "updated_at", "date_source", "date_confidence", "source"}
    if finrecall_keys.issubset(item):
        return True
    return isinstance(item.get("topics"), list) or isinstance(item.get("mentions"), list)


def _event_time(event: dict[str, Any]) -> datetime:
    ts = event.get("ts") or event.get("timestamp")
    if isinstance(ts, int | float):
        return datetime.fromtimestamp(ts, timezone.utc)
    parsed = _parse_optional_datetime(ts)
    return parsed or utc_now()


def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, timezone.utc)
    if value is None:
        return None
    return parse_datetime_text(str(value))


def _loads_json(raw: Any) -> Any:
    if isinstance(raw, dict | list):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
