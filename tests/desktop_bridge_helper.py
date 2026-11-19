"""Dùng cho tests/desktop_shim_smoke.cjs: chạy Bridge thật (như trong app) qua HTTP để trang giả gọi vào.

  python tests/desktop_bridge_helper.py <cổng helper> <cổng backend> <api key> <thư mục tạm> <file boot>
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import __version__  # noqa: E402
from desktop.backend_proc import BackendProcess  # noqa: E402
from desktop.bridge import Bridge, Store  # noqa: E402
from desktop.components import Components  # noqa: E402
from desktop.paths import Paths  # noqa: E402

port, be_port, key, tmp, boot_file = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
paths = Paths(tmp / "data").ensure()
backend = BackendProcess(Components(paths, manifest={}))
backend.port, backend.key, backend.state = be_port, key, "running"
page = {"url": "https://cvat.edufun.ai/tasks/5/jobs/12?frame=3"}
store = Store(paths.store)
bridge = Bridge(store, backend, lambda: {"https://cvat.edufun.ai"}, lambda: page["url"])
app = paths.app
boot_file.write_text(bridge._boot_script((app / "desktop" / "shim.js").read_text(encoding="utf-8"),
                                         (app / "userscript" / "sam3-cvat-assist.user.js").read_text(encoding="utf-8"),
                                         __version__), encoding="utf-8")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if req["method"] == "_seturl":
            page["url"] = req["args"][0]
            out = True
        elif req["method"] == "_store":
            out = store.all()
        else:
            try:
                out = getattr(bridge, req["method"])(*req["args"])
            except PermissionError as exc:
                out = {"__error": str(exc)}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
