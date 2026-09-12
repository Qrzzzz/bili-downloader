# Maintainer Notes

## 当前 CI 与验收策略

- 当前检查范围以 [发布检查清单](./RELEASE_CHECKLIST.md) 为准；下面的版本小节记录已发布版本的行为边界。
- PR/main 执行 Python 回归、真实 WinUI 编译和 C# 管道测试；最终目录/ZIP 组包、package smoke 和归档审计集中在 Release。
- 真实扫码、下载、界面截图和许可证复核按相关改动触发；公网探测仅手动运行。无关变更不要求重复人工验收。

## v3.1 下载偏好

- 从 main `861170c`（v3.0）开发，分支 `codex/v3.1-download-preferences`。用户已授权按 3.0 的流程清理临时产物到回收站、推送、PR、CI、合并、注释标签及 Release 核验。
- schema 1 兼容新增自动记忆、模式、画质高度；前端自动应用不写回，指定画质不可用时提示选择，MP3 保留画质，已入队任务维持原规格。
- 应用 3.1 / 程序集 3.1.0.0 / IPC v2 同步，第三方依赖锁不变。验证与人工边界见 [3.1 验证记录](./docs/validation/v3.1.md)。

## v3.0 发布边界

- 基于 main `f022ea8`，实现提交 `0f5c911`；实现独立持久任务、受控并行、批量添加及原生任务页。2026-09-12 用户授权将中间产物移入回收站并推送发布，按 PR、CI、保留实现作者的合并、注释标签及 Release 资产核验顺序执行。
- 当前协议为 [IPC v2](./docs/architecture/ipc-v2.md)。旧输入修订不再控制持久任务；后续历史小节描述对应旧版本，不覆盖 3.0。
- 真实公网媒体下载与夹具/原生状态验证分开记录，详见 [3.0 验证记录](./docs/validation/v3.0.md)。
- 本机两项公网媒体尝试因 TLS 证书校验错误失败；保留该未通过结果。公网 smoke 按当前清单为手动诊断，不作为合并或发布门槛；不能将 CI 或组包通过替代真实媒体、手机扫码及辅助技术人工验收。

## v2.12 发布边界

- 从 main `bbd842c` 开发分享文本提取，分支 `codex/v2.12-share-text`；用户已授权清理临时产物并推送发布，按 PR、CI、合并、注释标签及 Release 资产核验顺序执行。源码、程序集、界面与 Release 工作流目标同步为 2.12 / v2.12。
- `normalize_video_input` 在服务入口提取完整 URL token，再调用原有官方 HTTPS 校验及 canonicalization；不从被拒绝 URL 中回退提取 BV/av，也不为文本扫描发送请求。只有纯输入继续接受 BV/av 号。
- Markdown 中重复 URL 按去除追踪参数、保留分 P 后的地址去重；多个不同地址明确拒绝，不增加批量视频下载或短链联网去重。
- 下载页保持原有解析、取消、输入修订与旧结果失效流程；输入框支持多行分享文本。回归与外部验证边界见 [2.12 验证记录](./docs/validation/v2.12.md)。

## v2.11 发布边界

- 从 main `b4b5ccb` 修复 #28、#29；源码、程序集、窗口及 Release 目标同步为 2.11 / v2.11。用户已授权推送并发布，按 PR、CI、合并、注释标签和 Release 资产核验顺序执行；本地证据见 [2.11 验证记录](./docs/validation/v2.11.md)。
- 线程成功构造后才占用 active；ACK 先于启动，启动异常与 worker 退出共用终态收尾。请求接受前的错误与接受后的 operation.failed 分开，避免重复 response 和未启动线程的 join。
- 已在合并/转换中延迟的取消只豁免当前文件最终验证的取消检查；元数据、规格、完整解码、超时及子进程回收仍完整执行。取消状态保留，下一分 P 不启动；普通验证和已有文件复用继续可取消。
- 原生回归用隔离后端故障注入验证 ApplicationSession.Busy 恢复；结果 UI 使用显式占位文件，真实媒体证据来自 FFmpeg 生成及完整解码回归，不能混称为真实 Bilibili 下载。

## v2.10 发布边界

- 从最新 main `4d9d174` 修复 #24、#25、#26；本地验证见 [2.10 验证记录](./docs/validation/v2.10.md)。用户已授权推送并发布；执行 PR、CI、合并、注释标签及 Release 资产核验。
- 已有文件复用及新文件成功终态均要求元数据规格匹配并通过完整解码，MP3 音频流为 192 kbps。媒体检查可取消、有限时，原有不匹配文件保留。
- 输出目标选择、选项准备及空间检查位于逐项异常边界内；保留此前成功结果。磁盘空间按顺序下载的当前新项目估算，复用项目不计入。

## v2.9 发布边界

- 从 `85caeec` 检查仍开放的 #16、#18、#19，三个根因均仍存在；本轮只修复这些问题并同步 2.9 版本；用户已授权按 PR、CI、合并、标签、Release 顺序发布。
- 后端所有解析/下载/重试均显式传递预期 generation；`None` 表示接受时没有 canonical 凭据，不能静默采用新账号。直接调用的兼容默认值 `GenerationPolicy.UNBOUND` 不用于后端任务。
- lease 的 generation 核对与导出共用存储事务；获得 lease 后可使用该稳定副本完成批次，释放后照常清理。跨进程锁不覆盖网络请求，匿名模式不读存储。
- 扫码成功需保留 `LoginStatus` 及 generation，并在终态前复核当前存储；验证期间凭据被清除/损坏/替换时丢弃迟到结果。账号 ViewModel 收到 none/invalid 或代次改变时撤销旧解析和重试。
- 本地确定性回归及 WinUI 验收见 [2.9 验证记录](./docs/validation/v2.9.md)，不等同于真实手机扫码或公开 Release。

## v2.8 发布边界

- 精确基线为已发布 v2.7 的 `e9986d5cf82ff82d6a92691201a6f8991cebb3c1`，开发分支为 `codex/v2.8`；仅处理开始时为 OPEN 的 #14、#15、#17。源码、程序集、窗口和发行约束同步为 2.8 / v2.8。
- #14 的输出身份为 `video-mp4-<height>p`、`video-mp4-best` 或 `audio-mp3-192k`，置于清理、限长后的分 P/标题/来源 id 文件名中。相同规格沿同一路径断点续传或重试；完整已有文件只在受控的相邻 FFprobe（失败时回退已选绝对 FFmpeg）验证容器、音视频轨和严格高度后复用。不匹配文件不覆盖、不删除，改用带序号的新名称。
- #15 只在 `BiliBili` 真实原始提取结果中，将具备 quality/height/duration 且没有 codec/ext 字段的窄 legacy durl 形状标记为合流。普通 codec 未知格式不因此通过；显式 `none`、纯视频、纯音频、DASH 配对和严格高度仍按各自契约分类。MP4/MP3 预检与大小估算共用该分类。
- #17 在 sequence 状态、UI Event 和 operation 移除之前完整校验 completed/failed/cancelled 的 method/result/error 结构。任何协议异常由连接失败路径完成全部 pending/accepted Task；输入管道与并发写入同时关闭时复用已经记录的传输根因并显式完成当前调用。正常终态仍只完成一次，迟到/重复事件继续忽略，sequence 在正常终态清理。
- [发布说明](./docs/releases/v2.8.md) 与 [验证记录](./docs/validation/v2.8.md) 必须区分真实锁定库/真实管道、合成媒体、外部下载和人工待验。打包脚本未改；本地 dirty 包不能称为正式资产。
- #16 凭据代次、#18 扫码终态、#19 验证期间清除逻辑留给后续版本；配置 schema 1、IPC 1、DPAPI、Cookie/NAV 与 FFmpeg 选择边界不变。

## v2.7 发布边界

- 基线 `ef57a570a236b02f070d94f94adb989e86883ed5`，仅处理 #12 与 #13。源码、程序集、窗口版本和发行约束为 2.7 / v2.7；#14—#19 不在本轮范围。
- NAV 在 PreparedRequest 发送前及响应处理时限制精确官方 HTTPS 端点；所有 3xx 均拒绝，不跟随官方或外域跳转。CookieJar 保留域、路径、Secure、有效期和同名不同作用域 Cookie；主机 Cookie 采用严格策略。失败保留旧 canonical session，schema 和 DPAPI 原子提交协议不变。
- 三处生产 yt-dlp 调用统一经过 `_youtube_dl`。除显式 `ffmpeg_location` 外，还用锁定版库的 `FFmpegPostProcessor._ffmpeg_location` ContextVar 约束不带 downloader 的内部探测；退出（含异常）必须 reset，不能用进程全局 monkeypatch 或改 PATH 实现。
- FFmpeg 仍由现有 `utils.probe_ffmpeg` 从相邻 tools、资源 tools、绝对 PATH 选择。未找到可用程序时显式使用空 location；在锁定的 yt-dlp 2026.8.19 中它关闭 ffmpeg/ffprobe，不得改回 None 或某个可被创建的“占位文件”路径。FFprobe 只沿已选 FFmpeg 所在目录解析，缺失/不可用时的音频探测回退仍用已选绝对 FFmpeg 路径。
- 升级 yt-dlp 前必须重跑 `test_ffmpeg_boundary.py`，验证默认格式处理、无 downloader 回退、FFmpegFD、合并、转码和 ffprobe 的真实执行边界。下载前通过的路径直接传入预检和逐 P 下载；不自动安装、捆绑 FFmpeg。
- [发布说明](./docs/releases/v2.7.md) 与 [验收记录](./docs/validation/v2.7.md) 区分确定性回归、真实服务器结果和待人工项。合成产物、首次等待扫码状态及本地 dirty 包均不是正式发布或真实下载验收证据。

## v2.6 发布边界

- 从 v2.5 的 `c16dac1685e1127ebff230b29ba35deb2388575b` 开始，先审查后打磨 WinUI 3；[审查记录](./docs/architecture/v2.6-winui-review.md) 与 [验收记录](./docs/validation/v2.6.md) 分别记录发现和证据。
- 2026-09-04 用户明确授权进入 release 流程。应用版本 2.6、标签 v2.6、包名 `BiliDownloader.v2.6.win-x64.zip`、Release 标题 `Bili Downloader Lite v2.6` 保持一致；正式资产由精确标签的干净提交构建，本地 `dirty=true` 候选不可作为发行资产。
- 界面使用默认 WinUI 控件与主题资源；没有新增运行时依赖。协议、配置 schema、DPAPI 和平台访问边界保持兼容。
- 原生 `--self-test` 只写入指定的隔离输出目录；`--ui-regression` 额外要求源码中的 IPC 测试夹具，测试不访问 Bilibili。结果样例与占位文件不代表真实下载完成。

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

# 3.0 Codex 提交归属要求

3.0 的 Codex 实现提交应使用可归属至 `chatgpt-codex-connector[bot]` 的 author：`Codex <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>`；committer 保留实际提交者。使用真实的非空实现提交，不创建空提交凑贡献，不改写历史版本或全局 Git 身份。

合并时保留该作者身份；如果使用 squash，应核对最终默认分支提交的 author。README 署名或 Co-authored-by 不能代替本要求。发布阶段必须验证 GitHub commit API 的 `author.login`，并检查原生 Contributors/API 是否包含该账号。分支上的本地署名不算完成，GitHub 统计缓存未刷新时据实记为待验证。

规则来源：[GitHub 原生 Contributors](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors)。本次仅核对了该账号的公开 ID 与当前仓库贡献者列表，未声称已经归属。
