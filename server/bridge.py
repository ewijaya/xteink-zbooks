"""HTTP companion bridge for XTEINK X4 Pro Z-Books InkApp."""
from __future__ import annotations

import hmac
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .core import MAX_CHUNK_BYTES, ZBooksStore


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], store: ZBooksStore):
        self.store = store
        super().__init__(server_address, BridgeHandler)


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[zbooks-bridge] {self.address_string()} - {fmt % args}\n")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _check_auth(self) -> bool:
        expected = self.server.store.config.token
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            supplied = header[7:].strip()
            if hmac.compare_digest(supplied, expected):
                return True
        self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
        return False

    def _read_json_body(self) -> dict[str, Any]:
        length_hdr = self.headers.get("Content-Length", "0")
        try:
            length = int(length_hdr)
        except ValueError:
            raise ValueError("Invalid Content-Length")
        if length < 0 or length > 65536:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "zbooks-bridge",
                    "version": __version__,
                    "zlib_available": self.server.store.zlib_available(),
                },
            )
            return

        if not self._check_auth():
            return

        if path == "/api/status":
            zst = self.server.store.zlib_status()
            q = self.server.store.list_queue()
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "version": __version__,
                    "queue_count": len(q),
                    "zlib_available": zst.get("available", False),
                    "zlib_logged_in": zst.get("logged_in", False),
                    "zlib_error": zst.get("error", ""),
                    "quota": zst.get("quota", {}),
                },
            )
            return

        if path == "/api/queue":
            items = self.server.store.list_queue()
            self._send_json(HTTPStatus.OK, {"ok": True, "items": items})
            return

        if path == "/api/search":
            query = (qs.get("q") or [""])[0]
            ext = (qs.get("ext") or [self.server.store.config.default_ext])[0]
            try:
                page = int((qs.get("page") or ["1"])[0])
                count = int((qs.get("count") or ["8"])[0])
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid page or count")
                return
            try:
                res = self.server.store.search(query=query, ext=ext, page=page, count=count)
                self._send_json(HTTPStatus.OK, res)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_GATEWAY, str(exc))
            return

        if path.startswith("/api/file/"):
            dl_token = path[len("/api/file/"):]
            prep = self.server.store.get_prepared(dl_token)
            if not prep:
                self._send_error(HTTPStatus.NOT_FOUND, "Download token expired or not found")
                return
            try:
                size = prep.path.stat().st_size
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with prep.path.open("rb") as fh:
                    while True:
                        buf = fh.read(65536)
                        if not buf:
                            break
                        self.wfile.write(buf)
            except OSError as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return

        if path.startswith("/api/chunk/"):
            dl_token = path[len("/api/chunk/"):]
            prep = self.server.store.get_prepared(dl_token)
            if not prep:
                self._send_error(HTTPStatus.NOT_FOUND, "Download token expired or not found")
                return
            try:
                offset = max(0, int((qs.get("offset") or ["0"])[0]))
                length = int((qs.get("len") or [str(MAX_CHUNK_BYTES)])[0])
                length = max(1, min(length, MAX_CHUNK_BYTES))
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid offset or len")
                return
            try:
                total = prep.path.stat().st_size
                if offset >= total:
                    data = b""
                else:
                    with prep.path.open("rb") as fh:
                        fh.seek(offset)
                        data = fh.read(length)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Total-Size", str(total))
                self.send_header("X-Offset", str(offset))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if data:
                    self.wfile.write(data)
            except OSError as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if not self._check_auth():
            return

        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
            return

        if path == "/api/queue":
            try:
                if body.get("local_path"):
                    entry = self.server.store.add_queue_local(
                        body["local_path"],
                        title=str(body.get("title") or ""),
                        author=str(body.get("author") or ""),
                        filename=str(body.get("filename") or ""),
                    )
                else:
                    entry = self.server.store.add_queue_zlib(
                        book_id=str(body.get("id") or body.get("book_id") or ""),
                        title=str(body.get("title") or ""),
                        author=str(body.get("author") or ""),
                        year=str(body.get("year") or ""),
                        ext=str(body.get("ext") or "epub"),
                        size_bytes=int(body.get("size_bytes") or 0),
                        filename=str(body.get("filename") or ""),
                    )
                self._send_json(HTTPStatus.OK, {"ok": True, "item": entry})
            except (ValueError, FileNotFoundError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if path == "/api/queue/remove":
            qid = str(body.get("qid") or "").strip()
            if not qid:
                self._send_error(HTTPStatus.BAD_REQUEST, "Missing qid")
                return
            removed = self.server.store.remove_queue(qid)
            self._send_json(HTTPStatus.OK, {"ok": True, "removed": removed})
            return

        if path == "/api/prepare":
            book_id = str(body.get("id") or body.get("book_id") or "").strip()
            qid = str(body.get("qid") or "").strip()
            filename = str(body.get("filename") or "").strip()
            try:
                res = self.server.store.prepare_download(
                    book_id=book_id,
                    qid=qid,
                    preferred_filename=filename,
                )
                self._send_json(HTTPStatus.OK, res)
            except FileNotFoundError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, str(exc))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_GATEWAY, str(exc))
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Not found")


def run_server(store: ZBooksStore, host: str | None = None, port: int | None = None) -> None:
    bind_host = host or store.config.host
    bind_port = port or store.config.port
    srv = BridgeHTTPServer((bind_host, bind_port), store)
    print(f"Z-Books bridge v{__version__} listening on http://{bind_host}:{bind_port}", flush=True)
    print(f"Data dir: {store.data_dir}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down bridge...", flush=True)
    finally:
        srv.server_close()
