from datetime import datetime, timezone

from src.config import DomainConfig, Mapping, ReviewItem


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


def _compute_priority(mapping: Mapping) -> int:
  return mapping.confidence * (1 + len(mapping.affected_items))
