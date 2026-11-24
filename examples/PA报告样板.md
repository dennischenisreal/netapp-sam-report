# PA Report 虚构样板

以下数据全部人工构造，仅说明字段含义，不是客户数据、真实 PA 解析结果或
本次性能验收结论。真实报告共九个 Sheet，按实际采样窗口统计，不是整月均值。

## Node

| 集群 | 节点 | IO MiB/s | IOPS | CPU | 平均延迟 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| DEMO-CLUSTER | DEMO-01 | 320.00 | 18000 | 42% | 1.20 |
| DEMO-CLUSTER | DEMO-02 | 280.00 | 16000 | 38% | 0.90 |

真实表另有 CPU 区间 P95、高负载分钟及最长持续时间；默认 CPU 严格 >70%。

## Volume 与 FlexGroup

| 逻辑卷 | 类型 | IO MiB/s | IOPS | 延迟 ms | Read 操作比 | Write 操作比 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| DEMO-DATA | FlexVol | 100.00 | 8000 | 1.10 | 80% | 20% |
| DEMO-GROUP | FlexGroup | 200.00 | 16000 | 1.80 | 60% | 40% |

Volume 展示逻辑大卷，FlexGroup 页另列真实成员和负载分布，不能把两层重复相加。
热点子卷比较同窗已收成员，不仅按卷名后缀猜关系。
Read/Write 操作比以读+写操作为分母，不含 Other；字节比例另算。

## Network Port

| 节点 | 端口 | Role | IO MiB/s | RX 利用率 | TX 利用率 | 端口延迟 ms |
| --- | --- | --- | ---: | ---: | ---: | --- |
| DEMO-01 | e1a | data（数据） | 100.00 | 4% | 4% | N/A |

Role 直接来自同节点 ASUP network-ports 配置，不拼接 NFS/CIFS 等 LIF 服务。
端口没有验证过的延迟 counter 时保留 N/A，不能借用卷延迟。
只有全程观测 down 的接口隐藏；缺失和真实零不混淆。

FabricPool GET/PUT、协议与热点榜以真实 counter 和窗口为准。
PUT 不能单独证明冷数据下沉；短采样不能用来推断整月连续负载。
