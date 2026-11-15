# Frameza Pipeline Stability & Ranking Architecture Implementation Report

**Date:** 2026-11-15  
**Version:** Stage 8.2 Production Stability Release  
**Modules Modified:** `llm.py`, `llm_manager.py`, `style_refiner.py`, `director.py`, `pipeline.py`, `highlights.py`, `progress.py`, `server/copy.py`, `server/routes_library.py`, `web/js/app.js`

---

## 1. Architectural Fixes Implemented

### 1.1 Strict Duration Protection in `style_refiner.py`
To eliminate the collapse bug where candidate clips were truncated down to fractions of a second:
- In the ending optimizer:
  ```python
  cand_dur = e0 - s0
  if cand_dur >= min_s:
      trimmed_e = min(e0, max(s0 + min_s, last_end + tail))
  else:
      trimmed_e = e0
  ```
- Added a final duration guard before emitting the Edit Plan:
  ```python
  if output_duration < min_s:
      log.warning("refine_clip output_duration %.2fs < min_s %.2fs — restoring original bounds",
                  output_duration, min_s)
      s0, e0 = float(candidate["start"]), float(candidate["end"])
  ```

### 1.2 LLM Optimization & JSON Resilience in `llm.py` and `llm_manager.py`
To eliminate 115-second query timeouts and 28-minute rescore delays:
- Implemented `TASK_NUM_PREDICT` mapping in `llm_manager.py`:
  - `clip_score`: 120 tokens
  - `clip_metadata`: 350 tokens
  - `hook_classify`: 80 tokens
  - `virality`: 350 tokens
  - `niche`: 60 tokens
  - Default: 450 tokens
- Enforced native Ollama structured output via `payload["format"] = "json"`.
- Overhauled `_parse_json()` in `llm.py`:
  - Automatically strips reasoning tags (`<think>...</think>`).
  - Calls `repair_json(text)` before raising JSONDecodeError, stripping preamble, markdown fences, single quotes, and trailing commas on Attempt 1.
  - Added media compatibility guard in `_dispatch` to enforce standard schema contracts.

### 1.3 High-Integrity Ranking & Diagnostic Logging in `highlights.py`
Overhauled `rescore_clips()` into a 3-phase deterministic ranking pipeline:
1. **Candidate Eligibility Gate:**
   - Validates clip duration $\ge min\_seconds$.
   - Validates rendered media existence and size $> 1,000$ bytes on disk.
   - Marks ineligible clips clearly with structured `rejection_reasons` (`"duration_below_minimum"`, `"rendered_output_file_missing"`).
2. **Transcript Recovery & Normalized AI Scoring:**
   - Resolves spoken text from clip-specific words or SRT generated during Phase A when the master transcript is incomplete.
   - Computes normalized composite scores:
     $$\text{Score} = 0.60 \times \text{LLM} + 0.25 \times \text{Virality} + 0.15 \times \text{Candidate}$$
3. **Deterministic Multi-Tier Tie-Breaking:**
   - Sort key: `(composite_score, virality_score, candidate_score, -abs(dur - 35.0), -index)`
   - Structured diagnostic output saved to each clip's `ranking` payload.

### 1.4 Subtitle Band Masking in `director.py`
- Updated `render_director_clip()` and `direct_and_render_clip()` to accept `bottom_exclusion_ratio: float = 0.0`.
- Clamped usable framing height when `existing_subs="replace"` to prevent source subtitles from leaking beneath 9:16 portrait video crops.

### 1.5 Pipeline Progress Observability in `pipeline.py` & UI
- Registered Phase A in `progress.py` (`"ai_prep"` with 4% progress weight, adjusted render weight to 35%).
- Added `"ai_prep": "Preparing clip metadata"` in `server/copy.py` `STAGE_LABELS` and updated `web/js/app.js` progress bar.
- Wrapped Phase A in `tracker.start("ai_prep")` and `stage_timer()`.

### 1.6 Path Traversal & Scoped Workspaces in `server/routes_library.py`
- Updated `safe_job_path` to dynamically calculate `effective_base = curr if not curr.is_relative_to(base) else base`.
- Ensures unit tests mocking `output_root` with temporary paths remain isolated without throwing false 404 traversal errors.

---

## 2. Code Modifications Summary Table

| Module | Functions Affected | Lines Changed |
| :--- | :--- | :--- |
| `style_refiner.py` | `refine_clip`, `compress_pauses` | +14 / -4 |
| `llm.py` | `complete_json`, `_dispatch`, `_parse_json` | +28 / -9 |
| `llm_manager.py` | `TASK_NUM_PREDICT`, `query_ollama` | +22 / -4 |
| `highlights.py` | `rescore_clips`, `_get_clip_text` | +92 / -31 |
| `director.py` | `direct_and_render_clip`, `render_director_clip` | +18 / -5 |
| `pipeline.py` | `_prepare_clip_ai_sequentially`, `_render_one` | +32 / -11 |
| `progress.py` | `STAGES` | +3 / -2 |
| `server/copy.py` | `STAGE_LABELS` | +1 / -0 |
| `server/routes_library.py` | `safe_job_path` | +6 / -4 |
