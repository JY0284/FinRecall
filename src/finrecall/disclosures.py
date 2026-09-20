"""Bounded CNINFO disclosure catalogues and immutable document observations.

This source layer makes no investment eligibility decisions. Coverage refers to
the requested date window and title-selected bodies, never every issuer risk.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
VERSION = "cninfo-disclosure-archive-v1"
TERMS = (
    "减持",
    "转让",
    "权益变动",
    "持股比例",
    "持股变动",
    "限售",
    "解禁",
    "上市流通",
    "增发",
    "再融资",
    "向特定对象发行",
    "非公开发行",
    "配股",
    "可转换公司债券",
    "可转债",
    "发行股票",
    "募集资金",
    "融资方案",
    "发行股份",
)


def disclosure_candidate(title: str) -> bool:
    return any(term in title for term in TERMS)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False
    ).encode("utf-8")


def _immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("immutable archive content conflict")


def _request(url: str, data: dict | None = None) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "www.cninfo.com.cn",
        "static.cninfo.com.cn",
    }:
        raise ValueError("unsupported disclosure source")
    request = Request(
        url,
        data=urlencode(data).encode() if data else None,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"},
    )
    with urlopen(request, timeout=12) as response:
        if urlparse(response.url).hostname not in {
            "www.cninfo.com.cn",
            "static.cninfo.com.cn",
        }:
            raise ValueError("unsupported disclosure redirect")
        body = response.read(6_000_001)
    if len(body) > 6_000_000:
        raise ValueError("disclosure exceeds byte budget")
    return body


def _pdf_text(body: bytes) -> str:
    from pypdf import PdfReader

    if not body.startswith(b"%PDF"):
        raise ValueError("source did not return a PDF")
    pdf = PdfReader(io.BytesIO(body))
    if len(pdf.pages) > 80:
        raise ValueError("disclosure exceeds page budget")
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    if len(text) > 160_000:
        raise ValueError("disclosure exceeds text budget")
    return text


def _metadata(item, *, ts_code, org_id, start, end):
    if item.get("secCode") != ts_code[:6] or item.get("orgId") != org_id:
        raise ValueError("catalogue contains a different issuer")
    published = datetime.fromtimestamp(
        int(item["announcementTime"]) / 1000, SHANGHAI
    ).date()
    if not start <= published <= end:
        raise ValueError("catalogue document outside requested dates")
    relative = str(item["adjunctUrl"])
    if not re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/[\w.-]+", relative):
        raise ValueError("unexpected document URL")
    return dict(
        source_id=str(item["announcementId"]),
        ts_code=ts_code,
        title=re.sub(r"<[^>]+>", "", item["announcementTitle"]),
        url="https://static.cninfo.com.cn/" + relative,
        published_date=published.isoformat(),
        publication_precision="date",
        available_at=datetime.combine(
            published + timedelta(days=1), datetime.min.time(), SHANGHAI
        ).isoformat(),
    )


class DisclosureArchive:
    def __init__(self, root: Path, *, transport=None, pdf_text=None, clock=None):
        self.root = Path(root)
        self.transport = transport or _request
        self.pdf_text = pdf_text or _pdf_text
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._issuers = None

    def capture(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        *,
        max_pages=8,
        max_bodies=24,
    ) -> dict:
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ts_code):
            raise ValueError("exact exchange-qualified issuer code required")
        start, end = (
            datetime.fromisoformat(start_date).date(),
            datetime.fromisoformat(end_date).date(),
        )
        if start > end or (end - start).days > 730:
            raise ValueError("disclosure window must span 0..730 calendar days")
        if not 1 <= max_pages <= 20 or not 1 <= max_bodies <= 50:
            raise ValueError("invalid disclosure budget")
        observed = self.clock()
        if observed.tzinfo is None:
            raise ValueError("timezone-aware observation time required")
        if end > observed.astimezone(SHANGHAI).date():
            raise ValueError("future disclosure window")
        deadline = time.monotonic() + 90
        result = dict(
            schema_version=1,
            source_version=VERSION,
            ts_code=ts_code,
            window_start=start_date,
            window_end=end_date,
            observed_at=observed.isoformat(),
            catalogue_complete=False,
            candidate_bodies_complete=False,
            catalogue=[],
            documents=[],
            body_failures=[],
            errors=[],
        )

        def request(url, data=None):
            if time.monotonic() >= deadline:
                raise TimeoutError("issuer capture deadline")
            return self.transport(url, data)

        rows = {}
        try:
            if self._issuers is None:
                self._issuers = json.loads(
                    request("https://www.cninfo.com.cn/new/data/szse_stock.json")
                )["stockList"]
            code = ts_code[:6]
            matches = [r for r in self._issuers if r.get("code") == code]
            if len(matches) != 1:
                raise ValueError("issuer catalogue mapping missing or ambiguous")
            issuer = matches[0]
            result["issuer_name"] = issuer.get("zwjc", "")
            expected_total = None
            duplicate_dates = set()
            pages_used = 0
            for page in range(1, max_pages + 1):
                payload = json.loads(
                    request(
                        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                        {
                            "pageNum": str(page),
                            "pageSize": "30",
                            "column": {"SH": "sse", "SZ": "szse", "BJ": "bse"}[
                                ts_code[-2:]
                            ],
                            "tabName": "fulltext",
                            "stock": f"{code},{issuer['orgId']}",
                            "searchkey": "",
                            "secid": "",
                            "plate": "",
                            "category": "",
                            "trade": "",
                            "seDate": f"{start_date}~{end_date}",
                            "sortName": "time",
                            "sortType": "desc",
                            "isHLtitle": "false",
                        },
                    )
                )
                total = int(payload["totalAnnouncement"])
                pages_used += 1
                if expected_total is not None and total != expected_total:
                    raise ValueError("catalogue changed during pagination")
                expected_total = total
                batch = payload.get("announcements") or []
                for item in batch:
                    metadata = _metadata(
                        item,
                        ts_code=ts_code,
                        org_id=issuer["orgId"],
                        start=start,
                        end=end,
                    )
                    key = metadata["source_id"]
                    if key in rows and rows[key] != metadata:
                        raise ValueError("document metadata changed during pagination")
                    if key in rows:
                        duplicate_dates.add(metadata["published_date"])
                    rows[key] = metadata
                if not payload.get("hasMore"):
                    result["catalogue_complete"] = expected_total == len(rows)
                    if not result["catalogue_complete"]:
                        result["errors"].append("catalogue_total_mismatch")
                    break
            else:
                result["errors"].append("catalogue_page_budget_exceeded")
            # The upstream orders by date without a stable within-day key.
            # Equal-date records can move across offset pages. Recover only
            # those boundary dates, within the original request budget, and
            # require the window's declared total to match unique documents.
            if not result["catalogue_complete"] and duplicate_dates:
                for day in sorted(duplicate_dates, reverse=True)[
                    : max_pages - pages_used
                ]:
                    repair = json.loads(
                        request(
                            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                            {
                                "pageNum": "1",
                                "pageSize": "30",
                                "column": {"SH": "sse", "SZ": "szse", "BJ": "bse"}[
                                    ts_code[-2:]
                                ],
                                "tabName": "fulltext",
                                "stock": f"{code},{issuer['orgId']}",
                                "seDate": f"{day}~{day}",
                                "sortName": "time",
                                "sortType": "desc",
                                "isHLtitle": "false",
                            },
                        )
                    )
                    if repair.get("hasMore") or int(repair["totalAnnouncement"]) > 30:
                        continue
                    for item in repair.get("announcements") or []:
                        metadata = _metadata(
                            item,
                            ts_code=ts_code,
                            org_id=issuer["orgId"],
                            start=start,
                            end=end,
                        )
                        if metadata["published_date"] != day:
                            raise ValueError(
                                "boundary-date query returned another date"
                            )
                        key = metadata["source_id"]
                        if key in rows and rows[key] != metadata:
                            raise ValueError(
                                "document metadata changed during boundary recovery"
                            )
                        rows[key] = metadata
                if expected_total == len(rows):
                    result["catalogue_complete"] = True
                    result["errors"] = [
                        e for e in result["errors"] if e != "catalogue_total_mismatch"
                    ]
            if not rows:
                result["catalogue_complete"] = False
                result["errors"].append("empty_catalogue_unverified")
        except Exception as exc:
            result["errors"].append(
                "catalogue:" + type(exc).__name__ + ":" + str(exc)[:160]
            )

        # Keep verified earlier pages even when a later page fails. Partial
        # evidence can support a positive hit, never an absence-of-risk claim.
        result["catalogue"] = sorted(
            rows.values(),
            key=lambda r: (r["published_date"], r["source_id"]),
            reverse=True,
        )

        candidates = [
            r for r in result["catalogue"] if disclosure_candidate(r["title"])
        ]
        result["candidate_count"] = len(candidates)
        if len(candidates) > max_bodies:
            result["errors"].append("candidate_body_budget_exceeded")
        for metadata in candidates[:max_bodies]:
            body_hash = None
            try:
                body = request(metadata["url"])
                body_hash = _hash(body)
                _immutable(self.root / "blobs" / (body_hash + ".pdf"), body)
                text = self.pdf_text(body).strip()
                if len(text) < 100:
                    raise ValueError("missing or insufficient disclosure text")
                doc = metadata | dict(
                    body_sha256=_hash(body),
                    text_sha256=_hash(text.encode()),
                    text=text,
                    extraction_version=VERSION,
                )
                identifier = _hash(_json(doc))
                document_path = self.root / "documents" / (identifier + ".json")
                if document_path.exists():
                    doc = json.loads(document_path.read_bytes())
                    if doc["version_id"] != identifier:
                        raise ValueError("archive version mismatch")
                else:
                    doc.update(
                        version_id=identifier,
                        first_observed_at=self.clock().isoformat(),
                    )
                    _immutable(
                        self.root / "blobs" / (doc["body_sha256"] + ".pdf"), body
                    )
                    _immutable(document_path, _json(doc))
                result["documents"].append(doc)
            except Exception as exc:
                result["body_failures"].append(
                    metadata | {"body_sha256": body_hash, "reason": type(exc).__name__}
                )
                result["errors"].append(
                    metadata["source_id"]
                    + ":"
                    + type(exc).__name__
                    + ":"
                    + str(exc)[:160]
                )
        result["candidate_bodies_complete"] = bool(
            result["catalogue_complete"] and len(result["documents"]) == len(candidates)
        )
        result["completed_at"] = self.clock().isoformat()
        result["snapshot_id"] = _hash(_json(result))
        _immutable(
            self.root / "observations" / (result["snapshot_id"] + ".json"),
            _json(result),
        )
        return result
