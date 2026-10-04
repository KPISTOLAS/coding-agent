"""Failover across providers and models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from coding_agent.llm.client import ChatReply
from coding_agent.llm.cooldown import CooldownStore
from coding_agent.llm.errors import ErrorKind, classify


class ChatClient(Protocol):
    def chat(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ChatReply: ...


class AllProvidersExhausted(Exception):
    """Every configured provider and model failed or was unavailable."""

    def __init__(self, attempts: list[str]) -> None:
        self.attempts = attempts
        detail = "\n".join(attempts) if attempts else "no providers were available"
        super().__init__(detail)


@dataclass
class ResolvedProvider:
    id: str
    base_url: str
    api_key: str | None
    key_status: str
    models: list[str]
    cooldown_seconds: int = 60
    extra_headers: dict[str, str] = field(default_factory=dict)
    api_key_env: str | None = None


class Router:
    """Try models inside a provider, then the next provider.

    Rate limits cool down a single model. Account quota cools down the provider.
    Auth failures are skipped for the life of this process. Unknown models are
    skipped for the session. Transient errors get one retry, then the next model.
    """

    def __init__(
        self,
        providers: list[ResolvedProvider],
        cooldown: CooldownStore,
        client: ChatClient | None = None,
        sleep: Callable[[float], None] | None = None,
        retry_sleep: float = 0.4,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        from coding_agent.llm.client import OpenAIChatClient

        self.providers = providers
        self.cooldown = cooldown
        self.client: ChatClient = client if client is not None else OpenAIChatClient()
        self._sleep = sleep or _default_sleep
        self.retry_sleep = retry_sleep
        self.on_event = on_event or (lambda _message: None)
        self.session_skipped: set[str] = set()
        self.session_skipped_models: set[tuple[str, str]] = set()

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatReply:
        attempts: list[str] = []
        saw_candidate = False
        for provider in self.providers:
            if provider.api_key is None:
                attempts.append(f"{provider.id}: skipped ({provider.key_status})")
                continue
            if provider.id in self.session_skipped:
                attempts.append(f"{provider.id}: skipped (auth)")
                continue
            provider_until = self.cooldown.provider_until(provider.id)
            if provider_until is not None:
                attempts.append(f"{provider.id}: cooling until {provider_until.isoformat()}")
                continue

            action = "next_provider"
            for model in provider.models:
                key = (provider.id, model)
                if key in self.session_skipped_models:
                    attempts.append(f"{provider.id}/{model}: skipped (unknown model)")
                    continue
                model_until = self.cooldown.model_until(provider.id, model)
                if model_until is not None:
                    attempts.append(
                        f"{provider.id}/{model}: cooling until {model_until.isoformat()}"
                    )
                    continue
                saw_candidate = True
                action = self._attempt(provider, model, messages, tools, attempts)
                if isinstance(action, ChatReply):
                    action.provider_id = provider.id
                    action.model = model
                    return action
                if action == "next_provider":
                    break
            if action == "next_provider":
                continue
        if not saw_candidate and not attempts:
            attempts.append("no providers with a usable key")
        raise AllProvidersExhausted(attempts)

    def _attempt(
        self,
        provider: ResolvedProvider,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        attempts: list[str],
    ) -> ChatReply | str:
        retried = False
        while True:
            self.on_event(f"trying {provider.id} / {model}")
            try:
                api_key = provider.api_key
                if api_key is None:
                    return "next_provider"
                return self.client.chat(
                    api_key=api_key,
                    base_url=provider.base_url,
                    model=model,
                    messages=messages,
                    tools=tools,
                    extra_headers=provider.extra_headers or None,
                )
            except Exception as exc:
                classified = classify(exc)
                attempts.append(f"{provider.id}/{model}: {classified.kind.value}: {classified.message}")
                self.on_event(
                    f"{provider.id} / {model} failed ({classified.kind.value})"
                )
                if classified.kind is ErrorKind.TRANSIENT and not retried:
                    retried = True
                    self._sleep(self.retry_sleep)
                    continue
                if classified.kind is ErrorKind.AUTH:
                    self.session_skipped.add(provider.id)
                    return "next_provider"
                if classified.kind is ErrorKind.MODEL_NOT_FOUND:
                    self.session_skipped_models.add((provider.id, model))
                    return "next_model"
                if classified.kind is ErrorKind.ACCOUNT_QUOTA:
                    self.cooldown.mark_provider(provider.id, provider.cooldown_seconds)
                    return "next_provider"
                if classified.kind is ErrorKind.RATE_LIMIT:
                    self.cooldown.mark_model(provider.id, model, provider.cooldown_seconds)
                    if self._all_models_blocked(provider):
                        self.cooldown.mark_provider(provider.id, provider.cooldown_seconds)
                        return "next_provider"
                    return "next_model"
                return "next_model"

    def _all_models_blocked(self, provider: ResolvedProvider) -> bool:
        for model in provider.models:
            if (provider.id, model) in self.session_skipped_models:
                continue
            if self.cooldown.model_until(provider.id, model) is not None:
                continue
            return False
        return True


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
