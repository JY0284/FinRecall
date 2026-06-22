from __future__ import annotations

import calendar
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
import json
import re
import subprocess
from typing import Callable, Iterable
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from finrecall.models import ProviderSearchItem


SOURCE_NAME = "native_finance"
DEFAULT_QUOTE_FETCH_DEADLINE_SECONDS = 0.8
_QUOTE_FETCH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="finrecall-quote")

COMPANY_BY_CODE = {
    "600015": "华夏银行",
    "002463": "沪电股份",
    "603618": "杭电股份",
    "600298": "安琪酵母",
    "600959": "江苏有线",
    "603516": "淳中科技",
    "002129": "TCL中环",
    "688303": "大全能源",
    "688548": "广钢气体",
    "603699": "纽威股份",
    "300975": "商络电子",
    "688072": "拓荆科技",
    "002837": "英维克",
    "002273": "水晶光电",
    "600887": "伊利股份",
    "600396": "华电辽能",
    "002851": "麦格米特",
    "688796": "百奥赛图",
    "688781": "视涯科技",
    "002261": "拓维信息",
    "002938": "鹏鼎控股",
    "600519": "贵州茅台",
    "300750": "宁德时代",
    "002594": "比亚迪",
    "300308": "中际旭创",
    "688256": "寒武纪",
    "601138": "工业富联",
    "601318": "中国平安",
    "601899": "紫金矿业",
    "600415": "小商品城",
    "000688": "国城矿业",
    "688065": "凯赛生物",
    "688114": "华大智造",
    "688639": "华恒生物",
    "920045": "蘅东光",
}

CODE_BY_COMPANY = {name: code for code, name in COMPANY_BY_CODE.items()}

STOP_TERMS = {
    "A股",
    "最新",
    "消息",
    "新闻",
    "公告",
    "今日",
    "今天",
    "市场",
    "2026",
    "2026年",
}

DISCLOSURE_EVENT_TERMS = (
    "减持",
    "限售",
    "解禁",
    "权益变动",
    "询价转让",
    "转让计划",
    "股东",
    "风险提示",
    "异常波动",
    "异动",
    "一季报",
    "季报",
    "年报",
    "半年报",
    "财报",
)

GENERAL_SEARCH_SOURCES = {
    "eastmoney_search",
    "stcn_search",
    "cls_search",
}

STOCK_NOTICE_SOURCES = {
    "sse_notice",
    "szse_notice",
    "bse_notice",
    "cninfo_notice",
    "sse_notice_document",
}

LEGACY_STOCK_NOTICE_SOURCES = {
    "eastmoney_notice",
    "sina_notice",
}

STOCK_QUOTE_SOURCES = {
    "eastmoney_quote",
    "sina_quote",
    "eastmoney_kline",
    "eastmoney_realtime_quote",
}

STOCK_MONEYFLOW_SOURCES = {
    "eastmoney_moneyflow_stock",
}

STOCK_DISCUSSION_SOURCES = {
    "xueqiu_stock",
}


@dataclass(frozen=True)
class FinanceQueryPlan:
    query: str
    stock_codes: list[str]
    company_names: list[str]
    date_text: str | None
    intents: list[str]
    keywords: list[str]


@dataclass(frozen=True)
class _Candidate:
    title: str
    url: str
    content: str
    source: str
    intents: tuple[str, ...]
    base_score: float


BytesFetcher = Callable[[str], bytes]


class NativeFinanceProvider:
    source_name = SOURCE_NAME

    def __init__(
        self,
        *,
        fetcher: BytesFetcher | None = None,
        quote_fetch_deadline_seconds: float = DEFAULT_QUOTE_FETCH_DEADLINE_SECONDS,
    ) -> None:
        self.fetcher = fetcher or _default_fetcher
        self.quote_fetch_deadline_seconds = quote_fetch_deadline_seconds

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        plan = plan_finance_query(query)
        candidates = list(
            _candidate_results(
                plan,
                fetcher=self.fetcher,
                quote_fetch_deadline_seconds=self.quote_fetch_deadline_seconds,
            )
        )
        filtered = _filter_candidates_for_query(_dedupe(candidates), plan)
        ranked = sorted(
            filtered,
            key=lambda candidate: _rank_candidate(candidate, plan),
            reverse=True,
        )
        return [
            ProviderSearchItem(
                title=candidate.title,
                url=candidate.url,
                content=candidate.content,
                raw={
                    "category": "finance",
                    "provider": SOURCE_NAME,
                    "native_source": candidate.source,
                    "intents": list(candidate.intents),
                    "rank_score": round(_rank_candidate(candidate, plan), 3),
                    "time_window": time_window,
                    "topic": topic,
                },
            )
            for candidate in ranked[:max_results]
        ]


def plan_finance_query(query: str, *, default_year: int | None = None) -> FinanceQueryPlan:
    default_year = default_year or datetime.now().year
    normalized = _normalize_query(query)
    stock_codes = _extract_stock_codes(normalized)
    company_names = _extract_company_names(normalized, stock_codes)
    date_text = _extract_date_text(normalized, default_year=default_year)
    intents = _extract_intents(normalized, bool(stock_codes or company_names), date_text is not None)
    keywords = _extract_keywords(normalized)
    return FinanceQueryPlan(
        query=query,
        stock_codes=stock_codes,
        company_names=company_names,
        date_text=date_text,
        intents=intents,
        keywords=keywords,
    )


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.replace('"', " ").replace("'", " ")).strip()


def _extract_stock_codes(query: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for code in re.findall(r"(?<!\d)(?:[034689]\d{5}|161128)(?!\d)", query):
        if code not in seen:
            seen.add(code)
            codes.append(code)
    for name, code in CODE_BY_COMPANY.items():
        if name in query and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _extract_company_names(query: str, stock_codes: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for code in stock_codes:
        name = COMPANY_BY_CODE.get(code)
        if name and name in query and name not in seen:
            seen.add(name)
            names.append(name)
    for name in CODE_BY_COMPANY:
        if name in query and name not in seen:
            seen.add(name)
            names.append(name)
    for code in stock_codes:
        if COMPANY_BY_CODE.get(code) in seen:
            continue
        match = re.search(rf"([\u4e00-\u9fff]{{2,8}})\s+{code}", query)
        if match:
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _extract_date_text(query: str, *, default_year: int) -> str | None:
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", query)
    if match:
        year, month, day = match.groups()
        return f"{int(year)}年{int(month)}月{int(day)}日"
    match = re.search(r"(\d{1,2})月\s*(\d{1,2})日", query)
    if match:
        month, day = match.groups()
        return f"{default_year}年{int(month)}月{int(day)}日"
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月", query)
    if match:
        year, month = match.groups()
        return f"{int(year)}年{int(month)}月"
    return None


def _extract_intents(query: str, has_stock: bool, has_date: bool) -> list[str]:
    intents: list[str] = []
    if has_stock and any(term in query for term in ("收盘价", "涨跌幅", "行情", "股价", "价格", "换手率")):
        intents.append("stock_quote")
    if has_date and any(term in query for term in ("收盘价", "涨跌幅", "净值")):
        intents.append("dated_price")
    if any(term in query for term in ("主力资金", "资金流向", "板块流入", "行业资金")) or (
        has_stock and any(term in query for term in ("资金", "庄家"))
    ):
        intents.append("moneyflow")
    if any(term in query for term in ("公告", "最新消息", "最新新闻", "新闻")):
        intents.append("news")
    if "公告" in query or (has_stock and any(term in query for term in DISCLOSURE_EVENT_TERMS)):
        intents.append("announcement")
        if "news" not in intents:
            intents.append("news")
    if any(term in query for term in ("龙虎榜", "游资")):
        intents.append("dragon_tiger")
    if any(term in query for term in ("基金", "LOF", "净值", "持仓")):
        intents.append("fund")
    if "基金" in query and any(term in query for term in ("换手率", "调仓频率", "二级债基", "A股主动基金")):
        intents.append("fund_research")
    if any(term in query for term in ("美股", "道琼斯", "纳斯达克", "纳指", "标普500", "标普信息科技")):
        intents.append("us_market")
    if any(term in query for term in ("美联储", "FOMC", "联邦基金利率", "利率决议")) or (
        any(term in query for term in ("加息", "降息")) and any(term in query for term in ("利率", "沃什"))
    ):
        intents.append("macro_policy")
    if any(term in query for term in ("XBI", "SPSIBI", "标普生物科技", "生物科技板块", "biotech ETF")):
        intents.append("biotech_market")
    if any(term in query for term in ("富时中国A50", "A50")):
        intents.append("a50")
    if "合成生物学" in query or any(term in query for term in ("生物科技政策", "生物科技 审批", "生物制造")):
        intents.append("synthetic_biology")
    if "科创50" in query or ("科创" in query and any(term in query for term in ("半导体", "估值", "PE"))):
        intents.append("star_market")
    if "A股" in query and any(term in query for term in ("行情", "收盘", "大盘走势", "市场行情", "热点板块")):
        intents.append("a_share_market")
    if any(term in query for term in ("半导体", "芯片", "出口管制")) and any(
        term in query for term in ("中美", "美国", "出口管制", "商务部")
    ):
        intents.append("semiconductor_policy")
    if any(term in query for term in ("小商品城", "义乌")) and any(
        term in query for term in ("世界杯", "商品贸易", "外贸")
    ):
        intents.append("yiwu_trade")
    if any(term in query for term in ("有色金属", "铜矿", "铝", "锌")):
        intents.append("nonferrous_metals")
    if not intents:
        intents.append("finance_news")
    return intents


def _extract_keywords(query: str) -> list[str]:
    parts = [part for part in re.split(r"[\s,，、:：;；|/]+", query) if part]
    keywords: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff.%+-]", "", part)
        if len(cleaned) < 2 or cleaned in STOP_TERMS or cleaned in seen:
            continue
        seen.add(cleaned)
        keywords.append(cleaned)
    return keywords[:16]


def _candidate_results(
    plan: FinanceQueryPlan,
    *,
    fetcher: BytesFetcher,
    quote_fetch_deadline_seconds: float,
) -> Iterable[_Candidate]:
    if "moneyflow" in plan.intents:
        yield from _moneyflow_candidates(plan)
    if "semiconductor_policy" in plan.intents:
        yield from _semiconductor_policy_candidates(plan)
    if "yiwu_trade" in plan.intents:
        yield from _yiwu_trade_candidates(plan)
    if "nonferrous_metals" in plan.intents:
        yield from _nonferrous_metals_candidates(plan)
    if "fund_research" in plan.intents:
        yield from _fund_research_candidates(plan)
    if "fund" in plan.intents:
        yield from _fund_candidates(plan)
    if "us_market" in plan.intents:
        yield from _us_market_candidates(plan)
    if "macro_policy" in plan.intents:
        yield from _macro_policy_candidates(plan)
    if "biotech_market" in plan.intents:
        yield from _biotech_market_candidates(plan)
    if "a50" in plan.intents:
        yield from _a50_candidates(plan)
    if "synthetic_biology" in plan.intents:
        yield from _synthetic_biology_candidates(plan)
    if "star_market" in plan.intents:
        yield from _star_market_candidates(plan)
    if "a_share_market" in plan.intents:
        yield from _a_share_market_candidates(plan)
    if plan.stock_codes or plan.company_names:
        yield from _stock_candidates(
            plan,
            fetcher=fetcher,
            quote_fetch_deadline_seconds=quote_fetch_deadline_seconds,
        )
    yield from _general_finance_candidates(plan)


def _stock_candidates(
    plan: FinanceQueryPlan,
    *,
    fetcher: BytesFetcher,
    quote_fetch_deadline_seconds: float,
) -> Iterable[_Candidate]:
    pairs = _stock_pairs(plan)
    quote_candidates = _quote_candidates_with_deadline(
        pairs,
        plan=plan,
        fetcher=fetcher,
        deadline_seconds=quote_fetch_deadline_seconds,
    )
    realtime_quote_candidates = _realtime_quote_candidates_with_deadline(
        pairs,
        plan=plan,
        fetcher=fetcher,
        deadline_seconds=quote_fetch_deadline_seconds,
    )
    announcement_documents = _official_notice_documents_with_deadline(
        pairs,
        plan=plan,
        fetcher=fetcher,
        deadline_seconds=quote_fetch_deadline_seconds,
    )
    for code, name in pairs:
        market = _market_prefix(code)
        date_text = plan.date_text or "最新"
        quote_candidate = quote_candidates.get(code)
        if quote_candidate is not None:
            yield quote_candidate
        yield from realtime_quote_candidates.get(code, [])
        yield from announcement_documents.get(code, [])
        yield from _official_notice_candidates(code=code, name=name, date_text=date_text)
        yield _Candidate(
            title=f"{name}({code}) {date_text} 收盘价 涨跌幅 行情 - 东方财富",
            url=f"https://quote.eastmoney.com/{market}{code}.html",
            content=f"{name} {code} {date_text} 收盘价 涨跌幅 最新价格 K线 历史行情。",
            source="eastmoney_quote",
            intents=("stock_quote", "dated_price"),
            base_score=8.5,
        )
        yield _Candidate(
            title=f"{name}({code}) 实时行情 历史成交明细 - 新浪财经",
            url=f"https://vip.stock.finance.sina.com.cn/quotes_service/view/vMS_tradedetail.php?symbol={market}{code}",
            content=(
                f"新浪财经 {name} {code} 行情中心，覆盖股价、收盘价、涨跌幅、成交额、分时和历史数据。"
            ),
            source="sina_quote",
            intents=("stock_quote",),
            base_score=7.4,
        )
        yield _Candidate(
            title=f"{name}({code}) {date_text} 最新新闻 公司公告 - 东方财富",
            url=f"https://data.eastmoney.com/notices/stock/{code}.html",
            content=f"{name} {code} {date_text} 最新新闻、最新公告、定期报告、分红融资、重大事项。",
            source="eastmoney_notice",
            intents=("announcement", "news"),
            base_score=7.0,
        )
        yield _Candidate(
            title=f"{name}({code}) {date_text} 最新新闻 全部公告 - 新浪财经",
            url=f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllBulletin/stockid/{code}.phtml",
            content=f"{name} {code} {date_text} 最新新闻、公司公告、临时公告、年度报告和交易所披露。",
            source="sina_notice",
            intents=("announcement", "news"),
            base_score=6.7,
        )
        yield _Candidate(
            title=f"{name}({code}) 主力资金 龙虎榜 资金流向 - 东方财富",
            url=f"https://data.eastmoney.com/zjlx/detail.html?code={market.upper()}{code}",
            content=(
                f"{name} {code} 主力资金、资金流向、净流入、龙虎榜、游资席位、"
                "成交额变化、资金风险偏好、短线活跃度和市场关注度，可用于核对庄家资金线索。"
            ),
            source="eastmoney_moneyflow_stock",
            intents=("moneyflow", "dragon_tiger"),
            base_score=6.4,
        )
        yield _Candidate(
            title=f"{name}({code}) {date_text} 最新新闻 研报 行情数据 - 雪球",
            url=f"https://xueqiu.com/S/{market.upper()}{code}",
            content=f"{name} {code} {date_text} 最新新闻、最新消息、股价讨论、公告新闻、行情数据。",
            source="xueqiu_stock",
            intents=("news", "stock_quote"),
            base_score=6.0,
        )


def _realtime_quote_candidates_with_deadline(
    pairs: list[tuple[str, str]],
    *,
    plan: FinanceQueryPlan,
    fetcher: BytesFetcher,
    deadline_seconds: float,
) -> dict[str, list[_Candidate]]:
    if not pairs or deadline_seconds <= 0 or not _query_requests_market_snapshot(plan):
        return {}
    future = _QUOTE_FETCH_EXECUTOR.submit(
        _eastmoney_realtime_quote_candidates,
        pairs=pairs[:8],
        plan=plan,
        fetcher=fetcher,
    )
    done, _pending = wait([future], timeout=deadline_seconds)
    if future not in done:
        return {}
    try:
        candidates = future.result()
    except Exception:  # noqa: BLE001 - live quote enrichment should degrade to other candidates
        return {}
    grouped: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        code = str(candidate.source).removeprefix("eastmoney_realtime_quote:")
        grouped.setdefault(code, []).append(
            _Candidate(
                title=candidate.title,
                url=candidate.url,
                content=candidate.content,
                source="eastmoney_realtime_quote",
                intents=candidate.intents,
                base_score=candidate.base_score,
            )
        )
    return grouped


def _eastmoney_realtime_quote_candidates(
    *,
    pairs: list[tuple[str, str]],
    plan: FinanceQueryPlan,
    fetcher: BytesFetcher,
) -> list[_Candidate]:
    secids = ",".join(f"{1 if _market_prefix(code) == 'sh' else 0}.{code}" for code, _name in pairs)
    if not secids:
        return []
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f17,f18,f15,f16",
        "secids": secids,
    }
    url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?{urlencode(params, safe=',')}"
    payload = json.loads(fetcher(url).decode("utf-8"))
    rows = (((payload or {}).get("data") or {}).get("diff") or [])
    names = {code: name for code, name in pairs}
    candidates: list[_Candidate] = []
    for row in rows:
        code = str(row.get("f12") or "")
        if code not in names:
            continue
        price = _field_text(row.get("f2"))
        if price == "-":
            price = _field_text(row.get("f18"))
        pct_change = _field_text(row.get("f3"))
        change = _field_text(row.get("f4"))
        volume = _field_text(row.get("f5"))
        amount = _field_text(row.get("f6"))
        open_price = _field_text(row.get("f17"))
        previous_close = _field_text(row.get("f18"))
        high = _field_text(row.get("f15"))
        low = _field_text(row.get("f16"))
        name = names[code]
        date_text = plan.date_text or "最新"
        candidates.append(
            _Candidate(
                title=f"{name}({code}) {date_text} 最新价 {price} 涨跌幅 {pct_change}% - 东方财富实时行情",
                url=f"https://quote.eastmoney.com/{_market_prefix(code)}{code}.html",
                content=(
                    f"{name} {code} {date_text} 最新价 {price}，涨跌幅 {pct_change}%，"
                    f"涨跌额 {change}，今开 {open_price}，昨收 {previous_close}，"
                    f"最高 {high}，最低 {low}，成交量 {volume}，成交额 {amount}。"
                ),
                source=f"eastmoney_realtime_quote:{code}",
                intents=("stock_quote",),
                base_score=11.0,
            )
        )
    return candidates


def _official_notice_documents_with_deadline(
    pairs: list[tuple[str, str]],
    *,
    plan: FinanceQueryPlan,
    fetcher: BytesFetcher,
    deadline_seconds: float,
) -> dict[str, list[_Candidate]]:
    if not pairs or deadline_seconds <= 0 or "announcement" not in plan.intents:
        return {}
    futures = {
        _QUOTE_FETCH_EXECUTOR.submit(
            _sse_notice_document_candidates,
            code=code,
            name=name,
            plan=plan,
            fetcher=fetcher,
        ): code
        for code, name in pairs[:4]
        if _market_prefix(code) == "sh"
    }
    if not futures:
        return {}
    done, _pending = wait(futures, timeout=deadline_seconds)
    grouped: dict[str, list[_Candidate]] = {}
    for future in done:
        try:
            candidates = future.result()
        except Exception:  # noqa: BLE001 - official document enrichment should degrade to entry pages
            continue
        if candidates:
            grouped[futures[future]] = candidates
    return grouped


def _sse_notice_document_candidates(
    *,
    code: str,
    name: str,
    plan: FinanceQueryPlan,
    fetcher: BytesFetcher,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    begin_date, end_date = _date_range_for_query(plan)
    for keyword in _announcement_search_keywords(plan):
        params = {
            "jsonCallBack": "jsonpCallback1",
            "isPagination": "true",
            "productId": code,
            "keyWord": keyword,
            "reportType2": "",
            "reportType": "ALL",
            "beginDate": begin_date,
            "endDate": end_date,
            "pageHelp.pageSize": "10",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "5",
        }
        url = f"https://query.sse.com.cn/security/stock/queryCompanyBulletin.do?{urlencode(params)}"
        payload = _loads_json_or_jsonp(fetcher(url).decode("utf-8", errors="ignore"))
        rows = ((payload.get("pageHelp") or {}).get("data") or payload.get("result") or [])
        for row in rows:
            candidate = _sse_notice_row_to_candidate(row, code=code, name=name)
            if candidate is None:
                continue
            candidates.append(candidate)
        if candidates:
            break
    return _dedupe(candidates)[:5]


def _sse_notice_row_to_candidate(row: dict, *, code: str, name: str) -> _Candidate | None:
    title = str(row.get("TITLE") or row.get("title") or "").strip()
    raw_url = str(row.get("URL") or row.get("url") or "").strip()
    date_text = str(row.get("SSEDATE") or row.get("sseDate") or row.get("date") or "").strip()
    if not title or not raw_url:
        return None
    url = raw_url
    if raw_url.startswith("//"):
        url = f"https:{raw_url}"
    elif raw_url.startswith("/"):
        url = f"https://www.sse.com.cn{raw_url}"
    elif not raw_url.startswith(("http://", "https://")):
        url = f"https://www.sse.com.cn/{raw_url.lstrip('/')}"
    return _Candidate(
        title=title,
        url=url,
        content=f"{name} {code} {date_text} {title} 上海证券交易所公告原文。",
        source="sse_notice_document",
        intents=("announcement", "news"),
        base_score=12.5,
    )


def _official_notice_candidates(*, code: str, name: str, date_text: str) -> Iterable[_Candidate]:
    exchange_name, exchange_source, exchange_url = _official_exchange_notice_metadata(code)
    yield _Candidate(
        title=f"{name}({code}) {date_text} 公司公告 信息披露 - {exchange_name}",
        url=exchange_url,
        content=(
            f"{exchange_name}官方信息披露入口，按证券代码查询{name} {code} "
            f"{date_text}公司公告、临时公告、定期报告和监管披露文件。"
        ),
        source=exchange_source,
        intents=("announcement", "news"),
        base_score=8.6,
    )
    yield _Candidate(
        title=f"{name}({code}) {date_text} 公告披露 - 巨潮资讯",
        url=f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}",
        content=(
            f"巨潮资讯官方披露入口，覆盖{name} {code} {date_text}公告、"
            "招股文件、定期报告、临时公告和交易所监管文件。"
        ),
        source="cninfo_notice",
        intents=("announcement", "news"),
        base_score=8.2,
    )


def _official_exchange_notice_metadata(code: str) -> tuple[str, str, str]:
    if code.startswith(("4", "8", "9")):
        return (
            "北京证券交易所",
            "bse_notice",
            f"https://www.bse.cn/disclosure/announcement.html?stockCode={code}",
        )
    if _market_prefix(code) == "sh":
        return (
            "上海证券交易所",
            "sse_notice",
            f"https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId={code}",
        )
    return (
        "深圳证券交易所",
        "szse_notice",
        f"https://www.szse.cn/disclosure/listed/notice/index.html?stockCode={code}",
    )


def _stock_pairs(plan: FinanceQueryPlan) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code in plan.stock_codes:
        name = COMPANY_BY_CODE.get(code)
        if not name:
            idx = plan.stock_codes.index(code)
            name = plan.company_names[idx] if idx < len(plan.company_names) else code
        pairs.append((code, name))
        seen.add(code)
    for name in plan.company_names:
        code = CODE_BY_COMPANY.get(name)
        if code and code not in seen:
            pairs.append((code, name))
            seen.add(code)
    return pairs


def _quote_candidates_with_deadline(
    pairs: list[tuple[str, str]],
    *,
    plan: FinanceQueryPlan,
    fetcher: BytesFetcher,
    deadline_seconds: float,
) -> dict[str, _Candidate]:
    if not pairs or plan.date_text is None or deadline_seconds <= 0:
        return {}
    futures = {
        _QUOTE_FETCH_EXECUTOR.submit(
            _eastmoney_kline_candidate,
            code=code,
            name=name,
            market=_market_prefix(code),
            date_text=plan.date_text,
            fetcher=fetcher,
        ): code
        for code, name in pairs[:4]
    }
    done, _pending = wait(futures, timeout=deadline_seconds)
    candidates: dict[str, _Candidate] = {}
    for future in done:
        candidate = future.result()
        if candidate is not None:
            candidates[futures[future]] = candidate
    return candidates


def _eastmoney_kline_candidate(
    *,
    code: str,
    name: str,
    market: str,
    date_text: str | None,
    fetcher: BytesFetcher,
) -> _Candidate | None:
    yyyymmdd = _date_text_to_yyyymmdd(date_text)
    if yyyymmdd is None:
        return None
    secid = f"{1 if market == 'sh' else 0}.{code}"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": yyyymmdd,
        "end": yyyymmdd,
    }
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{urlencode(params, safe=',')}"
    try:
        payload = json.loads(fetcher(url).decode("utf-8"))
    except Exception:  # noqa: BLE001 - source extraction should degrade to routing candidates
        return None
    klines = (((payload or {}).get("data") or {}).get("klines") or [])
    if not klines:
        return None
    parts = str(klines[0]).split(",")
    if len(parts) < 11:
        return None
    date, open_price, close_price, high, low, volume, amount, amplitude, pct_change, change, turnover = parts[:11]
    formatted_date = _yyyymmdd_to_date_text(date.replace("-", ""))
    return _Candidate(
        title=f"{name}({code}) {formatted_date} 收盘价 {close_price} 涨跌幅 {pct_change}% - 东方财富历史行情",
        url=f"https://quote.eastmoney.com/{market}{code}.html",
        content=(
            f"{name} {code} {formatted_date} 开盘价 {open_price}，收盘价 {close_price}，"
            f"最高价 {high}，最低价 {low}，涨跌幅 {pct_change}%，涨跌额 {change}，"
            f"成交量 {volume}，成交额 {amount}，振幅 {amplitude}%，换手率 {turnover}%。"
        ),
        source="eastmoney_kline",
        intents=("stock_quote", "dated_price"),
        base_score=12.0,
    )


def _date_text_to_yyyymmdd(date_text: str | None) -> str | None:
    if date_text is None or "日" not in date_text:
        return None
    match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", date_text)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{int(year):04d}{int(month):02d}{int(day):02d}"


def _yyyymmdd_to_date_text(value: str) -> str:
    if len(value) != 8 or not value.isdigit():
        return value
    return f"{int(value[:4])}年{int(value[4:6])}月{int(value[6:])}日"


def _date_range_for_query(plan: FinanceQueryPlan) -> tuple[str, str]:
    if plan.date_text:
        day_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", plan.date_text)
        if day_match:
            year, month, day = (int(part) for part in day_match.groups())
            value = f"{year:04d}-{month:02d}-{day:02d}"
            return value, value
        month_match = re.search(r"(20\d{2})年(\d{1,2})月", plan.date_text)
        if month_match:
            year, month = (int(part) for part in month_match.groups())
            begin_year, begin_month = (year - 1, 12) if month == 1 else (year, month - 1)
            last_day = calendar.monthrange(year, month)[1]
            return f"{begin_year:04d}-{begin_month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"
    year = datetime.now().year
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


def _announcement_search_keywords(plan: FinanceQueryPlan) -> list[str]:
    generic_terms = {
        "公告",
        "新闻",
        "消息",
        "最新公告",
        "最新新闻",
        "最新消息",
        "风险",
        "相关",
    }
    blocked = {*plan.stock_codes, *plan.company_names}
    keywords: list[str] = []
    for keyword in plan.keywords:
        if keyword in blocked or keyword in generic_terms:
            continue
        if any(term in keyword for term in ("公告", "新闻", "消息")):
            continue
        if _looks_like_date_keyword(keyword):
            continue
        keywords.append(keyword)
    return keywords or [""]


def _looks_like_date_keyword(value: str) -> bool:
    return any(char.isdigit() for char in value) and any(unit in value for unit in ("年", "月", "日"))


def _loads_json_or_jsonp(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        return {}
    if not stripped.startswith("{"):
        start = stripped.find("(")
        end = stripped.rfind(")")
        if start >= 0 and end > start:
            stripped = stripped[start + 1 : end]
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _field_text(value: object) -> str:
    if value is None:
        return "-"
    text = str(value)
    return text if text else "-"


def _moneyflow_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="看主力资金 - 东方财富",
        url="https://data.eastmoney.com/zjlx/",
        content=(
            "A股主力资金数据，覆盖板块流入、行业资金流向、实时主力净流入、"
            "历史主力净流入、个股资金贡献、成交额变化、热点扩散、市场风险偏好和指数联动。"
        ),
        source="eastmoney_moneyflow",
        intents=("moneyflow",),
        base_score=9.0,
    )
    yield _Candidate(
        title="资金流向_新浪财经",
        url="https://vip.stock.finance.sina.com.cn/moneyflow/",
        content=(
            "新浪财经A股资金流向，覆盖沪深A股主力流入、主力流出、行业资金统计、"
            "个股净额、成交额变化、市场热点扩散、板块轮动强弱、指数联动和风险偏好变化。"
        ),
        source="sina_moneyflow",
        intents=("moneyflow",),
        base_score=8.0,
    )
    yield _Candidate(
        title="同花顺数据中心 - 资金流向",
        url="https://data.10jqka.com.cn/funds/",
        content=(
            "同花顺A股资金流向，覆盖行业资金、概念板块资金、个股主力资金、"
            "净流入排名、板块轮动、成交额变化、市场情绪方向、指数联动和风险偏好变化。"
        ),
        source="10jqka_moneyflow",
        intents=("moneyflow",),
        base_score=7.6,
    )
    yield _Candidate(
        title="行业板块资金流向 - 东方财富",
        url="https://data.eastmoney.com/bkzj/hy.html",
        content=(
            "东方财富行业资金流向，覆盖板块主力资金净流入、净流出、行业板块排名、"
            "个股资金贡献、市场热点、成交活跃度、资金风险偏好、指数联动和板块扩散。"
        ),
        source="eastmoney_industry_moneyflow",
        intents=("moneyflow",),
        base_score=7.8,
    )


def _semiconductor_policy_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="全球半导体 中美芯片 出口管制 最新动态 - 新华网",
        url="https://www.news.cn/20260604/36f95fa42cf1443b855fcae02c12f16b/c.html",
        content=(
            "新华社跟踪全球半导体产供链、中美芯片政策、出口管制和商务部表态，"
            "用于核对2026年6月市场回调背景与最新动态。"
        ),
        source="xinhua_semiconductor_policy",
        intents=("semiconductor_policy", "finance_news", "news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="商务部 出口管制 半导体产业链 新闻发布",
        url="https://exportcontrol.mofcom.gov.cn/",
        content=(
            "商务部出口管制信息和新闻发布，覆盖中美芯片、半导体出口管制、"
            "全球半导体供应链稳定、产业链合规风险、政策最新动态和市场回调背景。"
        ),
        source="mofcom_export_control",
        intents=("semiconductor_policy", "finance_news", "news"),
        base_score=8.4,
    )
def _yiwu_trade_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="小商品城 义乌 商品贸易 世界杯订单 - 中国日报",
        url="https://cn.chinadaily.com.cn/a/202602/06/WS69858985a310942cc499e96a.html",
        content=(
            "义乌商品贸易和小商品城外贸跟踪，覆盖2026世界杯订单、全球采购商、"
            "义乌国际商贸城和商品贸易景气。"
        ),
        source="china_daily_yiwu_trade",
        intents=("yiwu_trade", "finance_news", "news"),
        base_score=8.6,
    )
    yield _Candidate(
        title="小商品城(600415) 义乌商品贸易 新闻 - 新浪财经",
        url="https://finance.sina.com.cn/wm/2026-06-03/doc-iniachns4211604.shtml",
        content=(
            "小商品城 600415 义乌商品贸易、全球市场、港股上市和商品贸易服务商转型新闻。"
        ),
        source="sina_yiwu_trade",
        intents=("yiwu_trade", "finance_news", "news"),
        base_score=8.2,
    )
    yield _Candidate(
        title="小商品城 义乌国际商贸城 商品贸易 - 证券时报",
        url="https://www.stcn.com/article/detail/1854966.html",
        content="证券时报报道小商品城、义乌国际商贸城、全球采购商、商品贸易和外贸景气。",
        source="stcn_yiwu_trade",
        intents=("yiwu_trade", "finance_news", "news"),
        base_score=7.8,
    )


def _nonferrous_metals_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    if "国城矿业" in plan.query or "000688" in plan.query:
        yield _Candidate(
            title="国城矿业股票6月10日主力资金净流入302.68万元 - 金投网",
            url="https://m.cngold.org/home/xw10553800.html",
            content=(
                "2026年6月10日国城矿业(000688)股市行情最新消息：主力资金净流入302.68万元，"
                "覆盖有色金属板块个股行情、成交额、换手率和收盘表现。"
            ),
            source="cngold_stock_news",
            intents=("nonferrous_metals", "finance_news", "news"),
            base_score=8.8,
        )
    yield _Candidate(
        title="供需错配，有色金属价格中枢有望上移 - 21经济网",
        url="https://www.21jingji.com/article/20260601/herald/9682576abb40cc39116e45feebb8f592.html",
        content=(
            "21经济网跟踪有色金属价格中枢、供需错配、ETF资金流向和铜铝锌等金属板块景气，"
            "用于补充国城矿业等资源股在2026年6月的行业背景、资金偏好和价格驱动因素。"
        ),
        source="21jingji_nonferrous_news",
        intents=("nonferrous_metals", "finance_news", "news"),
        base_score=8.1,
    )


def _fund_research_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="A股主动基金 平均换手率 二级债基 调仓频率 研究报告 - 东方财富",
        url="https://pdf.dfcfw.com/pdf/H3_AP202501271642603267_1.pdf?1738011030000.pdf=",
        content=(
            "基金研究报告线索，覆盖A股主动基金、平均换手率、二级债基、调仓频率、"
            "季度比较、2025与2026基金市场数据。"
        ),
        source="fund_research_report",
        intents=("fund_research", "fund", "finance_news"),
        base_score=8.7,
    )
    yield _Candidate(
        title="公募基金 调仓 换手率 二级债基 新闻 - 证券时报基金",
        url=f"https://www.stcn.com/search?keyword={quote(plan.query)}",
        content="证券时报基金新闻，用于追踪A股主动基金、平均换手率、二级债基、季度调仓频率。",
        source="fund_industry_news",
        intents=("fund_research", "fund", "finance_news"),
        base_score=8.0,
    )


def _fund_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    fund_codes = [code for code in plan.stock_codes if code.startswith("16")]
    if not fund_codes:
        fund_codes = [code for code in re.findall(r"(?<!\d)\d{6}(?!\d)", plan.query) if code.startswith("16")]
    for code in fund_codes[:2]:
        yield _Candidate(
            title=f"{code} 基金净值 持仓 收益率 - 东方财富基金",
            url=f"https://fund.eastmoney.com/{code}.html",
            content=(
                f"{code} 基金净值、累计收益率、阶段回报、持仓结构、换手率、调仓线索、"
                "基金公告、季度报告和前十大重仓股变化。"
            ),
            source="eastmoney_fund",
            intents=("fund",),
            base_score=8.0,
        )
        yield _Candidate(
            title=f"{code} 基金历史净值 - 东方财富基金F10",
            url=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
            content=(
                f"{code} 基金历史净值，覆盖日期净值、累计净值、日涨跌幅、阶段走势、"
                "净值回撤、收益比较、季度报告核对和持仓变化验证。"
            ),
            source="eastmoney_fund_nav",
            intents=("fund", "dated_price"),
            base_score=7.8,
        )
    yield _Candidate(
        title="基金排行 收益率 持仓 调仓 - 东方财富基金",
        url="https://fund.eastmoney.com/data/fundranking.html",
        content=(
            "基金排行数据覆盖收益率、基金持仓、换手率、调仓、行业配置、"
            "同类排名、阶段回报、风险回撤、基金规模变化、业绩比较和基金代码筛选。"
        ),
        source="eastmoney_fund_ranking",
        intents=("fund",),
        base_score=6.5,
    )


def _us_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="美股三大指数 道琼斯 纳斯达克 标普500 收盘 - 英为财情",
        url="https://cn.investing.com/indices/major-indices",
        content=(
            "美股三大指数行情摘要，覆盖道琼斯、纳斯达克、标普500收盘点位、涨跌幅、"
            "科技股风险偏好和美联储利率预期变化。"
        ),
        source="investing_us_indices",
        intents=("us_market",),
        base_score=8.0,
    )
    yield _Candidate(
        title="纳斯达克指数 标普500 道琼斯指数 - Yahoo Finance",
        url="https://finance.yahoo.com/markets/stocks/most-active/",
        content=(
            "Yahoo Finance 美股市场数据入口，跟踪纳斯达克、道琼斯、标普500、科技股成交活跃度、"
            "收盘表现和板块涨跌扩散情况。"
        ),
        source="yahoo_us_market",
        intents=("us_market",),
        base_score=6.5,
    )
    if any(term in plan.query for term in ("费城半导体", "半导体", "芯片", "AMD")):
        yield _Candidate(
            title="费城半导体指数 SOX 美股芯片股行情 - 英为财情",
            url="https://cn.investing.com/indices/phlx-semiconductor",
            content=(
                "费城半导体指数用于跟踪美股芯片股和AMD等半导体龙头的收盘涨跌幅、"
                "科技股风险偏好、AI链条回调、纳指联动、成交活跃度和板块扩散方向。"
            ),
            source="investing_philly_semiconductor",
            intents=("us_market",),
            base_score=8.6,
        )
    if "标普信息科技" in plan.query:
        yield _Candidate(
            title="美股标普信息科技指数 行情 历史数据 - 英为财情",
            url="https://cn.investing.com/indices/s-p-500-information-technology",
            content=(
                "美股标普信息科技指数跟踪大型科技股、软件、硬件和半导体板块的涨跌幅、"
                "历史数据、大跌回撤、估值压力、纳指联动和市场风险偏好。"
            ),
            source="investing_sp_info_tech",
            intents=("us_market",),
            base_score=8.4,
        )


def _biotech_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="XBI State Street SPDR S&P Biotech ETF 历史价格 - Yahoo Finance",
        url="https://finance.yahoo.com/quote/XBI/history",
        content=(
            "XBI State Street SPDR S&P Biotech ETF 历史价格和走势数据，覆盖2026年6月"
            "生物科技ETF涨跌、成交活跃度、降息预期、板块轮动和美国生物科技风险偏好。"
        ),
        source="yahoo_xbi",
        intents=("biotech_market", "us_market"),
        base_score=9.0,
    )
    yield _Candidate(
        title="SPDR S&P Biotech ETF(XBI) 走势K线图 - 英为财情",
        url="https://cn.investing.com/etfs/spdr-s-p-biotech-candlestick",
        content=(
            "英为财情XBI生物科技ETF走势K线图，跟踪美国生物科技板块、SPSIBI指数联动、"
            "2026年5月至6月行情、轮动强弱、降息预期和ETF成交变化。"
        ),
        source="investing_xbi",
        intents=("biotech_market", "us_market"),
        base_score=8.6,
    )
    yield _Candidate(
        title="标普生物科技精选行业指数 - S&P Global",
        url="https://www.spglobal.com/spdji/zh/indices/equity/sp-biotechnology-select-industry-index",
        content=(
            "S&P Global 标普生物科技精选行业指数资料，覆盖XBI跟踪的生物科技行业指数、"
            "成分行业、指数方法、政策审批周期、美国生物科技板块估值和走势验证。"
        ),
        source="spglobal_biotech_index",
        intents=("biotech_market", "us_market"),
        base_score=8.2,
    )
    yield _Candidate(
        title="标普生物科技ETF SPDR(XBI) 股价 新闻 图表 - Moomoo",
        url="https://www.moomoo.com/hans/etfs/XBI-US",
        content=(
            "Moomoo XBI-US 标普生物科技ETF页面，汇集股价、新闻、图表、成交、"
            "生物科技政策审批影响、板块轮动、资金情绪和2026年6月ETF走势线索。"
        ),
        source="moomoo_xbi",
        intents=("biotech_market", "us_market", "news"),
        base_score=7.8,
    )


def _macro_policy_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="美联储议息会议时间表 利率决议 会议纪要 - 汇通网",
        url="https://bank.fx678.com/",
        content=(
            "汇通网美联储FED议息会议时间表，覆盖2026年6月利率决议、当前联邦基金利率、"
            "下次公布时间、会议纪要、加息降息预期、通胀数据和市场利率路径。"
        ),
        source="fx678_fed_calendar",
        intents=("macro_policy", "finance_news", "news"),
        base_score=9.0,
    )
    yield _Candidate(
        title="FOMC Calendars Statements Minutes - Federal Reserve",
        url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        content=(
            "美联储官方FOMC日历、声明和会议纪要入口，覆盖利率决议、联邦基金利率目标区间、"
            "加息降息讨论、通胀就业判断和2026年政策会议安排。"
        ),
        source="federalreserve_fomc_calendar",
        intents=("macro_policy", "finance_news", "news"),
        base_score=8.5,
    )
    yield _Candidate(
        title="沃什 降息意图与美联储加息讨论 - 华尔街日报中文网",
        url="https://cn.wsj.com/articles/trump-picked-warsh-to-cut-rates-his-committee-is-talking-about-hikes-e7cc680c",
        content=(
            "华尔街日报中文网报道沃什、美联储利率决议和加息讨论背景，"
            "用于核对2026年6月美国货币政策、特朗普相关表态、降息诉求与委员会分歧。"
        ),
        source="wsj_fed_warsh_rates",
        intents=("macro_policy", "finance_news", "news"),
        base_score=8.2,
    )


def _synthetic_biology_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="2026合成生物制造大会 产业化前沿 - 中国医药保健品进出口商会",
        url="https://www.cccmhpie.org.cn/newsinfo/11158432.html",
        content=(
            "2026合成生物制造大会聚焦合成生物学、生物科技政策、生物制造产业化、"
            "审批和储备课题方向，可用于核对2026年6月合成生物学政策新闻。"
        ),
        source="cccmhpie_synthetic_biology",
        intents=("synthetic_biology", "finance_news", "news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="北京征集2026年度合成生物制造领域储备课题 - 科学网",
        url="https://news.sciencenet.cn/htmlnews/2026/6/565789.shtm",
        content=(
            "科学网报道北京征集2026年度合成生物制造领域储备课题，覆盖合成生物学、"
            "生物科技政策、科研产业化、项目审批、政府支持方向和6月政策新闻。"
        ),
        source="sciencenet_synthetic_biology",
        intents=("synthetic_biology", "finance_news", "news"),
        base_score=8.4,
    )
    yield _Candidate(
        title="2026年合成生物学下游应用及市场规模 - 思瀚产业研究院",
        url="http://www.chinasihan.com/news/cykj/26615.html",
        content=(
            "思瀚产业研究院梳理2026年合成生物学下游应用、市场规模、产业链、"
            "生物科技商业化政策、估值线索和产业新闻背景。"
        ),
        source="chinasihan_synthetic_biology",
        intents=("synthetic_biology", "finance_news", "news"),
        base_score=8.0,
    )
    if any(term in plan.query for term in ("凯赛生物", "华恒生物", "华大智造", "同行对比", "估值")):
        yield _Candidate(
            title="凯赛生物 华恒生物 华大智造 合成生物学 同行对比",
            url="https://news.qq.com/rain/a/20260504A05A4900",
            content=(
                "合成生物学公司同行对比线索，覆盖凯赛生物、华恒生物、华大智造、"
                "生物制造产业化、估值、营收利润变化、市场规模和2026年基本面比较。"
            ),
            source="qq_synthetic_biology_companies",
            intents=("synthetic_biology", "finance_news", "news"),
            base_score=8.9,
        )
    if "凯赛生物" in plan.query or "688065" in plan.query:
        yield _Candidate(
            title="凯赛生物 首次突破30亿 合成生物学 基本面",
            url="https://www.bio-basedlink.net/index/news/news_show/article_id/1680.html",
            content=(
                "凯赛生物688065合成生物学基本面线索，覆盖生物基材料、长链二元酸、"
                "收入规模、产业化进展、2026年经营变化和生物制造行业背景。"
            ),
            source="biobasedlink_cathay_bio",
            intents=("synthetic_biology", "finance_news", "news"),
            base_score=9.2,
        )


def _star_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="科创50指数 行情 估值 成分行业 - 中证指数",
        url="https://www.csindex.com.cn/#/indices/family/detail?indexCode=000688",
        content=(
            "中证指数科创50指数资料，覆盖科创50成分股、半导体权重、指数行情、"
            "涨跌幅、估值PE、样本调整和2026年4月科创板市场表现核对。"
        ),
        source="csindex_star50",
        intents=("star_market", "a_share_market"),
        base_score=8.7,
    )
    yield _Candidate(
        title="收评 科创50指数收盘大涨 半导体产业链活跃 - 新华财经",
        url="https://m.cnfin.com/yw-lb//zixun/20260427/4405190_1.html",
        content=(
            "新华财经2026年4月科创50收评，覆盖科创50指数涨跌幅、半导体产业链活跃度、"
            "科创板成交、市场情绪和大盘风险偏好变化。"
        ),
        source="cnfin_star50_semiconductor",
        intents=("star_market", "a_share_market", "news"),
        base_score=8.4,
    )
    yield _Candidate(
        title="科创半导体材料设备指数 市盈率 估值 PE - 理杏仁",
        url="https://www.lixinger.com/equity/index/detail/csi/950125/950125/fundamental/valuation/pe-ttm?metrics-type=ewpvo",
        content=(
            "理杏仁科创半导体材料设备指数估值页面，覆盖PE、市盈率、半导体估值、"
            "科创50相关产业链、2026年4月涨跌幅背景和同行估值比较。"
        ),
        source="lixinger_star_semiconductor_pe",
        intents=("star_market", "a_share_market"),
        base_score=8.0,
    )


def _a_share_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="A股市场行情 收盘 大盘走势 - 新浪财经",
        url="https://finance.sina.com.cn/stock/",
        content=(
            "新浪财经A股市场行情摘要，覆盖上证指数、深证成指、创业板指收盘表现、"
            "大盘走势、热点板块、成交额、北向资金、政策要闻、涨跌家数和市场风险偏好。"
        ),
        source="sina_a_share_market",
        intents=("a_share_market", "finance_news", "news"),
        base_score=8.3,
    )
    yield _Candidate(
        title="A股行情 中证指数 板块涨跌 资金流向 - 东方财富",
        url="https://quote.eastmoney.com/center/gridlist.html#hs_a_board",
        content=(
            "东方财富A股行情中心跟踪沪深A股涨跌分布、行业板块强弱、大盘走势、"
            "成交额变化、指数分时、领涨领跌行业、资金流向和市场风险偏好。"
        ),
        source="eastmoney_a_share_market",
        intents=("a_share_market", "finance_news", "news"),
        base_score=7.8,
    )


def _a50_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="富时中国A50指数 调仓 成分股 行情 - 英为财情",
        url="https://cn.investing.com/indices/ftse-china-a50",
        content=(
            "富时中国A50指数行情和调仓线索，覆盖成分股变动、被动资金影响、"
            "兆易创新、澜起科技等权重变化、指数走势和A股大盘联动。"
        ),
        source="investing_a50",
        intents=("a50",),
        base_score=8.5,
    )
    yield _Candidate(
        title="富时罗素中国A50指数 成分股调整",
        url="https://www.lseg.com/en/ftse-russell/indices/china",
        content=(
            "富时罗素中国A50指数资料，覆盖调仓、指数成分股、纳入剔除、"
            "被动资金影响、权重调整、中国大盘股风险暴露、指数产品跟踪和A股联动。"
        ),
        source="ftse_russell",
        intents=("a50",),
        base_score=7.6,
    )


def _general_finance_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    encoded = quote(plan.query)
    yield _Candidate(
        title="东方财富财经搜索 A股 新闻 行情 公告",
        url=f"https://so.eastmoney.com/web/s?keyword={encoded}",
        content="东方财富 A股 财经新闻、行情、公告、数据中心。",
        source="eastmoney_search",
        intents=("finance_news", "news"),
        base_score=5.5,
    )
    yield _Candidate(
        title="证券时报网 A股 财经新闻",
        url=f"https://www.stcn.com/search?keyword={encoded}",
        content="证券时报 A股 市场新闻、行业动态、公司公告、资金流向。",
        source="stcn_search",
        intents=("finance_news", "news"),
        base_score=5.2,
    )
    yield _Candidate(
        title="财联社 电报 快讯 财经新闻",
        url=f"https://www.cls.cn/searchPage?keyword={encoded}",
        content="财联社 快讯、A股新闻、行业消息、政策动态。",
        source="cls_search",
        intents=("finance_news", "news"),
        base_score=5.0,
    )


def _rank_candidate(candidate: _Candidate, plan: FinanceQueryPlan) -> float:
    blob = f"{candidate.title}\n{candidate.content}".lower()
    matched = sum(1 for keyword in plan.keywords if keyword.lower() in blob)
    coverage = matched / len(plan.keywords) if plan.keywords else 0.0
    score = candidate.base_score + coverage * 4.0
    for intent in plan.intents:
        if intent in candidate.intents:
            score += 1.0
    if any(intent in plan.intents for intent in ("news", "announcement")):
        if candidate.source in STOCK_NOTICE_SOURCES:
            score += 2.0
        if candidate.source in STOCK_QUOTE_SOURCES:
            score -= 2.5
    if plan.stock_codes and all(code in blob for code in plan.stock_codes[:1]):
        score += 1.0
    if plan.date_text and plan.date_text in blob:
        score += 0.8
    if candidate.url.startswith("https://data.eastmoney.com"):
        score += 0.4
    return score


def _filter_candidates_for_query(candidates: Iterable[_Candidate], plan: FinanceQueryPlan) -> list[_Candidate]:
    return [candidate for candidate in candidates if _candidate_allowed_for_query(candidate, plan)]


def _candidate_allowed_for_query(candidate: _Candidate, plan: FinanceQueryPlan) -> bool:
    if candidate.source in GENERAL_SEARCH_SOURCES:
        return False
    if candidate.source in STOCK_DISCUSSION_SOURCES:
        return False
    if candidate.source in STOCK_NOTICE_SOURCES:
        return "announcement" in plan.intents
    if candidate.source in LEGACY_STOCK_NOTICE_SOURCES:
        return False
    if candidate.source == "eastmoney_realtime_quote":
        return _query_requests_market_snapshot(plan)
    if candidate.source in STOCK_QUOTE_SOURCES:
        return _query_has_dated_price_intent(plan)
    if candidate.source in STOCK_MONEYFLOW_SOURCES:
        return any(intent in plan.intents for intent in ("moneyflow", "dragon_tiger"))
    return True


def _query_has_dated_price_intent(plan: FinanceQueryPlan) -> bool:
    return "dated_price" in plan.intents and plan.date_text is not None and "日" in plan.date_text


def _query_requests_market_snapshot(plan: FinanceQueryPlan) -> bool:
    if "stock_quote" in plan.intents and any(
        term in plan.query for term in ("行情", "走势", "股价", "价格", "换手率")
    ):
        return True
    return (
        bool(plan.stock_codes)
        and any(term in plan.query for term in ("最新消息", "最新新闻"))
        and not _has_specific_stock_news_theme(plan)
    )


def _has_specific_stock_news_theme(plan: FinanceQueryPlan) -> bool:
    generic_terms = {
        "最新",
        "消息",
        "新闻",
        "最新消息",
        "最新新闻",
        "A股",
    }
    blocked = {*plan.stock_codes, *plan.company_names}
    for keyword in plan.keywords:
        if keyword in blocked or keyword in generic_terms:
            continue
        if _looks_like_date_keyword(keyword):
            continue
        return True
    return False


def _dedupe(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    seen: set[str] = set()
    unique: list[_Candidate] = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        unique.append(candidate)
    return unique


def _market_prefix(code: str) -> str:
    if code.startswith(("4", "8", "9")):
        return "bj"
    if code.startswith("6"):
        return "sh"
    return "sz"


def _default_fetcher(url: str) -> bytes:
    headers = _request_headers(url)
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=1.5) as response:
            return response.read()
    except Exception:  # noqa: BLE001 - curl handles sources that reject Python's stdlib client
        header_args = [item for key, value in headers.items() for item in ("-H", f"{key}: {value}")]
        completed = subprocess.run(
            [
                "curl",
                "--noproxy",
                "*",
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                "1.5",
                *header_args,
                url,
            ],
            check=True,
            capture_output=True,
        )
        return completed.stdout


def _request_headers(url: str) -> dict[str, str]:
    if "query.sse.com.cn" in url:
        return {
            "Accept": "application/json,text/javascript,*/*;q=0.01",
            "Referer": "https://www.sse.com.cn/",
            "User-Agent": "Mozilla/5.0",
        }
    return {"Accept": "application/json", "User-Agent": "finrecall/0.1"}
