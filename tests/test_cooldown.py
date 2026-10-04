"""Cooldown file persistence."""

from datetime import datetime, timedelta, timezone

from coding_agent.llm.cooldown import CooldownStore


def test_cooldown_survives_new_instance(tmp_path) -> None:
    path = tmp_path / "cooldowns.json"
    store = CooldownStore(path)
    store.mark_provider("groq", 3600)
    store.mark_model("openrouter", "openrouter/free", 3600)

    restored = CooldownStore(path)
    assert restored.provider_until("groq") is not None
    assert restored.model_until("openrouter", "openrouter/free") is not None
    assert restored.provider_until("mistral") is None


def test_cooldown_expires(tmp_path) -> None:
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    path = tmp_path / "cooldowns.json"
    store = CooldownStore(path, now=lambda: clock["now"])
    store.mark_model("groq", "openai/gpt-oss-120b", 60)
    assert store.model_until("groq", "openai/gpt-oss-120b") is not None

    clock["now"] = clock["now"] + timedelta(seconds=61)
    assert store.model_until("groq", "openai/gpt-oss-120b") is None

    reloaded = CooldownStore(path, now=lambda: clock["now"])
    assert reloaded.model_until("groq", "openai/gpt-oss-120b") is None
    assert reloaded.active() == []
