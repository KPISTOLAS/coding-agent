"""Tool loop: model reply, tool results, repeat until a final answer."""

from __future__ import annotations

import json
import re
import uuid
from typing import Protocol

from rich.console import Console
from rich.panel import Panel

from coding_agent.llm.client import ChatReply, ToolCall
from coding_agent.prompts import SYSTEM_PROMPT
from coding_agent.tools.registry import KNOWN_TOOLS, TOOL_SCHEMAS, ToolContext, dispatch

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class MaxStepsExceeded(Exception):
    """The tool loop hit its step cap before a final answer."""


class Chatter(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatReply: ...


def run(
    task: str,
    *,
    router: Chatter,
    tools_ctx: ToolContext,
    max_steps: int = 25,
    console: Console | None = None,
) -> str:
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    for _step in range(max_steps):
        reply = router.chat(messages, tools=TOOL_SCHEMAS)
        if reply.provider_id:
            _emit(console, f"[dim]{reply.provider_id} / {reply.model}[/dim]")
        tool_calls = list(reply.tool_calls)
        from_json = False
        if not tool_calls:
            parsed = parse_json_action(reply.content)
            if parsed is not None:
                from_json = True
                tool_calls = [
                    ToolCall(
                        id=f"call_{uuid.uuid4().hex[:12]}",
                        name=parsed["action"],
                        arguments=json.dumps(parsed["arguments"]),
                    )
                ]
        if not tool_calls:
            text = (reply.content or "").strip()
            if console is not None:
                console.print(Panel(text or "(empty reply)", title="agent"))
            return text
        messages.append(_assistant_message(reply, tool_calls, drop_content=from_json))
        for call in tool_calls:
            _emit(console, f"[bold]{call.name}[/bold]")
            result = dispatch(call.name, call.arguments, tools_ctx)
            _emit(console, _first_line(result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )
    raise MaxStepsExceeded(f"stopped after {max_steps} steps")


def parse_json_action(content: str | None) -> dict | None:
    """Accept a fenced JSON action, or a message that is only that object."""
    if not content or not content.strip():
        return None
    fences = [match.strip() for match in _FENCE.findall(content)]
    if fences:
        candidates = fences
    else:
        stripped = content.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return None
        candidates = [stripped]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        action = data.get("action")
        arguments = data.get("arguments", {})
        if not isinstance(action, str) or action not in KNOWN_TOOLS:
            continue
        if not isinstance(arguments, dict):
            continue
        return {"action": action, "arguments": arguments}
    return None


def _assistant_message(
    reply: ChatReply,
    tool_calls: list[ToolCall],
    *,
    drop_content: bool,
) -> dict:
    return {
        "role": "assistant",
        "content": None if drop_content else reply.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in tool_calls
        ],
    }


def _emit(console: Console | None, message: str) -> None:
    if console is not None and message:
        console.print(message)


def _first_line(text: str) -> str:
    line = text.splitlines()[0] if text else ""
    if len(line) > 180:
        return line[:180] + "..."
    return line
