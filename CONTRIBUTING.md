# 贡献指南

感谢你对本项目的关注。

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
pip install -r requirements-dev.txt
```

## 本地验证

提交前至少运行：

```powershell
python -m py_compile campus_auto_login.py
python -m py_compile watch_build.py
python -m pytest test_campus_auto_login.py -q
```

如果改动涉及Windows网络、Clash系统代理、portal可达性或Wi-Fi恢复，请在Windows环境额外验证：

```powershell
.\dist\campus_auto_login_cli.exe --force-portal-reachable --allow-temporary-proxy-bypass
.\dist\campus_auto_login_cli.exe --once --allow-temporary-proxy-bypass
```

请在PR中写清楚是否开启ClashVerge系统代理、是否开启TUN、当前校园网SSID以及关键日志结果。

## 提交规范

使用语义化提交信息：

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `refactor:` 代码重构（不改变功能）
- `chore:` 构建/工具变更

## 注意事项

- 不要提交配置文件（含密码）、日志文件、打包产物
- 不要改变默认登录逻辑，除非能证明原有行为是 bug
- 修改后请确保 `python campus_auto_login.py --help` 和 `--check` 仍可正常运行
- 涉及系统代理、注册表、WLAN、路由表的改动必须说明风险、恢复方式和验证结果
- 新增命令行参数时同步更新 README、CHANGELOG、Release Notes 和测试
