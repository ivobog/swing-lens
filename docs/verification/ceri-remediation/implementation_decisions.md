# Implementation decisions

- Multi-period weights are configuration-driven: current quarter 35%, next quarter 30%, current fiscal year 20%, next fiscal year 15%. This conservative near-term emphasis is not hardcoded in services and only applies after the 60% coverage gate.
- Source quality has no synthetic default. Missing evidence contributes no subscore and zero core revision coverage hard-gates Confidence to Insufficient.
- Runtime `CERI_ENABLED` is the master enable. YAML `engine.enabled` is exposed as deprecated diagnostic state.
- Frozen legacy evidence was not rewritten to populate new eligibility/provenance columns. It is safely rejected or left unavailable under the new calculation.
- Development captures r1/r2 were left immutable. The final verified config is r3; calculation semantics remain `ceri-1.1.0`.
