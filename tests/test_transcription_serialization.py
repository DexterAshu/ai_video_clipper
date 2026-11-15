"""Tests for Whisper Transcription Serialization and CPU Fallback (Fix C)."""
from __future__ import annotations

import unittest.mock as mock
import pytest

import transcribe


def test_build_model_caches_and_reuses_instances():
    """_build_model must cache loaded models by (model_name, device, compute_type)."""
    cfg = {
        "whisper": {
            "gpu": {"model": "tiny", "compute_type": "float16"},
            "cpu": {"model": "tiny", "compute_type": "int8"},
            "model_dir": "cache/models"
        },
        "render": {"compute": "cpu"}
    }

    dummy_model = mock.MagicMock()
    with mock.patch("faster_whisper.WhisperModel", return_value=dummy_model) as mock_wm:
        # Clear cache for isolated test
        transcribe._model_cache.clear()

        m1, name1, dev1, comp1 = transcribe._build_model(cfg, device_override="cpu", compute_override="int8")
        assert mock_wm.call_count == 1

        # Second call with same params must return cached model
        m2, name2, dev2, comp2 = transcribe._build_model(cfg, device_override="cpu", compute_override="int8")
        assert mock_wm.call_count == 1
        assert m1 is m2


def test_repair_clip_transcription_uses_cpu():
    """repair_clip_transcription must default to CPU int8 compute."""
    cfg = {
        "whisper": {
            "cpu": {"model": "small", "compute_type": "int8"},
            "model_dir": "cache/models"
        },
        "transcription": {"fallback_device": "cpu", "fallback_compute_type": "int8"}
    }

    with mock.patch("transcribe.transcribe") as mock_tr:
        mock_tr.return_value = {"words": [{"word": "repaired", "start": 1.0, "end": 2.0}]}
        words = transcribe.repair_clip_transcription(
            audio_path="dummy.wav",
            start_s=5.0,
            end_s=15.0,
            cfg=cfg,
            prefer_cpu=True
        )

        assert mock_tr.called
        kwargs = mock_tr.call_args.kwargs
        assert kwargs["device_override"] == "cpu"
        assert kwargs["compute_override"] == "int8"
        assert len(words) == 1
