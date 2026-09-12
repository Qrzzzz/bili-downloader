# Release Checklist

只检查本次变更相关的风险。同一提交已有通过的 CI 证据时，不再为填写清单重复跑本地全量回归或独立重建。

## 日常 CI

- `quality.yml` 在 PR 和 main 上执行 Windows / Python 3.13 pytest 与 pip check、真实 WinUI 编译、C# 管道测试；纯 Markdown 变更跳过。
- Python 回归包含三份依赖锁、登录态安全、下载、backend 生命周期及发布约束；NuGet locked restore 校验 .NET 依赖。
- PR 验证合并结果，main 验证实际落地主分支的提交；两者都不打包，也不访问 Bilibili 实时网络。

## 每次发布必须完成

- [ ] 已获得发布授权，候选提交已记录；适用的 Quality checks 通过，提交内容不含凭据、日志、用户配置或本机产物。
- [ ] 源码版本、目标 tag、提交、前端 EXE/ZIP 名称和 Release 标题一致；不移动、覆盖或删除任何已公开标签和 Release。
- [ ] Release 从干净的 Windows x64 / Python 3.13 环境构建一次；哈希锁安装、`pip check`、tag/commit 与 PE 元数据由 `build.ps1` 校验。
- [ ] 最终目录包的 package smoke 和 ZIP 审计通过：真实 WinUI 启动、前后端退出，PE/程序集版本和提交一致，不夹带 Qt、凭据、日志、浏览器运行时或 FFmpeg。
- [ ] 生成 ZIP、联合 CycloneDX SBOM、`SHA256SUMS`；SBOM 包含 NuGet、Python、.NET runtime 和实际文件摘要，许可证资料随包交付；发布前为三份资产生成并验证 attestation。
- [ ] 发布后用 API 核对 Release 状态、标题、tag/commit、资产名称/数量/大小及 digest；本地同一份资产已验证过 attestation，无需再验一遍或重新下载。
- [ ] 3.0 的 Codex 非空实现提交保留可关联的作者邮箱并进入默认分支；核对 commit API 的 `author.login` 为 `chatgpt-codex-connector[bot]`，再验证 GitHub 原生 Contributors。分支或文档署名不算通过，统计缓存未刷新时记录待验证。

## 仅在相关变更时验收

| 变更范围 | 需要的额外验证 |
| --- | --- |
| 二维码协议、登录界面、凭据存储/迁移，或登录相关依赖升级 | 真实手机扫码、确认、刷新/过期、取消/关闭，以及登录态读写；不以 mock 或首次等待扫码状态替代。 |
| 解析/下载逻辑、yt-dlp、FFmpeg 查找或调用 | 用有权访问的内容验证受影响的 MP4/MP3、分 P、取消或重试流程。 |
| 界面布局、窗口层级、交互流程 | 真实 UI Automation/Accessibility Insights/Inspect 语义，系统 caption、拖动、最大化/还原、键盘焦点，DPI 与高对比度；截图只作辅助。 |
| 依赖或分发方式变化 | 对变化的依赖运行漏洞审计，复核对应许可证与 notice；收到新安全公告时也需要复核。首次分发仍需核对全部组件。 |
| 打包脚本、spec、资源或打包依赖 | 可提前本地构建并执行 package smoke，避免到发布时才发现问题；无需恢复每个 PR 的固定打包。 |

相关人工验证未完成时记录待验收及原因。无关变更填写“不适用”，无需每版重新扫码、下载、截图或做全量许可证复核。

`public-smoke.yml` 仅供手动诊断，不作为合并或发布门槛；HTTP 412 仍如实报错并保存结果，不当作产品回归通过。没有 Authenticode 证书、EXE 体积变化也不是发布门槛，不宣称未实际完成的签名。

## 已移除的重复检查

| 原检查 | 处理及原因 |
| --- | --- |
| PR、main、Release 各构建一次 | 正式构建集中在 Release，常规发布由三次构建减为一次。 |
| 独立 Python compile/import、Qt self-test | 导入和后端生命周期由 pytest 覆盖；v2.5 增加实际 WinUI 编译与管道测试，最终原生启动由 package smoke 完成。 |
| CI 单独验证锁，随后 pytest 再验证锁 | CI 由 pytest 验证；独立构建仍在 `build.ps1` 中验证。 |
| 单独 tag/source/commit、PE 校验脚本 | 合并到已有的 `build.ps1` 检查，不保留两套入口。 |
| 发布前后各验证三份 attestation | 发布前验证一次；发布后通过 API digest 确认上传的是相同文件。 |
| 每周公网解析/二维码探测 | 改为手动运行，避免平台网络/风控造成定时失败噪声。 |
| 每版固定 pip-audit、扫码、截图与许可证复核 | 按上表中的变更范围或新公告触发。 |

## 本地检查命令

```powershell
# 日常改动：安装开发依赖后执行，与 Quality checks 一致
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
python -m pytest -q
python -m pip check

# 仅需验证打包时执行；普通开发构建会如实标记 dirty 状态
.\build.ps1
.\tools\package_smoke.ps1 -Executable .\dist\BiliDownloader.v3.0.win-x64\BiliDownloader.v3.0.exe
.\build\.venv\Scripts\python.exe tools\audit_release_artifact.py --executable .\dist\BiliDownloader.v3.0.win-x64.zip --expected-version 3.0
```
