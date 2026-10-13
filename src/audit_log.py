from datetime import datetime, timezone

from pydantic import BaseModel

from src.config import AuditEvent
from src.stores import audit_log_store

SYSTEM_ACTOR = 'system'


def log_event(
  tenant_id: str,
  environment: str,
  kind: str,
  target_type: str,
  target_id: str,
  new_value: BaseModel | None = None,
  prior_value: BaseModel | None = None,
  rationale: str | None = None,
  actor: str = SYSTEM_ACTOR,
) -> AuditEvent:
  event_id = f'{tenant_id}:{environment}:{int(datetime.now(timezone.utc).timestamp() * 1_000_000)}'
  event = AuditEvent(
    event_id=event_id,
    tenant_id=tenant_id,
    environment=environment,
    occurred_at=datetime.now(timezone.utc),
    actor=actor,
    kind=kind,  # type: ignore[arg-type]
    target_type=target_type,  # type: ignore[arg-type]
    target_id=target_id,
    prior_value_json=prior_value.model_dump_json() if prior_value else None,
    new_value_json=new_value.model_dump_json() if new_value else None,
    rationale=rationale,
  )
  audit_log_store.append(tenant_id, environment, event)
  return event
