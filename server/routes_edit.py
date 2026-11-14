"""Per-clip editing: re-render with new bounds/style, regenerate title &
description, snap bounds to sentences, and the auto-upload opt-out flag."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from logutil import get_logger
from server import jobs
from server.copy import friendly
from server.routes_library import invalidate_all_clips_cache, safe_job_path

log = get_logger("server")
router = APIRouter()


class RerenderRequest(BaseModel):
    start: float
    end: float
    preset: str | None = None
    style_refine: bool | None = None
    subs_mode: str | None = None
    framing_mode: str | None = None


@router.post("/api/jobs/{job_name}/clips/{index}/rerender")
def rerender(job_name: str, index: int, req: RerenderRequest):
    """Starts a re-render on a worker thread; progress streams over the same
    /ws/runs/{id} channel as full runs. Re-render ids never enter history."""
    from rerender import rerender_clip
    job_dir = safe_job_path(job_name)
    if not (job_dir / "job.json").exists():
        raise HTTPException(404, "That run's files can't be found.")

    # Persist framing_mode to edit_plan.json if specified in rerender request
    if req.framing_mode:
        clip_dir = job_dir / f"clip_{index:02d}"
        if clip_dir.exists():
            plan_path = clip_dir / "edit_plan.json"
            cur_plan = {}
            if plan_path.exists():
                try:
                    cur_plan = json.loads(plan_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            cur_plan["framing_mode"] = req.framing_mode
            plan_path.write_text(json.dumps(cur_plan, indent=2, ensure_ascii=False), encoding="utf-8")

    handle = jobs.create(f"rr_{uuid.uuid4().hex[:8]}")

    def work(h: jobs.RunHandle) -> None:
        try:
            clip = rerender_clip(job_dir, index, float(req.start),
                                 float(req.end), req.preset or None,
                                 style_refine=req.style_refine,
                                 subs_mode=req.subs_mode or None,
                                 tracker=h.tracker)
            h.finish("done", result=clip)
        except Exception as e:  # noqa: BLE001 — worker must record, never raise
            h.finish("error", error=friendly(e, "Re-rendering this clip"))

    jobs.launch(handle, work)
    return {"run_id": handle.id}


@router.post("/api/jobs/{job_name}/clips/{index}/metadata")
def regen_metadata(job_name: str, index: int):
    from rerender import regenerate_metadata
    job_dir = safe_job_path(job_name)
    try:
        return regenerate_metadata(job_dir, index)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, friendly(
            e, "Regenerating the title and description"))


@router.get("/api/jobs/{job_name}/clips/snap")
def snap(job_name: str, start: float, end: float):
    from rerender import snap_bounds
    job_dir = safe_job_path(job_name)
    try:
        s, e = snap_bounds(job_dir, float(start), float(end))
        return {"start": s, "end": e}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, friendly(e, "Snapping to sentences"))


class KeptRequest(BaseModel):
    kept: bool


@router.put("/api/jobs/{job_name}/clips/{index}/kept")
def set_kept(job_name: str, index: int, req: KeptRequest):
    """Persist the keep/discard choice on the clip record in job.json (the
    same flag rescore sets; display-only, nothing re-renders)."""
    job_path = safe_job_path(job_name, "job.json")
    if not job_path.exists():
        raise HTTPException(404, "That run's files can't be found.")
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        clip = next((c for c in job.get("clips", [])
                     if c.get("index") == index), None)
        if clip is None:
            raise HTTPException(404, "That clip can't be found.")
        clip["kept"] = bool(req.kept)
        job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    except HTTPException:
        raise
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(500, friendly(e, "Saving that choice"))
    return {"kept": bool(req.kept)}


def _srt_text(srt_path) -> str:
    """Spoken text from an .srt: drop cue numbers, timestamps and blanks."""
    try:
        lines = srt_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return " ".join(ln.strip() for ln in lines
                    if ln.strip() and "-->" not in ln
                    and not ln.strip().isdigit())


@router.post("/api/classify/backfill")
def classify_backfill():
    """Tag older clips that predate niche classification. Keyword-only (no
    LLM, no keys, no quota) — walks every clip missing a niche, classifies
    from its metadata + subtitle text, and patches metadata.json plus the
    matching clip record in job.json. Never overwrites an existing niche."""
    import classify
    from server import routes_library  # module attr so tests can monkeypatch output_root
    classified = skipped = 0
    for meta_path in routes_library.output_root().glob("*/clip_*/metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("niche"):
                skipped += 1
                continue
            text = _srt_text(meta_path.parent / "final.srt")
            text += " " + meta.get("description", "")
            meta["niche"] = classify.rule_based_niche(
                text, meta.get("title", ""), meta.get("hashtags", []))
            meta_path.write_text(json.dumps(meta, indent=2,
                                            ensure_ascii=False),
                                 encoding="utf-8")
            classified += 1
        except (OSError, json.JSONDecodeError) as e:
            log.warning("backfill skip %s: %s", meta_path, e)
            continue
        # Mirror the niche onto job.json's clip record (library list reads
        # job.json only); best-effort — metadata.json stays the authority.
        try:
            job_path = meta_path.parent.parent / "job.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            idx = int(meta_path.parent.name.split("_")[-1])
            for c in job.get("clips", []):
                if c.get("index") == idx and not c.get("niche"):
                    c["niche"] = meta["niche"]
            job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            log.warning("backfill job.json patch failed for %s: %s",
                        meta_path.parent, e)
    return {"classified": classified, "skipped": skipped}


class ExcludeRequest(BaseModel):
    exclude: bool


@router.put("/api/jobs/{job_name}/clips/{index}/exclude")
def set_exclude(job_name: str, index: int, req: ExcludeRequest):
    """Persist the per-clip auto-upload opt-out where the upload scheduler's
    find_candidates reads it (clip_NN/metadata.json → upload.exclude)."""
    meta_path = safe_job_path(job_name, f"clip_{index:02d}", "metadata.json")
    if not meta_path.exists():
        raise HTTPException(404, "That clip can't be found.")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.setdefault("upload", {})["exclude"] = bool(req.exclude)
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(500, friendly(e, "Saving that choice"))
    invalidate_all_clips_cache()
    return {"exclude": bool(req.exclude)}


class ApprovalRequest(BaseModel):
    approval: str


@router.put("/api/jobs/{job_name}/clips/{index}/approval")
def set_approval(job_name: str, index: int, req: ApprovalRequest):
    """Persist the owner's approve/reject decision where the upload
    scheduler's approval gate reads it (clip_NN/metadata.json →
    upload.approval)."""
    if req.approval not in ("approved", "rejected", "pending"):
        raise HTTPException(422, "Approval must be approved, rejected or "
                                 "pending.")
    meta_path = safe_job_path(job_name, f"clip_{index:02d}", "metadata.json")
    if not meta_path.exists():
        raise HTTPException(404, "That clip can't be found.")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.setdefault("upload", {})["approval"] = req.approval
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(500, friendly(e, "Saving that decision"))
    invalidate_all_clips_cache()
    return {"approval": req.approval}


# --- Advanced Clip Editing Studio Endpoints (Stage 7) -----------------------

class EditorSaveRequest(BaseModel):
    start: float
    end: float
    title: str = ""
    description: str = ""
    hashtags: list[str] = []
    hook: str = ""
    caption_text: str | None = None
    caption_preset: str | None = None
    caption_lang: str | None = None
    caption_anchor: float | None = None
    crop_x_offset: float = 0.0
    crop_y_offset: float = 0.0
    zoom: float = 1.0
    framing_mode: str = "smart_director"
    music_enabled: bool = False
    music_track: str | None = None
    music_volume_db: float = -18.0
    speech_enhance: bool = True


@router.get("/api/jobs/{job_name}/clips/{index}/editor")
def get_clip_editor(job_name: str, index: int):
    job_dir = safe_job_path(job_name)
    job_path = job_dir / "job.json"
    if not job_path.exists():
        raise HTTPException(404, "That run's files can't be found.")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    clip = next((c for c in job.get("clips", []) if c.get("index") == index), None)
    if not clip:
        raise HTTPException(404, "That clip can't be found.")

    clip_dir = job_dir / f"clip_{index:02d}"
    meta_path = clip_dir / "metadata.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Source duration from ingest marker or video probe
    ingest_marker = job_dir / "ingest.marker.json"
    src_dur = 0.0
    if ingest_marker.exists():
        try:
            idata = json.loads(ingest_marker.read_text(encoding="utf-8"))
            src_dur = float(idata.get("duration", 0.0))
        except Exception:
            pass

    # Subtitle / words from srt
    words_text = ""
    srt_path = clip_dir / "final.srt"
    if srt_path.exists():
        words_text = _srt_text(srt_path)

    # Saved edit plan if exists
    plan_path = clip_dir / "edit_plan.json"
    saved_plan = {}
    if plan_path.exists():
        try:
            saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "job_name": job_name,
        "clip_index": index,
        "source_name": clip.get("source_name") or job.get("source", ""),
        "source_duration": src_dur,
        "start": saved_plan.get("start", clip.get("start", 0.0)),
        "end": saved_plan.get("end", clip.get("end", 0.0)),
        "duration": saved_plan.get("duration", clip.get("duration", 0.0)),
        "title": saved_plan.get("title", meta.get("title", clip.get("title", f"Clip {index + 1}"))),
        "description": saved_plan.get("description", meta.get("description", "")),
        "hashtags": saved_plan.get("hashtags", meta.get("hashtags", [])),
        "hook": saved_plan.get("hook", clip.get("hook", "")),
        "caption_text": saved_plan.get("caption_text", words_text),
        "caption_preset": saved_plan.get("caption_preset", clip.get("preset", "modern-bold")),
        "caption_lang": saved_plan.get("caption_lang", clip.get("lang", "hinglish")),
        "caption_anchor": saved_plan.get("caption_anchor", 0.60),
        "crop_x_offset": saved_plan.get("crop_x_offset", 0.0),
        "crop_y_offset": saved_plan.get("crop_y_offset", 0.0),
        "zoom": saved_plan.get("zoom", 1.0),
        "framing_mode": saved_plan.get("framing_mode", "smart_director"),
        "music_enabled": saved_plan.get("music_enabled", False),
        "music_track": saved_plan.get("music_track", ""),
        "music_volume_db": saved_plan.get("music_volume_db", -18.0),
        "speech_enhance": saved_plan.get("speech_enhance", True),
        "original": {
            "start": clip.get("start", 0.0),
            "end": clip.get("end", 0.0),
            "title": clip.get("title", f"Clip {index + 1}"),
            "description": meta.get("description", ""),
            "hashtags": meta.get("hashtags", []),
            "preset": clip.get("preset", "modern-bold"),
            "aspect": clip.get("aspect", "9:16"),
        }
    }


@router.post("/api/jobs/{job_name}/clips/{index}/editor/save")
def save_clip_editor(job_name: str, index: int, req: EditorSaveRequest):
    job_dir = safe_job_path(job_name)
    clip_dir = job_dir / f"clip_{index:02d}"
    if not clip_dir.exists():
        raise HTTPException(404, "That clip can't be found.")

    plan_path = clip_dir / "edit_plan.json"
    plan_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    plan_path.write_text(json.dumps(plan_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update metadata.json title/description/hashtags
    meta_path = clip_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if req.title:
                meta["title"] = req.title
            if req.description:
                meta["description"] = req.description
            if req.hashtags:
                meta["hashtags"] = req.hashtags
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    return {"ok": True, "saved": plan_data}


@router.post("/api/jobs/{job_name}/clips/{index}/editor/export")
def export_clip_editor(job_name: str, index: int, req: EditorSaveRequest):
    """Export the edited clip with updated bounds, crop, captions, and audio settings."""
    from rerender import rerender_clip
    job_dir = safe_job_path(job_name)
    if not (job_dir / "job.json").exists():
        raise HTTPException(404, "That run's files can't be found.")

    save_clip_editor(job_name, index, req)

    handle = jobs.create(f"export_{uuid.uuid4().hex[:8]}")

    def work(h: jobs.RunHandle) -> None:
        try:
            clip = rerender_clip(
                job_dir,
                index,
                float(req.start),
                float(req.end),
                preset=req.caption_preset or None,
                tracker=h.tracker
            )
            h.finish("done", result=clip)
        except Exception as e:
            h.finish("error", error=friendly(e, "Exporting your edited clip"))

    jobs.launch(handle, work)
    return {"run_id": handle.id}


@router.post("/api/jobs/{job_name}/clips/{index}/editor/reset")
def reset_clip_editor(job_name: str, index: int):
    job_dir = safe_job_path(job_name)
    clip_dir = job_dir / f"clip_{index:02d}"
    plan_path = clip_dir / "edit_plan.json"
    plan_path.unlink(missing_ok=True)
    return {"ok": True, "reset": True}
