from __future__ import annotations

import subprocess
import time
from urllib.parse import parse_qs, urlparse

from finrecall.api import default_provider
from finrecall.native_finance import NativeFinanceProvider, _default_fetcher, plan_finance_query


def test_plan_finance_query_extracts_stock_date_and_price_intent() -> None:
    plan = plan_finance_query('"杭电股份" "603618" "6月12日" 收盘价 涨跌幅', default_year=2026)

    assert plan.stock_codes == ["603618"]
    assert plan.company_names == ["杭电股份"]
    assert plan.date_text == "2026年6月12日"
    assert "stock_quote" in plan.intents
    assert "dated_price" in plan.intents


def test_native_provider_prioritizes_stock_date_quote_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search('"杭电股份" "603618" "6月12日" 收盘价 涨跌幅', max_results=5, topic="general")

    assert len(items) >= 2
    first = items[0]
    first_blob = f"{first.title}\n{first.content}"
    assert "杭电股份" in first_blob
    assert "603618" in first_blob
    assert "2026年6月12日" in first_blob
    assert "收盘价" in first_blob
    assert "涨跌幅" in first_blob
    assert first.url.startswith("https://quote.eastmoney.com/")
    assert any("vip.stock.finance.sina.com.cn" in item.url for item in items)


def test_native_provider_extracts_dated_quote_when_fetcher_returns_kline() -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2his.eastmoney.com" in url
        return (
            b'{"rc":0,"data":{"code":"603618","name":"Hangdian",'
            b'"klines":["2026-06-12,41.00,38.86,42.22,38.74,773698,3117892407.00,8.41,-6.14,-2.54,11.19"]}}'
        )

    provider = NativeFinanceProvider(fetcher=fake_fetcher)

    items = provider.search("杭电股份 603618 2026年6月12日 收盘价 涨跌幅", max_results=3, topic="general")

    first = items[0]
    assert "收盘价 38.86" in first.title
    assert "涨跌幅 -6.14%" in first.title
    assert "开盘价 41.00" in first.content
    assert "成交额 3117892407.00" in first.content
    assert "查询词" not in first.content
    assert first.raw["native_source"] == "eastmoney_kline"


def test_native_provider_does_not_block_on_slow_quote_fetcher() -> None:
    def slow_fetcher(url: str) -> bytes:
        assert "push2his.eastmoney.com" in url
        time.sleep(0.2)
        raise TimeoutError("quote source was too slow")

    provider = NativeFinanceProvider(fetcher=slow_fetcher, quote_fetch_deadline_seconds=0.01)

    started = time.perf_counter()
    items = provider.search("杭电股份 603618 2026年6月12日 收盘价 涨跌幅", max_results=3, topic="general")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.12
    assert items
    assert items[0].raw["native_source"] == "eastmoney_quote"


def test_native_provider_uses_realtime_quotes_for_monthly_latest_market_query() -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in url
        return (
            b'{"rc":0,"data":{"diff":['
            b'{"f12":"002851","f14":"Megmeet","f2":147.95,"f3":10.0,"f4":13.45,'
            b'"f5":50200,"f6":744000000,"f17":140.0,"f18":134.5,"f15":147.95,"f16":140.0},'
            b'{"f12":"688796","f14":"Biocytogen","f2":96.64,"f3":-0.33,"f4":-0.32,'
            b'"f5":10000,"f6":96336700,"f17":97.0,"f18":96.96,"f15":98.0,"f16":96.0}'
            b"]}}"
        )

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search("麦格米特 002851 百奥赛图 688796 最新行情 2026年6月", max_results=5, topic="general")

    sources = [item.raw["native_source"] for item in items]
    assert sources[:2] == ["eastmoney_realtime_quote", "eastmoney_realtime_quote"]
    assert "最新价 147.95" in items[0].content
    assert "涨跌幅 10.0%" in items[0].content
    assert "成交额 744000000" in items[0].content
    assert "最新价 96.64" in items[1].content


def test_simple_stock_latest_news_query_can_use_realtime_quote_data() -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in url
        return (
            b'{"rc":0,"data":{"diff":['
            b'{"f12":"600887","f14":"Yili","f2":29.18,"f3":0.62,"f4":0.18,'
            b'"f5":102400,"f6":298000000,"f17":29.0,"f18":29.0,"f15":29.3,"f16":28.9}'
            b"]}}"
        )

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search("伊利股份 600887 2026年6月 最新消息", max_results=3, topic="general")

    assert items[0].raw["native_source"] == "eastmoney_realtime_quote"
    assert "最新价 29.18" in items[0].content


def test_simple_stock_latest_news_phrase_can_use_realtime_quote_data() -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in url
        return (
            b'{"rc":0,"data":{"diff":['
            b'{"f12":"002129","f14":"TCL Zhonghuan","f2":7.42,"f3":-1.2,"f4":-0.09,'
            b'"f5":221000,"f6":164000000,"f17":7.5,"f18":7.51,"f15":7.58,"f16":7.35},'
            b'{"f12":"688303","f14":"Daqo","f2":22.1,"f3":0.5,"f4":0.11,'
            b'"f5":31000,"f6":68500000,"f17":22.0,"f18":21.99,"f15":22.4,"f16":21.8}'
            b"]}}"
        )

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search(
        "TCL中环 002129 大全能源 688303 最新新闻 2026年6月",
        max_results=3,
        topic="general",
    )

    assert [item.raw["native_source"] for item in items[:2]] == [
        "eastmoney_realtime_quote",
        "eastmoney_realtime_quote",
    ]
    assert "TCL中环" in items[0].content
    assert "大全能源" in items[1].content


def test_default_fetcher_uses_short_curl_deadline_without_retries(monkeypatch) -> None:
    def raise_from_urlopen(*args: object, **kwargs: object) -> object:
        raise TimeoutError("stdlib client failed")

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b'{"ok": true}', stderr=b"")

    monkeypatch.setattr("finrecall.native_finance.urlopen", raise_from_urlopen)
    monkeypatch.setattr("finrecall.native_finance.subprocess.run", fake_run)

    assert _default_fetcher("https://example.test/data") == b'{"ok": true}'

    args = captured["args"]
    assert isinstance(args, list)
    assert "--retry" not in args
    assert "--retry-all-errors" not in args
    assert args[args.index("--max-time") + 1] == "1.5"


def test_default_fetcher_sends_sse_referer_on_curl_fallback(monkeypatch) -> None:
    def raise_from_urlopen(*args: object, **kwargs: object) -> object:
        raise TimeoutError("stdlib client failed")

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b'{"ok": true}', stderr=b"")

    monkeypatch.setattr("finrecall.native_finance.urlopen", raise_from_urlopen)
    monkeypatch.setattr("finrecall.native_finance.subprocess.run", fake_run)

    _default_fetcher(
        "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do?productId=688200"
    )

    args = captured["args"]
    assert isinstance(args, list)
    assert "Referer: https://www.sse.com.cn/" in args
    assert "User-Agent: Mozilla/5.0" in args


def test_native_provider_returns_moneyflow_authoritative_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("A股 主力资金 板块流入 行业资金流向 2026年6月", max_results=5, topic="general")

    urls = [item.url for item in items]
    assert len(items) >= 3
    assert urls[0] == "https://data.eastmoney.com/zjlx/"
    assert any("vip.stock.finance.sina.com.cn/moneyflow" in url for url in urls)
    assert any("data.10jqka.com.cn/funds" in url for url in urls)
    assert "主力资金" in items[0].content
    assert "板块流入" in items[0].content
    assert "行业资金流向" in items[0].content
    assert "查询词" not in items[0].content


def test_news_announcement_query_prioritizes_news_sources_over_quote() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("拓荆科技 688072 最新新闻 公告 2026年6月", max_results=3, topic="general")

    sources = [item.raw["native_source"] for item in items]
    assert sources
    assert sources[0] in {"sse_notice", "cninfo_notice"}
    assert set(sources).issubset({"sse_notice", "szse_notice", "cninfo_notice"})
    first_blob = f"{items[0].title}\n{items[0].content}"
    assert "拓荆科技" in first_blob
    assert "688072" in first_blob
    assert "2026年6月" in first_blob
    assert "公告" in first_blob
    assert "信息披露" in first_blob


def test_specific_announcement_query_uses_matching_exchange_document_before_entry_pages() -> None:
    def fake_fetcher(url: str) -> bytes:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert parsed.path.endswith("/security/stock/queryCompanyBulletin.do")
        assert params["productId"] == ["603979"]
        assert params["keyWord"][0] in {"Alacran", "铜矿"}
        assert params["beginDate"] == ["2026-05-01"]
        assert params["endDate"] == ["2026-06-30"]
        return (
            "jsonpCallback1({"
            '"pageHelp":{"data":[{'
            '"TITLE":"金诚信关于Alacran铜金银矿项目的进展公告",'
            '"URL":"/disclosure/listedinfo/announcement/c/new/2026-05-18/603979_20260518.pdf",'
            '"SSEDATE":"2026-05-18"'
            "}]},"
            '"result":[]'
            "})"
        ).encode()

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search("金诚信 603979 最新公告 2026年6月 铜矿 Alacran", max_results=3, topic="general")

    assert items[0].raw["native_source"] == "sse_notice_document"
    assert items[0].url == (
        "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/"
        "2026-05-18/603979_20260518.pdf"
    )
    assert "Alacran铜金银矿项目" in items[0].title
    assert "2026-05-18" in items[0].content
    assert {item.raw["native_source"] for item in items[1:]}.issubset({"sse_notice", "cninfo_notice"})


def test_event_disclosure_query_without_announcement_word_searches_exchange_documents() -> None:
    def fake_fetcher(url: str) -> bytes:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert parsed.path.endswith("/security/stock/queryCompanyBulletin.do")
        assert params["productId"] == ["688200"]
        assert params["keyWord"][0] in {"减持", "限售解禁"}
        assert params["beginDate"] == ["2026-05-01"]
        assert params["endDate"] == ["2026-06-30"]
        return (
            "jsonpCallback1({"
            '"pageHelp":{"data":[{'
            '"TITLE":"华峰测控股东询价转让计划书",'
            '"URL":"/disclosure/listedinfo/announcement/c/new/2026-05-27/688200_20260527.pdf",'
            '"SSEDATE":"2026-05-27"'
            "}]},"
            '"result":[]'
            "})"
        ).encode()

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search("华峰测控 688200 减持 限售解禁 风险 2026年6月", max_results=3, topic="general")

    assert items[0].raw["native_source"] == "sse_notice_document"
    assert "询价转让计划书" in items[0].title
    assert items[0].url == (
        "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/"
        "2026-05-27/688200_20260527.pdf"
    )


def test_stock_abnormal_movement_query_routes_to_official_disclosure_pages() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("国瓷材料 300285 股票 异动 2026年6月18日", max_results=3, topic="general")

    assert items
    assert {item.raw["native_source"] for item in items}.issubset({"szse_notice", "cninfo_notice"})
    assert "国瓷材料" in items[0].content


def test_financial_report_query_routes_to_official_disclosure_pages() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("伊利股份 600887 2026年4月 最新消息 一季报", max_results=3, topic="general")

    assert items
    assert {item.raw["native_source"] for item in items}.issubset({"sse_notice", "cninfo_notice"})
    assert "伊利股份" in items[0].content
    assert "公告" in items[0].content


def test_stock_article_news_query_does_not_emit_template_entry_pages() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("视涯科技 688781 Micro OLED AR 最新消息 2026年6月", max_results=5, topic="general")

    assert items == []


def test_general_article_news_query_does_not_emit_search_portals() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("A股 机器人 2026年6月 最新新闻", max_results=5, topic="general")

    assert items
    assert {item.raw["native_source"] for item in items}.issubset(
        {"sector_policy_news", "eastmoney_sector_board", "exchange_investor_education", "humanoid_robot_sector"}
    )


def test_undated_stock_quote_query_does_not_emit_template_quote_pages() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("伊利股份 600887 2026年6月 最新消息 股价", max_results=5, topic="general")

    assert items == []


def test_stock_turnover_query_does_not_route_to_fund_pages() -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in url
        return (
            b'{"rc":0,"data":{"diff":['
            b'{"f12":"688146","f14":"Zhongchuan","f2":33.2,"f3":1.1,"f4":0.36,'
            b'"f5":50000,"f6":166000000,"f17":32.8,"f18":32.84,"f15":33.8,"f16":32.6}'
            b"]}}"
        )

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search("中船特气 688146 最新消息 换手率 2026年6月", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    assert "eastmoney_realtime_quote" in sources
    assert not any(source.startswith("eastmoney_fund") for source in sources)


def test_native_provider_routes_semiconductor_policy_query_to_official_news_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("全球半导体 2026年6月 市场回调 中美芯片 出口管制 最新动态", max_results=3, topic="general")

    urls = [item.url for item in items]
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert any("news.cn" in url or "mofcom.gov.cn" in url for url in urls)
    assert "全球半导体" in blob
    assert "中美芯片" in blob
    assert "出口管制" in blob
    assert "市场回调" in blob


def test_native_provider_routes_yiwu_trade_theme_to_company_and_trade_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("小商品城 世界杯 义乌 商品贸易 2026", max_results=3, topic="general")

    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert any("600415" in item.url or "stcn.com" in item.url or "finance.sina.com.cn" in item.url for item in items)
    assert "小商品城" in blob
    assert "世界杯" in blob
    assert "义乌" in blob
    assert "商品贸易" in blob


def test_native_provider_routes_nonferrous_metals_query_to_content_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("国城矿业 最新消息 2026年6月 有色金属", max_results=3, topic="general")

    urls = [item.url for item in items]
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert any("cngold.org" in url or "21jingji.com" in url for url in urls)
    assert "国城矿业" in blob
    assert "有色金属" in blob
    assert all(len(item.content) >= 40 and len(item.title) + len(item.content) >= 80 for item in items)
    assert {item.raw["native_source"] for item in items}.issubset(
        {"cngold_stock_news", "21jingji_nonferrous_news"}
    )


def test_native_provider_routes_biotech_etf_query_to_structured_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("XBI biotech ETF 2026年6月 走势", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"yahoo_xbi", "investing_xbi", "spglobal_biotech_index", "moomoo_xbi"}
    assert "XBI" in blob
    assert "生物科技" in blob
    assert all(len(item.content) >= 40 and len(item.title) + len(item.content) >= 80 for item in items)


def test_native_provider_routes_synthetic_biology_query_to_content_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("合成生物学 生物科技 政策 新闻 2026年6月", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"cccmhpie_synthetic_biology", "sciencenet_synthetic_biology", "chinasihan_synthetic_biology"}
    assert "合成生物学" in blob
    assert "生物科技" in blob
    assert "政策" in blob
    assert all(len(item.content) >= 40 and len(item.title) + len(item.content) >= 80 for item in items)


def test_native_provider_routes_bse_stock_notice_query_to_official_disclosure_pages() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("蘅东光 920045 最新公告 新闻 2026年6月", max_results=3, topic="general")

    assert items
    assert {item.raw["native_source"] for item in items}.issubset({"bse_notice", "cninfo_notice"})
    assert "920045" in "\n".join(f"{item.title}\n{item.content}" for item in items)


def test_native_provider_routes_stock_money_keyword_to_moneyflow_sources() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("华电辽能 最新消息 2026年5月 庄家 资金", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    assert "eastmoney_moneyflow_stock" in sources
    assert "华电辽能" in "\n".join(f"{item.title}\n{item.content}" for item in items)


def test_native_provider_routes_star50_semiconductor_query_to_index_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("科创50 2026年4月 涨跌幅 估值 PE 半导体", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"csindex_star50", "cnfin_star50_semiconductor", "lixinger_star_semiconductor_pe"}
    assert "科创50" in blob
    assert "半导体" in blob
    assert all(len(item.content) >= 40 and len(item.title) + len(item.content) >= 80 for item in items)


def test_native_provider_routes_fed_rate_query_to_macro_policy_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("美联储 沃什 利率决议 2026年6月 加息", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"fx678_fed_calendar", "federalreserve_fomc_calendar", "wsj_fed_warsh_rates"}
    assert "美联储" in blob
    assert "利率决议" in blob
    assert "沃什" in blob
    assert all(len(item.content) >= 40 and len(item.title) + len(item.content) >= 80 for item in items)


def test_native_provider_routes_a_share_market_query_to_content_summary() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("A股 2026年6月19日 市场行情 大盘走势", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"sina_a_share_market", "eastmoney_a_share_market"}
    assert "A股" in blob
    assert "大盘走势" in blob
    assert all(len(item.content) >= 40 for item in items)


def test_native_provider_routes_fund_turnover_research_query_to_report_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search(
        "A股主动基金 平均换手率 二级债基 调仓频率 季度 2025 2026",
        max_results=3,
        topic="general",
    )

    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert any(item.raw["native_source"] in {"fund_research_report", "fund_industry_news"} for item in items)
    assert "A股主动基金" in blob
    assert "平均换手率" in blob
    assert "二级债基" in blob
    assert "调仓频率" in blob


def test_fund_ranking_candidate_has_substantive_summary() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("富国中证科创创业50ETF联接A 基金代码 业绩 2026", max_results=3, topic="general")

    assert items
    assert all(len(item.content) >= 40 and len(item.title) + len(item.content) >= 80 for item in items)


def test_native_provider_routes_named_fund_holdings_query_to_fund_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("嘉实多利收益债券 2026年一季报 股票持仓 前十大", max_results=3, topic="general")

    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert items
    assert any(item.raw["native_source"] in {"eastmoney_fund", "eastmoney_fund_nav"} for item in items)
    assert "160718" in blob
    assert "股票持仓" in blob


def test_native_provider_routes_company_name_only_market_query_to_quote_data() -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in url
        return (
            b'{"rc":0,"data":{"diff":['
            b'{"f12":"600611","f14":"Dazhong","f2":4.88,"f3":2.52,"f4":0.12,'
            b'"f5":220000,"f6":107000000,"f17":4.76,"f18":4.76,"f15":4.95,"f16":4.7}'
            b"]}}"
        )

    provider = NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2)

    items = provider.search("大众交通 2026年5月 最新消息 股价 走势", max_results=3, topic="general")

    assert items[0].raw["native_source"] == "eastmoney_realtime_quote"
    assert "大众交通" in items[0].content
    assert "最新价 4.88" in items[0].content


def test_native_provider_routes_nvda_query_to_us_equity_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("英伟达 NVDA 股价 2026年5月22日 5月23日 财报后走势", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"yahoo_nvda", "investing_nvda", "nvidia_ir"}
    assert "NVDA" in blob
    assert "英伟达" in blob
    assert "财报" in blob


def test_native_provider_routes_usdcny_query_to_fx_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("美元兑人民币 2025年12月31日 收盘 汇率 USDCNY 7.0 7.1", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"investing_usdcny", "safe_fx_rates", "bankofchina_fx"}
    assert "美元兑人民币" in blob
    assert "USDCNY" in blob


def test_native_provider_routes_topix_query_to_japan_market_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("日本股市 东证指数 TOPIX 2026年5月 最新走势 日元汇率", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"investing_topix", "jpx_topix", "nikkei_topix"}
    assert "TOPIX" in blob
    assert "日本股市" in blob


def test_native_provider_routes_smart_line_strategy_query_to_strategy_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("聪明线 A股 投资策略 组合", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"stock_strategy_smart_line", "ema_strategy_reference", "a_share_strategy_research"}
    assert "聪明线" in blob
    assert "投资策略" in blob


def test_native_provider_routes_block_trade_query_to_transaction_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("沪硅产业 688126 大宗交易 2026年5月 买方 接盘方 机构", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"eastmoney_block_trade", "sse_block_trade", "cninfo_notice"}
    assert "沪硅产业" in blob
    assert "大宗交易" in blob


def test_native_provider_routes_memory_chip_query_to_semiconductor_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("存储芯片 内存 HBM 2026年5月 最新行情 涨价周期", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"semiconductor_memory_market", "trendforce_memory_news", "eastmoney_semiconductor_board"}
    assert "存储芯片" in blob
    assert "HBM" in blob


def test_native_provider_routes_ai_concept_query_to_theme_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("A股 AI概念股 最新消息 2026年5月", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"eastmoney_ai_board", "stcn_ai_theme", "xinhua_ai_policy"}
    assert "AI概念股" in blob
    assert "A股" in blob


def test_native_provider_routes_optical_module_query_to_theme_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("水晶光电 002273 光模块 光引擎 进展 2026年", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"optical_module_market", "cpo_industry_news", "eastmoney_optical_board"}
    assert "光模块" in blob
    assert "水晶光电" in blob


def test_native_provider_routes_sp500_info_tech_english_query_to_market_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search(
        'SP500-45 "S&P 500 Information Technology" May 15 2026 close',
        max_results=3,
        topic="general",
    )

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert "investing_sp_info_tech" in sources
    assert "S&P 500 Information Technology" in blob


def test_native_provider_routes_china_credit_query_to_macro_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("2026年4月社融数据 M2 M1 央行 货币政策 解读", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"pbc_credit_data", "stats_china_money_supply", "cnfin_macro_policy"}
    assert "社融" in blob
    assert "M2" in blob


def test_native_provider_routes_trade_war_history_query_to_market_history_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("2018年中美贸易战 A股暴跌 上证指数 2440点", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"market_history_trade_war", "sse_composite_history", "a_share_crash_review"}
    assert "2018年" in blob
    assert "2440点" in blob


def test_native_provider_routes_gold_etf_query_to_commodity_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("黄金价格 2026年5月 黄金ETF 518880 最新走势", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"investing_gold", "eastmoney_gold_etf", "shanghai_gold_exchange", "eastmoney_fund_nav"}
    assert "黄金" in blob
    assert "518880" in blob


def test_native_provider_routes_china_index_query_to_index_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("创业板50指数 2026年4月28日 涨跌幅", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"csindex_china_indices", "eastmoney_china_index", "sina_china_index"}
    assert "创业板50" in blob
    assert "涨跌幅" in blob


def test_native_provider_routes_quant_factor_query_to_research_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("A股 因子 估值 分位 EP_TTM 市盈率倒数 交叉截面排名 计算方法", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"quant_factor_methodology", "ricequant_factor_research", "joinquant_factor_reference"}
    assert "EP_TTM" in blob
    assert "交叉截面" in blob


def test_native_provider_routes_us_big_tech_earnings_query_to_us_tech_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("谷歌微软亚马逊Meta 2026年4月财报 AI资本支出 展望", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"us_big_tech_earnings", "nasdaq_big_tech", "company_ir_big_tech"}
    assert "AI资本支出" in blob
    assert "Meta" in blob


def test_native_provider_routes_regulatory_compliance_query_to_risk_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("半导体 公司 财务造假 2025 2026 A股", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"csrc_enforcement", "exchange_disciplinary_actions", "cninfo_compliance_search"}
    assert "财务造假" in blob
    assert "信息披露" in blob


def test_native_provider_routes_sector_policy_query_to_sector_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("洪灾利好哪些A股板块 水利建设 防汛概念股", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"water_conservancy_theme", "eastmoney_water_board", "policy_disaster_prevention"}
    assert "防汛" in blob
    assert "水利建设" in blob


def test_native_provider_routes_hongmeng_history_query_to_theme_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("华为鸿蒙概念股 2019年6月 2021年6月 润和软件 诚迈科技 涨幅 回测", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"hongmeng_theme_history", "eastmoney_hongmeng_board", "stcn_hongmeng_news"}
    assert "鸿蒙" in blob
    assert "润和软件" in blob


def test_native_provider_routes_ipo_history_query_to_ipo_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("寒武纪 IPO 发行价 中签率 首日涨幅 2020", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"eastmoney_ipo_history", "sse_ipo_disclosure", "ipo_market_review"}
    assert "寒武纪" in blob
    assert "中签率" in blob


def test_native_provider_routes_aviation_stock_query_to_sector_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("中国国航 南方航空 2026年5月 最新消息 航空股", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert sources <= {"aviation_stock_sector", "eastmoney_aviation_board", "stcn_aviation_news"}
    assert "航空股" in blob
    assert "中国国航" in blob


def test_native_provider_routes_generic_company_research_query_to_profile_sources() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    items = provider.search("九号公司 689009 2026年 业绩 最新消息 利润下降", max_results=3, topic="general")

    sources = {item.raw["native_source"] for item in items}
    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert "eastmoney_stock_profile" in sources
    assert "九号公司" in blob
    assert "业绩" in blob


def test_us_market_query_preserves_combined_index_phrase() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("美股标普信息科技指数 大跌 2026年6月", max_results=3, topic="general")

    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert "美股标普信息科技指数" in blob
    assert "大跌" in blob


def test_nasdaq_short_name_routes_to_us_market_sources() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("纳指 2026年5月12日 收盘 跌幅 2% 科技股 暴跌", max_results=3, topic="general")

    assert items
    assert any(item.raw["native_source"] in {"investing_us_indices", "yahoo_us_market"} for item in items)
    assert "纳斯达克" in "\n".join(f"{item.title}\n{item.content}" for item in items)


def test_trace_audit_sector_queries_route_to_specific_content() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    cases = [
        (
            "2026年4月航天军工板块行情 商业航天政策最新消息",
            "commercial_space_policy",
            ("航天军工", "商业航天"),
        ),
        (
            "2026年食品饮料行业市场动态 业绩预期 消费股走势",
            "food_beverage_sector",
            ("食品饮料", "业绩预期", "消费股"),
        ),
        (
            "白酒行业 2026年 消费复苏 投资价值 最新分析",
            "baijiu_sector_research",
            ("白酒行业", "消费复苏", "投资价值"),
        ),
        (
            "2026年3月 机器人 人形机器人 投资机会 A股 宇树科技",
            "humanoid_robot_sector",
            ("机器人", "人形机器人", "宇树科技"),
        ),
    ]

    for query, expected_source, expected_terms in cases:
        items = provider.search(query, max_results=3, topic="general")
        assert items[0].raw["native_source"] == expected_source
        blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
        for term in expected_terms:
            assert term in blob


def test_trace_audit_strategy_etf_and_commodity_queries_are_specific() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    cases = [
        (
            "2026年4月 A股 市场风格 因子投资 动量 价值 质量因子",
            "a_share_factor_style",
            ("市场风格", "因子投资", "动量", "价值", "质量因子"),
        ),
        (
            "东证ETF 最新 新闻 2026 03 ETF 东京 交易所 1694 场内基金",
            "jpx_topix_etf",
            ("东证ETF", "东京", "交易所", "1694", "场内基金"),
        ),
        (
            "A股航空股 油价暴涨 历史走势 类似情况 2008 2022",
            "aviation_oil_history",
            ("航空股", "油价暴涨", "历史走势", "2008"),
        ),
        (
            "2026年全球经济形势 地缘冲突 黄金避险需求",
            "global_gold_safe_haven",
            ("全球经济形势", "地缘冲突", "黄金避险需求"),
        ),
    ]

    for query, expected_source, expected_terms in cases:
        items = provider.search(query, max_results=3, topic="general")
        assert items[0].raw["native_source"] == expected_source
        blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
        for term in expected_terms:
            assert term in blob


def test_trace_audit_stock_theme_queries_include_company_and_theme() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    cases = [
        (
            "安克创新 300866 关税 美国 储能 AI芯片 2026年5月 最新",
            "stock_theme_research",
            ("安克创新", "300866", "关税", "储能", "AI芯片"),
        ),
        (
            "旭光电子 600353 2026年 氮化铝 军工 最新动态",
            "stock_theme_research",
            ("旭光电子", "600353", "氮化铝", "军工"),
        ),
        (
            "A股 半导体 AI 芯片 2026年4月 最新政策 行情",
            "semiconductor_ai_policy_market",
            ("半导体", "AI", "芯片", "最新政策"),
        ),
    ]

    for query, expected_source, expected_terms in cases:
        items = provider.search(query, max_results=3, topic="general")
        assert items[0].raw["native_source"] == expected_source
        blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
        for term in expected_terms:
            assert term in blob


def test_trace_audit_remaining_market_and_fund_queries_cover_salient_terms() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    cases = [
        (
            "2026年4月 A股市场板块资金流向 主力资金买入 最新新闻",
            "eastmoney_moneyflow",
            ("2026年4月", "A股市场板块资金流向", "主力资金买入"),
        ),
        (
            "A股 2026年4月29日 市场震荡 最新政策 资金面",
            "sina_a_share_market",
            ("2026年4月29日", "市场震荡", "最新政策", "资金面"),
        ),
        (
            "2026年4月14日 A股 开盘 实时 行情",
            "sina_a_share_market",
            ("2026年4月14日", "开盘", "实时"),
        ),
        (
            "A股主动基金 平均换手率 二级债基 调仓频率 季度 2025 2026",
            "fund_turnover_research",
            ("A股主动基金", "平均换手率", "二级债基", "调仓频率", "季度"),
        ),
        (
            "2026年4月 A股市场 宽基ETF 投资 定投 政策 推荐",
            "broad_based_etf_strategy",
            ("2026年4月", "A股市场", "宽基ETF", "定投", "政策", "推荐"),
        ),
        (
            "原油价格 2026年4月 暴涨10% 原因",
            "investing_crude_oil",
            ("原油价格", "2026年4月", "暴涨10%"),
        ),
    ]

    for query, expected_source, expected_terms in cases:
        items = provider.search(query, max_results=3, topic="general")
        assert items[0].raw["native_source"] == expected_source
        blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
        for term in expected_terms:
            assert term in blob


def test_trace_audit_alias_and_hk_company_queries_preserve_user_terms() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    cases = [
        ("ST天箭 最新消息 2026年", ("ST天箭",)),
        ("华能蒙电 股票 分析 最新消息", ("华能蒙电",)),
        (
            "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务 业务表现",
            ("小米集团", "2025年财报", "收入构成", "智能手机", "IoT", "互联网服务", "业务表现"),
        ),
        (
            "胜宏科技 300476 2026年5月 最新动态 PCB",
            ("胜宏科技", "300476", "2026年5月", "PCB"),
        ),
    ]

    for query, expected_terms in cases:
        items = provider.search(query, max_results=3, topic="general")
        blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
        for term in expected_terms:
            assert term in blob


def test_trace_audit_remaining_report_and_cycle_queries_cover_salient_terms() -> None:
    provider = NativeFinanceProvider(quote_fetch_deadline_seconds=0)

    cases = [
        (
            '"161128" 标普信息科技 资产配置 股票仓位 债券 现金 2026年一季报 年报 占比',
            ("标普信息科技", "资产配置", "股票仓位", "债券", "现金", "2026年一季报", "年报", "占比"),
        ),
        (
            "民生银行 2025年 年报 不良贷款率 1.24% 资本充足率 9.38%",
            ("民生银行", "2025年", "年报", "不良贷款率", "1.24%", "资本充足率", "9.38%"),
        ),
        (
            "电力板块 2026年4月 最新政策 电价改革 新能源转型 市场动态",
            ("电力板块", "2026年4月", "最新政策", "电价改革", "新能源转型", "市场动态"),
        ),
        (
            "美股 2026年5月12日 科技股暴跌 原因 VIX 苹果 思科",
            ("美股", "2026年5月12日", "科技股暴跌", "VIX", "苹果", "思科"),
        ),
        (
            "XBI 标普生物科技指数 110%涨幅 2025-2026 降息周期 生物科技轮动逻辑",
            ("XBI", "标普生物科技指数", "110%涨幅", "2025-2026", "降息周期", "生物科技轮动逻辑"),
        ),
    ]

    for query, expected_terms in cases:
        items = provider.search(query, max_results=4, topic="general")
        blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
        for term in expected_terms:
            assert term in blob


def test_default_provider_uses_native_finance(monkeypatch) -> None:
    monkeypatch.delenv("FINRECALL_PROVIDER", raising=False)
    assert isinstance(default_provider(), NativeFinanceProvider)

    monkeypatch.setenv("FINRECALL_PROVIDER", "native")
    assert isinstance(default_provider(), NativeFinanceProvider)
