from pydantic import BaseModel, ConfigDict

from src.config import Citation, ExtractedObligation, ProceedingVersion
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller

TASK_NAME = 'obligation_extraction'

STUB_RATIONALE = (
  'stub extraction: hand-constructed example obligation covering the '
  'registration lead-time clause; real impl calls obligation_extraction LLM task'
)


class ObligationExtractionResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  extracted_obligations: tuple[ExtractedObligation, ...]


OBLIGATION_EXTRACTION_SPEC = CallSpec(
  purpose=(
    'From a proceeding version, extract each regulatory obligation it '
    'establishes. Attach a structural citation to the supporting passage.'
  ),
  acceptance_criteria=(
    'Every ExtractedObligation has a non-null citation, a valid '
    'obligation_type (reporting / operational / procedural / financial), and '
    'a confidence score with a rubric-grounded rationale.'
  ),
  failure_modes=(
    '(a) Mis-typing — classifying a procedural obligation as reporting. '
    '(b) Over-extraction — splitting one obligation into several. '
    '(c) Missing citation — producing an obligation without passage anchor.'
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
    rubrics=(
      '- One ExtractedObligation per discrete obligation.\n'
      '- obligation_type is one of reporting / operational / procedural / '
      'financial.\n'
      '- Each obligation carries a structural citation (passage_id + offsets).'
    ),
    inputs_dict={
      'passage_text': ('--- start of passage ---\n' + passage_text + '\n--- end of passage ---'),
    },
  )


def stub_obligation_extraction_response(params: dict) -> ObligationExtractionResponse:
  version: ProceedingVersion = params['version']
  if not version.passages:
    return ObligationExtractionResponse(extracted_obligations=())
  anchor = version.passages[0]
  citation = Citation(
    passage_id=anchor.passage_id,
    start_offset=0,
    end_offset=min(len(anchor.text), 200),
    verified=True,
    verification_rationale='stub extraction attaches a pre-verified citation',
  )
  return ObligationExtractionResponse(
    extracted_obligations=(
      ExtractedObligation(
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


def extract_obligations(version: ProceedingVersion) -> list[ExtractedObligation]:
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
