# AGENTS.md - FinRecall

This repository owns the `finrecall` Python package for finance research
search, extraction, storage, and archive recall.

## Repository Boundary

- Keep FinRecall self-contained.
- Do not add product-specific application code to this package.
- Keep integrations in downstream applications; FinRecall should expose stable
  Python and CLI interfaces.

## Runtime Role

- Default provider: `native_finance`.
- Storage is local SQLite/FTS. Use `FINRECALL_DB` to pin the database path.
- Public environment variables use the `FINRECALL_` prefix.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run finrecall doctor --db ./data/finrecall.sqlite
uv run finrecall stats --db ./data/finrecall.sqlite
```

## Change Discipline

- Keep the public API stable:
  `search_web`, `search_archive`, and `fetch_and_store`.
- Keep CLI JSON output machine-readable.
- Add tests for behavior changes before implementation.
- Do not commit `.venv`, SQLite databases, cache directories, or benchmark
  output unless a benchmark baseline is intentionally added.
