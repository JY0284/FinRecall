from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from finrecall.api import FinRecallClient
from finrecall.benchmark import run_synthetic_benchmark
from finrecall.utils import parse_datetime_text


def main(
    argv: list[str] | None = None,
    *,
    client: FinRecallClient | None = None,
    stdout: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    parser = _build_parser()
    args = parser.parse_args(argv)
    db_path = getattr(args, "command_db", None) or getattr(args, "db", None)
    active_client = client or FinRecallClient(db_path=db_path)

    if args.command == "search":
        outcome = active_client.search_web(
            args.query,
            max_results=args.max_results,
            topic=args.topic,
            time_window=args.since,
            force_refresh=args.force_refresh,
        )
        _write_json(stdout, outcome.to_dict())
        return 0 if outcome.error is None else 2

    if args.command == "archive":
        outcome = active_client.search_archive(
            args.query,
            limit=args.limit,
            published_after=parse_datetime_text(args.published_from),
            published_before=parse_datetime_text(args.published_to),
            topics=args.topic or None,
            sources=args.source or None,
        )
        _write_json(stdout, outcome.to_dict())
        return 0 if outcome.error is None else 2

    if args.command == "fetch":
        document = active_client.fetch_and_store(args.url, force_refresh=args.force_refresh)
        _write_json(stdout, document.to_dict())
        return 0

    if args.command == "stats":
        _write_json(stdout, active_client.storage.stats())
        return 0

    if args.command == "doctor":
        _write_json(stdout, active_client.diagnostics())
        return 0

    if args.command == "bench":
        metrics = run_synthetic_benchmark(
            Path(args.db) if args.db else Path("finrecall_bench.sqlite"),
            size=args.size,
            save=args.save,
        )
        _write_json(stdout, metrics)
        return 0

    parser.error("unknown command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finrecall")
    parser.add_argument("--db", help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search")
    _add_db_argument(search)
    search.add_argument("query")
    search.add_argument("--since", dest="since")
    search.add_argument("--topic", default="general")
    search.add_argument("--max-results", type=int, default=3)
    search.add_argument("--force-refresh", action="store_true")

    archive = subparsers.add_parser("archive")
    _add_db_argument(archive)
    archive.add_argument("query")
    archive.add_argument("--from", dest="published_from")
    archive.add_argument("--to", dest="published_to")
    archive.add_argument("--topic", action="append")
    archive.add_argument("--source", action="append")
    archive.add_argument("--limit", type=int, default=10)

    fetch = subparsers.add_parser("fetch")
    _add_db_argument(fetch)
    fetch.add_argument("url")
    fetch.add_argument("--force-refresh", action="store_true")

    stats = subparsers.add_parser("stats")
    _add_db_argument(stats)

    doctor = subparsers.add_parser("doctor")
    _add_db_argument(doctor)

    bench = subparsers.add_parser("bench")
    _add_db_argument(bench)
    bench.add_argument("--size", type=int, default=10_000)
    bench.add_argument("--save", action="store_true")

    return parser


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", dest="command_db", help="SQLite database path")


def _write_json(stdout: TextIO, payload: dict) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
