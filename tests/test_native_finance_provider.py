from __future__ import annotations

import subprocess
import time

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

    assert len(items) >= 3
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

    assert items[0].raw["native_source"] in {"eastmoney_notice", "sina_notice", "xueqiu_stock"}
    first_blob = f"{items[0].title}\n{items[0].content}"
    assert "拓荆科技" in first_blob
    assert "688072" in first_blob
    assert "2026年6月" in first_blob
    assert "最新新闻" in first_blob
    assert "公告" in first_blob


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


def test_us_market_query_preserves_combined_index_phrase() -> None:
    provider = NativeFinanceProvider()

    items = provider.search("美股标普信息科技指数 大跌 2026年6月", max_results=3, topic="general")

    blob = "\n".join(f"{item.title}\n{item.content}" for item in items)
    assert "美股标普信息科技指数" in blob
    assert "大跌" in blob


def test_default_provider_uses_native_finance(monkeypatch) -> None:
    monkeypatch.delenv("FINRECALL_PROVIDER", raising=False)
    assert isinstance(default_provider(), NativeFinanceProvider)

    monkeypatch.setenv("FINRECALL_PROVIDER", "native")
    assert isinstance(default_provider(), NativeFinanceProvider)
