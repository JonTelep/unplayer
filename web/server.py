#!/usr/bin/env python3
"""unplayer web: paste a page URL, watch its stream in a clean player.

unplayer --print finds the stream; ffmpeg remuxes it (no transcode) to local HLS,
which plays natively on iOS/Safari and through hls.js everywhere else.
/w?url=<page>[&n=<mirror>] is the watch page: it answers at once and polls /j/<id> while a
background thread finds and relays the stream, so it can be bookmarked, refreshed or shared to.
Every attempt is logged to SQLite and shown on /stats.
PASSWORD (optional) locks every page behind a one-field landing page (remembered by cookie).
Stream files (/s/<random id>/) stay open so a TV can play them over AirPlay.
"""
import hashlib, html, ipaddress, json, os, re, secrets, shutil, socket, sqlite3, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

PASSWORD = os.environ.get("PASSWORD", "")
TOKEN = hashlib.sha256(PASSWORD.encode()).hexdigest()  # cookie value; changing PASSWORD logs everyone out
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "5"))  # concurrent ffmpeg relays; protects the VPS
DB = os.environ.get("DB", "/data/stats.db")
ROOT = "/tmp/streams"
IDLE = 300  # seconds without a viewer before ffmpeg is stopped
# id -> {"url", "n": mirror asked for, "k": mirror found, "proc": ffmpeg Popen, "seen": last segment request,
#        "msg": progress, "ready": playable, "err": why it failed}
streams = {}
lock = threading.Lock()

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content=dark><title>unplayer</title>
<link rel=manifest href=/manifest.json><link rel=icon href=/icon.svg>
<style>body{{margin:0;background:#000;color:#ddd;font:16px system-ui,sans-serif}}
main{{max-width:960px;margin:auto;padding:16px}} a{{color:#8cf}} nav{{margin-bottom:16px}}
input,button{{width:100%;box-sizing:border-box;padding:16px;font-size:18px;border-radius:10px;border:1px solid #444}}
input{{background:#111;color:#fff}} button{{margin-top:12px;background:#2a6df4;color:#fff;border:0;font-weight:600}} button:disabled{{opacity:.6}}
.err{{background:#3a1212;color:#fbb;padding:12px 16px;border-radius:10px;margin-bottom:16px}}
video{{width:100%;max-height:85vh;background:#000}} .url{{word-break:break-all;color:#888}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{padding:6px;border-bottom:1px solid #333;text-align:left;vertical-align:top}}
.ok{{color:#6d6}} .fail{{color:#e66}} .tiles{{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0}} .tiles b{{display:block;font-size:28px}}
.btns{{display:flex;gap:12px}} .btns a{{flex:1;text-align:center;padding:14px;border-radius:10px;background:#222;color:#fff;text-decoration:none}}
.recent a{{display:block;padding:10px 0;border-bottom:1px solid #222;word-break:break-all}}
</style></head><body><main><nav><a href=/>unplayer</a> · <a href=/stats>stats</a></nav>{}</main></body></html>"""

def form(msg="", url=""):
    """The paste page; errors re-render it with the URL still filled in so retrying is one tap."""
    err = f"<div class=err>{msg}</div>" if msg else ""
    with db() as c:
        recent = c.execute("SELECT url FROM attempts WHERE ok GROUP BY url ORDER BY max(rowid) DESC LIMIT 5").fetchall()
    links = "".join(f'<a href="/w?url={quote(u, safe="")}">{html.escape(u)}</a>' for u, in recent)
    return PAGE.format(f"""<h1>Paste a stream link</h1>{err}<form action=/w>
<input name=url type=url placeholder="https://..." value="{html.escape(url)}" required autocomplete=off
 onpaste="setTimeout(()=>this.checkValidity()&&this.form.requestSubmit())">
<button>{"Try again" if msg else "Watch"}</button></form>
{f"<h3>Recent</h3><div class=recent>{links}</div>" if links else ""}
<p class=url>Skip the paste: drag <a id=bm>▶ unplayer</a> to your bookmarks bar, then tap it on any video page.</p>
<script>bm.href="javascript:location='"+location.origin+"/w?url='+encodeURIComponent(location)"</script>""")

def gate(next="/", wrong=False):
    err = "<div class=err>Wrong phrase.</div>" if wrong else ""
    return PAGE.format(f"""<h1>unplayer</h1>{err}<form method=post action=/login>
<input type=hidden name=next value="{html.escape(next)}">
<input name=phrase type=password placeholder="Phrase" required autofocus><button>Enter</button></form>""")

PLAYER = """<style>.spin{display:inline-block;width:14px;height:14px;margin-right:8px;vertical-align:-2px;border:2px solid #555;border-top-color:#8cf;border-radius:50%%;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(1turn)}}</style>
<video id=v controls playsinline></video><p id=msg></p><button id=snd hidden>🔊 Tap for sound</button>
<div class=btns id=btns hidden><a id=retry>↻ Restart</a><a id=next hidden>Next mirror →</a></div><p class=url id=u></p>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js"></script>
<script>const J=%s,v=document.getElementById('v'),msg=document.getElementById('msg'),snd=document.getElementById('snd'),
 src='/s/'+J.sid+'/index.m3u8',w='/w?url='+encodeURIComponent(J.url);
u.textContent=J.url;retry.href=w;let dead=false,started=false;
function say(t,spin){msg.hidden=false;msg.textContent=t;if(spin)msg.prepend(Object.assign(document.createElement('span'),{className:'spin'}))}
// the server finds and relays the stream in the background; poll for progress, then keep polling to notice a dead source
async function poll(){let j;try{j=await(await fetch('/j/'+J.sid)).json()}catch{return setTimeout(poll,2000)}
 if(j.k){next.href=w+'&n='+(j.k+1);next.hidden=false}
 if(j.state=='finding'){say(j.msg,1);return setTimeout(poll,1000)}
 btns.hidden=false;
 if(j.state=='ready'){if(!started){started=true;play()}return setTimeout(poll,5000)}
 dead=true;say(j.msg)}
poll();
function play(){
// hls.js first: Chromium now claims native HLS but stalls on live relays; native is for iPhone (no MSE)
// sources stall for 10-20s at a time and playing into an empty buffer garbles the audio,
// so hold off starting until 30s is buffered ahead; that cushion then soaks up the stalls.
// fatal error: start over with a fresh player after 2s (recovering in place could loop ~50x a second)
function load(){const h=new Hls({liveDurationInfinity:true,maxBufferLength:60});h.loadSource(src);h.attachMedia(v);
 h.on(Hls.Events.ERROR,(e,d)=>{if(d.fatal){h.destroy();if(!dead)setTimeout(()=>{load();v.play().catch(()=>{})},2000)}})}
if(Hls.isSupported())load();else v.src=src;
// browsers block autoplay with sound: try it, else start muted and offer one big unmute tap;
// some (Brave) block muted autoplay too, so then the button starts playback
snd.onclick=()=>{v.muted=false;v.play();snd.hidden=true};
v.onvolumechange=()=>{if(!v.muted)snd.hidden=true};
// start once 30s is buffered (or after 60s, in case a browser won't buffer while paused)
let tries=0;const wait=setInterval(()=>{const b=v.buffered,ahead=b.length?b.end(b.length-1)-v.currentTime:0;
 if(!dead)say(`Buffering for smooth playback… ${Math.min(30,ahead|0)}/30s`,1);
 if(ahead>=30||++tries>60){clearInterval(wait);
  v.play().catch(()=>{v.muted=true;v.play().catch(()=>{snd.textContent='▶ Tap to play'});snd.hidden=false})}},1000);
v.onplaying=()=>{clearInterval(wait);if(!dead)msg.hidden=true}}</script>"""

ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#2a6df4"/>'
        b'<path d="M38 28v44l36-22z" fill="#fff"/></svg>')
# installed as an app (Android), unplayer shows up in the share menu and opens /w with the shared link
MANIFEST = json.dumps({"name": "unplayer", "start_url": "/", "display": "standalone", "background_color": "#000",
                       "theme_color": "#000", "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
                       "share_target": {"action": "/w", "method": "GET",
                                        "params": {"url": "url", "text": "text", "title": "title"}}}).encode()

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


def watch(url, n):
    """Id of the relay for this page and mirror: a live or starting one is shared, else a new one starts."""
    with lock:
        for sid, s in streams.items():
            if (s["url"], s["n"]) == (url, n) and not s["err"] and (s["proc"] is None or s["proc"].poll() is None):
                return sid
        if sum(not s["err"] for s in streams.values()) >= MAX_STREAMS:
            return None
        sid = secrets.token_hex(16)
        streams[sid] = {"url": url, "n": n, "k": None, "proc": None, "seen": time.time(),
                        "msg": "Fetching the page…", "ready": False, "err": ""}
    threading.Thread(target=start, args=(sid,), daemon=True).start()
    return sid


def start(sid):
    s, t = streams[sid], time.time()
    try:
        relay(sid, s)
        record(s["url"], 1, "", time.time() - t)
    except Exception as e:
        s["err"] = str(e) or "Something went wrong."
        record(s["url"], 0, s["err"], time.time() - t)
        with lock:
            if s["proc"]:
                s["proc"].kill()


def relay(sid, s):
    """Find the stream and start remuxing it, reporting progress in s; raises with a reason."""
    p = subprocess.Popen(["unplayer", "--print", s["url"]] + ([str(s["n"])] if s["n"] else []),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timer = threading.Timer(180, p.kill)
    timer.start()
    last = ""
    for line in p.stderr:  # unplayer's progress, shown live on the page
        last = line.strip() or last
        if m := re.match(r"Playing stream (\d+)", last):
            s["k"] = int(m[1])
            last = f"Found stream {m[1]}, starting the relay…"
        s["msg"] = last
    out = p.stdout.read()
    p.wait()
    timer.cancel()
    if p.returncode == -9:
        raise RuntimeError("Gave up after 3 minutes looking for a stream.")
    if p.returncode or not out.strip():
        raise RuntimeError(last or "No working stream.")
    src, ref, ua = (out.splitlines() + ["", "", ""])[:3]
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
    with lock:
        if sid not in streams:  # reaped while searching
            return
        proc = s["proc"] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
    for i in range(60):  # wait for a few segments so the player starts with some buffer
        if proc.poll() is not None:
            break
        if os.path.exists(f"{out}/index.m3u8") and open(f"{out}/index.m3u8").read().count("#EXTINF") >= 3:
            break
        s["msg"] = f"Starting the relay… {i // 2}s"
        time.sleep(0.5)
    if proc.poll() is not None or not os.path.exists(f"{out}/index.m3u8"):
        raise RuntimeError("Found a stream but couldn't relay it. Try the next mirror.")
    s["ready"] = True  # slow source: play what we have


def status(sid):
    s = streams.get(sid)
    if not s or (s["proc"] and s["proc"].poll() is not None and not s["err"]):
        return {"state": "ended", "msg": "The stream stopped (the source went down, or nobody watched for a while)."}
    if s["err"]:
        return {"state": "failed", "msg": s["err"], "k": s["k"]}
    return {"state": "ready" if s["ready"] else "finding", "msg": s["msg"], "k": s["k"]}


def stop(sid):
    with lock:
        s = streams.pop(sid, None)
    if s and s["proc"]:
        s["proc"].kill()
        s["proc"].wait()
    shutil.rmtree(f"{ROOT}/{sid}", ignore_errors=True)


def reaper():
    while True:
        time.sleep(30)
        with lock:
            idle = [k for k, s in streams.items()
                    if time.time() - s["seen"] > IDLE or (s["proc"] and s["proc"].poll() is not None)]
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
        path, _, qs = self.path.partition("?")
        q = {k: v[0] for k, v in parse_qs(qs).items()}
        if path == "/":
            if q.get("url"):
                self.send(303, b"", headers=[("Location", f"/w?{qs}")])
            elif self.authed():
                self.send(200, form().encode())
        elif path == "/w":
            if self.authed():
                self.watch(q)
        elif path == "/stats":
            if self.authed():
                self.send(200, stats_page().encode())
        elif path == "/healthz":
            self.send(200, b"ok", "text/plain")
        elif path == "/manifest.json":  # no auth: browsers fetch these without cookies
            self.send(200, MANIFEST, "application/manifest+json")
        elif path == "/icon.svg":
            self.send(200, ICON, "image/svg+xml", [("Cache-Control", "max-age=86400")])
        elif m := re.fullmatch(r"/j/([0-9a-f]{32})", path):
            self.send(200, json.dumps(status(m[1])).encode(), "application/json")
        elif (m := re.fullmatch(r"/s/([0-9a-f]{32})/([\w-]+(\.m3u8|\.m4s|\.mp4))", path)) and m[1] in streams:
            streams[m[1]]["seen"] = time.time()
            try:
                body = open(f"{ROOT}/{m[1]}/{m[2]}", "rb").read()
            except FileNotFoundError:  # segment already rotated out
                return self.send(404, b"gone", "text/plain")
            self.send(200, body, TYPES[m[3]], [("Cache-Control", "no-cache")])
        else:
            self.send(404, b"not found", "text/plain")

    def watch(self, q):
        # share menus often put the link in text ("Look at this https://...") rather than url
        url = q.get("url") or (re.search(r"https?://\S+", q.get("text", "")) or [""])[0]
        n = int(q["n"]) if q.get("n", "").isdigit() and int(q["n"]) > 0 else None
        if not public_url(url):
            record(url, 0, "not a public URL", 0)
            return self.page(400, "That doesn't look like a link. Paste the full address, starting with https://", url)
        # ponytail: the cap counts relays, not searches in progress; add a search semaphore if people hammer it
        if not (sid := watch(url, n)):
            record(url, 0, "busy", 0)
            return self.page(503, f"All {MAX_STREAMS} stream slots are in use. Try again in a few minutes.", url)
        data = json.dumps({"sid": sid, "url": url}).replace("</", "<\\/")
        self.send(200, PAGE.format(PLAYER % data).encode())

    do_HEAD = do_GET

    def do_POST(self):
        body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 8192)).decode(errors="replace")
        if self.path == "/login":
            return self.login(body)
        self.send(404, b"not found", "text/plain")


if __name__ == "__main__":
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db().close()
    threading.Thread(target=reaper, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
