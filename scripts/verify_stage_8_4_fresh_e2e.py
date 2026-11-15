"""Stage 8.4 Fresh End-to-End Real-World Validation on 6-minute Ravi Gupta Podcast Excerpt."""
import json
import os
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

PODCAST_EXCERPT = ROOT / "samples" / "podcast_fresh_6min.mp4"
OUTPUT_DIR = ROOT / "output" / "_stage_8_4_fresh_podcast_validation"


def main():
    print("=" * 70)
    print("STAGE 8.4: FRESH END-TO-END PODCAST VALIDATION & RANKING AUDIT")
    print("=" * 70)

    assert PODCAST_EXCERPT.exists(), f"Source file missing: {PODCAST_EXCERPT}"
    src_probe = probe(PODCAST_EXCERPT)
    print(f"Source: {PODCAST_EXCERPT.name}")
    print(f"Properties: {src_probe['width']}x{src_probe['height']}, {src_probe['fps']}fps, "
          f"duration={src_probe['duration']:.2f}s, vcodec={src_probe['vcodec']}, acodec={src_probe['acodec']}")

    # Clean destination job dir
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rc = ResourceCoordinator()
    ram_init = rc.get_ram_status()
    gpu_init = rc.get_gpu_status(force_refresh=True)

    print(f"\nInitial RAM: {ram_init['used_mb']:.1f}MB used / {ram_init['total_mb']:.1f}MB ({ram_init['percent']:.1f}%)")
    print(f"Initial GPU VRAM: {gpu_init['used_mb']:.1f}MB used / {gpu_init['total_mb']:.1f}MB ({gpu_init['percent']:.1f}%)")

    # Load configuration
    cfg = load_config()
    cfg = apply_run_options(cfg, {
        "clip_min": "15",
        "clip_max": "35",
        "preset": "karaoke-pop",
        "framing_mode": "smart_director",
        "language": "hinglish",
    })

    print("\nStarting 100% fresh pipeline run (ingest -> scenes -> transcribe -> events -> highlights -> refine -> ai_prep -> render -> rescore)...")
    t0 = time.perf_counter()
    job = pipeline.run_job(
        source=str(PODCAST_EXCERPT),
        cfg=cfg,
        provider="mock",  # Deterministic test harness provider for repeatable ranking evaluation
        job_dir=OUTPUT_DIR,
        aspect="9:16",
        preset="karaoke-pop",
        target_count=5,
        caption_language="hinglish",
        force=True,  # Disables job-dir marker caching to force fresh stage execution
    )
    t_total = time.perf_counter() - t0

    print(f"\nPipeline Finished in {t_total:.2f}s with status: {job['status']}")
    assert job["status"] == "done", f"Pipeline failed with status {job['status']}"

    clips = job.get("clips", [])
    print(f"Generated {len(clips)} clips")
    assert len(clips) >= 3, f"Expected at least 3 clips, got {len(clips)}"

    results = {
        "source": {
            "path": str(PODCAST_EXCERPT),
            "duration": src_probe["duration"],
            "resolution": f"{src_probe['width']}x{src_probe['height']}",
            "fps": src_probe["fps"],
            "vcodec": src_probe["vcodec"],
            "acodec": src_probe["acodec"],
        },
        "pipeline": {
            "job_id": job.get("job_id"),
            "status": job["status"],
            "total_time_s": round(t_total, 2),
            "target_count": 5,
            "clips_count": len(clips),
            "stages": job.get("stages", {}),
            "notes": job.get("notes", []),
        },
        "telemetry": {
            "ram_initial_mb": ram_init["used_mb"],
            "ram_initial_pct": ram_init["percent"],
            "gpu_initial_mb": gpu_init["used_mb"],
            "gpu_initial_pct": gpu_init["percent"],
            "gpu_post_mb": rc.get_gpu_status(force_refresh=True)["used_mb"],
            "ram_post_mb": rc.get_ram_status()["used_mb"],
        },
        "clips": [],
    }

    print("\n" + "=" * 70)
    print("INDIVIDUAL CLIP QUALITY & RANKING AUDIT")
    print("=" * 70)

    for i, clip in enumerate(clips):
        clip_path = Path(clip["path"])
        assert clip_path.exists(), f"Clip file missing: {clip_path}"
        sz = clip_path.stat().st_size
        assert sz > 100_000, f"Clip file too small: {sz} bytes"

        p = probe(clip_path)
        w, h = int(p["width"]), int(p["height"])
        dur = float(p["duration"])
        vcodec = p.get("vcodec", "unknown")
        acodec = p.get("acodec", "unknown")

        # Container null muxer check
        container_ok = True
        try:
            run_ffmpeg(["-v", "error", "-i", str(clip_path), "-f", "null", "-"])
        except Exception as e:
            container_ok = False
            print(f"Container warning clip {i}: {e}")

        # Director layout plan
        clip_dir = clip_path.parent
        plan_file = clip_dir / "director_plan.json"
        director_layouts = []
        if plan_file.exists():
            plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
            director_layouts = [s.get("layout") for s in plan_data]

        r = clip.get("ranking", {})
        clip_record = {
            "index": clip.get("index", i),
            "rank": clip.get("rank"),
            "kept": clip.get("kept"),
            "composite_score": clip.get("weighted_score"),
            "hook": clip.get("hook", ""),
            "resolved_text": clip.get("resolved_text", "")[:200],
            "duration": dur,
            "width": w,
            "height": h,
            "vcodec": vcodec,
            "acodec": acodec,
            "container_ok": container_ok,
            "director_layouts": director_layouts,
            "ranking_details": r,
        }
        results["clips"].append(clip_record)

        print(f"\n[RANK {clip.get('rank')}] Clip #{clip.get('index', i)} (Kept: {clip.get('kept')})")
        print(f"  Score: {clip.get('weighted_score'):.3f} | Duration: {dur:.2f}s | Size: {sz / 1024 / 1024:.2f} MB")
        print(f"  Resolution: {w}x{h} (9:16 target) | Codecs: {vcodec}/{acodec}")
        print(f"  Container Integrity: {'PASSED (0 errors)' if container_ok else 'FAILED'}")
        print(f"  AI Director Layouts: {director_layouts}")
        print(f"  Hook: {clip.get('hook', '')[:80]}")
        print(f"  Explainability: Candidate={r.get('candidate_score')}, Virality={r.get('virality_score')}, "
              f"Content={r.get('content_quality_score')}, DurSuitability={r.get('duration_suitability')}")

    out_summary = OUTPUT_DIR / "stage_8_4_fresh_audit_summary.json"
    out_summary.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote full audit summary to: {out_summary}")


if __name__ == "__main__":
    main()
