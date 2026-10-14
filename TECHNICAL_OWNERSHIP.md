# Technical Ownership — Q&A

Short reference answers to the panel-prep questions in
[PREP_MATERIAL.md](PREP_MATERIAL.md), with pointers to the actual
code. Every claim here is a `grep` away from the file that proves
it; every `L<N>` link resolves to the current line.

See also: [PRD.md](PRD.md) (user, scope, trust metrics), [TDD.md](TDD.md)
(architecture + per-section `_Source: ..._` links), [README.md](README.md)
(reviewer runbook).

---

## Q1. The exact file and function that implement the core loop

**[`src/pipeline.py::run_pipeline(tenant_id, environment, dry_run=False)`](src/pipeline.py#L90)**
is the single orchestration surface. 100 lines. It calls every stage
in order, logs `<stage>.start` / `<stage>.done` events, persists
each stage's output through the `Store[T]` facades, and emits an
`AuditEvent` for every produced artefact. Scripts and tests both
drive the pipeline through this one function — neither constructs
stages ad hoc.

The orchestrator doesn't contain pipeline logic. Each stage lives
in its own module and `run_pipeline` is `grep`-able start to finish:

```
ingest   → src/ingest.py::ingest_tenant_fixture       (L137)
diff     → src/version_diff.py::detect_changes        (L273)
ground   → src/citation/citation_grounding.py
             ::ground_citations_for_changes           (L51)
verify   → src/citation/citation_verify.py
             ::verify_citations                       (L156)
extract  → src/obligation_extraction.py
             ::extract_obligations                    (L167)
             + ground_obligations (grounding.py:L77)
map      → src/mapping.py::map_change                 (L239)
enqueue  → src/review_queue.py::enqueue_mapping       (L59)
```

Entry points from CLI:
[`scripts/main_detect_changes.py`](scripts/main_detect_changes.py)
calls `run_pipeline` directly.
[`scripts/main_eval.py`](scripts/main_eval.py) assembles the stages
by hand so it can score each one independently against a
`GoldCase`.
[`scripts/main_review.py`](scripts/main_review.py) doesn't run the
pipeline — it serves the FastAPI app that *reads* the pipeline's
persisted outputs.

---

## Q2. Narrating the loop, step by step, as if running it live

Call: `python -m scripts.main_detect_changes --tenant_id=verdant_grid
--environment=prod`. `configure_logging()` loads `.env` (so
`OPENAI_API_KEY` is available) and installs the structlog JSON
sink. Then `run_pipeline('verdant_grid', 'prod', dry_run=False)`:

1. **Ingest**
   ([`src/ingest.py::ingest_tenant_fixture`](src/ingest.py#L137)).
   Reads `data/tenants/verdant_grid/proceedings/manifest.json`,
   iterates its entries, and for each one calls
   [`ingest_proceeding_version`](src/ingest.py#L86) with the metadata
   from the manifest plus the referenced text file. The parser is
   a plain regex (`SECTION_HEADER_RE`,
   [`src/ingest.py:L17`](src/ingest.py#L17)) that splits on FERC
   section headers like `§35.28(g)(1)(ix)` to produce a tuple of
   `Passage(passage_id, section_path, position, text)`. Also loads
   `company_context.json` via
   [`ingest_company_context`](src/ingest.py#L112) — the 8/3/5
   obligations/projects/documents. All models go to
   [`persist_ingest`](src/ingest.py#L123) which writes them through
   the typed Store singletons at
   [`src/stores.py:L117-L127`](src/stores.py#L117-L127).

2. **Diff**
   ([`src/version_diff.py::detect_changes(prior, final)`](src/version_diff.py#L391)).
   Builds one `PromptSpec` from `build_version_diff_prompt`,
   renders system + user messages, and calls `model_caller.call`
   with `task_name='version_diff'`. The LLM response parses into
   `VersionDiffResponse.changes: tuple[LlmChange, ...]` where each
   `LlmChange.prior_citation` / `.new_citation` is an
   `LlmCitation(passage_id, citing_span)` — **no offsets yet**. One
   LLM call per end-to-end run.

3. **Ground citations**
   ([`src/citation/citation_grounding.py::ground_citations_for_changes`](src/citation/citation_grounding.py#L80)).
   Deterministic Python runs `passage.text.find(citing_span)` for
   each `LlmCitation`. Successful match becomes a persisted
   `Citation` with real `start_offset` / `end_offset`,
   `verified=False`, rationale `'pending citation_verify stage'`.
   Three failure paths (all return `Citation` with
   `verified=False` and a specific rationale — the system fails to
   a reviewer, not to a guess):
   - Unknown `passage_id` → `ValueError` (hard — LLM hallucinated).
   - `len(citing_span) < min_citation_span_chars` → "span width N
     below citation.min_citation_span_chars=M".
   - `.find()` returns -1 → "citing span not found in cited
     passage".

4. **Verify citations**
   ([`src/citation/citation_verify.py::verify_citations`](src/citation/citation_verify.py#L277)).
   Only runs if `DomainConfig.citation.verification_required`
   (default `True`). One LLM call per `Change` with the full
   grounded `Citation`'s passage text + offsets rendered into the
   prompt. Flips `verified=True/False` + fresh rationale. In the
   stub path this always sets `verified=True`; the real LLM is
   what catches "citing span exists but doesn't support the
   claim".

5. **Extract obligations** (once per version). The LLM task
   ([`src/obligation_extraction.py::extract_obligations`](src/obligation_extraction.py#L305))
   returns `LlmExtractedObligation` with `LlmCitation`; same
   grounding step via
   [`ground_obligations`](src/citation/citation_grounding.py#L106)
   converts to persisted `ExtractedObligation` with real offsets.

6. **Map change → affected items**
   ([`src/mapping.py::map_change`](src/mapping.py#L432)). One LLM
   call per `Change`. The prompt sees the change description + the
   company context (obligations/projects/documents, capped at
   `config.mapping.candidate_set_size` — default 50; at v1
   cardinality of 16 items it's a no-op, but the cap is
   first-class for scaling-path (a)). LLM returns
   `MappingResponse.affected_items:
   tuple[AffectedItemByIndex, ...]` with `item_index: int` into
   the respective candidate list.
   [`_resolve_affected_item`](src/mapping.py#L476) translates each
   index to the real `item_id` and returns the persisted `Mapping`.

7. **Enqueue → review items**
   ([`src/review_queue.py::enqueue_mapping(mapping, change,
   config)`](src/review_queue.py#L85)). Computes priority
   (confidence × 1+affected_count). Two-tier escalation gate at
   [`_escalation_threshold`](src/review_queue.py#L106): if any of
   the owning `Change`'s citations has `verified=False`, use
   `config.escalation.citation_threshold`; else
   `mapping_threshold`. If `mapping.confidence < threshold`,
   `ReviewItem.status='escalated'`; otherwise `'pending'`.

Every stage's output is persisted through its typed Store
singleton AND appended to `audit_log.jsonl` via
[`log_event`](src/audit_log.py#L50). The pipeline ends with
`logger.info('pipeline.done', ...)`.

A warm-cache run (every diskcache key present) is <1 second total —
the LLM calls return from
[`src/model_caller.py::ModelCaller.call`](src/model_caller.py#L81)
before dispatching to any backend.

---

## Q3. Small directed change I could make on the spot

**Example change**: raise `citation.min_citation_span_chars` from
10 to 20 so that short overlapping noise (e.g. "The aggregator")
doesn't ground as a real citation.

Where to make the edit:
[`config/default_domain_config.json`](config/default_domain_config.json)
→ `citation.min_citation_span_chars: 20`.

Where it takes effect:
[`src/citation/citation_grounding.py::_resolve`](src/citation/citation_grounding.py#L135)
reads `config.citation.min_citation_span_chars` and compares
against `len(citing_span)` — if short, sets
`verified=False, verification_rationale="span width N below
citation.min_citation_span_chars=20"`.

Where I'd prove it:
`env3.12/bin/pytest tests/test_pipeline.py::test_ground_citations_marks_unverified_when_span_below_min`
— the test already builds an `LlmCitation` with a 3-char
`citing_span='The'` and asserts verified=False with the rationale
substring. Raise the default and the assertion remains true (the
test's citing_span is 3 chars, below both 10 and 20).

Other small directed changes I could demonstrate:
- Flip `citation.verification_required` to `false` in a tenant
  override at
  `data/tenants/<tenant>/<env>/domain_config.json` — the
  `load_domain_config` deep-merge
  ([`src/config.py:L245`](src/config.py#L245)) composes it over
  defaults, pipeline skips the verify stage, logs
  `'verify.skipped'`.
- Add a new routing destination: three touches, all
  `grep`-connected. (a)
  [`AffectedItem.proposed_route`](src/config.py) adds the
  Literal value, (b) same for
  [`ExpectedAffectedItem.expected_route`](src/evals.py) in
  the gold schema, (c) [`src/mapping.py`](src/mapping.py)
  prompt rubric names it. The seed company_context would then get
  obligations/projects with `owner` on the new value.

---

## Q4. Where state lives, what validates and guards it, what happens on failure, where latency and cost go

### Where state lives

| State                       | Location                                                        | Lifecycle                                 |
|-----------------------------|-----------------------------------------------------------------|-------------------------------------------|
| Seed proceedings (per tenant) | `data/tenants/<tenant>/proceedings/*.txt` + `manifest.json`     | author-controlled; committed with repo    |
| Seed company context        | `data/tenants/<tenant>/company_context.json`                    | author-controlled; committed              |
| Pipeline outputs            | `data/tenants/<tenant>/<env>/{proceedings,obligations,projects,documents,changes,extracted_obligations,mappings,review_items}.json` | written by `run_pipeline`; overwritten on each run |
| Audit log                   | `data/tenants/<tenant>/<env>/audit_log.jsonl`                   | append-only JSONL; one line per `log_event` |
| LLM cache                   | `.model_cache/` via `diskcache`                                 | plaintext JSON keys; `make clean` deletes |
| Secrets                     | `.env` (OPENAI_API_KEY)                                         | `.gitignore`d; loaded via dotenv          |
| Domain config               | `config/default_domain_config.json` + per-tenant overrides      | author-controlled                         |

No database. v1 is pure filesystem. All v1 state is a function of
seed + config + a replay of the pipeline — nothing non-reproducible.

### What validates — the four defense layers

1. **Pydantic at every boundary.** Every model in
   [`src/config.py`](src/config.py) + the per-stage LLM response
   models use `ConfigDict(extra='forbid')` — unknown fields raise.
   Categorical fields use `Literal`, not bare `str`. No validators
   (CLAUDE.md invariant). No defaults on required fields — stale
   data is a rebuild signal, not a load-with-fallback.

2. **No silent fallbacks in internal code.** CLAUDE.md's "no .get()
   defaults" rule is enforced:
   [`src/model_caller.py`](src/model_caller.py#L117) uses direct
   `params['input']` and `params['inputs_dict']` — a missing key
   raises `KeyError` loudly rather than caching off `[]`. Every
   `.get()` in the codebase has either been removed or is a
   boundary input with an `# intentional default` comment naming
   the producer.

3. **Facade constraints.** Each `Store[T]` method takes
   `(tenant_id, environment, ...)` as leading params — tenant
   isolation is a signature-level invariant, not a convention.
   `ModelCaller.call` is the single chokepoint for every LLM
   dispatch — no backend-specific code anywhere else.

4. **Pipeline invariants** (from TDD §1 Key Properties, each
   enforceable with a `grep`):
   - Every claim carries a structural citation (passage_id +
     offsets) — enforced in grounding (§3.3), verified in §3.4.
   - Low-confidence interpretations escalate — enforced in
     `enqueue_mapping` (§3.7), threshold in `DomainConfig`
     (§4.3).
   - Draft vs final is a Literal on `ProceedingVersion`, not a
     classifier — asserted at ingest.
   - Data isolation per `(tenant_id, environment)` — every facade
     signature.

### What fires on failure

| Failure                              | Behaviour                                                                                   |
|--------------------------------------|---------------------------------------------------------------------------------------------|
| Unknown `passage_id` in LLM response | [`ValueError`](src/citation/citation_grounding.py#L135) — propagates; stops the run         |
| `citing_span` not found in passage   | `Citation.verified=False`, rationale "citing span not found" — downstream escalates         |
| `len(citing_span) < min_citation_span_chars` | `Citation.verified=False`, rationale names the config knob — downstream escalates   |
| LLM 429 / 5xx / connection error     | Exponential-backoff retry (3 attempts, 1s/2s/4s) in [`openai_backend`](src/backends/openai_backend.py); final failure re-raises |
| LLM structured-output refusal        | `RuntimeError` from [`openai_backend`](src/backends/openai_backend.py#L71) naming the refusal |
| LLM response truncated (`status='incomplete'`) | `RuntimeError` naming `max_tokens` + `incomplete_details`                        |
| `mapping.confidence < threshold`     | `ReviewItem.status='escalated'` on construction — the system fails to a human, not a guess   |
| Missing `.env` / `OPENAI_API_KEY`    | `openai.OpenAIError` from `OpenAI()` constructor on first LLM call — loud and clear          |
| Dry-run                              | `run_pipeline(dry_run=True)` still makes LLM calls (populates the cache) but writes nothing  |

### Where latency goes

End-to-end run at v1 cardinality is **7 LLM calls**:
- 1 version_diff
- 1 citation_verify (one batch call over all Changes)
- 2 obligation_extraction (one per version)
- 3 map_change (one per Change)

Cold cache: **~15s wall-clock** dominated by OpenAI latency. The
mapping call is the single longest contributor — it carries the
full company context (16 items at v1) and asks for a structured
`MappingResponse` with per-item (match_confidence, match_rationale,
proposed_action, proposed_route). Second-longest: version_diff with
both NOPR + Final text in the prompt.

Warm cache: **<1s**. diskcache `_cache.get(cache_key_json)` in
[`model_caller.py:L116`](src/model_caller.py#L116) short-circuits
before the backend is touched. Cache keys are plaintext JSON (a
sorted dict of model_identifier + max_tokens + reasoning_effort +
verbosity + response_type.__name__ + input + inputs_dict) —
greppable, renameable, copyable.

### Where cost goes

Pricing in
[`openai_backend.py:L63-L68`](src/backends/openai_backend.py#L63-L68):
`gpt-5.4-nano` at $0.00010 / 1k input tokens, $0.00040 / 1k output
tokens. A typical cold-cache end-to-end run:
- ~2500 input tokens × 7 calls ≈ 17.5k input ≈ $0.002
- ~700 output tokens × 7 calls ≈ 5k output ≈ $0.002
- **~$0.005 per cold-cache end-to-end run.**

Every call logs `openai_backend.call` with
`{task_name, model_name, input_tokens, output_tokens,
estimated_cost_usd}` — grep structlog JSON output to compute
exact spend per run. Cost is explicit per CLAUDE.md's "unit
economics" principle.

**Where cost would blow up at production scale.** v1's $0.01-per-run
math is a tiny-corpus number: one tenant, one subsection, 16-item
company context. A production deployment hits a different regime:
600-page regulator orders, 100+ proceedings per year per regulator,
10+ customers of the same regulator across multiple industries,
1000+ obligations per company. The aggregate mapping surface
becomes `N_tenants × M_changes × L_items` — plausibly O(100K)
candidate mappings per rule issuance across the customer base.
At gpt-5.4-nano pricing that's ~$100 per rule, not catastrophic in
isolation but non-linear in regulator count and tenant count, and
latency-bound regardless. [TDD §8.9](TDD.md#89-cross-tenant-amortisation--multi-tier-mapping-cull)
writes up the amortisation path (cross-tenant change-detection
store, keyed on `(regulator, proceeding, version_pair)`, so N
tenants share one detection pass; small supervised classifier for
first cull; Jev Noul for second cull; LLM only on the probable
matches). The architecture is designed for this expansion — the
`.model_cache` key already deduplicates across tenants with
identical prompts — but the cross-tenant store, classifier
training loop, and Jev tier are production work, not v1 scope.

---

## Q5. The AI part at mechanism level — four tasks, what each receives, what it emits, one thing it gets wrong

### version_diff

- **Receives**: prior proceeding text + new proceeding text,
  rendered under `## Inputs` as two `--- start / end ---`-delimited
  blocks. Rubric in `## Details` with [`CHANGE_TYPE_RUBRIC`](src/prompt_calibration.py) +
  [`CONFIDENCE_LEVEL_RUBRIC`](src/prompt_calibration.py). JSON-example
  shape teaches the model to emit `citing_span: str` (verbatim), NOT
  numeric offsets.
- **Emits**: `VersionDiffResponse.changes: tuple[LlmChange, ...]`
  (one per detected change). Each `LlmChange` has `change_type:
  Literal['added'|'removed'|'tightened'|'loosened'|'cosmetic']`,
  `description: str`, `prior_citation: LlmCitation | None`,
  `new_citation: LlmCitation | None`, `confidence: int (0-5)`,
  `confidence_rationale: str`.
- **Gets wrong (observed on gpt-5.4-nano, Order 2222)**: three
  failures in live `--confirm_llm` runs. (1) **Sub-modification
  over-splitting**: §(B)'s tightening of the registration duty
  (adding a 60-day timing requirement AND a point-of-interconnection
  content element to one existing obligation) was initially emitted
  as TWO separate `added` changes rather than ONE `tightened` change
  grouping two parameter sub-modifications — the `added` definition
  did not yet distinguish "wholly-new STANDALONE obligation" from
  "new parameter grafted onto existing obligation." Fixed by Example
  3 in `## Details` (hypothetical FinCEN SAR filing with two
  parameter sub-tightenings) + an explicit grouping rule bullet in
  the task rubric (commit `cadac6b`). (2) **`passage_id` string
  hallucination**: the LLM invented IDs like `"prior_A"` that did
  not exist in either version — grounding raised
  `ValueError: citation references unknown passage_id: prior_A`.
  Fixed structurally by switching `LlmCitation` from `passage_id:
  str` to `passage_index: int` so invented IDs become out-of-range
  errors (commit `1ecade7`). (3) **Whitespace-collapse inside
  `citing_span`**: nano routinely drops newlines mid-phrase (emits
  `"size requirement"` where the passage has `"size\nrequirement"`),
  breaking the deterministic `.find()` on 4 of 6 citation sides.
  Fixed structurally in `citation_grounding` by a whitespace-
  normalized retry that back-maps offsets to the original passage
  text (commit `7f3a77a`) — rather than prompt-engineering the issue
  away, since all small models exhibit this pattern.

### citation_verify

- **Receives**: for each `Change`, a rendered block with the
  change_type + description + both cited passages' text + the
  resolved citing span (the substring located by grounding — no
  offsets, no `passage_id` strings, since those are structural
  grounding artefacts the LLM can only misread; see commit `3254f73`).
  Citation sides whose grounding failed upstream render as a
  `GROUNDING_FAILED (do not verify this side — handled upstream)`
  marker so the LLM focuses only on sides that need its judgment.
- **Emits**: `CitationVerificationResponse.verifications:
  tuple[CitationVerificationItem, ...]` with
  `prior_citation_verified: bool | None` +
  `prior_citation_rationale: str | None` (same pair for `new_`).
  `None` when the Change has no citation on that side (added /
  removed cases).
- **Gets wrong (observed on gpt-5.4-nano, Order 2222)**:
  **laundering structural grounding failures into semantic
  verifications**. When upstream grounding failed (the whitespace-
  collapse case above produced an empty `citing_span` sentinel),
  the citation still flowed to the verifier with full passage text
  attached. The LLM would vote `verified=true` based on the passage
  alone — losing the signal that the structural anchor was missing.
  Fixed at the merge level by `is_grounding_pending` short-circuit:
  a citation whose rationale is the grounding-failure sentinel
  bypasses the LLM entirely and preserves the grounding verdict,
  and the prompt renders failed sides as a terse
  `GROUNDING_FAILED (do not verify this side — handled upstream)`
  marker so the LLM focuses only on sides that need its judgment
  (commits `21f0442`, `4d9969b`). Also preemptively guarded by
  Example 2 in `## Details` (right-paragraph-wrong-content): a span
  IN the right paragraph but describing different content than the
  described change must still verify as `false` (commit `021b638`).

### obligation_extraction

- **Receives**: one proceeding version's full passage text (all
  passages concatenated with `\n\n`). Rubric in `## Details`
  carries [`OBLIGATION_TAXONOMY_REFERENCE`](src/prompt_calibration.py)
  — the four `obligation_type` Literal values with descriptions
  and a worked example each. Plus `CONFIDENCE_LEVEL_RUBRIC`.
- **Emits**: `ObligationExtractionResponse.extracted_obligations:
  tuple[LlmExtractedObligation, ...]`. Each has `obligation_type:
  Literal`, `summary: str` (one-sentence), `full_text: str`
  (verbatim), `citation: LlmCitation(passage_index: int,
  citing_span: str)`, `confidence: int + rationale`. `passage_index`
  rather than `passage_id` prevents the ID-hallucination failure
  mode observed in `version_diff` (commit `1ecade7`).
- **Gets wrong (observed on gpt-5.4-nano, Order 2222 NOPR)**: two
  systemic patterns. (1) **Regulator-side duty extracted as entity
  obligation**: §(A)'s "The aggregator registration process shall
  not include a minimum size requirement" was extracted as an
  aggregator obligation at confidence=5 because the subject phrase
  surface-mentions "aggregator". The actual grammatical subject is
  a PROCESS administered by the RTO/ISO — the sentence constrains
  the RTO's rulemaking authority, not the aggregator's duties.
  Fixed by baking `REGULATED_ENTITY_PERSPECTIVE` into
  `OBLIGATION_TAXONOMY_REFERENCE` + Example 5 in `## Details`
  (hypothetical SEC broker-dealer registration process) teaching
  the "who ADMINISTERS the process named in the subject?" decision
  test (commits `bedb038`, `871a418`). (2) **Content-specification
  over-split**: §(B)'s "The aggregator shall register each DER…"
  + "The registration shall include physical location…" were
  extracted as TWO procedural obligations rather than ONE — the
  content-specification sentence has no independent compliance
  surface (violating it IS violating the SAME registration duty).
  Fixed by Example 6 in `## Details` (hypothetical FinCEN CTR
  filing with content specification) + a matching Pitfall
  explicitly naming the `"The X shall include Y, Z"` sentence
  pattern (commit `1090082`). After both fixes, the NOPR run
  extracts exactly two obligations — §(B) registration (procedural)
  + §(C) information-provision (procedural / reporting boundary,
  confidence=4 with the boundary ambiguity named in rationale).

### mapping

- **Receives**: one `Change` (description + change_type +
  confidence) + three enumerated candidate lists (obligations,
  projects, documents) with `[0] <id> (<type>, <status>): <summary>`
  prefixes. The lists are capped at
  `config.mapping.candidate_set_size` *before* rendering.
- **Emits**: `MappingResponse` with
  `affected_items: tuple[AffectedItemByIndex, ...]`
  (each is `{item_type, item_index, match_confidence,
  match_rationale, proposed_action, proposed_route}`),
  `confidence + rationale`, overall `rationale`. **Returns
  indexes, not IDs** — PROMPT_CONVENTIONS.md §5 (LLMs can't echo
  long opaque IDs reliably). **Per-item (action, route)** —
  no mapping-level `recommended_action` / `routing_destination`
  (TDD §8.6 closed in v1). One Change touching N diverse assets
  produces N (action, route) proposals; Rachel's `AssetDecision`
  curates each pair.
- **Gets wrong (observed on gpt-5.4-nano, Order 2222 §A tightened
  change)**: two residual failure patterns after four rounds of
  prompt tightening. (1) **Topical-adjacency over-mapping with
  hedge-shaped rationales**: for the size-eligibility change
  against Verdant Grid's company context, the mapper emitted two
  borderline items at `match_confidence=3` whose rationales led
  with hedge verbs — `"may require updating..."` on obligation
  DER-REG-01, `"should reflect..."` on document DER-OPS-PROC-v3.2.
  The hedge verb was the signal that the match was reaching for
  topical adjacency rather than naming substantive effect.
  Partially fixed by a Pitfall naming the specific hedge verbs
  (commit `56f9fca`); nano then swapped to `"must reflect"` /
  `"must update"` and bumped confidence to 4 — the behaviour
  pattern persists under a different wordlist. (2) **Multi-team
  routing without naming the secondary team**: overall confidence
  correctly dropped to 4 per the multi-team-touch Pitfall, but
  `confidence_rationale` said `"primary accountable function is
  legal because..."` without naming market_ops as the secondary
  team — Rachel has to re-derive the multi-team split from scratch.
  Partially fixed by a Pitfall requiring the explicit "primary X,
  secondary Y" shape (commit `bfae321`); nano then named primary by
  team name but described secondary by topic area
  (`"operational/program impact"` rather than `"market_ops"`). Both
  patterns are **nano-shaped failures** — the model games specific
  prompt stricture lists, swapping banned verbs for same-shape
  synonyms and swapping named-team requirements for topic-area
  descriptions. Deeper structural enforcement (constrained
  rationale templates, enum-restricted routing_rationale fields) is
  either whack-a-mole at this model size or requires a bigger
  model. The project's fail-closed architecture handles it:
  Rachel's queue triage is designed exactly for borderline items;
  nothing silently ships.

### Underlying mechanism

Every task goes through
[`src/model_caller.py::ModelCaller.call`](src/model_caller.py#L81)
which (a) loads the per-tenant+env `TaskModelConfig` to get
`model_identifier`, max_tokens, reasoning_effort, verbosity, (b)
dispatches by URL-prefix (`openai://`, `jev://`, `stub://`) via
`Backend.matches` — the dispatch pattern reserves the Jev seam
without wiring it in v1. (c) The OpenAI backend calls
`client.responses.parse(model=..., text_format=ResponseModel,
reasoning={'effort': ...}, text={'verbosity': ...})` — Pydantic
is the structured-output contract. (d) Retry logic wraps every
call: `RateLimitError` / `InternalServerError` /
`APIConnectionError` → exponential backoff (3 attempts). (e)
Successful parse goes through diskcache (plaintext JSON key).

---

## Q6. One real bug from the build

### Bug: the LLM was asked to compute character offsets

**Symptom**: `Citation.start_offset` and `Citation.end_offset` on
every `Change` and `ExtractedObligation` were produced directly by
the LLM. The TDD §3.3 had the correct design (LLM emits
`citing_span: str`, deterministic Python computes offsets via
`passage.text.find`), but the code regressed over several commits
to a shape where the LLM response's `Citation` subobject carried
the two integer offsets directly. Every `json_example` and rubric
in version_diff and obligation_extraction told the model:
`"start_offset": <int>, "end_offset": <int>` — a prompt that
induces exactly the behaviour LLMs are known to fail at.

### Wrong turns

- **It wasn't obvious from passing tests.** The stub backend
  produced plausible offsets (`start_offset=0, end_offset=200`)
  that matched passage lengths well enough for integration tests
  to assert `end_offset == passage_length` and pass. The autouse
  stub fixture masked the real-LLM behaviour entirely.
- **Caught during a doc-vs-code gap sweep**, not during normal
  development. Re-reading TDD §3.3 next to
  [`src/citation/citation_grounding.py`](src/citation/citation_grounding.py)
  exposed the regression — the doc described a substring-match
  step that the code didn't do.
- **First instinct was wrong**: add a secondary verifier that
  checks whether the LLM-emitted offsets actually overlap the
  claim's semantic content. This is the "paper over a bad
  interface" direction — adds complexity without removing the
  error-prone LLM request.
- **The right instinct**: the LLM can quote a passage verbatim
  (that's a copy, not a count). Have it emit the verbatim
  `citing_span` and let Python do `.find`. This moves the
  error-prone math out of the LLM.

### The fix (commit `807cd34`)

Introduced an **LLM-side shape** separate from the persisted
shape:

- **`src/citation/llm_citation.py`** (new, 30 lines) —
  `LlmCitation(passage_id: str, citing_span: str)` — no offsets.
- **`src/version_diff.py`** — new `LlmChange` mirrors `Change` but
  with `LlmCitation` fields. `VersionDiffResponse.changes:
  tuple[LlmChange, ...]`. JSON example rewritten to show
  `"citing_span": "<verbatim text ...>"`; rubric gained a direct
  prohibition: "Do NOT count characters or emit numeric offsets —
  grounding is done deterministically by Python after this call."
- **`src/obligation_extraction.py`** — same treatment:
  `LlmExtractedObligation` with `LlmCitation`.
- **[`src/citation/citation_grounding.py`](src/citation/citation_grounding.py#L80)**
  — complete rewrite. `ground_citations_for_changes` (for diff
  output) and `ground_obligations` (for extraction output) both
  call `_resolve(llm_citation, passages_by_id, min_span)` which
  does: validate passage_id exists; check `len(citing_span) >=
  min_span`; run `start = passage.text.find(citing_span)`; emit a
  persisted `Citation` with real offsets or a `verified=False`
  rationale.
- **Pipeline** — threaded through: `detect_changes` returns
  `LlmChanges`; `ground_citations_for_changes` converts to
  `Changes`; downstream (verify_citations, mapping, review_queue)
  consumes the real `Citation`.
- **`PROMPT_CONVENTIONS.md` §5.5** (new section) — "Never ask the
  LLM to emit character offsets or count characters". With a
  rationale, an anti-pattern, and the shape of the fix. Stops the
  next author from reintroducing the regression.

### Evidence the fix works

1. **New test**:
   `test_ground_citations_marks_unverified_when_citing_span_not_found`
   builds an `LlmCitation` with `citing_span="text that is
   definitely not in the passage"`, grounds, asserts
   `verified=False` with rationale containing `"not found"`. Before
   the fix this test couldn't exist — there was no `citing_span`
   field for the LLM to emit.
2. **Updated existing test**:
   `test_ground_citations_finds_substring_and_computes_offsets`
   builds an `LlmCitation` with a known 29-character `citing_span`
   = `"The aggregator shall register"`, grounds, asserts
   `start_offset=0` and `end_offset=29`. These numbers are
   computed by Python from the known passage text, not asserted on
   LLM output.
3. **End-to-end stub run**:
   `python -c "from src.pipeline import run_pipeline; ..."` under
   the autouse stub fixture produces `changes[*].prior_citation
   .start_offset=0, end_offset=200`. The offsets are real — the
   stub carves a 200-character `citing_span` from the passage,
   grounding does `.find`, offsets come back as 0 and 200.
4. **`grep` audit**:
   `grep -rn "start_offset\|end_offset" src/ scripts/` shows the
   terms only appear in `src/config.py` (the persisted schema),
   `src/citation/citation_grounding.py` (where offsets are
   *computed*), and `src/citation/citation_verify.py` (where
   grounded offsets are *read* to render the verifier prompt).
   Zero hits in `src/version_diff.py`, `src/obligation_extraction.py`,
   or their json_examples — the LLM is no longer asked for them.
5. **124 tests still pass** post-fix, same ruff-clean. The fix
   added one test, converted several existing tests to use
   `LlmCitation`, and didn't change downstream Pydantic schemas —
   every file that consumed `Change` / `ExtractedObligation`
   before still consumes them.

---

## Q7. What the tests and evals prove, and what they can't

### Tests (124 pytest cases)

**What they prove.**

- **Schemas don't drift.** Pydantic round-trip tests
  (`test_config.py`) assert every model's `model_dump(mode='json')
  → model_validate` is lossless and that Literal fields reject
  invalid values. The `extra='forbid'` ConfigDict means any new
  field added to a model fails tests until the tests catch up —
  stale data is a test failure, not a silent misparse.
- **Grounding math is right.**
  `test_ground_citations_finds_substring_and_computes_offsets`
  proves the offset computation given a known passage + known
  `citing_span`. The three failure paths (not-found,
  below-min-span, unknown-passage-id) each have a dedicated
  assertion.
- **Escalation thresholds fire correctly.**
  `test_enqueue_mapping_uses_citation_threshold_when_citation_unverified`
  + `test_enqueue_mapping_uses_mapping_threshold_when_citations_verified`
  prove the two-tier gate: unverified citations use the stricter
  `citation_threshold`; verified citations use `mapping_threshold`.
  Covers the TDD §3.7 Decisions block verbatim.
- **ModelCaller cache + retry.** Cache-key tests prove the key is
  plaintext JSON; retry tests mock
  `RateLimitError` / `InternalServerError` / `APIConnectionError`
  and prove the backoff behavior (one retry → success; four
  consecutive → final `RateLimitError` propagates; non-retryable
  `ValueError` doesn't retry).
- **Stores round-trip.** `test_stores.py` proves save → load
  returns the same models for both `JsonStore` and `JsonlStore`.
  Tenant-isolation tests prove no leak between
  `(tenant_a, prod)` and `(tenant_b, prod)`.
- **Pipeline end-to-end under stub.**
  `test_pipeline_end_to_end_produces_expected_counts` runs
  `run_pipeline('verdant_grid', 'prod')` and asserts the exact
  per-stage counts (2 versions, 8 obligations, 3 projects, 5
  documents, 3 changes, 2 extracted, 3 mappings, 3 review_items).
  A regression in any stage changes a count.
- **Review-queue actions.** `apply_asset_decisions` tests prove
  per-pair approve / override / dismiss / escalate transitions,
  bulk writes with a shared `batch_id`, `ReviewItem.status` folds
  from the AssetDecision set, and priority decays as decisions
  land. Each decision emits a matching `AuditEvent`
  (`asset_decision_approved` / `_overridden` / `_dismissed` /
  `_escalated`) with actor, rationale, prior+new value JSON, and
  the shared `batch_id` on bulk ops.
- **Web app routes.** `test_web_app.py` proves `/` 307-redirects,
  `/queue` and `/audit` render with expected content (including
  the `<mark>`-highlighted citation spans), POST /action
  303-redirects + persists the status change.

**What they can't prove.**

- **Real-LLM behaviour.** The autouse `stub_all_task_configs`
  fixture rewrites every `TaskModelConfig.model_identifier` to
  `stub://<task_name>`, so the OpenAI backend is never exercised.
  The stub returns a hand-written response that's shape-correct
  by construction. Tests prove the plumbing; they prove nothing
  about whether `gpt-5.4-nano` actually diffs regulatory text well,
  mis-maps obligations, or over-affirms citations. Only the eval
  harness under real LLM dispatch answers those.
- **Prompt quality.** The prompt-builder tests assert the rendered
  `PromptSpec` includes the right sections, that the json_example
  is present, that the rubric mentions specific phrases. They
  don't prove the prompt is *good* — "the LLM following this
  rubric produces outputs Rachel would accept".
- **Non-determinism tolerance.** Tests use the stub which is
  deterministic. Real-LLM runs have variance; the eval harness
  catches it over the gold set but no test asserts a confidence
  interval on repeat runs.
- **Latency / cost.** Tests run under 2 seconds total; nothing
  asserts an upper bound on wall-clock or token spend at real-LLM
  scale.

### Evals (gold set + harness)

**What they prove.**

The eval harness
([`src/evals.py`](src/evals.py) +
[`scripts/main_eval.py`](scripts/main_eval.py)) runs the full
pipeline against `data/evals/gold.json` and scores every stage:

- **Change detection**: precision / recall / F1 by matching
  predicted `Change`s to `ExpectedChange`s on `(change_type,
  prior_passage_position, new_passage_position)` — the gold labels
  carry positions, not full citations, so the match is on
  structural location + type.
- **Citation precision**: the fraction of expected `(change_type,
  prior_pos, new_pos)` triples where the predicted Change's
  `prior_citation.verified` and `new_citation.verified` are both
  True.
- **Verification accuracy**: `score_verifications` compares
  predicted `verified` against `expected_verifications[*].{prior_
  verified,new_verified}`.
- **Obligation extraction**: `score_extractions` matches by
  `(version_id, obligation_type, passage_position)` and reports
  type-correctness.
- **Mapping**: `score_mappings` joins predicted and expected
  affected items on `(item_type, item_id)` and scores per-item
  precision/recall, plus per-item `proposed_action` and
  `proposed_route` accuracy (expected labels live on
  `ExpectedAffectedItem` in `src/evals.py`).
- **Confidence calibration**: `aggregate` produces a reliability
  diagram — predicted-confidence buckets vs. actual precision —
  which is the direct measurement of the PRD §4
  "high-confidence precision ≥ 95%" claim.

Results land in `output/evals/<YYYYMMDD-HHMMSS>.json` and (with
`--md_output` / `make eval-report`) a committed
`docs/eval-results/eval-results-<date>.md` — the submission's
trust-metric evidence.

**What they can't prove.**

- **The gold set is representative.** v1 ships **one** case (FERC
  Order 2222 §35.28(g)(1)(ix)) with 16 aggregate labels. The
  precision/recall numbers against this single case say nothing
  about other FERC rules, other regulators, or other industries.
  Rachel-persona plausibility is a PRD §7 open question.
- **The gold labels are correct.** The author labelled them as a
  Rachel-proxy, not an actual regulatory-affairs manager. Expert
  calibration (TDD §8.4) is deferred.
- **Confidence beyond self-report.** The reliability diagram shows
  where self-reported confidence miscalibrates, but the fix (Jev
  Noul migration for discrimination tasks) is TDD §8.1 future
  work. v1 can measure the problem; it can't fix it.
- **Live reviewer behaviour.** Nothing in the eval harness
  simulates Rachel's accept/override pattern. "Share of changes
  auto-mapped without override" (PRD §4) is observable only in
  production.
- **Workflow-engine semantics.** The gold set tests "which items
  does the mapper flag"; it doesn't test "did routing to IT &
  Metering unblock downstream work in time". That requires a real
  Rachel, a real timeline, and a real cost of delay — none of
  which fit in a build-challenge submission.

### Summary of claims vs. evidence

| Claim                                                    | Proven by                                 | Not proven                                             |
|----------------------------------------------------------|-------------------------------------------|--------------------------------------------------------|
| Pipeline shape + counts stable                           | 124 pytest cases, stub backend            | Real-LLM output quality                                |
| Grounding math + failure modes correct                   | `test_ground_citations_*`                 | How often LLM produces an unfindable `citing_span`     |
| Escalation gate semantics correct                        | `test_enqueue_mapping_*`                  | Threshold defaults are empirically optimal             |
| ModelCaller caches / retries / fails loudly              | `test_model_caller.py`, `test_openai_backend.py` | Real OpenAI 429 / 5xx distributions             |
| Review queue state transitions correct (per-pair)        | `test_apply_asset_decisions_*`            | Rachel's actual decision-making                        |
| Trust metrics (citation precision ≥ 98%, mapping ≥ 90%)  | Eval harness over gold set (when run with a real API key) | Generalisation beyond this one subsection    |
| Confidence calibration                                   | Reliability diagram (visual)              | Prod-quality calibration (needs Jev or fine-tuning)    |
