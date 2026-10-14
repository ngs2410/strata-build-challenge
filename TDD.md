# Strata TDD — Technical Design Document

## Preamble

Technical design for Strata v1 — a regulatory change-to-action workspace
for in-house regulatory affairs teams. Scope, user, and non-goals are
established in [PRD.md](PRD.md); this document covers the architecture
and the reasoning behind each piece.

Governing documents for code written against this design:
- [**PRD.md**](PRD.md) — scope, user, non-goals, success metrics
- [**CLAUDE.md**](CLAUDE.md) — code style, mandatory design-compliance
  rules, tooling, engineering principles
- [**PROMPT_CONVENTIONS.md**](PROMPT_CONVENTIONS.md) — authoring
  conventions for LLM tasks
- [**TEST_QUALITY.md**](TEST_QUALITY.md) — test-quality rules
- [**JEV.md**](JEV.md) — reference for the Jev discrimination model
  (used across the design)

**Cross-references to source.** Each design section carries an italic
`_Source: ..._` line linking to the exact module (and, where
specific, the line range) on the `main` branch at
<https://github.com/ngs2410/strata-build-challenge>. Line ranges are
accurate as of the most recent sync between this doc and the code —
if you find drift, that's a doc bug worth filing.

A note on scope: this is a build-challenge-sized prototype. For every
component below, the pattern is **Role → v1 → Production → Decisions**:
what the component does, how it's implemented now, how it scales, and
the alternatives considered with trade-offs. The "Production" column is
a specific scaling path, not a complete production design.

Strata uses a deliberate **two-model AI architecture**:
- **OpenAI frontier model** for *generation* — open-ended extraction,
  free-text rationale, candidate production.
- **Jev** (TypeSafe AI's System One decision model — see [JEV.md](JEV.md)) for
  *discrimination* — typed decisions over defined answer spaces with
  calibrated probabilities. v1 LLM-emulates the Jev-shaped calls through
  the same `ModelCaller` seam; migration to the real Jev client is a
  one-file change.
- **Deterministic code** owns everything else — orchestration, policy
  enforcement, I/O, aggregation.

The architectural insight is that regulatory change-to-action is a
*generation + many small discriminations* problem. The generation shape
(extracting an obligation from a passage) resists type constraints; the
discrimination shape (does this passage support this claim? is this
obligation affected? what's the routing destination?) is exactly the
shape Jev is architected for.

---

## 1. System Overview

```
┌───────────────────────────────────────────────────────────────────────────┐
│                              Operator (CLI)                                │
│      main_detect_changes.py   │   main_eval.py   │   main_review.py        │
└──────┬──────────────────────────────────────────────────────┬─────────────┘
       │                                                      │
       ▼                                                      ▼
┌──────────────────────────────────────────────────────┐  ┌──────────────────┐
│                   Core Pipeline (src/)               │  │  Web view        │
│                                                      │  │  src/web_app.py  │
│  (1) Ingest ───▶ ProceedingVersions + CompanyCtx     │  │  FastAPI+Jinja2  │
│                                                      │  │                  │
│  (2) Version Diff ─▶ Changes (added/removed/...)     │  │  /queue — Rachel │
│                                                      │  │  /audit — history│
│  (3) Citation Grounding ─▶ Changes w/ passage anchors│  │                  │
│                                                      │  └────────▲─────────┘
│  (4) Citation Verification ─▶ Verified Changes       │           │
│                                                      │           │ reads
│  (5) Obligation Extraction ─▶ ExtractedObligations   │           │ ReviewItem +
│                                                      │           │ AuditEvent
│  (6) Mapping ─▶ (affected items + action + routing)  │           │
│                                                      │           │
│  (7) Review Queue ─▶ ReviewItems + Audit events      ├───────────┘
│                                                      │
└──────────┬───────────────────────────────────────────┘
           │
           ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                       Cross-cutting services                               │
│                                                                            │
│  ModelCaller ──┬──▶ OpenAI (generation: extraction, free-text rationale)   │
│                │                                                           │
│                ├──▶ Jev (discrimination: Noul, Choice, Score — calibrated) │
│                │     [v1: LLM-emulated behind the seam; prod: real client] │
│                │                                                           │
│                └──▶ diskcache (plaintext JSON keys; replayable)            │
│                                                                            │
│  Store[T]  ──▶ JsonStore[T] (JSON + JSONL files on disk)                   │
│  Config    ──▶ load_domain_config(tenant_id, environment) → DomainConfig   │
│  Logging   ──▶ structlog (JSON to stderr)                                  │
└───────────────────────────────────────────────────────────────────────────┘
```

**Key properties, each enforced by code elsewhere in this document:**

1. Every claim carries a structural citation (`passage_id` + offsets).
   Enforced in [§3.3](#33-citation-grounding-depth-area); verified in
   [§3.4](#34-citation-verification-depth-area); measured in
   [§5](#5-evals-depth-area).
2. Low-confidence interpretations escalate to a human reviewer — the
   system fails to a reviewer, not to a guess. Enforced at the
   review-queue gate ([§3.7](#37-review--audit)); threshold is a
   `DomainConfig` hyperparameter ([§4.3](#43-configuration)).
3. Project state is a fold over an append-only audit log. Enforced in
   [§3.7](#37-review--audit); rollback is replay-to-timestamp.
4. Draft vs. final is a first-class attribute on every proceeding version
   (`Literal['draft', 'final']`). Enforced in [§2](#2-data-model).
5. Data isolation is per `(tenant_id, environment)`. Every facade method
   takes these as leading parameters ([§4.5](#45-data-isolation--multi-tenancy)).

**Depth areas** — components where the v1 implementation is more
thoroughly executed:
- **Citation grounding** (§3.3 + §3.4): structural anchors, a dedicated
  verifier, dedicated evals.
- **Evals** (§5): hand-labeled gold set, named failure modes with
  reproducing cases, reliability diagram for confidence calibration,
  metrics report produced on every end-to-end run.

Both map directly to the [PRD's trust metrics](PRD.md#4-trust-and-adoption-metrics-for-expert-users)
(citation precision ≥ 98%; mapping precision ≥ 90%; confidence
calibration ≥ 95% on high-confidence claims). The trust story falls
over if either is weak.

---

## 2. Data Model

Core Pydantic models shared across the pipeline. Each lives in
`src/config.py`; all use `model_config = ConfigDict(extra='forbid')` and
`Literal` types for every categorical field (per CLAUDE.md).

**Why Pydantic (not dataclasses, not dicts, not SQLAlchemy):** Pydantic
enforces schemas at every boundary — ingest, LLM response parsing, store
(de)serialization, HTTP request/response. Dataclasses don't validate;
dicts don't typecheck; SQLAlchemy's ORM shape would leak across our
`Store[T]` facade. Both OpenAI structured outputs and the Jev adapter
target Pydantic directly.

**Entity graph (ASCII — the shape before any one section's detail).**

```
External authority (one per proceeding):
    ProceedingVersion ──1:N─> Passage
         │                       │
         │ (version_diff emits   │ (every Citation ultimately points
         │  Changes grounded     │  into a Passage of either prior
         │  in Passage text)     │  or final version)
         v                       v
    Change ──1:0..1─> Citation (prior)   ──> Passage
          ──1:0..1─> Citation (new)      ──> Passage
      (grounding step .finds citing_span in passage.text →
       Citation.{start_offset, end_offset, passage_id};
       citation_verify flips Citation.verified)

Company context (per tenant, seeded as JSON):
    Obligation │ Project │ Document

Mapping (Change × company context):
    Change ──1:1─> Mapping ──1:N─> AffectedItem
                                     │
                                     ├─ item_type, item_id
                                     ├─ match_confidence,
                                     │  match_rationale
                                     └─ proposed_action,
                                        proposed_route  (per-item;
                                        no mapping-level summary —
                                        §8.6 closed in v1)

Rachel's queue (one ReviewItem per Mapping):
    Mapping ──1:1─> ReviewItem (status, priority)
         │              │
         │              └─ status = _fold_status over AssetDecisions
         │                 (pending > escalated > overridden > approved)
         │
         └─ for each AffectedItem index, 0..1 AssetDecision
            (decision ∈ {approved, overridden, dismissed, escalated},
             action, route, rationale, decided_at, actor)
            absence = pending; bulk ops share a batch_id

Audit log (append-only JSONL — fold-over-log is the Strata invariant):
    AuditEvent{kind, target_type, target_id,
               prior_value_json, new_value_json,
               rationale, batch_id, actor, occurred_at}
    one per state transition; event_id has a monotonic counter
    suffix to defeat microsecond collisions on bulk-apply.
```

### 2.1 ProceedingVersion

```python
class ProceedingVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: str
    environment: str
    proceeding_id: str                      # e.g. 'ferc-order-2222'
    version_id: str                         # e.g. 'nopr-2019-11' or 'final-2020-09'
    status: Literal['draft', 'final']
    docket_number: str                      # 'RM18-9'
    issued_at: datetime
    source_url: str
    passages: tuple[Passage, ...]
```

_Source: [`src/config.py` L51-L61](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L51-L61)._

**Decisions.**
- **`tenant_id` + `environment` are storage keys, not semantic
  ownership.** A regulator's proceeding version is tenant-independent
  — N customers of the same regulator would see the same NOPR + Final
  Rule text. v1 keys every persisted entity per `(tenant_id,
  environment)` because `Store[T]` is uniform (CLAUDE.md tenant-isolation
  rule), not because the version belongs to a tenant. The cross-tenant
  proceeding/change store is §8.9 (first next-move).
- **`status` is a required `Literal`, not a classifier task.** The user
  asserts draft/final at ingest time — it's a metadata property of the
  document, not a judgment call. Making this a classifier would be a bug:
  it would permit the system to be wrong about something known-at-source.
- **`passages` live on the version** — a passage is always-and-only in
  exactly one version. Cross-version passage identity is handled through
  structural IDs (§2.2), not shared objects.

### 2.2 Passage

```python
class Passage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passage_id: str          # structural ID — 'ferc-order-2222:final-2020-09:§35.28(g)(1)(ix):p3'
    section_path: tuple[str, ...]   # ('§35.28', 'g', '1', 'ix')
    position: int            # paragraph index within the section
    text: str
```

_Source: [`src/config.py` L43-L48](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L43-L48)._

**Decisions.**
- **Structural, human-readable `passage_id`** of the form
  `<proceeding>:<version>:<section-path>:p<n>`. Not a UUID, not a hash.
  Citations need to be greppable, renameable, and renderable in UIs
  without a resolution step. Same reasoning as CLAUDE.md's
  cache-keys-as-plaintext rule.
- **`position` is a paragraph index, not a byte offset.** Byte offsets
  drift under whitespace edits; paragraph position is stable to
  normalization.
- **Citation offsets live on `Citation` (§2.3), not on `Passage`.**
  Offsets are properties of the citation into the passage text, not of
  the passage itself.

### 2.3 Change and Citation

```python
class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    change_id: str
    tenant_id: str
    environment: str
    proceeding_id: str
    change_type: Literal['added', 'removed', 'tightened', 'loosened', 'cosmetic']
    description: str                        # short summary from the diff LLM task
    prior_citation: Citation | None         # None iff change_type == 'added'
    new_citation: Citation | None           # None iff change_type == 'removed'
    confidence: int                         # 0-5, per shared confidence rubric
    confidence_rationale: str
    detected_at: datetime


class Citation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passage_id: str
    start_offset: int                       # character offset within passage.text
    end_offset: int
    verified: bool                          # populated by §3.4
    verification_rationale: str
```

_Source: `Change` [`src/config.py` L73-L85](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L73-L85), `Citation` [`src/config.py` L64-L70](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L64-L70)._

**Decisions.**
- **`tenant_id` + `environment` are storage keys, not semantic
  ownership.** Same reasoning as `ProceedingVersion` (§2.1): a `Change`
  is a property of the regulator's two versions, independent of any one
  tenant. Keyed per `(tenant_id, environment)` in v1 for storage
  uniformity; the cross-tenant change store is §8.9 (first next-move).
- **`change_type` is a closed Literal of five values.** Flagged in
  open-question #5 — expect iteration with Rachel's feedback.
- **`confidence` is an integer 0–5, not a float.** Self-reported float
  confidence from LLMs is uncalibrated and uninterpretable; a 0–5 ordinal
  with a rubric in the prompt (criteria per level) is easier to reason
  about, calibrate against gold, and ultimately migrate to a Jev
  **Score** output.
- **`Citation` is a separate model, not inlined.** Appears on `Change`,
  `ExtractedObligation`, and `Mapping` — one shared model.
- **`verified` + `verification_rationale` on `Citation`.** Keeps a
  `Change` self-contained for the UI; verification state travels with
  the citation it describes.

### 2.4 Obligation / Project / Document — the company context

```python
class Obligation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    obligation_id: str                      # e.g. 'VG-OBL-DER-REG-01'
    tenant_id: str
    environment: str
    obligation_type: Literal['reporting', 'operational', 'procedural', 'financial']
    summary: str
    full_text: str
    source_authority: str                   # 'FERC Order 2222 §35.28(g)(1)(ix)' or 'MISO Tariff §38.6'
    status: Literal['active', 'proposed', 'retired']
    owner: Literal['market_ops', 'legal', 'it_metering']


class Project(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str
    tenant_id: str
    environment: str
    name: str
    description: str
    status: Literal['planned', 'in_progress', 'blocked', 'complete']
    related_obligations: tuple[str, ...]
    owner: Literal['market_ops', 'legal', 'it_metering']


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str
    tenant_id: str
    environment: str
    document_type: Literal['policy', 'procedure', 'tariff_filing', 'contract', 'template']
    title: str
    summary: str
    full_text: str
    last_reviewed_at: datetime
    owner: Literal['market_ops', 'legal', 'it_metering']
```

_Source: `Obligation` [`src/config.py` L103-L113](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L103-L113), `Project` [`src/config.py` L116-L125](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L116-L125), `Document` [`src/config.py` L128-L138](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L128-L138). Seed data: [`data/tenants/verdant_grid/company_context.json`](https://github.com/ngs2410/strata-build-challenge/blob/main/data/tenants/verdant_grid/company_context.json)._

**Decisions.**
- **Three peer models, not one `CompanyThing`.** Different schemas,
  different owners, different downstream treatment in Mapping. Collapsing
  them loses semantic signal and makes the review UI ambiguous ("what
  kind of thing is this?").
- **`tenant_id` + `environment` on every model** (CLAUDE.md tenant
  plumbing rule).
- **`owner` is a `Literal` of three strings** matching Verdant's internal
  destinations. Prod makes this a free-text per-tenant string with a
  config-side mapping to routing destinations.

### 2.5 Mapping

```python
class Mapping(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mapping_id: str
    tenant_id: str
    environment: str
    change_id: str                          # the Change this mapping is for
    affected_items: tuple[AffectedItem, ...]
    confidence: int                         # 0-5 overall mapping confidence
    confidence_rationale: str
    rationale: str                          # free-text explanation


class AffectedItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_type: Literal['obligation', 'project', 'document']
    item_id: str
    match_confidence: int                   # 0-5 per-item
    match_rationale: str
    proposed_action: Literal['review', 'amend', 'no_op']
    proposed_route: Literal['market_ops', 'legal', 'it_metering']
```

_Source: persisted `Mapping` [`src/config.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py), persisted `AffectedItem` [`src/config.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py). LLM-side intermediate `AffectedItemByIndex` + `MappingResponse` live in [`src/mapping.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py) — see §3.6 for the index→id translation._

**Decisions.**
- **Per-item (action, route) replace mapping-level summary.** Each
  `AffectedItem` carries `proposed_action` and `proposed_route`. One
  Change touching N diverse assets produces N (action, route) pairs
  — different items in the same Mapping can route to different teams
  (TDD §8.6 closed in v1).
- **`proposed_route` as `Literal`, not free text.** v1 ships three
  destinations; prod makes this configurable per-tenant.
- **`proposed_action` is a `Literal` of three values.** Alternatives:
  open-ended free text (unverifiable; forces Rachel to interpret every
  action), per-action-type structured outputs (over-engineered). Three
  categorical values is the right granularity for the per-pair
  review UI.
- **Per-pair curation lives in `AssetDecision` (§2.6).** The Mapping
  is the AI's proposal set; the AssetDecision records Rachel's
  per-pair decision + chosen (action, route).

### 2.6 ReviewItem

```python
class ReviewItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review_item_id: str
    tenant_id: str
    environment: str
    mapping_id: str
    priority: int                           # computed: higher confidence × higher impact first
    status: Literal['pending', 'approved', 'overridden', 'escalated']
    created_at: datetime
```

A `ReviewItem` is the queue-level wrapper for one `Mapping`. `status`
is a write-time aggregate (`_fold_status` in
[`src/review_queue.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/review_queue.py))
over the per-pair `AssetDecision` set — pending > escalated >
overridden > approved. Pure fold-over-the-audit-log is tracked as the
TDD §8.8 open question.

_Source: [`src/config.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py)._

### 2.6b AssetDecision

```python
class AssetDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision_id: str                        # f'{review_item_id}:item-{index:03d}'
    tenant_id: str
    environment: str
    review_item_id: str
    mapping_id: str
    affected_item_index: int                # stable key into mapping.affected_items
    item_type: Literal['obligation', 'project', 'document']   # denormalised
    item_id: str                                              # denormalised
    decision: Literal['approved', 'overridden', 'dismissed', 'escalated']
    action: Literal['review', 'amend', 'no_op']               # Rachel's final
    route: Literal['market_ops', 'legal', 'it_metering']      # Rachel's final
    rationale: str
    decided_at: datetime
    actor: str
```

Rachel's per-pair decision for a specific (change × asset) pair.
Absence of an `AssetDecision` for a `(review_item_id,
affected_item_index)` pair is pending — pending is NOT stored.
`action` and `route` are copied from the `AffectedItem`'s
`proposed_action` / `proposed_route` on `approved`; may differ on
`overridden`.

_Source: [`src/config.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py). `apply_asset_decisions` in [`src/review_queue.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/review_queue.py) is the single legitimate writer._

### 2.7 AuditEvent

```python
class AuditEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str                           # monotonic within (tenant_id, environment)
    tenant_id: str
    environment: str
    occurred_at: datetime
    actor: str                              # 'system' | user identifier
    kind: Literal[
        'proceeding_ingested',
        'change_detected',
        'mapping_proposed',
        'review_item_created',
        'review_item_approved',
        'review_item_overridden',
        'review_item_escalated',
        'asset_decision_approved',
        'asset_decision_overridden',
        'asset_decision_dismissed',
        'asset_decision_escalated',
        'rollback_executed',
    ]
    target_type: Literal[
        'proceeding_version', 'change', 'mapping', 'review_item', 'asset_decision'
    ]
    target_id: str
    prior_value_json: str | None            # the full prior Pydantic model as JSON
    new_value_json: str | None
    rationale: str | None
    batch_id: str | None                    # shared across N bulk-decision events
```

_Source: [`src/config.py` L208-L240](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L208-L240). Append-only JSONL log + `log_event()` helper in [`src/audit_log.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/audit_log.py#L50)._

**Decisions.**
- **`prior_value_json` and `new_value_json` are serialized JSON strings
  on the AuditEvent, not nested models.** The audit log is append-only
  and immutable; snapshotting the full prior/new model as a string
  preserves history even if the current model schema changes later.
  CLAUDE.md's no-JSONB rule is about *shape of the data model* (storing
  structured data as a blob when it should be columns) — here the
  snapshot *is* the atom and can never be edited.
- **`event_id` is monotonic within `(tenant_id, environment)`.** v1 uses
  `f'{tenant_id}:{environment}:{counter}'`; prod uses a database
  sequence.
- **`kind` is a closed Literal.** Extending it is a schema change by
  design — audit event kinds should be stable.

**Scaling note.** v1: `JsonStore[T]` serialises/deserialises JSON files
per entity type (§4.4). Prod: Postgres tables with the Pydantic models
round-tripping through SQL columns; `audit_events` is append-only with
materialised views for folded state.

---

## 3. The Core Pipeline

**End-to-end flow (ASCII).** `run_pipeline(tenant_id, environment, dry_run)` in
`src/pipeline.py` orchestrates the six stages below; each emits a structlog
`<stage>.start`/`.done` pair and (where applicable) appends an `AuditEvent`.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                     Strata pipeline — 6 stages                             │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  (1) INGEST         ─────> proceedings.json, obligations.json,             │
│      ingest_tenant_fixture  projects.json, documents.json                  │
│                                                                            │
│  (2) DIFF ─ LLM     ─────> changes.json                                    │
│      version_diff          (LlmChange → citation_grounding._resolve →      │
│                             persisted Change with real Citation offsets)   │
│                                                                            │
│  (3) VERIFY ─ LLM   ─────> changes.json (Citation.verified ← true/false)   │
│      citation_verify       (skipped if config.citation.verification_       │
│                             required == false; apply_verification short-   │
│                             circuits on grounding failure to prevent       │
│                             laundering)                                    │
│                                                                            │
│  (4) EXTRACT ─ LLM×2 ────> extracted_obligations.json                      │
│      obligation_           (one call per version; grounded via             │
│      extraction            ground_obligations; failed grounding escalates) │
│                                                                            │
│  (5) MAP ─ LLM×N    ─────> mappings.json                                   │
│      mapping               (one call per Change; emits per-item            │
│                             AffectedItem with proposed_action +            │
│                             proposed_route — §8.6 closed in v1)            │
│                                                                            │
│  (6) ENQUEUE        ─────> review_items.json                               │
│      enqueue_mapping       (ReviewItem.status = pending | escalated per    │
│                             EscalationConfig thresholds — citation         │
│                             threshold is used when any citation is         │
│                             unverified, mapping threshold otherwise)       │
│                                                                            │
├────────────────────────────────────────────────────────────────────────────┤
│  Every artefact-producing stage: appends to audit_log.jsonl via log_event  │
│  (append-only; fold-over-log is the Strata invariant).                     │
│  LLM-calling stages (2,3,4,5): dispatch via ModelCaller → diskcache →      │
│  OpenAI / Stub (see §4.1 diagram).                                         │
│  v1 cold-cache cost: 7 LLM calls (1 diff + 1 verify + 2 extract +          │
│  3 map) ≈ $0.005, ~15s wall-clock.                                         │
│                                                                            │
│  Rachel's loop — NOT a pipeline stage, triggered from the web UI:          │
│  POST /queue/{id}/decide → apply_asset_decisions → N AssetDecisions +      │
│  N AuditEvents sharing a batch_id (§3.7 Review & Audit).                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Ingest

**Role.** Load the two proceeding-version text files and the
company-context JSON for a tenant. Normalise into Pydantic models
(`ProceedingVersion`, `Obligation`, `Project`, `Document`). Persist via
`Store[T]` facades.

_Source: [`src/ingest.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/ingest.py) (`ingest_tenant_fixture` [L137](https://github.com/ngs2410/strata-build-challenge/blob/main/src/ingest.py#L137), `ingest_proceeding_version` [L86](https://github.com/ngs2410/strata-build-challenge/blob/main/src/ingest.py#L86), `ingest_company_context` [L112](https://github.com/ngs2410/strata-build-challenge/blob/main/src/ingest.py#L112)). Per-tenant manifest: [`data/tenants/verdant_grid/proceedings/manifest.json`](https://github.com/ngs2410/strata-build-challenge/blob/main/data/tenants/verdant_grid/proceedings/manifest.json)._

**v1.**
- Input: a per-tenant manifest at
  `data/tenants/<tenant>/proceedings/manifest.json` listing each
  proceeding version's metadata (`proceeding_id`, `version_id`,
  `status`, `docket_number`, `issued_at`, `source_url`,
  `text_file`), plus the referenced text files alongside the
  manifest, plus `data/tenants/<tenant>/company_context.json`.
- `src/ingest.py::ingest_tenant_fixture` iterates the manifest and
  dispatches each entry to `ingest_proceeding_version`, which
  splits text into passages via a lightweight structural parser
  (section-path extraction via regex on section headers like
  `§35.28(g)(1)(ix)`) and constructs `ProceedingVersion` + `Passage`
  models.
- Driver: `scripts/main_detect_changes.py --tenant_id=verdant_grid
  --environment=prod` — ingest is embedded inline, not a separate
  script (see §6.1 Decisions).

**Production.**
- Scheduled polling of FERC's eLibrary RSS feed; download new filings;
  PDF extraction via `pdfplumber` or `unstructured`; HTML extraction via
  `beautifulsoup4`. The section-path parser becomes a per-regulator
  plugin (FERC's section conventions differ from state PUCs').
- Incremental ingest — new versions arriving don't re-ingest prior
  versions.

**Decisions.**
- **Text excerpts, not PDFs, for v1.** PDF extraction is a
  solved-but-noisy problem; investing in it for v1 means investing in a
  non-depth-area. Reviewer-defensible: "we chose to make the structural
  parser the depth, not the extraction preprocessor."
- **Passage splitting via regex, not an LLM.** Section-path structure in
  FERC documents is deterministic (`§35.28(g)(1)(ix)`); an LLM here
  would be slow, expensive, non-deterministic, and no more accurate than
  a regex. The LLM shows up where interpretation is genuinely needed
  (§3.2+), not where parsing suffices.
- **Status is user-asserted at ingest.** As called out in §2.1 — making
  this a classifier would be a bug.

### 3.2 Version Diff

**Role.** Given two `ProceedingVersion`s (draft NOPR + final Order),
detect material changes between them. Each change gets a type (added /
removed / tightened / loosened / cosmetic) and a description.

_Source: [`src/version_diff.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/version_diff.py) — `LlmChange` [L107-L115](https://github.com/ngs2410/strata-build-challenge/blob/main/src/version_diff.py#L107-L115), `VersionDiffResponse` [L117-L119](https://github.com/ngs2410/strata-build-challenge/blob/main/src/version_diff.py#L117-L119), `detect_changes` [L391](https://github.com/ngs2410/strata-build-challenge/blob/main/src/version_diff.py#L391)._

**v1.**
- `src/version_diff.py` runs **one LLM task per subsection** with both
  versions' text in context. Returns a list of `Change` objects with
  `change_type`, `description`, `prior_citation`, `new_citation`,
  `confidence`, `confidence_rationale`.
- Task name: `version_diff`. Prompt structure follows
  PROMPT_CONVENTIONS.md (task / steps / details / rubrics / pitfalls /
  field_intents / json_example). The rubric for `confidence` is in the
  prompt — criteria per 0–5 level.
- Pydantic response model: `VersionDiffResponse` with
  `changes: tuple[LlmChange, ...]`. `LlmChange` mirrors the
  persisted `Change` (§2.3) but holds `LlmCitation` subobjects
  (`passage_id` + `citing_span`) instead of `Citation`. Grounding
  (§3.3) converts to the persisted `Change` with real `Citation`
  (offsets computed by substring match).
- The LLM-side / persisted-side split exists for every
  claim-emitting stage; see §3.3 for the rationale (LLMs can't
  count characters, so they emit the verbatim `citing_span` and
  deterministic Python computes offsets).
- Cardinality in v1: 1 LLM call per end-to-end run. Sequential loop (per
  CLAUDE.md's sequential-at-v1-scale rule for <20 items).

**Production.**
- Full-document diffs can't fit in a single LLM context. Switch to a
  hierarchical pipeline:
  1. **Textual pre-filter.** `difflib.SequenceMatcher` identifies
     candidate changed regions at the passage level — cheap,
     deterministic.
  2. **LLM focus on changed regions.** Per candidate region, run the
     `version_diff` task with just that region's prior/new text.
  3. **Jev Choice for change-type classification.** Jev **Choice** over
     the five change types replaces the LLM's self-reported
     `change_type` with a calibrated probability distribution across all
     five. One Jev request per Change, parallelisable.
  4. **Jev Score for severity.** Jev **Score** on an ordinal rubric
     (trivial / minor / substantive / critical) adds a per-change
     severity dimension that affects routing priority.

**Decisions.**
- **LLM-only diff for v1 vs. pure textual diff.** Pure textual diff
  (difflib) misses semantic equivalence — a sentence rewritten to say
  the same thing shows as a change. LLM-only correctly classifies this
  as `cosmetic`. At 2000-token scale, LLM-only is simpler and more
  accurate. At full-document scale (660 pages), LLM-only can't fit the
  context — hybrid wins there.
- **One LLM call per subsection vs. per passage.** v1 cardinality is 1
  subsection; the question is academic. At prod scale, hybrid above.
- **Why not Jev for the whole diff task in v1?** Jev doesn't generate —
  it discriminates. The diff task requires generating a *list* of
  `Change`s with free-text `description` and `confidence_rationale`
  fields. That's LLM territory. Where Jev fits (change-type
  classification, severity scoring) is *after* generation, in
  production.
- **Single first-draft + first-final pair diffed per run.** The
  pipeline picks the first `status='draft'` and first `status='final'`
  version from the manifest (`src/pipeline.py:119`); additional
  versions in the manifest (a NOPR revision, an Order on Rehearing)
  are ingested and persisted but not diffed. The manifest format and
  `ProceedingVersion.status` taxonomy already accommodate an
  unbounded version sequence; iterating adjacent-pair diffs over the
  sorted sequence is a post-wedge add.

### 3.3 Citation Grounding (depth area)

**Role.** Every detected `Change` (and later every extracted
`Obligation`) carries a structural citation —
`(passage_id, start_offset, end_offset)` — pointing to the exact text in
the source `Passage` that supports the claim. This is the core trust
property of the product.

_Source: [`src/citation/citation_grounding.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_grounding.py) (`ground_citations_for_changes` [L80](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_grounding.py#L80), `ground_obligations` [L106](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_grounding.py#L106), `_resolve` [L135](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_grounding.py#L135)). LLM-side shape: [`src/citation/llm_citation.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/llm_citation.py)._

**v1.**
- The LLM task that produces changes (§3.2) returns an `LlmChange`
  per detected change. Each `LlmChange.prior_citation` /
  `LlmChange.new_citation` is an `LlmCitation` with two fields:
  `passage_id: str` (the full structural ID of the cited passage)
  and `citing_span: str` (the verbatim text copied character-for-
  character from the cited passage).
- Post-generation, `src/citation/citation_grounding.py` runs a
  deterministic substring match: `start_offset =
  passage.text.find(citing_span)`, `end_offset = start_offset +
  len(citing_span)`. The resulting `Citation` has real offsets and
  starts `verified=False` pending the citation_verify stage.
- Three auditable failure paths, all `verified=False` with a
  specific rationale:
  - Unknown passage_id → raises `ValueError` (hard fail — the LLM
    cited a passage the pipeline doesn't know about).
  - `len(citing_span) < citation.min_citation_span_chars` → rationale
    "`span width <N> below citation.min_citation_span_chars=<M>`".
    Routes to escalation via citation_threshold at the review-queue
    gate (§3.7).
  - Substring not found in cited passage → rationale "`citing span
    not found in cited passage`". Routes to escalation the same way.

**Production.**
- Full-text indexing with structural anchors stable across versions.
  When a passage is edited between versions, previously-stored citations
  re-anchor via a dedicated passage-alignment process (open-question
  #2).
- Replace LLM-emits-index + substring-match with a dedicated anchoring
  step: fuzzy matching (Levenshtein) for mild edits; a Jev **Noul** gate
  ("is this candidate anchor the correct re-anchor for the prior
  citation?") for ambiguous cases.

**Decisions.**
- **Structural passage IDs vs. free-text citations.** Free-text ("see
  §4.2") is unverifiable without reparsing the citation string.
  Structural IDs collapse the verifier's job to a dict lookup. This is
  the key design win and the reason citation precision ≥ 98% is a
  defensible trust target.
- **Offsets, not just passage IDs.** A passage is often 50–200 words;
  offsets let the verifier and the UI highlight the specific supporting
  span.
- **LLM emits `citing_span: str`, deterministic code computes offsets.**
  LLMs cannot count characters reliably — asking the model to emit
  `start_offset: int` / `end_offset: int` produces plausible but
  wrong numbers (drift by a few characters, truncate mid-word, or
  fabricate values that look right). Instead we have the LLM copy
  the verbatim text span, and `passage.text.find(citing_span)` in
  Python computes the offsets. If the span isn't literally present
  in the passage (paraphrase, summary, hallucination) the match
  returns -1 and the citation is marked unverified with the
  "citing span not found" rationale — a loud auditable failure
  rather than a silent wrong offset. This mirrors the integer-index
  pattern used for list picks in §3.6 Mapping; both are instances
  of PROMPT_CONVENTIONS.md's "never ask the LLM to count or index"
  rule.
- **Why not hash-anchoring (storing a hash of the passage text as the
  anchor)?** Hashes change under any edit, including whitespace or
  renumbering. Structural anchors survive.

### 3.4 Citation Verification (depth area)

**Role.** For every claim with a citation, independently verify that the
cited passage actually supports the claim. Failures surface in
`Citation.verified = False` with a rationale; downstream code treats
unverified claims as low-confidence and routes them to escalation.

_Source: [`src/citation/citation_verify.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_verify.py) — `CitationVerificationResponse` [L93-L100](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_verify.py#L93-L100), `verify_citations` [L277](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_verify.py#L277)._

**v1.**
- `src/citation/citation_verify.py` runs a dedicated LLM task per claim.
  Prompt: shows the claim text, the cited passage text, and the citing
  span. Response model: `CitationVerificationResponse` with
  `supports: bool`, `confidence: int`, `rationale: str`.
- Runs on 100% of claims after extraction. Cardinality: ~5 Changes + ~8
  Obligations × 2 versions ≈ 21 verification calls per end-to-end run.
  Sequential, cached.

**Production.**
- **Jev Noul replaces the LLM verifier.** This is the strongest Jev fit
  in Strata — citation-support verification is literally one of Jev's
  named example use cases (JEV.md §2.3). Jev **Noul**: *"Does this
  passage support this claim?"* with the passage + claim in `state`,
  returning a calibrated probability.
- Jev's **70–500ms** latency and **$0.042 per million input tokens**
  vs. OpenAI's much higher cost makes verification at full scale
  economically viable where an LLM verifier would be prohibitive.
- Fall-back to LLM for very-low-probability or adversarial-looking
  inputs (per Jev's documented weakness: *"vulnerability to
  adversarially crafted input"*).

**Decisions.**
- **Verification as a separate step, not an inline check.** Separation
  of concerns: extraction owns generation; verification owns judgment.
  Verification is also independently measurable — citation precision in
  evals is specifically "fraction of verified citations whose claim is
  actually supported" (§5.2).
- **LLM-verifier in v1 vs. keyword overlap vs. embedding similarity.**
  Keyword overlap misses paraphrase; embedding similarity over-generates
  (anything topically related looks "supporting"). LLM-as-NLI is correct
  but expensive — exactly what Jev **Noul** is architected to replace at
  scale.
- **Verification fails → escalation, not silent reject.** A failed
  verification is persisted with `verified=False`; the containing
  `Change` or `ExtractedObligation` is auto-routed to Rachel. Per
  CLAUDE.md invariant: never silently drop or coerce.

### 3.5 Obligation Extraction

**Role.** For each `Passage` in each `ProceedingVersion`, extract any
new regulatory obligations the passage establishes. Each
`ExtractedObligation` carries a structural citation (per §3.3) and a
confidence score (per the shared rubric).

_Source: [`src/obligation_extraction.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/obligation_extraction.py) — `LlmExtractedObligation` [L67-L75](https://github.com/ngs2410/strata-build-challenge/blob/main/src/obligation_extraction.py#L67-L75), `ObligationExtractionResponse` [L77-L84](https://github.com/ngs2410/strata-build-challenge/blob/main/src/obligation_extraction.py#L77-L84), `extract_obligations` [L305](https://github.com/ngs2410/strata-build-challenge/blob/main/src/obligation_extraction.py#L305). Grounding: `ground_obligations` in [`src/citation/citation_grounding.py` L106](https://github.com/ngs2410/strata-build-challenge/blob/main/src/citation/citation_grounding.py#L106)._

**v1.**
- `src/obligation_extraction.py` runs an LLM task per subsection. Prompt
  includes `OBLIGATION_TAXONOMY_REFERENCE` — the four `obligation_type`
  values with descriptions and worked examples, built programmatically
  from a data source (per PROMPT_CONVENTIONS.md §10).
- Response model: `ObligationExtractionResponse` with
  `extracted_obligations: tuple[LlmExtractedObligation, ...]`.
  `LlmExtractedObligation` mirrors the persisted `ExtractedObligation`
  (§2.4) but carries `LlmCitation` (`passage_id` + `citing_span`)
  instead of `Citation`. `ground_obligations` (§3.3) converts to the
  persisted shape with real `Citation`.
- Cardinality: 1 LLM call per version × 2 versions = 2 LLM calls per
  end-to-end run. Sequential, cached.

**Production.**
- **Two-stage extraction.** A cheap classifier (either Jev **Noul** per
  passage — "does this passage establish a new regulatory obligation?" —
  or a fine-tuned small classifier once the gold set is large enough)
  acts as a pre-filter; the LLM extractor runs only on passages flagged
  as obligation-bearing.
- **Verification after extraction.** Jev **Noul** on each extracted
  obligation ("is this a well-formed obligation of the stated type? is
  it well-cited?") catches extraction errors without a human in the
  loop.

**Decisions.**
- **Structured extraction with Pydantic Literals** over free-text +
  post-parse. Schema is enforced by OpenAI structured outputs and by
  Pydantic; no parsing step; no "the model said 'reporting' sometimes
  and 'Reporting' other times" normalisation code.
- **Why not Jev for extraction itself?** Jev doesn't generate open-ended
  values. Extraction creates new structured objects from unstructured
  text — pure generation, Jev-incompatible. Jev shows up in the
  pre-filter (binary gate) and post-filter (verify extracted structure),
  not in the extraction itself.
- **Self-reported confidence with rubric and rationale.** v1's
  confidence design: `confidence: int (0-5)` + `confidence_rationale:
  str`, rubric embedded in the prompt. The eval harness produces a
  reliability diagram (confidence bucket → actual precision) that
  informs threshold tuning (§5.2).

### 3.6 Mapping

**Role.** Given a verified `Change` (with citation), determine which
Verdant obligations, projects, and documents it affects, recommend an
action per affected item, and route the overall item to its primary
internal destination (one of three in v1 — see [§8.6](#86-multi-reviewer-routing)
for the multi-reviewer open question; mirrored at
[PRD §7 bullet 6](PRD.md#7-open-questions-and-unvalidated-assumptions)).

_Source: [`src/mapping.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py) — `AffectedItemByIndex` [L88-L95](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py#L88-L95), `MappingResponse` [L98-L108](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py#L98-L108), `build_mapping_prompt` [L130](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py#L130), `map_change` [L432](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py#L432), `_resolve_affected_item` (index→id translation) [L476](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py#L476)._

**v1.**
- `src/mapping.py` runs **one LLM call** that sees:
  - The `Change` (with citation).
  - The Verdant company context — obligations, projects, documents
    rendered as enumerated lists per PROMPT_CONVENTIONS.md §5. Each
    list is capped at `config.mapping.candidate_set_size` (default
    50) before rendering; at v1 cardinality (16 items total) this
    is a no-op, but the cap is active and tunable per tenant for
    the TDD §3.6 scaling-path (a) handoff.
- LLM response: `MappingResponse` with `affected_items:
  tuple[AffectedItemByIndex, ...]` (`item_type` + `item_index: int` +
  `match_confidence` + `match_rationale` + per-item `proposed_action` +
  per-item `proposed_route`), overall `confidence`,
  `confidence_rationale`, and `rationale`. No mapping-level
  recommended_action / routing_destination — TDD §8.6 closed in v1.
- `src/mapping.py::map_change` then resolves each `item_index` into
  the corresponding `item_id` and returns the persisted `Mapping`
  with `affected_items: tuple[AffectedItem, ...]` (`item_id: str`).
- Cardinality: 1 LLM call per `Change` (3 Changes per end-to-end
  run — matches the current gold case). Sequential.

**Production — three distinct scaling paths**, each addressing a
different scaling pressure. Not mutually exclusive; they combine.

- **(a) Candidate pre-filter + reasoning LLM.** Embedding-based
  retrieval reduces 1000+ company-context items to top-20 per Change.
  The LLM mapping call then sees only the filtered set. Keeps the
  single-call shape; handles context-size growth. Works when the volume
  of changes is manageable but the context is big.
- **(b) Decomposed per-sub-task with Jev.** Replace one LLM call with
  multiple Jev evaluations over the same `state` (the Change text +
  citation):
  - **Jev Noul per candidate obligation**: *"does this change affect
    this obligation?"* Run in parallel across all candidates — Jev's
    architecture explicitly favours this pattern (JEV.md §5).
  - **Jev Noul per candidate project / document**, same shape.
  - **Jev Choice** per affected item for `proposed_action`.
  - **Jev Choice** per affected item for `proposed_route`.
  - Deterministic code aggregates Noul probabilities above threshold
    into `affected_items`; applies tenant-config policy for action and
    destination given the Choice distributions.
  - **This is where Jev's parallel-questions-per-state architecture
    compounds.** One Jev request covers tens of yes/no questions over
    the same shared state; latency is comparable to a single question,
    cost is proportional to input tokens. Equivalent LLM-call fan-out is
    50× latency.
- **(c) Fine-tuned small classifier for the frequent decisions.** Once
  Rachel's accept/override data accumulates (open-question #4), the
  `(change × obligation)` affinity is a supervised classification
  problem. A fine-tuned MiniLM-sized model handles routine mappings;
  Jev or LLM handles the long tail.

**Decisions.**
- **One LLM call sees the full-or-capped context in v1.** 16-item
  context fits comfortably in a single prompt and the
  `candidate_set_size` cap is a no-op at v1 scale; multiple LLM
  calls would introduce reconciliation cost and inter-call
  inconsistency. The cap is wired from day one so the eventual
  switch to scaling-path (a) is a config change, not a refactor.
  Multiple-call decomposition at scale is paths (b) / (c) above.
- **Index-based selection** (PROMPT_CONVENTIONS.md §5). LLM returns
  0-based integer indexes into the enumerated context; caller translates
  to IDs. Prevents ID hallucination.
- **Jev decomposition in (b) over per-sub-task LLM calls.** The problem
  decomposes naturally into *many parallel yes/no questions over shared
  state* — exactly what Jev is architected for. The LLM equivalent is
  many sequential or weakly-batched calls with worse cost and worse
  calibration.
- **Per-item `proposed_route` as `Literal`.** v1 ships three
  destinations matching Verdant's teams; pluggable per-tenant via
  `DomainConfig` in prod.
- **Per-item routing closed in v1 — TDD §8.6.** Each `AffectedItem`
  carries its own `proposed_route` + `proposed_action`. One Change
  touching N diverse assets produces N proposals that can route to N
  different teams. The deferred mapping-level single-destination
  shape is gone from the schema.

### 3.7 Review & Audit

**Role.** Rachel approves, overrides, dismisses, or escalates each
(change × asset) pair under a `ReviewItem` in the queue. Decisions
are per-pair (`AssetDecision` — TDD §2.6b) and recorded via
`apply_asset_decisions` in a single call that covers both single-row
and bulk UX (multi-select checkboxes share a `batch_id` across the
emitted audit events). `ReviewItem.status` is a write-time aggregate
over the AssetDecision set (`_fold_status`: pending > escalated >
overridden > approved). Pure fold-over-the-audit-log is tracked as
the TDD §8.8 open question.

_Source: [`src/review_queue.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/review_queue.py) (`enqueue_mapping`, `apply_asset_decisions`, `_fold_status`); audit log in [`src/audit_log.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/audit_log.py) (`log_event` with the new `batch_id` kwarg + monotonic counter in `event_id` to defeat microsecond collisions in bulk-apply loops)._

**v1.**
- `src/review_queue.py` constructs `ReviewItem`s from `Mapping`s.
  Priority is `mapping.confidence * (1 + pending_count)`. At enqueue
  pending_count == len(affected_items); each AssetDecision landed
  recomputes priority — items with the most undecided pairs float
  to the top.
- **Escalation gate**: on construction, if `mapping.confidence <
  threshold`, the `ReviewItem` is created with `status='escalated'`
  immediately — bypasses the auto-approval path per CLAUDE.md invariant.
  `threshold` is a two-tier function of the underlying `Change`'s
  citation state (0–5 integer scale):
  - If any `Change.prior_citation` / `Change.new_citation` is
    `verified=False`, use `DomainConfig.escalation.citation_threshold`
    (default 3). The semantic: a Mapping riding on an unverified
    citation needs a stricter confidence floor to clear
    auto-approval — the citation might be hallucinated and the
    reviewer should get the item regardless of how confident the
    mapper is.
  - Otherwise use `DomainConfig.escalation.mapping_threshold`
    (default 3). The common path.
  Both knobs are per-tenant tunable via `DomainConfig.escalation`.
- `src/audit_log.py` owns the append-only JSONL log at
  `data/tenants/<tenant>/<environment>/audit_log.jsonl`. Every state
  transition appends an `AuditEvent`.
- Rachel's actions in the web view (`approve` / `override` / `escalate`)
  emit new `AuditEvent`s **and** update the `ReviewItem.status` column
  pragmatically — see Decisions below.

**Production.**
- Postgres `audit_events` table (append-only, indexed by
  `(tenant_id, environment, occurred_at)`).
- Materialised views for folded state: `current_review_items`,
  `current_project_states` — refreshed on write via triggers or an
  event-sourcing projection worker.
- Rollback becomes a transactional operation (see §8.8 open question
  for the v1 → prod path).

**Decisions.**
- **Mutable `ReviewItem.status` column *and* append-only audit log,
  for v1.** The challenge requires "a living, auditable project state"
  — satisfied by the audit log capturing every transition. The pure
  fold-over-log architecture (current status derived at read time
  from the log) is cleaner but adds machinery (fold helpers,
  cache-vs-source-of-truth design) that is not required by the brief.
  v1 ships the pragmatic shape: the audit log is the source of truth
  for *history*; the mutable `status` column is the source of truth
  for *right now*. The pair is reconciled on write (every status
  mutation emits a matching `AuditEvent`). The pure fold architecture
  is tracked as open question §8.8.
- **Rollback deferred to future work.** CHALLENGE.md requires an
  auditable state, not rollback. Rollback (`replay_until(timestamp)`)
  is one of six optional depth areas we did not choose — our depth
  areas are citation grounding (§3.3 + §3.4) and evals (§5). Rollback
  design is retained in §8.8 for future work.
- **JSONL on disk in v1.** For ~hundreds of events per end-to-end run,
  loading in memory is instantaneous. At tens of thousands of events
  per tenant, read-time scanning stays cheap in Python; at millions,
  Postgres with materialised views is correct.

---

## 4. Cross-Cutting Infrastructure

### 4.0 Technology stack choices

All v1 technology choices optimise for the same priority stack:
**pragmatism** (shortest path to a working wedge) → **velocity**
(standard patterns the AI harness already knows, so iteration cycles
stay tight) → **simplicity** (fewer moving parts, fewer production-
shape surprises) → **boringness** (the tool a staff engineer would
default to, not the clever one) → **familiarity + integration
readiness** (ecosystems with first-class Pydantic / OpenAI / FastAPI
compatibility, so adapters are not bespoke). Nothing here is
contrarian. The list below names each v1 choice with the one-line
rationale under this rubric; alternative-and-why-not notes are
inline on the choices where the panel is likely to ask.

- **Language — Python 3.12.** Default stack for ML/LLM pipelines;
  `Literal` + `match` + Pydantic v2 keep schema code dense.
  Alternatives (Go, Rust, Node) force custom Pydantic-equivalents
  and lose LLM-ecosystem integration readiness.

- **Web framework — FastAPI + Jinja2.** FastAPI is the de-facto
  Python async web standard with first-class Pydantic integration
  (one model type across LLM dispatch, store, web form, template
  context). Jinja2 is the boring/conventional Python template
  choice; no JS framework at v1 (one `<script>` block in the
  queue template for the select-all checkbox — CLAUDE.md v1 rule).
  Alternatives (Flask + Marshmallow, Django + DRF) duplicate the
  Pydantic layer we already have.

- **Schema layer — Pydantic v2 with `extra='forbid'` on every
  model.** One schema definition serves JSON load/save, LLM
  structured output (via `client.responses.parse(text_format=...)`),
  FastAPI request/response, and the eventual SQL migration (when
  Postgres lands via a new `Store[T]` concrete). `extra='forbid'`
  is the invariant that lets us rebuild-the-world on each run
  (CLAUDE.md). Alternatives (dataclasses, attrs, msgspec) lose
  the LLM-structured-output + FastAPI integration.

- **JSON codec — orjson.** 2–5× faster than stdlib `json`,
  strict UTF-8, no silent NaN serialisation. Boring Python
  ecosystem default for data-pipeline workloads.

- **LLM cache — diskcache.** Pure-Python, SQLite-backed, no
  daemon, no infra. Fine at v1 scale (~7 calls per end-to-end
  run × a few runs per day). Alternatives (Redis, memcached)
  require a running daemon and bring zero marginal value until
  N tenants share a cache ([§8.9](#89-cross-tenant-amortisation--multi-tier-mapping-cull)).
  `.model_cache/` is gitignored.

- **Structured logs — structlog → JSON to stderr.** Pydantic-
  serialisable context, JSON output `grep`-able with `jq`, no
  sinks or collectors at v1. OTel Collector is the production
  shape ([§4.6](#46-telemetry)); structlog's JSON lines are the
  OTel-attribute format already, so the migration is a sink swap,
  not a code rewrite.

- **Server runtime — uvicorn.** FastAPI's reference ASGI server;
  `--reload` for dev-time template editing. Hypercorn / Daphne
  offer the same ASGI surface with zero marginal benefit at v1.

- **Tests — pytest.** Fixtures + `parametrize` + autouse are
  first-class; the conftest `stub_all_task_configs` fixture
  wires every LLM task to `StubBackend` so the whole suite is
  network-free. `unittest` lacks fixture + parametrize
  ergonomics; pytest is the Python ecosystem default.

- **Lint / format — ruff.** Single tool for lint + format +
  import-sort; ~100× faster than black + flake8 + isort combined.
  Pre-ruff tooling was three overlapping tools with cross-config
  friction.

- **Secrets — python-dotenv.** Standard Python pattern for
  `.env` → `os.environ` bootstrap. The load site is
  `src/telemetry.py::configure_logging` (one of the first things
  scripts call via `main_detect_changes.py`).

- **CLI flags — absl-py flags.** Centralised flag registration;
  cross-script flag reuse (shared defaults for `--tenant_id`,
  `--environment`); strict type coercion. Click / typer were
  considered; absl's `FLAGS` namespace is more honest about the
  global-state cost. The CLAUDE.md compliance rule prohibits
  tests importing from `scripts/main_*.py`, so the
  flag-pollution risk is already contained.

- **LLM provider — OpenAI responses API with structured
  outputs.** Only mainstream provider with first-class Pydantic
  structured output at the time of writing;
  `client.responses.parse(model=..., text_format=ResponseModel)`
  eliminates the downstream JSON parse + validate code we would
  otherwise own. The dispatch is URL-prefix (`openai://`,
  `jev://`, `stub://`) via `Backend.matches` (§4.1) — swapping
  to Anthropic or Google Gemini is a new concrete + a config
  change, no caller edits. Alternatives (OpenAI chat completions
  with JSON mode, LangChain structured output, instructor-style
  wrappers) all add parse-and-validate code we deleted when the
  responses API shipped.

Each per-section Decisions block (§2.x, §3.x, §4.x) covers the
schema-shape or algorithmic choices that this list does not —
Literal-vs-free-text, JsonStore batch-only semantics, index-based
LLM output. Together they cover the technology-choice layer and
the design-choice layer.

### 4.1 LLM Dispatch (ModelCaller)

**Role.** Single chokepoint for all LLM and Jev calls. Owns: cache
(diskcache with plaintext JSON keys), retries, response-model coercion,
cost telemetry, backend selection.

**Dispatch flow (ASCII).**

```
Call site (one of 4 LLM tasks: version_diff / citation_verify /
           obligation_extraction / mapping)
    │
    │   PromptSpec.render() → (system_message, user_message)
    │
    v
ModelCaller.call(task_name, params, response_type, call_spec,
                 tenant_id, environment)
    │
    │ (a) Load TaskModelConfig for task from DomainConfig
    │     - model_identifier: 'openai://gpt-5.4-nano' | 'stub://...'
    │     - max_tokens, reasoning_effort, verbosity
    │
    │ (b) Compute diskcache key (plaintext JSON dict: model +
    │     prompts + max_tokens + response_type.__name__)
    │
    │     ┌───────────────────┐
    ├────>│  CACHE HIT:       │────> return response (short-circuit)
    │     │  parsed Pydantic  │
    │     └───────────────────┘
    │
    │ (c) CACHE MISS: dispatch by URL-prefix via Backend.matches
    │     (facade — no inline startswith() ladder)
    │
    ├────> StubBackend (stub://)
    │        └─> hand-constructed response from
    │            PROMPT_REGISTRY[task_name].stub_module
    │            (deterministic; used by tests + --stub_all regen)
    │
    ├────> OpenAIBackend (openai://)
    │        │
    │        v
    │     client.responses.parse(
    │        model=model_name,
    │        input=[{role,content}, ...],
    │        text_format=response_type,       # Pydantic model
    │        reasoning={'effort': ...},
    │        text={'verbosity': ...})
    │        │
    │        │  Retry on RateLimitError / InternalServerError /
    │        │  APIConnectionError (exponential backoff, 3 attempts).
    │        │  NEVER retry on auth / 4xx schema errors.
    │        v
    │        structured response → parsed Pydantic instance
    │        │
    │        │  Emit openai_backend.call telemetry:
    │        │  {task_name, model_name, input_tokens, output_tokens,
    │        │   estimated_cost_usd}
    │        v
    │
    ├────> JevBackend (jev://)
    │        └─> NotImplementedError (shell reserved for future
    │            Jev integration; see JEV.md)
    │
    │ (d) Store parsed in diskcache (same plaintext JSON key)
    │ (e) --confirm_llm: print prompt + token count + parsed response,
    │     prompt y/n before dispatch; print parsed response after
    │     dispatch (both via _prompt_user / _print_response)
    │
    v
return response (Pydantic instance of response_type)
```

_Source: [`src/model_caller.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/model_caller.py) — `Backend` Protocol [L60](https://github.com/ngs2410/strata-build-challenge/blob/main/src/model_caller.py#L60), `ModelCaller` [L81](https://github.com/ngs2410/strata-build-challenge/blob/main/src/model_caller.py#L81). Backends: [`src/backends/openai_backend.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/backends/openai_backend.py) (retry logic + cost telemetry), [`src/backends/jev_backend.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/backends/jev_backend.py) (NotImplementedError shell), [`src/backends/stub_backend.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/backends/stub_backend.py)._

**v1.**
- `src/model_caller.py::ModelCaller.call(task_name, params, response_type,
  call_spec, tenant_id, environment)` is the one entry point. It reads
  `task_config = load_domain_config(tenant_id, environment)
  .task_model_configs[task_name]` and dispatches based on
  `task_config.model_identifier`.
- **Backend dispatch is URL-prefix on `model_identifier`.** Each backend
  implements `Backend.matches(model_identifier) -> bool` + `Backend.call(...)`.
  Three backends are registered in v1: `openai://<model-name>` →
  `src/backends/openai_backend.py` (dispatches via the OpenAI responses
  API with structured outputs); `jev://<model-name>` →
  `src/backends/jev_backend.py` (a NotImplementedError shell in v1 —
  the seam is reserved for the real Jev client, which lands via a
  follow-up that LLM-emulates Noul / Choice / Score behind the same
  prefix before the real TypeSafe AI client replaces the emulation);
  `stub://<task-name>` → `src/backends/stub_backend.py` (routes to a
  task-specific stub function via `PROMPT_REGISTRY`; used by tests and
  by `--dry_run`). The `Backend.matches` facade keeps call sites
  backend-agnostic — adding a provider is a new matcher, not an
  edit at every call site (per CLAUDE.md's facade-dispatch rule).
- **Diskcache** key is a plaintext JSON dict:
  `{model_identifier, max_tokens, reasoning_effort, verbosity,
  response_type, input, inputs_dict}`. Cache dir `.model_cache/`,
  in `.gitignore`.
- **Fan-out**: NOT shipped in v1. v1 cardinality is <20 everywhere,
  so every call site uses the sequential
  `for item in items: model_caller.call(...)` form. Fan-out helper
  (`fan_out_for_task(...)` or an equivalent) lands when a stage's
  cardinality crosses ~100 (per CLAUDE.md's sequential-at-v1-scale
  rule) — likely the hierarchical diff in §3.2 or the two-stage
  extraction in §3.5.
- **Retry**: exponential backoff on 429 / 5xx with max 3 retries
  inside `openai_backend.OpenAIBackend.call`.
- **Cost telemetry**: every call logs `{task_name, model_name,
  input_tokens, output_tokens, estimated_cost_usd}` via structlog.

**Production.**
- Adopt `fan_out_for_task` at every stage whose cardinality crosses 100.
- Add per-tenant cost ceilings as `DomainConfig` fields; `ModelCaller`
  refuses dispatch above budget.
- Add OTel traces per call (parent-child spans across stages).
- Replace Jev stub with real client (`typesafe-sdk`'s
  `POST /v1/systemone`).

**Future (tenant-driven, post-wedge).** The existing URL-prefix dispatch
on `TaskModelConfig.model_identifier` reserves the seam; wiring BYO is
additive, not a redesign. Preserves the CLAUDE.md "no irreversible
vendor dependencies" principle.
- **Tenant-supplied API keys** via `DomainConfig`, resolved through a
  `secret://env/...` indirection or a prod-time secret manager.
  Per-tenant cost accounting already implies per-tenant key accounting;
  BYO keys shifts the cost line to the tenant.
- **Alternative providers** (Anthropic, Google, self-hosted vLLM,
  on-prem endpoints) land as new URL prefixes (e.g.
  `anthropic://claude-opus-4-7`, `vllm://local-llama3`) with matching
  `Backend` matchers alongside the current `openai://` / `jev://` /
  `stub://` adapters. Call sites stay backend-agnostic.
- **Tenant-owned fine-tuned models** are a different `<model-name>`
  suffix behind the chosen prefix — no changes to task-level call
  sites.

**Decisions.**
- **One chokepoint for all AI dispatch (OpenAI + Jev).** Caching,
  retries, cost telemetry, and structured-output discipline all live in
  one place. Call sites stay backend-agnostic — they name only the
  `task_name`; `ModelCaller` looks up `TaskModelConfig` and routes by
  `model_identifier` prefix. Direct SDK calls scattered through the
  codebase would duplicate every one of these concerns and prevent
  swapping backends.
- **Jev-shaped seam at every discrimination point, even before the real
  Jev is wired.** v1 ships `jev://` as a registered prefix in
  `ModelCaller`; the `JevBackend` matcher raises `NotImplementedError`
  until the follow-up commit lands LLM-emulated Noul / Choice / Score.
  Discrimination tasks (`verify_citation`, `change_type_classify`,
  `route_destination`, `action_recommend`) will flip to `jev://...`
  model identifiers at that point. The real client migration is `pip
  install typesafe-sdk` + swap the matcher's inner call.
- **Plaintext JSON cache keys** (CLAUDE.md). Hashed keys are
  unrenamable; expensive LLM calls get repaid for a cosmetic rename.
- **Diskcache over Redis.** Local, replayable, zero infrastructure.
  Redis adds a service with no v1 benefit.

### 4.2 Prompt Management

**Role.** Author, version, and dispatch LLM-task prompts with a
consistent structure. Prompts are release artefacts, not inline strings.

_Source: `PromptSpec` + `CallSpec` dataclasses in [`src/llm.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/llm.py) (`PromptSpec` [L49](https://github.com/ngs2410/strata-build-challenge/blob/main/src/llm.py#L49), `CallSpec` [L97](https://github.com/ngs2410/strata-build-challenge/blob/main/src/llm.py#L97)). Shared rubric constants in [`src/prompt_calibration.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/prompt_calibration.py). Task-name → prompt-builder registry in [`src/prompt_registry.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/prompt_registry.py)._

**v1.**
- Every LLM task has a dedicated module (`src/version_diff.py`,
  `src/obligation_extraction.py`, `src/mapping.py`,
  `src/citation/citation_verify.py`) containing: a
  `build_<task>_prompt(...)` function returning a `PromptSpec`, a
  response Pydantic model, and a `CallSpec`. Per PROMPT_CONVENTIONS.md.
- `src/prompt_registry.py` maps task names → prompt builder functions,
  response classes, eval case paths, and config keys.
- `src/llm.py` defines `PromptSpec` (task / steps / details / rubrics /
  pitfalls / field_intents / json_example / inputs_dict) and `CallSpec`
  (purpose / acceptance_criteria / failure_modes).
- Prompts are plain-Python builders checked into git. Prompt diffs are
  code diffs.
- Shared rubric content (`OBLIGATION_TAXONOMY_REFERENCE`,
  `CONFIDENCE_LEVEL_RUBRIC`, `CHANGE_TYPE_RUBRIC`) is built
  programmatically from a data source (PROMPT_CONVENTIONS.md §10), not
  copy-pasted.

**Production.**
- Prompt versioning in `DomainConfig` so A/B tests across prompt
  versions are a config change, not a code deploy.
- Prompt-tuning harness (grid search or Optuna over wording/rubric
  variations) measured against the gold set.
- Full prompt + response sampled and logged per LLM call for post-hoc
  analysis.

**Decisions.**
- **PromptSpec dataclass vs. templating library (Jinja, LangChain).**
  PromptSpec is deterministic, testable, and introspectable (every
  section is a named attribute). Templating indirection makes it hard
  to grep the actual prompt a model saw. The discipline is codified in
  PROMPT_CONVENTIONS.md; the dataclass enforces it structurally.
- **json_example conventions** (PROMPT_CONVENTIONS.md §4). Placeholders
  over grounded values prevents the model from priming on example
  values and fabricating similar ones.
- **Integer-index selection for list picks** (PROMPT_CONVENTIONS.md §5).
  Appears in §3.3 (citation grounding) and §3.6 (mapping).
- **CallSpec is per-task, not global.** Each task's `acceptance_criteria`
  and `failure_modes` feed into eval authoring and prompt-tuning signals.

### 4.3 Configuration

**Role.** All hyperparameters live in `DomainConfig` sub-models with
JSON defaults, loaded per `(tenant_id, environment)`. Never in code.

_Source: `DomainConfig` + sub-models in [`src/config.py` L200-L242](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L200-L242) (`EscalationConfig` [L200](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L200), `MappingConfig` [L206](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L206), `CitationConfig` [L212](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L212), `TaskModelConfig` [L218](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L218), `DomainConfig` [L227](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L227)); loader `load_domain_config` [L235](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L235) with deep-merge of tenant override. Defaults: [`config/default_domain_config.json`](https://github.com/ngs2410/strata-build-challenge/blob/main/config/default_domain_config.json)._

**v1.**
- `config/default_domain_config.json` — baseline for all tenants.
- `data/tenants/<tenant_id>/<environment>/domain_config.json` —
  tenant+environment-specific overrides, dict-merged over defaults at
  load time.
- `src/config.py::load_domain_config(tenant_id, environment)` is the one
  function that reads these; returns a validated `DomainConfig` Pydantic
  model.
- Sub-models: `TaskModelConfig` (per LLM task — `model_identifier`
  (with backend URL prefix), `max_tokens`, `reasoning_effort`,
  `verbosity`, `included_inputs`), `EscalationConfig` (`mapping_threshold`,
  `citation_threshold`), `MappingConfig` (`candidate_set_size`,
  `context_window_tokens`), `CitationConfig` (`verification_required`,
  `min_citation_span_chars` — minimum `citing_span` width in characters;
  grounding uses this as the unverified floor per §3.3).

**Production.**
- Postgres `domain_config_*` tables keyed by
  `(tenant_id, environment, config_leaf_path) → value`.
- Admin CLI for setting config values: `strata-admin set-config
  --tenant_id=verdant_grid --path=escalation.mapping_threshold
  --value=4`.
- Config change history as its own audit log so "what was the threshold
  on 2026-11-02?" is answerable.

**Decisions.**
- **Pydantic + JSON, not YAML / TOML / env vars.** Strict parsing,
  schema enforcement, easy diffing. Env vars are forbidden for config
  per CLAUDE.md (secrets-only).
- **Hyperparameters-in-config, not constants-in-code** (CLAUDE.md). Any
  threshold, limit, cutoff, model id, or tuning knob lives in
  `DomainConfig`.
- **One file per tenant+environment** vs. one file per tenant with
  environment branches. v1 picks per-file for isolation; prod moves to
  Postgres where the shape doesn't matter.

### 4.4 Persistent Storage

**Role.** Save and load Pydantic models with backend-agnostic facade
methods. Every method takes `(tenant_id, environment, ...)`.

_Source: [`src/stores.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/stores.py) — `Store` ABC [L63-L68](https://github.com/ngs2410/strata-build-challenge/blob/main/src/stores.py#L63-L68), `JsonStore` [L71-L88](https://github.com/ngs2410/strata-build-challenge/blob/main/src/stores.py#L71-L88), `JsonlStore` [L91-L114](https://github.com/ngs2410/strata-build-challenge/blob/main/src/stores.py#L91-L114), module-level singletons [L117-L127](https://github.com/ngs2410/strata-build-challenge/blob/main/src/stores.py#L117-L127)._

**v1.**
- `src/stores.py` defines `Store[T]` ABC with two batch methods:
  `save_all(tenant_id, environment, items: list[T]) -> None` and
  `load_all(tenant_id, environment) -> list[T]`. No per-item CRUD —
  v1 writes the full entity file at once per stage; all-or-nothing
  semantics keep the write path atomic. Per-item surface (`load_by_id`,
  `delete`, filter kwargs) is a prod-shape add through the facade
  seam when a workflow actually needs it.
- `JsonStore[T]` concrete: files at
  `data/tenants/<tenant_id>/<environment>/<filename>` (one JSON array
  per entity type). `save_all` serialises the full list and writes;
  `load_all` reads and validates every item.
- Audit log is special: `src/audit_log.py` uses `JsonlStore[AuditEvent]`
  — line-delimited JSON for cheap append.
- Typed instances exported as module-level singletons:
  `proceeding_store: Store[ProceedingVersion] = JsonStore(ProceedingVersion, 'proceedings.json')`,
  etc. — the `JsonStore` constructor takes model type + filename as
  positional args.

**Production.**
- `PostgresStore[T]` concrete — same ABC. Pydantic models round-trip via
  SQL columns (one column per leaf field; nested models become
  foreign-keyed child tables per CLAUDE.md's no-JSONB rule).
- `audit_events` becomes a Postgres append-only table; materialised
  views for folded state (§3.7).

**Decisions.**
- **Facade pattern even with one concrete backend.** The ABC reserves
  the slot. Adding a Postgres concrete later is one file; call sites
  don't change.
- **JSON full-file R/W vs. JSONL append vs. SQLite.** JSON works at v1
  scale (<100 items per entity type). JSONL is right for the audit log
  (append-only is natural). SQLite would add a dependency with no v1
  benefit and would still need the facade.
- **One file per entity type, not one file per entity.** Reduces inode
  churn; still works at v1 scale.
- **Why not pickle?** Not human-inspectable, not debuggable, not
  language-portable. JSON wins all three.

### 4.5 Data Isolation / Multi-Tenancy

**Role.** Enforce that no data belonging to tenant A is ever visible to
tenant B. Enable multi-environment deployment (dev / staging / prod)
within a tenant.

_Source: tenant list + `DEFAULT_ENVIRONMENT` in [`src/tenant_registry.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/tenant_registry.py). Per-tenant data lives under [`data/tenants/`](https://github.com/ngs2410/strata-build-challenge/tree/main/data/tenants); the `(tenant_id, environment)` leading-param rule is enforced on every facade method in [`src/stores.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/stores.py), [`src/config.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/config.py#L235), and [`src/model_caller.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/model_caller.py#L81)._

**v1.**
- Every facade method takes `(tenant_id, environment)` as leading
  parameters (CLAUDE.md tenant plumbing rule).
- `src/tenant_registry.py` holds the tenant list +
  `DEFAULT_ENVIRONMENT = 'prod'` constant. Per-tenant data lives at
  `data/tenants/<tenant_id>/<environment>/*`.
- CLI scripts take `--tenant_id` and `--environment` flags; HTTP routes
  take `tenant_id` and `environment` query parameters.
- `DEFAULT_ENVIRONMENT` is legitimate only in CLI flag defaults,
  facade-ABC parameter signatures, test fixtures, and its own definition
  site — never hardcoded at call sites.

**Production.**
- Postgres row-level security (RLS): every table has a `tenant_id`
  column; session sets `app.current_tenant_id`; RLS policies filter
  queries automatically.
- Separate Postgres schemas per environment (`prod_schema`,
  `staging_schema`) with the same table shapes.
- Request-level auth binds `tenant_id` + `environment` from the auth
  token; HTTP routes cannot request a tenant they don't belong to.

**Future (tenant-driven, post-wedge).** RLS + per-environment schemas
in a shared Postgres is the default production shape. Some regulated
enterprises will require stronger **physical isolation** — data-residency
rules, procurement-approved infrastructure lists, or security teams that
reject shared-database designs on principle. The `Store[T]` facade +
`(tenant_id, environment)` plumbing reserves the seam:
- A tenant marked `isolation: 'physical'` resolves to a **tenant-specific
  Postgres instance** (or cluster) via a per-tenant `Store[T]` concrete
  selection — the facade signature is unchanged; call sites stay
  backend-agnostic.
- Co-exists with the shared-RLS default: tenants without the flag
  continue to share the main Postgres. Physical isolation is per-tenant
  opt-in, not a global mode flip.
- The `Backend.matches(identifier) -> bool` dispatch pattern
  (CLAUDE.md) accommodates the per-tenant backing URL without touching
  callers.

**Decisions.**
- **Explicit `(tenant_id, environment)` parameters** vs. implicit from
  request context. Explicit makes testing and CLI usage straightforward
  and migration to RLS trivial. Implicit (thread-locals, request
  contexts) is magic.
- **`environment` as a peer of `tenant_id`, not a subdirectory of some
  default.** Dev / staging / prod are first-class axes (CLAUDE.md
  invariant).
- **One tenant in v1 (Verdant Grid), one environment (prod).** The
  plumbing is in place for N tenants × M environments; the data has one
  row.

### 4.6 Telemetry

**Role.** Structured logging of every interesting event; enough to debug
a run and to compute cost per `(tenant_id, task_name)`.

_Source: [`src/telemetry.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/telemetry.py) (`configure_logging` [L31](https://github.com/ngs2410/strata-build-challenge/blob/main/src/telemetry.py#L31), `get_logger` [L48](https://github.com/ngs2410/strata-build-challenge/blob/main/src/telemetry.py#L48)). Cost telemetry emission in [`src/backends/openai_backend.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/backends/openai_backend.py)._

**v1.**
- `src/telemetry.py` configures `structlog` to emit JSON to stderr.
- Log levels via `LOG_LEVEL` env var (a legitimate env-var use per
  CLAUDE.md — library-controlled).
- Every LLM dispatch emits two structlog events at INFO:
  - `model_caller.call` with `{task_name, model_identifier,
    tenant_id, environment, cache_hit: False}` — logged by the
    `ModelCaller` chokepoint on every non-cached dispatch.
  - `model_caller.cache_hit` with `{task_name, model_identifier,
    tenant_id, environment}` — logged on diskcache hits (the
    `cache_hit=False` field on `model_caller.call` disambiguates).
  - `openai_backend.call` with `{task_name, model_name,
    input_tokens, output_tokens, estimated_cost_usd}` — logged by
    the OpenAI concrete after a successful dispatch; carries the
    cost telemetry.
- Retry events at WARN (`openai_backend.retry` with
  `{attempt, next_delay_seconds, error_type}`) and ERROR
  (`openai_backend.retry_exhausted`).
- Every stage logs start / end events (`ingest.start` / `.done`,
  `diff.start` / `.done`, etc.) with pipeline-scoped kwargs.

**Production.**
- OpenTelemetry Collector pipeline: Tempo for traces (per-run span
  tree), Loki for structured logs, Prometheus/Mimir for metrics
  (per-task latency / error histograms), Grafana for dashboards.
- Structlog's JSON output converts cleanly to OTel log records — the
  migration is a config change in the collector, not a code change at
  call sites.

**Decisions.**
- **Structured from day one.** JSON logs over print statements.
  Grep-able, parseable, machine-ready for OTel without rewriting call
  sites.
- **structlog over the stdlib logging module.** structlog's bind-context
  model threads correlation IDs through nested scopes cleanly; stdlib
  logging requires explicit `extra={}` on every call.
- **No OTel collector in v1.** Overkill. structlog's JSON stderr is the
  signal; adding a collector adds a daemon with no additional data
  value until a dashboard is actually being built.

### 4.7 Security

**Role.** Keep secrets out of source; keep tenants isolated from each
other; keep auditable records of who did what.

_Source: dotenv loading in `configure_logging` ([`src/telemetry.py` L31](https://github.com/ngs2410/strata-build-challenge/blob/main/src/telemetry.py#L31)). Secret template: [`.env.example`](https://github.com/ngs2410/strata-build-challenge/blob/main/.env.example). Actor plumbing on every state transition: [`src/audit_log.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/audit_log.py)._

**v1.**
- Secrets (`OPENAI_API_KEY`, future `TYPESAFE_API_KEY`) loaded via
  `dotenv` from `.env`. `.env` in `.gitignore`; `.env.example` ships
  with placeholder values.
- No user auth. The web view takes `tenant_id` from a query parameter;
  whoever runs the server trusts that parameter. Appropriate because v1
  runs locally for build-challenge review.
- Audit log records `actor` on every state transition. In v1 all
  human-triggered events have `actor='local_user'`; system events have
  `actor='system'`.

**Production.**
- **AuthN**: OIDC / SSO via Auth0 or a workspace IDP. Tenant membership
  encoded in the user's token.
- **AuthZ**: RBAC with per-tenant roles (`reviewer` can approve /
  override; `viewer` can only read; `admin` can manage config). Enforced
  in FastAPI dependencies.
- **Secrets**: secret manager (AWS Secrets Manager, GCP Secret Manager,
  or Vault) with `secret://` indirection in `bootstrap.json` (per
  CLAUDE.md env-vars rule — the escape hatch is a secret-manager
  reference, never the config value itself).
- **Encryption**: at-rest (storage layer) + in-transit (TLS). Per-tenant
  encryption keys for cross-tenant blast-radius containment.
- **Audit retention**: per-tenant per-category retention policies
  (regulatory audit logs often carry multi-year retention — needs legal
  input).

**Decisions.**
- **No auth in v1.** The local-prototype deployment model has no
  multi-user story to prove. Adding JWT or HTTP Basic in v1 is theatre —
  reviewers would see auth code that doesn't do anything meaningful.
- **Tenant_id in query parameter in v1.** Explicit, loggable, easy to
  swap for an OIDC-derived claim later.
- **dotenv over committed-config-with-decryption.** Standard Python
  shape; secrets never see git.
- **Audit log serves security + product.** Every approval / override /
  escalation is on the same log that drives Rachel's workspace.
  Security's "who approved this?" question is answered by the same
  query that drives the UI.

---

## 5. Evals (depth area)

The eval harness is the empirical substrate for every trust claim in the
PRD. Without it, "citation precision ≥ 98%" and "mapping precision ≥
90%" are aspiration, not evidence.

### 5.1 Gold Set

_Source: gold data [`data/evals/gold.json`](https://github.com/ngs2410/strata-build-challenge/blob/main/data/evals/gold.json); schemas in [`src/evals.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py) — `ExpectedChange` [L57](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L57), `ExpectedVerification` [L65](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L65), `ExpectedExtraction` [L72](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L72), `ExpectedAffectedItem` [L80](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L80), `ExpectedMapping` [L88](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L88), `GoldCaseInput` [L97](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L97), `GoldCase` [L107](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L107)._

- `data/evals/gold.json` — 1 hand-labelled case (FERC Order 2222
  §35.28(g)(1)(ix)) with 16 aggregate labels, labelled by the
  author as a proxy for Rachel: 3 expected changes + 3 citation
  verifications + 7 obligation extractions + 3 mappings.
- Schema (full source of truth in `src/evals.py`): each `GoldCase`
  has
  - `case_id: str`
  - `input: GoldCaseInput` with `docket_number`, `proceeding_id`,
    `prior_version_id`, `new_version_id`, `prior_version_file`,
    `new_version_file`
  - FLAT sibling fields: `expected_changes`,
    `expected_verifications`, `expected_extractions`,
    `expected_mappings`
  - `company_context_tenant: str | None` — tenant to read the
    company context from when scoring mappings
- Content: FERC Order 2222 §35.28(g)(1)(ix) in both NOPR and Final
  Rule form, plus Verdant Grid company-context slices (8
  obligations / 3 projects / 5 documents).

**Growing the gold set (production).** As Rachel reviews live traffic,
every override produces a new ground-truth label. v1 logs these but does
not auto-promote them; v2 adds a candidate-gold bucket with weekly
expert review before promotion.

### 5.2 Metrics

Each metric is computed by `src/evals.py`. The eval harness emits a
report (JSON + markdown) on every end-to-end run.

_Source: scoring in [`src/evals.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py) (`evaluate_case` [L158](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L158), `score_verifications` [L186](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L186), `score_extractions` [L208](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L208), `score_mappings` [L240](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L240), `aggregate` [L410](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L410), `render_text_report` [L515](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L515), `render_markdown_report` [L592](https://github.com/ngs2410/strata-build-challenge/blob/main/src/evals.py#L592))._

| Metric                            | Shape                                                                         | Target (per PRD §4) |
|-----------------------------------|-------------------------------------------------------------------------------|---------------------|
| Citation precision                | fraction of claimed citations whose cited passage actually supports the claim | ≥ 98%               |
| Citation recall                   | fraction of true claims in gold that got extracted with a citation            | track               |
| Mapping precision                 | fraction of suggested (change × affected-item) pairs that Rachel would accept | ≥ 90%               |
| Mapping recall                    | fraction of true (change × affected-item) pairs in gold that got surfaced     | track               |
| Change-detection F1               | per-change-type precision/recall against gold                                 | track               |
| High-confidence precision         | fraction of items labelled `confidence >= 4` that are actually correct        | ≥ 95%               |
| Reliability diagram               | per-confidence-bucket precision histogram — detects over/under-confidence     | visual              |
| Escalation acceptance (prod only) | fraction of escalations Rachel judges justified                               | ≥ 80%               |

### 5.3 Named Failure Modes

Each has a reproducing eval case in the gold set.

- **Hallucinated passage ID.** The LLM emits a `passage_id` that doesn't
  exist in the enumerated list, or substring-matching the citing span
  finds nothing. Verifier catches this; `Citation.verified = False` with
  rationale "citing span not found."
- **Over-mapping.** A change gets mapped to >5 affected items when gold
  says 1. Reproducing case: a specific NOPR→Final tightening of a single
  reporting cadence requirement that should affect exactly one
  obligation but tempts the LLM to also flag two tangentially-related
  operational obligations.
- **Draft/final confusion.** The system treats a NOPR citation as
  authoritative. Reproducing case: an obligation proposed in the NOPR
  but softened in the Final Rule — correct behaviour is to flag the
  Final Rule's weaker version, not the NOPR's stricter one.
- **Confidence collapse.** The LLM returns `confidence = 5` on
  everything regardless of actual uncertainty. Detected via the
  reliability diagram — the `confidence = 5` bucket would have much
  lower precision than 95%. Mitigation: tighter rubric; ensemble
  sampling; eventual Jev migration for discrimination tasks.
- **Routing fan-out confusion.** A change that genuinely affects two
  teams gets routed to only one. Open-question #6; v1 limitation.

### 5.4 Harness

_Source: [`scripts/main_eval.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/scripts/main_eval.py). Makefile target `eval-report` in [`Makefile`](https://github.com/ngs2410/strata-build-challenge/blob/main/Makefile)._

- `scripts/main_eval.py --tenant_id=verdant_grid
  --gold_file=data/evals/gold.json` runs the full pipeline (ingest →
  diff → cite → extract → map) on gold inputs, compares to expected
  outputs, prints a metrics table and a text-rendered reliability
  diagram, writes a JSON report to
  `output/evals/<YYYYMMDD-HHMMSS>.json`.
- Flags: `--tenant_id`, `--environment`, `--gold_file`,
  `--output_dir`, `--md_output`, `--dry_run`, `--k`, `--profile`,
  `--confirm_llm`.
- `--k=5` runs on a 5-case slice for smoke testing (CLAUDE.md
  scripts rule).
- `--profile` emits a cProfile top-50 (CLAUDE.md scripts rule).
- `--md_output=<path>` writes a human-readable markdown report
  (headline-numbers table + full text report) alongside the JSON.
  The `make eval-report` convenience target wires this to a dated
  committed artifact at
  `docs/eval-results/eval-results-<YYYY-MM-DD>.md` — the submission
  trust-metric evidence.

**Production.**
- CI integration: every PR runs the eval harness; metric regressions
  block merge.
- Per-prompt-version metric tracking so prompt A/B tests are measurable.
- Drift detection: compare current eval against a trailing baseline;
  alert on >5% metric regression.

### 5.5 Why evals are the depth area

PREP_MATERIAL.md calls out two kinds of technical spike reviewers look
for; empirical validation is one of them. Without the eval harness,
every trust metric in [PRD §4](PRD.md#4-trust-and-adoption-metrics-for-expert-users)
is aspirational. With it, we can defend the trust story in the panel
interview with real numbers against a specific gold set.

The eval harness is where every open technical question becomes
concrete:
- Confidence calibration ([§8.1](#81-confidence-calibration-beyond-self-report)) —
  the reliability diagram *is* the measurement.
- Mapping threshold ([§8.3](#83-mapping-confidence-threshold)) — the
  harness tells us where to set it empirically.
- Material-change taxonomy ([§8.5](#85-material-change-taxonomy-stability)) —
  the confusion matrix on `change_type` tells us where it's underspecified.
- Jev migration (future) — A/B-measurable on this harness.

---

## 6. Interface Surfaces

### 6.1 CLI

**Role.** The operator-facing surface. absl-flagged scripts.

_Source: [`scripts/main_detect_changes.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/scripts/main_detect_changes.py), [`scripts/main_eval.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/scripts/main_eval.py), [`scripts/main_review.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/scripts/main_review.py). Pipeline orchestration: [`src/pipeline.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/pipeline.py)._

- `scripts/main_detect_changes.py --tenant_id --environment` — ingests
  and runs the §3.1–3.6 pipeline end-to-end in one command. v1 keeps
  ingest + pipeline combined (see Decisions below).
- `scripts/main_review.py [--host 127.0.0.1] [--port 8000] [--reload]`
  — launches the FastAPI web view (uvicorn wrapper). `tenant_id` /
  `environment` are HTTP query parameters on `/queue` and `/audit`,
  not CLI flags — one process serves every tenant.
- `scripts/main_eval.py --tenant_id --gold_file` — runs evals
  end-to-end.

The pipeline scripts (`main_detect_changes.py`, `main_eval.py`)
support `--dry_run`, `--k`, `--profile`, `--confirm_llm` (per
CLAUDE.md). `main_review.py` is a thin uvicorn wrapper and only
exposes `--host`, `--port`, `--reload`.

**Production.**
- Scheduled execution via cron / airflow. `main_detect_changes.py`
  becomes an idempotent task triggered on new-proceeding-version
  arrival.
- Scripts emit OTel spans; failures page oncall.

**Decisions.**
- **absl flags over argparse.** Centralised flag registration;
  cross-script flag reuse; standard flag types.
- **Combined ingest + pipeline in v1; split in a future commit.** The
  original plan was a separate `scripts/main_ingest.py` that persists
  ingested proceedings + company context, with `main_detect_changes.py`
  reading from the persisted state. For v1, keeping ingest inline in
  `main_detect_changes.py` is a smaller surface and reviewers run one
  command. The split happens when scheduled / incremental ingest is
  added (production path).

### 6.2 Web View

**Role.** [Rachel](PRD.md#1-the-user)'s surface. Two routes, two
templates.

_Source: FastAPI app in [`src/web_app.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/web_app.py) (`queue` [L81](https://github.com/ngs2410/strata-build-challenge/blob/main/src/web_app.py#L81), `audit` [L149](https://github.com/ngs2410/strata-build-challenge/blob/main/src/web_app.py#L149), `post_decide` [L165](https://github.com/ngs2410/strata-build-challenge/blob/main/src/web_app.py#L165)); templates [`templates/queue.html`](https://github.com/ngs2410/strata-build-challenge/blob/main/templates/queue.html) + [`templates/audit.html`](https://github.com/ngs2410/strata-build-challenge/blob/main/templates/audit.html)._

**v1.**
- `src/web_app.py` FastAPI app:
  - `GET /queue?tenant_id=&environment=` → `templates/queue.html` —
    prioritised `ReviewItem` list; each card carries the Change
    description, source citation (passage text with the citing span
    `<mark>`-highlighted inline), confidence, and a per-pair table of
    affected (change × asset) rows with per-item proposed_action +
    proposed_route, current `AssetDecision` state (if any), and a
    bulk toolbar with four buttons `[approve] [override] [dismiss]
    [escalate]`.
  - `GET /audit?tenant_id=&environment=` → `templates/audit.html` —
    read-only chronological `AuditEvent` list for the tenant /
    environment, with `batch_id` surfaced per event so bulk per-pair
    decisions share a visible group id. v1 is log-viewer only; the
    "rollback to this point" affordance is deferred to future work
    along with the rollback implementation itself (see §8.8).
  - `POST /queue/{review_item_id}/decide` — handles single-row or
    bulk-select per-pair decisions. Form fields: repeated
    `affected_item_indexes`, one `decision` ∈ {approved, overridden,
    dismissed, escalated}, one `action` ∈ {review, amend, no_op},
    one `route` ∈ {market_ops, legal, it_metering}, one `rationale`.
    Delegates to `apply_asset_decisions`; writes N `AssetDecision`s
    + N `AuditEvent`s sharing a `batch_id` (None when N==1);
    redirects back to queue.
- Jinja2 templates; one tiny `<script>` block in `queue.html` wires
  each card's "select all" checkbox to its row checkboxes (vanilla
  JS, no framework). All other state server-rendered.
- Auth: `tenant_id` in query parameter (no multi-user story in v1).

**Production.**
- SPA (React / Svelte) for real-time updates (WebSocket).
- OIDC / SSO integration.
- Team dashboards (per-reviewer backlog, per-project state).
- Bulk operations (approve many at once with a shared rationale).
- Rich filtering + saved views.

**Decisions.**
- **FastAPI + Jinja2 server-rendered, no JS framework.** v1's job is to
  prove the backend produces output Rachel can act on. A client-side
  framework is theatre at v1 scale.
- **Query-parameter tenant selection in v1.** Trivial to migrate to
  session-bound tenant once auth exists.
- **Server-rendered highlights.** Source citation span highlighted
  inline in the queue view; no AJAX, no client-side DOM manipulation.

---

## 7. Scaling Pathway

Summary: for each component, v1 shape → prod shape + first bottleneck.

| Component             | v1 shape                    | Prod shape                                          | First bottleneck                     |
|-----------------------|-----------------------------|-----------------------------------------------------|--------------------------------------|
| Persistence           | JSON + JSONL files          | Postgres + materialised views                       | >1000 items per entity type          |
| LLM dispatch          | Sequential + diskcache      | `fan_out_for_task` + diskcache + budget caps        | >100 items per stage                 |
| Diff                  | 1 LLM call per subsection   | Textual pre-filter + Jev Choice + Jev Score         | Context > subsection (~1500 tokens)  |
| Citation grounding    | LLM-emits-index + substring | Full passage index + Jev Noul re-anchor             | Draft→final with restructuring       |
| Citation verify       | LLM-as-NLI                  | **Jev Noul** — direct replacement                   | Every claim; cost dominates at scale |
| Obligation extraction | 1 LLM call per subsection   | Jev Noul pre-filter + LLM extract + Jev Noul verify | Full documents (660pp × 100/yr)      |
| Mapping               | 1 LLM call, full context    | Three paths — see §3.6                              | Company context > ~200 items         |
| Review queue          | JSONL fold in memory        | Postgres materialised view                          | ~100K audit events per tenant        |
| Audit log             | JSONL append                | Postgres append + partitioning                      | Same as review queue                 |
| Config                | JSON + Pydantic             | Postgres config + admin CLI                         | Needing runtime config changes       |
| Telemetry             | structlog JSON stderr       | OTel Collector + Grafana                            | Cross-service tracing                |
| Security              | dotenv + tenant_id in URL   | OIDC + secret manager + RLS                         | First real user besides reviewer     |
| Web UI                | FastAPI + Jinja2            | SPA + WebSocket + OIDC                              | Real-time updates / multi-reviewer   |
| Deployment            | Dev-run Python on one host  | Containerized (OCI) → tenant cloud or on-prem       | First tenant demanding own cloud / on-prem |
| Model backend         | OpenAI frontier + diskcache | Per-tenant backend + BYO key + tenant fine-tuned    | First tenant with provider restrictions or BYO-key |
| Tenant data isolation | Filesystem per tenant       | Shared Postgres + RLS default; per-tenant DB opt-in | First tenant requiring physical isolation |

### Capacity envelope

- **v1 full end-to-end run:** 2 versions × ~2000 tokens per subsection →
  3 Changes → 3 ExtractedObligations × 2 versions → 3 Mappings against
  16-item company context → ~10 pipeline AuditEvents per end-to-end run
  (per-pair asset_decision events come from Rachel's queue actions, not
  the pipeline). 7 LLM calls total (1 version_diff + 1 citation_verify
  + 2 obligation_extraction + 3 mapping); ~15s wall-clock cold cache,
  <1s warm.
- **Prod envelope target:** 100+ proceedings/year × 10+ versions × 50+
  sections each; 1000+ obligations per company; 10K+ audit events per
  project per quarter.
- **First scaling pressure:** the mapping call's context overflow when
  company context exceeds ~200 items. All three Mapping scaling paths
  (§3.6) are designed for this.
- **Second scaling pressure — cross-tenant combinatorics.** The §3.6
  paths handle one tenant at a time; a production deployment with N
  customers sharing a regulator or K regulators per customer runs
  into an aggregate N × M mapping matrix that LLM-per-pair cannot
  afford. The amortisation path (cross-tenant change-detection store
  + learned BERT-tier pre-filter + Jev middle cull + LLM on
  survivors) is written up as [§8.9](#89-cross-tenant-amortisation--multi-tier-mapping-cull).

---

## 8. Open Technical Questions

Decisions consciously deferred, each with a lean and a next-move trigger.

### 8.1 Confidence calibration beyond self-report

_Mirrors [PRD §7 bullet "Confidence-threshold calibration"](PRD.md#7-open-questions-and-unvalidated-assumptions)._

**State.** v1 uses LLM-self-reported `confidence: int (0-5)` with a
rubric in the prompt and a required `confidence_rationale`. Reliability
measured against gold via the eval harness.

**Open.** Self-reported confidence is not natively calibrated. The
reliability diagram will show us *where* calibration fails; it won't fix
it.

**Next moves (in order of ambition).**
1. Prompt-engineering iteration — tighter rubric wording per task;
   decomposition into named sub-factors (for Mapping:
   `citation_strength`, `obligation_similarity`, `change_specificity`,
   aggregated deterministically).
2. Ensemble sampling — N=3 completions per task; agreement rate as a
   confidence proxy. Expensive.
3. **Jev migration for discrimination tasks** (citation verify,
   change-type classify, routing, severity scoring). Jev's RLCD-trained
   probabilities are the strongest calibration story in-market (JEV.md
   §6).
4. Fine-tuned classifier on accumulated gold once >200 labelled items
   exist. Produces per-task calibrated probabilities from a small
   supervised model.

**Trigger.** Reliability diagram's high-confidence bucket drops below
95% precision.

### 8.2 Passage re-anchoring across versions

**State.** v1 handles alignment inline in §3.2's diff LLM call — both
versions in context, implicit alignment.

**Open.** When draft→final has substantial restructuring (sections
renumbered, split, merged), inline alignment degrades.

**Next moves.**
1. Dedicated passage-alignment pass before diff: Jev Noul per candidate
   alignment pair.
2. Fuzzy string matching for mild edits.
3. Human-in-the-loop reviewer resolution for ambiguous alignments.

**Trigger.** First proceeding where the diff LLM produces alignment
errors we can't fix with prompt tweaks.

### 8.3 Mapping confidence threshold

_Mirrors [PRD §7 bullet "≥ 90% mapping-precision target"](PRD.md#7-open-questions-and-unvalidated-assumptions)._

**State.** Default 3 (on 0–5 scale) in
`DomainConfig.escalation.mapping_threshold`. Per-tenant tunable.

**Open.** What should the default be, empirically?

**Next move.** Run the eval harness; set the default at the threshold
where high-confidence precision hits 95% and escalation acceptance (via
Rachel's simulated accept rate) hits 80%.

**Trigger.** First run of the eval harness against the full v1 gold
set.

### 8.4 Gold set drift and growth

_Mirrors [PRD §7 bullet "Synthetic company context fidelity"](PRD.md#7-open-questions-and-unvalidated-assumptions)._

**State.** v1 ships 30 hand-labelled items. Rachel's overrides are
logged but not auto-promoted.

**Open.** How do live overrides become new gold items without
introducing noise?

**Next move (v2).** Candidate-gold bucket with weekly expert review
before promotion. Pattern: new failure in production → eval case →
regression caught on next CI run.

**Trigger.** First time >5 live overrides exist that aren't yet in gold.

### 8.5 Material-change taxonomy stability

_Mirrors [PRD §7 bullet "Material change is operator-defined"](PRD.md#7-open-questions-and-unvalidated-assumptions)._

**State.** v1 ships closed Literal of five values (added / removed /
tightened / loosened / cosmetic).

**Open.** Does the taxonomy stay closed, or do we need an open-tag
secondary dimension?

**Next move.** Collect every override where Rachel disagrees with the
assigned change_type; cluster the disagreements; propose taxonomy
adjustments.

**Trigger.** First week of real reviewer use.

### 8.6 Multi-reviewer routing — **CLOSED in v1**

_Mirrors [PRD §7 bullet "Routing is single-destination in v1"](PRD.md#7-open-questions-and-unvalidated-assumptions)._

**State (closed).** v1 ships per-item (action, route) on every
`AffectedItem`. One `Change` touching N diverse assets produces N
(`proposed_action`, `proposed_route`) proposals — different items in
the same `Mapping` can route to different teams. Rachel curates
per-pair via `AssetDecision` (TDD §2.6b, §3.7) and emits one audit
event per pair with a shared `batch_id` for bulk operations. The
mapping-level `recommended_action` / `routing_destination` fields
that this section originally deferred are gone from the schema —
replaced by per-item fields on `AffectedItem`.

**Rationale for closing in v1.** The observed Order 2222 §(A) run
forced four assets (two teams, two action types) into one tuple at
the mapping level, which hid the diversity from Rachel. The per-item
model restores fidelity without introducing a workflow engine. The
one-approval-unblocks-all / all-must-approve question doesn't
disappear — it collapses to "each pair is its own decision"; cascading
escalation becomes "escalated pair fold wins the ReviewItem.status
aggregate" (`_fold_status` in `src/review_queue.py`).

**Deferred to v2 (narrower).** When ReviewItem-level approval needs
to notify multiple teams by email / Slack / Linear, the notification
fan-out layer (NOT the data model) still has to pick who gets
notified first vs. CC vs. silent. That's a workflow-engine problem,
not a schema problem. The data model is already shaped for it.

### 8.7 Audit log behaviour across version transitions

**State.** v1 version-pins approvals — new version produces new review
items; old approvals remain as historical records.

**Open.** When a change's content is semantically identical between
versions, should the prior approval auto-apply?

**Next move.** Semantic-equivalence check via Jev Noul: "is this change
identical in substance to the prior-version approved change?" before
auto-generating a new review item.

**Trigger.** Rachel's feedback shows routine re-review of unchanged
items is a significant time cost.

### 8.8 Pure fold-over-log state + rollback

**State.** v1 ships a pragmatic hybrid: the audit log is the source of
truth for *history*; `ReviewItem.status` (a mutable column on the
persisted ReviewItem) is the source of truth for *right now*. Every
status mutation emits a matching `AuditEvent` on the same transaction,
so the pair is reconciled on write. The `/audit` web view is a
read-only log viewer in v1.

**Open.** Two intertwined architectural moves live here:
- **Pure fold:** make the audit log the sole source of truth; derive
  `ReviewItem.status` at read time by scanning events for that
  review_item_id. Removes the "both must agree" invariant at write
  time; adds a fold helper + caching discussion.
- **Rollback-as-replay:** `audit_log.replay_until(timestamp)`
  reconstructs state as of a cutoff, enabling a "rollback to this
  point" affordance on the audit page. One of CHALLENGE.md's six
  optional depth areas (not the two we chose).

**Next move.** `src/state_fold.py` with
`fold_review_item_status(review_item_id)` + `replay_until(timestamp)`;
`/audit` page gains a rollback button that fires a transactional
staging-state swap. Trades the mutable column for a materialised view
in production.

**Trigger.** Either (a) a reviewer specifically asks for undo of a
mis-approval, (b) the audit log and status column drift in practice,
or (c) we choose audit-and-rollback as a depth area in a future
revision.

### 8.9 Cross-tenant amortisation + multi-tier mapping cull

**State.** The current pipeline is calibrated for the v1 wedge: one
tenant, one proceeding, one subsection, ~16-item company context.
Every change is detected per-tenant from scratch; every candidate
mapping goes through a full LLM call. The §3.6 scaling paths
(candidate pre-filter, Jev decomposition, fine-tuned classifier)
handle growing *per-tenant* corpus size, but do not address the
cross-tenant and multi-regulator combinatorics that production
hits almost immediately. The §7 scaling-pathway table identifies
the per-stage first bottleneck but not the aggregate blow-up when
N tenants × M regulator documents × K changes × L company-context
items all compose.

**Open — the production N × M matrix.** At realistic scale (a
600-page order, 100+ proceedings per year per regulator, 10+
customers of the same regulator across multiple regulated
industries), the aggregate mapping surface is `N_customers ×
M_changes × L_items`. LLM-per-pair is impractical: a single
regulator issuing a quarterly rule produces O(1000) change-item
pairs per customer; across 100 customers that's O(100K)
candidate mappings per rule issuance. GPT-tier latency and cost
make this a non-starter at the frontier model and marginal even
at nano.

**Next moves — three composable amortisations:**

1. **Cross-tenant change-detection cache.** `version_diff` output
   is a property of the *regulator's* two document versions, not
   of any one tenant. The `.model_cache` key already includes the
   full prompt input, so two customers of the same RTO/ISO should
   deterministically hit the same cached detection. Promote this
   from an incidental diskcache artifact to an explicit
   cross-tenant **change store** keyed by `(regulator,
   proceeding_id, prior_version_id, new_version_id)` — one
   detection pass per regulator per rule issuance, N tenants read
   the same results. This leaves obligation extraction (also
   tenant-independent per version) in the same bucket. Only
   mapping and review are per-tenant.

2. **Learned pre-filter (BERT-tier classifier).** Before any LLM
   mapping call, run a small supervised classifier that scores
   `P(this change substantively affects this candidate item | change
   text + item summary)`. A MiniLM-sized model fine-tuned on
   accumulated Rachel-labelled accept/dismiss outcomes (open
   question 8.4) culls obviously-irrelevant mappings — plausibly
   10-100× reduction in LLM-mapping surface at the floor. The
   classifier runs on CPU, latency measured in milliseconds per
   candidate.

3. **Jev as the medium-cull middle tier.** For survivors of the
   BERT cull, run Jev Noul `"does this change substantively affect
   this item?"` in parallel across the remaining candidates (Jev's
   many-questions-per-state architecture is designed for exactly
   this shape — see JEV.md §5). Jev's calibrated probabilities
   provide a second filter at a cost / latency profile between the
   classifier and the LLM.

4. **LLM only on the probable matches.** The N × M matrix shrinks
   to the top-k survivors of the two culls — plausibly single-digit
   candidates per change at steady state. This is where the LLM's
   per-item rationale generation adds the most value: on items
   where the yes/no is already settled, writing the semantic
   mapping and recommending (action, route).

**Trigger.** Either (a) first prospective tenant whose company
context exceeds ~200 items (first per-tenant pressure, mitigated by
§3.6 (a) alone), (b) second prospective tenant sharing a regulator
with an existing tenant (triggers the cross-tenant change store
immediately), or (c) a prospective regulator-adjacent product
decision (e.g., "serve all MISO aggregators as one deployment")
that pushes the amortisation value past the wedge-stage
engineering investment.

### 8.10 Multi-user concurrency control

_Mirrors [PRD §6 bullet "Multi-user web UI"](PRD.md#6-non-goals-for-v1)._

**State.** v1 is single-user. `tenant_id` is a query parameter (no
auth, see §4 Security Decisions), the review-queue page is
server-rendered per request, and the JSON stores take no locks — the
single process is the only writer, and the only user is Rachel.
`POST /queue/{review_item_id}/decide` hardcodes `actor` to Rachel's
identity (see §6.2 v1).

**Open.** Rachel's actual team at Verdant has three analysts
([PRD §1](PRD.md#1-the-user)). In a production deployment they would
plausibly all operate against the same review queue, which the
current implementation does not support. Specific race conditions:

- Two analysts simultaneously `POST /decide` the same (change ×
  asset) pair — last-write-wins silently; the audit log records both
  events but the `AssetDecision` reflects only the second writer, so
  one analyst's intent is lost without warning.
- Analyst A loads the queue at T; analyst B decides a pair at T+1;
  A then bulk-decides including the stale pair — A's POST overwrites
  B's fresh decision.
- The `_fold_status` read over `AssetDecision` (see
  `src/review_queue.py`) is not isolated from concurrent writes —
  intermediate half-folded `ReviewItem.status` values can be
  observed.

**Next move.** The data-model foundation is in place:
`AuditEvent.actor` serialises every state transition (§2.7). Missing
layer: (a) OIDC / SSO to identify the analyst (§4 Security Decisions
already names this as the production shape); (b) optimistic
concurrency on `AssetDecision` writes via an `expected_version`
field checked at commit time, or pessimistic `SELECT ... FOR UPDATE`
once the store is Postgres (§7 Scaling Pathway, Persistence row);
(c) a short-lived "reviewing" marker with TTL as presence indicator;
(d) WebSocket push to invalidate stale queue views (§6.2 Production
already names this).

**Trigger.** First tenant onboarded with more than one named
reviewer, OR first observed last-write-wins incident in Rachel's own
operation (two tabs → two decisions).

---

## Appendix: Technology Choices

One row per component. "Why this" captures the key trade-off.

| Component              | Chosen (v1)                           | Considered                                    | Why this                                                |
|------------------------|---------------------------------------|-----------------------------------------------|---------------------------------------------------------|
| Language               | Python 3.12                           | Rust, Go, TypeScript                          | Pydantic ecosystem; LLM/Jev SDKs Python-native          |
| LLM backend            | OpenAI frontier                       | Anthropic, Google, local vLLM                 | Structured-outputs maturity; latency reliability        |
| Discrimination backend | OpenAI (v1 stub) → **Jev** (prod)     | Zero-shot LLM; fine-tuned classifier day-one  | Right shape for many-small-decisions; v1 stubs the seam |
| Response schema        | Pydantic structured outputs           | JSON schema, regex extraction                 | Type safety + `Literal` enforcement per CLAUDE.md       |
| Cache                  | diskcache                             | Redis, no cache, in-memory                    | Local, replayable, zero infra; plaintext keys           |
| Web framework          | FastAPI                               | Flask, Django, Streamlit                      | Async-ready; HTTP JSON + templating; well-typed         |
| Templating             | Jinja2                                | React/Svelte, Streamlit, HTMX                 | Server-rendered zero-JS fits v1 scope                   |
| Logging                | structlog                             | print+logger, OTel day-one                    | Structured JSON day-one; OTel-migratable                |
| Config                 | Pydantic + JSON                       | YAML, TOML, env vars                          | Schema enforcement; env vars forbidden for config       |
| Persistence            | JSON + JSONL via `JsonStore[T]`       | SQLite, Postgres, pickle                      | Zero infra at v1 scale; facade reserves Postgres slot   |
| Diff                   | LLM-only (v1)                         | difflib, embedding, hybrid                    | LLM handles semantic equivalence; hybrid is prod        |
| Verification           | LLM-as-NLI (v1) → **Jev Noul** (prod) | DeBERTa NLI, keyword overlap                  | LLM OK for v1 gold; Jev wins cost+latency at scale      |
| Secrets                | dotenv                                | Secret manager, env-direct, inline            | Standard; `.env` in `.gitignore`; `secret://` reserved  |
