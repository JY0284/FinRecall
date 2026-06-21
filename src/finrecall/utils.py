from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = ZoneInfo("Asia/Shanghai")
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=DEFAULT_TIMEZONE)
    return value.astimezone(timezone.utc)


def parse_datetime_text(text: str | None) -> datetime | None:
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    try:
        parsed = parsedate_to_datetime(raw)
        if parsed:
            return ensure_utc(parsed)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass

    iso_candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        return ensure_utc(parsed)
    except ValueError:
        pass

    normalized = raw
    cn_match = re.search(
        r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日?"
        r"(?:\s*(?P<h>\d{1,2}):(?P<min>\d{1,2})(?::(?P<s>\d{1,2}))?)?",
        normalized,
    )
    if cn_match:
        groups = cn_match.groupdict(default="0")
        parsed = datetime(
            int(groups["y"]),
            int(groups["m"]),
            int(groups["d"]),
            int(groups["h"]),
            int(groups["min"]),
            int(groups["s"]),
            tzinfo=DEFAULT_TIMEZONE,
        )
        return parsed.astimezone(timezone.utc)

    simple_match = re.search(
        r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})"
        r"(?:[ T](?P<h>\d{1,2}):(?P<min>\d{1,2})(?::(?P<s>\d{1,2}))?)?",
        normalized,
    )
    if simple_match:
        groups = simple_match.groupdict(default="0")
        parsed = datetime(
            int(groups["y"]),
            int(groups["m"]),
            int(groups["d"]),
            int(groups["h"]),
            int(groups["min"]),
            int(groups["s"]),
            tzinfo=DEFAULT_TIMEZONE,
        )
        return parsed.astimezone(timezone.utc)

    try:
        import dateparser

        parsed = dateparser.parse(raw, settings={"TIMEZONE": "Asia/Shanghai"})
        return ensure_utc(parsed)
    except Exception:  # noqa: BLE001
        return None


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_NAMES or lowered.startswith(TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))
    query = urlencode(query_items, doseq=True)
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def source_domain(url: str) -> str:
    return urlsplit(url).netloc.lower()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_text(encoded)


def datetime_to_storage(value: datetime | None) -> str | None:
    value = ensure_utc(value)
    return value.isoformat() if value else None


def datetime_from_storage(value: str | None) -> datetime | None:
    return parse_datetime_text(value)
