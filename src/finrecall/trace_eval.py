from __future__ import annotations

from collections import Counter
from typing import Any

from finrecall.api import FinRecallClient
from finrecall.models import DocumentRecord, SearchResult
from finrecall.utils import canonicalize_url, source_domain

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
PORTAL_TITLE_MARKERS = ("搜索", "财经搜索", "行情", "数据中心", "公告列表", "行情中心")
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
STRUCTURED_NATIVE_SOURCES = {
    "sse_notice",
    "szse_notice",
    "bse_notice",
    "cninfo_notice",
    "sse_notice_document",
    "eastmoney_realtime_quote",
    "eastmoney_moneyflow_stock",
    "eastmoney_moneyflow",
    "eastmoney_industry_moneyflow",
    "sina_moneyflow",
    "10jqka_moneyflow",
    "sina_a_share_market",
    "eastmoney_a_share_market",
    "investing_us_indices",
    "yahoo_us_market",
    "investing_sp_info_tech",
    "investing_philly_semiconductor",
    "yahoo_xbi",
    "investing_xbi",
    "spglobal_biotech_index",
    "moomoo_xbi",
    "csindex_star50",
    "cnfin_star50_semiconductor",
    "lixinger_star_semiconductor_pe",
    "investing_a50",
    "ftse_russell",
    "eastmoney_fund",
    "eastmoney_fund_nav",
    "eastmoney_fund_ranking",
}


def compare_trace_teacher_results(
    client: FinRecallClient,
    *,
    max_cases: int = 50,
    max_results: int = 5,
    topic: str = "general",
    time_window: str | None = None,
    include_results: bool = False,
    snippet_chars: int = 180,
) -> dict[str, Any]:
    queries = client.storage.trace_teacher_queries(limit=max_cases)
    cases: list[dict[str, Any]] = []
    summary = {
        "query_count": 0,
        "finrecall_empty_query_count": 0,
        "finrecall_result_count": 0,
        "teacher_result_count": 0,
        "portal_result_count": 0,
        "thin_result_count": 0,
        "duplicate_url_count": 0,
        "duplicate_domain_count": 0,
        "teacher_domain_overlap_count": 0,
    }

    original_replay = client.trace_teacher_replay
    client.trace_teacher_replay = False
    try:
        for query in queries:
            teacher_documents = client.storage.trace_teacher_documents_for_query(
                query,
                limit=max_results,
            )
            outcome = client.search_web(
                query,
                max_results=max_results,
                topic=topic,
                time_window=time_window,
                force_refresh=True,
                caller="trace_eval",
            )
            metrics = _quality_metrics(outcome.results, teacher_documents)
            issues = _case_issues(metrics)
            case = {
                "query": query,
                "teacher": {
                    "result_count": len(teacher_documents),
                    "domains": sorted(_document_domains(teacher_documents)),
                },
                "finrecall": {
                    "source": outcome.source,
                    "result_count": len(outcome.results),
                    "domains": sorted(_result_domains(outcome.results)),
                    **metrics,
                },
                "issues": issues,
                "diagnosis": _case_diagnosis(
                    teacher_documents=teacher_documents,
                    results=outcome.results,
                    metrics=metrics,
                ),
            }
            if include_results:
                case["teacher"]["results"] = [
                    _document_snapshot(document, rank=rank, snippet_chars=snippet_chars)
                    for rank, document in enumerate(teacher_documents, start=1)
                ]
                case["finrecall"]["results"] = [
                    _result_snapshot(result, rank=rank, snippet_chars=snippet_chars)
                    for rank, result in enumerate(outcome.results, start=1)
                ]
            cases.append(case)
            _add_case_to_summary(summary, case)
    finally:
        client.trace_teacher_replay = original_replay

    summary["portal_result_rate"] = _safe_rate(
        summary["portal_result_count"],
        summary["finrecall_result_count"],
    )
    summary["thin_result_rate"] = _safe_rate(
        summary["thin_result_count"],
        summary["finrecall_result_count"],
    )
    summary["empty_query_rate"] = _safe_rate(
        summary["finrecall_empty_query_count"],
        summary["query_count"],
    )
    return {"summary": summary, "cases": cases}


def _quality_metrics(
    results: list[SearchResult],
    teacher_documents: list[DocumentRecord],
) -> dict[str, Any]:
    urls = [canonicalize_url(result.url) for result in results]
    domains = [_result_domain(result) for result in results]
    duplication_domains = [
        _result_domain(result)
        for result in results
        if not _is_structured_native_result(result)
    ]
    teacher_domains = _document_domains(teacher_documents)
    result_domains = set(domains)
    overlap = result_domains & teacher_domains
    return {
        "portal_result_count": sum(1 for result in results if _is_portal_result(result)),
        "thin_result_count": sum(1 for result in results if _is_thin_result(result)),
        "duplicate_url_count": _duplicate_count(urls),
        "duplicate_domain_count": _duplicate_count(duplication_domains),
        "teacher_domain_overlap_count": len(overlap),
        "teacher_domain_overlap_rate": _safe_rate(len(overlap), len(teacher_domains)),
        "top_native_sources": [
            str(result.raw.get("native_source") or result.source or "")
            for result in results[:5]
        ],
    }


def _case_issues(metrics: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if metrics["portal_result_count"]:
        issues.append("portal_results")
    if metrics["thin_result_count"]:
        issues.append("thin_results")
    if metrics["duplicate_url_count"]:
        issues.append("duplicate_urls")
    if metrics["duplicate_domain_count"]:
        issues.append("duplicate_domains")
    return issues


def _case_diagnosis(
    *,
    teacher_documents: list[DocumentRecord],
    results: list[SearchResult],
    metrics: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    teacher_domains = _document_domains(teacher_documents)
    result_domains = _result_domains(results)
    native_sources = {str(result.raw.get("native_source") or "") for result in results}
    if teacher_documents and not results:
        notes.append(
            "FinRecall returned no result while the teacher has article/data results; "
            "this is a source-coverage gap, not a ranking issue."
        )
    if metrics["portal_result_count"]:
        notes.append("FinRecall returned portal/search or quote entry pages instead of content-bearing results.")
    if metrics["thin_result_count"]:
        notes.append("FinRecall snippets are thin/template-like compared with the teacher snippets.")
    if metrics["duplicate_domain_count"]:
        notes.append("FinRecall repeats domains across results, usually one generated entry per entity.")
    if native_sources & {"sse_notice", "szse_notice", "cninfo_notice"} and not (teacher_domains & result_domains):
        notes.append(
            "FinRecall found official disclosure entry pages, but the teacher found specific articles, PDFs, "
            "or dated result pages."
        )
    if native_sources & {"investing_us_indices", "yahoo_us_market"}:
        notes.append("FinRecall returned generic US market pages instead of query-specific ETF/news/data pages.")
    return notes


def _document_snapshot(
    document: DocumentRecord,
    *,
    rank: int,
    snippet_chars: int,
) -> dict[str, Any]:
    url = canonicalize_url(document.canonical_url or document.url)
    return {
        "rank": rank,
        "title": document.title,
        "domain": document.source_domain or source_domain(url),
        "url": url,
        "snippet": _snippet(document.content, snippet_chars=snippet_chars),
    }


def _result_snapshot(
    result: SearchResult,
    *,
    rank: int,
    snippet_chars: int,
) -> dict[str, Any]:
    url = canonicalize_url(result.url)
    return {
        "rank": rank,
        "title": result.title,
        "domain": source_domain(url),
        "url": url,
        "native_source": result.raw.get("native_source"),
        "snippet": _snippet(result.content, snippet_chars=snippet_chars),
    }


def _add_case_to_summary(summary: dict[str, Any], case: dict[str, Any]) -> None:
    summary["query_count"] += 1
    teacher = case["teacher"]
    finrecall = case["finrecall"]
    if finrecall["result_count"] == 0:
        summary["finrecall_empty_query_count"] += 1
    summary["teacher_result_count"] += int(teacher["result_count"])
    summary["finrecall_result_count"] += int(finrecall["result_count"])
    for key in (
        "portal_result_count",
        "thin_result_count",
        "duplicate_url_count",
        "duplicate_domain_count",
        "teacher_domain_overlap_count",
    ):
        summary[key] += int(finrecall[key])


def _is_portal_result(result: SearchResult) -> bool:
    if _is_structured_native_result(result):
        return False
    native_source = str(result.raw.get("native_source") or "")
    if native_source in LOW_VALUE_NATIVE_SOURCES:
        return True
    url = canonicalize_url(result.url).lower()
    title = result.title
    if source_domain(url) in PORTAL_DOMAINS:
        return True
    if any(marker in url for marker in PORTAL_URL_MARKERS):
        return True
    return any(marker in title for marker in PORTAL_TITLE_MARKERS) and len(result.content) < 80


def _is_structured_native_result(result: SearchResult) -> bool:
    return str(result.raw.get("native_source") or "") in STRUCTURED_NATIVE_SOURCES


def _is_thin_result(result: SearchResult) -> bool:
    content = result.content.strip()
    return len(content) < 40 or len(result.title.strip()) + len(content) < 80


def _result_domain(result: SearchResult) -> str:
    return source_domain(canonicalize_url(result.url))


def _result_domains(results: list[SearchResult]) -> set[str]:
    return {_result_domain(result) for result in results if result.url}


def _document_domains(documents: list[DocumentRecord]) -> set[str]:
    domains: set[str] = set()
    for document in documents:
        url = document.canonical_url or document.url
        if url:
            domains.add(document.source_domain or source_domain(canonicalize_url(url)))
    return domains


def _duplicate_count(values: list[str]) -> int:
    counts = Counter(value for value in values if value)
    return sum(count - 1 for count in counts.values() if count > 1)


def _snippet(text: str, *, snippet_chars: int) -> str:
    cleaned = " ".join(text.split())
    return cleaned[: max(0, snippet_chars)]


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
