# Bili Downloader IPC v1

运输：父进程启动配套 Python 后端，私有 stdin/stdout 管道，UTF-8 JSON Lines，每条以 LF 结束。stdout 只放协议；第三方 print、stderr 和应用日志不混入协议。无端口、远程服务、任意 shell 命令或任意 Python 调用入口。应用 2.5 与 IPC 1 分别版本化；hello 同时检查二者，前后端应来自同一发行包。

## Envelope

```json
{"v":1,"type":"request","id":"r1","method":"hello","params":{"protocol_version":1,"frontend_version":"2.5"}}
{"v":1,"type":"response","id":"r1","ok":true,"result":{"protocol_version":1,"backend_version":"2.5","session_id":"opaque","safe_mode":false,"settings":{"download_dir":"C:\\Downloads","theme":"system","schema_version":1},"status":{"code":"none","text":"无本地登录凭据","generation":null},"config_diagnostics":[],"capabilities":["parse","thumbnail","audio_video","audio_mp3","qr","retry","diagnostics"],"limits":{"request_bytes":65536,"message_bytes":16777216}}}
```

请求 ID 为 `[A-Za-z0-9_-]{1,80}`，本次进程会话不得复用；最多 100000 个。JSON 禁止重复字段、NaN/Infinity、错误编码和未知请求/参数字段。请求包含换行最多 64 KiB；消息最多 16 MiB。超过大小限制的终态转换为小型结构化错误。协议损坏、进程退出或半帧 EOF 在 C# 中使待处理调用失败，阻止继续提交不确定任务。

失败 response 是 `ok:false,error:{code,message,retryable,detail}`，没有 result。无法识别请求身份时 id 为 null。业务错误沿用原有稳定分类，如 `platform_412`、`ffmpeg_missing`、`invalid_url`。协议错误还包括 `handshake_required`、`protocol_mismatch`、`invalid_request`、`invalid_params`、`duplicate_request`、`backend_busy`、`stale_parse`、`stale_session`、`stale_batch`、`consent_required`、`safe_mode`、`shutting_down`。

## 同步命令

| method | params | result |
| --- | --- | --- |
| hello | protocol_version:int，frontend_version:string | 上例；其余命令必须先握手 |
| settings.get | {} | download_dir，theme，schema_version |
| settings.update | download_dir 和/或 theme | 校验、原子保存后的完整 settings；失败保留旧配置 |
| session.status | {} | code，text，generation:string/null |
| parse.invalidate | {} | input_revision:int；撤销旧解析并请求取消在途解析 |
| operation.cancel | operation_id:string | state:cancel_requested/already_finished，waiting_for_postprocessing?:bool |
| auth.qr.refresh | operation_id:string | state:refresh_requested，minimum_generation:int |
| shutdown | {} | state:draining；不再接受新任务，协作取消并等待资源释放 |

theme 枚举 system/light/dark。credential_mode 枚举 anonymous/saved。schema 与目录兼容规则由现有 config 层决定。

## 异步操作

接受操作时先返回 `result:{operation_id,state:"accepted"}`，然后才启动 worker。C# 必须在消费后续帧前注册 operation_id。前端响应等待上限为 20 秒；任务执行等待真正终态，没有通过超时强杀后处理的机制。

| method | params | 终态 result |
| --- | --- | --- |
| parse.start | input:string，credential_mode | VideoInfo |
| media.thumbnail | parse_id:string | parse_id，base64（空串表示无图片）；失败不撤销成功解析 |
| download.start | parse_id，part_indices:int[]，format_id?:string，download_mode，credential_mode，download_dir | BatchResult |
| download.retry | batch_id:string | 合并原成功项与本轮失败项重试的 BatchResult |
| auth.qr.start | consent:true | code，friendly，detail，status |
| session.validate | {} | code，text，generation；invalid/none 撤销旧解析 |
| session.clear | {} | ok:bool，failures:string[]，remaining_count:int，status |
| diagnostics.run | {} | items:[{name,status,summary,detail}]，text（脱敏文本） |
| updates.check | {} | current_version，latest_version:null/string，release_url:null/string，update_available:bool，message |

download_mode 枚举 audio_video/audio_mp3。MP4 的 format_id 是当前解析 formats 中的 opaque ID；MP3 使用后台既有最佳可用音轨策略。禁止从前端直接注入 yt-dlp selector、原始 info_dict、headers、Cookie 或任意网络地址。

VideoInfo：`parse_id,input_revision,title,uploader,duration_seconds,raw_id,source_url,current_part_index,thumbnail_available,parts,formats`。parts 每项含 `index,title,duration_seconds,id`；formats 每项含 `format_id,label,height,policy`，标签与实际下载策略由既有后端生成。

BatchResult：`batch_id,outcome,saved_files,output_dir,retry_allowed,part_results`。outcome 为 completed/partial/failed/cancelled。每个 part_result 包含 `index,title,status,saved_files,error`；status 为 completed/failed/cancelled，error 为错误对象或 null。output_dir 是原任务目录，不能被当前编辑框内容替代。

## 事件和终态

```json
{"v":1,"type":"event","operation_id":"opaque","seq":1,"event":"download.progress","data":{"phase":"downloading","part_index":1,"part_number":1,"part_count":2,"part_percent":20,"overall_percent":10,"downloaded_bytes":2048,"total_bytes":null,"total_bytes_estimate":10240,"speed_bytes_per_second":4096,"eta_seconds":2,"cancel_requested":false}}
{"v":1,"type":"event","operation_id":"opaque","seq":2,"event":"operation.completed","data":{"method":"download.start","result":{"batch_id":"opaque","outcome":"cancelled","saved_files":[],"output_dir":"C:\\Downloads","retry_allowed":false,"part_results":[{"index":1,"title":"示例","status":"cancelled","saved_files":[],"error":null}]}}}
```

seq 对 operation 严格递增，允许因进度合并或丢弃出现间隔，不允许倒序或复用。一个已接受的操作恰有一个 terminal：operation.completed、operation.failed 或 operation.cancelled。failed 的 result 为 `{error:...}`。取消的下载仍通过 completed 返回部分文件及逐项 cancelled 结果；扫码取消仍返回 LoginOutcome。不能把请求取消的 ACK 当成已经停止。

- `download.progress`：上例字段，未知数值为 null，数值非负且有限；phase 保留 preparing/downloading/merging/converting/postprocessing/completed/failed/cancelled 等既有阶段。
- `log.message`：`{text}`，先脱敏并截断；前端只保留有限文本。
- `auth.qr.state`：`{generation,code,text}`，code 为 generating/waiting_scan/waiting_confirmation/expired/validating/verified。
- `auth.qr.image`：`{generation,mime_type:"image/png",base64}`。不发送 qrcode_key、回调 URL、Cookie、refresh_token 或服务端响应原文。图片只存在内存，不记入日志。
- `shutdown.ready`：operation_id 使用 session_id，data 为 {}；它不属于业务操作，之后 stdout 结束。

发送队列有 128 条上限；持续 downloading 进度最多约 10 Hz。普通进度与日志可丢弃，终态与后处理状态不丢弃。前端只处理已登记的 operation，忽略旧 operation 的迟到事件；异步图片还校验输入修订或 QR generation。

## 状态所有权

同时只允许一个解析、下载、登录、验证、清除或诊断任务。管道 reader 始终可以处理 cancel、refresh、设置查询与 shutdown；不会让 UI 依赖 QObject/QThread/Signal。

解析持有 credential mode、输入 revision 和 session generation。下载开始冻结 source URL、分 P、配置、实际 selector、模式、原目录及凭据模式。失败重试只使用后台保存的原快照和失败 P，最多保留 16 个批次。改链接、重新解析或变更/清除账号状态会令相应重试失效。设置更改不改写正在执行的快照。

QR 刷新先标记旧代失效，关闭旧会话并丢弃迟到成功。Cookie 仍由原有允许列表、NAV 验证、取消检查、DPAPI 和原子提交事务管理。EOF 与 shutdown 都请求协作取消；资源释放后才发送终态并退出。
