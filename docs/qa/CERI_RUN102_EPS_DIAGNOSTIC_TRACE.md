# CERI Run 102 EPS Diagnostic Trace — NVDA

This is a read-only trace of source records `306749`–`306753` from the live
Run 102 database. The post-remediation normalized/feature stages were evaluated
in memory; the live database was not migrated or mutated.

## EODHD raw/source record

| Field | Value |
|---|---|
| Provider symbol | `NVDA.US` |
| Provider period / normalized slot | `0q` / `CURRENT_QUARTER` |
| Fiscal period end | `2026-07-31` |
| `epsTrendCurrent` | `2.0830` |
| `epsTrend7daysAgo` | `2.0830` |
| `epsTrend30daysAgo` | `2.0800` |
| `epsTrend60daysAgo` | `2.0793` |
| `epsTrend90daysAgo` | `1.9518` |
| `epsRevisionsUpLast7days` | provider did not expose a distinct retained value |
| `epsRevisionsUpLast30days` | `4` |
| `epsRevisionsDownLast30days` | `0` |
| Analyst count | `40` |
| Current source fingerprint | `b6b5e96740867578f792b1a27ae5b5a5ba36209f06c89871c4ec33246db8b883` |
| Current observation reference | `NVDA.US:CURRENT_QUARTER:2026-07-31:EPS_DILUTED` |
| Provider observation time | `2026-08-13T22:51:32.591537Z` |

Retrospective fingerprints are `da5c4903…` (7d), `f15169bc…` (30d),
`6d62951f…` (60d), and `a55bc694…` (90d).

## Normalized evidence after remediation

| Field | Current | 7d | 30d | 60d | 90d |
|---|---:|---:|---:|---:|---:|
| Metric | EPS_DILUTED | EPS_DILUTED | EPS_DILUTED | EPS_DILUTED | EPS_DILUTED |
| Consensus | 2.0830 | 2.0830 | 2.0800 | 2.0793 | 1.9518 |
| Baseline window | — | 7 | 30 | 60 | 90 |
| Baseline origin | — | PROVIDER_RETROSPECTIVE_WINDOW | PROVIDER_RETROSPECTIVE_WINDOW | PROVIDER_RETROSPECTIVE_WINDOW | PROVIDER_RETROSPECTIVE_WINDOW |
| Source/canonical scale | 1 / 1 | 1 / 1 | 1 / 1 | 1 / 1 | 1 / 1 |
| Source/canonical currency | null / null | null / null | null / null | null / null | null / null |
| `known_at` | 2026-08-13T22:51:32.591537Z | response retrieval | response retrieval | response retrieval | response retrieval |
| `reference_at` | response time | 2026-08-06T22:51:32.591537Z | 2026-07-14T22:51:32.591537Z | 2026-06-14T22:51:32.591537Z | 2026-05-15T22:51:32.591537Z |

Normalization preserves provider-scale EPS while leaving currency null and
adds `relative_value_only`. It does not make these values eligible for absolute
or cross-provider monetary comparison.

## Eligibility

| Check | Result |
|---|---|
| Same provider | yes — EODHD/EODHD |
| Same company | yes — company 128 |
| Same metric | yes — EPS_DILUTED |
| Same fiscal period | yes — CURRENT_QUARTER / 2026-07-31 |
| Same scale semantics | yes — 1 / 1 |
| Point-in-time safe | yes — response known before evaluation cutoff |
| Currency required | no for `SAME_PROVIDER_RELATIVE` |
| Analyst sample sufficient | yes — 40 |
| Current eligible | yes after remediation |
| Baselines eligible | yes after remediation |
| Exact first rejection | none |

Fail-closed controls remain: period mismatch yields
`SAME_PROVIDER_PERIOD_MISMATCH`, scale mismatch yields
`SAME_PROVIDER_SCALE_MISMATCH`, cross-provider missing currency yields
`CROSS_PROVIDER_CURRENCY_REQUIRED`, and absolute missing-currency comparison
yields `ABSOLUTE_COMPARISON_CURRENCY_REQUIRED`.

## Feature and component result

| Feature | Result |
|---|---:|
| EPS CQ 7d | `0%` |
| EPS CQ 30d | `0.144230769230769%` |
| EPS CQ 90d | `6.72200020493903%` |
| Breadth | `1.0` (`(4-0)/(4+0)`) |
| 7d-vs-90d acceleration | `-0.0746888911659892` percentage points/day |
| Comparison mode | `SAME_PROVIDER_RELATIVE` |

Magnitude, breadth, and acceleration are numeric available evidence. Negative
revision magnitude is not missing; its opportunity contribution may clamp at
zero while remaining available. The three revision components supply 45%
Opportunity coverage, still below the unchanged 60% rating threshold.

## Exact defect classification

Run 102’s UI `N/A` was an application defect. The licensed source record held
valid current and retrospective EPS values, but the estimate normalizer sent
them through an absolute currency conversion path. Missing currency nulled both
normalized consensus and canonical scale before the same-provider comparator
could run. Breadth was independently coupled to successful baseline selection.
The period-slot selector also assumed a current fiscal period end must be on or
after the cutoff; that is false between fiscal close and the earnings report.
It now honors the provider slot and chooses the latest fiscal end within it.

Migration `0043_ceri_run102_relative_evidence` safely rehydrates only EPS rows
with a provider observation reference, leaves currency null, and never changes
SEC acceptance or Opportunity thresholds.
