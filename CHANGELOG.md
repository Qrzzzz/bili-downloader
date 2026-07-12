# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
