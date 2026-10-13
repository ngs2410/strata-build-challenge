import shutil
import tempfile
from pathlib import Path

import diskcache
import pytest

SEED_SRC = Path('data/tenants/verdant_grid')

# Env subdirs under a tenant hold generated pipeline output; the fixture must
# never copy them into the test tempdir — doing so leaks stale state across
# runs (and defeats dry_run-persists-nothing tests).
SEED_IGNORE = shutil.ignore_patterns('prod', 'staging', 'dev')


@pytest.fixture(autouse=True)
def isolated_model_cache(monkeypatch, tmp_path):
  from src import model_caller as mc_mod

  cache_dir = tmp_path / 'model_cache'
  tmp_root = Path(tempfile.gettempdir())
  if tmp_root not in cache_dir.parents:
    raise RuntimeError(f'test fixture must write under {tmp_root}; got {cache_dir}')
  cache_dir.mkdir()
  monkeypatch.setattr(mc_mod.model_caller, '_cache', diskcache.Cache(str(cache_dir)))
  return cache_dir


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
