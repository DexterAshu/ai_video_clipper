# FRAMEZA — STAGE 8.3 PRODUCTION ACCEPTANCE ROOT CAUSE ANALYSIS (RCA)

**Project:** Frameza AI Video Clipper  
**Audit Stage:** Stage 8.3 Production Acceptance & Adversarial Audit  
**Target Hardware:** Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM  
**Environment:** Windows 11, Python 3.11.9, PyTorch CUDA 12.8, FFmpeg NVENC  
**Date:** November 2026  
**Status:** COMPLETE & VERIFIED

---

## 1. Executive Summary

Following the Stage 8.2 stability release, an independent adversarial investigation and code-path audit was conducted across the entire Frameza pipeline. While Stage 8.2 successfully stabilized rendering workers and resolved 10-way resource lockups, adversarial probing revealed five subtle yet critical latent defects that could lead to candidate starvation, silent LLM retries, numerical sorting crashes, and resolution drift:

1. **Highlight Candidate Starvation in Speech-Dense Videos (Audit A):** Event cluster fusion was gated strictly behind `mechanical or _is_sparse(transcript, duration, cfg)`. In speech-dense videos (>40 wpm), high-intensity viral events (e.g., laughter peaks, sudden energy spikes, visual reactions) occurring outside the shortlisted speech segments were completely starved from candidate selection.
2. **Missing Word Repair Gated Behind Captions (Audit A):** In `pipeline.py`, sequential missing word repair was gated by `and cfg.get("captions", {}).get("enabled", True)`. If a user disabled captions, missing transcript words were never recovered, depriving downstream AI metadata, virality, and rescoring of spoken text.
3. **Sorting Crash on Corrupted Score Signals (Audit B):** The deterministic tie-breaking key in `highlights.py::rescore_clips` directly called `float(virality["score"])` and `float(candidate_score)`. Non-numeric strings, `None`, or unhandled `NaN` from third-party or mock estimators triggered unhandled `ValueError` exceptions during sorting. Furthermore, `_get_clip_text` fell back to the string `"clip"` when transcript was empty, falsely masking missing transcripts as `transcript_status = "complete"`.
4. **Ollama Reasoning Runaway & Empty Response (Audit C):** Local reasoning models (`qwen3.5:4b`) consumed their entire token allocation inside internal reasoning tags (`<think>...</think>`), emitting an empty string in `response` with HTTP 200. Because `llm_manager.py` did not treat empty strings as failures, it caused repeated JSON decode retries instead of immediately failing over to the fast fallback model (`llama3.2:3b`).
5. **Resolution Drift in Dynamic AI Director (Audit F):** When configured for 720p output (`cfg["reframe"]["output"] = {"width": 720, "height": 1280}`), `director.direct_and_render_clip` defaulted to 1080x1920 because `ow` and `oh` were hardcoded to default 1080 and 1920 rather than inheriting from configuration.

All five root causes have been permanently resolved, validated with dedicated adversarial tests, and verified end-to-end.

---

## 2. Detailed Root Cause Investigations

### 2.1 Audit A: Candidate Starvation & Event Fusion in Dense Transcripts

- **Symptom:** In long podcasts (such as the 80-minute Ravi Gupta podcast), event clusters located outside the energy-shortlisted 23% span were never considered for candidate generation.
- **Affected File:** `highlights.py` (`select_highlights()`)
- **Technical Root Cause:**
  ```python
  if events:
      if mechanical or _is_sparse(transcript, duration, cfg):
          snapped += event_cluster_candidates(events, duration, cfg)
      snapped = fuse_event_scores(snapped, events, cfg)
  ```
  `_is_sparse` calculated words per minute across the whole video. In conversational podcasts, speech density frequently exceeds 120 wpm, making `_is_sparse` evaluate to `False`. As a result, `event_cluster_candidates` was completely skipped, dropping high-intensity laughter bursts (e.g., intensity 9.5/10) occurring in non-shortlisted segments.
- **Permanent Fix:**
  In `highlights.py`, strong event clusters with `intensity >= cfg["viral_v2"]["min_event_cluster_intensity"]` (default 6.0) are now extracted and included in `snapped` even when `_is_sparse` is `False`. The downstream `_dedupe()` function then objectively balances speech-based and event-based candidates according to score and temporal overlap.

### 2.2 Audit A: Captions Flag Gating Spoken Word Repair

- **Symptom:** Disabling captions resulted in clips having empty `words` arrays and ungrounded metadata/virality ratings.
- **Affected File:** `pipeline.py` (`_prepare_clip_ai_sequentially()`)
- **Technical Root Cause:**
  Line 949 checked `and cfg.get("captions", {}).get("enabled", True)` before triggering `repair_clip_transcription`. Word-timing repair is an essential transcription capability needed by AI metadata generators, niche classifiers, and virality scorers to understand what was said in the clip, independent of visual caption rendering.
- **Permanent Fix:**
  Removed the captions flag check. `repair_clip_transcription` now runs on CPU int8 whenever words are missing or coverage is under 40%, ensuring all clips have accurate spoken words regardless of caption rendering settings.

### 2.3 Audit B: Numerical Robustness & Tie-Breaking Sorting Defect

- **Symptom:** `ValueError: could not convert string to float: 'not-a-number'` during `rescore_clips()`.
- **Affected File:** `highlights.py` (`_safe_num()`, `_ranking_key()`, `_get_clip_text()`)
- **Technical Root Cause:**
  1. `_ranking_key` directly parsed `float((c.get("virality") or {}).get("score", 0.0))` without validation. Corrupted or mock inputs containing string placeholders caused a fatal crash.
  2. `_safe_num(float("inf"))` returned `default` (5.0) instead of clamping to `max_v` (10.0).
  3. `_get_clip_text` had a final fallback `return str(meta.get("description") or clip.get("hook") or "clip").strip()`. The literal `"clip"` prevented empty clips from being flagged as `transcript_status = "missing"`.
- **Permanent Fix:**
  1. Wrapped all sort key components in `_safe_num()`, safely bounding virality, candidate scores, weighted scores, and duration.
  2. Updated `_safe_num` to return `max_v` for `+inf` and `min_v` for `-inf`.
  3. Updated `_get_clip_text` to return `""` when neither transcript, words, SRT, description, nor hook are present.

### 2.4 Audit C: Ollama Reasoning Runaway & Silent Empty 200 Responses

- **Symptom:** Ollama `qwen3.5:4b` returned HTTP 200 with `response: ""` after exhausting token budget inside reasoning stream, leading to repeated JSONDecodeErrors.
- **Affected File:** `llm_manager.py` (`query_ollama()`)
- **Technical Root Cause:**
  When `qwen3.5:4b` runs with thinking enabled, it puts reasoning text into internal channels. If `num_predict` is hit during thinking, `response` is empty string. `llm_manager.py` did not validate `if not cleaned.strip(): raise LLMError(...)`, so it returned `""` to `llm.py`, causing `json.loads("")` to fail and consume retry attempts on the broken model rather than switching to `llama3.2:3b`.
- **Permanent Fix:**
  Added explicit empty-response validation in `llm_manager.py`. If cleaned response is empty, an `LLMError` is raised immediately, triggering seamless fallback to `llama3.2:3b` which delivers valid JSON in 0.4s. Furthermore, `fallback_payload` is cloned (`dict(payload)`) to prevent dictionary mutation.

### 2.5 Audit F: Resolution Drift in Dynamic AI Director

- **Symptom:** `test_720p_resolution` failed with `Expected 720x1280, got 1080x1920`.
- **Affected File:** `director.py` (`direct_and_render_clip()`)
- **Technical Root Cause:**
  `direct_and_render_clip` hardcoded parameter defaults `ow: int = 1080, oh: int = 1920`. When `pipeline.py` or test scripts passed a custom resolution config, the default values took precedence unless explicitly passed by the caller.
- **Permanent Fix:**
  Updated `direct_and_render_clip` signature to `ow: int | None = None, oh: int | None = None`. If `None`, it dynamically reads `width` and `height` from `cfg.get("reframe", {}).get("output", {})`, defaulting to 1080 and 1920 only if unconfigured.

---

## 3. Resolution Summary Table

| Issue Area | Module / Function | Defect Class | Resolution Status | Verified By |
| :--- | :--- | :--- | :--- | :--- |
| **Audit A** | `highlights.py::select_highlights` | Candidate Starvation | Strong event clusters included regardless of speech density | `test_dense_speech_with_high_intensity_event_outside_shortlist` |
| **Audit A** | `pipeline.py::_prepare_clip_ai_sequentially` | Gated Spoken Word Repair | Decoupled word repair from captions flag | `test_pipeline_ai_prep_word_repair_runs_when_captions_disabled` |
| **Audit B** | `highlights.py::_safe_num` | Numerical Invalidation | Clamps `+inf` to `max_v`, `-inf` to `min_v`, `nan` to default | `test_safe_num_handles_nan_inf_none_strings` |
| **Audit B** | `highlights.py::_ranking_key` | Unhandled Sorting Crash | Protected multi-tier keys via `_safe_num` | `test_ranking_engine_survives_nan_and_inf_scores` |
| **Audit B** | `highlights.py::_get_clip_text` | Fallback Masking | Returns empty string on missing text to correctly flag `missing` status | `test_ranking_engine_transcript_status_classification` |
| **Audit C** | `llm_manager.py::query_ollama` | Silent Empty Response | Raises `LLMError` on empty response and triggers fallback | `test_llm_manager_empty_response_triggers_fallback` |
| **Audit F** | `director.py::direct_and_render_clip` | Hardcoded Resolution | Inherits `ow`/`oh` from `cfg["reframe"]["output"]` | `scripts/test_pipeline_e2e.py::test_720p_resolution` |
