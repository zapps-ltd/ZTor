"""Dialogs: bookmark columns, options, add torrent, labels, and pickers.

Every control is a standard wx widget with a visible label and a keyboard
mnemonic, so NVDA and JAWS read it without extra work. Nothing here uses images
or owner drawn lists.
"""

import os
import re
import socket

import wx
import wx.lib.scrolledpanel as scrolled

from . import columns as colmod
from . import tuning
from .a11y import fix_check_boxes
from .icons import image_list

# Preferences sections: (builder name, title, icon), in page order. The page
# numbers used by OptionsDialog.validate follow this order.
OPTION_PAGES = (
    ("_build_general", "General", "prefs"),
    ("_build_connection", "Connection", "globe"),
    ("_build_speed", "Speed", "alt_speed"),
    ("_build_bandwidth", "Bandwidth", "down"),
    ("_build_scheduler", "Scheduler", "clock"),
    ("_build_queue", "Queueing", "queue_up"),
    ("_build_seeding", "Seeding", "up"),
    ("_build_rss", "RSS", "rss"),
    ("_build_proxy", "Proxy", "lock"),
    ("_build_display", "Display", "display"),
    ("_build_access", "Accessibility", "access"),
    ("_build_remote", "Remote", "phone"),
    ("_build_search", "Search", "search"),
)


def local_addresses():
    """This computer's addresses, including a VPN adapter's, for binding."""
    found = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        infos = []
    for info in infos:
        address = info[4][0].split("%")[0]
        if address.lower().startswith("fe80") or address in found:
            continue
        found.append(address)
    return found


def _user32():
    """A private user32 handle with 64 bit safe argument types."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetWindow.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    return user32


UDM_GETBUDDY = 0x046A
GW_HWNDPREV = 3


def spin_buddy(spin):
    """The edit box of a native spin control, or None.

    On Windows a wx.SpinCtrl is two sibling windows: the arrows, which are
    the wx window, and a separate edit box created just before them.
    """
    try:
        buddy = _user32().SendMessageW(int(spin.GetHandle()), UDM_GETBUDDY,
                                       0, 0)
        return buddy or None
    except Exception:
        return None


def window_before_text(hwnd):
    """The text of the window just before hwnd in the window order."""
    try:
        import ctypes
        user32 = _user32()
        previous = user32.GetWindow(hwnd, GW_HWNDPREV)
        if not previous:
            return ""
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(previous, buffer, 512)
        return buffer.value
    except Exception:
        return ""


def label_before(label, control):
    """Put a label just before its control in the window order.

    Windows names a box after the static text right before it, so a label
    created after its box would name the next box instead. A spin control's
    edit box is a separate window, so the label goes before that too.
    """
    try:
        label.MoveBeforeInTabOrder(control)
    except Exception:
        pass
    if isinstance(control, wx.SpinCtrl):
        buddy = spin_buddy(control)
        if buddy:
            try:
                user32 = _user32()
                label_hwnd = int(label.GetHandle())
                previous = user32.GetWindow(buddy, GW_HWNDPREV)
                # Insert the label after the window before the edit box,
                # which puts it right in front of the edit box. 0 is the top.
                if (previous or 0) != label_hwnd:
                    user32.SetWindowPos(label_hwnd, previous or 0,
                                        0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            except Exception:
                pass
    return label


def _kb(value):
    """A stored limit in bytes per second, shown in kilobytes per second."""
    try:
        return max(0, int(value or 0)) // 1000
    except (TypeError, ValueError):
        return 0

def _std_buttons(panel, flags):
    """A standard OK and Cancel row whose buttons are children of the panel.

    wx.Dialog.CreateStdDialogButtonSizer parents its buttons to the dialog, which
    breaks layouts built on a panel, so build the row by hand instead.
    """
    sizer = wx.StdDialogButtonSizer()
    buttons = []
    if flags & wx.OK:
        buttons.append(wx.Button(panel, wx.ID_OK, label="OK"))
    if flags & wx.CANCEL:
        buttons.append(wx.Button(panel, wx.ID_CANCEL, label="Cancel"))
    if flags & wx.CLOSE:
        buttons.append(wx.Button(panel, wx.ID_CLOSE, label="Close"))
    if flags & wx.YES:
        buttons.append(wx.Button(panel, wx.ID_YES, label="Yes"))
    if flags & wx.NO:
        buttons.append(wx.Button(panel, wx.ID_NO, label="No"))
    for button in buttons:
        sizer.AddButton(button)
    sizer.Realize()
    if buttons:
        buttons[0].SetDefault()
    return sizer




class ColumnsDialog(wx.Dialog):
    """Turn each torrent list column on or off and move it up or down."""

    def __init__(self, parent, state):
        wx.Dialog.__init__(self, parent, title="Select Columns",
                           size=(520, 620))
        self.state = colmod.normalize(state)
        self.result = None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        help_text = wx.StaticText(
            panel, label="Check the columns you want to see. "
                         "Use Move Up and Move Down to change their order.")
        outer.Add(help_text, 0, wx.ALL, 10)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.InsertColumn(0, "Column", width=240)
        self.list.InsertColumn(1, "Shown", width=90)
        self.list.SetName("Column list")
        outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self._fill()

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.up_button = wx.Button(panel, label="Move &Up")
        self.down_button = wx.Button(panel, label="Move &Down")
        self.show_button = wx.Button(panel, label="&Show")
        self.hide_button = wx.Button(panel, label="&Hide")
        buttons.Add(self.up_button, 0, wx.RIGHT, 6)
        buttons.Add(self.down_button, 0, wx.RIGHT, 6)
        buttons.Add(self.show_button, 0, wx.RIGHT, 6)
        buttons.Add(self.hide_button, 0, wx.RIGHT, 6)
        outer.Add(buttons, 0, wx.ALL, 10)

        row2 = wx.BoxSizer(wx.HORIZONTAL)
        self.width_spin = wx.SpinCtrl(panel, min=40, max=900, initial=120)
        row2.Add(label_before(wx.StaticText(panel,
                                            label="Width of selected column:"),
                              self.width_spin), 0,
                 wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        row2.Add(self.width_spin, 0, wx.RIGHT, 6)
        apply_width = wx.Button(panel, label="&Apply Width")
        row2.Add(apply_width, 0)
        outer.Add(row2, 0, wx.ALL, 10)

        preset = wx.BoxSizer(wx.HORIZONTAL)
        preset.Add(wx.StaticText(panel, label="Quick set:"), 0,
                   wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.preset_choice = wx.Choice(
            panel, choices=["Classic uTorrent columns",
                            "Every column",
                            "Only name, size, status",
                            "Reset to defaults"])
        self.preset_choice.SetSelection(0)
        preset.Add(self.preset_choice, 1, wx.RIGHT, 6)
        apply_preset = wx.Button(panel, label="Appl&y")
        preset.Add(apply_preset, 0)
        outer.Add(preset, 0, wx.EXPAND | wx.ALL, 10)

        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        outer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(outer)

        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.up_button.Bind(wx.EVT_BUTTON, self._on_up)
        self.down_button.Bind(wx.EVT_BUTTON, self._on_down)
        self.show_button.Bind(wx.EVT_BUTTON, self._on_show)
        self.hide_button.Bind(wx.EVT_BUTTON, self._on_hide)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_toggle)
        apply_width.Bind(wx.EVT_BUTTON, self._on_width)
        apply_preset.Bind(wx.EVT_BUTTON, self._on_preset)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.list.SetFocus()

    # ------------------------------------------------------------- helpers
    def _fill(self):
        self.list.DeleteAllItems()
        for entry in self.state:
            key = entry["key"]
            index = self.list.InsertItem(self.list.GetItemCount(),
                                         colmod.column_header(key))
            self.list.SetItem(index, 1,
                              "yes" if entry.get("visible") else "no")
        if self.list.GetItemCount():
            self._select(0)

    def _select(self, index):
        count = self.list.GetItemCount()
        if not count:
            return
        index = max(0, min(index, count - 1))
        state = self.list.GetItemState(index, wx.LIST_STATE_SELECTED |
                                       wx.LIST_STATE_FOCUSED)
        if not (state & wx.LIST_STATE_SELECTED):
            self.list.SetItemState(index, wx.LIST_STATE_SELECTED |
                                   wx.LIST_STATE_FOCUSED,
                                   wx.LIST_STATE_SELECTED |
                                   wx.LIST_STATE_FOCUSED)
        self.list.EnsureVisible(index)
        if hasattr(self, "width_spin"):
            self._on_select(None)

    def _current(self):
        index = self.list.GetFirstSelected()
        if index < 0:
            return -1
        return index

    def _on_select(self, event):
        index = self._current()
        if 0 <= index < len(self.state) and hasattr(self, "width_spin"):
            self.width_spin.SetValue(int(self.state[index]["width"]))

    def _announce(self, text):
        frame = wx.GetTopLevelParent(self)
        if hasattr(frame, "announce"):
            frame.announce(text)
        else:
            self.list.SetName(text)

    def _on_toggle(self, event):
        index = self._current()
        if index < 0:
            return
        entry = self.state[index]
        entry["visible"] = not entry.get("visible")
        self.list.SetItem(index, 1, "yes" if entry["visible"] else "no")
        self._announce("%s %s" % (colmod.column_header(entry["key"]),
                                  "shown" if entry["visible"] else "hidden"))

    def _on_show(self, event):
        index = self._current()
        if index < 0:
            return
        self.state[index]["visible"] = True
        self.list.SetItem(index, 1, "yes")
        self._announce("%s shown" % colmod.column_header(self.state[index]["key"]))

    def _on_hide(self, event):
        index = self._current()
        if index < 0:
            return
        self.state[index]["visible"] = False
        self.list.SetItem(index, 1, "no")
        self._announce("%s hidden" % colmod.column_header(self.state[index]["key"]))

    def _move(self, offset):
        index = self._current()
        if index < 0:
            return
        target = index + offset
        if not 0 <= target < len(self.state):
            return
        self.state[index], self.state[target] = \
            self.state[target], self.state[index]
        self._fill()
        self._select(target)
        self._announce("%s moved to position %d"
                       % (colmod.column_header(self.state[target]["key"]),
                          target + 1))

    def _on_up(self, event):
        self._move(-1)

    def _on_down(self, event):
        self._move(1)

    def _on_width(self, event):
        index = self._current()
        if index < 0:
            return
        self.state[index]["width"] = int(self.width_spin.GetValue())
        self._announce("Width set to %d" % self.state[index]["width"])

    def _on_preset(self, event):
        choice = self.preset_choice.GetSelection()
        if choice == 0:
            classic = ["name", "size", "downloaded", "uploaded", "status",
                       "seeds", "peers", "downspeed", "upspeed", "eta", "ratio"]
            for entry in self.state:
                entry["visible"] = entry["key"] in classic
        elif choice == 1:
            for entry in self.state:
                entry["visible"] = True
        elif choice == 2:
            keep = ("name", "size", "status")
            for entry in self.state:
                entry["visible"] = entry["key"] in keep
        else:
            self.state = colmod.default_state()
        self._fill()
        self._announce("Preset applied")

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_SPACE,):
            self._on_toggle(None)
            return
        if code == wx.WXK_UP and event.ControlDown():
            self._on_up(None)
            return
        if code == wx.WXK_DOWN and event.ControlDown():
            self._on_down(None)
            return
        event.Skip()

    def _on_ok(self, event):
        self.result = self.state
        event.Skip()


class OptionsDialog(wx.Dialog):
    """Connection, bandwidth, queue, and notification preferences."""

    def __init__(self, parent, config):
        wx.Dialog.__init__(self, parent, title="ZTor Options",
                           size=(860, 720),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.config = config
        self.result = None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        # Sections down the left as a list with icons: easy to tap, and read
        # as an ordinary list by screen readers. Every page scrolls.
        book = wx.Listbook(panel, style=wx.LB_LEFT)
        book.SetName("Options sections")
        try:
            icon_px = int(self.FromDIP(32))
        except Exception:
            icon_px = 32
        self._page_icons = image_list([kind for _b, _t, kind in OPTION_PAGES],
                                      icon_px)
        if self._page_icons is not None:
            book.SetImageList(self._page_icons)
        self.pages = []
        for index, (builder, title, _kind) in enumerate(OPTION_PAGES):
            page = getattr(self, builder)(book)
            book.AddPage(page, title, imageId=index if self._page_icons
                         is not None else -1)
            self.pages.append(page)
        for page in self.pages:
            page.SetupScrolling(scroll_x=False, rate_y=20)
        try:
            book.GetListView().SetName("Options sections")
        except Exception:
            pass
        self.book = book
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        outer.Add(book, 1, wx.EXPAND | wx.ALL, 8)

        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        outer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(outer)

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        fix_check_boxes(self)

    def _on_char_hook(self, event):
        """Control Tab and Control Shift Tab move between sections, as in a
        tabbed dialog, and land on the section list so its name is read."""
        if event.GetKeyCode() == wx.WXK_TAB and event.ControlDown():
            self.book.AdvanceSelection(not event.ShiftDown())
            try:
                self.book.GetListView().SetFocus()
            except Exception:
                pass
            return
        event.Skip()

    # ---------------------------------------------------------------- pages
    def _page(self, parent):
        page = scrolled.ScrolledPanel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        page.SetSizer(sizer)
        return page, sizer

    def _row(self, page, sizer, label, control, tip=""):
        box = wx.BoxSizer(wx.HORIZONTAL)
        text = label_before(wx.StaticText(page, label=label), control)
        if tip:
            text.SetToolTip(tip)
            control.SetToolTip(tip)
        box.Add(text, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        box.Add(control, 1, wx.EXPAND)
        sizer.Add(box, 0, wx.EXPAND | wx.ALL, 8)
        return box

    def _build_general(self, book):
        page, sizer = self._page(book)
        self.save_path = wx.DirPickerCtrl(page, path=self.config.get("save_path", ""),
                                          style=wx.DIRP_USE_TEXTCTRL)
        self.save_path.SetName("Default download folder")
        self._row(page, sizer, "Default download folder:", self.save_path)

        self.start_paused = wx.CheckBox(page, label="Add torrents &paused")
        self.start_paused.SetValue(bool(self.config.get("start_paused")))
        sizer.Add(self.start_paused, 0, wx.ALL, 8)

        self.start_minimized = wx.CheckBox(
            page, label="Start &minimized (tray icon only)")
        self.start_minimized.SetValue(bool(self.config.get("start_minimized")))
        sizer.Add(self.start_minimized, 0, wx.ALL, 8)

        self.ask_save_path = wx.CheckBox(
            page, label="Ask for a download folder &every time")
        self.ask_save_path.SetValue(bool(self.config.get("ask_save_path")))
        sizer.Add(self.ask_save_path, 0, wx.ALL, 8)

        self.confirm_remove = wx.CheckBox(
            page, label="Confirm before &removing a torrent")
        self.confirm_remove.SetValue(bool(self.config.get("confirm_remove", True)))
        sizer.Add(self.confirm_remove, 0, wx.ALL, 8)

        self.confirm_remove_data = wx.CheckBox(
            page, label="Confirm before removing a torrent &with its data")
        self.confirm_remove_data.SetValue(
            bool(self.config.get("confirm_remove_data", True)))
        sizer.Add(self.confirm_remove_data, 0, wx.ALL, 8)

        self.monitor_folder = wx.DirPickerCtrl(
            page, path=self.config.get("monitor_folder", ""),
            style=wx.DIRP_USE_TEXTCTRL)
        self.monitor_folder.SetName("Watch folder for torrent files")
        self._row(page, sizer, "Watch folder for .torrent files:",
                  self.monitor_folder)

        self.incomplete_path = wx.DirPickerCtrl(
            page, path=self.config.get("incomplete_path", ""),
            style=wx.DIRP_USE_TEXTCTRL)
        self.incomplete_path.SetName(
            "Folder for unfinished downloads, blank to download in place")
        self._row(page, sizer, "Unfinished downloads folder, blank for none:",
                  self.incomplete_path,
                  "Downloads stay here until they finish, then move to the "
                  "download folder or the completed folder")

        self.move_completed = wx.DirPickerCtrl(
            page, path=self.config.get("move_completed_path", ""),
            style=wx.DIRP_USE_TEXTCTRL)
        self.move_completed.SetName(
            "Move finished downloads to this folder, blank to leave them")
        self._row(page, sizer, "Move finished downloads to, blank for none:",
                  self.move_completed)

        self.auto_clean = wx.CheckBox(
            page, label="Automatically c&lear cache and temporary files of "
                        "finished and removed torrents")
        self.auto_clean.SetValue(bool(self.config.get("auto_clean", True)))
        self.auto_clean.SetToolTip(
            "Removes leftover backups, resume and part files, temporary files "
            "and empty unfinished folders. Downloaded files are never removed.")
        sizer.Add(self.auto_clean, 0, wx.ALL, 8)

        self.clean_now_button = wx.Button(page, label="Clear &cache now")
        self.clean_now_button.Bind(wx.EVT_BUTTON, self._on_clean_now)
        sizer.Add(self.clean_now_button, 0, wx.ALL, 8)

        self.windows_integration = wx.CheckBox(
            page, label="&Integrate with Windows: open magnet links and "
                        ".torrent files with ZTor")
        self.windows_integration.SetValue(
            bool(self.config.get("windows_integration")))
        self.windows_integration.SetToolTip(
            "Makes ZTor the default app for magnet links and .torrent "
            "files for your Windows account. Turn off to remove it again.")
        sizer.Add(self.windows_integration, 0, wx.ALL, 8)

        self.default_apps_button = wx.Button(
            page, label="Open Windows &default apps settings")
        self.default_apps_button.SetToolTip(
            "If Windows still uses another app, choose ZTor there")
        self.default_apps_button.Bind(wx.EVT_BUTTON, self._on_default_apps)
        sizer.Add(self.default_apps_button, 0, wx.ALL, 8)
        return page

    def _on_clean_now(self, event):
        owner = self.GetParent()
        if hasattr(owner, "clean_now"):
            text = owner.clean_now(min_age=60)
        else:
            from . import cleanup, paths
            from .store import store
            text = cleanup.sweep(store(), paths.cache_dir(), min_age=60).text()
        wx.MessageBox(text + ".", "Clear cache", wx.OK | wx.ICON_INFORMATION,
                      self)

    def _on_default_apps(self, event):
        from . import shellassoc
        if not shellassoc.open_default_apps():
            wx.MessageBox("Windows Default apps settings could not be opened.",
                          "Preferences", wx.OK | wx.ICON_INFORMATION, self)

    def _build_connection(self, book):
        page, sizer = self._page(book)
        self.port = wx.SpinCtrl(page, min=0, max=65535,
                                initial=int(self.config.get("listen_port", 0) or 0))
        self.port.SetName("Listening port, zero means pick automatically")
        self._row(page, sizer, "Listening port, zero is automatic:", self.port,
                  "Zero picks a random high port once and keeps it")

        addresses = local_addresses()
        current_bind = self.config.get("bind_address", "") or ""
        choices = [""] + addresses
        if current_bind and current_bind not in choices:
            choices.append(current_bind)
        self.bind_address = wx.ComboBox(page, value=current_bind,
                                        choices=choices, style=wx.CB_DROPDOWN)
        self.bind_address.SetName(
            "Network address to use, blank for any. Pick your VPN address to "
            "stop all transfers when the VPN drops")
        self._row(page, sizer, "Use only this network address, blank for any:",
                  self.bind_address,
                  "Choose the VPN adapter's address so nothing is sent if the "
                  "VPN disconnects")

        self.ip_filter = wx.FilePickerCtrl(
            page, path=self.config.get("ip_filter_path", ""),
            wildcard="IP filter lists (*.dat;*.p2p;*.txt;*.gz)|"
                     "*.dat;*.p2p;*.txt;*.gz|All files (*.*)|*.*",
            style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN)
        self.ip_filter.SetName("IP filter file, eMule dat or p2p format, "
                               "blank for none")
        self._row(page, sizer, "IP filter file, blank for none:",
                  self.ip_filter)

        self.dht = wx.CheckBox(page, label="Enable &DHT")
        self.dht.SetValue(bool(self.config.get("enable_dht")))
        sizer.Add(self.dht, 0, wx.ALL, 6)
        self.lsd = wx.CheckBox(page, label="Enable &local peer discovery")
        self.lsd.SetValue(bool(self.config.get("enable_lsd")))
        sizer.Add(self.lsd, 0, wx.ALL, 6)
        self.upnp = wx.CheckBox(page, label="Enable &UPnP port mapping")
        self.upnp.SetValue(bool(self.config.get("enable_upnp")))
        sizer.Add(self.upnp, 0, wx.ALL, 6)
        self.natpmp = wx.CheckBox(page, label="Enable &NAT PMP port mapping")
        self.natpmp.SetValue(bool(self.config.get("enable_natpmp")))
        sizer.Add(self.natpmp, 0, wx.ALL, 6)

        self.encryption = wx.Choice(
            page, choices=["Prefer plaintext, allow encryption",
                           "Require encrypted peers",
                           "Allow plaintext only"])
        self.encryption.SetSelection(int(self.config.get("encryption_mode", 0) or 0))
        self._row(page, sizer, "Encryption:", self.encryption)

        self.max_conn = wx.SpinCtrl(page, min=0, max=10000,
                                    initial=int(self.config.get("max_connections", 0) or 0))
        self.max_conn.SetName("Maximum connections, zero uses the speed "
                              "profile")
        self._row(page, sizer, "Maximum connections, zero uses the speed "
                               "profile:", self.max_conn)

        self.auto_manage = wx.CheckBox(
            page, label="Let ZTor &manage which torrents run (queueing)")
        self.auto_manage.SetValue(bool(self.config.get("auto_manage", True)))
        sizer.Add(self.auto_manage, 0, wx.ALL, 8)

        note = wx.StaticText(
            page, label="The listening port is applied the next time "
                        "ZTor starts. Everything else takes effect "
                        "straight away.")
        note.Wrap(520)
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_speed(self, book):
        page, sizer = self._page(book)
        self.speed_profile = wx.Choice(page, choices=tuning.PROFILE_NAMES)
        self.speed_profile.SetSelection(tuning.profile_index(self.config))
        self._row(page, sizer, "Speed &profile:", self.speed_profile,
                  "Fast opens more connections and keeps more requests in "
                  "flight. Low resources suits slow routers.")
        self.announce_all = wx.CheckBox(
            page, label="Ask &every tracker for peers, not just the first one")
        self.announce_all.SetValue(bool(self.config.get("announce_all", True)))
        sizer.Add(self.announce_all, 0, wx.ALL, 8)
        self.prefer_tcp = wx.CheckBox(
            page, label="Prefer &TCP over uTP (try this if speeds are low)")
        self.prefer_tcp.SetValue(bool(self.config.get("prefer_tcp")))
        sizer.Add(self.prefer_tcp, 0, wx.ALL, 8)
        self.extra_enabled = wx.CheckBox(
            page, label="Add public &trackers to new torrents that are not "
                        "private")
        self.extra_enabled.SetValue(
            bool(self.config.get("extra_trackers_enabled", True)))
        sizer.Add(self.extra_enabled, 0, wx.ALL, 8)
        self.extra_url = wx.TextCtrl(
            page, value=self.config.get("extra_trackers_url", "") or "")
        self.extra_url.SetName("Address of a tracker list to download, "
                               "blank for none")
        self._row(page, sizer, "Tracker list address:", self.extra_url)
        sizer.Add(wx.StaticText(page, label="Your own extra &trackers, one "
                                            "per line:"), 0, wx.LEFT | wx.TOP, 8)
        self.extra_custom = wx.TextCtrl(
            page, value=self.config.get("extra_trackers", "") or "",
            style=wx.TE_MULTILINE, size=(-1, 90))
        self.extra_custom.SetName("Your own extra trackers, one per line")
        sizer.Add(self.extra_custom, 1, wx.EXPAND | wx.ALL, 8)
        return page

    def _build_scheduler(self, book):
        page, sizer = self._page(book)
        self.alt_dl = wx.SpinCtrl(page, min=0, max=1000000,
                                  initial=_kb(self.config.get("alt_dl_limit")))
        self.alt_dl.SetName("Alternative download limit in kilobytes per "
                            "second")
        self._row(page, sizer, "Alternative download limit, KB per second, "
                               "zero is unlimited:", self.alt_dl)
        self.alt_ul = wx.SpinCtrl(page, min=0, max=1000000,
                                  initial=_kb(self.config.get("alt_ul_limit")))
        self.alt_ul.SetName("Alternative upload limit in kilobytes per second")
        self._row(page, sizer, "Alternative upload limit, KB per second, "
                               "zero is unlimited:", self.alt_ul)
        self.schedule_enabled = wx.CheckBox(
            page, label="Use the alternative limits on a &schedule")
        self.schedule_enabled.SetValue(bool(self.config.get("schedule_enabled")))
        sizer.Add(self.schedule_enabled, 0, wx.ALL, 8)
        self.schedule_from = wx.TextCtrl(
            page, value=self.config.get("schedule_from", "08:00") or "")
        self.schedule_from.SetName("Schedule starts at, hours and minutes, "
                                   "for example 08:00")
        self._row(page, sizer, "From, for example 08:00:", self.schedule_from)
        self.schedule_to = wx.TextCtrl(
            page, value=self.config.get("schedule_to", "18:00") or "")
        self.schedule_to.SetName("Schedule ends at, hours and minutes, "
                                 "for example 18:00")
        self._row(page, sizer, "To, for example 18:00:", self.schedule_to)
        self.schedule_days = wx.Choice(page, choices=tuning.DAY_NAMES)
        self.schedule_days.SetSelection(
            max(0, min(2, int(self.config.get("schedule_days", 0) or 0))))
        self._row(page, sizer, "On:", self.schedule_days)
        note = wx.StaticText(
            page, label="Control Shift T turns the alternative limits on and "
                        "off at any time. A schedule that runs past midnight, "
                        "such as 22:00 to 06:00, works too.")
        note.Wrap(560)
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_seeding(self, book):
        page, sizer = self._page(book)
        self.seed_ratio = wx.SpinCtrlDouble(
            page, min=0.0, max=100.0, inc=0.1,
            initial=float(self.config.get("seed_ratio_limit", 0) or 0))
        self.seed_ratio.SetDigits(2)
        self.seed_ratio.SetName("Stop seeding at this share ratio, zero for "
                                "no limit")
        self._row(page, sizer, "Stop seeding at ratio, zero for no limit:",
                  self.seed_ratio)
        self.seed_minutes = wx.SpinCtrl(
            page, min=0, max=525600,
            initial=int(self.config.get("seed_time_limit_min", 0) or 0))
        self.seed_minutes.SetName("Stop seeding after this many minutes, "
                                  "zero for no limit")
        self._row(page, sizer, "Stop seeding after minutes, zero for no "
                               "limit:", self.seed_minutes)
        self.seed_action = wx.Choice(
            page, choices=["Pause the torrent",
                           "Remove the torrent, keep the data"])
        self.seed_action.SetSelection(
            1 if int(self.config.get("seed_goal_action", 0) or 0) == 1 else 0)
        self._row(page, sizer, "When the goal is reached:", self.seed_action)
        note = wx.StaticText(
            page, label="Starting a torrent again by hand after it reached "
                        "its goal keeps it seeding until you stop it.")
        note.Wrap(560)
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_rss(self, book):
        page, sizer = self._page(book)
        self.rss_interval = wx.SpinCtrl(
            page, min=5, max=1440,
            initial=int(self.config.get("rss_interval_min", 30) or 30))
        self.rss_interval.SetName("Check feeds every this many minutes")
        self._row(page, sizer, "Check feeds every, minutes:", self.rss_interval)
        self.rss_auto = wx.CheckBox(
            page, label="&Download new articles that match a rule")
        self.rss_auto.SetValue(bool(self.config.get("rss_auto_download", True)))
        sizer.Add(self.rss_auto, 0, wx.ALL, 8)
        note = wx.StaticText(
            page, label="Feeds and rules are managed from Options, RSS Feeds, "
                        "or Control R.")
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_display(self, book):
        page, sizer = self._page(book)
        self.appearance = wx.Choice(
            page, choices=["Follow Windows", "Light", "Dark"])
        self.appearance.SetSelection(
            max(0, min(2, int(self.config.get("appearance", 0) or 0))))
        self._row(page, sizer, "&Appearance:", self.appearance,
                  "Applied the next time ZTor starts")
        self.color_rows = wx.CheckBox(
            page, label="&Colour the torrent list by status")
        self.color_rows.SetValue(bool(self.config.get("color_rows", True)))
        sizer.Add(self.color_rows, 0, wx.ALL, 8)
        self.show_toolbar = wx.CheckBox(page, label="Show the &toolbar")
        self.show_toolbar.SetValue(bool(self.config.get("show_toolbar", True)))
        sizer.Add(self.show_toolbar, 0, wx.ALL, 8)
        self.toasts = wx.CheckBox(
            page, label="Show &pop up notices when no screen reader is running")
        self.toasts.SetValue(bool(self.config.get("toast_notifications", True)))
        sizer.Add(self.toasts, 0, wx.ALL, 8)
        self.touch_mode = wx.Choice(
            page, choices=["Automatic, on for touch screens", "On", "Off"])
        self.touch_mode.SetSelection(
            max(0, min(2, int(self.config.get("touch_mode", 0) or 0))))
        self._row(page, sizer, "Touch &mode:", self.touch_mode,
                  "Taller rows, bigger buttons and the action buttons. "
                  "Applied the next time ZTor starts")
        self.action_bar = wx.CheckBox(
            page, label="Show the action &buttons under the torrent list")
        self.action_bar.SetValue(bool(self.config.get("show_action_bar")))
        sizer.Add(self.action_bar, 0, wx.ALL, 8)
        self.animations = wx.CheckBox(
            page, label="Gentle a&nimations, off while a screen reader runs")
        self.animations.SetValue(bool(self.config.get("animations", True)))
        sizer.Add(self.animations, 0, wx.ALL, 8)
        note = wx.StaticText(
            page, label="Status colours are switched off automatically while "
                        "Windows high contrast is on.")
        note.Wrap(560)
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_remote(self, book):
        page, sizer = self._page(book)
        self.webui_enabled = wx.CheckBox(
            page, label="Turn on the &web remote control")
        self.webui_enabled.SetValue(bool(self.config.get("webui_enabled")))
        sizer.Add(self.webui_enabled, 0, wx.ALL, 8)
        self.webui_port = wx.SpinCtrl(
            page, min=1024, max=65535,
            initial=int(self.config.get("webui_port", 8686) or 8686))
        self.webui_port.SetName("Web remote port")
        self._row(page, sizer, "Port:", self.webui_port)
        self.webui_lan = wx.CheckBox(
            page, label="Allow &other devices on my network, such as my phone")
        self.webui_lan.SetValue(bool(self.config.get("webui_lan")))
        sizer.Add(self.webui_lan, 0, wx.ALL, 8)
        self.webui_password = wx.TextCtrl(page, style=wx.TE_PASSWORD)
        has = bool(self.config.get("webui_hash"))
        self.webui_password.SetName(
            "New password, leave blank to keep the current one" if has
            else "Password, required")
        self._row(page, sizer, "New password, blank keeps the current one:"
                  if has else "Password, required:", self.webui_password)

        self.remote_url = wx.TextCtrl(page, style=wx.TE_READONLY)
        self.remote_url.SetName("Remote address")
        self._row(page, sizer, "Remote address:", self.remote_url)
        copy_url = wx.Button(page, label="&Copy remote address")
        sizer.Add(copy_url, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        copy_url.Bind(wx.EVT_BUTTON, self._on_copy_remote)
        self.webui_port.Bind(wx.EVT_SPINCTRL, self._update_remote_url)
        self.webui_port.Bind(wx.EVT_TEXT, self._update_remote_url)
        self.webui_lan.Bind(wx.EVT_CHECKBOX, self._update_remote_url)
        self._update_remote_url()
        note = wx.StaticText(
            page, label="Options, Web Remote Address shows the addresses to "
                        "open, including the one for your phone, and copies "
                        "it. Windows may ask to allow ZTor through the "
                        "firewall.")
        note.Wrap(560)
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    # ---------------------------------------------------- remote address
    def _remote_url(self):
        """The address to open, from this computer's network IP and the port
        on the page. Phones need the network IP, so other devices must be
        allowed for it."""
        port = self.webui_port.GetValue()
        if self.webui_lan.GetValue():
            from .webui import lan_addresses
            found = lan_addresses()
            if found:
                return "http://%s:%d/" % (found[0], port)
            return ""
        return "http://127.0.0.1:%d/" % port

    def _update_remote_url(self, event=None):
        url = self._remote_url()
        if not url:
            self.remote_url.SetValue("No network address found. Is Wi-Fi "
                                     "or a cable connected?")
        elif self.webui_lan.GetValue():
            self.remote_url.SetValue(url)
        else:
            self.remote_url.SetValue("%s  (this computer only)" % url)
        if event is not None:
            event.Skip()

    def _on_copy_remote(self, event):
        url = self._remote_url()
        frame = wx.GetTopLevelParent(self.GetParent()) if self.GetParent() \
            else None
        if not url:
            message = "No network address found. Check the connection."
        elif not wx.TheClipboard.Open():
            message = "Could not open the clipboard"
        else:
            wx.TheClipboard.SetData(wx.TextDataObject(url))
            wx.TheClipboard.Close()
            if self.webui_lan.GetValue():
                message = "Copied %s. Open it on your phone." % url
            else:
                message = ("Copied %s. It works on this computer only; check "
                           "Allow other devices for a phone address." % url)
        if frame is not None and hasattr(frame, "announce"):
            frame.announce(message)
        else:
            wx.MessageBox(message, "Remote address", wx.OK, self)

    def _build_search(self, book):
        page, sizer = self._page(book)
        self.torznab_url = wx.TextCtrl(
            page, value=self.config.get("torznab_url", "") or "")
        self.torznab_url.SetName("Jackett or Prowlarr Torznab address, blank "
                                 "for none")
        self._row(page, sizer, "Jackett or Prowlarr Torznab address, blank "
                               "for none:", self.torznab_url)
        self.torznab_key = wx.TextCtrl(
            page, value=self.config.get("torznab_key", "") or "",
            style=wx.TE_PASSWORD)
        self.torznab_key.SetName("Jackett or Prowlarr API key")
        self._row(page, sizer, "API key:", self.torznab_key)
        note = wx.StaticText(
            page, label="In Jackett, copy the Torznab feed address of one "
                        "indexer or of all indexers. In Prowlarr, use the "
                        "indexer's Torznab address. The Internet Archive is "
                        "always available. Search opens with Control E.")
        note.Wrap(560)
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_bandwidth(self, book):
        page, sizer = self._page(book)
        self.dl_limit = wx.SpinCtrl(page, min=0, max=1000000,
                                    initial=_kb(self.config.get("download_rate_limit")))
        self.dl_limit.SetName("Global download limit in kilobytes per second")
        self._row(page, sizer, "Global download limit, KB per second, "
                               "zero is unlimited:", self.dl_limit)
        self.ul_limit = wx.SpinCtrl(page, min=0, max=1000000,
                                    initial=_kb(self.config.get("upload_rate_limit")))
        self.ul_limit.SetName("Global upload limit in kilobytes per second")
        self._row(page, sizer, "Global upload limit, KB per second, "
                               "zero is unlimited:", self.ul_limit)
        note = wx.StaticText(page, label="Per torrent limits are on the "
                                         "Torrent menu and the context menu.")
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    def _build_queue(self, book):
        page, sizer = self._page(book)
        self.active_downloads = wx.SpinCtrl(
            page, min=1, max=200,
            initial=int(self.config.get("active_downloads", 3)))
        self.active_downloads.SetName("Maximum active downloads")
        self._row(page, sizer, "Maximum active downloads:", self.active_downloads)
        self.active_seeds = wx.SpinCtrl(
            page, min=1, max=200, initial=int(self.config.get("active_seeds", 5)))
        self.active_seeds.SetName("Maximum active seeds")
        self._row(page, sizer, "Maximum active seeds:", self.active_seeds)
        self.queue_top = wx.CheckBox(page, label="Add new torrents at the &top "
                                                 "of the queue")
        self.queue_top.SetValue(bool(self.config.get("queue_new_top")))
        sizer.Add(self.queue_top, 0, wx.ALL, 8)
        return page

    def _build_proxy(self, book):
        page, sizer = self._page(book)
        self.proxy_type = wx.Choice(
            page, choices=["None", "SOCKS5", "SOCKS4", "HTTP", "HTTP with "
                                                            "authentication"])
        self.proxy_type.SetSelection(int(self.config.get("proxy_type", 0) or 0))
        self._row(page, sizer, "Proxy type:", self.proxy_type)
        self.proxy_host = wx.TextCtrl(page, value=self.config.get("proxy_host", ""))
        self.proxy_host.SetName("Proxy host")
        self._row(page, sizer, "Proxy host:", self.proxy_host)
        self.proxy_port = wx.SpinCtrl(page, min=0, max=65535,
                                      initial=int(self.config.get("proxy_port", 0) or 0))
        self.proxy_port.SetName("Proxy port")
        self._row(page, sizer, "Proxy port:", self.proxy_port)
        self.proxy_user = wx.TextCtrl(page,
                                      value=self.config.get("proxy_username", ""))
        self.proxy_user.SetName("Proxy user name")
        self._row(page, sizer, "Proxy user name:", self.proxy_user)
        self.proxy_pass = wx.TextCtrl(page,
                                      value=self.config.get("proxy_password", ""),
                                      style=wx.TE_PASSWORD)
        self.proxy_pass.SetName("Proxy password")
        self._row(page, sizer, "Proxy password:", self.proxy_pass)
        self.proxy_peers = wx.CheckBox(page, label="Use proxy for peer &connections")
        self.proxy_peers.SetValue(bool(self.config.get("proxy_peers", True)))
        sizer.Add(self.proxy_peers, 0, wx.ALL, 8)
        self.proxy_trackers = wx.CheckBox(page, label="Use proxy for &trackers")
        self.proxy_trackers.SetValue(bool(self.config.get("proxy_trackers", True)))
        sizer.Add(self.proxy_trackers, 0, wx.ALL, 8)
        return page

    def _build_access(self, book):
        page, sizer = self._page(book)
        self.notifications = wx.CheckBox(
            page, label="Speak notices for finished downloads and errors")
        self.notifications.SetValue(bool(self.config.get("show_notifications", True)))
        sizer.Add(self.notifications, 0, wx.ALL, 8)
        self.announce_actions = wx.CheckBox(
            page, label="Speak &confirmations for menu actions")
        self.announce_actions.SetValue(bool(self.config.get("announce_actions", True)))
        sizer.Add(self.announce_actions, 0, wx.ALL, 8)
        self.row_interval = wx.SpinCtrl(page, min=300, max=10000,
                                        initial=int(self.config.get("row_interval_ms", 900)))
        self.row_interval.SetName("Torrent list refresh interval in milliseconds")
        self._row(page, sizer, "List refresh interval, milliseconds:",
                  self.row_interval)
        note = wx.StaticText(
            page, label="Use the Columns dialog from the View menu to choose "
                        "which fields are read out for each torrent.")
        sizer.Add(note, 0, wx.ALL, 8)
        return page

    # ----------------------------------------------------------------- save
    def _on_ok(self, event):
        problem = self.validate()
        if problem:
            page, control, message = problem
            wx.MessageBox(message, "ZTor Options",
                          wx.OK | wx.ICON_INFORMATION, self)
            self.book.SetSelection(page)
            control.SetFocus()
            return
        self.result = self.collect()
        event.Skip()

    def validate(self):
        """(page, control, message) for the first bad field, or None."""
        if self.webui_enabled.GetValue():
            password = self.webui_password.GetValue()
            if not password and not self.config.get("webui_hash"):
                return (11, self.webui_password,
                        "Set a password before turning on the web remote.")
            if password and len(password) < 8:
                return (11, self.webui_password,
                        "Use at least 8 characters for the password.")
        address = self.torznab_url.GetValue().strip()
        if address and not address.lower().startswith(("http://", "https://")):
            return (12, self.torznab_url,
                    "The Jackett or Prowlarr address must start with http:// "
                    "or https://.")
        if self.schedule_enabled.GetValue():
            page = 4
            for control, name in ((self.schedule_from, "start"),
                                  (self.schedule_to, "end")):
                if tuning.parse_hhmm(control.GetValue()) is None:
                    return (page, control,
                            "The schedule %s time must look like 08:00." % name)
        return None

    def collect(self):
        values = {
            "save_path": self.save_path.GetPath(),
            "start_paused": self.start_paused.GetValue(),
            "start_minimized": self.start_minimized.GetValue(),
            "ask_save_path": self.ask_save_path.GetValue(),
            "confirm_remove": self.confirm_remove.GetValue(),
            "confirm_remove_data": self.confirm_remove_data.GetValue(),
            "monitor_folder": self.monitor_folder.GetPath(),
            "windows_integration": self.windows_integration.GetValue(),
            "auto_clean": self.auto_clean.GetValue(),
            "listen_port": self.port.GetValue(),
            "enable_dht": self.dht.GetValue(),
            "enable_lsd": self.lsd.GetValue(),
            "enable_upnp": self.upnp.GetValue(),
            "enable_natpmp": self.natpmp.GetValue(),
            "encryption_mode": self.encryption.GetSelection(),
            "max_connections": self.max_conn.GetValue(),
            "auto_manage": self.auto_manage.GetValue(),
            "download_rate_limit": self.dl_limit.GetValue() * 1000,
            "upload_rate_limit": self.ul_limit.GetValue() * 1000,
            "active_downloads": self.active_downloads.GetValue(),
            "active_seeds": self.active_seeds.GetValue(),
            "queue_new_top": self.queue_top.GetValue(),
            "proxy_type": self.proxy_type.GetSelection(),
            "proxy_host": self.proxy_host.GetValue(),
            "proxy_port": self.proxy_port.GetValue(),
            "proxy_username": self.proxy_user.GetValue(),
            "proxy_password": self.proxy_pass.GetValue(),
            "proxy_peers": self.proxy_peers.GetValue(),
            "proxy_trackers": self.proxy_trackers.GetValue(),
            "show_notifications": self.notifications.GetValue(),
            "announce_actions": self.announce_actions.GetValue(),
            "row_interval_ms": self.row_interval.GetValue(),
            "incomplete_path": self.incomplete_path.GetPath(),
            "move_completed_path": self.move_completed.GetPath(),
            "bind_address": self.bind_address.GetValue().strip(),
            "ip_filter_path": self.ip_filter.GetPath(),
            "speed_profile": self.speed_profile.GetSelection(),
            "announce_all": self.announce_all.GetValue(),
            "prefer_tcp": self.prefer_tcp.GetValue(),
            "extra_trackers_enabled": self.extra_enabled.GetValue(),
            "extra_trackers_url": self.extra_url.GetValue().strip(),
            "extra_trackers": self.extra_custom.GetValue(),
            "alt_dl_limit": self.alt_dl.GetValue() * 1000,
            "alt_ul_limit": self.alt_ul.GetValue() * 1000,
            "schedule_enabled": self.schedule_enabled.GetValue(),
            "schedule_from": self.schedule_from.GetValue().strip(),
            "schedule_to": self.schedule_to.GetValue().strip(),
            "schedule_days": self.schedule_days.GetSelection(),
            "seed_ratio_limit": round(float(self.seed_ratio.GetValue()), 2),
            "seed_time_limit_min": self.seed_minutes.GetValue(),
            "seed_goal_action": self.seed_action.GetSelection(),
            "rss_interval_min": self.rss_interval.GetValue(),
            "rss_auto_download": self.rss_auto.GetValue(),
            "appearance": self.appearance.GetSelection(),
            "color_rows": self.color_rows.GetValue(),
            "show_toolbar": self.show_toolbar.GetValue(),
            "toast_notifications": self.toasts.GetValue(),
            "touch_mode": self.touch_mode.GetSelection(),
            "show_action_bar": self.action_bar.GetValue(),
            "animations": self.animations.GetValue(),
            "webui_enabled": self.webui_enabled.GetValue(),
            "webui_port": self.webui_port.GetValue(),
            "webui_lan": self.webui_lan.GetValue(),
            "torznab_url": self.torznab_url.GetValue().strip(),
            "torznab_key": self.torznab_key.GetValue().strip(),
        }
        password = self.webui_password.GetValue()
        if password:
            from .webui import hash_password
            values["webui_salt"], values["webui_hash"] = hash_password(password)
        return values


class AddTorrentDialog(wx.Dialog):
    """Add a magnet link, a URL, or a local .torrent file."""

    MODES = ("Magnet link or URL", "Torrent file on disk")

    def __init__(self, parent, default_path):
        wx.Dialog.__init__(self, parent, title="Add Torrent", size=(620, 330))
        self.result = None
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.mode = wx.RadioBox(panel, label="Source",
                                choices=list(self.MODES),
                                majorDimension=1, style=wx.RA_SPECIFY_ROWS)
        self.mode.SetSelection(0)
        sizer.Add(self.mode, 0, wx.EXPAND | wx.ALL, 8)

        self.link = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 90))
        self.link.SetName("Magnet link or URL, one per line")
        sizer.Add(wx.StaticText(panel, label="&Magnet links or URLs, one per "
                                             "line:"), 0, wx.LEFT | wx.TOP, 8)
        sizer.Add(self.link, 1, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.file_path = wx.TextCtrl(panel)
        self.file_path.SetName("Path to torrent file")
        browse = wx.Button(panel, label="&Browse")
        row.Add(self.file_path, 1, wx.RIGHT, 6)
        row.Add(browse, 0)
        sizer.Add(wx.StaticText(panel, label="Torrent &file:"), 0,
                  wx.LEFT | wx.TOP, 8)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        path_row = wx.BoxSizer(wx.HORIZONTAL)
        self.save_path = wx.DirPickerCtrl(panel, path=default_path,
                                          style=wx.DIRP_USE_TEXTCTRL)
        self.save_path.SetName("Download folder")
        path_row.Add(self.save_path, 1)
        sizer.Add(wx.StaticText(panel, label="Save &to folder:"), 0,
                  wx.LEFT | wx.TOP, 8)
        sizer.Add(path_row, 0, wx.EXPAND | wx.ALL, 8)

        self.paused = wx.CheckBox(panel, label="Add &paused")
        sizer.Add(self.paused, 0, wx.LEFT | wx.BOTTOM, 8)

        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        sizer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)

        browse.Bind(wx.EVT_BUTTON, self._on_browse)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.mode.Bind(wx.EVT_RADIOBOX, self._on_mode)
        self._fill_from_clipboard()
        self._on_mode(None)
        fix_check_boxes(self)
        if self.mode.GetSelection() == 1:
            self.file_path.SetFocus()
            self.file_path.SelectAll()
        else:
            self.link.SetFocus()
            self.link.SelectAll()

    def _fill_from_clipboard(self, contents=None):
        """Put copied magnet links in the box, or a copied .torrent file.

        The text is selected, so typing or pasting replaces it, and the screen
        reader reads it when the box gets focus.
        """
        if contents is None:
            contents = clipboard_contents()
        text, files = contents
        links = links_from_text(text)
        if links:
            self.link.SetValue("\n".join(links))
            self.mode.SetSelection(0)
        elif files:
            self.file_path.SetValue(files[0])
            self.mode.SetSelection(1)

    def _on_mode(self, event):
        magnet = self.mode.GetSelection() == 0
        self.link.Enable(magnet)
        self.file_path.Enable(not magnet)

    def _on_browse(self, event):
        with wx.FileDialog(self, "Open torrent file",
                           wildcard="Torrent files (*.torrent)|*.torrent|"
                                    "All files (*.*)|*.*",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.file_path.SetValue(dialog.GetPath())
                self.mode.SetSelection(1)
                self._on_mode(None)

    def _on_ok(self, event):
        if self.mode.GetSelection() == 0:
            text = self.link.GetValue()
            links = [line.strip() for line in text.splitlines() if line.strip()]
            if not links:
                wx.MessageBox("Enter at least one magnet link or URL.",
                              "Add Torrent", wx.OK | wx.ICON_INFORMATION, self)
                return
            self.result = {"links": links,
                           "save_path": self.save_path.GetPath(),
                           "paused": self.paused.GetValue()}
        else:
            path = self.file_path.GetValue().strip()
            if not path or not os.path.isfile(path):
                wx.MessageBox("Choose an existing torrent file.",
                              "Add Torrent", wx.OK | wx.ICON_INFORMATION, self)
                return
            self.result = {"files": [path],
                           "save_path": self.save_path.GetPath(),
                           "paused": self.paused.GetValue()}
        event.Skip()


class SingleLineDialog(wx.Dialog):
    """A label, one text box, OK and Cancel."""

    def __init__(self, parent, title, label, value="", hint=""):
        wx.Dialog.__init__(self, parent, title=title, size=(520, 190))
        self.value = None
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label=label), 0, wx.ALL, 10)
        self.field = wx.TextCtrl(panel, value=value)
        self.field.SetName(label.rstrip(":"))
        sizer.Add(self.field, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        if hint:
            sizer.Add(wx.StaticText(panel, label=hint), 0, wx.ALL, 10)
        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        sizer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.field.SetFocus()
        self.field.SelectAll()

    def _on_ok(self, event):
        self.value = self.field.GetValue().strip()
        event.Skip()


_HASH_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64}|[A-Za-z2-7]{32})$")


def links_from_text(text):
    """Magnet links, .torrent URLs and bare info hashes found in some text.

    Used to fill the Add Torrent dialog from the clipboard. A bare info hash
    becomes a magnet link. Other web addresses are left out, since a copied
    page address is rarely a torrent. Order is kept, duplicates dropped.
    """
    found = []
    for word in (text or "").split():
        word = word.strip().strip("<>\"'()[]")
        lower = word.lower()
        link = None
        if lower.startswith("magnet:?"):
            link = word
        elif lower.startswith(("http://", "https://")) and \
                lower.split("?", 1)[0].split("#", 1)[0].endswith(".torrent"):
            link = word
        elif _HASH_RE.match(word):
            link = "magnet:?xt=urn:btih:" + word
        if link and link not in found:
            found.append(link)
    return found


def clipboard_contents():
    """(text, torrent file paths) on the clipboard; empty when it is busy."""
    text, files = "", []
    clipboard = wx.TheClipboard
    try:
        if not clipboard.Open():
            return text, files
    except Exception:
        return text, files
    try:
        if clipboard.IsSupported(wx.DataFormat(wx.DF_UNICODETEXT)) or \
                clipboard.IsSupported(wx.DataFormat(wx.DF_TEXT)):
            data = wx.TextDataObject()
            if clipboard.GetData(data):
                text = data.GetText()
        if clipboard.IsSupported(wx.DataFormat(wx.DF_FILENAME)):
            data = wx.FileDataObject()
            if clipboard.GetData(data):
                files = [f for f in data.GetFilenames()
                         if f.lower().endswith(".torrent") and os.path.isfile(f)]
    except Exception:
        pass
    finally:
        clipboard.Close()
    return text, files


def _level_index(value):
    """Map a libtorrent file priority, 0 to 7, onto the four dialog levels."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 2
    if value <= 0:
        return 0
    if value <= 2:
        return 1
    if value <= 5:
        return 2
    return 3


def _nearest_level(value):
    return FilesDialog.PRIORITY_VALUES[_level_index(value)]


class FilesDialog(wx.Dialog):
    """Choose which files inside a torrent to download."""

    PRIORITIES = ["Skip", "Low", "Normal", "High"]
    PRIORITY_VALUES = [0, 1, 4, 7]

    def __init__(self, parent, row):
        wx.Dialog.__init__(self, parent, title="Files in %s" % row.announce_name,
                           size=(760, 560))
        self.row = row
        # libtorrent uses 0 to 7; the dialog shows four levels but stores the
        # real values so they can be handed straight back to prioritize_files.
        given = list(getattr(row, "files_priorities", None) or [])
        if len(given) != len(row.files):
            given = [4] * len(row.files)
        self.priorities = [_nearest_level(value) for value in given]
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.InsertColumn(0, "File", width=460)
        self.list.InsertColumn(1, "Size", width=110)
        self.list.InsertColumn(2, "Priority", width=100)
        self.list.SetName("Files")
        self._fill()
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)

        row1 = wx.BoxSizer(wx.HORIZONTAL)
        row1.Add(wx.StaticText(panel, label="Priority for selected files:"), 0,
                 wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.priority = wx.Choice(panel, choices=self.PRIORITIES)
        self.priority.SetSelection(3)
        row1.Add(self.priority, 0, wx.RIGHT, 8)
        apply_button = wx.Button(panel, label="&Apply to selection")
        row1.Add(apply_button, 0)
        sizer.Add(row1, 0, wx.ALL, 8)

        row2 = wx.BoxSizer(wx.HORIZONTAL)
        all_normal = wx.Button(panel, label="Set &all to Normal")
        all_skip = wx.Button(panel, label="Set all to &Skip")
        row2.Add(all_normal, 0, wx.RIGHT, 8)
        row2.Add(all_skip, 0)
        sizer.Add(row2, 0, wx.ALL, 8)

        note = wx.StaticText(panel, label="Use Control and Shift with the arrow "
                                          "keys to select several rows.")
        sizer.Add(note, 0, wx.ALL, 8)

        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        sizer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)

        apply_button.Bind(wx.EVT_BUTTON, self._on_apply)
        all_normal.Bind(wx.EVT_BUTTON, lambda e: self._set_all(2))
        all_skip.Bind(wx.EVT_BUTTON, lambda e: self._set_all(0))
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.list.SetFocus()

    def _fill(self):
        self.list.DeleteAllItems()
        from . import format as fmt
        for index, entry in enumerate(self.row.files):
            row = self.list.InsertItem(index, entry["path"])
            self.list.SetItem(row, 1, fmt.human_size(entry["size"]))
            self.list.SetItem(row, 2, self.PRIORITIES[self._level_of(index)])

    def _level_of(self, index):
        return _level_index(self.priorities[index])

    def _on_select(self, event):
        index = self.list.GetFirstSelected()
        if 0 <= index < len(self.priorities):
            self.priority.SetSelection(self._level_of(index))

    def _on_apply(self, event):
        level = self.priority.GetSelection()
        value = self.PRIORITY_VALUES[level]
        count = 0
        index = self.list.GetFirstSelected()
        while index >= 0:
            self.priorities[index] = value
            self.list.SetItem(index, 2, self.PRIORITIES[level])
            count += 1
            index = self.list.GetNextSelected(index)
        frame = wx.GetTopLevelParent(self)
        if hasattr(frame, "announce"):
            frame.announce("%d files set to %s" % (count, self.PRIORITIES[level]))

    def _set_all(self, level):
        self.priorities = [self.PRIORITY_VALUES[level]] * len(self.row.files)
        self._fill()

    def selection_priorities(self):
        return self.priorities


class TrackersDialog(wx.Dialog):
    """Trackers and web seeds for one torrent."""

    def __init__(self, parent, row):
        wx.Dialog.__init__(self, parent, title="Trackers for %s" % row.announce_name,
                           size=(700, 520))
        self.row = row
        self.added = []
        self.urls = []
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.InsertColumn(0, "Tracker", width=520)
        self.list.InsertColumn(1, "Tier", width=80)
        self.list.SetName("Trackers")
        for index, (url, tier) in enumerate(row.trackers):
            item = self.list.InsertItem(index, url)
            self.list.SetItem(item, 1, str(tier))
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)

        add_row = wx.BoxSizer(wx.HORIZONTAL)
        self.new_url = wx.TextCtrl(panel)
        self.new_url.SetName("New tracker URL")
        add_button = wx.Button(panel, label="&Add tracker")
        add_row.Add(self.new_url, 1, wx.RIGHT, 6)
        add_row.Add(add_button, 0)
        sizer.Add(add_row, 0, wx.EXPAND | wx.ALL, 8)

        seed_row = wx.BoxSizer(wx.HORIZONTAL)
        self.new_seed = wx.TextCtrl(panel)
        self.new_seed.SetName("New web seed URL")
        seed_button = wx.Button(panel, label="Add &web seed")
        seed_row.Add(self.new_seed, 1, wx.RIGHT, 6)
        seed_row.Add(seed_button, 0)
        sizer.Add(seed_row, 0, wx.EXPAND | wx.ALL, 8)

        close = wx.Button(panel, label="&Reannounce now")
        sizer.Add(close, 0, wx.ALL, 8)

        okcancel = _std_buttons(panel, wx.CLOSE)
        sizer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)

        add_button.Bind(wx.EVT_BUTTON, self._on_add)
        seed_button.Bind(wx.EVT_BUTTON, self._on_seed)
        close.Bind(wx.EVT_BUTTON, self._on_reannounce)
        self.list.SetFocus()

    def _on_add(self, event):
        url = self.new_url.GetValue().strip()
        if not url:
            return
        self.added.append(url)
        item = self.list.InsertItem(self.list.GetItemCount(), url)
        self.list.SetItem(item, 1, "0")
        self.new_url.SetValue("")
        frame = wx.GetTopLevelParent(self)
        if hasattr(frame, "announce"):
            frame.announce("Tracker added")

    def _on_seed(self, event):
        url = self.new_seed.GetValue().strip()
        if not url:
            return
        self.urls.append(url)
        self.new_seed.SetValue("")
        frame = wx.GetTopLevelParent(self)
        if hasattr(frame, "announce"):
            frame.announce("Web seed added")

    def _on_reannounce(self, event):
        frame = wx.GetTopLevelParent(self)
        if hasattr(frame, "reannounce_selected"):
            frame.reannounce_selected()
        if hasattr(frame, "announce"):
            frame.announce("Reannouncing")


class SpeedLimitDialog(wx.Dialog):
    """Per torrent download and upload limits."""

    def __init__(self, parent, row):
        wx.Dialog.__init__(self, parent,
                           title="Speed limits for %s" % row.announce_name,
                           size=(460, 240))
        self.result = None
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        dl = int(row.download_limit or 0)
        ul = int(row.upload_limit or 0)
        self.dl = wx.SpinCtrl(panel, min=0, max=1000000,
                              initial=max(0, dl) // 1000)
        self.dl.SetName("Download limit in kilobytes per second")
        self.ul = wx.SpinCtrl(panel, min=0, max=1000000,
                              initial=max(0, ul) // 1000)
        self.ul.SetName("Upload limit in kilobytes per second")

        sizer.Add(label_before(wx.StaticText(
            panel, label="Download limit, KB per second, zero is unlimited:"),
            self.dl), 0, wx.ALL, 10)
        sizer.Add(self.dl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(label_before(wx.StaticText(
            panel, label="Upload limit, KB per second, zero is unlimited:"),
            self.ul), 0, wx.ALL, 10)
        sizer.Add(self.ul, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        unlimited = wx.Button(panel, label="Set both to &unlimited")
        sizer.Add(unlimited, 0, wx.ALL, 10)
        okcancel = _std_buttons(panel, wx.OK | wx.CANCEL)
        sizer.Add(okcancel, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)

        unlimited.Bind(wx.EVT_BUTTON, self._on_unlimited)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_unlimited(self, event):
        self.dl.SetValue(0)
        self.ul.SetValue(0)

    def _on_ok(self, event):
        self.result = (self.dl.GetValue() * 1000, self.ul.GetValue() * 1000)
        event.Skip()


class StatisticsDialog(wx.Dialog):
    """Session wide counters, refreshed on a timer while open."""

    def __init__(self, parent, provider):
        wx.Dialog.__init__(self, parent, title="ZTor Statistics",
                           size=(620, 560))
        self.provider = provider
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.grid = wx.ListCtrl(panel, style=wx.LC_REPORT)
        self.grid.InsertColumn(0, "Item", width=280)
        self.grid.InsertColumn(1, "Value", width=280)
        self.grid.SetName("Statistics")
        sizer.Add(self.grid, 1, wx.EXPAND | wx.ALL, 8)

        close = wx.Button(panel, label="&Close")
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)
        close.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self.timer)
        self.timer.Start(1500)
        self._refresh()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.grid.SetFocus()

    def _refresh(self):
        values = self.provider() or []
        self.grid.DeleteAllItems()
        for index, (label, value) in enumerate(values):
            item = self.grid.InsertItem(index, label)
            self.grid.SetItem(item, 1, str(value))

    def _on_timer(self, event):
        self._refresh()

    def _on_close(self, event):
        self.timer.Stop()
        event.Skip()


class AboutDialog(wx.Dialog):
    def __init__(self, parent, text):
        wx.Dialog.__init__(self, parent, title="About ZTor",
                           size=(560, 420))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        box = wx.TextCtrl(panel, value=text,
                          style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        box.SetName("About text")
        sizer.Add(box, 1, wx.EXPAND | wx.ALL, 10)
        ok = wx.Button(panel, label="OK")
        sizer.Add(ok, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)
        ok.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        box.SetFocus()
