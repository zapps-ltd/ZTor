"""Clears leftover cache and temporary files.

Only files ZTor itself makes are touched: its .torrent backups and
resume files in app\\data, libtorrent part files (.<hash>.parts) beside the
data, stray .tmp files from saving, old files in app\\cache, and empty
folders a finished download leaves in the unfinished folder. Downloaded data
is never removed here.
"""

import os
import shutil
import time

# Files younger than this may still be in use, so they are left alone.
MIN_AGE = 600
KEEP_IN_CACHE = ("instance.lock", "instance.json")


def _safe(info_hash):
    return "".join(ch for ch in (info_hash or "") if ch.isalnum())


def _old_enough(path, now, min_age):
    try:
        return now - os.path.getmtime(path) >= min_age
    except OSError:
        return False


def _remove_file(path):
    """Size removed, or -1 when the file was missing or busy."""
    try:
        size = os.path.getsize(path)
        os.remove(path)
        return size
    except OSError:
        return -1


class Result(object):
    def __init__(self):
        self.files = 0
        self.bytes = 0

    def add(self, size):
        if size >= 0:
            self.files += 1
            self.bytes += size

    def merge(self, other):
        self.files += other.files
        self.bytes += other.bytes
        return self

    def text(self):
        if not self.files:
            return "No cache or temporary files to clear"
        size = self.bytes / 1024.0
        unit = "KB"
        if size >= 1024:
            size, unit = size / 1024.0, "MB"
        return "Cleared %d leftover cache and temporary items, %.1f %s" % (
            self.files, size, unit)


def part_files(info_hash, folders):
    safe = _safe(info_hash)
    if not safe:
        return []
    found = []
    for folder in folders:
        if not folder:
            continue
        path = os.path.join(folder, "." + safe + ".parts")
        if path not in found and os.path.isfile(path):
            found.append(path)
    return found


def clean_removed(store, info_hash, folders=()):
    """A torrent left the list: its backup, resume and part files go."""
    result = Result()
    safe = _safe(info_hash)
    if not safe:
        return result
    paths = [os.path.join(store.torrent_backup_dir(), safe + ".torrent"),
             store.resume_path(info_hash)]
    paths += part_files(info_hash, folders)
    for path in paths:
        if os.path.isfile(path):
            result.add(_remove_file(path))
    return result


def clean_finished(incomplete_root, name):
    """Remove the empty folders a finished download left behind."""
    result = Result()
    if not incomplete_root or not name:
        return result
    root = os.path.abspath(incomplete_root)
    target = os.path.abspath(os.path.join(root, name))
    if os.path.dirname(target) != root or not os.path.isdir(target):
        return result
    for folder, _dirs, _files in os.walk(target, topdown=False):
        try:
            os.rmdir(folder)        # only succeeds when empty
            result.add(0)
        except OSError:
            pass
    return result


def sweep(store, cache_dir, now=None, min_age=MIN_AGE):
    """Old temporary files, and backups of torrents no longer in the list."""
    now = time.time() if now is None else now
    result = Result()
    known = set(_safe(e.get("info_hash")) for e in store.bookmarks)
    # Stray .tmp files from interrupted saves.
    for folder, _dirs, files in os.walk(store.base):
        for name in files:
            path = os.path.join(folder, name)
            if name.endswith(".tmp") and _old_enough(path, now, min_age):
                result.add(_remove_file(path))
    # Backups and resume files of torrents that were removed.
    for folder, suffix in ((store.torrent_backup_dir(), ".torrent"),
                           (store.resume_dir, ".fastresume")):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if name.endswith(suffix) and name[:-len(suffix)] not in known \
                    and _old_enough(path, now, min_age):
                result.add(_remove_file(path))
    # Temporary files other programs and libraries left in app\cache.
    try:
        names = os.listdir(cache_dir)
    except OSError:
        names = []
    for name in names:
        if name.lower() in KEEP_IN_CACHE:
            continue
        path = os.path.join(cache_dir, name)
        if not _old_enough(path, now, min_age):
            continue
        if os.path.isdir(path):
            size = sum(os.path.getsize(os.path.join(d, f))
                       for d, _s, fs in os.walk(path) for f in fs
                       if os.path.isfile(os.path.join(d, f)))
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                result.add(size)
        else:
            result.add(_remove_file(path))
    return result


def clean_after_remove(store, info, full=False, incomplete_root=""):
    """Clear what a removed torrent leaves behind.

    info holds info_hash, name, save_path and source. Without full only the
    cache goes: backup, resume and part files. With full, also the empty
    folders of its data and the .torrent file it was added from, so no
    trace is left. Files that are not the torrent's own are never touched.
    """
    info_hash = info.get("info_hash") or ""
    save_path = info.get("save_path") or ""
    result = clean_removed(store, info_hash, [save_path, incomplete_root])
    if not full:
        return result
    name = info.get("name") or ""
    for root in (save_path, incomplete_root):
        result.merge(clean_finished(root, name))
    source = info.get("source") or ""
    if source.lower().endswith(".torrent") and os.path.isfile(source):
        result.add(_remove_file(source))
    return result
