# Bili Downloader Lite

一个 Windows x64 单窗口桌面程序，用于解析并下载用户有权访问的 Bilibili 视频内容。当前源码与候选发布版本为 **2.0**；已公开版本以 [GitHub Releases](https://github.com/Qrzzzz/bili-downloader/releases) 为准。

本项目只服务于合法、授权、个人备份或学习场景，不支持绕过会员、付费、DRM、地区、风控或其他访问限制。

## 功能与边界

- 支持 Bilibili 视频链接、BV 号和 av 号，显示标题、UP 主、时长、封面、分 P 和当前可用清晰度。
- 通过 yt-dlp Python API 下载，支持进度、取消、逐分 P 结果、失败项重试、日志和诊断。
- 扫码登录直接在应用内显示本地生成的二维码；不启动浏览器，不读取日常浏览器 Cookie，不要求输入账号密码。
- 保持 Python 3.13 + PySide6 + yt-dlp + PyInstaller 的 Windows x64 单窗口形态。不使用 Electron、Tauri、WebView2、QtWebEngine、后台服务或自动更新器。
- 不安装或内置 Chromium，不捆绑 FFmpeg。

## 发布资产

v2.0 Release 标题固定为 `Bili Downloader Lite v2.0`，并只包含：

- `BiliDownloader.v2.0.exe`
- `BiliDownloader.v2.0.sbom.json`
- `SHA256SUMS`

版本只使用两级数字。发布时，源码、标签、PE 元数据、文件名与 Release 标题必须一致。

## 从源码运行

已验证目标为 Windows x64 + Python 3.13。`requirements.txt` 锁定完整传递依赖和 SHA-256，并只允许 wheel：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

`requirements.in`、`requirements-dev.in` 和 `requirements-sbom.in` 记录直接依赖；对应 `.txt` 是 Python 3.13/Windows x64 哈希锁。v2.0 的二维码渲染依赖仍为无传递依赖的 `segno==1.6.6`，本版没有新增运行时依赖。

## 打包 Windows exe

```powershell
# onedir
.\build.ps1 -Clean

# onefile 候选发布包
.\build.ps1 -Clean -OneFile
```

默认 onedir 输出为 `dist\BiliDownloader\BiliDownloader.v2.0.exe`，onefile 输出为 `dist\BiliDownloader.v2.0.exe`。`build.ps1` 每次删除并重建 `build\.venv`，不复用开发环境。

发布前必须运行 package smoke、PE/内嵌版本/提交校验和 PyInstaller 归档审计。审计会拒绝 Playwright 包与 driver/Node、`ms-playwright`、Chromium、Electron runtime、浏览器 profile、FFmpeg、凭据和日志。EXE 只测量实际字节和 MiB，不设体积阈值。

## 应用内扫码登录

1. 程序用有限超时的 `requests.Session` 请求 Bilibili 官方网页扫码接口。
2. 应用使用 Segno 在本地生成带完整 quiet zone 的 PNG，只显示图像，不展示或记录 key/URL。
3. 等待扫码、已扫码待手机确认、过期、刷新、成功、取消、超时和网络/协议异常均有明确状态。刷新会废弃旧 key 和旧会话。
4. 候选 Cookie 先经域名、名称和字段白名单过滤，再请求 NAV API 验证。只有服务端确认有效后，才会在跨线程/跨进程锁内用 DPAPI 原子替换 canonical session。

取消、过期、超时、离线、HTTP 412、协议异常或保存失败都不会删除或覆盖原有登录态。HTTP 412 会被如实报告为外部平台限制，程序不尝试绕过。

v1.2/v1.3 的 DPAPI canonical session schema 保持可读。程序先确认 canonical 数据可解密且结构有效，再精确删除本程序拥有的旧 `storage_state.json` / `cookies.txt`、`playwright-profile` / `login-cache`；损坏 canonical 不触发旧明文删除。过期 lease、失败原子临时文件和历史隔离残留仅在精确命名、应用目录所有权和保守年龄条件同时满足时清理。每个活动 lease 另有独立跨进程锁，因此多个实例可以同时读取各自的临时文件，清理与退出登录则会保守避开仍在使用的 lease。

## 诊断、更新与公共网络 smoke

打开“环境诊断”不会联网；它只检查本地二维码组件、FFmpeg、目录和本地登录态。只有用户点击“检查更新”时才访问 GitHub，且只接受可选 `v` / `V` 前缀加两级数字版本。

PR/main 质量门禁的网络逻辑全部 mock。定时/手动 `public-smoke.yml` 独立运行匿名解析和“生成二维码 + 首次等待态”协议检查；`412` / `environment_blocked_412` 会失败并保留 JSON 证据。

## 清晰度与 FFmpeg

程序只展示 yt-dlp 在当前账号、视频、地区、平台策略和支持能力下实际解析到的格式。FFmpeg 用于合并音视频流；v2.0 不捆绑 `ffmpeg.exe` 或 `ffprobe.exe`。程序依次查找 EXE 相邻的 `tools\ffmpeg.exe`、PyInstaller 资源目录中的同一布局和绝对 `PATH` 目录；不会从任意当前工作目录执行 FFmpeg。onefile 用户可把自行取得且合规的 `ffmpeg.exe` 放在 `BiliDownloader.v2.0.exe` 相邻的 `tools` 文件夹中，无需修改系统 `PATH`。

## URL、短链与封面边界

- 输入、b23 最终目标和内部 canonical 视频 URL 共用独立的视频 URL 校验边界：只接受官方 HTTPS 主机、无用户信息、无异常端口的 BV/av 视频页与正整数 `p` 参数。
- b23 展开禁用自动重定向，逐跳验证 301/302/303/307/308，限制跳数并拒绝循环、协议降级、外域、localhost、私网/IP literal 和畸形 `Location`。
- 封面仅从 Bilibili/官方 CDN HTTPS 来源以 streaming 下载；连接/读取超时、Content-Type、声明长度和实际字节均有硬边界。封面失败只影响封面展示，不会把已成功的视频解析改判为失败。

## 运行恢复与多实例

运行标记使用版本化、原子写入的每实例文件，并用 Windows PID + 进程创建时间识别活跃实例、PID 重用和异常退出残留。程序不强制单实例；正常退出只删除本实例拥有的标记，不会删除其他活跃实例状态。

## 隐私与安全

- 程序本地运行，不收集遥测，不上传视频链接、下载记录、Cookie、账号信息或日志。
- `qrcode_key`、完整轮询/成功回调 URL、`refresh_token`、Cookie 和响应原文不会记录或展示。
- 提交 issue/PR 前请脱敏，不要上传 `SESSDATA`、`bili_jct`、`DedeUserID`、`storage_state.json`、`cookies.txt`、session/profile 或账号截图。

更完整的发布、安全和合规要求见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)、[SECURITY.md](SECURITY.md) 与 [DISCLAIMER.md](DISCLAIMER.md)。

## 鸣谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/)
- [Segno](https://segno.readthedocs.io/)
- [FFmpeg](https://ffmpeg.org/)
- [PyInstaller](https://pyinstaller.org/)
