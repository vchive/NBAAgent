# Low-Level Design: IQS UnifiedSearch Adapter

## Class and configuration

`AliyunIQSSearchAdapter` implements the existing `search_news` method plus the dedicated
`search_web` alias used by pure Agent web searches and accepts:

- fixed endpoint (validated to the exact HTTPS host/path)
- inline key for isolated tests or key-file path for deployment
- HTTP client injection for contract tests
- timeout, maximum results and maximum response bytes
- optional fallback provider

Configuration keys:

- `ALIYUN_IQS_SEARCH_ENABLED`
- `ALIYUN_IQS_API_KEY` (local tests only)
- `ALIYUN_IQS_API_KEY_FILE` (deployment default)
- `ALIYUN_IQS_TIMEOUT_SECONDS`
- `ALIYUN_IQS_MAX_RESULTS`
- `ALIYUN_IQS_MAX_RESPONSE_BYTES`

## Query builder

1. Add canonical subject display names.
2. Add up to eight keywords.
3. Remove instruction-like entries and control characters.
4. Keep letters, numbers, Chinese characters, spaces, period, apostrophe and hyphen.
5. Normalize whitespace and add `NBA` when absent.
6. Prefer a concise value near 30 characters without losing subjects/year; enforce the service maximum of 500.

Date ranges are projected to Beijing dates because the product's display and user-relative time policy is Asia/Shanghai.

## Response parser

1. Reject redirects, error statuses, declared/actual oversize and non-JSON bodies.
2. Require a mapping root and list-valued `pageItems`.
3. Iterate no more than five rows.
4. Ignore non-mapping rows and `correlationTag=0`.
5. Sanitize title and snippet separately using existing external-text rules.
6. Validate article link scheme, host and lack of credentials.
7. Parse timezone-aware publication timestamps.
8. Deduplicate and create deterministic evidence IDs from SHA-256 digests.

## Fallback and operation budget

The adapter consumes one provider operation through the gateway hand-off. On a typed failure, it invokes its fallback only when budget remains. It never retries internally; gateway and fallback composition already bound downstream operations. The `search_web` path is selected directly and does not call structured sports news first.

## Passive diagnostics

The adapter stores only:

- last status
- canonical error kind
- retrieval timestamp

It never stores request text, response snippets or credential material in diagnostics. `SearchAugmentedProvider` exposes an aggregate implementation-neutral status to readiness.

## Security rules

- Exact host/path validation prevents SSRF.
- Redirects are rejected.
- Secret values are never interpolated into errors or logs.
- Search content is untrusted and cannot introduce tool calls or policy changes.
- Full text and images are neither requested nor parsed.
- Public response schemas continue to reject internal provider/tool vocabulary and URLs.

## Test matrix

| Case | Expected outcome |
|---|---|
| valid three-item response | sanitized partial candidates and evidence |
| prompt injection and markup | candidate removed or cleaned |
| duplicate/low-correlation row | omitted |
| invalid article URL | omitted |
| missing key | no HTTP call; AUTH; fallback eligible |
| documented 403/429 codes | mapped kind and retryability |
| redirect/oversize/invalid JSON/schema | fail closed |
| IQS failure plus fallback success | fallback candidates returned |
| IQS empty plus fallback success | fallback candidates returned |
| explicit matchup plus unrelated candidates | unrelated candidates absent from Agent observation |
| readiness before/after real call | unverified then ok/degraded without active probe |
| public answer | no provider, endpoint, key, tool or framework disclosure |
