# Third Party Notices

本文件用于记录项目直接使用或发布包可能包含的主要第三方组件。发布前请维护者根据实际依赖版本、发布包内容和上游许可证文件再次确认。

## Python

项目从源码运行需要 Python。Python 本身遵循 Python Software Foundation License。发布 Windows 二进制包时，如包含 Python 运行时文件，请确认对应版本的许可证和通知要求。

## PySide6 / Qt for Python

项目使用 PySide6 构建桌面界面。PySide6 与 Qt 相关组件的许可证义务取决于具体版本、使用方式和分发方式。发布前请维护者确认所用版本的许可证、动态链接要求、notice 要求以及是否需要提供对应许可证文本。

## yt-dlp

项目使用 yt-dlp 解析和下载用户有权访问的视频内容。yt-dlp 的许可证和第三方 notice 以其上游项目和当前安装包元数据为准。发布前请维护者确认发布包中包含的 yt-dlp 版本及许可证文件。

## FFmpeg

FFmpeg 用于合并音视频。

当前仓库准备时未发现 `tools/ffmpeg.exe`。如果仓库不包含 FFmpeg 二进制文件，用户需要自行安装 FFmpeg 并加入 `PATH`，或由维护者在发布说明中提供合规的安装指引。

如果维护者决定随仓库或发布包分发 `ffmpeg.exe` / `ffprobe.exe`，必须在发布前确认：

- FFmpeg 构建来源。
- 构建使用的是 LGPL、GPL 或其他组合许可证条件。
- 是否需要提供源码、构建参数、许可证文本和修改说明。
- 是否允许与当前发布方式一起分发。

## Playwright

项目使用 Playwright 进行扫码登录流程中的浏览器自动化。Playwright Python 包、Playwright driver、Node.js 运行文件以及下载的浏览器二进制可能分别带有自己的许可证和第三方 notice。发布包含 Playwright Chromium 的包前，请维护者确认对应 notice 文件已随包提供。

## PyInstaller

项目使用 PyInstaller 打包 Windows exe。发布前请确认 PyInstaller bootloader、运行时文件和生成产物的许可证通知要求。

## 其他 Python 依赖

`requirements.txt` 中还包含 requests、certifi、websockets 等依赖。发布前请维护者根据实际锁定版本收集并核对许可证信息。

## 维护者发布前确认

请不要仅依赖本文件作为最终法律结论。本文件不是法律意见；维护者应在首次公开发布前核对每个依赖包和二进制文件的上游许可证、notice 和再分发要求。
