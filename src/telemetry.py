'''Structlog configuration — JSON logs to stderr, one call at startup.

`configure_logging()` is called exactly once per process (CLI entrypoint or
FastAPI startup) and installs a `JSONRenderer` on both `structlog` and the
stdlib `logging` root. Every subsequent `get_logger()` returns a bound
structlog logger that emits structured events to stderr; stdout stays clean
for program output.

`LOG_LEVEL` is read from the environment with `DEFAULT_LOG_LEVEL = 'INFO'`.
This is one of the few legitimate env-var reads in the codebase
(CLAUDE.md "env-vs-config": platform/library-level knobs are OK; configuration
belongs in bootstrap/`DomainConfig`). `.env` is loaded via `python-dotenv`
at the top of `configure_logging` so a local run picks up developer-set
levels without wrapping the CLI.

v1 is structlog-to-stderr; OpenTelemetry collector + Grafana stack is a
post-wedge addition if observability needs grow (CLAUDE.md).
'''

import logging
import os
import sys

import structlog
from dotenv import load_dotenv

LOG_LEVEL_ENV = 'LOG_LEVEL'
DEFAULT_LOG_LEVEL = 'INFO'


def configure_logging() -> structlog.stdlib.BoundLogger:
  load_dotenv()
  level_name = os.environ.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)
  level = getattr(logging, level_name)
  logging.basicConfig(format='%(message)s', stream=sys.stderr, level=level)
  structlog.configure(
    processors=[
      structlog.processors.add_log_level,
      structlog.processors.TimeStamper(fmt='iso'),
      structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(level),
    cache_logger_on_first_use=True,
  )
  return structlog.get_logger()


def get_logger() -> structlog.stdlib.BoundLogger:
  return structlog.get_logger()
