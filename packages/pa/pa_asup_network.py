"""Use explicit ASUP port/LIF/IFGRP configuration to describe PA ports."""

import argparse
import collections
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from validate_pa import read_member, source_digest

PURPOSE_LABELS = {
    "data": "业务口", "cluster": "集群互联", "intercluster": "集群间通信",
    "node-mgmt": "节点管理", "cluster-mgmt": "集群管理",
}
PURPOSE_ORDER = ("data", "cluster", "intercluster", "node-mgmt", "cluster-mgmt")


def items(value):
    return value if isinstance(value, list) else [value] if value else []


def lif_roles(lif):
    services, protocols = set(items(lif.get("services"))), set(items(lif.get("data_protocol")))
    result = {lif["role"]} if lif.get("role") in PURPOSE_LABELS else set()
    if "cluster-core" in services:
        result.add("cluster")
    if "intercluster-core" in services:
        result.add("intercluster")
    if services & {"data-core", "data-nfs", "data-cifs", "data-iscsi",
                   "data-s3-server", "data-flexcache", "data-nvme-tcp"} or (
            protocols - {"none", "N/A", "-"}):
        result.add("data")
    return result


def purpose_label(roles):
    if not roles:
        return "未知"
    return " + ".join(f"{r}（{PURPOSE_LABELS[r]}）" for r in roles) + (
        "（共用）" if len(roles) > 1 else "")


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
    services = set(items(lif.get("services")))
    protocols = set(items(lif.get("data_protocol")))
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
    by_port = {p["port"]: p for p in ports if p.get("node") == node}
    assignments = collections.defaultdict(list)
    for lif in lifs:
        # Current placement is a configuration snapshot, not historical PA traffic.
        if lif.get("curr_node") == node and lif.get("curr_port"):
            assignments[lif["curr_port"]].append(lif)
    membership = {}
    for group in groups:
        if group.get("node") == node:
            for member in items(group.get("ports")):
                if member in membership and membership[member] != group["ifgrp-name"]:
                    raise ValueError(f"Conflicting IFGRP membership: {node}:{member}")
                membership[member] = group["ifgrp-name"]
    for port in by_port.values():
        owner = port.get("ifgrp", "").rstrip("*")
        if owner:
            # T_PORT may append '*' to an active IFGRP. The explicit member
            # list in T_IFGRP is authoritative and must not be overwritten.
            membership.setdefault(port["port"], owner)
    members = collections.defaultdict(set)
    for member, group in membership.items():
        members[group].add(member)
    vlan_bases = {}
    # Only an explicitly recorded VLAN can be traced through its named base.
    for name, port in by_port.items():
        base, separator, tag = name.rpartition("-")
        if port.get("type") == "vlan" and separator and tag.isdigit() and (
                base in by_port or base in membership.values()):
            vlan_bases[name] = base

    def backing_ports(endpoint, trail=()):
        if endpoint in trail:
            raise ValueError(f"Cyclic port topology: {node}:{endpoint}")
        targets = {endpoint}
        children = members[endpoint] | ({vlan_bases[endpoint]} if endpoint in vlan_bases else set())
        for child in children:
            targets.update(backing_ports(child, (*trail, endpoint)))
        return targets

    mapped = collections.defaultdict(list)
    for endpoint, attached in assignments.items():
        for target in backing_ports(endpoint):
            mapped[target].extend(attached)
    result = {}
    for name in sorted(set(by_port) | set(mapped) | set(membership) | set(members)):
        port = by_port.get(name, {})
        attached = mapped[name]
        uses = set().union(*(purposes(lif) for lif in attached))
        categories = set().union(*(lif_roles(lif) for lif in attached))
        parent = membership.get(name)
        role = port.get("role", "")
        if role == "cluster":
            categories = {"cluster"}
        elif not categories and role in PURPOSE_LABELS:
            # A generic data Role is not evidence that a business LIF exists.
            categories = {role} if role != "data" else set()
        roles = [r for r in PURPOSE_ORDER if r in categories]
        label = purpose_label(roles)
        if not roles and role == "data":
            label = "data（用途未确认）"
        result[name] = {
            "role": label,
            "role_raw": role,
            "purpose_roles": roles,
            "lif_services": " / ".join(sorted(uses)),
            "ifgrp": parent or "",
            "port_type": port.get("type", ""),
            "lif_evidence": sorted([
                {k: lif.get(k, "") for k in ("vserver", "vif", "address", "role", "curr_node", "curr_port")}
                for lif in attached], key=lambda lif: (lif["vserver"], lif["vif"], lif["address"])),
            "role_method": "network-interface.xml:当前LIF角色/服务 + ifgrp(s).xml:成员"
                           " + network-ports.xml:物理Role/类型；共用口不拆分流量",
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
        cache = output / (digest + ".v5-purpose.json")
        if cache.exists():
            value = json.loads(cache.read_text(encoding="utf-8"))
        else:
            decoded = {}
            for name in ("network-ports.xml", "network-interface.xml", "ifgrps.xml"):
                raw = read_member(path, name)
                if not raw.strip() and name == "ifgrps.xml":
                    raw = read_member(path, "ifgrp.xml")
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
