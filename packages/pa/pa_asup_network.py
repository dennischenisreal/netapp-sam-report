"""Use explicit ASUP port/LIF/IFGRP configuration to describe PA ports."""

import argparse
import collections
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from validate_pa import read_member, source_digest


def rows(raw):
    root = ET.fromstring(raw)
    result = []
    for row in root.iter():
        if row.tag.rsplit("}", 1)[-1] != "ROW":
            continue
        result.append({
            c.tag.rsplit("}", 1)[-1]: (
                [li.text for li in c.iter() if li.tag.rsplit("}", 1)[-1] == "li" and li.text]
                if list(c) else c.text or ""
            ) for c in row
        })
    return root.attrib, result


def purposes(lif):
    values = set()
    role = lif.get("role")
    if role in ("cluster", "cluster-mgmt", "node-mgmt", "intercluster"):
        values.add({"cluster": "集群互联", "cluster-mgmt": "集群管理",
                    "node-mgmt": "节点管理", "intercluster": "集群间通信"}[role])
    services = set(lif.get("services", []))
    protocols = set(lif.get("data_protocol", []))
    if "data-nfs" in services or "nfs" in protocols:
        values.add("NFS数据")
    if "data-cifs" in services or "cifs" in protocols:
        values.add("CIFS数据")
    if "data-iscsi" in services or "iscsi" in protocols:
        values.add("iSCSI数据")
    if "cluster-core" in services:
        values.add("集群互联")
    if "intercluster-core" in services:
        values.add("集群间通信")
    if role == "data" and not values:
        values.add("数据")
    return values


def describe(ports, lifs, groups, node):
    assignments = collections.defaultdict(set)
    for lif in lifs:
        # Current placement is a configuration snapshot, not historical PA traffic.
        if lif.get("curr_node") == node and lif.get("curr_port"):
            assignments[lif["curr_port"]].update(purposes(lif))
    membership = {}
    for group in groups:
        if group.get("node") == node:
            for member in group.get("ports", []):
                membership[member] = group["ifgrp-name"]
    for port in ports:
        if port.get("node") == node and port.get("ifgrp"):
            # T_PORT may append '*' to an active IFGRP. The explicit member
            # list in T_IFGRP is authoritative and must not be overwritten.
            membership.setdefault(port["port"], port["ifgrp"])
    result = {}
    for port in ports:
        if port.get("node") != node:
            continue
        name = port["port"]
        uses = set(assignments[name])
        parent = membership.get(name)
        if parent:
            uses |= assignments[parent]
        role = port.get("role", "")
        explicit = {"data": "数据", "cluster": "集群互联", "node-mgmt": "节点管理",
                    "cluster-mgmt": "集群管理", "intercluster": "集群间通信"}
        result[name] = {
            "role": f"{role}（{explicit[role]}）" if role in explicit else role or "未知",
            "role_raw": role,
            "lif_services": " / ".join(sorted(uses)),
            "ifgrp": parent or "",
            "port_type": port.get("type", ""),
            "role_method": "network-ports.xml:role（与SAM端口Role同源，不以LIF协议推断）",
        }
    return result


def collect(batch, source, output):
    output.mkdir(parents=True, exist_ok=True)
    index = collections.defaultdict(list)
    for path in sorted(source.glob("*.7z")):
        header = dict(line.split(": ", 1) for line in
                      read_member(path, "X-HEADER-DATA.TXT").decode().splitlines() if ": " in line)
        stamp = datetime.strptime(header["X-Netapp-asup-generated-on"], "%a %b %d %H:%M:%S %z %Y")
        index[header["X-Netapp-asup-hostname"]].append((path, header, stamp.timestamp() * 1000))
    result, sources = {}, []
    for info in batch:
        candidates = [entry for entry in index[info["node"]]
                      if entry[1].get("X-Netapp-asup-serial-num") == info["serial_number"]
                      and entry[1].get("X-Netapp-asup-cluster-uuid") == info["cluster_uuid"]]
        if not candidates:
            print("ASUP_NOT_MATCHED", info["node"], flush=True)
            continue
        path, header, stamp = min(candidates, key=lambda entry: abs(entry[2] - info["requested_end_ms"]))
        digest = source_digest(path)
        cache = output / (digest + ".v4.json")
        if cache.exists():
            value = json.loads(cache.read_text(encoding="utf-8"))
        else:
            decoded = {}
            for name in ("network-ports.xml", "network-interface.xml", "ifgrps.xml"):
                raw = read_member(path, name)
                if not raw.strip():
                    decoded[name] = []
                    continue
                attrs, records = rows(raw)
                if attrs.get("node_sn") != info["serial_number"] or attrs.get("cluster_uuid") != info["cluster_uuid"]:
                    raise ValueError(f"ASUP XML identity mismatch: {path.name}:{name}")
                decoded[name] = records
            value = describe(decoded["network-ports.xml"], decoded["network-interface.xml"],
                             decoded["ifgrps.xml"], info["node"])
            cache.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for data in value.values():
            data.update(role_source=f"{path.name}；{data['role_method']}",
                        asup_date=stamp, asup_offset_days=(stamp - info["requested_end_ms"]) / 86400000)
        result[info["node"]] = value
        sources.append({"type": "ASUP网络配置快照", "file": path.name, "node": info["node"],
                        "start": stamp, "end": stamp, "sha256": digest})
        print("ASUP_MATCHED", info["node"], len(value), flush=True)
    artifact = {"ports": result, "sources": sources}
    (output / "network_roles.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(json.loads((args.baseline / "data/batch.json").read_text(encoding="utf-8")),
            args.source, args.output)


if __name__ == "__main__":
    main()
