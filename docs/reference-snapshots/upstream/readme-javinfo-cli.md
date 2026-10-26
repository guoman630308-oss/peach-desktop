# javinfo CLI

Production-oriented Rust CLI for [javinfo](https://javinfo.dev) — API client plus local tools.

Docs: [docs.javinfo.dev](https://docs.javinfo.dev) · API: `https://api.javinfo.dev`

AI / coding agents: see [AGENTS.md](AGENTS.md) (`CLAUDE.md` links to the same file).

## Install

### One-line installer (recommended)

```bash
curl -fsSL https://javinfo.dev/install.sh | bash
```

Until that URL is live, use the script from this repo:

```bash
curl -fsSL https://raw.githubusercontent.com/javinfo/cli/main/install.sh | bash
```

The script detects your OS/arch, downloads the matching GitHub Release asset, verifies `sha256sums.txt`, and installs to `~/.local/bin/javinfo`.

| Env | Default | Meaning |
|-----|---------|---------|
| `VERSION` | latest | Pin a release, e.g. `VERSION=0.1.0` |
| `INSTALL_DIR` | `~/.local/bin` | Where to place the `javinfo` binary |
| `REPO` | `javinfo/cli` | GitHub `owner/repo` (forks) |
| `NO_VERIFY` | `0` | Set `1` to skip checksum verification |

```bash
# examples
VERSION=0.1.0 curl -fsSL https://javinfo.dev/install.sh | bash
INSTALL_DIR=/usr/local/bin curl -fsSL https://javinfo.dev/install.sh | bash
```

Ensure the install dir is on your `PATH`.

### Prebuilt binaries (manual)

GitHub Releases ship stripped builds:

| Platform | Archive |
|----------|---------|
| Linux x86_64 | `javinfo-<ver>-x86_64-unknown-linux-gnu.tar.gz` |
| Linux arm64 | `javinfo-<ver>-aarch64-unknown-linux-gnu.tar.gz` |
| macOS Apple Silicon | `javinfo-<ver>-aarch64-apple-darwin.tar.gz` |

Not shipped as prebuilts (build from source if needed):

- **macOS Intel** — free GitHub Intel runners retired
- **Windows** — `serve` control uses Unix domain sockets (Unix/macOS only for now)

Browse [Releases](https://github.com/javinfo/cli/releases). Each release includes `sha256sums.txt`.

```bash
# Linux x86_64 example (replace VERSION)
VERSION=0.1.0
mkdir -p ~/.local/bin
curl -fsSL \
  "https://github.com/javinfo/cli/releases/download/v${VERSION}/javinfo-${VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
  | tar -xz -C ~/.local/bin
chmod +x ~/.local/bin/javinfo
javinfo --version
```

### From source

Requires Rust stable (1.85+ recommended; built with 1.97):

```bash
# if needed
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

cargo build --release
# binary: target/release/javinfo

# install onto PATH (~/.cargo/bin/javinfo; re-run with --force to update)
cargo install --path . --force
```

Ensure Cargo’s bin dir is on your `PATH` (`export PATH="$HOME/.cargo/bin:$PATH"` or `source "$HOME/.cargo/env"`). Without installing, run `./target/release/javinfo …` or `cargo run --release -- …`.

### Release process (maintainers)

1. Bump `version` in `Cargo.toml` (must match the git tag without the `v` prefix).
2. Commit, then tag and push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

3. The [Release](.github/workflows/release.yml) workflow builds multi-platform binaries and publishes the GitHub Release.

## Auth

Get a key (`jvi_…`) from [app.javinfo.dev](https://app.javinfo.dev), then:

```bash
javinfo login
# or non-interactive
javinfo login --api-key jvi_your_key_here
# or
export JAVINFO_API_KEY=jvi_your_key_here
```

Config is stored at **`~/.config/javinfo/config.toml`** (or `$XDG_CONFIG_HOME/javinfo/config.toml`), mode `0600`:

```toml
api_key = "jvi_..."
# base_url = "https://api.javinfo.dev"   # optional override

[players]
vlc = "/usr/bin/vlc"
mpv = "/usr/bin/mpv"
```

`javinfo login` also scans `PATH` for video clients (`vlc`, `mpv`, `ffplay`, `iina`, `celluloid`, `smplayer`) and writes absolute paths under `[players]`. Use `--no-scan-players` to leave that table unchanged.

Key resolution order:

1. `JAVINFO_API_KEY` env
2. config file
3. error → run `javinfo login`

Requests send the key as the `x-javinfo-key` header.

## Commands

### `movie`

Look up a title via `/movie`. **No provider pin by default** (API waterfall). Optionally restrict with `--providers`.

```bash
javinfo movie EBOD-391
javinfo movie SSIS-001 --json
javinfo movie EBOD-391 --providers missav
javinfo movie CAWD-001 --providers fanza,missav
```

Human output (streams only when the answering provider has them, e.g. missav):

```text
EBOD-391  source=missav  …
master   https://surrit.com/…/playlist.m3u8
variant  https://surrit.com/…/1280x720/video.m3u8
…
```

### `nfo`

Scan a folder (or one file), look up each DVD code, write Kodi/Jellyfin NFO + artwork next to the video. Does **not** rename or move files unless you pass `--write`.

```bash
javinfo nfo ~/Videos/JAV
javinfo nfo ~/Videos/SSIS-001.mkv
javinfo nfo ~/Videos/JAV --dry-run
javinfo nfo ~/Videos/JAV --write              # folder + rename to SSIS-001/SSIS-001.mp4
javinfo nfo ~/Videos/JAV --write torrent      # also torrent/magnet files; pins javdb
javinfo nfo ~/Videos/JAV --provider missav    # warns: metadata may be incomplete
```

Default `/movie` providers are `fanza,dmm`. `--write torrent` forces `javdb` and warns. Existing `*.nfo` files are skipped (`--force` to refresh). Trailer/sample is downloaded when the API has a URL (`--no-trailer` to skip). Up to 10 HTTP requests run at once.

Sidecars (in-place): `{stem}.nfo`, `{stem}-poster.jpg`, `{stem}-fanart.jpg`, `{stem}-thumb.jpg`, `{stem}-trailer.mp4`, `extrafanart/`.

### `login`

Save API key to local config and scan `PATH` for video clients (see Auth above).

### `status`

Local overview: API key (source + masked preview), configured players, and serve daemon.

```bash
javinfo status
javinfo status --json
```

Does not call the API or print the full key. Daemon section is non-fatal when the process is down (unlike `javinfo serve status`, which errors if nothing is running).

### `serve` / `open`

LAN HLS re-stream **daemon** with **opaque session tokens**. Resolve goes through the javinfo API — `missav` first, then `sextb` when missav has no match; the proxy sends player-shaped headers, picks a height-capped variant, and undisguises segments for ffmpeg-based players. The Referer follows the fetched host: missav's CDN requires its own origin, while sextb's answers a foreign one with `429`, so those fetches carry none.

Both upstreams hide MPEG-TS behind an image, differently, so the handling is picked from the master URL (`serve/flavor.rs` — anything unrecognised takes the missav path):

| Source | Segments | Fix applied |
|---|---|---|
| **missav** | `videoN.jpeg` under one CDN directory, bytes already clean TS | relabel `.jpeg`/`.jpg` → `.ts` |
| **sextb** | absolute signed `lh3.googleusercontent.com/d/…=d` URLs, each served as a real PNG | rewrite to local `videoN.ts` + keep a URL table on the session, and strip the PNG header (941 bytes) off each body |

Without the strip, ffmpeg rejects the whole stream (`Invalid data found when processing input`) — `-allowed_extensions ALL` does not help, since the segment URL has no media extension to allow and the PNG prefix is a second, separate blocker.

sextb also hands out `turboplays.click/t/<id>` links, which are embed *pages*: the daemon lifts the `.m3u8` out of the HTML and re-reads the flavor from that URL. Some sextb links are simply dead upstream (`404`, or a variant CDN with no DNS) — nothing local can rescue those, so the session falls back to missav when the API has it there.

```bash
# One-shot: auto-starts the daemon if needed, prints a play URL
javinfo open EBOD-391
javinfo open EBOD-391 --with vlc             # launch configured player
javinfo open EBOD-391 --with mpv
javinfo serve open EBOD-391 --ttl 2          # optional TTL (hours)
javinfo open SSIS-001 --provider sextb       # ask one provider only
javinfo open EBOD-391 --json                 # machine-readable

# Daemon lifecycle
javinfo serve                # foreground (logs to terminal)
javinfo serve --daemon       # or: javinfo serve start
javinfo serve status
javinfo serve stop
```

Example `open` output:

```text
EBOD-391  …
play   http://<lan-ip>:8787/s/<token>/EBOD-391.m3u8
meta   http://<lan-ip>:8787/s/<token>/meta.json
poster https://…
with   vlc (/usr/bin/vlc)
```

`--with` uses paths from `[players]` in config (filled by `login`); falls back to `PATH` if the entry is missing/stale. You can also pass an absolute binary path. The playlist path ends with `{code}.m3u8` so players show the DVD code as the media title. Sessions do not expire by default; pass `--ttl <hours>` to override. Codes are no longer accepted as auth — only tokens issued by `open`.

| Flag | Env | Default |
|------|-----|---------|
| `--port` / `-p` | `PORT` | `8787` |
| `--max-height` | `MAX_HEIGHT` | `1080` |
| `--bind` | — | `0.0.0.0` |
| `--ttl` (`open`) | — | none (no expiry) |
| `--provider` (`open`) | — | try `missav` then `sextb` |
| `--with` (`open`) | — | print URL only |
| `--no-start` (`open`) | — | auto-start daemon |

Runtime files (socket / pid / log): `$XDG_RUNTIME_DIR/javinfo/` (fallback `/tmp/javinfo-$UID/`).

Global: `-v` / `-vv` for debug/trace logs (`RUST_LOG` also works).

## Layout

```text
src/
  main.rs           # entry, tracing, dispatch
  cli.rs            # clap root + subcommands
  config.rs         # ~/.config/javinfo
  players.rs        # detect/launch vlc, mpv, …
  api/              # api.javinfo.dev client
  commands/         # login, movie, open, serve, status
  serve/            # HLS proxy domain logic
  util/             # shared helpers
```

Add a future command by: new file under `commands/`, new `Commands` variant in `cli.rs`, dispatch arm in `main.rs`.

Agent-oriented architecture notes and conventions: [AGENTS.md](AGENTS.md).
