"""Wires the engine to the window and starts the event loop.

The portable folders and the single instance check come first, before wx or
any window exists, so a second start never flashes a window.
"""

import sys
import time

from . import APP_NAME, __version__
from . import instance
from . import paths


import wx  # noqa: E402  after the portable set up in main

from .accessibility import announcer  # noqa: E402
from .engine_ext import Engine  # noqa: E402
from .main_window import MainFrame  # noqa: E402
from .store import store  # noqa: E402


class _PlaceholderEngine(object):
    """Lets the window be built before the real session exists."""

    def post(self, kind, **kwargs):
        return None

    def snapshot(self):
        return []

    def stop(self, timeout=6.0):
        return None

    def wait_for_rows(self, timeout=1.0):
        return []


def apply_appearance(app, mode):
    """0 follows Windows, 1 light, 2 dark. Must run before any window exists.

    wxWidgets 3.3 offers SetAppearance; older builds only have
    MSWEnableDarkMode. Anything missing is skipped quietly.
    """
    try:
        mode = int(mode or 0)
    except (TypeError, ValueError):
        mode = 0
    name = {0: "System", 1: "Light", 2: "Dark"}.get(mode, "System")
    enum = getattr(wx.App, "Appearance", None)
    value = getattr(enum, name, None) if enum is not None else None
    if value is None:
        value = getattr(wx.App, "Appearance_" + name, None)
    if value is not None and hasattr(app, "SetAppearance"):
        try:
            app.SetAppearance(value)
            return True
        except Exception:
            pass
    if mode != 1 and hasattr(app, "MSWEnableDarkMode"):
        flag = getattr(wx.App, "DarkMode_Always", None) if mode == 2 else None
        try:
            if flag is not None:
                app.MSWEnableDarkMode(flag)
            else:
                app.MSWEnableDarkMode()
            return True
        except Exception:
            pass
    return False


class TorrentApp(wx.App):
    def __init__(self, open_args=None):
        self.open_args = list(open_args or [])
        self.instance_server = None
        wx.App.__init__(self, False)

    def OnInit(self):
        self.SetAppName(APP_NAME)
        self.store_handle = store()
        apply_appearance(self, self.store_handle.settings.get("appearance", 0))
        self.frame = MainFrame(_PlaceholderEngine(), self.store_handle.settings)
        self.engine = Engine(
            self.store_handle.settings,
            on_snapshot=None,
            on_event=self.frame.on_engine_event,
            on_alert=self.frame.on_engine_alert,
        )
        self.frame.engine = self.engine
        self.engine.start()
        self.frame.Show()
        self.frame._restore_window()
        if self.store_handle.settings.get("start_minimized") and \
                self.frame.tray is not None:
            self.frame.Hide()
            self.frame.add_log("Started minimized to the tray")
        wx.CallLater(600, self._restore_torrents)
        if self.store_handle.settings.get("auto_clean", True):
            wx.CallLater(30000, self._startup_clean)
        if self.store_handle.settings.get("windows_integration"):
            wx.CallLater(1500, self._refresh_integration)
        announcer().load()
        frame = self.frame
        try:
            self.instance_server = instance.InstanceServer(
                lambda args: wx.CallAfter(frame.open_args, args))
        except OSError as exc:
            frame.add_log("Single instance listener failed: %s" % exc)
        if self.open_args:
            wx.CallLater(2500, frame.open_args, self.open_args, False)
        return True

    def _startup_clean(self):
        """Clear what earlier sessions left once the torrents are back."""
        frame = getattr(self, "frame", None)
        if frame is not None:
            frame.clean_now()

    def _refresh_integration(self):
        """The portable folder moved: point Windows at the new place."""
        try:
            from . import shellassoc
            if shellassoc.available() and shellassoc.needs_refresh():
                ok, message = shellassoc.register()
                self.frame.add_log("Windows integration updated for the new "
                                   "folder" if ok else message)
        except Exception as exc:
            self.frame.add_log("Windows integration check failed: %s" % exc)

    def _restore_torrents(self):
        bookmarks = list(self.store_handle.bookmarks)
        if not bookmarks:
            return
        self.frame.add_log("Restoring %d torrents" % len(bookmarks))
        self.engine.post("restore", entries=bookmarks)
        self.frame.announce("Restoring %d torrents" % len(bookmarks))

    def OnExit(self):
        if self.instance_server is not None:
            self.instance_server.close()
        if getattr(self, "engine", None) is not None:
            self.engine.stop()
        return 0


def _tell_user(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)
    except Exception:
        print(text)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print("%s %s" % (APP_NAME, __version__))
        return 0
    open_args = [a for a in argv[1:] if not a.startswith("--")]
    paths.prepare()
    lock = instance.acquire()
    if lock is None:
        # Already running: hand over the files and links, then leave.
        instance.allow_foreground()
        for _attempt in range(24):
            if instance.send_to_running(open_args):
                return 0
            time.sleep(0.25)
        _tell_user("ZTor is already running but did not answer. If no "
                   "ZTor window is open, end ZTor in Task Manager "
                   "and start it again.")
        return 1
    try:
        app = TorrentApp(open_args)
        app.MainLoop()
    finally:
        instance.release(lock)
    return 0
