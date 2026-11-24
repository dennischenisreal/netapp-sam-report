# Changelog

## Unreleased

- 接入独立 PA 0.1.1 源码安装候选：packages/pa、九页性能报告口径、完整源码 ZIP 和校验和、中文安装说明、虚构样板及安全镜像工具。SAM 源码/入口和原分发 ZIP 保持不变；Trend 联合容量/配置/性能分析仍为后续计划，不将已有容量趋势冒称为统一 Trend。此次仅接入分支，不创建稳定 Release。

- 按维护者要求准备公众版中文首页、SAM 虚构样板及 SAM/PA/Trend 接入目录说明；不迁移已有源码或变更 Skill 功能。公开前单独审核全部可达历史与分发包，版本仍为 0.5.0，不创建稳定 Release。

- 新增 Codex / WorkBuddy / TRAE 通用 Skill 分发候选包及中文安装、知识库输入指南；固定运行载荷提交，不修改六模块业务或生产部署。客户端 UI、新机器安装仍待验收，不作为稳定发布。
- ZIP 忽略规则仅放行精确分发包路径；更新精确仓库清单，安装包必须验证嵌套文件，禁止加入客户日志、报告或知识库。

- 中文优先重写项目介绍，新增中文使用指南，说明六子 Skill、报告内容、隔离演示、隐私及跨 Agent 安装的实际限制；不修改业务实现。
- 用户已批准本轮管理文档提交与推送；审核和验证通过后正常快进推送，不创建 tag/Release、不迁移生产。

- Refreshed governance status after the approved initial private main push; recorded the baseline commit and clean-checkout evidence without changing the project version.
- Defined one active private business source, a distinct sanitized Git boundary and deployment copies. Added future handover gates; did not migrate or deploy anything.
- Refreshed affected package inventory entries for the approved document changes; document publication is separately authorized from business changes or production deployment.

- Prepared a local sanitized source candidate based on version 0.5.0, retaining six modules and all existing report contracts. Production is unchanged; no Git release exists.
- Added reusable synthetic acceptance and packaging-safety tools, local-only installation, explicit source fingerprints and long-term maintenance, security, release and rollback instructions.
- Prior validation evidence: 302 private maintenance tests and 188 assertions across four synthetic scenarios. Record fresh candidate results by tested archive hash; do not confuse the private suite with packaged tests.
- Known limitation: long cover descriptions and some headers may clip. The initial commit passed clean local Git checkout validation; full pixel/printing and new-machine dependency validation remain incomplete. No stable Release is declared.
