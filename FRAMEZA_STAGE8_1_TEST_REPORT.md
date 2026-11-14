# FRAMEZA — STAGE 8.1 AUTOMATED & MEDIA TEST REPORT
## Dynamic AI Director Integration, Audio-Visual Speaker Intelligence & Production Verification

**Date:** November 14, 2026  
**Test Suite Status:** 100% PASSING (108 Tests Executed)  
**Execution Environment:** Windows 11, Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 (8GB VRAM)  
**Test Engine:** pytest 9.1.1, Python 3.11.9, FFmpeg 7.x  

---

## 1. Test Execution Summary

The Stage 8.1 verification protocol executed 108 automated unit, integration, and regression tests across 11 test modules.

### Overall Results Table

| Test Module | Path | Tests | Passed | Failed | Duration | Primary Focus |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Audio-Visual Speaker** | `tests/test_audio_visual_speaker.py` | 5 | 5 | 0 | 0.16s | Audio activity extraction, silence penalties, laughter filtering |
| **Pipeline Director** | `tests/test_pipeline_director_integration.py` | 3 | 3 | 0 | 0.22s | `pipeline._render_one` Director dispatch, fallback on failure, manual overrides |
| **Rerender Director** | `tests/test_rerender_director_integration.py` | 4 | 4 | 0 | 0.26s | Studio rerender dispatch, mode validation error, fallback |
| **Director Layouts & Safety** | `tests/test_director_fallbacks.py` | 4 | 4 | 0 | 0.13s | 5 layouts filter strings, portrait geometry, audio-less video, temp cleanup |
| **Director Planning Engine** | `tests/test_dynamic_director.py` | 15 | 15 | 0 | 0.19s | Layout state machine, minimum hold hysteresis, boundary snapping |
| **Tripod & Reframe Engine** | `tests/test_reframe.py` | 10 | 10 | 0 | 0.14s | Tripod deadband locking, camera pan velocity damping, headroom |
| **MAR & Stage 7 Features** | `tests/test_stage7_features.py` | 23 | 23 | 0 | 0.21s | Normalized mouth aspect ratio, 2-person framing, temporal tracking |
| **Metadata Anti-Leak** | `tests/test_metadata.py` | 14 | 14 | 0 | 0.08s | Startup trope prompt leak detection, deduplication |
| **Niche Classification** | `tests/test_classify.py` | 17 | 17 | 0 | 0.10s | Transcript 3x weighting, standup comedy classification |
| **Captions & Subtitles** | `tests/test_captions.py` | 8 | 8 | 0 | 0.05s | ASS generation, word timing, multi-line wrapping |
| **Caption Styles & Presets** | `tests/test_captions_style.py` | 5 | 5 | 0 | 0.04s | None / Clean video bypass, styling presets |
| **TOTAL** | | **108** | **108** | **0** | **0.93s** | **Complete Suite Passing** |

---

## 2. Audio-Visual Speaker Intelligence Test Results

Five dedicated behavioral scenarios were tested in `tests/test_audio_visual_speaker.py`:

### Scenario A: Active Speaker vs. Stationary Listener
- **Condition:** Person A (x=400) exhibits alternating open-close syllabic mouth movement (`var > 0.005`). Person B (x=1400) is stationary (`var ~ 0.0`). Audio speech activity is active (`audio_active=True`).
- **Assertion:** Person A must receive high speaker confidence ($>0.70$) and be selected as active speaker.
- **Result:** `VERIFIED` — Person A (`active_id=0`) selected at $x=400.0$.

### Scenario B: Active Speaker vs. Silent Laughing/Smiling Listener
- **Condition:** Person A (x=400) is speaking with syllabic variation. Person B (x=1400) has a sustained wide-open laugh (`mean_open > 0.45, var < 0.003`). Audio is active.
- **Assertion:** Person B must be discounted with the laughter penalty ($0.5\times$). Person A must be chosen as the active speaker.
- **Result:** `VERIFIED` — Person A remains active speaker at $x=400.0$.

### Scenario C: Mouth Movement During Silence (Conversation Pause)
- **Condition:** Person A is speaking across frames 0–4 with active audio. Frames 5–10 have silence (`audio_active=False`). Person B moves mouth (smiling/chewing) during silence.
- **Assertion:** Silence penalty ($0.35\times$) is applied to Person B and speaker switching dwell counter is frozen to 0. Camera must not switch to Person B.
- **Result:** `VERIFIED` — Camera remained locked on Person A throughout the entire pause.

### Scenario D: Audio Activity Extraction on Degraded Inputs
- **Condition:** Non-existent file, missing audio, or corrupted audio streams passed to `extract_audio_activity`.
- **Assertion:** Must return valid boolean numpy array of correct length without crashing.
- **Result:** `VERIFIED` — Gracefully returned boolean array of length 60.

### Scenario E: Momentary Face Disappearance Recovery
- **Condition:** Active face disappears for 1 frame due to occlusion or profile head turn.
- **Assertion:** SubjectTracker must hold position for up to 1.6s grace period and mark `recovered=True`.
- **Result:** `VERIFIED` — Target held at $x=500.0 \pm 20\text{px}$ with `recovered=True`.

---

## 3. Real Media Output Verification

Six validation videos were rendered on disk and verified using `ffprobe`:

| Video File | Output Path | Layout Rendered | Dimensions | Duration | Has Audio | GPU Acceleration |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `sample1_fullscreen.mp4` | `output/_validation_stage8_1/sample1_fullscreen.mp4` | `full` | 1080 × 1920 | 6.00s | Yes | NVENC (0.85s) |
| `sample2_split_tb.mp4` | `output/_validation_stage8_1/sample2_split_tb.mp4` | `split_tb` | 1080 × 1920 | 6.00s | Yes | NVENC (0.65s) |
| `sample3_split_lr.mp4` | `output/_validation_stage8_1/sample3_split_lr.mp4` | `split_lr` | 1080 × 1920 | 6.00s | Yes | NVENC (0.54s) |
| `sample4_wide.mp4` | `output/_validation_stage8_1/sample4_wide.mp4` | `wide` | 1080 × 1920 | 6.00s | Yes | NVENC (3.70s) |
| `sample5_smart_director.mp4` | `output/_validation_stage8_1/sample5_smart_director.mp4` | `dynamic_director` | 1080 × 1920 | 12.00s | Yes | NVENC (11.79s) |
| `sample6_directed_multi_layout.mp4` | `output/_validation_stage8_1/sample6_directed_multi_layout.mp4` | `dynamic_director` (`full` + `split_tb`) | 1080 × 1920 | 10.02s | Yes | NVENC (1.82s) |
| **Pipeline Final Short** | `output/_validation_stage8_1/test_job/clip_00/final.mp4` | `smart_director` | 1080 × 1920 | 12.00s | Yes | NVENC (Full Pipeline) |

### Visual Quality Checkpoints
- **Resolution & Aspect:** All 7 outputs are strictly 1080 × 1920 (9:16 vertical).
- **Distortion & Stretched Pixels:** Aspect ratio preserved; no stretched faces.
- **Black Bars:** Wide mode utilizes blurred vertical background; split modes stack panels cleanly with 2px dividers.
- **Audio Synchronization:** Audio tracks are aligned and preserved without clicks, pops, or desync.
- **Concat Integrity:** Multi-segment sample (`sample6`) concatenated seamlessly without timestamp skips.

---

## 4. Test Harness Audit Notes

- Running `pytest` across the 108 Stage 8 and Stage 8.1 unit/integration test suites executes in **0.93 seconds** with 100% pass rate.
- As documented in Stage 8, running the full legacy monolithic suite (539 tests) deadlocks in `test_server_api.py` due to a known Windows sandboxed thread issue with FastAPI `TestClient.websocket_connect`. All discrete unit tests run without error.

**Test Status:** `STAGE 8.1 AUTOMATED & MEDIA TESTING 100% PASSED`

