using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using BiliDownloader.WinUI.Models;

namespace BiliDownloader.WinUI.Services;

public sealed class BackendClient(Func<Process>? startProcess = null, TimeSpan? responseTimeout = null)
{
    private sealed class Pending(bool operation, Action<string>? accepted)
    {
        public bool IsOperation { get; } = operation;
        public Action<string>? Accepted { get; } = accepted;
        public TaskCompletionSource<JsonElement> Completion { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<bool> Acknowledged { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    }
    private sealed record Terminal(JsonElement Result, ErrorInfo? Error, bool Cancelled);
    private readonly ConcurrentDictionary<string, Pending> pending = new();
    private readonly ConcurrentDictionary<string, Pending> operations = new();
    private readonly ConcurrentDictionary<string, long> sequences = new();
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private Process? process;
    private Task? reader, stderrReader;
    private long requestSequence;
    private bool stopping;
    private Exception? transportFailure;
    public event Action<BackendEvent>? Event;
    public event Action<Exception>? Disconnected;

    public void Start()
    {
        if (process is not null) throw new InvalidOperationException("后端已启动。");
        process = (startProcess ?? BackendProcessHost.Start)();
        reader = Task.Run(() => ReadAsync(process.StandardOutput.BaseStream));
        stderrReader = Task.Run(() => DrainStderrAsync(process.StandardError));
    }
    public Task<JsonElement> RequestAsync(string method, object? parameters = null) => SendAsync(method, parameters, false, null);
    public Task<JsonElement> RunAsync(string method, object? parameters, Action<string> accepted) => SendAsync(method, parameters, true, accepted);

    private async Task<JsonElement> SendAsync(string method, object? parameters, bool operation, Action<string>? accepted)
    {
        if (transportFailure is not null) throw new IOException("后端连接已中断，请关闭并重新启动应用。", transportFailure);
        if (process is null || process.HasExited) throw new IOException("下载后端未运行。");
        string id = "r" + Interlocked.Increment(ref requestSequence);
        var call = new Pending(operation, accepted);
        if (!pending.TryAdd(id, call)) throw new InvalidOperationException();
        byte[] message = JsonSerializer.SerializeToUtf8Bytes(new { v = Protocol.Version, type = "request", id, method, @params = parameters ?? new { } }, Protocol.Json);
        if (message.Length + 1 > 64 * 1024) { pending.TryRemove(id, out _); throw new InvalidDataException("请求过长。"); }
        try
        {
            await writeLock.WaitAsync();
            try
            {
                await process.StandardInput.BaseStream.WriteAsync(message);
                await process.StandardInput.BaseStream.WriteAsync(new byte[] { 10 });
                await process.StandardInput.BaseStream.FlushAsync();
            }
            finally { writeLock.Release(); }
            await call.Acknowledged.Task.WaitAsync(responseTimeout ?? TimeSpan.FromSeconds(20));
            return await call.Completion.Task;
        }
        catch (Exception ex) when (ex is TimeoutException or IOException)
        {
            FailConnection(ex);
            throw;
        }
        catch { pending.TryRemove(id, out _); throw; }
    }

    private void FailConnection(Exception failure)
    {
        if (Interlocked.CompareExchange(ref transportFailure, failure, null) is not null) return;
        foreach (var item in pending.Values.Concat(operations.Values))
        {
            item.Acknowledged.TrySetResult(true);
            item.Completion.TrySetException(failure);
        }
        pending.Clear(); operations.Clear(); sequences.Clear();
        // Closing the input pipe requests cooperative backend cleanup, including FFmpeg.
        try { process?.StandardInput.Close(); } catch (Exception ex) { FrontendLog.Write(ex); }
        try { process?.StandardOutput.Close(); } catch (Exception ex) { FrontendLog.Write(ex); }
        if (!stopping) Disconnected?.Invoke(failure);
    }

    private async Task ReadAsync(Stream stream)
    {
        Exception failure = new IOException("下载后端连接已结束，未确认的任务状态未知。请查看日志后重新启动应用。");
        try
        {
            byte[] buffer = new byte[8192];
            using var frame = new MemoryStream();
            int count;
            while ((count = await stream.ReadAsync(buffer)) != 0)
                for (int i = 0; i < count; i++)
                    if (buffer[i] == 10)
                    {
                        using var document = JsonDocument.Parse(frame.ToArray(), new JsonDocumentOptions { MaxDepth = 32 });
                        Handle(document.RootElement);
                        frame.SetLength(0);
                    }
                    else
                    {
                        if (frame.Length >= Protocol.MaximumMessageBytes) throw new InvalidDataException("后端消息超过大小限制。");
                        frame.WriteByte(buffer[i]);
                    }
            if (frame.Length != 0) throw new InvalidDataException("后端消息未完整结束。");
        }
        catch (Exception ex) { failure = ex; }
        finally
        {
            FailConnection(failure);
        }
    }

    private static string RequiredString(JsonElement value, string property)
    {
        if (value.ValueKind != JsonValueKind.Object || !value.TryGetProperty(property, out var item) ||
            item.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(item.GetString()))
            throw new InvalidDataException($"后端终态缺少有效的 {property} 字段。");
        return item.GetString()!;
    }

    private static Terminal ReadTerminal(string name, JsonElement data)
    {
        if (data.ValueKind != JsonValueKind.Object || !data.TryGetProperty("result", out var result) ||
            result.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("后端终态缺少有效的 result 对象。");
        _ = RequiredString(data, "method");
        if (name == "operation.cancelled") return new(result.Clone(), null, true);
        if (name == "operation.completed") return new(result.Clone(), null, false);
        if (!result.TryGetProperty("error", out var error) || error.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("后端失败终态缺少有效的 error 对象。");
        string code = RequiredString(error, "code");
        string message = RequiredString(error, "message");
        if (!error.TryGetProperty("retryable", out var retryable) ||
            retryable.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new InvalidDataException("后端失败终态缺少有效的 retryable 字段。");
        if (!error.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.String)
            throw new InvalidDataException("后端失败终态缺少有效的 detail 字段。");
        return new(result.Clone(), new ErrorInfo(code, message, retryable.GetBoolean(), detail.GetString()!), false);
    }

    private void Handle(JsonElement message)
    {
        if (message.GetProperty("v").GetInt32() != Protocol.Version) throw new InvalidDataException("不兼容的后端协议。");
        if (message.GetProperty("type").GetString() == "response")
        {
            string? id = message.GetProperty("id").GetString();
            if (id is null || !pending.TryRemove(id, out var call)) return;
            try
            {
                if (!message.GetProperty("ok").GetBoolean())
                {
                    call.Completion.TrySetException(new BackendException(Protocol.Read<ErrorInfo>(message.GetProperty("error"))));
                    call.Acknowledged.TrySetResult(true);
                    return;
                }
                var result = message.GetProperty("result").Clone();
                if (call.IsOperation)
                {
                    string operationId = result.GetProperty("operation_id").GetString()!;
                    operations[operationId] = call;
                    call.Accepted?.Invoke(operationId);
                }
                else call.Completion.TrySetResult(result);
                call.Acknowledged.TrySetResult(true);
                return;
            }
            catch (Exception ex)
            {
                call.Acknowledged.TrySetResult(true);
                call.Completion.TrySetException(ex);
                throw;
            }
        }
        if (message.GetProperty("type").GetString() != "event") throw new InvalidDataException("未知的后端消息类型。");
        string op = message.GetProperty("operation_id").GetString()!;
        string name = message.GetProperty("event").GetString()!;
        if (name == "shutdown.ready") return;
        // Ignore stale events from completed operations. Only accepted operations own state.
        if (!operations.TryGetValue(op, out _)) return;
        long seq = message.GetProperty("seq").GetInt64();
        if (seq <= 0 || sequences.TryGetValue(op, out var previous) && seq <= previous) throw new InvalidDataException("后端事件顺序异常。");
        var data = message.GetProperty("data").Clone();
        bool isTerminal = name is "operation.completed" or "operation.failed" or "operation.cancelled";
        Terminal? terminal = isTerminal ? ReadTerminal(name, data) : null;
        sequences[op] = seq;
        Event?.Invoke(new BackendEvent(op, seq, name, data));
        if (terminal is not null && operations.TryRemove(op, out var operation))
        {
            sequences.TryRemove(op, out _);
            if (terminal.Error is not null) operation.Completion.TrySetException(new BackendException(terminal.Error));
            else if (terminal.Cancelled) operation.Completion.TrySetCanceled();
            else operation.Completion.TrySetResult(terminal.Result);
        }
    }

    private static async Task DrainStderrAsync(StreamReader stream)
    {
        char[] buffer = new char[4096];
        while (await stream.ReadAsync(buffer) != 0) { }
    }

    public async Task ShutdownAsync()
    {
        if (process is null) return;
        stopping = true;
        if (!process.HasExited)
        {
            try { await RequestAsync("shutdown"); }
            catch (Exception ex) { FrontendLog.Write(ex); }
            try { process.StandardInput.Close(); } catch (Exception ex) { FrontendLog.Write(ex); }
            await process.WaitForExitAsync();
        }
        if (reader is not null) await reader;
        if (stderrReader is not null) await stderrReader;
        process.Dispose(); process = null;
    }
}
