# Frameza Comprehensive Regression Test Report

**Date:** 2026-11-15  
**Test Suite:** Full Repository Automated Test Suite (`tests/`)  
**Environment:** Python 3.11.9, pytest-9.1.1, Windows 11  
**Hardware:** Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 Laptop GPU

---

## Executive Summary

A complete automated regression test run was executed across the entire Frameza codebase. All test modules—spanning unit tests, pipeline integrations, AI director routines, resource safety monitors, and REST API endpoints—were evaluated.

- **Total Tests Collected:** 583
- **Passed:** 582
- **Skipped:** 1 (`test_updater.py` network test)
- **Failed:** 0
- **Total Execution Time:** 108.33 seconds (1 minute 48 seconds)
- **Success Rate:** **100%** (582 of 582 non-skipped tests)

---

## Detailed Test Suite Results by Category

### 1. Ranking Engine & Duration Protection Suites
- `tests/test_ranking_engine_audit.py` (4 tests) — **100% PASSED**
  - `test_ranking_rejects_clip_below_minimum_duration`: Verifies collapsed fragments (< min_seconds) are flagged ineligible and never kept.
  - `test_ranking_rejects_missing_output_file`: Confirms missing or truncated MP4 files are rejected.
  - `test_ranking_resolves_text_from_clip_words_over_incomplete_transcript`: Confirms recovered clip words are scored when the master transcript has gaps.
  - `test_ranking_deterministic_tie_breaking`: Verifies multi-tier deterministic tie-breaking.
- `tests/test_duration_guard.py` (2 tests) — **100% PASSED**
  - `test_style_refiner_preserves_min_duration_with_sparse_words`: Verifies ending optimizer never trims below `min_seconds` on sparse-word clips.
  - `test_style_refiner_preserves_bounds_when_no_words`: Confirms valid durations when zero words are present.
- `tests/test_highlights.py` (11 tests) — **100% PASSED**
- `tests/test_highlights_fusion.py` (9 tests) — **100% PASSED**

### 2. LLM Resilience & Resource Management Suites
- `tests/test_llm_json_recovery.py` (3 tests) — **100% PASSED**
  - `test_parse_json_handles_preamble_and_markdown`: Verifies Attempt 1 recovery for conversational markdown.
  - `test_parse_json_strips_think_tags`: Confirms `<think>` reasoning block removal.
  - `test_task_num_predict_clamped`: Verifies token limit clamping.
- `tests/test_llm.py` (22 tests) — **100% PASSED**
- `tests/test_llm_manager_concurrency.py` (2 tests) — **100% PASSED**
- `tests/test_ai_preparation_phase.py` (3 tests) — **100% PASSED**
- `tests/test_no_llm_during_render.py` (2 tests) — **100% PASSED**
- `tests/test_render_resource_limits.py` (4 tests) — **100% PASSED**
- `tests/test_resource_monitor.py` (5 tests) — **100% PASSED**

### 3. Pipeline, Audio/Visual Director & Reframing
- `tests/test_pipeline_director_integration.py` (3 tests) — **100% PASSED**
- `tests/test_pipeline_resource_recovery.py` (2 tests) — **100% PASSED**
- `tests/test_pipeline_units.py` (11 tests) — **100% PASSED**
- `tests/test_dynamic_director.py` (12 tests) — **100% PASSED**
- `tests/test_director_fallbacks.py` (4 tests) — **100% PASSED**
- `tests/test_reframe.py` (14 tests) — **100% PASSED**
- `tests/test_reframe_events.py` (6 tests) — **100% PASSED**
- `tests/test_audio_visual_speaker.py` (5 tests) — **100% PASSED**
- `tests/test_mixdown.py` (6 tests) — **100% PASSED**
- `tests/test_music.py` (8 tests) — **100% PASSED**
- `tests/test_captions.py` (9 tests) — **100% PASSED**
- `tests/test_captions_style.py` (6 tests) — **100% PASSED**
- `tests/test_video_events.py` (18 tests) — **100% PASSED**
- `tests/test_virality_v2.py` (7 tests) — **100% PASSED**

### 4. Server, API, Scheduling & Archive Suites
- `tests/test_server_api.py` (43 tests) — **100% PASSED**
- `tests/test_post_queue.py` (17 tests) — **100% PASSED**
- `tests/test_upload_scheduler.py` (35 tests) — **100% PASSED**
- `tests/test_archive.py` (21 tests) — **100% PASSED**
- `tests/test_channels.py` (12 tests) — **100% PASSED**
- `tests/test_stage3_api.py` (7 tests) — **100% PASSED**
- `tests/test_stage4_regression.py` (8 tests) — **100% PASSED**

---

## Pytest Execution Log Excerpt

```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\Project Development\Python\AI-Video-Clipper
plugins: anyio-4.14.1
collected 583 items

........................................................................ [ 12%]
........................................................................ [ 24%]
...............................................s........................ [ 37%]
........................................................................ [ 49%]
........................................................................ [ 61%]
........................................................................ [ 74%]
........................................................................ [ 86%]
........................................................................ [ 98%]
.......                                                                  [100%]

============================== warnings summary ===============================
1 warning (FastAPI starlette.testclient httpx deprecation notice)

======================= 582 passed, 1 skipped in 108.33s =======================
```
