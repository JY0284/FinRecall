from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
import threading
import time

from finrecall import FinRecallClient
from finrecall.models import DocumentRecord
from finrecall.models import ProviderSearchItem
from finrecall.native_finance import NativeFinanceProvider
from finrecall.providers import ProviderError
from finrecall.storage import SearchStore


class FakeProvider:
    def __init__(self, items: list[ProviderSearchItem], delay: float = 0.0) -> None:
        self.items = items
        self.delay = delay
        self.calls: list[tuple[str, int, str]] = []
        self._lock = threading.Lock()

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        with self._lock:
            self.calls.append((query, max_results, topic))
        if self.delay:
            time.sleep(self.delay)
        return self.items[:max_results]


class FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        self.calls += 1
        raise ProviderError("provider request timed out", error_class="timeout", retryable=True)


def test_migrations_are_idempotent_and_enable_wal(tmp_path) -> None:
    db_path = tmp_path / "research.sqlite"
    store = SearchStore(db_path)

    store.migrate()
    store.migrate()

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert {
        "schema_migrations",
        "search_events",
        "search_results",
        "documents",
        "document_topics",
        "document_mentions",
        "document_fts",
        "fetch_attempts",
    }.issubset(tables)
    assert journal_mode == "wal"
    assert busy_timeout >= 1000


def test_search_web_caches_provider_results_and_indexes_archive(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="贵州茅台一季度利润增长",
                url="https://news.example.com/a?utm_source=x",
                content="贵州茅台 600519 A股 业绩 利润 增长",
                published_at=datetime(2026, 6, 19, 9, 30, tzinfo=timezone.utc),
                raw_date_text="2026-06-19 17:30",
                date_source="provider",
                date_confidence=0.7,
                raw={"category": "finance"},
            )
        ]
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        cache_ttl_seconds=60,
    )

    first = client.search_web("贵州茅台 最新 新闻", max_results=3, topic="news")
    second = client.search_web("贵州茅台 最新 新闻", max_results=3, topic="news")
    archive = client.search_archive(
        "贵州茅台 利润",
        topics=["a-share"],
        published_after=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    assert first.error is None
    assert first.cached is False
    assert first.results[0].url == "https://news.example.com/a"
    assert second.cached is True
    assert len(provider.calls) == 1
    assert archive.results[0].url == "https://news.example.com/a"
    assert "a-share" in {topic.topic for topic in archive.results[0].topics}
    assert any(mention.value == "600519" for mention in archive.results[0].mentions)


def test_search_web_ignores_trace_teacher_archive_by_default(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="视涯科技(688781) 最新新闻 公司公告 - 东方财富",
                url="https://data.eastmoney.com/notices/stock/688781.html",
                content="视涯科技 688781 最新新闻、最新公告、定期报告。",
            )
        ]
    )
    client = FinRecallClient(db_path=tmp_path / "research.sqlite", provider=provider)
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.abvr360.com/a/33400",
            title="视涯拟与歌尔开展16亿元关联交易，聚焦Micro OLED",
            content="视涯科技 688781 歌尔 Micro OLED 硅基OLED AR 16亿元 关联交易。",
            raw_payload={"source": "tool_web_search_trace", "teacher": "tavily"},
        )
    )

    outcome = client.search_web(
        "视涯科技 688781 Micro OLED AR 最新消息 2026年6月",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.source == "finrecall"
    assert outcome.results[0].url == "https://data.eastmoney.com/notices/stock/688781.html"
    assert provider.calls == [("视涯科技 688781 Micro OLED AR 最新消息 2026年6月", 3, "general")]


def test_search_web_can_opt_into_trace_teacher_replay_for_experiments(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="视涯科技(688781) 最新新闻 公司公告 - 东方财富",
                url="https://data.eastmoney.com/notices/stock/688781.html",
                content="视涯科技 688781 最新新闻、最新公告、定期报告。",
            )
        ]
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        trace_teacher_replay=True,
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.abvr360.com/a/33400",
            title="视涯拟与歌尔开展16亿元关联交易，聚焦Micro OLED",
            content="视涯科技 688781 歌尔 Micro OLED 硅基OLED AR 16亿元 关联交易。",
            raw_payload={"source": "tool_web_search_trace", "teacher": "tavily"},
        )
    )

    outcome = client.search_web(
        "视涯科技 688781 Micro OLED AR 最新消息 2026年6月",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.source == "trace_teacher_archive"
    assert outcome.results[0].url == "https://www.abvr360.com/a/33400"
    assert provider.calls == []


def test_search_web_replays_trace_teacher_for_exact_historical_query(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="金诚信(603979) 2026年6月 最新新闻 公司公告 - 东方财富",
                url="https://data.eastmoney.com/notices/stock/603979.html",
                content="金诚信 603979 2026年6月 最新新闻、最新公告、定期报告。",
            )
        ]
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        trace_teacher_replay=True,
    )
    query = "金诚信 603979 最新公告 2026年6月 铜矿 Alacran"
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.sohu.com/a/1026760023_115377",
            title="金诚信拟取得加拿大Alacran铜矿项目控制权",
            content="Alacran铜矿项目位于加拿大，交易完成后公司将进一步增加铜矿资源储备。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 2,
            },
        )
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.stcn.com/article/detail/3967343.html",
            title="金诚信Alacran铜矿项目公告",
            content="金诚信公告披露Alacran铜矿项目进展。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 1,
            },
        )
    )

    outcome = client.search_web(query, max_results=3, force_refresh=True)

    assert outcome.source == "trace_teacher_archive"
    assert outcome.results[0].url == "https://www.stcn.com/article/detail/3967343.html"
    assert outcome.results[1].url == "https://www.sohu.com/a/1026760023_115377"
    assert provider.calls == []


def test_search_web_replays_similar_trace_teacher_query(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="金诚信(603979) 最新新闻 公司公告 - 东方财富",
                url="https://data.eastmoney.com/notices/stock/603979.html",
                content="金诚信 603979 最新新闻、最新公告、定期报告。",
            )
        ]
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        trace_teacher_replay=True,
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.stcn.com/article/detail/3967343.html",
            title="金诚信Alacran铜矿项目公告",
            content="金诚信 603979 Alacran 铜矿 项目披露资源进展。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": "金诚信 603979 最新公告 2026年6月 铜矿 Alacran",
                "rank": 1,
            },
        )
    )

    outcome = client.search_web("金诚信 603979 铜矿 Alacran 最新消息", max_results=3, force_refresh=True)

    assert outcome.source == "trace_teacher_archive"
    assert outcome.results[0].url == "https://www.stcn.com/article/detail/3967343.html"
    assert provider.calls == []


def test_search_web_skips_low_value_teacher_quote_page_for_news_query(tmp_path) -> None:
    provider = FakeProvider([])
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        trace_teacher_replay=True,
    )
    query = "拓荆科技 688072 最新新闻 公告 2026年6月"
    client.storage.upsert_document(
        DocumentRecord(
            url="https://cn.investing.com/equities/piotech",
            title="拓荆科技股票最新价格行情,实时走势图,股价分析预测",
            content="拓荆科技 688072 股价 行情 K线图。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 1,
            },
        )
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://paper.cnstock.com/html/2026-06/17/content_2232118.htm",
            title="拓荆科技披露重大合同公告",
            content="拓荆科技 688072 发布公告，披露半导体设备订单和经营进展。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 2,
            },
        )
    )

    outcome = client.search_web(query, max_results=3, force_refresh=True)

    assert outcome.source == "trace_teacher_archive"
    assert outcome.results[0].url == "https://paper.cnstock.com/html/2026-06/17/content_2232118.htm"


def test_search_web_filters_low_value_native_news_entry_pages(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(quote_fetch_deadline_seconds=0),
    )

    outcome = client.search_web(
        "视涯科技 688781 Micro OLED AR 最新消息 2026年6月",
        max_results=5,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.source == "native_finance"
    assert outcome.results == []


def test_search_web_keeps_native_pages_when_user_requests_announcements(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(quote_fetch_deadline_seconds=0),
    )

    outcome = client.search_web("拓荆科技 688072 最新新闻 公告 2026年6月", force_refresh=True)

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"sse_notice", "cninfo_notice"}


def test_search_web_keeps_native_specific_notice_documents(tmp_path) -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "queryCompanyBulletin.do" in url
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

    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2),
    )

    outcome = client.search_web("金诚信 603979 最新公告 2026年6月 铜矿 Alacran", force_refresh=True)

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] == "sse_notice_document"
    assert "Alacran铜金银矿项目" in outcome.results[0].title


def test_search_web_keeps_native_realtime_quote_data_for_market_queries(tmp_path) -> None:
    def fake_fetcher(url: str) -> bytes:
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in url
        return (
            b'{"rc":0,"data":{"diff":['
            b'{"f12":"002851","f14":"Megmeet","f2":147.95,"f3":10.0,"f4":13.45,'
            b'"f5":50200,"f6":744000000,"f17":140.0,"f18":134.5,"f15":147.95,"f16":140.0}'
            b"]}}"
        )

    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(fetcher=fake_fetcher, quote_fetch_deadline_seconds=0.2),
    )

    outcome = client.search_web("麦格米特 002851 最新行情 2026年6月", max_results=3, force_refresh=True)

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] == "eastmoney_realtime_quote"
    assert "最新价 147.95" in outcome.results[0].content


def test_search_web_keeps_native_us_market_data_for_market_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "美股 2026年6月15日收盘 道指 纳指 标普 费城半导体 涨跌幅",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {
        "investing_us_indices",
        "yahoo_us_market",
        "investing_philly_semiconductor",
    }


def test_search_web_keeps_native_biotech_market_data_for_market_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web("XBI biotech ETF 2026年6月 走势", max_results=3, force_refresh=True)

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"yahoo_xbi", "investing_xbi", "spglobal_biotech_index"}


def test_search_web_keeps_native_fund_data_for_named_fund_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "嘉实多利收益债券 净值 2026年6月2日",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {
        "eastmoney_fund",
        "eastmoney_fund_nav",
        "eastmoney_fund_ranking",
    }


def test_search_web_keeps_native_fund_data_for_quarterly_holdings_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "嘉实多利收益债券 160718 2026年一季报 股票持仓 前十大",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    sources = {item.raw["native_source"] for item in outcome.results}
    assert sources <= {"eastmoney_fund", "eastmoney_fund_nav", "eastmoney_fund_ranking"}


def test_search_web_keeps_native_us_equity_data_for_market_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "英伟达 NVDA 股价 2026年5月22日 5月23日 财报后走势",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"yahoo_nvda", "investing_nvda", "nvidia_ir"}


def test_search_web_keeps_native_sp500_info_tech_data_for_quarterly_return_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "S&P 500 Information Technology SP500-45 2026 Q1 回报 收益率 3月31日",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"investing_sp_info_tech", "yahoo_us_market", "investing_us_indices"}


def test_search_web_keeps_native_fx_data_for_exchange_rate_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "美元兑人民币 2025年12月31日 收盘 汇率 USDCNY 7.0 7.1",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"investing_usdcny", "safe_fx_rates", "bankofchina_fx"}


def test_search_web_keeps_native_japan_market_data_for_market_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "日本股市 东证指数 TOPIX 2026年5月 最新走势 日元汇率",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"investing_topix", "jpx_topix", "nikkei_topix"}


def test_search_web_keeps_native_stock_data_for_drop_event_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(quote_fetch_deadline_seconds=0),
    )

    outcome = client.search_web(
        "新大陆 000997 大跌 2026年5月28日 5月29日",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"eastmoney_quote", "sina_quote"}


def test_search_web_keeps_native_star_market_data_for_market_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "科创50 2026年4月 涨跌幅 估值 PE 半导体",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {
        "csindex_star50",
        "cnfin_star50_semiconductor",
        "lixinger_star_semiconductor_pe",
    }


def test_search_web_does_not_misclassify_a50_passive_funds_as_moneyflow(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(),
    )

    outcome = client.search_web(
        "富时中国A50 调仓 6月18日 被动资金 兆易创新 澜起科技 2026",
        max_results=3,
        force_refresh=True,
    )

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"investing_a50", "ftse_russell"}


def test_search_web_keeps_native_moneyflow_for_dealer_money_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(quote_fetch_deadline_seconds=0),
    )

    outcome = client.search_web("华电辽能 最新消息 2026年5月 庄家 资金", max_results=3, force_refresh=True)

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {
        "eastmoney_moneyflow",
        "eastmoney_moneyflow_stock",
    }


def test_search_web_keeps_native_bse_notice_for_announcement_queries(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=NativeFinanceProvider(quote_fetch_deadline_seconds=0),
    )

    outcome = client.search_web("蘅东光 920045 最新公告 新闻 2026年6月", max_results=3, force_refresh=True)

    assert outcome.error is None
    assert outcome.results
    assert outcome.results[0].raw["native_source"] in {"bse_notice", "cninfo_notice"}


def test_search_web_records_provider_errors_without_crashing(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=FailingProvider(),
        cache_ttl_seconds=60,
    )

    outcome = client.search_web("A股 今日新闻", max_results=3)

    assert outcome.results == []
    assert outcome.error is not None
    assert outcome.error.error_class == "timeout"
    assert "timed out" in outcome.error.message
    assert client.storage.stats()["search_events"] == 1


def test_concurrent_identical_queries_dedupe_provider_calls(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderSearchItem(
                title="A股政策新闻",
                url="https://news.example.com/policy",
                content="A股 政策 证监会",
            )
        ],
        delay=0.05,
    )
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        cache_ttl_seconds=60,
    )

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(lambda _: client.search_web("A股 政策", max_results=1), range(5)))

    assert [outcome.results[0].url for outcome in outcomes] == [
        "https://news.example.com/policy",
        "https://news.example.com/policy",
        "https://news.example.com/policy",
        "https://news.example.com/policy",
        "https://news.example.com/policy",
    ]
    assert len(provider.calls) == 1
    assert sum(outcome.cached for outcome in outcomes) >= 4


def test_fetch_and_store_extracts_dates_topics_and_mentions(tmp_path) -> None:
    html = """
    <html>
      <head>
        <title>贵州茅台发布业绩公告</title>
        <meta property="article:published_time" content="2026-06-19T10:00:00+08:00">
      </head>
      <body>
        <article>贵州茅台 600519 A股 业绩 公告 政策 现金流改善。</article>
      </body>
    </html>
    """

    def fetcher(url: str):
        return {
            "status": 200,
            "headers": {"content-type": "text/html"},
            "body": html.encode("utf-8"),
            "final_url": url,
        }

    client = FinRecallClient(db_path=tmp_path / "research.sqlite", fetcher=fetcher)

    document = client.fetch_and_store(
        "https://news.example.com/moutai?utm_campaign=test#section",
        expected_topics=["a-share"],
    )
    archive = client.search_archive(
        "现金流 改善",
        topics=["a-share"],
        published_after=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    assert document.url == "https://news.example.com/moutai"
    assert document.title == "贵州茅台发布业绩公告"
    assert document.published_at is not None
    assert document.published_at.isoformat() == "2026-06-19T02:00:00+00:00"
    assert document.date_source == "metadata"
    assert any(topic.topic == "a-share" for topic in document.topics)
    assert any(mention.value == "600519" for mention in document.mentions)
    assert archive.results[0].url == "https://news.example.com/moutai"
