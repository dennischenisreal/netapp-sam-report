# Approved Report Contract

## Scope And Thresholds

The 0.1.0 initial release preserves the user's reviewed nine-sheet resource-first
report. Only actual PA sampling windows are measured, never a continuous monthly
average. Default comparisons are strictly CPU >70%, volume latency >10 ms,
and each RX/TX direction >70%. Equality does not count.
The driver currently rejects changed defaults and per-cluster/platform overrides:
custom thresholds require coordinated analysis/header/verifier changes and review.

| Sheet | Content |
| --- | --- |
| Node | Throughput, IOPS, CPU, latency; time-weighted CPU interval P95, high-load and longest high-load minutes. No busiest processor columns. |
| Aggr | Native aggregate throughput, IOPS, latency, disk utilization/latency, read/write mix. Do not replace missing aggregate IO with summed volume IO. |
| Volume | Logical FlexVol and FlexGroup parents only; IO, IOPS, latency, operation mix, high-latency duration, zombie gauges. |
| FlexGroup | Constituents, relative hotspot marks, parent load distribution and normalized node distribution on one sheet. No SAM inventory/member completeness comparison. |
| Network Port | IO, individual RX/TX utilization and peaks, high-utilization duration, observed error increments, mapped ASUP LIF purpose and separate raw physical Role. Hide only all-observed-down links. |
| 热点卷分析 | Site-wide Top 10 each by average latency, throughput and IOPS. Exclude node root volumes and constituent duplicates; not all SVM root volumes. |
| 协议与元数据 | Separate NFSv3/v4/v4.1 and CIFS SMB1/SMB2+3 by actual activity. CIFS-only Sites are valid. Other volume operations are not automatically metadata. |
| FabricPool | Matched object-store GET/PUT throughput, IOPS and latency. PUT is cloud writing, not proof of cold-data-only tiering or physical free-space gain. |
| 指标说明 | Units, scope, missing data and interpretation limitations. |

## Units And Timing

- IO means throughput in MiB/s, not operation count. Link speed is decimal Gb/s.
- CPU P95 and latency P95 describe time-weighted native interval means, not single-request P95.
- Latency is cumulative latency delta / matching operation delta, converted to ms as supported by metadata. Volume WAFL latency is not client end-to-end latency.
- Node CPU is system cpu_busy / cpu_elapsed_time, not mean processor utilization.
- Typical native periods: CPU 5 seconds, system 1 minute, volume/network 5 minutes,
  aggregate bytes/latency 1 hour. Do not manufacture higher resolution. Short
  windows can legitimately lack two valid hourly points and show Aggr IO N/A.
- Cross-preset/member alignment uses fully covered common 5-minute bins.
  Intersect valid intervals; weight latency by operation counts, not arithmetic means.
- Read/write operation ratios and byte ratios use their respective common
  windows and denominator read+write, excluding Other. Zero denominator is unavailable.
- `wvblk_zombie_blks` is a snapshot gauge, not a difference. Capacity is
  `blocks * 4096 / 1024^4` TiB, displayed with two decimals. These two columns
  immediately precede effective duration. Parent values require all received members'
  valid snapshots at a common as-of time; missing does not become zero.
- Zero is retained where explicitly measured. No-operation latency, missing counter,
  unknown speed or invalid interval remains N/A. A zero system IOPS counter does
  not establish an idle node if volume/backend counters are active.
- Network utilization follows `(RX+TX)*8/speed` plus separate RX/TX values.
  Full-duplex combined utilization may exceed 100%. Unknown/changing speed or
  non-full-duplex bins are not used. Port latency has no verified counter: N/A.

## FlexGroup And Port Identity

FlexGroup membership uses cluster + SVM + nonzero flexgroup_msid, not just a name suffix.
Member count means actually received members, not proof of full inventory.

Relative hotspot comparison requires >=2 received members and >=30 common minutes.
Mark if mean IOPS or throughput is >2 times the other members' mean, with independent
parent load guards >=1000 IOPS or >=10 MiB/s. Count the union of qualifying 5-minute
bins and its longest contiguous run. Zero peers yield N/A ratio but can identify the
sole active member. Low load does not trigger a hotspot. Coverage below 90% does not
establish sustained imbalance over the entire requested window.

Starting in 0.2.0, the main port-purpose column is distinct from physical Role.
Match node name + serial + cluster UUID, choose the nearest ASUP snapshot to
the PA window end, and display its actual date. Preserve `network-ports.xml:role`
in a separate raw physical Role column; a physical `data` Role does not exclude
an intercluster LIF.

Classify `network-interface.xml` LIF roles and explicit services into business
data, cluster, intercluster and management categories. Match only current
`curr_node`/`curr_port`, not home/failover targets or LIF/IP naming guesses.
Expand IFGRP endpoints through `ifgrps.xml` (`ifgrp.xml` alias supported) members.
An explicitly recorded VLAN may trace its named base and then IFGRP members;
an unrecorded hyphenated interface name alone is not topology evidence.
Node-scoped membership is authoritative; normalize the active `*` suffix only
as a fallback to `network-ports.xml:ifgrp`. Direct LIF evidence may identify an
interface omitted from the port table. Raw cluster Role remains cluster.

Show all actually mapped categories and mark shared ports; do not concatenate
NFS/CIFS protocol names as roles. Generic data Role without LIF evidence is
purpose-unconfirmed, not proof of business traffic. Missing configuration is unknown.
Never split physical PA traffic by LIF/category, sum physical and logical
interfaces, fabricate PA rows from ASUP inventory, or call the snapshot historical
traffic attribution. ASUP enrichment reads direct `.7z` children of the specified directory.

## Calibration And Future Comparisons

PA decoding is experimental, not equivalent to NetApp official backend certification.
SMB/cloud latency units still need AIQUM same-window calibration.
No periodic report requires an online service or official backend upload.

Preserve archive SHA, native windows, resource IDs, model/metric/software versions,
configuration and run evidence. Match UUID identities, not names alone; migrations
and recreation need corroborating ASUP UUID mappings before joining history.
Keep twice-monthly samples as separate batches. Monthly/quarterly/yearly reports and
forecasting remain a later feature; do not call two brief windows full-month coverage.
