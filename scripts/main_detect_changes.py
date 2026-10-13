from absl import app, flags

from src.model_caller import model_caller
from src.pipeline import run_pipeline
from src.telemetry import configure_logging

FLAGS = flags.FLAGS

flags.DEFINE_string('tenant_id', 'verdant_grid', 'Tenant identifier')
flags.DEFINE_string('environment', 'prod', 'Environment (prod/staging/dev)')
flags.DEFINE_boolean('dry_run', False, 'Compute pipeline outputs but do not persist')
flags.DEFINE_integer('k', None, 'Smoke-test on first K changes (currently no-op in stubs)')
flags.DEFINE_boolean('profile', False, 'Run under cProfile; log top 50 functions')
flags.DEFINE_boolean(
  'confirm_llm',
  False,
  'Interactively confirm each LLM call before dispatch — prints the rendered '
  'prompt + token-count estimate and prompts y/n. Useful for prompt debugging.',
)


def _run() -> None:
  model_caller.set_confirm_llm(FLAGS.confirm_llm)
  run_pipeline(FLAGS.tenant_id, FLAGS.environment, dry_run=FLAGS.dry_run)


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
