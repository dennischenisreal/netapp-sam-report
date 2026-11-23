# Changelog

## Unreleased

- 中文优先重写项目介绍，新增中文使用指南，说明六子 Skill、报告内容、隔离演示、隐私及跨 Agent 安装的实际限制；不修改业务实现。
- 用户已批准本轮管理文档提交与推送；审核和验证通过后正常快进推送，不创建 tag/Release、不迁移生产。

- Refreshed governance status after the approved initial private main push; recorded the baseline commit and clean-checkout evidence without changing the project version.
- Defined one active private business source, a distinct sanitized Git boundary and deployment copies. Added future handover gates; did not migrate or deploy anything.
- Refreshed affected package inventory entries for the approved document changes; document publication is separately authorized from business changes or production deployment.

- Prepared a local sanitized source candidate based on version 0.5.0, retaining six modules and all existing report contracts. Production is unchanged; no Git release exists.
- Added reusable synthetic acceptance and packaging-safety tools, local-only installation, explicit source fingerprints and long-term maintenance, security, release and rollback instructions.
- Prior validation evidence: 302 private maintenance tests and 188 assertions across four synthetic scenarios. Record fresh candidate results by tested archive hash; do not confuse the private suite with packaged tests.
- Known limitation: long cover descriptions and some headers may clip. The initial commit passed clean local Git checkout validation; full pixel/printing and new-machine dependency validation remain incomplete. No stable Release is declared.
