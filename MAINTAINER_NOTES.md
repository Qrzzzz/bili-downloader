# Maintainer Notes

本文件记录仓库发布与维护注意事项。

## v1.2 发布边界

- 版本只允许两级：源码 `1.2`、标签 `v1.2`、成品 `BiliDownloader.v1.2.exe`、Release 标题 `Bili Downloader Lite v1.2`。
- 公开 `v1.1` 标签仍指向 `dbfc8a044a3ef5e6b83f6ea22a65c59e9279b592`，而包含 v1.1 功能的主分支合并提交是 `908f91dc7f8662a7b9205c381a53f4d91c2d26ec`。这是历史事实；不得移动、删除或重写 `v1.1`，由新的 `v1.2` 一致性链替代。
- `quality.yml` 只运行确定性检查，不访问匿名公共 Bilibili 网络，也不依赖系统浏览器启动结果。
- `public-smoke.yml` 仅定时或手动运行；HTTP 412 / `environment_blocked_412` 是失败，但该工作流不是 PR 必需检查。
- `release.yml` 只由精确 `v1.2` 标签触发，并在发布前后完成 tag/source/commit/PE/asset/digest/attestation 校验。
- 构建不得复用 `.venv`；`build.ps1` 每次重建 `build\.venv`，按 Python 3.13/Windows x64 哈希锁只安装 wheel。
- 不设置 EXE 体积阈值。只记录实际大小并审计 Chromium、FFmpeg、凭据、日志、session/profile 等不应捆绑内容。

## v1.1 历史更新范围

- 新增本地环境诊断、脱敏报告和仅手动触发的 GitHub 更新检查。
- 新增逐分 P 下载结果窗口、文件操作和失败项重试。
- 保持现有下载格式、登录态、隐私与合规边界不变。
- 发布包不内置 Chromium，扫码登录优先使用系统 Edge，其次使用 Chrome。
- 运行完整自动化测试以及 onedir/onefile 构建和 smoke 验证。

## 只读审计发现

- 当前目录已初始化为 Git 仓库，并使用版本化发布流程。
- `app/` 是当前源码目录。
- 顶层存在 `.venv/`，不应提交。
- 顶层存在 `build/` 和 `dist/`，属于 PyInstaller 构建产物，不应提交。
- `dist/` 中的 `BiliDownloader.v1.1.exe` 以及第三方运行时文件属于历史本地构建产物，不应直接提交或复用于 v1.2。
- `app/__pycache__/` 中存在 Python 字节码缓存，不应提交。
- `tools/` 当前仅发现 `.gitkeep`，未发现 `ffmpeg.exe`。
- `BiliDownloader.spec` 使用 `Path(SPECPATH)`，审计时未发现硬编码本机绝对路径或个人用户名路径。

## 不应提交的内容

- 登录态、Cookie、`storage_state.json`、`cookies.txt`
- `session/`、`sessions/`、浏览器 profile、Playwright 用户数据目录
- `logs/`、`crash.log`、`app.log`
- 真实下载记录、测试视频、下载输出目录
- `.venv/`、`build/`、`dist/`、`__pycache__/`
- Playwright 下载的 Chromium 缓存
- `ffmpeg.exe`、`ffprobe.exe`，除非维护者明确决定按许可证要求分发
- 本机绝对路径、个人用户名路径、账号信息、token
- 用户配置文件，例如本地 `config.json` 或 `settings.json`

## 发布前人工确认

- 确认 `LICENSE` 中的版权主体和年份是否需要补全。
- 确认 MIT License 是否适用于本项目全部原创代码。
- 确认第三方依赖许可证和 notice，尤其是 PySide6 / Qt、yt-dlp、Playwright、PyInstaller、FFmpeg。
- 如果发布包包含 FFmpeg，确认 FFmpeg 构建来源、许可证组合、源码提供义务和 notice 要求。
- 补充安全问题私密联系方式，或启用 GitHub Security Advisories。
- 补充真实截图，避免截图包含账号信息、Cookie、私密视频链接或本机路径。
- 在干净环境重新构建 Release 包，不要复用当前 `dist/`。
- 为 Release 附件生成 SHA256 校验值。
- 检查杀毒误报说明和用户下载来源说明。

## 以后可考虑的改进

以下只是维护建议，本次未改源码：

- 为关键解析、下载、登录态处理路径补充最小测试。
- 增加发布脚本的许可证/notice 收集步骤。
- 增加预发布 secret scan 和大文件检查。
- 后续版本继续保持单一源码版本来源以及 tag/PE/commit/Release 的强绑定。
- 在 UI 中持续保持合规提示，避免误导用户理解工具能力边界。
