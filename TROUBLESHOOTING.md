# Frameza Troubleshooting Guide

This guide addresses common questions, error messages, and solutions when running Frameza on Windows.

---

## 1. Startup & Environment Issues

### Issue: `py -3.11` outputs "No Installed Pythons Found!"
- **Cause:** The generic Windows Python launcher `py.exe` sometimes fails to discover Microsoft Store or per-user local app directory installations.
- **Solution:** `run.bat` automatically searches the explicit path:
  `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`.
  If your Python 3.11 is in a custom folder, create the `.venv` manually:
  ```powershell
  & "C:\Path\To\Python311\python.exe" -m venv .venv
  .\.venv\Scripts\pip.exe install -r requirements.txt
  ```

### Issue: Port 7860 is already in use (`WinError 10048`)
- **Cause:** A previously opened Frameza terminal or process is still running and occupying port 7860.
- **Solution:**
  1. Simply navigate to [http://127.0.0.1:7860](http://127.0.0.1:7860) in your web browser.
  2. If the previous process crashed or hung, kill it via PowerShell:
     ```powershell
     Get-Process python | Where-Object { $_.MainWindowTitle -match "Frameza" } | Stop-Process -Force
     ```
  3. Or identify the PID using netstat:
     ```cmd
     netstat -ano | findstr :7860
     taskkill /F /PID <PID_NUMBER>
     ```

### Issue: `FFmpeg not found!` on startup
- **Cause:** FFmpeg is missing from both `tools/ffmpeg/bin/` and your system `PATH`.
- **Solution:** Run the automatic deploy script to fetch the portable build:
  ```powershell
  .\.venv\Scripts\python.exe scripts/deploy_ffmpeg.py
  ```

---

## 2. NVIDIA GPU & Hardware Acceleration Issues

### Issue: Faster-Whisper falls back to CPU
- **Symptoms:** Transcription runs slower, or log mentions `CUDA runtime not available`.
- **Cause:** Missing CUDA 12 runtime DLLs in the Python environment.
- **Solution:** Ensure `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` are installed in `.venv`:
  ```powershell
  .\.venv\Scripts\pip.exe install nvidia-cublas-cu12 nvidia-cudnn-cu12
  ```
  `transcribe.py` automatically injects their `site-packages/nvidia/*/bin` folders via `os.add_dll_directory()`.

### Issue: NVENC error `Minimum dimension requirement not met`
- **Cause:** Modern NVIDIA NVENC architectures (including Blackwell RTX 50 series) require video frames to be at least $145 \times 145$ pixels.
- **Solution:** Test videos and clips should always be at least $256 \times 256$ pixels. Frameza's default outputs (1080p and 720p) exceed this minimum.

---

## 3. Ollama & Local AI Issues

### Issue: Ollama service warning / Connection refused (`127.0.0.1:11434`)
- **Symptoms:** The UI log shows `Ollama connection error; falling back to rule-based highlight scoring`.
- **Solution:**
  1. Start the Ollama background service in a separate terminal:
     ```cmd
     ollama serve
     ```
  2. Ensure model `llama3.2:3b` is pulled:
     ```cmd
     ollama run llama3.2:3b
     ```

### Issue: High RAM or VRAM usage during LLM generation
- **Cause:** Running multiple heavy applications concurrently with an 8GB VRAM GPU.
- **Solution:** `llama3.2:3b` requires only ~2.2 GB VRAM. Close other heavy GPU tasks (games, 3D rendering) while running Frameza for optimal processing speed.

---

## 4. Video Ingestion & YouTube Issues

### Issue: YouTube download fails or is throttled
- **Symptoms:** `yt-dlp` returns `HTTP Error 429: Too Many Requests` or slow download speeds.
- **Solution:**
  1. YouTube frequently updates its bot detection algorithms. Update `yt-dlp` to the latest release:
     ```powershell
     .\.venv\Scripts\pip.exe install --upgrade yt-dlp
     ```
  2. Alternatively, download the video manually and drag-and-drop the local MP4 file directly into the Frameza UI.

### Issue: Local video upload rejected (`Unsupported format` or `422 Unprocessable`)
- **Cause:** File is empty (0 bytes) or has an unsupported container format.
- **Solution:** Ensure the video is a valid MP4, MKV, MOV, WebM, or AVI file with non-zero size.

---

## 5. Subtitles & Audio

### Issue: Subtitle text has missing characters or symbols
- **Cause:** Custom fonts missing specific unicode glyphs.
- **Solution:** Use the default Montserrat font family (included in `assets/fonts/`), which supports full Latin, extended Latin, and common international punctuation.

### Issue: Background music is too loud or quiet
- **Solution:** In the web UI under **Audio & Music**, adjust the **Music Volume** slider (default is `-18 dB`). The automatic sidechain ducking filter automatically lowers music volume by 12 dB whenever speech is detected.

