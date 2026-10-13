### TEST QUALITY RULES — violations are bugs

**Forbidden anti-patterns:**

1. **Logic Mirroring** — NEVER re-implement the function's algorithm to compute expected values. Use hand-computed literals or independent reference values.
   - BAD: `expected = sum(values) / len(values); assert _mean(values) == expected`
   - GOOD: `assert _mean([2.0, 4.0, 6.0]) == 4.0`

2. **Vacuous Assertions** — NEVER assert only `is not None`, `isinstance`, or truthiness. Assert specific values.
   - BAD: `assert result is not None`
   - GOOD: `assert result == 0.75`

3. **Over-Mocking** — NEVER mock the function under test or its internal methods. If a function needs mocking to test, pick a different function.

4. **Golden-Path Only** — NEVER test only the happy path. Include edge cases: empty inputs, zero values, boundary values, single-element inputs.

5. **Assertion-Free Tests** — Every test MUST have at least one explicit assertion. "No exception raised" is not a test.

6. **Testing Implementation Details** — Assert observable behavior (return values, raised exceptions), not internal state or call order.

7. **Determinism Leakage** — No dependence on wall clock time, randomness, or external state. Control all inputs.

8. **Input = Expected Symmetry** — NEVER derive expected output by applying a trivial transformation to the input inline.
   - BAD: `assert compute_run_id({'a': 1}) == hashlib.sha256(json.dumps({'a': 1}).encode()).hexdigest()[:16]`
   - GOOD: `assert compute_run_id({'a': 1}) == 'e2d04e45d0b5b6a1'`

9. **Single-Environment Testing of Multi-Environment Code** — Tests covering tenant-keyed code paths MUST exercise at least two distinct environment values (e.g. `'prod'` and `'staging'`) and assert that data written under env=A is not visible under env=B and vice versa. A test suite that only runs with `DEFAULT_ENVIRONMENT` cannot detect when a service-layer module is silently env-blind (hardcoded `DEFAULT_ENVIRONMENT` at facade call sites). This is the failure mode the env-threading audit was created to prevent — and in Strata it's load-bearing because data isolation between regulated enterprises is a product invariant.
   - BAD: `server.ingest_proceeding('acme_utility', ...); assert facades.proceeding.load_proceeding('acme_utility', DEFAULT_ENVIRONMENT, proceeding_id)` — passes regardless of whether the server actually routed to the right env.
   - GOOD: `server.ingest_proceeding('acme_utility', environment='staging', ...); assert facades.proceeding.load_proceeding('acme_utility', 'staging', proceeding_id) is non-empty; assert facades.proceeding.load_proceeding('acme_utility', 'prod', proceeding_id) is the empty sentinel.`

10. **Degenerate-Shape Fixtures That Mask Production Mismatches** — Test fixtures MUST use values whose SHAPE matches the production data flow, not values that happen to satisfy every backend's input validation by accident. The most common failure is using a UUID string in a place where production passes an external string id, because every backend "accepts" the UUID — a strict-typed backend (Postgres) accepts it as the underlying UUID column; a loose-typed backend (JSON / InMemory) stores it verbatim. The test passes; production crashes the moment the value is a non-UUID. The same trap applies to: empty strings where production has timestamps, populated dicts where production has dataclasses, well-formed JSON where production may emit malformed input, single-element lists where production batches scale to thousands.
    - BAD: `seed_passage(uuid_a); claim = Claim(passage_id=uuid_a, ...)` — `passage_id` is the external string id in production (`'CA-PUC-R2401-005§4.2.1-p3'`, built from docket + section + paragraph), not a UUID. The fixture's UUID-as-passage_id satisfies Postgres's UUID column by accident; production trips on the first non-UUID external id.
    - GOOD: `seed_passage(uuid_a); claim = Claim(passage_id=f'CA-PUC-R2401-005§4.2.1-p{uuid_a[:8]}', ...)` — fixture mirrors the production shape (external string id distinct from internal UUID surrogate); the round-trip exercises the external→UUID resolution that production depends on.
    - **How to spot the violation:** when a Postgres-strictness bug is found in production code (cast errors, FK errors, NOT NULL errors), the contract test fixture for that backend almost always used a degenerate value that "happened to work." Audit the fixture against the actual producer of that field — what shape does the production code emit?

11. **Tests Writing Outside Their Own Tempdir** — Tests MUST NOT write to (or modify) any path that is shared with production, the developer's working tree, or other tests' state. Test outputs go to `tmp_path`, `tempfile.mkdtemp()`, or a per-test-owned scratch dir — full stop. The most destructive failure mode is a fixture that "ensures" a config-derived file exists by overwriting it: if the config points at a production artifact (a shared tenant data dir, a persisted audit log, a trained extraction-model dir, anything under `output/`), the test silently destroys real state on every run. Cosmetic test-fixture mismatch becomes data loss the moment someone runs the test suite. This failure mode has shipped before — a fixture that read a path out of a config object and overwrote the production artifact on every test sweep; the underlying data survived, the metadata describing how to use it did not.
    - BAD: `metadata_path = Path(default_config.obligation_extraction.model_dir) / 'extraction_metadata.json'; metadata_path.write_bytes(stub)` — `model_dir` defaults to a production model directory; the test overwrites the real metadata on every run.
    - GOOD: `data['obligation_extraction']['model_dir'] = tempfile.mkdtemp(prefix='test_extraction_'); ... write the stub under that tempdir` — fixture redirects the config path to a test-owned dir before any write happens.
    - **Enforcement.** When a test needs to write a file the production code will read, the fixture MUST (a) override the path to a tempdir at config-construction time, AND (b) gate the write with a runtime check that the destination is under `tempfile.gettempdir()` (or your project's test-scratch root). The runtime check is the second line of defense — config drift or copy-paste from another test can't accidentally re-point at production.
    - **How to spot the violation:** any test that reads a path out of a config object (`default_config.X.path`, `default_config.Y.dir`) and then writes to that path is a candidate. Audit every such fixture against the actual default value of that config key. If the default points anywhere outside the test's tempdir, the fixture is dangerous.

**Every test must:**
- Validate externally observable behavior
- Include at least one non-trivial assertion with a hand-computed expected value
- Cover at least one edge or failure case
- Fail meaningfully if the core algorithm changes incorrectly
