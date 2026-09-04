# Release Checklist

只检查本次变更相关的风险。同一提交已有通过的 CI 证据时，不再为填写清单重复跑本地全量回归或独立重建。

## 日常 CI

- `quality.yml` 在 PR 和 main 上执行 Windows / Python 3.13 的完整 pytest 与 `pip check`；纯 Markdown 变更跳过。
- pytest 已包含三份依赖锁一致性、登录态安全、下载、界面生命周期及发布约束测试，无需再逐项人工勾选。
- PR 验证合并结果，main 验证实际落地主分支的提交；两者都不打包，也不访问 Bilibili 实时网络。

## 每次发布必须完成

- [ ] 已获得发布授权，候选提交已记录；适用的 Quality checks 通过，提交内容不含凭据、日志、用户配置或本机产物。
- [ ] 源码版本、目标 tag、提交、EXE 名称和 Release 标题一致；不移动、覆盖或删除任何已公开标签和 Release。
- [ ] Release 从干净的 Windows x64 / Python 3.13 环境构建一次；哈希锁安装、`pip check`、tag/commit 与 PE 元数据由 `build.ps1` 校验。
- [ ] 最终 EXE 的 package smoke 和归档审计通过：能启动/退出，内嵌版本和提交正确，不夹带凭据、日志、浏览器运行时或 FFmpeg。
- [ ] 生成 EXE、CycloneDX SBOM、`SHA256SUMS`，发布前为三份资产生成并验证 attestation。
- [ ] 发布后用 API 核对 Release 状态、标题、tag/commit、资产名称/数量/大小及 digest；本地同一份资产已验证过 attestation，无需再验一遍或重新下载。

## 仅在相关变更时验收

| 变更范围 | 需要的额外验证 |
| --- | --- |
| 二维码协议、登录界面、凭据存储/迁移，或登录相关依赖升级 | 真实手机扫码、确认、刷新/过期、取消/关闭，以及登录态读写；不以 mock 或首次等待扫码状态替代。 |
| 解析/下载逻辑、yt-dlp、FFmpeg 查找或调用 | 用有权访问的内容验证受影响的 MP4/MP3、分 P、取消或重试流程。 |
| 界面布局、窗口层级、交互流程 | 检查受影响界面的截图和交互；改动窗口层级时确认顶层窗口数量。 |
| 依赖或分发方式变化 | 对变化的依赖运行漏洞审计，复核对应许可证与 notice；收到新安全公告时也需要复核。首次分发仍需核对全部组件。 |
| 打包脚本、spec、资源或打包依赖 | 可提前本地构建并执行 package smoke，避免到发布时才发现问题；无需恢复每个 PR 的固定打包。 |

相关人工验证未完成时记录待验收及原因。无关变更填写“不适用”，无需每版重新扫码、下载、截图或做全量许可证复核。

`public-smoke.yml` 仅供手动诊断，不作为合并或发布门槛；HTTP 412 仍如实报错并保存结果，不当作产品回归通过。没有 Authenticode 证书、EXE 体积变化也不是发布门槛，不宣称未实际完成的签名。

## 已移除的重复检查

| 原检查 | 处理及原因 |
| --- | --- |
| PR、main、Release 各构建一次 | 正式构建集中在 Release，常规发布由三次构建减为一次。 |
| 独立 compile/import、源码 self-test | 导入和界面生命周期由 pytest 覆盖；最终启动验证由 EXE package smoke 完成。 |
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
.\build.ps1 -OneFile
.\tools\package_smoke.ps1 -Executable .\dist\BiliDownloader.v2.3.exe
.\build\.venv\Scripts\python.exe tools\audit_release_artifact.py --executable .\dist\BiliDownloader.v2.3.exe --expected-version 2.3
```
