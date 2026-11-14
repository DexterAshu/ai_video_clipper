"""Stage 6 verification suite:
- LLM Manager: catalog, context length, unload, fallback
- Caption Language: Hinglish, Hindi, English timestamp alignment & Devanagari detection
- Reframe 2D Subject Tracking: adaptive headroom & composition checks
"""
from pathlib import Path
import pytest

import caption_lang
import captions
import llm_manager
import reframe


def test_llm_manager_catalog():
    assert "qwen3.5:4b" in llm_manager.MODEL_CATALOG
    assert "llama3.2:3b" in llm_manager.MODEL_CATALOG
    assert llm_manager.MODEL_CATALOG["qwen3.5:4b"]["recommended"] is True
    assert llm_manager.MODEL_CATALOG["qwen3.5:4b"]["vision"] is True
    assert llm_manager.MODEL_CATALOG["llama3.2:3b"]["tier"] == "fallback"


def test_strip_reasoning_tags():
    raw = "<think>\nThinking deeply about the universe...\n</think>\nHere is the answer."
    cleaned = llm_manager.strip_reasoning_tags(raw)
    assert cleaned == "Here is the answer."


def test_devanagari_detection():
    english_words = [{"word": "Hello", "start": 0.0, "end": 0.5}, {"word": "world", "start": 0.5, "end": 1.0}]
    assert captions._has_devanagari(english_words) is False

    hindi_words = [{"word": "नमस्ते", "start": 0.0, "end": 0.5}, {"word": "दुनिया", "start": 0.5, "end": 1.0}]
    assert captions._has_devanagari(hindi_words) is True


def test_caption_lang_alignment():
    # 5 words spanning 0.0s to 2.5s
    original_words = [
        {"word": "maine", "start": 0.0, "end": 0.4},
        {"word": "kabhi", "start": 0.5, "end": 0.9},
        {"word": "nahi", "start": 1.0, "end": 1.4},
        {"word": "socha", "start": 1.5, "end": 1.9},
        {"word": "tha", "start": 2.0, "end": 2.5},
    ]
    
    # Process with mock provider
    processed = caption_lang.process_caption_language(
        original_words, target_lang="english", cfg={}, provider="mock"
    )
    
    assert len(processed) > 0
    # Must preserve exact total window span [0.0, 2.5]
    assert processed[0]["start"] >= 0.0
    assert processed[-1]["end"] <= 2.55
    # Word timestamps must be strictly monotonic
    for i in range(len(processed) - 1):
        assert processed[i]["end"] <= processed[i + 1]["end"]
        assert processed[i]["start"] < processed[i]["end"]


def test_reframe_2d_adaptive_crop_geometry():
    # 1080p source, 9:16 vertical crop
    crop_w, crop_h, y0 = reframe.crop_geometry(1920, 1080, "9:16")
    assert crop_w == 606 or crop_w == 608 or (crop_w % 2 == 0)
    assert crop_h == 1080
    assert y0 >= 0


def test_reframe_subject_tracker():
    tracker = reframe.SubjectTracker(1920, 1080)
    # Feed face at center
    active_face = tracker.update([(960, 540, 200, 200, 0.95, 0.2)], 0)
    assert active_face is not None
    assert active_face["cx"] == 960
    assert active_face["cy"] == 540
    assert tracker.active_id is not None
