"""Dùng cho tests/desktop_control_smoke.cjs: ControlApi thật (bảng điều khiển) qua HTTP, máy giả lập đã cài 0.6.0."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop.components import Components  # noqa: E402
from desktop.control import ControlApi  # noqa: E402
from desktop.paths import Paths  # noqa: E402

port, tmp = int(sys.argv[1]), Path(sys.argv[2])
paths = Paths(tmp / "data").ensure()
paths.settings.write_text(json.dumps({"sites": [{"name": "CVAT edufun", "url": "https://cvat.edufun.ai"}],
                                      "license_accepted": "x"}), encoding="utf-8")       # đã dùng bản 0.6.0
(paths.packages / "torch").mkdir(parents=True)
(paths.packages / "torch" / "__init__.py").write_text("")
paths.sam3.mkdir(parents=True)
(paths.sam3 / "model.safetensors").write_bytes(b"w")
paths.state.write_text(json.dumps({"torch": {"variant": "cuda", "versions": {"torch": "2.13.0+cu126"}},
                                   "sam3": {"commit": "abc", "files": ["model.safetensors"]}}), encoding="utf-8")
manifest = {"torch": {"pin": "2.14.1", "cuda": [{"name": "torch", "version": "2.14.1+cu126", "filename": "t.whl", "url": "https://x/t.whl",
                                                 "sha256": "0" * 64, "size": 2_600_000_000}],
                      "cpu": [{"name": "torch", "version": "2.14.1", "filename": "c.whl", "url": "https://x/c.whl", "sha256": "0" * 64, "size": 200_000_000}]},
            "models": {"sam3": {"repo": "facebook/sam3", "gated": True, "approx_size": 3_450_000_000},
                       "vitpose": {"repo": "usyd-community/vitpose-base-simple", "approx_size": 360_000_000},
                       "face": {"url": "https://x/f.task", "sha256": "0" * 64, "size": 3_700_000}}}
comps = Components(paths, manifest, allow_http=("127.0.0.1",), hf_base="http://127.0.0.1:9", hf_caches=[], tool_roots=[])


class FakeBackend:
    state, base_url = "running", "http://127.0.0.1:8765"
    port, preferred_port, remote, share_key = 8765, 8765, None, ""
    HEALTH = {"ok": True, "engine": "sam3", "device": "cuda", "model_loaded": True, "idle_unload_min": 15,
              "gpu": {"name": "NVIDIA GeForce RTX 2060", "used_mb": 3100, "total_mb": 6144, "temp_c": 54, "util_pct": 12}}

    def __init__(self):
        self.extra_env = {}
        self.problem = None
        self.auto_restart = True

    def restart(self):
        return self.start()

    def info(self):
        msg = {"running": "Đang chạy: sam3 trên cuda", "stopped": "Đã tắt.", "error": "Backend dừng khi đang khởi động."}[self.state]
        return {"state": self.state, "message": msg, "url": self.base_url, "port": 8765,
                "health": self.HEALTH if self.state == "running" else {}, "problem": self.problem, "uptime": 120}

    def stop(self):
        self.state, self.problem = "stopped", None

    def start(self):
        self.state, self.problem = "running", None
        return self.info()

    def selftest(self):
        if self.state != "running":
            self.start()
        return {"ok": True, "found": 1, "seconds": 0.4, "engine": "sam3", "device": "cuda", "gpu": self.HEALTH["gpu"], "stage": "model"}

    def crash(self):
        """Giả lập máy thiếu Visual C++ (0xC0000135): bảng điều khiển phải hiện cách sửa."""
        from desktop.backend_proc import describe_exit, diagnose
        self.state = "error"
        self.problem = dict(diagnose("", 0xC0000135), exit=describe_exit(-1073741515))
        return self.info()

    def tail(self, n=40):
        return "log"


import datetime as _dt, hashlib as _hl
_today = _dt.date.today()
paths.store.mkdir(parents=True, exist_ok=True)
(paths.store / (_hl.sha1(b"stats").hexdigest() + ".json")).write_text(json.dumps({"key": "stats", "value": {
    _today.isoformat(): {"applied": 126, "rejected": 9, "shown": 140, "frames": [f"30:{i}" for i in range(48)], "saved": 2880,
                         "jobs": {"30": {"applied": 126, "rejected": 9, "shown": 140}}},
    (_today - _dt.timedelta(days=2)).isoformat(): {"applied": 60, "rejected": 2, "shown": 70, "frames": ["31:1"], "saved": 1200, "jobs": {}},
}}), encoding="utf-8")
fake_backend = FakeBackend()
api = ControlApi(paths, comps, fake_backend, open_site=lambda o: None, on_quit=lambda: None)
api._test_crash = fake_backend.crash
(paths.logs / "backend.log").write_text("\n===== 2026-11-20 12:00:56 starting backend, port 8765 =====\nLoaded 6 profiles\n"
                                        "[app] 12:00:58 Backend process exited with code -1073741515 (0xC0000135)\n", encoding="utf-8")
api._record_visit("https://cvat.edufun.ai/tasks/5/jobs/12?frame=3")
import desktop.support as _sup  # noqa: E402

_orig_report = _sup.report_bundle
_sup.report_bundle = lambda p, extra=None, out_dir=None, note="": _orig_report(p, extra, out_dir=tmp, note=note)
api._update_info = {"available": True, "auto": True, "version": "99.0.0", "notes": "## 99.0.0\n- **Sửa** lỗi `x`\n  dòng tiếp\n\n<!-- en -->\n## 99.0.0\n- **Fixed** bug `x`\n  next line",
                    "size": 136_000_000, "page": "https://github.com/11thanh2233/csam-assist/releases/tag/v99.0.0"}
api._settings.data["auto_update_check"] = False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            out = getattr(api, req["method"])(*req["args"])
        except Exception as exc:  # noqa: BLE001
            out = {"__error": f"{type(exc).__name__}: {exc}"}
        body = json.dumps(out, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
