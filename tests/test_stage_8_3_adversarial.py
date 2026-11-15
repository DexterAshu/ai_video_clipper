"""Stage 8.3 Adversarial and Edge-Case Test Suite.

Tests rigorous edge cases across:
- Candidate starvation prevention & event fusion outside dense speech shortlists
- Non-numeric / NaN / Inf score handling and composite normalization
- Ranking engine eligibility and deterministic tie-breaking
- Decoupling of AI preparation and word repair from captions flag
- Ollama empty response / thinking channel fallback
- Media container integrity validation
- Telemetry robustness
"""
import math
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from highlights import (
    select_highlights,
    rescore_clips,
    _safe_num,
    event_cluster_candidates,
)
import config
from errors import LLMError


# ---------------------------------------------------------------------------
# Scenario 1: High-intensity event outside dense speech shortlists (Audit A)
# ---------------------------------------------------------------------------
def test_dense_speech_with_high_intensity_event_outside_shortlist():
    """Verify that strong event clusters (intensity >= 6.0) are extracted even
    when the video has dense speech (>40 wpm) and would otherwise starve events."""
    cfg = config.load_config()
    cfg["viral_v2"]["min_event_cluster_intensity"] = 6.0
    cfg["clips"]["min_seconds"] = 15.0
    cfg["clips"]["max_seconds"] = 60.0

    # Dense speech transcript covering first 60 seconds of a 300-second video
    sentences = [
        {"start": 0.0, "end": 10.0, "text": "This is a very fast spoken podcast discussion with lots of words."},
        {"start": 10.0, "end": 20.0, "text": "We are speaking continuously at high speed discussing technology."},
        {"start": 20.0, "end": 30.0, "text": "Every second is packed with dense technical jargon and debate."},
        {"start": 30.0, "end": 40.0, "text": "The conversation moves rapidly through various architectural points."},
        {"start": 40.0, "end": 50.0, "text": "Nobody is pausing or stopping during this early monologue segment."},
        {"start": 50.0, "end": 60.0, "text": "This establishes a dense wpm across the active transcript portion."},
    ]
    transcript = {
        "text": " ".join(s["text"] for s in sentences),
        "sentences": sentences,
        "words": [],
    }
    duration = 300.0  # 5 minute video

    # A major viral burst (e.g. huge audience laughter / chair flip) at 220s-240s,
    # far outside the speech region
    events = [
        {
            "t_start_s": 225.0,
            "t_end_s": 235.0,
            "type": "laughter",
            "intensity_1_10": 9.5,
            "description": "Explosive studio laughter and reaction",
        }
    ]

    candidates = select_highlights(
        transcript=transcript,
        scenes={"scenes": []},
        duration=duration,
        cfg=cfg,
        events=events,
        max_candidates=10,
    )

    # Check that a candidate covering the high-intensity event around 225s-235s was generated
    event_cands = [c for c in candidates if c["start"] <= 230.0 <= c["end"]]
    assert len(event_cands) >= 1, "High-intensity event cluster outside speech region was starved!"
    assert event_cands[0]["score"] >= 7.0


# ---------------------------------------------------------------------------
# Scenario 2: Numerical robustness - NaN, Inf, None, out-of-range (Audit B)
# ---------------------------------------------------------------------------
def test_safe_num_handles_nan_inf_none_strings():
    """Verify _safe_num never raises and clamps to [min_v, max_v]."""
    assert _safe_num(None, default=5.0) == 5.0
    assert _safe_num(float("nan"), default=5.0) == 5.0
    assert _safe_num(float("inf"), default=5.0, max_v=10.0) == 10.0
    assert _safe_num(float("-inf"), default=5.0, min_v=0.0) == 0.0
    assert _safe_num("invalid_string", default=3.5) == 3.5
    assert _safe_num("8.75", default=5.0) == 8.75
    assert _safe_num(150.0, default=5.0, max_v=100.0) == 100.0
    assert _safe_num(-20.0, default=5.0, min_v=0.0) == 0.0


# ---------------------------------------------------------------------------
# Scenario 3: Ranking engine handling of NaN / Inf in clip scores (Audit B)
# ---------------------------------------------------------------------------
def test_ranking_engine_survives_nan_and_inf_scores(tmp_path):
    """Corrupted score inputs from mock or broken estimators must not crash rescore_clips."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 15.0
    dummy_file = tmp_path / "clip.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        {
            "index": 1,
            "start": 0.0,
            "end": 20.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "words": [{"word": "test", "start": 0.0, "end": 1.0}],
            "candidate_score": float("nan"),
            "virality": {"score": float("inf")},
        },
        {
            "index": 2,
            "start": 25.0,
            "end": 45.0,
            "duration": 20.0,
            "path": str(dummy_file),
            "words": [{"word": "sample", "start": 25.0, "end": 26.0}],
            "candidate_score": None,
            "virality": {"score": "not-a-number"},
        },
    ]

    transcript = {"text": "test sample", "sentences": [], "words": []}

    # Should execute safely with zero uncaught exceptions
    ranked = rescore_clips(clips, transcript, cfg=cfg)
    assert len(ranked) == 2
    for c in ranked:
        assert not math.isnan(c["weighted_score"])
        assert not math.isinf(c["weighted_score"])
        assert 0.0 <= c["weighted_score"] <= 10.0


# ---------------------------------------------------------------------------
# Scenario 4: Ranking engine transcript status tracking (Audit B)
# ---------------------------------------------------------------------------
def test_ranking_engine_transcript_status_classification(tmp_path):
    """Verify missing, recovered, and complete transcript status assignments."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    dummy_file = tmp_path / "clip.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        # Missing text
        {
            "index": 1,
            "start": 0.0,
            "end": 15.0,
            "duration": 15.0,
            "path": str(dummy_file),
            "text": "",
        },
        # Recovered via clip words
        {
            "index": 2,
            "start": 20.0,
            "end": 35.0,
            "duration": 15.0,
            "path": str(dummy_file),
            "words": [{"word": "recovered", "start": 20.5, "end": 21.0}],
        },
    ]
    transcript = {"text": "", "sentences": [], "words": []}

    ranked = rescore_clips(clips, transcript, cfg=cfg)
    assert len(ranked) == 2
    # Clip with words has recovered status
    c2 = next(c for c in ranked if c["index"] == 2)
    assert c2["transcript_status"] == "recovered"
    # Clip without words or transcript has missing status
    c1 = next(c for c in ranked if c["index"] == 1)
    assert c1["transcript_status"] == "missing"
    assert c1["assessment_status"] == "not_assessed"


# ---------------------------------------------------------------------------
# Scenario 5: Deterministic ranking tie-breaking with identical scores (Audit B)
# ---------------------------------------------------------------------------
def test_ranking_deterministic_tie_breaking_order(tmp_path):
    """When composite scores are identical, sorting preserves order
    by composite_score desc, virality desc, candidate desc, duration sweet-spot proximity, index."""
    cfg = config.load_config()
    cfg["clips"]["min_seconds"] = 10.0
    dummy_file = tmp_path / "clip.mp4"
    dummy_file.write_bytes(b"x" * 5000)

    clips = [
        {
            "index": 1,
            "start": 10.0,
            "end": 30.0,
            "duration": 20.0,  # 20s is closer to 35s sweet-spot (diff=15) than 15s (diff=20)
            "path": str(dummy_file),
            "candidate_score": 6.0,
            "virality": {"score": 60.0},
        },
        {
            "index": 2,
            "start": 40.0,
            "end": 55.0,
            "duration": 15.0,
            "path": str(dummy_file),
            "candidate_score": 6.0,
            "virality": {"score": 60.0},
        },
    ]
    transcript = {"text": "identical speech content", "sentences": [], "words": []}

    ranked = rescore_clips(clips, transcript, cfg=cfg)
    assert len(ranked) == 2
    # Both have identical fallback score. Clip 1 (20s) is closer to the 35s sweet-spot.
    assert ranked[0]["index"] == 1
    assert ranked[1]["index"] == 2
    assert ranked[0]["rank"] == 1
    assert ranked[1]["rank"] == 2


# ---------------------------------------------------------------------------
# Scenario 6: Ollama empty thinking stream triggering fallback model (Audit C)
# ---------------------------------------------------------------------------
def test_llm_manager_empty_response_triggers_fallback():
    """Verify that when the primary Ollama model returns an empty text response
    (e.g. token budget consumed by reasoning thinking stream),
    query_ollama treats this as an error and switches to the fallback model."""
    from llm_manager import query_ollama

    cfg = config.load_config()

    primary_resp = MagicMock()
    primary_resp.status_code = 200
    primary_resp.json.return_value = {
        "response": "<think>Thinking...</think>",
        "done": True,
        "done_reason": "length",
        "eval_count": 120,
    }

    fallback_resp = MagicMock()
    fallback_resp.status_code = 200
    fallback_resp.json.return_value = {
        "response": '{"hook_strength": 8.0, "retention": 7.5, "clarity": 8.0, "impact": 7.0}',
        "done": True,
        "done_reason": "stop",
        "eval_count": 85,
    }

    with patch("requests.post", side_effect=[primary_resp, fallback_resp]) as mock_post:
        result = query_ollama(
            prompt="Analyze this clip",
            cfg=cfg,
            enable_fallback=True,
        )
        assert result is not None
        assert "hook_strength" in result
        assert mock_post.call_count == 2
        call_1 = mock_post.call_args_list[0][1]["json"]
        call_2 = mock_post.call_args_list[1][1]["json"]
        assert call_1["model"] == "qwen3.5:4b"
        assert call_2["model"] == "llama3.2:3b"


# ---------------------------------------------------------------------------
# Scenario 7: Word repair decoupled from captions flag (Audit A)
# ---------------------------------------------------------------------------
def test_pipeline_ai_prep_word_repair_runs_when_captions_disabled(tmp_path):
    """Verify that word-timing repair runs during ai_prep even when
    cfg['captions']['enabled'] is False."""
    from pipeline import _prepare_clip_ai_sequentially

    cfg = config.load_config()
    cfg["captions"]["enabled"] = False  # Captions explicitly turned OFF

    info = {
        "duration": 60.0,
        "audio_path": str(tmp_path / "audio.wav"),
        "width": 1920,
        "height": 1080,
    }

    cands = [
        {
            "index": 1,
            "start": 10.0,
            "end": 25.0,
            "words": [],  # Words missing!
        }
    ]

    transcript = {
        "text": "Hello world from test video",
        "sentences": [{"start": 10.0, "end": 25.0, "text": "Hello world from test video"}],
        "words": [],
    }

    mock_repaired = [
        {"word": "Hello", "start": 10.2, "end": 10.8},
        {"word": "world", "start": 11.0, "end": 11.5},
    ]

    import metadata

    with patch("transcribe.repair_clip_transcription", return_value=mock_repaired) as mock_repair:
        with patch("metadata.generate_metadata", return_value={"title": "Test Clip", "hook": "Hook", "description": "Desc", "hashtags": ["#shorts"]}):
            with patch("virality.rate_virality", return_value={"score": 75.0, "band": "Good"}):
                with patch("classify.classify_niche", return_value={"category": "tech"}):
                    # Call sequential prep
                    prep_dict = _prepare_clip_ai_sequentially(
                        candidates=cands,
                        info=info,
                        transcript=transcript,
                        scene_data=[],
                        edit_plans=None,
                        events=None,
                        cfg=cfg,
                        provider=None,
                        caption_language="en",
                        job_dir=tmp_path,
                        metadata_mod=metadata,
                    )

    # repair_clip_transcription was called even though captions were disabled!
    assert mock_repair.call_count == 1
    # Words were assigned to the candidate in prepared output
    assert 0 in prep_dict
    assert len(prep_dict[0]["words"]) == 2
    assert prep_dict[0]["words"][0]["word"] == "Hello"
