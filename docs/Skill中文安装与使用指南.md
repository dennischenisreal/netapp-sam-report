# NetApp SAM Report 中文安装与使用指南

适用对象：希望在 **Codex、WorkBuddy、TRAE** 中，把自己的 NetApp ONTAP ASUP 离线日志生成 Excel 巡检报告的同事。`workbudy` 在本指南中按腾讯 **WorkBuddy** 理解。

## 一、你拿到的是什么？

这是自包含 Skill 包，不是只有提示词的空壳。内部封装主报告入口、六个原子模块、共享文件、旧适配器、合成测试及锁定依赖清单。只需让 Agent 发现一个入口 `netapp-sam-report-portable`，程序会调用包内六个模块，不依赖作者电脑里的旧 Skill。

完整来源生成 25 个 Sheet，包括控制器、节点容量、Volume List、Aggregate、FlexGroup、Bucket、固件、Network/EMS、Case、14 项最佳实践、export-policy 等。只允许对真正缺失来源的对应固件页进行跳过；损坏和解析失败不视为缺日志。未知值保留 N/A，真实零不混淆；快照严格超过120天才算老旧，Convert 只建议、不执行转换。

包内业务源来自清单所记的已审核 Git 提交，版本仍为 0.5.0；本包是本地分发候选，不是未经验证就宣布的稳定 Release。三种 Agent 共用相同 Python 执行入口，差异仅在安装/发现 Skill 的方式。

## 二、先安装 Skill，不触碰已有环境

收到 `netapp-sam-report-portable.zip` 和 `SHA256SUMS.txt`。先核对 SHA256，再解压到一个新目录。PowerShell 核验命令：

```powershell
Get-FileHash "D:\Downloads\netapp-sam-report-portable.zip" -Algorithm SHA256
```

与同渠道校验文件匹配只证明传输一致；可信来源仍需自行确认，这不是数字签名。包中不含 Python、pip 下载依赖、真实 ASUP、客户知识库、报告、账户和 Token。

### Codex

将解压后的**整个目录**安装到你当前版本实际使用的 skills 根目录。较新文档采用项目或用户的 `.agents/skills`，部分已有安装采用 `.codex/skills`。不要同时重复安装；以客户端当前说明和可发现的 Skill 列表为准。

可以让 Agent 执行本包 `install.py`。示例为明确选择的项目级目录，不会扫描用户电脑或替你猜目录：

```powershell
python -B "D:\Downloads\SAM-Skill\install.py" --agent codex --skills-dir "D:\SAM-Demo\.agents\skills"
```

默认只预览；核对目标后加 `--apply` 才复制。已存在同名目录则拒绝覆盖，原先 NetApp-SAM-Report 及六个生产 Skill 不会被替换。重新开启会话或按客户端方式刷新，然后确认能找到 `netapp-sam-report-portable`。

### WorkBuddy

官方入口为 **专家·技能·连接器 → 技能 → 添加技能 → 上传技能**。优先选择原始 `netapp-sam-report-portable.zip`，不要只上传 `SKILL.md` 或内部 `runtime.zip`。上传后检查资源文件是否完整保留。

若你的版本对 ZIP 结构或体积有限制，用当前客户端提供的本地目录导入方式；也可确认实际 skills 根目录后，运行安装器 `--agent workbuddy --skills-dir "你确认的绝对目录"`，先预览，再 `--apply`。本项目不臆测 WorkBuddy 的全局路径，也不修改账号配置。

### TRAE

按当前版本的技能管理入口导入解压后的完整目录。中文版本官方文档列有用户目录 `.trae-cn/skills`；国际版及项目级路径可能不同，以你自己的版本为准。

明确本地路径后，可执行：

```powershell
python -B "D:\Downloads\SAM-Skill\install.py" --agent trae --skills-dir "D:\SAM-Demo\.trae\skills"
```

确认后加 `--apply`。不要把示例路径理解成所有 TRAE 版本唯一有效的路径。无论哪种客户端，能看到技能名称只表示发现成功，还需完成后面的运行配置与验收。

## 三、准备 Python 与自己的私有数据

已验证的 Python 基线是 Windows CPython 3.14。建议每位同事新建独立虚拟环境；不要在全局或已有生产环境中强行升级依赖。首次下载第三方依赖需要联网，不会由 Skill 自动安装：

```powershell
py -3.14 -m venv "D:\SAM-Private\venv"
& "D:\SAM-Private\venv\Scripts\python.exe" -m pip install -r "D:\Downloads\SAM-Skill\requirements.lock.txt"
```

锁文件记录测试版本，不是离线 wheels 包，也不是跨平台认证。之后所有命令使用同一个虚拟环境 Python。macOS/Linux、新机器依赖全新安装与三个客户端的实际 UI 导入未在本机验证，不宣称已通过。

此外必须安装 **7-Zip 命令行程序**。包内旧适配器需要 `7z.exe`，仅安装 Python 的 py7zr 不足以代替。setup 会查 PATH 或 Windows 标准安装位置；找不到时明确报错，也可加 `--seven-zip "D:\Tools\7-Zip\7z.exe"` 指定自己的绝对路径。本包不携带或自动安装7-Zip。

把自己的文件分开存放，以下全是虚构示例：

```text
D:\SAM-Private\ASUP\demo-site\11月\    自己的 ASUP 归档
D:\SAM-Private\Knowledge\               自己的知识库
D:\SAM-Private\History\                 历史 SAM 报告（可选）
D:\SAM-Private\Reports\                 本次及后续 Excel 输出
D:\SAM-Private\Config\profiles.json      自己的私有规则
D:\SAM-Private\Runtime-01\               尚不存在，由 setup 新建
```

原日志、知识库、历史与输出不可互相重叠，不要放在安装包目录、Skill 目录或 Git 仓库内。日志根用于限定允许读取的客户日志子目录；运行时仍明确选择某客户某月，不自动猜最近月份或客户别名。

### 客户规则不能照搬作者的

先把 `assets/customer-profiles.example.json` 复制到自己的私有 `profiles.json` 并审核修改。模板只有虚构示例，不适用于真实客户；setup 强制显式提供规则文件，不偷偷使用作者的客户规则。

`naming` 控制客户显示名称；`best_practice.special_customer_match` 和 `check14_clusters` 指定原先有特定客户适用范围的检查，`messages` 是相应文案。未匹配的专属检查会按原逻辑跳过，**不是全部客户都已通过该检查**。不要为了消除跳过而随意把客户范围扩大；需要 Agent 协助时先解释每个条件再填写。

知识库文件格式与子目录约定见[知识库输入约定](知识库输入约定.md)，细节以包内现有解析源码为准。setup 后源码位于 `Runtime-01/runtime/src`；不能仅建一个空 Knowledge 文件夹就认为固件与 Case 匹配完整。Case 可以没有匹配数据，但知识库/日志缺失与匹配不到要保留诊断。历史不足时不应制造趋势或 Convert 建议。

## 四、首次配置：可由 Agent 帮你操作

不需要每次手敲脚本。把下列信息告诉 Agent，让它用自己的工具执行。包内 Skill 说明要求它先询问缺失的路径、核对环境，再运行。

示例命令（`Skill目录` 用实际安装位置替换）：

```powershell
& "D:\SAM-Private\venv\Scripts\python.exe" -B "Skill目录\scripts\sam_skill.py" setup --workspace "D:\SAM-Private\Runtime-01" --logs "D:\SAM-Private\ASUP" --knowledge "D:\SAM-Private\Knowledge" --reports "D:\SAM-Private\Reports" --profiles "D:\SAM-Private\Config\profiles.json" --history "D:\SAM-Private\History"
```

没有历史时省略 `--history`；多历史根可重复提供。workspace 必须是新目录，不覆盖已配置目录。setup 解出包内审核源，在新工作空间建立六模块运行镜像，检查依赖与指纹；不写入你已有的生产 Skill。失败会保留现场，不强行清理，请排错后换新目录重试。

更换路径、规则、Python 或包版本，请审核后新建 Runtime-02；配置哈希变化会拒绝静默运行。此版本不自动改旧配置、升级或卸载。旧 Runtime 和报告原样保留，便于回滚。

## 五、生成报告

```powershell
& "D:\SAM-Private\venv\Scripts\python.exe" -B "Skill目录\scripts\sam_skill.py" doctor --workspace "D:\SAM-Private\Runtime-01"
& "D:\SAM-Private\venv\Scripts\python.exe" -B "Skill目录\scripts\sam_skill.py" report --workspace "D:\SAM-Private\Runtime-01" --input "D:\SAM-Private\ASUP\demo-site\11月"
```

默认执行原有 default 阈值。只有用户明确要求自定义，才加 `--custom-thresholds`，使用原有终端交互方式；不是三种客户端原生弹框。终端无法输入时，应由 Agent 先说明，而不是假装自定义已经生效。

给 Agent 的中文示例：

> 使用 netapp-sam-report-portable，工作空间是 D:\SAM-Private\Runtime-01，读取 D:\SAM-Private\ASUP\demo-site\11月，默认阈值，生成 SAM report。请给我最终文件路径以及缺失来源、needs_review 和失败检查的说明。不要修改代码或上传日志。

报告路径、运行模块结果及覆盖证据由脚本返回。每次生成保留新文件及运行记录；不要把“生成成功”当作“客户全部规范通过”。合成自测可调用 `sam_skill.py test --workspace ...`，但真实客户的报告仍需核对日志对象覆盖和数值。

## 六、安全、更新与回滚

- 本包不连接生产存储、不上传 ASUP、不自动 commit/push、不创建计划任务；Agent 是否把对话或文件摘要发送到模型由其客户端、连接器和公司策略决定。不能因为脚本本地运行就承诺模型侧完全离线。
- 私有客户规则和运行证据只留在自己工作空间。任何包升级都先核验来源和哈希，不覆盖旧 Skill/Runtime。保留旧环境重新指向旧 Skill 可用于人工回退；不自动删除报告和配置。
- 安装器没有自动卸载功能。卸载前先确认实际目录，保留运行工作空间及报告，仅手工移除自己安装的入口目录，不删除旧生产 Skill。
- 使用本包不代表取得 NetApp 或其他第三方软件的再分发许可；Python 依赖按各自许可证下载。分享仍遵守组织数据政策和仓库访问控制。

## 七、兼容性证据与边界

本轮以相同包对 Codex/WorkBuddy/TRAE 三种安装目标做隔离目录验收，并运行合成 ASUP 端到端检查。详细执行结果与限制见外部验收收据。本指南不提前断言未经执行的测试通过；客户端真实 UI 导入、Agent 自动触发、macOS/Linux、新机器依赖安装都需后续验证。

官方安装约定参考（核对日期2026-11-24，客户端版本变化后请重新确认）：

- Codex：https://developers.openai.com/api/reference/cli （技能安装目录示例）
- WorkBuddy：https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market （本地技能包上传入口）
- TRAE：https://docs.trae.cn/ide_skills （技能目录与发现约定）
