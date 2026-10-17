"""DNS-rebinding defense: local UIs must reject non-loopback Host headers."""

from __future__ import annotations

import http.client
import threading
from http.server import HTTPServer

import pytest

from banto.register_popup import _RegisterHandler
from banto.sync.web import SyncUIHandler


def _serve(base_cls):
    server = HTTPServer(("127.0.0.1", 0), base_cls)
    port = server.server_address[1]
    handler = type("H", (base_cls,), {"csrf_token": "tok", "server_port": port, "config": None})
    server.RequestHandlerClass = handler
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


def _get(port: int, host: str, path: str = "/api/csrf-token") -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("GET", path, skip_host=True)
    conn.putheader("Host", host)
    conn.endheaders()
    status = conn.getresponse().status
    conn.close()
    return status


@pytest.mark.parametrize("base_cls", [SyncUIHandler, _RegisterHandler])
def test_rebinding_host_rejected(base_cls) -> None:
    server, port = _serve(base_cls)
    try:
        assert _get(port, f"attacker.example:{port}") == 403
        assert _get(port, "attacker.example") == 403
        assert _get(port, f"127.0.0.1:{port}") == 200
        assert _get(port, f"localhost:{port}") == 200
    finally:
        server.shutdown()
        server.server_close()
