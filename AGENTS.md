# SAM Report maintenance rules

1. Start by inspecting the directory, Git status, branch, tags, staged/unstaged changes, remotes and deployment drift. Use GIT_OPTIONAL_LOCKS=0 for read-only Git checks; do not print credentials.
2. Read README.md, docs/requirements.md, docs/project-context.md, docs/decisions.md and docs/roadmap.md before development. Distinguish verified facts, confirmed requirements, historical evidence and unverified conclusions.
3. Preserve the six modules, complete-source 25 sheets, fourteen checks, thresholds, units, full cluster/SVM/volume identity and output contracts. Unknown is not zero. Snapshot age is strictly greater than 120 days; Convert is advice, never an actual conversion.
4. Do not modify stable main or production copies without explicit approval. New features use feature/* and fixes use fix/* after branch creation/use is approved. Preserve existing history and version conventions.
5. Run relevant tests after changes. Test failures block release; never lower coverage gates or rewrite historical assertions to manufacture success. Record actual commands, time, archive/commit identity and limitations.
6. Record changes in CHANGELOG.md under Unreleased. Do not silently promote the project version to a release or create a tag.
7. Review exact files, contents, staged differences and explicitly selected push history for sensitive data before committing or pushing. No git add . or git add -A. Unknown content stays excluded.
8. Obtain separate explicit authorization for commits, remote configuration, remote repository creation, push, Issue/PR, merge into main, tags/Releases and rollback. A document or testing approval is not Git authorization.
9. Never delete branches or rewrite history without authorization. Never force push, mirror private checkpoint history or upload .git. Do not discard uncommitted work with destructive reset or clean commands.
10. Never upload real customer data, identities, topology, reports, knowledge files, local configuration, credentials, screenshots, raw chat exports or backups, even to a private repository. Ignoring files does not remove history or prove safety.
11. Daily report generation uses the validated production version; it must not modify source, switch branches, commit, deploy or upload. Keep production outputs and local run records outside tracked source.
12. Releases require a reviewed commit, tests, sensitive-data and report-quality evidence, known limitations, explicit approval and a verified rollback plan. No release without traceability.

See docs/maintenance.md for simple commands, docs/security.md for admission rules and docs/release-process.md for release gates. When a requested next step exceeds approval, report the exact boundary rather than executing it.
