"""Tests for Frameza Dynamic Multi-Layout AI Director & Quality Enhancements."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

import caption_lang
import classify
import director
import metadata
import reframe


# =====================================================================
# 1. Dynamic Director Layout Planning Tests
# =====================================================================

def test_director_single_speaker_chooses_full_screen():
    """Verify single-speaker scenes choose Full-Screen layout."""
    targets = [{"num_faces": 1, "cx": 400.0, "cy": 500.0, "fw": 100.0, "fh": 120.0}] * 36
    plan = director.plan_clip_layouts(targets, {0: []}, duration_s=12.0)
    assert len(plan) >= 1
    assert all(s.layout == director.LAYOUT_FULL for s in plan)
    assert plan[0].start_s == 0.0
    assert plan[-1].end_s == 12.0


def test_director_two_wide_speakers_chooses_top_bottom_split():
    """Verify two participants separated widely choose Top-Bottom split."""
    targets = [{
        "num_faces": 2,
        "multi_person": False,
        "cx": 960.0,
        "cy": 500.0,
        "w": 950.0,
        "h": 200.0,
        "primary_face": {"cx": 380.0, "cy": 450.0, "fw": 140.0, "fh": 140.0},
        "secondary_face": {"cx": 1450.0, "cy": 450.0, "fw": 130.0, "fh": 130.0}
    }] * 36
    plan = director.plan_clip_layouts(targets, {0: [], 1: []}, duration_s=12.0)
    assert len(plan) >= 1
    assert any(s.layout == director.LAYOUT_SPLIT_TB for s in plan)


def test_director_two_close_speakers_chooses_two_person():
    """Verify two participants standing close side-by-side fit in two_person full composition."""
    targets = [{
        "num_faces": 2,
        "multi_person": True,
        "cx": 960.0,
        "cy": 500.0,
        "w": 400.0,
        "h": 160.0,
        "primary_face": {"cx": 800.0, "cy": 450.0, "fw": 120.0, "fh": 120.0},
        "secondary_face": {"cx": 1100.0, "cy": 450.0, "fw": 120.0, "fh": 120.0}
    }] * 36
    plan = director.plan_clip_layouts(targets, {0: [], 1: []}, duration_s=12.0)
    assert len(plan) >= 1
    assert plan[0].layout == director.LAYOUT_TWO_PERSON


def test_director_no_faces_falls_back_to_wide_context():
    """Verify scenes with no face tracks safely fall back to Wide / Context mode."""
    plan = director.plan_clip_layouts([], {}, duration_s=10.0)
    assert len(plan) == 1
    assert plan[0].layout == director.LAYOUT_WIDE


def test_director_min_hold_duration():
    """Verify director enforces minimum layout hold duration (>= 3.0s)."""
    # 2 seconds of single speaker, followed by 8 seconds of two speakers
    t_single = [{"num_faces": 1, "cx": 400.0, "cy": 500.0, "fw": 100.0, "fh": 120.0}] * 6
    t_split = [{
        "num_faces": 2, "multi_person": False, "cx": 960.0, "cy": 500.0, "w": 950.0, "h": 200.0,
        "primary_face": {"cx": 380.0, "cy": 450.0}, "secondary_face": {"cx": 1450.0, "cy": 450.0}
    }] * 24
    plan = director.plan_clip_layouts(t_single + t_split, {0: []}, duration_s=10.0, min_hold_s=3.0)
    for seg in plan:
        # Every segment hold duration must respect minimum duration (within boundary tolerance)
        assert seg.duration >= 2.5 or seg == plan[-1]


def test_director_manual_override():
    """Verify manual framing mode override overrides dynamic decisions."""
    targets = [{"num_faces": 1, "cx": 400.0, "cy": 500.0}] * 20
    plan = director.plan_clip_layouts(targets, {0: []}, duration_s=10.0, force_layout="split_lr")
    assert len(plan) == 1
    assert plan[0].layout == "split_lr"


# =====================================================================
# 2. Tripod-First Deadband & Speaker Scoring Tests
# =====================================================================

def test_tripod_deadband_locks_stationary_subject():
    """Verify spatial deadband keeps camera 100% locked during micro-movements."""
    # Subject hovers around 500px with small shifts (+15px, -10px, +8px)
    raw = [500.0, 515.0, 490.0, 508.0, 495.0, 502.0]
    deadband_px = 60.0
    out = reframe.apply_tripod_deadband(raw, deadband_px=deadband_px, min_val=0.0, max_val=1000.0)
    # Output must remain 100% identical to initial anchor
    assert all(x == 500.0 for x in out)


def test_tripod_deadband_smoothly_pans_on_large_movement():
    """Verify camera smoothly tracks when subject meaningfully exits safe zone."""
    raw = [500.0, 500.0, 750.0, 800.0, 850.0]
    deadband_px = 50.0
    out = reframe.apply_tripod_deadband(raw, deadband_px=deadband_px, min_val=0.0, max_val=1000.0)
    # Camera should start static, then track right
    assert out[0] == 500.0
    assert out[1] == 500.0
    assert out[-1] > 600.0


def test_speaker_scoring_area_normalization():
    """Verify foreground closer silent face does not beat active background speaker."""
    tracker = reframe.SubjectTracker(1920, 1080)
    # Person 1 (background, active speaker): smaller face (50x50), rapid syllabic mouth movement
    # Person 2 (foreground, silent listener): larger face (100x100), closed mouth
    # Feed 6 frames of speech to build history
    for i in range(6):
        m1 = 0.35 if (i % 2 == 0) else 0.05
        m2 = 0.02
        framed = tracker.update([
            (400.0, 400.0, 50.0, 50.0, 0.95, m1),
            (1400.0, 400.0, 100.0, 100.0, 0.95, m2)
        ], i)
    assert framed is not None
    # Active speaker at x=400 should be selected despite smaller face
    assert 380.0 <= framed["cx"] <= 420.0


# =====================================================================
# 3. Hinglish Transliteration Tests
# =====================================================================

def test_hinglish_deterministic_transliteration():
    """Verify Devanagari Hindi converts to conversational Roman Hinglish."""
    src = "आज हम एक नया प्रोजेक्ट शुरू करेंगे।"
    out = caption_lang.transliterate_devanagari_to_hinglish(src)
    assert "aaj" in out.lower()
    assert "hum" in out.lower()
    assert "naya" in out.lower()
    assert "project" in out.lower()
    assert "shuru" in out.lower()
    assert "karenge" in out.lower()


def test_hinglish_preserves_english_loanwords():
    """Verify English words already in Latin alphabet remain completely untouched."""
    words = [
        {"word": "Actually", "start": 0.0, "end": 0.5},
        {"word": "ये", "start": 0.5, "end": 0.9},
        {"word": "comedy", "start": 0.9, "end": 1.4},
        {"word": "शो", "start": 1.4, "end": 1.8},
        {"word": "hai.", "start": 1.8, "end": 2.2}
    ]
    out = caption_lang.process_caption_language(words, "hinglish")
    tokens = [w["word"] for w in out]
    assert tokens[0] == "Actually"
    assert tokens[1].lower() in ("ye", "yeh")
    assert tokens[2] == "comedy"
    assert tokens[3].lower() == "show"
    # Exact word timestamps must match Whisper ground truth
    assert out[0]["start"] == 0.0
    assert out[2]["start"] == 0.9
    assert out[4]["end"] == 2.2


# =====================================================================
# 4. Metadata Deduplication & Prompt Leak Guard Tests
# =====================================================================

def test_metadata_prompt_leak_rejection():
    """Verify metadata echoing prompt startup few-shot is rejected."""
    leaked = {
        "title": "The one habit that saved my startup",
        "description": "A two-time founder breaks down the daily reset that kept his team shipping when everything was on fire.",
        "hashtags": ["#startup", "#founder"]
    }
    assert metadata.detect_prompt_leak(leaked) is True


def test_job_metadata_deduplication():
    """Verify identical titles in a job are differentiated."""
    clips = [
        {"index": 0, "hook": "First joke", "metadata": {"title": "Best Moments"}},
        {"index": 1, "hook": "Second punchline", "metadata": {"title": "Best Moments"}},
        {"index": 2, "hook": "Third roast", "metadata": {"title": "Best Moments"}}
    ]
    deduped = metadata.deduplicate_job_metadata(clips)
    titles = [c["metadata"]["title"] for c in deduped]
    assert len(set(titles)) == 3
    assert all("Best Moments" in t for t in titles)


# =====================================================================
# 5. Transcript-First Classification Tests
# =====================================================================

def test_classification_recognizes_standup_comedy():
    """Verify standup comedy show in Hindi/Hinglish is classified as comedy, not finance."""
    text = "yaar yeh standup comedy show mein bohot hasi aayi, pura roast kar diya inhone"
    meta = {"title": "Viral Moment", "hashtags": ["#viral", "#shorts"]}
    niche = classify.rule_based_niche(text, meta["title"], meta["hashtags"])
    assert niche == "comedy"


def test_classification_prevents_metadata_hijacking():
    """Verify leaked hashtags do not force finance category without transcript evidence."""
    text = "aaj ka episode bohot entertaining aur funny tha sabhi dost bohot hase"
    meta = {"title": "Clip 4", "hashtags": ["#startup", "#business"]}
    niche = classify.rule_based_niche(text, meta["title"], meta["hashtags"])
    assert niche == "comedy"

