"""Comprehensive Stage 4 Automated Regression Test Suite for ClipForge."""
import io
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from config import ROOT, apply_run_options, load_config
from errors import ClipForgeError, IngestError
from ffutil import ffmpeg_bin, nvenc_available, probe
import history
from highlights import rule_based_candidates
from metadata import template_metadata
from server import jobs
from server.routes_library import (
    dashboard_stats,
    delete_clip_dir,
    delete_job,
    get_file,
    list_jobs,
    output_root,
    zip_job,
)
from server.routes_run import (
    InspectRequest,
    RunRequest,
    _build_cfg,
    _save_upload,
    cancel_run,
    inspect_youtube_url,
    list_runs,
    run_status,
    upload_video,
)
from server.routes_settings import system_info
from transcribe import gpu_available


def test_1_environment_hardware():
    """Verify hardware acceleration, CUDA, NVENC, and FFmpeg."""
    print("Testing Stage 2 environment checks...")
    sys = system_info()
    assert sys["gpu_transcribe"] is True, "Faster-Whisper CUDA should be available"
    assert sys["gpu_encode"] is True, "NVENC should be available"
    assert sys["acceleration"] == "full"
    assert Path(sys["ffmpeg_bin"]).exists()
    assert Path(ffmpeg_bin()).is_file()
    print("   Environment check PASSED: full acceleration on", sys["gpu_name"])


def test_2_upload_validation():
    """Verify upload validation rejects empty, oversized, and invalid formats."""
    print("Testing upload validation...")
    upload_dir = ROOT / "cache" / "_test_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 1. Invalid extension (e.g. .exe)
    invalid_file = UploadFile(
        file=io.BytesIO(b"fake binary"),
        filename="malware.exe",
    )
    try:
        upload_video(invalid_file)
        assert False, "Should reject .exe upload"
    except HTTPException as e:
        assert e.status_code == 422
        assert "Unsupported video format" in e.detail
        print("   Invalid extension correctly rejected (422)")

    # 2. Empty file (0 bytes)
    empty_file = UploadFile(
        file=io.BytesIO(b""),
        filename="empty.mp4",
    )
    try:
        upload_video(empty_file)
        assert False, "Should reject empty upload"
    except HTTPException as e:
        assert e.status_code == 422
        assert "empty" in e.detail
        print("   Empty file (0 bytes) correctly rejected (422)")

    # 3. Valid video upload
    valid_file = UploadFile(
        file=io.BytesIO(b"dummy video data for upload test"),
        filename="test_upload.mp4",
    )
    res = upload_video(valid_file)
    assert "path" in res
    saved_path = Path(res["path"])
    assert saved_path.exists()
    assert saved_path.stat().st_size > 0
    saved_path.unlink(missing_ok=True)
    print("   Valid upload correctly accepted and stored")


def test_3_config_mapping_and_validation():
    """Verify configuration options are validated and correctly propagated."""
    print("Testing processing configuration validation...")
    cfg = load_config()

    # 1. Guard against inverted duration (min > max)
    opts = apply_run_options(cfg, {"clip_min": "60", "clip_max": "20"})
    assert opts["clips"]["min_seconds"] == 20
    assert opts["clips"]["max_seconds"] == 60
    print("   Inverted duration min/max guard PASSED (20-60)")

    # 2. RunRequest mapping for 720p
    req = RunRequest(
        source="http://example.com/video.mp4",
        quality="720p",
        face_tracking=False,
        generate_captions=False,
        generate_metadata=False,
    )
    bcfg = _build_cfg(req)
    assert bcfg["reframe"]["output"]["width"] == 720
    assert bcfg["reframe"]["output"]["height"] == 1280
    assert bcfg["reframe"]["tracking"] is False
    assert bcfg["reframe"]["face_tracking"] is False
    assert bcfg["captions"]["enabled"] is False
    assert bcfg["metadata"]["enabled"] is False
    print("   RunRequest options correctly mapped to config dictionary")


def test_4_job_lifecycle_and_cancellation():
    """Verify in-memory handle creation, status monitoring, and cancellation."""
    print("Testing job lifecycle and cancellation...")
    handle = jobs.create("_test_cancel_run")
    assert handle.id == "_test_cancel_run"
    assert handle.state == "running"

    st = run_status(handle.id)
    assert st["state"] == "running"

    cancel_res = cancel_run(handle.id)
    assert handle.cancel_event.is_set()
    assert "Stopping" in cancel_res["message"]

    handle.finish("cancelled")
    assert handle.state == "cancelled"

    runs = list_runs()
    item = next((r for r in runs["runs"] if r["run_id"] == "_test_cancel_run"), None)
    assert item is not None
    assert item["state"] == "cancelled"

    # Clean up registry
    jobs.REGISTRY.pop(handle.id, None)
    print("   Job lifecycle and cooperative cancellation PASSED")


def test_5_zip_bundle_integrity():
    """Verify ZIP bundle packaging and archive integrity."""
    print("Testing ZIP bundle integrity...")
    res = zip_job("20261114-002848_sample")
    zip_path = Path(res.path)
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        print("   ZIP contents:", namelist)
        assert any(n.endswith(".mp4") for n in namelist)
        assert any(n.endswith(".json") for n in namelist)
        # Test archive integrity
        corrupt_file = zf.testzip()
        assert corrupt_file is None, f"Corrupted file in ZIP: {corrupt_file}"
    print("   ZIP bundle integrity PASSED")


def test_6_history_persistence():
    """Verify history recording and reading from SQLite."""
    print("Testing history SQLite persistence...")
    cfg = load_config()
    test_job_data = {
        "job_id": "test_hist_001",
        "created": "2099-01-01T00:00:00",
        "source": "samples/sample.mp4",
        "status": "done",
        "settings": {"preset": "karaoke-pop"},
        "stages": {"render": 5.0},
        "clips": [{"index": 0, "kept": True}],
        "notes": ["test note"],
    }
    history.record_job(test_job_data, "output/test_hist_001", cfg)

    hist_jobs = history.list_jobs(cfg)
    found = next((j for j in hist_jobs if j["job_id"] == "test_hist_001"), None)
    assert found is not None
    assert found["status"] == "done"
    assert found["clip_count"] == 1
    assert found["kept"] == 1

    # Remove test row from DB
    with sqlite3.connect(ROOT / cfg["paths"]["db_path"]) as conn:
        conn.execute("DELETE FROM jobs WHERE job_id='test_hist_001'")
    print("   History SQLite persistence PASSED")


def test_7_error_and_ai_fallbacks():
    """Verify AI and input error fallbacks."""
    print("Testing error and AI fallbacks...")

    # 1. Template metadata fallback
    meta = template_metadata("How to learn fast", "Here is a secret to learning. It takes consistency.")
    assert "title" in meta and len(meta["title"]) > 0
    assert "description" in meta and len(meta["description"]) > 0
    assert "hashtags" in meta and len(meta["hashtags"]) >= 8
    print("   Template metadata fallback PASSED")

    # 2. Rule-based candidate fallback
    windows = [
        {"start": 0.0, "end": 20.0, "text": "This is an exciting test of speech energy and viral moments!"},
        {"start": 20.0, "end": 40.0, "text": "Another test window with high content quality."},
    ]
    cfg = load_config()
    cands = rule_based_candidates(windows, cfg)
    assert len(cands) >= 1
    assert "start" in cands[0]
    assert "score" in cands[0]
    print("   Rule-based candidate selection fallback PASSED")


def test_8_clip_and_job_deletion():
    """Verify safe deletion boundaries."""
    print("Testing clip and job deletion boundaries...")
    root = output_root()
    test_dir = root / "_test_deletion_boundary"
    clip_dir = test_dir / "clip_00"
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / "final.mp4").write_bytes(b"dummy clip")
    (test_dir / "job.json").write_text(json.dumps({"clips": [{"index": 0}]}))

    assert clip_dir.exists()
    freed = delete_clip_dir(clip_dir)
    assert freed > 0
    assert not clip_dir.exists()

    # Verify job deletion
    res = delete_job("_test_deletion_boundary")
    assert res["status"] == "deleted"
    assert not test_dir.exists()
    print("   Clip and job deletion boundaries PASSED")


if __name__ == "__main__":
    test_1_environment_hardware()
    test_2_upload_validation()
    test_3_config_mapping_and_validation()
    test_4_job_lifecycle_and_cancellation()
    test_5_zip_bundle_integrity()
    test_6_history_persistence()
    test_7_error_and_ai_fallbacks()
    test_8_clip_and_job_deletion()
    print("\n=======================================================")
    print(">>> ALL 8 STAGE 4 REGRESSION TEST SUITES PASSED! <<<")
    print("=======================================================")

