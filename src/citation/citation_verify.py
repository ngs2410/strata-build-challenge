'''`citation_verify` LLM task — does the cited passage actually support the claim?

Project depth area (two-step citation pattern), post-grounding verifier.
`citation_grounding` locates the citing span deterministically; this module
runs a semantic NLI-style check that confirms the cited span SUPPORTS the
described change. The two steps are independent: grounding can find a span
that doesn't semantically support the claim, and verification is the gate
that catches it.

Pipeline position: `verify_citations(changes, prior, final)` is called in
`pipeline.run_pipeline` iff `DomainConfig.citation.verification_required ==
True`. One LLM call per batch of changes (not per citation) — the prompt
packs all changes with their cited passage text; the response is
one `CitationVerificationItem` per change_id.

Response merging:
  * The LLM returns `prior_citation_verified` and `new_citation_verified`
    as `bool | None` for each change — null means "no citation on this
    side" (added or removed changes). `_apply_verification` writes
    `verified` + `verification_rationale` back onto the existing
    `Citation` via `model_copy(update=...)` — the citation's
    `start_offset` / `end_offset` from grounding are preserved.
  * Missing a verification entry for any change_id raises `ValueError` —
    we never silently drop a change (CLAUDE.md fail-fast).
  * A `verified=None` response for a non-null citation falls back to
    `verified=False` with a diagnostic rationale — this is a verifier bug
    we want visible, not a silent pass.

Short-circuit on failed grounding:
  * A citation whose grounding step produced a failure sentinel (span
    not found, span below min width) carries a
    `verification_rationale` other than `GROUNDING_PENDING_RATIONALE`.
    `_apply_verification` returns such a citation unchanged — the LLM
    vote is NEVER allowed to overwrite the grounding failure. If NO
    change in the batch has any pending citation, `verify_citations`
    skips the LLM call entirely and returns `changes` as-is. Rationale:
    (a) a citation that can't be anchored structurally should not be
    reported as semantically verified, and (b) at gpt-5.4-nano the
    verifier would otherwise vote "true" on an empty span and silently
    launder a grounding failure into a verified citation.

The LLM is instructed to quote the specific supporting phrase in its
rationale, so a reviewer scanning an "unverified" item can see WHY — not
just that the verifier declined.

`stub_citation_verify_response` returns `True` for every citation whose
passage_id resolves — enough for StubBackend tests to exercise the merge
path without an API call.
'''

from pydantic import BaseModel, ConfigDict

from src.citation.citation_grounding import is_grounding_pending
from src.config import Change, Citation, ProceedingVersion
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller

TASK_NAME = 'citation_verify'

STUB_RATIONALE = 'stub verifier: cited passage trivially supports claim at v1 scale'

CITATION_VERIFY_JSON_EXAMPLE = """{
  "verifications": [
    {
      "change_index": <int 0-based index into the input change list>,
      "prior_citation_verified": <true|false|null>,
      "prior_citation_rationale": "<quote the specific supporting phrase, or null>",
      "new_citation_verified": <true|false|null>,
      "new_citation_rationale": "<quote the specific supporting phrase, or null>"
    }
  ]
}"""


class CitationVerificationItem(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_index: int
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
      'CitationVerificationItem per Change, keyed by the 0-based change_index '
      '(the "[0]", "[1]", … prefix on each change block in the Inputs section). '
      'The harness resolves the index back to the Change identity after.'
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
    field_intents=(
      '- change_index: 0-based index into the input change list (the "[N]" '
      'prefix on each rendered change block). Must be a valid index into that '
      'list.\n'
      '- prior_citation_verified: true/false if the Change has a '
      'prior_citation; null if the Change has no prior_citation (e.g. '
      'change_type="added").\n'
      '- prior_citation_rationale: short quoted supporting phrase if '
      'verified=true; a one-sentence refutation if verified=false; null when '
      'prior_citation_verified is null.\n'
      '- new_citation_verified / new_citation_rationale: same pattern for '
      'the new_citation.'
    ),
    inputs_dict={
      'claim': ('--- start of changes ---\n' + rendered_changes + '\n--- end of changes ---'),
    },
    json_example=CITATION_VERIFY_JSON_EXAMPLE,
  )


def stub_citation_verify_response(params: dict) -> CitationVerificationResponse:
  changes: list[Change] = params['changes']
  prior: ProceedingVersion = params['prior']
  final: ProceedingVersion = params['final']
  passages_by_id = {
    passage.passage_id: passage for version in (prior, final) for passage in version.passages
  }
  items: list[CitationVerificationItem] = []
  for index, change in enumerate(changes):
    prior_v, prior_r = _stub_verify(change.prior_citation, passages_by_id)
    new_v, new_r = _stub_verify(change.new_citation, passages_by_id)
    items.append(
      CitationVerificationItem(
        change_index=index,
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
  needs_llm = any(
    is_grounding_pending(change.prior_citation) or is_grounding_pending(change.new_citation)
    for change in changes
  )
  if not needs_llm:
    return changes
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
  by_change_index = {item.change_index: item for item in response.verifications}
  merged: list[Change] = []
  for index, change in enumerate(changes):
    verification = by_change_index.get(index)
    if verification is None:
      raise ValueError(
        f'citation_verify returned no verification for change_index={index} '
        f'(change_id={change.change_id})'
      )
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
  lines = [f'[{index}]']
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
  if not is_grounding_pending(citation):
    return citation
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
