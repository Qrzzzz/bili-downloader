using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class TaskRow(DownloadTask task, ApplicationSession session) : ViewModelBase
{
    public DownloadTask Value { get; private set; } = task;
    private DownloadTask? detail;
    private bool detailLoading;
    public bool DetailOpen { get; set; }
    public string Id => Value.TaskId;
    public string Title => Value.Title;
    public string Specification => $"{Value.FormatLabel} · {Value.PartCount} 个分 P";
    public bool Active => Value.State is "preparing" or "downloading" or "waiting_resources" or "merging" or "converting" or "postprocessing" or "verifying" or "cancelling";
    public bool NeedsAttention => Value.State is "partial" or "failed" or "cancelled" or "interrupted" or "blocked";
    public bool CanAct => session.Connected && !session.Closing && !Value.Foreign;
    public bool CanCancel => CanAct && (Active || Value.State == "queued") && Value.State != "cancelling";
    public bool CanResume => CanAct && NeedsAttention;
    public bool CanRetry => CanResume && Value.State is "failed" or "partial";
    public bool CanRemove => CanAct && !Active && Value.State != "queued";
    public bool CanReorder => CanAct && Value.State == "queued";
    public Visibility CancelVisibility => Active || Value.State == "queued" ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ResumeVisibility => NeedsAttention ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ReauthorizeVisibility => NeedsAttention && Value.CredentialMode == "saved" ? Visibility.Visible : Visibility.Collapsed;
    public Visibility RetryVisibility => Value.State is "failed" or "partial" ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ProgressVisibility => Active ? Visibility.Visible : Visibility.Collapsed;
    public bool Indeterminate => Value.State != "downloading" || Value.Progress?.OverallPercent is null;
    public double Percent => Math.Clamp(Value.Progress?.OverallPercent ?? 0, 0, 100);
    public string StateText => (Value.Foreign ? "其他窗口 · " : "") + (Value.State switch
    {
        "queued" => "等待下载", "preparing" => "正在准备", "downloading" => "正在下载",
        "waiting_resources" => "等待处理资源", "merging" => "正在合并", "converting" => "正在转换 MP3",
        "postprocessing" => "正在处理", "verifying" => "正在校验", "cancelling" => "正在安全取消",
        "completed" => "已完成", "partial" => "部分完成", "failed" => "失败", "cancelled" => "已取消",
        "interrupted" => "已中断", "blocked" => "需处理", _ => "未知状态"
    });
    public string Metrics => Value.Progress is { } p && Active
        ? $"P{p.PartIndex} · {p.PartNumber}/{p.PartCount} · {Percent:0.#}%" +
          (p.SpeedBytesPerSecond is { } speed ? $" · {speed / 1048576:0.00} MiB/s" : "") : Value.Message;
    public string Directory => Value.OutputDir;
    public string Details => detail is null ? "展开后读取任务详情。" : string.Join(Environment.NewLine,
        (detail.Result?.PartResults ?? []).Select(p => p.ToString())) + Environment.NewLine + detail.Message;
    public string Logs => detail?.Logs ?? "";
    public ObservableCollection<OutputFileItem> Files { get; } = [];
    private OutputFileItem? selectedFile;
    public OutputFileItem? SelectedFile { get => selectedFile; set { Set(ref selectedFile, value); Refresh(); } }
    public bool CanOpenFile => SelectedFile is not null && File.Exists(SelectedFile.Path);
    public bool CanOpenFolder => System.IO.Directory.Exists(Directory);
    public void Update(DownloadTask value) { Value = value; Refresh(); }
    public async Task LoadDetailsAsync()
    {
        if (detailLoading || !session.Connected || session.Closing) return;
        detailLoading = true;
        try
        {
        var reply = Protocol.Read<TaskReply>(await session.Client.RequestAsync("tasks.get", new { task_id = Id }));
        detail = reply.Task;
        string? selected = SelectedFile?.Path;
        Files.Clear();
        foreach (var path in detail.Result?.SavedFiles ?? []) Files.Add(new OutputFileItem(path));
        SelectedFile = Files.FirstOrDefault(f => f.Path == selected) ?? Files.FirstOrDefault();
        Refresh();
        }
        finally { detailLoading = false; }
    }
}

public sealed class TasksViewModel(ApplicationSession session) : ViewModelBase
{
    private readonly Dictionary<string, TaskRow> rows = [];
    public ObservableCollection<TaskRow> Items { get; } = [];
    private long revision = -1;
    private bool refreshing;
    private int filterIndex;
    public int FilterIndex { get => filterIndex; set { if (Set(ref filterIndex, value)) Refilter(); } }
    public bool Paused { get; private set; }
    public string PauseText => Paused ? "继续队列" : "暂停队列";
    public bool CanManage => session.Connected && !session.Closing;
    public bool CanClearCompleted => CanManage && rows.Values.Any(r => r.Value.State == "completed" && r.CanRemove);
    public bool SavedRunning => rows.Values.Any(r => r.Active && r.Value.CredentialMode == "saved" && !r.Value.Foreign);
    public string Summary => $"运行 {rows.Values.Count(r => r.Active)} · 等待 {rows.Values.Count(r => r.Value.State == "queued")} · " +
        $"{rows.Values.Where(r => r.Active).Sum(r => r.Value.Progress?.SpeedBytesPerSecond ?? 0) / 1048576:0.00} MiB/s" + (Paused ? " · 队列已暂停" : "");
    public string EmptyText => rows.Count == 0 ? "还没有任务。在“添加下载”中解析视频并加入队列。" : "此筛选下没有任务。";
    public Visibility EmptyVisibility => Items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    public void Apply(TaskSnapshot snapshot)
    {
        if (snapshot.Revision < revision) return;
        revision = snapshot.Revision; Paused = snapshot.Paused;
        var ids = snapshot.Tasks.Select(t => t.TaskId).ToHashSet();
        foreach (var id in rows.Keys.Where(id => !ids.Contains(id)).ToArray()) rows.Remove(id);
        foreach (var task in snapshot.Tasks) Upsert(task);
        Refilter();
    }
    public void Changed(TaskChange change)
    {
        if (change.Revision <= revision) return;
        revision = change.Revision; Paused = change.Paused; Upsert(change.Task); Refilter();
    }
    public void Upsert(DownloadTask task)
    {
        if (rows.TryGetValue(task.TaskId, out var row)) { if (task.Revision >= row.Value.Revision) row.Update(task); }
        else rows[task.TaskId] = new TaskRow(task, session);
    }
    private void Refilter()
    {
        var wanted = rows.Values.Where(r => FilterIndex switch
        { 1 => r.Active || r.Value.State == "queued", 2 => r.Value.State == "completed", 3 => r.NeedsAttention, _ => true })
            .OrderBy(r => r.Value.Position).ToArray();
        for (int i = Items.Count - 1; i >= 0; i--) if (!wanted.Contains(Items[i])) Items.RemoveAt(i);
        for (int i = 0; i < wanted.Length; i++)
        {
            int old = Items.IndexOf(wanted[i]);
            if (old < 0) Items.Insert(i, wanted[i]); else if (old != i) Items.Move(old, i);
        }
        Refresh(); session.Account.Refresh();
    }
    public async Task ReloadAsync()
    {
        if (!CanManage || refreshing) return;
        refreshing = true;
        try
        {
            Apply(Protocol.Read<TaskSnapshot>(await session.Client.RequestAsync("tasks.list")));
            foreach (var row in rows.Values.Where(r => r.DetailOpen).ToArray()) await row.LoadDetailsAsync();
        }
        finally { refreshing = false; }
    }
    public async Task TogglePauseAsync() => Apply(Protocol.Read<TaskSnapshot>(await session.Client.RequestAsync(Paused ? "queue.resume" : "queue.pause")));
    public async Task ActAsync(TaskRow row, string action, bool reauthorize = false)
    {
        await session.Client.RequestAsync("tasks." + action, new { task_id = row.Id, reauthorize });
        await ReloadAsync();
    }
    public async Task ClearCompletedAsync()
    {
        foreach (var row in rows.Values.Where(r => r.Value.State == "completed" && r.CanRemove).ToArray())
            await session.Client.RequestAsync("tasks.remove", new { task_id = row.Id });
        await ReloadAsync();
    }
    public void Disconnected()
    {
        foreach (var row in rows.Values.Where(r => r.Active)) row.Update(row.Value with { State = "interrupted", Message = "后端连接已中断，重新打开后可确认恢复。", Progress = null });
        Refilter();
    }
}
