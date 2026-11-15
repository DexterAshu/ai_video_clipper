"""Tests for Ollama Concurrency and Model Eviction (Fix D & Fix F)."""
from __future__ import annotations

import threading
import time
import unittest.mock as mock
import pytest

import llm_manager


def test_concurrent_ollama_requests_are_serialized():
    """Verify that multiple concurrent threads calling query_ollama are executed sequentially."""
    active_calls = 0
    max_concurrent = 0
    lock = threading.Lock()

    def fake_post(*args, **kwargs):
        nonlocal active_calls, max_concurrent
        with lock:
            active_calls += 1
            if active_calls > max_concurrent:
                max_concurrent = active_calls
        time.sleep(0.05)
        with lock:
            active_calls -= 1
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"response": "{\"test\": true}", "eval_count": 10}
        return resp

    with mock.patch("requests.post", side_effect=fake_post):
        threads = []
        for i in range(5):
            t = threading.Thread(target=llm_manager.query_ollama,
                                 kwargs={"prompt": f"test {i}", "cfg": {}})
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    # The serialized lock must prevent any concurrent requests > 1
    assert max_concurrent == 1, f"Expected strictly 1 concurrent request, but got {max_concurrent}"


def test_wait_for_unload_verifies_model_eviction():
    """Verify that wait_for_unload polls /api/ps until model is no longer running."""
    # First call: model is running; second call: model is gone
    ps_responses = [
        [{"name": "qwen3.5:4b", "size": 3500000000}],
        []
    ]

    def fake_get_running(*args, **kwargs):
        if ps_responses:
            return ps_responses.pop(0)
        return []

    with mock.patch("llm_manager.unload_model", return_value=True), \
         mock.patch("llm_manager.get_running_models", side_effect=fake_get_running):
        evicted = llm_manager.wait_for_unload(model_name="qwen3.5:4b", timeout_s=5.0)
        assert evicted is True
