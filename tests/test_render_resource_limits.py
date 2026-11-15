"""Tests for Render Worker Limits and Concurrency Controls (Fix A)."""
from __future__ import annotations

import unittest.mock as mock
import pytest

import pipeline
import resource_coordinator


def test_default_render_worker_count_is_one():
    """Default render worker count must be strictly 1."""
    cfg = {"render": {}}
    assert pipeline._worker_count(cfg, 10) == 1


def test_auto_render_worker_count_is_one():
    """'auto' worker count setting must resolve strictly to 1, never cpu_count // 2."""
    cfg = {"render": {"parallel_workers": "auto"}}
    assert pipeline._worker_count(cfg, 10) == 1


def test_ten_candidate_clips_do_not_create_ten_workers():
    """10 candidate clips must not launch 10 render workers."""
    cfg = {"render": {"parallel_workers": "auto"}}
    workers = pipeline._worker_count(cfg, n_clips=10)
    assert workers == 1


def test_configured_two_workers_allowed_when_resources_safe():
    """When parallel_workers is 2 and resources are healthy, allow 2."""
    cfg = {"render": {"parallel_workers": 2}, "resources": {"max_system_ram_percent": 85.0}}
    with mock.patch.object(resource_coordinator.coordinator, "check_resource_pressure",
                           return_value={"safe": True, "critical": False, "reasons": []}), \
         mock.patch.object(resource_coordinator.coordinator, "snapshot") as mock_snap:
        mock_snap.return_value.gpu_total_mb = 8192.0
        mock_snap.return_value.gpu_free_mb = 5000.0
        mock_snap.return_value.ram_percent = 50.0

        workers = pipeline._worker_count(cfg, n_clips=5)
        assert workers == 2


def test_configured_worker_count_capped_at_maximum_two():
    """Configuring 4 or 8 workers is hard-capped at 2 for RTX 5050 / 8GB VRAM."""
    cfg = {"render": {"parallel_workers": 8}, "resources": {"max_system_ram_percent": 85.0}}
    with mock.patch.object(resource_coordinator.coordinator, "check_resource_pressure",
                           return_value={"safe": True, "critical": False, "reasons": []}), \
         mock.patch.object(resource_coordinator.coordinator, "snapshot") as mock_snap:
        mock_snap.return_value.gpu_total_mb = 8192.0
        mock_snap.return_value.gpu_free_mb = 5000.0
        mock_snap.return_value.ram_percent = 50.0

        workers = pipeline._worker_count(cfg, n_clips=10)
        assert workers == 2


def test_resource_pressure_forces_fallback_to_one_worker():
    """When RAM or VRAM is under critical pressure, fallback to 1 worker."""
    cfg = {"render": {"parallel_workers": 2}, "resources": {"max_system_ram_percent": 85.0}}
    with mock.patch.object(resource_coordinator.coordinator, "check_resource_pressure",
                           return_value={"safe": False, "critical": True, "reasons": ["RAM at 90%"]}):
        workers = pipeline._worker_count(cfg, n_clips=5)
        assert workers == 1
