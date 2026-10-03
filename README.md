# unplayer

**Watch any web video stream in mpv, with no ads, popups or browser.**

Paste a link and unplayer finds the real video stream inside the page's player, then
plays it in [mpv](https://mpv.io). The website never loads, so its ads, popups,
popunders, fake "close" buttons and trackers never run.

```bash
unplayer https://example.com/watch/some-live-event
```

That's it. A clean video window opens.

---

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

## Install

You need **bash**, **curl** and **mpv**. **yt-dlp** is optional, for YouTube/Twitch.

```bash
# Arch / Omarchy
sudo pacman -S mpv yt-dlp curl

# Debian / Ubuntu
sudo apt install mpv yt-dlp curl

# Fedora
sudo dnf install mpv yt-dlp curl
```

Then:

```bash
git clone https://github.com/JonTelep/unplayer.git
cd unplayer
make install        # links unplayer into ~/.local/bin
```

Make sure `~/.local/bin` is on your `PATH`. Most distros include it by default.

To remove it: `make uninstall`.

## Usage

```bash
unplayer <url>          # play the first working stream on the page
unplayer <url> 2        # play the 2nd stream found (try this if one stutters)
```

You can paste the page you're watching on, or the player/embed URL directly.

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
| YouTube link fails | Update yt-dlp (`sudo pacman -Syu yt-dlp` or your distro's equivalent). YouTube changes often. |

## Limitations

- Works when the player exposes its stream URL in the page. Players that build the URL
  in heavily obfuscated JavaScript won't work unless yt-dlp supports the site.
- DRM-protected streams (Netflix, Disney+, etc.) are not supported and never will be.
- If the source stream itself is down, no player can fix that.

## Development

```bash
make test    # offline test against a local fake player (needs python3)
```

The whole tool is one bash script: [`unplayer`](unplayer). Issues and PRs are welcome.

## Disclaimer

unplayer is a media player helper. It doesn't host, index or link to any content. Use
it only for streams you have the right to watch, and respect the terms of the sites
you visit.

## License

[MIT](LICENSE)
