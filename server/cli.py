"""Command-line interface for Z-Books bridge server and Send-to-XTEINK queue."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .bridge import run_server
from .core import ZBooksStore


def cmd_init(store: ZBooksStore, args: argparse.Namespace) -> int:
    if args.rotate_token:
        store.config.token = secrets.token_urlsafe(24)
    if args.url:
        store.config.public_url = args.url.rstrip("/")
    if args.host:
        store.config.host = args.host
    if args.port:
        store.config.port = args.port
    store.save_config()

    device_cfg = {
        "url": store.config.public_url or f"https://YOUR-DOMAIN.example.com",
        "token": store.config.token,
    }
    print(f"Bridge config saved to: {store.config_path}")
    print(f"Listen address:         http://{store.config.host}:{store.config.port}")
    print()
    print("Copy this JSON into microSD/AppFiles/zbooks/config.json on your X4 Pro:")
    print(json.dumps(device_cfg, indent=2))
    return 0


def cmd_device_config(store: ZBooksStore, args: argparse.Namespace) -> int:
    url = (args.url or store.config.public_url or "https://YOUR-DOMAIN.example.com").rstrip("/")
    payload = {"url": url, "token": store.config.token}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote device config to {out}")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_serve(store: ZBooksStore, args: argparse.Namespace) -> int:
    run_server(store, host=args.host, port=args.port)
    return 0


def cmd_status(store: ZBooksStore, args: argparse.Namespace) -> int:
    zst = store.zlib_status()
    queue = store.list_queue()
    out = {
        "data_dir": str(store.data_dir),
        "listen": f"http://{store.config.host}:{store.config.port}",
        "public_url": store.config.public_url,
        "queue_count": len(queue),
        "zlib": zst,
    }
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"Data dir:   {out['data_dir']}")
        print(f"Listen:     {out['listen']}")
        if out["public_url"]:
            print(f"Public URL: {out['public_url']}")
        print(f"Queue:      {out['queue_count']} book(s)")
        print(f"zlib CLI:   {'ready' if zst.get('available') else 'missing'} / {'logged in' if zst.get('logged_in') else 'not logged in'}")
        if zst.get("error"):
            print(f"zlib note:  {zst['error']}")
    return 0


def cmd_search(store: ZBooksStore, args: argparse.Namespace) -> int:
    res = store.search(args.query, ext=args.ext, page=args.page, count=args.count)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    items = res["items"]
    if not items:
        print("No results found.")
        return 0
    for idx, it in enumerate(items, 1):
        meta = " · ".join(x for x in (it["author"], it["year"], it["size"], it["ext"].upper()) if x)
        print(f"[{idx}] {it['title']}")
        print(f"    {meta}")
        print(f"    id: {it['id']}")
    return 0


def cmd_queue_list(store: ZBooksStore, args: argparse.Namespace) -> int:
    items = store.list_queue()
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0
    if not items:
        print("Queue is empty.")
        return 0
    for idx, it in enumerate(items, 1):
        kind = "LOCAL" if it["kind"] == "local" else it["id"]
        meta = " · ".join(x for x in (it["author"], it["year"], it["size"], it["ext"].upper()) if x)
        print(f"[{idx}] ({it['qid']}) {it['title']}")
        print(f"    {meta}  [{kind}] -> {it['filename']}")
    return 0


def cmd_queue_add(store: ZBooksStore, args: argparse.Namespace) -> int:
    entry = store.add_queue_zlib(
        book_id=args.book_id,
        title=args.title or "",
        author=args.author or "",
        year=args.year or "",
        ext=args.ext or "epub",
        size_bytes=args.size_bytes or 0,
        filename=args.filename or "",
    )
    if args.prefetch:
        downloaded = store.ensure_zlib_downloaded(args.book_id)
        print(f"Prefetched to cache: {downloaded.name} ({downloaded.stat().st_size} bytes)", file=sys.stderr)
    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        print(f"Queued ({entry['qid']}): {entry['title']} -> {entry['filename']}")
    return 0


def cmd_queue_add_file(store: ZBooksStore, args: argparse.Namespace) -> int:
    entry = store.add_queue_local(
        args.path,
        title=args.title or "",
        author=args.author or "",
        filename=args.filename or "",
    )
    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        print(f"Queued local file ({entry['qid']}): {entry['title']} -> {entry['filename']}")
    return 0


def cmd_queue_rm(store: ZBooksStore, args: argparse.Namespace) -> int:
    ok = store.remove_queue(args.qid)
    if not ok:
        print(f"Queue item not found: {args.qid}", file=sys.stderr)
        return 1
    print(f"Removed {args.qid}")
    return 0


def cmd_queue_clear(store: ZBooksStore, args: argparse.Namespace) -> int:
    n = store.clear_queue()
    print(f"Cleared {n} queued book(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zbooks", description="Z-Books companion bridge and XTEINK queue CLI")
    p.add_argument("--data-dir", type=Path, default=None, help="Override config/cache directory")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="Initialize bridge config and print device config.json")
    sp.add_argument("--url", help="Public HTTPS URL of your Cloudflare Tunnel / domain")
    sp.add_argument("--host", help="Bind host (default 127.0.0.1)")
    sp.add_argument("--port", type=int, help="Bind port (default 8742)")
    sp.add_argument("--rotate-token", action="store_true", help="Generate a new Bearer token")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("device-config", help="Print or write AppFiles/zbooks/config.json")
    sp.add_argument("--url", help="Public HTTPS URL override")
    sp.add_argument("-o", "--out", help="Write config.json to this path")
    sp.set_defaults(func=cmd_device_config)

    sp = sub.add_parser("serve", help="Run the HTTP companion bridge server")
    sp.add_argument("--host", help="Bind host override")
    sp.add_argument("--port", type=int, help="Bind port override")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("status", help="Show bridge, queue, and zlib login status")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("search", help="Search Z-Library via zlib")
    sp.add_argument("query")
    sp.add_argument("--ext", default="epub")
    sp.add_argument("-p", "--page", type=int, default=1)
    sp.add_argument("-n", "--count", type=int, default=8)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    qp = sub.add_parser("queue", help="Manage the Send-to-XTEINK book queue")
    qsub = qp.add_subparsers(dest="queue_cmd", required=True)

    sp = qsub.add_parser("list", help="List queued books")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_queue_list)

    sp = qsub.add_parser("add", help="Queue a Z-Library book by id:hash")
    sp.add_argument("book_id", help="Z-Library id:hash from zlib search")
    sp.add_argument("--title", default="")
    sp.add_argument("--author", default="")
    sp.add_argument("--year", default="")
    sp.add_argument("--ext", default="epub")
    sp.add_argument("--size-bytes", type=int, default=0)
    sp.add_argument("--filename", default="")
    sp.add_argument("--prefetch", action="store_true", help="Download into server cache now")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_queue_add)

    sp = qsub.add_parser("add-file", help="Queue any local .epub/.txt file for XTEINK download")
    sp.add_argument("path", help="Path to local book file")
    sp.add_argument("--title", default="")
    sp.add_argument("--author", default="")
    sp.add_argument("--filename", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_queue_add_file)

    sp = qsub.add_parser("rm", help="Remove a book from the queue by qid")
    sp.add_argument("qid")
    sp.set_defaults(func=cmd_queue_rm)

    sp = qsub.add_parser("clear", help="Clear all queued books")
    sp.set_defaults(func=cmd_queue_clear)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = ZBooksStore(data_dir=args.data_dir)
    try:
        return args.func(store, args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
