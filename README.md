# FinRecall

FinRecall is an open-source finance research search and recall engine. It
discovers live finance information, extracts article metadata, stores documents
locally in SQLite/FTS, and recalls archived material without requiring a hosted
search service.

The default and only bundled provider is `native`, a built-in finance source
router focused on authoritative Chinese market sources. Downstream applications
can pass a custom provider to `FinRecallClient` when they need experiments or
fallback behavior outside the core package.

## Features

- Native finance search with no default API key requirement
- Trafilatura, htmldate, and dateparser based extraction
- SQLite WAL storage with FTS5 archive search
- Date provenance for observed, fetched, published, and updated times
- Topic and ticker mention extraction for finance recall
- Python API and JSON-first CLI

## Quick Start

```bash
uv sync
uv run pytest
uv run finrecall doctor
uv run finrecall search "A股 主力资金 板块流入" --max-results 5
```

Python API:

```python
from finrecall import search_archive, search_web

live = search_web("贵州茅台 600519 最新公告", max_results=3)
archive = search_archive("半导体 出口管制", limit=10)
```

## Configuration

FinRecall works out of the box with the native provider. Set these variables
only when you need a fixed database path or different local timeouts:

```env
FINRECALL_PROVIDER=native
FINRECALL_DB=./data/finrecall.sqlite
FINRECALL_CACHE_TTL_SECONDS=900
FINRECALL_FETCH_TIMEOUT_SECONDS=10
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for operations, backup, and
verification guidance.
