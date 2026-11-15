"""Tests ensuring zero LLM or CUDA Whisper operations execute during clip rendering (Fix B)."""
from __future__ import annotations

import json
import unittest.mock as mock
from pathlib import Path
import pytest

import pipeline


def test_render_one_makes_no_llm_or_transcribe_calls_when_prepared(tmp_path):
    """When prepared_ai is passed to _render_one, zero LLM or Whisper calls must occur."""
    cand = {"start": 10.0, "end": 20.0, "hook": "Test Hook", "score": 80.0}
    info = {"video_path": str(tmp_path / "vid.mp4"), "width": 1920, "height": 1080, "fps": 30.0}
    transcript = {"text": "dummy", "words": []}

    prepared_ai = {
        "words": [{"word": "sample", "start": 1.0, "end": 2.0}],
        "meta": {"title": "Prepared Title", "description": "Prepared Desc", "hashtags": ["#test"]},
        "virality": {"score": 88.0, "breakdown": {}, "reasons": ["great hook"]},
        "niche": "technology",
        "duration": 10.0,
        "out_start": 10.0,
        "out_end": 20.0,
    }

    dummy_final = tmp_path / "clip_00" / "final.mp4"
    dummy_final.parent.mkdir(parents=True, exist_ok=True)
    dummy_final.write_bytes(b"dummy final")

    cut_mod = mock.MagicMock()
    reframe_mod = mock.MagicMock()
    reframe_mod.probe.return_value = {"duration": 10.0, "width": 1080, "height": 1920}
    captions_mod = mock.MagicMock()
    captions_mod.caption_clip.return_value = dummy_final
    metadata_mod = mock.MagicMock()
    scenes_mod = mock.MagicMock()
    scenes_mod.scene_cuts_in_range.return_value = []

    cfg = {
        "reframe": {"framing_mode": "smart_director"},
        "captions": {"enabled": True, "preset": "karaoke-pop"},
        "render": {"audio_bitrate": "128k"},
        "audio": {"mix_ducking": False, "loudnorm": False},
        "metadata": {"enabled": True},
    }

    with mock.patch("director.direct_and_render_clip", return_value={"layout": "full", "segments": 1, "out": str(dummy_final)}), \
         mock.patch("music.mix_audio", return_value=None), \
         mock.patch("llm.complete_json") as mock_llm_complete, \
         mock.patch("llm_manager.query_ollama") as mock_query_ollama, \
         mock.patch("transcribe.transcribe") as mock_transcribe, \
         mock.patch("classify.classify_niche") as mock_classify:

        res = pipeline._render_one(
            i=0, cand=cand, info=info, transcript=transcript, scene_data=[],
            job_dir=tmp_path, cfg=cfg, provider="ollama", preset="karaoke-pop",
            aspect="9:16", debug_dir=None, cut_mod=cut_mod,
            reframe_mod=reframe_mod, captions_mod=captions_mod,
            metadata_mod=metadata_mod, scenes_mod=scenes_mod,
            prepared_ai=prepared_ai
        )

        # Assert zero LLM or transcription calls inside _render_one
        assert not mock_llm_complete.called, "llm.complete_json must not be called inside _render_one"
        assert not mock_query_ollama.called, "llm_manager.query_ollama must not be called inside _render_one"
        assert not mock_transcribe.called, "transcribe.transcribe must not be called inside _render_one"
        assert not metadata_mod.generate_metadata.called, "generate_metadata must not be called inside _render_one"
        assert not mock_classify.called, "classify_niche must not be called inside _render_one"

        # Output metadata must reflect the prepared data
        assert res["metadata"]["title"] == "Prepared Title"
        assert res["virality"]["score"] == 88.0
        assert res["niche"] == "technology"

        # Final metadata.json on disk must contain the prepared AI data
        meta_on_disk = json.loads((tmp_path / "clip_00" / "metadata.json").read_text(encoding="utf-8"))
        assert meta_on_disk["title"] == "Prepared Title"
        assert meta_on_disk["niche"] == "technology"
