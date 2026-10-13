from fastapi.testclient import TestClient

from src.pipeline import run_pipeline
from src.web_app import app


def _client() -> TestClient:
  return TestClient(app, follow_redirects=False)


def test_root_redirects_to_default_queue():
  response = _client().get('/')
  assert response.status_code == 307
  assert response.headers['location'] == '/queue?tenant_id=verdant_grid&environment=prod'


def test_queue_renders_empty_state_when_no_items(isolated_tenants_dir):
  del isolated_tenants_dir
  response = _client().get('/queue?tenant_id=verdant_grid&environment=prod')
  assert response.status_code == 200
  assert 'No review items for this tenant' in response.text


def test_queue_renders_review_items_after_pipeline(verdant_grid_seeded):
  del verdant_grid_seeded
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  response = _client().get('/queue?tenant_id=verdant_grid&environment=prod')
  assert response.status_code == 200
  assert 'review item(s)' in response.text
  assert 'Approve' in response.text
  assert 'Override' in response.text
  assert 'Escalate' in response.text
  assert 'market_ops' in response.text or 'legal' in response.text or 'it_metering' in response.text


def test_audit_renders_empty_state_when_no_events(isolated_tenants_dir):
  del isolated_tenants_dir
  response = _client().get('/audit?tenant_id=verdant_grid&environment=prod')
  assert response.status_code == 200
  assert 'No audit events for this tenant' in response.text


def test_audit_renders_events_after_pipeline(verdant_grid_seeded):
  del verdant_grid_seeded
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  response = _client().get('/audit?tenant_id=verdant_grid&environment=prod')
  assert response.status_code == 200
  assert 'event(s), newest first' in response.text
  assert 'Rollback is tracked as open question TDD §8.8' in response.text


def test_post_action_approves_and_redirects(verdant_grid_seeded):
  del verdant_grid_seeded
  from src.stores import review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  response = _client().post(
    f'/queue/{pending.review_item_id}/action',
    data={
      'tenant_id': 'verdant_grid',
      'environment': 'prod',
      'action': 'approve',
      'rationale': 'ok',
      'actor': 'rachel',
    },
  )
  assert response.status_code == 303
  assert response.headers['location'] == '/queue?tenant_id=verdant_grid&environment=prod'
  stored = next(
    i
    for i in review_item_store.load_all('verdant_grid', 'prod')
    if i.review_item_id == pending.review_item_id
  )
  assert stored.status == 'approved'


def test_post_action_rejects_invalid_action(verdant_grid_seeded):
  del verdant_grid_seeded
  from src.stores import review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  response = _client().post(
    f'/queue/{pending.review_item_id}/action',
    data={
      'tenant_id': 'verdant_grid',
      'environment': 'prod',
      'action': 'bogus',
      'rationale': 'ok',
    },
  )
  assert response.status_code == 422
