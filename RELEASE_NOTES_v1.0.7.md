# v1.0.7

## 改进内容

- `campus_auto_login.exe --init` 现在支持 GUI 弹窗输入，无需使用 CLI 版即可完成初始化配置。
- README 初始化说明更新，支持两种方式：CLI 版命令行 / 后台版 GUI 弹窗。
- 配置错误信息改为指向 exe 而非 Python 脚本。

## Bug 修复

- 修复 `network_ready()` 私有 IP 检查误匹配非私有 IP 的问题。
- 修复 `_get_portal_route_info` 中 PowerShell 命令格式化问题。
- 修复重复的虚拟网卡关键词列表。
- 移除冗余 import 和死代码。

## 升级方式

- 替换旧版 exe 即可
- 已初始化用户无需重新 `--init`

## SHA256

使用 `checksums.txt` 校验下载文件完整性。
