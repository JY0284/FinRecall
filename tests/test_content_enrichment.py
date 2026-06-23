from __future__ import annotations

from finrecall.content_enrichment import ContentEnricher, FetchResponse
from finrecall.models import ProviderSearchItem


def test_content_enricher_replaces_short_result_with_extracted_page_text() -> None:
    calls: list[str] = []

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        del headers, timeout
        calls.append(url)
        body = """
            <html>
              <head>
                <title>阿里巴巴2026财年业绩全文解读</title>
                <meta property="article:published_time" content="2026-05-15T08:30:00+08:00">
              </head>
              <body>
                <article>
                  <p>阿里巴巴发布2026财年第四季度及全年业绩，全年收入突破万亿元。</p>
                  <p>阿里云外部商业化收入同比增长40%，AI相关产品收入连续多季度增长。</p>
                  <p>管理层披露云业务、国际商业、本地生活和数字媒体业务的收入构成变化。</p>
                  <p>公司同时说明资本开支、AI基础设施投入和利润率变化对未来经营的影响。</p>
                </article>
              </body>
            </html>
        """.encode()
        return FetchResponse(url=url, status=200, headers={"content-type": "text/html"}, body=body)

    item = ProviderSearchItem(
        title="阿里巴巴财报短摘要",
        url="https://finance.publisher.local/alibaba-fy2026.html",
        content="阿里巴巴发布2026财年业绩。",
        raw={"provider": "keyless_search"},
    )

    enriched = ContentEnricher(
        fetcher=fetcher,
        min_content_chars=120,
        timeout_seconds=0.1,
    ).enrich("阿里巴巴 2026年财报 云业务 收入构成", [item])

    assert calls == ["https://finance.publisher.local/alibaba-fy2026.html"]
    assert enriched[0].title == "阿里巴巴2026财年业绩全文解读"
    assert "阿里云外部商业化收入同比增长40%" in enriched[0].content
    assert len(enriched[0].content) > len(item.content)
    assert enriched[0].date_source == "metadata"
    assert enriched[0].raw["content_enrichment"]["status"] == "enriched"


def test_content_enricher_skips_portal_and_market_data_urls() -> None:
    calls: list[str] = []

    def fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
        del headers, timeout
        calls.append(url)
        return FetchResponse(url=url, status=200, headers={}, body=b"")

    items = [
        ProviderSearchItem(
            title="占位财经新闻",
            url="https://finance.example.com/placeholder.html",
            content="占位财经新闻摘要。",
            raw={"provider": "keyless_search"},
        ),
        ProviderSearchItem(
            title="小米集团 2025年财报 收入构成",
            url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
            content="小米集团2025年财报和业务表现线索。",
            raw={"provider": "native_finance", "native_source": "xiaomi_hk_financials"},
        ),
        ProviderSearchItem(
            title="黄金ETF 518880 最新净值",
            url="https://fund.eastmoney.com/518880.html",
            content="黄金ETF 518880 最新单位净值。",
            raw={"provider": "native_finance", "native_source": "eastmoney_fund_nav"},
        ),
    ]

    enriched = ContentEnricher(fetcher=fetcher, min_content_chars=120).enrich(
        "小米集团 2025年财报 收入构成",
        items,
    )

    assert calls == []
    assert enriched == items
