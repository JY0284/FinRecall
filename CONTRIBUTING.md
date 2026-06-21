# Contributing to FinRecall

Thanks for helping improve FinRecall.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

Keep changes focused and add tests for behavior changes. The CLI should keep
returning machine-readable JSON.

## Pull Requests

- Explain the user-facing change.
- Include the test command you ran.
- Do not commit virtual environments, SQLite databases, cache directories, or
  benchmark output unless the benchmark data is intentional.
