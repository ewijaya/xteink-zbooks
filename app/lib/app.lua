-- Main Z-Books InkApp controller
local U = require("lib.core.util")
local Net = require("lib.net_client")
local Draw = require("lib.ui.draw")

local M = {}
M.__index = M

local function notify(ctx, text)
  if ctx.dialog and ctx.dialog.toast then
    ctx.dialog:toast(U.ellipsize(text, 90))
  end
end

function M.new(ctx)
  local self = setmetatable({
    net = Net.new(),
    cfg = { url = "", token = "" },
    screen = "main",     -- "setup" | "main" | "detail" | "downloading" | "settings"
    tab = "queue",       -- "queue" | "search" | "files"
    queue = {},
    queue_page = 1,
    query = "",
    search_ext = "epub",
    search_page = 1,
    search_has_more = false,
    search_results = {},
    search_done = false,
    recent_queries = {},
    files = {},
    files_page = 1,
    selected_row = 1,
    detail_item = nil,
    dl_item = nil,
    dl_status = "",
    dl_done = 0,
    dl_total = 0,
    loading = false,
    status_msg = nil,
    error_msg = nil,
    test_result = nil,
    last_key_ms = 0,
  }, M)

  if ctx.net and ctx.net.keep_wifi then
    ctx.net:keep_wifi(300000)
  end

  self:load_config(ctx)
  self:refresh_files(ctx)

  if U.is_valid_url(self.cfg.url) then
    self.screen = "main"
    self:load_queue(ctx)
  else
    self.screen = "setup"
  end

  return self
end

function M:load_config(ctx)
  -- Restore persisted state first
  if type(ctx.state) == "table" then
    if type(ctx.state.url) == "string" then
      self.cfg.url = U.clean_url(ctx.state.url)
    end
    if type(ctx.state.token) == "string" then
      self.cfg.token = U.trim(ctx.state.token)
    end
    if type(ctx.state.recent_queries) == "table" then
      self.recent_queries = ctx.state.recent_queries
    end
    if type(ctx.state.search_ext) == "string" then
      self.search_ext = ctx.state.search_ext
    end
  end

  -- AppFiles/zbooks/config.json overrides stored state when present
  if ctx.files and ctx.files:exists(U.CONFIG_FILE) then
    local raw = ctx.files:read(U.CONFIG_FILE, 0, 8192)
    if type(raw) == "string" and raw ~= "" then
      local ok, data = pcall(function()
        return ctx.json:decode(raw)
      end)
      if ok and type(data) == "table" then
        if type(data.url) == "string" and U.trim(data.url) ~= "" then
          self.cfg.url = U.clean_url(data.url)
        end
        if type(data.token) == "string" then
          self.cfg.token = U.trim(data.token)
        end
        self:save_state_fields(ctx)
      end
    end
  end
end

function M:save_state_fields(ctx)
  if type(ctx.state) == "table" then
    ctx.state.url = self.cfg.url
    ctx.state.token = self.cfg.token
    ctx.state.recent_queries = self.recent_queries
    ctx.state.search_ext = self.search_ext
    if ctx.save_state then
      pcall(function() ctx:save_state() end)
    end
  end
end

function M:persist_config(ctx)
  self:save_state_fields(ctx)
  if ctx.files and U.is_valid_url(self.cfg.url) then
    local encoded = ctx.json:encode({
      url = self.cfg.url,
      token = self.cfg.token,
    })
    if encoded then
      ctx.files:write(U.CONFIG_FILE, encoded .. "\n")
    end
  end
end

function M:refresh_files(ctx)
  if not ctx.files then
    self.files = {}
    return
  end
  local raw = ctx.files:list()
  self.files = U.filter_book_files(raw)
end

function M:record_recent_query(ctx, q)
  local trimmed = U.trim(q)
  if trimmed == "" then
    return
  end
  local next_list = { trimmed }
  for i = 1, #self.recent_queries do
    if self.recent_queries[i] ~= trimmed and #next_list < 6 then
      next_list[#next_list + 1] = self.recent_queries[i]
    end
  end
  self.recent_queries = next_list
  self:save_state_fields(ctx)
end

function M:load_queue(ctx)
  self.net:cancel()
  self.loading = true
  self.error_msg = nil
  self.status_msg = "Syncing queue..."
  ctx:invalidate()

  self.net:fetch_queue(ctx, self.cfg, function(data, err)
    self.loading = false
    self.status_msg = nil
    if err or not data then
      self.error_msg = err or "Failed to load queue"
    else
      self.queue = type(data.items) == "table" and data.items or {}
      self.queue_page = 1
      self.selected_row = 1
      self.error_msg = nil
    end
    self:refresh_files(ctx)
    ctx:invalidate()
  end)
end

function M:run_search(ctx, query, page)
  local q = U.trim(query)
  if q == "" then
    return
  end
  self.net:cancel()
  self.query = q
  self.search_page = page or 1
  self.loading = true
  self.error_msg = nil
  self.search_done = false
  self.status_msg = "Searching '" .. U.ellipsize(q, 20) .. "'..."
  self:record_recent_query(ctx, q)
  ctx:invalidate()

  self.net:search(ctx, self.cfg, q, self.search_ext, self.search_page, function(data, err)
    self.loading = false
    self.status_msg = nil
    self.search_done = true
    if err or not data then
      self.error_msg = err or "Search failed"
      self.search_results = {}
      self.search_has_more = false
    else
      self.search_results = type(data.items) == "table" and data.items or {}
      self.search_has_more = data.has_more == true
      self.selected_row = 1
      self.error_msg = nil
    end
    ctx:invalidate()
  end)
end

function M:start_download(ctx, item)
  if not item then
    return
  end
  self.net:cancel()
  self.screen = "downloading"
  self.dl_item = item
  self.dl_status = "Preparing on server..."
  self.dl_done = 0
  self.dl_total = tonumber(item.size_bytes) or 0
  if ctx.device and ctx.device.keep_awake then
    pcall(function() ctx.device:keep_awake(300000) end)
  end
  ctx:invalidate("full")

  local last_pct = -1
  self.net:download_book(ctx, self.cfg, item, {
    on_progress = function(msg, done_b, total_b)
      self.dl_status = msg
      self.dl_done = done_b or 0
      self.dl_total = total_b or self.dl_total
      local pct = self.dl_total > 0 and math.floor((self.dl_done * 100) / self.dl_total) or 0
      -- Avoid excessive e-ink redraws on huge chunked downloads
      if last_pct < 0 or pct >= last_pct + 10 or done_b == 0 then
        last_pct = pct
        ctx:invalidate()
      end
    end,
    on_done = function(ok, saved_name, err)
      self:refresh_files(ctx)
      if ok then
        self.screen = "main"
        self.dl_item = nil
        notify(ctx, "Saved: " .. U.ellipsize(saved_name or "book.epub", 40))
        ctx:invalidate("full")
        if item.qid and item.qid ~= "" and ctx.dialog and ctx.dialog.confirm then
          ctx.dialog:confirm({
            title = "Book Saved",
            message = "Saved to AppFiles/zbooks.\nRemove from server queue?",
            ok_text = "Remove",
            cancel_text = "Keep",
          }, function(c_ctx, confirmed)
            if confirmed then
              self:remove_queue_item(c_ctx, item.qid)
            end
          end)
        end
      else
        self.screen = "detail"
        self.detail_item = item
        self.dl_item = nil
        notify(ctx, err or "Download failed")
        ctx:invalidate("full")
      end
    end,
  })
end

function M:remove_queue_item(ctx, qid)
  if not qid or qid == "" then
    return
  end
  self.net:cancel()
  self.net:queue_remove(ctx, self.cfg, qid, function(data, err)
    if err then
      notify(ctx, err)
    else
      notify(ctx, "Removed from queue")
      if self.screen == "detail" then
        self.screen = "main"
      end
      self:load_queue(ctx)
    end
    ctx:invalidate()
  end)
end

function M:add_to_queue(ctx, item)
  self.net:cancel()
  self.net:queue_add(ctx, self.cfg, item, function(data, err)
    if err then
      notify(ctx, err)
    else
      notify(ctx, "Added to Queue")
    end
    ctx:invalidate()
  end)
end

function M:test_connection(ctx)
  self.net:cancel()
  self.loading = true
  self.test_result = "Connecting to bridge..."
  ctx:invalidate()

  self.net:fetch_status(ctx, self.cfg, function(data, err)
    self.loading = false
    if err or not data then
      self.test_result = "Failed:\n" .. (err or "Unknown error")
    else
      local lines = {
        "Bridge OK (v" .. tostring(data.version or "?") .. ")",
        "Queue: " .. tostring(data.queue_count or 0) .. " book(s)",
        "Z-Library: " .. (data.zlib_logged_in and "Logged in" or ("Not logged in" .. (data.zlib_error and data.zlib_error ~= "" and (" (" .. data.zlib_error .. ")") or ""))),
      }
      if type(data.quota) == "table" then
        local left = data.quota.daily_remaining
        if left == nil then
          left = data.quota.downloads_left
        end
        if left ~= nil then
          lines[#lines + 1] = "Daily quota left: " .. tostring(left)
        end
      end
      self.test_result = table.concat(lines, "\n")
    end
    ctx:invalidate()
  end)
end

function M:open_search_input(ctx)
  if not (ctx.input and ctx.input.text) then
    notify(ctx, "Keyboard unavailable")
    return
  end
  ctx.input:text({
    title = "Search Z-Library",
    text = self.query or "",
    max_bytes = 96,
  }, function(cb_ctx, text)
    if text and U.trim(text) ~= "" then
      self:run_search(cb_ctx, text, 1)
    else
      cb_ctx:invalidate()
    end
  end)
end

function M:edit_url(ctx)
  ctx.input:text({
    title = "Bridge URL (https://...)",
    text = self.cfg.url or "https://",
    max_bytes = 192,
  }, function(cb_ctx, text)
    if text then
      self.cfg.url = U.clean_url(text)
      self:persist_config(cb_ctx)
      notify(cb_ctx, "URL updated")
      cb_ctx:invalidate()
    end
  end)
end

function M:edit_token(ctx)
  ctx.input:text({
    title = "Bridge API Token",
    text = self.cfg.token or "",
    max_bytes = 128,
    secret = false,
  }, function(cb_ctx, text)
    if text then
      self.cfg.token = U.trim(text)
      self:persist_config(cb_ctx)
      notify(cb_ctx, "Token updated")
      cb_ctx:invalidate()
    end
  end)
end

function M:confirm_delete_file(ctx, filename)
  if not (ctx.dialog and ctx.dialog.confirm) then
    ctx.files:remove(filename)
    self:refresh_files(ctx)
    ctx:invalidate()
    return
  end
  ctx.dialog:confirm({
    title = "Delete Book?",
    message = U.ellipsize(filename, 80),
    ok_text = "Delete",
    cancel_text = "Cancel",
  }, function(cb_ctx, ok)
    if ok then
      cb_ctx.files:remove(filename)
      self:refresh_files(cb_ctx)
      notify(cb_ctx, "Deleted")
      cb_ctx:invalidate()
    end
  end)
end

function M:draw(ctx, g)
  Draw.draw(ctx, g, self)
end

-- ── Input handling ────────────────────────────────────────────────────────

function M:handle_main_tap(ctx, x, y)
  local R = Draw.RECTS

  -- Top bar
  if U.in_rect(x, y, R.exit_btn) then
    ctx:quit()
    return true
  end
  if U.in_rect(x, y, R.settings_btn) then
    self.net:cancel()
    self.loading = false
    self.screen = "settings"
    ctx:invalidate("full")
    return true
  end

  -- Tabs
  if U.in_rect(x, y, R.tab_queue) then
    if self.tab ~= "queue" then
      self.tab = "queue"
      self.selected_row = 1
      self:load_queue(ctx)
    end
    return true
  end
  if U.in_rect(x, y, R.tab_search) then
    if self.tab ~= "search" then
      self.net:cancel()
      self.loading = false
      self.error_msg = nil
      self.tab = "search"
      self.selected_row = 1
      ctx:invalidate("full")
    end
    return true
  end
  if U.in_rect(x, y, R.tab_files) then
    if self.tab ~= "files" then
      self.net:cancel()
      self.loading = false
      self.error_msg = nil
      self.tab = "files"
      self.selected_row = 1
      self:refresh_files(ctx)
      ctx:invalidate("full")
    end
    return true
  end

  -- Tab-specific body and footer
  if self.tab == "queue" then
    return self:handle_queue_tap(ctx, x, y)
  elseif self.tab == "search" then
    return self:handle_search_tap(ctx, x, y)
  else
    return self:handle_files_tap(ctx, x, y)
  end
end

function M:handle_queue_tap(ctx, x, y)
  local R = Draw.RECTS
  local slice, p, total_pages = U.slice_page(self.queue, self.queue_page, U.PAGE_SIZE)

  if U.in_rect(x, y, R.action_btn) then
    self:load_queue(ctx)
    return true
  end
  if U.in_rect(x, y, R.prev_btn) and p > 1 then
    self.queue_page = p - 1
    self.selected_row = 1
    ctx:invalidate()
    return true
  end
  if U.in_rect(x, y, R.next_btn) and p < total_pages then
    self.queue_page = p + 1
    self.selected_row = 1
    ctx:invalidate()
    return true
  end

  if self.loading or self.error_msg then
    return true
  end

  for i = 1, #slice do
    local r = Draw.row_rect(i, 136, 94)
    if U.in_rect(x, y, r) then
      local item = slice[i]
      self.selected_row = i
      if U.in_rect(x, y, Draw.row_action_rect(r)) then
        self:start_download(ctx, item)
      else
        self.detail_item = item
        self.screen = "detail"
        ctx:invalidate("full")
      end
      return true
    end
  end
  return false
end

function M:handle_search_tap(ctx, x, y)
  local R = Draw.RECTS

  if U.in_rect(x, y, R.search_box) then
    self:open_search_input(ctx)
    return true
  end
  if U.in_rect(x, y, R.ext_btn) then
    self.search_ext = (self.search_ext == "epub") and "all" or "epub"
    self:save_state_fields(ctx)
    if self.query ~= "" and self.search_done then
      self:run_search(ctx, self.query, 1)
    else
      ctx:invalidate()
    end
    return true
  end

  if self.loading then
    if U.in_rect(x, y, R.action_btn) then
      self.net:cancel()
      self.loading = false
      ctx:invalidate()
    end
    return true
  end

  if #self.search_results == 0 then
    if U.in_rect(x, y, R.action_btn) then
      if self.error_msg and self.query ~= "" then
        self:run_search(ctx, self.query, self.search_page)
      else
        self:open_search_input(ctx)
      end
      return true
    end
    -- Recent queries
    if not self.search_done and not self.error_msg then
      for i = 1, math.min(4, #self.recent_queries) do
        local rr = Draw.row_rect(i, 410, 66)
        if U.in_rect(x, y, rr) then
          self:run_search(ctx, self.recent_queries[i], 1)
          return true
        end
      end
    end
    return false
  end

  -- Pager
  if U.in_rect(x, y, R.prev_btn) and self.search_page > 1 then
    self:run_search(ctx, self.query, self.search_page - 1)
    return true
  end
  if U.in_rect(x, y, R.next_btn) and self.search_has_more then
    self:run_search(ctx, self.query, self.search_page + 1)
    return true
  end

  -- Results rows
  local visible = math.min(5, #self.search_results)
  for i = 1, visible do
    local r = Draw.row_rect(i, 200, 102)
    if U.in_rect(x, y, r) then
      local item = self.search_results[i]
      self.selected_row = i
      if U.in_rect(x, y, Draw.row_action_rect(r)) then
        self:start_download(ctx, item)
      else
        self.detail_item = item
        self.screen = "detail"
        ctx:invalidate("full")
      end
      return true
    end
  end
  return false
end

function M:handle_files_tap(ctx, x, y)
  local R = Draw.RECTS
  local slice, p, total_pages = U.slice_page(self.files, self.files_page, 5)

  if U.in_rect(x, y, R.action_btn) then
    self:refresh_files(ctx)
    ctx:invalidate()
    return true
  end
  if U.in_rect(x, y, R.prev_btn) and p > 1 then
    self.files_page = p - 1
    self.selected_row = 1
    ctx:invalidate()
    return true
  end
  if U.in_rect(x, y, R.next_btn) and p < total_pages then
    self.files_page = p + 1
    self.selected_row = 1
    ctx:invalidate()
    return true
  end

  for i = 1, #slice do
    local r = Draw.row_rect(i, 196, 102)
    if U.in_rect(x, y, r) then
      self.selected_row = i
      if U.in_rect(x, y, Draw.row_action_rect(r)) then
        self:confirm_delete_file(ctx, slice[i].name)
      else
        notify(ctx, "Open in All files -> AppFiles/zbooks")
      end
      return true
    end
  end
  return false
end

function M:handle_detail_tap(ctx, x, y)
  local R = Draw.RECTS
  if U.in_rect(x, y, R.back_btn) then
    self.screen = "main"
    ctx:invalidate("full")
    return true
  end
  if U.in_rect(x, y, R.dl_primary) then
    self:start_download(ctx, self.detail_item)
    return true
  end
  if U.in_rect(x, y, R.dl_secondary) and self.detail_item then
    if self.detail_item.qid and self.detail_item.qid ~= "" then
      self:remove_queue_item(ctx, self.detail_item.qid)
    elseif self.detail_item.id and self.detail_item.id ~= "" then
      self:add_to_queue(ctx, self.detail_item)
    end
    return true
  end
  return false
end

function M:handle_settings_tap(ctx, x, y)
  local R = Draw.RECTS
  if U.in_rect(x, y, R.back_btn) then
    self.net:cancel()
    self.loading = false
    if U.is_valid_url(self.cfg.url) then
      self.screen = "main"
      if self.tab == "queue" and #self.queue == 0 then
        self:load_queue(ctx)
      else
        ctx:invalidate("full")
      end
    else
      self.screen = "setup"
      ctx:invalidate("full")
    end
    return true
  end
  if U.in_rect(x, y, R.cfg_url_btn) then
    self:edit_url(ctx)
    return true
  end
  if U.in_rect(x, y, R.cfg_token_btn) then
    self:edit_token(ctx)
    return true
  end
  if U.in_rect(x, y, R.cfg_test_btn) then
    self:test_connection(ctx)
    return true
  end
  if U.in_rect(x, y, R.cfg_reload_btn) then
    self:load_config(ctx)
    notify(ctx, U.is_valid_url(self.cfg.url) and "Reloaded config.json" or "No valid config.json found")
    ctx:invalidate()
    return true
  end
  if U.in_rect(x, y, R.cfg_wifi_btn) then
    if ctx.system and ctx.system:can_open("settings.wifi") then
      ctx.system:open("settings.wifi")
    else
      notify(ctx, "Open Wi-Fi from system Settings")
    end
    return true
  end
  return false
end

function M:handle_setup_tap(ctx, x, y)
  local R = Draw.RECTS
  if U.in_rect(x, y, R.exit_btn) then
    ctx:quit()
    return true
  end
  if U.in_rect(x, y, R.setup_cfg_btn) then
    self.screen = "settings"
    ctx:invalidate("full")
    return true
  end
  if U.in_rect(x, y, R.setup_rld_btn) then
    self:load_config(ctx)
    if U.is_valid_url(self.cfg.url) then
      self.screen = "main"
      notify(ctx, "Loaded config.json")
      self:load_queue(ctx)
    else
      notify(ctx, "Put config.json in /AppFiles/zbooks")
    end
    ctx:invalidate()
    return true
  end
  return false
end

function M:handle_side_keys(ctx, key)
  local now = ctx.clock and ctx.clock:millis() or 0
  if now > 0 and (now - self.last_key_ms) < 250 then
    return true
  end
  self.last_key_ms = now

  if self.screen ~= "main" or self.loading then
    return false
  end

  local visible_count = 0
  local active_list = nil
  if self.tab == "queue" then
    active_list = U.slice_page(self.queue, self.queue_page, U.PAGE_SIZE)
    visible_count = #active_list
  elseif self.tab == "search" then
    visible_count = math.min(5, #self.search_results)
    active_list = self.search_results
  elseif self.tab == "files" then
    active_list = U.slice_page(self.files, self.files_page, 5)
    visible_count = #active_list
  end

  if visible_count == 0 then
    return false
  end

  if key == "down" then
    self.selected_row = (self.selected_row % visible_count) + 1
    ctx:invalidate()
    return true
  elseif key == "up" then
    local chosen = active_list[self.selected_row]
    if chosen then
      if self.tab == "files" then
        self:confirm_delete_file(ctx, chosen.name)
      else
        self.detail_item = chosen
        self.screen = "detail"
        ctx:invalidate("full")
      end
    end
    return true
  end
  return false
end

function M:input(ctx, ev)
  -- Back / Home tap navigation
  if ev.type == "key" and ev.key == "back" then
    if ev.phase ~= "up" then
      return true
    end
    if self.screen == "downloading" then
      self.net:cancel()
      self.screen = "main"
      notify(ctx, "Download cancelled")
      ctx:invalidate("full")
      return true
    elseif self.screen == "detail" then
      self.screen = "main"
      ctx:invalidate("full")
      return true
    elseif self.screen == "settings" then
      self.net:cancel()
      self.loading = false
      self.screen = U.is_valid_url(self.cfg.url) and "main" or "setup"
      ctx:invalidate("full")
      return true
    end
    return false
  end

  -- Side buttons
  if ev.type == "key" and (ev.key == "up" or ev.key == "down") then
    if ev.phase == "down" then
      return self:handle_side_keys(ctx, ev.key)
    end
    return true
  end

  -- Pointer taps & swipes
  if ev.type == "pointer" and ev.phase == "up" then
    if ev.gesture == "swipe_left" and self.screen == "main" then
      if self.tab == "queue" then
        return self:handle_main_tap(ctx, Draw.RECTS.next_btn.x + 10, Draw.RECTS.next_btn.y + 10)
      elseif self.tab == "search" then
        return self:handle_search_tap(ctx, Draw.RECTS.next_btn.x + 10, Draw.RECTS.next_btn.y + 10)
      else
        return self:handle_files_tap(ctx, Draw.RECTS.next_btn.x + 10, Draw.RECTS.next_btn.y + 10)
      end
    elseif ev.gesture == "swipe_right" and self.screen == "main" then
      if self.tab == "queue" then
        return self:handle_queue_tap(ctx, Draw.RECTS.prev_btn.x + 10, Draw.RECTS.prev_btn.y + 10)
      elseif self.tab == "search" then
        return self:handle_search_tap(ctx, Draw.RECTS.prev_btn.x + 10, Draw.RECTS.prev_btn.y + 10)
      else
        return self:handle_files_tap(ctx, Draw.RECTS.prev_btn.x + 10, Draw.RECTS.prev_btn.y + 10)
      end
    end

    if ev.gesture ~= "tap" then
      return false
    end

    if self.screen == "setup" then
      return self:handle_setup_tap(ctx, ev.x, ev.y)
    elseif self.screen == "settings" then
      return self:handle_settings_tap(ctx, ev.x, ev.y)
    elseif self.screen == "detail" then
      return self:handle_detail_tap(ctx, ev.x, ev.y)
    elseif self.screen == "downloading" then
      if U.in_rect(ev.x, ev.y, Draw.RECTS.cancel_btn) then
        self.net:cancel()
        self.screen = "main"
        notify(ctx, "Download cancelled")
        ctx:invalidate("full")
        return true
      end
      return true
    else
      return self:handle_main_tap(ctx, ev.x, ev.y)
    end
  end

  return false
end

function M:pause(ctx)
  -- Keep download alive if dialog opens; nothing extra needed
end

function M:resume(ctx)
  ctx:invalidate()
end

function M:leave(ctx)
  self.net:cancel()
  self:save_state_fields(ctx)
end

return M
