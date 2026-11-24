"""Supplemental PA audit. Base collector/report meanings remain unchanged."""

import argparse
import collections
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from pa_probe import metadata_root, records
from validate_pa import headers, read_member, source_digest, verify_md5, time_text

AUDIT_VERSION = 2
GROUPS = {"kernel", "kernel/opm", "kernel/asup"}
INTEREST = re.compile(
    r"^(system|aggregate|volume|nic_common|processor|resource_headroom_cpu|"
    r"nfsv.*|cifs|smb[12]|workload.*|object_store_client.*|wafl_cloud_io.*|"
    r"wafl_hya.*|wafl_objpacking|wafl)$"
)
ERRORS = re.compile(r"(error|discard|drop|crc|fcs|overflow|no_buffers)")
WAITS = re.compile(r"(delay_.*wait_time|cp_.*wait|wait_.*time)")
NFS_OPS = (
    "null", "getattr", "setattr", "lookup", "access", "read_symlink",
    "read", "write", "create", "mkdir", "symlink", "mknod", "remove",
    "rmdir", "rename", "link", "readdir", "readdirplus", "fsstat", "fsinfo",
    "pathconf", "commit",
)


def rule(obj, name, counters):
    """Explicit scalar rules only; unknown semantics are retained, not guessed."""
    base, scale, unit, mode = None, 1, "count/s", "rate"
    if obj == "volume" and name in {"other_ops", "other_latency"}:
        if name.endswith("latency"):
            base, scale, unit = "other_ops", .001, "ms"
    elif obj == "processor" and name == "processor_busy":
        base, scale, unit = "processor_elapsed_time", 100, "%"
    elif obj == "nic_common" and ERRORS.search(name) and name != "total_errors":
        pass
    elif obj == "nfsv3" and name in {op + "_total" for op in NFS_OPS}:
        pass
    elif obj == "nfsv3" and name in {op + "_avg_latency" for op in NFS_OPS}:
        base, scale, unit = name.removesuffix("_avg_latency") + "_total", .001, "ms"
    elif obj == "nfsv3" and name == "nfsv3_ops":
        pass
    elif obj == "nfsv3" and name in {"nfsv3_read_throughput", "nfsv3_write_throughput"}:
        scale, unit = 1 / 2**20, "MiB/s"
    elif obj == "workload_volume" and name.startswith("cloud_io_") and name.endswith("_ops"):
        pass
    elif obj == "workload_volume" and name.startswith("cloud_io_") and name.endswith("_latency"):
        base, scale, unit = name.removesuffix("_latency") + "_ops", .001, "ms"
    elif obj == "wafl_cloud_io" and name.startswith("cio_") and "queue" not in name:
        pass
    elif obj.startswith("workload_queue_") and WAITS.search(name):
        # Raw wait-time counters have no verified per-operation denominator here.
        # Preserve native observations without guessing an average or additivity.
        mode = "raw"
        unit = "us(raw)" if "microseconds" in counters[name]["description"].lower() else "native(raw)"
    else:
        return None
    if base and (
        base not in counters or counters[name]["base_id"] != counters[base]["id"]
    ):
        return None
    return {"base": base, "scale": scale, "unit": unit, "mode": mode}


def definitions(root):
    result = {}
    for obj in root.findall("preset/object"):
        counters = {}
        for c in obj.findall("counter"):
            counters[c.findtext("name")] = {
                "id": int(c.findtext("id")), "base_id": int(c.findtext("base_id")),
                "ctype": int(c.get("ctype")), "size": int(c.findtext("size")),
                "periods": sorted(int(p.text) for p in c.findall("period")),
                "description": c.findtext("description") or "",
                "key": c.get("is_key") == "1",
                "array_labels": [x.text for x in c.findall("x_name")],
            }
        result[obj.findtext("name")] = counters
    return result


def differences(samples, info):
    unique = {}
    conflicts = 0
    for sample in samples:
        stamp = sample["timestamp_ms"]
        if stamp in unique and unique[stamp] != sample:
            conflicts += 1
        unique[stamp] = sample
    ordered = sorted(unique.values(), key=lambda x: x["timestamp_ms"])
    rows, rejected = [], collections.Counter()
    if conflicts:
        return [], {"identity_or_sample_conflicts": conflicts}
    for a, b in zip(ordered, ordered[1:]):
        if a["timestamp_ms"] < info["requested_start_ms"] or b["timestamp_ms"] > info["requested_end_ms"]:
            continue
        dt = (b["timestamp_ms"] - a["timestamp_ms"]) / 1000
        if dt <= 0 or dt > max(a["period_ms"], b["period_ms"]) / 1000 * 1.5:
            rejected["gap"] += 1
            continue
        numerator = b["raw_value"] - a["raw_value"]
        denominator = b["raw_base"] - a["raw_base"] if b["raw_base"] is not None else dt
        if numerator < 0 or denominator < 0:
            # Cannot distinguish reset vs wrap without an independently verified rollover.
            rejected["reset_or_wrap"] += 1
            continue
        if b["unit"] == "%" and denominator and numerator / denominator * b["scale"] > 100.001:
            rejected["implausible_percentage"] += 1
            continue
        rows.append({
            **{k: b[k] for k in ("object", "resource", "metric", "source_counter",
                                  "base_counter", "period_ms", "scale", "unit", "group")},
            "node": info["node"], "cluster": info["cluster"],
            "svm": b.get("svm", ""), "a": a["timestamp_ms"] / 1000,
            "b": b["timestamp_ms"] / 1000,
            "start": time_text(a["timestamp_ms"]), "end": time_text(b["timestamp_ms"]),
            "numerator": numerator, "denominator": denominator, "seconds": dt,
            "value": numerator / denominator * b["scale"] if denominator else None,
            "reason": "zero_base" if denominator == 0 else "",
        })
    return rows, dict(rejected)


def audit(source, output):
    info = headers(source)
    info.update(source_sha256=source_digest(source), audit_version=AUDIT_VERSION)
    dest = output / info["source_sha256"]
    manifest = dest / "audit.json"
    if manifest.exists() and (dest / "intervals.json.gz").exists() and (dest / "raw.json.gz").exists():
        cached = json.loads(manifest.read_text(encoding="utf-8"))
        if cached.get("audit_version") == AUDIT_VERSION:
            return cached
    dest.mkdir(parents=True, exist_ok=True)
    capabilities, raw_series, identities = {}, collections.defaultdict(list), {}
    archive = tarfile.open(fileobj=io.BytesIO(read_member(source, "performance-archives.tar")))
    for member in archive.getmembers():
        if not member.name.endswith(".ccma.meta"):
            continue
        group = member.name.split("/ccma/", 1)[1].rsplit("/", 1)[0]
        root = metadata_root(archive.extractfile(member).read())
        if root is None:
            raise ValueError(f"Unsupported metadata: {member.name}")
        defs = definitions(root)
        # Audit all process presets for candidate wait/cloud/protocol objects.
        selected = {
            obj for obj, cs in defs.items()
            if INTEREST.fullmatch(obj) or any(WAITS.search(c) for c in cs)
        }
        for obj in selected:
            for name, c in defs[obj].items():
                if c["key"] or c["ctype"] & 0x2000:
                    continue
                key = (group, obj, name)
                capabilities.setdefault(key, {
                    "group": group, "object": obj, "counter": name, **c,
                    "observations": 0, "nonzero_observations": 0,
                    "instances": set(), "observed_periods": set(),
                    "min": None, "max": None,
                    "rule": rule(obj, name, defs[obj]),
                })
        if not selected:
            continue
        verify_md5(archive, member.name)
        data = gzip.decompress(archive.extractfile(member.name[:-5] + ".gz").read())
        for row in records(root, data, selected):
            obj, raw, timestamp, period = row["object"], row["raw"], row["timestamp_ms"], row["period_ms"]
            resource = str(raw.get("instance_name") or raw.get("instance_uuid") or "")
            if not resource:
                keys = {k: raw.get(k) for k, d in defs[obj].items() if d["key"]}
                resource = json.dumps(keys, sort_keys=True, separators=(",", ":"))
                if not keys:
                    raise ValueError(f"No verified instance key: {obj}")
            # Resource identity is local to node + object; no workload-to-volume guessing.
            identity = identities.setdefault((obj, resource), {})
            for name in ("vserver_name", "vserver_uuid", "parent_host", "parent_aggr",
                         "instance_uuid", "data_object_name", "data_object_type",
                         "aggr_uuid", "flexgroup_uuid", "flexgroup_name", "nic_type"):
                if raw.get(name) not in (None, ""):
                    identity[name] = raw[name]
            for name, value in raw.items():
                cap = capabilities.get((group, obj, name))
                if cap is None:
                    continue
                if info["requested_start_ms"] <= timestamp <= info["requested_end_ms"] and period != 999999000:
                    if isinstance(value, int):
                        cap["observations"] += 1
                        cap["nonzero_observations"] += value != 0
                        cap["instances"].add(resource)
                        cap["observed_periods"].add(period)
                        cap["min"] = min(value, cap["min"]) if cap["min"] is not None else value
                        cap["max"] = max(value, cap["max"]) if cap["max"] is not None else value
                spec = cap["rule"]
                if (
                    not spec or group not in GROUPS or period == 999999000
                    or not isinstance(value, int)
                    or spec["base"] and not isinstance(raw.get(spec["base"]), int)
                ):
                    continue
                raw_series[(group, obj, resource, name, period)].append({
                    "timestamp_ms": timestamp, "period_ms": period,
                    "object": obj, "resource": resource, "svm": raw.get("vserver_name", ""),
                    "metric": name, "source_counter": name, "base_counter": spec["base"],
                    "raw_value": value, "raw_base": raw.get(spec["base"]),
                    "scale": spec["scale"], "unit": spec["unit"], "group": group,
                })
        del data
    intervals, raw_samples, diagnostics = [], [], []
    for (group, obj, resource, metric, period), samples in sorted(raw_series.items()):
        raw_only = capabilities[(group, obj, metric)]["rule"]["mode"] == "raw"
        rows, rejected = ([], {}) if raw_only else differences(samples, info)
        identity = identities.get((obj, resource), {})
        for row in rows:
            row["svm"] = identity.get("vserver_name", row["svm"])
        intervals.extend(rows)
        raw_samples.extend(s for s in samples if info["requested_start_ms"] <= s["timestamp_ms"] <= info["requested_end_ms"])
        if rejected:
            diagnostics.append({
                "node": info["node"], "group": group, "object": obj,
                "resource": resource, "counter": metric, "period_ms": period, **rejected,
            })
    # Definitions, observations and valid deltas are deliberately separate.
    valid_counts = collections.Counter((r["group"], r["object"], r["metric"]) for r in intervals if r["value"] is not None)
    for key, cap in capabilities.items():
        cap["instances"] = sorted(cap["instances"])
        cap["observed_periods"] = sorted(cap["observed_periods"])
        cap["valid_intervals"] = valid_counts[key]
        cap["schema_hash"] = hashlib.sha256(json.dumps(
            {k: cap[k] for k in ("object", "counter", "ctype", "size", "base_id", "periods", "description", "array_labels")},
            sort_keys=True).encode()).hexdigest()
        cap["capability"] = (
            "已存在且可直接计算" if cap["valid_intervals"] else
            "已存在但需要补充解析" if cap["size"] not in (4, 8) or not cap["rule"] else
            "存在定义但缺少有效数据"
        )
    info.update(
        capabilities=list(capabilities.values()), diagnostics=diagnostics,
        identities=[{"object": obj, "resource": res, **details}
                    for (obj, res), details in sorted(identities.items())],
        interval_count=len(intervals), raw_sample_count=len(raw_samples),
    )
    for name, rows in [("intervals", intervals), ("raw", raw_samples)]:
        with gzip.open(dest / f"{name}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False)
    temporary = dest / "audit.pending.json"
    temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest)
    print("AUDITED", info["node"], len(capabilities), len(intervals), flush=True)
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--match", default="**/*.7z")
    args = parser.parse_args()
    summaries = []
    for source in sorted(args.source.glob(args.match)):
        result = audit(source, args.output)
        summaries.append({k: result[k] for k in ("file", "node", "cluster", "source_sha256", "interval_count")})
    (args.output / "index.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
