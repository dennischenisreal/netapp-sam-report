"""Read-only PA format investigation. Outputs stay in the project cache."""

import argparse
import collections
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import os
import shutil
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone


SEVEN_ZIP = Path(os.environ.get("PA_7ZIP") or shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe")
CACHE = Path(__file__).parent / ".pa_probe_cache"
TARGETS = {
    "system", "processor", "aggregate", "volume", "disk",
    "resource_headroom_cpu", "resource_headroom_aggr",
    "workload", "workload_volume", "volume_space",
}


class BinaryReader:
    def __init__(self, raw):
        self.raw = raw
        self.pos = 0

    def number(self, fmt):
        size = struct.calcsize(fmt)
        value, = struct.unpack_from(fmt, self.raw, self.pos)
        self.pos += size
        return value

    def tag(self, expected):
        pos = self.pos
        actual = self.number("<B")
        if actual != expected:
            raise ValueError(
                f"Metadata tag at {pos}: expected {expected}, got {actual}"
            )

    def text(self):
        size = self.number("<H")
        value = self.raw[self.pos:self.pos + size]
        if self.raw[self.pos + size] != 0:
            raise ValueError(f"Unterminated metadata string at {self.pos}")
        self.pos += size + 1
        return value.decode("utf-8")

    def peek(self):
        return self.raw[self.pos]


def element(parent, name, value):
    return ET.SubElement(parent, name) if value is None else _text(
        parent, name, value
    )


def _text(parent, name, value):
    child = ET.SubElement(parent, name)
    child.text = str(value)
    return child


def binary_metadata(raw):
    reader = BinaryReader(raw)
    reader.tag(1)
    outer_version = (reader.number("<I"), reader.number("<I"))
    if outer_version != (6, 3):
        raise ValueError(f"Unsupported metadata version: {outer_version}")
    reader.tag(2)
    reader.tag(3)
    major, minor = reader.number("<I"), reader.number("<I")
    root = ET.Element("archive", version=f"{major}.{minor}")
    root.set("generation", str(reader.number("<I")))
    root.set("preset_version", reader.text())
    root.set("header_offset", str(reader.number("<Q")))
    for tag, name in [
        (4, "local_base_time"), (5, "duration"),
        (6, "start_time"), (7, "end_time"),
    ]:
        reader.tag(tag)
        root.set(name, format(reader.number("<Q"), "x"))
    reader.tag(8)
    system = ET.SubElement(root, "system")
    element(system, "version", reader.text())
    element(system, "base_time", reader.number("<Q"))
    for name in [
        "node_uuid", "hostname", "model", "serial_number", "system_id",
        "cluster_name", "cluster_id", "process_name", "store_name",
    ]:
        element(system, name, reader.text())
    while reader.peek() == 9:
        reader.tag(9)
        attribute = ET.SubElement(root, "attribute_def")
        element(attribute, "name", reader.text())
        element(attribute, "value", reader.number("<I"))
        element(attribute, "description", reader.text())
    while reader.peek() == 10:
        reader.tag(10)
        section = ET.SubElement(root, "section_def")
        element(section, "section_type", reader.number("<I"))
        element(section, "section_version", reader.number("<I"))
        element(section, "section_offset", format(reader.number("<Q"), "x"))
    reader.tag(11)
    object_count = reader.number("<I")
    preset = ET.SubElement(root, "preset")
    for _ in range(object_count):
        reader.tag(12)
        obj = ET.SubElement(preset, "object")
        element(obj, "name", reader.text())
        element(obj, "id", reader.number("<I"))
        element(obj, "privilege", reader.text())
        counter_count = reader.number("<I")
        while reader.peek() == 13:
            reader.tag(13)
            aggregation = ET.SubElement(
                obj, "aggregation", type=reader.text()
            )
            for name in [
                "aggregation_name", "instance_name", "instance_uuid",
                "instance_list_required_filter",
            ]:
                element(aggregation, name, reader.text())
            element(
                aggregation, "get_instances_max_records",
                reader.number("<I")
            )
        for _ in range(counter_count):
            reader.tag(14)
            counter = ET.SubElement(
                obj, "counter",
                is_key=str(reader.number("<I")),
                ctype=str(reader.number("<I")),
            )
            element(counter, "name", reader.text())
            element(counter, "description", reader.text())
            element(counter, "id", reader.number("<I"))
            element(counter, "alias_of", reader.text())
            element(counter, "base_id", reader.number("<I"))
            element(counter, "size", reader.number("<I"))
            element(counter, "privilege", reader.text())
            periods, xs, ys = (
                reader.number("<I"), reader.number("<I"), reader.number("<I")
            )
            for _ in range(periods):
                reader.tag(15)
                element(counter, "period", reader.number("<Q"))
            for _ in range(xs):
                reader.tag(16)
                element(counter, "x_name", reader.text())
            for _ in range(ys):
                reader.tag(17)
                element(counter, "y_name", reader.text())
            reader.tag(18)
    reader.tag(19)
    reader.tag(0)
    if reader.pos != len(raw):
        raise ValueError(f"Unread binary metadata bytes at {reader.pos}")
    return root


def metadata_root(raw):
    end = raw.find(b"</archive>")
    if end < 0:
        return binary_metadata(raw) if raw[:1] == b"\x01" else None
    return ET.fromstring(raw[:end + len(b"</archive>")])


def unpack(source, groups):
    key = source.stem
    dest = CACHE / key
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.check_output([
        str(SEVEN_ZIP), "x", "-so", str(source), "performance-archives.tar"
    ])
    archive = tarfile.open(fileobj=io.BytesIO(data))
    members = archive.getmembers()
    meta_members = [m for m in members if m.name.endswith(".ccma.meta")]
    inventory = []
    saved = set()
    for m in meta_members:
        group = m.name.split("/ccma/", 1)[1].rsplit("/", 1)[0]
        raw = archive.extractfile(m).read()
        root = metadata_root(raw)
        entry = {"path": m.name, "group": group, "bytes": m.size}
        if root is not None:
            entry["archive"] = root.attrib
            entry["system"] = {
                c.tag: c.text for c in root.find("system")
            }
            entry["objects"] = [
                {
                    "name": o.findtext("name"), "id": o.findtext("id"),
                    "counter_count": len(o.findall("counter")),
                }
                for o in root.findall("preset/object")
            ]
        inventory.append(entry)
        if group not in groups or group in saved:
            continue
        saved.add(group)
        out = dest / group.replace("/", "_")
        out.with_suffix(".meta").write_bytes(raw)
        sample_path = m.name[:-5] + ".gz"
        compressed = archive.extractfile(sample_path).read()
        binary = gzip.decompress(compressed)
        out.with_suffix(".bin").write_bytes(binary)
        print("SAVED", group, len(raw), len(binary), flush=True)
        if root is not None:
            ET.indent(root)
            out.with_suffix(".xml").write_bytes(
                ET.tostring(root, encoding="utf-8", xml_declaration=True)
            )
    (dest / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("INVENTORY", len(inventory), collections.Counter(
        x["group"] for x in inventory
    ), flush=True)


def inspect(path):
    raw = path.read_bytes()
    root = metadata_root(raw)
    if root is None:
        print("BINARY_METADATA", len(raw))
        hexdump(raw[:512])
        return
    print("ARCHIVE", root.attrib)
    print("SYSTEM", {c.tag: c.text for c in root.find("system")})
    for o in root.findall("preset/object"):
        if o.findtext("name") not in TARGETS:
            continue
        print("OBJECT", o.findtext("name"), "ID", o.findtext("id"))
        for c in o.findall("counter"):
            print("  COUNTER", c.attrib, {
                child.tag: child.text for child in c
            })
    close = raw.find(b"</archive>")
    if close >= 0:
        close += len(b"</archive>")
        print("META_XML_END", close, "TRAILER_SIZE", len(raw) - close)
        hexdump(raw[close:close + 256])
    else:
        print("BINARY_META_BYTES", len(raw))
    data = path.with_suffix(".bin").read_bytes()
    print("DATA_BYTES", len(data))
    hexdump(data[:1024])


def hexdump(data, offset=0):
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"{i + offset:08x}  {row.hex(' '):47s}  {text}")


def schemas(root):
    result = {}
    for obj in root.findall("preset/object"):
        counters = []
        for counter in obj.findall("counter"):
            counters.append({
                "name": counter.findtext("name"),
                "id": int(counter.findtext("id")),
                "base_id": int(counter.findtext("base_id")),
                "size": int(counter.findtext("size")),
                "ctype": int(counter.get("ctype")),
                "key": counter.get("is_key") == "1",
                "periods": {int(p.text) for p in counter.findall("period")},
                "description": counter.findtext("description"),
                "array_labels": [x.text for x in counter.findall("x_name")],
                "y_labels": [x.text for x in counter.findall("y_name")],
            })
        result[int(obj.findtext("id"))] = {
            "name": obj.findtext("name"), "counters": counters
        }
    return result


def records(root, data, target_names=TARGETS, stats=None,
            array_counters=frozenset(), counter_names=None):
    schema = schemas(root)
    base_time = int(root.findtext("system/base_time"))
    offset = 0
    layouts = {}
    while offset < len(data):
        if len(data) - offset < 24:
            raise ValueError(f"Incomplete record header at {offset}")
        timestamp, size, object_id, period = struct.unpack_from(
            "<QIIQ", data, offset
        )
        start, stop = offset + 24, offset + 24 + size
        if stop > len(data):
            raise ValueError(f"Record extends beyond file at {offset}")
        obj = schema[object_id]
        layout_key = (object_id, period)
        if layout_key not in layouts:
            fields, stride = [], 0
            for c in obj["counters"]:
                if not (c["key"] or period in c["periods"]):
                    continue
                if counter_names is None or c["key"] or (obj["name"], c["name"]) in counter_names:
                    fields.append((c, stride))
                stride += c["size"]
            layouts[layout_key] = fields, stride
        fields, stride = layouts[layout_key]
        if size and (not stride or size % stride):
            raise ValueError(
                f"Schema mismatch at {offset}: {obj['name']} period={period} "
                f"size={size} stride={stride}"
            )
        if stats is not None:
            stats["record_blocks"] += 1
        if obj["name"] in target_names:
            for pos in range(start, stop, stride or 1):
                row = {}
                for field, relative in fields:
                    cursor = pos + relative
                    value_bytes = data[cursor:cursor + field["size"]]
                    if field["ctype"] & 0x2000:
                        value = value_bytes.split(b"\0", 1)[0].decode(
                            "utf-8", errors="replace"
                        )
                    elif (obj["name"], field["name"]) in array_counters:
                        labels = field["array_labels"]
                        if (not labels or len(set(labels)) != len(labels)
                                or field["y_labels"]
                                or field["size"] != len(labels) * 8):
                            raise ValueError(
                                f"Unsupported array layout: {obj['name']}.{field['name']}"
                            )
                        value = dict(zip(labels, struct.unpack(
                            "<" + "Q" * len(labels), value_bytes
                        )))
                    elif field["size"] in (4, 8):
                        value = int.from_bytes(value_bytes, "little")
                    else:
                        value = None
                    row[field["name"]] = value
                yield {
                    "object": obj["name"], "period_ms": period,
                    "timestamp_ms": base_time + timestamp, "raw": row,
                }
        offset = stop


def decode(path):
    root = metadata_root(path.read_bytes())
    if root is None:
        raise ValueError("Binary metadata is not yet supported")
    data = path.with_suffix(".bin").read_bytes()
    rows = list(records(root, data))
    counts = collections.Counter(r["object"] for r in rows)
    output = path.with_suffix(".raw.json")
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("DECODED", len(rows), counts, output)
    printed = collections.Counter()
    for row in rows:
        name = row["object"]
        if printed[name] >= 3:
            continue
        printed[name] += 1
        print("ROW", name, row["period_ms"], datetime.fromtimestamp(
            row["timestamp_ms"] / 1000, timezone(timedelta(hours=8))
        ).isoformat(), row["raw"])


def main():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    extract = subs.add_parser("unpack")
    extract.add_argument("source", type=Path)
    extract.add_argument("--groups", nargs="+", default=[
        "kernel", "kernel/opm", "kernel/asup"
    ])
    show = subs.add_parser("inspect")
    show.add_argument("meta", type=Path)
    reader = subs.add_parser("decode")
    reader.add_argument("meta", type=Path)
    args = parser.parse_args()
    if args.command == "unpack":
        unpack(args.source, set(args.groups))
    elif args.command == "inspect":
        inspect(args.meta)
    else:
        decode(args.meta)


if __name__ == "__main__":
    main()
