# Third Party Notices

本文件用于记录项目直接使用或发布包可能包含的主要第三方组件。发布前请维护者根据实际依赖版本、发布包内容和上游许可证文件再次确认。

## Python

项目从源码运行需要 Python。Python 本身遵循 Python Software Foundation License。发布 Windows 二进制包时，如包含 Python 运行时文件，请确认对应版本的许可证和通知要求。

## Windows App SDK / WinUI 3 / .NET

v2.5 使用 Microsoft.WindowsAppSDK 2.4.0、Windows SDK BuildTools 10.0.28000.2705 与 .NET 10。直接和传递 NuGet 版本固定于 `BiliDownloader.WinUI/packages.lock.json`，由自包含发布复制官方运行库。实际 .NET 运行时版本由发布目录 runtimeconfig 与联合 SBOM 记录。

包中的 `licenses/nuget/` 保留恢复包自带的 license、notice 和 nuspec，包含 Windows App SDK、WinUI、.NET runtime 以及其他 SDK 传递依赖的通知。SDK 附带的 Microsoft.Web.WebView2 projection/loader 也记录在清单中；应用没有 WebView 控件、HTML 页面或 Chromium/Edge runtime。不能因为应用未使用某个控件就删掉它所属 SDK 的 notice。

v2.5 不再分发 PySide6、Shiboken 或 Qt。历史版本记录中的 Qt 名称不代表当前依赖。

## yt-dlp

项目使用 yt-dlp 解析和下载用户有权访问的视频内容。v2.5 保留 PyPI wheel `yt-dlp==2026.8.19`；其 wheel SHA-256 记录在 `requirements.txt`。yt-dlp 的许可证和第三方 notice 以上游项目和安装包元数据为准。

## FFmpeg

FFmpeg 用于合并音视频，以及将用户选择的仅音频下载转换为 MP3。

仓库与 Release 不分发 FFmpeg；用户可把自行取得且合规的 `ffmpeg.exe` 放到两个 EXE 相邻的 `tools` 目录，或通过绝对 `PATH` 提供。

如果维护者决定随仓库或发布包分发 `ffmpeg.exe` / `ffprobe.exe`，必须在发布前确认：

- FFmpeg 构建来源。
- 构建使用的是 LGPL、GPL 或其他组合许可证条件。
- 是否需要提供源码、构建参数、许可证文本和修改说明。
- 是否允许与当前发布方式一起分发。

## Segno

项目 v2.5 保留 `segno==1.6.6` 在后端本地生成二维码 PNG。Segno 是纯 Python 包，在 Python 3.13 上无传递依赖；wheel 包含 BSD 3-Clause 许可文本 `licenses/LICENSE`，由组包脚本复制到发行目录的许可证资料中。

v2.5 不包含浏览器自动化包、driver/Node 或浏览器 runtime。历史 CHANGELOG 和旧凭据/profile 迁移清理仍可保留相关历史名称。

## PyInstaller

项目 v2.5 保留 PyInstaller 6.22.2 打包 Python 后端 EXE，前端由 .NET SDK 发布。发行资料保留 PyInstaller 的 GPL 及 bootloader exception 通知，以上游安装包中的实际文本为准。

## 其他 Python 依赖

`requirements.txt` 锁定 requests 2.34.2、certifi 2026.7.22、websockets 17.1 及传递依赖。组包脚本复制 CPython LICENSE.txt 和干净 Python 构建环境中的许可证文件。联合 CycloneDX SBOM 包括 Python/NuGet 构建依赖清单、实际 .NET runtime 以及每个交付文件的 SHA-256；构建依赖清单不等同于运行时可达性判断。

## 维护者发布前确认

请不要仅依赖本文件作为最终法律结论。本文件不是法律意见；维护者应在首次公开发布前核对每个依赖包和二进制文件的上游许可证、notice 和再分发要求。
