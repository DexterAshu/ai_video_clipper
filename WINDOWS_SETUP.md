# Frameza Windows Setup Guide

This guide details the complete procedure for configuring and running Frameza on **Windows 11 64-bit** with local AI processing, NVIDIA GPU acceleration, and zero paid API dependencies.

---

## 1. System Requirements & Hardware Specification

- **Operating System:** Windows 11 Home or Pro (64-bit).
- **Processor:** Intel Core i7-13650HX or equivalent multi-core CPU.
- **Graphics Card:** NVIDIA GeForce RTX 5050 Laptop GPU (8GB VRAM, Blackwell architecture) or any NVIDIA RTX series GPU.
- **Driver:** NVIDIA Game Ready / Studio Driver version 610.74 or later.
- **Memory:** 16GB DDR5 RAM minimum.
- **Storage:** Solid State Drive (SSD) with at least 15GB of free space.

---

## 2. Python Environment Setup

Frameza strictly enforces **Python 3.11** to ensure full binary wheel compatibility with MediaPipe and CTranslate2.

### A. Python 3.11 Installation
Python 3.11.9 is installed at:
```
C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe
```
> [!NOTE]
> On systems where `py -3.11` fails with `"No Installed Pythons Found!"`, always use the explicit executable path.

### B. Creating the Virtual Environment
Create the project-local `.venv` using the explicit Python 3.11 binary:
```powershell
& "C:\Users\getaa\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
```

### C. Installing Project Dependencies
Upgrade `pip` and install all repository packages:
```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

---

## 3. Local FFmpeg Deployment (NVENC & Libass)

Frameza requires a static build of FFmpeg containing `libass` (for subtitle burning) and `ffnvcodec` (for NVENC hardware encoding).

### A. Binary Placement
Deploy the static Windows build directly into the repository:
```
tools/ffmpeg/bin/ffmpeg.exe
tools/ffmpeg/bin/ffprobe.exe
```

### B. Verification
Test that both binaries and required components are present:
```powershell
.\tools\ffmpeg\bin\ffmpeg.exe -version
.\tools\ffmpeg\bin\ffprobe.exe -version
.\tools\ffmpeg\bin\ffmpeg.exe -encoders | Select-String "nvenc"
```
You should see:
- `av1_nvenc`
- `h264_nvenc`
- `hevc_nvenc`

---

## 4. NVIDIA GPU & Faster-Whisper CUDA Setup

### A. NVIDIA Driver & NVENC Hardware Encoding
- **GPU Architecture:** NVIDIA Blackwell (RTX 5050).
- **Encoder:** 9th Gen NVENC (`h264_nvenc`).
- **Resolution Constraint:** Modern Blackwell NVENC requires frame dimensions $\ge 145 \times 145$. The probe in `ffutil.py` uses $256 \times 256$ to guarantee flawless hardware initialization.

### B. Faster-Whisper CUDA Runtime
In `requirements.txt`, the CUDA 12 runtime packages are installed inside `.venv`:
- `ctranslate2==4.8.1`
- `nvidia-cublas-cu12==12.9.2.10`
- `nvidia-cudnn-cu12==9.24.0.43`

`transcribe.py` automatically registers the DLL directories on Windows startup via:
```python
transcribe._register_cuda_dll_dirs()
```
Verify GPU acceleration using the standalone diagnostic tool:
```powershell
.\.venv\Scripts\python.exe check_gpu.py
```
Expected Output:
```
Frameza GPU check
========================================
[1] NVIDIA driver: NVIDIA GeForce RTX 5050 Laptop GPU, 610.74
[2] ffmpeg / NVENC: SUCCESS (h264_nvenc)
[3] faster-whisper / CUDA: 4.8.1, cuda_devices=1
========================================
VERDICT
  GPU encode:    WORKING
  GPU transcribe: WORKING
```

---

## 5. Local LLM Setup (Ollama)

Frameza uses Ollama for local, privacy-respecting, and free highlight scoring and metadata generation.

### A. Ollama Installation
Ollama is installed at:
```
C:\Users\getaa\AppData\Local\Programs\Ollama\ollama.exe
```

### B. Starting Ollama Service
Ensure the Ollama background service is running:
```powershell
ollama serve
```

### C. Installing the Model
Pull the lightweight 3B model (optimal for 8GB VRAM and 16GB RAM):
```powershell
ollama pull llama3.2:3b
```
Verify the model is ready:
```powershell
ollama list
```

---

## 6. Configuration Overrides (`config.local.yaml`)

Frameza preserves `config.yaml` by applying local machine overrides via `config.local.yaml`:

```yaml
# Local Frameza configuration overrides
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

## 7. Starting the Application

### Option A: Using the Windows Launcher (Recommended)
Simply double-click `run.bat` or run:
```bat
run.bat
```
The launcher will:
1. Detect and configure local FFmpeg from `tools\ffmpeg\bin\`.
2. Check `.venv` and verify Python 3.11.
3. Check and start the Ollama background service if needed.
4. Launch the FastAPI server and open the UI at `http://127.0.0.1:7860`.

### Option B: Manual Command Line
```powershell
.\.venv\Scripts\python.exe -m server.main
```

---

## 8. Environment Verification Script

Run the automated health check at any time:
```powershell
powershell -ExecutionPolicy Bypass -File .\check-environment.ps1
```
This tests all 14 hardware, Python, FFmpeg, CUDA, Faster-Whisper, and Ollama components.

---

## 9. Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| `py -3.11` fails with "No Installed Pythons Found!" | Windows py launcher lacks HKCU registration | Use `run.bat` (which resolves the explicit Python 3.11 path) or call `.\.venv\Scripts\python.exe` directly. |
| `NVENC init failed: invalid param (8)` | Frame dimension too small for Blackwell GPU | Ensure `ffutil.py` uses `color=black:s=256x256` for NVENC probe (fixed in current branch). |
| `ConnectError: getaddrinfo failed` during Whisper | Whisper model weights not cached locally | Model downloaded and cached in `cache/models/`. Keep `whisper.model_override: small`. |
| Ollama connection refused on `11434` | Ollama background process is not running | Run `ollama serve` or launch via `run.bat` which starts it automatically. |

