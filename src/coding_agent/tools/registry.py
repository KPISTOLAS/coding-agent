"""Tool schemas and the dispatcher the agent loop calls."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from coding_agent.tools.files import list_dir, read_file, search_code, write_file
from coding_agent.tools.paths import SandboxError, resolve_inside
from coding_agent.tools.shell import DEFAULT_ALLOWLIST, executable_name, run_shell

MAX_TOOL_OUTPUT = 16_000
MUTATING = frozenset({"write_file", "run_shell"})

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace. Lines are numbered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {
                        "type": "integer",
                        "description": "1-based line to start at",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to return",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or replace a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "contents": {"type": "string"},
                },
                "required": ["path", "contents"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory relative to the workspace"},
                    "glob": {"type": "string", "description": "Optional glob relative to that directory"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search file contents with a regular expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run an allowlisted command in the workspace. "
                "argv is a list; the first item must be python, py, pytest, git, rg, or pip "
                "unless the user added more names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        },
    },
]

KNOWN_TOOLS = frozenset(schema["function"]["name"] for schema in TOOL_SCHEMAS)


@dataclass
class ToolContext:
    cwd: Path
    dry_run: bool = False
    auto_yes: bool = False
    allowlist: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWLIST))
    confirm: Callable[[str], bool] = lambda _message: False


def dispatch(name: str, arguments: str, ctx: ToolContext) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return f"error: invalid arguments JSON: {exc}"
    if not isinstance(args, dict):
        return "error: arguments must be a JSON object"
    if name not in KNOWN_TOOLS:
        return f"error: unknown tool {name}"
    if name in MUTATING:
        problem = _validate_mutating(name, args, ctx)
        if problem:
            return problem
        preview = _preview(name, args)
        if ctx.dry_run:
            return f"dry-run: would {preview}"
        if not ctx.auto_yes and not ctx.confirm(preview):
            return "cancelled by user"
    try:
        result = _run(name, args, ctx)
    except (SandboxError, ValueError) as exc:
        return f"error: {exc}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {exc}"
    return _cap(result)


def _run(name: str, args: dict, ctx: ToolContext) -> str:
    if name == "read_file":
        path = _require_str(args, "path")
        return read_file(ctx.cwd, path, _optional_int(args, "offset"), _optional_int(args, "limit"))
    if name == "write_file":
        return write_file(ctx.cwd, _require_str(args, "path"), _require_str(args, "contents"))
    if name == "list_dir":
        path = args.get("path", ".")
        pattern = args.get("glob")
        if not isinstance(path, str):
            return "error: path must be a string"
        if pattern is not None and not isinstance(pattern, str):
            return "error: glob must be a string"
        return list_dir(ctx.cwd, path, pattern)
    if name == "search_code":
        path = args.get("path", ".")
        glob = args.get("glob")
        if not isinstance(path, str):
            return "error: path must be a string"
        if glob is not None and not isinstance(glob, str):
            return "error: glob must be a string"
        return search_code(ctx.cwd, _require_str(args, "pattern"), path, glob)
    if name == "run_shell":
        argv = args.get("argv")
        if not isinstance(argv, list):
            return "error: argv must be a list of strings"
        return run_shell([str(item) for item in argv], ctx.cwd, ctx.allowlist)
    return f"error: unknown tool {name}"


def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise SandboxError(f"{key} must be a string")
    return value


def _optional_int(args: dict, key: str) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxError(f"{key} must be an integer")
    return value


def _validate_mutating(name: str, args: dict, ctx: ToolContext) -> str | None:
    if name == "write_file":
        path = args.get("path")
        if not isinstance(path, str):
            return "error: path must be a string"
        if not isinstance(args.get("contents"), str):
            return "error: contents must be a string"
        try:
            resolve_inside(ctx.cwd, path)
        except SandboxError as exc:
            return f"error: {exc}"
        return None
    if name == "run_shell":
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            return "error: argv must be a non-empty list of strings"
        allowed = {executable_name(item) for item in ctx.allowlist}
        exe = executable_name(argv[0])
        if exe not in allowed:
            return f"error: '{exe}' is not on the allowlist ({', '.join(sorted(allowed))})"
    return None


def _preview(name: str, args: dict) -> str:
    if name == "write_file":
        path = args.get("path", "")
        contents = args.get("contents", "")
        size = len(contents) if isinstance(contents, str) else 0
        return f"write {path} ({size} bytes)"
    if name == "run_shell":
        argv = args.get("argv", [])
        if isinstance(argv, list):
            return "run " + subprocess.list2cmdline([str(item) for item in argv])
        return "run shell"
    return name


def _cap(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    extra = len(text) - MAX_TOOL_OUTPUT
    return text[:MAX_TOOL_OUTPUT] + f"\n... truncated ({extra} more chars)"
