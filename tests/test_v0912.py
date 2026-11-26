"""0.9.12: Claude ở job 3D không còn báo "trả lời không đúng định dạng JSON".

Nguyên nhân ở 0.9.10: tool gọi Claude với max_tokens=4000; model luôn suy nghĩ (Fable 5.2, effort mặc định high) dùng
hết chỗ cho phần suy nghĩ, câu trả lời JSON bị cắt cụt. Sửa:
- mặc định Claude Opus 5.5, effort medium; đổi model / effort trong Cài đặt của app, áp dụng ngay (không bật lại backend);
- không giới hạn token ở tool: max_tokens = mức tối đa của model (128K), gọi kiểu streaming để không hết giờ HTTP;
- Claude trả lời hỏng: ghi nhật ký (model, effort, stop_reason, token, đầu câu trả lời), thử lại một lần;
- panel ghi tên model + effort.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import backend.guideline.llm as L
import backend.judge_3d as J3

ROOT = Path(__file__).resolve().parents[1]


def _sse(*events):
    out = []
    for e in events:
        out.append(f"event: {e['type']}".encode())
        out.append(("data: " + json.dumps(e)).encode())
        out.append(b"")
    return out


# ------------------------------------------------------------ gọi Claude ---
def test_read_sse_keeps_text_skips_thinking_and_reads_stop_reason():
    lines = _sse({"type": "message_start", "message": {"usage": {"input_tokens": 1200}}},
                 {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
                 {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm..."}},
                 {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": '{"tracks": '}},
                 {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "[]}"}},
                 {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                  "usage": {"output_tokens": 900, "output_tokens_details": {"thinking_tokens": 850}}},
                 {"type": "message_stop"})
    msg = L.read_sse(lines)
    assert msg["content"][0]["text"] == '{"tracks": []}', "chỉ lấy chữ, bỏ phần suy nghĩ"
    assert msg["stop_reason"] == "end_turn"
    assert msg["usage"]["input_tokens"] == 1200 and msg["usage"]["output_tokens_details"]["thinking_tokens"] == 850


def test_read_sse_error_event_raises():
    with pytest.raises(L.LLMError, match="overloaded"):
        L.read_sse(_sse({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))


def _capture(monkeypatch, fail_first=None):
    sent = []

    def fake(url, headers, body, timeout):
        sent.append(body)
        if fail_first and len(sent) == 1:
            raise L.LLMError(fail_first)
        return {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn", "usage": {"output_tokens": 5}}

    monkeypatch.setattr(L, "_post_stream", fake)
    return sent


def test_anthropic_uses_max_output_and_effort_not_caller_limit(monkeypatch):
    sent = _capture(monkeypatch)
    a = L.AnthropicLLM("k", "claude-opus-5-5", effort="medium")
    a.complete("SYS", "hi", None, max_tokens=4000)
    assert sent[0]["max_tokens"] == L.MAX_OUTPUT == 128000, "không còn giới hạn 4000 của nơi gọi"
    assert sent[0]["output_config"] == {"effort": "medium"} and sent[0]["model"] == "claude-opus-5-5"
    assert a.last_stop_reason == "end_turn"
    b = L.AnthropicLLM("k", "m", effort="")
    b.complete("SYS", "hi")
    assert "output_config" not in sent[1], "effort trống: để mặc định của model"


def test_anthropic_lowers_max_tokens_for_older_models(monkeypatch):
    sent = _capture(monkeypatch, fail_first='LLM trả lỗi 400: {"type":"invalid_request_error","message":"max_tokens: 128000 > 64000"}')
    a = L.AnthropicLLM("k", "claude-old", effort="medium")
    a.complete("SYS", "hi")
    assert [s["max_tokens"] for s in sent] == [128000, 64000] and a.max_output == 64000, "nhớ mức hợp lệ cho lần sau"


def test_anthropic_drops_effort_when_model_rejects_it(monkeypatch):
    sent = _capture(monkeypatch, fail_first='LLM trả lỗi 400: {"type":"invalid_request_error","message":"output_config.effort: not supported"}')
    a = L.AnthropicLLM("k", "claude-old", effort="high")
    a.complete("SYS", "hi")
    assert "output_config" in sent[0] and "output_config" not in sent[1]


def test_other_api_errors_are_not_retried(monkeypatch):
    sent = _capture(monkeypatch, fail_first="LLM trả lỗi 401: invalid x-api-key")
    with pytest.raises(L.LLMError, match="401"):
        L.AnthropicLLM("k", "m", effort="medium").complete("SYS", "hi")
    assert len(sent) == 1


def test_from_env_defaults_to_opus_medium(monkeypatch):
    monkeypatch.setenv("GUIDELINE_LLM", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.delenv("GUIDELINE_LLM_MODEL", raising=False)
    monkeypatch.delenv("GUIDELINE_LLM_EFFORT", raising=False)
    got = L.from_env()
    assert got.model == "claude-opus-5-5" and got.effort == "medium" and got.describe()["effort"] == "medium"
    monkeypatch.setenv("GUIDELINE_LLM_EFFORT", "")
    assert L.from_env().effort == "", "chọn 'Mặc định của model'"


def test_configure_changes_model_and_effort_live():
    a = L.AnthropicLLM("k", "claude-fable-5-2", effort="")
    a.max_output = 64000
    a.configure("claude-opus-5-5", "medium")
    assert a.model == "claude-opus-5-5" and a.effort == "medium" and a.max_output == L.MAX_OUTPUT
    a.configure(None, "bừa")
    assert a.effort == "" and a.model == "claude-opus-5-5"


# ------------------------------------------- trả lời hỏng: log + thử lại ---
class SeqLLM:
    model, effort = "claude-opus-5-5", "medium"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.last_usage = {"input_tokens": 3000, "output_tokens": 4000, "output_tokens_details": {"thinking_tokens": 3990}}
        self.last_stop_reason = "max_tokens"

    def complete(self, system, text, images=None, max_tokens=3000, context=""):
        self.calls += 1
        return self.replies.pop(0)


def test_bad_json_is_logged_then_retried_once(caplog):
    llm = SeqLLM(['{"tracks": [{"id": 1, "ke', '{"tracks": [{"id": 1, "keep": true}]}'])
    with caplog.at_level(logging.WARNING, logger="sam3-assist"):
        out = J3.ask_llm(llm, "x", [])
    assert out["tracks"][0]["keep"] is True and llm.calls == 2
    rec = "\n".join(r.getMessage() for r in caplog.records)
    for s in ("LLM bad reply (3D)", "model=claude-opus-5-5", "effort=medium", "stop_reason=max_tokens",
              "thinking=3990", '{"tracks": [{"id": 1, "ke'):
        assert s in rec, s


def test_bad_json_twice_reports_stop_reason():
    llm = SeqLLM(["không phải JSON", "vẫn không"])
    with pytest.raises(L.LLMError, match="stop_reason=max_tokens"):
        J3.ask_llm(llm, "x", [])
    assert llm.calls == 2, "chỉ thử lại một lần"


def test_log_never_contains_api_key(caplog):
    a = L.AnthropicLLM("sk-ant-SECRET", "claude-opus-5-5", effort="medium")
    with caplog.at_level(logging.WARNING, logger="sam3-assist"):
        L.log_bad_reply(a, "rác", "3D", "JSON")
    assert "SECRET" not in caplog.text and "claude-opus-5-5" in caplog.text


def test_judge_2d_logs_bad_reply(caplog):
    from backend.judge import Judge

    class U:
        limit = 0

        def allow(self):
            return True

        def add(self, usage):
            pass

    j = Judge(SeqLLM(["không phải JSON"]), U())
    with caplog.at_level(logging.WARNING, logger="sam3-assist"), pytest.raises(L.LLMError):
        j.ask("x", [])
    assert "LLM bad reply (2D)" in caplog.text


# ------------------------------------------------- đổi model / effort ngay ---
def test_llm_config_endpoint_changes_live(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.app as A

    with TestClient(A.app, base_url="http://127.0.0.1:8765") as c:
        a = L.AnthropicLLM("k", "claude-fable-5-2", effort="")
        monkeypatch.setattr(A.rt.guide, "llm", a)
        r = c.post("/llm/config", json={"model": "claude-opus-5-5", "effort": "medium"})
        assert r.status_code == 200 and r.json() == {"model": "claude-opus-5-5", "effort": "medium"}
        assert a.model == "claude-opus-5-5" and a.effort == "medium"
        assert c.get("/health").json()["guideline_llm"]["effort"] == "medium", "panel đọc effort qua /health"
        assert c.post("/llm/config", json={"effort": "rất cao"}).status_code == 400
        assert c.post("/llm/config", json={"model": "x; rm -rf"}).status_code == 400
        monkeypatch.setattr(A.rt.guide, "llm", None)
        assert c.post("/llm/config", json={"effort": "low"}).status_code == 400


class _Be:
    state = "running"

    def __init__(self, code=200):
        self.extra_env, self.auto_restart, self.calls, self.code = {}, True, [], code

    def request(self, method, path, body, timeout=300):
        self.calls.append((method, path, json.loads(body)))
        return self.code, "{}"


@pytest.fixture()
def keyring_mem(monkeypatch):
    from desktop import credstore

    box = {}
    monkeypatch.setattr(credstore, "available", lambda: True)
    monkeypatch.setattr(credstore, "get", lambda user=credstore.USER: box.get(user, ""))
    monkeypatch.setattr(credstore, "set", lambda token, user=credstore.USER: box.__setitem__(user, token))
    monkeypatch.setattr(credstore, "delete", lambda user=credstore.USER: box.pop(user, None))
    return box


def _api(tmp_path, be):
    from desktop.components import Components
    from desktop.control import ControlApi
    from desktop.paths import Paths

    paths = Paths(tmp_path / "data", app_dir=ROOT).ensure()
    return ControlApi(paths, Components(paths, manifest={}), be, open_site=lambda u: None, on_quit=lambda: None)


def test_app_changes_model_and_effort_without_restart(tmp_path, keyring_mem):
    be = _Be()
    api = _api(tmp_path, be)
    first = api.set_llm("anthropic", "claude-fable-5-2", "", "sk-ant-x")
    assert first["ok"] and first["restart"], "bật Claude lần đầu: cần bật lại backend"
    st = api.llm_settings()
    assert st["effort"] == "medium" and st["default_model"] == "claude-opus-5-5" and "claude-opus-5-5" in st["models"]
    r = api.set_llm("anthropic", "claude-opus-5-5", "", None, None, "high")
    assert r["ok"] and r["live"] and not r["restart"] and "claude-opus-5-5" in r["message"]
    assert be.calls[-1] == ("POST", "/llm/config", {"model": "claude-opus-5-5", "effort": "high"})
    assert be.extra_env["GUIDELINE_LLM_EFFORT"] == "high", "lần bật lại backend sau vẫn giữ"
    assert api.llm_settings()["effort"] == "high"
    assert not api.set_llm("anthropic", "claude-opus-5-5", "", None, None, "siêu")["ok"]
    r2 = api.set_llm("anthropic", "claude-opus-5-5", "", None, "50")
    assert r2["restart"] and not r2.get("live"), "đổi giới hạn mỗi ngày: cần bật lại"
    be.code = 500
    r3 = api.set_llm("anthropic", "claude-sonnet-5-5", "", None)
    assert r3["ok"] and r3["restart"], "backend không nhận được: nhắc bật lại"


def test_ui_has_model_and_effort_controls():
    html = (ROOT / "desktop" / "ui" / "control.html").read_text(encoding="utf-8")
    assert 'id="llmEffort"' in html and 'list="llmModels"' in html and "effort);" in html
    js = (ROOT / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8")
    assert "function llmModelText" in js and "Claude AI: bật · ${llmModelText()}" in js
