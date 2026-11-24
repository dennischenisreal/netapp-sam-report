# Roadmap

## PA 接入分支（2026-11-24）

- 新增 packages/pa 独立源码与 0.1.1 源码安装 ZIP，不改 SAM 源码、原分发包或生产部署。
- 本地 PA Skill 已安装；PA 合成测试与隔离安装启动、SAM 回归分别验收，详细命令见 docs/pa-report.md。
- 后续 Trend 按历史容量、配置、性能三类接口整合，先处理身份、窗口、单位和版本兼容；不提前交付虚构趋势功能。
- 接入分支供 review，main 合并、稳定 tag/Release、根 CI 与维护源迁移另行确认。以下“PA 后续接入”条目按其历史阶段理解。

## 当前公众展示与多报告计划（2026-11-24）

- 用户要求将仓库公开并调整中文首页；加入纯虚构 SAM 样板表格及 SAM/PA/Trend 状态导航。公开前审查全部可达提交、内嵌包及远程附属内容，不改业务或生产。
- SAM 已交付分发候选；PA 后续单独接入；Trend 已有源码，独立分发验收待完善。详见 report-catalog.md。
- 不迁移现有目录、不重命名远程仓库、不替用户选择 LICENSE，不创建稳定 tag/Release。下面旧阶段的“Private/待制作分发包”按其历史日期理解。

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
