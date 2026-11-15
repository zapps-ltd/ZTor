"""Torrent search. No wx here.

Built in: the Internet Archive, whose items are free to share and each come
with a torrent file. Optional: Jackett or Prowlarr, through their Torznab
address and API key, so users can search the indexers they set up there.
"""

import json
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .rss import _get, _local, _text

MAX_BYTES = 4 * 1024 * 1024


def providers(config=None):
    result = [("archive", "Internet Archive")]
    if config and (config.get("torznab_url") or "").strip():
        result.append(("torznab", "Jackett or Prowlarr"))
    return result


# Kept for older callers; the dialog asks providers(config).
PROVIDERS = providers()


def search(provider, query, limit=50, config=None):
    query = (query or "").strip()
    if not query:
        raise ValueError("type something to search for")
    if provider == "archive":
        return archive_search(query, limit)
    if provider == "torznab":
        return torznab_search(query, config or {}, limit)
    raise ValueError("unknown search source")


# ---------------------------------------------------------- Internet Archive
def archive_url(query, limit=50):
    params = [("q", "(%s) AND -mediatype:collection" % query)]
    params += [("fl[]", field) for field in ("identifier", "title",
                                              "item_size", "publicdate")]
    params += [("rows", str(int(limit))), ("output", "json")]
    return "https://archive.org/advancedsearch.php?" + \
        urllib.parse.urlencode(params)


def archive_search(query, limit=50):
    return parse_archive(_get(archive_url(query, limit), MAX_BYTES))


def parse_archive(data):
    payload = json.loads(data.decode("utf-8", "replace")
                         if isinstance(data, bytes) else data)
    docs = (payload.get("response") or {}).get("docs") or []
    results = []
    for doc in docs:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        title = doc.get("title") or identifier
        if isinstance(title, list):
            title = " ".join(str(t) for t in title)
        try:
            size = int(doc.get("item_size") or 0)
        except (TypeError, ValueError):
            size = 0
        quoted = urllib.parse.quote(identifier, safe="")
        results.append({
            "title": str(title),
            "size": size,
            "seeds": None,
            "date": str(doc.get("publicdate", "") or "")[:10],
            "link": "https://archive.org/download/%s/%s_archive.torrent"
                    % (quoted, quoted),
            "page": "https://archive.org/details/%s" % quoted,
            "source": "Internet Archive",
        })
    return results


# ------------------------------------------------------ Jackett, Prowlarr
def torznab_url(base, key, query, limit=50):
    base = (base or "").strip().rstrip("/")
    if not base.lower().startswith(("http://", "https://")):
        raise ValueError("the Jackett or Prowlarr address must start with "
                         "http:// or https://")
    if not base.lower().endswith("/api"):
        base += "/api"
    params = [("t", "search"), ("q", query), ("limit", str(int(limit)))]
    if key:
        params.insert(0, ("apikey", key))
    return base + "?" + urllib.parse.urlencode(params)


def torznab_search(query, config, limit=50):
    url = torznab_url(config.get("torznab_url", ""),
                      config.get("torznab_key", ""), query, limit)
    return parse_torznab(_get(url, MAX_BYTES))


def _short_date(text):
    try:
        return parsedate_to_datetime(text).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return (text or "")[:16]


def _number(text):
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_torznab(data):
    root = ET.fromstring(data)
    if _local(root.tag) == "error":
        raise ValueError(root.get("description") or
                         "the indexer returned an error")
    results = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        entry = {"title": "", "size": 0, "seeds": None, "date": "",
                 "link": "", "page": "", "source": ""}
        attrs = {}
        link = enclosure = ""
        for child in item:
            name = _local(child.tag)
            if name == "title":
                entry["title"] = _text(child)
            elif name == "link":
                link = _text(child)
            elif name == "comments":
                entry["page"] = _text(child)
            elif name == "guid" and not entry["page"] and \
                    _text(child).lower().startswith("http"):
                entry["page"] = _text(child)
            elif name == "size":
                entry["size"] = _number(_text(child)) or 0
            elif name == "pubdate":
                entry["date"] = _short_date(_text(child))
            elif name in ("jackettindexer", "prowlarrindexer"):
                entry["source"] = _text(child)
            elif name == "enclosure":
                enclosure = child.get("url", "") or ""
                if not entry["size"]:
                    entry["size"] = _number(child.get("length")) or 0
            elif name == "attr":
                attrs[(child.get("name") or "").lower()] = child.get("value", "")
        entry["link"] = attrs.get("magneturl") or enclosure or link
        if not entry["size"]:
            entry["size"] = _number(attrs.get("size")) or 0
        entry["seeds"] = _number(attrs.get("seeders"))
        entry["source"] = entry["source"] or "Indexer"
        if entry["title"] and entry["link"]:
            results.append(entry)
    results.sort(key=lambda r: r["seeds"] if r["seeds"] is not None else -1,
                 reverse=True)
    return results
