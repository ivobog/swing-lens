# Winner retirement test isolation

Failing node:
`tests/integration/test_t14d_caller_postgresql.py::test_model_retirement_api_delivers_exact_new_governance_and_maps_rejections`.

The first release gate's full suite rejected retirement with
`MUTATION_WINNER_MODEL_NOT_KNOWN_AT_OPERATION`; three isolated PG18 reruns and
the 15-test module's normal order passed before this change. No separate
predecessor test was found to be necessary. The minimal polluting sequence is
**inside the test itself**: `_seed_native_ready_outcome` commits, the test
sampled `at = datetime.now(UTC)`, and only then `ModelRegistry.register_model`
persisted a model with PostgreSQL's server-side `created_at`. Depending on
transaction start and host/DB clock timing, that model birth can be later than
`at`. The positive retirement then correctly fails the product's temporal
authority guard. This is a `TEST_ISOLATION_DEFECT` in fixture time selection,
not a runtime shared-state defect. No module global, settings cache, database
row leak, or prior-test monkeypatch is needed to produce the bad ordering.

The test now takes the valid governance time from the **persisted** model
birth (`created_at + 1 second`) after registration. It additionally proves
that `created_at - 1 microsecond` is rejected with the exact original error
code and leaves the model `SHADOW`. This retains, rather than weakens, the
product authority guard. The invalid naive/absent-time, forged model-key,
successful retirement, idempotent API and missing-model assertions remain.

Evidence before remediation: the first full suite had one failure at this
node while isolated reruns were green; the complete 15-test module in normal
order also passed. Evidence after remediation: isolated PG18 target passed three
consecutive fresh-process runs,
including deterministic before-birth rejection; the certificate/source
focused lane passed 82/82; the 15-node T14D caller module passed in explicit
reverse collection order (15/15). The full repository result is recorded in
`RELEASE_main_certification.json` after completion.
