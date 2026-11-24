-- Host-mock integration tests for Z-Books XTEINK X4 Pro InkApp
package.path = "app/?.lua;tools/shim/?.lua;" .. package.path

local json = require("json")
local U = require("lib.core.util")
local Draw = require("lib.ui.draw")

-- Pure utility checks
assert(U.clean_url("  https://books.example.com/// ") == "https://books.example.com")
assert(U.is_valid_url("https://books.example.com"))
assert(not U.is_valid_url("ftp://books.example.com"))
assert(U.format_bytes(512) == "512 B")
assert(U.format_bytes(2048) == "2 KB")
assert(U.format_bytes(2 * 1048576) == "2.0 MB")
assert(U.mask_token("abcdefghijklmnop") == "abcd...mnop")

-- Mock X4 Pro API 2.18 host
local function make_host(routes)
  local files = {}
  local toasts = {}
  local pending_net = {}
  local pending_after = {}
  local pending_input = nil
  local pending_confirm = nil
  local quit_called = false
  local keep_wifi_ms = nil
  local clock_ms = 1000

  local function make_font(lh)
    return {
      line_height = lh,
      ascent = math.floor(lh * 0.8),
      measure = function(_, s) return #tostring(s) * math.floor(lh * 0.5) end,
    }
  end

  local ctx
  ctx = {
    api = { major = 2, minor = 18 },
    device = {
      model = "x4_pro",
      firmware = "26112001",
      keep_awake = function() end,
      display = function() return { dark = false } end,
    },
    screen = { width = 480, height = 800, gray_levels = 4 },
    fonts = {
      small = make_font(20),
      medium = make_font(24),
      large = make_font(30),
    },
    state = {},
    json = {
      encode = function(_, v) return json:encode(v) end,
      decode = function(_, s) return json:decode(s) end,
    },
    url = {
      encode = function(_, s)
        return (tostring(s):gsub("([^%w%-_%.~])", function(c)
          return string.format("%%%02X", string.byte(c))
        end))
      end,
    },
    clock = {
      millis = function() clock_ms = clock_ms + 300; return clock_ms end,
      now = function() return { valid = true, hour = 14, minute = 0 } end,
    },
    log = {
      info = function() end,
      error = function(_, m) error("ctx.log:error called unexpectedly: " .. tostring(m)) end,
    },
    files = {
      path = "/AppFiles/zbooks",
      exists = function(_, name) return files[name] ~= nil end,
      read = function(_, name, off, len)
        local data = files[name]
        if not data then return nil, "not_found" end
        local s = (off or 0) + 1
        local e = s + (len or 65536) - 1
        return data:sub(s, e)
      end,
      write = function(_, name, data) files[name] = data; return true end,
      append = function(_, name, data) files[name] = (files[name] or "") .. data; return true end,
      remove = function(_, name) files[name] = nil; return true end,
      list = function()
        local out = {}
        for k, v in pairs(files) do
          out[#out + 1] = { name = k, size = #v }
        end
        table.sort(out, function(a, b) return a.name < b.name end)
        return out
      end,
    },
    dialog = {
      toast = function(_, msg) toasts[#toasts + 1] = msg end,
      confirm = function(_, opts, cb) pending_confirm = { opts = opts, cb = cb } end,
    },
    input = {
      text = function(_, opts, cb) pending_input = { opts = opts, cb = cb } end,
    },
    system = {
      can_open = function(_, target) return target == "settings.wifi" end,
      open = function() end,
    },
    net = {
      keep_wifi = function(_, ms) keep_wifi_ms = ms end,
      request = function(_, opts, cb)
        local job = { cancelled = false }
        function job:cancel() self.cancelled = true end
        pending_net[#pending_net + 1] = { opts = opts, cb = cb, job = job }
        return job
      end,
    },
    after = function(_, ms, fn)
      pending_after[#pending_after + 1] = fn
    end,
    invalidate = function() end,
    save_state = function() end,
    quit = function() quit_called = true end,
  }

  local function flush()
    local guard = 0
    while (#pending_net > 0 or #pending_after > 0) and guard < 100 do
      guard = guard + 1
      if #pending_after > 0 then
        local fn = table.remove(pending_after, 1)
        fn(ctx)
      elseif #pending_net > 0 then
        local req = table.remove(pending_net, 1)
        if not req.job.cancelled then
          local res = routes(req.opts)
          if req.opts.save and req.opts.save.to == "files" then
            if res.ok and res.status >= 200 and res.status < 300 then
              files[req.opts.save.name] = res.body
              res = { ok = true, status = res.status, headers = {}, body = "", bytes = #res.body, saved = true }
            else
              res.saved = false
            end
          end
          req.cb(ctx, res)
        end
      end
    end
  end

  local function draw_frame()
    local cmds = 0
    local g = {
      size = function() return 480, 800 end,
      clear = function() cmds = cmds + 1 end,
      line = function() cmds = cmds + 1 end,
      rect = function() cmds = cmds + 1 end,
      text = function() cmds = cmds + 1 end,
      text_box = function() cmds = cmds + 1 end,
    }
    on_draw(ctx, g)
    assert(cmds > 0 and cmds < 160, "Unexpected draw command count: " .. tostring(cmds))
    return cmds
  end

  local function tap(r)
    local x = r.x + math.floor(r.w / 2)
    local y = r.y + math.floor(r.h / 2)
    return on_input(ctx, { type = "pointer", phase = "up", gesture = "tap", x = x, y = y })
  end

  return {
    ctx = ctx,
    files = files,
    toasts = toasts,
    flush = flush,
    draw = draw_frame,
    tap = tap,
    submit_input = function(text)
      assert(pending_input, "No pending text input")
      local cb = pending_input.cb
      pending_input = nil
      cb(ctx, text)
    end,
    confirm = function(ok)
      assert(pending_confirm, "No pending confirm dialog")
      local cb = pending_confirm.cb
      pending_confirm = nil
      cb(ctx, ok)
    end,
    was_quit = function() return quit_called end,
    keep_wifi_ms = function() return keep_wifi_ms end,
  }
end

dofile("app/index.lua")

local big_payload = string.rep("0123456789ABCDEF", 200) -- 3200 bytes split across 3 chunks
local removed_qids = {}

local host = make_host(function(opts)
  assert(opts.headers and opts.headers["authorization"] == "Bearer tok123", "Missing Bearer auth")
  local url = opts.url
  if url:match("/api/queue$") and opts.method == "GET" then
    return {
      ok = true, status = 200, headers = {},
      body = json:encode({
        ok = true,
        items = {
          { qid = "q1", kind = "zlib", id = "10:aa", title = "Direct Book", author = "Author A", year = "2024", ext = "epub", size = "1.2 MB", size_bytes = 1200000, filename = "Direct Book.epub" },
          { qid = "q2", kind = "zlib", id = "20:bb", title = "Large Atlas", author = "Author B", year = "2023", ext = "epub", size = "12.0 MB", size_bytes = #big_payload, filename = "Large Atlas.epub" },
        },
      }),
    }
  elseif url:match("/api/queue/remove$") and opts.method == "POST" then
    local b = json:decode(opts.body)
    removed_qids[#removed_qids + 1] = b.qid
    return { ok = true, status = 200, headers = {}, body = json:encode({ ok = true, removed = true }) }
  elseif url:match("/api/search%?") then
    return {
      ok = true, status = 200, headers = {},
      body = json:encode({
        ok = true, query = "Marcus", page = 1, has_more = false,
        items = {
          { id = "99:ff", title = "Meditations", author = "Marcus Aurelius", year = "180", ext = "epub", size = "400 KB", size_bytes = 409600, filename = "Meditations.epub" },
        },
      }),
    }
  elseif url:match("/api/prepare$") and opts.method == "POST" then
    local b = json:decode(opts.body)
    if b.qid == "q2" then
      return {
        ok = true, status = 200, headers = {},
        body = json:encode({ ok = true, token = "dl_big", filename = "Large Atlas.epub", size_bytes = #big_payload, mode = "chunked", chunk_bytes = 1200 }),
      }
    end
    return {
      ok = true, status = 200, headers = {},
      body = json:encode({ ok = true, token = "dl_small", filename = "Direct Book.epub", size_bytes = 24, mode = "direct", chunk_bytes = 114688 }),
    }
  elseif url:match("/api/file/dl_small$") then
    return { ok = true, status = 200, headers = {}, body = "EPUB_DIRECT_BINARY_DATA!" }
  elseif url:match("/api/chunk/dl_big") then
    local off = tonumber(url:match("offset=(%d+)")) or 0
    local len = tonumber(url:match("len=(%d+)")) or 1200
    local slice = big_payload:sub(off + 1, off + len)
    return { ok = true, status = 200, headers = {}, body = slice }
  elseif url:match("/api/status$") then
    return {
      ok = true, status = 200, headers = {},
      body = json:encode({ ok = true, version = "0.1.0", queue_count = 2, zlib_logged_in = true, quota = { downloads_left = 8 } }),
    }
  end
  error("Unhandled mock route: " .. tostring(opts.method) .. " " .. tostring(url))
end)

-- 1. First load with no config -> Setup screen
on_load(host.ctx)
assert(host.keep_wifi_ms() == 300000)
host.draw()

-- 2. Drop config.json in AppFiles/zbooks and tap Reload
host.files["config.json"] = json:encode({ url = "https://books.example.com", token = "tok123" })
assert(host.tap(Draw.RECTS.setup_rld_btn))
host.flush()
host.draw()

-- 3. Tap Get on row 1 (direct API 2.18 save={to="files"})
local r1 = Draw.row_rect(1, 136, 94)
assert(host.tap(Draw.row_action_rect(r1)))
host.flush()
assert(host.files["Direct Book.epub"] == "EPUB_DIRECT_BINARY_DATA!", "Direct download did not save")
-- Confirm removing from queue
host.confirm(true)
host.flush()
assert(removed_qids[1] == "q1", "Queue item q1 should have been removed")

-- 4. Tap row 2 -> Book Detail -> Download (chunked mode)
local r2 = Draw.row_rect(2, 136, 94)
assert(host.tap({ x = r2.x + 20, y = r2.y + 20, w = 40, h = 40 }))
host.draw() -- Detail screen
assert(host.tap(Draw.RECTS.dl_primary))
host.draw() -- Downloading screen
host.flush()
assert(host.files["Large Atlas.epub"] == big_payload, "Chunked download bytes mismatch")
host.confirm(false) -- Keep in queue
host.draw()

-- 5. Switch to Search tab -> search "Marcus" -> draw results
assert(host.tap(Draw.RECTS.tab_search))
host.draw()
assert(host.tap(Draw.RECTS.search_box))
host.submit_input("Marcus")
host.flush()
host.draw()

-- 6. Switch to Files tab -> verify config.json is hidden and delete works
assert(host.tap(Draw.RECTS.tab_files))
host.draw()
local f1 = Draw.row_rect(1, 196, 102)
assert(host.tap(Draw.row_action_rect(f1)))
host.confirm(true)
host.draw()
assert(host.files["Direct Book.epub"] == nil, "File should have been deleted")
assert(host.files["config.json"] ~= nil, "config.json must never be deleted from Files tab")

-- 7. Settings -> Test Connection -> Exit
assert(host.tap(Draw.RECTS.settings_btn))
host.draw()
assert(host.tap(Draw.RECTS.cfg_test_btn))
host.flush()
host.draw()
assert(host.tap(Draw.RECTS.back_btn))
assert(host.tap(Draw.RECTS.exit_btn))
assert(host.was_quit())

on_leave(host.ctx)
print("HOST MOCK lua tests: ALL PASSED")
