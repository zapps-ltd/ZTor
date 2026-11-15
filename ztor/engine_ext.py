"""Extra engine commands that are not part of the base engine.

Kept separate so the session code stays focused on libtorrent itself: this adds
the per torrent detail collection (files, peers, tracker status), session wide
statistics, and restoring torrents from the bookmark store.
"""

import libtorrent as lt

from .engine import Engine as BaseEngine
from .engine import _hash_str, _tracker_pair
from . import format as fmt


def _decode(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return ""
    return str(value or "")


def _peer_address(peer):
    """peer.ip is a list of (family, address, port) tuples."""
    try:
        entries = peer.ip
    except Exception:
        entries = None
    if entries:
        entry = entries[0]
        if len(entry) >= 3:
            return "%s:%s" % (entry[1], entry[2])
        if len(entry) == 2:
            return str(entry[1])
    for name in ("remote_endpoint", "local_endpoint"):
        try:
            endpoint = getattr(peer, name)
            if endpoint is not None:
                return "%s:%s" % (endpoint.address(), endpoint.port())
        except Exception:
            continue
    return ""


def _peer_flags(peer):
    letters = []
    for name, letter in (("seed", "S"), ("upload_only", "U"),
                         ("choked", "c"), ("remote_choked", "C"),
                         ("interesting", "i"), ("remote_interested", "I"),
                         ("snubbed", "n"), ("outgoing_connection", "o"),
                         ("local_connection", "L"), ("dht", "D"),
                         ("pex", "X"), ("handshake", "H"),
                         ("optimistic_unchoke", "O")):
        try:
            if getattr(peer, name):
                letters.append(letter)
        except Exception:
            continue
    return " ".join(letters)


def peer_snapshot(peer):
    return {
        "address": _peer_address(peer),
        "client": _decode(getattr(peer, "client", "")),
        "flags": _peer_flags(peer),
        "progress": fmt.percent(getattr(peer, "progress", 0.0) or 0.0),
        "down": fmt.human_speed(getattr(peer, "payload_down_speed",
                                        getattr(peer, "down_speed", 0)) or 0),
        "up": fmt.human_speed(getattr(peer, "payload_up_speed",
                                      getattr(peer, "up_speed", 0)) or 0),
        "downloaded": fmt.human_size(getattr(peer, "total_download", 0) or 0),
        "uploaded": fmt.human_size(getattr(peer, "total_upload", 0) or 0),
    }


class Engine(BaseEngine):
    """BaseEngine plus the commands the window asks for."""

    def _handle_command(self, kind, kwargs):
        if kind == "restore":
            entries = kwargs.get("entries") or []
            self.restore(entries)
            self._save_resume_now()
            return
        if kind == "request_details":
            self._collect_details(kwargs.get("info_hash"))
            return
        if kind == "session_stats":
            self._collect_session_stats()
            return
        return BaseEngine._handle_command(self, kind, kwargs)

    # ------------------------------------------------------------- details
    def _collect_details(self, info_hash):
        handle = self._handles.get(info_hash)
        if handle is None or not handle.is_valid():
            return
        payload = {"info_hash": info_hash, "files_progress": [],
                   "files_priorities": [], "peers": [], "tracker_status": {}}
        try:
            payload["files_progress"] = [int(x) for x in handle.file_progress()]
        except Exception:
            payload["files_progress"] = []
        try:
            payload["files_priorities"] = [int(x)
                                           for x in handle.get_file_priorities()]
        except Exception:
            payload["files_priorities"] = []
        try:
            payload["peers"] = [peer_snapshot(p) for p in handle.get_peer_info()]
        except Exception:
            payload["peers"] = []
        try:
            status = handle.status()
            current = status.current_tracker or ""
            payload["current_tracker"] = current
            for url, _tier in [_tracker_pair(t) for t in handle.trackers()]:
                mark = "working" if url == current else "waiting"
                payload["tracker_status"][url] = mark
        except Exception:
            pass
        self.on_alert("details", payload)

    # ------------------------------------------------------- session stats
    def _collect_session_stats(self):
        if self.session is None:
            return
        payload = {}
        try:
            status = self.session.status()
            payload = {
                "download_rate": status.download_rate,
                "upload_rate": status.upload_rate,
                "payload_download_rate": status.payload_download_rate,
                "payload_upload_rate": status.payload_upload_rate,
                "total_download": status.total_download,
                "total_upload": status.total_upload,
                "num_peers": status.num_peers,
                "dht_nodes": status.dht_nodes,
                "num_torrents": len(self._handles),
            }
        except Exception:
            payload = {}
        self.on_alert("session_stats", payload)
