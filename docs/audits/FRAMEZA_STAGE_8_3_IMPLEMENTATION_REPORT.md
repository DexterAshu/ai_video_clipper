# FRAMEZA — STAGE 8.3 IMPLEMENTATION REPORT

**Project:** Frameza AI Video Clipper  
**Stage:** Stage 8.3 Production Hardening & Architectural Implementation  
**Target Hardware:** Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM  
**Environment:** Windows 11, Python 3.11.9, FFmpeg NVENC  
**Date:** November 2026  
**Status:** IMPLEMENTED, TESTED & PRODUCTION READY

---

## 1. Architectural Scope & Changes

Stage 8.3 focused on hardening the pipeline against edge cases discovered during adversarial audits, closing candidate starvation gaps, decoupling transcription repair, securing ranking calculations, guaranteeing LLM fallback routing, and ensuring full resolution configurability.

---

## 2. Detailed File Modifications

### 2.1 `highlights.py`

#### A. Dense-Speech Event Discovery (Lines 299–315)
- **Problem:** Speech-dense videos omitted high-intensity viral events if `_is_sparse` returned `False`.
- **Implementation:**
  ```python
  if events:
      # Fusion runs AFTER sentence snapping and before dedupe.
      cluster_cands = event_cluster_candidates(events, duration, cfg)
      if mechanical or _is_sparse(transcript, duration, cfg):
          snapped += cluster_cands
      else:
          # Even in speech-dense transcripts, include high-intensity event clusters (>= 6.0)
          # so viral events (laughter bursts, energy spikes) outside speech shortlists are never starved.
          min_cluster_intensity = float(cfg.get("viral_v2", {}).get("min_event_cluster_intensity", 6.0))
          strong_clusters = [
              c for c in cluster_cands
              if any(
                  e.get("intensity_1_10", 0) >= min_cluster_intensity
                  for e in _events_in_span(events, c["start"], c["end"])
              )
          ]
          snapped += strong_clusters
      snapped = fuse_event_scores(snapped, events, cfg)
      snapped = apply_reaction_boundaries(snapped, events, cfg, duration)
  ```

#### B. Text Resolution & Numerical Protection (Lines 406–420)
- **Implementation:**
  ```python
  # 6. Final fallback: hook or metadata title/description
  meta = clip.get("metadata") or {}
  return str(meta.get("description") or clip.get("hook") or "").strip()

  def _safe_num(val: Any, default: float = 5.0, min_v: float = 0.0, max_v: float = 10.0) -> float:
      try:
          if val is None:
              return default
          v = float(val)
          if math.isnan(v):
              return default
          if math.isinf(v):
              return max_v if v > 0 else min_v
          return min(max_v, max(min_v, v))
      except (TypeError, ValueError):
          return default
  ```

#### C. Multi-Tier Deterministic Ranking Key (Lines 534–545)
- **Implementation:**
  ```python
  def _ranking_key(c: dict):
      vir_s = _safe_num((c.get("virality") or {}).get("score"), default=0.0, min_v=0.0, max_v=100.0)
      cand_s = _safe_num(c.get("candidate_score"), default=0.0, min_v=0.0, max_v=100.0)
      return (
          _safe_num(c.get("weighted_score"), default=0.0, min_v=0.0, max_v=10.0),
          vir_s,
          cand_s,
          -abs(float(c.get("duration", 30.0)) - 35.0),  # Sweet-spot proximity
          -int(c.get("index", 0))                      # Stable tie-break
      )
  ```

---

### 2.2 `pipeline.py`

#### A. Decoupled Spoken Word Repair (Line 945)
- **Change:** Removed `and cfg.get("captions", {}).get("enabled", True)` from the trigger condition in `_prepare_clip_ai_sequentially`. Word repair runs whenever `needs_repair` is True, ensuring audio spoken text is always available for downstream metadata, virality, and rescoring regardless of caption display flags.

---

### 2.3 `llm_manager.py`

#### A. Empty String Guard & Fallback Routing (Lines 419–465)
- **Implementation:**
  ```python
  if not cleaned.strip():
      done_r = res_data.get("done_reason", "unknown")
      raise LLMError(
          f"ollama model '{chosen_model}' returned empty response (done_reason={done_r}, eval_count={eval_cnt})"
      )
  ```
- **Payload Non-Mutation:**
  ```python
  # Fallback to designated fallback model (llama3.2:3b)
  fallback_payload = dict(payload)
  fallback_payload["model"] = fallback_model
  if images_b64:
      fallback_payload.pop("images", None)
  ```

---

### 2.4 `director.py`

#### A. Dynamic Resolution Inheritance (Lines 338–355)
- **Implementation:**
  ```python
  def direct_and_render_clip(source: Path | str,
                             clip_start: float,
                             clip_end: float,
                             out_path: Path | str,
                             cfg: dict | None = None,
                             transcript: dict | None = None,
                             framing_mode: str = "smart_director",
                             cached_plan_path: Path | None = None,
                             manual_zoom: float = 1.0,
                             crop_x_offset: float = 0.0,
                             crop_y_offset: float = 0.0,
                             ow: int | None = None,
                             oh: int | None = None,
                             bottom_exclusion_ratio: float = 0.0) -> dict:
      ...
      cfg = cfg or load_config()
      rcfg_out = cfg.get("reframe", {}).get("output", {})
      if ow is None:
          ow = int(rcfg_out.get("width", 1080))
      if oh is None:
          oh = int(rcfg_out.get("height", 1920))
  ```

---

### 2.5 `server/copy.py` & `server/routes_library.py`

#### A. Stage Label Definition
- Added `"ai_prep": "Preparing clip metadata"` to `STAGE_LABELS` in `server/copy.py`.

#### B. Path Traversal Protection
- In `server/routes_library.py::safe_job_path`, bound `effective_base = curr if not curr.is_relative_to(base) else base` ensuring cross-drive and traversal boundaries return 404 rather than 500.

---

## 3. Automated Test Deliverables

- **`tests/test_stage_8_3_adversarial.py`:** Added 7 dedicated adversarial tests testing:
  1. Dense-speech event cluster preservation.
  2. Numerical safety (`_safe_num` with NaN, Inf, None, strings).
  3. Ranking engine survival under corrupted inputs.
  4. Explicit transcript status classification (`missing`, `recovered`, `complete`).
  5. Deterministic tie-breaking order.
  6. Ollama empty-response fallback routing.
  7. Spoken word repair execution when captions are disabled.
- **`scripts/verify_stage_8_3_real_quality.py`:** Real multi-clip validation runner capturing hardware telemetry, NVENC encoding, and full FFmpeg container audits.
