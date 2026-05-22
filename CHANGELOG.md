# Changelog

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
