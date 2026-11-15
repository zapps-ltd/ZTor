"""Web remote control: a small password protected page for phones and browsers.

Off by default. Listens on this computer only unless other devices are allowed.
Plain HTML forms, no scripts, so it works with any screen reader on any device.
Sessions use an HttpOnly, SameSite=Strict cookie, and every form carries a
per session token. Repeated wrong passwords lock the address out for a minute.
"""

import hashlib
import hmac
import html
import json
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_NAME
from . import format as fmt

SESSION_SECONDS = 12 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 60
MAX_BODY = 64 * 1024
COOKIE = "mt_session"
ITERATIONS = 200000

# One layout for every screen: cards in a single column on a phone, a grid of
# two or three columns in a full size browser. Light and dark follow the device.
STYLE = """
:root{color-scheme:light dark;--bg:#f4f6f8;--card:#fff;--text:#1b1f24;
--muted:#57606a;--line:#d0d7de;--accent:#0b63ce;--ok:#1a7f37;--warn:#9a6700;
--bad:#cf222e;--track:#e3e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;
--text:#e6edf3;--muted:#9da7b3;--line:#30363d;--accent:#58a6ff;--ok:#3fb950;
--warn:#d29922;--bad:#f85149;--track:#30363d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:76rem;margin:0 auto;padding:1rem}
h1{font-size:1.5rem;margin:.25rem 0}
h2{font-size:1.15rem;margin:1.5rem 0 .6rem}
.top{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:center;
justify-content:space-between}
.summary{color:var(--muted);margin:.25rem 0 0}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:1rem}
.alert{background:var(--card);border:1px solid var(--accent);border-radius:8px;
padding:.6rem .9rem;margin:.75rem 0}
form.add{display:flex;flex-wrap:wrap;gap:.5rem}
form.add label,form.login label{width:100%;font-weight:600}
input[type=text],input[type=password]{flex:1 1 16rem;min-width:0;font:inherit;
padding:.6rem .75rem;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--text)}
button,a.button{font:inherit;font-weight:600;min-height:44px;
padding:.5rem 1rem;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--text);cursor:pointer;text-decoration:none;
display:inline-flex;align-items:center;justify-content:center}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.danger{color:var(--bad);border-color:var(--bad)}
button:focus-visible,a:focus-visible,input:focus-visible{
outline:3px solid var(--accent);outline-offset:2px}
ul.torrents{list-style:none;margin:0;padding:0;display:grid;gap:.75rem;
grid-template-columns:repeat(auto-fill,minmax(min(100%,22rem),1fr))}
.torrent h3{font-size:1.05rem;margin:0 0 .4rem;overflow-wrap:anywhere}
.badge{display:inline-block;font-size:.85rem;font-weight:600;
padding:.05rem .6rem;border-radius:999px;border:1px solid currentColor}
.pct{margin-left:.5rem;color:var(--muted)}
.downloading .badge{color:var(--accent)}.seeding .badge{color:var(--ok)}
.paused .badge{color:var(--muted)}.error .badge{color:var(--bad)}
.checking .badge{color:var(--warn)}
.track{height:8px;background:var(--track);border-radius:999px;margin:.6rem 0;
overflow:hidden}
.fill{height:100%;background:var(--accent)}
.seeding .fill{background:var(--ok)}.paused .fill{background:var(--muted)}
.error .fill{background:var(--bad)}.checking .fill{background:var(--warn)}
dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(6.5rem,1fr));
gap:.25rem .75rem;margin:.5rem 0 .9rem}
dt{color:var(--muted);font-size:.85rem}
dd{margin:0;font-weight:600}
.actions{display:flex;flex-wrap:wrap;gap:.5rem}
.actions button{flex:1 1 5.5rem}
.empty{color:var(--muted)}
.signout{margin-top:1.5rem}
.login-box{max-width:26rem;margin:12vh auto 0}
form.login{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.75rem}
"""


def _bucket(row):
    """(css class, spoken status) for one torrent row."""
    if row.error:
        return "error", "Error"
    if row.paused:
        return "paused", row.status_text
    if row.state in ("checking_files", "checking_resume_data",
                     "queued_for_checking", "allocating"):
        return "checking", row.status_text
    if row.state in ("seeding", "finished"):
        return "seeding", row.status_text
    return "downloading", row.status_text


def hash_password(password, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 ITERATIONS)
    return salt.hex(), digest.hex()


def check_password(password, salt_hex, hash_hex):
    if not salt_hex or not hash_hex or not password:
        return False
    try:
        _salt, digest = hash_password(password, salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(digest, hash_hex)


def _e(text):
    return html.escape(str(text or ""), quote=True)


def _primary_address():
    """The address Windows uses to reach the network. A UDP connect sends
    nothing; it only asks the system which local address it would use."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def lan_addresses():
    """This computer's IPv4 network addresses, for a phone to connect to.
    The address used for the network comes first."""
    found = []
    primary = _primary_address()
    if primary and not primary.startswith(("127.", "169.254.", "0.")):
        found.append(primary)
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        address = info[4][0]
        if address.startswith("127.") or address.startswith("169.254.") or \
                address in found:
            continue
        found.append(address)
    return found


class WebRemote(object):
    def __init__(self, frame):
        self.frame = frame
        self.server = None
        self.thread = None
        self.port = 0
        self.host = ""
        self.sessions = {}
        self.fails = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------ control
    @property
    def config(self):
        return self.frame.config

    def running(self):
        return self.server is not None

    def urls(self):
        """Addresses to open, this computer first, then network addresses."""
        if not self.running():
            return []
        # 127.0.0.1 rather than localhost: the server listens on IPv4 only.
        result = ["http://127.0.0.1:%d/" % self.port]
        if self.host == "0.0.0.0":
            result += ["http://%s:%d/" % (address, self.port)
                       for address in lan_addresses()]
        return result

    def url(self):
        return ", ".join(self.urls())

    def start(self):
        """(ok, message). Does nothing when the remote is turned off."""
        self.stop()
        if not self.config.get("webui_enabled"):
            return True, ""
        if not self.config.get("webui_hash"):
            return False, "Set a password for the web remote first"
        self.host = "0.0.0.0" if self.config.get("webui_lan") else "127.0.0.1"
        try:
            port = int(self.config.get("webui_port", 8686) or 0)
        except (TypeError, ValueError):
            port = 8686
        try:
            server = ThreadingHTTPServer((self.host, port), _make_handler(self))
        except OSError as exc:
            return False, "The web remote could not start: %s" % exc
        server.daemon_threads = True
        self.server = server
        self.port = server.server_address[1]
        self.thread = threading.Thread(target=server.serve_forever,
                                       name="web-remote", daemon=True)
        self.thread.start()
        return True, "Web remote running at %s" % self.url()

    def stop(self):
        server = self.server
        self.server = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        with self.lock:
            self.sessions.clear()

    # ------------------------------------------------------------ helpers
    def log(self, text):
        try:
            import wx
            wx.CallAfter(self.frame.add_log, text)
        except Exception:
            pass

    def new_session(self):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self.lock:
            self.sessions[token] = (csrf, time.time() + SESSION_SECONDS)
        return token

    def session(self, token):
        if not token:
            return None
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None:
                return None
            if entry[1] < time.time():
                del self.sessions[token]
                return None
            return entry[0]

    def end_session(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def locked(self, address):
        with self.lock:
            count, until = self.fails.get(address, (0, 0))
            return count >= MAX_FAILS and until > time.time()

    def failed(self, address):
        with self.lock:
            count, until = self.fails.get(address, (0, 0))
            if until and until < time.time():
                count = 0
            self.fails[address] = (count + 1, time.time() + LOCK_SECONDS)

    def succeeded(self, address):
        with self.lock:
            self.fails.pop(address, None)

    def rows(self):
        try:
            return list(self.frame.engine.snapshot())
        except Exception:
            return []


def _make_handler(remote):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP_NAME
        sys_version = ""

        def log_message(self, *args):
            return

        # --------------------------------------------------------- output
        def _send(self, status, body, content_type="text/html; charset=utf-8",
                  headers=None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "form-action 'self'")
            for name, value in (headers or []):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, message="", cookie=None):
            location = "/"
            if message:
                location += "?" + urllib.parse.urlencode({"m": message})
            headers = [("Location", location)]
            if cookie:
                headers.append(("Set-Cookie", cookie))
            self._send(303, "", headers=headers)

        def _token(self):
            raw = self.headers.get("Cookie", "") or ""
            for part in raw.split(";"):
                name, _sep, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
            return ""

        def _page(self, title, body):
            return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width, "
                    "initial-scale=1\"><title>%s</title><style>%s</style></head>"
                    "<body>%s</body></html>" % (_e(title), STYLE, body))

        def _login_page(self, message=""):
            body = ("<main><div class=\"login-box panel\"><h1>%s remote</h1>%s"
                    "<form class=\"login\" method=\"post\" action=\"/login\">"
                    "<label for=\"password\">Password</label>"
                    "<input id=\"password\" name=\"password\" type=\"password\" "
                    "autocomplete=\"current-password\" required autofocus>"
                    "<button class=\"primary\" type=\"submit\">Sign in</button>"
                    "</form></div></main>"
                    % (_e(APP_NAME),
                       "<p class=\"alert\" role=\"alert\">%s</p>" % _e(message)
                       if message else ""))
            return self._page("%s remote, sign in" % APP_NAME, body)

        def _card(self, index, row, hidden):
            name = _e(row.announce_name)
            css, status = _bucket(row)
            progress = max(0.0, min(1.0, float(row.progress or 0.0)))
            percent = fmt.percent(progress) if row.has_metadata else ""
            details = [("Size", row.size_text or "unknown"),
                       ("Down", row.down_speed_text or "0 B/s"),
                       ("Up", row.up_speed_text or "0 B/s")]
            if row.eta_text:
                details.append(("Time left", row.eta_text))
            if row.num_seeds is not None:
                details.append(("Seeds", row.seeds_text))
                details.append(("Peers", row.peers_text))
            if row.ratio_text:
                details.append(("Ratio", row.ratio_text))
            items = "".join("<div><dt>%s</dt><dd>%s</dd></div>" % (_e(k), _e(v))
                            for k, v in details)
            buttons = []
            for op, label, kind in (("start", "Start",
                                     "primary" if row.paused else ""),
                                    ("pause", "Pause", ""),
                                    ("remove", "Remove", "danger")):
                buttons.append(
                    "<button type=\"submit\" name=\"op\" value=\"%s\" "
                    "class=\"%s\" aria-label=\"%s %s\">%s</button>"
                    % (op, kind, label, name, label))
            return (
                "<li class=\"torrent panel %s\"><article aria-labelledby=\"t%d\">"
                "<h3 id=\"t%d\">%s</h3>"
                "<p><span class=\"badge\">%s</span>"
                "<span class=\"pct\">%s</span></p>"
                "<div class=\"track\" aria-hidden=\"true\">"
                "<div class=\"fill\" style=\"width:%.1f%%\"></div></div>"
                "<dl>%s</dl>"
                "<form class=\"actions\" method=\"post\" action=\"/action\">%s"
                "<input type=\"hidden\" name=\"hash\" value=\"%s\">%s</form>"
                "</article></li>"
                % (css, index, index, name, _e(status), _e(percent),
                   progress * 100.0, items, hidden, _e(row.info_hash),
                   "".join(buttons)))

        def _main_page(self, csrf, message):
            rows = remote.rows()
            stats = getattr(remote.frame, "session_stats", None) or {}
            hidden = "<input type=\"hidden\" name=\"csrf\" value=\"%s\">" % _e(csrf)
            count = "%d torrent%s" % (len(rows), "" if len(rows) == 1 else "s")
            parts = ["<main>"]
            parts.append(
                "<div class=\"top\"><div><h1>%s remote</h1>"
                "<p class=\"summary\">Down %s, up %s. %s.</p></div>"
                "<a class=\"button\" href=\"/\">Refresh</a></div>"
                % (_e(APP_NAME),
                   _e(fmt.human_speed(stats.get("download_rate", 0)) or "0 B/s"),
                   _e(fmt.human_speed(stats.get("upload_rate", 0)) or "0 B/s"),
                   _e(count)))
            if message:
                parts.append("<p class=\"alert\" role=\"status\">%s</p>"
                             % _e(message))
            parts.append("<h2>Add a magnet link</h2><div class=\"panel\">"
                         "<form class=\"add\" method=\"post\" action=\"/add\">%s"
                         "<label for=\"magnet\">Magnet link</label>"
                         "<input id=\"magnet\" name=\"magnet\" type=\"text\" "
                         "inputmode=\"url\" autocomplete=\"off\" required>"
                         "<button class=\"primary\" type=\"submit\">Add</button>"
                         "</form></div>" % hidden)
            parts.append("<h2>Torrents, %s</h2>" % _e(count))
            if not rows:
                parts.append("<p class=\"empty\">There are no torrents.</p>")
            else:
                parts.append("<ul class=\"torrents\">")
                for index, row in enumerate(rows):
                    parts.append(self._card(index, row, hidden))
                parts.append("</ul>")
                parts.append("<p class=\"empty\">Remove keeps the downloaded "
                             "data.</p>")
            parts.append("<form class=\"signout\" method=\"post\" "
                         "action=\"/logout\">%s<button type=\"submit\">Sign out"
                         "</button></form></main>" % hidden)
            return self._page("%s remote" % APP_NAME, "".join(parts))

        # ------------------------------------------------------------ GET
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            csrf = remote.session(self._token())
            if parsed.path == "/api/torrents":
                if csrf is None:
                    self._send(401, json.dumps({"error": "sign in first"}),
                               "application/json")
                    return
                data = [{"name": r.announce_name, "hash": r.info_hash,
                         "status": r.state_phrase(), "progress": r.progress or 0,
                         "down": r.download_rate or 0, "up": r.upload_rate or 0}
                        for r in remote.rows()]
                self._send(200, json.dumps(data), "application/json")
                return
            if parsed.path != "/":
                self._send(404, self._page("Not found", "<p>Not found.</p>"))
                return
            message = urllib.parse.parse_qs(parsed.query).get("m", [""])[0]
            if csrf is None:
                self._send(200, self._login_page(message))
            else:
                self._send(200, self._main_page(csrf, message))

        # ----------------------------------------------------------- POST
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                self._send(413, self._page("Too large", "<p>Too large.</p>"))
                return
            raw = self.rfile.read(length).decode("utf-8", "replace")
            form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
            path = urllib.parse.urlparse(self.path).path
            address = self.client_address[0]

            if path == "/login":
                if remote.locked(address):
                    self._send(429, self._login_page(
                        "Too many wrong passwords. Wait a minute."))
                    return
                if check_password(form.get("password", ""),
                                  remote.config.get("webui_salt", ""),
                                  remote.config.get("webui_hash", "")):
                    remote.succeeded(address)
                    token = remote.new_session()
                    remote.log("Web remote sign in from %s" % address)
                    self._redirect("", "%s=%s; HttpOnly; SameSite=Strict; "
                                       "Path=/" % (COOKIE, token))
                else:
                    remote.failed(address)
                    time.sleep(1)
                    self._send(403, self._login_page("Wrong password."))
                return

            token = self._token()
            csrf = remote.session(token)
            if csrf is None or not hmac.compare_digest(
                    form.get("csrf", ""), csrf):
                self._send(403, self._login_page("Please sign in again."))
                return

            if path == "/logout":
                remote.end_session(token)
                self._redirect("Signed out", "%s=; Max-Age=0; Path=/" % COOKIE)
            elif path == "/add":
                magnet = form.get("magnet", "").strip()
                if not magnet.lower().startswith("magnet:"):
                    self._redirect("That is not a magnet link")
                    return
                remote.frame.engine.post(
                    "add_magnet", uri=magnet,
                    save_path=remote.config.get("save_path"))
                remote.log("Web remote added a magnet link")
                self._redirect("Magnet link added")
            elif path == "/action":
                op = form.get("op", "")
                info_hash = form.get("hash", "")
                known = dict((r.info_hash, r) for r in remote.rows())
                row = known.get(info_hash)
                if row is None or op not in ("start", "pause", "remove"):
                    self._redirect("That torrent is no longer in the list")
                    return
                command = {"start": "resume", "pause": "pause",
                           "remove": "remove"}[op]
                if command == "remove":
                    remote.frame.engine.post("remove", hashes=[info_hash],
                                             delete_files=False)
                else:
                    remote.frame.engine.post(command, hashes=[info_hash])
                done = {"start": "Started", "pause": "Paused",
                        "remove": "Removed"}[op]
                remote.log("Web remote: %s %s" % (done.lower(),
                                                  row.announce_name))
                self._redirect("%s %s" % (done, row.announce_name))
            else:
                self._send(404, self._page("Not found", "<p>Not found.</p>"))

    return Handler
