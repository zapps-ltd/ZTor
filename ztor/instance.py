"""One running copy per ZTor folder.

The first copy holds a lock file in app\\cache and listens on a random local
port written to app\\cache\\instance.json together with a secret token. A second
start finds the lock taken, sends its .torrent files and magnet links to the
running copy, which comes to the front, and exits.
"""

import json
import os
import secrets
import socket
import threading

from . import paths

INFO_FILE = "instance.json"
LOCK_FILE = "instance.lock"
MAX_MESSAGE = 256 * 1024


def _info_path():
    return os.path.join(paths.cache_dir(), INFO_FILE)


def acquire():
    """The lock handle for the first copy, or None when one is running."""
    handle = open(os.path.join(paths.cache_dir(), LOCK_FILE), "a+b")
    try:
        import msvcrt
    except ImportError:         # not Windows: no lock, never blocks
        return handle
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        handle.close()
        return None


def release(handle):
    if handle is None:
        return
    try:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


def allow_foreground():
    """Let the running copy take the foreground when this copy asks it to."""
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass


def send_to_running(args, timeout=3.0):
    """Hand args to the running copy. True when it accepted them."""
    try:
        with open(_info_path(), "r", encoding="utf-8") as handle:
            info = json.load(handle)
        port = int(info["port"])
        token = str(info["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    message = (json.dumps({"token": token, "args": list(args)}) + "\n")
    try:
        conn = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except OSError:
        return False            # nothing is listening: no copy is running
    try:
        conn.sendall(message.encode("utf-8"))
    except OSError:
        conn.close()
        return False
    # Delivered. The reply only confirms it; a lost reply is not a failure.
    reply = b""
    try:
        conn.settimeout(timeout)
        reply = conn.recv(16)
    except OSError:
        reply = b"ok"
    finally:
        conn.close()
    return reply.startswith(b"ok") or reply == b""


class InstanceServer(object):
    """Receives args from later starts and passes them to handler(args)."""

    def __init__(self, handler):
        self.handler = handler
        self.token = secrets.token_hex(16)
        self.closed = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        with open(_info_path(), "w", encoding="utf-8") as handle:
            json.dump({"port": self.port, "token": self.token,
                       "pid": os.getpid()}, handle)
        self.thread = threading.Thread(target=self._serve, name="instance",
                                       daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.closed:
            try:
                conn, _address = self.sock.accept()
            except OSError:
                return
            try:
                with conn:
                    conn.settimeout(3.0)
                    data = b""
                    while len(data) < MAX_MESSAGE and b"\n" not in data:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    line = data.split(b"\n", 1)[0]
                    message = json.loads(line.decode("utf-8", "replace"))
                    if message.get("token") != self.token:
                        try:
                            conn.sendall(b"no\n")
                        except OSError:
                            pass
                        continue
                    args = [str(a) for a in (message.get("args") or [])]
                    try:
                        conn.sendall(b"ok\n")
                        conn.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                try:
                    self.handler(args)
                except Exception:
                    pass
            except (OSError, ValueError, AttributeError):
                continue

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            with open(_info_path(), "r", encoding="utf-8") as handle:
                if json.load(handle).get("token") == self.token:
                    os.remove(_info_path())
        except (OSError, ValueError):
            pass
