from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict

from src.config import Change


class ExpectedChange(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_type: Literal['added', 'removed', 'tightened', 'loosened', 'cosmetic']
  prior_passage_position: int | None
  new_passage_position: int | None
  description_keywords: tuple[str, ...]


class GoldCaseInput(BaseModel):
  model_config = ConfigDict(extra='forbid')
  docket_number: str
  proceeding_id: str
  prior_version_id: str
  new_version_id: str
  prior_version_file: str
  new_version_file: str


class GoldCase(BaseModel):
  model_config = ConfigDict(extra='forbid')
  case_id: str
  input: GoldCaseInput
  expected_changes: tuple[ExpectedChange, ...]


class GoldSet(BaseModel):
  model_config = ConfigDict(extra='forbid')
  cases: tuple[GoldCase, ...]


@dataclass
class CaseResult:
  case_id: str
  predicted_count: int
  expected_count: int
  true_positives: int
  false_positives: int
  false_negatives: int
  change_type_confusion: dict[str, int] = field(default_factory=dict)
  citation_hits: int = 0
  citation_misses: int = 0
  mean_confidence: float | None = None
  confidence_buckets: dict[int, tuple[int, int]] = field(default_factory=dict)
  keyword_coverage: list[float] = field(default_factory=list)


def load_gold(path: Path) -> GoldSet:
  raw = orjson.loads(path.read_bytes())
  return GoldSet.model_validate(raw)


def evaluate_case(expected: tuple[ExpectedChange, ...], predicted: list[Change]) -> CaseResult:
  pred_triples = [(_type(c), *_positions(c)) for c in predicted]
  exp_triples = [
    (e.change_type, e.prior_passage_position, e.new_passage_position) for e in expected
  ]
  pred_set = _multiset(pred_triples)
  exp_set = _multiset(exp_triples)
  tp, fp, fn, confusion = _match_triples(pred_set, exp_set, pred_triples, exp_triples)
  citation_hits, citation_misses = _citation_hits(predicted, expected)
  buckets = _confidence_buckets(predicted, expected, pred_triples, exp_triples)
  mean_conf = sum(c.confidence for c in predicted) / len(predicted) if predicted else None
  keyword_coverage = _keyword_coverage(predicted, expected, pred_triples, exp_triples)
  return CaseResult(
    case_id='',  # filled by caller
    predicted_count=len(predicted),
    expected_count=len(expected),
    true_positives=tp,
    false_positives=fp,
    false_negatives=fn,
    change_type_confusion=confusion,
    citation_hits=citation_hits,
    citation_misses=citation_misses,
    mean_confidence=mean_conf,
    confidence_buckets=buckets,
    keyword_coverage=keyword_coverage,
  )


def _type(change: Change) -> str:
  return change.change_type


def _positions(change: Change) -> tuple[int | None, int | None]:
  return _position_of(change.prior_citation), _position_of(change.new_citation)


def _position_of(citation) -> int | None:
  if citation is None:
    return None
  suffix = citation.passage_id.rsplit(':p', 1)
  if len(suffix) < 2:
    return None
  try:
    return int(suffix[-1])
  except ValueError:
    return None


def _multiset(triples: list[tuple]) -> dict[tuple, int]:
  counts: dict[tuple, int] = {}
  for t in triples:
    counts[t] = counts.get(t, 0) + 1
  return counts


def _match_triples(
  pred: dict[tuple, int],
  exp: dict[tuple, int],
  pred_list: list[tuple],
  exp_list: list[tuple],
) -> tuple[int, int, int, dict[str, int]]:
  tp = 0
  all_keys = set(pred) | set(exp)
  for key in all_keys:
    tp += min(pred.get(key, 0), exp.get(key, 0))
  fp = sum(pred.values()) - tp
  fn = sum(exp.values()) - tp
  confusion = _change_type_confusion(pred_list, exp_list)
  return tp, fp, fn, confusion


def _change_type_confusion(pred_list: list[tuple], exp_list: list[tuple]) -> dict[str, int]:
  confusion: dict[str, int] = {}
  remaining_exp = list(exp_list)
  for p_type, p_prior, p_new in pred_list:
    paired = False
    for i, (e_type, e_prior, e_new) in enumerate(remaining_exp):
      if (p_prior, p_new) == (e_prior, e_new):
        key = f'{e_type}->{p_type}'
        confusion[key] = confusion.get(key, 0) + 1
        remaining_exp.pop(i)
        paired = True
        break
    if not paired:
      key = f'(unmatched)->{p_type}'
      confusion[key] = confusion.get(key, 0) + 1
  for e_type, _, _ in remaining_exp:
    key = f'{e_type}->(missing)'
    confusion[key] = confusion.get(key, 0) + 1
  return confusion


def _citation_hits(
  predicted: list[Change], expected: tuple[ExpectedChange, ...]
) -> tuple[int, int]:
  hits = 0
  misses = 0
  expected_pairs = [(e.prior_passage_position, e.new_passage_position) for e in expected]
  for change in predicted:
    pos = _positions(change)
    if pos in expected_pairs:
      hits += 1
      expected_pairs.remove(pos)
    else:
      misses += 1
  return hits, misses


def _confidence_buckets(
  predicted: list[Change],
  expected: tuple[ExpectedChange, ...],
  pred_triples: list[tuple],
  exp_triples: list[tuple],
) -> dict[int, tuple[int, int]]:
  del expected
  exp_counts = _multiset(exp_triples)
  used: dict[tuple, int] = {}
  buckets: dict[int, tuple[int, int]] = {}
  for change, triple in zip(predicted, pred_triples):
    bucket = change.confidence
    is_match = used.get(triple, 0) < exp_counts.get(triple, 0)
    if is_match:
      used[triple] = used.get(triple, 0) + 1
    correct, total = buckets.get(bucket, (0, 0))
    buckets[bucket] = (correct + (1 if is_match else 0), total + 1)
  return buckets


def _keyword_coverage(
  predicted: list[Change],
  expected: tuple[ExpectedChange, ...],
  pred_triples: list[tuple],
  exp_triples: list[tuple],
) -> list[float]:
  coverage: list[float] = []
  exp_by_triple: dict[tuple, list[ExpectedChange]] = {}
  for e, triple in zip(expected, exp_triples):
    exp_by_triple.setdefault(triple, []).append(e)
  for change, triple in zip(predicted, pred_triples):
    candidates = exp_by_triple.get(triple)
    if not candidates:
      continue
    exp_change = candidates.pop(0)
    if not exp_change.description_keywords:
      coverage.append(1.0)
      continue
    description_lower = change.description.lower()
    hits = sum(1 for kw in exp_change.description_keywords if kw.lower() in description_lower)
    coverage.append(hits / len(exp_change.description_keywords))
  return coverage


def aggregate(results: list[CaseResult]) -> dict:
  total_tp = sum(r.true_positives for r in results)
  total_fp = sum(r.false_positives for r in results)
  total_fn = sum(r.false_negatives for r in results)
  precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
  recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
  f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
  total_cite_hits = sum(r.citation_hits for r in results)
  total_cite_misses = sum(r.citation_misses for r in results)
  citation_precision = (
    total_cite_hits / (total_cite_hits + total_cite_misses)
    if (total_cite_hits + total_cite_misses) > 0
    else 0.0
  )
  mean_confidence_values = [r.mean_confidence for r in results if r.mean_confidence is not None]
  mean_confidence = (
    sum(mean_confidence_values) / len(mean_confidence_values) if mean_confidence_values else None
  )
  reliability: dict[int, tuple[int, int]] = {}
  for r in results:
    for bucket, (correct, total) in r.confidence_buckets.items():
      prev_correct, prev_total = reliability.get(bucket, (0, 0))
      reliability[bucket] = (prev_correct + correct, prev_total + total)
  confusion_total: dict[str, int] = {}
  for r in results:
    for key, count in r.change_type_confusion.items():
      confusion_total[key] = confusion_total.get(key, 0) + count
  all_keyword_coverages: list[float] = []
  for r in results:
    all_keyword_coverages.extend(r.keyword_coverage)
  mean_keyword_coverage = (
    sum(all_keyword_coverages) / len(all_keyword_coverages) if all_keyword_coverages else None
  )
  return {
    'cases_run': len(results),
    'precision': precision,
    'recall': recall,
    'f1': f1,
    'true_positives': total_tp,
    'false_positives': total_fp,
    'false_negatives': total_fn,
    'citation_precision': citation_precision,
    'citation_hits': total_cite_hits,
    'citation_misses': total_cite_misses,
    'mean_confidence': mean_confidence,
    'reliability_diagram': {
      str(b): {'correct': c, 'total': t} for b, (c, t) in reliability.items()
    },
    'change_type_confusion': confusion_total,
    'mean_keyword_coverage': mean_keyword_coverage,
  }


def render_text_report(report: dict, results: list[CaseResult]) -> str:
  lines = ['=== Strata Eval Report ===', f'Cases run: {report["cases_run"]}', '']
  lines.append('Per-case results:')
  for r in results:
    lines.append(
      f'  [{r.case_id}] predicted={r.predicted_count} expected={r.expected_count} '
      f'tp={r.true_positives} fp={r.false_positives} fn={r.false_negatives} '
      f'citation_hits={r.citation_hits}/{r.citation_hits + r.citation_misses} '
      f'mean_conf={_fmt(r.mean_confidence)}'
    )
  lines.append('')
  lines.append('Aggregate:')
  lines.append(
    f'  Change-detection: tp={report["true_positives"]} '
    f'fp={report["false_positives"]} fn={report["false_negatives"]}'
  )
  lines.append(
    f'  Precision={report["precision"]:.3f} Recall={report["recall"]:.3f} F1={report["f1"]:.3f}'
  )
  lines.append(
    f'  Citation precision: {report["citation_hits"]}/'
    f'{report["citation_hits"] + report["citation_misses"]} '
    f'({report["citation_precision"]:.3f})'
  )
  lines.append(f'  Mean confidence: {_fmt(report["mean_confidence"])}')
  lines.append(f'  Mean keyword coverage: {_fmt(report["mean_keyword_coverage"])}')
  lines.append('')
  lines.append('Reliability diagram (confidence_bucket: correct/total = accuracy):')
  for bucket in sorted(report['reliability_diagram']):
    data = report['reliability_diagram'][bucket]
    acc = data['correct'] / data['total'] if data['total'] > 0 else 0.0
    lines.append(f'  {bucket}: {data["correct"]}/{data["total"]} = {acc:.3f}')
  lines.append('')
  if report['change_type_confusion']:
    lines.append('Change-type confusion (expected->predicted = count):')
    for key, count in sorted(report['change_type_confusion'].items()):
      lines.append(f'  {key}: {count}')
  return '\n'.join(lines)


def _fmt(value: float | None) -> str:
  return 'n/a' if value is None else f'{value:.3f}'
