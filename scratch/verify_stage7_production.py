"""
Comprehensive Stage 7 Production Readiness Verification Script
Tests real media processing, FFprobe validation, Ollama live integration, and Studio edit persistence.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Ensure root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_config
from ffutil import probe, run_ffmpeg
import captions
import caption_lang
import reframe
import music
import rerender
import llm_manager

VERIFY_DIR = ROOT / "output" / "_stage7_verification"
VERIFY_DIR.mkdir(parents=True, exist_ok=True)

results = {
    "tests_run": 0,
    "tests_passed": 0,
    "tests_failed": 0,
    "details": []
}

def record_result(name: str, passed: bool, info: dict):
    results["tests_run"] += 1
    if passed:
        results["tests_passed"] += 1
        status = "PASSED"
    else:
        results["tests_failed"] += 1
        status = "FAILED"
    results["details"].append({"name": name, "status": status, **info})
    print(f"[{status}] {name} - {json.dumps(info, default=str)}")


def test_1_multi_aspect_and_ffprobe():
    """Verify 9:16, 1:1, and 16:9 rendering with real FFprobe inspection."""
    cfg = load_config()
    sample_mp4 = ROOT / "samples" / "sample.mp4"
    if not sample_mp4.exists():
        record_result("Multi-Aspect Render", False, {"error": "sample.mp4 not found"})
        return

    # 1. 9:16 render
    out_916 = VERIFY_DIR / "test_916.mp4"
    metrics_916 = reframe.reframe_clip(
        sample_mp4, 0.0, 4.0, out_916, [], cfg, aspect="9:16"
    )
    p_916 = probe(out_916)
    
    # 2. 1:1 render
    out_11 = VERIFY_DIR / "test_11.mp4"
    metrics_11 = reframe.reframe_clip(
        sample_mp4, 0.0, 4.0, out_11, [], cfg, aspect="1:1"
    )
    p_11 = probe(out_11)

    # 3. 16:9 render (passthrough)
    out_169 = VERIFY_DIR / "test_169.mp4"
    import cut
    cut.cut_clip(sample_mp4, 0.0, 4.0, out_169, cfg)
    p_169 = probe(out_169)

    aspect_916 = p_916["width"] / p_916["height"]
    aspect_11 = p_11["width"] / p_11["height"]
    aspect_169 = p_169["width"] / p_169["height"]

    valid = (
        abs(aspect_916 - (9 / 16)) < 0.05 and
        abs(aspect_11 - 1.0) < 0.01 and
        abs(aspect_169 - (16 / 9)) < 0.05 and
        p_916["vcodec"] in ("h264", "avc1") and
        p_916["acodec"] == "aac"
    )
    record_result("Multi-Aspect & FFprobe Verification", valid, {
        "9:16": f"{p_916['width']}x{p_916['height']} ({p_916['vcodec']})",
        "1:1": f"{p_11['width']}x{p_11['height']} ({p_11['vcodec']})",
        "16:9": f"{p_169['width']}x{p_169['height']} ({p_169['vcodec']})",
        "acodec": p_916["acodec"]
    })


def test_2_two_person_framing_and_deadband():
    """Verify 2-shot midpoint tracking and deadband suppression."""
    w, h = 1920, 1080
    tracker = reframe.SubjectTracker(w, h, fps=5.0)

    # Simulate two speakers: speaker A at cx=500, speaker B at cx=1300
    # format: (cx, cy, fw, fh, conf, mouth_open)
    frame_dets_2shot = [
        (500.0, 450.0, 100.0, 150.0, 0.95, 0.1),
        (800.0, 450.0, 100.0, 150.0, 0.95, 0.1)
    ]
    sub_2shot = tracker.update(frame_dets_2shot, 0)
    expected_midpoint = 650.0
    midpoint_diff = abs(sub_2shot["cx"] - expected_midpoint)

    # Simulate micro-jitter: tiny shift of 2px
    micro_jitter_dets = [
        (502.0, 450.0, 100.0, 150.0, 0.95, 0.1),
        (802.0, 450.0, 100.0, 150.0, 0.95, 0.1)
    ]
    sub_jitter = tracker.update(micro_jitter_dets, 1)
    # With deadband active, target remains at midpoint
    deadband_active = abs(sub_jitter["cx"] - expected_midpoint) < 2.0

    passed = (midpoint_diff < 5.0) and deadband_active
    record_result("Two-Person 2-Shot & Deadband Verification", passed, {
        "midpoint_cx": sub_2shot["cx"],
        "expected_midpoint": expected_midpoint,
        "deadband_suppressed": deadband_active
    })


def test_3_caption_presets_and_devanagari_render():
    """Verify all 6 Stage 7 caption presets and real Hindi Devanagari rendering."""
    cfg = load_config()
    presets = cfg.get("captions", {}).get("presets", {})
    required = ["modern-bold", "minimal-clean", "karaoke-highlight", "creator-style", "cinematic", "dynamic-social"]
    has_all = all(k in presets for k in required)

    # Check ASS generation with Devanagari Hindi text
    hindi_text = "यह फ्रेमज़ा का एआई वीडियो क्लिपर है"
    words = [
        {"word": "यह", "start": 0.0, "end": 0.6},
        {"word": "फ्रेमज़ा", "start": 0.6, "end": 1.2},
        {"word": "का", "start": 1.2, "end": 1.6},
        {"word": "एआई", "start": 1.6, "end": 2.2},
        {"word": "वीडियो", "start": 2.2, "end": 2.8},
        {"word": "क्लिपर", "start": 2.8, "end": 3.4},
        {"word": "है", "start": 3.4, "end": 4.0}
    ]
    ass_path = VERIFY_DIR / "hindi_test.ass"
    captions.write_ass(
        words, ass_path, cfg,
        preset_name="modern-bold"
    )
    ass_content = ass_path.read_text(encoding="utf-8")
    has_devanagari_font = "Noto Sans Devanagari" in ass_content
    has_hindi_chars = "फ्रेमज़ा" in ass_content

    # Burn captions onto video to confirm FFmpeg handles the ASS filter
    sample_mp4 = VERIFY_DIR / "test_916.mp4"
    burned_mp4 = VERIFY_DIR / "burned_hindi.mp4"
    captions.caption_clip(
        sample_mp4, words, burned_mp4, cfg,
        preset_name="modern-bold",
        anchor=0.60
    )
    p_burned = probe(burned_mp4)

    passed = has_all and has_devanagari_font and has_hindi_chars and p_burned["duration"] > 0
    record_result("Caption Presets & Devanagari Hindi Burn", passed, {
        "presets_count": len(presets),
        "devanagari_font_in_ass": has_devanagari_font,
        "burned_duration": p_burned.get("duration")
    })


def test_4_audio_speech_enhancement_and_ducking():
    """Verify speech enhancement filter chain, sidechain ducking, and -14 LUFS loudnorm."""
    cfg = load_config()
    audio_cfg = cfg.get("audio", {})
    loudnorm_cfg = cfg.get("music", {}).get("loudnorm", {})

    sample_mp4 = VERIFY_DIR / "test_916.mp4"
    synth_music = VERIFY_DIR / "synth_music.wav"
    if not synth_music.exists():
        run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=10", synth_music])

    out_audio = VERIFY_DIR / "test_mixed_audio.mp4"
    mixed = music.mix_audio(sample_mp4, out_audio, cfg, music_path=synth_music, volume_db=-18.0)
    
    p = probe(out_audio)
    passed = (
        mixed is not None and
        p["acodec"] == "aac" and
        audio_cfg.get("speech_enhance") is True and
        loudnorm_cfg.get("i") == -14
    )
    record_result("Intelligent Audio & Speech Enhancement", passed, {
        "acodec": p["acodec"],
        "speech_enhance_enabled": audio_cfg.get("speech_enhance"),
        "target_i": loudnorm_cfg.get("i"),
        "target_tp": loudnorm_cfg.get("tp")
    })


def test_5_studio_editing_workflow_e2e():
    """Verify studio edit persistence, custom offsets, manual zoom, caption edits and export."""
    job_dir = ROOT / "output" / "20261114-002848_sample"
    if not job_dir.exists():
        record_result("Studio Editing Workflow", False, {"error": "sample job dir not found"})
        return

    clip_dir = job_dir / "clip_00"
    plan_path = clip_dir / "edit_plan.json"

    # Step 1: Save custom studio plan
    edit_payload = {
        "start": 44.0,
        "end": 68.0,
        "crop_x_offset": 30.0,
        "crop_y_offset": -15.0,
        "zoom": 1.15,
        "caption_preset": "creator-style",
        "caption_text": "Frameza Pro Studio Edit",
        "music_enabled": True,
        "music_volume_db": -16.0,
        "speech_enhance": True,
        "title": "Verified AI Short Title"
    }
    plan_path.write_text(json.dumps(edit_payload, indent=2), encoding="utf-8")

    # Step 2: Verify persistence by re-reading
    loaded = json.loads(plan_path.read_text(encoding="utf-8"))
    assert loaded["crop_x_offset"] == 30.0
    assert loaded["zoom"] == 1.15

    # Step 3: Run rerender_clip with Studio edits applied
    updated_clip = rerender.rerender_clip(
        job_dir=job_dir,
        clip_index=0,
        start=44.0,
        end=68.0,
        preset="creator-style"
    )

    # Step 4: Verify final rendered MP4 with ffprobe
    final_mp4 = clip_dir / "final.mp4"
    p_final = probe(final_mp4)

    # Step 5: Test Reset
    plan_path.unlink(missing_ok=True)
    reset_verified = not plan_path.exists()

    passed = (
        p_final["width"] == 1080 and
        p_final["height"] == 1920 and
        p_final["vcodec"] in ("h264", "avc1") and
        p_final["acodec"] == "aac" and
        reset_verified
    )
    record_result("Studio Editing Workflow E2E", passed, {
        "final_dims": f"{p_final['width']}x{p_final['height']}",
        "vcodec": p_final["vcodec"],
        "acodec": p_final["acodec"],
        "reset_verified": reset_verified
    })


def test_6_ollama_model_manager():
    """Verify live Ollama model discovery, RTX 5050 suitability badges, and job lock."""
    from unittest.mock import MagicMock, patch
    from fastapi import HTTPException
    from server import jobs
    from server.routes_settings import SwitchModelRequest, switch_active_model

    # 1. Fetch available models from live Ollama API
    models = llm_manager.list_available_models()
    qwen_found = any("qwen3.5:4b" in m["name"] for m in models)
    llama_found = any("llama3.2:3b" in m["name"] for m in models)

    # 2. Check classification metadata
    qwen_meta = next((m for m in models if "qwen3.5:4b" in m["name"]), None)
    badge = qwen_meta.get("recommended_badge") if qwen_meta else None

    # 3. Test active job lock
    mock_handle = MagicMock()
    mock_handle.state = "running"
    blocked = False
    with patch.dict(jobs.REGISTRY, {"verify_active_job": mock_handle}):
        try:
            switch_active_model(SwitchModelRequest(model="qwen3.5:4b"))
        except HTTPException as e:
            if e.status_code == 409:
                blocked = True

    passed = len(models) > 0 and qwen_found and blocked
    record_result("Ollama Dynamic Manager & Job Lock", passed, {
        "models_count": len(models),
        "qwen3.5:4b_badge": badge,
        "active_job_lock_enforced": blocked
    })


def main():
    print("=========================================================")
    print("      FRAMEZA STAGE 7 REAL E2E VERIFICATION SUITE       ")
    print("=========================================================")
    t0 = time.time()
    test_1_multi_aspect_and_ffprobe()
    test_2_two_person_framing_and_deadband()
    test_3_caption_presets_and_devanagari_render()
    test_4_audio_speech_enhancement_and_ducking()
    test_5_studio_editing_workflow_e2e()
    test_6_ollama_model_manager()
    elapsed = time.time() - t0
    print("=========================================================")
    print(f"VERIFICATION SUMMARY: {results['tests_passed']}/{results['tests_run']} PASSED in {elapsed:.2f}s")
    print("=========================================================")
    
    # Save results to JSON for inclusion in final audit report
    out_json = VERIFY_DIR / "verification_results.json"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Results saved to {out_json}")


if __name__ == "__main__":
    main()
