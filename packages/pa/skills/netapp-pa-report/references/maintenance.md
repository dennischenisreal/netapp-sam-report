# Maintenance Routing

This Skill is a Windows directory junction to `skills/netapp-pa-report` in the
managed project. `scripts/run.py` resolves the real repository path. Update that
source only; moving the repository requires reinstalling the junction.
The shared suite stores the reviewed PA mirror under `packages/pa`; see
`docs/github-sync-plan.md`. Package and synchronization tools export only the
exact reviewed source set and refuse independently edited target files.

## Code Map

| Component | Files |
| --- | --- |
| Approved entry | `run_pa_report.py`, `pa_runtime.py` |
| PA decoding and native intervals | `validate_pa.py`, `pa_probe.py` |
| Focused supplementary counters | `pa_compact_counters.py`; shared definitions in `pa_capabilities.py` |
| ASUP port role enrichment | `pa_asup_network.py` |
| Base five resource tables | `prepare_performance_report.py` |
| Consolidated approved report | `performance_analysis.py`, `prepare_performance_compact.py` |
| Workbook authoring | `build_performance_workbook.mjs` using the bundled `@oai/artifact-tool` |
| Read-only verification | `verify_performance_compact.py`, shared baseline checks |
| Initial defaults | `performance_config.json` |
| Legacy/prototype, not the approved entry | `run_performance_v2.py` CLI, `prepare_performance_v2.py`, full V2 verifier, legacy publisher |
| Future trend groundwork, not connected to approved driver | `performance_history.py` |

The driver reuses the legacy module's narrowly scoped `build()` function, not its
diagnostic/trend orchestration. It does not build intermediate baseline workbooks.

## Make A Change

1. Inspect the existing code and the user's latest requested scope.
2. Add synthetic regression tests for the behavior; never commit real customer fixtures.
3. Update metric definitions, units, headers, verifier and Skill contract together.
   Bump `VERSION` and `CHANGELOG.md`. Change metric/model versions when meaning or
   compatibility changes; do not reset resource identity or silently mix versions.
4. Run `python -B -m unittest discover -v`, `python -B tools/check_source_privacy.py`,
   the installed Skill `--doctor`, and the Skill creator's structural validator.
5. Use a small real Site in a new private run for affected workflow changes.
   For changed workbook layouts, render and visually inspect all affected views.
   Do not publish smoke-test files into the formal report directory.
6. Record results and remaining gaps. Use `docs/github-sync-plan.md` for external
   synchronization, which is separate from generating reports.

## Runtime And Failure Handling

Python 3.12 with openpyxl 3.1.5 and the existing bundled Node/Artifact Tool have
been tested locally. 7-Zip is required; `PA_7ZIP` can override its executable.
`PA_NODE` or private local `node` config can select Node. The Skill launcher
selects private configured Python, then the existing bundled Python, then its own interpreter.
Dependencies must already exist; generation never downloads or installs packages.

`tools/install_skill.ps1` installs only a junction and, if available, the project's
Node dependency junction. Existing conflicting targets are rejected, not removed.
Copy `config/project.example.json` to ignored `project.local.json` for a new machine.
Runtime executables are machine-local settings, not source dependencies to upload.

The driver refuses duplicate node windows, ambiguous node names, changed input
archives during execution, existing run paths, and existing publication filenames.
It retains `run.json` with failure status when a stage fails.
It records the actual code hashes and approved metric/model version.
Explicit cache reuse trusts the original local decode; SHA checks do not prove that
someone has not edited its content. Keep caches private and protected.

The native renderer has exhibited crashes during teardown after successful export.
The approved driver runs a dedicated export worker with `--managed-export`:
it awaits all previews and the XLSX save, atomically writes a filename/SHA completion
marker, then stays alive. The parent validates that marker and SHA before explicitly
terminating its worker, avoiding native teardown. An unexpected worker exit or
15-minute timeout still fails. A newly saved valid ZIP and full saved-cell/native-
threshold verification remain mandatory. `worker_status.json`, logs and
`builder_result.json` distinguish controlled termination from failure.
Legacy `build()` without this option retains its historical exit-1 accommodation
and is not the approved driver's completion rule.
Failure after copying to the formal output may leave an incomplete file or missing
sidecar; investigate it and use a new label rather than claiming successful delivery.
