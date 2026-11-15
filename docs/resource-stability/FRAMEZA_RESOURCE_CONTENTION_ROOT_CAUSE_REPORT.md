# FRAMEZA — RESOURCE CONTENTION & SYSTEM FREEZE ROOT CAUSE REPORT

**Date:** 2026-11-15  
**Hardware Profile:** Intel Core i7-13650HX (14 cores, 20 threads), NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM), 16GB DDR5 RAM, NVMe SSD (Disk 0)  
**OS:** Windows 11  
**Repository:** Frameza AI Video Clipper  

---

## 1. Executive Summary

During execution of the Frameza clipping pipeline on local videos (e.g. `20261115-151408_b0f1c164_I_Regret_Inviting_Ravi_Gupta_on`), the system experienced catastrophic hardware saturation:
- **Dedicated GPU VRAM:** Flatlined at 7.6 / 8.0 GB (98–100%)
- **Shared GPU Memory:** Flatlined at 7.8 / 7.9 GB (100% full)
- **System RAM:** Reached 14.9 / 15.7 GB (95–100%)
- **Disk 0 (NVMe SSD):** 100% active time (continuous paging thrash)
- **Symptom:** Windows system freeze / lockup ("laptop stopped working")

Our deep investigation of code and logs revealed that the freeze was not caused by a single bug, but by a **deadly 4-way concurrency collision** triggered when the pipeline transitioned into the `render` stage ("Making your clips").

---

## 2. Root Cause Analysis: The 4 Cascading Failures

### Root Cause 1: Unrestricted Concurrency in `_worker_count`
In `pipeline.py`:
```python
def _worker_count(cfg: dict, n_clips: int) -> int:
    setting = cfg["render"].get("parallel_workers", "auto")
    if setting != "auto":
        return max(1, int(setting))
    return max(1, min(n_clips, (os.cpu_count() or 2) // 2))
```
On a 20-thread CPU with 10 candidate clips, `(20 // 2) = 10`. The worker pool launched **10 clips simultaneously** in a `ThreadPoolExecutor(max_workers=10)`.

### Root Cause 2: On-Demand Whisper CUDA Instances in Parallel Threads
In `pipeline.py` (line 594), when words were missing in candidate spans:
```python
if not words and cfg.get("captions", {}).get("enabled", True) and preset != "none":
    fresh_tr = transcribe_mod.transcribe(info["audio_path"], cfg, spans=[(out_start, out_end)])
```
Every thread with missing span words concurrently invoked `transcribe()`, creating multiple fresh `WhisperModel("medium", device="cuda")` instances. Each model instance demands ~1.5–2.5 GB of VRAM. Multiple concurrent allocations instantly triggered:
```text
WARNING frameza.pipeline on-demand clip transcribe fallback failed 
([transcribe] faster-whisper failed — CUDA failed with error out of memory)
```

### Root Cause 3: Ollama / Qwen Re-Entry During Parallel NVENC Video Rendering
Before rendering, `pipeline.py` called `llm_manager.unload_model()`. However, inside `_render_one` (lines 678, 700, 717), each of the 10 worker threads called:
1. `metadata_mod.generate_metadata()`
2. `virality_mod.rate_virality()`
3. `classify_mod.classify_niche()`
4. `caption_lang.process_caption_language()`

All 10 parallel threads simultaneously bombarded `http://localhost:11434/api/generate` requesting `qwen3.5:4b`. Ollama reloaded Qwen into VRAM (~3.8 GB) while VRAM was already exhausted by NVENC and Whisper. 

Because multiple requests hit Ollama concurrently, Ollama timed out (120s) and its `llama-server.exe` process crashed:
```text
WARNING frameza.llm_manager primary model 'qwen3.5:4b' failed: Read timed out (120.0s)
WARNING frameza.llm fallback ollama HTTP 500: {"error":"llama-server process no longer running: exit status 0xc0000409 ... CUDA error: shared object initialization failed"}
```

### Root Cause 4: The Windows WDDM Paging Cliff (System Freeze)
Windows Display Driver Model (WDDM) handles GPU memory over-allocation by spilling dedicated VRAM into **Shared GPU Memory** (allocated directly from system RAM, capped at 50% of physical RAM = 7.9 GB).
- Dedicated VRAM consumed: **7.6 GB / 8.0 GB**
- Shared VRAM consumed from RAM: **7.8 GB / 7.9 GB**
- Windows OS + Python + FFmpeg processes: **~7 GB RAM**
- **Total RAM demanded:** $7.8\text{ GB} + 7.0\text{ GB} = 14.8\text{ GB} / 15.7\text{ GB}$ (95–100%).

With physical RAM exhausted, the Windows kernel entered emergency memory swapping to `pagefile.sys` on Disk 0. The NVMe SSD was forced into 100% active time thrashing. Under 100% pagefile I/O, CPU threads lock up waiting for memory pages, causing the entire operating system to freeze.

---

## 3. Comparison: "Polishing clip timing" vs. "Making your clips"

| Attribute | Polishing Clip Timings (`refine`) | Making Your Clips (`render`) |
| :--- | :--- | :--- |
| **Execution Model** | Sequential (one candidate at a time) | Unbounded parallel (10 threads simultaneously) |
| **VRAM Consumption** | ~3.8 GB (Qwen 3.5 4B alone) | Multi-GB (Qwen + NVENC $\times 10$ + Whisper CUDA $\times 5$) |
| **Ollama Requests** | Serialized (1 request per step) | Concurrent burst (30 requests sent at once) |
| **System Outcome** | Stable, high resource usage (~70 tok/s) | Instant VRAM exhaustion $\rightarrow$ RAM spill $\rightarrow$ SSD thrash $\rightarrow$ Freeze |

---

## 4. Key Takeaways

1. **Qwen was indeed a core trigger**, but not because of the model itself. Rather, Qwen was being called **concurrently across 10 threads during hardware video encoding**, after having been unloaded.
2. Deriving GPU render workers from CPU core count (`cpu_count // 2`) is fatal on laptop GPUs with 8GB VRAM.
3. On-demand Whisper transcription on CUDA inside render workers caused massive VRAM contention.
4. Total separation of the AI phase from the video rendering phase is required for rock-solid stability.
