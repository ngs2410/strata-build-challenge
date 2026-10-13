'''End-to-end pipeline orchestrator — ingest → diff → ground → verify → extract → map → enqueue.

`run_pipeline(tenant_id, environment, dry_run=False)` runs every stage in
order and returns a `PipelineResult` with the fully populated artefacts.
This is the one orchestration surface — scripts and tests both drive the
pipeline through this function; neither constructs stages ad hoc.

Stages, in order (one `logger.info('<stage>.start' / .done', ...)` pair
per stage):

  1. **Ingest** — `ingest_tenant_fixture` parses the proceeding text files
     + company_context seed into Pydantic models; `persist_ingest` writes
     them to the stores.
  2. **Diff** — `detect_changes(prior, final)` runs the `version_diff` LLM
     task; `ground_citations_for_changes` substring-matches each
     `LlmCitation(citing_span)` to compute `(start_offset, end_offset)` on
     the real passage text.
  3. **Verify (optional)** — if `DomainConfig.citation.verification_required`
     is true, `verify_citations` runs the `citation_verify` LLM task over
     every grounded citation and sets `verified` + `verification_rationale`.
     Skipped when false (logged).
  4. **Extract** — `extract_obligations(version)` once per version; results
     are grounded by `ground_obligations`.
  5. **Map** — `map_change(c, obligations, projects, documents)` once per
     change. Sequential loop at v1 scale (~5 changes); CLAUDE.md
     fan-out-at-scale rule says refactor to `fan_out_for_task` only when
     cardinality crosses ~100.
  6. **Enqueue** — `enqueue_mapping(m, change, config)` applies the
     `EscalationConfig` thresholds (mapping confidence AND citation
     confidence) and creates a `ReviewItem` with status `pending` or
     `escalated`. The system fails to a reviewer, not to a guess (Strata
     invariant).

Audit discipline: every produced artefact (version, change, mapping,
review_item) is appended to the audit log via `log_event(...)` — the
JSONL append-only log is the system of record, and current state is a
fold over it (Strata product invariant).

Dry-run contract: `dry_run=True` runs every stage AND makes every LLM
call (so the diskcache fills and operators can inspect prompts via
`--confirm_llm`), but DOES NOT persist any artefact and does NOT write
audit events. The in-memory `PipelineResult` is still returned so callers
can inspect.
'''

from dataclasses import dataclass, field

from src.audit_log import log_event
from src.citation.citation_grounding import ground_citations_for_changes, ground_obligations
from src.citation.citation_verify import verify_citations
from src.config import (
  Change,
  Document,
  DomainConfig,
  ExtractedObligation,
  Mapping,
  Obligation,
  ProceedingVersion,
  Project,
  ReviewItem,
  load_domain_config,
)
from src.ingest import ingest_tenant_fixture, persist_ingest
from src.mapping import map_change
from src.obligation_extraction import extract_obligations
from src.review_queue import enqueue_mapping
from src.stores import (
  change_store,
  extracted_obligation_store,
  mapping_store,
  review_item_store,
)
from src.telemetry import get_logger
from src.version_diff import detect_changes


@dataclass
class PipelineResult:
  config: DomainConfig | None = None
  versions: list[ProceedingVersion] = field(default_factory=list)
  obligations: list[Obligation] = field(default_factory=list)
  projects: list[Project] = field(default_factory=list)
  documents: list[Document] = field(default_factory=list)
  changes: list[Change] = field(default_factory=list)
  extracted: list[ExtractedObligation] = field(default_factory=list)
  mappings: list[Mapping] = field(default_factory=list)
  review_items: list[ReviewItem] = field(default_factory=list)


def run_pipeline(tenant_id: str, environment: str, dry_run: bool = False) -> PipelineResult:
  logger = get_logger()
  result = PipelineResult(config=load_domain_config(tenant_id, environment))

  logger.info('ingest.start', tenant_id=tenant_id, environment=environment)
  versions, obligations, projects, documents = ingest_tenant_fixture(tenant_id, environment)
  result.versions = versions
  result.obligations = obligations
  result.projects = projects
  result.documents = documents
  if not dry_run:
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
  llm_changes = detect_changes(nopr, final)
  changes = ground_citations_for_changes(llm_changes, nopr, final)
  if result.config.citation.verification_required:
    changes = verify_citations(changes, nopr, final)
  else:
    logger.info('verify.skipped', reason='citation.verification_required=false')
  result.changes = changes
  if not dry_run:
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
  extracted: list[ExtractedObligation] = []
  for v in versions:
    llm_extracted = extract_obligations(v)
    extracted.extend(ground_obligations(llm_extracted, v))
  result.extracted = extracted
  if not dry_run:
    extracted_obligation_store.save_all(tenant_id, environment, extracted)
  logger.info('extract.done', extracted=len(extracted))

  logger.info('map.start')
  mappings = [map_change(c, obligations, projects, documents) for c in changes]
  result.mappings = mappings
  if not dry_run:
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
  changes_by_id = {c.change_id: c for c in changes}
  review_items = [enqueue_mapping(m, changes_by_id[m.change_id], result.config) for m in mappings]
  result.review_items = review_items
  if not dry_run:
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

  logger.info('pipeline.done', tenant_id=tenant_id, environment=environment, dry_run=dry_run)
  return result
