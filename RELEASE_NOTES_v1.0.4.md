# v1.0.4

## 核心修复

- 校园网网关请求使用 `http.client.HTTPConnection` 原生直连传输，完全绕过 urllib 和系统代理。
- 新增 portal 自动发现能力：当默认 `10.200.84.3` 不可达时，自动通过 NCSI 探测（`msftconnecttest.com`、`gstatic.com` 等）和网关子网扫描发现校园网认证入口。
- 优化错误信息，Portal unreachable 时自动触发诊断和 portal 发现。

## 和 v1.0.3 相比的改进

- v1.0.3 使用 http.client 直连，但如果 portal 地址不可达则直接失败。
- v1.0.4 在 portal 不可达时自动尝试发现可用的认证入口，减少用户手动配置的需求。

## 使用方式

```powershell
# 诊断校园网网关连通性（不登录，不修改配置）
.\campus_auto_login.exe --diagnose

# 单次检测/登录后退出（不启动托盘后台循环）
.\campus_auto_login.exe --once

# 手动指定 portal 地址
.\campus_auto_login.exe --once --portal-base http://10.200.100.1

# 正常使用（双击 exe 后台托盘运行）
.\campus_auto_login.exe
```

## 升级方式

- 直接替换旧版 `campus_auto_login.exe` 即可。
- 已初始化过账号密码的用户无需重新 `--init`。
- 如果配置文件丢失或换电脑，才需要重新 `--init`。

## SHA256 校验

使用 `checksums.txt` 校验下载文件完整性。

## 注意事项

- 默认双击 exe 仍为后台托盘静默运行，不会弹出终端窗口。
- 不开 TUN/虚拟网卡模式时，通常不需要关闭 Clash 即可正常使用。
- 如果开启 TUN/虚拟网卡模式，仍需在 Clash 规则中添加 DIRECT 规则：

```yaml
rules:
  - IP-CIDR,10.200.84.3/32,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
  - DOMAIN-SUFFIX,edu.cn,DIRECT
```

- 如果自动发现的 portal 地址不正确，可使用 `--portal-base` 手动指定。
