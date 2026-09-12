# Research: Alibaba Cloud IQS UnifiedSearch

## Decision 1: Use the direct UnifiedSearch HTTP API

**Decision**: Integrate `POST https://cloud-iqs.aliyuncs.com/search/unified` as a typed search adapter.

**Rationale**: The linked service is explicitly an open-domain search engine for Agents. Unlike model-integrated web search, it returns search records without replacing the existing model. Unlike an MCP client, the direct API fits the existing `NewsQuery` provider port without adding a protocol runtime.

**Alternatives considered**:

- Model-integrated web search: rejected because it couples retrieval to a second model call and changes the existing Agent/model architecture.
- Alibaba WebSearch MCP: viable, but adds an MCP transport/client when the project already has a typed search port.
- Existing authenticated Baidu search: retained as fallback; its current account is out of balance.
- HTML search scraping: retained only as a bounded fallback because verification pages make it operationally unreliable.

**Source**: [UnifiedSearch API](https://help.aliyun.com/zh/document_detail/2883041.html)

## Decision 2: Default to LiteAdvanced with a low-cost payload

**Decision**: Send `engineType=LiteAdvanced`, request at most five records, enable reranking, and disable `mainText`, `markdownText`, `richMainBody`, and enhanced `summary`.

**Rationale**: The official engine comparison describes LiteAdvanced as the lowest-cost option, with approximately 500 ms average latency, semantic retrieval and configurable 1–50 results. The service timeout is five seconds. Snippets are sufficient for candidate selection; requesting enhanced summary adds a billed value-added operation, while full body fields materially increase prompt and response size.

**Alternatives considered**:

- Generic: fresher and stronger for long-tail queries, but returns a fixed larger set and is not the lowest-cost default.
- GenericAdvanced: higher authority recall but explicitly a paid enhanced option.
- Deep: approximately six-second average latency conflicts with the documented five-second server timeout and the Agent tool budget.

**Source**: [Engine comparison](https://help.aliyun.com/zh/document_detail/3012727.html), [billing details](https://help.aliyun.com/zh/document_detail/2837302.html)

## Decision 3: Preserve typed partial evidence

**Decision**: Convert accepted `pageItems` to the existing `NewsItem` and `Evidence` models with source class `SEARCH`, medium trust, unknown freshness when publication time is absent, and `partial=true` for the result.

**Rationale**: Search snippets are discovery material, not an official box score or play-by-play source. This preserves the constitution's evidence-first rule and existing answer labels.

**Alternatives considered**:

- Mark high-rerank results as verified: rejected because relevance is not factual authority.
- Return raw provider JSON to the model: rejected for prompt-injection, size, privacy and vendor-leak risks.

## Decision 4: Make IQS primary and retain bounded fallbacks

**Decision**: Compose the search chain as IQS → authenticated Baidu search → Baidu HTML → DuckDuckGo when each option is enabled. A failure or empty relevant result may continue only while request budget remains.

**Rationale**: The user explicitly selected IQS. Existing adapters remain useful during trial expiry, quota, billing or transient failures. The application-level relevance filter must still discard unrelated matchup results.

**Alternatives considered**:

- Remove all fallbacks: rejected because supplementary search outages would unnecessarily reduce answer coverage.
- Merge every provider result: rejected because it spends more quota, increases latency and amplifies contradictory snippets.

## Decision 5: Use passive availability diagnostics

**Decision**: Track `unknown`, `ok`, or `degraded` from the most recently observed IQS call and expose only an implementation-neutral search state in readiness. Do not issue a paid search from `/readyz`.

**Rationale**: Health polling must not consume daily trial quota or make an optional external dependency remove a healthy API from service discovery.

**Alternatives considered**:

- Active search on every readiness call: rejected due to billing, quota and cascading outage risk.
- Continue reporting only `enabled`: rejected because it previously hid balance and availability failures from the operator.

## Decision 6: Classify documented failures without copying messages

**Decision**: Map invalid/missing key, service-not-activated and authorization failures to `AUTH`; arrears, trial expiry/limit and throttling to `RATE_LIMITED`; server/transport failures to `HTTP` or `TIMEOUT`; invalid or oversized bodies to schema/JSON errors. Return fixed safe messages only.

**Rationale**: The API documents these codes, but copying upstream text could leak account details or untrusted content. Retryability follows whether another attempt can succeed without operator action.

**Source**: [UnifiedSearch API error table](https://help.aliyun.com/zh/document_detail/2883041.html)
