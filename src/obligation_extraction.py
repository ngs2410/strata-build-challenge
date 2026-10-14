'''`obligation_extraction` LLM task — one `ProceedingVersion` → N obligations.

Pipeline position: `extract_obligations(version)` is called once per
version by `pipeline.run_pipeline`. Each returned `LlmExtractedObligation`
carries an `LlmCitation(passage_id, citing_span)` that
`citation.citation_grounding.ground_obligations` turns into a precise
`Citation` by substring search within the cited passage. Grounding failures
mark the citation unverified; `pipeline` then escalates downstream when
`citation.verification_required=true`.

Four `Literal` obligation types, injected into the prompt via
`OBLIGATION_TAXONOMY_REFERENCE` from `prompt_calibration`:
reporting / operational / procedural / financial. The taxonomy strings
and the `Literal` set on `Obligation` and `ExtractedObligation` in
config.py MUST stay in sync — grep both files when adding a type.

Perspective: `OBLIGATION_TAXONOMY_REFERENCE` embeds
`REGULATED_ENTITY_PERSPECTIVE` so the extractor never treats a
regulator-side duty ("the Commission shall...", "the RTO shall...") as
a regulated-entity obligation. Example 4 in the prompt's Details
section + the first Pitfall make this concrete.

The LLM returns `citing_span` as VERBATIM substring, never numeric offsets
— the project's citation-grounding depth-area pattern. The system prompt
and rubric both say "do not count characters or emit numeric offsets".

`stub_obligation_extraction_response` returns one hand-constructed
procedural obligation anchored to `version.passages[0]` — enough for
`StubBackend` tests and dry-runs to exercise the grounding + verification
chain without an LLM call.
'''

from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.citation.llm_citation import LlmCitation
from src.config import ProceedingVersion
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller
from src.prompt_calibration import CONFIDENCE_LEVEL_RUBRIC, OBLIGATION_TAXONOMY_REFERENCE

TASK_NAME = 'obligation_extraction'

OBLIGATION_EXTRACTION_JSON_EXAMPLE = """{
  "extracted_obligations": [
    {
      "obligation_type": "<reporting|operational|procedural|financial>",
      "summary": "<one-sentence summary of the obligation>",
      "full_text": "<verbatim obligation text from the passage>",
      "citation": {
        "passage_index": <int 0-based index into the version's passage list>,
        "citing_span": "<verbatim text span copied character-for-character from the cited passage>"
      },
      "confidence": <int 0-5 per CONFIDENCE_LEVEL_RUBRIC>,
      "confidence_rationale": "<which rubric level you picked and why>"
    }
  ]
}"""

STUB_RATIONALE = (
  'stub extraction: hand-constructed example obligation covering the '
  'registration lead-time clause; real impl calls obligation_extraction LLM task'
)


class LlmExtractedObligation(BaseModel):
  model_config = ConfigDict(extra='forbid')
  obligation_type: Literal['reporting', 'operational', 'procedural', 'financial']
  summary: str
  full_text: str
  citation: LlmCitation
  confidence: int
  confidence_rationale: str


class ObligationExtractionResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  extracted_obligations: tuple[LlmExtractedObligation, ...]


OBLIGATION_EXTRACTION_SPEC = CallSpec(
  purpose=(
    'From a proceeding version, extract each regulatory obligation it '
    'establishes. Attach a structural citation (passage_id + citing_span) '
    'that grounding will substring-match into offsets.'
  ),
  acceptance_criteria=(
    'Every LlmExtractedObligation has a non-null citation whose citing_span '
    'is copied verbatim from the cited passage, a valid obligation_type '
    '(reporting / operational / procedural / financial), and a confidence '
    'score with a rubric-grounded rationale.'
  ),
  failure_modes=(
    '(a) Mis-typing — classifying a procedural obligation as reporting. '
    '(b) Over-extraction — splitting one obligation into several. '
    '(c) Missing citation — producing an obligation without passage anchor. '
    '(d) citing_span not verbatim — paraphrasing or summarising the cited '
    'passage instead of copying the exact substring (grounding will mark '
    'the citation unverified).'
  ),
)


def build_obligation_extraction_prompt(version: ProceedingVersion) -> PromptSpec:
  passage_text = '\n\n'.join(f'[{i}] {p.text}' for i, p in enumerate(version.passages))
  return PromptSpec(
    system_prompt=(
      'You are a regulatory obligation extractor. You identify the discrete '
      'obligations a regulatory text establishes, classify each by type, and '
      'cite each to its source passage. You produce structured JSON output.'
    ),
    task='Extract every discrete obligation from the passage below.',
    steps=(
      '1. Read the passage. Identify every sentence or clause that '
      'establishes a discrete duty on the regulated entity.\n'
      '2. For each duty, decide its type per the obligation taxonomy '
      '(reporting / operational / procedural / financial).\n'
      '3. Draft a one-sentence summary. Copy the full obligation text '
      'verbatim from the passage as full_text.\n'
      '4. Extract citing_span: the SHORTEST verbatim substring from the '
      'cited passage that identifies the duty — not the full sentence, '
      'not the surrounding paragraph.\n'
      '5. Self-assess confidence per the rubric and name the level in '
      'confidence_rationale.'
    ),
    details=(
      'Example 1 — one sentence → one obligation (clean case):\n'
      '  Passage (hypothetical SEC rule): "A broker-dealer shall file '
      'Form X-17A-5 annually with the Commission by June 30."\n'
      '  Correct: ONE obligation.\n'
      '    obligation_type: reporting\n'
      '    summary: "A broker-dealer must file Form X-17A-5 annually '
      'with the Commission by June 30."\n'
      '    citing_span: "shall file Form X-17A-5 annually with the '
      'Commission by June 30"\n'
      '    confidence: 5 (the duty is stated verbatim).\n'
      '\n'
      'Example 2 — one sentence → two obligations (compound duty):\n'
      '  Passage (hypothetical HIPAA Minimum Necessary clause): "A '
      'covered entity shall limit uses and disclosures of protected '
      'health information to the minimum necessary for the intended '
      'purpose, and shall document a written policy identifying the '
      'individuals authorized to make minimum-necessary determinations."\n'
      '  Correct: TWO obligations.\n'
      '    Obligation A — operational: "shall limit uses and '
      'disclosures of protected health information to the minimum '
      'necessary for the intended purpose".\n'
      '    Obligation B — procedural: "shall document a written policy '
      'identifying the individuals authorized to make minimum-necessary '
      'determinations".\n'
      '  Rule of thumb: if removing one half of an "and shall ..." '
      'conjunction leaves the other half as a well-formed duty on its '
      'own, they are TWO obligations.\n'
      '\n'
      'Example 3 — shortest-span discipline:\n'
      '  Passage (hypothetical SEC rule, extended): "A broker-dealer '
      'shall file Form X-17A-5 annually with the Commission by June 30, '
      'which shall be executed by the chief executive officer and chief '
      'financial officer."\n'
      '  Correct citing_span: "shall file Form X-17A-5 annually with '
      'the Commission by June 30" — just the core filing duty.\n'
      '  NOT: the entire sentence including the "which shall be '
      'executed by ..." modifier. If the executive-signature clause '
      'were a distinct duty, it would be a separate obligation with '
      'its own span.\n'
      '\n'
      'Example 4 — regulator-side-duty filter (perspective rule):\n'
      '  Passage (hypothetical FinCEN rule): "FinCEN shall make the '
      'aggregated suspicious activity data available to law enforcement '
      'agencies within 72 hours of filing. A bank shall file a '
      'suspicious activity report with FinCEN when a transaction over '
      '$5,000 meets the identified reporting criteria."\n'
      '  Correct: ONE obligation — the bank\'s SAR-filing duty '
      '(reporting). FinCEN\'s data-sharing duty is a REGULATOR-side '
      'obligation and does NOT belong in the regulated entity\'s '
      'compliance set. The perspective rule in the Rubrics preamble '
      'applies to extraction as well as classification: Strata tracks '
      'the regulated entity\'s duties, not the regulator\'s.'
    ),
    rubrics=OBLIGATION_TAXONOMY_REFERENCE + '\n\n' + CONFIDENCE_LEVEL_RUBRIC,
    pitfalls=(
      '- Do NOT extract a REGULATOR-side duty as an obligation of the '
      'regulated entity. "The Commission shall...", "FinCEN shall...", '
      '"The RTO/ISO shall accept bids from aggregators..." — these '
      'establish duties FOR THE REGULATOR, not for the regulated '
      'entity. Strata tracks the regulated entity\'s compliance set; '
      'regulator-side duties are entity RIGHTS, not entity obligations, '
      'and must be omitted. See Example 4 and the perspective rule in '
      'the Rubrics preamble.\n'
      '- Do NOT split one duty with multiple sub-elements into multiple '
      'obligations. "shall limit uses and disclosures of PHI to the '
      'minimum necessary" is ONE duty (apply the minimum-necessary '
      'standard to both uses and disclosures); the conjunction '
      'describes the scope of the duty, not two separate duties.\n'
      '- Do NOT merge two independent duties into one. If two '
      'duties joined by "and shall ..." have separate compliance '
      'surfaces (one is a continuous constraint, the other produces an '
      'artefact), extract two obligations — see Example 2.\n'
      '- Do NOT include surrounding modifiers in citing_span. Pick the '
      'SHORTEST span that identifies the duty — see Example 3.\n'
      '- Do NOT emit confidence=5 on type-boundary cases. "shall '
      'maintain" could be operational (continuous constraint) or '
      'procedural (process); when the type is a judgment call, drop '
      'confidence to 3 or 4 and name the ambiguity in '
      'confidence_rationale.'
    ),
    field_intents=(
      '- obligation_type: one of reporting / operational / procedural / '
      'financial per the taxonomy above.\n'
      '- summary: one-sentence summary of the obligation.\n'
      '- full_text: verbatim obligation text copied from the passage.\n'
      '- citation.passage_index: 0-based index into the passage list '
      '(count "[0]" / "[1]" / ... prefixes).\n'
      '- citation.citing_span: SHORTEST verbatim substring from the '
      'cited passage that establishes this obligation. '
      'Character-for-character copy; do not paraphrase, summarise, or '
      'include surrounding context. Do NOT count characters or emit '
      'numeric offsets — grounding is done deterministically by Python '
      'after this call.\n'
      '- confidence: integer 0-5 per the confidence rubric above.\n'
      '- confidence_rationale: one sentence naming the rubric level you '
      'picked and why.\n'
      '- The harness owns extraction_id, tenant_id, environment, '
      'proceeding_id, and version_id — do NOT emit these fields.'
    ),
    inputs_dict={
      'passage_text': ('--- start of passage ---\n' + passage_text + '\n--- end of passage ---'),
    },
    json_example=OBLIGATION_EXTRACTION_JSON_EXAMPLE,
  )


def stub_obligation_extraction_response(params: dict) -> ObligationExtractionResponse:
  version: ProceedingVersion = params['version']
  if not version.passages:
    return ObligationExtractionResponse(extracted_obligations=())
  anchor = version.passages[0]
  citation = LlmCitation(
    passage_index=0,
    citing_span=anchor.text[: min(len(anchor.text), 200)],
  )
  return ObligationExtractionResponse(
    extracted_obligations=(
      LlmExtractedObligation(
        obligation_type='procedural',
        summary=('Aggregators must register each DER with the RTO/ISO prior to its participation.'),
        full_text=anchor.text,
        citation=citation,
        confidence=4,
        confidence_rationale=STUB_RATIONALE,
      ),
    ),
  )


def extract_obligations(version: ProceedingVersion) -> list[LlmExtractedObligation]:
  prompt = build_obligation_extraction_prompt(version)
  system, user = prompt.render()
  response = model_caller.call(
    task_name=TASK_NAME,
    params={
      'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
      'inputs_dict': prompt.inputs_dict,
      'version': version,
    },
    response_type=ObligationExtractionResponse,
    call_spec=OBLIGATION_EXTRACTION_SPEC,
    tenant_id=version.tenant_id,
    environment=version.environment,
  )
  return list(response.extracted_obligations)
