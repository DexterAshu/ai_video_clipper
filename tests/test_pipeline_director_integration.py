"""Tests for Stage 8.1 Pipeline and Director Integration."""
from __future__ import annotations

import unittest.mock as mock
from pathlib import Path
import pytest

import pipeline


def test_pipeline_render_one_invokes_smart_director(tmp_path):
    """Verify that pipeline._render_one invokes director.direct_and_render_clip for 9:16 clips."""
    cand = {"start": 10.0, "end": 25.0, "score": 85, "hook": "Test Hook"}
    info = {"video_path": str(tmp_path / "source.mp4"), "width": 1920, "height": 1080, "fps": 30.0}
    transcript = {
        "text": "Hello world this is a test.",
        "words": [{"word": "Hello", "start": 11.0, "end": 12.0}],
        "segments": [{"start": 10.5, "end": 24.5, "text": "Hello world"}]
    }
    cfg = {
        "reframe": {"framing_mode": "smart_director"},
        "captions": {"enabled": False, "preset": "none"},
        "render": {"audio_bitrate": "128k"},
        "audio": {"mix_ducking": False, "loudnorm": False}
    }

    dummy_final = tmp_path / "clip_00" / "final.mp4"
    dummy_final.parent.mkdir(parents=True, exist_ok=True)
    dummy_final.write_bytes(b"dummy video")

    cut_mod = mock.MagicMock()
    reframe_mod = mock.MagicMock()
    captions_mod = mock.MagicMock()
    captions_mod.caption_clip.return_value = dummy_final
    captions_mod.cta_from_cfg.return_value = {"cta": None, "captions_enabled": False}
    metadata_mod = mock.MagicMock()
    metadata_mod.generate_metadata.return_value = {"title": "Test Title", "description": "Test Desc", "hashtags": []}
    scenes_mod = mock.MagicMock()
    scenes_mod.scene_cuts_in_range.return_value = []

    with mock.patch("director.direct_and_render_clip") as mock_dir:
        mock_dir.return_value = {"layout": "dynamic_director", "segments": 2, "out": str(dummy_final)}
        with mock.patch("music.mix_audio", return_value=None):
            res = pipeline._render_one(
                i=0, cand=cand, info=info, transcript=transcript, scene_data=[],
                job_dir=tmp_path, cfg=cfg, provider="ollama", preset="none",
                aspect="9:16", debug_dir=None, cut_mod=cut_mod,
                reframe_mod=reframe_mod, captions_mod=captions_mod,
                metadata_mod=metadata_mod, scenes_mod=scenes_mod
            )

            # Director must be called with clip boundaries and transcript
            assert mock_dir.called
            call_kwargs = mock_dir.call_args.kwargs
            assert call_kwargs["clip_start"] == 10.0
            assert call_kwargs["clip_end"] == 25.0
            assert call_kwargs["framing_mode"] == "smart_director"
            assert call_kwargs["transcript"] == transcript

            # Legacy reframe_clip must NOT be called when director succeeds
            assert not reframe_mod.reframe_clip.called


def test_pipeline_render_one_falls_back_when_director_fails(tmp_path):
    """Verify that pipeline._render_one falls back cleanly to reframe_clip if director fails."""
    cand = {"start": 5.0, "end": 15.0, "score": 75, "hook": "Fallback Hook"}
    info = {"video_path": str(tmp_path / "source.mp4"), "width": 1920, "height": 1080, "fps": 30.0}
    transcript = {"text": "Test speech", "words": [], "segments": []}
    cfg = {
        "reframe": {"framing_mode": "smart_director"},
        "captions": {"enabled": False, "preset": "none"},
        "render": {"audio_bitrate": "128k"},
        "audio": {"mix_ducking": False, "loudnorm": False}
    }

    dummy_final = tmp_path / "clip_00" / "final.mp4"
    dummy_final.parent.mkdir(parents=True, exist_ok=True)
    dummy_final.write_bytes(b"dummy video")

    cut_mod = mock.MagicMock()
    reframe_mod = mock.MagicMock()
    reframe_mod.reframe_clip.return_value = {"aspect": "9:16", "crop": [0, 0, 1080, 1920]}
    captions_mod = mock.MagicMock()
    captions_mod.caption_clip.return_value = dummy_final
    captions_mod.cta_from_cfg.return_value = {"cta": None, "captions_enabled": False}
    metadata_mod = mock.MagicMock()
    metadata_mod.generate_metadata.return_value = {"title": "Test Title", "description": "Test Desc", "hashtags": []}
    scenes_mod = mock.MagicMock()
    scenes_mod.scene_cuts_in_range.return_value = []

    with mock.patch("director.direct_and_render_clip", side_effect=RuntimeError("FFmpeg error on layout")):
        with mock.patch("music.mix_audio", return_value=None):
            res = pipeline._render_one(
                i=0, cand=cand, info=info, transcript=transcript, scene_data=[],
                job_dir=tmp_path, cfg=cfg, provider="ollama", preset="none",
                aspect="9:16", debug_dir=None, cut_mod=cut_mod,
                reframe_mod=reframe_mod, captions_mod=captions_mod,
                metadata_mod=metadata_mod, scenes_mod=scenes_mod
            )

            # Reframe fallback must have been invoked
            assert reframe_mod.reframe_clip.called
            # The job must complete successfully without crashing
            assert res is not None
            assert res["index"] == 0


def test_pipeline_render_one_honors_manual_layout_override(tmp_path):
    """Verify that manual framing_mode in edit_plan is passed to director."""
    cand = {"start": 0.0, "end": 10.0, "score": 80, "hook": "Manual Hook"}
    info = {"video_path": str(tmp_path / "source.mp4"), "width": 1920, "height": 1080, "fps": 30.0}
    edit_plan = {
        "segments": [[0.0, 10.0]],
        "output_duration": 10.0,
        "total_ms_removed": 0,
        "start_action": "keep",
        "ending_action": "keep",
        "flags": [],
        "framing_mode": "split_tb",
        "existing_subs": {"decision": "none", "bottom_exclusion_ratio": 0.0, "h_bias_center": -1.0},
        "caption_anchor": 0.6,
        "cta": {"enabled": False},
        "captions_enabled": False,
        "fades": None,
        "zoom_punch": False,
        "words": []
    }
    cfg = {
        "reframe": {},
        "captions": {"enabled": False, "preset": "none"},
        "render": {"audio_bitrate": "128k"},
        "audio": {"mix_ducking": False, "loudnorm": False}
    }

    dummy_final = tmp_path / "clip_00" / "final.mp4"
    dummy_final.parent.mkdir(parents=True, exist_ok=True)
    dummy_final.write_bytes(b"dummy video")

    cut_mod = mock.MagicMock()
    reframe_mod = mock.MagicMock()
    captions_mod = mock.MagicMock()
    captions_mod.caption_clip.return_value = dummy_final
    metadata_mod = mock.MagicMock()
    metadata_mod.generate_metadata.return_value = {"title": "Title", "description": "Desc", "hashtags": []}
    scenes_mod = mock.MagicMock()

    with mock.patch("director.direct_and_render_clip") as mock_dir:
        mock_dir.return_value = {"layout": "split_tb", "segments": 1, "out": str(dummy_final)}
        with mock.patch("music.mix_audio", return_value=None):
            res = pipeline._render_one(
                i=0, cand=cand, info=info, transcript={"words": []}, scene_data=[],
                job_dir=tmp_path, cfg=cfg, provider="ollama", preset="none",
                aspect="9:16", debug_dir=None, cut_mod=cut_mod,
                reframe_mod=reframe_mod, captions_mod=captions_mod,
                metadata_mod=metadata_mod, scenes_mod=scenes_mod,
                edit_plan=edit_plan
            )

            assert mock_dir.called
            assert mock_dir.call_args.kwargs["framing_mode"] == "split_tb"
