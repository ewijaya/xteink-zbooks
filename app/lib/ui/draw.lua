-- Monochrome e-ink UI renderer for 480x800 portrait XTEINK X4 Pro
-- Note: never draw text on a black-filled rect (device keeps text black).
local U = require("lib.core.util")

local M = {}

M.W = 480
M.H = 800

-- Layout constants (also shared with hit-testing in lib/app.lua)
M.RECTS = {
  -- Top bar (y: 0..64)
  settings_btn = { x = 316, y = 10, w = 72, h = 44 },
  exit_btn     = { x = 396, y = 10, w = 68, h = 44 },

  -- Tabs bar (y: 66..120)
  tab_queue    = { x = 16,  y = 68, w = 144, h = 48 },
  tab_search   = { x = 168, y = 68, w = 144, h = 48 },
  tab_files    = { x = 320, y = 68, w = 144, h = 48 },

  -- Search controls (inside Search tab, y: 130..192)
  search_box   = { x = 16,  y = 132, w = 342, h = 54 },
  ext_btn      = { x = 366, y = 132, w = 98,  h = 54 },

  -- Footer bar (y: 726..790)
  prev_btn     = { x = 16,  y = 730, w = 112, h = 54 },
  action_btn   = { x = 136, y = 730, w = 208, h = 54 },
  next_btn     = { x = 352, y = 730, w = 112, h = 54 },

  -- Detail / Settings / Download back button
  back_btn     = { x = 16,  y = 10,  w = 108, h = 44 },

  -- Detail action buttons
  dl_primary   = { x = 16,  y = 560, w = 448, h = 68 },
  dl_secondary = { x = 16,  y = 642, w = 448, h = 56 },

  -- Download screen cancel button
  cancel_btn   = { x = 96,  y = 560, w = 288, h = 60 },

  -- Settings buttons
  cfg_url_btn    = { x = 16, y = 174, w = 448, h = 68 },
  cfg_token_btn  = { x = 16, y = 258, w = 448, h = 68 },
  cfg_test_btn   = { x = 16, y = 346, w = 448, h = 60 },
  cfg_reload_btn = { x = 16, y = 420, w = 448, h = 54 },
  cfg_wifi_btn   = { x = 16, y = 488, w = 448, h = 54 },

  -- Setup screen buttons
  setup_cfg_btn  = { x = 16, y = 520, w = 448, h = 64 },
  setup_rld_btn  = { x = 16, y = 600, w = 448, h = 56 },
}

-- List row geometry
function M.row_rect(index, start_y, row_h)
  local y = start_y + (index - 1) * row_h
  return { x = 16, y = y, w = 448, h = row_h - 8 }
end

function M.row_action_rect(row_r)
  return { x = row_r.x + row_r.w - 94, y = row_r.y + 16, w = 82, h = row_r.h - 32 }
end

local function draw_btn(ctx, g, r, label, opts)
  opts = opts or {}
  local lw = opts.primary and 3 or (opts.active and 3 or 1)
  g:rect(r.x, r.y, r.w, r.h, { radius = opts.radius or 8, line_width = lw })
  if opts.double then
    g:rect(r.x + 4, r.y + 4, r.w - 8, r.h - 8, { radius = math.max(2, (opts.radius or 8) - 3), line_width = 1 })
  end
  local font = opts.font or ctx.fonts.medium
  local ty = r.y + math.floor((r.h - font.line_height) / 2)
  g:text(r.x + math.floor(r.w / 2), ty, label, {
    font = font,
    align = "center",
    bold = opts.bold or opts.primary or opts.active or false,
  })
end

local function draw_header(ctx, g, state)
  g:text(16, 16, "Z-Books", { font = ctx.fonts.large, bold = true })
  draw_btn(ctx, g, M.RECTS.settings_btn, "Set", { font = ctx.fonts.small })
  draw_btn(ctx, g, M.RECTS.exit_btn, "Exit", { font = ctx.fonts.small })
  g:line(0, 62, M.W, 62, { line_width = 1 })

  local tabs = {
    { id = "queue",  label = "Queue",  rect = M.RECTS.tab_queue },
    { id = "search", label = "Search", rect = M.RECTS.tab_search },
    { id = "files",  label = "Files",  rect = M.RECTS.tab_files },
  }
  for i = 1, #tabs do
    local t = tabs[i]
    local active = (state.tab == t.id)
    local label = t.label
    if t.id == "queue" and #state.queue > 0 then
      label = "Queue (" .. tostring(#state.queue) .. ")"
    elseif t.id == "files" and #state.files > 0 then
      label = "Files (" .. tostring(#state.files) .. ")"
    end
    draw_btn(ctx, g, t.rect, label, { font = ctx.fonts.small, active = active })
    if active then
      g:line(t.rect.x + 12, t.rect.y + t.rect.h - 4, t.rect.x + t.rect.w - 12, t.rect.y + t.rect.h - 4, { line_width = 3 })
    end
  end
  g:line(0, 124, M.W, 124, { line_width = 1 })
end

local function draw_footer(ctx, g, page_label, has_prev, has_next, center_label)
  g:line(0, 722, M.W, 722, { line_width = 1 })
  if has_prev then
    draw_btn(ctx, g, M.RECTS.prev_btn, "< Prev", { font = ctx.fonts.small })
  else
    g:text(72, 747, "Prev", { font = ctx.fonts.small, align = "center" })
  end

  if center_label and center_label ~= "" then
    draw_btn(ctx, g, M.RECTS.action_btn, center_label, { font = ctx.fonts.small })
  else
    g:text(240, 747, page_label or "", { font = ctx.fonts.small, align = "center" })
  end

  if has_next then
    draw_btn(ctx, g, M.RECTS.next_btn, "Next >", { font = ctx.fonts.small })
  else
    g:text(408, 747, "Next", { font = ctx.fonts.small, align = "center" })
  end
end

local function draw_book_row(ctx, g, r, item, selected, is_saved)
  g:rect(r.x, r.y, r.w, r.h, { radius = 8, line_width = selected and 3 or 1 })
  local title = U.ellipsize(item.title or "Untitled", 30)
  local meta = U.book_meta_line(item)
  g:text(r.x + 14, r.y + 12, title, { font = ctx.fonts.medium, bold = true })
  g:text(r.x + 14, r.y + 48, U.ellipsize(meta, 38), { font = ctx.fonts.small })

  local act = M.row_action_rect(r)
  if is_saved then
    g:rect(act.x, act.y, act.w, act.h, { radius = 6, line_width = 1 })
    g:text(act.x + math.floor(act.w / 2), act.y + math.floor((act.h - ctx.fonts.small.line_height) / 2), "Saved", {
      font = ctx.fonts.small,
      align = "center",
    })
  else
    draw_btn(ctx, g, act, "Get", { font = ctx.fonts.small, primary = selected })
  end
end

function M.draw_queue(ctx, g, state)
  draw_header(ctx, g, state)

  if state.loading then
    g:text(240, 340, state.status_msg or "Loading queue...", { font = ctx.fonts.medium, align = "center" })
    draw_footer(ctx, g, "", false, false, "Loading...")
    return
  end

  if state.error_msg then
    g:rect(24, 210, 432, 180, { radius = 10, line_width = 2 })
    g:text(240, 232, "Connection Issue", { font = ctx.fonts.medium, align = "center", bold = true })
    g:text_box(40, 272, 400, 100, state.error_msg, { font = ctx.fonts.small, align = "center" })
    draw_footer(ctx, g, "", false, false, "Retry Sync")
    return
  end

  local slice, p, total_pages = U.slice_page(state.queue, state.queue_page, U.PAGE_SIZE)
  if #slice == 0 then
    g:text(240, 260, "Queue is empty", { font = ctx.fonts.large, align = "center", bold = true })
    g:text_box(36, 316, 408, 140,
      "Queue books from your computer or AI agent with 'zbooks queue add <id:hash>', or switch to the Search tab.",
      { font = ctx.fonts.small, align = "center" })
    draw_footer(ctx, g, "", false, false, "Refresh Queue")
    return
  end

  local start_y = 136
  local row_h = 94
  for i = 1, #slice do
    local item = slice[i]
    local r = M.row_rect(i, start_y, row_h)
    local saved = item.filename and item.filename ~= "" and ctx.files:exists(item.filename)
    draw_book_row(ctx, g, r, item, state.selected_row == i, saved)
  end

  draw_footer(ctx, g, "Page " .. tostring(p) .. "/" .. tostring(total_pages), p > 1, p < total_pages, "Refresh (" .. tostring(p) .. "/" .. tostring(total_pages) .. ")")
end

function M.draw_search(ctx, g, state)
  draw_header(ctx, g, state)

  -- Search input box
  local sb = M.RECTS.search_box
  g:rect(sb.x, sb.y, sb.w, sb.h, { radius = 8, line_width = 2 })
  local qlabel = (state.query and state.query ~= "") and U.ellipsize(state.query, 24) or "Tap to search books..."
  g:text(sb.x + 14, sb.y + 15, qlabel, { font = ctx.fonts.medium })

  -- Format chip
  local ext_label = (state.search_ext == "all") and "All" or string.upper(state.search_ext or "epub")
  draw_btn(ctx, g, M.RECTS.ext_btn, ext_label, { font = ctx.fonts.small })

  if state.loading then
    g:text(240, 360, state.status_msg or "Searching Z-Library...", { font = ctx.fonts.medium, align = "center" })
    draw_footer(ctx, g, "", false, false, "Cancel")
    return
  end

  if state.error_msg then
    g:rect(24, 240, 432, 170, { radius = 10, line_width = 2 })
    g:text(240, 260, "Search Error", { font = ctx.fonts.medium, align = "center", bold = true })
    g:text_box(40, 298, 400, 96, state.error_msg, { font = ctx.fonts.small, align = "center" })
    draw_footer(ctx, g, "", false, false, "Try Again")
    return
  end

  if #state.search_results == 0 then
    if state.search_done then
      g:text(240, 320, "No books found", { font = ctx.fonts.large, align = "center", bold = true })
      g:text(240, 368, "Try another keyword or switch EPUB -> All.", { font = ctx.fonts.small, align = "center" })
    else
      g:text(240, 266, "Search Z-Library", { font = ctx.fonts.large, align = "center", bold = true })
      g:text(240, 314, "Tap the search bar above to find EPUBs.", { font = ctx.fonts.small, align = "center" })
      -- Recent searches
      if #state.recent_queries > 0 then
        g:text(24, 376, "Recent searches:", { font = ctx.fonts.small, bold = true })
        for i = 1, math.min(4, #state.recent_queries) do
          local rr = M.row_rect(i, 410, 66)
          g:rect(rr.x, rr.y, rr.w, rr.h, { radius = 8, line_width = 1 })
          g:text(rr.x + 14, rr.y + 16, U.ellipsize(state.recent_queries[i], 36), { font = ctx.fonts.medium })
        end
      end
    end
    draw_footer(ctx, g, "", false, false, "Search")
    return
  end

  -- Display up to 5 results per page on Search tab (below search bar)
  local start_y = 200
  local row_h = 102
  local visible = math.min(5, #state.search_results)
  for i = 1, visible do
    local item = state.search_results[i]
    local r = M.row_rect(i, start_y, row_h)
    local saved = item.filename and item.filename ~= "" and ctx.files:exists(item.filename)
    draw_book_row(ctx, g, r, item, state.selected_row == i, saved)
  end

  local p = state.search_page or 1
  draw_footer(ctx, g, "Page " .. tostring(p), p > 1, state.search_has_more, "Page " .. tostring(p))
end

function M.draw_files(ctx, g, state)
  draw_header(ctx, g, state)

  -- Helpful folder banner
  g:rect(16, 134, 448, 48, { radius = 6, line_width = 1 })
  g:text(240, 148, "Read via: All files -> AppFiles/zbooks", { font = ctx.fonts.small, align = "center" })

  local slice, p, total_pages = U.slice_page(state.files, state.files_page, 5)
  if #slice == 0 then
    g:text(240, 320, "No downloaded books yet", { font = ctx.fonts.large, align = "center", bold = true })
    g:text(240, 370, "Download from Queue or Search.", { font = ctx.fonts.small, align = "center" })
    draw_footer(ctx, g, "", false, false, "Refresh Files")
    return
  end

  local start_y = 196
  local row_h = 102
  for i = 1, #slice do
    local f = slice[i]
    local r = M.row_rect(i, start_y, row_h)
    local sel = (state.selected_row == i)
    g:rect(r.x, r.y, r.w, r.h, { radius = 8, line_width = sel and 3 or 1 })
    g:text(r.x + 14, r.y + 14, U.ellipsize(f.name, 30), { font = ctx.fonts.medium, bold = true })
    g:text(r.x + 14, r.y + 52, U.format_bytes(f.size), { font = ctx.fonts.small })
    local act = M.row_action_rect(r)
    draw_btn(ctx, g, act, "Del", { font = ctx.fonts.small })
  end

  draw_footer(ctx, g, "Page " .. tostring(p) .. "/" .. tostring(total_pages), p > 1, p < total_pages, "Refresh (" .. tostring(#state.files) .. ")")
end

function M.draw_detail(ctx, g, state)
  local item = state.detail_item
  draw_btn(ctx, g, M.RECTS.back_btn, "< Back", { font = ctx.fonts.small })
  g:text(240, 18, "Book Details", { font = ctx.fonts.medium, align = "center", bold = true })
  g:line(0, 64, M.W, 64, { line_width = 1 })

  if not item then
    return
  end

  g:text_box(20, 84, 440, 120, item.title or "Untitled", { font = ctx.fonts.large, bold = true })

  local y = 218
  local function info_row(label, val)
    if not val or val == "" then
      return
    end
    g:text(20, y, label .. ":", { font = ctx.fonts.small, bold = true })
    g:text_box(128, y, 332, 48, tostring(val), { font = ctx.fonts.small })
    y = y + 36
  end

  info_row("Author", item.author ~= "" and item.author or "Unknown")
  info_row("Year", item.year)
  info_row("Format", string.upper(item.ext or "epub"))
  info_row("Size", item.size ~= "" and item.size or U.format_bytes(item.size_bytes))
  info_row("Lang", item.lang)
  info_row("Save as", U.ellipsize(item.filename or "book.epub", 34))

  local saved = item.filename and item.filename ~= "" and ctx.files:exists(item.filename)
  if saved then
    g:rect(20, 466, 440, 68, { radius = 8, line_width = 2 })
    g:text(240, 478, "Already saved in AppFiles/zbooks", { font = ctx.fonts.small, align = "center", bold = true })
    g:text(240, 504, "Open via All files -> AppFiles/zbooks", { font = ctx.fonts.small, align = "center" })
  end

  local dl_label = saved and "Download Again" or "Download to X4 Pro"
  draw_btn(ctx, g, M.RECTS.dl_primary, dl_label, { font = ctx.fonts.large, primary = true, double = true })

  if item.qid and item.qid ~= "" then
    draw_btn(ctx, g, M.RECTS.dl_secondary, "Remove from Queue", { font = ctx.fonts.medium })
  elseif item.id and item.id ~= "" then
    draw_btn(ctx, g, M.RECTS.dl_secondary, "Add to Server Queue", { font = ctx.fonts.medium })
  end
end

function M.draw_downloading(ctx, g, state)
  g:text(240, 24, "Downloading", { font = ctx.fonts.large, align = "center", bold = true })
  g:line(0, 66, M.W, 66, { line_width = 1 })

  local item = state.dl_item or {}
  g:text_box(24, 140, 432, 110, item.title or "Book", { font = ctx.fonts.large, align = "center", bold = true })
  g:text(240, 266, U.ellipsize(item.filename or "", 38), { font = ctx.fonts.small, align = "center" })

  -- Progress frame
  g:rect(40, 340, 400, 32, { radius = 6, line_width = 2 })
  local total = state.dl_total or 0
  local done = state.dl_done or 0
  if total > 0 and done > 0 then
    local fill_w = math.max(8, math.min(392, math.floor((done * 392) / total)))
    g:rect(44, 344, fill_w, 24, { radius = 4, fill = true })
  end

  g:text_box(24, 396, 432, 90, state.dl_status or "Working...", { font = ctx.fonts.medium, align = "center" })
  g:text(240, 496, "Keep screen awake until saved.", { font = ctx.fonts.small, align = "center" })

  draw_btn(ctx, g, M.RECTS.cancel_btn, "Cancel Download", { font = ctx.fonts.medium })
end

function M.draw_settings(ctx, g, state)
  draw_btn(ctx, g, M.RECTS.back_btn, "< Back", { font = ctx.fonts.small })
  g:text(240, 18, "Settings", { font = ctx.fonts.medium, align = "center", bold = true })
  g:line(0, 64, M.W, 64, { line_width = 1 })

  g:text(20, 82, "Bridge Configuration", { font = ctx.fonts.medium, bold = true })
  g:text(20, 114, "File: " .. (ctx.files and ctx.files.path or "/AppFiles/zbooks") .. "/config.json", { font = ctx.fonts.small })
  g:text(20, 140, "Tap below to edit on device, or drop config.json via USB.", { font = ctx.fonts.small })

  local url_disp = U.is_valid_url(state.cfg.url) and U.ellipsize(state.cfg.url, 32) or "(not set)"
  g:rect(M.RECTS.cfg_url_btn.x, M.RECTS.cfg_url_btn.y, M.RECTS.cfg_url_btn.w, M.RECTS.cfg_url_btn.h, { radius = 8, line_width = 2 })
  g:text(30, M.RECTS.cfg_url_btn.y + 10, "Server URL:", { font = ctx.fonts.small, bold = true })
  g:text(30, M.RECTS.cfg_url_btn.y + 36, url_disp, { font = ctx.fonts.small })

  g:rect(M.RECTS.cfg_token_btn.x, M.RECTS.cfg_token_btn.y, M.RECTS.cfg_token_btn.w, M.RECTS.cfg_token_btn.h, { radius = 8, line_width = 2 })
  g:text(30, M.RECTS.cfg_token_btn.y + 10, "API Token:", { font = ctx.fonts.small, bold = true })
  g:text(30, M.RECTS.cfg_token_btn.y + 36, U.mask_token(state.cfg.token), { font = ctx.fonts.small })

  draw_btn(ctx, g, M.RECTS.cfg_test_btn, state.loading and "Testing..." or "Test Connection", { font = ctx.fonts.medium, primary = true })
  draw_btn(ctx, g, M.RECTS.cfg_reload_btn, "Reload config.json from SD", { font = ctx.fonts.small })
  draw_btn(ctx, g, M.RECTS.cfg_wifi_btn, "Open Wi-Fi Settings", { font = ctx.fonts.small })

  -- Status / device diagnostics
  g:rect(16, 566, 448, 210, { radius = 8, line_width = 1 })
  local api_str = ctx.api and (tostring(ctx.api.major) .. "." .. tostring(ctx.api.minor)) or "?"
  local fw_str = ctx.device and tostring(ctx.device.firmware or "?") or "?"
  g:text(28, 580, "Z-Books v" .. U.VERSION .. "  |  API " .. api_str .. "  |  FW " .. fw_str, { font = ctx.fonts.small })
  g:text_box(28, 616, 424, 148, state.test_result or "Tap 'Test Connection' to verify bridge & Z-Library login.", {
    font = ctx.fonts.small,
  })
end

function M.draw_setup(ctx, g, state)
  g:text(16, 18, "Z-Books Setup", { font = ctx.fonts.large, bold = true })
  draw_btn(ctx, g, M.RECTS.exit_btn, "Exit", { font = ctx.fonts.small })
  g:line(0, 66, M.W, 66, { line_width = 1 })

  g:text(20, 96, "Connect to your Z-Books Bridge", { font = ctx.fonts.large, bold = true })
  g:text_box(20, 144, 440, 110,
    "Z-Books connects to your companion bridge server (behind Cloudflare Tunnel or your HTTPS domain).",
    { font = ctx.fonts.small })

  g:rect(20, 260, 440, 224, { radius = 10, line_width = 2 })
  g:text(36, 278, "Fastest setup (USB):", { font = ctx.fonts.medium, bold = true })
  g:text_box(36, 314, 408, 154,
    "1. Run 'zbooks init --url https://...' on your server.\n2. Copy the printed config.json into:\n   microSD/AppFiles/zbooks/config.json\n3. Tap 'Reload config.json' below.",
    { font = ctx.fonts.small })

  draw_btn(ctx, g, M.RECTS.setup_cfg_btn, "Configure URL & Token on Device", { font = ctx.fonts.medium, primary = true })
  draw_btn(ctx, g, M.RECTS.setup_rld_btn, "Reload config.json from SD", { font = ctx.fonts.medium })
end

function M.draw(ctx, g, state)
  g:clear(0)
  if state.screen == "setup" then
    M.draw_setup(ctx, g, state)
  elseif state.screen == "settings" then
    M.draw_settings(ctx, g, state)
  elseif state.screen == "detail" then
    M.draw_detail(ctx, g, state)
  elseif state.screen == "downloading" then
    M.draw_downloading(ctx, g, state)
  elseif state.tab == "search" then
    M.draw_search(ctx, g, state)
  elseif state.tab == "files" then
    M.draw_files(ctx, g, state)
  else
    M.draw_queue(ctx, g, state)
  end
end

return M
