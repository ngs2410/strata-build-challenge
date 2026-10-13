from absl import app, flags

from src.audit_log import log_event
from src.citation.citation_grounding import ground_citations
from src.citation.citation_verify import verify_citations
from src.config import load_domain_config
from src.ingest import ingest_verdant_grid_fixture, persist_ingest
from src.mapping import map_change
from src.obligation_extraction import extract_obligations
from src.review_queue import enqueue_mapping
from src.stores import (
  change_store,
  extracted_obligation_store,
  mapping_store,
  review_item_store,
)
from src.telemetry import configure_logging, get_logger
from src.version_diff import detect_changes

FLAGS = flags.FLAGS

flags.DEFINE_string('tenant_id', 'verdant_grid', 'Tenant identifier')
flags.DEFINE_string('environment', 'prod', 'Environment (prod/staging/dev)')
flags.DEFINE_boolean('dry_run', False, 'Compute pipeline outputs but do not persist')
flags.DEFINE_integer('k', None, 'Smoke-test on first K changes (currently no-op in stubs)')
flags.DEFINE_boolean('profile', False, 'Run under cProfile; log top 50 functions')


def _run() -> None:
  logger = get_logger()
  tenant_id = FLAGS.tenant_id
  environment = FLAGS.environment
  config = load_domain_config(tenant_id, environment)

  logger.info('ingest.start', tenant_id=tenant_id, environment=environment)
  versions, obligations, projects, documents = ingest_verdant_grid_fixture(tenant_id, environment)
  if not FLAGS.dry_run:
    persist_ingest(tenant_id, environment, versions, obligations, projects, documents)
  for v in versions:
    log_event(
      tenant_id,
      environment,
      kind='proceeding_ingested',
      target_type='proceeding_version',
      target_id=f'{v.proceeding_id}:{v.version_id}',
      new_value=v,
    )
  logger.info(
    'ingest.done',
    proceedings=len(versions),
    obligations=len(obligations),
    projects=len(projects),
    documents=len(documents),
  )

  nopr = next(v for v in versions if v.status == 'draft')
  final = next(v for v in versions if v.status == 'final')

  logger.info('diff.start', prior=nopr.version_id, final=final.version_id)
  changes = detect_changes(nopr, final)
  changes = ground_citations(changes, nopr, final)
  changes = verify_citations(changes, nopr, final)
  if not FLAGS.dry_run:
    change_store.save_all(tenant_id, environment, changes)
  for c in changes:
    log_event(
      tenant_id,
      environment,
      kind='change_detected',
      target_type='change',
      target_id=c.change_id,
      new_value=c,
    )
  logger.info('diff.done', changes=len(changes))

  logger.info('extract.start')
  extracted: list = []
  for v in versions:
    extracted.extend(extract_obligations(v))
  if not FLAGS.dry_run:
    extracted_obligation_store.save_all(tenant_id, environment, extracted)
  logger.info('extract.done', extracted=len(extracted))

  logger.info('map.start')
  mappings = [map_change(c, obligations, projects, documents) for c in changes]
  if not FLAGS.dry_run:
    mapping_store.save_all(tenant_id, environment, mappings)
  for m in mappings:
    log_event(
      tenant_id,
      environment,
      kind='mapping_proposed',
      target_type='mapping',
      target_id=m.mapping_id,
      new_value=m,
    )
  logger.info('map.done', mappings=len(mappings))

  logger.info('review_queue.start')
  review_items = [enqueue_mapping(m, config) for m in mappings]
  if not FLAGS.dry_run:
    review_item_store.save_all(tenant_id, environment, review_items)
  for r in review_items:
    log_event(
      tenant_id,
      environment,
      kind='review_item_created',
      target_type='review_item',
      target_id=r.review_item_id,
      new_value=r,
    )
  logger.info(
    'review_queue.done',
    review_items=len(review_items),
    escalated=sum(1 for r in review_items if r.status == 'escalated'),
    pending=sum(1 for r in review_items if r.status == 'pending'),
  )

  logger.info(
    'pipeline.done',
    tenant_id=tenant_id,
    environment=environment,
    dry_run=FLAGS.dry_run,
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
