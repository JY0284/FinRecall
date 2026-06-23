from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from finrecall.keyless_search import KeylessSearchHarvester
from finrecall.models import ProviderSearchItem
from finrecall.native_finance import NativeFinanceProvider
from finrecall.utils import canonicalize_url, source_domain


CONTENT_QUERY_TERMS = (
    "新闻",
    "最新消息",
    "财报",
    "收入构成",
    "业绩",
    "公告",
    "研报",
    "政策",
    "市场震荡",
    "暴跌",
    "大跌",
    "下跌",
    "回调",
    "为什么",
    "解读",
    "发布",
)

DATA_FIRST_TERMS = (
    "资金流向",
    "主力资金",
    "单位净值",
    "最新净值",
    "净值",
    "收盘价",
    "涨跌幅",
    "换手率",
    "最新价",
)


class HybridSearchProvider:
    source_name = "hybrid_keyless"

    def __init__(
        self,
        *,
        native_provider: Any | None = None,
        keyless_provider: Any | None = None,
    ) -> None:
        self.native_provider = native_provider or NativeFinanceProvider()
        self.keyless_provider = keyless_provider or KeylessSearchHarvester()

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        max_results = min(max(1, int(max_results)), 10)
        native_items = self.native_provider.search(
            query,
            max_results=max(max_results, 3),
            topic=topic,
            time_window=time_window,
        )
        keyless_items: list[ProviderSearchItem] = []
        if _should_run_keyless(query):
            try:
                keyless_items = self.keyless_provider.search(
                    query,
                    max_results=max_results,
                    topic=topic,
                    time_window=time_window,
                )
                keyless_items = _filter_temporal_mismatches(keyless_items, query)
                keyless_items = _filter_broad_market_mismatches(keyless_items, query)
            except Exception:  # noqa: BLE001
                keyless_items = []

        native_pool = _filter_native_fillers(native_items) if keyless_items else native_items
        combined = _dedupe_items([*keyless_items, *native_pool])
        ranked = sorted(combined, key=lambda item: _hybrid_rank(item, query), reverse=True)
        selected = _select_diverse_domains(ranked, max_results=max_results)
        return [_mark_hybrid(item) for item in selected]


def _should_run_keyless(query: str) -> bool:
    normalized = query.lower()
    if any(term.lower() in normalized for term in DATA_FIRST_TERMS):
        return False
    return any(term.lower() in normalized for term in CONTENT_QUERY_TERMS)


def _dedupe_items(items: list[ProviderSearchItem]) -> list[ProviderSearchItem]:
    seen: set[str] = set()
    seen_titles: set[str] = set()
    deduped: list[ProviderSearchItem] = []
    for item in items:
        canonical = canonicalize_url(item.url)
        if canonical in seen:
            continue
        title_signature = _title_signature(item.title)
        if title_signature and title_signature in seen_titles:
            continue
        seen.add(canonical)
        if title_signature:
            seen_titles.add(title_signature)
        deduped.append(item)
    return deduped


def _filter_native_fillers(items: list[ProviderSearchItem]) -> list[ProviderSearchItem]:
    return [item for item in items if not _is_low_value_native_filler(item)]


def _filter_temporal_mismatches(
    items: list[ProviderSearchItem],
    query: str,
) -> list[ProviderSearchItem]:
    if not _query_requests_full_year_report(query):
        return items
    filtered = [
        item
        for item in items
        if not _looks_like_quarterly_report(item.title)
        and not _looks_like_report_preview(item.title)
    ]
    return filtered or items


def _filter_broad_market_mismatches(
    items: list[ProviderSearchItem],
    query: str,
) -> list[ProviderSearchItem]:
    if not _query_requests_broad_market_context(query):
        return items
    filtered = [item for item in items if not _looks_like_single_stock_housekeeping(item.title)]
    return filtered or items


def _query_requests_full_year_report(query: str) -> bool:
    normalized = query.lower()
    if not re.search(r"20\d{2}", query):
        return False
    if any(term in normalized for term in ("q1", "q2", "q3", "q4")):
        return False
    if any(term in query for term in ("一季度", "二季度", "三季度", "四季度", "一季报", "季报")):
        return False
    return any(term in query for term in ("财报", "年报", "业绩", "收入构成", "营收"))


def _looks_like_quarterly_report(title: str) -> bool:
    normalized = title.lower()
    if any(term in normalized for term in ("q1", "q2", "q3", "q4")):
        return True
    return any(term in title for term in ("一季度", "二季度", "三季度", "四季度", "一季报", "季报"))


def _looks_like_report_preview(title: str) -> bool:
    return any(term in title for term in ("即将公布", "将公布", "即将披露", "将披露", "预告", "前瞻"))


def _query_requests_broad_market_context(query: str) -> bool:
    if "A股" not in query and "a股" not in query.lower():
        return False
    return any(term in query for term in ("市场", "震荡", "资金面", "走势", "展望", "大盘", "原因"))


def _looks_like_single_stock_housekeeping(title: str) -> bool:
    return any(
        term in title
        for term in (
            "股东户数",
            "户均持股",
            "分红派息实施公告",
            "权益分派实施公告",
            "减持计划",
            "限售股",
        )
    )


def _is_low_value_native_filler(item: ProviderSearchItem) -> bool:
    native_source = str(item.raw.get("native_source") or "")
    if native_source.endswith("_market"):
        return True
    return len(item.content.strip()) < 80


def _title_signature(title: str) -> str:
    normalized = " ".join(title.split()).lower()
    if normalized.startswith(("http://", "https://")):
        return ""
    for separator in (" _ ", " __ ", "__", " | ", "|", "｜", " - ", "_"):
        if separator in normalized:
            normalized = normalized.split(separator, 1)[0]
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    if len(normalized) < 12:
        return ""
    return normalized


def _select_diverse_domains(
    ranked: list[ProviderSearchItem],
    *,
    max_results: int,
) -> list[ProviderSearchItem]:
    selected: list[ProviderSearchItem] = []
    seen_domains: set[str] = set()
    for item in ranked:
        domain = source_domain(item.url)
        if domain in seen_domains:
            continue
        selected.append(item)
        seen_domains.add(domain)
        if len(selected) >= max_results:
            return selected
    return selected


def _hybrid_rank(item: ProviderSearchItem, query: str) -> float:
    raw_provider = str(item.raw.get("provider") or "")
    score = 0.0
    if raw_provider == KeylessSearchHarvester.source_name or item.raw.get("source_engine"):
        score += 5.0
    if len(item.content) >= 120:
        score += 2.0
    elif len(item.content) >= 40:
        score += 0.8
    if item.published_at:
        score += 1.0 + item.date_confidence
    if item.url.lower().endswith(".pdf"):
        score += 1.2
    score += _keyword_coverage_score(item, query)
    native_source = str(item.raw.get("native_source") or "")
    if native_source.endswith("_market") or native_source in {"futu_hk_market", "yahoo_hk_market"}:
        score -= 1.0
    return score


def _keyword_coverage_score(item: ProviderSearchItem, query: str) -> float:
    tokens = [token for token in query.replace("，", " ").replace(",", " ").split() if len(token) >= 2]
    if not tokens:
        return 0.0
    blob = f"{item.title}\n{item.content}".lower()
    matched = sum(1 for token in tokens if token.lower() in blob)
    return min(2.0, 2.0 * matched / len(tokens))


def _mark_hybrid(item: ProviderSearchItem) -> ProviderSearchItem:
    raw = dict(item.raw)
    upstream = str(raw.get("provider") or "unknown")
    raw["provider"] = HybridSearchProvider.source_name
    raw["hybrid_sources"] = [upstream]
    raw.setdefault("rank_reason", "hybrid_ranked_result")
    return replace(item, raw=raw)
