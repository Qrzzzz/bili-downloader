# IPC v2：持久任务与并行队列

应用版本 3.1，协议版本 2。私有 stdin/stdout JSONL 的编码、大小、字段拒绝、错误分类、ACK 在 worker 事件之前、单操作终态、EOF 收尾和前后端精确版本握手继续沿用 [IPC v1](ipc-v1.md)。v1 客户端不能连接 v2 后端。

## 状态所有权

前台解析、扫码、诊断仍是可取消的短期 operation；下载使用持久 task。解析与下载可以同时执行，下载不占用前台 `ApplicationSession.Busy`。后端保留最多 100 份解析结果；新输入只撤销编辑器当前解析，已经入队的任务不依赖它。最多保留 500 条任务记录，一次最多解析 50 个视频，一个任务最多选择 1000 个分 P。

SQLite `tasks.sqlite3` 存储任务快照、执行轮次、逐 P 结果和有界脱敏日志；WAL、FULL synchronous、事务与唯一提交标识确保先落盘再确认。进度是可替换的内存状态，列表摘要不携带完整日志或逐 P 结果，展开时使用 `tasks.get`。旧配置 schema 1 兼容新增 `max_parallel`（默认 2，范围 1—3）。

## 请求

| method | params | result |
| --- | --- | --- |
| parse.batch | input、credential_mode | 异步 operation；终态 `{items:[{input,message,video?}]}` |
| tasks.create | parse_id、part_indices、format_id?、download_mode、download_dir、token | `{task,duplicate}`；立即确认持久任务，不等待执行 |
| tasks.list | {} | `{tasks,paused,max_parallel,revision}`，列表摘要 |
| tasks.get | task_id | `{task}`，包含完整结果及日志 |
| tasks.cancel | task_id | `{task}`，正在执行时仅确认取消请求 |
| tasks.retry | task_id、reauthorize? | `{task}`，仅重试失败 P，保留成功和取消 P |
| tasks.resume | task_id、reauthorize? | `{task}`，执行所有尚未成功的 P |
| tasks.reorder | task_id | `{task}`，将等待任务移到队首 |
| tasks.remove | task_id | `{removed:task_id}`；先取消再移除，文件保留 |
| queue.pause / queue.resume | {} | 完整列表摘要；暂停不取消在途工作 |
| settings.update | download_dir?、theme?、max_parallel?、remember_download_preferences?、download_mode?、preferred_quality? | 原子保存后的完整设置 |
| settings.remember | download_mode?、preferred_quality? | 自动记忆开启时原子保存；关闭时返回原设置，不写入 |

`token` 在重复提交中保持不变；同 token 对应不同任务规格返回 `idempotency_conflict`。队列内同视频、分 P、模式、画质、目录与凭据身份的重复任务返回原任务。模式与画质必须来自后端解析选择，不接受任意 selector、媒体直链、headers、Cookie 或原始 yt-dlp 字典。

## 下载偏好

3.1 在 schema 1 中增加三个可选配置字段：`remember_download_preferences` 默认为 `true`；`download_mode` 为 `audio_video`（默认）或 `audio_mp3`；`preferred_quality` 为 `null`（最高可用）或 1—16384 的整数分辨率高度。读取旧配置补齐默认值，读取损坏的偏好字段只恢复该项，更新请求中的非法字段/类型则拒绝整次更新。

偏好不保存临时 `format_id`。新解析按高度选择当前格式，最高可用按每个视频独立选择；指定高度缺失时单项提示重新选择，批量中该项不入队，其余有效项可继续。MP3 忽略画质要求并保留原视频偏好。自动应用、清空解析结果不会覆盖用户偏好；只有主动选择触发 `settings.remember`。前端串行保存字段补丁，设置草稿按字段合并，正常关闭等待已发起的自动保存。所有偏好仅作用于编辑器和后续创建的任务，既有任务规格、重试和恢复维持原快照。

## 事件

下载任务使用 hello 返回的 `session_id` 作为事件 `operation_id`，名称固定为 `task.changed`，data 为 `{task,revision,paused}`。管道 `seq` 在会话内严格递增；任务中的 `attempt_id` 标识执行轮次，`revision` 标识持久修改版本；外层 revision 标识本实例的可见状态变化。每个执行轮次终态独立，任务重试不会复用旧 operation。

客户端在读取 hello response 时登记 session_id，然后才读取后续帧；拒绝错误会话、顺序或结构。先读取 tasks.list 快照，再按 revision 应用更新；迟到的旧快照不能覆盖新事件，旧任务版本不能覆盖新轮次。2 秒本地刷新同步其他窗口状态与已展开详情，不访问公网。

任务状态：queued、preparing、downloading、waiting_resources、merging、converting、postprocessing、verifying、cancelling、completed、partial、failed、cancelled、interrupted、blocked。分 P 的 completed/failed/cancelled 不等于整个任务终态。

## 调度与恢复

每个窗口默认同时执行 2 个任务，每任务内的分 P 顺序执行。任务等待输出资源或后处理时继续占用任务名额。元数据解析与 FFmpeg 重处理分别使用跨进程互斥租约，所有本机实例共享。降低并发数不打断在途任务；登录态任务在途时禁止当前实例更改账号，账号操作期间不会新启动登录态任务。

任务持久化原始分 P 身份与画质策略，不持久化 selector 或媒体直链。每轮执行重新解析并核对分 P 和画质；保存凭据在网络开始前检查原代次，实际 Cookie lease 继续在同一存储事务中检查。凭据改变时需用户明确重新确认；匿名任务不读取本地凭据。

输出按规范路径和规格跨进程互斥，临时媒体放在目标目录的 `.bili-tasks/<task_id>/`，保持重试/恢复路径稳定。验证通过后使用不覆盖现有文件的原子发布操作；并行任务可以复用已经完整验证的同规格输出。取消或移除记录不会删除媒体和中间文件。

空间预算按磁盘卷协调，已知大小保守预留估计量的 2.1 倍加 16 MiB，以容纳源流和最终媒体；未知大小先按 64 MiB 估计。下载获知更大媒体大小时按块扩大预留并复核空间，不足时停止当前任务。仍保留执行前实际可用空间检查和磁盘满错误处理，未知大小不构成空间足够的保证。

每实例持有 OS 文件租约；恢复时只将租约已释放实例的非终态任务标记为 interrupted。其他窗口的任务只读，不能重复领取；确认恢复使用 SQLite 事务认领。启动不自动联网恢复，正常关闭停止调度、取消并等待全部 worker 后关闭存储。异常退出由 OS 释放租约，下次启动继续恢复。存储失败暂停调度并协作取消在途任务，提示重新打开，不声称已经成功持久化结果。

旧 `download.start/retry` 保留用于已有单任务服务回归；3.0 界面的正式下载入口为 `tasks.create`。
