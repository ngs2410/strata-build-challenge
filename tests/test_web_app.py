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
  assert 'Review Queue' in response.text
  assert "Items awaiting Rachel's decision" in response.text
  # Per-pair proposed_route surfaces on every affected item — at least
  # one of the three team strings must appear.
  assert 'market_ops' in response.text or 'legal' in response.text or 'it_metering' in response.text
  # Per-pair action/route edit UI lands in commit 5; the current cards
  # show the proposals inline but no edit controls yet.
  assert 'per-pair actions coming in UI rebuild' in response.text


def test_queue_renders_citation_span_inline_with_mark(verdant_grid_seeded):
  del verdant_grid_seeded
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  response = _client().get('/queue?tenant_id=verdant_grid&environment=prod')
  assert response.status_code == 200
  # The queue must show the passage text with the citing span wrapped
  # in <mark>, not just the passage_id — PRD §3 says Rachel defends to
  # the GC with evidence under every claim.
  assert '<mark>' in response.text
  assert '</mark>' in response.text
  # Spot-check that actual Order 2222 proceeding text is present on
  # the page (not just the passage id string).
  assert 'aggregator' in response.text.lower() or 'telemetry' in response.text.lower()
  # Verification status is surfaced per citation (verified or unverified).
  assert 'verified' in response.text


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
  assert 'Audit Log' in response.text
  assert 'Newest first' in response.text
  assert 'Rollback is tracked as open question TDD §8.8' in response.text


# Per-pair /decide POST route tests land in commit 5 alongside the queue
# UI rebuild. The old /action route (one decision per mapping) was
# removed when apply_action was replaced by apply_asset_decisions.
