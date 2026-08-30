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
- [ ] v1.2 仓库和发布包均不包含 FFmpeg 二进制。
- [ ] `README.md` 使用合规口径，没有暗示绕过平台限制。
- [ ] `DISCLAIMER.md`、`SECURITY.md`、`THIRD_PARTY_NOTICES.md` 已更新。
- [ ] 安全联系渠道已补充。

## 构建与发布

- [ ] 已在干净环境验证从源码运行。
- [ ] Python 固定为 3.13，三份 `.in` 与哈希锁一致，`pip --require-hashes --only-binary=:all:` 安装通过。
- [ ] 已验证 Windows exe 打包流程。
- [ ] 已确认发布包不包含本机日志、登录态、浏览器 profile 或下载文件。
- [ ] 已确认发布包不包含 `ms-playwright` 或内置 Chromium，并已用系统 Edge/Chrome 完成登录浏览器 smoke。
- [ ] 已记录构建命令、Python 版本和依赖版本。
- [ ] 已准备杀毒误报说明。
- [ ] 已生成并公布 `BiliDownloader.v1.2.exe`、`BiliDownloader.v1.2.sbom.json` 与 `SHA256SUMS`。
- [ ] 已为三个发布资产生成并验证 GitHub artifact attestation。
- [ ] 已确认 `v1.2`、源码 `1.2`、PE FileVersion/ProductVersion `1.2`、构建提交和 Release 附件完全一致。
- [ ] 已通过 GitHub API 复核 Release 非 draft/非 prerelease、精确标题、标签提交、资产名称/数量/大小/API digest。
- [ ] 已记录最终 EXE 实际体积；没有设置固定体积阈值或 Actions 体积门。

## 建议检查命令

```powershell
git status --short
git diff --check
git ls-files --others --ignored --exclude-standard
rg -n -i "SESSDATA|bili_jct|DedeUserID|storage_state|cookies\\.txt|token|password" -g "!dist/**" -g "!build/**" -g "!.venv/**"
python tools\verify_dependency_lock.py
Get-FileHash .\dist\BiliDownloader.v1.2.exe -Algorithm SHA256
gh attestation verify .\dist\BiliDownloader.v1.2.exe --repo Qrzzzz/bili-downloader
```
