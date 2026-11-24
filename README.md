# Z-Books for XTEINK X4 Pro

An **XTEINK X4 Pro InkApp** (`app_id = "zbooks"`, API 2.18) paired with a zero-dependency **Companion Bridge** wrapping [`heartleo/zlib`](https://github.com/heartleo/zlib).

- **On-device Search & Download:** Search EPUBs directly from your X4 Pro over Wi-Fi and save them straight into `microSD/AppFiles/zbooks/`.
- **Send-to-XTEINK Queue:** Queue Z-Library books (`id:hash`) or any local `.epub` from your terminal or AI coding agent — then 1-tap **Get** on your X4 Pro with zero e-ink typing.
- **Automatic X4 Pro EPUB 3 TOC Fix:** Automatically flattens nested inline HTML tags (`<span>`, `<i>`, etc.) inside EPUB 3 `<nav>` TOC links (`server/epub_fix.py`) so the stock X4 Pro reader renders crisp chapter titles instead of raw markup, leaving all chapter content/fonts/images untouched.
- **EPUB Retail Quality Inspector:** Includes `tools/inspect_epub.py` (plus an agent skill in `skills/xteink-zbooks/`) to inspect OPF metadata, publisher, ISBN, reflowable layout, TOC depth, chapter splits, and body text before queueing books.
- **Large-book Resilient:** Books ≤ 7.5 MB stream straight to microSD via API 2.18 `save = { to = "files" }`; books > 7.5 MB automatically switch to chunked `ctx.files:append` transfers with a live progress bar.
- **Native Reading:** Exit Z-Books, open **All files → `AppFiles` → `zbooks`**, and tap your `.epub` to read it in the stock X4 Pro reader (it then appears on your **Bookshelf**).

## Project Layout

```text
xteink-zbooks/
├── app/                   # X4 Pro InkApp (Lua 5.5, API 2.18, 480x800 portrait)
├── server/                # Zero-dependency Python HTTP bridge, EPUB TOC cleaner & CLI
├── zbooks                 # Executable CLI wrapper (init, serve, search, queue)
├── tools/                 # Icon generator, EPUB inspector, linter, Lua tests, .xtapp packager
├── dist/                  # Built zbooks-0.1.0.xtapp + SHA256SUMS
├── docs/                  # Setup guide (Cloudflare Tunnel + systemd unit files)
└── skills/xteink-zbooks/  # Agent skill for finding, verifying, and queuing books to X4 Pro
```

## Quick Start

See [docs/SETUP.md](docs/SETUP.md) for full step-by-step instructions.

### 1. Log in to Z-Library on your server (terminal only)
```bash
zlib login
zlib profile
```

### 2. Initialize & run the Companion Bridge
```bash
./zbooks init --url https://books.yourdomain.com
./zbooks serve
```
Point your **Cloudflare Tunnel** (quick tunnel or custom domain) or HTTPS reverse proxy to `http://127.0.0.1:8742`.

### 3. Install `zbooks-0.1.0.xtapp` on your X4 Pro
1. Put your X4 Pro in **USB Mode**.
2. Copy `dist/zbooks-0.1.0.xtapp` to the root of the microSD card.
3. Copy the `config.json` printed by `./zbooks init` into `microSD/AppFiles/zbooks/config.json`:
   ```json
   {
     "url": "https://books.yourdomain.com",
     "token": "YOUR_GENERATED_TOKEN"
   }
   ```
4. Eject safely, open **All files**, tap `zbooks-0.1.0.xtapp`, and install it.
5. Launch **Ink App → Z-Books** and tap **Set → Test Connection**.

### 4. Queue books from your terminal (or AI agent)
```bash
# Search Z-Library
./zbooks search "Meditations Marcus Aurelius"

# Inspect a downloaded EPUB for retail quality
python3 tools/inspect_epub.py path/to/book.epub

# Queue a Z-Library book (--prefetch caches it immediately on the server)
./zbooks queue add 123456:abcdef01 --title "Meditations" --author "Marcus Aurelius" --prefetch

# Queue any local EPUB on the server
./zbooks queue add-file /path/to/book.epub

# List / remove queued items
./zbooks queue list
./zbooks queue rm <qid>
```

## Build & Test

```bash
make test      # Runs API 2.18 lint + Lua 5.5 compile + server tests + Lua host-mock tests
make package   # Rebuilds launcher icon, runs all tests, and packs dist/zbooks-0.1.0.xtapp
```

## License

MIT — see [LICENSE](LICENSE). Vendored packaging and lint tools under `tools/vendor/` retain their respective licenses.
