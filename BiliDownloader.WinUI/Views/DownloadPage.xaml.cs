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
    private bool initiallyFocused, resultVisible;
    public DownloadPage() { InitializeComponent(); DataContext = Model; NavigationCacheMode = NavigationCacheMode.Required; }
    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        Model.PropertyChanged += ModelChanged;
        SyncSelection();
        resultVisible = Model.ResultVisibility == Visibility.Visible;
        VisualStateManager.GoToState(this, ActualWidth >= 720 ? "Wide" : "Narrow", false);
        if (!initiallyFocused) { initiallyFocused = true; InputBox.Focus(FocusState.Programmatic); }
    }
    private void Page_Unloaded(object sender, RoutedEventArgs e) => Model.PropertyChanged -= ModelChanged;
    private void ModelChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (!string.IsNullOrEmpty(e.PropertyName)) return;
        SyncSelection();
        bool visible = Model.ResultVisibility == Visibility.Visible;
        if (visible && !resultVisible) DispatcherQueue.TryEnqueue(() => ResultCard.StartBringIntoView(new BringIntoViewOptions { AnimationDesired = false, VerticalAlignmentRatio = 0 }));
        resultVisible = visible;
    }
    private void Page_SizeChanged(object sender, SizeChangedEventArgs e) => VisualStateManager.GoToState(this, e.NewSize.Width >= 720 ? "Wide" : "Narrow", false);
    private void SyncSelection()
    {
        if (updatingSelection) return;
        updatingSelection = true;
        foreach (var part in Model.Parts)
        {
            if (Model.SelectedParts.Contains(part.Index) && !PartsList.SelectedItems.Contains(part)) PartsList.SelectedItems.Add(part);
            if (!Model.SelectedParts.Contains(part.Index) && PartsList.SelectedItems.Contains(part)) PartsList.SelectedItems.Remove(part);
        }
        updatingSelection = false;
    }
    private void Parts_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (updatingSelection) return;
        Model.SelectedParts.Clear(); foreach (VideoPart p in PartsList.SelectedItems) Model.SelectedParts.Add(p.Index); Model.Refresh();
    }
    private async void Parse_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ParseAsync);
    private async void Input_KeyDown(object sender, KeyRoutedEventArgs e) { if (e.Key == VirtualKey.Enter && Model.CanParse) { e.Handled = true; await App.Session.ExecuteAsync(Model.ParseAsync); } }
    private async void Browse_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(async () => { var path = await WindowsShellService.PickFolderAsync(); if (path is not null) Model.DownloadDirectory = path; });
    private async void Download_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => Model.DownloadAsync());
    private async void Cancel_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.CancelAsync);
    private void SelectAll_Click(object sender, RoutedEventArgs e) => PartsList.SelectAll();
    private void SelectNone_Click(object sender, RoutedEventArgs e) => PartsList.SelectedItems.Clear();
}
