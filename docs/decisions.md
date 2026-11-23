# Design decisions

1. Retain original business implementation and entrypoints. Do not refactor numerical functions merely to initialize version management.
2. Externalize local paths and customer-specific rules in private configuration snapshots. Defaults, ordering and matching semantics remain compatibility requirements.
3. Keep the original private workspace and application checkpoint objects untouched. Prepare only reviewed sanitized files for a separately approved repository boundary; never copy its .git or push all refs.
4. Use main for verified stable code, feature/* for features and fix/* for repairs, after the respective Git action is approved. Version 0.5.0 is a project version, not evidence of an existing tag or Release.
5. Treat report generation as production use, not development. Do not modify sources, branches or repository state when making customer reports.
6. Retain a portable full synthetic validator and independent numerical oracle. Fault scenarios must remain failures or needs_review as appropriate; lower-level smoke success never replaces full acceptance.
7. Keep runtime fingerprints separate from package inventory. The package manifest records exact source bytes; runtime locks and private evidence remain local. Any source change requires a newly reviewed inventory before distribution.
8. Keep one business editing authority during Git adoption: the private maintenance workspace's project/src. Git src is the sanitized version boundary; installed Skills are runtime copies. Governance-only work here does not authorize business handover; see maintenance-boundary.md.
9. Treat the first private main push as traceability, not stable release. Preserve version 0.5.0 and historical test evidence. Initial commit/push approvals do not authorize future writes.
10. Admit other report Skills individually after inventory, dependency, entrypoint, synthetic-test and sensitive-data review. Do not restructure existing modules or upload private historical trees merely to anticipate expansion.
