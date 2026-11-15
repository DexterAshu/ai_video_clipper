# Frameza Documentation & Reports Index

This directory contains all historical architecture documents, stage delivery reports, performance benchmarks, and resource stability audits for Frameza.

---

## Directory Structure

```
docs/
├── stages/                  # Development stage reports (Stage 2 through Stage 8.1)
│   ├── stage-2/             # Stage 2 implementation & verification reports
│   ├── stage-3/             # Stage 3 implementation & verification reports
│   ├── stage-4/             # Stage 4 implementation & verification reports
│   ├── stage-5/             # Stage 5 implementation & verification reports
│   ├── stage-6/             # Stage 6 implementation & verification reports
│   ├── stage-7/             # Stage 7 reports & final audit
│   ├── stage-8/             # Stage 8 dynamic AI director & video quality reports
│   └── stage-8.1/           # Stage 8.1 implementation, performance & test reports
│
├── resource-stability/      # GPU / RAM stability & resource contention fixes
│   ├── FRAMEZA_RESOURCE_CONTENTION_ROOT_CAUSE_REPORT.md
│   ├── FRAMEZA_GPU_RAM_STABILITY_IMPLEMENTATION_REPORT.md
│   ├── FRAMEZA_RESOURCE_STRESS_TEST_REPORT.md
│   └── FRAMEZA_FINAL_RESOURCE_STABILITY_AUDIT.md
│
├── setup/                   # Environment setup, diagnostics & installation guides
│   ├── INSTALLATION.md
│   ├── WINDOWS_SETUP.md
│   ├── TROUBLESHOOTING.md
│   ├── GPU-DIAGNOSTIC.md
│   └── PLATFORMS.md
│
├── audits/                  # General codebase and security audits
│   ├── AUDIT.md
│   └── PROJECT_AUDIT.md
│
└── project/                 # High-level project roadmaps, decisions & research
    ├── DECISIONS.md
    ├── LICENSES.md
    ├── PLAN.md
    ├── PROGRESS.md
    ├── REPORT.md
    ├── RESEARCH.md
    └── UPDATER-STATUS.md
```

---

## Stage Deliverables

| Stage | Path | Key Reports |
| :--- | :--- | :--- |
| **Stage 2** | `docs/stages/stage-2/` | [STAGE_2_REPORT.md](stages/stage-2/STAGE_2_REPORT.md) |
| **Stage 3** | `docs/stages/stage-3/` | [STAGE_3_REPORT.md](stages/stage-3/STAGE_3_REPORT.md) |
| **Stage 4** | `docs/stages/stage-4/` | [STAGE_4_REPORT.md](stages/stage-4/STAGE_4_REPORT.md) |
| **Stage 5** | `docs/stages/stage-5/` | [STAGE_5_REPORT.md](stages/stage-5/STAGE_5_REPORT.md) |
| **Stage 6** | `docs/stages/stage-6/` | [STAGE_6_REPORT.md](stages/stage-6/STAGE_6_REPORT.md) |
| **Stage 7** | `docs/stages/stage-7/` | [STAGE_7_REPORT.md](stages/stage-7/STAGE_7_REPORT.md), [STAGE_7_FINAL_AUDIT.md](stages/stage-7/STAGE_7_FINAL_AUDIT.md) |
| **Stage 8** | `docs/stages/stage-8/` | [FRAMEZA_DYNAMIC_AI_DIRECTOR_REPORT.md](stages/stage-8/FRAMEZA_DYNAMIC_AI_DIRECTOR_REPORT.md)<br>[FRAMEZA_STAGE8_FINAL_VERIFICATION_REPORT.md](stages/stage-8/FRAMEZA_STAGE8_FINAL_VERIFICATION_REPORT.md)<br>[FRAMEZA_VIDEO_QUALITY_FIXES_REPORT.md](stages/stage-8/FRAMEZA_VIDEO_QUALITY_FIXES_REPORT.md)<br>[FRAMEZA_VIDEO_QUALITY_INVESTIGATION.md](stages/stage-8/FRAMEZA_VIDEO_QUALITY_INVESTIGATION.md) |
| **Stage 8.1** | `docs/stages/stage-8.1/` | [FRAMEZA_STAGE8_1_IMPLEMENTATION_REPORT.md](stages/stage-8.1/FRAMEZA_STAGE8_1_IMPLEMENTATION_REPORT.md)<br>[FRAMEZA_STAGE8_1_PERFORMANCE_REPORT.md](stages/stage-8.1/FRAMEZA_STAGE8_1_PERFORMANCE_REPORT.md)<br>[FRAMEZA_STAGE8_1_TEST_REPORT.md](stages/stage-8.1/FRAMEZA_STAGE8_1_TEST_REPORT.md) |

---

## Resource Stability Architecture (RTX 5050 / 16GB RAM)

| Report | Description |
| :--- | :--- |
| [Root Cause Analysis](resource-stability/FRAMEZA_RESOURCE_CONTENTION_ROOT_CAUSE_REPORT.md) | 4-way collision root cause: 10 parallel workers, Ollama concurrency, Whisper CUDA re-entry, WDDM shared memory thrashing. |
| [Implementation Report](resource-stability/FRAMEZA_GPU_RAM_STABILITY_IMPLEMENTATION_REPORT.md) | 3-phase pipeline architecture, model eviction, CPU int8 timing repair fallback, centralized resource coordinator. |
| [Hardware Stress Test](resource-stability/FRAMEZA_RESOURCE_STRESS_TEST_REPORT.md) | Real media hardware validation measuring peak VRAM, RAM load, and 0 concurrent LLM/Whisper operations during render. |
| [Final Verification Audit](resource-stability/FRAMEZA_FINAL_RESOURCE_STABILITY_AUDIT.md) | Independent audit verifying call paths, 10-candidate test pass, memory math reconciliation, and worker concurrency limits. |
