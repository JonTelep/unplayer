# unplayer

[![CI](https://github.com/JonTelep/unplayer/actions/workflows/ci.yml/badge.svg)](https://github.com/JonTelep/unplayer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/JonTelep/unplayer)](https://github.com/JonTelep/unplayer/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Watch any web video stream in mpv, with no ads, popups or browser.**

```bash
unplayer https://example.com/watch/some-live-event
```

Paste a link and unplayer finds the real video stream inside the page's player, then
plays it in [mpv](https://mpv.io). The website never loads, so its ads, popups,
popunders, fake "close" buttons and trackers never run. A clean video window opens and
your terminal is free again.

**Platforms:** Linux and macOS (both tested in CI). Windows via WSL2.

## Install

One command. It's a single bash script, no build step:

```bash
mkdir -p ~/.local/bin && curl -fsSL https://github.com/JonTelep/unplayer/releases/latest/download/unplayer -o ~/.local/bin/unplayer && chmod +x ~/.local/bin/unplayer
```

It needs **curl** and **mpv**. **yt-dlp** is optional, for YouTube, Twitch and friends.

```bash
sudo pacman -S mpv yt-dlp        # Arch / Omarchy
sudo apt install mpv yt-dlp      # Debian / Ubuntu
sudo dnf install mpv yt-dlp      # Fedora
brew install mpv yt-dlp          # macOS
```

Make sure `~/.local/bin` is on your `PATH`. Most distros include it by default.
To remove it, delete `~/.local/bin/unplayer`.

## Why?

Lots of live-stream and video sites wrap a perfectly normal video stream in a page full
of ad scripts. They open popunder tabs, hijack your clicks and slow your browser to a
crawl. Ad blockers end up chasing them forever.

unplayer skips the whole page. It reads the page as plain text (no JavaScript ever
runs), pulls out the stream URL and hands it to mpv. You get the video and nothing
else, usually in better quality and with smoother playback than the site's own player.

## Features

- 🚫 **No ads, popups or trackers.** The site's code never executes.
- 🔍 **Finds the stream for you.** It looks in the page, embedded `<iframe>` players and
  mirror buttons.
- ✅ **Skips dead mirrors.** It tests each stream by downloading a real chunk of video
  and plays the first one that works.
- 🎞️ **Best quality automatically.** It picks the highest resolution the stream offers.
- 📺 **YouTube, Twitch and 1,800+ other sites.** If a page hides its stream, unplayer
  hands off to [yt-dlp](https://github.com/yt-dlp/yt-dlp), which skips the site's
  player and its ads.
- 🧈 **Smooth playback.** Read-ahead buffering absorbs network hiccups.

## Usage

```bash
unplayer <url>          # play the first working stream on the page
unplayer <url> 2        # play the 2nd stream found (try this if one stutters)
unplayer -f <url>       # keep mpv attached to the terminal to see its output
unplayer --help         # all options
unplayer --version
```

You can paste the page you're watching on, or the player/embed URL directly.

mpv runs in the background, so your terminal comes straight back. Close the window
(or press `q`) to stop. mpv's output goes to `~/.local/state/unplayer.log`. Check it
if a window closes unexpectedly.

### Controls

| Key / mouse | Action |
|---|---|
| `Space` / click | Pause / play |
| Mouse wheel, `↑` `↓` | Volume |
| `m` | Mute |
| `f` / double-click | Fullscreen |
| `←` `→` | Seek back / forward |
| `q` | Quit |

mpv's `1`–`8` picture keys (brightness, contrast…) are disabled, so a stray keypress
can't turn the video black.

## Web player (self-hosted)

Run unplayer as a website on your own server, so you and anyone you share the password with
can watch from any device, including phones and TVs. Paste a page URL and you land on a
plain video player: fullscreen, volume, nothing else. Each attempt is logged on `/stats`.

All you need is a machine with [Docker](https://docs.docker.com/engine/install/) and the
Compose plugin: a VPS, a home server or your desktop.

### 1. Configure

```bash
git clone https://github.com/JonTelep/unplayer.git && cd unplayer
cp .env.example .env
```

Edit `.env`:

| Setting | What it does |
|---|---|
| `PASSWORD` | Phrase that locks every page. Visitors type it once per browser (remembered for a year). **Set one on anything reachable from the internet.** Empty = open to anyone. Generate one with `openssl rand -base64 24`. |
| `DOMAIN` | Hostname for HTTPS, e.g. `streams.example.com`. Only used by `make deploy`. |
| `MAX_STREAMS` | Max streams relayed at once (default 5). Each one is an ffmpeg process plus its bandwidth. |

`.env` is gitignored, so your password stays out of git.

### 2a. Public server with HTTPS

```bash
make deploy    # docker compose up -d --build: the app + Caddy with automatic Let's Encrypt HTTPS
```

- Add a DNS **A record** for `DOMAIN` pointing at the server's IP. On Cloudflare, set it
  to **DNS only (grey cloud)**. Cloudflare's proxy isn't meant for relaying video, and it
  blocks Caddy from getting its certificate.
- Open ports 80 and 443 in the server's firewall (`sudo ufw allow 80,443/tcp`, plus your
  host's panel firewall if it has one).
- Open `https://<DOMAIN>`, enter your password and paste a link.

### 2b. Just this machine (or your home network / Tailscale)

```bash
make local     # the app only, no Caddy: http://localhost:8080
```

It only listens on localhost. To reach it from your phone over your LAN or
[Tailscale](https://tailscale.com), add a `compose.override.yml` (gitignored) with that
machine's IP and run `make local` again:

```yaml
services:
  app:
    ports: ["100.x.y.z:8080:8080"]   # your Tailscale or LAN IP
```

Then open `http://100.x.y.z:8080` on the phone.

### Running it

- **Update:** `git pull && make deploy` (or `make local`). Rebuilding also pulls the latest
  yt-dlp, which sites like YouTube need often.
- **Change the password:** edit `.env`, then `make deploy` again. Everyone has to re-enter it.
- **Logs:** `docker compose logs -f app`. **Health check:** `/healthz`.
- **Stop:** `make clean` (keeps the stats and TLS volumes; `docker compose down -v` wipes them).
- Stats live in SQLite on the `data` volume and survive rebuilds.

### How the web player plays

- The server finds the stream with `unplayer --print`. ffmpeg then relays it as HLS (fMP4)
  that browsers play directly: iPhone/Safari natively, everything else through hls.js. The
  video isn't re-encoded. The audio is re-encoded to plain AAC so it plays cleanly in every
  browser.
- Live sources often freeze for 10–20 seconds. The player waits until 30 seconds are buffered
  before it starts, and that cushion absorbs the freezes. Expect to be a bit behind live and
  a short "Buffering…" wait when a stream opens.
- If the browser blocks autoplay (Brave does by default), tap **▶ Tap to play**.
- A stream stops 5 minutes after its last viewer leaves.

### Security notes

- The password protects the pages and the "start a stream" form. Stream files (`/s/<id>/…`)
  stay open so a TV can play them over AirPlay. Their URLs are random and stop working when
  the stream ends.
- Anyone with the password can make your server fetch public web pages and relay video. The
  server refuses private and local addresses, so it can't be pointed at your own network.
  Bandwidth is on you, so keep `MAX_STREAMS` sensible for your plan.

## How it works

1. **Fetch the page** with `curl`. This is just text, so no scripts run.
2. **Find the players**: the page itself, its `<iframe>` embeds and any mirror buttons.
3. **Pull the stream URL** (an HLS `.m3u8` playlist) out of each player.
4. **Pick the best quality** and **check it's alive** by downloading one segment and
   confirming it's real video. Many hosts disguise video as `.txt` files, so it checks
   the bytes, not the label.
5. **Play it in mpv** with the headers the stream expects.
6. **Nothing found?** Hand the link to mpv + yt-dlp instead.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No working stream` | The stream may not be live yet, or every mirror is down. Try again closer to start time. |
| Stutters or freezes | Try another mirror: `unplayer <url> 2`, `3`, … |
| `429 Too Many Requests` | Close any browser tab playing the same stream. It counts against the same limit. |
| Window opens then closes | Check `~/.local/state/unplayer.log`, or rerun with `-f` to watch mpv's output live. |
| YouTube link fails | Update yt-dlp (`sudo pacman -Syu yt-dlp` or your distro's equivalent). YouTube changes often. |

## Limitations

- Works when the player exposes its stream URL in the page. Players that build the URL
  in heavily obfuscated JavaScript won't work unless yt-dlp supports the site.
- DRM-protected streams (Netflix, Disney+, etc.) are not supported and never will be.
- If the source stream itself is down, no player can fix that.

## Development

```bash
git clone https://github.com/JonTelep/unplayer.git
cd unplayer
make install   # symlinks ./unplayer into ~/.local/bin, so git pull updates it
make test      # offline test against a local fake player (needs python3)
```

The whole tool is one bash script: [`unplayer`](unplayer). Issues and PRs are welcome.

To release: bump `VERSION=` in `unplayer`, commit, then run `make release`. That tags
`vX.Y.Z`, pushes and publishes a GitHub release with the script attached.

## Disclaimer

unplayer is a media player helper. It doesn't host, index or link to any content. Use
it only for streams you have the right to watch, and respect the terms of the sites
you visit.

## License

[MIT](LICENSE)
