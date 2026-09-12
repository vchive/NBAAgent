# Feature Specification: Structured Game Index and Retrieval

**Feature Branch**: `004-structured-game-index`

**Created**: 2026-09-04

**Status**: Complete

**Input**: User description: "优化 SQLite 赛事缓存检索；结构化字段精确查询，文本证据使用 BM25，并让漫游问答能够命中缓存赛事、在缺失时补充公开数据。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find cached games by natural-language matchup (Priority: P1)

As an NBA fan, I can enter a short query such as “2026尼克斯-马刺” and receive the relevant season series and games already held in the local public-data index, even when my wording is not an exact cache key.

**Why this priority**: The current response cache cannot answer natural-language lookups, causing known games to be reported as absent and directly reducing answer quality.

**Independent Test**: Load a season schedule containing the 2026 Knicks–Spurs Finals, restart the service, ask the short matchup query, and verify that the indexed G1–G5 records are returned without a network request.

**Acceptance Scenarios**:

1. **Given** indexed games for two named teams and a season, **When** a user asks with Chinese aliases, abbreviations, punctuation, or reversed team order, **Then** the same canonical matchup is found and ordered by game date.
2. **Given** multiple seasons or multiple games between the teams, **When** the query includes a season, date, or game number, **Then** those fields constrain the result before relevance ranking.
3. **Given** an indexed final game, **When** the user asks for its winner, score, venue, coach, or player line, **Then** every available field is returned from the structured record with its evidence state and retrieval time.

---

### User Story 2 - Retrieve relevant cached public evidence (Priority: P2)

As an NBA fan, I can ask for a recap, tactical background, or loosely worded historical fact and receive a concise answer based on the most relevant cached public documents instead of an exact-string cache miss.

**Why this priority**: Narrative evidence does not fit exact relational lookup, while returning raw search-result snippets produces weak and unattractive answers.

**Independent Test**: Index several Chinese articles about different teams and games, query a Knicks–Spurs Finals topic, and verify that only documents matching the canonical entities and season are ranked, summarized, and supplied to answer generation.

**Acceptance Scenarios**:

1. **Given** multiple indexed documents, **When** a query names teams and a season, **Then** entity and season filters are applied before text relevance ranking.
2. **Given** Chinese team aliases shorter than three characters, **When** a user searches them, **Then** canonical entity tokens permit retrieval without depending on character-trigram behavior.
3. **Given** cached documents disagree or are not authoritative enough for a requested number, **When** the answer is generated, **Then** the discrepancy remains partial evidence and is not promoted to a verified structured fact.

---

### User Story 3 - Fill and preserve a trustworthy reusable index (Priority: P3)

As an operator, I can pre-load a season schedule and fetch game details on demand from allow-listed public sources, while demo fixtures and unverified web snippets remain separated from verified public records.

**Why this priority**: Persistent retrieval only improves answers if records survive restarts, remain attributable, and cannot be polluted by fixed demo data.

**Independent Test**: Warm the 2025-26 season, fetch the five Finals details, restart the application, and verify that the same records remain queryable while an attempted demo write is rejected from the public index.

**Acceptance Scenarios**:

1. **Given** an allow-listed public schedule page, **When** the warm job runs repeatedly, **Then** it upserts the same canonical games idempotently without duplicating records.
2. **Given** a public game-detail page, **When** the detail is fetched, **Then** available score, period, venue, coach, attendance, duration, and player statistics are stored without inventing absent fields.
3. **Given** demo-snapshot data or web-search excerpts, **When** persistence is attempted, **Then** demo data is excluded from the public game index and excerpts enter only the partial document collection.
4. **Given** an unavailable public source, **When** a cached record is still valid, **Then** the cached answer remains usable and clearly carries its stored freshness and provenance state.

### User Story 4 - Continue an intelligent series conversation (Priority: P1)

As an NBA fan, after I ask about a matchup or series I can naturally ask which game was most worth watching, challenge that recommendation, and continue into the recommended game's details without repeating the teams, season, or game number.

**Why this priority**: A chat product that retrieves the first answer but loses the subject on the next turn is only a search demo, not a useful NBA assistant.

**Independent Test**: In one new session ask “2026尼克斯-马刺”, “你觉得最精华的是哪一场？”, “为什么不是G2？”, and “你刚推荐的那场最后一分钟发生了什么？”. Verify that every turn remains in the same series, one game is explicitly recommended with evidence, and the final turn queries that verified game rather than a generic or demo fixture.

**Acceptance Scenarios**:

1. **Given** a verified series is active, **When** the user asks which game was most exciting, watchable, close, consequential, or worth recommending using a natural paraphrase, **Then** the question is treated as complete, one game is selected, and two to four series-scoped reasons are provided without asking for the teams again.
2. **Given** the user challenges a recommendation or refers to “the game you just recommended”, **When** the next answer needs facts, **Then** only a game from the current request's verified candidate set may become active and all factual details are re-read for that game.
3. **Given** a full-intelligence recommendation, comparison, recap, or tactical question, **When** the intelligent runtime succeeds, **Then** its concise synthesis remains the public answer after factual guards; a generic parser/template must not replace it.
4. **Given** the runtime times out, returns a generic clarification, chooses an irrelevant tool, or emits incomplete prose, **When** trusted observations can answer the question, **Then** the service returns a scoped recovery with truthful status; otherwise it reports the specific limitation and never inserts an unrelated game or fixed demo fact.
5. **Given** the live/public browser cannot reach the chat service, **When** a request fails, **Then** the user receives a retryable error state and no fixed fixture answer is silently substituted.

### Edge Cases

- Team aliases overlap, are only two Chinese characters, use English abbreviations, or appear in reverse home/away order.
- A season crosses calendar years and the user supplies only the ending year.
- Multiple games match a matchup but the user omits a game number or date.
- A public page contains postponed or unplayed placeholder games with no score.
- A detail page omits venue, coach, duration, or play-by-play while still containing a valid score.
- A source changes markup, redirects, returns oversized content, or embeds unsafe instructions.
- Two sources disagree on a final score or player statistic.
- A search headline contains a number or player name that its summary does not support.
- A recap sentence negates an event, assigns it to the opponent, or describes another game in the series.
- A subjective recommendation uses a paraphrase such as “最有观赏价值”, “只能选一场”, “哪一战最精彩”, or “比分最接近”.
- A reverse-polarity request such as “最不精彩” or “不推荐哪场” must not be recovered as the best game.
- A model or tool returns a valid-looking result for a different matchup than the active series or selected game.
- A network failure occurs in the live/public browser while local demo fixtures are bundled with the application.
- A valid post-game series-award sentence refers to one game's performance as context without claiming the award belongs to that game.
- One sentence mixes a verified single-game line with an unrelated series award or per-game aggregate.
- An away winner has a 94–90 public result while its final play row stores `home_score_after=90` and `away_score_after=94`; every public PBP score must retain the correct team relation without looking reversed.
- The text index is unavailable while structured rows remain readable, or the entire index is unavailable while live providers still work.
- A public/live browser request loses its network connection after the page loaded; the UI must retain a retryable error instead of answering from a fixed fixture.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST persist canonical games separately from route-response cache entries.
- **FR-002**: The system MUST normalize team aliases, season labels, dates, game numbers, and source identifiers before retrieval or storage.
- **FR-003**: The system MUST apply structured team, season, date, game-number, and status constraints before any free-text relevance ranking.
- **FR-004**: The system MUST support relevance-ranked retrieval over cached public documents and MUST support two-character Chinese team names through canonical entity terms rather than raw substring assumptions.
- **FR-005**: The system MUST make the persistent game index available to the normal chat provider path, including full-intelligence tool calls and non-model deterministic retrieval.
- **FR-006**: The system MUST retrieve indexed game summaries and player statistics by canonical game identity after a matchup search.
- **FR-007**: The system MUST distinguish verified structured game records, partially verified public documents, and fixed demo snapshots in storage and retrieval.
- **FR-008**: Fixed demo snapshots MUST NOT be written into or returned from the public structured index, and the browser MUST NOT substitute one for a failed public/live chat request. Fixture transport MUST require an explicit fixture runtime configuration.
- **FR-009**: Online-search excerpts MUST NOT become verified scores, statistics, play-by-play rows, or verified narrative claims; they MAY be stored as bounded partial documents for later relevance retrieval. A search title is ranking metadata only and MUST NOT authorize a number, proper name, event, or causal claim that is absent from the summary body. A claim supported only by search-summary text MUST retain partial/analytical wording rather than be labelled as verified fact.
- **FR-010**: The system MUST upsert public records idempotently, preserve source retrieval time, and reject an incoming final-score conflict unless a documented higher-authority source resolves it.
- **FR-011**: The system MUST provide a bounded, allow-listed public schedule/detail importer that fails safely when markup, transport, or response-size validation fails.
- **FR-012**: Retrieval MUST remain available after application restart and MUST fail open to the existing live provider path when the local index is unavailable.
- **FR-013**: Answers produced from document retrieval MUST synthesize the relevant result rather than display an unbounded list of raw titles or excerpts. If an Agent turn ends after tool completion with a dangling heading, budget/termination status, or otherwise incomplete prose, the service MUST reconstruct a complete answer from the server-owned observations before returning it. The synthesis MUST preserve the scope and polarity of retrieved facts: a series award cannot be described as a single-game award; valid wording that an award was received after a game or was earned partly through that game's performance MUST remain intact; and a single-game win explanation MUST NOT use unrelated season/series aggregate statistics or unasked historical anecdotes as proof of that game's outcome. A concrete causal event MUST be supported by affirmative evidence for the same team and same game; a negated event, opponent event, other-game event, or search title MUST NOT authorize it. When one sentence mixes supported game facts with an unsupported award, aggregate, causal, or historical-aside clause, only the unsupported clause is removed.
- **FR-014**: Operator diagnostics MUST expose index availability, record counts, and retrieval outcomes without exposing provider names, raw URLs, prompts, or credentials in the user interface.
- **FR-015**: Automated unit, contract, integration, restart, safety, and golden-question tests MUST cover the feature and its failure paths before deployment.
- **FR-016**: A pure web-search request MUST use a dedicated search-provider operation and MUST NOT spend an operation on the structured sports-news endpoint first; local BM25 hits MAY satisfy historical requests without network access, while remote excerpts remain partial evidence.
- **FR-017**: The system MUST inherit verified teams, season, stage, and series scope for contextual game-selection paraphrases; an already scoped selection or recommendation question MUST NOT receive a generic request to restate the object.
- **FR-018**: In full-intelligence mode, recommendation, player comparison, recap, win-reason, and tactical questions MUST remain on intelligent synthesis after evidence retrieval. Deterministic composition MAY recover a runtime, relevance, or output-guard failure, but its answer and status MUST remain scoped to the requested entities and topic.
- **FR-019**: A game mentioned only by generated prose MUST NOT become session fact. A recommended G1–G7 may become the active game only when it resolves to one of the current request's verified structured candidates; later pronoun questions MUST re-query that canonical game.
- **FR-020**: Fixed demo fixtures MUST be opt-in and visibly identified. A live/public API or browser transport failure MUST NOT silently substitute a fixture answer, and a public or hybrid retrieval MUST NOT resolve a guessed fixture game identifier.
- **FR-021**: Public answers MUST use concise, professional Simplified Chinese, address the user as “您” by default, and MUST NOT expose internal model, framework, prompt, tool, provider, cache, database, or retrieval identifiers. The assistant system prompt MUST also avoid the private runtime brand.
- **FR-022**: When an insulting nickname is used to identify a person, the system MUST refuse to map it to a real player, state that the referent cannot be identified from that wording, and invite a neutral name or description without retrieval.
- **FR-023**: Acceptance reporting MUST distinguish deterministic fixture regressions from real intelligent-runtime/search tests and MUST report data-detail coverage and live latency honestly; fixture scores MUST NOT be presented as proof of live answer quality.
- **FR-024**: Model and search quota, authentication, rate-limit, and timeout failures MUST retain
  their typed class and retryability across fallback, indexing, Agent-tool, synchronous HTTP, and
  SSE boundaries. If usable indexed/live evidence survives, the answer MAY complete with a
  provider-neutral request-scoped notice; without usable facts or observations the result MUST be
  a technical failure rather than `no_data` or clarification. A successful fallback MUST retain
  the current request's capability issue, but that issue MUST NOT be persisted in the shared cache.
- **FR-025**: Public play-by-play tables, event narration, last-shot answers, and tactical fact
  citations MUST label every available score-after pair with its corresponding home and away team
  (or explicit home/away placeholders when the game projection is unavailable). A provider-order
  score pair MUST NOT be presented as an unlabeled final score.

### Key Entities

- **Indexed Game**: A canonical NBA game with season, teams, time, status, score, optional series/game number, venue, coaches, duration, attendance, evidence state, source identity, and freshness.
- **Indexed Player Stat**: A game-scoped player line associated with one indexed game and team, containing only available structured metrics.
- **Indexed Play**: An optional ordered game event with period, clock, participants, action, score-after, and source evidence.
- **Source Document**: A bounded public text item with title, content, canonical entity terms, optional season/game association, partial evidence state, content fingerprint, and freshness.
- **Canonical Entity Term**: A normalized team/player/season token used to bridge Chinese aliases and short natural-language queries to canonical identifiers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The acceptance query “2026尼克斯-马刺” returns all five completed Finals games from a warmed index after a service restart, with zero network calls.
- **SC-002**: Across a regression set of Chinese aliases, English abbreviations, reversed matchups, season labels, and game numbers, at least 95% of known indexed games appear in the first five results and no result violates an explicit entity or season constraint.
- **SC-003**: Cached matchup retrieval completes within 300 milliseconds at the 95th percentile for an index containing one full NBA season and its available detail records.
- **SC-004**: In conflict, demo-pollution, unsafe-document, malformed-page, and source-failure tests, 100% of responses avoid presenting unverified numeric claims as verified facts.
- **SC-005**: A repeated season warm produces zero duplicate games or player lines and retains all previously more-complete fields.
- **SC-006**: All existing chat, provider, safety, highlights, browser, and evaluation regressions continue to pass.
- **SC-007**: For acceptance queries with cached relevant documents but no structured answer, the user receives a concise synthesized response rather than raw search-result excerpts in every test case. In the semantic-scope regression matrix, 100% of title-only, negated-event, opponent-event, other-game, award-scope, mixed-clause, unrelated-aggregate, and unasked-historical-aside cases preserve supported game facts while rejecting only unsupported claims.
- **SC-008**: The four-turn contextual recommendation journey and its positive paraphrase matrix complete without generic clarification, matchup drift, or unverified active-game mutation in 100% of deterministic integration runs.
- **SC-009**: In demo-isolation, wrong-tool, public-identifier guessing, and browser-network-failure tests, 100% of responses avoid unrelated fixture facts and expose a truthful completed, no-data, clarification, blocked, or retryable-error state.
- **SC-010**: The release report includes at least one repeated real-runtime test for each materially different PDF answer class exercised by the deployment, records response latency separately from fixture latency, and identifies any class whose structured evidence is incomplete.
- **SC-011**: In injected model/search quota, authentication, transient 429, timeout, empty-fallback,
  successful-fallback, cache-hit, synchronous HTTP, and SSE cases, 100% of results preserve the
  correct completed-with-notice or technical-failure state and never misreport the condition as
  ordinary no-data.
- **SC-012**: In deterministic and full-intelligence away-winner PBP regressions, 100% of score-after
  and terminal-score strings preserve the verified team-to-score relation and contain no unlabeled
  provider-order score pair.

## Assumptions

- Authentication remains unchanged. Session continuity, the pre-retrieval safety gate, full-intelligence routing, public answer projection, and browser failure behavior are in scope where needed to prevent context loss, internal-detail disclosure, or demo-data pollution of indexed answers.
- Structured fields are searched relationally; relevance ranking applies to narrative documents and secondary candidate ordering, not to canonical score lookup.
- The initial release uses lexical relevance plus canonical entity terms; dense-vector retrieval remains an optional later enhancement if evaluation demonstrates a measurable recall gap. Web-search retrieval is a separate provider operation so lexical cache hits and remote search can be composed without an unnecessary structured-news call.
- Public-source ingestion is server controlled and restricted to configured allow-listed hosts; arbitrary user-supplied URLs are out of scope.
- A full season schedule is warmed in batches, while large detail and play-by-play payloads may be fetched on demand and retained subject to storage limits.
