# Strata TDD — Technical Design Document

## Preamble

Technical design for Strata v1 — a regulatory change-to-action workspace
for in-house regulatory affairs teams. Scope, user, and non-goals are
established in **PRD.md**; this document covers the architecture and the
reasoning behind each piece.

Governing documents for code written against this design:
- **PRD.md** — scope, user, non-goals, success metrics
- **CLAUDE.md** — code style, mandatory design-compliance rules, tooling,
  engineering principles
- **PROMPT_CONVENTIONS.md** — authoring conventions for LLM tasks
- **TEST_QUALITY.md** — test-quality rules
- **JEV.md** — reference for the Jev discrimination model (used across
  the design)

A note on scope: this is a build-challenge-sized prototype. For every
component below, the pattern is **Role → v1 → Production → Decisions**:
what the component does, how it's implemented now, how it scales, and
the alternatives considered with trade-offs. The "Production" column is
a specific scaling path, not a complete production design.

Strata uses a deliberate **two-model AI architecture**:
- **OpenAI frontier model** for *generation* — open-ended extraction,
  free-text rationale, candidate production.
- **Jev** (TypeSafe AI's System One decision model — see JEV.md) for
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
   Enforced in §3.3; verified in §3.4; measured in §5.
2. Low-confidence interpretations escalate to a human reviewer — the
   system fails to a reviewer, not to a guess. Enforced at the
   review-queue gate (§3.7); threshold is a `DomainConfig` hyperparameter
   (§4.3).
3. Project state is a fold over an append-only audit log. Enforced in
   §3.7; rollback is replay-to-timestamp.
4. Draft vs. final is a first-class attribute on every proceeding version
   (`Literal['draft', 'final']`). Enforced in §2.
5. Data isolation is per `(tenant_id, environment)`. Every facade method
   takes these as leading parameters (§4.5).

**Depth areas** — components where the v1 implementation is more
thoroughly executed:
- **Citation grounding** (§3.3 + §3.4): structural anchors, a dedicated
  verifier, dedicated evals.
- **Evals** (§5): hand-labeled gold set, named failure modes with
  reproducing cases, reliability diagram for confidence calibration,
  metrics report produced on every end-to-end run.

Both map directly to the PRD's trust metrics (citation precision ≥ 98%;
mapping precision ≥ 90%; confidence calibration ≥ 95% on high-confidence
claims). The trust story falls over if either is weak.

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

**Decisions.**
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

**Decisions.**
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
    recommended_action: Literal['review', 'amend', 'no_op']
    routing_destination: Literal['market_ops', 'legal', 'it_metering']
    confidence: int                         # 0-5 overall mapping confidence
    confidence_rationale: str
    rationale: str                          # free-text explanation


class AffectedItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_type: Literal['obligation', 'project', 'document']
    item_id: str
    match_confidence: int                   # 0-5 per-item
    match_rationale: str
```

**Decisions.**
- **One `Mapping` per `Change`, with a tuple of `AffectedItem`
  children.** A change maps to a *set* of affected items (potentially
  from all three types) but gets *one* overall recommended action and
  *one* routing destination. Per-item `match_confidence` is distinct
  from the overall `confidence`.
- **`routing_destination` as `Literal`, not free text.** v1 ships three
  destinations; prod makes this configurable per-tenant.
- **Single `routing_destination` per Change — a v1 scope choice, not a
  modeling claim.** Real regulatory changes often touch multiple teams
  in parallel. v1 captures only the primary team; the expansion to
  `routing_destinations: tuple[Literal[...], ...]` (primary + CC, or
  parallel) is tracked as open question §8.6 along with the
  workflow-engine questions it opens.
- **`recommended_action` is a `Literal` of three values.** Alternatives:
  open-ended free text (unverifiable; forces Rachel to interpret every
  action), per-action-type structured outputs (over-engineered). Three
  categorical values is the right granularity for the review UI.

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

A `ReviewItem` is a reader's lens over a `Mapping` — see §3.7. `status`
is the current fold over `AuditEvent`s, not a mutable column (per
CLAUDE.md invariant).

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
        'rollback_executed',
    ]
    target_type: Literal['proceeding_version', 'change', 'mapping', 'review_item']
    target_id: str
    prior_value_json: str | None            # the full prior Pydantic model as JSON
    new_value_json: str | None
    rationale: str | None
```

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

### 3.1 Ingest

**Role.** Load the two proceeding-version text files and the
company-context JSON for a tenant. Normalise into Pydantic models
(`ProceedingVersion`, `Obligation`, `Project`, `Document`). Persist via
`Store[T]` facades.

**v1.**
- Input: `data/tenants/verdant_grid/proceedings/order-2222-nopr.txt` +
  `order-2222-final.txt` +
  `data/tenants/verdant_grid/company_context.json`.
- `src/ingest.py` reads each file, splits proceeding text into passages
  via a lightweight structural parser (section-path extraction via regex
  on section headers like `§35.28(g)(1)(ix)`), and constructs
  `ProceedingVersion` + `Passage` models.
- Driver:
  `scripts/main_ingest.py --tenant_id=verdant_grid --environment=prod`.

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
  `changes: list[Change]`.
- Cardinality in v1: 1 LLM call per end-to-end run. Sequential loop (per
  CLAUDE.md's revised fan-out rule for <20 items).

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

### 3.3 Citation Grounding (depth area)

**Role.** Every detected `Change` (and later every extracted
`Obligation`) carries a structural citation —
`(passage_id, start_offset, end_offset)` — pointing to the exact text in
the source `Passage` that supports the claim. This is the core trust
property of the product.

**v1.**
- The LLM task that produces `Change`s (§3.2) also returns, for each
  change, the `passage_id` (as a 0-based integer index into the
  enumerated list of passages in the prompt — per PROMPT_CONVENTIONS.md
  §5) plus a `citing_span: str` containing the verbatim text span the
  LLM claims supports the change.
- Post-generation, `src/citation/citation_grounding.py` resolves the
  integer index → `passage_id` string, then computes `start_offset` /
  `end_offset` by substring-matching `citing_span` within the
  `Passage.text`.
- If no match: `Citation.verified` starts `False` and
  `verification_rationale` records "citing span not found in cited
  passage." This is a loud, auditable failure, not a silent one.

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
- **LLM emits integer index, not the passage ID string.** LLMs truncate
  / transpose / hallucinate long opaque IDs. Short integer indexes into
  a short list are impossible to confuse (PROMPT_CONVENTIONS.md §5). The
  caller translates the index back to `passage_id`.
- **Why not hash-anchoring (storing a hash of the passage text as the
  anchor)?** Hashes change under any edit, including whitespace or
  renumbering. Structural anchors survive.

### 3.4 Citation Verification (depth area)

**Role.** For every claim with a citation, independently verify that the
cited passage actually supports the claim. Failures surface in
`Citation.verified = False` with a rationale; downstream code treats
unverified claims as low-confidence and routes them to escalation.

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

**v1.**
- `src/obligation_extraction.py` runs an LLM task per subsection. Prompt
  includes `OBLIGATION_TAXONOMY_REFERENCE` — the four `obligation_type`
  values with descriptions and worked examples, built programmatically
  from a data source (per PROMPT_CONVENTIONS.md §10).
- Response model: `ObligationExtractionResponse` with
  `extracted_obligations: list[ExtractedObligation]`. The
  `ExtractedObligation` has fields `obligation_type`, `summary`,
  `full_text`, `citation`, `confidence`, `confidence_rationale`.
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
internal destination (one of three in v1 — see §8.6 for the
multi-reviewer open question).

**v1.**
- `src/mapping.py` runs **one LLM call** that sees:
  - The `Change` (with citation).
  - The *full* Verdant company context — all obligations, projects,
    documents rendered as enumerated lists per PROMPT_CONVENTIONS.md §5.
- Returns a `Mapping` object: `affected_items` (tuple of `AffectedItem`
  referring to context items by 0-based integer index),
  `recommended_action`, `routing_destination`, overall `confidence`, and
  `rationale`.
- Cardinality: 1 LLM call per `Change` (~5 Changes per end-to-end run).
  Sequential.

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
  - **Jev Choice** for `recommended_action`.
  - **Jev Choice** for `routing_destination`.
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
- **One LLM call sees the full context in v1.** 16-item context fits
  comfortably in a single prompt; multiple LLM calls would introduce
  reconciliation cost and inter-call inconsistency. At scale this
  doesn't hold — paths (a) / (b) / (c) address that.
- **Index-based selection** (PROMPT_CONVENTIONS.md §5). LLM returns
  0-based integer indexes into the enumerated context; caller translates
  to IDs. Prevents ID hallucination.
- **Jev decomposition in (b) over per-sub-task LLM calls.** The problem
  decomposes naturally into *many parallel yes/no questions over shared
  state* — exactly what Jev is architected for. The LLM equivalent is
  many sequential or weakly-batched calls with worse cost and worse
  calibration.
- **`routing_destination` as `Literal`.** v1 ships three destinations
  matching Verdant's teams; pluggable per-tenant via `DomainConfig` in
  prod.
- **Single destination per Change is a v1 scope choice.** The schema
  captures one primary team per change. Real changes often touch
  multiple teams; the shape expansion is open-question §8.6 and
  explicitly out of v1 scope.

### 3.7 Review & Audit

**Role.** Rachel approves, overrides, or escalates each `ReviewItem` in
the queue. Every transition is logged to the append-only audit log.
Current state (status, owner, action) is a fold over the log — rollback
is replay-to-timestamp.

**v1.**
- `src/review_queue.py` constructs `ReviewItem`s from `Mapping`s.
  Priority is a function of `mapping.confidence` × a severity proxy
  (number of affected items).
- **Escalation gate**: on construction, if `mapping.confidence <
  DomainConfig.escalation.mapping_threshold` (default 3 on the 0–5
  scale), the `ReviewItem` is created with `status='escalated'`
  immediately — bypasses the auto-approval path per CLAUDE.md invariant.
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

### 4.1 LLM Dispatch (ModelCaller)

**Role.** Single chokepoint for all LLM and Jev calls. Owns: cache
(diskcache with plaintext JSON keys), retries, response-model coercion,
cost telemetry, backend selection.

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
- **Fan-out**: a `fan_out_for_task(...)` helper is reserved on the
  `ModelCaller` surface for future work. v1 cardinality is <20
  everywhere, so every call site uses the sequential
  `for item in items: model_caller.call(...)` form. The fan-out
  helper lands when a stage's cardinality crosses ~100 (per
  CLAUDE.md). It is NOT present in v1.
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

**v1.**
- `config/default_domain_config.json` — baseline for all tenants.
- `data/tenants/<tenant_id>/<environment>/domain_config.json` —
  tenant+environment-specific overrides, dict-merged over defaults at
  load time.
- `src/config.py::load_domain_config(tenant_id, environment)` is the one
  function that reads these; returns a validated `DomainConfig` Pydantic
  model.
- Sub-models: `TaskModelConfig` (per LLM task — model id, max tokens,
  reasoning effort, backend_type, included_inputs), `EscalationConfig`
  (per-task escalation thresholds), `MappingConfig` (candidate-set size,
  context window), `CitationConfig` (verification required y/n, min
  citation offsets).

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

**v1.**
- `src/stores.py` defines `Store[T]` ABC with
  `save(tenant_id, environment, item: T) -> None`,
  `load(tenant_id, environment, item_id: str) -> T`,
  `list(tenant_id, environment, **filters) -> list[T]`,
  `delete(tenant_id, environment, item_id: str) -> None`.
- `JsonStore[T]` concrete: files at
  `data/tenants/<tenant_id>/<environment>/<model_name>s.json` (one JSON
  array per entity type). `list` loads the file and filters in Python;
  `save` reads, updates/appends, writes.
- Audit log is special: `src/audit_log.py` uses `JsonlStore[AuditEvent]`
  — line-delimited JSON for cheap append.
- Typed instances exported as module-level singletons:
  `proceeding_store: Store[ProceedingVersion] = JsonStore[ProceedingVersion]()`,
  etc.

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

**v1.**
- `src/telemetry.py` configures `structlog` to emit JSON to stderr.
- Log levels via `LOG_LEVEL` env var (a legitimate env-var use per
  CLAUDE.md — library-controlled).
- Every LLM / Jev call logs `{task_name, model_id, backend_type,
  tenant_id, environment, input_tokens, output_tokens,
  estimated_cost_usd, cache_hit: bool, latency_ms}` at INFO.
- Every stage logs start / end events with a correlation ID threaded
  through for the run.

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

- `data/evals/gold.json` — ~30 hand-labelled items, labelled by the
  author as a proxy for Rachel.
- Each item: `{input: {proceeding_version_pair, change_text,
  company_context_slice}, expected: {changes: [...], obligations: [...],
  mappings: [...], citations: [...]}}`.
- Coverage: ~10 obligation extractions, ~5 change detections per version
  pair (prior/final), ~5 mapping decisions, ~5 citation verifications
  (positive + negative pairs).
- Content: FERC Order 2222 §35.28(g)(1)(ix) in both NOPR and Final Rule
  form, plus Verdant Grid company-context slices.

**Growing the gold set (production).** As Rachel reviews live traffic,
every override produces a new ground-truth label. v1 logs these but does
not auto-promote them; v2 adds a candidate-gold bucket with weekly
expert review before promotion.

### 5.2 Metrics

Each metric is computed by `src/evals.py`. The eval harness emits a
report (JSON + markdown) on every end-to-end run.

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

- `scripts/main_eval.py --tenant_id=verdant_grid
  --gold_file=data/evals/gold.json` runs the full pipeline (ingest →
  diff → cite → extract → map) on gold inputs, compares to expected
  outputs, prints a metrics table and a text-rendered reliability
  diagram, writes a JSON report to `output/evals/<timestamp>.json`.
- `--k=5` runs on a 5-item slice for smoke testing (CLAUDE.md scripts
  rule).
- `--profile` emits a cProfile top-50 (CLAUDE.md scripts rule).

**Production.**
- CI integration: every PR runs the eval harness; metric regressions
  block merge.
- Per-prompt-version metric tracking so prompt A/B tests are measurable.
- Drift detection: compare current eval against a trailing baseline;
  alert on >5% metric regression.

### 5.5 Why evals are the depth area

PREP_MATERIAL.md calls out two kinds of technical spike reviewers look
for; empirical validation is one of them. Without the eval harness,
every trust metric in PRD §4 is aspirational. With it, we can defend the
trust story in the panel interview with real numbers against a specific
gold set.

The eval harness is where every open technical question becomes
concrete:
- Confidence calibration (open-q #1) — the reliability diagram *is* the
  measurement.
- Mapping threshold (open-q #3) — the harness tells us where to set it
  empirically.
- Material-change taxonomy (open-q #5) — the confusion matrix on
  `change_type` tells us where it's underspecified.
- Jev migration (future) — A/B-measurable on this harness.

---

## 6. Interface Surfaces

### 6.1 CLI

**Role.** The operator-facing surface. absl-flagged scripts.

- `scripts/main_detect_changes.py --tenant_id --environment` — ingests
  and runs the §3.1–3.6 pipeline end-to-end in one command. v1 keeps
  ingest + pipeline combined (see Decisions below).
- `scripts/main_review.py --tenant_id --environment --port=8000` —
  launches the FastAPI web view (uvicorn wrapper).
- `scripts/main_eval.py --tenant_id --gold_file` — runs evals
  end-to-end.

Every script supports `--dry_run`, `--k`, `--profile`, `--confirm_llm`
(per CLAUDE.md).

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

**Role.** Rachel's surface. Two routes, two templates.

**v1.**
- `src/web_app.py` FastAPI app:
  - `GET /queue?tenant_id=&environment=` → `templates/queue.html` —
    prioritised `ReviewItem` list; each item shows the Change
    description, source citation (link to the Passage text with the
    citing span highlighted), affected items, recommended action,
    confidence, and `[approve] [override] [escalate]` buttons.
  - `GET /audit?tenant_id=&environment=` → `templates/audit.html` —
    read-only chronological `AuditEvent` list for the tenant /
    environment. v1 is log-viewer only; the "rollback to this point"
    affordance is deferred to future work along with the rollback
    implementation itself (see §8.8).
  - `POST /queue/<review_item_id>/action` — handle
    approve/override/escalate; emits `AuditEvent`, updates
    `ReviewItem.status`, redirects back to queue.
- Jinja2 templates; no JS beyond a single form submit. All state
  server-rendered.
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
  ~5 Changes → ~8 ExtractedObligations × 2 → ~5 Mappings against 16-item
  company context → ~10 AuditEvents per review session. ~20 LLM calls
  total; ~30s wall-clock cold cache, <1s warm.
- **Prod envelope target:** 100+ proceedings/year × 10+ versions × 50+
  sections each; 1000+ obligations per company; 10K+ audit events per
  project per quarter.
- **First scaling pressure:** the mapping call's context overflow when
  company context exceeds ~200 items. All three Mapping scaling paths
  (§3.6) are designed for this.

---

## 8. Open Technical Questions

Decisions consciously deferred, each with a lean and a next-move trigger.

### 8.1 Confidence calibration beyond self-report

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

**State.** Default 3 (on 0–5 scale) in
`DomainConfig.escalation.mapping_threshold`. Per-tenant tunable.

**Open.** What should the default be, empirically?

**Next move.** Run the eval harness; set the default at the threshold
where high-confidence precision hits 95% and escalation acceptance (via
Rachel's simulated accept rate) hits 80%.

**Trigger.** First run of the eval harness against the full v1 gold
set.

### 8.4 Gold set drift and growth

**State.** v1 ships 30 hand-labelled items. Rachel's overrides are
logged but not auto-promoted.

**Open.** How do live overrides become new gold items without
introducing noise?

**Next move (v2).** Candidate-gold bucket with weekly expert review
before promotion. Pattern: new failure in production → eval case →
regression caught on next CI run.

**Trigger.** First time >5 live overrides exist that aren't yet in gold.

### 8.5 Material-change taxonomy stability

**State.** v1 ships closed Literal of five values (added / removed /
tightened / loosened / cosmetic).

**Open.** Does the taxonomy stay closed, or do we need an open-tag
secondary dimension?

**Next move.** Collect every override where Rachel disagrees with the
assigned change_type; cluster the disagreements; propose taxonomy
adjustments.

**Trigger.** First week of real reviewer use.

### 8.6 Multi-reviewer routing

**State.** v1 routes each change to a single *primary* destination by
design — the schema (`Mapping.routing_destination` in `src/config.py`),
the gold-set schema (`ExpectedMapping.routing_destination` in
`src/evals.py`), and the mapping prompt + `MAPPING_SPEC` CallSpec in
`src/mapping.py` all encode one destination per change. This is a
scope decision, not a modeling claim about reality.

**Open.** Real regulatory changes often touch multiple teams in
parallel — e.g. the Order 2222 telemetry requirement belongs primarily
to IT & Metering but also to Market Operations (which configures DER
aggregation offers around telemetry data). The current gold case
labels the primary team per change; secondary teams aren't modelled.

**Next move (v2).** `routing_destinations: tuple[Literal[...], ...]`
with a primary + CC model. Workflow-engine decisions (one-approval-
unblocks-all vs. all-must-approve, cascading escalation) are v2 open
design questions.

**Trigger.** Rachel's feedback shows systematic manual re-routing to a
second team.

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
