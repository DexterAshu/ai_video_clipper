"""Stage 3 API and Feature Verification Test Suite."""
from pathlib import Path
from server.routes_library import (
    dashboard_stats,
    delete_job,
    get_file,
    list_jobs,
    output_root,
    zip_job,
)
from server.routes_run import InspectRequest, inspect_youtube_url
from server.routes_settings import system_info


def test_system_info():
    sys = system_info()
    assert sys["gpu_transcribe"] is True
    assert sys["gpu_encode"] is True
    assert sys["acceleration"] == "full"
    assert "RTX" in sys["gpu_name"] or "NVIDIA" in sys["gpu_name"]
    assert Path(sys["ffmpeg_bin"]).exists()


def test_dashboard_stats():
    stats = dashboard_stats()
    assert stats["total_videos"] >= 1
    assert stats["total_clips"] >= 1
    assert stats["completed_jobs"] >= 1
    assert stats["storage_bytes"] > 0
    assert len(stats["recent_jobs"]) >= 1


def test_list_jobs():
    jobs = list_jobs()
    assert "jobs" in jobs
    assert len(jobs["jobs"]) >= 1
    sample_job = next((j for j in jobs["jobs"] if "sample" in j["name"]), None)
    assert sample_job is not None
    assert sample_job["clip_count"] >= 1


def test_poster_thumbnail_generation():
    # Calling get_file for poster.jpg ensures ffmpeg extracts a frame if missing
    res = get_file("20261114-002848_sample", "clip_00", "poster.jpg")
    assert Path(res.path).exists()
    assert Path(res.path).stat().st_size > 1000


def test_zip_bundle_export():
    z = zip_job("20261114-002848_sample")
    assert Path(z.path).exists()
    assert Path(z.path).stat().st_size > 1000


def test_youtube_url_inspect_validation():
    try:
        inspect_youtube_url(InspectRequest(url="not_a_valid_url"))
        assert False, "Expected 422 HTTPException"
    except Exception as e:
        assert "422" in str(e)


def test_delete_job():
    dummy = output_root() / "_test_dummy_job_stage3"
    dummy.mkdir(parents=True, exist_ok=True)
    (dummy / "job.json").write_text("{}")
    assert dummy.exists()
    res = delete_job("_test_dummy_job_stage3")
    assert res["status"] == "deleted"
    assert not dummy.exists()


if __name__ == "__main__":
    test_system_info()
    test_dashboard_stats()
    test_list_jobs()
    test_poster_thumbnail_generation()
    test_zip_bundle_export()
    test_youtube_url_inspect_validation()
    test_delete_job()
    print("\n>>> ALL STAGE 3 TESTS PASSED CLEANLY! <<<")
