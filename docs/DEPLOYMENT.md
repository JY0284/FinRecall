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

Experimental keyless search settings:

```env
FINRECALL_PROVIDER=hybrid_keyless
FINRECALL_KEYLESS_TIMEOUT_SECONDS=5
FINRECALL_KEYLESS_SLEEP_SECONDS=0.2
```

`hybrid_keyless` keeps native finance results for structured market-data
queries and adds conservative keyless recall for content-heavy queries such as
news, earnings, announcements, and policy analysis. The first default source is
Bing News RSS, followed by browser-readable HTML sources when robots rules allow
them. It does not use API keys, CAPTCHA bypass, proxy pools, or anti-bot
evasion. Treat it as an experiment until source terms have been reviewed and
trace comparison metrics are consistently better than native-only search.

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

SQLite runs with WAL and busy timeout. Use SQLite's online backup API for a
consistent backup while writers are running. A filesystem copy requires all
writers to be stopped and the database and any remaining WAL files to be
preserved together; copying live files independently is not a consistent snapshot.

The optional `finrecall.disclosures.DisclosureArchive` writes immutable
catalogue and document observations under its caller-selected archive root.
Back up that root separately when the consuming application uses it; it is
outside `FINRECALL_DB`. Capture budgets and incomplete coverage remain part of
the observation, so a successful capture does not establish complete disclosure
history. See [the disclosure implementation](../src/finrecall/disclosures.py).

## Verification

Before release or deployment:

```bash
uv run pytest
uv run ruff check src tests
uv run finrecall doctor --db ./data/finrecall.sqlite
```
