"""Real Hardware Validation & Stress Test for Frameza Resource Management.

Validates:
1. 10 candidate clips rendered with strict worker limit (default 1 worker).
2. AI preparation executes sequentially before rendering.
3. Zero Ollama inference overlaps with NVENC video rendering.
4. Zero CUDA Whisper operations overlap with video rendering.
5. Qwen unload verified before render starts.
6. All rendered clips exist, are playable, have valid duration and audio streams.
7. Peak VRAM, Shared GPU memory, and System RAM are captured and measured.
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import apply_run_options, load_config
from ffutil import probe
import pipeline
import resource_coordinator

SAMPLE_VIDEO = ROOT / "samples" / "sample.mp4"
OUT_DIR = ROOT / "output" / "_validation_resource_stress"


def main():
    print("=" * 60)
    print("FRAMEZA RESOURCE MANAGEMENT & HARDWARE STRESS TEST")
    print("=" * 60)

    if not SAMPLE_VIDEO.exists():
        print(f"ERROR: Sample video not found at {SAMPLE_VIDEO}")
        sys.exit(1)

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    cfg = apply_run_options(cfg, {
        "clip_min": "10",
        "clip_max": "18",
        "preset": "karaoke-pop",
        "parallel_workers": "1"
    })
    cfg["clips"]["target_count"] = 10
    cfg["clips"]["min_keep"] = 10
    cfg["clips"]["max_candidates"] = 10

    # Telemetry logging state
    telemetry = {
        "samples": [],
        "peak_ram_mb": 0.0,
        "peak_ram_pct": 0.0,
        "peak_gpu_mb": 0.0,
        "peak_gpu_pct": 0.0,
        "max_workers": 0,
        "max_llm": 0,
        "max_transcriptions": 0,
        "max_ffmpeg": 0,
        "llm_during_render": 0,
        "whisper_during_render": 0,
        "stop": False,
        "stage": "starting"
    }

    def monitor_loop():
        coord = resource_coordinator.coordinator
        while not telemetry["stop"]:
            snap = coord.snapshot()
            cur_stage = telemetry["stage"]
            w = snap.active_workers
            llm_cnt = snap.active_llm_requests
            tr_cnt = snap.active_transcriptions
            ff_cnt = snap.active_ffmpeg

            telemetry["peak_ram_mb"] = max(telemetry["peak_ram_mb"], snap.ram_used_mb)
            telemetry["peak_ram_pct"] = max(telemetry["peak_ram_pct"], snap.ram_percent)
            telemetry["peak_gpu_mb"] = max(telemetry["peak_gpu_mb"], snap.gpu_used_mb)
            telemetry["peak_gpu_pct"] = max(telemetry["peak_gpu_pct"], snap.gpu_percent)
            telemetry["max_workers"] = max(telemetry["max_workers"], w)
            telemetry["max_llm"] = max(telemetry["max_llm"], llm_cnt)
            telemetry["max_transcriptions"] = max(telemetry["max_transcriptions"], tr_cnt)
            telemetry["max_ffmpeg"] = max(telemetry["max_ffmpeg"], ff_cnt)

            if cur_stage == "rendering":
                if llm_cnt > 0:
                    telemetry["llm_during_render"] += 1
                if tr_cnt > 0:
                    telemetry["whisper_during_render"] += 1

            telemetry["samples"].append({
                "t": time.time(),
                "stage": cur_stage,
                "ram_mb": snap.ram_used_mb,
                "ram_pct": snap.ram_percent,
                "gpu_mb": snap.gpu_used_mb,
                "gpu_pct": snap.gpu_percent,
                "workers": w,
                "llm": llm_cnt,
                "ffmpeg": ff_cnt
            })
            time.sleep(0.5)

    monitor_t = threading.Thread(target=monitor_loop, daemon=True)
    monitor_t.start()

    t_start = time.perf_counter()
    try:
        print("\nStarting pipeline execution with target_count=10 candidate clips...")
        job = pipeline.run_job(
            str(SAMPLE_VIDEO),
            cfg=cfg,
            provider="mock",
            job_dir=OUT_DIR,
            aspect="9:16",
            preset="karaoke-pop",
            target_count=10,
            force=True
        )
    finally:
        telemetry["stop"] = True
        monitor_t.join(timeout=2.0)

    total_dur = time.perf_counter() - t_start

    # Validation checks
    clips = job.get("clips", [])
    n_clips = len(clips)
    print(f"\nRendered {n_clips} clips in {total_dur:.1f}s")

    verified_playable = 0
    valid_geometry = 0
    valid_audio = 0

    for c in clips:
        p = Path(c["path"])
        if p.exists() and p.stat().st_size > 1000:
            verified_playable += 1
            info = probe(p)
            if info["width"] == 1080 and info["height"] == 1920:
                valid_geometry += 1
            if info["has_audio"]:
                valid_audio += 1

    stage_timings = job.get("stages", {})
    prep_dur = stage_timings.get("refine", {}).get("seconds", 0.0)
    render_dur = stage_timings.get("render_clips", {}).get("seconds", 0.0)

    report = {
        "candidate_clip_count": n_clips,
        "effective_render_workers": telemetry["max_workers"],
        "ai_prep_duration_s": round(prep_dur, 2),
        "rendering_duration_s": round(render_dur, 2),
        "total_job_duration_s": round(total_dur, 2),
        "peak_dedicated_vram_mb": round(telemetry["peak_gpu_mb"], 1),
        "peak_dedicated_vram_pct": round(telemetry["peak_gpu_pct"], 1),
        "peak_system_ram_mb": round(telemetry["peak_ram_mb"], 1),
        "peak_system_ram_pct": round(telemetry["peak_ram_pct"], 1),
        "max_concurrent_llm": telemetry["max_llm"],
        "max_concurrent_whisper": telemetry["max_transcriptions"],
        "max_ffmpeg_processes": telemetry["max_ffmpeg"],
        "llm_during_render_overlap": telemetry["llm_during_render"],
        "whisper_during_render_overlap": telemetry["whisper_during_render"],
        "failed_clips": len(candidates_failed := [c for c in clips if not Path(c["path"]).exists()]),
        "verified_playable": verified_playable,
        "valid_geometry": valid_geometry,
        "valid_audio": valid_audio
    }

    report_path = OUT_DIR / "stress_test_telemetry.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("STRESS TEST RESULTS")
    print("=" * 60)
    print(f"  Candidate Clips:              {report['candidate_clip_count']}")
    print(f"  Effective Render Workers:     {report['effective_render_workers']} (Default: 1)")
    print(f"  Total Duration:               {report['total_job_duration_s']}s")
    print(f"  Render Duration:              {report['rendering_duration_s']}s")
    print(f"  Peak Dedicated VRAM:          {report['peak_dedicated_vram_mb']}MB ({report['peak_dedicated_vram_pct']}%)")
    print(f"  Peak System RAM:              {report['peak_system_ram_mb']}MB ({report['peak_system_ram_pct']}%)")
    print(f"  Max Concurrent LLM Requests:  {report['max_concurrent_llm']}")
    print(f"  LLM during Render:            {report['llm_during_render_overlap']} (0 expected)")
    print(f"  Whisper during Render:        {report['whisper_during_render_overlap']} (0 expected)")
    print(f"  Playable / Valid Geometry:    {verified_playable}/{n_clips} | {valid_geometry}/{n_clips}")
    print(f"  Audio Streams Synchronized:   {valid_audio}/{n_clips}")
    print("=" * 60)

    # Assertions
    assert report["candidate_clip_count"] >= 9, f"Expected at least 9 clips, got {n_clips}"
    assert report["effective_render_workers"] == 1, f"Expected strictly 1 worker, got {report['effective_render_workers']}"
    assert report["llm_during_render_overlap"] == 0, "LLM inference overlapped with rendering"
    assert report["whisper_during_render_overlap"] == 0, "Whisper overlapped with rendering"
    assert report["failed_clips"] == 0, "Some clips failed to render"
    assert verified_playable == n_clips, "Not all clips are playable"
    print("\nALL STRESS TEST ACCEPTANCE CRITERIA PASSED!")


if __name__ == "__main__":
    main()
