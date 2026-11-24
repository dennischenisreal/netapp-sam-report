# Initial Local Validation

Validated on 2026-11-23:

- 78 unit tests passed using the existing bundled Python 3.12 environment.
  The suite uses synthetic fixtures, with an optional local metadata fixture check.
- The Skill creator's `quick_validate.py` passed. On this Windows machine it was
  run with the system Python's existing PyYAML and `-X utf8`; the report runtime
  itself does not require PyYAML.
- The source privacy preflight passed and ignored local customer paths, source
  archives, workbooks, caches and run evidence were checked separately.
- Installed source-linked Skill `--doctor` passed. Re-running the installer
  preserved the same junction without overwriting another Skill.
- A small real two-node Site passed the approved nine-sheet workflow using an
  explicitly selected trusted baseline/native-interval cache and focused counter
  cache, with fresh source SHA/identity checks and ASUP role enrichment.
  This was an orchestration/export/verification smoke test, not a fresh independent
  decoding of every PA counter.
- The resulting workbook passed saved-cell comparison, unchanged baseline
  numerical metrics, native-threshold duration recalculation, ZIP checks and
  formula-error checks. CPU, zombie and port-role previews were inspected.
  No layout redesign was made during Skill packaging.
- Earlier failed export-worker attempts were retained as private failed runs.
  The final managed-worker path passed; synthetic worker tests cover successful
  hash-marked completion, unexpected exit and timeout.
- Existing formal initial report hashes were unchanged. Smoke-test output
  remained local and was not copied to the user's formal report directory.

Private run manifests and verifiers retain the actual evidence and report hashes.
They are intentionally not included in this source repository.

Not yet verified: GitHub push/CI execution, new-machine installation, a fresh full
decode through the new wrapper for every Site, AIQUM same-window calibration,
or monthly/quarterly/yearly trend/forecast reporting.

## Source Package Validation (2026-11-24)

PA 0.1.1 adds source packaging and reviewed synchronization under `packages/pa`
in the shared SAM/PA/Trend repository; it does not change report metric meanings.

- 88 synthetic/local unit tests passed in the canonical project.
- Source privacy preflight and Skill structural validation passed.
- The installed source-linked Skill passed `--doctor`.
- A deterministic full-source ZIP passed its exact file inventory, SHA256,
  privacy and ZIP checks. An isolated extraction was installed into a temporary
  Skills root and passed `--doctor` and 88 tests, with one optional private
  metadata test skipped. This uses the existing machine's dependencies.
- The shared repository's isolated SAM runtime passed its 38 regression tests.
  SAM business source and its existing distribution archive are not PA edits.
- This packaging pass did not generate or publish customer reports.

The source ZIP is an installation candidate, not a single-folder Skill import
ZIP or a stable Release. New-machine and cross-Agent installation, remote CI,
AIQUM calibration and the unified Trend Skill remain unverified/unimplemented.
Remote synchronization evidence is the actual pushed feature commit, not a
claim inferred from these local tests.
