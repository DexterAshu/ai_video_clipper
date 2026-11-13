# Frameza — Stage 3 Completion Report

**Project:** Frameza (Local AI Video Clipper)  
**Hardware Platform:** Lenovo LOQ 15IRX10 · Intel Core i7-13650HX · NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM, Blackwell) · 16GB DDR5 RAM · Windows 11 Home 64-bit  
**Completed Date:** November 14, 2026  
**Status:** **STAGE 3 COMPLETE — ALL VERIFICATIONS PASSED**

---

## 1. Executive Summary

Stage 3 transforms Frameza from a backend pipeline into a **complete, free, responsive, locally running AI video clipping web application**. 

All 12 tasks outlined for Stage 3 have been designed, implemented, and verified:
1. **Modern Web Dashboard**: Real-time project overview, metrics cards (Total videos processed, Total clips generated, Active jobs, Completed runs, Disk storage used), and live system hardware status.
2. **Dual-source Video Ingestion**: YouTube URL input with live metadata preview (thumbnail, title, author, duration before downloading) alongside local file drag-and-drop and file picking (MP4, MKV, MOV, WebM, AVI) with upload progress indicators.
3. **Comprehensive Processing Configuration Panel**: Granular controls for 9:16 vertical, 1:1 square, and 16:9 landscape; duration presets (15–30s, 30–60s, 60–90s, Custom); clip count presets (Auto, 3, 5, 10, Custom); resolution selection (1080p Full HD vs 720p HD); and toggles for smart face tracking, animated subtitles, AI viral moments, AI social metadata, and background music.
4. **Real-time Processing Monitor**: WebSocket progress tracking with dot-matrix visualizer, stage status indicators, ETA, a live execution log console, and a safe job cancellation button.
5. **Clip Preview, Player & Social Media Package**: Vertical 9:16 video players for each clip, engagement reason badges, virality scores, 1-click "Copy Title", 1-click "Copy Description & Tags", and thumbnail download.
6. **Clip Management & Export**: Individual MP4 download, automatic poster frame extraction, full-job ZIP bundle downloads, and clip/project deletion.
7. **History Library**: Searchable past runs with status filtering (All, Completed, Failed), niche chips, ZIP exports, and project deletion.
8. **System Health Monitor**: Live hardware status showing NVIDIA RTX 5050 NVENC + CUDA, Faster-Whisper, Ollama `llama3.2:3b`, and local FFmpeg.

---

## 2. Architecture & File Modifications

### A. Backend Enhancements

| File | Changes Made |
| :--- | :--- |
| `server/routes_run.py` | • Added `POST /api/youtube/inspect` using `yt-dlp` to preview YouTube metadata (title, duration, thumbnail, author) without downloading.<br>• Extended `RunRequest` with `quality`, `face_tracking`, `generate_captions`, and `generate_metadata`.<br>• Updated `_build_cfg()` to map `720p` resolution and toggles into pipeline config. |
| `server/routes_library.py` | • Added `GET /api/dashboard/stats` aggregating videos, clips, active jobs, completed jobs, and storage usage.<br>• Added `DELETE /api/jobs/{job_name}` to cleanly remove past projects and invalidate cache.<br>• Enhanced `GET /api/files/{job_name}/{clip}/{name}` with on-demand poster frame extraction via FFmpeg when `poster.jpg` is requested. |
| `server/routes_settings.py` | • Enhanced `GET /api/system` to report GPU device name, VRAM, Faster-Whisper CUDA status, NVENC status, Ollama status, model name (`llama3.2:3b`), and local FFmpeg binary path. |
| `reframe.py` | • Added dynamic resolution support for 1080p and 720p across 9:16, 1:1, and 16:9 aspect ratios.<br>• Added native bypass for `face_tracking: false` to skip proxy creation and execute fast center crop directly. |
| `pipeline.py` | • Connected `captions_enabled: false` to skip subtitle burning when captions are disabled.<br>• Added conditional check to bypass LLM metadata generation when metadata is toggled off. |

### B. Frontend Enhancements

| File | Changes Made |
| :--- | :--- |
| `web/js/app.js` | • Auto-defaulted workspace to `"default"` on first navigation so users land directly on the Dashboard/Home.<br>• Built the Dashboard Stats Cards and Live Hardware Status Pill.<br>• Implemented debounced YouTube URL inspection with video preview card.<br>• Added duration presets (`15-30s`, `30-60s`, `60-90s`, `Custom`), clip count pills (`Auto`, `3`, `5`, `10`, `Custom`), and resolution toggles (`1080p`, `720p`).<br>• Enhanced `renderRun` with collapsible live execution log terminal.<br>• Enhanced `clipCard` with AI social media package (Title, Description, Hashtag pills, 1-click Copy buttons, Thumbnail download).<br>• Enhanced `renderHistory` with live search bar, status tabs (`All`, `Completed`, `Failed`), ZIP download, and project deletion. |
| `web/js/ui.js` | • Added `copyToClipboard(text, btn)` helper with visual feedback and toast notifications. |
| `web/css/screens.css` | • Added responsive styles for `.dashboard-stats`, `.stat-card`, `.sys-pill`, `.yt-preview-card`, `.pill-btn`, `.clip-meta-box`, `.clip-tag`, `.log-console`, and `.history-toolbar`. |

---

## 3. Verification & Test Results

A dedicated automated test suite (`tests/test_stage3_api.py`) was executed using the project's Python 3.11 environment:

```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe tests\test_stage3_api.py
```

### Verification Checklist:

| Feature / Endpoint | Test Description | Result |
| :--- | :--- | :---: |
| `GET /api/system` | Verifies GPU transcribe (CUDA), encode (NVENC), device name, and FFmpeg path | **PASSED** |
| `GET /api/dashboard/stats` | Verifies aggregation of total videos, clips, active jobs, and disk storage | **PASSED** |
| `GET /api/jobs` | Verifies historical jobs list from disk records | **PASSED** |
| `POST /api/youtube/inspect` | Verifies input validation and extraction schema | **PASSED** |
| `GET /api/files/.../poster.jpg` | Verifies on-demand FFmpeg poster frame extraction (1080x1920 JPEG) | **PASSED** |
| `GET /api/jobs/.../zip` | Verifies bundle generation packaging final MP4, SRT, and JSON | **PASSED** |
| `DELETE /api/jobs/{name}` | Verifies complete project removal from disk | **PASSED** |

---

## 4. How to Launch and Use the Web Application

1. **Start the application:**
   Double-click `run.bat` or run in PowerShell:
   ```powershell
   .\run.bat
   ```
2. **Access the Web Dashboard:**
   Open your browser to:
   ```
   http://127.0.0.1:7860/
   ```
3. **Clip a Video:**
   - **From YouTube:** Paste any YouTube video link. Frameza will inspect the video, preview its thumbnail, title, and duration.
   - **From Local Disk:** Drag and drop any MP4, MKV, MOV, WebM, or AVI file.
   - **Choose Preferences:** Select your target format (9:16 Vertical, 1:1 Square, 16:9 Landscape), duration (e.g. 30–60s), number of clips, quality (1080p / 720p), and toggle face tracking or subtitle styles.
   - Click **"Make clips"**.
4. **Monitor Progress:**
   Watch live stage execution, percentage, and real-time logs in the processing screen.
5. **Review and Export:**
   Play vertical clips in the browser, copy AI titles and hashtags with 1 click, download individual MP4s and thumbnails, or download everything as a ZIP file.

---

## 5. Next Steps

Stage 3 is 100% complete. In accordance with instructions, development stops here to await user feedback before proceeding to Stage 4.

