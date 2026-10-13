from pydantic import BaseModel, ConfigDict

from src.config import Change, Citation, ProceedingVersion
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller

TASK_NAME = 'citation_verify'

STUB_RATIONALE = 'stub verifier: cited passage trivially supports claim at v1 scale'


class CitationVerificationItem(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_id: str
  prior_citation_verified: bool | None
  prior_citation_rationale: str | None
  new_citation_verified: bool | None
  new_citation_rationale: str | None


class CitationVerificationResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  verifications: tuple[CitationVerificationItem, ...]


CITATION_VERIFY_SPEC = CallSpec(
  purpose=(
    'For each Change, verify that the cited passage(s) actually support the '
    'change description. Mark prior/new citation verified true when the passage '
    'entails the claim; false otherwise.'
  ),
  acceptance_criteria=(
    'verified=True only when the cited passage substantively supports the '
    'description; verification_rationale cites the specific supporting span.'
  ),
  failure_modes=(
    '(a) False positive — marking a non-supporting passage as verified. '
    '(b) Over-indexing on surface terms rather than semantic support. '
    '(c) Marking as verified when the described change is not actually '
    'reflected in the cited passage text.'
  ),
)


def build_citation_verify_prompt(
  changes: list[Change], prior: ProceedingVersion, final: ProceedingVersion
) -> PromptSpec:
  passages_by_id = {
    passage.passage_id: passage for version in (prior, final) for passage in version.passages
  }
  rendered_changes = '\n\n'.join(
    _render_change_block(i, change, passages_by_id) for i, change in enumerate(changes)
  )
  return PromptSpec(
    system_prompt=(
      'You are a citation verifier for a regulatory change-intelligence '
      'pipeline. For each proposed Change, you receive the change description '
      'and the exact text of the passage(s) it cites. You decide whether each '
      'cited passage substantively supports the described change. You produce '
      'structured JSON output.'
    ),
    task=(
      'Verify every citation on each listed Change. Return one '
      'CitationVerificationItem per Change (by change_id), with per-citation '
      'verified booleans and short rationales.'
    ),
    rubrics=(
      '- prior_citation_verified=True only when the prior passage text '
      'substantively supports the described change (as the pre-change state).\n'
      '- new_citation_verified=True only when the new passage text '
      'substantively supports the described change (as the post-change state).\n'
      '- When a change has no prior_citation (e.g. change_type="added"), set '
      'prior_citation_verified and prior_citation_rationale to null.\n'
      '- Same for new_citation when change_type="removed".\n'
      '- Rationale should quote the specific supporting phrase.'
    ),
    inputs_dict={
      'claim': ('--- start of changes ---\n' + rendered_changes + '\n--- end of changes ---'),
    },
  )


def stub_citation_verify_response(params: dict) -> CitationVerificationResponse:
  changes: list[Change] = params['changes']
  prior: ProceedingVersion = params['prior']
  final: ProceedingVersion = params['final']
  passages_by_id = {
    passage.passage_id: passage for version in (prior, final) for passage in version.passages
  }
  items: list[CitationVerificationItem] = []
  for change in changes:
    prior_v, prior_r = _stub_verify(change.prior_citation, passages_by_id)
    new_v, new_r = _stub_verify(change.new_citation, passages_by_id)
    items.append(
      CitationVerificationItem(
        change_id=change.change_id,
        prior_citation_verified=prior_v,
        prior_citation_rationale=prior_r,
        new_citation_verified=new_v,
        new_citation_rationale=new_r,
      )
    )
  return CitationVerificationResponse(verifications=tuple(items))


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
  by_change_id = {item.change_id: item for item in response.verifications}
  merged: list[Change] = []
  for change in changes:
    verification = by_change_id.get(change.change_id)
    if verification is None:
      raise ValueError(f'citation_verify returned no verification for change_id={change.change_id}')
    merged.append(
      change.model_copy(
        update={
          'prior_citation': _apply_verification(
            change.prior_citation,
            verification.prior_citation_verified,
            verification.prior_citation_rationale,
          ),
          'new_citation': _apply_verification(
            change.new_citation,
            verification.new_citation_verified,
            verification.new_citation_rationale,
          ),
        }
      )
    )
  return merged


def _render_change_block(index: int, change: Change, passages_by_id: dict) -> str:
  lines = [f'[{index}] change_id={change.change_id}']
  lines.append(f'change_type: {change.change_type}')
  lines.append(f'description: {change.description}')
  lines.append(_render_citation('prior', change.prior_citation, passages_by_id))
  lines.append(_render_citation('new', change.new_citation, passages_by_id))
  return '\n'.join(lines)


def _render_citation(label: str, citation: Citation | None, passages_by_id: dict) -> str:
  if citation is None:
    return f'{label}_citation: null'
  passage = passages_by_id.get(citation.passage_id)
  if passage is None:
    return (
      f'{label}_citation: passage_id={citation.passage_id} '
      f'(passage text unavailable — passage_id not found in either version)'
    )
  span = passage.text[citation.start_offset : citation.end_offset]
  return (
    f'{label}_citation (passage_id={citation.passage_id}, '
    f'offsets {citation.start_offset}-{citation.end_offset}):\n'
    f'  passage text: "{passage.text}"\n'
    f'  citing span: "{span}"'
  )


def _apply_verification(
  citation: Citation | None,
  verified: bool | None,
  rationale: str | None,
) -> Citation | None:
  if citation is None:
    return None
  if verified is None:
    return citation.model_copy(
      update={
        'verified': False,
        'verification_rationale': 'verifier returned null for a non-null citation',
      }
    )
  return citation.model_copy(
    update={
      'verified': verified,
      'verification_rationale': rationale if rationale is not None else '',
    }
  )


def _stub_verify(citation: Citation | None, passages_by_id: dict) -> tuple[bool | None, str | None]:
  if citation is None:
    return None, None
  if citation.passage_id not in passages_by_id:
    return False, 'citing passage not found in either version'
  return True, STUB_RATIONALE
