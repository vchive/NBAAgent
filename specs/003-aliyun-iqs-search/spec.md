# Feature Specification: Managed Public Search Integration

**Feature Branch**: `001-nba-chat-agent`

**Created**: 2026-09-03

**Status**: Draft

**Input**: User description: "接入阿里云信息查询服务的 UnifiedSearch 联网搜索 API；使用用户持有的密钥，作为 NBA Chat Agent 的在线搜索能力。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Search current NBA information (Priority: P1)

As an NBA fan, I can ask a current, historical, or long-tail NBA question that is not fully answered by structured game data and receive a concise answer grounded in relevant public search results.

**Why this priority**: The interview feedback identified weak open-domain NBA question answering as the product's primary gap.

**Independent Test**: Configure a working managed-search credential, ask about a recent matchup or NBA news item absent from the local records, and verify that the answer uses relevant public material without exposing implementation details.

**Acceptance Scenarios**:

1. **Given** a valid credential and an NBA question requiring public-web context, **When** the user submits the question in full-intelligence mode, **Then** the assistant retrieves relevant candidates and synthesizes a concise NBA-focused answer.
2. **Given** a query naming two teams, **When** public search returns mixed candidates, **Then** only candidates directly relevant to the requested matchup may influence or appear in the answer.
3. **Given** a search result that conflicts with structured game records, **When** the assistant composes the response, **Then** structured records remain authoritative and the conflict is described as unverified background rather than fact.

---

### User Story 2 - Continue safely during search failure (Priority: P2)

As an NBA fan, I receive a useful, honest response when the primary public-search service is unavailable, rate limited, out of quota, or returns malformed or irrelevant content.

**Why this priority**: External search availability must not make the conversational service misleading or unusable.

**Independent Test**: Simulate authentication, quota, timeout, malformed-response, and irrelevant-result failures and verify that the assistant uses a bounded fallback when possible or reports the missing evidence without fabricating facts.

**Acceptance Scenarios**:

1. **Given** the primary search service fails, **When** a configured fallback has relevant results, **Then** the request continues with those results within the existing response deadline.
2. **Given** every search source fails or returns unrelated material, **When** the request completes, **Then** unrelated material is omitted and the assistant clearly says that directly relevant public information was not found.
3. **Given** an external result contains markup, control characters, oversized text, or prompt-like instructions, **When** it is processed, **Then** unsafe content is removed and cannot alter assistant policy.

---

### User Story 3 - Configure and diagnose search securely (Priority: P3)

As an operator, I can configure the managed search credential outside source control, deploy the application, and distinguish search configuration from actual provider availability without exposing the credential or provider implementation in the public UI.

**Why this priority**: Reliable operation requires actionable diagnostics while preserving the product-facing abstraction and credential security.

**Independent Test**: Deploy once with a valid secret and once with a missing or invalid secret; confirm that diagnostics distinguish the states, logs contain no credential value, and user-facing responses contain no provider or internal framework name.

**Acceptance Scenarios**:

1. **Given** a readable valid secret, **When** the service starts, **Then** public search is available without placing the secret in an environment dump, repository file, response, or log.
2. **Given** a missing, unreadable, or invalid secret, **When** diagnostics are requested, **Then** the operator sees a safe degraded state while the main chat service remains available.
3. **Given** the search implementation changes, **When** an end user receives an answer, **Then** the UI describes only public information and verification state, not vendor, endpoint, credential, tool, or framework details.

### Edge Cases

- The query is blank after safety normalization or exceeds the external service's supported length.
- The result set contains duplicates, year-only titles, unrelated teams, or empty snippets.
- The response is larger than the configured maximum, not valid JSON, redirects unexpectedly, or exceeds the deadline.
- The provider reports invalid credentials, service not activated, arrears, an expired trial, throttling, or a daily trial limit.
- A result has no publication time, has a future publication time, or names a source but provides no usable content.
- The primary search succeeds with an empty result set while a fallback could still provide relevant material.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST use the operator-selected managed public-search service as the first search source when it is enabled and securely configured.
- **FR-002**: The system MUST convert long conversational questions into bounded, NBA-scoped search text without changing the requested teams, players, season, date, or intent.
- **FR-003**: The system MUST accept at most five search candidates per operation and retain a bounded title, excerpt, publication time, relevance signal, and internal provenance for each accepted candidate.
- **FR-004**: The system MUST treat public-search material as supplementary evidence and MUST NOT use it alone to mark scores, standings, player statistics, or play-by-play claims as verified.
- **FR-005**: The system MUST remove unsafe markup, control characters, embedded instructions, excessive text, and duplicate candidates before search content reaches answer generation.
- **FR-006**: For explicit matchup queries, the system MUST exclude candidates that do not establish direct relevance to the requested teams unless they answer a separately requested player or league-wide question.
- **FR-007**: The system MUST classify authentication, activation, billing, expiry, quota, throttling, timeout, transport, oversized-response, invalid-JSON, and schema failures into safe retryable or non-retryable outcomes.
- **FR-008**: When the primary search source fails or returns no relevant candidates, the system MUST try only configured, bounded fallback sources that fit within the same request budget.
- **FR-009**: When no directly relevant result remains, the system MUST omit unrelated candidates and return an honest no-evidence response rather than manufacture an answer.
- **FR-010**: Credentials MUST be supplied through a server-side secret, MUST NOT be committed to source control, and MUST NOT appear in logs, telemetry, public responses, or client code.
- **FR-011**: Operator diagnostics MUST distinguish search being configured from its most recently observed availability, while failure of supplementary search MUST NOT make deterministic NBA data unavailable.
- **FR-012**: The public interface MUST NOT expose vendor names, endpoints, internal tool names, prompts, or framework details.
- **FR-013**: Automated contract, integration, failure-path, safety, and end-to-end regression tests MUST cover this feature before deployment.

### Key Entities

- **Search Request**: A bounded NBA-scoped query with optional time constraints and requested result limit.
- **Search Candidate**: A sanitized public-page title and excerpt with optional publication time, relevance signal, and internal provenance.
- **Search Outcome**: The accepted candidates plus retrieval time, partial-evidence state, origin classification, and an optional safe error classification.
- **Search Availability**: Operator-visible configuration and last-observed runtime state that contains no credential or external payload.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All acceptance queries with an available relevant public result complete within the existing 65-second user-visible request deadline.
- **SC-002**: In a regression set of explicit two-team searches, 100% of candidates shown to answer generation mention or clearly describe both requested teams.
- **SC-003**: Across authentication, quota, timeout, malformed-response, and irrelevant-result tests, 100% of responses avoid fabricated NBA facts and unrelated search snippets.
- **SC-004**: Across source, logs, telemetry, HTTP responses, and browser assets, automated secret scanning finds zero copies of the configured credential.
- **SC-005**: All existing deterministic, safety, conversation-memory, and browser regression suites continue to pass after the integration.
- **SC-006**: An operator can configure the secret, deploy, run a real search probe, and identify success or the safe failure category in under ten minutes using the documented guide.

## Assumptions

- The operator already has or will obtain a valid credential and activate the selected managed public-search service.
- Structured NBA sources and the local cache remain the authority for scores, schedules, player statistics, and play-by-play.
- Search is primarily used for current news, venue and personnel context, tactical reporting, and other long-tail public information.
- The existing full-intelligence routing, request deadline, authentication, and public response-sanitization policies remain in force.
- The selected managed service may impose a time-limited trial, daily quota, or paid usage after the trial; quota ownership remains an operator responsibility.
