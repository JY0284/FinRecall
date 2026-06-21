from __future__ import annotations

import re

from finrecall.models import MentionRecord, TopicClassification, TopicRecord

TICKER_RE = re.compile(r"(?<!\d)(?:SH|SZ)?(?P<ticker>\d{6})(?!\d)", re.IGNORECASE)
COMPANY_NAMES = ("贵州茅台", "宁德时代", "比亚迪", "招商银行", "中国平安")


def classify_topics(
    *,
    title: str,
    content: str,
    provider_topic: str | None = None,
    expected_topics: list[str] | tuple[str, ...] | None = None,
) -> TopicClassification:
    text = f"{title} {content}"
    topics: dict[tuple[str, str], TopicRecord] = {}
    mentions: dict[tuple[str, str], MentionRecord] = {}

    def add_topic(topic: str, confidence: float, source: str, evidence: str = "") -> None:
        key = (topic, source)
        existing = topics.get(key)
        if existing is None or existing.confidence < confidence:
            topics[key] = TopicRecord(topic, confidence, source, evidence)

    for topic in expected_topics or []:
        add_topic(topic, 1.0, "expected", "caller")

    if provider_topic:
        mapped = "finance" if provider_topic in {"finance", "news"} else provider_topic
        add_topic(mapped, 0.6, "provider", provider_topic)

    if any(token in text for token in ("A股", "沪深", "股票", "证券", "证监会")):
        add_topic("a-share", 0.9, "taxonomy", "A股/证券")
        add_topic("finance", 0.75, "taxonomy", "market vocabulary")

    if any(token in text for token in ("业绩", "财报", "利润", "营收", "现金流")):
        add_topic("earnings", 0.82, "taxonomy", "业绩/利润")
        add_topic("finance", 0.76, "taxonomy", "financial metrics")

    if any(token in text for token in ("政策", "监管", "证监会", "央行", "财政部")):
        add_topic("policy", 0.84, "taxonomy", "policy/regulator")

    if any(token in text for token in ("新能源", "锂电", "光伏", "储能")):
        add_topic("sector:new-energy", 0.82, "taxonomy", "新能源")

    if any(token in text for token in ("CPI", "PMI", "社融", "LPR", "宏观")):
        add_topic("macro", 0.8, "taxonomy", "macro indicator")

    for match in TICKER_RE.finditer(text):
        ticker = match.group("ticker")
        mentions[("ticker", ticker)] = MentionRecord("ticker", ticker, match.group(0))
        add_topic("a-share", 0.86, "mention", ticker)

    for company in COMPANY_NAMES:
        if company in text:
            mentions[("company", company)] = MentionRecord("company", company, company)
            add_topic("company", 0.72, "mention", company)

    sorted_topics = sorted(topics.values(), key=lambda item: (-item.confidence, item.topic))
    sorted_mentions = sorted(mentions.values(), key=lambda item: (item.kind, item.value))
    return TopicClassification(topics=sorted_topics, mentions=sorted_mentions)
