'''CLI entrypoint — launch the FastAPI review-queue + audit-log web view.

Uvicorn wrapper around `src.web_app:app`. Dev-run shape only — v1 ships
the FastAPI app on a developer's host (CLAUDE.md "FastAPI + Jinja2 server
templates — no JS framework in v1"); production shape is OIDC auth +
RLS-bound sessions + containerized deployment (TDD §4.5 / §7 Future).

Flags:
  * `--host` — defaults to `127.0.0.1` (loopback). Bind `0.0.0.0` only
    when you need LAN access and the firewall is set up.
  * `--port` — defaults to 8000.
  * `--reload` — uvicorn auto-reload; dev convenience, do NOT set in prod
    (watches the filesystem and reloads on edit).

Routes exposed by `src.web_app`: `GET /` (redirect to demo tenant queue),
`GET /queue?tenant_id=&environment=`, `GET /audit?tenant_id=&environment=`,
`POST /queue/{review_item_id}/action` (form: action + rationale).
'''

import uvicorn
from absl import app, flags
from absl import logging as absl_logging

from src.telemetry import configure_logging

FLAGS = flags.FLAGS

flags.DEFINE_string('host', '127.0.0.1', 'Host interface to bind')
flags.DEFINE_integer('port', 8000, 'Port to listen on')
flags.DEFINE_boolean('reload', False, 'Enable uvicorn auto-reload (dev only)')


def main(argv):
  del argv
  configure_logging()
  absl_logging.info(
    'Serving Strata review UI on http://%s:%d (reload=%s)',
    FLAGS.host,
    FLAGS.port,
    FLAGS.reload,
  )
  uvicorn.run(
    'src.web_app:app',
    host=FLAGS.host,
    port=FLAGS.port,
    reload=FLAGS.reload,
  )


if __name__ == '__main__':
  app.run(main)
