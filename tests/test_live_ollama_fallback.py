"""Test live Ollama execution and seamless fallback when primary returns empty response."""
import pytest
import llm
import llm_manager


def test_ollama_empty_response_triggers_fallback(monkeypatch):
    """When a model returns HTTP 200 with an empty response string (e.g. thinking runaway),
    it must raise LLMError and trigger fallback_model instead of returning empty."""
    call_counts = {"primary": 0, "fallback": 0}

    class FakeResp:
        def __init__(self, text, eval_count=100, done_reason="length"):
            self.status_code = 200
            self._data = {"response": text, "eval_count": eval_count, "done_reason": done_reason}
        def json(self):
            return self._data

    def fake_post(url, json=None, timeout=None):
        model = json.get("model")
        if model == "qwen3.5:4b":
            call_counts["primary"] += 1
            # Simulate empty response from primary model
            return FakeResp("")
        elif model == "llama3.2:3b":
            call_counts["fallback"] += 1
            return FakeResp('{"hook_strength": 8.0, "retention": 7.0, "clarity": 8.5, "impact": 9.0}', done_reason="stop")
        return FakeResp("")

    monkeypatch.setattr("requests.post", fake_post)

    cfg = {
        "llm": {
            "provider": "ollama",
            "ollama_model": "qwen3.5:4b",
            "ollama_fallback_model": "llama3.2:3b",
            "max_retries": 1,
            "backoff_base_seconds": 0.05
        }
    }

    result = llm.complete_json(
        task="clip_score",
        schema_name="clip_score",
        prompt="Rate clip",
        provider="ollama",
        cfg=cfg
    )

    assert call_counts["primary"] >= 1, "Primary model must have been attempted"
    assert call_counts["fallback"] >= 1, "Fallback model must have been invoked when primary returned empty"
    assert result["hook_strength"] == 8.0
    assert result["impact"] == 9.0
