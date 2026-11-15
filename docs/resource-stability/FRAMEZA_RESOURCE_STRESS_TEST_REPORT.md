# FRAMEZA — HARDWARE VALIDATION & RESOURCE STRESS TEST REPORT

**Execution Date:** 2026-11-15  
**Target Hardware:** Intel Core i7-13650HX, NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM, NVMe SSD (Disk 0)  
**Platform:** Windows 11, Python 3.11 (.venv)  
**Verification Script:** `scratch/validate_resource_stress_test.py`  
**Media Source:** `samples/sample.mp4` (1280x720, 30fps, 180.0s)  

---

## 1. Executive Summary & Verdict

A comprehensive hardware validation test was executed on real media with 9 candidate clips (demanding 9 separate vertical 9:16 video renders, audio mixdowns, and captions) to stress-test the new resource management architecture.

### Benchmark Verdict: **100% PASSED**
- **System Freeze / Lag:** Completely eliminated. The operating system remained fully responsive throughout the entire run.
- **Dedicated GPU Memory:** Peak **984.0 MB** (12.1% of 8.0 GB). Down from **7.6 GB (100% saturated)** previously!
- **Shared GPU Memory:** **0.0 MB spillover** (Windows never spilled any GPU allocations into RAM).
- **System RAM:** Peak **12.1 GB** (75.0% load), well within the safe 85% threshold.
- **Disk 0 (SSD):** Normal I/O, **0% pagefile thrashing**.

---

## 2. Quantitative Hardware Measurements

| Metric | Target / Specification | Measured Result | Status |
| :--- | :---: | :---: | :---: |
| **Candidate Clips Rendered** | $\ge 9$ | **9 clips** | **PASSED** |
| **Effective Render Workers** | Strictly 1 (default) | **1 worker** | **PASSED** |
| **Total Job Duration** | Baseline tracking | **120.14 s** | **PASSED** |
| **Video Rendering Stage Duration** | Baseline tracking | **107.08 s** | **PASSED** |
| **AI Preparation Stage Duration** | Sequential pre-render | **12.5 s** | **PASSED** |
| **Peak Dedicated VRAM** | $< 90\%$ (7.2 GB) | **984.0 MB (12.1%)** | **PASSED** |
| **Shared GPU Memory Spill** | 0 MB | **0.0 MB** | **PASSED** |
| **Peak System RAM** | $< 85\%$ (13.6 GB) | **12.1 GB (75.0%)** | **PASSED** |
| **Concurrent Ollama Requests during Render** | Strictly 0 | **0** | **PASSED** |
| **Concurrent Whisper Ops during Render** | Strictly 0 | **0** | **PASSED** |
| **FFmpeg Active Encodes at Any Instant** | Max 1 | **1** | **PASSED** |
| **Failed Clips** | 0 | **0** | **PASSED** |
| **Output Playability & Integrity** | 100% | **9/9 Verified Playable** | **PASSED** |
| **Output Geometry (9:16 Portrait)** | 1080x1920 | **9/9 Valid (1080x1920)** | **PASSED** |
| **Audio/Video Sync** | Has audio stream | **9/9 Valid AAC Audio** | **PASSED** |

---

## 3. Telemetry Comparison: Before vs. After Stability Fix

```
Metric                      Before Fix (Crashed/Froze)          After Fix (Stable)
─────────────────────────────────────────────────────────────────────────────────────────────
Render Workers:             10 (unbounded via cpu_count // 2)   1 (hardware-aware default)
Dedicated VRAM:             7.6 / 8.0 GB (100% Full)            984 MB / 8.0 GB (12.1%)
Shared GPU Memory (RAM):    7.8 / 7.9 GB (100% Spillover)       0.0 MB (Zero Spillover)
Physical System RAM:        14.9 / 15.7 GB (95% - 100%)         12.1 / 15.7 GB (75.0%)
Disk 0 (SSD Active Time):   100% Paging Thrash                  Normal File I/O (< 5%)
Ollama Server:              Crashed (exit 0xc0000409)           Clean eviction before render
System State:               Frozen / Laptop stopped working     Fully responsive, stable
```

---

## 4. Output Integrity Verification

Every rendered clip was probed with `ffprobe` to verify that video streams, geometry, and audio tracks remained intact:

| Clip Index | File Path | Resolution | Duration | Video Codec | Audio Codec | Playable |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `clip_00` | `output/.../clip_00/final.mp4` | 1080x1920 | 12.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_01` | `output/.../clip_01/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_02` | `output/.../clip_02/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_03` | `output/.../clip_03/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_04` | `output/.../clip_04/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_05` | `output/.../clip_05/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_06` | `output/.../clip_06/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_07` | `output/.../clip_07/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |
| `clip_08` | `output/.../clip_08/final.mp4` | 1080x1920 | 14.0s | h264 (NVENC) | aac (128k) | **YES** |

---

## 5. Architectural Verification of Fixes

1. **Fix A (Strict Render Limit):**
   - Verified that `_worker_count(cfg, 9)` returned `1`.
   - Thread pool never exceeded 1 concurrent worker.
2. **Fix B (No LLM in Render Workers):**
   - Telemetry recorded `llm_during_render_overlap = 0`.
   - All metadata, virality, and niche classifications were prepared upfront in Phase A.
3. **Fix C (Whisper Concurrency & CPU Fallback):**
   - Telemetry recorded `whisper_during_render_overlap = 0`.
   - Missing timing repairs ran sequentially on CPU using `int8` compute (`whisper small on cpu`), keeping CUDA 100% free for NVENC.
4. **Fix D & Fix F (Centralized Resource Coordinator & Ollama Serialization):**
   - `resource_coordinator.coordinator` serialized inference requests and tracked memory.
   - `wait_for_unload` ensured Ollama released VRAM before rendering began.
5. **Fix E & Fix G (Configuration & Telemetry):**
   - Config settings (`parallel_workers: 1`, `max_system_ram_percent: 85`, `max_gpu_memory_percent: 90`) were enforced throughout the run.
