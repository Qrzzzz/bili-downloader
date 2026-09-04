"""Architecture acceptance gates, separate from real desktop/manual evidence."""
from pathlib import Path
import ast
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def test_real_winui_project_owns_the_windows_shell():
    project = ET.parse(ROOT / "BiliDownloader.WinUI/BiliDownloader.WinUI.csproj").getroot()
    assert project.findtext(".//UseWinUI") == "true"
    assert project.findtext(".//TargetFramework").startswith("net10.0-windows")
    references = {p.attrib["Include"] for p in project.findall(".//PackageReference")}
    assert "Microsoft.WindowsAppSDK" in references
    xaml = ET.parse(ROOT / "BiliDownloader.WinUI/MainWindow.xaml").getroot()
    names = {element.tag.split("}")[-1] for element in xaml.iter()}
    assert {"Window", "Window.SystemBackdrop", "MicaBackdrop", "TitleBar", "NavigationView", "Frame", "InfoBar"} <= names
    code = (ROOT / "BiliDownloader.WinUI/MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "MainWindow : Microsoft.UI.Xaml.Window" in code
    assert "ExtendsContentIntoTitleBar = true" in code and "SetTitleBar(AppTitleBar)" in code
    assert "PerMonitorV2" in (ROOT / "BiliDownloader.WinUI/app.manifest").read_text()


def test_backend_import_graph_contains_no_qt_and_ui_contains_no_web_renderer():
    for file in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all(not name.startswith(("PySide", "PyQt", "shiboken")) for name in names), file
    for file in (ROOT / "BiliDownloader.WinUI").rglob("*.xaml"):
        if "obj" in file.parts or "bin" in file.parts:
            continue
        text = file.read_text(encoding="utf-8")
        assert not re.search(r"<(?:\w+:)?(?:WebView2?|FluentButton|Win11ComboBox|MicaWidget)\b", text)
        assert not re.search(r'#[0-9a-fA-F]{6,8}\b', text)
    assert not (ROOT / "app/ui_main.py").exists()
    assert not (ROOT / "app/ui_dialogs.py").exists()
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pyside6" not in runtime and "pytest-qt" not in runtime
