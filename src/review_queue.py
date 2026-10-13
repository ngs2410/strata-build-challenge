'''Review-queue state transitions — enqueue, approve/override/escalate.

Two entry points:

  * `enqueue_mapping(mapping, change, config)` — called by `pipeline` after
    each `map_change`. Produces a `ReviewItem` whose `status` is `pending`
    or `escalated` based on the escalation threshold. This is the "system
    fails to a reviewer, not to a guess" invariant (Strata product
    invariant): low-confidence outputs are never silently dropped and
    never silently approved.

  * `apply_action(tenant_id, environment, review_item_id, action, actor,
    rationale)` — called by the FastAPI POST route when a reviewer
    approves / overrides / escalates. Loads the current review items,
    updates the matching one's status, writes back, and emits the
    corresponding audit event (`review_item_approved` / `_overridden` /
    `_escalated`) with both prior and new values. Rationale is required —
    the audit log is the system of record.

Escalation logic (`_escalation_threshold`):
  * If any of the change's citations is unverified → use
    `config.escalation.citation_threshold` (stricter — unverified evidence
    is a red flag).
  * Otherwise → use `config.escalation.mapping_threshold`.
  * Both thresholds are tenant hyperparameters in `DomainConfig.escalation`.

Priority is a simple product (`confidence * (1 + len(affected_items))`) —
items with high-confidence mappings AND many affected items float to the
top. v1 doesn't need a more sophisticated scoring function; swap it out via
`_compute_priority` when queue triage becomes a real workflow.

The `_ACTION_TO_STATUS` / `_ACTION_TO_EVENT_KIND` lookup tables are the
canonical mapping — no inline string literals at call sites (CLAUDE.md
"centralized lookup tables").
'''

from datetime import datetime, timezone
from typing import Literal

from src.audit_log import log_event
from src.config import Change, DomainConfig, Mapping, ReviewItem
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


def enqueue_mapping(mapping: Mapping, change: Change, config: DomainConfig) -> ReviewItem:
  if mapping.change_id != change.change_id:
    raise ValueError(
      f'mapping.change_id={mapping.change_id} does not match '
      f'change.change_id={change.change_id}'
    )
  threshold = _escalation_threshold(change, config)
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


def _escalation_threshold(change: Change, config: DomainConfig) -> int:
  if _has_unverified_citation(change):
    return config.escalation.citation_threshold
  return config.escalation.mapping_threshold


def _has_unverified_citation(change: Change) -> bool:
  for citation in (change.prior_citation, change.new_citation):
    if citation is not None and not citation.verified:
      return True
  return False


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
