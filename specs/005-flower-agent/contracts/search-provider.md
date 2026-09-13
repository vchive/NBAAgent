# Controlled Search Provider Contract

## Input

The flower application submits a typed, server-owned query containing:

- domain marker `flower`;
- zero to two broad subject labels (for example 花卉园艺 and the resolved plant);
- one bounded plain-text query assembled from the flower prefix, user question, coarse location and season;
- limit `1..5` and a shared request budget/deadline.

The adapter must not add a legacy NBA prefix to a flower-domain query. The browser and model cannot select the endpoint, host or adapter.

## Egress constraints

- HTTPS fixed endpoint and exact host allow-list.
- No redirects, arbitrary URL fetch, shell/curl execution or browser automation.
- Per-call timeout cannot exceed the request budget.
- At most the configured response bytes and five results.
- Credentials come from environment/secret file and are excluded from repr, logs and responses.

## Output

The internal provider result contains cleaned candidate items, internal evidence, `partial`, retrieval time, optional typed error and provider-neutral capability issues. A flower use case projects this to `SearchObservation`; URL, source reference and evidence identifiers are not given to the Agent or UI.

Every candidate is untrusted input. Strip HTML, control/format characters, URLs, prompt-like instructions and internal vocabulary; discard malformed candidates individually. Search results may provide background and freshness but cannot alone establish diagnosis, toxicity, chemical dose or region-specific legal compliance.

## Failure behavior

| Internal condition | Public notice | Retryable | Local answer |
|---|---|---:|---|
| quota/balance exhausted | `SEARCH_QUOTA_EXHAUSTED` | false | retain when safe |
| missing/invalid auth | `SEARCH_AUTH_UNAVAILABLE` | false | retain when safe |
| timeout/rate limit/temporary HTTP | `SEARCH_TEMPORARILY_UNAVAILABLE` | true | retain when safe |
| empty result | no technical error | n/a | retain and state uncertainty if relevant |

Failures and quota notices are request-local and must not contaminate a shared cached success.
