# FRAMEZA — STAGE 8.3 ADVERSARIAL TEST REPORT

**Project:** Frameza AI Video Clipper  
**Target Hardware:** Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM  
**Environment:** Windows 11, Python 3.11.9  
**Date:** November 2026  
**Status:** ALL TESTS PASSED (596 PASSED, 1 SKIPPED, 0 FAILED)

---

## 1. Executive Summary

To challenge the stability and edge-case resilience of the Frameza pipeline, a comprehensive adversarial test battery was authored in `tests/test_stage_8_3_adversarial.py`. The suite intentionally injects corrupted data, extreme numerical anomalies, missing speech boundaries, empty LLM reasoning channels, and disabled caption flags.

All 7 adversarial scenarios passed cleanly, confirming robust mathematical and logical safety. Furthermore, the complete full repository test suite executed with **596 passed, 1 skipped, 0 failed**.

---

## 2. Adversarial Test Results

| Test Function | Target Vulnerability | Test Conditions / Injections | Result |
| :--- | :--- | :--- | :--- |
| `test_dense_speech_with_high_intensity_event_outside_shortlist` | Event Starvation (Audit A) | 300s video with dense speech (first 60s) and an intense laughter burst (intensity 9.5/10) at 225s–235s outside speech span. | **PASSED** (Candidate generated at 225s, score >= 7.0) |
| `test_safe_num_handles_nan_inf_none_strings` | Numerical Boundaries (Audit B) | Tested `None`, `float("nan")`, `float("inf")`, `float("-inf")`, `"invalid_string"`, `"8.75"`, `150.0`, `-20.0`. | **PASSED** (All converted to valid floats within bounds) |
| `test_ranking_engine_survives_nan_and_inf_scores` | Ranking Crashes (Audit B) | Injected `float("nan")`, `float("inf")`, `None`, and `"not-a-number"` strings into clip scores and virality payloads. | **PASSED** (Zero exceptions, non-NaN/non-Inf output scores) |
| `test_ranking_engine_transcript_status_classification` | Transcript Status (Audit B) | Tested clips with empty text, clip-level recovered words, and full transcripts. | **PASSED** (Accurately assigned `missing`, `recovered`, `complete`) |
| `test_ranking_deterministic_tie_breaking_order` | Ranking Determinism (Audit B) | Identical composite scores, virality scores, and candidate scores evaluated for sweet-spot proximity and index tie-break. | **PASSED** (Deterministic, reproducible sorting order) |
| `test_llm_manager_empty_response_triggers_fallback` | Silent Ollama Retries (Audit C) | Primary model (`qwen3.5:4b`) emits `<think>...</think>` with empty output text. | **PASSED** (Detected empty text, switched to `llama3.2:3b` in 0.0s) |
| `test_pipeline_ai_prep_word_repair_runs_when_captions_disabled` | Decoupled Word Repair (Audit A) | `cfg["captions"]["enabled"] = False` with missing clip words. | **PASSED** (`repair_clip_transcription` invoked, words assigned) |

---

## 3. Test Execution Logs

```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\Project Development\Python\AI-Video-Clipper
plugins: anyio-4.14.1
collected 7 items

tests/test_stage_8_3_adversarial.py::test_dense_speech_with_high_intensity_event_outside_shortlist PASSED [ 14%]
tests/test_stage_8_3_adversarial.py::test_safe_num_handles_nan_inf_none_strings PASSED [ 28%]
tests/test_stage_8_3_adversarial.py::test_ranking_engine_survives_nan_and_inf_scores PASSED [ 42%]
tests/test_stage_8_3_adversarial.py::test_ranking_engine_transcript_status_classification PASSED [ 57%]
tests/test_stage_8_3_adversarial.py::test_ranking_deterministic_tie_breaking_order PASSED [ 71%]
tests/test_stage_8_3_adversarial.py::test_llm_manager_empty_response_triggers_fallback PASSED [ 85%]
tests/test_stage_8_3_adversarial.py::test_pipeline_ai_prep_word_repair_runs_when_captions_disabled PASSED [100%]

============================= 7 passed in 24.40s ==============================
```

---

## 4. Full Regression Suite Summary

```text
============================= Full Test Suite Summary =============================
Unit & Integration Tests (`pytest tests/`): 591 passed, 1 skipped in 111.02s
End-to-End Pipeline Tests (`pytest scripts/test_pipeline_e2e.py`): 5 passed in 24.30s
TOTAL REPOSITORY TEST SUITE: 596 passed, 1 skipped, 0 failed
Pass Rate: 100.0%
```
