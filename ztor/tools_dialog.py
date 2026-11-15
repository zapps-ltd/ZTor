"""Create Torrent and Search windows.

Slow work runs on a worker thread; results come back through wx.CallAfter.
Progress is spoken at each quarter, so a screen reader user hears it without
having to look for the bar.
"""

import os
import threading

import wx

from . import creator
from . import format as fmt
from . import search as searchmod
from .a11y import fix_check_boxes
from .dialogs import _std_buttons


def _announce(frame, text):
    if frame is not None and hasattr(frame, "announce"):
        frame.announce(text)


class CreateTorrentDialog(wx.Dialog):
    """Make a .torrent file from a file or folder, and optionally seed it."""

    def __init__(self, parent, frame):
        wx.Dialog.__init__(self, parent, title="Create Torrent", size=(660, 660),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.frame = frame
        self.worker = None
        self.cancel_flag = False
        self.alive = True
        self._last = -1
        self._spoken = 0
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="&Share this file or folder:"), 0,
                  wx.LEFT | wx.TOP, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.source = wx.TextCtrl(panel)
        self.source.SetName("File or folder to share")
        choose_file = wx.Button(panel, label="Choose &File...")
        choose_folder = wx.Button(panel, label="Choose F&older...")
        row.Add(self.source, 1, wx.RIGHT, 6)
        row.Add(choose_file, 0, wx.RIGHT, 6)
        row.Add(choose_folder, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        def area(label, name, height):
            sizer.Add(wx.StaticText(panel, label=label), 0, wx.LEFT | wx.TOP, 8)
            control = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, height))
            control.SetName(name)
            sizer.Add(control, 0, wx.EXPAND | wx.ALL, 8)
            return control

        self.trackers = area("&Trackers, one per line:",
                             "Trackers, one per line", 80)
        self.web_seeds = area("&Web seeds, one per line:",
                              "Web seeds, one per line", 50)
        sizer.Add(wx.StaticText(panel, label="Co&mment:"), 0, wx.LEFT | wx.TOP, 8)
        self.comment = wx.TextCtrl(panel)
        self.comment.SetName("Comment")
        sizer.Add(self.comment, 0, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label="&Piece size:"), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.piece = wx.Choice(panel, choices=[n for n, _v in creator.PIECE_SIZES])
        self.piece.SetSelection(0)
        row.Add(self.piece, 0)
        sizer.Add(row, 0, wx.ALL, 8)

        self.private = wx.CheckBox(
            panel, label="P&rivate torrent, for private trackers only")
        sizer.Add(self.private, 0, wx.ALL, 8)
        self.seed = wx.CheckBox(panel, label="Start s&eeding when it is made")
        self.seed.SetValue(True)
        sizer.Add(self.seed, 0, wx.ALL, 8)

        self.gauge = wx.Gauge(panel, range=1000)
        self.gauge.SetName("Hashing progress")
        sizer.Add(self.gauge, 0, wx.EXPAND | wx.ALL, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.create_button = wx.Button(panel, label="&Create...")
        self.close_button = wx.Button(panel, wx.ID_CANCEL, label="Cancel")
        self.create_button.SetDefault()
        buttons.Add(self.create_button, 0, wx.RIGHT, 8)
        buttons.Add(self.close_button, 0)
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)

        self.inputs = [self.source, choose_file, choose_folder, self.trackers,
                       self.web_seeds, self.comment, self.piece, self.private,
                       self.seed, self.create_button]
        choose_file.Bind(wx.EVT_BUTTON, self._on_choose_file)
        choose_folder.Bind(wx.EVT_BUTTON, self._on_choose_folder)
        self.create_button.Bind(wx.EVT_BUTTON, self._on_create)
        self.close_button.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        fix_check_boxes(self)
        self.source.SetFocus()

    # ------------------------------------------------------------ choosing
    def _on_choose_file(self, event):
        with wx.FileDialog(self, "File to share",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.source.SetValue(dialog.GetPath())
                self.source.SetFocus()

    def _on_choose_folder(self, event):
        with wx.DirDialog(self, "Folder to share",
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) \
                as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.source.SetValue(dialog.GetPath())
                self.source.SetFocus()

    # ------------------------------------------------------------ creating
    def _on_create(self, event):
        source = os.path.abspath(self.source.GetValue().strip() or ".")
        if not self.source.GetValue().strip() or not os.path.exists(source):
            wx.MessageBox("Choose an existing file or folder to share.",
                          "Create Torrent", wx.OK | wx.ICON_INFORMATION, self)
            self.source.SetFocus()
            return
        name = os.path.basename(source.rstrip("\\/")) or "torrent"
        with wx.FileDialog(self, "Save the torrent file as",
                           defaultDir=os.path.dirname(source),
                           defaultFile=name + ".torrent",
                           wildcard="Torrent files (*.torrent)|*.torrent",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            target = dialog.GetPath()
        options = {
            "path": source,
            "trackers": creator.lines(self.trackers.GetValue()),
            "web_seeds": creator.lines(self.web_seeds.GetValue()),
            "comment": self.comment.GetValue().strip(),
            "private": self.private.GetValue(),
            "piece_size": creator.PIECE_SIZES[max(0, self.piece.GetSelection())][1],
        }
        seed = self.seed.GetValue()
        for control in self.inputs:
            control.Enable(False)
        self.cancel_flag = False
        self._last = -1
        self._spoken = 0
        self.gauge.SetValue(0)
        self.close_button.SetFocus()
        _announce(self.frame, "Hashing %s. Escape cancels." % name)

        def work():
            try:
                blob = creator.create(progress=self._progress,
                                      cancelled=lambda: self.cancel_flag,
                                      **options)
                with open(target, "wb") as handle:
                    handle.write(blob)
                wx.CallAfter(self._done, blob, source, target, seed, "")
            except creator.Cancelled:
                wx.CallAfter(self._done, None, source, target, seed, "cancelled")
            except Exception as exc:
                wx.CallAfter(self._done, None, source, target, seed,
                             str(exc) or exc.__class__.__name__)

        self.worker = threading.Thread(target=work, name="create-torrent",
                                       daemon=True)
        self.worker.start()

    def _progress(self, done, total):
        permille = int(1000 * done / max(1, total))
        if permille != self._last:
            self._last = permille
            wx.CallAfter(self._set_progress, permille)

    def _set_progress(self, permille):
        if not self.alive:
            return
        self.gauge.SetValue(permille)
        quarter = permille // 250
        if 0 < quarter < 4 and quarter > self._spoken:
            self._spoken = quarter
            _announce(self.frame, "%d percent" % (quarter * 25))

    def _done(self, blob, source, target, seed, error):
        self.worker = None
        if not self.alive:
            return
        if error:
            for control in self.inputs:
                control.Enable(True)
            self.gauge.SetValue(0)
            if error == "cancelled":
                _announce(self.frame, "Cancelled")
            else:
                wx.MessageBox("The torrent could not be made.\n\n%s" % error,
                              "Create Torrent", wx.OK | wx.ICON_ERROR, self)
            self.create_button.SetFocus()
            return
        self.gauge.SetValue(1000)
        if seed and blob:
            self.frame.engine.post("add_torrent_blob", blob=blob,
                                   save_path=os.path.dirname(source),
                                   exact=True)
        if hasattr(self.frame, "add_log"):
            self.frame.add_log("Torrent file made: %s" % target)
        _announce(self.frame, "Torrent file saved%s"
                  % (", seeding started" if seed else ""))
        self.alive = False
        self.EndModal(wx.ID_OK)

    # ------------------------------------------------------------- closing
    def _on_cancel(self, event):
        if self.worker is not None:
            self.cancel_flag = True
            _announce(self.frame, "Cancelling")
            return
        self.alive = False
        self.EndModal(wx.ID_CANCEL)

    def _on_close(self, event):
        if self.worker is not None:
            self.cancel_flag = True
            event.Veto()
            return
        self.alive = False
        event.Skip()


class SearchDialog(wx.Dialog):
    """Search a torrent source and add results."""

    def __init__(self, parent, frame):
        wx.Dialog.__init__(self, parent, title="Search", size=(860, 620),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.frame = frame
        self.config = getattr(frame, "config", None) or {}
        self.providers = searchmod.providers(self.config)
        self.results = []
        self.busy = False
        self.alive = True
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label="Search &for:"), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.query = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.query.SetName("Search for")
        row.Add(self.query, 1, wx.RIGHT, 8)
        row.Add(wx.StaticText(panel, label="Sour&ce:"), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.provider = wx.Choice(panel, choices=[n for _k, n in
                                                  self.providers])
        self.provider.SetSelection(len(self.providers) - 1)
        row.Add(self.provider, 0, wx.RIGHT, 8)
        self.search_button = wx.Button(panel, label="&Search")
        self.search_button.SetDefault()
        row.Add(self.search_button, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        sizer.Add(wx.StaticText(panel, label="&Results:"), 0, wx.LEFT, 8)
        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT)
        for index, (title, width) in enumerate((("Title", 420), ("Size", 100),
                                                ("Seeds", 70), ("Date", 110),
                                                ("Source", 150))):
            self.list.InsertColumn(index, title, width=width)
        self.list.SetName("Search results")
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("&Download Selected", self._on_download),
                               ("Open &Page", self._on_open_page),
                               ("Copy &Link", self._on_copy)):
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 6)
        sizer.Add(row, 0, wx.LEFT | wx.RIGHT, 8)

        note = wx.StaticText(panel, label="Enter on a result downloads it. "
                                          "Internet Archive items are free to "
                                          "share. Jackett or Prowlarr can be "
                                          "added in Preferences, on the "
                                          "Search page.")
        note.Wrap(780)
        sizer.Add(note, 0, wx.ALL, 8)
        close = _std_buttons(panel, wx.CLOSE)
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)

        self.search_button.Bind(wx.EVT_BUTTON, self._on_search)
        self.query.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_download)
        self.Bind(wx.EVT_BUTTON, self._on_close_button, id=wx.ID_CLOSE)
        self.SetEscapeId(wx.ID_CLOSE)
        self.query.SetFocus()

    def _on_close_button(self, event):
        self.alive = False
        self.EndModal(wx.ID_CLOSE)

    def _on_search(self, event):
        if self.busy:
            _announce(self.frame, "Still searching")
            return
        query = self.query.GetValue().strip()
        if not query:
            _announce(self.frame, "Type something to search for")
            self.query.SetFocus()
            return
        index = max(0, self.provider.GetSelection())
        provider = self.providers[index][0]
        config = dict(self.config)
        self.busy = True
        _announce(self.frame, "Searching")

        def work():
            try:
                results, error = searchmod.search(provider, query,
                                                  config=config), ""
            except Exception as exc:
                results, error = [], str(exc) or exc.__class__.__name__
            wx.CallAfter(self._show, results, error, query)

        threading.Thread(target=work, name="search", daemon=True).start()

    def _show(self, results, error, query):
        self.busy = False
        if not self.alive:
            return
        self.results = results
        self.list.DeleteAllItems()
        for index, item in enumerate(results):
            row = self.list.InsertItem(index, item["title"])
            self.list.SetItem(row, 1, fmt.human_size(item["size"])
                              if item["size"] else "")
            seeds = item.get("seeds")
            self.list.SetItem(row, 2, "" if seeds is None else str(seeds))
            self.list.SetItem(row, 3, item["date"])
            self.list.SetItem(row, 4, item["source"])
        if error:
            _announce(self.frame, "Search failed: %s" % error)
            return
        _announce(self.frame, "%d result%s for %s"
                  % (len(results), "" if len(results) == 1 else "s", query))
        if results:
            self.list.SetItemState(0, wx.LIST_STATE_SELECTED |
                                   wx.LIST_STATE_FOCUSED,
                                   wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED)
            self.list.SetFocus()

    def _selected(self):
        chosen = []
        index = self.list.GetFirstSelected()
        while index >= 0:
            if index < len(self.results):
                chosen.append(self.results[index])
            index = self.list.GetNextSelected(index)
        return chosen

    def _on_download(self, event):
        items = self._selected()
        if not items:
            _announce(self.frame, "Select one or more results first")
            return
        rss = getattr(self.frame, "rss", None)
        if rss is None:
            return
        rss.download_items([{"title": i["title"], "link": i["link"]}
                            for i in items])

    def _on_open_page(self, event):
        items = self._selected()
        if not items:
            _announce(self.frame, "Select a result first")
            return
        page = items[0].get("page") or ""
        if not page and items[0]["link"].lower().startswith("http"):
            page = items[0]["link"]
        if not page:
            _announce(self.frame, "This result has no web page")
            return
        wx.LaunchDefaultBrowser(page)

    def _on_copy(self, event):
        items = self._selected()
        if not items:
            _announce(self.frame, "Select a result first")
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(
                "\n".join(i["link"] for i in items)))
            wx.TheClipboard.Close()
            _announce(self.frame, "Link copied to the clipboard")
