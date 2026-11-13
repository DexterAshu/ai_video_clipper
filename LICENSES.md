# Third-Party Licenses and Attributions

Frameza incorporates and interacts with various open-source libraries, media assets, tools, and AI models. This document summarizes their licensing terms and required attributions.

---

## 1. Application Code
- **License:** MIT License
- **Copyright:** (c) 2026 DexterAshu. All rights reserved.

---

## 2. Bundled & Downloaded Fonts
- **Font Family:** Montserrat (Black, Bold, ExtraBold, Regular)
- **Author:** Julieta Ulanovsky, Sol Matas, Juan Pablo del Peral, Jacques Le Bailly
- **License:** SIL Open Font License, Version 1.1 (OFL-1.1)
- **License Text:** See `assets/fonts/OFL.txt`

---

## 3. Royalty-Free Background Music
Background music tracks in `assets/music/manifest.json` are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0):
- **Tracks by Kevin MacLeod (incompetech.com):**
  - *"Carefree"* by Kevin MacLeod (CC BY 4.0)
  - *"Sneaky Snitch"* by Kevin MacLeod (CC BY 4.0)
  - *"Inspired"* by Kevin MacLeod (CC BY 4.0)
  - *"Local Forecast - Elevator"* by Kevin MacLeod (CC BY 4.0)
  - *"Cipher"* by Kevin MacLeod (CC BY 4.0)
- **Attribution Policy:** When background music is muxed into a generated clip, Frameza automatically appends the corresponding CC BY 4.0 attribution notice to the clip's exported description text.

---

## 4. Video & Audio Processing Binaries
- **FFmpeg & FFprobe:**
  - Portable Windows builds (gyan.dev / BtbN)
  - Licensed under GNU General Public License v3.0 (GPLv3) / LGPLv2.1
  - Features compiled: `--enable-libass`, `--enable-nvenc`, `--enable-libx264`, `--enable-libvpx`
  - Website: https://ffmpeg.org

---

## 5. Local AI & Machine Learning Models
- **Faster-Whisper & CTranslate2:**
  - **Faster-Whisper:** MIT License (SYSTRAN)
  - **CTranslate2:** MIT License (SYSTRAN)
  - **OpenAI Whisper Models:** MIT License (OpenAI)
- **MediaPipe:**
  - Apache License, Version 2.0 (Google LLC)
  - Face Mesh & Detection models included via MediaPipe Python wheel
- **Ollama & Llama 3.2:**
  - **Ollama Runtime:** MIT License
  - **Llama 3.2 (Meta):** Meta Llama 3.2 Community License Agreement
  - Website: https://ollama.com

---

## 6. Core Python Libraries
| Library | License | Primary Purpose |
| :--- | :--- | :--- |
| **FastAPI** | MIT | Asynchronous REST and WebSocket API server |
| **Uvicorn** | BSD-3-Clause | ASGI web server implementation |
| **OpenCV (`cv2`)** | Apache 2.0 | Frame manipulation and video inspection |
| **PySceneDetect** | BSD-3-Clause | Video scene transition detection |
| **yt-dlp** | The Unlicense | YouTube and web video stream downloader |
| **PyYAML** | MIT | Configuration file parsing |
| **Pydantic** | MIT | Data validation and settings management |
| **NVIDIA cuBLAS & cuDNN cu12** | NVIDIA Software License | Accelerated CUDA tensor operations |

