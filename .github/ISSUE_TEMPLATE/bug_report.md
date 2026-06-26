---
name: Bug Report
about: 提交 Bug 报告
title: '[Bug] '
labels: bug
---

## 问题描述

简洁描述你遇到的问题。

## 复现步骤

1. 运行 `...`
2. 点击 `...`
3. 看到错误 `...`

## 期望行为

描述你期望的正确行为。

## 实际行为

描述实际发生的情况。

## 环境信息

- Windows 版本：
- exe 版本 / commit：
- 校园网网关地址：
- 当前 Wi-Fi SSID：
- 是否开启 ClashVerge 系统代理：
- 是否开启 TUN/虚拟网卡模式：
- 系统代理地址（如 `127.0.0.1:7897`）：

## 诊断输出

请优先运行：

```powershell
.\campus_auto_login.exe --force-portal-reachable --allow-temporary-proxy-bypass
.\campus_auto_login.exe --once --allow-temporary-proxy-bypass
```

粘贴完整输出，尤其是 `Failure Matrix` 片段。

## 日志

如有日志文件，请粘贴相关片段（注意脱敏，不要包含密码）。
