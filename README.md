# BiliDownloader

一个 Windows 桌面程序，用于在用户提供 Bilibili 视频链接后解析并下载用户有权访问的视频内容。

当前稳定版本：**1.0**。

本项目的目标是提供一个本地运行的桌面工具，帮助用户在合法、授权、个人备份或学习场景下处理自己有权访问的视频内容。项目不支持绕过会员、付费、DRM、地区限制、风控或任何访问权限限制，也不鼓励或允许侵犯版权。

## 功能特性

- 输入 Bilibili 视频链接、BV 号或 av 号后解析视频信息。
- 显示视频标题、UP 主、时长、封面和分 P 列表。
- 展示 yt-dlp 在当前登录状态下实际解析到的可用清晰度。
- 支持使用 Bilibili 官方登录页面扫码登录。
- 支持选择分 P、保存目录和下载清晰度。
- 使用 yt-dlp Python API 下载，使用 FFmpeg 合并音视频。
- 支持下载进度、重试、取消下载、日志窗口和本地日志文件。
- 登录态仅保存在用户本机应用数据目录中。

## 安装方式

面向普通用户，推荐下载维护者在 GitHub Releases 中发布的 Windows 版本。

发布包应至少包含：

- `BiliDownloader.exe`
- 必要的运行时文件
- 发布说明
- 校验值，例如 SHA256

请只从项目维护者声明的官方发布页下载程序，不要运行来源不明的二进制文件。

## 从源码运行

推荐使用 Python 3.11 或 3.12。较新的 Python 版本是否可用，取决于 PySide6、Playwright、PyInstaller 等依赖是否提供对应 wheel。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path (Get-Location) "ms-playwright")
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m app.main
```

如果不使用项目内的 `ms-playwright\` 目录，也可以按 Playwright 默认方式安装浏览器。不要把 Playwright 下载的浏览器目录提交到 Git 仓库。

## 打包 Windows exe

项目提供 `build.ps1` 用于本地打包：

```powershell
.\build.ps1
```

默认输出位置：

```text
dist\BiliDownloader\BiliDownloader.exe
```

清理后重新打包：

```powershell
.\build.ps1 -Clean
```

可选 onefile 打包：

```powershell
.\build.ps1 -OneFile
```

onefile 启动通常更慢，且 Playwright Chromium 与 FFmpeg 等外部依赖更难排查。面向公开发布时，建议维护者先验证 onedir 包。

如果当前网络无法下载 Playwright Chromium，但接受使用系统 Chrome 或 Edge fallback，可以跳过浏览器下载：

```powershell
.\build.ps1 -SkipPlaywrightBrowserInstall
```

发布前请阅读 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)，确认没有把本地登录态、日志、下载文件、浏览器 profile、构建缓存或未确认许可证义务的二进制文件放入仓库或发布包。

## 扫码登录说明

程序使用 Playwright 打开 Bilibili 官方登录页面：

```text
https://passport.bilibili.com/login
```

用户使用 Bilibili 手机客户端扫码确认后，程序只读取此次扫码登录上下文中的 Bilibili 登录 Cookie，并在本机应用数据目录中保存登录态。项目不要求输入 Bilibili 账号密码，不读取用户日常浏览器中的 Cookie，也不把登录态上传到任何服务器。

登录态文件属于敏感本地数据，不应提交到 GitHub，也不应附在 issue、PR、截图或日志中。

## 下载清晰度说明

程序只展示 yt-dlp 在当前环境和当前登录状态下实际解析到的可用格式。

清晰度取决于账号权限、视频本身、平台限制和 yt-dlp 支持情况，包括但不限于：

- 用户账号是否有权访问该内容和对应清晰度。
- 视频本身是否提供该清晰度。
- 当前地区、平台策略、风控状态和服务端限制。
- yt-dlp 当前版本对 Bilibili 的支持情况。

如果账号没有权限访问某个清晰度或内容，程序不会绕过限制。

## FFmpeg 说明

FFmpeg 用于合并音视频流。你可以选择以下方式之一：

- 自行安装 FFmpeg，并将其加入系统 `PATH`。
- 在维护者确认许可证义务后，将 `ffmpeg.exe` 放入 `tools\ffmpeg.exe` 并参与本地打包。

当前仓库不应默认提交 `ffmpeg.exe` 或 `ffprobe.exe`。如果维护者决定随发布包分发 FFmpeg，必须在发布前确认 FFmpeg 构建来源、许可证类型以及对应的 LGPL/GPL 合规义务，并在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中更新说明。

## 常见问题

### 扫码登录打不开怎么办？

请先确认已安装 Playwright Chromium：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path (Get-Location) "ms-playwright")
.\.venv\Scripts\python.exe -m playwright install chromium
```

如果是打包后的程序，请确认发布包中包含必要的 Playwright 运行文件，或者系统中已安装可用的 Chrome / Edge。

### 解析失败怎么办？

常见原因包括：

- 链接无效或视频不存在。
- 当前网络无法访问 Bilibili。
- 登录态已失效，需要重新扫码登录。
- 视频需要账号权限、会员权限、付费权限、地区权限，或受到平台限制。
- yt-dlp 的 Bilibili 提取器需要更新。

从源码运行时可尝试更新 yt-dlp：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade yt-dlp
```

### 高分辨率不可用怎么办？

1080p 高码率、4K、8K 等清晰度是否可用，取决于视频本身、账号权限、登录状态、地区、平台限制和 yt-dlp 当前解析结果。程序不会绕过用户无权访问的内容或清晰度。

### FFmpeg 缺失怎么办？

安装 FFmpeg 并加入 `PATH`，或在维护者确认许可证后将 `ffmpeg.exe` 放到：

```text
tools\ffmpeg.exe
```

然后重新运行或重新打包。

### 杀毒软件提示怎么办？

PyInstaller 打包的 Windows 程序有时会被误报。维护者发布时应提供构建说明、源码对应的 tag、SHA256 校验值，并尽量使用干净环境构建。用户应只运行可信来源的发布包。

## 合规与版权声明

本项目仅用于合法、授权、个人备份或学习用途。使用者应自行确保拥有下载、保存和使用相关内容的权利，并遵守 Bilibili 服务条款、相关平台规则以及所在地法律法规。

本项目不提供、不支持、不接受任何绕过会员、付费、DRM、地区限制、风控或其他访问权限限制的功能或贡献。

## 隐私说明

- 程序在本机运行，不收集遥测数据。
- 项目不提供云端服务，不上传视频链接、下载记录、Cookie、账号信息或日志。
- 扫码登录产生的登录态只保存在用户本机。
- 提交 issue 或 PR 时，请先脱敏日志，不要上传 Cookie、`SESSDATA`、`bili_jct`、`DedeUserID`、`storage_state.json`、`cookies.txt` 或浏览器 profile。

## 免责声明

本项目与 Bilibili 官方无关。项目维护者不对使用者下载、保存、传播或使用内容的行为承担责任。使用本项目即表示你理解并同意自行承担合规责任。

更完整说明见 [DISCLAIMER.md](DISCLAIMER.md)。

## 鸣谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/)
- [Playwright](https://playwright.dev/python/)
- [FFmpeg](https://ffmpeg.org/)
- [PyInstaller](https://pyinstaller.org/)
