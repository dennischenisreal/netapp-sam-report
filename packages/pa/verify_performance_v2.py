"""Read-only regression and full saved-workbook checks for the V2 deliverables."""

import argparse
import collections
import hashlib
import json
from pathlib import Path
from xml.etree.ElementTree import tostring

import openpyxl
from openpyxl.utils import get_column_letter

from performance_history import digest
from verify_performance_workbooks import same_value

BASE_NAMES = ["Node", "Aggr", "Volume", "FlexGroup", "Network Port", "指标说明"]


def style(cell):
    return (str(cell.font), str(cell.fill), str(cell.border), str(cell.alignment),
            cell.number_format, str(cell.protection))


def conditional_rules(sheet):
    result = []
    for area in sheet.conditional_formatting:
        for rule in sheet.conditional_formatting[area]:
            xml = rule.to_tree()
            xml.attrib.pop("dxfId", None)
            result.append((str(area.sqref), tostring(xml),
                           tostring(rule.dxf.to_tree()) if rule.dxf else None))
    return result


def verify(directory):
    model = json.loads((directory / "report_model.json").read_text(encoding="utf-8"))
    baseline = Path(model["baseline_path"])
    baseline_model = json.loads((baseline.parent / "report_model.json").read_text(encoding="utf-8"))
    assert digest({k: model[k] for k in ("sheets", "info", "sources")}) == model["baseline_model_hash"]
    for k in ("sheets", "info", "sources", "flexgroup_checks", "excluded_down_ports"):
        assert model[k] == baseline_model[k], ("Baseline model changed", k)
    path = directory / model["output_filename"]
    values = openpyxl.load_workbook(path, data_only=True)
    formulas = openpyxl.load_workbook(path, data_only=False)
    old_values = openpyxl.load_workbook(baseline, data_only=True)
    old_formulas = openpyxl.load_workbook(baseline, data_only=False)
    assert values.sheetnames == BASE_NAMES + [s["name"] for s in model["analysis_sheets"]]
    checked = 0
    for name in BASE_NAMES:
        a, b = formulas[name], old_formulas[name]
        assert (a.max_row, a.max_column, a.freeze_panes, a.sheet_view.showGridLines) == (
            b.max_row, b.max_column, b.freeze_panes, b.sheet_view.showGridLines), name
        assert list(a.tables) == list(b.tables), name
        for key in a.tables:
            assert a.tables[key].ref == b.tables[key].ref
            assert str(a.tables[key].autoFilter) == str(b.tables[key].autoFilter)
            assert str(a.tables[key].tableStyleInfo) == str(b.tables[key].tableStyleInfo)
        assert list(a.merged_cells.ranges) == list(b.merged_cells.ranges)
        for key in set(a.column_dimensions) | set(b.column_dimensions):
            ac, bc = a.column_dimensions[key], b.column_dimensions[key]
            assert (ac.width, ac.hidden, ac.outlineLevel) == (bc.width, bc.hidden, bc.outlineLevel), (name, key)
        for ra, rb in zip(a.iter_rows(), b.iter_rows()):
            for ca, cb in zip(ra, rb):
                assert ca.value == cb.value, (name, ca.coordinate, "formula/value", ca.value, cb.value)
                assert style(ca) == style(cb), (name, ca.coordinate, "style")
                va, vb = values[name][ca.coordinate].value, old_values[name][cb.coordinate].value
                assert same_value(va, vb, {}) if isinstance(vb, (int, float)) else va == vb, (
                    name, ca.coordinate, "cached", va, vb)
                checked += 1
        for r in range(1, a.max_row + 1):
            assert a.row_dimensions[r].height == b.row_dimensions[r].height, (name, r, "height")
        assert conditional_rules(a) == conditional_rules(b), (name, "conditional formatting")
    extra_cells = 0
    for definition in model["analysis_sheets"]:
        sheet = values[definition["name"]]
        fields, rows = definition["fields"], definition["rows"]
        assert sheet.freeze_panes == f"{get_column_letter(definition['frozen'] + 1)}6"
        assert len(sheet.tables) == 1
        assert next(iter(sheet.tables.values())).ref == f"A5:{get_column_letter(len(fields))}{5 + len(rows)}"
        for c, f in enumerate(fields, 1):
            assert sheet.cell(5, c).value == f["label"]
            for r, row in enumerate(rows, 6):
                cell = sheet.cell(r, c)
                assert cell.data_type != "e", (definition["name"], cell.coordinate, cell.value)
                assert same_value(cell.value, row.get(f["key"]), f), (
                    definition["name"], cell.coordinate, cell.value, row.get(f["key"]))
                extra_cells += 1
                if f.get("format"):
                    assert cell.number_format == f["format"]
    for wb in (values, formulas, old_values, old_formulas):
        wb.close()
    result = {
        "site": model["customer"], "file": str(path.resolve()), "original_cells_unchanged": checked,
        "new_cells_verified": extra_cells, "sheet_count": 6 + len(model["analysis_sheets"]),
        "new_sheet_rows": {s["name"]: len(s["rows"]) for s in model["analysis_sheets"]},
        "original_sha256": hashlib.file_digest(baseline.open("rb"), "sha256").hexdigest(),
        "output_sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
    }
    (directory / "verification_v2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    results = [verify(p.parent) for p in sorted(args.root.glob("*/report_model.json"))]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    (args.root / "verification_v2.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
