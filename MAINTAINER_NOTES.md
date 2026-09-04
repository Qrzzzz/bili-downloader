# Maintainer Notes

## 当前 CI 与验收策略

- 当前检查范围以 [发布检查清单](./RELEASE_CHECKLIST.md) 为准；下面的版本小节记录已发布版本的行为边界。
- PR/main 执行 Python 回归、真实 WinUI 编译和 C# 管道测试；最终目录/ZIP 组包、package smoke 和归档审计集中在 Release。
- 真实扫码、下载、界面截图和许可证复核按相关改动触发；公网探测仅手动运行。无关变更不要求重复人工验收。

## v2.5 发布边界

- 应用版本 2.5，标签 v2.5，包名 `BiliDownloader.v2.5.win-x64.zip`。正式资产由 Release 工作流从精确标签的干净提交构建；带 dirty 元数据的本地候选包不可作为发行资产。
- [架构说明](./docs/architecture/v2.5-winui.md) 和 [IPC v1](./docs/architecture/ipc-v1.md) 是前后端边界。UI 必须调用真实 WinUI API，不接受 Qt 或 WebView 替代。
- `MainWindow` 只持有 shell/lifecycle；应用级 ViewModel 保留跨页状态；Python backend 持有任务快照、凭据代次和取消控制器。
- 配置及 DPAPI 存储兼容；下载/登录协议不随 UI 重写。后端 stdout 只发送受限 JSONL，日志走脱敏回调/文件/stderr。
- 原生窗口、手机 QR、受平台允许的实际下载和辅助技术验收见 [本地验收记录](./docs/validation/v2.5.md)，与模拟或静态证据分开。
- 发行包必须完整解压。前后端 EXE 相邻，Python 库位于 backend-runtime，FFmpeg 搜索规则沿用现有 utils；无需用户另装 .NET、Windows App SDK 或 Python。

## v2.4 前序候选记录（未发布）

- 从已发布 v2.3 的提交 `511773e64ab8b3b190afb0d839db09665773f518` 开发；当前为本地候选，尚未提交、打标签或发布。
- 版本严格为源码 `2.4`、未来标签 `v2.4`、成品 `BiliDownloader.v2.4.exe`；本地未提交构建必须保留 dirty 标记，不能当作正式 Release 证据。
- `app/ui/` 负责窗口、页面与配色，`MainWindow` 继续拥有解析、下载、登录和关闭生命周期；页面切换不能新建或销毁任务控制器。
- 下载、账号和设置页常驻同一个窗口；状态提示跨页保留，结果仍是主窗口子控件，关闭等待期仍以 `closing` 为最高优先级。
- `AppConfig.theme` 是可选字段，配置 schema 仍为 1；旧目录可读，未知主题回退为 system，修改目录不得重置主题。
- Windows 窗口使用 Qt 扩展客户区并保留系统窗口控件；背景采用稳定实色回退，不宣称实现 Mica 或迁移到 WinUI 3。
- 自动回归覆盖导航、主题、提示、结果和生命周期；真实手机扫码及原生标题栏交互由本轮 UI 人工验收覆盖。沿用上方精简后的 CI 策略，不恢复已移除的重复 Gate。

## v2.3 已发布记录

- 版本严格为源码 `2.3`、标签 `v2.3`、成品 `BiliDownloader.v2.3.exe`、Release 标题 `Bili Downloader Lite v2.3`；不允许多一级版本。
- v2.3 必须从已发布的 v2.2 提交 `b2d9d478acc90d63a64823cc6be47045940469ab` 发展；已公开标签和 Release 不得移动、覆盖或删除。
- 仅将下载结果从独立弹窗嵌入主窗口任务状态下方；单 P 紧凑结果、多 P/失败明细、打开文件、失败重试和链接变化后的重试失效契约保持不变。
- 新下载开始时收起旧结果，重试结果在同一内嵌区域合并；关闭等待期继续以 `closing` 为最高优先级终态。
- `quality.yml` 不访问 Bilibili 实时网络；`release.yml` 只由精确 `v2.3` 标签触发，并绑定 tag/source/commit/PE/asset/digest/attestation。

## v2.2 已发布记录

- 版本严格为源码 `2.2`、标签 `v2.2`、成品 `BiliDownloader.v2.2.exe`、Release 标题 `Bili Downloader Lite v2.2`；不允许多一级版本。
- v2.2 必须从已发布的 v2.1 提交 `4c582910eb297c25c66dc1d24cec012078f15c45` 发展；已公开标签和 Release 不得移动、覆盖或删除。
- 仅增加“仅音频（MP3）”模式；默认的音视频 MP4 模式、严格清晰度选择、分 P、登录、重试和取消契约保持不变。
- 音频模式使用 `bestaudio/best` 并通过 yt-dlp `FFmpegExtractAudio` 转换为 192 kbps MP3；必须在全批次下载前逐分 P 预检音轨。
- MP3 转换继续复用已验证的外置 FFmpeg 路径和协作式取消；转换开始后不强制终止 FFmpeg，应等待当前文件处理安全结束。
- `quality.yml` 不访问 Bilibili 实时网络；`release.yml` 只由精确 `v2.2` 标签触发，并绑定 tag/source/commit/PE/asset/digest/attestation。

## v2.1 已发布记录

- 版本严格为源码 `2.1`、标签 `v2.1`、成品 `BiliDownloader.v2.1.exe`、Release 标题 `Bili Downloader Lite v2.1`；不允许多一级版本。
- 已公开 `v1.1`、`v1.2`、`v1.3`、`v1.4` 与 `v2.0` 是不可改写历史，不得移动、删除或重置标签。v2.1 必须从已发布的 v2.0 提交 `ffb1cfd6e40c067ab84b6e385f307b7e217e9841` 发展。
- `quality.yml` 只运行确定性检查，不访问 Bilibili 实时网络。`public-smoke.yml` 现仅手动运行匿名解析和二维码首次等待态检查；HTTP 412 必须失败留证。
- `release.yml` 只由精确 `v2.1` 触发，并绑定 tag/source/commit/PE/asset/digest/attestation。不得从分支 push 触发发布。
- 构建固定 Windows x64 + Python 3.13；`build.ps1` 每次重建 `build\.venv`，只从哈希锁安装 wheel。
- 不设 EXE 体积阈值。删除浏览器自动化依赖后的自然缩小是预期结果，禁止填充无用内容。

## 原生二维码登录契约

- 网络协议只在 `app/auth_qr.py` 中适配：有限超时、明确 User-Agent、严格 schema/状态枚举、HTTPS 官方主机与成功回调来源校验。未知码、缺字段、类型错误或非允许来源一律 fail closed；不消费的回调查询参数不属于凭据或提交输入。
- 状态 `86101` 为等待扫码，`86090` 为已扫码待确认，`86038` 为过期，`0` 为成功。刷新必须关闭旧 `requests.Session`、废弃旧 key，并丢弃已在途中的旧轮询结果。
- Segno 只用于本地 PNG 生成，quiet zone 不小于 4 modules。不得引入 Pillow、WebView 或任何浏览器 fallback。
- `qrcode_key`、完整轮询/成功回调 URL、`refresh_token`、Cookie 和响应原文均不得记录、展示或持久化。成功回调 URL 只验证为允许的官方 HTTPS 来源，查询部分作为不透明敏感值立即丢弃；不导航、不解析，也不用于补取 Cookie。
- HTTP 412 如实归类为外部平台限制，不实现风控、会员、地区、付费或 DRM 绕过。

## 登录态事务与兼容

- 候选 Cookie 只保留 Bilibili 域、允许 Cookie 名称和 canonical 字段，并必须包含未过期 `SESSDATA` + `DedeUserID`。
- 先在锁外请求 NAV API 验证候选凭据；只有服务端确认有效且未被取消/刷新，才在跨线程/跨进程锁内用 DPAPI 原子替换 `session.dat`。
- 任何失败都不得删除或覆盖旧 canonical session。v1.2 schema 必须保持可读；旧 `storage_state.json` / `cookies.txt` 只在 canonical 提交并回读成功后删除。
- `playwright-profile` 和 `login-cache` 只是历史残留清理名称，不是运行时依赖。临时 Netscape lease 只在全局 session 锁内创建和销毁，使用期由各自 owner marker 的跨进程活动锁保护；不得重新让全局锁覆盖网络解析或下载。匿名模式不得读取凭据。
- canonical 已存在时也必须先解密并验证结构，再清理精确的旧明文目标；canonical 损坏时不得清理旧明文。过期 lease、原子临时文件和历史隔离目录必须同时满足应用目录、精确命名/owner marker 与保守年龄条件。

## v2.0 界面与生命周期边界（v2.5 继续保留）

- 主窗口按任务阶段渐进披露：初始链接入口、解析后视频与选项、下载进度与取消、最终结果；日志、诊断和隐私说明保留但默认不抢占主流程。
- 单 P 隐藏选集，多 P 保留选择摘要与列表。单 P 且只有一个成功输出时可使用紧凑结果区；多输出、多 P、失败、取消与重试继续使用完整明细表。v2.3 起结果区内嵌在主窗口，不再弹出独立结果窗口。
- `closing` 是最高优先级终态；下载期间不得启动新解析或让迟到解析回调隐藏取消入口。修改 URL 必须立即撤销旧下载目标，但不得中断已经开始的任务。

## v1.4 稳定性边界（v2.5 继续保留）

- FFmpeg 查找顺序固定为 EXE 相邻 `tools\ffmpeg.exe`、PyInstaller 资源目录同布局、绝对 PATH；不搜索当前工作目录，不下载、不捆绑、不安装，也不修改系统 PATH。
- b23 只手工处理 301/302/303/307/308；每跳请求前验证官方 HTTPS 主机、无 userinfo、无异常端口，限制跳数和循环。最终必须是受支持的 bilibili.com BV/av 视频 URL。
- 封面只从 Bilibili/官方 CDN 通过 HTTPS 下载。Bilibili API 返回的 HTTP 封面只有在无 userinfo、无自定义端口且主机位于既有允许列表时，才在请求前升级为 HTTPS；外域、IP 和含糊地址继续拒绝。连接/读取超时、streaming、Content-Length 预检和实际 5 MiB 硬上限不变；封面异常不能覆盖成功解析结果。
- 每实例运行标记包含 schema、PID、Windows 进程创建时间和随机 instance id，原子写入。多实例保持允许；正常退出只删除本实例标记。

## 归档与仓库禁入项

提交内容不包含以下本机数据或产物；最终目录/ZIP、Python 后端 CArchive 和内嵌 PYZ 由归档审计共同检查：

- Playwright Python 包、driver/Node、`ms-playwright`、Chromium/Chrome/Edge runtime、Electron executable/runtime。
- PySide6、Shiboken、PyQt 和 Qt Widgets。Windows App SDK 自带的互操作库按实际依赖与许可证记录，不误报为浏览器运行时。
- 浏览器 profile、session/凭据、`storage_state.json`、`cookies.txt`、`session.dat`、日志。
- `ffmpeg.exe`、`ffprobe.exe`、下载文件、测试视频、`.venv/`、`build/`、`dist/`、`__pycache__/`。
- 本机绝对路径、用户名、账号信息、token 和用户配置。

历史 CHANGELOG 事实、迁移测试和旧残留清理字符串不应被文本搜索误判为运行时打包内容。

## 按改动触发的人工验收

- 登录协议、界面或凭据相关变更才需要真实手机扫码，验证受影响的确认、过期/刷新、取消/关闭和重开流程；涉及迁移时检查旧凭据可读。自动测试不得伪造人工通过。
- 下载及外置 FFmpeg 相关变更才需要真实解析/下载验证；界面变更才需要相应截图与交互验收。
- 首次分发、依赖或分发方式变化时复核对应许可证/notice；依赖变化或出现新安全公告时执行漏洞审计，不要求每个无关补丁重复复核全部依赖。
- EXE 大小、SHA-256 已由产物审计自动记录；无签名证书和体积变化不构成额外门槛，不宣称未完成的 Authenticode 签名。
