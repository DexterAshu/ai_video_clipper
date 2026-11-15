"""Tests for Ranking Engine Auditing, Eligibility Gating, and Explainable Selection."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

import highlights


def test_ranking_rejects_clip_below_minimum_duration(tmp_path):
    """Clips under min_seconds (e.g. 0.76s) must be marked ineligible and never kept."""
    valid_file = tmp_path / "valid.mp4"
    valid_file.write_bytes(b"x" * 2000)

    broken_file = tmp_path / "broken.mp4"
    broken_file.write_bytes(b"x" * 2000)

    clips = [
        {
            "index": 0,
            "start": 0.0,
            "end": 30.0,
            "duration": 30.0,
            "path": str(valid_file),
            "hook": "Valid Clip",
            "candidate_score": 8.0,
            "virality": {"score": 80.0},
            "text": "Hello world this is a real clip."
        },
        {
            "index": 1,
            "start": 40.0,
            "end": 40.76,
            "duration": 0.76,  # Collapsed fragment!
            "path": str(broken_file),
            "hook": "Broken Fragment",
            "candidate_score": 9.0,
            "virality": {"score": 95.0},
            "text": "Too short"
        }
    ]

    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "max_seconds": 60.0,
            "min_keep": 1,
            "keep_ratio": 1.0,
            "rescore_weights": {"hook_strength": 0.25, "retention": 0.25, "clarity": 0.25, "impact": 0.25}
        }
    }

    ranked = highlights.rescore_clips(clips, transcript={"words": []}, cfg=cfg, provider="mock")

    c0 = next(c for c in ranked if c["index"] == 0)
    c1 = next(c for c in ranked if c["index"] == 1)

    assert c0["eligible"] is True
    assert c0["kept"] is True
    assert c0["rank"] == 1

    assert c1["eligible"] is False
    assert c1["kept"] is False
    assert c1["rank"] is None
    assert any("duration_below_minimum" in r for r in c1["rejection_reasons"])


def test_ranking_rejects_missing_output_file(tmp_path):
    """Clips with missing media output files must be rejected."""
    missing_file = tmp_path / "nonexistent.mp4"

    clips = [{
        "index": 0,
        "start": 0.0,
        "end": 20.0,
        "duration": 20.0,
        "path": str(missing_file),
        "hook": "Missing File Clip",
        "candidate_score": 8.0,
    }]

    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "min_keep": 1,
            "keep_ratio": 1.0,
            "rescore_weights": {"hook_strength": 0.25, "retention": 0.25, "clarity": 0.25, "impact": 0.25}
        }
    }

    ranked = highlights.rescore_clips(clips, transcript={"words": []}, cfg=cfg, provider="mock")
    c0 = ranked[0]
    assert c0["eligible"] is False
    assert c0["kept"] is False
    assert any("rendered_output_file_missing" in r for r in c0["rejection_reasons"])


def test_ranking_resolves_text_from_clip_words_over_incomplete_transcript(tmp_path):
    """Clip words recovered during Phase A must be used for scoring even if transcript is empty."""
    valid_file = tmp_path / "valid.mp4"
    valid_file.write_bytes(b"x" * 2000)

    clips = [{
        "index": 0,
        "start": 100.0,
        "end": 130.0,
        "duration": 30.0,
        "path": str(valid_file),
        "hook": "Repaired Clip",
        "candidate_score": 7.0,
        "words": [{"word": "recovered"}, {"word": "speech"}, {"word": "content"}]
    }]

    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "min_keep": 1,
            "keep_ratio": 1.0,
            "rescore_weights": {"hook_strength": 0.25, "retention": 0.25, "clarity": 0.25, "impact": 0.25}
        }
    }

    # Master transcript has NO words in range [100, 130]
    ranked = highlights.rescore_clips(clips, transcript={"words": []}, cfg=cfg, provider="mock")
    c0 = ranked[0]
    assert c0["resolved_text"] == "recovered speech content"


def test_ranking_deterministic_tie_breaking(tmp_path):
    """Clips with identical LLM scores must break ties deterministically."""
    f1 = tmp_path / "c1.mp4"
    f1.write_bytes(b"x" * 2000)
    f2 = tmp_path / "c2.mp4"
    f2.write_bytes(b"x" * 2000)

    clips = [
        {
            "index": 1,
            "start": 0.0,
            "end": 35.0,
            "duration": 35.0,  # Sweet spot!
            "path": str(f1),
            "hook": "Clip A",
            "candidate_score": 8.0,
            "virality": {"score": 80.0},
            "text": "same transcript content"
        },
        {
            "index": 2,
            "start": 50.0,
            "end": 100.0,
            "duration": 50.0,  # Farther from sweet spot
            "path": str(f2),
            "hook": "Clip B",
            "candidate_score": 7.0,
            "virality": {"score": 70.0},
            "text": "same transcript content"
        }
    ]

    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "min_keep": 1,
            "keep_ratio": 0.5,
            "rescore_weights": {"hook_strength": 0.25, "retention": 0.25, "clarity": 0.25, "impact": 0.25}
        }
    }

    ranked = highlights.rescore_clips(clips, transcript={"words": []}, cfg=cfg, provider="mock", target_count=1)

    assert ranked[0]["index"] == 1
    assert ranked[0]["kept"] is True
    assert ranked[1]["index"] == 2
    assert ranked[1]["kept"] is False
    assert any("dropped_by_quota" in r for r in ranked[1]["rejection_reasons"])
