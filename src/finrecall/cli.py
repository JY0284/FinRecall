from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from finrecall.api import FinRecallClient
from finrecall.benchmark import run_synthetic_benchmark
from finrecall.trace_eval import compare_trace_teacher_results
from finrecall.trace_training import discover_trace_files, import_web_search_traces
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

    if args.command == "import-traces":
        paths = [Path(path) for path in args.paths]
        for trace_dir in args.trace_dir or []:
            paths.extend(discover_trace_files(trace_dir, limit_files=args.limit_files))
        if not paths:
            parser.error("import-traces requires trace paths or --trace-dir")
        summary = import_web_search_traces(active_client, paths)
        _write_json(stdout, summary)
        return 0

    if args.command == "compare-traces":
        paths = [Path(path) for path in args.paths]
        for trace_dir in args.trace_dir or []:
            paths.extend(discover_trace_files(trace_dir, limit_files=args.limit_files))
        imported = import_web_search_traces(active_client, paths) if paths else None
        report = compare_trace_teacher_results(
            active_client,
            max_cases=args.max_cases,
            max_results=args.max_results,
            topic=args.topic,
            time_window=args.since,
            include_results=args.include_results,
            snippet_chars=args.snippet_chars,
        )
        if imported is not None:
            report["imported"] = imported
        _write_json(stdout, report)
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

    import_traces = subparsers.add_parser("import-traces")
    _add_db_argument(import_traces)
    import_traces.add_argument("paths", nargs="*")
    import_traces.add_argument("--trace-dir", action="append")
    import_traces.add_argument("--limit-files", type=int)

    compare_traces = subparsers.add_parser("compare-traces")
    _add_db_argument(compare_traces)
    compare_traces.add_argument("paths", nargs="*")
    compare_traces.add_argument("--trace-dir", action="append")
    compare_traces.add_argument("--limit-files", type=int)
    compare_traces.add_argument("--max-cases", type=int, default=50)
    compare_traces.add_argument("--max-results", type=int, default=5)
    compare_traces.add_argument("--topic", default="general")
    compare_traces.add_argument("--since", dest="since")
    compare_traces.add_argument("--include-results", action="store_true")
    compare_traces.add_argument("--snippet-chars", type=int, default=180)

    return parser


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", dest="command_db", help="SQLite database path")


def _write_json(stdout: TextIO, payload: dict) -> None:
    try:
        stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    except UnicodeEncodeError:
        stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
