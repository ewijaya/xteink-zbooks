"""Lossless EPUB TOC sanitizer for XTEINK X4 Pro stock reader compatibility.

The XTEINK X4 Pro stock EPUB reader parses EPUB 3 <nav> TOC <a> links up to the
first closing tag `</`, causing inline formatting tags like
`<a href="Ch1.xhtml"><span class="TOCNum">1</span> Orientation</a>`
to display as `<span class="TOCNum">1` in the Chapters menu.

This module flattens nested HTML tags inside <nav> TOC <a> elements to clean
plain text while preserving every other file, chapter, image, font, and the
uncompressed first `mimetype` entry byte-for-byte.
"""
from __future__ import annotations

import html
import os
import posixpath
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_NAV_BLOCK_RE = re.compile(r"(<nav\b[^>]*>)(.*?)(</nav>)", re.I | re.S)
_ANCHOR_RE = re.compile(r"(<a\b[^>]*>)(.*?)(</a>)", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _resolve_zip_path(opf_path: str, href: str) -> str:
    base = posixpath.dirname(opf_path)
    href_clean = href.split("#", 1)[0]
    joined = posixpath.normpath(posixpath.join(base, href_clean)) if base else posixpath.normpath(href_clean)
    return joined.lstrip("/")


def flatten_nav_anchors(xhtml: str) -> tuple[str, int]:
    """Flatten nested tags inside <a ...>...</a> within <nav>...</nav> blocks."""
    changes = 0

    def clean_anchor(m: re.Match[str]) -> str:
        nonlocal changes
        open_tag, inner, close_tag = m.group(1), m.group(2), m.group(3)
        if "<" not in inner:
            return m.group(0)
        # Strip inline tags, decode entities, collapse whitespace, re-escape XML
        plain = _TAG_RE.sub("", inner)
        plain = html.unescape(plain)
        plain = _WS_RE.sub(" ", plain).strip()
        escaped = html.escape(plain, quote=False)
        changes += 1
        return f"{open_tag}{escaped}{close_tag}"

    def clean_nav(m: re.Match[str]) -> str:
        open_nav, body, close_nav = m.group(1), m.group(2), m.group(3)
        return open_nav + _ANCHOR_RE.sub(clean_anchor, body) + close_nav

    updated = _NAV_BLOCK_RE.sub(clean_nav, xhtml)
    return updated, changes


def _find_nav_paths(zf: zipfile.ZipFile) -> list[str]:
    names = set(zf.namelist())
    if "META-INF/container.xml" not in names:
        return []
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
    except ET.ParseError:
        return []
    opf_path = ""
    for el in container.iter():
        if _local(el.tag) == "rootfile" and el.attrib.get("full-path"):
            opf_path = el.attrib["full-path"]
            break
    if not opf_path or opf_path not in names:
        return []
    try:
        opf = ET.fromstring(zf.read(opf_path))
    except ET.ParseError:
        return []

    nav_paths: list[str] = []
    for el in opf.iter():
        if _local(el.tag) == "item":
            props = (el.attrib.get("properties") or "").split()
            if "nav" in props and el.attrib.get("href"):
                zp = _resolve_zip_path(opf_path, el.attrib["href"])
                if zp in names and zp not in nav_paths:
                    nav_paths.append(zp)
    return nav_paths


def ensure_x4_clean_epub(src_path: Path, cache_dir: Path) -> Path:
    """Return a path to an X4-safe EPUB (original if already clean, else cached copy)."""
    if src_path.suffix.lower() != ".epub" or not src_path.is_file():
        return src_path

    try:
        zf = zipfile.ZipFile(src_path, "r")
    except zipfile.BadZipFile:
        return src_path

    replacements: dict[str, bytes] = {}
    with zf:
        nav_paths = _find_nav_paths(zf)
        for zp in nav_paths:
            raw = zf.read(zp)
            text = raw.decode("utf-8", errors="replace")
            fixed, count = flatten_nav_anchors(text)
            if count > 0:
                replacements[zp] = fixed.encode("utf-8")

        if not replacements:
            return src_path

        # Deterministic cached output keyed by source file stat
        st = src_path.stat()
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{src_path.stem}.{st.st_size}.{int(st.st_mtime)}.x4.epub"
        out_path = cache_dir / out_name
        if out_path.is_file() and out_path.stat().st_size > 0:
            return out_path

        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_x4_", suffix=".epub", dir=str(cache_dir))
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp_name, "w") as out_zf:
                infos = zf.infolist()
                # EPUB spec requires `mimetype` first and uncompressed
                ordered = sorted(infos, key=lambda info: (0 if info.filename == "mimetype" else 1))
                for info in ordered:
                    data = replacements.get(info.filename, zf.read(info.filename))
                    if info.filename == "mimetype":
                        zi = zipfile.ZipInfo("mimetype")
                        zi.compress_type = zipfile.ZIP_STORED
                        out_zf.writestr(zi, data)
                    else:
                        zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                        zi.compress_type = info.compress_type
                        zi.external_attr = info.external_attr
                        out_zf.writestr(zi, data)
            os.replace(tmp_name, out_path)
            return out_path
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return src_path
