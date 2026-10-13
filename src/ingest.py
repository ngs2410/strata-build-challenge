r'''Load proceeding text files + the company-context seed into Pydantic models.

Entry point: `ingest_tenant_fixture(tenant_id, environment)` — reads
`data/tenants/<tenant>/proceedings/manifest.json`, walks each entry's
`text_file`, parses it into `Passage`-level chunks, and also loads
`company_context.json` for the tenant's obligations/projects/documents.
`persist_ingest(...)` is a separate step so callers can inspect the parsed
models in dry-run before writing to the stores (CLAUDE.md "I/O and pure
functions separately").

Passage parsing is deterministic:
  * Text is split on blank lines (`\n\n`); each non-empty paragraph is one
    passage.
  * Section-header lines matching `^§([\d.]+)(\([^)]+\))*` set the current
    `section_path` tuple and reset `position` to 0. Trailing `(A)(1)(i)`
    style subsections become additional path elements.
  * `passage_id = '<proceeding_id>:<version_id>:§<base>(sub1)(sub2):p<pos>'`
    — stable across runs for the same text, which is critical for the
    citation-grounding substring step downstream (if the text doesn't
    change, the id doesn't change).

Isolation: `company_context.json` lives at the TENANT root, not under an
environment subdir — the seed is shared across the tenant's environments.
Proceeding fixtures live under the tenant root for the same reason. All
derived artefacts (parsed ProceedingVersions, downstream changes, mappings)
land under `data/tenants/<tenant>/<env>/` per the stores facade.

Fail-fast: Pydantic validation errors on malformed company_context JSON
propagate (CLAUDE.md: never add defaults to accommodate stale data).
'''

import re
from datetime import datetime
from pathlib import Path

import orjson

from src.config import Document, Obligation, Passage, ProceedingVersion, Project
from src.stores import DATA_DIR, document_store, obligation_store, proceeding_store, project_store

SECTION_HEADER_RE = re.compile(r'^§([\d.]+)((?:\([^)]+\))*)\s')
SECTION_SUB_RE = re.compile(r'\(([^)]+)\)')

PROCEEDINGS_DIRNAME = 'proceedings'
PROCEEDINGS_MANIFEST_FILENAME = 'manifest.json'


def _passage_id(
  proceeding_id: str, version_id: str, section_path: tuple[str, ...], position: int
) -> str:
  if not section_path:
    section_str = ''
  else:
    section_str = section_path[0] + ''.join(f'({p})' for p in section_path[1:])
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
      subs = tuple(SECTION_SUB_RE.findall(match.group(2)))
      current_section = (f'§{base}', *subs)
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


def ingest_tenant_fixture(
  tenant_id: str, environment: str
) -> tuple[list[ProceedingVersion], list[Obligation], list[Project], list[Document]]:
  proceedings_dir = DATA_DIR / tenant_id / PROCEEDINGS_DIRNAME
  manifest_path = proceedings_dir / PROCEEDINGS_MANIFEST_FILENAME
  manifest = orjson.loads(manifest_path.read_bytes())
  versions = [
    ingest_proceeding_version(
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=entry['proceeding_id'],
      version_id=entry['version_id'],
      status=entry['status'],
      docket_number=entry['docket_number'],
      issued_at=datetime.fromisoformat(entry['issued_at'].replace('Z', '+00:00')),
      source_url=entry['source_url'],
      text_path=proceedings_dir / entry['text_file'],
    )
    for entry in manifest['proceedings']
  ]
  obligations, projects, documents = ingest_company_context(tenant_id, environment)
  return versions, obligations, projects, documents
