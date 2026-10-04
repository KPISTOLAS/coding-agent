"""Workspace path checks."""

from __future__ import annotations

from pathlib import Path


class SandboxError(ValueError):
    """Raised when a path would leave the workspace."""


def resolve_inside(root: Path, user_path: str) -> Path:
    """Resolve user_path and require it to stay inside root."""
    if user_path is None or str(user_path).strip() == "":
        raise SandboxError("path is empty")
    root_resolved = root.resolve()
    raw = Path(user_path)
    candidate = raw if raw.is_absolute() else root_resolved / raw
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise SandboxError(f"cannot resolve path: {user_path}") from exc
    if not resolved.is_relative_to(root_resolved):
        raise SandboxError(f"path escapes workspace: {user_path}")
    return resolved
