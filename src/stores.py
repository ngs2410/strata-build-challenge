from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

import orjson
from pydantic import BaseModel

from src.config import (
  AuditEvent,
  Change,
  Document,
  ExtractedObligation,
  Mapping,
  Obligation,
  ProceedingVersion,
  Project,
  ReviewItem,
)

T = TypeVar('T', bound=BaseModel)

BACKEND_JSON = 'json'
FACADE_NAME = 'stores'

DATA_DIR = Path('data/tenants')


class Store(ABC, Generic[T]):
  @abstractmethod
  def save_all(self, tenant_id: str, environment: str, items: list[T]) -> None: ...

  @abstractmethod
  def load_all(self, tenant_id: str, environment: str) -> list[T]: ...


class JsonStore(Store[T], Generic[T]):
  def __init__(self, model_type: type[T], filename: str):
    self._model_type = model_type
    self._filename = filename

  def _path(self, tenant_id: str, environment: str) -> Path:
    return DATA_DIR / tenant_id / environment / self._filename

  def save_all(self, tenant_id: str, environment: str, items: list[T]) -> None:
    path = self._path(tenant_id, environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [item.model_dump(mode='json') for item in items]
    path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

  def load_all(self, tenant_id: str, environment: str) -> list[T]:
    path = self._path(tenant_id, environment)
    data = orjson.loads(path.read_bytes())
    return [self._model_type.model_validate(d) for d in data]


class JsonlStore(Generic[T]):
  def __init__(self, model_type: type[T], filename: str):
    self._model_type = model_type
    self._filename = filename

  def _path(self, tenant_id: str, environment: str) -> Path:
    return DATA_DIR / tenant_id / environment / self._filename

  def append(self, tenant_id: str, environment: str, item: T) -> None:
    path = self._path(tenant_id, environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('ab') as f:
      f.write(orjson.dumps(item.model_dump(mode='json')))
      f.write(b'\n')

  def load_all(self, tenant_id: str, environment: str) -> list[T]:
    path = self._path(tenant_id, environment)
    items: list[T] = []
    with path.open('rb') as f:
      for line in f:
        if not line.strip():
          continue
        items.append(self._model_type.model_validate(orjson.loads(line)))
    return items


proceeding_store: Store[ProceedingVersion] = JsonStore(ProceedingVersion, 'proceedings.json')
obligation_store: Store[Obligation] = JsonStore(Obligation, 'obligations.json')
project_store: Store[Project] = JsonStore(Project, 'projects.json')
document_store: Store[Document] = JsonStore(Document, 'documents.json')
change_store: Store[Change] = JsonStore(Change, 'changes.json')
extracted_obligation_store: Store[ExtractedObligation] = JsonStore(
  ExtractedObligation, 'extracted_obligations.json'
)
mapping_store: Store[Mapping] = JsonStore(Mapping, 'mappings.json')
review_item_store: Store[ReviewItem] = JsonStore(ReviewItem, 'review_items.json')
audit_log_store: JsonlStore[AuditEvent] = JsonlStore(AuditEvent, 'audit_log.jsonl')
