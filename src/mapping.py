from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.config import AffectedItem, Change, Document, Mapping, Obligation, Project
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller
from src.prompt_calibration import CONFIDENCE_LEVEL_RUBRIC

TASK_NAME = 'mapping'

MAPPING_JSON_EXAMPLE = """{
  "affected_items": [
    {
      "item_type": "<obligation|project|document>",
      "item_index": <int 0-based index into the matching candidate list>,
      "match_confidence": <int 0-5 per CONFIDENCE_LEVEL_RUBRIC>,
      "match_rationale": "<one sentence naming the specific overlap>"
    }
  ],
  "recommended_action": "<review|amend|no_op>",
  "routing_destination": "<market_ops|legal|it_metering>",
  "confidence": <int 0-5 per CONFIDENCE_LEVEL_RUBRIC>,
  "confidence_rationale": "<which rubric level you picked and why>",
  "rationale": "<one paragraph summarising WHY this change maps to these items and this team>"
}"""

STUB_RATIONALE = (
  'stub mapping: assigns affected items by naive match on obligation '
  'source_authority / project name containing the proceeding_id; real impl '
  'calls the mapping LLM task'
)


class AffectedItemByIndex(BaseModel):
  model_config = ConfigDict(extra='forbid')
  item_type: Literal['obligation', 'project', 'document']
  item_index: int
  match_confidence: int
  match_rationale: str


class MappingResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  affected_items: tuple[AffectedItemByIndex, ...]
  recommended_action: Literal['review', 'amend', 'no_op']
  routing_destination: Literal['market_ops', 'legal', 'it_metering']
  confidence: int
  confidence_rationale: str
  rationale: str


MAPPING_SPEC = CallSpec(
  purpose=(
    "For a given Change, identify which of Verdant's obligations, projects, "
    'and documents it affects; recommend a per-item action; route the '
    'overall change to its PRIMARY internal destination (one of three in v1 '
    '— multi-destination routing is a known v2 extension; see TDD §8.6).'
  ),
  acceptance_criteria=(
    'Mapping has at least one AffectedItem OR a rationale explaining why no '
    'obligation is affected; routing_destination is one of market_ops / '
    'legal / it_metering; recommended_action matches change_type semantics.'
  ),
  failure_modes=(
    '(a) Over-mapping — flagging unrelated obligations as affected. '
    '(b) Primary-destination miscall — picking the wrong primary team among '
    'market_ops / legal / it_metering when the change clearly belongs to a '
    'different one. '
    '(c) ID hallucination — picking an obligation_id not in the candidate list.'
  ),
)


def build_mapping_prompt(
  change: Change,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> PromptSpec:
  change_summary = (
    f'change_type={change.change_type}\n'
    f'description={change.description}\n'
    f'confidence={change.confidence}'
  )
  obligation_list = '\n'.join(
    f'[{i}] {o.obligation_id} ({o.obligation_type}, {o.status}): {o.summary}'
    for i, o in enumerate(obligations)
  )
  project_list = '\n'.join(
    f'[{i}] {p.project_id} ({p.status}): {p.name}' for i, p in enumerate(projects)
  )
  document_list = '\n'.join(
    f'[{i}] {d.document_id} ({d.document_type}): {d.title}' for i, d in enumerate(documents)
  )
  return PromptSpec(
    system_prompt=(
      'You are a regulatory-change impact mapper. You take a Change detected '
      'between proceeding versions and determine which of a specific '
      "company's obligations, projects, and documents it affects. You "
      'produce structured JSON output.'
    ),
    task=(
      'Map the Change below to its affected items. Recommend an action and '
      'route to the right internal team.'
    ),
    details=CONFIDENCE_LEVEL_RUBRIC,
    field_intents=(
      '- affected_items[*].item_type: one of obligation / project / document.\n'
      '- affected_items[*].item_index: 0-based index into the matching '
      'candidate list in the Inputs section (obligation_candidates, '
      'project_candidates, or document_candidates per item_type). Count '
      'the "[0]" / "[1]" / ... prefixes. Must be a valid index into that '
      'list.\n'
      '- recommended_action: one of review / amend / no_op.\n'
      '- routing_destination: one of market_ops / legal / it_metering — '
      'the single PRIMARY team accountable (TDD §8.6 scope limit).\n'
      '- confidence: integer 0-5 per the rubric in Details.'
    ),
    rubrics=(
      '- Emit affected_items by 0-based index into the matching candidate '
      'list, NOT by copying the item id string. Index-based selection '
      'prevents id hallucination.\n'
      '- recommended_action is one of review / amend / no_op.\n'
      '- routing_destination is one of market_ops / legal / it_metering. '
      'Pick the single PRIMARY team accountable for the change. Secondary '
      'teams are not modelled in v1 (TDD §8.6).\n'
      '- Report confidence as an integer 0-5 per the confidence rubric in '
      'Details; set confidence_rationale naming the rubric level and why.'
    ),
    inputs_dict={
      'change_description': change_summary,
      'obligation_candidates': obligation_list,
      'project_candidates': project_list,
      'document_candidates': document_list,
    },
    json_example=MAPPING_JSON_EXAMPLE,
  )


def stub_mapping_response(params: dict) -> MappingResponse:
  change: Change = params['change']
  obligations: list[Obligation] = params['obligations']
  projects: list[Project] = params['projects']
  documents: list[Document] = params['documents']
  by_index: list[AffectedItemByIndex] = []
  obligation_hits: set[str] = set()
  for i, obl in enumerate(obligations):
    if (
      change.proceeding_id in obl.source_authority.lower()
      or 'order 2222' in obl.source_authority.lower()
    ):
      by_index.append(
        AffectedItemByIndex(
          item_type='obligation',
          item_index=i,
          match_confidence=4,
          match_rationale=f'source_authority references {change.proceeding_id}',
        )
      )
      obligation_hits.add(obl.obligation_id)
  for i, prj in enumerate(projects):
    if any(oid in prj.related_obligations for oid in obligation_hits):
      by_index.append(
        AffectedItemByIndex(
          item_type='project',
          item_index=i,
          match_confidence=3,
          match_rationale='project relates to an obligation affected by this change',
        )
      )
  for i, doc in enumerate(documents):
    if 'der' in doc.title.lower() or 'der' in doc.summary.lower():
      by_index.append(
        AffectedItemByIndex(
          item_type='document',
          item_index=i,
          match_confidence=3,
          match_rationale='document title/summary references DER',
        )
      )
  return MappingResponse(
    affected_items=tuple(by_index),
    recommended_action=_action_from_change_type(change.change_type),
    routing_destination=_route_from_change_type(change.change_type),
    confidence=min(4, max(2, change.confidence - 1)),
    confidence_rationale='stub: inherits change confidence with a one-step uncertainty penalty',
    rationale=STUB_RATIONALE,
  )


def map_change(
  change: Change,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> Mapping:
  prompt = build_mapping_prompt(change, obligations, projects, documents)
  system, user = prompt.render()
  response = model_caller.call(
    task_name=TASK_NAME,
    params={
      'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
      'inputs_dict': prompt.inputs_dict,
      'change': change,
      'obligations': obligations,
      'projects': projects,
      'documents': documents,
    },
    response_type=MappingResponse,
    call_spec=MAPPING_SPEC,
    tenant_id=change.tenant_id,
    environment=change.environment,
  )
  affected_items = tuple(
    _resolve_affected_item(item, obligations, projects, documents)
    for item in response.affected_items
  )
  return Mapping(
    mapping_id=f'{change.change_id}:mapping',
    tenant_id=change.tenant_id,
    environment=change.environment,
    change_id=change.change_id,
    affected_items=affected_items,
    recommended_action=response.recommended_action,
    routing_destination=response.routing_destination,
    confidence=response.confidence,
    confidence_rationale=response.confidence_rationale,
    rationale=response.rationale,
  )


def _resolve_affected_item(
  item: AffectedItemByIndex,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> AffectedItem:
  if item.item_type == 'obligation':
    target_id = obligations[item.item_index].obligation_id
  elif item.item_type == 'project':
    target_id = projects[item.item_index].project_id
  else:
    target_id = documents[item.item_index].document_id
  return AffectedItem(
    item_type=item.item_type,
    item_id=target_id,
    match_confidence=item.match_confidence,
    match_rationale=item.match_rationale,
  )


def _route_from_change_type(change_type: str) -> str:
  if change_type in ('added', 'tightened'):
    return 'market_ops'
  if change_type == 'loosened':
    return 'legal'
  return 'market_ops'


def _action_from_change_type(change_type: str) -> str:
  if change_type == 'cosmetic':
    return 'no_op'
  if change_type in ('added', 'tightened'):
    return 'amend'
  return 'review'
