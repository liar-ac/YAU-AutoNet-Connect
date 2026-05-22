# YAU-AutoNet-Connect

延安大学校园网自动登录工具。支持 Dr.COM/eportal 认证门户，双击即用，后台静默运行。

## 功能特性

- 系统托盘后台运行，无终端窗口
- 自动检测网络认证状态，在线时跳过，离线时自动登录
- 实时日志窗口（右键托盘图标查看）
- 开机自启管理（托盘右键菜单一键切换）
- Windows DPAPI 密码加密，仅当前用户本机可解密
- 兼容读取 PowerShell 版配置文件
- 防重复运行保护
- 日志自动轮转（超过 1MB 归档）

## 系统要求

- Windows 10 / 11
- 校园网网关地址为 `http://10.200.84.3`（Dr.COM eportal）
- 如网关不同，可通过 `--portal-base` 参数指定

## 快速开始

> **首次使用必须先配置账号密码，否则无法自动登录。**

### 第一步：下载

从 [Releases](https://github.com/liar-ac/YAU-AutoNet-Connect/releases) 下载最新的 `campus_auto_login.exe`。

### 第二步：初始化配置（仅首次）

将 exe 放到一个**纯英文路径**下，例如 `D:\tools\YAU-AutoNet-Connect\`，然后在该目录打开终端运行：

```powershell
.\campus_auto_login.exe --init
```

按提示依次输入：

1. 校园网用户名
2. 校园网密码（输入时不可见）
3. 运营商后缀（直接回车为默认，`@dx` 电信，`@lt` 联通）

配置文件会生成在 exe 所在目录，密码使用 Windows DPAPI 加密。

### 第三步：开始使用

以后直接**双击 `campus_auto_login.exe`** 即可。程序会自动最小化到系统托盘后台运行。

## 托盘操作

双击 exe 后，程序驻留在任务栏右下角托盘区。右键点击托盘图标：

| 菜单项 | 说明 |
|--------|------|
| 开机自启 | 勾选后开机自动启动，再次点击取消 |
| 查看日志 | 打开实时日志窗口 |
| 退出 | 关闭程序 |

## 命令行参考

以下命令在 exe 和 Python 脚本中均可使用：

```powershell
# 初始化配置
.\campus_auto_login.exe --init

# 检查当前在线状态
.\campus_auto_login.exe --check

# 检查一次，离线时自动登录
.\campus_auto_login.exe --once

# 以指定间隔持续监控（5-30秒）
.\campus_auto_login.exe --interval 30

# 显式指定托盘模式
.\campus_auto_login.exe --tray
```

使用 Python 脚本：

```powershell
python .\campus_auto_login.py --init
python .\campus_auto_login.py --check
python .\campus_auto_login.py --once
python .\campus_auto_login.py --tray
```

## PowerShell 版本

```powershell
# 初始化
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -Init

# 检查状态
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -CheckOnly

# 单次登录
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -Once

# 持续监控
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -IntervalSeconds 30
```

## 配置说明

| 文件 | 说明 |
|------|------|
| `campus_login_py.config.json` | Python 版配置（`--init` 生成） |
| `campus_login.config.json` | PowerShell 版配置（`-Init` 生成） |

Python 版会自动兼容读取 PowerShell 版配置。配置文件和日志默认生成在 exe 所在目录。

## 从源码运行

```powershell
git clone https://github.com/liar-ac/YAU-AutoNet-Connect.git
cd YAU-AutoNet-Connect
pip install pystray Pillow watchdog
python .\campus_auto_login.py --init
python .\campus_auto_login.py --tray
```

## 打包构建

```powershell
pip install pyinstaller pystray Pillow watchdog
pyinstaller --clean campus_auto_login.spec
```

打包产物在 `dist/campus_auto_login.exe`。

如需自动监听代码变更并重新打包：

```powershell
python watch_build.py
```

> UPX 是可选的 exe 压缩工具，PyInstaller 检测到 UPX 时会自动启用。如未安装 UPX，打包仍然正常进行，仅体积略大。

## 安全说明

- 密码使用 **Windows DPAPI** 加密，只能由当前 Windows 用户在本机解密
- 配置文件（含加密密码）**不应上传到 GitHub 或分享给他人**
- 本项目不会收集、上传或存储任何用户数据
- 所有网络请求仅与校园网网关通信

## 注意事项

- exe 所在路径**尽量不要包含中文、空格或特殊字符**，避免路径兼容问题
- 建议放在类似 `D:\tools\YAU-AutoNet-Connect\` 这种简单英文路径下
- 如果校园网网关地址不同，可通过 `--portal-base` 指定，例如：
  ```powershell
  .\campus_auto_login.exe --tray --portal-base http://10.200.100.1
  ```
- 本项目仅适用于 Windows 系统

## 故障排除

| 问题 | 解决方法 |
|------|----------|
| 双击 exe 无反应 | 检查是否有其他实例在运行，查看任务管理器 |
| 提示 "Config not found" | 需要先运行 `--init` 配置账号密码 |
| 登录失败 | 检查用户名、密码、运营商后缀是否正确 |
| Portal unreachable | 确认已连接校园网，网关地址正确 |
| 提示已在运行中 | 任务管理器结束已有的 `campus_auto_login.exe` 进程 |

## 贡献指南

欢迎提交 Issue 和 Pull Request。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全政策

如发现安全漏洞，请勿公开提交 Issue。详见 [SECURITY.md](SECURITY.md)。

## 许可证

目前未指定许可证，需要维护者选择。
