# PROJECT AUDIT: Frameza (AI-Video-Clipper)

**Audit Date:** November 13, 2026  
**Auditor:** Senior AI/ML & Video Processing Engineer  
**Repository:** https://github.com/dexterAshu/frameza  
**Target Environment:** Windows 11 Home 64-bit | Lenovo LOQ 15IRX10 | Intel Core i7-13650HX | NVIDIA GeForce RTX 5050 (8GB VRAM) | 16GB DDR5 RAM  

---

## 1. Executive Summary

Frameza is an open-source, self-hosted video repurposing application designed to convert long-form horizontal videos (local files or YouTube URLs) into vertical 9:16 short clips (for YouTube Shorts, Instagram Reels, and TikTok). It incorporates speech transcription, scene detection, AI-driven highlight detection, intelligent face tracking reframing, animated karaoke subtitles, social metadata generation, and background music integration.

The repository is built on a clean Python architecture, combining a **FastAPI backend** (with WebSocket support and background daemon workers) and a **custom lightweight web frontend** (HTML5, CSS, vanilla JS with live progress boards and preview pickers).

The local hardware (Intel i7-13650HX, 16GB DDR5 RAM, NVIDIA GeForce RTX 5050 8GB VRAM, 610.74 driver) is capable of running the entire pipeline locally, freely, and with GPU acceleration. However, the system currently lacks an initialized Python 3.11 virtual environment, FFmpeg in PATH, installed CUDA libraries, and an active Ollama service.

---

## 2. Hardware & Environment Inspection Results

| Component | Target / Detected Specification | Status | Findings & Notes |
| :--- | :--- | :--- | :--- |
| **Operating System** | Windows 11 Home 64-bit | **Compatible** | Fully supported by Frameza Windows runner. |
| **CPU** | Intel Core i7-13650HX (14 cores, 20 threads) | **Compatible** | High multithreaded capacity for audio DSP and parallel clip rendering. |
| **RAM** | 15.71 GB Visible (DDR5), 4.44 GB Currently Free | **Compatible** | Sufficient for running local Whisper models (small/int8 or large-v3) and 3B–8B local LLMs with quantized weights. |
| **Storage** | C: (34.7 GB free) / D: (43.8 GB free) | **Compatible** | Workspace is on `D:\Project Development\Python\AI-Video-Clipper`. Adequate room for models, FFmpeg, and temporary render caches. |
| **NVIDIA GPU** | NVIDIA GeForce RTX 5050 Laptop GPU (8151 MiB VRAM) | **Detected** | Blackwell architecture with 9th Gen NVENC encoder. |
| **NVIDIA Driver** | 610.74 (WDDM), CUDA UMD 13.3 | **Compatible** | Driver is modern and fully supports CUDA 12/13 and NVENC API. |
| **Git** | git version 2.56.0.windows.4 | **Available** | Located in `C:\Program Files\Git\cmd\git.exe`. |
| **Python Installations** | Python 3.10.11 (default in PATH)<br>Python 3.11.9 (`%LOCALAPPDATA%\Programs\Python\Python311`) | **Action Required** | Python 3.10 is currently first in PATH. Python 3.11.9 is installed, but `py -3.11` fails because Windows `py.exe` has not registered it. Frameza strictly enforces Python 3.11 for MediaPipe compatibility. |
| **Python Environment** | Global Python 3.11 has only base `pip` (24.0) | **Missing venv** | No `.venv` exists in the repository. Dependencies in `requirements.txt` are not yet installed. |
| **FFmpeg / FFprobe** | Not found in PATH or in `tools/ffmpeg/` | **Missing** | `ffmpeg -version` fails; `check_gpu.py` fails on NVENC smoke test due to missing FFmpeg binary. |
| **CUDA Runtime** | ctranslate2 / cuBLAS / cuDNN not installed | **Missing in venv** | `check_gpu.py` reports `No module named 'ctranslate2'`, missing `nvidia-cublas` and `nvidia-cudnn`. |
| **Ollama** | Installed at `C:\Users\getaa\AppData\Local\Programs\Ollama\ollama.exe` | **Offline / Unloaded** | Binary is in PATH and installed, but the background service is not running and no local models are currently loaded. |
| **NVENC Hardware** | NVIDIA GeForce RTX 5050 Laptop GPU | **Supported** | Hardware supports AV1, HEVC, and H.264 hardware encoding. Requires an NVENC-enabled FFmpeg build. |

---

## 3. Existing Architecture Analysis

### A. Technology Stack
- **Backend Framework:** FastAPI (`fastapi==0.139.0`, `uvicorn==0.50.0`, `starlette==1.3.1`).
- **Frontend Architecture:** Static Vanilla HTML5/CSS/JavaScript with WebSocket-driven progress tracking and REST API endpoints (`/api/run`, `/api/presets`, `/api/channels`, `/api/workspaces`, `/api/library`, `/api/edit`, `/api/upload`, `/api/analytics`, `/api/settings`). Served directly via FastAPI `StaticFiles` mounted at `/`.
- **Packaging & Desktop Mode:** Edge/Chrome App window launcher (`launcher.py` using `--app` mode).

### B. Core Subsystems

1. **Ingest Subsystem (`ingest.py`):**
   - Supports YouTube and external URLs via `yt-dlp`. Prefers AVC1 (H.264) <= 1080p to allow fast stream-copy remuxing without re-encoding.
   - Probes files with `ffprobe` (`ffutil.py`) and determines normalization plan (`remux`, `audio_only`, or `reencode`). Produces standardized `video.mp4` and 16 kHz mono `audio.wav`.

2. **Transcription Engine (`transcribe.py` & `segment.py`):**
   - Powered by `faster-whisper` (`ctranslate2 4.8.1`).
   - Word-level timestamps with VAD filtering and greedy decoding (`beam_size: 1`).
   - Model matrix: GPU defaults to `large-v3` (`float16`), CPU defaults to `small` (`int8`).
   - Includes **Segment-First Transcription**: calculates per-second RMS energy on `audio.wav` and cross-references PySceneDetect cuts to only transcribe candidate high-energy spans (~3x target clip count), drastically reducing transcription time.
   - Dynamic cuBLAS/cuDNN DLL resolution via Python wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`).

3. **Scene Detection (`scenes.py`):**
   - Implemented using `PySceneDetect` (`ContentDetector(threshold=27.0)`).
   - Generates shot boundaries used for candidate window snapping and reframe smoothing resets.

4. **Highlight Detection & Virality Scoring (`highlights.py`, `virality.py`, `video_events.py`):**
   - Candidate window generator builds sentence-aligned 30–60s spans.
   - Primary AI highlight scoring uses strict JSON schemas via `llm.py`.
   - **Deterministic Fallback Ladder:** If an LLM call fails or times out, it performs retries, JSON repairs, and ultimately falls back to a deterministic rule-based heuristic scoring based on hook keywords, speech pacing, and emotional keyword density.
   - **Multimodal Viral Detection v2 (`video_events.py`):**
     - Local Audio DSP: 100% local, runs on `audio.wav` via NumPy and FFT to detect energy spikes and crowd laughter bursts without an external model.
     - Cloud Vision (Gemini / OpenRouter): Optional cloud eyes. By default, `viral_v2.allow_upload: false` protects user privacy for local files.
   - **Engagement Signals (`virality.py`):** Computes explainable scores (0–100) across 6 dimensions: hook, completeness, pacing, captions, duration, and delivery.

5. **Video Reframing & Face Tracking (`reframe.py`):**
   - MediaPipe Face Detection (`model_selection=1`) and Face Mesh for active speaker approximation based on mouth-openness variance across frames.
   - Runs tracking on a downscaled 360p proxy video (`proxy_fps: 12`) for speed.
   - Fallback hierarchy: Active Speaker -> Largest Face -> Motion Centroid (frame differencing via OpenCV) -> Center Crop with Headroom.
   - Smoothness enforcement: Centered moving average, Exponential Moving Average (`ema_alpha: 0.15`), and an acceleration-limited trapezoidal follower (`max_center_velocity_px: 14.0`, `max_center_accel_px: 4.0`).
   - Render executed via FFmpeg dynamic crop filter in a single re-encode pass.

6. **Animated Subtitles (`captions.py`):**
   - Advanced SubStation Alpha (`.ass`) karaoke subtitles burned directly into the video stream via FFmpeg `subtitles` filter with font directory pointing to bundled OFL Montserrat fonts.
   - 4 built-in presets: `karaoke-pop`, `bold-impact`, `clean-minimal`, `highlight-box`.
   - Safe-zone positioning law (vertical anchor clamped to 0.52–0.66).
   - Automatic `.srt` file generation exported alongside each clip.

7. **Social Metadata Generation (`metadata.py`):**
   - Hook-first titles (<60 chars), 2-sentence descriptions, and 8–12 topic-based hashtags.
   - Filtered against a comprehensive junk/filler blocklist (`FILLER_WORDS`).
   - Backed by LLM schema completion or deterministic template fallback.

8. **Audio & Music Handling (`music.py`):**
   - 5 royalty-free, CC BY 4.0 Kevin MacLeod tracks in `assets/music/manifest.json`.
   - Auto-matches track to transcript mood.
   - Advanced FFmpeg audio graph: sidechain ducking under voice, volume attenuation (-18 dB default), 1s in/out fades, and ITU-R BS.1770-4 loudnorm normalization (-14 LUFS, -1 dBTP).
   - Appends required CC attribution to clip metadata.

9. **Export & Batch Queuing (`cut.py`, `batch.py`, `bundle.py`, `server/routes_library.py`):**
   - Sequential batch queue with inbox directory watcher (`inbox/`).
   - Single-job and multi-job bulk zip bundling (`bundle.py`).
   - Optional YouTube upload via OAuth Desktop client (`youtube_upload.py`), disabled by default.

---

## 4. Feature Audit Table

| # | Feature | Current Implementation | Local or Cloud Dependent | Status | Required Changes for Stage 2 |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | **YouTube URL Input** | `ingest.py` (`_download_url` via `yt-dlp`) | External Download (Free, No API Key) | **Incomplete (Missing Deps)** | Install `yt-dlp` and `ffmpeg` in Python 3.11 environment. Verify download and format conversion. |
| **2** | **Local Video Upload** | `server/routes_run.py` (`/api/uploads`, streaming multipart up to 4GB) + `batch.py` (inbox watcher) | **100% Local** | **Incomplete (Missing Deps)** | Install FastAPI, uvicorn, python-multipart, and FFmpeg. |
| **3** | **AI Transcription** | `transcribe.py` (`faster-whisper` + ctranslate2 + segment-first shortlist) | **100% Local** (Offline weights) | **Incomplete (Missing Deps)** | Install `faster-whisper`, `ctranslate2`, `nvidia-cublas-cu12`, and `nvidia-cudnn-cu12` in Python 3.11. Register CUDA DLL paths. |
| **4** | **Automatic Highlight Detection** | `highlights.py` (Candidate windows -> `llm.py` -> heuristic fallback) + `video_events.py` | Configurable (Currently defaults to `auto` -> `mock` without key; supports `ollama`) | **Working in Code (Needs Local Model Setup)** | Configure `llm.provider: ollama` in `config.local.yaml`. Start Ollama service with a local model (e.g. `llama3.2:3b` or `qwen2.5:7b`). |
| **5** | **Multiple Clip Generation** | `highlights.py` (`rescore_clips`) + `pipeline.py` (per-clip loop) | **100% Local** | **Incomplete (Pipeline Blocked)** | Complete dependency installation for pipeline execution. |
| **6** | **Face Tracking & Active Speaker** | `reframe.py` (MediaPipe Face Detection + FaceMesh mouth variance + motion centroid fallback) | **100% Local** | **Incomplete (Missing Deps)** | Install `mediapipe==0.10.14`, `opencv-python`, and `numpy` in Python 3.11 venv. |
| **7** | **Vertical Video Conversion (9:16)** | `reframe.py` + `ffutil.py` (FFmpeg dynamic crop with smoothed trajectory) | **100% Local** | **Incomplete (Missing FFmpeg)** | Install FFmpeg with NVENC support. |
| **8** | **Animated Captions** | `captions.py` (ASS karaoke generator, Montserrat fonts, FFmpeg subtitles filter) | **100% Local** | **Incomplete (Missing FFmpeg)** | Install FFmpeg with `libass` support. Export `.srt` and `.ass`. |
| **9** | **AI Titles & Descriptions** | `metadata.py` (`llm.py` structured output + deterministic template fallback) | Configurable (`ollama` / `mock` / `gemini`) | **Working in Code (Needs Local Model Setup)** | Route to local Ollama instance with structured JSON prompt. |
| **10** | **Hashtag Generation** | `metadata.py` (`topic_hashtags`, filler removal, #shorts inclusion) | **100% Local** | **Working in Code** | Operates automatically once metadata runs. |
| **11** | **Background Music** | `music.py` (CC BY 4.0 manifest, auto-download, sidechain ducking, loudnorm) | **100% Local** (Cached MP3s) | **Incomplete (Missing FFmpeg)** | Ensure FFmpeg is present. Pre-cache music tracks so processing is completely offline. |
| **12** | **Video Rendering** | `ffutil.py` (`h264_nvenc` GPU encoding or `libx264` CPU fallback) | **100% Local** | **Incomplete (Missing FFmpeg)** | Install FFmpeg build compatible with NVIDIA driver 610.74. Verify NVENC smoke test. |
| **13** | **Batch Processing** | `batch.py` (`JobQueue`, multi-item queue, `inbox/` auto-watcher) | **100% Local** | **Working in Code** | Fully functional once backend server runs. |
| **14** | **Export & Download** | `bundle.py` (ZIP bundler for mp4, srt, and metadata) + web UI download buttons | **100% Local** | **Working in Code** | Operational through standard library `zipfile`. |

---

## 5. Dependency Audit

### A. Python Version Conflict
- **Current Default in PATH:** Python 3.10.11 (`C:\Users\getaa\AppData\Local\Programs\Python\Python310\python.exe`).
- **Required Version:** Python 3.11.x (strictly checked in `config.py:check_python_version` and `run.bat`).
- **Detected Python 3.11:** Python 3.11.9 is installed at `C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe`.
- **Root Cause of Launcher Failure:**
  `run.bat` invokes `py -3.11 --version`. The Windows `py.exe` launcher fails with `"No Installed Pythons Found!"` because the per-user installation was not registered in the system registry for `py.exe`. Furthermore, `Python310` precedes `Python311` in the system PATH.
- **Remediation for Stage 2:** Explicitly use `C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe` to create `.venv`, or adjust PATH/scripts so Python 3.11 is cleanly selected.

### B. Missing Core Binaries
1. **FFmpeg / FFprobe:**
   - Neither binary exists in system PATH or repository `tools/` directory.
   - Frameza includes a bootstrap download mechanism in `setup_env.py` (`FFMPEG_WIN_URL` pointing to BtbN GPL builds), or a portable Gyan.dev/BtbN release can be placed in `tools/ffmpeg/bin/`.
   - FFmpeg MUST include `h264_nvenc` and `libass` (standard in Gyan.dev and BtbN Windows builds).

### C. Missing Python Packages
No virtual environment (`.venv`) exists. All packages in `requirements.txt` must be installed into `.venv`. Key packages include:
- `fastapi`, `uvicorn`, `python-multipart` (Web server & API)
- `faster-whisper==1.2.1`, `ctranslate2==4.8.1` (Speech-to-text)
- `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (CUDA 12 runtime DLLs for Windows)
- `mediapipe==0.10.14`, `opencv-python==5.0.0.93`, `numpy==2.4.6` (Face tracking & computer vision)
- `scenedetect==0.7` (Shot boundary detection)
- `yt-dlp==2026.7.4` (YouTube downloading)
- `requests`, `pyyaml`, `pydantic` (Utilities & schemas)

### D. Cloud Dependencies & Decoupling Strategy
- `google-genai==2.10.0` is present in `requirements.txt`. However, `llm.py` uses **lazy imports**, meaning `google-genai` is never required when using `mock` or `ollama`.
- `viral_v2.providers` default to `[gemini, openrouter]`. Under keyless operation or with `viral_v2.allow_upload: false`, it automatically falls back to local Audio DSP (`video_events.py:audio_events`), keeping video analysis 100% private and offline.
- YouTube OAuth (`google-auth`, `google-api-python-client`) is optional and remains inert until the user explicitly initiates YouTube authorization.

---

## 6. Recommended Local AI Architecture

To satisfy the user's objective of a **completely free, 100% locally running AI video clipper** operating within 16GB RAM and 8GB VRAM:

```
+---------------------------------------------------------------------------------------+
|                                    USER INTERFACE                                     |
|               Local Web UI (Edge/Chrome App Window on http://127.0.0.1:7860)           |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
|                               FASTAPI BACKEND & WORKER                                |
|           (Daemon threads, job queues, event-driven WebSocket progress updates)        |
+---------------------------------------------------------------------------------------+
         |                        |                         |                    |
         v                        v                         v                    v
+------------------+    +-------------------+    +--------------------+   +-------------+
| INGESTION & AV   |    | TRANSCRIPTION     |    | LOCAL LLM ENGINE   |   | COMPUTER    |
| FFmpeg + yt-dlp  |    | Faster-Whisper    |    | Ollama (Local API) |   | VISION      |
| - Fast remux     |    | - large-v3 (CUDA) |    | - llama3.2:3b or   |   | MediaPipe   |
| - 16kHz mono WAV |    |   or small (int8) |    |   qwen2.5:7b       |   | - FaceMesh  |
| - NVENC H.264    |    | - Segment-first   |    | - Highlight rank   |   | - Speaker   |
| - Libass burning |    |   shortlisting    |    | - Titles / desc    |   |   variance  |
| - Audio mix/duck |    | - cuBLAS / cuDNN  |    | - Strict JSON      |   | PySceneDet  |
+------------------+    +-------------------+    +--------------------+   +-------------+
```

### Resource Allocation (8GB VRAM / 16GB RAM):
1. **GPU VRAM Budget (8GB):**
   - `faster-whisper` (`large-v3` with `float16` or `medium`): ~2.5 GB to 3.5 GB VRAM.
   - Ollama Local LLM (`llama3.2:3b` @ q4_K_M or `qwen2.5:7b` @ q4_K_M): ~2.5 GB to 4.5 GB VRAM.
   - NVENC hardware encoder: Dedicated chip silicon, minimal shared VRAM (<300 MB).
   - Peak concurrent VRAM stays under 7.5 GB, safely within the 8GB limit.
2. **System RAM Budget (16GB):**
   - OS & background apps: ~4.5 GB.
   - Python process, MediaPipe proxy tracking, FFmpeg buffers: ~2.5 GB.
   - Free headroom: ~4.0+ GB buffer.

---

## 7. Stage 2 Implementation Plan

1. **Step 1: Fix Python 3.11 Execution & Virtual Environment Setup**
   - Target `C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe`.
   - Create `.venv` in the repository root.
   - Upgrade `pip` and install all dependencies from `requirements.txt`.

2. **Step 2: Deploy and Configure Portable FFmpeg with NVENC & Libass**
   - Deploy a driver-compatible static Windows build of FFmpeg (Gyan.dev or BtbN master) into `tools/ffmpeg/bin/`.
   - Set `ffmpeg.binary` and `ffmpeg.ffprobe_binary` in `config.local.yaml` (or system PATH).
   - Run `check_gpu.py` to verify NVENC smoke encode (`h264_nvenc`) and CUDA device visibility.

3. **Step 3: Setup & Verify Local LLM (Ollama)**
   - Start the Ollama background service (`ollama serve`).
   - Pull an efficient local short-form editing model (`llama3.2:3b` or `qwen2.5:7b`).
   - Configure `config.local.yaml` with:
     ```yaml
     llm:
       provider: ollama
       ollama_model: llama3.2:3b
       ollama_base_url: http://localhost:11434
     ```
   - Verify structured JSON schema completion through `llm.complete_json`.

4. **Step 4: Verify Full Pipeline Locally with GPU Acceleration**
   - Run `.venv\Scripts\python.exe pipeline.py --sample --debug` using the built-in public domain test video.
   - Confirm all stages complete: Ingest -> Scene Detection -> Segment Shortlist -> Faster-Whisper GPU Transcription -> Ollama Highlight Selection -> MediaPipe 9:16 Reframe -> Animated Subtitle Burn -> Audio Mixdown -> Metadata -> Rescore.

5. **Step 5: Launch & Verify Web Application**
   - Update `run.bat` to reference `.venv\Scripts\python.exe` directly, preventing `py.exe` launcher lookup failures.
   - Launch application via `run.bat` or `python -m server.main`.
   - Verify UI on `http://127.0.0.1:7860`.

