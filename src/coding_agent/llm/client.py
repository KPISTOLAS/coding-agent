"""OpenAI-compatible chat client aimed at a custom base URL."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from openai import OpenAI

# Windows Application Control can block the OpenAI SDK's native jiter module.
_use_http = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_id: str = ""
    model: str = ""


class OpenAIChatClient:
    """One short-lived client per attempt so keys and base URLs can change."""

    def __init__(self, timeout: float = 120) -> None:
        self.timeout = timeout

    def chat(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ChatReply:
        global _use_http
        if _use_http:
            return self._chat_http(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                tools=tools,
                extra_headers=extra_headers,
            )
        try:
            return self._chat_sdk(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                tools=tools,
                extra_headers=extra_headers,
            )
        except Exception as exc:
            if not _runtime_blocked(exc):
                raise
            _use_http = True
            return self._chat_http(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                tools=tools,
                extra_headers=extra_headers,
            )

    def _chat_sdk(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ChatReply:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout,
            max_retries=0,
            default_headers=extra_headers or None,
        )
        request: dict = {"model": model, "messages": messages}
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        completion = client.chat.completions.create(**request)
        if not completion.choices:
            raise RuntimeError("empty completion")
        message = completion.choices[0].message
        calls: list[ToolCall] = []
        for tool_call in message.tool_calls or []:
            function = getattr(tool_call, "function", None)
            name = getattr(function, "name", None)
            if function is None or not name:
                continue
            calls.append(
                ToolCall(
                    id=tool_call.id,
                    name=name,
                    arguments=getattr(function, "arguments", None) or "{}",
                )
            )
        return ChatReply(content=message.content, tool_calls=calls)

    def _chat_http(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ChatReply:
        payload: dict = {"model": model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "coding-agent/0.1.0",
        }
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            error = RuntimeError(body or f"http {exc.code}")
            error.status_code = exc.code  # type: ignore[attr-defined]
            error.body = body  # type: ignore[attr-defined]
            raise error from exc
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise RuntimeError("empty completion")
        return _reply_from_message(choices[0].get("message") or {})


def _reply_from_message(message: dict) -> ChatReply:
    calls: list[ToolCall] = []
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        calls.append(
            ToolCall(
                id=str(tool_call.get("id") or ""),
                name=name,
                arguments=function.get("arguments") or "{}",
            )
        )
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        content = json.dumps(content)
    return ChatReply(content=content, tool_calls=calls)


def _runtime_blocked(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "jiter" in text or "dll load failed" in text or "application control" in text
