from __future__ import annotations

from finrecall.hybrid import HybridSearchProvider
from finrecall.models import ProviderSearchItem


class NativeStub:
    source_name = "native_finance"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        return [
            ProviderSearchItem(
                title="港股市场 行情 财报 新闻 - 富途资讯",
                url="https://news.futunn.com/hk",
                content="富途港股市场资讯，跟踪港股行情、财报、新闻和资金流向。",
                raw={"native_source": "futu_hk_market", "provider": "native_finance"},
            )
        ]


class KeylessStub:
    source_name = "keyless_search"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        return [
            ProviderSearchItem(
                title="小米集团发布2025年财报：营收4573亿",
                url="https://finance.example.com/news/xiaomi-2025-results.html",
                content="小米集团2025年总收入达4573亿元，手机 x AIoT 分部收入3512亿元。",
                raw={
                    "source_engine": "duckduckgo_html",
                    "harvest_mode": "keyless_http",
                    "provider": "keyless_search",
                },
            )
        ]


def test_hybrid_provider_prefers_keyless_content_for_news_and_earnings_queries() -> None:
    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=KeylessStub())

    items = provider.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=2,
        topic="general",
    )

    assert items[0].title == "小米集团发布2025年财报：营收4573亿"
    assert items[0].raw["provider"] == "hybrid_keyless"
    assert items[0].raw["hybrid_sources"] == ["keyless_search"]


def test_hybrid_provider_does_not_fill_content_queries_with_low_value_native_market_data() -> None:
    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=KeylessStub())

    items = provider.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=5,
        topic="general",
    )

    assert [item.url for item in items] == ["https://finance.example.com/news/xiaomi-2025-results.html"]


def test_hybrid_provider_keeps_native_only_for_market_data_queries() -> None:
    class FailingKeyless:
        source_name = "keyless_search"

        def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("keyless search should not run for data-only queries")

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=FailingKeyless())

    items = provider.search("黄金ETF 518880 最新净值 单位净值", max_results=2, topic="general")

    assert len(items) == 1
    assert items[0].url == "https://news.futunn.com/hk"


def test_hybrid_provider_diversifies_domains_when_keyless_has_repeats() -> None:
    class RepeatingKeyless:
        source_name = "keyless_search"

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            return [
                ProviderSearchItem(
                    title="小米财报文章一",
                    url="https://finance.example.com/a",
                    content="小米集团2025年财报收入构成详细分析，内容足够长。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="小米财报文章二",
                    url="https://finance.example.com/b",
                    content="小米集团2025年财报收入构成另一篇详细分析，内容足够长。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="小米官方财报",
                    url="https://ir.example.com/xiaomi",
                    content="小米集团官方财报，披露智能手机、IoT和互联网服务。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=RepeatingKeyless())

    items = provider.search("小米集团 2025年财报 收入构成", max_results=3, topic="general")

    domains = [item.url.split("/")[2] for item in items]
    assert domains.count("finance.example.com") == 1
    assert "ir.example.com" in domains


def test_hybrid_provider_dedupes_syndicated_results_by_title() -> None:
    class DuplicatedTitleKeyless:
        source_name = "keyless_search"

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            return [
                ProviderSearchItem(
                    title="券商研判2026年A股走势：慢牛行情延续，基本面重要性进一步上升",
                    url="https://www.yicai.com/news/1.html",
                    content="券商研判2026年A股走势，慢牛行情延续，基本面重要性进一步上升。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "duckduckgo_html"},
                ),
                ProviderSearchItem(
                    title="券商研判2026年A股走势：慢牛行情延续，基本面重要性进一步上升 _ 东方财富网",
                    url="https://finance.eastmoney.com/a/1.html",
                    content="券商研判2026年A股走势，慢牛行情延续，基本面重要性进一步上升。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "duckduckgo_html"},
                ),
                ProviderSearchItem(
                    title="A股市场2026年展望：乘势笃行",
                    url="https://stock.finance.sina.com.cn/a.html",
                    content="A股市场2026年展望，政策、流动性和盈利修复共同影响市场。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "duckduckgo_html"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=DuplicatedTitleKeyless())

    items = provider.search("A股 市场震荡 原因 资金面 2026", max_results=5, topic="general")

    titles = [item.title for item in items]
    assert titles == [
        "券商研判2026年A股走势：慢牛行情延续，基本面重要性进一步上升",
        "A股市场2026年展望：乘势笃行",
    ]


def test_hybrid_provider_filters_quarterly_results_for_full_year_report_queries() -> None:
    class QuarterlyKeyless:
        source_name = "keyless_search"

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            return [
                ProviderSearchItem(
                    title="小米集团2025年全年业绩浅读",
                    url="https://finance.sina.com.cn/full-year.html",
                    content="小米集团2025年全年业绩，披露智能手机、IoT和互联网服务收入构成。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="小米Q1财报：营收下滑11%、经营利润下滑60%",
                    url="https://finance.sina.com.cn/q1.html",
                    content="小米一季度财报，营收和利润变化。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=QuarterlyKeyless())

    items = provider.search("小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务", max_results=5, topic="general")

    assert [item.title for item in items] == ["小米集团2025年全年业绩浅读"]


def test_hybrid_provider_filters_report_preview_results_when_full_report_exists() -> None:
    class PreviewKeyless:
        source_name = "keyless_search"

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            return [
                ProviderSearchItem(
                    title="小米集团2025年全年业绩浅读",
                    url="https://finance.sina.com.cn/full-year.html",
                    content="小米集团2025年全年业绩，披露智能手机、IoT和互联网服务收入构成。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="小米、美团即将公布财报，机构：港股下半年盈利或优于上半年",
                    url="https://fund.stockstar.com/preview.html",
                    content="小米、美团即将公布财报，机构展望港股盈利。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=PreviewKeyless())

    items = provider.search("小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务", max_results=5, topic="general")

    assert [item.title for item in items] == ["小米集团2025年全年业绩浅读"]


def test_hybrid_provider_filters_single_stock_noise_for_broad_market_queries() -> None:
    class BroadMarketKeyless:
        source_name = "keyless_search"

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            return [
                ProviderSearchItem(
                    title="A股市场震荡上涨 科技龙头股受资金青睐",
                    url="https://www.cs.com.cn/market.html",
                    content="A股市场震荡上涨，科技龙头股受资金青睐，资金面边际改善。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="震裕科技A股股东户数减少2817户，户均持股市值107.52万元",
                    url="https://finance.sina.com.cn/single-stock.html",
                    content="震裕科技A股股东户数变化，户均持股市值变化。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=BroadMarketKeyless())

    items = provider.search("A股 市场震荡 原因 资金面 2026", max_results=5, topic="general")

    assert [item.title for item in items] == ["A股市场震荡上涨 科技龙头股受资金青睐"]
