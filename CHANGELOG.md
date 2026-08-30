# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.3] - 2026-08-30

### 扫码登录

- 将扫码登录改为应用内原生流程：通过独立 `requests.Session` 调用 Bilibili 官方网页扫码接口，使用 Segno 在本地生成带 quiet zone 的 QR PNG。
- 为等待扫码、已扫码待确认、过期/刷新、成功、取消、超时、网络失败、HTTP 412 和协议异常增加明确状态与 fail-closed 契约。
- 刷新二维码时废弃旧 key/会话并丢弃在途旧轮询结果；关闭、连续取消/刷新、成功后立即重开和应用退出保持协作式线程收敛。
- `qrcode_key`、完整轮询/成功回调 URL、`refresh_token`、Cookie 和响应原文纳入日志脱敏边界。

### 登录态事务与兼容

- 将浏览器上下文绑定保存入口替换为通用候选 Cookie API：先限定域/名称/字段并通过 NAV API 服务端验证，然后在跨线程/跨进程锁内用 DPAPI 原子替换 canonical session。
- 取消、超时、412、离线、协议异常、验证失败或保存失败均保留原有凭据。
- 保持 v1.2 DPAPI schema 可读，并继续安全迁移旧 `storage_state.json` / `cookies.txt`、清理旧 profile/cache 残留。临时 Netscape lease 与匿名模式契约不变。

### 依赖、诊断与发布链

- 从运行时、完整哈希锁、PyInstaller spec、package smoke 和发布包中移除 Playwright 及其 driver/Node/专用传递依赖，不保留浏览器 fallback。
- 环境诊断改为不联网的本地二维码组件/登录态说明；update checker 严格只接受可选 `v` / `V` 加两级数字版本。
- 定时/手动公共网络 smoke 增加匿名二维码生成与首次等待态证据，不进入 PR/main 确定性门禁。
- 归档审计扩展到内嵌 PYZ，拒绝 Playwright、driver/Node、Chromium、Electron、profile、FFmpeg、凭据和日志；继续只测量体积而不设阈值。

## [1.2] - 2026-08-30

### 修复

- 以新的 `v1.2` 源码、标签和二进制一致性链替代 v1.1 的错配状态；已公开的 `v1.1` 标签保持原位，不移动、不删除、不重写。
- 匿名公共网络 smoke 遇到 HTTP 412 / `environment_blocked_412` 时现在明确失败，不再把环境阻断伪装为成功。

### 构建与供应链

- 将直接依赖和完整传递依赖锁定到经过验证的版本与 SHA-256；构建固定为 Windows x64 + Python 3.13，并且每次重建干净虚拟环境。
- 将 yt-dlp 更新到稳定版 2026.8.19，覆盖上游 2026-07-03 的 Bilibili API 解析修复，并完成真实匿名解析验证。
- 拆分确定性的 PR/main 质量检查与定时/手动公共网络 smoke；所有外部 Actions 固定到审核过的完整提交 SHA，并配置最小权限和并发控制。
- 新增仅由 `v1.2` 标签触发的正式发布工作流，强制校验标签、两级源码版本、构建提交、PE 元数据和资产名称一致。
- 正式发布生成 `BiliDownloader.v1.2.exe`、`BiliDownloader.v1.2.sbom.json` 与 `SHA256SUMS`，并为全部资产生成 GitHub artifact attestation。
- 发布后通过 GitHub API/CLI 复核 Release 状态、标题、标签提交、资产数量、名称、大小、API digest 和 attestation。
- 增加 onefile 归档审计；测量实际体积但不设置体积阈值，并拒绝意外捆绑 Chromium、FFmpeg、凭据、浏览器 profile 或日志。

### 范围

- 保持 Python、PySide6、yt-dlp、PyInstaller、Playwright 和单窗口 Lite 形态；未扩张登录实现，未安装或内置 Chromium，未读取日常浏览器 Cookie，也未捆绑 FFmpeg。

## [1.1] - 2026-07-12

本版本重点改善故障自查、下载完成后的操作体验，并恢复 Lite 版本应有的轻量体积。

### 新增

- 新增独立环境诊断窗口，可检查程序、Python、Windows、yt-dlp、FFmpeg、登录浏览器、应用目录、下载目录和本地登录状态。
- 支持一键复制脱敏诊断报告，不包含 Cookie、账号标识、视频链接或完整用户目录。
- 新增手动 GitHub 更新检查；程序启动和打开诊断窗口时不会自动联网，也不会自动下载或安装更新。
- 新增逐分 P 下载结果窗口，清晰展示成功、失败、取消、输出文件和友好错误信息。
- 下载完成后可直接打开文件或所在目录；文件被移动或删除后会自动禁用对应操作。
- 支持仅重试失败分 P，并沿用原任务的清晰度、保存目录和登录模式；已完成或取消的分 P 不会重复下载。

### 改进

- 解析预检、磁盘空间、目录权限和 FFmpeg 等批次级错误现在也会进入统一下载结果窗口。
- 更改链接或开始解析新视频后，旧结果仍可查看和打开文件，但不能重试旧下载目标。
- 扫码登录和环境检测优先使用 Windows 自带 Microsoft Edge，其次使用系统 Chrome。
- 发布包不再内置 Chromium，EXE 体积由约 383 MiB 降至约 91 MiB。
- 产品名称、窗口标题、Windows 元数据和发布文件名统一为 **Bili Downloader Lite V1.1** 与 `BiliDownloader.v1.1.exe`。

### 验证

- 63 项自动化测试全部通过。
- Python 编译、模块导入、依赖一致性和源码 self-test 通过。
- 系统 Edge Playwright smoke、onedir 和 onefile package smoke 通过。
- onedir ZIP 与 onefile EXE 均确认不包含 `ms-playwright` 或 Chromium 浏览器文件。

## [1.0] - 2026-07-12

### Added

- Initial public release preparation.
- Repository-level documentation, compliance notes, security policy, contribution guide, issue template, PR template, release checklist, and ignore rules.
- Protected local Bilibili session storage with explicit server-validation states and safe anonymous mode.
- Deterministic b23 and multi-part parsing, strict cross-part quality checks, monotonic batch progress, and partial-result reporting.
- Automated lifecycle, privacy, download, configuration, logging, Playwright, and packaging regression gates.

### Fixed

- Native crashes when QR login succeeded, was cancelled, timed out, or the dialog was closed.
- Stale parsed targets remaining downloadable after the URL changed or a later parse failed.
- Unsafe shutdown while parsing, validating a session, downloading, or waiting for FFmpeg.
- Credential cleanup, log redaction/rotation, FFmpeg probing, and atomic configuration persistence edge cases.
