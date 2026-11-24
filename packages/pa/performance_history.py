"""Versioned, idempotent PA sampling-window history, using the standard SQLite API."""

import argparse
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

from performance_analysis import DEFAULTS, METRIC_VERSION, thresholds, table, N, P, M, INT, unit_for

TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = 2


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).isoformat()


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current not in (0, SCHEMA_VERSION):
        raise ValueError(f"Unsupported history schema {current}; explicit migration required")
    db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS files(
          sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, node TEXT NOT NULL,
          context_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batches(
          batch_id TEXT PRIMARY KEY, site TEXT NOT NULL, start_ms INTEGER NOT NULL,
          end_ms INTEGER NOT NULL, month TEXT NOT NULL, source_set_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batch_files(
          batch_id TEXT REFERENCES batches, sha256 TEXT REFERENCES files,
          PRIMARY KEY(batch_id,sha256)
        );
        CREATE TABLE IF NOT EXISTS sample_blocks(
          file_sha TEXT REFERENCES files, layer TEXT NOT NULL,
          metric_version TEXT NOT NULL, row_count INTEGER NOT NULL,
          encoding TEXT NOT NULL, payload BLOB NOT NULL,
          PRIMARY KEY(file_sha,layer,metric_version)
        );
        CREATE TABLE IF NOT EXISTS runs(
          batch_id TEXT REFERENCES batches, metric_version TEXT NOT NULL,
          config_hash TEXT NOT NULL, model_path TEXT NOT NULL, config_json TEXT NOT NULL,
          PRIMARY KEY(batch_id,metric_version,config_hash)
        );
        CREATE TABLE IF NOT EXISTS snapshots(
          batch_id TEXT REFERENCES batches, metric_version TEXT NOT NULL,
          config_hash TEXT NOT NULL, resource_id TEXT NOT NULL, kind TEXT NOT NULL,
          cluster TEXT NOT NULL, node TEXT NOT NULL, svm TEXT NOT NULL,
          resource TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
          unit TEXT NOT NULL, coverage REAL NOT NULL, status TEXT NOT NULL,
          context_json TEXT NOT NULL,
          PRIMARY KEY(batch_id,metric_version,config_hash,resource_id,metric)
        );
        CREATE TABLE IF NOT EXISTS audit_events(
          id INTEGER PRIMARY KEY, batch_id TEXT, kind TEXT, detail_json TEXT
        );
        CREATE TABLE IF NOT EXISTS metric_coverage(
          file_sha TEXT REFERENCES files, object TEXT NOT NULL, resource TEXT NOT NULL,
          svm TEXT NOT NULL, metric TEXT NOT NULL, metric_version TEXT NOT NULL,
          valid_seconds REAL NOT NULL, numeric_seconds REAL NOT NULL,
          coverage REAL NOT NULL, native_periods_json TEXT NOT NULL, status TEXT NOT NULL,
          PRIMARY KEY(file_sha,object,resource,svm,metric,metric_version)
        );
        CREATE TABLE IF NOT EXISTS metric_migrations(
          old_version TEXT, new_version TEXT, reason TEXT NOT NULL,
          accepted_at TEXT NOT NULL, PRIMARY KEY(old_version,new_version)
        );
        CREATE INDEX IF NOT EXISTS snapshots_lookup ON snapshots(resource_id,metric);
    """)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return db


def comparable(current, previous, config):
    c, p = current["context"], previous["context"]
    reasons = []
    cfg = thresholds(config, current["cluster"])
    if current["metric_version"] != previous["metric_version"]:
        reasons.append("metric_version变化，需重算到同版本")
    if c.get("config_hash") != p.get("config_hash"):
        reasons.append("分析参数变化，需同参数重算")
    if current["resource_id"] != previous["resource_id"]:
        reasons.append("资源稳定ID不一致")
    if c.get("schema_hash") != p.get("schema_hash"):
        reasons.append("counter定义/周期变化")
    if current["coverage"] < cfg["minimum_coverage"] or previous["coverage"] < cfg["minimum_coverage"]:
        reasons.append("指标覆盖不足")
    if c["ontap"] != p["ontap"]:
        reasons.append("ONTAP版本变化，需人工校准")
    if c["cluster_nodes"] != p["cluster_nodes"]:
        reasons.append("实收节点集合变化")
    if c.get("members") != p.get("members"):
        reasons.append("FlexGroup成员集合变化")
    if c.get("location") != p.get("location"):
        reasons.append("资源所属节点/Aggr/名称变化，需确认迁移或改名映射")
    cd, pd = c["duration"], p["duration"]
    if not pd or abs(cd - pd) / pd > cfg["window_duration_tolerance"]:
        reasons.append("窗口时长不相近")
    hdiff = abs(c["hour"] - p["hour"])
    if min(hdiff, 24 - hdiff) > cfg["window_hour_tolerance"]:
        reasons.append("采样时段不相近")
    if c.get("expected_complete") is not True or p.get("expected_complete") is not True:
        reasons.append("期望节点清单未确认完整")
    if current["value"] is None or previous["value"] is None:
        reasons.append("存在非数值结果")
    return reasons


def snapshots(analysis, config_hash):
    model, batch = analysis.model, analysis.batch
    by_node = analysis.nodes
    result = []
    cluster_nodes = {}
    for n in batch:
        cluster_nodes.setdefault(n["cluster"], []).append(n["node_uuid"])
    expected = analysis.config.get("expected_inventory", {}).get(model["customer"], {})
    for kind, rows, metric_keys in [
        ("Node", analysis.base_sheets["Node"]["rows"], {
            "cpu_avg": ("cpu_busy_pct", "%", 100), "iops_avg": ("iops", "ops/s", 1),
            "io_avg": ("throughput_mib_s", "MiB/s", 1), "lat_avg": ("latency_ms", "ms", 1)}),
        ("Volume", analysis.base_sheets["Volume"]["rows"], {
            "iops_avg": ("iops", "ops/s", 1), "io_avg": ("throughput_mib_s", "MiB/s", 1),
            "lat_avg": ("latency_ms", "ms", 1)}),
        ("Network", analysis.base_sheets["Network Port"]["rows"], {
            "rx_util": ("rx_utilization", "ratio", 1), "tx_util": ("tx_utilization", "ratio", 1)}),
        ("FlexGroup", list(analysis.fg_risks.values()), {
            "iops_cv": ("member_iops_cv", "ratio", 1), "io_cv": ("member_io_cv", "ratio", 1),
            "skew_minutes": ("skew_minutes", "minutes", 1),
            "slow_latency": ("slow_member_latency_ms", "ms", 1)}),
    ]:
        for r in rows:
            related = [n for n in batch if n["cluster"] == r["cluster"] and (r["node"] not in by_node or n["node"] == r["node"])]
            n = related[0]
            src = None
            if kind == "Volume":
                src = analysis.volume_series(r)
            members = sorted(m["resource_id"] for m in analysis.base_sheets["FlexGroup"]["rows"] if m["parent_id"] == r["resource_id"])
            exp = expected.get(r["cluster"], {}).get("nodes")
            observed_names = sorted(x["node"] for x in batch if x["cluster"] == r["cluster"])
            complete = sorted(exp) == observed_names if exp is not None else None
            raw_inventory = analysis.inventory.get((n["node"], "volume", r.get("svm", ""), r.get("volume", "")), {})
            svm_uuids = sorted({
                item.get("vserver_uuid") for nn in related for item in nn["inventory"]
                if item.get("vserver_name") == r.get("svm") and item.get("vserver_uuid")
            })
            for key, (metric, unit, scale) in metric_keys.items():
                coverage = r["coverage"]
                native = []
                if kind == "Node":
                    samples = analysis.get(r["node"], "system", r["node"], metric)
                    coverage = sum(s["seconds"] for s in samples) / ((r["window_end"] - r["window_start"]) / 1000)
                    native = sorted({s["period_ms"] for s in samples})
                elif kind == "Volume":
                    samples = src.get(metric, [])
                    if key == "io_avg":
                        samples = src["read_mib_s"]
                    coverage = sum(s["seconds"] for s in samples) / ((r["window_end"] - r["window_start"]) / 1000)
                    native = sorted({s["period_ms"] for s in samples})
                elif kind == "Network":
                    coverage = r["util_minutes"] * 60000 / (r["window_end"] - r["window_start"])
                    native = [300000]
                else:
                    native = [300000]
                relevant_caps = [
                    c["schema_hash"] for sup in analysis.supplements if sup["manifest"]["node"] in [x["node"] for x in related]
                    for c in sup["manifest"]["capabilities"]
                    if c["object"] == ("system" if kind == "Node" else "nic_common" if kind == "Network" else "volume")
                    and c["counter"] in {
                        "cpu_busy", "cpu_elapsed_time"} if key == "cpu_avg"
                ] if key == "cpu_avg" else [
                    c["schema_hash"] for sup in analysis.supplements if sup["manifest"]["node"] in [x["node"] for x in related]
                    for c in sup["manifest"]["capabilities"]
                    if c["object"] == ("system" if kind == "Node" else "nic_common" if kind == "Network" else "volume")
                    and c["counter"] in (
                        {"total_ops"} if key == "iops_avg" else
                        {"total_data", "read_data", "write_data"} if key == "io_avg" else
                        {"total_latency", "avg_latency", "total_ops"} if key == "lat_avg" else
                        {"rx_bytes", "tx_bytes", "link_speed"} if kind == "Network" else {"total_ops", "read_data", "write_data"}
                    )
                ]
                context = {
                    "start_iso": iso(r["window_start"]), "end_iso": iso(r["window_end"]),
                    "duration": (r["window_end"] - r["window_start"]) / 1000,
                    "hour": datetime.fromtimestamp(r["window_start"] / 1000, TZ).hour + datetime.fromtimestamp(r["window_start"] / 1000, TZ).minute / 60,
                    "ontap": sorted({x["version"] for x in related}),
                    "cluster_nodes": sorted(cluster_nodes[r["cluster"]]),
                    "members": members, "location": [r.get("node"), r.get("aggr"), r.get("svm"), r.get("volume", r.get("port"))],
                    "expected_complete": complete, "native_ms": native, "config_hash": config_hash,
                    "schema_hash": digest(sorted(set(relevant_caps))), "volume_uuid": raw_inventory.get("instance_uuid"),
                    "svm_uuid": svm_uuids, "identity_status": "PA instance_uuid；迁移/重建需映射" if kind == "Volume" else "PA身份",
                    "workload_read_share": analysis.vol_results.get(r["resource_id"], {}).get("read_share"),
                    "iops_value": r.get("iops_avg"), "throughput_value": r.get("io_avg"),
                    "hot_member": r.get("dominant"), "slow_member": r.get("slow"),
                    "style": r.get("style"),
                }
                value = r.get(key)
                result.append({
                    "resource_id": r["resource_id"], "kind": kind, "cluster": r["cluster"],
                    "node": r.get("node", ""), "svm": r.get("svm", ""),
                    "resource": r.get("volume", r.get("port", r.get("node", ""))),
                    "metric": metric, "value": value * scale if value is not None else None,
                    "unit": unit, "coverage": min(1, coverage),
                    "status": "VALID" if value is not None and coverage >= DEFAULTS["minimum_coverage"] else "PARTIAL",
                    "metric_version": METRIC_VERSION, "context": context,
                })
    ranks = []
    for metric in ("iops", "throughput_mib_s", "latency_ms"):
        clusters = {r["cluster"] for r in result if r["kind"] == "Volume"}
        for cluster in clusters:
            eligible = [
                r for r in result if r["kind"] == "Volume" and r["cluster"] == cluster
                and r["metric"] == metric and r["value"] is not None and r["context"].get("style") != "节点根卷"
            ]
            for rank, r in enumerate(sorted(eligible, key=lambda r: (-r["value"], r["resource_id"])), 1):
                ranks.append({**r, "metric": metric + "_cluster_rank", "value": rank, "unit": "rank"})
    result.extend(ranks)
    return result


def ingest(db, analysis, model_path, recompute_samples=False):
    shas = sorted(n["source_sha256"] for n in analysis.batch)
    if len(shas) != len(set(shas)):
        raise ValueError("Duplicate PA SHA in batch")
    node_ids = [n["node_uuid"] for n in analysis.batch]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Duplicate node UUID in batch")
    site = analysis.model["customer"]
    batch_id = digest({"site": site, "files": shas})[:24]
    config_hash = digest(analysis.config)
    start = min(n["requested_start_ms"] for n in analysis.batch)
    end = max(n["requested_end_ms"] for n in analysis.batch)
    month = iso(start)[:7]
    with db:
        db.execute("INSERT OR IGNORE INTO batches VALUES(?,?,?,?,?,?)",
                   (batch_id, site, start, end, month, json.dumps(shas)))
        for n in analysis.batch:
            context = {k: n[k] for k in (
                "cluster", "cluster_uuid", "node", "node_uuid", "version", "model",
                "requested_start_ms", "requested_end_ms", "source_timezone")}
            context["inventory"] = n["inventory"]
            inserted = db.execute("INSERT OR IGNORE INTO files VALUES(?,?,?,?)", (
                n["source_sha256"], n["file"], n["node"], json.dumps(context, ensure_ascii=False))).rowcount
            db.execute("INSERT OR IGNORE INTO batch_files VALUES(?,?)", (batch_id, n["source_sha256"]))
            if inserted:
                db.execute("INSERT INTO audit_events(batch_id,kind,detail_json) VALUES(?,?,?)",
                           (batch_id, "file_import", json.dumps({"sha256": n["source_sha256"], "file": n["file"]})))
        for n in analysis.batch:
            rows = []
            for key, src in analysis.series.items():
                if key[0] != n["node"]:
                    continue
                duration = (n["requested_end_ms"] - n["requested_start_ms"]) / 1000
                covered = sum(r["seconds"] for r in src)
                numeric = sum(r["seconds"] for r in src if r["value"] is not None)
                db.execute("INSERT OR REPLACE INTO metric_coverage VALUES(" + ",".join("?" for _ in range(11)) + ")", (
                    n["source_sha256"], key[1], key[3], key[2], key[4], METRIC_VERSION,
                    covered, numeric, covered / duration, json.dumps(sorted({r["period_ms"] for r in src})),
                    "VALID" if covered / duration >= DEFAULTS["minimum_coverage"] else "PARTIAL"))
            if not recompute_samples and db.execute(
                "SELECT 1 FROM sample_blocks WHERE file_sha=? AND metric_version=? AND layer='port_link_snapshot' LIMIT 1",
                (n["source_sha256"], METRIC_VERSION)).fetchone():
                continue
            native_rows, raw_rows = [], []
            for key, src in analysis.series.items():
                if key[0] != n["node"]:
                    continue
                for r in src:
                    native_rows.append({
                        **r, "object": key[1], "resource": key[3], "svm": key[2], "metric": key[4],
                        "unit": r.get("unit", unit_for(key[4])),
                        "start": iso(r["a"] * 1000), "end": iso(r["b"] * 1000),
                        "status": "VALID" if r["value"] is not None else "PARTIAL",
                    })
            for sup in analysis.supplements:
                if sup["manifest"]["node"] != n["node"]:
                    continue
                for r in sup["raw"]:
                    raw_rows.append({**r, "timestamp": iso(r["timestamp_ms"]), "status": "VALID"})
            gauges = [
                {**r, "timestamp": iso(r["timestamp_ms"]), "unit": "block(4KiB)", "status": "VALID"}
                for r in n.get("gauge_samples", [])
            ]
            links = [{**r, "timestamp": iso(r["timestamp_ms"])} for r in n.get("port_link_samples", [])]
            for layer, data in [
                ("native_interval", native_rows), ("raw_counter", raw_rows),
                ("gauge_snapshot", gauges), ("port_link_snapshot", links),
            ]:
                payload = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), compresslevel=6, mtime=0)
                db.execute("INSERT OR REPLACE INTO sample_blocks VALUES(?,?,?,?,?,?)", (
                    n["source_sha256"], layer, METRIC_VERSION, len(data), "json-gzip-v1", payload))
        db.execute("INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?)", (
            batch_id, METRIC_VERSION, config_hash, str(model_path.resolve()),
            json.dumps(analysis.config, ensure_ascii=False, sort_keys=True)))
        if recompute_samples:
            db.execute("INSERT INTO audit_events(batch_id,kind,detail_json) VALUES(?,?,?)",
                       (batch_id, "explicit_sample_recompute", json.dumps({"version": METRIC_VERSION, "config_hash": config_hash})))
        current = snapshots(analysis, config_hash)
        for r in current:
            db.execute("INSERT OR REPLACE INTO snapshots VALUES(" + ",".join("?" for _ in range(15)) + ")", (
                batch_id, METRIC_VERSION, config_hash, r["resource_id"], r["kind"], r["cluster"],
                r["node"], r["svm"], r["resource"], r["metric"], r["value"], r["unit"],
                r["coverage"], r["status"], json.dumps(r["context"], ensure_ascii=False, sort_keys=True)))
    return batch_id, config_hash, current


def trend_sheets(db, analysis, batch_id, config_hash, current):
    trends, risks = [], []
    batch_row = dict(db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone())
    site = analysis.model["customer"]
    for r in current:
        candidates = db.execute("""
            SELECT s.*, b.start_ms,b.end_ms,b.month,b.site FROM snapshots s
            JOIN batches b USING(batch_id)
            WHERE b.site=? AND b.end_ms<? AND s.resource_id=? AND s.metric=?
            ORDER BY b.end_ms DESC, s.metric_version DESC
        """, (site, batch_row["start_ms"], r["resource_id"], r["metric"])).fetchall()
        previous, reasons, count = None, [], 0
        unique_batches = set()
        for raw in candidates:
            p = dict(raw)
            if p["batch_id"] in unique_batches:
                continue
            # Prefer the exact metric/config run when historical reruns exist.
            versions = [dict(x) for x in candidates if x["batch_id"] == p["batch_id"]]
            p = next((x for x in versions if x["metric_version"] == METRIC_VERSION and x["config_hash"] == config_hash), p)
            unique_batches.add(p["batch_id"])
            p["context"] = json.loads(p["context_json"])
            why = comparable(r, p, analysis.config)
            if not why:
                previous = p
                break
            count += 1
            if not reasons:
                reasons = why
        previous_value = previous["value"] if previous else None
        delta = r["value"] - previous_value if previous and r["value"] is not None else None
        relative = delta / abs(previous_value) if delta is not None and previous_value else None
        interpretation = "数据不足"
        if previous:
            old_share, new_share = previous["context"].get("workload_read_share"), r["context"].get("workload_read_share")
            if old_share is not None and new_share is not None and abs(old_share - new_share) > .20:
                interpretation = "读写操作结构变化"
            else:
                interpretation = "窗口数值上升" if delta > 0 else "窗口数值下降" if delta < 0 else "窗口数值相同"
            if r["metric"].endswith("latency_ms"):
                old_ops, new_ops = previous["context"].get("iops_value"), r["context"].get("iops_value")
                if old_ops and new_ops is not None and abs(new_ops - old_ops) / old_ops > .25:
                    interpretation = "负载变化，不能单独判定延迟改善/恶化"
            if r["metric"].endswith("_cluster_rank"):
                cutoff = thresholds(analysis.config, r["cluster"])["top_n"]
                if r["value"] <= cutoff < previous_value:
                    interpretation = "新进入本集群Top N"
                elif previous_value <= cutoff < r["value"]:
                    interpretation = "退出本集群Top N，资源仍可观测"
                else:
                    interpretation = "集群内热点排名变化（非性能改善结论）"
            for event in analysis.config.get("optimization_events", {}).get(r["cluster"], []):
                event_time = datetime.fromisoformat(event["timestamp"])
                if event_time.tzinfo is None:
                    raise ValueError("Optimization event requires a timezone-aware timestamp")
                before = datetime.fromisoformat(previous["context"]["end_iso"])
                after = datetime.fromisoformat(r["context"]["start_iso"])
                if before <= event_time <= after:
                    interpretation += "；跨越已记录优化事件，仍需排除负载变化"
        elif candidates:
            interpretation = "采样窗口不可比"
        trends.append({
            "kind": r["kind"], "cluster": r["cluster"], "resource": r["resource"],
            "metric": r["metric"], "current": r["value"], "previous": previous_value,
            "delta": delta, "relative": relative, "unit": r["unit"],
            "coverage": r["coverage"], "previous_batch": previous["batch_id"] if previous else None,
            "previous_window": previous["context"]["start_iso"] if previous else None,
            "status": "VALID" if previous else "NOT_COMPARABLE" if candidates else "PARTIAL",
            "interpretation": interpretation,
            "notes": (
                f"跳过{count}个不可比批次。" if previous and count else ""
            ) + ("上期为0，相对变化不定义。" if previous and previous_value == 0 else "")
            + ("；".join(reasons) if not previous and reasons else "仅本批真实窗口，尚无历史可比批次。" if not previous else "只代表采样窗口，未证明因果或优化效果。"),
        })
        # Select at most one comparable window per real month, never fabricate a month.
        monthly = {batch_row["month"]: {**r, "month": batch_row["month"], "batch_id": batch_id}}
        for raw in candidates:
            p = dict(raw)
            if p["month"] in monthly or p["metric_version"] != METRIC_VERSION or p["config_hash"] != config_hash:
                continue
            p["context"] = json.loads(p["context_json"])
            if not comparable(r, p, analysis.config):
                monthly[p["month"]] = p
        ordered = sorted(monthly)
        three = [monthly[m] for m in ordered[-3:]]
        eligible = len(three) >= 3 and consecutive_months([x["month"] for x in three])
        vals = [x["value"] for x in three]
        if not eligible:
            risk = "不足3个连续可比月份"
        else:
            cfg = thresholds(analysis.config, r["cluster"])
            limit = {
                "cpu_busy_pct": cfg["cpu_high_pct"], "iops": cfg["iops_high"],
                "throughput_mib_s": cfg["throughput_high_mib_s"], "latency_ms": cfg["latency_watch_ms"],
                "rx_utilization": cfg["network_high"], "tx_utilization": cfg["network_high"],
            }.get(r["metric"])
            if r["metric"] == "skew_minutes":
                limit = 0
            if r["metric"] == "slow_member_latency_ms":
                limit = cfg["latency_watch_ms"]
            if r["metric"].endswith("_cluster_rank"):
                risk = (
                    "连续3月进入本集群Top N" if all(v <= cfg["top_n"] for v in vals)
                    else "存在热点排名进出"
                )
            elif limit is not None and all(v is not None and v > limit for v in vals):
                risk = "连续3个月采样窗口超过阈值"
            else:
                risk = "未触发连续超阈值规则"
            if r["kind"] == "FlexGroup" and r["metric"] == "skew_minutes" and all(v is not None and v > 0 for v in vals):
                hot = {x["context"].get("hot_member") for x in three}
                risk += "；热点成员固定" if len(hot) == 1 and None not in hot else "；热点成员有迁移或未知"
            if r["kind"] == "FlexGroup" and r["metric"] == "slow_member_latency_ms":
                slow = {x["context"].get("slow_member") for x in three}
                if len(slow) == 1 and None not in slow and all(v is not None and v > cfg["latency_watch_ms"] for v in vals):
                    risk += "；同一慢成员持续"
        risks.append({
            "kind": r["kind"], "cluster": r["cluster"], "resource": r["resource"], "metric": r["metric"],
            "months": ",".join(ordered), "count": len(ordered),
            "first": vals[0] if eligible else None, "latest": r["value"],
            "unit": r["unit"],
            "risk": risk, "status": "VALID" if eligible else "NOT_COMPARABLE",
            "notes": "每月选择最新可比批次；同月多批不伪装多月。观察变化不等于真实优化收益。",
        })
    historical_ids = db.execute("""
        SELECT DISTINCT s.resource_id,s.kind,s.cluster,s.resource,s.metric
        FROM snapshots s JOIN batches b USING(batch_id)
        WHERE b.site=? AND b.end_ms<? AND s.metric_version=? AND s.config_hash=?
    """, (site, batch_row["start_ms"], METRIC_VERSION, config_hash)).fetchall()
    current_ids = {r["resource_id"] for r in current}
    for old in historical_ids:
        if old["resource_id"] in current_ids:
            continue
        trends.append({
            **dict(old), "status": "NOT_COMPARABLE", "interpretation": "本期未观测资源",
            "notes": "不能判断热点消失或资源删除；先核对节点缺失、迁移、改名或重建。",
        })
    trend = table("月度采样窗口趋势", "月度采样窗口趋势",
                  "从第二个可比批次计算变化；先检查稳定ID、窗口、覆盖、版本和节点/成员清单。首次仅入库，不虚构上月。",
                  [("kind", "资源类型", 17), ("cluster", "集群", 24), ("resource", "资源", 37),
                   ("metric", "指标", 29), ("current", "本期", 18, M), ("previous", "上期可比", 18, M),
                   ("delta", "绝对变化", 18, M), ("relative", "相对变化", 18, P), ("unit", "单位", 16),
                   ("coverage", "本期覆盖", 17, P), ("status", "可比性状态", 22),
                   ("interpretation", "变化解释", 43), ("previous_window", "上期窗口 +08:00", 30),
                   ("previous_batch", "上期 Batch ID", 30), ("notes", "比较限制", 80)], trends, 3)
    risk = table("多月持续性风险", "多月采样窗口持续性风险",
                 "至少3个连续真实月份且窗口可比才触发持续性规则；不是整月均值，不输出容量或性能预测。",
                 [("kind", "资源类型", 17), ("cluster", "集群", 24), ("resource", "资源", 37),
                  ("metric", "指标", 29), ("count", "可比月份数", 18, INT), ("months", "真实月份", 33),
                  ("first", "最近3月首期", 20, M), ("latest", "本期", 18, M),
                  ("unit", "单位", 16),
                  ("risk", "多月结论", 45), ("status", "状态", 22), ("notes", "限制", 80)], risks, 3)
    return [trend, risk]


def consecutive_months(months):
    indexes = [int(s[:4]) * 12 + int(s[5:7]) for s in months]
    return all(b - a == 1 for a, b in zip(indexes, indexes[1:]))


def export_history(db, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for table_name in ("batches", "files", "runs", "snapshots", "metric_coverage", "audit_events", "metric_migrations"):
        cursor = db.execute(f"SELECT * FROM {table_name}")
        with (dest / f"{table_name}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([c[0] for c in cursor.description])
            writer.writerows(cursor)


def export_observations(db, path):
    """Streaming JSONL export keeps 64-bit raw counters exact and metadata explicit."""
    with gzip.open(path, "wt", encoding="utf-8") if path.suffix == ".gz" else path.open("w", encoding="utf-8") as handle:
        for block in db.execute("SELECT * FROM sample_blocks"):
            rows = json.loads(gzip.decompress(block["payload"]))
            for row in rows:
                handle.write(json.dumps({
                    "file_sha256": block["file_sha"], "metric_version": block["metric_version"],
                    "layer": block["layer"], **row,
                }, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--export-observations", type=Path)
    parser.add_argument("--register-migration", nargs=3, metavar=("OLD", "NEW", "REASON"))
    args = parser.parse_args()
    db = connect(args.database)
    if args.export:
        export_history(db, args.export)
    if args.export_observations:
        export_observations(db, args.export_observations)
    if args.register_migration:
        old, new, reason = args.register_migration
        with db:
            db.execute("INSERT INTO metric_migrations VALUES(?,?,?,?)", (old, new, reason, datetime.now(TZ).isoformat()))
    print({name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("files", "batches", "sample_blocks", "snapshots")})
    db.close()


if __name__ == "__main__":
    main()
