MT5 量化平台 · 本地网关安装步骤
================================

一、前置条件
  1. Windows 10/11 x64（与 MT5 终端同一台机器）。
  2. 已安装 MetaTrader 5 终端并登录目标账户（记下服务器名）。
  3. 平台后端地址，例如 http://192.168.1.10:8000。
  4. ⚠ Smart App Control（Win11 新装机默认开启）会拦截未签名的本安装包：
     若运行 MT5Gateway.exe / MT5Pair.exe 提示「Application Control policy
     has blocked this file」，需在「Windows 安全中心 → 应用和浏览器控制 →
     Smart App Control」中关闭（注意：关闭后不可再开启），或使用已签名版本。

二、安装网关
  1. 解压本压缩包到任意目录，如 D:\MT5Gateway。
  2. 复制 .env.example 为 .env，填写：
       MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
       GATEWAY_HOST=0.0.0.0        （供后端访问；仅本机用 127.0.0.1）
       GATEWAY_PORT=8090
     （GATEWAY_API_KEY 留空即可，下一步配对自动写入。）
  3. 双击 MT5Gateway\MT5Gateway.exe 启动网关（或用 NSSM 注册为 Windows
     服务实现开机自启，见平台文档「网关常驻运维」）。

三、配对绑定（二选一）
  A. 扫码：平台网页「网关」页 → 生成配对码 → 用手机扫码获得 server 与 code。
  B. 手动：在网页「网关」页点「生成配对码」，得到 8 位码（10 分钟有效）。
  然后在网关机器上执行：
    MT5Pair\MT5Pair.exe --server http://<后端地址>:8000 --code XXXX-XXXX ^
        --gateway-host <本机IP> --gateway-port 8090 --name "客厅机器"
  成功后密钥自动写入 .env，重启网关生效。

四、验证
  平台网页「网关」页出现本节点且状态 online（心跳每 30s 更新），
  即可在平台上绑定 MT5 账户并部署模拟盘 / 实盘。

常见问题
  * 提示「Application Control policy has blocked this file」→ Smart App
    Control 拦截未签名程序，见前置条件第 4 条。
  * 提示「配对码无效或已过期」→ 码是一次性的，重新生成即可。
  * 后端连不上网关 → 检查 GATEWAY_HOST/PORT 与防火墙入站 8090。
  * MT5 登录失败 → 确认终端已启动且「算法交易」按钮为绿色。
