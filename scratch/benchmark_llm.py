"""Benchmark llama3.2:3b vs qwen3.5:4b for Frameza Stage 6.

Evaluates:
1. Transcript analysis & highlight extraction (Hinglish/Hindi & English)
2. Hook generation & viral titling
3. JSON schema adherence & validity
4. Latency (s) & eval speed
5. Memory & GPU coexistence
"""
import json
import os
import sys
import time
import requests

MODELS = ["llama3.2:3b", "qwen3.5:4b"]
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

SAMPLE_HINGLISH_TRANSCRIPT = (
    "Samay: Bhai sach bolu toh maine kabhi socha nahi tha ki ye show itna viral ho jayega! "
    "Nawazuddin sir yahan baithe hain, inhone bola script likh ke aao, par hum toh bina script ke hi aa gaye. "
    "Nawazuddin: Script ki zaroorat unko hoti hai jinke paas talent nahi hota, par tumhara alag hi chal raha hai. "
    "Bhuvan: Arey bhai, yahan script hoti toh hum aate hi nahi! Audience ko raw real moments pasand hain aajkal."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "viral_hook": {"type": "string"},
        "viral_title": {"type": "string"},
        "topic": {"type": "string"},
        "engagement_score": {"type": "integer"},
        "hinglish_meaning": {"type": "string"},
        "key_quote": {"type": "string"}
    },
    "required": [
        "viral_hook", "viral_title", "topic",
        "engagement_score", "hinglish_meaning", "key_quote"
    ]
}

PROMPT = f"""You are an elite short-form video editor for YouTube Shorts and Instagram Reels.
Analyze this video transcript excerpt:
"{SAMPLE_HINGLISH_TRANSCRIPT}"

Tasks:
1. Create a high-converting, curiosity-driven viral_hook (max 8 words).
2. Create an engaging, clickable viral_title (max 60 chars).
3. Identify the main topic.
4. Assign an engagement_score from 1 to 100 based on humor, celebrity punchlines, and retention potential.
5. Provide a concise hinglish_meaning explaining the context in clear English.
6. Extract the punchiest key_quote from the dialogue.

Return ONLY a valid JSON object matching the requested schema.
"""

def run_benchmark():
    results = {}
    print("=" * 60)
    print("Frameza LLM Benchmark: llama3.2:3b vs qwen3.5:4b")
    print("=" * 60)
    
    for model in MODELS:
        print(f"\n[Testing Model: {model}]...")
        # Unload previous model to test clean cold/warm load & isolate VRAM
        requests.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "keep_alive": 0})
        time.sleep(1)
        
        t0 = time.time()
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": PROMPT,
                    "format": SCHEMA,
                    "stream": False,
                    "options": {
                        "num_ctx": 8192,
                        "temperature": 0.3,
                        "top_p": 0.9
                    }
                },
                timeout=90
            )
            elapsed = time.time() - t0
            if resp.status_code != 200:
                print(f"FAILED: HTTP {resp.status_code} - {resp.text[:200]}")
                continue
            
            data = resp.json()
            raw_text = data.get("response", "")
            eval_count = data.get("eval_count", 0)
            eval_dur_s = data.get("eval_duration", 1) / 1e9
            tok_per_sec = eval_count / eval_dur_s if eval_dur_s > 0 else 0
            
            # Check JSON validation
            parsed = json.loads(raw_text)
            
            # Query ollama ps for memory
            ps_resp = requests.get(f"{OLLAMA_URL}/api/ps").json()
            model_info = next((m for m in ps_resp.get("models", []) if model in m.get("name", "")), {})
            mem_size = model_info.get("size", 0) / (1024 * 1024 * 1024)
            processor = model_info.get("processor", "Unknown")
            
            results[model] = {
                "elapsed_s": round(elapsed, 2),
                "eval_count": eval_count,
                "tok_per_sec": round(tok_per_sec, 1),
                "vram_gb": round(mem_size, 2),
                "processor": processor,
                "parsed": parsed,
                "json_valid": True
            }
            print(f"-> Latency: {elapsed:.2f}s | Speed: {tok_per_sec:.1f} tok/s | VRAM: {mem_size:.2f} GB ({processor})")
            print(f"-> Hook: {parsed.get('viral_hook')}")
            print(f"-> Title: {parsed.get('viral_title')}")
            print(f"-> Score: {parsed.get('engagement_score')}/100")
            print(f"-> Meaning: {parsed.get('hinglish_meaning')}")
            print(f"-> Key Quote: {parsed.get('key_quote')}")
            
        except Exception as e:
            print(f"Error testing {model}: {e}")
            results[model] = {"error": str(e), "json_valid": False}
            
    print("\n" + "=" * 60)
    print("SUMMARY COMPARISON")
    print("=" * 60)
    print(json.dumps(results, indent=2))
    
    with open("scratch/benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_benchmark()

