"""Resolve existing local runtimes without installing or changing dependencies."""

import os
from pathlib import Path
import shutil

DEPENDENCIES = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"


def node_executable():
    override = os.environ.get("PA_NODE")
    if override:
        return Path(override)
    bundled = DEPENDENCIES / "node/bin/node.exe"
    if bundled.is_file():
        return bundled
    found = shutil.which("node")
    if not found:
        raise FileNotFoundError("Node.js not found; set PA_NODE to an existing executable")
    return Path(found)
