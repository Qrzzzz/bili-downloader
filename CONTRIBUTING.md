# Contributing

感谢你愿意参与改进本项目。贡献前请先阅读 README、DISCLAIMER 和 SECURITY。

## 贡献流程

1. Fork 本仓库。
2. 从主分支新建功能或修复分支。
3. 进行小范围、可审查的修改。
4. 提交 PR，并填写 PR 模板。

## 不要提交的内容

请不要提交以下内容：

- Cookie、登录态、`storage_state.json`、`cookies.txt`
- 视频文件、下载记录、测试视频
- 日志、crash log、浏览器 profile
- `.venv/`、`build/`、`dist/`、`__pycache__/`
- `ffmpeg.exe`、`ffprobe.exe` 或来源和许可证未确认的二进制文件
- 本机绝对路径、个人用户名路径、账号信息或 token

## 合规边界

本项目只接受用于下载用户有权访问内容的合法、合规改进。

不接受以下类型的 PR：

- 绕过付费、会员、DRM、地区限制、风控或访问权限限制。
- 规避平台技术保护措施。
- 收集、导出、上传或滥用用户 Cookie。
- 鼓励侵犯版权或违反平台规则的功能。

## Bug Report

提交 bug 时请提供：

- Windows 版本
- 程序版本
- Python 版本，如果从源码运行
- 是否为打包 exe
- 问题现象和复现步骤
- 脱敏后的日志或 crash 信息

请先删除或替换日志中的 Cookie、账号标识、视频私密链接和本机路径。

## 基本检查

提交前建议至少确认：

```powershell
git status --short
git diff --check
```

如果改动涉及运行或打包流程，请在 PR 中说明你执行过的命令和结果。
