# v1.0.2

## 核心说明

- 修复 Clash 系统代理开启时无法访问校园网认证网关的问题。
- 只开系统代理的用户无需关闭 Clash 即可让本工具自动尝试登录。
- 保持原有后台托盘/静默运行体验，不需要长期打开终端窗口。
- 如果开启 TUN/虚拟网卡模式，仍需在 Clash 中给校园网网关和内网 IP 段配置 DIRECT。

## 下载说明

- 下载 `campus_auto_login.exe`。
- 替换旧版 exe 即可。

## SHA256 校验

使用 `checksums.txt` 校验下载文件完整性。

## 升级说明

- 直接替换旧版 `campus_auto_login.exe` 即可。
- 已初始化过账号密码的用户无需重新 `--init`。
- 如果配置文件丢失或换电脑，才需要重新 `--init`。

## 注意事项

- 本版本解决的是 Clash 系统代理场景。
- 如果用户开启了 TUN/虚拟网卡模式，还需要在 Clash 规则中添加 DIRECT 规则：

```yaml
rules:
  - IP-CIDR,10.200.84.3/32,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
  - DOMAIN-SUFFIX,edu.cn,DIRECT
```
