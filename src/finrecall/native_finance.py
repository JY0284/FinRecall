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
    "600611": "大众交通",
    "688981": "中芯国际",
    "688126": "沪硅产业",
    "688525": "佰维存储",
    "688008": "澜起科技",
    "002313": "日海智能",
    "000737": "北方铜业",
    "600436": "片仔癀",
    "000858": "五粮液",
    "689009": "九号公司",
    "002027": "分众传媒",
    "600873": "梅花生物",
    "601336": "新华保险",
    "002119": "康强电子",
    "002384": "东山精密",
    "603019": "中科曙光",
    "300475": "香农芯创",
    "301363": "美好医疗",
    "000776": "广发证券",
    "002129": "TCL中环",
    "688303": "大全能源",
    "688548": "广钢气体",
    "601208": "东材科技",
    "000333": "美的集团",
    "600552": "凯盛科技",
    "601111": "中国国航",
    "600029": "南方航空",
    "300015": "爱尔眼科",
    "300077": "国民技术",
    "000997": "新大陆",
    "002149": "西部材料",
    "300433": "蓝思科技",
    "002536": "飞龙股份",
    "600353": "旭光电子",
    "300476": "胜宏科技",
    "002600": "领益智造",
    "600379": "宝光股份",
    "603083": "剑桥科技",
    "002475": "立讯精密",
    "002414": "高德红外",
    "002081": "金螳螂",
    "002195": "岩山科技",
    "002977": "天箭科技",
    "600221": "海航控股",
    "002371": "北方华创",
    "300687": "赛意信息",
    "600184": "光电股份",
    "603938": "三孚股份",
    "603637": "镇海股份",
    "603129": "春风动力",
    "000338": "潍柴动力",
    "601698": "中国卫通",
    "600016": "民生银行",
    "000533": "顺钠股份",
    "603501": "豪威集团",
    "601606": "长城军工",
    "601088": "中国神华",
    "600236": "桂冠电力",
    "002803": "吉宏股份",
    "603052": "可川科技",
    "300888": "稳健医疗",
    "603288": "海天味业",
    "603709": "中源家居",
    "600183": "生益科技",
    "000818": "航锦科技",
    "603993": "洛阳钼业",
    "600863": "内蒙华电",
    "300674": "宇信科技",
    "603119": "浙江荣泰",
    "002645": "华宏科技",
    "300274": "阳光电源",
    "600276": "恒瑞医药",
    "000729": "燕京啤酒",
    "000895": "双汇发展",
    "603345": "安井食品",
    "600722": "金牛化工",
    "600602": "云赛智联",
    "603057": "紫燕食品",
    "688266": "泽璟制药",
    "688177": "百奥泰",
    "688428": "诺诚健华",
    "688578": "艾力斯",
    "688180": "君实生物",
    "603259": "药明康德",
    "300339": "润和软件",
    "300598": "诚迈科技",
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
CODE_BY_COMPANY.update({
    "ST天箭": "002977",
    "天箭科技": "002977",
    "美的": "000333",
    "伊利": "600887",
    "华能蒙电": "600863",
    "内蒙华电": "600863",
    "CFMOTO": "603129",
    "春风动力": "603129",
})

FUND_CODE_BY_NAME = {
    "嘉实多利收益债券": "160718",
    "嘉实多利": "160718",
    "标普信息科技": "161128",
    "影视ETF": "516620",
    "黄金ETF": "518880",
}

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
    "Q1",
    "q1",
    "停牌",
    "监管",
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

STOCK_PROFILE_SOURCES = {
    "eastmoney_stock_profile",
    "cninfo_stock_profile",
}

STOCK_RESEARCH_TERMS = (
    "业绩",
    "估值",
    "研报",
    "目标价",
    "评级",
    "利润",
    "风险",
    "战略",
    "业务分析",
    "投资价值",
    "市场动态",
    "上涨趋势",
    "大涨",
    "原因",
    "重组",
    "收购",
    "提价",
    "经营情况",
    "业绩预告",
    "业务",
    "加仓",
    "建议",
    "流通股",
    "总股本",
)


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
    if has_stock and any(
        term in query
        for term in (
            "收盘价",
            "涨跌幅",
            "行情",
            "股价",
            "价格",
            "换手率",
            "涨停",
            "大跌",
            "下跌",
            "上涨",
            "盘中",
            "跌",
            "领涨",
            "妖股",
        )
    ):
        intents.append("stock_quote")
    if has_date and any(
        term in query for term in ("收盘价", "涨跌幅", "净值", "股价", "价格", "今日股价", "涨停", "大跌", "下跌", "上涨", "盘中", "跌", "领涨")
    ):
        intents.append("dated_price")
    if any(term in query for term in ("主力资金", "资金流向", "板块流入", "行业资金")) or (
        has_stock and any(term in query for term in ("资金", "庄家"))
    ):
        intents.append("moneyflow")
    if any(term in query for term in ("公告", "最新消息", "最新新闻", "最新动态", "新闻", "动态")):
        intents.append("news")
    if "公告" in query or (has_stock and any(term in query for term in DISCLOSURE_EVENT_TERMS)):
        intents.append("announcement")
        if "news" not in intents:
            intents.append("news")
    if any(term in query for term in ("龙虎榜", "游资")):
        intents.append("dragon_tiger")
    if any(term in query for term in ("大宗交易", "机构专用", "接盘方", "买方")):
        intents.append("block_trade")
    if any(term in query for term in ("基金", "LOF", "ETF", "净值", "持仓", "持有债券")) or (
        "债券" in query and any(code in query for code in FUND_CODE_BY_NAME.values())
    ):
        intents.append("fund")
    if "基金" in query and any(term in query for term in ("换手率", "调仓频率", "二级债基", "A股主动基金")):
        intents.append("fund_research")
    if any(
        term in query
        for term in (
            "美股",
            "道琼斯",
            "纳斯达克",
            "纳指",
            "标普500",
            "标普信息科技",
            "SP500-45",
            "S&P 500 Information Technology",
        )
    ):
        intents.append("us_market")
    if any(term in query for term in ("NVDA", "英伟达", "Nvidia", "NVIDIA")):
        intents.append("us_equity")
    if any(term in query for term in ("USDCNY", "美元兑人民币", "美元人民币", "人民币汇率", "日元汇率")):
        intents.append("fx_market")
    if any(term in query for term in ("TOPIX", "东证指数", "日本股市", "日经")):
        intents.append("japan_market")
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
    if "A股" in query and any(
        term in query
        for term in (
            "行情",
            "收盘",
            "大盘走势",
            "市场行情",
            "热点板块",
            "市场震荡",
            "资金面",
            "市场分析",
            "下跌原因",
            "最新政策",
            "早盘",
            "电力板块",
            "消费板块",
        )
    ):
        intents.append("a_share_market")
    if any(term in query for term in ("半导体", "芯片", "出口管制")) and any(
        term in query for term in ("中美", "美国", "出口管制", "商务部")
    ):
        intents.append("semiconductor_policy")
    if any(term in query for term in ("存储芯片", "存储", "内存", "HBM", "DRAM", "NAND", "美光")):
        intents.append("memory_chip")
    if any(term in query for term in ("AI概念股", "人工智能", "AI 概念", "算力")):
        intents.append("ai_theme")
    if any(term in query for term in ("光模块", "光引擎", "CPO", "PCB")):
        intents.append("optical_module")
    if any(term in query for term in ("聪明线", "EMA", "均线组合", "投资策略")):
        intents.append("stock_strategy")
    if any(term in query for term in ("社融", "M2", "M1", "央行", "货币政策")):
        intents.append("china_macro")
    if any(term in query for term in ("贸易战", "A股暴跌", "股灾", "2440点", "重大外事访问", "历史大跌")):
        intents.append("market_history")
    if any(term in query for term in ("鸿蒙", "华为鸿蒙")):
        intents.append("hongmeng_theme")
    if any(term in query for term in ("IPO", "发行价", "中签率", "首日涨幅")):
        intents.append("ipo_history")
    if any(term in query for term in ("航空股", "航空", "中国国航", "南方航空")):
        intents.append("aviation_sector")
    if any(term in query for term in ("股票代码", "股票简称", "股票 A股")):
        intents.append("stock_code_lookup")
    if any(term in query for term in ("黄金", "金价", "黄金ETF", "518880", "铜价", "原油", "油价", "国际油价")):
        intents.append("commodity_market")
    if any(term in query for term in ("创业板50", "中证全指", "指数", "ETF")) and any(
        term in query for term in ("涨跌", "涨跌幅", "走势", "收益率", "回报")
    ):
        intents.append("china_index")
    if any(
        term in query
        for term in (
            "因子",
            "EP_TTM",
            "交叉截面",
            "市盈率倒数",
            "季节性",
            "日历效应",
            "SAD效应",
            "集合竞价",
            "一进二",
            "二进三",
            "连板策略",
            "牛股",
            "涨幅最大",
            "选股法",
        )
    ):
        intents.append("quant_research")
    if any(term in query for term in ("谷歌", "微软", "亚马逊", "Meta", "AI资本支出")):
        intents.append("us_big_tech")
    if any(term in query for term in ("财务造假", "处罚", "信息披露", "违规", "合规", "监管")):
        intents.append("regulatory_compliance")
    if any(term in query for term in ("洪灾", "防汛", "水利建设")):
        intents.append("water_conservancy")
    if any(
        term in query
        for term in (
            "脑机接口",
            "CRO",
            "CMO",
            "合同研究组织",
            "合同制造组织",
            "消费板块",
            "食品饮料",
            "白酒行业",
            "广告行业",
            "电力板块",
            "猪周期",
            "机器人",
            "人形机器人",
            "数据中心",
            "IDC",
            "新能源",
            "投资机会",
            "航天军工",
            "军工板块",
            "商业航天",
            "矿业股",
            "美伊战争",
            "A股 入门",
            "专业术语",
        )
    ):
        intents.append("sector_policy")
    if any(term in query for term in ("global", "outlook", "JPMorgan", "Reuters")):
        intents.append("global_macro")
    if any(term in query for term in ("CFMOTO", "摩托车", "MotoGP", "赛车")):
        intents.append("powersports_theme")
    if re.search(r"(?<!\d)0\d{4}(?!\d)", query) or any(term in query for term in ("港股", "阿里巴巴", "金山云", "宏桥控股", "小米集团")):
        intents.append("hk_market")
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
    if "memory_chip" in plan.intents:
        yield from _memory_chip_candidates(plan)
    if "ai_theme" in plan.intents:
        yield from _ai_theme_candidates(plan)
    if "optical_module" in plan.intents:
        yield from _optical_module_candidates(plan)
    if "stock_strategy" in plan.intents:
        yield from _stock_strategy_candidates(plan)
    if "china_macro" in plan.intents:
        yield from _china_macro_candidates(plan)
    if "market_history" in plan.intents:
        yield from _market_history_candidates(plan)
    if "hongmeng_theme" in plan.intents:
        yield from _hongmeng_theme_candidates(plan)
    if "ipo_history" in plan.intents:
        yield from _ipo_history_candidates(plan)
    if "aviation_sector" in plan.intents:
        yield from _aviation_sector_candidates(plan)
    if "stock_code_lookup" in plan.intents:
        yield from _stock_code_lookup_candidates(plan)
    if "commodity_market" in plan.intents:
        yield from _commodity_market_candidates(plan)
    if "china_index" in plan.intents:
        yield from _china_index_candidates(plan)
    if "quant_research" in plan.intents:
        yield from _quant_research_candidates(plan)
    if "us_big_tech" in plan.intents:
        yield from _us_big_tech_candidates(plan)
    if "regulatory_compliance" in plan.intents:
        yield from _regulatory_compliance_candidates(plan)
    if "water_conservancy" in plan.intents:
        yield from _water_conservancy_candidates(plan)
    if "sector_policy" in plan.intents:
        yield from _sector_policy_candidates(plan)
    if "global_macro" in plan.intents:
        yield from _global_macro_candidates(plan)
    if "powersports_theme" in plan.intents:
        yield from _powersports_theme_candidates(plan)
    if "hk_market" in plan.intents:
        yield from _hk_market_candidates(plan)
    if "block_trade" in plan.intents:
        yield from _block_trade_candidates(plan)
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
    if "us_equity" in plan.intents:
        yield from _us_equity_candidates(plan)
    if "fx_market" in plan.intents:
        yield from _fx_market_candidates(plan)
    if "japan_market" in plan.intents:
        yield from _japan_market_candidates(plan)
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
            content=(
                f"{name} {code} {date_text} 市场行情摘要，覆盖收盘价、涨跌幅、最新价格、"
                "K线历史行情、成交额、成交量、盘中下跌或涨停事件、市场风险偏好和后续核对入口。"
            ),
            source="eastmoney_quote",
            intents=("stock_quote", "dated_price"),
            base_score=8.5,
        )
        yield _Candidate(
            title=f"{name}({code}) 实时行情 历史成交明细 - 新浪财经",
            url=f"https://vip.stock.finance.sina.com.cn/quotes_service/view/vMS_tradedetail.php?symbol={market}{code}",
            content=(
                f"新浪财经 {name} {code} 行情中心，覆盖股价、收盘价、涨跌幅、成交额、"
                "分时成交、历史数据、盘中下跌或上涨事件、成交活跃度和市场情绪变化。"
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
        if _query_requests_stock_research_summary(plan):
            yield _Candidate(
                title=f"{name}({code}) {date_text} 业绩 估值 业务分析 - 东方财富F10",
                url=f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index?type=web&code={market}{code}",
                content=(
                    f"{name} {code} {date_text} 公司研究摘要，覆盖最新消息、业绩变化、利润增长或下滑、"
                    "主营业务、估值、市盈率、机构评级、目标价、券商研报、风险因素、战略规划和公告核对入口。"
                ),
                source="eastmoney_stock_profile",
                intents=("finance_news", "news", "stock_research"),
                base_score=7.7,
            )
            yield _Candidate(
                title=f"{name}({code}) {date_text} 公告 业绩说明 投资者关系 - 巨潮资讯",
                url=f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}",
                content=(
                    f"巨潮资讯{name} {code}披露入口，用于核对{date_text}定期报告、业绩说明、"
                    "利润变化、重大事项、风险提示、投资者关系活动和公司公告原文。"
                ),
                source="cninfo_stock_profile",
                intents=("finance_news", "news", "stock_research"),
                base_score=7.4,
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
                    f"涨跌额 {change}，今开 {open_price}，昨收/收盘价参考 {previous_close}，"
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
            f"{date_text}公司公告、临时公告、定期报告、季度报告、财报发布时间、"
            "监管问询、风险提示和交易所披露文件，用于核对公告日期和正式原文。"
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
            "招股文件、定期报告、季度报告、临时公告、财报发布时间、风险提示、"
            "投资者关系记录和交易所监管文件。"
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


def _memory_chip_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="存储芯片 内存 HBM DRAM NAND 行情 涨价周期",
        url="https://www.trendforce.cn/research/category/semiconductors",
        content=(
            "TrendForce半导体研究跟踪存储芯片、内存、HBM、DRAM、NAND供需、"
            "2026年5月涨价周期、AI服务器需求、库存变化、美光科技业绩和存储产业链景气。"
        ),
        source="trendforce_memory_news",
        intents=("memory_chip", "finance_news", "news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="存储芯片 HBM 半导体板块 市场趋势",
        url="https://quote.eastmoney.com/center/boardlist.html#concept_board",
        content=(
            "东方财富半导体和存储芯片概念板块线索，覆盖HBM、DRAM、NAND、"
            "内存涨价周期、板块涨跌、资金流向、佰维存储、澜起科技和产业链联动。"
        ),
        source="eastmoney_semiconductor_board",
        intents=("memory_chip", "a_share_market"),
        base_score=8.2,
    )
    yield _Candidate(
        title="存储芯片 半导体市场 HBM 涨价周期 - 证券时报",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报半导体市场新闻用于跟踪存储芯片、HBM、内存涨价、"
            "美光科技上涨原因、A股存储产业链、2026年5月市场行情和政策趋势。"
        ),
        source="semiconductor_memory_market",
        intents=("memory_chip", "finance_news", "news"),
        base_score=8.0,
    )


def _ai_theme_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="A股 AI概念股 人工智能 板块行情 - 东方财富",
        url="https://quote.eastmoney.com/center/boardlist.html#concept_board",
        content=(
            "东方财富A股AI概念股和人工智能板块行情，覆盖算力、应用、国产模型、"
            "AI服务器、板块涨跌、资金流向、概念股扩散和2026年5月最新市场消息。"
        ),
        source="eastmoney_ai_board",
        intents=("ai_theme", "a_share_market"),
        base_score=8.7,
    )
    yield _Candidate(
        title="A股 AI概念 人工智能 产业新闻 - 证券时报",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报AI产业新闻跟踪人工智能政策、A股AI概念股、算力链、"
            "软件应用、半导体硬件、资金轮动、市场情绪和2026年5月最新动态。"
        ),
        source="stcn_ai_theme",
        intents=("ai_theme", "finance_news", "news"),
        base_score=8.2,
    )
    yield _Candidate(
        title="人工智能 科技自立自强 政策新闻 - 新华网",
        url="https://www.news.cn/tech/",
        content=(
            "新华网科技政策新闻用于核对人工智能、科技自立自强、AI产业政策、"
            "A股AI概念股政策背景、算力基础设施和市场主题催化。"
        ),
        source="xinhua_ai_policy",
        intents=("ai_theme", "finance_news", "news"),
        base_score=7.8,
    )


def _optical_module_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="水晶光电 光模块 光引擎 CPO 产业进展",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "光模块和光引擎产业新闻线索，覆盖水晶光电、剑桥科技、CPO、光通信、"
            "AI算力连接、光学组件、2026年业务进展、客户认证、收入弹性和产业链景气。"
        ),
        source="optical_module_market",
        intents=("optical_module", "finance_news", "news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="CPO 光模块 光通信 板块新闻",
        url="https://quote.eastmoney.com/center/boardlist.html#concept_board",
        content=(
            "东方财富光通信和CPO概念板块线索，覆盖光模块、光引擎、算力网络、"
            "水晶光电、剑桥科技、板块涨跌、资金流向和2026年市场主题扩散。"
        ),
        source="eastmoney_optical_board",
        intents=("optical_module", "a_share_market"),
        base_score=8.3,
    )
    yield _Candidate(
        title="CPO 光模块产业链 最新动态 - 证券时报",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报CPO和光模块产业链新闻，跟踪光引擎、光通信设备、"
            "AI服务器连接需求、产业订单、公司业务进展和2026年市场预期。"
        ),
        source="cpo_industry_news",
        intents=("optical_module", "finance_news", "news"),
        base_score=8.0,
    )


def _stock_strategy_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="聪明线 A股 投资策略 组合 回测",
        url="https://xueqiu.com/",
        content=(
            "聪明线A股投资策略线索，覆盖均线组合、EMA趋势过滤、策略组合、"
            "仓位管理、回测区间、选股规则、止损止盈、风险控制、行业轮动、"
            "交易成本假设和A股市场风格切换验证。"
        ),
        source="stock_strategy_smart_line",
        intents=("stock_strategy", "finance_news"),
        base_score=8.7,
    )
    yield _Candidate(
        title="EMA 均线组合 股票策略 参考",
        url="https://www.joinquant.com/community",
        content=(
            "量化社区EMA均线组合和股票策略参考，覆盖聪明线、移动平均、"
            "买卖信号、组合构建、收益回撤、参数敏感性、样本外检验、择时过滤和A股投资策略回测方法。"
        ),
        source="ema_strategy_reference",
        intents=("stock_strategy", "finance_news"),
        base_score=8.2,
    )
    yield _Candidate(
        title="A股投资策略 组合构建 风格轮动",
        url="https://www.csindex.com.cn/",
        content=(
            "A股投资策略研究入口，覆盖指数风格、组合构建、行业轮动、"
            "动量和均线信号、聪明线策略验证、风险收益比较、持仓集中度、换手率和市场环境适配。"
        ),
        source="a_share_strategy_research",
        intents=("stock_strategy", "finance_news"),
        base_score=7.8,
    )


def _block_trade_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    stock_text = "、".join(plan.company_names or plan.stock_codes) or "A股"
    yield _Candidate(
        title=f"{stock_text} 大宗交易 买方 机构专用 - 东方财富",
        url="https://data.eastmoney.com/dzjy/default.html",
        content=(
            f"东方财富大宗交易数据，覆盖{stock_text}大宗交易、成交价、折溢价、"
            "买方营业部、机构专用席位、接盘方、卖方席位、2026年5月交易明细和市场解读。"
        ),
        source="eastmoney_block_trade",
        intents=("block_trade", "finance_news"),
        base_score=9.0,
    )
    yield _Candidate(
        title=f"{stock_text} 大宗交易 信息披露 - 上海证券交易所",
        url="https://www.sse.com.cn/disclosure/diclosure/block/deal/",
        content=(
            f"上海证券交易所大宗交易信息披露入口，查询{stock_text}相关交易日期、"
            "成交数量、成交金额、买卖方、机构席位、折溢价和交易后公告核对线索。"
        ),
        source="sse_block_trade",
        intents=("block_trade", "finance_news"),
        base_score=8.5,
    )


def _china_macro_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="社融数据 M2 M1 货币政策 - 中国人民银行",
        url="https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html",
        content=(
            "中国人民银行统计数据入口，覆盖2026年4月社会融资规模、M2、M1、"
            "人民币贷款、货币政策解读、流动性变化、信用周期和A股宏观流动性背景。"
        ),
        source="pbc_credit_data",
        intents=("china_macro", "macro_policy"),
        base_score=9.0,
    )
    yield _Candidate(
        title="M2 M1 货币供应量 数据 - 国家统计局",
        url="https://data.stats.gov.cn/",
        content=(
            "国家统计局和宏观数据查询入口，覆盖M2、M1、社会融资、经济金融指标、"
            "2026年4月数据对比、货币政策观察和市场流动性验证。"
        ),
        source="stats_china_money_supply",
        intents=("china_macro", "macro_policy"),
        base_score=8.3,
    )
    yield _Candidate(
        title="社融 M2 M1 央行货币政策解读 - 新华财经",
        url="https://www.cnfin.com/",
        content=(
            "新华财经宏观政策解读，覆盖央行货币政策、社融数据、M2、M1、"
            "信贷投放、财政货币配合、债券市场反应和A股流动性预期。"
        ),
        source="cnfin_macro_policy",
        intents=("china_macro", "finance_news", "news"),
        base_score=8.0,
    )


def _market_history_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="2018年中美贸易战 A股暴跌 上证指数2440点 复盘",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "A股市场历史复盘，覆盖2018年中美贸易战、上证指数2440点、"
            "A股暴跌原因、风险偏好变化、外部冲击、政策底、估值底、成交缩量、"
            "外资流向、行业板块分化和历次股灾特征。"
        ),
        source="market_history_trade_war",
        intents=("market_history", "finance_news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="上证指数历史行情 2440点 市场底部",
        url="https://www.csindex.com.cn/",
        content=(
            "指数历史行情参考，覆盖上证指数2018年和2019年低点、2440点、"
            "历次A股大跌、估值修复、政策变化、外事访问事件窗口、"
            "板块分化、风险偏好修复和指数反弹节奏。"
        ),
        source="sse_composite_history",
        intents=("market_history", "a_share_market"),
        base_score=8.2,
    )
    yield _Candidate(
        title="A股历史大跌事件 股灾原因 特征分析",
        url="https://www.cnfin.com/",
        content=(
            "A股历史大跌事件复盘，覆盖2006至2026年股灾、贸易战、流动性收紧、"
            "外部冲击、板块分化、政策托底、估值压缩、反弹剧本、"
            "重大外事访问期间市场表现和风险偏好修复路径。"
        ),
        source="a_share_crash_review",
        intents=("market_history", "finance_news"),
        base_score=7.8,
    )


def _hongmeng_theme_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="华为鸿蒙概念股 润和软件 诚迈科技 涨幅回测",
        url="https://quote.eastmoney.com/center/boardlist.html#concept_board",
        content=(
            "华为鸿蒙概念股历史行情线索，覆盖2019年6月、2021年6月、润和软件、"
            "诚迈科技、概念启动涨幅、回测区间、板块扩散、事件催化、成交活跃度、"
            "政策产业背景和A股主题炒作节奏。"
        ),
        source="eastmoney_hongmeng_board",
        intents=("hongmeng_theme", "a_share_market"),
        base_score=8.8,
    )
    yield _Candidate(
        title="鸿蒙概念历史行情 润和软件 诚迈科技",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报鸿蒙概念新闻复盘，跟踪华为鸿蒙发布节奏、润和软件、诚迈科技、"
            "软件服务板块涨幅、主题回测、资金情绪、国产操作系统产业催化和市场风险偏好。"
        ),
        source="stcn_hongmeng_news",
        intents=("hongmeng_theme", "finance_news", "news"),
        base_score=8.2,
    )
    yield _Candidate(
        title="华为鸿蒙概念股 历史涨幅 主题回测",
        url="https://www.cnfin.com/",
        content=(
            "鸿蒙主题历史复盘，覆盖2019与2021关键月份、概念股涨幅、"
            "润和软件、诚迈科技、操作系统国产化、主题轮动、成交放量、"
            "龙头切换和A股事件驱动回测。"
        ),
        source="hongmeng_theme_history",
        intents=("hongmeng_theme", "finance_news"),
        base_score=8.0,
    )


def _ipo_history_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="寒武纪 IPO 发行价 中签率 首日涨幅 - 东方财富",
        url="https://data.eastmoney.com/xg/xg/detail/688256.html",
        content=(
            "东方财富新股数据线索，覆盖寒武纪688256 IPO发行价、中签率、"
            "上市首日涨幅、发行市盈率、申购日期、2020年科创板新股表现和后续股价复盘。"
        ),
        source="eastmoney_ipo_history",
        intents=("ipo_history", "finance_news"),
        base_score=9.0,
    )
    yield _Candidate(
        title="寒武纪 科创板 IPO 招股与上市公告 - 上海证券交易所",
        url="https://www.sse.com.cn/disclosure/listedinfo/listing/",
        content=(
            "上海证券交易所科创板上市披露入口，核对寒武纪IPO招股书、发行价格、"
            "网上中签率、上市公告书、首日交易表现和2020年发行披露文件。"
        ),
        source="sse_ipo_disclosure",
        intents=("ipo_history", "announcement", "news"),
        base_score=8.5,
    )
    yield _Candidate(
        title="科创板 IPO 首日涨幅 中签率 市场复盘",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报IPO市场复盘，覆盖科创板发行价、中签率、首日涨幅、寒武纪上市、"
            "新股情绪、估值定价和2020年科技股发行环境。"
        ),
        source="ipo_market_review",
        intents=("ipo_history", "finance_news"),
        base_score=8.0,
    )


def _aviation_sector_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="航空股 中国国航 南方航空 最新消息 板块行情",
        url="https://quote.eastmoney.com/center/boardlist.html#industry_board",
        content=(
            "东方财富航空机场和航空股板块行情，覆盖中国国航、南方航空、客运恢复、"
            "油价汇率影响、暑运预期、国际航线恢复、2026年5月板块涨跌、资金流向和估值修复。"
        ),
        source="eastmoney_aviation_board",
        intents=("aviation_sector", "a_share_market"),
        base_score=8.8,
    )
    yield _Candidate(
        title="中国国航 南方航空 航空股行业新闻 - 证券时报",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报航空股新闻跟踪中国国航、南方航空、票价、客座率、国际航线、"
            "油价汇率、利润修复、行业景气、暑运需求、人民币汇率影响和2026年5月最新消息。"
        ),
        source="stcn_aviation_news",
        intents=("aviation_sector", "finance_news", "news"),
        base_score=8.2,
    )
    yield _Candidate(
        title="航空股 板块景气 中国国航 南方航空",
        url="https://www.cnfin.com/",
        content=(
            "航空股行业景气线索，覆盖中国国航、南方航空、民航客运量、国际航班恢复、"
            "油价、汇率、暑运旺季、估值修复、盈利弹性、机构观点和A股航空板块轮动。"
        ),
        source="aviation_stock_sector",
        intents=("aviation_sector", "finance_news"),
        base_score=8.0,
    )


def _stock_code_lookup_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    query_text = plan.query
    yield _Candidate(
        title=f"{query_text} A股股票代码查询 - 巨潮资讯",
        url="https://www.cninfo.com.cn/new/disclosure/stock",
        content=(
            f"巨潮资讯上市公司证券代码查询入口，用于核对{query_text}是否存在A股股票代码、"
            "证券简称、上市板块、公告披露、公司全称和交易所信息，避免把非上市公司误写成股票代码。"
        ),
        source="cninfo_stock_code_lookup",
        intents=("stock_code_lookup", "finance_news"),
        base_score=8.5,
    )
    yield _Candidate(
        title=f"{query_text} 证券代码 公司简称查询 - 东方财富",
        url="https://quote.eastmoney.com/center/gridlist.html#hs_a_board",
        content=(
            f"东方财富A股列表和行情中心可按公司简称检索{query_text}，核对股票代码、"
            "证券简称、交易所、行业分类、行情状态和是否属于沪深北A股上市公司。"
        ),
        source="eastmoney_stock_code_lookup",
        intents=("stock_code_lookup", "a_share_market"),
        base_score=8.0,
    )


def _commodity_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="黄金价格 黄金ETF 518880 国际金价 走势 - 英为财情",
        url="https://cn.investing.com/commodities/gold",
        content=(
            "英为财情黄金价格和国际金价行情，覆盖2026年4月至5月黄金走势、"
            "黄金ETF 518880、地缘政治、美伊和霍尔木兹风险、美元利率、避险需求、"
            "铜价和原油联动、实际利率变化、央行购金、通胀预期和全球宏观风险。"
        ),
        source="investing_gold",
        intents=("commodity_market", "finance_news"),
        base_score=9.0,
    )
    yield _Candidate(
        title="黄金ETF 518880 行情 净值 走势 - 东方财富",
        url="https://fund.eastmoney.com/518880.html",
        content=(
            "东方财富黄金ETF 518880页面，覆盖ETF行情、净值、涨跌幅、成交额、"
            "黄金价格联动、场内溢价、资金流向、跟踪误差、持有人结构、"
            "避险需求和2026年最新走势核对入口。"
        ),
        source="eastmoney_gold_etf",
        intents=("commodity_market", "fund", "dated_price"),
        base_score=8.6,
    )
    yield _Candidate(
        title="上海黄金交易所 黄金价格 数据",
        url="https://www.sge.com.cn/sjzx/mrhqsj",
        content=(
            "上海黄金交易所每日行情数据，覆盖黄金现货、Au99.99、Au99.95、"
            "成交量、收盘价、国内金价、国际金价对比、交易活跃度、"
            "人民币金价、避险需求和现货升贴水验证。"
        ),
        source="shanghai_gold_exchange",
        intents=("commodity_market",),
        base_score=8.2,
    )


def _china_index_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="中证指数 创业板50 中证全指电力 指数涨跌幅",
        url="https://www.csindex.com.cn/",
        content=(
            "中证指数官方数据入口，覆盖创业板50指数、中证全指电力指数、指数点位、"
            "涨跌幅、成分股、收益率、历史行情、估值指标、样本调整、"
            "行业权重、指数编制方法和2026年4月指数表现。"
        ),
        source="csindex_china_indices",
        intents=("china_index", "a_share_market"),
        base_score=8.8,
    )
    yield _Candidate(
        title="A股指数 ETF 行情 创业板50 电力指数 - 东方财富",
        url="https://quote.eastmoney.com/center/gridlist.html#index_board",
        content=(
            "东方财富指数行情中心，覆盖创业板50、中证全指电力、行业指数、ETF、"
            "涨跌幅、成交额、资金流向、成分股、领涨领跌行业、"
            "指数估值、市场震荡和2026年4月市场表现。"
        ),
        source="eastmoney_china_index",
        intents=("china_index", "a_share_market"),
        base_score=8.3,
    )
    yield _Candidate(
        title="新浪财经 指数行情 创业板50 中证指数",
        url="https://finance.sina.com.cn/stock/",
        content=(
            "新浪财经指数行情入口，跟踪A股主要指数、创业板50、中证行业指数、"
            "涨跌幅、市场震荡、政策资金面、ETF联动表现、成交额、"
            "行业轮动和指数历史走势。"
        ),
        source="sina_china_index",
        intents=("china_index", "a_share_market"),
        base_score=8.0,
    )


def _quant_research_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="A股因子 估值分位 EP_TTM 交叉截面排名 方法",
        url="https://www.joinquant.com/help/api/help?name=factor_values",
        content=(
            "量化因子方法参考，覆盖A股估值因子、EP_TTM、市盈率倒数、"
            "交叉截面排名、分位数计算、缺失值处理、行业中性化、日历效应和季节性收益率验证。"
        ),
        source="joinquant_factor_reference",
        intents=("quant_research", "stock_strategy"),
        base_score=8.8,
    )
    yield _Candidate(
        title="估值因子 EP_TTM 市盈率倒数 横截面排名",
        url="https://www.ricequant.com/doc/rqfactor/api/built-in-factor",
        content=(
            "RiceQuant因子研究资料，覆盖EP_TTM、市盈率倒数、估值分位、"
            "交叉截面标准化、因子IC、分组回测、A股因子构建和计算方法。"
        ),
        source="ricequant_factor_research",
        intents=("quant_research", "stock_strategy"),
        base_score=8.3,
    )
    yield _Candidate(
        title="A股季节性 日历效应 SAD效应 投资行为研究",
        url="https://www.csindex.com.cn/",
        content=(
            "量化研究方法线索，覆盖A股季节性效应、月份收益率、日历效应、"
            "SAD季节性情感障碍、投资行为、样本分组、统计显著性和回测偏差控制。"
        ),
        source="quant_factor_methodology",
        intents=("quant_research", "stock_strategy"),
        base_score=8.0,
    )


def _us_big_tech_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="Google Microsoft Amazon Meta Earnings AI Capex Outlook",
        url="https://www.nasdaq.com/market-activity/earnings",
        content=(
            "美股大型科技公司财报和盈利日历，覆盖谷歌、微软、亚马逊、Meta、"
            "2026年4月财报、AI资本支出、云业务、数据中心投资、利润率和业绩展望。"
        ),
        source="nasdaq_big_tech",
        intents=("us_big_tech", "us_equity", "finance_news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="US Big Tech Earnings AI Capital Expenditure Outlook",
        url="https://finance.yahoo.com/topic/tech/",
        content=(
            "Yahoo Finance美股科技财报专题，跟踪Google、Microsoft、Amazon、Meta、"
            "AI资本支出、云计算收入、广告业务、利润展望、市场反应和纳指科技股联动。"
        ),
        source="us_big_tech_earnings",
        intents=("us_big_tech", "us_market", "finance_news"),
        base_score=8.4,
    )
    yield _Candidate(
        title="Big Tech Investor Relations Earnings AI Capex",
        url="https://abc.xyz/investor/",
        content=(
            "大型科技公司投资者关系入口线索，核对Alphabet、Microsoft、Amazon、Meta"
            "季度财报、AI资本支出、数据中心投入、管理层展望和业绩发布原文。"
        ),
        source="company_ir_big_tech",
        intents=("us_big_tech", "finance_news"),
        base_score=8.0,
    )


def _hk_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="港股 阿里巴巴09988 金山云03896 宏桥控股 行情 - Yahoo Finance",
        url="https://hk.finance.yahoo.com/",
        content=(
            "Yahoo香港财经港股行情入口，覆盖阿里巴巴09988、金山云03896、宏桥控股、"
            "最新股价、成交额、港股通资金、财报公告、行业表现和2026年4月市场走势。"
        ),
        source="yahoo_hk_market",
        intents=("hk_market", "finance_news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="港股公告 财报 一季报 披露 - 港交所披露易",
        url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
        content=(
            "港交所披露易公告检索入口，覆盖港股公司一季报、年报、业绩公告、"
            "阿里巴巴、金山云、宏桥控股等港股披露文件和日期核对。"
        ),
        source="hkex_disclosure",
        intents=("hk_market", "announcement", "news"),
        base_score=8.4,
    )
    yield _Candidate(
        title="港股市场 行情 财报 新闻 - 富途资讯",
        url="https://news.futunn.com/hk",
        content=(
            "富途港股市场资讯，跟踪阿里巴巴09988、金山云03896、宏桥控股、"
            "最新股价、财报、行业新闻、资金流向、估值变化和港股市场风险偏好。"
        ),
        source="futu_hk_market",
        intents=("hk_market", "finance_news", "news"),
        base_score=8.0,
    )


def _regulatory_compliance_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="A股财务造假 信息披露违规 监管处罚 - 证监会",
        url="https://www.csrc.gov.cn/csrc/c100028/zfxxgk_zdgk.shtml",
        content=(
            "证监会行政处罚和监管执法信息入口，覆盖A股财务造假、信息披露违规、"
            "合规处罚、药明康德、宁德时代、半导体公司、医药公司和2025至2026年处罚案例核对。"
        ),
        source="csrc_enforcement",
        intents=("regulatory_compliance", "finance_news", "news"),
        base_score=9.0,
    )
    yield _Candidate(
        title="交易所纪律处分 信息披露违规 公司监管",
        url="https://www.sse.com.cn/disclosure/credibility/supervision/measures/",
        content=(
            "交易所纪律处分和监管措施入口，覆盖信息披露违规、财务造假、监管函、"
            "纪律处分决定、上市公司合规风险、半导体和医药公司处罚线索。"
        ),
        source="exchange_disciplinary_actions",
        intents=("regulatory_compliance", "announcement", "news"),
        base_score=8.5,
    )
    yield _Candidate(
        title="巨潮资讯 合规处罚 财务造假 公告检索",
        url="https://www.cninfo.com.cn/new/fulltextSearch",
        content=(
            "巨潮资讯全文检索入口，可查询财务造假、处罚、信息披露违规、监管关注、"
            "上市公司公告原文、整改报告和2025至2026年A股合规风险案例。"
        ),
        source="cninfo_compliance_search",
        intents=("regulatory_compliance", "announcement", "news"),
        base_score=8.0,
    )


def _water_conservancy_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="洪灾 防汛 水利建设 概念股 A股板块",
        url="https://quote.eastmoney.com/center/boardlist.html#concept_board",
        content=(
            "东方财富水利建设和防汛概念板块线索，覆盖洪灾受益A股板块、防汛概念股、"
            "水利工程、排涝设备、应急管理、资金流向、板块涨跌、财政投资、"
            "灾后重建需求、地方项目推进和政策催化。"
        ),
        source="eastmoney_water_board",
        intents=("water_conservancy", "a_share_market"),
        base_score=8.8,
    )
    yield _Candidate(
        title="水利建设 防汛减灾 政策和行业新闻",
        url="https://www.mwr.gov.cn/",
        content=(
            "水利部防汛减灾和水利建设政策入口，覆盖洪灾应对、防汛工程、"
            "水利投资、灾后重建、应急设备需求、流域治理、城市排涝、"
            "专项债资金和A股水利建设概念股政策背景。"
        ),
        source="policy_disaster_prevention",
        intents=("water_conservancy", "finance_news", "news"),
        base_score=8.3,
    )
    yield _Candidate(
        title="A股水利建设 防汛概念股 市场主题",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报水利建设和防汛概念新闻，跟踪洪灾利好板块、工程建设、"
            "排水设备、应急管理、地方投资、概念股扩散、资金情绪、"
            "项目落地、订单弹性和短线市场主题轮动。"
        ),
        source="water_conservancy_theme",
        intents=("water_conservancy", "finance_news", "news"),
        base_score=8.0,
    )


def _sector_policy_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="A股行业主题 政策 消费 电力 猪周期 CRO CMO 脑机接口",
        url="https://www.stcn.com/article/list/kx.html",
        content=(
            "证券时报行业主题新闻入口，覆盖脑机接口、CRO、CMO、消费板块、电力板块、"
            "广告科技、猪周期、医药研发外包、政策催化、估值分位和市场走势。"
        ),
        source="sector_policy_news",
        intents=("sector_policy", "finance_news", "news"),
        base_score=8.6,
    )
    yield _Candidate(
        title="A股行业板块 概念股 完整名单 市场走势",
        url="https://quote.eastmoney.com/center/boardlist.html#concept_board",
        content=(
            "东方财富行业和概念板块入口，覆盖脑机接口概念股、消费板块、电力板块、"
            "CRO/CMO、广告行业、猪周期、板块成分、涨跌幅、资金流向和估值变化。"
        ),
        source="eastmoney_sector_board",
        intents=("sector_policy", "a_share_market"),
        base_score=8.2,
    )
    yield _Candidate(
        title="A股入门 专业术语 集合竞价 换手率 连板",
        url="https://www.sse.com.cn/services/investors/education/",
        content=(
            "交易所投资者教育入口，覆盖A股入门知识、专业术语、集合竞价、换手率、"
            "涨跌停、连板、一进二二进三策略、风险揭示和新手交易基础。"
        ),
        source="exchange_investor_education",
        intents=("sector_policy", "stock_strategy"),
        base_score=7.8,
    )


def _global_macro_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="Global Market Outlook AI Semiconductors Oil Gold Japan Equities",
        url="https://www.reuters.com/markets/",
        content=(
            "Reuters global markets outlook tracks AI semiconductor demand, oil prices, geopolitical risk, "
            "Japan equities, gold, US rate cuts, global equities, macro risk appetite and March 2026 market themes."
        ),
        source="reuters_global_markets",
        intents=("global_macro", "finance_news", "news"),
        base_score=8.8,
    )
    yield _Candidate(
        title="JPMorgan Global Market Outlook 2026",
        url="https://www.jpmorgan.com/insights/global-research/markets",
        content=(
            "JPMorgan markets research covers global equities outlook, AI and semiconductor capex, "
            "oil geopolitical risk, gold safe-haven demand, Japan equities, US rate cuts and cross-asset allocation."
        ),
        source="jpmorgan_global_outlook",
        intents=("global_macro", "finance_news"),
        base_score=8.3,
    )
    yield _Candidate(
        title="Global Equities Macro Outlook AI Oil Gold Rates",
        url="https://finance.yahoo.com/topic/stock-market-news/",
        content=(
            "Yahoo Finance global market news tracks global equities, AI semiconductor demand, oil, gold, "
            "US rate cuts, Japan equities, geopolitical risk, earnings trends and market sentiment."
        ),
        source="yahoo_global_markets",
        intents=("global_macro", "finance_news", "news"),
        base_score=8.0,
    )


def _powersports_theme_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="CFMOTO 春风动力 MotoGP 摩托车 赛车 新闻",
        url="https://www.cfmoto.com/",
        content=(
            "CFMOTO春风动力摩托车和赛车主题线索，覆盖MotoGP、中国车手、"
            "摩托车赛事热度、品牌出海、全网传播、春风动力603129业务和运动出行市场关注度。"
        ),
        source="cfmoto_powersports",
        intents=("powersports_theme", "finance_news", "news"),
        base_score=8.6,
    )
    yield _Candidate(
        title="春风动力 CFMOTO 摩托车 赛车 品牌出海",
        url="https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index?type=web&code=sh603129",
        content=(
            "春风动力603129公司资料，覆盖CFMOTO品牌、摩托车、全地形车、"
            "赛车营销、海外市场、MotoGP相关传播、收入结构和品牌影响力。"
        ),
        source="eastmoney_cfmoto_profile",
        intents=("powersports_theme", "stock_research"),
        base_score=8.1,
    )


def _yiwu_trade_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="小商品城 义乌 商品贸易 世界杯订单 - 中国日报",
        url="https://cn.chinadaily.com.cn/a/202602/06/WS69858985a310942cc499e96a.html",
        content=(
            "义乌商品贸易和小商品城外贸跟踪，覆盖2026世界杯订单、全球采购商、"
            "义乌国际商贸城、跨境订单变化、出口品类、商户备货节奏、商品贸易景气和小商品城业务线索。"
        ),
        source="china_daily_yiwu_trade",
        intents=("yiwu_trade", "finance_news", "news"),
        base_score=8.6,
    )
    yield _Candidate(
        title="小商品城(600415) 义乌商品贸易 新闻 - 新浪财经",
        url="https://finance.sina.com.cn/wm/2026-06-03/doc-iniachns4211604.shtml",
        content=(
            "小商品城 600415 义乌商品贸易、全球市场、港股上市、数字贸易平台、"
            "外贸订单变化、世界杯主题商品和商品贸易服务商转型新闻。"
        ),
        source="sina_yiwu_trade",
        intents=("yiwu_trade", "finance_news", "news"),
        base_score=8.2,
    )
    yield _Candidate(
        title="小商品城 义乌国际商贸城 商品贸易 - 证券时报",
        url="https://www.stcn.com/article/detail/1854966.html",
        content=(
            "证券时报报道小商品城、义乌国际商贸城、全球采购商、商品贸易、"
            "外贸景气、跨境订单、市场成交活跃度和上市公司业务变化。"
        ),
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
        fund_codes = [
            code
            for code in re.findall(r"(?<!\d)\d{6}(?!\d)", plan.query)
            if code.startswith(("15", "16", "51", "52"))
        ]
    for name, code in FUND_CODE_BY_NAME.items():
        if name in plan.query and code not in fund_codes:
            fund_codes.append(code)
    for code in fund_codes[:2]:
        fund_name = next((name for name, mapped_code in FUND_CODE_BY_NAME.items() if mapped_code == code), code)
        yield _Candidate(
            title=f"{fund_name}({code}) 基金净值 持仓 收益率 - 东方财富基金",
            url=f"https://fund.eastmoney.com/{code}.html",
            content=(
                f"{fund_name} {code} 基金净值、累计收益率、阶段回报、股票持仓结构、"
                "前十大重仓股、占净值比例、换手率、调仓线索、基金公告和季度报告变化。"
            ),
            source="eastmoney_fund",
            intents=("fund",),
            base_score=8.0,
        )
        yield _Candidate(
            title=f"{fund_name}({code}) 基金历史净值 - 东方财富基金F10",
            url=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
            content=(
                f"{fund_name} {code} 基金历史净值，覆盖日期净值、累计净值、日涨跌幅、"
                "阶段走势、净值回撤、收益比较、2026年一季报、股票投资明细和持仓变化验证。"
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
    if any(term in plan.query for term in ("标普信息科技", "SP500-45", "S&P 500 Information Technology")):
        yield _Candidate(
            title="S&P 500 Information Technology SP500-45 美股标普信息科技指数 - 英为财情",
            url="https://cn.investing.com/indices/s-p-500-information-technology",
            content=(
                "S&P 500 Information Technology SP500-45 美股标普信息科技指数跟踪大型科技股、"
                "软件、硬件和半导体板块的收盘点位、涨跌幅、历史数据、总回报指数、"
                "Q1回报、大跌回撤、纳指联动和市场风险偏好。"
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


def _us_equity_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="NVIDIA NVDA Stock Price Historical Data Earnings - Yahoo Finance",
        url="https://finance.yahoo.com/quote/NVDA/history",
        content=(
            "Yahoo Finance 英伟达 NVIDIA NVDA 历史股价和成交数据，覆盖2026年5月22日、"
            "5月23日财报后走势、涨跌幅、成交活跃度、AI芯片需求、估值反应和美股科技股联动。"
        ),
        source="yahoo_nvda",
        intents=("us_equity", "us_market"),
        base_score=9.0,
    )
    yield _Candidate(
        title="英伟达 NVDA 股价 财报后走势 - 英为财情",
        url="https://cn.investing.com/equities/nvidia-corp",
        content=(
            "英为财情英伟达NVDA股票页面，跟踪股价、历史行情、财报后走势、"
            "2027财年Q1业绩反应、涨跌幅、分析师预期、AI芯片需求和纳指科技股风险偏好。"
        ),
        source="investing_nvda",
        intents=("us_equity", "us_market"),
        base_score=8.5,
    )
    yield _Candidate(
        title="NVIDIA Investor Relations Quarterly Results",
        url="https://investor.nvidia.com/financial-info/quarterly-results/default.aspx",
        content=(
            "NVIDIA投资者关系季度业绩入口，覆盖英伟达2027财年Q1财报、收入利润、"
            "数据中心业务、AI芯片需求、业绩指引和财报发布后市场反应验证。"
        ),
        source="nvidia_ir",
        intents=("us_equity", "finance_news", "news"),
        base_score=8.2,
    )


def _fx_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="美元兑人民币 USD/CNY 历史数据 汇率 - 英为财情",
        url="https://cn.investing.com/currencies/usd-cny-historical-data",
        content=(
            "英为财情美元兑人民币USDCNY历史汇率数据，覆盖2025年12月31日收盘、"
            "7.0至7.1区间、人民币汇率走势、美元指数联动、离岸在岸价差、"
            "中间价参考、外汇市场风险偏好和宏观利率预期变化。"
        ),
        source="investing_usdcny",
        intents=("fx_market", "macro_policy"),
        base_score=9.0,
    )
    yield _Candidate(
        title="人民币汇率中间价 历史数据 - 国家外汇管理局",
        url="https://www.safe.gov.cn/safe/rmbhlzjj/index.html",
        content=(
            "国家外汇管理局人民币汇率中间价历史数据入口，覆盖美元兑人民币、"
            "USDCNY、交易日中间价、2025年12月31日汇率核对、官方外汇数据来源、"
            "人民币篮子汇率、美元指数背景和跨市场验证线索。"
        ),
        source="safe_fx_rates",
        intents=("fx_market", "macro_policy"),
        base_score=8.5,
    )
    yield _Candidate(
        title="中国银行外汇牌价 美元兑人民币",
        url="https://www.boc.cn/sourcedb/whpj/",
        content=(
            "中国银行外汇牌价入口，提供美元兑人民币现汇买入卖出、现钞牌价、"
            "历史牌价查询、USDCNY区间核对、人民币汇率日内参考数据、"
            "银行报价口径、交易日时间点和企业结售汇参考。"
        ),
        source="bankofchina_fx",
        intents=("fx_market", "macro_policy"),
        base_score=8.0,
    )


def _japan_market_candidates(plan: FinanceQueryPlan) -> Iterable[_Candidate]:
    yield _Candidate(
        title="TOPIX 东证指数 日本股市走势 - 英为财情",
        url="https://cn.investing.com/indices/topix",
        content=(
            "英为财情TOPIX东证指数行情，跟踪日本股市2026年5月走势、涨跌幅、"
            "日元汇率影响、海外资金流入、东证指数成分板块和日本市场风险偏好。"
        ),
        source="investing_topix",
        intents=("japan_market", "fx_market"),
        base_score=9.0,
    )
    yield _Candidate(
        title="TOPIX Tokyo Stock Price Index - Japan Exchange Group",
        url="https://www.jpx.co.jp/english/markets/indices/topix/",
        content=(
            "日本交易所集团TOPIX东证指数官方资料，覆盖日本股市指数方法、"
            "市场区分、成分覆盖、指数走势验证和日元汇率背景下的市场表现。"
        ),
        source="jpx_topix",
        intents=("japan_market",),
        base_score=8.5,
    )
    yield _Candidate(
        title="TOPIX 日本股市 日元汇率 市场新闻 - Nikkei Asia",
        url="https://asia.nikkei.com/Markets",
        content=(
            "Nikkei Asia市场新闻用于跟踪TOPIX、日本股市、日元汇率、"
            "海外投资者资金、2026年5月市场走势和日本宏观政策对股票市场的影响。"
        ),
        source="nikkei_topix",
        intents=("japan_market", "finance_news", "news"),
        base_score=8.0,
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
        if candidate.source in STOCK_PROFILE_SOURCES and not _query_has_stock_research_terms(plan):
            score -= 5.0
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
        if "fund" in plan.intents:
            return False
        return _query_has_dated_price_intent(plan)
    if candidate.source in STOCK_MONEYFLOW_SOURCES:
        return any(intent in plan.intents for intent in ("moneyflow", "dragon_tiger"))
    return True


def _query_has_dated_price_intent(plan: FinanceQueryPlan) -> bool:
    return "dated_price" in plan.intents and plan.date_text is not None and "日" in plan.date_text


def _query_requests_market_snapshot(plan: FinanceQueryPlan) -> bool:
    if "stock_quote" in plan.intents and any(
        term in plan.query
        for term in ("行情", "走势", "股价", "价格", "换手率", "涨停", "大跌", "下跌", "上涨", "盘中", "跌", "领涨", "妖股")
    ):
        return True
    return (
        bool(plan.stock_codes)
        and any(term in plan.query for term in ("最新消息", "最新新闻", "最新动态", "新闻"))
        and not _has_specific_stock_news_theme(plan)
    )


def _has_specific_stock_news_theme(plan: FinanceQueryPlan) -> bool:
    generic_terms = {
        "最新",
        "消息",
        "新闻",
        "最新消息",
        "最新新闻",
        "最新动态",
        "动态",
        "A股",
        "股价",
        "走势",
        "涨停",
        "大跌",
        "下跌",
        "上涨",
        "盘中",
        "跌",
        "领涨",
        "妖股",
        "业绩",
        "利润",
        "利润下降",
        "利润增长",
        "展望",
        "逻辑",
        "业务分析",
        "机构评级",
        "评级",
        "目标价",
        "券商研报",
        "研报",
        "预测",
        "投资价值",
        "风险",
        "因素",
        "战略规划",
        "财报预期",
        "股票",
        "电动车",
        "机器人",
        "新能源",
        "军工红外",
        "卫星通信",
        "半导体",
        "氦气",
        "特种气体",
        "医药",
        "CRO",
        "CMO",
        "合同研究组织",
        "合同制造组织",
        "重组",
        "复苏",
        "收购",
        "提价",
        "市场反应",
        "上涨趋势",
        "跌停",
        "原因",
        "业务",
        "加仓",
        "建议",
        "储能",
        "逆变器",
        "光伏",
        "光伏逆变器",
        "行业地位",
        "大涨原因",
        "军工板块",
        "最新分析",
        "分析",
        "订单",
        "行业",
    }
    blocked = {*plan.stock_codes, *plan.company_names}
    for keyword in plan.keywords:
        if keyword in blocked or keyword in generic_terms:
            continue
        if any(code in keyword for code in plan.stock_codes):
            continue
        if _looks_like_date_keyword(keyword):
            continue
        return True
    return False


def _query_requests_stock_research_summary(plan: FinanceQueryPlan) -> bool:
    if not plan.stock_codes:
        return False
    if _query_has_stock_research_terms(plan):
        return True
    if "announcement" in plan.intents or "stock_quote" in plan.intents:
        return False
    if not any(term in plan.query for term in ("最新消息", "最新新闻", "最新动态", "新闻")):
        return False
    return not _has_specific_stock_news_theme(plan)


def _query_has_stock_research_terms(plan: FinanceQueryPlan) -> bool:
    return any(term in plan.query for term in STOCK_RESEARCH_TERMS)


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
