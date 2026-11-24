---
name: xteink-zbooks
description: Search Z-Library via heartleo/zlib, hunt for and verify the highest-quality retail EPUB edition using EPUB internal inspection, and queue verified books (or local EPUBs) directly to the user's XTEINK X4 Pro Z-Books InkApp queue. Use when the user asks to "send a book to my XTEINK", "queue a book for X4 Pro", "find the best retail EPUB for my XTEINK", or "get [book] for my XTEINK".
---

# Send Verified Retail EPUBs to XTEINK X4 Pro (Z-Books Queue)

Use the `zbooks` CLI and EPUB quality inspector from your local `xteink-zbooks` checkout (set `ZBOOKS_DIR`, e.g. `~/projects/xteink-zbooks`) to find, verify, and queue clean retail EPUBs for 1-tap Wi-Fi download on the user's XTEINK X4 Pro.

## 1. Check Status (Optional)
```bash
"$ZBOOKS_DIR/zbooks" status
```
If `zlib` is not logged in, ask the user to run `zlib login` directly in their terminal (never ask for credentials in chat).

## 2. Search & Rank Candidates (Uses 0 Daily Quota)
Pull a wide set of EPUB candidates first:
```bash
"$ZBOOKS_DIR/zbooks" search "<title author>" --ext epub -n 15 --json
```
Rank candidates **before downloading** to save daily Z-Library quota (typically 10/day):
- Prefer the canonical / modern **retail publisher edition** (e.g., Stripe Press, Penguin Classics, Modern Library, O'Reilly, etc.) and matching translator/edition/year.
- Prefer realistic reflowable EPUB sizes (`250 KB – 7 MB` for prose; larger only when genuinely illustrated/technical).
- Skip obvious summaries, study guides, PDF-scan wraps, and suspiciously tiny (`< 100 KB`) files.

## 3. Download & Inspect Internals (`inspect_epub.py`)
Download the top candidate into a temporary staging folder and inspect its internals:
```bash
mkdir -p /tmp/zbooks-verify
zlib download "<id:hash>" -d /tmp/zbooks-verify --json
python3 "$ZBOOKS_DIR/tools/inspect_epub.py" "/tmp/zbooks-verify/<file.epub>"
```

Check the report carefully:
- **`RETAIL_PASS` (score ≥ 80, zero red flags):** Reflowable layout (not `pre-paginated`), genuine publisher & ISBN, structured TOC, proper multi-chapter spine, clean chapter text samples with no OCR/PDF page-break/broken-hyphen artifacts. *(Note: nested HTML tags inside EPUB 3 `<nav>` TOC links are auto-cleaned by `server/epub_fix.py` when the X4 Pro downloads the book.)*
- **If `REJECT` (or poor quality / wrong edition):**
  - Discard the bad file and try the next best ranked candidate.
  - **Quota guard:** Cap automatic retries at **3 downloads** per book request. If 3 candidates fail, report what was tried + remaining daily quota (`zlib profile --json`) and ask the user before downloading more.

## 4. Queue the Verified Retail EPUB for XTEINK X4 Pro
Keep the verified file in `~/.config/zbooks-bridge/verified/` and queue it via `add-file` (avoiding a second Z-Library download):
```bash
mkdir -p ~/.config/zbooks-bridge/verified
cp "/tmp/zbooks-verify/<file.epub>" ~/.config/zbooks-bridge/verified/"<Clean Title - Author.epub>"
"$ZBOOKS_DIR/zbooks" queue add-file \
  ~/.config/zbooks-bridge/verified/"<Clean Title - Author.epub>" \
  --title "<Title>" \
  --author "<Author>"
rm -rf /tmp/zbooks-verify
```

## 5. Report to the User
Tell the user:
- Exact edition verified (publisher, year/ISBN, chapter count, word count, file size, quality score).
- That it's ready in **Ink App → Z-Books → Queue** for 1-tap **`Get`**, after which it opens from **All files → `AppFiles` → `zbooks`**.
