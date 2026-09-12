# High-Level Design: Structured Game Index

## Retrieval flow

```text
User query
  -> safety and session context
  -> deterministic entity/season/game parsing
  -> ProviderGateway
  -> IndexedProvider
       1. structured filters -> IndexedGame/Stats/Play
       2. narrative query -> constrained FTS5/BM25 documents
       3. miss/incomplete -> existing live provider or fixed-host detail adapter
       4. accepted public result -> indexed upsert
  -> deterministic verification/derivation
  -> full-intelligence synthesis when selected
  -> output guard
```

## Storage boundaries

- `highlights_cache`: exact endpoint response cache, unchanged.
- structured index tables: canonical public NBA records, reusable by chat and highlights.
- document/FTS tables: partial public narrative evidence, reusable by Agent search.
- fixture files: deterministic demo/test source, never copied into the public index.

## Retrieval policy

1. Recognized entities and seasons become mandatory relational/document filters.
2. Structured facts win over narrative evidence.
3. BM25 ranks only candidates inside the permitted scope.
4. Online sources fill missing records under the existing deadline.
5. Search excerpts can support analysis but never upgrade objective evidence.

## Failure behavior

Index initialization, reads, writes, or FTS failures are observable but fail open. The provider wrapper delegates to the current live stack. A source parser failure affects only that enrichment attempt. Conflicts stay stored as rejected telemetry rather than overwriting existing final facts.

At the browser boundary, a public/live chat transport failure is rendered as a concise, retryable unavailable state. It never crosses into the fixed-fixture answer path. Local fixture recovery exists only for an explicitly configured fixture profile and remains labelled as a demo snapshot.
