"""Regression tests for LLM JSON recovery, <think> tag stripping, and num_predict clamping."""
import pytest
import llm
from llm_manager import TASK_NUM_PREDICT


def test_parse_json_handles_preamble_and_markdown():
    """Ollama/Qwen conversational preamble with markdown fences must parse cleanly on Attempt 1."""
    raw = (
        "Here is the result of rating your clip transcript as requested:\n\n"
        "```json\n"
        "{\n"
        '  "hook_strength": 8.5,\n'
        '  "retention": 7.0,\n'
        '  "clarity": 9.0,\n'
        '  "impact": 8.0,\n'  # Trailing comma!
        "}\n"
        "```\n"
        "I hope this helps your video processing pipeline!"
    )

    parsed = llm._parse_json(raw)
    assert parsed["hook_strength"] == 8.5
    assert parsed["retention"] == 7.0
    assert parsed["clarity"] == 9.0
    assert parsed["impact"] == 8.0


def test_parse_json_strips_think_tags():
    """Reasoning models emitting <think>...</think> blocks must have reasoning cleanly removed."""
    raw = (
        "<think>\n"
        "The transcript has strong emotional hooks at the beginning.\n"
        "I will rate hook_strength high and clarity decent.\n"
        "</think>\n"
        '{"hook_strength": 9, "retention": 8, "clarity": 8, "impact": 9}'
    )

    parsed = llm._parse_json(raw)
    assert parsed["hook_strength"] == 9
    assert parsed["impact"] == 9


def test_task_num_predict_clamped():
    """LLM tasks must have strictly bounded num_predict values to prevent runaway generation."""
    assert TASK_NUM_PREDICT.get("clip_score") <= 150
    assert TASK_NUM_PREDICT.get("clip_metadata") <= 400
    assert TASK_NUM_PREDICT.get("hook_classify") <= 100
    assert TASK_NUM_PREDICT.get("virality") <= 400
