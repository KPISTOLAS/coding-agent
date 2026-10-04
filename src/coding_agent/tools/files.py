"""Read, write, list, and search files inside a workspace."""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

from coding_agent.tools.paths import SandboxError, resolve_inside

_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".coding-agent"}
_MAX_MATCHES = 50
_MAX_READ_LINES = 2000
_DEFAULT_READ_LINES = 400


def read_file(
    root: Path,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    dest = resolve_inside(root, path)
    if not dest.is_file():
        return f"error: file not found: {path}"
    start = 1 if offset is None else offset
    if start < 1:
        return "error: offset must be >= 1"
    max_lines = _DEFAULT_READ_LINES if limit is None else limit
    if max_lines < 1:
        return "error: limit must be >= 1"
    max_lines = min(max_lines, _MAX_READ_LINES)
    lines: list[str] = []
    with dest.open("r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if number < start:
                continue
            if len(lines) >= max_lines:
                lines.append(f"... truncated after {max_lines} lines")
                break
            lines.append(f"{number}|{line.rstrip()}")
    if not lines:
        return "(no lines in range)"
    return "\n".join(lines)


def write_file(root: Path, path: str, contents: str) -> str:
    if not isinstance(contents, str):
        return "error: contents must be a string"
    dest = resolve_inside(root, path)
    if dest.exists() and dest.is_dir():
        raise SandboxError("refusing to write a directory")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(contents, encoding="utf-8")
    relative = dest.relative_to(root.resolve())
    return f"wrote {len(contents)} bytes to {relative.as_posix()}"


def list_dir(root: Path, path: str = ".", pattern: str | None = None) -> str:
    directory = resolve_inside(root, path)
    if not directory.is_dir():
        return f"error: not a directory: {path}"
    root_resolved = root.resolve()
    entries = sorted(directory.glob(pattern) if pattern else directory.iterdir())
    lines: list[str] = []
    for entry in entries:
        try:
            resolved = entry.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        kind = "dir" if entry.is_dir() else "file"
        lines.append(f"{kind}\t{resolved.relative_to(root_resolved).as_posix()}")
        if len(lines) >= 200:
            lines.append("... truncated")
            break
    return "\n".join(lines) if lines else "(empty)"


def search_code(
    root: Path,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"error: invalid regex: {exc}"
    base = resolve_inside(root, path)
    if not base.exists():
        return f"error: path not found: {path}"
    if shutil.which("rg"):
        return _search_rg(root, base, pattern, glob)
    return _search_python(root, base, regex, glob)


def _search_rg(root: Path, base: Path, pattern: str, glob: str | None) -> str:
    relative = base.resolve().relative_to(root.resolve()).as_posix()
    command = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--max-count",
        "20",
        "--glob",
        "!.git/**",
        "--glob",
        "!.venv/**",
        "--glob",
        "!venv/**",
        "--glob",
        "!__pycache__/**",
        "--glob",
        "!node_modules/**",
    ]
    if glob:
        command.extend(["--glob", glob])
    command.extend(["-e", pattern, relative])
    try:
        completed = subprocess.run(
            command,
            cwd=root.resolve(),
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: rg failed: {exc}"
    if completed.returncode == 1:
        return "(no matches)"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"rg exited {completed.returncode}"
        return f"error: {detail}"
    return completed.stdout.strip() or "(no matches)"


def _search_python(root: Path, base: Path, regex: re.Pattern[str], glob: str | None) -> str:
    root_resolved = root.resolve()
    files = [base] if base.is_file() else _walk_files(base)
    matches: list[str] = []
    for file_path in files:
        relative = file_path.resolve().relative_to(root_resolved).as_posix()
        if glob and not (file_path.match(glob) or fnmatch.fnmatch(relative, glob)):
            continue
        try:
            if file_path.stat().st_size > 1_000_000:
                continue
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{relative}:{number}:{line.strip()}")
                if len(matches) >= _MAX_MATCHES:
                    matches.append("... truncated")
                    return "\n".join(matches)
    return "\n".join(matches) if matches else "(no matches)"


def _walk_files(base: Path) -> list[Path]:
    found: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(base).parts):
            continue
        found.append(path)
        if len(found) > 5000:
            break
    return found
