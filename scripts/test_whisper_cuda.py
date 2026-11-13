"""Verify Faster-Whisper CUDA transcription on NVIDIA GPU"""
import os
import sys
import wave
import struct
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Register CUDA DLLs from nvidia-cublas and nvidia-cudnn
import transcribe
transcribe._register_cuda_dll_dirs()

import ctranslate2
import faster_whisper

print(f"ctranslate2 version: {ctranslate2.__version__}")
print(f"CUDA devices: {ctranslate2.get_cuda_device_count()}")
if ctranslate2.get_cuda_device_count() == 0:
    print("ERROR: No CUDA devices found by ctranslate2!", file=sys.stderr)
    sys.exit(1)

# Generate a small 2-second test audio file
test_wav = Path("cache/test_audio.wav")
test_wav.parent.mkdir(parents=True, exist_ok=True)
sample_rate = 16000
duration = 2.0
n_samples = int(sample_rate * duration)
with wave.open(str(test_wav), "w") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sample_rate)
    for i in range(n_samples):
        # 440 Hz tone
        val = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
        wf.writeframes(struct.pack("<h", val))

print(f"Generated test audio: {test_wav}")
print("Loading faster-whisper model on CUDA (device='cuda', compute_type='float16')...")

# Test with 'tiny' first for fast download, or 'small' as requested
model = faster_whisper.WhisperModel("tiny", device="cuda", compute_type="float16", download_root="cache/models")
print("Model loaded successfully on CUDA!")

segments, info = model.transcribe(str(test_wav), beam_size=1)
seg_list = list(segments)
print(f"Transcription complete! Detected language: {info.language} (p={info.language_probability:.2f})")
print(f"Segments produced: {len(seg_list)}")
print("SUCCESS: Faster-Whisper CUDA transcription verified!")
