"""One customer/site, one PA batch per invocation. Never overwrite an approved report."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

from pa_capabilities import audit
from performance_analysis import DEFAULTS
from performance_history import connect, export_history, digest
from prepare_performance_report import prepare
from prepare_performance_v2 import prepare_site, global_hotspots
from validate_pa import headers, parse_archive, source_digest
from pa_runtime import node_executable

ROOT = Path(__file__).parent


def managed_export(command, directory, timeout=900):
    marker = directory / "export_complete.json"
    if marker.exists():
        raise ValueError("Managed builder requires a fresh completion marker")
    completed = False
    with (directory / "builder.stdout.log").open("w", encoding="utf-8") as stdout, (
            directory / "builder.stderr.log").open("w", encoding="utf-8") as stderr:
        worker = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if worker.poll() is not None:
                    raise RuntimeError(f"Export worker exited before parent completion: {worker.returncode}")
                if marker.exists():
                    value = json.loads(marker.read_text(encoding="utf-8"))
                    filename = value["filename"]
                    if Path(filename).name != filename:
                        raise ValueError("Invalid export completion filename")
                    output = directory / filename
                    if source_digest(output) != value["sha256"]:
                        raise ValueError("Export completion hash mismatch")
                    worker.kill()
                    worker.wait(timeout=30)
                    completed = True
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Export worker timed out")
                time.sleep(.2)
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=30)
            stdout.flush()
            stderr.flush()
            (directory / "worker_status.json").write_text(json.dumps({
                "terminated_by_parent_after_export": completed, "returncode": worker.returncode,
            }), encoding="utf-8")
    return subprocess.CompletedProcess(
        command, worker.returncode,
        (directory / "builder.stdout.log").read_text(encoding="utf-8", errors="replace"),
        (directory / "builder.stderr.log").read_text(encoding="utf-8", errors="replace"),
    )


def build(directory, manage_export=False):
    model = json.loads((directory / "report_model.json").read_text(encoding="utf-8"))
    started = time.time()
    command = [str(node_executable()), "--max-old-space-size=8192",
               str(ROOT / "build_performance_workbook.mjs"), str(directory)]
    if manage_export:
        command.append("--managed-export")
    process = managed_export(command, directory) if manage_export else subprocess.run(
        command,
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(process.stdout[-6000:], flush=True)
    (directory / "builder_result.json").write_text(json.dumps({
        "returncode": process.returncode, "managed_export": manage_export,
        "stdout_tail": process.stdout[-6000:], "stderr_tail": process.stderr[-4000:],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    output = directory / model["output_filename"]
    if (
        "SAVED " not in process.stdout or not output.exists()
        or output.stat().st_mtime < started - 2
    ):
        raise RuntimeError(f"Workbook build failed ({process.returncode}): {process.stderr[-4000:]}")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError(f"Invalid XLSX archive: {output}")
    # The bundled renderer may exit nonzero during shutdown after a successful
    # export. Require a new, fully readable saved package, never just an old path.
    if manage_export:
        if not json.loads((directory / "worker_status.json").read_text(encoding="utf-8"))[
                "terminated_by_parent_after_export"]:
            raise RuntimeError("Export worker was not completed by parent")
    elif process.returncode not in (0, 1):
        raise RuntimeError(f"Unexpected builder exit: {process.returncode}")
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Build a versioned PA V2 report from one site's real sampling batch.",
        epilog="Same-month batches use different --label values. Never combine two requested windows of the same node in one invocation.",
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/pa_batches")
    parser.add_argument("--audit-root", type=Path, default=ROOT / "outputs/pa_v2_audit_cache")
    parser.add_argument("--database", type=Path, default=ROOT / "outputs/history/pa_history_v2.sqlite")
    parser.add_argument("--config", type=Path, default=ROOT / "performance_config.json")
    args = parser.parse_args()
    originals = sorted(args.source.rglob("*.7z"))
    if not originals:
        raise ValueError("No PA .7z files in source directory")
    by_sha, by_node, issues = {}, {}, []
    for source in originals:
        info, sha = headers(source), source_digest(source)
        if sha in by_sha:
            issues.append({"kind": "duplicate_file", "sha256": sha, "path": str(source), "kept": str(by_sha[sha])})
            continue
        if info["node"] in by_node:
            raise ValueError(
                f"Multiple logs/windows for node {info['node']}; create separate batch labels. "
                f"{by_node[info['node']]} vs {source}"
            )
        by_node[info["node"]] = source
        by_sha[sha] = source
    safe_site, safe_label = [re.sub(r'[<>:"/\\|?*]', "_", s) for s in (args.site, args.label)]
    if safe_label in ("", ".", "..") or safe_site in ("", ".", ".."):
        raise ValueError("Invalid site or label")
    root = args.output_root.resolve() / safe_label / safe_site
    baseline, final = root / "baseline", root / "report"
    baseline.mkdir(parents=True, exist_ok=True)
    manifest = {"site": args.site, "source_sha256": sorted(by_sha), "duplicates": issues}
    existing = root / "import_manifest.json"
    if existing.exists():
        old = json.loads(existing.read_text(encoding="utf-8"))
        if old["source_sha256"] != manifest["source_sha256"]:
            raise ValueError("This output label belongs to a different source set. Use a new label.")
    existing.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    batch = []
    for sha, source in by_sha.items():
        info = headers(source)
        saved = baseline / "data" / f"{info['node']}.json"
        cached = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else None
        if not cached or cached.get("source_sha256") != sha or cached.get("collector_version") != 3:
            cached = parse_archive(source, baseline / "data")
        batch.append(cached)
        audit(source, args.audit_root)
    (baseline / "data/batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    prepare(baseline / "data", baseline / "report_model.json", args.site)
    if not (baseline / json.loads((baseline / "report_model.json").read_text(encoding="utf-8"))["output_filename"]).exists():
        build(baseline)
    config = {**DEFAULTS, **json.loads(args.config.read_text(encoding="utf-8"))}
    db = connect(args.database)
    model_path, volumes = prepare_site(baseline, final, args.audit_root, db, config)
    global_hotspots([model_path], volumes, config["top_n"])
    with db:
        for issue in issues:
            model = json.loads(model_path.read_text(encoding="utf-8"))
            db.execute("INSERT INTO audit_events(batch_id,kind,detail_json) VALUES(?,?,?)",
                       (model["batch_id"], issue["kind"], json.dumps(issue, ensure_ascii=False)))
    export_history(db, root / "history_export")
    db.close()
    output = build(final)
    from verify_performance_v2 import verify
    result = verify(final)
    print("VERIFIED", json.dumps(result, ensure_ascii=False), flush=True)
    print("REPORT", output, flush=True)


if __name__ == "__main__":
    main()
