local App, app, fatal

local function fail(ctx, code, detail)
  local msg = detail and (code .. ": " .. tostring(detail)) or code
  if fatal ~= msg then
    if ctx.log and ctx.log.error then
      ctx.log:error(msg)
    end
  end
  fatal = msg
  if app then
    pcall(app.leave, app, ctx)
  end
  ctx:invalidate("full")
end

local function guard(ctx, code, fn, ...)
  local ok, result = pcall(fn, ...)
  if not ok then
    fail(ctx, code, result)
    return false, nil
  end
  return true, result
end

function on_load(ctx)
  guard(ctx, "E-LOAD", function()
    if app then
      app:leave(ctx)
    end
    App = require("lib.app")
    app = App.new(ctx)
    fatal = nil
  end)
end

function on_draw(ctx, g)
  local ok, err = pcall(function()
    if app and not fatal then
      app:draw(ctx, g)
    else
      g:clear(0)
      g:rect(16, 16, 108, 56, { radius = 8, line_width = 2 })
      g:text(70, 32, "Exit", { font = ctx.fonts.medium, align = "center" })
      g:text(240, 260, "Z-Books Error", { font = ctx.fonts.large, align = "center", bold = true })
      g:text_box(32, 320, 416, 140, fatal or "E-LOAD", { font = ctx.fonts.small, align = "center" })
      g:rect(64, 520, 352, 72, { radius = 10, line_width = 3 })
      g:text(240, 542, "Reload", { font = ctx.fonts.large, align = "center", bold = true })
    end
  end)
  if not ok then
    fatal = "E-DRAW: " .. tostring(err)
    if ctx.log and ctx.log.error then
      ctx.log:error(fatal)
    end
  end
end

function on_input(ctx, ev)
  local ok, handled = guard(ctx, "E-INPUT", function()
    if fatal then
      if ev.type == "pointer" and ev.phase == "up" and ev.gesture == "tap" then
        if ev.x < 130 and ev.y < 84 then
          ctx:quit()
          return true
        end
        if ev.y >= 510 and ev.y <= 604 then
          on_load(ctx)
          return true
        end
      end
      if ev.type == "key" and ev.key == "back" and ev.phase == "up" then
        return false
      end
      return false
    end
    return app and app:input(ctx, ev) or false
  end)
  return ok and handled or false
end

function on_pause(ctx)
  guard(ctx, "E-PAUSE", function()
    if app then
      app:pause(ctx)
    end
  end)
end

function on_resume(ctx)
  guard(ctx, "E-RESUME", function()
    if app then
      app:resume(ctx)
    end
  end)
end

function on_leave(ctx)
  guard(ctx, "E-LEAVE", function()
    if app then
      app:leave(ctx)
    end
  end)
end
