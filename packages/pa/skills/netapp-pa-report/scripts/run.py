"""Delegate to the one managed project, using its configured local Python runtime."""

import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[3]
    entry = root / "run_pa_report.py"
    if not entry.is_file():
        raise FileNotFoundError("Install this Skill from the full netapp-pa-report repository; do not copy it alone")
    config = root / "config/project.local.json"
    local = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
    python = local.get("python") or (str(bundled) if bundled.is_file() else sys.executable)
    return subprocess.call([python, "-X", "utf8", "-B", str(entry), *sys.argv[1:]], cwd=root)


if __name__ == "__main__":
    sys.exit(main())
