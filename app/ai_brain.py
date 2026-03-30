"""
app/ai_brain.py

AI model abstraction layer.
The rest of the app calls ai_brain.complete(prompt) — it never talks to
any model provider directly. The active backend is set by AI_PROVIDER in .env.

Supported providers
───────────────────
  claude   — Anthropic Claude API (best quality, paid)
             Requires: ANTHROPIC_API_KEY
             Model:    set via CLAUDE_MODEL (default: claude-haiku-4-5-20251001)

  groq     — Groq cloud inference (free tier, very fast)
             Requires: GROQ_API_KEY  (free at console.groq.com)
             Model:    set via GROQ_MODEL (default: llama-3.3-70b-versatile)

  ollama   — Local Ollama server (completely free, runs on your machine)
             Requires: Ollama installed + model pulled
             Base URL: OLLAMA_BASE_URL (default: http://localhost:11434)
             Model:    OLLAMA_MODEL (default: llama3.2)

Switching provider
──────────────────
  In .env, set:  AI_PROVIDER=groq
  Restart the app. Nothing else changes.

Quality notes
─────────────
  Claude Sonnet  — Best captions, best hook detection, most brand-accurate
  Claude Haiku   — Good quality, cheaper than Sonnet, still paid
  Groq Llama 70B — Close to Claude Haiku quality, free, fast
  Groq Llama 8B  — Decent, very fast, free
  Ollama Llama3  — Good, free forever, uses server RAM (~5GB for 8B model)
"""
import os
import json
import logging

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean_json(raw: str) -> str:
    """Strip markdown code fences that some models wrap JSON in."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        # Take the content between first pair of fences
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _get_provider() -> str:
    return os.getenv("AI_PROVIDER", "claude").lower().strip()


# ── Backend: Claude ────────────────────────────────────────────────────────────

def _complete_claude(prompt: str, max_tokens: int) -> str:
    import anthropic
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── Backend: Groq ──────────────────────────────────────────────────────────────

def _complete_groq(prompt: str, max_tokens: int) -> str:
    from groq import Groq
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return response.choices[0].message.content


# ── Backend: Ollama ────────────────────────────────────────────────────────────

def _complete_ollama(prompt: str, max_tokens: int) -> str:
    import httpx
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model    = os.getenv("OLLAMA_MODEL", "llama3.2")

    response = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model":  model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]


# ── Public API ─────────────────────────────────────────────────────────────────

def complete(prompt: str, max_tokens: int = 2000) -> str:
    """
    Send a prompt to the configured AI provider and return the response text.
    This is the only function the rest of the app should call.
    """
    provider = _get_provider()

    logger.debug(f"[AI Brain] Provider: {provider} | max_tokens: {max_tokens}")

    if provider == "claude":
        raw = _complete_claude(prompt, max_tokens)
    elif provider == "groq":
        raw = _complete_groq(prompt, max_tokens)
    elif provider == "ollama":
        raw = _complete_ollama(prompt, max_tokens)
    else:
        raise ValueError(
            f"Unknown AI_PROVIDER: '{provider}'. "
            f"Valid options: claude, groq, ollama"
        )

    return raw


def complete_json(prompt: str, max_tokens: int = 2000) -> dict | list:
    """
    Like complete(), but parses and returns the response as JSON.
    Strips markdown fences automatically.
    Raises json.JSONDecodeError if the model returns invalid JSON.
    """
    raw = complete(prompt, max_tokens)
    cleaned = _clean_json(raw)
    return json.loads(cleaned)


def get_provider_info() -> dict:
    """Return current provider info for the dashboard / setup check."""
    provider = _get_provider()
    info = {"provider": provider}

    if provider == "claude":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        info["model"] = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        info["configured"] = bool(key and not key.startswith("your_"))

    elif provider == "groq":
        key = os.getenv("GROQ_API_KEY", "")
        info["model"] = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        info["configured"] = bool(key and not key.startswith("your_"))
        info["free"] = True

    elif provider == "ollama":
        info["model"] = os.getenv("OLLAMA_MODEL", "llama3.2")
        info["base_url"] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        info["configured"] = True   # No key needed
        info["free"] = True

    return info

