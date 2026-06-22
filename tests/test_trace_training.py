from __future__ import annotations

import json
from pathlib import Path

from finrecall.api import FinRecallClient
from finrecall.trace_training import import_web_search_traces


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )


def test_import_web_search_traces_keeps_article_results_and_skips_portals(tmp_path: Path) -> None:
    trace = tmp_path / "traces" / "yu" / "20260622_010000_run-1.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "ts": 1782061200.0,
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "tool_web_search",
                        "id": "call_1",
                        "args": {
                            "query": "视涯科技 688781 Micro OLED 最新消息",
                            "max_results": 3,
                        },
                    }
                ],
            },
            {
                "ts": 1782061201.0,
                "role": "tool",
                "name": "tool_web_search",
                "tool_call_id": "call_1",
                "content": json.dumps(
                    {
                        "query": "视涯科技 688781 Micro OLED 最新消息",
                        "error": None,
                        "results": [
                            {
                                "title": "视涯拟与歌尔开展16亿元关联交易，聚焦Micro OLED",
                                "url": "https://www.abvr360.com/a/33400",
                                "content": "视涯科技发布公告，新增与歌尔光学日常关联交易，金额不超过16亿元，标的为硅基OLED微显示屏。",
                            },
                            {
                                "title": "东方财富财经搜索 A股 新闻 行情 公告",
                                "url": "https://so.eastmoney.com/web/s?keyword=Micro+OLED",
                                "content": "东方财富 A股 财经新闻、行情、公告、数据中心。",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    client = FinRecallClient(db_path=tmp_path / "research.sqlite")

    summary = import_web_search_traces(client, [trace])
    archive = client.search_archive("视涯 歌尔 Micro OLED", limit=5)

    assert summary == {
        "files": 1,
        "tool_calls": 1,
        "results_seen": 2,
        "documents_imported": 1,
        "results_skipped": 1,
    }
    assert [item.url for item in archive.results] == ["https://www.abvr360.com/a/33400"]
    assert archive.results[0].raw_payload["source"] == "tool_web_search_trace"
    assert archive.results[0].raw_payload["teacher"] == "tavily"
    assert archive.results[0].raw_payload["query"] == "视涯科技 688781 Micro OLED 最新消息"


def test_import_web_search_traces_skips_native_finrecall_results(tmp_path: Path) -> None:
    trace = tmp_path / "traces" / "yu" / "native.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "ts": 1782061200.0,
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "tool_web_search",
                        "id": "call_1",
                        "args": {"query": "视涯科技 Micro OLED"},
                    }
                ],
            },
            {
                "ts": 1782061201.0,
                "role": "tool",
                "name": "tool_web_search",
                "tool_call_id": "call_1",
                "content": json.dumps(
                    {
                        "query": "视涯科技 Micro OLED",
                        "error": None,
                        "results": [
                            {
                                "title": "视涯科技与Micro OLED产业链新闻",
                                "url": "https://example.com/article",
                                "content": "这是一条文章型结果，但结构来自FinRecall native，不能作为Tavily teacher。",
                                "published_at": None,
                                "updated_at": None,
                                "date_source": "none",
                                "date_confidence": 0.0,
                                "source": "example.com",
                                "topics": [],
                                "mentions": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    client = FinRecallClient(db_path=tmp_path / "research.sqlite")

    summary = import_web_search_traces(client, [trace])
    archive = client.search_archive("视涯 Micro OLED", limit=5)

    assert summary["results_seen"] == 1
    assert summary["documents_imported"] == 0
    assert summary["results_skipped"] == 1
    assert archive.results == []


def test_import_web_search_traces_keeps_official_exchange_stock_disclosure_pages(tmp_path: Path) -> None:
    trace = tmp_path / "traces" / "yu" / "sse.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "tool_web_search",
                        "id": "call_1",
                        "args": {"query": "凯赛生物 688065 最新公告"},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "tool_web_search",
                "tool_call_id": "call_1",
                "content": json.dumps(
                    {
                        "query": "凯赛生物 688065 最新公告",
                        "results": [
                            {
                                "title": "凯赛生物公司公告 - 上海证券交易所",
                                "url": (
                                    "https://www.sse.com.cn/assortment/stock/list/info/"
                                    "announcement/index.shtml?productId=688065"
                                ),
                                "content": "上海证券交易所官方披露凯赛生物 688065 公司公告、临时公告、定期报告。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    client = FinRecallClient(db_path=tmp_path / "research.sqlite")

    summary = import_web_search_traces(client, [trace])
    archive = client.search_archive("凯赛生物 上海证券交易所", limit=5)

    assert summary["documents_imported"] == 1
    assert archive.results[0].source_domain == "www.sse.com.cn"
