import re
from datetime import datetime, timezone
from pathlib import Path

import orjson

from src.config import Document, Obligation, Passage, ProceedingVersion, Project
from src.stores import DATA_DIR, document_store, obligation_store, proceeding_store, project_store

SECTION_HEADER_RE = re.compile(r'^§([\d.]+)(?:\(([^)]+)\))*\s')


def _passage_id(
  proceeding_id: str, version_id: str, section_path: tuple[str, ...], position: int
) -> str:
  section_str = (
    '§' + '('.join(section_path).rstrip('(')
    if len(section_path) == 1
    else (section_path[0] + ''.join(f'({p})' for p in section_path[1:]))
  )
  return f'{proceeding_id}:{version_id}:{section_str}:p{position}'


def _parse_passages(text: str, proceeding_id: str, version_id: str) -> tuple[Passage, ...]:
  passages: list[Passage] = []
  current_section: tuple[str, ...] = ()
  position = 0
  for raw_para in text.split('\n\n'):
    para = raw_para.strip()
    if not para:
      continue
    match = SECTION_HEADER_RE.match(para)
    if match:
      base = match.group(1)
      extras = [g for g in match.groups()[1:] if g]
      current_section = (f'§{base}', *extras)
      position = 0
      continue
    position += 1
    passage_id = _passage_id(proceeding_id, version_id, current_section, position)
    passages.append(
      Passage(
        passage_id=passage_id,
        section_path=current_section,
        position=position,
        text=para,
      )
    )
  return tuple(passages)


def ingest_proceeding_version(
  tenant_id: str,
  environment: str,
  proceeding_id: str,
  version_id: str,
  status: str,
  docket_number: str,
  issued_at: datetime,
  source_url: str,
  text_path: Path,
) -> ProceedingVersion:
  text = text_path.read_text()
  passages = _parse_passages(text, proceeding_id, version_id)
  return ProceedingVersion(
    tenant_id=tenant_id,
    environment=environment,
    proceeding_id=proceeding_id,
    version_id=version_id,
    status=status,  # type: ignore[arg-type]
    docket_number=docket_number,
    issued_at=issued_at,
    source_url=source_url,
    passages=passages,
  )


def ingest_company_context(
  tenant_id: str, environment: str
) -> tuple[list[Obligation], list[Project], list[Document]]:
  seed_path = DATA_DIR / tenant_id / 'company_context.json'
  raw = orjson.loads(seed_path.read_bytes())
  obligations = [Obligation.model_validate(o) for o in raw['obligations']]
  projects = [Project.model_validate(p) for p in raw['projects']]
  documents = [Document.model_validate(d) for d in raw['documents']]
  return obligations, projects, documents


def persist_ingest(
  tenant_id: str,
  environment: str,
  versions: list[ProceedingVersion],
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> None:
  proceeding_store.save_all(tenant_id, environment, versions)
  obligation_store.save_all(tenant_id, environment, obligations)
  project_store.save_all(tenant_id, environment, projects)
  document_store.save_all(tenant_id, environment, documents)


def ingest_verdant_grid_fixture(
  tenant_id: str, environment: str
) -> tuple[list[ProceedingVersion], list[Obligation], list[Project], list[Document]]:
  proceedings_dir = DATA_DIR / tenant_id / 'proceedings'
  nopr = ingest_proceeding_version(
    tenant_id=tenant_id,
    environment=environment,
    proceeding_id='ferc-order-2222',
    version_id='nopr-2019-11',
    status='draft',
    docket_number='RM18-9',
    issued_at=datetime(2019, 11, 21, tzinfo=timezone.utc),
    source_url='https://www.ferc.gov/sites/default/files/2020-06/E-1_0.pdf',
    text_path=proceedings_dir / 'order-2222-nopr.txt',
  )
  final = ingest_proceeding_version(
    tenant_id=tenant_id,
    environment=environment,
    proceeding_id='ferc-order-2222',
    version_id='final-2020-09',
    status='final',
    docket_number='RM18-9',
    issued_at=datetime(2020, 9, 17, tzinfo=timezone.utc),
    source_url='https://www.ferc.gov/sites/default/files/2020-09/E-1_0.pdf',
    text_path=proceedings_dir / 'order-2222-final.txt',
  )
  obligations, projects, documents = ingest_company_context(tenant_id, environment)
  return [nopr, final], obligations, projects, documents
