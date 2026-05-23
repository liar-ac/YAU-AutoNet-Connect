# v1.0.3

## 核心修复

- 校园网网关请求改为 `http.client.HTTPConnection` 原生直连传输，完全绕过 urllib 代理机制。
- v1.0.2 使用 urllib 的 `ProxyHandler({})` 绕过代理，但仍可能受 Windows 系统代理影响。v1.0.3 进一步使用底层 `http.client` 直接建立 TCP 连接，不经过任何 urllib 代理处理。
- 优化错误信息：Portal unreachable 时明确区分代理问题和网络路由不可达。

## 为什么 v1.0.2 可能仍失败

v1.0.2 使用 urllib 的 `request.build_opener(request.ProxyHandler({}))`，但 urllib 内部的连接建立仍可能与 Windows 系统代理设置交互。WinError 10065 表示本机无法建立到目标主机的 TCP 连接，这可能是：

1. **系统代理仍在干扰 urllib 的底层 socket 连接**
2. **本机到校园网网关没有可用路由**（未连接校园网 WiFi/网线）
3. **校园网网关地址已变更**
4. **安全软件或网络策略拦截**

v1.0.3 使用 `http.client.HTTPConnection` 直接创建 TCP socket 连接，完全不经过 urllib 的代理处理层。

## 如果仍然出现 WinError 10065

说明问题不只是 urllib 代理，而是本机到 `10.200.84.3` 没有网络路由。请：

1. 确认电脑已连接校园网 WiFi 或校园网网线
2. 确认 portal 地址仍为 `10.200.84.3`
3. 尝试在浏览器中打开 `http://10.200.84.3`
4. 运行 `campus_auto_login.exe --diagnose` 查看详细诊断信息
5. 检查是否存在 TUN/虚拟网卡模式或安全软件拦截
6. 如果开启了 TUN/虚拟网卡，仍需在 Clash 规则中添加 DIRECT 规则

## --diagnose 使用方法

```powershell
.\campus_auto_login.exe --diagnose
```

输出内容包括：
- Portal host 和端口
- Socket 直连测试结果（端口 80 和 801）
- 进程代理环境变量检测
- Windows 系统代理状态
- 路由检查

诊断结果同时写入日志文件。

## 保持原有体验

- 后台托盘运行：不变
- 静默启动：不变（双击 exe 无终端窗口）
- 开机自启：不变
- 单实例保护：不变
- 日志窗口/托盘菜单：不变

## 升级方式

- 直接替换旧版 `campus_auto_login.exe` 即可。
- 已初始化过账号密码的用户无需重新 `--init`。
- 如果配置文件丢失或换电脑，才需要重新 `--init`。

## TUN/虚拟网卡模式

如果用户开启了 TUN/虚拟网卡模式，仍需在 Clash 规则中添加 DIRECT 规则：

```yaml
rules:
  - IP-CIDR,10.200.84.3/32,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
  - DOMAIN-SUFFIX,edu.cn,DIRECT
```
