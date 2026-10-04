"""Agent loop, including the JSON action fallback."""

import json

from coding_agent.agent import parse_json_action, run
from coding_agent.llm.client import ChatReply, ToolCall
from coding_agent.tools.registry import ToolContext


class FakeRouter:
    def __init__(self, replies: list[ChatReply]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatReply:
        self.calls.append(messages)
        return self.replies.pop(0)


def test_native_tool_call_writes_file(tmp_path) -> None:
    router = FakeRouter(
        [
            ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="write_file",
                        arguments=json.dumps({"path": "a.txt", "contents": "hi"}),
                    )
                ],
                provider_id="groq",
                model="m1",
            ),
            ChatReply(content="wrote it", provider_id="groq", model="m1"),
        ]
    )
    text = run(
        "create a file",
        router=router,
        tools_ctx=ToolContext(cwd=tmp_path, auto_yes=True),
    )
    assert text == "wrote it"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
    tool_messages = [message for message in router.calls[1] if message["role"] == "tool"]
    assert tool_messages and "wrote" in tool_messages[0]["content"]


def test_json_fallback_dry_run_does_not_write(tmp_path) -> None:
    payload = json.dumps(
        {"action": "write_file", "arguments": {"path": "a.txt", "contents": "hi"}}
    )
    router = FakeRouter(
        [
            ChatReply(content=f"```json\n{payload}\n```"),
            ChatReply(content="planned"),
        ]
    )
    text = run(
        "plan a write",
        router=router,
        tools_ctx=ToolContext(cwd=tmp_path, dry_run=True, auto_yes=True),
    )
    assert text == "planned"
    assert not (tmp_path / "a.txt").exists()
    assert "dry-run" in router.calls[1][-1]["content"]


def test_prose_that_mentions_json_is_a_final_answer(tmp_path) -> None:
    content = 'Example only: {"action": "write_file", "arguments": {"path": "a.txt", "contents": "x"}}'
    text = run(
        "explain",
        router=FakeRouter([ChatReply(content=content)]),
        tools_ctx=ToolContext(cwd=tmp_path, auto_yes=True),
    )
    assert text == content
    assert not (tmp_path / "a.txt").exists()
    assert parse_json_action(content) is None
