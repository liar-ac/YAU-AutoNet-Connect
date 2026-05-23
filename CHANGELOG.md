# Changelog

## v1.0.4 (2026-05-23)

### Bug修复
- 修复Clash仅开启系统代理时，校园网认证网关可能仍不可达的问题。
- 校园网请求采用4层兜底传输栈：raw direct → 网卡绑定 → PowerShell no-proxy → 临时代理旁路。
- 修复诊断中网卡/路由识别错误（之前可能选中VMware虚拟网卡IP）。
- 增强虚拟网卡检测（VMware、SecTap、Netease UU TAP、TUN/TAP等）。
- 退出认证后没有外网时不影响portal直连判断。

### 新增
- 新增fetch_portal_text_resilient 4层兜底传输栈。
- 新增网卡绑定直连（source_address绑定物理网卡IP绕过代理路由）。
- 新增临时代理旁路（--allow-temporary-proxy-bypass），临时关闭系统代理后安全恢复。
- 新增进程级NO_PROXY保护（ensure_process_proxy_bypass_for_portal）。
- 新增portal自动发现（configured → DEFAULT_PORTAL → gateway subnet → NCSI）。
- 新增--diagnose增强诊断（路由、虚拟网卡、Raw direct HTTP、NO_PROXY等）。
- 新增--once单次检测/登录模式。
- 新增--check-wifi检测当前WiFi SSID。
- 新增--set-campus-ssid保存校园WiFi SSID。
- 新增campus_auto_login_cli.exe（console=True）用于命令行诊断。

### 兼容性
- 保持后台托盘静默运行体验不变（campus_auto_login.exe console=False）。
- 保持开机自启、单实例保护、托盘菜单、日志窗口等原有框架不变。

## v1.0.3 (2026-05-23)

### Bug修复
- 进一步修复Clash系统代理开启时校园网网关不可达的问题。
- 校园网认证请求改为原生http.client直连传输，避免继续依赖urllib.urlopen。
- 优化Portal unreachable错误信息，区分代理绕过失败和网络路由不可达。

### 诊断
- 新增校园网网关连通性诊断能力。
- 新增--diagnose模式，用于排查portal host、端口80/801、系统代理状态和路由可达性。
- Portal unreachable时增加限频诊断日志，避免日志刷屏。

### 兼容性
- 保持原有后台托盘运行方式不变。
- 保持双击exe静默运行体验。
- 保持开机自启、单实例保护、托盘菜单、日志窗口等原有框架不变。

## v1.0.2 (2026-05-23)

### Bug修复
- 修复Clash系统代理开启时，校园网网关请求可能走代理导致Portal unreachable的问题。
- Python版访问认证网关时改为专用直连opener，绕过系统代理、http_proxy、https_proxy。
- PowerShell版增加直连请求封装，优先使用-NoProxy并保持兼容。

### 兼容性
- 保持原有后台托盘运行方式不变。
- 保持双击exe静默运行体验，不需要长期打开终端窗口。
- 保持开机自启、单实例保护、托盘菜单、日志窗口等原有框架不变。

### 文档
- README新增Clash系统代理/TUN模式说明。
- README新增校园网网关DIRECT规则示例。
- README明确v1.0.2不会改变原有后台托盘运行体验。

### 测试
- 新增直连请求逻辑的单元测试。

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
