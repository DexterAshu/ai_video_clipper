"""Settings (persisted to config.local.yaml only), hardware summary,
self-updater, and the batch queue."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import load_config, save_config
from logutil import get_logger
from server.copy import friendly

log = get_logger("server")
router = APIRouter()


@router.get("/api/settings")
def get_settings():
    cfg = load_config()
    llm = cfg.get("llm", {})
    return {
        "compute": cfg.get("render", {}).get("compute", "auto"),
        "whisper_model": cfg.get("whisper", {}).get("model_override", ""),
        "provider": llm.get("provider", "mock"),
        "gemini_model": llm.get("gemini_model", ""),
        "groq_model": llm.get("groq_model", ""),
        "ollama_model": llm.get("ollama_model", ""),
        "auto_open": cfg.get("ui", {}).get("auto_open", True),
        "custom_niches": ", ".join(
            cfg.get("classify", {}).get("custom_niches", []) or []),
        "require_approval": bool(
            cfg.get("upload", {}).get("require_approval", False)),
    }


class SettingsRequest(BaseModel):
    compute: str = "auto"
    whisper_model: str = ""
    provider: str = "mock"
    gemini_model: str = ""
    groq_model: str = ""
    ollama_model: str = ""
    custom_niches: str = ""
    require_approval: bool = True


@router.put("/api/settings")
def put_settings(req: SettingsRequest):
    if req.compute not in ("auto", "gpu", "cpu"):
        raise HTTPException(422, "Speed setting must be auto, gpu or cpu.")
    try:
        save_config({
            "render": {"compute": req.compute},
            "whisper": {"model_override": req.whisper_model or ""},
            "llm": {"provider": req.provider,
                    "gemini_model": req.gemini_model.strip(),
                    "groq_model": req.groq_model.strip(),
                    "ollama_model": req.ollama_model.strip()},
            "classify": {"custom_niches": sorted(
                {s.strip().lower() for s in req.custom_niches.split(",")
                 if s.strip()})},
            "upload": {"require_approval": bool(req.require_approval)},
        })
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, friendly(e, "Saving your settings"))
    return get_settings()


@router.get("/api/models")
def get_models():
    import llm_manager
    cfg = load_config()
    base_url = llm_manager.get_ollama_base_url(cfg)
    available = llm_manager.list_available_models(base_url)
    active = llm_manager.get_active_model(cfg)
    fallback = llm_manager.get_fallback_model(cfg)
    running = llm_manager.get_running_models(base_url)
    return {
        "active_model": active,
        "fallback_model": fallback,
        "catalog": llm_manager.MODEL_CATALOG,
        "installed_models": available,
        "running_models": running,
    }


@router.post("/api/models/refresh")
def refresh_models():
    """Fetch live installed models from Ollama /api/tags without server restart."""
    import llm_manager
    cfg = load_config()
    base_url = llm_manager.get_ollama_base_url(cfg)
    models = llm_manager.list_available_models(base_url)
    active = llm_manager.get_active_model(cfg)
    is_active_installed = any(m["name"] == active for m in models)
    return {
        "ok": True,
        "models": models,
        "count": len(models),
        "active_model": active,
        "is_active_installed": is_active_installed
    }


class SwitchModelRequest(BaseModel):
    model: str


@router.post("/api/models/active")
def switch_active_model(req: SwitchModelRequest):
    import llm_manager
    from server import jobs

    if jobs.has_active_runs():
        raise HTTPException(409, "Cannot switch models while a processing job is actively running.")

    model_name = req.model.strip()
    if not model_name:
        raise HTTPException(422, "Model name required")

    available = llm_manager.list_available_models()
    if available and not any(m["name"] == model_name for m in available):
        raise HTTPException(400, f"Model '{model_name}' is not installed in Ollama.")

    # Release previous model before activating new one
    llm_manager.unload_model()

    health = llm_manager.check_model_health(model_name)
    save_config({"llm": {"ollama_model": model_name}})
    return {"ok": True, "active_model": model_name, "health": health}


@router.post("/api/models/unload")
def unload_active_model():
    import llm_manager
    ok = llm_manager.unload_model()
    return {"ok": ok}


@router.get("/api/system")
def system_info():
    import updater
    from ffutil import ffmpeg_bin, nvenc_available
    from transcribe import gpu_available
    try:
        gpu = bool(gpu_available())
        nvenc = bool(nvenc_available())
    except Exception as e:  # noqa: BLE001 — probes must not break the page
        log.warning("hardware probe failed: %s", e)
        gpu = nvenc = False
    speed = ("full" if gpu and nvenc else
             "partial" if gpu or nvenc else "cpu")

    gpu_name = ""
    gpu_vram_gb = 0.0
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_vram_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
    except Exception:
        pass

    ollama_ok = False
    ollama_model = ""
    try:
        import requests
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=(0.2, 0.5))
        if r.status_code == 200:
            ollama_ok = True
            models = [m.get("name") for m in r.json().get("models", [])]
            cfg = load_config()
            configured = cfg.get("llm", {}).get("ollama_model", "llama3.2:3b")
            ollama_model = configured if configured in models else (models[0] if models else "")
    except Exception:
        pass

    return {
        "version": updater.current_version(),
        "cpu_count": os.cpu_count(),
        "gpu_transcribe": gpu,
        "gpu_encode": nvenc,
        "acceleration": speed,
        "gpu_name": gpu_name or ("NVIDIA RTX GPU" if gpu or nvenc else "CPU only"),
        "gpu_vram_gb": gpu_vram_gb,
        "ollama_running": ollama_ok,
        "ollama_model": ollama_model,
        "ffmpeg_bin": str(ffmpeg_bin()),
    }


@router.get("/api/update")
def update_state():
    import updater
    return {"state": updater.get_state(),
            "current": updater.current_version()}


@router.post("/api/update/check")
def update_check():
    import updater
    try:
        return {"state": updater.check_for_update(),
                "current": updater.current_version()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, friendly(e, "Checking for updates"))


@router.post("/api/update/apply")
def update_apply():
    import updater
    try:
        return {"message": updater.apply_update()}
    except Exception as e:  # noqa: BLE001 — updater rolls back; say why
        raise HTTPException(500,
                            f"Update failed (no changes were kept): {e}")


# ------------------------------------------------------------------ batch

class BatchAddRequest(BaseModel):
    sources: list[str]
    music: str = ""


@router.get("/api/batch")
def batch_status():
    from batch import get_queue
    return {"rows": get_queue().status_rows()}


@router.post("/api/batch")
def batch_add(req: BatchAddRequest):
    from batch import get_queue
    from server.routes_run import _resolve_music_choice
    q = get_queue()
    n = 0
    for line in req.sources:
        line = line.strip()
        if not line:
            continue
        resolved = _resolve_music_choice(req.music)
        q.add(line, **({"music": resolved} if resolved else {}))
        n += 1
    return {"queued": n, "rows": q.status_rows()}


@router.get("/api/batch/zip")
def batch_zip():
    """One zip of every finished queue job (mirrors the old UI's
    'Download everything')."""
    from batch import get_queue
    from bundle import zip_jobs
    dirs = [i["job_dir"] for i in get_queue().items
            if i["status"] == "done" and i.get("job_dir")]
    if not dirs:
        raise HTTPException(404, "Nothing finished in the queue yet.")
    try:
        return FileResponse(str(zip_jobs(dirs)), filename="frameza-all.zip")
    except Exception as e:  # noqa: BLE001 — zip failure must be a sentence
        raise HTTPException(500, friendly(e, "Packaging the clips"))


class InboxRequest(BaseModel):
    enabled: bool


@router.put("/api/batch/inbox")
def batch_inbox(req: InboxRequest):
    from batch import get_queue
    q = get_queue()
    msg = (q.start_inbox_watcher() if req.enabled
           else q.stop_inbox_watcher())
    return {"enabled": bool(req.enabled), "message": msg}
