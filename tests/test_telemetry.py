import structlog.testing

from src.telemetry import DEFAULT_LOG_LEVEL, LOG_LEVEL_ENV, configure_logging, get_logger


def test_log_level_env_is_log_level():
  assert LOG_LEVEL_ENV == 'LOG_LEVEL'


def test_default_log_level_is_info():
  assert DEFAULT_LOG_LEVEL == 'INFO'


def test_configure_logging_returns_bound_logger_with_default_level(monkeypatch):
  monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
  logger = configure_logging()
  assert hasattr(logger, 'info')
  assert hasattr(logger, 'warning')


def test_configure_logging_resolves_log_level_env_value(monkeypatch):
  # The body does `getattr(logging, level_name)` — any valid level name must
  # resolve without raising. If someone types `LOG_LEVEL=NOT_A_LEVEL` the
  # function fails loud at startup rather than silently defaulting.
  monkeypatch.setenv(LOG_LEVEL_ENV, 'DEBUG')
  logger = configure_logging()
  assert hasattr(logger, 'info')


def test_configure_logging_raises_on_unknown_log_level(monkeypatch):
  import pytest

  monkeypatch.setenv(LOG_LEVEL_ENV, 'NOT_A_LEVEL')
  with pytest.raises(AttributeError):
    configure_logging()


def test_configure_logging_emits_structlog_json(monkeypatch):
  # The body wires structlog with a JSONRenderer; proving that a bound logger
  # .info() call under capture_logs yields a dict with the event name is the
  # smallest end-to-end check of the configure body.
  monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
  configure_logging()
  with structlog.testing.capture_logs() as events:
    get_logger().info('telemetry_test_event', extra='value')
  assert any(e['event'] == 'telemetry_test_event' for e in events)


def test_get_logger_returns_bound_logger():
  logger = get_logger()
  assert hasattr(logger, 'info')
  assert hasattr(logger, 'warning')
  assert hasattr(logger, 'error')
