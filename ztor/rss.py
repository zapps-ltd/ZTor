"""RSS and Atom feeds with download rules. No wx here, so it tests on its own.

Rules only act on articles that arrive after a feed was first checked, so adding
a feed never starts a flood of downloads. Older articles can still be
downloaded by hand from the RSS window.
"""

import json
import os
import re
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

from .accessibility import appdata_dir

RSS_FILE = "rss.json"
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_TORRENT_BYTES = 10 * 1024 * 1024
MAX_ITEMS = 200
MAX_SEEN = 5000


def _local(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(element):
    if element is None:
        return ""
    return (element.text or "").strip()


def parse_feed(data):
    """(feed title, [ {title, link, id, date} ]) from RSS 2.0 or Atom bytes."""
    root = ET.fromstring(data)
    title = ""
    for element in root.iter():
        name = _local(element.tag)
        if name in ("item", "entry"):
            break
        if name == "title" and not title:
            title = _text(element)
    items = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        entry = {"title": "", "link": "", "id": "", "date": ""}
        magnet = enclosure = link = info_hash = ""
        for child in element.iter():
            if child is element:
                continue
            name = _local(child.tag)
            if name == "title" and not entry["title"]:
                entry["title"] = _text(child)
            elif name == "magneturi":
                magnet = magnet or _text(child)
            elif name == "infohash":
                info_hash = info_hash or _text(child)
            elif name == "enclosure":
                enclosure = enclosure or (child.get("url") or "")
            elif name == "link":
                href = child.get("href")
                if href is not None:
                    if child.get("rel") == "enclosure":
                        enclosure = enclosure or href
                    else:
                        link = link or href
                else:
                    link = link or _text(child)
            elif name in ("guid", "id") and not entry["id"]:
                entry["id"] = _text(child)
            elif name in ("pubdate", "published", "updated", "date") and \
                    not entry["date"]:
                entry["date"] = _text(child)
        if not magnet and re.fullmatch(r"[0-9a-fA-F]{40}", info_hash or ""):
            magnet = "magnet:?xt=urn:btih:" + info_hash
        if not magnet and link.lower().startswith("magnet:"):
            magnet = link
        entry["link"] = magnet or enclosure or link
        if not entry["id"]:
            entry["id"] = entry["link"] or entry["title"]
        if entry["link"]:
            items.append(entry)
        if len(items) >= MAX_ITEMS:
            break
    return title, items


class MagnetRedirect(Exception):
    """A download link answered with a redirect to a magnet link."""

    def __init__(self, url):
        Exception.__init__(self, "redirected to a magnet link")
        self.url = url


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """Jackett and many feeds redirect a download link to a magnet link.
    urllib refuses that scheme, so it is caught here and handed back."""

    def http_error_302(self, req, fp, code, msg, headers):
        location = (headers.get("location") or headers.get("uri") or "").strip()
        if location.lower().startswith("magnet:"):
            raise MagnetRedirect(location)
        return urllib.request.HTTPRedirectHandler.http_error_302(
            self, req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = \
        http_error_302


_OPENER = urllib.request.build_opener(_RedirectHandler)


def _get(url, limit, timeout=20):
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http and https addresses are supported")
    request = urllib.request.Request(url, headers={"User-Agent": "ZTor RSS"})
    with _OPENER.open(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the response is too large")
    return data


def fetch_feed(url):
    return parse_feed(_get(url, MAX_FEED_BYTES))


def download_torrent(url):
    data = _get(url, MAX_TORRENT_BYTES)
    if not data.startswith(b"d"):
        raise ValueError("the link is not a torrent file")
    return data


def _words(text):
    return [w for w in re.split(r"[\s,]+", (text or "").lower()) if w]


def rule_matches(rule, feed_url, title):
    """A rule needs words that must appear; an empty rule matches nothing.

    Must contain may start with re: to use a regular expression.
    """
    if not rule.get("enabled", True):
        return False
    feed = rule.get("feed", "") or ""
    if feed and feed != feed_url:
        return False
    low = (title or "").lower()
    must = (rule.get("must_contain", "") or "").strip()
    if not must:
        return False
    if must.lower().startswith("re:"):
        try:
            if not re.search(must[3:].strip(), title or "", re.IGNORECASE):
                return False
        except re.error:
            return False
    else:
        for word in _words(must):
            if word not in low:
                return False
    for word in _words(rule.get("must_not_contain", "")):
        if word in low:
            return False
    return True


def check_feeds(feeds, rules, seen, first_urls):
    """Fetch feeds and find what to download. Runs on a worker thread.

    Returns (results, downloads, new_keys). results holds one entry per feed,
    downloads one per matched article, new_keys every article key not seen
    before.
    """
    results, downloads, new_keys = [], [], []
    seen = set(seen)
    for feed in feeds:
        url = feed.get("url", "")
        try:
            title, items = fetch_feed(url)
        except Exception as exc:
            results.append({"url": url, "title": "", "items": [],
                            "error": str(exc) or exc.__class__.__name__})
            continue
        results.append({"url": url, "title": title, "items": items, "error": ""})
        for item in items:
            key = url + "|" + item["id"]
            if key in seen:
                continue
            seen.add(key)
            new_keys.append(key)
            if url in first_urls:
                continue
            rule = None
            for candidate in rules:
                if rule_matches(candidate, url, item["title"]):
                    rule = candidate
                    break
            if rule is None:
                continue
            downloads.append(fetch_item(item, rule))
    return results, downloads, new_keys


def fetch_item(item, rule=None):
    """Turn an article into something the engine can add."""
    entry = {"title": item.get("title", ""), "link": item.get("link", ""),
             "rule": rule, "blob": None, "error": ""}
    if not entry["link"].lower().startswith("magnet:"):
        try:
            entry["blob"] = download_torrent(entry["link"])
        except MagnetRedirect as redirect:
            entry["link"] = redirect.url
        except Exception as exc:
            entry["error"] = str(exc) or exc.__class__.__name__
    return entry


class RssStore(object):
    """Feeds, rules and seen articles, saved as rss.json."""

    def __init__(self, base=None):
        self.path = os.path.join(base or appdata_dir(), RSS_FILE)
        self.data = {"feeds": [], "rules": [], "seen": []}
        self.load()

    @property
    def feeds(self):
        return self.data["feeds"]

    @property
    def rules(self):
        return self.data["rules"]

    @property
    def seen(self):
        return self.data["seen"]

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                for key in ("feeds", "rules", "seen"):
                    if isinstance(data.get(key), list):
                        self.data[key] = data[key]
        except (OSError, ValueError):
            pass

    def save(self):
        folder = os.path.dirname(self.path)
        try:
            fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def find_feed(self, url):
        for feed in self.feeds:
            if feed.get("url") == url:
                return feed
        return None

    def add_feed(self, url, name=""):
        url = (url or "").strip()
        if not url or self.find_feed(url) is not None:
            return None
        feed = {"url": url, "name": name or "", "enabled": True,
                "last_checked": 0, "items": [], "error": ""}
        self.feeds.append(feed)
        return feed

    def remove_feed(self, url):
        self.data["feeds"] = [f for f in self.feeds if f.get("url") != url]
        prefix = url + "|"
        self.data["seen"] = [k for k in self.seen if not k.startswith(prefix)]

    def update_feed(self, url, title, items, error=""):
        feed = self.find_feed(url)
        if feed is None:
            return
        if error:
            feed["error"] = error
            return
        if title and not feed.get("name"):
            feed["name"] = title
        feed["items"] = items
        feed["last_checked"] = int(time.time())
        feed["error"] = ""

    def mark_seen(self, keys):
        if not keys:
            return
        known = set(self.seen)
        for key in keys:
            if key not in known:
                self.seen.append(key)
                known.add(key)
        if len(self.seen) > MAX_SEEN:
            del self.seen[:len(self.seen) - MAX_SEEN]
