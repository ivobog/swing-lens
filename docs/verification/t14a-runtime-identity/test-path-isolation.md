# Lifecycle test-path isolation audit

Scope: the focused lifecycle, process identity, worker and supervisor unit lane;
no destructive live lifecycle or production pipeline tests were executed.
Search results are retained in `test-path-audit.txt`.

The original unsafe core-stop test allowed `Stop-SwingLensCore` to reach its
default production `RuntimeStatePath` cleanup. The Run-161 remediation commit
sets that test's state path to `tmp_path` and mocks operational probes.

T14A additionally makes `_module_command` require an autouse test-owned root.
Every PowerShell module invocation through this helper overrides both
`RepoRoot` and `RuntimeStatePath` before calling lifecycle functions. Thus
default runtime files, lock files, journal paths and fallback probe paths are
under that individual test's temporary directory. The competing-process mutex
test uses the same temporary root for its two isolated test processes.
Explicit per-test state files also stay under `tmp_path`. The regression test
writes through those defaults and checks both resolved paths exactly.

The only direct launcher subprocess outside that helper is the Windows
PowerShell 5.1 rejection test. It exits at the required-version gate before
module import. Other launcher references are source-only assertions.

Python lifecycle safety and stopped-generation tests monkeypatch runtime
paths to temporary files; stale-state archival tests also replace `ROOT`.
Tests for gone processes mock role/listener evidence rather than inspecting
the live generation. Signal tests mock OS signaling and use temporary request
files. Convergence/diagnostic tests use temporary output roots and mocked
observations. Recovery tests use synthetic process/registration snapshots,
temporary publication targets, and no real DB mutation or process signaling.
Worker quiescence tests use fake sessions. Supervisor and worker launch tests
mock launching/signals and use temporary watchdog files. Observability tests
mock Docker commands. Configuration and contract tests are read-only.

Final validation: 53 PowerShell/contract cases and 149 Python lifecycle,
identity, supervisor and worker cases passed. The live metadata remained
missing after these tests and before the explicit reviewed recovery operation.
