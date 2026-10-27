using System.ComponentModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Navigation;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;
using BiliDownloader.WinUI.ViewModels;
using Windows.System;

namespace BiliDownloader.WinUI.Views;

public sealed partial class DownloadPage : Page
{
    private DownloadViewModel Model => App.Session.Download;
    private bool updatingSelection;
    private bool selectionSyncPending;
    private bool initiallyFocused, resultVisible;
    public DownloadPage() { InitializeComponent(); DataContext = Model; NavigationCacheMode = NavigationCacheMode.Required; }
    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        Model.PropertyChanged += ModelChanged;
        ScheduleSelectionSync();
        resultVisible = Model.ResultVisibility == Visibility.Visible;
        UpdateLayoutMode();
        if (!initiallyFocused) { initiallyFocused = true; InputBox.Focus(FocusState.Programmatic); }
    }
    private void Page_Unloaded(object sender, RoutedEventArgs e) => Model.PropertyChanged -= ModelChanged;
    private void ModelChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (!string.IsNullOrEmpty(e.PropertyName)) return;
        ScheduleSelectionSync();
        UpdateLayoutMode();
        bool visible = Model.ResultVisibility == Visibility.Visible;
        if (visible && !resultVisible) DispatcherQueue.TryEnqueue(() => ResultCard.StartBringIntoView(new BringIntoViewOptions { AnimationDesired = false, VerticalAlignmentRatio = 0 }));
        resultVisible = visible;
    }
    private void Page_SizeChanged(object sender, SizeChangedEventArgs e) => UpdateLayoutMode();
    private void UpdateLayoutMode()
    {
        bool wide = Model.IsBatch && ActualWidth >= 900;
        Model.WideLayout = wide;
        ListColumn.Width = wide ? new GridLength(300) : new GridLength(1, GridUnitType.Star);
        WorkspaceGap.Width = new GridLength(wide ? 20 : 0);
        EditorColumn.Width = wide ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
        Grid.SetColumn(EditorScroll, wide ? 2 : 0);
    }
    private void SyncSelection()
    {
        if (updatingSelection) return;
        updatingSelection = true;
        foreach (var part in PartsList.Items.OfType<VideoPart>())
        {
            if (Model.SelectedParts.Contains(part.Index) && !PartsList.SelectedItems.Contains(part)) PartsList.SelectedItems.Add(part);
            if (!Model.SelectedParts.Contains(part.Index) && PartsList.SelectedItems.Contains(part)) PartsList.SelectedItems.Remove(part);
        }
        updatingSelection = false;
    }
    private void ScheduleSelectionSync()
    {
        if (selectionSyncPending) return;
        selectionSyncPending = true;
        DispatcherQueue.TryEnqueue(() =>
        {
            try { SyncSelection(); }
            finally { selectionSyncPending = false; }
        });
    }
    private void Parts_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (updatingSelection || selectionSyncPending || Model.ApplyingConfiguration) return;
        // WinUI may publish the change before SelectedItems reflects the final
        // collection, especially after replacing the list while loading a draft.
        foreach (VideoPart p in e.RemovedItems) Model.SelectedParts.Remove(p.Index);
        foreach (VideoPart p in e.AddedItems) Model.SelectedParts.Add(p.Index);
        Model.SaveBatchDraft();
        Model.Refresh();
    }
    private async void Parse_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ParseBatchAsync);
    private async void Input_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        bool control = Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Control).HasFlag(Windows.UI.Core.CoreVirtualKeyStates.Down);
        if (e.Key == VirtualKey.Enter && control && Model.CanParse) { e.Handled = true; await App.Session.ExecuteAsync(Model.ParseBatchAsync); }
    }
    private async void Browse_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(async () => { var path = await WindowsShellService.PickFolderAsync(); if (path is not null) Model.DownloadDirectory = path; });
    private async void Download_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.EnqueueAsync);
    private async void BatchParse_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ParseBatchAsync);
    private async void AddBatch_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.EnqueueBatchAsync);
    private void ConfigureBatch_Click(object sender, RoutedEventArgs e)
    {
        Model.ConfigureBatch((BatchInputRow)((FrameworkElement)sender).DataContext);
        EditorScroll.ChangeView(null, 0, null, true);
        DispatcherQueue.TryEnqueue(() => ModeField.Focus(FocusState.Programmatic));
    }
    private void BackToBatch_Click(object sender, RoutedEventArgs e)
    { Model.SaveBatchDraft(); Model.EditingBatch = false; DispatcherQueue.TryEnqueue(() => BatchList.Focus(FocusState.Programmatic)); }
    private void SelectBatchAll_Click(object sender, RoutedEventArgs e) => Model.SelectBatch(true);
    private void SelectBatchNone_Click(object sender, RoutedEventArgs e) => Model.SelectBatch(false);
    private void RemoveBatch_Click(object sender, RoutedEventArgs e) => Model.RemoveBatch((BatchInputRow)((FrameworkElement)sender).DataContext);
    private void UndoRemove_Click(object sender, RoutedEventArgs e) => Model.UndoRemove();
    private async void RetryBatch_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => Model.RetryBatchAsync((BatchInputRow)((FrameworkElement)sender).DataContext));
    private async void Logs_Click(object sender, RoutedEventArgs e)
    {
        var logs = new TextBox { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MaxHeight = 420 };
        logs.Text = Model.Logs;
        await new ContentDialog { XamlRoot = XamlRoot, Title = "诊断日志", Content = logs, CloseButtonText = "关闭" }.ShowAsync();
    }
    private void ApplyBatchDefaults_Click(object sender, RoutedEventArgs e) => Model.ApplyBatchDefaults();
    private void ResetBatchDraft_Click(object sender, RoutedEventArgs e) => Model.ResetBatchDraft();
    private void Tasks_Click(object sender, RoutedEventArgs e) => App.Session.ShowTasks();
    private async void Cancel_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.CancelAsync);
    private void SelectAll_Click(object sender, RoutedEventArgs e)
    { PartsList.SelectAll(); Model.SelectedParts.UnionWith(Model.Parts.Select(p => p.Index)); Model.SaveBatchDraft(); Model.Refresh(); }
    private void SelectNone_Click(object sender, RoutedEventArgs e)
    { PartsList.SelectedItems.Clear(); Model.SelectedParts.Clear(); Model.SaveBatchDraft(); Model.Refresh(); }
}
