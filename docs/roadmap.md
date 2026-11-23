# Roadmap

## 中文当前计划（2026-11-23）

1. 当前获批：完善中文用途、报告效果和使用指南，审核管理文档并提交/推送；不修改业务、不部署。
2. 下一候选：为同事提供完整可分发 Skill 安装流程，先确认目标 Agent，再做新机器验收；尚未实施。
3. 唯一维护源交接、CI/分支保护、稳定 tag/Release 分别批准；不能把源码上传当成通用安装或稳定发布。

以下为之前基线及审批记录；其中等待管理文档提交授权的描述已被本轮授权取代。

## Verified baseline (2026-11-23)

The reviewed 87-file baseline was pushed to Private main as 54c9acad9752ced4aeb99e3548099bec6455a5be. A clean local clone passed 38 portable tests and 188 synthetic assertions. Remote commit/tree and Private visibility were verified. No stable tag/Release, CI, branch protection or production deployment is implied.

## Current approved local task

Refresh governance and clarify the sole active business source without moving code, private settings or Skills. Refresh affected package fingerprints and validate exact boundaries. Do not commit or push the new working-tree changes without separate approval.

## Next approval gates

1. Review the governance diff, exact revised inventory and fresh checks before approving its commit/push. Pending local changes are not automatically visible on GitHub.
2. If this repository should become the active business editing root, approve the handover gates in maintenance-boundary.md, reconcile implementation/configuration and verify production compatibility before switching authority.
3. Separately evaluate CI and branch protection; do not claim they already exist or alter remote settings without approval.
4. Complete visual/fresh-environment release evidence, then propose a separately approved tag/Release and tested rollback target. Do not assume v1.0.0 or relabel version 0.5.0.
5. Admit other report Skills incrementally after per-Skill review; do not restructure working modules in advance.

Production synchronization is a separate decision with backup, drift checks and regression. It is not a prerequisite for safely preparing local source history and must not happen automatically.
