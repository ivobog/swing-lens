# CERI filing authority classification

SEC company-ticker metadata is a coverage index, not a complete security master.
Some listed banks file Exchange Act reports with the FDIC, OCC, or Federal
Reserve. An absent SEC ticker remains unresolved unless its reporting authority
has been explicitly reviewed.

`config/ceri_filing_authorities.yaml` is the reviewed, version-controlled issuer
registry. Entries require an exact canonical ticker, company name, reporting
authority, authority identifier, review date, explanation, and source URLs.
Duplicate tickers, unknown authorities, and incomplete evidence are rejected.
Review a primary regulator or issuer filing before adding an entry; do not infer
an exemption from an absent ticker, industry, name similarity, or third-party CIK.

Company onboarding and SEC identity repair apply reviewed non-SEC entries using
the existing `sec_applicability=NOT_APPLICABLE` fields. The persisted reason
retains the authority, identifier, review date, coverage limitation, and evidence
URLs. Existing provider IDs and CIKs are preserved. Explicit existing coverage
exemptions are also preserved by identity repair. An unknown issuer remains
unresolved; a missing SEC match cannot grant it a reviewed authority exemption.

The reviewed FDIC issuers include TowneBank (TOWN, certificate 35095) and
Northeast Bank (NBN, certificate 19690). NBN retained its ticker after its May 2019
reorganization and files current Exchange Act reports with the FDIC. Its former
holding company's SEC CIK must not be assigned as a substitute for current SEC
coverage. The registry retains the issuer announcement and current filing page
as review evidence.

The SEC batch scope excludes these reviewed issuers. Other provider scopes
continue to include them. The pipeline progress page displays the specific SEC
guidance coverage limitation even when the pipeline completes. This exemption
does not create FDIC, OCC, or Federal Reserve document ingestion.

To review a run's mapping changes before persisting them:

```powershell
python -m scripts.resolve_sec_ciks --run-id 51 --dry-run
```

Run the same command without `--dry-run` to persist the reviewed identities and
coverage decisions. The report separates `not_applicable` from resolved CIKs and
unresolved tickers. Then use **Retry pipeline preflight** on the existing blocked
pipeline. Reviewed non-SEC reporting authority and unresolved SEC identity are
different coverage decisions; neither supplies missing SEC guidance evidence.

Terminal SEC child outcomes project readiness, telemetry, unresolved tickers,
and failure diagnostics into the parent pipeline for completed, blocked, failed,
and cancelled repairs. A completed queue job waiting on a dependency does not
mean the complete pipeline finished.
