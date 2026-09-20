import hashlib
from datetime import datetime, timezone

import pytest

from finrecall.disclosures import DisclosureArchive, disclosure_candidate
from finrecall.native_finance import plan_finance_query


NOW = datetime(2026, 9, 20, 3, tzinfo=timezone.utc)


def row(identifier="1", title="中毅达：股东减持计划公告", code="600610"):
    return dict(
        secCode=code,
        orgId="gssh0600610",
        announcementId=identifier,
        announcementTitle=title,
        announcementTime=1788883200000,
        adjunctUrl=f"finalpage/2026-09-09/{identifier}.PDF",
        adjunctType="PDF",
    )


def transport(rows, *, total=None):
    def request(url, data=None):
        import json

        if url.endswith("szse_stock.json"):
            result = {
                "stockList": [
                    {"code": "600610", "orgId": "gssh0600610", "zwjc": "中毅达"}
                ]
            }
        elif url.endswith("query"):
            result = dict(
                announcements=rows,
                totalAnnouncement=len(rows) if total is None else total,
                hasMore=False,
            )
        else:
            return b"%PDF-1.4 sample body"
        return json.dumps(result).encode()

    return request


def test_capture_archives_body_and_conservative_date_precision(tmp_path):
    archive = DisclosureArchive(
        tmp_path,
        transport=transport([row()]),
        pdf_text=lambda b: "证券代码：600610 中毅达 股东减持计划" * 15,
        clock=lambda: NOW,
    )
    result = archive.capture("600610.SH", "2025-09-20", "2026-09-20")
    assert result["catalogue_complete"] is True
    assert result["candidate_bodies_complete"] is True
    doc = result["documents"][0]
    assert doc["published_date"] == "2026-09-09"
    assert doc["publication_precision"] == "date"
    assert doc["available_at"].startswith("2026-09-10T00:00:00+08:00")
    assert doc["first_observed_at"] == NOW.isoformat()
    assert hashlib.sha256(b"%PDF-1.4 sample body").hexdigest() == doc["body_sha256"]
    assert (
        archive.capture("600610.SH", "2025-09-20", "2026-09-20")["documents"][0][
            "version_id"
        ]
        == doc["version_id"]
    )


def test_wrong_issuer_and_incomplete_pagination_cannot_claim_coverage(tmp_path):
    a = DisclosureArchive(
        tmp_path, transport=transport([row(code="600611")], total=2), clock=lambda: NOW
    )
    r = a.capture("600610.SH", "2025-09-20", "2026-09-20")
    assert not r["catalogue_complete"]
    assert not r["documents"]
    assert r["errors"]


def test_missing_body_and_body_cap_are_explicit(tmp_path):
    a = DisclosureArchive(
        tmp_path,
        transport=transport([row(), row("2")]),
        pdf_text=lambda _: "",
        clock=lambda: NOW,
    )
    r = a.capture("600610.SH", "2025-09-20", "2026-09-20", max_bodies=1)
    assert r["catalogue_complete"]
    assert not r["candidate_bodies_complete"]
    assert r["candidate_count"] == 2
    assert r["errors"]


def test_duplicate_source_rows_preserve_evidence_without_faking_completeness(tmp_path):
    a = DisclosureArchive(
        tmp_path,
        transport=transport([row(), row()], total=2),
        pdf_text=lambda _: "证券代码600610 中毅达 股东减持计划" * 20,
        clock=lambda: NOW,
    )
    result = a.capture("600610.SH", "2025-09-20", "2026-09-20")
    assert not result["catalogue_complete"]
    assert len(result["documents"]) == 1
    assert "catalogue_total_mismatch" in result["errors"]


def test_observed_later_preserves_original_version(tmp_path):
    a = DisclosureArchive(
        tmp_path,
        transport=transport([row()]),
        pdf_text=lambda _: "中毅达 600610 减持" * 30,
        clock=lambda: NOW,
    )
    first = a.capture("600610.SH", "2025-09-20", "2026-09-20")["documents"][0]
    a.clock = lambda: NOW.replace(day=21)
    same = a.capture("600610.SH", "2025-09-20", "2026-09-21")["documents"][0]
    assert same["first_observed_at"] == first["first_observed_at"]
    a.pdf_text = lambda _: "中毅达 600610 修订减持计划" * 30
    revised = a.capture("600610.SH", "2025-09-20", "2026-09-21")["documents"][0]
    assert revised["version_id"] != first["version_id"]
    assert (tmp_path / "documents" / (first["version_id"] + ".json")).exists()


@pytest.mark.parametrize(
    "term", ["增发", "再融资", "向特定对象发行", "配股", "可转换公司债券"]
)
def test_financing_routes_to_disclosure_without_magic_announcement_word(term):
    assert disclosure_candidate("中毅达：" + term)
    assert "announcement" in plan_finance_query("中毅达 600610 " + term).intents
