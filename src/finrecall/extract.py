from __future__ import annotations

from html import unescape
import json
import re
from typing import Any

from finrecall.models import ExtractedDocument
from finrecall.utils import parse_datetime_text, sha256_text

EXTRACTION_VERSION = "finrecall-v1"
TITLE_RE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
META_RE = re.compile(r"<meta\s+(?P<attrs>[^>]+)>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r"(?P<key>[a-zA-Z_:.-]+)\s*=\s*(?:\"(?P<dq>[^\"]*)\"|'(?P<sq>[^']*)'|(?P<bare>[^\s>]+))"
)
SCRIPT_JSON_RE = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)

PUBLISHED_META_NAMES = {
    "article:published_time",
    "datepublished",
    "date",
    "pubdate",
    "publishdate",
    "publish_time",
    "weibo:article:create_at",
}
UPDATED_META_NAMES = {"article:modified_time", "datemodified", "lastmod", "updated_time"}


def extract_document(
    url: str,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
    provider_date_text: str | None = None,
) -> ExtractedDocument:
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    html = _decode_body(body, headers)
    title = _extract_title(html) or url
    content = _extract_content(html)

    metadata = _extract_meta_values(html)
    json_ld = _extract_json_ld_dates(html)
    published_text = metadata.get("published") or json_ld.get("published")
    updated_text = metadata.get("updated") or json_ld.get("updated")

    date_source = "none"
    date_confidence = 0.0
    raw_date_text = None
    published_at = None

    if published_text:
        published_at = parse_datetime_text(published_text)
        if published_at:
            raw_date_text = published_text
            date_source = "metadata"
            date_confidence = 0.95

    if published_at is None:
        htmldate_text = _extract_with_htmldate(html)
        if htmldate_text:
            published_at = parse_datetime_text(htmldate_text)
            if published_at:
                raw_date_text = htmldate_text
                date_source = "htmldate"
                date_confidence = 0.85

    if published_at is None and provider_date_text:
        published_at = parse_datetime_text(provider_date_text)
        if published_at:
            raw_date_text = provider_date_text
            date_source = "provider"
            date_confidence = 0.7

    updated_at = parse_datetime_text(updated_text) if updated_text else None
    if updated_at is None:
        updated_at = parse_datetime_text(headers.get("last-modified"))

    return ExtractedDocument(
        url=url,
        title=title,
        content=content,
        published_at=published_at,
        updated_at=updated_at,
        raw_date_text=raw_date_text,
        date_source=date_source,
        date_confidence=date_confidence,
        content_hash=sha256_text(content),
    )


def _decode_body(body: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("content-type", "")
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "latin-1"])
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _extract_title(html: str) -> str:
    match = TITLE_RE.search(html)
    if not match:
        return ""
    return _collapse_space(unescape(TAG_RE.sub("", match.group("title"))))


def _extract_content(html: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted:
            return _collapse_space(extracted)
    except Exception:  # noqa: BLE001
        pass

    body = SCRIPT_STYLE_RE.sub(" ", html)
    text = TAG_RE.sub(" ", body)
    return _collapse_space(unescape(text))


def _extract_meta_values(html: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in META_RE.finditer(html):
        attrs = _parse_attrs(match.group("attrs"))
        name = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        content = attrs.get("content", "")
        if not name or not content:
            continue
        normalized = name.replace("_", "").replace("-", "")
        if name in PUBLISHED_META_NAMES or normalized in PUBLISHED_META_NAMES:
            values.setdefault("published", content)
        if name in UPDATED_META_NAMES or normalized in UPDATED_META_NAMES:
            values.setdefault("updated", content)
    return values


def _parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(raw):
        value = match.group("dq") or match.group("sq") or match.group("bare") or ""
        attrs[match.group("key").lower()] = unescape(value.strip())
    return attrs


def _extract_json_ld_dates(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in SCRIPT_JSON_RE.finditer(html):
        try:
            payload = json.loads(unescape(match.group("body").strip()))
        except json.JSONDecodeError:
            continue
        for node in _walk_json(payload):
            if isinstance(node, dict):
                published = node.get("datePublished")
                updated = node.get("dateModified")
                if published and "published" not in found:
                    found["published"] = str(published)
                if updated and "updated" not in found:
                    found["updated"] = str(updated)
    return found


def _walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _extract_with_htmldate(html: str) -> str | None:
    try:
        import htmldate

        return htmldate.find_date(html)
    except Exception:  # noqa: BLE001
        return None


def _collapse_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
