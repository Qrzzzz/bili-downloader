# Release Checklist

发布前请逐项确认。

## 仓库内容

- [ ] `.gitignore` 已生效。
- [ ] 没有 Cookie、登录态、`storage_state.json`、`cookies.txt`。
- [ ] 没有 `logs/`、`crash.log`、`app.log` 或其他本地日志。
- [ ] 没有视频文件、真实下载记录或测试视频。
- [ ] 没有 `dist/`、`build/`、`.venv/`、`__pycache__/`。
- [ ] 没有浏览器 profile、Playwright 用户数据目录或下载的 Chromium 缓存。
- [ ] 没有本机绝对路径、个人用户名路径、token 或账号信息。
- [ ] 没有未确认来源和许可证义务的 `ffmpeg.exe`、`ffprobe.exe` 或其他二进制文件。

## 文档与许可证

- [ ] `LICENSE` 已确认适用于本项目原创代码。
- [ ] 第三方依赖许可证和 notice 已确认。
- [ ] 如果发布包包含 FFmpeg，已确认 FFmpeg 构建来源和 LGPL/GPL 等许可证义务。
- [ ] `README.md` 使用合规口径，没有暗示绕过平台限制。
- [ ] `DISCLAIMER.md`、`SECURITY.md`、`THIRD_PARTY_NOTICES.md` 已更新。
- [ ] 安全联系渠道已补充。

## 构建与发布

- [ ] 已在干净环境验证从源码运行。
- [ ] 已验证 Windows exe 打包流程。
- [ ] 已确认发布包不包含本机日志、登录态、浏览器 profile 或下载文件。
- [ ] 已记录构建命令、Python 版本和依赖版本。
- [ ] 已准备杀毒误报说明。
- [ ] 已生成并公布 GitHub Release 附件校验值，例如 SHA256。
- [ ] 已确认 tag、源码包和 Release 附件对应同一版本。

## 建议检查命令

```powershell
git status --short
git diff --check
git ls-files --others --ignored --exclude-standard
rg -n -i "SESSDATA|bili_jct|DedeUserID|storage_state|cookies\\.txt|token|password" -g "!dist/**" -g "!build/**" -g "!.venv/**"
Get-FileHash .\dist\BiliDownloader\BiliDownloader.v1.0.exe -Algorithm SHA256
```
