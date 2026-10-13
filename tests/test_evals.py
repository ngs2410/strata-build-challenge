from datetime import datetime, timezone
from pathlib import Path

from src.config import AffectedItem, Change, Citation, ExtractedObligation, Mapping
from src.evals import (
  CaseResult,
  ExpectedChange,
  ExpectedExtraction,
  ExpectedMapping,
  ExpectedVerification,
  aggregate,
  evaluate_case,
  load_gold,
  render_markdown_report,
  render_text_report,
  score_extractions,
  score_mappings,
  score_verifications,
)


def _citation(proceeding_id: str, version_id: str, position: int) -> Citation:
  return Citation(
    passage_id=f'{proceeding_id}:{version_id}:§1:p{position}',
    start_offset=0,
    end_offset=50,
    verified=True,
    verification_rationale='r',
  )


def _change(
  change_id: str,
  change_type: str,
  prior_position: int | None,
  new_position: int | None,
  confidence: int = 4,
  description: str = 'd',
) -> Change:
  prior = _citation('proc', 'nopr', prior_position) if prior_position is not None else None
  new = _citation('proc', 'final', new_position) if new_position is not None else None
  return Change(
    change_id=change_id,
    tenant_id='t',
    environment='e',
    proceeding_id='proc',
    change_type=change_type,
    description=description,
    prior_citation=prior,
    new_citation=new,
    confidence=confidence,
    confidence_rationale='r',
    detected_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
  )


def _expected(
  change_type: str,
  prior_position: int | None,
  new_position: int | None,
  description_keywords: tuple[str, ...] = (),
) -> ExpectedChange:
  return ExpectedChange(
    change_type=change_type,
    prior_passage_position=prior_position,
    new_passage_position=new_position,
    description_keywords=description_keywords,
  )


def test_evaluate_case_perfect_match_three_changes():
  expected = (
    _expected('loosened', 1, 1),
    _expected('tightened', 2, 2),
    _expected('added', None, 3),
  )
  predicted = [
    _change('c-1', 'loosened', 1, 1),
    _change('c-2', 'tightened', 2, 2),
    _change('c-3', 'added', None, 3),
  ]
  result = evaluate_case(expected, predicted)
  assert result.true_positives == 3
  assert result.false_positives == 0
  assert result.false_negatives == 0
  assert result.citation_hits == 3
  assert result.citation_misses == 0


def test_evaluate_case_counts_missing_and_extra():
  expected = (
    _expected('loosened', 1, 1),
    _expected('tightened', 2, 2),
  )
  predicted = [
    _change('c-1', 'loosened', 1, 1),
    _change('c-2', 'added', None, 5),
  ]
  result = evaluate_case(expected, predicted)
  assert result.true_positives == 1
  assert result.false_positives == 1
  assert result.false_negatives == 1


def test_evaluate_case_detects_change_type_confusion():
  expected = (_expected('tightened', 1, 1),)
  predicted = [_change('c-1', 'loosened', 1, 1)]
  result = evaluate_case(expected, predicted)
  assert result.true_positives == 0
  assert result.false_positives == 1
  assert result.false_negatives == 1
  assert result.change_type_confusion == {'tightened->loosened': 1}


def test_evaluate_case_citation_miss_when_position_wrong():
  expected = (_expected('loosened', 1, 1),)
  predicted = [_change('c-1', 'loosened', 7, 7)]
  result = evaluate_case(expected, predicted)
  assert result.citation_hits == 0
  assert result.citation_misses == 1


def test_evaluate_case_keyword_coverage_counts_matched_keywords():
  expected = (_expected('added', None, 3, description_keywords=('telemetry', 'feed')),)
  predicted = [
    _change('c-1', 'added', None, 3, description='Final rule adds a Telemetry requirement.'),
  ]
  result = evaluate_case(expected, predicted)
  assert result.keyword_coverage == [0.5]


def test_evaluate_case_mean_confidence_averages_predicted():
  expected = (_expected('added', None, 1),)
  predicted = [
    _change('c-1', 'added', None, 1, confidence=2),
    _change('c-2', 'added', None, 2, confidence=4),
  ]
  result = evaluate_case(expected, predicted)
  assert result.mean_confidence == 3.0


def test_evaluate_case_confidence_buckets_capture_correctness():
  expected = (_expected('added', None, 1), _expected('loosened', 2, 2))
  predicted = [
    _change('c-1', 'added', None, 1, confidence=5),
    _change('c-2', 'loosened', 2, 2, confidence=2),
    _change('c-3', 'tightened', 3, 3, confidence=4),
  ]
  result = evaluate_case(expected, predicted)
  assert result.confidence_buckets[5] == (1, 1)
  assert result.confidence_buckets[2] == (1, 1)
  assert result.confidence_buckets[4] == (0, 1)


def test_aggregate_computes_f1_from_case_counts():
  expected = (_expected('loosened', 1, 1), _expected('tightened', 2, 2))
  predicted = [_change('c-1', 'loosened', 1, 1)]
  result = evaluate_case(expected, predicted)
  result.case_id = 'c1'
  agg = aggregate([result])
  cd = agg['change_detection']
  assert cd['true_positives'] == 1
  assert cd['false_positives'] == 0
  assert cd['false_negatives'] == 1
  assert cd['precision'] == 1.0
  assert cd['recall'] == 0.5
  assert cd['f1'] == 2 / 3


def test_aggregate_builds_reliability_diagram_across_cases():
  expected = (_expected('added', None, 1),)
  predicted = [_change('c-1', 'added', None, 1, confidence=5)]
  result_a = evaluate_case(expected, predicted)
  result_a.case_id = 'a'
  predicted_b = [_change('c-2', 'added', None, 1, confidence=5)]
  result_b = evaluate_case(expected, predicted_b)
  result_b.case_id = 'b'
  agg = aggregate([result_a, result_b])
  assert agg['change_detection']['reliability_diagram']['5'] == {'correct': 2, 'total': 2}


def test_load_gold_parses_real_gold_file():
  gold = load_gold(Path('data/evals/gold.json'))
  assert len(gold.cases) == 1
  case = gold.cases[0]
  assert case.case_id == 'order-2222-section-g-1-ix'
  assert case.input.docket_number == 'RM18-9'
  assert len(case.expected_changes) == 3
  assert case.expected_changes[2].change_type == 'added'
  assert case.expected_changes[2].prior_passage_position is None


def test_render_text_report_includes_aggregate_and_per_case_lines():
  expected = (_expected('loosened', 1, 1),)
  predicted = [_change('c-1', 'loosened', 1, 1)]
  result = evaluate_case(expected, predicted)
  result.case_id = 'case-1'
  agg = aggregate([result])
  text = render_text_report(agg, [result])
  assert 'Strata Eval Report' in text
  assert 'case-1' in text
  assert 'F1=1.000' in text
  assert 'Reliability diagram' in text


# ---- score_verifications ----


def _empty_result() -> CaseResult:
  return CaseResult(
    case_id='',
    predicted_count=0,
    expected_count=0,
    true_positives=0,
    false_positives=0,
    false_negatives=0,
  )


def test_score_verifications_counts_matched_prior_and_new():
  change = _change('c-1', 'loosened', 1, 1)
  change = change.model_copy(
    update={
      'prior_citation': change.prior_citation.model_copy(update={'verified': True}),
      'new_citation': change.new_citation.model_copy(update={'verified': False}),
    }
  )
  expected = (
    ExpectedVerification(change_match=('loosened', 1, 1), prior_verified=True, new_verified=True),
  )
  result = _empty_result()
  score_verifications(result, expected, [change])
  assert result.verification_total == 2
  assert result.verification_correct == 1


def test_score_verifications_skips_expected_without_matching_change():
  change = _change('c-1', 'added', None, 3)
  change = change.model_copy(
    update={'new_citation': change.new_citation.model_copy(update={'verified': True})}
  )
  expected = (
    ExpectedVerification(change_match=('tightened', 2, 2), prior_verified=True, new_verified=True),
  )
  result = _empty_result()
  score_verifications(result, expected, [change])
  assert result.verification_total == 0


def test_score_verifications_handles_null_side_for_added_change():
  change = _change('c-1', 'added', None, 3)
  change = change.model_copy(
    update={'new_citation': change.new_citation.model_copy(update={'verified': True})}
  )
  expected = (
    ExpectedVerification(change_match=('added', None, 3), prior_verified=None, new_verified=True),
  )
  result = _empty_result()
  score_verifications(result, expected, [change])
  assert result.verification_total == 2
  assert result.verification_correct == 2


# ---- score_extractions ----


def _extracted(version_id: str, position: int, obligation_type: str, text: str = 'xyz'):
  cite = Citation(
    passage_id=f'proc:{version_id}:§1:p{position}',
    start_offset=0,
    end_offset=50,
    verified=True,
    verification_rationale='r',
  )
  return ExtractedObligation(
    extraction_id=f'proc:{version_id}:ext-{position}',
    tenant_id='t',
    environment='e',
    proceeding_id='proc',
    version_id=version_id,
    obligation_type=obligation_type,
    summary='s',
    full_text=text,
    citation=cite,
    confidence=4,
    confidence_rationale='r',
  )


def test_score_extractions_counts_tp_fp_fn_by_version_and_position():
  expected = (
    ExpectedExtraction(
      version_id='nopr',
      obligation_type='procedural',
      passage_position=1,
      description_keywords=('register',),
    ),
    ExpectedExtraction(
      version_id='nopr',
      obligation_type='operational',
      passage_position=2,
      description_keywords=('telemetry',),
    ),
  )
  predicted = [
    _extracted('nopr', 1, 'procedural', text='register before participation'),
    _extracted('nopr', 3, 'procedural', text='something else'),
  ]
  result = _empty_result()
  score_extractions(result, expected, predicted)
  assert result.extraction_tp == 1
  assert result.extraction_fp == 1
  assert result.extraction_fn == 1
  assert result.extraction_type_correct == 1


def test_score_extractions_tracks_obligation_type_mismatch():
  expected = (
    ExpectedExtraction(
      version_id='nopr',
      obligation_type='operational',
      passage_position=1,
      description_keywords=(),
    ),
  )
  predicted = [_extracted('nopr', 1, 'procedural')]
  result = _empty_result()
  score_extractions(result, expected, predicted)
  assert result.extraction_tp == 1
  assert result.extraction_type_correct == 0


def test_score_extractions_computes_keyword_coverage():
  expected = (
    ExpectedExtraction(
      version_id='nopr',
      obligation_type='procedural',
      passage_position=1,
      description_keywords=('register', 'der'),
    ),
  )
  predicted = [_extracted('nopr', 1, 'procedural', text='Register each DER before use.')]
  result = _empty_result()
  score_extractions(result, expected, predicted)
  assert result.extraction_keyword_coverage == [1.0]


# ---- score_mappings ----


def _mapping(
  change_id: str,
  obligation_ids: tuple[str, ...] = (),
  project_ids: tuple[str, ...] = (),
  document_ids: tuple[str, ...] = (),
  action: str = 'review',
  destination: str = 'legal',
) -> Mapping:
  items = []
  for oid in obligation_ids:
    items.append(
      AffectedItem(item_type='obligation', item_id=oid, match_confidence=4, match_rationale='r')
    )
  for pid in project_ids:
    items.append(
      AffectedItem(item_type='project', item_id=pid, match_confidence=4, match_rationale='r')
    )
  for did in document_ids:
    items.append(
      AffectedItem(item_type='document', item_id=did, match_confidence=4, match_rationale='r')
    )
  return Mapping(
    mapping_id=f'{change_id}:mapping',
    tenant_id='t',
    environment='e',
    change_id=change_id,
    affected_items=tuple(items),
    recommended_action=action,
    routing_destination=destination,
    confidence=4,
    confidence_rationale='r',
    rationale='r',
  )


def test_score_mappings_counts_affected_items_tp_fp_fn():
  change = _change('c-1', 'loosened', 1, 1)
  mapping = _mapping(
    'c-1',
    obligation_ids=('O1', 'O2'),  # expected O1 only → O2 is FP
    project_ids=(),
    document_ids=(),
    action='review',
    destination='legal',
  )
  expected = (
    ExpectedMapping(
      change_match=('loosened', 1, 1),
      affected_obligation_ids=('O1',),
      affected_project_ids=('P1',),  # missing → FN
      affected_document_ids=(),
      recommended_action='review',
      routing_destination='legal',
    ),
  )
  result = _empty_result()
  score_mappings(result, expected, [change], [mapping])
  assert result.mapping_item_tp == 1  # O1
  assert result.mapping_item_fp == 1  # O2
  assert result.mapping_item_fn == 1  # P1


def test_score_mappings_tracks_action_and_route_accuracy():
  change = _change('c-1', 'loosened', 1, 1)
  mapping = _mapping('c-1', obligation_ids=('O1',), action='amend', destination='legal')
  expected = (
    ExpectedMapping(
      change_match=('loosened', 1, 1),
      affected_obligation_ids=('O1',),
      affected_project_ids=(),
      affected_document_ids=(),
      recommended_action='review',  # mismatch
      routing_destination='legal',  # match
    ),
  )
  result = _empty_result()
  score_mappings(result, expected, [change], [mapping])
  assert result.mapping_action_correct == 0
  assert result.mapping_action_total == 1
  assert result.mapping_route_correct == 1
  assert result.mapping_route_total == 1


def test_score_mappings_handles_missing_predicted_change():
  expected = (
    ExpectedMapping(
      change_match=('tightened', 2, 2),
      affected_obligation_ids=('O1', 'O2'),
      affected_project_ids=('P1',),
      affected_document_ids=(),
      recommended_action='amend',
      routing_destination='market_ops',
    ),
  )
  result = _empty_result()
  score_mappings(result, expected, [], [])
  assert result.mapping_item_fn == 3  # 2 obligations + 1 project
  assert result.mapping_action_total == 0


# ---- aggregate ----


def test_aggregate_includes_per_stage_sections():
  change = _change('c-1', 'loosened', 1, 1)
  change = change.model_copy(
    update={'new_citation': change.new_citation.model_copy(update={'verified': True})}
  )
  expected_changes = (_expected('loosened', 1, 1),)
  result = evaluate_case(expected_changes, [change])
  result.case_id = 'case-1'
  score_verifications(
    result,
    (
      ExpectedVerification(change_match=('loosened', 1, 1), prior_verified=True, new_verified=True),
    ),
    [change],
  )
  score_extractions(
    result,
    (
      ExpectedExtraction(
        version_id='nopr', obligation_type='procedural', passage_position=1, description_keywords=()
      ),
    ),
    [_extracted('nopr', 1, 'procedural')],
  )
  score_mappings(
    result,
    (
      ExpectedMapping(
        change_match=('loosened', 1, 1),
        affected_obligation_ids=('O1',),
        affected_project_ids=(),
        affected_document_ids=(),
        recommended_action='review',
        routing_destination='legal',
      ),
    ),
    [change],
    [_mapping('c-1', obligation_ids=('O1',), action='review', destination='legal')],
  )
  agg = aggregate([result])
  assert agg['change_detection']['f1'] == 1.0
  assert agg['citation_verify']['verification_total'] == 2
  assert agg['obligation_extraction']['f1'] == 1.0
  assert agg['mapping']['item_f1'] == 1.0
  assert agg['mapping']['action_accuracy'] == 1.0


def test_load_gold_parses_extended_fields():
  gold = load_gold(Path('data/evals/gold.json'))
  case = gold.cases[0]
  assert case.expected_verifications is not None
  assert len(case.expected_verifications) == 3
  assert case.expected_verifications[2].prior_verified is None
  assert case.expected_extractions is not None
  assert len(case.expected_extractions) == 5
  assert case.expected_mappings is not None
  assert len(case.expected_mappings) == 3
  assert case.company_context_tenant == 'verdant_grid'


def test_render_markdown_report_includes_headline_table_and_text_body():
  change = _change('c-1', 'loosened', 1, 1)
  expected_changes = (_expected('loosened', 1, 1),)
  result = evaluate_case(expected_changes, [change])
  result.case_id = 'case-1'
  agg = aggregate([result])
  md = render_markdown_report(
    agg,
    [result],
    tenant_id='verdant_grid',
    environment='prod',
    gold_file='data/evals/gold.json',
    generated_at_iso='2026-10-13T00:00:00+00:00',
  )
  assert md.startswith('# Strata eval results — 2026-10-13T00:00:00+00:00')
  assert '## Headline numbers' in md
  assert '| change_detection      | precision             |' in md
  assert '| mapping               | route_accuracy        |' in md
  assert '## Full text report' in md
  assert '```' in md
  assert 'Cases run: 1' in md
