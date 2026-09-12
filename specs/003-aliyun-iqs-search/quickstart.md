# Quickstart: IQS UnifiedSearch Integration

## Prerequisites

1. Activate Alibaba Cloud Information Query Service (IQS) and obtain its bearer API key.
2. Keep the key out of shell history and source control.
3. The trial documented by IQS is valid for 15 days and allows 1,000 calls per day. Verify current activation, quota and billing in the IQS console before deployment.

The documented formal price for the selected `LiteAdvanced` engine is 12 RMB per 1,000 calls. Provider terms can change; the console and current official billing page remain authoritative.

## Configure the secret

Run the repository helper interactively so the key is read without terminal echo:

```bash
cd /root/NBAAgent
make configure-aliyun-iqs-key
```

The helper writes only `secrets/aliyun_iqs_api_key` with restrictive permissions. Never pass the key as a command-line argument.

## Validate locally

```bash
pytest -q tests/contract/test_aliyun_iqs_search.py
pytest -q tests/contract/test_provider_mode.py tests/integration/test_full_intelligence.py
ruff check .
git diff --check
```

## Deploy

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.siliconflow.yml \
  up -d --build --force-recreate
```

## Check service and secret mount

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.siliconflow.yml \
  exec -T nba-agent sh -lc '
test -r /run/secrets/aliyun_iqs_api_key && echo "search key readable"
'

curl -sS http://127.0.0.1:8000/readyz
```

Expected before the first real query: API status `ok` and supplementary search `enabled_unverified`. A search outage must not make the overall API unready.

The readiness request is passive: it never sends a search query and therefore does not spend search quota. After an actual search, the supplementary dependency changes to `ok` or `degraded` based on the last observed result.

## Real acceptance query

After logging into the protected web application, ask:

```text
2026 尼克斯和马刺总决赛系列赛发生了什么？请核验来源冲突。
```

Expected:

- full-intelligence mode invokes bounded public search;
- output is concise and does not dump raw result lists;
- hard numerical claims remain partial unless structured NBA evidence confirms them;
- no internal provider, endpoint, key, tool or framework name appears in the UI.

## Live acceptance record (2026-09-04)

- The protected application accepted the query and returned HTTP 200 with `status=completed`.
- The answer was a 283-character synthesis rather than a raw result list and completed in 15.5 seconds.
- Evidence remained `partial`; readiness stayed `ok` and passive search availability became `ok` after the real request.
- The public answer contained no provider, endpoint, credential, tool or framework identifier.
