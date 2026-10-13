from pydantic import BaseModel, ConfigDict

from src.config import Change, Citation, ProceedingVersion
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller

TASK_NAME = 'citation_verify'

STUB_RATIONALE = 'stub verifier: cited passage trivially supports claim at v1 scale'


class CitationVerificationResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  changes: tuple[Change, ...]


CITATION_VERIFY_SPEC = CallSpec(
  purpose=(
    'For each Change, verify that the cited passage(s) actually support the '
    'change description. Mark Citation.verified true when the passage '
    'entails the claim; false otherwise.'
  ),
  acceptance_criteria=(
    'verified=True only when the cited passage substantively supports the '
    'description; verification_rationale cites the specific supporting span.'
  ),
  failure_modes=(
    '(a) False positive — marking a non-supporting passage as verified. '
    '(b) Over-indexing on surface terms rather than semantic support. '
    '(c) Marking as verified when citing_span was not found in the passage.'
  ),
)


def build_citation_verify_prompt(
  changes: list[Change], prior: ProceedingVersion, final: ProceedingVersion
) -> PromptSpec:
  del prior, final
  claim_text = '\n'.join(
    f'[{i}] change_type={c.change_type}: {c.description}' for i, c in enumerate(changes)
  )
  return PromptSpec(
    system_prompt=(
      'You are a citation verifier. For each Change below, determine whether '
      'its cited passage(s) genuinely support the claim. You produce '
      'structured JSON output: for every Change, update Citation.verified '
      'and Citation.verification_rationale.'
    ),
    task='Verify every citation on the listed Changes.',
    rubrics=(
      '- verified=True only when the cited passage entails or substantively '
      'supports the described claim.\n'
      '- Mark verified=False with a rationale when the cited passage is '
      'unrelated or when the citing_span could not be located.'
    ),
    inputs_dict={
      'claim': ('--- start of changes ---\n' + claim_text + '\n--- end of changes ---'),
    },
  )


def stub_citation_verify_response(params: dict) -> CitationVerificationResponse:
  changes: list[Change] = params['changes']
  prior: ProceedingVersion = params['prior']
  final: ProceedingVersion = params['final']
  passages_by_id = {
    passage.passage_id: passage for version in (prior, final) for passage in version.passages
  }
  verified: list[Change] = []
  for change in changes:
    prior_c = _verify_one(change.prior_citation, passages_by_id)
    new_c = _verify_one(change.new_citation, passages_by_id)
    verified.append(change.model_copy(update={'prior_citation': prior_c, 'new_citation': new_c}))
  return CitationVerificationResponse(changes=tuple(verified))


def verify_citations(
  changes: list[Change],
  prior: ProceedingVersion,
  final: ProceedingVersion,
) -> list[Change]:
  prompt = build_citation_verify_prompt(changes, prior, final)
  system, user = prompt.render()
  response = model_caller.call(
    task_name=TASK_NAME,
    params={
      'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
      'inputs_dict': prompt.inputs_dict,
      'changes': changes,
      'prior': prior,
      'final': final,
    },
    response_type=CitationVerificationResponse,
    call_spec=CITATION_VERIFY_SPEC,
    tenant_id=prior.tenant_id,
    environment=prior.environment,
  )
  return list(response.changes)


def _verify_one(citation: Citation | None, passages_by_id: dict) -> Citation | None:
  if citation is None:
    return None
  if citation.passage_id not in passages_by_id:
    return citation.model_copy(
      update={
        'verified': False,
        'verification_rationale': 'citing passage not found in either version',
      }
    )
  return citation.model_copy(
    update={
      'verified': True,
      'verification_rationale': STUB_RATIONALE,
    }
  )
