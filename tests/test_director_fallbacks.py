"""Tests for Stage 8.1 Director Layout Rendering, Fallbacks, and Safety."""
from __future__ import annotations

import unittest.mock as mock
from pathlib import Path
import pytest

import director
from errors import ReframeError


def test_director_build_segment_filter_all_layouts():
    """Verify filtergraph construction for all 5 layout modes produces valid FFmpeg filters."""
    seg_full = director.DirectorSegment(0.0, 5.0, director.LAYOUT_FULL, target_a={"cx": 960.0, "cy": 540.0})
    f_full = director._build_segment_filter(seg_full, 1920, 1080, 1080, 1920)
    assert "crop=" in f_full
    assert "scale=1080:1920" in f_full

    seg_tb = director.DirectorSegment(0.0, 5.0, director.LAYOUT_SPLIT_TB,
                                      target_a={"cx": 400.0, "cy": 500.0},
                                      target_b={"cx": 1400.0, "cy": 500.0})
    f_tb = director._build_segment_filter(seg_tb, 1920, 1080, 1080, 1920)
    assert "split=2" in f_tb
    assert "vstack=inputs=2" in f_tb
    assert "scale=1080:960" in f_tb

    seg_lr = director.DirectorSegment(0.0, 5.0, director.LAYOUT_SPLIT_LR,
                                      target_a={"cx": 400.0, "cy": 500.0},
                                      target_b={"cx": 1400.0, "cy": 500.0})
    f_lr = director._build_segment_filter(seg_lr, 1920, 1080, 1080, 1920)
    assert "split=2" in f_lr
    assert "hstack=inputs=2" in f_lr
    assert "scale=540:1920" in f_lr

    seg_wide = director.DirectorSegment(0.0, 5.0, director.LAYOUT_WIDE)
    f_wide = director._build_segment_filter(seg_wide, 1920, 1080, 1080, 1920)
    assert "boxblur=" in f_wide
    assert "overlay=" in f_wide

    seg_two = director.DirectorSegment(0.0, 5.0, director.LAYOUT_TWO_PERSON, target_a={"cx": 960.0, "cy": 540.0})
    f_two = director._build_segment_filter(seg_two, 1920, 1080, 1080, 1920)
    assert "crop=" in f_two
    assert "scale=1080:1920" in f_two


def test_director_portrait_source_geometry_safety():
    """Verify portrait source video (1080x1920) does not produce invalid crop geometry."""
    seg = director.DirectorSegment(0.0, 5.0, director.LAYOUT_FULL, target_a={"cx": 540.0, "cy": 960.0})
    # sw=1080, sh=1920 (portrait source)
    f_str = director._build_segment_filter(seg, 1080, 1920, 1080, 1920)
    assert "crop=1080:1920" in f_str or "crop=1080:" in f_str


def test_director_render_clip_handles_video_without_audio(tmp_path):
    """Verify that render_director_clip safely uses -an when source has no audio."""
    src = tmp_path / "silent.mp4"
    src.write_bytes(b"dummy")
    out = tmp_path / "out.mp4"

    seg = director.DirectorSegment(0.0, 5.0, director.LAYOUT_FULL, target_a={"cx": 960.0, "cy": 540.0})
    cfg = {
        "render": {
            "compute": "cpu",
            "video_codec": "libx264",
            "crf": 23,
            "preset_intermediate": "ultrafast",
            "preset_final": "medium"
        }
    }

    with mock.patch("director.probe", return_value={"width": 1920, "height": 1080, "fps": 30.0, "has_audio": False}):
        with mock.patch("director.run_ffmpeg") as mock_ff:
            # Mock file creation
            def fake_ff(cmd):
                out.write_bytes(b"rendered")
            mock_ff.side_effect = fake_ff

            res = director.render_director_clip(src, 0.0, 5.0, [seg], out, cfg)
            assert res is not None
            assert out.exists()
            # Verify -an was passed in ffmpeg args because has_audio is False
            cmd_args = mock_ff.call_args[0][0]
            assert "-an" in cmd_args


def test_director_cleanup_temporary_files_on_failure(tmp_path):
    """Verify that temp directory is cleaned up in finally block when FFmpeg fails."""
    src = tmp_path / "video.mp4"
    src.write_bytes(b"dummy")
    out = tmp_path / "out.mp4"

    seg1 = director.DirectorSegment(0.0, 3.0, director.LAYOUT_FULL)
    seg2 = director.DirectorSegment(3.0, 6.0, director.LAYOUT_SPLIT_TB)

    cfg = {
        "render": {
            "compute": "cpu",
            "video_codec": "libx264",
            "crf": 23,
            "preset_intermediate": "ultrafast",
            "preset_final": "medium"
        }
    }

    with mock.patch("director.probe", return_value={"width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}):
        with mock.patch("director.run_ffmpeg", side_effect=RuntimeError("FFmpeg error")):
            with pytest.raises(Exception):
                director.render_director_clip(src, 0.0, 6.0, [seg1, seg2], out, cfg)

    # Temporary directory should be cleaned up
    tmp_dirs = list(tmp_path.glob("_director_tmp_*"))
    assert len(tmp_dirs) == 0
