"""Extra public trackers that are added to new, non private torrents.

A best-trackers list is fetched from a URL in the background and cached under
the portable data folder app\\data, so a failed fetch still leaves a working list.
"""

import os
import threading
import urllib.request

from .accessibility import appdata_dir

DEFAULT_URL = ("https://raw.githubusercontent.com/ngosang/trackerslist/"
               "master/trackers_best.txt")
FALLBACK = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://explodie.org:6969/announce",
]
SCHEMES = ("udp://", "http://", "https://", "wss://")
CACHE_FILE = "trackers.txt"
MAX_BYTES = 1024 * 1024


def parse(text):
    seen = set()
    urls = []
    for line in (text or "").splitlines():
        url = line.strip()
        if url.lower().startswith(SCHEMES) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def merge(*lists):
    seen = set()
    result = []
    for urls in lists:
        for url in urls:
            if url not in seen:
                seen.add(url)
                result.append(url)
    return result


def cache_path():
    return os.path.join(appdata_dir(), CACHE_FILE)


def load_cached():
    try:
        with open(cache_path(), "r", encoding="utf-8") as handle:
            return parse(handle.read())
    except OSError:
        return []


def save_cache(urls):
    try:
        with open(cache_path(), "w", encoding="utf-8") as handle:
            handle.write("\n".join(urls) + "\n")
        return True
    except OSError:
        return False


def initial_list(config):
    """The user's own trackers first, then the cached or built in list."""
    custom = parse(config.get("extra_trackers", "") or "")
    return merge(custom, load_cached() or list(FALLBACK))


def fetch(url, timeout=15):
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http and https lists are supported")
    request = urllib.request.Request(url, headers={"User-Agent": "ZTor"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES)
    return parse(data.decode("utf-8", "replace"))


def refresh_async(config, done):
    """Fetch the list on a worker thread, then call done(urls, count, error)."""
    url = (config.get("extra_trackers_url") or "").strip()

    def work():
        count, error = 0, ""
        if url:
            try:
                fetched = fetch(url)
                if fetched:
                    save_cache(fetched)
                    count = len(fetched)
                else:
                    error = "the list was empty"
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
        done(initial_list(config), count, error)

    thread = threading.Thread(target=work, name="tracker-list", daemon=True)
    thread.start()
    return thread
