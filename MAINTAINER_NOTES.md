# Maintainer Notes

## v1.4 候选发布边界

- 版本严格为源码 `1.4`、标签 `v1.4`、成品 `BiliDownloader.v1.4.exe`、Release 标题 `Bili Downloader Lite v1.4`；不允许多一级版本。
- 已公开 `v1.1`、`v1.2` 与 `v1.3` 是不可改写历史，不得移动、删除或重置标签。v1.4 必须从已验收发布的 v1.3 提交 `01d7a0df4fee38e31067f4971f92a70de45e7c54` 发展。
- `quality.yml` 只运行确定性检查，不访问 Bilibili 实时网络。`public-smoke.yml` 仅定时/手动运行匿名解析和二维码首次等待态检查；HTTP 412 必须失败留证。
- `release.yml` 只由精确 `v1.4` 触发，并绑定 tag/source/commit/PE/asset/digest/attestation。不得从分支 push 触发发布。
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

## v1.4 稳定性边界

- FFmpeg 查找顺序固定为 EXE 相邻 `tools\ffmpeg.exe`、PyInstaller 资源目录同布局、绝对 PATH；不搜索当前工作目录，不下载、不捆绑、不安装，也不修改系统 PATH。
- b23 只手工处理 301/302/303/307/308；每跳请求前验证官方 HTTPS 主机、无 userinfo、无异常端口，限制跳数和循环。最终必须是受支持的 bilibili.com BV/av 视频 URL。
- 封面只接受 Bilibili/官方 CDN HTTPS 图片，使用连接/读取超时、streaming、Content-Length 预检和实际 5 MiB 硬上限；封面异常不能覆盖成功解析结果。
- 每实例运行标记包含 schema、PID、Windows 进程创建时间和随机 instance id，原子写入。多实例保持允许；正常退出只删除本实例标记。

## 归档与仓库禁入项

源码、锁、构建环境、PyInstaller CArchive 和内嵌 PYZ 都要复核以下内容为零：

- Playwright Python 包、driver/Node、`ms-playwright`、Chromium/Chrome/Edge runtime、Electron executable/runtime。
- 浏览器 profile、session/凭据、`storage_state.json`、`cookies.txt`、`session.dat`、日志。
- `ffmpeg.exe`、`ffprobe.exe`、下载文件、测试视频、`.venv/`、`build/`、`dist/`、`__pycache__/`。
- 本机绝对路径、用户名、账号信息、token 和用户配置。

历史 CHANGELOG 事实、迁移测试和旧残留清理字符串不应被文本搜索误判为运行时打包内容。

## 发布前人工 Gate

- 使用真实 Bilibili App 扫描应用内二维码，验证已扫码待确认、成功、过期/刷新、取消/关闭、连续重开和应用退出。
- 扫码后复核登录解析/下载、退出登录和旧 v1.2 凭据直接可读。该 Gate 需用户参与，自动测试不得伪造通过。
- 核对第三方许可证/notice，尤其是 PySide6/Qt、yt-dlp、Segno、PyInstaller 和由用户自行提供的 FFmpeg。
- 记录 EXE 实际 bytes/MiB、SHA-256 与 Authenticode 状态；本机无签名证书不阻断候选构建，但必须如实报告。
