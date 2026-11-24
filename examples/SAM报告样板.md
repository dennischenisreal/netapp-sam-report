# SAM Report 样板表格

> **全部名称、版本、时间和数字均为人工构造的虚构样板。** 不来自客户数据，也不是报告引擎实际生成的完整 Excel。以下为便于网页阅读的字段节选，不能用于生产决策、固件升级或判断实际检查结果。生成真实报告请使用自己的日志和知识库。

## 1. 控制器与容量

| Cluster Name | Node Name | Model | ONTAP Version | 支持信息 |
|:---:|:---:|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-NODE-A | DEMO-MODEL | N/A | N/A |
| DEMO-CLUSTER | DEMO-NODE-B | DEMO-MODEL | N/A | N/A |

实际报告从日志与知识库匹配设备、版本和支持信息。这里的 N/A 为示例未知值，不能解释为版本为零或没有支持服务。

## 2. Volume List

| Cluster Name | Volume Name | SVM名字 | Aggr Name | Volume Size (TB) | Available Size (TB) | Used Size (TB) | Used Percentage | Logical Used Percent |
|:---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|
| DEMO-CLUSTER | DEMO-DATA | DEMO-SVM | DEMO-AGGR-A | 60.00 | 12.00 | 48.00 | 80.00% | 90.00% |
| DEMO-CLUSTER | DEMO-ARCHIVE | DEMO-SVM | DEMO-AGGR-B | 100.00 | 65.00 | 35.00 | 35.00% | N/A |

| Volume Name | Space Reporting Logical | Space Enforcement Logical | Snapshot Policy | Volume Tiering Policy | Min Cooling Days | Performance Tier (TB) | Capacity Tier (TB) | 待下沉数据 (TB) |
|:---:|:---:|:---:|:---:|:---:|---:|---:|---:|---:|
| DEMO-DATA | true | false | DEMO-POLICY | auto | 31 | 18.00 | 30.00 | 2.00 |
| DEMO-ARCHIVE | false | false | none | N/A | N/A | N/A | N/A | N/A |

为了网页可读性将一张宽表拆成两张；实际字段布局以生成版本为准。逻辑使用率和分层容量取值口径不同，不应将所有列相加；待下沉数据也不是另一个已用层。上述数字只演示显示效果，不代表通用计费或容量计算公式。

## 3. 固件与版本对照

| Cluster Name | Node Name | BMC/SP Model | Firmware Version | latest firmware |
|:---:|:---:|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-NODE-A | DEMO-BMC | DEMO-CURRENT | DEMO-LATEST |

真实 latest firmware 来自使用者准备的本地知识库；不表示此网页给出了实际设备推荐版本。真正缺少固件源日志时，仅跳过对应固件页，并说明缺源情况。

## 4. Network / EMS

| Cluster Name | SVM | Interface | Status |
|:---:|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-SVM | DEMO-LIF-A | up |

| Cluster Name | Event | 说明 |
|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-EVENT | 虚构事件，仅用于展示字段 |

样板不包含真实地址、内部拓扑或故障；实际页包含更多网络配置和事件字段。

## 5. Case

| Cluster Name | Case | 说明 |
|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-CASE | 虚构服务记录 |

实际 Case 按本地知识库匹配。没有匹配记录可以为空，不表示其他模块停止执行。

## 6. 最佳实践规范检查

### Check 13 · 老旧 Snapshot 检查（严格超过 120 天）

固定样板评估时间：2026-11-24 00:00:00。下列虚构快照早于评估时间超过 120 天。

| Cluster Name | Vserver | Volume Name | Snapshot Name | Snapshot Size | Creation Time | 建议 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| DEMO-CLUSTER | DEMO-SVM | DEMO-DATA | DEMO-SNAPSHOT | 256 GiB | 2026-06-01 00:00:00 | 结合保留要求人工评估是否清理 |

样板只示意这一项，实际包含 14 项检查。FlexGroup Convert 为评估建议；不会转换卷、删除快照或修改存储配置。

## 如何生成自己的报告

1. 下载 [Skill 安装包](../distribution/netapp-sam-report-portable.zip) 和 [校验文件](../distribution/SHA256SUMS.txt)。
2. 按 [中文安装与使用指南](../docs/Skill中文安装与使用指南.md)安装，并在独立工作空间准备日志、知识库和私有规则。
3. 先完成 doctor 和样本验证，再生成自己的报告并核对对象覆盖与数值。

本仓库只展示虚构样板。请勿通过 Issue、PR、附件或公开讨论提交真实客户日志、报告、地址或凭据。
