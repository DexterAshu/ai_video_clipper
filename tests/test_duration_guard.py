"""Regression tests to ensure style_refiner never collapses clip durations below min_seconds."""
import pytest
import copy
from style_refiner import refine_clip


def test_style_refiner_preserves_min_duration_with_sparse_words(cfg):
    """When a candidate has only 1 or 2 words near the start, the ending optimizer
    must NOT trim the clip down to < min_seconds (e.g. 0.76s)."""
    # 50 second span, but words exist only at the start (2497.0s - 2497.26s)
    sparse_words = [
        {"word": "haan", "start": 2497.0, "end": 2497.10},
        {"word": "bhai", "start": 2497.12, "end": 2497.26},
    ]

    candidate = {"start": 2497.0, "end": 2547.0, "index": 0}
    transcript = {
        "words": sparse_words,
        "sentences": [{"text": "haan bhai", "start": 2497.0, "end": 2497.26}]
    }
    scenes = {"scenes": [{"start": 2490.0, "end": 2550.0}]}
    subs = {"has_burned_subtitles": False, "subtitles": []}

    test_cfg = copy.deepcopy(cfg)
    test_cfg["clips"]["min_seconds"] = 15.0
    test_cfg["clips"]["max_seconds"] = 60.0

    plan = refine_clip(
        candidate=candidate,
        transcript=transcript,
        scenes=scenes,
        subs=subs,
        profile=None,
        cfg=test_cfg,
        provider="mock"
    )

    out_dur = plan["output_duration"]
    assert out_dur >= 15.0, f"Expected output_duration >= 15.0s, but got {out_dur:.2f}s"
    total_seg = sum(seg[1] - seg[0] for seg in plan["segments"])
    assert total_seg >= 15.0, f"Expected total segment duration >= 15.0s, but got {total_seg:.2f}s"


def test_style_refiner_preserves_bounds_when_no_words(cfg):
    """When a candidate has no words at all, it should keep valid duration bounded by min/max."""
    candidate = {"start": 100.0, "end": 140.0, "index": 1}
    transcript = {"words": [], "sentences": []}
    scenes = {"scenes": []}
    subs = {"has_burned_subtitles": False, "subtitles": []}

    test_cfg = copy.deepcopy(cfg)
    test_cfg["clips"]["min_seconds"] = 20.0
    test_cfg["clips"]["max_seconds"] = 60.0

    plan = refine_clip(
        candidate=candidate,
        transcript=transcript,
        scenes=scenes,
        subs=subs,
        profile=None,
        cfg=test_cfg,
        provider="mock"
    )

    out_dur = plan["output_duration"]
    assert out_dur >= 20.0, f"Expected output_duration >= 20.0s, but got {out_dur:.2f}s"
