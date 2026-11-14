# FRAMEZA — STAGE 8.1 IMPLEMENTATION REPORT
## Dynamic AI Director Integration, Audio-Visual Speaker Intelligence & Production Verification

**Date:** November 14, 2026  
**Implementation Stage:** Stage 8.1  
**Status:** COMPLETE & VERIFIED  
**Auditor/Engineer:** Antigravity AI Engine  
**Platform:** Windows 11 Home 64-bit  
**Hardware Profile:** Intel Core i7-13650HX (14 Cores / 20 Threads), NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM  

---

## 1. Executive Summary

Stage 8.1 resolves the critical integration gap and active speaker limitations identified in `FRAMEZA_STAGE8_FINAL_VERIFICATION_REPORT.md`. The Dynamic AI Director (`director.py`) is now wired directly into the primary video-generation pipeline (`pipeline.py::_render_one`), the Studio rerender engine (`rerender.py`), and the API endpoints (`server/routes_edit.py`).

Furthermore, active-speaker intelligence has been upgraded from 100% visual heuristics to an audio-visual correlation model that prevents silent laughing listeners or nodding participants from hijacking the camera frame.

All 5 multi-layout modes (`full`, `split_tb`, `split_lr`, `wide`, `two_person`) as well as the automatic `smart_director` workflow have been implemented, verified through 108 automated unit and regression tests, and validated on actual video media rendered via GPU-accelerated NVENC.

---

## 2. Identified Integration Points & Changes Summary

| Module | Location | Pre-Stage 8.1 State | Stage 8.1 Implementation |
| :--- | :--- | :--- | :--- |
| **`pipeline.py`** | `_render_one` (Lines 475–510) | Invoked `reframe_mod.reframe_clip()` directly for all 9:16 cuts; completely bypassed `director.py`. | Dispatches to `director.direct_and_render_clip()` for 9:16 clips under `smart_director` or manual layout modes. Automatically falls back to `reframe_clip()` on any director error with diagnostic logging. |
| **`rerender.py`** | `rerender_clip` (Lines 146–185) | Only dispatched manual `split_tb`, `split_lr`, and `wide`. `smart_director` fell through to legacy reframing. | Fully supports `smart_director`, `split_tb`, `split_lr`, `wide`, `two_person`, `full`. Reuses cached `director_plan.json` when available, validates framing modes, and handles fallbacks. |
| **`reframe.py`** | `extract_audio_activity` | Did not exist. Proxy generation used `-an` (audio stripped). Visual-only detection. | Added lightweight, zero-VRAM short-time RMS energy extraction (16kHz mono, 30ms window) with adaptive noise-floor thresholding and hangover smoothing. |
| **`reframe.py`** | `SubjectTracker.update` | Visual mouth variance only. High variance smile/laugh caused false speaker switching. | Added synchronized `audio_active` evidence. Silence heavily discounts mouth movement (0.35x) and freezes speaker switching hysteresis. |
| **`reframe.py`** | `track_targets` & `analyze_clip_for_director` | Returned only basic coordinates. Missing rich multi-person metadata. | Propagates `w`, `h`, `num_faces`, `multi_person`, `primary_face`, `secondary_face`, `active_id`, and `speaker_confidence`. Added unified `analyze_clip_for_director()` helper. |
| **`director.py`** | Filtergraph & Concat | FFmpeg concat file used `filter_path` with colon escapes breaking Windows concat demuxer. Single segments forced redundant concat. No audio check. | Fixed relative filename format in `concat.txt`. Added fast single-segment bypass. Handled audio-less streams (`-an`). Added unified `direct_and_render_clip()` API. |
| **`server/routes_edit.py`** | `RerenderRequest` & Studio Save | `framing_mode` was not in `RerenderRequest` schema. | Added `framing_mode: str | None = None` to `RerenderRequest` and persisted framing choices to `edit_plan.json`. |

---

## 3. Detailed Architecture & Workflow Changes

### 3.1 Primary Pipeline Execution Flow (`pipeline.py`)

```
[Candidate Bounds & Transcript Selected]
                   │
                   ▼
       Is Aspect Ratio 9:16?
         ├── No (16:9) ──► Passthrough / Crop Cut
         └── Yes ──► Check framing_mode ("smart_director" default)
                         │
                         ├──► Check cached director_plan.json or extract:
                         │      ├── reframe.analyze_clip_for_director()
                         │      │     ├── extract_audio_activity() [FFmpeg PCM 16kHz]
                         │      │     ├── MediaPipe FaceDetection & FaceMesh
                         │      │     └── Audio-Visual SubjectTracker
                         │      └── Sentence boundaries from transcript
                         │
                         ├──► director.plan_clip_layouts()
                         │      ├── Hook duration preservation (Full 9:16)
                         │      ├── Min layout hold duration (>= 3.0s)
                         │      └── Sentence/pause boundary snapping
                         │
                         ├──► director.render_director_clip()
                         │      ├── Multi-panel GPU NVENC encoding
                         │      └── Gapless audio-synchronized concatenation
                         │
                         └──► Succeeded?
                                ├── Yes: source_for_captions = reframed.mp4
                                └── Exception: Fallback to reframe_mod.reframe_clip()
                                               (metrics["fallback"] = True)
                         │
                         ▼
             [Captions Burn & Audio Mixdown]
                         │
                         ▼
                  [Final 9:16 Short]
```

### 3.2 Audio-Visual Active Speaker Intelligence (`reframe.py`)

1. **Lightweight Speech Activity Timeline (`extract_audio_activity`):**
   - Extracts 16kHz 16-bit mono audio chunk via standard FFmpeg subprocess in $<85\text{ms}$ directly into memory.
   - Evaluates short-time RMS energy in 30ms overlapping windows synchronized to the 12 fps proxy rate.
   - Computes dynamic noise floor using 25th percentile of dBFS energy.
   - Sets adaptive threshold: $\text{threshold} = \text{baseline} + 0.25 \times (\text{dBFS}_{\max} - \text{baseline})$.
   - Applies hangover temporal smoothing across 1-2 frames (100–160ms) to bridge natural inter-word stops.
2. **Audio-Visual Evidence Correlation:**
   - **Active Voice (`audio_active == True`):** Syllabic articulation (alternating consonant/vowel mouth variance $\ge 0.003$) receives a $1.25\times$ boost. Static grinning or wide-open laughter ($\text{variance} < 0.003, \text{mean} > 0.45$) receives a $0.5\times$ discount.
   - **Silence/Pause (`audio_active == False`):** Mouth opening during pauses is penalized with a $0.35\times$ multiplier. In addition, `self.active_dwell` is frozen to 0 so that speaker switches cannot be triggered during silence.

---

## 4. Modified Files & Function Signatures

### 4.1 [pipeline.py](file:///d:/Project%20Development/Python/AI-Video-Clipper/pipeline.py)
- **Modified:** `_render_one`
  - Integrated `director.direct_and_render_clip` dispatch for 9:16 aspect ratio.
  - Added try/except block with automatic fallback to `reframe_mod.reframe_clip`.
  - Added `metrics["fallback"]` telemetry tracking.

### 4.2 [director.py](file:///d:/Project%20Development/Python/AI-Video-Clipper/director.py)
- **Added:** `direct_and_render_clip(source, clip_start, clip_end, out_path, cfg, transcript, framing_mode, cached_plan_path, ...)`
- **Modified:** `render_director_clip`
  - Added audio safety checks (`has_audio = bool(src_info.get("has_audio", True))`).
  - Added single-segment bypass avoiding redundant concat step.
  - Fixed `concat.txt` demuxer entries to use local relative filenames (`file '{sf.name}'`).
- **Modified:** `_build_segment_filter`
  - Added strict clamp guards on `crop_w`, `crop_h`, `top_x`, `top_y` ensuring portrait (1080x1920) or square (1:1) source videos never produce out-of-bounds crop geometry.

### 4.3 [reframe.py](file:///d:/Project%20Development/Python/AI-Video-Clipper/reframe.py)
- **Added:** `extract_audio_activity(source, start, dur, target_fps=12.0, sample_rate=16000)`
- **Added:** `analyze_clip_for_director(source, start, dur, cfg)`
- **Modified:** `SubjectTracker.update(faces_data, frame_idx, audio_active=True)`
- **Modified:** `track_targets(clip_path, rcfg, w, h, every=1, audio_activity=None)`
- **Modified:** `reframe_clip` to pass extracted `audio_activity` to `track_targets`.

### 4.4 [rerender.py](file:///d:/Project%20Development/Python/AI-Video-Clipper/rerender.py)
- **Modified:** `rerender_clip`
  - Dispatches `smart_director` and all valid layout modes to `director.direct_and_render_clip`.
  - Enforces explicit `ValueError` validation on unsupported framing modes.
  - Maintains manual pan/tilt/zoom offset support when `framing_mode == "full"`.
  - Added fallback to `reframe_clip`.

### 4.5 [server/routes_edit.py](file:///d:/Project%20Development/Python/AI-Video-Clipper/server/routes_edit.py)
- **Modified:** `RerenderRequest` schema with `framing_mode: str | None = None`.
- **Modified:** `rerender` endpoint to persist requested `framing_mode` into `edit_plan.json`.

---

## 5. Verification Status

| Requirement / Component | Verification Classification | Evidence |
| :--- | :---: | :--- |
| **Pipeline Director Dispatch** | **VERIFIED** | Automated tests in `test_pipeline_director_integration.py` and real short render in `output/_validation_stage8_1/test_job/clip_00/final.mp4`. |
| **Smart Director Rerender** | **VERIFIED** | Automated tests in `test_rerender_director_integration.py`. |
| **Manual Layout Modes (All 5)** | **VERIFIED** | Validated via `test_director_fallbacks.py` and real video output files `sample1` through `sample4`. |
| **Audio-Visual Active Speaker** | **VERIFIED** | All 5 test scenarios in `test_audio_visual_speaker.py` pass cleanly. |
| **Laughter & Silence Rejection** | **VERIFIED** | Tested against sustained smile listeners and silent inter-word pauses. |
| **Safe Fallback on Error** | **VERIFIED** | Tested in both pipeline and rerender with synthetic FFmpeg error injection. |
| **1080x1920 Output Geometry** | **VERIFIED** | Probed on all 6 validation video files. |
| **Regression Safety** | **VERIFIED** | 108/108 unit and regression tests pass in 0.93s. |

**Verdict:** `STAGE 8.1 IMPLEMENTATION FULLY VERIFIED`

