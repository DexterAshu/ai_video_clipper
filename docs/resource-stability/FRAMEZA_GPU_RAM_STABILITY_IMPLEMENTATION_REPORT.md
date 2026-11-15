# FRAMEZA — GPU / RAM STABILITY & PIPELINE ARCHITECTURE IMPLEMENTATION REPORT

**Author:** Performance Engineering & Video Architecture Team  
**Date:** 2026-11-15  
**Version:** Production Stability Release (Hardware Profile: RTX 5050 8GB / 16GB RAM)  
**Status:** IMPLEMENTED & TESTED  

---

## 1. Architecture Overhaul Overview

To eliminate system memory exhaustion, GPU driver spills into shared memory, and catastrophic SSD pagefile thrashing, Frameza has transitioned from an unconstrained multi-model concurrent execution model to a **strictly staged 3-Phase Pipeline Architecture**.

```
                           OLD ARCHITECTURE (Unstable)
                           ───────────────────────────
   [ Highlights Selected (10 Clips) ]
                   │
                   ▼
   [ ThreadPoolExecutor (10 Parallel Workers spawned via cpu_count // 2) ]
        ├── Worker 0: NVENC + MediaPipe + Whisper CUDA + Qwen (3 LLM calls) ──┐
        ├── Worker 1: NVENC + MediaPipe + Whisper CUDA + Qwen (3 LLM calls) ──┤
        ├── ...                                                               ├─► GPU VRAM 100% (8GB)
        └── Worker 9: NVENC + MediaPipe + Whisper CUDA + Qwen (3 LLM calls) ──┘   Shared VRAM 100% (8GB)
                                                                                  RAM 100% (16GB)
                                                                                  SSD Thrash 100%
                                                                                  SYSTEM FREEZE

                           NEW ARCHITECTURE (Rock-Solid)
                           ─────────────────────────────
   [ Highlights Selected (10 Clips) ]
                   │
                   ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ PHASE A — Dedicated Sequential AI Preparation Phase                    │
   │ - Sequential timing repair on CPU (int8) if words missing             │
   │ - Sequential caption language translation (Qwen)                       │
   │ - Sequential metadata generation (title, description, tags)            │
   │ - Sequential virality rating & niche classification                    │
   │ - Results persisted to clip_XX/prepared_ai.json (reusable)             │
   └────────────────────────────────────────────────────────────────────────┘
                   │
                   ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ PHASE B — Complete Model Eviction & VRAM Verification                  │
   │ - POST keep_alive: 0 to Ollama                                         │
   │ - Poll /api/ps to confirm target & fallback models evicted             │
   │ - Verified 100% GPU VRAM released for video encoding                   │
   └────────────────────────────────────────────────────────────────────────┘
                   │
                   ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ PHASE C — Hardware-Bounded Video Rendering                             │
   │ - Default: 1 worker (Maximum 2 only if memory headroom validated)      │
   │ - Consumes prepared AI data: ZERO LLM calls, ZERO Whisper CUDA calls   │
   │ - Single NVENC stream has 100% GPU VRAM and zero thread contention     │
   │ - Resource coordinator continuously guards RAM and VRAM thresholds     │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Key Modules Modified & Implemented

### 1. `resource_coordinator.py` (New Centralized Coordinator)
- **Windows RAM Telemetry:** Direct calls via Windows kernel `GlobalMemoryStatusEx` (`dwMemoryLoad`, `ullTotalPhys`, `ullAvailPhys`, `ullTotalPageFile`). No heavy third-party dependencies.
- **Dedicated VRAM Telemetry:** Fast 1-second cached queries via `nvidia-smi` (`memory.total`, `memory.used`, `memory.free`, `utilization.gpu`).
- **Resource Pressure Guardrails:** Configurable safety thresholds (default: 85% RAM, 90% Dedicated VRAM). Flags warnings and halts additional worker spawning if thresholds are breached.
- **Active Operations Tracking:** Context managers `track_worker()`, `track_llm()`, `track_transcription()`, `track_ffmpeg()`.

### 2. `pipeline.py` (Concurrency & Pipeline Separation)
- **`_worker_count(cfg, n_clips)`:** Hard-defaults to **1 worker**. Ignores `cpu_count // 2`. Clamps any configured value strictly to a maximum of 2, and validates free VRAM ($\ge 3000\text{ MB}$) and RAM ($< 80\%$) before allowing 2 workers.
- **`_prepare_clip_ai_sequentially(...)`:** Runs Phase A on the main thread before rendering. Repaires missing words, translates caption languages, generates metadata, scores virality, and classifies niches sequentially. Saves `prepared_ai.json` per clip.
- **`llm_manager.wait_for_unload(timeout_s=10.0)`:** Phase B verifies complete eviction of Qwen before opening the render thread pool.
- **`_render_one(...)`:** Phase C receives `prepared_ai`. Completely skips on-demand transcription, language translation, metadata generation, and virality scoring during rendering.

### 3. `transcribe.py` (Whisper Caching & CPU Fallback)
- **Model Instance Caching:** `_model_cache` keyed by `(model_name, device, compute_type)`. Reuses existing model instances rather than recreating them.
- **`repair_clip_transcription(...)`:** Performs missing-word timing repairs on CPU using `int8` compute, ensuring CUDA memory is never touched during repair.
- **`_transcribe_lock`:** Thread-safe serialization prevents concurrent CUDA initialization.

### 4. `llm_manager.py` (Ollama Serialization & Eviction Verification)
- **`_llm_request_lock`:** Reentrant lock wraps `query_ollama` so concurrent callers queue cleanly, preventing Ollama timeouts and `llama-server.exe` crashes.
- **`wait_for_unload(...)`:** Polls `/api/ps` after posting `keep_alive: 0` until model eviction is verified.

### 5. `ffutil.py` (FFmpeg Tracking)
- Wraps all FFmpeg executions in `resource_coordinator.coordinator.track_ffmpeg()`.

### 6. Configuration Integration (`config.yaml` & `config.local.yaml`)
```yaml
render:
  parallel_workers: 1           # 1 worker default (safe for RTX 5050 8GB / 16GB RAM)
  resource_aware: true          # monitors RAM and VRAM before launching jobs

resources:
  max_system_ram_percent: 85    # guards worker spawning when RAM exceeds 85%
  max_gpu_memory_percent: 90    # guards worker spawning when dedicated VRAM exceeds 90%
  pause_on_critical_pressure: true

transcription:
  on_demand_during_render: false # never invoke on-demand Whisper inside render workers
  fallback_device: cpu           # missing-word timing repairs use CPU to preserve GPU VRAM
  fallback_compute_type: int8

llm:
  concurrent_requests: 1        # serialize LLM calls to prevent VRAM spikes
  unload_before_render: true    # evict model from GPU before video render
```

---

## 3. Verification & Test Suite Results

The following test suites were created and executed with `.venv\Scripts\python.exe -m pytest`:

| Test File | Tests | Status | Scope |
| :--- | :---: | :---: | :--- |
| `tests/test_render_resource_limits.py` | 6 | **PASSED** | Worker count defaulting to 1, capping at 2, pressure fallback |
| `tests/test_ai_preparation_phase.py` | 3 | **PASSED** | Sequential metadata, virality, niche, and CPU timing repair |
| `tests/test_no_llm_during_render.py` | 1 | **PASSED** | Asserts 0 LLM and 0 Whisper calls inside `_render_one` |
| `tests/test_llm_manager_concurrency.py`| 2 | **PASSED** | Serialized Ollama requests, verified model eviction |
| `tests/test_transcription_serialization.py` | 2 | **PASSED** | Whisper model caching and CPU fallback |
| `tests/test_resource_monitor.py` | 4 | **PASSED** | GlobalMemoryStatusEx, nvidia-smi, pressure thresholds |
| `tests/test_pipeline_resource_recovery.py` | 2 | **PASSED** | Cancellation safety and metadata reuse during rerenders |
| `tests/test_pipeline_director_integration.py` | 3 | **PASSED** | Stage 8.1 dynamic director layouts and fallbacks |
| `tests/test_dynamic_director.py` | 17 | **PASSED** | All 5 layouts: full, split_tb, split_lr, wide, two_person |
| `tests/test_director_fallbacks.py` | 4 | **PASSED** | Filtergraph construction, error recovery, portrait geometry |
| `tests/test_audio_visual_speaker.py` | 5 | **PASSED** | Multi-speaker tracking and lip-sync speech activity |
| `tests/test_rerender_director_integration.py` | 4 | **PASSED** | Single-clip rerendering with dynamic director |
| **Total Test Count** | **53** | **ALL PASSED** | **100% Pass Rate** |
