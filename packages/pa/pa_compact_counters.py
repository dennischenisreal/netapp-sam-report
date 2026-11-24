"""Focused, source-verified counters for the consolidated PA workbook."""

import argparse
import collections
from concurrent.futures import ProcessPoolExecutor
import gzip
import io
import json
from pathlib import Path
import tarfile

from pa_capabilities import definitions, differences, rule
from pa_probe import metadata_root, records
from validate_pa import read_member, source_digest, verify_md5

VERSION = 1
GROUPS = {"kernel", "kernel/opm", "kernel/asup"}
ARRAYS = {("object_store_client_op_stats", c)
          for c in ("stats", "ops", "average_latency")}


def rules(obj, cs):
    result = {}
    ids = {c["id"]: n for n, c in cs.items()}
    for name, c in cs.items():
        if c["key"] or c["ctype"] & 0x2000:
            continue
        spec = None
        if obj in ("volume", "nic_common", "nfsv3", "workload_volume"):
            spec = rule(obj, name, cs)
        if obj in ("smb1", "smb2"):
            if name in ("ops", "read_class_ops", "write_class_ops", "other_ops") or name.endswith("_latency_base"):
                spec = dict(base=None, scale=1, unit="ops/s", mode="rate")
            elif name in ("read_data", "write_data"):
                spec = dict(base=None, scale=1 / 2**20, unit="MiB/s", mode="rate")
            elif (name == "latency" or name.endswith("_latency")) and not c["array_labels"]:
                base = ids.get(c["base_id"])
                if base:
                    spec = dict(base=base, scale=.001, unit="ms", mode="rate")
        if obj in ("nfsv4", "nfsv4_1") and not c["array_labels"]:
            if name.endswith("_total") or name in ("nfsv4_ops", "nfsv4_1_ops", "nfs4_ops", "nfs41_ops"):
                spec = dict(base=None, scale=1, unit="ops/s", mode="rate")
            elif name.endswith("_avg_latency") and ids.get(c["base_id"]):
                spec = dict(base=ids[c["base_id"]], scale=.001, unit="ms", mode="rate")
            elif name.endswith(("_read_throughput", "_write_throughput")):
                spec = dict(base=None, scale=1 / 2**20, unit="MiB/s", mode="rate")
        if obj == "object_store_client_op_stats":
            if name == "throughput_bytes":
                spec = dict(base=None, scale=1 / 2**20, unit="MiB/s", mode="rate")
            elif name == "stats":
                result["stats.total"] = dict(base=None, scale=1, unit="ops/s", mode="rate")
                continue
            elif name == "average_latency":
                base = ids.get(c["base_id"])
                if base == "ops" and cs[base]["array_labels"] == c["array_labels"]:
                    result["average_latency.total-latency"] = dict(
                        base="ops.total-latency", scale=.001, unit="ms", mode="rate")
                continue
        if spec and spec["mode"] != "raw" and not c["array_labels"]:
            result[name] = spec
    return result


def flatten(raw):
    return {key if not isinstance(value, dict) else key + "." + label: number
            for key, value in raw.items()
            for label, number in (value.items() if isinstance(value, dict) else [("", value)])}


def collect(source, info, output):
    dest = output / info["source_sha256"]
    manifest = dest / "audit.json"
    if source_digest(source) != info["source_sha256"]:
        raise ValueError(f"Source changed: {source}")
    if manifest.exists():
        cached = json.loads(manifest.read_text(encoding="utf-8"))
        if (cached.get("compact_version") == VERSION
                and cached.get("source_sha256") == info["source_sha256"]
                and cached.get("node_uuid") == info["node_uuid"]
                and (dest / "intervals.json.gz").is_file()):
            return cached
    print("COMPACT_START", info["node"], flush=True)
    archive = tarfile.open(fileobj=io.BytesIO(read_member(source, "performance-archives.tar")))
    series, identities, caps = collections.defaultdict(list), {}, []
    selected_objects = {"volume", "nic_common", "nfsv3", "nfsv4", "nfsv4_1",
                        "smb1", "smb2", "workload_volume", "object_store_client_op_stats"}
    for member in archive.getmembers():
        if not member.name.endswith(".ccma.meta"):
            continue
        group = member.name.split("/ccma/", 1)[1].rsplit("/", 1)[0]
        if group not in GROUPS:
            continue
        root = metadata_root(archive.extractfile(member).read())
        if root.findtext("system/hostname") != info["node"] or (
            info.get("node_uuid") and root.findtext("system/node_uuid") != info["node_uuid"]
        ):
            raise ValueError("PA metadata identity does not match baseline")
        defs = definitions(root)
        specs = {o: rules(o, cs) for o, cs in defs.items() if o in selected_objects}
        specs = {o: s for o, s in specs.items() if s}
        names = {(o, m.split(".", 1)[0]) for o, ss in specs.items() for m in ss}
        names |= {(o, s["base"].split(".", 1)[0]) for o, ss in specs.items()
                  for s in ss.values() if s["base"]}
        names |= {(o, n) for o in specs for n in (
            "vserver_name", "vserver_uuid", "nic_type", "data_object_name", "data_object_type"
        ) if n in defs[o]}
        for obj, ss in specs.items():
            for metric, spec in ss.items():
                caps.append({"object": obj, "counter": metric, "group": group, "rule": spec,
                             "description": defs[obj][metric.split(".", 1)[0]]["description"]})
        if not specs:
            continue
        verify_md5(archive, member.name)
        data = gzip.decompress(archive.extractfile(member.name[:-5] + ".gz").read())
        for row in records(root, data, set(specs), array_counters=ARRAYS & names, counter_names=names):
            obj, raw = row["object"], flatten(row["raw"])
            if row["period_ms"] == 999999000:
                continue
            resource = str(raw.get("instance_name") or raw.get("instance_uuid") or "")
            keys = {k: raw.get(k) for k, d in defs[obj].items() if d["key"]}
            if not resource:
                if not keys:
                    raise ValueError(f"No instance key for {obj}")
                resource = json.dumps(keys, sort_keys=True, separators=(",", ":"))
            identity = identities.setdefault((obj, resource), {})
            identity.update({k: v for k, v in keys.items() if v not in (None, "")})
            for k in ("vserver_name", "vserver_uuid", "nic_type", "data_object_name", "data_object_type"):
                if raw.get(k) not in (None, ""):
                    identity[k] = raw[k]
            for metric, spec in specs[obj].items():
                value, base = raw.get(metric), raw.get(spec["base"])
                if not isinstance(value, int) or spec["base"] and not isinstance(base, int):
                    continue
                series[(group, obj, resource, metric, row["period_ms"])].append({
                    "timestamp_ms": row["timestamp_ms"], "period_ms": row["period_ms"],
                    "object": obj, "resource": resource, "svm": raw.get("vserver_name", ""),
                    "metric": metric, "source_counter": metric, "base_counter": spec["base"],
                    "raw_value": value, "raw_base": base, "scale": spec["scale"],
                    "unit": spec["unit"], "group": group,
                })
        del data
    intervals, diagnostics = [], []
    for key, samples in series.items():
        rows, rejected = differences(samples, info)
        ident = identities[(key[1], key[2])]
        for row in rows:
            row["svm"] = ident.get("vserver_name", row["svm"])
        intervals.extend(rows)
        if rejected:
            diagnostics.append({"key": list(key), **rejected})
    dest.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest / "intervals.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(intervals, handle, ensure_ascii=False)
    result = {k: info[k] for k in ("node", "cluster", "node_uuid", "source_sha256", "version", "file")}
    result.update(compact_version=VERSION, capabilities=caps, diagnostics=diagnostics,
                  identities=[{"object": o, "resource": r, **d} for (o, r), d in identities.items()],
                  interval_count=len(intervals))
    pending = dest / "audit.pending.json"
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(manifest)
    print("COMPACT_DONE", info["node"], len(intervals), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    batch = json.loads((args.baseline / "data/batch.json").read_text(encoding="utf-8"))
    sources = {p.name: p for p in args.source.rglob("*.7z")}
    selected = batch[:args.limit]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(collect, sources[info["file"]], info, args.output) for info in selected]
        for job in jobs:
            job.result()


if __name__ == "__main__":
    main()
