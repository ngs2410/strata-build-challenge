'''Append-only audit log — the system of record for every state transition.

`log_event(...)` is the only legitimate writer. Every meaningful event in
the pipeline (proceeding ingested, change detected, mapping proposed,
review item created, asset decisions approved/overridden/dismissed/
escalated, rollback executed) is appended as one `AuditEvent` to
`audit_log_store` (a `JsonlStore[AuditEvent]` backed by
`data/tenants/<tenant>/<env>/audit_log.jsonl`).

Why append-only (Strata product invariant):
  * Project state (`current_status`, `current_owner`, `current_action`) is
    a FOLD over this log, not a mutable column anywhere.
  * Rollback-to-timestamp is a replay operation (TDD §8.8 — deferred as an
    open question, but the log is already shaped to support it).
  * Both prior and new serialized values are captured on every mutation,
    so a replay has everything it needs to reconstruct any point in time
    without consulting the current state tables.

Event id composition: `'<tenant_id>:<environment>:<microseconds since
epoch>:<counter>'`. The counter is a process-local
`itertools.count()` — bulk-apply_asset_decisions loops can emit N
events in the same microsecond, so a monotonic suffix is needed to keep
event_ids unique. When concurrent writers across processes become a
thing, swap for a ULID or UUIDv7 — the Pydantic field type is a `str` so
the change is local.

`batch_id` plumbs through for bulk operations (one Rachel click
decides N pairs) so audit.html can collapse the N events into one
timeline entry. None for single-row and non-review events.

`SYSTEM_ACTOR = 'system'` is the default actor for pipeline-emitted
events. Reviewer-driven events (approve/override/escalate from the web
app) pass the authenticated username (today: `local_user` placeholder from
the FastAPI form).
'''

import itertools
from datetime import datetime, timezone

from pydantic import BaseModel

from src.config import AuditEvent
from src.stores import audit_log_store

SYSTEM_ACTOR = 'system'

_event_counter = itertools.count()


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
  batch_id: str | None = None,
) -> AuditEvent:
  now = datetime.now(timezone.utc)
  counter = next(_event_counter)
  event_id = f'{tenant_id}:{environment}:{int(now.timestamp() * 1_000_000)}:{counter}'
  event = AuditEvent(
    event_id=event_id,
    tenant_id=tenant_id,
    environment=environment,
    occurred_at=now,
    actor=actor,
    kind=kind,  # type: ignore[arg-type]
    target_type=target_type,  # type: ignore[arg-type]
    target_id=target_id,
    prior_value_json=prior_value.model_dump_json() if prior_value else None,
    new_value_json=new_value.model_dump_json() if new_value else None,
    rationale=rationale,
    batch_id=batch_id,
  )
  audit_log_store.append(tenant_id, environment, event)
  return event
