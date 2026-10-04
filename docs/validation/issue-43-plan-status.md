# #43 分步计划完成情况

核对日期：2026-10-04。计划核对基线 main / v3.6 为 `722c69a0a21a6d6e3e053923ccb2124f642f15c3`。GitHub #36–#41 已全部关闭，三个计划版本均存在公开非草稿 Release。#43 原文的未勾选计划已落后于实际进度，不能据此再次实现同一功能。

| 计划范围 | 实际状态与实现 | 验证记录 |
| --- | --- | --- |
| 3.4：#36 输出恢复可靠性、#37 执行轮次日志 | 已实现，两个 Issue CLOSED；[PR #44](https://github.com/Qrzzzz/bili-downloader/pull/44) 合并为 `b69f6635f90cbdcffaef01f0d2accb9a20afe070`；[v3.4](https://github.com/Qrzzzz/bili-downloader/releases/tag/v3.4) 已公开。 | [3.4 验证记录](./v3.4.md)：470 Python、27 C# 管道、WinUI 构建及原生检查；首次真实双任务通过。 |
| 3.5：#40 暂存分类/保护/清理/还原、#38 多窗口进度同步 | 已实现，两个 Issue CLOSED；[PR #45](https://github.com/Qrzzzz/bili-downloader/pull/45) 合并为 `14f288c88e9fff2d4bdd98e863e7f7aa7069c1e8`；[v3.5](https://github.com/Qrzzzz/bili-downloader/releases/tag/v3.5) 已公开。 | [3.5 验证记录](./v3.5.md)：484 Python、27 C# 管道、WinUI 构建、原生与跨进程/暂存竞态证据。 |
| 3.6：#39 批量草稿/冻结/部分失败重试、#41 长媒体测量/预算/公平排队 | 已实现，两个 Issue CLOSED；[PR #46](https://github.com/Qrzzzz/bili-downloader/pull/46) 合并为当前 main；[v3.6](https://github.com/Qrzzzz/bili-downloader/releases/tag/v3.6) 已公开。 | [3.6 验证记录](./v3.6.md)：504 Python、27 C# 管道、WinUI 构建、87 状态/479 界面检查和媒体测量。支持边界及未复现原 300 秒误拒均已记录。 |
| #42：真实媒体与人工范围 | 当前版本双任务、真实失败重试、单/多分 P 取消继续和独立进程恢复已补测通过；2026-10-04 用户确认手机扫码、文件关联、剪贴板、实体读屏与高对比度验收通过。 | [3.6 真实任务补充验收](./v3.6-external.md) 与脱敏 JSON；五项人工结论已按用户报告补齐。 |

本次还核对 v3.6 Release ID `402813882`、公开时间 `2026-10-04T03:44:38Z`，ZIP、SBOM 与 SHA256SUMS 三项资产均有大小与 API digest。没有重新下载资产，也不把 API 核对等同于本轮本地重新组包。

#43 规定人工依赖未完成时保持 #42 开放，并不要求重复实施已完成的三个版本。#43 的三版功能、联合回归、验证记录和发行说明均已完成；#42 的人工验收此前独立追踪，现已收到用户通过确认。用户随后授权推送并 Release，本核对随 3.7 修复 PR 提交，#43 已随 PR #47 完成；#42 随本次人工验收记录补充 PR 完成。发布证据见 [3.7 验证记录](./v3.7.md)。
