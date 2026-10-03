#!/bin/bash
# Offline check: page -> iframe -> player -> master -> variant -> disguised .txt segment.
# Stream 1 is dead (HTML instead of video); stream 2 must be picked.
set -euo pipefail
cd "$(dirname "$0")"; root=$(mktemp -d); trap 'kill $srv 2>/dev/null; rm -rf "$root"' EXIT
port=8765 base=http://127.0.0.1:8765
mkdir -p "$root/s1" "$root/s2" "$root/bin"
echo "<iframe id=x src=\"$base/player1.html\"></iframe><iframe src=\"$base/player2.html\"></iframe>" > "$root/page.html"
for n in 1 2; do
  echo "<script>const source = \"$base/s$n/master.m3u8\"</script>" > "$root/player$n.html"
  printf '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=9\nhigh.m3u8\n' > "$root/s$n/master.m3u8"
  printf '#EXTM3U\n#EXTINF:6,\n/s%s/seg.txt\n' "$n" > "$root/s$n/high.m3u8"
done
echo '<html>404</html>' > "$root/s1/seg.txt"
printf 'G@\x11\x15rest-of-ts' > "$root/s2/seg.txt"
printf '#!/bin/sh\necho "MPV $*" | tr "\\n" " "; echo\n' > "$root/bin/mpv"; chmod +x "$root/bin/mpv"
python3 -m http.server -d "$root" -b 127.0.0.1 $port >/dev/null 2>&1 & srv=$!
for _ in $(seq 20); do curl -s "$base/" >/dev/null && break; sleep 0.1; done

export XDG_STATE_HOME="$root/state" PATH="$root/bin:$PATH"
../unplayer --version | grep -q '^unplayer [0-9]'
out=$(../unplayer -f "$base/page.html")
echo "$out"
grep -q 'Stream 1 dead' <<<"$out"
grep -q "Playing stream 2: $base/s2/high.m3u8" <<<"$out"
grep -q "MPV .*--referrer=$base/ .*$base/s2/high.m3u8" <<<"$out"
# default mode detaches mpv and returns; its output goes to the log
out=$(../unplayer "$base/page.html")
grep -q 'Opened in mpv' <<<"$out"
for _ in $(seq 20); do grep -q "MPV .*$base/s2/high.m3u8" "$root/state/unplayer.log" 2>/dev/null && break; sleep 0.1; done
grep -q "MPV .*$base/s2/high.m3u8" "$root/state/unplayer.log"
! ../unplayer "$base/page.html" 9 2>/dev/null   # out-of-range stream number
echo PASS
