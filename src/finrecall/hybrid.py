from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from finrecall.content_enrichment import ContentEnricher
from finrecall.keyless_search import KeylessSearchHarvester
from finrecall.llm_rerank import LLMReranker
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
    "减持",
    "限售",
    "解禁",
    "限售解禁",
    "解除限售",
    "上市流通",
    "股票上市",
    "股票上市公告",
    "限制性股票",
    "归属结果",
    "询价转让",
    "转让计划",
    "权益变动",
    "持股5%以上",
    "风险提示",
    "异动",
    "异常波动",
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

STOCK_DISCLOSURE_EVENT_TERMS = (
    "减持",
    "限售",
    "解禁",
    "限售解禁",
    "解除限售",
    "上市流通",
    "股票上市",
    "股票上市公告",
    "限制性股票",
    "归属结果",
    "询价转让",
    "转让计划",
    "股东询价转让",
    "权益变动",
    "持股5%以上",
    "风险提示",
    "异动",
    "异常波动",
)


class HybridSearchProvider:
    source_name = "hybrid_keyless"

    def __init__(
        self,
        *,
        native_provider: Any | None = None,
        keyless_provider: Any | None = None,
        reranker: Any | None = None,
        content_enricher: Any | None = None,
    ) -> None:
        self.native_provider = native_provider or NativeFinanceProvider()
        self.keyless_provider = keyless_provider or KeylessSearchHarvester()
        self.reranker = reranker if reranker is not None else LLMReranker.from_env()
        self.content_enricher = (
            content_enricher if content_enricher is not None else ContentEnricher.from_env()
        )

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
        run_keyless = _should_run_keyless(query)
        if run_keyless and _query_requests_stock_disclosure_event(query) and _has_native_disclosure_body(native_items):
            run_keyless = False
        if run_keyless:
            native_items = _filter_content_intent_mismatches(native_items, query)

        keyless_items: list[ProviderSearchItem] = []
        if run_keyless:
            try:
                keyless_items = self.keyless_provider.search(
                    query,
                    max_results=max_results,
                    topic=topic,
                    time_window=time_window,
                )
                keyless_items = _filter_content_intent_mismatches(keyless_items, query)
            except Exception:  # noqa: BLE001
                keyless_items = []

        native_pool = _filter_native_fillers(native_items) if keyless_items else native_items
        combined = _dedupe_items([*keyless_items, *native_pool])
        ranked = sorted(combined, key=lambda item: _hybrid_rank(item, query), reverse=True)
        if run_keyless and self.reranker is not None:
            try:
                ranked = self.reranker.rerank(query, ranked, max_results=max_results)
            except Exception:  # noqa: BLE001
                pass
        selected = _select_diverse_domains(ranked, max_results=max_results)
        if run_keyless and self.content_enricher is not None:
            try:
                selected = self.content_enricher.enrich(query, selected)
            except Exception:  # noqa: BLE001
                pass
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


def _has_native_disclosure_body(items: list[ProviderSearchItem]) -> bool:
    return any(
        str(item.raw.get("native_source") or "") in {"sina_notice_body"}
        and len(item.content.strip()) >= 300
        for item in items
    )


def _filter_content_intent_mismatches(
    items: list[ProviderSearchItem],
    query: str,
) -> list[ProviderSearchItem]:
    filtered = _filter_temporal_mismatches(items, query)
    filtered = _filter_relevance_mismatches(filtered, query)
    return _filter_broad_market_mismatches(filtered, query)


def _filter_temporal_mismatches(
    items: list[ProviderSearchItem],
    query: str,
) -> list[ProviderSearchItem]:
    if not _query_requests_full_year_report(query):
        return items
    filtered = [
        item
        for item in items
        if not _looks_like_quarterly_mismatch(item)
        and not _looks_like_report_preview(item.title)
        and not _has_conflicting_report_year(item, query)
    ]
    return filtered


def _looks_like_quarterly_mismatch(item: ProviderSearchItem) -> bool:
    blob = _report_temporal_blob(item)
    if not _looks_like_quarterly_report(blob):
        return False
    return not _has_full_year_evidence(blob)


def _filter_relevance_mismatches(
    items: list[ProviderSearchItem],
    query: str,
) -> list[ProviderSearchItem]:
    filtered = items
    if _query_requests_stock_disclosure_event(query):
        filtered = [
            item for item in filtered if _item_mentions_query_stock_identity(item, query)
        ]
        if not filtered:
            return filtered

    if _query_requests_full_year_report(query):
        strict_report_items = [
            item
            for item in filtered
            if _item_mentions_query_company(item, query)
            and _has_report_evidence(item)
        ]
        if strict_report_items:
            filtered = strict_report_items

    subtopic_terms = _query_subtopic_terms(query)
    if subtopic_terms:
        subtopic_items = [
            item for item in filtered if _item_mentions_any(item, subtopic_terms)
        ]
        if subtopic_items:
            filtered = subtopic_items

    if _query_requests_policy_context(query):
        policy_items = [item for item in filtered if _has_primary_policy_evidence(item)]
        if policy_items:
            filtered = policy_items

    return filtered


def _filter_broad_market_mismatches(
    items: list[ProviderSearchItem],
    query: str,
) -> list[ProviderSearchItem]:
    if not _query_requests_broad_market_context(query):
        return items
    filtered = [item for item in items if not _looks_like_single_stock_housekeeping(item.title)]
    if not filtered:
        return items

    if any(_has_broad_market_context_evidence(item) for item in filtered):
        non_housekeeping = [
            item for item in filtered if not _looks_like_etf_housekeeping(item.title)
        ]
        if non_housekeeping:
            filtered = non_housekeeping
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
    return any(
        term in title
        for term in (
            "一季度",
            "二季度",
            "三季度",
            "四季度",
            "第一季度",
            "第二季度",
            "第三季度",
            "第四季度",
            "一季报",
            "季报",
        )
    )


def _looks_like_report_preview(title: str) -> bool:
    return any(term in title for term in ("即将公布", "将公布", "即将披露", "将披露", "预告", "前瞻"))


def _report_temporal_blob(item: ProviderSearchItem) -> str:
    return f"{item.title}\n{item.content[:260]}"


def _has_full_year_evidence(text: str) -> bool:
    return any(term in text for term in ("全年", "年度", "财年", "年报", "全年财报", "全年业绩"))


def _has_conflicting_report_year(item: ProviderSearchItem, query: str) -> bool:
    requested_years = set(re.findall(r"20\d{2}", query))
    if not requested_years:
        return False
    observed_years = set(re.findall(r"20\d{2}", _report_temporal_blob(item)))
    return bool(observed_years - requested_years) and not bool(observed_years & requested_years)


def _has_report_evidence(item: ProviderSearchItem) -> bool:
    return _item_mentions_any(
        item,
        ("财报", "年报", "业绩", "营收", "收入", "全年", "财年", "收入构成"),
    )


def _query_requests_broad_market_context(query: str) -> bool:
    if "A股" not in query and "a股" not in query.lower():
        return False
    return any(term in query for term in ("市场", "震荡", "资金面", "走势", "展望", "大盘", "原因"))


def _query_requests_policy_context(query: str) -> bool:
    return any(
        term in query
        for term in ("政策", "管制", "监管", "规则", "措施", "国产替代", "产业链")
    )


def _query_requests_stock_disclosure_event(query: str) -> bool:
    if not any(term in query for term in STOCK_DISCLOSURE_EVENT_TERMS):
        return False
    return bool(_query_stock_identity_terms(query))


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


def _looks_like_etf_housekeeping(title: str) -> bool:
    upper_title = title.upper()
    if "ETF" in upper_title and any(term in title for term in ("净值", "资金流入", "资金流出", "基金")):
        return True
    return bool(re.search(r"\b\d{6}\b", title)) and any(
        term in title for term in ("ETF", "基金", "净值")
    )


def _has_primary_policy_evidence(item: ProviderSearchItem) -> bool:
    return _item_mentions_any(
        item,
        (
            "政策更新",
            "政策解读",
            "政策分歧",
            "管制",
            "监管",
            "限制",
            "制裁",
            "法规",
            "办法",
            "意见",
            "通知",
            "指引",
            "方案",
            "合规",
            "解读",
        ),
    )


def _has_broad_market_context_evidence(item: ProviderSearchItem) -> bool:
    return _item_mentions_any(
        item,
        (
            "展望",
            "策略",
            "复盘",
            "慢牛",
            "资金面",
            "流动性",
            "盈利修复",
            "市场震荡",
            "大盘",
            "原因",
            "券商",
        ),
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
    score += _intent_relevance_score(item, query)
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


def _intent_relevance_score(item: ProviderSearchItem, query: str) -> float:
    score = 0.0
    if _query_requests_full_year_report(query):
        if _item_mentions_query_company(item, query):
            score += 1.5
        if _item_mentions_query_year(item, query):
            score += 0.8
        if _has_report_evidence(item):
            score += 1.2
        if _looks_like_quarterly_report(item.title) or _looks_like_report_preview(item.title):
            score -= 1.5

    subtopic_terms = _query_subtopic_terms(query)
    if subtopic_terms and _item_mentions_any(item, subtopic_terms):
        score += min(1.0, 0.4 * len(subtopic_terms))

    if _query_requests_policy_context(query):
        if _has_primary_policy_evidence(item):
            score += 1.5
        elif _looks_like_market_hype(item):
            score -= 1.0

    if _query_requests_broad_market_context(query):
        if _has_broad_market_context_evidence(item):
            score += 1.2
        if _looks_like_single_stock_housekeeping(item.title) or _looks_like_etf_housekeeping(item.title):
            score -= 1.5

    return score


def _item_mentions_query_company(item: ProviderSearchItem, query: str) -> bool:
    company_terms = _query_company_terms(query)
    return not company_terms or _item_mentions_any(item, company_terms)


def _item_mentions_query_year(item: ProviderSearchItem, query: str) -> bool:
    years = re.findall(r"20\d{2}", query)
    return not years or _item_mentions_any(item, tuple(years))


def _item_mentions_query_stock_identity(item: ProviderSearchItem, query: str) -> bool:
    identity_terms = _query_stock_identity_terms(query)
    return not identity_terms or _item_mentions_any(item, identity_terms)


def _query_stock_identity_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    for code in re.findall(r"(?<!\d)\d{6}(?!\d)", query):
        terms.append(code)
    terms.extend(_query_company_terms(query))
    return tuple(dict.fromkeys(term for term in terms if len(term) >= 2))


def _query_company_terms(query: str) -> tuple[str, ...]:
    tokens = [token for token in re.split(r"[\s,，、;；|/]+", query) if token]
    if not tokens:
        return ()
    company = tokens[0]
    if company.lower() in {"a股", "ai"} or re.search(r"20\d{2}", company):
        return ()
    aliases = [company, _company_alias(company)]
    if company == "阿里巴巴":
        aliases.append("阿里")
    return tuple(dict.fromkeys(alias for alias in aliases if len(alias) >= 2))


def _query_subtopic_terms(query: str) -> tuple[str, ...]:
    excluded = set(_query_company_terms(query))
    excluded.update(
        {
            "财报",
            "年报",
            "业绩",
            "收入",
            "收入构成",
            "营收",
            "最新",
            "新闻",
            "消息",
            "政策",
            "影响",
            "A股",
            "a股",
            "市场",
            "市场震荡",
            "原因",
            "资金面",
            "走势",
            "展望",
        }
    )
    terms: list[str] = []
    for token in re.split(r"[\s,，、;；|/]+", query):
        cleaned = token.strip()
        if len(cleaned) < 2:
            continue
        if cleaned in excluded or cleaned.lower() in excluded:
            continue
        if re.fullmatch(r"20\d{2}年?", cleaned):
            continue
        if any(term in cleaned for term in ("财报", "年报", "业绩", "收入", "营收")):
            continue
        terms.append(cleaned)
    return tuple(dict.fromkeys(terms))


def _company_alias(company: str) -> str:
    alias = company
    for suffix in ("集团", "股份", "控股", "有限", "公司"):
        if alias.endswith(suffix) and len(alias) > len(suffix) + 1:
            alias = alias[: -len(suffix)]
    return alias or company


def _item_mentions_any(item: ProviderSearchItem, terms: tuple[str, ...]) -> bool:
    blob = _item_blob(item)
    return any(term.lower() in blob for term in terms)


def _item_blob(item: ProviderSearchItem) -> str:
    return f"{item.title}\n{item.content}".lower()


def _looks_like_market_hype(item: ProviderSearchItem) -> bool:
    return _item_mentions_any(item, ("概念股", "涨停", "成交放量", "板块活跃", "资金追捧"))


def _mark_hybrid(item: ProviderSearchItem) -> ProviderSearchItem:
    raw = dict(item.raw)
    upstream = str(raw.get("provider") or "unknown")
    raw["provider"] = HybridSearchProvider.source_name
    raw["hybrid_sources"] = [upstream]
    raw.setdefault("rank_reason", "hybrid_ranked_result")
    return replace(item, raw=raw)
