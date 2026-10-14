//+------------------------------------------------------------------+
//| EAQuantBridge.mq5 — MT5Quant EA 桥（方案 B, P4 EB-2）             |
//|                                                                  |
//| 职责（EB-1 + EB-2）：                                             |
//|   1. 配对：一次性码 → POST /api/v1/ea/pair → node_key 存终端文件   |
//|   2. tick 上行：OnTimer 同步回环，copy_ticks 游标采集 + 时区 skew  |
//|      归一化 → POST /api/v1/ea/sync（X-EA-Key 认证）               |
//|   3. 账户上行（EB-2）：sync 携带真实账户快照（余额/净值/持仓），   |
//|      后端镜像到 run 行（模拟盘详情页展示真实 demo 账户状态）       |
//|   4. 命令下行（EB-2）：rates_req → CopyRates 应答（最新 N 根 /    |
//|      时间区间两种语义）；order_request EB-3 开放                  |
//|                                                                  |
//| 安装指引（必须先做，否则 WebRequest 静默失败 err=4014）：          |
//|   工具 → 选项 → Expert Advisors → 勾选「允许 WebRequest 列出的     |
//|   URL」，并把后端地址（默认 http://127.0.0.1:8000）加入列表。      |
//|   然后把本文件编译挂到目标品种图表，输入一次配对码。               |
//|                                                                  |
//| 已知坑（务必保持的写法）：                                        |
//|   * copy_ticks 必须显式 COPY_TICKS_ALL —— 缺省 flags 静默返回 -1； |
//|   * 采集用 OnTimer 轮询而非 OnTick 计数 —— WebRequest 同步阻塞，   |
//|     OnTick 事件会积压，游标轮询慢网也不丢 tick；                   |
//|   * tick 时间轴 = 服务器时区：发送前用 skew = TimeTradeServer() -  |
//|     TimeGMT() 归一化到 UTC 域，与后端游标对齐；                    |
//|   * bars 时间轴刻意不做 skew 归一 —— 与 python 网关 rates 路径     |
//|     同域（原始服务器标签秒），后端两端共享同一套游标/切窗逻辑；    |
//|   * node_key 不能存 GlobalVariable（只能存 double），落盘到        |
//|     MQL5\Files（配对一次，重启终端仍有效）。                       |
//|   * 断线指数退避 1s→30s，恢复后自动续追（游标补齐，不丢数据）。     |
//+------------------------------------------------------------------+
#property copyright "MT5Quant Platform"
#property link      ""
#property version   "1.10"
#property description "EA 桥：tick/账户上行 + rates/命令下行同步回环（EB-2）"

//--- 输入参数
input string InpBackendUrl     = "http://127.0.0.1:8000"; // 后端地址（需加入终端 WebRequest 白名单）
input string InpPairCode       = "";                      // 配对码（仅首次运行填写，配对成功后可留空）
input int    InpSyncIntervalMs = 500;                     // 同步回环间隔（毫秒）
input int    InpTimeoutMs      = 5000;                    // WebRequest 超时（毫秒）
input int    InpMaxTicksPerSync= 1000;                    // 单次 sync 最大 tick 行数

//--- 常量
#define KEY_FILE         "EAQuantBridge_node_key.txt"
#define BACKOFF_MIN_MS   1000
#define BACKOFF_MAX_MS   30000
#define EA_RATES_PAGE_MAX 5000   // 单次 rates 应答上限（与后端 EA_RATES_MAX_BARS 一致）

//--- 全局状态
string   g_nodeKey     = "";   // X-EA-Key 凭据（pair 后落盘）
long     g_lastSentMsc = 0;    // 采集游标（原始 server-tz 毫秒，CopyTicks 域）
long     g_skewMs      = 0;    // server-tz - UTC（毫秒），周期性刷新
long     g_backoffMs   = 0;    // 当前退避间隔
datetime g_nextSyncAt  = 0;    // 下次允许发起 sync 的本地时间
bool     g_paired      = false;

//+------------------------------------------------------------------+
//| 初始化                                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_chartSymbol_ok();
   if(InpSyncIntervalMs < 100)
     {
      Print("EAQuantBridge: 同步间隔过小（>=100ms），使用 100ms");
      EventSetMillisecondTimer(100);
     }
   else
      EventSetMillisecondTimer(InpSyncIntervalMs);

   g_nodeKey = LoadKey();
   g_paired  = (StringLen(g_nodeKey) > 0);
   if(g_paired)
      Print("EAQuantBridge: 已加载节点密钥（", _Symbol, "），进入同步回环");
   else if(StringLen(InpPairCode) > 0)
      DoPair();   // 首次配对（失败则退避重试）
   else
      Print("EAQuantBridge: 尚未配对 —— 请在输入参数中填写一次性配对码",
            "（管理端 网关节点页生成）");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| 反初始化                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
//| OnTick：刻意为空 —— 采集在 OnTimer（防 WebRequest 阻塞积压事件）   |
//+------------------------------------------------------------------+
void OnTick()
  {
  }

//+------------------------------------------------------------------+
//| 主回环：pair（如需）→ sync（tick 上行 + 命令下行）                 |
//+------------------------------------------------------------------+
void OnTimer()
  {
   // 周期刷新时区 skew（服务器时区坑；TimeTradeServer 秒级精度可接受）
   g_skewMs = ((long)TimeTradeServer() - (long)TimeGMT()) * 1000;

   if(!g_paired)
     {
      if(StringLen(InpPairCode) > 0 && TimeLocal() >= g_nextSyncAt)
         DoPair();
      return;
     }
   if(TimeLocal() < g_nextSyncAt)
      return;   // 退避窗口内

   DoSync();
  }

//+------------------------------------------------------------------+
//| 配对：一次性码 → node_key（落盘持久化）                            |
//+------------------------------------------------------------------+
void DoPair()
  {
   string body = "{\"pair_code\":\"" + JsonEscape(InpPairCode) + "\"" +
                 ",\"mt5_login\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) +
                 ",\"mt5_server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\"" +
                 ",\"terminal_info\":{\"mt5_version\":\"MT5 build " +
                 IntegerToString(TerminalInfoInteger(TERMINAL_BUILD)) + "\",\"os\":\"" +
                 JsonEscape(TerminalInfoString(TERMINAL_OS_VERSION)) + "\"}}";
   string resp = "";
   if(!HttpPost("/api/v1/ea/pair", body, false, resp))
      return;   // HttpPost 内部已处理退避与日志

   string key = ExtractJsonString(resp, "node_key");
   if(StringLen(key) < 32)
     {
      Print("EAQuantBridge: 配对响应缺少 node_key：", StringSubstr(resp, 0, 200));
      ScheduleRetry();
      return;
     }
   g_nodeKey = key;
   SaveKey(key);
   g_paired    = true;
   g_backoffMs = 0;
   g_lastSentMsc = 0;   // 新节点从头采集（后端游标会过滤历史 tick）
   Print("EAQuantBridge: 配对成功，node_key 已保存到 MQL5\\Files\\", KEY_FILE);
  }

//+------------------------------------------------------------------+
//| 同步回环：采集 → 上行 → 处理下行命令                              |
//+------------------------------------------------------------------+
void DoSync()
  {
   string ticksJson = CollectTicksJson();
   string body = "{\"cursor_last_msc\":" + IntegerToString(g_lastSentMsc) +
                 ",\"ticks\":{\"" + JsonEscape(_Symbol) + "\":" + ticksJson + "}" +
                 ",\"account\":" + CollectAccountJson() + "}";

   string resp = "";
   if(!HttpPost("/api/v1/ea/sync", body, true, resp))
      return;   // 退避已调度；游标未推进 → 恢复后自动补追

   g_backoffMs  = 0;   // 成功 → 恢复常规节奏
   g_nextSyncAt = TimeLocal() + (datetime)(InpSyncIntervalMs / 1000);

   HandleCommands(resp);
  }

//+------------------------------------------------------------------+
//| 采集自上次游标以来的 tick（显式 COPY_TICKS_ALL）→ JSON 数组        |
//| 返回 "[[msc,bid,ask],...]"；msc 已做 skew 归一（UTC 域）           |
//+------------------------------------------------------------------+
string CollectTicksJson()
  {
   MqlTick ticks[];
   long from = (g_lastSentMsc > 0) ? g_lastSentMsc + 1 : 0;
   int n = CopyTicks(_Symbol, ticks, COPY_TICKS_ALL, from, InpMaxTicksPerSync);
   if(n <= 0)
      return "[]";   // 无新 tick（0 与 -1 都按空处理，下一轮重试）

   // 时间升序保证（CopyTicks 返回按时间升序）
   long maxRawMsc = g_lastSentMsc;
   string parts = "";
   int emitted = 0;
   for(int i = 0; i < n; i++)
     {
      long rawMsc = (long)ticks[i].time_msc;
      if(rawMsc <= g_lastSentMsc)
         continue;                       // 游标去重（from 含端点语义差异兜底）
      if(emitted > 0)
         parts += ",";
      // skew 归一：server-tz → UTC 域（与后端 last_tick_msc 游标对齐）
      parts += "[" + IntegerToString((long)rawMsc - g_skewMs) +
               "," + DoubleToString(ticks[i].bid, _Digits) +
               "," + DoubleToString(ticks[i].ask, _Digits) + "]";
      if(rawMsc > maxRawMsc)
         maxRawMsc = rawMsc;
      emitted++;
     }
   if(emitted > 0)
      g_lastSentMsc = maxRawMsc;   // 仅在成功构建后推进；发送失败由上层保留
   return "[" + parts + "]";
  }

//+------------------------------------------------------------------+
//| 处理 sync 响应中的 commands（EB-2：rates_req 就绪）                |
//| 命令结构 {"type":"...","req_id":"...","params":{...}}（扁平）      |
//+------------------------------------------------------------------+
void HandleCommands(const string resp)
  {
   int cursor = 0;
   while(true)
     {
      int tp = StringFind(resp, "\"type\":\"", cursor);
      if(tp < 0)
         break;
      int tps = tp + StringLen("\"type\":\"");
      int tpe = StringFind(resp, "\"", tps);
      if(tpe < 0)
         break;
      string cmdType = StringSubstr(resp, tps, tpe - tps);

      int rp = StringFind(resp, "\"req_id\":\"", tpe);
      if(rp < 0)
         break;
      int rs = rp + StringLen("\"req_id\":\"");
      int re = StringFind(resp, "\"", rs);
      if(re < 0)
         break;
      string reqId = StringSubstr(resp, rs, re - rs);

      // params 为扁平 dict（symbol/timeframe/count/start/end）——首个 "}" 收尾
      int ps = StringFind(resp, "\"params\":", re);
      if(ps < 0)
         break;
      ps += StringLen("\"params\":");
      int pe = StringFind(resp, "}", ps);
      if(pe < 0)
         break;
      string params = StringSubstr(resp, ps, pe - ps + 1);
      cursor = pe + 1;

      if(cmdType == "rates_req")
         HandleRatesReq(reqId, params);
      else
         SendResult(reqId, false, "", "UNSUPPORTED_IN_EB2");
     }
  }

//+------------------------------------------------------------------+
//| rates_req：CopyRates 应答（EB-2）                                  |
//|   params.count>0          → 最新 N 根（启动快照，禁固定日历窗）     |
//|   params.start/end（秒）  → [start,end] 区间（poll 补数）           |
//| bars 的 time 保持原始服务器标签秒（与 python 网关 rates 同域，     |
//| 不做 skew 归一——后端两端共享同一套游标/切窗逻辑）。                |
//+------------------------------------------------------------------+
void HandleRatesReq(const string reqId, const string params)
  {
   string sym = ExtractJsonString(params, "symbol");
   if(StringLen(sym) <= 0)
      sym = _Symbol;
   ENUM_TIMEFRAMES tf = TfFromString(ExtractJsonString(params, "timeframe"));
   if(tf == PERIOD_CURRENT)
     {
      SendResult(reqId, false, "", "BAD_TIMEFRAME");
      return;
     }
   if(!SymbolSelect(sym, true))
     {
      SendResult(reqId, false, "", "SYMBOL_NOT_FOUND");
      return;
     }
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   MqlRates rates[];
   int n = 0;
   long count = ExtractJsonNumber(params, "count");
   if(count > 0)
     {
      // start_pos=0 = 含未收盘末根，后端 _select_history 只留已收盘 bar
      int want = (int)MathMin(count, EA_RATES_PAGE_MAX);
      n = CopyRates(sym, tf, 0, want, rates);
     }
   else
     {
      long startS = ExtractJsonNumber(params, "start");
      long endS   = ExtractJsonNumber(params, "end");
      if(startS <= 0 || endS <= startS)
        {
         SendResult(reqId, false, "", "BAD_RANGE");
         return;
        }
      // CopyRates 为闭区间：多出的未收盘末根由后端 fresh 过滤兜底
      n = CopyRates(sym, tf, (datetime)startS, (datetime)endS, rates);
     }
   if(n <= 0)
     {
      // 空应答（休市/游标已到最新）不算错误
      SendResult(reqId, true,
                 "{\"bars\":[],\"symbol_info\":" + BuildSymbolInfoJson(sym) + "}", "");
      return;
     }
   int from = 0;
   if(n > EA_RATES_PAGE_MAX)
      from = n - EA_RATES_PAGE_MAX;   // 超页保留最新一段
   string payload = "{\"bars\":" + BuildBarsJson(rates, n, digits, from) +
                    ",\"symbol_info\":" + BuildSymbolInfoJson(sym) + "}";
   SendResult(reqId, true, payload, "");
  }

//+------------------------------------------------------------------+
//| timeframe 字符串 → ENUM_TIMEFRAMES（未知返回 PERIOD_CURRENT 哨兵）  |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES TfFromString(const string s)
  {
   if(s == "M1")  return PERIOD_M1;
   if(s == "M5")  return PERIOD_M5;
   if(s == "M15") return PERIOD_M15;
   if(s == "M30") return PERIOD_M30;
   if(s == "H1")  return PERIOD_H1;
   if(s == "H4")  return PERIOD_H4;
   if(s == "D1")  return PERIOD_D1;
   if(s == "W1")  return PERIOD_W1;
   if(s == "MN")  return PERIOD_MN1;
   return PERIOD_CURRENT;
  }

//+------------------------------------------------------------------+
//| bars → JSON 数组 "[[time,o,h,l,c,vol,spread],...]"（升序，原始域） |
//+------------------------------------------------------------------+
string BuildBarsJson(const MqlRates &rates[], int n, int digits, int fromIdx)
  {
   string parts = "";
   int emitted = 0;
   for(int i = fromIdx; i < n; i++)
     {
      if(emitted > 0)
         parts += ",";
      parts += "[" + IntegerToString((long)rates[i].time) +
               "," + DoubleToString(rates[i].open, digits) +
               "," + DoubleToString(rates[i].high, digits) +
               "," + DoubleToString(rates[i].low, digits) +
               "," + DoubleToString(rates[i].close, digits) +
               "," + IntegerToString((long)rates[i].tick_volume) +
               "," + IntegerToString((long)rates[i].spread) + "]";
      emitted++;
     }
   return "[" + parts + "]";
  }

//+------------------------------------------------------------------+
//| 品种信息 → JSON（键名与 python 网关 /symbols 对齐，后端直接同构）  |
//+------------------------------------------------------------------+
string BuildSymbolInfoJson(const string sym)
  {
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   return "{\"description\":\"" + JsonEscape(SymbolInfoString(sym, SYMBOL_DESCRIPTION)) + "\"" +
          ",\"path\":\"" + JsonEscape(SymbolInfoString(sym, SYMBOL_PATH)) + "\"" +
          ",\"currency_base\":\"" + JsonEscape(SymbolInfoString(sym, SYMBOL_CURRENCY_BASE)) + "\"" +
          ",\"currency_profit\":\"" + JsonEscape(SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT)) + "\"" +
          ",\"digits\":" + IntegerToString(digits) +
          ",\"point\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_POINT), 8) +
          ",\"volume_step\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP), 4) +
          ",\"volume_min\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), 4) +
          ",\"volume_max\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), 2) +
          ",\"trade_contract_size\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), 2) +
          ",\"margin_initial\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_MARGIN_INITIAL), 2) +
          ",\"trade_mode\":" + IntegerToString(SymbolInfoInteger(sym, SYMBOL_TRADE_MODE)) +
          ",\"spread\":" + IntegerToString(SymbolInfoInteger(sym, SYMBOL_SPREAD)) +
          ",\"swap_long\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_SWAP_LONG), 2) +
          ",\"swap_short\":" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT), 2) +
          "}";
  }

//+------------------------------------------------------------------+
//| 账户快照 → JSON（键名与 runner account 事件同构，后端镜像直写）     |
//| opened_at 传 UTC epoch 秒（POSITION_TIME 减 skew 归一）            |
//+------------------------------------------------------------------+
string CollectAccountJson()
  {
   string pos = "";
   int emitted = 0;
   int total = PositionsTotal();
   for(int i = 0; i < total && emitted < 100; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      long openedUtc = (long)PositionGetInteger(POSITION_TIME) - g_skewMs / 1000;
      if(emitted > 0)
         pos += ",";
      pos += "{\"id\":" + IntegerToString((long)ticket) +
             ",\"symbol\":\"" + JsonEscape(PositionGetString(POSITION_SYMBOL)) + "\"" +
             ",\"direction\":\"" +
             (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "buy" : "sell") + "\"" +
             ",\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) +
             ",\"open_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 8) +
             ",\"current_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 8) +
             ",\"unrealized_pnl\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) +
             ",\"realized_pnl\":0.0" +
             ",\"swap\":" + DoubleToString(PositionGetDouble(POSITION_SWAP), 2) +
             ",\"commission\":0.0" +
             ",\"opened_at\":" + IntegerToString(openedUtc) + "}";
      emitted++;
     }
   return "{\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) +
          ",\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) +
          ",\"margin_used\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) +
          ",\"margin_free\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) +
          ",\"unrealized_pnl\":" + DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT), 2) +
          ",\"margin_level\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2) +
          ",\"positions\":[" + pos + "]}";
  }

//+------------------------------------------------------------------+
//| 命令结果回传 /api/v1/ea/result                                    |
//+------------------------------------------------------------------+
void SendResult(const string req_id, const bool ok, const string payloadJson,
                const string error)
  {
   string body = "{\"req_id\":\"" + JsonEscape(req_id) + "\"" +
                 ",\"ok\":" + (ok ? "true" : "false") +
                 ",\"payload\":" + (StringLen(payloadJson) > 0 ? payloadJson : "{}") +
                 (StringLen(error) > 0 ? ",\"error\":\"" + JsonEscape(error) + "\"" : "") +
                 "}";
   string resp = "";
   if(!HttpPost("/api/v1/ea/result", body, true, resp))
      Print("EAQuantBridge: result 回传失败 req_id=", req_id);
  }

//+------------------------------------------------------------------+
//| HTTP POST（WebRequest 封装：错误处理 / 白名单提示 / 退避调度）      |
//+------------------------------------------------------------------+
bool HttpPost(const string path, const string body, const bool withKey,
              string &response)
  {
   string url = InpBackendUrl + path;
   string headers = "Content-Type: application/json\r\n";
   if(withKey && StringLen(g_nodeKey) > 0)
      headers += "X-EA-Key: " + g_nodeKey + "\r\n";

   char data[];
   char result[];
   string resultHeaders;
   int len = StringToCharArray(body, data, 0, StringLen(body), CP_UTF8);
   if(len <= 0)
      ArrayResize(data, 0);
   else
      ArrayResize(data, len);   // 去掉末尾终止符

   ResetLastError();
   int status = WebRequest("POST", url, headers, InpTimeoutMs,
                           data, result, resultHeaders);
   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

   if(status == -1)
     {
      int err = GetLastError();
      if(err == 4014)
         Print("EAQuantBridge: WebRequest 被拒绝（err=4014）—— 请在 "
               "工具→选项→Expert Advisors→允许 WebRequest 中添加：", InpBackendUrl);
      else
         Print("EAQuantBridge: WebRequest 失败 err=", err, " url=", url);
      ScheduleRetry();
      return false;
     }
   if(status < 200 || status >= 300)
     {
      Print("EAQuantBridge: HTTP ", status, " ", path,
            " resp=", StringSubstr(response, 0, 200));
      ScheduleRetry();
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| 指数退避 1s→30s（失败时调度下次请求时间）                          |
//+------------------------------------------------------------------+
void ScheduleRetry()
  {
   g_backoffMs = (g_backoffMs < BACKOFF_MIN_MS) ? BACKOFF_MIN_MS
                                                : g_backoffMs * 2;
   if(g_backoffMs > BACKOFF_MAX_MS)
      g_backoffMs = BACKOFF_MAX_MS;
   g_nextSyncAt = TimeLocal() + (datetime)(g_backoffMs / 1000);
   Print("EAQuantBridge: 将在 ", g_backoffMs, "ms 后重试");
  }

//+------------------------------------------------------------------+
//| node_key 落盘 / 读取（GlobalVariable 只能存 double，故用文件）      |
//+------------------------------------------------------------------+
string LoadKey()
  {
   int h = FileOpen(KEY_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return "";
   string key = "";
   while(!FileIsEnding(h))
      key += FileReadString(h);
   FileClose(h);
   StringTrimLeft(key);
   StringTrimRight(key);
   return key;
  }

void SaveKey(const string key)
  {
   int h = FileOpen(KEY_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
     {
      Print("EAQuantBridge: node_key 落盘失败 err=", GetLastError());
      return;
     }
   FileWriteString(h, key);
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| JSON 字符串转义（server 名 / URL 中的特殊字符）                    |
//+------------------------------------------------------------------+
string JsonEscape(const string s)
  {
   string out = "";
   int n = StringLen(s);
   for(int i = 0; i < n; i++)
     {
      ushort c = StringGetCharacter(s, i);
      if(c == '"' || c == '\\')
         out += "\\" + ShortToString(c);
      else if(c == '\n')
         out += "\\n";
      else if(c == '\r')
         out += "\\r";
      else if(c == '\t')
         out += "\\t";
      else
         out += ShortToString(c);
     }
   return out;
  }

//+------------------------------------------------------------------+
//| 极简 JSON 取值："key":"value" → value（仅字符串值）                |
//+------------------------------------------------------------------+
string ExtractJsonString(const string json, const string key)
  {
   string needle = "\"" + key + "\":\"";
   int p = StringFind(json, needle);
   if(p < 0)
      return "";
   int s = p + StringLen(needle);
   int e = StringFind(json, "\"", s);
   if(e < 0)
      return "";
   return StringSubstr(json, s, e - s);
  }

//+------------------------------------------------------------------+
//| 极简 JSON 取值："key":<number> → long（含负号/小数，截断为整数）    |
//+------------------------------------------------------------------+
long ExtractJsonNumber(const string json, const string key)
  {
   string needle = "\"" + key + "\":";
   int p = StringFind(json, needle);
   if(p < 0)
      return 0;
   int s = p + StringLen(needle);
   int n = StringLen(json);
   while(s < n && StringGetCharacter(json, s) == ' ')
      s++;                        // 跳过空格
   int e = s;
   while(e < n)
     {
      ushort c = StringGetCharacter(json, e);
      if((c >= '0' && c <= '9') || c == '-' || c == '.')
         e++;
      else
         break;
     }
   if(e <= s)
      return 0;
   return (long)StringToDouble(StringSubstr(json, s, e - s));
  }

//+------------------------------------------------------------------+
//| 占位：确认图表品种可用（多品种采集 EB-2 扩展）                     |
//+------------------------------------------------------------------+
void g_chartSymbol_ok()
  {
   Print("EAQuantBridge: 桥接品种 = ", _Symbol,
         "（EB-1 仅采集当前图表品种）");
  }
//+------------------------------------------------------------------+
