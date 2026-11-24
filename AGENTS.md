# SAM Report maintenance rules

## 中文维护要点与本轮授权

- 2026-11-24 用户要求评估 SAM/PA/Trend 的统一管理、确认本地 PA 安装并同步 GitHub。本轮接入独立 packages/pa 源码、源码安装 ZIP、测试和文档；推送 codex/pa-report-admission 分支。不得据此改 SAM 业务、移动维护根、覆盖生产、合并 main、启用根 CI、改可见性或创建 tag/Release。本机 PA 原项目仍为其唯一业务编辑源，packages/pa 是经审查的同步边界。

- 2026-11-24 用户追加要求将仓库改为 Public、更新中文展示、增加虚构样板并规划 PA/Trend 接入。本轮允许经公开前审查后更新这些文档、精确清单并推送、改变可见性；不改业务、不部署、不重命名仓库、不选许可证或发布稳定版。下文旧 Private 状态仅为历史。

- 2026-11-24 用户明确批准分发包验证后提交并推送。本轮仅晋升已验收的固定 Skill 包、中文指南、管理说明和精确包清单；不修改业务、不部署、不迁移维护根、不创建 tag/Release。后续新增操作仍需单独授权。

- 每次先读需求、决策、路线图并检查 Git 和部署状态；原私有维护区 `project/src` 仍是唯一业务修改源。
- 本轮用户明确批准中文优先管理文档、安全审核及精确提交/推送；此授权取代下方旧的“仅本地文档、不提交”限制，但不包含业务修改、生产同步、tag/Release 或维护源交接。
- 保留六模块、完整来源 25 页、14 项检查和原有阈值；未知不是零，Check13 严格超过 120 天，Convert 仅建议。
- 开发分支、业务修改、合并 main、部署与发布按独立授权执行；不得 force push、改写历史或删除未提交工作。
- 提交前检查准确文件清单、实际暂存内容及敏感信息；不使用全量 git add，不上传任何真实客户数据。
- 修改后测试并记 CHANGELOG，失败不得发布。日常报告不改源码、不提交、不上传。发布必须可追溯、可回滚。
- 下面英文规则作为详细约束保留；本轮中文授权仅取代其中过时的管理文档提交边界。

## Current authority and Git facts (2026-11-23)

- The first main commit 54c9acad9752ced4aeb99e3548099bec6455a5be has been pushed to the private repository. No stable tag, Release, CI policy or production deployment follows from that fact.
- The private maintenance workspace's project/src remains the sole business editing source. This repository's src is a sanitized version boundary; installed Skills are runtime copies. Resolve private locations from the local record, never guessed paths. Do not independently edit both trees.
- Current approval covers local governance documents, the source-role map and necessary package inventory refresh/validation only. No additional commit, branch, push, CI, branch protection, synchronization or source handover is implied. See docs/maintenance-boundary.md.
- Keep the initial commit's 38 portable tests and 188 synthetic assertions separate from fresh working-tree/package checks.

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
