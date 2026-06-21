from __future__ import annotations

from datetime import timezone

from finrecall.extract import extract_document
from finrecall.topics import classify_topics


def test_extract_document_prefers_page_metadata_over_provider_date() -> None:
    html = """
    <html>
      <head>
        <title>政策更新</title>
        <meta name="datePublished" content="2026-06-18 09:15">
      </head>
      <body><p>A股 政策 证监会 发布 新规。</p></body>
    </html>
    """

    extracted = extract_document(
        "https://example.com/news",
        html.encode("utf-8"),
        headers={},
        provider_date_text="2026-06-20",
    )

    assert extracted.title == "政策更新"
    assert extracted.published_at is not None
    assert extracted.published_at.tzinfo == timezone.utc
    assert extracted.published_at.isoformat() == "2026-06-18T01:15:00+00:00"
    assert extracted.raw_date_text == "2026-06-18 09:15"
    assert extracted.date_source == "metadata"
    assert extracted.date_confidence > 0.9


def test_extract_document_uses_last_modified_as_updated_at_not_published_at() -> None:
    extracted = extract_document(
        "https://example.com/news",
        b"<html><body><p>No publication date.</p></body></html>",
        headers={"last-modified": "Fri, 19 Jun 2026 02:30:00 GMT"},
    )

    assert extracted.published_at is None
    assert extracted.updated_at is not None
    assert extracted.updated_at.isoformat() == "2026-06-19T02:30:00+00:00"
    assert extracted.date_confidence == 0.0
    assert extracted.date_source == "none"


def test_topic_classifier_finds_finance_topics_tickers_and_sources() -> None:
    classification = classify_topics(
        title="新能源板块反弹",
        content="A股 新能源 300750 宁德时代 业绩 改善，证监会 政策 支持。",
        provider_topic="finance",
        expected_topics=["a-share"],
    )

    topics = {topic.topic for topic in classification.topics}
    mentions = {(mention.kind, mention.value) for mention in classification.mentions}

    assert {"a-share", "finance", "earnings", "policy", "sector:new-energy"}.issubset(topics)
    assert ("ticker", "300750") in mentions
    assert ("company", "宁德时代") in mentions
