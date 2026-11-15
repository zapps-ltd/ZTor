"""libtorrent session wrapper.

The session runs on its own thread. Commands are queued from the GUI thread and
executed in order. Once per refresh interval the engine builds a list of plain
TorrentRow snapshots, which is the only thing handed back to the GUI, so no
libtorrent object is shared between threads.
"""

import os
import queue
import random
import threading
import time
import warnings

import libtorrent as lt

from . import ipfilter
from . import trackers as trackmod
from . import tuning
from .row import TorrentRow

warnings.simplefilter("ignore", DeprecationWarning)

POST_TORRENT_UPDATES = int(lt.alert.category_t.status_notification |
                           lt.alert.category_t.error_notification |
                           lt.alert.category_t.storage_notification |
                           lt.alert.category_t.tracker_notification |
                           lt.alert.category_t.performance_warning)

QUIET_MASK = int(lt.alert.category_t.error_notification |
                 lt.alert.category_t.performance_warning)

MAGNET_PREFIXES = ("magnet:",)
TORRENT_SUFFIX = ".torrent"

# The DHT routing table, kept between runs like uTorrent's dht.dat, so a start
# finds peers at once instead of bootstrapping from nothing.
DHT_STATE_FILE = "dht.dat"
DHT_STATE_FLAGS = lt.save_state_flags_t.save_dht_state


def _hash_str(info_hashes):
    if info_hashes is None:
        return ""
    try:
        v1 = str(info_hashes.v1)
        if v1 and v1 != "0000000000000000000000000000000000000000":
            return v1
    except Exception:
        pass
    try:
        if info_hashes.has_v2():
            return str(info_hashes.v2)
    except Exception:
        pass
    return ""


class Engine(object):
    """Owns the libtorrent session and the background worker thread."""

    def __init__(self, config, on_snapshot, on_event, on_alert):
        self.config = config
        self.on_snapshot = on_snapshot
        self.on_event = _wrap(on_event)
        self.on_alert = _wrap(on_alert)
        self.session = None
        self._commands = queue.Queue()
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._handles = {}          # info hash -> torrent_handle
        self._order = []            # info hash -> insertion order
        self._meta = {}             # info hash -> dict of bookkeeping
        self._rows = []
        self._rows_ready = threading.Event()
        self._resume_pending = set()
        self._alert_wakeup = None
        self._tick_wakeup = None
        self._running = False
        self._active_port = 0
        self._ip_filter_path = None
        self._row_cache = {}        # info hash -> last TorrentRow
        self.dht_state_path = _dht_state_path()
        try:
            self._extra_trackers = trackmod.initial_list(config)
        except Exception:
            self._extra_trackers = list(trackmod.FALLBACK)

    # ----------------------------------------------------------- lifecycle
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="libtorrent",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout=6.0):
        self._stop.set()
        self._wake()
        self._wake_tick()
        if self._thread is not None:
            self._thread.join(timeout)

    def _wake(self):
        wake = self._alert_wakeup
        if wake is not None:
            try:
                wake()
            except Exception:
                self._alert_wakeup = None

    def _wake_tick(self):
        wake = self._tick_wakeup
        if wake is not None:
            try:
                wake()
            except Exception:
                self._tick_wakeup = None

    # ------------------------------------------------------------- commands
    def post(self, kind, **kwargs):
        self._commands.put((kind, kwargs))
        self._wake()
        self._wake_tick()

    def snapshot(self):
        """Latest plain row list, or an empty list. Safe from any thread."""
        with self._lock:
            return list(self._rows)

    def wait_for_rows(self, timeout=1.0):
        self._rows_ready.wait(timeout)
        self._rows_ready.clear()
        return self.snapshot()

    # ------------------------------------------------------------------ run
    def _build_session(self):
        port = self._choose_port()
        base = {
            "listen_interfaces": tuning.listen_string(
                self.config.get("bind_address", ""), port),
            "alert_mask": POST_TORRENT_UPDATES,
            "user_agent": tuning.USER_AGENT,
            "enable_dht": bool(self.config.get("enable_dht", True)),
            "dht_bootstrap_nodes": tuning.DHT_ROUTERS,
        }
        params = self._load_dht_state()
        if params is not None:
            params.settings = base
            self.session = lt.session(params)
        else:
            self.session = lt.session(base)
        # Everything else goes through the tolerant path, one key at a time
        # if needed, so one unknown key never stops the session.
        self._apply_settings(tuning.session_settings(self.config, port))
        # No set_alert_notify: the loop polls every 80 ms anyway, and the
        # callback made libtorrent's network thread wait for the Python lock
        # on every alert, which slowed transfers and hung the DHT save at exit.
        try:
            self.session.start_dht()
        except Exception:
            pass
        self._load_ip_filter(self.config.get("ip_filter_path", ""))

    def _choose_port(self):
        """The saved port, or a random high one that is then kept.

        Many providers slow down or block the classic 6881 to 6889 range, so an
        automatic port is picked from 20000 to 60999 and saved for next time.
        Windows reserves new port ranges at every start (Hyper-V, WSL), and
        libtorrent opens nothing at all when the UDP half is refused, so a
        saved port that can no longer be opened is replaced the same way.
        """
        try:
            port = int(self.config.get("listen_port", 0) or 0)
        except (TypeError, ValueError):
            port = 0
        blocked = port if port and not _port_free(port) else 0
        if not port or blocked:
            port = 0
            for _attempt in range(40):
                candidate = random.randint(20000, 60999)
                if _port_free(candidate):
                    port = candidate
                    break
            if not port:
                port = 16881
            self.config["listen_port"] = port
            self.on_event("port_chosen", port=port, replaced=blocked)
        self._active_port = port
        return port

    def _load_ip_filter(self, path, force=False):
        path = (path or "").strip()
        if not force and path == self._ip_filter_path:
            return
        self._ip_filter_path = path
        rules = lt.ip_filter()
        count = 0
        if path:
            try:
                ranges = ipfilter.load(path)
            except Exception as exc:
                self.on_event("ip_filter_failed", message=str(exc))
                ranges = []
            for start, end in ranges:
                try:
                    rules.add_rule(start, end, 1)
                    count += 1
                except Exception:
                    continue
        try:
            self.session.set_ip_filter(rules)
        except Exception as exc:
            self.on_event("ip_filter_failed", message=str(exc))
            return
        if path:
            self.on_event("ip_filter_loaded", count=count, path=path)

    def _run(self):
        try:
            self._build_session()
        except Exception as exc:
            self.on_event("session_failed", message=str(exc))
            return
        self._running = True
        self.on_event("session_ready")
        last_snapshot = 0.0
        interval = max(0.2, float(self.config.get("row_interval_ms", 900)) / 1000.0)
        while not self._stop.is_set():
            self._drain_commands()
            self._drain_alerts()
            now = time.monotonic()
            if now - last_snapshot >= interval:
                self._publish_snapshot()
                last_snapshot = now
            time.sleep(0.08)
        self._shutdown()

    def _shutdown(self):
        """Save resume data for everything, then let the session go."""
        self.on_event("shutting_down")
        try:
            for handle in list(self._handles.values()):
                try:
                    if handle.is_valid():
                        handle.save_resume_data(
                            int(lt.save_resume_flags_t.save_info_dict))
                except Exception:
                    pass
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                self._drain_alerts(collect_resume=True)
                if not self._resume_pending:
                    break
                time.sleep(0.1)
        except Exception:
            pass
        self._save_dht_state()
        self._running = False

    def _load_dht_state(self):
        """Session parameters holding the saved DHT table, or None."""
        path = self.dht_state_path
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "rb") as handle:
                return lt.read_session_params(handle.read(), DHT_STATE_FLAGS)
        except Exception:
            return None

    def _save_dht_state(self):
        path = self.dht_state_path
        if not path or self.session is None:
            return False
        try:
            blob = lt.write_session_params_buf(
                self.session.session_state(flags=DHT_STATE_FLAGS),
                DHT_STATE_FLAGS)
            temp = path + ".tmp"
            with open(temp, "wb") as handle:
                handle.write(blob)
            os.replace(temp, path)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------- commands
    def _drain_commands(self):
        while True:
            try:
                kind, kwargs = self._commands.get_nowait()
            except queue.Empty:
                return
            # A command may change a label, a name or a flag that the status
            # update does not report, so every row is read fresh next time.
            self._row_cache.clear()
            try:
                self._handle_command(kind, kwargs)
            except Exception as exc:
                self.on_event("command_failed", command=kind, message=str(exc))

    def _handle_command(self, kind, kwargs):
        if kind == "add_magnet":
            self._add_magnet(kwargs["uri"], kwargs.get("save_path"),
                             kwargs.get("paused", False), kwargs.get("label", ""))
        elif kind == "add_torrent_file":
            self._add_file(kwargs["path"], kwargs.get("save_path"),
                           kwargs.get("paused", False), kwargs.get("label", ""))
        elif kind == "add_torrent_blob":
            self._add_blob(kwargs["blob"], kwargs.get("save_path"),
                           kwargs.get("paused", False), kwargs.get("label", ""),
                           exact=bool(kwargs.get("exact", False)))
        elif kind == "pause":
            self._set_stopped(kwargs["hashes"], False)
            self._each(kwargs["hashes"], self._stop_torrent)
        elif kind == "stop":
            # Like pause for libtorrent, but the list says Stopped, or
            # Finished once the download is complete, as uTorrent does.
            self._set_stopped(kwargs["hashes"], True)
            self._each(kwargs["hashes"], self._stop_torrent)
        elif kind == "resume":
            for info_hash in kwargs["hashes"]:
                meta = self._meta.get(info_hash)
                if meta is not None and meta.get("goal_done"):
                    # Started again by hand after its seeding goal: keep it
                    # running until the user stops it.
                    meta["goal_override"] = True
            self._set_stopped(kwargs["hashes"], False)
            self._each(kwargs["hashes"], self._start_torrent)
            self._queue_first(kwargs["hashes"])
        elif kind == "apply_config":
            self._apply_settings(tuning.session_settings(self.config,
                                                         self._active_port))
            self._load_ip_filter(self.config.get("ip_filter_path", ""),
                                 force=True)
        elif kind == "set_extra_trackers":
            self._extra_trackers = list(kwargs.get("urls") or [])
        elif kind == "force_recheck":
            self._each(kwargs["hashes"], lambda h: h.force_recheck())
        elif kind == "reannounce":
            self._each(kwargs["hashes"], self._force_reannounce)
        elif kind == "reset_connections":
            self._each(kwargs["hashes"], self._reset_connections)
            self.on_event("connections_reset", count=len(kwargs["hashes"]))
        elif kind == "clear_peers":
            done = []
            self._each(kwargs["hashes"],
                       lambda h: done.append(self._clear_peers(h)))
            self.on_event("peers_cleared", count=sum(1 for d in done if d),
                          supported=all(done) if done else True)
        elif kind == "update_trackers":
            if kwargs.get("urls"):
                self._extra_trackers = list(kwargs["urls"])
            added = []
            self._each(kwargs["hashes"],
                       lambda h: added.append(self._update_trackers(h)))
            self.on_event("trackers_updated", torrents=len(added),
                          added=sum(n for n in added if n > 0),
                          private=sum(1 for n in added if n < 0))
        elif kind == "queue_top":
            self._each(kwargs["hashes"], lambda h: h.queue_position_top())
        elif kind == "queue_up":
            self._each(kwargs["hashes"], lambda h: h.queue_position_up())
        elif kind == "queue_down":
            self._each(kwargs["hashes"], lambda h: h.queue_position_down())
        elif kind == "queue_bottom":
            self._each(kwargs["hashes"], lambda h: h.queue_position_bottom())
        elif kind == "set_labels":
            label = kwargs.get("label", "")
            changed = []
            for info_hash in kwargs["hashes"]:
                meta = self._meta.get(info_hash)
                if meta is None:
                    continue
                meta["label"] = label
                changed.append(info_hash)
            if changed:
                self.on_event("labels_changed", hashes=changed, label=label)
        elif kind == "set_sequential":
            value = kwargs["value"]
            self._each(kwargs["hashes"],
                       lambda h: h.set_sequential_download(value))
        elif kind == "set_super_seeding":
            value = kwargs["value"]
            self._each(kwargs["hashes"], lambda h: h.super_seeding(value))
        elif kind == "set_download_limit":
            limit = kwargs["value"]
            self._each(kwargs["hashes"], lambda h: h.set_download_limit(limit))
        elif kind == "set_upload_limit":
            limit = kwargs["value"]
            self._each(kwargs["hashes"], lambda h: h.set_upload_limit(limit))
        elif kind == "set_priority":
            value = int(kwargs["value"])
            changed = []
            for info_hash in kwargs["hashes"]:
                meta = self._meta.get(info_hash)
                if meta is not None:
                    meta["priority"] = value
                    changed.append(info_hash)
                handle = self._handles.get(info_hash)
                if handle is not None and handle.is_valid():
                    try:
                        handle.set_priority(value)
                    except Exception:
                        pass
            if changed:
                self.on_event("priority_changed", hashes=changed, value=value)
        elif kind == "remove":
            self._remove(kwargs["hashes"], kwargs.get("delete_files", False))
        elif kind == "remove_files_only":
            self._remove(kwargs["hashes"], True)
        elif kind == "save_resume":
            self._request_resume(kwargs["hashes"])
        elif kind == "save_now":
            self._save_resume_now()
        elif kind == "apply_settings":
            self._apply_settings(kwargs.get("settings", {}))
        elif kind == "pause_all":
            self.session.pause()
        elif kind == "resume_all":
            self.session.resume()
        elif kind == "add_tracker":
            handle = self._handles.get(kwargs["info_hash"])
            if handle is not None and handle.is_valid():
                handle.add_tracker({"url": kwargs["url"],
                                    "tier": kwargs.get("tier", 0)})
                handle.force_reannounce()
        elif kind == "set_file_priorities":
            handle = self._handles.get(kwargs["info_hash"])
            if handle is not None and handle.is_valid():
                handle.prioritize_files(kwargs["priorities"])
                self.on_event("file_priorities_changed",
                              info_hash=kwargs["info_hash"],
                              priorities=kwargs["priorities"])
        elif kind == "set_display_name":
            meta = self._meta.get(kwargs["info_hash"])
            if meta is not None:
                meta["display_name"] = kwargs.get("name", "") or ""
        elif kind == "rename_file":
            handle = self._handles.get(kwargs["info_hash"])
            if handle is not None and handle.is_valid():
                handle.rename_file(kwargs["index"], kwargs["name"])
        elif kind == "move_storage":
            handle = self._handles.get(kwargs["info_hash"])
            if handle is not None and handle.is_valid():
                handle.move_storage(kwargs["path"])
                self.on_event("storage_moved", info_hash=kwargs["info_hash"],
                              path=kwargs["path"])
        elif kind == "add_url_seed":
            handle = self._handles.get(kwargs["info_hash"])
            if handle is not None and handle.is_valid():
                handle.add_url_seed(kwargs["url"])
        elif kind == "connect_peer":
            handle = self._handles.get(kwargs["info_hash"])
            if handle is not None and handle.is_valid():
                endpoint = _parse_endpoint(kwargs.get("address", ""))
                if endpoint is None:
                    self.on_event("peer_failed", address=kwargs.get("address", ""))
                else:
                    handle.connect_peer(endpoint)
                    self.on_event("peer_connected",
                                  address=kwargs.get("address", ""))
        elif kind == "set_auto_managed":
            value = kwargs["value"]
            self._each(kwargs["hashes"],
                       lambda h: h.set_flags(lt.torrent_flags.auto_managed)
                       if value else h.unset_flags(lt.torrent_flags.auto_managed))
        elif kind == "force_start":
            value = bool(kwargs.get("value", True))
            for info_hash in kwargs["hashes"]:
                meta = self._meta.setdefault(info_hash, {})
                meta["force_started"] = value
            if value:
                self._set_stopped(kwargs["hashes"], False)
            self._each(kwargs["hashes"],
                       lambda h: self._force_start(h) if value else None)
            self.on_event("force_changed", hashes=kwargs["hashes"], value=value)
        elif kind == "clear_error":
            self._each(kwargs["hashes"], lambda h: h.clear_error())
        elif kind == "restart_dht":
            self.session.stop_dht()
            self.session.start_dht()
            self.on_event("dht_restarted")
        elif kind == "save_torrent_file":
            info_hash = kwargs["info_hash"]
            path = kwargs["path"]
            handle = self._handles.get(info_hash)
            if handle is None or not handle.is_valid():
                self.on_event("save_torrent_failed", message="No such torrent")
                return
            try:
                torrent_file = handle.torrent_file()
            except Exception:
                torrent_file = None
            if torrent_file is None:
                self.on_event("save_torrent_failed",
                              message="Metadata is not available yet")
                return
            try:
                lt.create_torrent(torrent_file).generate()
            except Exception:
                pass
            written = False
            for name in ("save", "write_torrent_file"):
                method = getattr(torrent_file, name, None)
                if method is None:
                    continue
                try:
                    method(path)
                    written = True
                    break
                except Exception as exc:
                    self.on_event("save_torrent_failed", message=str(exc))
                    return
            if written:
                self.on_event("torrent_saved", info_hash=info_hash, path=path)
            else:
                self.on_event("save_torrent_failed",
                              message="This build cannot write torrent files")
        elif kind == "set_dht":
            self._each(kwargs["hashes"],
                       lambda h: h.unset_flags(lt.torrent_flags.disable_dht)
                       if kwargs["value"] else
                       h.set_flags(lt.torrent_flags.disable_dht))
        elif kind == "set_pex":
            self._each(kwargs["hashes"],
                       lambda h: h.unset_flags(lt.torrent_flags.disable_pex)
                       if kwargs["value"] else
                       h.set_flags(lt.torrent_flags.disable_pex))
        elif kind == "set_lsd":
            self._each(kwargs["hashes"],
                       lambda h: h.unset_flags(lt.torrent_flags.disable_lsd)
                       if kwargs["value"] else
                       h.set_flags(lt.torrent_flags.disable_lsd))
        elif kind == "shutdown":
            self._stop.set()

    def _each(self, hashes, action):
        for info_hash in hashes:
            handle = self._handles.get(info_hash)
            if handle is None or not handle.is_valid():
                continue
            try:
                action(handle)
            except Exception:
                continue

    def _stop_torrent(self, handle):
        """Pause and keep it paused.

        A torrent managed by the queue would be resumed again by libtorrent,
        so it leaves the queue while it is stopped, as in qBittorrent.
        """
        try:
            handle.unset_flags(lt.torrent_flags.auto_managed)
        except Exception:
            pass
        handle.pause()

    def _set_stopped(self, hashes, value):
        """Remember Stop apart from Pause; the GUI keeps it in bookmarks."""
        changed = []
        for info_hash in hashes:
            meta = self._meta.get(info_hash)
            if meta is None or bool(meta.get("stopped")) == bool(value):
                continue
            meta["stopped"] = bool(value)
            changed.append(info_hash)
        if changed:
            self.on_event("stopped_changed", hashes=changed, value=bool(value))

    def _queue_first(self, hashes):
        """Put torrents at the top of the download queue, keeping their order.

        A torrent started by hand, or just added, should download now. Lower
        in a full queue, libtorrent would pause it again at once and show it
        as queued.
        """
        for info_hash in reversed(list(hashes)):
            handle = self._handles.get(info_hash)
            if handle is None or not handle.is_valid():
                continue
            try:
                handle.queue_position_top()
            except Exception:
                continue

    def _start_torrent(self, handle):
        if self.config.get("auto_manage", True):
            try:
                handle.set_flags(lt.torrent_flags.auto_managed)
            except Exception:
                pass
        handle.resume()

    def _force_start(self, handle):
        """Start now and keep the torrent out of the queue."""
        try:
            handle.unset_flags(lt.torrent_flags.auto_managed)
        except Exception:
            pass
        try:
            handle.set_flags(lt.torrent_flags.auto_managed)
            handle.unset_flags(lt.torrent_flags.auto_managed)
        except Exception:
            pass
        handle.resume()

    def _reset_connections(self, handle):
        """Drop every peer connection and connect again, like Reset Bans."""
        paused = False
        try:
            paused = bool(handle.flags() & lt.torrent_flags.paused)
        except Exception:
            pass
        if paused:
            return
        handle.pause()
        handle.resume()
        self._force_reannounce(handle)

    def _clear_peers(self, handle):
        """Forget the peer list, banned peers included, and look again."""
        clear = getattr(handle, "clear_peers", None)
        if clear is None:
            self._reset_connections(handle)
            return False
        clear()
        self._force_reannounce(handle)
        return True

    def _update_trackers(self, handle):
        """Add the current public tracker list. -1 for a private torrent."""
        try:
            info = handle.torrent_file()
            if info is not None and info.priv():
                return -1
        except Exception:
            pass
        known = set()
        try:
            known = set(entry.get("url") for entry in handle.trackers())
        except Exception:
            pass
        added = 0
        for index, url in enumerate(self._extra_trackers or []):
            if url in known:
                continue
            try:
                handle.add_tracker({"url": url, "tier": 30 + index})
                added += 1
            except Exception:
                continue
        self._force_reannounce(handle)
        return added

    def _force_reannounce(self, handle):
        try:
            handle.force_reannounce(0, -1,
                                    int(lt.reannounce_flags_t.ignore_min_interval))
        except Exception:
            handle.force_reannounce()

    def _remove(self, hashes, delete_files):
        flags = int(lt.options_t.delete_files) if delete_files else 0
        for info_hash in hashes:
            handle = self._handles.pop(info_hash, None)
            self._meta.pop(info_hash, None)
            if info_hash in self._order:
                self._order.remove(info_hash)
            if handle is not None and handle.is_valid():
                try:
                    self.session.remove_torrent(handle, flags)
                except Exception:
                    pass
            self._resume_pending.discard(info_hash)
            self.on_event("removed", info_hash=info_hash)

    # ----------------------------------------------------------------- add
    def _add_params(self, params, save_path, paused, label, source,
                    exact=False):
        """exact keeps the data where it is, for seeding a torrent just made."""
        target = save_path or self.config.get("save_path")
        incomplete = (self.config.get("incomplete_path") or "").strip()
        completed = (self.config.get("move_completed_path") or "").strip()
        final_path = ""
        if exact:
            params.save_path = target
        elif incomplete and not _same_path(incomplete, target):
            params.save_path = incomplete
            final_path = completed or target
        else:
            params.save_path = target
            if completed and not _same_path(completed, target):
                final_path = completed
        os.makedirs(params.save_path, exist_ok=True)
        # libtorrent's default flags include paused, which left every new
        # torrent waiting as Queued. Clear it unless adding paused was asked.
        start_now = not (paused or self.config.get("start_paused"))
        if start_now:
            params.flags &= ~int(lt.torrent_flags.paused)
        else:
            params.flags |= int(lt.torrent_flags.paused)
        if self.config.get("auto_manage"):
            params.flags |= int(lt.torrent_flags.auto_managed)
        else:
            params.flags &= ~int(lt.torrent_flags.auto_managed)
        handle = self.session.add_torrent(params)
        if handle is None or not handle.is_valid():
            self.on_event("duplicate", source=source)
            return None
        info_hash = _hash_str(handle.info_hashes())
        if not info_hash:
            info_hash = _hash_str(params.info_hashes)
        if not info_hash:
            info_hash = str(handle.info_hash())
        if info_hash in self._handles:
            self.on_event("duplicate", source=source, info_hash=info_hash)
            return None
        self._handles[info_hash] = handle
        self._order.append(info_hash)
        self._meta[info_hash] = {
            "label": label or "",
            "source": source or "",
            "priority": 0,
            "display_name": "",
            "added_time": int(time.time()),
            "resume_requested": False,
            "force_started": False,
            "final_path": final_path,
            "goal_done": False,
            "goal_override": False,
            "stopped": False,
        }
        self._add_extra_trackers(handle, params)
        if start_now or self.config.get("queue_new_top"):
            try:
                handle.queue_position_top()
            except Exception:
                pass
        self.on_event("added", info_hash=info_hash,
                      name=handle.status().name or source,
                      label=label or "", save_path=params.save_path,
                      final_path=final_path)
        return info_hash

    def _add_extra_trackers(self, handle, params):
        """Public trackers for more peers. Never for private torrents."""
        if not self.config.get("extra_trackers_enabled", True):
            return
        urls = list(self._extra_trackers or [])
        if not urls:
            return
        try:
            info = params.ti
            if info is not None and info.priv():
                return
        except Exception:
            pass
        for index, url in enumerate(urls):
            try:
                handle.add_tracker({"url": url, "tier": 30 + index})
            except Exception:
                continue

    def _strip_extra_if_private(self, handle):
        """A magnet turned out to be private: take the extra trackers off."""
        try:
            info = handle.torrent_file()
            if info is None or not info.priv():
                return
            extras = set(self._extra_trackers or [])
            keep = []
            for entry in handle.trackers():
                url = _tracker_field(entry, "url", "") or ""
                if url and url not in extras:
                    announce = lt.announce_entry(url)
                    announce.tier = int(_tracker_field(entry, "tier", 0) or 0)
                    keep.append(announce)
            handle.replace_trackers(keep)
        except Exception:
            pass

    def _move_finished(self, handle):
        """Move a finished download to its completed folder, once."""
        try:
            if handle is None or not handle.is_valid():
                return
            info_hash = _hash_str(handle.info_hashes()) or str(handle.info_hash())
        except Exception:
            return
        meta = self._meta.get(info_hash)
        if not meta:
            return
        target = meta.get("final_path") or ""
        if not target:
            return
        meta["final_path"] = ""
        try:
            current = handle.status().save_path
        except Exception:
            current = ""
        if not _same_path(current, target):
            try:
                os.makedirs(target, exist_ok=True)
                handle.move_storage(target)
            except Exception as exc:
                self.on_event("command_failed", command="move completed",
                              message=str(exc))
                return
            self.on_event("storage_moved", info_hash=info_hash, path=target,
                          automatic=True)
        self.on_event("final_path_done", info_hash=info_hash)

    def _check_seed_goal(self, info_hash, handle, status, meta):
        """Pause or remove a torrent that reached its seeding goal.

        Returns True when the torrent was removed.
        """
        if not meta or meta.get("goal_override") or meta.get("goal_done") \
                or meta.get("force_started"):
            return False
        try:
            if status.paused or not (status.is_seeding or status.is_finished):
                return False
        except Exception:
            return False
        ratio_limit = self.config.get("seed_ratio_limit", 0)
        minutes = self.config.get("seed_time_limit_min", 0)
        try:
            if float(ratio_limit or 0) <= 0 and int(minutes or 0) <= 0:
                return False
        except (TypeError, ValueError):
            return False
        down = getattr(status, "all_time_download", 0) or 0
        up = getattr(status, "all_time_upload", 0) or 0
        if not down:
            down = status.total_done or 0
        ratio = (float(up) / down) if down else 0.0
        seconds = _seconds(getattr(status, "seeding_duration", None)) or \
            _seconds(getattr(status, "seeding_time", 0))
        reason = tuning.seed_goal_reason(ratio_limit, minutes, ratio, seconds)
        if not reason:
            return False
        meta["goal_done"] = True
        name = meta.get("display_name") or status.name or info_hash
        if int(self.config.get("seed_goal_action", 0) or 0) == 1:
            self._remove([info_hash], False)
            self.on_event("seed_goal", name=name, reason=reason,
                          action="removed")
            return True
        try:
            self._stop_torrent(handle)
        except Exception:
            pass
        self.on_event("seed_goal", name=name, reason=reason, action="paused")
        return False

    def _add_magnet(self, uri, save_path, paused, label):
        params = lt.parse_magnet_uri(uri)
        params.save_path = save_path or self.config.get("save_path")
        self._add_params(params, save_path, paused, label, uri)
        self.on_event("magnet_added", uri=uri)

    def _add_file(self, path, save_path, paused, label):
        if not os.path.isfile(path):
            self.on_event("add_failed", message="Torrent file not found",
                          source=path)
            return
        if path.lower().startswith(MAGNET_PREFIXES) or \
                os.path.splitext(path)[1].lower() != TORRENT_SUFFIX:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            if text.strip().lower().startswith("magnet:"):
                self._add_magnet(text.strip(), save_path, paused, label)
                return
        params = lt.add_torrent_params()
        try:
            params.ti = lt.torrent_info(path)
        except Exception as exc:
            self.on_event("add_failed", message=str(exc), source=path)
            return
        info_hash = self._add_params(params, save_path, paused, label, path)
        if info_hash:
            try:
                with open(path, "rb") as handle:
                    blob = handle.read()
                self.on_event("torrent_file", info_hash=info_hash, blob=blob)
            except OSError:
                pass

    def _add_blob(self, blob, save_path, paused, label, source="", exact=False):
        params = lt.add_torrent_params()
        try:
            params.ti = lt.torrent_info(bytes(blob))
        except Exception as exc:
            self.on_event("add_failed", message=str(exc), source=source)
            return
        info_hash = self._add_params(params, save_path, paused, label, source,
                                     exact=exact)
        if info_hash:
            self.on_event("torrent_file", info_hash=info_hash, blob=blob)

    def restore(self, entries):
        """Re-add torrents recorded in the bookmark file."""
        for entry in entries:
            info_hash = entry.get("info_hash")
            try:
                params = None
                blob = entry.get("resume_data")
                if blob:
                    params = lt.read_resume_data(bytes(blob))
                if params is None or (not params.ti and not params.info_hashes):
                    torrent_blob = entry.get("torrent_file")
                    if not torrent_blob:
                        continue
                    params = lt.add_torrent_params()
                    params.ti = lt.torrent_info(bytes(torrent_blob))
                    # No resume data yet, so no saved paused state: the
                    # default flags would leave it paused as Queued.
                    if not entry.get("stopped"):
                        params.flags &= ~int(lt.torrent_flags.paused)
                save_path = entry.get("save_path") or self.config.get("save_path")
                params.save_path = save_path
                if entry.get("paused") or entry.get("stopped"):
                    params.flags |= int(lt.torrent_flags.paused)
                handle = self.session.add_torrent(params)
                if handle is None or not handle.is_valid():
                    continue
                priority = int(entry.get("priority", 0) or 0)
                if priority:
                    try:
                        handle.set_priority(priority)
                    except Exception:
                        pass
                key = info_hash or _hash_str(handle.info_hashes())
                self._handles[key] = handle
                self._order.append(key)
                self._meta[key] = {
                    "label": entry.get("label", ""),
                    "source": entry.get("source", ""),
                    "priority": int(entry.get("priority", 0) or 0),
                    "display_name": entry.get("display_name", "") or "",
                    "added_time": int(entry.get("added_time") or time.time()),
                    "resume_requested": False,
                    "force_started": False,
                    "final_path": entry.get("final_path", "") or "",
                    "goal_done": False,
                    "goal_override": False,
                    "stopped": bool(entry.get("stopped")),
                }
            except Exception:
                continue

    def _save_resume_now(self):
        self._request_resume(list(self._handles.keys()))

    def _request_resume(self, hashes):
        for info_hash in hashes:
            handle = self._handles.get(info_hash)
            if handle is None or not handle.is_valid():
                continue
            try:
                handle.save_resume_data(int(lt.save_resume_flags_t.save_info_dict))
                self._resume_pending.add(info_hash)
            except Exception:
                continue

    def _apply_settings(self, values):
        if not values:
            return
        try:
            self.session.apply_settings(values)
        except Exception:
            for key, value in values.items():
                try:
                    self.session.apply_settings({key: value})
                except Exception:
                    continue

    # -------------------------------------------------------------- alerts
    def _drain_alerts(self, collect_resume=False):
        try:
            alerts = self.session.pop_alerts()
        except Exception:
            return
        for alert in alerts:
            self._handle_alert(alert)

    def _handle_alert(self, alert):
        try:
            if isinstance(alert, lt.save_resume_data_alert):
                self._handle_resume(alert)
            elif isinstance(alert, lt.save_resume_data_failed_alert):
                name = getattr(alert, "torrent_name", "") or ""
                self.on_event("resume_failed", name=name,
                              message=alert.message())
            elif isinstance(alert, lt.add_torrent_alert):
                code = _error_value(getattr(alert, "error", None))
                if code:
                    self.on_event(
                        "add_failed",
                        message=_error_message(getattr(alert, "error", None))
                        or "the torrent could not be added",
                        source=alert.message())
            elif isinstance(alert, lt.metadata_received_alert):
                self._strip_extra_if_private(alert.handle)
                self.on_event("metadata", name=self._name_of(alert))
            elif isinstance(alert, lt.metadata_failed_alert):
                self.on_event("metadata_failed", name=self._name_of(alert))
            elif isinstance(alert, lt.torrent_finished_alert):
                self.on_event("finished", name=self._name_of(alert))
                self._move_finished(alert.handle)
            elif isinstance(alert, lt.torrent_error_alert):
                self.on_event("torrent_error", name=self._name_of(alert),
                              message=str(alert.error))
            elif isinstance(alert, lt.torrent_removed_alert):
                self.on_event("removed", name=getattr(alert, "torrent_name", ""))
            elif isinstance(alert, lt.listen_failed_alert):
                self.on_event("listen_failed", message=alert.message())
            elif isinstance(alert, lt.portmap_error_alert):
                self.on_event("portmap_error", message=alert.message())
            elif isinstance(alert, lt.tracker_error_alert):
                self.on_event("tracker_error", name=self._name_of(alert),
                              message=alert.message())
            elif isinstance(alert, lt.torrent_checked_alert):
                self.on_event("checked", name=self._name_of(alert))
        except Exception:
            return

    def _name_of(self, alert):
        name = getattr(alert, "torrent_name", "") or ""
        if name:
            return name
        try:
            handle = getattr(alert, "handle", None)
            if handle is not None and handle.is_valid():
                return handle.status().name
        except Exception:
            pass
        return ""

    def _handle_resume(self, alert):
        payload = _blob_from_resume(alert)
        if not payload:
            self.on_event("resume_failed", message="no resume data produced")
            return
        info_hash = _hash_str(alert.params.info_hashes)
        if not info_hash:
            info_hash = _hash_str(alert.handle.info_hashes())
        if not info_hash:
            info_hash = str(alert.handle.info_hash())
        self._resume_pending.discard(info_hash)
        self.on_event("resume_data", info_hash=info_hash, blob=payload,
                      save_path=alert.params.save_path,
                      name=alert.params.name or "")

    # ------------------------------------------------------------ snapshots
    def _publish_snapshot(self):
        try:
            self.session.post_torrent_updates()
            statuses = None
            for alert in self.session.pop_alerts():
                if isinstance(alert, lt.state_update_alert):
                    statuses = alert.status
                else:
                    self._handle_alert(alert)
            if statuses is None:
                statuses = [h.status() for h in self._handles.values()
                            if h.is_valid()]
        except Exception:
            return
        order = {info_hash: index for index, info_hash in enumerate(self._order)}
        self._enc_policy = None
        try:
            self._enc_policy = int(self.session.get_settings().get(
                "out_enc_policy", 0))
        except Exception:
            self._enc_policy = None
        rows_by_hash = dict(self._row_cache)
        for status in statuses:
            try:
                row = self._row_from_status(status)
            except Exception:
                continue
            if row is not None:
                rows_by_hash[row.info_hash] = row
        # The status update only lists torrents that changed since the last
        # one. Keep the last row of the others, and read any torrent without
        # a row yet directly, so idle torrents never drop out of the list.
        for info_hash, handle in list(self._handles.items()):
            if info_hash in rows_by_hash:
                continue
            try:
                if handle.is_valid():
                    row = self._row_from_status(handle.status())
                    if row is not None:
                        rows_by_hash[row.info_hash] = row
            except Exception:
                continue
        live = set(self._handles)
        self._row_cache = dict((key, row) for key, row in rows_by_hash.items()
                               if key in live)
        rows = sorted(self._row_cache.values(),
                      key=lambda r: order.get(r.info_hash, 9999))
        with self._lock:
            self._rows = rows
        self._rows_ready.set()

    _enc_policy = None

    def _row_from_status(self, status):
        handle = status.handle
        if not handle.is_valid():
            return None
        info_hash = _hash_str(handle.info_hashes())
        if not info_hash:
            info_hash = str(handle.info_hash())
        meta = self._meta.get(info_hash, {})
        if self._check_seed_goal(info_hash, handle, status, meta):
            return None
        row = TorrentRow()
        row.handle_id = str(handle.info_hash())
        row.info_hash = info_hash
        row.name = (meta.get("display_name") or status.name
                    or meta.get("source") or info_hash)
        row.label = meta.get("label", "")
        row.source = meta.get("source", "")
        row.save_path = status.save_path
        row.size = status.total
        row.total_done = status.total_done
        row.total_wanted = status.total_wanted
        row.total_upload = status.total_upload
        row.total_download = status.total_download
        row.download_rate = status.download_payload_rate
        row.upload_rate = status.upload_payload_rate
        row.progress = status.progress
        row.state = str(status.state)
        row.has_metadata = bool(status.has_metadata)
        row.paused = bool(status.paused)
        row.error = str(status.error) if status.error else ""
        row.num_seeds = status.num_seeds
        row.num_peers = status.num_peers
        row.num_connections = status.num_connections
        row.num_complete = status.num_complete
        row.num_incomplete = status.num_incomplete
        row.distributed_copies = status.distributed_copies
        row.added_time = status.added_time or meta.get("added_time")
        row.completed_time = status.completed_time
        row.active_time = status.active_time
        row.seeding_time = status.seeding_time
        row.queue_position = status.queue_position
        row.sequential = bool(status.sequential_download)
        row.super_seeding = bool(status.super_seeding)
        flags = int(status.flags)
        row.auto_managed = bool(flags & int(lt.torrent_flags.auto_managed))
        row.encryption_mode = getattr(self, "_enc_policy", None)
        row.download_limit = status.download_limit
        row.upload_limit = status.upload_limit
        row.current_tracker = status.current_tracker or ""
        row.num_pieces = status.num_pieces
        row.verified_pieces = status.verified_pieces
        row.priority = int(meta.get("priority", 0) or 0)
        row.flags_value = status.flags
        row.dht = not bool(flags & int(lt.torrent_flags.disable_dht))
        row.pex = not bool(flags & int(lt.torrent_flags.disable_pex))
        row.lsd = not bool(flags & int(lt.torrent_flags.disable_lsd))
        row.force_started = bool(meta.get("force_started", False))
        row.stopped = bool(meta.get("stopped")) and row.paused
        row.finished = bool(status.is_finished)
        torrent_file = status.torrent_file
        if torrent_file is not None:
            try:
                row.comment = torrent_file.comment() or ""
            except Exception:
                row.comment = ""
            try:
                storage = torrent_file.files()
                files = []
                for index in range(storage.num_files()):
                    files.append({
                        "index": index,
                        "path": storage.file_path(index),
                        "size": storage.file_size(index),
                    })
                row.files = files
            except Exception:
                row.files = []
            try:
                row.trackers = [_tracker_pair(t)
                                for t in torrent_file.trackers()]
            except Exception:
                row.trackers = []
        return row


def _tracker_field(entry, name, default=None):
    """A tracker entry is an announce_entry object, not a dictionary."""
    try:
        value = getattr(entry, name)
        return value() if callable(value) else value
    except Exception:
        pass
    try:
        return entry.get(name, default)
    except Exception:
        return default


def _tracker_pair(entry):
    url = _tracker_field(entry, "url", "") or ""
    tier = _tracker_field(entry, "tier", 0)
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        tier = 0
    return (url, tier)


def _error_value(error):
    """An error_code is always truthy, so compare its numeric value."""
    if error is None:
        return 0
    try:
        return int(error.value())
    except Exception:
        pass
    try:
        return 0 if str(error) == "" else 1
    except Exception:
        return 0


def _error_message(error):
    if error is None:
        return ""
    for attribute in ("message", "to_string"):
        try:
            value = getattr(error, attribute)
            text = value() if callable(value) else value
            if text:
                return str(text)
        except Exception:
            continue
    return str(error)


def _blob_from_resume(alert):
    """Return real resume bytes.

    Depending on the binding, save_resume_data_alert.resume_data is either the
    raw buffer or the resume dictionary. write_resume_data_buf always produces
    the buffer, and with save_info_dict set it also embeds the torrent file.
    """
    for attempt in (lambda: lt.write_resume_data_buf(alert.params),):
        try:
            raw = attempt()
            if raw:
                return bytes(raw)
        except Exception:
            pass
    data = getattr(alert, "resume_data", None)
    if data:
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data)
        try:
            return bytes(lt.bencode(data))
        except Exception:
            pass
    try:
        return bytes(lt.write_resume_data_buf(alert.params))
    except Exception:
        return None


def _parse_endpoint(text):
    """'1.2.3.4:6881' to (address, port), or None when it is not usable."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("["):
        closing = text.find("]")
        if closing < 0:
            return None
        address = text[1:closing]
        rest = text[closing + 1:].lstrip(":")
    else:
        if text.count(":") != 1:
            return None
        address, rest = text.split(":", 1)
    address = address.strip()
    try:
        port = int(rest.strip())
    except (TypeError, ValueError):
        return None
    if not address or not 0 < port < 65536:
        return None
    return (address, port)


def _same_path(first, second):
    if not first or not second:
        return False
    try:
        return os.path.normcase(os.path.abspath(first)) == \
            os.path.normcase(os.path.abspath(second))
    except Exception:
        return first == second


def _seconds(value):
    """A timedelta or a number of seconds, as whole seconds."""
    if value is None:
        return 0
    try:
        return int(value.total_seconds())
    except AttributeError:
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dht_state_path():
    try:
        from .accessibility import appdata_dir
        return os.path.join(appdata_dir(), DHT_STATE_FILE)
    except Exception:
        return ""


def _port_free(port):
    """True when both TCP and UDP can bind the port, as libtorrent needs."""
    import socket
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        sock = socket.socket(socket.AF_INET, kind)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
        finally:
            sock.close()
    return True


def _wrap(handler):
    """Guarantee that the handler receives (kind, payload mapping)."""
    if handler is None:
        return lambda kind, **payload: None

    def wrapped(kind, *args, **kwargs):
        payload = dict(kwargs)
        for extra in args:
            if isinstance(extra, dict):
                payload.update(extra)
            elif extra is not None:
                payload.setdefault("value", extra)
        try:
            return handler(kind, payload)
        except TypeError:
            # handler only takes the kind
            try:
                return handler(kind)
            except Exception:
                return None
        except Exception:
            return None

    return wrapped
