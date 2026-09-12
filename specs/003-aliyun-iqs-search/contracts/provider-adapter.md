# Contract: IQS UnifiedSearch Adapter

## Port

```text
search_news(NewsQuery, RequestBudget) -> ProviderResult[list[NewsItem]]
search_web(NewsQuery, RequestBudget) -> ProviderResult[list[NewsItem]]

`search_web` is the dedicated operation for pure Agent web lookups. It shares the same
bounded request/response and partial-evidence contract as `search_news`, but is not preceded
by a structured sports-news call.
```

The adapter is internal. No public HTTP request or response field changes.

## Fixed egress

- Method: `POST`
- Scheme: `https`
- Host: `cloud-iqs.aliyuncs.com`
- Path: `/search/unified`
- Redirects: rejected
- Authentication: `Authorization: Bearer <server-side secret>`
- Content type: `application/json`
- Timeout: at most 5.5 seconds and never greater than the remaining request budget
- Response size: at most 800,000 bytes

## Request projection

```json
{
  "query": "2026 NBA 尼克斯 马刺 总决赛",
  "engineType": "LiteAdvanced",
  "timeRange": "NoLimit",
  "contents": {
    "mainText": false,
    "markdownText": false,
    "richMainBody": false,
    "summary": false,
    "rerankScore": true
  },
  "advancedParams": {
    "numResults": "5"
  }
}
```

When `NewsQuery.date_range` exists, `advancedParams.startPublishedDate` and `advancedParams.endPublishedDate` use Beijing calendar dates. Query text is plain text, control-free, injection-filtered, NBA scoped, non-empty, and no longer than 500 characters; the builder targets 30 characters when it can do so without dropping canonical subjects, season/year or intent.

## Success projection

- Accept only a JSON object containing a list-valued `pageItems`.
- Examine no more than `max_results` items.
- Require non-empty sanitized `title` and `snippet`/`summary`.
- Reject candidates with `correlationTag == 0`.
- Require `link` to be an absolute HTTP(S) URL with no userinfo.
- Parse ISO `publishedTime` only when timezone-aware.
- Deduplicate by normalized title plus excerpt.
- Bound title to 500 characters and excerpt to 1,200 characters.
- Return `partial=true`, even for HTTP 200.

## Error mapping

| HTTP/provider code | Internal kind | Retryable | Fallback allowed |
|---|---|---:|---:|
| missing/malformed local key | AUTH | no | yes |
| 401, 404 InvalidAccessKeyId.NotFound | AUTH | no | yes |
| 403 Retrieval.NotActivate | AUTH | no | yes |
| 403 Retrieval.NotAuthorised | AUTH | no | yes |
| 403 Retrieval.Arrears | RATE_LIMITED | no | yes |
| 403 Retrieval.TestUserPeriodExpired | RATE_LIMITED | no | yes |
| 429 Retrieval.TestUserQueryExceeded | RATE_LIMITED | no | yes |
| 429 Retrieval.Throttling.User | RATE_LIMITED | yes | yes |
| 5xx or transport error | HTTP | yes | yes |
| deadline/timeout | TIMEOUT | yes | yes |
| invalid JSON | INVALID_JSON | no | yes |
| redirect, oversized or wrong schema | SCHEMA_MISMATCH | no | yes |

No upstream body or credential may be copied into `safe_message`.

## Diagnostic contract

Before any real request, search status is `enabled_unverified` when any search adapter is enabled. After a real attempt, readiness may report `ok` or `degraded` based on the last observed search-chain state. Search degradation does not change overall API readiness.
