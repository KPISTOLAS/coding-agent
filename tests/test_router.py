"""Router failover with a fake client."""

from coding_agent.llm.client import ChatReply
from coding_agent.llm.cooldown import CooldownStore
from coding_agent.llm.router import AllProvidersExhausted, ResolvedProvider, Router


class ScriptedError(Exception):
    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(body or f"http {status_code}")
        self.status_code = status_code
        self.body = body


class FakeClient:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def chat(self, **kwargs) -> ChatReply:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider(provider_id: str, models: list[str]) -> ResolvedProvider:
    return ResolvedProvider(
        id=provider_id,
        base_url=f"https://{provider_id}.example/v1",
        api_key="test-key",
        key_status="set",
        models=models,
        cooldown_seconds=60,
    )


def _router(tmp_path, providers, outcomes) -> tuple[Router, FakeClient]:
    client = FakeClient(outcomes)
    router = Router(
        providers,
        CooldownStore(tmp_path / "cooldowns.json"),
        client=client,
        sleep=lambda _seconds: None,
    )
    return router, client


def test_rate_limit_rotates_to_next_model(tmp_path) -> None:
    providers = [_provider("groq", ["m1", "m2"])]
    router, client = _router(
        tmp_path,
        providers,
        [ScriptedError(429, "rate limit"), ChatReply(content="ok")],
    )
    reply = router.chat([{"role": "user", "content": "hi"}])
    assert reply.content == "ok"
    assert reply.model == "m2"
    assert [call["model"] for call in client.calls] == ["m1", "m2"]
    assert router.cooldown.model_until("groq", "m1") is not None
    assert router.cooldown.model_until("groq", "m2") is None
    assert router.cooldown.provider_until("groq") is None


def test_account_quota_skips_rest_of_provider(tmp_path) -> None:
    providers = [
        _provider("groq", ["m1", "m2"]),
        _provider("openrouter", ["free"]),
    ]
    router, client = _router(
        tmp_path,
        providers,
        [ScriptedError(402, "insufficient_quota"), ChatReply(content="fallback")],
    )
    reply = router.chat([{"role": "user", "content": "hi"}])
    assert reply.content == "fallback"
    assert reply.provider_id == "openrouter"
    assert [call["model"] for call in client.calls] == ["m1", "free"]
    assert router.cooldown.provider_until("groq") is not None


def test_auth_failure_skips_provider_for_later_calls(tmp_path) -> None:
    providers = [
        _provider("groq", ["g-model"]),
        _provider("openrouter", ["o-model"]),
    ]
    router, client = _router(
        tmp_path,
        providers,
        [
            ScriptedError(401, "invalid api key"),
            ChatReply(content="ok"),
            ChatReply(content="again"),
        ],
    )
    assert router.chat([{"role": "user", "content": "hi"}]).content == "ok"
    assert router.chat([{"role": "user", "content": "hi"}]).content == "again"
    assert [call["model"] for call in client.calls] == ["g-model", "o-model", "o-model"]
    assert "groq" in router.session_skipped


def test_transient_error_retries_once(tmp_path) -> None:
    providers = [_provider("groq", ["m1"])]
    router, client = _router(
        tmp_path,
        providers,
        [ScriptedError(500, "bad gateway"), ChatReply(content="recovered")],
    )
    reply = router.chat([{"role": "user", "content": "hi"}])
    assert reply.content == "recovered"
    assert [call["model"] for call in client.calls] == ["m1", "m1"]


def test_unknown_model_is_skipped_on_the_next_call(tmp_path) -> None:
    providers = [_provider("groq", ["missing", "real"])]
    router, client = _router(
        tmp_path,
        providers,
        [
            ScriptedError(404, "model not found"),
            ChatReply(content="ok"),
            ChatReply(content="again"),
        ],
    )
    assert router.chat([{"role": "user", "content": "hi"}]).model == "real"
    assert router.chat([{"role": "user", "content": "hi"}]).model == "real"
    assert [call["model"] for call in client.calls] == ["missing", "real", "real"]


def test_single_model_rate_limit_cools_provider_and_persists(tmp_path) -> None:
    path = tmp_path / "cooldowns.json"
    client = FakeClient(
        [ScriptedError(429, "rate limit"), ChatReply(content="other")]
    )
    providers = [
        _provider("groq", ["only"]),
        _provider("kilo", ["free"]),
    ]
    router = Router(providers, CooldownStore(path), client=client, sleep=lambda _s: None)
    assert router.chat([{"role": "user", "content": "hi"}]).provider_id == "kilo"

    restored = CooldownStore(path)
    assert restored.provider_until("groq") is not None
    assert restored.model_until("groq", "only") is not None

    later = Router(
        providers,
        restored,
        client=FakeClient([ChatReply(content="still kilo")]),
        sleep=lambda _s: None,
    )
    assert later.chat([{"role": "user", "content": "hi"}]).content == "still kilo"
    assert later.client.calls[0]["model"] == "free"  # type: ignore[attr-defined]


def test_exhausted_providers_raise(tmp_path) -> None:
    providers = [_provider("groq", ["m1"])]
    router, _client = _router(tmp_path, providers, [ScriptedError(401, "invalid api key")])
    try:
        router.chat([{"role": "user", "content": "hi"}])
    except AllProvidersExhausted as exc:
        assert "auth" in str(exc)
    else:
        raise AssertionError("expected AllProvidersExhausted")
