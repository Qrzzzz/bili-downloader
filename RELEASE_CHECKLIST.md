# Release Checklist

## 仓库与依赖

- [ ] 工作树干净，分支与候选提交已记录。
- [ ] 没有 Cookie、登录态、`storage_state.json`、`cookies.txt`、`session.dat`、session/profile、日志、视频、下载记录或用户配置。
- [ ] 没有 `.venv/`、`build/`、`dist/`、`__pycache__/`、本机绝对路径、用户名、token 或账号信息。
- [ ] `requirements.in` / `requirements.txt` 不包含 Playwright 及其独有传递依赖；`segno==1.6.6` 有精确哈希。
- [ ] Python 固定 3.13/Windows x64，三份 `.in` 和哈希锁一致，`--require-hashes --only-binary=:all:` 安装通过。
- [ ] 已核对 `LICENSE`、`THIRD_PARTY_NOTICES.md` 和实际 SBOM，包括 PySide6/Qt、yt-dlp、Segno 和 PyInstaller。

## 功能与安全回归

- [ ] 协议 schema/状态/未知码、超时/断网/412、非允许来源、PNG、刷新废弃旧会话与取消/关闭测试通过。
- [ ] 候选 Cookie 域/名称/字段白名单、先验证后提交、失败保留旧凭据和 DPAPI 原子替换测试通过。
- [ ] v1.2 canonical schema 可读，旧 `storage_state.json` / `cookies.txt` 安全迁移，旧 `playwright-profile` / `login-cache` 只做兼容清理。
- [ ] 临时 Netscape lease 权限/生命周期/清理与匿名模式不读凭据的契约通过。
- [ ] `qrcode_key`、完整轮询/回调 URL、`refresh_token`、Cookie 和响应原文均通过脱敏测试。
- [ ] 打开环境诊断不联网；检查项为应用内二维码组件/本地登录态，不启动外部组件。
- [ ] 真实手机扫码 Gate 已人工完成或被明确列为待验收，未用 mock/协议探测伪造通过。

## 版本、构建与归档

- [ ] 源码 `1.3`、标签 `v1.3`、PE FileVersion/ProductVersion `1.3`、`BiliDownloader.v1.3.exe`、Release 标题 `Bili Downloader Lite v1.3` 严格一致。
- [ ] update checker 只接受可选 `v` / `V` 加两级数字，拒绝多一级、多两级及前后垃圾字符。
- [ ] compile/import、完整 pytest、锁验证、`pip check`、`pip-audit`、源码 self-test 通过。
- [ ] 从干净 Python 3.13/Windows x64 环境构建 onefile，package smoke、PE/内嵌版本/提交校验通过。
- [ ] PyInstaller CArchive 和内嵌 PYZ 审计中 Playwright、driver/Node、`ms-playwright`、Chromium、Electron、profile、FFmpeg、凭据和日志均为零。
- [ ] 已记录 EXE 实际 bytes/MiB 和 SHA-256，未设体积阈值，未填充无用内容。
- [ ] 已如实记录 Authenticode 状态；本机缺少代码签名证书不伪装为已签名。

## 只在获得发布授权后

- [ ] 生成 `BiliDownloader.v1.3.exe`、`BiliDownloader.v1.3.sbom.json` 与 `SHA256SUMS`，为全部资产生成 attestation。
- [ ] 通过 GitHub API 复核 Release 非 draft/非 prerelease、标题、tag/commit、资产名称/数量/大小/API digest 与 attestation。
- [ ] 没有移动或改写已公开 `v1.1` / `v1.2`。

## 建议检查命令

```powershell
git status --short
git diff --check
python tools\verify_dependency_lock.py
python -m pytest -q
.\build.ps1 -Clean -OneFile
.\tools\package_smoke.ps1 -Executable .\dist\BiliDownloader.v1.3.exe
.\build\.venv\Scripts\python.exe tools\audit_release_artifact.py --executable .\dist\BiliDownloader.v1.3.exe --expected-version 1.3 --expected-commit <COMMIT> --require-clean
Get-FileHash .\dist\BiliDownloader.v1.3.exe -Algorithm SHA256
```
