"""Settings, torrent bookmarks, and resume data storage.

Everything lives in the portable app\\data folder (see paths.py):
  settings.json    user preferences, including the column state
  bookmarks.json   one record per torrent, including its resume data blob
  resume\\          raw .fastresume files, one per info hash
  torrents\\        a copy of every torrent file
Folder paths inside the install folder are saved relative to it, so the whole
folder can move to another drive letter.
"""

import json
import os
import shutil
import tempfile

from .accessibility import appdata_dir, sub_dir
from . import columns as colmod
from . import paths

# Settings that hold a folder or file path, saved portable.
PATH_KEYS = ("save_path", "monitor_folder", "incomplete_path",
             "move_completed_path", "ip_filter_path")
BOOKMARK_PATH_KEYS = ("save_path", "final_path")

SETTINGS_FILE = "settings.json"
BOOKMARKS_FILE = "bookmarks.json"
RESUME_DIR = "resume"


DEFAULTS = {
    "save_path": "",
    "download_rate_limit": 0,
    "upload_rate_limit": 0,
    "listen_port": 0,
    "enable_dht": True,
    "enable_lsd": True,
    "enable_upnp": True,
    "enable_natpmp": True,
    "encryption_mode": 0,
    "max_connections": 0,
    "active_downloads": 3,
    "active_seeds": 5,
    "proxy_type": 0,
    "proxy_host": "",
    "proxy_port": 0,
    "proxy_username": "",
    "proxy_password": "",
    "proxy_peers": True,
    "proxy_trackers": True,
    "user_agent": "ZTor/0.2.0",
    "start_paused": False,
    "start_minimized": False,
    "ask_save_path": False,
    "confirm_remove": True,
    "confirm_remove_data": True,
    "monitor_folder": "",
    "windows_integration": False,
    "auto_clean": True,
    "watch_interval_ms": 5000,
    "auto_manage": True,
    "queue_new_top": False,
    "show_notifications": True,
    "announce_actions": True,
    "row_interval_ms": 900,
    "window": {"x": -1, "y": -1, "w": 1100, "h": 700, "maximized": False},
    "columns": None,
    "sort_column": "name",
    "sort_reverse": False,
    "category_filter": "all",
    "label_filter": "all",
    "status_filter": "all",
    "labels": [],
    "torrent_queue": [],
    "upside_down_labels": False,
    # speed
    "speed_profile": 1,
    "announce_all": True,
    "prefer_tcp": False,
    "extra_trackers_enabled": True,
    "extra_trackers_url": ("https://raw.githubusercontent.com/ngosang/"
                           "trackerslist/master/trackers_best.txt"),
    "extra_trackers": "",
    # network
    "bind_address": "",
    "ip_filter_path": "",
    # folders
    "incomplete_path": "",
    "move_completed_path": "",
    # seeding goals: ratio 0 and minutes 0 mean no goal; action 0 pause, 1 remove
    "seed_ratio_limit": 0.0,
    "seed_time_limit_min": 0,
    "seed_goal_action": 0,
    # alternative speed limits and schedule, limits in bytes per second
    "alt_speed_on": False,
    "alt_dl_limit": 500000,
    "alt_ul_limit": 50000,
    "schedule_enabled": False,
    "schedule_from": "08:00",
    "schedule_to": "18:00",
    "schedule_days": 0,
    # display: appearance 0 follows Windows, 1 light, 2 dark
    "appearance": 0,
    "color_rows": True,
    "show_toolbar": True,
    "toast_notifications": True,
    # touch: 0 automatic, 1 on, 2 off; bigger rows, buttons and an action bar
    "touch_mode": 0,
    "show_action_bar": False,
    "animations": True,
    # RSS
    "rss_interval_min": 30,
    "rss_auto_download": True,
    # web remote: off by default, this computer only, password stored hashed
    "webui_enabled": False,
    "webui_port": 8686,
    "webui_lan": False,
    "webui_salt": "",
    "webui_hash": "",
    # search: optional Jackett or Prowlarr Torznab address and API key
    "torznab_url": "",
    "torznab_key": "",
}


def _atomic_write(path, text):
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store(object):
    def __init__(self):
        self.base = appdata_dir()
        self.resume_dir = sub_dir(RESUME_DIR)
        self.settings = dict(DEFAULTS)
        self.bookmarks = []
        self.load()

    # ------------------------------------------------------------- settings
    @property
    def settings_path(self):
        return os.path.join(self.base, SETTINGS_FILE)

    @property
    def bookmarks_path(self):
        return os.path.join(self.base, BOOKMARKS_FILE)

    def load(self):
        self.settings = dict(DEFAULTS)
        try:
            with open(self.settings_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            for key, value in saved.items():
                if key == "window" and isinstance(value, dict):
                    merged = dict(DEFAULTS["window"])
                    merged.update(value)
                    self.settings["window"] = merged
                else:
                    self.settings[key] = value
        except (OSError, ValueError):
            self.settings["columns"] = None
        for key in PATH_KEYS:
            self.settings[key] = paths.from_portable(self.settings.get(key) or "")
        if not self.settings.get("save_path"):
            self.settings["save_path"] = paths.downloads_dir()
        self.settings["columns"] = colmod.normalize(self.settings.get("columns"))
        self.bookmarks = self._read_bookmarks()

    def save(self):
        data = dict(self.settings)
        for key in PATH_KEYS:
            data[key] = paths.to_portable(data.get(key) or "")
        try:
            _atomic_write(self.settings_path,
                          json.dumps(data, indent=2, sort_keys=True))
        except OSError:
            return False
        return True

    def get(self, key, default=None):
        return self.settings.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.settings[key] = value

    # ------------------------------------------------------------ bookmarks
    def _read_bookmarks(self):
        try:
            with open(self.bookmarks_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict):
                        for key in BOOKMARK_PATH_KEYS:
                            if entry.get(key):
                                entry[key] = paths.from_portable(entry[key])
                return [e for e in data if isinstance(e, dict)]
        except (OSError, ValueError):
            pass
        return []

    def save_bookmarks(self):
        data = []
        for entry in self.bookmarks:
            copy = dict(entry)
            for key in BOOKMARK_PATH_KEYS:
                if copy.get(key):
                    copy[key] = paths.to_portable(copy[key])
            data.append(copy)
        try:
            _atomic_write(self.bookmarks_path, json.dumps(data, indent=1))
        except OSError:
            return False
        return True

    def find_bookmark(self, info_hash):
        for entry in self.bookmarks:
            if entry.get("info_hash") == info_hash:
                return entry
        return None

    def upsert_bookmark(self, info_hash, **fields):
        entry = self.find_bookmark(info_hash)
        if entry is None:
            entry = {"info_hash": info_hash}
            self.bookmarks.append(entry)
        entry.update(fields)
        return entry

    def remove_bookmark(self, info_hash):
        self.bookmarks = [e for e in self.bookmarks
                          if e.get("info_hash") != info_hash]
        resume = self.resume_path(info_hash)
        if os.path.isfile(resume):
            try:
                os.remove(resume)
            except OSError:
                pass

    # --------------------------------------------------------------- resume
    def resume_path(self, info_hash):
        safe = "".join(ch for ch in (info_hash or "unknown") if ch.isalnum())
        return os.path.join(self.resume_dir, safe + ".fastresume")

    def write_resume(self, info_hash, blob):
        try:
            with open(self.resume_path(info_hash), "wb") as handle:
                handle.write(blob)
            return True
        except OSError:
            return False

    def read_resume(self, info_hash):
        try:
            with open(self.resume_path(info_hash), "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def clear_resume(self, info_hash):
        path = self.resume_path(info_hash)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------ torrents
    def torrent_backup_dir(self):
        return sub_dir("torrents")

    def backup_torrent_file(self, info_hash, blob):
        safe = "".join(ch for ch in (info_hash or "unknown") if ch.isalnum())
        path = os.path.join(self.torrent_backup_dir(), safe + ".torrent")
        try:
            with open(path, "wb") as handle:
                handle.write(blob)
            return path
        except OSError:
            return None

    def copy_torrent_backup(self, info_hash, source_path):
        safe = "".join(ch for ch in (info_hash or "unknown") if ch.isalnum())
        dest = os.path.join(self.torrent_backup_dir(), safe + ".torrent")
        try:
            shutil.copyfile(source_path, dest)
            return dest
        except OSError:
            return None

    def read_torrent_backup(self, info_hash):
        safe = "".join(ch for ch in (info_hash or "unknown") if ch.isalnum())
        path = os.path.join(self.torrent_backup_dir(), safe + ".torrent")
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None


_store = None


def store():
    global _store
    if _store is None:
        _store = Store()
    return _store
