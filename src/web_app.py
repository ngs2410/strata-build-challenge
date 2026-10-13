from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.review_queue import apply_action
from src.stores import audit_log_store, change_store, mapping_store, review_item_store

TEMPLATES_DIR = Path('templates')

app = FastAPI(title='Strata')
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get('/')
def root() -> RedirectResponse:
  return RedirectResponse(url='/queue?tenant_id=verdant_grid&environment=prod', status_code=307)


@app.get('/queue', response_class=HTMLResponse)
def queue(request: Request, tenant_id: str, environment: str) -> HTMLResponse:
  review_items = _load_or_empty(review_item_store, tenant_id, environment)
  mappings = {m.mapping_id: m for m in _load_or_empty(mapping_store, tenant_id, environment)}
  changes = {c.change_id: c for c in _load_or_empty(change_store, tenant_id, environment)}
  rows = []
  for item in sorted(review_items, key=lambda i: -i.priority):
    mapping = mappings.get(item.mapping_id)
    change = changes.get(mapping.change_id) if mapping is not None else None
    rows.append({'item': item, 'mapping': mapping, 'change': change})
  return templates.TemplateResponse(
    request,
    'queue.html',
    {
      'tenant_id': tenant_id,
      'environment': environment,
      'rows': rows,
    },
  )


@app.get('/audit', response_class=HTMLResponse)
def audit(request: Request, tenant_id: str, environment: str) -> HTMLResponse:
  events = _load_audit_or_empty(tenant_id, environment)
  events = sorted(events, key=lambda e: e.occurred_at, reverse=True)
  return templates.TemplateResponse(
    request,
    'audit.html',
    {
      'tenant_id': tenant_id,
      'environment': environment,
      'events': events,
    },
  )


@app.post('/queue/{review_item_id}/action')
def post_action(
  review_item_id: str,
  tenant_id: str = Form(...),
  environment: str = Form(...),
  action: Literal['approve', 'override', 'escalate'] = Form(...),
  rationale: str = Form(...),
  actor: str = Form('local_user'),
) -> RedirectResponse:
  apply_action(
    tenant_id=tenant_id,
    environment=environment,
    review_item_id=review_item_id,
    action=action,
    actor=actor,
    rationale=rationale,
  )
  return RedirectResponse(
    url=f'/queue?tenant_id={tenant_id}&environment={environment}',
    status_code=303,
  )


# The store facade raises FileNotFoundError when a tenant/env has no persisted
# items yet. For the web view "nothing to show yet" is a legitimate empty state
# (operator opened the UI before running the pipeline), so we flatten that one
# specific error to an empty list here. Any other store-level failure still
# propagates per CLAUDE.md's no-silent-fallback rule.
def _load_or_empty(store, tenant_id: str, environment: str) -> list:
  try:
    return store.load_all(tenant_id, environment)
  except FileNotFoundError:
    return []


def _load_audit_or_empty(tenant_id: str, environment: str) -> list:
  try:
    return audit_log_store.load_all(tenant_id, environment)
  except FileNotFoundError:
    return []
