# Architecture

During Git adoption, the private maintenance project's project/src is the sole active business source; this repository's src is a reviewed sanitized snapshot, not an independently writable implementation. Source, private configuration, deployment copies and outputs have separate roles; see [maintenance-boundary.md](maintenance-boundary.md). Clarifying these roles does not move files, redirect entrypoints or deploy production.

src contains the original orchestrator, six child modules, trend analysis and active legacy adapters. The orchestrator creates current-run artifacts and combines them into a final workbook. Path and customer rules come from explicit configuration snapshots. Production copies are not a second editing location.

tools/candidate_runtime.py installs reviewed source fingerprints only under this project's .local runtime and creates local configuration from fictional examples. sam.py and management tools retain the alternate report/trend safety gate. Installed candidate entrypoints are available for synthetic acceptance; deployment to user Skills is not part of this source package.

tools/validate_synthetic_report.py generates two fictional archives, knowledge workbooks and a fixed evaluation time plus historical samples. It invokes the real report entrypoint and checks independently computed workbook expectations. Scenario children use packaging/templates/isolation_guard.py to restrict file reads to the isolated scenario and the Python installation. No production ASUP or customer rules are required.

tools/acceptance_support.py supplies strict inventory/hash extraction. tools/package_candidate.py deterministically packages the exact reviewed source inventory into .local. All candidate inputs, runtime settings, reports and evidence remain untracked. Dependencies reuse the validated Python environment; a fresh dependency installation is a separate validation step.
