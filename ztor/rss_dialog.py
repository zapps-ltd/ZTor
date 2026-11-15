"""RSS feeds window, rule editor, and the controller that checks feeds.

Network work happens on a worker thread; every change to the feed store and
every engine command happens back on the GUI thread through wx.CallAfter.
"""

import threading

import wx

from . import rss
from .a11y import fix_check_boxes
from .dialogs import _std_buttons, SingleLineDialog


class RssController(object):
    """Owns the feed store, the check timer, and the worker thread."""

    FIRST_CHECK_MS = 20000

    def __init__(self, frame):
        self.frame = frame
        self.store = rss.RssStore()
        self.busy = False
        self.closed = False
        self.dialog = None
        self.timer = wx.Timer(frame)
        frame.Bind(wx.EVT_TIMER, self._on_timer, self.timer)
        self.reschedule()
        wx.CallLater(self.FIRST_CHECK_MS, self._first_check)

    # ------------------------------------------------------------- timing
    def _alive(self):
        return not self.closed and not getattr(self.frame, "_closed", False)

    def reschedule(self):
        try:
            minutes = max(5, int(self.frame.config.get("rss_interval_min", 30)
                                 or 30))
        except (TypeError, ValueError):
            minutes = 30
        if any(f.get("enabled", True) for f in self.store.feeds):
            self.timer.Start(minutes * 60000)
        else:
            self.timer.Stop()

    def stop(self):
        self.closed = True
        self.timer.Stop()

    def _first_check(self):
        if self._alive():
            self.refresh()

    def _on_timer(self, event):
        if self._alive():
            self.refresh()

    # ------------------------------------------------------------ checking
    def refresh(self, urls=None, manual=False):
        if self.busy:
            if manual:
                self.frame.announce("Feeds are already being checked")
            return
        feeds = [dict(f) for f in self.store.feeds
                 if f.get("enabled", True) and (urls is None or f["url"] in urls)]
        if not feeds:
            if manual:
                self.frame.announce("There are no feeds to check")
            return
        first = set(f["url"] for f in feeds if not f.get("last_checked"))
        rules = []
        if self.frame.config.get("rss_auto_download", True):
            rules = [dict(r) for r in self.store.rules]
        seen = set(self.store.seen)
        self.busy = True

        def work():
            try:
                outcome = rss.check_feeds(feeds, rules, seen, first)
            except Exception as exc:
                outcome = ([{"url": "", "title": "", "items": [],
                             "error": str(exc)}], [], [])
            wx.CallAfter(self._done, outcome, manual)

        threading.Thread(target=work, name="rss", daemon=True).start()
        if manual:
            self.frame.announce("Checking %d feed%s"
                                % (len(feeds), "" if len(feeds) == 1 else "s"))

    def _done(self, outcome, manual):
        self.busy = False
        if not self._alive():
            return
        results, downloads, new_keys = outcome
        failed = 0
        for result in results:
            if not result.get("url"):
                failed += 1
                continue
            if result.get("error"):
                failed += 1
                self.store.update_feed(result["url"], "", [], result["error"])
                self.frame.add_log("RSS feed failed: %s: %s"
                                   % (result["url"], result["error"]))
            else:
                self.store.update_feed(result["url"], result.get("title", ""),
                                       result.get("items", []))
        self.store.mark_seen(new_keys)
        added = sum(1 for entry in downloads if self._add(entry))
        self.store.save()
        if added:
            self.frame.notify("RSS added %d torrent%s"
                              % (added, "" if added == 1 else "s"))
        if manual:
            self.frame.announce("Feeds checked. %d new torrent%s, %d feed%s "
                                "failed" % (added, "" if added == 1 else "s",
                                            failed, "" if failed == 1 else "s"))
        if self.dialog is not None:
            self.dialog.refresh()

    def _add(self, entry):
        rule = entry.get("rule") or {}
        save_path = rule.get("save_path") or self.frame.config.get("save_path")
        label = rule.get("label", "") or ""
        if entry.get("blob"):
            self.frame.engine.post("add_torrent_blob", blob=entry["blob"],
                                   save_path=save_path, label=label)
        elif entry.get("link", "").lower().startswith("magnet:"):
            self.frame.engine.post("add_magnet", uri=entry["link"],
                                   save_path=save_path, label=label)
        else:
            self.frame.add_log("RSS could not get %s: %s"
                               % (entry.get("title", ""), entry.get("error", "")))
            return False
        self.frame.add_log("RSS adding %s" % entry.get("title", ""))
        return True

    def download_items(self, items):
        """Download articles chosen by hand in the RSS window."""
        items = [dict(i) for i in items]

        def work():
            entries = [rss.fetch_item(item) for item in items]
            wx.CallAfter(self._manual_done, entries)

        threading.Thread(target=work, name="rss-download", daemon=True).start()
        self.frame.announce("Downloading %d article%s"
                            % (len(items), "" if len(items) == 1 else "s"))

    def _manual_done(self, entries):
        if not self._alive():
            return
        added = sum(1 for entry in entries if self._add(entry))
        self.frame.announce("Added %d of %d" % (added, len(entries)))

    # ------------------------------------------------------------- editing
    def add_feed(self, url):
        feed = self.store.add_feed(url)
        if feed is None:
            self.frame.announce("That feed is already in the list, or the "
                                "address is empty")
            return False
        self.store.save()
        self.reschedule()
        self.refresh(urls=[feed["url"]], manual=True)
        return True

    def remove_feed(self, url):
        self.store.remove_feed(url)
        self.store.rules[:] = [r for r in self.store.rules
                               if r.get("feed", "") != url]
        self.store.save()
        self.reschedule()

    def toggle_feed(self, url):
        feed = self.store.find_feed(url)
        if feed is None:
            return None
        feed["enabled"] = not feed.get("enabled", True)
        self.store.save()
        self.reschedule()
        return feed["enabled"]

    def save_rules(self):
        self.store.save()

    # -------------------------------------------------------------- window
    def show(self):
        if self.dialog is not None:
            self.dialog.Raise()
            return
        self.dialog = RssDialog(self.frame, self)
        try:
            self.dialog.ShowModal()
        finally:
            self.dialog.Destroy()
            self.dialog = None


def _feed_name(feed):
    return feed.get("name") or feed.get("url", "")


class RssDialog(wx.Dialog):
    """Feeds, their articles, and download rules, all in plain lists."""

    def __init__(self, parent, controller):
        wx.Dialog.__init__(self, parent, title="RSS Feeds", size=(860, 680),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.controller = controller
        self.store = controller.store
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        outer.Add(wx.StaticText(panel, label="&Feeds:"), 0, wx.LEFT | wx.TOP, 8)
        self.feeds = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (title, width) in enumerate((("Name", 260), ("Address", 330),
                                                ("Articles", 80),
                                                ("Status", 140))):
            self.feeds.InsertColumn(index, title, width=width)
        self.feeds.SetName("Feeds")
        outer.Add(self.feeds, 1, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("&Add Feed...", self._on_add_feed),
                               ("&Remove Feed", self._on_remove_feed),
                               ("&Enable or Disable", self._on_toggle_feed),
                               ("Check &Now", self._on_refresh)):
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 6)
        outer.Add(row, 0, wx.LEFT | wx.RIGHT, 8)

        outer.Add(wx.StaticText(panel, label="A&rticles in the selected feed:"),
                  0, wx.LEFT | wx.TOP, 8)
        self.articles = wx.ListCtrl(panel, style=wx.LC_REPORT)
        self.articles.InsertColumn(0, "Title", width=600)
        self.articles.InsertColumn(1, "Date", width=200)
        self.articles.SetName("Articles")
        outer.Add(self.articles, 2, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("&Download Selected", self._on_download),
                               ("&Copy Link", self._on_copy_link)):
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 6)
        outer.Add(row, 0, wx.LEFT | wx.RIGHT, 8)

        outer.Add(wx.StaticText(panel, label="Download r&ules:"), 0,
                  wx.LEFT | wx.TOP, 8)
        self.rules = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (title, width) in enumerate((("Name", 160),
                                                ("Must contain", 200),
                                                ("Must not contain", 160),
                                                ("Feed", 160), ("On", 50))):
            self.rules.InsertColumn(index, title, width=width)
        self.rules.SetName("Download rules")
        outer.Add(self.rules, 1, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("Add Ru&le...", self._on_add_rule),
                               ("Ed&it Rule...", self._on_edit_rule),
                               ("Remo&ve Rule", self._on_remove_rule)):
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 6)
        outer.Add(row, 0, wx.LEFT | wx.RIGHT, 8)

        close = _std_buttons(panel, wx.CLOSE)
        outer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(outer)

        self.feeds.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self._fill_articles())
        self.articles.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_download)
        self.rules.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_edit_rule)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE),
                  id=wx.ID_CLOSE)
        self.SetEscapeId(wx.ID_CLOSE)
        self.refresh()
        self.feeds.SetFocus()

    # ------------------------------------------------------------- filling
    def _announce(self, text):
        frame = self.controller.frame
        if hasattr(frame, "announce"):
            frame.announce(text)

    def refresh(self):
        selected = self._selected_feed_url()
        self.feeds.DeleteAllItems()
        for index, feed in enumerate(self.store.feeds):
            item = self.feeds.InsertItem(index, _feed_name(feed))
            self.feeds.SetItem(item, 1, feed.get("url", ""))
            self.feeds.SetItem(item, 2, str(len(feed.get("items") or [])))
            if not feed.get("enabled", True):
                status = "off"
            elif feed.get("error"):
                status = "failed: %s" % feed["error"]
            elif feed.get("last_checked"):
                status = "ok"
            else:
                status = "not checked yet"
            self.feeds.SetItem(item, 3, status)
        target = 0
        for index, feed in enumerate(self.store.feeds):
            if feed.get("url") == selected:
                target = index
        if self.feeds.GetItemCount():
            self.feeds.SetItemState(target, wx.LIST_STATE_SELECTED |
                                    wx.LIST_STATE_FOCUSED,
                                    wx.LIST_STATE_SELECTED |
                                    wx.LIST_STATE_FOCUSED)
        self._fill_articles()
        self._fill_rules()

    def _selected_feed(self):
        index = self.feeds.GetFirstSelected()
        if 0 <= index < len(self.store.feeds):
            return self.store.feeds[index]
        return None

    def _selected_feed_url(self):
        feed = self._selected_feed()
        return feed.get("url") if feed else None

    def _fill_articles(self):
        self.articles.DeleteAllItems()
        feed = self._selected_feed()
        if feed is None:
            return
        for index, item in enumerate(feed.get("items") or []):
            row = self.articles.InsertItem(index, item.get("title", ""))
            self.articles.SetItem(row, 1, item.get("date", ""))

    def _fill_rules(self):
        self.rules.DeleteAllItems()
        for index, rule in enumerate(self.store.rules):
            item = self.rules.InsertItem(index, rule.get("name", ""))
            self.rules.SetItem(item, 1, rule.get("must_contain", ""))
            self.rules.SetItem(item, 2, rule.get("must_not_contain", ""))
            feed = self.store.find_feed(rule.get("feed", ""))
            self.rules.SetItem(item, 3, _feed_name(feed) if feed else "All feeds")
            self.rules.SetItem(item, 4, "yes" if rule.get("enabled", True)
                               else "no")

    # --------------------------------------------------------------- feeds
    def _on_add_feed(self, event):
        dialog = SingleLineDialog(
            self, "Add Feed", "Feed address:", "",
            "Articles already in the feed are listed but not downloaded. "
            "Rules act on new articles.")
        if dialog.ShowModal() == wx.ID_OK and dialog.value:
            if self.controller.add_feed(dialog.value):
                self.refresh()
        dialog.Destroy()

    def _on_remove_feed(self, event):
        feed = self._selected_feed()
        if feed is None:
            self._announce("No feed selected")
            return
        if wx.MessageBox("Remove the feed %s and its rules?" % _feed_name(feed),
                         "RSS Feeds", wx.YES_NO | wx.NO_DEFAULT, self) != wx.YES:
            return
        self.controller.remove_feed(feed["url"])
        self.refresh()
        self._announce("Feed removed")

    def _on_toggle_feed(self, event):
        feed = self._selected_feed()
        if feed is None:
            self._announce("No feed selected")
            return
        enabled = self.controller.toggle_feed(feed["url"])
        self.refresh()
        self._announce("Feed %s" % ("on" if enabled else "off"))

    def _on_refresh(self, event):
        self.controller.refresh(manual=True)

    # ------------------------------------------------------------ articles
    def _selected_articles(self):
        feed = self._selected_feed()
        if feed is None:
            return []
        items = feed.get("items") or []
        chosen = []
        index = self.articles.GetFirstSelected()
        while index >= 0:
            if index < len(items):
                chosen.append(items[index])
            index = self.articles.GetNextSelected(index)
        return chosen

    def _on_download(self, event):
        items = self._selected_articles()
        if not items:
            self._announce("Select one or more articles first")
            return
        self.controller.download_items(items)

    def _on_copy_link(self, event):
        items = self._selected_articles()
        if not items:
            self._announce("Select an article first")
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(
                "\n".join(i.get("link", "") for i in items)))
            wx.TheClipboard.Close()
            self._announce("Link copied to the clipboard")

    # --------------------------------------------------------------- rules
    def _selected_rule_index(self):
        index = self.rules.GetFirstSelected()
        if 0 <= index < len(self.store.rules):
            return index
        return -1

    def _on_add_rule(self, event):
        feed = self._selected_feed()
        dialog = RuleDialog(self, self.store.feeds,
                            {"feed": feed["url"] if feed else ""})
        if dialog.ShowModal() == wx.ID_OK and dialog.result:
            self.store.rules.append(dialog.result)
            self.controller.save_rules()
            self._fill_rules()
            self._announce("Rule added")
        dialog.Destroy()

    def _on_edit_rule(self, event):
        index = self._selected_rule_index()
        if index < 0:
            self._announce("No rule selected")
            return
        dialog = RuleDialog(self, self.store.feeds, self.store.rules[index])
        if dialog.ShowModal() == wx.ID_OK and dialog.result:
            self.store.rules[index] = dialog.result
            self.controller.save_rules()
            self._fill_rules()
            self._announce("Rule saved")
        dialog.Destroy()

    def _on_remove_rule(self, event):
        index = self._selected_rule_index()
        if index < 0:
            self._announce("No rule selected")
            return
        del self.store.rules[index]
        self.controller.save_rules()
        self._fill_rules()
        self._announce("Rule removed")


class RuleDialog(wx.Dialog):
    """One download rule."""

    def __init__(self, parent, feeds, rule=None):
        wx.Dialog.__init__(self, parent, title="Download Rule", size=(600, 470))
        rule = dict(rule or {})
        self.result = None
        self.feed_urls = [""] + [f.get("url", "") for f in feeds]
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        def row(label, control):
            sizer.Add(wx.StaticText(panel, label=label), 0, wx.LEFT | wx.TOP, 8)
            sizer.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self.name = wx.TextCtrl(panel, value=rule.get("name", ""))
        self.name.SetName("Rule name")
        row("Rule &name:", self.name)
        self.must = wx.TextCtrl(panel, value=rule.get("must_contain", ""))
        self.must.SetName("Words the title must contain, required. Start with "
                          "re colon for a regular expression")
        row("&Must contain, every word, or re: for a pattern:", self.must)
        self.must_not = wx.TextCtrl(panel, value=rule.get("must_not_contain", ""))
        self.must_not.SetName("Words that must not appear")
        row("Must &not contain, any of these words:", self.must_not)
        self.feed = wx.Choice(panel, choices=["All feeds"] + [
            _feed_name(f) for f in feeds])
        current = rule.get("feed", "")
        self.feed.SetSelection(self.feed_urls.index(current)
                               if current in self.feed_urls else 0)
        row("&Feed:", self.feed)
        self.save_path = wx.DirPickerCtrl(panel, path=rule.get("save_path", ""),
                                          style=wx.DIRP_USE_TEXTCTRL)
        self.save_path.SetName("Save to folder, blank for the default")
        row("&Save to folder, blank for the default:", self.save_path)
        self.label = wx.TextCtrl(panel, value=rule.get("label", ""))
        self.label.SetName("Label for downloaded torrents")
        row("&Label:", self.label)
        self.enabled = wx.CheckBox(panel, label="Rule is &on")
        self.enabled.SetValue(bool(rule.get("enabled", True)))
        sizer.Add(self.enabled, 0, wx.ALL, 8)
        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        sizer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        fix_check_boxes(self)
        self.name.SetFocus()

    def _on_ok(self, event):
        must = self.must.GetValue().strip()
        if not must:
            wx.MessageBox("Enter at least one word the title must contain.",
                          "Download Rule", wx.OK | wx.ICON_INFORMATION, self)
            self.must.SetFocus()
            return
        index = self.feed.GetSelection()
        self.result = {
            "name": self.name.GetValue().strip() or must,
            "must_contain": must,
            "must_not_contain": self.must_not.GetValue().strip(),
            "feed": self.feed_urls[index] if 0 <= index < len(self.feed_urls)
            else "",
            "save_path": self.save_path.GetPath(),
            "label": self.label.GetValue().strip(),
            "enabled": self.enabled.GetValue(),
        }
        event.Skip()
