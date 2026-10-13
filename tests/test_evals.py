from datetime import datetime, timezone
from pathlib import Path

from src.config import Change, Citation
from src.evals import (
  ExpectedChange,
  aggregate,
  evaluate_case,
  load_gold,
  render_text_report,
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
  assert agg['true_positives'] == 1
  assert agg['false_positives'] == 0
  assert agg['false_negatives'] == 1
  assert agg['precision'] == 1.0
  assert agg['recall'] == 0.5
  assert agg['f1'] == 2 / 3


def test_aggregate_builds_reliability_diagram_across_cases():
  expected = (_expected('added', None, 1),)
  predicted = [_change('c-1', 'added', None, 1, confidence=5)]
  result_a = evaluate_case(expected, predicted)
  result_a.case_id = 'a'
  predicted_b = [_change('c-2', 'added', None, 1, confidence=5)]
  result_b = evaluate_case(expected, predicted_b)
  result_b.case_id = 'b'
  agg = aggregate([result_a, result_b])
  assert agg['reliability_diagram']['5'] == {'correct': 2, 'total': 2}


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
  assert 'Precision=1.000' in text
  assert 'Reliability diagram' in text
