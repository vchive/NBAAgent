# Research: 种花 Agent

## Decision 1: Reuse the existing HTTP/SSE shell, split the application domain

**Decision**: Add a server-owned `DomainRouter` whose default is flower, and implement an independent `FlowerChatUseCase` behind the existing `ChatResult` contract. Keep NBA only for an explicit compatibility profile or unambiguous sports request.

**Rationale**: The transport, auth, idempotency and streaming state machine are well-tested and domain-neutral. Reusing them lowers risk, while a separate use case prevents sports parsers, fixtures and fallbacks from contaminating flower answers.

**Alternatives considered**:

- Rewrite the whole service: rejected because it discards proven safety and SSE behavior.
- Add flower keywords to the NBA parser: rejected because the two fact models and missing-slot semantics are incompatible.

## Decision 2: Curated offline knowledge is the safety floor

**Decision**: Ship a versioned, bounded catalogue of common flowering plants with aliases, conditional care principles, common issues and toxicity notes. Use deterministic selection, symptom differential and care-plan renderers for a complete offline answer.

**Rationale**: Search and model access are optional and quota-limited. A local knowledge floor makes basic care reliable, testable and available during outages, while avoiding fabricated precision such as fixed watering intervals.

**Alternatives considered**:

- Vector database for the small catalogue: rejected for v1 because exact aliases plus conservative fuzzy matching are simpler and deterministic.
- Search-only answers: rejected because outages and untrusted snippets would directly degrade safety.

## Decision 3: Full intelligence uses application-owned continuity

**Decision**: Each web tab maintains one logical session until “新对话”. The application passes the latest four complete user/assistant turns and a compact `GardenContext` to a stateless bounded Agent. Native Agent memory, filesystem and session databases remain disabled.

**Rationale**: This resolves pronouns and comparative follow-ups while keeping state bounded, inspectable and isolated. Every current-turn fact and safety decision is still re-evaluated.

**Alternatives considered**:

- New model session per message with no history: rejected because “它/这盆” cannot work reliably.
- Enable native Agent memory: rejected because it widens the privacy and capability boundary.

## Decision 4: Exactly three flower tools

**Decision**: Register only `flower_lookup`, `care_plan` and `flower_search` for the flower runtime. The registry bridge validates argument shape, deadline, call count, result size and toolset on every invocation.

**Rationale**: These tasks cover stable knowledge, contextual planning and fresh long-tail material. They do not expose provider objects or generic I/O.

**Alternatives considered**:

- Generic browser/search URL tool: rejected because it enables SSRF, prompt injection and data leaks.
- One monolithic tool: rejected because it obscures evidence type and makes call policy harder to test.

## Decision 5: Search queries carry a domain marker

**Decision**: Reuse the fixed-host Baidu/Qianfan/Aliyun/DDG adapters but mark flower queries so their legacy NBA prefixes are not added. Search inputs are bounded plain text; results are stripped of HTML, URLs, invisible characters, prompt-like instructions and provider metadata.

**Rationale**: Prefixing a flower query with NBA silently destroys recall. A server-owned marker keeps backward compatibility while preventing the browser/model from choosing a provider or endpoint.

**Alternatives considered**:

- Duplicate every adapter for flower: rejected as unnecessary code and test duplication.
- Let the model call arbitrary search APIs: rejected by the fixed-egress constitution boundary.

## Decision 6: Evidence has three states and explicit origins

**Decision**: Use evidence state `verified|partial|none`; expose origin as `local`, `search`, `mixed` or legacy-compatible values. Curated profile rules may be verified locally. Search-only claims remain partial. Model prose inherits the strongest conservative state of the observations it used.

**Rationale**: “Verified” must not mean “the model said it”. The origin distinction explains why a useful offline answer can exist without pretending it was current web research.

**Alternatives considered**:

- Treat all model answers as unverified: too coarse when the model only paraphrases curated facts.
- Treat all search snippets as verified: unsafe and contrary to the PDF evidence model.

## Decision 7: Dangerous requests short-circuit before intelligence

**Decision**: A local flower safety classifier handles pesticide mixtures, unidentified ingestion, human/pet exposure, unsafe disposal and high-risk chemical operations before search, cache or Agent.

**Rationale**: No external text or model is needed to decide that these actions require immediate conservative guidance. Call-counter tests make the order observable.

**Alternatives considered**:

- Let the model decide safety: nondeterministic and too late if retrieval already occurred.
- Search product labels first: unsafe for unidentified products and adds delay to urgent guidance.

## Decision 8: Failure is additive, not a canned replacement

**Decision**: When local knowledge can answer, return that answer and attach a provider-neutral notice for quota, auth, timeout or temporary failure. Fail the whole turn only when no safe answer exists.

**Rationale**: This preserves usefulness and makes capability limitations visible without raw error leakage or misleading “no data” responses.

**Alternatives considered**:

- Silent fallback: rejected because the user cannot tell that freshness/intelligence is unavailable.
- Always return an HTTP error: rejected because it discards safe offline guidance.

## Decision 9: Keep deployment private by default

**Decision**: Compose and documented Uvicorn commands bind to `127.0.0.1`. Public exposure requires a separate authenticated reverse proxy/profile and secrets mounted outside source control.

**Rationale**: This preserves the explicit authorization boundary requested after the prior public demo was shut down.

**Alternatives considered**:

- Bind `0.0.0.0` by default: rejected because a local evaluation should not create an Internet-facing service accidentally.
