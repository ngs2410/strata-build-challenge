'''Tenant list and the one `DEFAULT_ENVIRONMENT` constant.

Tenants are a first-class axis of data + infra isolation in Strata (CLAUDE.md:
"a regulated enterprise is a tenant"). v1 ships with one tenant,
`verdant_grid`, whose data lives at `data/tenants/verdant_grid/<env>/`.

`DEFAULT_ENVIRONMENT` has strict usage rules — hardcoding it at a facade call
site is a bug because the runtime env is being silently dropped. It is
legitimate ONLY in: (a) parameter-signature defaults on facade ABCs/concretes,
(b) CLI flag defaults, (c) test fixtures, and (d) this module's own
definition site. Everywhere else, plumb `environment: str` as a sibling of
`tenant_id`. See CLAUDE.md "Engineering principles — tenants/environments".
'''

DEFAULT_ENVIRONMENT = 'prod'

TENANTS = {
  'verdant_grid': {
    'display_name': 'Verdant Grid, Inc.',
    'environments': ('prod',),
  },
}
