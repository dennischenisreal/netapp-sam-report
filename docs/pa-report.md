# PA Report 安装与维护

## 适用范围

PA 是独立的 `netapp-pa-report` Skill。输入为用户提供的离线 Performance Archive，
可选同 Site ASUP 配置用于端口 Role；不依赖 SAM 安装，也不改动原有采集工具。
九页包括 Node、Aggr、Volume、FlexGroup、Network Port、热点卷分析、
协议与元数据、FabricPool、指标说明。

当前为 **0.1.1 源码安装候选**，不是稳定 Release，也不是已认证的跨 Agent 包。
三类报告的统一 Trend Skill 尚未完成，只有原容量趋势源码与后续接口计划。

## 下载完整源码

两种方式择一：

1. 克隆/下载本仓库，进入 `packages/pa`。
2. 下载 [PA 完整源码 ZIP](../distribution/pa/netapp-pa-report-0.1.1-source.zip)
   和 [SHA256SUMS.txt](../distribution/pa/SHA256SUMS.txt)，在自己的机器核验后解压。

```powershell
Get-FileHash -LiteralPath ./netapp-pa-report-0.1.1-source.zip -Algorithm SHA256
Get-Content -LiteralPath ./SHA256SUMS.txt
Expand-Archive -LiteralPath ./netapp-pa-report-0.1.1-source.zip -DestinationPath ./PA-Source
Set-Location ./PA-Source/netapp-pa-report
```

核对 Get-FileHash 与校验文件中的 SHA256 完全相同再安装。ZIP 含完整源码、
Skill、测试和精确 source-inventory.json；不含客户日志/报告、知识库、Node 或
Python 运行依赖。它不能代替 SAM 的 WorkBuddy Skill 导入 ZIP，也不能仅复制 SKILL.md。

## 注册 Skill 与环境检查

已验收环境为 Windows、Codex 已有 bundled Python/Node/Artifact Tool、
openpyxl 3.1.5 和 7-Zip。安装器仅建立目录联接，不复制依赖、不下载软件；
项目目录必须长期保留，不能安装后移动/删除。

```powershell
./tools/install_skill.ps1
python "$env:USERPROFILE/.codex/skills/netapp-pa-report/scripts/run.py" --doctor
```

已有同名但来源不同的 Skill 时，安装器停止，不覆盖。更新时先检查旧安装目标、
本地修改及配置，明确切换版本后再重装；不要为试用删掉原生产版本。
跨 Agent 自动触发、客户端 UI 导入、全新机器依赖安装均未完整验收。
缺少 Codex Artifact Tool 时 doctor 会报错，不能用任意同名 npm 包替代。

## 本地配置与生成

将 `config/project.example.json` 复制成 `config/project.local.json`，填写自己的
reports_root。所有真实路径和客户归属只留本地，不提交该文件。
先不带 publish 在私有运行目录试用，再做正式输出：

```powershell
python "$env:USERPROFILE/.codex/skills/netapp-pa-report/scripts/run.py" --source "D:/Private/PA/Site-A/20261118" --site "Site-A" --label "20261118-AM-r1" --asup "D:/Private/ASUP/Site-A/11"
```

一条命令处理一个 Site、一批窗口；源 PA 目录递归查找，ASUP 目录只查直接子文件。
ASUP 可省略，端口角色显示未知；每月两次分别使用不同 label。
成功校验后使用新批次 `--publish` 才会写正式目录，拒绝覆盖旧报告。
同一已生成运行目录不重复执行，失败或重试使用新 label；不要将测试输出复制到生产目录。

完整口径见 [报告契约](../packages/pa/skills/netapp-pa-report/references/report-contract.md)。
默认严格 CPU>70%、卷延迟>10ms、单向端口利用率>70%；缺失不补零。
PA 解析是实验解析，SMB/云端延迟仍待同窗 AIQUM 校准。
端口无已验证 latency counter 时为 N/A，PUT 云端写入不等同确认冷数据下沉。

## 更新与验证

PA 使用自己的 VERSION/CHANGELOG；不因 PA 更新修改 SAM 0.5.0 或原分发 ZIP。
一次需求更新包括实现、指标定义、合成测试、对应校验和包哈希。
需 review 的源码通过独立接入分支同步；不自动 merge、tag、Release 或部署。
本机仍保留原维护根，不在镜像中另改一套解析逻辑。

在 PA 完整源码目录执行：

```powershell
python -B -m unittest discover -v
python -B tools/check_source_privacy.py
python -B -m tools.distribution verify "<downloaded-source-zip>"
```

隐私预检需要 Git 检出；只解压 ZIP 的使用者可用包验证与单元测试。
具体同步步骤见 PA `docs/github-sync-plan.md`；Trend 接口见
`docs/trend-data-contract-plan.md`。共享仓库目前没有启用根 CI，
PA 内嵌 workflow 仅作候选定义，不代表远端已运行。

## 本轮验收边界

本机已安装的原 PA Skill 通过 doctor；源码候选包在独立目录解压、安装到
临时 Skills 根、通过 doctor 与 88 项测试（可选私有元数据用例跳过 1 项）。
SAM 独立隔离运行时回归 38 项通过。此前小 Site 九页真实数据验证作为历史证据，
本次没有重新处理客户全部 Site，也没有修改正式输出。
这证明同机干净源码目录的安装路径可用，不等于全新机器或不同 Agent 已验收。
