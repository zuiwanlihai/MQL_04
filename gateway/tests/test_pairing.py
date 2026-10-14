"""Gateway pairing CLI tests (M17).

Pure-unit: env writing is idempotent; the HTTP call is exercised through a
local stdlib HTTP server (no mocks of the module under test beyond the
server itself).
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def test_write_env_key_creates_and_replaces(tmp_path):
    from app.pairing import write_env_key

    env = tmp_path / ".env"
    p = write_env_key("key-1", env)
    assert p == env and env.exists()
    assert "GATEWAY_API_KEY=key-1" in env.read_text(encoding="utf-8")

    # existing file with the key → replaced in place
    env.write_text("MT5_PATH=x\nGATEWAY_API_KEY=old\nGATEWAY_PORT=8090\n",
                   encoding="utf-8")
    write_env_key("key-2", env)
    text = env.read_text(encoding="utf-8")
    assert "GATEWAY_API_KEY=old" not in text
    assert "GATEWAY_API_KEY=key-2" in text
    assert "MT5_PATH=x" in text and "GATEWAY_PORT=8090" in text


def test_pair_against_local_backend(tmp_path):
    from app.pairing import pair

    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — stdlib API
            captured["path"] = self.path
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured["body"] = body
            resp = {
                "node_id": "00000000-0000-0000-0000-000000000001",
                "name": body["name"], "host": body["host"],
                "port": body["port"], "api_key": "paired-key",
                "backend_version": "0.1.0",
            }
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        out = pair(url, "ABCD-EFGH", "127.0.0.1", 8090, "CLI 网关")
        assert out["api_key"] == "paired-key"
        assert captured["path"] == "/api/v1/gateway/pair"
        assert captured["body"]["code"] == "ABCD-EFGH"
        assert captured["body"]["port"] == 8090
    finally:
        server.shutdown()
        server.server_close()


def test_pair_rejection_exits_2(capsys):
    from app.pairing import pair

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            # Drain the request body BEFORE replying.  Replying and closing
            # with the body still unread makes Windows send an RST instead of
            # a FIN, which discards the response already sitting in the
            # client's buffer → the client sees WinError 10053 instead of the
            # 404.  Measured: 15/40 runs aborted undrained, 0/40 drained.
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(404)
            body = json.dumps({"detail": "配对码无效或已过期"}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        try:
            pair(f"http://127.0.0.1:{server.server_port}", "XXXX-XXXX",
                 "127.0.0.1", 8090, "n")
            raised = False
        except SystemExit as exc:
            raised = exc.code == 2
        assert raised
        assert "配对码无效" in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()


def test_connection_drop_mid_request_exits_1(capsys):
    """A backend that drops the connection mid-request must report the friendly
    network error and exit 1 — not blow up with a raw ConnectionResetError.

    urllib wraps failures from ``send()`` in URLError but not from
    ``getresponse()``, so an RST during the response read escapes
    ``except URLError`` entirely.  Reproduced with SO_LINGER=0, which forces
    the RST that a backend restart (or Windows itself) produces.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0)
            )
            self.close_connection = True

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from app.pairing import pair

        try:
            pair(f"http://127.0.0.1:{server.server_port}", "XXXX-XXXX",
                 "127.0.0.1", 8090, "n")
            code = None
        except SystemExit as exc:
            code = exc.code
        assert code == 1
        assert "无法连接后端" in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()
