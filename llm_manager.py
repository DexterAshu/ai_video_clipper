"""Centralized LLM and Model Management for Frameza.

Handles:
- Active model selection (qwen3.5:4b default, llama3.2:3b fallback, qwen3.5:9b advanced)
- Model availability detection & health checks
- Intelligent VRAM management (keep_alive: 0 to unload before Whisper & NVENC)
- Fast context-length bounding (num_ctx: 8192) to keep Qwen within 3.7GB VRAM
- Multimodal frame vision support via base64 images
- Seamless fallback to llama3.2:3b if primary model is unavailable or errors
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

from config import load_config
from errors import LLMError
from logutil import get_logger

log = get_logger("llm_manager")

DEFAULT_PRIMARY_MODEL = "qwen3.5:4b"
DEFAULT_FALLBACK_MODEL = "llama3.2:3b"
ADVANCED_COMPARISON_MODEL = "qwen3.5:9b"

MODEL_CATALOG = {
    "qwen3.5:4b": {
        "display_name": "Qwen 3.5 4B (Recommended)",
        "parameter_size": "4.2B",
        "description": "High-accuracy reasoning, rich Hinglish/Hindi understanding, multimodal vision, 100% GPU.",
        "recommended": True,
        "default_ctx": 8192,
        "vision": True,
        "tier": "primary"
    },
    "llama3.2:3b": {
        "display_name": "Llama 3.2 3B (Fast Fallback)",
        "parameter_size": "3.2B",
        "description": "Ultra-fast low-latency local inference, lightweight VRAM footprint.",
        "recommended": False,
        "default_ctx": 8192,
        "vision": False,
        "tier": "fallback"
    },
    "qwen3.5:9b": {
        "display_name": "Qwen 3.5 9B (Advanced)",
        "parameter_size": "9.0B",
        "description": "Maximum reasoning depth for complex editorial and multi-character dialogue.",
        "recommended": False,
        "default_ctx": 8192,
        "vision": True,
        "tier": "advanced"
    }
}


def get_ollama_base_url(cfg: dict | None = None) -> str:
    """Resolve Ollama endpoint from environment or config."""
    cfg = cfg or load_config()
    return os.environ.get(
        "OLLAMA_BASE_URL",
        cfg.get("llm", {}).get("ollama_base_url", "http://127.0.0.1:11434")
    ).rstrip("/")


def get_active_model(cfg: dict | None = None) -> str:
    """Get the currently configured active Ollama model name."""
    cfg = cfg or load_config()
    return cfg.get("llm", {}).get("ollama_model", DEFAULT_PRIMARY_MODEL)


def get_fallback_model(cfg: dict | None = None) -> str:
    """Get the designated fallback Ollama model name."""
    cfg = cfg or load_config()
    return cfg.get("llm", {}).get("ollama_fallback_model", DEFAULT_FALLBACK_MODEL)


def classify_model_for_hardware(name: str, size_gb: float, details: dict | None = None,
                                capabilities: list | None = None) -> dict[str, Any]:
    """Classify model suitability for Lenovo LOQ RTX 5050 Laptop GPU (8GB VRAM) and 16GB RAM."""
    details = details or {}
    capabilities = capabilities or []
    param_str = str(details.get("parameter_size", "") or "")

    # Check catalog metadata override first
    meta = MODEL_CATALOG.get(name, {})
    if meta.get("parameter_size"):
        param_str = meta["parameter_size"]

    # Extract numerical parameter count (in billions)
    param_val = 0.0
    if param_str:
        match = re.search(r"(\d+(\.\d+)?)", param_str)
        if match:
            try:
                param_val = float(match.group(1))
            except ValueError:
                param_val = 0.0
    if param_val == 0.0 and size_gb > 0:
        param_val = round(size_gb * 1.4, 1)

    # Vision capability detection
    vision = bool(
        meta.get("vision", False)
        or "vision" in capabilities
        or "vision" in name.lower()
        or "llava" in name.lower()
        or "qwen3.5" in name.lower()
    )

    # Classification rules for 8GB VRAM RTX 5050
    badges: list[str] = []
    if name.startswith("qwen3.5:4b") or name == "qwen3.5:4b":
        category = "BEST RECOMMENDED"
        badges.append("BEST RECOMMENDED")
        desc = "Recommended for your RTX 5050 8GB GPU (100% GPU offload, ~3.7GB VRAM)."
        recommended = True
    elif param_val <= 4.5 or size_gb <= 3.5:
        category = "LIGHTWEIGHT / FAST"
        badges.append("LIGHTWEIGHT / FAST")
        desc = "Fast model with lower memory requirements (~2-3GB VRAM)."
        recommended = False
    elif param_val <= 8.5 or size_gb <= 5.5:
        category = "BALANCED"
        badges.append("BALANCED")
        desc = "Moderate resource requirements (~5GB VRAM); good for general AI tasks."
        recommended = False
    elif param_val <= 14.0 or size_gb <= 10.0:
        category = "HEAVY"
        badges.append("HEAVY")
        desc = "High memory usage (~7-8GB VRAM); may use partial CPU offloading."
        recommended = False
    else:
        category = "VERY HEAVY / NOT RECOMMENDED FOR THIS HARDWARE"
        badges.append("VERY HEAVY")
        badges.append("NOT RECOMMENDED")
        desc = "Exceeds 8GB VRAM; requires heavy CPU offloading, slow on laptop GPU."
        recommended = False

    if vision:
        badges.append("VISION CAPABLE")

    return {
        "category": category,
        "badges": badges,
        "description": desc,
        "vision": vision,
        "is_recommended": recommended,
        "parameter_size": param_str or f"{param_val:.1f}B"
    }


def list_available_models(base_url: str | None = None) -> list[dict[str, Any]]:
    """Query Ollama /api/tags to list all locally installed models with hardware classification."""
    import requests
    base = base_url or get_ollama_base_url()
    try:
        r = requests.get(f"{base}/api/tags", timeout=5.0)
        if r.status_code != 200:
            log.warning("ollama /api/tags returned HTTP %d", r.status_code)
            return []
        data = r.json().get("models", [])
        models = []
        for m in data:
            name = m.get("name", "")
            size_gb = round(m.get("size", 0) / (1024 ** 3), 2)
            meta = MODEL_CATALOG.get(name, {})
            classification = classify_model_for_hardware(
                name, size_gb, m.get("details", {}), m.get("capabilities", [])
            )
            models.append({
                "name": name,
                "size_gb": size_gb,
                "modified_at": m.get("modified_at"),
                "display_name": meta.get("display_name", name),
                "parameter_size": classification["parameter_size"],
                "vision": classification["vision"],
                "category": classification["category"],
                "badges": classification["badges"],
                "hardware_notes": classification["description"],
                "is_recommended": classification["is_recommended"]
            })
        # Sort so BEST RECOMMENDED is first, then ascending by size
        models.sort(key=lambda x: (not x["is_recommended"], x["size_gb"]))
        return models
    except Exception as e:
        log.warning("could not connect to ollama at %s: %s", base, e)
        return []


def check_model_health(model_name: str, base_url: str | None = None, timeout: float = 12.0) -> dict[str, Any]:
    """Test model responsiveness with a lightweight generation probe."""
    import requests
    base = base_url or get_ollama_base_url()
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{base}/api/generate",
            json={
                "model": model_name,
                "prompt": "ping",
                "stream": False,
                "options": {"num_predict": 5, "num_ctx": 2048}
            },
            timeout=timeout
        )
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        if r.status_code == 200:
            return {"ok": True, "model": model_name, "latency_ms": latency_ms, "error": None}
        return {"ok": False, "model": model_name, "latency_ms": latency_ms, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"ok": False, "model": model_name, "latency_ms": latency_ms, "error": str(e)}


def unload_model(model_name: str | None = None, base_url: str | None = None) -> bool:
    """Unload a model from GPU VRAM immediately by posting keep_alive: 0.
    
    Essential before running heavy CUDA workloads like Faster-Whisper large-v3
    or FFmpeg NVENC to prevent VRAM competition on 8GB GPUs.
    """
    import requests
    base = base_url or get_ollama_base_url()
    target_model = model_name or get_active_model()
    try:
        r = requests.post(
            f"{base}/api/generate",
            json={"model": target_model, "keep_alive": 0},
            timeout=5.0
        )
        if r.status_code == 200:
            log.info("unloaded model '%s' from VRAM (keep_alive: 0)", target_model)
            return True
        log.warning("unload_model '%s' returned HTTP %d", target_model, r.status_code)
        return False
    except Exception as e:
        log.warning("could not unload model '%s': %s", target_model, e)
        return False


def get_running_models(base_url: str | None = None) -> list[dict[str, Any]]:
    """Query /api/ps to inspect active models in memory, their VRAM footprint, and processor."""
    import requests
    base = base_url or get_ollama_base_url()
    try:
        r = requests.get(f"{base}/api/ps", timeout=5.0)
        if r.status_code != 200:
            return []
        data = r.json().get("models", [])
        running = []
        for m in data:
            running.append({
                "name": m.get("name"),
                "size_gb": round(m.get("size", 0) / (1024 ** 3), 2),
                "processor": m.get("processor", "Unknown"),
                "context": m.get("context", 0),
                "expires_at": m.get("expires_at")
            })
        return running
    except Exception as e:
        log.warning("could not query ollama /api/ps: %s", e)
        return []


def strip_reasoning_tags(text: str) -> str:
    """Remove <think>...</think> blocks from models with internal chain-of-thought."""
    if not text or not isinstance(text, str):
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def query_ollama(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    system: str | None = None,
    media: list[dict] | None = None,
    timeout: float = 120.0,
    options: dict | None = None,
    cfg: dict | None = None,
    enable_fallback: bool = True
) -> str:
    """Execute inference on Ollama with memory controls, vision support, and graceful fallback."""
    import requests
    cfg = cfg or load_config()
    base = get_ollama_base_url(cfg)
    chosen_model = model or get_active_model(cfg)
    fallback_model = get_fallback_model(cfg)
    
    # Bound context window to 8192 tokens by default to keep VRAM strictly <= 3.7GB
    opt = {
        "num_ctx": int(cfg.get("llm", {}).get("context_length", 8192)),
        "temperature": float(cfg.get("llm", {}).get("temperature", 0.3)),
        "top_p": float(cfg.get("llm", {}).get("top_p", 0.9)),
    }
    if options:
        opt.update(options)

    # Process multimodal media (images)
    images_b64: list[str] = []
    if media:
        for m in media:
            if m.get("kind") == "image" and m.get("data"):
                raw_bytes = m["data"]
                images_b64.append(base64.b64encode(raw_bytes).decode("ascii"))
            else:
                log.warning("unsupported media item for Ollama: %s", m.get("kind"))

    # Vision capability gate: text-only models fail gracefully by dropping images
    if images_b64:
        meta = MODEL_CATALOG.get(chosen_model, {})
        has_vision = bool(meta.get("vision") or "vision" in chosen_model.lower() or "llava" in chosen_model.lower() or "qwen3.5" in chosen_model.lower())
        if not has_vision:
            log.warning("model '%s' does not support vision/multimodal input; processing as text-only prompt", chosen_model)
            images_b64 = []

    payload: dict[str, Any] = {
        "model": chosen_model,
        "prompt": prompt,
        "stream": False,
        "options": opt
    }
    if system:
        payload["system"] = system
    if schema:
        payload["format"] = schema
    if images_b64:
        payload["images"] = images_b64

    # Try primary model
    t0 = time.perf_counter()
    try:
        log.info("querying Ollama model='%s' (ctx=%d, images=%d)...",
                 chosen_model, opt["num_ctx"], len(images_b64))
        r = requests.post(f"{base}/api/generate", json=payload, timeout=timeout)
        if r.status_code == 200:
            res_data = r.json()
            raw_resp = res_data.get("response", "")
            cleaned = strip_reasoning_tags(raw_resp)
            eval_cnt = res_data.get("eval_count", 0)
            elapsed = time.perf_counter() - t0
            log.info("ollama model='%s' finished in %.2fs (%d tokens, ~%.1f tok/s)",
                     chosen_model, elapsed, eval_cnt, eval_cnt / elapsed if elapsed > 0 else 0)
            return cleaned
        raise LLMError(f"ollama HTTP {r.status_code}", detail=r.text[:300])
    except Exception as primary_err:
        if not enable_fallback or chosen_model == fallback_model:
            raise LLMError(f"ollama call failed on '{chosen_model}'", detail=str(primary_err)) from primary_err
        
        # Fallback to designated fallback model (llama3.2:3b)
        log.warning("primary model '%s' failed: %s — falling back to '%s'",
                    chosen_model, primary_err, fallback_model)
        payload["model"] = fallback_model
        if images_b64:
            # Fallback model may not support vision; omit images to avoid hard failure
            payload.pop("images", None)
            log.info("omitted images for fallback model '%s'", fallback_model)
            
        t_fb = time.perf_counter()
        try:
            r_fb = requests.post(f"{base}/api/generate", json=payload, timeout=timeout)
            if r_fb.status_code == 200:
                res_data = r_fb.json()
                cleaned = strip_reasoning_tags(res_data.get("response", ""))
                log.info("fallback model '%s' succeeded in %.2fs",
                         fallback_model, time.perf_counter() - t_fb)
                return cleaned
            raise LLMError(f"fallback ollama HTTP {r_fb.status_code}", detail=r_fb.text[:300])
        except Exception as fb_err:
            raise LLMError(f"both primary '{chosen_model}' and fallback '{fallback_model}' failed",
                           detail=f"primary: {primary_err}; fallback: {fb_err}") from fb_err

