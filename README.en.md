<p align="center">
  <img src="resources/peach-logo.png" alt="Peach" width="96">
</p>

<h1 align="center">PeachDesktop</h1>

<p align="center">A Windows desktop customization of Peach, with a WebView2 window and PotPlayer or built-in playback.</p>

> **Modified version notice · 2026-10-26**: Based on [peach-mitao/peach](https://github.com/peach-mitao/peach), Copyright (C) 2026 longmeidao. This fork is independently maintained by [guoman630308-oss](https://github.com/guoman630308-oss). Application code remains [AGPL-3.0-or-later](LICENSE); third-party components retain their own licenses. See the [fork reference](docs/FORK.md) and [NOTICE](NOTICE.md) for attribution, changes and distribution requirements.

<p align="center">
  <a href="https://peach.video">Upstream website</a> ·
  <a href="https://github.com/guoman630308-oss/peach-desktop/releases">Fork releases</a> ·
  <a href="https://demo.peach.video">Upstream demo</a> ·
  <a href="https://github.com/peach-mitao/peach/releases/tag/intro-video">Upstream intro video</a> ·
  <a href="https://github.com/guoman630308-oss/peach-desktop/issues">Fork issues</a> ·
  <a href="https://github.com/orgs/peach-mitao/projects/7/views/1">Requests board</a> ·
  <a href="https://github.com/orgs/peach-mitao/projects/7/views/3">Roadmap</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="README.md">中文</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?logo=gnu&amp;logoColor=white" alt="license"></a>
  <a href="docs/TESTING_DESKTOP.md"><img src="https://img.shields.io/badge/Windows-x64-0078D4" alt="Windows x64"></a>
  <a href="docs/OPERATIONS.md#macos-独立测试包"><img src="https://img.shields.io/badge/macOS-DMG-555555?logo=apple&amp;logoColor=white" alt="Build and install the standalone macOS app"></a>
  <img src="https://img.shields.io/badge/18%2B-adult%20content-critical" alt="18+ adult content">
</p>

https://github.com/user-attachments/assets/a5610874-e611-41bb-be22-7d99f0d64b62

> **18+** For adults managing their own adult-content collections. The repository contains no media or site data. Access to outside sites requires the appropriate accounts and permissions.

## Customizations and resource use

- **Windows window**: WebView2 displays the Peach interface, with the tray owning the local service. External websites and manual verification use the system browser.
- **PotPlayer playback**: detect PotPlayer or select another executable. External playback is the default in the Windows window, with built-in playback available in the player area. External progress is not synchronized.
- **Playback-aware previews**: local cards do not start hover video previews in external-player mode, reducing background conversion and cache writes while browsing.
- **Single-file import**: select one video inside a configured media directory and register, probe and collect metadata for that file.
- **JavLibrary source**: search and parse matching titles, preserve exact code checks and reuse manual browser verification within the existing source chain and cooldown policy.
- **Navigation**: return to a directory index with filters, loaded pages and scroll position preserved. The back icon has no normal border and shares sidebar icon colors; the gray marker stays on the selected navigation item.

Scanning, performer/code relationships, tags, favorites, follows, cloud downloads and the built-in player originate from Peach. See the [module reference and known limitations](docs/FORK.md) for the full scope. Windows x64 is this fork's validated platform. A general movie/TV/season/episode data model remains future work.

## Features

The following core capabilities are inherited from upstream. Enable network sources, follows and downloads through their settings.

- **Local and cloud together**: browse hard drives and mounted cloud drives, including 115, PikPak and OneDrive, in one library.
- **Pick from the video wall**: drag through a full-screen cover wall and play in place, with versions and parts of the same title grouped together.
- **Player subtitles**: load local subtitles, search SubtitleCat for simplified and traditional Chinese subtitles by video code, then adjust fonts, size, colors and backgrounds. No API key is required; results may include machine translations.
- **Bring your existing collection**: read NFO files and posters, then fill missing titles, performers, studios and covers from multiple sources while keeping your edits.
- **Follow across sites**: combine a creator's updates from FANBOX, Patreon, Kemono and other sites, with matching posts grouped into one card.
- **Find your next watch**: subscribe to performers' new releases, discover titles based on your taste and save them to your want list.
- **Cloud download**: search by video code or paste a magnet link, send it to 115 or PikPak and organize the downloaded video into your library automatically.
- **Add JAV from your browser**: detect or select a video code on JavDB, MissAV, Jable and other sites, then send it to Peach for cloud download and library ingestion. The [userscript](https://github.com/peach-mitao/peach-userscript) is maintained independently; see [installation and pairing](docs/JAV_BROWSER.md).
- **Taste preferences**: combine viewing, feedback, your library, browser history, bookmarks and follows to understand your preferences. Read history and bookmarks from supported local browsers in one action, or import HTML bookmarks and Chromium Bookmarks JSON. Stop reading a source or remove it when needed. See [taste data sources](docs/TASTE_SOURCES.md).

Watch history, favorites and settings stay on your computer. Scanning leaves original files untouched; manual organization offers a preview and an undo for the last batch.

Scanning, follow updates and new-release metadata collection save their progress and continue unfinished items after the app restarts. See [task recovery](docs/OPERATIONS.md#长时任务断点续跑) for recovery boundaries.

The combined "Send version statistics and error diagnostics (recommended)" option is checked by default. Clear it during installation, first setup or in settings to disable both. Saved opt-out choices are preserved. While enabled, it sends the listed technical fields to Sentry; full logs, diagnostic ZIPs, media details and watch history are excluded. See [remote diagnostics](docs/DIAGNOSTICS.md#可选远程诊断).

Sharing library and taste details is a separate option in the installer and settings, off by default. Review the complete fields and local preview before explicitly consenting to weekly snapshots of works, tags, ratings, viewing summaries and every preference dimension. Account, device and installation identifiers are excluded. Detailed snapshots can still be linked or re-identified, and the receiving service can see the connection IP. See the [diagnostics reference](docs/DIAGNOSTICS.md#馆藏与口味群体研究).

## Screenshots

These videos and screenshots belong to the upstream demonstration and illustrate the inherited library interface. See [the Windows window guide](docs/NATIVE_WINDOW.md) for this fork's desktop behavior.

Try search, filters and profiles in the [read-only demo](https://demo.peach.video). Click a screenshot for the original image.

<table>
  <tr>
    <td width="50%"><a href="https://peach.video/assets/screenshots/home-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/home-20261014.webp" alt="Library filters and video cards" width="600"></a></td>
    <td width="50%"><a href="https://peach.video/assets/screenshots/profile-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/profile-20261014.webp" alt="Performer portrait, profile and videos" width="600"></a></td>
  </tr>
  <tr>
    <td align="center">Library: browse by performer, source and tag</td>
    <td align="center">Performer profile: details, aliases and all videos</td>
  </tr>
  <tr>
    <td><a href="https://peach.video/assets/screenshots/follow-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/follow-20261014.webp" alt="Creator navigation and image updates" width="600"></a></td>
    <td><a href="https://peach.video/assets/screenshots/stats-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/stats-20261014.webp" alt="Library, storage and content statistics" width="600"></a></td>
  </tr>
  <tr>
    <td align="center">Following: creator updates across sites</td>
    <td align="center">Statistics: storage and watch history</td>
  </tr>
  <tr>
    <td colspan="2"><a href="https://peach.video/assets/screenshots/wall-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/wall-20261014-readme.webp" alt="Full-screen video cover wall" width="1200"></a></td>
  </tr>
  <tr>
    <td colspan="2" align="center">Video wall: drag to browse, play in place</td>
  </tr>
</table>

## Download and use

### Windows

1. Use the actual `PeachDesktop-<version>-windows-x64-setup.exe` assets on [this fork's Releases](https://github.com/guoman630308-oss/peach-desktop/releases), or extract a window portable package and run `Peach.exe`. Build from source when the desired asset is not published.
2. Microsoft Edge WebView2 Runtime is required. Choose media directories and access permissions inside the desktop setup window.
3. Scan and browse your collection. Clicking a video opens the configured external player; built-in playback remains available in the player area.

The full window package includes media tools. Source credentials, manual verification and the optional amane bridge are configured separately; see [the Windows window guide](docs/NATIVE_WINDOW.md).

Window test builds are unsigned. Check the release source and checksums before installation. See the [Windows guide](docs/TESTING_DESKTOP.md) for installation, updates and troubleshooting.

Build on Windows x64 with Git, uv, Python 3.14, Node.js 24 and Inno Setup 6:

```powershell
uv sync --locked --all-extras
npm --prefix frontend ci
npm --prefix frontend run build
& .\scripts\build_windows.ps1 -EmbeddedWindow -Installer
```

Player mode and executable path are available under Peach configuration → Media library and import → Player. See [external players](docs/EXTERNAL_PLAYERS.md).

### Build on macOS

The macOS and Docker entry points below are inherited from upstream. This fork's platform validation covers Windows x64.

Build a self-contained `.app` and DMG locally on macOS. Running it requires neither Python, a source checkout, nor another computer. See [the macOS build guide](docs/OPERATIONS.md#macos-独立测试包).

The release workflow generates separate Apple Silicon (`macos-arm64.dmg`) and Intel (`macos-x86_64.dmg`) test packages. Check the actual assets on [Releases](https://github.com/peach-mitao/peach/releases) for availability. Check your chip in About This Mac, open the matching image, and drag Peach into Applications. Test packages are not notarized; media tools must be installed separately.

### Run from source (Windows, macOS)

Requires Git, uv and Python 3.14 or newer. See [running from source](docs/OPERATIONS.md#从源码运行windowsmacos).

### Docker (VPS / NAS)

See [Docker deployment](docs/DOCKER.md) for setup and supported configurations.

## FAQ

- **How do I add one video?** Open Manage → Scan and collect → Add one video, select a file inside a configured media folder, and choose Add and collect. Only that video is processed.

- **Will it change my files?** Scanning and filling in details leave originals untouched. Manual organization renames or moves files; emptying the trash deletes files, and resource-sync cleanup removes empty folders. Each requires confirmation.
- **Which cloud drives work?** Drives mounted as local folders through tools such as CloudDrive2; see [cloud drive mounts](docs/CLOUDDRIVE.md). Cloud download supports 115 and PikPak, with sign-in tokens stored locally.
- **How do I watch on my phone?** Allow devices on the same network during setup, connect your phone to that network, open the address shown and set an access password.
- **Where is my data?** The Windows window defaults to `%LOCALAPPDATA%\PeachDesktop\peach-data`. Setup can select another data directory for the library, settings, posters and generated caches; original video files stay in their media directories. Close the application before moving or backing up data.
- **Does external playback produce caches?** Local files are passed directly to the external player without Peach playback conversion. Scan previews, downloaded artwork and manually selected built-in playback can still produce their own caches.
- **How are sources configured?** Use the built-in source settings for JavLibrary; an ordinary website page is not a custom JSON API. See [sourcing](docs/SOURCING.md).

## Reporting problems

Report fork-specific problems in [this repository's Issues](https://github.com/guoman630308-oss/peach-desktop/issues), with the version, steps, expected result and actual result. Hide credentials, LAN addresses and full paths in screenshots; do not upload databases or media. See the [security policy](SECURITY.md) for sensitive reports. Upstream feedback remains available in [the original repository](https://github.com/peach-mitao/peach/issues).

## Documentation

[Fork reference](docs/FORK.md) · [Windows window](docs/NATIVE_WINDOW.md) · [Operations](docs/OPERATIONS.md) · [Sourcing](docs/SOURCING.md) · [External players](docs/EXTERNAL_PLAYERS.md) · [Changelog](CHANGELOG.md)

Contributing: [Development guide](AGENTS.md) · [Testing and dependencies](docs/TESTING.md)

## Related projects

Thanks to [amane](https://github.com/sqzw-x/amane), [Gfriends](https://github.com/gfriends/gfriends), [CloudDrive2](https://www.clouddrive2.com/) and others. See the [reuse inventory](docs/REUSE.md) for full credits and details.

## Star History

The following statistics belong to the upstream Peach project.

<a href="https://www.star-history.com/?repos=peach-mitao%2Fpeach&amp;type=date&amp;logscale=&amp;releases=&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;theme=dark&amp;logscale&amp;legend=top-left">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;logscale&amp;legend=top-left">
    <img alt="Peach Star History" src="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;logscale&amp;legend=top-left">
  </picture>
</a>

## License

[AGPL-3.0-or-later](LICENSE) · Copyright (C) 2026 longmeidao. Third-party components retain their own licenses.

Fork modifications are maintained by guoman630308-oss, with a modification notice dated 2026-10-26. See [distribution requirements](docs/FORK.md#许可与再分发) for corresponding-source and remote-access obligations.
