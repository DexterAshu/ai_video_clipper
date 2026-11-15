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
  2. Ensure model `qwen3.5:4b` or `llama3.2:3b` is pulled:
     ```cmd
     ollama pull qwen3.5:4b
     ollama pull llama3.2:3b
     ```

### Issue: GPU Out of Memory (CUDA OOM) with Ollama + Whisper + NVENC
- **Symptoms:** Whisper or NVENC crashes with CUDA out of memory on 8GB laptops.
- **Cause:** Large models default to 262,144 context length if unconstrained, consuming up to 14GB of memory.
- **Solution:** Frameza automatically bounds Ollama to `num_ctx: 8192` (3.7GB VRAM) and automatically triggers `keep_alive: 0` to unload LLM weights before Faster-Whisper and NVENC run. You can also click **"Free GPU VRAM (Unload)"** in Settings.
- **Model Recommendation:** Use models marked `[BEST RECOMMENDED]` (`qwen3.5:4b`) or `[LIGHTWEIGHT / FAST]` (`llama3.2:3b`). Avoid models marked `[VERY HEAVY / NOT RECOMMENDED]` (`gemma4:26b`) which exceed 8GB VRAM.

### Issue: "Cannot switch models while a processing job is actively running" (HTTP 409)
- **Cause:** Switching AI models mid-job can corrupt highlight detection prompts or cause VRAM competition.
- **Solution:** Wait for the active video clipping job to complete before switching models in Settings, or cancel the active run from the progress screen.

---

## 4. Editing Studio & Reframing

### Issue: Multi-person conversation cuts off one of the speakers
- **Cause:** In wide 16:9 shots, participants sitting far apart may not fit in a 9:16 vertical crop.
- **Solution:** 
  1. Frameza automatically detects 2-shot conversations: if participants are within 75% of the crop width, it centers between them.
  2. If they are farther apart, the active speaker is tracked. In the **Advanced Clip Editing Studio** (`#/edit`), use the **Horizontal Pan** slider to manually nudge framing toward either speaker, or zoom out slightly to capture both.

### Issue: Camera jitter when speaker is stationary
- **Solution:** Frameza includes a stationary subject deadband ($\pm 4\text{px}$) to suppress micro-movement when subjects are seated. If manual framing is desired, adjust coordinates in the Clip Editing Studio.

---

## 5. Subtitles & Audio

### Issue: Background noise, mic hum or low rumble in exported clips
- **Solution:** Frameza's Stage 7 professional audio pipeline includes automatic speech enhancement:
  - 80Hz highpass filter to eliminate HVAC hum and mic handling thumps.
  - Adaptive FFT noise reduction (`afftdn=nf=-25`) to drop ambient room noise.
  - Dialogue compression (`acompressor`) and -14 LUFS loudness normalization.
  You can toggle this on or off per clip in the Clip Editing Studio under **5. Professional Audio**.

