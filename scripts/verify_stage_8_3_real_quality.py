"""Script to run multi-clip pipeline validation on sample.mp4 and inspect real quality & hardware telemetry."""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_config, apply_run_options
from ffutil import probe, run_ffmpeg
import pipeline
from resource_coordinator import ResourceCoordinator
import llm_manager

SAMPLE_VIDEO = ROOT / "samples" / "sample.mp4"
OUTPUT_DIR = ROOT / "output" / "_stage_8_3_real_validation"


def main():
    print("===============================================================")
    print("STAGE 8.3: REAL MULTI-CLIP VIDEO QUALITY & HARDWARE STRESS TEST")
    print("===============================================================")

    rc = ResourceCoordinator()
    ram_init = rc.get_ram_status()
    gpu_init = rc.get_gpu_status(force_refresh=True)

    print(f"Initial RAM: {ram_init['used_mb']:.1f}MB used / {ram_init['total_mb']:.1f}MB total ({ram_init['percent']:.1f}%)")
    print(f"Initial GPU VRAM: {gpu_init['used_mb']:.1f}MB used / {gpu_init['total_mb']:.1f}MB total ({gpu_init['percent']:.1f}%)")

    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    cfg = apply_run_options(cfg, {
        "clip_min": "15",
        "clip_max": "35",
        "preset": "karaoke-pop",
        "framing_mode": "smart_director",
    })

    t0 = time.perf_counter()
    job = pipeline.run_job(
        str(SAMPLE_VIDEO),
        cfg,
        provider="mock",
        job_dir=OUTPUT_DIR,
        aspect="9:16",
        preset="karaoke-pop",
        target_count=3,
    )
    t_total = time.perf_counter() - t0

    print(f"\nPipeline Job Finished in {t_total:.2f}s with status: {job['status']}")
    assert job["status"] == "done", f"Job failed with status {job['status']}"

    clips = job.get("clips", [])
    print(f"Total clips generated: {len(clips)}")
    assert len(clips) >= 2, f"Expected at least 2 clips, got {len(clips)}"

    results = {
        "job_id": job.get("job_id"),
        "status": job["status"],
        "total_time_s": round(t_total, 2),
        "clips": [],
        "telemetry": {
            "ram_initial_mb": ram_init["used_mb"],
            "ram_initial_pct": ram_init["percent"],
            "gpu_initial_mb": gpu_init["used_mb"],
            "gpu_initial_pct": gpu_init["percent"],
            "gpu_post_mb": rc.get_gpu_status(force_refresh=True)["used_mb"],
            "ram_post_mb": rc.get_ram_status()["used_mb"],
        }
    }

    for i, clip in enumerate(clips):
        clip_path = Path(clip["path"])
        assert clip_path.exists(), f"Clip file missing: {clip_path}"
        file_sz = clip_path.stat().st_size
        assert file_sz > 100_000, f"Clip file suspiciously small ({file_sz} bytes)"

        info = probe(clip_path)
        w, h = int(info["width"]), int(info["height"])
        dur = float(info["duration"])
        vcodec = info.get("vcodec", "unknown")
        acodec = info.get("acodec", "unknown")

        # Container integrity check via ffmpeg null muxer
        integrity_ok = True
        try:
            run_ffmpeg(["-v", "error", "-i", str(clip_path), "-f", "null", "-"])
        except Exception as e:
            integrity_ok = False
            print(f"Clip {i} FFmpeg container check warning: {e}")

        # Check director plan
        clip_dir = clip_path.parent
        director_plan = []
        plan_file = clip_dir / "director_plan.json"
        if plan_file.exists():
            director_plan = json.loads(plan_file.read_text(encoding="utf-8"))

        clip_audit = {
            "index": clip.get("index", i),
            "rank": clip.get("rank"),
            "composite_score": clip.get("weighted_score"),
            "path": str(clip_path),
            "size_bytes": file_sz,
            "width": w,
            "height": h,
            "duration": dur,
            "vcodec": vcodec,
            "acodec": acodec,
            "container_integrity_ok": integrity_ok,
            "director_layouts": [s.get("layout") for s in director_plan],
            "title": (clip.get("metadata") or {}).get("title"),
            "transcript_status": clip.get("transcript_status"),
            "assessment_status": clip.get("assessment_status"),
        }
        results["clips"].append(clip_audit)

        print(f"\n--- CLIP {i + 1} AUDIT ---")
        print(f"Path: {clip_path.name} ({file_sz / 1024 / 1024:.2f} MB)")
        print(f"Resolution: {w}x{h} (9:16 target)")
        print(f"Duration: {dur:.2f}s")
        print(f"Codecs: v={vcodec}, a={acodec}")
        print(f"Integrity check: {'PASSED' if integrity_ok else 'FAILED'}")
        print(f"Director layouts: {clip_audit['director_layouts']}")
        print(f"Composite Score: {clip_audit['composite_score']} (Rank {clip_audit['rank']})")

    report_path = OUTPUT_DIR / "quality_audit_summary.json"
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote full quality audit summary to: {report_path}")


if __name__ == "__main__":
    main()
