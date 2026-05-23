# v1.0.5

## 修复内容

- 修复后台托盘运行时每30秒闪现CMD/PowerShell终端窗口的问题。
- 所有`subprocess.run()`调用添加`CREATE_NO_WINDOW`标志，禁止`netsh`、`powershell`等子进程创建可见控制台窗口。

## 功能不变

- Clash系统代理兼容（多层兜底传输栈）
- Wi-Fi自动恢复
- Portal自动发现
- 后台托盘静默运行
- CLI诊断版

## 升级方式

- 替换旧版exe即可
- 已初始化用户无需重新`--init`

## SHA256

使用`checksums.txt`校验下载文件完整性。
