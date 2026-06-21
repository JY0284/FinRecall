from __future__ import annotations

from pathlib import Path

import pytest

from finrecall import FinRecallClient
from finrecall.api import default_db_path, default_provider
from finrecall.cli import _build_parser
from finrecall.native_finance import NativeFinanceProvider


def test_public_cli_and_package_name() -> None:
    parser = _build_parser()

    assert parser.prog == "finrecall"
    assert FinRecallClient.__name__ == "FinRecallClient"


def test_finrecall_env_controls_runtime(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "finrecall.sqlite"
    monkeypatch.setenv("FINRECALL_DB", str(db_path))
    monkeypatch.setenv("FINRECALL_CACHE_TTL_SECONDS", "7")
    monkeypatch.setenv("FINRECALL_PROVIDER", "native")

    client = FinRecallClient(provider=NativeFinanceProvider())

    assert default_db_path() == db_path
    assert client.storage.path == db_path
    assert client.cache_ttl_seconds == 7
    assert isinstance(default_provider(), NativeFinanceProvider)


def test_default_provider_rejects_external_provider_names(monkeypatch) -> None:
    monkeypatch.setenv("FINRECALL_PROVIDER", "searxng")

    with pytest.raises(ValueError, match="Unsupported FINRECALL_PROVIDER"):
        default_provider()


def test_public_docs_do_not_reference_private_workspace() -> None:
    private_fragments = [
        "/home/yu",
        "a-share-agent",
        "spiderman",
        "RESEARCH_SEARCH_",
        "research-search",
        "research_search",
        "SearXNG",
        "searxng",
        "SEARXNG",
    ]
    for path in [Path("README.md"), Path("docs/DEPLOYMENT.md"), Path("AGENTS.md")]:
        text = path.read_text(encoding="utf-8")
        for fragment in private_fragments:
            assert fragment not in text, f"{path} still contains {fragment!r}"
