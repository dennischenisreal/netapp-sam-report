# NetApp SAM Report

Local, unpublished source candidate based on project version 0.5.0. No Git repository, tag, remote, CI or release is implied. Existing production Skills are not installed or changed by this package.

## Scope

Six modules analyze controllers, capacity, cases, firmware, network/EMS and fourteen best-practice checks. Complete-source reports contain 25 sheets. Only genuinely absent firmware sources permit the corresponding sheet to be omitted; corrupt sources remain failures. Preserve default thresholds, complete object identity, N/A versus zero, strictly greater than 120-day snapshots and advisory-only FlexGroup Convert.

## Local installation and tests

Use the validated Windows CPython 3.14 environment. Dependencies are listed in requirements.in and requirements.lock.txt; install dependencies only in a separately approved environment. The lock records tested versions, not a cross-platform or hash-locked dependency certification.

From this directory:

```text
python -B tools/candidate_runtime.py install
python -B tools/candidate_runtime.py doctor
python -B tools/candidate_runtime.py test
python -B tools/package_candidate.py --output .local/package-01
python -B tools/validate_synthetic_report.py --archive .local/package-01/netapp-sam-report-shared-candidate.zip --output .local/acceptance-complete --scenario complete
```

Repeat the final command using fresh output directories and scenarios missing-switch, broken-switch and identity-gap. The broken-switch scenario succeeds only when the parser rejects the corrupt source and no final report is delivered. Do not overwrite previous evidence. Packaging rejects changed or unreviewed source inventory; after approved source changes refresh and re-review fingerprints before packaging.

The installer writes only this project's .local runtime/configuration. It does not install into user Skills or deploy production. The managed sam.py report/trend alternate-configuration safety gate remains closed; do not weaken it to obtain a green test. Synthetic acceptance uses the existing real process_directory entrypoint and six actual subprocess modules, not patched business functions. Production reporting continues through the separately validated deployed Skill.

## Validation status

Before this management-only preparation, the private maintenance suite passed 302 tests and four isolated synthetic scenarios passed 188 assertions. Those are historical results, not all included as portable unit tests. This source candidate carries the fixture tests, parameter-path tests, packaging-safety tests and reusable full synthetic validator so future maintainers can repeat its own acceptance without the private project. Fresh results belong in .local, with the tested archive hash.

Seven key sheets were rendered in the earlier acceptance. Some cover descriptions and long headers were clipped; full 25-sheet pixel/Excel-print acceptance and a new-machine dependency installation remain unverified. No stable release is declared.

See AGENTS.md for approvals and docs/maintenance.md for simple commands. Private customer profiles, aliases, ASUP, knowledge files, reports, configuration, backups and chat exports must never enter source control, including private repositories.
