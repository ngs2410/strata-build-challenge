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
