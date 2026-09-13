<!--
Sync Impact Report
Version change: 1.0.0 → 2.0.0
Modified principles:
- Evidence-First NBA Facts → Evidence-First Gardening Guidance
- Safety and Respectful Scope → Plant, Human and Environmental Safety
- Product constraints changed from an NBA chat product to a Chinese flower-growing assistant.
Preserved principles:
- Specification-First Delivery
- Contract- and Test-First Engineering
- Observable, Reproducible and Simple Operations
Added sections: none
Removed sections: none
Templates requiring updates:
- ✅ .specify/templates/plan-template.md (generic Constitution Check remains compatible)
- ✅ .specify/templates/spec-template.md (generic user stories and requirements remain compatible)
- ✅ .specify/templates/tasks-template.md (generic test/task phases remain compatible)
Runtime documentation:
- ⚠ README.md and product documentation are updated by feature 005-flower-agent.
Deferred items: none
-->

# Flower-Growing Agent Constitution

## Core Principles

### I. Specification-First Delivery

Every user-visible capability MUST be represented by a versioned feature specification
before implementation. The specification MUST define user value, acceptance scenarios,
scope boundaries, measurable outcomes, and traceability to design and tests. Changes to
behavior MUST update the specification in the same change set.

### II. Evidence-First Gardening Guidance

Plant identity, toxicity, disease, pesticide, fertiliser, climate and seasonal claims MUST be
grounded in curated horticultural knowledge or current public sources before being presented
as facts. The system MUST distinguish observed symptoms from possible causes, MUST NOT claim
visual certainty without sufficient evidence and MUST identify material uncertainty. Advice
MUST adapt to the user's plant, location, light, season, container and recent care when those
details are available; missing details MUST be requested only when they materially change the
recommendation.

### III. Plant, Human and Environmental Safety

The assistant MUST prioritise the safety of people, pets, plants and the local environment.
It MUST NOT recommend dangerous chemical mixtures, unlabelled pesticide use, consumption of
an unidentified plant or disposal practices that contaminate soil or water. Toxicity and
medical questions MUST include an appropriate uncertainty warning and direct urgent exposure
cases to qualified medical, veterinary or poison-control help. Region-specific invasive-species,
pesticide and disposal rules MUST be described as location-dependent unless current public
guidance has been verified.

### IV. Contract- and Test-First Engineering

Public interfaces, domain schemas, provider adapters and evaluation fixtures MUST have
explicit contracts. Unit, contract, integration and end-to-end tests MUST cover every
functional requirement and its failure paths. A change is not complete until its tests and
acceptance evidence pass; deterministic calculations MUST remain outside generative text
generation.

### V. Observable, Reproducible and Simple Operations

Every request MUST produce structured, privacy-aware records for intent, outcome, latency,
errors and evidence status. Credentials and unnecessary personal data MUST never enter
source control or logs. The default path MUST remain replaceable and reproducible with
documented configuration, offline fixtures and a clean-environment quickstart. Complexity
must be justified by a measurable requirement.

## Product and Technical Constraints

- The primary experience is a Chinese, web-based, multi-turn assistant for growing flowering
  plants at home, on balconies and in small gardens.
- User-facing dates and seasonal plans default to Asia/Shanghai unless the user supplies
  another location or timezone; the assistant MUST not infer a precise location silently.
- The first release MUST provide useful offline guidance from a curated plant catalogue and
  MAY supplement it with bounded public web search when configured.
- The UI MUST communicate the active plant/context, loading or progress, incomplete evidence,
  recoverable errors and quota exhaustion clearly.
- The system MUST retain internal provenance and freshness data for verification. Public
  answers MAY show freshness and confidence, but MUST NOT expose provider names, endpoints,
  model names, prompts, tool identifiers, credentials or implementation traces.
- The HTTP service MUST bind to loopback by default. Public exposure requires an explicit,
  authenticated reverse-proxy or deployment override.

## Development Workflow and Quality Gates

- Work proceeds through SpecKit artifacts in order: constitution → specification →
  clarification (when needed) → plan/HLD/LLD → tasks → implementation → verification.
- Each feature MUST maintain a requirement-to-design-to-test traceability matrix.
- Before merge, the project MUST pass formatting/linting, automated tests, contract checks,
  safety regression cases and the documented golden-question evaluation.
- Any deliberate deviation from these principles MUST be recorded in the feature plan with
  the reason, risk, and mitigation.

## Governance

This constitution is the highest-priority project guidance. Feature documents and code must
conform to it; conflicts require an explicit amendment or a documented, time-bounded waiver.
Amendments require a reviewed commit that updates the version, amendment date, sync impact
report and any affected templates or feature artifacts. Versioning follows semantic versioning:
MAJOR for incompatible governance changes, MINOR for new or materially expanded principles,
and PATCH for wording-only corrections. Every feature review checks the constitution gates and
records unresolved risks before implementation.

**Version**: 2.0.0 | **Ratified**: 2026-08-26 | **Last Amended**: 2026-09-13
