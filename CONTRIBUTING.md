# 贡献指南

感谢你对YAU-AutoNet-Connect项目的关注！本文档将帮助你理解项目架构并参与开发。

## 项目架构说明

### 核心组件

`campus_auto_login.py`（主程序，约3000行）的模块化组织：

```
配置管理层
├── read_config()              # 读取配置文件（支持Python和PowerShell格式）
├── init_config()              # GUI初始化配置
├── dpapi_protect/unprotect    # Windows DPAPI密码加密
└── _find_config_file()        # 多路径配置文件搜索

网络检测层
├── get_status()               # 获取portal在线状态
├── discover_portal_base()     # 自动发现portal地址
├── wait_for_network_ready()   # 等待网络就绪（开机场景）
└── fetch_direct_text()        # 绕过代理直连

登录核心层
├── login_once()               # 单次登录逻辑（带快速恢复）
├── invoke_url_jsonp()         # 调用portal API
└── eportal_login_url()        # 构造登录URL

Wi-Fi管理层
├── reconnect_campus_wifi()    # 重连校园网SSID
├── get_current_ssid()         # 获取当前SSID
└── disable_wifi_power_save()  # 禁用Wi-Fi节能

托盘GUI层
├── run_tray_mode()            # 托盘模式入口
├── show_log_window()          # 实时日志窗口
└── _build_menu()              # 构建托盘菜单

工具函数层
├── write_log()                # 日志记录（支持级别过滤）
├── is_private_ip()            # 私有IP判断
└── boot_grace_wait()          # 开机等待
```

### 程序启动流程

```
用户启动exe
    ↓
检查配置文件是否存在
    ↓
[不存在] → GUI初始化窗口 → 保存配置到%APPDATA% → 进入托盘模式
    ↓
[存在] → 直接进入托盘模式
    ↓
后台监控线程启动（login_loop）
    ↓
每隔N秒执行一次：
    1. get_status()查询portal状态
    2. [在线] → 跳过
       [离线] → login_once()登录
       [网络不可达] → reconnect_campus_wifi() → 快速重试
```

### 代理处理策略（Clash兼容核心）

多层兜底机制确保在Clash等代理软件开启时仍能访问校园网portal：

1. **raw direct连接**：使用`http.client.HTTPConnection`绕过urllib代理
2. **缓存源IP**：首次成功后缓存portal IP，后续直接用IP访问
3. **路由缓存**：记录到portal的路由，网络恢复时快速修复
4. **临时代理旁路**：`--allow-temporary-proxy-bypass`参数临时禁用系统代理

## 如何添加新功能

### 1. 添加命令行参数

在`parse_args()`中添加：

```python
parser.add_argument("--my-feature", action="store_true", help="My feature description.")
```

在`main()`中处理：

```python
if args.my_feature:
    # 你的逻辑
    return 0
```

### 2. 添加托盘菜单项

在`_build_menu()`中添加：

```python
def my_menu_action(icon, item):
    """菜单项点击回调"""
    # 你的逻辑
    pass

# 在_build_menu()中
menu_items.append(pystray.MenuItem("我的功能", my_menu_action))
```

### 3. 添加配置项

在`init_config()`中添加：

```python
data = {
    # ... 现有配置
    "my_setting": value,
}
```

在`read_config()`中读取：

```python
config["my_setting"] = data.get("my_setting", default_value)
```

## 调试技巧

### 1. 诊断portal连通性

```powershell
.\campus_auto_login.exe --diagnose --allow-temporary-proxy-bypass
```

输出包括：portal可达性、代理设置、路由表、Wi-Fi SSID、内网IP状态

### 2. 查看详细日志

```powershell
.\campus_auto_login.exe --once --log-level debug
```

### 3. 模拟portal环境

使用本地HTTP服务器模拟portal响应：

```python
from http.server import HTTPServer, BaseHTTPRequestHandler
class MockPortal(BaseHTTPRequestHandler):
    def do_GET(self):
        if 'eportal/InterFace.do' in self.path:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"result":1,"msg":"login_success"}')

HTTPServer(('127.0.0.1', 8080), MockPortal).serve_forever()
```

然后运行：

```powershell
python campus_auto_login.py --portal-base http://127.0.0.1:8080 --once
```

## 如何贡献

1. Fork 本仓库
2. 创建你的特性分支：`git checkout -b feature/your-feature`
3. 提交你的改动：`git commit -m 'feat: add something'`
4. 推送到远程分支：`git push origin feature/your-feature`
5. 提交 Pull Request

## 开发环境

```powershell
git clone https://github.com/liar-ac/YAU-AutoNet-Connect.git
cd YAU-AutoNet-Connect
pip install -r requirements.txt
```

## 本地验证

提交前至少运行：

```powershell
python -m py_compile campus_auto_login.py
python -m pytest test_campus_auto_login.py -v
```

如果改动涉及Windows网络、Clash系统代理、portal可达性或Wi-Fi恢复，请额外验证：

```powershell
.\campus_auto_login.exe --force-portal-reachable --allow-temporary-proxy-bypass
.\campus_auto_login.exe --once --allow-temporary-proxy-bypass
```

请在PR中写清楚：是否开启Clash系统代理、是否开启TUN、当前校园网SSID、关键日志结果

## 提交规范

使用语义化提交信息：

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `refactor:` 代码重构（不改变功能）
- `test:` 添加/修改测试
- `chore:` 构建/工具变更

## 注意事项

- 不要提交配置文件（含密码）、日志文件、打包产物
- 不要改变默认登录逻辑，除非能证明原有行为是 bug
- 修改后请确保 `python campus_auto_login.py --help` 和 `--check` 仍可正常运行
- 涉及系统代理、注册表、WLAN、路由表的改动必须说明风险、恢复方式和验证结果
- 新增命令行参数时同步更新 README、CHANGELOG、测试
