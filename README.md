<div align="center">

# 📺 Bili Downloader Lite

### 简洁、原生的 Bilibili 视频解析与下载工具

**Bilibili 链接 / BV / av · 分 P 选择 · 当前账号可用画质 · 应用内扫码登录 · Windows x64**

<p>
  <strong>导航</strong><br/>
  <a href="https://github.com/Qrzzzz/bili-downloader/releases/latest">下载最新版</a> ·
  <a href="./docs/releases/v2.3.md">v2.3 发布说明</a> ·
  <a href="#主要功能">主要功能</a> ·
  <a href="#从源码运行">从源码运行</a> ·
  <a href="./SECURITY.md">安全策略</a> ·
  <a href="./DISCLAIMER.md">使用声明</a> ·
  <a href="./LICENSE">许可证</a>
</p>

![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D4)
![Runtime](https://img.shields.io/badge/Runtime-Python%203.13-3776AB)
![UI](https://img.shields.io/badge/UI-PySide6-41CD52)
![Downloader](https://img.shields.io/badge/Downloader-yt--dlp-FF5722)
![Release](https://img.shields.io/github/v/release/Qrzzzz/bili-downloader)
![License](https://img.shields.io/badge/License-MIT-7C3AED)

</div>

---

> 本项目仅用于合法、授权、个人备份或学习场景。使用者应确保自己拥有下载和使用相关内容的权利，并遵守 Bilibili 服务条款及所在地法律法规。本项目与 Bilibili 官方无关，不提供或支持会员、付费、地区、风控、DRM 等限制的绕过能力。

## 📦 下载与使用

Windows x64 发布文件请从 [GitHub Releases](https://github.com/Qrzzzz/bili-downloader/releases/latest) 获取。v2.3 的文件名为：

* 主程序：`BiliDownloader.v2.3.exe`
* 软件物料清单：`BiliDownloader.v2.3.sbom.json`
* 校验文件：`SHA256SUMS`

主程序为单文件应用，无需安装 Python 或 Node.js。下载前请自行准备合法来源的 `ffmpeg.exe`，并选择以下任一方式放置：

1. 放入主程序相邻的 `tools\ffmpeg.exe`。
2. 将 FFmpeg 所在的绝对目录加入系统 `PATH`。

程序不会下载、安装或捆绑 FFmpeg，也不会从任意当前工作目录执行它。

### 基本流程

1. 运行 `BiliDownloader.v2.3.exe`。
2. 粘贴 Bilibili 视频链接、BV 号或 av 号并解析。
3. 如需账号权限下的更多可用画质，可使用应用内二维码扫码登录。
4. 选择音视频 MP4 或仅音频 MP3，按需选择分 P、画质和保存目录后开始下载。
5. 在任务结果中查看成功、失败或取消的项目，并按需重试失败项。

### v2.3 更新重点

* 下载完成后不再弹出独立窗口，结果直接显示在主窗口任务状态下方。
* 单 P 成功结果保持紧凑；多 P、多输出、失败、取消与失败项重试仍保留完整明细。
* 保留打开文件、打开所在目录和收起结果操作，主流程无需在两个窗口之间切换。
* 没有新增运行时依赖，也不改变下载、登录、FFmpeg 或既有安全边界。

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

* 初始界面聚焦链接输入、解析和账号状态
* 解析成功后再显示视频信息、分 P、画质、保存目录和下载操作
* 下载期间突出显示进度与取消入口，任务结束后在主窗口下方展示紧凑或完整结果
* 日志、环境诊断、隐私说明和详细任务信息默认收纳，不抢占主流程

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

* 使用 Python 3.13、PySide6、yt-dlp 和 PyInstaller
* 保持 Windows x64 原生单窗口体验
* 不使用 Electron、Tauri、WebView2、QtWebEngine 或后台服务
* 不安装或内置 Chromium，不提供浏览器 Cookie 导入
* 支持多实例运行，并区分正常退出、异常退出和 PID 重用
* 环境诊断默认只检查本地状态；只有用户主动点击“检查更新”时才访问 GitHub

## 🔐 安全与使用边界

本项目不会尝试绕过会员、付费、地区、DRM、风控或其他平台限制。HTTP 412 等平台拒绝会被如实报告，而不会通过削弱校验或伪装请求来规避。

提交 issue、PR、截图或日志前，请删除 `SESSDATA`、`bili_jct`、`DedeUserID`、`qrcode_key`、`refresh_token`、完整扫码回调 URL、Cookie、session/profile 和可识别账号身份的信息。

完整要求请阅读 [安全策略](./SECURITY.md) 与 [使用声明](./DISCLAIMER.md)。

<a id="从源码运行"></a>

## 🧰 从源码运行

已验证环境为 Windows x64 + Python 3.13。依赖锁包含完整传递依赖与 SHA-256，并只允许安装 wheel：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

<details>
<summary><strong>展开查看测试与打包命令</strong></summary>

```powershell
# 安装开发依赖，运行完整测试（已包含三组锁一致性验证）
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
python -m pytest -q
python -m pip check

# 构建 onedir
.\build.ps1 -Clean

# 构建 onefile 候选包
.\build.ps1 -Clean -OneFile
```

默认 onedir 输出为 `dist\BiliDownloader\BiliDownloader.v2.3.exe`，onefile 输出为 `dist\BiliDownloader.v2.3.exe`。`build.ps1` 会重新创建隔离的 `build\.venv`，不会复用开发环境。

PR/main CI 只运行自动回归，纯 Markdown 变更跳过。正式构建集中在 Release，自动完成 PE 与内嵌版本校验、package smoke、归档审计、SBOM、摘要和来源证明。真实扫码、下载、截图与许可证复核按相关改动触发；公网探测仅供手动诊断。详见 [发布检查清单](./RELEASE_CHECKLIST.md) 与 [维护者说明](./MAINTAINER_NOTES.md)。

</details>

## 🙏 致谢

感谢以下开源项目及其维护者：

* [yt-dlp](https://github.com/yt-dlp/yt-dlp) 提供持续维护的视频解析与下载能力
* [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/) 提供原生 Windows 桌面界面
* [Segno](https://segno.readthedocs.io/) 提供本地二维码生成能力
* [Requests](https://requests.readthedocs.io/) 提供受控的网络请求基础
* [FFmpeg](https://ffmpeg.org/) 提供音视频处理能力，由用户自行安装和配置
* [PyInstaller](https://pyinstaller.org/) 提供 Windows 应用打包能力

## 📄 许可证

本项目采用 [MIT License](./LICENSE)。你可以在许可证条款允许的范围内使用、复制、修改、合并、发布和分发本项目。

第三方组件仍遵循各自的许可证与通知要求，详见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
