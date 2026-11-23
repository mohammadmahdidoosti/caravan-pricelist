"""One JSON answer of a cheap model through OpenRouter (key and model from the environment)."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


def model() -> str:
    return os.environ.get("PL_MODEL", "deepseek/deepseek-v4.1-flash")


def has_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def ask(messages: list, max_tokens: int = 4000, title: str = "Caravan supplier price list") -> tuple[dict | None, float]:
    """(answer JSON or None, cost in dollars)."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    body = {"model": model(), "max_tokens": max_tokens, "temperature": 0, "reasoning": {"enabled": False},
            "response_format": {"type": "json_object"}, "messages": messages, "usage": {"include": True}}
    for attempt in range(3):
        try:
            r = requests.post(OPENROUTER, json=body, timeout=180, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "HTTP-Referer": "https://caravansanat.com", "X-Title": title})
        except requests.RequestException as e:
            log.info("model: %s", e)
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503):
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            log.warning("model: HTTP %s %s", r.status_code, r.text[:200])
            return None, 0.0
        j = r.json()
        usd = float((j.get("usage") or {}).get("cost") or 0)
        text = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return (json.loads(m.group(0)) if m else None), usd
        except ValueError:
            return None, usd
    return None, 0.0


def image_part(pic: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(pic).decode()}}
