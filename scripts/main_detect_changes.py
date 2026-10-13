'''CLI entrypoint — run the end-to-end detection pipeline for a tenant/env.

Thin wrapper over `src.pipeline.run_pipeline`. Keeps the script skeleton
small (CLAUDE.md "scripts are flags + a `_run` calling into the lib")
precisely so tests NEVER import this file — module-level `absl` flags
would pollute the global `FLAGS` namespace and break any test that
re-parses flags. The pipeline library lives in `src/pipeline.py`;
callers that want to drive the pipeline from code import that.

Flags:
  * `--tenant_id` / `--environment` — the usual isolation pair, defaults
    to the demo tenant.
  * `--dry_run` — compute every stage (including LLM calls so the cache
    fills) but do not persist artefacts or append audit events.
  * `--k` — smoke-test knob; currently no-op in the stub harness
    (CLAUDE.md requires every script to expose `--k` even when the stage
    doesn't yet exercise it).
  * `--profile` — wrap `_run()` in `cProfile`, print top-50 cumulative
    time functions. The output is a performance snapshot, not a prod
    telemetry signal.
  * `--confirm_llm` — forwarded straight into `ModelCaller` so every
    dispatch prints the rendered prompt + estimated tokens and blocks
    for operator `y/n`. Does NOT force `n_jobs=1` (TDD §4.1 — fan-out
    semantics around confirm are handled inside ModelCaller).
'''

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
