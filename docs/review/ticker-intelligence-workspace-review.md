# Ticker Intelligence Workspace — Independent Forensic Review

21 October 2026 · Local checkout `C:\Users\Ivica\Documents\SwingLens`

## A. Executive verdict

**REVIEW FAIL.** All three deliverables exist and are integrated. Deliverables 1 and 2 passed the focused checks described here. Deliverable 3 has three confirmed evidence-selection defects and three confirmed presentation/performance defects. These prevent acceptance of the full workspace for operational use.

The existing suites independently returned **234 passed, zero failed, zero skipped**. Additional adversarial probes reproduced failures that those suites do not detect. Four selector/availability counterexamples were repeated against a freshly migrated disposable PostgreSQL database, retaining production foreign keys and constraints. No production code or existing test was changed to obtain these results.

The strongest finding also occurs in the actual persisted EOG workspace: its selected CERI snapshot belongs to calculation context 83, while the revision detail includes context 81 and truncates valid context-83 rows. This is an incorrect combined picture despite plausible individual values.

No unauthorized mutation, order action, provider request or scoring invocation was found in the new workspace paths. This is a feature review, **not full application certification**. Normal operational activation was not performed because the supported startup procedure would launch unrelated job recovery and scheduling; the review task expressly permits an isolated read-only alternative in that situation.

### Requirements, checkout and evidence basis

The complete approved vision and all three original implementation prompts were accessible and read. None was reconstructed from an implementation report.

| Source | Exact local document |
| --- | --- |
| V: Vision v1.0 | `C:\Users\Ivica\Downloads\SwingLens_Ticker_Intelligence_Workspace_Vision_v1.0.md` |
| D1: Global Search Dock | `C:\Users\Ivica\.codex\attachments\f929581a-35af-4ec5-8563-3e96c7f65228\Pasted text.txt` |
| D2: Full Decision Workspace | `C:\Users\Ivica\.codex\attachments\7ed18b9a-aa96-4c1f-9349-d6e0f59ef2ef\Pasted text.txt` |
| D3: Intelligence Integration | `C:\Users\Ivica\.codex\attachments\22f0ae83-bd8a-44c4-a10a-2413df314c5c\Pasted text.txt` |
| Current review authorization | `C:\Users\Ivica\.codex\attachments\445e7040-5f9b-40ab-9863-dab72ca31ad0\Pasted text.txt` |

The vision supplies product requirements; its historical “next action” text is not an instruction to implement during this review. The current review request governs execution. Its runtime authorization supersedes the earlier prompts' prohibition on restarting port 8000, subject to its startup safety conditions.

All three `docs/qa/ticker-*.md` implementation reports were read as claims. Their successful test counts were reproduced; their unconditional Deliverable 3/readiness conclusion is contradicted below.

Reviewed branch: `codex/ceri-authority-remediation`; HEAD: `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. At entry there were 131 Git status entries, including extensive unrelated changes. There is no clean commit boundary isolating this feature. Attribution below follows actual files, diffs and dependencies, not an assumption that every dirty file belongs to the workspace.

Temporary evidence is retained under `output/forensic-review/`; screenshots under `output/playwright/forensic-*.png`. These are local diagnostic artifacts, not application changes. Reproduction commands and limitations are recorded below.

### Actual implementation inventory

Paths in this report are relative to the checkout unless shown as absolute. Function names and line numbers refer to this reviewed working tree.

| Layer | Files and responsibilities |
| --- | --- |
| Routing | `app/routers/ticker_routes.py`: five GET routes; registration in `app/main.py:140`. |
| D1 selection | `app/services/ticker_search_service.py`: SQL catalog, aliases, identity qualification, shared eligible-decision iterator and frozen preview. |
| D2 composition | `app/services/ticker_workspace_service.py`: selected immutable decision, pinned compatible producers, transient card models and bounded run selector. |
| D3 composition | `app/services/ticker_intelligence_service.py`: four separate read adapters, publication/eligibility filters, summaries and detail projections. D3 is implemented, not a placeholder. |
| Shared shell | `app/templates/base.html`, `app/templates/partials/_nav.html`, `_ticker_search.html`; `app/static/ticker_search.js` and `.css`. |
| Workspace | `app/templates/ticker_workspace.html`; `app/static/ticker_workspace.js` and `.css`; `app/templates/partials/_ticker_intelligence.html`. |
| Reuse/compatibility | `app/templates/partials/_ticker_price_chart.html`, `_score_card_body.html`; extracted from `ticker_chart_panel.html`. `app/services/chart_data_service.py` adds an optional explicit technical source. Legacy callers keep their existing lookup. `ticker_chart_panel.js` and `build_score_cards()` are reused. |
| Existing query change | `app/services/ceri/query_service.py`: optional latest-successful-check-per-feed freshness query; default behavior retained. |
| Feature tests | `tests/test_ticker_search.py`, `test_ticker_workspace.py`, `test_ticker_intelligence.py`; corresponding three `tests/e2e/test_ticker_*` files. `tests/test_route_security.py` updates the GET inventory. |
| Implementation reports | `docs/qa/ticker-search-dock.md`, `ticker-decision-workspace.md`, `ticker-intelligence-integration.md`. |

The shared `_score_card_body.html` is also newly present even though its filename does not contain “ticker.” No new package dependency, schema, ticker master table or migration was introduced by these feature files. Existing lifecycle/pipeline/ranking changes elsewhere in the dirty tree are dependencies inspected where relevant; they are not attributed wholesale to D1–D3.

The route surface is:

```text
GET /api/tickers/search
GET /api/tickers/{ticker}/summary
GET /tickers/{ticker}
GET /api/tickers/{ticker}/chart-data
GET /api/tickers/{ticker}/intelligence/{ceri|lifecycle|rankings|winner}
```

D2 reuses D1's identity and eligible-decision iterator. D3 reuses both, plus D2's calculation-context compatibility helper. Intelligence failures occur in separate HTTP requests, so they do not block the main decision render.

## B. Requirement compliance matrix

Statuses assess the stated bounded requirement, not unrelated application certification. “PASS” on a seeded edge case does not mean that edge case exists in the live database. `TS`, `TW`, `TI` mean the three feature backend test files; `BS`, `BW`, `BI` mean their browser counterparts. Full test names are discussed in H and in individual findings.

| ID | Source and requirement | Implementation | Existing coverage | Independent evidence | Status |
| --- | --- | --- | --- | --- | --- |
| V-01 | V §6–9; D1–3: reuse Jinja/vanilla JS and engines | Shared shell, three services, existing chart/cards | Feature and regression suites | Source/diff inspection; no new dependency/schema | PASS |
| V-02 | V §7; D1–3: read-only research interface | Five GET routes; service call graph | TS/TW/TI read-only tests; route security | Production transaction READ ONLY; recorded statements SELECT/WITH; no writer calls | PASS |
| V-03 | V §5: verified identity across sections | `resolve_ticker_identity`, qualified URLs, symbol-only current-intelligence guard | TS ambiguity/alias; TW/TI identity cases | EOG qualified identity preserved; ambiguous cases fail closed in disposable DB | PASS |
| V-04 | V §5; D3 §3: correct source-specific context | `_ceri`, `_winner`, independent dates | TI positive contexts | Actual mixed CERI revision contexts; competing Winner selector divergence, F02/F03 | FAIL |
| D1-01 | D1 §2: global non-modal accessibility | `base.html`, `_nav.html`, `_ticker_search.html` | BS shared-shell/nonmodal; browser smoke | Actual dashboard dock; background controls visible/usable; no backdrop/focus trap | PASS |
| D1-02 | D1 §2/4: slash, focus, arrows, Enter, Escape/dialogs | `ticker_search.js` handlers | BS keyboard/confirmation tests | Chromium/Firefox suites; real input focus and Enter preview | PASS |
| D1-03 | D1 §2: own desktop space, responsive dock | `ticker_search.css`, 390px grid, stacked <=1000px | BS narrow; BW canvas resize | Actual dashboard screenshot; 390px missing workspace; browser canvas checks | PASS |
| D1-04 | D1 §2: optional manual dock resizing | Fixed CSS width | No drag-resize implementation | Requirement is conditional, not mandatory; content/chart resize works | NOT APPLICABLE |
| D1-05 | D1 §3: local history, exact/prefix/substring/name ordering | `_catalog`, `search_tickers` | TS matching/historical tests | Actual EO six matches; SQL grouping/ordering inspected | PASS |
| D1-06 | D1 §3: dedup, aliases, exchange/share classes | `_active_aliases`, `_catalog`, resolver | TS aliases/validity/punctuation/duplicate identities | Stored aliases only; conservative exchange ambiguity; no punctuation guessing | PASS |
| D1-07 | D1 §3: validation, bounds, parameterization | Route Query constraints; SQL LIMIT, wildcard escaping | TS limit/wildcard/API tests | Invalid qualifier 422; search 1 SQL read; no history materialized in Python | PASS |
| D1-08 | D1 §6: latest eligible consistent preview | `eligible_ticker_decisions`, frozen JSON fields | TS failed/incomplete/unpublished/legacy/unsafe-partial/readiness cases | Actual EOG #46423; invalid newer seeded candidates excluded; one envelope supplies score fields | PASS |
| D1-09 | D1 §4: debounce, cancellation, race protection | 200ms timer, AbortController, version counters | BS obsolete search/preview tests | Both browser engines passed; source inspection | PASS |
| D1-10 | D1 §4/6: empty/loading/error/retry, safe text | Dedicated states; textContent | BS empty/error/escaping/retry | Existing cases independently rerun; no external HTML interpolation | PASS |
| D1-11 | D1 §6/7; D2 §3: preview and explicit independent opening | `workspaceLink`, qualified summary destination | BS popup and blocked-popup cases; BW integration | Actual popup retained dashboard; correct EOG/run URL; opener null | PASS |
| D1-12 | D1 §9–12: preserve ordinary navigation and controls | Additive shell integration | Smoke, dashboard/progress/dialog regressions | All relevant executed cases pass; no live mutation control invoked | PASS |
| D2-01 | D2 §2/3: standalone, bookmarkable, qualified URL | `workspace` route, GET form | TW default/explicit; BW refresh | Actual direct EOG; selection generates identity+run URL; refresh covered | PASS |
| D2-02 | D2 §4: default/history/invalid run, no fallback | Shared iterator; explicit run query | TW invalid/default tests | Actual runs 45/44/36; scores/levels switch; invalid run tests | PASS |
| D2-03 | D2 §4/7: selected immutable combined and pinned producers | `_frozen_score`, `_source_matches`, source IDs | TW incompatible/missing/mutable-source tests | Real EOG stops/targets and scores match frozen records | PASS |
| D2-04 | D2 §7: preserve every score-card field and zero | Existing builder; shared `_score_card_body.html` | TW `test_every_score_card_field_and_valid_zero_is_preserved`; old card/route tests | Extracted markup matches old fields; rendered field/value comparisons pass | PASS |
| D2-05 | D2 §5: compact header, warning/provenance/disclaimer | `ticker_workspace.html` | BW header; TW template assertions | Actual EOG 8.08/9.11/6.83, rank1, warning, cutoff, research size | PASS |
| D2-06 | D2 §6: candles/volume/SMAs/levels/fallback/empty | Existing renderer/service, chart partial | Chart service/route and BW tests | Actual 758 bars and chart; tabular fallback/SMAs/empty fixture cases pass | PASS |
| D2-07 | D2 §6: accurate cached-price provenance | Chart label and chart API provenance | TW/BW assertions | Exact current-cache label; distinct Run45/44 frozen levels | PASS |
| D2-08 | D2 §7: six native disclosures/defaults/persistence | Native details; sessionStorage JS | TW defaults; BW switching/refresh | Original six retained; chart+Combined initially open in fresh context; saved choices restored | PASS |
| D2-09 | D2 §8: full-width responsive chart, usable selector | Workspace CSS; existing ResizeObserver | BW actual canvas/narrow layout | Both engines pass; actual mobile no horizontal page overflow | PASS |
| D2-10 | D2 §9: unknown/ambiguous/unscored/missing/error | Route status mapping and component states | TW availability/query errors; BW missing | Actual AZTA200, unknown404, invalid identity422; no fabricated run | PASS |
| D2-11 | D2 evidence; review §4/6: detect changed combined pointer | Expected evidence ID in chart and run-specific intelligence | TW pointer change; TI changed state | Real chart with wrong expected ID returns409; controlled pointer cases pass | PASS |
| D2-12 | D2 §11: retain old chart URLs/APIs | Optional chart source; shared partials | Old chart/score tests | Actual legacy chart and API return200 with matching prices | PASS |
| D3-01 | D3 §2/8/9: ten sections, lazy independent loading/cache | Workspace JS; four independent requests | BI summary/lazy/cache/obsolete tests | Real expanded sections; initial HTML does not call intelligence readers | PASS |
| D3-02 | D3 §4: eligible CERI headline and own timestamp | `_ceri` snapshot predicate | TI excluded/owner quarantine/disabled | Real snapshot5136; seeded headline correctly falls back; no score recomputation | PASS |
| D3-03 | D3 §3/4: relevant eligible CERI changes/alerts | `_ceri` calls existing list readers | TI only headline assertions for owner quarantine | Quarantined run7 change9100/ACTIVE alert9300 remain CURRENT, F01 | FAIL |
| D3-04 | D3 §3/4: revision evidence belongs to stated source | `_ceri` revision query; detail line26 | No multiple-context/future feature case | Actual 16 context81 +14 context83; later unrelated +99% admitted, F02 | FAIL |
| D3-05 | D3 §4: accepted guidance state | `_ceri` guidance query | Positive guidance absent from existing feature seed | Canonical AVAILABLE vs workspace empty for null session, F05 | FAIL |
| D3-06 | D3 §4: contributors, freshness, catalysts/earnings | Existing serializers plus bounded queries | TI contributor/freshness checks | Real contributors/freshness shown; no positive current catalyst/earnings example independently validated | PARTIAL |
| D3-07 | D3 §5: published daily primary/secondary episodes and blockers | `_lifecycle`, publication helpers, episode payload | TI primary/secondary/unpublished; lifecycle regressions | Actual episode910 READY/GENERIC/BLOCKED, blocker MARKET_POLICY_BLOCK; seeded secondary case | PASS |
| D3-08 | D3 §5/10: meaningful signal changes and detail links | Timeline merge and shared intelligence template | Existing assertions check generic timeline text | Actual signal17717 name/old/new/delta omitted; no row link, F04 | FAIL |
| D3-09 | D3 §5: dated bounded timeline/alerts, retired exclusion | Per-source LIMIT10, publication predicates, status filter | TI unpublished/superseded; lifecycle suite | Real timeline dated newest first, bounded active-page count; negative cases pass | PASS |
| D3-10 | D3 §6: all persisted selected-run frozen rankings | `_rankings` current-pointer/context/readiness checks | TI custom profile/config/context tests; BI run change | All actual five in45/44, no hardcoded five; >100 profiles silently capped and not tested | PARTIAL |
| D3-11 | D3 §7: prediction/selected-run/combined linkage | `_winner` run/ticker/combined pin + visibility | TI wrong pin/run/superseded prediction | Real predictions947/763 change with run; exact immutable combined pin | PASS |
| D3-12 | D3 §3/7: appropriate serving estimate selection | `_winner` ascending ID/first estimate | TI one serving estimate + nonserving distractors | Canonical selector81% vs workspace64% with two published versions, F03 | FAIL |
| D3-13 | D3 §7: outcome, probability bounds/grade/sample, insufficient | Existing estimate/outcome payloads; guarded template | TI probability/interval/outcome/insufficient; BI values | Real insufficient/no_eligible_cohort stays distinct from0%; supported64/41 and bounds in disposable cases | PASS |
| D3-14 | D3 §3/10: source labels/provenance/evidence links | Section context labels; source pages and reproduction URLs | TI/BIs inspect context and select links | Real independent dates and run links; signal links absent (F04), revisions mislabelled(F02) | PARTIAL |
| D3-15 | D3 §9: independent errors/retry/stale requests | Separate GETs and per-section version/state | TI SQL error isolation; BI503/retry/navigation | Both engines rerun; simulated CERI error leaves sibling/chart working | PASS |
| D3-16 | D3 §9; V §6: efficient bounded loading | Lazy detail; bounded lists | TI summary counts only; BI cached reopen | CERI20queries, 2.4–7.0sec with unused count/context work, F06 | PARTIAL |
| X-01 | Review §8: validation/escaping/opener/feature access | Param regex/ranges, Jinja, textContent, flags | Route security, external-text and popup tests | Safe503 errors, CERI-disabled state, opener null; no demonstrated injection/mutation | PASS |
| X-02 | Review §9: actual live ambiguous identity view | Resolver and truthful409 | TS/TW/TI seeded ambiguity | Actual catalog grouping found no duplicate identities; no real ambiguous example to inspect | NOT VERIFIED |
| X-03 | Review §9: supported normal operational startup | `swinglens.ps1` -> supervisor/worker | Outside feature suites | Conflict documented in I; port8000 intentionally remains stopped | NOT VERIFIED |
| X-04 | Review §7: large-history capacity and memory | SQL limits/batches, no frontend universe | Small focused fixtures only | Small current DB measured; no large-universe/load/peak-memory benchmark | NOT VERIFIED |
| X-05 | Review §6: source changes cannot tear any page state | Combined-ID guards; independent current panels | Specific pointer/navigation tests only | No concurrency test of simultaneous CERI/lifecycle publication; not an all-source atomicity proof | NOT VERIFIED |
| X-06 | V §7.2: reconstructed historical price chart | Explicit current-cache presentation | Provenance tests | Intentionally excluded from v1 and accurately labelled | NOT APPLICABLE |

No entire deliverable is `NOT IMPLEMENTED`. Failed requirements have existing implementations with demonstrated wrong behavior. Untested positive source cases and operational activation remain explicitly separated from missing code.

## C. Findings summary

| Severity | Count |
| --- | ---: |
| P0 | 0 |
| P1 | 3 |
| P2 | 3 |
| P3 | 0 |

| Finding | Severity | Confidence | Short description |
| --- | --- | --- | --- |
| F01 | P1 | PROVEN | Quarantined CERI owner excluded from headline but still supplies CURRENT change and ACTIVE alert. |
| F02 | P1 | PROVEN | CERI revisions mix calculation contexts and can include evidence known after the displayed cutoff. |
| F03 | P1 | PROVEN | Winner summary chooses the oldest-ID serving estimate instead of the established selected estimate. |
| F04 | P2 | PROVEN | Lifecycle signal rows omit signal identity, changed values and usable evidence links. |
| F05 | P2 | PROVEN | Accepted guidance with an absent effective session disappears despite canonical AVAILABLE status. |
| F06 | P2 | PROVEN | CERI detail pays for repeated expensive history/count/context queries not consumed by the panel. |

## D. Detailed findings

### F01 — CERI details expose quarantined owner evidence as current

**Affected:** D3 / CERI authority. **Requirement:** D3 §3–4; V §5/§8 D3 acceptance. Quarantined/non-authoritative evidence must not be presented as current valid intelligence.

**Expected:** Headline, current changes and active alerts obey one consistent owner eligibility boundary. Historical rejected evidence, if deliberately exposed, is identified as rejected historical evidence.

**Actual:** Quarantining pipeline owner7 makes the headline correctly fall back from snapshot9000 (8.20) to8999 (5.00). The same detail response retains change9100 targeting snapshot9000 as `COMPARABLE / CURRENT` and alert9300 as `ACTIVE`. Neither receives a quarantine warning.

**Established root cause/location:** `app/services/ticker_intelligence_service.py:126` adds a QUARANTINED/RETIRED owner exclusion for the headline. Lines179–180 delegate details to `CeriQueryService.changes/alerts` with ticker-only filters. `app/services/ceri/evidence_eligibility.py:146` checks failed/cancelled/blocked/unsafe-partial owner status, but not `execution_authority_state`; detail readers therefore retain a different current snapshot. Rendering occurs at `_ticker_intelligence.html:31` and `:32`. This is an integration defect exposed through an existing reader-policy gap, not a claim that D3 created that underlying helper.

**Reproduction/evidence:** Run the disposable probe in H. Seeded snapshots8999/9000 and alert/change9300/9100 are documented in the probe and existing seed helper. Update only the disposable `PipelineRun(id=7).execution_authority_state` to QUARANTINED. GET `/api/tickers/EOG/intelligence/ceri?identity=ceri:8000&run_id=7&evidence_id=7&detail=true`. The recorded JSON has summary `Opportunity 5.00 ...`, yet HTML still contains both IDs and the CURRENT/ACTIVE labels. Repeated successfully with all migrations and production FKs. See `adversarial-migrated-results.json -> quarantined_ceri`.

**Coverage gap:** `test_ceri_quarantined_owner_and_uncertified_snapshots` at `tests/test_ticker_intelligence.py:557` checks only the headline and then removes all snapshot certifications. It never asserts exclusion/classification of linked detail evidence. Current real EOG data was not altered to manufacture this case.

**Minimal recommendation:** Reuse a shared eligible-current scope for both headline and linked current records, including owner authority and certification. Add a migrated-DB regression asserting detail labels/IDs after quarantine. Preserve deliberately historical reads and existing CERI consumers' contracts. **Confidence: PROVEN.**

### F02 — CERI revision rows are not pinned to the selected source context

**Affected:** D3 / CERI revision provenance. **Requirement:** D3 §3–4; V §5. Current intelligence still needs its own internally consistent source cutoff and evidence lineage.

**Expected:** “Estimate revisions at this source cutoff” shows the selected snapshot's verified revision inputs, or an explicit unavailable state. Other context/configuration generations and later-known features cannot silently serve as that snapshot's evidence.

**Actual production evidence:** EOG snapshot5136 has context83 and cutoff `2026-10-20T03:05:25.356265+02:00`. For the same company/session/calculation version, the DB contains 24 revision features in context83 and24 in context81, whose cutoff is `2026-10-19T23:47:42.545722+02:00`. The actual service returns **16 context81 rows and14 context83 rows**. Its 30-row limit therefore includes an older generation and omits ten selected-context rows. The HTML gives metric/window but omits period and context, so repeated rows are not distinguishable by their actual period/generation.

**Additional migrated-DB proof:** A legal standalone EPS feature88882 with matching company/session/calculation version, different config and `known_at=2026-10-22T22:00:00+02:00` appears as `+99.00%` under snapshot9000's `2026-10-21T22:00:00+02:00` source cutoff. It is not in that snapshot's ledger. This demonstrates forward leakage as well as the actual cross-context mixture.

**Established root cause/location:** `_ceri` at `app/services/ticker_intelligence_service.py:182` filters only company, session and calculation version, plus purge warning; it omits context, cutoff, known-at and pinned ledger IDs. Sorting by metric/window/ID followed by LIMIT30 at lines193–195 creates truncation across generations. `_ticker_intelligence.html:26` labels them at the source cutoff without displaying `period_key` or individual provenance. Existing `_revision_lineage_audits` in `app/services/ceri/query_service.py:4203` demonstrates that snapshot ledger feature IDs are available as a read boundary.

**Reproduction/evidence:** GET the actual EOG CERI detail; compare `real-ceri-True.json` revision IDs to `ceri_revision_features`. Exact context counts, sample IDs114391–114394 and cutoffs are retained in `read-diagnostics.json`. The disposable future feature and resulting HTML are in `adversarial-migrated-results.json -> future_revision`.

**Coverage gap:** Existing CERI feature tests seed no competing revision contexts or future-known revision features. Snapshot score assertions do not validate the evidence rows beneath them.

**Minimal recommendation:** Read the snapshot's pinned/verified revision lineage using established helpers; fail closed when lineage cannot be established. Show period identity and disclose any intentional bounded continuation. Do not fix this by recomputing revisions or applying today's configuration. **Confidence: PROVEN.**

### F03 — Winner summary disagrees with established estimate selection

**Affected:** D3 / Winner serving estimate. **Requirement:** D3 §3/§7; V §4.3/§5. Use existing visibility, serving and version selection semantics rather than an ID shortcut.

**Expected:** For one prediction/outcome/estimate kind, the displayed selected estimate agrees with `WinnerProbabilityApiService._selected_estimate`. Alternative versions, if shown, are clearly identified as alternatives.

**Actual:** A migrated disposable DB contains two PUBLISHED, serving DECISION_TIME estimates for prediction80007/outcome8000, at the same original training cutoff. Estimate81007 has probability0.64; estimate89999 has source version `independent-published-v2`, later creation and probability0.81. The canonical selector returns89999/81%. The workspace summary returns64%, and detail prints both without identifying the selected version. These rows satisfy retained schema uniqueness/FK checks and the subsystem's serving predicate. No later training cutoff or cross-run substitution was needed.

**Established root cause/location:** `_winner`, `app/services/ticker_intelligence_service.py:473` orders by outcome priority/horizon then **estimate ID ascending**; line569 chooses the first returned estimate for the summary. `app/services/winner_probability/api_service.py:419`, especially line464, selects by descending training cutoff, creation and ID. The workspace duplicates only part of that contract.

**Reproduction/evidence:** `adversarial-migrated-results.json -> competing_estimates` records canonical ID89999/probability0.810000 versus the API summary `Probability 64%`. The probe clones a same-prediction/outcome serving row into a separate admitted persisted version. This is a consumer-selection counterexample, not an assertion that the current native writer routinely creates duplicate active versions. A read-only grouping found **no such multiple-serving group in the actual database**.

**Coverage gap:** `tests/test_ticker_intelligence.py:649` provides one PUBLISHED estimate plus CANDIDATE and SUPERSEDED distractors. It verifies serving exclusion, not selection among multiple versions admitted by the serving query. Existing Winner API tests exercise its selector, but no workspace-to-canonical equivalence assertion exists.

**Minimal recommendation:** Reuse or batch the established per-prediction/outcome selected-estimate rule. Preserve frozen outcome FKs and exact decision pinning. If additional published versions remain visible, label the chosen view and alternatives explicitly. **Confidence: PROVEN for selector divergence under the reproduced admitted state; production occurrence not observed.**

### F04 — Lifecycle signal-change rows lose their meaning

**Affected:** D3 / Setup Lifecycle UI. **Requirement:** D3 §5/§10: show signal changes, actual events, chronology and evidence links.

**Expected:** A signal row identifies its signal, previous/current values and meaningful delta, with a route to source evidence even if it has no episode ID.

**Actual production evidence:** EOG signal change17717 reports `signal_key=technical_score`, old8.2971, new6.8252, delta-1.4719 and threshold exit `crossing_7`. In the rendered timeline it becomes `2026-10-19 · signal ·` with From/To/Actionability all “Unavailable,” plus generic reason codes. The name and changed values are absent. Since `episode_id` is null, this row has no link. The same defect affects other real rows such as17718/tightness_score.

**Established root cause/location:** `app/templates/partials/_ticker_intelligence.html:45` renders all timeline DTOs as lifecycle-transition fields (`event_type/change_type/alert_type`, from_state/to_state), but `signal_change_payload` at `app/services/setup_lifecycle/query_service.py:752` exposes `signal_key`, `old_value`, `new_value`, delta and threshold fields instead. The template only links rows having `episode_id`.

**Reproduction/evidence:** Expand actual EOG Setup Lifecycle in Run45 or44. Compare `real-lifecycle-True.json -> timeline` with `http-lifecycle.json` HTML and the live screenshot `forensic-eog45.png`. The actual data exists; this is presentation loss, not missing source data or a lifecycle calculation defect.

**Coverage gap:** TI's lifecycle case checks broad strings such as states, blockers and timeline existence. BI checks a primary/secondary episode, not a rendered signal name and before/after value. Both suites pass despite the lost meaning.

**Minimal recommendation:** Render timeline entries by their existing DTO kind; add signal name/value/delta/threshold and a supported snapshot/change-detail destination. Retain publication predicates and bounded date ordering. **Confidence: PROVEN.**

### F05 — Accepted guidance with a null effective session is falsely unavailable

**Affected:** D3 / CERI guidance availability. **Requirement:** D3 §4: reuse existing guidance semantics and accurately distinguish absence from available persisted evidence.

**Expected:** Accepted guidance follows the established CERI read contract when an exact effective session is absent.

**Actual:** Accepted RAISED EPS/FY guidance88881 has a valid source record, effective_at before the selected cutoff, and null effective_session. `_guidance_summary` reports AVAILABLE and selects88881; workspace `_ceri` returns `{}` and HTML says “No accepted guidance available.” Reproduced against the migrated disposable schema. The actual database contains zero accepted null-session guidance rows, so no current live occurrence is claimed.

**Established root cause/location:** `app/services/ticker_intelligence_service.py:239` requires `effective_session <= cutoff.date()`, which SQL excludes when NULL. The established `_guidance_summary` at `app/services/ceri/query_service.py:2262` expressly admits an absent effective session and uses effective_at/session for selection. D3 separately reimplements this selection.

**Reproduction/evidence:** See `adversarial-migrated-results.json -> null_session_guidance`: canonical status AVAILABLE, selected ID88881; workspace `{}`. Source record88880 and company8000 exist, and production FKs were retained.

**Coverage gap:** The original feature seed has no guidance rows; its CERI test never proves positive accepted-guidance rendering.

**Minimal recommendation:** Reuse the established guidance selection contract or a bounded equivalent with the same null/date semantics. Add a positive null-session case and a future-effective case; do not broadly relax cutoff checks. **Confidence: PROVEN.**

### F06 — CERI detail executes expensive history summaries the workspace discards

**Affected:** D3 / CERI detail performance. **Requirement:** D3 §9; V §6.3: bounded, efficient read projections; avoid full-history work just for a small panel.

**Expected:** Recent-detail loading fetches the bounded records and authority context needed to render the panel, without repeated expensive full matching-history counts and aggregate comparison summaries.

**Measured actual:** EOG CERI detail took7040ms and4371ms in two instrumented service calls, with20 SQL reads each. A subsequent warmed HTTP call took2401ms. In the slower sample, CERI change COUNT took2298ms, page selection1498ms and run-pair aggregate1302ms. The other sample spent1126/1494/1125ms on those operations. The panel consumes only the recent `items`, not total counts or the full comparison-context aggregate. Its initial response remains independent, which limits the impact to the expanded section.

**Established root cause/location:** `_ceri` at `app/services/ticker_intelligence_service.py:179–180` calls the full list contracts. `CeriQueryService._database_changes` at `query_service.py:404` executes its total at433, page selection and run-pair aggregation at489; `_database_alerts` at763 executes another total at810. Bounded output does not bound all this backend work.

**Reproduction/evidence:** Run `probes.py real` with real sessions READ ONLY. `service-measurements.json` stores counts, durations and slow SQL prefixes. `http-measurements.json` records response bytes and HTTP durations. Measurements are local, partly concurrent with tests; they are not an SLA or capacity benchmark. The redundant aggregate work is established; a particular index or universal speedup is not established.

**Coverage gap:** The TI budget assertion at `tests/test_ticker_intelligence.py:750` limits summary SQL count and response size on tiny fixtures. It does not measure detail queries, history scale or latency. Browser lazy/cache tests correctly pass but do not prevent this backend cost.

**Minimal recommendation:** Add the smallest shared-predicate bounded recent-items read mode, omitting unused total/comparison aggregates for this adapter. Inspect the resulting plan before any index change, preserve existing CERI API behavior, and measure this same path afterward. **Confidence: PROVEN.**

### Unverified risks and optional improvements — excluded from severity counts

- Ranking results cap at100, Winner estimates at100, predictions at10 and lifecycle episodes at20. Most history panels have a deeper page link. Ranking overflow has no pagination/truncation disclosure; only five real profiles and three fixture profiles were verified. Do not certify “every profile at any scale” from those cases.
- No concurrent publication test establishes a coherent multi-query snapshot across all independent panels. Combined evidence-ID guards work in their tested cases, and source dates are explicit; these do not prove arbitrary concurrent authority changes safe.
- Winner `_estimate_payload(None, ...)` leaves registry-derived model labels/status unavailable when a model FK exists. Source/version fields are present. A real supported model-backed estimate was not available for a complete display check.
- Positive catalyst and earnings-detail display needs a representative eligible fixture/live example, including revision timing. Current catalyst rows are independently current and labelled that way; their relationship to the selected score cutoff warrants explicit verification.
- Broad metadata is loaded in some existing serializers (for example, approximately517KB lifecycle and433KB Winner composition dictionaries before template projection). The HTTP responses are much smaller. Peak memory and concurrent load were not measured; no out-of-memory finding is asserted.
- Existing report wording should be updated after remediation: a passing fixture suite cannot substantiate “no required work remains” while these cases fail. No application rewrite or generic intelligence framework is recommended.

## E. Deliverable-by-deliverable assessment

**D1 — focused review PASS.** The dock is implemented globally through the shared shell, occupies its own desktop space, remains non-modal, and has meaningful keyboard/race/retry/popup tests in both engines. Search stays local, deduplicates in SQL, supports history and established aliases, escapes wildcards and exposes ambiguity conservatively. A valid decision uses a matching current COMBINED projection, compatible result pointer, frozen READY proof, terminal eligible owner and matching raw identity. It does not use maximum upload ID. Default preview score fields share one frozen envelope. No specific D1 blocker was reproduced. Large-universe capacity and live duplicate-identity behavior remain bounded review limitations.

**D2 — focused review PASS.** Direct qualified URLs, eligible historical selection, explicit unavailable states, complete shared card rendering, cached chart reuse, frozen stop/target and source labels passed the executed cases. Run45/44 values matched actual immutable sources. Optional technical-source behavior preserves the old chart routes. The page's expected combined ID protects chart and run-specific intelligence calls from the tested pointer change. This is not proof of all possible concurrent source changes.

**D3 — FAIL.** Four real adapters, ten disclosures, independent summaries and lazy details are present. Rankings and the actual insufficient Winner examples work for the verified cases, and current-independent dates remain distinct. CERI details violate authority/context rules; Winner summary selection diverges from the established selector. Lifecycle signal changes and accepted guidance lose required information. CERI detail has measured avoidable overhead. The previous implementation PASS/readiness claim is not sustained.

## F. Cross-system evidence correctness

| Boundary | Result |
| --- | --- |
| Identity | Search returns qualified local keys; workspace preserves them. Multiple exchange assignments fail closed rather than attach an arbitrary symbol-scoped decision. No wrong-ticker assignment was reproduced. |
| Decision and producers | Combined scores come from the selected immutable envelope. Fundamental/technical IDs are pinned and compared through the established compatibility policy. Missing/incompatible details remain missing. Mutable999/888/777 test scores do not leak into these paths. |
| Decision and rankings | Actual selected-run profile values and frozen configuration fingerprints remain tied to the decision context; combined rank is labelled separately. Higher-volume profile completeness is not established. |
| Decision and Winner | Exact combined pin plus selected run/ticker prevents the tested cross-run substitution. Candidate/superseded exclusion works. Choosing one serving version is wrong under F03's reproduced state. |
| Current CERI | Own dates are correctly separated from decision dates. Internally, headline authority and detail authority disagree (F01), while revision rows cross source contexts/cutoffs (F02). |
| Current Lifecycle | Own published daily state and date are explicit. Primary/blockers were verified. Timeline signal DTOs are loaded but inadequately rendered (F04). |
| Source changes | Expected combined ID and frontend request-version guards work for their tests. No all-source atomicity claim is justified. |

The answer to the review's central question is **yes**: individually persisted data can produce an incorrect combined picture. F01/F02 prove this for CERI. Dates alone do not repair a context join that is already wrong.

## G. Measured performance and implementation quality

Actual data volume:5039 uploaded rows,594 CERI companies,596 IB contracts. Three sequential service samples per basic path, with fresh read-only sessions, gave:

| Path | SQL reads | Samples, ms |
| --- | ---: | --- |
| Search EO | 1 | 373.19,41.95,36.05 |
| EOG summary | 3 | 135.11,40.53,43.95 |
| EOG workspace composition | 6 | 74.50,57.15,72.56 |

Two samples per intelligence mode:

| Section | Summary SQL / ms | Detail SQL / ms | Actual detail HTTP bytes / ms |
| --- | --- | --- | --- |
| CERI | 5 /267.38,119.61 | 20 /7040.12,4371.36 | 61289 /2401.39 |
| Lifecycle | 5 /141.66,99.80 | 15 /856.65,619.11 | 11339 /212.24 |
| Rankings | 5 /246.22,102.20 | 5 /97.09,109.38 | 16402 /73.52 |
| Winner | 6 /159.36,88.17 | 7 /93.80,136.49 | 4925 /83.83 |

Other sequential HTTP observations: search243.58ms; preview65.01ms; workspace130.62ms/22321bytes; historical workspace97.90ms/22325bytes; chart706.04ms/171408bytes. These differ from direct service timings because they include rendering/serialization/connection and cache effects. They should not be combined into a synthetic benchmark.

No actual N+1 query explosion was observed. Existing alert context and Winner upstream evidence are batched; ranking envelopes are joined, so the certification call can use the request identity map. Search/preview do not download the catalog/history or heavy intelligence payloads to the browser. Four summary requests repeat identity resolution and some adapters build more DTO data than they return, but measured fast paths do not justify a new cache or platform. Native disclosure state, AbortControllers and one service per presentation concern are proportionate. The significant avoidable measured issue is F06; duplicated source-selection policy also directly causes F01/F03/F05.

Returned lists are bounded; this does not mean every query has constant work. Search name/substring lookup still aggregates/scans persisted rows, and eligible-decision iteration can continue through ten-row batches of rejected candidates. Large-history behavior, peak process memory and concurrent throughput remain unbenchmarked. No new index is recommended without a measured plan.

## H. Tests: executed results and coverage quality

All commands ran from `C:\Users\Ivica\Documents\SwingLens` with the existing virtual environment. Test writes used the repository's disposable PostgreSQL patterns; no existing test was repaired or rewritten.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ticker_intelligence.py tests/test_ticker_workspace.py tests/test_ticker_search.py tests/test_ticker_chart_panel_routes.py tests/test_chart_data_service.py tests/test_score_card_view_service.py tests/test_dashboard_upload.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_route_security.py -q --tb=short
# 95 passed, 1 warning, 119.73s

.venv\Scripts\python.exe -m pytest tests/ceri/test_ceri_query_service.py tests/setup_lifecycle/test_query_service.py tests/test_ranking_profile_service.py tests/test_ranking_profile_routes.py tests/winner_probability/test_routes_api.py tests/winner_probability/test_estimate_lifecycle.py -q --tb=short
# 84 passed, 1 warning, 52.17s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 37 passed, 1 warning, 122.82s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 18 passed, 1 warning, 102.09s
```

The warning is the existing Starlette TestClient/httpx deprecation. Total234 independently executed passing cases. Test output is saved in `backend-tests.txt`, `subsystem-tests.txt`, `chromium-tests.txt`, `firefox-tests.txt` under the diagnostic directory.

**Meaningful coverage:** D1's SQL tests verify exact/prefix/name history and alias validity; preview tests compare frozen versus deliberately changed compatibility rows and invalid newer candidates. D2 tests compare all builder fields, preserve zero, reject incompatible pins, and check pointer change. Browser tests assert actual canvas, rendered scores, popup URLs/opener, retry, navigation races and resizing rather than just page200. Existing chart/service/security/regression cases add independent compatibility checks.

**Fixture limitations:** D1/D2/D3 backend fixtures copy tables while removing foreign-key constraints; snapshots/configuration evidence are direct inserts rather than full writer executions. They exercise actual SQL read predicates but cannot establish native writer provenance or FK integrity. Browser fixtures use a migrated PostgreSQL database and representative direct seeding. The primary missing dimension is competing source generations and full detail semantics, not simply an absence of SQL tests. Some subsystem unit tests use in-memory collections/mocks; passing them alone would not validate production SQL predicates.

**Critical gaps:** No original test validates quarantined change/alert details, revision context/known-at pinning, equivalence with canonical Winner selection among serving versions, accepted null-session guidance, real signal before/after rendering or detail-history performance. Error isolation is injected with a monkeypatched reader/HTTP503; page-cache recovery is simulated with PageTransitionEvents. These are useful checks, not a complete real-browser cache/publication concurrency certification. Positive model-backed Winner/catalyst/earnings paths and high-volume truncation need additional cases.

Temporary adversarial diagnostics can be reproduced without changing production or test files:

```powershell
.venv\Scripts\python.exe output/forensic-review/probes.py adversarial
.venv\Scripts\python.exe output/forensic-review/probes.py adversarial --migrated
# Both completed successfully; the four counterexamples remain present.
```

The second command creates a safely named disposable database, runs existing Alembic migrations there, seeds the existing feature fixtures, applies each controlled case inside a rolled-back savepoint, and drops that disposable database in a finally block. Recorded migrated database: `swinglens_pytest_8dca85a16240`. Probe output is evidence of wrong results, not a claim that additional application tests passed. An initial diagnostic attempt lacked a mandatory synthetic source idempotency key; only that temporary fixture was corrected, then all cases were rerun with full schema. No application failure is attributed to that diagnostic setup error.

## I. Runtime/browser observations and preservation

### Startup conflict and process accounting

Port8000 was free; process enumeration found no previous authoritative `app.serve`, `app.worker` or `app.worker_supervisor` process. Supported startup is `swinglens.ps1 start`. Inspection established:

- `scripts/ops/SwingLensLifecycle.psm1:878` starts the stack, performs Alembic upgrade for a new generation and calls the canonical runtime launcher.
- `app/services/canonical_runtime_launcher.py:36` requires the durable worker and launches `app.worker_supervisor`; this is not a web-only start.
- `app/services/background_worker.py:781`, `:807` and `:851` recover abandoned/stale jobs and claim work. The current NORMAL settings enable Winner maturation, automatic cohort refresh and market-data prewarm.
- The real DB has50 RUNNING/QUEUED jobs in the inspected pending-state set, including CERI_PROVIDER_INGEST_BATCH182 with an expired1October lease and queued provider batches183 onward. These are retained state, not proof of live ownership. Starting a claim-capable worker could act on them.

Normal startup therefore conflicts with the explicit restriction on unrelated processing and real pipeline/evidence mutation. It was **not invoked**, no lifecycle safeguard was disabled for an authoritative runtime, and no reset, cleanup, repair, schema upgrade or job recovery command was issued against the real DB.

Started exactly one manual review process: `.venv\Scripts\python.exe output/forensic-review/readonly_server.py`, server PID74300, bound127.0.0.1:8779. It sets every route DB session to transaction READ ONLY, rejects non-GET/HEAD before handlers, disables worker/automatic processing settings for this process, and runs Uvicorn with `lifespan=off`. It is an isolated presentation check, not an authoritative SwingLens generation. The Playwright CLI session was named `forensic-review` (initial browser PID75216). Both manual browser tabs and that session were closed. The server was stopped through its own exec session; interruption exit1 is expected. Disposable test web processes were managed and stopped by their fixtures.

Final checks: port8000 free, port8779 free, no authoritative web/worker/supervisor process. No preexisting process was stopped by this review. No commit or push was made.

### Actual persisted-data browser walkthrough

| View/action | Observed and compared evidence |
| --- | --- |
| Dashboard/dock | Shared navigation entry; own right column; focused input; EO six local results; Enter creates EOG preview without navigating dashboard. |
| Independent opening | Explicit Open Workspace leaves dashboard/dock in tab0, creates EOG in tab1 with `identity=ceri:399&run_id=45`; popup opener null. Native fallback independently exercised in both automated engines. |
| Default EOG | Selected45, eligible selector45/44/36; evidence46423; final8.0815, fund9.1093, technical6.8252; rendered8.08/9.11/6.83. |
| Historical EOG | GET form selects44 and preserves identity in URL; evidence45121; final8.0877; chart stop155.3467/target162.8665 versus45 stop155.3293/target162.9537. |
| Chart | 758 cached daily bars through19October2026, volume/SMAs, summary and research levels; current-price/run-specific-level label visible. |
| CERI | Snapshot5136: opportunity7.73, risk0, Low confidence, coverage50%, Positive posture, own cutoff20October03:05:25+02. Same current projection on runs45/44. Context defect in F02 independently measured beneath the score. |
| Lifecycle | Episode910 READY/GENERIC/BLOCKED, confidence72/NORMAL, blockerMARKET_POLICY_BLOCK, current as-of19October. Actual signal meaning missing as F04. |
| Rankings | Five actual profiles. Best rank2 in45,3 in44. Frozen per-profile values and run labels change correctly; main combined rank remains1. |
| Winner | Prediction947/estimate947 for45,763/763 for44. Published but insufficient/no_eligible_cohort, sample0; source cutoffs differ correctly; no0% probability fabricated. |
| Known/no evidence | AZTA/ceri:128 renders200/no eligible decision; CERI/lifecycle empty, rankings/Winner no selected eligible run. At390px, scrollWidth390 with readable stacked sections. |
| Errors/legacy | Actual unknown ticker404; bad identity422; mismatched expected chart evidence409. Old chart HTML200/17429bytes and old chart API200. Independent section503/retry demonstrated in disposable browser tests. |
| Ambiguous identity | Actual catalog had no ambiguous group and CERI had no duplicate symbol. Seeded ambiguity covered by SQL/API tests; actual persisted ambiguous browser case NOT VERIFIED. |

Screenshots were visually inspected: `forensic-dashboard-dock.png`, `forensic-eog45.png`, `forensic-eog44.png`, `forensic-missing-mobile.png`. The current browser console check returned zero errors and zero warnings; AZTA's four intelligence reads returned200. Console absence is supporting evidence, not the basis for evidence-correctness conclusions.

### Preservation evidence and limits

`source-baseline.json` and `preservation-check.json` confirm **1292 source/configuration/test/script/migration files unchanged** by SHA256. The report and temporary review diagnostics are the intentional new files. Git diff/status retained unrelated work. CLI-generated snapshots are review artifacts.

All production DB probes and browser route sessions explicitly used `SET TRANSACTION READ ONLY`; writes occurred only in disposable DBs. Inspected row-ID fingerprints across pipeline/job/current-projection/CERI/lifecycle/ranking/Winner tables remained stable between recorded observations. Full pipeline_runs/background_jobs row-content hashes were also stable across final browser/server closure. These hashes are supporting preservation checks, not a byte-for-byte forensic image of every table/artifact or a claim about concurrent activity outside this review. No code path in the review was authorized to mutate production evidence.

## J. Prioritized minimal remediation recommendations

1. **F01 and F02 first:** unify CERI current authority for score and related current records; bind revision rows to verified source lineage/context/cutoff and render period identity. Keep historical audit visibility deliberately distinct. Regression risk: changing shared CERI predicates can alter existing list/history results, so preserve those contracts and add focused migrated-DB cases.
2. **F03:** use canonical per-prediction/outcome estimate selection, preferably batched. Keep selected-run combined pinning, outcome-version FKs and candidate/superseded exclusion. Regression risk: accidentally choosing a current LATEST_RESCORE or today's outcome definition for a historical decision.
3. **F04 and F05:** adapt each timeline DTO's actual fields and source links; reuse accepted-guidance null/date semantics. These are small read/presentation corrections; no lifecycle or guidance calculation changes are needed.
4. **F06:** after authority fixes, remove only the unused heavy aggregate work from the recent CERI projection and remeasure. Preserve full CERI list APIs. Do not begin an unrelated database/index/platform rewrite.
5. Close the recorded positive-data, overflow and concurrent-publication coverage gaps; then perform separate end-to-end certification. Resolve operational activation through the supported lifecycle workflow in a task that explicitly allows its worker/recovery effects.

These are recommendations only. **No remediation was implemented.**

## K. Readiness verdict

| Next stage | Ready? | Reason |
| --- | --- | --- |
| Prepare scoped remediation | **YES** | Six findings have exact locations, reproducible evidence and minimal correction directions; three affect core intelligence correctness. |
| Final end-to-end certification | **NO** | Correct F01–F06 and add the missing boundary/detail regressions first; retain the explicit unverified cases in the certification plan. |
| Operational use of the full workspace | **NO** | Proven CERI context/authority and Winner selection defects remain. Port8000 activation was not performed; normal startup would act on unrelated persisted work. |

Review complete. Stop here; do not begin remediation from this report.
