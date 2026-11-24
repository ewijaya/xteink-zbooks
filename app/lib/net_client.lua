-- Networking client for the Z-Books companion bridge (API 2.18 GET/POST only)
local U = require("lib.core.util")

local M = {}
M.__index = M

function M.new()
  return setmetatable({
    job = nil,
    cancelled = false,
  }, M)
end

function M:is_busy()
  return self.job ~= nil
end

function M:cancel()
  self.cancelled = true
  if self.job then
    pcall(function()
      self.job:cancel()
    end)
    self.job = nil
  end
end

local function make_headers(token, is_json)
  local h = {}
  if token and token ~= "" then
    h["authorization"] = "Bearer " .. token
  end
  if is_json then
    h["content-type"] = "application/json"
  end
  return h
end

local function clock_valid(ctx)
  if ctx.clock and ctx.clock.now then
    local now = ctx.clock:now()
    if type(now) == "table" and now.valid ~= nil then
      return now.valid
    end
  end
  return true
end

local function parse_error_body(ctx, res)
  if res and type(res.body) == "string" and res.body ~= "" then
    local ok, decoded = pcall(function()
      return ctx.json:decode(res.body)
    end)
    if ok and type(decoded) == "table" and type(decoded.error) == "string" and decoded.error ~= "" then
      return decoded.error
    end
  end
  return U.friendly_net_error(res, clock_valid(ctx))
end

function M:request_json(ctx, cfg, method, path, body_tbl, timeout_ms, cb)
  if not ctx.net then
    cb(nil, "Network permission unavailable")
    return false
  end
  if not U.is_valid_url(cfg.url) then
    cb(nil, "Server URL is not configured")
    return false
  end
  if self.job then
    cb(nil, "Another request is already running")
    return false
  end

  local opts = {
    url = U.clean_url(cfg.url) .. path,
    method = method or "GET",
    headers = make_headers(cfg.token, body_tbl ~= nil),
    timeout_ms = timeout_ms or 30000,
  }
  if body_tbl ~= nil then
    opts.body = ctx.json:encode(body_tbl)
  end

  self.cancelled = false
  local job, err = ctx.net:request(opts, function(cb_ctx, res)
    self.job = nil
    if self.cancelled then
      return
    end
    if not res or not res.ok then
      cb(nil, U.friendly_net_error(res, clock_valid(cb_ctx)))
      return
    end
    if res.status < 200 or res.status >= 300 then
      cb(nil, parse_error_body(cb_ctx, res))
      return
    end
    local ok, decoded = pcall(function()
      return cb_ctx.json:decode(res.body or "{}")
    end)
    if not ok or type(decoded) ~= "table" then
      cb(nil, "Invalid JSON from bridge")
      return
    end
    if decoded.ok == false then
      cb(nil, decoded.error or "Bridge returned error")
      return
    end
    cb(decoded, nil)
  end)

  if not job then
    cb(nil, "Request start failed: " .. tostring(err))
    return false
  end
  self.job = job
  return true
end

function M:fetch_status(ctx, cfg, cb)
  return self:request_json(ctx, cfg, "GET", "/api/status", nil, 20000, cb)
end

function M:fetch_queue(ctx, cfg, cb)
  return self:request_json(ctx, cfg, "GET", "/api/queue", nil, 20000, cb)
end

function M:search(ctx, cfg, query, ext, page, cb)
  local enc_q = ctx.url and ctx.url.encode and ctx.url:encode(query) or query
  local enc_ext = ctx.url and ctx.url.encode and ctx.url:encode(ext or "epub") or (ext or "epub")
  local path = "/api/search?q=" .. enc_q .. "&ext=" .. enc_ext .. "&page=" .. tostring(page or 1) .. "&count=8"
  return self:request_json(ctx, cfg, "GET", path, nil, 45000, cb)
end

function M:queue_add(ctx, cfg, item, cb)
  local payload = {
    id = item.id,
    title = item.title,
    author = item.author,
    year = item.year,
    ext = item.ext or "epub",
    size_bytes = item.size_bytes or 0,
    filename = item.filename or "",
  }
  return self:request_json(ctx, cfg, "POST", "/api/queue", payload, 20000, cb)
end

function M:queue_remove(ctx, cfg, qid, cb)
  return self:request_json(ctx, cfg, "POST", "/api/queue/remove", { qid = qid }, 20000, cb)
end

-- Two-stage download:
-- 1) POST /api/prepare -> returns { token, filename, size_bytes, mode, chunk_bytes }
-- 2) Direct (API >= 2.18 and <= 7.5 MB): GET /api/file/<token> with save={to="files", name=filename}
--    Chunked (> 7.5 MB or API < 2.18): loop GET /api/chunk/<token>?offset=... + ctx.files:append
function M:download_book(ctx, cfg, item, hooks)
  local on_progress = hooks.on_progress or function() end
  local on_done = hooks.on_done or function() end

  local req_body = {}
  if item.qid and item.qid ~= "" then
    req_body.qid = item.qid
  else
    req_body.id = item.id
    req_body.filename = item.filename or ""
  end

  on_progress("Preparing book on server...", 0, item.size_bytes or 0)

  return self:request_json(ctx, cfg, "POST", "/api/prepare", req_body, 60000, function(prep, err)
    if err or not prep then
      on_done(false, nil, err or "Prepare failed")
      return
    end
    local filename = prep.filename or item.filename or "book.epub"
    local total_bytes = tonumber(prep.size_bytes) or 0
    local dl_token = prep.token
    local can_direct = (ctx.api and (ctx.api.minor or 0) >= 18) and (prep.mode == "direct")

    if can_direct then
      on_progress("Downloading " .. U.format_bytes(total_bytes) .. "...", 0, total_bytes)
      local url = U.clean_url(cfg.url) .. "/api/file/" .. dl_token
      self.cancelled = false
      local job, rerr = ctx.net:request({
        url = url,
        method = "GET",
        headers = make_headers(cfg.token, false),
        save = { to = "files", name = filename },
        timeout_ms = 60000,
      }, function(cb_ctx, res)
        self.job = nil
        if self.cancelled then
          return
        end
        if res and res.ok and res.saved then
          on_done(true, filename, nil)
        else
          on_done(false, nil, parse_error_body(cb_ctx, res))
        end
      end)
      if not job then
        on_done(false, nil, "Download start failed: " .. tostring(rerr))
        return
      end
      self.job = job
      return
    end

    -- Chunked download path (supports > 8 MB files and API 2.9-2.17)
    local chunk_len = math.min(tonumber(prep.chunk_bytes) or U.CHUNK_BYTES, 114688)
    ctx.files:remove(filename)

    local function fetch_chunk(cb_ctx, offset)
      if self.cancelled then
        ctx.files:remove(filename)
        return
      end
      if offset >= total_bytes and total_bytes > 0 then
        on_done(true, filename, nil)
        return
      end
      local pct = total_bytes > 0 and math.floor((offset * 100) / total_bytes) or 0
      on_progress(
        string.format("Downloading %s / %s (%d%%)", U.format_bytes(offset), U.format_bytes(total_bytes), pct),
        offset,
        total_bytes
      )
      local url = U.clean_url(cfg.url)
        .. "/api/chunk/" .. dl_token
        .. "?offset=" .. tostring(offset)
        .. "&len=" .. tostring(chunk_len)

      local job, rerr = cb_ctx.net:request({
        url = url,
        method = "GET",
        headers = make_headers(cfg.token, false),
        timeout_ms = 30000,
      }, function(done_ctx, res)
        self.job = nil
        if self.cancelled then
          done_ctx.files:remove(filename)
          return
        end
        if not res or not res.ok or res.status < 200 or res.status >= 300 then
          done_ctx.files:remove(filename)
          on_done(false, nil, parse_error_body(done_ctx, res))
          return
        end
        local body = res.body or ""
        if #body == 0 then
          if offset >= total_bytes then
            on_done(true, filename, nil)
          else
            done_ctx.files:remove(filename)
            on_done(false, nil, "Unexpected end of file")
          end
          return
        end
        local wrote, werr = done_ctx.files:append(filename, body)
        if not wrote then
          done_ctx.files:remove(filename)
          on_done(false, nil, "SD write failed: " .. tostring(werr))
          return
        end
        local next_offset = offset + #body
        if next_offset >= total_bytes then
          on_done(true, filename, nil)
        else
          done_ctx:after(10, function(next_ctx)
            fetch_chunk(next_ctx, next_offset)
          end)
        end
      end)

      if not job then
        cb_ctx.files:remove(filename)
        on_done(false, nil, "Chunk start failed: " .. tostring(rerr))
        return
      end
      self.job = job
    end

    fetch_chunk(ctx, 0)
  end)
end

return M
