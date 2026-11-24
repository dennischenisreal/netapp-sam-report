"""Fail closed on unexpected source files, personal paths and common credential forms."""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_SUFFIXES = {".py", ".mjs", ".md", ".ps1"}
ROOT_FILES = {".gitignore", ".gitattributes", "VERSION", "requirements.txt",
              "performance_config.json", "source-inventory.json"}
TEXT_SUFFIXES = {".py", ".mjs", ".md", ".ps1", ".json", ".yaml", ".yml"}
PATTERNS = {
    "personal Windows profile path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\s<>\"']+", re.I),
    "personal Unix home path": re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    "GitHub token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})"),
    "private key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
}


def allowed(path):
    p = Path(path)
    if len(p.parts) == 1:
        return p.name in ROOT_FILES or p.suffix in ROOT_SUFFIXES
    if p.parts[0] == "config":
        return p.name.endswith(".example.json") and len(p.parts) == 2
    if p.parts[0] in ("docs", "tools", "skills", ".github"):
        return p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts
    return False


def problems(path, text):
    issues = []
    if not allowed(path):
        issues.append("file outside source allowlist")
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            issues.append(label)
    return issues


def candidates(root=ROOT):
    files = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
    ).decode("utf-8").split("\0")
    return sorted(set(filter(None, files)))


def main():
    failures, checked = [], 0
    for name in candidates():
        path = ROOT / name
        if not path.is_file():
            failures.append(f"{name}: missing file")
            continue
        if path.is_symlink():
            failures.append(f"{name}: symlink outside managed source")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            failures.append(f"{name}: non-text or unreadable file")
            continue
        failures.extend(f"{name}: {issue}" for issue in problems(name, text))
        checked += 1
    if not checked:
        failures.append("No source candidates found")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Source privacy preflight passed: {checked} text files; manual diff review still required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
