# PA Report Maintenance

- The approved entry is `run_pa_report.py`; the full legacy V2 CLI is not the accepted report.
- Preserve `skills/netapp-pa-report/references/report-contract.md` unless the user's
  request explicitly changes the relevant behavior. Keep labels and verification aligned.
- Customer archives, reports, native caches, private config and run manifests are local
  evidence only, never source fixtures or GitHub content, including in private repositories.
- Run synthetic tests and `tools/check_source_privacy.py` before a source handoff.
  Real-data smoke tests use new `.local/` run directories, not formal publication.
- Update the canonical Skill in this repository, not a second installed copy.
- GitHub sync does not authorize changing SAM, choosing public visibility, or force pushing.
