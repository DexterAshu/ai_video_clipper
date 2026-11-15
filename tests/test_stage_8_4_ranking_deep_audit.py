"""Stage 8.4 Deep Ranking Engine Audit & Ground Truth Verification.

Tests:
1. Malformed duration, index, and end values (None, strings, NaN, Inf, negative).
2. Corrupted nested virality objects (floats, strings, None, missing keys).
3. Bulletproof _slice_text resilience against None and malformed transcripts.
4. Comprehensive ranking explainability schema compliance.
5. Ground-truth relative ranking ordering across strong vs mediocre vs weak clips.
6. Configurable composite weight formula verification.
7. Ineligible candidate penalty guarantees (composite_score == 0.0, rank == None).
"""
import math
import pytest
from pathlib import Path

from highlights import rescore_clips, _slice_text, _safe_num
import config


def test_ranking_malformed_duration_and_index_types(tmp_path):
    """Ensure malformed duration and index types never crash rescore_clips."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        {
            "index": "clip_alpha",  # String index
            "start": 0.0,
            "end": 20.0,
            "duration": "invalid_dur",  # String duration
            "path": str(dummy_file),
            "text": "Valid speech content for clip alpha.",
        },
        {
            "index": None,  # None index
            "start": 25.0,
            "end": None,  # None end
            "duration": None,  # None duration
            "path": str(dummy_file),
            "text": "Valid speech content for clip beta.",
        },
        {
            "index": 3,
            "start": 50.0,
            "end": 70.0,
            "duration": float("nan"),  # NaN duration
            "path": str(dummy_file),
            "text": "Valid speech content for clip gamma.",
        },
    ]

    transcript = {"text": "dummy transcript", "words": []}
    ranked = rescore_clips(clips, transcript, cfg=cfg, provider="mock")

    assert len(ranked) == 3
    for c in ranked:
        assert "ranking" in c
        assert not math.isnan(c["weighted_score"])
        assert not math.isinf(c["weighted_score"])
        assert 0.0 <= c["weighted_score"] <= 10.0
        # Duration suitability is always a safe float between 0.0 and 10.0
        assert 0.0 <= c["ranking"]["duration_suitability"] <= 10.0


def test_ranking_corrupted_nested_virality_types(tmp_path):
    """Ensure non-dict virality fields (float, string, None, corrupted) don't throw AttributeError."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        {
            "index": 1,
            "start": 0.0,
            "end": 20.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "text": "Direct float virality.",
            "virality": 85.0,  # Legacy or direct float virality
        },
        {
            "index": 2,
            "start": 25.0,
            "end": 45.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "text": "String virality.",
            "virality": "high",  # String virality
        },
        {
            "index": 3,
            "start": 50.0,
            "end": 70.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "text": "Dict with NaN score.",
            "virality": {"score": float("nan")},
        },
    ]

    transcript = {"text": "dummy transcript", "words": []}
    ranked = rescore_clips(clips, transcript, cfg=cfg, provider="mock")

    assert len(ranked) == 3
    # Clip 1 with direct float 85.0 should normalize to 8.5
    c1 = next(c for c in ranked if c["index"] == 1)
    assert c1["normalized_components"]["virality"] == 8.5
    # Clip 2 with "high" should default to 5.0
    c2 = next(c for c in ranked if c["index"] == 2)
    assert c2["normalized_components"]["virality"] == 5.0


def test_slice_text_resilience_to_malformed_transcripts():
    """Verify _slice_text never raises on None or broken transcript structures."""
    assert _slice_text(None, 0.0, 10.0) == ""
    assert _slice_text({}, 0.0, 10.0) == ""
    assert _slice_text({"words": "not_a_list"}, 0.0, 10.0) == ""
    assert _slice_text({"words": [{"word": "hello", "start": None, "end": 1.0}]}, 0.0, 10.0) == ""
    assert _slice_text({"words": [{"word": "hello", "start": 1.0, "end": 2.0}]}, 0.0, 10.0) == "hello"


def test_ranking_explainability_full_breakdown_schema(tmp_path):
    """Verify all required audit breakdown fields exist and are populated."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        {
            "index": 1,
            "start": 0.0,
            "end": 25.0,
            "duration": 25.0,
            "path": str(dummy_file),
            "text": "Comprehensive explainability clip.",
            "candidate_score": 7.5,
            "virality": {"score": 80.0},
        }
    ]

    transcript = {"text": "Comprehensive explainability clip.", "words": []}
    ranked = rescore_clips(clips, transcript, cfg=cfg, provider="mock")

    assert len(ranked) == 1
    r = ranked[0]["ranking"]
    required_keys = [
        "candidate_id",
        "candidate_score",
        "virality_score",
        "content_quality_score",
        "hook_quality",
        "retention_score",
        "impact_score",
        "duration_suitability",
        "composite_score",
        "final_weighted_score",
        "assessment_status",
        "transcript_status",
        "rank",
        "kept",
        "rejection_reasons",
        "verdict_reason",
    ]
    for key in required_keys:
        assert key in r, f"Missing required ranking explainability key: {key}"


def test_ranking_ground_truth_relative_ordering(tmp_path):
    """Validate ranking ordering against ground-truth editorial quality.
    Clip A: High-virality, punchy hook, ideal 30s duration. (Expected Rank 1)
    Clip B: Average conversational clip. (Expected Rank 2)
    Clip C: Ineligible clip (under min duration 5s). (Expected Ineligible / Rank None)
    """
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    cfg["clips"]["target_count"] = 2
    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        # Clip B: Average (virality=50, candidate=5.0)
        {
            "index": 1,
            "start": 0.0,
            "end": 20.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "text": "Average conversational discussion.",
            "candidate_score": 5.0,
            "virality": {"score": 50.0},
        },
        # Clip A: Outstanding (virality=95, candidate=9.5, duration=32s near 35s sweet-spot)
        {
            "index": 2,
            "start": 25.0,
            "end": 57.0,
            "duration": 32.0,
            "path": str(dummy_file),
            "text": "Incredible breakthrough moment with massive audience reaction.",
            "candidate_score": 9.5,
            "virality": {"score": 95.0},
        },
        # Clip C: Ineligible (duration=5.0s < min_seconds=10.0s)
        {
            "index": 3,
            "start": 60.0,
            "end": 65.0,
            "duration": 5.0,
            "path": str(dummy_file),
            "text": "Too short.",
            "candidate_score": 9.0,
            "virality": {"score": 90.0},
        },
    ]

    transcript = {"text": "dummy transcript", "words": []}
    ranked = rescore_clips(clips, transcript, cfg=cfg, provider="mock")

    assert len(ranked) == 3
    # Clip 2 (Clip A) must rank #1
    assert ranked[0]["index"] == 2
    assert ranked[0]["rank"] == 1
    assert ranked[0]["kept"] is True

    # Clip 1 (Clip B) must rank #2
    assert ranked[1]["index"] == 1
    assert ranked[1]["rank"] == 2
    assert ranked[1]["kept"] is True

    # Clip 3 (Clip C) must be ineligible
    assert ranked[2]["index"] == 3
    assert ranked[2]["eligible"] is False
    assert ranked[2]["rank"] is None
    assert ranked[2]["kept"] is False
    assert ranked[2]["weighted_score"] == 0.0


def test_ranking_custom_composite_weights_respected(tmp_path):
    """Verify that user-configured composite weights are strictly applied."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    # Custom weights heavily prioritizing virality (80%) over LLM (10%) and candidate (10%)
    cfg["clips"]["composite_weights"] = {"llm": 0.10, "virality": 0.80, "candidate": 0.10}

    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        {
            "index": 1,
            "start": 0.0,
            "end": 20.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "text": "Weighted clip test.",
            "candidate_score": 10.0,  # cand_norm = 1.0 (or 10.0 / 10 if > 10, wait: 10.0 <= 10.0 so cand_norm = 10.0)
            "virality": {"score": 100.0},  # vir_norm = 10.0
        }
    ]

    transcript = {"text": "Weighted clip test.", "words": []}
    ranked = rescore_clips(clips, transcript, cfg=cfg, provider="mock")

    assert len(ranked) == 1
    c = ranked[0]
    llm_s = c["normalized_components"]["llm"]
    vir_s = c["normalized_components"]["virality"]
    cand_s = c["normalized_components"]["candidate"]
    expected_composite = round((0.10 * llm_s + 0.80 * vir_s + 0.10 * cand_s) / 1.0, 3)
    assert abs(c["weighted_score"] - expected_composite) < 1e-4
