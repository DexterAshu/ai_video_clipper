"""Tests for Frameza Stage 8.1 Audio-Visual Speaker Intelligence."""
from __future__ import annotations

import numpy as np
import pytest
from pathlib import Path

import reframe


def test_scenario_a_active_speaker_vs_stationary_listener():
    """Scenario A: Person A has rhythmic syllabic mouth movement, Person B is stationary.
    Audio is active. Person A must receive higher confidence and become active speaker."""
    tracker = reframe.SubjectTracker(1920, 1080)
    # Feed 6 frames:
    # Person A (x=400): alternating open/closed (syllabic speech)
    # Person B (x=1400): stationary closed mouth
    for i in range(6):
        m_a = 0.45 if (i % 2 == 0) else 0.05
        m_b = 0.02
        faces = [
            (400.0, 500.0, 80.0, 80.0, 0.95, m_a),
            (1400.0, 500.0, 80.0, 80.0, 0.95, m_b)
        ]
        res = tracker.update(faces, i, audio_active=True)

    assert res is not None
    assert 360.0 <= res["cx"] <= 440.0
    assert res["active_id"] == 0
    assert res["speaker_confidence"] > 0.7


def test_scenario_b_active_speaker_vs_silent_laughing_listener():
    """Scenario B: Person A is speaking, Person B is laughing (wide open mouth with no syllabic variance).
    Audio is active. Person B must be discounted and Person A must remain active speaker."""
    tracker = reframe.SubjectTracker(1920, 1080)
    # Feed 8 frames:
    # Person A (x=400): active speech (high variance)
    # Person B (x=1400): held open laughter (mean_open > 0.45, var < 0.003)
    for i in range(8):
        m_a = 0.50 if (i % 2 == 0) else 0.08
        m_b = 0.55 + 0.01 * (i % 2)  # stationary wide open mouth (var ~ 0.0001)
        faces = [
            (400.0, 500.0, 80.0, 80.0, 0.95, m_a),
            (1400.0, 500.0, 80.0, 80.0, 0.95, m_b)
        ]
        res = tracker.update(faces, i, audio_active=True)

    assert res is not None
    # Active speaker must stay on Person A (x=400), not Person B (x=1400)
    assert 360.0 <= res["cx"] <= 440.0


def test_scenario_c_mouth_movement_during_silence_does_not_switch():
    """Scenario C: Audio is silent (pause in conversation).
    Person B makes mouth movements. The camera must hold current speaker and freeze hysteresis."""
    tracker = reframe.SubjectTracker(1920, 1080)
    # Establish Person A as the active speaker across 5 frames with active audio
    for i in range(5):
        m_a = 0.45 if (i % 2 == 0) else 0.05
        faces = [
            (400.0, 500.0, 80.0, 80.0, 0.95, m_a),
            (1400.0, 500.0, 80.0, 80.0, 0.95, 0.02)
        ]
        tracker.update(faces, i, audio_active=True)

    assert tracker.active_id == 0

    # Next 6 frames: Audio is SILENT. Person B moves mouth (e.g. smiling/chewing)
    for i in range(5, 11):
        faces = [
            (400.0, 500.0, 80.0, 80.0, 0.95, 0.02),
            (1400.0, 500.0, 80.0, 80.0, 0.95, 0.40 if (i % 2 == 0) else 0.10)
        ]
        res = tracker.update(faces, i, audio_active=False)

    # Tracker MUST remain on Person A because audio was silent
    assert res is not None
    assert res["active_id"] == 0
    assert 360.0 <= res["cx"] <= 440.0


def test_scenario_d_audio_activity_extraction():
    """Verify extract_audio_activity handles synthetic audio or missing files gracefully."""
    # Test on a nonexistent path: should gracefully return True array without raising
    mask = reframe.extract_audio_activity("nonexistent_video.mp4", 0.0, 5.0, target_fps=12.0)
    assert isinstance(mask, np.ndarray)
    assert len(mask) == 60
    assert all(mask)


def test_scenario_e_graceful_handling_missing_faces():
    """Verify tracker holds active target during momentary face disappearance (grace frames)."""
    tracker = reframe.SubjectTracker(1920, 1080)
    # Establish active face
    for i in range(4):
        tracker.update([(500.0, 500.0, 100.0, 100.0, 0.9, 0.3)], i, audio_active=True)

    # 1 frame with no detections: must return recovered track
    recovered = tracker.update([], 4, audio_active=True)
    assert recovered is not None
    assert recovered.get("recovered") is True
    assert 480.0 <= recovered["cx"] <= 520.0

