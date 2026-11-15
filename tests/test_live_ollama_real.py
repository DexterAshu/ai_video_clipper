"""Live integration test against actual running Ollama daemon."""
import pytest
import llm
import os

def test_live_ollama_end_to_end_evaluation():
    # Only run if Ollama daemon is reachable
    import requests
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=1.0)
        if r.status_code != 200:
            pytest.skip("Ollama daemon not reachable")
    except Exception:
        pytest.skip("Ollama daemon not reachable")

    cfg = {
        "llm": {
            "provider": "ollama",
            "ollama_model": "qwen3.5:4b",
            "ollama_fallback_model": "llama3.2:3b",
            "max_retries": 1,
            "context_length": 8192,
            "temperature": 0.3
        }
    }

    prompt = (
        "TASK: Rate this short-form clip transcript 1-10 on each axis.\n"
        'OUTPUT SCHEMA: {"hook_strength": n, "retention": n, "clarity": n, "impact": n}\n'
        "CLIP: Ravi Gupta shares an incredible story about starting comedy."
    )

    result = llm.complete_json(
        task="clip_score",
        schema_name="clip_score",
        prompt=prompt,
        provider="ollama",
        cfg=cfg
    )

    print("\nLive Ollama result:", result)
    assert 1.0 <= result["hook_strength"] <= 10.0
    assert 1.0 <= result["retention"] <= 10.0
    assert 1.0 <= result["clarity"] <= 10.0
    assert 1.0 <= result["impact"] <= 10.0
