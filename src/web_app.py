'''FastAPI web view — Rachel's review-queue + audit-log pages.

Programmable surface per CLAUDE.md "API first": HTTP JSON (and here,
Jinja2-rendered HTML) is the v1 consumer-facing shape. Three GETs today;
the per-pair /decide POST lands in commit 5 (queue UI rebuild).

  * `GET /` — redirects to the queue for the hard-coded demo tenant
    (`verdant_grid` / `prod`). Convenience for the build-challenge demo.
  * `GET /queue?tenant_id=...&environment=...` — joins review_items,
    mappings, and changes in Python then renders `queue.html`. Items are
    sorted by priority desc (highest first). Each row dict is
    `{item, mapping, change}` with None-tolerant joins because a mapping
    may be missing a change reference in degraded data (though in the
    happy path it never is).
  * `GET /audit?tenant_id=...&environment=...` — renders the full audit
    log newest-first.
  * `POST /queue/{review_item_id}/decide` (commit 5) — form-encoded
    per-pair decisions (checkbox-selected affected_item_indexes + one
    decision + one action + one route + one rationale). Delegates to
    `src.review_queue.apply_asset_decisions`. Redirects back to the
    queue (303 See Other).

Static files are mounted at `/static` from `./static/` (holds
`strata.css` — the single stylesheet, no JS framework per CLAUDE.md v1
rule). Templates live in `./templates/`; the `_base.html` shell is the
three-pane layout both `queue.html` and `audit.html` extend. The
template context dict passes `active_nav='queue'` or `'audit'` so the
sidebar highlights the current page.

Tenant / environment plumbing: both are query parameters on every GET
and form fields on the POST — explicit, matching the CLAUDE.md invariant
that `environment` is a sibling of `tenant_id` everywhere.

The one hedged try/except in this module (`_load_or_empty`) is
deliberately scoped to `FileNotFoundError` from the store facade — the
operator viewing the UI before running the pipeline is a legitimate empty
state, not a bug. Every other exception propagates per the CLAUDE.md
no-silent-fallback rule. The comment inline there is the "intentional
default" marker.

v1 is dev-run (`uvicorn src.web_app:app`); production shape is OIDC auth +
RLS-bound sessions + per-tenant deployment (TDD §4.5 Future). Containerized
deployment on tenant-chosen cloud or on-premise is a post-wedge option
(PRD §6, TDD §7 Scaling Pathway).
'''

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.stores import (
  audit_log_store,
  change_store,
  mapping_store,
  proceeding_store,
  review_item_store,
)

TEMPLATES_DIR = Path('templates')
STATIC_DIR = Path('static')

app = FastAPI(title='Strata')
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get('/')
def root() -> RedirectResponse:
  return RedirectResponse(url='/queue?tenant_id=verdant_grid&environment=prod', status_code=307)


@app.get('/queue', response_class=HTMLResponse)
def queue(request: Request, tenant_id: str, environment: str) -> HTMLResponse:
  review_items = _load_or_empty(review_item_store, tenant_id, environment)
  mappings = {m.mapping_id: m for m in _load_or_empty(mapping_store, tenant_id, environment)}
  changes = {c.change_id: c for c in _load_or_empty(change_store, tenant_id, environment)}
  passages_by_id = _build_passages_by_id(tenant_id, environment)
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
      'passages_by_id': passages_by_id,
      'active_nav': 'queue',
    },
  )


def _build_passages_by_id(tenant_id: str, environment: str) -> dict:
  versions = _load_or_empty(proceeding_store, tenant_id, environment)
  return {p.passage_id: p for v in versions for p in v.passages}


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
      'active_nav': 'audit',
    },
  )


# Per-pair /decide POST route lands in commit 5 alongside the queue UI
# rebuild. The old /action POST route (one decision per mapping) was
# deleted when `apply_action` was replaced by `apply_asset_decisions`.


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
