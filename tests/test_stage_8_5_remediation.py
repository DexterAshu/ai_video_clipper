import pytest
from pathlib import Path
import highlights as hl
from highlights import normalize_virality_score, compute_composite_score, rescore_clips


def test_issue_a_no_mock_leakage_and_provenance():
    # 1. Event with mock description must be sanitized and flagged is_synthetic=True
    events_mock = [{
        "t_start_s": 10.0,
        "t_end_s": 15.0,
        "description": "mock: sudden energy spike",
        "type": "energy_spike",
        "intensity_1_10": 8.0,
        "source": "mock",
    }]
    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "max_seconds": 30.0,
            "max_candidates": 5,
        }
    }
    cands_mock = hl.event_cluster_candidates(events_mock, duration=60.0, cfg=cfg)
    assert len(cands_mock) > 0
    c_mock = cands_mock[0]
    assert "mock:" not in c_mock["hook"].lower()
    assert c_mock["is_synthetic"] is True
    assert c_mock["candidate_source"] == "synthetic_event"

    # 2. Event with real description must retain real description and is_synthetic=False
    events_real = [{
        "t_start_s": 20.0,
        "t_end_s": 25.0,
        "description": "Host and guest erupt into intense laughter",
        "type": "laughter",
        "intensity_1_10": 9.0,
        "source": "audio_dsp",
    }]
    cands_real = hl.event_cluster_candidates(events_real, duration=60.0, cfg=cfg)
    assert len(cands_real) > 0
    c_real = cands_real[0]
    assert c_real["hook"] == "Host and guest erupt into intense laughter"
    assert c_real["is_synthetic"] is False
    assert c_real["candidate_source"] == "audio_event_cluster"


def test_issue_b_authoritative_mathematics_reconciliation():
    # Verify the authoritative formula against compute_composite_score and rescore_clips
    weights = {"llm": 0.60, "virality": 0.25, "candidate": 0.15}
    s_llm = 6.462
    s_vir = 3.6
    s_cand = 9.85

    score = compute_composite_score(s_llm, s_vir, s_cand, weights)
    expected = (0.60 * 6.462 + 0.25 * 3.6 + 0.15 * 9.85) / 1.0
    assert abs(score - round(expected, 3)) < 1e-4
    assert score == 6.255

    # Run through rescore_clips and verify full breakdown and recalculation
    clips = [{
        "index": 1,
        "start": 0.0,
        "end": 35.0,
        "duration": 35.0,
        "text": "This is a great podcast moment about life and comedy.",
        "candidate_score": 9.85,
        "virality": {"score": 36.0},
        "hook": "This is a great podcast moment",
        "candidate_source": "audio_event_cluster",
        "is_synthetic": False,
    }]
    transcript = {
        "text": "This is a great podcast moment about life and comedy.",
        "words": [{"word": "This", "start": 0.0, "end": 0.5}],
        "sentences": [{"text": "This is a great podcast moment about life and comedy.", "start": 0.0, "end": 35.0}],
    }
    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "max_seconds": 60.0,
            "min_keep": 1,
            "keep_ratio": 1.0,
            "rescore_weights": {"hook_strength": 0.35, "retention": 0.30, "clarity": 0.20, "impact": 0.15},
            "composite_weights": {"llm": 0.60, "virality": 0.25, "candidate": 0.15},
        }
    }
    ranked = rescore_clips(clips, transcript, cfg=cfg, provider="mock")
    assert len(ranked) == 1
    r = ranked[0]["ranking"]

    # Verify that all components and weights are transparently recorded
    assert "llm_assessment_score" in r
    assert "weights_used" in r
    assert "clarity_score" in r
    assert r["candidate_source"] == "audio_event_cluster"
    assert r["is_synthetic"] is False

    # Independent mathematical verification
    recalculated = compute_composite_score(
        r["llm_assessment_score"],
        r["virality_score"],
        r["candidate_score"],
        r["weights_used"]
    )
    assert abs(recalculated - r["composite_score"]) < 1e-4
    assert r["composite_score"] == r["final_weighted_score"]


def test_issue_c_virality_normalization_canonical_scale():
    # 0 - 100 raw score to 0 - 10 canonical scale
    assert normalize_virality_score(85.0) == 8.5
    assert normalize_virality_score(100.0) == 10.0
    assert normalize_virality_score(0.0) == 0.0

    # Already normalized score must NEVER be double-normalized
    assert normalize_virality_score(8.5) == 8.5
    assert normalize_virality_score(3.6) == 3.6
    assert normalize_virality_score(0.5) == 0.5

    # Dict format
    assert normalize_virality_score({"score": 85.0}) == 8.5
    assert normalize_virality_score({"score": 8.5}) == 8.5

    # String format
    assert normalize_virality_score("85.0") == 8.5
    assert normalize_virality_score("8.5") == 8.5

    # None and fallback
    assert normalize_virality_score(None) == 5.0
    assert normalize_virality_score("invalid") == 5.0


def test_issue_d_candidate_traceability_and_alignment():
    clips = [
        {
            "index": 10,
            "original_source_start_s": 100.0,
            "original_source_end_s": 135.0,
            "start": 102.0,
            "end": 134.0,
            "duration": 32.0,
            "text": "Clip A text with good hook",
            "candidate_score": 8.0,
            "virality": {"score": 75.0},
            "hook": "Hook A",
            "candidate_source": "llm_transcript",
            "is_synthetic": False,
        },
        {
            "index": 20,
            "original_source_start_s": 200.0,
            "original_source_end_s": 240.0,
            "start": 201.0,
            "end": 239.0,
            "duration": 38.0,
            "text": "Clip B text with higher hook",
            "candidate_score": 9.5,
            "virality": {"score": 90.0},
            "hook": "Hook B",
            "candidate_source": "audio_event_cluster",
            "is_synthetic": False,
        },
        {
            "index": 30,
            "original_source_start_s": 300.0,
            "original_source_end_s": 305.0,
            "start": 300.0,
            "end": 305.0,
            "duration": 5.0,  # Below min_seconds (ineligible)
            "text": "Too short",
            "candidate_score": 4.0,
            "virality": {"score": 20.0},
            "hook": "Too short",
        }
    ]
    transcript = {
        "text": "Clip A text with good hook Clip B text with higher hook Too short",
        "words": [],
        "sentences": [],
    }
    cfg = {
        "clips": {
            "min_seconds": 10.0,
            "max_seconds": 60.0,
            "min_keep": 1,
            "keep_ratio": 0.5,
            "rescore_weights": {"hook_strength": 0.35, "retention": 0.30, "clarity": 0.20, "impact": 0.15},
            "composite_weights": {"llm": 0.60, "virality": 0.25, "candidate": 0.15},
        }
    }
    rescored = rescore_clips(clips, transcript, cfg=cfg, provider="mock")

    # Ineligible clip must be rejected with rank=None, kept=False
    ineligible = [c for c in rescored if not c["eligible"]]
    assert len(ineligible) == 1
    assert ineligible[0]["index"] == 30
    assert ineligible[0]["ranking"]["candidate_id"] == 30
    assert ineligible[0]["ranking"]["rank"] is None
    assert ineligible[0]["ranking"]["kept"] is False
    assert any("duration_below_minimum" in r for r in ineligible[0]["ranking"]["rejection_reasons"])

    # Eligible clips must preserve source timestamps and candidate IDs
    eligible = [c for c in rescored if c["eligible"]]
    assert len(eligible) == 2
    for c in eligible:
        assert c["ranking"]["candidate_id"] == c["index"]
        assert c["ranking"]["original_start"] == c["original_source_start_s"]
        assert c["ranking"]["original_end"] == c["original_source_end_s"]
        assert c["ranking"]["start"] == c["start"]
        assert c["ranking"]["end"] == c["end"]
        assert c["ranking"]["duration"] == c["duration"]

    # Rank 1 must have composite_score >= Rank 2
    assert eligible[0]["ranking"]["rank"] == 1
    assert eligible[1]["ranking"]["rank"] == 2
    assert eligible[0]["ranking"]["composite_score"] >= eligible[1]["ranking"]["composite_score"]
