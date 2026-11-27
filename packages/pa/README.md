# NetApp PA Report

本地离线 Performance Archive 性能报告与可维护的 Codex Skill。
当前源码安装候选为 **0.2.0**，保留九个 Sheet；端口用途区分实际 LIF 用途与物理 Role。
代码与 Skill 使用同一份源码；不依赖在线解析服务，不涉及采集工具改造。

## 日常使用

统一仓库为 `dennischenisreal/netapp-sam-report`，PA 位于 `packages/pa`。
可以克隆完整仓库后进入该目录，或者下载独立的
`netapp-pa-report-0.2.0-source.zip`、校验 SHA256 后解压。
ZIP 是完整源码安装包，不是上传单个 SKILL.md 的通用导入包。
SAM 保持独立安装包；统一 Trend 包尚待后续开发。

安装本地 Skill（只建立目录联接，不复制第二套解析代码）：

```powershell
./tools/install_skill.ps1
python "$env:USERPROFILE/.codex/skills/netapp-pa-report/scripts/run.py" --doctor
python "$env:USERPROFILE/.codex/skills/netapp-pa-report/scripts/run.py" --source "D:/Private/PA/Site-A/20261118" --site "Site-A" --label "20261118-AM-r1" --asup "D:/Private/ASUP/Site-A/11" --publish
```

以后可以直接对 Codex 说：“使用 netapp-pa-report，生成这个目录本批 PA report。”
新会话会发现安装的 Skill；当前会话也可以直接读取其 SKILL.md。
每个 Site、每批采样分别运行，`--label` 必须唯一。
ASUP 补充当前 LIF 用途、IFGRP 成员与物理 Role，省略时显示未知，不影响 PA 性能计算。

本机正式输出目录保存在 **不入库** 的 `config/project.local.json` 的
`reports_root` 中。其他机器需按 `config/project.example.json` 建立本地配置，
不能把示例目录当成客户目录使用。`pa_root` 和 `asup_root` 只作为本地查找提示，
执行仍以明确的 `--source` / `--asup` 为准。
首次使用需要按示例建立自己的私有配置；安装器不会替新用户配置客户输出目录。
已经安装同名但来源不同的 Skill 时会停止，不覆盖已有版本。

每次正式输出为 `<reports_root>/<Site>/PA_Report_<Site>_<采样起始月>_<批次>.xlsx`，
另附 `.run.json` / `.verification.json`。同月两次采样不会互相覆盖。
不加 `--publish` 只在 `.local/runs/` 生成验证版。失败任务保留证据，不交付为成功。
旧的初版月度文件保持不变；旧 `publish_performance_reports.ps1` 只保留兼容，
常规报告不要使用其覆盖模式。

## 已确认口径

Node、Aggr、Volume、FlexGroup、Network Port、热点卷分析、
协议与元数据、FabricPool、指标说明，共九页。
默认严格 CPU >70%、卷延迟 >10ms、单向端口利用率 >70%。
僵尸块折算 TiB 使用 `blocks*4096/1024^4`，显示两位小数。
完整定义与限制见 [报告口径](skills/netapp-pa-report/references/report-contract.md)。
Network Port 用途按当前 LIF 角色/服务经 IFGRP 回溯物理口，
明确区分业务、cluster 集群互联及 intercluster 集群间通信；原始物理 Role 另列。
共用口标注共用，但不将物理网卡流量拆成各 LIF 或业务流量。

PA 解析仍属实验解析，SMB/云端延迟尚待 AIQUM 同窗校准。
端口延迟无已验证 counter，保留 N/A。短采样不代表整月均值。
已保留后续趋势分析所需身份、采样窗口、配置、版本和源哈希，
但本入口尚未接入月度、季度、年度趋势或预测。

## 环境与测试

已在 Windows、Python 3.12、openpyxl 3.1.5、现有 bundled Node/Artifact Tool
与 7-Zip 环境验证。`requirements.txt` 仅列 Python 只读 Excel 校验依赖；
Node 的 `@oai/artifact-tool` 来自 Codex 运行环境，不能用任意同名 npm 包替代。
`--doctor` 检查现有依赖，不自动下载。新机器完整部署尚需单独验证。

```powershell
python -B -m unittest discover -v
python -B tools/check_source_privacy.py
```

GitHub Actions 仅运行合成单元测试和源码隐私检查，不含客户数据，
也不声称验证了真实 PA 或私有 Artifact Tool 的导出。
本源码目录内的 workflow 在统一仓库子目录中不会自动启用；
本次没有改动统一仓库的 CI 或分支保护。
本次实际验证与未验证范围见 [验证记录](docs/validation.md)。

## 维护与同步

需求变更时同时更新实现、合成测试、指标口径和版本记录。
具体计划见 [GitHub 同步计划](docs/github-sync-plan.md)。
源码、Skill、文档、合成测试可以同步；即使是私有仓库，
PA/ASUP、客户报告、私有配置、缓存、运行证据都不允许进入仓库。
尚未选择对外开源许可证，不默认授予第三方使用许可。
