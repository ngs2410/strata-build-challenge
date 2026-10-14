'''Eval harness — gold-set schemas + scoring + aggregation + report rendering.

Project depth area. Scores four stages against a hand-labeled gold set
(`data/evals/gold.json`), aggregates per-case results, and emits either a
text or markdown report. Pure functions; the orchestrator is
`scripts/main_eval.py` which drives the pipeline then feeds its outputs
here.

Gold-set schema (all Pydantic `extra='forbid'`):
  * `GoldSet.cases: tuple[GoldCase, ...]`
  * `GoldCase.input` names the proceeding files; `expected_changes`,
    `expected_verifications`, `expected_extractions`, `expected_mappings`
    are the per-stage labels. Verifications/extractions/mappings are
    optional per case (`None` = don't score that stage for this case).

Matching strategy — triple-key alignment:
Predicted and expected changes are keyed by
`(change_type, prior_passage_position, new_passage_position)`. Positions
come from the passage_id suffix (`p<N>` emitted by `ingest.py`). Matching
is multiset-based (`_match_triples`): tp = per-key `min(pred, exp)`,
fp/fn from the remainders. Keeps the harness independent of the LLM's
arbitrary `change_id` strings — the test is "did we find the same
semantic change in the same place?" not "did the id match?".

Metrics computed per case (`CaseResult`):
  * Change detection — tp/fp/fn, confusion matrix of `expected → predicted`
    change_type pairs, citation hits/misses (position-pair match), mean
    confidence, confidence-bucket reliability (predicted-confidence vs.
    actual-correctness), keyword coverage in the description.
  * Citation verify — accuracy of `verified` booleans against expected.
  * Obligation extraction — precision/recall/F1 keyed by
    `(version_id, passage_position)`, obligation_type accuracy, keyword
    coverage.
  * Mapping — item-level precision/recall/F1 (affected_items set compared
    per entity type), action accuracy, routing-destination accuracy.

`aggregate(results)` sums per-case counts into stage-wide
precision/recall/F1, builds the reliability diagram, and renders a confusion
dict. `render_text_report` is the stdout format;
`render_markdown_report` is the GitHub-friendly format that pins the
`(tenant, env, gold_file, generated_at)` provenance in frontmatter.

CLAUDE.md "evaluation first": every ML/LLM component ships with its eval
harness and gold data before it ships. This module is that harness.
'''

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict

from src.config import Change, ExtractedObligation, Mapping


class ExpectedChange(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_type: Literal['added', 'removed', 'tightened', 'loosened', 'cosmetic']
  prior_passage_position: int | None
  new_passage_position: int | None
  description_keywords: tuple[str, ...]


class ExpectedVerification(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_match: tuple[str, int | None, int | None]
  prior_verified: bool | None
  new_verified: bool | None


class ExpectedExtraction(BaseModel):
  model_config = ConfigDict(extra='forbid')
  version_id: str
  obligation_type: Literal['reporting', 'operational', 'procedural', 'financial']
  passage_position: int
  description_keywords: tuple[str, ...]


class ExpectedAffectedItem(BaseModel):
  model_config = ConfigDict(extra='forbid')
  item_type: Literal['obligation', 'project', 'document']
  item_id: str
  expected_action: Literal['review', 'amend', 'no_op']
  expected_route: Literal['market_ops', 'legal', 'it_metering']


class ExpectedMapping(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_match: tuple[str, int | None, int | None]
  # Per-item expectations. Each affected item carries its own expected
  # (action, route) because v1 closed TDD §8.6 (multi-reviewer routing):
  # one Change touching N items can produce N (action, route) pairs.
  affected_items: tuple[ExpectedAffectedItem, ...]


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
  expected_verifications: tuple[ExpectedVerification, ...] | None
  expected_extractions: tuple[ExpectedExtraction, ...] | None
  expected_mappings: tuple[ExpectedMapping, ...] | None
  company_context_tenant: str | None


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
  verification_total: int = 0
  verification_correct: int = 0
  extraction_tp: int = 0
  extraction_fp: int = 0
  extraction_fn: int = 0
  extraction_type_correct: int = 0
  extraction_keyword_coverage: list[float] = field(default_factory=list)
  mapping_item_tp: int = 0
  mapping_item_fp: int = 0
  mapping_item_fn: int = 0
  mapping_action_total: int = 0
  mapping_action_correct: int = 0
  mapping_route_total: int = 0
  mapping_route_correct: int = 0


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
    case_id='',
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


def score_verifications(
  result: CaseResult,
  expected: tuple[ExpectedVerification, ...],
  predicted: list[Change],
) -> None:
  by_triple: dict[tuple, Change] = {}
  for change in predicted:
    by_triple[(change.change_type, *_positions(change))] = change
  for exp in expected:
    key = tuple(exp.change_match)
    change = by_triple.get(key)
    if change is None:
      continue
    for side_verified_actual, side_verified_expected in (
      (_cite_verified(change.prior_citation), exp.prior_verified),
      (_cite_verified(change.new_citation), exp.new_verified),
    ):
      result.verification_total += 1
      if side_verified_actual == side_verified_expected:
        result.verification_correct += 1


def score_extractions(
  result: CaseResult,
  expected: tuple[ExpectedExtraction, ...],
  predicted: list[ExtractedObligation],
) -> None:
  predicted_keys: dict[tuple[str, int], ExtractedObligation] = {}
  for obligation in predicted:
    predicted_keys[(obligation.version_id, _passage_position_from_citation(obligation))] = (
      obligation
    )
  seen_keys: set[tuple[str, int]] = set()
  for exp in expected:
    key = (exp.version_id, exp.passage_position)
    obligation = predicted_keys.get(key)
    if obligation is None:
      result.extraction_fn += 1
      continue
    seen_keys.add(key)
    result.extraction_tp += 1
    if obligation.obligation_type == exp.obligation_type:
      result.extraction_type_correct += 1
    text_lower = (obligation.summary + ' ' + obligation.full_text).lower()
    if exp.description_keywords:
      hits = sum(1 for kw in exp.description_keywords if kw.lower() in text_lower)
      result.extraction_keyword_coverage.append(hits / len(exp.description_keywords))
    else:
      result.extraction_keyword_coverage.append(1.0)
  for key in predicted_keys:
    if key not in seen_keys:
      result.extraction_fp += 1


def score_mappings(
  result: CaseResult,
  expected: tuple[ExpectedMapping, ...],
  predicted_changes: list[Change],
  predicted_mappings: list[Mapping],
) -> None:
  change_triple_by_id = {c.change_id: (c.change_type, *_positions(c)) for c in predicted_changes}
  mappings_by_triple: dict[tuple, Mapping] = {}
  for mapping in predicted_mappings:
    triple = change_triple_by_id.get(mapping.change_id)
    if triple is not None:
      mappings_by_triple[triple] = mapping
  for exp in expected:
    key = tuple(exp.change_match)
    mapping = mappings_by_triple.get(key)
    if mapping is None:
      result.mapping_item_fn += len(exp.affected_items)
      continue
    pred_by_key = {(a.item_type, a.item_id): a for a in mapping.affected_items}
    exp_by_key = {(e.item_type, e.item_id): e for e in exp.affected_items}
    for exp_key, exp_item in exp_by_key.items():
      pred_item = pred_by_key.get(exp_key)
      if pred_item is None:
        result.mapping_item_fn += 1
        continue
      result.mapping_item_tp += 1
      result.mapping_action_total += 1
      if pred_item.proposed_action == exp_item.expected_action:
        result.mapping_action_correct += 1
      result.mapping_route_total += 1
      if pred_item.proposed_route == exp_item.expected_route:
        result.mapping_route_correct += 1
    for pred_key in pred_by_key:
      if pred_key not in exp_by_key:
        result.mapping_item_fp += 1


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


def _cite_verified(citation) -> bool | None:
  if citation is None:
    return None
  return citation.verified


def _passage_position_from_citation(obligation: ExtractedObligation) -> int:
  pos = _position_of(obligation.citation)
  return pos if pos is not None else -1


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


def _citation_hits(predicted, expected) -> tuple[int, int]:
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
  predicted, expected, pred_triples, exp_triples
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


def _keyword_coverage(predicted, expected, pred_triples, exp_triples) -> list[float]:
  coverage: list[float] = []
  exp_by_triple: dict[tuple, list] = {}
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


def _safe_div(numerator: int, denominator: int) -> float:
  return numerator / denominator if denominator > 0 else 0.0


def _f1(precision: float, recall: float) -> float:
  return 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0


def aggregate(results: list[CaseResult]) -> dict:
  total_tp = sum(r.true_positives for r in results)
  total_fp = sum(r.false_positives for r in results)
  total_fn = sum(r.false_negatives for r in results)
  precision = _safe_div(total_tp, total_tp + total_fp)
  recall = _safe_div(total_tp, total_tp + total_fn)
  f1 = _f1(precision, recall)
  total_cite_hits = sum(r.citation_hits for r in results)
  total_cite_misses = sum(r.citation_misses for r in results)
  citation_precision = _safe_div(total_cite_hits, total_cite_hits + total_cite_misses)
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
  verification_total = sum(r.verification_total for r in results)
  verification_correct = sum(r.verification_correct for r in results)
  verification_accuracy = _safe_div(verification_correct, verification_total)
  extraction_tp = sum(r.extraction_tp for r in results)
  extraction_fp = sum(r.extraction_fp for r in results)
  extraction_fn = sum(r.extraction_fn for r in results)
  extraction_precision = _safe_div(extraction_tp, extraction_tp + extraction_fp)
  extraction_recall = _safe_div(extraction_tp, extraction_tp + extraction_fn)
  extraction_f1 = _f1(extraction_precision, extraction_recall)
  extraction_type_correct = sum(r.extraction_type_correct for r in results)
  extraction_type_accuracy = _safe_div(extraction_type_correct, extraction_tp)
  all_extraction_coverage = [v for r in results for v in r.extraction_keyword_coverage]
  extraction_keyword_coverage = (
    sum(all_extraction_coverage) / len(all_extraction_coverage) if all_extraction_coverage else None
  )
  mapping_tp = sum(r.mapping_item_tp for r in results)
  mapping_fp = sum(r.mapping_item_fp for r in results)
  mapping_fn = sum(r.mapping_item_fn for r in results)
  mapping_item_precision = _safe_div(mapping_tp, mapping_tp + mapping_fp)
  mapping_item_recall = _safe_div(mapping_tp, mapping_tp + mapping_fn)
  mapping_item_f1 = _f1(mapping_item_precision, mapping_item_recall)
  mapping_action_total = sum(r.mapping_action_total for r in results)
  mapping_action_correct = sum(r.mapping_action_correct for r in results)
  mapping_action_accuracy = _safe_div(mapping_action_correct, mapping_action_total)
  mapping_route_total = sum(r.mapping_route_total for r in results)
  mapping_route_correct = sum(r.mapping_route_correct for r in results)
  mapping_route_accuracy = _safe_div(mapping_route_correct, mapping_route_total)
  return {
    'cases_run': len(results),
    'change_detection': {
      'precision': precision,
      'recall': recall,
      'f1': f1,
      'true_positives': total_tp,
      'false_positives': total_fp,
      'false_negatives': total_fn,
      'confusion': confusion_total,
      'citation_precision': citation_precision,
      'citation_hits': total_cite_hits,
      'citation_misses': total_cite_misses,
      'mean_confidence': mean_confidence,
      'mean_keyword_coverage': mean_keyword_coverage,
      'reliability_diagram': {
        str(b): {'correct': c, 'total': t} for b, (c, t) in reliability.items()
      },
    },
    'citation_verify': {
      'verification_total': verification_total,
      'verification_correct': verification_correct,
      'verification_accuracy': verification_accuracy,
    },
    'obligation_extraction': {
      'precision': extraction_precision,
      'recall': extraction_recall,
      'f1': extraction_f1,
      'true_positives': extraction_tp,
      'false_positives': extraction_fp,
      'false_negatives': extraction_fn,
      'obligation_type_accuracy': extraction_type_accuracy,
      'mean_keyword_coverage': extraction_keyword_coverage,
    },
    'mapping': {
      'item_precision': mapping_item_precision,
      'item_recall': mapping_item_recall,
      'item_f1': mapping_item_f1,
      'item_true_positives': mapping_tp,
      'item_false_positives': mapping_fp,
      'item_false_negatives': mapping_fn,
      'action_accuracy': mapping_action_accuracy,
      'action_total': mapping_action_total,
      'route_accuracy': mapping_route_accuracy,
      'route_total': mapping_route_total,
    },
  }


def render_text_report(report: dict, results: list[CaseResult]) -> str:
  lines = ['=== Strata Eval Report ===', f'Cases run: {report["cases_run"]}', '']
  lines.append('Per-case summary:')
  for r in results:
    citation_total = r.citation_hits + r.citation_misses
    lines.append(
      f'  [{r.case_id}] diff_tp={r.true_positives} diff_fp={r.false_positives} '
      f'diff_fn={r.false_negatives} citation={r.citation_hits}/{citation_total} '
      f'mean_conf={_fmt(r.mean_confidence)}'
    )
    if r.verification_total:
      lines.append(f'      verifications: {r.verification_correct}/{r.verification_total} correct')
    if r.extraction_tp + r.extraction_fp + r.extraction_fn:
      lines.append(
        f'      extractions: tp={r.extraction_tp} fp={r.extraction_fp} fn={r.extraction_fn} '
        f'type_correct={r.extraction_type_correct}/{r.extraction_tp}'
      )
    if r.mapping_action_total:
      lines.append(
        f'      mappings: items tp={r.mapping_item_tp} fp={r.mapping_item_fp} '
        f'fn={r.mapping_item_fn}; action {r.mapping_action_correct}/{r.mapping_action_total}; '
        f'route {r.mapping_route_correct}/{r.mapping_route_total}'
      )
  lines.append('')
  cd = report['change_detection']
  lines.append('Aggregate — change detection (version_diff):')
  lines.append(
    f'  tp={cd["true_positives"]} fp={cd["false_positives"]} fn={cd["false_negatives"]}; '
    f'P={cd["precision"]:.3f} R={cd["recall"]:.3f} F1={cd["f1"]:.3f}'
  )
  lines.append(
    f'  Citation precision: {cd["citation_hits"]}/'
    f'{cd["citation_hits"] + cd["citation_misses"]} '
    f'({cd["citation_precision"]:.3f})'
  )
  lines.append(f'  Mean confidence: {_fmt(cd["mean_confidence"])}')
  lines.append(f'  Mean keyword coverage: {_fmt(cd["mean_keyword_coverage"])}')
  lines.append('')
  lines.append('Reliability diagram (confidence_bucket: correct/total = accuracy):')
  for bucket in sorted(cd['reliability_diagram']):
    data = cd['reliability_diagram'][bucket]
    acc = data['correct'] / data['total'] if data['total'] > 0 else 0.0
    lines.append(f'  {bucket}: {data["correct"]}/{data["total"]} = {acc:.3f}')
  lines.append('')
  if cd['confusion']:
    lines.append('Change-type confusion (expected->predicted = count):')
    for key, count in sorted(cd['confusion'].items()):
      lines.append(f'  {key}: {count}')
    lines.append('')
  cv = report['citation_verify']
  lines.append('Aggregate — citation_verify:')
  lines.append(
    f'  Verification accuracy: {cv["verification_correct"]}/{cv["verification_total"]} '
    f'({cv["verification_accuracy"]:.3f})'
  )
  lines.append('')
  oe = report['obligation_extraction']
  lines.append('Aggregate — obligation_extraction:')
  lines.append(
    f'  tp={oe["true_positives"]} fp={oe["false_positives"]} fn={oe["false_negatives"]}; '
    f'P={oe["precision"]:.3f} R={oe["recall"]:.3f} F1={oe["f1"]:.3f}'
  )
  lines.append(f'  Obligation-type accuracy: {oe["obligation_type_accuracy"]:.3f}')
  lines.append(f'  Mean keyword coverage: {_fmt(oe["mean_keyword_coverage"])}')
  lines.append('')
  mp = report['mapping']
  lines.append('Aggregate — mapping:')
  lines.append(
    f'  Items: tp={mp["item_true_positives"]} fp={mp["item_false_positives"]} '
    f'fn={mp["item_false_negatives"]}; '
    f'P={mp["item_precision"]:.3f} R={mp["item_recall"]:.3f} F1={mp["item_f1"]:.3f}'
  )
  lines.append(f'  Action accuracy: {mp["action_accuracy"]:.3f} (n={mp["action_total"]})')
  lines.append(f'  Route accuracy: {mp["route_accuracy"]:.3f} (n={mp["route_total"]})')
  return '\n'.join(lines)


def render_markdown_report(
  report: dict,
  results: list[CaseResult],
  *,
  tenant_id: str,
  environment: str,
  gold_file: str,
  generated_at_iso: str,
) -> str:
  text_body = render_text_report(report, results)
  cd = report['change_detection']
  cv = report['citation_verify']
  oe = report['obligation_extraction']
  mp = report['mapping']
  rows = [
    ('change_detection', 'precision', cd['precision']),
    ('change_detection', 'recall', cd['recall']),
    ('change_detection', 'f1', cd['f1']),
    ('change_detection', 'citation_precision', cd['citation_precision']),
    ('citation_verify', 'verification_accuracy', cv['verification_accuracy']),
    ('obligation_extraction', 'f1', oe['f1']),
    ('obligation_extraction', 'type_accuracy', oe['obligation_type_accuracy']),
    ('mapping', 'item_f1', mp['item_f1']),
    ('mapping', 'action_accuracy', mp['action_accuracy']),
    ('mapping', 'route_accuracy', mp['route_accuracy']),
  ]
  lines = [
    f'# Strata eval results — {generated_at_iso}',
    '',
    f'- **Tenant:** `{tenant_id}`',
    f'- **Environment:** `{environment}`',
    f'- **Gold file:** `{gold_file}`',
    f'- **Cases run:** {report["cases_run"]}',
    '',
    '## Headline numbers',
    '',
    '| Stage                 | Metric                | Value |',
    '|-----------------------|-----------------------|-------|',
  ]
  for stage, metric, value in rows:
    lines.append(f'| {stage:<21} | {metric:<21} | {value:.3f} |')
  lines.extend(['', '## Full text report', '', '```', text_body, '```', ''])
  return '\n'.join(lines)


def _fmt(value: float | None) -> str:
  return 'n/a' if value is None else f'{value:.3f}'
