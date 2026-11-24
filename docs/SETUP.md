# Z-Books Setup Guide (Cloudflare Tunnel + XTEINK X4 Pro)

## 1. Authenticate `zlib` on this Server

Never paste your Z-Library password into a chat prompt. Log in directly in your SSH terminal:

```bash
zlib login
zlib profile
```

Verify that `zlib profile` prints your account email and remaining daily download quota.

## 2. Initialize the Z-Books Bridge

```bash
cd /home/ubuntu/projects/xteink-zbooks
./zbooks init --url https://books.yourdomain.com
```

This creates `~/.config/zbooks-bridge/config.json` (mode `0600`) with a random Bearer token and prints the `config.json` snippet for your X4 Pro.

## 3. Expose `127.0.0.1:8742` via Cloudflare Tunnel (HTTPS)

The XTEINK X4 Pro validates HTTPS against built-in public root CAs (`ctx.net:request`), so Cloudflare Tunnel gives you valid HTTPS with zero firewall ports open.

### Option A: Quick test tunnel (no custom domain needed)
```bash
cloudflared tunnel --url http://127.0.0.1:8742
```
*(Note: Quick tunnel URLs change when restarted; great for first verification.)*

### Option B: Permanent custom domain (recommended)
In Cloudflare Zero Trust → **Networks → Tunnels**, add a **Public Hostname**:
- **Hostname:** `books.yourdomain.com`
- **Service:** `http://127.0.0.1:8742`

> **Important:** Do **not** place a Cloudflare Access login screen in front of this hostname, because the X4 Pro InkApp cannot complete browser OAuth/OTP challenges. Authentication is enforced by the bridge's `Authorization: Bearer <token>` header instead.

## 4. Run the Bridge as a Background Service (`systemd`)

Copy the provided user unit file and enable it:

```bash
mkdir -p ~/.config/systemd/user
cp /home/ubuntu/projects/xteink-zbooks/docs/zbooks-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now zbooks-bridge.service
systemctl --user status zbooks-bridge.service
```

Verify locally:
```bash
curl -s http://127.0.0.1:8742/health
```

## 5. Install on XTEINK X4 Pro (`XT Licorice 26112001` / API 2.18)

1. Connect the X4 Pro in **USB Mode**.
2. Copy `dist/zbooks-0.1.0.xtapp` to the root of the microSD card.
3. Create folder `microSD/AppFiles/zbooks/` and save `config.json` inside it:
   ```bash
   ./zbooks device-config --url https://books.yourdomain.com -o /tmp/config.json
   ```
   *(Or configure URL and Token directly on-device via **Set → Server URL / API Token**.)*
4. Eject safely. On the X4 Pro:
   - Open **All files → `zbooks-0.1.0.xtapp`** → **Install**.
   - Open **Ink App → Z-Books**.
   - Tap **Set → Test Connection** to confirm `Bridge OK` and `Z-Library: Logged in`.

## 6. Reading Downloaded Books

1. Tap **Get** on any queued book (or search & tap **Download to X4 Pro**).
2. Exit Z-Books (`Exit` or long-press Home).
3. Open **All files → `AppFiles` → `zbooks`** and tap the `.epub`.
4. After opening it once, it automatically appears on your **Bookshelf**.

## Troubleshooting

- **`HTTPS failed (clock unset)`:** Connect the X4 Pro to Wi-Fi and wait a few seconds for system time to sync, then retry.
- **`Unauthorized (401)`:** Ensure the `token` in `microSD/AppFiles/zbooks/config.json` matches `~/.config/zbooks-bridge/config.json`.
- **`Z-Library: Not logged in`:** Run `zlib login` (or `zlib doctor` if your mirror domain changed) on the server.
- **Large books (> 8 MB):** The bridge automatically switches to chunked mode (`mode: "chunked"`), showing a live progress bar while appending chunks to `AppFiles/zbooks/`.
