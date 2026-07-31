"""BridgeProvider: submit/poll/cancel against the local UDS API.

Phase 0: client works against the daemon; daemon fails closed because sends
are disabled. Used by delegate_task wiring (Phase 3+).
"""

from __future__ import annotations

import json
import socket
import time
import uuid

from ..protocol.canonicalization import canonical_bytes, sha256_hex
from ..protocol.signing import build_hmac, new_nonce


class BridgeClient:
    def __init__(self, sock_path: str, hmac_secret: bytes, *, connect_timeout: float = 1.0):
        self.sock_path = sock_path
        self.hmac_secret = hmac_secret
        self.connect_timeout = connect_timeout

    def _raw_request(self, method: str, path: str, body: dict | None = None,
                     idempotency_key: str = "") -> tuple[int, dict]:
        raw = canonical_bytes(body) if body is not None else b""
        timestamp = str(int(time.time() * 1000))
        nonce = new_nonce()
        content_sha = sha256_hex(raw)
        sig = build_hmac(self.hmac_secret, method, path, timestamp, nonce, content_sha, idempotency_key)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Bridge-Protocol": "1",
            "X-Request-ID": str(uuid.uuid4()),
            "Idempotency-Key": idempotency_key,
            "X-Hermes-Session-ID": "",
            "X-Bridge-Timestamp": timestamp,
            "X-Bridge-Nonce": nonce,
            "X-Content-SHA256": content_sha,
            "Authorization": f"HMAC-SHA256 {sig}",
            "Content-Length": str(len(raw)) if raw else "0",
        }
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.connect_timeout)
        s.connect(self.sock_path)
        try:
            head = f"{method} {path} HTTP/1.1\r\n"
            head += "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            head += "Host: bridge\r\nConnection: close\r\n\r\n"
            s.sendall(head.encode("utf-8") + raw)
            chunks = []
            while True:
                part = s.recv(65536)
                if not part:
                    break
                chunks.append(part)
        finally:
            s.close()
        data = b"".join(chunks)
        head, _, rest = data.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n")[0].decode("utf-8", "replace")
        code = int(status_line.split(" ")[1])
        payload = json.loads(rest.decode("utf-8", "replace")) if rest.strip() else {}
        return code, payload

    def health(self) -> tuple[int, dict]:
        return self._raw_request("GET", "/v1/health")

    def submit(self, request: dict) -> tuple[int, dict]:
        return self._raw_request("POST", "/v1/delegations", request, request["idempotency_key"])

    def poll(self, request_id: str) -> tuple[int, dict]:
        return self._raw_request("GET", f"/v1/delegations/{request_id}")

    def cancel(self, request_id: str) -> tuple[int, dict]:
        return self._raw_request("POST", f"/v1/delegations/{request_id}/cancel")
