"""Build V2 side-by-side models from audited PA data and the approved V1 models."""

import argparse
import collections
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re

from pa_capabilities import AUDIT_VERSION
from performance_analysis import Analysis, DEFAULTS, METRIC_VERSION, table, N, P, M, INT, WAIT_COUNTERS
from performance_history import connect, ingest, trend_sheets, export_history, digest
from validate_pa import SPECS


def load_gzip(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def important(cap):
    obj, name = cap["object"], cap["counter"]
    if cap["rule"] or name in WAIT_COUNTERS:
        return True
    if obj in SPECS:
        return name in {s[2] for s in SPECS[obj]} | {s[3] for s in SPECS[obj] if s[3]} | {"wvblk_zombie_blks", "link_speed", "link_duplex"}
    if obj == "processor":
        return name in ("domain_busy", "processor_busy", "processor_elapsed_time")
    if obj.startswith(("nfsv", "smb", "cifs", "object_store_client", "wafl_cloud", "wafl_hya")):
        return True
    return bool(re.search(r"(cloud|tiering|recall|delay_.*wait_time|latency_hist)", name))


def capability_sheet(analysis):
    grouped = {}
    for sup in analysis.supplements:
        m = sup["manifest"]
        for cap in m["capabilities"]:
            if not important(cap):
                continue
            key = (cap["group"], cap["object"], cap["counter"], cap["schema_hash"])
            row = grouped.setdefault(key, {
                "group": cap["group"], "object": cap["object"], "counter": cap["counter"],
                "ctype": cap["ctype"], "size": cap["size"], "base": cap["base_id"],
                "periods": ",".join(str(p / 1000) for p in cap["periods"]),
                "schema": cap["schema_hash"], "description": cap["description"] or "本版本 PA 未提供文字定义",
                "files": set(), "usable_files": set(), "versions": set(),
                "observations": 0, "nonzero": 0, "intervals": 0,
                "capability": cap["capability"], "min": None, "max": None,
                "unit": cap["rule"]["unit"] if cap["rule"] else "待确认",
                "calc": "保留原始值，未确认累计/区间语义" if cap["rule"] and cap["rule"]["mode"] == "raw" else
                        "相邻累计量差分/elapsed，ratio按base差分" if cap["rule"] else "需补充语义/数组解析",
                "known_calculation": bool(cap["rule"] and cap["rule"]["mode"] != "raw"),
                "grain": "Node×SVM协议实例" if cap["object"].startswith("nfsv") else
                         "Node×processor" if cap["object"] == "processor" else
                         "Node×网络接口" if cap["object"] == "nic_common" else
                         "Volume实例" if cap["object"] == "volume" else "PA原始实例，不自动映射到卷",
            })
            row["files"].add(m["node"])
            row["versions"].add(m["version"])
            row["observations"] += cap["observations"]
            row["nonzero"] += cap["nonzero_observations"]
            row["intervals"] += cap["valid_intervals"]
            if cap["valid_intervals"]:
                row["usable_files"].add(m["node"])
            if cap["min"] is not None:
                row["min"] = min(row["min"], cap["min"]) if row["min"] is not None else cap["min"]
                row["max"] = max(row["max"], cap["max"]) if row["max"] is not None else cap["max"]
            # Existing base metrics are computed by V1, not this supplemental collector.
            base_metrics = [s for s in SPECS.get(cap["object"], []) if s[1] == cap["group"] and s[2] == cap["counter"]]
            if base_metrics:
                row["known_calculation"] = True
                matching = [x for x in analysis.nodes[m["node"]]["metrics"] if x["object"] == cap["object"] and x["counter"] == cap["counter"]]
                count = sum(x["valid_intervals"] for x in matching)
                row["intervals"] += count
                if count:
                    row["usable_files"].add(m["node"])
                row["unit"] = (
                    "%" if base_metrics[0][0].endswith("_pct") else
                    "MiB/s" if base_metrics[0][0].endswith("_mib_s") else
                    "ms/block" if "per_block" in base_metrics[0][0] else
                    "ms" if "latency" in base_metrics[0][0] else "ops/s"
                )
                row["calc"] = "沿用V1已验证的差分与base依赖；未改原统计口径"
            if cap["object"] == "volume" and cap["counter"] == "wvblk_zombie_blks":
                row["unit"], row["calc"] = "block (4KiB)", "时点Gauge，不差分；TiB=blocks*4096/1024^4"
                if cap["observations"]:
                    row["usable_files"].add(m["node"])
            row["aggregation"] = (
                "仅在完整共同窗口相加操作/字节；延迟按同层操作数加权；不跨层叠加"
                if cap["object"] in ("volume", "nfsv3") else
                "不与逻辑IFGRP/其他错误类型混加" if cap["object"] == "nic_common" else
                "不直接跨对象相加；保留实例粒度"
            )
    rows = []
    for row in grouped.values():
        row["file_count"], row["usable_count"] = len(row["files"]), len(row["usable_files"])
        row["files"] = ",".join(sorted(row["files"]))
        row["versions"] = "；".join(sorted(row["versions"]))
        row.pop("usable_files")
        row["capability"] = (
            "已存在且可直接计算" if row["usable_count"] else
            "存在定义但缺少有效数据" if row["known_calculation"] else
            "已存在但需要补充解析" if row["size"] not in (4, 8) or row["observations"] else
            "存在定义但缺少有效数据"
        )
        rows.append(row)
    for metric, why in [
        ("nic_common.port_latency", "当前样本不存在端口延迟counter；不能以WAFL延迟替代"),
        ("expected_cluster_node_inventory", "需要同期ASUP/SAM/用户确认节点清单；没有把实收数当期望数"),
        ("port_role_and_ifgrp_members", "需要可验证的端口用途与IFGRP物理成员配置，不能凭端口名称猜测"),
        ("fabricpool_tiering_policy", "需要同期策略/对象存储配置；云端请求不能当tiering迁移量"),
        ("single_io_latency_p95", "部分workload定义含直方图；数组语义、有效桶及卷映射未校准，不输出单IO P95"),
    ]:
        rows.append({"counter": metric, "capability": "需要其他数据源支持" if metric in (
            "expected_cluster_node_inventory", "port_role_and_ifgrp_members", "fabricpool_tiering_policy") else "已存在但需要补充解析" if metric == "single_io_latency_p95" else "当前样本不存在",
                     "description": why, "calc": "不补零、不推断", "unit": "未支持"})
    return table("Counter 能力矩阵", "PA Counter Capability Matrix",
                 "按本Site真实schema归并；定义数、标量观测、非零值和有效差分分别记录。数值均为实验解析，待AIQUM同窗校准。",
                 [
                     ("object", "Object", 30), ("counter", "Counter", 44), ("capability", "能力状态", 34),
                     ("group", "PA Preset", 22), ("grain", "统计粒度", 35),
                     ("unit", "输出单位 / 未确认", 25), ("calc", "处理方式", 62),
                     ("file_count", "含定义节点数", 20, INT), ("usable_count", "可计算节点数", 20, INT),
                     ("observations", "窗口内标量观测数", 25, INT), ("nonzero", "非零标量观测数", 23, INT),
                     ("intervals", "有效差分数", 19, INT), ("ctype", "原始 CType", 19, INT),
                     ("size", "原始字节数", 19, INT), ("base", "原始 Base ID", 20, INT),
                     ("periods", "定义原生周期 秒", 25), ("min", "原始标量最小值", 25, INT),
                     ("max", "原始标量最大值", 25, INT), ("aggregation", "聚合限制", 70),
                     ("description", "PA 原始定义", 115), ("versions", "ONTAP 来源", 75),
                     ("files", "含定义节点", 100), ("schema", "Schema SHA-256", 75),
                 ], sorted(rows, key=lambda r: (r.get("object", ""), r["counter"], r.get("group", ""))), 2)


def parameter_sheet(analysis, batch_id, config_hash):
    rows = [
        {"scope": "版本", "parameter": "metric_version", "value": METRIC_VERSION, "unit": "", "meaning": "新增分析口径；原六张表仍保持performance-report-v4。"},
        {"scope": "版本", "parameter": "Batch ID", "value": batch_id, "meaning": "Site+PA SHA集合确定；同月不同窗口是独立批次。"},
        {"scope": "版本", "parameter": "Config SHA-256", "value": config_hash, "meaning": "不同参数分析保留独立历史run，不静默覆盖。"},
        {"scope": "统计限制", "parameter": "窗口 / P95", "value": "仅PA采样窗口", "meaning": "区间P95按有效区间时长加权nearest rank；不是单IO延迟P95；不能外推整月。"},
        {"scope": "统计限制", "parameter": "相交窗口", "value": "不插值生成采样点", "meaning": "持续时间使用原生区间均值的时间交集；FlexGroup沿用V1的5分钟分配法，存在跨preset相位近似。"},
        {"scope": "参数刷新", "parameter": "参数来源", "value": "performance_config.json", "meaning": "此页是本次配置快照；修改配置文件后重跑。Excel内改值不会重算离线分析。"},
        {"scope": "外部资料", "parameter": "期望节点/端口用途/SAM关联", "value": "配置接口已预留", "meaning": "expected_inventory / port_roles / sam_associations；没有证据时显示未知，不自行猜测。"},
        {"scope": "版本校准", "parameter": "实验解析", "value": "尚未官方数值校准", "meaning": "需要AIQUM同窗口CPU、吞吐、IOPS、WAFL与协议延迟数值核对。"},
    ]
    for cluster in sorted({n["cluster"] for n in analysis.batch}):
        cfg = thresholds_for_cluster(analysis, cluster)
        for key, unit in [
            ("cpu_watch_pct", "%"), ("cpu_high_pct", "%"), ("iops_high", "ops/s"),
            ("throughput_high_gbps", "Gb/s十进制"), ("throughput_high_mib_s", "MiB/s"),
            ("latency_watch_ms", "ms"), ("latency_high_ms", "ms"), ("latency_severe_ms", "ms"),
            ("network_watch", "ratio"), ("network_high", "ratio"), ("minimum_coverage", "ratio"),
            ("top_n", "rank"), ("fg_min_iops", "ops/s"), ("fg_min_mib_s", "MiB/s"),
            ("fg_min_members", "count"), ("fg_skew_ratio", "max/mean"), ("fg_slow_ratio", "ratio"),
            ("fg_min_common_minutes", "分钟"), ("window_hour_tolerance", "小时"),
            ("window_duration_tolerance", "ratio"),
        ]:
            rows.append({"scope": cluster, "parameter": key, "numeric": cfg[key], "unit": unit,
                         "meaning": "客户可配置观察规则，不代表NetApp统一故障阈值。"})
    return table("分析参数", "分析口径与配置快照", "阈值来源为本地配置，保留版本与批次身份以便重算和审计。",
                 [("scope", "范围", 27), ("parameter", "参数 / 定义", 38), ("numeric", "数值", 19, "0.0000"),
                  ("value", "文本值", 75), ("unit", "单位", 25), ("meaning", "口径 / 限制", 115)], rows, 2)


def thresholds_for_cluster(analysis, cluster):
    node = next(n for n in analysis.batch if n["cluster"] == cluster)
    return analysis.cfg(node["node"])


def summary_sheet(analysis):
    rows = []
    for r in analysis.node_results.values():
        if r["cpu_high_minutes"]:
            rows.append({
                "type": "CPU高负载", "cluster": r["cluster"], "resource": r["node"],
                "value": r["cpu_high_minutes"], "unit": "分钟", "status": "观察",
                "evidence": f"最长连续{r['cpu_high_longest']:.1f}分钟；CPU区间P95={r['cpu_p95']:.1%}",
                "next": "核对最忙processor和同窗协议/延迟，不直接判定CPU故障。",
            })
        if r["consistency"].startswith("system 为零"):
            rows.append({"type": "system口径一致性", "cluster": r["cluster"], "resource": r["node"],
                         "status": "需核验", "evidence": r["consistency"], "next": "不可判断节点空闲；建议AIQUM同窗校准。"})
    for r in analysis.fg_risks.values():
        if r["risk"] != "未触发偏斜规则":
            rows.append({
                "type": "FlexGroup", "cluster": r["cluster"], "resource": r["volume"],
                "value": r["skew_minutes"], "unit": "偏斜分钟", "status": r["risk"],
                "evidence": (
                    f"共同{r['valid_minutes']:.1f}分钟；实收{r['members']}成员；"
                    f"IOPS最热={r['hot_iops']}({r['hot_iops_share'] or 0:.1%})；"
                    f"吞吐最热={r['hot_io']}({r['hot_io_share'] or 0:.1%})"
                ),
                "next": "结合每节点成员数归一负载和慢成员检查；不把偏斜直接当故障。",
            })
    for r in analysis.port_results.values():
        if r["rx_high"] or r["tx_high"]:
            rows.append({
                "type": "单向网络压力", "cluster": r["cluster"], "resource": f"{r['node']}/{r['port']}",
                "value": max(r["rx_high"], r["tx_high"]), "unit": "分钟", "status": "观察",
                "evidence": f"RX峰值={r['rx_util_peak']:.1%}；TX峰值={r['tx_util_peak']:.1%}",
                "next": "确认端口用途和IFGRP，核对交换机方向性拥塞。",
            })
    logical_results = [r for r in analysis.vol_results.values() if r.get("style") != "FlexGroup成员"]
    for r in sorted(logical_results, key=lambda r: -(r["co_iops_minutes"] + r["co_io_minutes"]))[:10]:
        if r["co_iops_minutes"] or r["co_io_minutes"]:
            rows.append({
                "type": "高负载且高延迟", "cluster": r["cluster"], "resource": r["svm"] + "/" + r["volume"],
                "value": max(r["co_iops_minutes"], r["co_io_minutes"]), "unit": "重叠分钟", "status": "观察",
                "evidence": f"WAFL均值{r['lat_avg']:.3f}ms；IOPS={r['iops_avg']:.1f}；MiB/s={r['io_avg']:.1f}",
                "next": "查看卷操作结构、协议操作与等待项；不是客户端端到端延迟。",
            })
    rows.append({"type": "数据范围", "status": "PARTIAL", "evidence": "期望节点清单未提供的集群，不宣称日志完整；只有2026-11真实数据。", "next": "后续导入真实批次；提供同期节点清单后启用严格趋势比较。"})
    starts = [n["requested_start_ms"] for n in analysis.batch]
    ends = [n["requested_end_ms"] for n in analysis.batch]
    tz = timezone(timedelta(hours=8))
    window = f"{datetime.fromtimestamp(min(starts) / 1000, tz):%Y-%m-%d %H:%M} 至 {datetime.fromtimestamp(max(ends) / 1000, tz):%Y-%m-%d %H:%M} +08:00"
    return table("性能分析摘要", "本期性能观察",
                 f"{window}；实收 {len(analysis.batch)} 节点。只代表采样窗口，不是整月平均或官方故障结论。",
                 [("type", "观察类型", 25), ("cluster", "集群", 27), ("resource", "资源", 48),
                  ("value", "观测量", 18, N), ("unit", "单位", 19), ("status", "观察状态", 32),
                  ("evidence", "窗口证据", 87), ("next", "建议核验", 85)], rows, 2)


def prepare_site(baseline, output, audit_root, db, config, recompute_samples=False):
    model = json.loads((baseline / "report_model.json").read_text(encoding="utf-8"))
    batch = json.loads((baseline / "data/batch.json").read_text(encoding="utf-8"))
    if len({n["node_uuid"] for n in batch}) != len(batch):
        raise ValueError("Conflicting or duplicate node UUID in batch")
    supplements, intervals = [], []
    for n in batch:
        intervals.extend(load_gzip(baseline / "data" / f"{n['node']}.samples.json.gz"))
        root = audit_root / n["source_sha256"]
        manifest = json.loads((root / "audit.json").read_text(encoding="utf-8"))
        if manifest["audit_version"] != AUDIT_VERSION or manifest["node"] != n["node"] or manifest["source_sha256"] != n["source_sha256"]:
            raise ValueError(f"Audit does not match base source: {n['node']}")
        supplements.append({"manifest": manifest, "intervals": load_gzip(root / "intervals.json.gz"), "raw": load_gzip(root / "raw.json.gz")})
    analysis = Analysis(batch, model, intervals, supplements, config)
    analysis.run()
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "report_model.json"
    batch_id, config_hash, current = ingest(db, analysis, model_path, recompute_samples)
    new_sheets = [summary_sheet(analysis)] + analysis.sheets
    new_sheets.extend(trend_sheets(db, analysis, batch_id, config_hash, current))
    new_sheets.extend([capability_sheet(analysis), parameter_sheet(analysis, batch_id, config_hash)])
    model["analysis_sheets"] = new_sheets
    model["metric_version"] = METRIC_VERSION
    model["batch_id"], model["config_hash"] = batch_id, config_hash
    model["baseline_path"] = str((baseline / model["output_filename"]).resolve())
    model["baseline_model_hash"] = digest({k: model[k] for k in ("sheets", "info", "sources")})
    model["output_filename"] = model["output_filename"].replace(".xlsx", "_V2.xlsx")
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    global_volumes = [
        {**analysis.vol_results[r["resource_id"]], "site": model["customer"]}
        for r in analysis.base_sheets["Volume"]["rows"] if r["style"] != "节点根卷"
    ]
    print("PREPARED_V2", model["customer"], {s["name"]: len(s["rows"]) for s in new_sheets}, flush=True)
    return model_path, global_volumes


def global_hotspots(models, rows, top_n):
    ranked = []
    for dimension, key in [("IOPS", "iops_avg"), ("吞吐", "io_avg"), ("延迟", "lat_avg")]:
        eligible = [r for r in rows if isinstance(r[key], (int, float))]
        for rank, r in enumerate(sorted(eligible, key=lambda r: (-r[key], r["resource_id"]))[:top_n], 1):
            ranked.append({**r, "dimension": dimension, "rank": rank})
    sites = sorted({r["site"] for r in rows})
    scope = "、".join(sites)
    sheet = table("本批热点总榜", "本批逻辑卷热点 Top N",
                  f"范围仅含本批 {len(sites)} 个 Site：{scope}。不代表未处理 Site；不同请求时段只作采样窗口排名，不作同步负载汇总。",
                  [("dimension", "排名维度", 16), ("rank", "排名", 11, INT), ("site", "Site", 23),
                   ("cluster", "集群", 26), ("svm", "SVM", 27), ("volume", "逻辑卷", 42),
                   ("iops_avg", "平均 IOPS", 18, N), ("io_avg", "平均 MiB/s", 18, N),
                   ("lat_avg", "平均 WAFL 延迟 ms", 26, M),
                   ("window_start", "窗口开始 +08:00", 25, "yyyy-mm-dd hh:mm"),
                   ("window_end", "窗口结束 +08:00", 25, "yyyy-mm-dd hh:mm")], ranked, 3)
    for path in models:
        model = json.loads(path.read_text(encoding="utf-8"))
        model["analysis_sheets"].insert(3, sheet)
        path.write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=Path("outputs/pa_reports_20261122"))
    parser.add_argument("--output", type=Path, default=Path("outputs/pa_v2_20261123"))
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--config", type=Path, default=Path("performance_config.json"))
    parser.add_argument("--database", type=Path, default=Path("outputs/history/pa_history_v2.sqlite"))
    parser.add_argument("--site", action="append")
    parser.add_argument("--recompute-samples", action="store_true",
                        help="Explicitly rebuild this batch's versioned native sample blocks; records an audit event.")
    args = parser.parse_args()
    config = {**DEFAULTS, **json.loads(args.config.read_text(encoding="utf-8"))}
    db = connect(args.database)
    paths, volumes = [], []
    for model_path in sorted(args.baseline.glob("*/report_model.json")):
        if args.site and model_path.parent.name not in args.site:
            continue
        path, rows = prepare_site(model_path.parent, args.output / model_path.parent.name,
                                  args.audit or args.output / "audit", db, config, args.recompute_samples)
        paths.append(path)
        volumes.extend(rows)
    global_hotspots(paths, volumes, config["top_n"])
    export_history(db, args.output / "history_export")
    print("HISTORY", {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("files", "batches", "sample_blocks", "snapshots")})
    db.close()


if __name__ == "__main__":
    main()
