<!--
Sync Impact Report
Version change: template → 1.0.0
Modified principles: template placeholders replaced with five project principles.
Added sections: Product and technical constraints; Development workflow and quality gates.
Removed sections: none (template placeholders were replaced).
Templates requiring updates: plan/spec/tasks templates reviewed; no structural changes required.
Follow-up decisions: exact production data provider and hosting choice remain feature-level decisions.
-->

# NBA Chat Agent Constitution

## Core Principles

### I. Specification-First Delivery

Every user-visible capability MUST be represented by a versioned feature specification
before implementation. The specification MUST define user value, acceptance scenarios,
scope boundaries, measurable outcomes, and traceability to design and tests. Changes to
behavior MUST update the specification in the same change set.

### II. Evidence-First NBA Facts

Objective answers MUST be grounded in current, publicly accessible and reliable NBA data.
The system MUST verify scores, dates, standings, player/team statistics, aggregates and
play-by-play claims before presenting them. It MUST never invent a number or silently trust
a user-supplied premise. Unverified information MUST be identified as unavailable or
uncertain. Derived totals MUST be calculated from verified records rather than memory.

### III. Safety and Respectful Scope

Safety filtering MUST occur before external retrieval for all topics covered by the brief's
red lines, including politics or sensitive social controversy, private gossip, legal/criminal
claims, unsupported game-fixing allegations, gambling or betting, and hateful or abusive
content. A blocked request MUST receive a concise, respectful redirection to basketball;
the system MUST not retrieve data first or expose internal implementation details.

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

- The primary experience is a Chinese, web-based, multi-turn chat for NBA fans.
- User-facing time defaults to Asia/Shanghai (UTC+8); internal time values MUST be
  unambiguous and season labels MUST support cross-calendar-year NBA seasons.
- The first release MUST use public Internet data sources and MUST NOT depend on an
  undisclosed internal database.
- The UI MUST communicate loading, streaming or progress, empty results and recoverable
  errors clearly.
- The system MUST retain internal provenance and freshness data for verification; user-facing
  answers may show the data-as-of time and verification status, but MUST NOT expose provider
  names, endpoints, field names, prompts or implementation traces unless a later product
  decision explicitly permits it.

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

**Version**: 1.0.0 | **Ratified**: 2026-08-26 | **Last Amended**: 2026-08-26
