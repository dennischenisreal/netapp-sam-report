"""Consolidated resource-first workbook model, retaining native source caches."""

import argparse
import collections
import copy
import gzip
import json
from pathlib import Path
import re

from performance_analysis import Analysis, DEFAULTS, N, P, M, INT, safe_mean, span, longest, table
from prepare_performance_report import bin_intervals, field


def load(path):
    if str(path).endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def split(read, write):
    """Same five-minute bins; the denominator excludes Other operations."""
    left, right = bin_intervals(read), bin_intervals(write)
    common = set(left) & set(right)
    a = sum(left[t]["numerator"] for t in common)
    b = sum(right[t]["numerator"] for t in common)
    return (a / (a + b) if a + b else None,
            b / (a + b) if a + b else None, len(common) * 5)


def append_fields(sheet, columns, before):
    existing = {f["key"] for f in sheet["fields"]}
    additions = [field(*c) for c in columns if c[0] not in existing]
    index = next(i for i, f in enumerate(sheet["fields"]) if f["key"] == before)
    sheet["fields"][index:index] = additions


def resource_mix(analysis, base, aggregate=False):
    src = ({m: analysis.get(base["node"], "aggregate", base["aggr"], m)
            for m in ("read_iops", "write_iops", "read_mib_s", "write_mib_s")}
           if aggregate else analysis.volume_series(base))
    result = {}
    for suffix, left, right in (
        ("ops", "read_iops", "write_iops"), ("bytes", "read_mib_s", "write_mib_s")
    ):
        a, b, minutes = split(src[left], src[right])
        result.update({f"read_{suffix}_ratio": a, f"write_{suffix}_ratio": b,
                       f"rw_{suffix}_minutes": minutes})
    return result


MIX_COLUMNS = [
    ("read_ops_ratio", "读操作占比\nRead/(Read+Write)", 24, P),
    ("write_ops_ratio", "写操作占比\nWrite/(Read+Write)", 24, P),
    ("read_bytes_ratio", "读数据量占比", 19, P),
    ("write_bytes_ratio", "写数据量占比", 19, P),
    ("rw_ops_minutes", "读写操作共同\n分钟", 19, N),
    ("rw_bytes_minutes", "读写数据共同\n分钟", 19, N),
]

def member_hotspots(members, bins, common, config):
    """Compare each constituent with its peers on the same observed 5m bins."""
    result = {}
    ids = [m["resource_id"] for m in members]
    minutes = len(common) * 5
    loads = {
        t: {
            rid: {
                "iops": bins[rid]["iops"][t]["numerator"] / 300,
                "io": (bins[rid]["read_mib_s"][t]["numerator"] +
                       bins[rid]["write_mib_s"][t]["numerator"]) / 300 / 2**20,
            } for rid in ids
        } for t in common
    }
    means = {rid: {metric: sum(v[rid][metric] for v in loads.values()) / len(common)
                   if common else None for metric in ("iops", "io")} for rid in ids}
    totals = {metric: sum(v[metric] for v in means.values()) if common else None
              for metric in ("iops", "io")}
    enough = len(ids) >= config["fg_min_members"] and minutes >= config["fg_min_common_minutes"]
    limits = {"iops": config["fg_min_iops"], "io": config["fg_min_mib_s"]}
    all_high = []
    for rid in ids:
        ratios, average_hot, high_times, zero_peers = {}, [], [], []
        for metric in ("iops", "io"):
            own = means[rid][metric]
            peer_sum = sum(v[metric] for other, v in means.items() if other != rid) if common else None
            peer = peer_sum / (len(ids) - 1) if len(ids) > 1 and peer_sum is not None else None
            ratio = own / peer if peer else None
            ratios["peer_" + metric + "_ratio"] = ratio
            if enough and totals[metric] >= limits[metric] and own > config["fg_skew_ratio"] * peer:
                average_hot.append(metric)
                if not peer:
                    zero_peers.append(metric)
        if enough:
            for t, values in loads.items():
                for metric in ("iops", "io"):
                    total = sum(v[metric] for v in values.values())
                    own = values[rid][metric]
                    peers = sum(v[metric] for other, v in values.items() if other != rid) / (len(ids) - 1)
                    if total >= limits[metric] and own > config["fg_skew_ratio"] * peers:
                        high_times.append({"a": t, "b": t + 300})
                        break
        label = ("IOPS / 吞吐热点" if len(average_hot) == 2 else
                 "IOPS热点" if average_hot == ["iops"] else
                 "吞吐热点" if average_hot == ["io"] else
                 "区间相对热点" if high_times else
                 "低负载，不判热点" if enough and all(totals[m] < limits[m] for m in limits) else
                 "未见明显热点" if enough else
                 "成员不足" if len(ids) < config["fg_min_members"] else "共同窗口不足")
        result[rid] = {
            "hotspot": label, **ratios,
            "hot_minutes": len(high_times) * 5 if enough else None,
            "hot_longest": longest(high_times) / 60 if enough else None,
            "hotspot_marked": bool(average_hot or high_times),
            "hotspot_zero_peers": bool(zero_peers),
        }
        all_high.extend(high_times)
    union = [{"a": t, "b": t + 300} for t in sorted({r["a"] for r in all_high})]
    return result, {
        "risk": "采样窗口相对不均" if union or any(r["hotspot_marked"] for r in result.values()) else
                "条件不足，不判断偏斜" if not enough else
                "低负载，不判断偏斜" if all(totals[m] < limits[m] for m in limits) else "未见明显相对热点",
        "skew_minutes": len(union) * 5 if enough else None,
        "skew_longest": longest(union) / 60 if enough else None,
    }


def site_hotspots(generated):
    result = copy.deepcopy(generated)
    result["rows"] = [r for r in result["rows"] if r["scope"] == "本Site" and r["rank"] <= 10]
    keep = {"dimension", "rank", "cluster", "svm", "volume", "iops_avg", "io_avg", "lat_avg"}
    result["fields"] = [f for f in result["fields"] if f["key"] in keep]
    result["frozen"] = 2
    result["title"] = "本 Site 热点卷 TOP 10"
    result["note"] = "分别按窗口平均延迟、吞吐量、IOPS降序取前10；只含逻辑卷，不含节点根卷与重复的FlexGroup子卷。少于10个有效卷则按实列出；排名不等于故障。"
    order = {"延迟": 0, "吞吐": 1, "IOPS": 2}
    result["rows"].sort(key=lambda r: (order[r["dimension"]], r["rank"]))
    return result


def protocol_sheet(analysis):
    output, inactive = [], collections.Counter()
    for node, obj, svm, resource in sorted({k[:4] for k in analysis.series
                                            if k[1] in ("nfsv3", "nfsv4", "nfsv4_1", "smb1", "smb2")}):
        get = lambda m: analysis.get(node, obj, resource, m, svm)
        ident = next((i for s in analysis.supplements if s["manifest"]["node"] == node
                      for i in s["manifest"]["identities"]
                      if i["object"] == obj and i["resource"] == resource), {})
        actual_svm = svm or ident.get("vserver_name") or "未标注（保留PA实例）"
        protocol = {"nfsv3": "NFSv3", "nfsv4": "NFSv4.0", "nfsv4_1": "NFSv4.1",
                    "smb1": "CIFS / SMB1", "smb2": "CIFS / SMB2+3"}[obj]
        smb = obj.startswith("smb")
        total = get("ops" if smb else obj + "_ops")
        candidates = []
        if smb:
            candidates.append(("总操作", "协议总计", "ops", "latency", None))
            candidates.extend([
                ("READ 类", "读类（含扩展读）", "read_class_ops", "read_class_latency", "read_data"),
                ("WRITE 类", "写类（含扩展写）", "write_class_ops", "write_class_latency", "write_data"),
                ("OTHER 类", "非读写类（不等于元数据）", "other_ops", None, None),
            ])
            # Each command's count is its schema-verified latency base.
            for key, values in analysis.series.items():
                if key[:4] == (node, obj, svm, resource) and key[4].endswith("_latency_base"):
                    operation = key[4].removesuffix("_latency_base")
                    if operation not in ("read", "write", "pipe_read", "pipe_write"):
                        category = ("元数据/目录" if operation in {
                            "create", "close", "query_directory", "query_info", "set_info",
                            "open", "delete", "rename", "create_directory", "delete_directory",
                            "query_information", "set_information",
                        } else "其他协议命令")
                        if operation in ("negotiate", "session_setup", "logoff", "tree_connect", "tree_disconnect"):
                            category = "协商/会话控制"
                        candidates.append((operation.upper(), category, key[4], operation + "_latency", None))
        else:
            candidates.append(("总操作", "协议总计", obj + "_ops", None, None))
            for key in analysis.series:
                if key[:4] != (node, obj, svm, resource) or not key[4].endswith("_total"):
                    continue
                op = key[4].removesuffix("_total")
                candidates.append((op.upper(), "数据" if op in ("read", "write") else "元数据/协议控制",
                                   key[4], op + "_avg_latency",
                                   obj + "_" + op + "_throughput" if op in ("read", "write") else None))
        active = any(any(r.get("value") and r["value"] > 0 for r in get(c[2])) for c in candidates)
        if not active:
            inactive[(analysis.nodes[node]["cluster"], node, protocol)] += 1
            continue
        for operation, category, counter, latency_counter, byte_counter in candidates:
            src = get(counter)
            if not src or operation != "总操作" and not any(r.get("value") and r["value"] > 0 for r in src):
                continue
            latency = get(latency_counter) if latency_counter else []
            throughput = get(byte_counter) if byte_counter else []
            common = set(bin_intervals(src)) & set(bin_intervals(latency)) if latency else set()
            output.append({
                "cluster": analysis.nodes[node]["cluster"], "node": node, "protocol": protocol,
                "svm": actual_svm, "instance": resource, "operation": operation, "category": category,
                "rate": safe_mean(src), "io": safe_mean(throughput), "latency": safe_mean(latency),
                "minutes": span(src) / 60, "lat_minutes": span(latency) / 60 if latency else None,
                "io_minutes": span(throughput) / 60 if throughput else None,
                "common_minutes": len(common) * 5 if latency else None,
                "source": counter + (f"；{latency_counter}" if latency_counter else ""),
                "notes": (
                    "类别行与具体命令有重叠，不相加。总操作与延迟可为不同原生窗口，分别标注时长。"
                    if smb else "NFSv4总调用与子操作不保证相加；不跨层叠加延迟。"
                    if obj != "nfsv3" else "协议层操作与延迟；不归因到单卷，不与WAFL延迟相加。"
                ),
            })
    # One compact row records inactive/missing protocol scope without hundreds of zero operations.
    for (cluster, node, protocol), count in sorted(inactive.items()):
        output.append({"cluster": cluster, "node": node, "protocol": protocol,
                       "operation": "其他无活动实例", "category": "无活动实例汇总",
                       "instance": f"{count}个PA实例", "inactive": True,
                       "notes": "这些实例本窗口未见非零有效差分，不代表本节点此协议无活动；缺失不补零。"})
    order = {"CIFS / SMB2+3": 0, "NFSv3": 1, "NFSv4.0": 2, "NFSv4.1": 3, "CIFS / SMB1": 4}
    return table("协议与元数据", "NFS 与 CIFS/SMB 操作分析",
                 "按协议、节点和PA实例分别统计。仅展开有实际活动的操作；分类与明细不叠加。延迟单位按PA累计量/base换算，待同窗校准。",
                 [
                     ("cluster", "集群", 24), ("node", "节点", 25), ("protocol", "协议", 22),
                     ("svm", "SVM", 29), ("operation", "操作", 31), ("category", "类别", 30),
                     ("rate", "平均 IOPS", 19, N), ("io", "平均 IO MiB/s", 21, N),
                     ("latency", "协议平均延迟 ms", 23, M),
                     ("minutes", "IOPS有效 分钟", 20, N), ("lat_minutes", "延迟有效 分钟", 20, N),
                     ("io_minutes", "IO有效 分钟", 20, N), ("common_minutes", "操作/延迟共同 分钟", 25, N),
                     ("instance", "PA实例", 37), ("source", "Counter依据", 55),
                     ("notes", "口径说明", 69),
                 ], sorted(output, key=lambda r: (bool(r.get("inactive")), order[r["protocol"]], r["cluster"], r["node"],
                                                  r.get("svm", ""), r.get("instance", ""),
                                                  r["operation"] != "总操作", r["operation"])), 3)


def fabricpool_sheet(analysis):
    output = []
    covered_nodes = set()
    for node, obj, svm, resource in sorted({k[:4] for k in analysis.series
                                            if k[1] == "object_store_client_op_stats"}):
        # The command comes from the explicit PA instance suffix, not volume-name guessing.
        match = re.fullmatch(r"(.+)_(put|get|get_multirange)", resource, re.IGNORECASE)
        if not match:
            continue
        src = {name: analysis.get(node, obj, resource, name, svm) for name in (
            "stats.total", "throughput_bytes", "average_latency.total-latency")}
        if not any(any(r.get("value") and r["value"] > 0 for r in rows) for rows in src.values()):
            continue
        covered_nodes.add(node)
        bins = {name: bin_intervals(rows) for name, rows in src.items()}
        common = set.intersection(*(set(b) for b in bins.values()))
        means = {}
        for name, b in bins.items():
            denominator = sum(b[t]["denominator"] for t in common)
            means[name] = (sum(b[t]["numerator"] for t in common) / denominator *
                           b[next(iter(common))]["scale"]) if denominator else None
        put = match[2].lower() == "put"
        output.append({
            "cluster": analysis.nodes[node]["cluster"], "node": node, "handle": match[1],
            "direction": "云端写入 PUT" if put else "云端读取 " + match[2].upper(),
            "io": means["throughput_bytes"], "iops": means["stats.total"],
            "latency": means["average_latency.total-latency"],
            "minutes": len(common) * 5, "instance": resource,
            "scope": "对象存储客户端；未确认FabricPool专用",
            "notes": "写入含后台整理等可能，不能单独识别纯冷数据下沉。"
                     if put else "对象存储GET，不等同于净回迁数据量。",
        })
    for info in analysis.batch:
        if info["node"] not in covered_nodes:
            output.append({"cluster": info["cluster"], "node": info["node"],
                           "direction": "本窗口无有效非零 GET/PUT",
                           "notes": "未见活动不等于没有配置FabricPool；缺失不补零。"})
    return table("FabricPool", "FabricPool 相关云端 IO",
                 "同一对象存储命令的 IO、IOPS、延迟使用共同5分钟格。PUT仅代表云端写入，不能独立确认冷数据下沉，亦不代表净释放容量。",
                 [("cluster", "集群", 25), ("node", "节点", 27), ("direction", "方向", 31),
                  ("handle", "PA存储句柄", 17), ("io", "平均 IO MiB/s", 23, "0.000000"),
                  ("iops", "平均 IOPS", 20, "0.000000"), ("latency", "云端平均延迟 ms", 26, M),
                  ("minutes", "共同有效 分钟", 22, N), ("instance", "PA实例", 28),
                  ("scope", "归属确认", 48), ("notes", "说明", 65)], output, 3)


def prepare(baseline, output, counters, network_file, config):
    model = load(baseline / "report_model.json")
    original = copy.deepcopy(model)
    batch = load(baseline / "data/batch.json")
    base, supplements = [], []
    for n in batch:
        base.extend(load(baseline / "data" / f"{n['node']}.samples.json.gz"))
        root = counters / n["source_sha256"]
        manifest = load(root / "audit.json")
        if manifest["node_uuid"] != n["node_uuid"] or manifest["source_sha256"] != n["source_sha256"]:
            raise ValueError("Supplement source mismatch")
        supplements.append(dict(manifest=manifest, intervals=load(root / "intervals.json.gz"), raw=[]))
    network = load(network_file)
    config["port_roles"] = {model["customer"]: network["ports"]}
    analysis = Analysis(batch, model, base, supplements, config)
    for name in ("node_analysis", "volume_analysis", "hotspots", "flexgroups", "network"):
        print("ANALYZE", name, flush=True)
        getattr(analysis, name)()
    generated = {s["name"]: s for s in analysis.sheets}
    sheets = {s["name"]: s for s in model["sheets"]}
    for r in sheets["Node"]["rows"]:
        extra = analysis.node_results[r["resource_id"]]
        r.update({k: extra[k] for k in ("cpu_p95", "cpu_high_minutes", "cpu_high_longest")})
        r["cpu_threshold"] = analysis.cfg(r["node"])["cpu_high_pct"] / 100
        if extra["consistency"].startswith("system 为零"):
            r["notes"] += "；system IOPS为零但后端有负载，不能据此判断节点空闲。"
    append_fields(sheets["Node"], [
        ("cpu_p95", "CPU 区间 P95", 21, P), ("cpu_threshold", "CPU 高负载阈值", 22, P),
        ("cpu_high_minutes", "CPU 高负载 分钟", 23, N),
        ("cpu_high_longest", "高负载最长 分钟", 23, N)], "coverage")
    for r in sheets["Aggr"]["rows"]:
        r.update(resource_mix(analysis, r, True))
    append_fields(sheets["Aggr"], MIX_COLUMNS, "io_minutes")
    for name in ("Volume", "FlexGroup"):
        for r in sheets[name]["rows"]:
            extra = analysis.vol_results[r["resource_id"]]
            r.update({k: extra[k] for k in ("read_iops", "write_iops", "other_iops", "read_size",
                                           "write_size", "other_lat", "lat_p95", "high_minutes",
                                           "longest", "structure_minutes")})
            r.update(resource_mix(analysis, r))
        append_fields(sheets[name], [
            ("read_iops", "Read IOPS", 18, N), ("write_iops", "Write IOPS", 18, N),
            ("other_iops", "Other IOPS", 18, N),
            *MIX_COLUMNS,
            ("read_size", "平均 Read IO KiB", 24, N), ("write_size", "平均 Write IO KiB", 24, N),
            ("other_lat", "Other 延迟 ms", 21, M), ("lat_p95", "区间延迟 P95 ms", 24, M),
            ("high_minutes", "高延迟 >10ms\n分钟", 23, N), ("longest", "高延迟最长 分钟", 24, N),
            ("structure_minutes", "操作结构共同 分钟", 24, N),
        ], "zombie_blocks")
        sheets[name]["note"] += "；读写比例分别按操作数/字节数计算（不含Other）；跨preset按共同5分钟格对齐。"
        # Zombie gauges remain the two columns immediately before effective duration.
        keys = [f["key"] for f in sheets[name]["fields"]]
        assert keys[keys.index("valid_minutes") - 2:keys.index("valid_minutes")] == ["zombie_blocks", "zombie_tib"]
    fg_summary = generated["FlexGroup 深度分析"]
    fg_summary["title"] = "FlexGroup 大卷负载分布"
    fg_summary["note"] = "与成员明细采用同一共同5分钟窗口。相对热点只评价已收成员及有效片段，覆盖不足不推断全请求窗口持续性；低总负载不判热点。"
    for parent in sheets["Volume"]["rows"]:
        if parent["style"] != "FlexGroup":
            continue
        members, bins, common = analysis.fg_alignment_cache[parent["resource_id"]]
        markings, conclusion = member_hotspots(members, bins, common, analysis.cfg(members[0]["node"]))
        for member in members:
            marker = markings[member["resource_id"]]
            member.update(marker)
            if marker["hotspot_zero_peers"]:
                member["notes"] += "；其他成员该维度均为0，倍数为N/A；本成员为唯一有负载成员。"
            if parent["coverage"] < analysis.cfg(member["node"])["minimum_coverage"]:
                member["notes"] += "；相对热点仅代表共同有效窗口，覆盖不足，不能判断整段请求窗口持续性。"
        summary = next(r for r in fg_summary["rows"] if r["resource_id"] == parent["resource_id"])
        summary.update(conclusion)
    append_fields(sheets["FlexGroup"], [("hotspot", "相对热点标记", 25)], "date")
    append_fields(sheets["FlexGroup"], [
        ("peer_iops_ratio", "IOPS / 其他成员均值", 27, "0.00"),
        ("peer_io_ratio", "吞吐 / 其他成员均值", 27, "0.00"),
        ("hot_minutes", "相对热点 分钟", 22, N),
        ("hot_longest", "相对热点最长 分钟", 26, N),
    ], "zombie_blocks")
    chosen = {
        "cluster", "svm", "volume", "risk", "members", "valid_members", "iops_avg", "io_avg",
        "hot_iops", "hot_iops_share", "hot_io", "hot_io_share", "iops_ratio", "io_ratio",
        "iops_cv", "io_cv", "skew_minutes", "skew_longest", "slow", "slow_latency",
        "slow_minutes", "valid_minutes", "coverage",
    }
    fg_summary["fields"] = [f for f in fg_summary["fields"] if f["key"] in chosen]
    fg_summary["sheet_name"] = "FlexGroup"
    fg_summary["header_row"] = len(sheets["FlexGroup"]["rows"]) + 10
    fg_nodes = generated["FlexGroup 节点分布"]
    fg_nodes["sheet_name"] = "FlexGroup"
    fg_nodes["header_row"] = fg_summary["header_row"] + len(fg_summary["rows"]) + 5
    model["sections"] = [s for s in (fg_summary, fg_nodes) if s["rows"]]
    sheets["FlexGroup"]["note"] = "成员明细；下方同页另列大卷分析与节点分布。各层数据不可重复相加。" + sheets["FlexGroup"]["note"]

    errors = generated["网络错误明细"]["rows"]
    error_lookup = {(r.get("node"), r.get("port"), r.get("counter")): r for r in errors if r.get("counter")}
    nonzero = {r["counter"] for r in errors if (r.get("delta") or 0) > 0}
    known = {r["counter"] for r in errors if r.get("counter")}
    names = sorted(nonzero | (known & {"rx_errors", "tx_errors", "rx_discards", "tx_discards"}))
    for r in sheets["Network Port"]["rows"]:
        extra = analysis.port_results[r["resource_id"]]
        r.update({k: extra[k] for k in ("rx_peak", "tx_peak", "rx_util_peak", "tx_util_peak",
                                      "rx_high", "tx_high", "rx_longest", "tx_longest")})
        role = network["ports"].get(r["node"], {}).get(r["port"], {})
        r.update({k: role.get(k, "未知") for k in ("role", "ifgrp", "port_type", "role_source")})
        r["asup_date"] = role.get("asup_date")
        if not role:
            source = next((s for s in network["sources"] if s["node"] == r["node"]), None)
            r["role_source"] = (source["file"] + "；网络配置未列该PA接口" if source
                                else "未匹配到同节点ASUP配置")
            r["asup_date"] = source["start"] if source else None
        for counter in names:
            error = error_lookup.get((r["node"], r["port"], counter))
            r["error_" + counter] = error["delta"] if error else None
        observed = [v for (node, port, _), v in error_lookup.items() if node == r["node"] and port == r["port"]]
        r["error_minutes"] = min((v["minutes"] for v in observed), default=None)
    append_fields(sheets["Network Port"], [("role", "端口用途（Role）", 32)], "date")
    append_fields(sheets["Network Port"], [
        ("rx_peak", "原生 RX 峰值 MiB/s", 26, N), ("tx_peak", "原生 TX 峰值 MiB/s", 26, N),
        ("rx_util_peak", "RX 峰值利用率", 21, P), ("tx_util_peak", "TX 峰值利用率", 21, P),
        ("rx_high", "RX 高利用 分钟", 22, N), ("tx_high", "TX 高利用 分钟", 22, N),
        ("rx_longest", "RX 高利用最长 分钟", 27, N),
        ("tx_longest", "TX 高利用最长 分钟", 27, N),
        *[("error_" + c, c + "\n窗口增量", max(23, len(c)), INT) for c in names],
        ("error_minutes", "错误计数有效\n分钟(最短)", 25, N),
        ("ifgrp", "所属 IFGRP", 20), ("port_type", "ASUP 端口类型", 23),
        ("asup_date", "用途配置时点 +08:00", 27, "yyyy-mm-dd hh:mm"),
        ("role_source", "用途来源（非PA同窗）", 77),
    ], "coverage")
    unknown = sum(r["role"] == "未知" for r in sheets["Network Port"]["rows"])
    sheets["Network Port"]["note"] = (
        f"全程down不展示；{unknown}个PA接口角色未知。端口Role直接取ASUP network-ports，与SAM同源，不拼接LIF协议。"
        "RX/TX各自>70%为高利用；ASUP配置时点另列，非PA历史流量归因。端口延迟无counter，保持N/A。")
    hotspots = site_hotspots(generated["热点卷分析"])
    model["analysis_sheets"] = [hotspots, protocol_sheet(analysis), fabricpool_sheet(analysis)]
    model["compact"] = True
    model["model_version"] = "performance-report-v6-refined"
    model["metric_version"] = "pa-window-v2.2-refined"
    model["analysis_config"] = {k: v for k, v in config.items() if k != "port_roles"}
    model["output_filename"] = original["output_filename"]
    model["info"] = [
        ["报告范围", f"{model['customer']}；{len(batch)}节点、{len({n['cluster'] for n in batch})}集群。每项仅代表PA有效采样窗口，非整月平均。"],
        ["报表布局", "5张资源表、1张热点表、1张协议表、1张FabricPool表。FlexGroup页内含成员、大卷、节点三个表。"],
        ["基础指标", "原版IO、IOPS、CPU、延迟、利用率、zombie基础数值沿用，不把不同层级IO相加；N/A不是0。"],
        ["读写比例", "操作比=读操作/(读+写)，写操作同理；数据量比=读字节/(读+写字节)。不含Other；使用各自共同5分钟格，零分母不计算。"],
        ["时序对齐", "沿用5分钟格按原生区间重叠比例分配累计增量，不生成更细IO采样。跨preset有相位近似，真实有效时长单列。"],
        ["操作结构", "Read/Write/Other IOPS、平均IO Size使用操作结构共同窗口；该窗口可能与原版吞吐窗口不同。Other不是元数据；不强制等于原版Total。"],
        ["CPU与区间P95", "按有效区间时长加权P95，不是单IO延迟P95。高CPU/高延迟时长由本地阈值判断，非厂商故障结论。"],
        ["阈值", "默认CPU>70%、延迟>10ms、单向端口利用率>70%。严格大于，等于阈值不计入高负载/高延迟时长；高延迟及最长连续时长已重新计算。"],
        ["Aggr 数据源", "IO读写字节与总延迟采用原生小时级kernel/asup，IOPS采用kernel/opm。短窗口若无完整小时差分，相关指标保持N/A，不用卷数据替代。"],
        ["网络端口", "RX+TX全双工合计可超过100%；单向峰值/高利用时长分开看。没有已验证端口延迟counter。错误类型可能重叠，不计算混合总错数。"],
        ["端口用途", "按节点名、序列号和集群UUID匹配最近ASUP；主列直接取network-ports.xml的role，与SAM Network Port的Role同源。data不是NFS或CIFS协议名称，不继承LIF服务作为端口角色；未知不猜测。各行列出真实配置日期，不宣称与PA同窗。"],
        ["FlexGroup", "Volume仅展示大卷；FlexGroup成员明细不再进入逻辑卷热点总榜。成员数量为实际收到数量，不做SAM清单核对。大卷与节点表在FlexGroup成员明细下方。"],
        ["子卷相对热点", "只比较同一大卷的已收成员及共同5分钟格，至少2成员、30分钟。窗口平均IOPS或吞吐>其他成员均值2倍标记热点；期间任一5分钟格超2倍另记热点分钟/最长连续分钟。IOPS维度需大卷>=1000 IOPS，吞吐维度需>=10 MiB/s，分别判断。其他成员均为0时倍数N/A，但可标唯一负载热点；低总负载不判热点。"],
        ["热点边界", "热点标记表示采样窗口相对访问集中，不是成员故障。覆盖不足时只评价共同有效片段，不宣称全请求窗口持续偏斜；未提供的成员不参与判断。"],
        ["zombie", "wvblk_zombie_blks为时点待回收块数；待回收TiB=blocks*4096/1024^4，保留2位。位置紧邻有效时长左侧。"],
        ["协议", "NFSv3/v4/v4.1与CIFS(SMB1/SMB2+3)独立展示；按实际活动展开，不把NFS零值当主协议。SMB分类行与命令行不相加。"],
        ["FabricPool", "按对象存储客户端命令提取PUT/GET：throughput_bytes、stats.total、average_latency[total-latency]/ops[total-latency]。仅在三项共同格计算。"],
        ["下沉限制", "本批PUT不能独立区分冷数据下沉、整理或其他后台云端写入，句柄未映射分层策略；不标成确认下沉量，不预测物理容量释放。"],
        ["校准限制", "PA实验解析已核验源文件校验和、差分及base依赖；新增SMB与云端延迟单位尚待AIQUM同窗数值校准，不宣称官方后台等效。"],
        ["后续趋势", "本版不展示趋势/风险/能力矩阵等页。源SHA、原生区间缓存和resource_id保留，待后续真实同口径批次再做趋势。"],
    ]
    model["sources"].extend(network["sources"])
    # Full equality for the approved primary numerical metrics, despite authorized layout changes.
    for current, old in zip(model["sheets"], original["sheets"]):
        assert len(current["rows"]) == len(old["rows"])
        for a, b in zip(current["rows"], old["rows"]):
            for key in ("resource_id", "io_avg", "iops_avg", "lat_avg", "cpu_avg", "util_avg",
                        "read_io", "write_io", "zombie_blocks", "zombie_tib"):
                assert a.get(key) == b.get(key), (current["name"], a.get("resource_id"), key)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report_model.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print("COMPACT_MODEL", {s["name"]: len(s["rows"]) for s in model["sheets"] + model["analysis_sheets"]}, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--counters", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("performance_config.json"))
    args = parser.parse_args()
    prepare(args.baseline, args.output, args.counters, args.network,
            {**DEFAULTS, **load(args.config)})


if __name__ == "__main__":
    main()
