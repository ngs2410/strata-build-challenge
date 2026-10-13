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
