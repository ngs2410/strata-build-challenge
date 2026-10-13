from datetime import datetime, timezone

import pytest

from src.citation.citation_grounding import ground_citations
from src.citation.citation_verify import verify_citations
from src.config import (
  AffectedItem,
  Change,
  Citation,
  DomainConfig,
  EscalationConfig,
  Mapping,
  Passage,
  ProceedingVersion,
)
from src.mapping import map_change
from src.obligation_extraction import extract_obligations
from src.pipeline import run_pipeline
from src.review_queue import enqueue_mapping
from src.version_diff import detect_changes


def _version(status, version_id):
  return ProceedingVersion(
    tenant_id='verdant_grid',
    environment='prod',
    proceeding_id='proc',
    version_id=version_id,
    status=status,
    docket_number='D1',
    issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    source_url='https://x',
    passages=(
      Passage(
        passage_id=f'proc:{version_id}:§1:p1',
        section_path=('§1',),
        position=1,
        text='The aggregator shall register each DER before participation.',
      ),
      Passage(
        passage_id=f'proc:{version_id}:§1:p2',
        section_path=('§1',),
        position=2,
        text='The aggregator shall provide a telemetry feed.',
      ),
      Passage(
        passage_id=f'proc:{version_id}:§1:p3',
        section_path=('§1',),
        position=3,
        text='The RTO may establish a minimum size requirement.',
      ),
    ),
  )


def _change(change_type, prior_citation=None, new_citation=None):
  return Change(
    change_id='c-1',
    tenant_id='verdant_grid',
    environment='prod',
    proceeding_id='proc',
    change_type=change_type,
    description='d',
    prior_citation=prior_citation,
    new_citation=new_citation,
    confidence=4,
    confidence_rationale='r',
    detected_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
  )


def _mapping(confidence, affected_items=(), change_id='c-1'):
  return Mapping(
    mapping_id='m',
    tenant_id='t',
    environment='e',
    change_id=change_id,
    affected_items=tuple(affected_items),
    recommended_action='review',
    routing_destination='legal',
    confidence=confidence,
    confidence_rationale='r',
    rationale='r',
  )


def _verified_citation() -> Citation:
  return Citation(
    passage_id='proc:v:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=True,
    verification_rationale='verified in test',
  )


def _verified_change(change_type='tightened'):
  return _change(
    change_type,
    prior_citation=_verified_citation(),
    new_citation=_verified_citation(),
  )


def _config(threshold):
  return DomainConfig(
    escalation=EscalationConfig(mapping_threshold=threshold, citation_threshold=3),
    task_model_configs={},
  )


def test_detect_changes_returns_three_changes_in_known_order():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  changes = detect_changes(nopr, final)
  assert [c.change_type for c in changes] == ['loosened', 'tightened', 'added']
  assert changes[2].prior_citation is None  # 'added' change has no prior
  assert changes[0].prior_citation is not None
  assert changes[0].prior_citation.passage_id == 'proc:nopr:§1:p1'


def test_detect_changes_rejects_mismatched_statuses():
  with pytest.raises(ValueError, match='draft'):
    detect_changes(_version('draft', 'a'), _version('draft', 'b'))


def test_ground_citations_clamps_end_offset_to_passage_length():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  passage_length = len(nopr.passages[0].text)
  overshoot = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=10_000,
    verified=False,
    verification_rationale='pending',
  )
  grounded = ground_citations([_change('tightened', prior_citation=overshoot)], nopr, final)
  assert grounded[0].prior_citation.end_offset == passage_length


def test_ground_citations_raises_on_unknown_passage_id():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  bogus = Citation(
    passage_id='proc:nopr:§99:p99',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale='pending',
  )
  with pytest.raises(ValueError, match='unknown passage_id'):
    ground_citations([_change('tightened', prior_citation=bogus)], nopr, final)


def test_verify_citations_sets_verified_true_for_known_passage():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  citation = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale='pending',
  )
  verified = verify_citations([_change('tightened', prior_citation=citation)], nopr, final)
  assert verified[0].prior_citation.verified is True


def test_extract_obligations_produces_one_entry_per_version():
  extracted = extract_obligations(_version('draft', 'nopr'))
  assert len(extracted) == 1
  assert extracted[0].obligation_type == 'procedural'
  assert extracted[0].citation.verified is True


@pytest.mark.parametrize(
  'change_type,expected_destination,expected_action',
  [
    ('added', 'market_ops', 'amend'),
    ('tightened', 'market_ops', 'amend'),
    ('loosened', 'legal', 'review'),
    ('cosmetic', 'market_ops', 'no_op'),
    ('removed', 'market_ops', 'review'),
  ],
)
def test_map_change_routes_by_change_type(change_type, expected_destination, expected_action):
  mapping = map_change(_change(change_type), obligations=[], projects=[], documents=[])
  assert mapping.routing_destination == expected_destination
  assert mapping.recommended_action == expected_action


def test_map_change_translates_llm_index_response_into_item_ids(verdant_grid_seeded):
  del verdant_grid_seeded
  from src.ingest import ingest_company_context

  obligations, projects, documents = ingest_company_context('verdant_grid', 'prod')
  mapping = map_change(
    _change('added'),
    obligations=obligations,
    projects=projects,
    documents=documents,
  )
  valid_obligation_ids = {o.obligation_id for o in obligations}
  valid_project_ids = {p.project_id for p in projects}
  valid_document_ids = {d.document_id for d in documents}
  valid_by_type = {
    'obligation': valid_obligation_ids,
    'project': valid_project_ids,
    'document': valid_document_ids,
  }
  for item in mapping.affected_items:
    assert item.item_id in valid_by_type[item.item_type]


def test_enqueue_mapping_escalates_below_threshold():
  item = enqueue_mapping(_mapping(confidence=2), _verified_change(), _config(threshold=3))
  assert item.status == 'escalated'


def test_enqueue_mapping_pending_at_threshold():
  item = enqueue_mapping(_mapping(confidence=3), _verified_change(), _config(threshold=3))
  assert item.status == 'pending'


def test_enqueue_mapping_priority_increases_with_affected_items():
  bigger = _mapping(
    confidence=4,
    affected_items=(
      AffectedItem(item_type='obligation', item_id='o-1', match_confidence=4, match_rationale='r'),
      AffectedItem(item_type='project', item_id='p-1', match_confidence=3, match_rationale='r'),
    ),
  )
  smaller = _mapping(confidence=4)
  change = _verified_change()
  bigger_item = enqueue_mapping(bigger, change, _config(threshold=3))
  smaller_item = enqueue_mapping(smaller, change, _config(threshold=3))
  assert bigger_item.priority > smaller_item.priority
  assert bigger_item.priority == 4 * 3  # confidence × (1 + affected_count)
  assert smaller_item.priority == 4 * 1


def test_enqueue_mapping_uses_citation_threshold_when_citation_unverified():
  config = DomainConfig(
    escalation=EscalationConfig(mapping_threshold=2, citation_threshold=5),
    task_model_configs={},
  )
  unverified = Citation(
    passage_id='proc:v:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale='unverified in test',
  )
  change = _change('tightened', prior_citation=unverified, new_citation=unverified)
  item = enqueue_mapping(_mapping(confidence=4), change, config)
  assert item.status == 'escalated'


def test_enqueue_mapping_uses_mapping_threshold_when_citations_verified():
  config = DomainConfig(
    escalation=EscalationConfig(mapping_threshold=2, citation_threshold=5),
    task_model_configs={},
  )
  item = enqueue_mapping(_mapping(confidence=4), _verified_change(), config)
  assert item.status == 'pending'


def test_enqueue_mapping_raises_on_mismatched_change_id():
  with pytest.raises(ValueError, match='does not match'):
    enqueue_mapping(
      _mapping(confidence=4, change_id='c-1'),
      _change('tightened').model_copy(update={'change_id': 'c-OTHER'}),
      _config(threshold=3),
    )


def test_pipeline_end_to_end_produces_expected_counts(verdant_grid_seeded):
  result = run_pipeline('verdant_grid', 'prod', dry_run=False)
  assert len(result.versions) == 2
  assert len(result.obligations) == 3
  assert len(result.projects) == 2
  assert len(result.documents) == 2
  assert len(result.changes) == 3
  assert len(result.extracted) == 2
  assert len(result.mappings) == 3
  assert len(result.review_items) == 3


def test_pipeline_persists_review_items_under_tenant_env_path(verdant_grid_seeded):
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  path = verdant_grid_seeded / 'verdant_grid' / 'prod' / 'review_items.json'
  assert path.is_file()


def test_pipeline_dry_run_persists_nothing_under_env_dir(verdant_grid_seeded):
  run_pipeline('verdant_grid', 'prod', dry_run=True)
  env_dir = verdant_grid_seeded / 'verdant_grid' / 'prod'
  if env_dir.exists():
    assert list(env_dir.iterdir()) == []


def test_apply_action_approve_transitions_status_and_logs_event(verdant_grid_seeded):
  from src.audit_log import audit_log_store
  from src.review_queue import apply_action
  from src.stores import review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  items_before = review_item_store.load_all('verdant_grid', 'prod')
  pending = next(i for i in items_before if i.status == 'pending')
  updated = apply_action(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    action='approve',
    actor='rachel',
    rationale='looks good',
  )
  assert updated.status == 'approved'
  items_after = review_item_store.load_all('verdant_grid', 'prod')
  stored = next(i for i in items_after if i.review_item_id == pending.review_item_id)
  assert stored.status == 'approved'
  events = audit_log_store.load_all('verdant_grid', 'prod')
  approval_events = [e for e in events if e.kind == 'review_item_approved']
  assert len(approval_events) == 1
  assert approval_events[0].target_id == pending.review_item_id
  assert approval_events[0].actor == 'rachel'
  assert approval_events[0].rationale == 'looks good'


def test_apply_action_override_sets_overridden_status(verdant_grid_seeded):
  from src.review_queue import apply_action

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  from src.stores import review_item_store

  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  updated = apply_action(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    action='override',
    actor='rachel',
    rationale='wrong mapping',
  )
  assert updated.status == 'overridden'


def test_apply_action_escalate_sets_escalated_status(verdant_grid_seeded):
  from src.review_queue import apply_action

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  from src.stores import review_item_store

  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  updated = apply_action(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    action='escalate',
    actor='rachel',
    rationale='needs GC review',
  )
  assert updated.status == 'escalated'


def test_apply_action_raises_on_unknown_action(verdant_grid_seeded):
  from src.review_queue import apply_action

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  from src.stores import review_item_store

  item = review_item_store.load_all('verdant_grid', 'prod')[0]
  with pytest.raises(ValueError, match='unknown review action'):
    apply_action(
      tenant_id='verdant_grid',
      environment='prod',
      review_item_id=item.review_item_id,
      action='bogus',  # type: ignore[arg-type]
      actor='x',
      rationale='x',
    )


def test_apply_action_raises_on_unknown_review_item_id(verdant_grid_seeded):
  from src.review_queue import apply_action

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  with pytest.raises(ValueError, match='review_item_id not found'):
    apply_action(
      tenant_id='verdant_grid',
      environment='prod',
      review_item_id='does-not-exist',
      action='approve',
      actor='x',
      rationale='x',
    )


def test_pipeline_isolates_tenants(verdant_grid_seeded):
  import shutil

  shutil.copytree(
    verdant_grid_seeded / 'verdant_grid',
    verdant_grid_seeded / 'other_tenant',
  )
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  verdant_env = verdant_grid_seeded / 'verdant_grid' / 'prod'
  other_env = verdant_grid_seeded / 'other_tenant' / 'prod'
  assert verdant_env.is_dir()
  assert not other_env.exists()
