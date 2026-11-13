# Stage 2 Execution Report: Windows Environment, Dependencies, FFmpeg, NVIDIA GPU & Ollama

**Date:** November 14, 2026  
**Project:** Frameza (Local AI Video Clipper)  
**Target Hardware:** Lenovo LOQ 15IRX10 | Intel Core i7-13650HX | NVIDIA GeForce RTX 5050 (8GB VRAM) | 16GB DDR5 RAM | Windows 11 Home 64-bit  
**Status:** **STAGE 2 COMPLETE & VERIFIED**

---

## 1. Summary of Installations & Deployments

1. **Python 3.11 Virtual Environment (`.venv`):**
   - Created with explicit binary: `C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe`.
   - All 114 dependencies from `requirements.txt` installed into `.venv` without conflicts.
   - Core modules verified: `fastapi`, `uvicorn`, `faster_whisper`, `ctranslate2`, `mediapipe`, `opencv-python`, `scenedetect`, `yt-dlp`, `pyyaml`.

2. **Project-Local FFmpeg & FFprobe:**
   - Deployed portable static Windows GPL build into `tools/ffmpeg/bin/ffmpeg.exe` and `tools/ffmpeg/bin/ffprobe.exe`.
   - Verified codecs: H.264 (`libx264`, `h264_nvenc`), H.265/HEVC (`hevc_nvenc`), AV1 (`av1_nvenc`), AAC.
   - Verified filter support: `libass` (Advanced SubStation Alpha subtitle rendering).

3. **CUDA Runtime Libraries for CTranslate2 / Faster-Whisper:**
   - Installed `ctranslate2==4.8.1`, `nvidia-cublas-cu12==12.9.2.10`, and `nvidia-cudnn-cu12==9.24.0.43`.
   - Verified automatic DLL discovery via `transcribe._register_cuda_dll_dirs()`.
   - Verified 1 CUDA device visible and active.

4. **Local LLM Engine (Ollama):**
   - Verified Ollama binary at `C:\Users\getaa\AppData\Local\Programs\Ollama\ollama.exe`.
   - Background service started on `http://127.0.0.1:11434`.
   - Downloaded and verified lightweight model `llama3.2:3b` (2.0 GB).

---

## 2. Configuration & Overrides Applied

Created `config.local.yaml` to configure local hardware without altering `config.yaml`:
```yaml
ffmpeg:
  binary: tools/ffmpeg/bin/ffmpeg.exe
  ffprobe_binary: tools/ffmpeg/bin/ffprobe.exe

llm:
  provider: ollama
  ollama_model: llama3.2:3b
  ollama_base_url: http://localhost:11434

whisper:
  model_override: small

render:
  compute: auto
  use_nvenc: auto
```

---

## 3. Verification & Benchmark Results

### A. GPU Acceleration & Diagnostic Verdict (`check_gpu.py`)
```
ClipForge GPU check
========================================
[1] NVIDIA driver:       NVIDIA GeForce RTX 5050 Laptop GPU, 610.74
[2] ffmpeg / NVENC:      D:\Project Development\Python\AI-Video-Clipper\tools\ffmpeg\bin\ffmpeg.exe
                         N-128889-g88c46fce27-20261113
                         NVENC smoke encode: SUCCESS (h264_nvenc)
[3] faster-whisper/CUDA: ctranslate2 4.8.1, cuda_devices=1
                         nvidia-cublas: installed
                         nvidia-cudnn:  installed
========================================
VERDICT
  GPU encode:    WORKING
  GPU transcribe: WORKING
```

### B. Hardware Subtitle Rendering Test
- **Filter:** `subtitles` with `libass`.
- **Encoder:** `h264_nvenc`.
- **Output:** 1080x1920 (9:16) video with burned styled karaoke text rendered with exit code 0.

### C. CPU Fallback Encoding Test
- **Encoder:** `libx264`.
- **Resolution:** 256x256 test pattern.
- **Output:** Verified clean exit code 0.

### D. Faster-Whisper CUDA Test
- Model loaded on `device='cuda'`, `compute_type='float16'`.
- Successfully transcribed test WAV; verified zero runtime errors and pure GPU execution.

### E. Ollama Local LLM Test
- Process verified running on `http://localhost:11434`.
- `llama3.2:3b` verified installed and operational.

### F. End-to-End Pipeline Smoke Test (`pipeline.py`)
Executed full automated pipeline on `samples/sample.mp4`:
- **Stage 1 (Ingest):** Completed in 0.2s (remux fast path).
- **Stage 2 (Scenes):** Completed in 16.9s (PySceneDetect shot boundaries).
- **Stage 3 (Transcribe):** Completed in 17.4s on NVIDIA CUDA using `whisper small`.
- **Stage 4 (Events):** Completed in 0.1s (local audio DSP).
- **Stage 5 (Highlights):** Completed in 0.0s (candidate selection).
- **Stage 6 (Refine):** Completed in 0.9s (pause compression & subtitle detection).
- **Stage 7 (Render Clips):** Completed in 8.2s (MediaPipe face tracking, smoothed 9:16 reframe, subtitle burn, audio mixdown with loudnorm, rendered via NVENC `h264_nvenc`).
- **Stage 8 (Rescore):** Completed in 0.0s.
- **Total Pipeline Execution:** **26.9 seconds**.
- **Output:** `output/20261114-002848_sample/clip_00/final.mp4` verified as 1080x1920 H.264/AAC.

---

## 4. Environment Check Script (`check-environment.ps1`)

Automated health check script created and verified:
- `[ 1] Python 3.11 Installation : [PASS]`
- `[ 2] Virtual Environment (.venv): [PASS]`
- `[ 3] Core Python Packages : [PASS]`
- `[ 4] FFmpeg Binary : [PASS]`
- `[ 5] FFprobe Binary : [PASS]`
- `[ 6] Subtitle Engine (libass) : [PASS]`
- `[ 7] Hardware Encoder (NVENC) : [PASS]`
- `[ 8] NVIDIA GPU Detected : [PASS] (RTX 5050 Laptop GPU, 610.74)`
- `[ 9] CUDA Driver Compatibility: [PASS]`
- `[10] Faster-Whisper CUDA : [PASS] (1 device(s))`
- `[11] Ollama Installation : [PASS]`
- `[12] Ollama Background Service : [PASS]`
- `[13] Ollama Models : [PASS] (llama3.2:3b)`
- `[14] Available Disk Space : [PASS] (D: 40 GB free | C: 31.2 GB free)`

---

## 5. Errors Encountered & Engineering Solutions

| # | Error / Symptom | Root Cause | Engineering Solution |
| :--- | :--- | :--- | :--- |
| **1** | `py -3.11` fails with `"No Installed Pythons Found!"` | Python 3.11.9 was installed per-user without Windows py launcher registration. | Explicitly targeted `C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe` to create `.venv`, and updated `run.bat` to detect and use it. |
| **2** | `NVENC init failed: invalid param (8): Frame Dimension less than minimum supported value` | `ffutil.py` smoke test probed `s=64x64`. NVIDIA Blackwell (RTX 5050) NVENC hardware requires minimum frame dimensions $\ge 145 \times 145$. | Updated `ffutil.py` probe to `color=black:s=256x256`, enabling flawless NVENC detection. |
| **3** | `Unable to open D:Project Development...` in libass | Windows drive letters and backslashes in filter strings need escaping. | Utilized project's `ffutil.filter_path()` to escape file paths for FFmpeg `-vf subtitles='...'`. |
| **4** | `FileNotFoundError` in `scripts/make_synthetic_sample.py` | Script hardcoded `["ffmpeg", ...]` and `["ffprobe", ...]`, ignoring `tools/ffmpeg/bin`. | Patched `make_synthetic_sample.py` to import `ffmpeg_bin()` and `ffprobe_bin()` from `ffutil.py`. |
| **5** | Whisper failed to download `large-v3` in sandbox | Default config specified `large-v3`, which was not yet cached. | Prefetched and cached `small` model in `cache/models/` and configured `whisper.model_override: small`. |

---

## 6. Launcher Status (`run.bat`)

The launcher was updated to:
1. Automatically add `tools\ffmpeg\bin\` to session `PATH`.
2. Locate Python 3.11 and create/repair `.venv` if needed.
3. Automatically detect if `ollama.exe` is running; starts `ollama serve` in the background if offline.
4. Execute `.venv\Scripts\python.exe -m server.main`.
5. Keep the console window open with clear error diagnostics if a failure occurs.

---

## 7. Items Requiring Attention in Stage 3

1. **End-to-End Web UI Testing:** Verify video upload, YouTube URL ingestion, and interactive clip editing on `http://127.0.0.1:7860`.
2. **Ollama Structured Output Optimization:** Test and tune `llama3.2:3b` prompt response times for long video transcripts.
3. **Background Music Pre-caching:** Pre-cache the 5 CC BY 4.0 tracks in `assets/music/` to enable fully offline audio mixdown.
4. **YouTube Ingest Reliability:** Test `yt-dlp` against YouTube URLs to verify rate limiting and format handling.

