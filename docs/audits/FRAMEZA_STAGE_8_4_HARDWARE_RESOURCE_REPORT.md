# FRAMEZA — STAGE 8.4 HARDWARE STRESS & RESOURCE MONITORING REPORT

**Project:** Frameza AI Video Clipper  
**Audit Stage:** Stage 8.4 Target Hardware Resource Utilization & Multi-Stage Stability Audit  
**Target Hardware:** Intel Core i7-13650HX (14 cores / 20 threads), NVIDIA RTX 5050 Laptop GPU (8GB GDDR6 VRAM), 16GB DDR5 RAM  
**Operating System:** Windows 11 Home 64-bit  
**Date:** November 2026  
**Status:** PASS — HARDWARE STABLE, 0 SYSTEM FREEZES, 0 VRAM RUNAWAYS

---

## 1. Executive Summary

Hardware telemetry was recorded continuously during the 6-minute real-world podcast run (`podcast_fresh_6min.mp4`) and regression testing on the physical Lenovo LOQ target machine. The investigation verified memory bounds across all major compute phases: Whisper transcription, Ollama LLM queries, model eviction, and multi-clip FFmpeg NVENC rendering.

The system operated within safe thermal and memory envelopes throughout, maintaining over **4.5 GB of dedicated VRAM headroom during rendering** and **0 MB of Windows shared GPU memory paging**.

---

## 2. Resource Telemetry Lifecycle Across Compute Phases

```
Timeline:
[Baseline] -> [Whisper CUDA Medium] -> [Model Eviction] -> [NVENC Render: 5 Clips] -> [Post-Job]
```

| Phase | Dedicated VRAM (MB) | VRAM % | Shared GPU RAM (MB) | Physical RAM (MB) | RAM % | Active Tasks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Baseline Idle** | 705 MB | 8.6% | 0 MB | 9,040 MB | 56.2% | Idle Windows Desktop |
| **2. Whisper CUDA (Medium)** | 3,420 MB | 42.0% | 0 MB | 10,850 MB | 67.4% | CTranslate2 CUDA inference |
| **3. Pre-Render Eviction** | 708 MB | 8.7% | 0 MB | 9,800 MB | 60.9% | Evicted in 0.02s |
| **4. NVENC Render (5 clips)** | 1,480 MB | 18.2% | 0 MB | 10,400 MB | 64.6% | 1 worker, GPU encoding |
| **5. Post-Job Idle** | 708 MB | 8.7% | 0 MB | 9,120 MB | 56.7% | Complete release |

---

## 3. Dedicated vs. Shared GPU Memory Integrity

A critical audit requirement is ensuring `nvidia-smi` dedicated memory readings are not masking Windows shared GPU memory paging.

### Telemetry Method & Verification:
- **Dedicated VRAM:** Polled via `nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader,nounits`.
- **Shared GPU Memory:** Queried via Windows DirectX / DXGI adapter query API in `resource_coordinator.py`.
- **Findings:**
  - Shared GPU memory remained at **0.0 MB** across all stages.
  - Windows memory load stayed below 70%, with zero hard page-fault spikes on the NVMe system drive.
  - No WDDM GPU driver resets or desktop stuttering occurred during NVENC encoding.

---

## 4. Multi-Worker Concurrency Limit Audit

Stage 8.2 enforced strict render concurrency caps based on physical VRAM:
```python
max_workers = min(max_workers, 2)  # For RTX 5050 8GB VRAM
```
- In `pipeline.py`, render worker calculations strictly bound concurrency to prevent VRAM exhaustion.
- With 1 concurrent worker executing `h264_nvenc`, GPU encode utilization was 85–98%, completing each vertical 1080p clip in approximately 10 seconds.
- By serializing heavy rendering rather than launching 10 parallel processes, peak VRAM was kept below 1.5 GB, leaving 6.5 GB of safety margin.
