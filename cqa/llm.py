"""LLM provider layer: anthropic | openai-compatible (organizers' temporary API) | mock. JSON out, retries, disk cache.

Env:
  CQA_PROVIDER   anthropic | openai | mock          (default: anthropic if ANTHROPIC_API_KEY, openai if OPENAI_API_KEY/CQA_BASE_URL, else mock)
  CQA_MODEL      model id (default anthropic: claude-sonnet-5-5, openai: gpt-4o-mini)
  CQA_BASE_URL   base url of an OpenAI-compatible endpoint (e.g. the hackathon's temporary API)
  CQA_API_KEY    key for that endpoint (falls back to OPENAI_API_KEY)
  CQA_CACHE      cache dir (default .cache/llm), CQA_NO_CACHE=1 to disable
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

CACHE = Path(os.environ.get("CQA_CACHE", ".cache/llm"))


def provider() -> str:
    p = os.environ.get("CQA_PROVIDER")
    if p:
        return p
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("CQA_BASE_URL") or os.environ.get("OPENAI_API_KEY") or os.environ.get("CQA_API_KEY"):
        return "openai"
    return "mock"


def model() -> str:
    return os.environ.get("CQA_MODEL") or {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}.get(provider(), "mock")


def extract_json(text: str):
    """Parse the first JSON object/array in text (handles ```json fences and trailing prose)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in model output")
    depth, instr, esc = 0, False, False
    open_c = t[start]
    close_c = "}" if open_c == "{" else "]"
    for i in range(start, len(t)):
        ch = t[i]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return json.loads(t[start : i + 1])
    raise ValueError("unbalanced JSON in model output")


_clients: dict = {}


def _call_anthropic(system: str, user: str, max_tokens: int, temperature: float) -> str:
    import anthropic

    if "a" not in _clients:
        _clients["a"] = anthropic.Anthropic()
    msg = _clients["a"].messages.create(model=model(), max_tokens=max_tokens, temperature=temperature, system=system, messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _call_openai(system: str, user: str, max_tokens: int, temperature: float) -> str:
    from openai import OpenAI

    if "o" not in _clients:
        _clients["o"] = OpenAI(base_url=os.environ.get("CQA_BASE_URL") or None, api_key=os.environ.get("CQA_API_KEY") or os.environ.get("OPENAI_API_KEY") or "none")
    kw = dict(model=model(), messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=temperature, max_tokens=max_tokens)
    try:
        r = _clients["o"].chat.completions.create(response_format={"type": "json_object"}, **kw)
    except Exception:
        r = _clients["o"].chat.completions.create(**kw)
    return r.choices[0].message.content or ""


def complete_json(system: str, user: str, max_tokens: int = 4000, temperature: float = 0.0, retries: int = 3, mock_fn=None):
    """Call the configured provider and return parsed JSON. Cached by (provider, model, prompt)."""
    prov = provider()
    if prov == "mock":
        if mock_fn is None:
            raise RuntimeError("mock provider needs mock_fn")
        return mock_fn()
    key = hashlib.sha256(json.dumps([prov, model(), system, user, temperature]).encode()).hexdigest()
    cp = CACHE / f"{key}.json"
    if cp.exists() and not os.environ.get("CQA_NO_CACHE"):
        return json.loads(cp.read_text())
    last = None
    for a in range(retries):
        try:
            text = _call_anthropic(system, user, max_tokens, temperature) if prov == "anthropic" else _call_openai(system, user, max_tokens, temperature)
            out = extract_json(text)
            CACHE.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(out, ensure_ascii=False))
            return out
        except Exception as e:  # rate limits, bad JSON
            last = e
            time.sleep(2 * (a + 1))
            user = user + "\n\nReturn ONLY valid JSON matching the schema."
    raise RuntimeError(f"LLM failed after {retries} tries: {last}")
