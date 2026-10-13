from pydantic import BaseModel, ConfigDict

from src.config import AffectedItem, Change, Document, Mapping, Obligation, Project
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller

TASK_NAME = 'mapping'

STUB_RATIONALE = (
  'stub mapping: assigns affected items by naive match on obligation '
  'source_authority / project name containing the proceeding_id; real impl '
  'calls the mapping LLM task'
)


class MappingResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  mapping: Mapping


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
    rubrics=(
      '- affected_items references items by their id (not list index).\n'
      '- recommended_action is one of review / amend / no_op.\n'
      '- routing_destination is one of market_ops / legal / it_metering.\n'
      '  Pick the single PRIMARY team accountable for the change. '
      'Secondary teams are not modelled in v1 (TDD §8.6).'
    ),
    inputs_dict={
      'change_description': change_summary,
      'obligation_candidates': obligation_list,
      'project_candidates': project_list,
      'document_candidates': document_list,
    },
  )


def stub_mapping_response(params: dict) -> MappingResponse:
  change: Change = params['change']
  obligations: list[Obligation] = params['obligations']
  projects: list[Project] = params['projects']
  documents: list[Document] = params['documents']
  affected: list[AffectedItem] = []
  for obl in obligations:
    if (
      change.proceeding_id in obl.source_authority.lower()
      or 'order 2222' in obl.source_authority.lower()
    ):
      affected.append(
        AffectedItem(
          item_type='obligation',
          item_id=obl.obligation_id,
          match_confidence=4,
          match_rationale=f'source_authority references {change.proceeding_id}',
        )
      )
  for prj in projects:
    related_obligation_hit = any(
      a.item_id in prj.related_obligations for a in affected if a.item_type == 'obligation'
    )
    if related_obligation_hit:
      affected.append(
        AffectedItem(
          item_type='project',
          item_id=prj.project_id,
          match_confidence=3,
          match_rationale='project relates to an obligation affected by this change',
        )
      )
  for doc in documents:
    if 'der' in doc.title.lower() or 'der' in doc.summary.lower():
      affected.append(
        AffectedItem(
          item_type='document',
          item_id=doc.document_id,
          match_confidence=3,
          match_rationale='document title/summary references DER',
        )
      )
  mapping = Mapping(
    mapping_id=f'{change.change_id}:mapping',
    tenant_id=change.tenant_id,
    environment=change.environment,
    change_id=change.change_id,
    affected_items=tuple(affected),
    recommended_action=_action_from_change_type(change.change_type),
    routing_destination=_route_from_change_type(change.change_type),
    confidence=min(4, max(2, change.confidence - 1)),
    confidence_rationale='stub: inherits change confidence with a one-step uncertainty penalty',
    rationale=STUB_RATIONALE,
  )
  return MappingResponse(mapping=mapping)


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
  return response.mapping


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
