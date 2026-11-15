"""Frameza Dynamic Multi-Layout AI Director.

Intelligently analyzes scene composition, speaker count, spatial separation,
and speech activity to dynamically choose the best vertical composition:
  1. 'full': Full-Screen Single Speaker (9:16 portrait on active speaker)
  2. 'split_tb': Top-Bottom Split Screen (Preferred 2-person layout: speaker on top, listener on bottom)
  3. 'split_lr': Left-Right Split Screen (Side-by-side independent panels)
  4. 'wide': Wide / Context Mode (Sharp 16:9 main video centered over blurred vertical background)
  5. 'two_person': Two-Person Full-Scene Composition (both participants framed comfortably in one crop)

Enforces professional director stability laws:
  - Minimum layout hold duration (default: 3.0s) prevents jittery switching.
  - Aligns layout transitions with speech/sentence pauses.
  - Hysteresis suppresses unnecessary transitions.
  - Generates robust FFmpeg filter graphs for single-pass multi-layout rendering.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from config import load_config
from errors import ReframeError
from ffutil import filter_path, probe, run_ffmpeg, video_encode_args
from logutil import get_logger

log = get_logger("director")

LAYOUT_FULL = "full"
LAYOUT_SPLIT_TB = "split_tb"
LAYOUT_SPLIT_LR = "split_lr"
LAYOUT_WIDE = "wide"
LAYOUT_TWO_PERSON = "two_person"

SUPPORTED_LAYOUTS = (LAYOUT_FULL, LAYOUT_SPLIT_TB, LAYOUT_SPLIT_LR, LAYOUT_WIDE, LAYOUT_TWO_PERSON)
DEFAULT_MIN_HOLD_S = 3.0


@dataclass
class DirectorSegment:
    """A continuous timeline slice directed with a specific framing layout."""
    start_s: float
    end_s: float
    layout: str
    target_a: dict = field(default_factory=dict)       # Primary subject or Top panel
    target_b: dict = field(default_factory=dict)       # Secondary subject or Bottom panel
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def to_dict(self) -> dict:
        return {
            "start": round(self.start_s, 3),
            "end": round(self.end_s, 3),
            "duration": round(self.duration, 3),
            "layout": self.layout,
            "target_a": self.target_a,
            "target_b": self.target_b,
            "reason": self.reason
        }


def plan_clip_layouts(raw_targets: list[dict | None],
                      face_tracks: dict[int, list[dict]],
                      duration_s: float,
                      fps: float = 12.0,
                      sentence_bounds: list[float] | None = None,
                      min_hold_s: float = DEFAULT_MIN_HOLD_S,
                      force_layout: str | None = None,
                      sw: int = 1920,
                      sh: int = 1080) -> list[DirectorSegment]:
    """Analyze multi-face tracks and timeline boundaries to produce a stable,
    professionally directed sequence of layout segments."""
    if duration_s <= 0:
        return []

    # Manual layout override from Studio
    if force_layout and force_layout in SUPPORTED_LAYOUTS:
        avg_a = {}
        avg_b = {}
        if raw_targets:
            sample_a = [t.get("primary_face") or t for t in raw_targets if t and ("cx" in t or "primary_face" in t)]
            sample_b = [t.get("secondary_face") or t for t in raw_targets if t and t.get("secondary_face")]
            avg_a = _mean_target(sample_a)
            avg_b = _mean_target(sample_b)
        return [DirectorSegment(
            start_s=0.0,
            end_s=duration_s,
            layout=force_layout,
            target_a=avg_a,
            target_b=avg_b,
            reason=f"manual override: {force_layout}"
        )]

    if not raw_targets or not face_tracks:
        # No faces detected: default to Wide / Context mode so footage is not cropped blindly
        return [DirectorSegment(
            start_s=0.0,
            end_s=duration_s,
            layout=LAYOUT_WIDE,
            reason="no stable face tracks: wide context fallback"
        )]

    n_samples = len(raw_targets)
    step_s = duration_s / max(1, n_samples)

    # 1. Evaluate per-sample layout suitability
    sample_layouts = []
    sample_targets_a = []
    sample_targets_b = []

    # Dynamic 2-person span limit: fits comfortably within ~85% of the 9:16 crop width
    max_two_shot_span = int(sh * 9 / 16 * 0.85)

    for i, t in enumerate(raw_targets):
        if t is None:
            sample_layouts.append(LAYOUT_WIDE)
            sample_targets_a.append({})
            sample_targets_b.append({})
            continue

        num_faces = t.get("num_faces", 1)
        multi = bool(t.get("multi_person", False))
        tracks_active = t.get("active_tracks", [])

        if num_faces >= 2:
            span_w = t.get("w", 0)
            # If two faces are within comfortable 2-shot distance across any source resolution
            if multi and span_w > 0 and span_w <= max_two_shot_span:
                sample_layouts.append(LAYOUT_TWO_PERSON)
                sample_targets_a.append(t)
                sample_targets_b.append({})
            else:
                # Speakers sit wider apart: Top-Bottom split is the preferred two-person layout
                sample_layouts.append(LAYOUT_SPLIT_TB)
                # Pick primary active speaker for top, second participant for bottom
                sub_a = t.get("primary_face") or t
                sub_b = t.get("secondary_face") or t
                sample_targets_a.append(sub_a)
                sample_targets_b.append(sub_b)
        else:
            # Single speaker
            sample_layouts.append(LAYOUT_FULL)
            sample_targets_a.append(t)
            sample_targets_b.append({})

    # 2. Group into candidate layout blocks with minimum hold hysteresis
    min_hold_samples = max(1, int(round(min_hold_s / max(1e-3, step_s))))
    blocks: list[tuple[int, int, str]] = []
    curr_layout = sample_layouts[0]
    curr_start = 0

    for i in range(1, n_samples):
        candidate = sample_layouts[i]
        block_len = i - curr_start
        if candidate != curr_layout and block_len >= min_hold_samples:
            # Check if transition aligns with a sentence bound
            t_curr = i * step_s
            aligned_t = _snap_to_boundary(t_curr, sentence_bounds or [], max_delta=0.8)
            split_idx = int(round(aligned_t / step_s)) if aligned_t is not None else i
            split_idx = max(curr_start + 1, min(n_samples - 1, split_idx))

            blocks.append((curr_start, split_idx, curr_layout))
            curr_start = split_idx
            curr_layout = candidate

    blocks.append((curr_start, n_samples, curr_layout))

    # 3. Build DirectorSegment list with boundary timestamps
    segments: list[DirectorSegment] = []
    for s_idx, e_idx, lay in blocks:
        st = round(s_idx * step_s, 3)
        et = round(min(duration_s, e_idx * step_s), 3)
        if et - st < 0.2:
            continue

        # Average targets for this segment
        seg_targets_a = [sample_targets_a[k] for k in range(s_idx, min(e_idx, len(sample_targets_a))) if sample_targets_a[k]]
        seg_targets_b = [sample_targets_b[k] for k in range(s_idx, min(e_idx, len(sample_targets_b))) if sample_targets_b[k]]

        avg_a = _mean_target(seg_targets_a)
        avg_b = _mean_target(seg_targets_b)

        segments.append(DirectorSegment(
            start_s=st,
            end_s=et,
            layout=lay,
            target_a=avg_a,
            target_b=avg_b,
            reason=f"directed: {lay} across {st:.1f}s–{et:.1f}s"
        ))

    # Guarantee timeline covers [0, duration_s] contiguously
    if not segments:
        segments = [DirectorSegment(0.0, duration_s, LAYOUT_FULL, reason="default fallback")]
    else:
        segments[0].start_s = 0.0
        segments[-1].end_s = duration_s

    log.info("director planned %d layout segments for %.2fs: %s",
             len(segments), duration_s, [s.layout for s in segments])
    return segments


def _snap_to_boundary(t: float, bounds: list[float], max_delta: float = 0.8) -> float | None:
    """Find the nearest sentence or speech boundary within max_delta seconds."""
    if not bounds:
        return None
    nearest = min(bounds, key=lambda b: abs(b - t))
    if abs(nearest - t) <= max_delta:
        return nearest
    return None


def _mean_target(targets: list[dict]) -> dict:
    if not targets:
        return {}
    cxs = [t["cx"] for t in targets if "cx" in t]
    cys = [t["cy"] for t in targets if "cy" in t]
    fws = [t.get("fw", t.get("w", 0)) for t in targets]
    fhs = [t.get("fh", t.get("h", 0)) for t in targets]
    return {
        "cx": float(np.mean(cxs)) if cxs else 0.0,
        "cy": float(np.mean(cys)) if cys else 0.0,
        "fw": float(np.mean(fws)) if fws else 0.0,
        "fh": float(np.mean(fhs)) if fhs else 0.0,
    }


def render_director_clip(source: Path,
                         clip_start: float,
                         clip_end: float,
                         segments: list[DirectorSegment],
                         out_path: Path,
                         cfg: dict | None = None,
                         ow: int = 1080,
                         oh: int = 1920,
                         bottom_exclusion_ratio: float = 0.0) -> dict:
    """Render a multi-layout clip using FFmpeg with pixel-faithful composition,
    independent panel tracking, and clean transitions."""
    cfg = cfg or load_config()
    source, out_path = Path(source), Path(out_path)
    dur = max(0.1, clip_end - clip_start)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    src_info = probe(source)
    sw = int(src_info["width"])
    sh = int(src_info["height"])
    has_audio = bool(src_info.get("has_audio", True))
    audio_args = ["-c:a", "aac", "-b:a", cfg["render"].get("audio_bitrate", "128k"), "-ar", "44100", "-ac", "2"] if has_audio else ["-an"]

    # If only 1 segment and layout is full, use standard fast single-crop path
    if len(segments) == 1 and segments[0].layout == LAYOUT_FULL:
        target_cx = segments[0].target_a.get("cx", sw / 2.0) or (sw / 2.0)
        target_cy = segments[0].target_a.get("cy", sh / 2.0) or (sh / 2.0)
        cw = max(2, min(sw, int(sh * 9 / 16) & ~1))
        ch = max(2, min(sh, int(cw * 16 / 9) & ~1))
        usable_sh = sh * (1.0 - max(0.0, min(0.5, bottom_exclusion_ratio)))
        top_x = max(0.0, min(sw - cw, target_cx - cw / 2.0))
        top_y = max(0.0, min(max(0.0, usable_sh - ch), target_cy - ch / 2.0))
        vf = f"crop={cw}:{ch}:{top_x:.1f}:{top_y:.1f},scale={ow}:{oh}:flags=lanczos,setsar=1"
        args = ["-ss", f"{clip_start:.3f}", "-i", source, "-t", f"{dur:.3f}",
                "-vf", vf] + video_encode_args(cfg) + audio_args + [
                "-movflags", "+faststart", out_path]
        run_ffmpeg(args)
        return {"layout": LAYOUT_FULL, "segments": 1, "out": str(out_path)}

    # Multi-segment rendering: render each segment's video slice, then concat
    temp_dir = out_path.parent / f"_director_tmp_{out_path.stem}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    seg_files: list[Path] = []

    try:
        for idx, seg in enumerate(segments):
            seg_out = temp_dir / f"seg_{idx:02d}.mp4"
            s_start = clip_start + seg.start_s
            s_dur = seg.duration
            if s_dur <= 0.05:
                continue

            filter_str = _build_segment_filter(seg, sw, sh, ow, oh)
            cmd = ["-ss", f"{s_start:.3f}", "-i", source, "-t", f"{s_dur:.3f}",
                   "-vf", filter_str] + video_encode_args(cfg) + audio_args + [
                   "-movflags", "+faststart", seg_out]
            run_ffmpeg(cmd)
            if seg_out.exists():
                seg_files.append(seg_out)

        if not seg_files:
            raise ReframeError("director produced no segment files")

        if len(seg_files) == 1:
            if out_path.exists():
                out_path.unlink()
            seg_files[0].replace(out_path)
        else:
            # Concatenate rendered segment files (use relative names within temp_dir for universal path safety)
            concat_list = temp_dir / "concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                for sf in seg_files:
                    f.write(f"file '{sf.name}'\n")

            run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_list,
                        "-c", "copy", "-movflags", "+faststart", out_path])
    finally:
        # Cleanup temporary files
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    if not out_path.exists():
        raise ReframeError(f"director failed to produce output at {out_path}")

    return {
        "layout": "dynamic_director",
        "segments": len(segments),
        "layouts_used": list({s.layout for s in segments}),
        "out": str(out_path)
    }


def direct_and_render_clip(source: Path | str,
                           clip_start: float,
                           clip_end: float,
                           out_path: Path | str,
                           cfg: dict | None = None,
                           transcript: dict | None = None,
                           framing_mode: str = "smart_director",
                           cached_plan_path: Path | None = None,
                           manual_zoom: float = 1.0,
                           crop_x_offset: float = 0.0,
                           crop_y_offset: float = 0.0,
                           ow: int | None = None,
                           oh: int | None = None,
                           bottom_exclusion_ratio: float = 0.0) -> dict:
    """High-level unified pipeline and studio entrypoint for Dynamic AI Director:
    1. Validates framing_mode (rejects unsupported modes with clear error).
    2. Reuses cached layout plan when available or plans dynamically.
    3. Analyzes audio-visual face tracking and aligns with sentence boundaries.
    4. Renders output with render_director_clip.
    5. Returns metrics dictionary."""
    import json
    import reframe

    cfg = cfg or load_config()
    rcfg_out = cfg.get("reframe", {}).get("output", {})
    if ow is None:
        ow = int(rcfg_out.get("width", 1080))
    if oh is None:
        oh = int(rcfg_out.get("height", 1920))
    source, out_path = Path(source), Path(out_path)
    dur = max(0.1, clip_end - clip_start)

    valid_modes = SUPPORTED_LAYOUTS + ("smart_director",)
    if framing_mode not in valid_modes:
        raise ValueError(f"Unsupported framing mode: '{framing_mode}'. Supported modes: {valid_modes}")

    src_info = probe(source)
    sw = int(src_info["width"])
    sh = int(src_info["height"])

    # Check for cached plan if automatic mode
    segments: list[DirectorSegment] = []
    if cached_plan_path and cached_plan_path.exists() and framing_mode == "smart_director":
        try:
            cached_data = json.loads(cached_plan_path.read_text(encoding="utf-8"))
            if isinstance(cached_data, list) and len(cached_data) > 0:
                first_ta = cached_data[0].get("target_a", {})
                first_cx = float(first_ta.get("cx", 0))
                first_fw = float(first_ta.get("fw", 0))
                # Detect stale unscaled proxy coordinates (e.g. 1080p source with 360p proxy coords)
                if sw >= 1280 and 0 < first_cx <= 640 and 0 < first_fw <= 110:
                    log.info("cached director plan has stale unscaled proxy coords - re-planning with source scale")
                    segments = []
                else:
                    segments = [
                        DirectorSegment(
                            start_s=float(s["start"]),
                            end_s=float(s["end"]),
                            layout=s["layout"],
                            target_a=s.get("target_a", {}),
                            target_b=s.get("target_b", {}),
                            reason=s.get("reason", "cached plan")
                        )
                        for s in cached_data if "start" in s and "end" in s and "layout" in s
                    ]
        except Exception as e:
            log.debug("could not load cached director plan (%s) - re-planning", e)
            segments = []

    if not segments:
        # Extract sentence boundaries from transcript
        sentence_bounds: list[float] = []
        if transcript:
            for s in transcript.get("segments", []):
                for b in (s.get("start"), s.get("end")):
                    if b is not None and clip_start <= b <= clip_end:
                        sentence_bounds.append(round(b - clip_start, 3))
            if not sentence_bounds and "words" in transcript:
                for w in transcript.get("words", []):
                    end_t = w.get("end")
                    if end_t is not None and clip_start <= end_t <= clip_end:
                        sentence_bounds.append(round(end_t - clip_start, 3))
        sentence_bounds = sorted(set(sentence_bounds))

        # Analyze face targets and audio-visual speech activity
        raw_targets, face_tracks, _ = reframe.analyze_clip_for_director(source, clip_start, dur, cfg)

        force_layout = framing_mode if framing_mode in SUPPORTED_LAYOUTS else None
        segments = plan_clip_layouts(
            raw_targets=raw_targets,
            face_tracks=face_tracks,
            duration_s=dur,
            fps=12.0,
            sentence_bounds=sentence_bounds,
            force_layout=force_layout,
            sw=sw,
            sh=sh,
        )

        if cached_plan_path and segments:
            try:
                cached_plan_path.parent.mkdir(parents=True, exist_ok=True)
                cached_plan_path.write_text(
                    json.dumps([s.to_dict() for s in segments], indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
            except Exception as e:
                log.debug("could not save director_plan.json: %s", e)

    metrics = render_director_clip(source, clip_start, clip_end, segments, out_path, cfg, ow=ow, oh=oh, bottom_exclusion_ratio=bottom_exclusion_ratio)
    metrics["framing_mode"] = framing_mode
    return metrics


def _build_segment_filter(seg: DirectorSegment, sw: int, sh: int, ow: int, oh: int) -> str:
    """Construct FFmpeg filtergraph for a specific layout mode with strict geometry guards."""
    lay = seg.layout

    if lay == LAYOUT_SPLIT_TB:
        # Top-Bottom split: upper panel (ow x oh/2), lower panel (ow x oh/2)
        half_h = oh // 2
        crop_w = max(2, min(sw, int(sw * 0.55) & ~1))
        crop_h = max(2, min(sh, int(crop_w * (half_h / ow)) & ~1))

        t_a = seg.target_a
        t_b = seg.target_b

        cx_a = t_a.get("cx", sw * 0.35) or (sw * 0.35)
        cy_a = t_a.get("cy", sh * 0.40) or (sh * 0.40)
        x_a = max(0.0, min(sw - crop_w, cx_a - crop_w / 2.0))
        y_a = max(0.0, min(sh - crop_h, cy_a - crop_h * 0.35))

        cx_b = t_b.get("cx", sw * 0.65) or (sw * 0.65)
        cy_b = t_b.get("cy", sh * 0.40) or (sh * 0.40)
        x_b = max(0.0, min(sw - crop_w, cx_b - crop_w / 2.0))
        y_b = max(0.0, min(sh - crop_h, cy_b - crop_h * 0.35))

        return (
            f"[0:v]split=2[v_top_in][v_bot_in];"
            f"[v_top_in]crop={crop_w}:{crop_h}:{x_a:.1f}:{y_a:.1f},"
            f"scale={ow}:{half_h}:flags=lanczos,setsar=1[top_pan];"
            f"[v_bot_in]crop={crop_w}:{crop_h}:{x_b:.1f}:{y_b:.1f},"
            f"scale={ow}:{half_h}:flags=lanczos,setsar=1[bot_pan];"
            f"[top_pan][bot_pan]vstack=inputs=2,"
            f"drawbox=y={half_h - 1}:h=2:color=black@0.7:t=fill"
        )

    elif lay == LAYOUT_SPLIT_LR:
        # Left-Right split: left panel (ow/2 x oh), right panel (ow/2 x oh)
        half_w = ow // 2
        crop_w = max(2, min(sw, int(sh * (half_w / oh)) & ~1))
        crop_h = sh

        t_a = seg.target_a
        t_b = seg.target_b

        cx_1 = t_a.get("cx", sw * 0.3) or (sw * 0.3)
        cx_2 = t_b.get("cx", sw * 0.7) or (sw * 0.7)
        cx_left = min(cx_1, cx_2)
        cx_right = max(cx_1, cx_2)
        x_a = max(0.0, min(sw - crop_w, cx_left - crop_w / 2.0))
        x_b = max(0.0, min(sw - crop_w, cx_right - crop_w / 2.0))

        return (
            f"[0:v]split=2[v_l_in][v_r_in];"
            f"[v_l_in]crop={crop_w}:{crop_h}:{x_a:.1f}:0,"
            f"scale={half_w}:{oh}:flags=lanczos,setsar=1[left_pan];"
            f"[v_r_in]crop={crop_w}:{crop_h}:{x_b:.1f}:0,"
            f"scale={half_w}:{oh}:flags=lanczos,setsar=1[right_pan];"
            f"[left_pan][right_pan]hstack=inputs=2,"
            f"drawbox=x={half_w - 1}:w=2:color=black@0.7:t=fill"
        )

    elif lay == LAYOUT_WIDE:
        # Wide / Context: 16:9 main video centered, blurred vertical background
        return (
            f"[0:v]split=2[v_bg_in][v_fg_in];"
            f"[v_bg_in]scale={ow}:{oh}:force_original_aspect_ratio=increase,"
            f"crop={ow}:{oh},boxblur=25:5[bg];"
            f"[v_fg_in]scale={ow}:-2:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )

    elif lay == LAYOUT_TWO_PERSON:
        # Two-person full composition: crop encompassing both faces
        cw = max(2, min(sw, int(sh * 9 / 16) & ~1))
        ch = max(2, min(sh, int(cw * 16 / 9) & ~1))
        t = seg.target_a
        cx = t.get("cx", sw / 2.0) or (sw / 2.0)
        cy = t.get("cy", sh / 2.0) or (sh / 2.0)
        top_x = max(0.0, min(sw - cw, cx - cw / 2.0))
        top_y = max(0.0, min(sh - ch, cy - ch / 2.0))
        return f"crop={cw}:{ch}:{top_x:.1f}:{top_y:.1f},scale={ow}:{oh}:flags=lanczos,setsar=1"

    else:
        # Full-Screen Single Speaker
        cw = max(2, min(sw, int(sh * 9 / 16) & ~1))
        ch = max(2, min(sh, int(cw * 16 / 9) & ~1))
        t = seg.target_a
        cx = t.get("cx", sw / 2.0) or (sw / 2.0)
        cy = t.get("cy", sh / 2.0) or (sh / 2.0)
        top_x = max(0.0, min(sw - cw, cx - cw / 2.0))
        top_y = max(0.0, min(sh - ch, cy - ch / 2.0))
        return f"crop={cw}:{ch}:{top_x:.1f}:{top_y:.1f},scale={ow}:{oh}:flags=lanczos,setsar=1"

