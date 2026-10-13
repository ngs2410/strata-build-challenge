from datetime import datetime, timezone
from typing import Literal

from src.audit_log import log_event
from src.config import DomainConfig, Mapping, ReviewItem
from src.stores import review_item_store

ReviewAction = Literal['approve', 'override', 'escalate']

_ACTION_TO_STATUS: dict[str, str] = {
  'approve': 'approved',
  'override': 'overridden',
  'escalate': 'escalated',
}

_ACTION_TO_EVENT_KIND: dict[str, str] = {
  'approve': 'review_item_approved',
  'override': 'review_item_overridden',
  'escalate': 'review_item_escalated',
}


def enqueue_mapping(mapping: Mapping, config: DomainConfig) -> ReviewItem:
  threshold = config.escalation.mapping_threshold
  status = 'escalated' if mapping.confidence < threshold else 'pending'
  priority = _compute_priority(mapping)
  return ReviewItem(
    review_item_id=f'{mapping.mapping_id}:review',
    tenant_id=mapping.tenant_id,
    environment=mapping.environment,
    mapping_id=mapping.mapping_id,
    priority=priority,
    status=status,  # type: ignore[arg-type]
    created_at=datetime.now(timezone.utc),
  )


def apply_action(
  tenant_id: str,
  environment: str,
  review_item_id: str,
  action: ReviewAction,
  actor: str,
  rationale: str,
) -> ReviewItem:
  if action not in _ACTION_TO_STATUS:
    raise ValueError(f'unknown review action: {action!r}')
  items = review_item_store.load_all(tenant_id, environment)
  prior_item = _find_item(items, review_item_id)
  new_status = _ACTION_TO_STATUS[action]
  updated_item = prior_item.model_copy(update={'status': new_status})
  items = [updated_item if i.review_item_id == review_item_id else i for i in items]
  review_item_store.save_all(tenant_id, environment, items)
  log_event(
    tenant_id,
    environment,
    kind=_ACTION_TO_EVENT_KIND[action],
    target_type='review_item',
    target_id=review_item_id,
    prior_value=prior_item,
    new_value=updated_item,
    rationale=rationale,
    actor=actor,
  )
  return updated_item


def _find_item(items: list[ReviewItem], review_item_id: str) -> ReviewItem:
  for item in items:
    if item.review_item_id == review_item_id:
      return item
  raise ValueError(f'review_item_id not found: {review_item_id}')


def _compute_priority(mapping: Mapping) -> int:
  return mapping.confidence * (1 + len(mapping.affected_items))
