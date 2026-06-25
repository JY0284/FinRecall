from __future__ import annotations

from dataclasses import replace

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


class NoopEnricher:
    def enrich(
        self,
        query: str,
        items: list[ProviderSearchItem],
    ) -> list[ProviderSearchItem]:
        return items


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


def test_hybrid_provider_filters_generic_news_for_full_year_report_queries() -> None:
    class MixedXiaomiKeyless:
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
                    title="小米：跌到怀疑人生？最惨时刻已过",
                    url="https://news.qq.com/rain/a/xiaomi-market.html",
                    content="小米集团股价阶段性承压，市场关注汽车业务进展和估值修复。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="小米集团发布2025年财报：营收4573亿，同比增长25%",
                    url="https://finance.example.com/xiaomi-2025-report.html",
                    content="小米集团发布2025年全年财报，全年总营收4573亿元，披露智能手机、IoT和互联网服务收入构成。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=MixedXiaomiKeyless())

    items = provider.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=5,
        topic="general",
    )

    assert [item.title for item in items] == [
        "小米集团发布2025年财报：营收4573亿，同比增长25%"
    ]


def test_hybrid_provider_filters_quarterly_content_for_full_year_report_queries() -> None:
    class QuarterlyContentKeyless:
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
                    title="小米：跌到怀疑人生？最惨时刻已过",
                    url="https://news.qq.com/rain/a/xiaomi-q1.html",
                    content=(
                        "小米集团于2026年5月发布2026年第一季度财报，收入991亿元，"
                        "智能手机和IoT业务阶段性承压。"
                    )
                    * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
                    url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
                    content="小米集团2025年财报和业务表现线索，覆盖收入构成、智能手机、IoT、互联网服务、年报原文和管理层讨论分析。",
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=QuarterlyContentKeyless())

    items = provider.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=5,
        topic="general",
    )

    assert [item.title for item in items] == [
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务"
    ]


def test_hybrid_provider_filters_native_fallback_for_full_year_report_queries() -> None:
    class MixedNative:
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
                    title="小米：跌到怀疑人生？最惨时刻已过",
                    url="https://news.qq.com/rain/a/xiaomi-q1.html",
                    content=(
                        "小米集团于2026年5月发布2026年第一季度财报，收入991亿元，"
                        "智能手机和IoT业务阶段性承压。"
                    )
                    * 5,
                    raw={"native_source": "xiaomi_hk_financials", "provider": "native_finance"},
                ),
                ProviderSearchItem(
                    title="小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
                    url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
                    content="小米集团2025年财报和业务表现线索，覆盖收入构成、智能手机、IoT、互联网服务、年报原文和管理层讨论分析。",
                    raw={"native_source": "xiaomi_hk_financials", "provider": "native_finance"},
                ),
            ]

    class EmptyKeyless:
        source_name = "keyless_search"

        def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return []

    provider = HybridSearchProvider(native_provider=MixedNative(), keyless_provider=EmptyKeyless())

    items = provider.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=5,
        topic="general",
    )

    assert [item.title for item in items] == [
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务"
    ]


def test_hybrid_provider_keeps_full_year_results_that_also_mention_fourth_quarter() -> None:
    class AlibabaAnnualKeyless:
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
                    title="阿里巴巴2026财年营收破万亿，AI商业化加速，云业务增长40%",
                    url="https://finance.sina.com.cn/alibaba-fy2026.html",
                    content=(
                        "阿里巴巴发布2026财年第四季度及全年财报，全年收入破万亿，"
                        "阿里云外部商业化收入增长40%，AI相关云业务收入占比提升。"
                    )
                    * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="2026 Q1财报解读：阿里、腾讯、京东的三种AI焦虑",
                    url="https://36kr.com/p/alibaba-q1.html",
                    content="阿里巴巴、腾讯、京东一季度财报解读，云业务与AI资本开支焦虑。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=AlibabaAnnualKeyless())

    items = provider.search("阿里巴巴 2026年财报 云业务 收入构成", max_results=5, topic="general")

    assert [item.title for item in items] == [
        "阿里巴巴2026财年营收破万亿，AI商业化加速，云业务增长40%"
    ]


def test_hybrid_provider_uses_native_when_keyless_only_has_temporal_mismatches() -> None:
    class ReportNative:
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
                    title="小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
                    url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
                    content="小米集团2025年财报和业务表现线索，覆盖收入构成、智能手机、IoT、互联网服务、年报原文和管理层讨论分析。",
                    raw={"native_source": "xiaomi_hk_financials", "provider": "native_finance"},
                )
            ]

    class OnlyQuarterlyKeyless:
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
                    title="小米：跌到怀疑人生？最惨时刻已过",
                    url="https://news.qq.com/rain/a/xiaomi-q1.html",
                    content="小米集团于2026年5月发布2026年第一季度财报，收入991亿元，智能手机和IoT业务阶段性承压。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                )
            ]

    provider = HybridSearchProvider(native_provider=ReportNative(), keyless_provider=OnlyQuarterlyKeyless())

    items = provider.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=5,
        topic="general",
    )

    assert [item.title for item in items] == [
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务"
    ]


def test_hybrid_provider_filters_other_companies_for_subtopic_earnings_queries() -> None:
    class MixedAlibabaKeyless:
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
                    title="美团发布2026年财报，本地生活业务收入增长",
                    url="https://finance.example.com/meituan-2026.html",
                    content="美团发布2026年财报，本地生活、到店酒旅和新业务收入构成变化。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="阿里巴巴2026财年业绩：阿里云收入恢复增长",
                    url="https://finance.example.com/alibaba-cloud-earnings.html",
                    content="阿里巴巴发布2026财年业绩，阿里云收入同比增长，AI相关云业务需求持续提升。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=MixedAlibabaKeyless())

    items = provider.search("阿里巴巴 2026年财报 云业务 收入构成", max_results=5, topic="general")

    assert [item.title for item in items] == ["阿里巴巴2026财年业绩：阿里云收入恢复增长"]


def test_hybrid_provider_prefers_policy_evidence_over_market_hype_for_policy_queries() -> None:
    class MixedPolicyKeyless:
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
                    title="AI芯片概念股再掀涨停潮，半导体板块成交放量",
                    url="https://finance.example.com/ai-chip-market.html",
                    content="AI芯片概念股活跃，A股半导体板块成交放量，资金持续关注国产替代。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="AI芯片出口管制政策更新，A股半导体产业链影响解读",
                    url="https://policy.example.com/ai-chip-export-control.html",
                    content="出口管制政策、监管规则和国产替代措施影响AI芯片与A股半导体产业链。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=MixedPolicyKeyless())

    items = provider.search("半导体 AI 芯片 最新政策 A股 影响", max_results=5, topic="general")

    assert [item.title for item in items] == [
        "AI芯片出口管制政策更新，A股半导体产业链影响解读"
    ]


def test_hybrid_provider_does_not_treat_policy_booster_language_as_policy_evidence() -> None:
    class GenericPolicyHypeKeyless:
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
                    title="芯片大消息！A股半导体主线彻底引爆",
                    url="https://news.qq.com/rain/a/ai-chip-market.html",
                    content="A股半导体板块爆发，多重政策利好共振，AI算力与国产替代两大主线活跃。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="芯片换现金？美国对华科技销售政策分歧何去何从",
                    url="https://www.businesstimes.com.sg/zh-hans/chip-policy.html",
                    content="美国限制对华半导体销售的政策分歧，涉及国家安全、监管限制和AI芯片产业链影响。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=GenericPolicyHypeKeyless())

    items = provider.search("半导体 AI 芯片 最新政策 A股 影响", max_results=5, topic="general")

    assert [item.title for item in items] == [
        "芯片换现金？美国对华科技销售政策分歧何去何从"
    ]


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


def test_hybrid_provider_filters_etf_housekeeping_for_broad_market_queries() -> None:
    class BroadMarketWithEtfKeyless:
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
                    title="A500指数ETF（159352）资金流入，最新净值更新",
                    url="https://fund.example.com/a500-etf.html",
                    content="A500指数ETF最新净值和资金流入变化，跟踪宽基指数。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="2026年A股市场展望：资金面改善与盈利修复驱动慢牛",
                    url="https://strategy.example.com/a-share-2026-outlook.html",
                    content="券商策略报告复盘A股市场震荡原因，认为资金面改善、政策支持和盈利修复驱动慢牛行情。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    provider = HybridSearchProvider(native_provider=NativeStub(), keyless_provider=BroadMarketWithEtfKeyless())

    items = provider.search("A股 市场震荡 原因 资金面 2026", max_results=5, topic="general")

    assert [item.title for item in items] == [
        "2026年A股市场展望：资金面改善与盈利修复驱动慢牛"
    ]


def test_hybrid_provider_applies_reranker_after_hard_filters() -> None:
    class AlibabaKeyless:
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
                    title="2026 Q1财报解读：阿里、腾讯、京东的三种AI焦虑",
                    url="https://36kr.com/p/alibaba-q1.html",
                    content="阿里巴巴2026财年第四季度及全年财报解读，阿里云业务与AI资本开支焦虑。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
                ProviderSearchItem(
                    title="阿里巴巴2026财年营收破万亿，AI商业化加速，云业务增长40%",
                    url="https://finance.sina.com.cn/alibaba-fy2026.html",
                    content="阿里巴巴2026财年第四季度及全年财报显示，阿里云外部客户收入增长40%。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                ),
            ]

    class SelectingReranker:
        calls: list[list[str]] = []

        def rerank(
            self,
            query: str,
            items: list[ProviderSearchItem],
            *,
            max_results: int,
        ) -> list[ProviderSearchItem]:
            self.calls.append([item.title for item in items])
            return [item for item in items if "云业务增长40%" in item.title]

    reranker = SelectingReranker()
    provider = HybridSearchProvider(
        native_provider=NativeStub(),
        keyless_provider=AlibabaKeyless(),
        reranker=reranker,
    )

    items = provider.search("阿里巴巴 2026年财报 云业务 收入构成", max_results=5, topic="general")

    assert [item.title for item in items] == [
        "阿里巴巴2026财年营收破万亿，AI商业化加速，云业务增长40%"
    ]
    assert len(reranker.calls) == 1
    assert all("港股市场 行情" not in title for title in reranker.calls[0])


def test_hybrid_provider_does_not_call_reranker_for_data_queries() -> None:
    class FailingReranker:
        def rerank(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("reranker should not run for data-only queries")

    provider = HybridSearchProvider(
        native_provider=NativeStub(),
        keyless_provider=KeylessStub(),
        reranker=FailingReranker(),
    )

    items = provider.search("黄金ETF 518880 最新净值 单位净值", max_results=2, topic="general")

    assert len(items) == 1
    assert items[0].url == "https://news.futunn.com/hk"


def test_hybrid_provider_enriches_final_short_content_results() -> None:
    class ShortContentKeyless:
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
                    title="阿里巴巴2026财年业绩短讯",
                    url="https://finance.example.com/alibaba-fy2026.html",
                    content="阿里巴巴发布2026财年业绩。",
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                )
            ]

    class SpyEnricher:
        calls: list[tuple[str, list[str]]] = []

        def enrich(
            self,
            query: str,
            items: list[ProviderSearchItem],
        ) -> list[ProviderSearchItem]:
            self.calls.append((query, [item.title for item in items]))
            return [
                replace(
                    item,
                    content=item.content + " 阿里云外部商业化收入同比增长40%，全年收入构成披露充分。" * 5,
                )
                for item in items
            ]

    enricher = SpyEnricher()
    provider = HybridSearchProvider(
        native_provider=NativeStub(),
        keyless_provider=ShortContentKeyless(),
        reranker=None,
        content_enricher=enricher,
    )

    items = provider.search("阿里巴巴 2026年财报 云业务 收入构成", max_results=3, topic="general")

    assert len(enricher.calls) == 1
    assert enricher.calls[0][1] == ["阿里巴巴2026财年业绩短讯"]
    assert "阿里云外部商业化收入同比增长40%" in items[0].content


def test_hybrid_provider_does_not_enrich_data_queries() -> None:
    class FailingEnricher:
        def enrich(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("content enrichment should not run for data-only queries")

    provider = HybridSearchProvider(
        native_provider=NativeStub(),
        keyless_provider=KeylessStub(),
        reranker=None,
        content_enricher=FailingEnricher(),
    )

    items = provider.search("黄金ETF 518880 最新净值 单位净值", max_results=2, topic="general")

    assert len(items) == 1
    assert items[0].url == "https://news.futunn.com/hk"


def test_hybrid_provider_runs_keyless_for_stock_disclosure_event_queries() -> None:
    class NoticeNative:
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
                    title="华峰测控(688200) 2026年6月 公司公告 信息披露 - 上海证券交易所",
                    url="https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=688200",
                    content="上海证券交易所官方信息披露入口，按证券代码查询华峰测控 688200 公司公告。",
                    raw={"native_source": "sse_notice", "provider": "native_finance"},
                )
            ]

    class DisclosureKeyless:
        source_name = "keyless_search"
        called = False

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            self.called = True
            return [
                ProviderSearchItem(
                    title="华峰测控：股东询价转让计划书",
                    url="https://paper.cnstock.com/html/2026-05/27/content_2222303.htm",
                    content=(
                        "证券代码：688200 证券简称：华峰测控。股东询价转让计划书，"
                        "1,355,596股，不属于通过二级市场减持，受让后6个月内不得转让。"
                    )
                    * 4,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                )
            ]

    keyless = DisclosureKeyless()
    provider = HybridSearchProvider(
        native_provider=NoticeNative(),
        keyless_provider=keyless,
        reranker=None,
        content_enricher=NoopEnricher(),
    )

    items = provider.search("华峰测控 688200 减持 限售解禁 风险 2026年6月", max_results=3, topic="general")

    assert keyless.called
    assert items[0].title == "华峰测控：股东询价转让计划书"
    assert "不属于通过二级市场减持" in items[0].content


def test_hybrid_provider_runs_keyless_for_stock_listing_disclosure_synonyms() -> None:
    class NoticeNative:
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
                    title="华峰测控(688200) 2026年6月 公司公告 信息披露 - 上海证券交易所",
                    url="https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=688200",
                    content="上海证券交易所官方信息披露入口，按证券代码查询华峰测控 688200 公司公告。",
                    raw={"native_source": "sse_notice", "provider": "native_finance"},
                )
            ]

    class ListingKeyless:
        source_name = "keyless_search"
        called = False

        def search(
            self,
            query: str,
            *,
            max_results: int,
            topic: str,
            time_window: str | None = None,
        ) -> list[ProviderSearchItem]:
            self.called = True
            return [
                ProviderSearchItem(
                    title="华峰测控：限制性股票归属结果暨股票上市",
                    url="https://finance.example.com/688200-stock-listing.html",
                    content=(
                        "证券代码：688200 证券简称：华峰测控。限制性股票归属结果，"
                        "本次归属股票上市流通日期为2026年5月26日。"
                    )
                    * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                )
            ]

    keyless = ListingKeyless()
    provider = HybridSearchProvider(
        native_provider=NoticeNative(),
        keyless_provider=keyless,
        reranker=None,
        content_enricher=NoopEnricher(),
    )

    items = provider.search("华峰测控 688200 限制性股票归属结果 上市流通 2026年6月", max_results=3, topic="general")

    assert keyless.called
    assert items[0].title == "华峰测控：限制性股票归属结果暨股票上市"
    assert "上市流通日期" in items[0].content


def test_hybrid_provider_filters_wrong_company_keyless_for_stock_event_queries() -> None:
    class NoticeNative:
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
                    title="华峰测控(688200) 2026年6月 公司公告 信息披露 - 上海证券交易所",
                    url="https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=688200",
                    content="上海证券交易所官方信息披露入口，按证券代码查询华峰测控 688200 公司公告。",
                    raw={"native_source": "sse_notice", "provider": "native_finance"},
                )
            ]

    class WrongCompanyKeyless:
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
                    title="华丰科技6月限售股解禁规模居前",
                    url="https://finance.sina.com.cn/wrong-company.html",
                    content="华丰科技6月限售股解禁，电子行业解禁风险受到市场关注。" * 5,
                    raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
                )
            ]

    provider = HybridSearchProvider(
        native_provider=NoticeNative(),
        keyless_provider=WrongCompanyKeyless(),
        reranker=None,
        content_enricher=NoopEnricher(),
    )

    items = provider.search("华峰测控 688200 减持 限售解禁 风险 2026年6月", max_results=3, topic="general")

    assert all("华丰科技" not in item.title for item in items)
    assert items[0].raw["hybrid_sources"] == ["native_finance"]


def test_hybrid_provider_skips_keyless_when_native_disclosure_body_is_available() -> None:
    class BodyNative:
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
                    title="华峰测控：股东询价转让计划书",
                    url="https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=688200&id=12355654",
                    content=(
                        "证券代码：688200 证券简称：华峰测控。股东询价转让计划书，"
                        "1,355,596股，不属于通过二级市场减持，受让后6个月内不得转让。"
                    )
                    * 8,
                    raw={"native_source": "sina_notice_body", "provider": "native_finance"},
                )
            ]

    class SpyKeyless:
        source_name = "keyless_search"
        called = False

        def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.called = True
            return []

    keyless = SpyKeyless()
    provider = HybridSearchProvider(
        native_provider=BodyNative(),
        keyless_provider=keyless,
        reranker=None,
        content_enricher=NoopEnricher(),
    )

    items = provider.search("华峰测控 688200 减持 限售解禁 风险 2026年6月", max_results=3, topic="general")

    assert not keyless.called
    assert items[0].raw["hybrid_sources"] == ["native_finance"]
    assert "不属于通过二级市场减持" in items[0].content
