"""Load providers.yaml and resolve API keys from the environment."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from coding_agent.llm.router import ResolvedProvider

PLACEHOLDER_API_KEY = "not-needed"


class ProviderConfig(BaseModel):
    id: str
    base_url: str
    api_key_env: str | None = None
    api_key_required: bool = True
    models: list[str]
    cooldown_seconds: int = 60
    extra_headers: dict[str, str] = Field(default_factory=dict)


class AppConfig(BaseModel):
    providers: list[ProviderConfig]


def find_providers_file(explicit: Path | None = None) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"providers file not found: {path}")
        return path
    candidates = [Path.cwd() / "providers.yaml"]
    try:
        candidates.append(Path(__file__).resolve().parents[2] / "providers.yaml")
    except IndexError:
        pass
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"providers.yaml not found. Looked in: {searched}")


def load_env(providers_file: Path) -> None:
    """Load .env without overriding variables that are already set."""
    load_dotenv(Path.cwd() / ".env")
    providers_dir = providers_file.parent.resolve()
    if providers_dir != Path.cwd().resolve():
        load_dotenv(providers_dir / ".env")


def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return AppConfig.model_validate(data)


def resolve_providers(config: AppConfig) -> list[ResolvedProvider]:
    resolved: list[ResolvedProvider] = []
    for provider in config.providers:
        api_key, key_status = _resolve_key(provider)
        resolved.append(
            ResolvedProvider(
                id=provider.id,
                base_url=provider.base_url,
                api_key=api_key,
                key_status=key_status,
                models=list(provider.models),
                cooldown_seconds=provider.cooldown_seconds,
                extra_headers=dict(provider.extra_headers),
                api_key_env=provider.api_key_env,
            )
        )
    return resolved


def _resolve_key(provider: ProviderConfig) -> tuple[str | None, str]:
    if provider.api_key_env is None:
        return PLACEHOLDER_API_KEY, "not_required"
    value = os.environ.get(provider.api_key_env, "").strip()
    if value:
        return value, "set"
    if not provider.api_key_required:
        return PLACEHOLDER_API_KEY, "not_required"
    return None, "missing"
