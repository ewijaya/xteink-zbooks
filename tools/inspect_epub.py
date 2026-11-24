#!/usr/bin/env python3
"""Inspect an EPUB file for retail edition quality and XTEINK X4 Pro readability.

Zero external dependencies (Python 3 stdlib only). Checks:
- ZIP & EPUB structure (META-INF/container.xml, OPF, spine, TOC/nav, DRM)
- Retail metadata (title, creator, publisher, ISBN, language, date, rendition:layout)
- Conversion / OCR / PDF-dump red flags (Calibre PDF wrap, 1-chapter dump,
  fixed-layout pre-paginated, mid-sentence page numbers, soft/broken hyphens,
  unusual non-ASCII/OCR glyph noise)
- Text samples from real body chapters for human/agent sanity check
"""
from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_WS_RE = re.compile(r"\s+")
_ISBN_RE = re.compile(r"(?:97[89][-\s]?)?\d[-\s\d]{8,14}[\dXx]")
_PAGE_MARKER_RE = re.compile(r"(?m)^\s*(?:Page\s+\d+|-\s*\d+\s*-|\d{1,4})\s*$")
_BROKEN_HYPHEN_RE = re.compile(r"[a-z]-\s+[a-z]")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _strip_xhtml(raw_bytes: bytes) -> str:
    text = raw_bytes.decode("utf-8", errors="replace")
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    # Preserve paragraph breaks roughly
    text = re.sub(r"</(?:p|div|h[1-6]|li|tr|br)\b[^>]*>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _resolve_zip_path(opf_path: str, href: str) -> str:
    base = posixpath.dirname(opf_path)
    href_clean = href.split("#", 1)[0]
    joined = posixpath.normpath(posixpath.join(base, href_clean)) if base else posixpath.normpath(href_clean)
    return joined.lstrip("/")


def inspect_epub(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    report: dict[str, Any] = {
        "file": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "valid_epub": False,
        "drm_encrypted": False,
        "metadata": {},
        "structure": {},
        "signals": {"good": [], "warnings": [], "red_flags": []},
        "samples": [],
        "score": 0,
        "verdict": "REJECT",
    }

    if not path.is_file():
        report["signals"]["red_flags"].append("File does not exist")
        return report

    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile:
        report["signals"]["red_flags"].append("Corrupt or non-ZIP file")
        return report

    with zf:
        names = set(zf.namelist())
        if "META-INF/encryption.xml" in names:
            enc = zf.read("META-INF/encryption.xml").decode("utf-8", errors="ignore")
            # Font obfuscation (IDPF/Adobe) is harmless; actual content DRM is fatal
            if "http://www.idpf.org/2008/embedding" not in enc and "http://ns.adobe.com/pdf/enc#RC" not in enc:
                report["drm_encrypted"] = True
                report["signals"]["red_flags"].append("Contains META-INF/encryption.xml (possible DRM)")

        if "META-INF/container.xml" not in names:
            report["signals"]["red_flags"].append("Missing META-INF/container.xml")
            return report

        try:
            container_root = ET.fromstring(zf.read("META-INF/container.xml"))
        except ET.ParseError as exc:
            report["signals"]["red_flags"].append(f"Invalid container.xml: {exc}")
            return report

        opf_path = ""
        for el in container_root.iter():
            if _local(el.tag) == "rootfile" and el.attrib.get("full-path"):
                opf_path = el.attrib["full-path"]
                break

        if not opf_path or opf_path not in names:
            report["signals"]["red_flags"].append(f"OPF not found: {opf_path!r}")
            return report

        try:
            opf_root = ET.fromstring(zf.read(opf_path))
        except ET.ParseError as exc:
            report["signals"]["red_flags"].append(f"Invalid OPF XML: {exc}")
            return report

        report["valid_epub"] = True
        epub_version = opf_root.attrib.get("version", "?")

        # Parse metadata
        meta: dict[str, Any] = {
            "epub_version": epub_version,
            "titles": [],
            "creators": [],
            "publishers": [],
            "identifiers": [],
            "languages": [],
            "dates": [],
            "rendition_layout": "",
            "generator": "",
        }
        for el in opf_root.iter():
            lt = _local(el.tag)
            txt = (el.text or "").strip()
            if lt == "title" and txt:
                meta["titles"].append(txt)
            elif lt == "creator" and txt:
                meta["creators"].append(txt)
            elif lt == "publisher" and txt:
                meta["publishers"].append(txt)
            elif lt == "identifier" and txt:
                meta["identifiers"].append(txt)
            elif lt == "language" and txt:
                meta["languages"].append(txt)
            elif lt == "date" and txt:
                meta["dates"].append(txt)
            elif lt == "meta":
                prop = el.attrib.get("property", "")
                mname = el.attrib.get("name", "")
                mcontent = el.attrib.get("content", "") or txt
                if prop == "rendition:layout" or mname == "fixed-layout":
                    meta["rendition_layout"] = mcontent
                if mname.lower() in ("generator", "calibre:timestamp", "sigil version") or "calibre" in mname.lower():
                    if not meta["generator"]:
                        meta["generator"] = f"{mname}={mcontent}"
                    elif "calibre" in mname.lower() and "calibre" not in meta["generator"].lower():
                        meta["generator"] += f"; {mname}"

        report["metadata"] = meta

        # Manifest & spine
        manifest: dict[str, dict[str, str]] = {}
        for el in opf_root.iter():
            if _local(el.tag) == "item" and el.attrib.get("id"):
                manifest[el.attrib["id"]] = {
                    "href": el.attrib.get("href", ""),
                    "media_type": el.attrib.get("media-type", ""),
                    "properties": el.attrib.get("properties", ""),
                }

        spine_ids: list[str] = []
        for el in opf_root.iter():
            if _local(el.tag) == "itemref" and el.attrib.get("idref"):
                spine_ids.append(el.attrib["idref"])

        # Count asset types
        css_count = 0
        font_count = 0
        img_count = 0
        img_bytes = 0
        has_nav = False
        has_ncx = False
        toc_entries = 0

        for item in manifest.values():
            mt = item["media_type"]
            props = item["properties"].split()
            zpath = _resolve_zip_path(opf_path, item["href"])
            if "nav" in props:
                has_nav = True
                if zpath in names:
                    nav_txt = zf.read(zpath).decode("utf-8", errors="ignore")
                    toc_entries = max(toc_entries, len(re.findall(r"<a\b", nav_txt, re.I)))
            if mt == "application/x-dtbncx+xml":
                has_ncx = True
                if zpath in names:
                    ncx_txt = zf.read(zpath).decode("utf-8", errors="ignore")
                    toc_entries = max(toc_entries, len(re.findall(r"<navPoint\b", ncx_txt, re.I)))
            if mt == "text/css":
                css_count += 1
            elif "font" in mt or zpath.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
                font_count += 1
            elif mt.startswith("image/"):
                img_count += 1
                if zpath in names:
                    img_bytes += zf.getinfo(zpath).file_size

        # Analyze spine documents
        chapter_word_counts: list[tuple[str, int, str]] = []
        total_words = 0
        total_page_markers = 0
        total_broken_hyphens = 0
        total_replacement_chars = 0
        calibre_pdf_hints = 0

        for idref in spine_ids:
            item = manifest.get(idref)
            if not item:
                continue
            zpath = _resolve_zip_path(opf_path, item["href"])
            if zpath not in names:
                continue
            raw = zf.read(zpath)
            raw_head = raw[:4000].decode("utf-8", errors="ignore").lower()
            if "pdftohtml" in raw_head or "abbyy" in raw_head or "pdfreflow" in raw_head:
                calibre_pdf_hints += 1
            plain = _strip_xhtml(raw)
            words = len(plain.split())
            total_words += words
            total_page_markers += len(_PAGE_MARKER_RE.findall(plain))
            total_broken_hyphens += len(_BROKEN_HYPHEN_RE.findall(plain))
            total_replacement_chars += plain.count("\ufffd")
            chapter_word_counts.append((posixpath.basename(zpath), words, plain))

        substantial_chapters = [c for c in chapter_word_counts if c[1] >= 300]

        report["structure"] = {
            "spine_docs": len(spine_ids),
            "substantial_chapters": len(substantial_chapters),
            "total_words": total_words,
            "has_nav": has_nav,
            "has_ncx": has_ncx,
            "toc_entries": toc_entries,
            "css_files": css_count,
            "font_files": font_count,
            "image_files": img_count,
            "image_bytes": img_bytes,
            "page_markers": total_page_markers,
            "broken_hyphens": total_broken_hyphens,
            "replacement_chars": total_replacement_chars,
            "pdf_convert_hints": calibre_pdf_hints,
        }

        # Pick 2-3 representative body samples (around 20%, 50%, 75% through substantial chapters)
        if substantial_chapters:
            idxs = sorted({
                0,
                len(substantial_chapters) // 2,
                max(0, (len(substantial_chapters) * 3) // 4),
            })
            for idx in idxs[:3]:
                fname, wc, plain = substantial_chapters[idx]
                snippet = plain[:550].strip()
                report["samples"].append({"doc": fname, "words": wc, "excerpt": snippet})

        # Scoring & signals
        good = report["signals"]["good"]
        warn = report["signals"]["warnings"]
        red = report["signals"]["red_flags"]
        score = 50

        if meta["rendition_layout"].lower() in ("pre-paginated", "true"):
            red.append("Fixed-layout (pre-paginated) EPUB — poor reading experience on 4.3\" X4 Pro")
            score -= 40
        else:
            good.append("Reflowable layout (X4 Pro friendly)")
            score += 10

        if meta["publishers"]:
            good.append(f"Publisher: {', '.join(meta['publishers'])}")
            score += 12
        else:
            warn.append("No dc:publisher metadata")

        has_isbn = any(_ISBN_RE.search(x) for x in meta["identifiers"])
        if has_isbn:
            good.append("Contains ISBN identifier")
            score += 8
        else:
            warn.append("No ISBN in OPF identifiers")

        if has_nav or has_ncx:
            if toc_entries >= 5:
                good.append(f"Structured TOC ({toc_entries} entries)")
                score += 10
            else:
                warn.append(f"Sparse TOC ({toc_entries} entries)")
        else:
            red.append("Missing TOC (neither nav nor ncx)")
            score -= 20

        if len(substantial_chapters) >= 5:
            good.append(f"Well-split chapters ({len(substantial_chapters)} body docs, {total_words:,} words)")
            score += 10
        elif len(substantial_chapters) <= 1 and total_words > 10000:
            red.append(f"Single-file HTML dump ({len(substantial_chapters)} body doc for {total_words:,} words)")
            score -= 25
        elif total_words < 5000:
            red.append(f"Very low word count ({total_words:,} words — possible sample, summary, or image-only PDF wrap)")
            score -= 35

        if calibre_pdf_hints > 0:
            red.append(f"PDF/OCR conversion artifacts detected in {calibre_pdf_hints} chapter(s)")
            score -= 25

        if meta["generator"] and "calibre" in meta["generator"].lower():
            warn.append(f"Touched by Calibre ({meta['generator']})")
            score -= 5

        if total_words > 0:
            hyphen_rate = (total_broken_hyphens * 1000.0) / total_words
            page_rate = (total_page_markers * 1000.0) / total_words
            if hyphen_rate > 2.5:
                red.append(f"High broken-hyphen rate ({total_broken_hyphens} hits, {hyphen_rate:.1f}/1k words — likely PDF/OCR)")
                score -= 20
            elif hyphen_rate > 0.8:
                warn.append(f"Moderate broken hyphens ({total_broken_hyphens} hits)")
                score -= 5

            if page_rate > 1.5:
                red.append(f"Stray page number lines ({total_page_markers} hits — likely PDF conversion)")
                score -= 15
            elif page_rate > 0.4:
                warn.append(f"Some standalone page numbers ({total_page_markers} hits)")

        if total_replacement_chars > 5:
            red.append(f"UTF-8 replacement characters found ({total_replacement_chars})")
            score -= 15

        if img_bytes > 8_000_000 and total_words < 10000:
            red.append("Image-heavy with little text (likely scanned image EPUB)")
            score -= 30

        score = max(0, min(100, score))
        report["score"] = score
        if red or score < 60:
            report["verdict"] = "REJECT"
        elif score >= 80 and not red:
            report["verdict"] = "RETAIL_PASS"
        else:
            report["verdict"] = "ACCEPTABLE"

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("epub", type=Path, help="Path to .epub file to inspect")
    ap.add_argument("--json", action="store_true", help="Print JSON report")
    args = ap.parse_args()

    rep = inspect_epub(args.epub)
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0 if rep["verdict"] != "REJECT" else 2

    m = rep["metadata"]
    s = rep["structure"]
    print(f"File:      {rep['filename']} ({rep['size_bytes'] / 1024:.0f} KB)")
    print(f"Verdict:   {rep['verdict']}  (score: {rep['score']}/100)")
    print(f"Title:     {', '.join(m.get('titles', [])) or '?'}")
    print(f"Author:    {', '.join(m.get('creators', [])) or '?'}")
    print(f"Publisher: {', '.join(m.get('publishers', [])) or '(none)'}")
    print(f"Date/Ver:  {', '.join(m.get('dates', [])) or '?'}  |  EPUB {m.get('epub_version', '?')}")
    print(f"IDs:       {', '.join(m.get('identifiers', [])) or '(none)'}")
    if s:
        print(
            f"Structure: {s.get('substantial_chapters', 0)}/{s.get('spine_docs', 0)} chapters, "
            f"{s.get('total_words', 0):,} words, TOC={s.get('toc_entries', 0)}, "
            f"CSS={s.get('css_files', 0)}, fonts={s.get('font_files', 0)}, "
            f"images={s.get('image_files', 0)} ({s.get('image_bytes', 0) / 1024:.0f} KB)"
        )
    if rep["signals"]["good"]:
        print("\nGood signals:")
        for g in rep["signals"]["good"]:
            print(f"  + {g}")
    if rep["signals"]["warnings"]:
        print("\nWarnings:")
        for w in rep["signals"]["warnings"]:
            print(f"  ! {w}")
    if rep["signals"]["red_flags"]:
        print("\nRed flags:")
        for r in rep["signals"]["red_flags"]:
            print(f"  X {r}")
    if rep["samples"]:
        print("\nBody samples:")
        for smp in rep["samples"]:
            print(f"  --- {smp['doc']} ({smp['words']:,} words) ---")
            print("  " + smp["excerpt"].replace("\n", "\n  "))
    return 0 if rep["verdict"] != "REJECT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
