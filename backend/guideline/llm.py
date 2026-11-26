"""LLM tuỳ chọn cho phần soạn guideline. Tool chạy được không cần LLM; có LLM thì bản nháp đầy đủ hơn.

Hai cách nối, chọn bằng biến môi trường (xem .env.example):
  - Claude API của Anthropic  (ANTHROPIC_API_KEY)
  - Máy chủ tương thích OpenAI (GUIDELINE_OPENAI_BASE_URL), ví dụ Ollama hoặc LM Studio chạy model local.
Ảnh chỉ được gửi cho LLM khi người dùng bật "Cho phép gửi ảnh mẫu" trên panel; mặc định chỉ gửi số liệu.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Iterable, Optional

log = logging.getLogger("sam3-assist")

# 0.9.12: mặc định Claude Opus 5.5, effort medium (đổi được trong Cài đặt của app, áp dụng ngay)
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
EFFORTS = ("low", "medium", "high", "max")      # "" = để model tự chọn mức mặc định của nó
# max_tokens bắt buộc với API: đặt bằng mức tối đa của model (128K) để Claude không bị cắt giữa chừng khi suy nghĩ;
# chỉ token thật sự sinh ra mới tính tiền. Model cũ có mức thấp hơn: API báo lỗi thì tool tự hạ dần.
MAX_OUTPUT = 128000
MAX_OUTPUT_FALLBACKS = (64000, 32000, 16000)


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


def read_sse(lines: Iterable[bytes]) -> dict:
    """Gộp luồng server-sent events của Messages API thành một message: {content, stop_reason, usage}.

    Chỉ giữ khối chữ (text); khối suy nghĩ (thinking) không cần cho tool. Sự kiện lỗi giữa luồng -> LLMError."""
    text: list[str] = []
    usage: dict = {}
    stop_reason = None
    event = ""
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n") if isinstance(raw, bytes) else str(raw).rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = data.get("type") or event
        if kind == "error":
            err = data.get("error") or {}
            raise LLMError(f"LLM trả lỗi {err.get('type', '?')}: {str(err.get('message', ''))[:300]}")
        if kind == "message_start":
            usage.update((data.get("message") or {}).get("usage") or {})
        elif kind == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                text.append(delta.get("text", ""))
        elif kind == "message_delta":
            stop_reason = (data.get("delta") or {}).get("stop_reason") or stop_reason
            usage.update(data.get("usage") or {})
    return {"content": [{"type": "text", "text": "".join(text)}], "stop_reason": stop_reason, "usage": usage}


def _post_stream(url: str, headers: dict, body: dict, timeout: int) -> dict:
    """Gọi Messages API kiểu streaming (0.9.12): max_tokens lớn không bị hết giờ HTTP; timeout tính cho mỗi lần đọc."""
    request = urllib.request.Request(url, data=json.dumps(dict(body, stream=True)).encode("utf-8"),
                                     headers={"content-type": "application/json", "accept": "text/event-stream", **headers},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return read_sse(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"LLM trả lỗi {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"Không gọi được LLM tại {url}: {exc}") from exc


def log_bad_reply(llm, raw: str, where: str, reason: str = "") -> None:
    """0.9.12: ghi nhật ký khi Claude trả lời hỏng (không đọc được JSON...), để biết vì sao: model, effort, stop_reason,
    số token (cả token suy nghĩ), 300 ký tự đầu câu trả lời. Không bao giờ ghi API key."""
    usage = getattr(llm, "last_usage", None) or {}
    details = usage.get("output_tokens_details") or {}
    log.warning("LLM bad reply (%s): model=%s effort=%s stop_reason=%s in=%s out=%s thinking=%s reason=%s reply=%r",
                where, getattr(llm, "model", "?"), getattr(llm, "effort", "") or "default",
                getattr(llm, "last_stop_reason", None), usage.get("input_tokens"), usage.get("output_tokens"),
                details.get("thinking_tokens"), reason[:200], (raw or "")[:300])


class LLM:
    provider = "none"
    model = ""
    sees_images = True
    # 0.9.11: complete() nhận `context` = khối chữ dài giống hệt nhau giữa nhiều lần gọi (guideline của project).
    # Anthropic: gửi thành khối system riêng có cache_control (các lô sau đọc từ cache, rẻ hơn).
    supports_context = True
    effort = ""
    last_usage: Optional[dict] = None    # {input_tokens, output_tokens} của lần gọi gần nhất (0.9.5: đếm lượt dùng)
    last_stop_reason: Optional[str] = None   # 0.9.12: end_turn | max_tokens | refusal ... (ghi nhật ký khi trả lời hỏng)

    def complete(self, system: str, text: str, images: Optional[list[bytes]] = None, max_tokens: int = 3000,
                 context: str = "") -> str:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"provider": self.provider, "model": self.model, "local": self.provider == "local"}


class AnthropicLLM(LLM):
    provider = "anthropic"

    def describe(self) -> dict:
        # 0.9.12: kèm effort để panel ghi "claude-opus-5-5 · effort medium"
        return dict(super().describe(), effort=self.effort)

    def __init__(self, api_key: str, model: str, timeout: int = 300, effort: str = ""):
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.effort = effort if effort in EFFORTS else ""
        self.max_output = MAX_OUTPUT

    def configure(self, model: Optional[str] = None, effort: Optional[str] = None) -> None:
        """0.9.12: đổi model / effort khi backend đang chạy (Cài đặt của app, áp dụng ngay, không cần bật lại)."""
        if model:
            self.model = model
            self.max_output = MAX_OUTPUT
        if effort is not None:
            self.effort = effort if effort in EFFORTS else ""

    def complete(self, system, text, images=None, max_tokens=3000, context=""):
        """max_tokens của nơi gọi bị bỏ qua (0.9.12): luôn dùng mức tối đa của model, để phần suy nghĩ của Claude không
        ăn hết chỗ và cắt cụt câu trả lời JSON (lỗi "trả lời không đúng định dạng JSON" ở 0.9.10 với Fable 5.2)."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(img).decode()}} for img in images or []]
        content.append({"type": "text", "text": text})
        # 0.9.11: guideline lặp lại ở mọi lô -> khối system riêng, đánh dấu cache (prompt caching). Khối ngắn hơn mức
        # tối thiểu của model thì API chỉ không cache, vẫn chạy bình thường.
        sys_field = ([{"type": "text", "text": system},
                      {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}] if context else system)
        effort = self.effort
        while True:
            body = {"model": self.model, "max_tokens": self.max_output, "system": sys_field,
                    "messages": [{"role": "user", "content": content}]}
            if effort:
                body["output_config"] = {"effort": effort}
            try:
                data = _post_stream("https://api.anthropic.com/v1/messages",
                                    {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}, body, self.timeout)
                break
            except LLMError as exc:
                msg = str(exc).lower()
                if "400" not in msg and "invalid_request" not in msg:
                    raise
                lower = [m for m in MAX_OUTPUT_FALLBACKS if m < self.max_output]
                if "max_tokens" in msg and lower:
                    log.warning("Model %s rejects max_tokens=%s, retrying with %s", self.model, self.max_output, lower[0])
                    self.max_output = lower[0]
                    continue
                if effort and ("effort" in msg or "output_config" in msg):
                    log.warning("Model %s rejects effort=%s, retrying with the model default", self.model, effort)
                    effort = ""
                    continue
                raise
        self.last_usage = data.get("usage") or None
        self.last_stop_reason = data.get("stop_reason")
        return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")


class OpenAICompatLLM(LLM):
    provider = "local"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 600):
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout
        if not re.search(r"//(127\.0\.0\.1|localhost|\[::1\])[:/]", self.base_url + "/"):
            self.provider = "openai-compatible"

    def complete(self, system, text, images=None, max_tokens=3000, context=""):
        if context:                      # máy chủ OpenAI-compatible: không có cache, ghép thẳng vào system
            system = f"{system}\n\n{context}"
        content = [{"type": "text", "text": text}]
        for img in images or []:
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}})
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post(self.base_url + "/chat/completions", headers,
                     {"model": self.model, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}, self.timeout)
        usage = data.get("usage") or {}
        self.last_usage = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
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
        effort = os.getenv("GUIDELINE_LLM_EFFORT")
        return AnthropicLLM(key, model or DEFAULT_MODEL, effort=DEFAULT_EFFORT if effort is None else effort)
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
