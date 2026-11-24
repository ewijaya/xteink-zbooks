#!/usr/bin/env python3
"""Local community .xtapp packager for personal USB installation on XTEINK X4 Pro."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HASHES = {
    "tools/vendor/mksd.py": "8787787c61ec2f37202e845c6ca0bfab0dfc04a41da59bc4d395432786ab4e9e",
    "xtpack.py": "76580e3feedf5a19aac75b217246c5a69f5bbfb964907dcd68f837276227baa6",
    "tools/vendor/xtapp.py": "2e87c7630fcb46aea0b4d9c0ba17d84bf3d3d5b62cb05ba0c0d2bb830736a06a",
}


def package() -> Path:
    vendor = ROOT / "tools/vendor/xtpack"
    for name, expected in HASHES.items():
        actual = hashlib.sha256((vendor / name).read_bytes()).hexdigest()
        assert actual == expected, f"Packager source changed: {name}"

    spec = importlib.util.spec_from_file_location("zbooks_xtpack", vendor / "xtpack.py")
    pack = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(pack)

    source = ROOT / "app"
    man = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    pack.validate_manifest(man, str(source / "manifest.json"))

    code = [(str(p.relative_to(source)), p.read_bytes()) for p in sorted(source.rglob("*.lua"))]
    code.sort(key=lambda pair: (pair[0] != "index.lua", pair[0]))
    assert 1 <= len(code) <= 40

    icon_path = source / "assets" / f"{man['app_icon_s']}.png"
    with Image.open(icon_path) as image:
        assert image.size == (88, 88) and image.mode == "1"
        gray = image.convert("L")
        expected_pixels = list(gray.get_flattened_data() if hasattr(gray, "get_flattened_data") else gray.getdata())
        icon = pack.build_xic_1bpp(image)
    icons = pack.build_xtab([("app_icon_s", icon)])

    raw, key = pack.build_container(man, pack.device_pubkey(), code, [], icons=icons)

    # Full roundtrip verification
    _, decoded = pack.xtapp.read_licorice_container(raw)
    assert dict(pack.xtapp.read_licorice_code(raw, key)) == dict(code)
    sections = pack.xtapp.licorice_sections(raw)
    assert sections["data"][1] == 0

    offset, size = sections["icons"]
    stored = raw[offset : offset + size]
    assert stored == icons and stored[:4] == b"XTAB"
    count, table, data_start, total = struct.unpack_from("<IIII", stored, 8)
    assert count == 1 and table == 64 and data_start == 104 and total == len(stored)
    assert stored[64:88].rstrip(b"\0") == b"app_icon_s"
    image_offset, image_size, w, h, fmt = struct.unpack_from("<IIHHB", stored, 88)
    xic = stored[data_start + image_offset : data_start + image_offset + image_size]
    assert (w, h, fmt) == (88, 88, 1) and xic[:4] == b"XIC\0"
    pixels = [0 if xic[24 + y * 11 + x // 8] & (0x80 >> (x % 8)) else 255 for y in range(88) for x in range(88)]
    assert pixels == expected_pixels, "Packaged icon pixels differ from PNG"

    for field, value in man.items():
        assert decoded[field] == value

    out = ROOT / "dist" / f"{man['app_id']}-{man['version']}.xtapp"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    (ROOT / "dist/SHA256SUMS").write_text(f"{sha}  {out.name}\n", encoding="utf-8")

    report = {
        "path": str(out.relative_to(ROOT)),
        "sha256": sha,
        "bytes": len(raw),
        "app_id": man["app_id"],
        "version": man["version"],
        "code_files": len(code),
        "roundtrip": f"byte-identical {len(code)} Lua files; all 7744 launcher icon pixels match",
    }
    print(json.dumps(report, indent=2))
    return out


if __name__ == "__main__":
    package()
