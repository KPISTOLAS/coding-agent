"""Persist provider and model cooldowns across process restarts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def default_cooldown_path() -> Path:
    return Path.home() / ".coding-agent" / "cooldowns.json"


@dataclass(frozen=True)
class CooldownEntry:
    scope: str
    provider: str
    model: str | None
    until: datetime


class CooldownStore:
    """JSON file of cooldown deadlines. Expired rows are dropped on load and save."""

    def __init__(
        self,
        path: Path,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._entries: list[CooldownEntry] = []
        self._load()

    def mark_provider(self, provider_id: str, seconds: int) -> None:
        self._mark("provider", provider_id, None, seconds)

    def mark_model(self, provider_id: str, model: str, seconds: int) -> None:
        self._mark("model", provider_id, model, seconds)

    def provider_until(self, provider_id: str) -> datetime | None:
        return self._until("provider", provider_id, None)

    def model_until(self, provider_id: str, model: str) -> datetime | None:
        return self._until("model", provider_id, model)

    def active(self) -> list[CooldownEntry]:
        self._prune()
        return list(self._entries)

    def _mark(self, scope: str, provider_id: str, model: str | None, seconds: int) -> None:
        until = self._now() + timedelta(seconds=seconds)
        self._entries = [
            entry
            for entry in self._entries
            if not _same(entry, scope, provider_id, model)
        ]
        self._entries.append(
            CooldownEntry(scope=scope, provider=provider_id, model=model, until=until)
        )
        self._save()

    def _until(self, scope: str, provider_id: str, model: str | None) -> datetime | None:
        self._prune()
        for entry in self._entries:
            if _same(entry, scope, provider_id, model):
                return entry.until
        return None

    def _prune(self) -> None:
        now = self._now()
        self._entries = [entry for entry in self._entries if entry.until > now]

    def _load(self) -> None:
        if not self.path.is_file():
            self._entries = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._entries = []
            return
        entries: list[CooldownEntry] = []
        for item in raw.get("entries", []):
            try:
                until = datetime.fromisoformat(item["until"])
            except (KeyError, TypeError, ValueError):
                continue
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            model = item.get("model")
            entries.append(
                CooldownEntry(
                    scope=str(item.get("scope", "")),
                    provider=str(item.get("provider", "")),
                    model=str(model) if model else None,
                    until=until,
                )
            )
        self._entries = entries
        self._prune()

    def _save(self) -> None:
        self._prune()
        payload = {
            "entries": [
                {
                    "scope": entry.scope,
                    "provider": entry.provider,
                    "model": entry.model,
                    "until": entry.until.isoformat(),
                }
                for entry in self._entries
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _same(entry: CooldownEntry, scope: str, provider_id: str, model: str | None) -> bool:
    return entry.scope == scope and entry.provider == provider_id and entry.model == model
