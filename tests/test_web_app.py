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
  # Per-pair decision table + bulk toolbar are present.
  assert '<table class="affected-table">' in response.text
  assert 'name="affected_item_indexes"' in response.text
  assert '>Approve<' in response.text
  assert '>Dismiss<' in response.text
  assert '>Escalate<' in response.text
  assert '>Override<' in response.text


def test_queue_renders_item_titles_from_tenant_inventory(verdant_grid_seeded):
  del verdant_grid_seeded
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  response = _client().get('/queue?tenant_id=verdant_grid&environment=prod')
  assert response.status_code == 200
  # Seeded Verdant Grid DER aggregation obligations should render their
  # summary strings (not just the opaque VG-OBL-DER-* ids).
  assert 'DER' in response.text  # ids contain DER
  # One of the seeded obligation summaries contains "aggregation" — the
  # titles must flow through, not be swallowed by the queue route.
  assert 'aggregation' in response.text.lower()


def test_post_decide_single_row_creates_one_decision_and_redirects(verdant_grid_seeded):
  del verdant_grid_seeded
  from src.stores import asset_decision_store, mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  response = _client().post(
    f'/queue/{pending.review_item_id}/decide',
    data={
      'tenant_id': 'verdant_grid',
      'environment': 'prod',
      'decision': 'approved',
      'action': mapping.affected_items[0].proposed_action,
      'route': mapping.affected_items[0].proposed_route,
      'rationale': 'looks good',
      'affected_item_indexes': ['0'],
      'actor': 'rachel',
    },
  )
  assert response.status_code == 303
  assert response.headers['location'] == '/queue?tenant_id=verdant_grid&environment=prod'
  decisions = asset_decision_store.load_all('verdant_grid', 'prod')
  mine = [d for d in decisions if d.review_item_id == pending.review_item_id]
  assert len(mine) == 1
  assert mine[0].decision == 'approved'


def test_post_decide_bulk_creates_n_decisions_in_one_request(verdant_grid_seeded):
  del verdant_grid_seeded
  from src.stores import asset_decision_store, audit_log_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  # TestClient's data= doesn't preserve duplicate form keys reliably;
  # send the raw url-encoded body so Starlette's multi_items() sees
  # multiple affected_item_indexes values (which the HTML form does
  # emit from multiple checked checkboxes).
  body = (
    'tenant_id=verdant_grid&environment=prod&decision=approved'
    '&action=amend&route=legal&rationale=bulk+approve'
    '&affected_item_indexes=0&affected_item_indexes=1&actor=rachel'
  )
  response = _client().post(
    f'/queue/{pending.review_item_id}/decide',
    content=body,
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
  )
  assert response.status_code == 303
  decisions = asset_decision_store.load_all('verdant_grid', 'prod')
  mine = sorted(
    (d for d in decisions if d.review_item_id == pending.review_item_id),
    key=lambda d: d.affected_item_index,
  )
  assert len(mine) == 2
  assert [d.affected_item_index for d in mine] == [0, 1]
  # Both share the same batch_id in the audit log.
  events = audit_log_store.load_all('verdant_grid', 'prod')
  decision_events = [e for e in events if e.kind == 'asset_decision_approved']
  batch_ids = {e.batch_id for e in decision_events}
  assert len(batch_ids) == 1
  assert next(iter(batch_ids)) is not None


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
