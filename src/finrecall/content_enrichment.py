from __future__ import annotations

from dataclasses import dataclass, replace
import os
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from finrecall.extract import extract_document
from finrecall.models import ProviderSearchItem
from finrecall.utils import source_domain


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 FinRecallEnricher/0.1"
)
DEFAULT_MIN_CONTENT_CHARS = 500
DEFAULT_MAX_ENRICHED_RESULTS = 2
DEFAULT_TIMEOUT_SECONDS = 3.0

MARKET_DATA_NATIVE_SOURCES = {
    "eastmoney_quote",
    "sina_quote",
    "eastmoney_kline",
    "eastmoney_realtime_quote",
    "eastmoney_fund",
    "eastmoney_fund_nav",
    "eastmoney_fund_ranking",
    "eastmoney_moneyflow_stock",
    "eastmoney_moneyflow",
    "eastmoney_industry_moneyflow",
    "sina_moneyflow",
    "10jqka_moneyflow",
    "futu_hk_market",
    "yahoo_hk_market",
}
LOW_VALUE_URL_MARKERS = (
    "quote.eastmoney.com/",
    "fund.eastmoney.com/",
    "xueqiu.com/s/",
    "vip.stock.finance.sina.com.cn/quotes_service/",
    "finance.sina.com.cn/realstock/",
    "basic.10jqka.com.cn/",
    "stockpage.10jqka.com.cn/",
    "data.eastmoney.com/",
    "so.eastmoney.com/",
    "search/titlesearch.xhtml",
    "sse.com.cn/assortment/stock/list/info/announcement/index.shtml",
    "cninfo.com.cn/new/disclosure/stock",
    "emweb.securities.eastmoney.com/pc_hsf10/",
    "/search?",
    "/search/",
)

FetchFn = Callable[[str, dict[str, str], float], "FetchResponse"]


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


class ContentEnricher:
    """Fetch selected short article results so agents receive source text."""

    def __init__(
        self,
        *,
        fetcher: FetchFn | None = None,
        min_content_chars: int = DEFAULT_MIN_CONTENT_CHARS,
        max_results: int = DEFAULT_MAX_ENRICHED_RESULTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.fetcher = fetcher or _default_fetcher
        self.min_content_chars = max(80, int(min_content_chars))
        self.max_results = max(0, int(max_results))
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    @classmethod
    def from_env(cls) -> "ContentEnricher | None":
        if not _env_flag("FINRECALL_CONTENT_ENRICHMENT", default=True):
            return None
        return cls(
            min_content_chars=_env_int(
                "FINRECALL_ENRICH_MIN_CONTENT_CHARS",
                DEFAULT_MIN_CONTENT_CHARS,
            ),
            max_results=_env_int(
                "FINRECALL_ENRICH_MAX_RESULTS",
                DEFAULT_MAX_ENRICHED_RESULTS,
            ),
            timeout_seconds=_env_float(
                "FINRECALL_ENRICH_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
            ),
        )

    def enrich(
        self,
        query: str,
        items: list[ProviderSearchItem],
    ) -> list[ProviderSearchItem]:
        del query
        if not items or self.max_results <= 0:
            return items

        enriched: list[ProviderSearchItem] = []
        attempted = 0
        for item in items:
            if attempted < self.max_results and self._should_enrich(item):
                attempted += 1
                enriched.append(self._enrich_item(item))
            else:
                enriched.append(item)
        return enriched

    def _should_enrich(self, item: ProviderSearchItem) -> bool:
        if _content_len(item.content) >= self.min_content_chars:
            return False
        if _is_market_data_item(item):
            return False
        parsed = urlsplit(item.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        if _is_reserved_example_host(parsed.netloc):
            return False
        if parsed.path.lower().endswith(".pdf"):
            return False
        return not _is_low_value_url(item.url)

    def _enrich_item(self, item: ProviderSearchItem) -> ProviderSearchItem:
        try:
            response = self.fetcher(item.url, _browser_headers(), self.timeout_seconds)
        except Exception:  # noqa: BLE001
            return item
        if response.status >= 400 or not _is_html_response(response.headers):
            return item

        extracted = extract_document(response.url, response.body, headers=response.headers)
        if not _is_richer_content(extracted.content, item.content, self.min_content_chars):
            return item

        raw = dict(item.raw)
        raw["content_enrichment"] = {
            "status": "enriched",
            "source": "page_fetch",
            "content_chars": _content_len(extracted.content),
            "previous_content_chars": _content_len(item.content),
            "source_domain": source_domain(extracted.url),
        }
        return replace(
            item,
            title=extracted.title if _is_useful_title(extracted.title, extracted.url) else item.title,
            url=extracted.url or item.url,
            content=extracted.content,
            published_at=extracted.published_at or item.published_at,
            updated_at=extracted.updated_at or item.updated_at,
            raw_date_text=extracted.raw_date_text or item.raw_date_text,
            date_source=extracted.date_source if extracted.published_at else item.date_source,
            date_confidence=extracted.date_confidence if extracted.published_at else item.date_confidence,
            raw=raw,
        )


def _default_fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return FetchResponse(
            url=response.geturl(),
            status=response.status,
            headers={str(key).lower(): str(value) for key, value in response.headers.items()},
            body=response.read(),
        )


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Cache-Control": "no-cache",
    }


def _is_html_response(headers: dict[str, str]) -> bool:
    content_type = ""
    for key, value in headers.items():
        if key.lower() == "content-type":
            content_type = value.lower()
            break
    return "text/html" in content_type or "application/xhtml+xml" in content_type or not content_type


def _is_richer_content(extracted_content: str, current_content: str, min_content_chars: int) -> bool:
    extracted_len = _content_len(extracted_content)
    current_len = _content_len(current_content)
    minimum_accepted = min(min_content_chars, 160)
    return extracted_len >= minimum_accepted and extracted_len > current_len


def _is_useful_title(title: str, url: str) -> bool:
    cleaned = " ".join(title.split())
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    domain = source_domain(url).lower()
    return lowered not in {domain, domain.removeprefix("www.")}


def _is_market_data_item(item: ProviderSearchItem) -> bool:
    native_source = str(item.raw.get("native_source") or "")
    if native_source in MARKET_DATA_NATIVE_SOURCES:
        return True
    title = item.title.upper()
    return "ETF" in title and any(term in item.title for term in ("净值", "基金", "行情"))


def _is_low_value_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in LOW_VALUE_URL_MARKERS)


def _is_reserved_example_host(host: str) -> bool:
    host = host.lower().split("@")[-1].split(":", 1)[0]
    return host in {"example.com", "example.net", "example.org"} or host.endswith(
        (".example.com", ".example.net", ".example.org")
    )


def _content_len(value: str) -> int:
    return len(" ".join(value.split()))


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
