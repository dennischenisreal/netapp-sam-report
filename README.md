# NetApp Report Skills — 存储巡检、性能与趋势分析

把 NetApp ONTAP AutoSupport（ASUP）日志转成直观的 Excel SAM 报告，方便存储管理员检查容量、配置、固件、告警和最佳实践。无需连接生产存储；本项目不执行存储配置变更，也不会实际转换 FlexGroup。

本仓库按 **SAM Report、PA Report、Trend Report** 三个独立 Skill 管理。SAM 保留原有源码和分发包；PA 以独立源码安装候选包接入；Trend 后续整合历史容量、配置和性能分析。各自安装、版本和验证，不要求一次更新全部 Skill。仓库地址保留 `netapp-sam-report`，不移动现有 SAM 入口。

## 报告与 Skill 导航

| 报告 | 主要用途 | 输入 | 当前状态 |
|---|---|---|---|
| **SAM Report** | 配置巡检、容量、固件、Case、网络事件和最佳实践 | 离线 ASUP 日志及本地知识库 | 已提供可下载的分发候选包 |
| **PA Report** | Node/Aggr/Volume/FlexGroup/端口性能、热点卷、协议和 FabricPool | 离线 Performance Archive，可选 ASUP 端口配置 | [0.1.1 源码安装候选](docs/pa-report.md)，独立九页报告 |
| **Trend Report** | 目标为历史容量、配置、性能趋势 | 历史 SAM 与 PA 结果 | 已有容量等趋势源码；统一三类趋势 Skill/包尚未交付 |

详细规划见 [多报告接入规范](docs/report-catalog.md)。PA 的完整源码位于 [packages/pa](packages/pa)，下载与安装见 [PA 中文指南](docs/pa-report.md)，示例见 [PA 虚构样板](examples/PA报告样板.md)。下面 SAM 功能和安装说明仍只针对 SAM，不会安装 PA 或 Trend。

## 报告效果预览

以下为**人工构造的虚构样板**，用于说明表格字段，不是客户数据、真实报告截图或本次运行结果。实际 Excel 包含更多字段与工作表。

**Volume List · 容量与配置**

| Cluster Name | Volume Name | SVM名字 | Volume Size (TB) | Available Size (TB) | Used Size (TB) | Used Percentage |
|:---:|:---:|:---:|---:|---:|---:|---:|
| DEMO-CLUSTER | DEMO-DATA | DEMO-SVM | 60.00 | 12.00 | 48.00 | 80.00% |
| DEMO-CLUSTER | DEMO-ARCHIVE | DEMO-SVM | 100.00 | 65.00 | 35.00 | 35.00% |

**最佳实践 · 老旧快照提醒**

| Cluster Name | SVM名字 | Volume Name | Snapshot Name | Snapshot Size | Creation Time |
|:---:|:---:|:---:|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-SVM | DEMO-DATA | DEMO-SNAPSHOT | 256 GiB | 2026-06-01 00:00:00 |

以 2026-11-24 为固定样板评估日期，该快照严格超过 120 天；这里只展示提醒，不执行清理。[查看更多模块样板与字段解释 →](examples/SAM报告样板.md)

## 能生成什么？

- 控制器设备信息、支持生命周期和容量使用情况。
- Aggregate、Volume、FlexGroup、Bucket、分层容量和 export-policy 信息。
- 磁盘、磁盘柜、交换机、BMC/SP 固件信息；知识库可用于版本对照。
- Network/EMS 网络配置与事件、Case 服务记录匹配。
- 14 项最佳实践检查，包括老旧快照、配置规范和 FlexGroup Convert 评估建议。
- 完整来源时生成 **25 个 Sheet**，含 Cover 和 Index；真正缺少对应固件源日志时，只允许跳过相关固件页并说明原因。损坏或解析失败不能冒充“没有日志”。

容量未知值显示 `N/A`，不等于真实的 `0`。老旧快照条件为严格超过 120 天；Convert 仅是建议，不执行变更。具体覆盖项以源码和输入来源为准，不代表对所有 ONTAP 版本的全面合规认证。

## 这是一个 Skill，还是普通脚本？

这是**包含主 Skill、六个子 Skill、配套 Python 脚本和管理工具的项目源码**。Agent 通过各目录的 `SKILL.md` 理解任务，再调用脚本处理日志；GitHub 本身不会运行这些 Skill。

| 模块目录 | 用途 |
|---|---|
| `src/NetApp-SAM-Report` | 主 Skill：组织六模块、整合报告 |
| `src/1-netapp-controller-eos-ma` | 控制器和生命周期 |
| `src/2-netapp-capacity-aggr-vol-fg` | 容量、卷、Aggregate 和 FlexGroup |
| `src/3-jv-case` | Case 匹配 |
| `src/4-netapp-firmware-shelf-io-sp` | 固件信息 |
| `src/5-netapp-network-ems` | 网络与 EMS |
| `src/6-best-practice-check` | 最佳实践规范检查 |

仓库还保留趋势分析 Skill、共享运行依赖及旧适配器。**不能只复制主 Skill 目录就认为全部依赖已经安装。**

## 下载与安装

现已提供可导入的 **Skill 分发候选包**，支持 Codex、WorkBuddy、TRAE 的明确目录安装；不是只下载主 Skill 文件夹。维护者已有的生产 Skill 部署继续独立使用，未因本次 GitHub 接入改变。

### 安装步骤

1. 在本仓库打开 `distribution/netapp-sam-report-portable.zip`，点击下载原始文件；同时下载该目录的 `SHA256SUMS.txt`，核验哈希。GitHub 的“Download ZIP”下载的是整个源码仓库，不能代替这个 Skill 安装包。
2. 阅读 [Skill 中文安装与使用指南](docs/Skill中文安装与使用指南.md)。Codex / TRAE 解压后使用明确目录安装器；WorkBuddy 可按指南上传原始 Skill ZIP。
3. 按 [知识库输入约定](docs/知识库输入约定.md)准备自己的输入，在新的独立工作空间执行 setup、doctor，再让 Agent 生成报告；先用非生产样本验证。

包内包含可阅读的安装器、Python 入口与六模块运行源码（`assets/runtime.zip`），不含客户日志、报告或知识库。包固定基于已审核提交 `8f611405938c656e5045993b223ab1e67504a807`，不会因仓库文档更新而自动变化。Python 依赖及 7-Zip 需要另外准备。实际三个客户端 UI 导入、自动触发及新机器依赖安装尚未验证；详见 [分发验收说明](docs/skill-distribution-validation.md)。这不是稳定 Release。

## 快速体验：不使用真实客户数据

以下命令在仓库根目录运行。已验证环境为 Windows、CPython 3.14；依赖版本见 `requirements.in` 和 `requirements.lock.txt`。其他操作系统、Agent 及新机器依赖安装尚未完整验收，请先使用独立 Python 环境，不影响现有环境。

首次使用一个全新下载/克隆目录时：

```text
python -B tools/candidate_runtime.py install
python -B tools/candidate_runtime.py doctor
python -B tools/candidate_runtime.py test
python -B tools/package_candidate.py --output .local/demo-package
python -B tools/validate_synthetic_report.py --archive .local/demo-package/netapp-sam-report-shared-candidate.zip --output .local/demo-complete --scenario complete
```

`install` 只安装到本项目的 `.local`，不注册到 Agent、不覆盖已部署 Skill；已有本地配置时拒绝覆盖，请勿重复安装。合成验收使用虚构日志和知识库，经实际六模块生成示例报告；报告路径及断言结果见指定输出目录的 `summary.json`。示例不能用于客户决策。

`sam.py report/trend` 的替代配置保护门槛仍保留，不是目前已验证的通用安装入口。不要通过删除保护或修改业务源码来“安装成功”。

## 安全与维护状态

- 项目基于 `0.5.0`，首次 main 提交为 `54c9acad9752ced4aeb99e3548099bec6455a5be`，不是稳定 tag 或 Release。
- 首次提交的干净本地检出通过 38 项测试及四场景 188 项合成断言；本轮文档变更需另记验证证据。
- 尚未配置 CI/分支保护；全部 Sheet 的像素/打印验收和新机器安装仍待完成，部分长表头可能截断。
- 仓库公开不等于输入数据公开：真实 ASUP、报告、知识库、客户身份、网络信息、凭据和私有配置必须保留在使用者本地，禁止提交。
- 原私有维护工作区的 `project/src` 仍是唯一业务修改源；当前 Git 源码是脱敏版本边界，生产副本不是第二开发源。

## 中文导航

- [SAM 报告样板](examples/SAM报告样板.md)：纯虚构数据，查看各模块典型字段。
- [多报告接入规范](docs/report-catalog.md)：SAM、PA、Trend 的边界与后续管理方式。

- [中文使用指南](docs/quick-start-zh.md)：输入、输出、体验方式和正式接入边界。
- [项目维护规则](AGENTS.md)：审批、测试、提交和数据安全。
- [后续计划](docs/roadmap.md)：共享安装、维护源交接和发布门槛。
- 技术明细（部分英文）：[架构](docs/architecture.md)、[维护命令](docs/maintenance.md)、[验收](docs/acceptance.md)、[安全](docs/security.md)、[回滚](docs/rollback.md)。
