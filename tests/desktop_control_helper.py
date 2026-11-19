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

    def info(self):
        return {"state": self.state, "message": "Đang chạy: mock trên cpu", "url": self.base_url, "port": 8765, "health": {}}

    def stop(self):
        self.state = "stopped"

    def start(self):
        self.state = "running"
        return self.info()

    def tail(self, n=40):
        return "log"


api = ControlApi(paths, comps, FakeBackend(), open_site=lambda o: None, on_quit=lambda: None)
api._update_info = {"available": True, "auto": True, "version": "0.7.1", "notes": "## 0.7.1\n- **Sửa** lỗi `x`\n  dòng tiếp",
                    "size": 136_000_000, "page": "https://github.com/11thanh2233/csam-assist/releases/tag/v0.7.1"}
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
