# GitHub 同步与维护

## 已选方案：一个仓库，三个独立 Skill

2026-11-24 检查现有仓库后，采用 `dennischenisreal/netapp-sam-report`，
其展示名称已为 NetApp Report Skills。保留仓库地址、SAM 源码、SAM 安装包，
新增 `packages/pa`。不再新建 PA 仓库，不改仓库可见性。
源码使用 packages 而非 reports，避免与现有客户报告输出目录的忽略规则混淆。

| Skill | 范围 | 版本与安装 |
| --- | --- | --- |
| SAM Report | ASUP 配置、容量、巡检 | 保持现有 0.5.0 与原分发候选包 |
| PA Report | 离线 PA 性能 | 独立 VERSION、测试、源码安装 ZIP；当前为 0.2.0 |
| Trend Report | 历史容量、配置、性能比较 | 后续独立整合，尚未交付统一三类趋势包 |

共仓库便于统一下载入口、需求记录和跨报告兼容性；分目录、分版本、分包
避免一次 PA 更新要求重装 SAM。不为了共享而提前重写现有解析器。
只有当权限、维护团队或独立发布需求明显分开时，再考虑拆仓库。

## 本地与远端的维护边界

当前本地 PA 项目仍是本机唯一业务编辑源，已安装 Skill 通过目录联接指向它。
共享仓库的 `packages/pa` 是审核后的同步边界，不在两处分别改业务。
其他用户可克隆仓库，在自己的检出目录维护自己的本地版本。
以后若要将本机维护源正式迁入统一仓库，需另做漂移核对、配置备份和入口切换。

日常报告不自动 git pull、不提交、不上传。需求更新才进入代码同步流程。
客户 PA/ASUP、报告、历史指标、缓存和本机路径始终留在本地，
即使仓库私有也不能上传。现有仓库公开，本次不替用户选择开源许可证。

## 修改和同步步骤

1. 在本地 PA 维护源修改，补合成测试；更新 VERSION、CHANGELOG 和口径。
2. 跑单元测试、隐私预检、安装入口 `--doctor`；涉及指标时小 Site 私有验证，
   涉及布局时看预览。源码改动不自动覆盖已交付报告。
3. 在统一仓库的独立检出中 fetch，检查 main 和本地状态，
   从明确的基线提交建立 `codex/pa-<change>` 分支。
4. 先预览，再导出经过隐私预检的精确源码：

```powershell
python -B -m tools.distribution stage-suite --suite "D:/Source/ReportSkills" --expected-base "<full-reviewed-commit>"
python -B -m tools.distribution stage-suite --suite "D:/Source/ReportSkills" --expected-base "<full-reviewed-commit>" --apply
python -B -m tools.distribution build --output ".local/package-unique"
python -B -m tools.distribution verify ".local/package-unique/netapp-pa-report-0.2.0-source.zip"
```

导出工具不执行远端写入，不删除旧文件。目标有独立修改、未登记文件、
过时基线、目录联接或需要删除的旧源码时停止。
先核对差异再重试，不能强制覆盖 GitHub 上他人更新。
以上版本号只是本次示例，新版本使用各自 VERSION 对应文件名。

5. 将验证过的 ZIP 和校验文件复制到统一仓库 `distribution/pa/`，
   只放行该版本的精确 ZIP 路径；更新 PA 文档、目录状态和统一精确清单。
   SAM 原分发包不得随 PA 更新被重打包或替换。
6. 在同步边界重跑 PA 测试，并做 SAM 安装/测试回归。审查准确文件清单、
   暂存内容、候选 ZIP 每个成员和将推送的提交历史。
7. 按本次授权正常提交和推送接入分支。合并 main、tag、Release、
   仓库设置和生产部署仍单独确认。不 force-push。

修复且不改口径为 patch；兼容的新增指标为 minor；
不兼容的指标口径/结构变化应升级 major 或明确的新 schema。
指标含义、窗口、单位、身份和聚合变化另记 metric/model 兼容性。
PA CI 定义随独立源码保存；本次不擅自启用根仓库 CI 或修改分支保护。
首次同步不等同于稳定 Release；跨 Agent、全新机器尚需独立验收。

## 趋势扩展

按 [Trend 数据接口计划](trend-data-contract-plan.md) 逐步建立版本化接口。
先积累同口径独立采样批次，不把两个短采样当成整月连续数据。
不把名称当唯一标识，不把缺失当零，不把口径变化串成业务趋势。
现有 performance_history.py 是早期基础，不是已验收的统一 Trend 入口。
