from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import orjson
from absl import app, flags

from src.citation.citation_grounding import ground_citations
from src.citation.citation_verify import verify_citations
from src.evals import (
  GoldCase,
  aggregate,
  evaluate_case,
  load_gold,
  render_markdown_report,
  render_text_report,
  score_extractions,
  score_mappings,
  score_verifications,
)
from src.ingest import ingest_company_context, ingest_proceeding_version
from src.mapping import map_change
from src.model_caller import model_caller
from src.obligation_extraction import extract_obligations
from src.telemetry import configure_logging, get_logger
from src.version_diff import detect_changes

FLAGS = flags.FLAGS

flags.DEFINE_string('tenant_id', 'verdant_grid', 'Tenant identifier')
flags.DEFINE_string('environment', 'prod', 'Environment (prod/staging/dev)')
flags.DEFINE_string('gold_file', 'data/evals/gold.json', 'Path to gold-set JSON')
flags.DEFINE_string('output_dir', 'output/evals', 'Directory for JSON report output')
flags.DEFINE_string(
  'md_output',
  '',
  'If set, also write a human-readable markdown report at this path. Intended '
  'for committed submission artifacts (see Makefile target eval-report).',
)
flags.DEFINE_boolean('dry_run', False, 'Compute metrics but do not write report to disk')
flags.DEFINE_integer('k', None, 'Smoke-test: run only first K gold cases')
flags.DEFINE_boolean('profile', False, 'Run under cProfile; log top 50 functions')
flags.DEFINE_boolean(
  'confirm_llm',
  False,
  'Interactively confirm each LLM call before dispatch — prints the rendered '
  'prompt + token-count estimate and prompts y/n. Useful for prompt debugging.',
)


def _run() -> None:
  model_caller.set_confirm_llm(FLAGS.confirm_llm)
  logger = get_logger()
  gold = load_gold(Path(FLAGS.gold_file))
  cases = list(gold.cases)
  if FLAGS.k is not None:
    cases = cases[: FLAGS.k]

  case_results = []
  for case in cases:
    logger.info('eval.case.start', case_id=case.case_id)
    prior = _build_version(case, 'prior')
    final = _build_version(case, 'new')
    predicted_changes = detect_changes(prior, final)
    predicted_changes = ground_citations(predicted_changes, prior, final)
    predicted_changes = verify_citations(predicted_changes, prior, final)
    result = evaluate_case(case.expected_changes, predicted_changes)
    result.case_id = case.case_id

    if case.expected_verifications is not None:
      score_verifications(result, case.expected_verifications, predicted_changes)

    predicted_extractions = []
    if case.expected_extractions is not None:
      predicted_extractions = list(extract_obligations(prior)) + list(extract_obligations(final))
      score_extractions(result, case.expected_extractions, predicted_extractions)

    predicted_mappings = []
    if case.expected_mappings is not None:
      tenant_for_context = case.company_context_tenant or FLAGS.tenant_id
      obligations, projects, documents = ingest_company_context(
        tenant_for_context, FLAGS.environment
      )
      predicted_mappings = [
        map_change(change, obligations, projects, documents) for change in predicted_changes
      ]
      score_mappings(result, case.expected_mappings, predicted_changes, predicted_mappings)

    logger.info(
      'eval.case.done',
      case_id=case.case_id,
      diff_tp=result.true_positives,
      diff_fp=result.false_positives,
      diff_fn=result.false_negatives,
      verification_correct=f'{result.verification_correct}/{result.verification_total}',
      extraction_tp=result.extraction_tp,
      mapping_item_tp=result.mapping_item_tp,
    )
    case_results.append(result)

  report = aggregate(case_results)
  text_report = render_text_report(report, case_results)
  print(text_report)

  if not FLAGS.dry_run:
    output_dir = Path(FLAGS.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    output_path = output_dir / f'{stamp}.json'
    payload = {
      'tenant_id': FLAGS.tenant_id,
      'environment': FLAGS.environment,
      'gold_file': FLAGS.gold_file,
      'generated_at': stamp,
      'aggregate': report,
      'per_case': [asdict(r) for r in case_results],
    }
    output_path.write_bytes(
      orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS)
    )
    logger.info('eval.report_written', path=str(output_path))

    if FLAGS.md_output:
      md_path = Path(FLAGS.md_output)
      md_path.parent.mkdir(parents=True, exist_ok=True)
      md_body = render_markdown_report(
        report,
        case_results,
        tenant_id=FLAGS.tenant_id,
        environment=FLAGS.environment,
        gold_file=FLAGS.gold_file,
        generated_at_iso=datetime.now(timezone.utc).isoformat(timespec='seconds'),
      )
      md_path.write_text(md_body)
      logger.info('eval.markdown_report_written', path=str(md_path))


def _build_version(case: GoldCase, which: str):
  if which == 'prior':
    version_id = case.input.prior_version_id
    text_path = Path(case.input.prior_version_file)
    status = 'draft'
  else:
    version_id = case.input.new_version_id
    text_path = Path(case.input.new_version_file)
    status = 'final'
  return ingest_proceeding_version(
    tenant_id=FLAGS.tenant_id,
    environment=FLAGS.environment,
    proceeding_id=case.input.proceeding_id,
    version_id=version_id,
    status=status,
    docket_number=case.input.docket_number,
    issued_at=datetime.now(timezone.utc),
    source_url=f'file://{text_path}',
    text_path=text_path,
  )


def main(argv):
  del argv
  configure_logging()
  if FLAGS.profile:
    import cProfile
    import pstats

    profiler = cProfile.Profile()
    profiler.enable()
    _run()
    profiler.disable()
    stats = pstats.Stats(profiler).sort_stats('cumulative')
    stats.print_stats(50)
    return
  _run()


def main_entry():
  app.run(main)


if __name__ == '__main__':
  main_entry()
