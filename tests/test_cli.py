"""Shipped provider config and the providers command."""

from pathlib import Path

from typer.testing import CliRunner

from coding_agent.cli import app
from coding_agent.config import load_config, resolve_providers

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_shipped_providers_skip_missing_keys(monkeypatch) -> None:
    for name in (
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "LLM7_API_KEY",
        "MISTRAL_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    resolved = resolve_providers(load_config(ROOT / "providers.yaml"))
    by_id = {provider.id: provider for provider in resolved}
    assert list(by_id) == ["groq", "openrouter", "llm7", "kilo", "mistral", "gemini"]
    assert by_id["groq"].api_key is None
    assert by_id["groq"].key_status == "missing"
    assert by_id["llm7"].api_key == "not-needed"
    assert by_id["kilo"].key_status == "not_required"
    assert by_id["openrouter"].extra_headers["X-Title"] == "coding-agent"
    assert by_id["groq"].models == [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]
    assert all(
        model == "openrouter/free" or model.endswith(":free")
        for model in by_id["openrouter"].models
    )
    assert by_id["kilo"].models == ["kilo-auto/free"]
    assert by_id["gemini"].models == ["gemini-3.8-flash"]


def test_providers_command_and_missing_task(tmp_path: Path) -> None:
    cooldown = tmp_path / "cooldowns.json"
    listed = runner.invoke(
        app,
        [
            "--providers",
            "--providers-file",
            str(ROOT / "providers.yaml"),
            "--cooldown-file",
            str(cooldown),
        ],
    )
    assert listed.exit_code == 0
    assert "groq" in listed.stdout
    assert "kilo" in listed.stdout
    assert "not required" in listed.stdout
    assert "openai/gpt-oss-120b" in listed.stdout

    missing = runner.invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--providers-file",
            str(ROOT / "providers.yaml"),
            "--cooldown-file",
            str(cooldown),
        ],
    )
    assert missing.exit_code == 1
    assert "Usage" in missing.stdout or "usage" in missing.output.lower()

    options_after_task = runner.invoke(
        app,
        [
            "do the task",
            "--cwd",
            str(tmp_path),
            "--providers-file",
            str(tmp_path / "missing.yaml"),
        ],
    )
    assert options_after_task.exit_code == 1
    assert "No such command" not in options_after_task.output
    assert "not found" in options_after_task.output.lower()


def test_providers_file_has_no_secrets() -> None:
    for line in (ROOT / "providers.yaml").read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("api_key_env:"):
            continue
        value = line.split(":", 1)[1].strip()
        assert value == "null" or value.isidentifier() and value.isupper()
