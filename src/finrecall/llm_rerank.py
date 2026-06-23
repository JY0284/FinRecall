from __future__ import annotations

from dataclasses import replace
import json
import os
from typing import Any, Callable
from urllib.request import Request, urlopen

from finrecall.models import ProviderSearchItem


Requester = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


RERANK_SYSTEM_PROMPT = (
    "You rank finance search results. Return only compact JSON: "
    '{"selected":[ids],"drop":[ids]}. Select results that answer q. '
    "Drop wrong company, wrong year, wrong report period, market hype for "
    "policy queries, ETF/fund housekeeping for broad market queries, and thin "
    "portal/search pages."
)


class LLMReranker:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        requester: Requester | None = None,
        timeout_seconds: float = 2.5,
        max_candidates: int = 6,
        content_chars: int = 240,
        max_tokens: int = 260,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.requester = requester or _default_requester
        self.timeout_seconds = timeout_seconds
        self.max_candidates = max(1, min(int(max_candidates), 10))
        self.content_chars = max(80, min(int(content_chars), 600))
        self.max_tokens = max(64, min(int(max_tokens), 800))

    @classmethod
    def from_env(cls) -> "LLMReranker | None":
        if not _env_flag("FINRECALL_LLM_RERANK", default=False):
            return None
        api_key = (
            os.environ.get("FINRECALL_LLM_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("AGENT_API_KEY")
            or ""
        ).strip()
        if not api_key:
            return None
        base_url = (
            os.environ.get("FINRECALL_LLM_BASE_URL")
            or os.environ.get("DEEPSEEK_BASE_URL")
            or os.environ.get("AGENT_BASE_URL")
            or "https://api.deepseek.com"
        ).strip()
        model = (
            os.environ.get("FINRECALL_LLM_MODEL")
            or os.environ.get("AGENT_MODEL")
            or "deepseek-v4-flash"
        ).strip()
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=_env_float("FINRECALL_LLM_TIMEOUT_SECONDS", 2.5),
            max_candidates=_env_int("FINRECALL_LLM_MAX_CANDIDATES", 6),
            content_chars=_env_int("FINRECALL_LLM_CONTENT_CHARS", 240),
            max_tokens=_env_int("FINRECALL_LLM_MAX_TOKENS", 260),
        )

    def rerank(
        self,
        query: str,
        items: list[ProviderSearchItem],
        *,
        max_results: int,
    ) -> list[ProviderSearchItem]:
        if len(items) < 2:
            return items
        candidates = items[: self.max_candidates]
        payload = self._payload(query, candidates)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self.requester(
                f"{self.base_url}/chat/completions",
                payload,
                headers,
                self.timeout_seconds,
            )
            content = _message_content(response)
            selected_ids, dropped_ids = _parse_decision(content)
        except Exception:  # noqa: BLE001
            return items
        reranked = _apply_decision(
            candidates,
            selected_ids=selected_ids,
            dropped_ids=dropped_ids,
        )
        if not reranked:
            return items
        if len(items) > len(candidates):
            reranked.extend(items[len(candidates) :])
        return reranked[: max(max_results, len(reranked))]

    def _payload(self, query: str, candidates: list[ProviderSearchItem]) -> dict[str, Any]:
        compact = [
            {
                "id": index,
                "title": item.title[:160],
                "url": item.url[:180],
                "content": item.content[: self.content_chars],
                "source": str(
                    item.raw.get("source_engine")
                    or item.raw.get("native_source")
                    or item.raw.get("provider")
                    or ""
                ),
            }
            for index, item in enumerate(candidates, start=1)
        ]
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": RERANK_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"q": query, "r": compact},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }


def _default_requester(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty content")
    return content


def _parse_decision(content: str) -> tuple[list[int], set[int]]:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("decision must be an object")
    selected_ids = _parse_id_list(data.get("selected"))
    dropped_ids = set(_parse_id_list(data.get("drop") or data.get("dropped")))
    return selected_ids, dropped_ids


def _parse_id_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    parsed: list[int] = []
    for entry in value:
        raw_id = entry.get("id") if isinstance(entry, dict) else entry
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in parsed:
            parsed.append(item_id)
    return parsed


def _apply_decision(
    candidates: list[ProviderSearchItem],
    *,
    selected_ids: list[int],
    dropped_ids: set[int],
) -> list[ProviderSearchItem]:
    by_id = {index: item for index, item in enumerate(candidates, start=1)}
    selected: list[ProviderSearchItem] = []
    selected_set: set[int] = set()
    for item_id in selected_ids:
        item = by_id.get(item_id)
        if item is None:
            continue
        selected_set.add(item_id)
        selected.append(_mark_llm_reranked(item, selected=True))
    for item_id, item in by_id.items():
        if item_id in selected_set or item_id in dropped_ids:
            continue
        selected.append(item)
    return selected


def _mark_llm_reranked(item: ProviderSearchItem, *, selected: bool) -> ProviderSearchItem:
    raw = dict(item.raw)
    raw["llm_rerank"] = {"selected": selected}
    raw["rank_reason"] = "llm_semantic_reranked"
    return replace(item, raw=raw)


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default
