"""Stage 7 Features Test Suite:
- Multi-person 2-shot framing and conversation tracking
- SubjectTracker stationary deadband anti-jitter
- Temporary face disappearance recovery
- Controlled dynamic zoom with reaction event ramping
- Adaptive headroom with shoulder/upper-body awareness
- 6 Professional caption presets + legacy backward compatibility
- Devanagari font rendering verification
- Professional speech enhancement audio filters (highpass, FFT denoise, compressor)
- Dynamic Ollama model classification for RTX 5050 8GB GPU
- Editing studio API endpoints and active-job model lock
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import captions
import config
import llm_manager
import music
import reframe
from server import jobs
from server.routes_edit import router as edit_router
from server.routes_settings import router as settings_router


# =====================================================================
# 1. Reframing, Tracking & Dynamic Zoom Tests
# =====================================================================

def test_reframe_deadband_suppresses_micro_jitter():
    """Verify stationary speaker deadband holds coordinates when motion is < 4px."""
    tracker = reframe.SubjectTracker(1920, 1080, deadband_px=4.0)
    # Initial detection at center (960, 540)
    t1 = tracker.update([(960.0, 540.0, 200.0, 200.0, 0.95, 0.2)], 0)
    assert t1 is not None
    assert t1["cx"] == 960.0

    # Slight camera jitter / motion of 2px (within 4px deadband)
    t2 = tracker.update([(962.0, 541.0, 200.0, 200.0, 0.95, 0.2)], 1)
    assert t2 is not None
    # Track center should remain deadband-clamped to original position
    assert t2["cx"] == 960.0
    assert t2["cy"] == 540.0


def test_reframe_temporary_face_disappearance_recovery():
    """Verify tracker holds position across temporary blinks/turns (grace period)."""
    tracker = reframe.SubjectTracker(1920, 1080)
    t1 = tracker.update([(960.0, 540.0, 200.0, 200.0, 0.95, 0.2)], 0)
    assert t1 is not None

    # Face temporarily missing for 5 frames (e.g. subject looks away / obstruction)
    for frame_i in range(1, 6):
        rec = tracker.update([], frame_i)
        assert rec is not None
        assert rec.get("recovered") is True
        assert rec["cx"] == 960.0


def test_reframe_multi_person_2shot_framing():
    """Verify 2 speakers fitting within 9:16 crop width produce a unified 2-shot midpoint."""
    tracker = reframe.SubjectTracker(1920, 1080)
    crop_w_est = int(1080 * 9 / 16)  # 607 px
    # Two people seated at x=800 and x=1100 (span = 300px <= 0.75 * 607 = 455px)
    face1 = (800.0, 450.0, 120.0, 120.0, 0.9, 0.3)
    face2 = (1100.0, 470.0, 120.0, 120.0, 0.9, 0.1)

    framed = tracker.update([face1, face2], 0)
    assert framed is not None
    assert framed.get("multi_person") is True
    # Midpoint of 800 and 1100 is 950
    assert 940.0 <= framed["cx"] <= 960.0


def test_reframe_multi_person_too_wide_tracks_active_speaker():
    """Verify 2 people positioned too far apart (> 0.75 * crop_w) prioritize the active speaker."""
    tracker = reframe.SubjectTracker(1920, 1080)
    # Face 1 at x=400, Face 2 at x=1500 (span = 1100px >> 455px)
    # Face 1 is actively talking (higher mouth variance/openness)
    face1 = (400.0, 450.0, 140.0, 140.0, 0.95, 0.8)
    face2 = (1500.0, 450.0, 140.0, 140.0, 0.95, 0.05)

    framed = tracker.update([face1, face2], 0)
    assert framed is not None
    assert framed.get("multi_person") is False
    # Should lock onto active speaker (face1 at x=400)
    assert 380.0 <= framed["cx"] <= 420.0


def test_dynamic_zoom_parameters_in_config():
    """Verify dynamic zoom config options exist and validate defaults."""
    cfg = config.load_config()
    rcfg = cfg.get("reframe", {})
    zoom_cfg = rcfg.get("dynamic_zoom", {})
    assert zoom_cfg.get("enabled") is True
    assert 1.05 <= zoom_cfg.get("max_zoom", 1.12) <= 1.20


# =====================================================================
# 2. Caption Engine & Preset Tests
# =====================================================================

def test_stage7_all_six_presets_defined_and_valid():
    """Verify all 6 Stage 7 professional presets exist in config and render valid ASS."""
    cfg = config.load_config()
    presets = cfg.get("captions", {}).get("presets", {})
    required = [
        "modern-bold",
        "minimal-clean",
        "karaoke-highlight",
        "creator-style",
        "cinematic",
        "dynamic-social"
    ]
    for p in required:
        assert p in presets, f"Preset '{p}' missing from config.yaml"
        preset = presets[p]
        assert "font" in preset
        assert "font_size" in preset
        assert "primary_color" in preset
        assert "highlight_color" in preset
        assert preset.get("style") in ("karaoke", "fade", "box")


def test_legacy_caption_presets_backward_compatibility():
    """Verify legacy preset names are preserved so existing jobs/configs don't break."""
    cfg = config.load_config()
    presets = cfg.get("captions", {}).get("presets", {})
    legacy = ["karaoke-pop", "bold-impact", "clean-minimal", "highlight-box"]
    for p in legacy:
        assert p in presets, f"Legacy preset '{p}' must be preserved for backward compatibility"


def test_stage7_caption_ass_generation(tmp_path):
    """Test ASS generation for each of the 6 Stage 7 presets."""
    cfg = config.load_config()
    words = [
        {"word": "Welcome", "start": 0.0, "end": 0.5},
        {"word": "to", "start": 0.5, "end": 0.7},
        {"word": "Frameza", "start": 0.7, "end": 1.2}
    ]
    for preset_name in ["modern-bold", "minimal-clean", "karaoke-highlight", "creator-style", "cinematic", "dynamic-social"]:
        out_ass = tmp_path / f"{preset_name}.ass"
        captions.write_ass(words, out_ass, cfg, preset_name)
        assert out_ass.exists()
        content = out_ass.read_text(encoding="utf-8-sig")
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content
        assert "Dialogue:" in content
        assert "Frameza" in content or "FRAMEZA" in content


def test_devanagari_hindi_font_rendering(tmp_path):
    """Verify Hindi Devanagari text automatically switches font to Noto Sans Devanagari."""
    cfg = config.load_config()
    words = [
        {"word": "नमस्ते", "start": 0.0, "end": 0.5},
        {"word": "दुनिया", "start": 0.5, "end": 1.0}
    ]
    out_ass = tmp_path / "hindi.ass"
    captions.write_ass(words, out_ass, cfg, "modern-bold")
    content = out_ass.read_text(encoding="utf-8-sig")
    assert "Noto Sans Devanagari" in content


# =====================================================================
# 3. Professional Audio Processing Tests
# =====================================================================

def test_speech_enhancement_in_mix_graph():
    """Verify voice enhancement filters (highpass, afftdn, acompressor) are assembled in mix_graph."""
    cfg = config.load_config()
    graph, inputs = music._mix_graph(
        dur=10.0,
        cfg=cfg,
        sfx=[],
        has_music=True,
        volume_db=-18.0,
        loudnorm={"enabled": True, "i": -14, "tp": -1.5, "lra": 11}
    )
    assert "highpass=f=80" in graph
    assert "afftdn=nf=-25" in graph
    assert "acompressor" in graph
    assert "sidechaincompress" in graph
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in graph
    assert "aresample=48000" in graph


def test_audio_config_defaults():
    """Verify audio section exists in config with speech enhancement enabled."""
    cfg = config.load_config()
    audio_cfg = cfg.get("audio", {})
    assert audio_cfg.get("speech_enhance") is True
    assert audio_cfg.get("normalize") is True


# =====================================================================
# 4. Ollama Dynamic Model Manager & Classification Tests
# =====================================================================

def test_classify_model_qwen35_4b_best_recommended():
    """Verify qwen3.5:4b is classified as BEST RECOMMENDED and VISION CAPABLE."""
    res = llm_manager.classify_model_for_hardware(
        name="qwen3.5:4b",
        size_gb=3.3,
        details={"parameter_size": "4.2B"},
        capabilities=["vision"]
    )
    assert res["category"] == "BEST RECOMMENDED"
    assert "BEST RECOMMENDED" in res["badges"]
    assert "VISION CAPABLE" in res["badges"]
    assert res["is_recommended"] is True
    assert "RTX 5050" in res["description"]


def test_classify_model_llama32_3b_lightweight_fast():
    """Verify llama3.2:3b is classified as LIGHTWEIGHT / FAST."""
    res = llm_manager.classify_model_for_hardware(
        name="llama3.2:3b",
        size_gb=2.0,
        details={"parameter_size": "3.2B"},
        capabilities=[]
    )
    assert res["category"] == "LIGHTWEIGHT / FAST"
    assert "LIGHTWEIGHT / FAST" in res["badges"]
    assert res["is_recommended"] is False


def test_classify_model_qwen25_coder_balanced():
    """Verify 7B model is classified as BALANCED."""
    res = llm_manager.classify_model_for_hardware(
        name="qwen2.5-coder:7b",
        size_gb=4.7,
        details={"parameter_size": "7.6B"},
        capabilities=[]
    )
    assert res["category"] == "BALANCED"
    assert "BALANCED" in res["badges"]


def test_classify_model_gemma_12b_heavy():
    """Verify 12B model is classified as HEAVY."""
    res = llm_manager.classify_model_for_hardware(
        name="gemma4:12b",
        size_gb=8.0,
        details={"parameter_size": "12B"},
        capabilities=[]
    )
    assert res["category"] == "HEAVY"
    assert "HEAVY" in res["badges"]


def test_classify_model_gemma_26b_very_heavy_not_recommended():
    """Verify 26B model is classified as VERY HEAVY / NOT RECOMMENDED FOR THIS HARDWARE."""
    res = llm_manager.classify_model_for_hardware(
        name="gemma4:26b",
        size_gb=18.0,
        details={"parameter_size": "26B"},
        capabilities=[]
    )
    assert "VERY HEAVY" in res["category"]
    assert "NOT RECOMMENDED" in res["badges"]


def test_list_available_models_mock_response():
    """Verify list_available_models parses Ollama tags and sorts BEST RECOMMENDED first."""
    mock_payload = {
        "models": [
            {"name": "gemma4:12b", "size": 8589934592, "details": {"parameter_size": "12B"}},
            {"name": "qwen3.5:4b", "size": 3543348019, "details": {"parameter_size": "4.2B"}, "capabilities": ["vision"]},
            {"name": "llama3.2:3b", "size": 2147483648, "details": {"parameter_size": "3.2B"}}
        ]
    }
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_get.return_value = mock_resp

        models = llm_manager.list_available_models("http://127.0.0.1:11434")
        assert len(models) == 3
        # Best recommended should be sorted first
        assert models[0]["name"] == "qwen3.5:4b"
        assert models[0]["is_recommended"] is True
        assert models[0]["category"] == "BEST RECOMMENDED"


def test_ollama_offline_returns_empty_list():
    """Verify list_available_models handles Ollama offline without crashing."""
    with patch("requests.get", side_effect=Exception("Connection refused")):
        models = llm_manager.list_available_models("http://127.0.0.1:11434")
        assert models == []


def test_active_job_blocks_model_switching():
    """Verify model switching is rejected when a job is actively running."""
    from fastapi import HTTPException
    from server.routes_settings import SwitchModelRequest, switch_active_model

    # Mock an active run in registry
    mock_handle = MagicMock()
    mock_handle.state = "running"
    with patch.dict(jobs.REGISTRY, {"test_run": mock_handle}):
        assert jobs.has_active_runs() is True
        with pytest.raises(HTTPException) as exc_info:
            switch_active_model(SwitchModelRequest(model="qwen3.5:4b"))
        assert exc_info.value.status_code == 409


# =====================================================================
# 5. Editing Studio API Tests
# =====================================================================

def test_editor_save_and_reset_workflow(tmp_path):
    """Verify editor save persists edit_plan.json and reset cleans it up."""
    from server.routes_edit import EditorSaveRequest, reset_clip_editor, save_clip_editor
    with patch("server.routes_edit.safe_job_path", return_value=tmp_path):
        clip_dir = tmp_path / "clip_00"
        clip_dir.mkdir(parents=True)
        (clip_dir / "metadata.json").write_text("{}", encoding="utf-8")

        req = EditorSaveRequest(
            start=2.5,
            end=18.0,
            title="Custom Title",
            description="Custom Description",
            hashtags=["#ai", "#viral"],
            caption_preset="creator-style",
            crop_x_offset=15.0,
            crop_y_offset=-10.0,
            zoom=1.10
        )
        save_res = save_clip_editor("job_test", 0, req)
        assert save_res["ok"] is True
        assert (clip_dir / "edit_plan.json").exists()

        plan = json.loads((clip_dir / "edit_plan.json").read_text(encoding="utf-8"))
        assert plan["start"] == 2.5
        assert plan["title"] == "Custom Title"
        assert plan["caption_preset"] == "creator-style"
        assert plan["zoom"] == 1.10

        # Reset
        reset_res = reset_clip_editor("job_test", 0)
        assert reset_res["ok"] is True
        assert not (clip_dir / "edit_plan.json").exists()


def test_sampling_rate_adaptive_grace_period():
    """Verify SubjectTracker face disappearance grace frames dynamically scale with sampling FPS."""
    t12 = reframe.SubjectTracker(1920, 1080, fps=12.0)
    assert t12.grace_frames == 19
    assert t12.stale_frames == 24

    t24 = reframe.SubjectTracker(1920, 1080, fps=24.0)
    assert t24.grace_frames == 38
    assert t24.stale_frames == 48

    t6 = reframe.SubjectTracker(1920, 1080, fps=6.0)
    assert t6.grace_frames == 10
    assert t6.stale_frames == 12


def test_caption_cyan_highlight_bgr_encoding():
    """Verify karaoke-highlight uses genuine Cyan in ASS BGR (&H00FFD700)."""
    cfg = config.load_config()
    preset = cfg["captions"]["presets"]["karaoke-highlight"]
    # In ASS &H00BBGGRR: Cyan is Blue=FF, Green=D7, Red=00 -> &H00FFD700
    assert preset["highlight_color"] == "&H00FFD700"


def test_text_only_model_drops_vision_gracefully():
    """Verify query_ollama handles vision media on text-only models by processing text gracefully."""
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Processed text without crash", "eval_count": 10}
        mock_post.return_value = mock_resp

        # Pass image media to a text-only model
        fake_media = [{"kind": "image", "data": b"fake_png_bytes"}]
        res = llm_manager.query_ollama(
            prompt="Analyze this frame",
            model="llama3.2:3b",
            media=fake_media,
            enable_fallback=False
        )
        assert res == "Processed text without crash"
        # Verify sent payload did NOT include images
        sent_payload = mock_post.call_args[1]["json"]
        assert "images" not in sent_payload


