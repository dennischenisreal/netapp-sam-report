"""Approved nine-sheet PA workflow. Customer data stays in local run directories."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from pa_runtime import node_executable
from performance_analysis import DEFAULTS
from validate_pa import COLLECTOR_VERSION, SEVEN_ZIP, headers, parse_archive, source_digest

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
TZ = timezone(timedelta(hours=8))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def leaf(value):
    if (not value or value in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value != value.rstrip(" .")
            or value.split(".")[0].upper() in {
                "CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)]}):
        raise ValueError(f"Invalid site/batch filename component: {value!r}")
    return value


def sampling_month(batch):
    months = {datetime.fromtimestamp(n["requested_start_ms"] / 1000, TZ).strftime("%Y%m") for n in batch}
    if len(months) != 1:
        raise ValueError("Use separate batches for different sampling start months")
    if any(n["requested_end_ms"] <= n["requested_start_ms"] for n in batch):
        raise ValueError("Invalid requested PA window")
    return months.pop()


def inventory(source):
    if not source.is_dir():
        raise ValueError(f"PA source directory not found: {source}")
    selected, nodes, duplicates = {}, {}, []
    for path in sorted(source.rglob("*.7z")):
        sha = source_digest(path)
        if sha in selected:
            duplicates.append({"sha256": sha, "ignored": str(path)})
            continue
        info = headers(path)
        leaf(info["node"])
        if not info["cluster_uuid"] or not info["serial_number"]:
            raise ValueError(f"PA identity missing: {path.name}")
        # Existing interval and model APIs index nodes by name. Reject ambiguity,
        # including same-named nodes in different clusters, instead of merging.
        if info["node"] in nodes:
            raise ValueError(f"Multiple PA archives/windows for node {info['node']}; split batches")
        nodes[info["node"]] = path
        selected[sha] = (path, info)
    if not selected:
        raise ValueError("No PA .7z archives found")
    sampling_month([info for _, info in selected.values()])
    return selected, duplicates


def code_fingerprint():
    paths = sorted([*ROOT.glob("*.py"), *ROOT.glob("*.mjs"),
                    ROOT / "VERSION", ROOT / "performance_config.json"])
    return {p.name: source_digest(p) for p in paths}


def doctor():
    import openpyxl
    node = node_executable()
    if not SEVEN_ZIP.is_file():
        raise FileNotFoundError("7-Zip not found; set PA_7ZIP to an existing 7z executable")
    subprocess.run([str(SEVEN_ZIP), "i"], check=True, capture_output=True)
    probe = subprocess.run(
        [str(node), "--input-type=module", "-e",
         'import {Workbook,SpreadsheetFile} from "@oai/artifact-tool";'
         'console.log(typeof Workbook.create,typeof SpreadsheetFile.exportXlsx)'],
        cwd=ROOT, capture_output=True, text=True,
    )
    if probe.returncode or "function function" not in probe.stdout:
        raise RuntimeError("Artifact Tool unavailable in project node_modules; run tools/install_skill.ps1")
    result = {"version": VERSION, "python": sys.version.split()[0],
              "openpyxl": openpyxl.__version__, "node": subprocess.check_output(
                  [str(node), "--version"], text=True).strip(), "seven_zip": "available",
              "artifact_tool": "available"}
    print(json.dumps(result, indent=2))
    return result


def baseline_data(selected, destination, reusable=None):
    destination.mkdir(parents=True, exist_ok=True)
    batch = []
    for sha, (source, info) in selected.items():
        cached = reusable / "data" / f"{info['node']}.json" if reusable else None
        value = load(cached) if cached and cached.is_file() else {}
        matches = value.get("source_sha256") == sha and value.get("collector_version") == COLLECTOR_VERSION
        matches = matches and all(value.get(k) == info[k] for k in (
            "node", "cluster_uuid", "serial_number", "requested_start_ms", "requested_end_ms"))
        samples = cached.with_name(f"{info['node']}.samples.json.gz") if cached else None
        if matches and samples.is_file():
            shutil.copy2(cached, destination / cached.name)
            shutil.copy2(samples, destination / samples.name)
        else:
            value = parse_archive(source, destination)
        batch.append(value)
    save(destination / "batch.json", batch)
    return batch


def verified_package(directory):
    model = load(directory / "report_model.json")
    if model.get("model_version") != "performance-report-v6-refined":
        raise ValueError("Publication requires the approved resource-first report format")
    filename = leaf(model["output_filename"])
    target = directory / filename
    verification = load(directory / "verification_compact.json")
    if verification.get("formula_errors") != 0 or source_digest(target) != verification.get("sha256"):
        raise ValueError("Workbook changed or verification failed")
    with zipfile.ZipFile(target) as package:
        if package.testzip():
            raise ValueError("Corrupt XLSX")
    return model, target, verification


def publish(directory, reports_root, run_manifest):
    model, source, verification = verified_package(directory)
    site = leaf(model["customer"])
    destination = reports_root.resolve() / site / source.name
    if not destination.resolve().is_relative_to(reports_root.resolve()):
        raise ValueError("Publication target escapes configured report root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects earlier samples, including simultaneous runs.
    try:
        with destination.open("xb") as output, source.open("rb") as content:
            shutil.copyfileobj(content, output)
    except FileExistsError:
        raise ValueError(f"Report already exists; use a new batch label: {destination}") from None
    if source_digest(destination) != verification["sha256"]:
        raise ValueError(f"Publication hash mismatch: {destination}")
    save(destination.with_suffix(".verification.json"), verification)
    save(destination.with_suffix(".run.json"), run_manifest)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--source", type=Path, help="One site, one sampling batch; recursively reads .7z")
    parser.add_argument("--site")
    parser.add_argument("--label", help="Unique batch, e.g. 20261118-AM-r1")
    parser.add_argument("--asup", type=Path, help="One site's ASUP snapshot directory; direct .7z children")
    parser.add_argument("--baseline-cache", type=Path, help="Explicit trusted baseline to reuse after SHA/identity checks")
    parser.add_argument("--counter-cache", type=Path, default=ROOT / ".local/counters")
    parser.add_argument("--work-root", type=Path, default=ROOT / ".local/runs")
    parser.add_argument("--local-config", type=Path, default=ROOT / "config/project.local.json")
    parser.add_argument("--config", type=Path, default=ROOT / "performance_config.json")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--publish", action="store_true", help="Copy a verified new batch to the configured report root")
    args = parser.parse_args()
    local = load(args.local_config) if args.local_config.exists() else {}
    if local.get("node"):
        os.environ["PA_NODE"] = local["node"]
    if args.doctor:
        doctor()
        return
    if not all((args.source, args.site, args.label)):
        parser.error("--source, --site and --label are required except with --doctor")
    leaf(args.site)
    leaf(args.label)
    if args.workers < 1 or args.workers > 4:
        parser.error("--workers must be between 1 and 4")
    if args.asup and (not args.asup.is_dir() or not any(args.asup.glob("*.7z"))):
        raise ValueError("--asup must contain this site's ASUP .7z files directly")
    if args.publish and not local.get("reports_root"):
        raise ValueError("Set reports_root in the private local configuration before publishing")
    config = {**DEFAULTS, **load(args.config)}
    # The initial accepted contract has fixed defaults. Custom metrics/thresholds
    # require coordinated model/header/verifier changes and a reviewed version.
    if any(config[k] != DEFAULTS[k] for k in ("cpu_high_pct", "latency_watch_ms", "network_high")):
        raise ValueError("This release requires approved thresholds 70%, 10ms, 70%; update the contract first")
    if config.get("clusters") or config.get("platforms"):
        raise ValueError("Per-cluster/platform overrides need a coordinated contract update in this release")
    doctor()
    selected, duplicates = inventory(args.source)
    month = sampling_month([info for _, info in selected.values()])
    run = args.work_root.resolve() / leaf(args.label) / leaf(args.site)
    run.mkdir(parents=True, exist_ok=False)
    state = {
        "status": "running", "version": VERSION, "site": args.site, "batch_label": args.label,
        "sampling_start_month": month, "started_at": datetime.now(TZ).isoformat(),
        "sources": [{"path": str(path.resolve()), "sha256": sha, **info}
                    for sha, (path, info) in selected.items()],
        "duplicate_files": duplicates, "analysis_config": config, "code_sha256": code_fingerprint(),
        "asup_directory": str(args.asup.resolve()) if args.asup else None,
        "baseline_cache": str(args.baseline_cache.resolve()) if args.baseline_cache else None,
    }
    state["batch_id"] = hashlib.sha256(json.dumps(
        [args.site, sorted(selected)], ensure_ascii=True).encode()).hexdigest()
    save(run / "run.json", state)
    try:
        from pa_compact_counters import collect
        from pa_asup_network import collect as collect_network
        from prepare_performance_report import prepare as prepare_baseline
        from prepare_performance_compact import prepare as prepare_compact
        from run_performance_v2 import build
        from verify_performance_compact import verify

        baseline, report = run / "baseline", run / "report"
        batch = baseline_data(selected, baseline / "data", args.baseline_cache)
        prepare_baseline(baseline / "data", baseline / "report_model.json", args.site)
        by_sha = {info["source_sha256"]: info for info in batch}
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            jobs = [pool.submit(collect, path, by_sha[sha], args.counter_cache)
                    for sha, (path, _) in selected.items()]
            for job in jobs:
                job.result()
        network = run / "asup/network_roles.json"
        if args.asup:
            collect_network(batch, args.asup, network.parent)
        else:
            save(network, {"ports": {}, "sources": []})
        prepare_compact(baseline, report, args.counter_cache, network, config)
        model = load(report / "report_model.json")
        model.update(output_filename=f"PA_Report_{args.site}_{month}_{args.label}.xlsx",
                     software_version=VERSION, batch_id=state["batch_id"], batch_label=args.label)
        save(report / "report_model.json", model)
        build(report, manage_export=True)
        verify(report, baseline)
        if code_fingerprint() != state["code_sha256"]:
            raise ValueError("Source code changed during the run; refuse delivery")
        if any(source_digest(path) != sha for sha, (path, _) in selected.items()):
            raise ValueError("PA archives changed during the run; refuse delivery")
        for source in model["sources"]:
            if source.get("type") == "ASUP网络配置快照":
                if source_digest(args.asup / source["file"]) != source["sha256"]:
                    raise ValueError("ASUP archive changed during the run; refuse delivery")
        state.update(status="verified", finished_at=datetime.now(TZ).isoformat(),
                     model_version=model["model_version"], metric_version=model["metric_version"],
                     verification=load(report / "verification_compact.json"))
        save(run / "run.json", state)
        if args.publish:
            state["published_report"] = str(publish(report, Path(local["reports_root"]), state))
            save(run / "run.json", state)
        print("REPORT", state.get("published_report", str(report / model["output_filename"])), flush=True)
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save(run / "run.json", state)
        raise


if __name__ == "__main__":
    main()
