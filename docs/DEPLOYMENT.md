# FinRecall deployment and operations

FinRecall runs as an embedded Python package and CLI. It does not require a
separate search service: `FINRECALL_PROVIDER=native` uses the built-in finance
source router and local SQLite/FTS storage.

## Install

```bash
git clone git@github.com:JY0284/FinRecall.git
cd FinRecall
uv sync
uv run pytest
uv run finrecall doctor
```

For package usage:

```python
from finrecall import search_web

outcome = search_web("拓荆科技 688072 最新公告", max_results=3)
print(outcome.to_dict())
```

## Environment

Recommended local settings:

```env
FINRECALL_PROVIDER=native
FINRECALL_DB=./data/finrecall.sqlite
FINRECALL_CACHE_TTL_SECONDS=900
FINRECALL_FETCH_TIMEOUT_SECONDS=10
```

## Management

Use these commands during operations:

```bash
uv run finrecall doctor --db ./data/finrecall.sqlite
uv run finrecall stats --db ./data/finrecall.sqlite
uv run finrecall search "A股 主力资金 板块流入" --max-results 5 --force-refresh
uv run finrecall archive "半导体 出口管制" --limit 10
```

`finrecall doctor` reports provider, database path, cache TTL, storage stats,
and basic local checks. `finrecall stats` is the cheap health check for stored
events and documents.

SQLite runs with WAL and busy timeout. Back up the database by copying
`FINRECALL_DB`, plus `-wal` and `-shm` files if present, or by taking a SQLite
online backup from a maintenance shell.

## Verification

Before release or deployment:

```bash
uv run pytest
uv run ruff check src tests
uv run finrecall doctor --db ./data/finrecall.sqlite
```
