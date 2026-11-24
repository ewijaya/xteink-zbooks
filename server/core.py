"""Core state, filename sanitization, queue persistence, and zlib wrapper."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .epub_fix import ensure_x4_clean_epub

# X4 Pro API 2.18 `save = { to = "files" }` limit is 8,388,608 bytes (8 MB).
# Use a conservative threshold so anything near/over 8 MB uses chunked mode.
DIRECT_SAVE_MAX_BYTES = 7_500_000
MAX_CHUNK_BYTES = 114_688  # 112 KB (safely under 131,072-byte HTTP body cap)

# Section 29 (App files) forbidden names and characters on X4 Pro microSD.
_ILLEGAL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|~]')
_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_BOOK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def default_data_dir() -> Path:
    env = os.environ.get("ZBOOKS_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "zbooks-bridge"


def truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate a UTF-8 string cleanly on a code-point boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[:max_bytes]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""


def sanitize_x4_filename(name: str, fallback_stem: str = "book", default_ext: str = "epub") -> str:
    """Produce a valid X4 Pro Section 29 ctx.files filename (<= 120 UTF-8 bytes)."""
    raw = (name or "").strip()
    # Replace illegal characters and collapse whitespace
    cleaned = _ILLEGAL_CHARS_RE.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")

    stem, ext = os.path.splitext(cleaned)
    ext = ext.lstrip(".").lower()
    ext = re.sub(r"[^a-z0-9]", "", ext)
    if not ext:
        ext = re.sub(r"[^a-z0-9]", "", (default_ext or "epub").lower()) or "epub"

    stem = stem.strip(" .")
    if not stem:
        stem = fallback_stem
    if stem.upper() in _RESERVED_STEMS:
        stem = f"book_{stem}"

    suffix = f".{ext}"
    max_stem_bytes = max(1, 120 - len(suffix.encode("utf-8")))
    stem = truncate_utf8(stem, max_stem_bytes).strip(" .")
    if not stem:
        stem = "book"
    if stem.upper() in _RESERVED_STEMS:
        stem = f"b_{stem}"
    return f"{stem}{suffix}"


def format_size(size_bytes: int | None) -> str:
    if not size_bytes or size_bytes <= 0:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def parse_size_bytes(value: Any) -> int:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    if not isinstance(value, str):
        return 0
    s = value.strip().upper()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|K|M|G)$", s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2)
    mult = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024 * 1024,
        "MB": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }[unit]
    return int(num * mult)


def make_book_filename(title: str, author: str = "", ext: str = "epub", fallback: str = "book") -> str:
    t = (title or "").strip()
    a = (author or "").strip()
    if a and t:
        # Keep author short if multiple authors
        first_author = a.split(",")[0].split(";")[0].strip()
        if len(first_author) > 36:
            first_author = truncate_utf8(first_author, 36).strip()
        base = f"{t} - {first_author}"
    else:
        base = t or a or fallback
    return sanitize_x4_filename(f"{base}.{ext or 'epub'}", fallback_stem=fallback, default_ext=ext or "epub")


def _extract_book_id(item: dict[str, Any]) -> str:
    bid = item.get("id") or item.get("book_id") or ""
    bid = str(bid).strip()
    bhash = str(item.get("hash") or "").strip()
    if bid and ":" not in bid and bhash:
        return f"{bid}:{bhash}"
    return bid


def normalize_search_item(raw: dict[str, Any]) -> dict[str, Any]:
    bid = _extract_book_id(raw)
    title = truncate_utf8(str(raw.get("title") or raw.get("name") or "Untitled").strip(), 120)
    raw_author = raw.get("author")
    if not raw_author:
        raw_authors = raw.get("authors")
        if isinstance(raw_authors, list):
            raw_author = ", ".join(str(a).strip() for a in raw_authors if str(a).strip())
        else:
            raw_author = raw_authors or ""
    author = truncate_utf8(str(raw_author).strip(), 80)
    year = str(raw.get("year") or "").strip()
    if year == "0":
        year = ""
    ext = str(raw.get("extension") or raw.get("ext") or raw.get("format") or "epub").strip().lower()
    lang = truncate_utf8(str(raw.get("language") or raw.get("lang") or "").strip(), 24)

    size_bytes = parse_size_bytes(raw.get("filesize") or raw.get("size_bytes") or raw.get("size"))
    size_str = str(raw.get("filesizeString") or raw.get("filesize_string") or "").strip()
    if not size_str and size_bytes:
        size_str = format_size(size_bytes)
    elif size_str and not size_bytes:
        size_bytes = parse_size_bytes(size_str)

    fallback = f"book_{re.sub(r'[^A-Za-z0-9]', '', bid)[:12] or 'item'}"
    filename = make_book_filename(title, author, ext, fallback=fallback)

    return {
        "id": bid,
        "title": title,
        "author": author,
        "year": year,
        "ext": ext,
        "lang": lang,
        "size": size_str,
        "size_bytes": size_bytes,
        "filename": filename,
    }


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class BridgeConfig:
    token: str
    host: str = "127.0.0.1"
    port: int = 8742
    zlib_bin: str = "/usr/local/bin/zlib"
    default_ext: str = "epub"
    public_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeConfig":
        return cls(
            token=str(data.get("token") or ""),
            host=str(data.get("host") or "127.0.0.1"),
            port=int(data.get("port") or 8742),
            zlib_bin=str(data.get("zlib_bin") or shutil.which("zlib") or "/usr/local/bin/zlib"),
            default_ext=str(data.get("default_ext") or "epub"),
            public_url=str(data.get("public_url") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "host": self.host,
            "port": self.port,
            "zlib_bin": self.zlib_bin,
            "default_ext": self.default_ext,
            "public_url": self.public_url,
        }


@dataclass
class PreparedFile:
    dl_token: str
    path: Path
    filename: str
    size_bytes: int
    created_at: float = field(default_factory=time.time)


class ZBooksStore:
    """Manages config, queue, zlib invocations, and download cache."""

    def __init__(self, data_dir: Path | None = None, config: BridgeConfig | None = None):
        self.data_dir = (data_dir or default_data_dir()).expanduser()
        self.config_path = self.data_dir / "config.json"
        self.queue_path = self.data_dir / "queue.json"
        self.cache_dir = self.data_dir / "cache"
        self._lock = threading.RLock()
        self._prepared: dict[str, PreparedFile] = {}

        if config is not None:
            self.config = config
        else:
            self.config = self.load_or_create_config()

    def load_or_create_config(self) -> BridgeConfig:
        if self.config_path.exists():
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            cfg = BridgeConfig.from_dict(data)
            if not cfg.token:
                cfg.token = secrets.token_urlsafe(24)
                _atomic_write_json(self.config_path, cfg.to_dict())
            return cfg
        cfg = BridgeConfig(
            token=secrets.token_urlsafe(24),
            zlib_bin=shutil.which("zlib") or "/usr/local/bin/zlib",
        )
        _atomic_write_json(self.config_path, cfg.to_dict())
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        return cfg

    def save_config(self) -> None:
        _atomic_write_json(self.config_path, self.config.to_dict())
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass

    # ── Queue persistence ──────────────────────────────────────────────────

    def _read_queue_unlocked(self) -> list[dict[str, Any]]:
        if not self.queue_path.exists():
            return []
        try:
            data = json.loads(self.queue_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return []

    def list_queue(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self._read_queue_unlocked()
            out: list[dict[str, Any]] = []
            for it in items:
                # Keep wire payload compact and free of server filesystem paths
                row = {
                    "qid": it.get("qid", ""),
                    "kind": it.get("kind", "zlib"),
                    "id": it.get("book_id", ""),
                    "title": it.get("title", "Untitled"),
                    "author": it.get("author", ""),
                    "year": it.get("year", ""),
                    "ext": it.get("ext", "epub"),
                    "size": it.get("size") or format_size(it.get("size_bytes")),
                    "size_bytes": int(it.get("size_bytes") or 0),
                    "filename": it.get("filename", "book.epub"),
                    "added_at": it.get("added_at", ""),
                }
                out.append(row)
            return out

    def add_queue_zlib(
        self,
        book_id: str,
        title: str = "",
        author: str = "",
        year: str = "",
        ext: str = "epub",
        size_bytes: int = 0,
        filename: str = "",
    ) -> dict[str, Any]:
        book_id = book_id.strip()
        if not _BOOK_ID_RE.match(book_id):
            raise ValueError("Invalid book_id format (expected id:hash)")
        ext = (ext or "epub").strip().lower()
        clean_title = truncate_utf8((title or book_id).strip(), 120)
        clean_author = truncate_utf8((author or "").strip(), 80)
        clean_name = (
            sanitize_x4_filename(filename, default_ext=ext)
            if filename
            else make_book_filename(clean_title, clean_author, ext)
        )
        entry = {
            "qid": uuid.uuid4().hex[:10],
            "kind": "zlib",
            "book_id": book_id,
            "title": clean_title,
            "author": clean_author,
            "year": str(year or "").strip(),
            "ext": ext,
            "size": format_size(size_bytes),
            "size_bytes": int(size_bytes or 0),
            "filename": clean_name,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self._lock:
            items = self._read_queue_unlocked()
            # Deduplicate by book_id: replace existing entry at top
            items = [x for x in items if not (x.get("kind") == "zlib" and x.get("book_id") == book_id)]
            items.insert(0, entry)
            _atomic_write_json(self.queue_path, items)
        return entry

    def add_queue_local(
        self,
        local_path: str | Path,
        title: str = "",
        author: str = "",
        filename: str = "",
    ) -> dict[str, Any]:
        p = Path(local_path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")
        stat = p.stat()
        ext = p.suffix.lstrip(".").lower() or "epub"
        clean_title = truncate_utf8((title or p.stem).strip(), 120)
        clean_author = truncate_utf8((author or "").strip(), 80)
        clean_name = (
            sanitize_x4_filename(filename, default_ext=ext)
            if filename
            else sanitize_x4_filename(p.name, default_ext=ext)
        )
        entry = {
            "qid": uuid.uuid4().hex[:10],
            "kind": "local",
            "book_id": "",
            "local_path": str(p),
            "title": clean_title,
            "author": clean_author,
            "year": "",
            "ext": ext,
            "size": format_size(stat.st_size),
            "size_bytes": stat.st_size,
            "filename": clean_name,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self._lock:
            items = self._read_queue_unlocked()
            items = [x for x in items if not (x.get("kind") == "local" and x.get("local_path") == str(p))]
            items.insert(0, entry)
            _atomic_write_json(self.queue_path, items)
        return entry

    def remove_queue(self, qid: str) -> bool:
        with self._lock:
            items = self._read_queue_unlocked()
            new_items = [x for x in items if x.get("qid") != qid]
            if len(new_items) == len(items):
                return False
            _atomic_write_json(self.queue_path, new_items)
            return True

    def clear_queue(self) -> int:
        with self._lock:
            items = self._read_queue_unlocked()
            count = len(items)
            _atomic_write_json(self.queue_path, [])
            return count

    def get_queue_entry(self, qid: str) -> dict[str, Any] | None:
        with self._lock:
            for x in self._read_queue_unlocked():
                if x.get("qid") == qid:
                    return dict(x)
            return None

    # ── zlib CLI integration ───────────────────────────────────────────────

    def zlib_available(self) -> bool:
        b = self.config.zlib_bin
        return bool(b and (Path(b).is_file() or shutil.which(b)))

    def _run_zlib(self, args: list[str], timeout: int = 45) -> tuple[int, str, str]:
        if not self.zlib_available():
            raise RuntimeError(f"zlib binary not found: {self.config.zlib_bin}")
        proc = subprocess.run(
            [self.config.zlib_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def zlib_status(self) -> dict[str, Any]:
        if not self.zlib_available():
            return {"available": False, "logged_in": False, "error": "zlib binary not found"}
        try:
            rc, out, err = self._run_zlib(["profile", "--json"], timeout=15)
        except subprocess.TimeoutExpired:
            return {"available": True, "logged_in": False, "error": "zlib profile timed out"}
        if rc != 0:
            msg = (err or out or "not logged in").strip().splitlines()[-1] if (err or out).strip() else "not logged in"
            return {"available": True, "logged_in": False, "error": truncate_utf8(msg, 120)}
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return {"available": True, "logged_in": True, "quota": {}}
        # Extract common quota fields defensively
        quota: dict[str, Any] = {}
        if isinstance(data, dict):
            for k in ("downloads_today", "downloads_limit", "downloads_left", "daily_remaining", "daily_limit", "email", "name"):
                if k in data:
                    quota[k] = data[k]
            if isinstance(data.get("user"), dict):
                for k in ("downloads_today", "downloads_limit", "downloads_left", "email", "name"):
                    if k in data["user"] and k not in quota:
                        quota[k] = data["user"][k]
        return {"available": True, "logged_in": True, "quota": quota}

    def search(self, query: str, ext: str = "epub", page: int = 1, count: int = 8) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ValueError("Empty search query")
        page = max(1, min(int(page), 50))
        count = max(1, min(int(count), 15))
        ext = (ext or "").strip().lower()

        # Ask for count + 1 so we know whether another page exists.
        args = ["search", query, "--json", "-p", str(page), "-n", str(count)]
        if ext and ext != "all":
            args.extend(["--ext", ext])

        rc, out, err = self._run_zlib(args, timeout=35)
        if rc != 0:
            msg = (err or out or f"zlib search failed (exit {rc})").strip()
            raise RuntimeError(truncate_utf8(msg, 180))

        payload = json.loads(out) if out.strip() else []
        if isinstance(payload, list):
            raw_items = payload
            has_more = len(raw_items) >= count
        elif isinstance(payload, dict):
            raw_items = (
                payload.get("books")
                or payload.get("results")
                or payload.get("items")
                or []
            )
            total_pages = payload.get("total_pages") or payload.get("pages")
            if isinstance(total_pages, int):
                has_more = page < total_pages
            else:
                has_more = len(raw_items) >= count
        else:
            raw_items = []
            has_more = False

        items = [normalize_search_item(r) for r in raw_items if isinstance(r, dict)][:count]
        return {
            "ok": True,
            "query": query,
            "ext": ext or "all",
            "page": page,
            "has_more": bool(has_more),
            "items": items,
        }

    # ── Caching & preparing downloads ──────────────────────────────────────

    def _cache_slot(self, book_id: str) -> Path:
        digest = hashlib.sha256(book_id.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / digest

    def _find_cached_file(self, slot: Path) -> Path | None:
        if not slot.is_dir():
            return None
        files = [p for p in slot.iterdir() if p.is_file() and not p.name.startswith(".")]
        if not files:
            return None
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0]

    def ensure_zlib_downloaded(self, book_id: str) -> Path:
        book_id = book_id.strip()
        if not _BOOK_ID_RE.match(book_id):
            raise ValueError("Invalid book_id format")
        slot = self._cache_slot(book_id)
        cached = self._find_cached_file(slot)
        if cached is not None and cached.stat().st_size > 0:
            return cached

        slot.mkdir(parents=True, exist_ok=True)
        rc, out, err = self._run_zlib(["download", book_id, "-d", str(slot), "--json"], timeout=90)
        if rc != 0:
            msg = (err or out or f"zlib download failed (exit {rc})").strip()
            raise RuntimeError(truncate_utf8(msg, 180))

        # Parse JSON path if provided, otherwise inspect the slot directory
        found: Path | None = None
        if out.strip():
            try:
                meta = json.loads(out)
                if isinstance(meta, dict):
                    for key in ("path", "file", "file_path", "saved_path", "output"):
                        cand = meta.get(key)
                        if isinstance(cand, str) and cand.strip():
                            p = Path(cand.strip())
                            if not p.is_absolute():
                                p = slot / p
                            if p.is_file():
                                found = p
                                break
            except json.JSONDecodeError:
                pass
        if found is None:
            found = self._find_cached_file(slot)
        if found is None or found.stat().st_size == 0:
            raise RuntimeError("zlib reported success but no downloaded file was found")
        return found

    def _prune_prepared(self) -> None:
        now = time.time()
        expired = [k for k, v in self._prepared.items() if now - v.created_at > 3600]
        for k in expired:
            self._prepared.pop(k, None)

    def prepare_download(
        self,
        *,
        book_id: str = "",
        qid: str = "",
        preferred_filename: str = "",
    ) -> dict[str, Any]:
        file_path: Path
        target_name: str

        if qid:
            entry = self.get_queue_entry(qid)
            if not entry:
                raise FileNotFoundError(f"Queue item not found: {qid}")
            if entry.get("kind") == "local":
                file_path = Path(entry["local_path"])
                if not file_path.is_file():
                    raise FileNotFoundError(f"Queued local file missing: {file_path.name}")
                target_name = entry.get("filename") or sanitize_x4_filename(file_path.name)
            else:
                file_path = self.ensure_zlib_downloaded(entry["book_id"])
                ext = file_path.suffix.lstrip(".").lower() or entry.get("ext") or "epub"
                target_name = entry.get("filename") or sanitize_x4_filename(file_path.name, default_ext=ext)
                # Ensure extension matches the actual downloaded file
                target_name = sanitize_x4_filename(
                    f"{os.path.splitext(target_name)[0]}.{ext}", default_ext=ext
                )
        elif book_id:
            file_path = self.ensure_zlib_downloaded(book_id)
            ext = file_path.suffix.lstrip(".").lower() or "epub"
            if preferred_filename:
                base = os.path.splitext(preferred_filename)[0]
                target_name = sanitize_x4_filename(f"{base}.{ext}", default_ext=ext)
            else:
                target_name = sanitize_x4_filename(file_path.name, default_ext=ext)
        else:
            raise ValueError("Provide either book_id or qid")

        # Automatically flatten nested HTML tags inside EPUB 3 <nav> TOC links
        # so the XTEINK X4 Pro stock reader renders clean chapter titles.
        file_path = ensure_x4_clean_epub(file_path, self.cache_dir / "x4clean")

        size_bytes = file_path.stat().st_size
        dl_token = secrets.token_urlsafe(16)
        mode = "direct" if size_bytes <= DIRECT_SAVE_MAX_BYTES else "chunked"

        with self._lock:
            self._prune_prepared()
            self._prepared[dl_token] = PreparedFile(
                dl_token=dl_token,
                path=file_path,
                filename=target_name,
                size_bytes=size_bytes,
            )

        return {
            "ok": True,
            "token": dl_token,
            "filename": target_name,
            "size_bytes": size_bytes,
            "size": format_size(size_bytes),
            "mode": mode,
            "chunk_bytes": MAX_CHUNK_BYTES,
        }

    def get_prepared(self, dl_token: str) -> PreparedFile | None:
        with self._lock:
            self._prune_prepared()
            item = self._prepared.get(dl_token)
            if item and item.path.is_file():
                return item
            return None
