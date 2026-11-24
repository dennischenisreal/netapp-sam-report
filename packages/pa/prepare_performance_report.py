"""Prepare reader-facing resource rows without changing source PA files."""

import argparse
from bisect import bisect_right
import collections
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).parent
VOLUME_METRICS = (
    "iops", "read_iops", "write_iops", "read_mib_s", "write_mib_s",
    "latency_ms", "read_latency_ms", "write_latency_ms",
)
GRID_SECONDS = 300
REPORT_TZ = timezone(timedelta(hours=8))


def seconds(text):
    return datetime.fromisoformat(text).timestamp()


def summarize(rows):
    available = [r for r in rows if r["value"] is not None]
    numerator = sum(r["numerator"] for r in available)
    denominator = sum(r["denominator"] for r in available)
    return {
        "avg": numerator / denominator * available[0]["scale"] if denominator else None,
        "max": max((r["value"] for r in available), default=None),
        "seconds": sum(r["seconds"] for r in rows),
        "start": min((r["start"] for r in rows), default=None),
        "end": max((r["end"] for r in rows), default=None),
        "period": sorted({r["period_ms"] / 1000 for r in rows}),
    }


def combine_rate_rows(left, right):
    lookup = {(r["start"], r["end"]): r for r in right}
    result = []
    for a in left:
        b = lookup.get((a["start"], a["end"]))
        if b is None or a["value"] is None or b["value"] is None:
            continue
        if a["scale"] != b["scale"] or a["base_counter"] or b["base_counter"]:
            raise ValueError("Only matching rate counters can be added")
        result.append({
            **a, "value": a["value"] + b["value"],
            "numerator": a["numerator"] + b["numerator"],
        })
    return result


def bin_intervals(rows, step=GRID_SECONDS):
    """Rebin interval means by time overlap; never claim finer native resolution."""
    bins = collections.defaultdict(lambda: {
        "numerator": 0.0, "denominator": 0.0, "covered": 0.0,
    })
    for row in rows:
        a, b = row["a"], row["b"]
        if b <= a:
            raise ValueError("Non-positive interval")
        start = math.floor(a / step) * step
        while start < b:
            overlap = max(0, min(b, start + step) - max(a, start))
            if overlap:
                target = bins[start]
                fraction = overlap / (b - a)
                target["numerator"] += row["numerator"] * fraction
                target["denominator"] += row["denominator"] * fraction
                target["covered"] += overlap
                target["scale"] = row["scale"]
                target["is_ratio"] = row["base_counter"] is not None
            start += step
    result = {}
    for timestamp, item in bins.items():
        if item["covered"] > step + 0.001:
            raise ValueError("Overlapping source intervals")
        if item["covered"] >= step - 0.001:
            result[timestamp] = item
    return result


def aggregate_bins(member_bins, timestamps):
    result = []
    for timestamp in sorted(timestamps):
        rows = [member[timestamp] for member in member_bins]
        scale = rows[0]["scale"]
        if any(r["scale"] != scale or r["is_ratio"] != rows[0]["is_ratio"] for r in rows):
            raise ValueError("Incompatible counter definitions")
        numerator = sum(r["numerator"] for r in rows)
        # Parallel member rates share elapsed time, while latency shares IO count.
        denominator = (
            sum(r["denominator"] for r in rows)
            if rows[0]["is_ratio"] else GRID_SECONDS
        )
        result.append({
            "start": timestamp, "end": timestamp + GRID_SECONDS,
            "numerator": numerator, "denominator": denominator, "scale": scale,
            "value": numerator / denominator * scale if denominator else None,
        })
    return result


def bin_stats(rows):
    denominator = sum(r["denominator"] for r in rows)
    return {
        "avg": sum(r["numerator"] for r in rows) / denominator * rows[0]["scale"]
        if denominator else None,
        "max": max((r["value"] for r in rows if r["value"] is not None), default=None),
        "seconds": len(rows) * GRID_SECONDS,
    }


def link_speed_bps(text):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGTP])(?:BPS|B/S)?", str(text).upper())
    if not match:
        return None
    return float(match[1]) * 1000 ** ("KMGTP".index(match[2]) + 1)


def port_utilization(rx, tx, links):
    states = {}
    for link in sorted(links, key=lambda r: r["timestamp_ms"]):
        t = link["timestamp_ms"] / 1000
        state = dict(states[max(states)]) if states else {}
        state.update(link)
        states[t] = state
    times = sorted(states)
    result = []
    for timestamp in sorted(set(rx) & set(tx)):
        pos = bisect_right(times, timestamp) - 1
        if pos < 0:
            continue
        config = states[times[pos]]
        speed = link_speed_bps(config.get("link_speed"))
        duplex = str(config.get("link_duplex", "")).lower()
        if not speed or duplex != "full":
            continue
        later = [
            states[t] for t in times
            if timestamp < t < timestamp + GRID_SECONDS
        ]
        if any(
            link_speed_bps(row.get("link_speed")) != speed
            or str(row.get("link_duplex", "")).lower() != duplex for row in later
        ):
            continue
        rx_bps = rx[timestamp]["numerator"] / GRID_SECONDS * 8
        tx_bps = tx[timestamp]["numerator"] / GRID_SECONDS * 8
        result.append({
            "timestamp": timestamp, "rx": rx_bps / speed, "tx": tx_bps / speed,
            "total": (rx_bps + tx_bps) / speed, "speed_bps": speed,
        })
    return result


def zombie_snapshots(member_samples):
    """Use a common as-of point; allow only fresh, real gauge observations."""
    latest = [max(s["timestamp_ms"] for s in samples) for samples in member_samples if samples]
    if not latest:
        return None, [None] * len(member_samples)
    asof = min(latest)
    chosen = []
    for samples in member_samples:
        candidates = [s for s in samples if s["timestamp_ms"] <= asof]
        sample = max(candidates, key=lambda s: s["timestamp_ms"]) if candidates else None
        if sample and asof - sample["timestamp_ms"] > sample["period_ms"]:
            sample = None
        chosen.append(sample)
    return asof, chosen


def zombie_fields(sample):
    blocks = sample["value"] if sample is not None else None
    return {
        "zombie_blocks": blocks,
        "zombie_tib": blocks * 4096 / 1024**4 if blocks is not None else None,
        "zombie_at": sample["timestamp_ms"] if sample is not None else None,
    }


def all_links_down(links):
    states = {
        str(r["link_current_state"]).strip().lower()
        for r in links if r.get("link_current_state") not in (None, "")
    }
    return states == {"down"}


def field(key, label, width=14, fmt=None):
    return {"key": key, "label": label, "width": width, "format": fmt}


def performance_fields(cpu=False):
    result = [
        field("io_avg", "平均 IO\nMiB/s", 14, "#,##0.00"),
        field("io_max", "峰值 IO\nMiB/s", 14, "#,##0.00"),
        field("iops_avg", "平均 IOPS", 14, "#,##0.0"),
        field("iops_max", "峰值 IOPS", 14, "#,##0.0"),
    ]
    if cpu:
        result += [
            field("cpu_avg", "平均 CPU\n使用率", 13, "0.0%"),
            field("cpu_max", "峰值 CPU\n使用率", 13, "0.0%"),
        ]
    result += [
        field("lat_avg", "平均延迟\nms", 13, "0.000"),
        field("lat_max", "峰值延迟\nms", 13, "0.000"),
    ]
    return result


def supporting_fields():
    return [
        field("coverage", "有效覆盖", 12, "0.0%"),
        field("notes", "数据备注", 48),
        field("window_start", "窗口开始\nUTC+08:00", 22, "yyyy-mm-dd hh:mm"),
        field("window_end", "窗口结束\nUTC+08:00", 22, "yyyy-mm-dd hh:mm"),
        field("resource_id", "资源 ID", 56),
    ]


def metric_pair(row, prefix, summary):
    row[prefix + "_avg"], row[prefix + "_max"] = summary["avg"], summary["max"]


def note_join(*values):
    return "；".join(v for v in values if v)


def prepare(data_dir, output, customer=None):
    batch = json.loads((data_dir / "batch.json").read_text(encoding="utf-8"))
    if not batch or any(n.get("collector_version") != 3 or n.get("status") == "failed" for n in batch):
        raise ValueError("This report requires a fully decoded v3 batch")
    node_names = [n["node"] for n in batch]
    if len(node_names) != len(set(node_names)):
        raise ValueError("Duplicate nodes/windows require separate report batches; refusing to overwrite")
    series = collections.defaultdict(list)
    gauge_series = collections.defaultdict(list)
    summaries = {}
    inventory = {}
    node_map = {}
    groups = collections.defaultdict(list)
    sources = []
    excluded_down_ports = []
    for node in batch:
        node_map[node["node"]] = node
        summaries[node["node"]] = {
            (m["object"], m["svm"], m["resource"], m["metric"]): m for m in node["metrics"]
        }
        for sample in node.get("gauge_samples", []):
            gauge_series[(node["node"], sample["svm"], sample["resource"])].append(sample)
        path = data_dir / f"{node['node']}.samples.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for item in json.load(handle):
                item["a"], item["b"] = seconds(item["start"]), seconds(item["end"])
                key = (node["node"], item["object"], item["svm"], item["resource"], item["metric"])
                series[key].append(item)
        for item in node["inventory"]:
            if item["object"] != "volume":
                continue
            key = (node["node"], item["vserver_name"], item["instance_name"])
            inventory[key] = item
            if item.get("flexgroup_msid"):
                groups[(node["cluster"], item["vserver_name"], item["flexgroup_msid"])].append(key)
        sources.append({
            "type": "PA", "file": node["file"], "node": node["node"],
            "start": node["requested_start_ms"], "end": node["requested_end_ms"],
            "sha256": node["source_sha256"],
        })
    for rows in series.values():
        rows.sort(key=lambda r: r["a"])

    def fetch(node, obj, resource, metric, svm=""):
        return series[(node, obj, svm, resource, metric)]

    def stat(node, obj, resource, metric, svm=""):
        return summarize(fetch(node, obj, resource, metric, svm))

    def base(node):
        return {
            "cluster": node["cluster"], "node": node["node"],
            "date": node["requested_start_ms"], "window_start": node["requested_start_ms"],
            "window_end": node["requested_end_ms"],
        }

    def coverage(node, *stats):
        duration = (node["requested_end_ms"] - node["requested_start_ms"]) / 1000
        return min((s["seconds"] for s in stats), default=0) / duration

    node_rows, aggr_rows, volume_rows, detail_rows, port_rows = [], [], [], [], []
    for node in batch:
        name, cluster = node["node"], node["cluster"]
        row = base(node)
        io = stat(name, "system", name, "throughput_mib_s")
        iops = stat(name, "system", name, "iops")
        cpu = stat(name, "system", name, "cpu_busy_pct")
        latency = stat(name, "system", name, "latency_ms")
        for label, item in [("io", io), ("iops", iops), ("lat", latency)]:
            metric_pair(row, label, item)
        row["cpu_avg"] = cpu["avg"] / 100 if cpu["avg"] is not None else None
        row["cpu_max"] = cpu["max"] / 100 if cpu["max"] is not None else None
        row["coverage"] = coverage(node, io, iops, cpu)
        row["notes"] = "system IO counter 未增长；不等于后端无负载" if iops["avg"] == 0 else ""
        row["resource_id"] = f"{node['cluster_uuid']}|node|{node['node_uuid']}"
        node_rows.append(row)
        for aggr in (r for r in node["inventory"] if r["object"] == "aggregate"):
            aggr_name = aggr["instance_name"]
            row = {**base(node), "aggr": aggr_name}
            io = summarize(combine_rate_rows(
                fetch(name, "aggregate", aggr_name, "read_mib_s"),
                fetch(name, "aggregate", aggr_name, "write_mib_s"),
            ))
            iops = stat(name, "aggregate", aggr_name, "iops")
            latency = stat(name, "aggregate", aggr_name, "latency_ms")
            util = stat(name, "resource_headroom_aggr", aggr["instance_uuid"], "disk_utilization_pct")
            disk_latency = stat(name, "resource_headroom_aggr", aggr["instance_uuid"], "disk_latency_ms")
            for label, item in [("io", io), ("iops", iops), ("lat", latency)]:
                metric_pair(row, label, item)
            row["util_avg"] = util["avg"] / 100 if util["avg"] is not None else None
            row["util_max"] = util["max"] / 100 if util["max"] is not None else None
            row["disk_latency"] = disk_latency["avg"]
            row["coverage"] = coverage(node, iops)
            row["io_minutes"] = io["seconds"] / 60
            row["notes"] = note_join(
                "IO/每操作延迟为小时粒度",
                (
                    "无有效小时差分区间，无法计算IO/每操作延迟；"
                    + "、".join(
                        f"{metric}={summaries[name].get(('aggregate', '', aggr_name, metric), {}).get('raw_samples_in_window', 0)}点"
                        for metric in ("read_mib_s", "write_mib_s", "latency_ms")
                    )
                ) if io["avg"] is None else "",
                "未提供磁盘利用率 counter" if util["avg"] is None else "",
            )
            row["resource_id"] = f"{node['cluster_uuid']}|aggregate|{aggr['instance_uuid']}"
            aggr_rows.append(row)

        links = collections.defaultdict(list)
        for link in node.get("port_link_samples", []):
            links[link["port"]].append(link)
        for port in (r for r in node["inventory"] if r["object"] == "nic_common"):
            port_name = port["instance_name"]
            if all_links_down(links[port_name]):
                excluded_down_ports.append({"cluster": cluster, "node": name, "port": port_name})
                continue
            row = {**base(node), "port": port_name}
            rx = fetch(name, "nic_common", port_name, "rx_mib_s")
            tx = fetch(name, "nic_common", port_name, "tx_mib_s")
            rx_bins, tx_bins = bin_intervals(rx), bin_intervals(tx)
            port_times = set(rx_bins) & set(tx_bins)
            rx_aligned = aggregate_bins([rx_bins], port_times)
            tx_aligned = aggregate_bins([tx_bins], port_times)
            combined = [
                {**a, "numerator": a["numerator"] + b["numerator"],
                 "value": a["value"] + b["value"]}
                for a, b in zip(rx_aligned, tx_aligned)
            ]
            io = bin_stats(combined)
            metric_pair(row, "io", io)
            row["rx_io"], row["tx_io"] = bin_stats(rx_aligned)["avg"], bin_stats(tx_aligned)["avg"]
            utilization = port_utilization(rx_bins, tx_bins, links[port_name])
            speed_set = {r["speed_bps"] for r in utilization}
            observed = {link_speed_bps(r.get("link_speed")) for r in links[port_name]}
            observed.discard(None)
            row["speed_gbps"] = next(iter(observed)) / 1e9 if len(observed) == 1 else None
            row["rx_util"] = sum(r["rx"] for r in utilization) / len(utilization) if utilization else None
            row["tx_util"] = sum(r["tx"] for r in utilization) / len(utilization) if utilization else None
            row["util_avg"] = sum(r["total"] for r in utilization) / len(utilization) if utilization else None
            row["util_max"] = max((r["total"] for r in utilization), default=None)
            row["latency"] = None
            states = {r["link_current_state"] for r in links[port_name] if "link_current_state" in r}
            row["link_state"] = "/".join(sorted(states)) or "N/A"
            row["coverage"] = coverage(node, io)
            row["util_minutes"] = len(utilization) * GRID_SECONDS / 60
            row["notes"] = note_join(
                "无端口 latency counter",
                "无有效全双工速率快照，利用率缺失" if not utilization else "",
                "速率变化，按区间速率计算" if len(speed_set) > 1 else "",
            )
            row["resource_id"] = f"{node['cluster_uuid']}|{node['node_uuid']}|ethernet|{port_name}"
            port_rows.append(row)

    group_checks = []
    grouped_keys = set()
    for (cluster, svm, msid), members in sorted(groups.items()):
        grouped_keys.update(members)
        prefixes = {re.sub(r"__\d{4}$", "", key[2]) for key in members}
        if len(prefixes) != 1:
            raise ValueError(f"Inconsistent FlexGroup names for {cluster} {msid}")
        parent_name = prefixes.pop()
        binned = {
            key: {metric: bin_intervals(fetch(key[0], "volume", key[2], metric, key[1]))
                  for metric in VOLUME_METRICS} for key in members
        }
        common = set.intersection(*(
            set(binned[key][metric]) for key in members for metric in VOLUME_METRICS
        ))
        zombie_asof, zombie_members = zombie_snapshots([gauge_series[k] for k in members])
        zombie_by_key = dict(zip(members, zombie_members))
        zombie_parent = (
            {"value": sum(s["value"] for s in zombie_members), "timestamp_ms": zombie_asof}
            if all(s is not None for s in zombie_members) else None
        )
        zombie_note = (
            "Zombie为共同截至时点各成员最近5分钟内快照之和，非原子同时快照"
            if zombie_parent is not None else "Zombie缺少部分成员有效快照，不计算大卷总量"
        )
        node = node_map[members[0][0]]
        windows = {
            (node_map[k[0]]["requested_start_ms"], node_map[k[0]]["requested_end_ms"])
            for k in members
        }
        if len(windows) != 1:
            raise ValueError("FlexGroup nodes do not share a requested window")
        parent_id = f"{node['cluster_uuid']}|{svm}|flexgroup-msid|{msid}"
        aggregates = {
            metric: aggregate_bins([binned[key][metric] for key in members], common)
            for metric in VOLUME_METRICS
        }

        def volume_metrics(source):
            iops, latency = bin_stats(source["iops"]), bin_stats(source["latency_ms"])
            io_rows = [
                {**a, "numerator": a["numerator"] + b["numerator"],
                 "value": a["value"] + b["value"]}
                for a, b in zip(source["read_mib_s"], source["write_mib_s"])
            ]
            io = bin_stats(io_rows)
            return {
                "io_avg": io["avg"], "io_max": io["max"],
                "iops_avg": iops["avg"], "iops_max": iops["max"],
                "lat_avg": latency["avg"], "lat_max": latency["max"],
                "read_io": bin_stats(source["read_mib_s"])["avg"],
                "write_io": bin_stats(source["write_mib_s"])["avg"],
                "read_lat": bin_stats(source["read_latency_ms"])["avg"],
                "write_lat": bin_stats(source["write_latency_ms"])["avg"],
                "coverage": coverage(node, iops), "valid_minutes": iops["seconds"] / 60,
                "actual_start": min(common) * 1000 if common else None,
                "actual_end": (max(common) + GRID_SECONDS) * 1000 if common else None,
            }

        parent = {
            **base(node), **volume_metrics(aggregates), "svm": svm, "volume": parent_name,
            **zombie_fields(zombie_parent),
            "style": "FlexGroup", "members": len(members),
            "node": f"跨 {len({m[0] for m in members})} 节点",
            "notes": zombie_note, "resource_id": parent_id,
        }
        volume_rows.append(parent)
        for key in sorted(members, key=lambda x: x[2]):
            item = inventory[key]
            member_source = {
                metric: aggregate_bins([binned[key][metric]], common) for metric in VOLUME_METRICS
            }
            detail = {
                **base(node_map[key[0]]), **volume_metrics(member_source),
                **zombie_fields(zombie_by_key[key]),
                "svm": svm, "volume": key[2], "parent": parent_name,
                "aggr": item["parent_aggr"],
                "notes": "" if zombie_by_key[key] is not None else "缺少Zombie有效快照",
                "resource_id": f"{node['cluster_uuid']}|volume-instance|{item['instance_uuid']}",
                "parent_id": parent_id,
            }
            detail_rows.append(detail)
        actual_details = [r for r in detail_rows if r["parent_id"] == parent_id]
        for metric in ("io_avg", "iops_avg"):
            if parent[metric] is not None and not math.isclose(
                sum(d[metric] for d in actual_details), parent[metric], rel_tol=1e-10, abs_tol=1e-8
            ):
                raise ValueError(f"FlexGroup {metric} does not reconcile")
        group_checks.append({
            "cluster": cluster, "svm": svm, "name": parent_name,
            "member_count": len(members),
            "common_5m_bins": len(common), "resource_id": parent_id,
        })

    for key, item in sorted(inventory.items()):
        if key in grouped_keys:
            continue
        node = node_map[key[0]]
        row = {
            **base(node), "svm": key[1], "volume": key[2],
            "style": "节点根卷" if key[1] == key[0] and key[2] == "vol0" else "FlexVol",
            "node": key[0], "members": None,
            **zombie_fields(zombie_snapshots([gauge_series[key]])[1][0]),
        }
        io = summarize(combine_rate_rows(
            fetch(key[0], "volume", key[2], "read_mib_s", key[1]),
            fetch(key[0], "volume", key[2], "write_mib_s", key[1]),
        ))
        iops = stat(key[0], "volume", key[2], "iops", key[1])
        latency = stat(key[0], "volume", key[2], "latency_ms", key[1])
        for label, value in [("io", io), ("iops", iops), ("lat", latency)]:
            metric_pair(row, label, value)
        row["read_io"] = stat(key[0], "volume", key[2], "read_mib_s", key[1])["avg"]
        row["write_io"] = stat(key[0], "volume", key[2], "write_mib_s", key[1])["avg"]
        row["read_lat"] = stat(key[0], "volume", key[2], "read_latency_ms", key[1])["avg"]
        row["write_lat"] = stat(key[0], "volume", key[2], "write_latency_ms", key[1])["avg"]
        row["coverage"] = coverage(node, io, iops)
        row["valid_minutes"] = iops["seconds"] / 60
        row["actual_start"] = seconds(iops["start"]) * 1000 if iops["start"] else None
        row["actual_end"] = seconds(iops["end"]) * 1000 if iops["end"] else None
        row["notes"] = note_join(
            "节点根卷" if row["style"] == "节点根卷" else "",
            "缺少Zombie有效快照" if row["zombie_blocks"] is None else "",
        )
        row["resource_id"] = f"{node['cluster_uuid']}|volume-instance|{item['instance_uuid']}"
        volume_rows.append(row)

    common_ids = [field("cluster", "集群", 22), field("node", "节点", 22)]
    date = field("date", "采样日期", 14, "yyyy-mm-dd")
    zombie_columns = [
        field("zombie_blocks", "Zombie 块数\nwvblk_zombie_blks", 24, "#,##0"),
        field("zombie_tib", "待回收数据量\nTiB (1024制)", 18, "#,##0.00"),
    ]
    vol_extras = [
        field("read_io", "平均读 IO\nMiB/s", 14, "#,##0.00"),
        field("write_io", "平均写 IO\nMiB/s", 14, "#,##0.00"),
        field("read_lat", "平均读延迟\nms", 14, "0.000"),
        field("write_lat", "平均写延迟\nms", 14, "0.000"),
        *zombie_columns,
        field("valid_minutes", "有效时长\n分钟", 13, "0.0"),
        field("actual_start", "有效开始\nUTC+08:00", 22, "yyyy-mm-dd hh:mm"),
        field("actual_end", "有效结束\nUTC+08:00", 22, "yyyy-mm-dd hh:mm"),
        field("zombie_at", "Zombie 采样时点\nUTC+08:00", 24, "yyyy-mm-dd hh:mm:ss"),
    ]
    sheets = [
        {
            "name": "Node", "title": "节点性能", "frozen": 2,
            "note": "IO = 读写及其他数据吞吐；CPU = cpu_busy；CPU 粒度 5 秒，IO/IOPS/延迟粒度 1 分钟。",
            "fields": common_ids + [date] + performance_fields(cpu=True) + supporting_fields(),
            "rows": node_rows,
        },
        {
            "name": "Aggr", "title": "Aggregate 性能", "frozen": 3,
            "note": "IO/每操作延迟为 1 小时粒度；IOPS 为 5 分钟；磁盘利用率/磁盘层延迟为 1 分钟。无 counter 时为 N/A。",
            "fields": common_ids + [field("aggr", "Aggregate", 40), date] + performance_fields() + [
                field("util_avg", "平均磁盘\n利用率", 14, "0.0%"),
                field("util_max", "峰值磁盘\n利用率", 14, "0.0%"),
                field("disk_latency", "磁盘层平均\n延迟 ms", 15, "0.000"),
                field("io_minutes", "IO有效时长\n分钟", 14, "0.0"),
            ] + supporting_fields(), "rows": aggr_rows,
        },
        {
            "name": "Volume", "title": "逻辑卷性能", "frozen": 3,
            "note": "FlexVol 与 FlexGroup 大卷各一行；不重复列成员卷。延迟为 WAFL 处理延迟；FlexGroup 使用成员共同覆盖的 5 分钟窗口。",
            "fields": [
                field("cluster", "集群", 22), field("svm", "SVM", 28),
                field("volume", "逻辑卷", 42), date,
            ] + performance_fields() + [
                field("style", "卷类型", 14), field("members", "成员数", 12, "#,##0"),
                field("node", "所属节点", 22),
            ] + vol_extras + supporting_fields(), "rows": volume_rows,
        },
        {
            "name": "FlexGroup", "title": "FlexGroup 成员性能", "frozen": 3,
            "note": "与大卷使用相同 5 分钟共同窗口；IO/IOPS 相加，延迟按操作数加权；大卷峰值不是成员峰值之和。",
            "fields": [
                field("cluster", "集群", 22), field("parent", "FlexGroup 大卷", 36),
                field("volume", "成员卷", 42), date,
            ] + performance_fields() + [
                field("node", "节点", 22), field("svm", "SVM", 28),
                field("aggr", "Aggregate", 38),
            ] + vol_extras + supporting_fields() + [
                field("parent_id", "大卷资源 ID", 56),
            ], "rows": detail_rows,
        },
        {
            "name": "Network Port", "title": "网络端口性能", "frozen": 3,
            "note": "5 分钟粒度。合计带宽比 = (RX+TX)×8 / 单向链路速度，全双工可超过 100%；端口 latency 无 counter，显示 N/A。",
            "fields": common_ids + [field("port", "端口", 13), date] + [
                field("io_avg", "平均 IO\nMiB/s", 14, "#,##0.00"),
                field("io_max", "峰值 IO\nMiB/s", 14, "#,##0.00"),
                field("latency", "端口延迟\nms", 14, "0.000"),
                field("util_avg", "平均合计\n带宽比", 14, "0.0%"),
                field("util_max", "峰值合计\n带宽比", 14, "0.0%"),
                field("speed_gbps", "链路速度\nGb/s", 14, "0.0"),
                field("link_state", "链路状态", 14),
                field("rx_io", "平均接收\nMiB/s", 14, "#,##0.00"),
                field("tx_io", "平均发送\nMiB/s", 14, "#,##0.00"),
                field("rx_util", "平均 RX\n使用率", 14, "0.0%"),
                field("tx_util", "平均 TX\n使用率", 14, "0.0%"),
                field("util_minutes", "带宽比有效\n时长 分钟", 16, "0.0"),
            ] + supporting_fields(), "rows": port_rows,
        },
    ]
    for sheet in sheets:
        keys = [r["resource_id"] for r in sheet["rows"]]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Duplicate resource IDs in {sheet['name']}")
        sheet["rows"].sort(key=lambda r: (
            r["cluster"], r.get("svm", ""), r.get("parent", ""), r.get("node", ""),
            r.get("volume", r.get("aggr", r.get("port", ""))),
        ))
    sample_dates = sorted({
        datetime.fromtimestamp(n["requested_start_ms"] / 1000, REPORT_TZ).date().isoformat()
        for n in batch
    })
    missing_aggr_io = [r for r in aggr_rows if r["io_avg"] is None]
    missing_io_by_node = collections.Counter(r["node"] for r in missing_aggr_io)
    aggr_missing_description = (
        "本批各 Aggregate 均有可用 IO 差分区间。"
        if not missing_aggr_io else
        f"本批 {len(missing_aggr_io)} 个 Aggregate 无有效小时 IO 差分区间："
        + "、".join(f"{name}（{count}个）" for name, count in sorted(missing_io_by_node.items()))
        + "。各行列出原始快照数；至少需要两点且无过大间隔或计数器回退，才能计算。"
    )
    info = [
        ["统计状态", "PA 实验解析，尚未完成 AIQUM 或官方数值校准。"],
        ["采样日期", "、".join(sample_dates) + "；各行分别保留窗口，不把目录日期当采样时间。"],
        ["IO 单位", "IO 表示吞吐，不是 IO 次数；1 MiB/s = 1,048,576 B/s。网络速度使用十进制 Gb/s。"],
        ["IOPS", "操作数增量 / 实际经过秒数；总操作可能包含读写之外的操作。"],
        ["峰值", "原生采样区间平均值的最大值，不是瞬时尖峰。FlexGroup 为对齐后 5 分钟区间峰值。"],
        ["延迟", "延迟累计量增量 / 操作数增量，us 转 ms；跨区间、跨成员按操作数加权。"],
        ["Volume 延迟", "WAFL 文件系统处理延迟，不包含请求处理和网络通信时间，不是客户端端到端延迟。"],
        ["CPU 使用率", "system.cpu_busy / cpu_elapsed_time；可能受最忙处理域限制，不等同于各处理器 busy 的算术平均。"],
        ["Aggregate 层级", "IO/IOPS/每操作延迟来自 aggregate；磁盘利用率和磁盘层延迟来自 resource_headroom_aggr，二者不混称。"],
        ["FlexGroup 识别", "按 cluster + SVM + 非零 flexgroup_msid 分组，名称仅用于展示；不只凭 __0001 后缀认定成员关系。"],
        ["FlexGroup 成员数", "仅统计本批 PA 中识别到的实际成员数。若批次未包含全部节点，不据此断言成员完整性。"],
        ["FlexGroup 时间对齐", "保留所有成员、各指标都完整覆盖的 5 分钟格；按原区间均值和重叠时长分配计数，不提高原始分辨率。"],
        ["FlexGroup 明细", "成员行与大卷使用同一共同窗口，因此平均 IO/IOPS 可相加；延迟不能直接算术平均，成员峰值不能相加。"],
        ["端口利用率", "按用户定义提供 (RX+TX)×8 / 单向链路 speed。全双工该比例理论上可达 200%，同时提供 RX/TX 单向利用率。"],
        ["端口速度", "取 PA nic_common 的 link_speed、link_duplex 快照；速度未知、非全双工或格内速度变化时不计算该格利用率。"],
        ["端口延迟", "当前 PA 的 nic_common 没有端口 latency counter，保留该列为 N/A。不能用存储 IO 延迟代替网络延迟。"],
        ["端口展示", f"本批全程观测为 down 的 {len(excluded_down_ports)} 个端口不展示；up/down 变化或状态未知的端口保留。原始数据未删除。"],
        ["Zombie counter", "volume.wvblk_zombie_blks 为时点块数，取原始快照，不做差分。普通卷取窗口内末次快照；真实零保留0。"],
        ["待回收数据量", "wvblk_zombie_blks × 4096 / 1024^4，单位为 TiB（1024制）。显示两位小数，底层保留精度；0.00 不一定表示零块。该值为待回收块的名义容量，不等同于最终物理可用空间增量。"],
        ["FlexGroup Zombie", "在所有已提供成员共同截至时点，取各成员不早于该时点前一个原生采样周期（本批5分钟）的最近快照求和。不同节点非原子同时采样；成员表记录各自真实时点。缺少任一成员有效值，大卷为N/A，不以0补齐。"],
        ["Aggregate IO缺失", aggr_missing_description],
        ["N/A 与零", "真实零增量保留 0；无操作、缺样或无 counter 的延迟显示 N/A。缺失数据绝不补零。"],
        ["system IO 为零", "只说明所选 system counter 未增长，不代表该节点的卷或后端 aggregate 没有负载。"],
        ["有效覆盖", "有效时间 / 请求窗口时间。Node 不把零操作延迟当作缺样；Aggregate 为 IOPS 覆盖，小时 IO 另列有效分钟。"],
        ["跨期比较", "后续按资源 ID、采样窗口、指标版本对齐；各批保持单位、统计层级和聚合粒度一致。短窗口不能代表整月均值。"],
        ["卷资源 ID", "当前使用 PA instance_uuid。卷迁移或重建后应通过同期 ASUP i_uuid/vol_uuid 映射核对，不仅按同名卷自动拼接。"],
        ["数据范围", "只包含本批 PA 提供的设备和资源；未提供设备不是零负载，也不纳入本次比较。"],
    ]
    if customer:
        info.insert(0, ["客户 / Site", customer])
        info.insert(1, [
            "本批日志",
            f"{len(batch)} 份 PA，{len({n['cluster_uuid'] for n in batch})} 个集群，"
            f"{len(batch)} 个节点。每行数值仅代表各自请求窗口，不是整月连续统计。",
        ])
    model = {
        "version": "performance-report-v4", "timezone": "Asia/Shanghai",
        "customer": customer,
        "sheets": sheets, "info": info, "sources": sources,
        "flexgroup_checks": group_checks, "excluded_down_ports": excluded_down_ports,
        "counts": {s["name"]: len(s["rows"]) for s in sheets},
    }
    if customer:
        month = sample_dates[-1][:7].replace("-", "")
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", customer)
        model["output_filename"] = f"PA_Report_{safe_name}_{month}.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT_ROWS", model["counts"])
    print("FLEXGROUP_CHECKS", json.dumps(group_checks, ensure_ascii=True))
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "outputs/performance_report_20261116_r2/data")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/performance_report_20261116_r3/report_model.json")
    parser.add_argument("--customer")
    args = parser.parse_args()
    prepare(args.data, args.output, args.customer)


if __name__ == "__main__":
    main()
