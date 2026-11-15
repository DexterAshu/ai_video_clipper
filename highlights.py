"""Highlight selection: candidate windows → LLM scoring (schema-enforced,
retry → repair inside llm.py) → rule-based fallback scorer. All results are
sentence-snapped and hard-bounded to [min_seconds, max_seconds].

Also: post-render re-scoring (weighted 1–10) with the drop-bottom-30% /
keep-at-least-3 rule."""
from __future__ import annotations

import json
import math
from pathlib import Path

import llm
from config import load_config
from errors import LLMError
from logutil import get_logger
from schemas import validate

log = get_logger("highlights")

HOOK_KEYWORDS = {
    "secret", "mistake", "never", "always", "why", "how", "truth", "surprising",
    "best", "worst", "stop", "start", "nobody", "everyone", "actually", "wrong",
    "important", "amazing", "danger", "warning", "remember", "imagine", "problem",
    "simple", "easy", "free", "first", "last", "biggest", "question", "story",
}

PROMPT_TEMPLATE = """TASK: You are an elite short-form video editor for YouTube Shorts, Instagram Reels, and TikTok.
From the numbered candidate windows of the transcript below, choose the {n} most viral, engaging moments
likely to succeed as standalone 30-60 second vertical clips.

CRITICAL SELECTION CRITERIA:
1. Viral Hook (First 3 seconds): The opening statement MUST instantly grab attention (curiosity gap, bold claim, surprising confession, question, or comedic punch).
2. High Retention Value: Engaging storytelling, emotional reaction, laughter, insightful breakdown, or unexpected twist.
3. Standalone Completeness: The clip must make complete sense on its own with zero outside context (clear setup, progression, and climax/punchline).
4. Clean Sentence Boundaries: Ensure the thought begins cleanly and finishes completely; never cut off mid-thought or mid-sentence.
5. Hindi / Hinglish Sensitivity: If the audio is in Hindi or Hinglish, preserve comedic timing, iconic cultural phrases, and emotional highs.

CONSTRAINTS:
- Use ONLY the start/end times of the given windows (seconds, may adjust within transcript boundaries).
- Duration of every chosen clip must be between {min_s} and {max_s} seconds.
- "hook" = the exact punchy opening line a viewer hears first.
- "reason" = concise explanation of why this moment will hook viewers and retain them.
- "score" = predicted viral engagement score from 1.0 to 10.0 (where 9+ is top tier).
- Return AT MOST {n} candidates, ranked strongest first.

OUTPUT SCHEMA (respond with ONLY this JSON, no prose):
{schema}

EXAMPLE OUTPUT:
{{"candidates": [{{"start": 12.4, "end": 48.1, "hook": "Most projects fail for one simple reason.", "reason": "strong contrarian hook with immediate value and complete punchline", "score": 9.2}}]}}

CANDIDATE WINDOWS:
{windows}
"""


# ------------------------------------------------------------ window building

def build_windows(transcript: dict, cfg: dict, max_windows: int = 24) -> list[dict]:
    """Sentence-aligned candidate windows within [min,max] seconds."""
    min_s, max_s = cfg["clips"]["min_seconds"], cfg["clips"]["max_seconds"]
    sents = transcript["sentences"]
    windows = []
    i = 0
    while i < len(sents):
        j, start = i, sents[i]["start"]
        while j < len(sents) and sents[j]["end"] - start < min_s:
            j += 1
        if j < len(sents) and sents[j]["end"] - start <= max_s:
            windows.append({
                "start": round(start, 3),
                "end": round(sents[j]["end"], 3),
                "text": " ".join(s["text"] for s in sents[i:j + 1]),
            })
        i += 2
    if len(windows) > max_windows:  # spread evenly across the video
        step = len(windows) / max_windows
        windows = [windows[int(k * step)] for k in range(max_windows)]
    return windows


def mechanical_windows(duration: float, cfg: dict) -> list[dict]:
    """No transcript (silent/synthetic audio): fixed windows so the pipeline
    still runs end-to-end mechanically."""
    span = (cfg["clips"]["min_seconds"] + cfg["clips"]["max_seconds"]) / 2
    out, t, n = [], 0.0, 1
    while t + cfg["clips"]["min_seconds"] <= duration and len(out) < 8:
        end = min(t + span, duration)
        out.append({"start": round(t, 3), "end": round(end, 3),
                    "text": f"Segment {n}"})
        t += span
        n += 1
    return out


# ------------------------------------------------------- sentence snapping

def snap_to_sentences(start: float, end: float, sentences: list[dict],
                      min_s: float, max_s: float) -> tuple[float, float] | None:
    """Snap (start, end) to sentence boundaries with duration in [min_s, max_s].
    Pure function (unit-tested). Returns None when impossible."""
    if not sentences:
        return None
    starts = [s["start"] for s in sentences]
    ends = [s["end"] for s in sentences]
    s_idx = min(range(len(starts)), key=lambda i: abs(starts[i] - start))
    snap_start = starts[s_idx]
    # candidate ends: sentence ends after snap_start, duration within bounds
    valid = [e for e in ends if min_s <= e - snap_start <= max_s]
    if valid:
        return round(snap_start, 3), round(min(valid, key=lambda e: abs(e - end)), 3)
    # nothing in bounds from this start — try earlier starts to gain room
    for i in range(s_idx - 1, -1, -1):
        valid = [e for e in ends if min_s <= e - starts[i] <= max_s]
        if valid:
            return round(starts[i], 3), round(max(valid), 3)
    return None


def _overlap_frac(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = max(1e-6, min(a[1] - a[0], b[1] - b[0]))
    return inter / shorter


def _dedupe(cands: list[dict], max_keep: int) -> list[dict]:
    kept: list[dict] = []
    for c in sorted(cands, key=lambda c: -c["score"]):
        span = (c["start"], c["end"])
        if all(_overlap_frac(span, (k["start"], k["end"])) < 0.4 for k in kept):
            kept.append(c)
        if len(kept) >= max_keep:
            break
    return sorted(kept, key=lambda c: -c["score"])


# ------------------------------------------------- viral_v2 event fusion

def _events_in_span(events: list[dict], start: float, end: float) -> list[dict]:
    return [e for e in events
            if e["t_start_s"] < end and e["t_end_s"] > start]


def fuse_event_scores(cands: list[dict], events: list[dict],
                      cfg: dict) -> list[dict]:
    """ADD event signal to candidate scores — never gates the transcript path.
    score += density_weight * min(weighted events/min, 2.0)
           + peak_weight * max intensity in span. Clamped to 10."""
    vcfg = cfg["viral_v2"]
    weights = vcfg.get("weights", {})
    dw = float(vcfg.get("density_weight", 0.4))
    pw = float(vcfg.get("peak_weight", 0.15))
    out = []
    for c in cands:
        inside = _events_in_span(events, c["start"], c["end"])
        if not inside:
            out.append(c)
            continue
        dur_min = max(1e-6, (c["end"] - c["start"]) / 60.0)
        weighted = sum(weights.get(e["type"], 0.3) for e in inside)
        peak = max(e["intensity_1_10"] for e in inside)
        bonus = dw * min(weighted / dur_min, 2.0) + pw * peak
        out.append({**c, "score": round(min(10.0, c["score"] + bonus), 2)})
    return out


def apply_reaction_boundaries(cands: list[dict], events: list[dict],
                              cfg: dict, duration: float) -> list[dict]:
    """End on the reaction: a laughter/strong_reaction event beginning within
    reaction_window_s after a candidate's end pulls the end out to include the
    event (+1s tail), bounded by clips.max_seconds. Mirror at starts: never
    begin mid-event."""
    from video_events import REACTION_TYPES
    vcfg = cfg["viral_v2"]
    window = float(vcfg.get("reaction_window_s", 6.0))
    max_s = float(cfg["clips"]["max_seconds"])
    reactions = [e for e in events if e["type"] in REACTION_TYPES]
    out = []
    for c in cands:
        start, end = c["start"], c["end"]
        for e in reactions:
            if end < e["t_start_s"] <= end + window:
                end = max(end, min(e["t_end_s"] + 1.0, start + max_s, duration))
        for e in reactions:
            if e["t_start_s"] < start < e["t_end_s"]:
                # orig duration <= max_s, so this only ever moves start earlier
                start = max(e["t_start_s"] - 0.5, end - max_s, 0.0)
        out.append({**c, "start": round(start, 3), "end": round(end, 3)})
    return out


def event_cluster_candidates(events: list[dict], duration: float,
                             cfg: dict) -> list[dict]:
    """Candidates straight from event clusters for low-speech sources (the
    silent-recording case: a fall at 3:12:44 still becomes a clip)."""
    min_s = float(cfg["clips"]["min_seconds"])
    max_s = float(cfg["clips"]["max_seconds"])
    out: list[dict] = []
    for e in sorted(events, key=lambda e: -e["intensity_1_10"]):
        cluster = _events_in_span(events, e["t_start_s"] - max_s / 2,
                                  e["t_end_s"] + max_s / 2)
        c0 = min(x["t_start_s"] for x in cluster)
        c1 = max(x["t_end_s"] for x in cluster)
        # centre a clip-length window on the cluster, lead-in before the event
        span = min(max_s, max(min_s, (c1 - c0) + 8.0))
        start = max(0.0, min(c0 - 3.0, duration - span))
        end = min(duration, start + span)
        if end - start < min_s:
            continue
        if any(_overlap_frac((start, end), (k["start"], k["end"])) >= 0.4
               for k in out):
            continue
        desc = str(e.get("description", "")).strip()
        is_synth = (e.get("source") == "mock" or desc.lower().startswith("mock:"))
        if is_synth or not desc or desc.lower().startswith("mock:"):
            clean_hook = f"Notable {e.get('type', 'moment').replace('_', ' ')}"
        else:
            clean_hook = desc[:120]

        out.append({
            "start": round(start, 3), "end": round(end, 3),
            "hook": clean_hook,
            "reason": f"event-cluster: {len(cluster)} events, "
                      f"peak {e['type']} {e['intensity_1_10']:.0f}/10",
            "score": round(min(10.0, 4.0 + 0.5 * e["intensity_1_10"]), 2),
            "candidate_source": "synthetic_event" if is_synth else "audio_event_cluster",
            "is_synthetic": is_synth,
        })
    return out


def _is_sparse(transcript: dict, duration: float, cfg: dict) -> bool:
    if duration <= 0:
        return False
    wpm = len(transcript["text"].split()) / (duration / 60.0)
    return wpm < float(cfg["viral_v2"].get("sparse_wpm", 40))


# ---------------------------------------------------------- fallback scorer

def rule_based_candidates(windows: list[dict], cfg: dict) -> list[dict]:
    """Deterministic keyword + speech-energy heuristic (keyless fallback)."""
    out = []
    for w in windows:
        words = w["text"].split()
        dur = max(1e-6, w["end"] - w["start"])
        energy = len(words) / dur                     # words/sec ≈ speech energy
        kw = sum(1 for t in words if t.lower().strip(".,!?\"'") in HOOK_KEYWORDS)
        first = w["text"].split(".")[0]
        hook_bonus = 1.0 if any(t.lower().strip(".,!?\"'") in HOOK_KEYWORDS
                                for t in first.split()[:8]) else 0.0
        score = min(10.0, round(2.0 + energy * 1.6 + kw * 0.5 + hook_bonus, 2))
        out.append({"start": w["start"], "end": w["end"],
                    "hook": first.strip()[:120] or "An interesting moment",
                    "reason": f"rule-based: {kw} hook keywords, "
                              f"{energy:.1f} words/sec",
                    "score": score,
                    "candidate_source": "speech_energy_rule",
                    "is_synthetic": False})
    return out


# ------------------------------------------------------------- main entry

def select_highlights(transcript: dict, scenes: dict, duration: float,
                      cfg: dict | None = None, provider: str | None = None,
                      debug_dir: str | Path | None = None,
                      max_candidates: int | None = None,
                      events: list[dict] | None = None) -> list[dict]:
    """Returns validated, sentence-snapped, deduped candidate list (desc score).
    `max_candidates` overrides the config cap (used by the clip-count selector).
    `events` (viral_v2 timeline) ADDS score/boundary signal; None or empty
    leaves the transcript-only path untouched."""
    cfg = cfg or load_config()
    ccfg = cfg["clips"]
    min_s, max_s = ccfg["min_seconds"], ccfg["max_seconds"]
    max_n = int(max_candidates) if max_candidates else ccfg["max_candidates"]

    mechanical = not transcript["sentences"]
    windows = (mechanical_windows(duration, cfg) if mechanical
               else build_windows(transcript, cfg))
    if not windows:
        log.warning("video too short for %ds clips — single full-length window",
                    min_s)
        windows = [{"start": 0.0, "end": min(duration, max_s),
                    "text": transcript["text"][:500] or "Full video"}]

    if mechanical:
        cands = [{"start": w["start"], "end": w["end"], "hook": w["text"],
                  "reason": "mechanical window (no transcript)", "score": 5.0,
                  "candidate_source": "mechanical_window", "is_synthetic": False}
                 for w in windows]
    else:
        cands = _llm_or_fallback(windows, cfg, provider, min_s, max_s, max_n,
                                 debug_dir)

    snapped = []
    for c in cands:
        if mechanical:
            snapped.append(c)
            continue
        r = snap_to_sentences(c["start"], c["end"], transcript["sentences"],
                              min_s, max_s)
        if r is None:
            log.warning("candidate %.1f-%.1f could not be snapped in bounds — dropped",
                        c["start"], c["end"])
            continue
        snapped.append({**c, "start": r[0], "end": r[1]})

    if events:
        # Fusion runs AFTER sentence snapping (snapping would undo the
        # reaction-boundary extension) and before dedupe.
        cluster_cands = event_cluster_candidates(events, duration, cfg)
        if mechanical or _is_sparse(transcript, duration, cfg):
            snapped += cluster_cands
        else:
            # Even in speech-dense transcripts, include high-intensity event clusters (>= 6.0)
            # so viral events (laughter bursts, energy spikes) outside speech shortlists are never starved.
            min_cluster_intensity = float(cfg.get("viral_v2", {}).get("min_event_cluster_intensity", 6.0))
            strong_clusters = [
                c for c in cluster_cands
                if any(
                    e.get("intensity_1_10", 0) >= min_cluster_intensity
                    for e in _events_in_span(events, c["start"], c["end"])
                )
            ]
            snapped += strong_clusters
        snapped = fuse_event_scores(snapped, events, cfg)
        snapped = apply_reaction_boundaries(snapped, events, cfg, duration)

    final = _dedupe(snapped, max_n)
    result = {"candidates": final}
    validate(result, "highlight_candidates")
    log.info("selected %d candidates (mechanical=%s)", len(final), mechanical)
    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        (Path(debug_dir) / "candidates.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
    return final


def _llm_or_fallback(windows, cfg, provider, min_s, max_s, max_n, debug_dir):
    listing = "\n".join(
        f"[{i}] {w['start']:.1f}s–{w['end']:.1f}s: {w['text'][:400]}"
        for i, w in enumerate(windows))
    from schemas import SCHEMAS as _S
    prompt = PROMPT_TEMPLATE.format(n=max_n, min_s=min_s, max_s=max_s,
                                    schema=json.dumps(_S["highlight_candidates"]),
                                    windows=listing)
    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        (Path(debug_dir) / "highlight_prompt.txt").write_text(
            prompt, encoding="utf-8")
    try:
        out = llm.complete_json("highlight_candidates", "highlight_candidates",
                                prompt, provider=provider,
                                context={"windows": windows}, cfg=cfg)
        cands = out["candidates"]
        if not cands:
            raise LLMError("LLM returned zero candidates")
        for c in cands:
            c.setdefault("candidate_source", "llm_transcript")
            raw_hook = str(c.get("hook", "")).strip()
            if raw_hook.lower().startswith("mock:"):
                c["hook"] = raw_hook[5:].strip() or "A notable moment"
                c["is_synthetic"] = True
            else:
                c.setdefault("is_synthetic", False)
        if debug_dir:
            (Path(debug_dir) / "highlight_llm_raw.json").write_text(
                json.dumps(out, indent=2), encoding="utf-8")
        return cands
    except LLMError as e:
        log.warning("LLM highlight scoring failed (%s) — rule-based fallback", e)
        return rule_based_candidates(windows, cfg)


# ------------------------------------------------------------- re-scoring & ranking engine

def _get_clip_text(clip: dict, transcript: dict) -> str:
    """Resolve spoken text for a clip using the most accurate available source."""
    # 1. Direct text on clip record
    if clip.get("text") and str(clip["text"]).strip():
        return str(clip["text"]).strip()

    # 2. Words list on clip record
    if clip.get("words"):
        words_str = " ".join(w["word"] for w in clip["words"] if isinstance(w, dict) and "word" in w)
        if words_str.strip():
            return words_str.strip()

    # 3. Read words from prepared_ai.json cache if clip directory exists
    clip_path = clip.get("path")
    if clip_path:
        clip_dir = Path(clip_path).parent
        prep_file = clip_dir / "prepared_ai.json"
        if prep_file.exists():
            try:
                p_data = json.loads(prep_file.read_text(encoding="utf-8"))
                p_words = p_data.get("words", [])
                words_str = " ".join(w["word"] for w in p_words if isinstance(w, dict) and "word" in w)
                if words_str.strip():
                    return words_str.strip()
            except Exception:
                pass

    # 4. Extract from burned subtitles (.srt)
    srt_path = clip.get("srt")
    if srt_path and Path(srt_path).exists():
        try:
            srt_lines = [
                line.strip() for line in Path(srt_path).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().isdigit() and "-->" not in line
            ]
            if srt_lines:
                return " ".join(srt_lines).strip()
        except Exception:
            pass

    # 5. Fallback: slice from master transcript
    sliced = _slice_text(transcript, clip.get("start", 0.0), clip.get("end", 0.0))
    if sliced.strip():
        return sliced.strip()

    # 6. Final fallback: hook or metadata title/description
    meta = clip.get("metadata") or {}
    return str(meta.get("description") or clip.get("hook") or "").strip()


def _safe_num(val: Any, default: float = 5.0, min_v: float = 0.0, max_v: float = 10.0) -> float:
    try:
        if val is None:
            return default
        v = float(val)
        if math.isnan(v):
            return default
        if math.isinf(v):
            return max_v if v > 0 else min_v
        return min(max_v, max(min_v, v))
    except (TypeError, ValueError):
        return default


def normalize_virality_score(val: Any, scale: str | None = None) -> float:
    """Normalize virality score strictly onto canonical 0.0 - 10.0 scale.
    - If val is dict (e.g. {"score": 85.0}), extract score.
    - If val is 0 - 100 (standard virality.py schema): divide by 10.0.
    - If val is already 0.0 - 10.0: keep it unchanged.
    - Never double-normalizes (e.g. 8.5 stays 8.5).
    """
    if val is None:
        return 5.0
    if isinstance(val, dict):
        val = val.get("score", 50.0)
    safe_v = _safe_num(val, default=50.0, min_v=0.0, max_v=100.0)
    if scale == "0_10":
        return round(min(10.0, max(0.0, safe_v)), 3)
    if scale == "0_100" or safe_v > 10.0:
        return round(min(10.0, max(0.0, safe_v / 10.0)), 3)
    return round(min(10.0, max(0.0, safe_v)), 3)


def compute_composite_score(
    llm_score: float,
    virality_score: float,
    candidate_score: float,
    weights: dict | None = None
) -> float:
    """Authoritative composite score calculation.
    Formula: (w_llm * S_llm + w_vir * S_vir + w_cand * S_cand) / (w_llm + w_vir + w_cand)
    All components on canonical 0.0 - 10.0 scale.
    Defaults: 60% LLM, 25% virality, 15% candidate.
    """
    weights = weights or {}
    w_llm = float(weights.get("llm", 0.60))
    w_vir = float(weights.get("virality", 0.25))
    w_cand = float(weights.get("candidate", 0.15))
    w_sum = max(1e-6, w_llm + w_vir + w_cand)

    s_llm = _safe_num(llm_score, default=5.0, min_v=0.0, max_v=10.0)
    s_vir = _safe_num(virality_score, default=5.0, min_v=0.0, max_v=10.0)
    s_cand = _safe_num(candidate_score, default=5.0, min_v=0.0, max_v=10.0)

    composite = (w_llm * s_llm + w_vir * s_vir + w_cand * s_cand) / w_sum
    return round(min(10.0, max(0.0, composite)), 3)


def rescore_clips(clips: list[dict], transcript: dict,
                  cfg: dict | None = None, provider: str | None = None,
                  target_count: int | None = None) -> list[dict]:
    """Audit, validate eligibility, score, and rank each rendered clip.
    Enforces minimum duration, playable output, deterministic tie-breaking,
    and structured rejection reasons.
    """
    cfg = cfg or load_config()
    weights = cfg["clips"]["rescore_weights"]
    min_seconds = float(cfg["clips"].get("min_seconds", 10.0))

    # Phase 1: Candidate Eligibility Audit
    for clip in clips:
        start_s = _safe_num(clip.get("start"), default=0.0, min_v=0.0, max_v=86400.0)
        end_s = _safe_num(clip.get("end"), default=0.0, min_v=start_s, max_v=86400.0)
        raw_dur = clip.get("duration")
        if raw_dur is not None:
            dur = _safe_num(raw_dur, default=max(0.0, end_s - start_s), min_v=0.0, max_v=86400.0)
        else:
            dur = max(0.0, end_s - start_s)

        rejection_reasons: list[str] = []
        is_eligible = True

        # Duration gate (hard reject under min_seconds)
        if dur < min_seconds:
            is_eligible = False
            rejection_reasons.append(f"duration_below_minimum: {dur:.2f}s < {min_seconds:.1f}s")

        # Media output integrity gate (if path is specified, it must exist and be valid)
        if "path" in clip:
            clip_path = clip.get("path")
            if not clip_path or not Path(clip_path).exists():
                is_eligible = False
                rejection_reasons.append("rendered_output_file_missing")
            elif Path(clip_path).stat().st_size < 1000:
                is_eligible = False
                rejection_reasons.append("rendered_output_file_empty_or_corrupted")

        clip["eligible"] = is_eligible
        clip["rejection_reasons"] = rejection_reasons
        clip["duration"] = round(dur, 3)

    # Phase 2: AI Scoring for Eligible Candidates
    for clip in clips:
        text = _get_clip_text(clip, transcript)
        clip["resolved_text"] = text

        # Determine transcript status
        if not text.strip():
            transcript_status = "missing"
        elif clip.get("words"):
            transcript_status = "recovered"
        else:
            transcript_status = "complete"
        clip["transcript_status"] = transcript_status

        # Composite normalization: incorporate virality and candidate signals safely
        vir_norm = normalize_virality_score(clip.get("virality"))

        cand_raw = _safe_num(clip.get("candidate_score"), default=5.0, min_v=0.0, max_v=100.0)
        cand_norm = round(cand_raw / 10.0, 3) if cand_raw > 10.0 else round(cand_raw, 3)
        cand_norm = min(10.0, max(0.0, cand_norm))

        clip_id_display = str(clip.get("index", "?"))
        if not clip["eligible"]:
            clip["scores"] = {"hook_strength": 0.0, "retention": 0.0, "clarity": 0.0, "impact": 0.0}
            clip["weighted_score"] = 0.0
            clip["assessment_status"] = "not_assessed"
            clip["normalized_components"] = {"llm": 0.0, "virality": 0.0, "candidate": 0.0}
            log.info("clip %s [INELIGIBLE]: %s", clip_id_display, ", ".join(clip["rejection_reasons"]))
            continue

        if transcript_status == "missing":
            clip["scores"] = {"hook_strength": 1.0, "retention": 1.0, "clarity": 1.0, "impact": 1.0}
            clip["assessment_status"] = "not_assessed"
            llm_score = 1.0
            clip["rejection_reasons"].append("empty_transcript")
        else:
            prompt = (
                "TASK: Rate this short-form clip transcript 1-10 on each axis.\n"
                "AXES: hook_strength (first 3 seconds), retention potential, "
                "clarity out of context, emotional/informational impact.\n"
                "CONSTRAINTS: numbers 1-10, JSON only.\n"
                "OUTPUT SCHEMA: {\"hook_strength\": n, \"retention\": n, "
                "\"clarity\": n, \"impact\": n}\n"
                f"CLIP ({clip['duration']:.0f}s): {text[:1500]}")
            try:
                raw_scores = llm.complete_json("clip_score", "clip_score", prompt,
                                               provider=provider,
                                               context={"text": text}, cfg=cfg)
                clean_scores = {k: _safe_num(raw_scores.get(k), default=5.0, min_v=1.0, max_v=10.0) for k in weights}
                clip["scores"] = clean_scores
                llm_score = sum(clean_scores[k] * weights[k] for k in weights)
                clip["assessment_status"] = "completed"
            except LLMError as e:
                log.warning("rescore failed for clip %s (%s) — transparent fallback",
                            clip_id_display, e)
                fb_val = min(7.0, max(2.0, round(0.6 * cand_norm + 0.4 * vir_norm, 2)))
                clip["scores"] = {k: fb_val for k in ("hook_strength", "retention", "clarity", "impact")}
                llm_score = fb_val
                clip["assessment_status"] = "fallback"

        llm_norm = round(llm_score, 3)

        # Configurable composite scoring weights (defaults: 60% LLM, 25% virality, 15% candidate)
        comp_weights = cfg.get("clips", {}).get("composite_weights", {})
        composite_score = compute_composite_score(llm_norm, vir_norm, cand_norm, comp_weights)

        clip["weighted_score"] = composite_score
        clip["normalized_components"] = {
            "llm": llm_norm,
            "virality": vir_norm,
            "candidate": cand_norm
        }

    # Phase 3: Deterministic Multi-Tier Ranking & Selection
    eligible_clips = [c for c in clips if c["eligible"]]
    ineligible_clips = [c for c in clips if not c["eligible"]]

    def _ranking_key(c: dict):
        norm = c.get("normalized_components", {})
        vir_s = _safe_num(norm.get("virality"), default=0.0, min_v=0.0, max_v=10.0)
        cand_s = _safe_num(norm.get("candidate"), default=0.0, min_v=0.0, max_v=10.0)
        dur_s = _safe_num(c.get("duration"), default=30.0, min_v=0.0, max_v=7200.0)
        idx_s = _safe_num(c.get("index"), default=0.0, min_v=-1e9, max_v=1e9)
        return (
            _safe_num(c.get("weighted_score"), default=0.0, min_v=0.0, max_v=10.0),
            vir_s,
            cand_s,
            -abs(dur_s - 35.0),  # Sweet-spot proximity
            -int(idx_s)          # Stable tie-break
        )

    eligible_clips.sort(key=_ranking_key, reverse=True)

    n_eligible = len(eligible_clips)
    if target_count:
        keep_target = min(int(target_count), n_eligible)
    else:
        keep_target = max(min(cfg["clips"]["min_keep"], n_eligible),
                          math.ceil(n_eligible * cfg["clips"]["keep_ratio"]))

    comp_weights = cfg.get("clips", {}).get("composite_weights", {})
    w_llm = float(comp_weights.get("llm", 0.60))
    w_vir = float(comp_weights.get("virality", 0.25))
    w_cand = float(comp_weights.get("candidate", 0.15))
    w_sum = max(1e-6, w_llm + w_vir + w_cand)

    for rank_idx, c in enumerate(eligible_clips):
        c["rank"] = rank_idx + 1
        c["kept"] = rank_idx < keep_target
        if not c["kept"]:
            c["rejection_reasons"].append(f"dropped_by_quota: rank {c['rank']} exceeds target {keep_target}")

        verdict = "selected" if c["kept"] else f"dropped_by_quota: rank {c['rank']} exceeds target {keep_target}"
        dur_val = float(c.get("duration") or 0.0)
        duration_suitability = round(max(0.0, min(10.0, 10.0 - abs(dur_val - 35.0) * 0.3)), 2)
        c_id = c.get("index") if c.get("index") is not None else (rank_idx + 1)
        llm_val = c.get("normalized_components", {}).get("llm", 0.0)
        c["ranking"] = {
            "candidate_id": c_id,
            "original_start": c.get("original_source_start_s", c.get("start")),
            "original_end": c.get("original_source_end_s", c.get("end")),
            "start": c.get("start"),
            "end": c.get("end"),
            "duration": c.get("duration"),
            "eligible": True,
            "rejection_reasons": c["rejection_reasons"],
            "transcript_status": c.get("transcript_status", "complete"),
            "assessment_status": c.get("assessment_status", "completed"),
            "breakdown": c["scores"],
            "normalized_components": c.get("normalized_components", {}),
            "candidate_score": c.get("normalized_components", {}).get("candidate", 0.0),
            "virality_score": c.get("normalized_components", {}).get("virality", 0.0),
            "llm_assessment_score": llm_val,
            "content_quality_score": llm_val,
            "clarity_score": c.get("scores", {}).get("clarity", 0.0),
            "hook_quality": c.get("scores", {}).get("hook_strength", 0.0),
            "retention_score": c.get("scores", {}).get("retention", 0.0),
            "impact_score": c.get("scores", {}).get("impact", 0.0),
            "duration_suitability": duration_suitability,
            "composite_score": c["weighted_score"],
            "final_weighted_score": c["weighted_score"],
            "weights_used": {
                "llm": round(w_llm / w_sum, 4),
                "virality": round(w_vir / w_sum, 4),
                "candidate": round(w_cand / w_sum, 4),
                "llm_subscores": weights,
            },
            "candidate_source": c.get("candidate_source", "heuristic"),
            "transcript_source": c.get("transcript_source", "whisper"),
            "assessment_source": c.get("assessment_source", provider or "ollama"),
            "is_synthetic": bool(c.get("is_synthetic", False)),
            "rank": c["rank"],
            "kept": c["kept"],
            "verdict_reason": verdict,
        }
        log.info("clip %s [RANK %d | score=%.2f | kept=%s]: %s",
                 str(c_id), c["rank"], c["weighted_score"], c["kept"], c.get("hook", ""))

    for c in ineligible_clips:
        c["rank"] = None
        c["kept"] = False
        c_id = c.get("index") if c.get("index") is not None else "?"
        c["ranking"] = {
            "candidate_id": c_id,
            "original_start": c.get("original_source_start_s", c.get("start")),
            "original_end": c.get("original_source_end_s", c.get("end")),
            "start": c.get("start"),
            "end": c.get("end"),
            "duration": c.get("duration"),
            "eligible": False,
            "rejection_reasons": c["rejection_reasons"],
            "transcript_status": c.get("transcript_status", "missing"),
            "assessment_status": "not_assessed",
            "breakdown": c.get("scores", {}),
            "normalized_components": {"llm": 0.0, "virality": 0.0, "candidate": 0.0},
            "candidate_score": 0.0,
            "virality_score": 0.0,
            "llm_assessment_score": 0.0,
            "content_quality_score": 0.0,
            "clarity_score": 0.0,
            "hook_quality": 0.0,
            "retention_score": 0.0,
            "impact_score": 0.0,
            "duration_suitability": 0.0,
            "composite_score": 0.0,
            "final_weighted_score": 0.0,
            "weights_used": {
                "llm": round(w_llm / w_sum, 4),
                "virality": round(w_vir / w_sum, 4),
                "candidate": round(w_cand / w_sum, 4),
                "llm_subscores": weights,
            },
            "candidate_source": c.get("candidate_source", "heuristic"),
            "transcript_source": c.get("transcript_source", "whisper"),
            "assessment_source": c.get("assessment_source", provider or "ollama"),
            "is_synthetic": bool(c.get("is_synthetic", False)),
            "rank": None,
            "kept": False,
            "verdict_reason": f"ineligible: {', '.join(c['rejection_reasons'])}",
        }

    log.info("rescore: kept %d of %d eligible clips (%d ineligible rejected)",
             keep_target, n_eligible, len(ineligible_clips))

    return eligible_clips + ineligible_clips


def _slice_text(transcript: dict | None, start: float, end: float) -> str:
    if not transcript or not isinstance(transcript, dict):
        return ""
    words = transcript.get("words")
    if not words or not isinstance(words, list):
        return ""
    s_clean = _safe_num(start, default=0.0, min_v=0.0)
    e_clean = _safe_num(end, default=0.0, min_v=s_clean)
    ws = []
    for w in words:
        if not isinstance(w, dict) or "word" not in w:
            continue
        w_start = _safe_num(w.get("start"), default=-1.0, min_v=-1.0)
        w_end = _safe_num(w.get("end"), default=-1.0, min_v=-1.0)
        if w_start >= s_clean - 0.05 and w_end <= e_clean + 0.05:
            ws.append(str(w["word"]))
    return " ".join(ws)
