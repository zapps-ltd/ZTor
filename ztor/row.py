"""A snapshot of one torrent, rendered for the list and the detail panes.

The engine thread fills these in; the GUI thread only reads finished rows, so no
libtorrent object is ever touched from the GUI.
"""

import time

from . import columns as colmod
from . import format as fmt


class TorrentRow(object):
    __slots__ = ("handle_id", "info_hash", "name", "label", "source", "save_path",
                 "size", "total_done", "total_wanted", "total_upload",
                 "total_download", "download_rate", "upload_rate", "progress",
                 "state", "has_metadata", "paused", "error", "num_seeds",
                 "num_peers", "num_connections", "num_complete", "num_incomplete",
                 "distributed_copies", "added_time", "completed_time",
                 "active_time", "seeding_time", "queue_position", "sequential",
                 "super_seeding", "auto_managed", "download_limit",
                 "upload_limit", "current_tracker", "num_pieces",
                 "verified_pieces", "comment", "priority", "finished",
                 "files", "trackers", "peers_list", "files_progress",
                 "files_priorities", "flags_value", "has_v2", "dht", "pex",
                 "lsd", "encryption_mode", "force_started", "stopped")

    def __init__(self):
        for name in self.__slots__:
            setattr(self, name, None)
        self.label = ""
        self.files = []
        self.trackers = []
        self.peers_list = []
        self.files_progress = []
        self.files_priorities = []
        self.comment = ""
        self.error = ""
        self.current_tracker = ""
        self.source = ""
        self.stopped = False
        self.save_path = ""
        self.num_pieces = 0
        self.verified_pieces = 0
        self.priority = 0
        self.flags_value = 0
        self.has_v2 = False
        self.dht = None
        self.pex = None
        self.lsd = None
        self.encryption_mode = None
        self.force_started = False

    # ------------------------------------------------------------ list fields
    @property
    def size_text(self):
        return fmt.human_size(self.size)

    @property
    def downloaded_text(self):
        if not self.has_metadata:
            return ""
        return fmt.human_size(self.total_download)

    @property
    def uploaded_text(self):
        if not self.has_metadata:
            return ""
        return fmt.human_size(self.total_upload)

    @property
    def down_speed_text(self):
        rate = self.download_rate or 0
        return fmt.human_speed(rate) if rate else ""

    @property
    def up_speed_text(self):
        rate = self.upload_rate or 0
        return fmt.human_speed(rate) if rate else ""

    @property
    def progress_text(self):
        if not self.has_metadata:
            return ""
        return fmt.percent(self.progress or 0.0)

    @property
    def remaining_text(self):
        if not self.has_metadata:
            return ""
        left = (self.total_wanted or 0) - (self.total_done or 0)
        return fmt.human_size(max(left, 0))

    @property
    def eta_text(self):
        if not self.has_metadata:
            return ""
        if self.state in ("seeding", "finished") or (self.progress or 0) >= 1.0:
            return ""
        rate = self.download_rate or 0
        if rate <= 0:
            return ""
        left = (self.total_wanted or 0) - (self.total_done or 0)
        if left <= 0:
            return ""
        return fmt.human_eta(left / float(rate))

    @property
    def ratio_text(self):
        down = self.total_download or 0
        up = self.total_upload or 0
        if not down:
            return "inf" if up else ""
        return fmt.ratio(up, down)

    @property
    def seeds_text(self):
        total = self.num_complete
        connected = self.num_seeds or 0
        if total is None or total < 0:
            return "%d connected" % connected
        return "%d of %d" % (connected, total)

    @property
    def peers_text(self):
        total = self.num_incomplete
        connected = self.num_peers or 0
        if total is None or total < 0:
            return "%d connected" % connected
        return "%d of %d" % (connected, total)

    @property
    def connections_text(self):
        return "%d" % (self.num_connections or 0) if self.num_connections else ""

    @property
    def availability_text(self):
        copies = self.distributed_copies
        if copies is None:
            return ""
        try:
            return "%.3f" % float(copies)
        except (TypeError, ValueError):
            return ""

    @property
    def pieces_text(self):
        return fmt.pieces_text(self.verified_pieces, self.num_pieces)

    @property
    def added_text(self):
        return fmt.timestamp(self.added_time)

    @property
    def completed_text(self):
        return fmt.timestamp(self.completed_time)

    @property
    def active_text(self):
        return fmt.human_duration(self.active_time)

    @property
    def dl_limit_text(self):
        return fmt.limit_text(self.download_limit)

    @property
    def ul_limit_text(self):
        return fmt.limit_text(self.upload_limit)

    @property
    def sequential_text(self):
        return "yes" if self.sequential else ""

    @property
    def super_seeding_text(self):
        return "yes" if self.super_seeding else ""

    @property
    def tracker_text(self):
        return self.current_tracker

    @property
    def comment_text(self):
        return self.comment

    @property
    def error_text(self):
        return self.error

    @property
    def priority_text(self):
        return {0: "normal", 1: "low", 2: "high"}.get(self.priority, "normal")

    @property
    def is_complete(self):
        """Every wanted piece is here: seeding, finished or at 100 percent."""
        if self.finished or self.state in ("seeding", "finished"):
            return True
        return bool(self.has_metadata) and (self.progress or 0) >= 1.0

    @property
    def status_text(self):
        if self.error:
            return "Error"
        if self.paused:
            if self.stopped:
                # Stop, not Pause: Finished once nothing is left to download.
                return "Finished" if self.is_complete else "Stopped"
            # Paused and still managed by the queue means waiting its turn.
            return "Queued" if self.auto_managed else "Paused"
        if not self.has_metadata:
            return "Downloading metadata"
        return {
            "queued_for_checking": "Queued for checking",
            "checking_files": "Checking files",
            "downloading_metadata": "Downloading metadata",
            "downloading": "Downloading",
            "finished": "Finished",
            "seeding": "Seeding",
            "allocating": "Allocating",
            "checking_resume_data": "Checking resume data",
        }.get(self.state, self.state or "")

    @property
    def flags_text(self):
        letters = []
        if self.error:
            letters.append("E")
        if self.paused:
            letters.append("P")
        if self.force_started:
            letters.append("F")
        if self.state == "downloading":
            letters.append("D")
        if self.state in ("seeding", "finished"):
            letters.append("S")
        if self.super_seeding:
            letters.append("M")
        if self.sequential:
            letters.append("Q")
        return " ".join(letters)

    @property
    def time_active_text(self):
        """Seconds since the torrent was added, the uTorrent reading."""
        added = self.added_time or 0
        if not added:
            return ""
        return fmt.human_duration(max(0, time.time() - added))

    @property
    def time_remaining_text(self):
        return self.eta_text

    # ---------------------------------------------------------- spoken fields
    @property
    def announce_name(self):
        return self.name or self.info_hash or "torrent"

    def state_phrase(self):
        text = self.status_text
        if self.has_metadata and self.progress is not None:
            return "%s, %s" % (text, fmt.percent(self.progress))
        return text

    def summary_line(self):
        """One line summary for the status bar, uTorrent style."""
        parts = [self.announce_name, self.state_phrase()]
        if self.down_speed_text:
            parts.append("down " + self.down_speed_text)
        if self.up_speed_text:
            parts.append("up " + self.up_speed_text)
        if self.eta_text:
            parts.append("eta " + self.eta_text)
        if self.num_seeds is not None:
            parts.append("%s seeds, %s peers" % (self.seeds_text, self.peers_text))
        if self.ratio_text:
            parts.append("ratio " + self.ratio_text)
        return ". ".join(parts)


def sort_key_for(key):
    col = colmod.get(key)
    if col is None:
        return lambda row: ""
    return col.sort_key
