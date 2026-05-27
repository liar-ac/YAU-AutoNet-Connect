# Changelog

## v1.0.6 (2026-05-27)

### Bug修复
- 修复开机启动时Portal探测过早执行导致自动登录失败的问题。
- 修复Clash TUN虚拟网卡IP（198.18.x.x）被错误绑定为源地址的问题。
- 修复缓存源IP未验证是否为虚拟网卡IP的问题。

### 新增
- 新增系统启动宽限期（Boot Grace Period）：系统启动后90秒内等待网络稳定。
- 新增网络就绪检测（Network Ready Gate）：确认物理网卡UP、已获私有IPv4、默认路由存在、Portal TCP可连后才开始探测。
- 新增连续稳定检测：网络就绪需连续5秒稳定后才开始Portal探测。
- 增强虚拟IP过滤：198.18.x.x、198.19.x.x、169.254.x.x、127.x.x.x均被识别为虚拟/无效源地址。

### 兼容性
- 所有CLI参数保持不变。
- 后台托盘、静默启动、开机自启行为不变。

## v1.0.5 (2026-05-23)

### Bug修复
- 修复后台托盘运行时每30秒闪现CMD/PowerShell终端窗口的问题。
- 所有subprocess.run()调用添加CREATE_NO_WINDOW标志，禁止子进程创建可见控制台窗口。

### 兼容性
- 功能完全不变，仅消除终端窗口闪烁。

## v1.0.4 (2026-05-23)

### Bug修复
- 修复Clash仅开启系统代理时，校园网认证网关可能仍不可达的问题。
- 校园网请求采用多层兜底传输栈：raw direct → 缓存源IP → 网卡绑定 → 临时路由修复 → PowerShell no-proxy → 临时代理旁路。
- 修复诊断中网卡/路由识别错误（之前可能选中VMware虚拟网卡IP）。
- 增强虚拟网卡检测（VMware、SecTap、Netease UU TAP、TUN/TAP等）。
- 退出认证后没有外网时不影响portal直连判断。
- 修复portal已可达时缺少`attempts`字段导致托盘循环记录`登录异常（'attempts'）`的问题。

### 新增
- 新增fetch_portal_text_resilient多层兜底传输栈。
- 新增网卡绑定直连（source_address绑定物理网卡IP绕过代理路由）。
- 新增校园网路由缓存`campus_route_cache.json`，用于短时路由丢失后的恢复判断。
- 新增Windows WLAN自动恢复：识别“无线局域网接口电源关闭”后尝试启用WLAN接口、开启自动配置、调用NativeWiFi软件无线电开关，再重连校园网SSID。
- 新增临时代理旁路（--allow-temporary-proxy-bypass），临时关闭系统代理后安全恢复。
- 新增进程级NO_PROXY保护（ensure_process_proxy_bypass_for_portal）。
- 新增portal自动发现（configured → DEFAULT_PORTAL → gateway subnet → NCSI）。
- 新增--force-portal-reachable，用于强制验证portal可达性并输出Failure Matrix。
- 新增--diagnose增强诊断（路由、虚拟网卡、Raw direct HTTP、NO_PROXY等）。
- 新增--once单次检测/登录模式。
- 新增--check-wifi检测当前WiFi SSID。
- 新增--set-campus-ssid保存校园WiFi SSID。
- 新增campus_auto_login_cli.exe（console=True）用于命令行诊断。

### 兼容性
- 保持后台托盘静默运行体验不变（campus_auto_login.exe console=False）。
- 保持开机自启、单实例保护、托盘菜单、日志窗口等原有框架不变。
- 默认不修改Clash配置，不长期关闭系统代理；临时代理旁路仅在显式传入`--allow-temporary-proxy-bypass`时启用。

### 测试
- 单元测试扩展到76项。
- 已验证ClashVerge系统代理开启、TUN关闭时，`10.200.84.3`可通过`raw_direct`访问。

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
