<div align="center">

# 📺 Bili Downloader Lite

### A focused native Bilibili video parser and downloader for Windows

**Bilibili URLs / BV / av · Multi-part selection · Account-available qualities · In-app QR login · MP4 / MP3 · Windows x64**

<p>
  <strong>Language</strong><br/>
  <a href="./README.md">简体中文</a> ·
  <strong>English</strong>
</p>

<p>
  <strong>Navigation</strong><br/>
  <a href="https://github.com/Qrzzzz/bili-downloader/releases/latest">Download</a> ·
  <a href="./docs/releases/v2.10.md">Release Notes</a> ·
  <a href="#features">Features</a> ·
  <a href="./docs/architecture/v2.5-winui.md">Architecture</a> ·
  <a href="./SECURITY.md">Security</a> ·
  <a href="./DISCLAIMER.md">Disclaimer</a> ·
  <a href="#development">Development</a> ·
  <a href="./LICENSE">License</a>
</p>

![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D4)
![UI](https://img.shields.io/badge/UI-WinUI%203-0078D4)
![Stack](https://img.shields.io/badge/Stack-C%23%20%2B%20Python-512BD4)
![Downloader](https://img.shields.io/badge/Downloader-yt--dlp-FF5722)
![Output](https://img.shields.io/badge/Output-MP4%20%2F%20MP3-0F766E)
![Release](https://img.shields.io/github/v/release/Qrzzzz/bili-downloader)
![License](https://img.shields.io/github/license/Qrzzzz/bili-downloader)

</div>

---

> [!IMPORTANT]
> This project is intended only for **lawful, authorized, personal-backup, or educational use**.
>
> Users are responsible for ensuring that they have the right to download, store, and use the relevant content and for complying with Bilibili's terms, platform rules, and applicable laws.
>
> This project is not affiliated with Bilibili and does not provide or support bypasses for membership, paid-content, regional, anti-abuse, DRM, or other platform restrictions.

## 📦 Download and run

Download the latest stable release from [GitHub Releases](https://github.com/Qrzzzz/bili-downloader/releases/latest).

The current release is **Bili Downloader Lite v2.10**. See the [release notes](./docs/releases/v2.10.md) and [validation record](./docs/validation/v2.10.md).

| File                              | Purpose                                 |
| --------------------------------- | --------------------------------------- |
| `BiliDownloader.v2.10.win-x64.zip` | Complete Windows x64 application        |
| `BiliDownloader.v2.10.sbom.json`   | Software bill of materials              |
| `SHA256SUMS`                      | SHA-256 checksums for release artifacts |

### Installation

1. Download `BiliDownloader.v2.10.win-x64.zip`.
2. **Fully extract** the ZIP instead of launching the executable from inside the archive.
3. Keep the main executable, backend executable, and runtime files in their original directory structure.
4. Configure FFmpeg as described below.
5. Run:

```text
BiliDownloader.v2.10.exe
```

The release package includes the required **.NET, Windows App SDK, and Python runtimes**. End users do not need to install these separately.

### FFmpeg

Bili Downloader Lite **does not download, install, or bundle FFmpeg**.

Obtain `ffmpeg.exe` yourself from a lawful and trusted source, then either place it at:

```text
BiliDownloader.v2.10.exe
tools/
└── ffmpeg.exe
```

or add the **absolute directory** containing FFmpeg to the system `PATH`.

The application does not execute FFmpeg from arbitrary current working directories.

### System requirements

* Windows x64
* Minimum target: Windows 10 Version 1809
* Recommended: Windows 11

On supported Windows 11 systems, the application can use native Windows effects such as Mica.

## 🚀 Basic workflow

1. Open Bili Downloader Lite.
2. Paste a Bilibili URL, `b23.tv` short link, BV ID, or av ID.
3. Parse the video to retrieve its title, uploader, cover, parts, and currently available formats.
4. If account-authorized formats are required, sign in from the Account page using the in-app QR code.
5. Select the parts, quality, MP4 / MP3 mode, and output directory.
6. Start the download and follow its progress in the application.
7. Review per-item success, failure, or cancellation results and retry failed items when appropriate.

> [!NOTE]
> The application only shows formats that are **actually available to the current account, video, region, and platform policy**. Signing in does not grant access to content or qualities the account is not entitled to use.

## ✨ What's new in v2.10

* Existing media and newly generated output must pass cancellable, time-limited full decoding checks. MP3 audio must be 192 kbps; damaged or incompatible files are preserved and a new name is selected.
* A later part's output preparation failure preserves earlier successful results and saved paths. Retrying failed parts merges correctly with completed items.
* Reusable files bypass the source-media disk budget. Each new sequential download checks the current free space before it starts.

See the [v2.10 release notes](./docs/releases/v2.10.md) and [validation record](./docs/validation/v2.10.md).

<a id="features"></a>

## ✨ Features

### 🎬 Bilibili video parsing

Supported input forms include:

* Standard Bilibili video URLs
* `b23.tv` short links
* BV IDs
* av IDs

After parsing, the application can display:

* Title
* Uploader
* Duration
* Cover image
* Video parts
* Formats and qualities currently available to the active session

Parsing uses the **yt-dlp Python API** rather than launching a separate yt-dlp command-line process for every parse.

### 📥 MP4 video downloads

* Download individual video parts.
* Select multiple parts in one workflow.
* Choose among formats that are actually available to the current session.
* Follow download progress.
* Cancel active tasks.
* View success, failure, or cancellation for every item.
* Retry failed items.
* Output names carry a specification identity so different quality or output modes do not share the same path.
* Existing media matching the requested specification can be validated and reused where applicable.
* Damaged or mismatched same-name media is preserved rather than silently overwritten.

### 🎵 MP3 audio downloads

Audio can be processed as:

```text
MP3 · 192 kbps
```

Audio extraction and media post-processing depend on the user-provided FFmpeg installation.

### 📚 Multi-part videos and task results

For videos with multiple parts:

* Display the complete part list.
* Select only the desired parts.
* Track each part independently in the final result.
* A failure in one item does not hide successful items.
* Failed items can be retried from their original task snapshot.
* Current tasks and results survive navigation between application pages.

### 📱 In-app QR login

Bili Downloader Lite provides a dedicated Account page.

QR codes are generated **locally with Segno**. The application does not launch a browser for login.

The workflow handles:

* Waiting for scan
* Mobile confirmation
* Successful login
* QR expiration
* QR refresh
* User cancellation
* Timeout
* Network errors
* Credential changes during validation

The application **does not ask for the Bilibili account password** and does not read everyday browser cookies.

Candidate cookies are committed as the application session only after they:

1. pass the cookie allowlist;
2. pass validation against the Bilibili NAV API; and
3. are confirmed to still belong to the current login transaction.

### 🔐 Local account state

When login state is saved:

* Cookies remain on the local machine.
* Windows **DPAPI** protects the persisted session.
* Bilibili passwords are never stored.
* No cloud account synchronization is provided.
* Browser-cookie import is not provided.
* Anonymous mode does not read the saved authenticated session.
* Temporary cookie leases are cleaned up when their lifecycle ends.
* Credential generations are tracked across multiple instances to reduce session races and unintended account mixing.

## 🛡️ URL and network boundaries

### Video URLs

Input URLs and resolved short-link destinations are restricted to supported **official Bilibili HTTPS addresses**.

For `b23.tv` redirects, every hop is validated. The application rejects:

* Protocol downgrades
* Redirects to non-allowlisted domains
* Redirect loops
* Unexpected ports
* IP-address destinations
* Unsupported final URLs

### Cover images

Cover images are fetched over HTTPS only from supported Bilibili or official CDN hosts, with controls on:

* Source
* Response type
* Timeout
* Actual response size

A cover-image failure affects only the cover preview and does not replace an otherwise successful video parse.

## 🧭 Focused desktop workflow

The application has three primary pages.

### Download

Contains:

* URL input
* Video parsing
* Metadata
* Part selection
* Quality and output mode
* Destination selection
* Download progress
* Task results
* Failed-item retry

### Account

Contains:

* Anonymous / authenticated state
* In-app QR login
* Login validation
* Credential persistence
* Sign-out
* Login and privacy information

### Settings

Contains:

* System / light / dark appearance
* Environment diagnostics
* Error logs
* Update checking

Download tasks and results live in application-level state and are not recreated simply because the user navigates between pages.

## 🪟 A real native WinUI 3 application

The production Bili Downloader Lite interface is built with:

**C# · .NET 10 · XAML · WinUI 3 · Windows App SDK**

Its main window is a real:

```text
Microsoft.UI.Xaml.Window
```

The application uses native Windows App SDK components including:

* `MicaBackdrop`
* `TitleBar`
* `NavigationView`
* `Frame`
* `Page`
* `InfoBar`
* `ContentDialog`
* Windows Picker / Launcher APIs
* Default WinUI controls, theme resources, and focus states

Windows itself manages:

* Minimize
* Maximize
* Close
* Window menu
* Resize borders
* Caption behavior

The interface is **not implemented with**:

* Qt
* Electron
* Tauri
* HTML
* WebView
* A custom browser-like desktop shell

Windows App SDK may carry WebView2 interoperability dependencies of its own, but Bili Downloader Lite does not use a WebView to render application pages and does not install or bundle Chromium.

See [Windows App SDK / WinUI 3 Architecture](./docs/architecture/v2.5-winui.md) for the implementation and migration details.

## 🌗 Windows appearance and interaction

Supported appearance modes:

* Follow Windows
* Light
* Dark

Mica is used where supported.

The application also preserves:

* System caption buttons
* Native window dragging and resizing
* Per-monitor DPI behavior
* Default Windows focus states
* WinUI high-contrast resources
* Responsive NavigationView layout

It does not redraw a simulated Windows title bar or a custom set of caption controls.

## 🔒 Privacy

Bili Downloader Lite does not provide a cloud service and does not collect telemetry.

The project does not intentionally upload:

* Bilibili cookies
* Account information
* Video-link history
* Download history
* Local logs
* Local application configuration

Network access occurs only when required for an explicit product function, such as:

* Parsing Bilibili videos
* QR login and account validation
* Downloading media
* Accessing GitHub after the user explicitly selects Check for Updates

Environment diagnostics are local by default.

## ⚠️ Platform and copyright boundaries

The project does not attempt to bypass:

* Membership entitlements
* Paid videos
* Regional restrictions
* DRM
* Bilibili anti-abuse controls
* Platform technical protection measures
* Formats unavailable to the current account

For example, when the platform returns an HTTP 412 or another access rejection, the application reports the failure instead of weakening validation or disguising requests to evade the restriction.

See [DISCLAIMER.md](./DISCLAIMER.md) for the complete usage policy.

## 🔑 Remove sensitive information before reporting issues

Before submitting an Issue, Pull Request, screenshot, or log, remove:

* `SESSDATA`
* `bili_jct`
* `DedeUserID`
* `qrcode_key`
* `refresh_token`
* Complete QR polling or success callback URLs
* Cookies
* Session / profile data
* Screenshots or logs identifying an account

Security vulnerabilities should be reported privately according to [SECURITY.md](./SECURITY.md). Do not publish valid login credentials in a GitHub Issue.

## 🧩 Architecture

| Layer          | Technology                           | Responsibility                                      |
| -------------- | ------------------------------------ | --------------------------------------------------- |
| Windows UI     | C# · .NET 10 · WinUI 3               | Native windows, pages, appearance, interaction      |
| Windows SDK    | Windows App SDK 2.4.0                | XAML, windowing, Mica, desktop APIs                 |
| IPC            | Versioned JSONL                      | WinUI ↔ local Python backend                        |
| Backend        | Python 3.13                          | Parse, download, login, diagnostics, task lifecycle |
| Downloader     | yt-dlp                               | Bilibili metadata and media format extraction       |
| Authentication | Requests · Segno · DPAPI             | QR login, validation, local credentials             |
| Media          | FFmpeg / FFprobe                     | Merging, validation, MP3 post-processing            |
| Packaging      | PyInstaller + self-contained .NET    | Windows x64 distribution                            |
| Supply Chain   | Python hash lock · NuGet lock · SBOM | Dependency and release auditing                     |

The WinUI frontend communicates with the Python backend through a versioned JSONL pipe.

In production, WinUI launches:

```text
BiliDownloader.Backend.exe
```

from the application directory. It does not search the system `PATH` for an arbitrary backend executable.

## 📚 Documentation

| Document                                                | Contents                                             |
| ------------------------------------------------------- | ---------------------------------------------------- |
| [v2.10 Release Notes](./docs/releases/v2.10.md)           | Current release changes                              |
| [v2.10 Validation](./docs/validation/v2.10.md)            | Validation record for the current release            |
| [WinUI Architecture](./docs/architecture/v2.5-winui.md) | Native Windows architecture and migration boundary   |
| [IPC v1](./docs/architecture/ipc-v1.md)                 | WinUI ↔ Python protocol                              |
| [Security Policy](./SECURITY.md)                        | Credentials, sensitive data, vulnerability reporting |
| [Disclaimer](./DISCLAIMER.md)                           | Lawful-use and platform boundaries                   |
| [Release Checklist](./RELEASE_CHECKLIST.md)             | Formal release checks                                |
| [Maintainer Notes](./MAINTAINER_NOTES.md)               | Development and release maintenance                  |
| [Third-party Notices](./THIRD_PARTY_NOTICES.md)         | Third-party components and licenses                  |

<a id="development"></a>

## 🧰 Development

Development requires:

* Windows x64
* Python 3.13
* The .NET 10 SDK specified by `global.json`
* PowerShell
* Git

Create the Python environment and install locked dependencies:

```powershell
python -m venv .venv

.\.venv\Scripts\python.exe -m pip install `
  --require-hashes `
  --only-binary=:all: `
  -r requirements.txt
```

Configure the source backend:

```powershell
$env:BILI_BACKEND_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
$env:BILI_BACKEND_SOURCE = (Get-Location).Path
```

Restore NuGet dependencies and start the native WinUI application:

```powershell
dotnet restore BiliDownloader.WinUI --locked-mode -p:Platform=x64
dotnet run --project BiliDownloader.WinUI -p:Platform=x64
```

`python -m app.main` remains only as a compatible backend entry point.

The current desktop UI entry point is:

```text
BiliDownloader.WinUI
```

### Primary Python dependencies

Direct production dependencies currently include:

* `yt-dlp`
* `requests`
* `certifi`
* `websockets`
* `segno`
* `pyinstaller`

Production and transitive Python dependencies are pinned through a hash-locked requirements file.

### Tests

Install development dependencies:

```powershell
python -m pip install `
  --require-hashes `
  --only-binary=:all: `
  -r requirements-dev.txt
```

Run Python tests:

```powershell
python -m pytest -q
python -m pip check
```

Run the C# IPC / frontend-backend integration tests:

```powershell
$env:BILI_TEST_PYTHON = (Get-Command python).Source
$env:BILI_BACKEND_SOURCE = (Get-Location).Path

dotnet run --project BiliDownloader.WinUI.Tests
```

### Build the Windows package

```powershell
.\build.ps1 -Clean
```

The current build output is written to:

```text
dist\
└── BiliDownloader.v2.10.win-x64\
```

with a corresponding ZIP candidate.

Run the packaged smoke test with:

```powershell
.\tools\package_smoke.ps1 `
  -Executable .\dist\BiliDownloader.v2.10.win-x64\BiliDownloader.v2.10.exe
```

PR / main CI runs Python regression tests, WinUI compilation, and C# pipeline tests.

Formal Releases additionally verify:

* Frontend/backend version consistency
* Native application startup
* Package contents
* Python / NuGet dependency state
* Combined SBOM generation
* Release summaries and provenance information

Real QR login, real video downloads, DPI, high contrast, screen-reader behavior, and complete native-window interaction remain external acceptance tasks rather than substitutes for unit tests.

## 🗂️ Repository structure

```text
BiliDownloader.WinUI/         Native C# / WinUI 3 desktop frontend
BiliDownloader.WinUI.Tests/   C# IPC and frontend-backend integration tests

app/
├── backend/                   JSONL backend, protocol, task lifecycle
├── services/                  Parse, download, login, session services
├── downloader.py             yt-dlp, format selection, downloads, FFmpeg
├── auth_qr.py                 Bilibili QR-login protocol
├── cookies.py                 Cookie validation, DPAPI, multi-process state
├── video_urls.py              Bilibili URL validation
├── diagnostics.py             Environment diagnostics
└── logger.py                  Logging, classification, redaction

docs/
├── architecture/              WinUI / IPC architecture
├── releases/                  Historical release notes
└── validation/                Versioned validation records

tools/                         Packaging, auditing, SBOM, release tooling
```

## 🙏 Acknowledgements

Thanks to the projects and maintainers behind:

* [yt-dlp](https://github.com/yt-dlp/yt-dlp) — video metadata extraction and downloading
* [Windows App SDK](https://github.com/microsoft/WindowsAppSDK) — native Windows desktop UI and windowing APIs
* [WinUI Gallery](https://github.com/microsoft/WinUI-Gallery) — official WinUI controls and implementation references
* [Segno](https://segno.readthedocs.io/) — local QR-code generation
* [Requests](https://requests.readthedocs.io/) — HTTP networking
* [FFmpeg](https://ffmpeg.org/) — media merging, validation, and audio processing
* [PyInstaller](https://pyinstaller.org/) — Windows distribution for the Python backend

Microsoft Learn and the Windows App SDK Samples also provide the implementation specifications and reference patterns used for the native Windows architecture.

## 📄 License

This project is licensed under the [MIT License](./LICENSE).

You may use, copy, modify, merge, publish, and distribute the project under the terms of the MIT License.

Third-party components remain subject to their respective license and notice requirements. See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

Using this software does not grant copyright, download authorization, or platform access rights to any third-party video content. Users remain responsible for the lawful use of downloaded material.
