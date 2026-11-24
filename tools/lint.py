#!/usr/bin/env python3
"""API 2.18 static lint and Lua 5.5 syntax/const check for Z-Books InkApp."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LUAC_CANDIDATES = [
    ROOT / "build/toolchain/lua-5.5.0/src/luac",
    Path("/home/ubuntu/projects/xteink-timer-deck/build/toolchain/lua-5.5.0/src/luac"),
]


def find_luac() -> Path | None:
    for cand in LUAC_CANDIDATES:
        if cand.is_file():
            return cand
    return None


def check_file(path: Path, source: str) -> None:
    token = re.compile(r"--\[\[.*?\]\]|--[^\n]*|\[\[.*?\]\]|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", re.S)
    code = token.sub(lambda m: "" if m[0].startswith("--") else m[0], source)
    assert not re.search(r'''\brequire\s*\(?\s*['"][^'"]*[/\\]''', code), f"{path}: slash require"
    code = token.sub(lambda m: '""', code)

    banned = [
        r"(?<![.\w:])(?:io|os|debug|load|loadfile|dofile|print|collectgarbage)\b",
        r"\bctx\s*[.:]\s*(?:data|assets|display|set_tick_rate|request_refresh)\b",
    ]
    for pat in banned:
        assert not re.search(pat, code), f"{path}: banned API pattern {pat}"

    if "core" in path.parts:
        assert not re.search(r"\b(?:ctx|_G|_ENV)\b", code), f"{path}: impure core module"

    if re.search(r"\bon_draw\b", code):
        assert re.search(r"function\s+on_draw\s*\(\s*ctx\s*,\s*g\s*\)", code), f"{path}: bad on_draw signature"


def main() -> None:
    files = sorted((ROOT / "app").rglob("*.lua"))
    assert files, "No Lua files found under app/"
    subprocess.run([sys.executable, str(ROOT / "tools/vendor/lualint55.py"), *map(str, files)], check=True)
    luac = find_luac()
    for p in files:
        check_file(p, p.read_text(encoding="utf-8"))
        if luac:
            subprocess.run([str(luac), "-p", str(p)], check=True)
    print(f"Lint OK: {len(files)} Lua script(s) passed (luac={'yes' if luac else 'no'})")


if __name__ == "__main__":
    main()
