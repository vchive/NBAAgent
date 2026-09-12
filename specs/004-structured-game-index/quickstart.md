# Quickstart: Validate Structured Game Retrieval

## Prerequisites

- Python 3.12 environment with project development dependencies installed.
- Network access only for the explicit warm/import step.
- A writable SQLite path outside source control.

## Offline contract and integration validation

```bash
pytest -q tests/unit/test_game_index.py tests/contract/test_hupu_adapter.py tests/contract/test_indexed_provider.py tests/integration/test_game_index_chat.py
ruff check apps/api/src tests scripts
```

Expected: all tests pass using captured minimal HTML/test doubles; no public network request is required.

## Warm the Knicks–Spurs 2025-26 records

```bash
GAME_INDEX_DB=/tmp/nba-index.sqlite3 \
python scripts/warm-game-index.py \
  --season 2025-26 \
  --teams knicks,spurs \
  --details \
  --from 2026-06-01 \
  --to 2026-06-30
```

Expected: G1–G5 are inserted once; placeholder G6/G7 have no final score and are not mistaken for completed games. A second run reports unchanged/updated rows rather than duplicates.

## Restart persistence acceptance

1. Start the application with game indexing enabled and the same database path.
2. Ask `2026尼克斯-马刺` in roaming mode.
3. Verify the answer identifies the five completed Finals games and the 4–1 series result from structured records.
4. Stop network access, restart the service, repeat the query, and verify the same structured result is available.
5. Ask `2026 总决赛 G5 布伦森多少分？` and verify the indexed player line is used when details were warmed.

## Safety and origin acceptance

Run the demo fixture profile against the same index, open highlights, and inspect record counts. Expected: no fixture game enters the public tables. Store a mocked web-search result and verify it appears only in document search with partial evidence, never in game/stat tables.

## Full regression

```bash
pytest -q
ruff check .
git diff --check
make eval
npx playwright test
```

Expected: existing deterministic, full-intelligence, safety, authentication, highlights, and browser behavior remains green.
