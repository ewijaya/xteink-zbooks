# Z-Books Setup Guide

## Prerequisites

- **Device:** XTEINK X4 Pro running stock **XTOS Licorice** with **InkApp API 2.18** (verified on `XT Licorice 26112001`).
- **Bridge host:** Any Linux/macOS machine with **Python 3.10+** (zero pip dependencies required for running the bridge).
- **Optional (`zlib` search):** [`heartleo/zlib`](https://github.com/heartleo/zlib) CLI installed on the bridge host if you want Z-Library search & downloads. *(You can also use Z-Books purely as a wireless local EPUB queue with `./zbooks queue add-file` without installing `zlib`.)*

---

## 1. Authenticate `zlib` (Optional — for Z-Library Search)

Never paste your Z-Library password into a chat prompt. Log in directly in your terminal:

```bash
zlib login
zlib profile
```

Verify that `zlib profile` prints your account details and remaining daily quota.

---

## 2. Initialize the Companion Bridge

```bash
git clone https://github.com/ewijaya/xteink-zbooks.git
cd xteink-zbooks
./zbooks init --url https://YOUR-BRIDGE-URL
```

This creates `~/.config/zbooks-bridge/config.json` (mode `0600`) with a random Bearer token and prints the `config.json` snippet for your X4 Pro.

---

## 3. Choose How Your X4 Pro Connects

### Option A: Local Home Wi-Fi / macOS LAN (Simplest — No Tunnel Needed)
If your Mac or home Linux box is on the same Wi-Fi as your X4 Pro:

```bash
# Find your Mac/PC local IP (e.g. 192.168.1.25)
./zbooks init --url http://192.168.1.25:8742
./zbooks serve --host 0.0.0.0 --port 8742
```

### Option B: Cloudflare Tunnel (Access From Any Wi-Fi Over HTTPS)
The X4 Pro validates HTTPS against built-in public root CAs (`ctx.net:request`), so Cloudflare Tunnel gives valid HTTPS with zero firewall ports open.

- **Quick Tunnel (zero Cloudflare account needed):**
  ```bash
  cloudflared tunnel --url http://127.0.0.1:8742
  ```
  *(Note: Quick tunnel URLs change when restarted; update the URL anytime in Z-Books **Set → Server URL**.)*
- **Permanent Custom Domain:**
  In Cloudflare Zero Trust → **Networks → Tunnels**, add a **Public Hostname** (e.g. `books.yourdomain.com`) pointing to `http://127.0.0.1:8742`.
  > **Important:** Do **not** place a Cloudflare Access browser login screen in front of this hostname, since the X4 Pro InkApp cannot complete browser OAuth challenges. Authentication is enforced by the bridge's `Authorization: Bearer <token>` header.

---

## 4. Run in the Background on Linux (`systemd --user`)

Edit `WorkingDirectory` in [docs/zbooks-bridge.service](zbooks-bridge.service) if your clone is not in `~/projects/xteink-zbooks`, then enable it:

```bash
mkdir -p ~/.config/systemd/user
cp docs/zbooks-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now zbooks-bridge.service
systemctl --user status zbooks-bridge.service
```

Verify locally:
```bash
curl -s http://127.0.0.1:8742/health
```

---

## 5. Install Z-Books on Your XTEINK X4 Pro

1. Download `zbooks-0.1.1.xtapp` from the [latest GitHub release](https://github.com/ewijaya/xteink-zbooks/releases/latest) (or use `dist/zbooks-0.1.1.xtapp`).
2. Put your X4 Pro in **USB Mode**.
3. Copy `zbooks-0.1.1.xtapp` to the root of the microSD card.
4. Create `microSD/AppFiles/zbooks/` and place `config.json` inside it:
   ```bash
   ./zbooks device-config -o /tmp/config.json
   ```
   *(Or enter URL and Token on-device later via **Set → Server URL / API Token**.)*
5. Eject safely. On the X4 Pro:
   - Open **All files → `zbooks-0.1.1.xtapp`** → **Install**.
   - *(If **Ink App** is hidden, open **Settings → About** and tap **Model** ten times.)*
   - Open **Ink App → Z-Books** and tap **Set → Test Connection**.

---

## 6. Reading Downloaded Books

1. Tap **`Get`** on a queued book (or search & tap **Download to X4 Pro**).
2. Exit Z-Books (`Exit` or long-press Home).
3. Open **All files → `AppFiles` → `zbooks`** and tap your `.epub`.
4. After opening it once, it automatically appears on your **Bookshelf**.

---

## Troubleshooting

- **`HTTPS failed (clock unset)`:** Connect the X4 Pro to Wi-Fi and wait a few seconds for system time to sync, then retry.
- **`Unauthorized (401)`:** Ensure the `token` in `microSD/AppFiles/zbooks/config.json` matches `~/.config/zbooks-bridge/config.json`.
- **`Z-Library: Not logged in`:** Run `zlib login` (or `zlib doctor` if your mirror domain changed) on the bridge host.
- **Large books (> 8 MB):** The bridge automatically switches to chunked mode (`mode: "chunked"`), showing a live progress bar while appending chunks to `AppFiles/zbooks/`.
- **Stale TOC after re-downloading the same filename:** Delete the old file from Z-Books' **Files** tab (`Del`) before tapping **Get** so the X4 Pro stock reader clears its cached chapter list.
