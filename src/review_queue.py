'''Review-queue state transitions — per-pair asset decisions.

Three entry points:

  * `enqueue_mapping(mapping, change, config)` — called by `pipeline`
    after each `map_change`. Produces a `ReviewItem` whose `status` is
    `pending` or `escalated` based on the escalation threshold. "System
    fails to a reviewer, not to a guess" (Strata product invariant):
    low-confidence outputs never silently drop and never silently
    approve.

  * `apply_asset_decisions(tenant_id, environment, review_item_id,
    inputs, actor)` — called by the FastAPI POST route when Rachel
    curates per-pair decisions. Covers both single-row and bulk UX
    (len(inputs) == 1 vs > 1). Writes N `AssetDecision`s to
    `asset_decision_store`, recomputes `ReviewItem.status` via
    `_fold_status`, and persists the updated ReviewItem. Audit-log
    emission lands in a follow-up commit (per-pair events + batch_id).

  * `_fold_status(decisions, affected_item_count)` — pure write-time
    aggregator over the per-pair decisions for one `ReviewItem`.
    Priority order: pending > escalated > overridden > approved. If
    any `(review_item_id, affected_item_index)` pair has no decision,
    status is `pending` regardless of what the decided pairs say.
    Status is a mutable column (TDD §3.7 "pragmatic hybrid"); pure
    fold-over-the-audit-log is TDD §8.8 open question and out of
    scope here.

Escalation logic (`_escalation_threshold`):
  * If any of the change's citations is unverified → use
    `config.escalation.citation_threshold` (stricter — unverified
    evidence is a red flag).
  * Otherwise → use `config.escalation.mapping_threshold`.
  * Both thresholds are tenant hyperparameters in `DomainConfig.escalation`.

Priority is `confidence * (1 + pending_count)`. At enqueue time
pending_count == len(affected_items) so the formula matches the
pre-per-pair invariant exactly. As Rachel lands decisions,
pending_count decays and the ReviewItem naturally sinks in the
queue — items with the most undecided pairs float to the top.

The `_DECISION_TO_STATUS_PRIORITY` lookup is the canonical mapping
from AssetDecision decisions to _fold_status priority — no inline
string literals at call sites (CLAUDE.md "centralized lookup
tables").
'''

from datetime import datetime, timezone

from src.config import (
  AssetDecision,
  Change,
  DomainConfig,
  Mapping,
  ReviewItem,
)
from src.stores import asset_decision_store, mapping_store, review_item_store

# Priority order used by _fold_status when a ReviewItem has at least one
# decision per affected_item (otherwise 'pending' always wins). Higher
# number = higher precedence when summarising a mixed set.
_DECISION_FOLD_PRIORITY: dict[str, int] = {
  'escalated': 3,
  'overridden': 2,
  'approved': 1,
  'dismissed': 1,
}

_FOLD_PRIORITY_TO_STATUS: dict[int, str] = {
  3: 'escalated',
  2: 'overridden',
  1: 'approved',
}


def enqueue_mapping(mapping: Mapping, change: Change, config: DomainConfig) -> ReviewItem:
  if mapping.change_id != change.change_id:
    raise ValueError(
      f'mapping.change_id={mapping.change_id} does not match '
      f'change.change_id={change.change_id}'
    )
  threshold = _escalation_threshold(change, config)
  status = 'escalated' if mapping.confidence < threshold else 'pending'
  pending_count = len(mapping.affected_items)
  priority = _compute_priority(mapping.confidence, pending_count)
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


def apply_asset_decisions(
  tenant_id: str,
  environment: str,
  review_item_id: str,
  inputs: list[dict],
  actor: str,
) -> ReviewItem:
  if not inputs:
    raise ValueError('apply_asset_decisions requires at least one input')
  review_items = review_item_store.load_all(tenant_id, environment)
  review_item = _find_review_item(review_items, review_item_id)
  mapping = _find_mapping(tenant_id, environment, review_item.mapping_id)
  now = datetime.now(timezone.utc)
  new_decisions: list[AssetDecision] = []
  for raw in inputs:
    index = raw['affected_item_index']
    if not (0 <= index < len(mapping.affected_items)):
      raise ValueError(
        f'affected_item_index={index} out of range '
        f'[0, {len(mapping.affected_items)}) for mapping {mapping.mapping_id}'
      )
    affected = mapping.affected_items[index]
    new_decisions.append(
      AssetDecision(
        decision_id=f'{review_item_id}:item-{index:03d}',
        tenant_id=tenant_id,
        environment=environment,
        review_item_id=review_item_id,
        mapping_id=mapping.mapping_id,
        affected_item_index=index,
        item_type=affected.item_type,
        item_id=affected.item_id,
        decision=raw['decision'],
        action=raw['action'],
        route=raw['route'],
        rationale=raw['rationale'],
        decided_at=now,
        actor=actor,
      )
    )
  # First-ever decision for a tenant has no asset_decisions.json on disk
  # yet — treat FileNotFoundError as empty, same pattern as
  # web_app._load_or_empty. Any other store-level failure propagates.
  try:
    all_decisions = asset_decision_store.load_all(tenant_id, environment)
  except FileNotFoundError:
    all_decisions = []
  updated_decisions = _merge_decisions(all_decisions, new_decisions)
  asset_decision_store.save_all(tenant_id, environment, updated_decisions)
  decisions_for_item = [d for d in updated_decisions if d.review_item_id == review_item_id]
  new_status = _fold_status(decisions_for_item, len(mapping.affected_items))
  pending_count = len(mapping.affected_items) - len(decisions_for_item)
  new_priority = _compute_priority(mapping.confidence, pending_count)
  updated_item = review_item.model_copy(
    update={'status': new_status, 'priority': new_priority}
  )
  review_items = [
    updated_item if i.review_item_id == review_item_id else i for i in review_items
  ]
  review_item_store.save_all(tenant_id, environment, review_items)
  return updated_item


def _merge_decisions(
  existing: list[AssetDecision], new: list[AssetDecision]
) -> list[AssetDecision]:
  by_key: dict[tuple[str, int], AssetDecision] = {
    (d.review_item_id, d.affected_item_index): d for d in existing
  }
  for d in new:
    by_key[(d.review_item_id, d.affected_item_index)] = d
  return list(by_key.values())


def _fold_status(decisions: list[AssetDecision], affected_item_count: int) -> str:
  if len(decisions) < affected_item_count:
    return 'pending'
  max_priority = max(_DECISION_FOLD_PRIORITY[d.decision] for d in decisions)
  return _FOLD_PRIORITY_TO_STATUS[max_priority]


def _find_review_item(items: list[ReviewItem], review_item_id: str) -> ReviewItem:
  for item in items:
    if item.review_item_id == review_item_id:
      return item
  raise ValueError(f'review_item_id not found: {review_item_id}')


def _find_mapping(tenant_id: str, environment: str, mapping_id: str) -> Mapping:
  for mapping in mapping_store.load_all(tenant_id, environment):
    if mapping.mapping_id == mapping_id:
      return mapping
  raise ValueError(f'mapping_id not found: {mapping_id}')


def _compute_priority(confidence: int, pending_count: int) -> int:
  return confidence * (1 + pending_count)
