from src.tenant_registry import DEFAULT_ENVIRONMENT, TENANTS


def test_default_environment_is_prod():
  assert DEFAULT_ENVIRONMENT == 'prod'


def test_tenants_registry_includes_verdant_grid():
  assert 'verdant_grid' in TENANTS


def test_tenant_entry_has_required_fields():
  entry = TENANTS['verdant_grid']
  assert entry['display_name'] == 'Verdant Grid, Inc.'
  assert 'prod' in entry['environments']


def test_default_environment_is_a_registered_environment_for_every_tenant():
  for tenant_id, entry in TENANTS.items():
    assert DEFAULT_ENVIRONMENT in entry['environments'], (
      f'tenant {tenant_id} does not register {DEFAULT_ENVIRONMENT}'
    )
