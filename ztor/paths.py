"""Where everything lives. ZTor is 100% portable.

Built:   ZTor\\ZTor.exe        the launcher
         ZTor\\app\\                 runtime files, plus:
                  data\\       settings, bookmarks, RSS, tracker list,
                               resume\\ and torrents\\ backups
                  downloads\\  default download folder
                  cache\\      temporary files and the single instance lock
                  logs\\       ZTor.log
From source the same app folder sits beside main.py.

Nothing is written outside the app folder. Paths inside the install folder
are stored relative to it, so the folder can move to another drive or a USB
stick with a different letter. ZTOR_HOME overrides the app folder, for
the tests.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

APP_DIR_NAME = "app"
LOG_NAME = "ZTor.log"
LOG_LIMIT = 1024 * 1024


def install_root():
    """The folder holding the launcher: the exe when built, main.py from source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    override = os.environ.get("ZTOR_HOME")
    if override:
        return os.path.abspath(override)
    return os.path.join(install_root(), APP_DIR_NAME)


def _sub(name):
    path = os.path.join(app_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path


def data_dir():
    return _sub("data")


def cache_dir():
    return _sub("cache")


def logs_dir():
    return _sub("logs")


def downloads_dir():
    return _sub("downloads")


# ------------------------------------------------------------ portable paths
def to_portable(path):
    """An absolute path inside the install folder becomes relative to it."""
    if not path:
        return path
    try:
        absolute = os.path.abspath(path)
        relative = os.path.relpath(absolute, install_root())
    except ValueError:          # another drive
        return path
    if relative == os.curdir or relative.startswith(os.pardir):
        return path
    return relative


def from_portable(path):
    """A stored relative path becomes absolute against the install folder."""
    if not path or os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(install_root(), path))


# ------------------------------------------------------------------ startup
def migrate_legacy():
    """Copy data from the old %APPDATA%\\MyTorrent once, if there is no data yet.

    The old folder is only read, never changed. Returns True when it copied.
    """
    if os.environ.get("ZTOR_HOME"):
        return False
    target = data_dir()
    if os.path.exists(os.path.join(target, "settings.json")):
        return False
    legacy = os.path.join(os.environ.get("APPDATA", ""), "MyTorrent")
    if not os.environ.get("APPDATA") or not os.path.isdir(legacy):
        return False
    for name in os.listdir(legacy):
        source = os.path.join(legacy, name)
        destination = os.path.join(target, name)
        try:
            if os.path.isdir(source):
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)
        except OSError:
            continue
    return True


def _open_log():
    path = os.path.join(logs_dir(), LOG_NAME)
    try:
        if os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".old")
    except OSError:
        pass
    return open(path, "a", encoding="utf-8", buffering=1)


def _write_crash(handle, kind, value, tb):
    try:
        handle.write("%s  %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                       "unhandled error",
                                       "".join(traceback.format_exception(
                                           kind, value, tb))))
    except Exception:
        pass


def prepare():
    """Create the folders, keep temporary files inside, start the log."""
    for folder in (data_dir, cache_dir, logs_dir, downloads_dir):
        folder()
    cache = cache_dir()
    os.environ["TEMP"] = cache
    os.environ["TMP"] = cache
    tempfile.tempdir = cache
    copied = migrate_legacy()
    log = _open_log()
    # The windowed build and pythonw have no console; errors go to the log.
    if sys.stderr is None or getattr(sys, "frozen", False):
        sys.stderr = log
    if sys.stdout is None:
        sys.stdout = log
    previous = sys.excepthook

    def hook(kind, value, tb):
        _write_crash(log, kind, value, tb)
        if previous is not None and previous is not hook:
            try:
                previous(kind, value, tb)
            except Exception:
                pass

    sys.excepthook = hook

    def thread_hook(args):
        _write_crash(log, args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = thread_hook
    log.write("%s  started from %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                         install_root()))
    if copied:
        log.write("copied settings and torrents from the old AppData folder\n")
    return log
