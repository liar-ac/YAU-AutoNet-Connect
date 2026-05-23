# v1.0.4

## 核心修复

校园网请求采用 **4层兜底传输栈**，确保 Clash 系统代理开启时仍能访问校园网认证网关：

1. **Raw direct**：`http.client.HTTPConnection` 直接 TCP 连接，不经过 urllib/系统代理
2. **Interface-bound**：绑定物理网卡 IP 的 `source_address`，强制从校园网接口出站
3. **PowerShell no-proxy**：.NET `WebClient` + `GlobalProxySelection::GetEmptyWebProxy`
4. **临时代理旁路**：临时关闭 Windows 系统代理，请求后立即恢复（需 `--allow-temporary-proxy-bypass`）

## 和 v1.0.3 相比

- v1.0.3 只用 http.client 直连，如果路由/网卡选择错误仍会失败
- v1.0.4 自动枚举物理网卡并绑定 source_address，逐个尝试直到成功
- v1.0.4 新增 PowerShell no-proxy 和临时代理旁路作为兜底
- v1.0.4 修复了诊断中错误选择 VMware 虚拟网卡 IP 的问题
- v1.0.4 不再依赖外网连通性判断校园网状态

## 使用方式

```powershell
# 日常使用（双击静默托盘）
.\campus_auto_login.exe

# 诊断（CLI 版有实时输出）
.\campus_auto_login_cli.exe --diagnose

# 单次检测/登录
.\campus_auto_login_cli.exe --once

# 允许临时代理旁路
.\campus_auto_login_cli.exe --once --allow-temporary-proxy-bypass

# 检查 WiFi
.\campus_auto_login_cli.exe --check-wifi
```

## 产物

| 文件 | 说明 |
|---|---|
| `campus_auto_login.exe` | 后台托盘版（console=False），双击静默运行 |
| `campus_auto_login_cli.exe` | 命令行版（console=True），用于 --diagnose / --once |

## 升级方式

- 替换旧版 exe 即可
- 已初始化用户无需重新 `--init`
- 配置文件丢失或换电脑才需重新 `--init`

## SHA256 校验

使用 `checksums.txt` 校验下载文件完整性。

## 注意事项

- 不开 TUN/虚拟网卡时，通常不需要关闭 Clash 系统代理
- 开启 TUN/虚拟网卡时仍需 Clash DIRECT 规则
- 退出校园网认证后没有外网是正常的，程序只访问校园网内网网关
- 如果使用 `--allow-temporary-proxy-bypass`，程序会临时关闭系统代理并在请求后恢复
