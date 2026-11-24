"""V2 window analytics; deliberately independent of the six approved base sheets."""

from bisect import bisect_right
import collections
from datetime import datetime, timedelta, timezone
import math
import statistics

from prepare_performance_report import (
    aggregate_bins, bin_intervals, bin_stats, combine_rate_rows, field,
    port_utilization, seconds, summarize, VOLUME_METRICS,
)

METRIC_VERSION = "pa-window-v2.2.0"
TZ = timezone(timedelta(hours=8))
DEFAULTS = {
    "cpu_watch_pct": 70, "cpu_high_pct": 70, "iops_high": 100000,
    "throughput_high_gbps": 5, "latency_watch_ms": 10, "latency_high_ms": 10,
    "latency_severe_ms": 20, "network_watch": .70, "network_high": .70,
    "top_n": 10, "minimum_coverage": .90, "fg_min_iops": 1000,
    "fg_min_mib_s": 10, "fg_min_members": 2, "fg_skew_ratio": 2,
    "fg_slow_ratio": 2, "fg_min_common_minutes": 30,
    "window_hour_tolerance": 1, "window_duration_tolerance": .25,
    "clusters": {}, "platforms": {}, "expected_inventory": {}, "port_roles": {},
}


def thresholds(config, cluster, platform=None):
    result = {**DEFAULTS, **config}
    result.update(config.get("platforms", {}).get(platform, {}))
    result.update(config.get("clusters", {}).get(cluster, {}))
    result["throughput_high_mib_s"] = result["throughput_high_gbps"] * 1e9 / 8 / 2**20
    return result


def valid(rows):
    return [r for r in rows if isinstance(r.get("value"), (int, float)) and math.isfinite(r["value"])]


def span(rows):
    merged = []
    for r in sorted(rows, key=lambda r: r["a"]):
        a, b = r["a"], r["b"]
        if merged and a < merged[-1][1] - .001:
            raise ValueError("Overlapping metric intervals")
        if merged and abs(a - merged[-1][1]) < .001:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return sum(b - a for a, b in merged)


def longest(rows):
    total, start, end = 0, None, None
    for r in sorted(rows, key=lambda r: r["a"]):
        if end is None or abs(r["a"] - end) > .001:
            start = r["a"]
        end = r["b"]
        total = max(total, end - start)
    return total


def p95(rows):
    """Time-weighted nearest-rank quantile of native interval means, not IOs."""
    items = sorted(valid(rows), key=lambda r: r["value"])
    target = sum(r["b"] - r["a"] for r in items) * .95
    elapsed = 0
    for r in items:
        elapsed += r["b"] - r["a"]
        if elapsed >= target:
            return r["value"]
    return None


def above(rows, threshold):
    available = valid(rows)
    high = [r for r in available if r["value"] > threshold]
    duration, observed = span(high), span(available)
    return {
        "minutes": duration / 60, "share": duration / observed if observed else None,
        "longest": longest(high) / 60, "valid_minutes": observed / 60,
    }


def overlap(left, right, predicate=lambda a, b: True):
    """Intersect native interval windows without inventing observations."""
    left, right = valid(left), valid(right)
    i, j, result = 0, 0, []
    while i < len(left) and j < len(right):
        a, b = left[i], right[j]
        start, end = max(a["a"], b["a"]), min(a["b"], b["b"])
        if end > start and predicate(a["value"], b["value"]):
            result.append({"a": start, "b": end, "left": a["value"], "right": b["value"]})
        if a["b"] <= b["b"]:
            i += 1
        else:
            j += 1
    return result


def simultaneous(left, right, left_threshold, right_threshold):
    common = overlap(left, right)
    high = [r for r in common if r["left"] > left_threshold and r["right"] > right_threshold]
    covered, busy = span(common), span(high)
    return busy / 60, busy / covered if covered else None, covered / 60


def safe_mean(rows):
    return summarize(rows)["avg"] if rows else None


def binned_rows(bins, common=None):
    selected = set(bins) if common is None else common
    return [
        {**r, "a": r["start"], "b": r["end"], "seconds": 300, "period_ms": 300000}
        for r in aggregate_bins([bins], selected)
    ]


def table(name, title, note, columns, rows, frozen=3):
    return {
        "name": name, "title": title, "note": note, "frozen": frozen,
        "fields": [field(*c) for c in columns], "rows": rows,
    }


IDENTITY = [("cluster", "集群", 23), ("node", "节点", 23)]
VOL_ID = [("cluster", "集群", 23), ("svm", "SVM", 24), ("volume", "卷", 36)]
N = "#,##0.0"
P = "0.0%"
M = "0.000"
INT = "#,##0"
END = [("status", "有效性", 20), ("notes", "依据 / 数据限制", 66)]


class Analysis:
    def __init__(self, batch, model, base_intervals, supplements, config):
        self.batch, self.model, self.config = batch, model, config
        self.nodes = {n["node"]: n for n in batch}
        self.series = collections.defaultdict(list)
        self.base_keys = set()
        self.supplements = supplements
        self.sheets = []
        self.facts = []
        self.fg_risks = {}
        self.node_results = {}
        self.vol_results = {}
        self.port_results = {}
        self.fg_alignment_cache = {}
        self.inventory = {}
        for n in batch:
            for item in n["inventory"]:
                self.inventory[(n["node"], item["object"], item.get("vserver_name", ""),
                                item.get("instance_name", item.get("instance_uuid", "")))] = item
        for r in base_intervals:
            r.setdefault("a", seconds(r["start"]))
            r.setdefault("b", seconds(r["end"]))
            key = (r["node"], r["object"], r["svm"], r["resource"], r["metric"])
            self.series[key].append(r)
            self.base_keys.add(key)
        grouped = collections.defaultdict(lambda: collections.defaultdict(list))
        for sup in supplements:
            for r in sup["intervals"]:
                key = (r["node"], r["object"], r["svm"], r["resource"], r["metric"])
                grouped[key][(r["period_ms"], r["group"])].append(r)
        for key, choices in grouped.items():
            if key in self.series:
                continue
            # One native source per counter. Never concatenate duplicate presets.
            best = min(choices, key=lambda x: (
                -span(choices[x]), x[0], {"kernel/opm": 0, "kernel": 1, "kernel/asup": 2}.get(x[1], 3)))
            self.series[key] = choices[best]
        for rows in self.series.values():
            rows.sort(key=lambda r: r["a"])
        self.base_sheets = {s["name"]: s for s in model["sheets"]}
        self.member_lookup = {
            (r["node"], r["svm"], r["volume"]): r for r in self.base_sheets["FlexGroup"]["rows"]
        }

    def get(self, node, obj, resource, metric, svm=""):
        return self.series.get((node, obj, svm, resource, metric), [])

    def cfg(self, node):
        n = self.nodes[node]
        return thresholds(self.config, n["cluster"], n["model"])

    def add(self, *args, **kwargs):
        self.sheets.append(table(*args, **kwargs))

    def evidence(self, category, cluster, resource, phenomenon, evidence, cause, followup, node=""):
        self.facts.append(dict(
            category=category, cluster=cluster, node=node, resource=resource,
            phenomenon=phenomenon, evidence=evidence, cause=cause, followup=followup,
        ))

    def data_quality(self):
        rows = []
        groups = collections.defaultdict(list)
        for n in self.batch:
            groups[n["cluster"]].append(n)
        expected = self.config.get("expected_inventory", {}).get(self.model["customer"], {})
        for cluster in sorted(set(groups) | set(expected)):
            nodes = groups.get(cluster, [])
            observed = sorted(n["node"] for n in nodes)
            definition = expected.get(cluster, {})
            expected_nodes = definition.get("nodes")
            missing = sorted(set(expected_nodes or []) - set(observed)) if expected_nodes is not None else None
            extra = sorted(set(observed) - set(expected_nodes or [])) if expected_nodes is not None else None
            cpu_sets = [self.get(n["node"], "system", n["node"], "cpu_busy_pct") for n in nodes]
            common = valid(cpu_sets[0]) if cpu_sets else []
            for other in cpu_sets[1:]:
                common = [{**r, "value": 1} for r in overlap(common, other)]
            rows.append({
                "cluster": cluster, "node": "", "object": "集群完整性", "resource": "",
                "metric": "期望节点与跨节点 CPU 共同窗口",
                "expected": len(expected_nodes) if expected_nodes is not None else None,
                "observed": len(observed), "missing": ",".join(missing) if missing is not None else "未知",
                "value": span(common) / 60 if nodes else None, "unit": "共同分钟",
                "status": "PARTIAL" if expected_nodes is None or missing or extra else "VALID",
                "notes": (
                    "期望清单未知；不能据已收节点数确认完整。已收：" + ",".join(observed)
                    if expected_nodes is None else
                    f"清单来源：{definition.get('source', '用户配置')}；额外节点：{','.join(extra) or '无'}"
                ),
            })
        for key, samples in sorted(self.series.items()):
            node, obj, svm, res, metric = key
            if obj == "nic_common" and not any(
                p["node"] == node and p["port"] == res for p in self.base_sheets["Network Port"]["rows"]
            ):
                continue
            n = self.nodes[node]
            duration = (n["requested_end_ms"] - n["requested_start_ms"]) / 1000
            coverage, usable = span(samples) / duration, span(valid(samples)) / duration
            positive = [r["value"] for r in valid(samples) if r["value"] > 0]
            median = statistics.median(positive) if positive else None
            jump_candidates = (
                sum(r["value"] > median * 50 for r in valid(samples))
                if median and len(positive) >= 10 and "pct" not in metric and "latency" not in metric else 0
            )
            status = "INVALID" if not samples else "PARTIAL" if coverage < self.cfg(node)["minimum_coverage"] else "VALID"
            rows.append({
                "cluster": n["cluster"], "node": node, "object": obj,
                "resource": f"{svm}/{res}" if svm else res, "metric": metric,
                "value": safe_mean(samples), "unit": samples[0].get("unit", unit_for(metric)) if samples else unit_for(metric),
                "coverage": coverage, "numeric_coverage": usable,
                "jump_candidates": jump_candidates,
                "native": ",".join(str(x / 1000) for x in sorted({r["period_ms"] for r in samples})),
                "start": min((r["a"] for r in samples), default=0) * 1000 or None,
                "end": max((r["b"] for r in samples), default=0) * 1000 or None,
                "status": status,
                "notes": (
                    ("零操作延迟为 N/A，计入有效计数覆盖，不计数值覆盖。" if usable < coverage else "")
                    + ("存在>非零区间中位数50倍的候选跳变，可能是真实突发，未剔除。" if jump_candidates else "")
                ),
            })
        # Base definitions with no valid intervals would otherwise disappear.
        for n in self.batch:
            for m in n["metrics"]:
                if m["valid_intervals"] or m.get("zero_base_intervals"):
                    continue
                rows.append({
                    "cluster": n["cluster"], "node": n["node"], "object": m["object"],
                    "resource": m["resource"], "metric": m["metric"], "coverage": 0,
                    "numeric_coverage": 0, "status": "INVALID",
                    "native": ",".join(str(p / 1000) for p in m["native_period_ms"]),
                    "notes": f"窗口内原始快照 {m['raw_samples_in_window']} 点；无可用差分，不补零",
                })
            for kind in ("rejected_gap_intervals", "rejected_reset_intervals"):
                if n["stats"].get(kind):
                    rows.append({
                        "cluster": n["cluster"], "node": n["node"], "object": "基础解析器审计",
                        "metric": kind, "value": n["stats"][kind], "unit": "interval",
                        "status": "PARTIAL", "notes": "基础 v3 仅记录节点总计，未定位到具体 counter；原始 PA 可重算复核",
                    })
        for sup in self.supplements:
            for diag in sup["manifest"]["diagnostics"]:
                rows.append({
                    "cluster": sup["manifest"]["cluster"], "node": diag["node"], "object": diag["object"],
                    "resource": diag["resource"], "metric": diag["counter"], "status": "PARTIAL",
                    "notes": str({k: v for k, v in diag.items() if k in ("gap", "reset_or_wrap", "identity_or_sample_conflicts", "implausible_percentage")}),
                })
        self.add("数据质量", "数据质量与每指标覆盖",
                 "VALID 仅表示所选指标可计算；期望清单未知不等于集群完整。零操作与缺样分开记录。",
                 IDENTITY + [
                     ("object", "对象 / 审计项", 23), ("resource", "资源", 40), ("metric", "指标", 34),
                     ("value", "数值", 17, M), ("unit", "单位", 16),
                     ("coverage", "计数有效覆盖", 17, P), ("numeric_coverage", "数值有效覆盖", 17, P),
                     ("jump_candidates", "候选跳变区间数", 22, INT),
                     ("native", "原生周期 秒", 19), ("expected", "期望节点数", 15, INT),
                     ("observed", "实收节点数", 15, INT), ("missing", "缺失节点", 36),
                     ("start", "有效开始 +08:00", 23, "yyyy-mm-dd hh:mm:ss"),
                     ("end", "有效结束 +08:00", 23, "yyyy-mm-dd hh:mm:ss"),
                 ] + END, rows, 2)

    def node_analysis(self):
        rows = []
        for base in self.base_sheets["Node"]["rows"]:
            node = base["node"]
            cfg = self.cfg(node)
            cpu = self.get(node, "system", node, "cpu_busy_pct")
            iops = self.get(node, "system", node, "iops")
            io = self.get(node, "system", node, "throughput_mib_s")
            lat = self.get(node, "system", node, "latency_ms")
            watch, high = above(cpu, cfg["cpu_watch_pct"]), above(cpu, cfg["cpu_high_pct"])
            iops_high, io_high, lat_high = above(iops, cfg["iops_high"]), above(io, cfg["throughput_high_mib_s"]), above(lat, cfg["latency_watch_ms"])
            co, _, common = simultaneous(cpu, lat, cfg["cpu_high_pct"], cfg["latency_watch_ms"])
            peers = [n["node"] for n in self.batch if n["cluster"] == base["cluster"]]
            bins = [bin_intervals(self.get(p, "system", p, "cpu_busy_pct"), 60) for p in peers]
            common_bins = set.intersection(*(set(b) for b in bins)) if bins else set()
            peer_values = [
                sum(b[t]["numerator"] for t in common_bins) /
                sum(b[t]["denominator"] for t in common_bins) * 100 if common_bins else None for b in bins
            ]
            peer_mean = statistics.mean(peer_values) if common_bins else None
            skew = peer_values[peers.index(node)] / peer_mean if peer_mean else None
            processor = [
                (key[3], samples) for key, samples in self.series.items()
                if key[0] == node and key[1] == "processor" and key[4] == "processor_busy"
            ]
            busiest = max(processor, key=lambda x: safe_mean(x[1]) or 0) if processor else None
            processor_avg = safe_mean(busiest[1]) if busiest else None
            processor_peak = max((r["value"] for r in valid(busiest[1])), default=None) if busiest else None
            backend = [
                r for r in self.base_sheets["Aggr"]["rows"] if r["node"] == node
                and (r.get("iops_avg") or 0) > 0
            ]
            consistency = "system 为零但后端有负载" if base["iops_avg"] == 0 and (backend or (base["cpu_avg"] or 0) > .05) else "未发现此类冲突"
            row = {
                **base, "cpu_p95": p95(cpu) / 100 if cpu else None,
                "cpu_watch_minutes": watch["minutes"], "cpu_watch_share": watch["share"],
                "cpu_watch_longest": watch["longest"], "cpu_high_minutes": high["minutes"],
                "cpu_high_share": high["share"], "cpu_high_longest": high["longest"],
                "high_iops_minutes": iops_high["minutes"], "high_io_minutes": io_high["minutes"],
                "high_lat_minutes": lat_high["minutes"], "cpu_lat_minutes": co,
                "cpu_lat_common_minutes": common, "peer_ratio": skew, "peer_minutes": len(common_bins),
                "processor": busiest[0] if busiest else "无有效标量数据",
                "processor_avg": processor_avg / 100 if processor_avg is not None else None,
                "processor_peak": processor_peak / 100 if processor_peak is not None else None,
                "consistency": consistency, "status": "VALID" if cpu else "INVALID",
                "notes": (
                    f"CPU>{cfg['cpu_watch_pct']}/{cfg['cpu_high_pct']}%；高延迟>{cfg['latency_watch_ms']}ms。"
                    "processor_busy 为 PA 标量 busy/elapsed；未校准平台处理域与轮询占用，单项100%不据此判CPU瓶颈。"
                    "处理域数组另见能力矩阵；节点偏斜仅对已收节点共同1分钟窗口。"
                ),
            }
            rows.append(row)
            self.node_results[base["resource_id"]] = row
            if high["minutes"]:
                self.evidence("节点", base["cluster"], node, "CPU 高负载",
                              f"CPU>{cfg['cpu_high_pct']}% {high['minutes']:.1f}分钟；最长{high['longest']:.1f}分钟；与高延迟重叠{co:.1f}分钟",
                              "可能存在处理域或工作负载热点，尚不能确认 CPU 是延迟根因",
                              "结合 processor、协议操作结构与 AIQUM 同窗口核验", node)
            if consistency.startswith("system 为零"):
                self.evidence("数据一致性", base["cluster"], node, consistency,
                              f"system IOPS=0；CPU={base['cpu_avg']:.1%}；{len(backend)}个aggr有IOPS",
                              "所选 system counter 口径或路径未反映后端负载",
                              "不能判空闲；与同窗口 AIQUM 节点统计校准", node)
        self.add("节点持续负载", "节点持续负载",
                 "持续时间依据原生区间均值。P95 为按时长加权的区间值分位数。CPU 不是所有物理核心的算术均值。",
                 IDENTITY + [
                     ("cpu_avg", "平均 CPU", 14, P), ("cpu_p95", "CPU 区间 P95", 17, P),
                     ("cpu_watch_minutes", "CPU 关注 分钟", 18, N), ("cpu_watch_share", "CPU 关注 占比", 18, P),
                     ("cpu_watch_longest", "关注最长 分钟", 18, N), ("cpu_high_minutes", "CPU 高负载 分钟", 19, N),
                     ("cpu_high_share", "CPU 高负载 占比", 19, P), ("cpu_high_longest", "高负载最长 分钟", 19, N),
                     ("high_iops_minutes", "高 IOPS 分钟", 18, N), ("high_io_minutes", "高吞吐 分钟", 18, N),
                     ("high_lat_minutes", "高延迟 分钟", 18, N), ("cpu_lat_minutes", "高 CPU + 延迟 分钟", 22, N),
                     ("cpu_lat_common_minutes", "CPU/延迟共同 分钟", 23, N),
                     ("peer_ratio", "本节点/同集群均值", 23, "0.00"),
                     ("peer_minutes", "节点比较共同 分钟", 23, N),
                     ("processor", "平均最忙 processor", 27), ("processor_avg", "该 processor 平均", 23, P),
                     ("processor_peak", "该 processor 峰值", 23, P),
                     ("consistency", "system 一致性", 35),
                 ] + END, sorted(rows, key=lambda r: -(r["cpu_high_minutes"] or 0)), 2)

    def volume_series(self, base):
        if base.get("style") == "FlexGroup" or base.get("parent_id"):
            parent_id = base.get("parent_id", base["resource_id"])
            if parent_id not in self.fg_alignment_cache:
                members = [r for r in self.base_sheets["FlexGroup"]["rows"] if r["parent_id"] == parent_id]
                member_bins = {
                    r["resource_id"]: {m: bin_intervals(self.get(r["node"], "volume", r["volume"], m, r["svm"]))
                                       for m in VOLUME_METRICS} for r in members
                }
                common = set.intersection(*(set(b[m]) for b in member_bins.values() for m in VOLUME_METRICS))
                self.fg_alignment_cache[parent_id] = (members, member_bins, common)
            all_members, member_bins, common = self.fg_alignment_cache[parent_id]
            members = all_members if not base.get("parent_id") else [base]
            binned = [member_bins[r["resource_id"]] for r in members]
            result = {
                m: [{**x, "a": x["start"], "b": x["end"], "seconds": 300, "period_ms": 300000,
                     "base_counter": "ops" if "latency" in m else None}
                    for x in aggregate_bins([b[m] for b in binned], common)] for m in VOLUME_METRICS
            }
            for extra in ("other_ops", "other_latency"):
                bins = [bin_intervals(self.get(r["node"], "volume", r["volume"], extra, r["svm"])) for r in members]
                times = common & set.intersection(*(set(b) for b in bins))
                result[extra] = [
                    {**x, "a": x["start"], "b": x["end"], "seconds": 300, "period_ms": 300000,
                     "base_counter": "other_ops" if extra == "other_latency" else None}
                    for x in aggregate_bins(bins, times)
                ]
            return result
        return {
            m: self.get(base["node"], "volume", base["volume"], m, base["svm"])
            for m in (*VOLUME_METRICS, "other_ops", "other_latency")
        }

    def volume_analysis(self):
        rows = []
        # User-facing logical volumes plus separately identifiable constituents.
        resources = self.base_sheets["Volume"]["rows"] + [
            {**r, "style": "FlexGroup成员"} for r in self.base_sheets["FlexGroup"]["rows"]
        ]
        for base in resources:
            node = base["node"] if base["node"] in self.nodes else next(
                n["node"] for n in self.batch if n["cluster"] == base["cluster"])
            cfg = self.cfg(node)
            src = self.volume_series(base)
            io = combine_compatible(src["read_mib_s"], src["write_mib_s"])
            high = above(src["latency_ms"], cfg["latency_watch_ms"])
            co_iops, share_iops, common_iops = simultaneous(src["latency_ms"], src["iops"], cfg["latency_watch_ms"], cfg["iops_high"])
            co_io, share_io, common_io = simultaneous(src["latency_ms"], io, cfg["latency_watch_ms"], cfg["throughput_high_mib_s"])
            # Read/write/other shares and IO sizes use one common 5m accounting window.
            bins = {m: bin_intervals(src[m]) for m in ("iops", "read_iops", "write_iops", "read_mib_s", "write_mib_s", "other_ops")}
            common = set.intersection(*(set(b) for b in bins.values()))
            totals = {m: sum(b[t]["numerator"] for t in common) for m, b in bins.items()}
            rate = {m: totals[m] * bins[m][next(iter(common))]["scale"] / (300 * len(common)) if common else None for m in bins}
            ops_ok = common and abs(totals["read_iops"] + totals["write_iops"] + totals["other_ops"] - totals["iops"]) <= max(1, totals["iops"] * .001)
            shares = {
                m: totals[m] / totals["iops"] if ops_ok and totals["iops"] else None
                for m in ("read_iops", "write_iops", "other_ops")
            }
            read_size = totals["read_mib_s"] / totals["read_iops"] / 1024 if common and totals["read_iops"] else None
            write_size = totals["write_mib_s"] / totals["write_iops"] / 1024 if common and totals["write_iops"] else None
            # Both counters belong to the volume WAFL layer; kernel/opm phases are
            # explicitly coarsened using the unchanged V1 alignment, not interpolated IOs.
            row = {
                **base, "read_iops": rate["read_iops"], "write_iops": rate["write_iops"],
                "read_io": rate["read_mib_s"], "write_io": rate["write_mib_s"],
                "other_iops": rate["other_ops"], "read_share": shares["read_iops"],
                "write_share": shares["write_iops"], "other_share": shares["other_ops"],
                "read_size": read_size, "write_size": write_size, "structure_minutes": len(common) * 5,
                "other_lat": safe_mean(src["other_latency"]),
                "lat_p95": p95(src["latency_ms"]), "high_minutes": high["minutes"],
                "longest": high["longest"], "high_share": high["share"],
                "lat10_minutes": above(src["latency_ms"], cfg["latency_high_ms"])["minutes"],
                "lat20_minutes": above(src["latency_ms"], cfg["latency_severe_ms"])["minutes"],
                "iops_high_minutes": above(src["iops"], cfg["iops_high"])["minutes"],
                "io_high_minutes": above(io, cfg["throughput_high_mib_s"])["minutes"],
                "co_iops_minutes": co_iops, "co_iops_share": share_iops,
                "co_io_minutes": co_io, "co_io_share": share_io,
                "co_iops_common": common_iops, "co_io_common": common_io,
                "status": "VALID" if ops_ok else "PARTIAL",
                "notes": (
                    "操作结构为同层5分钟共同格；IO Size=同格字节增量/对应读写操作增量/1024。"
                    + ("Read+Write+Other与Total未满足一致性，份额不计算。" if not ops_ok else "")
                    + ("零操作不计算份额或IO Size。" if not totals.get("iops") else "")
                ),
            }
            rows.append(row)
            self.vol_results[base["resource_id"]] = row
        self.add("卷操作结构", "卷操作结构与持续高延迟",
                 "WAFL 层延迟，不是客户端 NFS 端到端响应。Other 不等于元数据。成员卷以卷类型区分，不重复进入逻辑卷热点排名。",
                 VOL_ID + [
                     ("style", "卷类型", 19), ("read_iops", "Read IOPS", 16, N),
                     ("write_iops", "Write IOPS", 16, N), ("other_iops", "Other IOPS", 16, N),
                     ("read_share", "Read 操作占比", 18, P), ("write_share", "Write 操作占比", 18, P),
                     ("other_share", "Other 操作占比", 18, P),
                     ("read_io", "结构窗口读 MiB/s", 23, N), ("write_io", "结构窗口写 MiB/s", 23, N),
                     ("read_size", "平均 Read IO KiB", 22, N), ("write_size", "平均 Write IO KiB", 22, N),
                     ("structure_minutes", "结构共同 分钟", 19, N),
                     ("read_lat", "Read 延迟 ms", 19, M), ("write_lat", "Write 延迟 ms", 19, M),
                     ("other_lat", "Other 延迟 ms", 19, M), ("lat_p95", "区间延迟 P95 ms", 22, M),
                     ("high_minutes", "高延迟 分钟", 18, N), ("longest", "高延迟最长 分钟", 22, N),
                     ("high_share", "高延迟占比", 18, P),
                     ("lat10_minutes", "较高延迟 分钟", 20, N), ("lat20_minutes", "严重延迟 分钟", 20, N),
                     ("co_iops_minutes", "高延迟+IOPS 分钟", 23, N),
                     ("co_iops_share", "高延迟+IOPS 占比", 23, P),
                     ("co_io_minutes", "高延迟+吞吐 分钟", 23, N), ("co_io_share", "高延迟+吞吐 占比", 23, P),
                     ("co_iops_common", "延迟/IOPS共同 分钟", 24, N),
                     ("co_io_common", "延迟/吞吐共同 分钟", 24, N),
                     ("node", "节点", 24),
                 ] + END, rows)

    def hotspots(self):
        rows = []
        logical = [self.vol_results[r["resource_id"]] for r in self.base_sheets["Volume"]["rows"] if r["style"] != "节点根卷"]
        clusters = sorted({r["cluster"] for r in logical})
        for scope in ["本Site", *clusters]:
            group = logical if scope == "本Site" else [r for r in logical if r["cluster"] == scope]
            for dimension, key in [("IOPS", "iops_avg"), ("吞吐", "io_avg"), ("延迟", "lat_avg")]:
                candidates = [r for r in group if isinstance(r[key], (int, float))]
                top_n = self.config.get("top_n", DEFAULTS["top_n"]) if scope == "本Site" else thresholds(self.config, scope)["top_n"]
                for rank, r in enumerate(sorted(candidates, key=lambda r: (-r[key], r["resource_id"]))[:top_n], 1):
                    high_load = bool(r["co_iops_minutes"] or r["co_io_minutes"])
                    label = "高延迟且高负载" if high_load else "低负载高延迟观察" if r["high_minutes"] else "排名观察"
                    rows.append({**r, "scope": scope, "dimension": dimension, "rank": rank, "category": label})
        self.add("热点卷分析", "逻辑卷热点 Top N",
                 "分别提供 Site 与每集群 IOPS、吞吐、延迟排名；排名不等于故障。跨 Site 集团总榜由同一批报告汇总生成。",
                 [("scope", "排名范围", 25), ("dimension", "排名维度", 14), ("rank", "排名", 10, INT)] + VOL_ID + [
                     ("category", "观察分类", 27), ("iops_avg", "平均 IOPS", 17, N), ("io_avg", "平均 MiB/s", 17, N),
                     ("lat_avg", "WAFL 平均延迟 ms", 24, M), ("high_minutes", "高延迟 分钟", 18, N),
                     ("co_iops_minutes", "高延迟+高IOPS 分钟", 25, N),
                     ("co_io_minutes", "高延迟+高吞吐 分钟", 25, N),
                 ], rows, 3)

    def flexgroups(self):
        rows, node_rows = [], []
        for parent in self.base_sheets["Volume"]["rows"]:
            if parent["style"] != "FlexGroup":
                continue
            members = [m for m in self.base_sheets["FlexGroup"]["rows"] if m["parent_id"] == parent["resource_id"]]
            cfg = self.cfg(members[0]["node"])
            bins = {
                m["resource_id"]: {
                    metric: bin_intervals(self.get(m["node"], "volume", m["volume"], metric, m["svm"]))
                    for metric in VOLUME_METRICS
                } for m in members
            }
            common = set.intersection(*(set(v) for b in bins.values() for v in b.values()))
            values = [m["iops_avg"] for m in members]
            ios = [m["io_avg"] for m in members]
            sufficient = (
                len(members) >= cfg["fg_min_members"] and parent["coverage"] >= cfg["minimum_coverage"]
                and len(common) * 5 >= cfg["fg_min_common_minutes"]
            )
            load_ok = (parent["iops_avg"] or 0) >= cfg["fg_min_iops"] or (parent["io_avg"] or 0) >= cfg["fg_min_mib_s"]
            observed_complete = bool(common) and all(isinstance(v, (float, int)) for v in values + ios)
            hot_iops = max(members, key=lambda m: m["iops_avg"] or 0)
            hot_io = max(members, key=lambda m: m["io_avg"] or 0)
            slow = max(members, key=lambda m: m["lat_avg"] or 0)
            peak_slow = max(members, key=lambda m: m["lat_max"] or 0)
            slow_duration = max(members, key=lambda m: self.vol_results[m["resource_id"]]["high_minutes"])
            node_groups = collections.defaultdict(list)
            for m in members:
                node_groups[m["node"]].append(m)
            time_hot, skew_times, node_skew_times, highload_skew = [], [], [], []
            highload_ratios, ordinary_ratios = [], []
            slow_counts = collections.Counter()
            local_low_total = 0
            for t in sorted(common):
                member_iops = {m["resource_id"]: bins[m["resource_id"]]["iops"][t]["numerator"] / 300 for m in members}
                member_io = {
                    m["resource_id"]: (bins[m["resource_id"]]["read_mib_s"][t]["numerator"] + bins[m["resource_id"]]["write_mib_s"][t]["numerator"]) / 300 / 2**20
                    for m in members
                }
                total_iops, total_io = sum(member_iops.values()), sum(member_io.values())
                active = total_iops >= cfg["fg_min_iops"] or total_io >= cfg["fg_min_mib_s"]
                if active:
                    hot = max(member_iops, key=member_iops.get) if total_iops >= cfg["fg_min_iops"] else max(member_io, key=member_io.get)
                    time_hot.append((t, hot))
                    ratio = max(member_iops.values()) / (total_iops / len(members)) if total_iops else 0
                    io_ratio = max(member_io.values()) / (total_io / len(members)) if total_io else 0
                    target_ratios = highload_ratios if total_iops > cfg["iops_high"] or total_io > cfg["throughput_high_mib_s"] else ordinary_ratios
                    target_ratios.append(max(ratio, io_ratio))
                    if ratio > cfg["fg_skew_ratio"] or io_ratio > cfg["fg_skew_ratio"]:
                        skew_times.append({"a": t, "b": t + 300})
                        if total_iops > cfg["iops_high"] or total_io > cfg["throughput_high_mib_s"]:
                            highload_skew.append(t)
                    per_member_node = [sum(member_iops[m["resource_id"]] for m in ms) / len(ms) for ms in node_groups.values()]
                    per_member_io = [sum(member_io[m["resource_id"]] for m in ms) / len(ms) for ms in node_groups.values()]
                    if len(per_member_node) > 1 and (
                        (statistics.mean(per_member_node) > 0 and max(per_member_node) / statistics.mean(per_member_node) > cfg["fg_skew_ratio"])
                        or (statistics.mean(per_member_io) > 0 and max(per_member_io) / statistics.mean(per_member_io) > cfg["fg_skew_ratio"])
                    ):
                        node_skew_times.append({"a": t, "b": t + 300})
                if total_iops < cfg["iops_high"] and total_io < cfg["throughput_high_mib_s"] and any(
                    member_iops[k] > cfg["iops_high"] / len(members) or member_io[k] > cfg["throughput_high_mib_s"] / len(members)
                    for k in member_iops
                ):
                    local_low_total += 1
                for m in members:
                    item = bins[m["resource_id"]]["latency_ms"][t]
                    lat = item["numerator"] / item["denominator"] * item["scale"] if item["denominator"] else None
                    if lat is not None and lat > cfg["latency_watch_ms"]:
                        slow_counts[m["volume"]] += 1
            counts = collections.Counter(h for _, h in time_hot)
            dominant_id, dominant_count = counts.most_common(1)[0] if counts else (None, 0)
            dominant = next((m["volume"] for m in members if m["resource_id"] == dominant_id), None)
            switches = sum(
                a[1] != b[1] for a, b in zip(time_hot, time_hot[1:]) if b[0] - a[0] == 300
            )
            busiest_node = max(node_groups, key=lambda n: sum(m["iops_avg"] or 0 for m in node_groups[n]))
            avg_iops = sum(v for v in values if v is not None) / len(members) if observed_complete else None
            avg_io = sum(v for v in ios if v is not None) / len(members) if observed_complete else None
            iops_ratio = max(values) / avg_iops if avg_iops else None
            io_ratio = max(ios) / avg_io if avg_io else None
            risk = "持续偏斜观察" if sufficient and load_ok and skew_times else "未触发偏斜规则" if sufficient and load_ok else "条件不足，不判断偏斜"
            for node, ms in sorted(node_groups.items()):
                node_rows.append({
                    **parent, "node": node, "count": len(ms),
                    "node_iops": sum(m["iops_avg"] or 0 for m in ms) if observed_complete else None,
                    "node_io": sum(m["io_avg"] or 0 for m in ms) if observed_complete else None,
                    "share_iops": sum(m["iops_avg"] or 0 for m in ms) / parent["iops_avg"] if parent["iops_avg"] else None,
                    "share_io": sum(m["io_avg"] or 0 for m in ms) / parent["io_avg"] if parent["io_avg"] else None,
                    "per_member_iops": sum(m["iops_avg"] or 0 for m in ms) / len(ms) if observed_complete else None,
                    "per_member_io": sum(m["io_avg"] or 0 for m in ms) / len(ms) if observed_complete else None,
                    "normalized_iops": sum(m["iops_avg"] or 0 for m in ms) / len(ms) / avg_iops if avg_iops else None,
                    "normalized_io": sum(m["io_avg"] or 0 for m in ms) / len(ms) / avg_io if avg_io else None,
                })
            peer_latencies = [m["lat_avg"] for m in members if m is not slow and m["lat_avg"] is not None and (m["iops_avg"] or 0) >= cfg["fg_min_iops"] / len(members)]
            peer_median = statistics.median(peer_latencies) if peer_latencies else None
            slow_ratio = slow["lat_avg"] / peer_median if peer_median and slow["lat_avg"] is not None else None
            row = {
                **parent, "valid_members": len(members) if observed_complete else 0,
                "hot_iops": hot_iops["volume"], "hot_iops_share": hot_iops["iops_avg"] / parent["iops_avg"] if parent["iops_avg"] else None,
                "hot_io": hot_io["volume"], "hot_io_share": hot_io["io_avg"] / parent["io_avg"] if parent["io_avg"] else None,
                "iops_ratio": iops_ratio, "io_ratio": io_ratio,
                "iops_cv": statistics.pstdev(values) / avg_iops if avg_iops else None,
                "io_cv": statistics.pstdev(ios) / avg_io if avg_io else None,
                "busiest_node": busiest_node,
                "busiest_node_share": sum(m["iops_avg"] or 0 for m in node_groups[busiest_node]) / parent["iops_avg"] if parent["iops_avg"] else None,
                "slow": slow["volume"], "slow_latency": slow["lat_avg"],
                "peak_slow": peak_slow["volume"], "peak_latency": peak_slow["lat_max"],
                "duration_slow": slow_duration["volume"],
                "slow_minutes": self.vol_results[slow_duration["resource_id"]]["high_minutes"],
                "slow_peer_ratio": slow_ratio,
                "highload_slow": ",".join(m["volume"] for m in members if self.vol_results[m["resource_id"]]["co_iops_minutes"] or self.vol_results[m["resource_id"]]["co_io_minutes"]) or "未触发",
                "slow_node_cpu": self.node_results[next(r["resource_id"] for r in self.base_sheets["Node"]["rows"] if r["node"] == slow["node"])]["cpu_avg"],
                "dominant": dominant, "dominant_share": dominant_count / len(time_hot) if time_hot else None,
                "switches": switches if len(time_hot) >= 2 else None,
                "skew_minutes": len(skew_times) * 5 if sufficient and load_ok else None,
                "skew_longest": longest(skew_times) / 60 if sufficient and load_ok else None,
                "node_skew_minutes": len(node_skew_times) * 5 if sufficient and load_ok else None,
                "highload_skew_minutes": len(highload_skew) * 5 if sufficient and load_ok else None,
                "highload_skew_ratio": statistics.mean(highload_ratios) if sufficient and highload_ratios else None,
                "ordinary_skew_ratio": statistics.mean(ordinary_ratios) if sufficient and ordinary_ratios else None,
                "local_minutes": local_low_total * 5 if sufficient else None,
                "persistent_slow": ",".join(f"{n}({c * 5}分钟)" for n, c in slow_counts.most_common(3)) or "未触发",
                "slow_relative_flag": (
                    "相对慢成员观察" if sufficient and slow_ratio is not None
                    and slow_ratio > cfg["fg_slow_ratio"]
                    and (slow["lat_avg"] or 0) > cfg["latency_watch_ms"]
                    else "未触发" if sufficient else "共同窗口不足"
                ),
                "status": "VALID" if sufficient else "PARTIAL", "risk": risk,
                "notes": "成员数为实收，不宣称完整配置。偏斜需足够负载、成员与覆盖。热点迁移仅代表本窗口。慢成员比值对其他活跃成员延迟中位数，不作吞吐均衡告警。",
            }
            rows.append(row)
            self.fg_risks[parent["resource_id"]] = row
            if risk == "持续偏斜观察":
                self.evidence("FlexGroup", parent["cluster"], parent["volume"], risk,
                              f"偏斜{row['skew_minutes']:.1f}分钟；IOPS最热={row['hot_iops']}({row['hot_iops_share'] or 0:.1%})、CV={row['iops_cv'] or 0:.2f}；吞吐最热={row['hot_io']}({row['hot_io_share'] or 0:.1%})、CV={row['io_cv'] or 0:.2f}",
                              "可能为访问集中或成员放置差异，不等于容量不均或节点故障",
                              "对照节点归一化负载、慢成员及同窗口CPU；必要时关联SAM容量配置")
        self.add("FlexGroup 深度分析", "FlexGroup 成员热点与持续偏斜",
                 "沿用原报告共同5分钟窗口。成员配置完整性未知时，仅评价已收成员；低总负载和覆盖不足不告警。",
                 VOL_ID + [
                     ("risk", "观察结论", 29), ("iops_avg", "大卷平均 IOPS", 20, N),
                     ("io_avg", "大卷平均 MiB/s", 20, N), ("members", "实收成员数", 16, INT),
                     ("valid_members", "共同有效成员数", 20, INT), ("valid_minutes", "共同有效 分钟", 20, N),
                     ("coverage", "共同覆盖", 15, P),
                     ("hot_iops", "最热 IOPS 成员", 34), ("hot_iops_share", "最热 IOPS 占比", 21, P),
                     ("hot_io", "最热吞吐成员", 34), ("hot_io_share", "最热吞吐占比", 20, P),
                     ("iops_ratio", "最大/平均 IOPS", 21, "0.00"), ("io_ratio", "最大/平均吞吐", 21, "0.00"),
                     ("iops_cv", "成员 IOPS CV", 19, "0.00"), ("io_cv", "成员吞吐 CV", 19, "0.00"),
                     ("busiest_node", "最忙 IOPS 节点", 27), ("busiest_node_share", "该节点 IOPS 占比", 23, P),
                     ("skew_minutes", "成员偏斜 分钟", 20, N), ("skew_longest", "成员偏斜最长 分钟", 24, N),
                     ("node_skew_minutes", "节点归一偏斜 分钟", 24, N),
                     ("highload_skew_minutes", "大卷高负载时偏斜 分钟", 28, N),
                     ("highload_skew_ratio", "高负载格平均 max/mean", 29, "0.00"),
                     ("ordinary_skew_ratio", "其他活跃格平均 max/mean", 30, "0.00"),
                     ("dominant", "窗口常驻热点成员", 34), ("dominant_share", "活跃格热点占比", 22, P),
                     ("switches", "相邻格热点迁移次数", 25, INT),
                     ("slow", "平均最慢成员", 34), ("slow_latency", "该成员平均 ms", 20, M),
                     ("peak_slow", "峰值最慢成员", 34), ("peak_latency", "该成员区间峰值 ms", 25, M),
                     ("duration_slow", "高延迟最长成员", 34), ("slow_minutes", "该成员高延迟 分钟", 25, N),
                     ("slow_peer_ratio", "最慢/其他活跃延迟中位数", 30, "0.00"),
                     ("slow_relative_flag", "慢成员相对观察", 25),
                     ("slow_node_cpu", "平均最慢成员所在节点CPU", 30, P),
                     ("highload_slow", "高负载且高延迟成员", 40),
                     ("persistent_slow", "窗口高延迟成员", 48),
                     ("local_minutes", "低于大卷高负载阈值但有相对热点 分钟", 42, N),
                 ] + END, rows)
        self.add("FlexGroup 节点分布", "FlexGroup 节点负载归一化",
                 "负载合计与每成员平均同时展示；不能仅凭节点总负载判断均衡。仅使用原共同窗口。",
                 VOL_ID + [
                     ("node", "承载节点", 26), ("count", "本节点成员数", 19, INT),
                     ("node_iops", "节点成员合计 IOPS", 24, N), ("node_io", "节点成员合计 MiB/s", 25, N),
                     ("share_iops", "大卷 IOPS 占比", 21, P), ("share_io", "大卷吞吐占比", 20, P),
                     ("per_member_iops", "每成员平均 IOPS", 22, N), ("per_member_io", "每成员平均 MiB/s", 23, N),
                     ("normalized_iops", "每成员IOPS/全组每成员", 29, "0.00"),
                     ("normalized_io", "每成员吞吐/全组每成员", 29, "0.00"),
                     ("valid_minutes", "共同 分钟", 17, N),
                 ], node_rows)

    def network(self):
        rows, error_rows = [], []
        for base in self.base_sheets["Network Port"]["rows"]:
            node, port = base["node"], base["port"]
            cfg = self.cfg(node)
            rx = self.get(node, "nic_common", port, "rx_mib_s")
            tx = self.get(node, "nic_common", port, "tx_mib_s")
            rx_bins, tx_bins = bin_intervals(rx), bin_intervals(tx)
            links = [r for r in self.nodes[node]["port_link_samples"] if r["port"] == port]
            util = port_utilization(rx_bins, tx_bins, links)
            directions = {
                d: [{"a": r["timestamp"], "b": r["timestamp"] + 300, "value": r[d]} for r in util]
                for d in ("rx", "tx")
            }
            role = self.config.get("port_roles", {}).get(self.model["customer"], {}).get(node, {}).get(port, {})
            typ = next((i.get("nic_type") for sup in self.supplements if sup["manifest"]["node"] == node
                        for i in sup["manifest"]["identities"] if i["object"] == "nic_common" and i["resource"] == port), None)
            row = {**base, "role": role.get("role", "未知"), "role_source": role.get("source", "未提供可验证配置"),
                   "nic_type": typ or "未知", "status": "VALID" if util else "PARTIAL",
                   "notes": "按每端口展示，不把物理端口与逻辑IFGRP相加；速度未知/变化的格不计算利用率。"}
            for d, src in [("rx", rx), ("tx", tx)]:
                watch, high = above(directions[d], cfg["network_watch"]), above(directions[d], cfg["network_high"])
                row.update({
                    f"{d}_peak": max((r["value"] for r in valid(src)), default=None),
                    f"{d}_util_peak": max((r["value"] for r in directions[d]), default=None),
                    f"{d}_watch": watch["minutes"], f"{d}_high": high["minutes"],
                    f"{d}_longest": high["longest"], f"{d}_watch_longest": watch["longest"],
                })
            rows.append(row)
            self.port_results[base["resource_id"]] = row
            for key, src in self.series.items():
                if key[0] != node or key[1] != "nic_common" or key[3] != port or key[4] in ("rx_mib_s", "tx_mib_s", "throughput_mib_s"):
                    continue
                if key in self.base_keys:
                    continue
                error_rows.append({
                    "cluster": base["cluster"], "node": node, "port": port, "counter": key[4],
                    "delta": sum(r["numerator"] for r in src) if src else None,
                    "rate": safe_mean(src), "minutes": span(src) / 60,
                    "coverage": span(src) / ((base["window_end"] - base["window_start"]) / 1000),
                    "status": "VALID" if src else "INVALID", "notes": "独立错误类型；不能跨类型相加；负差分被剔除并记录于数据质量。",
                })
            if row["rx_high"] or row["tx_high"]:
                self.evidence("网络", base["cluster"], port, "单向带宽高利用率",
                              f"RX高利用{row['rx_high']:.1f}分钟；TX高利用{row['tx_high']:.1f}分钟",
                              "可能存在链路方向性压力；RX+TX合计不能代表单向饱和",
                              "确认端口用途、IFGRP物理关系、交换机同窗口错误及拥塞", node)
        self.add("网络深度分析", "网络 RX/TX 方向性压力",
                 "全程 down 端口沿用原表排除规则。RX、TX独立与70%/85%阈值比较，不使用合计比例判断饱和。",
                 IDENTITY + [("port", "端口", 14), ("role", "用途", 20), ("nic_type", "PA nic_type", 24),
                             ("role_source", "用途证据来源", 35)] + [
                     ("rx_io", "平均 RX MiB/s", 20, N), ("rx_peak", "原生峰值 RX MiB/s", 25, N),
                     ("tx_io", "平均 TX MiB/s", 20, N), ("tx_peak", "原生峰值 TX MiB/s", 25, N),
                     ("rx_util", "平均 RX 利用率", 21, P), ("rx_util_peak", "峰值 RX 利用率", 21, P),
                     ("tx_util", "平均 TX 利用率", 21, P), ("tx_util_peak", "峰值 TX 利用率", 21, P),
                     ("rx_watch", "RX 关注 分钟", 19, N), ("rx_high", "RX 高利用 分钟", 21, N),
                     ("rx_watch_longest", "RX 关注最长 分钟", 23, N), ("rx_longest", "RX 高利用最长 分钟", 25, N),
                     ("tx_watch", "TX 关注 分钟", 19, N), ("tx_high", "TX 高利用 分钟", 21, N),
                     ("tx_watch_longest", "TX 关注最长 分钟", 23, N), ("tx_longest", "TX 高利用最长 分钟", 25, N),
                     ("util_minutes", "速率有效 分钟", 20, N),
                 ] + END, rows)
        if not error_rows:
            error_rows = [{"status": "UNSUPPORTED", "notes": "本批没有可计算网络错误差分，见能力矩阵；缺失不是零。"}]
        self.add("网络错误明细", "网络错误独立计数",
                 "每行一个端口、一个counter；错误总量、CRC、丢弃等可能重叠，不生成混合错误总数。",
                 IDENTITY + [
                     ("port", "端口", 15), ("counter", "原始 Counter", 34), ("delta", "有效区间增量", 20, INT),
                     ("rate", "有效区间平均 /s", 23, "0.000000"), ("minutes", "有效 分钟", 18, N),
                     ("coverage", "覆盖率", 16, P),
                 ] + END, error_rows)

    def protocol(self):
        rows = []
        instances = sorted({key[:4] for key in self.series if key[1] == "nfsv3"})
        for node, obj, svm, resource in instances:
            sources = {op: self.get(node, obj, resource, op + "_total", svm) for op in NFS_OPERATIONS}
            total = self.get(node, obj, resource, "nfsv3_ops", svm)
            bins = {op: bin_intervals(src) for op, src in sources.items()}
            total_bins = bin_intervals(total)
            common = set(total_bins)
            for b in bins.values():
                common &= set(b)
            total_delta = sum(total_bins[t]["numerator"] for t in common)
            metadata_delta = sum(
                bins[op][t]["numerator"] for op in NFS_OPERATIONS if op not in ("read", "write")
                for t in common
            )
            rows.append({
                "cluster": self.nodes[node]["cluster"], "node": node, "svm": svm,
                "instance": resource, "grain": "Node×SVM实例" if svm else "Node×协议实例，SVM未确认",
                "operation": "已列元数据操作合计", "category": "元数据/目录汇总",
                "rate": metadata_delta / (len(common) * 300) if common else None,
                "share": metadata_delta / total_delta if total_delta else None,
                "minutes": len(common) * 5, "common_minutes": len(common) * 5,
                "status": "VALID" if common else "PARTIAL",
                "notes": "仅合计下列明确元数据/目录操作；不包括未列出的其他协议操作。总操作份额分母为NFSv3全部操作。",
            })
            for op, src in sources.items():
                aligned_delta = sum(bins[op][t]["numerator"] for t in common)
                latency = self.get(node, obj, resource, op + "_avg_latency", svm)
                rows.append({
                    "cluster": self.nodes[node]["cluster"], "node": node, "svm": svm,
                    "instance": resource, "grain": "Node×SVM实例" if svm else "Node×协议实例，SVM未确认",
                    "operation": op.upper(), "category": "数据" if op in ("read", "write") else "元数据/目录",
                    "rate": safe_mean(src), "share": aligned_delta / total_delta if total_delta else None,
                    "latency": safe_mean(latency), "minutes": span(src) / 60, "common_minutes": len(common) * 5,
                    "status": "VALID" if src else "UNSUPPORTED",
                    "notes": "份额分母为NFSv3全部操作（并非仅列出的操作）；不归因到单卷。延迟为协议层，不能与WAFL相加。" if src else "本实例无可计算counter；不能补零。",
                })
        if not rows:
            rows = [{"status": "UNSUPPORTED", "notes": "本批无可计算 NFSv3 标量样本；其他协议见能力矩阵。"}]
        self.add("协议与元数据", "NFSv3 操作结构",
                 "仅用真实NFSv3 counter，不以Volume Other替代元数据。若全部为零，不据此推断业务空闲或主协议；其他协议见能力审计。",
                 IDENTITY + [
                     ("svm", "SVM", 25), ("instance", "PA 协议实例", 34), ("grain", "统计粒度", 32),
                     ("operation", "NFSv3 操作", 20), ("category", "操作类别", 19),
                     ("rate", "平均操作 /s", 19, N), ("share", "共同窗口总操作占比", 27, P),
                     ("latency", "协议层平均延迟 ms", 26, M),
                     ("minutes", "操作有效 分钟", 20, N), ("common_minutes", "份额共同 分钟", 20, N),
                 ] + END, rows)

    def fabricpool(self):
        rows, inactive = [], {}
        for key, src in sorted(self.series.items()):
            node, obj, svm, resource, metric = key
            if not (metric.startswith("cloud_io_") or obj == "wafl_cloud_io"):
                continue
            item = {
                "cluster": self.nodes[node]["cluster"], "node": node, "object": obj,
                "instance": resource, "metric": metric,
                "value": safe_mean(src), "unit": src[0].get("unit", "") if src else "",
                "delta": sum(r["numerator"] for r in src) if src and not src[0]["base_counter"] else None,
                "minutes": span(src) / 60, "status": "VALID" if src else "INVALID",
                "category": "云端操作延迟" if metric.endswith("_latency") else "云端请求/重试/状态",
                "notes": "未关联卷UUID/分层策略；实例原样展示。云端IO不等于下沉量或回迁量；不可推断物理释放容量。",
            }
            if item["value"] not in (None, 0):
                rows.append(item)
            else:
                k = (node, obj, metric)
                summary = inactive.setdefault(k, {
                    **item, "instance": "零值/无操作实例汇总",
                    "inactive_count": 0, "zero_count": 0, "undefined_count": 0,
                })
                summary["inactive_count"] += 1
                summary["zero_count"] += item["value"] == 0
                summary["undefined_count"] += item["value"] is None
                summary["value"] = 0 if summary["zero_count"] == summary["inactive_count"] else None
                summary["delta"] = 0 if summary["zero_count"] == summary["inactive_count"] else None
                summary["status"] = "VALID" if not summary["undefined_count"] else "PARTIAL"
                summary["notes"] = (
                    f"合并{summary['inactive_count']}个实例：{summary['zero_count']}个有效零值，"
                    f"{summary['undefined_count']}个无操作/无数值。未把无操作延迟补零；完整实例记录保存在历史库。"
                )
        rows.extend(inactive.values())
        if not rows:
            rows = [{"status": "UNSUPPORTED", "notes": "本批没有可计算云端差分；是否配置 FabricPool 仍需同期配置资料。"}]
        self.add("FabricPool 观察", "云端请求与后台 IO 观察",
                 "请求、延迟、失败和重试分行保留，不混合为tiering数据量。SAM关联接口保留在配置中，未提供策略时不推断。",
                 IDENTITY + [
                     ("object", "PA 对象", 30), ("instance", "PA 实例", 40), ("category", "观测类别", 27),
                     ("metric", "Counter", 40), ("value", "有效区间均值", 21, M),
                     ("unit", "单位", 17), ("delta", "请求类有效增量", 23, INT),
                     ("minutes", "有效 分钟", 18, N),
                     ("inactive_count", "汇总实例数", 19, INT), ("zero_count", "有效零值实例数", 23, INT),
                     ("undefined_count", "无数值实例数", 21, INT),
                 ] + END, rows)

    def bottlenecks(self):
        raw_waits = collections.defaultdict(list)
        for sup in self.supplements:
            for r in sup["raw"]:
                if r["object"].startswith("workload_queue_") and r["unit"] in ("us(raw)", "native(raw)"):
                    raw_waits[(sup["manifest"]["node"], r["object"], r["resource"], r["metric"], r["unit"])].append(r)
            caps = sup["manifest"]["capabilities"]
            waits = [c for c in caps if c["counter"] in WAIT_COUNTERS or "cp" in c["counter"] and "wait" in c["counter"]]
            for c in waits:
                self.evidence(
                    "等待项能力", sup["manifest"]["cluster"], c["object"] + "." + c["counter"],
                    "有定义，未建立可加总延迟口径",
                    f"原始类型={c['ctype']}；base_id={c['base_id']}；标量观测={c['observations']}；单位见原始定义",
                    "不能把定义或累计量直接当平均等待，也不能与WAFL相加",
                    "需要counter原始说明、base与AIQUM/官方同窗值完成单位和可加性校准", sup["manifest"]["node"])
        observed_waits = {c["counter"] for sup in self.supplements for c in sup["manifest"]["capabilities"]}
        for counter in WAIT_COUNTERS:
            if counter not in observed_waits:
                self.evidence("等待项能力", "", counter, "当前样本不存在该counter", "已检查本批所有PA元数据preset",
                              "缺少该等待项不是等待为零", "需要其他性能源或可包含该counter的日志")
        if not self.facts:
            self.evidence("窗口观察", "", "", "未触发当前规则", "本批数据", "不等于无业务风险", "保留后续可比窗口")
        self.add("延迟与瓶颈分析", "性能现象、证据与待验证原因",
                 "候选原因不是根因结论。不同层延迟和等待项不相加。原始定义与解析状态见 Counter 能力矩阵。",
                 [("category", "分析类别", 22), ("cluster", "集群", 24), ("node", "节点", 24),
                  ("resource", "资源 / Counter", 46), ("phenomenon", "观测现象", 42),
                  ("evidence", "支持证据", 65), ("cause", "可能原因 / 限制", 65),
                  ("followup", "仍需验证", 65)], self.facts, 2)
        wait_rows = []
        wait_groups = {}
        for (node, obj, res, metric, unit), samples in sorted(raw_waits.items()):
            # Prefer one native preset/period, avoiding repeated kernel/asup snapshots.
            choices = collections.defaultdict(dict)
            for r in samples:
                choices[(r["group"], r["period_ms"])][r["timestamp_ms"]] = r
            chosen_key = min(choices, key=lambda k: (-len(choices[k]), k[1], k[0]))
            chosen = sorted(choices[chosen_key].values(), key=lambda r: r["timestamp_ms"])
            item = {
                "cluster": self.nodes[node]["cluster"], "node": node, "object": obj, "instance": res,
                "counter": metric, "min": min(r["raw_value"] for r in chosen),
                "max": max(r["raw_value"] for r in chosen), "last": chosen[-1]["raw_value"],
                "unit": unit, "samples": len(chosen), "period": chosen_key[1] / 1000,
                "status": "PARTIAL",
                "notes": "按节点/对象/counter汇总原始观测范围，不是每IO平均等待。实例明细保存在历史库；累计/区间语义和分母待校准，不与WAFL相加。",
            }
            group_key = (node, obj, metric, unit, chosen_key[1])
            if group_key not in wait_groups:
                wait_groups[group_key] = {**item, "instance_count": 1}
            else:
                target = wait_groups[group_key]
                target["instance_count"] += 1
                target["samples"] += item["samples"]
                target["min"] = min(target["min"], item["min"])
                if item["max"] > target["max"]:
                    target["max"], target["instance"] = item["max"], res
                # A multi-instance "last" value has no useful aggregation meaning.
                target["last"] = None
        wait_rows = list(wait_groups.values())
        if wait_rows:
            self.add("等待项原始观测", "等待项原始采样与语义缺口",
                     "按节点/对象/counter汇总原始范围，并标出最大值所在实例。多实例不汇总末值；不做平均延迟或可加总解释。",
                     IDENTITY + [("object", "Object", 31), ("instance", "最大值所在 PA 实例", 85),
                                 ("counter", "等待 Counter", 49), ("instance_count", "原始实例数", 19, INT),
                                 ("min", "原始最小", 22, INT), ("max", "原始最大", 22, INT),
                                 ("last", "原始末值", 22, INT), ("unit", "原始单位", 19),
                                 ("samples", "采样数", 15, INT), ("period", "原生周期 秒", 20, N)] + END,
                     wait_rows, 2)

    def run(self):
        self.node_analysis()
        self.volume_analysis()
        self.hotspots()
        self.flexgroups()
        self.network()
        self.protocol()
        self.fabricpool()
        self.bottlenecks()
        self.data_quality()
        return self.sheets


def combine_compatible(left, right):
    return combine_rate_rows(left, right)


def unit_for(metric):
    if "pct" in metric:
        return "%"
    if "latency_per_block" in metric:
        return "ms/block"
    if "latency" in metric:
        return "ms"
    if "mib_s" in metric:
        return "MiB/s"
    return "ops/s"


NFS_OPERATIONS = ("read", "write", "lookup", "getattr", "readdir", "readdirplus", "create", "remove", "rename", "setattr")
WAIT_COUNTERS = (
    "delay_network_wait_time", "delay_cluster_interconnect_wait_time",
    "delay_disk_io_wait_time", "delay_qos_limit_wait_time",
)
