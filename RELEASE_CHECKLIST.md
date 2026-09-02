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
- [ ] canonical 有效时旧明文会被精确清理；损坏 canonical、部分删除失败、双进程 lease 重叠、活动 lease 防清理/防退出、原子临时文件和隔离残留的保守清理测试通过。
- [ ] onefile/onedir/源码/绝对 PATH 的 FFmpeg 顺序、Unicode/空格路径和损坏候选测试通过，归档仍不含 FFmpeg。
- [ ] b23 逐跳状态、相对跳转、循环/超限、协议降级、外域、私网/IP、端口和 userinfo 测试通过；单元测试不访问实时网络。
- [ ] 封面类型、声明/实际大小、chunked、重定向、超时、中断与正常小图测试通过，失败不破坏解析结果。
- [ ] 运行 marker 的正常退出、硬崩溃、双实例、PID 重用、损坏/无权限、旧格式和重启陈旧状态测试通过。
- [ ] `qrcode_key`、完整轮询/回调 URL、`refresh_token`、Cookie 和响应原文均通过脱敏测试。
- [ ] 打开环境诊断不联网；检查项为应用内二维码组件/本地登录态，不启动外部组件。
- [ ] 真实手机扫码 Gate 已人工完成或被明确列为待验收，未用 mock/协议探测伪造通过。

## 版本、构建与归档

- [ ] 源码 `2.0`、标签 `v2.0`、PE FileVersion/ProductVersion `2.0`、`BiliDownloader.v2.0.exe`、Release 标题 `Bili Downloader Lite v2.0` 严格一致。
- [ ] update checker 只接受可选 `v` / `V` 加两级数字，拒绝多一级、多两级及前后垃圾字符。
- [ ] compile/import、完整 pytest、锁验证、`pip check`、`pip-audit`、源码 self-test 通过。
- [ ] 从干净 Python 3.13/Windows x64 环境构建 onefile，package smoke、PE/内嵌版本/提交校验通过。
- [ ] PyInstaller CArchive 和内嵌 PYZ 审计中 Playwright、driver/Node、`ms-playwright`、Chromium、Electron、profile、FFmpeg、凭据和日志均为零。
- [ ] 已记录 EXE 实际 bytes/MiB 和 SHA-256，未设体积阈值，未填充无用内容。
- [ ] 已如实记录 Authenticode 状态；本机缺少代码签名证书不伪装为已签名。

## 只在获得发布授权后

- [ ] 生成 `BiliDownloader.v2.0.exe`、`BiliDownloader.v2.0.sbom.json` 与 `SHA256SUMS`，为全部资产生成 attestation。
- [ ] 通过 GitHub API 复核 Release 非 draft/非 prerelease、标题、tag/commit、资产名称/数量/大小/API digest 与 attestation。
- [ ] 没有移动或改写已公开 `v1.1` / `v1.2` / `v1.3`。

## 建议检查命令

```powershell
git status --short
git diff --check
python tools\verify_dependency_lock.py
python -m pytest -q
.\build.ps1 -Clean -OneFile
.\tools\package_smoke.ps1 -Executable .\dist\BiliDownloader.v2.0.exe
.\build\.venv\Scripts\python.exe tools\audit_release_artifact.py --executable .\dist\BiliDownloader.v2.0.exe --expected-version 2.0 --expected-commit <COMMIT> --require-clean
Get-FileHash .\dist\BiliDownloader.v2.0.exe -Algorithm SHA256
```
