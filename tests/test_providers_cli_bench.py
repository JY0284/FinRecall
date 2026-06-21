from __future__ import annotations

from io import StringIO
import json

from finrecall import FinRecallClient
from finrecall.benchmark import run_synthetic_benchmark
from finrecall.cli import main
from finrecall.models import ProviderSearchItem


class FakeProvider:
    def search(self, query: str, *, max_results: int, topic: str, time_window: str | None = None):
        return [
            ProviderSearchItem(
                title="A股市场新闻",
                url="https://example.com/a",
                content=f"{query} A股 市场",
                raw={"category": topic},
            )
        ][:max_results]


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, max_results: int, topic: str, time_window: str | None = None):
        self.calls += 1
        return [
            ProviderSearchItem(
                title=f"A股市场新闻 {self.calls}",
                url=f"https://example.com/{self.calls}",
                content=f"{query} A股 市场",
                raw={"category": topic},
            )
        ][:max_results]


def test_cli_search_archive_stats_and_bench(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=FakeProvider(),
        cache_ttl_seconds=60,
    )
    out = StringIO()

    assert main(["search", "A股 新闻", "--max-results", "1"], client=client, stdout=out) == 0
    payload = json.loads(out.getvalue())
    assert payload["results"][0]["url"] == "https://example.com/a"

    out = StringIO()
    assert main(["archive", "市场", "--topic", "a-share"], client=client, stdout=out) == 0
    payload = json.loads(out.getvalue())
    assert payload["results"][0]["title"] == "A股市场新闻"

    out = StringIO()
    assert main(["stats"], client=client, stdout=out) == 0
    payload = json.loads(out.getvalue())
    assert payload["documents"] == 1
    assert payload["search_events"] == 1

    metrics = run_synthetic_benchmark(tmp_path / "bench.sqlite", size=100, query="利润")
    assert metrics["documents"] == 100
    assert metrics["archive_result_count"] > 0
    assert metrics["db_size_bytes"] > 0


def test_cli_search_force_refresh_bypasses_cached_search(tmp_path) -> None:
    provider = CountingProvider()
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=provider,
        cache_ttl_seconds=60,
    )

    assert main(["search", "A股 新闻"], client=client, stdout=StringIO()) == 0

    out = StringIO()
    assert main(["search", "A股 新闻", "--force-refresh"], client=client, stdout=out) == 0
    payload = json.loads(out.getvalue())

    assert provider.calls == 2
    assert payload["cached"] is False
    assert payload["results"][0]["url"] == "https://example.com/2"


def test_cli_doctor_reports_runtime_management_state(tmp_path) -> None:
    client = FinRecallClient(
        db_path=tmp_path / "research.sqlite",
        provider=FakeProvider(),
        cache_ttl_seconds=60,
    )

    out = StringIO()
    assert main(["doctor"], client=client, stdout=out) == 0
    payload = json.loads(out.getvalue())

    assert payload["provider"] == "FakeProvider"
    assert payload["database"]["path"].endswith("research.sqlite")
    assert payload["cache_ttl_seconds"] == 60
    assert payload["checks"]["database"] == "ok"
    assert "documents" in payload["stats"]


def test_cli_accepts_db_after_management_subcommand(tmp_path) -> None:
    db_path = tmp_path / "research.sqlite"

    out = StringIO()
    assert main(["doctor", "--db", str(db_path)], stdout=out) == 0
    payload = json.loads(out.getvalue())

    assert payload["database"]["path"] == str(db_path)
    assert payload["database"]["exists"] is True
