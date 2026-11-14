"""Per-shot intelligent reframing to vertical (or square) crops.

Features:
  1. MediaPipe face detection + face mesh mouth openness variance to identify
     the active speaker among multiple candidates.
  2. Persistent subject tracking with hysteresis and dwell-time: prevents
     erratic flapping between speakers and eliminates empty-center smearing.
  3. Adaptive vertical positioning & headroom law:
     - Places the subject's face in the upper third (rule of thirds: 25-36% from top).
     - Maintains 12-18% headroom above the head crown.
     - Adapts crop scale on wide shots so subjects aren't pushed into the bottom third
       under massive empty dark curtains.
  4. 2D smooth trajectory follower: both x-center and y-offset are smoothed
     with look-ahead moving average and acceleration-limited follower.
  5. Rendering: FFmpeg sendcmd drives dynamic crop (x and y) + Lanczos scaling.
  6. Automated quality validation: validate_reframing() computes framing balance
     scores and detects low/unbalanced subjects.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

from config import load_config
from errors import ReframeError
from ffutil import filter_path, probe, run_ffmpeg, video_encode_args
from logutil import get_logger

log = get_logger("reframe")


# ------------------------------------------------------- pure path helpers

def smooth_path(raw: list[float], ema_alpha: float, lookahead: int) -> list[float]:
    """Centered moving average (look-ahead: future frames influence current
    crop) followed by EMA. Pure, unit-tested."""
    if not raw:
        return []
    arr = np.asarray(raw, dtype=np.float64)
    k = max(1, int(lookahead))
    kernel = np.ones(2 * k + 1) / (2 * k + 1)
    padded = np.pad(arr, k, mode="edge")
    ma = np.convolve(padded, kernel, mode="valid")
    out = [ma[0]]
    for v in ma[1:]:
        out.append(out[-1] + ema_alpha * (v - out[-1]))
    return [float(x) for x in out]


def clamp_velocity(path: list[float], max_v: float) -> list[float]:
    """Limit per-frame movement of the crop center. Pure, unit-tested."""
    if not path:
        return []
    out = [path[0]]
    for v in path[1:]:
        step = v - out[-1]
        out.append(out[-1] + max(-max_v, min(max_v, step)))
    return out


def follow_path(targets: list[float], max_v: float, max_a: float) -> list[float]:
    """Acceleration-limited trapezoidal follower: tracks `targets` while
    guaranteeing |velocity| <= max_v and |Delta velocity| <= max_a per frame BY
    CONSTRUCTION. Pure, unit-tested."""
    if not targets:
        return []
    pos, vel = float(targets[0]), 0.0
    out = [pos]
    for target in targets[1:]:
        err = target - pos
        brake = math.sqrt(2.0 * max_a * abs(err)) if err else 0.0
        desired = math.copysign(min(brake, max_v, abs(err)), err)
        vel += max(-max_a, min(max_a, desired - vel))
        vel = max(-max_v, min(max_v, vel))
        pos += vel
        out.append(pos)
    return out


def path_metrics(path: list[float]) -> dict:
    """Max per-frame velocity and acceleration of a crop-center path."""
    if len(path) < 3:
        return {"max_velocity": 0.0, "max_accel": 0.0}
    a = np.asarray(path)
    vel = np.diff(a)
    acc = np.diff(vel)
    return {"max_velocity": float(np.max(np.abs(vel))),
            "max_accel": float(np.max(np.abs(acc)))}


def enforce_smoothness(path: list[float], rcfg: dict,
                       scale: float = 1.0) -> tuple[list[float], dict, bool]:
    """Run the accel-limited follower (thresholds are in OUTPUT px; `scale`
    converts source px -> output px) and verify metrics against config."""
    s = max(scale, 1e-6)
    max_v = rcfg.get("max_center_velocity_px", 28.0) / s
    max_a = rcfg.get("max_center_accel_px", 6.0) / s
    followed = follow_path(path, max_v, max_a)
    m = path_metrics([p * scale for p in followed])
    ok = (m["max_velocity"] <= rcfg.get("max_center_velocity_px", 28.0) + 1e-3
          and m["max_accel"] <= rcfg.get("max_center_accel_px", 6.0) + 1e-3)
    return followed, m, ok


def crop_geometry(w: int, h: int, aspect: str) -> tuple[int, int, int]:
    """(crop_w, crop_h, y0) for the target aspect within a w x h source."""
    if aspect == "1:1":
        side = min(w, h)
        return side, side, (h - side) // 2
    cw = int(h * 9 / 16) & ~1  # 9:16 (default)
    if cw <= w:
        return cw, h, 0
    ch = int(w * 16 / 9) & ~1  # source narrower than 9:16 - crop height
    return w, min(ch, h), max(0, (h - ch) // 2)


# ---------------------------------------------------------- target tracking

def _mouth_openness(landmarks, h: int) -> float:
    """Lip gap (landmarks 13/14) normalized by face height (10/152)."""
    top, bottom = landmarks[13], landmarks[14]
    fh = abs(landmarks[152].y - landmarks[10].y) * h
    return abs(bottom.y - top.y) * h / max(fh, 1e-6)


def apply_tripod_deadband(targets: list[float], deadband_px: float,
                          min_val: float, max_val: float) -> list[float]:
    """Tripod-first spatial deadband: holds camera completely static while
    the subject remains inside the safe zone [-deadband_px, +deadband_px].
    Smoothly repositions only when the subject meaningfully exits the safe zone."""
    if not targets:
        return []
    anchor = float(targets[0])
    out = [anchor]
    for raw in targets[1:]:
        delta = raw - anchor
        if abs(delta) > deadband_px:
            shift = delta - math.copysign(deadband_px, delta)
            anchor += 0.28 * shift
        anchor = max(min_val, min(max_val, anchor))
        out.append(anchor)
    return out


class SubjectTracker:
    """Persistent subject tracker across frames: tracks multiple faces,
    maintains spatial track identity, scores active speakers with area
    normalization and laughter filtering, applies hysteresis to prevent
    erratic switching, provides deadband anti-jitter, sampling-aware face
    disappearance recovery, and multi-person 2-shot framing."""

    def __init__(self, w: int, h: int, deadband_px: float = 4.0, fps: float = 12.0):
        self.w = w
        self.h = h
        self.deadband_px = deadband_px
        self.fps = max(1.0, float(fps))
        self.grace_frames = max(3, int(round(1.6 * self.fps)))
        self.stale_frames = max(5, int(round(2.0 * self.fps)))
        self.dwell_threshold = max(3, int(round(0.5 * self.fps)))
        self.tracks: dict[int, dict] = {}
        self.next_id = 0
        self.active_id: int | None = None
        self.active_dwell = 0
        self.mouth_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=16))
        self.frames_since_face = 0

    def update(self, faces_data: list[tuple[float, float, float, float, float, float]],
               frame_idx: int, audio_active: bool = True) -> dict | None:
        """faces_data: list of (cx, cy, fw, fh, conf, mouth_open).
        audio_active: synchronized speech activity indicator for the current frame."""
        if not faces_data:
            self.frames_since_face += 1
            # Dynamic sampling-aware face disappearance recovery: hold track up to 1.6s
            if self.frames_since_face <= self.grace_frames and self.active_id in self.tracks:
                rec = dict(self.tracks[self.active_id])
                rec["recovered"] = True
                return rec
            return None

        self.frames_since_face = 0
        matched_tracks = set()
        for cx, cy, fw, fh, conf, m_open in faces_data:
            best_id, best_dist = None, 0.25 * self.w
            for tid, t in self.tracks.items():
                if tid in matched_tracks:
                    continue
                d = math.hypot(cx - t["cx"], cy - t["cy"])
                if d < best_dist:
                    best_dist = d
                    best_id = tid
            if best_id is None:
                best_id = self.next_id
                self.next_id += 1
                self.tracks[best_id] = {
                    "cx": cx, "cy": cy, "w": fw, "h": fh,
                    "first_frame": frame_idx, "last_frame": frame_idx
                }
            matched_tracks.add(best_id)
            t = self.tracks[best_id]

            # Deadband anti-jitter: suppress micro-jitter when subject is stationary
            dx = cx - t["cx"]
            dy = cy - t["cy"]
            eff_cx = t["cx"] if abs(dx) < self.deadband_px else cx
            eff_cy = t["cy"] if abs(dy) < self.deadband_px else cy

            t["cx"] = 0.65 * t["cx"] + 0.35 * eff_cx
            t["cy"] = 0.65 * t["cy"] + 0.35 * eff_cy
            t["w"] = 0.65 * t["w"] + 0.35 * fw
            t["h"] = 0.65 * t["h"] + 0.35 * fh
            t["last_frame"] = frame_idx
            self.mouth_hist[best_id].append(m_open)

        # prune stale tracks based on actual sampling rate (default ~2.0s)
        stale = [tid for tid, t in self.tracks.items() if frame_idx - t["last_frame"] > self.stale_frames]
        for tid in stale:
            del self.tracks[tid]
            if tid in self.mouth_hist:
                del self.mouth_hist[tid]

        if not self.tracks:
            return None

        # Multi-person 2-shot conversation framing:
        # If 2 or more faces are detected, check if their span fits comfortably inside 9:16 crop width
        primary_face, secondary_face = {}, {}
        if len(faces_data) >= 2:
            crop_w_est = int(self.h * 9 / 16)
            sorted_faces = sorted(faces_data, key=lambda f: f[2] * f[3], reverse=True)[:2]
            f1, f2 = sorted_faces[0], sorted_faces[1]
            primary_face = {"cx": f1[0], "cy": f1[1], "fw": f1[2], "fh": f1[3]}
            secondary_face = {"cx": f2[0], "cy": f2[1], "fw": f2[2], "fh": f2[3]}
            min_x = min(f1[0] - f1[2] / 2.0, f2[0] - f2[2] / 2.0)
            max_x = max(f1[0] + f1[2] / 2.0, f2[0] + f2[2] / 2.0)
            span_x = max_x - min_x
            if span_x <= 0.85 * crop_w_est:
                raw_cx = (min_x + max_x) / 2.0
                raw_cy = min(f1[1], f2[1])
                if hasattr(self, "_last_multi_cx") and abs(raw_cx - self._last_multi_cx) < self.deadband_px:
                    multi_cx = self._last_multi_cx
                else:
                    multi_cx = raw_cx
                    self._last_multi_cx = raw_cx
                return {
                    "cx": multi_cx,
                    "cy": raw_cy,
                    "w": span_x,
                    "h": max(f1[3], f2[3]),
                    "multi_person": True,
                    "num_faces": len(faces_data),
                    "primary_face": primary_face,
                    "secondary_face": secondary_face,
                    "first_frame": frame_idx,
                    "last_frame": frame_idx
                }

        # score tracks based on normalized area + syllabic mouth dynamics + audio correlation
        scored = []
        for tid, t in self.tracks.items():
            hist = self.mouth_hist[tid]
            has_history = len(hist) >= 4
            mean_open = float(np.mean(hist)) if hist else 0.0
            var = float(np.var(hist)) if len(hist) >= 3 else 0.0
            latest = hist[-1] if hist else 0.0

            # Syllabic speech has rhythmic variation between consonants and vowels.
            # Static wide-open laughing/smiling listeners (sustained high mean with near-zero var across 4+ frames) are penalized.
            if has_history and mean_open > 0.45 and var < 0.003:
                speech_boost = 0.5  # sustained laughing/smiling listener discount
            elif has_history:
                speech_boost = 1.0 + 4.5 * min(var * 100.0, 2.0)
            else:
                # Initial frames before history accumulates: use instantaneous mouth openness
                speech_boost = 1.0 + 3.0 * min(latest * 2.0, 2.0)

            # Audio-visual evidence correlation:
            # During audio silence or background pauses, mouth movements (chewing, smiling, laughing)
            # are NOT vocal speech. Heavily discount to prevent silent listeners from stealing the frame.
            if not audio_active:
                speech_boost *= 0.35
            else:
                # Audio is active: speech-like syllabic variation receives an extra confidence boost
                if var >= 0.003:
                    speech_boost *= 1.25

            # Area normalization: use square-root of area so proximity doesn't drown active background speakers
            norm_dim = math.sqrt(max(10.0, t["w"] * t["h"]))
            score = norm_dim * speech_boost
            confidence = min(1.0, max(0.1, (score / max(1.0, norm_dim)) * (1.0 if audio_active else 0.6)))
            t["speaker_confidence"] = confidence
            scored.append((tid, score, t))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_tid, top_score, top_t = scored[0]

        # hysteresis / stickiness: responsive switching without erratic jitter.
        # CRITICAL: Speaker switching requires active audio evidence (cannot switch during a pause).
        if self.active_id in self.tracks:
            curr_score = next((s for tid, s, _ in scored if tid == self.active_id), 0.0)
            curr_hist = self.mouth_hist.get(self.active_id, [])
            curr_is_laughter = len(curr_hist) >= 4 and float(np.mean(curr_hist)) > 0.45 and float(np.var(curr_hist)) < 0.003
            needed_dwell = max(2, self.dwell_threshold // 2) if curr_is_laughter else self.dwell_threshold

            if audio_active and top_tid != self.active_id and top_score > curr_score * 1.35:
                self.active_dwell += 1
                if self.active_dwell >= needed_dwell:  # time-scaled hold (~0.3-0.5s)
                    self.active_id = top_tid
                    self.active_dwell = 0
            else:
                self.active_dwell = 0
        else:
            self.active_id = top_tid
            self.active_dwell = 0

        res = dict(self.tracks[self.active_id])
        res["multi_person"] = False
        res["num_faces"] = len(faces_data)
        res["active_id"] = self.active_id
        res["speaker_confidence"] = res.get("speaker_confidence", 0.8)
        if primary_face:
            res["primary_face"] = primary_face
            res["secondary_face"] = secondary_face
        return res


def track_targets(clip_path: Path, rcfg: dict, w: int, h: int,
                  every: int | None = None,
                  audio_activity: np.ndarray | None = None) -> tuple[list[dict | None], dict]:
    """Sampled targets: returns list of target dicts:
       {'cx': float, 'cy': float, 'fw': float, 'fh': float, 'kind': str, ...}
       or None when no target found."""
    import cv2
    import mediapipe as mp

    every = max(1, int(every if every is not None
                       else rcfg.get("face_detect_every_n_frames", 1)))
    cap = cv2.VideoCapture(str(clip_path))
    sample_fps = float(rcfg.get("proxy_fps", 12)) / every
    tracker = SubjectTracker(w, h, fps=sample_fps)
    targets: list[dict | None] = []
    kinds: list[str] = []
    stats: dict = {"face": 0, "motion": 0, "center": 0}
    prev_gray = None

    min_conf = float(rcfg.get("min_face_confidence", 0.40))
    with mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=min_conf) as fd, \
         mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False, max_num_faces=4,
            refine_landmarks=False, min_detection_confidence=min_conf) as fm:
        idx = 0
        while True:
            if idx % every:
                if not cap.grab():
                    break
                idx += 1
                continue
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            det = fd.process(rgb)
            faces_data = []
            if det and det.detections:
                mesh = fm.process(rgb)
                m_map = {}
                if mesh and mesh.multi_face_landmarks:
                    for fl in mesh.multi_face_landmarks:
                        fcx = float(np.mean([p.x for p in fl.landmark])) * w
                        m_map[fcx] = _mouth_openness(fl.landmark, h)
                for d in det.detections:
                    bb = d.location_data.relative_bounding_box
                    cx = (bb.xmin + bb.width / 2.0) * w
                    cy = (bb.ymin + bb.height / 2.0) * h
                    fw = bb.width * w
                    fh = bb.height * h
                    closest_m = 0.0
                    if m_map:
                        closest_m = min(m_map.items(), key=lambda x: abs(x[0] - cx))[1]
                    faces_data.append((cx, cy, fw, fh, float(d.score[0]), closest_m))

            is_audio_active = bool(audio_activity[idx]) if (audio_activity is not None and idx < len(audio_activity)) else True
            active_t = tracker.update(faces_data, idx, audio_active=is_audio_active)
            if active_t is not None:
                targets.append({
                    "cx": float(active_t["cx"]),
                    "cy": float(active_t["cy"]),
                    "w": float(active_t.get("w", active_t.get("fw", 0.0))),
                    "h": float(active_t.get("h", active_t.get("fh", 0.0))),
                    "fw": float(active_t.get("w", active_t.get("fw", 0.0))),
                    "fh": float(active_t.get("h", active_t.get("fh", 0.0))),
                    "kind": "face",
                    "num_faces": int(active_t.get("num_faces", 1)),
                    "multi_person": bool(active_t.get("multi_person", False)),
                    "primary_face": active_t.get("primary_face") or {},
                    "secondary_face": active_t.get("secondary_face") or {},
                    "speaker_confidence": float(active_t.get("speaker_confidence", 1.0)),
                    "active_id": active_t.get("active_id", 0),
                })
                kinds.append("face")
                stats["face"] += 1
            else:
                gray = cv2.cvtColor(cv2.resize(frame, (w // 2 or 1, h // 2 or 1)),
                                    cv2.COLOR_BGR2GRAY)
                t_dict = None
                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                    m = cv2.moments(th)
                    if m["m00"] > 800:
                        mcx = float(np.clip(m["m10"] / m["m00"] * 2, 0, w))
                        mcy = float(np.clip(m["m01"] / m["m00"] * 2, 0, h))
                        t_dict = {"cx": mcx, "cy": mcy, "fw": 0.0, "fh": 0.0, "kind": "motion"}
                prev_gray = gray
                targets.append(t_dict)
                kinds.append("motion" if t_dict is not None else "center")
                stats["motion" if t_dict is not None else "center"] += 1
            idx += 1
    cap.release()
    stats["kinds"] = kinds
    stats["tracks"] = {tid: dict(t) for tid, t in tracker.tracks.items()}
    return targets, stats


def extract_audio_activity(source: str | Path, start: float, dur: float,
                           target_fps: float = 12.0, sample_rate: int = 16000) -> np.ndarray:
    """Extract audio speech activity timeline (boolean speech active per video frame)
    using short-time RMS energy, adaptive dynamic range thresholding, and temporal hangover smoothing."""
    import subprocess
    from ffutil import ffmpeg_bin

    n_frames = max(1, int(round(dur * target_fps)))
    cmd = [
        ffmpeg_bin(), "-v", "error",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{dur:.3f}",
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        if proc.returncode != 0 or not proc.stdout:
            return np.ones(n_frames, dtype=bool)

        audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < 100:
            return np.ones(n_frames, dtype=bool)

        win_size = int(sample_rate * 0.030)  # 30ms window
        hop_size = int(sample_rate / target_fps)

        rms_vals = np.zeros(n_frames, dtype=np.float32)
        for i in range(n_frames):
            center = int(i * hop_size)
            w0 = max(0, center - win_size // 2)
            w1 = min(len(audio), center + win_size // 2)
            chunk = audio[w0:w1]
            if len(chunk) > 0:
                rms_vals[i] = float(np.sqrt(np.mean(chunk * chunk)))

        eps = 1e-5
        dbfs = 20.0 * np.log10(np.maximum(rms_vals, eps))

        db_min = float(np.min(dbfs))
        db_max = float(np.max(dbfs))
        db_range = db_max - db_min

        if db_range > 7.0:
            # Contrast exists between speech and pauses
            baseline = float(np.percentile(dbfs, 25))
            threshold = baseline + 0.25 * (db_max - baseline)
        else:
            # Low dynamic range (continuous audio / background music)
            threshold = -42.0

        raw_mask = dbfs > threshold

        # Temporal hangover smoothing: bridge short 1-2 frame inter-word pauses (~100-160ms)
        smoothed = np.copy(raw_mask)
        for i in range(1, len(smoothed) - 1):
            if not smoothed[i] and smoothed[i - 1] and smoothed[i + 1]:
                smoothed[i] = True
        return smoothed
    except Exception as e:
        log.warning("audio activity extraction failed (%s) - defaulting to speech active", e)
        return np.ones(n_frames, dtype=bool)


def _scale_target_dict(t: dict, sx: float, sy: float) -> dict:
    if not t:
        return {}
    out = dict(t)
    for k in ("cx", "fw", "w"):
        if k in out and out[k] is not None:
            out[k] = float(out[k]) * sx
    for k in ("cy", "fh", "h"):
        if k in out and out[k] is not None:
            out[k] = float(out[k]) * sy
    if "primary_face" in out and isinstance(out["primary_face"], dict):
        out["primary_face"] = _scale_target_dict(out["primary_face"], sx, sy)
    if "secondary_face" in out and isinstance(out["secondary_face"], dict):
        out["secondary_face"] = _scale_target_dict(out["secondary_face"], sx, sy)
    return out


def analyze_clip_for_director(source: Path | str, start: float, dur: float,
                              cfg: dict | None = None) -> tuple[list[dict | None], dict[int, list[dict]], dict]:
    """Extract per-frame targets, active face tracks, and audio-visual speech activity for dynamic directing.
    Returns (raw_targets, face_tracks, stats) properly scaled to source video resolution."""
    cfg = cfg or load_config()
    rcfg = cfg.get("reframe", {})
    source = Path(source)
    proxy_h = int(rcfg.get("proxy_height", 360))
    proxy_fps = float(rcfg.get("proxy_fps", 12))

    src_info = probe(source)
    sw = int(src_info["width"])
    sh = int(src_info["height"])

    proxy_path = source.parent / f"_proxy_dir_{source.stem}_{int(start * 100)}.mp4"
    try:
        _build_proxy(source, start, dur, proxy_path, proxy_h, proxy_fps)
        pinfo = probe(proxy_path)
        pw, ph = int(pinfo["width"]), int(pinfo["height"])
        scale_x = sw / max(pw, 1)
        scale_y = sh / max(ph, 1)

        # Extract audio activity
        audio_act = extract_audio_activity(source, start, dur, target_fps=proxy_fps)
        targets, stats = track_targets(proxy_path, rcfg, pw, ph, every=1, audio_activity=audio_act)
        tracks = stats.get("tracks", {})

        scaled_targets = [_scale_target_dict(t, scale_x, scale_y) if t else t for t in targets]
        scaled_tracks = {}
        for tid, tr in tracks.items():
            strack = dict(tr)
            for k in ("mean_cx", "mean_fw"):
                if k in strack and strack[k] is not None:
                    strack[k] = float(strack[k]) * scale_x
            for k in ("mean_cy", "mean_fh"):
                if k in strack and strack[k] is not None:
                    strack[k] = float(strack[k]) * scale_y
            scaled_tracks[tid] = strack

        return scaled_targets, scaled_tracks, stats
    finally:
        proxy_path.unlink(missing_ok=True)


# --------------------------------------------------------------- main entry

def _build_proxy(source: Path, start: float, dur: float, proxy: Path,
                 proxy_h: int, proxy_fps: float) -> None:
    """Small, cheap, low-fps proxy for tracking. Fast keyframe seek."""
    run_ffmpeg(["-ss", f"{start:.3f}", "-i", source, "-t", f"{dur:.3f}", "-an",
                "-vf", f"scale=-2:{proxy_h},fps={proxy_fps}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", proxy],
               timeout=900)


def event_cut_bounds(cut_frames: list[int], event_frames: list[int],
                     n_frames: int, hold_frames: int) -> tuple[list[int], set[int]]:
    """Combine scene-cut and reaction-event boundaries with hysteresis.
    Pure, unit-tested."""
    hold = max(0, int(hold_frames))
    accepted: set[int] = set()
    last_event = -hold - 1
    for f in sorted(set(event_frames)):
        if 0 < f < n_frames and f - last_event > hold:
            accepted.add(f)
            last_event = f
    inner = set(accepted)
    for f in sorted(set(cut_frames)):
        if 0 < f < n_frames and not any(abs(f - e) <= hold for e in accepted):
            inner.add(f)
    return [0] + sorted(inner) + [n_frames], accepted


def reframe_clip(source: str | Path, start: float, end: float,
                 out_path: str | Path, scene_cuts_rel: list[float] | None = None,
                 cfg: dict | None = None, aspect: str = "9:16",
                 debug_dir: str | Path | None = None,
                 info: dict | None = None,
                 bottom_exclusion_ratio: float = 0.0,
                 h_bias_center: float = -1.0,
                 event_cuts_rel: list[dict] | None = None,
                 crop_x_offset: float = 0.0,
                 crop_y_offset: float = 0.0,
                 manual_zoom: float = 1.0) -> dict:
    """Intelligently reframe the [start, end] window of `source` to the target aspect
    with subject-aware 2D tracking and adaptive headroom. Single re-encode."""
    cfg = cfg or load_config()
    rcfg = cfg.get("reframe", {})
    source, out_path = Path(source), Path(out_path)
    if not source.exists():
        raise ReframeError(f"source not found: {source}")
    if aspect == "16:9":
        raise ReframeError("16:9 is pass-through - pipeline must skip reframe")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    src = info or probe(source)
    w, h, fps = int(src["width"]), int(src["height"]), float(src["fps"])
    dur = end - start
    if dur <= 0:
        raise ReframeError(f"invalid range: start={start} end={end}")

    ow = int(rcfg.get("output", {}).get("width", 1080))
    oh = int(rcfg.get("output", {}).get("height", 1920))
    if aspect == "9:16":
        pass
    elif aspect == "1:1":
        oh = ow
    else:
        ow, oh = (1920, 1080) if ow >= 1080 else (1280, 720)

    # Base crop geometry
    base_cw, base_ch, base_y0 = crop_geometry(w, h, aspect)
    n_frames = max(1, int(round(dur * fps)))

    proxy_h = int(rcfg.get("proxy_height", 360))
    proxy_fps = float(rcfg.get("proxy_fps", 12))
    proxy = out_path.with_name(out_path.stem + "_proxy.mp4")

    if rcfg.get("tracking") is False or rcfg.get("face_tracking") is False:
        raw_targets, stats, pw, ph = [], {"face": 0, "motion": 0, "center": 1}, w, h
    else:
        try:
            _build_proxy(source, start, dur, proxy, proxy_h, proxy_fps)
            pinfo = probe(proxy)
            pw, ph = int(pinfo["width"]), int(pinfo["height"])
            audio_act = extract_audio_activity(source, start, dur, target_fps=proxy_fps)
            raw_targets, stats = track_targets(proxy, rcfg, pw, ph, every=1, audio_activity=audio_act)
        except Exception as e:
            log.warning("tracking failed (%s) - center crop with headroom", e)
            raw_targets, stats, pw, ph = [], {"face": 0, "motion": 0, "center": 1}, w, h
        finally:
            proxy.unlink(missing_ok=True)

    scale_x = w / max(pw, 1)
    scale_y = h / max(ph, 1)

    # 1. Determine optimal crop dimensions per subject scale
    # If aspect is 9:16, check whether the subject is small in a wide shot
    target_chs = []
    face_heights = []
    for t in raw_targets:
        if t and t.get("kind") == "face" and t.get("fh", 0) > 0:
            fh_src = t["fh"] * scale_y
            face_heights.append(fh_src)
            # If face is small (< 160px on 1080p), zoom in (min crop_h = 720px)
            if aspect == "9:16" and fh_src < 160:
                ch_val = int(np.clip(fh_src * 5.8, int(h * 0.65), h)) & ~1
            else:
                ch_val = base_ch
            target_chs.append(ch_val)
        else:
            target_chs.append(base_ch)

    # For rock-solid video encoding without frame-size resizing artifacts,
    # select a stable optimal crop height for the clip
    if target_chs and aspect == "9:16":
        med_ch = int(np.median(target_chs)) & ~1
        cw = int(med_ch * 9 / 16) & ~1
        ch = med_ch
    else:
        cw, ch = base_cw, base_ch

    # REPLACE mode: bottom subtitle exclusion
    if bottom_exclusion_ratio and 0.0 < bottom_exclusion_ratio < 0.5 and aspect == "9:16":
        eff_h = max(16, int(h * (1.0 - bottom_exclusion_ratio)) & ~1)
        cw, ch, _ = crop_geometry(w, eff_h, aspect)

    half_w = cw / 2.0

    # 2. Compute dynamic crop x and y targets
    target_xs: list[float] = []
    target_ys: list[float] = []
    last_x = w / 2.0
    last_y = 0.0

    for t in raw_targets:
        if t is not None:
            cx = t["cx"] * scale_x
            cy = t["cy"] * scale_y
            fh = t.get("fh", 0) * scale_y

            # Horizontal centering
            tx = float(np.clip(cx, half_w, w - half_w))

            # Vertical headroom & shoulder awareness (Headroom law: place head top at ~13-16% of crop_h)
            if t.get("kind") == "face" and fh > 0:
                face_top = cy - fh / 2.0
                head_top = face_top - 0.18 * fh
                headroom_px = 0.14 * ch
                desired_y = head_top - headroom_px
                # Shoulder awareness: accommodate upper torso (cy + 1.25 * fh)
                shoulder_bottom = cy + 1.25 * fh
                if desired_y + ch < shoulder_bottom:
                    desired_y = max(0.0, shoulder_bottom - ch)
                ty = float(np.clip(desired_y, 0, h - ch))
            else:
                # Fallback to centering around motion/previous position
                ty = float(np.clip(cy - ch * 0.35, 0, h - ch))

            last_x = tx
            last_y = ty
        else:
            tx = last_x
            ty = last_y

        target_xs.append(tx)
        target_ys.append(ty)

    if not target_xs:
        target_xs = [w / 2.0]
        target_ys = [0.0]

    # Tripod-First Deadband: hold camera completely static while subject remains
    # inside the composition safe corridor (horizontal: ~20% of crop_w, vertical: ~10% of crop_h).
    # Eliminates handheld camera floating motion while preserving smooth dynamic pans when needed.
    deadband_x = max(24.0, cw * 0.20)
    deadband_y = max(16.0, ch * 0.10)
    target_xs = apply_tripod_deadband(target_xs, deadband_x, half_w, w - half_w)
    target_ys = apply_tripod_deadband(target_ys, deadband_y, 0.0, h - ch)

    # Interpolate sampled proxy targets onto every output frame
    sample_times = np.arange(len(target_xs)) / proxy_fps
    frame_times = np.arange(n_frames) / fps
    full_x = np.interp(frame_times, sample_times, target_xs).tolist()
    full_y = np.interp(frame_times, sample_times, target_ys).tolist()

    # Apply manual framing offsets from Studio if specified
    if crop_x_offset:
        full_x = [float(np.clip(px + crop_x_offset, half_w, w - half_w)) for px in full_x]
    if crop_y_offset:
        full_y = [float(np.clip(py + crop_y_offset, 0.0, h - ch)) for py in full_y]

    # KEEP mode: bias horizontal crop toward center subtitle if requested
    if 0.0 <= h_bias_center <= 1.0:
        bias_x = float(np.clip(h_bias_center * w, half_w, w - half_w))
        full_x = [float(np.clip(p + 0.6 * (bias_x - p), half_w, w - half_w)) for p in full_x]
        log.info("KEEP: biasing horizontal crop toward x=%.0f", bias_x)

    # Per-scene segments: smoothing resets at shot boundaries
    cut_frames = sorted({int(t * fps) for t in (scene_cuts_rel or []) if 0 < t < dur})
    kinds = stats.get("kinds", [])
    hold_frames = int(float(cfg.get("viral_v2", {}).get("min_shot_s", 1.5)) * fps)
    event_frames = []
    for e in (event_cuts_rel or []):
        t = float(e.get("t", -1.0))
        if 0 < t < dur:
            s_idx = int(round(t * proxy_fps))
            if 0 <= s_idx < len(kinds) and kinds[s_idx] == "face":
                event_frames.append(int(t * fps))

    bounds, accepted = event_cut_bounds(cut_frames, event_frames, n_frames, hold_frames)
    for f in accepted:
        full_x[f:min(f + hold_frames, n_frames)] = [full_x[f]] * (min(f + hold_frames, n_frames) - f)
        full_y[f:min(f + hold_frames, n_frames)] = [full_y[f]] * (min(f + hold_frames, n_frames) - f)

    scale = ow / cw  # source px -> output px
    path_x, path_y = [], []
    all_ok, worst = True, {"max_velocity": 0.0, "max_accel": 0.0}

    for a, b in zip(bounds[:-1], bounds[1:]):
        seg_x = smooth_path(full_x[a:b], rcfg.get("ema_alpha", 0.12), rcfg.get("lookahead_frames", 12))
        seg_x, mx, ok_x = enforce_smoothness(seg_x, rcfg, scale)
        path_x.extend(seg_x)

        seg_y = smooth_path(full_y[a:b], rcfg.get("ema_alpha", 0.12), rcfg.get("lookahead_frames", 12))
        seg_y, my, ok_y = enforce_smoothness(seg_y, rcfg, scale)
        path_y.extend(seg_y)

        all_ok &= (ok_x and ok_y)
        worst = {k: max(worst[k], mx[k], my[k]) for k in worst}

    # Controlled dynamic zoom
    zoom_cfg = rcfg.get("dynamic_zoom", {})
    zoom_enabled = bool(zoom_cfg.get("enabled", True))
    max_zoom = float(np.clip(zoom_cfg.get("max_zoom", 1.12), 1.05, 1.18))
    zooms = np.ones(n_frames, dtype=float)

    if zoom_enabled and (event_cuts_rel or []):
        for ev in event_cuts_rel:
            t_ev = float(ev.get("t", -1.0))
            if 0 < t_ev < dur:
                f_ev = int(t_ev * fps)
                ramp_in = max(1, int(0.3 * fps))
                hold_len = max(1, int(1.0 * fps))
                ramp_out = max(1, int(0.4 * fps))
                total_ev_frames = ramp_in + hold_len + ramp_out
                for k in range(total_ev_frames):
                    frame_i = f_ev + k
                    if 0 <= frame_i < n_frames:
                        if k < ramp_in:
                            alpha = 0.5 * (1.0 - math.cos(math.pi * k / ramp_in))
                        elif k < ramp_in + hold_len:
                            alpha = 1.0
                        else:
                            out_k = k - (ramp_in + hold_len)
                            alpha = 0.5 * (1.0 + math.cos(math.pi * out_k / ramp_out))
                        target_z = 1.0 + (max_zoom - 1.0) * alpha
                        zooms[frame_i] = max(zooms[frame_i], target_z)

    # Suppress zoom during multi-person shots so both faces stay fully framed
    if raw_targets:
        for i in range(n_frames):
            s_idx = min(int(round((i / fps) * proxy_fps)), len(raw_targets) - 1)
            if s_idx >= 0 and raw_targets[s_idx] and raw_targets[s_idx].get("multi_person"):
                zooms[i] = 1.0

    # Apply manual zoom from Studio if specified
    if manual_zoom and manual_zoom != 1.0:
        zooms = zooms * max(1.0, float(manual_zoom))

    # ffmpeg sendcmd file: crop x, y, w, h command per frame
    cmd_file = out_path.with_suffix(".cmds.txt")
    with open(cmd_file, "w", encoding="ascii") as f:
        for i in range(n_frames):
            z_i = float(zooms[i])
            cw_i = int(cw / z_i) & ~1
            ch_i = int(ch / z_i) & ~1
            top_x = max(0.0, min(w - cw_i, path_x[i] - cw_i / 2.0))
            top_y = max(0.0, min(h - ch_i, path_y[i]))
            f.write(f"{i / fps:.4f} crop@dyn w {cw_i}, crop@dyn h {ch_i}, crop@dyn x {top_x:.1f}, crop@dyn y {top_y:.1f};\n")

    init_top_x = max(0.0, min(w - cw, path_x[0] - half_w))
    init_top_y = max(0.0, min(h - ch, path_y[0]))

    vf = (f"sendcmd=f='{filter_path(cmd_file)}',"
          f"crop@dyn=w={cw}:h={ch}:x={init_top_x:.1f}:y={init_top_y:.1f},"
          f"scale={ow}:{oh}:flags=lanczos,setsar=1")

    run_ffmpeg(["-ss", f"{start:.3f}", "-i", source, "-t", f"{dur:.3f}",
                "-vf", vf] + video_encode_args(cfg)
               + ["-c:a", "aac", "-b:a", cfg["render"]["audio_bitrate"],
                  "-movflags", "+faststart", out_path])

    metrics = {
        "aspect": aspect,
        "crop": [cw, ch],
        "output": [ow, oh],
        "dynamic_zoom": {"enabled": zoom_enabled, "max_applied_zoom": round(float(np.max(zooms)), 3)},
        "tracking": {k: v for k, v in stats.items() if k != "kinds"},
        "smoothness": worst,
        "smoothness_ok": bool(all_ok),
        "segments": len(bounds) - 1,
        "event_cuts": sorted(round(f / fps, 2) for f in accepted),
        "mean_face_height": round(float(np.mean(face_heights)), 1) if face_heights else 0.0
    }
    log.info("reframed %s [%.1f-%.1f]: crop=%dx%d tracking=%s smoothness=%s ok=%s",
             source.name, start, end, cw, ch, stats,
             {k: round(v, 2) for k, v in worst.items()}, all_ok)

    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        (Path(debug_dir) / f"{out_path.stem}_croppath.json").write_text(
            json.dumps({"metrics": metrics, "path_x_every_10": path_x[::10],
                        "path_y_every_10": path_y[::10]}),
            encoding="utf-8")
    if not cmd_file.exists() or not out_path.exists():
        raise ReframeError("reframe output missing")
    cmd_file.unlink(missing_ok=True)
    return metrics


def validate_reframing(clip_path: str | Path, aspect: str = "9:16",
                       n_samples: int = 12) -> dict:
    """Automated visual quality validation for reframed clips. Evaluates face
    visibility, vertical framing balance, headroom, and flags low-placed subjects."""
    import cv2
    import mediapipe as mp

    clip_path = Path(clip_path)
    if not clip_path.exists():
        return {"error": f"file not found: {clip_path}"}

    cap = cv2.VideoCapture(str(clip_path))
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    if total_f <= 0:
        cap.release()
        return {"error": "unreadable video"}

    step = max(1, total_f // n_samples)
    detected_faces = 0
    top_pcts = []
    headroom_pcts = []
    low_subject_frames = 0

    with mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.40) as fd:
        for idx in range(0, total_f, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            det = fd.process(rgb)
            if det and det.detections:
                detected_faces += 1
                # largest face in frame
                best = max(det.detections,
                           key=lambda d: d.location_data.relative_bounding_box.width *
                                         d.location_data.relative_bounding_box.height)
                bb = best.location_data.relative_bounding_box
                y_top = float(bb.ymin)
                fh = float(bb.height)
                top_pcts.append(y_top)
                headroom_pcts.append(max(0.0, y_top - 0.15 * fh))
                if y_top > 0.52:  # face top sits in the lower half of frame
                    low_subject_frames += 1

    cap.release()
    samples_checked = len(top_pcts) or 1
    mean_top = float(np.mean(top_pcts)) if top_pcts else 0.5
    mean_headroom = float(np.mean(headroom_pcts)) if headroom_pcts else 0.5
    detection_ratio = detected_faces / max(1, (total_f // step) or 1)

    # Ideal portrait eye level sits around 25-36% from top
    # Score penalizes subjects pushed into lower half (mean_top > 0.45)
    score = 100.0
    if mean_top > 0.50:
        score -= min(60.0, (mean_top - 0.50) * 200.0)
    elif mean_top < 0.10:
        score -= min(40.0, (0.10 - mean_top) * 200.0)

    if low_subject_frames > samples_checked * 0.3:
        score -= 25.0

    score = max(0.0, min(100.0, score))

    return {
        "samples_checked": samples_checked,
        "face_detected_ratio": round(detection_ratio, 2),
        "mean_face_top_pct": round(mean_top * 100.0, 1),
        "mean_headroom_pct": round(mean_headroom * 100.0, 1),
        "is_low_subject": bool(mean_top > 0.52 or low_subject_frames > 2),
        "composition_score": round(score, 1)
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="smoke: reframe a source window")
    ap.add_argument("source")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("--out", default="output/_smoke_reframe.mp4")
    ap.add_argument("--aspect", default="9:16")
    a = ap.parse_args()
    print(json.dumps(reframe_clip(a.source, a.start, a.end, a.out,
                                  aspect=a.aspect), indent=2))
