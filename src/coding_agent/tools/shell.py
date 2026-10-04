"""Allowlisted commands locked to the workspace directory."""

from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_ALLOWLIST = frozenset({"python", "py", "pytest", "git", "rg", "pip"})
MAX_OUTPUT = 16_000
DEFAULT_TIMEOUT = 60


def executable_name(argv0: str) -> str:
    name = Path(argv0).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def run_shell(
    argv: list[str],
    cwd: Path,
    allowlist: set[str] | frozenset[str],
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        return "error: argv must be a non-empty list of strings"
    allowed = {executable_name(item) for item in allowlist}
    exe = executable_name(argv[0])
    if exe not in allowed:
        return f"error: '{exe}' is not on the allowlist ({', '.join(sorted(allowed))})"
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        return _cap(
            f"error: timed out after {timeout}s\n{_as_text(exc.stdout)}\n{_as_text(exc.stderr)}"
        )
    except OSError as exc:
        return f"error: {exc}"
    body = f"exit {completed.returncode}\n{completed.stdout}{completed.stderr}"
    return _cap(body.strip())


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _cap(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    extra = len(text) - MAX_OUTPUT
    return text[:MAX_OUTPUT] + f"\n... truncated ({extra} more chars)"
