# Ownership Map

Replace placeholder owners with real GitHub usernames or teams before branch protection depends on
CODEOWNERS.

| Area | Primary Owner | Required Review |
| --- | --- | --- |
| App shell, routes, templates, static assets | App maintainer | UX/accessibility reviewer for user-facing changes |
| Upload, CSV, raw preservation | Data ingestion owner | Security reviewer for file handling |
| Database models and Alembic | Persistence owner | Domain owner plus migration reviewer |
| Background jobs and pipeline | Operations owner | Persistence owner |
| IB market data | Provider integration owner | Safety/security reviewer |
| Fundamental and technical scoring | Quant scoring owner | Golden-fixture reviewer |
| Combined decisions and ranking profiles | Quant scoring owner | UX/safety reviewer |
| Market regime and sector rotation | Market context owner | Quant scoring reviewer |
| Setup lifecycle | Lifecycle owner | OWPE owner if point-in-time features change |
| Winner probability | Model governance owner | Quant validation reviewer |
| CERI, provider licensing, purge | Provider governance owner | Security/legal-policy reviewer |
| Security and local admin | Security owner | App maintainer |
| Operations, backup, recovery | Operations owner | Persistence owner |
| Docs and release governance | Maintainer lead | Affected domain owner |
