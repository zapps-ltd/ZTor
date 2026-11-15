"""The main window: torrent list, filters, tabs, menus, and status bar.

Nothing in this file touches libtorrent directly. It reads plain snapshots from
the engine thread and posts commands back through a queue.
"""

import os
import time

import wx
import wx.adv

from . import APP_NAME, __version__
from . import cleanup
from . import columns as colmod
from . import dialogs
from . import format as fmt
from . import graph
from . import paths
from . import trackers as trackmod
from . import tuning
from .a11y import add_menu_letters, motion_enabled, touch_mode
from .accessibility import announcer, high_contrast
from .icons import image_list, make_image_list, make_icon, tool_bitmap
from .rss_dialog import RssController
from .tools_dialog import CreateTorrentDialog, SearchDialog
from .webui import WebRemote
from .drop_target import TorrentDropTarget
from .listview import TorrentList
from .store import store


PRIORITY_NAMES = {0: "Normal", 1: "Low", 2: "High"}

# wxPython does not always expose the Application key, so name it here.
WXK_APPS = getattr(wx, "WXK_APPS", 0x5D)

# Text colours for the torrent list, (light theme, dark theme).
STATUS_COLOURS = {
    "downloading": ((0, 90, 180), (120, 180, 255)),
    "seeding": ((0, 120, 50), (110, 210, 130)),
    "completed": ((0, 105, 95), (90, 205, 185)),
    "paused": ((105, 105, 105), (165, 165, 165)),
    "error": ((185, 0, 0), (255, 120, 120)),
    "checking": ((150, 85, 0), (235, 185, 90)),
}

# Filter tree icons; the index of each kind is its image number.
FILTER_KINDS = ("all", "down", "up", "finished", "paused", "error", "queued",
                "tag")
FILTER_ICON = {"all": 0, "active": 1, "downloading": 1, "seeding": 2,
               "completed": 3, "paused": 4, "error": 5, "inactive": 6,
               "labels": 7}

# Status bar fields: message, speeds, speed mode, torrent count.
STATUS_WIDTHS = [-3, -3, -1, -2]


def _stopped_complete(row):
    """Stopped after the download completed: listed as Finished."""
    return bool(getattr(row, "stopped", False)) and row.is_complete


class GlobalLimitsDialog(wx.Dialog):
    """Session wide download and upload limits."""

    def __init__(self, parent, dl, ul):
        wx.Dialog.__init__(self, parent, title="Global Speed Limits",
                           size=(470, 250))
        self.result = None
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.dl = wx.SpinCtrl(panel, min=0, max=1000000, initial=max(0, dl) // 1000)
        self.dl.SetName("Download limit in kilobytes per second")
        self.ul = wx.SpinCtrl(panel, min=0, max=1000000, initial=max(0, ul) // 1000)
        self.ul.SetName("Upload limit in kilobytes per second")
        sizer.Add(dialogs.label_before(wx.StaticText(
            panel, label="Download limit, KB per second, zero is unlimited:"),
            self.dl), 0, wx.ALL, 10)
        sizer.Add(self.dl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(dialogs.label_before(wx.StaticText(
            panel, label="Upload limit, KB per second, zero is unlimited:"),
            self.ul), 0, wx.ALL, 10)
        sizer.Add(self.ul, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        unlimited = wx.Button(panel, label="Set both to &unlimited")
        sizer.Add(unlimited, 0, wx.ALL, 10)
        ok = wx.Button(panel, wx.ID_OK, label="OK")
        cancel = wx.Button(panel, wx.ID_CANCEL, label="Cancel")
        ok.SetDefault()
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(ok, 0, wx.RIGHT, 8)
        row.Add(cancel, 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)
        unlimited.Bind(wx.EVT_BUTTON, lambda e: (self.dl.SetValue(0),
                                                self.ul.SetValue(0)))
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, event):
        self.result = (self.dl.GetValue() * 1000, self.ul.GetValue() * 1000)
        event.Skip()


class MainFrame(wx.Frame):
    def __init__(self, engine, config):
        wx.Frame.__init__(self, None, title=APP_NAME,
                          size=(config["window"]["w"], config["window"]["h"]))
        self.engine = engine
        self.config = config
        self.store_handle = store()
        self.column_state = colmod.normalize(config.get("columns"))
        self.rows = []
        self.all_rows = []
        self.visible_keys = colmod.visible_keys(self.column_state)
        self.sort_key = config.get("sort_column", "name")
        self.sort_reverse = bool(config.get("sort_reverse", False))
        self.current_filter = config.get("category_filter", "all")
        self.current_label = config.get("label_filter", "all")
        self.search_text = ""
        self.log_lines = []
        self.selected_hash = None
        self.details_hash = None
        self.details = {}
        self.last_signature = None
        self.session_stats = {}
        self.tracker_status = {}
        self._closed = False
        self._pending_clean = {}
        self._filter_items = {}
        self._watch_seen = set()
        self._watch_timer = None
        self._schedule_timer = None
        self._speed_state = None
        self._stats_ticks = 0
        self._attr_cache = {}
        self._high_contrast = False
        self._use_colours = False
        self.toolbar = None
        self.alt_tool_id = None
        self.alt_menu_item = None
        self.rss = None
        self.remote = None
        self._remote_sig = None
        self.speed_history = graph.SpeedHistory()
        # Touch mode: taller rows, bigger buttons, the action bar.
        self.touch = touch_mode(config)
        self.motion = motion_enabled(config)
        self._list_px = self._dip(24 if self.touch else 16)
        self._tool_px = self._dip(32 if self.touch else 24)

        self._make_icons()
        self._build_ui()
        self._build_menu()
        self._build_accelerators()
        self._build_toolbar()
        self._bind_events()
        self._build_tray()
        self._refresh_colour_settings()
        self._restore_window()
        self._start_timer()
        self.rss = RssController(self)
        self._refresh_trackers()
        self.remote = WebRemote(self)
        self._start_remote(announce=False)
        self.announce("%s ready. Press Alt for the menu bar." % APP_NAME)

    # ------------------------------------------------------------------ UI
    def _dip(self, pixels):
        """Pixels scaled for the display, so icons stay sharp at any scale."""
        try:
            return int(self.FromDIP(pixels))
        except Exception:
            return pixels

    def _make_icons(self):
        self.icon_paused = make_icon("paused")
        self.icon_down = make_icon("down")
        self.icon_up = make_icon("up")
        self.icon_error = make_icon("error")
        self.icon_app = make_icon("app")
        if self.icon_app is not None:
            self.SetIcon(self.icon_app)
        self.image_list = make_image_list(self._list_px)
        self.filter_images = image_list(FILTER_KINDS, self._dip(16))

    def _build_ui(self):
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        top = wx.Panel(panel)
        self.top_panel = top
        top_sizer = wx.BoxSizer(wx.VERTICAL)

        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_row.Add(wx.StaticText(top, label="&Filter torrents:"), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.search = wx.SearchCtrl(top, style=wx.TE_PROCESS_ENTER)
        self.search.SetName("Filter torrents by name, label, or info hash")
        self.search.ShowCancelButton(True)
        search_row.Add(self.search, 1)
        top_sizer.Add(search_row, 0, wx.EXPAND | wx.ALL, 6)

        self.v_split = wx.SplitterWindow(top, style=wx.SP_LIVE_UPDATE)
        self.v_split.SetName("Filters and torrents")

        side = wx.Panel(self.v_split)
        side_sizer = wx.BoxSizer(wx.VERTICAL)
        side_sizer.Add(wx.StaticText(side, label="&Filters:"), 0, wx.ALL, 4)
        self.filter_tree = wx.TreeCtrl(
            side, style=wx.TR_HIDE_ROOT | wx.TR_HAS_BUTTONS | wx.TR_SINGLE |
                        wx.TR_LINES_AT_ROOT)
        self.filter_tree.SetName("Filters")
        if self.filter_images is not None:
            self.filter_tree.SetImageList(self.filter_images)
        side_sizer.Add(self.filter_tree, 1, wx.EXPAND | wx.ALL, 4)
        side.SetSizer(side_sizer)

        right = wx.Panel(self.v_split)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        self.list = TorrentList(right, self)
        self.list.SetName("Torrent list")
        if self.image_list is not None:
            self.list.SetImageList(self.image_list, wx.IMAGE_LIST_SMALL)
        if hasattr(self.list, "EnableAlternateRowColours"):
            self.list.EnableAlternateRowColours(True)
        right_sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 4)
        self._build_action_bar(right, right_sizer)
        right.SetSizer(right_sizer)

        self.v_split.SplitVertically(side, right, 200)
        self.v_split.SetMinimumPaneSize(120)
        self.v_split.SetSashGravity(0.0)

        self.detail_book = wx.Notebook(top)
        self.detail_book.SetName("Torrent details")
        self._build_tabs(self.detail_book)

        top_sizer.Add(self.v_split, 3, wx.EXPAND)
        self.progress_strip = graph.ProgressStrip(top)
        self.progress_strip.animate = self.motion
        top_sizer.Add(self.progress_strip, 0,
                      wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)
        top_sizer.Add(self.detail_book, 2, wx.EXPAND | wx.TOP, 4)
        top.SetSizer(top_sizer)
        # No empty filler panels: anything that can take focus but holds
        # nothing traps the Tab key.
        self.show_details = True

        outer.Add(top, 1, wx.EXPAND)
        panel.SetSizer(outer)

        self.status = self.CreateStatusBar(len(STATUS_WIDTHS))
        self.status.SetStatusWidths(STATUS_WIDTHS)
        self.status.SetStatusText("Starting session...", 0)
        # The status bar shows each part's text as its own tooltip; a
        # tooltip set by hand is refused by wxWidgets.
        self.status.Bind(wx.EVT_LEFT_UP, self._on_status_click)

        self._rebuild_columns()
        self._rebuild_filter_tree()

    def _build_action_bar(self, parent, sizer):
        """Big buttons for mouse and touch under the torrent list. They are
        ordinary labelled buttons, and every one is also a menu command."""
        self.action_bar = wx.Panel(parent)
        self.action_bar.SetName("Torrent actions")
        row = wx.BoxSizer(wx.HORIZONTAL)
        for kind, label, tip, handler in (
                ("start", "Start", "Start the selection", self.on_start),
                ("pause", "Pause", "Pause the selection", self.on_pause),
                ("remove", "Remove", "Remove the selection, keep the data",
                 self.on_remove),
                ("all", "Files", "Choose which files to download",
                 self.on_files_dialog),
                ("folder", "Open Folder", "Open the download folder",
                 self.on_open_folder)):
            button = wx.Button(self.action_bar, label=label)
            try:
                button.SetBitmap(tool_bitmap(kind, self._dip(
                    24 if self.touch else 16)))
                button.SetBitmapMargins(self._dip(4), 0)
            except Exception:
                pass
            button.SetToolTip(tip)
            if self.touch:
                button.SetMinSize((-1, self._dip(44)))
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 1, wx.RIGHT, 4)
        self.action_bar.SetSizer(row)
        sizer.Add(self.action_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        self.action_bar.Show(self._action_bar_wanted())

    def _action_bar_wanted(self):
        return bool(self.touch or self.config.get("show_action_bar"))

    def on_toggle_action_bar(self, event):
        value = not bool(self.config.get("show_action_bar"))
        self.config["show_action_bar"] = value
        self.store_handle.settings["show_action_bar"] = value
        self.store_handle.save()
        self.action_bar.Show(self._action_bar_wanted())
        self.action_bar.GetParent().Layout()
        self.announce("Action buttons %s" % (
            "shown" if self._action_bar_wanted() else "hidden"))

    # ------------------------------------------------------- status bar
    def _status_action(self, field):
        """What a click on a status bar field does."""
        if field == 1:
            self.show_detail_tab(self.detail_book.GetPageCount() - 1)
            return "graph"
        if field == 2:
            self.on_toggle_alt_speed(None)
            return "speed mode"
        if field == 3:
            self.on_statistics(None)
            return "statistics"
        return ""

    def _on_status_click(self, event):
        position = event.GetPosition()
        for field in range(self.status.GetFieldsCount()):
            rect = self.status.GetFieldRect(field)
            if isinstance(rect, tuple):     # some builds return (ok, rect)
                rect = rect[-1]
            if rect.Contains(position):
                self._status_action(field)
                break
        event.Skip()

    def _on_tab_changed(self, event):
        """Say which tab is open and how much is in it."""
        try:
            index = event.GetSelection()
        except Exception:
            index = self.detail_book.GetSelection()
        if not 0 <= index < self.detail_book.GetPageCount():
            return
        title = self.detail_book.GetPageText(index)
        row = self.current_row()
        if row is None:
            self.announce("%s tab" % title)
            return
        counts = {
            "Torrents": len(self.general),
            "Files": len(row.files),
            "Peers": len(row.peers_list),
            "Trackers": len(row.trackers),
            "Speed": len(self.speed),
            "Log": len(self.log_lines),
        }
        count = counts.get(title)
        if count is None:
            self.announce("%s tab" % title)
        elif count == 0:
            self.announce("%s tab, empty" % title)
        else:
            self.announce("%s tab, %d item%s"
                          % (title, count, "" if count == 1 else "s"))
        event.Skip()

    def _build_tabs(self, book):
        self.general = wx.ListCtrl(book, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.general.InsertColumn(0, "Property", width=230)
        self.general.InsertColumn(1, "Value", width=560)
        self.general.SetName("Torrent information")
        book.AddPage(self.general, "Torrents")

        self.files = wx.ListCtrl(book, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        for index, (title, width) in enumerate((("File", 420), ("Size", 100),
                                                ("Downloaded", 110),
                                                ("Progress", 90),
                                                ("Priority", 100))):
            self.files.InsertColumn(index, title, width=width)
        self.files.SetName("Files")
        book.AddPage(self.files, "Files")

        self.peers = wx.ListCtrl(book, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        for index, (title, width) in enumerate((("Address", 170),
                                                ("Client", 170), ("Flags", 110),
                                                ("Progress", 90),
                                                ("Down Speed", 100),
                                                ("Up Speed", 100),
                                                ("Downloaded", 110),
                                                ("Uploaded", 110))):
            self.peers.InsertColumn(index, title, width=width)
        self.peers.SetName("Peers")
        book.AddPage(self.peers, "Peers")

        self.trackers = wx.ListCtrl(book, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        for index, (title, width) in enumerate((("Tracker", 460), ("Tier", 70),
                                                ("Status", 240))):
            self.trackers.InsertColumn(index, title, width=width)
        self.trackers.SetName("Trackers")
        book.AddPage(self.trackers, "Trackers")

        self.speed = wx.ListCtrl(book, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.speed.InsertColumn(0, "Item", width=240)
        self.speed.InsertColumn(1, "Value", width=320)
        self.speed.SetName("Speed")
        book.AddPage(self.speed, "Speed")

        self.log = wx.TextCtrl(book, style=wx.TE_MULTILINE | wx.TE_READONLY |
                                            wx.TE_RICH2 | wx.BORDER_SUNKEN)
        self.log.SetName("Session log")
        book.AddPage(self.log, "Log")

        self.graph_page = wx.Panel(book)
        graph_sizer = wx.BoxSizer(wx.VERTICAL)
        self.speed_summary = wx.TextCtrl(
            self.graph_page, value=self.speed_history.summary(),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SUNKEN,
            size=(-1, 52))
        self.speed_summary.SetName("Speed summary")
        graph_sizer.Add(self.speed_summary, 0, wx.EXPAND | wx.ALL, 4)
        self.speed_graph = graph.SpeedGraph(self.graph_page, self.speed_history)
        graph_sizer.Add(self.speed_graph, 1, wx.EXPAND | wx.ALL, 4)
        self.graph_page.SetSizer(graph_sizer)
        book.AddPage(self.graph_page, "Graph")

        book.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_tab_changed)

    # --------------------------------------------------------------- menus
    def _build_menu(self):
        bar = wx.MenuBar()
        self.menu_bar = bar

        file_menu = wx.Menu()
        self._item(file_menu, "&Add Torrent File...\tCtrl+O",
                   "Open a torrent file", self.on_add_file)
        self._item(file_menu, "Add &Magnet Link or URL...\tCtrl+N",
                   "Add a magnet link or URL", self.on_add_magnet)
        self._item(file_menu, "Add &Paused...\tCtrl+Shift+N",
                   "Add without starting", self.on_add_paused)
        self._item(file_menu, "Sea&rch...\tCtrl+E",
                   "Search for torrents to add", self.on_search)
        self._item(file_menu, "&Create Torrent...\tCtrl+Shift+M",
                   "Make a torrent file from a file or folder",
                   self.on_create_torrent)
        file_menu.AppendSeparator()
        self._item(file_menu, "&Import Torrent Files from Folder...",
                   "Add every torrent file in a folder", self.on_import_folder)
        self._item(file_menu, "&Export Selected Torrent Files...",
                   "Copy the torrent files of the selection",
                   self.on_export_torrent)
        file_menu.AppendSeparator()
        self._item(file_menu, "&Save Resume Data Now",
                   "Write resume data for every torrent", self.on_save_resume)
        file_menu.AppendSeparator()
        self._item(file_menu, "E&xit\tAlt+F4", "Close ZTor",
                   lambda e: self.Close())
        bar.Append(file_menu, "&File")

        edit_menu = wx.Menu()
        self._item(edit_menu, "Select &All",
                   "Select every torrent. Press Control A while the torrent list has focus",
                   self.on_select_all)
        self._item(edit_menu, "&Deselect All", "Clear the selection",
                   self.on_select_none)
        self._item(edit_menu, "&Invert Selection", "Swap selected and unselected",
                   self.on_invert_selection)
        edit_menu.AppendSeparator()
        self._item(edit_menu, "&Filter Torrents\tCtrl+F",
                   "Jump to the filter box", self.on_focus_search)
        self._item(edit_menu, "Clear Filter",
                   "Clear the filter box. Press Escape while the filter box or the torrent list has focus",
                   self.on_search_clear)
        bar.Append(edit_menu, "&Edit")

        torrent_menu = wx.Menu()
        self._item(torrent_menu, "&Start\tCtrl+S", "Resume the selection",
                   self.on_start)
        self._item(torrent_menu, "&Pause\tCtrl+P", "Pause the selection",
                   self.on_pause)
        torrent_menu.AppendSeparator()
        self._item(torrent_menu, "&Force Start",
                   "Ignore queueing and start now", self.on_force_start)
        self._item(torrent_menu, "Force &Recheck", "Recheck downloaded data",
                   self.on_force_recheck)
        self._item(torrent_menu, "Force Re&announce",
                   "Ask the trackers for peers now", self.on_reannounce)
        torrent_menu.AppendSeparator()

        queue_menu = wx.Menu()
        self._item(queue_menu, "Move to &Top", "Move to the top of the queue",
                   lambda e: self.on_queue("top"))
        self._item(queue_menu, "Move &Up", "Move up one place",
                   lambda e: self.on_queue("up"))
        self._item(queue_menu, "Move &Down", "Move down one place",
                   lambda e: self.on_queue("down"))
        self._item(queue_menu, "Move to &Bottom", "Move to the bottom",
                   lambda e: self.on_queue("bottom"))
        torrent_menu.AppendSubMenu(queue_menu, "&Queue")

        priority_menu = wx.Menu()
        for label, value in (("&Low", 1), ("&Normal", 0), ("&High", 2)):
            self._item(priority_menu, label,
                       "Bandwidth priority for the selection",
                       lambda e, v=value: self.set_priority(v))
        torrent_menu.AppendSubMenu(priority_menu, "&Priority")

        self.label_menu = wx.Menu()
        torrent_menu.AppendSubMenu(self.label_menu, "&Label")
        self._rebuild_label_menu()

        torrent_menu.AppendSeparator()
        self._item(torrent_menu, "Set &Speed Limits...\tF3",
                   "Per torrent download and upload limits",
                   self.on_speed_limits)
        self._item(torrent_menu, "&Files...",
                   "Choose which files to download. Press Enter while the torrent list has focus",
                   self.on_files_dialog)
        self._item(torrent_menu, "&Trackers...",
                   "View and add trackers and web seeds",
                   self.on_trackers_dialog)
        self._item(torrent_menu, "Mo&ve Data Folder...",
                   "Move the downloaded data", self.on_move_storage)
        self._item(torrent_menu, "Open &Containing Folder",
                   "Open the download folder", self.on_open_folder)
        torrent_menu.AppendSeparator()
        self._item(torrent_menu, "&Remove",
                   "Remove the torrent, keep the data. Press Delete while the torrent list has focus",
                   self.on_remove)
        self._item(torrent_menu, "Remove and &Delete Data",
                   "Remove the torrent and delete its files. Press Shift Delete while the torrent list has focus",
                   self.on_remove_delete)
        torrent_menu.AppendSeparator()
        self._item(torrent_menu, "Copy &Magnet Link",
                   "Put a magnet link on the clipboard",
                   lambda e: self.copy_field("magnet"))
        self._item(torrent_menu, "Copy &Info Hash",
                   "Put the info hash on the clipboard",
                   lambda e: self.copy_field("hash"))
        self._item(torrent_menu, "Copy &Name",
                   "Put the torrent name on the clipboard",
                   lambda e: self.copy_field("name"))
        self._item(torrent_menu, "Copy Sa&ve Path",
                   "Put the folder on the clipboard",
                   lambda e: self.copy_field("path"))
        bar.Append(torrent_menu, "&Torrent")

        view_menu = wx.Menu()
        self._item(view_menu, "&Columns...\tCtrl+Shift+C",
                   "Choose which columns are shown and their order",
                   self.on_columns)
        self._item(view_menu, "Read Selected Torrent\tCtrl+Shift+D",
                   "Speak the selected torrent with every visible column",
                   self.on_read_selected)
        self._item(view_menu, "&Refresh Now\tF5",
                   "Refresh the list immediately", self.on_refresh)
        self._item(view_menu, "Speak Speed S&ummary\tCtrl+Shift+S",
                   "Current, average and peak speeds of the last few minutes",
                   self.on_speak_speed)
        view_menu.AppendSeparator()

        sort_menu = wx.Menu()
        for key in ("name", "size", "downloaded", "uploaded", "status", "seeds",
                    "peers", "downspeed", "upspeed", "eta", "ratio", "progress",
                    "added_on", "queue_position"):
            col = colmod.get(key)
            if col is None:
                continue
            self._item(sort_menu, col.label, "Sort by %s" % col.label,
                       lambda e, k=key: self.set_sort(k))
        self._item(sort_menu, "Reverse Order", "Flip the sort direction",
                   self.on_reverse_sort)
        view_menu.AppendSubMenu(sort_menu, "&Sort By")
        self._item(view_menu, "Clear Sort", "Return to the queue order",
                   self.on_clear_sort)
        view_menu.AppendSeparator()
        self._item(view_menu, "Show &Details Panel",
                   "Show or hide the tabbed detail panel",
                   self.on_toggle_details)
        self._item(view_menu, "Show &Filter Panel",
                   "Show or hide the filter tree", self.on_toggle_filters)
        self._item(view_menu, "Show Status &Bar", "Show or hide the status bar",
                   self.on_toggle_statusbar)
        self._item(view_menu, "Show &Toolbar", "Show or hide the toolbar",
                   self.on_toggle_toolbar)
        self._item(view_menu, "Show &Action Buttons",
                   "Show or hide the big buttons under the torrent list",
                   self.on_toggle_action_bar)
        view_menu.AppendSeparator()
        self._item(view_menu, "&Statistics...", "Session counters",
                   self.on_statistics)
        bar.Append(view_menu, "&View")

        options_menu = wx.Menu()
        self._item(options_menu, "&Preferences...\tCtrl+,",
                   "Open the preferences dialog", self.on_options)
        self._item(options_menu, "&Columns...", "Choose which columns are shown",
                   self.on_columns)
        options_menu.AppendSeparator()
        self._item(options_menu, "Global &Speed Limits...",
                   "Limits for the whole session", self.on_global_limits)
        self.alt_menu_item = options_menu.AppendCheckItem(
            wx.ID_ANY, "&Alternative Speed Limits\tCtrl+Shift+T",
            "Switch between the normal and the alternative speed limits")
        self.Bind(wx.EVT_MENU, self.on_toggle_alt_speed, self.alt_menu_item)
        self._item(options_menu, "&RSS Feeds...\tCtrl+R",
                   "Feeds and automatic download rules", self.on_rss)
        self._item(options_menu, "&Web Remote Address...",
                   "Show the web remote addresses and copy the one for a phone",
                   self.on_remote_address)
        self._item(options_menu, "&Start All", "Start all torrents",
                   self.on_start_all)
        self._item(options_menu, "Pause A&ll", "Pause all torrents",
                   self.on_pause_all)
        options_menu.AppendSeparator()
        self._item(options_menu, "&Speech Announcements",
                   "Speak confirmations and notices", self.on_toggle_speech)
        bar.Append(options_menu, "&Options")

        help_menu = wx.Menu()
        self._item(help_menu, "&Keyboard Shortcuts",
                   "List the keyboard commands", self.on_shortcuts)
        self._item(help_menu, "&About", "About ZTor", self.on_about)
        bar.Append(help_menu, "&Help")

        self.SetMenuBar(bar)

    def _item(self, menu, label, help_text, handler):
        item = menu.Append(wx.ID_ANY, label, help_text)
        self.Bind(wx.EVT_MENU, handler, item)
        return item

    def _build_accelerators(self):
        # Only keys that mean nothing while editing text are global.
        # Control A, Delete, Shift Delete and Enter are deliberately left out
        # so they keep their normal meaning inside the filter box; the list
        # handles them itself, in _on_list_key.
        entries = [
            (wx.ACCEL_CTRL, ord("O"), "add_file"),
            (wx.ACCEL_CTRL, ord("N"), "add_magnet"),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("N"), "add_paused"),
            (wx.ACCEL_CTRL, ord("F"), "find"),
            (wx.ACCEL_CTRL, ord("S"), "start"),
            (wx.ACCEL_CTRL, ord("P"), "pause"),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("C"), "columns"),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("D"), "read"),
            (wx.ACCEL_CTRL, ord(","), "options"),
            (wx.ACCEL_NORMAL, wx.WXK_F5, "refresh"),
            (wx.ACCEL_NORMAL, wx.WXK_F3, "speed_limits"),
            (wx.ACCEL_NORMAL, wx.WXK_F2, "rename"),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("T"), "alt_speed"),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("S"), "speed_summary"),
            (wx.ACCEL_CTRL, ord("R"), "rss"),
            (wx.ACCEL_CTRL, ord("E"), "search"),
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("M"), "create"),
        ]
        ids = dict((name, wx.NewIdRef()) for _m, _k, name in entries)
        table = [(mod, key, ids[name]) for mod, key, name in entries]
        self.SetAcceleratorTable(wx.AcceleratorTable(table))
        self.accelerator_entries = list(entries)
        handlers = {
            "add_file": self.on_add_file,
            "add_magnet": self.on_add_magnet,
            "add_paused": self.on_add_paused,
            "find": self.on_focus_search,
            "start": self.on_start,
            "pause": self.on_pause,
            "columns": self.on_columns,
            "read": self.on_read_selected,
            "options": self.on_options,
            "refresh": self.on_refresh,
            "speed_limits": self.on_speed_limits,
            "rename": self.on_rename_torrent,
            "alt_speed": self.on_toggle_alt_speed,
            "speed_summary": self.on_speak_speed,
            "rss": self.on_rss,
            "search": self.on_search,
            "create": self.on_create_torrent,
        }
        for name, handler in handlers.items():
            self.Bind(wx.EVT_MENU, handler, ids[name])

    def _build_tray(self):
        self.tray = None
        if not hasattr(wx.adv, "TaskBarIcon"):
            return
        frame = self

        class Tray(wx.adv.TaskBarIcon):
            def __init__(self):
                wx.adv.TaskBarIcon.__init__(self)
                if frame.icon_app is not None:
                    self.SetIcon(frame.icon_app, "%s torrent client" % APP_NAME)
                menu = wx.Menu()
                item_show = menu.Append(wx.ID_ANY, "&Show ZTor")
                item_pause = menu.Append(wx.ID_ANY, "&Pause All")
                item_resume = menu.Append(wx.ID_ANY, "Resume A&ll")
                menu.AppendSeparator()
                item_quit = menu.Append(wx.ID_ANY, "E&xit")
                add_menu_letters(menu)
                self.menu = menu
                self.Bind(wx.EVT_MENU, lambda e: frame.restore_window(), item_show)
                self.Bind(wx.EVT_MENU, lambda e: frame.on_pause_all(None),
                          item_pause)
                self.Bind(wx.EVT_MENU, lambda e: frame.on_start_all(None),
                          item_resume)
                self.Bind(wx.EVT_MENU, lambda e: frame.Close(), item_quit)
                self.Bind(wx.EVT_TASKBAR_LEFT_DCLICK,
                          lambda e: frame.restore_window())

            def CreatePopupMenu(self):
                return self.menu

        try:
            self.tray = Tray()
        except Exception:
            self.tray = None

    # ------------------------------------------------------- filter tree
    def _rebuild_filter_tree(self):
        self.filter_tree.DeleteAllItems()
        root = self.filter_tree.AddRoot("root")
        counts = self._counts()
        self._filter_items = {}

        node = self.filter_tree.AppendItem(
            root, "All Torrents (%d)" % counts["all"], FILTER_ICON["all"])
        self._filter_items[node] = ("all", None)
        status_root = self.filter_tree.AppendItem(root, "Status",
                                                  FILTER_ICON["all"])
        for key, label in (("active", "Active"), ("downloading", "Downloading"),
                           ("seeding", "Seeding"), ("completed", "Completed"),
                           ("paused", "Paused"), ("error", "Error"),
                           ("inactive", "Inactive")):
            item = self.filter_tree.AppendItem(
                status_root, "%s (%d)" % (label, counts.get(key, 0)),
                FILTER_ICON.get(key, -1))
            self._filter_items[item] = ("status", key)
        label_root = self.filter_tree.AppendItem(root, "Labels",
                                                 FILTER_ICON["labels"])
        labels = self.config.get("labels", [])
        if not labels:
            self.filter_tree.AppendItem(label_root, "No labels yet")
        for label in labels:
            item = self.filter_tree.AppendItem(
                label_root, "%s (%d)" % (label, counts.get("label:" + label, 0)),
                FILTER_ICON["labels"])
            self._filter_items[item] = ("label", label)
        self.filter_tree.ExpandAll()
        self.filter_tree.SelectItem(node)

    def _counts(self):
        counts = {"all": 0}
        for row in self.all_rows:
            counts["all"] += 1
            key = self._status_bucket(row)
            counts[key] = counts.get(key, 0) + 1
            if row.label:
                counts["label:" + row.label] = \
                    counts.get("label:" + row.label, 0) + 1
        return counts

    def _status_bucket(self, row):
        if row.error:
            return "error"
        if row.paused:
            return "completed" if _stopped_complete(row) else "paused"
        if not row.has_metadata or row.state in (
                "checking_files", "checking_resume_data", "queued_for_checking",
                "allocating", "downloading_metadata"):
            return "downloading"
        if row.state == "downloading":
            return "active" if (row.download_rate or 0) > 0 else "downloading"
        if row.state == "seeding":
            return "seeding"
        if row.state == "finished" or row.finished:
            return "completed"
        return "inactive"

    def _passes_filter(self, row):
        kind = self.current_filter
        if kind == "all":
            return True
        if kind == "status":
            return self._status_bucket(row) == self.current_label
        if kind == "label":
            return (row.label or "") == self.current_label
        return True

    def _passes_search(self, row):
        if not self.search_text:
            return True
        needle = self.search_text
        for field in (row.name, row.label, row.info_hash, row.save_path,
                      row.status_text, row.source):
            if field and needle in field.lower():
                return True
        return False

    # -------------------------------------------------------------- columns
    def _rebuild_columns(self):
        while self.list.GetColumnCount():
            if not self.list.DeleteColumn(self.list.GetColumnCount() - 1):
                break
        self.visible_keys = colmod.visible_keys(self.column_state)
        for index, key in enumerate(self.visible_keys):
            col = colmod.get(key)
            if col is None:
                continue
            align = wx.LIST_FORMAT_RIGHT if col.align == colmod.RIGHT \
                else wx.LIST_FORMAT_LEFT
            info = wx.ListItem()
            info.SetText(col.label)
            info.SetWidth(int(self._width_for(key)))
            info.SetAlign(align)
            self.list.InsertColumn(index, info)
        self.last_signature = None

    def _width_for(self, key):
        for entry in self.column_state:
            if entry["key"] == key:
                return entry.get("width", 110)
        return 110

    # ------------------------------------------------- virtual list callbacks
    def get_item_text(self, item, column):
        if not 0 <= item < len(self.rows):
            return ""
        if not 0 <= column < len(self.visible_keys):
            return ""
        col = colmod.get(self.visible_keys[column])
        return col.text(self.rows[item]) if col else ""

    def get_item_image(self, item):
        """An icon per state, in icons.STATE_KINDS order: paused, down, up,
        error, queued, checking, finished, metadata."""
        if not 0 <= item < len(self.rows):
            return -1
        row = self.rows[item]
        if row.error:
            return 3
        if row.paused:
            if _stopped_complete(row):
                return 6
            return 4 if row.auto_managed else 0
        if row.state in ("checking_files", "checking_resume_data",
                         "queued_for_checking", "allocating"):
            return 5
        if not row.has_metadata or row.state == "downloading_metadata":
            return 7
        if row.state == "seeding":
            return 2
        if row.state == "finished" or row.finished:
            return 6
        if row.state == "downloading":
            return 1
        return -1

    def get_item_attr(self, item):
        """Status colour and alternate row shading for one row.

        Overriding this hook replaces the built in alternate row colours, so
        the shading is done here too. Attributes are cached per kind of row.
        """
        if not 0 <= item < len(self.rows):
            return None
        bucket = self._colour_bucket(self.rows[item]) if self._use_colours \
            else ""
        key = (bucket, item % 2 == 1)
        if key not in self._attr_cache:
            self._attr_cache[key] = self._make_attr(*key)
        return self._attr_cache[key]

    def _colour_bucket(self, row):
        if row.error:
            return "error"
        if row.paused:
            return "completed" if _stopped_complete(row) else "paused"
        if row.state in ("checking_files", "checking_resume_data",
                         "queued_for_checking", "allocating"):
            return "checking"
        bucket = self._status_bucket(row)
        if bucket in ("active", "downloading"):
            return "downloading"
        if bucket in ("seeding", "completed"):
            return bucket
        return ""

    def _make_attr(self, bucket, odd):
        shade = odd and not self._high_contrast
        if not bucket and not shade:
            return None
        attr = wx.ItemAttr()
        base = self.list.GetBackgroundColour()
        dark = graph.is_dark(base)
        if bucket:
            light_rgb, dark_rgb = STATUS_COLOURS[bucket]
            attr.SetTextColour(wx.Colour(*(dark_rgb if dark else light_rgb)))
        if shade:
            attr.SetBackgroundColour(base.ChangeLightness(118 if dark else 95))
        return attr

    def _refresh_colour_settings(self):
        self._high_contrast = high_contrast()
        self._use_colours = bool(self.config.get("color_rows", True)) and \
            not self._high_contrast
        self._attr_cache = {}
        self.list.Refresh()

    def _on_sys_colour(self, event):
        self._refresh_colour_settings()
        event.Skip()

    # kept for compatibility with the wx virtual hooks
    def OnGetItemText(self, item, column):
        return self.get_item_text(item, column)

    def OnGetItemImage(self, item):
        return self.get_item_image(item)

    def OnGetItemAttr(self, item):
        return self.get_item_attr(item)

    # ------------------------------------------------------------ list data
    def _row_signature(self, row):
        return (row.info_hash, row.name, row.state, row.has_metadata,
                int((row.progress or 0) * 1000), row.download_rate,
                row.upload_rate, row.total_download, row.total_upload,
                row.num_seeds, row.num_peers, row.num_complete,
                row.num_incomplete, row.paused, row.error, row.label,
                row.verified_pieces, row.queue_position, row.download_limit,
                row.upload_limit, row.sequential, row.super_seeding,
                row.auto_managed, row.priority, row.current_tracker,
                getattr(row, "stopped", False))

    def apply_snapshot(self, snapshot):
        self.all_rows = list(snapshot or [])
        filtered = [r for r in self.all_rows
                    if self._passes_filter(r) and self._passes_search(r)]
        col = colmod.get(self.sort_key) if self.sort_key else None
        if col is not None:
            if self.sort_key in ("size", "downloaded", "uploaded", "seeds",
                                 "peers", "downspeed", "upspeed", "progress",
                                 "queue_position"):
                filtered.sort(key=self._numeric_sort, reverse=self.sort_reverse)
            else:
                filtered.sort(key=col.sort_key, reverse=self.sort_reverse)
        signature = tuple(self._row_signature(r) for r in filtered)
        if signature == self.last_signature:
            self._update_selected_details()
            return
        self.last_signature = signature
        self.rows = filtered
        self.list.SetItemCount(len(self.rows))
        self.list.Refresh()
        self._reselect()
        self._update_selected_details()
        self._update_status()

    def _numeric_sort(self, row):
        mapping = {
            "size": row.size or 0,
            "downloaded": row.total_download or 0,
            "uploaded": row.total_upload or 0,
            "seeds": row.num_seeds or 0,
            "peers": row.num_peers or 0,
            "downspeed": row.download_rate or 0,
            "upspeed": row.upload_rate or 0,
            "progress": row.progress or 0,
            "queue_position": row.queue_position or 0,
        }
        return mapping.get(self.sort_key, 0)

    def _reselect(self):
        previous = self.selected_hash
        if previous:
            for index, row in enumerate(self.rows):
                if row.info_hash == previous:
                    self._select_index(index, announce=False)
                    return
        self.selected_hash = None
        if not self.rows:
            return
        self._select_index(0, announce=False)
        if previous:
            # The torrent that was selected is gone, usually because of a
            # filter or a sort. Say so instead of moving in silence.
            self.announce("Selection moved to %s" % self.rows[0].announce_name)

    def _select_index(self, index, announce=True):
        if not 0 <= index < len(self.rows):
            return
        state = wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED
        self.list.SetItemState(index, state, state)
        self.list.EnsureVisible(index)
        self.selected_hash = self.rows[index].info_hash
        if announce:
            self.status.SetStatusText(self.rows[index].summary_line(), 0)

    def selected_rows(self):
        result = []
        index = self.list.GetFirstSelected()
        while index >= 0:
            if index < len(self.rows):
                result.append(self.rows[index])
            index = self.list.GetNextSelected(index)
        return result

    def selected_hashes(self):
        return [r.info_hash for r in self.selected_rows()]

    def current_row(self):
        index = self.list.GetFirstSelected()
        if index < 0:
            if self.selected_hash:
                for row in self.rows:
                    if row.info_hash == self.selected_hash:
                        return row
            return None
        if index < len(self.rows):
            self.selected_hash = self.rows[index].info_hash
            return self.rows[index]
        return None

    # ------------------------------------------------------------ details
    def _update_selected_details(self):
        row = self.current_row()
        if row is None:
            self._clear_details()
            return
        self.details_hash = row.info_hash
        details = self.details.get(row.info_hash, {})
        self.progress_strip.set(row.progress if row.has_metadata else 0.0,
                                row.state_phrase())
        self._fill_general(row)
        self._fill_files(row, details)
        self._fill_peers(details)
        self._fill_trackers(row, details)
        self._fill_speed(row)

    def _clear_details(self):
        self.details_hash = None
        self.progress_strip.set(0.0, "")
        self.general.DeleteAllItems()
        self.files.DeleteAllItems()
        self.peers.DeleteAllItems()
        self.trackers.DeleteAllItems()
        self.speed.DeleteAllItems()

    def _fill_general(self, row):
        fields = [
            ("Name", row.name or ""),
            ("Info hash", row.info_hash or ""),
            ("Status", row.state_phrase()),
            ("Size", row.size_text),
            ("Downloaded", row.downloaded_text),
            ("Uploaded", row.uploaded_text),
            ("Remaining", row.remaining_text),
            ("Ratio", row.ratio_text),
            ("Seeds", row.seeds_text),
            ("Peers", row.peers_text),
            ("Connections", row.connections_text),
            ("Down speed", row.down_speed_text or "0 B/s"),
            ("Up speed", row.up_speed_text or "0 B/s"),
            ("ETA", row.eta_text),
            ("Availability", row.availability_text),
            ("Pieces", row.pieces_text),
            ("Added on", row.added_text),
            ("Completed on", row.completed_text),
            ("Active time", row.active_text),
            ("Save path", row.save_path or ""),
            ("Label", row.label or ""),
            ("Priority", row.priority_text),
            ("Download limit", row.dl_limit_text),
            ("Upload limit", row.ul_limit_text),
            ("Sequential download", "yes" if row.sequential else "no"),
            ("Super seeding", "yes" if row.super_seeding else "no"),
            ("Managed by queue", "yes" if row.auto_managed else "no"),
            ("Tracker", row.tracker_text),
            ("Comment", row.comment_text),
            ("Error", row.error_text),
            ("Source", row.source or ""),
        ]
        self._fill_pairs(self.general, fields)

    def _fill_speed(self, row):
        fields = [
            ("Download rate", row.down_speed_text or "0 B/s"),
            ("Upload rate", row.up_speed_text or "0 B/s"),
            ("Downloaded", row.downloaded_text),
            ("Uploaded", row.uploaded_text),
            ("Download limit", row.dl_limit_text),
            ("Upload limit", row.ul_limit_text),
            ("Ratio", row.ratio_text),
            ("Active time", row.active_text),
            ("Seeding time", fmt.human_duration(row.seeding_time)),
            ("Queue position", str((row.queue_position or 0) + 1)),
        ]
        self._fill_pairs(self.speed, fields)

    def _fill_pairs(self, control, fields):
        if control.GetItemCount() != len(fields):
            control.DeleteAllItems()
            for index, (label, value) in enumerate(fields):
                item = control.InsertItem(index, label)
                control.SetItem(item, 1, str(value))
            return
        for index, (label, value) in enumerate(fields):
            if control.GetItemText(index, 1) != str(value):
                control.SetItem(index, 1, str(value))

    def _fill_files(self, row, details):
        progress = details.get("files_progress") or []
        priorities = details.get("files_priorities") or []
        if self.files.GetItemCount() != len(row.files):
            self.files.DeleteAllItems()
            for index, entry in enumerate(row.files):
                item = self.files.InsertItem(index, entry["path"])
                self.files.SetItem(item, 1, fmt.human_size(entry["size"]))
        for index, entry in enumerate(row.files):
            if index < len(progress):
                done = progress[index]
                self.files.SetItem(index, 2, fmt.human_size(done))
                size = entry["size"] or 0
                pct = (float(done) / size) if size else 0.0
                self.files.SetItem(index, 3, fmt.percent(pct))
            if index < len(priorities):
                self.files.SetItem(index, 4, _priority_name(priorities[index]))

    def _fill_peers(self, details):
        peers = details.get("peers") or []
        if self.peers.GetItemCount() != len(peers):
            self.peers.DeleteAllItems()
            for index, peer in enumerate(peers):
                item = self.peers.InsertItem(index, peer.get("address", ""))
                self.peers.SetItem(item, 1, peer.get("client", ""))
                self.peers.SetItem(item, 2, peer.get("flags", ""))
                self.peers.SetItem(item, 3, peer.get("progress", ""))
                self.peers.SetItem(item, 4, peer.get("down", ""))
                self.peers.SetItem(item, 5, peer.get("up", ""))
                self.peers.SetItem(item, 6, peer.get("downloaded", ""))
                self.peers.SetItem(item, 7, peer.get("uploaded", ""))
            return
        for index, peer in enumerate(peers):
            for column, key in ((1, "client"), (2, "flags"), (3, "progress"),
                                (4, "down"), (5, "up"), (6, "downloaded"),
                                (7, "uploaded")):
                value = peer.get(key, "")
                if self.peers.GetItemText(index, column) != value:
                    self.peers.SetItem(index, column, value)

    def _fill_trackers(self, row, details):
        trackers = row.trackers or []
        status_map = details.get("tracker_status") or {}
        if self.trackers.GetItemCount() != len(trackers):
            self.trackers.DeleteAllItems()
            for index, (url, tier) in enumerate(trackers):
                item = self.trackers.InsertItem(index, url)
                self.trackers.SetItem(item, 1, str(tier))
                self.trackers.SetItem(item, 2, status_map.get(url, ""))
            return
        for index, (url, _tier) in enumerate(trackers):
            value = status_map.get(url, "")
            if self.trackers.GetItemText(index, 2) != value:
                self.trackers.SetItem(index, 2, value)

    # ------------------------------------------------------------- status
    def _update_status(self):
        row = self.current_row()
        if row is not None:
            self.status.SetStatusText(row.summary_line(), 0)
        elif self.rows:
            self.status.SetStatusText("%d torrents" % len(self.rows), 0)
        else:
            self.status.SetStatusText("No torrents shown", 0)
        stats = self.session_stats or {}
        down = fmt.human_speed(stats.get("download_rate", 0)) or "0 B/s"
        up = fmt.human_speed(stats.get("upload_rate", 0)) or "0 B/s"
        self.status.SetStatusText(
            "Down %s. Up %s. Peers %s. DHT nodes %s."
            % (down, up, stats.get("num_peers", 0), stats.get("dht_nodes", 0)),
            1)
        alt = bool(self._speed_state and self._speed_state[2])
        self.status.SetStatusText("Slow mode" if alt else "Normal speed", 2)
        total = len(self.all_rows)
        shown = len(self.rows)
        if shown == total:
            self.status.SetStatusText("Showing all %d torrents" % total, 3)
        else:
            self.status.SetStatusText("Showing %d of %d torrents" % (shown, total), 3)

    def update_session_stats(self, stats):
        self.session_stats = stats or {}
        if stats:
            try:
                seconds = int(self.config.get("row_interval_ms", 900)) / 1000.0
            except (TypeError, ValueError):
                seconds = 0.9
            self.speed_history.seconds_per_sample = max(0.3, seconds)
            self.speed_history.add(stats.get("download_rate", 0),
                                   stats.get("upload_rate", 0))
            self._refresh_graph()
        self._update_status()

    def _refresh_graph(self):
        self._stats_ticks += 1
        try:
            if self.speed_graph.IsShownOnScreen():
                self.speed_graph.redraw()
                # Rewrite the summary only every few seconds, and never while
                # it has focus, so a screen reader is not interrupted.
                if self._stats_ticks % 3 == 0 and \
                        wx.Window.FindFocus() is not self.speed_summary:
                    text = self.speed_history.summary()
                    if self.speed_summary.GetValue() != text:
                        self.speed_summary.ChangeValue(text)
        except RuntimeError:
            return
        if self.tray is not None and self.icon_app is not None and \
                self._stats_ticks % 5 == 0:
            stats = self.session_stats or {}
            tip = "%s. Down %s, up %s" % (
                APP_NAME, fmt.human_speed(stats.get("download_rate", 0)) or "0 B/s",
                fmt.human_speed(stats.get("upload_rate", 0)) or "0 B/s")
            try:
                self.tray.SetIcon(self.icon_app, tip)
            except Exception:
                pass

    # -------------------------------------------------------------- timer
    def _start_timer(self):
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self.timer)
        self.timer.Start(max(300, int(self.config.get("row_interval_ms", 900))))
        self._start_watch_timer()
        self._schedule_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_schedule_tick, self._schedule_timer)
        self._schedule_timer.Start(30000)
        wx.CallLater(500, self._on_timer, None)

    def _on_schedule_tick(self, event):
        if not self._closed:
            self._apply_speed_mode()

    def _on_timer(self, event):
        if self._closed:
            return
        self.apply_snapshot(self.engine.snapshot())
        self.engine.post("session_stats")
        row = self.current_row()
        if row is not None:
            self.engine.post("request_details", info_hash=row.info_hash)

    # -------------------------------------------------------------- events
    def _bind_events(self):
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        self.list.Bind(wx.EVT_LIST_ITEM_FOCUSED, self._on_item_selected)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)
        self.list.Bind(wx.EVT_LIST_COL_CLICK, self._on_header_click)
        self.list.Bind(wx.EVT_RIGHT_DOWN, self._on_right_down)
        self.list.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.filter_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_filter_changed)
        self.search.Bind(wx.EVT_TEXT, self._on_search)
        self.search.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_search_cancel)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_SYS_COLOUR_CHANGED, self._on_sys_colour)

        self.files.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_file_selected)
        self.peers.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_peer_selected)
        self.peers.Bind(wx.EVT_RIGHT_DOWN, self._on_peers_right_down)
        self.trackers.Bind(wx.EVT_RIGHT_DOWN, self._on_trackers_right_down)
        self.files.Bind(wx.EVT_RIGHT_DOWN, self._on_files_right_down)
        for control, handler in ((self.peers, self._on_peers_right_down),
                                 (self.trackers, self._on_trackers_right_down),
                                 (self.files, self._on_files_right_down)):
            control.Bind(wx.EVT_KEY_DOWN,
                         self._make_detail_key_handler(control, handler))
            control.Bind(wx.EVT_CONTEXT_MENU,
                         self._make_detail_menu_handler(control, handler))
        self.peers.Bind(wx.EVT_LIST_ITEM_ACTIVATED,
                        lambda e: self.copy_detail_text(self.peers, 1, "Client"))
        self.trackers.Bind(wx.EVT_LIST_ITEM_ACTIVATED,
                           lambda e: self.copy_detail_text(self.trackers, 0,
                                                           "Tracker URL"))

        self.drop_target = TorrentDropTarget(self._on_drop_files)
        self.list.SetDropTarget(self.drop_target)

    # ------------------------------------------------ detail list commands
    def _on_file_selected(self, event):
        index = event.GetIndex()
        row = self.current_row()
        if row is not None and 0 <= index < len(row.files):
            entry = row.files[index]
            self.announce("%s, %s. Enter to change the file list."
                          % (entry.get("path", ""),
                             fmt.human_size(entry.get("size", 0))))

    def _on_peer_selected(self, event):
        index = event.GetIndex()
        details = self.details.get(self.details_hash or "", {})
        peers = details.get("peers") or []
        if 0 <= index < len(peers):
            peer = peers[index]
            self.announce("%s, %s, %s down, %s up"
                          % (peer.get("address", ""), peer.get("client", ""),
                             peer.get("down", "0 B/s"),
                             peer.get("up", "0 B/s")))

    def _selected_detail_index(self, control):
        index = control.GetFirstSelected()
        if index < 0:
            self.announce("No row selected")
            return -1
        return index

    def copy_detail_text(self, control, column, label):
        index = self._selected_detail_index(control)
        if index < 0:
            return
        text = control.GetItemText(index, column)
        if not text:
            self.announce("Nothing to copy")
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()
            self.announce("%s copied to the clipboard" % label)
        else:
            self.announce("Could not open the clipboard")

    def _on_peers_right_down(self, event):
        event = event or wx.CommandEvent()
        if isinstance(event, wx.MouseEvent):
            index, _flags = self.peers.HitTest(event.GetPosition())
        else:
            index, _flags = -1, None
        if index != -1:
            self.peers.SetItemState(index, wx.LIST_STATE_SELECTED,
                                    wx.LIST_STATE_SELECTED)
        elif self.peers.GetFirstSelected() < 0 and self.peers.GetItemCount():
            self.peers.SetItemState(0, wx.LIST_STATE_SELECTED,
                                    wx.LIST_STATE_SELECTED)
        menu = wx.Menu()

        def add(label, handler):
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, handler, item)

        add("&Copy Address", lambda e: self.copy_detail_text(self.peers, 0,
                                                             "Address"))
        add("Copy &Client", lambda e: self.copy_detail_text(self.peers, 1,
                                                            "Client"))
        add("&Read Peer Aloud", lambda e: self.read_detail_row(self.peers,
                                                               "Peer"))
        menu.AppendSeparator()
        add("Connect to &Peer...", lambda e: self.prompt_add_peer())
        add("&Refresh Now", lambda e: self.on_refresh(None))
        self.PopupMenu(menu, self._detail_menu_position(self.peers))
        menu.Destroy()

    def _on_trackers_right_down(self, event):
        if isinstance(event, wx.MouseEvent):
            index, _flags = self.trackers.HitTest(event.GetPosition())
        else:
            index, _flags = -1, None
        if index != -1:
            self.trackers.SetItemState(index, wx.LIST_STATE_SELECTED,
                                       wx.LIST_STATE_SELECTED)
        elif self.trackers.GetFirstSelected() < 0 and self.trackers.GetItemCount():
            self.trackers.SetItemState(0, wx.LIST_STATE_SELECTED,
                                       wx.LIST_STATE_SELECTED)
        menu = wx.Menu()

        def add(label, handler):
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, handler, item)

        add("&Copy Tracker URL", lambda e: self.copy_detail_text(
            self.trackers, 0, "Tracker URL"))
        add("Copy &Status", lambda e: self.copy_detail_text(self.trackers, 2,
                                                            "Status"))
        add("&Read Tracker Aloud", lambda e: self.read_detail_row(self.trackers,
                                                                  "Tracker"))
        menu.AppendSeparator()
        add("&Add Tracker...", lambda e: self.on_trackers_dialog(None))
        add("Force Re&announce", lambda e: self.on_reannounce(None))
        self.PopupMenu(menu, self._detail_menu_position(self.trackers))
        menu.Destroy()

    def _on_files_right_down(self, event):
        if isinstance(event, wx.MouseEvent):
            index, _flags = self.files.HitTest(event.GetPosition())
        else:
            index, _flags = -1, None
        if index != -1:
            self.files.SetItemState(index, wx.LIST_STATE_SELECTED,
                                    wx.LIST_STATE_SELECTED)
        elif self.files.GetFirstSelected() < 0 and self.files.GetItemCount():
            self.files.SetItemState(0, wx.LIST_STATE_SELECTED,
                                    wx.LIST_STATE_SELECTED)
        menu = wx.Menu()

        def add(label, handler):
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, handler, item)

        add("&Copy File Name", lambda e: self.copy_detail_text(self.files, 0,
                                                               "File name"))
        add("&Read File Aloud", lambda e: self.read_detail_row(self.files,
                                                               "File"))
        menu.AppendSeparator()
        priority_menu = wx.Menu()
        current = self._selected_file_priority()
        for label, value in FILE_PRIORITIES:
            item = priority_menu.AppendRadioItem(wx.ID_ANY, label)
            if current is not None and _file_level(current) == value:
                item.Check(True)
            self.Bind(wx.EVT_MENU,
                      lambda e, v=value: self.set_file_level(v), item)
        menu.AppendSubMenu(priority_menu, "&Priority")
        add("&Download Selected Files", lambda e: self.set_file_level(4))
        add("&Skip Selected Files", lambda e: self.set_file_level(0))
        menu.AppendSeparator()
        add("Open Containing &Folder", lambda e: self.on_open_folder(None))
        self.PopupMenu(menu, self._detail_menu_position(self.files))
        menu.Destroy()

    def _detail_menu_position(self, control):
        """Where to open a detail list menu, from the focused row if possible."""
        index = control.GetFirstSelected()
        rect = wx.Rect()
        if index >= 0:
            try:
                if control.GetItemRect(index, rect):
                    return wx.Point(rect.x + 30, rect.y + rect.height)
            except Exception:
                pass
        return wx.Point(20, 20)

    def read_detail_row(self, control, label):
        index = self._selected_detail_index(control)
        if index < 0:
            return
        parts = []
        for column in range(control.GetColumnCount()):
            title = control.GetColumn(column).GetText()
            text = control.GetItemText(index, column)
            if text:
                parts.append("%s %s" % (title, text))
        announcer().speak("%s. %s" % (label, ". ".join(parts))
                          if parts else "%s, no information" % label)

    def _selected_file_priority(self):
        """The libtorrent priority of the first file selected in the Files
        tab, or None."""
        row = self.current_row()
        index = self.files.GetFirstSelected()
        if row is None or index < 0:
            return None
        priorities = self.details.get(row.info_hash, {}).get(
            "files_priorities") or []
        if index < len(priorities):
            return int(priorities[index])
        return 4

    def set_file_level(self, value):
        """Give the files selected in the Files tab a priority: 0 skip,
        1 low, 4 normal, 7 high."""
        row = self.current_row()
        if row is None or not row.files:
            self.announce("No file list for this torrent")
            return
        details = self.details.get(row.info_hash, {})
        priorities = list(details.get("files_priorities")
                          or [4] * len(row.files))
        if len(priorities) != len(row.files):
            priorities = [4] * len(row.files)
        chosen = []
        index = self.files.GetFirstSelected()
        while index >= 0:
            if 0 <= index < len(priorities):
                priorities[index] = int(value)
                chosen.append(index)
            index = self.files.GetNextSelected(index)
        if not chosen:
            self.announce("Select the files in the Files tab first")
            return
        self.engine.post("set_file_priorities", info_hash=row.info_hash,
                         priorities=priorities)
        self.announce("%d file%s set to %s"
                      % (len(chosen), "" if len(chosen) == 1 else "s",
                         _priority_name(value).lower()))

    def prompt_add_peer(self):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        dialog = dialogs.SingleLineDialog(
            self, "Add Peer", "Address and port, for example 1.2.3.4:6881",
            "", "Peers added by hand are used even when the tracker is down.")
        if dialog.ShowModal() == wx.ID_OK and dialog.value:
            self.engine.post("connect_peer", info_hash=row.info_hash,
                             address=dialog.value)
            self.announce("Connecting to %s" % dialog.value)
        dialog.Destroy()

    # ------------------------------------------------------- Tab order
    def _tab_stops(self):
        """The main window's Tab order, only what is shown: filter box,
        filters, torrent list, action buttons, details tab strip, and the
        control inside the open details tab. Tab past the last wraps round."""
        stops = [self.search]
        if self.v_split.IsSplit() and self.filter_tree.IsShownOnScreen():
            stops.append(self.filter_tree)
        stops.append(self.list)
        if self.action_bar.IsShown():
            stops.extend(c for c in self.action_bar.GetChildren()
                         if isinstance(c, wx.Button) and c.IsEnabled())
        if self.show_details and self.detail_book.IsShown():
            stops.append(self.detail_book)
            page = self.detail_book.GetCurrentPage()
            if page is self.graph_page:
                stops.append(self.speed_summary)
            elif page is not None:
                stops.append(page)
        return stops

    def _cycle_focus(self, forward):
        """Move focus along _tab_stops. Done by hand because a native
        control in the details area kept the Tab key, trapping it."""
        focus = wx.Window.FindFocus()
        if focus is None:
            return False
        stops = self._tab_stops()
        current = -1
        # Exact match first: the details tab strip also contains its pages.
        for index, stop in enumerate(stops):
            if focus is stop:
                current = index
                break
        if current < 0:
            for index, stop in reversed(list(enumerate(stops))):
                try:
                    if stop.IsDescendant(focus):
                        current = index
                        break
                except RuntimeError:
                    continue
        if current < 0:
            return False
        step = 1 if forward else -1
        target = stops[(current + step) % len(stops)]
        target.SetFocus()
        return True

    def _on_char_hook(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_TAB and not event.ControlDown() and \
                not event.AltDown():
            if self._cycle_focus(not event.ShiftDown()):
                return
        if code == wx.WXK_ESCAPE:
            if self.search.GetValue():
                self.on_search_clear(None)
                return
        # The list handles these itself, but a reader or a shell may send them
        # while focus sits on the frame, so they are caught here as a fallback.
        if (code in (WXK_APPS, 0x5D) or
                (code == wx.WXK_F10 and event.ControlDown())) and \
                not isinstance(wx.Window.FindFocus(), wx.TextCtrl):
            self.list.SetFocus()
            self.show_torrent_menu()
            return
        event.Skip()

    def _on_drop_files(self, paths):
        for path in paths:
            if path.lower().endswith(".torrent"):
                self.engine.post("add_torrent_file", path=path,
                                 save_path=self.config.get("save_path"))
                self.announce("Adding %s" % os.path.basename(path))
            else:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read().strip()
                except OSError:
                    continue
                if text.lower().startswith("magnet:"):
                    self.engine.post("add_magnet", uri=text,
                                     save_path=self.config.get("save_path"))
                    self.announce("Adding magnet link")

    def _on_item_selected(self, event):
        if self._closed:
            return
        index = event.GetIndex()
        if 0 <= index < len(self.rows):
            self.selected_hash = self.rows[index].info_hash
            row = self.rows[index]
            self.status.SetStatusText(row.summary_line(), 0)
            self.engine_touch_details(row.info_hash)
            self._update_selected_details()

    def engine_touch_details(self, info_hash):
        self.engine.post("request_details", info_hash=info_hash)

    def _on_item_activated(self, event):
        self.on_files_dialog(None)

    def _on_header_click(self, event):
        column = event.GetColumn()
        if not 0 <= column < len(self.visible_keys):
            return
        key = self.visible_keys[column]
        if key == self.sort_key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        self.config["sort_column"] = self.sort_key
        self.config["sort_reverse"] = self.sort_reverse
        self.last_signature = None
        self.apply_snapshot(self.all_rows)
        self.announce("Sorted by %s, %s" % (
            colmod.column_header(key),
            "descending" if self.sort_reverse else "ascending"))

    def _list_key_action(self, code, control, shift, alt):
        """Which list command a key stands for, or None to pass it on.

        These keys are handled here rather than in the accelerator table so
        that the same keystrokes keep their normal meaning while the user is
        typing in the filter box.
        """
        if control and not alt and code == ord("A"):
            return "select_all"
        if alt and code == wx.WXK_LEFT:
            return "files"
        if code == wx.WXK_DELETE:
            return "remove_delete" if shift else "remove"
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            return "files"
        return None

    def _on_list_key(self, event):
        code = event.GetKeyCode()
        if code in (WXK_APPS, wx.WXK_F10) or \
                (code == 0x5D and not event.ControlDown()):
            self.show_torrent_menu()
            return
        action = self._list_key_action(code, event.ControlDown(),
                                       event.ShiftDown(), event.AltDown())
        if action == "select_all":
            self.on_select_all(None)
            return
        if action == "files":
            self.on_files_dialog(None)
            return
        if action == "remove":
            self._remove(delete_files=False)
            return
        if action == "remove_delete":
            self._remove(delete_files=True)
            return
        event.Skip()

    def show_torrent_menu(self):
        """Open the torrent context menu from the keyboard.

        F10, the Application key, and Control F10 all land here, so the menu is
        reachable the same way it is in uTorrent and BitTorrent. It is built
        fresh each time, and it opens next to the focused row.
        """
        if self._closed:
            return
        if not self.rows:
            self.announce("There are no torrents to show a menu for")
            return
        index = self.list.GetFirstSelected()
        if index < 0:
            index = 0
            self._select_index(index, announce=False)
        position = self._menu_position(index)
        menu = self._build_context_menu()
        self.announce("%d commands for %s"
                      % (self._count_menu_items(menu),
                         self.rows[min(index, len(self.rows) - 1)].announce_name))
        self.PopupMenu(menu, position)
        menu.Destroy()

    def _menu_position(self, index):
        """Top left corner of the row, so the menu does not cover it."""
        rect = wx.Rect()
        try:
            ok = self.list.GetItemRect(index, rect)
        except Exception:
            ok = False
        if not ok:
            return wx.Point(20, 20)
        return wx.Point(rect.x + 30, rect.y + rect.height)

    @staticmethod
    def _count_menu_items(menu):
        count = 0
        for item in menu.GetMenuItems():
            if item.IsSeparator():
                continue
            count += 1
            if item.GetSubMenu() is not None:
                count += MainFrame._count_menu_items(item.GetSubMenu())
        return count

    def _on_filter_changed(self, event):
        if self._closed:
            return
        try:
            item = event.GetItem()
        except RuntimeError:
            return
        if not item.IsOk():
            return
        entry = self._filter_items.get(item)
        if entry is None:
            try:
                entry = self._filter_items.get(
                    self.filter_tree.GetItemParent(item))
            except RuntimeError:
                return
        if entry is None:
            return
        self.current_filter, self.current_label = entry
        self.config["category_filter"] = entry[0]
        self.config["label_filter"] = entry[1]
        self.last_signature = None
        self.apply_snapshot(self.all_rows)
        self.announce("Filter %s. %d torrents."
                      % (self._filter_description(), len(self.rows)))

    def _filter_description(self):
        if self.current_filter == "all":
            return "all torrents"
        if self.current_filter == "label":
            return "label %s" % self.current_label
        return "status %s" % self.current_label

    def _on_search(self, event):
        if self._closed:
            return
        self.search_text = self.search.GetValue().strip().lower()
        self.last_signature = None
        self.apply_snapshot(self.all_rows)

    def _on_search_cancel(self, event):
        self.on_search_clear(None)

    def on_focus_search(self, event):
        self.search.SetFocus()

    def on_search_clear(self, event=None):
        self.search_text = ""
        self.search.SetValue("")
        self.last_signature = None
        self.apply_snapshot(self.all_rows)
        self.announce("Filter cleared")

    # ------------------------------------------------------- announcements
    def announce(self, text):
        if not text:
            return
        if self.config.get("announce_actions", True):
            announcer().speak(text)
        self.status.SetStatusText(text, 0)

    def notify(self, text):
        if not text or not self.config.get("show_notifications", True):
            return
        announcer().speak(text)

    def add_log(self, text):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
        self.log_lines.append(line)
        if len(self.log_lines) > 400:
            self.log_lines = self.log_lines[-400:]
        if hasattr(self, "log"):
            self.log.AppendText(line + "\n")

    # ---------------------------------------------------------- engine glue
    def on_engine_event(self, kind, payload):
        """Called from the engine thread, so hop to the GUI thread first."""
        if self._closed:
            return
        try:
            wx.CallAfter(self._handle_engine_event, kind, payload)
        except Exception:
            self._handle_engine_event(kind, payload)

    def on_engine_alert(self, kind, payload):
        if self._closed:
            return
        try:
            wx.CallAfter(self._handle_engine_alert, kind, payload)
        except Exception:
            pass

    def _handle_engine_event(self, kind, payload):
        if self._closed:
            return
        payload = payload or {}
        if kind == "session_ready":
            self.status.SetStatusText("Session running", 0)
            self.add_log("Session started")
            self._apply_speed_mode(force=True)
        elif kind == "session_failed":
            self.add_log("Session failed: %s" % payload.get("message", ""))
            announcer().speak("The session failed to start")
            wx.MessageBox("The BitTorrent session failed to start.\n\n%s"
                          % payload.get("message", ""), APP_NAME,
                          wx.OK | wx.ICON_ERROR, self)
        elif kind == "added":
            self.announce("Added %s" % (payload.get("name") or "torrent"))
            self.add_log("Added %s" % payload.get("name", ""))
            info_hash = payload.get("info_hash")
            label = payload.get("label") or ""
            if info_hash:
                fields = {}
                if label:
                    fields["label"] = label
                if payload.get("save_path"):
                    fields["save_path"] = payload.get("save_path")
                if payload.get("final_path"):
                    fields["final_path"] = payload.get("final_path")
                if fields:
                    self.store_handle.upsert_bookmark(info_hash, **fields)
                if label:
                    labels = list(self.config.get("labels", []))
                    if label not in labels:
                        labels.append(label)
                        self.config["labels"] = labels
                        self.store_handle.settings["labels"] = labels
                        self._rebuild_label_menu()
                        self._rebuild_filter_tree()
                self._save_bookmarks()
            wx.CallLater(1500, self._save_bookmarks)
        elif kind == "magnet_added":
            self.add_log("Magnet added: %s" % str(payload.get("uri", ""))[:80])
        elif kind == "duplicate":
            self.announce("That torrent is already in the list")
        elif kind == "add_failed":
            message = payload.get("message", "")
            self.announce("Could not add the torrent: %s" % message)
            self.add_log("Add failed: %s" % message)
        elif kind == "torrent_file":
            info_hash = payload.get("info_hash")
            blob = payload.get("blob")
            if info_hash and blob:
                self.store_handle.backup_torrent_file(info_hash, blob)
                self.store_handle.upsert_bookmark(info_hash, torrent_file=list(blob))
                self._save_bookmarks()
        elif kind == "resume_data":
            info_hash = payload.get("info_hash")
            blob = payload.get("blob")
            if not info_hash or not blob:
                return
            self.store_handle.write_resume(info_hash, blob)
            self.store_handle.upsert_bookmark(
                info_hash, resume_data=list(blob),
                save_path=payload.get("save_path", ""),
                name=payload.get("name", ""))
            self._save_bookmarks()
        elif kind == "resume_failed":
            self.add_log("Resume data failed: %s" % payload.get("message", ""))
        elif kind == "finished":
            if self.config.get("auto_clean", True):
                wx.CallLater(20000, self._clean_finished,
                             payload.get("name", ""))
            self.notify("Finished: %s" % payload.get("name", ""))
            self.add_log("Finished %s" % payload.get("name", ""))
            self._toast("Download finished", payload.get("name", ""))
            if self.motion and not self.IsActive():
                try:
                    self.RequestUserAttention(wx.USER_ATTENTION_INFO)
                except Exception:
                    pass
        elif kind == "metadata":
            self.notify("Metadata received for %s" % payload.get("name", ""))
            self.add_log("Metadata for %s" % payload.get("name", ""))
            wx.CallLater(1000, lambda: self.engine.post("save_now"))
        elif kind == "metadata_failed":
            self.add_log("Metadata failed for %s" % payload.get("name", ""))
        elif kind == "torrent_error":
            self.notify("Torrent error: %s" % payload.get("message", ""))
            self.add_log("Torrent error: %s" % payload.get("message", ""))
        elif kind == "tracker_error":
            self.add_log("Tracker error: %s" % payload.get("message", ""))
        elif kind == "listen_failed":
            self.add_log("Listen problem: %s" % payload.get("message", ""))
            if self.config.get("bind_address"):
                self.notify("The chosen network address is not available. "
                            "Transfers are blocked until it is back.")
        elif kind == "portmap_error":
            self.add_log("Port mapping: %s" % payload.get("message", ""))
        elif kind == "stopped_changed":
            for info_hash in payload.get("hashes") or []:
                entry = self.store_handle.find_bookmark(info_hash)
                if entry is not None:
                    entry["stopped"] = bool(payload.get("value"))
            self._save_bookmarks()
        elif kind == "force_changed":
            for info_hash in payload.get("hashes") or []:
                entry = self.store_handle.find_bookmark(info_hash)
                if entry is not None:
                    entry["force_started"] = bool(payload.get("value"))
            self._save_bookmarks()
        elif kind == "dht_restarted":
            self.announce("DHT restarted")
            self.add_log("DHT restarted")
        elif kind == "torrent_saved":
            self.announce("Torrent file saved")
            self.add_log("Torrent file saved to %s" % payload.get("path", ""))
        elif kind == "save_torrent_failed":
            self.announce("Could not save the torrent file: %s"
                          % payload.get("message", ""))
            self.add_log("Save torrent file failed: %s"
                         % payload.get("message", ""))
        elif kind == "removed":
            info_hash = payload.get("info_hash", "")
            pending = self._pending_clean.pop(info_hash, None) \
                if info_hash else None
            if pending is not None:
                wx.CallLater(4000, self._clean_after_remove, pending)
            elif info_hash and self.config.get("auto_clean", True):
                entry = self.store_handle.find_bookmark(info_hash) or {}
                folders = [paths.from_portable(entry.get("save_path") or ""),
                           self.config.get("incomplete_path") or "",
                           self.config.get("move_completed_path") or "",
                           self.config.get("save_path") or ""]
                # libtorrent closes the files a moment after the removal.
                wx.CallLater(4000, self._clean_removed, info_hash, folders)
            self.store_handle.remove_bookmark(info_hash)
            self._save_bookmarks()
            self.add_log("Removed %s" % payload.get("info_hash", ""))
        elif kind == "labels_changed":
            for info_hash in payload.get("hashes") or []:
                self.store_handle.upsert_bookmark(info_hash,
                                                  label=payload.get("label", ""))
            self._save_bookmarks()
        elif kind == "priority_changed":
            for info_hash in payload.get("hashes") or []:
                self.store_handle.upsert_bookmark(
                    info_hash, priority=int(payload.get("value", 0) or 0))
            self._save_bookmarks()
        elif kind == "storage_moved":
            if payload.get("automatic"):
                self.add_log("Moved a finished download to %s"
                             % payload.get("path", ""))
            else:
                self.announce("Data folder changed")
            info_hash = payload.get("info_hash")
            path = payload.get("path")
            if info_hash and path:
                self.store_handle.upsert_bookmark(info_hash, save_path=path)
                self._save_bookmarks()
        elif kind == "file_priorities_changed":
            self.announce("File priorities updated")
            self.engine.post("save_resume",
                             hashes=[payload.get("info_hash")])
        elif kind == "peer_connected":
            self.announce("Connecting to %s" % payload.get("address", ""))
            self.add_log("Connecting to %s" % payload.get("address", ""))
        elif kind == "peer_failed":
            self.announce("%s is not a usable address"
                          % payload.get("address", ""))
        elif kind == "connections_reset":
            text = "Connections reset for %d torrents" % payload.get("count", 0)
            self.add_log(text)
            self.announce(text)
        elif kind == "peers_cleared":
            text = "Peer list cleared"
            if not payload.get("supported", True):
                text = ("This libtorrent cannot clear the peer list; "
                        "connections were reset instead")
            self.add_log(text)
            self.announce(text)
        elif kind == "trackers_updated":
            text = "Trackers updated: %d added to %d torrents" % (
                payload.get("added", 0), payload.get("torrents", 0))
            if payload.get("private"):
                text += ", %d private torrents left unchanged" % payload["private"]
            self.add_log(text)
            self.announce(text)
        elif kind == "command_failed":
            self.add_log("Command %s failed: %s"
                         % (payload.get("command", ""),
                            payload.get("message", "")))
        elif kind == "shutting_down":
            self.add_log("Saving resume data before exit")
        elif kind == "port_chosen":
            if payload.get("replaced"):
                text = ("Port %s is blocked by Windows, now using port %s"
                        % (payload.get("replaced"), payload.get("port", "")))
                self.announce(text)
                self.add_log(text)
            self.add_log("Listening port %s chosen and saved"
                         % payload.get("port", ""))
            self.store_handle.settings["listen_port"] = payload.get("port", 0)
            self.store_handle.save()
        elif kind == "ip_filter_loaded":
            self.add_log("IP filter loaded, %s ranges blocked"
                         % payload.get("count", 0))
        elif kind == "ip_filter_failed":
            self.announce("The IP filter could not be loaded")
            self.add_log("IP filter failed: %s" % payload.get("message", ""))
        elif kind == "seed_goal":
            text = "Seeding goal reached for %s, %s" % (
                payload.get("name", ""), payload.get("action", "paused"))
            self.notify(text)
            self.add_log("%s: %s" % (text, payload.get("reason", "")))
        elif kind == "final_path_done":
            info_hash = payload.get("info_hash")
            if info_hash:
                self.store_handle.upsert_bookmark(info_hash, final_path="")
                self._save_bookmarks()
        else:
            self.add_log("%s: %s" % (kind, payload))

    def _handle_engine_alert(self, kind, payload):
        if self._closed:
            return
        payload = payload or {}
        if kind == "session_stats":
            self.update_session_stats(payload)
        elif kind == "details":
            info_hash = payload.get("info_hash")
            if info_hash:
                self.details[info_hash] = payload
            row = self.current_row()
            if row is not None and row.info_hash == info_hash:
                self._update_selected_details()
        elif kind == "tracker_status":
            self.tracker_status.update(payload)

    def _save_bookmarks(self):
        self.store_handle.save_bookmarks()

    # ------------------------------------------------------------ commands
    def _save_path_for_add(self):
        if self.config.get("ask_save_path"):
            with wx.DirDialog(self, "Choose a download folder",
                              defaultPath=self.config.get("save_path", ""),
                              style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) \
                    as dialog:
                if dialog.ShowModal() == wx.ID_OK:
                    return dialog.GetPath()
                return None
        return self.config.get("save_path")

    def on_add_file(self, event):
        with wx.FileDialog(self, "Add torrent files",
                           wildcard="Torrent files (*.torrent)|*.torrent|"
                                    "All files (*.*)|*.*",
                           style=wx.FD_OPEN | wx.FD_MULTIPLE |
                                 wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            paths = list(dialog.GetPaths())
        save_path = self._save_path_for_add()
        if save_path is None:
            return
        for path in paths:
            self.engine.post("add_torrent_file", path=path, save_path=save_path)
        self.announce("Adding %d torrent file%s"
                      % (len(paths), "" if len(paths) == 1 else "s"))

    def on_add_magnet(self, event):
        self._show_add_dialog(paused=False)

    def on_add_paused(self, event):
        self._show_add_dialog(paused=True)

    def _show_add_dialog(self, paused):
        dialog = dialogs.AddTorrentDialog(self, self.config.get("save_path", ""))
        if dialog.ShowModal() != wx.ID_OK or not dialog.result:
            dialog.Destroy()
            return
        result = dialog.result
        dialog.Destroy()
        if result.get("paused"):
            paused = True
        save_path = result.get("save_path")
        for link in result.get("links", []):
            self.engine.post("add_magnet", uri=link, save_path=save_path,
                             paused=paused)
        for path in result.get("files", []):
            self.engine.post("add_torrent_file", path=path, save_path=save_path,
                             paused=paused)
        count = len(result.get("links", [])) + len(result.get("files", []))
        self.announce("Adding %d torrent%s" % (count, "" if count == 1 else "s"))

    def on_import_folder(self, event):
        with wx.DirDialog(self, "Folder of torrent files",
                          defaultPath=self.config.get("save_path", ""),
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) \
                as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            folder = dialog.GetPath()
        save_path = self._save_path_for_add()
        if save_path is None:
            return
        count = 0
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".torrent"):
                self.engine.post("add_torrent_file",
                                 path=os.path.join(folder, name),
                                 save_path=save_path)
                count += 1
        self.announce("Adding %d torrents from that folder" % count)

    def on_export_torrent(self, event):
        rows = self.selected_rows()
        if not rows:
            self.announce("No torrent selected")
            return
        with wx.DirDialog(self, "Folder to export torrent files to",
                          defaultPath=self.config.get("save_path", ""),
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) \
                as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            folder = dialog.GetPath()
        written = 0
        for row in rows:
            blob = self.store_handle.read_torrent_backup(row.info_hash)
            if not blob:
                continue
            path = os.path.join(folder, _safe_filename(row.name or
                                                       row.info_hash) + ".torrent")
            try:
                with open(path, "wb") as handle:
                    handle.write(blob)
                written += 1
            except OSError:
                continue
        self.announce("Exported %d torrent files" % written)

    def on_save_resume(self, event):
        self.engine.post("save_now")
        self.announce("Saving resume data for all torrents")

    def on_start(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("resume", hashes=hashes)
        self.announce("Started %d torrent%s"
                      % (len(hashes), "" if len(hashes) == 1 else "s"))

    def on_pause(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("pause", hashes=hashes)
        self.announce("Paused %d torrent%s"
                      % (len(hashes), "" if len(hashes) == 1 else "s"))

    def on_force_start(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        rows = self.selected_rows()
        value = not all(getattr(r, "force_started", False) for r in rows)
        self.engine.post("force_start", hashes=hashes, value=value)
        self.announce("Force start %s for %d torrent%s"
                      % ("on" if value else "off", len(hashes),
                         "" if len(hashes) == 1 else "s"))

    def on_force_recheck(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            return
        self.engine.post("force_recheck", hashes=hashes)
        self.announce("Rechecking %d torrent%s"
                      % (len(hashes), "" if len(hashes) == 1 else "s"))

    def on_reannounce(self, event):
        self.reannounce_selected()
        self.announce("Reannouncing to the trackers")

    def reannounce_selected(self):
        hashes = self.selected_hashes()
        if hashes:
            self.engine.post("reannounce", hashes=hashes)

    def on_queue(self, direction):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("queue_%s" % direction, hashes=hashes)
        self.announce("Moved %s" % direction)

    def set_priority(self, value):
        hashes = self.selected_hashes()
        if not hashes:
            return
        self.engine.post("set_priority", hashes=hashes, value=value)
        self.announce("Priority set to %s" % PRIORITY_NAMES.get(value, "Normal"))

    def on_speed_limits(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        dialog = dialogs.SpeedLimitDialog(self, row)
        if dialog.ShowModal() == wx.ID_OK and dialog.result:
            dl, ul = dialog.result
            self.engine.post("set_download_limit", hashes=[row.info_hash],
                             value=dl)
            self.engine.post("set_upload_limit", hashes=[row.info_hash], value=ul)
            self.announce("Limits set. Download %s, upload %s"
                          % (fmt.limit_text(dl), fmt.limit_text(ul)))
        dialog.Destroy()

    def on_files_dialog(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        if not row.files:
            self.announce("Metadata is not available yet for this torrent")
            return
        details = self.details.get(row.info_hash, {})
        row.files_priorities = details.get("files_priorities") or []
        dialog = dialogs.FilesDialog(self, row)
        if dialog.ShowModal() == wx.ID_OK:
            self.engine.post("set_file_priorities", info_hash=row.info_hash,
                             priorities=dialog.selection_priorities())
        dialog.Destroy()

    def on_trackers_dialog(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        dialog = dialogs.TrackersDialog(self, row)
        dialog.ShowModal()
        for url in dialog.added:
            self.engine.post("add_tracker", info_hash=row.info_hash, url=url)
        for url in dialog.urls:
            self.engine.post("add_url_seed", info_hash=row.info_hash, url=url)
        if dialog.added or dialog.urls:
            self.announce("Trackers updated")
        dialog.Destroy()

    def on_move_storage(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        with wx.DirDialog(self, "New data folder",
                          defaultPath=row.save_path or
                          self.config.get("save_path"),
                          style=wx.DD_DEFAULT_STYLE) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = dialog.GetPath()
        self.engine.post("move_storage", info_hash=row.info_hash, path=path)
        self.announce("Moving the data to the new folder")

    def on_open_folder(self, event):
        row = self.current_row()
        if row is None or not row.save_path:
            self.announce("There is no folder to open")
            return
        try:
            os.startfile(row.save_path)
            self.announce("Opened the download folder")
        except OSError as exc:
            self.announce("Could not open the folder: %s" % exc)

    def on_rename_torrent(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        dialog = dialogs.SingleLineDialog(self, "Rename Torrent",
                                          "Display name:", row.name)
        if dialog.ShowModal() == wx.ID_OK and dialog.value:
            self.engine.post("set_display_name", info_hash=row.info_hash,
                             name=dialog.value)
            self.store_handle.upsert_bookmark(row.info_hash,
                                              display_name=dialog.value)
            self._save_bookmarks()
            self.announce("Name saved as %s" % dialog.value)
        dialog.Destroy()

    def on_remove(self, event):
        self._remove(delete_files=False)

    def on_remove_delete(self, event):
        self._remove(delete_files=True)

    def _remove(self, delete_files):
        """Delete: off the list, cache and temporary files cleared; data and
        the .torrent file stay. Shift Delete: off the list with every file
        and folder it made and the .torrent file it came from."""
        rows = self.selected_rows()
        if not rows:
            self.announce("No torrent selected")
            return
        count = len(rows)
        what = "%d torrent%s" % (count, "" if count == 1 else "s")
        if delete_files:
            message = ("Remove %s and delete everything that belongs to it: "
                       "the downloaded files and folders, the .torrent file "
                       "and the cache. This cannot be undone." % what)
            key = "confirm_remove_data"
        else:
            message = ("Remove %s from the list and clear its cache and "
                       "temporary files? The downloaded files and the "
                       ".torrent file stay." % what)
            key = "confirm_remove"
        if not self._confirm_remove(message, key, delete_files):
            return
        incomplete = self.config.get("incomplete_path") or ""
        for row in rows:
            self._pending_clean[row.info_hash] = {
                "info_hash": row.info_hash, "name": row.name or "",
                "save_path": row.save_path or "", "source": row.source or "",
                "full": bool(delete_files), "incomplete": incomplete}
        hashes = [r.info_hash for r in rows]
        self.engine.post("remove", hashes=hashes, delete_files=delete_files)
        if delete_files:
            self.announce("Removed %s with all data" % what)
        else:
            self.announce("Removed %s from the list" % what)

    def _confirm_remove(self, message, key, danger):
        """Yes or No, with a box to stop asking. True means go ahead."""
        if not self.config.get(key, True):
            return True
        style = wx.YES_NO | wx.ICON_WARNING | (wx.NO_DEFAULT if danger else 0)
        dialog = wx.RichMessageDialog(self, message, APP_NAME, style)
        dialog.ShowCheckBox("&Don't show this warning again")
        answer = dialog.ShowModal()
        hide = dialog.IsCheckBoxChecked()
        dialog.Destroy()
        if answer != wx.ID_YES:
            return False
        if hide:
            self.config[key] = False
            self.store_handle.settings[key] = False
            self.store_handle.save()
            self.add_log("Remove warning turned off; turn it back on in "
                         "Preferences, General")
        return True

    def on_select_all(self, event):
        for index in range(len(self.rows)):
            self.list.SetItemState(index, wx.LIST_STATE_SELECTED,
                                   wx.LIST_STATE_SELECTED)
        self.announce("All %d torrents selected" % len(self.rows))

    def on_select_none(self, event):
        for index in range(len(self.rows)):
            self.list.SetItemState(index, 0, wx.LIST_STATE_SELECTED)
        self.announce("Selection cleared")

    def on_invert_selection(self, event):
        for index in range(len(self.rows)):
            selected = self.list.GetItemState(index, wx.LIST_STATE_SELECTED)
            self.list.SetItemState(
                index, 0 if selected else wx.LIST_STATE_SELECTED,
                wx.LIST_STATE_SELECTED)
        self.announce("Selection inverted")

    def copy_field(self, kind):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        if kind == "magnet":
            text = "magnet:?xt=urn:btih:%s&dn=%s" % (
                row.info_hash, _quote(row.name or ""))
            label = "Magnet link"
        elif kind == "hash":
            text = row.info_hash
            label = "Info hash"
        elif kind == "name":
            text = row.name or ""
            label = "Name"
        else:
            text = row.save_path or ""
            label = "Save path"
        if not text:
            self.announce("There is nothing to copy")
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()
            self.announce("%s copied to the clipboard" % label)
        else:
            self.announce("Could not open the clipboard")

    def on_read_selected(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        parts = []
        for key in self.visible_keys:
            col = colmod.get(key)
            if col is None:
                continue
            text = col.text(row)
            if text:
                parts.append("%s %s" % (col.label, text))
        announcer().speak(". ".join(parts) if parts else row.announce_name)

    def on_refresh(self, event):
        self.last_signature = None
        self.apply_snapshot(self.engine.snapshot())
        self.engine.post("session_stats")
        self.announce("Refreshed")

    def set_sort(self, key):
        self.sort_key = key
        self.sort_reverse = False
        self.config["sort_column"] = key
        self.config["sort_reverse"] = False
        self.last_signature = None
        self.apply_snapshot(self.all_rows)
        self.announce("Sorted by %s" % colmod.column_header(key))

    def on_reverse_sort(self, event):
        self.sort_reverse = not self.sort_reverse
        self.config["sort_reverse"] = self.sort_reverse
        self.last_signature = None
        self.apply_snapshot(self.all_rows)
        self.announce("Sort order reversed")

    def on_clear_sort(self, event):
        self.sort_key = ""
        self.config["sort_column"] = ""
        self.last_signature = None
        self.apply_snapshot(self.all_rows)
        self.announce("Sort cleared, using the queue order")

    def on_columns(self, event):
        dialog = dialogs.ColumnsDialog(self, self.column_state)
        if dialog.ShowModal() == wx.ID_OK and dialog.result:
            self.column_state = colmod.normalize(dialog.result)
            self.config["columns"] = self.column_state
            self._rebuild_columns()
            self.list.SetItemCount(len(self.rows))
            self.list.Refresh()
            self._update_selected_details()
            self.announce("Columns updated, %d visible"
                          % len(colmod.visible_keys(self.column_state)))
        dialog.Destroy()

    def on_toggle_details(self, event):
        self.show_details = not self.show_details
        self.detail_book.Show(self.show_details)
        self.progress_strip.Show(self.show_details)
        self.top_panel.Layout()
        if not self.show_details and not self.list.HasFocus():
            self.list.SetFocus()
        self.announce("Details panel %s"
                      % ("shown" if self.show_details else "hidden"))

    def on_toggle_filters(self, event):
        if self.v_split.IsSplit():
            self.v_split.Unsplit(self.v_split.GetWindow1())
            self.announce("Filter panel hidden")
        else:
            windows = self.v_split.GetChildren()
            if len(windows) == 2:
                self.v_split.SplitVertically(windows[0], windows[1], 200)
            else:
                self.announce("The filter panel could not be restored; restart "
                              "ZTor to bring it back")
                return
            self.announce("Filter panel shown")

    def on_toggle_statusbar(self, event):
        showing = self.status.IsShown()
        self.status.Show(not showing)
        self.SendSizeEvent()
        self.announce("Status bar %s" % ("hidden" if showing else "shown"))

    def on_statistics(self, event):
        dialog = dialogs.StatisticsDialog(self, self._statistics_rows)
        dialog.ShowModal()
        dialog.Destroy()

    def _statistics_rows(self):
        stats = self.session_stats or {}
        return [
            ("Torrents in list", len(self.all_rows)),
            ("Torrents shown", len(self.rows)),
            ("Download rate", fmt.human_speed(stats.get("download_rate", 0))),
            ("Upload rate", fmt.human_speed(stats.get("upload_rate", 0))),
            ("Total downloaded",
             fmt.human_size(stats.get("total_download", 0))),
            ("Total uploaded", fmt.human_size(stats.get("total_upload", 0))),
            ("Peers connected", stats.get("num_peers", 0)),
            ("DHT nodes", stats.get("dht_nodes", 0)),
            ("Listen port", self._listen_port_text()),
            ("Speed profile",
             tuning.PROFILE_NAMES[tuning.profile_index(self.config)]),
            ("Speed limits", "alternative" if self._speed_state and
             self._speed_state[2] else "normal"),
            ("Speech", announcer().backend),
            ("Web remote", self.remote.url() if self.remote is not None and
             self.remote.running() else "off"),
        ]

    def _listen_port_text(self):
        port = self.config.get("listen_port", 0) or 0
        return port if port else "automatic"

    def on_options(self, event):
        dialog = dialogs.OptionsDialog(self, self.config)
        integrate = None
        if dialog.ShowModal() == wx.ID_OK and dialog.result:
            was = bool(self.config.get("windows_integration"))
            self.config.update(dialog.result)
            self.store_handle.settings.update(dialog.result)
            self.store_handle.save()
            self._apply_runtime_settings()
            now = bool(self.config.get("windows_integration"))
            if now != was:
                integrate = now
            else:
                self.announce("Preferences saved")
        dialog.Destroy()
        if integrate is not None:
            self._apply_windows_integration(integrate)

    # ------------------------------------------------------------ cleanup
    def _clean_removed(self, info_hash, folders):
        try:
            result = cleanup.clean_removed(self.store_handle, info_hash,
                                           folders)
            if result.files:
                self.add_log("Removed torrent: " + result.text())
        except Exception as exc:
            self.add_log("Cleanup after removal failed: %s" % exc)

    def _clean_finished(self, name):
        try:
            result = cleanup.clean_finished(
                self.config.get("incomplete_path") or "", name)
            result.merge(cleanup.sweep(self.store_handle, paths.cache_dir()))
            if result.files:
                self.add_log("Finished download: " + result.text())
        except Exception as exc:
            self.add_log("Cleanup after finishing failed: %s" % exc)

    def _clean_after_remove(self, info):
        try:
            result = cleanup.clean_after_remove(
                self.store_handle, info, info.get("full"),
                info.get("incomplete") or "")
            if result.files:
                self.add_log("Removed %s: %s" % (info.get("name") or "torrent",
                                                 result.text()))
        except Exception as exc:
            self.add_log("Cleanup after removal failed: %s" % exc)

    def clean_now(self, min_age=cleanup.MIN_AGE):
        """Sweep now. Returns the result text, also written to the log."""
        try:
            text = cleanup.sweep(self.store_handle, paths.cache_dir(),
                                 min_age=min_age).text()
        except Exception as exc:
            text = "Clearing the cache failed: %s" % exc
        self.add_log(text)
        return text

    def _apply_windows_integration(self, enable):
        """Register or remove ZTor for magnet links and .torrent files."""
        from . import shellassoc
        if not shellassoc.available():
            return
        if not enable:
            ok, message = shellassoc.unregister()
            self.add_log(message)
            self.announce("Preferences saved. " + message)
            return
        ok, message = shellassoc.register()
        self.add_log(message)
        if not ok:
            self.announce(message)
            wx.MessageBox(message, "Windows integration",
                          wx.OK | wx.ICON_WARNING, self)
            return
        text = shellassoc.status_text(shellassoc.status())
        self.add_log(text)
        if all(shellassoc.status().values()):
            self.announce("Preferences saved. " + text)
            return
        answer = wx.MessageBox(
            message + "\n\n" + text + "\n\nOpen Windows Default apps now?",
            "Windows integration", wx.YES_NO | wx.ICON_INFORMATION, self)
        if answer == wx.YES:
            shellassoc.open_default_apps()

    def _apply_runtime_settings(self):
        """Push saved preferences into the running session and the window."""
        self.engine.post("apply_config")
        self.engine.post("set_extra_trackers",
                         urls=trackmod.initial_list(self.config))
        self._apply_speed_mode(force=True)
        self.timer.Start(max(300, int(self.config.get("row_interval_ms", 900))))
        self._start_watch_timer()
        if self.rss is not None:
            self.rss.reschedule()
        self._show_toolbar(bool(self.config.get("show_toolbar", True)))
        self.motion = motion_enabled(self.config)
        self.progress_strip.animate = self.motion
        self.action_bar.Show(self._action_bar_wanted())
        self.action_bar.GetParent().Layout()
        self._refresh_colour_settings()
        self._refresh_trackers()
        self._start_remote()

    # ------------------------------------------- remote, search, creator
    def _start_remote(self, announce=True):
        """Start, restart or stop the web remote when its settings change."""
        if self.remote is None:
            return
        sig = (bool(self.config.get("webui_enabled")),
               self.config.get("webui_port"), bool(self.config.get("webui_lan")),
               self.config.get("webui_hash"))
        if sig == self._remote_sig:
            return
        self._remote_sig = sig
        if not sig[0]:
            if self.remote.running():
                self.remote.stop()
                self.add_log("Web remote stopped")
                if announce:
                    self.announce("Web remote stopped")
            return
        ok, message = self.remote.start()
        if message:
            self.add_log(message)
            if announce or not ok:
                self.announce(message)
        if not ok:
            self._remote_sig = None

    def remote_address_text(self):
        """(text, address to copy) for the Web Remote Address window."""
        if self.remote is None or not self.remote.running():
            return ("The web remote is off. Turn it on in Preferences, on the "
                    "Remote page, and set a password.", "")
        urls = self.remote.urls()
        phone = urls[1:]
        lines = ["Web remote addresses", ""]
        if phone:
            lines.append("Open this on your phone, which must be on the same "
                         "Wi-Fi network as this computer:")
            lines.append(phone[0])
            if len(phone) > 1:
                lines.append("If that does not open, try: %s"
                             % ", ".join(phone[1:]))
            lines += ["", "Copied to the clipboard: %s" % phone[0], "",
                      "If the phone says connection refused or timed out, "
                      "allow ZTor, or Python, through the Windows "
                      "firewall for private networks."]
        elif self.config.get("webui_lan"):
            lines.append("No network address was found for this computer. "
                         "Check that it is connected to your Wi-Fi or "
                         "network, then try again.")
        else:
            lines.append("Phones cannot connect yet. Turn on Allow other "
                         "devices on my network, in Preferences, on the "
                         "Remote page, then open this window again.")
        lines += ["", "On this computer only: %s" % urls[0],
                  "127.0.0.1 always means the device you are using, so it "
                  "does not work from a phone."]
        copy = phone[0] if phone else ""
        return "\r\n".join(lines), copy

    def _remote_running(self):
        return self.remote is not None and self.remote.running()

    def on_remote_address(self, event):
        text, copy = self.remote_address_text()
        if not self._remote_running():
            self.announce(text)
            return
        if copy and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(copy))
            wx.TheClipboard.Close()
        dialog = dialogs.AboutDialog(self, text)
        dialog.SetTitle("Web Remote Address")
        dialog.ShowModal()
        dialog.Destroy()

    def on_search(self, event):
        dialog = SearchDialog(self, self)
        dialog.ShowModal()
        dialog.Destroy()

    def on_create_torrent(self, event):
        dialog = CreateTorrentDialog(self, self)
        dialog.ShowModal()
        dialog.Destroy()

    def on_global_limits(self, event):
        dialog = GlobalLimitsDialog(self, self.config.get("download_rate_limit", 0),
                                    self.config.get("upload_rate_limit", 0))
        if dialog.ShowModal() == wx.ID_OK and dialog.result:
            dl, ul = dialog.result
            self.config["download_rate_limit"] = dl
            self.config["upload_rate_limit"] = ul
            self.store_handle.settings.update({"download_rate_limit": dl,
                                               "upload_rate_limit": ul})
            self.store_handle.save()
            self._apply_speed_mode(force=True)
            self.announce("Global limits. Download %s, upload %s"
                          % (fmt.limit_text(dl), fmt.limit_text(ul)))
        dialog.Destroy()

    def on_start_all(self, event):
        self.engine.post("resume_all")
        self.announce("Starting all torrents")

    def on_pause_all(self, event):
        self.engine.post("pause_all")
        self.announce("Pausing all torrents")

    def on_toggle_speech(self, event):
        value = not self.config.get("announce_actions", True)
        self.config["announce_actions"] = value
        self.config["show_notifications"] = value
        self.store_handle.settings.update({"announce_actions": value,
                                           "show_notifications": value})
        self.store_handle.save()
        announcer().enabled = value
        if value:
            announcer().speak("Speech announcements on")
        self.announce("Speech announcements %s" % ("on" if value else "off"))

    # ------------------------------------------------------------- toolbar
    def _build_toolbar(self):
        """Picture buttons for sighted users. Every one is also a menu command
        with a shortcut, so nothing here is needed from the keyboard."""
        try:
            bar = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_FLAT |
                                     wx.TB_TEXT | wx.TB_NODIVIDER)
        except Exception:
            self.toolbar = None
            return
        bar.SetToolBitmapSize((self._tool_px, self._tool_px))
        specs = (
            ("add_file", "Add File", "Add torrent files, Control O",
             self.on_add_file, wx.ITEM_NORMAL),
            ("add_link", "Add Link", "Add a magnet link or URL, Control N",
             self.on_add_magnet, wx.ITEM_NORMAL),
            ("search", "Search", "Search for torrents, Control E",
             self.on_search, wx.ITEM_NORMAL),
            ("create", "Create", "Create a torrent file, Control Shift M",
             self.on_create_torrent, wx.ITEM_NORMAL),
            None,
            ("start", "Start", "Start the selection, Control S",
             self.on_start, wx.ITEM_NORMAL),
            ("pause", "Pause", "Pause the selection, Control P",
             self.on_pause, wx.ITEM_NORMAL),
            ("remove", "Remove", "Remove the selection, Delete",
             self.on_remove, wx.ITEM_NORMAL),
            None,
            ("queue_up", "Up", "Move up the queue",
             lambda e: self.on_queue("up"), wx.ITEM_NORMAL),
            ("queue_down", "Down", "Move down the queue",
             lambda e: self.on_queue("down"), wx.ITEM_NORMAL),
            None,
            ("alt_speed", "Slow Mode",
             "Alternative speed limits, Control Shift T",
             self.on_toggle_alt_speed, wx.ITEM_CHECK),
            ("rss", "RSS", "RSS feeds, Control R", self.on_rss, wx.ITEM_NORMAL),
            ("prefs", "Options", "Preferences, Control comma",
             self.on_options, wx.ITEM_NORMAL),
        )
        for spec in specs:
            if spec is None:
                bar.AddSeparator()
                continue
            kind, label, help_text, handler, item_kind = spec
            tool = bar.AddTool(wx.ID_ANY, label,
                               tool_bitmap(kind, self._tool_px),
                               help_text, item_kind)
            self.Bind(wx.EVT_TOOL, handler, tool)
            if kind == "alt_speed":
                self.alt_tool_id = tool.GetId()
        bar.Realize()
        self.toolbar = bar
        if not self.config.get("show_toolbar", True):
            bar.Hide()

    def _show_toolbar(self, value):
        if self.toolbar is None:
            return
        if bool(self.toolbar.IsShown()) != bool(value):
            self.toolbar.Show(bool(value))
            self.SendSizeEvent()

    def on_toggle_toolbar(self, event):
        value = not bool(self.config.get("show_toolbar", True))
        self.config["show_toolbar"] = value
        self.store_handle.settings["show_toolbar"] = value
        self.store_handle.save()
        self._show_toolbar(value)
        self.announce("Toolbar %s" % ("shown" if value else "hidden"))

    # --------------------------------------------------- speed limit modes
    def _apply_speed_mode(self, force=False, announce=False):
        """Send the normal or alternative limits, whichever apply now."""
        dl, ul, alt = tuning.effective_limits(self.config)
        state = (dl, ul, alt)
        previous = self._speed_state
        if not force and state == previous:
            return
        self._speed_state = state
        self.engine.post("apply_settings",
                         settings={"download_rate_limit": dl,
                                   "upload_rate_limit": ul})
        self._sync_alt_controls(alt)
        switched = previous is not None and previous[2] != alt
        if announce or switched:
            text = "%s speed limits. Download %s, upload %s" % (
                "Alternative" if alt else "Normal", fmt.limit_text(dl),
                fmt.limit_text(ul))
            self.announce(text)
            self.add_log(text)
        self._update_status()

    def _sync_alt_controls(self, alt):
        if self.alt_menu_item is not None:
            try:
                self.alt_menu_item.Check(bool(alt))
            except RuntimeError:
                pass
        if self.toolbar is not None and self.alt_tool_id is not None:
            try:
                self.toolbar.ToggleTool(self.alt_tool_id, bool(alt))
            except RuntimeError:
                pass

    def on_toggle_alt_speed(self, event):
        value = not bool(self.config.get("alt_speed_on"))
        self.config["alt_speed_on"] = value
        self.store_handle.settings["alt_speed_on"] = value
        self.store_handle.save()
        self._apply_speed_mode(force=True, announce=True)
        if not value and self._speed_state and self._speed_state[2]:
            self.announce("Alternative limits stay on while the schedule is "
                          "active")

    def on_speak_speed(self, event):
        text = self.speed_history.summary()
        announcer().speak(text)
        self.status.SetStatusText(text, 0)

    # ----------------------------------------------------------------- RSS
    def on_rss(self, event):
        if self.rss is not None:
            self.rss.show()

    # ------------------------------------------------------ notices, trackers
    def _toast(self, title, text):
        """A tray pop up for sighted users, skipped while a screen reader
        runs because the reader already spoke the same notice."""
        if not self.config.get("toast_notifications", True) or self.tray is None:
            return
        if announcer().screen_reader_active():
            return
        try:
            self.tray.ShowBalloon(title, text or APP_NAME, 5000)
        except Exception:
            pass

    def _refresh_trackers(self):
        if not self.config.get("extra_trackers_enabled", True):
            return
        if not (self.config.get("extra_trackers_url") or "").strip():
            return
        frame = self

        def done(urls, count, error):
            try:
                wx.CallAfter(frame._trackers_ready, urls, count, error)
            except Exception:
                pass

        trackmod.refresh_async(dict(self.config), done)

    def _trackers_ready(self, urls, count, error):
        if self._closed:
            return
        self.engine.post("set_extra_trackers", urls=urls)
        if error:
            self.add_log("Tracker list not updated: %s" % error)
        else:
            self.add_log("Tracker list updated, %d trackers" % count)

    def on_shortcuts(self, event):
        text = (
            "ZTor keyboard shortcuts\r\n\r\n"
            "These work anywhere in the window:\r\n"
            "Control O  add torrent files\r\n"
            "Control N  add a magnet link or URL\r\n"
            "Control Shift N  add paused\r\n"
            "Control F  jump to the filter box\r\n"
            "Control S  start the selection\r\n"
            "Control P  pause the selection\r\n"
            "Control Shift C  choose columns\r\n"
            "Control Shift D  read the selected torrent with all visible "
            "columns\r\n"
            "Control comma  preferences\r\n"
            "F2  rename the display name\r\n"
            "F3  set speed limits for the torrent\r\n"
            "F5  refresh now\r\n"
            "Control Shift T  alternative speed limits on or off\r\n"
            "Control Shift S  speak the speed summary\r\n"
            "Control R  RSS feeds and download rules\r\n"
            "Control E  search for torrents\r\n"
            "Control Shift M  create a torrent file\r\n"
            "Alt  open the menu bar, then use the arrow keys\r\n"
            "\r\n"
            "These work while the torrent list has focus, so that the filter "
            "box keeps the normal meaning of the same keys:\r\n"
            "Enter  open the files dialog\r\n"
            "Control A  select all torrents\r\n"
            "Delete  remove the torrent, keep the data\r\n"
            "Shift Delete  remove the torrent and delete the data\r\n"
            "Alt Left  open the files dialog\r\n"
            "Applications key or Shift F10  open the context menu\r\n"
            "Arrow keys move, Space selects, Control Space adds to the "
            "selection\r\n"
            "\r\n"
            "In the filter box, Escape clears it.\r\n"
            "Column headers can be clicked or reached with the keyboard to "
            "sort.\r\n"
        )
        dialog = dialogs.AboutDialog(self, text)
        dialog.SetTitle("Keyboard Shortcuts")
        dialog.ShowModal()
        dialog.Destroy()

    def on_about(self, event):
        from . import about_page
        about_page.show_about(self.store_handle.base)

    # ---------------------------------------------------------- context menu
    def _make_detail_key_handler(self, control, handler):
        """Let the Files, Peers, and Trackers menus open from the keyboard."""
        def on_key(event):
            code = event.GetKeyCode()
            if code in (WXK_APPS, 0x5D) or \
                    (code == wx.WXK_F10 and event.ShiftDown()):
                handler(None)
                return
            event.Skip()
        return on_key

    def _make_detail_menu_handler(self, control, handler):
        """Windows asked one of the detail lists for its context menu."""
        def on_menu(event):
            handler(None)
        return on_menu

    def _on_context_menu(self, event):
        """Windows asked for the context menu, usually via the Application key."""
        if self._closed:
            return
        if not self.rows:
            self.announce("There are no torrents to show a menu for")
            return
        index = self.list.GetFirstSelected()
        if index < 0:
            index = 0
            self._select_index(index, announce=False)
        menu = self._build_context_menu()
        self.PopupMenu(menu, self._menu_position(index))
        menu.Destroy()

    def _on_right_down(self, event):
        position = event.GetPosition()
        index, _flags = self.list.HitTest(position)
        if index != -1:
            if not self.list.GetItemState(index, wx.LIST_STATE_SELECTED):
                self._select_index(index, announce=True)
        elif self.list.GetFirstSelected() < 0:
            self.announce("No torrent selected")
            return
        menu = self._build_context_menu()
        self.PopupMenu(menu, position)
        menu.Destroy()

    def PopupMenu(self, menu, *args):
        """Every context menu shows and reads each item's letter after it."""
        add_menu_letters(menu)
        return wx.Frame.PopupMenu(self, menu, *args)

    def _add_menu_item(self, menu, label, handler, enabled=True,
                       kind=wx.ITEM_NORMAL, checked=False):
        """One context menu row, with its handler bound to the frame."""
        item = menu.Append(wx.ID_ANY, label, kind=kind)
        if kind == wx.ITEM_CHECK:
            item.Check(bool(checked))
        if enabled:
            self.Bind(wx.EVT_MENU, handler, item)
        else:
            item.Enable(False)
        return item

    def _build_context_menu(self):
        """The torrent list right click menu.

        The layout follows uTorrent and BitTorrent: the start and pause group
        first, then the queue, then priority, labels, files and trackers, then
        the copy commands, and finally the remove commands at the bottom.
        """
        menu = wx.Menu()
        rows = self.selected_rows()
        count = len(rows)
        suffix = "" if count <= 1 else " (%d torrents)" % count
        first = rows[0] if rows else None
        single = count == 1

        # ---------------------------------------------------- start and pause
        self._add_menu_item(menu, "&Start" + suffix, self.on_start, count > 0)
        self._add_menu_item(menu, "&Force Start" + suffix, self.on_force_start,
                            count > 0)
        self._add_menu_item(menu, "&Pause" + suffix, self.on_pause, count > 0)
        menu.AppendSeparator()
        self._add_menu_item(menu, "S&top" + suffix, self.on_stop, count > 0)
        self._add_menu_item(menu, "Force Rec&heck", self.on_force_recheck,
                            count > 0)
        self._add_menu_item(menu, "Force Re&announce", self.on_reannounce,
                            count > 0)
        self._add_menu_item(menu, "&Update Torrent Trackers",
                            self.on_update_tracker, count > 0)
        menu.AppendSeparator()

        # -------------------------------------------------------------- queue
        queue_menu = wx.Menu()
        for label, direction in (("Move to &Top", "top"), ("Move &Up", "up"),
                                 ("Move &Down", "down"),
                                 ("Move to &Bottom", "bottom")):
            self._add_menu_item(queue_menu, label,
                                lambda e, d=direction: self.on_queue(d),
                                count > 0)
        menu.AppendSubMenu(queue_menu, "&Queue")

        # ----------------------------------------------------------- priority
        priority_menu = wx.Menu()
        current = (first.priority if first is not None else 0)
        for label, value in (("&Low", 1), ("&Normal", 0), ("&High", 2)):
            self._add_menu_item(
                priority_menu, label,
                lambda e, v=value: self.set_priority(v), count > 0,
                kind=wx.ITEM_RADIO, checked=(current == value))
        menu.AppendSubMenu(priority_menu, "&Bandwidth Priority")

        # -------------------------------------------------------------- label
        label_menu = wx.Menu()
        current_label = (first.label if first is not None else "")
        self._add_menu_item(
            label_menu, "&None", lambda e: self.set_label(""), count > 0,
            kind=wx.ITEM_RADIO, checked=not current_label)
        for label in self.config.get("labels", []):
            self._add_menu_item(
                label_menu, label, lambda e, l=label: self.set_label(l),
                count > 0, kind=wx.ITEM_RADIO, checked=(current_label == label))
        label_menu.AppendSeparator()
        self._add_menu_item(label_menu, "Ne&w Label...",
                            lambda e: self.new_label(), count > 0)
        menu.AppendSubMenu(label_menu, "&Label")

        menu.AppendSeparator()

        # ------------------------------------------------------------ content
        self._add_menu_item(menu, "&Open Containing Folder",
                            self.on_open_folder, single)
        self._add_menu_item(menu, "F&iles...", self.on_files_dialog, single)
        self._add_menu_item(menu, "Trac&kers...", self.on_trackers_dialog, single)
        self._add_menu_item(menu, "P&eers...", self.on_peers_dialog, single)
        self._add_menu_item(menu, "Set Speed Li&mits...", self.on_speed_limits,
                            single)
        self._add_menu_item(menu, "Mo&ve Data Folder...", self.on_move_storage,
                            count > 0)
        self._add_menu_item(menu, "Save Torre&nt File As...",
                            self.on_save_torrent_file, single)
        menu.AppendSeparator()

        # --------------------------------------------------------- the toggles
        self._add_menu_item(
            menu, "Sequential Do&wnload", self.on_toggle_sequential, count > 0,
            kind=wx.ITEM_CHECK,
            checked=bool(first.sequential) if first is not None else False)
        self._add_menu_item(
            menu, "Super Seedin&g", self.on_toggle_super_seeding, count > 0,
            kind=wx.ITEM_CHECK,
            checked=bool(first.super_seeding) if first is not None else False)
        self._add_menu_item(
            menu, "Managed b&y Queue", self.on_toggle_auto_managed, count > 0,
            kind=wx.ITEM_CHECK,
            checked=bool(first.auto_managed) if first is not None else False)

        network_menu = wx.Menu()
        self._add_menu_item(
            network_menu, "&DHT", lambda e: self.on_toggle_torrent_net("dht"),
            count > 0, kind=wx.ITEM_CHECK,
            checked=bool(getattr(first, "dht", False)) if first else False)
        self._add_menu_item(
            network_menu, "Peer &Exchange", lambda e: self.on_toggle_torrent_net("pex"),
            count > 0, kind=wx.ITEM_CHECK,
            checked=bool(getattr(first, "pex", False)) if first else False)
        self._add_menu_item(
            network_menu, "&Local Peer Discovery",
            lambda e: self.on_toggle_torrent_net("lsd"),
            count > 0, kind=wx.ITEM_CHECK,
            checked=bool(getattr(first, "lsd", False)) if first else False)
        menu.AppendSubMenu(network_menu, "&Network Features")

        # ---------------------------------------------------------- the details
        detail_menu = wx.Menu()
        for index in range(self.detail_book.GetPageCount()):
            title = self.detail_book.GetPageText(index)
            self._add_menu_item(detail_menu, title,
                                lambda e, i=index: self.show_detail_tab(i),
                                count > 0)
        menu.AppendSubMenu(detail_menu, "Show Details Ta&b")

        menu.AppendSeparator()

        # ------------------------------------------------------------- copying
        self._add_menu_item(menu, "&Copy Magnet Link",
                            lambda e: self.copy_field("magnet"), count > 0)
        self._add_menu_item(menu, "Copy &Info Hash",
                            lambda e: self.copy_field("hash"), count > 0)
        self._add_menu_item(menu, "Copy &Name",
                            lambda e: self.copy_field("name"), count > 0)
        self._add_menu_item(menu, "Copy Sa&ve Path",
                            lambda e: self.copy_field("path"), count > 0)
        self._add_menu_item(menu, "Copy All Detai&ls",
                            lambda e: self.copy_all_details(), single)
        menu.AppendSeparator()

        # ------------------------------------------------------------ the rest
        self._add_menu_item(menu, "Re&name...", self.on_rename_torrent, single)
        self._add_menu_item(menu, "Clear &Error", self.on_clear_error, count > 0)
        self._add_menu_item(menu, "Read Torrent Al&oud", self.on_read_selected,
                            count > 0)
        more_menu = wx.Menu()
        self._add_menu_item(more_menu, "Reset Torrent &Connections" + suffix,
                            self.on_reset_connections, count > 0)
        self._add_menu_item(more_menu, "Clear &Peer List" + suffix,
                            self.on_clear_peers, count > 0)
        menu.AppendSubMenu(more_menu, "&More")
        menu.AppendSeparator()
        self._add_menu_item(menu, "&Remove" + suffix, self.on_remove, count > 0)
        self._add_menu_item(menu, "Remove and &Delete Data" + suffix,
                            self.on_remove_delete, count > 0)
        return menu

    # ---------------------------------------------------- context menu actions
    def show_detail_tab(self, index):
        if 0 <= index < self.detail_book.GetPageCount():
            self.detail_book.SetSelection(index)
            page = self.detail_book.GetPage(index)
            if page is not None:
                page.SetFocus()
            self.announce("%s tab"
                          % self.detail_book.GetPageText(index))

    def on_stop(self, event):
        """Stop: paused for libtorrent, listed as Stopped, or Finished when
        the download is complete."""
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("stop", hashes=hashes)
        self.announce("Stopped %d torrent%s"
                      % (len(hashes), "" if len(hashes) == 1 else "s"))

    def on_update_tracker(self, event):
        """Fetch the newest public tracker list, add it, ask for peers."""
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.announce("Updating trackers")
        frame = self

        def done(urls, count, error):
            wx.CallAfter(frame._update_trackers_ready, hashes, urls, error)
        trackmod.refresh_async(dict(self.config), done)

    def _update_trackers_ready(self, hashes, urls, error):
        if self._closed:
            return
        if error:
            self.add_log("Tracker list not downloaded, using the saved one: %s"
                         % error)
        self.engine.post("update_trackers", hashes=hashes, urls=urls)

    def on_reset_connections(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("reset_connections", hashes=hashes)

    def on_clear_peers(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("clear_peers", hashes=hashes)

    def on_clear_error(self, event):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("clear_error", hashes=hashes)
        self.announce("Error cleared")

    def on_restart_dht(self, event):
        self.engine.post("restart_dht")
        self.announce("Restarting the DHT")

    def on_toggle_torrent_net(self, feature):
        rows = self.selected_rows()
        if not rows:
            self.announce("No torrent selected")
            return
        current = bool(getattr(rows[0], feature, False))
        value = not current
        self.engine.post("set_%s" % feature, hashes=self.selected_hashes(),
                         value=value)
        names = {"dht": "DHT", "pex": "Peer exchange",
                 "lsd": "Local peer discovery"}
        self.announce("%s %s" % (names.get(feature, feature),
                                 "on" if value else "off"))

    def on_peers_dialog(self, event):
        self.show_detail_tab(2)
        self.announce("Peers for the selected torrent")

    def on_save_torrent_file(self, event):
        row = self.current_row()
        if row is None:
            self.announce("No torrent selected")
            return
        default = _safe_filename(row.name or "torrent") + ".torrent"
        with wx.FileDialog(self, "Save torrent file as",
                           defaultDir=self.config.get("save_path", ""),
                           defaultFile=default,
                           wildcard="Torrent files (*.torrent)|*.torrent|"
                                    "All files (*.*)|*.*",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = dialog.GetPath()
        self.engine.post("save_torrent_file", info_hash=row.info_hash,
                         path=path)
        self.announce("Saving the torrent file to %s" % os.path.basename(path))

    def copy_all_details(self):
        """Put every visible column of the selection on the clipboard."""
        rows = self.selected_rows()
        if not rows:
            self.announce("No torrent selected")
            return
        headers = [colmod.column_header(key) for key in self.visible_keys]
        lines = ["\t".join(headers)]
        for row in rows:
            cells = []
            for key in self.visible_keys:
                col = colmod.get(key)
                cells.append(col.text(row) if col else "")
            lines.append("\t".join(cells))
        text = "\n".join(lines)
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()
            self.announce("%d row%s copied to the clipboard"
                          % (len(rows), "" if len(rows) == 1 else "s"))
        else:
            self.announce("Could not open the clipboard")

    def on_toggle_sequential(self, event):
        rows = self.selected_rows()
        if not rows:
            return
        value = not rows[0].sequential
        self.engine.post("set_sequential", hashes=self.selected_hashes(),
                         value=value)
        self.announce("Sequential download %s" % ("on" if value else "off"))

    def on_toggle_super_seeding(self, event):
        rows = self.selected_rows()
        if not rows:
            return
        value = not rows[0].super_seeding
        self.engine.post("set_super_seeding", hashes=self.selected_hashes(),
                         value=value)
        self.announce("Super seeding %s" % ("on" if value else "off"))

    def on_toggle_auto_managed(self, event):
        rows = self.selected_rows()
        if not rows:
            return
        value = not rows[0].auto_managed
        self.engine.post("set_auto_managed", hashes=self.selected_hashes(),
                         value=value)
        self.announce("Managed by queue %s" % ("yes" if value else "no"))

    def set_label(self, label):
        hashes = self.selected_hashes()
        if not hashes:
            self.announce("No torrent selected")
            return
        self.engine.post("set_labels", hashes=hashes, label=label)
        for info_hash in hashes:
            self.store_handle.upsert_bookmark(info_hash, label=label)
        self._save_bookmarks()
        self.last_signature = None
        labels = list(self.config.get("labels", []))
        if label and label not in labels:
            labels.append(label)
            self.config["labels"] = labels
            self.store_handle.settings["labels"] = labels
            self.store_handle.save()
            self._rebuild_label_menu()
        self._rebuild_filter_tree()
        self.announce("Label set to %s" % (label or "none"))

    def new_label(self):
        dialog = dialogs.SingleLineDialog(
            self, "New Label", "Label name:", "",
            "Labels group torrents and appear in the filter panel.")
        if dialog.ShowModal() == wx.ID_OK and dialog.value:
            self.set_label(dialog.value)
        dialog.Destroy()

    def _rebuild_label_menu(self):
        menu = self.label_menu
        while menu.GetMenuItemCount():
            menu.Delete(menu.FindItemByPosition(0))
        item = menu.Append(wx.ID_ANY, "None")
        self.Bind(wx.EVT_MENU, lambda e: self.set_label(""), item)
        for label in self.config.get("labels", []):
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, l=label: self.set_label(l), item)
        menu.AppendSeparator()
        item = menu.Append(wx.ID_ANY, "New Label...")
        self.Bind(wx.EVT_MENU, lambda e: self.new_label(), item)

    # ------------------------------------------------------------- window
    def _restore_window(self):
        window = self.config.get("window", {})
        if window.get("maximized"):
            self.Maximize(True)
        else:
            w = int(window.get("w", 1100))
            h = int(window.get("h", 700))
            x = window.get("x", -1)
            y = window.get("y", -1)
            self.SetSize((w, h))
            if x is not None and y is not None and x >= 0 and y >= 0:
                self.SetPosition((int(x), int(y)))
            else:
                self.Centre()
        self.list.SetFocus()

    def restore_window(self):
        self.Show()
        self.Iconize(False)
        self.Raise()
        self._bring_to_front()
        self.list.SetFocus()
        self.announce("ZTor window shown")

    def _bring_to_front(self):
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(self.GetHandle()))
        except Exception:
            pass

    def open_args(self, args, show=True):
        """Torrent files and magnet links from the command line, or handed
        over by a second start of ZTor."""
        if self._closed:
            return
        if show:
            self.restore_window()
        added = 0
        save_path = self.config.get("save_path")
        for arg in args or []:
            text = (arg or "").strip().strip('"')
            if text.lower().startswith("magnet:"):
                self.engine.post("add_magnet", uri=text, save_path=save_path)
                added += 1
            elif text.lower().endswith(".torrent") and os.path.isfile(text):
                self.engine.post("add_torrent_file", path=text,
                                 save_path=save_path)
                added += 1
        if added:
            self.announce("Adding %d torrent%s" % (added,
                                                   "" if added == 1 else "s"))

    def _save_window_state(self):
        if self.IsMaximized():
            window = dict(self.config.get("window", {}))
            window["maximized"] = True
            self.config["window"] = window
            return
        position = self.GetPosition()
        size = self.GetSize()
        self.config["window"] = {"x": position.x, "y": position.y,
                                 "w": size.width, "h": size.height,
                                 "maximized": False}

    # --------------------------------------------------------- watch folder
    def _start_watch_timer(self):
        """Poll the watch folder for torrent files that appear later."""
        if self._watch_timer is None:
            self._watch_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_watch_tick, self._watch_timer)
        folder = self.config.get("monitor_folder", "")
        if folder and os.path.isdir(folder):
            self._watch_timer.Start(
                max(1000, int(self.config.get("watch_interval_ms", 5000))))
        else:
            self._watch_timer.Stop()

    def _on_watch_tick(self, event):
        folder = self.config.get("monitor_folder", "")
        if not folder or not os.path.isdir(folder):
            if self._watch_timer is not None:
                self._watch_timer.Stop()
            return
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        save_path = self.config.get("save_path", "")
        added = 0
        for name in names:
            if not name.lower().endswith(".torrent"):
                continue
            path = os.path.join(folder, name)
            if path in self._watch_seen:
                continue
            if not os.path.isfile(path):
                continue
            self._watch_seen.add(path)
            self.engine.post("add_torrent_file", path=path, save_path=save_path)
            added += 1
        if added:
            self.add_log("Watch folder: added %d torrent files" % added)
            self.announce("Watch folder added %d torrent%s"
                          % (added, "" if added == 1 else "s"))

    def _on_close(self, event):
        if self._closed:
            event.Skip()
            return
        self._closed = True
        self.timer.Stop()
        if self._watch_timer is not None:
            self._watch_timer.Stop()
        if self._schedule_timer is not None:
            self._schedule_timer.Stop()
        if self.rss is not None:
            self.rss.stop()
        if self.remote is not None:
            self.remote.stop()
        self._save_window_state()
        self.config["columns"] = self.column_state
        self.config["sort_column"] = self.sort_key
        self.config["sort_reverse"] = self.sort_reverse
        self.config["category_filter"] = self.current_filter
        self.config["label_filter"] = self.current_label
        self.store_handle.settings.update(self.config)
        self.store_handle.settings["columns"] = self.column_state
        self.store_handle.save()
        announcer().speak("Saving resume data and closing")
        self.engine.stop()
        self.store_handle.save_bookmarks()
        if self.tray is not None:
            try:
                self.tray.RemoveIcon()
            except Exception:
                pass
        event.Skip()


# Files tab priority menu: the four levels and their libtorrent values.
FILE_PRIORITIES = (("&Skip, do not download", 0), ("&Low", 1),
                   ("&Normal", 4), ("&High", 7))


def _file_level(value):
    """libtorrent's 0 to 7 file priority, rounded to the menu's four."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 4
    if value <= 0:
        return 0
    if value <= 2:
        return 1
    if value <= 5:
        return 4
    return 7


def _priority_name(value):
    return {0: "Skip", 1: "Low", 2: "Normal", 3: "High", 4: "Normal",
            5: "Normal", 6: "Normal", 7: "High"}.get(value, "Normal")


def _safe_filename(name):
    bad = '<>:"/\\|?*'
    cleaned = "".join(ch if ch not in bad else "_" for ch in name)
    return cleaned.strip() or "torrent"


def _quote(text):
    from urllib.parse import quote
    return quote(text, safe="")
