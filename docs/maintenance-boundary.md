# One business source during Git adoption

## Current roles

| Role | Authority and allowed use |
|---|---|
| Existing private maintenance workspace, project/src | Sole active business editing source, subject to task and branch approvals. Private settings and original history stay here. |
| This repository, src | Reviewed sanitized version boundary. No independent business development here before approved handover. Governance-only work does not move production. |
| Installed Skills and legacy adapters | Runtime copies, not editing sources. Synchronization requires approval and evidence. |
| ASUP, knowledge files and production reports | Private external inputs/outputs, never Git source. Reporting uses existing validated production entrypoints. |
| .local | Ignored synthetic tests, runtime and private receipts. Never stage, upload or treat it as source. |

Actual absolute private locations belong in the host's local record, not this shared document. Do not guess sibling paths or deployment settings. This decision clarifies one source; it does not move files or promote this repository to production authority.

## Work before handover

1. Inspect both roles and the approved task; read requirements, decisions and pending work.
2. For ordinary reports, use the existing validated production Skill. Do not edit either source tree, switch branches, commit or upload.
3. For approved governance work here, change only documents and necessary package fingerprints. Review the diff, preserve runtime bytes and run checks. Commit/push remain separate approvals.
4. For business changes, propose the single editing source, approved branch, compatibility impact and tests first. Never implement independently in both trees.
5. Promotion into Git is reviewed: compare against the published commit, isolate private rules/data, update the exact intended inventory and verify numerical/identity contracts. Never copy the private workspace or its .git wholesale.

## Future handover plan, not executed

1. Obtain explicit approval to make this repository the business editing root and create the appropriate branch. First-push approval does not authorize this.
2. Back up source, tools, configuration, deployed fingerprints and pending work. Reconcile private active code versus sanitized baseline; record intentional differences and unresolved rules/adapters.
3. Verify private configuration and deployment mappings without putting real settings in Git. Preserve production entrypoints and outputs during isolated testing.
4. Run unit/synthetic suites, numerical/identity checks and non-overwriting compatibility regression. Check production configuration/entrypoints; fictional package tests alone are insufficient.
5. After acceptance, separately approve the source-root switch and any production synchronization. Record the cutover commit, verified rollback procedure and compatibility mapping. Mark the old workspace as reference, not a second writable source.

If a gate fails, retain the current private source and runtime. Do not switch partly, weaken tests or deploy to hide drift.

## Other report Skills and release

The user intends to add other report Skills. Admit each by scope/entrypoint, dependencies/configuration, data sensitivity, synthetic fixtures, output contracts and rollback. Define ownership and shared dependencies before structural changes; do not refactor the working six-module SAM pipeline or rename the repository merely for expansion.

The first main push established traceability, not CI, branch protection, stable tag/Release or source migration. Preserve exact prior evidence. Future remote settings, commits, pushes and deployments require their applicable approval.
