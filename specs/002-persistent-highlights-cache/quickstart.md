# Quickstart: Persistent Highlights Cache

## Local validation

```bash
python3 -m pytest -q tests/unit/test_highlights_cache.py \
  tests/contract/test_highlights_cache.py \
  tests/integration/test_highlights_cache_restart.py
python3 -m ruff check apps tests scripts
npm run e2e -- --reporter=line
```

Expected:

- first recent request calls the provider and writes one cache row;
- second request returns the same five games with no additional provider call;
- corrupt payload is ignored and replaced;
- a lower-completeness detail does not replace a full detail;
- slow history fetch shows one loading state；fast cache response shows none.

## Restart persistence

```bash
tmp_dir="$(mktemp -d)"
HIGHLIGHTS_CACHE_DB="$tmp_dir/highlights.sqlite3" python3 -m pytest -q \
  tests/integration/test_highlights_cache_restart.py
```

The test creates one app instance, primes recent/detail responses, closes it, starts a new app instance over the same file with an unavailable provider and verifies cached final data remains readable.

## Docker deployment

```bash
make deploy-live
docker compose \
  -f docker-compose.yml \
  -f docker-compose.public.yml \
  -f docker-compose.auth.yml \
  -f docker-compose.siliconflow.yml \
  exec -T nba-agent python - <<'PY'
from pathlib import Path
path = Path('/app/data/highlights.sqlite3')
print({'exists': path.exists(), 'bytes': path.stat().st_size if path.exists() else 0})
PY
```

Recreate the container without deleting the named volume, then verify the file remains and `/readyz` is 200. Do not use `docker compose down -v` unless intentionally deleting the cache.

## Source coverage probe

The source assessment is recorded in [research.md](research.md). Re-run reachability probes from the deployment host before enabling any new source. A 200 response alone is insufficient: verify season coverage, game count, field presence, timestamp, and conflict behavior against an already trusted game.

## Verified evidence — 2026-09-01

- `python3 -m pytest -q`: 383 tests passed.
- `python3 -m ruff check apps tests scripts`: passed.
- `python3 -m compileall -q apps scripts` and `node --check apps/web-demo/app.js`: passed.
- `npm run e2e -- --reporter=line`: 12 tests passed, including 50ms no-loading and
  300ms single-loading cases.
- Golden evaluation: 63 runs, weighted score 100.00, safety vetoes 0.
- Public live Compose: container healthy；`GET /readyz` returned HTTP 200 / `ok`.
- The first recent/detail load created the SQLite database in `/app/data`.
- After `docker compose ... up -d --force-recreate` without volume deletion, the cache
  still contained 6 entries. The first recent request returned five games with
  `persistent_cache_hit_count=1` and `persistent_cache_write_count=0`.
- Public browser smoke: five recent cards, full-intelligence switch enabled, no framework
  name in visible copy, zero console errors.
