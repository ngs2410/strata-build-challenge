from src.config import AffectedItem, Change, Document, Mapping, Obligation, Project

STUB_RATIONALE = (
  'stub mapping: assigns affected items by naive match on obligation source_authority / '
  'project name containing the proceeding_id; real impl calls the mapping LLM task'
)


def map_change(
  change: Change,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> Mapping:
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
  routing = _route_from_change_type(change.change_type)
  action = _action_from_change_type(change.change_type)
  return Mapping(
    mapping_id=f'{change.change_id}:mapping',
    tenant_id=change.tenant_id,
    environment=change.environment,
    change_id=change.change_id,
    affected_items=tuple(affected),
    recommended_action=action,
    routing_destination=routing,
    confidence=min(4, max(2, change.confidence - 1)),
    confidence_rationale='stub: inherits change confidence with a one-step uncertainty penalty',
    rationale=STUB_RATIONALE,
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
