import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.resolve(process.argv[2] ?? "outputs/performance_report_20261116_r3");
const model = JSON.parse(await fs.readFile(path.join(outputDir, "report_model.json"), "utf8"));
const workbook = Workbook.create();
const font = "Microsoft YaHei";
const colors = {
  text: "#263238", header: "#304A50", green: "#1F7258",
  secondary: "#5F6B73", rule: "#CED7DC", pale: "#FFF4D6",
};
const headerRow = 5;
const firstRow = 6;

function column(index) {
  let result = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    result = String.fromCharCode(65 + (n - 1) % 26) + result;
  }
  return result;
}

function displayValue(field, value) {
  if (value === null || value === undefined) return "N/A";
  if (field.format?.includes("yyyy")) {
    // Excel stores these explicit UTC+08:00 wall-clock times without a timezone.
    return new Date(value + 8 * 3600 * 1000);
  }
  return typeof value === "string" && value.startsWith("=") ? "'" + value : value;
}

function title(sheet, text, note) {
  sheet.showGridLines = false;
  sheet.getRange("A2").values = [[text]];
  sheet.getRange("A2").format.font = { name: font, size: 15, bold: true, color: colors.text };
  sheet.getRange("A2:K2").format.borders = {
    bottom: { style: "thin", color: colors.rule },
  };
  sheet.getRange("A2:K2").format.rowHeight = 30;
  if (note) {
    sheet.getRange("A3").values = [[note]];
    sheet.getRange("A3").format.font = { name: font, size: 10, color: colors.secondary };
    sheet.getRange("A3").format.wrapText = false;
    sheet.getRange("A3:K3").format.rowHeight = 26;
  }
}

function textUnits(value) {
  return [...String(value ?? "")].reduce((sum, ch) => sum + (ch.charCodeAt(0) > 255 ? 2 : 1), 0);
}

const sheets = new Map();
for (const definition of model.sheets) {
  const sheet = workbook.worksheets.add(definition.name);
  sheets.set(definition.name, sheet);
}
let notes = model.compact ? null : workbook.worksheets.add("指标说明");
for (const definition of model.analysis_sheets ?? []) {
  const sheet = workbook.worksheets.add(definition.name);
  sheets.set(definition.name, sheet);
}
if (model.compact) notes = workbook.worksheets.add("指标说明");
for (const definition of model.sheets) {
  const sheet = sheets.get(definition.name);
  const { fields, rows } = definition;
  const lastCol = column(fields.length - 1);
  const lastRow = headerRow + rows.length;
  const all = sheet.getRange(`A${headerRow}:${lastCol}${lastRow}`);
  all.format.font = { name: font, size: 10, color: colors.text };
  all.format.verticalAlignment = "center";
  title(sheet, model.customer ? `${model.customer} - ${definition.title}` : definition.title,
    definition.note);
  const header = sheet.getRange(`A${headerRow}:${lastCol}${headerRow}`);
  header.values = [fields.map(f => f.label)];
  if (rows.length) {
    sheet.getRange(`A${firstRow}:${lastCol}${lastRow}`).values =
      rows.map(row => fields.map(f => displayValue(f, row[f.key])));
  }
  const tableName = "Perf" + definition.name.replace(/[^A-Za-z]/g, "");
  const table = sheet.tables.add(`A${headerRow}:${lastCol}${lastRow}`, true, tableName);
  table.style = "TableStyleLight9";
  table.showFilterButton = true;
  header.format = {
    fill: colors.header,
    font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
  };
  header.format.rowHeight = 43;
  header.format.borders = {
    insideVertical: { style: "thin", color: "#FFFFFF" },
  };
  fields.forEach((field, index) => {
    const width = field.key.endsWith("_id") ? Math.max(field.width, 116) : field.width;
    const range = sheet.getRange(
      `${column(index)}${firstRow}:${column(index)}${Math.max(lastRow, firstRow)}`);
    range.format.columnWidth = width;
    range.format.wrapText = true;
    range.format.horizontalAlignment = field.format ? "right" : "left";
    if (field.format) range.setNumberFormat(field.format);
    if (field.key === "notes") {
      range.conditionalFormats.add("notContainsBlanks", {
        format: { fill: colors.pale, font: { color: "#805A11" } },
      });
    }
    if (field.key === "hotspot") {
      for (const label of ["IOPS / 吞吐热点", "IOPS热点", "吞吐热点", "区间相对热点"]) {
        range.conditionalFormats.add("cellIs", {
          operator: "equal", formula: `"${label}"`,
          format: { fill: colors.pale, font: { bold: true, color: "#805A11" } },
        });
      }
    }
  });
  rows.forEach((row, index) => {
    let lines = 1;
    for (const field of fields) {
      if (field.format) continue;
      const width = field.key.endsWith("_id") ? Math.max(field.width, 116) : field.width;
      lines = Math.max(lines, Math.ceil(textUnits(row[field.key]) * 6.2 / (width * 7 - 10)));
    }
    sheet.getRange(`A${firstRow + index}:${lastCol}${firstRow + index}`).format.rowHeight =
      Math.max(25, lines * 15 + 8);
  });
  sheet.freezePanes.freezeRows(headerRow);
  sheet.freezePanes.freezeColumns(definition.frozen);
  sheet.tabColor = definition.name === "Node" ? colors.green : "#5B7D88";
}

function fieldColumn(definition, key) {
  return column(definition.fields.findIndex(f => f.key === key));
}

const network = model.sheets.find(s => s.name === "Network Port");
const networkSheet = sheets.get(network.name);
const netCol = key => fieldColumn(network, key);
network.rows.forEach((row, index) => {
  const r = firstRow + index;
  const io = netCol("io_avg"), speed = netCol("speed_gbps");
  networkSheet.getRange(`${io}${r}`).formulas = [[
    `=IF(AND(ISNUMBER(${netCol("rx_io")}${r}),ISNUMBER(${netCol("tx_io")}${r})),` +
    `${netCol("rx_io")}${r}+${netCol("tx_io")}${r},"N/A")`,
  ]];
  // Only stable-speed, fully covered configurations use the simple displayed ratio.
  if (row.speed_gbps > 0 && row.util_avg !== null &&
      Math.abs(row.util_minutes - row.coverage *
        (row.window_end - row.window_start) / 60000) < 0.001 &&
      Math.abs(row.io_avg * 8388608 / (row.speed_gbps * 1e9) - row.util_avg) < 1e-10) {
    for (const [target, source] of [
      ["util_avg", "io_avg"], ["util_max", "io_max"],
      ["rx_util", "rx_io"], ["tx_util", "tx_io"],
    ]) {
      networkSheet.getRange(`${netCol(target)}${r}`).formulas = [[
        `=IF(AND(ISNUMBER(${speed}${r}),${speed}${r}>0),` +
        `${netCol(source)}${r}*8388608/(${speed}${r}*1000000000),"N/A")`,
      ]];
    }
  }
});

const detail = model.sheets.find(s => s.name === "FlexGroup");
const volume = model.sheets.find(s => s.name === "Volume");
const lastDetailRow = headerRow + detail.rows.length;
volume.rows.forEach((row, index) => {
  if (row.style !== "FlexGroup") return;
  const r = firstRow + index;
  for (const key of ["io_avg", "iops_avg", "read_io", "write_io"]) {
    const source = fieldColumn(detail, key);
    const id = fieldColumn(detail, "parent_id");
    const sourceRange = `'FlexGroup'!$${source}$${firstRow}:$${source}$${lastDetailRow}`;
    const idRange = `'FlexGroup'!$${id}$${firstRow}:$${id}$${lastDetailRow}`;
    const parentCell = `$${fieldColumn(volume, "resource_id")}${r}`;
    sheets.get("Volume").getRange(`${fieldColumn(volume, key)}${r}`).formulas = [[
      `=IF(COUNTIFS(${idRange},${parentCell},${sourceRange},">=0",${sourceRange},"<>")=` +
      `${fieldColumn(volume, "members")}${r},SUMIFS(${sourceRange},${idRange},${parentCell}),"N/A")`,
    ]];
  }
  const source = fieldColumn(detail, "zombie_blocks");
  const id = fieldColumn(detail, "parent_id");
  const blockRange = `'FlexGroup'!$${source}$${firstRow}:$${source}$${lastDetailRow}`;
  const idRange = `'FlexGroup'!$${id}$${firstRow}:$${id}$${lastDetailRow}`;
  const parentCell = `$${fieldColumn(volume, "resource_id")}${r}`;
  sheets.get("Volume").getRange(`${fieldColumn(volume, "zombie_blocks")}${r}`).formulas = [[
    `=IF(COUNTIFS(${idRange},${parentCell},${blockRange},">=0",${blockRange},"<>")=` +
    `${fieldColumn(volume, "members")}${r},SUMIFS(${blockRange},${idRange},${parentCell}),"N/A")`,
  ]];
});
for (const definition of [volume, detail]) {
  const sheet = sheets.get(definition.name);
  definition.rows.forEach((row, index) => {
    const r = firstRow + index;
    const blocks = `${fieldColumn(definition, "zombie_blocks")}${r}`;
    sheet.getRange(`${fieldColumn(definition, "zombie_tib")}${r}`).formulas = [[
      `=IF(ISNUMBER(${blocks}),${blocks}*4096/1024^4,"N/A")`,
    ]];
  });
}

title(notes, model.customer ? `${model.customer} - 指标与数据来源` : "指标与数据来源");
notes.tabColor = "#839098";
notes.getRange("A4:B4").values = [["项目", "口径说明"]];
notes.getRange(`A5:B${4 + model.info.length}`).values = model.info;
notes.getRange(`A4:F${model.info.length + model.sources.length + 12}`)
  .format.font = { name: font, size: 10, color: colors.text };
notes.getRange("A4:A60").format.columnWidth = 28;
notes.getRange("B4:B60").format.columnWidth = 114;
notes.getRange("C4:C60").format.columnWidth = 24;
notes.getRange("D4:E60").format.columnWidth = 23;
notes.getRange("F4:F60").format.columnWidth = 94;
notes.getRange(`A4:B${4 + model.info.length}`).format.wrapText = true;
model.info.forEach((item, index) => {
  const lines = Math.max(1, Math.ceil(textUnits(item[1]) * 6.2 / (114 * 7 - 10)));
  notes.getRange(`A${5 + index}:B${5 + index}`).format.rowHeight = Math.max(27, lines * 16 + 9);
});
const sourceHeader = model.info.length + 7;
notes.getRange(`A${sourceHeader}:F${sourceHeader}`).values = [[
  "来源", "文件", "节点/用途", "请求窗口开始 +08:00", "请求窗口结束 +08:00", "SHA-256",
]];
const sourceRows = [
  ...model.sources.map(s => [
    s.type, s.file, s.node, new Date(s.start + 8 * 3600 * 1000),
    new Date(s.end + 8 * 3600 * 1000), s.sha256,
  ]),
];
notes.getRange(`A${sourceHeader + 1}:F${sourceHeader + sourceRows.length}`).values = sourceRows;
notes.getRange(`A${sourceHeader + 1}:F${sourceHeader + sourceRows.length}`).format.rowHeight = 27;
notes.getRange(`D${sourceHeader + 1}:E${sourceHeader + sourceRows.length}`).setNumberFormat("yyyy-mm-dd hh:mm");
for (const area of ["A4:B4", `A${sourceHeader}:F${sourceHeader}`]) {
  notes.getRange(area).format = {
    fill: colors.header, font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
    verticalAlignment: "center", horizontalAlignment: "center",
  };
  notes.getRange(area).format.rowHeight = 30;
}

for (const [sheetIndex, definition] of [
  ...(model.analysis_sheets ?? []), ...(model.sections ?? []),
].entries()) {
  console.log("BUILD_ANALYSIS", definition.name, definition.rows.length);
  const sheet = sheets.get(definition.sheet_name ?? definition.name);
  const headerRow = definition.header_row ?? 5;
  const firstRow = headerRow + 1;
  const { fields, rows } = definition;
  if (!rows.length) {
    title(sheet, `${model.customer} - ${definition.title}`, definition.note);
    sheet.getRange("A5").values = [["本批无可计算数据"]];
    continue;
  }
  const lastCol = column(fields.length - 1);
  const lastRow = headerRow + rows.length;
  if (definition.sheet_name) {
    sheet.getRange(`A${headerRow - 2}`).values = [[definition.title]];
    sheet.getRange(`A${headerRow - 2}`).format.font = {
      name: font, size: 13, bold: true, color: colors.text,
    };
    sheet.getRange(`A${headerRow - 1}`).values = [[definition.note]];
    sheet.getRange(`A${headerRow - 1}`).format.font = {
      name: font, size: 10, color: colors.secondary,
    };
  } else {
    title(sheet, `${model.customer} - ${definition.title}`, definition.note);
  }
  const all = sheet.getRange(`A${headerRow}:${lastCol}${lastRow}`);
  all.format.font = { name: font, size: 10, color: colors.text };
  all.format.verticalAlignment = "center";
  sheet.getRange(`A${headerRow}:${lastCol}${headerRow}`).values = [fields.map(f => f.label)];
  sheet.getRange(`A${firstRow}:${lastCol}${lastRow}`).values =
    rows.map(row => fields.map(f => displayValue(f, row[f.key])));
  const table = sheet.tables.add(`A${headerRow}:${lastCol}${lastRow}`, true, `Analysis${sheetIndex + 1}`);
  table.style = "TableStyleLight9";
  table.showFilterButton = true;
  const header = sheet.getRange(`A${headerRow}:${lastCol}${headerRow}`);
  header.format = {
    fill: "#405B50", font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
  };
  header.format.rowHeight = 48;
  header.format.borders = { insideVertical: { style: "thin", color: "#FFFFFF" } };
  fields.forEach((f, i) => {
    const range = sheet.getRange(`${column(i)}${firstRow}:${column(i)}${lastRow}`);
    if (!definition.sheet_name) range.format.columnWidth = f.width;
    range.format.wrapText = true;
    range.format.horizontalAlignment = f.format ? "right" : "left";
    if (f.format) range.setNumberFormat(f.format);
    if (f.key === "status") {
      for (const [text, fill, color] of [
        ["INVALID", "#FCE6E6", "#9C2525"], ["PARTIAL", "#FFF4D6", "#805A11"],
        ["NOT_COMPARABLE", "#FFF4D6", "#805A11"], ["UNSUPPORTED", "#ECEFF1", "#5F6B73"],
      ]) {
        range.conditionalFormats.add("cellIs", {
          operator: "equal", formula: `"${text}"`, format: { fill, font: { color } },
        });
      }
    }
  });
  const rowHeights = rows.map(row => {
    const primary = model.sheets.find(s => s.name === definition.sheet_name);
    const lines = Math.max(1, ...fields.map((f, i) => {
      if (f.format) return 1;
      const width = primary?.fields[i]?.width ?? f.width;
      return Math.ceil(textUnits(row[f.key]) * 6.2 / (width * 7 - 10));
    }));
    return Math.max(27, lines * 15 + 8);
  });
  for (let begin = 0; begin < rowHeights.length;) {
    let end = begin + 1;
    while (end < rowHeights.length && rowHeights[end] === rowHeights[begin]) end++;
    sheet.getRange(`A${firstRow + begin}:${lastCol}${firstRow + end - 1}`)
      .format.rowHeight = rowHeights[begin];
    begin = end;
  }
  if (!definition.sheet_name) {
    sheet.freezePanes.freezeRows(headerRow);
    sheet.freezePanes.freezeColumns(definition.frozen);
    sheet.tabColor = definition.name === "性能分析摘要" ? "#1F7258" : "#7D8B83";
  }
}
console.log("ANALYSIS_FORMATTED");

workbook.recalculate();

// Check real dependencies with a temporary network input edit, then restore it.
const liveIndex = network.rows.findIndex(r => r.util_avg > 0 && r.speed_gbps > 0);
if (liveIndex >= 0) {
  const r = firstRow + liveIndex;
  const source = networkSheet.getRange(`${netCol("rx_io")}${r}`);
  const original = source.values[0][0];
  source.values = [[original + 1]];
  workbook.recalculate();
  const changed = networkSheet.getRange(`${netCol("io_avg")}${r}`).values[0][0];
  if (Math.abs(changed - network.rows[liveIndex].io_avg - 1) > 1e-8) {
    throw new Error("Network IO formula did not recalculate");
  }
  source.values = [[original]];
  workbook.recalculate();
}

// A missing constituent gauge must stay unavailable rather than become zero.
if (detail.rows.length) {
  const firstChild = detail.rows[0];
  const parentIndex = volume.rows.findIndex(r => r.resource_id === firstChild.parent_id);
  const childBlocks = sheets.get("FlexGroup").getRange(`${fieldColumn(detail, "zombie_blocks")}${firstRow}`);
  const originalBlocks = childBlocks.values[0][0];
  const parentTb = sheets.get("Volume").getRange(
    `${fieldColumn(volume, "zombie_tib")}${firstRow + parentIndex}`);
  for (const replacement of ["N/A", null]) {
    childBlocks.values = [[replacement]];
    workbook.recalculate();
    if (parentTb.values[0][0] !== "N/A") {
      throw new Error(`Missing constituent zombie gauge (${replacement}) became ${parentTb.values[0][0]}`);
    }
  }
  childBlocks.values = [[originalBlocks]];
  workbook.recalculate();
}

for (const definition of model.sheets) {
  const sheet = sheets.get(definition.name);
  if (!definition.rows.length) continue;
  const values = sheet.getRange(
    `A${firstRow}:${column(definition.fields.length - 1)}${headerRow + definition.rows.length}`).values;
  for (let index = 0; index < definition.rows.length; index++) {
    const row = definition.rows[index];
    for (const key of ["io_avg", "iops_avg", "lat_avg", "util_avg", "zombie_blocks", "zombie_tib"]) {
      const fieldIndex = definition.fields.findIndex(f => f.key === key);
      if (fieldIndex < 0) continue;
      const actual = values[index][fieldIndex];
      if (row[key] === null || row[key] === undefined) {
        if (actual !== "N/A") {
          throw new Error(`Missing value became ${actual}: ${definition.name} ${index} ${key}`);
        }
        continue;
      }
      if (typeof actual !== "number" ||
          Math.abs(actual - row[key]) > Math.max(1e-8, Math.abs(row[key]) * 1e-10)) {
        throw new Error(`Value mismatch ${definition.name} ${index} ${key}: ${actual} != ${row[key]}`);
      }
    }
  }
}

const errors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 50 }, summary: "final formula error scan",
  maxChars: 3000,
});
console.log("FORMULA_SCAN", errors.ndjson);
console.log((await workbook.inspect({
  kind: "table", range: "'Volume'!Q12:W14", include: "values,formulas",
  tableMaxRows: 3, tableMaxCols: 7, maxChars: 3000,
})).ndjson);

await fs.mkdir(path.join(outputDir, "previews"), { recursive: true });
for (const [sheetName, range, suffix = ""] of [
  ...model.sheets.map(s => [s.name, `A1:${s.name === "Node" ? "K" : "J"}${Math.min(14, 5 + s.rows.length)}`]),
  ["指标说明", "A1:B12"], ["Volume", `N5:W${Math.min(17, 5 + volume.rows.length)}`],
  ["FlexGroup", `N5:W${Math.min(15, 5 + detail.rows.length)}`],
  ...(model.analysis_sheets ?? []).map(s =>
    [s.name, `A1:${column(Math.min(s.fields.length, 8) - 1)}${Math.min(11, 5 + s.rows.length)}`]),
  ...((model.analysis_sheets && !model.compact) ? [
    ["节点持续负载", "R5:W7", "Processor"],
    ["卷操作结构", "K5:U12", "IOSizes"],
    ["FlexGroup 深度分析", "I5:R7", "Skew"],
    ["FlexGroup 深度分析", "AB5:AQ7", "SlowMembers"],
    ["网络深度分析", "K5:Y11", "Directions"],
    ["等待项原始观测", "I5:N11", "Semantics"],
    ["数据质量", "I5:R11", "Coverage"],
    ["月度采样窗口趋势", "I5:O11", "Comparability"],
    ["多月持续性风险", "I5:L11", "Comparability"],
  ] : []),
  ...(model.compact ? [
    ["Node", "I5:Q12", "CPU"],
    ["Volume", `${fieldColumn(volume, "read_iops")}5:${fieldColumn(volume, "rw_bytes_minutes")}12`, "ReadWrite"],
    ["Aggr", "O5:V12", "ReadWrite"],
    ["Volume", `${fieldColumn(volume, "zombie_blocks")}5:${fieldColumn(volume, "actual_end")}12`, "Zombie"],
    ["Network Port", `${netCol("rx_peak")}5:${netCol("rx_longest")}12`, "Directions"],
    ...(() => {
      const index = network.rows.findIndex(r => r.purpose_roles?.includes("intercluster"));
      return index < 0 ? [] : [
        ["Network Port", `A${index + 5}:J${Math.min(index + 11, network.rows.length + 5)}`, "Intercluster"],
        ["Network Port", `${netCol("ifgrp")}${index + 5}:${netCol("role_source")}${Math.min(index + 9, network.rows.length + 5)}`, "RoleEvidence"],
      ];
    })(),
    ...(detail.rows.length && detail.fields.some(f => f.key === "peer_iops_ratio") ? [
      ["FlexGroup", `${fieldColumn(detail, "peer_iops_ratio")}5:${fieldColumn(detail, "hot_longest")}12`, "HotspotRatios"],
      ...(() => {
        const index = detail.rows.findIndex(r => r.hotspot_marked);
        return index < 0 ? [] : [["FlexGroup", `A${index + 5}:J${index + 10}`, "MarkedMembers"]];
      })(),
    ] : []),
    ...(model.sections ?? []).map(s => [
      s.sheet_name, `A${s.header_row - 2}:${column(Math.min(s.fields.length, 10) - 1)}${Math.min(s.header_row + s.rows.length, s.header_row + 5)}`, s.name,
    ]),
  ] : []),
]) {
  if (model.analysis_sheets && !model.compact && !process.argv.includes("--render-all") &&
      ["Node", "Aggr", "Volume", "FlexGroup", "Network Port", "指标说明"].includes(sheetName)) continue;
  if (process.argv.includes("--notes-preview-only") && sheetName !== "指标说明") continue;
  const preview = await workbook.render({ sheetName, range, scale: 1.25, format: "png" });
  await fs.writeFile(path.join(outputDir, "previews",
    sheetName.replaceAll(" ", "_") + (suffix ? `_${suffix}` : range.startsWith("N") ? "_Zombie" : "") + ".png"),
    new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
const outputPath = path.join(outputDir,
  model.output_filename ?? "NetApp_性能报告_20261106-20261111_定稿版.xlsx");
await output.save(outputPath);
console.log("SAVED", outputPath);
if (process.argv.includes("--managed-export")) {
  // The parent owns this worker's lifetime, avoiding native renderer teardown.
  // Publish an atomic completion marker only after all previews and XLSX writes.
  const marker = {
    filename: path.basename(outputPath),
    sha256: createHash("sha256").update(await fs.readFile(outputPath)).digest("hex"),
  };
  const pending = path.join(outputDir, "export_complete.pending.json");
  await fs.writeFile(pending, JSON.stringify(marker));
  await fs.rename(pending, path.join(outputDir, "export_complete.json"));
  await new Promise(() => setInterval(() => {}, 1000));
}
