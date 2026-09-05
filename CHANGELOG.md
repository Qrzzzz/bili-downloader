# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [2.9] - 2026-09-05

- #16：解析、下载及重试携带原凭据代次，在创建 Cookie lease 的同一存储事务内核对；清除或换代后明确拒绝，匿名模式继续不读取本地凭据。
- #18：扫码终态保留真实验证结果与保存代次，复核当前凭据后立即显示已登录；取消、刷新、清除、换代及损坏不能显示成功。
- #19：在途账号验证完成后复核当前存储，返回准确的 none / invalid / local_pending；前端撤销旧解析、重试及已登录状态。

## [2.8] - 2026-09-05

- #14：为输出名加入模式/严格画质身份，并在复用前通过受控 FFprobe/FFmpeg 验证媒体；不匹配的同名文件保留并分配新名称，断点与失败重试沿用同一规格路径。
- #15：仅标记真实 `BilibiliBaseIE` legacy durl 原始格式为 codec 未知的合流来源；同步修正 MP4/MP3 预检与大小估算，显式无音轨和严格高度缺失继续失败关闭。
- #17：在发布终态事件和移除 operation 前校验完整结构；畸形 completed/failed/cancelled、非法序号、断连及写入关闭竞态会让全部调用明确收尾，并扩充真实 Python 管道和 WinUI Busy/关闭回归。
- 同步源码、程序集、manifest、窗口、测试和 Release 约束为 2.8 / v2.8；未升级依赖，未改动 #16、#18、#19。

## [2.7] - 2026-09-05

- #12：NAV 验证保留 Cookie 作用域，在请求发送前及响应处理时限制官方 HTTPS 端点，拒绝重定向和异常来源；保留登录态验证、失败分类与 DPAPI 原子提交。
- #13：统一所有生产 yt-dlp 实例的 FFmpeg 位置及上下文，覆盖解析、预检、MP4/MP3、ffprobe 和库内部无下载器参数的探测回退；没有可用 FFmpeg 时继续支持解析。
- 增加真实 Requests 内存传输和锁定版 yt-dlp 子进程边界回归；同步 2.7 版本与发布说明。实际验收进度见 `docs/validation/v2.7.md`，不包含 #14—#19。

## [2.6] - 2026-09-04

### WinUI 3 整体打磨

- 统一下载、账号、设置和内嵌结果的间距与层次；规格宽屏双列、窄屏单列，修复滚动容器限宽时的横向偏移与裁切。
- 下载期间锁定链接输入；区分下载任务与账号/诊断任务，合并转码显示准确阶段，取消请求仅提交一次。
- 按真实登录状态显示账号操作；设置草稿跨页保留，外观即时预览，保存失败恢复已保存外观并保留草稿。
- 单文件结果直接显示文件名，多文件按文件名选择；保留完整路径、逐 P 详情与失败重试，无文件时隐藏空选择器。
- 更新前后端、程序集和发行工作流版本至 2.6；补充前端状态回归、原生窗口截图与最小尺寸验证。详见 `docs/validation/v2.6.md`。

## [2.5] - 2026-09-04

### Windows App SDK / WinUI 3 架构迁移

- 新增 C#/.NET 10 WinUI solution；官方 Window、MicaBackdrop、TitleBar、NavigationView、Frame、Page 与 InfoBar 承担窗口和页面结构。
- Python 下载、登录与诊断逻辑通过无 Qt 的 services/backend 提供 IPC v1；移除 PySide6 UI、QThread/Signal glue 与 Qt 依赖。
- 任务快照、失败重试、扫码刷新代次、取消及关闭等待由后端统一管理，前端采用应用级状态与独立 ViewModel。
- 分发改为自包含 Windows x64 目录 ZIP，联合审计 WinUI 与 Python 文件，SBOM 包含 NuGet、Python、.NET 运行时和实际文件摘要。
- 原生运行、协议回归、依赖审计与外部验收范围记录在 `docs/validation/v2.5.md`，发行资产从干净的标签提交构建。

## 2.4 — Windows 风格界面重写（未发布的前序候选）

- 以标题栏、侧边导航和内容区重组主窗口，分为下载、账号与设置三页；切换页面保留任务、进度和内嵌结果。
- 新增跟随系统、浅色、深色主题及持久化偏好，高对比度模式优先采用系统配色；旧配置与保存目录保持兼容。
- 解析错误、输入校验和任务结果使用内嵌提示；非下载页可通过提示返回任务，窄窗口自动收起导航文字。
- 保留分 P、MP4 / MP3、失败重试、任务详情与安全关闭等待；解析或下载期间禁用账号状态变更。
- 继续使用 Python / PySide6，保留原生系统窗口控件和实色背景，不新增运行时依赖。
- 简化 CI 与验收：PR/main 运行确定性回归，正式成品检查集中在 Release，公网探测仅手动运行，人工验收按相关变更触发。

## [2.3] - 2026-09-04

### 主窗口内嵌下载结果

- 下载完成后不再打开独立结果弹窗，结果内容直接显示在主窗口“任务状态”下方的留白区域。
- 单 P 单输出成功继续使用紧凑结果视图；多输出、多 P、失败、取消和失败项重试继续保留完整明细表。
- 保留打开文件、打开所在目录、重试失败项和结果收起能力，不改变下载、登录、FFmpeg 或安全边界。

## [2.2] - 2026-09-03

### 仅音频 MP3 下载

- 下载选项新增“仅音频（MP3）”模式：对每个选中的分 P 预检最佳可用音轨，下载后通过现有外置 FFmpeg 转换为 192 kbps MP3。
- 音频模式不再展示无意义的视频清晰度，并在进度、安全取消、结果与失败项重试中保留音频下载语义。
- 继续使用现有 FFmpeg 发现与验证边界；不下载、捆绑或安装 FFmpeg，也不修改系统 `PATH`。

## [2.1] - 2026-09-03

### 封面兼容性修复

- 修复 Bilibili 视频信息 API 返回官方 CDN `http://` 封面地址时，安全校验直接拒绝该地址、导致视频解析成功但封面保持空白的问题。
- 对无 userinfo、无自定义端口且主机位于既有 Bilibili/官方 CDN 允许列表的 HTTP 封面，在任何网络请求前升级为 HTTPS；外域、IP、含凭据或自定义端口地址仍保持拒绝。
- 保留封面下载的手工重定向、连接/读取超时、Content-Type、声明长度、streaming 实际 5 MiB 上限，以及“封面失败不覆盖视频解析成功”的边界。
- 没有新增运行时依赖，也不改变下载、扫码登录、权限、FFmpeg 或主界面任务流程。

## [2.0] - 2026-09-02

### 主流程与信息层级

- 主窗口从多面板常驻布局改为任务阶段驱动的单列流程：初始仅保留链接、解析与精简账号入口，解析后再显示视频、画质、目录和下载操作，任务期间只突出进度与取消。
- 单 P 自动隐藏选集；多 P 显示已选数量和限高列表。画质名称去除重复技术后缀，严格匹配规则集中说明。
- 登录状态压缩为稳定状态码对应的简短文案；扫码、退出与清除操作互斥，环境诊断、错误日志、任务详情和登录隐私说明收纳到“更多”。
- 日志默认折叠但持续记录，进度区只显示文件名，完整路径保留在 tooltip，兼顾窄窗口和故障排查。

### 结果与诊断

- 单 P、单输出且成功时使用紧凑结果页；多输出、多 P、失败、取消和重试仍保留完整结果表、文件存在性检查与失败项重试。
- 环境诊断将本地检测与手动 GitHub 更新检查分层，继续保证打开诊断窗口本身不联网。

### 生命周期与兼容

- 关闭等待期设为不可被回调撤销的终态，隐藏无关选项并禁用交互，继续协作等待解析、登录、诊断、下载和 FFmpeg 处理安全退出。
- 下载中修改 URL 不会隐藏进度或取消入口，也不会重复刷日志；下载结束但线程引用尚未清理时不会错误恢复下载态。
- 下载期间阻止自动或手动并发解析，并忽略迟到的解析成功、失败与取消回调，避免任务状态被覆盖。
- 保持 Python 3.13、PySide6、yt-dlp、PyInstaller、原生二维码登录、外置 FFmpeg 与既有隐私/权限边界；没有新增运行时依赖。

## [1.4] - 2026-08-30

### 隐私与凭据生命周期

- canonical DPAPI session 已存在时，先验证可解密和结构完整，再精确清理本程序拥有的旧 `storage_state.json` / `cookies.txt` 与历史 profile/cache；损坏 canonical 不触发旧明文删除。
- 临时 Cookie lease 改为带 owner marker 的独立目录；过期 lease、失败原子临时文件和历史隔离目录只有在应用目录、精确命名/所有权与保守年龄条件同时满足时才会删除。匿名模式继续不读取凭据。
- 全局 session 锁不再覆盖整个解析或下载周期；每个临时 Cookie lease 使用独立跨进程活动锁，允许多实例并发，同时让残留清理和跨实例退出登录继续保守避开正在使用的明文文件。
- 清理失败仅报告脱敏的应用相对路径和错误类型，部分删除失败不影响 canonical session。

### URL、封面与外置工具边界

- onefile 明确区分 `_MEIPASS` 资源根与 EXE 目录，按 EXE 相邻 `tools\ffmpeg.exe`、资源目录同布局、绝对 PATH 的顺序探测；不从当前工作目录执行，也不下载、捆绑或安装 FFmpeg。
- b23 禁用自动重定向，逐跳验证官方 HTTPS 主机、userinfo、端口、循环和跳数，最终只接受受支持的 bilibili.com BV/av 视频 URL；错误不回显敏感查询参数。
- 输入、短链目标与内部 canonical 视频 URL 共用独立视频边界；登录回调仍保持自己的严格协议验证器。
- 封面下载增加连接/读取超时、手工重定向、Content-Type/Content-Length 预检、streaming 和 5 MiB 实际字节上限。封面失败只影响展示，不覆盖成功解析。

### 崩溃恢复与多实例

- 用版本化、原子写入的每实例 marker 替代单个“存在即崩溃”文件，并以 Windows PID + 进程创建时间识别活跃实例、PID 重用、异常退出和系统重启残留。
- 保持允许多开；正常退出只删除本实例拥有的 marker。旧 v1.3 marker 会安全识别和迁移。

### 依赖与范围

- 没有新增运行时依赖；继续保持 Python + PySide6 + yt-dlp + PyInstaller 的 Windows x64 单窗口形态。
- 不包含下载内核重构、批量队列、历史数据库、自动更新、内置 FFmpeg、浏览器 Cookie 导入、后台服务或遥测。

## [1.3] - 2026-08-30

### 扫码登录

- 将扫码登录改为应用内原生流程：通过独立 `requests.Session` 调用 Bilibili 官方网页扫码接口，使用 Segno 在本地生成带 quiet zone 的 QR PNG。
- 为等待扫码、已扫码待确认、过期/刷新、成功、取消、超时、网络失败、HTTP 412 和协议异常增加明确状态与 fail-closed 契约。
- 刷新二维码时废弃旧 key/会话并丢弃在途旧轮询结果；关闭、连续取消/刷新、成功后立即重开和应用退出保持协作式线程收敛。
- `qrcode_key`、完整轮询/成功回调 URL、`refresh_token`、Cookie 和响应原文纳入日志脱敏边界。
- 成功回调 URL 只校验为允许的 Bilibili 官方 HTTPS 来源，查询部分作为不透明敏感值立即丢弃；平台扩展参数不再误伤登录，Cookie 仍只取自受控 Session 并通过 NAV 事务验证。

### 登录态事务与兼容

- 将浏览器上下文绑定保存入口替换为通用候选 Cookie API：先限定域/名称/字段并通过 NAV API 服务端验证，然后在跨线程/跨进程锁内用 DPAPI 原子替换 canonical session。
- 取消、超时、412、离线、协议异常、验证失败或保存失败均保留原有凭据。
- 保持 v1.2 DPAPI schema 可读，并继续安全迁移旧 `storage_state.json` / `cookies.txt`、清理旧 profile/cache 残留。临时 Netscape lease 与匿名模式契约不变。

### 依赖、诊断与发布链

- 从运行时、完整哈希锁、PyInstaller spec、package smoke 和发布包中移除 Playwright 及其 driver/Node/专用传递依赖，不保留浏览器 fallback。
- 环境诊断改为不联网的本地二维码组件/登录态说明；update checker 严格只接受可选 `v` / `V` 加两级数字版本。
- 定时/手动公共网络 smoke 增加匿名二维码生成与首次等待态证据，不进入 PR/main 确定性门禁。
- 归档审计扩展到内嵌 PYZ，拒绝 Playwright、driver/Node、Chromium、Electron、profile、FFmpeg、凭据和日志；继续只测量体积而不设阈值。

## [1.2] - 2026-08-30

### 修复

- 以新的 `v1.2` 源码、标签和二进制一致性链替代 v1.1 的错配状态；已公开的 `v1.1` 标签保持原位，不移动、不删除、不重写。
- 匿名公共网络 smoke 遇到 HTTP 412 / `environment_blocked_412` 时现在明确失败，不再把环境阻断伪装为成功。

### 构建与供应链

- 将直接依赖和完整传递依赖锁定到经过验证的版本与 SHA-256；构建固定为 Windows x64 + Python 3.13，并且每次重建干净虚拟环境。
- 将 yt-dlp 更新到稳定版 2026.8.19，覆盖上游 2026-07-03 的 Bilibili API 解析修复，并完成真实匿名解析验证。
- 拆分确定性的 PR/main 质量检查与定时/手动公共网络 smoke；所有外部 Actions 固定到审核过的完整提交 SHA，并配置最小权限和并发控制。
- 新增仅由 `v1.2` 标签触发的正式发布工作流，强制校验标签、两级源码版本、构建提交、PE 元数据和资产名称一致。
- 正式发布生成 `BiliDownloader.v1.2.exe`、`BiliDownloader.v1.2.sbom.json` 与 `SHA256SUMS`，并为全部资产生成 GitHub artifact attestation。
- 发布后通过 GitHub API/CLI 复核 Release 状态、标题、标签提交、资产数量、名称、大小、API digest 和 attestation。
- 增加 onefile 归档审计；测量实际体积但不设置体积阈值，并拒绝意外捆绑 Chromium、FFmpeg、凭据、浏览器 profile 或日志。

### 范围

- 保持 Python、PySide6、yt-dlp、PyInstaller、Playwright 和单窗口 Lite 形态；未扩张登录实现，未安装或内置 Chromium，未读取日常浏览器 Cookie，也未捆绑 FFmpeg。

## [1.1] - 2026-07-12

本版本重点改善故障自查、下载完成后的操作体验，并恢复 Lite 版本应有的轻量体积。

### 新增

- 新增独立环境诊断窗口，可检查程序、Python、Windows、yt-dlp、FFmpeg、登录浏览器、应用目录、下载目录和本地登录状态。
- 支持一键复制脱敏诊断报告，不包含 Cookie、账号标识、视频链接或完整用户目录。
- 新增手动 GitHub 更新检查；程序启动和打开诊断窗口时不会自动联网，也不会自动下载或安装更新。
- 新增逐分 P 下载结果窗口，清晰展示成功、失败、取消、输出文件和友好错误信息。
- 下载完成后可直接打开文件或所在目录；文件被移动或删除后会自动禁用对应操作。
- 支持仅重试失败分 P，并沿用原任务的清晰度、保存目录和登录模式；已完成或取消的分 P 不会重复下载。

### 改进

- 解析预检、磁盘空间、目录权限和 FFmpeg 等批次级错误现在也会进入统一下载结果窗口。
- 更改链接或开始解析新视频后，旧结果仍可查看和打开文件，但不能重试旧下载目标。
- 扫码登录和环境检测优先使用 Windows 自带 Microsoft Edge，其次使用系统 Chrome。
- 发布包不再内置 Chromium，EXE 体积由约 383 MiB 降至约 91 MiB。
- 产品名称、窗口标题、Windows 元数据和发布文件名统一为 **Bili Downloader Lite V1.1** 与 `BiliDownloader.v1.1.exe`。

### 验证

- 63 项自动化测试全部通过。
- Python 编译、模块导入、依赖一致性和源码 self-test 通过。
- 系统 Edge Playwright smoke、onedir 和 onefile package smoke 通过。
- onedir ZIP 与 onefile EXE 均确认不包含 `ms-playwright` 或 Chromium 浏览器文件。

## [1.0] - 2026-07-12

### Added

- Initial public release preparation.
- Repository-level documentation, compliance notes, security policy, contribution guide, issue template, PR template, release checklist, and ignore rules.
- Protected local Bilibili session storage with explicit server-validation states and safe anonymous mode.
- Deterministic b23 and multi-part parsing, strict cross-part quality checks, monotonic batch progress, and partial-result reporting.
- Automated lifecycle, privacy, download, configuration, logging, Playwright, and packaging regression gates.

### Fixed

- Native crashes when QR login succeeded, was cancelled, timed out, or the dialog was closed.
- Stale parsed targets remaining downloadable after the URL changed or a later parse failed.
- Unsafe shutdown while parsing, validating a session, downloading, or waiting for FFmpeg.
- Credential cleanup, log redaction/rotation, FFmpeg probing, and atomic configuration persistence edge cases.
