---
name: "NetApp-SAM-Report"
description: "Orchestrate six ASUP analysis modules and assemble the SAM Excel report."
---

# NetApp-SAM-Report

Entry: `run.py <input-directory> [--interactive]`. Dependencies are pinned in the project requirements.

This is an unpublished shared candidate, not an installed production Skill. Select SAM_PROJECT_ROOT, SAM_PROJECT_CONFIG and SAM_SHARED_SKILL_DIR explicitly. Use only local private rules and a selected knowledge base; never infer customer identity from archive filenames.

Keep all six modules, 25 final sheets with complete firmware sources and fourteen checks. Only genuinely absent firmware source categories may omit their corresponding sheet; corruption and parsing failures must remain errors.

Default thresholds remain unchanged. Request custom thresholds explicitly. Check13 uses strictly more than 120 days; FlexGroup Convert is advisory only. Unknown capacity is not zero. Preserve full cluster/SVM/volume identity, original volume names, units, provenance and coverage warnings.

Children: 1-netapp-controller-eos-ma, 2-netapp-capacity-aggr-vol-fg, 3-jv-case, 4-netapp-firmware-shelf-io-sp, 5-netapp-network-ems, 6-best-practice-check.

Keep original ASUP, knowledge files and reports unchanged. Outputs and run evidence stay outside source control; do not deploy, commit, connect or publish automatically. See the project README and docs/architecture.md for configuration and installation limits.
