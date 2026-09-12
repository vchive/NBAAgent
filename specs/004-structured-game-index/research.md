# Research: Structured Game Index and Retrieval

## Decision 1: Split exact response cache from queryable evidence

**Decision**: Preserve `highlights_cache` for route projections and add normalized game/stat/play/document tables in the same SQLite database file.

**Rationale**: A cache key answers “have I executed this exact request?”; it cannot answer “which cached records match these entities?”. Separate tables prevent route serialization versions and fixture projections from defining chat retrieval semantics.

**Alternatives considered**:

- Scan every cached JSON payload: duplicate-prone, slow, version-coupled, and unable to enforce origin/conflict rules reliably.
- Replace the response cache: unnecessary migration risk for a working highlights path.

## Decision 2: Structured filters before lexical relevance

**Decision**: Query games by canonical team IDs, season, date, status, and game number. Apply BM25 only to narrative documents, after any available entity/season filters.

**Rationale**: Scores, dates, teams, and game identity are exact relations. Vector or lexical similarity can rank the wrong game and is not appropriate for deterministic facts. Narrative recaps need relevance ordering and fit text retrieval.

**Alternatives considered**:

- Dense vectors for all data: adds an embedding service/model, operational state, and nondeterministic similarity without improving exact game lookup.
- BM25 across serialized game JSON: loses relational constraints and makes numeric/date semantics fragile.

## Decision 3: Canonical terms for Chinese retrieval

**Decision**: FTS documents store a generated term field such as `nyk sas season_2025_26 finals 尼克斯 马刺`, and queries generate the same canonical terms through the entity resolver.

**Rationale**: SQLite's default tokenizer does not provide general Chinese word segmentation, and trigram tokenization cannot reliably match two-character team aliases such as “马刺”. Stable ASCII entity IDs bridge aliases without a tokenizer dependency.

**Alternatives considered**:

- `unicode61` over raw Chinese only: a punctuation-free short query can become one token and miss documents.
- trigram only: excludes important two-character terms and increases false positives.
- Add a Chinese segmentation dependency: larger image and language-specific operational complexity; not needed for the known entity catalog.

## Decision 4: FTS5 BM25 now, dense-vector extension later

**Decision**: Use SQLite FTS5/BM25 for the current corpus and leave a retrieval seam for future dense candidates.

**Rationale**: One NBA season plus bounded articles is small, lexical queries contain strong entity/season terms, SQLite already supports FTS5, and the approach is fast, explainable, offline, and easy to test. Add vectors only if golden-query evaluation shows semantic recall below target after canonical term expansion.

**Alternatives considered**:

- FAISS/Qdrant/pgvector immediately: extra deployment and embedding lifecycle with no demonstrated quality gain.
- LIKE queries: no field weighting or principled ranking and poor token behavior.

## Decision 5: Server-controlled Hupu adapter for structured gaps

**Decision**: Add a fixed-host, bounded HTML adapter for team schedule and box-score pages. The importer obtains URLs from configured team slugs or validated numeric game IDs only.

**Rationale**: The verified pages expose 2025-26 schedule, score, game ID, period scores, duration, venue/attendance when present, and complete player tables. It fills the concrete gap left by the current scoreboard archive without permitting user-controlled browsing.

**Alternatives considered**:

- Search snippets as structured facts: insufficient provenance and frequently truncated/inconsistent.
- Browser automation: slower and unnecessary because server-rendered HTML contains the data.
- Official Finals page only: initial HTML does not expose a complete directly parseable season archive.

## Decision 6: Provenance tiers and conflict policy

**Decision**: Only public structured adapters may write game/stat/play rows. Search candidates write `source_documents` with partial evidence. Demo snapshots are rejected. Existing complete final rows are not overwritten by conflicting scores or less-complete details.

**Rationale**: Persistence amplifies mistakes. Explicit tiers keep cached convenience from silently changing what “verified” means.

**Alternatives considered**:

- Last-write-wins: permits stale or demo data to corrupt facts.
- Never update: prevents legitimate enrichment of venue/player details.

## Decision 7: Local-first provider wrapper with fail-open behavior

**Decision**: Compose the index as a provider wrapper in live/hybrid deployments. It returns local hits, enriches known Hupu games on demand, delegates misses to the current provider, and persists only accepted public outcomes.

**Rationale**: The existing parser, deterministic path, highlights service, and Agent tools already depend on the provider port. One wrapper makes every path benefit from the index and avoids special-case code in prompts or UI.

**Alternatives considered**:

- Query the index only inside the Agent prompt/tool: non-full mode and highlights remain inconsistent.
- Add a second public endpoint: exposes implementation and duplicates routing decisions.
