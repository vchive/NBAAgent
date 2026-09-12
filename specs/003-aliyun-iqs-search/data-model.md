# Data Model: Managed Public Search Integration

## Existing canonical entities reused

### NewsQuery

- `subject_refs`: canonical teams or players already resolved by the application
- `keywords`: bounded user/search-plan terms
- `date_range`: optional half-open UTC interval
- `limit`: requested maximum candidate count; the IQS adapter further caps it at five

Validation remains in the existing domain model. No provider-specific field is added.

### NewsItem

- `news_id`: deterministic digest derived from sanitized title and excerpt
- `title`: sanitized and bounded page title
- `summary`: sanitized and bounded snippet
- `published_utc`: optional normalized publication time
- `subject_refs`: canonical subjects copied from the request
- `evidence_id`: link to the internal evidence record

### Evidence

- `evidence_id`: stable per sanitized candidate
- `source_class`: always `SEARCH`
- `source_ref`: internal adapter identifier; never part of the public wire projection
- `url`: validated HTTP(S) article link used only as internal provenance
- `fetched_at_utc`: retrieval timestamp
- `data_as_of_utc`: parsed publication timestamp when available
- `trust`: `MEDIUM`
- `freshness`: derived only when publication time is valid; otherwise `UNKNOWN`

### ProviderResult[list[NewsItem]]

- `data`: zero to five accepted candidates
- `evidence`: corresponding internal evidence records
- `partial`: always true for a successful public-search operation
- `error`: typed safe error or null
- `retrieved_at_utc`: completion time

## Adapter-local state

### SearchAvailability

- `configured`: whether a syntactically valid key can be loaded
- `last_status`: `unknown`, `ok`, or `degraded`
- `last_error_kind`: optional canonical error category; no upstream message
- `last_checked_at_utc`: optional timestamp of the most recent real search

State transitions:

```text
startup ──> unknown
unknown/ok/degraded ── successful valid response ──> ok
unknown/ok/degraded ── typed provider failure ─────> degraded
```

This is process-local diagnostic state. It is not persisted and does not control factual verification.

## External response projection

Only the following `pageItems[]` fields are read:

- `title`
- `link`
- `snippet` (or `summary` if present despite being disabled in the request)
- `publishedTime`
- `rerankScore`
- `correlationTag`

All other response content, including full bodies, images, logos, scene items, provider query context and billing counters, is ignored.
