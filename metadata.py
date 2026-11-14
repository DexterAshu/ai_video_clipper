"""Per-clip social metadata: title (<60 chars, hook-first), 2-sentence
description, 8-12 hashtags. One LLM call with strict JSON; deterministic
template fallback guarantees every clip ALWAYS ships valid metadata."""
from __future__ import annotations

import json
import re
from collections import Counter

import llm
from config import load_config
from errors import LLMError
from logutil import get_logger
from schemas import SCHEMAS, validate

log = get_logger("metadata")

STAPLE_TAGS = ["#shorts", "#reels", "#viral", "#fyp", "#video",
               "#clips", "#trending", "#creator", "#daily", "#watch"]

STOPWORDS = set("""a an and are as at be but by for from has have he her his i
if in is it its just me my not of on or our she so that the their them they
this to was we were what when which who will with you your about all can could
do did get got had how like more most no now one only other out over some such
than then there these up us very would""".split())

# Conversational filler that reads as hashtag spam when it leaks through
# (the reported "#done #cool #right #yeah #wearing"). This is the single junk
# blocklist for the whole app — upload_scheduler.clean_hashtags reuses it via
# clean_tag() so there aren't two policies to keep in sync.
FILLER_WORDS = STOPWORDS | set("""
done cool right yeah yep yes nope nah okay ok gonna wanna gotta kinda sorta
really actually literally basically honestly seriously totally maybe probably
stuff thing things something anything everything nothing someone everyone
anyone nobody people guy guys man woman dude kind sort lot lots bit way ways
know think thought feel feels felt want wanted need needed make makes made
making get gets getting go goes going went come comes came take takes took
look looks looking see sees saw say says said tell told talk talking wearing
good bad best better worse nice great awesome amazing cool crazy wild huge
big small little much many even still also because though although actually
here there where why what when who whom whose while into onto upon than then
used using wait waiting answer answered saying doing woke hold holding thank
thanks please sorry worry worried next last first round hey yours actual
clearly enough almost always never ever tell told talk talking asking asked
calling called trying tried keep keeping give given gave show shows showed
put putting turn turned start started stop stopped let lets bit chunk chunks
""".split())

PROMPT = """TASK: Write YouTube Shorts metadata for a short vertical video clip.
Ground everything STRICTLY in the provided transcript — use the real names, topics, and the
actual punchline or statement made. No clickbait, no invented facts, no generic placeholder copies.
CONSTRAINTS:
- title: under 60 characters, the hook first, no quotes, no emojis, grounded strictly in the speech
- description: exactly 2 sentences and NO hashtags in the text.
  Sentence 1 is a hook that EXPANDS the title (adds stakes or intrigue from this clip).
  Sentence 2 gives one concrete detail from this clip so a viewer knows what they will watch.
- hashtags: 8 to 12 topic tags only — nouns, names, and themes from the clip
  (never filler like done/yeah/cool/right). Each is #word, letters/digits.
- CRITICAL RULES:
  * Do NOT invent accusations, fraud, scam claims (e.g. 'Scam Exposed'), or unverified charges.
  * Do NOT output generic startup/founder tropes unless the speaker is literally discussing a startup.
  * Base the title and description 100% on the transcript provided below.
OUTPUT SCHEMA (respond with ONLY this JSON):
{schema}
FORMAT STRUCTURE (do NOT copy this placeholder text, extract from the transcript below):
{{"title": "Spoken Hook Under Sixty Characters", "description": "Engaging sentence introducing the moment from the speech. Second sentence highlighting the concrete outcome or punchline.", "hashtags": ["#topic1", "#topic2", "#topic3", "#topic4", "#topic5", "#topic6", "#topic7", "#shorts"]}}
{source_context}CLIP HOOK: {hook}
CLIP TRANSCRIPT:
{text}
"""

PROMPT_LEAK_PHRASES = (
    "saved my startup",
    "two-time founder",
    "kept his team shipping",
    "when everything was on fire",
    "spoken hook under sixty",
    "two-time contestant breaks down the daily reset",
    "most founders quit the week before",
    "most contestants quit the week before"
)


def detect_prompt_leak(meta: dict) -> bool:
    """Return True if generated metadata echoes known few-shot prompt templates."""
    title = str(meta.get("title", "")).lower()
    desc = str(meta.get("description", "")).lower()
    for phrase in PROMPT_LEAK_PHRASES:
        if phrase in title or phrase in desc:
            return True
    return False


def clean_tag(raw: str) -> str | None:
    """Normalize one hashtag to a bare lowercase word, or None if it's junk.
    The single rule used everywhere: strip #, lowercase, keep only 3+ letter
    /digit words that aren't conversational filler (FILLER_WORDS)."""
    w = (raw or "").lstrip("#").strip().lower()
    if len(w) < 3 or not re.fullmatch(r"[a-z0-9]+", w) or w in FILLER_WORDS:
        return None
    return w


def _strip_hashtags(text: str) -> str:
    """Drop any hashtag tokens from description prose (they belong only in the
    hashtags field, which YouTube appends separately)."""
    return re.sub(r"\s*#\w+", "", text or "").strip()


def topic_hashtags(text: str, seed_tags: list | None = None,
                   min_n: int = 8, max_n: int = 12) -> list[str]:
    """Clean, topic-first hashtags. Keeps entity/content words from seed_tags
    (e.g. an LLM's output) and from the transcript — proper nouns first, then
    by frequency — drops filler, and only pads with platform staples to reach
    the schema minimum. Lowercase, deduped, #-prefixed, always includes
    #shorts. Topic tags come first so an upload-time cap keeps the best ones."""
    picked: list[str] = []

    def add(w: str | None) -> None:
        if w and w not in picked and len(picked) < max_n:
            picked.append(w)

    for t in (seed_tags or []):          # cleaned LLM tags: usually most topical
        add(clean_tag(t))

    tokens = re.findall(r"[A-Za-z][A-Za-z']+", text or "")
    freq = Counter(w.lower() for w in tokens)
    proper = [w.lower() for w in tokens if w[0].isupper()]  # name/entity signal
    for w in proper + [w for w, _ in freq.most_common()]:
        c = clean_tag(w)
        if c and len(c) >= 4:            # extracted words must be content-length
            add(c)

    if "shorts" not in picked:           # #shorts is mandatory for the format
        if len(picked) >= max_n:
            picked[-1] = "shorts"
        else:
            picked.append("shorts")
    for s in STAPLE_TAGS:                 # pad only to the schema minimum
        if len(picked) >= min_n:
            break
        add(clean_tag(s))
    return ["#" + t for t in picked]


def generate_metadata(clip_text: str, hook: str, cfg: dict | None = None,
                      provider: str | None = None,
                      source_title: str | None = None) -> dict:
    """Returns ClipMetadata (schema-validated) — LLM first, template fallback.
    Hashtags are always re-cleaned through topic_hashtags so junk can't ride
    through from the model, and any stray hashtags are stripped from the
    description prose."""
    cfg = cfg or load_config()
    ctx_str = f"SOURCE VIDEO / SHOW CONTEXT: {source_title}\n" if source_title else ""
    prompt = PROMPT.format(schema=json.dumps(SCHEMAS["clip_metadata"]),
                           hook=hook[:200], text=clip_text[:2000],
                           source_context=ctx_str)
    try:
        data = llm.complete_json("clip_metadata", "clip_metadata", prompt,
                                 provider=provider,
                                 context={"text": clip_text, "hook": hook},
                                 cfg=cfg)
        if detect_prompt_leak(data):
            log.warning("metadata LLM echoed prompt template (%s) — falling back to grounded template",
                        data.get("title"))
            data = template_metadata(hook, clip_text)
    except LLMError as e:
        log.warning("metadata LLM failed (%s) — template fallback", e)
        data = template_metadata(hook, clip_text)
        validate(data, "clip_metadata")
        return data

    data["description"] = _strip_hashtags(data.get("description", ""))
    data["hashtags"] = topic_hashtags(clip_text, seed_tags=data.get("hashtags"))
    validate(data, "clip_metadata")
    return data


def deduplicate_job_metadata(clips: list[dict]) -> list[dict]:
    """Ensure no two clips within the same job share the exact same title.
    If duplicates occur, append distinctive hook details to differentiate them."""
    seen_titles: dict[str, int] = {}
    for i, clip in enumerate(clips):
        meta = clip.get("metadata")
        if not meta or not isinstance(meta, dict):
            continue
        title = meta.get("title", f"Clip {i + 1}").strip()
        if title in seen_titles:
            seen_titles[title] += 1
            count = seen_titles[title]
            hook = clip.get("hook", "").strip()
            if hook and hook.lower() not in title.lower():
                distinct_title = _trim_title(f"{title}: {hook}")
            else:
                distinct_title = _trim_title(f"{title} (Part {count})")
            meta["title"] = distinct_title
        else:
            seen_titles[title] = 1
    return clips


def template_metadata(hook: str, text: str) -> dict:
    """Deterministic generator (LLM fallback): title = trimmed hook (≤60);
    description = hook sentence + one concrete context sentence from the
    transcript; hashtags = clean transcript topics + platform staples (always
    8-12, always pattern-valid)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", _strip_hashtags(text))
                 if s.strip()]
    hook_s = _strip_hashtags(hook or (sentences[0] if sentences else "")).strip()
    title = _trim_title(hook_s) or "Watch this moment"

    # context = the most substantial later sentence (not the hook), so the two
    # description lines don't just restate each other. Compare normalized so a
    # trailing period doesn't make the hook sentence look like new context.
    def _norm(s: str) -> str:
        return s.rstrip(".!?").strip().lower()

    hook_norm = _norm(hook_s)
    context = next((s for s in sentences
                    if s and _norm(s) != hook_norm and len(s.split()) >= 4), "")
    description = " ".join(x for x in [
        hook_s if hook_s.endswith((".", "!", "?")) else hook_s + ".",
        context or "Watch the full moment for the payoff.",
    ] if x).strip()

    return {"title": title, "description": description,
            "hashtags": topic_hashtags(text)}


def _trim_title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip().strip('"')
    if len(s) <= 60:
        return s.rstrip(".")[:60]
    cutoff = s[:57]
    if " " in cutoff:
        cutoff = cutoff[:cutoff.rfind(" ")]
    return cutoff + "..."


if __name__ == "__main__":
    demo = template_metadata(
        "Most projects fail not because of bad code.",
        "Most projects fail not because of bad code. They fail because of "
        "unclear goals. Write the one sentence that explains who needs this.")
    validate(demo, "clip_metadata")
    print(json.dumps(demo, indent=2))
