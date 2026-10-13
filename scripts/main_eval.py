from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import orjson
from absl import app, flags

from src.evals import (
  GoldCase,
  aggregate,
  evaluate_case,
  load_gold,
  render_text_report,
)
from src.ingest import ingest_proceeding_version
from src.telemetry import configure_logging, get_logger
from src.version_diff import detect_changes

FLAGS = flags.FLAGS

flags.DEFINE_string('tenant_id', 'verdant_grid', 'Tenant identifier')
flags.DEFINE_string('environment', 'prod', 'Environment (prod/staging/dev)')
flags.DEFINE_string('gold_file', 'data/evals/gold.json', 'Path to gold-set JSON')
flags.DEFINE_string('output_dir', 'output/evals', 'Directory for JSON report output')
flags.DEFINE_boolean('dry_run', False, 'Compute metrics but do not write report to disk')
flags.DEFINE_integer('k', None, 'Smoke-test: run only first K gold cases')
flags.DEFINE_boolean('profile', False, 'Run under cProfile; log top 50 functions')


def _run() -> None:
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
    predicted = detect_changes(prior, final)
    result = evaluate_case(case.expected_changes, predicted)
    result.case_id = case.case_id
    logger.info(
      'eval.case.done',
      case_id=case.case_id,
      tp=result.true_positives,
      fp=result.false_positives,
      fn=result.false_negatives,
      citation_hits=result.citation_hits,
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


if __name__ == '__main__':
  app.run(main)
