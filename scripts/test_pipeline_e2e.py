"""End-to-end verification of ClipForge video clipping pipeline across configurations."""
import json
import shutil
import sys
from pathlib import Path

from config import load_config, apply_run_options
from ffutil import probe
import pipeline

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VIDEO = ROOT / "samples" / "sample.mp4"

def probe_file(p: Path) -> dict:
    return probe(p)

def test_9_16_workflow():
    print("\n--- Test 1: 9:16 Vertical Reframing with Subtitles ---")
    out_dir = ROOT / "output" / "_test_e2e_9_16"
    shutil.rmtree(out_dir, ignore_errors=True)
    cfg = load_config()
    cfg = apply_run_options(cfg, {
        "clip_min": "15",
        "clip_max": "30",
        "preset": "karaoke-pop",
    })
    job = pipeline.run_job(
        str(SAMPLE_VIDEO),
        cfg,
        provider="mock",
        job_dir=out_dir,
        aspect="9:16",
        preset="karaoke-pop",
        target_count=1,
    )
    print("Job status:", job["status"])
    assert job["status"] == "done"
    assert len(job["clips"]) >= 1

    clip = job["clips"][0]
    final_mp4 = Path(clip["path"])
    assert final_mp4.exists(), f"Missing output: {final_mp4}"

    info = probe_file(final_mp4)
    w, h = int(info["width"]), int(info["height"])
    dur = float(info["duration"])
    print(f"9:16 Output: {w}x{h}, duration={dur:.1f}s, vcodec={info['vcodec']}, acodec={info['acodec']}")

    assert w == 1080 and h == 1920, f"Expected 1080x1920, got {w}x{h}"
    assert 10 <= dur <= 35, f"Duration {dur} outside expected range"
    print(">>> 9:16 Vertical workflow verified successfully!")

def test_1_1_workflow():
    print("\n--- Test 2: 1:1 Square Format ---")
    out_dir = ROOT / "output" / "_test_e2e_1_1"
    shutil.rmtree(out_dir, ignore_errors=True)
    cfg = load_config()
    job = pipeline.run_job(
        str(SAMPLE_VIDEO),
        cfg,
        provider="mock",
        job_dir=out_dir,
        aspect="1:1",
        preset="karaoke-pop",
        target_count=1,
    )
    assert job["status"] == "done"
    final_mp4 = Path(job["clips"][0]["path"])
    info = probe_file(final_mp4)
    w, h = int(info["width"]), int(info["height"])
    print(f"1:1 Output: {w}x{h}")
    assert w == 1080 and h == 1080, f"Expected 1080x1080, got {w}x{h}"
    print(">>> 1:1 Square workflow verified successfully!")

def test_16_9_workflow():
    print("\n--- Test 3: 16:9 Landscape Format ---")
    out_dir = ROOT / "output" / "_test_e2e_16_9"
    shutil.rmtree(out_dir, ignore_errors=True)
    cfg = load_config()
    job = pipeline.run_job(
        str(SAMPLE_VIDEO),
        cfg,
        provider="mock",
        job_dir=out_dir,
        aspect="16:9",
        preset="karaoke-pop",
        target_count=1,
    )
    assert job["status"] == "done"
    final_mp4 = Path(job["clips"][0]["path"])
    info = probe_file(final_mp4)
    w, h = int(info["width"]), int(info["height"])
    print(f"16:9 Output: {w}x{h}")
    assert w == 1280 and h == 720, f"Expected 1280x720, got {w}x{h}"
    print(">>> 16:9 Landscape workflow verified successfully!")

def test_720p_resolution():
    print("\n--- Test 4: 720p Resolution Mode ---")
    out_dir = ROOT / "output" / "_test_e2e_720p"
    shutil.rmtree(out_dir, ignore_errors=True)
    cfg = load_config()
    cfg.setdefault("reframe", {}).setdefault("output", {})
    cfg["reframe"]["output"] = {"width": 720, "height": 1280}
    job = pipeline.run_job(
        str(SAMPLE_VIDEO),
        cfg,
        provider="mock",
        job_dir=out_dir,
        aspect="9:16",
        preset="karaoke-pop",
        target_count=1,
    )
    assert job["status"] == "done"
    final_mp4 = Path(job["clips"][0]["path"])
    info = probe_file(final_mp4)
    w, h = int(info["width"]), int(info["height"])
    print(f"720p 9:16 Output: {w}x{h}")
    assert w == 720 and h == 1280, f"Expected 720x1280, got {w}x{h}"
    print(">>> 720p resolution verified successfully!")

def test_captions_disabled():
    print("\n--- Test 5: Subtitles / Captions Disabled ---")
    out_dir = ROOT / "output" / "_test_e2e_no_subs"
    shutil.rmtree(out_dir, ignore_errors=True)
    cfg = load_config()
    cfg.setdefault("captions", {})["enabled"] = False
    job = pipeline.run_job(
        str(SAMPLE_VIDEO),
        cfg,
        provider="mock",
        job_dir=out_dir,
        aspect="9:16",
        preset="none",
        target_count=1,
    )
    assert job["status"] == "done"
    final_mp4 = Path(job["clips"][0]["path"])
    assert final_mp4.exists()
    print(">>> Captions disabled workflow verified successfully!")

if __name__ == "__main__":
    test_9_16_workflow()
    test_1_1_workflow()
    test_16_9_workflow()
    test_720p_resolution()
    test_captions_disabled()
    print("\n============================================")
    print(">>> ALL PIPELINE E2E TESTS PASSED CLEANLY! <<<")
    print("============================================")
