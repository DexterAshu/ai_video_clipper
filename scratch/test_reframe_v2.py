import cv2
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import ffutil
from config import load_config
import mediapipe as mp

def smooth_path(raw: list[float], ema_alpha: float = 0.12, lookahead: int = 15) -> list[float]:
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

def follow_path(targets: list[float], max_v: float = 12.0, max_a: float = 2.0) -> list[float]:
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

class SubjectTracker:
    def __init__(self, w: int, h: int):
        self.w = w
        self.h = h
        self.tracks = {} # id -> dict
        self.next_id = 0
        self.active_id = None
        self.active_dwell = 0
        self.mouth_hist = defaultdict(lambda: deque(maxlen=16))

    def update(self, faces_data, frame_idx):
        # faces_data: list of (cx, cy, fw, fh, conf, mouth_open)
        matched_tracks = set()
        for cx, cy, fw, fh, conf, m_open in faces_data:
            # find best matching existing track within distance
            best_id, best_dist = None, 0.22 * self.w
            for tid, t in self.tracks.items():
                if tid in matched_tracks:
                    continue
                d = math.hypot(cx - t['cx'], cy - t['cy'])
                if d < best_dist:
                    best_dist = d
                    best_id = tid
            if best_id is None:
                best_id = self.next_id
                self.next_id += 1
                self.tracks[best_id] = {'cx': cx, 'cy': cy, 'w': fw, 'h': fh, 'first_frame': frame_idx}
            matched_tracks.add(best_id)
            # update track
            t = self.tracks[best_id]
            t['cx'] = 0.7 * t['cx'] + 0.3 * cx
            t['cy'] = 0.7 * t['cy'] + 0.3 * cy
            t['w'] = 0.7 * t['w'] + 0.3 * fw
            t['h'] = 0.7 * t['h'] + 0.3 * fh
            t['last_frame'] = frame_idx
            self.mouth_hist[best_id].append(m_open)

        # prune stale tracks
        stale = [tid for tid, t in self.tracks.items() if frame_idx - t['last_frame'] > 20]
        for tid in stale:
            del self.tracks[tid]
            if tid in self.mouth_hist:
                del self.mouth_hist[tid]

        if not self.tracks:
            return None

        # score tracks
        scored = []
        for tid, t in self.tracks.items():
            hist = self.mouth_hist[tid]
            var = float(np.var(hist)) if len(hist) >= 3 else 0.0
            score = (t['w'] * t['h']) * (1.0 + 3.0 * min(var * 100, 2.0))
            scored.append((tid, score, t))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_tid, top_score, top_t = scored[0]

        # stickiness / hysteresis: don't switch active_id on minor noise
        if self.active_id in self.tracks:
            curr_score = next((s for tid, s, _ in scored if tid == self.active_id), 0.0)
            if top_tid != self.active_id and top_score > curr_score * 1.6:
                self.active_dwell += 1
                if self.active_dwell > 6: # hold for ~0.5s at 12fps before switching
                    self.active_id = top_tid
                    self.active_dwell = 0
            else:
                self.active_dwell = 0
        else:
            self.active_id = top_tid
            self.active_dwell = 0

        return self.tracks[self.active_id]

def _mouth_openness(landmarks, h: int) -> float:
    top, bottom = landmarks[13], landmarks[14]
    fh = abs(landmarks[152].y - landmarks[10].y) * h
    return abs(bottom.y - top.y) * h / max(fh, 1e-6)

def run_test():
    cut_path = Path('output/20261114-014346_6bb5e1ed_INDIA_S_GOT_LATENT_S2_EP7_ft-_N/clip_01/cut.mp4')
    out_test = Path('scratch/test_reframed_v2.mp4')
    out_test.parent.mkdir(parents=True, exist_ok=True)

    info = ffutil.probe(cut_path)
    w, h, fps = int(info['width']), int(info['height']), float(info['fps'])
    dur = float(info['duration'])
    n_frames = int(round(dur * fps))

    # proxy pass
    proxy = Path('scratch/temp_proxy.mp4')
    ffutil.run_ffmpeg(['-y', '-i', str(cut_path), '-vf', 'scale=-2:360,fps=12', '-c:v', 'libx264', '-preset', 'ultrafast', '-an', str(proxy)])
    pinfo = ffutil.probe(proxy)
    pw, ph, pfps = int(pinfo['width']), int(pinfo['height']), float(pinfo['fps'])

    cap = cv2.VideoCapture(str(proxy))
    tracker = SubjectTracker(pw, ph)

    raw_targets = []
    with mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.4) as fd, \
         mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=4, refine_landmarks=False, min_detection_confidence=0.4) as fm:
        f_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            det = fd.process(rgb)
            faces_data = []
            if det.detections:
                mesh = fm.process(rgb)
                m_map = {}
                if mesh.multi_face_landmarks:
                    for fl in mesh.multi_face_landmarks:
                        fcx = float(np.mean([p.x for p in fl.landmark])) * pw
                        m_map[fcx] = _mouth_openness(fl.landmark, ph)
                for d in det.detections:
                    bb = d.location_data.relative_bounding_box
                    cx = (bb.xmin + bb.width / 2) * pw
                    cy = (bb.ymin + bb.height / 2) * ph
                    fw = bb.width * pw
                    fh = bb.height * ph
                    closest_m = 0.0
                    if m_map:
                        closest_m = min(m_map.items(), key=lambda x: abs(x[0] - cx))[1]
                    faces_data.append((cx, cy, fw, fh, float(d.score[0]), closest_m))

            active_t = tracker.update(faces_data, f_idx)
            raw_targets.append(active_t)
            f_idx += 1
    cap.release()
    proxy.unlink(missing_ok=True)

    # Scale to full source resolution
    scale_x = w / pw
    scale_y = h / ph

    target_xs = []
    target_ys = []
    target_cws = []
    target_chs = []

    # Default fallback full crop
    def_ch = h
    def_cw = int(def_ch * 9 / 16) & ~1

    last_x = w / 2.0
    last_y = 0.0
    last_ch = def_ch
    last_cw = def_cw

    for t in raw_targets:
        if t is not None:
            cx = t['cx'] * scale_x
            cy = t['cy'] * scale_y
            fw = t['w'] * scale_x
            fh = t['h'] * scale_y

            # Adaptive crop height based on face size:
            # If face is small (< 160px), zoom in to keep subject prominent (min crop_h = 740px)
            # If face is large (>= 180px), use full height 1080px (no zoom needed)
            if fh < 160:
                ch = int(np.clip(fh * 5.8, 720, h)) & ~1
            else:
                ch = h
            cw = int(ch * 9 / 16) & ~1

            # Headroom calculation:
            # Head top is roughly face_y - 0.20 * fh
            # Place head top at ~13-16% of crop height
            face_top = cy - fh / 2
            head_top = face_top - 0.18 * fh
            headroom_px = 0.14 * ch
            target_crop_y = head_top - headroom_px
            target_crop_y = float(np.clip(target_crop_y, 0, h - ch))

            target_crop_x = float(np.clip(cx, cw / 2.0, w - cw / 2.0))

            last_x = target_crop_x
            last_y = target_crop_y
            last_ch = ch
            last_cw = cw
        else:
            # Smoothly hold last position
            target_crop_x = last_x
            target_crop_y = last_y
            last_ch = def_ch
            last_cw = def_cw

        target_xs.append(target_crop_x)
        target_ys.append(target_crop_y)
        target_chs.append(last_ch)
        target_cws.append(last_cw)

    # Smooth the chosen crop_h per shot (use median per shot or global stable crop_h)
    # To keep encoding rock-solid without dynamic resizing artifacts, choose the optimal crop_h
    # for the clip or per shot!
    median_ch = int(np.median(target_chs)) & ~1
    median_cw = int(median_ch * 9 / 16) & ~1
    print(f'Chosen optimal crop dimensions: {median_cw}x{median_ch} (from source {w}x{h})')

    # Re-clamp target_xs and target_ys with median_cw and median_ch
    clamped_xs = [float(np.clip(x, median_cw / 2.0, w - median_cw / 2.0)) for x in target_xs]
    clamped_ys = [float(np.clip(y, 0, h - median_ch)) for y in target_ys]

    # Interpolate to output frame rate
    s_times = np.arange(len(clamped_xs)) / pfps
    f_times = np.arange(n_frames) / fps
    full_x = np.interp(f_times, s_times, clamped_xs).tolist()
    full_y = np.interp(f_times, s_times, clamped_ys).tolist()

    smooth_x = follow_path(smooth_path(full_x, 0.12, 15), max_v=14.0, max_a=2.0)
    smooth_y = follow_path(smooth_path(full_y, 0.12, 15), max_v=10.0, max_a=1.5)

    cmd_file = Path('scratch/test_crop_cmds.txt')
    with open(cmd_file, 'w', encoding='ascii') as f:
        for i in range(n_frames):
            top_left_x = smooth_x[i] - median_cw / 2.0
            top_left_y = smooth_y[i]
            f.write(f'{i / fps:.4f} crop@dyn x {top_left_x:.1f}, crop@dyn y {top_left_y:.1f};\n')

    init_x = max(0.0, smooth_x[0] - median_cw / 2.0)
    init_y = max(0.0, smooth_y[0])
    vf = (f"sendcmd=f='{ffutil.filter_path(cmd_file)}',"
          f"crop@dyn=w={median_cw}:h={median_ch}:x={init_x:.1f}:y={init_y:.1f},"
          f"scale=1080:1920:flags=lanczos,setsar=1")

    cfg = load_config()
    cmd = ['-y', '-i', str(cut_path), '-vf', vf] + ffutil.video_encode_args(cfg) + ['-c:a', 'copy', str(out_test)]
    print('Encoding test reframed clip...')
    ffutil.run_ffmpeg(cmd, timeout=300)
    print(f'Done! Output written to {out_test}')

if __name__ == '__main__':
    run_test()
