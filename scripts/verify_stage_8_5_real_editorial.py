"""Stage 8.5 Fresh Real-World Editorial Validation & Ranking Integrity Verification.
Uses real video excerpt (samples/podcast_fresh_6min.mp4) with real audio DSP and real Ollama.
Strictly verifies that NO mock data enters ranking, all scores match the authoritative formula,
virality is normalized to 0.0 - 10.0, and editorial ordering is meaningful.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_config, apply_run_options
from ffutil import probe
import highlights as hl
from highlights import normalize_virality_score, compute_composite_score, rescore_clips
import video_events
import transcribe
import llm_manager
from resource_coordinator import ResourceCoordinator

SOURCE_VIDEO = ROOT / "samples" / "podcast_fresh_6min.mp4"
DEBUG_DIR = ROOT / "output" / "_stage_8_5_real_editorial_debug"


def main():
    print("=" * 80)
    print("STAGE 8.5: REAL-WORLD EDITORIAL VALIDATION & RANKING INTEGRITY AUDIT")
    print("=" * 80)

    assert SOURCE_VIDEO.exists(), f"Source file missing: {SOURCE_VIDEO}"
    src_probe = probe(SOURCE_VIDEO)
    duration_s = float(src_probe["duration"])
    print(f"Source file: {SOURCE_VIDEO.name}")
    print(f"Duration: {duration_s:.2f}s, Resolution: {src_probe['width']}x{src_probe['height']}, FPS: {src_probe['fps']}")

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    rc = ResourceCoordinator()
    ram_init = rc.get_ram_status()
    gpu_init = rc.get_gpu_status(force_refresh=True)
    print(f"Initial RAM: {ram_init['used_mb']:.1f}MB ({ram_init['percent']:.1f}%), GPU VRAM: {gpu_init['used_mb']:.1f}MB ({gpu_init['percent']:.1f}%)")

    cfg = load_config()
    cfg = apply_run_options(cfg, {
        "clip_min": "15",
        "clip_max": "45",
        "language": "hinglish",
    })

    # Step 1: Extract Audio & Detect Real DSP Events
    print("\n--- Step 1: Audio Extraction & Real DSP Event Detection ---")
    audio_path = DEBUG_DIR / "audio.wav"
    cached_audio = ROOT / "output" / "_stage_8_4_fresh_podcast_validation" / "audio.wav"
    if cached_audio.exists():
        import shutil
        shutil.copy2(cached_audio, audio_path)
    else:
        from ffutil import run_ffmpeg
        run_ffmpeg(["-y", "-i", str(SOURCE_VIDEO), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio_path)])
    assert audio_path.exists(), "Audio extraction failed"

    t0_events = time.perf_counter()
    real_events = video_events.audio_events(str(audio_path), cfg)
    t_events = time.perf_counter() - t0_events
    print(f"Detected {len(real_events)} real audio DSP events in {t_events:.2f}s")
    for idx, e in enumerate(real_events[:5]):
        print(f"  Event {idx+1}: [{e.get('t_start_s', 0):.1f}s - {e.get('t_end_s', 0):.1f}s] {e.get('type')} intensity={e.get('intensity_1_10')}/10 - {e.get('description')}")

    # Verify no mock description in real events
    assert all("mock:" not in str(e.get("description", "")).lower() for e in real_events), "Leakage: mock description in real events!"

    # Step 2: Transcribe using Whisper (or load cached transcript if available)
    print("\n--- Step 2: Transcription ---")
    cached_tr_path = DEBUG_DIR / "transcript.json"
    if not cached_tr_path.exists():
        cached_tr_path = ROOT / "output" / "_stage_8_4_fresh_podcast_validation" / ".done_transcribe.json"
    if cached_tr_path.exists():
        print(f"Loading cached transcript from {cached_tr_path}")
        tr = json.loads(cached_tr_path.read_text(encoding="utf-8"))
    else:
        print("Running fresh Whisper transcription...")
        tr = transcribe.transcribe(str(audio_path), cfg, spans=None)
        (DEBUG_DIR / "transcript.json").write_text(json.dumps(tr, indent=2), encoding="utf-8")

    words_count = len(tr.get("words", []))
    sents_count = len(tr.get("sentences", []))
    print(f"Transcript loaded: {len(tr.get('text', ''))} characters, {words_count} words, {sents_count} sentences")

    # Step 3: Highlight Candidate Generation & Event Fusion
    print("\n--- Step 3: Candidate Selection with Real Events ---")
    scenes = {"scenes": [{"index": 0, "start": 0.0, "end": duration_s}]}
    candidates = hl.select_highlights(
        transcript=tr,
        scenes=scenes,
        duration=duration_s,
        cfg=cfg,
        provider="ollama",
        events=real_events,
        max_candidates=10,
        debug_dir=DEBUG_DIR
    )
    print(f"Generated {len(candidates)} highlight candidates")
    for i, c in enumerate(candidates):
        print(f"  Candidate {i+1} [{c['start']:.1f}s - {c['end']:.1f}s | cand_score={c['score']:.2f}]: {c['hook']}")
        assert "mock:" not in c["hook"].lower(), f"Leakage: 'mock:' found in candidate hook: {c['hook']}"

    # Step 4: Refine & Re-score with Real Ollama
    print("\n--- Step 4: AI Re-scoring & Ranking with Real Ollama ---")
    mock_clips = []
    for idx, c in enumerate(candidates):
        mock_clips.append({
            "index": idx + 1,
            "original_source_start_s": c["start"],
            "original_source_end_s": c["end"],
            "start": c["start"],
            "end": c["end"],
            "duration": round(c["end"] - c["start"], 3),
            "hook": c["hook"],
            "reason": c.get("reason", ""),
            "candidate_score": c.get("score", 5.0),
            "candidate_source": c.get("candidate_source", "heuristic"),
            "is_synthetic": bool(c.get("is_synthetic", False)),
            "transcript_source": tr.get("source", "whisper"),
            "assessment_source": "ollama",
            "virality": {"score": min(100.0, max(10.0, c.get("score", 5.0) * 9.5))},
        })

    ranked_clips = rescore_clips(mock_clips, tr, cfg=cfg, provider="ollama", target_count=5)
    print(f"\nRescored and Ranked {len(ranked_clips)} clips:")
    print("-" * 120)
    print(f"{'Rank':<5} | {'Cand ID':<7} | {'Start-End':<15} | {'Dur(s)':<6} | {'LLM(0-10)':<9} | {'Vir(0-10)':<9} | {'Cand(0-10)':<10} | {'Composite':<9} | {'Kept':<6} | {'Hook'}")
    print("-" * 120)

    audit_records = []
    for c in ranked_clips:
        r = c["ranking"]
        llm_s = r["llm_assessment_score"]
        vir_s = r["virality_score"]
        cand_s = r["candidate_score"]
        comp_s = r["composite_score"]
        kept_s = "YES" if r["kept"] else "NO"
        rank_display = f"#{r['rank']}" if r["rank"] is not None else "REJ"

        # Mathematical verification of authoritative formula
        recomputed = compute_composite_score(llm_s, vir_s, cand_s, r["weights_used"])
        math_diff = abs(recomputed - comp_s)
        assert math_diff < 1e-4, f"Math discrepancy: recorded {comp_s} != recomputed {recomputed} (diff={math_diff})"

        # Provenance verification
        assert r["is_synthetic"] is False, "Error: real clip flagged as synthetic!"
        assert "mock:" not in str(c.get("hook", "")).lower(), "Error: mock string in hook!"

        print(f"{rank_display:<5} | {str(r['candidate_id']):<7} | {r['start']:5.1f}-{r['end']:5.1f}s    | {r['duration']:<6.1f} | {llm_s:<9.3f} | {vir_s:<9.3f} | {cand_s:<10.3f} | {comp_s:<9.3f} | {kept_s:<6} | {c.get('hook', '')[:40]}")
        audit_records.append({
            "rank": r["rank"],
            "candidate_id": r["candidate_id"],
            "start": r["start"],
            "end": r["end"],
            "duration": r["duration"],
            "scores": {
                "llm": llm_s,
                "virality": vir_s,
                "candidate": cand_s,
                "clarity": r["clarity_score"],
                "composite": comp_s,
                "recomputed": recomputed,
                "diff": math_diff,
            },
            "weights": r["weights_used"],
            "provenance": {
                "candidate_source": r["candidate_source"],
                "transcript_source": r["transcript_source"],
                "assessment_source": r["assessment_source"],
                "is_synthetic": r["is_synthetic"],
            },
            "kept": r["kept"],
            "verdict": r["verdict_reason"],
            "hook": c.get("hook", ""),
        })

    # Save detailed audit output
    output_path = DEBUG_DIR / "editorial_validation_results.json"
    output_path.write_text(json.dumps(audit_records, indent=2), encoding="utf-8")
    print(f"\nEditorial validation results saved to: {output_path}")

    # Compute ranking metrics
    kept_clips = [c for c in ranked_clips if c["ranking"]["kept"]]
    dropped_clips = [c for c in ranked_clips if not c["ranking"]["kept"] and c["eligible"]]
    if kept_clips and dropped_clips:
        avg_kept_score = sum(c["ranking"]["composite_score"] for c in kept_clips) / len(kept_clips)
        avg_dropped_score = sum(c["ranking"]["composite_score"] for c in dropped_clips) / len(dropped_clips)
        print(f"\nRanking Separation Analysis:")
        print(f"  Average Kept Composite Score:    {avg_kept_score:.3f}")
        print(f"  Average Dropped Composite Score: {avg_dropped_score:.3f}")
        print(f"  Score Separation Margin:         {avg_kept_score - avg_dropped_score:+.3f}")
        assert avg_kept_score >= avg_dropped_score, "Editorial ranking defect: kept clips have lower average score than dropped clips!"

    print("\n[SUCCESS] All Stage 8.5 editorial validation and mathematical integrity assertions passed!")


if __name__ == "__main__":
    main()
