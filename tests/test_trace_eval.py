from __future__ import annotations

from finrecall import FinRecallClient
from finrecall.models import DocumentRecord, ProviderSearchItem
from finrecall.trace_eval import compare_trace_teacher_results


class LowQualityProvider:
    source_name = "comparison_provider"

    def search(self, query: str, *, max_results: int, topic: str, time_window: str | None = None):
        return [
            ProviderSearchItem(
                title="东方财富财经搜索 A股 新闻 行情 公告",
                url="https://so.eastmoney.com/web/s?keyword=Micro+OLED",
                content="东方财富 A股 财经新闻、行情、公告、数据中心。",
                raw={"native_source": "eastmoney_search"},
            ),
            ProviderSearchItem(
                title="视涯科技行情中心",
                url="https://quote.eastmoney.com/sh688781.html",
                content="视涯科技 688781 行情 K线。",
                raw={"native_source": "eastmoney_quote"},
            ),
            ProviderSearchItem(
                title="视涯科技公司页",
                url="https://news.example.com/stock-a",
                content="视涯科技 688781 Micro OLED。",
                raw={"native_source": "example_article"},
            ),
            ProviderSearchItem(
                title="视涯科技重复公司页",
                url="https://news.example.com/stock-b",
                content="视涯科技 688781 AR。",
                raw={"native_source": "example_article"},
            ),
        ][:max_results]


class OfficialDisclosureProvider:
    source_name = "comparison_provider"

    def search(self, query: str, *, max_results: int, topic: str, time_window: str | None = None):
        return [
            ProviderSearchItem(
                title="凯赛生物公司公告 信息披露 - 上海证券交易所",
                url=(
                    "https://www.sse.com.cn/assortment/stock/list/info/"
                    "announcement/index.shtml?productId=688065"
                ),
                content=(
                    "上海证券交易所官方信息披露入口，按证券代码查询凯赛生物 688065 "
                    "公司公告、临时公告、定期报告、监管披露文件和交易所公告原文。"
                ),
            )
        ][:max_results]


class RealtimeQuoteProvider:
    source_name = "comparison_provider"

    def search(self, query: str, *, max_results: int, topic: str, time_window: str | None = None):
        return [
            ProviderSearchItem(
                title="麦格米特(002851) 最新价 147.95 - 东方财富实时行情",
                url="https://quote.eastmoney.com/sz002851.html",
                content="麦格米特 002851 最新价 147.95，涨跌幅 10.0%，成交额 744000000。",
                raw={"native_source": "eastmoney_realtime_quote"},
            ),
            ProviderSearchItem(
                title="百奥赛图(688796) 最新价 96.64 - 东方财富实时行情",
                url="https://quote.eastmoney.com/sh688796.html",
                content="百奥赛图 688796 最新价 96.64，涨跌幅 -0.33%，成交额 96336700。",
                raw={"native_source": "eastmoney_realtime_quote"},
            ),
        ][:max_results]


class StructuredMarketDataProvider:
    source_name = "comparison_provider"

    def search(self, query: str, *, max_results: int, topic: str, time_window: str | None = None):
        return [
            ProviderSearchItem(
                title="A股主力资金 行业资金流向 - 东方财富",
                url="https://data.eastmoney.com/zjlx/",
                content=(
                    "A股主力资金和行业资金流向数据，覆盖板块流入、板块流出、"
                    "个股资金净额、成交额变化、行业轮动、指数强弱、市场风险偏好和热点扩散情况。"
                ),
                raw={"native_source": "eastmoney_moneyflow"},
            )
        ][:max_results]


def test_compare_trace_teacher_results_reports_low_quality_native_gaps(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=LowQualityProvider(),
    )
    query = "视涯科技 688781 Micro OLED AR 最新消息 2026年6月"
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.abvr360.com/a/33400",
            title="视涯拟与歌尔开展16亿元关联交易，聚焦Micro OLED",
            content="视涯科技发布公告，新增与歌尔光学日常关联交易，金额不超过16亿元。",
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
            url="https://finance.sina.com.cn/tech/2026-06-18/doc-example.shtml",
            title="硅基OLED产业链订单跟踪",
            content="报道提到视涯科技、Micro OLED、AR 眼镜和显示屏订单。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 2,
            },
        )
    )

    report = compare_trace_teacher_results(client, max_cases=1, max_results=4)

    assert report["summary"]["query_count"] == 1
    assert report["summary"]["portal_result_count"] == 1
    assert report["summary"]["thin_result_count"] == 4
    assert report["summary"]["duplicate_domain_count"] == 1
    assert report["cases"][0]["query"] == query
    assert "portal_results" in report["cases"][0]["issues"]
    assert "thin_results" in report["cases"][0]["issues"]


def test_compare_trace_teacher_results_can_include_actual_result_snapshots(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=LowQualityProvider(),
    )
    query = "视涯科技 688781 Micro OLED AR 最新消息 2026年6月"
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.abvr360.com/a/33400",
            title="视涯拟与歌尔开展16亿元关联交易，聚焦Micro OLED",
            content="视涯科技发布公告，新增与歌尔光学日常关联交易，金额不超过16亿元。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 1,
            },
        )
    )

    report = compare_trace_teacher_results(
        client,
        max_cases=1,
        max_results=2,
        include_results=True,
        snippet_chars=12,
    )
    case = report["cases"][0]

    assert case["teacher"]["results"] == [
        {
            "rank": 1,
            "title": "视涯拟与歌尔开展16亿元关联交易，聚焦Micro OLED",
            "domain": "www.abvr360.com",
            "url": "https://www.abvr360.com/a/33400",
            "snippet": "视涯科技发布公告，新增与",
        }
    ]
    assert case["finrecall"]["results"][0]["native_source"] == "eastmoney_search"
    assert case["finrecall"]["results"][0]["snippet"] == "东方财富 A股 财经新闻"
    assert any("portal/search" in item for item in case["diagnosis"])


def test_compare_trace_teacher_results_diagnoses_empty_native_results(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=OfficialDisclosureProvider(),
    )
    query = "国城矿业 最新消息 2026年6月 有色金属"
    client.storage.upsert_document(
        DocumentRecord(
            url="https://m.cngold.org/home/xw10553800.html",
            title="国城矿业股票6月10日主力资金净流入302.68万元",
            content="国城矿业 000688 股市行情最新消息，主力资金净流入302.68万元。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": query,
                "rank": 1,
            },
        )
    )
    client.provider = type(
        "EmptyProvider",
        (),
        {
            "source_name": "empty_provider",
            "search": lambda self, query, *, max_results, topic, time_window=None: [],
        },
    )()

    report = compare_trace_teacher_results(
        client,
        max_cases=1,
        include_results=True,
    )

    assert report["cases"][0]["finrecall"]["results"] == []
    assert any("returned no result" in item for item in report["cases"][0]["diagnosis"])


def test_compare_trace_teacher_results_treats_realtime_quote_as_structured_data(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=RealtimeQuoteProvider(),
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://cn.investing.com/equities/shenzhen-megmeet-electrical",
            title="麦格米特(002851)股票最新价格行情",
            content="截至2026年06月01日，麦格米特的交易价格报147.95。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": "麦格米特 002851 百奥赛图 688796 最新行情 2026年6月",
                "rank": 1,
            },
        )
    )

    report = compare_trace_teacher_results(client, max_cases=1, max_results=5)
    case = report["cases"][0]

    assert case["finrecall"]["portal_result_count"] == 0
    assert case["finrecall"]["duplicate_domain_count"] == 0
    assert "portal_results" not in case["issues"]
    assert "duplicate_domains" not in case["issues"]


def test_compare_trace_teacher_results_treats_curated_market_data_as_structured_data(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=StructuredMarketDataProvider(),
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://stock.cngold.org/c/2026-06-19/c10559616.html",
            title="A股主力资金和行业板块资金流向",
            content="A股主力资金、板块流入、行业资金流向和市场成交额数据。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": "A股 主力资金 板块流入 行业资金流向 2026年6月",
                "rank": 1,
            },
        )
    )

    report = compare_trace_teacher_results(client, max_cases=1, max_results=5)
    case = report["cases"][0]

    assert case["finrecall"]["portal_result_count"] == 0
    assert case["finrecall"]["thin_result_count"] == 0
    assert "portal_results" not in case["issues"]
    assert "thin_results" not in case["issues"]


def test_compare_trace_teacher_results_does_not_mark_official_disclosure_as_portal(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=OfficialDisclosureProvider(),
    )
    client.storage.upsert_document(
        DocumentRecord(
            url="https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=688065",
            title="凯赛生物公司公告 - 上海证券交易所",
            content="上海证券交易所官方披露凯赛生物 688065 公司公告、临时公告、定期报告。",
            raw_payload={
                "source": "tool_web_search_trace",
                "teacher": "tavily",
                "query": "凯赛生物 688065 最新公告",
                "rank": 1,
            },
        )
    )

    report = compare_trace_teacher_results(client, max_cases=1, max_results=1)

    assert report["summary"]["portal_result_count"] == 0
    assert report["cases"][0]["issues"] == []
