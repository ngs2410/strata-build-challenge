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
  * When a batch has a mix of pending and already-failed sides, the
    prompt renders failed sides as `GROUNDING_FAILED_MARKER` rather
    than passage-text-plus-empty-span. The Steps and Pitfalls sections
    tell the LLM to emit `verified=null, rationale=null` for those
    sides. The merge step enforces the preservation regardless of what
    the LLM actually emits — the prompt-side instruction is a
    distraction-reducing nicety, not a correctness-load-bearing one.

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

GROUNDING_FAILED_MARKER = 'GROUNDING_FAILED (do not verify this side — handled upstream)'

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
    steps=(
      '1. For each change block in the Inputs section, read the description.\n'
      '2. For each non-null citation side, read the citing span AND the full '
      'passage text that contains it.\n'
      '3. Decide verification per side:\n'
      '   - tightened/loosened prior_citation: span should show the pre-change '
      "state of the modified obligation (the parent obligation BEFORE the "
      'parameter change).\n'
      '   - tightened/loosened new_citation: span should show the new state '
      'INCLUDING the specific parameter named in the description.\n'
      '   - added new_citation: span should show the new standalone action.\n'
      '   - removed prior_citation: span should show the obligation that was '
      'removed.\n'
      '4. Verified=true requires the span to substantively support the '
      'specific claim in description — not merely sit in the right paragraph.\n'
      '5. For null citations (added has null prior; removed has null new): '
      'verified=null and rationale=null.\n'
      f'6. For sides rendered as "{GROUNDING_FAILED_MARKER}": emit '
      'verified=null and rationale=null — the grounding failure is '
      'already recorded upstream.'
    ),
    details=(
      'Example 1 — strong support (verified=true):\n'
      '  Description: "A new standalone policy-documentation requirement for '
      'minimum-necessary determinations was added."\n'
      '  new_citation.citing span: "and shall document a written policy '
      'identifying the individuals authorized to make minimum-necessary '
      'determinations"\n'
      '  Correct: new_citation_verified=true. The span explicitly describes '
      'the new standalone policy-documentation action. '
      'new_citation_rationale: "quotes \'shall document a written policy '
      'identifying the individuals authorized\' — the exact new standalone '
      'obligation named in the description".\n'
      '\n'
      'Example 2 — right paragraph, wrong content (verified=false):\n'
      '  Description: "The capability-confirmation obligation is tightened by '
      'adding a required telemetry feed for aggregated resources rated at or '
      'above 100 kW."\n'
      '  new_citation.citing span: "the resources in the aggregation have the '
      'capability to provide the services for which the aggregation is '
      'bidding"\n'
      '  Correct: new_citation_verified=false. The span is from the same '
      'paragraph as the new telemetry requirement, but says NOTHING about '
      'telemetry or the 100 kW threshold — it is the pre-change capability '
      'language, not the post-change tightened state. '
      'new_citation_rationale: "cited span addresses capability confirmation '
      'but does not mention telemetry feed or the 100 kW threshold named in '
      'the description".\n'
      '\n'
      'Example 3 — parent-obligation span is correct for a tightened change\'s '
      'prior_citation (verified=true):\n'
      '  Description: "The SAR-filing obligation is tightened by adding a '
      '30-day deadline and a required wire-routing-path data element."\n'
      '  prior_citation.citing span: "shall file a suspicious activity report '
      'with FinCEN when a transaction over $5,000 meets any of the identified '
      'reporting criteria"\n'
      '  Correct: prior_citation_verified=true. The span is the pre-change '
      'parent obligation (file a SAR with FinCEN) whose parameters the new '
      'version tightens. prior_citation_rationale: "span is the pre-change '
      'SAR-filing duty whose timing and data-element parameters the new '
      'version tightens".'
    ),
    rubrics=(
      '- prior_citation_verified=True only when the prior passage text '
      'substantively supports the described change (as the pre-change state).\n'
      '- new_citation_verified=True only when the new passage text '
      'substantively supports the described change (as the post-change state).\n'
      '- When a change has no prior_citation (e.g. change_type="added"), set '
      'prior_citation_verified and prior_citation_rationale to null.\n'
      '- Same for new_citation when change_type="removed".\n'
      '- Rationale should quote the specific supporting phrase from the '
      'passage — do NOT restate the description.'
    ),
    pitfalls=(
      '- Do NOT verify=true when the span is in the right paragraph but does '
      'not mention the specific parameter / action named in the description. '
      '"The passage is topically relevant" is NOT the same as "the span '
      'substantively supports the specific claim" — see Example 2.\n'
      '- Do NOT paraphrase the description in the rationale. The rationale '
      'MUST quote the specific supporting phrase from the passage/span; it is '
      'the auditor\'s receipt that the span actually says what the description '
      'claims.\n'
      '- Do NOT verify=false merely because the prior_citation span lacks the '
      'new parameter named in the description. A tightened/loosened '
      "prior_citation shows the pre-change parent obligation; it is CORRECT "
      'for it not to contain the tightening parameter — that lives in the '
      'new_citation. See Example 3.\n'
      f'- When a citation side is rendered as "{GROUNDING_FAILED_MARKER}", '
      'the structural grounding step upstream could not locate the citing '
      'span in the passage. That side is already recorded as unverified; '
      'emit verified=null and rationale=null for it. Do NOT attempt to '
      'verify from the description alone.'
    ),
    field_intents=(
      '- change_index: 0-based index into the input change list (the "[N]" '
      'prefix on each rendered change block). Must be a valid index into that '
      'list.\n'
      '- prior_citation_verified: true/false if the Change has a '
      'prior_citation; null if the Change has no prior_citation (e.g. '
      'change_type="added").\n'
      '- prior_citation_rationale: short quoted supporting phrase from the '
      "passage if verified=true; a one-sentence refutation naming what the "
      'span does NOT support if verified=false; null when '
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
  if not is_grounding_pending(citation):
    return f'{label}_citation: {GROUNDING_FAILED_MARKER}'
  passage = passages_by_id.get(citation.passage_id)
  if passage is None:
    raise ValueError(
      f'citation_verify: cited passage_id={citation.passage_id} not found '
      f'in either version — grounding should have raised earlier'
    )
  span = passage.text[citation.start_offset : citation.end_offset]
  return (
    f'{label}_citation:\n'
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
