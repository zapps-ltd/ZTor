"""Torrent list columns.

The order below follows the classic column list of BitTorrent and uTorrent:
Name, Size, Downloaded, Uploaded, Status, Seeds, Peers, Down Speed, Up Speed,
ETA, Ratio, Availability, Flags, Priority, Label, Added On, Completed On, and so
on. Each column can be turned on or off and moved up or down individually.
"""

from . import format as fmt

LEFT = "left"
RIGHT = "right"


def _shared_name_and_speed(row):
    """The BitTorrent column that shows a name and its rate on one line."""
    name = row.name or ""
    rate = fmt.human_speed(row.download_rate or 0)
    return "%s  %s" % (name, rate) if rate else name


def _encryption_text(row):
    """Incoming and outgoing encryption, the way the old clients showed it."""
    value = getattr(row, "encryption_mode", None)
    if value in (0, "0", "", None):
        return "Off"
    if value in (1, "1"):
        return "Enabled"
    if value in (2, "2"):
        return "Forced"
    return str(value)


def _flag_word(row, name):
    value = getattr(row, name, None)
    if value is None:
        return ""
    return "yes" if value else "no"


class Column(object):
    """One column definition: how to render it and how to sort by it."""

    def __init__(self, key, label, width=110, align=LEFT, default_visible=False,
                 value=None, tip=""):
        self.key = key
        self.label = label
        self.width = width
        self.align = align
        self.default_visible = default_visible
        self.value = value or (lambda row: "")
        self.tip = tip

    def text(self, row):
        try:
            return self.value(row)
        except Exception:
            return ""

    def sort_key(self, row):
        text = self.text(row)
        return text.lower()


COLUMNS = [
    Column("name", "Name", 320, LEFT, True, lambda r: r.name,
           "Torrent name"),
    Column("name_then_speed", "Name, Speed", 380, LEFT, False,
           _shared_name_and_speed,
           "Name and download rate on one line, as BitTorrent shows it"),
    Column("size", "Size", 90, RIGHT, True, lambda r: r.size_text,
           "Total size of the torrent"),
    Column("downloaded", "Downloaded", 95, RIGHT, True,
           lambda r: r.downloaded_text, "Payload downloaded in total"),
    Column("uploaded", "Uploaded", 95, RIGHT, True,
           lambda r: r.uploaded_text, "Payload uploaded in total"),
    Column("status", "Status", 130, LEFT, True, lambda r: r.status_text,
           "Current state of the torrent"),
    Column("seeds", "Seeds", 75, RIGHT, True, lambda r: r.seeds_text,
           "Connected seeds and peers that are seeds"),
    Column("peers", "Peers", 75, RIGHT, True, lambda r: r.peers_text,
           "Connected peers"),
    Column("downspeed", "Down Speed", 95, RIGHT, True,
           lambda r: r.down_speed_text, "Current download rate"),
    Column("upspeed", "Up Speed", 95, RIGHT, True,
           lambda r: r.up_speed_text, "Current upload rate"),
    Column("eta", "ETA", 80, RIGHT, True, lambda r: r.eta_text,
           "Estimated time remaining"),
    Column("ratio", "Ratio", 70, RIGHT, True, lambda r: r.ratio_text,
           "Uploaded divided by downloaded"),
    Column("availability", "Availability", 100, RIGHT, False,
           lambda r: r.availability_text, "Copies of the torrent in the swarm"),
    Column("flags", "Flags", 70, LEFT, False, lambda r: r.flags_text,
           "E error, P paused, F force start, D downloading, S seeding, "
           "M super seeding, U upload only, H DHT off, X PEX off, L LSD off, "
           "I incoming connection"),
    Column("priority", "Priority", 85, LEFT, False,
           lambda r: r.priority_text, "Bandwidth priority of the torrent"),
    Column("label", "Label", 110, LEFT, False, lambda r: r.label,
           "Your own label for the torrent"),
    Column("added_on", "Added On", 130, LEFT, False, lambda r: r.added_text,
           "Date and time the torrent was added"),
    Column("completed_on", "Completed On", 130, LEFT, False,
           lambda r: r.completed_text, "Date and time the download finished"),
    Column("active_time", "Active Time", 100, RIGHT, False,
           lambda r: r.active_text, "Time spent actively downloading or seeding"),
    Column("time_active", "Time Active", 110, RIGHT, False,
           lambda r: r.time_active_text,
           "Time since the torrent was added to the session"),
    Column("time_remaining", "Time Remaining", 120, RIGHT, False,
           lambda r: r.time_remaining_text,
           "Same as ETA, named the way BitTorrent and uTorrent name it"),
    Column("downloaded_total", "Downloaded Total", 120, RIGHT, False,
           lambda r: r.downloaded_text,
           "All payload received for this torrent"),
    Column("uploaded_total", "Uploaded Total", 115, RIGHT, False,
           lambda r: r.uploaded_text,
           "All payload sent for this torrent"),
    Column("save_path", "Save Path", 220, LEFT, False, lambda r: r.save_path,
           "Folder the data is written to"),
    Column("dl_limit", "Down Limit", 90, RIGHT, False,
           lambda r: r.dl_limit_text, "Per torrent download limit"),
    Column("ul_limit", "Up Limit", 90, RIGHT, False,
           lambda r: r.ul_limit_text, "Per torrent upload limit"),
    Column("sequential", "Sequential", 90, LEFT, False,
           lambda r: r.sequential_text, "Sequential download turned on"),
    Column("super_seeding", "Super Seeding", 100, LEFT, False,
           lambda r: r.super_seeding_text, "Super seeding mode"),
    Column("connections", "Connections", 105, RIGHT, False,
           lambda r: r.connections_text, "Number of connected peers"),
    Column("remaining", "Remaining", 95, RIGHT, False,
           lambda r: r.remaining_text, "Bytes still wanted"),
    Column("progress", "Progress", 85, RIGHT, False,
           lambda r: r.progress_text, "Percentage downloaded"),
    Column("pieces", "Pieces", 110, RIGHT, False, lambda r: r.pieces_text,
           "Verified pieces out of the total"),
    Column("current_tracker", "Tracker", 200, LEFT, False,
           lambda r: r.tracker_text, "Tracker currently in use"),
    Column("info_hash", "Info Hash", 280, LEFT, False,
           lambda r: r.info_hash, "Info hash of the torrent"),
    Column("source", "Torrent File", 240, LEFT, False,
           lambda r: r.source, "Where the torrent was added from"),
    Column("comment", "Comment", 200, LEFT, False,
           lambda r: r.comment_text, "Comment stored in the torrent"),
    Column("error", "Error", 200, LEFT, False, lambda r: r.error_text,
           "Last error reported by libtorrent"),
    Column("dht", "DHT", 60, LEFT, False, lambda r: _flag_word(r, "dht"),
           "Distributed hash table turned on for this torrent"),
    Column("pex", "PEX", 60, LEFT, False, lambda r: _flag_word(r, "pex"),
           "Peer exchange turned on for this torrent"),
    Column("lsd", "Local Peer Discovery", 150, LEFT, False,
           lambda r: _flag_word(r, "lsd"),
           "Local peer discovery turned on for this torrent"),
    Column("encryption", "Encryption", 100, LEFT, False, _encryption_text,
           "Off, Enabled, or Forced for both directions"),
]

BY_KEY = dict((c.key, c) for c in COLUMNS)

DEFAULT_VISIBLE = [c.key for c in COLUMNS if c.default_visible]


def get(key):
    return BY_KEY.get(key)


def default_state():
    """Fresh column list: every defined column, with the classic ones visible."""
    return [{"key": c.key, "visible": c.default_visible, "width": c.width}
            for c in COLUMNS]


def normalize(state):
    """Merge a saved column state with the definitions, adding new columns."""
    known = set(BY_KEY)
    result = []
    used = set()
    for entry in state or []:
        key = entry.get("key")
        if key in known and key not in used:
            used.add(key)
            col = BY_KEY[key]
            result.append({
                "key": key,
                "visible": bool(entry.get("visible", col.default_visible)),
                "width": int(entry.get("width", col.width)),
            })
    for col in COLUMNS:
        if col.key not in used:
            result.append({"key": col.key, "visible": col.default_visible,
                           "width": col.width})
    return result


def visible_keys(state):
    return [entry["key"] for entry in state if entry.get("visible")]


def column_header(key):
    col = BY_KEY.get(key)
    return col.label if col else key


def column_align(key):
    col = BY_KEY.get(key)
    return col.align if col else LEFT
