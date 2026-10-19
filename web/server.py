#!/usr/bin/env python3
"""unplayer web: paste a page URL, watch its stream in a clean player.

unplayer --print finds the stream; ffmpeg remuxes it (no transcode) to local HLS,
which plays natively on iOS/Safari and through hls.js everywhere else.
Every attempt is logged to SQLite and shown on /stats.
PASSWORD (optional) locks every page behind a one-field landing page (remembered by cookie).
Stream files (/s/<random id>/) stay open so a TV can play them over AirPlay.
"""
import hashlib, html, ipaddress, os, re, secrets, shutil, socket, sqlite3, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PASSWORD = os.environ.get("PASSWORD", "")
TOKEN = hashlib.sha256(PASSWORD.encode()).hexdigest()  # cookie value; changing PASSWORD logs everyone out
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "5"))  # concurrent ffmpeg relays; protects the VPS
DB = os.environ.get("DB", "/data/stats.db")
ROOT = "/tmp/streams"
IDLE = 300  # seconds without a viewer before ffmpeg is stopped
streams = {}  # id -> {"proc": Popen, "seen": last request time, "url": page url}
lock = threading.Lock()

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content=dark><title>unplayer</title>
<style>body{{margin:0;background:#000;color:#ddd;font:16px system-ui,sans-serif}}
main{{max-width:960px;margin:auto;padding:16px}} a{{color:#8cf}} nav{{margin-bottom:16px}}
input,button{{width:100%;box-sizing:border-box;padding:16px;font-size:18px;border-radius:10px;border:1px solid #444}}
input{{background:#111;color:#fff}} button{{margin-top:12px;background:#2a6df4;color:#fff;border:0;font-weight:600}} button:disabled{{opacity:.6}}
.err{{background:#3a1212;color:#fbb;padding:12px 16px;border-radius:10px;margin-bottom:16px}}
video{{width:100%;max-height:85vh;background:#000}} .url{{word-break:break-all;color:#888}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{padding:6px;border-bottom:1px solid #333;text-align:left;vertical-align:top}}
.ok{{color:#6d6}} .fail{{color:#e66}} .tiles{{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0}} .tiles b{{display:block;font-size:28px}}
</style></head><body><main><nav><a href=/>unplayer</a> · <a href=/stats>stats</a></nav>{}</main></body></html>"""

def form(msg="", url=""):
    """The paste page; errors re-render it with the URL still filled in so retrying is one tap."""
    err = f"<div class=err>{msg}</div>" if msg else ""
    return PAGE.format(f"""<h1>Paste a stream link</h1>{err}<form method=post action=/new
onsubmit="this.querySelector('button').disabled=true;this.querySelector('button').textContent='Finding stream… (up to a minute)'">
<input name=url type=url placeholder="https://..." value="{html.escape(url)}" required autocomplete=off>
<button>{"Try again" if msg else "Watch"}</button></form>""")

def gate(next="/", wrong=False):
    err = "<div class=err>Wrong phrase.</div>" if wrong else ""
    return PAGE.format(f"""<h1>unplayer</h1>{err}<form method=post action=/login>
<input type=hidden name=next value="{html.escape(next)}">
<input name=phrase type=password placeholder="Phrase" required autofocus><button>Enter</button></form>""")

PLAYER = """<style>.spin{display:inline-block;width:14px;height:14px;margin-right:8px;vertical-align:-2px;border:2px solid #555;border-top-color:#8cf;border-radius:50%%;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(1turn)}}</style>
<video id=v controls playsinline></video><p id=msg><span class=spin></span>Loading stream…</p><button id=snd hidden>🔊 Tap for sound</button><p class=url>%s</p>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js"></script>
<script>const v=document.getElementById('v'),src='/s/%s/index.m3u8';
// hls.js first: Chromium now claims native HLS but stalls on live relays; native is for iPhone (no MSE)
// sources stall for 10-20s at a time and playing into an empty buffer garbles the audio,
// so hold off starting until 30s is buffered ahead; that cushion then soaks up the stalls.
// fatal error: start over with a fresh player after 2s (recovering in place could loop ~50x a second)
function load(){const h=new Hls({liveDurationInfinity:true,maxBufferLength:60});h.loadSource(src);h.attachMedia(v);
 h.on(Hls.Events.ERROR,(e,d)=>{if(d.fatal){h.destroy();setTimeout(()=>{load();v.play().catch(()=>{})},2000)}})}
if(Hls.isSupported())load();else v.src=src;
// browsers block autoplay with sound: try it, else start muted and offer one big unmute tap;
// some (Brave) block muted autoplay too, so then the button starts playback
const snd=document.getElementById('snd'),msg=document.getElementById('msg');
snd.onclick=()=>{v.muted=false;v.play();snd.hidden=true};
v.onvolumechange=()=>{if(!v.muted)snd.hidden=true};
// start once 30s is buffered (or after 60s, in case a browser won't buffer while paused)
let tries=0;const wait=setInterval(()=>{const b=v.buffered,ahead=b.length?b.end(b.length-1)-v.currentTime:0;
 msg.lastChild.textContent=`Buffering for smooth playback… ${Math.min(30,ahead|0)}/30s`;
 if(ahead>=30||++tries>60){clearInterval(wait);
  v.play().catch(()=>{v.muted=true;v.play().catch(()=>{snd.textContent='▶ Tap to play'});snd.hidden=false})}},1000);
v.onplaying=()=>{clearInterval(wait);msg.hidden=true};</script>"""

TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".m4s": "video/iso.segment", ".mp4": "video/mp4"}


def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS attempts (ts INTEGER, url TEXT, ok INTEGER, detail TEXT, secs REAL)")
    return c


def record(url, ok, detail, secs):
    with db() as c:
        c.execute("INSERT INTO attempts VALUES (?,?,?,?,?)", (int(time.time()), url, ok, detail, round(secs, 1)))


def public_url(url):
    """http(s) URL whose host resolves only to public IPs (no fetching the VPS's own network)."""
    if not re.fullmatch(r"https?://\S+", url):
        return False
    try:
        host = urlsplit(url).hostname
        return bool(host) and all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(host, None))
    except (OSError, ValueError):
        return False


def start(url):
    """Find the stream and start remuxing it. Returns the id, or raises with a reason."""
    r = subprocess.run(["unplayer", "--print", url], capture_output=True, text=True, timeout=180)
    if r.returncode or not r.stdout.strip():
        raise RuntimeError((r.stderr.strip().splitlines() or ["No working stream."])[-1])
    src, ref, ua = (r.stdout.splitlines() + ["", "", ""])[:3]
    sid = secrets.token_hex(16)
    out = f"{ROOT}/{sid}"
    os.makedirs(out)
    # -re: read at play speed, or a VOD gets remuxed and rotated out before anyone watches it;
    # but grab the first 15s (a live source's whole window) at once so the player starts with a cushion
    cmd = ["ffmpeg", "-loglevel", "error", "-re", "-readrate_initial_burst", "15", "-user_agent", ua]
    if ref:
        cmd += ["-referer", ref, "-headers", f"Origin: {ref.rstrip('/')}\r\n"]
    # HLS input (anything unplayer found itself has a referer; playlist URLs may lack .m3u8):
    # hosts disguise segments as .txt, which ffmpeg refuses by default
    if ref or ".m3u8" in src:
        cmd += ["-allowed_extensions", "ALL", "-allowed_segment_extensions", "ALL", "-extension_picky", "0"]
    # video as-is; audio re-encoded with its timestamps smoothed over source hiccups.
    # fMP4 segments, not .ts: browsers play them directly, while hls.js rewrites .ts audio itself
    # (and labels plain AAC as HE-AAC on Chromium), which came out deep and slowed down in Brave
    cmd += ["-i", src, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", "aresample=async=1",
            "-f", "hls", "-hls_segment_type", "fmp4", "-hls_time", "4", "-hls_list_size", "90",
            "-hls_flags", "delete_segments+omit_endlist", f"{out}/index.m3u8"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
    with lock:
        streams[sid] = {"proc": proc, "seen": time.time(), "url": url}
    for _ in range(60):  # wait for a few segments so the player starts with some buffer
        if proc.poll() is not None:
            break
        if os.path.exists(f"{out}/index.m3u8") and open(f"{out}/index.m3u8").read().count("#EXTINF") >= 3:
            return sid
        time.sleep(0.5)
    if proc.poll() is None and os.path.exists(f"{out}/index.m3u8"):
        return sid  # slow source: play what we have
    stop(sid)
    raise RuntimeError("Found a stream but couldn't relay it.")


def stop(sid):
    with lock:
        s = streams.pop(sid, None)
    if s:
        s["proc"].kill()
        s["proc"].wait()
    shutil.rmtree(f"{ROOT}/{sid}", ignore_errors=True)


def reaper():
    while True:
        time.sleep(30)
        with lock:
            idle = [k for k, s in streams.items() if time.time() - s["seen"] > IDLE or s["proc"].poll() is not None]
        for sid in idle:
            stop(sid)


def stats_page():
    with db() as c:
        total, ok = c.execute("SELECT count(*), coalesce(sum(ok),0) FROM attempts").fetchone()
        day = c.execute("SELECT count(*) FROM attempts WHERE ts > ?", (time.time() - 86400,)).fetchone()[0]
        rows = c.execute("SELECT ts, url, ok, detail, secs FROM attempts ORDER BY rowid DESC LIMIT 200").fetchall()
    rate = f"{100 * ok // total}%" if total else "–"
    tiles = (f"<div class=tiles><div><b>{total}</b>attempts</div><div><b>{rate}</b>success</div>"
             f"<div><b>{day}</b>last 24h</div><div><b>{len(streams)}</b>live now</div></div>")
    trs = "".join(
        f"<tr><td>{time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))}</td><td class=url>{html.escape(u)}</td>"
        f"<td class={'ok' if k else 'fail'}>{'ok' if k else 'failed'}</td><td>{html.escape(d)}</td><td>{s}s</td></tr>"
        for ts, u, k, d, s in rows)
    return PAGE.format(f"<h1>Stats</h1>{tiles}<table><tr><th>When (UTC)</th><th>URL</th><th>Result</th>"
                       f"<th>Detail</th><th>Took</th></tr>{trs}</table>")


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def page(self, code, msg, url=""):
        self.send(code, form(msg, url).encode())

    def authed(self):
        """True if unlocked; otherwise answers with the landing page."""
        if not PASSWORD:
            return True
        cookie = re.search(r"(?:^|;\s*)k=(\w+)", self.headers.get("Cookie", ""))
        if cookie and secrets.compare_digest(cookie[1], TOKEN):
            return True
        self.send(401, gate(self.path if self.command == "GET" else "/").encode())
        return False

    def login(self, body):
        form = parse_qs(body)
        nxt = form.get("next", ["/"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):  # only same-site redirects
            nxt = "/"
        if not secrets.compare_digest(form.get("phrase", [""])[0].encode(), PASSWORD.encode()):
            time.sleep(1)  # slows guessing
            return self.send(401, gate(nxt, True).encode())
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        self.send(303, b"", headers=[("Location", nxt),
                                     ("Set-Cookie", f"k={TOKEN}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")])

    def do_GET(self):
        if self.path == "/":
            if self.authed():
                self.send(200, form().encode())
        elif self.path == "/stats":
            if self.authed():
                self.send(200, stats_page().encode())
        elif self.path == "/healthz":
            self.send(200, b"ok", "text/plain")
        elif m := re.fullmatch(r"/w/([0-9a-f]{32})", self.path):
            if not self.authed():
                return
            if s := streams.get(m[1]):
                self.send(200, PAGE.format(PLAYER % (html.escape(s["url"]), m[1])).encode())
            else:
                self.page(404, "That stream ended. Paste the link again to restart it.")
        elif (m := re.fullmatch(r"/s/([0-9a-f]{32})/([\w-]+(\.m3u8|\.m4s|\.mp4))", self.path)) and m[1] in streams:
            streams[m[1]]["seen"] = time.time()
            try:
                body = open(f"{ROOT}/{m[1]}/{m[2]}", "rb").read()
            except FileNotFoundError:  # segment already rotated out
                return self.send(404, b"gone", "text/plain")
            self.send(200, body, TYPES[m[3]], [("Cache-Control", "no-cache")])
        else:
            self.send(404, b"not found", "text/plain")

    do_HEAD = do_GET

    def do_POST(self):
        body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 8192)).decode(errors="replace")
        if self.path == "/login":
            return self.login(body)
        if self.path != "/new" or not self.authed():
            return
        url = parse_qs(body).get("url", [""])[0].strip()
        if not public_url(url):
            record(url, 0, "not a public URL", 0)
            return self.page(400, "That doesn't look like a link. Paste the full address, starting with https://", url)
        # ponytail: the cap counts relays, not searches in progress; add a search semaphore if people hammer it
        if len(streams) >= MAX_STREAMS:
            record(url, 0, "busy", 0)
            return self.page(503, f"All {MAX_STREAMS} stream slots are in use. Try again in a few minutes.", url)
        t = time.time()
        try:
            sid = start(url)
        except Exception as e:
            record(url, 0, str(e), time.time() - t)
            return self.page(502, html.escape(str(e)), url)
        record(url, 1, "", time.time() - t)
        self.send(303, b"", headers=[("Location", f"/w/{sid}")])


if __name__ == "__main__":
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db().close()
    threading.Thread(target=reaper, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
