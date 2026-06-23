from __future__ import annotations

from finrecall.keyless_search import (
    BingNewsRssSource,
    DuckDuckGoHtmlSource,
    FetchResponse,
    KeylessSearchHarvester,
    RobotsPolicy,
    _default_fetcher,
)
from finrecall.models import ProviderSearchItem


def test_keyless_harvester_extracts_duckduckgo_results_and_fetches_pages() -> None:
    article_url = "https://finance.example.com/news/xiaomi-2025-results.html"
    ddg_html = f"""
    <html><body>
      <div class="result">
        <a class="result__a" href="/l/?uddg={article_url}">小米集团发布2025年财报</a>
        <a class="result__snippet">营收4573亿元，智能手机、IoT、互联网服务分部披露。</a>
      </div>
    </body></html>
    """.encode()
    article_html = """
    <html>
      <head>
        <title>小米集团发布2025年财报：营收4573亿</title>
        <meta name="datePublished" content="2026-03-24T18:18:00+08:00">
      </head>
      <body>
        <article>
          <p>小米集团2025年总收入达4573亿元，同比增长25%。</p>
          <p>手机 x AIoT 分部收入3512亿元，智能电动汽车及AI等创新业务收入1061亿元。</p>
        </article>
      </body>
    </html>
    """.encode()

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://html.duckduckgo.com/html/?q="):
            return FetchResponse(url=url, status=200, headers={"content-type": "text/html"}, body=ddg_html)
        if url == article_url:
            return FetchResponse(
                url=url,
                status=200,
                headers={"content-type": "text/html"},
                body=article_html,
            )
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[DuckDuckGoHtmlSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=3,
        topic="general",
    )

    assert len(items) == 1
    assert items[0].title == "小米集团发布2025年财报：营收4573亿"
    assert items[0].url == article_url
    assert "4573亿元" in items[0].content
    assert items[0].published_at is not None
    assert items[0].raw["source_engine"] == "duckduckgo_html"
    assert items[0].raw["harvest_mode"] == "keyless_http"
    assert items[0].raw["robots_allowed"] is True


def test_keyless_harvester_skips_disallowed_search_pages() -> None:
    called: list[str] = []

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        called.append(url)
        return FetchResponse(url=url, status=200, headers={}, body=b"")

    harvester = KeylessSearchHarvester(
        sources=[DuckDuckGoHtmlSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy(disallow_all_hosts={"html.duckduckgo.com"}),
        sleep_seconds=0,
    )

    items = harvester.search("半导体 AI 芯片 最新政策", max_results=3, topic="general")

    assert items == []
    assert called == []


def test_keyless_harvester_preserves_results_when_later_source_times_out() -> None:
    class WorkingSource:
        def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return [
                ProviderSearchItem(
                    title="A股市场震荡上涨 科技龙头股受资金青睐",
                    url="https://finance.example.com/a-share-market.html",
                    content="A股市场震荡上涨，科技龙头股受资金青睐，资金面边际改善。",
                    raw={"provider": "keyless_search", "source_engine": "working_source"},
                )
            ]

    class TimeoutSource:
        def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise TimeoutError("source timed out")

    harvester = KeylessSearchHarvester(
        sources=[WorkingSource(), TimeoutSource()],
        fetcher=lambda url, headers, timeout: FetchResponse(url=url, status=200, headers={}, body=b""),
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("A股 市场震荡 原因 资金面 2026", max_results=5, topic="general")

    assert len(items) == 1
    assert items[0].url == "https://finance.example.com/a-share-market.html"


def test_default_fetcher_uses_system_proxy_opener_for_bing_rss(monkeypatch) -> None:
    class FakeResponse:
        status = 200
        headers = {"content-type": "application/xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
            return False

        def geturl(self) -> str:
            return "https://www.bing.com/news/search?q=test&format=rss"

        def read(self) -> bytes:
            return b"<rss><channel /></rss>"

    class FakeOpener:
        calls: list[str] = []

        def open(self, request, timeout):  # type: ignore[no-untyped-def]
            self.calls.append(request.full_url)
            return FakeResponse()

    opener = FakeOpener()
    monkeypatch.setattr("finrecall.keyless_search._system_proxy_opener", lambda: opener)

    def forbidden_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("global urlopen should not be used for Bing RSS")

    monkeypatch.setattr("finrecall.keyless_search.urlopen", forbidden_urlopen)

    response = _default_fetcher(
        "https://www.bing.com/news/search?q=test&format=rss",
        {"Accept": "application/rss+xml"},
        3,
    )

    assert response.status == 200
    assert response.body == b"<rss><channel /></rss>"
    assert opener.calls == ["https://www.bing.com/news/search?q=test&format=rss"]


def test_keyless_harvester_parses_bing_news_rss_and_unwraps_result_urls() -> None:
    article_url = "https://finance.example.com/news/xiaomi-results.html"
    rss_body = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>小米集团发布2025年财报：营收4573亿元</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>小米集团披露智能手机、IoT和互联网服务收入构成。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    article_html = """
    <html><head><title>小米集团发布2025年财报：营收4573亿元</title></head>
    <body><main>小米集团2025年总收入达4573亿元，手机 x AIoT 分部收入3512亿元。</main></body></html>
    """.encode()

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=rss_body)
        if url == article_url:
            return FetchResponse(url=url, status=200, headers={"content-type": "text/html"}, body=article_html)
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("小米集团 2025年财报", max_results=3, topic="general")

    assert len(items) == 1
    assert items[0].url == article_url
    assert "4573亿元" in items[0].content
    assert items[0].published_at is not None
    assert items[0].raw["source_engine"] == "bing_news_rss"


def test_bing_news_source_retries_simplified_query_when_full_query_is_empty() -> None:
    article_url = "https://www.sohu.com/a/1000861943_121885030"
    empty_rss = b"""<rss version="2.0"><channel><title>empty</title></channel></rss>"""
    hit_rss = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>小米2025年营收4573亿 创新高汽车业务盈利</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>小米集团发布2025年全年财报，全年总营收达到4573亿元。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    article_html = """
    <html><head><title>小米2025年营收4573亿 创新高汽车业务盈利</title></head>
    <body><article>小米集团发布2025年全年财报，全年总营收达到4573亿元。</article></body></html>
    """.encode()
    searched_urls: list[str] = []

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            searched_urls.append(url)
            if len(searched_urls) == 1:
                return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=empty_rss)
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=hit_rss)
        if url == article_url:
            return FetchResponse(url=url, status=200, headers={"content-type": "text/html"}, body=article_html)
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search(
        "小米集团 2025年财报 收入构成 智能手机 IoT 互联网服务",
        max_results=3,
        topic="general",
    )

    assert len(searched_urls) >= 2
    assert any("%E5%B0%8F%E7%B1%B3+2025" in url for url in searched_urls)
    assert len(items) == 1
    assert items[0].url == article_url
    assert "4573亿元" in items[0].content


def test_bing_news_source_continues_after_one_variant_timeout() -> None:
    article_url = "https://www.sohu.com/a/1000861943_121885030"
    empty_rss = b"""<rss version="2.0"><channel><title>empty</title></channel></rss>"""
    hit_rss = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>小米2025年营收4573亿 创新高汽车业务盈利</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>小米集团发布2025年全年财报，全年总营收达到4573亿元。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    calls = 0

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        nonlocal calls
        if url.startswith("https://www.bing.com/news/search?"):
            calls += 1
            if calls == 1:
                return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=empty_rss)
            if calls == 2:
                raise TimeoutError("variant timed out")
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=hit_rss)
        if url == article_url:
            return FetchResponse(
                url=url,
                status=200,
                headers={"content-type": "text/html"},
                body="<html><body>小米集团发布2025年全年财报，全年总营收达到4573亿元。</body></html>".encode(),
            )
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("小米集团 2025年财报 收入构成", max_results=3, topic="general")

    assert len(items) == 1
    assert items[0].url == article_url


def test_bing_news_source_tries_compact_policy_variants() -> None:
    article_url = "https://finance.example.com/news/ai-chip-policy.html"
    empty_rss = b"""<rss version="2.0"><channel><title>empty</title></channel></rss>"""
    hit_rss = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>技术拐点遇上资本窗口：6家A股端侧AI芯企赴港开启全球化跃迁</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>AI芯片政策和国产替代趋势影响A股半导体产业链。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    searched_urls: list[str] = []

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            searched_urls.append(url)
            body = hit_rss if "AI%E8%8A%AF%E7%89%87+A%E8%82%A1" in url else empty_rss
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=body)
        if url == article_url:
            return FetchResponse(
                url=url,
                status=200,
                headers={"content-type": "text/html"},
                body="<html><body>AI芯片政策和国产替代趋势影响A股半导体产业链。</body></html>".encode(),
            )
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("半导体 AI 芯片 最新政策 A股 影响", max_results=3, topic="general")

    assert any("AI%E8%8A%AF%E7%89%87+A%E8%82%A1" in url for url in searched_urls)
    assert len(items) == 1
    assert items[0].url == article_url


def test_bing_news_source_ranks_policy_evidence_over_market_hype() -> None:
    market_url = "https://finance.example.com/ai-chip-market.html"
    policy_url = "https://policy.example.com/ai-chip-export-control.html"
    rss_body = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>A股半导体AI芯片概念股再掀涨停潮，影响科技主线</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={market_url}</link>
          <description>半导体AI芯片概念股活跃，A股板块成交放量。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
        <item>
          <title>AI芯片出口管制政策更新，产业链影响解读</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={policy_url}</link>
          <description>监管规则和出口管制措施影响AI芯片与A股半导体产业链。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=rss_body)
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource(fetch_result_pages=False)],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("半导体 AI 芯片 最新政策 A股 影响", max_results=1, topic="general")

    assert len(items) == 1
    assert items[0].url == policy_url


def test_bing_news_source_tries_market_flow_variants() -> None:
    article_url = "https://finance.example.com/news/a-share-liquidity.html"
    empty_rss = b"""<rss version="2.0"><channel><title>empty</title></channel></rss>"""
    hit_rss = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>中信建投：A股短期有望反弹但空间有限，关注资金面后续动态</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>A股市场震荡与资金面变化相关，券商提示关注流动性。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    searched_urls: list[str] = []

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            searched_urls.append(url)
            body = hit_rss if "A%E8%82%A1+%E8%B5%84%E9%87%91%E9%9D%A2" in url else empty_rss
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=body)
        if url == article_url:
            return FetchResponse(
                url=url,
                status=200,
                headers={"content-type": "text/html"},
                body="<html><body>A股市场震荡与资金面变化相关，券商提示关注流动性。</body></html>".encode(),
            )
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("A股 市场震荡 原因 资金面 2026", max_results=3, topic="general")

    assert any("A%E8%82%A1+%E8%B5%84%E9%87%91%E9%9D%A2" in url for url in searched_urls)
    assert len(items) == 1
    assert items[0].url == article_url


def test_keyless_keeps_rss_summary_when_page_extraction_is_thin() -> None:
    article_url = "https://www.msn.cn/zh-cn/money/markets/xiaomi-2025"
    rss_body = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>小米2025年营收4573亿 创新高汽车业务盈利</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>小米集团发布2025年全年财报，全年总营收达到4573亿元，汽车业务首次实现盈利。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    thin_html = b"<html><head><title>MSN</title></head><body>MSN</body></html>"

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=rss_body)
        if url == article_url:
            return FetchResponse(url=url, status=200, headers={"content-type": "text/html"}, body=thin_html)
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("小米集团 2025年财报 收入构成", max_results=3, topic="general")

    assert len(items) == 1
    assert items[0].title == "小米2025年营收4573亿 创新高汽车业务盈利"
    assert "全年总营收达到4573亿元" in items[0].content


def test_keyless_keeps_search_title_when_extracted_title_is_url() -> None:
    article_url = "https://xueqiu.com/7302028995/377887708"
    rss_body = f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>A股市场2026年展望：乘势笃行</title>
          <link>https://www.bing.com/news/apiclick.aspx?url={article_url}</link>
          <description>A股市场2026年展望，政策、流动性和盈利修复共同影响市场。</description>
          <pubDate>Tue, 24 Mar 2026 10:18:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """.encode()
    article_html = f"""
    <html>
      <head><title>{article_url}</title></head>
      <body><article>{"A股市场2026年展望，慢牛行情延续，基本面重要性进一步上升。" * 10}</article></body>
    </html>
    """.encode()

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        if url.startswith("https://www.bing.com/news/search?"):
            return FetchResponse(url=url, status=200, headers={"content-type": "application/xml"}, body=rss_body)
        if url == article_url:
            return FetchResponse(url=url, status=200, headers={"content-type": "text/html"}, body=article_html)
        raise AssertionError(f"unexpected URL {url}")

    harvester = KeylessSearchHarvester(
        sources=[BingNewsRssSource()],
        fetcher=fetcher,
        robots_policy=RobotsPolicy.allow_all(),
        sleep_seconds=0,
    )

    items = harvester.search("A股 市场震荡 原因 资金面 2026", max_results=3, topic="general")

    assert len(items) == 1
    assert items[0].title == "A股市场2026年展望：乘势笃行"
    assert len(items[0].content) >= 200
