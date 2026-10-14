from datetime import datetime, timezone

from src.config import AuditEvent, Obligation
from src.stores import JsonlStore, JsonStore, audit_log_store


def _obligation(obligation_id, tenant_id, environment, owner='legal'):
  return Obligation(
    obligation_id=obligation_id,
    tenant_id=tenant_id,
    environment=environment,
    obligation_type='reporting',
    summary='s',
    full_text='f',
    source_authority='a',
    status='active',
    owner=owner,
  )


def _audit_event(event_id, day, environment='prod'):
  return AuditEvent(
    event_id=event_id,
    tenant_id='acme',
    environment=environment,
    occurred_at=datetime(2020, 1, day, tzinfo=timezone.utc),
    actor='system',
    kind='change_detected',
    target_type='change',
    target_id=f't-{event_id}',
    prior_value_json=None,
    new_value_json=None,
    rationale=None,
    batch_id=None,
  )


def test_json_store_round_trips_items(isolated_tenants_dir):
  store = JsonStore(Obligation, 'obligations.json')
  item = _obligation('o-1', 'acme', 'prod', owner='market_ops')
  store.save_all('acme', 'prod', [item])
  loaded = store.load_all('acme', 'prod')
  assert len(loaded) == 1
  assert loaded[0].obligation_id == 'o-1'
  assert loaded[0].owner == 'market_ops'


def test_json_store_isolates_by_tenant(isolated_tenants_dir):
  store = JsonStore(Obligation, 'obligations.json')
  store.save_all('acme', 'prod', [_obligation('o-a', 'acme', 'prod')])
  store.save_all('beta', 'prod', [_obligation('o-b', 'beta', 'prod')])
  assert [o.obligation_id for o in store.load_all('acme', 'prod')] == ['o-a']
  assert [o.obligation_id for o in store.load_all('beta', 'prod')] == ['o-b']


def test_json_store_isolates_by_environment(isolated_tenants_dir):
  store = JsonStore(Obligation, 'obligations.json')
  prod = _obligation('o-prod', 'acme', 'prod')
  staging = _obligation('o-stg', 'acme', 'staging')
  store.save_all('acme', 'prod', [prod])
  store.save_all('acme', 'staging', [staging])
  assert [o.obligation_id for o in store.load_all('acme', 'prod')] == ['o-prod']
  assert [o.obligation_id for o in store.load_all('acme', 'staging')] == ['o-stg']


def test_jsonl_store_append_preserves_order(isolated_tenants_dir):
  store = JsonlStore(AuditEvent, 'audit.jsonl')
  for i in range(3):
    store.append('acme', 'prod', _audit_event(f'e-{i}', day=1 + i))
  loaded = store.load_all('acme', 'prod')
  assert [e.event_id for e in loaded] == ['e-0', 'e-1', 'e-2']


def test_audit_log_store_writes_under_isolated_dir(isolated_tenants_dir):
  event = _audit_event('e-x', day=1)
  audit_log_store.append('acme', 'prod', event)
  expected = isolated_tenants_dir / 'acme' / 'prod' / 'audit_log.jsonl'
  assert expected.is_file()
  loaded = audit_log_store.load_all('acme', 'prod')
  assert [e.event_id for e in loaded] == ['e-x']
