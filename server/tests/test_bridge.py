"""Unit and integration tests for Z-Books companion bridge server and CLI."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server.bridge import BridgeHTTPServer
from server.cli import main as cli_main
from server.core import (
    BridgeConfig,
    DIRECT_SAVE_MAX_BYTES,
    ZBooksStore,
    sanitize_x4_filename,
)
from server.epub_fix import ensure_x4_clean_epub
import zipfile


FAKE_ZLIB_SCRIPT = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
if not args:
    sys.exit(1)

cmd = args[0]
if cmd == "profile":
    print(json.dumps({"email": "reader@example.com", "downloads_left": 9, "downloads_limit": 10}))
    sys.exit(0)

if cmd == "search":
    q = args[1] if len(args) > 1 else ""
    if q == "fail":
        sys.stderr.write("mirror unreachable\\n")
        sys.exit(2)
    items = [
        {
            "id": "1001:deadbeef",
            "title": f"{q}: The Way of Books",
            "author": "Jane Reader",
            "year": 2024,
            "extension": "epub",
            "language": "English",
            "filesize": 123456,
            "filesizeString": "121 KB",
        },
        {
            "id": "2002:cafebabe",
            "title": "Big Illustrated Atlas",
            "author": "Cartographer",
            "year": 2023,
            "extension": "epub",
            "language": "English",
            "filesize": 9000000,
            "filesizeString": "8.6 MB",
        },
    ]
    print(json.dumps(items))
    sys.exit(0)

if cmd == "download":
    bid = args[1]
    out_dir = Path(".")
    if "-d" in args:
        out_dir = Path(args[args.index("-d") + 1])
    out_dir.mkdir(parents=True, exist_ok=True)
    # Count invocations to verify caching
    counter_file = out_dir.parent / "download_calls.txt"
    prev = int(counter_file.read_text()) if counter_file.exists() else 0
    counter_file.write_text(str(prev + 1))

    if bid == "2002:cafebabe":
        target = out_dir / "big.epub"
        # > DIRECT_SAVE_MAX_BYTES to exercise chunked mode
        target.write_bytes(b"PK\\x03\\x04" + (b"Z" * 7_600_000))
    else:
        target = out_dir / "sample.epub"
        target.write_bytes(b"PK\\x03\\x04HELLO_EPUB_BYTES_1234567890")
    print(json.dumps({"path": str(target)}))
    sys.exit(0)

sys.exit(1)
"""


class FilenameSanitizationTests(unittest.TestCase):
    def test_removes_illegal_chars_and_reserved_names(self) -> None:
        self.assertEqual(sanitize_x4_filename('A/B:C*?"<>|~D.epub'), "A B C D.epub")
        self.assertEqual(sanitize_x4_filename("CON.epub"), "book_CON.epub")
        self.assertEqual(sanitize_x4_filename("...leading.epub"), "leading.epub")

    def test_utf8_byte_budget(self) -> None:
        long_jp = "日本語のとても長い本のタイトル" * 10 + ".epub"
        res = sanitize_x4_filename(long_jp)
        self.assertLessEqual(len(res.encode("utf-8")), 120)
        self.assertTrue(res.endswith(".epub"))

    def test_epub_nav_toc_flattening(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "stripe.epub"
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                zf.writestr(
                    "META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                )
                zf.writestr(
                    "OEBPS/content.opf",
                    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
                    '<item id="toc" href="Text/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                    '<item id="c1" href="Text/Ch1.xhtml" media-type="application/xhtml+xml"/>'
                    "</manifest></package>",
                )
                zf.writestr(
                    "OEBPS/Text/toc.xhtml",
                    '<html><body><nav epub:type="toc"><ol>'
                    '<li><a href="../Text/Foreword.xhtml"><i class="Italic">Foreword</i></a></li>'
                    '<li><a href="../Text/Ch1.xhtml"><span class="TOCNum">1</span> Orientation &amp; Style</a></li>'
                    "</ol></nav></body></html>",
                )
                zf.writestr("OEBPS/Text/Ch1.xhtml", "<html><body><p><i>Keep</i> chapter body intact.</p></body></html>")

            cleaned = ensure_x4_clean_epub(src, root / "cache")
            self.assertNotEqual(cleaned, src)
            with zipfile.ZipFile(cleaned, "r") as zf:
                infos = zf.infolist()
                self.assertEqual(infos[0].filename, "mimetype")
                self.assertEqual(infos[0].compress_type, zipfile.ZIP_STORED)
                toc = zf.read("OEBPS/Text/toc.xhtml").decode("utf-8")
                self.assertIn('<a href="../Text/Foreword.xhtml">Foreword</a>', toc)
                self.assertIn('<a href="../Text/Ch1.xhtml">1 Orientation &amp; Style</a>', toc)
                self.assertNotIn("TOCNum", toc)
                # Body chapter untouched
                self.assertIn("<i>Keep</i>", zf.read("OEBPS/Text/Ch1.xhtml").decode("utf-8"))


class BridgeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fake_zlib = self.root / "fake_zlib"
        self.fake_zlib.write_text(FAKE_ZLIB_SCRIPT, encoding="utf-8")
        self.fake_zlib.chmod(self.fake_zlib.stat().st_mode | stat.S_IEXEC)

        self.data_dir = self.root / "data"
        self.token = "secret-test-token-12345"
        self.cfg = BridgeConfig(
            token=self.token,
            host="127.0.0.1",
            port=0,
            zlib_bin=str(self.fake_zlib),
        )
        self.store = ZBooksStore(data_dir=self.data_dir, config=self.cfg)
        self.store.save_config()

        self.server = BridgeHTTPServer(("127.0.0.1", 0), self.store)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def _req(self, path: str, method: str = "GET", body: dict | None = None, auth: bool = True) -> tuple[int, bytes, dict]:
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def test_health_and_auth(self) -> None:
        st, raw, _ = self._req("/health", auth=False)
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["zlib_available"])

        st, _, _ = self._req("/api/status", auth=False)
        self.assertEqual(st, 401)

        st, raw, _ = self._req("/api/status", auth=True)
        self.assertEqual(st, 200)
        body = json.loads(raw)
        self.assertTrue(body["zlib_logged_in"])
        self.assertEqual(body["quota"]["downloads_left"], 9)

    def test_search_and_direct_download_with_cache(self) -> None:
        st, raw, _ = self._req("/api/search?q=Stoic&ext=epub&page=1")
        self.assertEqual(st, 200)
        res = json.loads(raw)
        self.assertEqual(len(res["items"]), 2)
        first = res["items"][0]
        self.assertEqual(first["id"], "1001:deadbeef")
        self.assertTrue(first["filename"].endswith(".epub"))

        # Prepare + direct download
        st, raw, _ = self._req("/api/prepare", method="POST", body={"id": first["id"], "filename": first["filename"]})
        self.assertEqual(st, 200)
        prep = json.loads(raw)
        self.assertEqual(prep["mode"], "direct")
        self.assertEqual(prep["filename"], first["filename"])

        st, content, _ = self._req(f"/api/file/{prep['token']}")
        self.assertEqual(st, 200)
        self.assertEqual(content, b"PK\x03\x04HELLO_EPUB_BYTES_1234567890")

        # Second prepare hits cache without calling zlib download again
        st, _, _ = self._req("/api/prepare", method="POST", body={"id": first["id"]})
        self.assertEqual(st, 200)
        calls = int((self.store.cache_dir / "download_calls.txt").read_text())
        self.assertEqual(calls, 1)

    def test_large_book_chunked_download(self) -> None:
        st, raw, _ = self._req("/api/prepare", method="POST", body={"id": "2002:cafebabe", "filename": "Atlas.epub"})
        self.assertEqual(st, 200)
        prep = json.loads(raw)
        self.assertEqual(prep["mode"], "chunked")
        self.assertGreater(prep["size_bytes"], DIRECT_SAVE_MAX_BYTES)

        # Fetch first two chunks and final tail chunk
        st, c0, hdr0 = self._req(f"/api/chunk/{prep['token']}?offset=0&len=1024")
        self.assertEqual(st, 200)
        self.assertEqual(len(c0), 1024)
        self.assertTrue(c0.startswith(b"PK\x03\x04"))
        self.assertEqual(int(hdr0["X-Total-Size"]), prep["size_bytes"])

        tail_off = prep["size_bytes"] - 500
        st, tail, _ = self._req(f"/api/chunk/{prep['token']}?offset={tail_off}&len=1024")
        self.assertEqual(st, 200)
        self.assertEqual(len(tail), 500)

    def test_queue_lifecycle_and_local_epub(self) -> None:
        local_book = self.root / "My Notes Book.epub"
        local_book.write_bytes(b"LOCAL_EPUB_CONTENT_XYZ")

        # Queue local file via CLI
        rc = cli_main(["--data-dir", str(self.data_dir), "queue", "add-file", str(local_book), "--author", "Me"])
        self.assertEqual(rc, 0)

        # Queue zlib book via HTTP
        st, raw, _ = self._req(
            "/api/queue",
            method="POST",
            body={"id": "1001:deadbeef", "title": "Meditations", "author": "Marcus Aurelius", "ext": "epub"},
        )
        self.assertEqual(st, 200)
        q_zlib = json.loads(raw)["item"]

        st, raw, _ = self._req("/api/queue")
        self.assertEqual(st, 200)
        items = json.loads(raw)["items"]
        self.assertEqual(len(items), 2)

        # Download the queued local book via /api/prepare
        local_item = [x for x in items if x["kind"] == "local"][0]
        st, raw, _ = self._req("/api/prepare", method="POST", body={"qid": local_item["qid"]})
        self.assertEqual(st, 200)
        prep = json.loads(raw)
        st, blob, _ = self._req(f"/api/file/{prep['token']}")
        self.assertEqual(st, 200)
        self.assertEqual(blob, b"LOCAL_EPUB_CONTENT_XYZ")

        # Remove queued zlib book
        st, raw, _ = self._req("/api/queue/remove", method="POST", body={"qid": q_zlib["qid"]})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["removed"])


if __name__ == "__main__":
    unittest.main()
