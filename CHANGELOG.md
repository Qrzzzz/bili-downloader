# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.1] - 2026-07-12

### Added

- Added a local environment diagnostics window with a redacted report and manual GitHub update checks.
- Added an actionable per-part download result window with file and folder shortcuts.
- Added failed-part retry while preserving the original download quality, directory, and credential mode.

### Changed

- Unified the product name as **Bili Downloader Lite**, including the main window title and Windows metadata.
- Standardized Windows release filenames as `BiliDownloader.v<version>.exe`.
- Routed batch-level failures through the same structured result flow as completed and partially failed downloads.
- Kept Lite release packages compact by using system Edge first and Chrome second instead of bundling Chromium.

## [1.0] - 2026-07-12

### Added

- Initial public release preparation.
- Repository-level documentation, compliance notes, security policy, contribution guide, issue template, PR template, release checklist, and ignore rules.
- Protected local Bilibili session storage with explicit server-validation states and safe anonymous mode.
- Deterministic b23 and multi-part parsing, strict cross-part quality checks, monotonic batch progress, and partial-result reporting.
- Automated lifecycle, privacy, download, configuration, logging, Playwright, and packaging regression gates.

### Fixed

- Native crashes when QR login succeeded, was cancelled, timed out, or the dialog was closed.
- Stale parsed targets remaining downloadable after the URL changed or a later parse failed.
- Unsafe shutdown while parsing, validating a session, downloading, or waiting for FFmpeg.
- Credential cleanup, log redaction/rotation, FFmpeg probing, and atomic configuration persistence edge cases.
