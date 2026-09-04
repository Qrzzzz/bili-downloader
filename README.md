<div align="center">

# 📺 Bili Downloader Lite

### 简洁、原生的 Bilibili 视频解析与下载工具

**Bilibili 链接 / BV / av · 分 P 选择 · 当前账号可用画质 · 应用内扫码登录 · Windows x64**

<p>
  <strong>导航</strong><br/>
  <a href="https://github.com/Qrzzzz/bili-downloader/releases/latest">下载最新版</a> ·
  <a href="./docs/releases/v2.6.md">v2.6 发布说明</a> ·
  <a href="#主要功能">主要功能</a> ·
  <a href="#从源码运行">从源码运行</a> ·
  <a href="./SECURITY.md">安全策略</a> ·
  <a href="./DISCLAIMER.md">使用声明</a> ·
  <a href="./LICENSE">许可证</a>
</p>

![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D4)
![Runtime](https://img.shields.io/badge/Runtime-Python%203.13-3776AB)
![UI](https://img.shields.io/badge/UI-WinUI%203-0078D4)
![Downloader](https://img.shields.io/badge/Downloader-yt--dlp-FF5722)
![Release](https://img.shields.io/github/v/release/Qrzzzz/bili-downloader)
![License](https://img.shields.io/badge/License-MIT-7C3AED)

</div>

---

> 本项目仅用于合法、授权、个人备份或学习场景。使用者应确保自己拥有下载和使用相关内容的权利，并遵守 Bilibili 服务条款及所在地法律法规。本项目与 Bilibili 官方无关，不提供或支持会员、付费、地区、风控、DRM 等限制的绕过能力。

## 📦 下载与使用

**v2.6** 对 WinUI 3 界面做整体打磨，改善窗口布局、任务反馈、结果展示和设置草稿。见 [发布说明](./docs/releases/v2.6.md) 和 [验收记录](./docs/validation/v2.6.md)。

Windows x64 版本请从 [GitHub Releases](https://github.com/Qrzzzz/bili-downloader/releases/latest) 获取。v2.6 使用 WinUI 3，发行资产为：

* 完整应用包：`BiliDownloader.v2.6.win-x64.zip`
* 软件物料清单：`BiliDownloader.v2.6.sbom.json`
* 校验文件：`SHA256SUMS`

完整解压 ZIP 后运行其中的 `BiliDownloader.v2.6.exe`，保留相邻的后端 EXE 和运行库。包中包含 .NET、Windows App SDK 和 Python 运行时，无需另装这些运行环境。下载前请自行准备合法来源的 `ffmpeg.exe`，并选择以下任一方式放置：

1. 放入主程序相邻的 `tools\ffmpeg.exe`。
2. 将 FFmpeg 所在的绝对目录加入系统 `PATH`。

程序不会下载、安装或捆绑 FFmpeg，也不会从任意当前工作目录执行它。

### 基本流程

1. 运行完整解压目录中的 `BiliDownloader.v2.6.exe`。
2. 粘贴 Bilibili 视频链接、BV 号或 av 号并解析。
3. 如需账号权限下的更多可用画质，可在“账号”页使用应用内二维码扫码登录。
4. 选择音视频 MP4 或仅音频 MP3，按需选择分 P、画质和保存目录后开始下载。
5. 在任务结果中查看成功、失败或取消的项目，并按需重试失败项。

### WinUI 3 原生界面

* C#/.NET + XAML 使用 Microsoft 官方 Windows App SDK / WinUI 3；主窗口是真实 `Microsoft.UI.Xaml.Window`。
* 使用 `MicaBackdrop`、`TitleBar`、`NavigationView`、`Frame`、独立 `Page` 和 `InfoBar`；系统管理 caption 按钮与窗口行为。
* 采用默认 WinUI 控件、主题资源和焦点状态，提供跟随 Windows、浅色与深色偏好。
* Python 后端移除 Qt 依赖，通过版本化 JSONL 管道提供解析、下载、取消、扫码、凭据和诊断服务。
* 保留 yt-dlp、Bilibili URL 校验、二维码协议、DPAPI、FFmpeg 管理、失败分类和日志逻辑。详见 [架构说明](./docs/architecture/v2.5-winui.md)。

### v2.6 更新重点

* 下载规格根据窗口宽度重排，统一三个页面的间距和原生卡片层次。
* 下载中保护链接与任务状态；区分解析、下载、转码、取消及其他页面的忙碌状态。
* 结果按文件名展示，保留逐 P 详情和失败重试，隐藏无文件时的空选择器。
* 账号操作跟随真实登录状态；切页保留设置草稿，外观可即时预览。

<a id="主要功能"></a>

## ✨ 主要功能

### 🎬 视频解析与下载

* 支持标准 Bilibili 视频链接、`b23.tv` 短链、BV 号和 av 号
* 显示标题、UP 主、时长、封面、分 P 和当前实际可用画质
* 支持音视频 MP4 和 192 kbps MP3 仅音频下载
* 支持单 P 与多 P 选择、下载进度、取消、逐项结果和失败项重试
* 通过 yt-dlp Python API 工作，不启动命令行子进程解析视频
* 只展示当前账号、视频、地区和平台策略实际允许访问的格式

### 🧭 清晰的任务流程

* 下载页聚焦链接输入与解析，账号管理和应用设置各有独立页面
* 解析成功后再显示视频信息、分 P、画质、保存目录和下载操作
* 下载期间突出显示进度与取消入口，任务结束后在主窗口下方展示紧凑或完整结果
* 任务详情可在下载页展开；环境诊断和错误日志位于设置页，登录与隐私说明位于账号页
* 切页保留当前任务和结果，解析或下载期间暂停账号状态变更

### 📱 应用内扫码登录

* 二维码由应用使用 Segno 在本地生成，不启动浏览器
* 不要求输入账号密码，也不读取日常浏览器 Cookie
* 覆盖等待扫码、手机确认、过期、刷新、取消、超时和网络异常状态
* 候选 Cookie 经过允许列表和 Bilibili NAV API 验证后，才会原子保存
* Windows 登录态使用 DPAPI 保护；匿名模式不会读取已保存凭据

### 🛡️ URL、封面与隐私边界

* 视频输入和短链最终目标只接受受支持的 Bilibili 官方 HTTPS 地址
* `b23.tv` 重定向逐跳校验，拒绝协议降级、外域、循环、异常端口和 IP 地址
* 封面只通过 HTTPS 从 Bilibili 或官方 CDN 读取，并限制响应类型、超时和实际 5 MiB 大小
* 封面失败只影响封面展示，不会覆盖已经成功的视频解析结果
* 不收集遥测，不上传视频链接、下载记录、Cookie、账号信息或日志

### 🪟 原生 Windows 桌面应用

* 前端使用 .NET 10、Windows App SDK 2.4.0（SDK 版本与应用版本独立）；后端使用 Python 3.13、yt-dlp 和 PyInstaller
* 保持 Windows x64 原生单窗口体验
* 保留系统窗口按钮、缩放边框与窗口菜单；支持浅色、深色和跟随系统主题
* 界面不使用 Qt、Electron、Tauri、HTML 或 WebView；SDK 自带的 WebView2 互操作依赖不用于页面渲染
* 不安装或内置 Chromium，不提供浏览器 Cookie 导入
* 支持多实例运行，并区分正常退出、异常退出和 PID 重用
* 环境诊断默认只检查本地状态；只有用户主动点击“检查更新”时才访问 GitHub

## 🔐 安全与使用边界

本项目不会尝试绕过会员、付费、地区、DRM、风控或其他平台限制。HTTP 412 等平台拒绝会被如实报告，而不会通过削弱校验或伪装请求来规避。

提交 issue、PR、截图或日志前，请删除 `SESSDATA`、`bili_jct`、`DedeUserID`、`qrcode_key`、`refresh_token`、完整扫码回调 URL、Cookie、session/profile 和可识别账号身份的信息。

完整要求请阅读 [安全策略](./SECURITY.md) 与 [使用声明](./DISCLAIMER.md)。

<a id="从源码运行"></a>

## 🧰 从源码运行

开发需要 Windows x64、Python 3.13 和 `global.json` 指定的 .NET 10 SDK（当前 10.0.400）。前端支持的最低系统目标为 Windows 10 1809；Mica 的实际效果由系统能力决定，完整窗口验收以 Windows 11 为主。Python 依赖锁包含传递依赖和 SHA-256；NuGet 使用 `packages.lock.json`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.txt
$env:BILI_BACKEND_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
$env:BILI_BACKEND_SOURCE = (Get-Location).Path
dotnet restore BiliDownloader.WinUI --locked-mode -p:Platform=x64
dotnet run --project BiliDownloader.WinUI -p:Platform=x64
```

<details>
<summary><strong>展开查看测试与打包命令</strong></summary>

```powershell
# 安装开发依赖，运行完整测试（已包含三组锁一致性验证）
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
python -m pytest -q
python -m pip check

$env:BILI_TEST_PYTHON = (Get-Command python).Source
$env:BILI_BACKEND_SOURCE = (Get-Location).Path
dotnet run --project BiliDownloader.WinUI.Tests

# 构建自包含目录与 ZIP 候选包
.\build.ps1 -Clean
.\tools\package_smoke.ps1 -Executable .\dist\BiliDownloader.v2.6.win-x64\BiliDownloader.v2.6.exe
```

输出为 `dist\BiliDownloader.v2.6.win-x64\` 与同名 ZIP。`build.ps1` 重新创建 `build\.venv`，使用哈希锁与 NuGet locked restore。`python -m app.main` 是兼容后端入口，UI 入口已迁移至 WinUI 工程。

PR/main CI 执行 Python 回归、WinUI 编译和 C# 管道测试，纯 Markdown 变更跳过。正式打包集中在 Release，校验前后端版本、原生启动、包内容及联合 SBOM，再生成摘要和来源证明。真实扫码、下载、DPI、读屏和窗口交互的外部验收单独记录。详见 [发布检查清单](./RELEASE_CHECKLIST.md) 与 [维护者说明](./MAINTAINER_NOTES.md)。

</details>

## 🙏 致谢

感谢以下开源项目及其维护者：

* [yt-dlp](https://github.com/yt-dlp/yt-dlp) 提供持续维护的视频解析与下载能力
* [Windows App SDK](https://github.com/microsoft/WindowsAppSDK) 与 [WinUI Gallery](https://github.com/microsoft/WinUI-Gallery) 提供官方 Windows UI API 与参考实现
* [Segno](https://segno.readthedocs.io/) 提供本地二维码生成能力
* [Requests](https://requests.readthedocs.io/) 提供受控的网络请求基础
* [FFmpeg](https://ffmpeg.org/) 提供音视频处理能力，由用户自行安装和配置
* [PyInstaller](https://pyinstaller.org/) 提供 Windows 应用打包能力

## 📄 许可证

本项目采用 [MIT License](./LICENSE)。你可以在许可证条款允许的范围内使用、复制、修改、合并、发布和分发本项目。

第三方组件仍遵循各自的许可证与通知要求，详见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
