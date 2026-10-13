from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from src.config import Change, Citation, ProceedingVersion
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller
from src.prompt_calibration import CHANGE_TYPE_RUBRIC, CONFIDENCE_LEVEL_RUBRIC

TASK_NAME = 'version_diff'

VERSION_DIFF_JSON_EXAMPLE = """{
  "changes": [
    {
      "change_id": "<stable id for this change>",
      "tenant_id": "<tenant id>",
      "environment": "<environment>",
      "proceeding_id": "<proceeding id>",
      "change_type": "<added|removed|tightened|loosened|cosmetic>",
      "description": "<one-sentence description of the semantic shift>",
      "prior_citation": {
        "passage_id": "<passage id from the prior version, or null if added>",
        "start_offset": <int>,
        "end_offset": <int>,
        "verified": false,
        "verification_rationale": "pending citation_verify stage"
      },
      "new_citation": {
        "passage_id": "<passage id from the new version, or null if removed>",
        "start_offset": <int>,
        "end_offset": <int>,
        "verified": false,
        "verification_rationale": "pending citation_verify stage"
      },
      "confidence": <int 0-5 per CONFIDENCE_LEVEL_RUBRIC>,
      "confidence_rationale": "<which rubric level you picked and why>",
      "detected_at": "<ISO-8601 datetime>"
    }
  ]
}"""


class VersionDiffResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  changes: tuple[Change, ...]


VERSION_DIFF_SPEC = CallSpec(
  purpose=(
    'Detect material changes between a draft (NOPR) and final version of a '
    'regulatory proceeding subsection. Each change gets a change_type '
    '(added / removed / tightened / loosened / cosmetic) and citations into '
    'both versions.'
  ),
  acceptance_criteria=(
    'Each returned Change: has a non-empty description; cites real passages '
    'in prior/new (or null when the change is purely added or removed); '
    'change_type matches the semantic shift between the cited passages.'
  ),
  failure_modes=(
    '(a) Hallucinated passage_id not present in either version. '
    '(b) Over-mapping — treating paraphrases as material changes. '
    '(c) Draft/final confusion — citing NOPR text as if it were the final rule.'
  ),
)


def build_version_diff_prompt(prior: ProceedingVersion, final: ProceedingVersion) -> PromptSpec:
  prior_text = '\n\n'.join(p.text for p in prior.passages)
  new_text = '\n\n'.join(p.text for p in final.passages)
  return PromptSpec(
    system_prompt=(
      'You are a regulatory-change analyst. You compare the draft (NOPR) and '
      'final versions of a single subsection of a regulatory rule and return '
      'the material differences, classified by type and cited to the exact '
      'passage in each version. You produce structured JSON output.'
    ),
    task=('Compare the prior and new versions below and return a list of material changes.'),
    details=CHANGE_TYPE_RUBRIC + '\n\n' + CONFIDENCE_LEVEL_RUBRIC,
    rubrics=(
      '- Classify each change as added / removed / tightened / loosened / '
      'cosmetic per the change-type rubric in Details.\n'
      '- Attach prior_citation and new_citation; set one to null when the '
      'change is added-only or removed-only.\n'
      '- Pure rewording that preserves meaning is cosmetic, not material.\n'
      '- Report confidence as an integer 0-5 per the confidence rubric in '
      'Details; set confidence_rationale naming the rubric level and why.'
    ),
    inputs_dict={
      'prior_version_text': (
        '--- start of prior version ---\n' + prior_text + '\n--- end of prior version ---'
      ),
      'new_version_text': (
        '--- start of new version ---\n' + new_text + '\n--- end of new version ---'
      ),
    },
    json_example=VERSION_DIFF_JSON_EXAMPLE,
  )


def stub_version_diff_response(params: dict) -> VersionDiffResponse:
  prior = params['prior']
  final = params['final']
  now = datetime.now(timezone.utc)
  tenant_id = prior.tenant_id
  environment = prior.environment
  proceeding_id = prior.proceeding_id
  changes = (
    Change(
      change_id=f'{proceeding_id}:change-001',
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=proceeding_id,
      change_type='loosened',
      description=(
        'Final Rule permits RTOs/ISOs to establish an aggregation minimum size up to '
        '100 kW; NOPR explicitly disallowed any minimum size.'
      ),
      prior_citation=_stub_citation(prior, 1),
      new_citation=_stub_citation(final, 1),
      confidence=4,
      confidence_rationale=(
        'Direct textual contrast between subsection (A) of NOPR and Final Rule — '
        'stub-generated against the sample excerpts.'
      ),
      detected_at=now,
    ),
    Change(
      change_id=f'{proceeding_id}:change-002',
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=proceeding_id,
      change_type='tightened',
      description=(
        'Final Rule requires aggregator registration at least 60 days before '
        'participation; NOPR had no lead-time requirement.'
      ),
      prior_citation=_stub_citation(prior, 2),
      new_citation=_stub_citation(final, 2),
      confidence=4,
      confidence_rationale=(
        'Subsection (B) adds the 60-day lead time in Final; stub-generated against '
        'the sample excerpts.'
      ),
      detected_at=now,
    ),
    Change(
      change_id=f'{proceeding_id}:change-003',
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=proceeding_id,
      change_type='added',
      description=(
        'Final Rule introduces a telemetry-feed requirement for aggregated resources '
        'rated ≥100 kW; NOPR had no telemetry requirement.'
      ),
      prior_citation=None,
      new_citation=_stub_citation(final, 3),
      confidence=5,
      confidence_rationale=(
        'Final subsection (C) adds the telemetry obligation; stub-generated against '
        'the sample excerpts.'
      ),
      detected_at=now,
    ),
  )
  return VersionDiffResponse(changes=changes)


def detect_changes(prior: ProceedingVersion, final: ProceedingVersion) -> list[Change]:
  if prior.proceeding_id != final.proceeding_id:
    raise ValueError(
      f'proceeding_id mismatch: prior={prior.proceeding_id} final={final.proceeding_id}'
    )
  if prior.tenant_id != final.tenant_id or prior.environment != final.environment:
    raise ValueError('tenant_id / environment mismatch between prior and final versions')
  if prior.status != 'draft' or final.status != 'final':
    raise ValueError(f'expected draft→final; got {prior.status}→{final.status}')
  prompt = build_version_diff_prompt(prior, final)
  system, user = prompt.render()
  response = model_caller.call(
    task_name=TASK_NAME,
    params={
      'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
      'inputs_dict': prompt.inputs_dict,
      'prior': prior,
      'final': final,
    },
    response_type=VersionDiffResponse,
    call_spec=VERSION_DIFF_SPEC,
    tenant_id=prior.tenant_id,
    environment=prior.environment,
  )
  return list(response.changes)


def _stub_citation(version: ProceedingVersion, position: int) -> Citation:
  passage = _passage_at_position(version, position)
  return Citation(
    passage_id=passage.passage_id,
    start_offset=0,
    end_offset=min(len(passage.text), 200),
    verified=False,
    verification_rationale='pending citation_verify stage',
  )


def _passage_at_position(version: ProceedingVersion, position: int):
  for passage in version.passages:
    if passage.position == position:
      return passage
  raise ValueError(
    f'no passage at position {position} in {version.proceeding_id}:{version.version_id}'
  )
