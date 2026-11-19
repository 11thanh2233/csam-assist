"""LLM tuỳ chọn cho phần soạn guideline. Tool chạy được không cần LLM; có LLM thì bản nháp đầy đủ hơn.

Hai cách nối, chọn bằng biến môi trường (xem .env.example):
  - Claude API của Anthropic  (ANTHROPIC_API_KEY)
  - Máy chủ tương thích OpenAI (GUIDELINE_OPENAI_BASE_URL), ví dụ Ollama hoặc LM Studio chạy model local.
Ảnh chỉ được gửi cho LLM khi người dùng bật "Cho phép gửi ảnh mẫu" trên panel; mặc định chỉ gửi số liệu.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional


class LLMError(RuntimeError):
    pass


def _post(url: str, headers: dict, body: dict, timeout: int) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"content-type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"LLM trả lỗi {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"Không gọi được LLM tại {url}: {exc}") from exc


class LLM:
    provider = "none"
    model = ""
    sees_images = True

    def complete(self, system: str, text: str, images: Optional[list[bytes]] = None, max_tokens: int = 3000) -> str:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"provider": self.provider, "model": self.model, "local": self.provider == "local"}


class AnthropicLLM(LLM):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: int = 180):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, system, text, images=None, max_tokens=3000):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(img).decode()}} for img in images or []]
        content.append({"type": "text", "text": text})
        data = _post("https://api.anthropic.com/v1/messages",
                     {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                     {"model": self.model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": content}]}, self.timeout)
        return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")


class OpenAICompatLLM(LLM):
    provider = "local"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 600):
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout
        if not re.search(r"//(127\.0\.0\.1|localhost|\[::1\])[:/]", self.base_url + "/"):
            self.provider = "openai-compatible"

    def complete(self, system, text, images=None, max_tokens=3000):
        content = [{"type": "text", "text": text}]
        for img in images or []:
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}})
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post(self.base_url + "/chat/completions", headers,
                     {"model": self.model, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}, self.timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM trả về dữ liệu không đúng dạng: {str(data)[:200]}") from exc


def from_env() -> Optional[LLM]:
    choice = os.getenv("GUIDELINE_LLM", "auto").lower()
    if choice == "none":
        return None
    key = os.getenv("ANTHROPIC_API_KEY", "")
    base = os.getenv("GUIDELINE_OPENAI_BASE_URL", "")
    model = os.getenv("GUIDELINE_LLM_MODEL", "")
    if choice in ("auto", "anthropic") and key:
        return AnthropicLLM(key, model or "claude-sonnet-5-5")
    if choice in ("auto", "openai", "local") and base and model:
        return OpenAICompatLLM(base, model, os.getenv("GUIDELINE_OPENAI_API_KEY", ""))
    return None


def parse_json(text: str):
    """Lấy khối JSON đầu tiên trong câu trả lời (model hay bọc JSON trong ```json ... ```)."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise LLMError("LLM không trả về JSON.")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(candidate[start:])
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON của LLM không hợp lệ: {exc}") from exc
    return value
