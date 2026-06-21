from __future__ import annotations

from pathlib import Path


def test_deployment_doc_covers_runtime_integration_and_management() -> None:
    doc = Path("docs/DEPLOYMENT.md")

    text = doc.read_text(encoding="utf-8")

    required_fragments = [
        "uv sync",
        "FINRECALL_PROVIDER=native",
        "FINRECALL_DB=./data/finrecall.sqlite",
        "FINRECALL_CACHE_TTL_SECONDS=900",
        "finrecall doctor",
        "finrecall stats",
        "uv run pytest",
        "uv run ruff check src tests",
    ]
    for fragment in required_fragments:
        assert fragment in text
