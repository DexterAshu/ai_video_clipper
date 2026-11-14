"""Dynamic Caption Language Translation and Audio-Sync Alignment.

Supports:
  1. 'hinglish' (Default): Conversational Roman-script Hindi-English mix.
     Example: "Aaj hum ek bahut important topic par baat karenge."
  2. 'hindi': Natural conversational Devanagari Hindi.
     Example: "आज हम एक बहुत जरूरी विषय पर बात करेंगे।"
  3. 'english': Natural English translation.
     Example: "Today we are discussing a very important topic."

Preserves timestamp synchronization:
  - Speech recognition timestamps (Faster-Whisper) remain the ground truth.
  - Translations are grouped per speech segment [start, end].
  - Segment bounds are strictly preserved so audio synchronization never drifts.
  - Words within each translated segment are proportionally distributed for
    animated karaoke pop.
  - Graceful fallback: If LLM is unreachable or fails, returns original words.
"""
from __future__ import annotations

import re
from typing import Any

from logutil import get_logger

log = get_logger("caption_lang")

SUPPORTED_LANGUAGES = ("hinglish", "hindi", "english")
DEFAULT_LANGUAGE = "hinglish"

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def has_devanagari(text: str) -> bool:
    """Return True if text contains any Devanagari characters."""
    return bool(DEVANAGARI_RE.search(text))


def _prompt_for_language(text: str, target_lang: str) -> str:
    if target_lang == "hinglish":
        instructions = (
            "Convert/translate the following speech into natural, conversational HINGLISH "
            "(Hindi-English mix written in the ROMAN alphabet).\n"
            "Rules:\n"
            "- Use natural conversational Roman Hindi words (e.g., 'aaj', 'hum', 'baat', 'karenge').\n"
            "- Keep commonly used English words in English (e.g., 'important', 'topic', 'podcast', 'video').\n"
            "- Do NOT use pure formal Hindi and do NOT use Devanagari script.\n"
            "- Output ONLY the converted text without explanations, quotes, or notes."
        )
    elif target_lang == "hindi":
        instructions = (
            "Translate the following speech into natural, conversational HINDI written in DEVANAGARI script.\n"
            "Rules:\n"
            "- Use natural Hindi grammar and spelling (e.g., 'आज हम एक बहुत जरूरी विषय पर बात करेंगे।').\n"
            "- Output ONLY the Hindi translation in Devanagari script without explanations or notes."
        )
    elif target_lang == "english":
        instructions = (
            "Translate the following speech into clear, natural, grammatically correct ENGLISH.\n"
            "Rules:\n"
            "- Keep the tone natural and engaging.\n"
            "- Output ONLY the English translation without explanations or notes."
        )
    else:
        return text

    return f"{instructions}\n\nOriginal Speech:\n\"{text}\"\n\nOutput:"


def translate_segment_text(text: str, target_lang: str, cfg: dict | None = None,
                           provider: str | None = None) -> str:
    """Translate or transliterate a text snippet into the target caption language
    using local LLM. Falls back to original text on any failure."""
    target_lang = (target_lang or DEFAULT_LANGUAGE).lower()
    if target_lang not in SUPPORTED_LANGUAGES:
        target_lang = DEFAULT_LANGUAGE

    clean = text.strip()
    if not clean:
        return ""

    # Check if text is already in the target format
    if target_lang == "hindi" and has_devanagari(clean):
        return clean
    if target_lang == "english" and not has_devanagari(clean) and re.match(r"^[A-Za-z0-9\s.,!?'\"-]+$", clean):
        # Already pure English characters
        pass

    prompt = _prompt_for_language(clean, target_lang)

    try:
        from llm import resolve_provider
        import requests
        from config import load_config

        cfg = cfg or load_config()
        prov = resolve_provider(cfg, provider)

        if prov == "ollama":
            base = cfg["llm"].get("ollama_base_url", "http://localhost:11434")
            model = cfg["llm"].get("ollama_model", "llama3.2:3b")
            r = requests.post(
                f"{base}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.2}},
                timeout=45,
            )
            if r.status_code == 200:
                resp = r.json().get("response", "").strip()
                # Clean up any surrounding quotes
                resp = re.sub(r'^["\']|["\']$', '', resp).strip()
                if resp:
                    return resp
        elif prov == "gemini":
            from llm import _gemini_client
            client = _gemini_client()
            resp = client.models.generate_content(
                model=cfg["llm"]["gemini_model"], contents=prompt)
            out = getattr(resp, "text", "").strip()
            out = re.sub(r'^["\']|["\']$', '', out).strip()
            if out:
                return out
        elif prov == "groq":
            from llm import _groq_complete
            # Simple text fallback
            pass
    except Exception as e:
        log.warning("caption translation to '%s' failed (%s) — keeping original transcript",
                    target_lang, e)

    return clean


def align_translated_words(original_words: list[dict], translated_text: str,
                           seg_start: float, seg_end: float) -> list[dict]:
    """Distribute translated words proportionally across [seg_start, seg_end]
    to guarantee zero timeline drift and maintain animated karaoke pop."""
    words = [w for w in re.split(r"\s+", translated_text.strip()) if w]
    if not words:
        return original_words

    seg_dur = max(0.1, seg_end - seg_start)
    # Weight word durations by character length
    lengths = [max(1, len(w)) for w in words]
    total_len = sum(lengths)

    aligned = []
    curr_t = seg_start
    for i, w in enumerate(words):
        fraction = lengths[i] / total_len
        w_dur = max(0.08, seg_dur * fraction)
        w_start = curr_t
        w_end = min(seg_end, w_start + w_dur) if i < len(words) - 1 else seg_end
        aligned.append({
            "start": round(w_start, 3),
            "end": round(w_end, 3),
            "word": w
        })
        curr_t = w_end

    return aligned


def process_caption_language(words: list[dict], target_lang: str = DEFAULT_LANGUAGE,
                             cfg: dict | None = None, provider: str | None = None) -> list[dict]:
    """Main entry point: takes clip-relative words, groups them into natural
    speech clauses, translates each clause to the target language, and aligns
    timestamps within original segment boundaries. Returns aligned word dicts."""
    target_lang = (target_lang or DEFAULT_LANGUAGE).lower()
    if target_lang not in SUPPORTED_LANGUAGES:
        target_lang = DEFAULT_LANGUAGE

    if not words:
        return []

    # Group words into sentence / pause-separated clauses
    clauses: list[list[dict]] = []
    current_clause: list[dict] = []

    for i, w in enumerate(words):
        current_clause.append(w)
        # Split on punctuation or pause > 0.45s
        is_punc = bool(re.search(r"[.?!,।]$", w["word"]))
        has_pause = (i < len(words) - 1 and (words[i + 1]["start"] - w["end"]) > 0.45)
        if (is_punc or has_pause or len(current_clause) >= 8) and len(current_clause) >= 3:
            clauses.append(current_clause)
            current_clause = []

    if current_clause:
        clauses.append(current_clause)

    all_aligned: list[dict] = []
    for clause in clauses:
        clause_text = " ".join(w["word"] for w in clause)
        c_start = clause[0]["start"]
        c_end = clause[-1]["end"]

        translated = translate_segment_text(clause_text, target_lang, cfg, provider)
        aligned = align_translated_words(clause, translated, c_start, c_end)
        all_aligned.extend(aligned)

    return all_aligned

