"""Experimental PA decoding validation for the supplied log batch."""

import argparse
import collections
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import tarfile
import uuid

from pa_probe import SEVEN_ZIP, metadata_root, records


TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).parent
TARGETS = {"system", "aggregate", "volume", "resource_headroom_aggr", "nic_common"}
GROUPS = {"kernel", "kernel/opm", "kernel/asup"}
COLLECTOR_VERSION = 3

# Metric calculation rules use each file's own schema and base counter.
SPECS = {
    "system": [
        ("cpu_busy_pct", "kernel", "cpu_busy", "cpu_elapsed_time", 100),
        ("avg_processor_busy_pct", "kernel", "avg_processor_busy",
         "cpu_elapsed_time", 100),
        ("iops", "kernel/opm", "total_ops", None, 1),
        ("read_iops", "kernel/opm", "read_ops", None, 1),
        ("write_iops", "kernel/opm", "write_ops", None, 1),
        ("read_mib_s", "kernel/opm", "read_data", None, 1 / 2**20),
        ("write_mib_s", "kernel/opm", "write_data", None, 1 / 2**20),
        ("throughput_mib_s", "kernel/opm", "total_data", None, 1 / 2**20),
        ("latency_ms", "kernel/opm", "total_latency", "total_ops", 0.001),
        ("read_latency_ms", "kernel/opm", "read_latency", "read_ops", 0.001),
        ("write_latency_ms", "kernel/opm", "write_latency", "write_ops", 0.001),
    ],
    "aggregate": [
        ("iops", "kernel/opm", "total_transfers", None, 1),
        ("read_iops", "kernel/opm", "user_reads", None, 1),
        ("write_iops", "kernel/opm", "user_writes", None, 1),
        ("read_mib_s", "kernel/asup", "read_data", None, 1 / 2**20),
        ("write_mib_s", "kernel/asup", "write_data", None, 1 / 2**20),
        ("latency_ms", "kernel/asup", "latency", "total_transfers", 0.001),
        ("read_latency_per_block_ms", "kernel/opm", "user_read_latency",
         "user_read_blocks", 0.001),
        ("write_latency_per_block_ms", "kernel/opm", "user_write_latency",
         "user_write_blocks", 0.001),
    ],
    "resource_headroom_aggr": [
        ("disk_utilization_pct", "kernel/opm", "current_utilization",
         "current_utilization_total", 100),
        ("disk_iops", "kernel/opm", "current_ops", None, 1),
        ("disk_latency_ms", "kernel/opm", "current_latency", "current_ops", 0.001),
    ],
    "volume": [
        ("iops", "kernel/opm", "total_ops", None, 1),
        ("read_iops", "kernel/opm", "read_ops", None, 1),
        ("write_iops", "kernel", "write_ops", None, 1),
        ("read_mib_s", "kernel", "read_data", None, 1 / 2**20),
        ("write_mib_s", "kernel", "write_data", None, 1 / 2**20),
        ("latency_ms", "kernel/opm", "avg_latency", "total_ops", 0.001),
        ("read_latency_ms", "kernel/opm", "read_latency", "read_ops", 0.001),
        ("write_latency_ms", "kernel", "write_latency", "write_ops", 0.001),
    ],
    "nic_common": [
        ("rx_mib_s", "kernel/opm", "rx_bytes", None, 1 / 2**20),
        ("tx_mib_s", "kernel/opm", "tx_bytes", None, 1 / 2**20),
        ("throughput_mib_s", "kernel/opm", "total_bytes", None, 1 / 2**20),
    ],
}


def read_member(source, name):
    return subprocess.check_output([
        str(SEVEN_ZIP), "x", "-so", str(source), name
    ])


def source_digest(source):
    with source.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def headers(source):
    text = read_member(source, "X-HEADER-DATA.TXT").decode()
    fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)
    subject = fields["X-Netapp-asup-subject"]
    match = re.search(
        r"UPLOAD: (\d+/\d+/\d+ \d+:\d+:\d+) to "
        r"(\d+/\d+/\d+ \d+:\d+:\d+)", subject
    )
    if not match:
        raise ValueError(f"Unrecognized requested window: {subject}")
    offset = re.search(r" ([+-])(\d{2})(\d{2}) ", fields["X-Netapp-asup-generated-on"])
    if offset is None:
        raise ValueError("Missing log timezone; refusing to assume a sampling window")
    minutes = (int(offset[2]) * 60 + int(offset[3])) * (1 if offset[1] == "+" else -1)
    source_tz = timezone(timedelta(minutes=minutes))
    start, end = [
        int(datetime.strptime(s, "%m/%d/%Y %H:%M:%S").replace(
            tzinfo=source_tz
        ).timestamp() * 1000)
        for s in match.groups()
    ]
    return {
        "file": source.name,
        "node": fields["X-Netapp-asup-hostname"],
        "cluster": fields["X-Netapp-asup-cluster-name"],
        "cluster_uuid": fields.get("X-Netapp-asup-cluster-uuid"),
        "version": fields["X-Netapp-asup-os-version"],
        "model": fields["X-Netapp-asup-model-name"],
        "serial_number": fields["X-Netapp-asup-serial-num"],
        "source_timezone": str(source_tz),
        "requested_start_ms": start, "requested_end_ms": end,
    }


def resource_key(obj, raw):
    if obj == "resource_headroom_aggr":
        return (
            obj,
            str(uuid.UUID(bytes_le=struct.pack(
                "<QQ", raw["archive_uuid_hi"], raw["archive_uuid_lo"]
            ))),
            str(raw["resource_type"]),
        )
    return obj, raw.get("vserver_name", ""), raw["instance_name"]


def time_text(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).isoformat()


def verify_md5(archive, meta_name):
    lines = archive.extractfile(meta_name[:-5] + ".md5").read().decode().splitlines()
    for line in lines:
        digest, name = line.split(maxsplit=1)
        target = str(PurePosixPath(meta_name).parent / name.lstrip("*"))
        actual = hashlib.md5(archive.extractfile(target).read()).hexdigest()
        if actual != digest:
            raise ValueError(f"MD5 mismatch: {target}")


def parse_archive(source, output):
    info = headers(source)
    info["source_sha256"] = source_digest(source)
    info["collector_version"] = COLLECTOR_VERSION
    print("START", source.name, info["node"], flush=True)
    inventory = {}
    series = collections.defaultdict(list)
    stats = collections.Counter()
    formats = set()
    port_links = []
    gauges = {}
    tar_data = read_member(source, "performance-archives.tar")
    tar = tarfile.open(fileobj=io.BytesIO(tar_data))
    members = tar.getmembers()
    for member in members:
        if not member.name.endswith(".ccma.meta"):
            continue
        group = member.name.split("/ccma/", 1)[1].rsplit("/", 1)[0]
        if group not in GROUPS:
            continue
        verify_md5(tar, member.name)
        stats["verified_files"] += 2
        root = metadata_root(tar.extractfile(member).read())
        if root is None:
            raise ValueError(f"Unknown metadata: {member.name}")
        formats.add(root.get("version"))
        if root.findtext("system/hostname") != info["node"]:
            raise ValueError("Header and metadata node identities disagree")
        node_uuid = root.findtext("system/node_uuid")
        if info.get("node_uuid") and info["node_uuid"] != node_uuid:
            raise ValueError("Conflicting node UUIDs")
        info["node_uuid"] = node_uuid
        schema_by_name = {
            o.findtext("name"): {
                c.findtext("name"): c for c in o.findall("counter")
            }
            for o in root.findall("preset/object")
        }
        compressed = tar.extractfile(member.name[:-5] + ".gz").read()
        data = gzip.decompress(compressed)
        stats["decoded_files"] += 1
        stats["uncompressed_bytes"] += len(data)
        for row in records(root, data, TARGETS, stats):
            obj, raw, timestamp, period = (
                row["object"], row["raw"], row["timestamp_ms"], row["period_ms"]
            )
            key = resource_key(obj, raw)
            detail = inventory.setdefault(key, {})
            for name in [
                "instance_name", "instance_uuid", "parent_aggr", "parent_host",
                "vserver_name", "vserver_uuid", "flexgroup_msid",
            ]:
                if raw.get(name) not in (None, ""):
                    detail[name] = raw[name]
            if obj == "resource_headroom_aggr":
                detail["instance_uuid"] = key[1]
                detail["disk_type"] = "HDD" if key[2] == "0" else "SSD"
            if (
                obj == "volume" and group == "kernel"
                and period != 999999000 and "wvblk_zombie_blks" in raw
                and info["requested_start_ms"] <= timestamp <= info["requested_end_ms"]
            ):
                value = raw["wvblk_zombie_blks"]
                if not isinstance(value, int):
                    raise ValueError("Non-scalar zombie block gauge")
                gauge_key = (key, timestamp)
                sample = {
                    "object": obj, "resource": raw["instance_name"],
                    "svm": raw.get("vserver_name", ""),
                    "metric": "wvblk_zombie_blks", "timestamp_ms": timestamp,
                    "period_ms": period, "value": value,
                }
                if gauge_key in gauges and gauges[gauge_key] != sample:
                    raise ValueError(f"Conflicting zombie snapshots: {gauge_key}")
                gauges[gauge_key] = sample
            if obj == "nic_common":
                link = {
                    name: raw[name] for name in [
                        "link_speed", "link_current_state", "link_media_state",
                        "link_duplex", "nic_type",
                    ] if name in raw
                }
                if link:
                    port_links.append({
                        "port": raw["instance_name"], "timestamp_ms": timestamp,
                        **link,
                    })
                if group == "kernel/opm" and all(
                    name in raw for name in ("rx_bytes", "tx_bytes", "total_bytes")
                ):
                    if raw["rx_bytes"] + raw["tx_bytes"] != raw["total_bytes"]:
                        raise ValueError("NIC byte counter invariant failed")
                    stats["nic_counter_identity_checks"] += 1
            for metric, expected_group, counter, base, scale in SPECS[obj]:
                if group != expected_group or period == 999999000:
                    continue
                if counter not in raw or base is not None and base not in raw:
                    continue
                # Reject rule drift when a new ONTAP schema changes a dependency.
                definition = schema_by_name[obj][counter]
                if base:
                    base_def = schema_by_name[obj][base]
                    if definition.findtext("base_id") != base_def.findtext("id"):
                        raise ValueError(f"Unexpected metric dependency: {obj}.{counter}")
                value = raw[counter]
                denominator = raw[base] if base else None
                if not isinstance(value, int) or base and not isinstance(denominator, int):
                    raise ValueError(f"Non-scalar metric: {obj}.{counter}")
                series[(key, metric)].append((
                    timestamp, value, denominator, period, counter, base, scale
                ))
            if obj == "system" and group == "kernel/opm" and "total_ops" in raw:
                if raw["read_ops"] + raw["write_ops"] + raw["other_ops"] != raw["total_ops"]:
                    raise ValueError("Node operation count invariant failed")
                if raw["read_data"] + raw["write_data"] + raw["other_data"] != raw["total_data"]:
                    raise ValueError("Node byte count invariant failed")
                stats["node_counter_identity_checks"] += 2
        del data
    aggregate_names = {
        details["instance_uuid"]: details["instance_name"]
        for key, details in inventory.items()
        if key[0] == "aggregate" and details.get("instance_uuid")
    }
    results = []
    intervals = []
    for (key, metric), samples in sorted(series.items()):
        samples.sort()
        # Rotated files can repeat a boundary sample. Conflicts must be visible.
        unique = {}
        for sample in samples:
            if sample[0] in unique and unique[sample[0]][1:3] != sample[1:3]:
                raise ValueError(f"Conflicting samples: {key} {metric}")
            unique[sample[0]] = sample
        samples = sorted(unique.values())
        values = []
        sum_numerator = 0
        sum_denominator = 0
        covered_seconds = 0
        periods = set()
        first, last = None, None
        zero_base_intervals = 0
        for a, b in zip(samples, samples[1:]):
            if a[0] < info["requested_start_ms"] or b[0] > info["requested_end_ms"]:
                continue
            seconds = (b[0] - a[0]) / 1000
            if seconds <= 0 or seconds > max(a[3], b[3]) / 1000 * 1.5:
                stats["rejected_gap_intervals"] += 1
                continue
            delta = b[1] - a[1]
            denominator = b[2] - a[2] if b[2] is not None else seconds
            if delta < 0 or denominator < 0:
                stats["rejected_reset_intervals"] += 1
                continue
            interval = {
                "node": info["node"], "cluster": info["cluster"],
                "object": key[0],
                "resource": key[2] if key[0] != "resource_headroom_aggr" else key[1],
                "svm": key[1] if key[0] == "volume" else "",
                "metric": metric, "start": time_text(a[0]), "end": time_text(b[0]),
                "source_counter": b[4], "base_counter": b[5], "period_ms": b[3],
                "numerator": delta, "denominator": denominator,
                "scale": b[6], "seconds": seconds,
            }
            if denominator == 0:
                zero_base_intervals += 1
                intervals.append({**interval, "value": None, "reason": "zero_base"})
                continue
            value = delta / denominator * b[6]
            if metric.endswith("_pct") and not 0 <= value <= 100.001:
                raise ValueError(f"Invalid percentage: {key} {metric} {value}")
            values.append(value)
            sum_numerator += delta
            sum_denominator += denominator
            covered_seconds += seconds
            periods.add(b[3])
            first = a[0] if first is None else min(first, a[0])
            last = b[0] if last is None else max(last, b[0])
            intervals.append({**interval, "value": value})
        details = inventory[key]
        name = details.get("instance_name", key[2])
        if key[0] == "resource_headroom_aggr":
            name = aggregate_names.get(key[1])
        results.append({
            "cluster": info["cluster"], "node": info["node"],
            "object": key[0], "resource": name,
            "uuid": details.get("instance_uuid"),
            "svm": details.get("vserver_name", ""),
            "aggr": details.get("parent_aggr", ""),
            "disk_type": details.get("disk_type"),
            "metric": metric,
            "avg": sum_numerator / sum_denominator * samples[0][6] if sum_denominator else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "valid_intervals": len(values), "zero_base_intervals": zero_base_intervals,
            "covered_seconds": covered_seconds, "period_ms": sorted(periods),
            "start": time_text(first) if first else None,
            "end": time_text(last) if last else None,
            "counter": samples[0][4], "base": samples[0][5],
            "sum_numerator": sum_numerator, "sum_denominator": sum_denominator,
            "native_period_ms": sorted({s[3] for s in samples}),
            "raw_samples_in_window": sum(
                info["requested_start_ms"] <= s[0] <= info["requested_end_ms"]
                for s in samples
            ),
            "raw_sample_times": [
                time_text(s[0]) for s in samples
                if info["requested_start_ms"] <= s[0] <= info["requested_end_ms"]
            ],
        })
    info["formats"] = sorted(formats)
    info["counts"] = dict(collections.Counter(key[0] for key in inventory))
    info["stats"] = dict(stats)
    info["status"] = "decoded_experimental_not_vendor_calibrated"
    info["inventory"] = [
        {"object": key[0], **value} for key, value in sorted(inventory.items())
    ]
    info["metrics"] = results
    info["port_link_samples"] = sorted(
        port_links, key=lambda r: (r["port"], r["timestamp_ms"])
    )
    info["gauge_samples"] = sorted(
        gauges.values(), key=lambda r: (r["svm"], r["resource"], r["timestamp_ms"])
    )
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{info['node']}.json"
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    with gzip.open(output / f"{info['node']}.samples.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(intervals, handle, ensure_ascii=False)
    print("DONE", info["node"], info["counts"], info["stats"], flush=True)
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "pa_validation")
    parser.add_argument("--match", default="*.7z")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    batch = []
    for source in sorted(args.source.glob(args.match)):
        try:
            info = headers(source) if args.resume else None
            saved = args.output / f"{info['node']}.json" if info else None
            cached = json.loads(saved.read_text(encoding="utf-8")) if saved and saved.exists() else None
            if cached and (
                cached.get("collector_version") == COLLECTOR_VERSION
                and
                cached.get("file") == source.name
                and cached.get("requested_start_ms") == info["requested_start_ms"]
                and cached.get("requested_end_ms") == info["requested_end_ms"]
                and cached.get("source_sha256") == source_digest(source)
            ):
                info = cached
                print("CACHED", info["node"], flush=True)
            else:
                info = parse_archive(source, args.output)
            batch.append(info)
        except Exception as error:
            batch.append({"file": source.name, "status": "failed", "error": repr(error)})
            print("FAILED", source.name, repr(error), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "batch.json").write_text(
        json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rows = [metric for node in batch for metric in node.get("metrics", [])]
    if rows:
        with (args.output / "resource_metrics.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print("BATCH_STATUS", collections.Counter(n["status"] for n in batch), flush=True)


if __name__ == "__main__":
    main()
