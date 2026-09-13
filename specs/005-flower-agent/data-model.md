# Data Model: 种花 Agent

## PlantProfile

A curated, immutable description of one common flowering plant.

| Field | Type | Rules |
|---|---|---|
| `canonical_name` | string | 1–100 chars, unique |
| `aliases` | list[string] | max 32, de-duplicated |
| `scientific_name` | string? | max 160; never inferred |
| `light_preference` | string | conditional, not a fixed promise |
| `temperature_range_c` | tuple[float,float]? | -50..70, ordered |
| `soil_preference` | string | includes drainage/aeration considerations |
| `watering_principle` | string | observation-triggered rather than fixed calendar only |
| `fertilization_principle` | string | label-first and conservative |
| `pruning_principle` | string? | optional |
| `propagation_principle` | string? | optional |
| `common_issues` | list[string] | max 24 |
| `safety_notes` | list[string] | max 24 |
| `toxicity` | enum | `none_known|low|caution|toxic|unknown` |

Aliases may resolve to zero, one or multiple profiles. A multiple match is an ambiguity and must not silently choose a plant.

## GardenContext

Bounded, session-owned state used to resolve follow-ups.

| Field | Type | Rules |
|---|---|---|
| `session_id` | UUID? | opaque; never shown in prose |
| `plant` | PlantProfile|string? | latest explicitly resolved plant |
| `location` | string? | coarse city/region/room only; no street/unit |
| `climate` | string? | coarse descriptor |
| `light` | enum|string? | `full_sun|partial_sun|bright_indirect|shade|unknown` |
| `container` | ContainerInfo|string? | type, optional diameter/depth/drainage |
| `season` | string? | user-supplied month/season |
| `recent_observations` | list[string] | max 8 × 500 chars |
| `recent_questions` | list[string] | max 8 × 500 chars |
| `updated_at` | aware UTC datetime | required |
| `turn_count` | integer | non-negative, bounded |

State transition per turn:

```text
load context -> parse explicit slots -> resolve pronoun only when gardening signal exists
-> merge confirmed slots -> append bounded observation/question -> save with TTL
```

Explicit plant names override prior context. Ambiguous names do not inherit the previous plant. “新对话” creates a new session and therefore an empty context.

## FlowerParseResult

One request's interpreted intent and evidence-free slot resolution.

- `intent_name`: selection, identification, watering, light, soil, fertilizing, pruning, propagation, pest/disease, seasonal plan, general care, safety or out-of-scope.
- `resolved_plant`: profile only when one catalogue match is confirmed.
- `used_context_reference`: true only when a pronoun or valid follow-up inherited state.
- `missing_slots` and `clarification`: request only material information.
- `observations`: symptom words actually present in the user text.
- `safety_notice`: non-null means the request must not reach search/model.

## CarePlan

A list of conditional actions generated from one profile and context.

- Each action has category, action, trigger/condition and priority.
- Frequency is a range or observation trigger, not invented exact timing.
- `uncertainty` explains which environment variable may change the plan.
- A plan never contains pesticide mixture ratios or a medical/veterinary diagnosis.

## SearchObservation

A cleaned, bounded candidate from fixed public search.

| Field | Rule |
|---|---|
| title/snippet | HTML, URL, controls and instruction-like text removed |
| query | server-owned bounded flower query |
| retrieved time | aware UTC timestamp |
| relevance | 0..1 |
| verification | defaults to `unverified`; search-only answer is at most partial |
| source URL/label | retained internally only, never projected into public prose |

Search observations are not persisted as conversation truth and cannot prove toxicity, diagnosis, chemical concentration or regulation without a stronger verified source.

## SafetyNotice

Terminal pre-retrieval result for a dangerous request.

- `category`: chemical mixing, unknown ingestion, pet exposure, human exposure, high-risk chemical, unsafe disposal or unknown.
- `message`: immediate conservative conclusion.
- `immediate_actions`: actions safe before species/product identification.
- `do_not_do`: explicitly prohibited actions.
- `seek_help`: poison-control, emergency, medical, veterinary, product-label or local plant-protection direction as appropriate.

## Public ChatResult

The shared sync/SSE completion envelope contains request/session IDs, status, Markdown, typed blocks, Beijing freshness, evidence state, origin, optional follow-up, notices, latency and provider-neutral composition status. A safe `garden_context` projection may contain only plant, coarse location, light, container and season labels. It must never include exact address, transcript, provider metadata or internal tool observations.
