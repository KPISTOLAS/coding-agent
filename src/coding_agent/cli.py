"""Terminal entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from coding_agent.agent import MaxStepsExceeded, run
from coding_agent.config import find_providers_file, load_config, load_env, resolve_providers
from coding_agent.llm.cooldown import CooldownStore, default_cooldown_path
from coding_agent.llm.router import AllProvidersExhausted, Router
from coding_agent.tools.registry import ToolContext
from coding_agent.tools.shell import DEFAULT_ALLOWLIST

console = Console()


def main(
    ctx: typer.Context,
    task: str | None = typer.Argument(None, help="Task for the agent to carry out."),
    cwd: Path | None = typer.Option(
        None,
        "--cwd",
        help="Workspace the agent may read and edit. Defaults to the current directory.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation for writes and shell."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Read and search only. Do not write files or run commands.",
    ),
    show_providers: bool = typer.Option(
        False,
        "--providers",
        help="Show which keys are set and which providers are cooling down.",
    ),
    allow: list[str] | None = typer.Option(
        None,
        "--allow",
        help="Extra executable name allowed for run_shell. Repeat for more than one.",
    ),
    max_steps: int = typer.Option(25, "--max-steps", help="Maximum model turns."),
    providers_file: Path | None = typer.Option(
        None,
        "--providers-file",
        help="Path to providers.yaml. Defaults to ./providers.yaml or the project file.",
    ),
    cooldown_file: Path | None = typer.Option(
        None,
        "--cooldown-file",
        help="Cooldown JSON path. Defaults to ~/.coding-agent/cooldowns.json.",
    ),
) -> None:
    """Run a coding task, or pass --providers to inspect failover state."""
    try:
        config_path = find_providers_file(providers_file)
        load_env(config_path)
        config = load_config(config_path)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    resolved = resolve_providers(config)
    cooldown = CooldownStore((cooldown_file or default_cooldown_path()).expanduser())
    if show_providers:
        _print_providers(resolved, cooldown)
        raise typer.Exit()
    if not task:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=1)

    workspace = (cwd or Path.cwd()).resolve()
    if not workspace.is_dir():
        console.print(f"[red]workspace is not a directory: {workspace}[/red]")
        raise typer.Exit(code=1)

    allowlist = set(DEFAULT_ALLOWLIST)
    for name in allow or []:
        cleaned = name.strip().lower()
        if cleaned:
            allowlist.add(cleaned)

    router = Router(
        resolved,
        cooldown,
        on_event=lambda message: console.print(f"[dim]{message}[/dim]"),
    )
    tools_ctx = ToolContext(
        cwd=workspace,
        dry_run=dry_run,
        auto_yes=yes,
        allowlist=allowlist,
        confirm=lambda message: typer.confirm(message, default=False),
    )
    try:
        run(task, router=router, tools_ctx=tools_ctx, max_steps=max_steps, console=console)
    except (AllProvidersExhausted, MaxStepsExceeded) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


_typer = typer.Typer(add_completion=False)
_typer.command(name="agent", no_args_is_help=True)(main)
app = _typer


def _print_providers(resolved: list, cooldown: CooldownStore) -> None:
    table = Table(title="Providers")
    table.add_column("Provider", overflow="fold")
    table.add_column("Key", overflow="fold")
    table.add_column("Cooldown", overflow="fold")
    table.add_column("Models", overflow="fold")
    for provider in resolved:
        if provider.key_status == "set":
            key = "set"
        elif provider.key_status == "missing":
            key = f"missing {provider.api_key_env}" if provider.api_key_env else "missing"
        else:
            key = "not required"
        until = cooldown.provider_until(provider.id)
        models: list[str] = []
        for model in provider.models:
            model_until = cooldown.model_until(provider.id, model)
            if model_until is None:
                models.append(model)
            else:
                models.append(f"{model} (until {model_until.isoformat()})")
        table.add_row(
            provider.id,
            key,
            until.isoformat() if until else "",
            "\n".join(models),
        )
    console.print(table)
