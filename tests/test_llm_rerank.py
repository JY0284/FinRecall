from __future__ import annotations

import json

from finrecall.llm_rerank import LLMReranker
from finrecall.models import ProviderSearchItem


def _item(title: str, url: str, content: str = "content") -> ProviderSearchItem:
    return ProviderSearchItem(
        title=title,
        url=url,
        content=content,
        raw={"provider": "keyless_search", "source_engine": "bing_news_rss"},
    )


def test_llm_reranker_promotes_selected_ids_and_drops_rejected_ids() -> None:
    calls: list[dict] = []

    def requester(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"selected": [2], "drop": [1]})
                    }
                }
            ]
        }

    reranker = LLMReranker(
        api_key="secret-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        requester=requester,
        timeout_seconds=2.5,
    )
    items = [
        _item("market hype", "https://example.com/hype"),
        _item("policy source", "https://example.com/policy"),
        _item("background", "https://example.com/background"),
    ]

    reranked = reranker.rerank("半导体 AI 芯片 最新政策 A股 影响", items, max_results=3)

    assert [item.title for item in reranked] == ["policy source", "background"]
    assert reranked[0].raw["llm_rerank"]["selected"] is True
    assert calls[0]["payload"]["thinking"] == {"type": "disabled"}
    assert calls[0]["payload"]["response_format"] == {"type": "json_object"}
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in json.dumps(calls[0]["payload"])
    assert calls[0]["timeout"] == 2.5


def test_llm_reranker_falls_back_to_original_items_on_bad_response() -> None:
    def requester(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
        return {"choices": [{"message": {"content": ""}}]}

    reranker = LLMReranker(
        api_key="secret-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        requester=requester,
    )
    items = [
        _item("first", "https://example.com/first"),
        _item("second", "https://example.com/second"),
    ]

    assert reranker.rerank("query", items, max_results=2) == items


def test_llm_reranker_from_env_is_default_off(monkeypatch) -> None:
    monkeypatch.delenv("FINRECALL_LLM_RERANK", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")

    assert LLMReranker.from_env() is None


def test_llm_reranker_from_env_reads_deepseek_settings(monkeypatch) -> None:
    monkeypatch.setenv("FINRECALL_LLM_RERANK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("AGENT_MODEL", "deepseek-v4-flash")

    reranker = LLMReranker.from_env()

    assert reranker is not None
    assert reranker.model == "deepseek-v4-flash"
    assert reranker.base_url == "https://api.deepseek.com"
