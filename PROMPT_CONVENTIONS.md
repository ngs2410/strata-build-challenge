# Prompt Authoring Conventions

Conventions for authoring LLM prompts in this codebase. Applies to all prompt builders across `src/ingest/`, `src/diff/`, `src/citation/`, `src/extraction/`, `src/mapping/`, and `src/workflow/`.

Read this before writing or substantially editing any `PromptSpec`. The first draft of a new prompt is almost always wrong in at least one of the ways catalogued here.

---

## 1. Module layout

Each LLM task gets its own module containing the trio: **prompt builder**, **response Pydantic model**, **`CallSpec`**. Nothing else in that trio goes in orchestrator modules.

- Workflow-stage LLM tasks → the matching stage directory (`src/extraction/obligation_extraction.py`, `src/mapping/change_to_obligation.py`, `src/citation/citation_verify.py`, `src/diff/draft_vs_final.py`, etc.)
- Shared calibration text → programmatically built from a data source (see §10), not hand-maintained strings

Then wire the task into `src/prompt_registry.py` and add a `TaskModelConfig` entry under `task_model_configs` in `config/default_domain_config.json`.

---

## 2. The `PromptSpec` infrastructure (`src/llm.py`)

```python
@dataclass(frozen=True)
class PromptSpec:
  system_prompt: str      # system message
  task: str               # one-paragraph goal
  steps: str              # numbered procedure
  details: str            # calibration — taxonomies, worked examples, disambiguation
  rubrics: str            # acceptance criteria for quality
  pitfalls: str           # explicit anti-patterns
  json_example: str       # pretty-printed JSON skeleton with placeholders
  field_intents: str      # per-field semantics
  inputs_dict: dict       # the actual input DATA (never put these in details)
```

`render()` produces the user message with sections in this exact order:

```
## Task → ## Steps → ## Details → ## Rubrics → ## Pitfalls
→ ## Field Intents → ## JSON Output Example → ## Inputs
```

**`## Inputs` is deliberately last** to maximise OpenAI's prompt-prefix caching. Everything from `## Task` through `## JSON Output Example` must be byte-identical across every call of the same task — identical system_prompt, identical task description, identical rubrics, identical JSON example. Only the Inputs block varies per call. On the second+ call within the cache window, the stable prefix (typically ~90% of the input tokens for a Strata task) is cached and billed at the lower cached-input rate. Any content that varies per call MUST live inside `inputs_dict`; anything that would make the prefix drift between calls (per-run timestamps, per-tenant branding, randomised preamble, etc.) is a cost regression.

### ⚠ The `included_inputs=[]` trap

`_render_inputs` treats an **empty list** as "render ALL keys in `inputs_dict`", not "render none". Only a **non-empty list** filters. So:

- `included_inputs=[]` (the default for every task in `default_task_model_configs.json`) → every key in `inputs_dict` is rendered under `## Inputs`.
- `included_inputs=['a', 'b']` → only those keys are rendered.

**Do not manually paste input text into `details` to control rendering** — it will show up twice (once in `## Details`, once in `## Inputs`). This is the #1 authoring error in this codebase.

---

## 3. Writing each section

### `system_prompt`
2–4 sentences. Name the role, the pipeline position, and the **downstream cost of mistakes**. `obligation_extraction` is the exemplar:

> "You are an obligation extractor in a regulatory change-intelligence pipeline. You receive a passage from a regulatory filing and the surrounding version context. Your extracted obligation feeds directly into change-to-obligation mapping (which uses the obligation type to route the detected change to the right company obligation) and into reviewer routing (which uses the obligation's severity to prioritize review). A wrong obligation type biases every downstream mapping; a missed obligation means a material change never reaches a reviewer. You produce structured JSON output."

End with `"You produce structured JSON output."` — this is a near-universal closing line.

### `task`
1–3 sentences. States the goal and what "correct" looks like at a structural (not procedural) level. Don't restate steps here.

### `steps`
Numbered procedure, usually 4–8 top-level steps with optional sub-steps (`1a`, `5b`). Each step describes a discrete action. Steps are *procedural*; save calibration for `details`.

### `details` — **this is where inputs DO NOT go, and rubrics DO NOT go**

`details` is for **task elaboration** — procedural guidance for how to perform the task that doesn't belong in `steps` (not ordered enough) but also isn't a scoring rubric. Think disambiguation precedence rules, worked examples, decision procedures for confusable categories. If the content is a *rubric* — a labelled list of values the model picks from (change types, confidence levels, obligation types, routing destinations) — it belongs in `rubrics`, NOT in `details`.

Legitimate `details` content:

- Disambiguation precedence rules (`src/ingest/proceeding_ingest.py`'s "docket metadata > order caption > filing cover sheet")
- Decision procedures for confusable judgment calls ("REPORTING vs PROCEDURAL: does the obligation require a submission from the company, or does it govern the process by which an existing obligation must be executed?") — note this is a *procedure*, not a rubric; rubrics are the labelled value definitions themselves
- **Worked examples** — the single most effective calibration technique. For smaller prompts, a 3–4 line `Example N — <verdict>:` block per major judgment axis is appropriate.

Explicitly NOT details content:

- **Rubrics** (`CHANGE_TYPE_RUBRIC`, `CONFIDENCE_LEVEL_RUBRIC`, `OBLIGATION_TAXONOMY_REFERENCE`) — these go in `rubrics`. The whole point of the Rubrics section is that it's where all per-value / per-level definitions live, so the model has one canonical place to look.
- **Input data** (the text to be classified, candidate lists, etc.) — that belongs in `inputs_dict`.

Many prompts have no `details` content at all — if the task has no procedure beyond the rubric content, leave `details=None` (the default). The three live tasks `version_diff`, `obligation_extraction`, and `mapping` all currently ship without a `details=` arg because every scoring artifact is a rubric and goes in `rubrics`.

### `rubrics`
Everything the model picks a value from, plus quality acceptance criteria. Two kinds of content both belong here:

1. **Per-value / per-level definitions** — the `CHANGE_TYPE_RUBRIC` (five values with descriptions), `CONFIDENCE_LEVEL_RUBRIC` (six levels 0-5 with descriptions), `OBLIGATION_TAXONOMY_REFERENCE` (four obligation types with descriptions + examples). Build them programmatically from a data source (see §10) so the Literal types and prompt text never drift. Compose them into `rubrics` with blank-line separators.
2. **Additional rules** — a bulleted checklist of quality acceptance criteria. Phrased as positive statements: "Each concept must be a self-contained structural unit." Describes what "good" output looks like.

Convention when both are present: rubric content first, then a blank line + `Additional rules:` + the bulleted checklist. Bullets that reference a rubric say "per the X rubric above" (same section) — never "per the X rubric in Details" (that would be a §3-level mistake).

**Scale with task complexity, not task importance.** A richly nuanced judgment (segmentation, role classification, feature extraction, decision record origination) often needs 8–15 rubric bullets to cover the quality dimensions. A simple binary or categorical pick (record matching, narrative form classification) may need only 2–4 — trying to pad it to 10 produces vacuous or repetitive items that dilute the signal. Write only what adds real calibration; a tight 3-rubric list on a simple task is better than 12 diluted ones.

### `pitfalls`
Explicit anti-patterns. Phrased negatively: "Do not split multi-clause dynamics into separate segments." Describes observed failure modes.

**Scales the same way as rubrics.** Complex judgments accumulate 10–20 observed pitfalls; simple tasks may only have 2–5 genuinely distinct failure modes worth naming. Every pitfall must correspond to a mistake the model actually makes on this task — not a generic "be careful." If you can't state a concrete failure you've observed (or reasonably expect), don't add the item.

Rubrics + pitfalls together = "good looks like X; here are N specific ways models get it wrong." N is determined by the judgment's complexity, not by a fixed quota.

### `field_intents`
Per-field semantic documentation. Format:

```
- field_name: what the field means + where the value comes from
  + any vocabulary rules or required patterns
- other_field: ...
```

One bullet per output field. For each field, explain **what it captures** and **the rules the model must follow** (vocabulary constraints, formatting rules, precedence when multiple sources apply). The field name should match the Pydantic response model exactly.

### `json_example` — see §4 (most error-prone section)

### `inputs_dict`
Maps input key → input value. Every key must:
1. Exist in `INPUT_LABELS` in `src/llm.py` (adding new keys requires updating that registry — see §8).
2. Correspond to a parameter of the builder function.

Values are strings; empty strings and `None` are automatically skipped during rendering. If you need to build a composite value (e.g. formatted record summaries), do the formatting in the builder and pass the resulting string — do not put formatting logic elsewhere.

**Wrap multi-line prose inputs in explicit delimiters.** Whenever an input value is free-form prose that may span multiple paragraphs (conversation text, thread prose, article body, document excerpt), wrap it in the builder with `--- start of <name> ---` / `--- end of <name> ---` markers. Without delimiters, when several inputs render back-to-back under `## Inputs`, the boundary of a multi-line prose value blurs into the next input's label or the next section header, and long prose in the middle of a prompt effectively gets "lost" — the model skips over it or merges it with neighbouring content.

Example (from `src/extraction/obligation_extraction.py`):
```python
'passage_text': (
  '--- start of passage ---\n'
  + passage_text
  + '\n--- end of passage ---'
),
```

Used consistently across `src/extraction/obligation_extraction.py` (`passage_text`, `surrounding_context`), `src/diff/version_diff.py` (`prior_version_text`, `new_version_text`), `src/mapping/change_to_obligation.py` (`change_summary`), and `src/citation/citation_verify.py` (`cited_passage`). Apply to any input that contains newlines. Short scalar values (obligation types, IDs, dates, single-phrase summaries) don't need wrapping.

---

## 4. JSON example conventions (critical — where most first drafts fail)

The `json_example` is a **template**, not a realistic grounded example. Its purpose is to teach the model the output **shape and placeholder format** — not to prime it with plausible content. Real content risks priming the model to fabricate similar-looking values.

### Rules

**Always pretty-print.** Every existing `json_example` uses newlines + 2-space indentation. Never single-line.

**Use `<...>` placeholders for all values the model generates.** Never use concrete realistic values in the example (no `"dr-042"`, no `0.87`, no full reasoning sentences). Use descriptive placeholder text inside angle brackets.

**Type-specific placeholder conventions:**

| Type | Placeholder | Example |
|------|-------------|---------|
| String (free text) | `"<description of what goes here>"` | `"<human-readable title>"` |
| Integer | `<int>` | `<int>` |
| Integer index | `<int 0-based index into ...>` | `<int 0-based index into candidates>` |
| Float with range | `<float 0.0-1.0>` | `<float 0.0-1.0>` |
| Boolean (model chooses) | `<true\|false>` | `<true\|false>` |
| Enum of strings (`Literal[...]`) | `"<a\|b\|c>"` | `"<prose\|conversational>"` |
| Nullable int/float/bool | unquoted, include `\|null` in the placeholder | `<int\|null>`, `<int 0-based index, or null if no match>` |
| Nullable string | keep quoted, mention null in the description | `"<verbatim span from predecessor, or null>"` |

**Only strings and string-valued enums are quoted.** In valid JSON, `"confidence": 0.87` is a float and `"active": true` is a boolean — the values are unquoted. The placeholder must match the JSON type the model will actually emit. Quoting a non-string placeholder (`"<int>"`, `"<float 0.0-1.0>"`, `"<true|false>"`) teaches the model that the field's value is a string literal, which is wrong. Structured-output coercion usually saves us, but the example should not lie about the type.

Nullable strings are the one subtlety: when the non-null value is a string, keep the placeholder quoted and mention `null` in the description (`"<verbatim span from predecessor, or null>"`). When the non-null value is an int/float/bool, unquote and use `|null` (`<int|null>`).

**When to use literal values instead of placeholders:** when the LLM should always output that exact value at this stage because the field is an invariant at this step (e.g. `src/extraction/obligation_extraction.py` uses `"obligation_id": null` and `"verified": false` because obligation resolution and citation verification both happen in later stages — the LLM must always emit those exact values here). The rule: placeholder when the model is choosing, literal when the value is invariant at this stage.

**Lists:** show one element in the array template with its full object shape expanded. Don't show `[...]` or empty arrays unless empty is the invariant expected value.

**Nested objects:** expand fully. Never elide nested structure with `{}` or `...`.

**"Also valid when ..." secondary examples:** use sparingly, only when showing a minimal-form variant genuinely clarifies a conditional output shape. `obligation_extraction` (no-predecessor-version variant) and `change_summary` (`"delta": null` variant for a newly-added obligation) are the two precedents. Do **not** use this to show both branches of a categorical output — a single placeholder with a descriptive note handles that in one example.

### Past mistakes (don't repeat)

1. **Grounded realistic examples** — first draft of `obligation_match` used `"matched_obligation_id": "OBL-042"` and a full reasoning sentence about a specific California PUC docket. Wrong: the model will pattern-match the real-looking values and fabricate similar ones. Fix: use descriptive placeholders.
2. **Single-line JSON** — compresses the example and makes nested shape hard to read. All examples must be pretty-printed.
3. **Quoted numeric / boolean placeholders** — never write `"confidence": "<float 0.0-1.0>"` or `"active": "<true|false>"`. Those placeholders teach the model that the value is a string literal. Use `<float 0.0-1.0>` and `<true|false>` unquoted. Same rule applies to `<int>` and `<int|null>`.

---

## 5. Reference list items by 0-based integer index, not by opaque ID

When the LLM needs to pick one or more items from a finite list passed in as input, have it emit the **0-based integer index** of the chosen item(s), not the item's name or ID. The caller then translates the index back to the real identifier.

### Why

LLMs are error-prone when asked to reproduce long opaque strings (`dr-abc123def456`, `c_9f8a7b6e`, UUIDs, ticket IDs, hashed keys). They substitute characters, truncate, hallucinate plausible-looking but wrong IDs, or merge IDs across items. Integer indexes into a short list are small, ordered, and impossible to confuse — the model only needs to count.

`src/mapping/change_to_obligation.py` is the exemplar: the mapping prompt renders candidate company obligations with numeric ids (`id: int`) and the response model `MappedObligationResult.id: int` references those positions. The caller owns the index → obligation mapping.

### When this applies

- Mapping a detected change to a list of candidate company obligations → emit the obligation's list index, not its `obligation_id`.
- Routing a proposed action to one of a candidate reviewer list → emit the reviewer's list index.
- Selecting the passage(s) that support an extracted claim from a short candidate set → emit indexes into the passage list.
- Picking an option from a multi-choice prompt → emit the option's index (A/B/C labels also work, but integer indexes compose better with arrays).
- Any "choose from this short list I just showed you" pattern.

### How to structure the prompt

1. In the builder, render the list with explicit 0-based indexes as the visible label:
   ```
   [0] Monthly RPS compliance filing (type: reporting, status: active)
   [1] Interconnection application processing SLA (type: procedural, status: active)
   [2] Customer outage notification protocol (type: operational, status: pending)
   ```
   Include whatever summary fields the model needs to make the choice *after* the index.

2. In the response model, use `int` (or `int | None` for optional selection, `list[int]` for multiple):
   ```python
   class MatchResult(BaseModel):
     model_config = ConfigDict(extra='forbid')
     matched_index: int | None   # 0-based; None if no match
     confidence: float
     reasoning: str
   ```

3. In `field_intents`, document the index semantics explicitly:
   ```
   - matched_index: 0-based index of the matched item in the candidate list
     shown in the Inputs section, or null if no candidate describes the same
     decision. Counts from 0. Must be a valid index into the list.
   ```

4. In `json_example`, use the integer-index placeholder convention (unquoted — the value is an int, not a string):
   ```
   "matched_index": <int 0-based index into candidates, or null if no match>
   ```

5. In the caller, translate the index back to the real identifier before returning/logging:
   ```python
   matched_id = (
     candidate_obligations[result.matched_index]['obligation_id']
     if result.matched_index is not None
     else None
   )
   ```

### Practical caps

Keep the list short — typically < 20 items. If the list is longer, pre-filter (keyword overlap, embedding similarity) before the LLM call. The LLM's reliability at picking the right index degrades with list length, and long lists also bloat the prompt.

### Anti-pattern

Asking the model to emit a `matched_obligation_id: str` where the string is expected to exactly match an ID from the candidate list. Even with the candidate IDs visible in the prompt, the model will occasionally truncate (`OBL-042` → `OBL-42`), transpose characters, or hallucinate a plausible-looking ID that doesn't exist. This is a real failure mode, not a theoretical one.

---

## 5.5 Never ask the LLM to emit character offsets or count characters

Same spirit as §5 (don't ask the model to do indexing work), applied to character-level positions in text. If downstream code needs `start_offset: int` and `end_offset: int` into a passage, have the LLM emit the **verbatim substring** (`citing_span: str`) and let deterministic Python compute offsets with `passage.text.find(citing_span)`.

### Why

LLMs cannot count characters reliably. Asking the model to emit numeric offsets into a multi-hundred-character passage produces:
- Plausible-looking but wrong offsets that drift by 1–10 characters.
- Offsets that truncate the span mid-word.
- Fabricated numbers that happen to land in the right ballpark but miss the actual supporting text.

A verbatim-copy task is a strict superset of what the LLM can do reliably (quote from a passage), while leaving the error-prone arithmetic to Python's `str.find`. If the LLM paraphrases or summarises instead of copying verbatim, `str.find` returns -1 — a loud failure signal — rather than silently producing a wrong offset.

### How to structure the prompt

1. In the response model, use a dedicated LLM-side type with the string field:
   ```python
   class LlmCitation(BaseModel):
     model_config = ConfigDict(extra='forbid')
     passage_id: str
     citing_span: str   # verbatim; grounding computes offsets
   ```

2. In `rubrics` or `pitfalls`, tell the model explicitly:
   > `citing_span` is the VERBATIM text copied character-for-character from the cited passage. Do NOT paraphrase, summarise, or truncate mid-word. Do NOT count characters or emit numeric offsets — grounding is done deterministically by Python after this call.

3. In the deterministic grounding step, run `start = passage.text.find(citing_span)`. If `start < 0` or `len(citing_span) < min_citation_span_chars`, mark the citation unverified with a specific rationale. If match, compute `end = start + len(citing_span)`.

4. Keep the persisted / domain `Citation` type with real `start_offset: int` / `end_offset: int` — those are correct downstream; grounding is just the bridge from LLM-shape to domain-shape.

### Anti-pattern

Asking the model to emit `"start_offset": <int>, "end_offset": <int>` directly on a `Citation` field. Even with the passage text in context, the model will produce offsets that look reasonable but are off by a few characters, point to the wrong word boundary, or fabricate wholesale. This is a real correctness bug — the Strata build had this pattern earlier and regressed; see commit history for the fix. For every claim-with-source pattern (citation grounding, obligation extraction, change detection, excerpt highlighting), use `citing_span: str` + deterministic `find`.

### When this applies

- Citation grounding: LLM says "this passage supports the claim"; LLM emits `citing_span`, grounding finds offsets.
- Obligation extraction: LLM extracts an obligation whose text lives inside a passage; same pattern.
- Change detection: LLM identifies the text span that changed between versions; same pattern.
- Any extraction task where the LLM output must point to a precise span inside an input text — never offsets, always verbatim-span-plus-deterministic-find.

---

## 6. Pydantic response model conventions

```python
from pydantic import BaseModel, ConfigDict
from typing import Literal

class MyResult(BaseModel):
  model_config = ConfigDict(extra='forbid')  # always
  category: Literal['a', 'b', 'c']           # never bare str for enums
  count: int
  reason: str
  optional_field: str | None                 # nullable only where absence is meaningful
```

**Rules:**
- Always `model_config = ConfigDict(extra='forbid')`. Strict schema validation is non-negotiable.
- Categorical fields **must** use `Literal[...]`, never bare `str`. This is enforced by CLAUDE.md.
- **Never use field or model validators** (`@field_validator`, `@model_validator`). Rely on Pydantic's native types. This is enforced by CLAUDE.md.
- **Never add field defaults** for backward compatibility (`= None`, `= []`, `= 0`). If deserialization fails on old data, rebuild the data — don't weaken the schema. This is enforced by CLAUDE.md.
- Nullable (`T | None`) only where absence is semantically meaningful (e.g. `unmapped_obligation: UnmappedObligation | None` when the detected change does not fit any obligation in the taxonomy; `delta: DeltaFeatures | None` when no predecessor version exists for this proceeding). Not as a convenience.
- Nest freely. Use nested `BaseModel` classes rather than loose `dict` shapes (`severity: ChangeSeverity`, not `severity: dict`).
- Prefer `int` over `str` when the field is an index into a list (see §5).

---

## 7. `CallSpec` conventions

```python
MY_TASK_SPEC = CallSpec(
  purpose=(
    '<what the call produces and its role in the pipeline>'
  ),
  acceptance_criteria=(
    '<2-4 concrete, measurable dimensions of a good output>'
  ),
  failure_modes=(
    '<3-6 concrete, observable mistakes — not abstract warnings>'
  ),
)
```

Three fields only, all strings:

- **purpose** — pipeline role + downstream impact. Frames *why* the call exists.
- **acceptance_criteria** — what makes an output good. Reference concrete dimensions ("vocabulary overlap", "cites source evidence", "correct granularity") — not abstract goals.
- **failure_modes** — named, observable mistakes. "Matching on keyword overlap instead of decision identity" — not "poor accuracy."

`CallSpec`s are used by evaluation and prompt-tuning infrastructure. Precise, concrete language pays off there more than anywhere else in the prompt.

---

## 8. `INPUT_LABELS` registration

Every key used in any `inputs_dict` must appear in `INPUT_LABELS` in `src/llm.py`. The label is the human-readable prefix used when the input is rendered (`Label: <value>`).

When adding a new input key, add it to `INPUT_LABELS` with a descriptive label (proper capitalization, short noun phrase). Forgetting this step will cause a `KeyError` at render time.

---

## 9. Registration and config wiring

### Register in `src/prompt_registry.py`

```python
'my_task': PromptEntry(
    builder_module='src.my_module',
    builder_function='build_my_prompt',
    response_module='src.my_module',
    response_class='MyResult',
    eval_cases_path='evals/my_task_eval.json',
    task_config_key='my_task',
),
```

The registry key = the `task_name` used when calling `model_caller.call(task_name=...)` and the key in `default_task_model_configs.json`. Keep these three identical.

### Add to `task_model_configs` in `config/default_domain_config.json`

```json
"my_task": {
  "backend_type": "llm",
  "model_identifier": "gpt-5.4-nano",
  "max_tokens": 2048,
  "reasoning_effort": "low",
  "verbosity": "low",
  "included_inputs": []
}
```

`included_inputs: []` is the default; keep it unless you genuinely need to filter inputs (overwhelmingly rare — most tasks render every key in `inputs_dict`).

Token budget guidance for nuanced vs. simple tasks:
- `obligation_extraction` and `change_summary` → `max_tokens >= 4096` (reasoning exhaustion on multi-dimensional classification)
- `change_to_obligation` (candidate-list mapping) → `reasoning_effort: 'low'` with `max_tokens=8192` (long candidate lists + rationale)
- Short-label tasks (`action_recommender`, `escalation_tag`, `draft_vs_final`) → `max_tokens >= 512`
- Cheap/fast tasks (`citation_verify`, `obligation_match`, metadata extraction) → `nano` model with `reasoning_effort: 'low'`

### Caller boilerplate

```python
spec = build_my_prompt(...)
system, user = spec.render(included_inputs=task_config.included_inputs)
messages = [
  {'role': 'system', 'content': system},
  {'role': 'user', 'content': user},
]
result = model_caller.call(
  'my_task',
  task_config,
  {'input': messages, 'inputs_dict': spec.inputs_dict},
  MyResult,
  MY_TASK_SPEC,
  tenant_id,
)
```

Pass `task_config.included_inputs` through to `render()`. Always pass `inputs_dict` to `model_caller.call` alongside `input` — the cache and telemetry layers use it.

---

## 10. Shared calibration constants

When the same taxonomy or reference text appears in multiple prompts, **build it programmatically from a data source** and import it. Never copy-paste.

Exemplars:
- `OBLIGATION_TYPES` (dict) → `OBLIGATION_TAXONOMY_REFERENCE` (string, built by `_build_obligation_taxonomy()`) — imported by `obligation_extraction`, `change_to_obligation`, and `action_recommender`.
- `CHANGE_SEVERITY_LEVELS` → `CHANGE_SEVERITY_REFERENCE` — imported by `version_diff`, `change_summary`, and `confidence_scoring`.
- `JURISDICTION_DISAMBIGUATION` — shared across `proceeding_ingest` and `change_to_obligation`.

Benefits: Literal types and calibration text never drift. Adding a new obligation type once updates everywhere.

If two prompts need the *same* calibration, extract a constant. If they need *related* calibration, share the data source but format per-prompt.

---

## 11. Testing conventions

See TEST_QUALITY.md for the authoritative rules. Prompt-specific patterns:

1. **Taxonomy integrity** — count check + presence of required fields on each entry (e.g. "every obligation type has `description` and ≥3 `examples`").
2. **Response model roundtrip** — `model_dump()` → re-construct → assert equivalence. Catches accidental field-schema changes.
3. **Mocked call chain** — use `make_model_caller([fixture])` to inject a pre-built response, then assert the builder + render + call + parse pipeline produces the expected result.
4. **Prompt builder test** — construct a spec with representative inputs, call `spec.render(included_inputs=[])`, assert:
   - Presence of key input values in the rendered `user` message (confirms inputs flow through).
   - No duplicate rendering of any input string.
   - `system` contains role-framing language.
5. **Quality invariants** — e.g. `test_obligation_type_jurisdiction_free` audits fixture data to enforce "no jurisdiction-specific vocabulary leaks into generic obligation-type fields." Write one of these for any rule that would be catastrophic to violate in production.

Test file naming is simple — examples: `test_version_diff.py`, `test_obligation_extraction.py`, `test_change_to_obligation.py`.

---

## 12. Authoring checklist

When adding a new LLM task, work through this list in order:

### Design
- [ ] Chosen module location (the matching workflow-stage directory under `src/`).
- [ ] Identified all required inputs and their labels.
- [ ] Drafted the response model with `Literal` types for every categorical field.
- [ ] For any "pick item(s) from this list" field, used `int` index rather than `str` ID (§5).
- [ ] Confirmed no existing prompt already does this (grep for similar CallSpec `purpose` text).

### Module contents (the trio)
- [ ] Response model with `ConfigDict(extra='forbid')`, `Literal[...]` for enums, no validators, no defaults.
- [ ] `CallSpec` with concrete `acceptance_criteria` and `failure_modes`.
- [ ] Prompt builder function returning `PromptSpec`.

### `PromptSpec` content review
- [ ] `system_prompt` names the role and downstream cost of mistakes.
- [ ] `task` is 1–3 sentences, structural not procedural.
- [ ] `steps` is numbered, 4–8 top-level steps.
- [ ] `details` contains **calibration only** — no input data. Worked examples if judgment is nuanced.
- [ ] `rubrics` is a bulleted checklist of acceptance criteria.
- [ ] `pitfalls` names concrete anti-patterns ("Do not ...").
- [ ] `field_intents` has one bullet per response-model field.
- [ ] `json_example` is pretty-printed, uses `<...>` placeholders (never real values), with correct type conventions (§4). Literals only where invariant at this stage.
- [ ] `inputs_dict` contains every input, and nothing appears there AND in `details`.
- [ ] Any list-selection fields in the input are rendered with explicit `[0]`, `[1]`, ... labels (§5).

### Wiring
- [ ] Every key in `inputs_dict` has an entry in `INPUT_LABELS` (`src/llm.py`).
- [ ] Registered in `src/prompt_registry.py` with matching key/module/class names.
- [ ] Entry added under `task_model_configs` in `config/default_domain_config.json` with sensible `max_tokens` / `reasoning_effort` (see §9 guidance).
- [ ] Caller uses the §9 boilerplate: `spec.render(included_inputs=task_config.included_inputs)` and passes `inputs_dict` to `model_caller.call`.
- [ ] For list-index responses, the caller translates the returned index back to the real identifier before downstream use (§5).

### Verification
- [ ] Render a sample spec and visually inspect the full user message: section headings in the §2 order, no input duplication, pretty-printed JSON example, all placeholders.
- [ ] Response model tests: roundtrip, enum validation, fixture building.
- [ ] Mocked call chain test.
- [ ] Taxonomy integrity test if the prompt references a taxonomy.
- [ ] `ruff check src/<module>.py` clean.

---

## 13. Known failure modes (and the real prompts they came from)

| Failure | What happens | Fix |
|---|---|---|
| Input text placed in `details` | Inputs rendered twice (once in `## Details`, once in `## Inputs`) because `included_inputs=[]` renders ALL keys | Keep inputs in `inputs_dict` only; put calibration in `details` |
| Real grounded values in `json_example` | Model primes on `"OBL-042"`, `0.87`, reasoning sentences — fabricates similar | Use `"<descriptive placeholder>"` for strings, `<float 0.0-1.0>` / `<int>` / `<true\|false>` unquoted for non-strings |
| Quoted non-string placeholders in `json_example` | `"<int>"`, `"<float 0.0-1.0>"`, `"<true\|false>"` teach the model the value is a string literal | Unquote non-string placeholders (§4 table); only strings and string-valued enums stay quoted |
| Single-line `json_example` | Nested shape unreadable; model sometimes mirrors the style | Pretty-print, 2-space indent |
| Asking LLM to echo back a long opaque ID | Character substitution, truncation, hallucinated plausible-but-wrong IDs | Have the model emit a 0-based integer index into a short list; the caller translates the index back to the ID (§5) |
| Multi-line prose input without delimiters | Boundary between the prose and following inputs becomes ambiguous; long prose in the middle of the prompt effectively gets "lost" | Wrap in `--- start of <name> ---` / `--- end of <name> ---` in the builder (see §3 `inputs_dict`) |
| New input key not in `INPUT_LABELS` | `KeyError` at render time | Add to `INPUT_LABELS` in `src/llm.py` |
| Bare `str` for enum field | Model can return anything; validation doesn't catch drift | Use `Literal['a', 'b', 'c']` |
| Pydantic field default for nullable | Old data silently loads with wrong value | Never add defaults; rebuild data instead |
| Field validator | Violates CLAUDE.md; brittle | Encode the rule in types, not validators |
| Rubrics/pitfalls empty or generic | Model makes same mistakes repeatedly | List specific rubrics and concrete pitfalls from observed output — scale to task complexity (2–5 each for simple binary/categorical picks; 8–20 each for nuanced multi-dimensional judgments); don't pad |
| `details` without worked examples | Nuanced judgments degrade | Add `Example N — <verdict>:` blocks per major judgment axis |
| Inline `max_tokens` too low | Reasoning exhaustion; empty output | See §9 guidance; bump to 4096+ for classification tasks |

---

## 14. References

- `src/llm.py` — `PromptSpec`, `CallSpec`, `INPUT_LABELS`, `_render_inputs`, `PromptSpec.render`
- `src/prompt_registry.py` — task registration
- `src/config.py` — `TaskModelConfig`
- `src/extraction/obligation_extraction.py` — exemplar for `details` taxonomy embedding, worked examples, and structural invariants
- `src/mapping/change_to_obligation.py` — exemplar for shared calibration constants and integer-index-based list selection (§5)
- `src/extraction/change_summary.py` — exemplar for nested response models and placeholder-style `json_example`
- `src/diff/version_diff.py` — exemplar for multi-line prose inputs with explicit delimiters
- `src/citation/citation_verify.py` — exemplar for post-generation verification prompts (claim ↔ cited passage)
- `src/ingest/proceeding_ingest.py` — exemplar for self-contained (no shared constants) prompts
- `CLAUDE.md` — governs `Literal` usage, `extra='forbid'`, no-validators, no-defaults, one-module-per-LLM-task
- `TEST_QUALITY.md` — authoritative testing rules
