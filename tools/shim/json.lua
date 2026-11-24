-- Deterministic pure-Lua JSON for HOST MOCK, API §10.
local J={null={}}
local function quote(s)
  assert(utf8.len(s),'invalid UTF-8')
  return '"'..s:gsub('[%z\1-\31\\"]',function(c)
    return ({['"']='\\"',['\\']='\\\\',['\n']='\\n',['\r']='\\r',['\t']='\\t'})[c] or string.format('\\u%04x',c:byte())
  end)..'"'
end
local function encode(v,seen,depth)
  assert(depth<=32)
  if v==J.null then return 'null' end
  local t=type(v)
  if t=='string' then return quote(v) end
  if t=='boolean' then return tostring(v) end
  if t=='number' then assert(v==v and v~=math.huge and v~=-math.huge);return tostring(v) end
  assert(t=='table' and not seen[v]);seen[v]=true
  local keys,n,array={},0,true
  for k in pairs(v) do n=n+1;keys[n]=k;if type(k)~='number' or math.type(k)~='integer' or k<1 then array=false end end
  if array then for i=1,n do if v[i]==nil then array=false end end end
  local out={}
  if array then for i=1,n do out[i]=encode(v[i],seen,depth+1) end
  else
    for _,k in ipairs(keys) do assert(type(k)=='string') end
    table.sort(keys)
    for i,k in ipairs(keys) do out[i]=quote(k)..':'..encode(v[k],seen,depth+1) end
  end
  seen[v]=nil;return (array and '[' or '{')..table.concat(out,',')..(array and ']' or '}')
end
function J:encode(v)
  local ok,s=pcall(encode,v,{},0)
  if not ok then return nil,'invalid_argument' end
  if #s>262144 then return nil,'limit_exceeded' end
  return s
end
function J:decode(s)
  if type(s)~='string' then error('string required') end
  local p=1
  local function space() local _,e=s:find('^[ \n\r\t]*',p);p=(e or p-1)+1 end
  local function str()
    assert(s:sub(p,p)=='"');p=p+1;local out={}
    while p<=#s do
      local c=s:sub(p,p);p=p+1
      if c=='"' then local v=table.concat(out);assert(utf8.len(v));return v end
      if c=='\\' then
        c=s:sub(p,p);p=p+1
        local esc={['"']='"',['\\']='\\',['/']='/',b='\b',f='\f',n='\n',r='\r',t='\t'}
        if c=='u' then
          local hex=s:sub(p,p+3);assert(hex:match('^%x%x%x%x$'));local n=tonumber(hex,16);p=p+4
          if n>=0xD800 and n<=0xDBFF then
            assert(s:sub(p,p+1)=='\\u');local low=tonumber(s:sub(p+2,p+5),16);assert(low and low>=0xDC00 and low<=0xDFFF);p=p+6;n=0x10000+(n-0xD800)*1024+low-0xDC00
          end
          assert(n<0xD800 or n>0xDFFF);c=utf8.char(n)
        else c=assert(esc[c]) end
      else assert(c:byte()>=32) end
      out[#out+1]=c
    end
    error('unterminated string')
  end
  local value
  value=function(depth)
    assert(depth<=32);space();local c=s:sub(p,p)
    if c=='"' then return str() end
    if c=='[' or c=='{' then
      p=p+1;local t={};local close=c=='[' and ']' or '}';space()
      if s:sub(p,p)==close then p=p+1;return t end
      while true do
        local k=#t+1
        if c=='{' then space();k=str();space();assert(s:sub(p,p)==':');p=p+1 end
        t[k]=value(depth+1);space();local sep=s:sub(p,p);p=p+1
        if sep==close then return t end;assert(sep==',')
      end
    end
    for token,v in pairs({['true']=true,['false']=false,['null']=J.null}) do
      if s:sub(p,p+#token-1)==token then p=p+#token;return v end
    end
    local start=p
    if c=='-' then p=p+1 end
    if s:sub(p,p)=='0' then p=p+1 else assert(s:sub(p,p):match('[1-9]'));repeat p=p+1 until not s:sub(p,p):match('%d') end
    if s:sub(p,p)=='.' then p=p+1;assert(s:sub(p,p):match('%d'));repeat p=p+1 until not s:sub(p,p):match('%d') end
    if s:sub(p,p):match('[eE]') then p=p+1;if s:sub(p,p):match('[+-]') then p=p+1 end;assert(s:sub(p,p):match('%d'));repeat p=p+1 until not s:sub(p,p):match('%d') end
    return assert(tonumber(s:sub(start,p-1)))
  end
  local ok,v=pcall(function() local v=value(0);space();assert(p>#s);return v end)
  if not ok then return nil,'invalid_argument' end;return v
end
return J
