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
      "extraction_id": "<stable id for this extraction>",
      "tenant_id": "<tenant id>",
      "environment": "<environment>",
      "proceeding_id": "<proceeding id>",
      "version_id": "<version id>",
      "obligation_type": "<reporting|operational|procedural|financial>",
      "summary": "<one-sentence summary of the obligation>",
      "full_text": "<verbatim obligation text from the passage>",
      "citation": {
        "passage_id": "<passage id that establishes this obligation>",
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
  extraction_id: str
  tenant_id: str
  environment: str
  proceeding_id: str
  version_id: str
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
  passage_text = '\n\n'.join(p.text for p in version.passages)
  return PromptSpec(
    system_prompt=(
      'You are a regulatory obligation extractor. You identify the discrete '
      'obligations a regulatory text establishes, classify each by type, and '
      'cite each to its source passage. You produce structured JSON output.'
    ),
    task='Extract every discrete obligation from the passage below.',
    details=OBLIGATION_TAXONOMY_REFERENCE + '\n\n' + CONFIDENCE_LEVEL_RUBRIC,
    rubrics=(
      '- One LlmExtractedObligation per discrete obligation.\n'
      '- obligation_type is one of reporting / operational / procedural / '
      'financial per the taxonomy in Details.\n'
      '- Each obligation carries a structural citation: passage_id + '
      'citing_span. citing_span is the VERBATIM text copied '
      'character-for-character from the cited passage. Do NOT paraphrase, '
      'summarise, or truncate mid-word. Do NOT count characters or emit '
      'numeric offsets — grounding is done deterministically by Python '
      'after this call.\n'
      '- Report confidence as an integer 0-5 per the confidence rubric in '
      'Details; set confidence_rationale naming the rubric level and why.'
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
    passage_id=anchor.passage_id,
    citing_span=anchor.text[: min(len(anchor.text), 200)],
  )
  return ObligationExtractionResponse(
    extracted_obligations=(
      LlmExtractedObligation(
        extraction_id=f'{version.proceeding_id}:{version.version_id}:ext-001',
        tenant_id=version.tenant_id,
        environment=version.environment,
        proceeding_id=version.proceeding_id,
        version_id=version.version_id,
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
