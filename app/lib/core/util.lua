-- Pure utility helpers (no ctx dependencies)
local M = {}

M.VERSION = "0.1.0"
M.CONFIG_FILE = "config.json"
M.PAGE_SIZE = 6
M.CHUNK_BYTES = 98304

function M.trim(s)
  if type(s) ~= "string" then
    return ""
  end
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

function M.clean_url(url)
  local u = M.trim(url)
  u = u:gsub("/+$", "")
  return u
end

function M.is_valid_url(url)
  local u = M.clean_url(url)
  return u:match("^https?://.+") ~= nil
end

function M.mask_token(tok)
  local t = M.trim(tok)
  if t == "" then
    return "(not set)"
  end
  if #t <= 8 then
    return "****"
  end
  return t:sub(1, 4) .. "..." .. t:sub(-4)
end

function M.format_bytes(n)
  local num = tonumber(n) or 0
  if num <= 0 then
    return ""
  end
  if num < 1024 then
    return tostring(math.floor(num)) .. " B"
  end
  if num < 1048576 then
    return string.format("%d KB", math.floor((num + 512) / 1024))
  end
  return string.format("%.1f MB", num / 1048576)
end

function M.ellipsize(s, max_chars)
  if type(s) ~= "string" then
    return ""
  end
  local limit = max_chars or 36
  if #s <= limit then
    return s
  end
  -- Walk back to a valid UTF-8 character boundary
  local cut = limit - 3
  if cut < 1 then
    return "..."
  end
  while cut > 1 do
    local b = string.byte(s, cut + 1)
    if not b or b < 0x80 or b >= 0xC0 then
      break
    end
    cut = cut - 1
  end
  return s:sub(1, cut) .. "..."
end

function M.book_meta_line(item)
  local parts = {}
  if item.author and item.author ~= "" then
    parts[#parts + 1] = M.ellipsize(item.author, 28)
  end
  if item.year and item.year ~= "" then
    parts[#parts + 1] = tostring(item.year)
  end
  local sz = item.size
  if (not sz or sz == "") and item.size_bytes and item.size_bytes > 0 then
    sz = M.format_bytes(item.size_bytes)
  end
  if sz and sz ~= "" then
    parts[#parts + 1] = sz
  end
  if item.ext and item.ext ~= "" then
    parts[#parts + 1] = string.upper(item.ext)
  end
  if #parts == 0 then
    return "EPUB"
  end
  return table.concat(parts, " | ")
end

function M.filter_book_files(list)
  local out = {}
  if type(list) ~= "table" then
    return out
  end
  for i = 1, #list do
    local entry = list[i]
    if entry and type(entry.name) == "string" then
      local lower = string.lower(entry.name)
      if lower ~= M.CONFIG_FILE then
        out[#out + 1] = entry
      end
    end
  end
  return out
end

function M.slice_page(items, page, page_size)
  local ps = page_size or M.PAGE_SIZE
  local total = #items
  local total_pages = math.max(1, math.floor((total + ps - 1) / ps))
  local p = math.max(1, math.min(page or 1, total_pages))
  local start_i = (p - 1) * ps + 1
  local end_i = math.min(total, p * ps)
  local out = {}
  for i = start_i, end_i do
    out[#out + 1] = items[i]
  end
  return out, p, total_pages, start_i
end

function M.friendly_net_error(res, clock_valid)
  if not res then
    return "Network request failed"
  end
  if res.err == "no_network" then
    return "No Wi-Fi connection. Check Settings -> Wi-Fi."
  end
  if res.err == "timeout" then
    return "Request timed out. Server may be busy."
  end
  if res.err == "limit_exceeded" then
    return "File exceeded device size or storage limit."
  end
  if res.err == "out_of_memory" then
    return "Not enough memory for response."
  end
  if res.err == "io_error" then
    if clock_valid == false then
      return "HTTPS failed (clock unset). Sync time over Wi-Fi."
    end
    return "Connection failed. Check server URL and network."
  end
  if res.status == 401 then
    return "Unauthorized (401). Check your API token."
  end
  if res.status == 404 then
    return "Not found (404) on server."
  end
  if res.status and res.status >= 400 then
    return "Server error (" .. tostring(res.status) .. ")"
  end
  return res.err or "Request failed"
end

function M.in_rect(x, y, r)
  return x >= r.x and x < (r.x + r.w) and y >= r.y and y < (r.y + r.h)
end

return M
