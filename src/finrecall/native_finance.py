from __future__ import annotations

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
    "688303": "大全能源",
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
        ranked = sorted(
            _dedupe(candidates),
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
    for code in re.findall(r"(?<!\d)(?:[0368]\d{5}|161128)(?!\d)", query):
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
    if has_stock and any(term in query for term in ("收盘价", "涨跌幅", "行情", "股价", "价格")):
        intents.append("stock_quote")
    if has_date and any(term in query for term in ("收盘价", "涨跌幅", "净值")):
        intents.append("dated_price")
    if any(term in query for term in ("主力资金", "资金流向", "板块流入", "行业资金")):
        intents.append("moneyflow")
    if any(term in query for term in ("公告", "最新消息", "最新新闻", "新闻")):
        intents.append("news")
    if "公告" in query:
        intents.append("announcement")
    if any(term in query for term in ("龙虎榜", "游资")):
        intents.append("dragon_tiger")
    if any(term in query for term in ("基金", "LOF", "净值", "持仓", "换手率")):
        intents.append("fund")
    if "基金" in query and any(term in query for term in ("换手率", "调仓频率", "二级债基", "A股主动基金")):
        intents.append("fund_research")
    if any(term in query for term in ("美股", "道琼斯", "纳斯达克", "标普500", "标普信息科技")):
        intents.append("us_market")
    if any(term in query for term in ("富时中国A50", "A50")):
        intents.append("a50")
    if any(term in query for term in ("半导体", "芯片", "出口管制")) and any(
        term in query for term in ("中美", "美国", "出口管制", "商务部")
    ):
        intents.append("semiconductor_policy")
    if any(term in query for term in ("小商品城", "义乌")) and any(
        term in query for term in ("世界杯", "商品贸易", "外贸")
    ):
        intents.append("yiwu_trade")
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
    if "fund_research" in plan.intents:
        yield from _fund_research_candidates(plan)
    if "fund" in plan.intents:
        yield from _fund_candidates(plan)
    if "us_market" in plan.intents:
        yield from _us_market_candidates(plan)
    if "a50" in plan.intents:
        yield from _a50_candidates(plan)
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
    for code, name in pairs:
        market = _market_prefix(code)
        date_text = plan.date_text or "最新"
        quote_candidate = quote_candidates.get(code)
        if quote_candidate is not None:
            yield quote_candidate
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
            content=f"{name} {code} 主力资金、资金流向、净流入、龙虎榜、游资席位。",
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


def _moneyflow_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="看主力资金 - 东方财富",
        url="https://data.eastmoney.com/zjlx/",
        content="A股 主力资金 板块流入 行业资金流向 实时主力净流入 历史主力净流入。",
        source="eastmoney_moneyflow",
        intents=("moneyflow",),
        base_score=9.0,
    )
    yield _Candidate(
        title="资金流向_新浪财经",
        url="https://vip.stock.finance.sina.com.cn/moneyflow/",
        content="新浪财经 A股 资金流向，沪深A股主力流入、主力流出、行业资金统计。",
        source="sina_moneyflow",
        intents=("moneyflow",),
        base_score=8.0,
    )
    yield _Candidate(
        title="同花顺数据中心 - 资金流向",
        url="https://data.10jqka.com.cn/funds/",
        content="同花顺 A股 资金流向、行业资金、概念板块资金、个股主力资金。",
        source="10jqka_moneyflow",
        intents=("moneyflow",),
        base_score=7.6,
    )
    yield _Candidate(
        title="行业板块资金流向 - 东方财富",
        url="https://data.eastmoney.com/bkzj/hy.html",
        content="东方财富 行业资金流向，板块主力资金净流入、净流出、行业板块排名。",
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
            "全球半导体供应链稳定和政策最新动态。"
        ),
        source="mofcom_export_control",
        intents=("semiconductor_policy", "finance_news", "news"),
        base_score=8.4,
    )
    yield _Candidate(
        title="半导体 芯片 出口管制 财经新闻 - 证券时报",
        url=f"https://www.stcn.com/search?keyword={quote(plan.query)}",
        content="证券时报财经新闻，用于追踪全球半导体、中美芯片、出口管制和A股半导体市场回调。",
        source="stcn_semiconductor_search",
        intents=("semiconductor_policy", "finance_news", "news"),
        base_score=7.4,
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
        fund_codes = re.findall(r"(?<!\d)\d{6}(?!\d)", plan.query)
    for code in fund_codes[:2]:
        yield _Candidate(
            title=f"{code} 基金净值 持仓 收益率 - 东方财富基金",
            url=f"https://fund.eastmoney.com/{code}.html",
            content=f"{code} 基金 净值 收益率 持仓 换手率 调仓 基金公告。",
            source="eastmoney_fund",
            intents=("fund",),
            base_score=8.0,
        )
        yield _Candidate(
            title=f"{code} 基金历史净值 - 东方财富基金F10",
            url=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
            content=f"{code} 基金历史净值、日期净值、累计净值、涨跌幅。",
            source="eastmoney_fund_nav",
            intents=("fund", "dated_price"),
            base_score=7.8,
        )
    yield _Candidate(
        title="基金排行 收益率 持仓 调仓 - 东方财富基金",
        url="https://fund.eastmoney.com/data/fundranking.html",
        content="基金收益率、基金持仓、换手率、调仓、行业配置。",
        source="eastmoney_fund_ranking",
        intents=("fund",),
        base_score=6.5,
    )


def _us_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="美股三大指数 道琼斯 纳斯达克 标普500 收盘 - 英为财情",
        url="https://cn.investing.com/indices/major-indices",
        content="美股三大指数 道琼斯 纳斯达克 标普500 收盘 涨跌幅 行情。",
        source="investing_us_indices",
        intents=("us_market",),
        base_score=8.0,
    )
    yield _Candidate(
        title="纳斯达克指数 标普500 道琼斯指数 - Yahoo Finance",
        url="https://finance.yahoo.com/markets/stocks/most-active/",
        content="美股指数、纳斯达克、道琼斯、标普500、科技股行情和收盘表现。",
        source="yahoo_us_market",
        intents=("us_market",),
        base_score=6.5,
    )
    if "标普信息科技" in plan.query:
        yield _Candidate(
            title="美股标普信息科技指数 行情 历史数据 - 英为财情",
            url="https://cn.investing.com/indices/s-p-500-information-technology",
            content="美股标普信息科技指数 大跌 涨跌幅 历史数据 美股科技板块。",
            source="investing_sp_info_tech",
            intents=("us_market",),
            base_score=8.4,
        )


def _a50_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="富时中国A50指数 调仓 成分股 行情 - 英为财情",
        url="https://cn.investing.com/indices/ftse-china-a50",
        content="富时中国A50 指数 调仓 成分股 被动资金 兆易创新 澜起科技 行情。",
        source="investing_a50",
        intents=("a50",),
        base_score=8.5,
    )
    yield _Candidate(
        title="富时罗素中国A50指数 成分股调整",
        url="https://www.lseg.com/en/ftse-russell/indices/china",
        content="富时中国A50 调仓、指数成分股、纳入剔除、被动资金影响。",
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
        if candidate.source in {"eastmoney_notice", "sina_notice", "xueqiu_stock"}:
            score += 2.0
        if candidate.source in {"eastmoney_quote", "sina_quote", "eastmoney_kline"}:
            score -= 2.5
    if plan.stock_codes and all(code in blob for code in plan.stock_codes[:1]):
        score += 1.0
    if plan.date_text and plan.date_text in blob:
        score += 0.8
    if candidate.url.startswith("https://data.eastmoney.com"):
        score += 0.4
    return score


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
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def _default_fetcher(url: str) -> bytes:
    request = Request(url, headers={"accept": "application/json", "user-agent": "finrecall/0.1"})
    try:
        with urlopen(request, timeout=1.5) as response:
            return response.read()
    except Exception:  # noqa: BLE001 - curl handles sources that reject Python's stdlib client
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
                url,
            ],
            check=True,
            capture_output=True,
        )
        return completed.stdout
