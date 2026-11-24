"""Read-only checks of exported PA workbooks against their prepared models."""

import argparse
import collections
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from prepare_performance_report import all_links_down


def same_value(actual, expected, field):
    if expected is None:
        return actual == "N/A"
    if expected == "" and actual is None:
        return True
    if field.get("format") and "yyyy" in field["format"]:
        wall_clock = datetime.fromtimestamp(expected / 1000, timezone.utc).replace(
            tzinfo=None) + timedelta(hours=8)
        return isinstance(actual, datetime) and abs((actual - wall_clock).total_seconds()) < 0.002
    if isinstance(expected, (float, int)):
        return isinstance(actual, (float, int)) and math.isclose(
            actual, expected, rel_tol=1e-10, abs_tol=1e-8)
    return actual == expected


def verify(directory, source_root=None):
    model = json.loads((directory / "report_model.json").read_text(encoding="utf-8"))
    path = directory / model["output_filename"]
    workbook = openpyxl.load_workbook(path, data_only=True)
    formulas = openpyxl.load_workbook(path, data_only=False)
    assert workbook.sheetnames == [s["name"] for s in model["sheets"]] + ["指标说明"]
    cells_checked = 0
    for definition in model["sheets"]:
        sheet = workbook[definition["name"]]
        fields, rows = definition["fields"], definition["rows"]
        assert [sheet.cell(5, c + 1).value for c in range(len(fields))] == [
            f["label"] for f in fields]
        assert sheet.max_row == max(6, 5 + len(rows)), (sheet.title, sheet.max_row, len(rows))
        freeze = f"{get_column_letter(definition['frozen'] + 1)}6"
        assert sheet.freeze_panes == freeze, (sheet.title, sheet.freeze_panes, freeze)
        assert len(sheet.tables) == 1
        table = next(iter(sheet.tables.values()))
        assert table.ref == f"A5:{get_column_letter(len(fields))}{5 + len(rows)}"
        for r, row in enumerate(rows, 6):
            for c, field in enumerate(fields, 1):
                cell = sheet.cell(r, c)
                assert cell.data_type != "e", (sheet.title, cell.coordinate, cell.value)
                expected = row.get(field["key"])
                assert same_value(cell.value, expected, field), (
                    sheet.title, cell.coordinate, field["key"], cell.value, expected)
                if field.get("format"):
                    assert cell.number_format == field["format"], (
                        sheet.title, cell.coordinate, cell.number_format, field["format"])
                cells_checked += 1
        if sheet.title in ("Volume", "FlexGroup"):
            keys = [f["key"] for f in fields]
            duration = keys.index("valid_minutes")
            assert keys[duration - 2:duration] == ["zombie_blocks", "zombie_tib"]
            for r in range(6, 6 + len(rows)):
                formula = formulas[sheet.title].cell(r, keys.index("zombie_tib") + 1).value
                assert "*4096/1024^4" in formula

    batch = json.loads((directory / "data" / "batch.json").read_text(encoding="utf-8"))
    assert all(n["status"] == "decoded_experimental_not_vendor_calibrated" for n in batch)
    assert len(batch) == len(model["sources"]) == model["counts"]["Node"]
    assert len({n["node"] for n in batch}) == len(batch)
    raw_volume_count = sum(n["counts"].get("volume", 0) for n in batch)
    logical = model["sheets"][2]["rows"]
    member_count = model["counts"]["FlexGroup"]
    assert raw_volume_count == len([r for r in logical if r["style"] != "FlexGroup"]) + member_count
    raw_port_count = sum(n["counts"].get("nic_common", 0) for n in batch)
    assert raw_port_count == model["counts"]["Network Port"] + len(model["excluded_down_ports"])
    links = collections.defaultdict(list)
    for node in batch:
        for link in node["port_link_samples"]:
            links[(node["node"], link["port"])].append(link)
    for excluded in model["excluded_down_ports"]:
        assert all_links_down(links[(excluded["node"], excluded["port"])])

    notes = workbook["指标说明"]
    for r, expected in enumerate(model["info"], 5):
        assert [notes.cell(r, c).value for c in (1, 2)] == expected
    source_header = len(model["info"]) + 7
    for r, source in enumerate(model["sources"], source_header + 1):
        assert notes.cell(r, 2).value == source["file"]
        assert notes.cell(r, 3).value == source["node"]
        assert notes.cell(r, 6).value == source["sha256"]

    if source_root:
        originals = {p.name: p for p in (source_root / model["customer"]).rglob("*.7z")}
        assert len(originals) == len(batch), (model["customer"], len(originals), len(batch))
        assert set(originals) == {s["file"] for s in model["sources"]}
        for source in model["sources"]:
            with originals[source["file"]].open("rb") as handle:
                assert hashlib.file_digest(handle, "sha256").hexdigest() == source["sha256"]
            for bound in ("start", "end"):
                observed = datetime.fromtimestamp(
                    source[bound] / 1000, timezone(timedelta(hours=8)))
                assert (observed.year, observed.month) == (2026, 11), (
                    source["file"], bound, observed)

    result = {
        "customer": model["customer"], "file": str(path.resolve()),
        "logs": len(batch), "clusters": len({n["cluster_uuid"] for n in batch}),
        "counts": model["counts"], "cells_checked": cells_checked,
        "hidden_down_ports": len(model["excluded_down_ports"]),
        "aggr_io_unavailable": sum(r["io_avg"] is None for r in model["sheets"][1]["rows"]),
        "node_io_unavailable": sum(r["io_avg"] is None for r in model["sheets"][0]["rows"]),
        "volume_io_unavailable": sum(r["io_avg"] is None for r in logical),
        "zero_window_flexgroups": [
            r["volume"] for r in logical if r["style"] == "FlexGroup" and r["valid_minutes"] == 0
        ],
        "source_start": min(s["start"] for s in model["sources"]),
        "source_end": max(s["end"] for s in model["sources"]),
    }
    workbook.close()
    formulas.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    reports = [verify(p.parent, args.source_root) for p in sorted(args.root.glob("*/report_model.json"))]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    (args.root / "verification.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
