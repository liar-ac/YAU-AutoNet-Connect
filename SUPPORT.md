# 支持说明

## 优先排查路径

遇到无法登录或portal不可达时，请优先使用命令行诊断版：

```powershell
.\campus_auto_login_cli.exe --force-portal-reachable --allow-temporary-proxy-bypass
.\campus_auto_login_cli.exe --once --allow-temporary-proxy-bypass
```

如果问题发生在后台托盘模式，请同时提供`campus_auto_login_py.log`中相关时间段的日志。

## 提交Issue前请准备

- Windows版本。
- 程序版本或commit。
- 是否开启ClashVerge系统代理。
- 是否开启TUN/虚拟网卡模式。
- 当前校园网SSID。
- `--force-portal-reachable`完整输出。
- `FailureMatrix`完整片段。

请先删除账号、密码、手机号、Token等敏感信息。

## 适合提交Issue的问题

- 程序崩溃、异常日志、登录状态误判。
- Clash系统代理开启后portal不可达。
- Windows路由、网卡、SSID识别异常。
- 文档描述不清或命令不可用。

## 不适合公开提交的问题

- 账号密码泄露。
- 安全漏洞细节。
- 需要公开个人网络环境、日志或账号信息才能定位的问题。

安全问题请按`SECURITY.md`说明走私有渠道。
