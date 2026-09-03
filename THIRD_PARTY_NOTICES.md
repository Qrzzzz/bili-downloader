# Third Party Notices

本文件用于记录项目直接使用或发布包可能包含的主要第三方组件。发布前请维护者根据实际依赖版本、发布包内容和上游许可证文件再次确认。

## Python

项目从源码运行需要 Python。Python 本身遵循 Python Software Foundation License。发布 Windows 二进制包时，如包含 Python 运行时文件，请确认对应版本的许可证和通知要求。

## PySide6 / Qt for Python

项目 v2.3 锁定 PySide6 6.11.2 构建桌面界面。PySide6 与 Qt 相关组件的许可证义务取决于具体版本、使用方式和分发方式。发布前请维护者确认所用版本的许可证、动态链接要求、notice 要求以及是否需要提供对应许可证文本。

## yt-dlp

项目使用 yt-dlp 解析和下载用户有权访问的视频内容。v2.3 锁定 PyPI wheel `yt-dlp==2026.8.19`；其 wheel SHA-256 记录在 `requirements.txt`。yt-dlp 的许可证和第三方 notice 以上游项目和安装包元数据为准。

## FFmpeg

FFmpeg 用于合并音视频，以及将用户选择的仅音频下载转换为 MP3。

当前仓库准备时未发现 `tools/ffmpeg.exe`。仓库与 Release 不分发 FFmpeg；用户可把自行取得且合规的 `ffmpeg.exe` 放到 EXE 相邻 `tools` 目录，或通过绝对 `PATH` 提供。

如果维护者决定随仓库或发布包分发 `ffmpeg.exe` / `ffprobe.exe`，必须在发布前确认：

- FFmpeg 构建来源。
- 构建使用的是 LGPL、GPL 或其他组合许可证条件。
- 是否需要提供源码、构建参数、许可证文本和修改说明。
- 是否允许与当前发布方式一起分发。

## Segno

项目 v2.3 锁定 `segno==1.6.6` 在应用内本地生成二维码 PNG。Segno 是纯 Python 包，在 Python 3.13 上无传递依赖；已核对 wheel 包含 BSD 3-Clause 许可文本 `licenses/LICENSE`。候选发布资料应保留其版权、条件与免责文本。

v2.3 的运行时、构建锁和发布包不包含浏览器自动化包、driver/Node 或浏览器 runtime。历史 CHANGELOG 和旧凭据/profile 迁移清理仍可保留相关历史名称。

## PyInstaller

项目 v2.3 锁定 PyInstaller 6.22.2 打包 Windows exe。发布前请确认 PyInstaller bootloader、运行时文件和生成产物的许可证通知要求。

## 其他 Python 依赖

`requirements.txt` 锁定 requests 2.34.2、certifi 2026.7.22、websockets 17.1 及完整传递依赖。Release 同时发布由锁文件生成的 CycloneDX SBOM；维护者仍应核对实际许可证义务。

## 维护者发布前确认

请不要仅依赖本文件作为最终法律结论。本文件不是法律意见；维护者应在首次公开发布前核对每个依赖包和二进制文件的上游许可证、notice 和再分发要求。
