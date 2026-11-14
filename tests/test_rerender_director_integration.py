"""Tests for Stage 8.1 Rerender and Director Integration."""
from __future__ import annotations

import json
import unittest.mock as mock
from pathlib import Path
import pytest

import rerender


def _setup_job_dir(job_dir: Path):
    clip_dir = job_dir / "clip_00"
    clip_dir.mkdir(parents=True, exist_ok=True)

    job_data = {
        "job_id": "test_job_01",
        "created": "2026-11-14T00:00:00Z",
        "source": str(job_dir / "source.mp4"),
        "status": "done",
        "settings": {"aspect": "9:16"},
        "stages": {},
        "clips": [{
            "index": 0, "start": 5.0, "end": 15.0, "aspect": "9:16",
            "hook": "Test Hook", "title": "Clip 1", "description": "Test",
            "hashtags": [], "kept": True, "score": 80
        }]
    }
    (job_dir / "job.json").write_text(json.dumps(job_data), encoding="utf-8")

    info = {"video_path": str(job_dir / "source.mp4"), "width": 1920, "height": 1080, "fps": 30.0}
    (job_dir / ".done_ingest.json").write_text(json.dumps(info), encoding="utf-8")

    transcript = {
        "text": "test transcript",
        "words": [],
        "segments": [],
        "sentences": [{"start": 5.0, "end": 15.0, "text": "test sentence"}]
    }
    (job_dir / ".done_transcribe.json").write_text(json.dumps(transcript), encoding="utf-8")

    scenes = {"scenes": [{"start": 0.0}]}
    (job_dir / ".done_scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
    return clip_dir


def test_rerender_invokes_smart_director(tmp_path):
    """Verify that rerender_clip uses director.direct_and_render_clip for smart_director."""
    job_dir = tmp_path / "job_01"
    clip_dir = _setup_job_dir(job_dir)

    studio_plan = {"framing_mode": "smart_director"}
    (clip_dir / "edit_plan.json").write_text(json.dumps(studio_plan), encoding="utf-8")

    dummy_reframed = clip_dir / "reframed.mp4"
    dummy_reframed.write_bytes(b"dummy")

    dummy_final = clip_dir / "final.mp4"
    dummy_final.write_bytes(b"dummy")

    with mock.patch("director.direct_and_render_clip") as mock_dir:
        mock_dir.return_value = {"layout": "dynamic_director", "segments": 1, "out": str(dummy_reframed)}
        with mock.patch("rerender.load_config", return_value={"render": {}, "captions": {"enabled": False, "preset": "none"}, "audio": {}}):
            with mock.patch("ffutil.probe", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}):
                with mock.patch("cut.cut_clip"):
                    with mock.patch("captions.caption_clip", return_value=dummy_final):
                        with mock.patch("music.mix_audio", return_value=None):
                            with mock.patch("schemas.validate"):
                                res = rerender.rerender_clip(job_dir, clip_index=0, start=5.0, end=15.0, style_refine=False)

                                assert mock_dir.called
                                assert mock_dir.call_args.kwargs["framing_mode"] == "smart_director"
                                assert mock_dir.call_args.kwargs["clip_start"] == 5.0
                                assert mock_dir.call_args.kwargs["clip_end"] == 15.0


def test_rerender_manual_layout_mode(tmp_path):
    """Verify that manual layout override (split_tb) in edit_plan.json is executed."""
    job_dir = tmp_path / "job_01"
    clip_dir = _setup_job_dir(job_dir)

    studio_plan = {"framing_mode": "split_tb"}
    (clip_dir / "edit_plan.json").write_text(json.dumps(studio_plan), encoding="utf-8")

    dummy_final = clip_dir / "final.mp4"
    dummy_final.write_bytes(b"dummy")

    with mock.patch("director.direct_and_render_clip") as mock_dir:
        mock_dir.return_value = {"layout": "split_tb", "segments": 1, "out": str(clip_dir / "reframed.mp4")}
        with mock.patch("rerender.load_config", return_value={"render": {}, "captions": {"enabled": False, "preset": "none"}, "audio": {}}):
            with mock.patch("ffutil.probe", return_value={"duration": 12.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}):
                with mock.patch("captions.caption_clip", return_value=dummy_final):
                    with mock.patch("music.mix_audio", return_value=None):
                        with mock.patch("schemas.validate"):
                            res = rerender.rerender_clip(job_dir, clip_index=0, start=5.0, end=15.0, style_refine=False)

                            assert mock_dir.called
                            assert mock_dir.call_args.kwargs["framing_mode"] == "split_tb"


def test_rerender_unsupported_framing_mode_raises_error(tmp_path):
    """Verify that unsupported framing mode raises a clear ValueError instead of silent wrong layout."""
    job_dir = tmp_path / "job_01"
    clip_dir = _setup_job_dir(job_dir)

    studio_plan = {"framing_mode": "invalid_mode_xyz"}
    (clip_dir / "edit_plan.json").write_text(json.dumps(studio_plan), encoding="utf-8")

    with mock.patch("rerender.load_config", return_value={"render": {}, "captions": {"enabled": False, "preset": "none"}, "audio": {}}):
        with mock.patch("ffutil.probe", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}):
            with pytest.raises(ValueError, match="Unsupported framing mode"):
                rerender.rerender_clip(job_dir, clip_index=0, start=5.0, end=15.0, style_refine=False)


def test_rerender_falls_back_to_reframe_on_director_error(tmp_path):
    """Verify that rerender falls back to reframe_clip if director rendering encounters an error."""
    job_dir = tmp_path / "job_01"
    clip_dir = _setup_job_dir(job_dir)

    studio_plan = {"framing_mode": "smart_director"}
    (clip_dir / "edit_plan.json").write_text(json.dumps(studio_plan), encoding="utf-8")

    dummy_final = clip_dir / "final.mp4"
    dummy_final.write_bytes(b"dummy")

    with mock.patch("director.direct_and_render_clip", side_effect=RuntimeError("FFmpeg director failed")):
        with mock.patch("reframe.reframe_clip", return_value={"aspect": "9:16", "crop": [0, 0, 1080, 1920]}) as mock_reframe:
            with mock.patch("rerender.load_config", return_value={"render": {}, "captions": {"enabled": False, "preset": "none"}, "audio": {}}):
                with mock.patch("ffutil.probe", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}):
                    with mock.patch("captions.caption_clip", return_value=dummy_final):
                        with mock.patch("music.mix_audio", return_value=None):
                            with mock.patch("schemas.validate"):
                                res = rerender.rerender_clip(job_dir, clip_index=0, start=5.0, end=15.0, style_refine=False)

                                assert mock_reframe.called
                                assert res is not None
