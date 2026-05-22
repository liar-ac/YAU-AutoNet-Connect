# 校园网自动登录

认证页识别结果:

- 网关:http://10.200.84.3
- 状态接口:/drcom/chkstatus，返回JSONP，result=1表示已在线，result=0表示未认证。
- 登录接口:http://10.200.84.3:801/eportal/portal/login，JSONPGET方式。
- 当前页面配置:Dr.COM/eportal，login_method=1；网页登录时账号参数名为`user_account`，密码参数名为`user_password`，账号会带`,0,`前缀。

## Python版本

已打包exe位置:

```powershell
.\dist\campus_auto_login.exe
```

双击运行会进入**系统托盘后台模式**，自动最小化到任务栏右下角托盘区，不占用终端窗口。右键点击托盘图标可以"查看日志"或"退出"。程序会从exe所在目录读取`campus_login_py.config.json`或`campus_login.config.json`，日志写到exe所在目录。

命令行验证exe:

```powershell
.\dist\campus_auto_login.exe --check
.\dist\campus_auto_login.exe --once
.\dist\campus_auto_login.exe --interval 30
```

显式指定托盘模式:

```powershell
.\dist\campus_auto_login.exe --tray
```

监控间隔会限制在5到30秒之间；小于5秒会自动调到5秒，大于30秒会自动调到30秒。

首次配置:

```powershell
python .\campus_auto_login.py --init
```

配置会生成`campus_login_py.config.json`。密码使用WindowsDPAPI加密，只能由当前Windows用户在本机解密。
如果没有`campus_login_py.config.json`，Python脚本会自动兼容读取PowerShell版生成的`campus_login.config.json`。

手动执行一次检测并在未认证时登录:

```powershell
python .\campus_auto_login.py --once
```

只检查状态，不登录:

```powershell
python .\campus_auto_login.py --check
```

持续监控:

```powershell
python .\campus_auto_login.py --interval 30
```

系统托盘后台模式（双击exe默认行为）:

```powershell
python .\campus_auto_login.py --tray
```

## PowerShell版本

首次配置:

```powershell
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -Init
```

配置会生成`campus_login.config.json`。密码使用WindowsDPAPI加密，只能由当前Windows用户在本机解密。

手动执行一次检测并在未认证时登录:

```powershell
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -Once
```

只检查状态，不登录:

```powershell
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -CheckOnly
```

持续监控:

```powershell
powershell -ExecutionPolicy Bypass -File .\campus_auto_login.ps1 -IntervalSeconds 30
```

如果要开机自动运行，可以用任务计划程序创建启动任务。该操作会修改系统计划任务，我没有自动执行。
