"""Tests for Dedicated AI Preparation Phase (Fix B & Fix C)."""
from __future__ import annotations

import json
import unittest.mock as mock
from pathlib import Path
import pytest

import pipeline


def test_ai_prep_generates_metadata_virality_and_niche_before_render(tmp_path):
    """Verify that _prepare_clip_ai_sequentially generates metadata, virality, and niche."""
    cands = [
        {"start": 10.0, "end": 25.0, "hook": "Hook 1", "score": 80.0},
        {"start": 30.0, "end": 45.0, "hook": "Hook 2", "score": 85.0},
    ]
    info = {"video_path": str(tmp_path / "vid.mp4"), "audio_path": str(tmp_path / "audio.wav")}
    transcript = {
        "text": "sample text",
        "words": [
            {"word": "hello", "start": 11.0, "end": 12.0},
            {"word": "world", "start": 12.1, "end": 13.0},
            {"word": "second", "start": 31.0, "end": 32.0},
            {"word": "clip", "start": 32.1, "end": 33.0},
        ]
    }
    cfg = {
        "metadata": {"enabled": True},
        "captions": {"enabled": True},
        "transcription": {"fallback_device": "cpu"},
    }

    mock_metadata = mock.MagicMock()
    mock_metadata.generate_metadata.side_effect = [
        {"title": "Title 1", "description": "Desc 1", "hashtags": ["#t1"]},
        {"title": "Title 2", "description": "Desc 2", "hashtags": ["#t2"]},
    ]

    with mock.patch("virality.rate_virality", return_value={"score": 82.0, "reasons": ["strong"]}) as mock_vir, \
         mock.patch("classify.classify_niche", return_value="education") as mock_niche:
        prepared = pipeline._prepare_clip_ai_sequentially(
            candidates=cands,
            info=info,
            transcript=transcript,
            scene_data=[],
            edit_plans=None,
            events=[],
            cfg=cfg,
            provider="mock",
            caption_language="",
            job_dir=tmp_path,
            metadata_mod=mock_metadata
        )

        assert len(prepared) == 2
        assert prepared[0]["meta"]["title"] == "Title 1"
        assert prepared[0]["virality"]["score"] == 82.0
        assert prepared[0]["niche"] == "education"
        assert len(prepared[0]["words"]) == 2

        assert prepared[1]["meta"]["title"] == "Title 2"
        assert prepared[1]["virality"]["score"] == 82.0
        assert prepared[1]["niche"] == "education"
        assert len(prepared[1]["words"]) == 2

        # Verify prepared_ai.json was persisted
        p0 = tmp_path / "clip_00" / "prepared_ai.json"
        assert p0.exists()
        saved = json.loads(p0.read_text(encoding="utf-8"))
        assert saved["meta"]["title"] == "Title 1"


def test_ai_prep_repairs_missing_timings_on_cpu(tmp_path):
    """When a clip has no words, missing timing repair is triggered sequentially on CPU."""
    cands = [{"start": 50.0, "end": 70.0, "hook": "Silent Hook", "score": 70.0}]
    info = {"video_path": str(tmp_path / "vid.mp4"), "audio_path": str(tmp_path / "audio.wav")}
    transcript = {"text": "empty words", "words": []}  # No words overlapping 50..70
    cfg = {
        "metadata": {"enabled": True},
        "captions": {"enabled": True},
        "transcription": {"fallback_device": "cpu", "fallback_compute_type": "int8"},
    }

    mock_metadata = mock.MagicMock()
    mock_metadata.generate_metadata.return_value = {"title": "T", "description": "D", "hashtags": []}

    repaired_words = [
        {"word": "recovered", "start": 51.0, "end": 52.0},
        {"word": "speech", "start": 52.2, "end": 53.0},
    ]

    with mock.patch("transcribe.repair_clip_transcription", return_value=repaired_words) as mock_repair, \
         mock.patch("virality.rate_virality", return_value={"score": 75.0, "reasons": []}), \
         mock.patch("classify.classify_niche", return_value="general"):
        prepared = pipeline._prepare_clip_ai_sequentially(
            candidates=cands,
            info=info,
            transcript=transcript,
            scene_data=[],
            edit_plans=None,
            events=[],
            cfg=cfg,
            provider="mock",
            caption_language="",
            job_dir=tmp_path,
            metadata_mod=mock_metadata
        )

        assert mock_repair.called
        assert mock_repair.call_args.kwargs["prefer_cpu"] is True
        assert len(prepared[0]["words"]) == 2
        assert prepared[0]["words"][0]["word"] == "recovered"


def test_ai_prep_failure_falls_back_safely_without_blocking(tmp_path):
    """When metadata generation throws an exception, safe template metadata is used."""
    cands = [{"start": 0.0, "end": 10.0, "hook": "Crash Hook", "score": 60.0}]
    info = {"video_path": str(tmp_path / "vid.mp4"), "audio_path": str(tmp_path / "audio.wav")}
    transcript = {"text": "hello", "words": [{"word": "hello", "start": 1.0, "end": 2.0}]}
    cfg = {"metadata": {"enabled": True}, "captions": {"enabled": True}}

    mock_metadata = mock.MagicMock()
    mock_metadata.generate_metadata.side_effect = RuntimeError("Ollama connection refused")

    with mock.patch("virality.rate_virality", return_value={"score": 50.0, "reasons": []}), \
         mock.patch("classify.classify_niche", return_value="other"):
        prepared = pipeline._prepare_clip_ai_sequentially(
            candidates=cands,
            info=info,
            transcript=transcript,
            scene_data=[],
            edit_plans=None,
            events=[],
            cfg=cfg,
            provider="mock",
            caption_language="",
            job_dir=tmp_path,
            metadata_mod=mock_metadata
        )

        assert len(prepared) == 1
        assert "title" in prepared[0]["meta"]
        assert prepared[0]["meta"]["title"] != ""
