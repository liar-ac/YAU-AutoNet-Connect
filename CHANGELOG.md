# Changelog

## v1.0.1 (2026-05-22)

### Bug 修复
- 修复自定义 `--portal-base` 时登录请求 Referer 仍使用默认网关的问题
- 修复 `--help` 被单实例检查拦截的问题

### 新功能
- 新增 `--version` 参数
- 新增日志轮转，超过 1MB 自动归档为 `.log.old`
- 新增 MIT License
- 新增单元测试（15 项）
- 新增 SHA256 校验文件

### 优化
- 优化 README 结构，参考 BearPi-Nano-Lab 风格
- 新增首次配置说明、已知限制、故障排除章节
- 优化 exe 打包体积（30MB → 11MB）

## v1.0.0 (2026-05-22)

### 功能
- 系统托盘后台运行，双击 exe 直接进入后台模式
- 实时日志窗口（托盘右键菜单）
- 开机自启管理（Windows 注册表）
- 防重复运行保护（Windows Mutex）
- 日志自动轮转（超过 1MB 归档）
- Windows DPAPI 密码加密
- 兼容 PowerShell 版配置文件
- Python 版本，支持 PyInstaller 打包为单文件 exe

### 命令
- `--init` 初始化配置
- `--check` 检查在线状态
- `--once` 单次检测并登录
- `--interval N` 持续监控
- `--tray` 系统托盘模式
