"""Build and verify a source-only PA package, or stage its reviewed suite mirror."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

from tools.check_source_privacy import candidates, problems

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "source-inventory.json"
PREFIX = "netapp-pa-report/"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name
            or p.as_posix() != name or name == "." or any(s.endswith((" ", ".")) for s in p.parts)):
        raise ValueError(f"Unsafe package path: {name}")
    return name


def reviewed_sources(root=ROOT):
    files = {}
    for name in candidates(root):
        if name == INVENTORY:
            continue
        safe_name(name)
        path = root / name
        current = path
        while current != root:
            if current.is_symlink() or current.is_junction():
                raise ValueError(f"Linked source not portable: {name}")
            current = current.parent
        raw = path.read_bytes()
        if problems(name, raw.decode("utf-8")):
            raise ValueError(f"Source privacy review failed: {name}")
        files[name] = raw
    if not files:
        raise ValueError("Empty source inventory")
    return files


def inventory(files):
    return {
        "schema_version": 1, "report": "pa", "version": files["VERSION"].decode().strip(),
        "kind": "source-install-candidate",
        "files": [{"path": name, "bytes": len(raw), "sha256": digest(raw)}
                  for name, raw in sorted(files.items())],
    }


def validate_payload(files, manifest):
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "source-install-candidate":
        raise ValueError("Unsupported PA source inventory")
    recorded = manifest["files"]
    if len(recorded) != len({r["path"].casefold() for r in recorded}):
        raise ValueError("Duplicate source inventory path")
    if set(files) != {r["path"] for r in recorded}:
        raise ValueError("Payload differs from exact source inventory")
    for entry in recorded:
        name = safe_name(entry["path"])
        raw = files[name]
        if len(raw) != entry["bytes"] or digest(raw) != entry["sha256"]:
            raise ValueError(f"Source hash mismatch: {name}")
        if problems(name, raw.decode("utf-8")):
            raise ValueError(f"Private or unexpected payload: {name}")
    if files["VERSION"].decode().strip() != manifest["version"]:
        raise ValueError("Package version mismatch")


def verify(archive):
    with zipfile.ZipFile(archive) as stream:
        if stream.testzip():
            raise ValueError("Package ZIP CRC failed")
        names = stream.namelist()
        if len(names) != len({name.casefold() for name in names}):
            raise ValueError("Duplicate archive path")
        if any(not name.startswith(PREFIX) for name in names):
            raise ValueError("Unexpected package root")
        files = {}
        for name in names:
            relative = safe_name(name[len(PREFIX):])
            if (stream.getinfo(name).external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Linked package member")
            files[relative] = stream.read(name)
        manifest = json.loads(files.pop(INVENTORY))
        validate_payload(files, manifest)
    return {"version": manifest["version"], "source_files": len(files),
            "sha256": digest(archive.read_bytes()), "kind": manifest["kind"]}


def build(output, root=ROOT):
    files = dict(reviewed_sources(root))
    manifest = inventory(files)
    validate_payload(files, manifest)
    output.mkdir(parents=True, exist_ok=False)
    archive = output / f"netapp-pa-report-{manifest['version']}-source.zip"
    payload = {**files, INVENTORY: json.dumps(manifest, indent=2).encode("utf-8")}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as stream:
        for name, raw in sorted(payload.items()):
            info = zipfile.ZipInfo(PREFIX + name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            stream.writestr(info, raw)
    result = verify(archive)
    (output / "SHA256SUMS.txt").write_text(f"{result['sha256']}  {archive.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(archive), **result}, indent=2))
    return archive


def stage_suite(suite, expected_base, apply=False, root=ROOT):
    suite, root = suite.resolve(), root.resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=suite, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=suite, text=True).strip()
    if head != expected_base or branch in ("main", "master", ""):
        raise ValueError("Use a non-default suite branch at the explicitly reviewed base commit")
    target = suite / "packages/pa"
    if not target.resolve().is_relative_to(suite) or root == target.resolve():
        raise ValueError("Invalid suite staging target")
    current = target
    while current != suite:
        if current.is_symlink() or current.is_junction():
            raise ValueError("Linked suite target")
        current = current.parent
    files = dict(reviewed_sources(root))
    manifest = inventory(files)
    validate_payload(files, manifest)
    files[INVENTORY] = json.dumps(manifest, indent=2).encode("utf-8")
    if target.exists():
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z", "packages/pa"], cwd=suite,
        ).decode().split("\0")
        known = {n.removeprefix("packages/pa/") for n in tracked if n}
        if known - set(files):
            raise ValueError("Obsolete mirror files require explicit review; automatic deletion is disabled")
        if any(not (target / name).is_file() for name in known):
            raise ValueError("Tracked mirror file is missing; reconcile the independent deletion")
        for path in target.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Linked mirror file")
            if not path.is_file():
                continue
            name = path.relative_to(target).as_posix()
            if name not in files:
                raise ValueError(f"Unreviewed mirror file: {name}")
            if path.read_bytes() == files[name]:
                continue
            if name not in known:
                raise ValueError(f"Uncommitted mirror differs: {name}")
            previous = subprocess.check_output(["git", "show", f"HEAD:packages/pa/{name}"], cwd=suite)
            if path.read_bytes() != previous:
                raise ValueError(f"Mirror independently edited: {name}")
    if apply:
        for name, raw in files.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        validate_payload({k: v for k, v in files.items() if k != INVENTORY}, manifest)
    result = {"mode": "staged" if apply else "preview", "report": "pa", "files": len(files),
              "suite_branch": branch, "base_commit": head, "remote_writes": False}
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("build")
    package.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("archive", type=Path)
    stage = commands.add_parser("stage-suite")
    stage.add_argument("--suite", type=Path, required=True)
    stage.add_argument("--expected-base", required=True)
    stage.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        build(args.output)
    elif args.command == "verify":
        print(json.dumps(verify(args.archive), indent=2))
    else:
        stage_suite(args.suite, args.expected_base, args.apply)


if __name__ == "__main__":
    main()
