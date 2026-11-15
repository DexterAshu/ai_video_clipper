"""Centralized GPU and System Resource Coordinator for Frameza.

Provides:
- Lightweight Windows/Python resource monitoring (RAM via GlobalMemoryStatusEx, GPU via nvidia-smi)
- Centralized synchronization gates for CUDA initialization, LLM inference, and render workers
- Resource pressure checks against configurable safety thresholds (RAM, VRAM)
- Active operation tracking (workers, LLM requests, Whisper transcription, FFmpeg processes)
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from config import load_config
from logutil import get_logger

log = get_logger("resources")

# ---------------------------------------------------------------------------
# Windows Memory Statistics Structure
# ---------------------------------------------------------------------------
class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass
class SystemSnapshot:
    ram_total_mb: float = 0.0
    ram_avail_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_percent: float = 0.0
    pagefile_total_mb: float = 0.0
    pagefile_avail_mb: float = 0.0
    gpu_total_mb: float = 0.0
    gpu_used_mb: float = 0.0
    gpu_free_mb: float = 0.0
    gpu_percent: float = 0.0
    gpu_util_percent: float = 0.0
    gpu_name: str = ""
    active_workers: int = 0
    active_llm_requests: int = 0
    active_transcriptions: int = 0
    active_ffmpeg: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ram_total_mb": round(self.ram_total_mb, 1),
            "ram_avail_mb": round(self.ram_avail_mb, 1),
            "ram_used_mb": round(self.ram_used_mb, 1),
            "ram_percent": round(self.ram_percent, 1),
            "pagefile_total_mb": round(self.pagefile_total_mb, 1),
            "pagefile_avail_mb": round(self.pagefile_avail_mb, 1),
            "gpu_total_mb": round(self.gpu_total_mb, 1),
            "gpu_used_mb": round(self.gpu_used_mb, 1),
            "gpu_free_mb": round(self.gpu_free_mb, 1),
            "gpu_percent": round(self.gpu_percent, 1),
            "gpu_util_percent": round(self.gpu_util_percent, 1),
            "gpu_name": self.gpu_name,
            "active_workers": self.active_workers,
            "active_llm_requests": self.active_llm_requests,
            "active_transcriptions": self.active_transcriptions,
            "active_ffmpeg": self.active_ffmpeg,
        }


class ResourceCoordinator:
    """Singleton coordinator governing concurrent access to GPU, RAM and AI models."""

    _instance: ResourceCoordinator | None = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> ResourceCoordinator:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self.llm_lock = threading.RLock()
        self.cuda_lock = threading.RLock()
        self.transcribe_lock = threading.RLock()
        self.worker_lock = threading.RLock()

        self._active_workers = 0
        self._active_llm_requests = 0
        self._active_transcriptions = 0
        self._active_ffmpeg = 0

        self._gpu_cache: dict[str, Any] = {}
        self._gpu_cache_time = 0.0
        self._gpu_cache_ttl = 1.0  # seconds

    # --- Metrics Gathering ---

    def get_ram_status(self) -> dict[str, float]:
        """Query physical and pagefile RAM usage via Windows GlobalMemoryStatusEx."""
        if os.name == "nt":
            try:
                stat = _MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    total_mb = stat.ullTotalPhys / (1024 * 1024)
                    avail_mb = stat.ullAvailPhys / (1024 * 1024)
                    used_mb = total_mb - avail_mb
                    pct = float(stat.dwMemoryLoad)
                    pf_total_mb = stat.ullTotalPageFile / (1024 * 1024)
                    pf_avail_mb = stat.ullAvailPageFile / (1024 * 1024)
                    return {
                        "total_mb": total_mb,
                        "avail_mb": avail_mb,
                        "used_mb": used_mb,
                        "percent": pct,
                        "pagefile_total_mb": pf_total_mb,
                        "pagefile_avail_mb": pf_avail_mb,
                    }
            except Exception as e:
                log.debug("GlobalMemoryStatusEx failed: %s", e)

        # Fallback / non-Windows dummy
        return {
            "total_mb": 16384.0,
            "avail_mb": 8192.0,
            "used_mb": 8192.0,
            "percent": 50.0,
            "pagefile_total_mb": 16384.0,
            "pagefile_avail_mb": 8192.0,
        }

    def get_gpu_status(self, force_refresh: bool = False) -> dict[str, Any]:
        """Query NVIDIA dedicated VRAM and GPU utilization via nvidia-smi (cached 1.0s)."""
        now = time.time()
        if not force_refresh and (now - self._gpu_cache_time < self._gpu_cache_ttl) and self._gpu_cache:
            return dict(self._gpu_cache)

        result: dict[str, Any] = {
            "total_mb": 0.0,
            "used_mb": 0.0,
            "free_mb": 0.0,
            "percent": 0.0,
            "util_percent": 0.0,
            "name": "NVIDIA GPU",
            "available": False,
        }

        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free,utilization.gpu,name",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2.0
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
                if len(parts) >= 4:
                    tot = float(parts[0])
                    used = float(parts[1])
                    free = float(parts[2])
                    util = float(parts[3])
                    name = parts[4] if len(parts) > 4 else "NVIDIA GPU"
                    pct = (used / tot * 100.0) if tot > 0 else 0.0
                    result = {
                        "total_mb": tot,
                        "used_mb": used,
                        "free_mb": free,
                        "percent": pct,
                        "util_percent": util,
                        "name": name,
                        "available": True,
                    }
        except Exception as e:
            log.debug("nvidia-smi query skipped or failed: %s", e)

        self._gpu_cache = result
        self._gpu_cache_time = now
        return dict(result)

    def snapshot(self) -> SystemSnapshot:
        """Capture an instant diagnostic snapshot of system and GPU health."""
        ram = self.get_ram_status()
        gpu = self.get_gpu_status()
        with self.worker_lock:
            w = self._active_workers
            llm_cnt = self._active_llm_requests
            trans = self._active_transcriptions
            ff = self._active_ffmpeg

        return SystemSnapshot(
            ram_total_mb=ram["total_mb"],
            ram_avail_mb=ram["avail_mb"],
            ram_used_mb=ram["used_mb"],
            ram_percent=ram["percent"],
            pagefile_total_mb=ram["pagefile_total_mb"],
            pagefile_avail_mb=ram["pagefile_avail_mb"],
            gpu_total_mb=gpu["total_mb"],
            gpu_used_mb=gpu["used_mb"],
            gpu_free_mb=gpu["free_mb"],
            gpu_percent=gpu["percent"],
            gpu_util_percent=gpu["util_percent"],
            gpu_name=gpu["name"],
            active_workers=w,
            active_llm_requests=llm_cnt,
            active_transcriptions=trans,
            active_ffmpeg=ff,
        )

    # --- Safety & Pressure Checks ---

    def check_resource_pressure(self, cfg: dict | None = None) -> dict[str, Any]:
        """Check if RAM or VRAM are approaching critical limits."""
        cfg = cfg or load_config()
        res_cfg = cfg.get("resources", {})
        max_ram_pct = float(res_cfg.get("max_system_ram_percent", 85.0))
        max_gpu_pct = float(res_cfg.get("max_gpu_memory_percent", 90.0))

        snap = self.snapshot()
        reasons: list[str] = []
        is_critical = False
        is_warning = False

        # RAM check
        if snap.ram_percent >= max_ram_pct:
            is_critical = True
            reasons.append(f"RAM at {snap.ram_percent:.1f}% (critical threshold: {max_ram_pct:.0f}%)")
        elif snap.ram_percent >= (max_ram_pct - 10.0):
            is_warning = True
            reasons.append(f"RAM at {snap.ram_percent:.1f}% (approaching threshold)")

        # GPU VRAM check (if GPU detected)
        if snap.gpu_total_mb > 0:
            if snap.gpu_percent >= max_gpu_pct:
                is_critical = True
                reasons.append(f"Dedicated VRAM at {snap.gpu_percent:.1f}% "
                               f"({snap.gpu_used_mb:.0f}/{snap.gpu_total_mb:.0f}MB, critical threshold: {max_gpu_pct:.0f}%)")
            elif snap.gpu_percent >= (max_gpu_pct - 15.0):
                is_warning = True
                reasons.append(f"Dedicated VRAM at {snap.gpu_percent:.1f}% (warning threshold)")

        safe = not is_critical
        return {
            "safe": safe,
            "warning": is_warning,
            "critical": is_critical,
            "reasons": reasons,
            "snapshot": snap.to_dict(),
        }

    # --- Operation Tracking Context Managers ---

    def track_worker(self):
        """Context manager tracking active render worker concurrency."""
        coord = self

        class _WorkerContext:
            def __enter__(self):
                with coord.worker_lock:
                    coord._active_workers += 1
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                with coord.worker_lock:
                    coord._active_workers = max(0, coord._active_workers - 1)

        return _WorkerContext()

    def track_llm(self):
        """Context manager serializing and tracking active LLM inference requests."""
        coord = self

        class _LLMContext:
            def __enter__(self):
                coord.llm_lock.acquire()
                with coord.worker_lock:
                    coord._active_llm_requests += 1
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                with coord.worker_lock:
                    coord._active_llm_requests = max(0, coord._active_llm_requests - 1)
                coord.llm_lock.release()

        return _LLMContext()

    def track_transcription(self):
        """Context manager tracking active transcription operations."""
        coord = self

        class _TransContext:
            def __enter__(self):
                coord.transcribe_lock.acquire()
                with coord.worker_lock:
                    coord._active_transcriptions += 1
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                with coord.worker_lock:
                    coord._active_transcriptions = max(0, coord._active_transcriptions - 1)
                coord.transcribe_lock.release()

        return _TransContext()

    def track_ffmpeg(self):
        """Context manager tracking active FFmpeg sub-processes."""
        coord = self

        class _FFmpegContext:
            def __enter__(self):
                with coord.worker_lock:
                    coord._active_ffmpeg += 1
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                with coord.worker_lock:
                    coord._active_ffmpeg = max(0, coord._active_ffmpeg - 1)

        return _FFmpegContext()


# Global module singleton
coordinator = ResourceCoordinator()
