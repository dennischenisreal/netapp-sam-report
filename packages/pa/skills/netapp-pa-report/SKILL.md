---
name: netapp-pa-report
description: Generate recurring NetApp ONTAP PA performance Excel reports from offline Performance Archive batches, optionally enrich port roles from ASUP, and maintain the approved PA report workflow and metric contract. Use for PA report generation, PA performance analysis, and changes to this report, not SAM configuration/capacity reporting.
---

# NetApp PA Report

This Skill is the installed entry to the managed `netapp-pa-report` project,
not a second copy of its parsers. Current package baseline: version 0.1.1.

## Generate A Report

Read [references/report-contract.md](references/report-contract.md) before interpreting counters.
Use the supplied offline PA batch; collection tooling and customer collection conditions
are outside this workflow unless explicitly requested.

Run `scripts/run.py` relative to this Skill's directory:

```powershell
python "<skill-dir>/scripts/run.py" --doctor
python "<skill-dir>/scripts/run.py" --source "D:/Private/PA/Site-A/20261118" --site "Site-A" --label "20261118-AM-r1" --asup "D:/Private/ASUP/Site-A/11"
```

- One invocation handles one Site and one batch. Identify logs by archive headers, not filenames or folder dates.
- List available Site/batch directories first when asked to process multiple Sites. Run sequentially by Site; do not merge their input trees or multiple windows of a node.
- ASUP is optional. Select the same Site's actual snapshot directory containing `.7z` files directly. Missing ASUP leaves port role unknown; it does not invalidate PA metrics.
- A new label creates a new private `.local/runs/<label>/<site>/` directory. Refuse existing runs rather than silently replacing them.
- Add `--publish` when delivering an approved run. It reads `reports_root` from the ignored project `config/project.local.json` and creates one versioned workbook plus `.run.json` and `.verification.json` sidecars per Site. A generation request authorizes ordinary local output, not overwriting an earlier report.
- The existing local formal output is configured for the user's PA report directory. Do not hardcode personal paths in source or change that configuration merely to run a test.
- Use `--baseline-cache` or `--counter-cache` only for explicitly trusted local intermediates. Cache reuse verifies PA SHA and identity; it is not an independent re-decode or vendor calibration.

The accepted workbook has exactly nine sheets: Node, Aggr, Volume, FlexGroup,
Network Port, 热点卷分析, 协议与元数据, FabricPool, 指标说明.
Do not restore removed diagnostic/trend tabs without a new request.

Deliver only after the builder and `verify_performance_compact.py` succeed.
Retain the private run directory for provenance and later comparisons.
Inspect generated previews if the layout, labels, or rendering dependencies changed.
Report data gaps and calibration limits; never turn unavailable counters into zero.

## Maintain Or Extend

Read [references/maintenance.md](references/maintenance.md) for code ownership,
tests, versioning, GitHub synchronization, and trend compatibility.
The shared repository is `dennischenisreal/netapp-sam-report`, under `packages/pa`;
SAM and Trend remain separate Skills. PA's source ZIP contains the full project
and must be extracted before running its `tools/install_skill.ps1`; it is not a
single-folder Skill import ZIP. Check another installed copy's source and local
modifications before replacing it with a downloaded candidate.
Apply the user's new requirement without redesigning unrelated sheets.
Update metric definitions, labels, verification and synthetic tests together when changing a counter or threshold.

GitHub synchronization concerns code, Skill instructions, examples and synthetic tests only.
Never upload customer PA/ASUP, reports, private configurations, native caches or run manifests,
including to a private repository. Do not infer authorization to create a remote repository,
change visibility, push, or modify the SAM repository from ordinary report generation.
