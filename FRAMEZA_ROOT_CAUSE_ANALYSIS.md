# Frameza Comprehensive Root-Cause Analysis (RCA)

**Date:** 2026-11-15  
**Target Hardware:** Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM, Windows 11  
**Incident Reference:** 100+ Minute Processing Run & System Contention on 80-minute Hindi Podcast (`d2b939ed_...`)

---

## Executive Summary

An investigation was conducted across the Frameza pipeline after an 80-minute (4,812.7s) podcast video took over 100 minutes to process, stalled during clip rendering, generated collapsed 0.76-second clips, and caused massive resource exhaustion (100% VRAM, 95% RAM, 100% Disk Active Time).

Six distinct technical root causes were identified, analyzed, and traced to specific source code locations.

---

## Detailed Root-Cause Findings

### 1. Highlight Candidate Starvation via Energy Shortlisting
- **Symptom:** Only 15 candidate spans totaling 1,104.7s were shortlisted out of a 4,812.7s podcast (77% of video duration un-transcribed). Later, video and audio event detection found high-energy viral moments across the entire video. When candidate clips were formed outside the 15 shortlisted spans, they had **zero transcript words**.
- **Location:** `segment.py::shortlist_spans()`
- **Impact:** Clips in un-transcribed regions arrived at `rescore_clips` with empty text (`""`), breaking ranking and AI scoring.

### 2. Ending Optimizer Duration Collapse Bug
- **Symptom:** Candidate 5 (start: 2497.0s, end: 2550.0s, duration: 53.0s) was collapsed into an invalid **0.76-second clip** (start: 2497.0s, end: 2497.76s).
- **Location:** `style_refiner.py#L510-L517`
- **Root Cause:**
  ```python
  win = _words_in(words, s0, e0)
  if win:
      last_end = win[-1]["end"]
      trimmed_e = min(e0, last_end + tail)  # BUG: No min_seconds floor!
      e0 = trimmed_e
  ```
  Because candidate 5 had 2 stray words at the very beginning (2497.00s to 2497.26s), `trimmed_e` clamped to `2497.26 + 0.50 = 2497.76s`, completely ignoring `clips.min_seconds` (15s–30s).
- **Impact:** Unplayable 0.76-second clip was rendered, scored, and erroneously kept.

### 3. Runaway Token Generation & Attempt 1 JSON Parse Failures
- **Symptom:** Stage `rescore` alone took **1,706.8s (28.4 minutes)**. Ollama/Qwen inference calls took ~115s each.
- **Location:** `llm.py` and `llm_manager.py`
- **Root Cause:**
  1. No `num_predict` cap was set in Ollama queries, allowing Qwen 3.5 4B to generate up to 8,192 tokens per single evaluation call.
  2. `llm.py::_parse_json()` called `json.loads(text)` directly without calling `repair_json(text)` on Attempt 1. Whenever Qwen emitted conversational preamble (e.g. `"Here is your JSON:"`) or `<think>` tags, Attempt 1 failed with `Expecting value: line 1 column 1 (char 0)`, burning 2–3 full 115-second retries per call.
- **Impact:** 30+ LLM queries across 10 clips generated over 60 minutes of unnecessary blocking GPU computation, causing system lockups and WebSocket connection drops (`WinError 10054`).

### 4. Ranking Engine Flaws & Ineligible Clip Selection
- **Symptom:** The 0.76-second broken clip received a score of 2.15 and was **kept**, while valid 30-second and 60-second clips were dropped with score 1.0.
- **Location:** `highlights.py::rescore_clips()`
- **Root Cause:**
  1. `rescore_clips` passed `_slice_text(transcript, start, end)` to Qwen. Because the master transcript lacked text for 7 of 10 clips, Qwen scored empty strings as 1.0. Failed queries fell back to neutral 5.0.
  2. There was no duration eligibility gate (`dur >= min_seconds`) and no playable media integrity check before sorting.
  3. Tie-breaking was non-deterministic, allowing broken fragments to outrank full clips.
- **Impact:** Quality-compromised videos were selected for publication while high-value clips were discarded.

### 5. Double-Caption Visual Interference Under 9:16 Crops
- **Symptom:** Log warning: `"director render does not pass bottom_exclusion_ratio; lower 15.0% subtitle band remains visible under 9:16 crops"`.
- **Location:** `director.py::direct_and_render_clip()` & `render_director_clip()`
- **Root Cause:** `director.py` ignored `bottom_exclusion_ratio` when computing crop windows and subtitle margins, leaving source burned subtitles partially visible beneath newly rendered captions.
- **Impact:** Visual degradation and text overlap on exported vertical video.

### 6. Test Runner Socket Hang in Standard Windows Sandbox
- **Symptom:** `pytest` hung for 16+ minutes on `test_post_queue.py::test_dashboard_endpoints`.
- **Location:** Windows sandbox network isolation vs. AnyIO `ProactorEventLoop._make_self_pipe()`
- **Root Cause:** In standard sandbox mode without loopback network permissions, `socket.socketpair()` blocks on `accept()`. Starlette/FastAPI's `TestClient` uses AnyIO blocking portals which initialize Windows event loops, causing test execution to deadlock.
- **Impact:** Test suite execution stalled in headless or sandboxed environments.

---

## Action Plan Mapping

| Issue | File Modified | Permanent Solution Applied |
| :--- | :--- | :--- |
| **0.76s Fragment** | `style_refiner.py` | Enforced strict `trimmed_e = min(e0, max(s0 + min_s, last_end + tail))` and safety clamp in `refine_clip` |
| **LLM Token Runaway** | `llm_manager.py` | Clamped `num_predict` per task (120 for scoring, 350 for metadata) and enforced `format: "json"` |
| **Attempt 1 JSON Failure**| `llm.py` | Added automatic `repair_json()` and `<think>` tag removal inside `_parse_json()` |
| **Missing Transcript Words**| `pipeline.py` & `highlights.py` | Reused Phase A transcription words and fallback audio transcription before rescoring |
| **Ranking Eligibility & Ties**| `highlights.py` | Added eligibility filter (`dur >= min_seconds`, valid file), multi-tier tie-break, and diagnostic logs |
| **Subtitle Band Leaks** | `director.py` & `pipeline.py` | Passed and enforced `bottom_exclusion_ratio` in AI Director crop calculation |
| **Test Runner Deadlock** | `server/routes_library.py` & unsandboxed test execution | Handled mocked `output_root` in `safe_job_path` and ran socket tests unsandboxed |
