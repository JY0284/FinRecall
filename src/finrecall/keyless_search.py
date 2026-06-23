from __future__ import annotations

import base64
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import os
import re
import time
from typing import Callable, Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen
import urllib.request
import xml.etree.ElementTree as ET

from finrecall.extract import extract_document
from finrecall.models import ProviderSearchItem
from finrecall.utils import canonicalize_url, parse_datetime_text, source_domain


FetchFn = Callable[[str, dict[str, str], float], "FetchResponse"]


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 FinRecallKeyless/0.1"
)
QUERY_NOISE_TERMS = {
    "收入构成",
    "智能手机",
    "互联网服务",
    "最新政策",
    "资金面",
    "行情",
    "走势",
    "市场新闻",
    "市场",
    "最新",
    "新闻",
    "消息",
}
MIN_USEFUL_EXTRACTED_CONTENT_CHARS = 80
POLICY_EVIDENCE_TERMS = (
    "政策更新",
    "政策解读",
    "政策分歧",
    "管制",
    "监管",
    "限制",
    "制裁",
    "法规",
    "办法",
    "意见",
    "通知",
    "指引",
    "方案",
    "合规",
    "解读",
)
MARKET_HYPE_TERMS = ("概念股", "涨停", "成交放量", "板块活跃", "资金追捧")


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class SearchCandidate:
    title: str
    url: str
    snippet: str
    source_engine: str
    published_text: str | None = None


class RobotsPolicy:
    """Small robots.txt checker for keyless sources.

    The parser intentionally supports only Allow/Disallow rules for user-agent
    groups that include "*". Unknown or unreachable robots files are treated as
    allow so transient robots failures do not make search unusable.
    """

    def __init__(
        self,
        *,
        disallow_all_hosts: Iterable[str] | None = None,
        fetcher: Callable[[str], bytes] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 2.0,
        assume_allowed: bool = False,
    ) -> None:
        self.disallow_all_hosts = {host.lower() for host in (disallow_all_hosts or [])}
        self.fetcher = fetcher or self._default_fetcher
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.assume_allowed = assume_allowed
        self._cache: dict[str, tuple[list[str], list[str]]] = {}

    @classmethod
    def allow_all(cls) -> "RobotsPolicy":
        return cls(assume_allowed=True)

    def is_allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = parsed.netloc.lower()
        if self.assume_allowed:
            return True
        if host in self.disallow_all_hosts:
            return False
        if not host:
            return False
        allows, disallows = self._rules_for(parsed.scheme or "https", host)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        allow_match = _longest_prefix_match(path, allows)
        disallow_match = _longest_prefix_match(path, disallows)
        return allow_match >= disallow_match

    def _rules_for(self, scheme: str, host: str) -> tuple[list[str], list[str]]:
        cached = self._cache.get(host)
        if cached is not None:
            return cached
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            text = self.fetcher(robots_url).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            rules = ([], [])
        else:
            rules = _parse_star_robots_rules(text)
        self._cache[host] = rules
        return rules

    def _default_fetcher(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()


class DuckDuckGoHtmlSource:
    source_engine = "duckduckgo_html"
    base_url = "https://html.duckduckgo.com/html/"

    def __init__(self, *, fetch_result_pages: bool = True) -> None:
        self.fetch_result_pages = fetch_result_pages

    def search(
        self,
        query: str,
        *,
        max_results: int,
        fetcher: FetchFn,
        robots_policy: RobotsPolicy,
        timeout_seconds: float,
        sleep_seconds: float,
    ) -> list[ProviderSearchItem]:
        search_url = f"{self.base_url}?q={quote_plus(query)}"
        if not robots_policy.is_allowed(search_url):
            return []

        response = fetcher(search_url, _browser_headers(), timeout_seconds)
        if response.status >= 400:
            return []

        candidates = _parse_duckduckgo_html(response.body, base_url=response.url)
        return _candidates_to_items(
            candidates[: max_results * 2],
            max_results=max_results,
            fetcher=fetcher,
            robots_policy=robots_policy,
            timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds,
            fetch_result_pages=self.fetch_result_pages,
        )


class BingNewsRssSource:
    source_engine = "bing_news_rss"
    base_url = "https://www.bing.com/news/search"

    def __init__(self, *, fetch_result_pages: bool = True) -> None:
        self.fetch_result_pages = fetch_result_pages

    def search(
        self,
        query: str,
        *,
        max_results: int,
        fetcher: FetchFn,
        robots_policy: RobotsPolicy,
        timeout_seconds: float,
        sleep_seconds: float,
    ) -> list[ProviderSearchItem]:
        candidates: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        for variant in _query_variants(query):
            search_url = f"{self.base_url}?q={quote_plus(variant)}&format=rss"
            if not robots_policy.is_allowed(search_url):
                continue

            try:
                response = fetcher(
                    search_url,
                    _browser_headers(accept="application/rss+xml,application/xml"),
                    timeout_seconds,
                )
            except Exception:  # noqa: BLE001
                continue
            if response.status >= 400:
                continue

            for candidate in _parse_bing_news_rss(response.body):
                canonical = canonicalize_url(candidate.url)
                if canonical in seen_urls:
                    continue
                seen_urls.add(canonical)
                candidates.append(candidate)

        candidates = sorted(
            candidates,
            key=lambda candidate: _candidate_relevance(candidate, query),
            reverse=True,
        )
        return _candidates_to_items(
            candidates[: max_results * 2],
            max_results=max_results,
            fetcher=fetcher,
            robots_policy=robots_policy,
            timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds,
            fetch_result_pages=self.fetch_result_pages,
        )


class KeylessSearchHarvester:
    source_name = "keyless_search"

    def __init__(
        self,
        *,
        sources: list[AnyKeylessSource] | None = None,
        fetcher: FetchFn | None = None,
        robots_policy: RobotsPolicy | None = None,
        timeout_seconds: float | None = None,
        sleep_seconds: float | None = None,
    ) -> None:
        self.sources = sources or [BingNewsRssSource(), DuckDuckGoHtmlSource()]
        self.fetcher = fetcher or _default_fetcher
        self.robots_policy = robots_policy or RobotsPolicy()
        self.timeout_seconds = timeout_seconds or float(
            os.environ.get("FINRECALL_KEYLESS_TIMEOUT_SECONDS", "5")
        )
        self.sleep_seconds = (
            float(os.environ.get("FINRECALL_KEYLESS_SLEEP_SECONDS", "0.2"))
            if sleep_seconds is None
            else sleep_seconds
        )

    def search(
        self,
        query: str,
        *,
        max_results: int,
        topic: str,
        time_window: str | None = None,
    ) -> list[ProviderSearchItem]:
        del topic, time_window
        max_results = min(max(1, int(max_results)), 10)
        results: list[ProviderSearchItem] = []
        seen: set[str] = set()
        for source in self.sources:
            try:
                source_items = source.search(
                    query,
                    max_results=max_results,
                    fetcher=self.fetcher,
                    robots_policy=self.robots_policy,
                    timeout_seconds=self.timeout_seconds,
                    sleep_seconds=self.sleep_seconds,
                )
            except Exception:  # noqa: BLE001
                continue
            for item in source_items:
                canonical = canonicalize_url(item.url)
                if canonical in seen:
                    continue
                seen.add(canonical)
                results.append(item)
                if len(results) >= max_results:
                    return results
        return results


class _DuckDuckGoParser(HTMLParser):
    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.results: list[SearchCandidate] = []
        self._active: str | None = None
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        css_class = attr.get("class", "")
        if tag.lower() == "a" and "result__a" in css_class:
            self._active = "title"
            self._href = attr.get("href", "")
            self._text = []
        elif tag.lower() in {"a", "div"} and "result__snippet" in css_class:
            self._active = "snippet"
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in {"a", "div"} or not self._active:
            return
        text = _clean_text(" ".join(self._text))
        if self._active == "title" and self._href and text:
            url = _unwrap_duckduckgo_url(self._href, self.base_url)
            if url:
                self.results.append(
                    SearchCandidate(
                        title=text,
                        url=url,
                        snippet="",
                        source_engine=DuckDuckGoHtmlSource.source_engine,
                    )
                )
        elif self._active == "snippet" and self.results and text:
            last = self.results[-1]
            self.results[-1] = SearchCandidate(
                title=last.title,
                url=last.url,
                snippet=text,
                source_engine=last.source_engine,
            )
        self._active = None
        self._href = ""
        self._text = []


def _parse_duckduckgo_html(body: bytes, *, base_url: str) -> list[SearchCandidate]:
    parser = _DuckDuckGoParser(base_url=base_url)
    parser.feed(body.decode("utf-8", errors="replace"))
    return [
        candidate
        for candidate in parser.results
        if candidate.url.startswith(("http://", "https://")) and not _is_low_value_url(candidate.url)
    ]


AnyKeylessSource = BingNewsRssSource | DuckDuckGoHtmlSource


def _parse_bing_news_rss(body: bytes) -> list[SearchCandidate]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    candidates: list[SearchCandidate] = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title") or "")
        link = _unwrap_bing_news_url(item.findtext("link") or "")
        snippet = _clean_text(item.findtext("description") or "")
        published_text = _clean_text(item.findtext("pubDate") or "")
        if not title or not link or _is_low_value_url(link):
            continue
        candidates.append(
            SearchCandidate(
                title=title,
                url=link,
                snippet=snippet,
                source_engine=BingNewsRssSource.source_engine,
                published_text=published_text or None,
            )
        )
    return candidates


def _query_variants(query: str) -> list[str]:
    raw = " ".join(query.split())
    variants: list[str] = []
    _append_unique(variants, raw)

    tokens = [token for token in re.split(r"[\s,，、;；|/]+", raw) if token]
    filtered = [
        token
        for token in tokens
        if token not in QUERY_NOISE_TERMS and token.lower() not in QUERY_NOISE_TERMS
    ]
    if 2 <= len(filtered) < len(tokens):
        _append_unique(variants, " ".join(filtered))

    years = re.findall(r"20\d{2}", raw)
    company = tokens[0] if tokens else ""
    company_alias = _company_alias(company)
    if company and years and any(term in raw for term in ("财报", "年报", "业绩", "收入", "营收")):
        if company_alias != company:
            _append_unique(variants, f"{company_alias} {years[0]} 财报 营收")
            _append_unique(variants, f"{company_alias} {years[0]} 全年业绩")
        _append_unique(variants, f"{company} {years[0]} 财报 营收")
        _append_unique(variants, f"{company} {years[0]} 全年业绩")
    if company and years and any(term in raw for term in ("市场震荡", "暴跌", "大跌", "上涨", "下跌")):
        _append_unique(variants, f"{company} {years[0]} 市场震荡")

    if any(term in raw for term in ("政策", "出口管制", "国产替代", "产业链", "影响")):
        if "AI" in raw.upper() and "芯片" in raw and ("A股" in raw or "a股" in raw.lower()):
            _append_unique(variants, "AI芯片 A股")
        if "AI" in raw.upper() and "芯片" in raw:
            _append_unique(variants, "AI芯片 政策")
            _append_unique(variants, "AI芯片 出口管制 政策")
        if "芯片" in raw:
            _append_unique(variants, "芯片 政策")
        if "半导体" in raw and ("A股" in raw or "a股" in raw.lower()):
            _append_unique(variants, "半导体 A股 政策")
        if "半导体" in raw:
            _append_unique(variants, "半导体 政策")
            _append_unique(variants, "半导体 政策 解读")

    if "A股" in raw or "a股" in raw.lower():
        if "资金面" in raw:
            _append_unique(variants, "A股 资金面")
        if any(term in raw for term in ("震荡", "市场震荡", "大跌", "暴跌", "调整")):
            _append_unique(variants, "A股 震荡 原因")
        if years and any(term in raw for term in ("展望", "走势", "市场震荡", "资金面", "原因")):
            _append_unique(variants, f"{years[0]} A股 展望")
            _append_unique(variants, f"A股 慢牛 {years[0]}")

    return variants[:8]


def _company_alias(company: str) -> str:
    alias = company
    for suffix in ("集团", "股份", "控股", "有限", "公司"):
        if alias.endswith(suffix) and len(alias) > len(suffix) + 1:
            alias = alias[: -len(suffix)]
    return alias or company


def _append_unique(values: list[str], value: str) -> None:
    cleaned = " ".join(value.split())
    if cleaned and cleaned not in values:
        values.append(cleaned)


def _candidate_relevance(candidate: SearchCandidate, query: str) -> float:
    tokens = [
        token.lower()
        for token in re.split(r"[\s,，、;；|/]+", query)
        if len(token) >= 2 and token not in QUERY_NOISE_TERMS
    ]
    blob = f"{candidate.title}\n{candidate.snippet}".lower()
    matched = sum(1 for token in tokens if token in blob)
    score = matched / len(tokens) if tokens else 0.0
    if candidate.published_text:
        score += 0.2
    if any(term in blob for term in ("财报", "年报", "业绩", "营收", "收入")):
        score += 0.2
    if _query_requests_policy_context(query):
        if any(term.lower() in blob for term in POLICY_EVIDENCE_TERMS):
            score += 0.7
        elif any(term.lower() in blob for term in MARKET_HYPE_TERMS):
            score -= 0.4
    return score


def _query_requests_policy_context(query: str) -> bool:
    return any(
        term in query
        for term in ("政策", "管制", "监管", "规则", "措施", "国产替代", "产业链")
    )


def _candidates_to_items(
    candidates: list[SearchCandidate],
    *,
    max_results: int,
    fetcher: FetchFn,
    robots_policy: RobotsPolicy,
    timeout_seconds: float,
    sleep_seconds: float,
    fetch_result_pages: bool,
) -> list[ProviderSearchItem]:
    items: list[ProviderSearchItem] = []
    seen: set[str] = set()
    for candidate in candidates:
        canonical = canonicalize_url(candidate.url)
        if canonical in seen:
            continue
        seen.add(canonical)
        if not robots_policy.is_allowed(candidate.url):
            continue

        item = _candidate_to_item(
            candidate,
            fetcher=fetcher,
            timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds,
            fetch_result_pages=fetch_result_pages,
        )
        items.append(item)
        if len(items) >= max_results:
            break
    return items


def _candidate_to_item(
    candidate: SearchCandidate,
    *,
    fetcher: FetchFn,
    timeout_seconds: float,
    sleep_seconds: float,
    fetch_result_pages: bool,
) -> ProviderSearchItem:
    title = candidate.title
    content = candidate.snippet
    published_at = None
    updated_at = None
    raw_date_text = None
    date_source = "none"
    date_confidence = 0.0
    final_url = candidate.url
    fetch_latency_ms = 0

    if candidate.published_text:
        published_at = parse_datetime_text(candidate.published_text)
        if published_at:
            raw_date_text = candidate.published_text
            date_source = "provider"
            date_confidence = 0.7

    if fetch_result_pages:
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        start = time.monotonic()
        try:
            response = fetcher(candidate.url, _browser_headers(), timeout_seconds)
            fetch_latency_ms = int((time.monotonic() - start) * 1000)
            if response.status < 400 and _is_html_response(response.headers):
                extracted = extract_document(response.url, response.body, headers=response.headers)
                final_url = extracted.url
                if _is_useful_extracted_content(extracted.content, content):
                    if _is_useful_extracted_title(extracted.title, final_url):
                        title = extracted.title
                    content = extracted.content or content
                    published_at = extracted.published_at
                    updated_at = extracted.updated_at
                    raw_date_text = extracted.raw_date_text
                    date_source = extracted.date_source
                    date_confidence = extracted.date_confidence
        except Exception:  # noqa: BLE001
            fetch_latency_ms = int((time.monotonic() - start) * 1000)

    return ProviderSearchItem(
        title=title,
        url=final_url,
        content=content,
        published_at=published_at,
        updated_at=updated_at,
        raw_date_text=raw_date_text,
        date_source=date_source,
        date_confidence=date_confidence,
        raw={
            "category": "finance",
            "provider": KeylessSearchHarvester.source_name,
            "source_engine": candidate.source_engine,
            "harvest_mode": "keyless_http",
            "robots_allowed": True,
            "source_domain": source_domain(final_url),
            "fetch_latency_ms": fetch_latency_ms,
            "rank_reason": "keyless_content_result",
        },
    )


def _is_useful_extracted_content(extracted_content: str, fallback_content: str) -> bool:
    extracted_length = len(_clean_text(extracted_content))
    fallback_length = len(_clean_text(fallback_content))
    if extracted_length >= MIN_USEFUL_EXTRACTED_CONTENT_CHARS:
        return True
    return extracted_length >= 40 and extracted_length >= fallback_length


def _is_useful_extracted_title(title: str, url: str) -> bool:
    cleaned = _clean_text(title)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    domain = source_domain(url).lower()
    generic_titles = {
        "msn",
        "xueqiu",
        "雪球",
        domain,
        domain.removeprefix("www."),
    }
    return lowered not in generic_titles


def _default_fetcher(url: str, headers: dict[str, str], timeout: float) -> FetchResponse:
    request = Request(url, headers=headers)
    opener = _system_proxy_opener() if _is_bing_rss_url(url) else None
    open_response = opener.open(request, timeout=timeout) if opener else urlopen(request, timeout=timeout)
    with open_response as response:
        return FetchResponse(
            url=response.geturl(),
            status=response.status,
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
            body=response.read(),
        )


def _is_bing_rss_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.netloc.lower() not in {"www.bing.com", "cn.bing.com", "global.bing.com"}:
        return False
    params = parse_qs(parsed.query)
    return parsed.path == "/news/search" and (params.get("format") or [""])[0].lower() == "rss"


class _NoBypassProxyHandler(ProxyHandler):
    def proxy_open(self, req, proxy, type):  # type: ignore[no-untyped-def]
        orig_type = req.type
        proxy_type, user, password, hostport = urllib.request._parse_proxy(proxy)
        if proxy_type is None:
            proxy_type = orig_type

        if user and password:
            user_pass = f"{unquote(user)}:{unquote(password)}"
            creds = base64.b64encode(user_pass.encode()).decode("ascii")
            req.add_header("Proxy-authorization", f"Basic {creds}")
        hostport = unquote(hostport)
        req.set_proxy(hostport, proxy_type)
        if orig_type == proxy_type or orig_type == "https":
            return None
        return self.parent.open(req, timeout=req.timeout)


def _system_proxy_opener():
    registry_getter = getattr(urllib.request, "getproxies_registry", None)
    proxies = registry_getter() if registry_getter else urllib.request.getproxies()
    proxies = {key: value for key, value in proxies.items() if key in {"http", "https"}}
    if not proxies:
        return None
    return build_opener(_NoBypassProxyHandler(proxies))


def _browser_headers(*, accept: str | None = None) -> dict[str, str]:
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Cache-Control": "no-cache",
    }


def _unwrap_duckduckgo_url(href: str, base_url: str) -> str:
    absolute = urljoin(base_url, unescape(href))
    parsed = urlsplit(absolute)
    params = parse_qs(parsed.query)
    uddg = params.get("uddg")
    if uddg and uddg[0]:
        return uddg[0]
    if "duckduckgo.com" in parsed.netloc:
        return ""
    return absolute


def _unwrap_bing_news_url(link: str) -> str:
    if not link:
        return ""
    parsed = urlsplit(unescape(link))
    if "bing.com" not in parsed.netloc.lower():
        return link
    params = parse_qs(parsed.query)
    target = params.get("url")
    if target and target[0]:
        return target[0]
    return ""


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _is_html_response(headers: dict[str, str]) -> bool:
    content_type = ""
    for key, value in headers.items():
        if key.lower() == "content-type":
            content_type = value.lower()
            break
    return "text/html" in content_type or "application/xhtml+xml" in content_type or not content_type


def _is_low_value_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "duckduckgo.com" in host:
        return True
    return any(marker in path for marker in ("/search", "/s?", "/so/"))


def _parse_star_robots_rules(text: str) -> tuple[list[str], list[str]]:
    allows: list[str] = []
    disallows: list[str] = []
    applies = False
    seen_rule_in_group = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if seen_rule_in_group:
                applies = False
                seen_rule_in_group = False
            if value == "*":
                applies = True
            continue
        if key in {"allow", "disallow"}:
            seen_rule_in_group = True
            if not applies:
                continue
            if key == "allow":
                allows.append(value)
            elif value:
                disallows.append(value)
    return allows, disallows


def _longest_prefix_match(path: str, prefixes: list[str]) -> int:
    matches = [len(prefix) for prefix in prefixes if prefix and path.startswith(prefix)]
    return max(matches, default=0)
