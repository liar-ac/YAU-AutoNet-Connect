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
