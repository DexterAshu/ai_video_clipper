# Frameza Installation Guide (Windows 11)

This guide walks you through setting up Frameza on a Windows 11 PC equipped with an NVIDIA GPU.

---

## 1. System Requirements

### Hardware
- **Processor:** Intel Core i5/i7 (12th Gen+) or AMD Ryzen 5/7 (5000 series+). (Tested: Intel Core i7-13650HX).
- **Graphics Card:** NVIDIA GeForce RTX (30/40/50 series recommended with 6GB–8GB+ VRAM). (Tested: NVIDIA GeForce RTX 5050 Laptop GPU, 8GB VRAM).
- **RAM:** 16 GB DDR4/DDR5.
- **Storage:** At least 15 GB of free SSD storage.

### Software
- **Operating System:** Windows 11 Home / Pro (64-bit).
- **NVIDIA Driver:** Driver version 550.00 or higher (Tested: 610.74, CUDA 13.3).

---

## 2. Step-by-Step Prerequisites

### Step 2.1: Install Python 3.11.9
> [!IMPORTANT]
> Frameza requires **Python 3.11** specifically. MediaPipe and CTranslate2 bindings for Windows currently require Python 3.11 for prebuilt wheel compatibility. Do not use Python 3.12 or 3.13.

1. Download the Windows installer: [Python 3.11.9 (64-bit)](https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe).
2. During setup:
   - Check **"Add python.exe to PATH"**.
   - Choose default or custom installation (typically installed to `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`).
3. Verify installation in PowerShell:
   ```powershell
   & "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" --version
   ```

### Step 2.2: Install Ollama (Local AI Engine)
1. Download Ollama for Windows from [https://ollama.com/download/windows](https://ollama.com/download/windows).
2. Run `OllamaSetup.exe` and complete the installer.
3. Open Command Prompt and pull the recommended model:
   ```cmd
   ollama run llama3.2:3b
   ```
   Once the prompt `>>>` appears, type `/bye` to exit. The model is now cached locally.

### Step 2.3: Set Up Portable FFmpeg with NVENC & libass
Frameza looks for local FFmpeg binaries in `tools/ffmpeg/bin/`.
If you don't already have them, run the included deployment script:
```powershell
.\.venv\Scripts\python.exe scripts/deploy_ffmpeg.py
```
This fetches and extracts a verified Gyan.dev Windows build containing `h264_nvenc` and `libass`.

Verify that the binaries exist:
```text
tools\ffmpeg\bin\ffmpeg.exe
tools\ffmpeg\bin\ffprobe.exe
```

---

## 3. Environment Verification

Frameza includes a PowerShell verification script that checks all hardware, Python, GPU, CUDA, and FFmpeg components:

```powershell
powershell -ExecutionPolicy Bypass -File .\check-environment.ps1
```

You should see all or mostly green `[PASS]` statuses:
```text
=======================================================
          Frameza Environment Health Check            
=======================================================
[ 1] Python 3.11 Installation         : [PASS] Python 3.11.9
[ 2] Virtual Environment (.venv)      : [PASS] Python 3.11.9 (.venv is healthy)
[ 3] Core Python Packages             : [PASS] fastapi, faster-whisper, mediapipe, opencv, scenedetect, yt-dlp OK
[ 4] FFmpeg Binary                    : [PASS] ffmpeg version N-128889
[ 5] FFprobe Binary                   : [PASS] ffprobe version N-128889
[ 6] Subtitle Engine (libass)         : [PASS] libass is enabled in FFmpeg build
[ 7] Hardware Encoder (NVENC)         : [PASS] h264_nvenc verified
[ 8] NVIDIA GPU Detected              : [PASS] NVIDIA GeForce RTX 5050 Laptop GPU
[ 9] CUDA Driver Compatibility        : [PASS] Driver supports CUDA 12/13 runtime
[10] Faster-Whisper CUDA              : [PASS] CUDA runtime available (1 device(s))
[11] Ollama Installation              : [PASS] Installed
[12] Ollama Background Service        : [PASS] Ollama process is currently running
[13] Ollama Models                    : [PASS] llama3.2:3b is installed and ready
[14] Workspace Directories            : [PASS] output/, cache/, downloads/ initialized
[15] Available Disk Space             : [PASS] Sufficient disk storage available
```

---

## 4. Starting the Application

To launch Frameza:
```bat
run.bat
```
The launcher will start the Ollama background service if needed, boot the FastAPI backend, and open `http://127.0.0.1:7860` in a dedicated chromeless window.

