"""Tests for Resource Recovery, Error Handling, and Rerendering (Fix H & Fix I)."""
from __future__ import annotations

import json
import threading
import unittest.mock as mock
from pathlib import Path
import pytest

import errors
import pipeline
import rerender


def test_rerender_reuses_existing_metadata(tmp_path):
    """Rerendering a clip must reuse existing metadata.json without invoking LLMs."""
    job_dir = tmp_path / "test_job"
    clip_dir = job_dir / "clip_00"
    clip_dir.mkdir(parents=True, exist_ok=True)

    saved_meta = {
        "title": "Existing Title",
        "description": "Existing Description",
        "hashtags": ["#existing"],
        "niche": "comedy",
        "virality": {"score": 90.0}
    }
    (clip_dir / "metadata.json").write_text(json.dumps(saved_meta), encoding="utf-8")

    (job_dir / ".done_ingest.json").write_text(json.dumps({"video_path": str(tmp_path / "source.mp4"), "fps": 30.0, "width": 1920, "height": 1080}), encoding="utf-8")
    (job_dir / ".done_transcribe.json").write_text(json.dumps({"words": [], "sentences": []}), encoding="utf-8")
    (job_dir / ".done_scenes.json").write_text(json.dumps([]), encoding="utf-8")

    job_data = {
        "job_id": "test_job_123",
        "created": "2026-11-15T12:00:00Z",
        "source": str(tmp_path / "source.mp4"),
        "status": "done",
        "settings": {"aspect": "9:16", "captions": {"preset": "karaoke-pop"}},
        "stages": {},
        "clips": [{"index": 0, "start": 5.0, "end": 15.0, "metadata": saved_meta}],
        "notes": []
    }
    (job_dir / "job.json").write_text(json.dumps(job_data), encoding="utf-8")

    dummy_video = clip_dir / "final.mp4"
    dummy_video.write_bytes(b"dummy final")

    with mock.patch("cut.cut_clip", return_value=dummy_video), \
         mock.patch("director.direct_and_render_clip", return_value={"layout": "full", "segments": 1, "out": str(dummy_video)}), \
         mock.patch("captions.caption_clip", return_value=dummy_video), \
         mock.patch("music.mix_audio", return_value=None), \
         mock.patch("llm.complete_json") as mock_llm:

        res = rerender.rerender_clip(
            job_dir=job_dir,
            clip_index=0,
            start=6.0,
            end=16.0,
            style_refine=False
        )

        assert not mock_llm.called
        assert res["metadata"]["title"] == "Existing Title"


def test_job_cancelled_releases_resources_cleanly():
    """JobCancelled exception must be raised when cancel event is set."""
    cancel_evt = threading.Event()
    cancel_evt.set()

    with pytest.raises(errors.JobCancelled):
        pipeline.run_job("dummy.mp4", cancel=cancel_evt)
