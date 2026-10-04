"""Workspace sandbox and allowlisted shell."""

import json
import sys
from pathlib import Path

import pytest

from coding_agent.tools.files import list_dir, read_file, search_code, write_file
from coding_agent.tools.paths import SandboxError
from coding_agent.tools.registry import ToolContext, dispatch
from coding_agent.tools.shell import DEFAULT_ALLOWLIST, executable_name, run_shell


def test_write_stays_inside_workspace(tmp_path: Path) -> None:
    message = write_file(tmp_path, "sub/a.txt", "hello")
    assert (tmp_path / "sub" / "a.txt").read_text(encoding="utf-8") == "hello"
    assert "sub/a.txt" in message


def test_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(SandboxError):
        write_file(tmp_path, "../outside.txt", "nope")
    with pytest.raises(SandboxError):
        write_file(tmp_path, str(outside), "nope")
    assert not outside.exists()


def test_read_file_numbers_lines(tmp_path: Path) -> None:
    write_file(tmp_path, "note.txt", "one\ntwo\nthree\n")
    text = read_file(tmp_path, "note.txt", offset=2, limit=2)
    assert text == "2|two\n3|three"


def test_search_finds_a_line(tmp_path: Path) -> None:
    write_file(tmp_path, "pkg/util.py", "def unique_marker():\n    return 1\n")
    found = search_code(tmp_path, "unique_marker", glob="*.py")
    assert "pkg/util.py" in found.replace("\\", "/")
    assert "unique_marker" in found
    assert search_code(tmp_path, "[unterminated").startswith("error: invalid regex")


def test_list_dir(tmp_path: Path) -> None:
    write_file(tmp_path, "pkg/util.py", "x")
    listing = list_dir(tmp_path, "pkg")
    assert "util.py" in listing


def test_shell_allowlist_and_cwd(tmp_path: Path) -> None:
    denied = run_shell(["cmd", "/c", "echo hi"], tmp_path, DEFAULT_ALLOWLIST)
    assert denied.startswith("error:")
    assert "not on the allowlist" in denied
    assert not (tmp_path / "hi").exists()

    allow = set(DEFAULT_ALLOWLIST) | {executable_name(sys.executable)}
    result = run_shell(
        [sys.executable, "-c", "open('marker.txt','w',encoding='utf-8').write('1')"],
        tmp_path,
        allow,
    )
    assert result.startswith("exit 0")
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "1"


def test_dispatch_rejects_escape_and_cancelled_write(tmp_path: Path) -> None:
    ctx = ToolContext(cwd=tmp_path, auto_yes=True)
    escaped = dispatch(
        "write_file",
        json.dumps({"path": "../outside.txt", "contents": "x"}),
        ctx,
    )
    assert escaped.startswith("error:")
    assert not (tmp_path.parent / "outside.txt").exists()

    cancelled = dispatch(
        "write_file",
        json.dumps({"path": "a.txt", "contents": "hi"}),
        ToolContext(cwd=tmp_path, confirm=lambda _message: False),
    )
    assert cancelled == "cancelled by user"
    assert not (tmp_path / "a.txt").exists()


def test_dry_run_does_not_write_or_run(tmp_path: Path) -> None:
    ctx = ToolContext(cwd=tmp_path, dry_run=True, auto_yes=True)
    preview = dispatch(
        "write_file",
        json.dumps({"path": "a.txt", "contents": "hi"}),
        ctx,
    )
    assert preview.startswith("dry-run:")
    assert not (tmp_path / "a.txt").exists()

    shell_preview = dispatch(
        "run_shell",
        json.dumps({"argv": ["python", "-c", "open('ran.txt','w').write('1')"]}),
        ctx,
    )
    assert shell_preview.startswith("dry-run:")
    assert not (tmp_path / "ran.txt").exists()
