from datetime import datetime, timezone

from src.ingest import ingest_company_context, ingest_proceeding_version

SAMPLE_TEXT = """\
§35.28(g)(1)(ix) Distributed energy resource aggregations.

(A) First paragraph with content.

(B) Second paragraph with more content.
"""


def test_ingest_proceeding_version_parses_section_headers_and_paragraphs(tmp_path):
  tp = tmp_path / 'excerpt.txt'
  tp.write_text(SAMPLE_TEXT)
  v = ingest_proceeding_version(
    tenant_id='acme',
    environment='prod',
    proceeding_id='proc',
    version_id='v1',
    status='draft',
    docket_number='D1',
    issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    source_url='https://x',
    text_path=tp,
  )
  assert v.proceeding_id == 'proc'
  assert len(v.passages) == 2
  assert v.passages[0].section_path == ('§35.28', 'g', '1', 'ix')
  assert v.passages[0].position == 1
  assert v.passages[1].position == 2
  assert '(A) First paragraph' in v.passages[0].text
  assert '(B) Second paragraph' in v.passages[1].text


def test_ingest_proceeding_version_empty_text_produces_zero_passages(tmp_path):
  tp = tmp_path / 'empty.txt'
  tp.write_text('')
  v = ingest_proceeding_version(
    tenant_id='acme',
    environment='prod',
    proceeding_id='proc',
    version_id='v1',
    status='draft',
    docket_number='D1',
    issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    source_url='https://x',
    text_path=tp,
  )
  assert v.passages == ()


def test_ingest_company_context_returns_expected_counts(verdant_grid_seeded):
  obligations, projects, documents = ingest_company_context('verdant_grid', 'prod')
  assert len(obligations) == 8
  assert len(projects) == 3
  assert len(documents) == 5
  assert all(o.tenant_id == 'verdant_grid' for o in obligations)
  assert all(p.tenant_id == 'verdant_grid' for p in projects)
  assert all(d.tenant_id == 'verdant_grid' for d in documents)
