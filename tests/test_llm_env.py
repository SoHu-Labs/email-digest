"""LLM env preflight (DeepSeek key) and OpenCode auth.json fallback."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from email_digest.llm import complete


def test_complete_raises_clear_error_when_deepseek_key_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr(
        "agentkit.llm._litellm._AUTH_PATH", tmp_path / "nonexistent.json"
    )
    with pytest.raises(Exception, match="No DeepSeek API key"):
        complete([{"role": "user", "content": "hi"}], alias="fast")


def test_complete_uses_opencode_when_env_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"opencode": {"key": "sk-go-sub-key"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("agentkit.llm._litellm._AUTH_PATH", auth)

    captured: dict[str, Any] = {}

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    def fake_completion(**kwargs: Any) -> _Resp:
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr("agentkit.llm._litellm._post_completion", fake_completion)

    out = complete([{"role": "user", "content": "hi"}], alias="fast")
    assert out == "ok"
    assert "model" in captured
    assert "go/v1" in captured.get("api_base", "")
