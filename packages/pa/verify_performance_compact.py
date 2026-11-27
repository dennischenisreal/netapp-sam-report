"""Read-only verification of the final consolidated workbook."""

import argparse
import collections
import hashlib
import json
from pathlib import Path
import zipfile

import openpyxl
from openpyxl.utils import get_column_letter

from verify_performance_workbooks import same_value
from prepare_performance_report import all_links_down
from prepare_performance_compact import load
from performance_analysis import Analysis
from prepare_performance_report import bin_intervals, port_utilization


def duration_check(rows, threshold):
    total, run, maximum, end = 0, 0, 0, None
    for r in sorted(rows, key=lambda r: r["a"]):
        if r.get("value") is None or r["value"] <= threshold:
            run, end = 0, None
            continue
        elapsed = r["b"] - r["a"]
        run = run + elapsed if end is not None and abs(r["a"] - end) < .001 else elapsed
        total += elapsed
        maximum = max(maximum, run)
        end = r["b"]
    return total / 60, maximum / 60


def verify_native_thresholds(model, old, baseline):
    """Independent duration accumulation over the decoded native source intervals."""
    batch = load(baseline / "data/batch.json")
    base = [r for node in batch for r in load(baseline / "data" / f"{node['node']}.samples.json.gz")]
    analysis = Analysis(batch, old, base, [], model["analysis_config"])
    checked = 0
    for definition in model["sheets"]:
        for row in definition["rows"]:
            if definition["name"] == "Node":
                src = analysis.get(row["node"], "system", row["node"], "cpu_busy_pct")
                expected = duration_check(src, 70)
                assert abs(row["cpu_threshold"] - .7) < 1e-12
                actual = row["cpu_high_minutes"], row["cpu_high_longest"]
            elif definition["name"] in ("Volume", "FlexGroup"):
                src = analysis.volume_series(row)["latency_ms"]
                expected = duration_check(src, 10)
                actual = row["high_minutes"], row["longest"]
            elif definition["name"] == "Network Port":
                node, port = row["node"], row["port"]
                bins = {d: bin_intervals(analysis.get(node, "nic_common", port, d + "_mib_s"))
                        for d in ("rx", "tx")}
                links = [r for r in analysis.nodes[node]["port_link_samples"] if r["port"] == port]
                util = port_utilization(bins["rx"], bins["tx"], links)
                for d in ("rx", "tx"):
                    expected = duration_check(
                        [dict(a=r["timestamp"], b=r["timestamp"] + 300, value=r[d]) for r in util], .7)
                    assert all(abs(x - y) < 1e-8 for x, y in
                               zip(expected, (row[d + "_high"], row[d + "_longest"])))
                    checked += 2
                continue
            else:
                continue
            assert all(abs(x - y) < 1e-8 for x, y in zip(expected, actual)), (
                definition["name"], row["resource_id"], expected, actual)
            checked += 2
    return checked


def verify(directory, baseline):
    model = json.loads((directory / "report_model.json").read_text(encoding="utf-8"))
    old = json.loads((baseline / "report_model.json").read_text(encoding="utf-8"))
    target = directory / model["output_filename"]
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
    values = openpyxl.load_workbook(target, data_only=True)
    formulas = openpyxl.load_workbook(target, data_only=False)
    expected = [s["name"] for s in model["sheets"] + model["analysis_sheets"]] + ["指标说明"]
    assert values.sheetnames == expected and len(expected) == 9
    checked, baseline_checked = 0, 0
    table_counts = collections.Counter()
    for definition in model["sheets"] + model["analysis_sheets"] + model["sections"]:
        name = definition.get("sheet_name", definition["name"])
        sheet = values[name]
        table_counts[name] += 1
        header = definition.get("header_row", 5)
        fields = definition["fields"]
        assert len({f["key"] for f in fields}) == len(fields)
        assert [sheet.cell(header, c + 1).value for c in range(len(fields))] == [f["label"] for f in fields]
        expected_ref = f"A{header}:{get_column_letter(len(fields))}{header + len(definition['rows'])}"
        assert any(t.ref == expected_ref for t in sheet.tables.values()), (name, expected_ref)
        for r, row in enumerate(definition["rows"], header + 1):
            for c, field in enumerate(fields, 1):
                cell = sheet.cell(r, c)
                assert cell.data_type != "e", (name, cell.coordinate, cell.value)
                assert same_value(cell.value, row.get(field["key"]), field), (
                    name, cell.coordinate, field["key"], cell.value, row.get(field["key"]))
                if field.get("format"):
                    assert cell.number_format == field["format"], (name, cell.coordinate, cell.number_format)
                checked += 1
        if not definition.get("sheet_name"):
            assert sheet.freeze_panes == f"{get_column_letter(definition['frozen'] + 1)}6"
    for name, count in table_counts.items():
        assert len(values[name].tables) == count
    for a, b in zip(model["sheets"], old["sheets"]):
        assert a["name"] == b["name"] and len(a["rows"]) == len(b["rows"])
        old_rows = {r["resource_id"]: r for r in b["rows"]}
        keys = [f["key"] for f in a["fields"]]
        assert len(set(keys)) == len(keys)
        for r, row in enumerate(a["rows"], 6):
            prior = old_rows[row["resource_id"]]
            for key in ("io_avg", "io_max", "iops_avg", "iops_max", "lat_avg", "lat_max",
                        "cpu_avg", "cpu_max", "read_io", "write_io", "read_lat", "write_lat",
                        "util_avg", "util_max", "zombie_blocks", "zombie_tib", "coverage"):
                if key in prior:
                    assert row[key] == prior[key], (a["name"], row["resource_id"], key)
                    assert same_value(values[a["name"]].cell(r, keys.index(key) + 1).value, prior[key], {}), (
                        a["name"], r, key)
                    baseline_checked += 1
            if "read_ops_ratio" in row:
                for suffix in ("ops", "bytes"):
                    x, y = row[f"read_{suffix}_ratio"], row[f"write_{suffix}_ratio"]
                    if x is not None:
                        assert 0 <= x <= 1 and abs(x + y - 1) < 1e-12
            if a["name"] in ("Volume", "FlexGroup"):
                assert keys[keys.index("valid_minutes") - 2:keys.index("valid_minutes")] == ["zombie_blocks", "zombie_tib"]
                formula = formulas[a["name"]].cell(r, keys.index("zombie_tib") + 1).value
                assert "*4096/1024^4" in formula
                assert values[a["name"]].cell(r, keys.index("zombie_tib") + 1).number_format.endswith("0.00")
    network = next(s for s in model["sheets"] if s["name"] == "Network Port")
    keys = [f["key"] for f in network["fields"]]
    assert keys[keys.index("port") + 1] == "role"
    node_keys = [f["key"] for s in model["sheets"] if s["name"] == "Node" for f in s["fields"]]
    assert not any("processor" in k for k in node_keys)
    batch = json.loads((baseline / "data/batch.json").read_text(encoding="utf-8"))
    links = collections.defaultdict(list)
    for node in batch:
        for link in node["port_link_samples"]:
            links[(node["node"], link["port"])].append(link)
    for row in network["rows"]:
        assert not all_links_down(links[(row["node"], row["port"])])
    protocol = next(s for s in model["analysis_sheets"] if s["name"] == "协议与元数据")
    assert all(r["protocol"].startswith(("CIFS", "NFS")) for r in protocol["rows"])
    hotspots = next(s for s in model["analysis_sheets"] if s["name"] == "热点卷分析")
    assert len(hotspots["rows"]) <= 30
    for dimension in ("延迟", "吞吐", "IOPS"):
        ranked = [r for r in hotspots["rows"] if r["dimension"] == dimension]
        assert all(r["scope"] == "本Site" and r["style"] != "节点根卷" for r in ranked)
        assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
        assert len({r["resource_id"] for r in ranked}) == len(ranked)
    allowed_roles = {"data", "cluster", "intercluster", "node-mgmt", "cluster-mgmt"}
    for row in network["rows"]:
        roles = row["purpose_roles"]
        assert len(roles) == len(set(roles)) and set(roles) <= allowed_roles
        assert all(f"{role}（" in row["role"] for role in roles)
        assert "NFS" not in row["role"] and "CIFS" not in row["role"]
        assert bool("共用" in row["role"]) == (len(roles) > 1)
        if row["role_raw"] == "cluster":
            assert roles == ["cluster"]
        if "intercluster" in roles and row["role_raw"] != "intercluster":
            assert row["lif_evidence"], (row["node"], row["port"])
    assert model["analysis_config"]["cpu_high_pct"] == 70
    assert model["analysis_config"]["latency_watch_ms"] == 10
    assert model["analysis_config"]["network_high"] == .7
    threshold_checked = verify_native_thresholds(model, old, baseline)
    for sheet in values:
        for row in sheet:
            assert not any(c.data_type == "e" for c in row), sheet.title
    result = {
        "file": str(target.resolve()), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "sheet_count": len(expected), "all_model_cells_checked": checked,
        "baseline_metric_cells_unchanged": baseline_checked,
        "resource_counts": {s["name"]: len(s["rows"]) for s in model["sheets"]},
        "flexgroup_tables": len(values["FlexGroup"].tables),
        "port_purpose_known": sum(bool(r["purpose_roles"]) for r in network["rows"]),
        "port_purpose_unknown": sum(not r["purpose_roles"] for r in network["rows"]),
        "intercluster_ports": sum("intercluster" in r["purpose_roles"] for r in network["rows"]),
        "shared_purpose_ports": sum(len(r["purpose_roles"]) > 1 for r in network["rows"]),
        "marked_flexgroup_members": sum(r.get("hotspot_marked", False)
                                       for s in model["sheets"] if s["name"] == "FlexGroup" for r in s["rows"]),
        "hotspot_ranking_rows": len(hotspots["rows"]),
        "native_duration_values_recomputed": threshold_checked,
        "formula_errors": 0,
    }
    values.close()
    formulas.close()
    (directory / "verification_compact.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    verify(args.directory, args.baseline)
