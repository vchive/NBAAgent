# High-Level Design: Managed Public Search Integration

## Context

The chat system already separates deterministic NBA retrieval from supplementary public search. IQS is introduced only inside the public-search branch; no browser, public API, session or Agent tool contract changes.

```text
User question
    │
    ▼
Safety + session context
    │
    ▼
Full-intelligence Agent
    ├── structured NBA tools ──> official/public structured provider + cache
    │
    └── public search tool ────> IQS UnifiedSearch
                                  └── existing bounded fallbacks
    │
    ▼
Evidence-aware composition
    │
    ▼
Public response sanitization
```

## Responsibilities

- **Composition root**: builds the fallback chain and makes IQS first when enabled.
- **IQS adapter**: validates fixed egress, loads the secret, projects the request, enforces time/size budgets, sanitizes candidates and maps failures.
- **Search augmentation wrapper**: merges structured-news and public-search candidates without changing deterministic NBA operations.
- **Application relevance policy**: removes unrelated matchup candidates and prevents web-only hard facts from being presented as verified.
- **Readiness endpoint**: reports passive search availability without issuing paid probes or exposing a provider.

## Search sequence

```text
Agent       Application       Gateway       IQS       Fallback
  | nba_search |                |            |            |
  |----------->| typed query    |            |            |
  |            |--------------->| POST       |            |
  |            |                |----------->|            |
  |            |                | candidates |            |
  |            |                |<-----------|            |
  |            | sanitize/filter/partial evidence         |
  |<-----------| concise observation                       |

On IQS failure/empty:
  |            |                |------------ fallback --->|
```

## Evidence and failure boundary

Search snippets may support narrative context, but cannot establish a score, standing, stat line or play-by-play event. If structured and search material conflict, structured records win and the search claim stays explicitly unverified. If all candidates are irrelevant, none are sent to answer generation.

## Operational design

- Credential is a read-only Docker Secret.
- IQS is disabled by default in offline/local profiles.
- The live Compose profile enables it only when the secret file is mounted.
- Readiness is passive: `enabled_unverified`, `ok` or `degraded`; it never spends search quota.
- Existing fallbacks and deterministic fixture mode preserve reproducible development and evaluation.
