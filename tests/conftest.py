import shutil
import tempfile
from pathlib import Path

import pytest

SEED_SRC = Path('data/tenants/verdant_grid')

# Env subdirs under a tenant hold generated pipeline output; the fixture must
# never copy them into the test tempdir — doing so leaks stale state across
# runs (and defeats dry_run-persists-nothing tests).
SEED_IGNORE = shutil.ignore_patterns('prod', 'staging', 'dev')


@pytest.fixture
def isolated_tenants_dir(monkeypatch, tmp_path):
  override = tmp_path / 'tenants'
  tmp_root = Path(tempfile.gettempdir())
  if tmp_root not in override.parents:
    raise RuntimeError(f'test fixture must write under {tmp_root}; got {override}')
  override.mkdir()
  monkeypatch.setattr('src.stores.DATA_DIR', override)
  monkeypatch.setattr('src.ingest.DATA_DIR', override)
  return override


@pytest.fixture
def verdant_grid_seeded(isolated_tenants_dir):
  dest = isolated_tenants_dir / 'verdant_grid'
  shutil.copytree(SEED_SRC, dest, ignore=SEED_IGNORE)
  return isolated_tenants_dir
