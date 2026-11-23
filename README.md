# NetApp SAM Report — 离线存储巡检报告 Skill

把 NetApp ONTAP AutoSupport（ASUP）日志转成直观的 Excel SAM 报告，方便存储管理员检查容量、配置、固件、告警和最佳实践。无需连接生产存储；本项目不执行存储配置变更，也不会实际转换 FlexGroup。

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

## 给朋友同事使用：当前能做什么？

仓库为 **Private**：同事需要仓库读取权限才能下载或克隆；使用相同邮箱不等于自动获得访问权限。不要通过改成 Public 来绕过权限管理。

目前已验证的是“脱敏源码包 + 本机隔离安装与合成测试”，不是“任意 Agent 下载即用”的通用发行版。维护者已有的生产 Skill 部署继续独立使用，未因本次 GitHub 接入改变。

同事可以先按照下面步骤验证示例运行环境。正式接入自己的 ASUP、知识库和 Agent，仍需配置路径、补齐依赖、确认 Agent 的 Skill 安装规则并完成真实输入验收；不应直接复制维护者私有配置。后续建议单独交付一套可分发安装包和中文接入流程。

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
- 不上传真实 ASUP、报告、知识库、客户身份、网络信息、凭据或私有配置，即使仓库是 Private。
- 原私有维护工作区的 `project/src` 仍是唯一业务修改源；当前 Git 源码是脱敏版本边界，生产副本不是第二开发源。

## 中文导航

- [中文使用指南](docs/quick-start-zh.md)：输入、输出、体验方式和正式接入边界。
- [项目维护规则](AGENTS.md)：审批、测试、提交和数据安全。
- [后续计划](docs/roadmap.md)：共享安装、维护源交接和发布门槛。
- 技术明细（部分英文）：[架构](docs/architecture.md)、[维护命令](docs/maintenance.md)、[验收](docs/acceptance.md)、[安全](docs/security.md)、[回滚](docs/rollback.md)。
