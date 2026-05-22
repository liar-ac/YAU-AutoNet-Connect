<div align="center">

<h1>YAU-AutoNet-Connect</h1>

<h3>延安大学校园网自动登录工具</h3>

<p>双击即用，静默运行在系统托盘。自动检测网络认证状态，在线时跳过，离线时自动登录 Dr.COM ePortal 网关。</p>

[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white)]()
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)](requirements.txt)
[![Dr.COM](https://img.shields.io/badge/Dr.COM-ePortal-FF6B00?style=for-the-badge)]()
[![DPAPI](https://img.shields.io/badge/DPAPI-密码加密-2EA44F?style=for-the-badge&logo=letsencrypt&logoColor=white)]()
[![PyInstaller](https://img.shields.io/badge/PyInstaller-单文件打包-3572A5?style=for-the-badge)](campus_auto_login.spec)
[![License](https://img.shields.io/badge/License-MIT-00A86B?style=for-the-badge)](LICENSE)

<p>
<a href="#快速开始">快速开始</a> ·
<a href="#命令行参考">命令行参考</a> ·
<a href="#从源码运行">从源码运行</a> ·
<a href="#安全说明">安全说明</a> ·
<a href="#故障排除">故障排除</a>
</p>

</div>

---

## 项目定位

YAU-AutoNet-Connect 是一个面向延安大学校园网的自动登录工具，适用于 Dr.COM/ePortal 认证网关。下载 exe 后首次配置一次账号密码，之后双击即可后台静默运行，无需手动打开浏览器登录。

项目同时提供 Python 脚本和 PowerShell 脚本两个版本，密码均使用 Windows DPAPI 加密存储。

| 组件 | 说明 |
|---|---|
| `campus_auto_login.py` | Python 主程序，支持托盘后台运行、日志窗口、开机自启 |
| `campus_auto_login.ps1` | PowerShell 兼容版本，支持初始化、检测、单次登录和持续监控 |
| `campus_auto_login.spec` | PyInstaller 打包配置，已排除不必要依赖以压缩体积 |
| `watch_build.py` | 开发用，监听代码变更自动重新打包 |

## 功能亮点

| 功能 | 说明 |
|---|---|
| 系统托盘后台运行 | 双击 exe 直接最小化到托盘，无终端窗口 |
| 自动检测与登录 | 在线时跳过，离线时自动调用认证接口登录 |
| 实时日志窗口 | 托盘右键菜单打开，实时显示运行日志 |
| 开机自启 | 托盘右键菜单一键切换，写入 Windows 注册表 |
| 防重复运行 | Windows Mutex 保护，重复启动时弹窗提示 |
| 日志自动轮转 | 超过 1MB 自动归档为 `.log.old` |
| DPAPI 密码加密 | 仅当前 Windows 用户在本机可解密 |
| PowerShell 兼容 | 可自动读取 PowerShell 版生成的配置文件 |

## 系统要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 |
| 校园网环境 | 网关地址 `http://10.200.84.3`（Dr.COM ePortal） |
| 其他网关 | 可通过 `--portal-base` 参数指定 |

---

## 快速开始

> **首次下载后不能直接登录校园网，必须先运行初始化命令配置账号密码。**

### 第一步：下载

从 [Releases](https://github.com/liar-ac/YAU-AutoNet-Connect/releases) 下载最新的 `campus_auto_login.exe`。

### 第二步：初始化配置（仅首次）

将 exe 放到一个**纯英文路径**下，例如 `D:\tools\YAU-AutoNet-Connect\`，然后在该目录打开 PowerShell：

```powershell
.\campus_auto_login.exe --init
```

按提示依次输入：

| 提示 | 说明 |
|---|---|
| `Campus username` | 校园网用户名 |
| `Campus password` | 校园网密码（输入时不可见） |
| `Service suffix` | 运营商后缀：直接回车为默认，`@dx` 电信，`@lt` 联通 |

> 密码使用 Windows DPAPI 加密，配置文件生成在 exe 所在目录，不同用户的密码互不影响。

### 第三步：开始使用

配置完成后，以后直接**双击 `campus_auto_login.exe`** 即可。程序自动进入系统托盘后台运行。

---

## 日常使用

双击 exe 后，程序驻留在任务栏右下角托盘区。右键点击托盘图标：

| 菜单项 | 说明 |
|---|---|
| 开机自启 | 勾选后开机自动启动，再次点击取消 |
| 查看日志 | 打开实时日志窗口 |
| 退出 | 关闭程序 |

程序运行时会自动检测网络状态：在线时跳过，离线时尝试登录。日志超过 1MB 会自动归档为 `.log.old`。

---

## 命令行参考

exe 和 Python 脚本均支持以下命令：

### exe 命令

```powershell
# 查看版本
.\campus_auto_login.exe --version

# 初始化配置（首次使用必须）
.\campus_auto_login.exe --init

# 检查当前在线状态
.\campus_auto_login.exe --check

# 检查一次，离线时自动登录
.\campus_auto_login.exe --once

# 以指定间隔持续监控（5-30 秒）
.\campus_auto_login.exe --interval 30

# 显式指定托盘模式
.\campus_auto_login.exe --tray

# 指定自定义网关
.\campus_auto_login.exe --tray --portal-base http://10.200.100.1
```

### Python 脚本命令

```powershell
python .\campus_auto_login.py --version
python .\campus_auto_login.py --init
python .\campus_auto_login.py --check
python .\campus_auto_login.py --once
python .\campus_auto_login.py --interval 30
python .\campus_auto_login.py --tray
```

### PowerShell 版本

```powershell
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -Init
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -CheckOnly
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -Once
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -IntervalSeconds 30
```

---

## 配置文件说明

| 文件 | 生成方式 | 说明 |
|---|---|---|
| `campus_login_py.config.json` | `--init` 生成 | Python 版配置，密码为 DPAPI Base64 |
| `campus_login.config.json` | PowerShell `-Init` 生成 | PowerShell 版配置，密码为 SecureString Hex |

两个版本互相兼容：Python 脚本会自动读取 PowerShell 版配置。配置文件和日志默认生成在 exe 所在目录。

> **不要把配置文件、日志文件上传到 GitHub 或公开分享。** 虽然密码已加密，但仍不应公开。

---

## 从源码运行

```powershell
git clone https://github.com/liar-ac/YAU-AutoNet-Connect.git
cd YAU-AutoNet-Connect
pip install -r requirements.txt
python .\campus_auto_login.py --init
python .\campus_auto_login.py --tray
```

如需开发依赖（PyInstaller、watchdog）：

```powershell
pip install -r requirements-dev.txt
```

## 打包构建

```powershell
pip install -r requirements-dev.txt
pyinstaller --clean campus_auto_login.spec
```

打包产物在 `dist/campus_auto_login.exe`。

如需监听代码变更自动重新打包：

```powershell
python watch_build.py
```

> UPX 是可选的 exe 压缩工具。PyInstaller 检测到 UPX 时会自动启用，未安装时打包正常进行，仅体积略大。

---

## 安全说明

| 项目 | 说明 |
|---|---|
| 密码加密 | Windows DPAPI，仅当前用户在本机可解密 |
| 配置文件 | 含加密密码，不应公开分享 |
| 使用环境 | 仅适用于可信校园网环境 |
| 数据收集 | 不收集、不上传任何用户数据 |
| 网络请求 | 仅与校园网网关通信 |

如发现安全问题，请勿公开提交 Issue，详见 [SECURITY.md](SECURITY.md)。

---

## 注意事项

- 本项目**仅适用于 Windows** 系统
- exe 路径**建议使用纯英文**，例如 `D:\tools\YAU-AutoNet-Connect\`，避免中文、空格或特殊字符
- 不需要管理员权限运行
- 如果提示"已在运行中"，检查任务管理器中是否已有 `campus_auto_login.exe` 进程
- 不要把配置文件（`campus_login_py.config.json`、`campus_login.config.json`）和日志文件上传到 GitHub

---

## 已知限制

| 限制 | 说明 |
|---|---|
| 仅限 Windows | 依赖 Windows DPAPI 和注册表，不支持 macOS / Linux |
| 单网关 | 默认只支持 `http://10.200.84.3`，多网关需手动指定 `--portal-base` |
| 单用户单机 | DPAPI 加密绑定当前 Windows 用户，换用户或换机需重新 `--init` |
| PowerShell 版无托盘 | PowerShell 版不支持系统托盘、日志窗口和开机自启菜单 |
| 日志无远程上报 | 日志仅写入本地文件，不支持远程查看或上报 |

---

## 故障排除

| 问题 | 解决方法 |
|---|---|
| `Config not found` | 首次使用需先运行 `--init` 配置账号密码 |
| `Portal unreachable` | 检查是否已连接校园网，网关地址是否正确 |
| 登录失败 | 检查用户名、密码、运营商后缀是否正确 |
| 双击 exe 无窗口 | 正常行为，程序在系统托盘运行 |
| 托盘图标找不到 | 查看任务栏隐藏图标区域，或检查任务管理器 |
| 提示"已在运行中" | 已有实例在运行，任务管理器结束已有进程 |
| `--check` 显示 Offline | 确认已连接校园网，尝试运行 `--once` |

---

## 贡献指南

欢迎提交 Issue 和 Pull Request，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 联系作者

| 方式 | 信息 |
|---|---|
| QQ | 3291890183 |
| 邮箱 | [yuhaohu05@163.com](mailto:yuhaohu05@163.com) |

## 许可证

本项目采用 MIT License 开源，详见 [LICENSE](LICENSE)。
