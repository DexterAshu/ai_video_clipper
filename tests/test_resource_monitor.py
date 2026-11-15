"""Tests for System Resource Monitoring and Pressure Guardrails (Fix E)."""
from __future__ import annotations

import unittest.mock as mock
import pytest

import resource_coordinator


def test_resource_coordinator_snapshot_captures_ram_and_gpu():
    """Verify that snapshot() returns a valid SystemSnapshot."""
    snap = resource_coordinator.coordinator.snapshot()
    assert snap.ram_total_mb > 0
    assert snap.ram_percent >= 0
    assert isinstance(snap.to_dict(), dict)


def test_resource_pressure_detects_critical_ram():
    """When RAM exceeds max_system_ram_percent, critical pressure must be flagged."""
    cfg = {"resources": {"max_system_ram_percent": 80.0, "max_gpu_memory_percent": 90.0}}
    with mock.patch.object(resource_coordinator.coordinator, "snapshot") as mock_snap:
        mock_snap.return_value.ram_percent = 85.0
        mock_snap.return_value.gpu_percent = 50.0
        mock_snap.return_value.gpu_total_mb = 8192.0

        res = resource_coordinator.coordinator.check_resource_pressure(cfg)
        assert res["critical"] is True
        assert res["safe"] is False
        assert any("RAM at 85.0%" in r for r in res["reasons"])


def test_resource_pressure_detects_critical_gpu():
    """When Dedicated VRAM exceeds max_gpu_memory_percent, critical pressure must be flagged."""
    cfg = {"resources": {"max_system_ram_percent": 85.0, "max_gpu_memory_percent": 85.0}}
    with mock.patch.object(resource_coordinator.coordinator, "snapshot") as mock_snap:
        mock_snap.return_value.ram_percent = 60.0
        mock_snap.return_value.gpu_percent = 92.0
        mock_snap.return_value.gpu_total_mb = 8192.0
        mock_snap.return_value.gpu_used_mb = 7536.0

        res = resource_coordinator.coordinator.check_resource_pressure(cfg)
        assert res["critical"] is True
        assert res["safe"] is False
        assert any("Dedicated VRAM at 92.0%" in r for r in res["reasons"])


def test_context_managers_track_active_operations():
    """Context managers must accurately update active counters."""
    coord = resource_coordinator.coordinator
    assert coord._active_workers == 0
    assert coord._active_ffmpeg == 0

    with coord.track_worker():
        assert coord._active_workers == 1
        with coord.track_ffmpeg():
            assert coord._active_ffmpeg == 1
        assert coord._active_ffmpeg == 0
    assert coord._active_workers == 0
